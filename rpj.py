"""RPJ-Schattenhaushalt: Mittel des Rings Politischer Jugend je Projekt (ab 0.26).

Wunsch Lennert Gust 08.11.2026: „Einzelübersicht … da das Mittel von der Stadt sind, die wir in einem Gesamtmittel bekommen
… und dann in z. B. 10 Einzelprojekten enden. Da gibt es auch einen Haushalt, wo genau festgelegt ist, wie hoch das Ganze ist …
RPJ-Schattenhaushalt … beim Verbuchen dem Projekt zuweisen … RPJ-Stammdaten, wo man Projekte erstellen kann.“
Entscheidungen (Rückfrage 08.11.2026): Zuordnung ist Pflicht auf den RPJ-Posten; je Projekt Plan für Einnahmen und Ausgaben;
ein Bescheid je Jahr (Kontingent), die Zuwendung wird quartalsweise abgerufen (Mittelabruf).

Im Gesamthaushalt stehen die Mittel als Titel 1592/H18 (Mittelzufluss), 4380/H45 (Mittelverwendung) und H19 (Teilnahmebeiträge
RPJ). Der RPJ-Haushalt (Antrag) gliedert sie in Projekte mit den Kostenarten a–k des RPJ-Formulars (i = Teilnahmebeiträge,
Einnahme); „Mittel RPJ“ eines Projekts = Kosten (a–h, j, k) − Teilnahmebeiträge (i).

Nebenbuch ohne eigene Buchungen (wie die Mitgliederbuchhaltung): jede RPJ-Buchungszeile wird einem Projekt mit Kostenart oder
einem Mittelabruf zugeordnet (Tabelle rpj_links; Betrag = Haben − Soll der Zeile, also Kosten negativ, Zuflüsse positiv;
eine Zeile lässt sich aufteilen). Pflicht bei neuen Buchungen aus Kontoauszug und Kasse (core._book) und bei Eingangsrechnungen
(verbindlichkeiten.create; eine Ausbuchung übernimmt die Zuordnung der Position). Ältere Buchungen (z. B. Excel-Übernahme)
werden über „RPJ zuordnen“ nachgetragen. Stornierte Buchungen zählen nicht (kvbudget._excluded_journals).

Regeln je RPJ-Buchungszeile (kind_of):
  cost  – Konto 4380 bzw. Titel H45: Projekt + Kostenart (i = erstatteter Teilnahmebeitrag)
  fee   – Titel H19: Projekt, Kostenart i
  grant – Konto 1592 bzw. Titel H18: Mittelabruf (Zufluss bzw. Rückzahlung) oder Projekt + Kostenart (Teilnahmebeitrag i;
          in der Excel-Übernahme wurde 1592 netto geführt, dort auch Kosten)
Projekt bzw. Mittelabruf liegen im Haushaltsjahr der Buchung oder höchstens ein Jahr daneben (Rechnung im Januar für ein
Projekt im Dezember, Anzahlung im Vorjahr); die Abstimmung zeigt solche Buchungen gesondert. Die Eingänge eines Mittelabrufs
dürfen dessen Betrag nicht übersteigen; die Mittelabrufe eines Jahres nicht das Kontingent laut Bescheid.
"""
import csv
import io
import re
import unicodedata
from datetime import date, datetime

from formate import money, iso, euro, german_date

CATEGORIES = [('a', 'Raummiete'), ('b', 'An- und Abreisekosten'), ('c', 'Verpflegungskosten'), ('d', 'Referentenkosten'),
              ('e', 'Marketing / Bewerbungskosten'), ('f', 'Reisekosten'), ('g', 'Eintritte'), ('h', 'Sonstige Veranstaltungskosten'),
              ('i', 'Teilnahmebeiträge'), ('j', 'Technische Ausstattung'), ('k', 'Geschäftsführungskosten')]
LABELS = dict(CATEGORIES)
CODES = [c for c, _ in CATEGORIES]
FEE = 'i'                                       # Teilnahmebeiträge: Einnahme
COSTS = [c for c in CODES if c != FEE]
COST_ACCOUNT, GRANT_ACCOUNT = '4380', '1592'
COST_TITLE, GRANT_TITLE, FEE_TITLE = 'H45', 'H18', 'H19'
SOURCES = ('bank', 'resolve', 'bill', 'bill-writeoff')     # Buchungen, die zugeordnet werden (Umbuchungen u. Ä. nicht)
STATUS = {'planned': 'geplant', 'done': 'durchgeführt', 'cancelled': 'abgesagt'}
KINDS = {'cost': 'Mittelverwendung', 'fee': 'Teilnahmebeiträge', 'grant': 'Mittelzufluss'}
MAX_LINKS = 50


def init(db):
    db.executescript(f'''
    CREATE TABLE IF NOT EXISTS rpj_years(year INTEGER PRIMARY KEY,contingent INTEGER CHECK(contingent IS NULL OR contingent>=0),
        notice_on TEXT,reference TEXT,applied_on TEXT,note TEXT);
    CREATE TABLE IF NOT EXISTS rpj_projects(id INTEGER PRIMARY KEY,year INTEGER NOT NULL,no INTEGER NOT NULL CHECK(no>0),kind TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,quarter INTEGER CHECK(quarter IS NULL OR quarter BETWEEN 1 AND 4),held_on TEXT,participants TEXT,
        attended INTEGER CHECK(attended IS NULL OR attended>=0),contact TEXT,
        status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','done','cancelled')),note TEXT,UNIQUE(year,no));
    CREATE TABLE IF NOT EXISTS rpj_plan(project_id INTEGER NOT NULL REFERENCES rpj_projects(id),
        category TEXT NOT NULL CHECK(category IN ({','.join(repr(c) for c in CODES)})),amount INTEGER NOT NULL CHECK(amount>=0),
        PRIMARY KEY(project_id,category));
    CREATE TABLE IF NOT EXISTS rpj_requests(id INTEGER PRIMARY KEY,year INTEGER NOT NULL,quarter INTEGER NOT NULL CHECK(quarter BETWEEN 1 AND 4),
        amount INTEGER NOT NULL CHECK(amount>0),requested_on TEXT NOT NULL,note TEXT,created TEXT NOT NULL,actor TEXT);
    CREATE TABLE IF NOT EXISTS rpj_links(id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL REFERENCES entries(id),
        project_id INTEGER REFERENCES rpj_projects(id),category TEXT CHECK(category IS NULL OR category IN ({','.join(repr(c) for c in CODES)})),
        request_id INTEGER REFERENCES rpj_requests(id),amount INTEGER NOT NULL CHECK(amount!=0),created TEXT NOT NULL,actor TEXT,
        CHECK((project_id IS NOT NULL AND category IS NOT NULL AND request_id IS NULL) OR (request_id IS NOT NULL AND project_id IS NULL AND category IS NULL)));
    CREATE INDEX IF NOT EXISTS rpj_links_entry ON rpj_links(entry_id);
    ''')
    db.commit()


def kind_of(account, title):
    """RPJ-Art einer Buchungszeile: 'cost', 'fee', 'grant' oder None (kein RPJ-Posten)."""
    if account == COST_ACCOUNT or title == COST_TITLE:
        return 'cost'
    if title == FEE_TITLE:
        return 'fee'
    if account == GRANT_ACCOUNT or title == GRANT_TITLE:
        return 'grant'
    return None


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _skip(store):
    import kvbudget
    return kvbudget._excluded_journals(store)


