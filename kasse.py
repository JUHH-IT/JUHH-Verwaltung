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
  Kontoauszug wird der Gegenumsatz ebenfalls auf 2380 gebucht. Fehlt er, ist der Beleg ein offener Posten (`transit`).
- Bestand: Anfangsbestand + alle Buchungen auf 1400 (auch aus dem Kontoauszug) + noch nicht verbuchte Kassenbelege. Er darf an
  keinem Tag negativ werden (sonst fehlt eine Einnahme oder Abhebung) – geprüft beim Erfassen und beim Buchen auf 1400.
- Kassenbelege werden nie gelöscht: ein offener Beleg lässt sich mit Grund für ungültig erklären (state 'void'), ein verbuchter
  wird wie ein Bankumsatz storniert.

Mehrere Kassen (ab 0.60, Wunsch Lennert Gust 23.11.2026: „Wir haben hier z. B. auch ein PayPal-Konto. Das sollten wir ja wie eine
Kasse pflegen“): Alles oben gilt je Kasse. Die Bargeldkasse ist fest (`BAR`: Kennung 0, Konto 1400, Kürzel K, Beginn wie seit 0.25
in der Einstellung `cash_start`) – bestehende Daten laufen ohne Umstellung weiter. Jede weitere Kasse (`add`, z. B. PayPal) steht in
der Tabelle `kassen` mit eigenem Bestandskonto 1401–1499 (wird dabei im Kontenrahmen angelegt), eigenem Belegkürzel (`PP-JJJJ-nnnn`)
und eigenem Beginn. Ihre Belege bleiben Zeilen in `bank` mit `ledger='cash'`; die Spalte `bank.kasse` nennt die Kasse (0 = Bargeld).
Ein Kassenkonto ist wie 1400 nur aus dem Kontoauszug direkt bebuchbar (im Zeitraum des Kassenbuchs, Bestand nie negativ), nie aus
dem Beleg einer Kasse – zwischen Kasse und Bank läuft das Geld über 2380, von einer Kasse in eine andere über die Bank (zwei Belege,
zwei Kontoumsätze). 2380 ist ein Konto für alle; „Geldtransit offen“ ist deshalb kein Saldo, sondern eine Zuordnung (`transit`): jeder
Beleg einer Kasse auf 2380 braucht seinen Kontoumsatz auf 2380 (gleicher Betrag, Gegenrichtung) – zwei Kassen gleichen sich nie
gegenseitig aus.

Art, Umbenennen, Stilllegen (ab 0.60, Antworten Lennert Gust 23.11.2026, REGELN Nr. 37): Jede weitere Kasse hat eine Art (`ARTEN`,
fest ab dem Einrichten). „bar“ ist ein Bargeldbestand: nie negativ, Geld von und zur Bank über 2380 – wie oben. „dienst“ ist ein Konto
bei einem Zahlungsdienst (PayPal), der jede Zahlung selbst vom Bankkonto einzieht („ungerne mit einem Transitkonto“): keine Belege auf
2380 (`kinds`), die Abbuchung im Kontoauszug wird direkt auf das Konto der Kasse gebucht, und der Bestand darf im Buch im Minus
stehen, bis der Einzug gebucht ist (`_check` prüft nur „bar“). Weitere Kassen lassen sich umbenennen (`rename`, nur der Name),
stilllegen und wieder aktivieren (`set_active`, nur bei Bestand 0 ohne offene Posten; stillgelegt nimmt sie nichts mehr an, bleibt
aber in Kassenbuch und Berichten der Jahre mit Bewegungen, `bewegt`) und löschen, solange es keinen Beleg und keine Buchung gibt
(`delete`). Die Bargeldkasse ist fest.

