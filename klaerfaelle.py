"""Klärfälle der Mitgliederverwaltung (ab 0.31, Wunsch Lennert Gust 10.11.2026).

Zahlungen auf 8100, bei denen die Person nicht (mehr) in der Mitgliederliste steht, landen gesammelt als Aufgabe bei der
Mitgliederverwaltung (Mitgliederbeauftragte). Es gibt zwei Arten – beide stehen in `dues_cases`:

- **ohne Zuordnung** (`external_id` leer): Die Buchhaltung meldet eine Zahlung oder einen Teil davon als Klärfall (Buchungsplatz,
  Beitragseingänge, SEPA-Datei). Bis zur Klärung bleibt der Betrag auf 8100 ohne Mitglied: Der Haushalt stimmt, Beitragskonto und
  Anteil des Kreisverbands warten; der KV-Budgetabschluss des Jahres ist gesperrt (`beitraege.pending`).
- **als „nicht in der Mitgliederliste“ gebucht** (`external_id` = `dues_external.id`): zählt schon für Haushalt und Kreisverband;
  die Mitgliederverwaltung prüft und bestätigt („war Mitglied“) oder ordnet doch ein Mitglied zu. Jede solche Zuordnung erzeugt
  einen Klärfall (`link_external`), vorhandene aus 0.29/0.30 einmalig beim Start (`init`).

Status: offen → rueckfrage (beim Kreisverband nachgefragt) → geklaert bzw. zurueck (kein Mitgliedsbeitrag – wieder bei der
Buchhaltung). Klären ändert nie eine Buchung, nur die Zuordnung (wie `beitraege.assign`). Stornierte Buchungen zählen nicht.
Im Änderungsprotokoll stehen nur Nummern und Beträge, keine Namen.
"""
from datetime import date, datetime, timedelta

ACTIVE = ('offen', 'rueckfrage')
REASON = 'nicht in der Mitgliederliste gefunden'
ORIGINS = ('SEPA-Datei', 'Kontoauszug', 'Kasse')   # woher die Zahlung kommt
MAX_ID = 2 ** 53


class FromFile(dict):
    """Angaben zu einem Klärfall, die das Programm selbst aus einer SEPA-Datei gelesen hat (sepadatei._targets): nur dann werden Herkunft,
    Gläubiger-ID und Mandatsreferenz übernommen. Aus einer Anfrage (JSON) kann dieser Typ nicht entstehen (Prüfbefund 0.31)."""