def _text(value, limit):
    return ' '.join(str(value or '').split())[:limit]


def _year(value):
    try:
        y = int(value)
    except (TypeError, ValueError):
        raise ValueError('Bitte ein Jahr angeben.') from None
    if not 2000 <= y <= 2100:
        raise ValueError('Ungültiges Jahr.')
    return y


def _quarter(value, required=False):
    """Quartal 1–4 aus „Q2“, „Q2 2026“, „Q2/2026“, „2“, „2. Quartal“; ein Datum wird nicht als Quartal gelesen."""
    s = ' '.join(str(value or '').split()).upper()
    m = re.fullmatch(r'Q\s*([1-4])(?:\s*[-/ ]\s*\d{2}(?:\d{2})?)?|([1-4])(?:\s*\.?\s*QUARTAL)?(?:\s*[-/ ]\s*\d{2}(?:\d{2})?)?', s)
    if m:
        return int(m.group(1) or m.group(2))
    if s in ('', '-', '–', '—', 'GANZJÄHRIG', 'JAHR'):
        if required:
            raise ValueError('Bitte das Quartal (1 bis 4) angeben.')
        return None
    raise ValueError(f'Quartal „{value}“ nicht erkannt (1 bis 4 bzw. Q1 bis Q4).')


def _date(value, label):
    s = str(value or '').strip()
    if not s:
        return None
    try:
        return iso(s)
    except (ValueError, KeyError, TypeError):
        raise ValueError(f'{label}: Datum nicht erkannt.') from None


def request_label(r):
    return f"Mittelabruf Q{r['quarter']}/{r['year']}"


def project_label(p):
    return f"{p['year']} · Nr. {p['no']} {p['name']}"


# ---------------------------------------------------------------- Stammdaten: Jahr (Bescheid), Projekte, Mittelabrufe

def year_info(store, year):
    row = store.db.execute('SELECT * FROM rpj_years WHERE year=?', (int(year),)).fetchone()
    return dict(row) if row else None


def save_year(store, data, actor):
    """Antrag und Bescheid eines Jahres: Kontingent (bewilligte Summe), Datum und Aktenzeichen des Bescheids, Antragsdatum."""
    year = _year(data.get('year'))
    raw = str(data.get('contingent') or '').strip()
    contingent = money(raw) if raw else None
    if contingent is not None and contingent < 0:
        raise ValueError('Das Kontingent darf nicht negativ sein.')
    values = {'contingent': contingent, 'notice_on': _date(data.get('notice_on'), 'Bescheid vom'),
              'reference': _text(data.get('reference'), 80) or None, 'applied_on': _date(data.get('applied_on'), 'Antrag vom'),
              'note': _text(data.get('note'), 500) or None}
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        requested = store.db.execute('SELECT COALESCE(SUM(amount),0) FROM rpj_requests WHERE year=?', (year,)).fetchone()[0]
        if requested and (contingent is None or contingent < requested):
            raise ValueError(f'Für {year} sind schon {euro(requested)} abgerufen – das Kontingent kann nicht kleiner sein.')
        before = year_info(store, year)
        store.db.execute('INSERT INTO rpj_years(year,contingent,notice_on,reference,applied_on,note) VALUES(?,?,?,?,?,?) '
                         'ON CONFLICT(year) DO UPDATE SET contingent=excluded.contingent,notice_on=excluded.notice_on,'
                         'reference=excluded.reference,applied_on=excluded.applied_on,note=excluded.note',
                         (year, *values.values()))
        store.audit(actor, 'RPJ-Bescheid gespeichert' if values['contingent'] is not None else 'RPJ-Jahr gespeichert',
                    {'jahr': year, **values, **({'vorher': {k: before[k] for k in values}} if before else {})})


def _plan(data):
    if data is not None and not isinstance(data, dict):
        raise ValueError('Plan: bitte Beträge je Kostenart angeben.')
    plan = {}
    for c in CODES:
        raw = str((data or {}).get(c) or '').strip()
        amount = money(raw) if raw else 0
        if amount < 0:
            raise ValueError(f'Plan {c}) {LABELS[c]}: Betrag darf nicht negativ sein.')
        if amount:
            plan[c] = amount
    return plan


def _project_values(data):
    try:
        no = int(str(data.get('no') or '').strip())
    except ValueError:
        raise ValueError('Bitte die laufende Nummer des Projekts angeben (wie im RPJ-Antrag).') from None
    if not 1 <= no <= 999:
        raise ValueError('Laufende Nummer: 1 bis 999.')
    name = _text(data.get('name'), 200)
    if not name:
        raise ValueError('Bitte den Namen der Veranstaltung angeben.')
    attended = str(data.get('attended') or '').strip()
    if attended and not attended.isdigit():
        raise ValueError('Tatsächliche Teilnehmer: bitte eine Zahl angeben.')
    status = data.get('status') or 'planned'
    if status not in STATUS:
        raise ValueError('Unbekannter Status.')
    return {'year': _year(data.get('year')), 'no': no, 'kind': _text(data.get('kind'), 60), 'name': name,
            'quarter': _quarter(data.get('quarter')), 'held_on': _date(data.get('held_on'), 'Durchgeführt am'),
            'participants': _text(data.get('participants'), 120) or None, 'attended': int(attended) if attended else None,
            'contact': _text(data.get('contact'), 120) or None, 'status': status, 'note': _text(data.get('note'), 500) or None}


FIELDS = ('year', 'no', 'kind', 'name', 'quarter', 'held_on', 'participants', 'attended', 'contact', 'status', 'note')


def _write_project(store, pid, v, plan):
    """Projekt anlegen bzw. ändern und seinen Plan ersetzen – innerhalb einer offenen Transaktion. Liefert die ID."""
    other = store.db.execute('SELECT id FROM rpj_projects WHERE year=? AND no=?', (v['year'], v['no'])).fetchone()
    if other and other['id'] != pid:
        raise ValueError(f"Projekt Nr. {v['no']} gibt es für {v['year']} schon.")
    if pid:
        old = store.db.execute('SELECT * FROM rpj_projects WHERE id=?', (pid,)).fetchone()
        if not old:
            raise ValueError('Projekt nicht gefunden.')
        if old['year'] != v['year']:
            far = [r for r in _links_of(store, project_id=pid) if abs(r['fy'] - v['year']) > 1]
            if far:
                raise ValueError('Das Projekt hat Buchungen aus einem anderen Haushaltsjahr – Jahr nicht änderbar (erst die Zuordnungen ändern).')
        store.db.execute(f'UPDATE rpj_projects SET {",".join(k + "=?" for k in FIELDS)} WHERE id=?', [v[k] for k in FIELDS] + [pid])
    else:
        pid = store.db.execute(f'INSERT INTO rpj_projects({",".join(FIELDS)}) VALUES({",".join("?" * len(FIELDS))})', [v[k] for k in FIELDS]).lastrowid
    store.db.execute('DELETE FROM rpj_plan WHERE project_id=?', (pid,))
    store.db.executemany('INSERT INTO rpj_plan(project_id,category,amount) VALUES(?,?,?)', [(pid, c, a) for c, a in plan.items()])
    return pid


