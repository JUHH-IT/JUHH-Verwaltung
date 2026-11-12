"""E-Rechnungen einlesen: XRechnung (UBL oder CII als XML) und ZUGFeRD/Factur-X (PDF mit eingebetteter CII-XML).

Gelesen wird nur, was die Erfassung als Eingangsrechnung braucht (Kreditor, Rechnungsnummer, Datum, Fälligkeit,
zu zahlender Betrag, IBAN/BIC, Verwendungszweck, Positionen). Gebucht wird erst, wenn das ausgefüllte Formular geprüft
und abgeschickt ist. Ohne zusätzliche Bibliotheken: eingebettete Dateien werden direkt aus den PDF-Datenströmen gelesen
(unkomprimiert oder FlateDecode). Sicherheit: höchstens 10 MB, kein DOCTYPE (keine Entitäten), nur Euro.
"""
import io
import re
import zlib
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from formate import euro, german_date, iban_compact, iban_valid

CII = 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100'
RAM = 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'
UDT = 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100'
UBL = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
UBL_CREDIT = 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2'
CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS = {'rsm': CII, 'ram': RAM, 'udt': UDT, 'ubl': UBL, 'cac': CAC, 'cbc': CBC}
MAX = 10_000_000
DEBIT_MEANS = ('49', '59')  # Lastschrift: der Kreditor zieht ein – nicht überweisen
TRANSFER_MEANS = ('30', '31', '42', '58')  # Überweisung (UNTDID 4461)
OTHER_MEANS = {'10': 'bar', '20': 'Scheck', '48': 'Karte', '54': 'Kreditkarte', '55': 'Debitkarte', '97': 'Verrechnung'}
# Gutschriften und Korrekturgutschriften (UNTDID 1001) – nie als Eingangsrechnung buchen
CREDIT_TYPES = {'81', '83', '261', '262', '296', '308', '381', '396', '420', '458', '532'}
MAX_STREAMS = 5000  # so viele Datenströme einer PDF werden höchstens durchsucht
_STREAM = re.compile(rb'stream\r?\n')
_OBJ = re.compile(rb'\d+\s+\d+\s+obj')
_SIGNATURES = (b'CrossIndustryInvoice', UBL.encode(), UBL_CREDIT.encode())


# ---------------------------------------------------------------- Datei → XML

def extract(raw):
    """XML der E-Rechnung aus einer XML- oder PDF-Datei (bytes)."""
    if len(raw) > MAX:
        raise ValueError('Datei ist zu groß (höchstens 10 MB).')
    if raw[:4] == b'%PDF':
        return _from_pdf(raw)
    if raw.lstrip(b'\xef\xbb\xbf \t\r\n')[:1] == b'<':
        return raw
    raise ValueError('Keine E-Rechnung: bitte XRechnung (XML) oder ZUGFeRD-/Factur-X-PDF wählen.')


def _streams(raw):
    """(Wörterbuch, Daten) je Datenstrom der PDF, höchstens MAX_STREAMS."""
    pos = 0
    for _ in range(MAX_STREAMS):
        m = _STREAM.search(raw, pos)
        if not m:
            return
        start, end = m.end(), raw.find(b'endstream', m.end())
        if end < 0:
            return
        objects = list(_OBJ.finditer(raw, max(0, m.start() - 4000), m.start()))
        yield (raw[objects[-1].end():m.start()] if objects else b''), raw[start:end]
        pos = end + 9


def _xml_in(dictionary, data):
    """Rechnungs-XML aus einem Datenstrom (unkomprimiert oder FlateDecode), sonst None; Ausgabe begrenzt auf MAX."""
    try:
        if b'/FlateDecode' in dictionary:
            head = zlib.decompressobj().decompress(data, 4096)
            if any(s in head for s in _SIGNATURES):
                return zlib.decompressobj().decompress(data, MAX)
        elif b'/Filter' not in dictionary and any(s in data[:4096] for s in _SIGNATURES):
            return data.rstrip(b'\r\n')
    except zlib.error:
        pass
    return None


def _from_pdf(raw):
    """Eingebettete Rechnungs-XML einer PDF: zuerst die als Datei eingebetteten Datenströme (/EmbeddedFile), dann alle übrigen."""
    streams = list(_streams(raw))
    for embedded in (True, False):
        for dictionary, data in streams:
            if (b'/EmbeddedFile' in dictionary) == embedded:
                xml = _xml_in(dictionary, data)
                if xml:
                    return xml
    raise ValueError('In der PDF steckt keine E-Rechnung (ZUGFeRD/Factur-X) – bitte als normalen Beleg erfassen.')


# ---------------------------------------------------------------- XML → Angaben