def _int(value, label):
    """Kennung aus einer Anfrage: ganze Zahl im üblichen Bereich – keine Wahrheitswerte, Listen oder Riesenzahlen."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f'{label} fehlt oder ist ungültig.')
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f'{label} fehlt oder ist ungültig.') from None
    if not 0 < number < MAX_ID:
        raise ValueError(f'{label} fehlt oder ist ungültig.')
    return number
RESULTS = {'mitglied': 'Mitglied zugeordnet', 'neu': 'neues Mitglied angelegt', 'ehemalig': 'nicht (mehr) in der Mitgliederliste',
           'kein_beitrag': 'kein Mitgliedsbeitrag – zurück an die Buchhaltung', 'neu_zugeordnet': 'von der Buchhaltung neu zugeordnet'}
NOT_DUES = ('Die Mitgliederverwaltung meldet: kein Mitgliedsbeitrag. Buchung im Buchungsplatz stornieren und richtig verbuchen '
            '(z. B. Rückzahlung, Spende) oder einem Mitglied zuordnen.')


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS dues_cases(id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL REFERENCES entries(id),external_id INTEGER,
        year INTEGER NOT NULL,amount INTEGER NOT NULL CHECK(amount!=0),name TEXT NOT NULL,info TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL,origin TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'offen'
        CHECK(status IN ('offen','rueckfrage','geklaert','zurueck')),note TEXT NOT NULL DEFAULT '',inquiry_kv TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL DEFAULT '',creditor TEXT NOT NULL DEFAULT '',mandate TEXT NOT NULL DEFAULT '',
        created TEXT NOT NULL,actor TEXT,resolved TEXT,resolved_by TEXT);
    CREATE INDEX IF NOT EXISTS dues_cases_entry ON dues_cases(entry_id);
    CREATE INDEX IF NOT EXISTS dues_cases_external ON dues_cases(external_id);
    ''')
    # Bestand aus 0.29/0.30: schon als „nicht in der Mitgliederliste“ gebuchte Zahlungen werden einmalig zu offenen Klärfällen
    import re
    sepa = {r[0] for r in db.execute('SELECT bank_id FROM sepa_files')} if db.execute("SELECT 1 FROM sqlite_master WHERE name='sepa_files'").fetchone() else set()
    for x in db.execute('SELECT x.id,x.entry_id,x.year,x.amount,x.name,x.number,x.reason,x.created,x.actor,j.source FROM dues_external x '
                        'JOIN entries e ON e.id=x.entry_id JOIN journals j ON j.id=e.journal_id '
                        'WHERE NOT EXISTS(SELECT 1 FROM dues_cases c WHERE c.external_id=x.id)').fetchall():
        m = re.match(r'^(?:bank|resolve):(\d+)', str(x[9] or ''))
        db.execute('INSERT INTO dues_cases(entry_id,external_id,year,amount,name,info,reason,origin,created,actor) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (x[1], x[0], x[2], x[3], x[4], f'Mitgliedsnummer {x[5]}' if x[5] else '', x[6],
                    'SEPA-Datei' if m and int(m.group(1)) in sepa else 'Kontoauszug', x[7], x[8]))
    db.commit()


def _text(value, limit):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def clean(raw):
    """Angaben zu einem neuen Klärfall ohne Zuordnung prüfen → „Person“ wie in beitraege._members (ohne Kreisverband: Landesverband)."""
    if not isinstance(raw, dict):
        raise ValueError('Angaben zum Klärfall fehlen.')
    name = _text(raw.get('name'), 120)
    reason = _text(raw.get('reason'), 500) or REASON
    if not name:
        raise ValueError('Klärfall: bitte angeben, wer gezahlt hat (Name laut Zahlung).')
    if len(reason) > 200:
        raise ValueError('Klärfall: Grund höchstens 200 Zeichen.')
    trusted = isinstance(raw, FromFile)              # Herkunft und Mandat nur aus der selbst gelesenen SEPA-Datei, nie aus der Anfrage
    origin = raw.get('origin') if trusted and raw.get('origin') in ORIGINS else 'Kontoauszug'
    return {'id': None, 'kv': 'LV', 'name': name, 'number': '',
            'case': {'name': name, 'info': _text(raw.get('info'), 300), 'reason': reason, 'origin': origin,
                     'creditor': _text(raw.get('creditor'), 35) if trusted else '',
                     'mandate': (raw.get('mandate') or '').strip()[:35] if trusted and isinstance(raw.get('mandate'), str) else ''}}


def _origin(store, entry_id, origin=''):
    """Herkunft eines Klärfalls: „SEPA-Datei“ nur, wenn das Programm die Zahlung selbst aus einer Datei gelesen hat (FromFile);
    sonst nach dem Umsatz der Buchung – Kassenbeleg oder Kontoauszug. Nie aus der Anfrage (Nachprüfung 0.31)."""
    import beitraege
    if origin == 'SEPA-Datei':
        return origin
    row = store.db.execute('SELECT j.source FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.id=?', (entry_id,)).fetchone()
    bank = store.db.execute('SELECT ledger FROM bank WHERE id=?', (beitraege._bank_id(row['source']),)).fetchone() if row else None
    return 'Kasse' if bank and bank['ledger'] == 'cash' else 'Kontoauszug'


def insert(store, entry_id, case, year, amount, now, actor):
    """Klärfall ohne Zuordnung anlegen (innerhalb der Transaktion des Aufrufers)."""
    return store.db.execute('INSERT INTO dues_cases(entry_id,year,amount,name,info,reason,origin,creditor,mandate,created,actor) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (entry_id, int(year), amount, case['name'], case.get('info') or '', case.get('reason') or REASON,
                             _origin(store, entry_id, case.get('origin')), case.get('creditor') or '', case.get('mandate') or '', now, actor)).lastrowid


def _key(name, number, kv):
    """Dieselbe Person beim Ersetzen einer Zuordnung: Name, Nummer und Kreisverband – ein anderer Betrag ändert nichts an der Prüfung,
    ein anderer Kreisverband schon (dann zählt die Zahlung woanders: neuer Klärfall)."""
    import beitraege
    return (beitraege._fold(name), str(number or ''), kv)


def link_external(store, entry_id, external_id, x, year, amount, now, actor, reuse=None):
    """Zu einer Zuordnung „nicht in der Mitgliederliste“ gehört ein Klärfall: bei geänderter Zuordnung derselben Person der bisherige
    (reuse aus detach_externals – der Stand „geprüft“ bleibt), sonst ein neuer offener."""
    old = (reuse or {}).get(_key(x['name'], x['number'], x['kv']))
    if old:
        store.db.execute('UPDATE dues_cases SET external_id=?,amount=? WHERE id=?', (external_id, amount, old.pop(0)))
        return
    store.db.execute('INSERT INTO dues_cases(entry_id,external_id,year,amount,name,info,reason,origin,created,actor) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (entry_id, external_id, int(year), amount, x['name'], f"Mitgliedsnummer {x['number']}" if x['number'] else '', x['reason'],
                      _origin(store, entry_id, x.get('origin')), now, actor))


def detach_externals(store, entry_id):
    """Vor dem Ersetzen einer Zuordnung (beitraege.assign): Klärfälle der bisherigen Personen außerhalb der Liste merken und lösen."""
    reuse = {}
    for c in store.rows('SELECT c.id,x.name,x.number,x.kv FROM dues_cases c JOIN dues_external x ON x.id=c.external_id WHERE x.entry_id=? ORDER BY c.id',
                        (entry_id,)):
        reuse.setdefault(_key(c['name'], c['number'], c['kv']), []).append(c['id'])
    store.db.execute('UPDATE dues_cases SET external_id=NULL WHERE external_id IN (SELECT id FROM dues_external WHERE entry_id=?)', (entry_id,))
    return reuse


def close_detached(store, reuse, now, actor):
    """Nach dem Ersetzen: Klärfälle, deren Person nicht mehr zugeordnet ist, sind erledigt (die Buchhaltung hat neu zugeordnet)."""
    for ids in reuse.values():
        for cid in ids:
            store.db.execute("UPDATE dues_cases SET status='geklaert',result=CASE WHEN status IN ('offen','rueckfrage') THEN 'neu_zugeordnet' ELSE result END,"
                             "resolved=COALESCE(resolved,?),resolved_by=COALESCE(resolved_by,?) WHERE id=?", (now, actor, cid))


def close_entry_cases(store, entry_id, now, actor):
    """Offene Klärfälle ohne Zuordnung einer Buchungszeile erledigen – die Zeile wird vollständig neu aufgeteilt (SEPA-Datei)."""
    return store.db.execute("UPDATE dues_cases SET status='geklaert',result='neu_zugeordnet',resolved=?,resolved_by=? WHERE entry_id=? "
                            "AND external_id IS NULL AND status IN ('offen','rueckfrage')", (now, actor, entry_id)).rowcount


def acknowledge_returned(store, entry_id):
    """Die Buchhaltung hat eine zurückgegebene Zahlung („kein Mitgliedsbeitrag“) wieder angefasst (neu zugeordnet oder erneut gemeldet):
    der Hinweis unter Beitragseingänge ist damit erledigt; der Klärfall bleibt mit seinem Ergebnis in der Liste „erledigt“."""
    store.db.execute("UPDATE dues_cases SET status='geklaert' WHERE entry_id=? AND status='zurueck'", (entry_id,))


def number(row):
    return f"K-{str(row['created'])[:4]}-{row['id']:04d}"


def _rows(store):
    import beitraege
    skip = beitraege._skip(store)
    rows = store.rows('SELECT c.*,e.journal_id,e.kv entry_kv,e.credit-e.debit entry_amount,j.day,j.source,j.description,x.kv ext_kv,x.number ext_number '
                      'FROM dues_cases c JOIN entries e ON e.id=c.entry_id JOIN journals j ON j.id=e.journal_id '
                      'LEFT JOIN dues_external x ON x.id=c.external_id ORDER BY j.day,c.id')
    return [r for r in rows if r['journal_id'] not in skip]


def items(store):
    """Alle Klärfälle (ohne stornierte Buchungen), älteste Zahlung zuerst; offene mit Vorschlägen aus der Mitgliederliste."""
    import beitraege
    rows = _rows(store)
    banks, ids = {}, sorted({beitraege._bank_id(r['source']) for r in rows} - {None})
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        banks.update({b['id']: b for b in store.rows(f"SELECT id,name,reference FROM bank WHERE id IN ({','.join('?' * len(chunk))})", chunk)})
    index = beitraege._index(store.rows('SELECT id,number,name,first_name,last_name,kv FROM members')) if any(r['status'] in ACTIVE for r in rows) else None
    out = []
    for r in rows:
        bank = banks.get(beitraege._bank_id(r['source'])) or {}
        active = r['status'] in ACTIVE
        out.append({'id': r['id'], 'number': number(r), 'entry_id': r['entry_id'], 'day': r['day'], 'year': r['year'], 'amount': r['amount'],
                    'entry_amount': r['entry_amount'], 'name': r['name'], 'info': r['info'], 'reason': r['reason'], 'origin': r['origin'],
                    'status': r['status'], 'note': r['note'], 'inquiry_kv': r['inquiry_kv'], 'result': r['result'],
                    'result_text': RESULTS.get(r['result'], ''), 'created': r['created'], 'resolved': r['resolved'],
                    'booked': r['external_id'] is not None, 'kv': r['ext_kv'] or '', 'ext_number': r['ext_number'] or '',
                    'entry_kv': r['entry_kv'], 'payer': bank.get('name') or '', 'reference': bank.get('reference') or r['description'] or '',
                    'suggestions': [s['member_id'] for s in beitraege.suggest(store, {'payer': r['name'], 'reference': f"{r['info']} {r['ext_number'] or ''}"},
                                                                              index=index)] if active else []})
    return out


def counts(store, today=None):
    """Kennzahlen für Start und Seitenleiste: offen, in Rückfrage, davon ohne Zuordnung, geklärt in den letzten 30 Tagen."""
    rows = _rows(store)
    since = (date.fromisoformat(today) if today else date.today()) - timedelta(days=30)
    active = [r for r in rows if r['status'] in ACTIVE]
    solved = [r for r in rows if r['status'] not in ACTIVE and (r['resolved'] or '')[:10] >= since.isoformat()]
    by_result = {}
    for r in solved:
        by_result[r['result']] = by_result.get(r['result'], 0) + 1
    return {'active': len(active), 'offen': sum(1 for r in active if r['status'] == 'offen'), 'rueckfrage': sum(1 for r in active if r['status'] == 'rueckfrage'),
            'unassigned': sum(1 for r in active if r['external_id'] is None), 'sum': sum(r['amount'] for r in active), 'solved_30': len(solved),
            'by_result': by_result}


def _entry(store, entry_id, skip=None):
    import beitraege
    e = store.db.execute('SELECT e.id,e.account,e.kv,e.credit-e.debit amount,e.journal_id,j.source,j.description,'
                         'COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) year FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.id=?',
                         (_int(entry_id, 'Zahlung'),)).fetchone()
    if not e or e['account'] != beitraege.ACCOUNT or str(e['source']).split(':')[0] not in beitraege.BANK_SOURCES:
        raise ValueError('Nur Zahlungen auf 8100 aus dem Kontoauszug können ein Klärfall sein.')
    if e['journal_id'] in (skip if skip is not None else beitraege._skip(store)):
        raise ValueError('Diese Buchung ist storniert.')
    return e


def _open_part(store, e):
    """Teil einer 8100-Zeile, der weder zugeordnet noch als Klärfall gemeldet ist."""
    done = store.db.execute("SELECT COALESCE((SELECT SUM(amount) FROM dues_assignments WHERE entry_id=:e),0)+COALESCE((SELECT SUM(amount) FROM dues_external "
                            "WHERE entry_id=:e),0)+COALESCE((SELECT SUM(amount) FROM dues_cases WHERE entry_id=:e AND external_id IS NULL AND "
                            "status IN ('offen','rueckfrage')),0)", {'e': e['id']}).fetchone()[0]
    return e['amount'] - done


def _report(store, entry_id, data, now, actor, skip=None):
    import beitraege
    e = _entry(store, entry_id, skip)
    cents = _open_part(store, e)
    if not cents:
        raise ValueError('Diese Zahlung ist schon vollständig zugeordnet bzw. als Klärfall gemeldet.')
    bank = store.db.execute('SELECT name,reference FROM bank WHERE id=?', (beitraege._bank_id(e['source']),)).fetchone()
    raw = data if isinstance(data, dict) else {}
    text = lambda key: raw.get(key) if isinstance(raw.get(key), str) and raw.get(key).strip() else None   # noqa: E731
    case = clean({'name': text('name') or (bank and bank['name']) or 'unbekannt', 'reason': text('reason'),
                  'info': text('info') or (bank and bank['reference']) or e['description']})['case']
    acknowledge_returned(store, e['id'])
    cid = insert(store, e['id'], case, e['year'], cents, now, actor)
    return {'fall': cid, 'entry': e['id'], 'cents': cents}


def report(store, entry_id, data, actor):
    """Buchhaltung: den offenen Teil einer gebuchten 8100-Zahlung als Klärfall an die Mitgliederverwaltung melden."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        done = _report(store, entry_id, data, datetime.now().isoformat(timespec='seconds'), actor)
        store.audit(actor, 'Klärfall gemeldet', done)
    return done['fall']


