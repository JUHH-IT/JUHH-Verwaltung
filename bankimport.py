"""Kontoauszüge (CSV oder Excel .xls) in ein einheitliches Zeilenformat überführen.

Ergebnis je Datenzeile: row_no, booked (ISO-Datum), amount (Cent), name, reference,
bank_ref – oder ein Feld ``error``. Hier wird nichts gebucht und nichts gespeichert;
Dublettenprüfung und Import erfolgen in ``core.Store``.
"""
import csv
import io
import re
from datetime import datetime

from formate import cents_from_number, iso, money

# Spaltenbezeichnungen (normalisiert) je Feld. Die Reihenfolge legt den Vorrang fest:
# Gibt es Buchungsdatum und Valuta, gilt das Buchungsdatum.
FIELDS = {
    'booked': ['buchungsdatum', 'buchungstag', 'datum', 'valuta', 'valutadatum', 'wertstellung', 'wertstellungsdatum'],
    'amount': ['betrag', 'betrag (eur)', 'betrag in eur', 'betrag (€)', 'umsatz', 'umsatz in eur'],
    'name': ['name', 'zahlungspartner', 'empfänger', 'zahlungsempfänger', 'beguenstigter/zahlungspflichtiger',
             'begünstigter/zahlungspflichtiger', 'auftraggeber/empfänger', 'empfänger/auftraggeber', 'name zahlungsbeteiligter'],
    'reference': ['verwendungszweck', 'beschreibung', 'buchungstext'],
    'bank_ref': ['bankreferenz', 'referenz'],
    'currency': ['währung', 'waehrung'],
}
PLACEHOLDER_REFS = {'', 'notprovided', 'nonref', 'keine angabe'}
IBAN = re.compile(r'[A-Z]{2}\d{2}[A-Z0-9]{11,30}')
MAX_ROWS = 20000


def _norm(value):
    return ' '.join(str(value or '').strip().strip('"').casefold().split())


def map_header(cells):
    """Ordnet Spaltenüberschriften Feldern zu. Liefert {feld: spaltenindex} oder None."""
    names = [_norm(c) for c in cells]
    mapping = {}
    for field, aliases in FIELDS.items():
        for alias in aliases:
            if alias in names:
                mapping[field] = names.index(alias)
                break
    return mapping if 'booked' in mapping and 'amount' in mapping else None


def split_combined(text):
    """Leitet bei Bankexporten ohne Namensspalte den Zahlungspartner aus dem Verwendungszweck ab.

    Aufbau typischer Texte: ``BUCHUNGSART Name … IBAN Zweck``. Ohne IBAN werden die Wörter
    nach der Buchungsart bis zum ersten Wort mit Ziffern übernommen (höchstens fünf).
    Der vollständige Originaltext bleibt immer als Verwendungszweck erhalten.
    """
    tokens = str(text or '').split()
    if tokens and re.fullmatch(r'[A-ZÄÖÜ][A-ZÄÖÜ\-]{2,}', tokens[0]):
        tokens = tokens[1:]
    for i, token in enumerate(tokens):
        if IBAN.fullmatch(token):
            return ' '.join(tokens[:i])[:80]
    name = []
    for token in tokens[:5]:
        if re.search(r'\d', token):
            break
        name.append(token)
    return ' '.join(name)[:80]


def _row(row_no, values, mapping, combined):
    def get(field):
        index = mapping.get(field)
        return values[index] if index is not None and index < len(values) else ''

    item = {'row_no': row_no}
    try:
        raw_date, raw_amount = get('booked'), get('amount')
        if isinstance(raw_date, datetime):
            booked = raw_date.date().isoformat()
        else:
            booked = iso(raw_date)
        amount = cents_from_number(raw_amount) if isinstance(raw_amount, (int, float)) else money(raw_amount)
        if not amount:
            raise ValueError('Nullbetrag ist kein Bankumsatz.')
        currency = str(get('currency') or '').strip().upper()
        if currency and currency not in ('EUR', '€'):
            raise ValueError('Nur Umsätze in EUR werden unterstützt.')
        reference = ' '.join(str(get('reference') or '').split())
        name = ' '.join(str(get('name') or '').split())
        if combined and not name:
            name = split_combined(reference)
        bank_ref = str(get('bank_ref') or '').strip()
        item.update(booked=booked, amount=amount, name=name, reference=reference,
                    bank_ref=None if bank_ref.casefold() in PLACEHOLDER_REFS else bank_ref)
    except ValueError as e:
        item['error'] = str(e)
    return item


def _is_blank(values):
    return all(str(v if v is not None else '').strip() == '' for v in values)


def parse_csv(raw):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    lines = text.splitlines()
    for start, line in enumerate(lines[:40]):
        for delimiter in (';', '\t', ','):
            cells = next(csv.reader([line], delimiter=delimiter), [])
            mapping = map_header(cells)
            if mapping:
                rows = []
                reader = csv.reader(io.StringIO('\n'.join(lines[start + 1:])), delimiter=delimiter)
                for offset, values in enumerate(reader):
                    if _is_blank(values):
                        continue
                    rows.append(_row(start + 2 + offset, values, mapping, 'name' not in mapping))
                return rows
    raise ValueError('Kopfzeile nicht erkannt. Benötigt werden mindestens Datum (oder Buchungstag/Valuta) und Betrag.')


def parse_xls(raw):
    import xlrd
    try:
        book = xlrd.open_workbook(file_contents=raw)
    except Exception:
        raise ValueError('Excel-Datei konnte nicht gelesen werden. Bitte den Originalexport der Bank (.xls) verwenden.')
    for sheet in book.sheets():
        header = None
        for r in range(min(sheet.nrows, MAX_ROWS)):
            mapping = map_header(sheet.row_values(r))
            if mapping:
                header = (r, mapping)
                break
        if not header:
            continue
        header_row, mapping = header
        rows = []
        for r in range(min(sheet.nrows, MAX_ROWS)):
            if r == header_row:
                continue
            values = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    values.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                elif cell.ctype == xlrd.XL_CELL_NUMBER:
                    values.append(cell.value)
                else:
                    values.append(str(cell.value).strip())
            if _is_blank(values):
                continue
            rows.append(_row(r + 1, values, mapping, 'name' not in mapping))
        return rows
    raise ValueError('Kopfzeile nicht gefunden. Erwartet werden Spalten wie „Valuta“, „Verwendungszweck“ und „Betrag“.')


def parse_statement(raw, filename=''):
    """Erkennt das Format am Dateiinhalt und liefert die Datenzeilen."""
    if raw[:4] == b'\xd0\xcf\x11\xe0':
        rows = parse_xls(raw)
    elif raw[:2] == b'PK':
        raise ValueError('.xlsx wird noch nicht unterstützt. Bitte den Originalexport der Bank (.xls) oder CSV verwenden.')
    else:
        rows = parse_csv(raw)
    if not rows:
        raise ValueError('Keine Datenzeilen gefunden.')
    if len(rows) > MAX_ROWS:
        raise ValueError('Zu viele Zeilen in einer Datei.')
    return rows
