"""Mitgliederbuchhaltung: Beitragskonto je Mitglied (Nebenbuch zu den Mitgliedsbeiträgen).

Wunsch Lennert Gust 08.11.2026: „jede Buchung an Mitgliedsbeiträgen einer Person zuordnen müssen … da ich auch eine
Mitgliederbuchhaltung machen möchte“. Entscheidungen (Rückfrage 08.11.2026): Mitglieder kommen aus der Mitgliederliste
(Import); das Soll eines Jahres ist der Beitrag laut Staffel bzw. individuellem Beitrag – auch schon für 2025.

Soll (Beitrag eines Jahres), in dieser Reihenfolge:
  1. von Hand festgelegt (Tabelle dues_targets: Betrag und Grund – z. B. Austrittsjahr, Befreiung, Erlass, Korrektur),
  2. Beitragsrechnung des Jahres (gestellt: Rechnungsbetrag; storniert: 0),
  3. individueller Jahresbeitrag bzw. Staffel nach Satzung § 49 Abs. 1 (Store.dues_calc, im Eintrittsjahr anteilig nach Abs. 2).
  Für Jahre nach dem laufenden zählen nur 1. und 2. (z. B. im Dezember gestellte Beitragsrechnung fürs Folgejahr).
  Erstes Jahr ist der Datenbeginn (Stammdaten), frühestens das Eintrittsjahr; nach dem Austrittsjahr gibt es kein Soll.
  Fällig ist der Beitrag am 01.01., im Eintrittsjahr am Tag des Beitritts (Satzung § 49 Abs. 2 Satz 2, Abs. 4).
Ist: einem Mitglied zugeordnete Zahlungen auf 8100 (beitraege.py, Beitragsjahr = Haushaltsjahr der Buchung) und Zahlungen
  auf Beitragsrechnungen (Beitragsjahr der Rechnung); Rücklastschriften mindern. Stornierte Buchungen zählen nicht.
Saldo = Ist − fälliges Soll: negativ = Rückstand, positiv = Guthaben (z. B. Vorauszahlung).

Das Soll ohne Beitragsrechnung wird nicht im Hauptbuch gebucht (Mitgliedsbeiträge zählen im Haushalt bei Zahlung) – das
Beitragskonto ist ein Nebenbuch. Abstimmung mit dem Hauptbuch: Zahlungen auf 8100 aus dem Kontoauszug = zugeordnet +
ohne Mitglied; nur wenn „ohne Mitglied“ 0 ist, sind die Mitgliederkonten vollständig.
"""
from datetime import date, datetime

from formate import money