def report_open(store, year, actor):
    """Alle Zahlungen (des Jahres) ohne Mitglied auf einmal als Klärfälle melden; liefert die Anzahl. Zahlungen, die die Mitgliederverwaltung
    als „kein Mitgliedsbeitrag“ zurückgegeben hat, bleiben bei der Buchhaltung (einzeln lassen sie sich erneut melden – Prüfbefund 0.31)."""
    import beitraege
    year = None if year is None else _int(year, 'Jahr')
    now = datetime.now().isoformat(timespec='seconds')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        skip = beitraege._skip(store)
        done = [_report(store, x['id'], None, now, actor, skip) for x in beitraege.entries(store, year, only_open=True) if not x['returned']]
        if done:
            for i in range(0, len(done), 300):       # je Eintrag höchstens 300 – vollständig, damit die Bereinigung jeden Fall findet
                part = done[i:i + 300]
                store.audit(actor, 'Klärfälle gemeldet', {'jahr': year, 'anzahl': len(part), 'faelle': part})
    return len(done)


def _case(store, case_id):
    c = store.db.execute('SELECT * FROM dues_cases WHERE id=?', (_int(case_id, 'Klärfall'),)).fetchone()
    if not c or c['status'] not in ACTIVE:
        raise ValueError('Klärfall nicht gefunden oder schon erledigt – Liste neu laden.')
    return c


