"""Kontenrahmen, Bestandskonten beim Verbuchen und Kontenblatt (kontenrahmen.py) – nur erfundene Testdaten."""
import csv
import io
import tempfile
import unittest
from pathlib import Path

import haushalt
import kontenrahmen as kr
from core import Store


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, amount, day='2026-03-02', ref='Testzahlung'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};Beispiel;{ref};k{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'k{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def book(self, amount, account, day='2026-03-02', kv='LV', title=''):
        bid = self.bank(amount, day)
        self.s.book(bid, [{'account': account, 'kv': kv, 'title': title, 'amount': amount}], 'Test', confirm_no_bill=True)
        return bid


class KontenrahmenTests(Base):
    def test_all_accounts_and_names(self):
        codes = {r[0]: r for r in self.s.db.execute('SELECT code,name,kind FROM accounts')}
        self.assertTrue(set(kr.KR) <= set(codes))                                      # alle Konten des Kontenrahmens
        self.assertEqual(codes['4210'][1], 'Bürobedarf / Telefon / EDV')                 # Haushaltskonto: Name aus der Excel-Vorlage
        self.assertEqual(codes['8590'][1], 'Unterstützung Haspa Marathon')
        self.assertEqual(codes['4232'][1], 'EDV-Kosten')
        self.assertEqual((codes['1400'][1], codes['1400'][2]), ('Kasse', 'asset'))       # neues Konto: Name des Kontenrahmens
        self.assertEqual((codes['2600'][2], codes['9900'][2]), ('equity', 'closing'))
        self.assertIn('KV', codes)                                                       # JU-Konten bleiben
        self.assertIn('1371', codes)
        for code, name, kind, _ in kr.ACCOUNTS:
            self.assertTrue(code.isdigit() and len(code) == 4 and kind in kr.KINDS and name, code)
        self.assertEqual(len({a[0] for a in kr.ACCOUNTS}), len(kr.ACCOUNTS))             # keine doppelte Kontonummer
        # erneuter Start ändert nichts
        before = self.s.rows('SELECT * FROM accounts ORDER BY code')
        self.s.db.close()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.assertEqual(self.s.rows('SELECT * FROM accounts ORDER BY code'), before)

    def test_catalog_flags(self):
        cat = {a['code']: a for a in kr.catalog(self.s)}
        self.assertTrue(cat['4210']['household'] and not cat['4210']['bank'])
        self.assertFalse(cat['4232']['household'])                                      # Kontenrahmen-Konto ohne eigenen Titel
        self.assertTrue(cat['1400']['bank'] and cat['2380']['bank'] and cat['2250']['bank'])
        for code in ('1420', '1370', '1500', '1600', '2600', '9100'):
            self.assertFalse(cat[code]['bank'], code)
        self.assertEqual(cat['8100']['note'], kr.ZMD)
        self.assertEqual(kr.group_of('4232'), ('4', 'Ausgaben des laufenden Geschäftsbetriebes'))
        self.assertEqual(kr.group_of('KV')[0], '4')


class LockTests(Base):
    def test_open_while_another_writer_holds_the_lock(self):
        import sqlite3
        import time
        path = Path(self.tmp.name) / 't.db'
        other = sqlite3.connect(path, isolation_level=None)
        other.execute('BEGIN IMMEDIATE')
        other.execute("INSERT INTO settings VALUES('sperre','1')")
        started = time.time()
        try:
            Store(path).db.close()                       # Grunddaten vorhanden: kein Schreiben beim Öffnen (Store je Anfrage)
        finally:
            other.execute('ROLLBACK')
            other.close()
        self.assertLess(time.time() - started, 1)


class BookingTests(Base):
    def test_balance_accounts_from_bank(self):
        self.book('-200', '1410')                                                       # Bargeld → Portokasse (Kasse 1400 nur mit Kassenbuch)
        self.book('500', '2250', day='2026-04-01')                                      # Darlehen erhalten
        rows = self.s.rows("SELECT account,kv,debit,credit,title FROM entries WHERE account IN ('1410','2250') ORDER BY account")
        self.assertEqual(rows, [{'account': '1410', 'kv': 'LV', 'debit': 20000, 'credit': 0, 'title': ''},
                                {'account': '2250', 'kv': 'LV', 'debit': 0, 'credit': 50000, 'title': ''}])
        values, unmatched, count, _ = haushalt.actuals(self.s, 2026)
        self.assertEqual((sum(values.values()), unmatched, count), (0, {'income': 0, 'expense': 0}, {'income': 0, 'expense': 0}))
        self.assertEqual(self.s.db.execute('SELECT SUM(debit-credit) FROM entries').fetchone()[0], 0)

    def test_system_and_wrong_accounts_rejected(self):
        bid = self.bank('-10')
        for code in ('1420', '1370', '1500', '1600', '2600', '9100', '9999'):
            with self.assertRaises(ValueError, msg=code):
                self.s.book(bid, [{'account': code, 'kv': 'LV', 'amount': '-10'}], 'Test', confirm_no_bill=True)
        with self.assertRaises(ValueError) as cm:                                       # Ausgabe auf Einnahmenkonto
            self.s.book(bid, [{'account': '8500', 'kv': 'LV', 'amount': '-10'}], 'Test', confirm_no_bill=True)
        self.assertIn('passendes Konto', str(cm.exception))
        with self.assertRaises(ValueError) as cm:                                       # Kontenrahmen-Konto ohne eigenen Titel
            self.s.book(bid, [{'account': '4232', 'kv': 'LV', 'amount': '-10'}], 'Test', confirm_no_bill=True)
        self.assertIn('Haushaltstitel', str(cm.exception))
        with self.assertRaises(ValueError):                                             # Einnahmentitel zu Ausgabenkonto
            self.s.book(bid, [{'account': '4232', 'kv': 'LV', 'title': 'H17', 'amount': '-10'}], 'Test', confirm_no_bill=True)
        self.s.book(bid, [{'account': '4232', 'kv': 'LV', 'title': 'H27', 'amount': '-10'}], 'Test', confirm_no_bill=True)
        self.assertEqual(haushalt.actuals(self.s, 2026)[0]['H27'], -1000)

    def test_configured_liability_account_is_system(self):
        self.s.save_settings({'liability_account': '2300', 'liability_name': 'Verbindlichkeiten aus Lieferungen und Leistungen'}, 'Test')
        bid = self.bank('-10')
        with self.assertRaises(ValueError) as cm:
            self.s.book(bid, [{'account': '2300', 'kv': 'LV', 'amount': '-10'}], 'Test', confirm_no_bill=True)
        self.assertIn('Eingangsrechnung', str(cm.exception))
        self.assertFalse({a['code']: a for a in kr.catalog(self.s)}['2300']['bank'])


class LedgerTests(Base):
    def test_ledger_by_date_with_opening_balance(self):
        self.book('100', '8500', day='2025-12-30', title='H17')
        self.book('-30', '1410', day='2026-01-05')
        self.book('-20', '4225', day='2026-02-01', kv='5100')
        led = kr.ledger(self.s, '1420', 2026)
        self.assertEqual((led['by_date'], led['opening'], led['debit'], led['credit'], led['closing']), (True, 10000, 0, 5000, 5000))
        self.assertEqual([r['balance'] for r in led['rows']], [7000, 5000])
        self.assertEqual(led['rows'][0]['kind'], 'Bank')
        income = kr.ledger(self.s, '8500', 2025)
        self.assertEqual((income['by_date'], income['opening'], income['closing']), (False, 0, -10000))   # Habensaldo
        self.assertEqual(kr.ledger(self.s, '8500', 2026)['rows'], [])
        self.assertEqual(len(kr.ledger(self.s, '4225', 2026, kv='5100')['rows']), 1)
        self.assertEqual(kr.ledger(self.s, '4225', 2026, kv='LV')['rows'], [])
        for bad in (('9998', 2026, None), ('1420', 1999, None), ('1420', 2026, 'XX')):
            with self.assertRaises(ValueError):
                kr.ledger(self.s, *bad)

    def test_account_list_and_csv(self):
        self.book('-30', '1410', day='2025-11-05')
        self.book('-20', '1410', day='2026-01-05')
        rows = {a['code']: a for a in kr.accounts(self.s, 2026)}
        self.assertEqual((rows['1410']['opening'], rows['1410']['debit'], rows['1410']['balance'], rows['1410']['count']), (3000, 2000, 5000, 1))
        self.assertEqual(rows['8100']['class_label'], kr.CLASSES['8'])
        self.assertTrue(rows['8100']['household'] and rows['8100']['titles'] == ['H4'])
        self.assertEqual(rows['1370']['system'], kr.SYSTEM['1370'])
        self.assertTrue(rows['1371']['ju_only'] and not rows['1410']['ju_only'])
        data = kr.ledger_csv(self.s, '1410', 2026)
        table = list(csv.reader(io.StringIO(data.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(table[0][-1], 'Saldo (Soll − Haben)')
        self.assertEqual((table[1][3], table[1][-1]), ('Anfangsbestand', '30,00'))
        self.assertEqual(table[2][-1], '50,00')
        self.assertEqual(kr.source_label('kvumbuchung:3/storno'), 'Storno Budget-Umbuchung')


if __name__ == '__main__':
    unittest.main()