def save_project(store, data, actor):
    """Projekt des RPJ-Haushalts mit Plan je Kostenart a–k anlegen oder ändern."""
    v = _project_values(data)
    plan = _plan(data.get('plan'))
    pid = int(data['id']) if data.get('id') else None
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        pid = _write_project(store, pid, v, plan)
        store.audit(actor, 'RPJ-Projekt geändert' if data.get('id') else 'RPJ-Projekt angelegt',
                    {'id': pid, 'jahr': v['year'], 'nr': v['no'], 'plan': plan})
    return pid


def delete_project(store, pid, actor):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        p = store.db.execute('SELECT * FROM rpj_projects WHERE id=?', (int(pid),)).fetchone()
        if not p:
            raise ValueError('Projekt nicht gefunden.')
        if _links_of(store, project_id=p['id']):
            raise ValueError('Dem Projekt sind Buchungen zugeordnet – nicht löschbar. Stattdessen den Status „abgesagt“ wählen oder die Zuordnungen ändern.')
        store.db.execute('DELETE FROM rpj_links WHERE project_id=?', (p['id'],))   # nur noch Zuordnungen stornierter Buchungen
        store.db.execute('DELETE FROM rpj_plan WHERE project_id=?', (p['id'],))
        store.db.execute('DELETE FROM rpj_projects WHERE id=?', (p['id'],))
        store.audit(actor, 'RPJ-Projekt gelöscht', {'id': p['id'], 'jahr': p['year'], 'nr': p['no'], 'name': p['name']})


def save_request(store, data, actor):
    """Mittelabruf (Quartal, Betrag, Datum) anlegen oder ändern – höchstens bis zum Kontingent laut Bescheid."""
    year = _year(data.get('year'))
    quarter = _quarter(data.get('quarter'), required=True)
    amount = money(data.get('amount', ''))
    if amount <= 0:
        raise ValueError('Bitte den abgerufenen Betrag angeben.')
    requested_on = _date(data.get('requested_on'), 'Abgerufen am') or date.today().isoformat()
    note = _text(data.get('note'), 300) or None
    rid = int(data['id']) if data.get('id') else None
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        info = year_info(store, year)
        if not info or info['contingent'] is None:
            raise ValueError(f'Zuerst den Bescheid für {year} eintragen (Kontingent), dann die Mittel abrufen.')
        others = store.db.execute('SELECT COALESCE(SUM(amount),0) FROM rpj_requests WHERE year=? AND id!=?', (year, rid or 0)).fetchone()[0]
        if others + amount > info['contingent']:
            raise ValueError(f"Mittelabrufe {year} zusammen {euro(others + amount)} – mehr als das Kontingent laut Bescheid ({euro(info['contingent'])}).")
        if rid:
            old = store.db.execute('SELECT * FROM rpj_requests WHERE id=?', (rid,)).fetchone()
            if not old:
                raise ValueError('Mittelabruf nicht gefunden.')
            got = _received(store).get(rid, 0)
            if amount < got:
                raise ValueError(f'Auf diesen Mittelabruf sind schon {euro(got)} eingegangen – der Betrag kann nicht kleiner sein.')
            if old['year'] != year and _links_of(store, request_id=rid):
                raise ValueError('Dem Mittelabruf sind Zahlungseingänge zugeordnet – das Jahr ist nicht änderbar.')
            store.db.execute('UPDATE rpj_requests SET year=?,quarter=?,amount=?,requested_on=?,note=? WHERE id=?', (year, quarter, amount, requested_on, note, rid))
        else:
            rid = store.db.execute('INSERT INTO rpj_requests(year,quarter,amount,requested_on,note,created,actor) VALUES(?,?,?,?,?,?,?)',
                                   (year, quarter, amount, requested_on, note, _now(), actor)).lastrowid
        store.audit(actor, 'RPJ-Mittelabruf gespeichert', {'id': rid, 'jahr': year, 'quartal': quarter, 'cents': amount, 'am': requested_on})
    return rid


def delete_request(store, rid, actor):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        r = store.db.execute('SELECT * FROM rpj_requests WHERE id=?', (int(rid),)).fetchone()
        if not r:
            raise ValueError('Mittelabruf nicht gefunden.')
        if _links_of(store, request_id=r['id']):
            raise ValueError('Dem Mittelabruf sind Zahlungseingänge zugeordnet – nicht löschbar (erst die Zuordnung ändern).')
        store.db.execute('DELETE FROM rpj_links WHERE request_id=?', (r['id'],))   # nur noch Zuordnungen stornierter Buchungen
        store.db.execute('DELETE FROM rpj_requests WHERE id=?', (r['id'],))
        store.audit(actor, 'RPJ-Mittelabruf gelöscht', {'id': r['id'], 'jahr': r['year'], 'quartal': r['quarter'], 'cents': r['amount']})


# ---------------------------------------------------------------- Zuordnung von Buchungszeilen

def _links_of(store, project_id=None, request_id=None, skip=None):
    """Gültige Zuordnungen (ohne stornierte Buchungen) eines Projekts bzw. Mittelabrufs mit Haushaltsjahr der Buchung."""
    skip = _skip(store) if skip is None else skip
    where, arg = ('l.project_id=?', project_id) if project_id is not None else ('l.request_id=?', request_id)
    return [r for r in store.rows('SELECT l.*,e.journal_id,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) fy FROM rpj_links l '
                                  f'JOIN entries e ON e.id=l.entry_id JOIN journals j ON j.id=e.journal_id WHERE {where}', (arg,))
            if r['journal_id'] not in skip]


def _flows(store, skip=None):
    """Je Mittelabruf (ohne Stornos): {'in': Eingänge, 'back': Rückzahlungen (positiv)}."""
    skip = _skip(store) if skip is None else skip
    out = {}
    for r in store.rows('SELECT l.request_id,l.amount,e.journal_id FROM rpj_links l JOIN entries e ON e.id=l.entry_id WHERE l.request_id IS NOT NULL'):
        if r['journal_id'] not in skip:
            f = out.setdefault(r['request_id'], {'in': 0, 'back': 0})
            f['in' if r['amount'] > 0 else 'back'] += abs(r['amount'])
    return out


def _received(store, skip=None):
    """Eingänge je Mittelabruf (ohne Rückzahlungen und Stornos)."""
    return {rid: f['in'] for rid, f in _flows(store, skip).items()}


def parse_target(value):
    """„p:12“ (Projekt) bzw. „r:3“ (Mittelabruf) → ('p', 12) / ('r', 3); leer → None."""
    if isinstance(value, dict):
        if value.get('project_id'):
            return 'p', int(value['project_id'])
        if value.get('request_id'):
            return 'r', int(value['request_id'])
        value = value.get('target')
    m = re.fullmatch(r'\s*([pr]):(\d+)\s*', str(value or ''))
    return (m.group(1), int(m.group(2))) if m else None


MISSING = {'cost': 'bitte das RPJ-Projekt und die Kostenart wählen',
           'fee': 'bitte das RPJ-Projekt wählen',
           'grant': 'bitte den Mittelabruf (bzw. bei einem Teilnahmebeitrag das RPJ-Projekt) wählen'}


