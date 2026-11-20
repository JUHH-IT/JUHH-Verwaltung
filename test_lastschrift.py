"""SEPA-Lastschrift für Mitgliedsbeiträge – nur erfundene Testdaten (Beispiel-Gläubiger-ID der Bundesbank, Beispiel-IBANs)."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import lastschrift as l
import rechnung_pdf
import versand
from core import Store

CREDITOR = 'DE98ZZZ09999999999'                      # Beispiel-Gläubiger-ID der Bundesbank
OWN_IBAN = 'DE89 3704 0044 0532 0130 00'
MEMBER_IBAN = 'DE02120300000000202051'
XSD = Path(__file__).parent / 'tests' / 'schemas' / 'pain.008.001.08.xsd'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'Junge Union Hamburg (Test)', 'issuer_address': 'Teststraße 1\n20000 Hamburg', 'iban': OWN_IBAN,
                              'bic': 'COBADEFFXXX', 'age_basis': 'jan1', 'creditor_id': CREDITOR, 'mail_from': 'kasse@example.org'}, 'Test')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, number='M1', name='Erika Müller', email='erika@example.org'):
        return self.s.member(dict(number=number, name=name, address='Musterweg 1\n20000 Hamburg', email=email, birth='2000-02-01',
                                  joined='2024-01-01', kv='5100'), 'Test')

    def mandate(self, mid, **extra):
        data = {'iban': MEMBER_IBAN, 'signed_on': '2025-03-01', **extra}
        return l.create_mandate(self.s, mid, data, 'Test')

    def dues(self, mid, year=2027, notify=True):
        iid = self.s.issue_dues(year, [mid], 'Test')[0]
        if notify:
            self.mailed(iid)
        return iid

    def mailed(self, iid, day=None):
        """Rechnungs-E-Mail als versendet protokollieren (Vorabankündigung zugestellt)."""
        with self.s.db:
            self.s.db.execute("INSERT INTO mailings(invoice_id,time,recipient,subject,status,kind) VALUES(?,?,?,?,'sent','invoice')",
                              (iid, (day or date.today().isoformat()) + 'T10:00:00', 'erika@example.org', 'Beitrag'))

    def bank(self, amount, ref, key, day=None):
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day or date.today().isoformat()};{amount};Erika Mueller;{ref};{key}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, key + '.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def number(self, iid):
        return self.s.db.execute('SELECT number FROM invoices WHERE id=?', (iid,)).fetchone()[0]

    def inv(self, iid):
        return dict(self.s.db.execute('SELECT * FROM invoices WHERE id=?', (iid,)).fetchone())

    def submit(self, batch):
        """Herunterladen, hochladen (gedacht) und als eingereicht markieren."""
        l.download(self.s, batch, 'Test')
        l.set_batch(self.s, batch, 'submitted', 'Test')


class RuleTests(Base):
    def test_creditor_id_and_settings(self):
        self.assertTrue(l.creditor_id_valid(CREDITOR))
        self.assertFalse(l.creditor_id_valid('DE99ZZZ09999999999'))
        with self.assertRaises(ValueError):
            self.s.save_settings({'creditor_id': 'DE99ZZZ09999999999'}, 'Test')
        for bad in ({'sepa_prenotice': '0'}, {'sepa_prenotice': '31'}, {'sepa_prefix': 'JU HH'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.s.save_settings(bad, 'Test')

    def test_business_days(self):
        self.assertEqual(l.business_day('2027-01-01'), '2027-01-04')                  # Neujahr, Wochenende
        self.assertEqual(l.business_day('2027-03-26'), '2027-03-30')                  # Karfreitag bis Ostermontag
        self.assertEqual(l.business_day('2026-12-25'), '2026-12-28')
        self.assertEqual(l.business_day('2026-05-01'), '2026-05-04')

    def test_sepa_text(self):
        self.assertEqual(l.sepa_text('Şahin Yıldız-Łukaszewicz & Müller <x>', 70), 'Sahin Yildiz-Lukaszewicz + Mueller x')
        self.assertEqual(len(l.sepa_text('A' * 200, 70)), 70)


class MandateTests(Base):
    def test_create_reference_and_rules(self):
        mid = self.member()
        ref = self.mandate(mid)
        self.assertEqual(ref, 'JUHH-M1-1')
        with self.assertRaises(ValueError):
            self.mandate(mid)                                                          # nur ein aktives Mandat
        mandate = l.active_mandate(self.s, mid)
        l.revoke_mandate(self.s, mandate['id'], 'Kontowechsel', 'Test')
        self.assertEqual(self.mandate(mid, iban='DE89370400440532013000'), 'JUHH-M1-2')  # neues Mandat für neues Konto
        other = self.member('M2', 'Max Muster')
        for bad in ({'iban': 'DE00123'}, {'iban': 'CH9300762011623852957'}, {'signed_on': (date.today() + timedelta(days=1)).isoformat()},
                    {'reference': 'MIT LEERZEICHEN'}, {'reference': 'JUHH-M1-1'}, {'reference': '/ALT-1'}, {'reference': 'ALT-1/'},
                    {'reference': 'ALT//1'}, {'holder': '\u00a0 '}, {'holder': 'Δημήτρης Παπαδόπουλος'},
                    {'used_before': '2025-01-01'}):                                        # genutzt vor der Unterschrift
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.mandate(other, **bad)
        self.assertEqual(self.mandate(other, reference='ALT-2019/07'), 'ALT-2019/07')  # vorhandenes Papiermandat übernehmen

    def test_requires_creditor_id(self):
        self.s.save_settings({'creditor_id': ''}, 'Test')
        with self.assertRaises(ValueError):
            self.mandate(self.member())

    def test_mandate_pdf(self):
        mid = self.member()
        self.mandate(mid)
        self.assertTrue(l.mandate_pdf(self.s, mid).startswith(b'%PDF'))
        self.assertTrue(l.mandate_pdf(self.s).startswith(b'%PDF'))                      # leeres Formular


class DebitTests(Base):
    def test_invoice_carries_prenotification(self):
        with_mandate, without = self.member(), self.member('M2', 'Max Muster', 'max@example.org')
        self.mandate(with_mandate)
        a, b = self.dues(with_mandate), self.dues(without)
        snap = json.loads(self.s.db.execute('SELECT snapshot FROM invoices WHERE id=?', (a,)).fetchone()[0])
        debit = snap['debit']
        self.assertEqual((debit['mandate'], debit['creditor_id'], debit['iban']), ('JUHH-M1-1', CREDITOR, 'DE02 •••• 2051'))
        self.assertGreaterEqual(debit['date'], (date.today() + timedelta(days=14)).isoformat())   # Vorabankündigung 14 Tage
        self.assertEqual(debit['date'], l.business_day(debit['date']))
        due = self.s.db.execute('SELECT due FROM invoices WHERE id=?', (a,)).fetchone()[0]
        self.assertEqual(due, debit['date'])
        self.assertNotIn('debit', json.loads(self.s.db.execute('SELECT snapshot FROM invoices WHERE id=?', (b,)).fetchone()[0]))
        inv = rechnung_pdf.invoice_data(self.s, a)
        self.assertIsNone(rechnung_pdf.epc_payload(inv))                                 # kein GiroCode bei Lastschrift
        subject, body = versand.texts(self.s.config(), inv)
        self.assertIn('per SEPA-Lastschrift', body)
        self.assertIn('JUHH-M1-1', body)
        self.assertIn('Einzug am', versand.html_body(self.s.config(), inv, body))
        self.assertTrue(rechnung_pdf.render([inv]).startswith(b'%PDF'))
        preview = [r for r in self.s.dues_preview(2028) if r['id'] == with_mandate][0]
        self.assertTrue(preview['debit'])

    def test_batch_xml_matches_schema(self):
        mids = [self.member('M1', 'Erika Müller'), self.member('M2', 'Şahin Yıldız')]
        for mid in mids:
            self.mandate(mid, bic='COBADEFFXXX' if mid == mids[0] else '')
        ids = [self.dues(mid) for mid in mids]
        items = l.collectable(self.s)
        self.assertEqual([(i['id'], i['reason']) for i in items], [(ids[0], None), (ids[1], None)])
        batch = l.create_batch(self.s, ids, 'Test')
        xml = l.xml_file(self.s, batch)
        self.assertIn(b'pain.008.001.08', xml)
        self.assertIn(b'<SeqTp>RCUR</SeqTp>', xml)
        self.assertIn(b'<Nm>Sahin Yildiz</Nm>', xml)
        self.assertIn(b'<Id>NOTPROVIDED</Id>', xml)                                       # IBAN-only beim Zahlungspflichtigen
        self.assertIn(b'<CtrlSum>60.00</CtrlSum>', xml)
        try:
            from lxml import etree
        except ImportError:
            self.skipTest('lxml fehlt – Schemaprüfung übersprungen')
        schema = etree.XMLSchema(etree.parse(str(XSD)))
        schema.assertValid(etree.fromstring(xml))
        self.assertEqual([i['reason'] for i in l.collectable(self.s)], ['bereits in einer Lastschriftdatei'] * 2)
        with self.assertRaises(ValueError):
            l.create_batch(self.s, ids, 'Test')                                             # keine zweite Datei für dieselben Rechnungen
        l.set_batch(self.s, batch, 'cancelled', 'Test')
        self.assertEqual([i['reason'] for i in l.collectable(self.s)], [None, None])       # verworfen → wieder einziehbar

    def test_credit_return_and_reminder(self):
        mid = self.member()
        self.mandate(mid)
        iid = self.dues(mid)
        number = self.number(iid)
        self.assertIn('noch nicht eingereicht', versand.reminders(self.s, [iid])[0]['reason'])
        batch = l.create_batch(self.s, [iid], 'Test')
        with self.assertRaises(ValueError):
            l.set_batch(self.s, batch, 'submitted', 'Test')                                 # erst herunterladen
        self.submit(batch)
        with self.assertRaises(ValueError):
            l.set_batch(self.s, batch, 'submitted', 'Test')
        self.assertIn('per SEPA-Lastschrift eingezogen', versand.reminders(self.s, [iid])[0]['reason'])

        credit = self.bank('30', f'Mitgliedsbeitrag 2027 {number}', 'g1')
        import verbindlichkeiten as v
        self.assertEqual([(p['kind'], p['id']) for p in v.proposals(self.s)], [('invoice', iid)])  # Gutschrift wird erkannt
        self.s.book(credit, [{'invoice_id': iid, 'amount': '30'}], 'Test')
        self.assertEqual(self.s.invoices()[0]['paid'], 3000)
        returned = self.bank('-30', f'RUECKLASTSCHRIFT {number}', 'r1')
        self.s.book(returned, [{'invoice_id': iid, 'amount': '-30'}], 'Test')           # Rücklastschrift der Rechnung zuordnen
        self.assertEqual((self.s.invoices()[0]['paid'], l.debit_state(self.s, iid)), (0, 'returned'))
        inv = rechnung_pdf.invoice_data(self.s, iid)
        self.assertIsNone(rechnung_pdf.debit_of(inv))                                      # jetzt per Überweisung zahlen
        self.assertIsNotNone(rechnung_pdf.epc_payload(inv))
        self.assertIn('Rücklastschrift', l.collectable(self.s)[0]['reason'])
        self.s.reverse_bank(returned, 'Test')                                               # Zuordnung der Rücklastschrift storniert
        self.assertEqual(l.debit_state(self.s, iid), 'pending')
        with self.assertRaises(ValueError):
            l.set_batch(self.s, batch, 'cancelled', 'Test', 'von der Bank abgelehnt')       # schon verbuchte Gutschrift

    def test_revoked_mandate_blocks_collection(self):
        mid = self.member()
        self.mandate(mid)
        iid = self.dues(mid)
        l.revoke_mandate(self.s, l.active_mandate(self.s, mid)['id'], 'Widerruf durch das Mitglied', 'Test')
        self.assertTrue(l.collectable(self.s)[0]['reason'].startswith('Mandat widerrufen'))
        with self.assertRaises(ValueError):
            l.create_batch(self.s, [iid], 'Test')
        inv = rechnung_pdf.invoice_data(self.s, iid)                                       # fällt auf Überweisung zurück
        self.assertIsNone(rechnung_pdf.debit_of(inv))
        self.assertIsNotNone(rechnung_pdf.epc_payload(inv))
        self.assertNotIn('Lastschrift', versand.reminders(self.s, [iid])[0]['reason'] or '')
        self.assertIn('Bitte überweise', versand.texts(self.s.config(), inv)[1])


class ReviewTests(Base):
    """Befunde der unabhängigen Prüfung von 0.15: erstellte Dateien werden erneut geprüft, nichts wird doppelt eingezogen."""

    def batch(self, *mids):
        ids = [self.dues(mid) for mid in mids]
        return ids, l.create_batch(self.s, ids, 'Test')

    def test_revoked_after_batch_blocks_file(self):
        a, b = self.member(), self.member('M2', 'Max Muster', 'max@example.org')
        self.mandate(a)
        self.mandate(b)
        (ia, ib), batch = self.batch(a, b)
        l.revoke_mandate(self.s, l.active_mandate(self.s, a)['id'], 'Widerruf durch das Mitglied', 'Test')
        row = l.batches(self.s)[0]
        self.assertEqual(row['issues'], 1)
        bad = [d for d in row['items'] if d['issue']]
        self.assertEqual((bad[0]['invoice_id'], bad[0]['issue']), (ia, 'Mandat widerrufen'))
        for action in (lambda: l.download(self.s, batch, 'Test'), lambda: l.set_batch(self.s, batch, 'submitted', 'Test')):
            with self.assertRaises(ValueError):
                action()
        l.remove_debit(self.s, bad[0]['id'], 'Test')                                        # nie heruntergeladen → herausnehmen
        self.assertNotIn(b'JUHH-M1-1', l.download(self.s, batch, 'Test'))                 # Datei ohne den Widerruf
        row = l.batches(self.s)[0]
        self.assertEqual((row['count'], row['total'], row['issues'], bool(row['downloaded_on'])), (1, 3000, 0, True))
        with self.assertRaises(ValueError):
            l.set_batch(self.s, batch, 'cancelled', 'Test')                                 # heruntergeladen → nur mit Grund
        l.set_batch(self.s, batch, 'submitted', 'Test')
        with self.assertRaises(ValueError):
            l.download(self.s, batch, 'Test')                                               # eingereicht → nie ein zweites Mal hochladen

    def test_downloaded_file_stays_as_it_is(self):
        """Befunde A und B der zweiten Prüfung: eine heruntergeladene Datei wird nicht mehr verändert."""
        mid = self.member()
        self.mandate(mid)
        (iid,), batch = self.batch(mid)
        l.download(self.s, batch, 'Test')
        debit = l.batches(self.s)[0]['items'][0]
        late = (date.fromisoformat(debit['collection_date']) + timedelta(days=1)).isoformat()
        self.assertIn('schon hochgeladen', l.issues(self.s, batch, late)[debit['id']])    # Befund A: nicht „neu einziehen“
        l.revoke_mandate(self.s, l.active_mandate(self.s, mid)['id'], 'Widerruf durch das Mitglied', 'Test')
        debit = l.batches(self.s)[0]['items'][0]
        self.assertEqual(debit['issue'], 'Mandat widerrufen')
        with self.assertRaises(ValueError) as cm:
            l.remove_debit(self.s, debit['id'], 'Test')                                     # kein Herausnehmen ohne Grund
        self.assertIn('heruntergeladen', str(cm.exception))
        with self.assertRaises(ValueError):
            l.download(self.s, batch, 'Test')
        l.set_batch(self.s, batch, 'submitted', 'Test')                                     # hochgeladen: dokumentieren, was bei der Bank liegt
        self.assertEqual(l.debit_state(self.s, iid), 'pending')

    def test_downloaded_file_with_credit_not_cancelled(self):
        """Befund G: Gutschrift ab dem Einzugsdatum → die Datei wurde ausgeführt."""
        mid = self.member()
        self.mandate(mid)
        (iid,), batch = self.batch(mid)
        l.download(self.s, batch, 'Test')
        day = l.batches(self.s)[0]['dates'][0]
        credit = self.bank('30', f'SEPA-Lastschrift {self.number(iid)}', 'g1', day=day)
        self.s.book(credit, [{'invoice_id': iid, 'amount': '30'}], 'Test')
        with self.assertRaises(ValueError):
            l.set_batch(self.s, batch, 'cancelled', 'Test', 'nie hochgeladen')
        self.assertEqual(l.debit_state(self.s, iid), 'pending')

    def test_single_debit_not_executed(self):
        """Befund C: Bank weist eine Lastschrift ab, ohne dass Geld fließt."""
        mid = self.member()
        self.mandate(mid)
        (iid,), batch = self.batch(mid)
        self.submit(batch)
        debit = l.batches(self.s)[0]['items'][0]
        with self.assertRaises(ValueError):
            l.reject_debit(self.s, debit['id'], '', 'Test')
        l.reject_debit(self.s, debit['id'], 'IBAN erloschen laut Bank', 'Test')
        d = l.info(self.s, self.inv(iid))
        self.assertEqual((d['state'], d['active']), ('returned', False))
        self.assertIn('nicht ausgeführt', d['reason'])
        inv = rechnung_pdf.invoice_data(self.s, iid)
        self.assertIsNone(rechnung_pdf.debit_of(inv))
        self.assertIsNotNone(rechnung_pdf.epc_payload(inv))
        self.s.reverse_invoice(iid, 'Test')                                                 # jetzt stornierbar

    def test_reject_blocked_after_money(self):
        mid = self.member()
        self.mandate(mid)
        (iid,), batch = self.batch(mid)
        self.submit(batch)
        self.bank('30', f'SEPA-Lastschrift {self.number(iid)}', 'g1')
        with self.assertRaises(ValueError):
            l.reject_debit(self.s, l.batches(self.s)[0]['items'][0]['id'], 'abgelehnt', 'Test')

    def test_paid_by_transfer_before_submission(self):
        mid = self.member()
        self.mandate(mid)
        (iid,), batch = self.batch(mid)
        transfer = self.bank('30', f'Beitrag {self.number(iid)}', 'u1')
        self.assertIn('Zahlungseingang', l.batches(self.s)[0]['items'][0]['issue'])       # schon im Kontoauszug, noch nicht zugeordnet
        self.s.book(transfer, [{'invoice_id': iid, 'amount': '30'}], 'Test')
        debit = l.batches(self.s)[0]['items'][0]
        self.assertEqual(debit['issue'], 'Rechnung inzwischen bezahlt')
        l.remove_debit(self.s, debit['id'], 'Test')                                         # letzte Lastschrift → Datei verworfen
        self.assertEqual(l.batches(self.s)[0]['status'], 'cancelled')
        self.assertEqual(l.collectable(self.s), [])

    def test_invoice_storno_blocked_while_in_file(self):
        mid = self.member()
        self.mandate(mid)
        (iid,), batch = self.batch(mid)
        with self.assertRaises(ValueError):
            self.s.reverse_invoice(iid, 'Test')
        self.submit(batch)
        with self.assertRaises(ValueError):
            self.s.reverse_invoice(iid, 'Test')

    def test_submitted_file_with_money_not_cancelled(self):
        mid = self.member()
        self.mandate(mid)
        (iid,), batch = self.batch(mid)
        self.submit(batch)
        with self.assertRaises(ValueError):
            l.set_batch(self.s, batch, 'cancelled', 'Test')                                 # ohne Grund
        self.bank('30', f'SEPA-Lastschrift {self.number(iid)}', 'g1')                      # Gutschrift importiert, nicht zugeordnet
        with self.assertRaises(ValueError):
            l.set_batch(self.s, batch, 'cancelled', 'Test', 'von der Bank abgelehnt')
        self.assertEqual(l.debit_state(self.s, iid), 'pending')

    def test_unassigned_transfer_blocks_collection(self):
        mid = self.member()
        self.mandate(mid)
        iid = self.dues(mid)
        self.bank('30', f'Beitrag {self.number(iid)}', 'u1')
        self.assertIn('Zahlungseingang vom', l.collectable(self.s)[0]['reason'])
        with self.assertRaises(ValueError):
            l.create_batch(self.s, [iid], 'Test')

    def test_prenotification_must_be_sent(self):
        mid = self.member()
        self.mandate(mid)
        iid = self.dues(mid, notify=False)
        item = l.collectable(self.s)[0]
        self.assertEqual(item['state'], 'unnotified')
        self.assertIn('Vorabankündigung nicht versendet', item['reason'])
        self.assertIn('Vorabankündigung nicht versendet', versand.reminders(self.s, [iid])[0]['reason'])
        with self.assertRaises(ValueError):
            l.record_notice(self.s, iid, (date.today() + timedelta(days=1)).isoformat(), 'Test')
        announced = json.loads(self.inv(iid)['snapshot'])['debit']['date']
        never = (date.fromisoformat(announced) + timedelta(days=1)).isoformat()            # bis nach dem Datum nie versendet
        self.assertEqual((l.info(self.s, self.inv(iid), today=never)['state'], l.info(self.s, self.inv(iid), today=never)['active']), ('missed', False))
        l.record_notice(self.s, iid, date.today().isoformat(), 'Test')                    # per Post versendet
        self.assertEqual(l.collectable(self.s)[0]['reason'], None)

    def test_late_notice_shifts_date_and_missed_date_falls_back(self):
        mid = self.member()
        self.mandate(mid)
        iid = self.dues(mid, notify=False)
        announced = json.loads(self.inv(iid)['snapshot'])['debit']['date']
        late = (date.fromisoformat(announced) - timedelta(days=10)).isoformat()            # Versand 10 statt 14 Tage vorher
        with self.s.db:
            self.s.db.execute("INSERT INTO debit_notices(invoice_id,sent_on,created) VALUES(?,?,'x')", (iid, late))
        d = l.info(self.s, self.inv(iid), today=late)
        self.assertEqual((d['state'], d['late']), ('ready', True))
        self.assertEqual(d['date'], l.business_day((date.fromisoformat(late) + timedelta(days=14)).isoformat()))   # volle Frist ab Versand
        self.assertGreater(d['date'], announced)
        after = (date.fromisoformat(announced) + timedelta(days=l.GRACE_DAYS + 1)).isoformat()
        d = l.info(self.s, self.inv(iid), today=after)
        self.assertEqual((d['state'], d['active']), ('missed', False))
        self.assertEqual([c['state'] for c in l.collectable(self.s, after)], ['missed'])
        self.assertEqual(d['pay_by'], (date.fromisoformat(announced) + timedelta(days=l.GRACE_DAYS + l.REPAY_DAYS)).isoformat())
        self.assertEqual(l.info(self.s, self.inv(iid), today='2099-01-01')['pay_by'], d['pay_by'])   # feste Frist, wandert nicht

    def test_mandate_expires_after_36_months(self):
        self.assertEqual(l.add_months('2024-02-29', 36), '2027-02-28')
        a, b = self.member(), self.member('M2', 'Max Muster', 'max@example.org')
        self.mandate(a, signed_on='2019-03-01')
        self.mandate(b, signed_on='2019-03-01', used_before='2025-06-01')                  # Papiermandat, zuletzt 2025 genutzt
        preview = {r['id']: r['debit'] for r in self.s.dues_preview(2027)}
        self.assertEqual((preview[a], preview[b]), (False, True))                          # Vorschau wie die fertige Rechnung
        ia, ib = self.dues(a), self.dues(b)
        self.assertNotIn('debit', json.loads(self.inv(ia)['snapshot']))                   # verfallen → Rechnung zur Überweisung
        self.assertEqual([c['id'] for c in l.collectable(self.s)], [ib])
        self.assertEqual(l.mandates(self.s)[0]['expires'], '2028-06-01')
        with self.s.db:                                                                     # Mandat verfällt nach dem Beitragslauf
            self.s.db.execute("UPDATE mandates SET used_before=NULL WHERE member_id=?", (b,))
        d = l.info(self.s, self.inv(ib))
        self.assertEqual((d['state'], d['active']), ('expired', False))

    def test_postal_notice_only_while_unnotified(self):
        """Befund F: eine entfallene Lastschrift wird durch einen nachgetragenen Postversand nie wieder einziehbar."""
        mid = self.member()
        self.mandate(mid)
        mailed = self.dues(mid)
        with self.assertRaises(ValueError):
            l.record_notice(self.s, mailed, date.today().isoformat(), 'Test')              # schon per E-Mail versendet
        other = self.member('M2', 'Max Muster', '')
        self.mandate(other)
        iid = self.dues(other, notify=False)
        snap = json.loads(self.inv(iid)['snapshot'])
        snap['debit']['date'] = (date.today() - timedelta(days=20)).isoformat()            # angekündigtes Datum längst vorbei
        with self.s.db:
            self.s.db.execute('UPDATE invoices SET snapshot=?,issued=? WHERE id=?', (json.dumps(snap), (date.today() - timedelta(days=40)).isoformat(), iid))
        self.assertEqual(l.info(self.s, self.inv(iid))['state'], 'missed')
        with self.assertRaises(ValueError):
            l.record_notice(self.s, iid, (date.today() - timedelta(days=40)).isoformat(), 'Test')
        self.assertEqual(l.info(self.s, self.inv(iid))['state'], 'missed')

    def test_columns_added_to_first_015_database(self):
        """Befund H: Datenbank aus dem ersten 0.15-Stand bekommt die neuen Spalten (nur additiv)."""
        import sqlite3
        path = Path(self.tmp.name) / 'alt.db'
        db = sqlite3.connect(path)
        db.executescript('''CREATE TABLE mandates(id INTEGER PRIMARY KEY,member_id INTEGER NOT NULL,reference TEXT UNIQUE NOT NULL,holder TEXT NOT NULL,
            iban TEXT NOT NULL,bic TEXT,signed_on TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',revoked_on TEXT,revoke_reason TEXT,created TEXT NOT NULL,actor TEXT);
            CREATE TABLE debit_batches(id INTEGER PRIMARY KEY,msg_id TEXT UNIQUE NOT NULL,created TEXT NOT NULL,count INTEGER NOT NULL,total INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',submitted_on TEXT,actor TEXT);
            CREATE TABLE debits(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL,invoice_id INTEGER NOT NULL,mandate_id INTEGER NOT NULL,amount INTEGER NOT NULL,
            collection_date TEXT NOT NULL,end_to_end TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',returned_on TEXT);''')
        db.close()
        store = Store(path)
        try:
            cols = lambda t: {c[1] for c in store.db.execute(f'PRAGMA table_info({t})')}
            self.assertLessEqual({'used_before'}, cols('mandates'))
            self.assertLessEqual({'downloaded_on', 'cancel_reason'}, cols('debit_batches'))
            self.assertLessEqual({'note'}, cols('debits'))
        finally:
            store.db.close()

    def test_partial_refund_is_no_return(self):
        mid = self.member()
        self.mandate(mid)
        iid = self.dues(mid)
        part = self.bank('10', f'Beitrag {self.number(iid)} Teilzahlung', 'p1')
        self.s.book(part, [{'invoice_id': iid, 'amount': '10'}], 'Test')
        batch = l.create_batch(self.s, [iid], 'Test')                                       # Rest 20 € per Lastschrift
        self.submit(batch)
        refund = self.bank('-10', 'Erstattung Teilzahlung', 'e1')
        self.s.book(refund, [{'invoice_id': iid, 'amount': '-10'}], 'Test')
        self.assertEqual(l.debit_state(self.s, iid), 'pending')                             # keine Rücklastschrift

    def test_xml_uses_announced_creditor_id(self):
        mid = self.member()
        self.mandate(mid)
        (iid,), batch = self.batch(mid)
        self.s.save_settings({'creditor_id': 'DE79ZZZ01234567890'}, 'Test')                     # später geänderte Gläubiger-ID (erfunden)
        xml = l.xml_file(self.s, batch)
        self.assertIn(CREDITOR.encode(), xml)
        self.assertNotIn(b'DE79ZZZ01234567890', xml)


if __name__ == '__main__':
    unittest.main()
