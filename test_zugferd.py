"""Ausgangsrechnungen als E-Rechnung (ZUGFeRD/Factur-X, EN 16931) – nur erfundene Testdaten, Versand nur an den lokalen Testserver.

Ohne Prüfwerkzeug von außen: Rundlauf über erechnung.py, Aufbau der PDF/A-3 und die Reihenfolge der XML-Elemente, wie sie am
23.11.2026 mit Mustang 2.26.0 (veraPDF, Schema, Schematron) als gültig geprüft wurde – siehe werkzeuge/zugferd_beispiele.py.
"""
import contextlib
import email
import hashlib
import http.cookiejar
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email import policy
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent / 'tests'))
sys.path.insert(0, str(Path(__file__).parent / 'werkzeuge'))

import app
import berichte
import erechnung
import geheimnis
import lastschrift
import pdfschrift
import rechnung_pdf
import stammdaten
import versand
import zugferd
import zugferd_beispiele
import test_server   # Passwort und schnelles Hashing der Tests (wie test_http)
import zugang
from core import Store
from fake_smtp import FakeSMTP
from formate import euro, german_date, iban_pretty
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


def eingebettet(datei):
    """Die Rechnungsdaten, wie ein Empfänger sie aus der PDF liest."""
    namen = PdfReader(io.BytesIO(datei)).trailer['/Root']['/Names']['/EmbeddedFiles']['/Names']
    return ET.fromstring(namen[1].get_object()['/EF']['/F'].get_object().get_data())


def sichtbar(datei):
    """Der Text, den ein Mensch auf der PDF liest."""
    return ' '.join(' '.join((seite.extract_text() or '').split()) for seite in PdfReader(io.BytesIO(datei)).pages)


