"""Eingangsrechnungen (Verbindlichkeiten), Zahlungsvorschlag und Abgleich mit Kontoumsätzen.

Ablauf: Eingangsrechnung erfassen (Aufwand an Verbindlichkeiten 1600, Haushaltsjahr = Rechnungsdatum)
→ Zahlungsvorschlag: Überweisungsdaten zum Kopieren oder als GiroCode, „als überwiesen markieren“
→ Kontoauszug importieren → Abgleich ordnet die Abbuchung der Eingangsrechnung zu (Verbindlichkeiten an Bank).
Der Abgleich schlägt nur eindeutige Fälle vor (Betrag + IBAN oder Verwendungszweck); verbucht wird erst nach Bestätigung.
Wird eine zugeordnete Zahlung storniert, ist der Umsatz wieder offen; die Eingangsrechnung bleibt als „Zahlung storniert –
prüfen“ gesperrt, damit sie nicht versehentlich ein zweites Mal überwiesen wird. Ordnet man die Abbuchung ihr wieder zu,
ist die Sperre erledigt; sonst gibt „wieder zur Zahlung freigeben“ sie frei.
"""
import base64
import re
from datetime import date, datetime, timedelta

from formate import german_date, money, iso, iban_valid, iban_compact, iban_pretty

LIABILITY = ('1600', 'Verbindlichkeiten (Eingangsrechnungen)', 'liability')
FILE_TYPES = {'.pdf': (b'%PDF', 'application/pdf'), '.png': (b'\x89PNG', 'image/png'),
              '.jpg': (b'\xff\xd8\xff', 'image/jpeg'), '.jpeg': (b'\xff\xd8\xff', 'image/jpeg'),
              '.xml': (b'<', 'application/xml')}  # XML nur als E-Rechnung (XRechnung), geprüft mit erechnung.read
MAX_FILE = 10_000_000
_IBAN_START = re.compile(r'(?<![A-Z0-9])[A-Z]{2}\d{2}')
IBAN_LENGTHS = {'DE': 22, 'AT': 20, 'CH': 21, 'LI': 21, 'NL': 18, 'BE': 16, 'LU': 20, 'FR': 27, 'IT': 27, 'ES': 24, 'PT': 25,
                'DK': 18, 'SE': 24, 'NO': 15, 'FI': 18, 'PL': 28, 'CZ': 24, 'GB': 22, 'IE': 22}
DATE_TOLERANCE = 7  # Abbuchung höchstens so viele Tage vor dem Rechnungsdatum gilt noch als passend
_NUMBER_IN_TEXT = re.compile(r'\b([BR]-\d{4}-\d{5})\b')
# großzügig für Sperren (Zahlungserinnerung): b-2026-00001, B202600001, B 2026 1 …
_NUMBER_LOOSE = re.compile(r'(?<![A-Za-z0-9])([BRbr])\s*[-–_/.]?\s*(20\d{2})\s*[-–_/.]?\s*(\d{1,5})(?!\d)')


