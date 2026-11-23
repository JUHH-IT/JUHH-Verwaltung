"""Ausgangsrechnungen als E-Rechnung (ZUGFeRD/Factur-X, EN 16931) – nur erfundene Testdaten, Versand nur an den lokalen Testserver.

Ohne Prüfwerkzeug von außen: Rundlauf über erechnung.py, Aufbau der PDF/A-3 und die Reihenfolge der XML-Elemente, wie sie am
23.11.2026 mit Mustang 2.26.0 (veraPDF, Schema, Schematron) als gültig geprüft wurde – siehe werkzeuge/zugferd_beispiele.py.
"""
import email
import hashlib
import io
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from email import policy
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent / 'tests'))
sys.path.insert(0, str(Path(__file__).parent / 'werkzeuge'))

import erechnung
import geheimnis
import rechnung_pdf
import stammdaten
import versand
import zugferd
import zugferd_beispiele
from fake_smtp import FakeSMTP
from pypdf import PdfReader
from test_rechnungen import IBAN, Base

RAM = '{' + zugferd.RAM + '}'
GLAEUBIGER = 'DE98ZZZ09999999999'
# Reihenfolge aller Elemente, so von Schema und Schematron angenommen – wer sie ändert, prüft neu (werkzeuge/zugferd_beispiele.py)
KOPF = ('CrossIndustryInvoice ExchangedDocumentContext GuidelineSpecifiedDocumentContextParameter ID ExchangedDocument ID TypeCode IssueDateTime '
        'DateTimeString SupplyChainTradeTransaction ')
POSITION = ('IncludedSupplyChainTradeLineItem AssociatedDocumentLineDocument LineID SpecifiedTradeProduct Name SpecifiedLineTradeAgreement '
            'NetPriceProductTradePrice ChargeAmount SpecifiedLineTradeDelivery BilledQuantity SpecifiedLineTradeSettlement ApplicableTradeTax TypeCode '
            'CategoryCode {satz}SpecifiedTradeSettlementLineMonetarySummation LineTotalAmount ')
LASTSCHRIFT = (KOPF + POSITION.format(satz='') + 'ApplicableHeaderTradeAgreement SellerTradeParty ID Name PostalTradeAddress PostcodeCode LineOne LineTwo '
               'CityName CountryID SpecifiedTaxRegistration ID BuyerTradeParty ID Name PostalTradeAddress PostcodeCode LineOne CityName CountryID '
               'ApplicableHeaderTradeDelivery ShipToTradeParty Name ApplicableHeaderTradeSettlement CreditorReferenceID PaymentReference '
               'InvoiceCurrencyCode SpecifiedTradeSettlementPaymentMeans TypeCode ApplicableTradeTax CalculatedAmount TypeCode ExemptionReason '
               'BasisAmount CategoryCode ExemptionReasonCode BillingSpecifiedPeriod StartDateTime DateTimeString EndDateTime DateTimeString '
               'SpecifiedTradePaymentTerms DueDateDateTime DateTimeString DirectDebitMandateID SpecifiedTradeSettlementHeaderMonetarySummation '
               'LineTotalAmount TaxBasisTotalAmount TaxTotalAmount GrandTotalAmount DuePayableAmount')
STEUERBEFREIT = (KOPF + POSITION.format(satz='RateApplicablePercent ') * 2 + 'ApplicableHeaderTradeAgreement SellerTradeParty ID Name PostalTradeAddress '
                 'PostcodeCode LineOne LineTwo CityName CountryID SpecifiedTaxRegistration ID SpecifiedTaxRegistration ID BuyerTradeParty Name '
                 'PostalTradeAddress PostcodeCode LineOne LineTwo LineThree CityName CountryID ApplicableHeaderTradeDelivery ShipToTradeParty Name '
                 'ApplicableHeaderTradeSettlement PaymentReference InvoiceCurrencyCode SpecifiedTradeSettlementPaymentMeans TypeCode '
                 'PayeePartyCreditorFinancialAccount IBANID AccountName PayeeSpecifiedCreditorFinancialInstitution BICID ApplicableTradeTax '
                 'CalculatedAmount TypeCode ExemptionReason BasisAmount CategoryCode RateApplicablePercent SpecifiedTradePaymentTerms DueDateDateTime '
                 'DateTimeString SpecifiedTradeSettlementHeaderMonetarySummation LineTotalAmount TaxBasisTotalAmount TaxTotalAmount GrandTotalAmount '
                 'TotalPrepaidAmount DuePayableAmount')