Welche Kasse: Von außen (Anfrage) zählt nur die Kennung (`kennung`, `eine`) – alles andere ist „Kasse nicht gefunden“. Intern darf
statt der Kennung der Eintrag aus `liste` weitergegeben werden; er hat den eigenen Typ `Kasse`, den keine Anfrage bilden kann.
"""
import re
from datetime import date, datetime

from formate import money, iso

ACCOUNT = '1400'        # Kasse (CDU-Kontenrahmen)
TRANSIT = '2380'        # Durchlauf- und Übergangskonto (Geldtransit Kasse/Bank)
OPENING = '9100'        # Eröffnungsbilanz
SOURCE = 'Kassenbuch'
KINDS = {'in': 'Einnahme', 'out': 'Ausgabe', 'withdraw': 'Abhebung von der Bank', 'deposit': 'Einzahlung auf die Bank'}
KINDS_OTHER = {**KINDS, 'withdraw': 'Umbuchung von der Bank', 'deposit': 'Umbuchung auf die Bank'}   # weitere Kassen (PayPal): kein Bargeld
MAX_TEXT = 200
# Art einer Kasse: bar = Bestand nie negativ, Geld von/zur Bank über 2380; dienst = Zahlungsdienst zieht selbst vom Bankkonto ein
ARTEN = {'bar': 'Bargeld', 'dienst': 'Konto bei einem Zahlungsdienst'}
BAR = {'id': 0, 'name': 'Bargeldkasse', 'account': ACCOUNT, 'prefix': 'K', 'art': 'bar', 'aktiv': 1}
RESERVED = ('E', 'B', 'R')   # Nummernkreise der Eingangsrechnungen, Beitrags- und Ausgangsrechnungen
VERWECHSELBAR = ('kasse', 'bank')   # stehen in Texten für die Bargeldkasse bzw. das Bankkonto („Bargeldkasse“ heißt schon eine Kasse)


class Kasse(dict):
    """Eintrag einer Kasse – entsteht nur in `liste`. Daten einer Anfrage (JSON) sind nie von diesem Typ."""


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
    if 'kasse' not in cols:      # additiv (0.60): welche Kasse – 0 = Bargeldkasse, sonst kassen.id
        db.execute('ALTER TABLE bank ADD COLUMN kasse INTEGER NOT NULL DEFAULT 0')
        changed = True
    db.execute('CREATE INDEX IF NOT EXISTS bank_ledger ON bank(ledger,booked)')
    db.execute('CREATE TABLE IF NOT EXISTS kassen(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,account TEXT NOT NULL UNIQUE,'
               "prefix TEXT NOT NULL UNIQUE,start TEXT NOT NULL,art TEXT NOT NULL DEFAULT 'bar',aktiv INTEGER NOT NULL DEFAULT 1)")
    have = [c[1] for c in db.execute('PRAGMA table_info(kassen)')]
    for col, kind in (('art', "TEXT NOT NULL DEFAULT 'bar'"), ('aktiv', 'INTEGER NOT NULL DEFAULT 1')):   # additiv: Tabelle aus einem früheren Stand von 0.60
        if col not in have:
            db.execute(f'ALTER TABLE kassen ADD COLUMN {col} {kind}')
            changed = True
    if changed:
        db.commit()


def liste(store):
    """Alle Kassen, die Bargeldkasse zuerst: je Kasse {'id', 'name', 'account', 'prefix', 'start', 'art', 'aktiv'} (start None = noch nicht
    eingerichtet, art siehe `ARTEN`, aktiv 0 = stillgelegt)."""
    row = store.db.execute("SELECT value FROM settings WHERE key='cash_start'").fetchone()
    return [Kasse(BAR, start=row[0] if row else None)] + [Kasse(r) for r in store.rows('SELECT id,name,account,prefix,start,art,aktiv FROM kassen ORDER BY id')]


def kennung(wert):
    """Kennung einer Kasse aus einer Eingabe: leer = Bargeldkasse (0), sonst eine ganze Zahl (auch als Ziffernfolge) – alles andere
    (Objekt, Liste, Kommazahl, Wahrheitswert, Text) ist keine Kasse."""
    if wert is None or wert == '':
        return 0
    if isinstance(wert, int) and not isinstance(wert, bool) and wert >= 0:
        return wert
    if isinstance(wert, str) and re.fullmatch(r'[0-9]{1,9}', wert):
        return int(wert)
    raise ValueError('Kasse nicht gefunden.')


def eine(store, kasse=0):
    """Die Kasse zu einer Kennung (leer = Bargeldkasse) – gibt es sie nicht oder ist es keine Kennung: „Kasse nicht gefunden“."""
    kid = kennung(kasse)
    found = next((k for k in liste(store) if k['id'] == kid), None)
    if not found:
        raise ValueError('Kasse nicht gefunden.')
    return found


def _k(store, kasse):
    # interner Weg: ein Eintrag aus `liste` gilt, wie er ist – alles andere wird als Kennung geprüft
    return kasse if isinstance(kasse, Kasse) else eine(store, kasse)


def kinds(k):
    """Arten eines Belegs mit den Wörtern der Kasse: Bargeld „Abhebung/Einzahlung“, sonst „Umbuchung von der/auf die Bank“ – bei
    einem Zahlungsdienst (PayPal) nur Einnahme und Ausgabe: sein Geld läuft nie über 2380."""
    if k['art'] == 'dienst':
        return {kind: KINDS[kind] for kind in ('in', 'out')}
    return KINDS_OTHER if k['id'] else KINDS


def _aktiv(k):
    if not k['aktiv']:
        raise ValueError(f"{k['name']} ist stillgelegt – zuerst wieder aktivieren (Seite Kasse → Kassen verwalten).")


def _weitere(store, kasse):
    k = eine(store, kasse)
    if not k['id']:
        raise ValueError('Die Bargeldkasse ist fest – sie lässt sich nicht umbenennen, stilllegen oder löschen.')
    return k


def _name(store, raw, ausser=None):
    """Name einer weiteren Kasse prüfen (Einrichten, Umbenennen) – ausser: Kennung der Kasse, die ihn schon trägt."""
    name = ' '.join(str(raw or '').split())
    if not re.fullmatch(r'[A-Za-zÄÖÜäöüß0-9 .&+-]{2,40}', name) or not re.search(r'[A-Za-zÄÖÜäöüß]', name):   # steht in Dateinamen und Buchungstexten
        raise ValueError('Bitte einen Namen mit 2 bis 40 Zeichen und mindestens einem Buchstaben angeben, z. B. PayPal '
                         '(Buchstaben, Ziffern, Leerzeichen und . - & +).')
    if name.lower() in VERWECHSELBAR:
        raise ValueError(f'„{name}“ steht schon für die Bargeldkasse bzw. das Bankkonto – bitte einen eigenen Namen wählen, z. B. PayPal.')
    if any(k['name'].lower() == name.lower() and k['id'] != ausser for k in liste(store)):
        raise ValueError(f'Eine Kasse „{name}“ gibt es schon.')
    return name


def _label(k):
    return k['name'] if k['id'] else 'Kasse'    # Bargeldkasse: Texte wie seit 0.25


def ledger_account(store, b):
    """Gegenkonto eines Umsatzes: das Konto der Kasse für Kassenbelege, sonst Bank."""
    return eine(store, b['kasse'])['account'] if 'ledger' in b.keys() and b['ledger'] == 'cash' else '1420'


def start(store, kasse=0):
    """Beginn des Kassenbuchs (Tag des Anfangsbestands) oder None, solange die Kasse nicht eingerichtet ist."""
    return _k(store, kasse)['start']


def _opening_journals(store, k):
    return store.rows("SELECT j.id,j.day,j.source,j.reversal_of,e.debit-e.credit amount FROM journals j JOIN entries e ON e.journal_id=j.id "
                      "WHERE j.source LIKE 'kasse-anfang:%' AND e.account=? ORDER BY j.id", (k['account'],))


def opening(store, kasse=0):
    """Gültiger Anfangsbestand {'day', 'amount', 'journal'} oder None."""
    rows = _opening_journals(store, _k(store, kasse))
    reversed_ids = {r['reversal_of'] for r in rows if r['reversal_of']}
    live = [r for r in rows if not r['reversal_of'] and r['id'] not in reversed_ids]
    return {'day': live[-1]['day'], 'amount': live[-1]['amount'], 'journal': live[-1]['id']} if live else None


def set_opening(store, day, amount, actor, kasse=0):
    """Anfangsbestand einer Kasse festlegen (Kasse an 9100 Eröffnungsbilanz); eine frühere Festlegung wird gegengebucht."""
    day = iso(day)
    cents = money(amount)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        _set_opening(store, _k(store, kasse), day, cents, actor)


def _set_opening(store, k, day, cents, actor):
    """Anfangsbestand buchen und Beginn festhalten – innerhalb einer offenen Transaktion (set_opening, add)."""
    label = _label(k)
    _aktiv(k)
    if cents < 0:
        raise ValueError('Der Anfangsbestand der Kasse kann nicht negativ sein.')
    if store.data_start() and day < store.data_start():
        raise ValueError(f'Der Anfangsbestand liegt vor dem Datenbeginn ({store.data_start()}).')
    if day > date.today().isoformat():
        raise ValueError('Der Tag des Anfangsbestands darf nicht in der Zukunft liegen.')
    earlier = store.db.execute("SELECT MIN(booked) FROM bank WHERE ledger='cash' AND kasse=? AND state!='void'", (k['id'],)).fetchone()[0]
    if earlier and earlier < day:
        raise ValueError(f'Es gibt schon Kassenbelege vor dem {day} (ab {earlier}) – Tag des Anfangsbestands früher wählen.')
    moved = store.db.execute('SELECT MIN(j.day) FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account=? '
                             "AND j.source NOT LIKE 'kasse-anfang:%'", (k['account'],)).fetchone()[0]
    if moved and moved < day:
        raise ValueError(f"Auf der Kasse {k['account']} gibt es schon Buchungen vor dem {day} (ab {moved}) – Tag des Anfangsbestands früher wählen.")
    old = opening(store, k)
    n = store.db.execute("SELECT COUNT(*) FROM journals WHERE source LIKE 'kasse-anfang:%'").fetchone()[0] + 1   # fortlaufend über alle Kassen
    if old:
        back = store.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?', (old['journal'],))
        store.journal(old['day'], f'Storno Anfangsbestand {label}', f'kasse-anfang:{n}', back, old['journal'], year=int(old['day'][:4]))
        n += 1
    if cents:
        store.journal(day, f'Anfangsbestand {label}', f'kasse-anfang:{n}',
                      [{'account': k['account'], 'kv': 'LV', 'debit': cents, 'credit': 0},
                       {'account': OPENING, 'kv': 'LV', 'debit': 0, 'credit': cents, 'title': ''}], year=int(day[:4]))
    if k['id']:
        store.db.execute('UPDATE kassen SET start=? WHERE id=?', (day, k['id']))
    else:
        store.db.execute("INSERT INTO settings VALUES('cash_start',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (day,))
    _check(store, Kasse(k, start=day))
    store.audit(actor, 'Anfangsbestand Kasse festgelegt', {'tag': day, 'cents': cents, **({'kasse': k['name']} if k['id'] else {}),
                                                           **({'vorher': {'tag': old['day'], 'cents': old['amount']}} if old else {})})


def free_account(store):
    """Vorschlag für das Bestandskonto einer weiteren Kasse: das erste freie ab 1430 (darunter Kasse, Portokasse, Bankkonten), sonst ab 1401."""
    have = {r[0] for r in store.db.execute("SELECT code FROM accounts WHERE code BETWEEN '1400' AND '1499'")}
    return next((c for c in (str(n) for n in (*range(1430, 1500), *range(1401, 1430))) if c not in have), '')


def add(store, data, actor):
    """Weitere Kasse einrichten (z. B. PayPal): Name, Art (`ARTEN`, ohne Angabe „bar“ = die strenge), eigenes Bestandskonto 1401–1499
    (wird angelegt, darf es noch nicht geben), Belegkürzel, Stichtag und Anfangsbestand. Liefert die Kennung."""
    account = str(data.get('account') or '').strip()
    prefix = str(data.get('prefix') or '').strip().upper()
    art = data.get('art') or 'bar'
    if not isinstance(art, str) or art not in ARTEN:
        raise ValueError('Bitte die Art wählen: Bargeld oder Konto bei einem Zahlungsdienst (z. B. PayPal).')
    if not re.fullmatch(r'14[0-9][0-9]', account) or account == ACCOUNT:
        raise ValueError('Das Konto der Kasse muss im Bereich 1401–1499 (Geldbestände) liegen.')
    if not re.fullmatch(r'[A-Z]{1,4}', prefix):
        raise ValueError('Belegkürzel: 1 bis 4 Buchstaben, z. B. PP.')
    day = iso(data.get('day') or '')
    cents = money(data.get('amount') or '0')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        name = _name(store, data.get('name'))
        if prefix in RESERVED or any(k['prefix'] == prefix for k in liste(store)):
            raise ValueError(f'Das Belegkürzel {prefix} ist schon vergeben – bitte ein anderes wählen.')
        taken = store.db.execute('SELECT name FROM accounts WHERE code=?', (account,)).fetchone()
        if taken:
            raise ValueError(f"Das Konto {account} gibt es schon ({taken['name']}) – bitte ein freies wählen, z. B. {free_account(store)}.")
        store.db.execute("INSERT INTO accounts VALUES(?,?,'asset')", (account, name))
        kid = store.db.execute('INSERT INTO kassen(name,account,prefix,start,art) VALUES(?,?,?,?,?)', (name, account, prefix, day, art)).lastrowid
        store.audit(actor, 'Kasse eingerichtet', {'id': kid, 'name': name, 'konto': account, 'kuerzel': prefix, 'art': art})
        _set_opening(store, eine(store, kid), day, cents, actor)
    return kid


def rename(store, kasse, name, actor):
    """Weitere Kasse umbenennen – nur der Name (Konto und Belegkürzel bleiben); der Kontoname im Kontenrahmen zieht mit."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        k = _weitere(store, kasse)
        name = _name(store, name, k['id'])
        store.db.execute('UPDATE kassen SET name=? WHERE id=?', (name, k['id']))
        store.db.execute('UPDATE accounts SET name=? WHERE code=?', (name, k['account']))
        store.audit(actor, 'Kasse umbenannt', {'id': k['id'], 'vorher': k['name'], 'name': name})


