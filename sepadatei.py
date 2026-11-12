"""SEPA-Dateien einlesen: Sammelbuchungen (z. B. Lastschrifteinzug der Mitgliedsbeiträge) auf Mitglieder und Rechnungen aufteilen.

Wunsch Lennert Gust 10.11.2026: „Sepa Datein auch einlösen können um zb. sammelzahlungen von Mitgliedern zuordnen zu können“.

Gelesen werden (auch mehrere in einer ZIP-Datei):
- pain.008 – Lastschrifteinreichung (eigene Datei aus „Lastschriften“ oder z. B. aus der CDU-Mitgliederverwaltung): je Zahlungsblock
  (PmtInf) ein Sammler mit Einzugsdatum; liegt die Bank alle Blöcke als eine Gutschrift, auch die ganze Datei als ein Sammler;
- camt.054 / camt.053 / camt.052 – Sammlerauflösung bzw. Kontoauszug der Bank: je Gutschrift mit mindestens zwei Einzelposten (TxDtls).
Jede Einzelzahlung wird zugeordnet (Reihenfolge = Verlässlichkeit): eigene Lastschrift (EndToEndId) bzw. Rechnungsnummer → offene Rechnung;
Mandatsreferenz, Mitgliedsnummer, IBAN eines Mandats → Mitglied; nur der Name (oder mehrere Mitgliedsnummern) → Vorschlag, der vor
dem Aufteilen bestätigt werden muss. Je Mitglied gehen alle seine Einzelzahlungen zuerst auf seine offenen Beitragsrechnungen (älteste
zuerst, je bis zum offenen Betrag), nur der Rest als Beitrag auf 8100 – sonst stünde der Beitrag doppelt im Haushalt (Sollbuchung der
Rechnung + 8100). Die Sammelbuchung wird im Kontoauszug gesucht (gleicher Betrag, Buchung 3 Tage vor bis 10 Tage nach
dem Datum der Datei): offen → wird aufgeteilt verbucht (core._book); schon als eine Zeile auf 8100 gebucht (z. B. Excel-Übernahme) →
Mitglieder werden zugeordnet (beitraege). Die Datei bleibt als Beleg (Daten/Belege, Tabelle sepa_files) und kommt ins Kassenprüfer-Paket.
Die Oberfläche zeigt IBANs nur gekürzt.

Ab 0.30 (Prüfung einer echten Datei der Mitgliederverwaltung, Wunsch Lennert Gust 10.11.2026): Der Begleitzettel (PDF) zur
Lastschriftdatei hat eine Spalte „Zahler“ (das Mitglied), die in der XML fehlt – zahlen z. B. Eltern, steht dort das Mitglied. Er kann
zusätzlich eingelesen werden (`add_payers`, nur Text über pypdf). Bestätigte Zuordnungen werden je Mandatsreferenz gemerkt
(`sepa_mandates`); dieselbe Referenz in einer späteren Datei gilt dann als sicher.
"""
import hashlib
import io
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