class Fall(Base):
    """Schalter an, Aussteller mit Gläubiger-ID: Beitragsrechnungen, die jetzt ausgestellt werden, sind E-Rechnungen –
    normale erst, wenn bei ihrer Ausstellung auch die Umsatzsteuer entschieden ist."""

    def setUp(self):
        super().setUp()
        self.s.save_settings({'creditor_id': GLAEUBIGER, 'erechnung': 'yes'}, 'Test')   # Vorgabe ist aus: Lennert schaltet ein, wenn die Umsatzsteuer-Einordnung bestätigt ist

    def laden(self, iid, **zusatz):
        return {**rechnung_pdf.invoice_data(self.s, iid), **zusatz}

    def aendern(self, iid, sql=None, werte=(), **abbild):
        """Altdaten direkt in der Datenbank verändern – am Programm vorbei (das Programm selbst ändert festgeschriebene Rechnungen nie)."""
        snap = json.loads(self.s.db.execute('SELECT snapshot FROM invoices WHERE id=?', (iid,)).fetchone()[0])
        for k, v in abbild.items():
            snap.pop(k, None) if v is None else snap.__setitem__(k, v)
        with self.s.db:
            self.s.db.execute('UPDATE invoices SET snapshot=? WHERE id=?', (json.dumps(snap), iid))
            if sql:
                self.s.db.execute(sql, (*werte, iid))
        return self.laden(iid)

    def gleich(self, inv):
        """Die eingebetteten Rechnungsdaten sagen dasselbe wie der sichtbare Text derselben PDF; liefert (Daten, Text)."""
        datei, grund = zugferd.pdf(inv)
        self.assertIsNone(grund)
        root, text = eingebettet(datei), sichtbar(datei)
        wert = lambda name: next(root.iter(RAM + name)).text
        tag = lambda el: german_date('-'.join((el[0].text[:4], el[0].text[4:6], el[0].text[6:])))
        cent = lambda name, in_=None: round(float((in_ if in_ is not None else root).find(f'.//{RAM}{name}').text) * 100)
        summe = root.find(f'.//{RAM}SpecifiedTradeSettlementHeaderMonetarySummation')
        kopf, faellig, offen = root.find('{' + zugferd.RSM + '}ExchangedDocument'), tag(next(root.iter(RAM + 'DueDateDateTime'))), cent('DuePayableAmount', summe)
        erwartet = [f"Rechnungsnummer {kopf.find(RAM + 'ID').text}", f"Rechnungsdatum {tag(kopf.find(RAM + 'IssueDateTime'))}",
                    f"Verwendungszweck: {wert('PaymentReference')}", f"Gesamtbetrag {euro(cent('GrandTotalAmount', summe))}"]
        kaeufer = root.find(f'.//{RAM}BuyerTradeParty')
        erwartet.append(f"{kaeufer.find(RAM + 'Name').text} {kaeufer.find(f'.//{RAM}LineOne').text}")
        erwartet.append(f"{kaeufer.find(f'.//{RAM}PostcodeCode').text} {kaeufer.find(f'.//{RAM}CityName').text} Rechnungsnummer")
        self.assertTrue(text.startswith(root.find(f'.//{RAM}SellerTradeParty/{RAM}Name').text))
        zahlungsweg = root.find(f'.//{RAM}SpecifiedTradeSettlementPaymentMeans/{RAM}TypeCode').text
        if zahlungsweg == '59':
            erwartet += [f'Einzug am {faellig}', f'Wir ziehen {euro(offen)} am {faellig} per SEPA-Lastschrift', f"Mandatsreferenz: {wert('DirectDebitMandateID')}",
                         f"Gläubiger-ID: {wert('CreditorReferenceID')}"]
            self.assertEqual(texte(root, 'IBANID'), [])
        else:
            self.assertEqual(zahlungsweg, '58')
            erwartet += [f'Zahlbar bis {faellig}', f"IBAN: {iban_pretty(wert('IBANID'))}", f"Empfänger: {wert('AccountName')}"]
            erwartet.append(f'Bitte überweise {euro(offen)} bis zum {faellig}' if offen > 0 else 'Diese Rechnung ist bezahlt.')
            self.assertEqual(texte(root, 'DirectDebitMandateID') + texte(root, 'CreditorReferenceID'), [])
            self.assertNotIn('SEPA-Lastschrift von deinem Konto', text)
        if summe.find(RAM + 'TotalPrepaidAmount') is not None:
            erwartet += [f"Bereits bezahlt: {euro(cent('TotalPrepaidAmount', summe))}", f'Offener Betrag: {euro(offen)}']
        else:
            self.assertNotIn('Bereits bezahlt', text)
        zeitraum = root.find(f'.//{RAM}BillingSpecifiedPeriod')
        if zeitraum is not None:
            start, ende = (zeitraum.find(f'{RAM}{n}')[0].text for n in ('StartDateTime', 'EndDateTime'))
            self.assertEqual((start[4:], ende), ('0101', start[:4] + '1231'))
            erwartet.append(f'Beitragsjahr {start[:4]}')
        else:
            self.assertNotIn('Beitragsjahr', text)
        for i, pos in enumerate(root.iter(RAM + 'IncludedSupplyChainTradeLineItem'), 1):
            erwartet.append(f"{i} {pos.find(f'.//{RAM}Name').text} {euro(cent('LineTotalAmount', pos))}")
            self.assertEqual(cent('ChargeAmount', pos), cent('LineTotalAmount', pos))
        for stelle in erwartet:
            self.assertIn(stelle, text)
        return root, text

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

    def test_ohne_steuerhinweis_steht_die_einordnung_als_begruendung(self):
        self.s.save_settings({'tax_category': 'O'}, 'Test')
        inv = self.aendern(self.rechnung()['id'], tax_note=' \n ')                             # Hinweis nur aus Leerraum: kein leeres Element
        self.assertEqual(texte(ET.fromstring(zugferd.xml(inv)), 'ExemptionReason'), ['Umsatzsteuer: nicht steuerbar'])

    def test_beitrag_bleibt_nicht_steuerbar_auch_wenn_normale_rechnungen_steuerbefreit_sind(self):
        self.s.save_settings({'tax_category': 'E', 'tax_number': '22/123/45678'}, 'Test')
        self.assertEqual(set(texte(ET.fromstring(zugferd.xml(self.beitrag())), 'CategoryCode')), {'O'})

    def test_ust_idnr_steht_nie_in_nicht_steuerbaren_rechnungen(self):
        self.s.save_settings({'vat_id': 'DE123456789', 'tax_number': '22/123/45678', 'tax_category': 'O'}, 'Test')
        ids = lambda inv: {e.get('schemeID'): e.text for e in ET.fromstring(zugferd.xml(inv)).iter(RAM + 'ID') if e.get('schemeID')}
        self.assertEqual(ids(self.rechnung()), {'FC': '22/123/45678'})                          # Regel BR-O-02
        self.s.save_settings({'tax_category': 'E'}, 'Test')
        self.assertEqual(ids(self.rechnung()), {'FC': '22/123/45678', 'VA': 'DE123456789'})

    def test_kennung_des_verkaeufers_ist_die_glaeubiger_id_sonst_die_steuernummer(self):
        kennung = lambda inv: ET.fromstring(zugferd.xml(inv)).find(f'.//{RAM}SellerTradeParty/{RAM}ID').text
        self.s.save_settings({'tax_number': '22/123/45678'}, 'Test')
        self.assertEqual(kennung(self.beitrag()), GLAEUBIGER)
        self.s.save_settings({'creditor_id': ''}, 'Test')
        self.assertEqual(kennung(self.beitrag(number='M2')), '22/123/45678')

    def test_anschrift(self):
        self.assertEqual(zugferd.anschrift('c/o Muster\nHinterhof\nAufgang B\nWeg 1\nD-20099  Hamburg\nDeutschland'),
                         {'zeilen': ['c/o Muster', 'Hinterhof', 'Aufgang B, Weg 1'], 'plz': '20099', 'ort': 'Hamburg'})
        for text in ('', 'Weg 1', 'Ringstraße 1\n1010 Wien\nÖsterreich', 'Weg 1\nHamburg', '1 Rue de Rivoli\n75001 Paris\nFrankreich'):
            self.assertIsNone(zugferd.anschrift(text), text)
        # bekannte Grenze (REGELN Nr. 38): ohne Länderzeile gilt eine fünfstellige Postleitzahl als deutsch – deshalb das Land immer in die letzte Zeile
        self.assertEqual(zugferd.anschrift('1 Rue de Rivoli\n75001 Paris')['ort'], 'Paris')

    def test_einstellungen_werden_geprueft(self):
        for bad in ({'tax_number': '123'}, {'tax_number': 'DE123456789'}, {'vat_id': 'DE12345'}, {'vat_id': 'ATU12345678'}, {'tax_category': 'S'}):
            with self.assertRaises(ValueError, msg=bad):
                self.s.save_settings(bad, 'Test')
        self.s.save_settings({'tax_number': ' 22/123/45678 ', 'vat_id': 'de 123456789', 'tax_category': 'E', 'erechnung': 'no'}, 'Test')
        cfg = self.s.config()
        self.assertEqual([cfg[k] for k in ('tax_number', 'vat_id', 'tax_category', 'erechnung')], ['22/123/45678', 'DE123456789', 'E', 'no'])
        self.assertEqual(stammdaten.DEFAULTS['erechnung'], 'no')   # erst nach bewusstem Einschalten gehen E-Rechnungen hinaus

    def test_stand_nennt_was_kuenftigen_rechnungen_fehlt(self):
        self.assertEqual(self.s.snapshot(2026)['erechnung'], {'an': True, 'dues': [], 'normal': ['Umsatzsteuer normaler Rechnungen (nicht steuerbar oder steuerbefreit)']})
        self.s.save_settings({'creditor_id': '', 'tax_category': 'E', 'issuer_address': 'Postfach 1'}, 'Test')
        offen = ['Anschrift des Ausstellers mit Postleitzahl und Ort in der letzten Zeile']
        self.assertEqual(zugferd.stand(self.s.config()), {'an': True, 'dues': ['Steuernummer oder Gläubiger-ID'] + offen, 'normal': ['Steuernummer oder USt-IdNr.'] + offen})

    def test_steuerzeichen_stehen_nie_in_den_rechnungsdaten(self):
        inv = self.beitrag()
        inv['recipient'], inv['lines'][0]['description'] = 'Erika\x01 Muster\x7ffrau￾', 'Beitrag\x00\x08 2026\x1b'   # an der Prüfung `fehlt` vorbei
        root = ET.fromstring(zugferd.xml(inv))                                                  # sonst wäre die Datei nicht einmal lesbar
        self.assertEqual((root.find(f'.//{RAM}BuyerTradeParty/{RAM}Name').text, root.find(f'.//{RAM}SpecifiedTradeProduct/{RAM}Name').text),
                         ('Erika Muster\x7ffrau', 'Beitrag 2026'))

    def test_sonderzeichen_in_titel_und_aussteller_brechen_die_metadaten_nicht(self):
        self.s.save_settings({'issuer': 'M&M <Söhne> "JU"'}, 'Test')
        datei, grund = zugferd.pdf(self.beitrag())
        self.assertIsNone(grund)
        xmp = ET.fromstring(PdfReader(io.BytesIO(datei)).trailer['/Root']['/Metadata'].get_object().get_data())
        self.assertEqual(xmp.find('.//{http://purl.org/dc/elements/1.1/}creator//{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li').text, 'M&M <Söhne> "JU"')
        self.assertEqual(eingebettet(datei).find(f'.//{RAM}SellerTradeParty/{RAM}Name').text, 'M&M <Söhne> "JU"')