def set_active(store, kasse, aktiv, actor):
    """Weitere Kasse stilllegen (aktiv False: nur bei Bestand 0, ohne offene oder geparkte Belege und ohne offenen Geldtransit) oder
    wieder aktivieren. Stillgelegt nimmt sie keine Belege und Buchungen mehr an, bleibt aber in Kassenbuch und Berichten."""
    from formate import euro
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        k = _weitere(store, kasse)
        if not aktiv:
            offen = store.db.execute("SELECT COUNT(*) FROM bank WHERE ledger='cash' AND kasse=? AND state IN ('open','parked')", (k['id'],)).fetchone()[0]
            if offen:
                raise ValueError(f"{k['name']} hat noch {offen} offene oder geparkte Belege – zuerst verbuchen oder für ungültig erklären.")
            if transit(store)['kassen'].get(k['id']):
                raise ValueError(f"{k['name']} hat noch Belege im Geldtransit {TRANSIT} ohne Gegenseite im Kontoauszug – zuerst den Kontoumsatz auf {TRANSIT} buchen.")
            stand = balance(store, kasse=k)
            if stand:
                raise ValueError(f"Der Bestand von {k['name']} ist nicht 0 ({euro(stand)}) – zuerst ausgleichen: den Kontoumsatz dazu im "
                                 f"Kontoauszug auf Konto {k['account']} buchen.")
        store.db.execute('UPDATE kassen SET aktiv=? WHERE id=?', (1 if aktiv else 0, k['id']))
        store.audit(actor, 'Kasse wieder aktiviert' if aktiv else 'Kasse stillgelegt', {'id': k['id'], 'name': k['name']})


