"""Ausgangsrechnungen als E-Rechnung: ZUGFeRD/Factur-X, Profil EN 16931 (COMFORT) – ab 0.59, REGELN Nr. 38.

Eine Datei: die gewohnte Rechnungs-PDF als PDF/A-3b mit den Rechnungsdaten als eingebettete `factur-x.xml`
(UN/CEFACT Cross Industry Invoice). Gilt für festgeschriebene, nicht stornierte Beitrags- und Ausgangsrechnungen mit Betrag –
einzeln per E-Mail oder als Download. Vorschauen, Sammel-PDF, Zahlungserinnerungen und stornierte Rechnungen bleiben normale PDF.

Eine Rechnungsnummer hat einen Inhalt: Die Rechnungsdaten kommen nur aus der Rechnung und ihrem festgeschriebenen Abbild
(`snapshot`), nie aus den heutigen Einstellungen. Fehlte bei der Ausstellung eine Pflichtangabe oder stammt die Rechnung von vor 0.59,
bleibt sie für immer eine normale PDF; der Schalter entscheidet nur, ob eine vollständige Rechnung als E-Rechnung hinausgeht.
Wie auf der PDF folgen nur Zahlungsstand und Zahlungsweg dem heutigen Stand (bereits bezahlt, entfallene Lastschrift).
Geht die bisherige PDF hinaus, nennt `pdf` den Grund – nie eine ungültige E-Rechnung, nie ein Fehler.
Umsatzsteuer: Beitragsrechnungen sind „nicht steuerbar“ (Kategorie O); für andere Rechnungen entscheidet die festgeschriebene
Angabe `tax_category` (O oder E = steuerbefreit), der Begründungstext ist der Steuerhinweis der Rechnung.
Ohne Zusatzbibliothek: XML mit der Standardbibliothek, Einbetten mit pypdf (ist ohnehin Abhängigkeit, sepadatei.py).
"""
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

import lastschrift
import stammdaten
from formate import iban_compact, iban_valid
from pdfschrift import covers

RSM = 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100'
RAM = 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100'
UDT = 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100'
QDT = 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100'
PROFIL = 'urn:cen.eu:en16931:2017'          # Kennung des Profils EN 16931 in der XML (BT-24)
DATEI = 'factur-x.xml'                      # vorgeschriebener Name der eingebetteten Datei
# sRGB-Farbprofil für PDF/A (OutputIntent): Compact-ICC-Profiles von Clinton Ingram, CC0 – farbprofil/LIZENZ.txt
ICC = Path(__file__).resolve().parent / 'farbprofil' / 'sRGB-v2-micro.icc'
KATEGORIEN = {'O': 'nicht steuerbar', 'E': 'steuerbefreit'}
for _kurz, _uri in (('rsm', RSM), ('ram', RAM), ('udt', UDT), ('qdt', QDT)):
    ET.register_namespace(_kurz, _uri)
_ORT = re.compile(r'(?:D-|DE-)?(\d{5})\s+(\S.*)')
_UNERLAUBT = re.compile(r'[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')   # in XML 1.0 nicht erlaubte Zeichen
_TAG = re.compile(r'\d{4}-\d{2}-\d{2}')
UNERWARTET = 'E-Rechnung konnte nicht erzeugt werden (unerwarteter Fehler)'
melden = lambda wo: None   # app.py setzt hier `log_error` ein: Art und Programmstelle der laufenden Ausnahme ins Serverprotokoll


def anschrift(text):
    """Freitext-Anschrift → {'zeilen': [höchstens 3], 'plz', 'ort'}; None, wenn die letzte Zeile nicht „PLZ Ort“ (Deutschland) ist."""
    zeilen = [' '.join(z.split()) for z in str(text or '').splitlines() if z.strip()]
    if zeilen and zeilen[-1].casefold() in ('deutschland', 'germany'):
        zeilen.pop()
    m = _ORT.fullmatch(zeilen[-1]) if zeilen else None
    if not m:
        return None
    davor = zeilen[:-1]
    return {'zeilen': davor[:2] + ([', '.join(davor[2:])] if davor[2:] else []), 'plz': m[1], 'ort': m[2]}


def kategorie(kind, e):
    """Umsatzsteuer-Kategorie der Rechnung: Beitrag immer O, sonst laut Angabe ('' = noch nicht entschieden).
    `e` trägt die Angaben `stammdaten.ERECHNUNG`: das Abbild der Rechnung, für künftige Rechnungen die Einstellungen."""
    return 'O' if kind == 'dues' else e.get('tax_category') or ''