def texte(root, name):
    return [e.text for e in root.iter(RAM + name)]


class Fall(Base):
    """Aussteller mit Gläubiger-ID: Beitragsrechnungen sind sofort E-Rechnungen, normale erst mit der Entscheidung zur Umsatzsteuer."""

    def setUp(self):
        super().setUp()
        self.s.save_settings({'creditor_id': GLAEUBIGER, 'erechnung': 'yes'}, 'Test')   # Vorgabe ist aus: Lennert schaltet ein, wenn die Umsatzsteuer-Einordnung bestätigt ist

    def beitrag(self, **member):
        iid = self.s.issue_dues(2026, [self.member(**member)], 'Test')[0]
        return rechnung_pdf.invoice_data(self.s, iid)

    def rechnung(self, **recipient):
        data = self.series(recipients=[{'name': 'Muster GmbH', 'address': 'Lange Reihe 5\n20099 Hamburg', 'email': 'muster@example.org', **recipient}])
        return rechnung_pdf.invoice_data(self.s, self.s.issue_series(data, 'Test')[0])


class BeispielTests(unittest.TestCase):
    """Die Beispiele des Prüfwerkzeugs: jede Datei lässt sich wieder einlesen und hat den geprüften Aufbau."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ordner = Path(cls.tmp.name)
        cls.namen = zugferd_beispiele.beispiele(cls.ordner)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rundlauf_jedes_beispiels(self):
        self.assertEqual(len(self.namen), 9)
        for name in self.namen:
            with self.subTest(name=name):
                if name == 'rechnung-E-bezahlt':                                              # gelesen bis zum Betrag: nichts mehr zu zahlen
                    with self.assertRaisesRegex(ValueError, 'zu zahlender Betrag ist 0'):
                        erechnung.read((self.ordner / f'{name}.pdf').read_bytes())
                    continue
                d = erechnung.read((self.ordner / f'{name}.pdf').read_bytes())
                self.assertEqual((d['format'], d['profile'], d['type'], d['creditor'], d['currency'], d['tax']),
                                 ('ZUGFeRD/Factur-X (CII)', 'urn:cen.eu:en16931:2017', '380', 'Junge Union Beispielstadt', 'EUR', 0))
                self.assertTrue(d['number'] and d['issued'] and d['due'] >= d['issued'])
                self.assertEqual(d['gross'], sum(l['amount'] for l in d['lines']))            # Summe der Positionen = Rechnungsbetrag
                self.assertEqual(d['debit'], name == 'beitrag-lastschrift')
                self.assertEqual(d['iban'], '' if d['debit'] else IBAN.replace(' ', ''))
                self.assertEqual((d['paid'], d['total']), (1000, d['gross'] - 1000) if 'teilzahlung' in name else (0, d['gross']))

    def test_reihenfolge_der_elemente_wie_geprueft(self):
        for name, erwartet in (('beitrag-lastschrift', LASTSCHRIFT), ('rechnung-E-firma-teilzahlung', STEUERBEFREIT)):
            root = ET.parse(self.ordner / f'{name}.xml').getroot()
            self.assertEqual(' '.join(e.tag.split('}')[1] for e in root.iter()), erwartet, name)
            self.assertFalse([e.tag for e in root.iter() if not len(e) and not (e.text or '').strip()], 'leere Elemente rügt die Prüfung')

    def test_pdf_ist_pdfa3_mit_eingebetteter_rechnung(self):
        raw = (self.ordner / 'rechnung-O-mitglied.pdf').read_bytes()
        self.assertTrue(raw.startswith(b'%PDF-1.7\n%') and raw[10] > 127)                     # Kennzeichen „binär“ in der zweiten Zeile
        pdf = PdfReader(io.BytesIO(raw))
        root = pdf.trailer['/Root']
        self.assertEqual(len(pdf.trailer['/ID']), 2)
        meta = root['/Metadata'].get_object()
        self.assertEqual((meta['/Type'], meta['/Subtype'], meta.get('/Filter')), ('/Metadata', '/XML', None))   # unkomprimiert
        xmp = ET.fromstring(meta.get_data())                                                   # wohlgeformt
        fx, pdfaid = '{urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#}', '{http://www.aiim.org/pdfa/ns/id/}'
        self.assertEqual([xmp.find(f'.//{k}').text for k in (pdfaid + 'part', pdfaid + 'conformance', fx + 'DocumentType', fx + 'DocumentFileName',
                                                               fx + 'Version', fx + 'ConformanceLevel')], ['3', 'B', 'INVOICE', 'factur-x.xml', '1.0', 'EN 16931'])
        intent = root['/OutputIntents'][0].get_object()
        profil = intent['/DestOutputProfile'].get_object()
        self.assertEqual((intent['/S'], profil['/N'], profil.get_data()), ('/GTS_PDFA1', 3, zugferd.ICC.read_bytes()))
        name, spec = root['/Names']['/EmbeddedFiles']['/Names']
        spec = spec.get_object()
        self.assertEqual((name, spec['/F'], spec['/UF'], spec['/AFRelationship']), ('factur-x.xml',) * 3 + ('/Alternative',))
        self.assertEqual(root['/AF'][0].get_object(), spec)
        datei = spec['/EF']['/F'].get_object()
        self.assertEqual((datei['/Type'], datei['/Subtype'], datei.get_data()), ('/EmbeddedFile', '/text/xml', (self.ordner / 'rechnung-O-mitglied.xml').read_bytes()))
        self.assertIn('/ModDate', datei['/Params'])
        fonts = [f.get_object() for page in pdf.pages for f in page['/Resources']['/Font'].values()]
        eingebettet = [f for f in fonts if '/FontDescriptor' in f]
        self.assertTrue(eingebettet and all('/FontFile2' in f['/FontDescriptor'] for f in eingebettet))
        self.assertTrue(all(f['/BaseFont'].split('+')[-1].startswith('LiberationSans') for f in eingebettet))
        self.assertNotIn('/Encrypt', pdf.trailer)

    def test_farbprofil_ist_das_freie_original(self):
        self.assertEqual(hashlib.sha256(zugferd.ICC.read_bytes()).hexdigest(), '0a8a33aea66a6f154a5642ebe168ef287e73265d9f7b51c42a45e6eedbacda7a')
        self.assertIn('CC0', (zugferd.ICC.parent / 'LIZENZ.txt').read_text(encoding='utf-8'))


class InhaltTests(Fall):
    def test_beitrag_ist_nicht_steuerbar(self):
        inv = self.beitrag()
        self.assertIsNone(zugferd.fehlt(inv))
        root = ET.fromstring(zugferd.xml(inv))
        self.assertEqual(set(texte(root, 'CategoryCode')), {'O'})
        self.assertEqual((texte(root, 'RateApplicablePercent'), texte(root, 'ExemptionReasonCode')), ([], ['VATEX-EU-O']))
        self.assertEqual(texte(root, 'ExemptionReason'), ['Mitgliedsbeitrag gemäß Satzung der Jungen Union Hamburg.'])
        seller = root.find(f'.//{RAM}SellerTradeParty')
        self.assertEqual((seller.find(RAM + 'ID').text, seller.find(RAM + 'Name').text), (GLAEUBIGER, 'JU Test'))
        self.assertEqual([e.text for e in seller.iter(RAM + 'PostcodeCode')] + [e.text for e in seller.iter(RAM + 'CityName')], ['20000', 'Hamburg'])
        buyer = root.find(f'.//{RAM}BuyerTradeParty')
        self.assertEqual([e.text for e in buyer.iter() if e.text], ['M1', 'Erika Musterfrau', '20000', 'Musterweg 1', 'Hamburg', 'DE'])
        self.assertEqual(texte(root, 'IBANID'), [IBAN.replace(' ', '')])

    def test_ust_idnr_steht_nie_in_nicht_steuerbaren_rechnungen(self):
        self.s.save_settings({'vat_id': 'DE123456789', 'tax_number': '22/123/45678', 'tax_category': 'O'}, 'Test')
        ids = lambda inv: {e.get('schemeID'): e.text for e in ET.fromstring(zugferd.xml(inv)).iter(RAM + 'ID') if e.get('schemeID')}
        self.assertEqual(ids(self.rechnung()), {'FC': '22/123/45678'})                          # Regel BR-O-02
        self.s.save_settings({'tax_category': 'E'}, 'Test')
        self.assertEqual(ids(self.rechnung()), {'FC': '22/123/45678', 'VA': 'DE123456789'})

    def test_angaben_werden_mit_der_rechnung_festgeschrieben(self):
        self.s.save_settings({'tax_number': '22/123/45678'}, 'Test')
        iid = self.beitrag()['id']
        self.s.save_settings({'tax_number': '99/999/99999'}, 'Test')
        self.assertIn(b'22/123/45678', zugferd.xml(rechnung_pdf.invoice_data(self.s, iid)))

    def test_aeltere_rechnung_nutzt_die_heutigen_angaben(self):
        self.s.save_settings({'creditor_id': ''}, 'Test')
        iid = self.beitrag()['id']                                                              # festgeschrieben, bevor etwas eingetragen war
        self.assertIn('Steuernummer oder Gläubiger-ID', zugferd.fehlt(rechnung_pdf.invoice_data(self.s, iid)))
        self.s.save_settings({'tax_number': '22/123/45678'}, 'Test')
        self.assertIsNone(zugferd.fehlt(rechnung_pdf.invoice_data(self.s, iid)))

    def test_anschrift(self):
        self.assertEqual(zugferd.anschrift('c/o Muster\nHinterhof\nAufgang B\nWeg 1\nD-20099  Hamburg\nDeutschland'),
                         {'zeilen': ['c/o Muster', 'Hinterhof', 'Aufgang B, Weg 1'], 'plz': '20099', 'ort': 'Hamburg'})
        for text in ('', 'Weg 1', 'Ringstraße 1\n1010 Wien\nÖsterreich', 'Weg 1\nHamburg'):
            self.assertIsNone(zugferd.anschrift(text), text)

    def test_einstellungen_werden_geprueft(self):
        for bad in ({'tax_number': '123'}, {'tax_number': 'DE123456789'}, {'vat_id': 'DE12345'}, {'vat_id': 'ATU12345678'}, {'tax_category': 'S'}):
            with self.assertRaises(ValueError, msg=bad):
                self.s.save_settings(bad, 'Test')
        self.s.save_settings({'tax_number': ' 22/123/45678 ', 'vat_id': 'de 123456789', 'tax_category': 'E', 'erechnung': 'no'}, 'Test')
        cfg = self.s.config()
        self.assertEqual([cfg[k] for k in ('tax_number', 'vat_id', 'tax_category', 'erechnung')], ['22/123/45678', 'DE123456789', 'E', 'no'])
        self.assertEqual(stammdaten.DEFAULTS['erechnung'], 'no')   # erst nach bewusstem Einschalten gehen E-Rechnungen hinaus

    def test_stand_nennt_was_fehlt(self):
        self.assertEqual(self.s.snapshot(2026)['erechnung'], {'an': True, 'dues': [], 'normal': ['Umsatzsteuer normaler Rechnungen (nicht steuerbar oder steuerbefreit)']})
        self.s.save_settings({'creditor_id': '', 'tax_category': 'E', 'issuer_address': 'Postfach 1'}, 'Test')
        offen = ['Anschrift des Ausstellers mit Postleitzahl und Ort in der letzten Zeile']
        self.assertEqual(zugferd.stand(self.s.config()), {'an': True, 'dues': ['Steuernummer oder Gläubiger-ID'] + offen, 'normal': ['Steuernummer oder USt-IdNr.'] + offen})


class RueckfallTests(Fall):
    """Fehlt etwas, kommt genau die bisherige PDF heraus – mit Grund."""

    def einfach(self, inv, grund):
        datei, warum = zugferd.pdf(inv)
        self.assertIn(grund, warum)
        self.assertNotIn(b'factur-x', datei)
        self.assertNotIn(b'/OutputIntents', datei)
        with self.assertRaises(ValueError):
            erechnung.read(datei)

    def test_ohne_kennung_des_ausstellers(self):
        self.s.save_settings({'creditor_id': ''}, 'Test')
        self.einfach(self.beitrag(), 'In den Einstellungen fehlt: Steuernummer oder Gläubiger-ID')

    def test_normale_rechnung_ohne_entscheidung_zur_umsatzsteuer(self):
        self.einfach(self.rechnung(), 'In den Einstellungen fehlt: Umsatzsteuer normaler Rechnungen')
        self.s.save_settings({'tax_category': 'E'}, 'Test')
        self.einfach(self.rechnung(), 'Steuernummer oder USt-IdNr.')                            # Gläubiger-ID genügt bei „steuerbefreit“ nicht
        self.s.save_settings({'tax_number': '22/123/45678'}, 'Test')
        self.assertIsNone(zugferd.pdf(self.rechnung())[1])

    def test_ausgeschaltet(self):
        self.s.save_settings({'erechnung': 'no'}, 'Test')
        self.einfach(self.beitrag(), 'ausgeschaltet')

    def test_erinnerung_storno_vorschau_und_ohne_betrag(self):
        inv = self.beitrag()
        self.einfach({**inv, 'reminder': versand.reminder_terms(self.s.config(), 1)}, 'Zahlungserinnerung')
        self.einfach(versand.sample_invoice(self.s.config(), 'dues'), 'Vorschau')
        self.einfach({**inv, 'total': 0, 'lines': []}, 'ohne Betrag')
        self.s.reverse_invoice(inv['id'], 'Test')
        self.einfach(rechnung_pdf.invoice_data(self.s, inv['id']), 'storniert')

    def test_anschrift_ausserhalb_deutschlands_und_fremde_zeichen(self):
        self.s.save_settings({'tax_category': 'O'}, 'Test')
        self.einfach(self.rechnung(address='Ringstraße 1\n1010 Wien\nÖsterreich'), 'Anschrift des Empfängers')
        self.einfach(self.rechnung(name='株式会社サンプル'), 'Zeichen')

    def test_unerwarteter_fehler_liefert_die_bisherige_pdf(self):
        with mock.patch.object(zugferd, 'einbetten', side_effect=RuntimeError('kaputt')):
            self.einfach(self.beitrag(), 'unerwarteter Fehler')

    def test_sammel_pdf_bleibt_normale_pdf(self):
        self.assertNotIn(b'factur-x', rechnung_pdf.render([self.beitrag(), self.beitrag(number='M2')]))


class VersandTests(Fall):
    def setUp(self):
        super().setUp()
        self.smtp = FakeSMTP(user='kasse@example.org', password='geheim').start()
        self.s.save_settings({'mail_from': 'kasse@example.org', 'smtp_user': 'kasse@example.org', 'smtp_host': '127.0.0.1',
                              'smtp_port': str(self.smtp.port), 'smtp_security': 'none', 'mail_pause': '0', 'reminder_after': '1'}, 'Test')
        geheimnis.save('kasse@example.org', 'geheim')

    def tearDown(self):
        geheimnis.forget('kasse@example.org')
        self.smtp.stop()
        super().tearDown()

    def senden(self, ids, **kw):
        versand.start(self.db, ids, 'Test', **kw)
        for _ in range(200):
            if versand.status()['state'] != 'running':
                break
            time.sleep(0.05)
        job = versand.status()
        self.assertEqual((job['state'], job['failed']), ('done', 0))
        anhaenge = [next(email.message_from_bytes(m['data'], policy=policy.default).iter_attachments()) for m in self.smtp.messages]
        self.smtp.messages.clear()
        return job, anhaenge

    def test_versand_haengt_eine_datei_mit_den_rechnungsdaten_an(self):
        inv = self.beitrag()
        job, anhaenge = self.senden([inv['id']])
        self.assertEqual((job['sent'], job['einfach'], len(anhaenge)), (1, {}, 1))               # eine Datei, kein zweiter Anhang
        self.assertEqual((anhaenge[0].get_filename(), anhaenge[0].get_content_type()), (inv['number'] + '.pdf', 'application/pdf'))
        d = erechnung.read(anhaenge[0].get_content())
        self.assertEqual((d['number'], d['total'], d['creditor'], d['iban']), (inv['number'], 3000, 'JU Test', IBAN.replace(' ', '')))
        versand.send_test(self.s.config(), inv)                                                  # Testmail zeigt dasselbe wie der Versand
        self.assertIn(b'factur-x', next(email.message_from_bytes(self.smtp.messages[0]['data'], policy=policy.default).iter_attachments()).get_content())

    def test_versand_ohne_angaben_schickt_die_bisherige_pdf_und_nennt_den_grund(self):
        self.s.save_settings({'creditor_id': ''}, 'Test')
        inv = self.beitrag()
        job, anhaenge = self.senden([inv['id']])
        self.assertEqual((job['sent'], job['einfach']), (1, {'In den Einstellungen fehlt: Steuernummer oder Gläubiger-ID': 1}))
        datei = anhaenge[0].get_content()
        self.assertTrue(datei.startswith(b'%PDF') and b'factur-x' not in datei and b'/OutputIntents' not in datei)

    def test_zahlungserinnerung_bleibt_normale_pdf(self):
        inv = self.beitrag()
        self.senden([inv['id']])
        self.s.db.execute("UPDATE invoices SET due='2026-01-01' WHERE id=?", (inv['id'],))       # überfällig
        self.s.db.execute("UPDATE mailings SET time='2026-01-01T08:00:00'")
        self.s.db.commit()
        job, anhaenge = self.senden([inv['id']], kind='reminder')
        self.assertEqual((job['sent'], job['einfach'], len(anhaenge)), (1, {}, 1))
        self.assertNotIn(b'factur-x', anhaenge[0].get_content())


if __name__ == '__main__':
    unittest.main()
