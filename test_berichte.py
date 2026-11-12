"""Monatsbericht und Kassenprüfer-Paket – nur erfundene Testdaten."""
import csv
import hashlib
import io
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from pathlib import Path

import berichte
import freigabe
import haushalt
import kvbudget
import verbindlichkeiten as v
from core import Store


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.s = Store(self.data / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00',
                              'age_basis': 'jan1', 'invoice_ready': 'yes', 'tax_note': 'Fiktiv'}, 'Test')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, amount, day, ref='x', name='Test'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};{name};{ref};k{self.n}\n'.encode()
        preview = self.s.preview_bank(raw, f'auszug-{self.n}.csv')
        self.s.import_bank(preview, [2], 'Test')
        (self.data / 'Importe').mkdir(exist_ok=True)                                     # wie app.py: Original unter dem Hash
        (self.data / 'Importe' / (hashlib.sha256(raw).hexdigest() + '.csv')).write_bytes(raw)
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def book(self, amount, day, account, kv='5100', title='H5', ref='x'):
        bid = self.bank(amount, day, ref)
        self.s.book(bid, [{'account': account, 'kv': kv, 'title': title, 'amount': amount}], 'Test', confirm_no_bill=True)
        return bid

    def bill(self, amount, ext, attachment=True):
        data = {'creditor': 'Druckerei Muster GmbH', 'iban': 'DE89370400440532013000', 'ext_number': ext, 'issued': '2026-03-10', 'due': '',
                'kv': 'LV', 'lines': [{'description': 'Flyer', 'amount': amount, 'account': '4430', 'title': 'H32'}]}
        bid, number, filename = v.create(self.s, data, 'Test', '.pdf' if attachment else None)
        if filename:
            (self.data / 'Belege').mkdir(exist_ok=True)
            (self.data / 'Belege' / filename).write_bytes(b'%PDF-1.4 Testbeleg')
        return bid, number

    def scenario(self):
        mid = self.s.member(dict(number='M1', name='Test Mitglied', address='Weg 1', email='', birth='2000-02-01', joined='2024-01-01', kv='5100'), 'Test')
        iid = self.s.issue_dues(2026, [mid], 'Test')[0]
        number = self.s.db.execute('SELECT number FROM invoices WHERE id=?', (iid,)).fetchone()[0]
        self.s.book(self.bank('30', '2026-02-10', number), [{'invoice_id': iid, 'amount': '30'}], 'Test')
        self.book('-5', '2026-06-01', '4430', title='H32')                                  # KV-Ausgabe
        self.book('100', '2026-03-01', '8200')                                            # KV-Spende
        self.bank('-12,50', '2026-04-01', '=HYPERLINK("http://example.org")')              # offen, Formel im Text
        self.paid_bill, self.paid_number = self.bill('600', 'RE-1')                        # über 500 € → Freigabe nötig
        self.open_bill, self.open_number = self.bill('40', 'RE-2', attachment=False)
        kvbudget.save_cash(self.s, {'received_on': (date.today() - timedelta(days=40)).isoformat(), 'donor': 'Max Spender', 'amount': '50', 'kv': 'LV',
                                    'receiver1': 'Vorsitz Beispiel', 'receiver2': 'Schatz Beispiel', 'receipt': 'Q-1'}, 'Test')
        with self.s.db:
            self.s.db.execute("INSERT INTO household_plan VALUES(2026,'H32',-300,'Test')")   # Plan 3 €, Ist 600 € + 40 € + 5 € → über 10 %


class MonthlyTests(Base):
    def test_figures_and_pdf(self):
        self.scenario()
        m = berichte.monthly(self.s, '2026-10-28')
        self.assertEqual(m['month'], 'Oktober 2026')
        self.assertEqual(m['bank'], 3000 + 10000 - 500)                                    # offener Umsatz noch nicht gebucht
        self.assertEqual(m['open_bank'], {'count': 1, 'sum': -1250})
        self.assertEqual((m['payables']['count'], m['payables']['sum'], m['payables']['waiting']), (2, 64000, 1))
        self.assertEqual([o['code'] for o in m['overruns']], [haushalt.TITLES['H32']['code']])
        altona = [k for k in m['kv'] if k['code'] == '5100'][0]
        self.assertEqual(altona['available'], 750 + 10000 - 500)
        self.assertIn('5800', [k['code'] for k in m['kv']])                                # Schüler Union mit eigenem Budget
        self.assertEqual((m['donations']['count'], m['donations']['kv_share']), (1, 10000))
        self.assertTrue(any('Freigabe' in t for t in m['tasks']))
        self.assertTrue(any('Barspende Q-1' in t for t in m['tasks']))
        self.assertTrue(berichte.monthly_pdf(self.s, '2026-10-28').startswith(b'%PDF'))

    def test_empty_database(self):
        self.assertTrue(berichte.monthly_pdf(self.s).startswith(b'%PDF'))

    def test_month_cutoff_and_flows(self):
        self.scenario()
        june = berichte.monthly(self.s, '2026-06-30')
        self.assertEqual((june['month'], june['flows']['bank_out'], june['flows']['bank_in'], june['flows']['expense']), ('Juni 2026', -500, 0, -500))
        self.assertEqual(june['flows']['expense_titles'], [('5100 KV Altona / Elbvororte', -500)])
        self.assertEqual(june['totals']['expense'][3], -60000 - 4000 - 500)                # Haushalt-Ist nur bis 30.06. (Eingangsrechnungen vom 10.03.)
        self.assertEqual(june['bank'], 3000 + 10000 - 500)
        feb = berichte.monthly(self.s, '2026-02-28')
        self.assertIn(feb['totals']['expense'][3], (None, 0))                              # noch keine Ausgaben gebucht
        self.assertEqual((feb['bank'], feb['flows']['bank_in']), (3000, 3000))
        self.assertTrue(berichte.monthly_pdf(self.s, '2026-06-30').startswith(b'%PDF'))
        today = '2026-10-29'
        self.assertEqual([berichte.report_day(m, '2025-01-01', today) for m in ('2026-02', '2025-12', '2026-10', '')],
                         ['2026-02-28', '2025-12-31', today, today])
        self.assertEqual(berichte.report_day('2028-02', None, '2028-03-01'), '2028-02-29')   # Schaltjahr
        for bad in ('2026-13', '2026-11', '26-01', '2024-12', '2026-1'):
            with self.assertRaises(ValueError):
                berichte.report_day(bad, '2025-01-01', today)