def target(store, kind, value, category, fy, label='RPJ-Mittel'):
    """Prüft eine Zuordnung für eine Buchungszeile der Art kind im Haushaltsjahr fy.
    Liefert {'project_id', 'category'} oder {'request_id'}."""
    parsed = parse_target(value)
    if not parsed:
        raise ValueError(f'{label}: {MISSING[kind]}.')
    what, tid = parsed
    if what == 'r':
        if kind != 'grant':
            raise ValueError(f'{label}: Ein Mittelabruf gehört nur zum Mittelzufluss (1592).')
        r = store.db.execute('SELECT * FROM rpj_requests WHERE id=?', (tid,)).fetchone()
        if not r:
            raise ValueError('Mittelabruf nicht gefunden.')
        if abs(r['year'] - fy) > 1:
            raise ValueError(f'{label}: {request_label(r)} passt nicht zum Haushaltsjahr {fy} der Buchung.')
        return {'request_id': r['id']}
    p = store.db.execute('SELECT * FROM rpj_projects WHERE id=?', (tid,)).fetchone()
    if not p:
        raise ValueError('RPJ-Projekt nicht gefunden.')
    if abs(p['year'] - fy) > 1:
        raise ValueError(f"{label}: Projekt {p['no']} aus {p['year']} passt nicht zum Haushaltsjahr {fy} der Buchung "
                         '(höchstens ein Jahr Abstand).')
    category = str(category or '').strip().lower() or (FEE if kind in ('fee', 'grant') else '')
    if kind == 'fee':
        category = FEE
    if category not in CODES:
        raise ValueError(f'{label}: {MISSING["cost"]}.')
    return {'project_id': p['id'], 'category': category}


def check_part(store, account, title, rpj, fy, label=None):
    """Teilbetrag beim Verbuchen (Kontoauszug, Kasse, Eingangsrechnung): RPJ-Zuordnung prüfen. None = kein RPJ-Posten."""
    kind = kind_of(account, title)
    if not kind:
        return None
    rpj = rpj or {}
    return target(store, kind, rpj.get('target', rpj) if isinstance(rpj, dict) else rpj, rpj.get('category') if isinstance(rpj, dict) else None,
                  fy, label or f'RPJ-Mittel ({account})')


def _check_requests(store, ids):
    if not ids:
        return
    flows = _flows(store)
    for r in store.rows(f"SELECT * FROM rpj_requests WHERE id IN ({','.join('?' * len(ids))})", list(ids)):
        f = flows.get(r['id'], {'in': 0, 'back': 0})
        if f['in'] > r['amount']:
            raise ValueError(f"{request_label(r)}: Eingänge {euro(f['in'])} übersteigen den abgerufenen Betrag {euro(r['amount'])} – "
                             'Betrag des Mittelabrufs prüfen oder einem anderen Abruf zuordnen.')
        if f['back'] > f['in']:
            raise ValueError(f"{request_label(r)}: Rückzahlungen {euro(f['back'])} sind höher als die Eingänge {euro(f['in'])} – "
                             'einem anderen Mittelabruf zuordnen.')


def attach(store, entries, actor):
    """Nach dem Buchen (entry_id gesetzt): Zuordnungen der Zeilen mit 'rpj' festhalten – innerhalb der Transaktion."""
    now, requests = _now(), set()
    for e in entries:
        link = e.get('rpj')
        if not link:
            continue
        store.db.execute('INSERT INTO rpj_links(entry_id,project_id,category,request_id,amount,created,actor) VALUES(?,?,?,?,?,?,?)',
                         (e['entry_id'], link.get('project_id'), link.get('category'), link.get('request_id'), e['credit'] - e['debit'], now, actor))
        if link.get('request_id'):
            requests.add(link['request_id'])
    _check_requests(store, requests)


def inherit(store, entry, source_entry_ids, actor):
    """Ausbuchung eines Restbetrags (Eingangsrechnung): Zuordnung der größten zugeordneten Position übernehmen."""
    if not kind_of(entry['account'], entry.get('title')):
        return
    rows = [r for i in source_entry_ids for r in store.rows('SELECT * FROM rpj_links WHERE entry_id=? AND project_id IS NOT NULL', (i,))]
    if not rows:
        return
    best = max(rows, key=lambda r: abs(r['amount']))
    store.db.execute('INSERT INTO rpj_links(entry_id,project_id,category,request_id,amount,created,actor) VALUES(?,?,?,?,?,?,?)',
                     (entry['entry_id'], best['project_id'], best['category'], None, entry['credit'] - entry['debit'], _now(), actor))


def _origin(source, banks, bills):
    """Herkunft einer Buchung für die Anzeige: Kontoauszug, Kasse (Belegnummer) oder Eingangsrechnung."""
    prefix, _, rest = str(source).partition(':')
    if prefix in ('bank', 'resolve'):
        b = banks.get(int(rest.split('/')[0])) if rest.split('/')[0].isdigit() else None
        if b and b['ledger'] == 'cash':
            return {'origin': 'Kasse', 'ref': b['bank_ref'] or '', 'name': b['name'] or '', 'bank_id': b['id'], 'ledger': 'cash'}
        return {'origin': 'Kontoauszug', 'ref': '', 'name': (b['name'] if b else '') or '', 'bank_id': b['id'] if b else None, 'ledger': 'bank'}
    bill = bills.get(int(rest)) if rest.isdigit() else None
    return {'origin': 'Eingangsrechnung' if prefix == 'bill' else 'Ausbuchung Eingangsrechnung', 'ref': bill['number'] if bill else '',
            'name': bill['creditor'] if bill else '', 'bill_id': bill['id'] if bill else None}


def entries(store, year=None, skip=None):
    """RPJ-Buchungszeilen (Kontoauszug, Kasse, Eingangsrechnungen, ohne Stornos) mit ihren Zuordnungen; year = Haushaltsjahr."""
    skip = _skip(store) if skip is None else skip
    sql = ('SELECT e.id,e.journal_id,e.account,e.kv,e.debit,e.credit,e.title,j.day,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) fy,'
           'j.source,j.description FROM entries e JOIN journals j ON j.id=e.journal_id '
           "WHERE (e.account IN (?,?) OR e.title IN (?,?,?))" + (' AND COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER))=?' if year else '')
           + ' ORDER BY j.day,e.id')
    args = (COST_ACCOUNT, GRANT_ACCOUNT, COST_TITLE, GRANT_TITLE, FEE_TITLE) + ((int(year),) if year else ())
    rows = [r for r in store.rows(sql, args) if r['journal_id'] not in skip and str(r['source']).split(':')[0] in SOURCES
            and kind_of(r['account'], r['title']) and r['credit'] != r['debit']]
    if not rows:
        return []
    links = {}
    for l in store.rows('SELECT l.*,p.year p_year,p.no p_no,p.name p_name,q.year r_year,q.quarter r_quarter FROM rpj_links l '
                        'LEFT JOIN rpj_projects p ON p.id=l.project_id LEFT JOIN rpj_requests q ON q.id=l.request_id ORDER BY l.id'):
        links.setdefault(l['entry_id'], []).append(l)
    bank_ids = {int(m.group(1)) for r in rows for m in [re.match(r'^(?:bank|resolve):(\d+)', r['source'])] if m}
    bill_ids = {int(m.group(1)) for r in rows for m in [re.match(r'^bill(?:-writeoff)?:(\d+)$', r['source'])] if m}
    banks, bills = {}, {}
    for ids, table, out, cols in ((sorted(bank_ids), 'bank', banks, 'id,name,bank_ref,ledger,reference'), (sorted(bill_ids), 'bills', bills, 'id,number,creditor')):
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            out.update({x['id']: x for x in store.rows(f"SELECT {cols} FROM {table} WHERE id IN ({','.join('?' * len(chunk))})", chunk)})
    result = []
    for r in rows:
        amount = r['credit'] - r['debit']
        mine = links.get(r['id'], [])
        result.append({'id': r['id'], 'journal_id': r['journal_id'], 'day': r['day'], 'fy': r['fy'], 'account': r['account'], 'title': r['title'],
                       'kv': r['kv'], 'kind': kind_of(r['account'], r['title']), 'amount': amount, 'text': r['description'] or '',
                       'source': r['source'], **_origin(r['source'], banks, bills),
                       'links': [{'project_id': l['project_id'], 'category': l['category'], 'request_id': l['request_id'], 'amount': l['amount'],
                                  'label': (f"Nr. {l['p_no']} {l['p_name']} · {l['category']}) {LABELS[l['category']]}" + (f" ({l['p_year']})" if l['p_year'] != r['fy'] else '')
                                            if l['project_id'] else f"Mittelabruf Q{l['r_quarter']}/{l['r_year']}")} for l in mine],
                       'open': amount - sum(l['amount'] for l in mine)})
    return result