def delete(store, kasse, actor):
    """Weitere Kasse löschen – nur solange es keinen einzigen Beleg und keine Buchung auf ihrem Konto gibt; das Konto verschwindet mit."""
    import sqlite3
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        k = _weitere(store, kasse)
        if (store.db.execute("SELECT 1 FROM bank WHERE ledger='cash' AND kasse=? LIMIT 1", (k['id'],)).fetchone()
                or store.db.execute('SELECT 1 FROM entries WHERE account=? LIMIT 1', (k['account'],)).fetchone()):
            raise ValueError(f"{k['name']} hat schon Belege oder Buchungen – Gebuchtes wird nie gelöscht. Stattdessen stilllegen.")
        store.db.execute('DELETE FROM kassen WHERE id=?', (k['id'],))
        try:
            store.db.execute('DELETE FROM accounts WHERE code=?', (k['account'],))
        except sqlite3.IntegrityError:
            raise ValueError(f"Das Konto {k['account']} wird noch verwendet – {k['name']} stattdessen stilllegen.") from None
        store.audit(actor, 'Kasse gelöscht', {'id': k['id'], 'name': k['name'], 'konto': k['account']})


def bewegt(store, kasse, year):
    """Hatte die Kasse im Jahr Bewegungen (Belege, auch ungültige, oder Buchungen auf ihrem Konto)? Eine stillgelegte Kasse steht nur
    in den Berichten solcher Jahre."""
    k, y = _k(store, kasse), str(year)
    return bool(store.db.execute("SELECT 1 FROM bank WHERE ledger='cash' AND kasse=? AND substr(booked,1,4)=? LIMIT 1", (k['id'], y)).fetchone()
                or store.db.execute('SELECT 1 FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account=? AND substr(j.day,1,4)=? LIMIT 1',
                                    (k['account'], y)).fetchone())