def luecken(kind, e, issuer_address):
    """Was dem Aussteller für E-Rechnungen dieser Art fehlt (Texte für die Oberfläche; leer = vollständig)."""
    cat, out = kategorie(kind, e), []
    if cat not in KATEGORIEN:
        out.append('Umsatzsteuer normaler Rechnungen (nicht steuerbar oder steuerbefreit)')
    elif cat == 'E' and not (e.get('tax_number') or e.get('vat_id')):      # BR-E-01
        out.append('Steuernummer oder USt-IdNr.')
    elif cat == 'O' and not (e.get('tax_number') or e.get('creditor_id')):  # BR-CO-26; eine USt-IdNr. darf bei O nicht stehen (BR-O-02)
        out.append('Steuernummer oder Gläubiger-ID')
    if not anschrift(issuer_address):
        out.append('Anschrift des Ausstellers mit Postleitzahl und Ort in der letzten Zeile')
    return out


def stand(cfg):
    """Stand für die Oberfläche: Schalter und was Rechnungen, die ab jetzt ausgestellt werden, je Rechnungsart fehlt.
    Für schon ausgestellte Rechnungen zählt allein ihr Abbild (`fehlt`)."""
    return {'an': cfg.get('erechnung') == 'yes', **{kind: luecken(kind, cfg, cfg.get('issuer_address')) for kind in ('dues', 'normal')}}


def faellig(inv):
    """Fälligkeit wie auf der PDF: Einzugsdatum der geltenden Lastschrift, sonst die Zahlungsfrist."""
    from rechnung_pdf import debit_of
    debit = debit_of(inv)
    return debit['date'] if debit else lastschrift.payable_by(inv)


def fehlt(inv):
    """Grund, warum diese Rechnung als normale PDF hinausgeht – None, wenn sie eine gültige E-Rechnung wird.
    Bis auf Schalter, Erinnerung und Storno hängt der Grund nur an der Rechnung selbst: er ändert sich später nicht mehr."""
    snap = inv['snap']
    if inv.get('preview') or 'erechnung' not in inv:
        return 'Vorschau'
    if not inv['erechnung']:
        return 'E-Rechnung ausgeschaltet'
    if inv.get('reminder'):
        return 'Zahlungserinnerung'
    if inv['status'] != 'issued':
        return 'storniert'
    if snap.get('pilot'):
        return 'Pilotrechnung aus einer Vorversion'
    if inv['total'] <= 0 or not inv['lines']:
        return 'Rechnung ohne Betrag'
    if any(k not in snap for k in stammdaten.ERECHNUNG):
        return 'vor Einführung der E-Rechnung ausgestellt'
    offen = luecken(inv['kind'], snap, snap.get('issuer_address'))
    if offen:
        return 'Bei der Ausstellung fehlte: ' + '; '.join(offen)
    if not anschrift(inv['address']):
        return 'Anschrift des Empfängers endet nicht mit Postleitzahl und Ort (nur Deutschland)'
    if not iban_valid(snap.get('iban')):
        return 'IBAN des Ausstellers ist ungültig'
    # direkt veränderte Altdaten: das Programm selbst schreibt solche Rechnungen nicht fest
    if not (_t(snap.get('issuer')) and _t(inv['recipient']) and all(_t(l['description']) for l in inv['lines'])
            and _TAG.fullmatch(str(inv['issued'])[:10]) and _TAG.fullmatch(str(faellig(inv) or '')[:10])):
        return 'Aussteller, Empfänger, Datum, Fälligkeit oder Positionstext fehlt'
    if sum(l['amount'] for l in inv['lines']) != inv['total']:
        return 'Summe der Positionen weicht vom Rechnungsbetrag ab'
    texte = [inv['recipient'], inv['address'], *(l['description'] for l in inv['lines']), *snap.values(), *(snap.get('debit') or {}).values()]
    if not all(covers(z) for t in texte if isinstance(t, str) for z in t.split('\n')):   # sonst stünden Kästchen in der PDF – und PDF/A verbietet fehlende Zeichen
        return 'Die Rechnung enthält Zeichen, die die PDF-Schrift nicht kennt'
    return None


