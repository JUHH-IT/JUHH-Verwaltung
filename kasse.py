"""Kassenbuch (Bargeld) – Kassenbelege werden wie Bankumsätze verbucht.

Wunsch Lennert Gust 08.11.2026: „die Funktion eine Kasse zu führen, also die Buchungen vorzunehmen. Wir starten in 2026 mit einem
Anfangssaldo. Auch dort muss ich alle Buchungen wie bei der Kontoverbuchung haben.“

Ein Kassenbeleg ist eine Zeile der Tabelle `bank` mit `ledger='cash'` (Belegnummer K-JJJJ-nnnn in `bank_ref`). Damit gilt für
die Kasse genau dieselbe Buchungslogik wie für den Kontoauszug (core.book): Teilbeträge auf Haushaltsposten mit Bereich,
Mitgliedsbeiträge 8100 nur mit Mitglied, Rechnungen, Eingangsrechnungen, Bestandskonten, Parkkonto 1370, Storno mit Neuzuordnung,
Lernbeispiele – nur das Gegenkonto ist die Kasse 1400 statt der Bank 1420. Journal-Quellen bleiben `bank:<id>`.

- Anfangsbestand: einmal je Kassenbuch (Stichtag, z. B. 01.01.2026) als Buchung Kasse an 9100 Eröffnungsbilanz (zählt nicht im
  Haushalt); ändern = alte Buchung gegenbuchen, neue anlegen.
- Geldtransit (Abhebung von der Bank, Einzahlung auf die Bank): Kassenbeleg gegen 2380 Durchlauf-/Übergangskonto; im
  Kontoauszug wird der Gegenumsatz ebenfalls auf 2380 gebucht. Saldo 2380 ≠ 0 heißt: eine Seite fehlt noch.
- Bestand: Anfangsbestand + alle Buchungen auf 1400 (auch aus dem Kontoauszug) + noch nicht verbuchte Kassenbelege. Er darf an
  keinem Tag negativ werden (sonst fehlt eine Einnahme oder Abhebung) – geprüft beim Erfassen und beim Buchen auf 1400.
- Kassenbelege werden nie gelöscht: ein offener Beleg lässt sich mit Grund für ungültig erklären (state 'void'), ein verbuchter
  wird wie ein Bankumsatz storniert.
"""
from datetime import date, datetime

from formate import money, iso

ACCOUNT = '1400'        # Kasse (CDU-Kontenrahmen)
TRANSIT = '2380'        # Durchlauf- und Übergangskonto (Geldtransit Kasse/Bank)
OPENING = '9100'        # Eröffnungsbilanz
SOURCE = 'Kassenbuch'
KINDS = {'in': 'Einnahme', 'out': 'Ausgabe', 'withdraw': 'Abhebung von der Bank', 'deposit': 'Einzahlung auf die Bank'}
MAX_TEXT = 200


def init(db):
    cols = [c[1] for c in db.execute('PRAGMA table_info(bank)')]
    changed = False
    if 'ledger' not in cols:     # additiv (0.25): Kontoauszug oder Kasse
        db.execute("ALTER TABLE bank ADD COLUMN ledger TEXT NOT NULL DEFAULT 'bank' CHECK(ledger IN ('bank','cash'))")
        changed = True
    for col in ('attachment', 'note'):
        if col not in cols:
            db.execute(f'ALTER TABLE bank ADD COLUMN {col} TEXT')
            changed = True
    db.execute('CREATE INDEX IF NOT EXISTS bank_ledger ON bank(ledger,booked)')
    if changed:
        db.commit()


def ledger_account(b):
    """Gegenkonto eines Umsatzes: Kasse für Kassenbelege, sonst Bank."""
    return ACCOUNT if 'ledger' in b.keys() and b['ledger'] == 'cash' else '1420'


def start(store):
    """Beginn des Kassenbuchs (Tag des Anfangsbestands) oder None, solange keine Kasse eingerichtet ist."""
    row = store.db.execute("SELECT value FROM settings WHERE key='cash_start'").fetchone()
    return row[0] if row else None


def _opening_journals(store):
    return store.rows("SELECT j.id,j.day,j.source,j.reversal_of,e.debit-e.credit amount FROM journals j JOIN entries e ON e.journal_id=j.id "
                      "WHERE j.source LIKE 'kasse-anfang:%' AND e.account=? ORDER BY j.id", (ACCOUNT,))