def _text(node, path):
    found = node.find(path, NS) if node is not None else None
    return ' '.join((found.text or '').split()) if found is not None and found.text else ''


def _cents(value, label):
    try:
        d = Decimal(value)
        if not d.is_finite() or abs(d) >= 10 ** 9:
            raise InvalidOperation
        return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'E-Rechnung: {label} fehlt oder ist keine gültige Zahl.') from None


def _payment(means, code_path, iban_path, bic_path):
    """Zahlungsweg aus allen Zahlungsangaben: Lastschrift hat Vorrang; IBAN nur von einer Überweisungsangabe."""
    codes = [_text(m, code_path) for m in means]
    debit = any(c in DEBIT_MEANS for c in codes)
    transfer = next((m for m in means if _text(m, code_path) in TRANSFER_MEANS and _text(m, iban_path)), None)
    other = [OTHER_MEANS[c] for c in codes if c in OTHER_MEANS]
    return {'debit': debit, 'other': other if not debit and transfer is None else [],
            'iban': '' if debit or transfer is None else iban_compact(_text(transfer, iban_path)),
            'bic': '' if debit or transfer is None else _text(transfer, bic_path).replace(' ', '').upper()}


def _date(value):
    v = (value or '').strip()
    try:
        if re.fullmatch(r'\d{8}', v):
            return date(int(v[:4]), int(v[4:6]), int(v[6:])).isoformat()
        return date.fromisoformat(v[:10]).isoformat() if v else ''
    except ValueError:
        raise ValueError(f'E-Rechnung: Datum „{v}“ ist ungültig.') from None


def parse(xml):
    """Angaben aus der XML (CII oder UBL). Wirft ValueError mit verständlichem Grund."""
    if len(xml) > MAX:
        raise ValueError('E-Rechnung ist zu groß.')
    if re.search(rb'<!(DOCTYPE|ENTITY)', xml, re.I):
        raise ValueError('E-Rechnung enthält eine DOCTYPE-Angabe und wird aus Sicherheitsgründen nicht gelesen.')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError('E-Rechnung ist kein gültiges XML.') from None
    if root.tag == f'{{{CII}}}CrossIndustryInvoice':
        out = _cii(root)
    elif root.tag == f'{{{UBL}}}Invoice':
        out = _ubl(root)
    elif root.tag == f'{{{UBL_CREDIT}}}CreditNote':
        raise ValueError('Das ist eine Gutschrift (CreditNote) – bitte nicht als Eingangsrechnung erfassen.')
    else:
        raise ValueError('Unbekanntes Format – erwartet wird eine XRechnung oder ZUGFeRD/Factur-X-Rechnung.')
    if out['type'] in CREDIT_TYPES:
        raise ValueError(f"Das ist eine Gutschrift (Rechnungsart {out['type']}) – bitte nicht als Eingangsrechnung erfassen.")
    if out['type'] == '389':
        out['warnings'].append('Gutschriftsverfahren (vom Leistungsempfänger ausgestellt) – bitte prüfen, ob es wirklich eine Rechnung an uns ist.')
    if out['other']:
        out['warnings'].append(f"Zahlungsweg laut Rechnung: {', '.join(dict.fromkeys(out['other']))} – prüfen, ob noch etwas zu zahlen ist; IBAN nicht übernommen.")
    if out['currency'] != 'EUR':
        raise ValueError(f"Rechnung in {out['currency'] or 'unbekannter Währung'} – nur Euro-Rechnungen werden eingelesen.")
    for key, label in (('number', 'Rechnungsnummer'), ('issued', 'Rechnungsdatum'), ('creditor', 'Rechnungssteller')):
        if not out[key]:
            raise ValueError(f'E-Rechnung: {label} fehlt.')
    if out['total'] <= 0:
        raise ValueError('E-Rechnung: zu zahlender Betrag ist 0 oder negativ – bitte prüfen und manuell erfassen.')
    if out['due'] and out['due'] < out['issued']:
        out['warnings'].append('Fälligkeit liegt vor dem Rechnungsdatum – Fälligkeit bitte prüfen.')
        out['due'] = ''
    if out['iban'] and not iban_valid(out['iban']):
        out['warnings'].append(f"IBAN {out['iban']} ist ungültig – nicht übernommen.")
        out['iban'] = ''
    if out['debit']:
        out['warnings'].append('Die Rechnung wird per Lastschrift eingezogen – nicht überweisen; die Abbuchung später zuordnen.')
    if out['paid']:
        out['warnings'].append(f"Laut Rechnung bereits {euro(out['paid'])} bezahlt – zu zahlen ist der Restbetrag.")
    out['bill_lines'] = _bill_lines(out)
    return out