MAX_REASON = 200


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS dues_targets(id INTEGER PRIMARY KEY,member_id INTEGER NOT NULL REFERENCES members(id),year INTEGER NOT NULL,
        amount INTEGER NOT NULL CHECK(amount>=0),reason TEXT NOT NULL,created TEXT NOT NULL,actor TEXT,UNIQUE(member_id,year));
    ''')
    db.commit()


def first_year(store):
    """Erstes Jahr der Mitgliederkonten: Datenbeginn (ältere Daten sind gelöscht), sonst KV-Finanzrahmen-Beginn."""
    import kvbudget
    return int((store.data_start() or kvbudget.start(store))[:4])


def _context(store, today=None):
    import beitraege
    today = today or date.today().isoformat()
    skip = beitraege._skip(store)
    first = first_year(store)
    ctx = {'cfg': store.config(), 'first': first, 'today': today, 'year': int(today[:4]),
           'targets': {}, 'invoices': {}, 'pay': {}}            # je Mitglied: {Jahr: …} bzw. Liste der Zahlungen
    for r in store.rows('SELECT * FROM dues_targets'):
        ctx['targets'].setdefault(r['member_id'], {})[r['year']] = r
    ctx['rates'] = {}
    for r in store.db.execute('SELECT member_id,valid_from,amount FROM dues_rates ORDER BY member_id,valid_from'):
        ctx['rates'].setdefault(r[0], []).append((r[1], r[2]))
    for r in store.rows("SELECT i.id,i.number,i.member_id,i.year,i.total,i.status,i.issued,i.due FROM invoices i WHERE i.kind='dues' "
                        "AND i.member_id IS NOT NULL AND i.year IS NOT NULL AND NOT COALESCE(json_extract(i.snapshot,'$.pilot'),0)"):
        ctx['invoices'].setdefault(r['member_id'], {})[r['year']] = r   # Pilotrechnungen zählen nicht (wie Monatsbericht, Versand)
    for r in store.rows('SELECT a.member_id,a.year,a.amount,j.day,j.id journal_id FROM dues_assignments a JOIN entries e ON e.id=a.entry_id '
                        'JOIN journals j ON j.id=e.journal_id ORDER BY j.day,a.id'):
        if r['journal_id'] not in skip and r['day'] <= today and r['year'] >= first:
            ctx['pay'].setdefault(r['member_id'], []).append({'day': r['day'], 'year': r['year'], 'amount': r['amount'], 'journal_id': r['journal_id'],
                                                              'text': 'Beitragszahlung' if r['amount'] > 0 else 'Rücklastschrift / Rückzahlung'})
    for r in store.rows("SELECT i.member_id,i.year,i.number,p.amount,b.booked FROM payments p JOIN invoices i ON i.id=p.invoice_id JOIN bank b ON b.id=p.bank_id "
                        "WHERE i.kind='dues' AND b.state='booked' AND i.member_id IS NOT NULL AND b.booked<=? AND i.year>=? ORDER BY b.booked,p.id", (today, first)):
        ctx['pay'].setdefault(r['member_id'], []).append({'day': r['booked'], 'year': r['year'], 'amount': r['amount'], 'journal_id': None,
                                                          'text': ('Zahlung ' if r['amount'] > 0 else 'Rücklastschrift ') + r['number']})
    for v in ctx['pay'].values():
        v.sort(key=lambda x: x['day'])
    return ctx


def _due_day(m, year):
    return m['joined'] if str(m['joined'])[:4] == str(year) else f'{year}-01-01'


def _left_year(m):
    return int(m['left_on'][:4]) if m['left_on'] else None


def target(store, m, year, ctx):
    """Soll eines Mitglieds für ein Jahr: {'amount': Cent oder None (unklar), 'basis', 'source', 'issue', 'due'} oder None (kein Beitrag)."""
    due = _due_day(m, year)
    t = ctx['targets'].get(m['id'], {}).get(year)
    if t:
        return {'amount': t['amount'], 'basis': f"festgelegt: {t['reason']}", 'source': 'set', 'issue': None, 'due': due, 'reason': t['reason']}
    inv = ctx['invoices'].get(m['id'], {}).get(year)
    left = _left_year(m)
    if inv and left is not None and year > left:   # nach dem Austrittsjahr gibt es keinen Beitrag
        return None if inv['status'] == 'cancelled' else {
            'amount': None, 'basis': f"Beitragsrechnung {inv['number']}", 'source': 'invoice', 'due': due,
            'issue': f"Beitragsrechnung {inv['number']} nach dem Austritt – bitte stornieren"}
    if inv:
        if inv['status'] == 'cancelled':      # kein stiller Erlass: Soll mit Grund festlegen (eine neue Beitragsrechnung ist nicht möglich)
            return {'amount': None, 'basis': f"Beitragsrechnung {inv['number']} storniert", 'source': 'invoice', 'due': due,
                    'issue': f"Beitragsrechnung {inv['number']} storniert – Soll festlegen"}
        return {'amount': inv['total'], 'basis': f"Beitragsrechnung {inv['number']}", 'source': 'invoice', 'issue': None,
                'due': max(due, inv['due'] or due), 'invoice': inv['number']}      # nie vor dem Zahlungsziel der Rechnung fällig
    if year > ctx['year']:
        return None                                   # Folgejahr: nur mit Rechnung oder festgelegtem Soll
    if m.get('guest'):
        return {'amount': 0, 'basis': 'Gastmitglied (beitragsfrei)', 'source': 'calc', 'issue': None, 'due': due}
    if m.get('dues_free'):
        return {'amount': 0, 'basis': 'beitragsfrei', 'source': 'calc', 'issue': None, 'due': due}
    try:
        r = store.dues_calc(m, year, ctx['cfg'], rates=ctx['rates'].get(m['id'], []))
    except ValueError as e:
        return {'amount': None, 'basis': '', 'source': 'calc', 'issue': str(e), 'due': due}
    if r is None:
        return None
    return {'amount': r['amount'], 'basis': r['basis'], 'source': 'calc', 'issue': None, 'due': due}


def member_years(m, ctx_first, this_year):
    """Mögliche Beitragsjahre eines Mitglieds: ab Datenbeginn bzw. Eintritt bis zum laufenden Jahr bzw. Austrittsjahr."""
    first = max(ctx_first, int(str(m['joined'])[:4]))
    last = this_year if not m['left_on'] else min(this_year, int(m['left_on'][:4]))
    return first, last


def _years(m, ctx):
    """Jahre des Beitragskontos: mögliche Beitragsjahre, dazu Jahre mit Rechnung, Festlegung oder Zahlung (ab Datenbeginn)."""
    first, last = member_years(m, ctx['first'], ctx['year'])
    years = set(range(first, last + 1))
    years |= set(ctx['targets'].get(m['id'], {})) | set(ctx['invoices'].get(m['id'], {}))
    years |= {p['year'] for p in ctx['pay'].get(m['id'], [])}
    return sorted(y for y in years if y >= ctx['first'])


def _account(store, m, ctx):
    years, postings, issues = [], [], []
    pays = ctx['pay'].get(m['id'], [])
    for y in _years(m, ctx):
        t = target(store, m, y, ctx)
        soll = t['amount'] if t else 0
        due = bool(t) and t['due'] <= ctx['today']
        if t and t['issue']:
            issues.append(f"{y}: {t['issue']}")
        years.append({'year': y, 'soll': soll, 'basis': t['basis'] if t else 'kein Beitrag', 'source': t['source'] if t else None,
                      'issue': t['issue'] if t else None, 'due': t['due'] if t else None, 'due_now': due, 'reason': (t or {}).get('reason'),
                      'paid': sum(p['amount'] for p in pays if p['year'] == y)})
        if t and t['amount']:
            postings.append({'day': t['due'], 'year': y, 'text': f'Beitrag {y} ({t["basis"]})', 'soll': t['amount'], 'ist': 0, 'counts': due})
    for p in pays:
        postings.append({'day': p['day'], 'year': p['year'], 'text': p['text'], 'soll': 0, 'ist': p['amount'], 'counts': True,
                         'journal_id': p['journal_id']})
    postings.sort(key=lambda x: (x['day'], x['soll'] == 0))     # Sollstellung vor Zahlung am selben Tag
    bal = 0
    for x in postings:
        bal += x['ist'] - (x['soll'] if x['counts'] else 0)
        x['balance'] = bal
    # Offen je Jahr: alle Zahlungen decken die ältesten fälligen Beiträge zuerst (unabhängig vom Haushaltsjahr der Zahlung)
    pool = sum(p['amount'] for p in pays)
    for r in years:
        r['end_balance'] = next((x['balance'] for x in reversed(postings) if x['day'] <= f"{r['year']}-12-31"), 0)
        owed = r['soll'] if r['due_now'] and r['soll'] else 0
        covered = min(max(pool, 0), owed)
        pool -= covered
        r['open'] = None if r['soll'] is None else owed - covered
    if pool < 0:     # mehr zurückgebucht als gezahlt (Rücklastschrift ohne Gegenzahlung): Rest beim jüngsten fälligen Jahr
        last_due = next((r for r in reversed(years) if r['due_now'] and r['open'] is not None), None)
        if last_due:
            last_due['open'] -= pool
    balance = sum(p['amount'] for p in pays) - sum(r['soll'] for r in years if r['due_now'] and r['soll'])
    last = max((p['day'] for p in pays if p['amount'] > 0), default=None)
    return {'member_id': m['id'], 'years': years, 'postings': postings, 'balance': balance, 'arrears': max(-balance, 0),
            'credit': max(balance, 0), 'issues': issues, 'last_payment': last,
            'arrear_years': [r['year'] for r in years if r['open']],
            'unclear_years': [r['year'] for r in years if r['soll'] is None]}      # Soll nicht berechenbar (wahlrecht.py)


def account(store, member_id, today=None):
    """Beitragskonto eines Mitglieds: Jahre (Soll, Grundlage, gezahlt, offen, Stand 31.12.) und Buchungen mit laufendem Saldo."""
    m = store.db.execute('SELECT * FROM members WHERE id=?', (int(member_id),)).fetchone()
    if not m:
        raise ValueError('Mitglied nicht gefunden.')
    ctx = _context(store, today)
    return {**_account(store, dict(m), ctx), 'first': ctx['first'], 'today': ctx['today'], 'member': dict(m)}


def overview(store, year=None, today=None):
    """Alle Mitglieder: Soll und gezahlt im Jahr, Saldo (heute) – für die Mitgliederliste, Rückstände und Abstimmung."""
    ctx = _context(store, today)
    year = int(year or ctx['year'])
    mandates = {r[0] for r in store.db.execute("SELECT member_id FROM mandates WHERE status='active'")}
    rows = {}
    for m in store.rows('SELECT * FROM members'):
        acc = _account(store, m, ctx)
        y = next((r for r in acc['years'] if r['year'] == year), None)
        rows[m['id']] = {'soll': y['soll'] if y else 0, 'basis': y['basis'] if y else 'kein Beitrag', 'issue': y['issue'] if y else None,
                         'paid': y['paid'] if y else 0, 'open': y['open'] if y else 0, 'due_now': bool(y and y['due_now']), 'balance': acc['balance'], 'arrears': acc['arrears'],
                         'credit': acc['credit'], 'issues': acc['issues'], 'last_payment': acc['last_payment'], 'arrear_years': acc['arrear_years'],
                         'unclear_years': acc['unclear_years'], 'mandate': m['id'] in mandates}
    return rows


def stand(row, m):
    """Stand des Beitrags im Jahr ohne Betrag (sichtbar mit „Mitglieder lesen“, 0.43): wie jahr_stand – Zahlungen decken die ältesten
    fälligen Beiträge zuerst; row aus overview. Gast und beitragsfrei: frei."""
    if row['soll'] is not None and (m['guest'] or m['dues_free']):
        return 'frei'
    return jahr_stand(row)


def jahr_stand(y):
    """Stand eines Beitragsjahres aus account ohne Betrag (Akte, 0.43): klaeren / frei / spaeter (noch nicht fällig) / offen / bezahlt."""
    if y['soll'] is None:
        return 'klaeren'
    if not y['soll']:
        return 'frei'
    if not y['due_now']:
        return 'spaeter'
    return 'offen' if y['open'] else 'bezahlt'


def returned_after(store, after, years, today=None):
    """Mitglieder, bei denen nach dem Tag `after` (bis heute) eine Rücklastschrift oder Rückzahlung für eines der Beitragsjahre gebucht ist."""
    pay = _context(store, today)['pay']
    return {mid for mid, rows in pay.items() if any(p['amount'] < 0 and p['day'] > after and p['year'] in years for p in rows)}


def reconciliation(store, year, until=None):
    """Abstimmung Hauptbuch ↔ Mitgliederkonten für ein Haushaltsjahr (nur Zahlungen, ohne Stornos; until: Zahlungen bis zu diesem Tag)."""
    import beitraege
    until = until or '9999-12-31'
    items = [x for x in beitraege.entries(store, year) if x['day'] <= until]
    assigned = sum(sum(a['amount'] for a in x['members']) for x in items)
    external = sum(sum(a['amount'] for a in x['members'] if a.get('external')) for x in items)   # nicht (mehr) in der Mitgliederliste
    total = sum(x['amount'] for x in items)
    invoice = store.db.execute("SELECT COALESCE(SUM(p.amount),0) FROM payments p JOIN invoices i ON i.id=p.invoice_id JOIN bank b ON b.id=p.bank_id "
                               "WHERE i.kind='dues' AND b.state='booked' AND i.year=? AND b.booked<=?", (int(year), until)).fetchone()[0]
    open_items = [x for x in items if x['open'] or x['in_case']]
    return {'year': int(year), 'total': total, 'assigned': assigned, 'external': external, 'open': total - assigned, 'open_count': len(open_items),
            'in_case': sum(x['in_case'] for x in items), 'case_count': sum(len(x['cases']) for x in items),   # als Klärfall bei der Mitgliederverwaltung
            'invoice_paid': invoice}


def summary(store, year=None, today=None, rows=None):
    """Kennzahlen der Mitgliederbuchhaltung für ein Jahr."""
    year = int(year or (today or date.today().isoformat())[:4])
    rows = rows if rows is not None else overview(store, year, today)
    return {'year': year, 'soll': sum(r['soll'] or 0 for r in rows.values()), 'paid': sum(r['paid'] for r in rows.values()),
            'arrears': sum(r['arrears'] for r in rows.values()), 'arrears_count': sum(1 for r in rows.values() if r['arrears']),
            'credit': sum(r['credit'] for r in rows.values()), 'issues': sum(1 for r in rows.values() if r['issue']),
            'issues_all': sum(1 for r in rows.values() if r['issues']),
            'reconciliation': reconciliation(store, year, today)}


def arrears(store, kv=None, today=None):
    """Rückstandsliste (Stand heute): Mitglieder mit negativem Saldo, nach Kreisverband und Name."""
    rows = overview(store, today=today)
    out = []
    for m in store.rows("SELECT id,number,name,first_name,last_name,kv,email,left_on FROM members ORDER BY kv,COALESCE(NULLIF(last_name,''),name) COLLATE NOCASE,"
                        "first_name COLLATE NOCASE"):
        r = rows[m['id']]
        if r['arrears'] and (not kv or m['kv'] == kv):
            out.append({**m, **r})
    return out


def issues(store, kv=None, today=None):
    """Mitglieder, deren Soll sich nicht berechnen lässt (z. B. Austrittsjahr, fehlende Beitragsregel) – Soll festlegen."""
    rows = overview(store, today=today)
    return [{**m, 'issues': rows[m['id']]['issues']} for m in store.rows('SELECT id,number,name,first_name,last_name,kv FROM members ORDER BY kv,name')
            if rows[m['id']]['issues'] and (not kv or m['kv'] == kv)]


def set_target(store, member_id, year, amount, reason, actor):
    """Soll eines Jahres von Hand festlegen (Betrag ≥ 0 mit Grund) – geht Beitragsrechnung und Staffel vor."""
    year = int(year)
    m = store.db.execute('SELECT id,number,joined,left_on FROM members WHERE id=?', (int(member_id),)).fetchone()
    if not m:
        raise ValueError('Mitglied nicht gefunden.')
    first, last = member_years(m, first_year(store), date.today().year + 1)
    if not first <= year <= last:
        raise ValueError(f'Beitragsjahr {first} bis {last} (Datenbeginn, Eintritt, Austritt).' if first <= last else 'Für dieses Mitglied gibt es kein Beitragsjahr.')
    inv = store.db.execute("SELECT number FROM invoices WHERE kind='dues' AND member_id=? AND year=? AND status='issued' "
                           "AND NOT COALESCE(json_extract(snapshot,'$.pilot'),0)", (m['id'], year)).fetchone()
    if inv:   # sonst liefe das Beitragskonto auseinander mit der offenen Forderung (1500), Erinnerung und Lastschrift
        raise ValueError(f"Für {year} gibt es die Beitragsrechnung {inv['number']} – sie bestimmt das Soll. Zuerst die Rechnung stornieren.")
    cents = money(amount)
    if cents < 0:
        raise ValueError('Der Beitrag darf nicht negativ sein.')
    reason = ' '.join(str(reason or '').split())
    if not reason:
        raise ValueError('Bitte einen Grund angeben (z. B. Austritt, Befreiung, Erlass).')
    if len(reason) > MAX_REASON:
        raise ValueError(f'Grund höchstens {MAX_REASON} Zeichen.')
    with store.db:
        old = store.db.execute('SELECT amount,reason FROM dues_targets WHERE member_id=? AND year=?', (m['id'], year)).fetchone()
        store.db.execute('INSERT INTO dues_targets(member_id,year,amount,reason,created,actor) VALUES(?,?,?,?,?,?) ON CONFLICT(member_id,year) '
                         'DO UPDATE SET amount=excluded.amount,reason=excluded.reason,created=excluded.created,actor=excluded.actor',
                         (m['id'], year, cents, reason, datetime.now().isoformat(timespec='seconds'), actor))
        store.audit(actor, 'Beitrags-Soll festgelegt', {'member': m['id'], 'number': m['number'], 'year': year, 'cents': cents, 'grund': reason,
                                                       **({'vorher': {'cents': old['amount'], 'grund': old['reason']}} if old else {})})


def remove_target(store, member_id, year, actor):
    """Festgelegtes Soll entfernen: es gilt wieder Beitragsrechnung bzw. Staffel."""
    with store.db:
        old = store.db.execute('SELECT t.amount,t.reason,m.number FROM dues_targets t JOIN members m ON m.id=t.member_id WHERE t.member_id=? AND t.year=?',
                               (int(member_id), int(year))).fetchone()
        if not old:
            raise ValueError('Für dieses Jahr ist kein Soll festgelegt.')
        store.db.execute('DELETE FROM dues_targets WHERE member_id=? AND year=?', (int(member_id), int(year)))
        store.audit(actor, 'Festgelegtes Beitrags-Soll entfernt', {'member': int(member_id), 'number': old['number'], 'year': int(year),
                                                                   'vorher': {'cents': old['amount'], 'grund': old['reason']}})


# ---------------------------------------------------------------- Ausgaben (CSV, PDF)

def _eur(cents):
    return '' if cents is None else f'{cents / 100:.2f}'.replace('.', ',')


def overview_csv(store, year, kv=None, today=None):
    """Mitgliederkonten eines Jahres als CSV (für Kassenprüfung und Kreisverbände)."""
    import berichte
    from core import KVS
    from formate import german_date
    today = today or date.today().isoformat()
    names, rows = dict(KVS), overview(store, year, today)
    data = [[m['number'], m['last_name'] or m['name'], m['first_name'] or '', names.get(m['kv'], m['kv']), m['joined'], m['left_on'] or '',
             _eur(rows[m['id']]['soll']), rows[m['id']]['basis'] or '', _eur(rows[m['id']]['paid']), _eur(rows[m['id']]['open']),
             _eur(rows[m['id']]['balance']), 'ja' if m['guest'] else '', 'ja' if m['dues_free'] else '', '; '.join(rows[m['id']]['issues'])]
            for m in store.rows("SELECT * FROM members ORDER BY kv,COALESCE(NULLIF(last_name,''),name) COLLATE NOCASE,first_name COLLATE NOCASE")
            if not kv or m['kv'] == kv]
    return berichte._csv(data, ['Mitgliedsnummer', 'Nachname', 'Vorname', 'Kreisverband', 'Eintritt', 'Austritt', f'Soll {year}', 'Grundlage',
                                f'Gezahlt für {year}', f'Offen {year}', f'Saldo am {german_date(today)} (− Rückstand, + Guthaben)', 'Gastmitglied',
                                'Beitragsfrei', 'Zu klären'])