def next_number(store, year, kasse=0):
    """Belegnummer <Kürzel>-JJJJ-nnnn (Bargeld: K-JJJJ-nnnn), fortlaufend je Kasse und Jahr."""
    k = _k(store, kasse)
    prefix = f"{k['prefix']}-{year}-"
    row = store.db.execute("SELECT MAX(CAST(substr(bank_ref,?) AS INTEGER)) FROM bank WHERE ledger='cash' AND kasse=? AND bank_ref LIKE ?",
                           (len(prefix) + 1, k['id'], prefix + '%')).fetchone()
    return f'{prefix}{(row[0] or 0) + 1:04d}'


def timeline(store, extra=None, kasse=0):
    """Bewegungen einer Kasse in zeitlicher Folge mit Bestand: Buchungen auf ihrem Konto (Kassenbelege, Kontoauszug, Anfangsbestand)
    und noch nicht verbuchte Kassenbelege. extra: zusätzlicher Beleg {'day', 'amount'} zur Prüfung vor dem Erfassen."""
    k = _k(store, kasse)
    moves = []
    for r in store.rows('SELECT j.id,j.day,j.source,j.description,e.debit-e.credit amount FROM entries e JOIN journals j ON j.id=e.journal_id '
                        'WHERE e.account=?', (k['account'],)):
        moves.append({'day': r['day'], 'amount': r['amount'], 'journal': r['id'], 'source': r['source'], 'text': r['description']})
    for b in store.rows("SELECT id,booked,amount FROM bank WHERE ledger='cash' AND kasse=? AND state='open'", (k['id'],)):
        moves.append({'day': b['booked'], 'amount': b['amount'], 'bank_id': b['id'], 'source': None, 'text': ''})
    if extra:
        moves.append({'day': extra['day'], 'amount': extra['amount'], 'source': None, 'text': ''})
    moves.sort(key=lambda m: (m['day'], -m['amount']))      # am selben Tag Eingänge vor Ausgängen
    total = 0
    for m in moves:
        total += m['amount']
        m['balance'] = total
    return moves


def balance(store, until=None, kasse=0):
    """Bestand einer Kasse am Ende eines Tages (Standard: alles)."""
    until = until or '9999-12-31'
    return sum(m['amount'] for m in timeline(store, kasse=kasse) if m['day'] <= until)


def plus_frage(k):
    """Frage zum Bestand im Plus (`k`: Kasse mit `balance`), sonst '' – nur beim Zahlungsdienst (PayPal): er zieht jede Zahlung
    selbst vom Bankkonto ein, Guthaben liegt dort gewöhnlich keins. Steht der Bestand im Buch im Plus, ist der Einzug gebucht, aber
    der Beleg der Zahlung fehlt (dann fehlt die Ausgabe im Haushalt) – oder es liegt wirklich Guthaben dort. Hinweis, kein Fehler."""
    return f"Fehlt ein Beleg für eine Zahlung, oder liegt Guthaben bei {k['name']}?" if k['art'] == 'dienst' and k['balance'] > 0 else ''