def _cii(root):
    doc = root.find('rsm:ExchangedDocument', NS)
    tx = root.find('rsm:SupplyChainTradeTransaction', NS)
    agreement = tx.find('ram:ApplicableHeaderTradeAgreement', NS) if tx is not None else None
    settlement = tx.find('ram:ApplicableHeaderTradeSettlement', NS) if tx is not None else None
    type_code = _text(doc, 'ram:TypeCode')
    summary = settlement.find('ram:SpecifiedTradeSettlementHeaderMonetarySummation', NS) if settlement is not None else None
    pay = _payment(settlement.findall('ram:SpecifiedTradeSettlementPaymentMeans', NS) if settlement is not None else [], 'ram:TypeCode',
                   'ram:PayeePartyCreditorFinancialAccount/ram:IBANID', 'ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID')
    payable = _text(summary, 'ram:DuePayableAmount') or _text(summary, 'ram:GrandTotalAmount')
    lines = []
    for item in (tx.findall('ram:IncludedSupplyChainTradeLineItem', NS) if tx is not None else []):
        amount = _text(item, 'ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount')
        lines.append({'description': _text(item, 'ram:SpecifiedTradeProduct/ram:Name')[:120], 'amount': _cents(amount, 'Positionsbetrag') if amount else 0})
    profile = _text(root, 'rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID')
    return {'format': 'XRechnung (CII)' if 'xrechnung' in profile.lower() else 'ZUGFeRD/Factur-X (CII)', 'profile': profile, 'type': type_code,
            'number': _text(doc, 'ram:ID')[:60], 'issued': _date(_text(doc, 'ram:IssueDateTime/udt:DateTimeString')),
            'due': _date(_text(settlement, 'ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString')),
            'creditor': _text(agreement, 'ram:SellerTradeParty/ram:Name')[:120],
            'buyer_reference': _text(agreement, 'ram:BuyerReference'),
            **pay, 'reference': _text(settlement, 'ram:PaymentReference')[:140],
            'currency': _text(settlement, 'ram:InvoiceCurrencyCode'),
            'total': _cents(payable, 'zu zahlender Betrag'),
            'gross': _cents(_text(summary, 'ram:GrandTotalAmount') or payable, 'Bruttobetrag'),
            'net': _cents(_text(summary, 'ram:TaxBasisTotalAmount') or '0', 'Nettobetrag'),
            'tax': _cents(_text(summary, 'ram:TaxTotalAmount') or '0', 'Umsatzsteuer'),
            'paid': _cents(_text(summary, 'ram:TotalPrepaidAmount') or '0', 'Vorauszahlung'),
            'lines': lines, 'warnings': []}


def _ubl(root):
    party = root.find('cac:AccountingSupplierParty/cac:Party', NS)
    means = root.findall('cac:PaymentMeans', NS)
    pay = _payment(means, 'cbc:PaymentMeansCode', 'cac:PayeeFinancialAccount/cbc:ID', 'cac:PayeeFinancialAccount/cac:FinancialInstitutionBranch/cbc:ID')
    first = means[0] if means else None
    total = root.find('cac:LegalMonetaryTotal', NS)
    lines = [{'description': _text(line, 'cac:Item/cbc:Name')[:120],
              'amount': _cents(_text(line, 'cbc:LineExtensionAmount') or '0', 'Positionsbetrag')} for line in root.findall('cac:InvoiceLine', NS)]
    profile = _text(root, 'cbc:CustomizationID')
    return {'format': 'XRechnung (UBL)' if 'xrechnung' in profile.lower() else 'E-Rechnung (UBL)', 'profile': profile,
            'type': _text(root, 'cbc:InvoiceTypeCode'), 'number': _text(root, 'cbc:ID')[:60],
            'issued': _date(_text(root, 'cbc:IssueDate')), 'due': _date(_text(root, 'cbc:DueDate') or _text(root, 'cac:PaymentTerms/cbc:PaymentDueDate')),
            'creditor': (_text(party, 'cac:PartyLegalEntity/cbc:RegistrationName') or _text(party, 'cac:PartyName/cbc:Name'))[:120],
            'buyer_reference': _text(root, 'cbc:BuyerReference'),
            **pay, 'reference': _text(first, 'cbc:PaymentID')[:140],
            'currency': _text(root, 'cbc:DocumentCurrencyCode'),
            'total': _cents(_text(total, 'cbc:PayableAmount'), 'zu zahlender Betrag'),
            'gross': _cents(_text(total, 'cbc:TaxInclusiveAmount') or _text(total, 'cbc:PayableAmount'), 'Bruttobetrag'),
            'net': _cents(_text(total, 'cbc:TaxExclusiveAmount') or '0', 'Nettobetrag'),
            'tax': _cents(_text(root, 'cac:TaxTotal/cbc:TaxAmount') or '0', 'Umsatzsteuer'),
            'paid': _cents(_text(total, 'cbc:PrepaidAmount') or '0', 'Vorauszahlung'),
            'lines': lines, 'warnings': []}


