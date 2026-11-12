"""E-Rechnungen einlesen (XRechnung CII/UBL, ZUGFeRD/Factur-X) – nur erfundene Testdaten."""
import base64
import tempfile
import unittest
import zlib
from pathlib import Path

import erechnung as e
import verbindlichkeiten as v
from core import Store

IBAN = 'DE89370400440532013000'          # bekannte Beispiel-IBAN

CII = f'''<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
 <rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter>
  <ram:ID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter></rsm:ExchangedDocumentContext>
 <rsm:ExchangedDocument><ram:ID>RE-2026-0815</ram:ID><ram:TypeCode>{{type}}</ram:TypeCode>
  <ram:IssueDateTime><udt:DateTimeString format="102">20261020</udt:DateTimeString></ram:IssueDateTime></rsm:ExchangedDocument>
 <rsm:SupplyChainTradeTransaction>
  <ram:IncludedSupplyChainTradeLineItem><ram:SpecifiedTradeProduct><ram:Name>Flyer A5 &amp; Plakate</ram:Name></ram:SpecifiedTradeProduct>
   <ram:SpecifiedLineTradeSettlement><ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>100.00</ram:LineTotalAmount>
   </ram:SpecifiedTradeSettlementLineMonetarySummation></ram:SpecifiedLineTradeSettlement></ram:IncludedSupplyChainTradeLineItem>
  <ram:ApplicableHeaderTradeAgreement><ram:BuyerReference>04011000-12345-67</ram:BuyerReference>
   <ram:SellerTradeParty><ram:Name>Druckerei Muster GmbH</ram:Name></ram:SellerTradeParty></ram:ApplicableHeaderTradeAgreement>
  <ram:ApplicableHeaderTradeSettlement><ram:PaymentReference>RE-2026-0815</ram:PaymentReference><ram:InvoiceCurrencyCode>{{currency}}</ram:InvoiceCurrencyCode>
   <ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>{{means}}</ram:TypeCode>
    <ram:PayeePartyCreditorFinancialAccount><ram:IBANID>{IBAN}</ram:IBANID></ram:PayeePartyCreditorFinancialAccount></ram:SpecifiedTradeSettlementPaymentMeans>
   <ram:SpecifiedTradePaymentTerms><ram:DueDateDateTime><udt:DateTimeString format="102">20261103</udt:DateTimeString></ram:DueDateDateTime></ram:SpecifiedTradePaymentTerms>
   <ram:SpecifiedTradeSettlementHeaderMonetarySummation><ram:LineTotalAmount>100.00</ram:LineTotalAmount><ram:TaxBasisTotalAmount>100.00</ram:TaxBasisTotalAmount>
    <ram:TaxTotalAmount currencyID="EUR">19.00</ram:TaxTotalAmount><ram:GrandTotalAmount>119.00</ram:GrandTotalAmount><ram:DuePayableAmount>119.00</ram:DuePayableAmount>
   </ram:SpecifiedTradeSettlementHeaderMonetarySummation></ram:ApplicableHeaderTradeSettlement>
 </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>'''

UBL = (Path(__file__).resolve().parent / 'tests' / 'beispiel-xrechnung.xml').read_text(encoding='utf-8')  # auch für den Browser-Durchlauf


def cii(type_='380', currency='EUR', means='58'):
    return CII.replace('{type}', type_).replace('{currency}', currency).replace('{means}', means).encode()


def zugferd_pdf(xml, compress=True):
    """Kleinste PDF mit eingebetteter Rechnung (wie ZUGFeRD/Factur-X: Datenstrom mit der XML), davor ein Bildstrom."""
    data = zlib.compress(xml) if compress else xml
    image = zlib.compress(bytes(range(256)) * 40)
    parts = [b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n',
             b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [(factur-x.xml) 5 0 R] >> >> >>\nendobj\n',
             b'2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n',
             b'3 0 obj\n<< /Type /XObject /Subtype /Image /Filter /FlateDecode /Length %d >>\nstream\n' % len(image) + image + b'\nendstream\nendobj\n',
             b'4 0 obj\n<< /Type /EmbeddedFile /Subtype /text#2Fxml ' + (b'/Filter /FlateDecode ' if compress else b'')
             + b'/Length %d >>\nstream\n' % len(data) + data + b'\nendstream\nendobj\n',
             b'5 0 obj\n<< /Type /Filespec /F (factur-x.xml) /AFRelationship /Data /EF << /F 4 0 R >> >>\nendobj\n',
             b'trailer\n<< /Root 1 0 R >>\n%%EOF\n']
    return b''.join(parts)