class GleichTests(Fall):
    """Die Rechnungsdaten sagen dasselbe wie die PDF: je Fall die Werte der XML im sichtbaren Text derselben Datei (`gleich`)."""

    def lastschrift(self):
        mid = self.member()
        lastschrift.create_mandate(self.s, mid, {'iban': IBAN, 'holder': 'Erika Musterfrau', 'signed_on': '2026-01-10'}, 'Test')
        return self.laden(self.s.issue_dues(2027, [mid], 'Test')[0])

    def test_beitrag_per_ueberweisung(self):
        inv = self.beitrag()
        inv = self.aendern(inv['id'], 'UPDATE invoices SET issued=?,due=? WHERE id=?', ('2026-03-05', '2026-04-07'))   # Rechnungsdatum ist nicht „heute“
        root, text = self.gleich(inv)
        self.assertIn('Rechnungsdatum 05.03.2026', text)
        self.assertIn('Zahlbar bis 07.04.2026', text)
        self.assertIn('Beitragsjahr 2026', text)
        self.assertEqual(texte(root, 'PaymentReference'), [inv['number']])

    def test_beitrag_per_lastschrift(self):
        inv = self.lastschrift()
        root, text = self.gleich(inv)
        debit = inv['snap']['debit']
        self.assertEqual((texte(root, 'DirectDebitMandateID'), texte(root, 'CreditorReferenceID')), ([debit['mandate']], [GLAEUBIGER]))
        self.assertNotEqual(debit['mandate'], GLAEUBIGER)
        self.assertIn('Einzug am ' + german_date(debit['date']), text)
        self.assertIn('Beitragsjahr 2027', text)

    def test_entfallene_lastschrift_wird_ueberweisung_mit_neuer_frist(self):
        inv = self.lastschrift()
        self.aendern(inv['id'], 'UPDATE invoices SET due=? WHERE id=?', ('2026-01-15',))        # alte Fälligkeit, damit die neue Frist erkennbar ist
        lastschrift.revoke_mandate(self.s, self.s.db.execute('SELECT id FROM mandates').fetchone()[0], 'Widerruf durch das Mitglied', 'Test')
        inv = self.laden(inv['id'])
        frist = (date.today() + timedelta(days=lastschrift.REPAY_DAYS)).isoformat()
        self.assertEqual((inv['debit_active'], inv['due'], lastschrift.payable_by(inv)), (False, '2026-01-15', frist))
        root, text = self.gleich(inv)                                                            # Überweisung mit IBAN, keine Lastschrift mehr
        self.assertIn('Zahlbar bis ' + german_date(frist), text)
        self.assertEqual(next(root.iter(RAM + 'DueDateDateTime'))[0].text, frist.replace('-', ''))

    def test_teilzahlung_und_bezahlt(self):
        self.s.save_settings({'tax_category': 'E', 'tax_number': '22/123/45678'}, 'Test')
        zwei = [dict(description='Teilnahme Sommerfest', amount='25', account='8504'), dict(description='Getränke', amount='7,50', account='8504')]
        iid = self.s.issue_series(self.series(recipients=[{'name': 'Muster GmbH', 'address': 'Lange Reihe 5\n20099 Hamburg', 'email': ''}], lines=zwei), 'Test')[0]
        root, text = self.gleich(self.laden(iid))                                                # zwei Positionen mit eigenem Betrag
        self.assertEqual(texte(root, 'LineTotalAmount'), ['25.00', '7.50', '32.50'])
        root, text = self.gleich(self.laden(iid, paid=1000))
        self.assertIn('Bitte überweise 22,50 €', text)
        root, text = self.gleich(self.laden(iid, paid=3250))
        self.assertEqual(texte(root, 'DuePayableAmount'), ['0.00'])
        self.assertNotIn('Bitte überweise', text)