def invoice_numbers(text):
    """Rechnungsnummern in beliebiger Schreibweise, normalisiert auf B-JJJJ-nnnnn."""
    return {f'{m.group(1).upper()}-{m.group(2)}-{int(m.group(3)):05d}' for m in _NUMBER_LOOSE.finditer(str(text or ''))}


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS bills(id INTEGER PRIMARY KEY,number TEXT UNIQUE NOT NULL,creditor TEXT NOT NULL,iban TEXT,bic TEXT,
        ext_number TEXT,reference TEXT NOT NULL,issued TEXT NOT NULL,due TEXT NOT NULL,total INTEGER NOT NULL CHECK(total>0),
        kv TEXT NOT NULL REFERENCES kv(code),status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','cancelled')),
        transferred TEXT,attachment TEXT,note TEXT,created TEXT NOT NULL,actor TEXT,
        written_off INTEGER NOT NULL DEFAULT 0 CHECK(written_off>=0),written_off_on TEXT,payment_reversed TEXT);
    CREATE TABLE IF NOT EXISTS bill_lines(id INTEGER PRIMARY KEY,bill_id INTEGER NOT NULL REFERENCES bills(id),description TEXT NOT NULL,
        amount INTEGER NOT NULL CHECK(amount>0),account TEXT NOT NULL REFERENCES accounts(code),title TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bill_payments(id INTEGER PRIMARY KEY,bank_id INTEGER NOT NULL REFERENCES bank(id),
        bill_id INTEGER NOT NULL REFERENCES bills(id),amount INTEGER NOT NULL CHECK(amount>0),UNIQUE(bank_id,bill_id));
    ''')
    from core import ensure_rows
    ensure_rows(db, 'accounts', [LIABILITY])
    if 'liability' not in [c[1] for c in db.execute('PRAGMA table_info(bills)')]:
        # Verbindlichkeitenkonto je Rechnung (Stammdaten änderbar; erfasste Rechnungen behalten ihr Konto)
        db.execute(f"ALTER TABLE bills ADD COLUMN liability TEXT NOT NULL DEFAULT '{LIABILITY[0]}'")
    db.commit()


def _paid_sql(alias='b'):
    return (f"COALESCE((SELECT SUM(p.amount) FROM bill_payments p JOIN bank k ON k.id=p.bank_id "
            f"WHERE p.bill_id={alias}.id AND k.state='booked'),0)")


def paid(store, bill_id):
    return store.db.execute(f'SELECT {_paid_sql()} FROM bills b WHERE b.id=?', (bill_id,)).fetchone()[0]


def _open(row):
    return 0 if row['status'] != 'open' else row['total'] - row['paid'] - row['written_off']


def bills(store):
    rows = store.rows(f'SELECT b.*,{_paid_sql()} paid FROM bills b ORDER BY b.id DESC')
    lines = {}
    for l in store.rows('SELECT * FROM bill_lines ORDER BY id'):
        lines.setdefault(l['bill_id'], []).append(l)
    pending = pending_in_bank(store)
    for r in rows:
        r['lines'] = lines.get(r['id'], [])
        r['open'] = _open(r)
        r['in_bank'] = pending.get(r['id']) if r['open'] > 0 else None
    import erstattung
    import freigabe
    return erstattung.annotate(store, freigabe.annotate(store, rows))


def pending_in_bank(store):
    """Eingangsrechnungen, deren Abbuchung schon im Kontoauszug steht, aber noch nicht zugeordnet ist (Rechnung → Datum)."""
    found = {}
    for bank in store.rows("SELECT * FROM bank WHERE state IN ('open','parked') AND amount<0"):
        strong = [c for c in bill_candidates(store, bank) if c['strong']]
        if len(strong) == 1:
            found.setdefault(strong[0]['id'], bank['booked'])
    return found


def liability_account(store):
    """Verbindlichkeitenkonto für neue Eingangsrechnungen laut Stammdaten (Standard 1600)."""
    code = store.config().get('liability_account') or LIABILITY[0]
    row = store.db.execute('SELECT kind FROM accounts WHERE code=?', (code,)).fetchone()
    if not row or row['kind'] != 'liability':
        raise ValueError(f'Verbindlichkeitenkonto {code} fehlt – in den Stammdaten prüfen.')
    return code


def next_number(store, day=None):
    year = (day or date.today().isoformat())[:4]
    seq = store.db.execute('SELECT COUNT(*) FROM bills WHERE number LIKE ?', (f'E-{year}-%',)).fetchone()[0]
    return f'E-{year}-{seq + 1:05d}'


def _clean(store, data):
    creditor = ' '.join(str(data.get('creditor') or '').split())
    if not creditor:
        raise ValueError('Bitte den Rechnungssteller (Kreditor) angeben.')
    iban = iban_compact(data.get('iban'))
    if iban and not iban_valid(iban):
        raise ValueError('IBAN des Kreditors ist ungültig (Prüfziffer stimmt nicht).')
    bic = str(data.get('bic') or '').upper().replace(' ', '')
    if bic and not re.fullmatch(r'[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?', bic):
        raise ValueError('BIC ist ungültig (8 oder 11 Zeichen).')
    issued = iso(data['issued'])
    today = date.today()
    if not f'{today.year - 1}-01-01' <= issued <= (today + timedelta(days=60)).isoformat():
        raise ValueError(f'Rechnungsdatum {issued} ist unplausibel (erlaubt: ab 01.01.{today.year - 1} bis 60 Tage in der Zukunft).')
    due = iso(data['due']) if str(data.get('due') or '').strip() else (date.fromisoformat(issued) + timedelta(days=14)).isoformat()
    if due < issued:
        raise ValueError('Fälligkeit liegt vor dem Rechnungsdatum.')
    if due > (date.fromisoformat(issued) + timedelta(days=365)).isoformat():
        raise ValueError('Fälligkeit liegt mehr als ein Jahr nach dem Rechnungsdatum.')
    ext = ' '.join(str(data.get('ext_number') or '').split())[:60]
    reference = ' '.join(str(data.get('reference') or '').split()) or ext  # leer → eigene Belegnummer (in create)
    if len(reference) > 140:
        raise ValueError('Verwendungszweck: höchstens 140 Zeichen.')
    kv = data.get('kv')
    if not store.db.execute('SELECT 1 FROM kv WHERE code=?', (kv,)).fetchone():
        raise ValueError('Bitte einen Bereich wählen.')
    import haushalt
    import rpj
    lines = []
    for n, l in enumerate(data.get('lines') or [], 1):
        amount = money(l.get('amount', ''))
        description = ' '.join(str(l.get('description') or '').split())
        if amount <= 0 or not description:
            raise ValueError('Positionen benötigen eine Beschreibung und einen positiven Betrag.')
        account = l.get('account')
        ac = store.db.execute('SELECT kind FROM accounts WHERE code=?', (account,)).fetchone()
        if not ac or ac['kind'] != 'expense':
            raise ValueError('Für Eingangsrechnungen bitte ein Ausgabenkonto wählen.')
        title = (haushalt.default_title(account, kv) if account == 'KV' else str(l.get('title') or '').strip() or haushalt.default_title(account, kv)) or ''   # Titel ergibt sich aus dem Konto
        if account == 'KV' and kv not in haushalt.KV_ROWS:
            raise ValueError('Das Sammelkonto KV nur mit einem Kreisverband als Bereich verwenden.')
        if title not in haushalt.TITLES or haushalt.TITLES[title]['kind'] != 'expense':
            raise ValueError('Bitte den Haushaltstitel (Ausgabe) angeben.')
        link = rpj.check_part(store, account, title, l.get('rpj') or {'target': l.get('rpj_target'), 'category': l.get('rpj_category')},
                              int(issued[:4]), f'Position {n} (RPJ-Mittel {account})')   # RPJ-Posten: Projekt und Kostenart (Pflicht)
        lines.append({'description': description, 'amount': amount, 'account': account, 'title': title, **({'rpj': link} if link else {})})
    if not lines:
        raise ValueError('Mindestens eine Position erforderlich.')
    kind = data.get('kind') or 'external'
    if kind not in ('external', 'refund'):
        raise ValueError('Art wählen: Fremdrechnung oder Auslagenerstattung.')
    return {'kind': kind, 'creditor': creditor[:120], 'iban': iban or None, 'bic': bic or None, 'ext_number': ext or None,
            'reference': reference, 'issued': issued, 'due': due, 'kv': kv, 'lines': lines,
            'note': str(data.get('note') or '').strip()[:500] or None}


def check_file(filename, content_b64):
    """Beleg prüfen: PDF/PNG/JPEG oder E-Rechnung als XML, höchstens 10 MB, Inhalt passt zur Endung. Liefert (Endung, Bytes)."""
    suffix = ('.' + str(filename).rsplit('.', 1)[-1].lower()) if '.' in str(filename) else ''
    if suffix not in FILE_TYPES:
        raise ValueError('Beleg bitte als PDF, PNG, JPG oder E-Rechnung (XML) anhängen.')
    raw = base64.b64decode(content_b64, validate=True)
    if len(raw) > MAX_FILE:
        raise ValueError('Beleg ist zu groß (höchstens 10 MB).')
    if suffix == '.xml':
        import erechnung
        erechnung.check(raw)  # nur E-Rechnungen (Form), sonst ValueError mit Grund
    elif not raw.startswith(FILE_TYPES[suffix][0]):
        raise ValueError('Der Inhalt der Datei passt nicht zur Endung.')
    return ('.jpg' if suffix == '.jpeg' else suffix), raw


DUPLICATE = 'Möglicherweise doppelt erfasst'


def duplicates(store, v, total):
    """Bereits erfasste (nicht stornierte) Eingangsrechnungen, die dieselbe sein könnten."""
    found = []
    for b in store.rows("SELECT * FROM bills WHERE status='open'"):
        same_creditor = b['creditor'].casefold() == v['creditor'].casefold() or (v['iban'] and b['iban'] == v['iban'])
        # Erstattung: derselbe Beleg darf nicht zweimal (auch nicht über ein anderes Mitglied oder als Fremdrechnung) bezahlt werden (§ 8 Abs. 6)
        same_creditor = same_creditor or v.get('kind') == 'refund' or b['kind'] == 'refund'
        same_number = v['ext_number'] and b['ext_number'] and _norm(b['ext_number']) == _norm(v['ext_number'])
        # gleicher Betrag + Datum nur verdächtig, wenn nicht beide verschiedene Rechnungsnummern tragen
        same_bill = b['total'] == total and b['issued'] == v['issued'] and not (v['ext_number'] and b['ext_number'])
        if same_creditor and (same_number or same_bill):
            found.append(b['number'])
    return found


def create(store, data, actor, attachment_suffix=None):
    """Eingangsrechnung erfassen und buchen (Aufwand an Verbindlichkeiten). Liefert (id, Belegnummer, Dateiname).
    Auslagenerstattung (kind „refund“): zusätzlich die Antragsdaten nach Finanzordnung § 8 (erstattung.py)."""
    import erstattung
    data = erstattung.prepare(store, data)
    v = _clean(store, data)
    refund = erstattung.clean(store, data, v, bool(attachment_suffix)) if v['kind'] == 'refund' else None
    if refund and (not str(data.get('due') or '').strip() or v['due'] < refund['submitted_on']):
        v['due'] = refund['due']  # Fälligkeit ab Antragseingang, nie vor dem Antrag (nicht ab dem früheren Ausgabedatum)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        number = next_number(store)
        total = sum(l['amount'] for l in v['lines'])
        same = duplicates(store, v, total)
        if same and data.get('allow_duplicate') is not True:
            raise ValueError(f"{DUPLICATE}: gleicher Kreditor mit gleicher Rechnungsnummer oder gleichem Betrag und Datum wie {', '.join(same)}.")
        filename = f'{number}{attachment_suffix}' if attachment_suffix else None
        liability = liability_account(store)
        import freigabe
        c = store.db.execute('INSERT INTO bills(number,creditor,iban,bic,ext_number,reference,issued,due,total,kv,attachment,note,created,actor,'
                             'liability,kind,approval_limit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (number, v['creditor'], v['iban'], v['bic'], v['ext_number'], v['reference'] or number, v['issued'], v['due'],
                              total, v['kv'], filename, v['note'], datetime.now().isoformat(timespec='seconds'), actor, liability, v['kind'],
                              freigabe.limit(store)))
        bid = c.lastrowid
        for l in v['lines']:
            store.db.execute('INSERT INTO bill_lines(bill_id,description,amount,account,title) VALUES(?,?,?,?,?)',
                             (bid, l['description'], l['amount'], l['account'], l['title']))
        entries = [{'account': l['account'], 'kv': v['kv'], 'debit': l['amount'], 'credit': 0, 'title': l['title'], 'rpj': l.get('rpj')} for l in v['lines']]
        entries.append({'account': liability, 'kv': v['kv'], 'debit': 0, 'credit': total, 'title': number})
        store.journal(v['issued'], f"{number} {v['creditor']}" + (f" ({v['ext_number']})" if v['ext_number'] else ''), 'bill:' + str(bid),
                      entries, year=int(v['issued'][:4]))
        import rpj
        rpj.attach(store, entries, actor)   # RPJ-Schattenhaushalt: Zuordnung je Position
        if refund:
            erstattung.save(store, bid, refund, actor)
        store.audit(actor, 'Eingangsrechnung erfasst', {'id': bid, 'number': number, 'cents': total})
    return bid, number, filename


def cancel(store, bill_id, actor):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b = store.db.execute('SELECT * FROM bills WHERE id=?', (bill_id,)).fetchone()
        if not b or b['status'] != 'open':
            raise ValueError('Eingangsrechnung ist nicht stornierbar.')
        if store.db.execute("SELECT 1 FROM bill_payments p JOIN bank k ON k.id=p.bank_id WHERE p.bill_id=? AND k.state='booked'", (bill_id,)).fetchone():
            raise ValueError('Eingangsrechnung mit zugeordneter Zahlung kann nicht storniert werden.')
        j = store.db.execute('SELECT id,year FROM journals WHERE source=?', ('bill:' + str(bill_id),)).fetchone()
        entries = store.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?', (j['id'],))
        store.journal(date.today().isoformat(), 'Storno ' + b['number'], 'bill-cancel:' + str(bill_id), entries, j['id'], year=j['year'])
        w = store.db.execute('SELECT id,year FROM journals WHERE source=?', ('bill-writeoff:' + str(bill_id),)).fetchone()
        if w:   # ausgebuchter Restbetrag gehört zur Rechnung und wird mit storniert
            back = store.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?', (w['id'],))
            store.journal(date.today().isoformat(), 'Storno Ausbuchung ' + b['number'], 'bill-writeoff-cancel:' + str(bill_id), back, w['id'], year=w['year'])
        store.db.execute("UPDATE bills SET status='cancelled' WHERE id=?", (bill_id,))
        store.audit(actor, 'Eingangsrechnung storniert', {'id': bill_id, 'number': b['number']})


def mark_transferred(store, ids, actor, undo=False):
    if not isinstance(ids, list) or not ids:
        raise ValueError('Bitte Eingangsrechnungen auswählen.')
    day = None if undo else date.today().isoformat()
    with store.db:
        import freigabe
        for bid in {int(i) for i in ids}:
            b = store.db.execute('SELECT * FROM bills WHERE id=?', (bid,)).fetchone()
            if not b or b['status'] != 'open':
                raise ValueError('Nur offene Eingangsrechnungen können angewiesen werden.')
            if not undo:
                freigabe.check_payable(store, b)  # § 7 Abs. 4 Finanzordnung
            store.db.execute('UPDATE bills SET transferred=? WHERE id=?', (day, bid))
        store.audit(actor, 'Überweisung zurückgenommen' if undo else 'Überweisung beauftragt', {'ids': sorted({int(i) for i in ids})})


def transfer(store, bill_id):
    """Überweisungsdaten für das Online-Banking (offener Betrag)."""
    b = store.db.execute(f'SELECT b.*,{_paid_sql()} paid FROM bills b WHERE b.id=?', (bill_id,)).fetchone()
    if not b or _open(b) <= 0:
        raise ValueError('Eingangsrechnung ist nicht offen.')
    if b['payment_reversed']:
        raise ValueError(f"Für {b['number']} wurde eine zugeordnete Zahlung storniert. Erst klären, ob das Geld schon überwiesen ist, "
                         "und dann „Wieder zur Zahlung freigeben“.")
    if not b['iban']:
        raise ValueError(f"Für {b['number']} ist keine IBAN hinterlegt.")
    import freigabe
    freigabe.check_payable(store, b)  # § 7 Abs. 4 Finanzordnung
    return {'id': b['id'], 'number': b['number'], 'name': b['creditor'][:70], 'iban': iban_pretty(b['iban']), 'iban_compact': b['iban'],
            'bic': b['bic'] or '', 'amount': _open(b), 'reference': b['reference'][:140], 'due': b['due'],
            'transferred': b['transferred'], 'in_bank': pending_in_bank(store).get(b['id'])}


def release(store, bill_id, actor):
    """Nach einer stornierten Zahlung: geprüft, Rechnung darf wieder in den Zahlungsvorschlag."""
    with store.db:
        b = store.db.execute('SELECT number,payment_reversed FROM bills WHERE id=?', (bill_id,)).fetchone()
        if not b or not b['payment_reversed']:
            raise ValueError('Keine stornierte Zahlung zu klären.')
        store.db.execute('UPDATE bills SET payment_reversed=NULL,transferred=NULL WHERE id=?', (bill_id,))
        store.audit(actor, 'Eingangsrechnung wieder zur Zahlung freigegeben', {'id': bill_id, 'number': b['number']})


def on_bank_reversed(store, bank_id):
    """Zahlung storniert: betroffene Eingangsrechnungen sperren (nicht automatisch erneut überweisen)."""
    store.db.execute('UPDATE bills SET payment_reversed=? WHERE id IN (SELECT bill_id FROM bill_payments WHERE bank_id=?)',
                     (date.today().isoformat(), bank_id))


def _reversed_open(store, bill_id, except_bank=None):
    """Stornierte Abbuchungen dieser Eingangsrechnung, die noch offen oder geparkt (nicht neu zugeordnet) sind."""
    return store.db.execute("SELECT 1 FROM reversed_allocations r JOIN bank k ON k.id=r.bank_id WHERE r.kind='bill' "
                            "AND r.target_id=? AND k.state IN ('open','parked') AND r.bank_id IS NOT ?",
                            (bill_id, except_bank)).fetchone() is not None


def write_off(store, bill_id, reason, actor):
    """Restbetrag ausbuchen (z. B. Skonto, Kürzung): mindert den Aufwand, Verbindlichkeit wird ausgeglichen."""
    reason = ' '.join(str(reason or '').split())
    if not reason:
        raise ValueError('Bitte einen Grund angeben (z. B. Skonto).')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b = store.db.execute(f'SELECT b.*,{_paid_sql()} paid FROM bills b WHERE b.id=?', (bill_id,)).fetchone()
        if not b or b['status'] != 'open' or b['paid'] <= 0 or _open(b) <= 0:
            raise ValueError('Ausbuchen ist nur für teilweise bezahlte Eingangsrechnungen möglich (sonst stornieren).')
        rest = _open(b)
        line = store.db.execute('SELECT account,title FROM bill_lines WHERE bill_id=? ORDER BY amount DESC,id LIMIT 1', (bill_id,)).fetchone()
        j = store.db.execute('SELECT id,year FROM journals WHERE source=?', ('bill:' + str(bill_id),)).fetchone()
        entries = [{'account': b['liability'], 'kv': b['kv'], 'debit': rest, 'credit': 0, 'title': b['number']},
                   {'account': line['account'], 'kv': b['kv'], 'debit': 0, 'credit': rest, 'title': line['title']}]
        store.journal(date.today().isoformat(), f"Restbetrag {b['number']} ausgebucht: {reason}", 'bill-writeoff:' + str(bill_id), entries, year=j['year'])
        import rpj   # RPJ-Posten: die Minderung gehört zum Projekt der Position
        rpj.inherit(store, entries[1], [r[0] for r in store.db.execute('SELECT id FROM entries WHERE journal_id=? AND account=? AND title=? AND debit>0',
                                                                         (j['id'], line['account'], line['title']))], actor)
        store.db.execute('UPDATE bills SET written_off=?,written_off_on=? WHERE id=?', (rest, date.today().isoformat(), bill_id))
        store.audit(actor, 'Restbetrag Eingangsrechnung ausgebucht', {'id': bill_id, 'number': b['number'], 'cents': rest, 'grund': reason})


def book_part(store, bank_id, part_bill_id, amount):
    """Teil einer Bankzuordnung: Abbuchung bezahlt Eingangsrechnung. amount in Cent (negativ). Liefert Buchungszeile."""
    b = store.db.execute(f'SELECT b.*,{_paid_sql()} paid FROM bills b WHERE b.id=?', (int(part_bill_id),)).fetchone()
    if not b or b['status'] != 'open':
        raise ValueError('Eingangsrechnung ist nicht offen.')
    if amount >= 0:
        raise ValueError('Eingangsrechnungen können nur Abbuchungen zugeordnet werden.')
    if -amount > _open(b):
        raise ValueError('Zahlung übersteigt den offenen Betrag der Eingangsrechnung.')
    store.db.execute('INSERT INTO bill_payments(bank_id,bill_id,amount) VALUES(?,?,?)', (bank_id, b['id'], -amount))
    # neue Zahlung zugeordnet → geklärt, sofern keine weitere stornierte Abbuchung dieser Rechnung noch offen ist
    if not _reversed_open(store, b['id'], bank_id):
        store.db.execute('UPDATE bills SET payment_reversed=NULL WHERE id=?', (b['id'],))
    return {'account': b['liability'], 'kv': b['kv'], 'debit': -amount, 'credit': 0, 'title': b['number']}


# ---------------------------------------------------------------- Abgleich mit Kontoumsätzen

def _ibans(text):
    """Gültige IBANs im Text, auch mit Leerzeichen und direkt gefolgt von weiteren Wörtern („… 202051 RE-1“)."""
    s = str(text or '').upper()
    found = set()
    for m in _IBAN_START.finditer(s):
        chars = []
        for ch in s[m.start():m.start() + 50]:
            if ch.isalnum():
                chars.append(ch)
            elif ch != ' ':
                break
            if len(chars) >= 34:
                break
        candidate = ''.join(chars)
        lengths = [IBAN_LENGTHS[candidate[:2]]] if candidate[:2] in IBAN_LENGTHS else range(min(34, len(candidate)), 14, -1)
        for n in lengths:
            if len(candidate) >= n and iban_valid(candidate[:n]):
                found.add(candidate[:n])
                break
    return found


def _norm(text):
    return re.sub(r'[^0-9a-z]', '', str(text or '').lower())


_SEP = r'[\s\-/._:]*'


def _mentions(text, ref):
    """Steht die Referenz als eigenständige Angabe im Text? Leer- und Satzzeichen dürfen abweichen,
    davor und danach darf aber nichts angehängt sein: „RE-1“ nicht in „RE-12“, „2026“ nicht in „2026-117“,
    „E-2026-00001“ nicht in „RE-2026-00001“, keine Ziffernfolgen mitten in einer IBAN."""
    chars = _norm(ref)
    if len(chars) < 3:
        return False
    body = _SEP.join(re.escape(c) for c in chars)
    pattern = r'(?<![0-9a-z])(?<![0-9a-z][\-/._])' + body + r'(?![0-9a-z])(?![\-/._][0-9a-z])'
    return re.search(pattern, str(text or '').lower()) is not None


def _tokens(text):
    return {t for t in re.findall(r'[a-zäöüß0-9]{4,}', str(text or '').lower()) if t not in {'gmbh', 'mbh', 'und', 'bank', 'hamburg'}}


def bill_candidates(store, bank):
    """Offene Eingangsrechnungen, deren offener Betrag genau zur Abbuchung passt – mit Begründung, beste zuerst.

    stark (für Abgleich und Hinweis): IBAN oder Rechnungsnummer/Verwendungszweck passen, keine abweichende IBAN im
    Umsatz und Abbuchung nicht deutlich vor dem Rechnungsdatum; sicher (vorausgewählt): mindestens zwei Belege."""
    if bank['amount'] >= 0:
        return []
    text = f"{bank['name'] or ''} {bank['reference'] or ''}"
    ibans, tokens = _ibans(text), _tokens(bank['name'])
    found = []
    for b in store.rows(f"SELECT b.*,{_paid_sql()} paid FROM bills b WHERE b.status='open'"):
        open_ = _open(b)
        if open_ != -bank['amount']:
            continue
        evidence, against = [], []
        if b['iban'] and b['iban'] in ibans:
            evidence.append('IBAN')
        elif b['iban'] and ibans:
            against.append('andere IBAN')
        if any(_mentions(text, x) for x in (b['ext_number'], b['reference'], b['number'])):
            evidence.append('Rechnungsnummer')
        if tokens & _tokens(b['creditor']):
            evidence.append('Name')
        earliest = (date.fromisoformat(b['issued']) - timedelta(days=DATE_TOLERANCE)).isoformat()
        if bank['booked'] < earliest:
            against.append('vor Rechnungsdatum')
        strong = bool({'IBAN', 'Rechnungsnummer'} & set(evidence)) and not against
        found.append({'kind': 'bill', 'id': b['id'], 'number': b['number'], 'name': b['creditor'], 'open': open_,
                      'evidence': evidence, 'against': against, 'strong': strong, 'confident': strong and len(evidence) >= 2})
    return sorted(found, key=lambda c: (not c['strong'], -len(c['evidence'])))


def unique_bill(store, bank):
    """Genau eine stark passende Eingangsrechnung für diese Abbuchung, sonst None."""
    strong = [c for c in bill_candidates(store, bank) if c['strong']]
    return strong[0] if len(strong) == 1 else None


def invoice_candidates(store, bank):
    """Offene Ausgangsrechnungen, deren Nummer im Verwendungszweck steht und deren offener Betrag genau passt."""
    if bank['amount'] <= 0:
        return []
    numbers = set(_NUMBER_IN_TEXT.findall(f"{bank['reference'] or ''} {bank['name'] or ''}"))
    found = []
    for number in numbers:
        i = store.db.execute("SELECT i.*,COALESCE((SELECT SUM(p.amount) FROM payments p JOIN bank k ON k.id=p.bank_id "
                             "WHERE p.invoice_id=i.id AND k.state='booked'),0) paid FROM invoices i WHERE i.number=? AND i.status='issued'",
                             (number,)).fetchone()
        if i and i['total'] - i['paid'] == bank['amount']:
            found.append({'kind': 'invoice', 'id': i['id'], 'number': i['number'], 'name': i['recipient'], 'open': bank['amount'],
                          'evidence': ['Rechnungsnummer', 'Betrag'], 'against': [], 'strong': True, 'confident': True})
    return found


def invoices_in_bank(store):
    """Ausgangsrechnungen, deren Nummer in einem noch nicht zugeordneten Zahlungseingang steht (Nummer → Datum)."""
    found = {}
    for bank in store.rows("SELECT * FROM bank WHERE state IN ('open','parked') AND amount>0 ORDER BY booked"):
        for number in invoice_numbers(f"{bank['reference'] or ''} {bank['name'] or ''}"):
            found.setdefault(number, bank['booked'])
    return found


def incoming_hint(store):
    """Zahlungserinnerung und Lastschrift: Hinweis auf einen noch nicht zugeordneten Zahlungseingang zu einer Rechnung
    (Rechnungsnummer im Verwendungszweck, oder Betrag und Name passen). Liefert eine Funktion (Rechnung, offen) → Text oder None."""
    in_bank = invoices_in_bank(store)
    incoming = store.rows("SELECT * FROM bank WHERE state IN ('open','parked') AND amount>0 ORDER BY booked")

    def hint(inv, open_):
        if inv['number'] in in_bank:
            return f"Zahlungseingang vom {german_date(in_bank[inv['number']])} (Kontoauszug oder Kasse) – erst zuordnen"
        names = _tokens(inv['recipient'])
        maybe = next((b for b in incoming if b['amount'] == open_ and names & _tokens(f"{b['name'] or ''} {b['reference'] or ''}")), None)
        return f"möglicher Zahlungseingang vom {german_date(maybe['booked'])} (Betrag und Name passen) – erst prüfen und zuordnen" if maybe else None
    return hint


def matches(store, bank_id):
    bank = store.db.execute('SELECT * FROM bank WHERE id=?', (bank_id,)).fetchone()
    if not bank:
        raise ValueError('Bankumsatz nicht gefunden.')
    return bill_candidates(store, bank) + invoice_candidates(store, bank)


def proposals(store, year=None):
    """Eindeutige Zuordnungen für offene Bankumsätze: genau ein starker Treffer, der nicht von einem anderen Umsatz beansprucht wird."""
    import freigabe
    sql = "SELECT * FROM bank WHERE state='open' AND ledger='bank'" + (" AND substr(booked,1,4)=?" if year else '') + ' ORDER BY booked,id'
    result, claimed = [], {}
    for bank in store.rows(sql, (str(year),) if year else ()):
        strong = [c for c in bill_candidates(store, bank) + invoice_candidates(store, bank) if c['strong']]
        if len(strong) == 1:
            key = (strong[0]['kind'], strong[0]['id'])
            claimed.setdefault(key, []).append(len(result))
            hit = {k: strong[0][k] for k in ('kind', 'id', 'number', 'name', 'open', 'evidence', 'confident')}
            if bank['reversals']:  # Zuordnung wurde schon einmal storniert → nie vorauswählen, selbst prüfen
                hit.update(confident=False, reversed=True)
            if hit['kind'] == 'bill' and freigabe.bill_status(store, hit['id'])['missing']:
                hit.update(confident=False, unapproved=True)  # § 7 Abs. 4: bezahlt ohne Freigabe → nicht vorauswählen
            result.append({'bank_id': bank['id'], 'booked': bank['booked'], 'bank_name': bank['name'], 'reference': bank['reference'],
                           'amount': bank['amount'], **hit})
    doubled = {i for idx in claimed.values() if len(idx) > 1 for i in idx}  # zwei Umsätze für dieselbe Rechnung → selbst prüfen
    return [p for i, p in enumerate(result) if i not in doubled]


def apply(store, items, actor):
    """Bestätigte Zuordnungen verbuchen. Jede muss noch genau so im aktuellen Abgleich stehen (eindeutig, nicht doppelt
    beansprucht); erst wenn alle passen, wird gebucht. Liefert (Anzahl gebucht, Fehlertext oder None)."""
    current = {(p['bank_id'], p['kind'], p['id']): p for p in proposals(store)}
    wanted = []
    for item in items:
        key = (int(item['bank_id']), str(item.get('kind')), int(item['id']))
        if key not in current:
            raise ValueError('Mindestens eine Zuordnung passt nicht mehr – bitte den Abgleich neu öffnen. Es wurde nichts gebucht.')
        wanted.append(current[key])
    done = 0
    for p in wanted:
        cents = f"{p['amount'] / 100:.2f}"
        part = {'amount': cents, 'bill_id': p['id']} if p['kind'] == 'bill' else {'amount': cents, 'invoice_id': p['id']}
        try:
            store.book(p['bank_id'], [part], actor)
        except Exception as e:
            return done, f"{done} von {len(wanted)} verbucht; bei {p['number']} abgebrochen: {e}"
        done += 1
    return done, None