class ParseTests(unittest.TestCase):
    def test_cii_xrechnung(self):
        d = e.read(cii())
        self.assertEqual((d['format'], d['number'], d['issued'], d['due'], d['creditor']),
                         ('XRechnung (CII)', 'RE-2026-0815', '2026-10-20', '2026-11-03', 'Druckerei Muster GmbH'))
        self.assertEqual((d['total'], d['net'], d['tax'], d['iban'], d['reference']), (11900, 10000, 1900, IBAN, 'RE-2026-0815'))
        self.assertEqual(d['bill_lines'], [{'description': 'Flyer A5 & Plakate', 'amount': 11900}])   # netto ≠ brutto → eine Position
        self.assertTrue(e.view_pdf(d).startswith(b'%PDF'))

    def test_ubl_xrechnung_with_lines(self):
        d = e.read(UBL.encode())
        self.assertEqual((d['format'], d['creditor'], d['iban'], d['bic'], d['total'], d['reference']),
                         ('XRechnung (UBL)', 'Saalvermietung Muster e.K.', IBAN, 'COBADEFFXXX', 25000, '2026-77 JU'))
        self.assertEqual(d['bill_lines'], [{'description': 'Saalmiete', 'amount': 20000}, {'description': 'Reinigung', 'amount': 5000}])

    def test_zugferd_pdf(self):
        for compress in (True, False):
            with self.subTest(compress=compress):
                self.assertEqual(e.read(zugferd_pdf(cii(), compress))['number'], 'RE-2026-0815')
        with self.assertRaises(ValueError):
            e.read(zugferd_pdf(b'<foo/>'))                                                      # PDF ohne Rechnung

    def test_real_pdf_library_attachment(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest('pypdf nicht installiert')
        import io
        writer = PdfWriter()
        writer.add_blank_page(595, 842)
        writer.add_attachment('factur-x.xml', cii())
        buf = io.BytesIO()
        writer.write(buf)
        self.assertEqual(e.read(buf.getvalue())['total'], 11900)

    def test_rejections(self):
        bad = {'credit note': cii('381'), 'currency': cii(currency='USD'), 'not xml': b'Hallo', 'broken': b'<a>',
               'doctype': b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>',
               'ubl credit': b'<CreditNote xmlns="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"/>',
               'other xml': b'<?xml version="1.0"?><root/>', 'too big': b'<' + b'a' * (e.MAX + 1)}
        for name, raw in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                e.read(raw)

    def test_credit_notes_in_both_syntaxes(self):
        for code in ('381', '261', '396'):
            with self.subTest(cii=code), self.assertRaises(ValueError):
                e.read(cii(code))
        with self.assertRaises(ValueError):
            e.read(UBL.replace('<cbc:InvoiceTypeCode>380', '<cbc:InvoiceTypeCode>381').encode())
        self.assertTrue(any('Gutschriftsverfahren' in w for w in e.read(cii('389'))['warnings']))

    def test_payment_means(self):
        two = UBL.replace('<cac:PaymentMeans>', '<cac:PaymentMeans><cbc:PaymentMeansCode>30</cbc:PaymentMeansCode></cac:PaymentMeans><cac:PaymentMeans>', 1)
        self.assertEqual(e.read(two.encode())['iban'], IBAN)                                   # IBAN aus der zweiten Angabe
        debit_second = UBL.replace('</cac:PaymentMeans>', '</cac:PaymentMeans><cac:PaymentMeans><cbc:PaymentMeansCode>59</cbc:PaymentMeansCode></cac:PaymentMeans>', 1)
        d = e.read(debit_second.encode())
        self.assertEqual((d['debit'], d['iban']), (True, ''))
        card = e.read(cii(means='48'))
        self.assertEqual(card['iban'], '')
        self.assertTrue(any('Karte' in w for w in card['warnings']))

    def test_bad_numbers(self):
        for value in ('Infinity', '1E+50', 'NaN', 'abc'):
            with self.subTest(value), self.assertRaises(ValueError):
                e.read(cii().replace(b'<ram:DuePayableAmount>119.00', b'<ram:DuePayableAmount>' + value.encode()))

    def test_receipt_check_is_structural(self):
        paid = cii().replace(b'<ram:DuePayableAmount>119.00', b'<ram:DuePayableAmount>0.00')
        with self.assertRaises(ValueError):
            e.read(paid)                                                                      # nichts zu zahlen → nicht vorbelegen
        e.check(paid)                                                                         # als Beleg aber in Ordnung
        e.check(cii(currency='USD'))
        for bad in (zugferd_pdf(cii()), b'<html/>', b'<?xml version="1.0"?><!DOCTYPE x><x/>'):
            with self.subTest(bad[:12]), self.assertRaises(ValueError):
                e.check(bad)

    def test_direct_debit_invoice_has_no_iban(self):
        d = e.read(cii(means='59'))
        self.assertTrue(d['debit'])
        self.assertEqual(d['iban'], '')                                                         # bei Lastschrift ist die IBAN unsere
        self.assertTrue(any('Lastschrift' in w for w in d['warnings']))


class BillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_einvoice_in_audit_package(self):
        import berichte
        d = e.read(UBL.encode())
        bid, number, filename = v.create(self.s, {'creditor': d['creditor'], 'iban': d['iban'], 'ext_number': d['number'], 'issued': d['issued'], 'due': d['due'],
                                                   'kv': 'LV', 'lines': [{'description': 'Saal', 'amount': '250', 'account': '4210', 'title': 'H27'}]}, 'Test', '.xml')
        folder = Path(self.tmp.name) / 'Belege'
        folder.mkdir()
        (folder / filename).write_bytes(UBL.encode())
        files, notes = berichte.audit_files(self.s, 2026, Path(self.tmp.name))
        self.assertEqual(files[f'Belege/{number}-{filename}'], UBL.encode())
        self.assertTrue(files[f'Belege/{number}-Sichtfassung.pdf'].startswith(b'%PDF'))
        self.assertFalse([n for n in notes if number in n])

    def test_xml_receipt_only_as_einvoice(self):
        ok = base64.b64encode(UBL.encode()).decode()
        self.assertEqual(v.check_file('rechnung.xml', ok)[0], '.xml')
        self.assertEqual(v.check_file('rechnung.XML', base64.b64encode(b'\xef\xbb\xbf' + UBL.encode()).decode())[0], '.xml')
        with self.assertRaises(ValueError):
            v.check_file('beliebig.xml', base64.b64encode(b'<?xml version="1.0"?><html><script>x</script></html>').decode())


if __name__ == '__main__':
    unittest.main()