def grund(inv):
    """Wie `fehlt`, scheitert aber nie: unerwartete Daten sind auch ein Grund für die normale PDF (für `pdf` und die E-Mail-Vorschau)."""
    try:
        return fehlt(inv)
    except Exception:
        melden('E-Rechnung prüfen')
        return UNERWARTET


# ---------------------------------------------------------------- XML

def _t(text):
    return _UNERLAUBT.sub('', ' '.join(str(text or '').split()))


def _el(parent, name, text=None, ns=RAM, **attrs):
    el = ET.SubElement(parent, f'{{{ns}}}{name}', attrs)
    if text is not None:
        el.text = _t(text)
    return el


def _betrag(cents):
    return str((Decimal(cents) / 100).quantize(Decimal('0.01')))


def _datum(parent, name, iso_day):
    _el(_el(parent, name), 'DateTimeString', iso_day[:10].replace('-', ''), ns=UDT, format='102')


def _partei(parent, name, kennung, bezeichnung, adr):
    p = _el(parent, name)
    if kennung:
        _el(p, 'ID', kennung)
    _el(p, 'Name', bezeichnung)
    a = _el(p, 'PostalTradeAddress')
    _el(a, 'PostcodeCode', adr['plz'])
    for tag, zeile in zip(('LineOne', 'LineTwo', 'LineThree'), adr['zeilen']):
        _el(a, tag, zeile)
    _el(a, 'CityName', adr['ort'])
    _el(a, 'CountryID', 'DE')
    return p