def _check(store, k, extra=None, storno=False):
    if k['art'] != 'bar':      # Zahlungsdienst (PayPal): der Einzug vom Bankkonto folgt der Zahlung – im Buch bis dahin im Minus
        return
    low = next((m for m in timeline(store, extra, k) if m['balance'] < 0), None)
    if low:
        from formate import german_date, euro
        what, money_, move = (('Der Kassenbestand', 'das Bargeld', 'eine Abhebung') if not k['id'] else
                              (f"Der Bestand der Kasse {k['name']}", 'das Geld', 'eine Umbuchung von der Bank'))
        raise ValueError(f"{what} würde am {german_date(low['day'])} negativ ({euro(low['balance'])}) – "
                         + (f'{money_} ist schon ausgegeben: zuerst die späteren Kassenausgaben prüfen (ungültig erklären bzw. stornieren).' if storno else
                            f'fehlt eine Einnahme, {move} oder der Anfangsbestand? Datum und Betrag prüfen.'))


def check_parts(store, b, parts):
    """Vor dem Verbuchen: Kontierung direkt auf ein Kassenkonto (z. B. Bareinzahlung im Kontoauszug auf 1400) – nur aus dem
    Kontoauszug und nur im Zeitraum des Kassenbuchs, sonst zählte das Geld doppelt."""
    hit = {p.get('account') for p in parts if not p.get('bill_id') and not p.get('invoice_id')}
    for k in liste(store):
        if k['account'] not in hit:
            continue
        if b['ledger'] == 'cash':
            raise ValueError(f"Ein Kassenbeleg wird nicht direkt auf eine andere Kasse ({k['account']} {k['name']}) gebucht – "
                             f'auf beiden Seiten einen Beleg erfassen und jeweils auf {TRANSIT} (Geldtransit) buchen.')
        if not k['start']:
            raise ValueError(f"Zuerst die Kasse einrichten (Seite Kasse → Anfangsbestand), dann auf {k['account']} buchen.")
        if b['booked'] < k['start']:
            raise ValueError(f"Buchungen auf die Kasse erst ab Beginn des Kassenbuchs ({k['start']}).")


def check_after_booking(store, entries, storno=False):
    """Nach einer Buchung auf ein Kassenkonto (Kassenbeleg, Kontoauszug, Storno): der Bestand dieser Kasse darf nie negativ werden
    (Art „bar“, nur bei eingerichteter Kasse); eine stillgelegte Kasse nimmt keine Buchung an – auch kein Storno auf 2380, nach dem
    einer ihrer Belege im Geldtransit wieder ohne Kontoumsatz dastünde (stillgelegt heißt: kein offener Geldtransit)."""
    hit = {e['account'] for e in entries}
    offen = transit(store)['kassen'] if storno and TRANSIT in hit else {}
    for k in liste(store):
        if k['account'] in hit or offen.get(k['id']):
            _aktiv(k)
        if k['account'] in hit and k['start']:
            _check(store, k, storno=storno)


def create(store, data, actor, attachment_suffix=None):
    """Kassenbeleg erfassen. Einnahme/Ausgabe bleiben offen und werden im Buchungsplatz der Kasse verbucht; Geldtransit wird sofort
    gegen 2380 gebucht. Liefert {'id', 'number', 'filename'}."""
    kind = data.get('kind')
    if kind not in KINDS:
        raise ValueError('Art wählen: Einnahme, Ausgabe oder Geldtransit (von der Bank bzw. auf die Bank).')
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
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        k = eine(store, data.get('kasse'))                 # in der Transaktion lesen (gleichzeitige Änderung des Beginns)
        _aktiv(k)
        if kind not in kinds(k):
            raise ValueError(f"Bei {k['name']} gibt es keine Umbuchung über {TRANSIT}: Die Abbuchung bzw. Gutschrift im Kontoauszug direkt auf "
                             f"Konto {k['account']} buchen.")
        begin, label = k['start'], kinds(k)[kind]
        if not begin:
            raise ValueError('Zuerst den Anfangsbestand der Kasse festlegen.')
        if day < begin:
            raise ValueError(f'Kassenbelege erst ab Beginn des Kassenbuchs ({begin}).')
        _check(store, k, {'day': day, 'amount': amount})
        number = next_number(store, day[:4], k)
        filename = f'{number}{attachment_suffix}' if attachment_suffix else None
        bid = store.db.execute("INSERT INTO bank(booked,amount,name,reference,bank_ref,source,fingerprint,ledger,attachment,kasse) "
                               "VALUES(?,?,?,?,?,?,?,'cash',?,?)", (day, amount, name or None, text or label, number,
                                                                    f"{k['name'] if k['id'] else SOURCE} · {label}", f'kasse:{number}', filename, k['id'])).lastrowid
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
        _check(store, eine(store, b['kasse']))
        store.audit(actor, 'Kassenbeleg ungültig', {'id': b['id'], 'number': b['bank_ref'], 'grund': reason[:MAX_TEXT]})


