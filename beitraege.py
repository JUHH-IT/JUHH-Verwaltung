"""Mitgliedsbeiträge ohne Beitragsrechnung (Konto 8100) einem Mitglied zuordnen.

Wunsch Lennert Gust 29.10.2026: Jeder Zugang auf 8100 wird einem Mitglied zugeordnet (Suche nach Kreisverband, Name,
Anschrift); Pflicht bei neuen Buchungen. Bereits gebuchte 8100-Zeilen (z. B. aus der Excel-Übernahme) werden über die
Liste „Beiträge zuordnen“ nachgetragen. Grundlage für den 25-%-Anteil des Kreisverbands (FO § 6) sind nur die tatsächlich
gezahlten Beiträge (Ist), nicht die Beitragsrechnungen (Soll).

Die Zuordnung ändert keine Buchung: sie hält je Buchungszeile fest, für welches Mitglied (mit dessen Kreisverband zum
Zeitpunkt der Zuordnung) und für welches Beitragsjahr (Haushaltsjahr der Buchung) gezahlt wurde. Eine Sammelzahlung
lässt sich auf mehrere Mitglieder aufteilen; die Beträge ergeben immer genau den Betrag der Buchungszeile.
Neue Bankbuchungen erzeugen je Mitglied eine eigene 8100-Zeile mit dessen Kreisverband als Bereich.
Stornierte Buchungen zählen nicht (kvbudget._excluded_journals).

Zahlungen von Personen, die nicht (mehr) in der Mitgliederliste stehen – z. B. ausgetretene Mitglieder, deren Daten gelöscht
sind (Wunsch Lennert Gust 10.11.2026) –, werden mit Name, Mitgliedsnummer (falls bekannt), Kreisverband und Grund in
`dues_external` festgehalten. Sie zählen wie Mitgliederzahlungen für den Haushalt und den Anteil des Kreisverbands, haben aber
kein Beitragskonto. Auch Rückzahlungen und Rücklastschriften (negativer Betrag) lassen sich so zuordnen.

Ab 0.31 (klaerfaelle.py): Ist unklar, zu wem eine Zahlung gehört, wird sie (oder ein Teil) als Klärfall an die Mitgliederverwaltung
gemeldet – Eingabe `{case: {name, info, reason}, amount}`. Der Betrag bleibt auf 8100 ohne Mitglied und ohne Kreisverband, bis der
Klärfall geklärt ist (`entries`: `in_case`; `open` ist nur noch, was weder zugeordnet noch gemeldet ist; `pending` = beides).
Jede Zuordnung „nicht in der Mitgliederliste“ erzeugt ebenfalls einen Klärfall zum Prüfen (zählt aber schon).
"""
import re
import unicodedata
from datetime import datetime

from formate import money