def _bill_lines(d):
    """Positionen für das Formular: die Rechnungspositionen, wenn sie zusammen den zu zahlenden Betrag ergeben
    (z. B. ohne Umsatzsteuer), sonst eine Position über den zu zahlenden Betrag (brutto – Vorsteuer wird nicht abgezogen)."""
    lines = [l for l in d['lines'] if l['amount'] > 0 and l['description']]
    if lines and len(lines) <= 20 and sum(l['amount'] for l in lines) == d['total']:
        return lines
    names = ', '.join(dict.fromkeys(l['description'] for l in lines))[:100]
    return [{'description': names or f"Rechnung {d['number']}", 'amount': d['total']}]


def check(raw):
    """Nur die Form prüfen (für XML-Belege): wohlgeformt, kein DOCTYPE, Rechnung oder Gutschrift im XRechnung-Format.
    Fachliche Regeln (Euro, Betrag > 0) gelten nur beim Vorbelegen des Formulars – auch bezahlte Rechnungen sind Belege."""
    if raw[:4] == b'%PDF':
        raise ValueError('Das ist eine PDF – bitte mit der Endung .pdf anhängen.')
    xml = extract(raw)
    if re.search(rb'<!(DOCTYPE|ENTITY)', xml, re.I):
        raise ValueError('XML mit DOCTYPE-Angabe wird aus Sicherheitsgründen nicht angenommen.')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError('Beleg ist kein gültiges XML.') from None
    if root.tag not in (f'{{{CII}}}CrossIndustryInvoice', f'{{{UBL}}}Invoice', f'{{{UBL_CREDIT}}}CreditNote'):
        raise ValueError('XML-Belege nur als E-Rechnung (XRechnung oder ZUGFeRD/Factur-X).')


def read(raw):
    """Datei (bytes) → Angaben der E-Rechnung."""
    return parse(extract(raw))


# ---------------------------------------------------------------- Sichtfassung

def view_pdf(d):
    """Lesbare Fassung einer XML-Rechnung (für Belegansicht und Kassenprüfung) – ersetzt nicht die XML."""
    from pdfschrift import exclusive
    return exclusive(_view_pdf)(d)


def _view_pdf(d):
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from pdfschrift import SANS, SANS_BOLD
    st = {'t': ParagraphStyle('t', fontName=SANS_BOLD, fontSize=14, leading=18, textColor=colors.HexColor('#043770')),
          'b': ParagraphStyle('b', fontName=SANS, fontSize=9, leading=12), 's': ParagraphStyle('s', fontName=SANS, fontSize=7.5, leading=10, textColor=colors.HexColor('#5b6b7f')),
          'n': ParagraphStyle('n', fontName=SANS, fontSize=9, leading=12, alignment=2)}
    p = lambda text, style='b': Paragraph(escape(str(text)), st[style])
    facts = [('Rechnungssteller', d['creditor']), ('Rechnungsnummer', d['number']), ('Rechnungsdatum', german_date(d['issued'])),
             ('Fällig', german_date(d['due']) or '—'), ('Zu zahlen', euro(d['total'])), ('Netto / USt / Brutto', f"{euro(d['net'])} / {euro(d['tax'])} / {euro(d['gross'])}"),
             ('Zahlung', 'Lastschrift durch den Rechnungssteller' if d['debit'] else f"Überweisung an {d['iban'] or '—'}{(' · ' + d['bic']) if d['bic'] else ''}"),
             ('Verwendungszweck', d['reference'] or '—'), ('Käuferreferenz', d['buyer_reference'] or '—'), ('Format', f"{d['format']} {d['profile']}".strip())]
    table = Table([[p(k, 's'), p(v)] for k, v in facts], colWidths=[130, 350],
                  style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#d5dde8'))]))
    lines = Table([[p('Position', 's'), p('Betrag (netto)', 's')]] + ([[p(l['description']), p(euro(l['amount']), 'n')] for l in d['lines']] or [[p('—'), p('')]]),
                  colWidths=[380, 100], style=TableStyle([('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#d5dde8'))]))
    story = [p('Sichtfassung einer E-Rechnung', 't'), p('Aus der XML-Datei erzeugt. Maßgeblich ist die XML-Datei der Rechnung.', 's'),
             Spacer(1, 10), table, Spacer(1, 12), lines]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=50, rightMargin=50, topMargin=45, bottomMargin=45, title=f"E-Rechnung {d['number']}").build(story)
    return buf.getvalue()