def summary(store):
    """Kurzüberblick für die Oberfläche: je Kasse Beginn, Bestand heute, offene Belege, Anfangsbestand, ihre offenen Posten im
    Geldtransit (`transit`) und `plus` (`plus_frage`); dazu offene Belege aller Kassen, die Kontoumsätze auf 2380 ohne Kassenbeleg
    mit dem Saldo 2380 und der Kontovorschlag für eine weitere Kasse."""
    open_ = dict(store.db.execute("SELECT kasse,COUNT(*) FROM bank WHERE ledger='cash' AND state='open' GROUP BY kasse").fetchall())
    t = transit(store)
    kassen = [{**k, 'balance': balance_today(store, k) if k['start'] else 0, 'open': open_.get(k['id'], 0),
               'opening': opening(store, k) if k['start'] else None, 'transit': t['kassen'].get(k['id'], [])} for k in liste(store)]
    for k in kassen:
        k['plus'] = plus_frage(k)
    return {'kassen': kassen, 'open': sum(k['open'] for k in kassen), 'next_account': free_account(store),
            'transit': {'saldo': t['saldo'], 'bank': t['bank']}}


def transit_open(store, until=None):
    """Saldo des Durchlaufkontos 2380 am Ende eines Tages – nur eine Zahl: gegenläufige offene Posten heben sich darin auf.
    Was offen ist, sagt `transit`."""
    return store.db.execute('SELECT COALESCE(SUM(e.debit-e.credit),0) FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account=? AND j.day<=?',
                            (TRANSIT, until or '9999-12-31')).fetchone()[0]


def transit(store, until=None):
    """Offene Posten des Geldtransits 2380 am Ende eines Tages – zugeordnet, nicht saldiert. Ein Posten ist ein Beleg einer Kasse
    bzw. ein Kontoumsatz, der (nach Abzug seiner Storni) auf 2380 gebucht ist. Ein Beleg ist erst ausgeglichen, wenn es im Kontoauszug
    einen Umsatz auf 2380 mit gleichem Betrag in Gegenrichtung gibt (bei mehreren der zeitlich nächste) – nie durch den Beleg einer
    anderen Kasse. Liefert {'saldo', 'kassen': {Kennung: [Belege ohne Kontoumsatz]}, 'bank': [Kontoumsätze ohne Beleg – anderer
    Durchlaufposten oder Kassenbeleg fehlt; erst ab Beginn des ersten Kassenbuchs]}, je Posten {'id', 'day', 'number', 'text',
    'amount'} (Betrag aus Sicht der Kasse bzw. der Bank)."""
    until = until or '9999-12-31'
    begin = min((k['start'] for k in liste(store) if k['start']), default=None)
    result = {'saldo': transit_open(store, until) if begin else 0, 'kassen': {}, 'bank': []}
    if not begin:
        return result
    rows = store.rows("SELECT b.id,b.booked day,b.bank_ref number,COALESCE(b.reference,b.name,'') text,b.ledger,b.kasse,SUM(e.credit-e.debit) amount "
                      'FROM entries e JOIN journals j ON j.id=e.journal_id '
                      "JOIN bank b ON b.id=CAST(substr(j.source,instr(j.source,':')+1) AS INTEGER) "   # bank:7, resolve:7/1, reverse-bank:7 → 7
                      "WHERE e.account=? AND j.day<=? AND (j.source LIKE 'bank:%' OR j.source LIKE 'resolve:%' OR j.source LIKE 'reverse-bank:%') "
                      'GROUP BY b.id HAVING SUM(e.credit-e.debit)!=0 ORDER BY b.booked,b.id', (TRANSIT, until))
    rows = [r for r in rows if r['ledger'] == 'cash' or r['day'] >= begin]    # Kontoumsätze vor dem ersten Kassenbuch: nur im Saldo
    belege = [r for r in rows if r['ledger'] == 'cash']
    umsaetze = {}
    for r in rows:
        if r['ledger'] != 'cash':
            umsaetze.setdefault(r['amount'], []).append(r)
    tage = lambda c, b: abs((date.fromisoformat(c['day']) - date.fromisoformat(b['day'])).days)
    paare = sorted(((tage(c, b), c['day'], c['id'], b['id']), c, b) for c in belege for b in umsaetze.get(-c['amount'], ()))
    erledigt = set()
    for _, c, b in paare:                                  # nächstliegendes Paar zuerst; jeder Posten gleicht höchstens einen aus
        if c['id'] not in erledigt and b['id'] not in erledigt:
            erledigt |= {c['id'], b['id']}
    for r in rows:
        if r['id'] not in erledigt:
            posten = {x: r[x] for x in ('id', 'day', 'number', 'text', 'amount')}
            (result['kassen'].setdefault(r['kasse'], []) if r['ledger'] == 'cash' else result['bank']).append(posten)
    return result


def _round(source):
    """Zuordnungsrunde einer Journal-Quelle (bank:7 → 0, bank:7/2 → 2)."""
    tail = str(source).split(':', 1)[1]
    return int(tail.split('/')[1]) if '/' in tail else 0