def open_entries(store, year=None, skip=None):
    return [x for x in entries(store, year, skip) if x['open']]


def _entry(store, entry_id, skip):
    """RPJ-Buchungszeile mit Haushaltsjahr und Art – sonst ValueError."""
    e = store.db.execute('SELECT e.*,j.source,j.day,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) fy FROM entries e '
                         'JOIN journals j ON j.id=e.journal_id WHERE e.id=?', (int(entry_id),)).fetchone()
    kind = e and kind_of(e['account'], e['title'])
    if not e or not kind or str(e['source']).split(':')[0] not in SOURCES:
        raise ValueError('Nur Buchungszeilen auf den RPJ-Posten (4380, 1592, Titel H45/H18/H19) aus Kontoauszug, Kasse oder Eingangsrechnungen lassen sich zuordnen.')
    if e['journal_id'] in skip:
        raise ValueError('Diese Buchung ist storniert.')
    return e, kind


def _replace_links(store, e, kind, items, actor):
    """Zuordnung einer Zeile ersetzen (innerhalb einer offenen Transaktion). Liefert (neue Zuordnungen, vorher, betroffene Abrufe)."""
    total = e['credit'] - e['debit']
    links, requests = [], set()
    for n, item in enumerate(items, 1):
        label = f'Teilbetrag {n}' if len(items) > 1 else 'RPJ-Zuordnung'
        raw = item.get('amount')
        cents = total if len(items) == 1 and (raw is None or str(raw).strip() == '') else money(raw)
        if not cents or (cents > 0) != (total > 0):
            raise ValueError(f'{label}: Betrag ungleich 0 und mit derselben Richtung wie die Buchung.')
        link = target(store, kind, item.get('target'), item.get('category'), e['fy'], label)
        links.append({**link, 'amount': cents})
        if link.get('request_id'):
            requests.add(link['request_id'])
    if sum(x['amount'] for x in links) != total:
        raise ValueError(f'Die Teilbeträge ergeben nicht den Betrag der Buchung ({euro(abs(total))}).')
    before = store.rows('SELECT project_id,category,request_id,amount FROM rpj_links WHERE entry_id=?', (e['id'],))
    requests |= {b['request_id'] for b in before if b['request_id']}
    store.db.execute('DELETE FROM rpj_links WHERE entry_id=?', (e['id'],))
    now = _now()
    store.db.executemany('INSERT INTO rpj_links(entry_id,project_id,category,request_id,amount,created,actor) VALUES(?,?,?,?,?,?,?)',
                         [(e['id'], x.get('project_id'), x.get('category'), x.get('request_id'), x['amount'], now, actor) for x in links])
    return links, before, requests