class FestschreibungTests(Fall):
    """Eine Rechnungsnummer hat einen Inhalt: Die Rechnungsdaten kommen nur aus dem Abbild der Rechnung (REGELN Nr. 38 g)."""

    ALLES_ANDERS = {'tax_category': 'O', 'tax_number': '99/999/99999', 'vat_id': 'DE123456789', 'creditor_id': '', 'tax_note': 'Hinweis später',
                    'issuer': 'JU Anders', 'issuer_address': 'Anderer Weg 9\n20001 Hamburg', 'bic': 'COBADEFFXXX'}

    def test_spaetere_einstellungen_aendern_die_rechnungsdaten_nicht(self):
        self.s.save_settings({'tax_category': 'E', 'tax_number': '11/111/11111'}, 'Test')
        beitrag, rechnung = self.beitrag()['id'], self.rechnung()['id']
        vorher = [zugferd.xml(self.laden(i)) for i in (beitrag, rechnung)]
        self.assertIn(b'11/111/11111', vorher[1])
        self.s.save_settings(self.ALLES_ANDERS, 'Test')
        self.assertEqual([zugferd.xml(self.laden(i)) for i in (beitrag, rechnung)], vorher)
        self.assertEqual([zugferd.fehlt(self.laden(i)) for i in (beitrag, rechnung)], [None, None])

    def test_leer_festgeschriebene_angabe_wird_nicht_nachgezogen(self):
        iid = self.beitrag()['id']                                                              # nur mit Gläubiger-ID ausgestellt
        vorher = zugferd.xml(self.laden(iid))
        self.s.save_settings({'tax_number': '22/123/45678', 'vat_id': 'DE123456789', 'creditor_id': ''}, 'Test')
        self.assertEqual(zugferd.xml(self.laden(iid)), vorher)                                   # weiter Gläubiger-ID als Kennung, keine Steuernummer
        self.assertNotIn(b'SpecifiedTaxRegistration', vorher)

    def test_fehlte_bei_der_ausstellung_etwas_bleibt_die_rechnung_fuer_immer_normale_pdf(self):
        self.s.save_settings({'tax_number': '11/111/11111'}, 'Test')
        iid = self.rechnung()['id']                                                             # Umsatzsteuer war noch nicht entschieden
        grund = 'Bei der Ausstellung fehlte: Umsatzsteuer normaler Rechnungen (nicht steuerbar oder steuerbefreit)'
        for spaeter in ({}, {'tax_category': 'O'}, {'tax_category': 'E', 'vat_id': 'DE123456789'}, {'creditor_id': GLAEUBIGER}, {'tax_number': '99/999/99999'}, {'tax_category': ''}):
            self.s.save_settings(spaeter, 'Test')
            self.assertEqual(zugferd.fehlt(self.laden(iid)), grund, spaeter)

    def test_rechnungen_von_vor_der_einfuehrung_bleiben_normale_pdf(self):
        self.s.save_settings({'tax_category': 'O', 'tax_number': '11/111/11111'}, 'Test')
        alt = {k: None for k in stammdaten.ERECHNUNG}                                           # Abbild wie vor 0.58: die vier Angaben gibt es nicht
        ids = [self.aendern(i, **alt)['id'] for i in (self.beitrag()['id'], self.rechnung()['id'])]
        for spaeter in ({}, {'tax_category': 'E'}, {'creditor_id': '', 'tax_number': '99/999/99999'}):
            self.s.save_settings(spaeter, 'Test')
            self.assertEqual([zugferd.fehlt(self.laden(i)) for i in ids], ['vor Einführung der E-Rechnung ausgestellt'] * 2, spaeter)
        datei, grund = zugferd.pdf(self.laden(ids[0]))
        self.assertEqual((grund, b'factur-x' in datei), ('vor Einführung der E-Rechnung ausgestellt', False))

    def test_schalter_entscheidet_nur_ob_nie_was(self):
        iid = self.beitrag()['id']
        an = zugferd.xml(self.laden(iid))
        self.s.save_settings({'erechnung': 'no'}, 'Test')
        self.assertEqual(zugferd.fehlt(self.laden(iid)), 'E-Rechnung ausgeschaltet')
        bei_aus = self.beitrag(number='M2')['id']                                               # bei ausgeschaltetem Schalter ausgestellt, Angaben vollständig
        self.s.save_settings({'erechnung': 'yes'}, 'Test')
        self.assertEqual(zugferd.xml(self.laden(iid)), an)
        self.assertIsNone(zugferd.fehlt(self.laden(bei_aus)))

    def test_abbild_enthaelt_alle_angaben_auch_leere(self):
        snap = self.beitrag()['snap']
        self.assertEqual({k: snap[k] for k in stammdaten.ERECHNUNG}, {'tax_number': '', 'vat_id': '', 'tax_category': '', 'creditor_id': GLAEUBIGER})