def book(store, year, kasse=0):
    """Kassenbuch einer Kasse für ein Jahr: Übertrag, Bewegungen mit Beleg, Kontierung und Bestand; ungültige Belege nur zur
    Information. Reihenfolge nach Tag und Buchung (ein Storno steht unter seiner Buchung); Summen netto je Beleg, Anfangsbestand eigens."""
    from core import bank_source
    k = _k(store, kasse)
    year = int(year)
    first, last = f'{year}-01-01', f'{year}-12-31'
    rows = {b['id']: b for b in store.rows("SELECT * FROM bank WHERE ledger='cash' AND kasse=?", (k['id'],))}
    by_source = {}
    for b in rows.values():
        for r in range(b['reversals'] + 1):
            for kind in ('bank', 'resolve', 'reverse-bank'):
                by_source[bank_source(kind, {'id': b['id'], 'reversals': r})] = b
    accounts = {}
    for e in store.rows("SELECT j.source,e.account,e.kv,e.title,e.credit-e.debit amount FROM entries e JOIN journals j ON j.id=e.journal_id "
                        "WHERE e.account NOT IN (?,'1370') AND (j.source LIKE 'bank:%' OR j.source LIKE 'resolve:%')", (k['account'],)):
        accounts.setdefault(e['source'], []).append(e)
    moves = timeline(store, kasse=k)
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
            kind = 'bank'                                      # Buchung auf das Kassenkonto aus dem Kontoauszug
            net[('bank', source)] = net.get(('bank', source), 0) + m['amount']
        storno = bool(source and source.startswith('reverse-bank:'))
        label = {'anfang': 'Anfangsbestand', 'beleg': 'Kassenbeleg'}.get(kind) or (
            'aus Kontoauszug' if k['art'] == 'bar' else 'Storno aus Kontoauszug' if storno else
            'Einzug vom Bankkonto' if m['amount'] > 0 else 'Auszahlung aufs Bankkonto')
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
        lines.append({'day': m['day'], 'amount': m['amount'], 'balance': balance, 'kind': kind, 'label': label,
                      'number': b['bank_ref'] if b is not None else None, 'bank_id': b['id'] if b is not None else None,
                      'name': b['name'] if b is not None else None, 'text': (b['reference'] if b is not None else m['text']),
                      'state': state, 'attachment': b['attachment'] if b is not None else None, 'journal': m.get('journal'), 'source': source,
                      'posted': [{'account': e['account'], 'kv': e['kv'], 'title': e['title'], 'amount': e['amount']} for e in posted],
                      'storno': storno})
    voided = [b for b in rows.values() if b['state'] == 'void' and b['booked'][:4] == str(year)]
    in_year = [b for b in rows.values() if b['booked'][:4] == str(year)]
    return {'year': year, 'kasse': k, 'start': k['start'], 'opening': opening(store, k), 'carry': carry, 'opening_in_year': opening_sum, 'lines': lines,
            'end': balance, 'income': sum(v for v in net.values() if v > 0), 'expense': -sum(v for v in net.values() if v < 0),
            'void': sorted(voided, key=lambda b: b['booked']), 'today': balance_today(store, k),
            'transit': transit(store, min(last, date.today().isoformat()))['kassen'].get(k['id'], []),   # offene Posten dieser Kasse
            'open': sum(1 for b in in_year if b['state'] == 'open'), 'parked': sum(1 for b in in_year if b['state'] == 'parked'),
            'missing_receipt': sum(1 for b in in_year if b['state'] != 'void' and not b['attachment'] and b['amount'] < 0 and not is_transit(b))}


def balance_today(store, kasse=0):
    return balance(store, date.today().isoformat(), kasse)


def is_transit(b):
    """Geldtransit (Abhebung/Einzahlung bzw. Umbuchung) – braucht keinen eigenen Beleg; die Gegenseite steht im Kontoauszug."""
    return str(b['source'] or '').endswith(tuple(x[kind] for x in (KINDS, KINDS_OTHER) for kind in ('withdraw', 'deposit')))


STATES = {'open': 'offen', 'parked': 'geparkt', 'booked': 'verbucht', 'reversed': 'verbucht, später storniert', 'storno': 'Storno'}


def csv(store, year, kasse=0):
    """Kassenbuch einer Kasse als CSV (Kassenprüfung)."""
    import berichte
    from formate import german_date
    b = book(store, year, kasse)
    rows = [['', f'01.01.{year}', 'Übertrag', '', '', '', '', berichte._eur(b['carry']), '', '']]
    for x in b['lines']:
        rows.append([x['number'] or '', german_date(x['day']), x['label'],
                     x['name'] or '', x['text'] or '', berichte._eur(x['amount']) if x['amount'] > 0 else '',
                     berichte._eur(-x['amount']) if x['amount'] < 0 else '', berichte._eur(x['balance']),
                     '; '.join(f"{p['account']}/{p['kv']}" + (f"/{p['title']}" if p['title'] else '') for p in x['posted']),
                     STATES.get(x['state'], x['state'])])
    for v in b['void']:
        rows.append([v['bank_ref'], german_date(v['booked']), 'ungültig', v['name'] or '', v['reference'] or '', '', '', '', '', f"ungültig: {v['note']}"])
    return berichte._csv(rows, ['Beleg', 'Datum', 'Art', 'Name', 'Text', 'Einnahme', 'Ausgabe', 'Bestand', 'Kontierung (Konto/Bereich/Titel)', 'Stand'])