ACCOUNT = '8100'
MAX_MEMBERS = 5000   # auch SEPA-Sammler mit allen Mitgliedern (sepadatei.py)
BANK_SOURCES = ('bank', 'resolve')     # nur Zahlungen (Ist) – nicht die Sollbuchung einer Beitragsrechnung


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS dues_assignments(id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL REFERENCES entries(id),
        member_id INTEGER NOT NULL REFERENCES members(id),kv TEXT NOT NULL REFERENCES kv(code),year INTEGER NOT NULL,
        amount INTEGER NOT NULL CHECK(amount!=0),created TEXT NOT NULL,actor TEXT);
    CREATE INDEX IF NOT EXISTS dues_assignments_entry ON dues_assignments(entry_id);
    CREATE INDEX IF NOT EXISTS dues_assignments_member ON dues_assignments(member_id);
    CREATE TABLE IF NOT EXISTS dues_external(id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL REFERENCES entries(id),
        name TEXT NOT NULL,number TEXT NOT NULL DEFAULT '',kv TEXT NOT NULL REFERENCES kv(code),year INTEGER NOT NULL,
        amount INTEGER NOT NULL CHECK(amount!=0),reason TEXT NOT NULL,created TEXT NOT NULL,actor TEXT);
    CREATE INDEX IF NOT EXISTS dues_external_entry ON dues_external(entry_id);
    ''')
    db.commit()


EXTERNAL_REASON = 'ausgetreten, nicht mehr in der Mitgliederliste'


def number_key(number):
    """Mitgliedsnummer zum Vergleichen: nur die Ziffernfolgen ohne führende Nullen (0012345 = 12345 = JU-12345 ≠ 12346);
    ohne Ziffern die Buchstaben. Im Zweifel gilt eine Nummer als vergeben – dann ist das Mitglied zu wählen."""
    digits = re.findall(r'[0-9]+', str(number or ''))
    return '-'.join(d.lstrip('0') or '0' for d in digits) if digits else re.sub(r'[^0-9a-z]', '', _fold(number))


def external(store, raw):
    """Person ohne Eintrag in der Mitgliederliste (z. B. ausgetreten, Daten gelöscht): Name, Nummer (falls bekannt), Kreisverband, Grund."""
    if not isinstance(raw, dict):
        raise ValueError('Angaben zur Person fehlen.')
    import klaerfaelle
    text = lambda key: raw.get(key) if isinstance(raw.get(key), str) else ''   # noqa: E731 – keine Listen, Zahlen oder Wahrheitswerte als Text
    name = ' '.join(text('name').split())
    number = str(raw['number']) if isinstance(raw.get('number'), int) and not isinstance(raw.get('number'), bool) else text('number').strip()
    reason = ' '.join(text('reason').split())
    kv = text('kv').strip()
    if not name:
        raise ValueError('Nicht in der Mitgliederliste: bitte den Namen angeben.')
    if not reason:
        raise ValueError('Nicht in der Mitgliederliste: bitte den Grund angeben (z. B. ausgetreten).')
    if len(name) > 120 or len(number) > 30 or len(reason) > 200:
        raise ValueError('Angaben zur Person sind zu lang (Name höchstens 120, Nummer 30, Grund 200 Zeichen).')
    if not store.db.execute('SELECT 1 FROM kv WHERE code=?', (kv,)).fetchone():
        raise ValueError('Bitte den Kreisverband wählen (ohne Kreisverband: Landesverband).')
    key = number_key(number)
    if key and any(number_key(r[0]) == key for r in store.db.execute('SELECT number FROM members')):
        raise ValueError(f'Die Mitgliedsnummer {number} gehört zu einem Mitglied der Liste – bitte das Mitglied auswählen.')
    # Herkunft „SEPA-Datei“ nur aus der vom Programm gelesenen Datei (sepadatei.apply), nie aus der Anfrage
    return {'id': None, 'kv': kv, 'name': name, 'number': number, 'reason': reason,
            'origin': raw.get('origin') if isinstance(raw, klaerfaelle.FromFile) and isinstance(raw.get('origin'), str) else ''}


def _members(store, items, total):
    """Prüft die Aufteilung [{member_id, amount}], [{external: {name, number, kv, reason}, amount}] (Person nicht in der
    Mitgliederliste) bzw. [{case: {name, info, reason}, amount}] (Klärfall für die Mitgliederverwaltung, klaerfaelle.clean);
    amount darf bei nur einer Person fehlen."""
    import klaerfaelle
    items = list(items or [])
    if not items:
        raise ValueError('Mitgliedsbeitrag (8100): bitte das Mitglied zuordnen.')
    if len(items) > MAX_MEMBERS:
        raise ValueError(f'Höchstens {MAX_MEMBERS} Mitglieder je Zahlung.')
    out, seen = [], set()
    for n, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError('Mitglied fehlt.')
        if item.get('case') is not None:
            m = klaerfaelle.clean(item['case'])
            key = ('fall', n)                               # mehrere Klärfälle je Zahlung möglich (verschiedene Zahler)
        elif item.get('external') is not None:
            m = external(store, item['external'])
            key = ('extern', _fold(m['name']), m['number'])
        else:
            try:
                key = int(item.get('member_id'))
            except (TypeError, ValueError):
                raise ValueError('Mitglied fehlt.') from None
            m = None
        if key in seen:
            raise ValueError('Ein Mitglied ist doppelt zugeordnet.')
        seen.add(key)
        raw = item.get('amount')
        cents = total if len(items) == 1 and (raw is None or str(raw).strip() == '') else money(raw)
        if not cents or (cents > 0) != (total > 0):
            raise ValueError('Beträge je Mitglied: ungleich 0 und mit derselben Richtung wie die Zahlung.')
        if m is None:
            m = store.db.execute('SELECT id,kv,name,number FROM members WHERE id=?', (key,)).fetchone()
            if not m:
                raise ValueError('Mitglied nicht gefunden.')
            m = dict(m)
        out.append({'member': m, 'amount': cents, 'external': m if m['id'] is None and not m.get('case') else None, 'case': m.get('case')})
    if sum(x['amount'] for x in out) != total:
        raise ValueError('Die Beträge der Mitglieder ergeben nicht den Betrag der Zahlung.')
    return out


def split_entries(store, part, amount, title):
    """Buchungszeilen einer Beitragszahlung aus dem Kontoauszug: je Mitglied eine 8100-Zeile (Bereich = Kreisverband)."""
    return [{'account': ACCOUNT, 'kv': x['member']['kv'], 'debit': max(-x['amount'], 0), 'credit': max(x['amount'], 0), 'title': title,
             'member_id': x['member']['id'], **({'external': x['external']} if x['external'] else {}), **({'case': x['case']} if x['case'] else {})}
            for x in _members(store, part.get('members'), amount)]


def audit_parts(parts):
    """Teilbeträge einer Buchung für das Änderungsprotokoll: Personen außerhalb der Mitgliederliste ohne Namen (nur Nummer),
    große Sammelzahlungen nur als Anzahl (Prüfbefund 0.29)."""
    out = []
    for p in parts if isinstance(parts, list) else []:
        members = p.get('members') if isinstance(p, dict) else None
        if not members or not isinstance(members, list):
            out.append(p)
            continue
        q = {k: v for k, v in p.items() if k != 'members'}
        ext = lambda m: isinstance(m, dict) and m.get('external') is not None   # noqa: E731
        case = lambda m: isinstance(m, dict) and m.get('case') is not None       # noqa: E731
        if len(members) > 50:
            q['mitglieder'] = {'anzahl': len(members), 'nicht_in_liste': sum(1 for m in members if ext(m)), 'klaerfaelle': sum(1 for m in members if case(m))}
        else:
            q['members'] = [{'id': 'klaerfall', 'amount': m.get('amount')} if case(m) else
                            {'id': 'extern', 'nummer': str((m['external'] if isinstance(m['external'], dict) else {}).get('number') or ''),
                             'amount': m.get('amount')} if ext(m) else m for m in members]
        out.append(q)
    return out


def _insert(store, entry_id, x, year, amount, now, actor, reuse=None):
    """Eine Zuordnung speichern: Mitglied (dues_assignments), Klärfall ohne Zuordnung (dues_cases) oder Person ohne Eintrag in der
    Mitgliederliste (dues_external, dazu ein Klärfall zum Prüfen – reuse: bisherige Klärfälle beim Ersetzen einer Zuordnung)."""
    import klaerfaelle
    if x.get('id'):
        store.db.execute('INSERT INTO dues_assignments(entry_id,member_id,kv,year,amount,created,actor) VALUES(?,?,?,?,?,?,?)',
                         (entry_id, x['id'], x['kv'], int(year), amount, now, actor))
    elif x.get('case'):
        klaerfaelle.insert(store, entry_id, x['case'], year, amount, now, actor)
    else:
        xid = store.db.execute('INSERT INTO dues_external(entry_id,name,number,kv,year,amount,reason,created,actor) VALUES(?,?,?,?,?,?,?,?,?)',
                               (entry_id, x['name'], x['number'], x['kv'], int(year), amount, x['reason'], now, actor)).lastrowid
        klaerfaelle.link_external(store, entry_id, xid, x, year, amount, now, actor, reuse)


def attach(store, entries, year, actor):
    """Nach dem Buchen: Zuordnung der 8100-Zeilen festhalten (entry_id setzt Store.journal)."""
    now = datetime.now().isoformat(timespec='seconds')
    for e in entries:
        if e.get('member_id'):
            _insert(store, e['entry_id'], {'id': e['member_id'], 'kv': e['kv']}, year, e['credit'] - e['debit'], now, actor)
        elif e.get('external'):
            _insert(store, e['entry_id'], e['external'], year, e['credit'] - e['debit'], now, actor)
        elif e.get('case'):
            _insert(store, e['entry_id'], {'case': e['case']}, year, e['credit'] - e['debit'], now, actor)


def _skip(store):
    import kvbudget
    return kvbudget._excluded_journals(store)


def _bank_id(source):
    m = re.match(r'^(bank|resolve):(\d+)(/\d+)?$', str(source or ''))
    return int(m.group(2)) if m else None


def entries(store, year=None, only_open=False):
    """8100-Zahlungen aus dem Kontoauszug mit Zuordnung (stornierte Buchungen nicht). Je Zahlung: members (zugeordnet), cases und
    in_case (als Klärfall bei der Mitgliederverwaltung), open (weder zugeordnet noch gemeldet – Aufgabe der Buchhaltung) und
    returned (von der Mitgliederverwaltung zurück: kein Mitgliedsbeitrag). only_open: nur Zahlungen mit open."""
    skip = _skip(store)
    sql = ("SELECT e.id,e.journal_id,e.kv,e.credit-e.debit amount,j.day,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) year,j.source,"
           "j.description FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account=? AND (j.source LIKE 'bank:%' OR j.source LIKE 'resolve:%')"
           + (' AND COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER))=?' if year else '') + ' ORDER BY j.day,e.id')
    rows = [r for r in store.rows(sql, (ACCOUNT, int(year)) if year else (ACCOUNT,)) if r['journal_id'] not in skip and r['amount']]
    assigned = {}
    for a in store.rows('SELECT a.*,m.name,m.number,m.first_name,m.last_name FROM dues_assignments a JOIN members m ON m.id=a.member_id ORDER BY a.id'):
        assigned.setdefault(a['entry_id'], []).append(a)
    for a in store.rows('SELECT * FROM dues_external ORDER BY id'):
        assigned.setdefault(a['entry_id'], []).append({**a, 'member_id': None, 'external': True})
    cases, returned = {}, {}
    for c in store.rows("SELECT id,entry_id,amount,name,status,reason,note,created FROM dues_cases WHERE external_id IS NULL AND status!='geklaert' ORDER BY id"):
        (returned if c['status'] == 'zurueck' else cases).setdefault(c['entry_id'], []).append(c)
    banks = {}
    ids = sorted({_bank_id(r['source']) for r in rows} - {None})
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        banks.update({b['id']: b for b in store.rows(f"SELECT id,name,reference,booked,amount FROM bank WHERE id IN ({','.join('?' * len(chunk))})", chunk)})
    out = []
    for r in rows:
        mine = assigned.get(r['id'], [])
        done = sum(a['amount'] for a in mine)
        waiting = cases.get(r['id'], [])
        in_case = sum(c['amount'] for c in waiting)
        bank = banks.get(_bank_id(r['source'])) or {}
        item = {**r, 'payer': bank.get('name') or '', 'reference': bank.get('reference') or r['description'] or '', 'bank_id': bank.get('id'),
                'members': [{'member_id': a['member_id'], 'name': a['name'], 'number': a['number'], 'kv': a['kv'], 'amount': a['amount'],
                             **({'external': {'name': a['name'], 'number': a['number'], 'kv': a['kv'], 'reason': a['reason']}} if a.get('external') else {})}
                            for a in mine],
                'cases': [{'id': c['id'], 'number': f"K-{c['created'][:4]}-{c['id']:04d}", 'name': c['name'], 'amount': c['amount'], 'status': c['status']}
                          for c in waiting],
                'in_case': in_case, 'open': r['amount'] - done - in_case}
        item['returned'] = [{'name': c['name'], 'note': c['note']} for c in returned.get(r['id'], [])] if item['open'] else []
        if not only_open or item['open']:
            out.append(item)
    return out


def pending(store, year=None):
    """Zahlungen, die noch nicht vollständig einem Mitglied (bzw. einer Person außerhalb der Liste) zugeordnet sind – offen bei der
    Buchhaltung oder als Klärfall bei der Mitgliederverwaltung. Sperrt den KV-Budgetabschluss des Jahres."""
    return [x for x in entries(store, year) if x['open'] or x['in_case']]


def _fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').casefold().replace('ß', 'ss'))
    return ''.join(c for c in text if not unicodedata.combining(c))


def _index(members):
    """Mitglieder für die Vorschläge vorbereiten (einmal je Liste statt je Zahlung – bei 2000 Mitgliedern und 250 Zahlungen wichtig)."""
    by_word, numbers, odd = {}, {}, []
    for m in members:
        last = re.findall(r'[a-z0-9]+', _fold(m.get('last_name') or str(m['name']).split(' ')[-1]))
        first = set(re.findall(r'[a-z0-9]+', _fold(m.get('first_name') or ' '.join(str(m['name']).split(' ')[:-1]))))
        entry = (m['id'], last, first)
        if last:
            by_word.setdefault(last[0], []).append(entry)
        number = _fold(m['number'])
        if len(number) >= 3:
            if re.fullmatch(r'[a-z0-9]+', number):
                numbers.setdefault(number, []).append(m['id'])
            else:
                odd.append((m['id'], number))             # Nummer mit Trennzeichen (z. B. JU-1001): wie bisher als ganzes Wort suchen
    return {'by_word': by_word, 'numbers': numbers, 'odd': odd}


def suggest(store, entry, members=None, index=None):
    """Mitglieder, deren Name (bzw. Mitgliedsnummer) im Namen oder Verwendungszweck der Zahlung steht – stärkste zuerst."""
    if index is None:
        index = _index(members if members is not None else store.rows('SELECT id,number,name,first_name,last_name,kv FROM members'))
    text = _fold(f"{entry.get('payer', '')} {entry.get('reference', '')}")
    words = set(re.findall(r'[a-z0-9]+', text))
    score, named = {}, set()
    for w in words:
        for mid in index['numbers'].get(w, ()):             # ganze Nummer, nicht Teil einer längeren
            score[mid] = score.get(mid, 0) + 5
        for mid, last, first in index['by_word'].get(w, ()):
            if all(x in words for x in last):
                score[mid] = score.get(mid, 0) + 2 + (1 if first & words else 0)
                named.add(mid)
    for mid, number in index['odd']:
        if re.search(rf'(?<![a-z0-9]){re.escape(number)}(?![a-z0-9])', text):
            score[mid] = score.get(mid, 0) + 5
    out = [{'member_id': mid, 'score': v, 'name': mid in named} for mid, v in score.items() if v >= 3]
    return sorted(out, key=lambda x: (-x['score'], x['member_id']))[:3]


def _payer(text):
    return ' '.join(re.findall(r'[a-z0-9]+', _fold(text)))


# Zahler, die für viele zahlen (Zahlungsdienste, Verbände): nie als „gleicher Zahler“ vorschlagen
COLLECTIVE = {'paypal', 'stripe', 'klarna', 'sumup', 'adyen', 'mollie', 'cdu', 'ju', 'union', 'kreisverband', 'landesverband', 'ortsverband',
              'verein', 'gmbh', 'sparkasse'}


def learned(store, items=None):
    """Zahler (Name laut Kontoauszug), deren Zahlungen bisher genau einem Mitglied zugeordnet sind – z. B. Eltern, die für ihr Kind
    zahlen (Satzung § 49 Abs. 3). Nur als Einzelvorschlag, nie für die Sammelübernahme: zahlt dieselbe Person für Geschwister, wäre er falsch."""
    seen = {}
    for x in items if items is not None else entries(store):
        key = _payer(x['payer'])
        if len(key) >= 5 and x['members'] and not any(w in key.split() for w in COLLECTIVE):
            seen.setdefault(key, set()).update(a['member_id'] or 'extern' for a in x['members'])   # zahlte auch für Externe: nie lernen
            if x['cases']:
                seen[key].add('extern')                    # ein Teil ist noch Klärfall: ebenfalls nicht lernen
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1 and 'extern' not in v}


def open_list(store, year=None):
    """Liste „Beiträge zuordnen“: offene 8100-Zahlungen mit Vorschlägen nach Name bzw. Mitgliedsnummer, sonst nach früherer
    Zuordnung desselben Zahlers."""
    index = _index(store.rows('SELECT id,number,name,first_name,last_name,kv FROM members'))
    items = entries(store, year, only_open=True)
    known = learned(store) if items else {}
    for item in items:
        item['suggestions'] = suggest(store, item, index=index) if item['open'] == item['amount'] else []
        mid = known.get(_payer(item['payer'])) if item['open'] == item['amount'] else None
        if not item['suggestions'] and mid:
            item['suggestions'] = [{'member_id': mid, 'score': 3, 'name': False, 'learned': True}]
    return items


def assign(store, entry_id, items, actor):
    """Bereits gebuchte 8100-Zeile (z. B. Excel-Übernahme) Mitgliedern zuordnen; ersetzt eine frühere Zuordnung. Ein Teil, der als
    Klärfall bei der Mitgliederverwaltung liegt, bleibt unberührt – verteilt wird der Rest."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        assign_in_transaction(store, entry_id, items, actor)


def assign_in_transaction(store, entry_id, items, actor, replace_cases=False):
    """Wie assign, innerhalb einer schon offenen Transaktion des Aufrufers (z. B. SEPA-Sammler aufteilen; replace_cases: die Zeile
    wird vollständig neu aufgeteilt, offene Klärfälle ohne Zuordnung sind damit erledigt)."""
    import klaerfaelle
    e = store.db.execute('SELECT e.id,e.account,e.kv,e.credit-e.debit amount,e.journal_id,j.source,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) year '
                         'FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.id=?', (int(entry_id),)).fetchone()
    if not e or e['account'] != ACCOUNT or str(e['source']).split(':')[0] not in BANK_SOURCES:
        raise ValueError('Nur Zahlungen auf 8100 aus dem Kontoauszug lassen sich einem Mitglied zuordnen.')
    if e['journal_id'] in _skip(store):
        raise ValueError('Diese Buchung ist storniert.')
    now = datetime.now().isoformat(timespec='seconds')
    if replace_cases:
        klaerfaelle.close_entry_cases(store, e['id'], now, actor)
    in_case = store.db.execute("SELECT COALESCE(SUM(amount),0) FROM dues_cases WHERE entry_id=? AND external_id IS NULL AND status IN ('offen','rueckfrage')",
                               (e['id'],)).fetchone()[0]
    if in_case == e['amount']:
        raise ValueError('Diese Zahlung liegt als Klärfall bei der Mitgliederverwaltung – dort wird sie geklärt (Mitgliederverwaltung → Klärfälle).')
    chosen = _members(store, items, e['amount'] - in_case)
    other = [x for x in chosen if not x['case'] and _kv_conflict(e['kv'], x['member']['kv'])]
    if other:   # Buchungszeile trägt schon einen Kreisverband (beim Buchen je Mitglied aufgeteilt): Bereich und Anteil müssen passen
        raise ValueError('Diese Zahlung ist für einen anderen Kreisverband gebucht. Für ein Mitglied eines anderen Kreisverbands '
                         'die Buchung im Buchungsplatz stornieren und neu verbuchen.')
    before = (store.rows('SELECT member_id id,amount cents FROM dues_assignments WHERE entry_id=?', (e['id'],))
              + store.rows("SELECT 'extern' id,number nummer,amount cents FROM dues_external WHERE entry_id=?", (e['id'],)))
    klaerfaelle.acknowledge_returned(store, e['id'])       # „kein Mitgliedsbeitrag“ der Mitgliederverwaltung: jetzt neu zugeordnet
    reuse = klaerfaelle.detach_externals(store, e['id'])   # Klärfälle der bisherigen Personen außerhalb der Liste
    store.db.execute('DELETE FROM dues_assignments WHERE entry_id=?', (e['id'],))
    store.db.execute('DELETE FROM dues_external WHERE entry_id=?', (e['id'],))
    for x in chosen:
        _insert(store, e['id'], x['member'], e['year'], x['amount'], now, actor, reuse)
    klaerfaelle.close_detached(store, reuse, now, actor)
    store.audit(actor, 'Mitgliedsbeitrag zugeordnet' if not before else 'Beitragszuordnung geändert',
                {'entry': e['id'], 'members': [{'id': x['member']['id'], 'cents': x['amount']} if x['member']['id'] else
                                               {'id': 'klaerfall', 'cents': x['amount']} if x['case'] else
                                               {'id': 'extern', 'nummer': x['member']['number'], 'cents': x['amount']} for x in chosen],
                 **({'vorher': before} if before else {})})


def unique_suggestion(item):
    """Eindeutiger Vorschlag für die Sammelübernahme: genau ein Mitglied mit der höchsten Punktzahl, dessen Nachname in der
    Zahlung steht (die Mitgliedsnummer allein genügt nicht)."""
    sug = item.get('suggestions') or []
    if not sug or (len(sug) > 1 and sug[1]['score'] == sug[0]['score']) or not sug[0].get('name'):
        return None
    return sug[0]['member_id']


def _kv_conflict(entry_kv, member_kv):
    """Buchungszeile trägt schon einen Kreisverband (beim Buchen je Mitglied aufgeteilt): Mitglied muss dazu passen."""
    return entry_kv != 'LV' and member_kv != entry_kv


def assign_suggested(store, year=None, actor=''):
    """Alle offenen Zahlungen des Jahres mit eindeutigem Vorschlag zuordnen (ganzer Betrag an ein Mitglied).
    Für die erste Zuordnung nach dem Import der Mitgliederliste; jede Zuordnung bleibt einzeln änderbar."""
    items = [x for x in open_list(store, year) if unique_suggestion(x)]
    now = datetime.now().isoformat(timespec='seconds')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        done = []
        for x in items:
            if store.db.execute("SELECT 1 FROM dues_assignments WHERE entry_id=:e UNION ALL SELECT 1 FROM dues_external WHERE entry_id=:e UNION ALL "
                                "SELECT 1 FROM dues_cases WHERE entry_id=:e AND external_id IS NULL AND status IN ('offen','rueckfrage')",
                                {'e': x['id']}).fetchone():
                continue                                        # inzwischen zugeordnet
            m = store.db.execute('SELECT id,kv FROM members WHERE id=?', (unique_suggestion(x),)).fetchone()
            if _kv_conflict(x['kv'], m['kv']):
                continue                                        # Bereich der Buchung passt nicht: einzeln klären
            store.db.execute('INSERT INTO dues_assignments(entry_id,member_id,kv,year,amount,created,actor) VALUES(?,?,?,?,?,?,?)',
                             (x['id'], m['id'], m['kv'], x['year'], x['amount'], now, actor))
            done.append({'entry': x['id'], 'member': m['id'], 'cents': x['amount']})
        if done:
            store.audit(actor, 'Beiträge nach Vorschlag zugeordnet', {'jahr': year, 'anzahl': len(done), 'zuordnungen': done})
    return len(done)


COUNTED = "substr(j.day,1,4)=CAST(a.year AS TEXT)"   # wie bei Beitragsrechnungen: nur Zahlungen im Beitragsjahr zählen


def paid_by_kv(store, kv, until=None):
    """Zugeordnete Beitragszahlungen eines Kreisverbands je Beitragsjahr (Ist, ohne Stornos); until: Zahlungen bis zu diesem Tag.
    Wie bei Beitragsrechnungen zählt eine Zahlung nur im eigenen Jahr (Beitragsjahr = Zahlungsjahr); abweichende stehen unter
    „Jahreszuordnung klären“ (year_mismatch)."""
    skip = _skip(store)
    out = {}
    for table in ('dues_assignments', 'dues_external'):    # Mitglieder und Personen, die nicht (mehr) in der Liste stehen
        for r in store.rows(f'SELECT a.year,a.amount,e.journal_id FROM {table} a JOIN entries e ON e.id=a.entry_id JOIN journals j ON j.id=e.journal_id '
                            f'WHERE a.kv=? AND j.day<=? AND {COUNTED}', (kv, until or '9999-12-31')):
            if r['journal_id'] not in skip:
                out[r['year']] = out.get(r['year'], 0) + r['amount']
    return out


def year_mismatch(store):
    """Zugeordnete Zahlungen, deren Beitragsjahr (Haushaltsjahr der Buchung) nicht dem Zahlungsjahr entspricht – zählen nicht."""
    skip = _skip(store)
    rows = store.rows('SELECT m.number,j.day booked,a.amount,a.year,e.journal_id FROM dues_assignments a JOIN entries e ON e.id=a.entry_id '
                      f'JOIN journals j ON j.id=e.journal_id JOIN members m ON m.id=a.member_id WHERE NOT ({COUNTED})')
    rows += [{**r, 'number': (r['number'] or 'ohne Nummer') + ', nicht in der Mitgliederliste'} for r in store.rows(
        'SELECT a.number,j.day booked,a.amount,a.year,e.journal_id FROM dues_external a JOIN entries e ON e.id=a.entry_id '
        f'JOIN journals j ON j.id=e.journal_id WHERE NOT ({COUNTED})')]
    return sorted((r for r in rows if r['journal_id'] not in skip), key=lambda r: r['booked'])


def by_member(store, member_id):
    """Beitragszahlungen eines Mitglieds ohne Beitragsrechnung (für das Beitragskonto)."""
    skip = _skip(store)
    return [r for r in store.rows('SELECT a.id,a.year,a.amount,a.kv,j.day,j.description,e.journal_id FROM dues_assignments a JOIN entries e ON e.id=a.entry_id '
                                  'JOIN journals j ON j.id=e.journal_id WHERE a.member_id=? ORDER BY j.day DESC,a.id DESC', (int(member_id),))
            if r['journal_id'] not in skip]


def summary(store, year):
    """Je Mitglied gezahlte Beiträge eines Jahres (Rechnungszahlungen und zugeordnete 8100-Zahlungen) – für Suche und Liste."""
    skip = _skip(store)
    out = {}
    for r in store.rows('SELECT a.member_id,a.amount,e.journal_id FROM dues_assignments a JOIN entries e ON e.id=a.entry_id WHERE a.year=?', (int(year),)):
        if r['journal_id'] not in skip:
            out[r['member_id']] = out.get(r['member_id'], 0) + r['amount']
    for r in store.rows("SELECT i.member_id,SUM(p.amount) paid FROM payments p JOIN invoices i ON i.id=p.invoice_id JOIN bank b ON b.id=p.bank_id "
                        "WHERE i.kind='dues' AND i.year=? AND b.state='booked' AND i.member_id IS NOT NULL GROUP BY i.member_id", (int(year),)):
        out[r['member_id']] = out.get(r['member_id'], 0) + r['paid']
    return out