class ZeichenTests(Fall):
    """Leerraum ohne Glyphe und fremde Zeichen: neue Rechnungen werden beim Festschreiben bereinigt, sonst bleibt es die normale PDF."""
    LEERRAUM = '\t　   \x0b\x0c\x1c\x1d\x1e\x1f\x85'

    def setUp(self):
        super().setUp()
        self.s.save_settings({'tax_category': 'O'}, 'Test')

    def test_leerraum_wird_beim_festschreiben_bereinigt(self):
        for z in self.LEERRAUM:
            with self.subTest(zeichen=hex(ord(z))):
                data = self.series(recipients=[{'name': f' Erika{z}Muster ', 'address': f'Lange{z}{z}Reihe 5\n\n20099\tHamburg ', 'email': ''}],
                                   lines=[dict(description=f'Teilnahme{z}Fest', amount='5', account='8504')])
                inv = self.laden(self.s.issue_series(data, 'Test')[0])
                gespeichert = [inv['recipient'], *inv['lines'][0]['description'].split('\n'), *inv['address'].split('\n')]   # je Zeile, wie die PDF sie setzt
                self.assertEqual(gespeichert[0], 'Erika Muster')
                self.assertEqual(inv['lines'][0]['description'].split(), ['Teilnahme', 'Fest'])
                self.assertTrue(all(pdfschrift.covers(t) and t == t.strip() and t for t in gespeichert), gespeichert)
                self.assertIn(zugferd.fehlt(inv), (None, 'Anschrift des Empfängers endet nicht mit Postleitzahl und Ort (nur Deutschland)'))
        root, text = self.gleich(inv)                                                            # die letzte ist eine gültige E-Rechnung
        self.assertIn('Erika Muster Lange Reihe 5 20099 Hamburg', text)

    def test_tabulator_im_namen_ergibt_eine_e_rechnung_ohne_kaestchen(self):
        inv = self.rechnung(name='Erika\tMuster')
        self.assertEqual((inv['recipient'], zugferd.fehlt(inv)), ('Erika Muster', None))

    def test_vorschau_zeigt_was_festgeschrieben_wird(self):
        pv = self.s.series_preview(self.series(recipients=[{'name': 'Erika\tMuster', 'address': 'Weg　1\n20099 Hamburg', 'email': ''}],
                                               lines=[dict(description='Teil\tnahme', amount='5', account='8504')]))
        self.assertEqual((pv['recipients'][0]['name'], pv['recipients'][0]['address'], pv['lines'][0]['description']), ('Erika Muster', 'Weg 1\n20099 Hamburg', 'Teil nahme'))

    def test_bestehende_rechnung_mit_leerraum_oder_fremdem_zeichen_bleibt_normale_pdf(self):
        iid = self.rechnung()['id']
        for feld, wert in (('recipient', 'Erika\tMuster'), ('recipient', 'Erika　Muster'), ('recipient', 'Erika￿Muster'), ('address', 'Lange\tReihe 5\n20099 Hamburg'),
                           ('recipient', '株式会社サンプル'), ('recipient', 'Erika\U0001f600')):
            inv = self.aendern(iid, f'UPDATE invoices SET {feld}=? WHERE id=?', (wert,))
            self.assertEqual((inv[feld], zugferd.fehlt(inv)), (wert, 'Die Rechnung enthält Zeichen, die die PDF-Schrift nicht kennt'), repr(wert))
        self.assertTrue(self.s.db.execute('UPDATE lines SET description=? WHERE invoice_id=?', ('Teil\x0bnahme', iid)).rowcount)
        self.assertEqual(zugferd.fehlt(self.aendern(iid, 'UPDATE invoices SET recipient=? WHERE id=?', ('Muster GmbH',))), 'Die Rechnung enthält Zeichen, die die PDF-Schrift nicht kennt')

    def test_fremdes_zeichen_beim_festschreiben_bleibt_normale_pdf(self):
        for name in ('株式会社サンプル', 'Erika￿Muster'):
            datei, grund = zugferd.pdf(self.rechnung(name=name))
            self.assertEqual((grund, b'factur-x' in datei), ('Die Rechnung enthält Zeichen, die die PDF-Schrift nicht kennt', False))