def assign(store, entry_id, items, actor):
    """RPJ-Buchungszeile zuordnen (auch nachträglich, z. B. Excel-Übernahme) bzw. Zuordnung ändern; ersetzt die bisherige.
    items: [{target: 'p:<id>'|'r:<id>', category, amount}] – amount darf bei nur einer Zeile fehlen (ganzer Betrag)."""
    items = list(items or []) if isinstance(items, (list, tuple)) else None
    if not items or not all(isinstance(i, dict) for i in items):
        raise ValueError('Bitte mindestens eine Zuordnung angeben.')
    if len(items) > MAX_LINKS:
        raise ValueError(f'Höchstens {MAX_LINKS} Teilbeträge je Buchungszeile.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        e, kind = _entry(store, entry_id, _skip(store))
        links, before, requests = _replace_links(store, e, kind, items, actor)
        _check_requests(store, requests)
        store.audit(actor, 'RPJ-Zuordnung geändert' if before else 'RPJ-Buchung zugeordnet',
                    {'entry': e['id'], 'zuordnung': links, **({'vorher': before} if before else {})})


MAX_BULK = 500


def assign_many(store, entry_ids, value, category, actor):
    """Mehrere noch nicht zugeordnete RPJ-Buchungszeilen mit dem ganzen Betrag demselben Projekt (mit Kostenart) bzw. Mittelabruf
    zuordnen – z. B. alle Teilnahmebeiträge einer Reise. Alles oder nichts; bereits zugeordnete Zeilen werden nicht verändert."""
    if not isinstance(entry_ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in entry_ids):
        raise ValueError('Ungültige Auswahl.')
    ids = sorted(set(entry_ids))
    if not ids:
        raise ValueError('Bitte mindestens eine Buchung auswählen.')
    if len(ids) > MAX_BULK:
        raise ValueError(f'Höchstens {MAX_BULK} Buchungen auf einmal.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        skip, requests, done = _skip(store), set(), []
        for i in ids:
            e, kind = _entry(store, i, skip)
            if store.db.execute('SELECT 1 FROM rpj_links WHERE entry_id=?', (e['id'],)).fetchone():
                raise ValueError(f"Die Buchung vom {german_date(e['day'])} ist schon zugeordnet – einzeln über „Ändern“ bearbeiten.")
            links, _, touched = _replace_links(store, e, kind, [{'target': value, 'category': category}], actor)
            requests |= touched
            done.append({'entry': e['id'], 'cents': links[0]['amount']})
        _check_requests(store, requests)
        store.audit(actor, 'RPJ-Buchungen gemeinsam zugeordnet', {'ziel': str(value), 'kostenart': category or None, 'anzahl': len(done), 'zeilen': done})
    return len(done)


# ---------------------------------------------------------------- Auswertung

def _cost(link):
    """Kosten (positiv) einer Zuordnung zu einer Kostenart a–h, j, k; 0 für Teilnahmebeiträge."""
    return -link['amount'] if link['category'] != FEE else 0


def _sums(plan, ist):
    cost_plan = sum(v for c, v in plan.items() if c != FEE)
    cost_ist = sum(v for c, v in ist.items() if c != FEE)
    return {'cost_plan': cost_plan, 'fee_plan': plan.get(FEE, 0), 'rpj_plan': cost_plan - plan.get(FEE, 0),
            'cost_ist': cost_ist, 'fee_ist': ist.get(FEE, 0), 'rpj_ist': cost_ist - ist.get(FEE, 0)}


def _project_rows(store, where, args, skip):
    """Projekte mit Plan und Ist je Kostenart (Ist: Kosten positiv, Teilnahmebeiträge positiv)."""
    projects = store.rows(f'SELECT * FROM rpj_projects WHERE {where} ORDER BY year,no', args)
    if not projects:
        return []
    ids = [p['id'] for p in projects]
    marks = ','.join('?' * len(ids))
    plans = {}
    for r in store.rows(f'SELECT * FROM rpj_plan WHERE project_id IN ({marks})', ids):
        plans.setdefault(r['project_id'], {})[r['category']] = r['amount']
    ist, other, count = {}, {}, {}
    for l in store.rows(f'SELECT l.*,e.journal_id,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) fy,p.year p_year FROM rpj_links l '
                        f'JOIN entries e ON e.id=l.entry_id JOIN journals j ON j.id=e.journal_id JOIN rpj_projects p ON p.id=l.project_id '
                        f'WHERE l.project_id IN ({marks})', ids):
        if l['journal_id'] in skip:
            continue
        value = -l['amount'] if l['category'] != FEE else l['amount']
        bucket = ist.setdefault(l['project_id'], {})
        bucket[l['category']] = bucket.get(l['category'], 0) + value
        count[l['project_id']] = count.get(l['project_id'], 0) + 1
        if l['fy'] != l['p_year']:
            other[l['project_id']] = other.get(l['project_id'], 0) + 1     # Buchungen aus einem anderen Haushaltsjahr
    out = []
    for p in projects:
        plan, actual = plans.get(p['id'], {}), ist.get(p['id'], {})
        out.append({**p, 'status_label': STATUS[p['status']], 'plan': plan, 'ist': actual, **_sums(plan, actual),
                    'other_years': other.get(p['id'], 0), 'bookings': count.get(p['id'], 0)})
    return out


def year_view(store, year):
    """RPJ-Jahresübersicht: Bescheid, Mittelabrufe, Projekte mit Plan/Ist, Kostenarten, Abstimmung mit dem Haushalt."""
    year = _year(year)
    skip = _skip(store)
    info = year_info(store, year)
    projects = _project_rows(store, 'year=?', (year,), skip)
    flows = _flows(store, skip)
    flow = lambda rid: flows.get(rid, {'in': 0, 'back': 0})   # noqa: E731
    requests = [{**r, 'label': request_label(r), 'received': flow(r['id'])['in'], 'refunded': flow(r['id'])['back'],
                 'open': r['amount'] - flow(r['id'])['in']}
                for r in store.rows('SELECT * FROM rpj_requests WHERE year=? ORDER BY quarter,requested_on,id', (year,))]
    categories = [{'code': c, 'label': l, 'income': c == FEE, 'plan': sum(p['plan'].get(c, 0) for p in projects),
                   'ist': sum(p['ist'].get(c, 0) for p in projects)} for c, l in CATEGORIES]
    plan_quarter = {q: sum(p['rpj_plan'] for p in projects if p['quarter'] == q and p['status'] != 'cancelled') for q in (1, 2, 3, 4)}
    plan_quarter[None] = sum(p['rpj_plan'] for p in projects if p['quarter'] is None and p['status'] != 'cancelled')
    applied = sum(p['rpj_plan'] for p in projects)
    used = sum(p['rpj_ist'] for p in projects)
    requested = sum(r['amount'] for r in requests)
    received = sum(r['received'] for r in requests)
    refunded = sum(r['refunded'] for r in requests)
    contingent = info['contingent'] if info else None
    # Abstimmung: RPJ-Buchungen im Haushaltsjahr – zugeordnet zu Projekten/Abrufen dieses Jahres, anderer Jahre, offen
    owner = {('p', r['id']): r['year'] for r in store.rows('SELECT id,year FROM rpj_projects')}
    owner.update({('r', r['id']): r['year'] for r in store.rows('SELECT id,year FROM rpj_requests')})
    of = lambda l: owner.get(('p', l['project_id']) if l['project_id'] else ('r', l['request_id']))
    rows = entries(store, year, skip)
    recon = []
    for kind, label in KINDS.items():
        mine = [x for x in rows if x['kind'] == kind]
        sign = -1 if kind == 'cost' else 1
        same = sum(l['amount'] for x in mine for l in x['links'] if of(l) == year)
        recon.append({'kind': kind, 'label': label, 'count': len(mine), 'household': sign * sum(x['amount'] for x in mine),
                      'linked': sign * same, 'other_years': sign * (sum(l['amount'] for x in mine for l in x['links']) - same),
                      'open': sign * sum(x['open'] for x in mine), 'open_count': sum(1 for x in mine if x['open'])})
    return {'year': year, 'info': info, 'projects': projects, 'requests': requests, 'categories': categories,
            'plan_quarter': [{'quarter': q, 'amount': plan_quarter[q]} for q in (1, 2, 3, 4, None) if plan_quarter[q] or q],
            'cards': {'contingent': contingent, 'applied': applied, 'requested': requested, 'available': None if contingent is None else contingent - requested,
                      'received': received, 'refunded': refunded, 'outstanding': requested - received, 'used': used,
                      'balance': received - refunded - used,
                      'cost': sum(p['cost_ist'] for p in projects), 'fees': sum(p['fee_ist'] for p in projects)},
            'reconciliation': recon, 'open': sum(r['open_count'] for r in recon),
            'issues': [f"{r['label']}: Rückzahlungen {euro(r['refunded'])} höher als die Eingänge {euro(r['received'])} (Eingang storniert?)"
                       for r in requests if r['refunded'] > r['received']]}


def project_view(store, pid):
    """Einzelübersicht eines Projekts: Plan und Ist je Kostenart, alle zugeordneten Buchungen."""
    skip = _skip(store)
    rows = _project_rows(store, 'id=?', (int(pid),), skip)
    if not rows:
        raise ValueError('RPJ-Projekt nicht gefunden.')
    p = rows[0]
    categories = [{'code': c, 'label': l, 'income': c == FEE, 'plan': p['plan'].get(c, 0), 'ist': p['ist'].get(c, 0),
                   'diff': p['ist'].get(c, 0) - p['plan'].get(c, 0)} for c, l in CATEGORIES]
    bookings = []
    for x in entries(store, None, skip):
        for l in x['links']:
            if l['project_id'] == p['id']:
                bookings.append({'entry_id': x['id'], 'day': x['day'], 'fy': x['fy'], 'text': x['text'], 'name': x['name'], 'origin': x['origin'],
                                 'ref': x['ref'], 'account': x['account'], 'category': l['category'], 'label': LABELS[l['category']],
                                 'cost': -l['amount'] if l['category'] != FEE else 0, 'income': l['amount'] if l['category'] == FEE else 0,
                                 'split': len(x['links']) > 1, 'bank_id': x.get('bank_id'), 'ledger': x.get('ledger')})
    return {'project': p, 'categories': categories, 'bookings': bookings, 'info': year_info(store, p['year'])}


def summary(store, year):
    """Für die Oberfläche (/api/data): Projekte und Mittelabrufe (Auswahl beim Verbuchen) und offene Zuordnungen."""
    skip = _skip(store)
    got = _received(store, skip)
    open_rows = open_entries(store, None, skip)
    return {'projects': store.rows('SELECT id,year,no,kind,name,quarter,status FROM rpj_projects ORDER BY year DESC,no'),
            'requests': [{**r, 'received': got.get(r['id'], 0)} for r in store.rows('SELECT id,year,quarter,amount,requested_on FROM rpj_requests ORDER BY year DESC,quarter,id')],
            'open': len(open_rows), 'open_year': sum(1 for x in open_rows if x['fy'] == int(year)),
            'years': [r[0] for r in store.db.execute('SELECT year FROM rpj_years UNION SELECT year FROM rpj_projects ORDER BY 1')]}


# ---------------------------------------------------------------- CSV

def _eur(cents):
    return '' if cents is None else f'{cents / 100:.2f}'.replace('.', ',')


def year_csv(store, year):
    """Projekte × Kostenarten mit Plan, Ist und Abweichung (eine Zeile je Projekt und Kostenart mit Werten)."""
    from berichte import _csv
    v = year_view(store, year)
    rows = []
    for p in v['projects']:
        for c, label in CATEGORIES:
            plan, ist = p['plan'].get(c, 0), p['ist'].get(c, 0)
            if plan or ist:
                rows.append([p['year'], p['no'], p['kind'], p['name'], f"Q{p['quarter']}" if p['quarter'] else '', p['status_label'],
                             f'{c}) {label}', 'Einnahme' if c == FEE else 'Ausgabe', _eur(plan), _eur(ist), _eur(ist - plan)])
    return _csv(rows, ['Jahr', 'Nr.', 'Maßnahme', 'Veranstaltung', 'Quartal', 'Status', 'Kostenart', 'Art', 'Plan', 'Ist', 'Abweichung'])


def bookings_csv(store, year=None, project_id=None):
    """Zugeordnete RPJ-Buchungen (eines Jahres bzw. eines Projekts) – Betrag wie gebucht: Ausgaben negativ. Jahr = RPJ-Jahr des Projekts bzw.
    Mittelabrufs (kann vom Haushaltsjahr der Buchung abweichen); nicht zugeordnete Buchungen nach Haushaltsjahr."""
    from berichte import _csv
    skip = _skip(store)
    year_of = {p['id']: p for p in store.rows('SELECT id,year,no,name FROM rpj_projects')}
    req_of = {r['id']: r for r in store.rows('SELECT * FROM rpj_requests')}
    rows = []
    for x in entries(store, None, skip):
        for l in x['links'] or [{'project_id': None, 'request_id': None, 'category': None, 'amount': x['open']}]:
            p, r = year_of.get(l['project_id']), req_of.get(l['request_id'])
            owner_year = p['year'] if p else r['year'] if r else x['fy']
            if project_id is not None and l['project_id'] != project_id:
                continue
            if year is not None and owner_year != int(year):
                continue
            rows.append([x['day'], x['fy'], x['origin'], x['ref'], x['text'], x['name'], x['account'], x['title'], owner_year if (p or r) else '',
                         f"{p['no']}" if p else '', p['name'] if p else (request_label(r) if r else 'nicht zugeordnet'),
                         f"{l['category']}) {LABELS[l['category']]}" if l['category'] else '', _eur(l['amount'])])
        if x['links'] and x['open'] and project_id is None and (year is None or x['fy'] == int(year)):
            rows.append([x['day'], x['fy'], x['origin'], x['ref'], x['text'], x['name'], x['account'], x['title'], '', '', 'nicht zugeordnet', '', _eur(x['open'])])
    return _csv(rows, ['Datum', 'Haushaltsjahr', 'Herkunft', 'Beleg', 'Text', 'Name', 'Konto', 'Titel', 'RPJ-Jahr', 'Projekt-Nr.', 'Projekt bzw. Mittelabruf',
                       'Kostenart', 'Betrag'])


# ---------------------------------------------------------------- Import des RPJ-Haushalts (CSV/Excel)

def _fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').casefold().replace('ß', 'ss'))
    return ''.join(c for c in text if c.isalnum() and not unicodedata.combining(c))


HEADERS = {'year': ('jahr', 'haushaltsjahr'), 'no': ('nr', 'lfdnr', 'lfd', 'nummer', 'laufendenummer', 'projektnr'),
           'kind': ('massnahme', 'art', 'format'), 'name': ('veranstaltung', 'namederveranstaltung', 'name', 'projekt', 'titel'),
           'quarter': ('quartal',), 'held': ('datum', 'termin', 'zeitraum'), 'participants': ('teilnehmer', 'teilnehmerzahl', 'geplanteteilnehmer', 'geplanteanzahlteilnehmer'),
           'contact': ('ansprechpartner', 'kontakt'), 'applied_on': ('antragvom', 'antrag', 'antragsdatum')}
MAX_ROWS = 200


def _header(cells):
    found = {}
    for i, cell in enumerate(cells):
        raw = str(cell or '').strip()
        key = _fold(raw)
        m = re.match(r'^([a-k])\s*\)', raw.lower()) or re.fullmatch(r'([a-k])', raw.lower())
        if m and m.group(1) not in found:
            found[m.group(1)] = i
            continue
        for c, label in CATEGORIES:
            if key == _fold(label) and c not in found:
                found[c] = i
                break
        else:
            for field, names in HEADERS.items():
                if key in names and field not in found:
                    found[field] = i
                    break
    return found if {'year', 'no', 'name'} <= set(found) and any(c in found for c in CODES) else None


def _table(raw):
    if raw[:2] == b'PK':
        import openpyxl
        try:
            book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:
            raise ValueError('Excel-Datei (.xlsx) konnte nicht gelesen werden.') from None
        sheets = [[list(r) for r in s.iter_rows(values_only=True, max_row=MAX_ROWS + 20)] for s in book.worksheets]
        if _never_calculated(raw):   # z. B. von einem Programm erzeugt und nie in Excel gespeichert: Formeln stehen ohne Wert da
            try:
                raw_book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=False)
                for rows, s in zip(sheets, raw_book.worksheets):
                    for row, formulas in zip(rows, s.iter_rows(values_only=True, max_row=MAX_ROWS + 20)):
                        for i, f in enumerate(formulas):
                            if i < len(row) and row[i] is None and isinstance(f, str) and f.startswith('='):
                                row[i] = FORMULA
            except Exception:
                pass
    else:
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252')
        sample = '\n'.join(text.splitlines()[:10])
        delimiter = max((';', '\t', ','), key=sample.count)
        try:
            sheets = [list(csv.reader(io.StringIO(text, newline=''), delimiter=delimiter))]
        except csv.Error:
            raise ValueError('Die CSV-Datei ist nicht lesbar (Trennzeichen, Anführungszeichen oder Zeilenenden prüfen).') from None
    for rows in sheets:
        for index, row in enumerate(rows[:15]):
            mapping = _header(row)
            if mapping:
                body = [(index + 2 + n, r) for n, r in enumerate(rows[index + 1:]) if any(str(c or '').strip() for c in r)]
                if len(body) > MAX_ROWS:
                    raise ValueError(f'Höchstens {MAX_ROWS} Projekte je Datei.')
                return mapping, body
    raise ValueError('Kopfzeile nicht erkannt. Benötigt: Jahr, Nr., Veranstaltung und die Kostenarten a) bis k) '
                     '(Vorlage: Seite RPJ-Mittel → Haushalt importieren).')


FORMULA = object()   # Formelzelle ohne gespeicherten Wert


def _never_calculated(raw):
    """True, wenn die Arbeitsmappe Formeln enthält, aber keine davon ein gespeichertes Ergebnis hat. Excel und LibreOffice speichern
    auch ein leeres Textergebnis (=WENN(…;"";…)) mit t="str" – das gilt als berechnet."""
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            found = computed = False
            for name in z.namelist():
                if not re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name):
                    continue
                for attrs, value in re.findall(rb'<c\b([^>]*)>\s*<f\b[^>]*(?:/>|>.*?</f>)\s*(?:<v>(.*?)</v>)?', z.read(name), re.S):
                    found = True
                    if value or re.search(rb'\bt="(?:str|e|b|s)"', attrs):
                        computed = True
                        break
                if computed:
                    break
            return found and not computed
    except (zipfile.BadZipFile, OSError):
        return False