def resolve(store, case_id, data, actor):
    """Mitgliederverwaltung klärt: {'member_id'} (mit 'new': neu angelegt), {'external': {name, number, kv, reason}} bzw. {'confirm': True}
    (war Mitglied / nicht mehr in der Liste) oder {'no_dues': True} (kein Mitgliedsbeitrag – zurück an die Buchhaltung)."""
    import beitraege
    import sepadatei
    if not isinstance(data, dict):
        raise ValueError('Ungültige Angabe.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        c = _case(store, case_id)
        e = _entry(store, c['entry_id'])
        now = datetime.now().isoformat(timespec='seconds')
        status, member, ext, amount, merged = 'geklaert', None, c['external_id'], c['amount'], None
        if data.get('member_id') is not None:
            m = store.db.execute('SELECT id,kv FROM members WHERE id=?', (_int(data['member_id'], 'Mitglied'),)).fetchone()
            if not m:
                raise ValueError('Mitglied nicht gefunden.')
            booked = store.db.execute('SELECT kv FROM dues_external WHERE id=?', (ext,)).fetchone() if ext else None
            if booked and booked['kv'] != m['kv'] and store.db.execute('SELECT 1 FROM kv_closings WHERE year=?', (c['year'],)).fetchone():
                raise ValueError(f"Das Budgetjahr {c['year']} ist abgeschlossen: Die Zahlung zählt dort für einen anderen Kreisverband als den des Mitglieds. "
                                 'Bitte bestätigen („nicht in der Mitgliederliste“) und mit einer Notiz an die Buchhaltung geben.')   # Prüfbefund 0.31
            if beitraege._kv_conflict(e['kv'], m['kv']):
                raise ValueError('Diese Zahlung ist für einen anderen Kreisverband gebucht. Für ein Mitglied eines anderen Kreisverbands muss die '
                                 'Buchhaltung die Buchung stornieren und neu verbuchen – den Klärfall dazu mit einer Notiz zurückgeben.')
            if ext:                                  # die Zeile „nicht in der Mitgliederliste“ geht ganz an das Mitglied – mit ihrem Betrag,
                amount = store.db.execute('SELECT amount FROM dues_external WHERE id=?', (ext,)).fetchone()['amount']   # damit die Summe der Zahlung stimmt
                store.db.execute('UPDATE dues_cases SET external_id=NULL WHERE external_id=?', (ext,))   # nie ein Klärfall an einer gelöschten Zeile
                store.db.execute('DELETE FROM dues_external WHERE id=?', (ext,))
                ext = None
            same = store.db.execute('SELECT id FROM dues_assignments WHERE entry_id=? AND member_id=?', (e['id'], m['id'])).fetchone()
            if same:                                 # Mitglied steht bei dieser Zahlung schon: Betrag erhöhen statt zweiter Zeile
                store.db.execute('UPDATE dues_assignments SET amount=amount+? WHERE id=?', (amount, same['id']))
            else:
                store.db.execute('INSERT INTO dues_assignments(entry_id,member_id,kv,year,amount,created,actor) VALUES(?,?,?,?,?,?,?)',
                                 (e['id'], m['id'], m['kv'], c['year'], amount, now, actor))
            result, member = 'neu' if data.get('new') else 'mitglied', m['id']
        elif data.get('no_dues') is True:
            if ext:
                raise ValueError('Diese Zahlung ist schon als „nicht in der Mitgliederliste“ gebucht – die Zuordnung ändert die Buchhaltung '
                                 'unter „Beitragseingänge“.')
            status, result = 'zurueck', 'kein_beitrag'
        elif ext and data.get('confirm') is True:
            result = 'ehemalig'
        elif not ext and data.get('external') is not None:
            x = beitraege.external(store, data['external'])
            if beitraege._kv_conflict(e['kv'], x['kv']):
                raise ValueError('Diese Zahlung ist für einen anderen Kreisverband gebucht – bitte diesen Kreisverband wählen.')
            same = next((r for r in store.rows('SELECT id,name,number,kv,amount FROM dues_external WHERE entry_id=?', (e['id'],))
                         if _key(r['name'], r['number'], '') == _key(x['name'], x['number'], '')), None)
            if same and same['kv'] != x['kv']:       # eine Person zählt je Zahlung nur für einen Kreisverband (sonst ließe sich die Zuordnung nicht mehr ändern)
                raise ValueError('Diese Person steht bei dieser Zahlung schon für einen anderen Kreisverband – bitte denselben Kreisverband wählen '
                                 'oder die Zuordnung von der Buchhaltung unter „Beitragseingänge“ ändern lassen.')
            if same:
                # Dieselbe Person steht bei dieser Zahlung schon: eine Zeile, Beträge zusammengezählt. Zu einer Zeile gehört genau ein Klärfall
                # (der vorhandene, er zeigt jetzt den ganzen Betrag) – dieser hier ist damit erledigt und hängt an keiner Zeile (Nachprüfung 0.31).
                store.db.execute('UPDATE dues_external SET amount=amount+? WHERE id=?', (c['amount'], same['id']))
                linked = store.db.execute('SELECT id FROM dues_cases WHERE external_id=? ORDER BY id LIMIT 1', (same['id'],)).fetchone()
                if linked:
                    store.db.execute('UPDATE dues_cases SET amount=? WHERE id=?', (same['amount'] + c['amount'], linked['id']))
                    merged = linked['id']
                else:
                    ext, amount = same['id'], same['amount'] + c['amount']
            else:
                ext = store.db.execute('INSERT INTO dues_external(entry_id,name,number,kv,year,amount,reason,created,actor) VALUES(?,?,?,?,?,?,?,?,?)',
                                       (e['id'], x['name'], x['number'], x['kv'], c['year'], c['amount'], x['reason'], now, actor)).lastrowid
            result = 'ehemalig'
        else:
            raise ValueError('Bitte wählen, wie der Klärfall geklärt wird.')
        if c['mandate'] and status == 'geklaert':    # Mandatsreferenz der SEPA-Datei: beim nächsten Einzug sicher (bzw. nicht mehr vorschlagen)
            sepadatei.learn_one(store, c['creditor'], c['mandate'], member, now, actor)
        note = _text(data.get('note'), 500) if isinstance(data.get('note'), str) else c['note']
        store.db.execute('UPDATE dues_cases SET status=?,result=?,note=?,external_id=?,amount=?,resolved=?,resolved_by=? WHERE id=?',
                         (status, result, note, ext, amount if ext or member else c['amount'], now, actor, c['id']))
        store.audit(actor, 'Klärfall geklärt', {'fall': c['id'], 'ergebnis': result, 'entry': e['id'], 'cents': amount if member else c['amount'],
                                                 **({'mitglied': member} if member else {}), **({'zusammen_mit_fall': merged} if merged else {})})
    return result


def update(store, case_id, data, actor):
    """Notiz und Rückfrage beim Kreisverband: {'note'}, {'status': 'rueckfrage', 'kv'} bzw. {'status': 'offen'}."""
    if not isinstance(data, dict):
        raise ValueError('Ungültige Angabe.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        c = _case(store, case_id)
        _entry(store, c['entry_id'])                 # stornierte Buchung: der Klärfall gilt nicht mehr
        status = data.get('status', c['status'])
        if not isinstance(status, str) or status not in ACTIVE:
            raise ValueError('Ungültiger Status.')
        kv = data.get('kv') or '' if status == 'rueckfrage' else ''
        if kv and (not isinstance(kv, str) or not store.db.execute('SELECT 1 FROM kv WHERE code=?', (kv,)).fetchone()):
            raise ValueError('Kreisverband nicht gefunden.')
        note = _text(data.get('note'), 500) if isinstance(data.get('note'), str) else c['note']
        store.db.execute('UPDATE dues_cases SET status=?,inquiry_kv=?,note=? WHERE id=?', (status, kv, note, c['id']))
        if status != c['status']:
            store.audit(actor, 'Klärfall: Rückfrage beim Kreisverband' if status == 'rueckfrage' else 'Klärfall: Rückfrage beendet',
                        {'fall': c['id'], 'entry': c['entry_id'], **({'kv': kv} if kv else {})})