MAX_FILE = 10_000_000
MAX_UNZIPPED = 20_000_000      # alle XML-Dateien einer ZIP zusammen (Speicher beim Lesen etwa das 20-Fache)
MAX_TAGS = 600_000             # grobe Obergrenze vor dem Lesen (5000 Einzelzahlungen brauchen etwa 150 000 Elemente)
MAX_FILES = 100
MAX_ITEMS = 5000
MAX_CENTS = 99_999_999_999     # wie formate (höchstens 999 Mio. €)
MAX_PDF = 5_000_000            # Begleitzettel
MAX_PDF_PAGES = 300
MAX_PDF_STREAM = 1_000_000     # entpackter Inhalt je Datenstrom (pypdf-Standard 75 MB; echte Seite einige 10 KB)
MAX_PDF_SECONDS = 15           # Lesezeit des Begleitzettels insgesamt (je Seite höchstens etwa 3 s)
BEFORE, AFTER = 3, 10          # Tage um das Datum der Datei, in denen die Sammelbuchung im Kontoauszug stehen darf


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS sepa_files(id INTEGER PRIMARY KEY,bank_id INTEGER NOT NULL REFERENCES bank(id),hash TEXT NOT NULL,
        filename TEXT NOT NULL,stored TEXT,kind TEXT NOT NULL,ref TEXT,count INTEGER NOT NULL,total INTEGER NOT NULL,created TEXT NOT NULL,actor TEXT,
        round INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS sepa_files_bank ON sepa_files(bank_id);
    ''')
    cols = {r[1] for r in db.execute('PRAGMA table_info(sepa_mandates)')}
    if cols and 'creditor' not in cols:              # Zwischenstand 0.30 (nur Testkopien): ohne Gläubiger-ID – umstellen, nichts geht verloren
        db.execute('ALTER TABLE sepa_mandates RENAME TO sepa_mandates_vorlaeufig')
    db.execute('CREATE TABLE IF NOT EXISTS sepa_mandates(creditor TEXT NOT NULL DEFAULT \'\',mandate TEXT NOT NULL,'
               'member_id INTEGER NOT NULL REFERENCES members(id),file_id INTEGER,learned TEXT NOT NULL,actor TEXT,PRIMARY KEY(creditor,mandate))')
    # gemerkte Mandatsreferenzen fremder Lastschriftdateien (z. B. Mitgliederverwaltung) je Gläubiger-ID; file_id = sepa_files.id (Storno)
    if cols and 'creditor' not in cols:
        db.execute("INSERT OR IGNORE INTO sepa_mandates(creditor,mandate,member_id,learned,actor) SELECT '',mandate,member_id,learned,actor "
                   'FROM sepa_mandates_vorlaeufig')
        db.execute('DROP TABLE sepa_mandates_vorlaeufig')
    if 'round' not in {r[1] for r in db.execute('PRAGMA table_info(sepa_files)')}:   # Zuordnungsrunde des Umsatzes (bank.reversals)
        db.execute('ALTER TABLE sepa_files ADD COLUMN round INTEGER NOT NULL DEFAULT 0')
    db.commit()


# ---------------------------------------------------------------- Datei lesen

def _local(tag):
    return str(tag).rsplit('}', 1)[-1]


def _kids(el, name):
    return [c for c in el if _local(c.tag) == name]


def _all(el, path):
    found = [el]
    for part in path.split('/'):
        found = [c for f in found for c in _kids(f, part)]
    return found


def _first(el, path):
    found = _all(el, path)
    return found[0] if found else None


def _text(el, path):
    node = _first(el, path)
    return ' '.join((node.text or '').split()) if node is not None else ''


def _texts(el, path):
    return ' '.join(' '.join((n.text or '').split()) for n in _all(el, path)).strip()


def _cents(node, label='Betrag'):
    if node is None:
        raise ValueError(f'{label} fehlt in der SEPA-Datei.')
    if (node.get('Ccy') or 'EUR') != 'EUR':
        raise ValueError('Nur Beträge in Euro werden unterstützt.')
    text = (node.text or '').strip()
    if not re.fullmatch(r'\d{1,12}(\.\d{1,2})?', text):           # Schema: Dezimalzahl mit höchstens 2 Nachkommastellen
        raise ValueError(f'{label} in der SEPA-Datei nicht lesbar.')
    cents = int(Decimal(text) * 100)
    if not 0 < cents <= MAX_CENTS:
        raise ValueError(f'{label} in der SEPA-Datei ungültig.')
    return cents


def _day(text):
    text = (text or '')[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ''


def _item(amount, name, iban, mandate, e2e, text, bic=''):
    e2e = '' if e2e.upper() == 'NOTPROVIDED' else e2e
    return {'amount': amount, 'name': name[:140], 'iban': re.sub(r'\s+', '', iban).upper()[:34], 'mandate': mandate[:35], 'e2e': e2e[:35], 'text': text[:280],
            'bic': bic.strip().upper()[:11]}


def _pain008(init_, filename):
    msg = _text(init_, 'GrpHdr/MsgId')
    batches = []
    for n, pmt in enumerate(_kids(init_, 'PmtInf'), 1):
        day = _day(_text(pmt, 'ReqdColltnDt') or _text(pmt, 'ReqdColltnDt/Dt'))
        items = [_item(_cents(_first(tx, 'InstdAmt')), _text(tx, 'Dbtr/Nm'), _text(tx, 'DbtrAcct/Id/IBAN'), _text(tx, 'DrctDbtTx/MndtRltdInf/MndtId'),
                       _text(tx, 'PmtId/EndToEndId'), _texts(tx, 'RmtInf/Ustrd'), _text(tx, 'DbtrAgt/FinInstnId/BIC') or _text(tx, 'DbtrAgt/FinInstnId/BICFI'))
                 for tx in _kids(pmt, 'DrctDbtTxInf')]
        creditor = _text(pmt, 'CdtrSchmeId/Id/PrvtId/Othr/Id')[:35]
        for i, tx in zip(items, _kids(pmt, 'DrctDbtTxInf')):   # Mandatsreferenzen sind nur je Gläubiger eindeutig (auch je Transaktion möglich)
            i['creditor'] = (_text(tx, 'DrctDbtTx/CdtrSchmeId/Id/PrvtId/Othr/Id') or creditor)[:35]
        if items:
            total = sum(i['amount'] for i in items)
            ctrl = _first(pmt, 'CtrlSum')
            note = '' if ctrl is None or _cents(ctrl, 'Kontrollsumme') == total else 'Kontrollsumme der Datei weicht von der Summe der Einzelposten ab.'
            batches.append({'ref': _text(pmt, 'PmtInfId') or f'{msg}-{n}', 'date': day, 'total': total, 'items': items, 'note': note})
    if len(batches) > 1 and len({b['date'] for b in batches}) == 1:   # Bank bucht manchmal alle Blöcke eines Tages als eine Gutschrift
        items = [i for b in batches for i in b['items']]
        batches.append({'ref': msg or filename, 'date': batches[0]['date'], 'total': sum(i['amount'] for i in items), 'items': items,
                        'note': 'ganze Datei als eine Gutschrift'})
    return 'pain.008', batches


def _camt(body, kind):
    batches = []
    for report in [c for c in body if _local(c.tag) in ('Ntfctn', 'Stmt', 'Rpt')]:
        for ntry in _kids(report, 'Ntry'):
            if _text(ntry, 'CdtDbtInd') != 'CRDT' or _text(ntry, 'RvslInd').lower() == 'true':
                continue
            items = []
            for tx in _all(ntry, 'NtryDtls/TxDtls'):
                if _text(tx, 'CdtDbtInd') == 'DBIT':
                    continue
                amount = next((a for a in (_first(tx, path) for path in ('Amt', 'AmtDtls/TxAmt/Amt', 'AmtDtls/InstdAmt/Amt')) if a is not None), None)   # Element ohne Kinder ist „falsch“: is not None
                items.append(_item(_cents(amount), _text(tx, 'RltdPties/Dbtr/Nm') or _text(tx, 'RltdPties/Dbtr/Pty/Nm'),
                                   _text(tx, 'RltdPties/DbtrAcct/Id/IBAN'), _text(tx, 'Refs/MndtId'), _text(tx, 'Refs/EndToEndId'),
                                   _texts(tx, 'RmtInf/Ustrd'), _text(tx, 'RltdAgts/DbtrAgt/FinInstnId/BIC') or _text(tx, 'RltdAgts/DbtrAgt/FinInstnId/BICFI')))
            if len(items) < 2:
                continue                                       # Einzelzahlung: kein Sammler – normal im Buchungsplatz zuordnen
            total = _cents(_first(ntry, 'Amt'))
            note = '' if sum(i['amount'] for i in items) == total else 'Summe der Einzelposten weicht vom Betrag der Gutschrift ab.'
            batches.append({'ref': _text(ntry, 'AcctSvcrRef') or _text(ntry, 'NtryRef') or _text(ntry, 'NtryDtls/Btch/PmtInfId'),
                            'date': _day(_text(ntry, 'BookgDt/Dt') or _text(ntry, 'BookgDt/DtTm') or _text(ntry, 'ValDt/Dt')),
                            'total': total, 'items': items, 'note': note})
    return kind, batches


KINDS = {'CstmrDrctDbtInitn': 'pain.008', 'BkToCstmrDbtCdtNtfctn': 'camt.054', 'BkToCstmrStmt': 'camt.053', 'BkToCstmrAcctRpt': 'camt.052'}


class _Doctype(Exception):
    pass


def _refuse(*_args):
    raise _Doctype()


def _check_xml(raw, filename):
    """Vorprüfung mit expat (gleich in welcher Zeichenkodierung): keine DOCTYPE- und keine ENTITY-Angaben, begrenzte Elementzahl."""
    from xml.parsers import expat
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = parser.EntityDeclHandler = parser.UnparsedEntityDeclHandler = _refuse
    count = [0]

    def start(*_args):
        count[0] += 1
        if count[0] > MAX_TAGS:
            raise _Doctype()
    parser.StartElementHandler = start
    try:
        parser.Parse(raw, True)
    except _Doctype:
        raise ValueError(f'{filename}: XML mit DOCTYPE-Angabe oder zu vielen Einträgen wird aus Sicherheitsgründen nicht gelesen.') from None
    except expat.ExpatError:
        raise ValueError(f'{filename}: keine lesbare XML-Datei.') from None


def _xml(raw, filename):
    _check_xml(raw, filename)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError(f'{filename}: keine lesbare XML-Datei.') from None
    body = next(iter(root), None) if _local(root.tag) == 'Document' else root
    kind = KINDS.get(_local(body.tag)) if body is not None else None
    if not kind:
        raise ValueError(f'{filename}: keine SEPA-Datei (erwartet pain.008 oder camt.052/053/054).')
    return _pain008(body, filename) if kind == 'pain.008' else _camt(body, kind)


def parse(raw, filename):
    """SEPA-Datei (XML oder ZIP mit XML-Dateien) → {'kind', 'batches': [{ref, date, total, items, note}]} (nur Gutschriften/Einzüge)."""
    if len(raw) > MAX_FILE:
        raise ValueError('Datei zu groß (höchstens 10 MB).')
    files = []
    if raw[:2] == b'PK':
        try:
            book = zipfile.ZipFile(io.BytesIO(raw))
            infos = [i for i in book.infolist() if not i.is_dir() and i.filename.lower().endswith('.xml')]
            if not infos or len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
                raise ValueError(f'ZIP-Datei: 1 bis {MAX_FILES} XML-Dateien, zusammen höchstens {MAX_UNZIPPED // 1_000_000} MB.')
            left = MAX_UNZIPPED
            for info in infos:
                with book.open(info) as f:
                    data = f.read(left + 1)                    # Angaben im Verzeichnis können falsch sein: tatsächliche Größe zählt
                left -= len(data)
                if left < 0:
                    raise ValueError('ZIP-Datei zu groß.')
                files.append((info.filename.rsplit('/', 1)[-1], data))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError, zlib.error):   # verschlüsselt, unbekannte Kompression, defekt
            raise ValueError('ZIP-Datei nicht lesbar (verschlüsselt oder beschädigt?).') from None
    else:
        files.append((filename, raw))
    kinds, batches = set(), []
    for name, data in files:
        kind, found = _xml(data, name)
        kinds.add(kind)
        for b in found:
            b['file'] = name
        batches += found
    if not batches:
        raise ValueError('Die Datei enthält keine Sammelbuchung mit Einzelposten (bei camt: Gutschrift mit mindestens zwei Einzelzahlungen).')
    if sum(len(b['items']) for b in batches) > MAX_ITEMS:
        raise ValueError(f'Höchstens {MAX_ITEMS} Einzelzahlungen je Datei.')
    for b in batches:
        if not b['date']:
            raise ValueError('Datum der Sammelbuchung fehlt in der Datei (Einzugs- bzw. Buchungsdatum).')
    return {'kind': ', '.join(sorted(kinds)), 'batches': batches, 'hash': hashlib.sha256(raw).hexdigest()}


# ---------------------------------------------------------------- Zuordnung

def fold(text):
    """Vergleichsform für Namen aus SEPA-Dateien (dort oft ohne Umlaute, groß geschrieben): Wörter ohne Akzente, ä → ae …"""
    text = str(text or '').casefold()
    for a, b in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        text = text.replace(a, b)
    text = unicodedata.normalize('NFKD', text)
    return set(re.findall(r'[a-z0-9]+', ''.join(c for c in text if not unicodedata.combining(c))))


def _compact(text):
    return re.sub(r'[^A-Z0-9]', '', str(text or '').upper())


def _index(store):
    import verbindlichkeiten   # noqa: F401 (Rechnungsnummern)
    members = store.rows('SELECT id,number,first_name,last_name,name,kv,left_on FROM members')
    numbers = {}
    for m in members:
        key = _compact(m['number'])
        if len(key) >= 4:
            numbers.setdefault(key, m['id'])
            numbers.setdefault(key.lstrip('0'), m['id'])
    names = []
    for m in members:
        words = fold(f"{m['first_name'] or ''} {m['last_name'] or ''}") or fold(m['name'])
        if words:
            names.append((words, m['id']))
    ibans = {}
    refs = {}
    for r in store.rows('SELECT reference,iban,member_id FROM mandates'):   # auch widerrufene: die Referenz bezeichnet weiter dasselbe Mitglied
        refs[_compact(r['reference'])] = r['member_id']
        ibans.setdefault(_compact(r['iban']), set()).add(r['member_id'])
    learned = {(r['creditor'], r['mandate']): r['member_id'] for r in store.rows('SELECT creditor,mandate,member_id FROM sepa_mandates')}
    open_invoices = {r['number']: r for r in store.rows(
        "SELECT i.id,i.number,i.kind,i.year,i.member_id,i.total-COALESCE((SELECT SUM(p.amount) FROM payments p JOIN bank k ON k.id=p.bank_id "
        "WHERE p.invoice_id=i.id AND k.state='booked'),0) open FROM invoices i WHERE i.status='issued'") if r['open'] > 0}
    dues_open = {}
    for inv in open_invoices.values():
        if inv['kind'] == 'dues' and inv['member_id']:
            dues_open.setdefault(inv['member_id'], []).append(inv)
    debits = {_compact(r['end_to_end']): r['number'] for r in store.rows(
        "SELECT d.end_to_end,i.number FROM debits d JOIN invoices i ON i.id=d.invoice_id WHERE d.status!='cancelled'")}
    for v in dues_open.values():
        v.sort(key=lambda i: (i['year'] or 0, i['number']))     # älteste Beitragsrechnung zuerst
    return {'members': {m['id']: m for m in members}, 'numbers': numbers, 'names': names, 'ibans': ibans, 'refs': refs, 'learned': learned,
            'open': open_invoices, 'open_by_id': {i['id']: i for i in open_invoices.values()}, 'dues_open': dues_open, 'debits': debits}


def _current(dues, year):
    """Offene Beitragsrechnungen, die eine Zahlung des Jahres automatisch bedient: Beitragsjahr bis einschließlich Zahlungsjahr
    (eine Rechnung des Folgejahres nur, wenn sie ausdrücklich gewählt bzw. über Rechnungsnummer/Lastschrift erkannt ist)."""
    return [i for i in dues if not year or (i['year'] or 0) <= year]


def learn_key(item):
    """Schlüssel einer gemerkten Mandatsreferenz: Gläubiger-ID der Datei und Referenz unverändert."""
    return (item.get('creditor') or '', (item.get('mandate') or '').strip())


def match(item, ix, day=None):
    """Ziel einer Einzelzahlung: {'member_id', 'invoice_id', 'invoice', 'reason', 'sure', 'bulk'} – leer, wenn nichts passt.
    day: Datum der Zahlung (JJJJ-MM-TT). Alle Hinweise werden unabhängig ermittelt; der stärkste bestimmt das Mitglied, zeigt ein anderer
    auf ein anderes Mitglied, ist es nur ein Vorschlag „prüfen“ (auch bei gemerkter oder eigener Mandatsreferenz). bulk: einfacher
    Namensvorschlag ohne Widerspruch – darf mit „Alle Vorschläge bestätigen“ übernommen werden."""
    import verbindlichkeiten
    day = str(day or '')
    year = int(day[:4]) if day[:4].isdigit() else None
    misfit = []                                          # Rechnungsnummer im Text, Betrag passt nicht: höchstens Vorschlag

    def invoice(number, reason):
        inv = ix['open'].get(number)
        if inv and item['amount'] <= inv['open']:
            return {'member_id': inv['member_id'], 'invoice_id': inv['id'], 'invoice': inv['number'], 'reason': reason, 'sure': True, 'bulk': False}
        if inv:
            misfit.append(inv['number'])
        return None

    number = ix['debits'].get(_compact(item['e2e'])) if item['e2e'] else None
    hit = invoice(number, 'eigene Lastschrift') if number else None
    for n in sorted(verbindlichkeiten.invoice_numbers(f"{item['e2e']} {item['text']}")):
        hit = hit or invoice(n, 'Rechnungsnummer')
    if hit:
        return hit

    def by_name(text):
        words = fold(text)
        found = {mid for need, mid in ix['names'] if len(need) >= 2 and need <= words}
        return found.pop() if len(found) == 1 else None
    own = ix['refs'].get(_compact(item['mandate'])) if item['mandate'] else None
    learned = ix['learned'].get(learn_key(item)) if item['mandate'] else None
    numbers = []
    for token in re.findall(r'[A-Za-z0-9]+', f"{item['mandate']} {item['e2e']} {item['text']}"):
        key = _compact(token)
        if len(key) >= 4 and key in ix['numbers'] and any(ch.isdigit() for ch in key) and ix['numbers'][key] not in numbers:
            numbers.append(ix['numbers'][key])
    holders = ix['ibans'].get(_compact(item['iban']), ()) if item['iban'] else ()
    iban = next(iter(holders)) if len(holders) == 1 else None
    payer = by_name(item['payer']) if item.get('payer') else None
    holder = by_name(f"{item['name']} {item['text']}")
    if own:
        member, reason, sure, kind = own, 'Mandatsreferenz', True, 'own'
    elif learned:
        member, reason, sure, kind = learned, 'Mandatsreferenz (gemerkt)', True, 'learned'
    elif numbers:                                       # mehrere Mitgliedsnummern (z. B. Geschwister): nur Vorschlag
        member, reason, sure, kind = numbers[0], 'Mitgliedsnummer' if len(numbers) == 1 else 'mehrere Mitgliedsnummern', len(numbers) == 1, 'number'
    elif iban:
        member, reason, sure, kind = iban, 'IBAN des Mandats', True, 'iban'
    elif payer:                                         # Zahler laut Begleitzettel (das Mitglied) vor dem Kontoinhaber
        member, reason, sure, kind = payer, 'Zahler laut Begleitzettel', False, 'payer'
    elif holder:
        member, reason, sure, kind = holder, 'nur Name', False, 'name'
    else:
        return {}
    caveats = []
    number_other = next((n for n in numbers if n != member), None) if kind != 'number' else None   # auch eine von mehreren Nummern
    others = [label for label, mid in (('Mitgliedsnummer', number_other),
                                       ('Zahler laut Begleitzettel', payer if kind != 'payer' else None),
                                       ('Kontoinhaber', holder if kind in ('learned', 'payer', 'iban') else None)) if mid and mid != member]
    if others:
        caveats.append(f"{' und '.join(others)} {'passt' if len(others) == 1 else 'passen'} zu einem anderen Mitglied")
    left_on = (ix['members'].get(member) or {}).get('left_on') or ''
    if left_on and day and left_on < day:
        caveats.append(f"Mitglied ist zum {left_on[8:10]}.{left_on[5:7]}.{left_on[:4]} ausgetreten")
    if misfit:
        caveats.append(f"Rechnung {', '.join(misfit)} im Text, Betrag passt nicht")
    all_dues = ix['dues_open'].get(member, [])
    dues = _current(all_dues, year)
    later = [i for i in all_dues if i not in dues]
    if later:      # Beitragsrechnung eines späteren Jahres offen: nicht automatisch – Zahlung kann für das laufende Jahr sein
        caveats.append(f"Beitragsrechnung {', '.join(i['number'] for i in later)} (Folgejahr) offen")
    if dues and item['amount'] != dues[0]['open']:
        caveats.append('Betrag weicht von der offenen Beitragsrechnung ab')
    sure = sure and not caveats
    text = reason + (' · offene Beitragsrechnung' if dues else '') + ''.join(f' · {c}' for c in caveats) + ('' if sure else ' – bitte prüfen')
    return {'member_id': member, 'invoice_id': None, 'invoice': ', '.join(i['number'] for i in dues) or None, 'reason': text, 'sure': sure,
            'bulk': not sure and kind in ('payer', 'name') and not caveats}


def _cross_check(batch, ix):
    """Einzelzahlungen eines Sammlers zusammen betrachten: bedient eine Zahlung eine Rechnung, die andere schon ausgleichen, oder zahlt
    ein Mitglied mehrmals, wird sie zum Vorschlag „prüfen“ (doppelte Abbuchung?)."""
    left, seen = {i: inv['open'] for i, inv in ix['open_by_id'].items()}, set()
    refs = {}
    for item in batch['items']:
        if item['mandate']:
            refs[learn_key(item)] = refs.get(learn_key(item), 0) + 1

    def flag(m, text):
        base = m['reason'][:-len(' – bitte prüfen')] if m['reason'].endswith(' – bitte prüfen') else m['reason']
        m.update(sure=False, bulk=False, reason=f'{base} · {text} – bitte prüfen')
    for item in batch['items']:
        m = item['match']
        if not m:
            continue
        if m.get('invoice_id'):
            if left.get(m['invoice_id'], 0) < item['amount']:
                flag(m, 'Rechnung schon durch eine andere Zahlung der Datei ausgeglichen')
            left[m['invoice_id']] = left.get(m['invoice_id'], 0) - item['amount']
        if item['mandate'] and refs.get(learn_key(item), 0) > 1:
            flag(m, 'Mandatsreferenz mehrfach in der Datei (Geschwister?)')
        elif m.get('member_id') in seen:
            flag(m, 'weitere Zahlung desselben Mitglieds')
        if m.get('member_id'):
            seen.add(m['member_id'])


def candidates(store, batch):
    """Sammelbuchung(en) im Kontoauszug: gleicher Betrag, Datum passend. mode 'book' (offen/geparkt) oder 'assign' (eine 8100-Zeile)."""
    from core import bank_source
    import beitraege
    day = date.fromisoformat(batch['date'])
    rows = store.rows("SELECT * FROM bank WHERE ledger='bank' AND amount=? AND booked BETWEEN ? AND ? ORDER BY booked,id",
                      (batch['total'], (day - timedelta(days=BEFORE)).isoformat(), (day + timedelta(days=AFTER)).isoformat()))
    out, lines_by_bank = [], None
    done = {(r['bank_id'], r['round']) for r in store.rows('SELECT bank_id,round FROM sepa_files')}
    words = {'sepa', 'sammler', 'sammel', 'sammellastschrift', 'lastschrift', 'lastschriften', 'einzug'}
    words |= {w for w in fold(batch['ref']) if len(w) >= 6 and not w.isdigit()}      # Referenz der Datei: nur aussagekräftige Teile
    ref = _compact(batch['ref'])
    for b in rows:
        if (b['id'], b['reversals'] or 0) in done:
            continue                                           # schon mit einer SEPA-Datei aufgeteilt (nach Storno wieder möglich)
        base = {'bank_id': b['id'], 'booked': b['booked'], 'amount': b['amount'], 'name': b['name'] or '', 'reference': (b['reference'] or '')[:140],
                'hint': bool(words & fold(f"{b['name']} {b['reference']}")) or (len(ref) >= 6 and not ref.isdigit() and ref in _compact(b['reference']))}
        if b['state'] in ('open', 'parked'):
            out.append({**base, 'mode': 'book', 'state': b['state']})
        elif b['state'] == 'booked':
            if lines_by_bank is None:                          # 8100-Zeilen aus dem Kontoauszug nur einmal lesen
                lines_by_bank = {}
                for e in beitraege.entries(store):
                    lines_by_bank.setdefault(e['bank_id'], []).append(e)
            lines = lines_by_bank.get(b['id'], [])
            sources = {bank_source('bank', b), bank_source('resolve', b)}
            lines = [e for e in lines if e['source'] in sources]
            if len(lines) == 1 and lines[0]['amount'] == b['amount']:
                out.append({**base, 'mode': 'assign', 'state': 'booked', 'entry_id': lines[0]['id'], 'kv': lines[0]['kv'], 'assigned': len(lines[0]['members'])})
    return out


def preview(store, raw, filename):
    """Datei lesen, Einzelzahlungen zuordnen, Sammelbuchung suchen – nichts wird gespeichert."""
    from lastschrift import masked
    data = parse(raw, filename)
    ix = _index(store)
    done = {r['ref']: r['created'] for r in store.rows('SELECT ref,created FROM sepa_files WHERE hash=?', (data['hash'],))}
    for b in data['batches']:
        b['done'] = done.get(b['ref'][:70], '')[:10]          # dieser Sammler der Datei wurde schon aufgeteilt
        for n, item in enumerate(b['items']):
            item['no'] = n
            item['iban_masked'] = masked(item['iban']) if item['iban'] else ''
            item['match'] = match(item, ix, b['date'])
        _cross_check(b, ix)
        b['candidates'] = candidates(store, b)
        b['counts'] = _counts(b)
    data['filename'] = filename
    return data


_PAYER = re.compile(r'^[^\d,;]{2,80}, [^\d,;]{2,80}$')      # „Nachname, Vorname“ wie in der Spalte „Zahler“


def _pdf_pieces(raw):
    """Textstücke des Begleitzettels mit Position je Seite: [[(x, y, text)]]. Grenzen gegen präparierte PDFs: Größe, Seiten, entpackter
    Inhalt je Datenstrom, gesamte Lesezeit; ein Inhalt, den mehrere Seiten teilen, wird nur einmal gelesen."""
    import logging
    import time
    import pypdf
    logging.getLogger('pypdf').setLevel(logging.ERROR)   # Warnungen zu kleinen Formfehlern nicht ins Protokoll
    if b'%PDF-' not in raw[:1024]:
        raise ValueError('Begleitzettel: keine PDF-Datei.')
    if len(raw) > MAX_PDF:
        raise ValueError(f'Begleitzettel zu groß (höchstens {MAX_PDF // 1_000_000} MB).')
    limits = dict(zlib_maximum_output_length=MAX_PDF_STREAM, lzw_maximum_output_length=MAX_PDF_STREAM, run_length_maximum_output_length=MAX_PDF_STREAM,
                  maximum_declared_stream_length=MAX_PDF, array_based_stream_maximum_output_length=MAX_PDF_STREAM,
                  brotli_maximum_output_length=MAX_PDF_STREAM, jbig2_maximum_output_length=MAX_PDF_STREAM)
    deadline = time.monotonic() + MAX_PDF_SECONDS
    pages, seen = [], set()
    try:
        with pypdf.apply_configuration(**limits):
            reader = pypdf.PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                raise ValueError('Begleitzettel ist verschlüsselt.')
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ValueError(f'Begleitzettel: höchstens {MAX_PDF_PAGES} Seiten.')
            for page in reader.pages:
                if time.monotonic() > deadline:
                    raise ValueError('Begleitzettel: Lesen dauert zu lange – Datei prüfen.')
                key = repr(page.get_object().raw_get('/Contents')) if '/Contents' in page.get_object() else None
                if key in seen:
                    pages.append([])
                    continue
                seen.add(key)
                pieces = []

                stopped = []

                def visit(text, cm, tm, _font, _size, pieces=pieces, stopped=stopped):
                    if time.monotonic() > deadline or len(pieces) > 20_000:   # auch innerhalb einer Seite (z. B. oft aufgerufene Formulare)
                        stopped.append(True)                 # pypdf fängt Fehler in Formularen teils ab: Merker prüfen
                        raise _TooLong()
                    text = ' '.join(str(text).split())
                    if text:
                        pieces.append((tm[4] * cm[0] + tm[5] * cm[2] + cm[4], tm[4] * cm[1] + tm[5] * cm[3] + cm[5], text))
                page.extract_text(visitor_text=visit)
                if stopped:
                    raise _TooLong()
                pages.append(pieces)
    except _TooLong:
        raise ValueError('Begleitzettel: Lesen dauert zu lange oder die Datei enthält zu viel Text – Datei prüfen.') from None
    except ValueError:
        raise
    except Exception:                                   # defekte, zu große oder ungewöhnliche PDF: nicht lesbar, nichts geändert
        raise ValueError('Begleitzettel (PDF) nicht lesbar.') from None
    return pages


class _TooLong(Exception):
    pass


def payers_in_pdf(raw, items):
    """Spalte „Zahler“ des Begleitzettels je Einzelzahlung: {EndToEndId: Zahler}. Eine Zeile wird über die EndToEndId (Textstück) erkannt;
    zu ihr gehören nur Stücke auf derselben Grundlinie (±1,5 pt), die Mandatsreferenz der XML muss in der Zeile stehen. Der Zahler beginnt an
    der Position der Überschrift „Zahler“ und endet vor der nächsten eigenen Überschrift bzw. vor dem ersten Wort mit Ziffern oder BIC/IBAN/
    Mandat der Einzelzahlung (Werte aus der XML) – PDF-Programme zerlegen
    Namen oft in mehrere Stücke oder kleben sie an die Nachbarspalte. Leere Zahler-Zellen bleiben leer, Kontoinhaber (links davon) zählt nie."""
    by_e2e = {i['e2e']: i for i in items if i['e2e']}
    found, column = {}, None
    for pieces in _pdf_pieces(raw):
        head = next(((x, y) for x, y, t in pieces if t == 'Zahler'), None)
        if head is not None:
            right = [x for x, y, t in pieces if abs(y - head[1]) < 3 and x > head[0] + 1]
            column = (head[0] - 3, min(right) - 1 if right else 10_000)
        if column is None:
            continue
        for _x, y, t in pieces:
            item = by_e2e.get(t)
            if not item or item['e2e'] in found:
                continue
            line = sorted((x2, t2) for x2, y2, t2 in pieces if abs(y2 - y) <= 1.5)
            if item['mandate'] and item['mandate'] not in ' '.join(t2 for _x2, t2 in line):
                continue
            stop = {v for v in (item.get('bic'), item.get('iban'), (item.get('mandate') or '').upper()) if v}
            words = []
            for word in ' '.join(t2 for x2, t2 in line if column[0] <= x2 < column[1]).split():
                if any(ch.isdigit() for ch in word) or word.upper() in stop:
                    break
                words.append(word)
            payer = re.sub(r'\s+,', ',', ' '.join(words)).strip(' ,')
            if _PAYER.match(payer):
                found[item['e2e']] = payer[:140]
    return found


def add_payers(store, prev, raw):
    """Begleitzettel (PDF) zur Lastschriftdatei einlesen: Spalte „Zahler“ je Einzelzahlung (payers_in_pdf), danach neu zuordnen.
    Liefert die Anzahl der gefundenen Zahler. Die PDF wird nicht gespeichert."""
    items = [i for b in prev['batches'] for i in b['items']]
    found = payers_in_pdf(raw, items)
    if not found:
        raise ValueError('Im Begleitzettel wurde keine Zeile dieser Lastschriftdatei gefunden (Spalte „Zahler“, EndToEnd-ID und Mandatsreferenz).')
    ix = _index(store)
    for b in prev['batches']:
        for item in b['items']:
            item['payer'] = found.get(item['e2e'], item.get('payer', ''))
            item['match'] = match(item, ix, b['date'])
        _cross_check(b, ix)
        b['counts'] = _counts(b)
    prev['payers'] = len(found)
    return len(found)


def _counts(b):
    return {'items': len(b['items']), 'sure': sum(1 for i in b['items'] if i['match'].get('sure')),
            'check': sum(1 for i in b['items'] if i['match'] and not i['match'].get('sure')),
            'open': sum(1 for i in b['items'] if not i['match'])}


def public(prev):
    """Vorschau für die Oberfläche: ohne vollständige IBAN."""
    batches = []
    for b in prev['batches']:
        batches.append({**{k: b[k] for k in ('ref', 'date', 'total', 'note', 'candidates', 'counts', 'file', 'done')},
                        'items': [{**{k: i[k] for k in ('no', 'amount', 'name', 'iban_masked', 'mandate', 'e2e', 'text', 'match')},
                                   'payer': i.get('payer', '')} for i in b['items']]})
    return {'filename': prev['filename'], 'kind': prev['kind'], 'batches': batches, 'payers': prev.get('payers', 0)}


def _euro_text(cents):
    return f'{cents / 100:.2f}'.replace('.', ',')


def _targets(store, batch, choices, ix):
    """Ziele der Einzelzahlungen → (Rechnungen {id: Cent}, Mitglieder auf 8100 {id: Cent}, Externe {Schlüssel: {..., amount}}).
    Je Mitglied zuerst seine offenen Beitragsrechnungen (älteste zuerst, je bis zum offenen Betrag), nur der Rest auf 8100.
    Vorschläge nur über den Namen (sure=False) gelten erst, wenn sie in choices bestätigt sind.
    Gemerkt (Mandatsreferenz → Mitglied) wird nur, was einzeln gewählt oder einzeln bestätigt ist – nicht aus „Alle Vorschläge bestätigen“
    (confirmed='bulk') und nicht aus automatischen Treffern; eine Referenz, die in der Datei mehrfach vorkommt, nie; ist sie auf eine Person
    außerhalb der Mitgliederliste gewählt, wird eine gemerkte Zuordnung gelöscht (Wert None)."""
    import beitraege
    year = int(batch['date'][:4])
    explicit, pools, externals, cases = {}, {}, {}, []
    learn = {}                                          # (Gläubiger, Referenz) → Mitglied | None (löschen) | False (nicht merken)
    count = {}
    for item in batch['items']:
        if item['mandate']:
            count[learn_key(item)] = count.get(learn_key(item), 0) + 1
    for item in batch['items']:
        chosen = choices.get(str(item['no'])) if str(item['no']) in choices else choices.get(item['no'])
        label = f"Einzelzahlung „{item['name'][:40]}“ ({_euro_text(item['amount'])} €)"
        explicit_choice = chosen is not None and isinstance(chosen, dict) and chosen.get('confirmed') != 'bulk'
        if chosen is None:
            if item['match'] and not item['match'].get('sure'):
                raise ValueError(f'{label}: Vorschlag „{item["match"]["reason"]}“ bitte bestätigen oder ändern.')
            chosen = item['match']
        if not isinstance(chosen, dict):
            raise ValueError('Ungültige Zuordnung.')
        if chosen.get('external') is not None:
            x = beitraege.external(store, chosen['external'])
            key = (beitraege._fold(x['name']), x['number'])
            externals[key] = {**x, 'amount': externals.get(key, {}).get('amount', 0) + item['amount']}
        elif chosen.get('case') is not None:             # unklar: Klärfall für die Mitgliederverwaltung (bleibt auf 8100 ohne Mitglied)
            raw, ref = chosen['case'] if isinstance(chosen['case'], dict) else {}, learn_key(item)
            keep = len(ref[1]) >= 5 and _compact(ref[1]) not in ix['refs'] and count.get(ref, 0) == 1   # nach dem Klären merken
            info = [item.get('payer') and item['name'] and f"Kontoinhaber {item['name']}", item['mandate'] and f"Mandat {item['mandate']}", item['text']]
            import klaerfaelle
            cases.append({'case': klaerfaelle.FromFile({'name': item.get('payer') or item['name'] or 'unbekannt', 'info': ' · '.join(x for x in info if x),
                                                        'reason': raw.get('reason') if isinstance(raw.get('reason'), str) else '', 'origin': 'SEPA-Datei',
                                                        **({'creditor': ref[0], 'mandate': ref[1]} if keep else {})}),
                          'amount': item['amount']})
        elif chosen.get('invoice_id'):
            iid = int(chosen['invoice_id'])
            if iid not in ix['open_by_id']:
                raise ValueError(f'{label}: Die Rechnung ist nicht (mehr) offen.')
            explicit[iid] = explicit.get(iid, 0) + item['amount']
        elif chosen.get('member_id'):
            mid = int(chosen['member_id'])
            if mid not in ix['members']:
                raise ValueError(f'{label}: Mitglied nicht gefunden.')
            pools[mid] = pools.get(mid, 0) + item['amount']
        else:
            raise ValueError(f'{label} ist noch keinem Mitglied zugeordnet.')
        key = learn_key(item)
        if len(key[1]) >= 5 and _compact(key[1]) not in ix['refs']:   # eigene Mandate kennt das Programm schon
            if count.get(key, 0) > 1:
                learn[key] = False
            elif chosen.get('external') is not None:
                learn[key] = None
            elif explicit_choice:
                mid = chosen.get('member_id') or (ix['open_by_id'][int(chosen['invoice_id'])]['member_id'] if chosen.get('invoice_id') else None)
                learn[key] = int(mid) if mid else False
    left = {i: inv['open'] for i, inv in ix['open_by_id'].items()}
    invoices = {}
    for iid, cents in explicit.items():                 # Rechnungsnummer bzw. eigene Lastschrift: diese Rechnung, Überschuss zum Mitglied
        take = min(cents, left[iid])
        invoices[iid], left[iid] = take, left[iid] - take
        if cents > take:
            inv = ix['open_by_id'][iid]
            if inv['kind'] != 'dues' or not inv['member_id']:   # z. B. Seminarrechnung: Überschuss ist kein Mitgliedsbeitrag
                raise ValueError(f"Die Zahlungen auf Rechnung {inv['number']} übersteigen ihren offenen Betrag – die überzählige Einzelzahlung "
                                 'bitte einem Mitglied (als Beitrag) zuordnen oder die Datei prüfen.')
            pools[inv['member_id']] = pools.get(inv['member_id'], 0) + cents - take
    members = {}
    for mid, cents in pools.items():
        for inv in _current(ix['dues_open'].get(mid, []), year):
            take = min(cents, left[inv['id']])
            if take > 0:
                invoices[inv['id']] = invoices.get(inv['id'], 0) + take
                left[inv['id']] -= take
                cents -= take
        if cents:
            members[mid] = cents                         # Rest (z. B. Vorauszahlung, Beitrag ohne Rechnung) auf 8100
    return {k: v for k, v in invoices.items() if v}, members, externals, {k: v for k, v in learn.items() if v is not False}, cases


def _batch(prev, batch_no, choices):
    if not isinstance(batch_no, int) or isinstance(batch_no, bool) or not 0 <= batch_no < len(prev['batches']):
        raise ValueError('Sammelbuchung der Datei nicht gefunden.')
    if not isinstance(choices, dict):
        raise ValueError('Ungültige Zuordnung.')
    batch = prev['batches'][batch_no]
    if batch['note'] and 'weicht' in batch['note']:
        raise ValueError(batch['note'] + ' Datei prüfen.')
    return batch


def validate(store, prev, batch_no, bank_id, choices):
    """Wie apply, ohne zu schreiben – damit ein Fehler gemeldet wird, bevor eine Sicherung angelegt wird."""
    batch = _batch(prev, batch_no, choices)
    cand = next((c for c in candidates(store, batch) if c['bank_id'] == bank_id), None)
    if not cand:
        raise ValueError('Die Sammelbuchung passt nicht (mehr) oder ist schon aufgeteilt – Kontoauszug prüfen und Datei erneut einlesen.')
    invoices, _members, _externals, _learn, _cases = _targets(store, batch, choices, _index(store))
    if cand['mode'] == 'assign' and invoices:
        raise ValueError(ASSIGN_INVOICES)


ASSIGN_INVOICES = ('Die Sammelbuchung ist schon auf 8100 gebucht; Zahlungen auf offene Rechnungen lassen sich so nicht zuordnen '
                   '(der Beitrag stünde doppelt im Haushalt). Die Buchung im Buchungsplatz stornieren und die Datei erneut einlesen – '
                   'dann wird neu aufgeteilt.')


def apply(store, prev, batch_no, bank_id, choices, actor):
    """Sammler aufteilen. choices: {Nr. der Einzelzahlung: {'member_id'}, {'invoice_id'} oder {'external': {name, number, kv, reason}}
    (Person nicht in der Mitgliederliste, z. B. ausgetreten)} – überschreibt bzw. bestätigt die Vorschläge.
    Jede Einzelzahlung braucht ein Ziel. Liefert die Kennzahlen und den Datensatz für den Beleg (id in sepa_files)."""
    import beitraege
    import klaerfaelle
    batch = _batch(prev, batch_no, choices)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        cand = next((c for c in candidates(store, batch) if c['bank_id'] == bank_id), None)
        if not cand:
            raise ValueError('Die Sammelbuchung passt nicht (mehr) oder ist schon aufgeteilt – Kontoauszug prüfen und Datei erneut einlesen.')
        ix = _index(store)
        invoices, members, externals, learn, cases = _targets(store, batch, choices, ix)   # in der Transaktion: offene Beträge aktuell
        member_items = ([{'member_id': mid, 'amount': _euro_text(c)} for mid, c in members.items()]
                        + [{'external': klaerfaelle.FromFile({**x, 'origin': 'SEPA-Datei'}), 'amount': _euro_text(x['amount'])} for x in externals.values()]
                        + [{'case': c['case'], 'amount': _euro_text(c['amount'])} for c in cases])
        if cand['mode'] == 'book':
            parts = [{'invoice_id': iid, 'amount': _euro_text(c)} for iid, c in invoices.items()]
            if member_items:
                parts.append({'account': '8100', 'kv': 'LV', 'members': member_items, 'amount': _euro_text(
                    sum(members.values()) + sum(x['amount'] for x in externals.values()) + sum(c['amount'] for c in cases))})
            store._book(bank_id, parts, actor, confirm_no_bill=True)   # offene Beitragsrechnungen sind oben schon zuerst bedient
        else:
            if invoices:
                raise ValueError(ASSIGN_INVOICES)
            beitraege.assign_in_transaction(store, cand['entry_id'], member_items, actor, replace_cases=True)
        now = datetime.now().isoformat(timespec='seconds')
        rnd = store.db.execute('SELECT reversals FROM bank WHERE id=?', (bank_id,)).fetchone()[0] or 0
        fid = store.db.execute('INSERT INTO sepa_files(bank_id,hash,filename,stored,kind,ref,count,total,created,actor,round) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (bank_id, prev['hash'], prev['filename'][:200], None, prev['kind'], batch['ref'][:70], len(batch['items']),
                                batch['total'], datetime.now().isoformat(timespec='seconds'), actor, rnd)).lastrowid
        changes = []                                     # gemerkte Mandatsreferenzen: Referenz und Mitglieds-ids (ohne Namen) fürs Protokoll
        for (creditor, ref), mid in learn.items():
            old = ix['learned'].get((creditor, ref))
            if mid is None and old:
                store.db.execute('DELETE FROM sepa_mandates WHERE creditor=? AND mandate=?', (creditor, ref))
                changes.append({'referenz': ref, 'vorher': old, 'nachher': None})
            elif mid and mid != old:
                store.db.execute('INSERT INTO sepa_mandates(creditor,mandate,member_id,file_id,learned,actor) VALUES(?,?,?,?,?,?) ON CONFLICT(creditor,mandate) '
                                 'DO UPDATE SET member_id=excluded.member_id,file_id=excluded.file_id,learned=excluded.learned,actor=excluded.actor',
                                 (creditor, ref, mid, fid, now, actor))
                changes.append({'referenz': ref, 'vorher': old, 'nachher': mid})
        store.audit(actor, 'SEPA-Sammler aufgeteilt', {'bank': bank_id, 'datei': prev['filename'][:200], 'hash': prev['hash'][:16],
                                                       'einzelzahlungen': len(batch['items']), 'mitglieder': len(members), 'rechnungen': len(invoices),
                                                       'nicht_in_liste': len(externals), 'klaerfaelle': len(cases), 'mandate_gemerkt': changes[:300],
                                                       'art': 'verbucht' if cand['mode'] == 'book' else 'zugeordnet'})
    return {'id': fid, 'mode': cand['mode'], 'members': len(members), 'invoices': len(invoices), 'external': len(externals), 'cases': len(cases),
            'count': len(batch['items']),
            'learned': sum(1 for c in changes if c['nachher'])}


def on_bank_reversed(store, bank_id):
    """Storno einer aufgeteilten Sammelbuchung: dabei gemerkte Mandatsreferenzen gelten nicht mehr (innerhalb der Storno-Transaktion)."""
    return store.db.execute('DELETE FROM sepa_mandates WHERE file_id IN (SELECT id FROM sepa_files WHERE bank_id=?)', (bank_id,)).rowcount


def learn_one(store, creditor, mandate, member_id, now, actor):
    """Mandatsreferenz aus einem geklärten Klärfall merken (member_id) bzw. eine gemerkte Zuordnung löschen (None: Person steht nicht
    in der Mitgliederliste) – innerhalb der Transaktion des Aufrufers. Die Voraussetzungen (Länge, nicht mehrfach in der Datei, kein
    eigenes Mandat) hat _targets beim Anlegen des Klärfalls geprüft; ein eigenes Mandat mit dieser Referenz geht weiter vor.
    Bewusst ohne file_id: Die Zuordnung hat ein Mensch im Klärfall getroffen – sie bleibt auch nach einem Storno der Sammelbuchung
    (anders als die beim Aufteilen gemerkten, on_bank_reversed) und lässt sich im Mitglied entfernen."""
    creditor, mandate = str(creditor or ''), str(mandate or '').strip()
    if len(mandate) < 5 or store.db.execute('SELECT 1 FROM mandates WHERE reference=?', (mandate,)).fetchone():
        return
    if member_id is None:
        store.db.execute('DELETE FROM sepa_mandates WHERE creditor=? AND mandate=?', (creditor, mandate))
    else:
        store.db.execute('INSERT INTO sepa_mandates(creditor,mandate,member_id,file_id,learned,actor) VALUES(?,?,?,NULL,?,?) ON CONFLICT(creditor,mandate) '
                         'DO UPDATE SET member_id=excluded.member_id,file_id=NULL,learned=excluded.learned,actor=excluded.actor',
                         (creditor, mandate, int(member_id), now, actor))


def learned_for(store, member_id):
    """Gemerkte Mandatsreferenzen eines Mitglieds (Ansicht im Mitglied, dort einzeln entfernbar)."""
    return store.rows('SELECT creditor,mandate,learned FROM sepa_mandates WHERE member_id=? ORDER BY learned DESC', (int(member_id),))


def forget(store, creditor, mandate, actor):
    with store.db:
        row = store.db.execute('SELECT member_id FROM sepa_mandates WHERE creditor=? AND mandate=?', (str(creditor or ''), str(mandate or ''))).fetchone()
        if not row:
            raise ValueError('Gemerkte Mandatsreferenz nicht gefunden.')
        store.db.execute('DELETE FROM sepa_mandates WHERE creditor=? AND mandate=?', (str(creditor or ''), str(mandate or '')))
        store.audit(actor, 'Gemerkte Mandatsreferenz entfernt', {'referenz': str(mandate)[:35], 'mitglied': row[0]})


def set_stored(store, file_id, name):
    with store.db:
        store.db.execute('UPDATE sepa_files SET stored=? WHERE id=?', (name, file_id))


def files_for_year(store, year):
    """Belege (SEPA-Dateien) zu Sammelbuchungen eines Jahres – für das Kassenprüfer-Paket."""
    return store.rows("SELECT f.*,b.booked FROM sepa_files f JOIN bank b ON b.id=f.bank_id WHERE substr(b.booked,1,4)=? "
                      'ORDER BY b.booked,f.id', (str(year),))   # stored leer: Datei konnte nicht gespeichert werden (Hinweis im Paket)
