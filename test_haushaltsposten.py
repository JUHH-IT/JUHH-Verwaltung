"""Haushaltsposten: Titel ergibt sich aus dem Konto; Kontenbezeichnungen wie im Haushalt (Entscheidung 29.10.2026)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import haushalt
import verbindlichkeiten
from core import Store


class PostenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 't.db'
        self.s = Store(self.path)

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, amount, ref):
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-05-02;{amount};Test;{ref};{ref}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, ref + '.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def test_default_title(self):
        self.assertEqual(haushalt.default_title('4473'), 'H43')
        self.assertEqual(haushalt.default_title('8590'), 'H16')
        self.assertEqual(haushalt.default_title('KV', '5500'), 'H54')
        self.assertIsNone(haushalt.default_title('KV', 'LV'))
        self.assertIsNone(haushalt.default_title('7200'))                           # zwei Titel → Auswahl nötig
        self.assertIsNone(haushalt.default_title('4450'))                           # Altkonto ohne Titel

    def test_account_names_follow_household_once(self):
        names = dict(self.s.db.execute('SELECT code,name FROM accounts'))
        self.assertEqual((names['8590'], names['8500'], names['4210'], names['4360']),
                         ('Unterstützung Haspa Marathon', 'Sonstige Veranstaltungseinnahmen', 'Bürobedarf / Telefon / EDV', 'Landesdelegiertenversammlung'))
        self.assertEqual(names['7200'], 'Wahlkampf Kandidaten – Abruf Spenden')     # mehrdeutig: unverändert
        audit = [r[0] for r in self.s.db.execute("SELECT action FROM audit")]
        self.assertEqual(audit.count('Kontenbezeichnungen an den Haushalt angepasst'), 1)
        self.s.db.execute("UPDATE accounts SET name='Eigener Name' WHERE code='4210'")
        self.s.db.commit()
        self.s.db.close()
        self.s = Store(self.path)                                                   # Neustart: nur einmal angepasst
        self.assertEqual(self.s.db.execute("SELECT name FROM accounts WHERE code='4210'").fetchone()[0], 'Eigener Name')

    def test_old_database_renamed(self):
        self.s.db.execute("DELETE FROM settings WHERE key='account_names_household'")
        self.s.db.execute("UPDATE accounts SET name='Sonstige Veranstaltungseinnahmen' WHERE code='8590'")
        self.s.db.commit()
        changed = haushalt.sync_account_names(self.s.db)
        self.assertEqual(changed, [{'konto': '8590', 'vorher': 'Sonstige Veranstaltungseinnahmen', 'jetzt': 'Unterstützung Haspa Marathon'}])

    def test_booking_without_title(self):
        bid = self.bank('-20', 'a1')
        self.s.book(bid, [{'account': '4473', 'kv': 'LV', 'amount': '-20'}], 'Test', confirm_no_bill=True)
        self.assertEqual(self.s.db.execute("SELECT title FROM entries WHERE account='4473'").fetchone()[0], 'H43')
        bid = self.bank('-5', 'a2')
        with self.assertRaises(ValueError) as cm:
            self.s.book(bid, [{'account': 'KV', 'kv': 'LV', 'amount': '-5'}], 'Test', confirm_no_bill=True)
        self.assertIn('Sammelkonto KV', str(cm.exception))
        with self.assertRaises(ValueError):
            self.s.book(bid, [{'account': '7200', 'kv': 'LV', 'amount': '-5'}], 'Test', confirm_no_bill=True)   # Titel nötig
        self.s.book(bid, [{'account': '7200', 'kv': 'LV', 'amount': '-5', 'title': 'H61'}], 'Test', confirm_no_bill=True)
        self.s.book(self.bank('-7', 'a3'), [{'account': 'KV', 'kv': '5500', 'amount': '-7', 'title': 'H50'}], 'Test', confirm_no_bill=True, learn=True)
        self.assertEqual(self.s.db.execute("SELECT title FROM entries WHERE account='KV'").fetchone()[0], 'H54')   # KV-Zeile, nicht der fremde Titel
        bid = self.bank('-3', 'a4')
        self.s.book(bid, [{'account': '4473', 'kv': 'LV', 'amount': '-3'}], 'Test', confirm_no_bill=True, learn=True)   # Lernbeispiel ohne Titel
        self.assertIn('"H43"', self.s.db.execute('SELECT parts FROM booking_examples WHERE bank_id=?', (bid,)).fetchone()[0])

    def test_bill_line_without_title(self):
        bid, number, _ = verbindlichkeiten.create(self.s, {'creditor': 'Druckerei Beispiel', 'iban': '', 'ext_number': 'X-1', 'issued': '2026-05-01',
                                                           'due': '', 'kv': 'LV', 'lines': [{'description': 'Flyer', 'amount': '10', 'account': '4290'}]}, 'Test')
        self.assertEqual(self.s.db.execute('SELECT title FROM bill_lines WHERE bill_id=?', (bid,)).fetchone()[0], 'H37')


if __name__ == '__main__':
    unittest.main()
