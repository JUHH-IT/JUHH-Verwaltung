"""Befunde der unabhängigen Prüfung zu 0.23 (Fachlogik) – Regressionstests mit erfundenen Daten."""
import os
os.environ['JU_FINANZEN_OHNE_SCHLUESSELBUND'] = '1'
import datetime as _dt
import io
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import core
import haushalt
import kvbudget
import mitglieder
import rechnung_pdf
import verbindlichkeiten
from core import Store


def fake_today(day, *modules):
    class FakeDate(_dt.date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(day)
    return [unittest.mock.patch.object(m, 'date', FakeDate) for m in modules]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1\n20000 Hamburg', 'iban': 'DE89 3704 0044 0532 0130 00',
                              'age_basis': 'jan1', 'invoice_ready': 'yes', 'tax_note': 'Nur fiktive Testrechnung'}, 'Test')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, kv='5300', dues=None, joined='2024-01-01'):
        self.n += 1
        return self.s.member(dict(number=f'T{self.n}', first_name='Erika', last_name='Testmann', street='Teststraße 1', zip='20000', city='Hamburg',
                                  birth='2000-02-01', joined=joined, kv=kv, dues=dues), 'Test')

    def bank(self, amount, day='2026-06-01', purpose='Beitrag'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};Test;{purpose};r{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'r{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def balance(self, account):
        return self.s.db.execute('SELECT COALESCE(SUM(debit-credit),0) FROM entries WHERE account=?', (account,)).fetchone()[0]


class DuesYearTests(Base):
    def test_dues_invoice_for_next_year_counts_in_that_year(self):
        m = self.member()
        patches = fake_today('2026-12-11', core)                     # Versand im Dezember (Satzung § 49 Abs. 4)
        for p in patches:
            p.start()
        try:
            self.s.issue_dues(2027, [m], 'Test')
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(haushalt.actuals(self.s, 2026)[0]['H4'], 0)
        self.assertEqual(haushalt.actuals(self.s, 2027)[0]['H4'], 3000)

    def test_cancel_keeps_fiscal_year_of_invoice(self):
        iid = self.s.issue_series({'kv': 'LV', 'due': '', 'member_ids': [], 'recipients': [{'name': 'Gast', 'address': 'Weg 1', 'email': ''}],
                                   'lines': [{'description': 'Sommerfest', 'amount': '25', 'account': '8504'}]}, 'Test')[0]
        year = int(self.s.db.execute("SELECT substr(issued,1,4) FROM invoices WHERE id=?", (iid,)).fetchone()[0])
        patches = fake_today(f'{year + 1}-01-05', core)
        for p in patches:
            p.start()
        try:
            self.s.reverse_invoice(iid, 'Test')
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(self.s.db.execute("SELECT year FROM journals WHERE source=?", (f'cancel:{iid}',)).fetchone()[0], year)
        self.assertEqual(sum(haushalt.actuals(self.s, year + 1)[0].values()), 0)

    def test_invoice_income_gets_title_of_its_account(self):
        self.s.issue_series({'kv': 'LV', 'due': '', 'member_ids': [], 'recipients': [{'name': 'Gast', 'address': 'Weg 1', 'email': ''}],
                             'lines': [{'description': 'Sommerfest', 'amount': '25', 'account': '8504'}]}, 'Test')
        year = _dt.date.today().year
        values, unmatched, _, _ = haushalt.actuals(self.s, year)
        self.assertEqual(unmatched['income'], 0)
        self.assertEqual(values[haushalt.default_title('8504', 'LV')], 2500)
        # Altbestand ohne Titel (bis 0.22) wird über das Konto zugeordnet
        self.s.db.execute("UPDATE entries SET title='' WHERE account='8504'")
        self.assertEqual(haushalt.actuals(self.s, year)[1]['income'], 0)


class InvoiceTests(Base):
    def test_cancel_after_returned_debit(self):
        m = self.member()
        iid = self.s.issue_dues(2026, [m], 'Test')[0]
        b1 = self.bank('30', '2026-07-01')
        self.s.book(b1, [{'amount': '30', 'invoice_id': iid}], 'Test')
        b2 = self.bank('-30', '2026-07-08', 'Rücklastschrift')
        self.s.book(b2, [{'amount': '-30', 'invoice_id': iid}], 'Test', confirm_no_bill=True)
        self.s.reverse_invoice(iid, 'Test')                              # Zahlungen heben sich auf: Storno erlaubt
        self.assertEqual(self.balance('1500'), 0)
        self.assertEqual(self.s.db.execute('SELECT status FROM invoices WHERE id=?', (iid,)).fetchone()[0], 'cancelled')
        iid2 = self.s.issue_series({'kv': 'LV', 'due': '', 'member_ids': [], 'recipients': [{'name': 'Gast', 'address': 'Weg 1', 'email': ''}],
                                    'lines': [{'description': 'Fest', 'amount': '10', 'account': '8504'}]}, 'Test')[0]
        b3 = self.bank('10', '2026-07-09', 'Fest')
        self.s.book(b3, [{'amount': '10', 'invoice_id': iid2}], 'Test')
        with self.assertRaises(ValueError):
            self.s.reverse_invoice(iid2, 'Test')                         # echte Zahlung sperrt weiterhin

    def test_long_line_text(self):
        data = {'kv': 'LV', 'due': '', 'member_ids': [], 'recipients': [{'name': 'Gast', 'address': 'Weg 1', 'email': ''}],
                'lines': [{'description': 'x' * (core.MAX_LINE_TEXT + 1), 'amount': '10', 'account': '8504'}]}
        with self.assertRaises(ValueError):
            self.s.issue_series(data, 'Test')
        data['lines'][0]['description'] = 'Langer Text ' * 20
        iid = self.s.issue_series(data, 'Test')[0]
        self.s.db.execute('UPDATE lines SET description=? WHERE invoice_id=?', ('Altbestand ' * 800, iid))   # sehr langer Altbestand
        self.assertTrue(rechnung_pdf.render([rechnung_pdf.invoice_data(self.s, iid)]).startswith(b'%PDF'))

    def test_incoming_payment_with_invoice_number_needs_confirmation(self):
        m = self.member()
        iid = self.s.issue_dues(2026, [m], 'Test')[0]
        number = self.s.db.execute('SELECT number FROM invoices WHERE id=?', (iid,)).fetchone()[0]
        b = self.bank('30', '2026-07-01', number)
        part = [{'amount': '30', 'account': '8100', 'kv': 'LV', 'members': [{'member_id': m}]}]
        with self.assertRaises(ValueError) as cm:
            self.s.book(b, part, 'Test')
        self.assertIn(number, str(cm.exception))
        self.assertTrue(str(cm.exception).startswith(Store.INVOICE_CONFLICT))
        self.s.book(b, part, 'Test', confirm_no_bill=True)               # ausdrücklich bestätigt
        self.assertEqual(self.s.db.execute("SELECT state FROM bank WHERE id=?", (b,)).fetchone()[0], 'booked')

    def test_prorated_dues_round_half_up(self):
        m = self.member(dues='25,50', joined='2026-02-01')
        self.s.save_settings({'dues_prorate': 'yes'}, 'Test')
        row = self.s.db.execute('SELECT * FROM members WHERE id=?', (m,)).fetchone()
        self.assertEqual(self.s.dues_calc(row, 2026)['amount'], 1913)    # 25,50 € × 75 % = 19,125 → 19,13 €


class DuesRefundTests(Base):
    def test_refund_on_8100_with_member(self):
        m = self.member()
        b1 = self.bank('30', '2026-07-01')
        self.s.book(b1, [{'amount': '30', 'account': '8100', 'kv': 'LV', 'members': [{'member_id': m}]}], 'Test')
        b2 = self.bank('-30', '2026-07-05', 'Erstattung')
        self.s.book(b2, [{'amount': '-30', 'account': '8100', 'kv': 'LV', 'members': [{'member_id': m}]}], 'Test', confirm_no_bill=True)
        self.assertEqual(haushalt.actuals(self.s, 2026)[0]['H4'], 0)
        self.assertEqual(self.s.db.execute('SELECT SUM(amount) FROM dues_assignments WHERE member_id=?', (m,)).fetchone()[0], 0)
        b3 = self.bank('-5', '2026-07-06', 'Falsch')
        with self.assertRaises(ValueError):                               # andere Einnahmenkonten bleiben gesperrt
            self.s.book(b3, [{'amount': '-5', 'account': '8500', 'kv': 'LV'}], 'Test', confirm_no_bill=True)


class KvClosingTests(Base):
    def test_closing_due_with_assigned_payments_only(self):
        m = self.member(kv='5300')
        b = self.bank('30', '2026-03-01')
        self.s.book(b, [{'amount': '30', 'account': '8100', 'kv': 'LV', 'members': [{'member_id': m}]}], 'Test')
        self.assertIn(2026, kvbudget.closing_due(self.s, today='2027-02-15'))
        kvbudget.close_year(self.s, 2026, 'Test', today='2027-02-15')
        self.assertNotIn(2026, kvbudget.closing_due(self.s, today='2027-02-15'))


class BillTests(Base):
    def test_cancel_reverses_write_off(self):
        bid, _, _ = verbindlichkeiten.create(self.s, {'creditor': 'Druckerei Beispiel', 'issued': '2026-05-01', 'kv': 'LV', 'reference': 'D-1',
                                                      'lines': [{'description': 'Druck', 'amount': '100', 'account': '4210'}]}, 'Test')
        b = self.bank('-90', '2026-05-10', 'D-1')
        self.s.book(b, [{'amount': '-90', 'bill_id': bid}], 'Test')
        verbindlichkeiten.write_off(self.s, bid, 'Skonto', 'Test')
        self.s.reverse_bank(b, 'Test')
        verbindlichkeiten.cancel(self.s, bid, 'Test')
        self.assertEqual(self.balance('1600'), 0)
        self.assertEqual(self.balance('4210'), 0)
        self.assertEqual(haushalt.actuals(self.s, 2026)[0]['H27'], 0)


class ImportTests(Base):
    def test_member_csv_with_mac_line_endings(self):
        rows = ['Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband',
                '100;Erika;Testfrau;Weg 1;20000;Hamburg;01.02.2000;01.01.2024;Altona']
        for sep in ('\n', '\r\n', '\r'):
            raw = (sep.join(rows) + sep).encode('cp1252')
            self.assertEqual(mitglieder.preview(self.s, raw, 'liste.csv')['counts']['new'], 1, repr(sep))
        with self.assertRaises(ValueError):
            mitglieder.preview(self.s, b'Mitgliedsnummer;Vorname\n"1;"Erika\n' + b'x' * 200_000, 'liste.csv')

    def test_cancelled_status_with_strict_sql_quotes(self):
        con = self.s.db
        if hasattr(con, 'setconfig') and hasattr(sqlite3, 'SQLITE_DBCONFIG_DQS_DML'):
            con.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
        iid = self.s.issue_series({'kv': 'LV', 'due': '', 'member_ids': [], 'recipients': [{'name': 'Gast', 'address': 'Weg 1', 'email': ''}],
                                   'lines': [{'description': 'Fest', 'amount': '10', 'account': '8504'}]}, 'Test')[0]
        self.s.reverse_invoice(iid, 'Test')
        self.assertEqual(con.execute('SELECT status FROM invoices WHERE id=?', (iid,)).fetchone()[0], 'cancelled')


if __name__ == '__main__':
    unittest.main()