class RueckfallTests(Fall):
    """Geht etwas nicht, kommt genau die bisherige PDF heraus – mit Grund."""

    def einfach(self, inv, grund):
        datei, warum = zugferd.pdf(inv)
        self.assertIn(grund, warum)
        self.assertEqual(datei[:5], b'%PDF-')
        self.assertNotIn(b'factur-x', datei)
        self.assertNotIn(b'/OutputIntents', datei)
        with self.assertRaises(ValueError):
            erechnung.read(datei)

    def test_ohne_kennung_des_ausstellers(self):
        self.s.save_settings({'creditor_id': ''}, 'Test')
        self.einfach(self.beitrag(), 'Bei der Ausstellung fehlte: Steuernummer oder Gläubiger-ID')

    def test_normale_rechnung_ohne_entscheidung_zur_umsatzsteuer(self):
        self.einfach(self.rechnung(), 'Bei der Ausstellung fehlte: Umsatzsteuer normaler Rechnungen')
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

    def test_anschrift_ausserhalb_deutschlands(self):
        self.s.save_settings({'tax_category': 'O'}, 'Test')
        self.einfach(self.rechnung(address='Ringstraße 1\n1010 Wien\nÖsterreich'), 'Anschrift des Empfängers')
        self.einfach(self.rechnung(address='1 Rue de Rivoli\n75001 Paris\nFrankreich'), 'Anschrift des Empfängers')

    def test_pilotrechnung_und_ungueltige_iban(self):
        iid = self.beitrag()['id']
        self.einfach(self.aendern(iid, pilot=True), 'Pilotrechnung aus einer Vorversion')
        self.einfach(self.aendern(iid, pilot=False, iban='DE00 1234'), 'IBAN des Ausstellers ist ungültig')

    def test_direkt_veraenderte_altdaten_bleiben_normale_pdf(self):
        iid = self.beitrag()['id']
        unvollstaendig = 'Aussteller, Empfänger, Datum, Fälligkeit oder Positionstext fehlt'
        self.einfach(self.aendern(iid, issuer=' '), unvollstaendig)
        self.einfach(self.aendern(iid, "UPDATE invoices SET due='' WHERE id=?", issuer='JU Test'), unvollstaendig)
        self.s.db.execute("UPDATE lines SET description=' ' WHERE invoice_id=?", (iid,))
        self.einfach(self.aendern(iid, "UPDATE invoices SET due=issued WHERE id=?"), unvollstaendig)
        self.s.db.execute("UPDATE lines SET description='Mitgliedsbeitrag 2026' WHERE invoice_id=?", (iid,))
        self.assertIsNone(zugferd.fehlt(self.laden(iid)))                                        # wieder vollständig – die Gegenprobe
        self.einfach(self.aendern(iid, 'UPDATE invoices SET total=total+1 WHERE id=?'), 'Summe der Positionen weicht vom Rechnungsbetrag ab')

    def test_unerwarteter_fehler_liefert_die_bisherige_pdf(self):
        inv = self.beitrag()
        for stelle in ('einbetten', 'xml', 'fehlt'):                                             # auch die Prüfung selbst und die Rechnungsdaten
            with mock.patch.object(zugferd, stelle, side_effect=RuntimeError('kaputt')):
                self.einfach(inv, 'unerwarteter Fehler')

    def test_ursache_steht_mit_art_und_programmstelle_im_serverprotokoll(self):
        self.assertIs(zugferd.melden, app.log_error)                                             # der vorhandene Weg ins Protokoll
        aus = io.StringIO()
        with mock.patch.object(zugferd, 'xml', side_effect=RuntimeError('Erika Musterfrau')), contextlib.redirect_stdout(aus):
            self.einfach(self.beitrag(), 'unerwarteter Fehler')
        self.assertRegex(aus.getvalue(), r'Interner Fehler bei E-Rechnung erzeugen: RuntimeError \(.*zugferd\.py:\d+')
        self.assertNotIn('Musterfrau', aus.getvalue())                                           # Art und Stelle, keine Inhalte

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

    def protokoll(self):
        """Je Nachricht: (Rechnungsnummer, Art, festgehaltene E-Rechnung) – wie in mailings, in der Liste und im Prüferpaket."""
        return [(r['number'], r['kind'], r['erechnung']) for r in self.s.rows('SELECT i.number,m.kind,m.erechnung FROM mailings m JOIN invoices i ON i.id=m.invoice_id ORDER BY m.id')]

    def test_versand_haengt_eine_datei_mit_den_rechnungsdaten_an(self):
        inv = self.beitrag()
        job, anhaenge = self.senden([inv['id']])
        self.assertEqual((job['sent'], job['einfach'], len(anhaenge)), (1, {}, 1))               # eine Datei, kein zweiter Anhang
        self.assertEqual((anhaenge[0].get_filename(), anhaenge[0].get_content_type()), (inv['number'] + '.pdf', 'application/pdf'))
        d = erechnung.read(anhaenge[0].get_content())
        self.assertEqual((d['number'], d['total'], d['creditor'], d['iban']), (inv['number'], 3000, 'JU Test', IBAN.replace(' ', '')))
        self.assertEqual(self.protokoll(), [(inv['number'], 'invoice', '')])                     # festgehalten: ging als E-Rechnung hinaus
        self.assertEqual(self.s.invoices()[0]['sent_erechnung'], '')
        versand.send_test(self.s.config(), inv)                                                  # Testmail zeigt dasselbe wie der Versand
        self.assertIn(b'factur-x', next(email.message_from_bytes(self.smtp.messages[0]['data'], policy=policy.default).iter_attachments()).get_content())

    def test_versand_ohne_angaben_schickt_die_bisherige_pdf_und_haelt_den_grund_fest(self):
        self.s.save_settings({'creditor_id': ''}, 'Test')
        inv = self.beitrag()
        grund = 'Bei der Ausstellung fehlte: Steuernummer oder Gläubiger-ID'
        job, anhaenge = self.senden([inv['id']])
        self.assertEqual((job['sent'], job['einfach']), (1, {grund: 1}))
        datei = anhaenge[0].get_content()
        self.assertTrue(datei.startswith(b'%PDF') and b'factur-x' not in datei and b'/OutputIntents' not in datei)
        self.assertEqual(self.protokoll(), [(inv['number'], 'invoice', grund)])                  # je Rechnung, nicht nur als Summe im Arbeitsspeicher
        self.assertEqual(self.s.invoices()[0]['sent_erechnung'], grund)
        jahr = date.today().year                                                                # Kassenprüfer-Paket: E-Mail-Protokoll mit Spalte „E-Rechnung“
        zeilen = berichte.audit_files(self.s, jahr, Path(self.tmp.name))[0][f'E-Mail-Protokoll-{jahr}.csv'].decode('utf-8-sig').splitlines()
        self.assertTrue(zeilen[0].endswith(';E-Rechnung') and zeilen[1].endswith(';nein: ' + grund), zeilen)

    def test_ausgeschaltet_wird_festgehalten_aber_nicht_als_ungewollt_gemeldet(self):
        self.s.save_settings({'erechnung': 'no'}, 'Test')
        inv = self.beitrag()
        job, _ = self.senden([inv['id']])
        self.assertEqual((job['einfach'], self.protokoll()), ({}, [(inv['number'], 'invoice', 'E-Rechnung ausgeschaltet')]))

    def test_zahlungserinnerung_bleibt_normale_pdf(self):
        inv = self.beitrag()
        self.senden([inv['id']])
        self.s.db.execute("UPDATE invoices SET due='2026-01-01' WHERE id=?", (inv['id'],))       # überfällig
        self.s.db.execute("UPDATE mailings SET time='2026-01-01T08:00:00'")
        self.s.db.commit()
        job, anhaenge = self.senden([inv['id']], kind='reminder')
        self.assertEqual((job['sent'], job['einfach'], len(anhaenge)), (1, {}, 1))
        self.assertNotIn(b'factur-x', anhaenge[0].get_content())
        self.assertEqual(self.protokoll(), [(inv['number'], 'invoice', ''), (inv['number'], 'reminder', None)])
        self.assertEqual(self.s.invoices()[0]['sent_erechnung'], '')                             # die Erinnerung ändert nicht, wie die Rechnung hinausging

    def test_aeltere_datenbank_bekommt_die_spalte_dazu(self):
        self.s.db.execute('ALTER TABLE mailings DROP COLUMN erechnung')
        self.s.db.execute("INSERT INTO mailings(invoice_id,time,recipient,subject,status) VALUES(?,?,?,?,'sent')", (self.beitrag()['id'], '2026-01-01T08:00:00', 'a@example.org', 'Rechnung'))
        self.s.db.commit()
        neu = Store(self.db)
        self.addCleanup(neu.db.close)
        self.assertIsNone(neu.invoices()[0]['sent_erechnung'])                                   # nicht erfasst – weder „als E-Rechnung“ noch ein Grund


