"""Auslagenerstattung nach Finanzordnung § 8 – nur erfundene Testdaten."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import erstattung
import freigabe
import verbindlichkeiten as v
from core import Store

IBAN = 'DE89370400440532013000'          # bekannte Beispiel-IBAN


class RefundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.member = self.s.member(dict(number='A1', name='Anna Antrag', address='Weg 1\n20000 Hamburg', email='anna@example.org',
                                         birth='2000-01-01', joined='2025-01-01', kv='5100'), 'Test')
        self.today = date.today()

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def data(self, cost=None, submitted=None, **extra):
        cost = cost or (self.today - timedelta(days=20)).isoformat()
        return {'kind': 'refund', 'member_id': self.member, 'creditor': '', 'iban': IBAN, 'issued': cost, 'due': '', 'kv': '5100',
                'submitted_on': submitted or self.today.isoformat(), 'signed': 'on', 'complete': 'on', 'authorized': 'on', 'no_other': 'on',
                'lines': [{'description': 'Getränke Stammtisch', 'amount': '23,40', 'account': '4430', 'title': 'H32'}], **extra}

    def row(self, bid):
        return [b for b in v.bills(self.s) if b['id'] == bid][0]

    def test_refund_with_application(self):
        bid = v.create(self.s, self.data(), 'Test', '.jpg')[0]
        b = self.row(bid)
        self.assertEqual((b['kind'], b['creditor'], b['total']), ('refund', 'Anna Antrag', 2340))           # Kreditor = Antragsteller
        self.assertEqual(b['due'], (self.today + timedelta(days=14)).isoformat())                            # Frist ab Antragseingang
        self.assertEqual((b['refund']['member'], b['refund']['late']), ('Anna Antrag', False))
        self.assertFalse(b['approval']['required'])                                                           # § 7 Abs. 4 gilt nicht
        self.assertEqual(freigabe.report(self.s, *freigabe.quarter_of(b['issued']))['bills'][0]['findings'], [])

    def test_payee_is_always_the_applicant(self):
        """§ 8 Abs. 7: überwiesen wird an den Antragsteller – auch wenn ein anderer Kreditor (Rechnungssteller des Belegs) angegeben ist."""
        bid = v.create(self.s, self.data(creditor='Hotel Muster GmbH', due=(self.today - timedelta(days=10)).isoformat()), 'Test', '.pdf')[0]
        b = self.row(bid)
        self.assertEqual((b['creditor'], b['due']), ('Anna Antrag', (self.today + timedelta(days=14)).isoformat()))   # Fälligkeit nie vor dem Antrag
        self.assertEqual(v.transfer(self.s, bid)['name'], 'Anna Antrag')
        with self.assertRaises(ValueError) as cm:
            v.create(self.s, self.data(iban='', lines=[{'description': 'Taxi', 'amount': '9', 'account': '4430', 'title': 'H32'}]), 'Test', '.pdf')
        self.assertIn('IBAN', str(cm.exception))

    def test_same_receipt_only_once(self):
        """Derselbe Beleg wird nicht zweimal erstattet – weder an ein anderes Mitglied noch zusätzlich als Fremdrechnung (§ 8 Abs. 6)."""
        v.create(self.s, self.data(ext_number='H-77'), 'Test', '.pdf')
        other = self.s.member(dict(number='A2', name='Bernd Beispiel', address='Weg 2', email='', birth='2000-01-01', joined='2025-01-01', kv='5100'), 'Test')
        with self.assertRaises(ValueError) as cm:
            v.create(self.s, self.data(ext_number='H 77', member_id=other), 'Test', '.pdf')
        self.assertTrue(str(cm.exception).startswith(v.DUPLICATE))
        external = {**self.data(ext_number='H-77'), 'kind': 'external', 'creditor': 'Hotel Muster GmbH'}
        with self.assertRaises(ValueError):
            v.create(self.s, external, 'Test')
        v.create(self.s, {**external, 'allow_duplicate': True}, 'Test')                       # nach Rückfrage bewusst erfassen

    def test_rules(self):
        cases = {'kein Mitglied gewählt': self.data(member_id=''), 'nicht unterschrieben': self.data(signed=''),
                 'andere Erstattung möglich': self.data(no_other=False), 'Antrag vor der Ausgabe': self.data(submitted=(self.today - timedelta(days=30)).isoformat()),
                 'Antrag in der Zukunft': self.data(submitted=(self.today + timedelta(days=1)).isoformat()),
                 'vor dem Eintritt': self.data(cost='2024-12-20')}
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                v.create(self.s, data, 'Test', '.pdf')
        with self.assertRaises(ValueError) as cm:
            v.create(self.s, self.data(), 'Test')                                                             # ohne Nachweis
        self.assertIn('Nachweis', str(cm.exception))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bills').fetchone()[0], 0)

    def test_three_month_rule(self):
        cost = (self.today - timedelta(days=120)).isoformat()
        with self.assertRaises(ValueError) as cm:
            v.create(self.s, self.data(cost=cost), 'Test', '.pdf')
        self.assertIn('drei Monate', str(cm.exception))
        bid = v.create(self.s, self.data(cost=cost, late_reason='Beschluss geschfd. LaVo vom 01.10.'), 'Test', '.pdf')[0]
        b = self.row(bid)
        self.assertTrue(b['refund']['late'])
        findings = freigabe.report(self.s, *freigabe.quarter_of(b['issued']))['bills'][0]['findings']
        self.assertEqual(findings, ['Antrag nach Ablauf von drei Monaten – Ausnahme: Beschluss geschfd. LaVo vom 01.10.'])

    def test_refund_without_application_data_is_flagged(self):
        bid = v.create(self.s, self.data(), 'Test', '.pdf')[0]
        with self.s.db:
            self.s.db.execute('DELETE FROM refunds')                                                            # wie vor 0.17 erfasst
        b = self.row(bid)
        self.assertIsNone(b['refund'])
        self.assertIn('Antragsdaten nach § 8 nicht erfasst (vor Version 0.17)', freigabe.report(self.s, *freigabe.quarter_of(b['issued']))['bills'][0]['findings'])

    def test_form(self):
        self.assertTrue(erstattung.form_pdf(self.s).startswith(b'%PDF'))
        self.assertTrue(erstattung.form_pdf(self.s, self.member).startswith(b'%PDF'))
        self.assertEqual(erstattung.deadline('2026-11-30'), '2027-02-28')


if __name__ == '__main__':
    unittest.main()