def xml(inv):
    """Rechnungsdaten als Cross Industry Invoice (EN 16931). Nur aufrufen, wenn `fehlt(inv)` None ist."""
    from rechnung_pdf import amount_due, debit_of
    snap, debit = inv['snap'], debit_of(inv)
    cat = kategorie(inv['kind'], snap)
    begruendung = _t(snap.get('tax_note')) or f'Umsatzsteuer: {KATEGORIEN[cat]}'   # nie ein leeres Element (auch nicht bei einem Hinweis nur aus Leerraum)

    root = ET.Element(f'{{{RSM}}}CrossIndustryInvoice')
    _el(_el(_el(root, 'ExchangedDocumentContext', ns=RSM), 'GuidelineSpecifiedDocumentContextParameter'), 'ID', PROFIL)
    doc = _el(root, 'ExchangedDocument', ns=RSM)
    _el(doc, 'ID', inv['number'])
    _el(doc, 'TypeCode', '380')
    _datum(doc, 'IssueDateTime', inv['issued'])
    tx = _el(root, 'SupplyChainTradeTransaction', ns=RSM)

    for i, line in enumerate(inv['lines'], 1):                       # eine Position = 1 Stück zum Positionsbetrag
        item = _el(tx, 'IncludedSupplyChainTradeLineItem')
        _el(_el(item, 'AssociatedDocumentLineDocument'), 'LineID', i)
        _el(_el(item, 'SpecifiedTradeProduct'), 'Name', line['description'])
        _el(_el(_el(item, 'SpecifiedLineTradeAgreement'), 'NetPriceProductTradePrice'), 'ChargeAmount', _betrag(line['amount']))
        _el(_el(item, 'SpecifiedLineTradeDelivery'), 'BilledQuantity', '1', unitCode='C62')
        settle = _el(item, 'SpecifiedLineTradeSettlement')
        tax = _el(settle, 'ApplicableTradeTax')
        _el(tax, 'TypeCode', 'VAT')
        _el(tax, 'CategoryCode', cat)
        if cat != 'O':                                                # bei „nicht steuerbar“ darf kein Steuersatz stehen (BR-O-05)
            _el(tax, 'RateApplicablePercent', '0')
        _el(_el(settle, 'SpecifiedTradeSettlementLineMonetarySummation'), 'LineTotalAmount', _betrag(line['amount']))

    agreement = _el(tx, 'ApplicableHeaderTradeAgreement')
    seller = _partei(agreement, 'SellerTradeParty', snap['creditor_id'] or snap['tax_number'], snap.get('issuer'), anschrift(snap.get('issuer_address')))
    if snap['tax_number']:
        _el(_el(seller, 'SpecifiedTaxRegistration'), 'ID', snap['tax_number'], schemeID='FC')
    if snap['vat_id'] and cat != 'O':
        _el(_el(seller, 'SpecifiedTaxRegistration'), 'ID', snap['vat_id'], schemeID='VA')
    _partei(agreement, 'BuyerTradeParty', snap.get('member_number'), inv['recipient'], anschrift(inv['address']))
    _el(_el(_el(tx, 'ApplicableHeaderTradeDelivery'), 'ShipToTradeParty'), 'Name', inv['recipient'])   # Leistungsempfänger = Rechnungsempfänger; leere Elemente rügt die Prüfung

    settle = _el(tx, 'ApplicableHeaderTradeSettlement')
    if debit:
        _el(settle, 'CreditorReferenceID', debit['creditor_id'])
    _el(settle, 'PaymentReference', inv['number'])
    _el(settle, 'InvoiceCurrencyCode', 'EUR')
    means = _el(settle, 'SpecifiedTradeSettlementPaymentMeans')
    if debit:                                                         # das Konto des Mitglieds steht nur gekürzt in der Rechnung – nicht in die XML
        _el(means, 'TypeCode', '59')
    else:
        _el(means, 'TypeCode', '58')
        konto = _el(means, 'PayeePartyCreditorFinancialAccount')
        _el(konto, 'IBANID', iban_compact(snap['iban']))
        _el(konto, 'AccountName', snap.get('issuer'))
        if snap.get('bic'):
            _el(_el(means, 'PayeeSpecifiedCreditorFinancialInstitution'), 'BICID', snap['bic'])
    tax = _el(settle, 'ApplicableTradeTax')
    _el(tax, 'CalculatedAmount', '0.00')
    _el(tax, 'TypeCode', 'VAT')
    _el(tax, 'ExemptionReason', begruendung)
    _el(tax, 'BasisAmount', _betrag(inv['total']))
    _el(tax, 'CategoryCode', cat)
    if cat == 'O':
        _el(tax, 'ExemptionReasonCode', 'VATEX-EU-O')
    else:
        _el(tax, 'RateApplicablePercent', '0')
    if inv['kind'] == 'dues' and inv.get('year'):                     # Beitragsjahr als Abrechnungszeitraum
        period = _el(settle, 'BillingSpecifiedPeriod')
        _datum(period, 'StartDateTime', f"{inv['year']}-01-01")
        _datum(period, 'EndDateTime', f"{inv['year']}-12-31")
    terms = _el(settle, 'SpecifiedTradePaymentTerms')
    _datum(terms, 'DueDateDateTime', faellig(inv))
    if debit:
        _el(terms, 'DirectDebitMandateID', debit['mandate'])
    total = _el(settle, 'SpecifiedTradeSettlementHeaderMonetarySummation')
    _el(total, 'LineTotalAmount', _betrag(inv['total']))
    _el(total, 'TaxBasisTotalAmount', _betrag(inv['total']))
    _el(total, 'TaxTotalAmount', '0.00', currencyID='EUR')
    _el(total, 'GrandTotalAmount', _betrag(inv['total']))
    if inv.get('paid'):
        _el(total, 'TotalPrepaidAmount', _betrag(inv['paid']))
    _el(total, 'DuePayableAmount', _betrag(amount_due(inv)))
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


# ---------------------------------------------------------------- PDF/A-3 mit eingebetteter XML

_XMP = '''<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/"><pdfaid:part>3</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance></rdf:Description>
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title><rdf:Alt><rdf:li xml:lang="x-default">{titel}</rdf:li></rdf:Alt></dc:title>
<dc:creator><rdf:Seq><rdf:li>{autor}</rdf:li></rdf:Seq></dc:creator></rdf:Description>
<rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/"><pdf:Producer>{programm}</pdf:Producer></rdf:Description>
<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"><xmp:CreatorTool>{programm}</xmp:CreatorTool>
<xmp:CreateDate>{zeit}</xmp:CreateDate><xmp:ModifyDate>{zeit}</xmp:ModifyDate></rdf:Description>
<rdf:Description rdf:about="" xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/" xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#" xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#">
<pdfaExtension:schemas><rdf:Bag><rdf:li rdf:parseType="Resource"><pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema>
<pdfaSchema:namespaceURI>urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#</pdfaSchema:namespaceURI><pdfaSchema:prefix>fx</pdfaSchema:prefix>
<pdfaSchema:property><rdf:Seq>{eigenschaften}</rdf:Seq></pdfaSchema:property></rdf:li></rdf:Bag></pdfaExtension:schemas></rdf:Description>
<rdf:Description rdf:about="" xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#"><fx:DocumentType>INVOICE</fx:DocumentType>
<fx:DocumentFileName>''' + DATEI + '''</fx:DocumentFileName><fx:Version>1.0</fx:Version><fx:ConformanceLevel>EN 16931</fx:ConformanceLevel></rdf:Description>
</rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>'''
_EIGENSCHAFTEN = ''.join(
    f'<rdf:li rdf:parseType="Resource"><pdfaProperty:name>{name}</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType>'
    f'<pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>{text}</pdfaProperty:description></rdf:li>'
    for name, text in (('DocumentFileName', 'Name of the embedded XML invoice file'), ('DocumentType', 'INVOICE'),
                       ('Version', 'Version of the Factur-X XML schema'), ('ConformanceLevel', 'Conformance level of the embedded Factur-X data')))
