"""Budgets der Kreisverbände und der Schüler Union – Regel wie in der Excel-Masterdatei (Entscheidung Lennert Gust, 08.11.2026).

Jahreswerte je Bereich und Haushaltsjahr (Tabelle kv_year_values, Eingabe oder Abgleich mit der Masterdatei):
- Freundes- und Förderkreis (FFK): Jahresbetrag je Kreisverband, eigener Punkt (nicht aus Buchungen auf 8202),
- Beitragsanteil: festgestellter Betrag („Budget MB“); ohne Eingabe 25 % der tatsächlich gezahlten Beiträge der eigenen
  Mitglieder im Jahr (FO § 6 Abs. 1). Schüler Union: festes Jahresbudget, kein berechneter Anteil,
- Anfangsbestand: nur im ersten Jahr der KV-Budgets (Stand 31.12. des Vorjahres, z. B. „SB-Wert 2024“ der Masterdatei).
FFK und Beitragsanteil stehen ab 01.01. zur Verfügung. Dazu kommen Spenden auf 8200 mit Bereich = Kreisverband (FO § 2 Abs. 2),
Gutschriften und Budget-Umbuchungen auf Ausgabenkonten; Ausgaben mindern das Budget.
Jahreswechsel: Ein positiver Rest des Vorjahres steht bis 31.01. zur Verfügung – Ausgaben im Januar verbrauchen ihn zuerst –,
was dann übrig ist, verfällt an den Landesverband (FO § 6 Abs. 3). Ein Minus wird ins neue Jahr vorgetragen. Schüler Union:
der Rest wird vollständig vorgetragen (wie in der Masterdatei). Bewusst abweichend von FO § 2 Abs. 3–4 (FFK zur Hälfte,
365 Tage je Spende) – siehe REGELN-UND-OFFENE-PUNKTE.md.
Das Budgetkonto wird aus dem Journal berechnet (stornierte Buchungen und ihre Stornos zählen nicht); run() ist eine reine
Funktion und rechnet auch die Masterdatei nach (kvabgleich.py). Der Budgetabschluss hält je Jahr fest, was verfallen bzw.
vorgetragen wurde, damit spätere Buchungen das Ergebnis nicht unbemerkt ändern.
§ 2 Abs. 5: Barspenden – zwei Vertreter des geschäftsführenden Landesvorstands, Quittung, Einzahlung binnen 30 Tagen,
CDU-Buchhaltung über eine Spendenquittung informieren (Register mit Prüfhinweisen).
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from formate import euro, german_date, iso, money

NO_SHARE = ('LV', '5800')                 # kein berechneter 25-%-Anteil (Landesverband, Schüler Union)
NO_LAPSE = ('5800',)                      # Schüler Union: Rest wird vollständig vorgetragen
DONATION_ACCOUNT = '8200'                 # Spende mit Bereich = Kreisverband: ganz ins Budget (FO § 2 Abs. 2)
VALUE_FIELDS = ('ffk', 'dues_share', 'opening')
RULE = 'Masterdatei'                      # Kennung der Budgetregel im Budgetabschluss (seit 0.23)
LABEL_MAX = 80
CASH_DAYS = 30


def init(db):
    _merge_plans(db)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS kv_closings(year INTEGER NOT NULL,kv TEXT NOT NULL REFERENCES kv(code),share INTEGER NOT NULL,
        used INTEGER NOT NULL,lapsed INTEGER NOT NULL,closed_on TEXT NOT NULL,actor TEXT,PRIMARY KEY(year,kv));
    CREATE TABLE IF NOT EXISTS cash_donations(id INTEGER PRIMARY KEY,received_on TEXT NOT NULL,donor TEXT NOT NULL,
        amount INTEGER NOT NULL CHECK(amount>0),kv TEXT NOT NULL REFERENCES kv(code),receiver1 TEXT NOT NULL,receiver2 TEXT NOT NULL,
        receipt TEXT NOT NULL,deposited_on TEXT,cdu_informed_on TEXT,note TEXT,created TEXT NOT NULL,actor TEXT);
    CREATE TABLE IF NOT EXISTS kv_year_values(year INTEGER NOT NULL,kv TEXT NOT NULL REFERENCES kv(code),ffk INTEGER,dues_share INTEGER,
        opening INTEGER,updated TEXT NOT NULL,actor TEXT,PRIMARY KEY(year,kv));
    CREATE TABLE IF NOT EXISTS kv_labels(journal_id INTEGER PRIMARY KEY REFERENCES journals(id),label TEXT NOT NULL,recipient TEXT,
        source TEXT NOT NULL,updated TEXT NOT NULL,actor TEXT);
    ''')
    have = {r[1] for r in db.execute('PRAGMA table_info(kv_closings)')}
    for column, ddl in (('carried', 'INTEGER NOT NULL DEFAULT 0'), ('carry_in', 'INTEGER NOT NULL DEFAULT 0'),
                        ('jan_used', 'INTEGER NOT NULL DEFAULT 0'), ('rule', "TEXT NOT NULL DEFAULT 'bis 0.22'")):
        if column not in have:                                   # Abschlüsse vor 0.23: alte Regel, werden nicht neu verglichen
            db.execute(f'ALTER TABLE kv_closings ADD COLUMN {column} {ddl}')
    db.commit()


def _merge_plans(db):
    """Einmalig (0.22): KV-Planwerte nur noch an einer Stelle – in der Kreisverbands-Zeile des Gesamthaushalts.
    Frühere KV-Arbeitsbudgets (Tabelle budgets) hatten Vorrang vor dem Gesamthaushalt; der bisher wirksame Wert bleibt daher
    gültig und wird in die Zeile übernommen. Ein dabei ersetzter Haushaltswert steht im Änderungsprotokoll."""
    from haushalt import KV_ROWS
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='budgets'").fetchone() and \
        db.execute("SELECT 1 FROM sqlite_master WHERE name='household_plan'").fetchone()
    if not exists or db.execute("SELECT 1 FROM settings WHERE key='kv_plans_merged'").fetchone():
        return
    moved, replaced = [], []
    for year, kv, amount in db.execute('SELECT year,kv,amount FROM budgets ORDER BY year,kv').fetchall():
        title = KV_ROWS.get(kv)
        if not title:
            continue
        have = db.execute('SELECT amount FROM household_plan WHERE year=? AND title=?', (year, title)).fetchone()
        if have is not None and -have[0] == amount:
            continue
        db.execute('INSERT INTO household_plan VALUES(?,?,?,?) ON CONFLICT(year,title) DO UPDATE SET amount=excluded.amount,source=excluded.source',
                   (year, title, -amount, 'KV-Arbeitsbudget (übernommen 0.22)'))
        if have is None:
            moved.append({'year': year, 'kv': kv, 'cents': amount})
        else:
            replaced.append({'year': year, 'kv': kv, 'arbeitsbudget': amount, 'haushalt_vorher': -have[0]})
    db.execute("INSERT INTO settings(key,value) VALUES('kv_plans_merged',?)", (date.today().isoformat(),))
    if moved or replaced:
        import json
        db.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)', (datetime.now().isoformat(timespec='seconds'), 'Programm',
                   'KV-Planwerte zusammengeführt (nur noch Gesamthaushalt)', json.dumps({'uebernommen': moved, 'haushaltswert_ersetzt': replaced},
                                                                                       ensure_ascii=False)))
    db.commit()


def areas():
    """Bereiche mit Budget: Kreisverbände und Schüler Union (Code, Name)."""
    from core import KVS
    return [(code, name) for code, name in KVS if code != 'LV']


def area_name(code):
    from core import KVS
    name = dict(KVS).get(code, code)
    return name if code in ('LV', '5800') else 'KV ' + name


def set_plan(store, year, kv, amount, actor):
    """Planwert eines Kreisverbands bzw. der Schüler Union (positiver Betrag) = Zeile im Gesamthaushalt (Ausgabe, negativ)."""
    import haushalt
    if kv not in haushalt.KV_ROWS:
        raise ValueError('Planwerte gibt es nur für Kreisverbände und die Schüler Union.')
    value = None if amount is None or str(amount).strip() == '' else money(amount)
    if value is not None and value < 0:
        raise ValueError('Planwert bitte als positiven Betrag angeben.')
    haushalt.save_plan(store, int(year), haushalt.KV_ROWS[kv], None if value is None else f'{-value / 100:.2f}'.replace('.', ','), actor)


def _half_up(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _excluded_journals(store):
    """Stornierte Buchungen und ihre Stornobuchungen heben sich auf und zählen im Budget nicht. Beim Storno eines
    Bankumsatzes gehört die Auflösung aus dem Parkkonto (resolve:7, nach Runden resolve:7/1) mit dazu – die
    Stornobuchung kehrt beide um, verweist aber nur auf bank:7."""
    rows = store.rows('SELECT j.id,j.reversal_of,o.source FROM journals j JOIN journals o ON o.id=j.reversal_of')
    skip = {r['id'] for r in rows} | {r['reversal_of'] for r in rows}
    resolved = ['resolve:' + r['source'][5:] for r in rows if r['source'].startswith('bank:')]
    for i in range(0, len(resolved), 500):
        chunk = resolved[i:i + 500]
        skip |= {r[0] for r in store.db.execute(f"SELECT id FROM journals WHERE source IN ({','.join('?' * len(chunk))})", chunk)}
    return skip


def start(store):
    """Beginn der KV-Budgets (Stammdaten, immer ein 1. Januar): ältere Buchungen und Jahre zählen nicht."""
    return store.config().get('fo_start') or '2026-01-01'


# ---------------------------------------------------------------- Jahreswerte und Bezeichnungen

def year_values(store):
    """Jahreswerte {(Jahr, Bereich): {ffk, dues_share, opening, …}}."""
    return {(r['year'], r['kv']): r for r in store.rows('SELECT * FROM kv_year_values')}


def _cents(raw, cents):
    if raw is None or (isinstance(raw, str) and raw.strip() == ''):
        return None
    if cents:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError('Betrag in Cent erwartet.')
        return raw
    return money(raw)


def check_values(store, year, kv, data, cents=False):
    """Jahreswerte prüfen und in Cent umwandeln (ohne zu speichern): {Feld: Cent oder None}."""
    year = int(year)
    if kv not in dict(areas()):
        raise ValueError('Jahreswerte gibt es nur für Kreisverbände und die Schüler Union.')
    first, last = int(start(store)[:4]), date.today().year + 1
    if not first <= year <= last:
        raise ValueError(f'Jahreswerte gibt es für {first} (Beginn der KV-Budgets, Stammdaten) bis {last}.')
    changes = {}
    for key in VALUE_FIELDS:
        if key not in data:
            continue
        value = _cents(data[key], cents)
        if value is not None and abs(value) > 100_000_000:
            raise ValueError('Betrag unplausibel hoch.')
        if key != 'opening' and value is not None and value < 0:
            raise ValueError('Freundes- und Förderkreis und Beitragsanteil bitte als positiven Betrag angeben.')
        if key == 'opening' and value is not None and year != first:
            raise ValueError(f'Einen Anfangsbestand gibt es nur im ersten Jahr der KV-Budgets ({first}).')
        if key == 'ffk' and value and kv in NO_SHARE:
            raise ValueError('Die Schüler Union hat keinen Anteil am Freundes- und Förderkreis (Budget im Feld Beitragsanteil/Budget).')
        changes[key] = value
    return changes


def set_values(store, year, kv, data, actor, cents=False, source='Eingabe', in_transaction=False):
    """Jahreswerte eines Bereichs: nur übergebene Felder ändern, leer = nicht festgelegt. FFK und Beitragsanteil ≥ 0;
    Anfangsbestand (auch negativ) nur im ersten Jahr der KV-Budgets. Liefert True bei einer Änderung.
    in_transaction: Aufrufer hält schon eine Schreibtransaktion (Abgleich mit der Masterdatei: alles oder nichts)."""
    year = int(year)
    changes = check_values(store, year, kv, data, cents)
    if in_transaction:
        return _store_values(store, year, kv, changes, actor, source)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        return _store_values(store, year, kv, changes, actor, source)


def _store_values(store, year, kv, changes, actor, source):
    before = store.db.execute('SELECT * FROM kv_year_values WHERE year=? AND kv=?', (year, kv)).fetchone()
    old = {k: before[k] if before else None for k in VALUE_FIELDS}
    new = {**old, **changes}
    if new == old:
        return False
    if all(v is None for v in new.values()):
        store.db.execute('DELETE FROM kv_year_values WHERE year=? AND kv=?', (year, kv))
    else:
        store.db.execute('INSERT INTO kv_year_values(year,kv,ffk,dues_share,opening,updated,actor) VALUES(?,?,?,?,?,?,?) '
                         'ON CONFLICT(year,kv) DO UPDATE SET ffk=excluded.ffk,dues_share=excluded.dues_share,opening=excluded.opening,'
                         'updated=excluded.updated,actor=excluded.actor',
                         (year, kv, new['ffk'], new['dues_share'], new['opening'], datetime.now().isoformat(timespec='seconds'), actor))
    store.audit(actor, 'KV-Jahreswerte geändert', {'year': year, 'kv': kv, 'quelle': source, 'vorher': old, 'nachher': new})
    return True


def _clean_text(value, limit):
    return ' '.join(str(value or '').split())[:limit]


def set_label(store, journal_id, label, recipient, actor, source='manuell', in_transaction=False):
    """Kurze Bezeichnung und Empfänger einer Buchung im KV-Budget (Bericht für den Kreisvorsitz). Leer = automatisch.
    Nicht für Budget-Umbuchungen: Sie betreffen zwei Bereiche, ihre Bezeichnung ergibt sich aus Gegenbereich und Grund."""
    jid = int(journal_id)
    label, recipient = _clean_text(label, LABEL_MAX), _clean_text(recipient, 60)
    found = store.db.execute("SELECT j.source FROM entries e JOIN accounts a ON a.code=e.account JOIN journals j ON j.id=e.journal_id "
                             "WHERE e.journal_id=? AND e.kv!='LV' AND (a.kind='expense' OR e.account=?)", (jid, DONATION_ACCOUNT)).fetchone()
    if not found:
        raise ValueError('Buchung gehört zu keinem Kreisverbands-Budget.')
    if str(found[0]).startswith('kvumbuchung:'):
        raise ValueError('Budget-Umbuchungen tragen ihre Bezeichnung selbst (Gegenbereich und Grund).')
    if in_transaction:
        return _store_label(store, jid, label, recipient, actor, source)
    with store.db:
        return _store_label(store, jid, label, recipient, actor, source)


def _store_label(store, jid, label, recipient, actor, source):
    if not label and not recipient:
        store.db.execute('DELETE FROM kv_labels WHERE journal_id=?', (jid,))
    else:
        store.db.execute('INSERT INTO kv_labels VALUES(?,?,?,?,?,?) ON CONFLICT(journal_id) DO UPDATE SET label=excluded.label,'
                         'recipient=excluded.recipient,source=excluded.source,updated=excluded.updated,actor=excluded.actor',
                         (jid, label or '', recipient or None, source, datetime.now().isoformat(timespec='seconds'), actor))
    if source == 'manuell':
        store.audit(actor, 'Bezeichnung im KV-Budget geändert', {'journal': jid})


_NOISE = re.compile(r'\b(?:EREF|MREF|CRED|KREF|ABWA|ABWE)\+\S*|\b(?:BIC|SWIFT)[:+]?\s*[A-Z0-9]{8,11}\b|\bNOTPROVIDED\b|'
                    r'\b(?:SVWZ|IBAN|End-to-End-Ref\.?|Kundenreferenz|Mandatsref\.?|Gläubiger-ID)\b[+:]?\s*', re.I)
_TOKENS = re.compile(r'\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b|\b[A-Za-z0-9]*\d[A-Za-z0-9]{9,}\b|\b\d{6,}\b')


_KIND_WORDS = re.compile(r'^\W*(?:(?:SEPA[- ]?)?(?:ECHTZEIT[- ]?)?(?:ERST|FOLGE)?(?:LASTSCHRIFT|GUTSCHRIFT|ÜBERWEISUNG|UEBERWEISUNG|DAUERAUFTRAG|'
                         r'KARTENZAHLUNG|BASISLASTSCHRIFT|EINZUG|ABBUCHUNG)(?:SEINGANG|SAUSGANG)?|ONLINE[- ]?BANKING|DIRECT BANKING)\b[\s:/(-]*', re.I)
_NUMBERED = re.compile(r'\b(?:KD|RG|RE|Kd|Rg|Re|Kunden|Rechnungs|Vertrags|Auftrags|Beleg|Mandats?)[-. ]?(?:Nr|nummer)\b\.?:?\s*\S*', re.I)
_IDS = re.compile(r'\b[A-Z]{1,4}[-/]?\d{3,}\b|\b[A-Z]{2}\d{2}[A-Z0-9]*\b')   # Kennungen, auch gekürzte IBAN (DE0276 …)
_TAIL = re.compile(r'\s(?:MANDATSREF\w*|MANDAT\b|GL(?:Ä|AE|A)UBIGER[- ]?ID\w*|CREDITOR[- ]?ID|KUNDENREFERENZ\w*|END[- ]TO[- ]END\w*|'
                   r'ABWEICHENDER?\s+(?:EMPF|AUFTR)\w*|IBAN\b|BIC\b)', re.I)   # SEPA-Angaben hinter dem Verwendungszweck


def short_text(text, limit=45):
    """Kurze Bezeichnung aus einem Buchungstext: ohne Übernahmehinweis, Buchungsart (LASTSCHRIFT, ÜBERWEISUNG …),
    SEPA-Kennungen, IBAN/BIC, Kunden-/Rechnungsnummern und lange Nummern; höchstens limit Zeichen."""
    text = str(text or '').split(' · Excel-Übernahme')[0].split(' · Kostenstelle')[0]
    for _ in range(2):                                   # z. B. „ÜBERWEISUNG (DIRECT BANKING …“
        text = _KIND_WORDS.sub('', text.strip())
    tail = _TAIL.search(text)
    if tail and tail.start() >= 3:                       # alles ab „Mandatsreferenz“, „Gläubiger-ID“ … ist Technik
        text = text[:tail.start()]
    gap = ' \x00 '                                       # Markierung für Entferntes: Trennzeichen daneben fallen mit weg
    text = _NOISE.sub(gap, text)
    text = _NUMBERED.sub(gap, text)
    text = _IDS.sub(gap, _TOKENS.sub(gap, text))
    text = re.sub(r'\s*[/|;,]\s*\x00|\x00\s*[/|;,]\s*', ' \x00 ', text).replace('\x00', ' ')
    text = re.sub(r'\(\s*\)|\s*[/|;,]\s*(?=[/|;,]|$)', ' ', text)
    text = ' '.join(text.split()).strip(' -–/,;:.()')
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    return (cut.rsplit(' ', 1)[0] if ' ' in cut[limit // 2:] else cut).rstrip(' -–/,;:.(') + '…'


# ---------------------------------------------------------------- Budgetkonto

def run(first, opening, credits, events, today, lapse=True):
    """Budgetkonto nach der Regel der Masterdatei (reine Funktion: Programm und nachgerechnete Masterdatei).

    first: erstes Jahr; opening: Anfangsbestand 01.01. des ersten Jahres (Cent, auch negativ);
    credits: {Jahr: {'ffk', 'dues_share', 'fixed', 'paid'}}; events: Bewegungen ab dem ersten Jahr
    [{'day', 'kind', 'amount' (+ Zugang, − Abgang), 'label', …}]; today: Stichtag; lapse=False: Rest wird ganz vorgetragen.
    Liefert {'years': [...], 'lines': [...]} – Zeilen je Jahr mit laufendem Stand (Kontoauszug des Bereichs)."""
    events = sorted((e for e in events if e['day'] >= f'{first}-01-01'), key=lambda e: (e['day'], e.get('journal') or 0))
    if today < f'{first}-01-01' and not events:
        return {'years': [], 'lines': []}
    last = max([int(today[:4])] + [int(e['day'][:4]) for e in events])
    years, lines, carry, i = [], [], opening or 0, 0
    for year in range(first, last + 1):
        lapse_day, c = f'{year}-01-31', credits.get(year, {})
        y = {'year': year, 'carry': carry, 'ffk': c.get('ffk') or 0, 'dues_share': c.get('dues_share') or 0, 'dues_fixed': bool(c.get('fixed')),
             'dues_paid': c.get('paid') or 0, 'gains': 0, 'donations': 0, 'spent': 0, 'lapsed': 0, 'jan_from_old': 0,
             'rest_lapsed': None, 'carried_out': None, 'lapse_day': lapse_day if lapse and carry > 0 else None}
        old, bal = (carry, 0) if lapse and carry > 0 else (0, carry)
        state = {'lapsed': not (lapse and carry > 0)}

        def add(day, kind, amount, label, **extra):
            lines.append({'year': year, 'day': day, 'kind': kind, 'amount': amount, 'label': label, 'balance': old + bal, **extra})

        def do_lapse():
            nonlocal old
            state['lapsed'] = True
            y['lapsed'] = old
            if years:
                years[-1]['rest_lapsed'] = old
            if old:
                gone, old = old, 0
                add(lapse_day, 'verfall', -gone, f'Verfall Rest {year - 1} (FO § 6 Abs. 3)')

        if year == first:
            add(f'{year}-01-01', 'anfang', carry, f'Anfangsbestand 01.01.{year}')
        else:
            add(f'{year}-01-01', 'uebertrag', carry, f'Übertrag aus {year - 1}' + (f' (verfügbar bis 31.01.{year})' if lapse and carry > 0 else ''))
        if c.get('ffk') is not None and (y['ffk'] or not c.get('budget')):   # FFK als eigener Punkt (Schüler Union ohne FFK: keine Zeile)
            bal += y['ffk']
            add(f'{year}-01-01', 'ffk', y['ffk'], f'Freundes- und Förderkreis {year}')
        if y['dues_share'] or c.get('fixed'):
            bal += y['dues_share']
            add(f'{year}-01-01', 'beitrag', y['dues_share'],
                ('Budget ' if c.get('budget') else 'Beitragsanteil ') + str(year) + ('' if c.get('fixed') else ' (25 % der gezahlten Beiträge)'))
        while i < len(events) and events[i]['day'] <= f'{year}-12-31':
            e = events[i]
            i += 1
            if not state['lapsed'] and e['day'] > lapse_day:
                do_lapse()
            amount = e['amount']
            if amount < 0:
                take = 0 if state['lapsed'] else min(old, -amount)
                old -= take
                bal += amount + take
                y['jan_from_old'] += take
                y['spent'] -= amount
            else:
                back = 0
                if not state['lapsed'] and e['kind'] != 'spende':   # Erstattung im Januar: macht zuerst den Verbrauch aus dem Vorjahresrest rückgängig
                    back = min(amount, y['jan_from_old'])
                    old += back
                    y['jan_from_old'] -= back
                bal += amount - back
                y['gains'] += amount
                if e['kind'] == 'spende':
                    y['donations'] += amount
            add(e['day'], e['kind'], amount, e.get('label') or '', **{k: e[k] for k in ('journal', 'recipient', 'auto') if k in e})
        if not state['lapsed'] and (today > lapse_day or year < last):
            do_lapse()
        y['open_old'] = old                                  # Rest des Vorjahres, der noch nicht verfallen ist (nur im Januar)
        y['balance'] = bal                                   # Stand ohne den noch verfügbaren Vorjahresrest
        y['end'] = old + bal
        if years:
            years[-1]['carried_out'] = min(carry, 0) if lapse else carry
            years[-1]['jan_used'] = y['jan_from_old']
        years.append(y)
        carry = old + bal
    return {'years': years, 'lines': lines}


def auto_from(cfg):
    """Schalter „Beitragsanteil der Kreisverbände automatisch“ (Verwaltung, ab 0.31): erstes Haushaltsjahr, ab dem der Anteil immer aus
    den gezahlten Beiträgen berechnet wird (25 %, FO § 6 Abs. 1) und ein festgestellter Betrag nicht mehr zählt; None = aus."""
    year = str(cfg.get('kv_share_auto_from') or '')
    return int(year) if cfg.get('kv_share_auto') == 'yes' and year.isdigit() else None


def share_is_auto(auto, kv, year):
    return auto is not None and int(year) >= auto and kv not in NO_SHARE


def check_auto_change(store, before, after):
    """Der Schalter darf kein abgeschlossenes Budgetjahr umstellen (der Abschluss hält die Werte fest)."""
    old, new = auto_from(before), auto_from(after)
    if old == new:
        return
    first = int((after.get('fo_start') or '2026')[:4])
    if new is not None and not first <= new <= date.today().year + 1:
        raise ValueError(f'Beitragsanteil automatisch: gilt ab einem Haushaltsjahr von {first} (Beginn der KV-Budgets) bis {date.today().year + 1}.')
    closed = [r[0] for r in store.db.execute('SELECT DISTINCT year FROM kv_closings ORDER BY year')]
    first_changed = min(y for y in (old, new) if y is not None)   # ab hier rechnet der Anteil anders – und damit jeder spätere Übertrag
    if closed and max(closed) >= first_changed:
        if old is not None and old <= max(closed):   # mit dem Schalter abgeschlossen: Ausschalten oder Verschieben änderte dieses Jahr
            raise ValueError(f'Der Schalter gilt seit {old}, und das Budgetjahr {max(closed)} ist damit abgeschlossen – '
                             'er lässt sich nicht mehr ausschalten oder auf ein anderes Jahr legen.')
        raise ValueError(f'Das Budgetjahr {max(closed)} ist abgeschlossen – der Schalter kann erst ab {max(closed) + 1} gelten.')


def _context(store):
    """Einmal je Anfrage: Storno-Paare, Jahreswerte, Bezeichnungen, Bereichsnamen, Beginn, Schalter „Beitragsanteil automatisch“."""
    return {'skip': _excluded_journals(store), 'values': year_values(store), 'start': start(store), 'auto': auto_from(store.config()),
            'labels': {r['journal_id']: r for r in store.rows('SELECT * FROM kv_labels')}}


def _bank_id(source):
    m = re.match(r'(?:bank|resolve):(\d+)', source or '')
    return int(m.group(1)) if m else None


def events(store, kv, until=None, ctx=None):
    """Bewegungen eines Bereichs ab Beginn der KV-Budgets: Ausgaben, Gutschriften und Budget-Umbuchungen auf Ausgabenkonten
    sowie Spenden auf 8200 – je Buchung ein Betrag (+ Zugang, − Abgang), mit kurzer Bezeichnung und Empfänger."""
    ctx = ctx or _context(store)
    rows = store.rows("SELECT j.id,j.day,j.description,j.source,SUM(e.credit-e.debit) amount,MAX(e.account=?) donation FROM entries e "
                      "JOIN journals j ON j.id=e.journal_id JOIN accounts a ON a.code=e.account WHERE e.kv=? AND (a.kind='expense' OR e.account=?) "
                      "AND j.day>=? AND j.day<=? GROUP BY j.id ORDER BY j.day,j.id",
                      (DONATION_ACCOUNT, kv, DONATION_ACCOUNT, ctx['start'], until or '9999-12-31'))
    rows = [r for r in rows if r['id'] not in ctx['skip'] and r['amount'] != 0]
    banks = {}
    ids = sorted({b for b in (_bank_id(r['source']) for r in rows) if b})
    for n in range(0, len(ids), 500):
        chunk = ids[n:n + 500]
        banks.update({r['id']: r['name'] for r in store.rows(f"SELECT id,name FROM bank WHERE id IN ({','.join('?' * len(chunk))})", chunk)})
    bills = {r['id']: r['creditor'] for r in store.rows('SELECT id,creditor FROM bills')} if any(r['source'].startswith('bill:') for r in rows) else {}
    out = []
    for r in rows:
        own = ctx['labels'].get(r['id'])
        if r['source'].startswith('kvumbuchung:'):
            kind = 'umbuchung'
            other = store.db.execute('SELECT kv FROM entries WHERE journal_id=? AND kv!=? LIMIT 1', (r['id'], kv)).fetchone()
            reason = r['description'].split(' – ', 1)[1] if ' – ' in r['description'] else ''
            who = area_name(other[0]) if other else 'anderem Bereich'
            label = short_text(f"Umbuchung {'an' if r['amount'] < 0 else 'von'} {who}" + (f': {reason}' if reason else ''), LABEL_MAX)
            recipient = None
        else:
            kind = 'spende' if r['donation'] else 'ausgabe' if r['amount'] < 0 else 'gutschrift'
            label = short_text(r['description'])
            recipient = None if kind == 'spende' else (   # keine Spendernamen im Bericht für den Kreisvorsitz
                banks.get(_bank_id(r['source'])) or (bills.get(int(r['source'][5:])) if r['source'].startswith('bill:') else None))
        item = {'day': r['day'], 'kind': kind, 'amount': r['amount'], 'journal': r['id'], 'label': label, 'recipient': recipient, 'auto': True}
        if own and kind != 'umbuchung':
            item.update(label=own['label'] or label, recipient=own['recipient'] or recipient, auto=False)
        out.append(item)
    return out


def _credits(store, kv, ctx, until=None):
    paid = dues_paid(store, kv, until) if kv not in NO_SHARE else {}
    years = {y for (y, k) in ctx['values'] if k == kv} | set(paid)
    out = {}
    for year in years:
        row = ctx['values'].get((year, kv))
        fixed = row['dues_share'] if row and not share_is_auto(ctx.get('auto'), kv, year) else None   # Schalter: festgestellter Betrag zählt nicht
        share = fixed if fixed is not None else (_half_up(Decimal(paid.get(year, 0)) / 4) if kv not in NO_SHARE else 0)
        out[year] = {'ffk': row['ffk'] if row else None, 'dues_share': max(share, 0), 'fixed': fixed is not None, 'paid': paid.get(year, 0),
                     'budget': kv == '5800'}
    return out


def ledger(store, kv, today=None, until=None, ctx=None, values=None):
    """Budgetkonto eines Bereichs (Stand heute bzw. until). values: Jahreswerte zur Probe (Vorschau Abgleich)."""
    today = until or today or date.today().isoformat()          # Stand: spätere (auch vordatierte) Buchungen zählen noch nicht
    ctx = ctx or _context(store)
    if values is not None:
        ctx = {**ctx, 'values': values}
    first = int(ctx['start'][:4])
    opening = (ctx['values'].get((first, kv)) or {}).get('opening') or 0
    result = run(first, opening, _credits(store, kv, ctx, today), events(store, kv, today, ctx), today, lapse=kv not in NO_LAPSE)
    years = result['years']
    now = years[-1] if years else None
    nxt = []
    if now and now['open_old'] > 0:
        nxt.append({'until': now['lapse_day'], 'amount': now['open_old'], 'label': f"Rest {now['year'] - 1}"})
    if now and kv not in NO_LAPSE and now['balance'] > 0:
        nxt.append({'until': f"{now['year'] + 1}-01-31", 'amount': now['balance'], 'label': f"Rest {now['year']}"})
    available = now['end'] if now else 0
    return {'kv': kv, 'start': first, 'available': available, 'uncovered': max(0, -available), 'lapsed': sum(y['lapsed'] for y in years),
            'spent': sum(y['spent'] for y in years), 'next': nxt, 'years': years, 'lines': result['lines'], 'lapse': kv not in NO_LAPSE}


def overview(store, today=None, until=None):
    ctx = _context(store)
    return {code: ledger(store, code, today, until, ctx) for code, _ in areas()}


def year_overview(store, year, until=None):
    """Bereiche eines Haushaltsjahres: Plan (Zeile im Gesamthaushalt), Übertrag, FFK, Beitragsanteil, Zugänge, Ausgaben, Stand.
    Beiträge (Ist) = tatsächlich gezahlte Beiträge der eigenen Mitglieder – Grundlage des Anteils, wenn kein Betrag festgelegt ist.
    until: Stand eines Tages (Monatsbericht)."""
    import beitraege
    import haushalt
    from core import KVS
    year, last = int(year), until or '9999-12-31'
    ctx = _context(store)
    active = year >= int(ctx['start'][:4])
    plans = {r['title']: -r['amount'] for r in store.rows('SELECT title,amount FROM household_plan WHERE year=?', (year,))}
    invoices = {r['kv']: r['paid'] for r in store.rows(
        "SELECT i.kv,SUM(p.amount) paid FROM payments p JOIN invoices i ON i.id=p.invoice_id JOIN bank b ON b.id=p.bank_id "
        "WHERE i.kind='dues' AND i.year=? AND b.state='booked' AND substr(b.booked,1,4)=? AND b.booked<=? GROUP BY i.kv", (year, str(year), last))}
    # Plan-Vergleich wie im Gesamthaushalt: Ausgabenkonten netto im Haushaltsjahr (nicht das Budgetkonto nach Datum, ohne Spenden)
    fiscal = {r['kv']: r['spent'] for r in store.rows(
        "SELECT e.kv,COALESCE(SUM(e.debit-e.credit),0) spent FROM entries e JOIN accounts a ON a.code=e.account JOIN journals j ON j.id=e.journal_id "
        "WHERE a.kind='expense' AND COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER))=? AND j.day<=? GROUP BY e.kv", (year, last))}
    out = []
    for kv, name in KVS:
        assigned = beitraege.paid_by_kv(store, kv, until).get(year, 0)
        paid = invoices.get(kv, 0) + assigned
        plan = plans.get(haushalt.KV_ROWS.get(kv))
        led = ledger(store, kv, until=until, ctx=ctx) if kv != 'LV' else None
        y = next((x for x in led['years'] if x['year'] == year), None) if led else None
        if not y:   # Landesverband, Jahr vor Beginn oder nach heute: Werte ohne Budgetkonto
            spent = fiscal.get(kv, 0)
            row = ctx['values'].get((year, kv)) or {}
            if share_is_auto(ctx.get('auto'), kv, year):   # Schalter: auch im Folgejahr zählt der festgestellte Betrag nicht (Prüfbefund 0.31)
                row = {**row, 'dues_share': None}
                auto_share = max(_half_up(Decimal(paid) / 4), 0)
            else:
                auto_share = 0
            y = {'carry': 0, 'ffk': row.get('ffk') or 0, 'dues_share': row.get('dues_share') or auto_share, 'dues_fixed': row.get('dues_share') is not None,
                 'gains': max(-spent, 0), 'donations': 0, 'spent': max(spent, 0), 'lapsed': 0, 'end': 0, 'rest_lapsed': None, 'open_old': 0}
        net = y['spent'] - y['gains']
        plan_spent = fiscal.get(kv, 0)
        out.append({'code': kv, 'name': name, 'paid': paid, 'paid_invoices': invoices.get(kv, 0), 'paid_assigned': assigned,
                    'carry': y['carry'], 'old_lapsed': y['lapsed'], 'ffk': y['ffk'], 'share': y['dues_share'], 'share_fixed': y['dues_fixed'],
                    'donation_share': y['donations'], 'gains': y['gains'], 'spent': y['spent'], 'net': net,
                    'ist_budget': y['ffk'] + y['dues_share'], 'end': y['end'], 'remaining': y['end'], 'rest_lapsed': y['rest_lapsed'],
                    'plan': plan, 'plan_spent': plan_spent, 'plan_remaining': plan - plan_spent if plan is not None else None,
                    'fo_active': active and kv != 'LV',
                    'available': led['available'] if led else None, 'lapse': led['lapse'] if led else False,
                    'set': {f: (ctx['values'].get((year, kv)) or {}).get(f) for f in VALUE_FIELDS},
                    'share_auto': share_is_auto(ctx.get('auto'), kv, year),
                    'share_computed': _half_up(Decimal(paid) / 4) if kv not in NO_SHARE else 0})
    return out


def dues_paid(store, kv, until=None):
    """Tatsächlich gezahlte Beiträge (Ist) der Mitglieder eines Kreisverbands je Beitragsjahr: Zahlungen auf Beitragsrechnungen
    im Beitragsjahr und zugeordnete 8100-Zahlungen im Beitragsjahr (beitraege.py). Beitragsrechnungen selbst (Soll) zählen nicht."""
    import beitraege
    out = dict(beitraege.paid_by_kv(store, kv, until))
    for year, paid in store.db.execute(
            "SELECT i.year,SUM(p.amount) FROM payments p JOIN invoices i ON i.id=p.invoice_id JOIN bank b ON b.id=p.bank_id "
            "WHERE i.kind='dues' AND i.kv=? AND b.state='booked' AND substr(b.booked,1,4)=CAST(i.year AS TEXT) AND b.booked<=? GROUP BY i.year",
            (kv, until or '9999-12-31')):
        out[year] = out.get(year, 0) + paid
    return out


def detail(store, kv, year):
    """Seite eines Bereichs: Kennzahlen des Jahres, Budgetkonto (Bewegungen mit Stand) und Beitragseingänge je Mitglied."""
    from core import KVS
    names = dict(KVS)
    if kv not in names:
        raise ValueError('Unbekannter Bereich.')
    year = int(year)
    row = next(b for b in year_overview(store, year) if b['code'] == kv)
    skip = _excluded_journals(store)
    payments = [r for r in store.rows(
        'SELECT a.member_id,m.number,m.name,m.first_name,m.last_name,a.amount,j.day,e.journal_id FROM dues_assignments a '
        'JOIN members m ON m.id=a.member_id JOIN entries e ON e.id=a.entry_id JOIN journals j ON j.id=e.journal_id '
        'WHERE a.kv=? AND a.year=? AND substr(j.day,1,4)=CAST(a.year AS TEXT) ORDER BY j.day,a.id', (kv, year)) if r['journal_id'] not in skip]
    payments += [{**r, 'external': True} for r in store.rows(    # Personen, die nicht (mehr) in der Mitgliederliste stehen
        'SELECT NULL member_id,a.number,a.name,NULL first_name,NULL last_name,a.amount,j.day,e.journal_id,a.reason FROM dues_external a '
        'JOIN entries e ON e.id=a.entry_id JOIN journals j ON j.id=e.journal_id '
        'WHERE a.kv=? AND a.year=? AND substr(j.day,1,4)=CAST(a.year AS TEXT) ORDER BY j.day,a.id', (kv, year)) if r['journal_id'] not in skip]
    payments += [{**r, 'invoice': True} for r in store.rows(
        "SELECT i.member_id,m.number,m.name,m.first_name,m.last_name,p.amount,b.booked day,i.number invoice_number FROM payments p "
        "JOIN invoices i ON i.id=p.invoice_id JOIN bank b ON b.id=p.bank_id LEFT JOIN members m ON m.id=i.member_id "
        "WHERE i.kind='dues' AND i.kv=? AND i.year=? AND b.state='booked' AND substr(b.booked,1,4)=? ORDER BY b.booked", (kv, year, str(year)))]
    payments.sort(key=lambda r: (r['day'], str(r.get('last_name') or r.get('name') or '')))
    members = store.db.execute("SELECT COUNT(*) FROM members WHERE kv=? AND joined<=? AND (left_on IS NULL OR left_on='' OR left_on>=?)",
                               (kv, f'{year}-12-31', f'{year}-01-01')).fetchone()[0]
    paying = len({p['member_id'] for p in payments if p.get('member_id')})
    led = ledger(store, kv) if kv != 'LV' else None
    statement = statement_for(led, year) if led else None
    return {**row, 'members': members, 'paying_members': paying, 'payments': payments,
            'transfers': [t for t in transfers(store, year) if kv in (t['from'], t['to'])], 'statement': statement,
            'next': led['next'] if led else [], 'start': led['start'] if led else None}


def statement_for(led, year):
    """Budgetkonto eines Jahres für Seite und PDF: Kennzahlen und Bewegungen mit laufendem Stand."""
    y = next((x for x in led['years'] if x['year'] == year), None)
    if not y:
        return {'year': year, 'active': False, 'lines': []}
    lines = [dict(line) for line in led['lines'] if line['year'] == year]
    after = next((x for x in led['years'] if x['year'] == year + 1), None)
    return {'year': year, 'active': True, 'lapse': led['lapse'], 'lines': lines, 'carry': y['carry'], 'lapsed': y['lapsed'],
            'lapse_day': y['lapse_day'], 'ffk': y['ffk'], 'dues_share': y['dues_share'], 'dues_fixed': y['dues_fixed'], 'gains': y['gains'],
            'donations': y['donations'], 'spent': y['spent'], 'end': y['end'], 'open_old': y['open_old'], 'balance': y['balance'],
            'rest_lapsed': y['rest_lapsed'], 'jan_used': y.get('jan_used'), 'carried_out': y['carried_out'],
            'open_next': after['open_old'] if after else None}


# ---------------------------------------------------------------- Budgetabschluss

def closing_due(store, today=None):
    """Haushaltsjahre ab Beginn der KV-Budgets, deren Rest am 31.01. des Folgejahres verfallen ist, ohne Budgetabschluss."""
    today = today or date.today().isoformat()
    first = int(start(store)[:4])
    done = {r[0] for r in store.db.execute('SELECT DISTINCT year FROM kv_closings')}
    return [y for y in range(first, int(today[:4])) if f'{y + 1}-01-31' < today and y not in done]


CLOSING = ('share', 'used', 'lapsed', 'carried', 'carry_in', 'jan_used')


def _closing_values(led, year):
    """Abschluss eines Jahres: Zugänge (FFK, Beitragsanteil, Spenden, Gutschriften), Ausgaben, am 31.01. des Folgejahres verfallen,
    ins Folgejahr vorgetragen, Übertrag ins Jahr, im Januar des Folgejahres aus dem Rest verbraucht.
    Abstimmung: carry_in + share − used = Stand 31.12. = jan_used + lapsed + carried (bei positivem Stand und Verfall)."""
    y = next((x for x in led['years'] if x['year'] == year), None) if led else None
    if not y:
        return dict.fromkeys(CLOSING, 0)
    carried = y['carried_out'] if y['carried_out'] is not None else (y['end'] if not led['lapse'] else min(y['end'], 0))
    return {'share': y['ffk'] + y['dues_share'] + y['gains'], 'used': y['spent'], 'lapsed': y['rest_lapsed'] or 0, 'carried': carried,
            'carry_in': y['carry'], 'jan_used': y.get('jan_used') or 0}


def close_year(store, year, actor, today=None):
    """Budgetabschluss eines Jahres (nach dem 31.01. des Folgejahres): je Bereich Zugänge, Ausgaben, verfallen, vorgetragen."""
    today = today or date.today().isoformat()
    year = int(year)
    if not f'{year + 1}-01-31' < today:
        raise ValueError(f'Die KV-Budgets {year} verfallen erst mit Ablauf des 31.01.{year + 1}.')
    if year < int(start(store)[:4]):
        raise ValueError(f'Für {year} gibt es keine KV-Budgets (Beginn {start(store)[:4]}).')
    import beitraege
    open_dues = beitraege.pending(store, year)
    if open_dues:     # Pflicht (Wunsch Lennert Gust 08.11.2026): erst alle Beitragszahlungen des Jahres einem Mitglied zuordnen
        cases = sum(1 for x in open_dues if x['in_case'])
        raise ValueError(f'Vor dem Budgetabschluss {year} alle Beitragszahlungen des Jahres einem Mitglied zuordnen – noch {len(open_dues)} offen '
                         f'(Buchhaltung → Beitragseingänge{f"; davon {cases} als Klärfall bei der Mitgliederverwaltung" if cases else ""}). '
                         'Sonst fehlt den Kreisverbänden ihr Beitragsanteil. Gehört eine Zahlung zu keinem '
                         'Mitglied (z. B. Sammelüberweisung ohne Liste), die Buchung im Buchungsplatz stornieren und richtig verbuchen bzw. '
                         'mit Grund auf das Parkkonto 1370 legen.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if store.db.execute('SELECT 1 FROM kv_closings WHERE year=?', (year,)).fetchone():
            raise ValueError(f'Budgetjahr {year} ist bereits abgeschlossen.')
        ctx, rows = _context(store), []
        for code, _ in areas():
            v = _closing_values(ledger(store, code, today, ctx=ctx), year)
            store.db.execute('INSERT INTO kv_closings(year,kv,share,used,lapsed,closed_on,actor,carried,carry_in,jan_used,rule) '
                             'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (year, code, v['share'], v['used'], v['lapsed'], today, actor, v['carried'],
                                                                v['carry_in'], v['jan_used'], RULE))
            rows.append({'kv': code, 'uebertrag': v['carry_in'], 'zugaenge': v['share'], 'ausgaben': v['used'], 'januar': v['jan_used'],
                         'verfallen': v['lapsed'], 'vorgetragen': v['carried']})
        store.audit(actor, f'KV-Budgets {year} abgeschlossen (Finanzordnung § 6 Abs. 3)', {'kv': rows})


def closings(store):
    """Festgehaltene Abschlüsse mit Hinweis, wenn sich der berechnete Wert seither geändert hat."""
    out, ctx, cache = [], _context(store), {}
    for r in store.rows('SELECT * FROM kv_closings ORDER BY year DESC,kv'):
        key = (r['kv'], r['closed_on'])
        if key not in cache:
            cache[key] = ledger(store, r['kv'], r['closed_on'], ctx=ctx) if r['kv'] != 'LV' else None
        v = _closing_values(cache[key], r['year'])
        r['now_lapsed'] = v['lapsed']
        r['old_rule'] = r['rule'] != RULE                         # Abschluss nach der Regel bis 0.22: nicht neu verglichen
        r['changed'] = not r['old_rule'] and any(v[k] != r[k] for k in CLOSING)
        out.append(r)
    return out


# ---------------------------------------------------------------- Budget-Umbuchung zwischen Bereichen

TRANSFER = 'kvumbuchung:'


def transfer(store, data, actor):
    """Budget-Umbuchung (Wunsch Lennert Gust 29.10.2026): ein Bereich beteiligt sich an Kosten eines anderen, z. B. an
    einer Rechnung für eine gemeinsame Feier. Gebucht wird auf demselben Ausgaben-Haushaltsposten: Soll beim
    Bereich, der sich beteiligt (sein Budget wird belastet), Haben beim Bereich, der entlastet wird. Der Gesamthaushalt
    bleibt gleich; Kreisverbands-Zeilen und Finanzrahmen (§§ 2, 6) verschieben sich. Storno über reverse_transfer."""
    from core import KVS
    import haushalt
    names = {code: name if code in ('LV', '5800') else 'KV ' + name for code, name in KVS}
    src, dst = data.get('from'), data.get('to')
    if src not in names or dst not in names or src == dst:
        raise ValueError('Bitte zwei verschiedene Bereiche wählen.')
    amount = money(data.get('amount', ''))
    if amount <= 0:
        raise ValueError('Bitte einen Betrag größer als 0 angeben.')
    day = iso(data.get('day') or date.today().isoformat())
    if day > date.today().isoformat():
        raise ValueError('Das Datum der Umbuchung darf nicht in der Zukunft liegen.')
    if store.data_start() and day < store.data_start():
        raise ValueError(f'Das Datum liegt vor dem Datenbeginn {german_date(store.data_start())}.')
    reason = ' '.join(str(data.get('reason') or '').split())[:200]
    if len(reason) < 3:
        raise ValueError('Bitte den Grund angeben, z. B. „Beteiligung gemeinsames Sommerfest“.')
    account = data.get('account') or 'KV'
    ac = store.db.execute('SELECT kind FROM accounts WHERE code=?', (account,)).fetchone()
    if not ac or ac['kind'] != 'expense':
        raise ValueError('Bitte einen Ausgaben-Haushaltsposten wählen.')
    if account == 'KV' and not (src in haushalt.KV_ROWS and dst in haushalt.KV_ROWS):
        raise ValueError('Mit dem Landesverband bitte den Haushaltsposten der Kosten wählen (z. B. 4473 Sommerfest), nicht das Sammelkonto KV.')
    entries = []
    for kv, debit, credit in ((dst, amount, 0), (src, 0, amount)):
        title = str(data.get('title') or '').strip() or haushalt.default_title(account, kv)
        if account == 'KV':
            title = haushalt.default_title(account, kv)                      # je Kreisverband seine Zeile
        if title not in haushalt.TITLES or haushalt.TITLES[title]['kind'] != 'expense':
            raise ValueError('Bitte den Haushaltstitel angeben.')
        entries.append({'account': account, 'kv': kv, 'debit': debit, 'credit': credit, 'title': title})
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        bill = None
        if data.get('bill_id'):
            bill = store.db.execute("SELECT id,number,creditor FROM bills WHERE id=? AND status!='cancelled'", (int(data['bill_id']),)).fetchone()
            if not bill:
                raise ValueError('Eingangsrechnung nicht gefunden oder storniert.')
        n = store.db.execute("SELECT COUNT(*) FROM journals WHERE source LIKE ? AND reversal_of IS NULL", (TRANSFER + '%',)).fetchone()[0] + 1
        text = f'Budget-Umbuchung: {names[dst]} beteiligt sich an Kosten von {names[src]} – {reason}'
        if bill:
            text += f' (Eingangsrechnung {bill["number"]}, {bill["creditor"]})'
        jid = store.journal(day, text, f'{TRANSFER}{n}', entries)
        store.audit(actor, 'Budget umgebucht', {'id': jid, 'von': src, 'an': dst, 'betrag': amount, 'konto': account, 'grund': reason,
                                                'eingangsrechnung': bill['number'] if bill else None})
    return jid


def transfers(store, year=None):
    """Umbuchungen (je Haushaltsjahr) mit Storno-Kennzeichen."""
    rows = store.rows("SELECT id,day,year,description,reversal_of FROM journals WHERE source LIKE ? ORDER BY day DESC,id DESC", (TRANSFER + '%',))
    reversed_ids = {r['reversal_of'] for r in rows if r['reversal_of']}
    out = []
    for r in rows:
        if r['reversal_of'] or (year and (r['year'] or int(r['day'][:4])) != int(year)):
            continue
        es = store.rows('SELECT account,kv,debit,credit FROM entries WHERE journal_id=?', (r['id'],))
        to = next(e for e in es if e['debit'])
        fr = next(e for e in es if e['credit'])
        out.append({'id': r['id'], 'day': r['day'], 'description': r['description'], 'from': fr['kv'], 'to': to['kv'],
                    'amount': to['debit'], 'account': to['account'], 'reversed': r['id'] in reversed_ids})
    return out


def reverse_transfer(store, jid, reason, actor):
    """Storno einer Umbuchung: Gegenbuchung im selben Haushaltsjahr; beide zählen danach nicht mehr im Budget."""
    reason = ' '.join(str(reason or '').split())[:200]
    if len(reason) < 3:
        raise ValueError('Bitte den Grund für das Storno angeben.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        j = store.db.execute('SELECT * FROM journals WHERE id=? AND source LIKE ? AND reversal_of IS NULL', (jid, TRANSFER + '%')).fetchone()
        if not j:
            raise ValueError('Umbuchung nicht gefunden.')
        if store.db.execute('SELECT 1 FROM journals WHERE reversal_of=?', (jid,)).fetchone():
            raise ValueError('Diese Umbuchung ist bereits storniert.')
        entries = store.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?', (jid,))
        store.journal(date.today().isoformat(), f'Storno Budget-Umbuchung #{jid}: {reason}', j['source'] + '/storno', entries,
                      reversal=jid, year=j['year'] or int(j['day'][:4]))
        store.audit(actor, 'Budget-Umbuchung storniert', {'id': jid, 'grund': reason})


# ---------------------------------------------------------------- Spenden

def donations(store, year=None):
    """Spenden laut Journal (Konten 8200, 8202, 1371) mit Anteil für das Budget des Bereichs (ohne Storno-Paare):
    8200 mit Bereich = Kreisverband bzw. Schüler Union ganz; Freundes- und Förderkreis (8202) nur über den Jahresbetrag je KV.
    until = Verfall mit dem Jahresrest am 31.01. des Folgejahres (Schüler Union: kein Verfall)."""
    skip, begin = _excluded_journals(store), start(store)
    sql = ("SELECT j.id,j.day,j.description,e.account,e.kv,a.name account_name,SUM(e.credit-e.debit) amount FROM entries e "
           "JOIN journals j ON j.id=e.journal_id JOIN accounts a ON a.code=e.account WHERE e.account IN ('8200','8202','1371')"
           + (" AND substr(j.day,1,4)=?" if year else '') + ' GROUP BY j.id,e.account,e.kv ORDER BY j.day,j.id')
    out = []
    for r in store.rows(sql, (str(year),) if year else ()):
        if r['id'] in skip or r['amount'] <= 0:
            continue
        share = r['amount'] if r['account'] == DONATION_ACCOUNT and r['kv'] != 'LV' and r['day'] >= begin else 0
        r['kv_share'] = share
        r['until'] = f"{int(r['day'][:4]) + 1}-01-31" if share and r['kv'] not in NO_LAPSE else None
        out.append(r)
    return out


def _cash_values(store, data):
    received = iso(data.get('received_on') or '')
    if received > date.today().isoformat():
        raise ValueError('Datum der Spende liegt in der Zukunft.')
    donor = ' '.join(str(data.get('donor') or '').split())[:120]
    if not donor:
        raise ValueError('Bitte den Spender angeben.')
    amount = money(data.get('amount') or '')
    if amount <= 0:
        raise ValueError('Bitte einen Betrag angeben.')
    kv = data.get('kv') or 'LV'
    if not store.db.execute('SELECT 1 FROM kv WHERE code=?', (kv,)).fetchone():
        raise ValueError('Bitte einen Bereich wählen.')
    r1, r2 = (' '.join(str(data.get(k) or '').split())[:120] for k in ('receiver1', 'receiver2'))
    if not r1 or not r2 or r1.casefold() == r2.casefold():
        raise ValueError('§ 2 Abs. 5: Barspenden nehmen zwei verschiedene Vertreter des geschäftsführenden Landesvorstands entgegen.')
    receipt = ' '.join(str(data.get('receipt') or '').split())[:60]
    if not receipt:
        raise ValueError('§ 2 Abs. 5: Barspenden nur gegen Quittung – bitte die Quittungsnummer angeben.')
    out = {'received_on': received, 'donor': donor, 'amount': amount, 'kv': kv, 'receiver1': r1, 'receiver2': r2, 'receipt': receipt,
           'note': str(data.get('note') or '').strip()[:300] or None}
    for key in ('deposited_on', 'cdu_informed_on'):
        v = str(data.get(key) or '').strip()
        out[key] = iso(v) if v else None
        if out[key] and not received <= out[key] <= date.today().isoformat():
            raise ValueError('Datum der Einzahlung bzw. Information der CDU: nach der Spende und nicht in der Zukunft.')
    return out


def save_cash(store, data, actor, cid=None):
    v = _cash_values(store, data)
    new = cid is None
    with store.db:
        before = None
        if cid:
            before = store.db.execute('SELECT * FROM cash_donations WHERE id=?', (cid,)).fetchone()
            if not before:
                raise ValueError('Barspende nicht gefunden.')
            store.db.execute('UPDATE cash_donations SET ' + ','.join(f'{k}=?' for k in v) + ' WHERE id=?', [*v.values(), cid])
        else:
            cid = store.db.execute('INSERT INTO cash_donations(' + ','.join(v) + ',created,actor) VALUES(' + ','.join('?' * (len(v) + 2)) + ')',
                                   [*v.values(), datetime.now().isoformat(timespec='seconds'), actor]).lastrowid
        changed = {k: [before[k], v[k]] for k in v if before and before[k] != v[k]}
        store.audit(actor, 'Barspende erfasst' if new else 'Barspende geändert',
                    {'id': cid, 'cents': v['amount'], 'receipt': v['receipt'], **({'changes': changed} if changed else {})})
    return cid


def cash_list(store, today=None):
    today = today or date.today().isoformat()
    out = []
    for r in store.rows('SELECT * FROM cash_donations ORDER BY received_on DESC,id DESC'):
        deadline = (date.fromisoformat(r['received_on']) + timedelta(days=CASH_DAYS)).isoformat()
        issues = []
        if not r['deposited_on'] and today > deadline:
            issues.append(f'Einzahlung überfällig (bis {german_date(deadline)})')
        elif r['deposited_on'] and r['deposited_on'] > deadline:
            issues.append('später als 30 Tage eingezahlt')
        elif not r['deposited_on']:
            issues.append(f'einzahlen bis {german_date(deadline)}')
        if not r['cdu_informed_on']:
            issues.append('CDU-Buchhaltung noch nicht informiert')
        r.update(deadline=deadline, issues=issues, overdue=bool(not r['deposited_on'] and today > deadline))
        out.append(r)
    return out