class HttpTests(unittest.TestCase):
    """Über den Server: Download einer Rechnung, E-Mail-Vorschau, Sammel-PDF."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DATA = Path(cls.tmp.name)
        app.DB = app.DATA / 'finanzen.sqlite3'
        (app.DATA / 'Importe').mkdir()
        app.BACKUPS = app.DATA / 'Sicherungen-Test'
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'Kasse Test', test_server.PASSWORT, create=True)
        finally:
            store.db.close()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        cls.csrf = json.load(cls.request('/api/login', {'name': 'Kasse Test', 'password': test_server.PASSWORT}))['csrf']
        cls.request('/api/bestaetigen', {'password': test_server.PASSWORT})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    @classmethod
    def request(cls, path, data=None):
        kopf = {'Origin': cls.url, **({'Content-Type': 'application/json', 'X-CSRF-Token': getattr(cls, 'csrf', '')} if data is not None else {})}
        return cls.client.open(urllib.request.Request(cls.url + path, json.dumps(data).encode() if data is not None else None, kopf))

    def test_download_vorschau_und_sammel_pdf(self):
        request = self.request
        request('/api/settings', {'issuer': 'JU Test', 'issuer_address': 'Teststraße 1\n20000 Hamburg', 'iban': IBAN, 'age_basis': 'jan1', 'creditor_id': GLAEUBIGER})
        m = json.load(request('/api/member', {'number': 'T1', 'name': 'Erika Musterfrau', 'address': 'Musterweg 1\n20000 Hamburg', 'birth': '2000-02-01',
                                              'joined': '2024-01-01', 'kv': '5100', 'email': 'erika@example.org'}))
        iid = json.load(request('/api/dues', {'year': 2026, 'ids': [m['id']]}))['ids'][0]
        self.assertEqual(json.load(request('/api/data?year=2026'))['erechnung'], {'an': False, 'dues': [], 'normal': ['Umsatzsteuer normaler Rechnungen (nicht steuerbar oder steuerbefreit)']})
        laden = lambda: request(f'/api/invoice.pdf?id={iid}').read()
        vorschau = lambda: request(f'/api/mail-preview?id={iid}').read().decode()
        self.assertNotIn(b'factur-x', laden())                                                   # Vorgabe: Schalter aus
        self.assertNotIn('E-Rechnung', vorschau())
        request('/api/settings', {'erechnung': 'yes'})
        antwort = request(f'/api/invoice.pdf?id={iid}')
        datei = antwort.read()
        self.assertEqual((antwort.headers['Content-Type'], antwort.headers['Content-Disposition']), ('application/pdf', 'inline; filename="B-2026-00001.pdf"'))
        d = erechnung.read(datei)                                                                # dieselbe Rechnung, jetzt mit Rechnungsdaten
        self.assertEqual((d['number'], d['total'], d['paid']), ('B-2026-00001', 3000, 0))
        self.assertIn('B-2026-00001.pdf – E-Rechnung (ZUGFeRD)', vorschau())
        self.assertIn(b'factur-x', request(f'/api/preview.pdf?id={iid}').read())
        self.assertNotIn(b'factur-x', request(f'/api/invoices.pdf?ids={iid}').read())           # Sammel-PDF zum Drucken
        self.assertNotIn(b'factur-x', request(f"/api/preview.pdf?dues={m['id']}&year=2027").read())   # Entwurf

        store = Store(app.DB)                                                                    # Teilzahlung: Download wie Versand und Vorschau
        try:
            heute = date.today().isoformat()
            store.import_bank(store.preview_bank(f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{heute};10;Erika Musterfrau;B-2026-00001;z1\n'.encode(), 'z1.csv'), [2], 'Test')
            store.book(store.db.execute('SELECT MAX(id) FROM bank').fetchone()[0], [{'invoice_id': iid, 'amount': '10'}], 'Test')
        finally:
            store.db.close()
        datei = laden()
        d = erechnung.read(datei)
        self.assertEqual((d['gross'], d['paid'], d['total']), (3000, 1000, 2000))
        self.assertIn('Bitte überweise 20,00 €', sichtbar(datei))
        self.assertIn('Bereits bezahlt: 10,00 €', sichtbar(datei))
        self.assertEqual(sichtbar(request(f'/api/preview.pdf?id={iid}').read()), sichtbar(datei))

        store = Store(app.DB)                                                                    # Rechnung von vor 0.58: bleibt normale PDF, die Vorschau sagt warum
        try:
            snap = {k: v for k, v in json.loads(store.db.execute('SELECT snapshot FROM invoices WHERE id=?', (iid,)).fetchone()[0]).items() if k not in stammdaten.ERECHNUNG}
            with store.db:
                store.db.execute('UPDATE invoices SET snapshot=? WHERE id=?', (json.dumps(snap), iid))
        finally:
            store.db.close()
        self.assertNotIn(b'factur-x', laden())
        self.assertIn('B-2026-00001.pdf – normale PDF ohne E-Rechnung: vor Einführung der E-Rechnung ausgestellt', vorschau())


if __name__ == '__main__':
    unittest.main()