def opening(store):
    """Gültiger Anfangsbestand {'day', 'amount', 'journal'} oder None."""
    rows = _opening_journals(store)
    reversed_ids = {r['reversal_of'] for r in rows if r['reversal_of']}
    live = [r for r in rows if not r['reversal_of'] and r['id'] not in reversed_ids]
    return {'day': live[-1]['day'], 'amount': live[-1]['amount'], 'journal': live[-1]['id']} if live else None


def set_opening(store, day, amount, actor):
    """Anfangsbestand der Kasse festlegen (Kasse an 9100 Eröffnungsbilanz); eine frühere Festlegung wird gegengebucht."""
    day = iso(day)
    cents = money(amount)
    if cents < 0:
        raise ValueError('Der Anfangsbestand der Kasse kann nicht negativ sein.')
    if store.data_start() and day < store.data_start():
        raise ValueError(f'Der Anfangsbestand liegt vor dem Datenbeginn ({store.data_start()}).')
    if day > date.today().isoformat():
        raise ValueError('Der Tag des Anfangsbestands darf nicht in der Zukunft liegen.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        earlier = store.db.execute("SELECT MIN(booked) FROM bank WHERE ledger='cash' AND state!='void'").fetchone()[0]
        if earlier and earlier < day:
            raise ValueError(f'Es gibt schon Kassenbelege vor dem {day} (ab {earlier}) – Tag des Anfangsbestands früher wählen.')
        moved = store.db.execute('SELECT MIN(j.day) FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account=? '
                                 "AND j.source NOT LIKE 'kasse-anfang:%'", (ACCOUNT,)).fetchone()[0]
        if moved and moved < day:
            raise ValueError(f'Auf der Kasse 1400 gibt es schon Buchungen vor dem {day} (ab {moved}) – Tag des Anfangsbestands früher wählen.')
        old = opening(store)
        n = store.db.execute("SELECT COUNT(*) FROM journals WHERE source LIKE 'kasse-anfang:%'").fetchone()[0] + 1
        if old:
            back = store.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?', (old['journal'],))
            store.journal(old['day'], 'Storno Anfangsbestand Kasse', f'kasse-anfang:{n}', back, old['journal'], year=int(old['day'][:4]))
            n += 1
        if cents:
            store.journal(day, 'Anfangsbestand Kasse', f'kasse-anfang:{n}',
                          [{'account': ACCOUNT, 'kv': 'LV', 'debit': cents, 'credit': 0},
                           {'account': OPENING, 'kv': 'LV', 'debit': 0, 'credit': cents, 'title': ''}], year=int(day[:4]))
        store.db.execute("INSERT INTO settings VALUES('cash_start',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (day,))
        _check(store)
        store.audit(actor, 'Anfangsbestand Kasse festgelegt', {'tag': day, 'cents': cents,
                                                               **({'vorher': {'tag': old['day'], 'cents': old['amount']}} if old else {})})


def next_number(store, year):
    """Belegnummer K-JJJJ-nnnn, fortlaufend je Jahr."""
    prefix = f'K-{year}-'
    row = store.db.execute("SELECT MAX(CAST(substr(bank_ref,8) AS INTEGER)) FROM bank WHERE ledger='cash' AND bank_ref LIKE ?", (prefix + '%',)).fetchone()
    return f'{prefix}{(row[0] or 0) + 1:04d}'


def timeline(store, extra=None):
    """Kassenbewegungen in zeitlicher Folge mit Bestand: Buchungen auf 1400 (Kassenbelege, Kontoauszug, Anfangsbestand) und noch
    nicht verbuchte Kassenbelege. extra: zusätzlicher Beleg {'day', 'amount'} zur Prüfung vor dem Erfassen."""
    moves = []
    for r in store.rows('SELECT j.id,j.day,j.source,j.description,e.debit-e.credit amount FROM entries e JOIN journals j ON j.id=e.journal_id '
                        'WHERE e.account=?', (ACCOUNT,)):
        moves.append({'day': r['day'], 'amount': r['amount'], 'journal': r['id'], 'source': r['source'], 'text': r['description']})
    for b in store.rows("SELECT id,booked,amount FROM bank WHERE ledger='cash' AND state='open'"):
        moves.append({'day': b['booked'], 'amount': b['amount'], 'bank_id': b['id'], 'source': None, 'text': ''})
    if extra:
        moves.append({'day': extra['day'], 'amount': extra['amount'], 'source': None, 'text': ''})
    moves.sort(key=lambda m: (m['day'], -m['amount']))      # am selben Tag Eingänge vor Ausgängen
    total = 0
    for m in moves:
        total += m['amount']
        m['balance'] = total
    return moves


def balance(store, until=None):
    """Kassenbestand am Ende eines Tages (Standard: alles)."""
    until = until or '9999-12-31'
    return sum(m['amount'] for m in timeline(store) if m['day'] <= until)


def _check(store, extra=None, storno=False):
    low = next((m for m in timeline(store, extra) if m['balance'] < 0), None)
    if low:
        from formate import german_date, euro
        raise ValueError(f"Der Kassenbestand würde am {german_date(low['day'])} negativ ({euro(low['balance'])}) – "
                         + ('das Bargeld ist schon ausgegeben: zuerst die späteren Kassenausgaben prüfen (ungültig erklären bzw. stornieren).' if storno else
                            'fehlt eine Einnahme, eine Abhebung oder der Anfangsbestand? Datum und Betrag prüfen.'))


def check_after_booking(store, storno=False):
    """Nach einer Buchung auf 1400 (z. B. aus dem Kontoauszug): Bestand darf nie negativ werden – nur bei eingerichteter Kasse."""
    if start(store):
        _check(store, storno=storno)


def create(store, data, actor, attachment_suffix=None):
    """Kassenbeleg erfassen. Einnahme/Ausgabe bleiben offen und werden im Buchungsplatz der Kasse verbucht; Geldtransit wird sofort
    gegen 2380 gebucht. Liefert {'id', 'number', 'filename'}."""
    kind = data.get('kind')
    if kind not in KINDS:
        raise ValueError('Art wählen: Einnahme, Ausgabe, Abhebung oder Einzahlung.')
    day = iso(data.get('day') or date.today().isoformat())
    if day > date.today().isoformat():
        raise ValueError('Das Datum eines Kassenbelegs darf nicht in der Zukunft liegen.')
    cents = money(data.get('amount', ''))
    if cents <= 0:
        raise ValueError('Bitte einen Betrag größer als 0 angeben.')
    amount = cents if kind in ('in', 'withdraw') else -cents
    name = ' '.join(str(data.get('name') or '').split())
    text = ' '.join(str(data.get('text') or '').split())
    if kind in ('in', 'out') and not text:
        raise ValueError('Bitte angeben, wofür (Text des Belegs).')
    if len(name) > MAX_TEXT or len(text) > MAX_TEXT:
        raise ValueError(f'Name und Text höchstens {MAX_TEXT} Zeichen.')
    text = text or KINDS[kind]
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        begin = start(store)                               # in der Transaktion lesen (gleichzeitige Änderung des Beginns)
        if not begin:
            raise ValueError('Zuerst den Anfangsbestand der Kasse festlegen.')
        if day < begin:
            raise ValueError(f'Kassenbelege erst ab Beginn des Kassenbuchs ({begin}).')
        _check(store, {'day': day, 'amount': amount})
        number = next_number(store, day[:4])
        filename = f'{number}{attachment_suffix}' if attachment_suffix else None
        bid = store.db.execute("INSERT INTO bank(booked,amount,name,reference,bank_ref,source,fingerprint,ledger,attachment) "
                               "VALUES(?,?,?,?,?,?,?,'cash',?)", (day, amount, name or None, text, number, f'{SOURCE} · {KINDS[kind]}', f'kasse:{number}',
                                                                  filename)).lastrowid
        store.audit(actor, 'Kassenbeleg erfasst', {'id': bid, 'number': number, 'kind': kind, 'cents': amount, 'tag': day})
        if kind in ('withdraw', 'deposit'):      # Geldtransit: in derselben Transaktion gegen 2380 – scheitert das, gibt es keinen Beleg
            store._book(bid, [{'amount': f'{amount / 100:.2f}', 'account': TRANSIT, 'kv': 'LV'}], actor, confirm_no_bill=True)
    return {'id': bid, 'number': number, 'filename': filename}


def void(store, bid, reason, actor):
    """Offenen Kassenbeleg mit Grund für ungültig erklären (Tippfehler, doppelt erfasst) – er bleibt sichtbar, zählt aber nicht."""
    reason = ' '.join(str(reason or '').split())
    if not reason:
        raise ValueError('Bitte einen Grund angeben.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b = store.db.execute('SELECT * FROM bank WHERE id=?', (int(bid),)).fetchone()
        if not b or b['ledger'] != 'cash':
            raise ValueError('Kassenbeleg nicht gefunden.')
        if b['state'] != 'open':
            raise ValueError('Nur offene Kassenbelege lassen sich für ungültig erklären – einen verbuchten zuerst stornieren.')
        store.db.execute("UPDATE bank SET state='void',note=? WHERE id=?", (reason[:MAX_TEXT], b['id']))
        _check(store)
        store.audit(actor, 'Kassenbeleg ungültig', {'id': b['id'], 'number': b['bank_ref'], 'grund': reason[:MAX_TEXT]})


def summary(store):
    """Kurzüberblick für die Oberfläche: Beginn, Bestand heute, offene Belege, Geldtransit."""
    begin = start(store)
    if not begin:
        return {'start': None}
    return {'start': begin, 'balance': balance(store, date.today().isoformat()), 'transit': transit_open(store),
            'open': store.db.execute("SELECT COUNT(*) FROM bank WHERE ledger='cash' AND state='open'").fetchone()[0],
            'opening': opening(store)}


def transit_open(store, until=None):
    """Saldo des Durchlaufkontos 2380 (Geldtransit) am Ende eines Tages: ≠ 0, solange Abhebung/Einzahlung nur auf einer Seite
    (Kasse oder Kontoauszug) gebucht ist – oder ein anderer Durchlaufposten auf 2380 offen ist."""
    return store.db.execute('SELECT COALESCE(SUM(e.debit-e.credit),0) FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account=? AND j.day<=?',
                            (TRANSIT, until or '9999-12-31')).fetchone()[0]


def _round(source):
    """Zuordnungsrunde einer Journal-Quelle (bank:7 → 0, bank:7/2 → 2)."""
    tail = str(source).split(':', 1)[1]
    return int(tail.split('/')[1]) if '/' in tail else 0


def book(store, year):
    """Kassenbuch eines Jahres: Übertrag, Bewegungen mit Beleg, Kontierung und Bestand; ungültige Belege nur zur Information.
    Reihenfolge nach Tag und Buchung (ein Storno steht unter seiner Buchung); Summen netto je Beleg, Anfangsbestand eigens."""
    from core import bank_source
    year = int(year)
    first, last = f'{year}-01-01', f'{year}-12-31'
    rows = {b['id']: b for b in store.rows("SELECT * FROM bank WHERE ledger='cash'")}
    by_source = {}
    for b in rows.values():
        for r in range(b['reversals'] + 1):
            for kind in ('bank', 'resolve', 'reverse-bank'):
                by_source[bank_source(kind, {'id': b['id'], 'reversals': r})] = b
    accounts = {}
    for e in store.rows("SELECT j.source,e.account,e.kv,e.title,e.credit-e.debit amount FROM entries e JOIN journals j ON j.id=e.journal_id "
                        "WHERE e.account NOT IN (?,'1370') AND (j.source LIKE 'bank:%' OR j.source LIKE 'resolve:%')", (ACCOUNT,)):
        accounts.setdefault(e['source'], []).append(e)
    moves = timeline(store)
    carry = sum(m['amount'] for m in moves if m['day'] < first)
    shown = sorted((m for m in moves if first <= m['day'] <= last),
                   key=lambda m: (m['day'], m.get('journal') or 10 ** 12, m.get('bank_id') or 0))   # offene Belege am Tagesende
    lines, balance, net = [], carry, {}
    opening_sum = 0
    for m in shown:
        balance += m['amount']
        source = m['source']
        b = rows.get(m['bank_id']) if m.get('bank_id') else by_source.get(source)
        if source and source.startswith('kasse-anfang:'):
            kind = 'anfang'
            opening_sum += m['amount']
        elif b is not None:
            kind = 'beleg'
            net[('beleg', b['id'])] = net.get(('beleg', b['id']), 0) + m['amount']
        else:
            kind = 'bank'                                      # Buchung auf 1400 aus dem Kontoauszug
            net[('bank', source)] = net.get(('bank', source), 0) + m['amount']
        storno = bool(source and source.startswith('reverse-bank:'))
        if b is None or not source:
            state = 'open' if b is not None else 'booked'
        elif storno:
            state = 'storno'
        elif _round(source) < b['reversals']:
            state = 'reversed'                                 # diese Zuordnung wurde später storniert
        else:
            state = b['state']
        posted = []
        if source and not storno:
            posted = accounts.get(source, []) + (accounts.get('resolve:' + source.split(':', 1)[1], []) if source.startswith('bank:') else [])
        lines.append({'day': m['day'], 'amount': m['amount'], 'balance': balance, 'kind': kind,
                      'number': b['bank_ref'] if b is not None else None, 'bank_id': b['id'] if b is not None else None,
                      'name': b['name'] if b is not None else None, 'text': (b['reference'] if b is not None else m['text']),
                      'state': state, 'attachment': b['attachment'] if b is not None else None, 'journal': m.get('journal'), 'source': source,
                      'posted': [{'account': e['account'], 'kv': e['kv'], 'title': e['title'], 'amount': e['amount']} for e in posted],
                      'storno': storno})
    voided = [b for b in rows.values() if b['state'] == 'void' and b['booked'][:4] == str(year)]
    in_year = [b for b in rows.values() if b['booked'][:4] == str(year)]
    return {'year': year, 'start': start(store), 'opening': opening(store), 'carry': carry, 'opening_in_year': opening_sum, 'lines': lines,
            'end': balance, 'income': sum(v for v in net.values() if v > 0), 'expense': -sum(v for v in net.values() if v < 0),
            'void': sorted(voided, key=lambda b: b['booked']), 'today': balance_today(store),
            'transit': transit_open(store, min(last, date.today().isoformat())),
            'open': sum(1 for b in in_year if b['state'] == 'open'), 'parked': sum(1 for b in in_year if b['state'] == 'parked'),
            'missing_receipt': sum(1 for b in in_year if b['state'] != 'void' and not b['attachment'] and b['amount'] < 0 and not is_transit(b))}


def balance_today(store):
    return balance(store, date.today().isoformat())


def is_transit(b):
    """Geldtransit (Abhebung/Einzahlung) – braucht keinen eigenen Beleg; die Gegenseite steht im Kontoauszug."""
    return str(b['source'] or '').endswith((KINDS['withdraw'], KINDS['deposit']))


STATES = {'open': 'offen', 'parked': 'geparkt', 'booked': 'verbucht', 'reversed': 'verbucht, später storniert', 'storno': 'Storno'}


def csv(store, year):
    """Kassenbuch als CSV (Kassenprüfung)."""
    import berichte
    from formate import german_date
    b = book(store, year)
    rows = [['', f'01.01.{year}', 'Übertrag', '', '', '', '', berichte._eur(b['carry']), '', '']]
    for x in b['lines']:
        rows.append([x['number'] or '', german_date(x['day']), {'anfang': 'Anfangsbestand', 'bank': 'aus Kontoauszug'}.get(x['kind'], 'Kassenbeleg'),
                     x['name'] or '', x['text'] or '', berichte._eur(x['amount']) if x['amount'] > 0 else '',
                     berichte._eur(-x['amount']) if x['amount'] < 0 else '', berichte._eur(x['balance']),
                     '; '.join(f"{p['account']}/{p['kv']}" + (f"/{p['title']}" if p['title'] else '') for p in x['posted']),
                     STATES.get(x['state'], x['state'])])
    for v in b['void']:
        rows.append([v['bank_ref'], german_date(v['booked']), 'ungültig', v['name'] or '', v['reference'] or '', '', '', '', '', f"ungültig: {v['note']}"])
    return berichte._csv(rows, ['Beleg', 'Datum', 'Art', 'Name', 'Text', 'Einnahme', 'Ausgabe', 'Bestand', 'Kontierung (Konto/Bereich/Titel)', 'Stand'])