def _cell(row, mapping, field):
    i = mapping.get(field)
    if i is None or i >= len(row) or row[i] is None:
        return ''
    v = row[i]
    if v is FORMULA:
        raise ValueError('Formel ohne gespeicherten Wert – die Datei in Excel öffnen, speichern und erneut einlesen.')
    if isinstance(v, (datetime, date)):
        return v.strftime('%d.%m.%Y')
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f'{v:.2f}'.replace('.', ',')
    return str(v).strip()


def preview_import(store, raw, filename):
    """RPJ-Haushalt (Projekte mit Plan a–k) lesen und mit dem Bestand vergleichen – schreibt nichts."""
    mapping, body = _table(raw)
    rows, seen = [], set()
    existing = {(p['year'], p['no']): p for p in store.rows('SELECT * FROM rpj_projects')}
    plans = {}
    for r in store.rows('SELECT * FROM rpj_plan'):
        plans.setdefault(r['project_id'], {})[r['category']] = r['amount']
    for row_no, cells in body:
        item = {'row_no': row_no, 'error': None, 'action': 'new', 'changes': []}
        try:
            data = {f: _cell(cells, mapping, f) for f in ('year', 'no', 'kind', 'name', 'quarter', 'participants', 'contact')}
            held = _cell(cells, mapping, 'held')
            if held and not data['quarter']:   # Quartal aus einem Datum (Spalte „Datum“), sonst ganzjährig
                try:
                    data['quarter'] = str((int(iso(held)[5:7]) - 1) // 3 + 1)
                except (ValueError, KeyError, TypeError):
                    data['quarter'] = held           # z. B. „Q3“ in der Spalte Datum (wie im RPJ-Formular)
            data['no'] = re.sub(r'\.0+$', '', data['no'])
            v = _project_values({**data, 'status': 'planned'})
            plan = {}
            for c in CODES:
                text = re.sub(r'(?i)\s*(eur|€)\s*$', '', _cell(cells, mapping, c)).strip()
                if text in ('', '-', '—'):
                    continue
                amount = money(text)
                if amount < 0:
                    raise ValueError(f'{c}) {LABELS[c]}: negativer Betrag.')
                if amount:
                    plan[c] = amount
            if (v['year'], v['no']) in seen:
                raise ValueError(f"Projekt Nr. {v['no']} ({v['year']}) steht doppelt in der Datei.")
            seen.add((v['year'], v['no']))
            applied = _date(_cell(cells, mapping, 'applied_on'), 'Antrag vom') if 'applied_on' in mapping else None
            old = existing.get((v['year'], v['no']))
            if old:
                changes = [k for k in ('kind', 'name', 'quarter', 'participants', 'contact') if (old[k] or None) != (v[k] or None)]
                if plans.get(old['id'], {}) != plan:
                    changes.append('plan')
                item.update(action='update' if changes else 'same', changes=changes, id=old['id'])
            item.update(values={k: v[k] for k in ('year', 'no', 'kind', 'name', 'quarter', 'participants', 'contact')}, plan=plan,
                        applied_on=applied, **_sums(plan, {}))
        except ValueError as e:
            item.update(action='error', error=str(e))
        rows.append(item)
    if not rows:
        raise ValueError('Die Datei enthält keine Projekte.')
    years = sorted({r['values']['year'] for r in rows if not r['error']})
    totals = {y: {'categories': {c: sum(r['plan'].get(c, 0) for r in rows if not r['error'] and r['values']['year'] == y) for c in CODES},
                  'rpj': sum(r['rpj_plan'] for r in rows if not r['error'] and r['values']['year'] == y)} for y in years}
    missing = [{'year': p['year'], 'no': p['no'], 'name': p['name']} for (y, n), p in sorted(existing.items()) if y in years and (y, n) not in seen]
    return {'filename': filename, 'rows': rows, 'years': years, 'totals': totals, 'missing': missing,
            'counts': {k: sum(1 for r in rows if r['action'] == k) for k in ('new', 'update', 'same', 'error')}}


def apply_import(store, prev, selected, actor):
    """Gewählte Zeilen der Vorschau übernehmen: neue Projekte anlegen, geänderte mit Plan aktualisieren (Status, Durchführung,
    tatsächliche Teilnehmer und Notizen bleiben). Antragsdatum je Jahr, falls noch leer."""
    if not isinstance(selected, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in selected):
        raise ValueError('Ungültige Auswahl.')
    chosen = set(selected)
    rows = [r for r in prev['rows'] if r['row_no'] in chosen and r['action'] in ('new', 'update')]
    if not rows:
        raise ValueError('Keine neuen oder geänderten Projekte ausgewählt.')
    done = {'new': 0, 'update': 0}
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        for r in rows:
            v = dict(r['values'])
            old = store.db.execute('SELECT * FROM rpj_projects WHERE year=? AND no=?', (v['year'], v['no'])).fetchone()
            if (old is None) != (r['action'] == 'new'):
                raise ValueError('Der Bestand hat sich seit der Vorschau geändert – Datei bitte erneut auswählen.')
            if old:
                v.update({k: old[k] for k in ('held_on', 'attended', 'status', 'note')})
            else:
                v.update(held_on=None, attended=None, status='planned', note=None)
            _write_project(store, old['id'] if old else None, v, r['plan'])
            done[r['action']] += 1
        for y in sorted({r['values']['year'] for r in rows}):
            applied = next((r['applied_on'] for r in rows if r['values']['year'] == y and r['applied_on']), None)
            if applied:
                store.db.execute('INSERT INTO rpj_years(year,applied_on) VALUES(?,?) ON CONFLICT(year) DO UPDATE SET '
                                 'applied_on=COALESCE(rpj_years.applied_on,excluded.applied_on)', (y, applied))
        store.audit(actor, 'RPJ-Haushalt importiert', {'datei': prev['filename'], 'neu': done['new'], 'geändert': done['update'],
                                                        'jahre': sorted({r['values']['year'] for r in rows})})
    return done


TEMPLATE_HEADER = ['Jahr', 'Nr.', 'Maßnahme', 'Veranstaltung', 'Quartal', 'Teilnehmer', 'Antrag vom'] + [f'{c}) {l}' for c, l in CATEGORIES]


def template_csv():
    """Leere Vorlage mit Beispielzeile (erfunden) für den Import."""
    from berichte import _csv
    year = date.today().year + 1
    return _csv([[year, 1, 'Podiumsdiskussion', 'Beispiel: Thema der Veranstaltung', 'Q1', '30', f'01.10.{year - 1}',
                  '900', '', '400', '', '50', '', '', '', '', '', '']], TEMPLATE_HEADER)