PROGRAMM = 'JU Hamburg Finanzen'


def einbetten(pdf, xml_bytes, titel, autor):
    """Rechnungs-PDF (reportlab) → PDF/A-3b mit eingebetteter Rechnungs-XML: XMP mit Factur-X-Schema, Farbprofil (OutputIntent),
    Datei `factur-x.xml` mit /AFRelationship /Alternative. Schriften bettet rechnung_pdf schon ein (pdfschrift)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, create_string_object
    N, text = NameObject, create_string_object
    jetzt = datetime.now(timezone.utc).replace(microsecond=0)
    pdf_zeit = jetzt.strftime("D:%Y%m%d%H%M%S+00'00'")
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    w.pdf_header = '%PDF-1.7'
    w.metadata = {'/Title': titel, '/Author': autor, '/Creator': PROGRAMM, '/Producer': PROGRAMM, '/CreationDate': pdf_zeit, '/ModDate': pdf_zeit}

    def strom(data, **eintraege):
        s = DecodedStreamObject()
        s.set_data(data)
        s.update({N('/' + k): v for k, v in eintraege.items()})
        return w._add_object(s)

    datei = strom(xml_bytes, Type=N('/EmbeddedFile'), Subtype=N('/text/xml'),
                  Params=DictionaryObject({N('/ModDate'): text(pdf_zeit), N('/Size'): NumberObject(len(xml_bytes))}))
    spec = w._add_object(DictionaryObject({
        N('/Type'): N('/Filespec'), N('/F'): text(DATEI), N('/UF'): text(DATEI), N('/Desc'): text('Rechnungsdaten (ZUGFeRD/Factur-X)'),
        N('/AFRelationship'): N('/Alternative'), N('/EF'): DictionaryObject({N('/F'): datei, N('/UF'): datei})}))
    xmp = _XMP.format(titel=escape(titel), autor=escape(autor), programm=PROGRAMM, zeit=jetzt.strftime('%Y-%m-%dT%H:%M:%S+00:00'), eigenschaften=_EIGENSCHAFTEN)
    root = w.root_object
    root[N('/Metadata')] = strom(xmp.encode('utf-8'), Type=N('/Metadata'), Subtype=N('/XML'))   # unkomprimiert, wie PDF/A verlangt
    root[N('/AF')] = ArrayObject([spec])
    namen = root.setdefault(N('/Names'), DictionaryObject())
    namen[N('/EmbeddedFiles')] = DictionaryObject({N('/Names'): ArrayObject([text(DATEI), spec])})
    root[N('/OutputIntents')] = ArrayObject([w._add_object(DictionaryObject({
        N('/Type'): N('/OutputIntent'), N('/S'): N('/GTS_PDFA1'), N('/OutputConditionIdentifier'): text('sRGB'), N('/Info'): text('sRGB'),
        N('/DestOutputProfile'): strom(ICC.read_bytes(), N=NumberObject(3))}))])
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def pdf(inv):
    """Rechnungs-PDF für Versand und Download: (E-Rechnung, None) oder (bisherige PDF, Grund). Scheitert nie an der E-Rechnung."""
    import rechnung_pdf
    einfach = rechnung_pdf.render([inv])
    warum = grund(inv)
    if warum:
        return einfach, warum
    try:
        return einbetten(einfach, xml(inv), inv['number'], _t(inv['snap'].get('issuer'))), None
    except Exception:   # Versand und Download gehen vor: lieber die bisherige PDF als gar keine
        melden('E-Rechnung erzeugen')
        return einfach, UNERWARTET