class AuditPackageTests(Base):
    def files(self, year=2026):
        out = io.BytesIO()
        berichte.audit_package(self.s, year, self.data, out)
        z = zipfile.ZipFile(out)
        return {n.split('/', 1)[1]: z.read(n) for n in z.namelist()}

    def rows(self, data):
        return list(csv.reader(io.StringIO(data.decode('utf-8-sig')), delimiter=';'))

    def test_contents(self):
        self.scenario()
        f = self.files()
        for name in ('00-Uebersicht.pdf', 'Buchungsjournal-2026.csv', 'Haushalt-2026.pdf', 'Kontoumsaetze-2026.csv', 'Ausgangsrechnungen-2026.csv',
                     'Ausgangsrechnungen-2026.pdf', 'Eingangsrechnungen-2026.csv', 'Freigaben-2026.csv', 'Pruefprotokolle/Pruefprotokoll-Q1-2026.pdf',
                     'Spenden-2026.csv', 'Barspenden-2026.csv', 'KV-Budgets-2026.csv', 'Aenderungsprotokoll-2026.csv'):
            self.assertIn(name, f)
        self.assertEqual(f[f'Belege/{self.paid_number}-{self.paid_number}.pdf'], b'%PDF-1.4 Testbeleg')
        self.assertEqual(len([n for n in f if n.startswith('Kontoauszuege-Originale/')]), 4)        # vier importierte Auszüge
        bank = self.rows(f['Kontoumsaetze-2026.csv'])
        formula = [r for r in bank if 'HYPERLINK' in r[4]][0]
        self.assertTrue(formula[4].startswith("'="))                                      # keine Formel in Excel
        self.assertEqual(formula[2], '-12,50')                                            # negative Beträge bleiben Zahlen
        journal = self.rows(f['Buchungsjournal-2026.csv'])
        self.assertEqual(sum(float((r[8] or '0').replace(',', '.')) - float((r[9] or '0').replace(',', '.')) for r in journal[1:]), 0)
        bills = {r[0]: r for r in self.rows(f['Eingangsrechnungen-2026.csv'])[1:]}
        self.assertEqual((bills[self.paid_number][11], bills[self.open_number][10]), ('fehlt', 'fehlt'))

    def test_notes_in_overview(self):
        self.scenario()
        freigabe.approve(self.s, self.paid_bill, 'chair', {'name': 'Vorsitz Beispiel', 'approved_on': date.today().isoformat(), 'how': 'E-Mail'}, 'Test')
        out = io.BytesIO()
        berichte.audit_package(self.s, 2026, self.data, out)
        self.assertTrue(out.getvalue().startswith(b'PK'))
        _, notes = berichte.audit_files(self.s, 2026, self.data)
        self.assertTrue(any(n.startswith(f'Beleg fehlt: {self.open_number}') for n in notes))
        self.assertTrue(any('Bankumsätze nicht endgültig zugeordnet' in n for n in notes))
        self.assertTrue(any('Barspende Q-1' in n for n in notes))
        self.assertTrue(any('Quartalsprüfung Q1/2026 nicht protokolliert' in n for n in notes))

    def test_other_year_and_bad_year(self):
        self.scenario()
        f = self.files(2025)
        self.assertNotIn('Ausgangsrechnungen-2025.pdf', f)
        self.assertEqual(len(self.rows(f['Kontoumsaetze-2025.csv'])), 1)                    # nur Kopfzeile
        self.assertFalse([n for n in f if n.startswith('Kontoauszuege-Originale/')])
        with self.assertRaises(ValueError):
            berichte.audit_package(self.s, 1999, self.data, io.BytesIO())


if __name__ == '__main__':
    unittest.main()
