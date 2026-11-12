"""OP-Listen der Mitgliedsbeiträge je Kreisverband – nur erfundene Testdaten."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import gliederung
import opliste
from core import Store

IBAN = 'DE89 3704 0044 0532 0130 00'
BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg')
DAY = '2026-06-01'


class OpListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1\n20000 Hamburg', 'iban': IBAN, 'age_basis': 'jan1'}, 'Test')
        with self.s.db:
            self.s.db.execute("INSERT INTO settings VALUES('data_start','2025-01-01')")
        self.ov = gliederung.save_ov(self.s, {'kv': '5100', 'number': '11', 'name': 'Testort'}, 'Test')
        add = lambda number, first, kv, **x: self.s.member({**BASE, 'number': number, 'first_name': first, 'last_name': 'Test', 'kv': kv,   # noqa: E731
                                                            'birth': '1995-03-01', 'joined': '2020-01-01', **x}, 'Test')
        self.a = add('T1', 'Anna', '5100', ov_id=self.ov, email='anna@example.org', mobile='0170 1')   # 2025 und 2026 offen (je 30 €)
        self.b = add('T2', 'Bernd', '5100')                                                             # 2025 bezahlt, 2026 offen
        self.c = add('T3', 'Clara', '5300', left_on='2026-03-31')                                       # ausgetreten 2026: 2025 offen, 2026 zu klären
        self.d = add('T4', 'Dora', '5300', guest='J')                                                   # Gast: nichts offen
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def pay(self, member, amount, day):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};Zahler;Beitrag;k{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'k{self.n}.csv'), [2], 'Test')
        bid = self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': amount, 'members': [{'member_id': member}]}], 'Test')

    def test_items_per_kv_and_ov(self):
        self.pay(self.b, '30', '2025-02-01')
        r = opliste.items(self.s, today=DAY)
        self.assertEqual([(x['number'], [(y['year'], y['open']) for y in x['years']]) for x in r['members']],
                         [('T1', [(2025, 3000), (2026, 3000)]), ('T2', [(2026, 3000)]), ('T3', [(2025, 3000)])])
        self.assertEqual((r['count'], r['total'], r['by_year']), (3, 12000, [{'year': 2025, 'open': 6000}, {'year': 2026, 'open': 6000}]))
        self.assertEqual([(k['code'], k['count'], k['open']) for k in r['by_kv']], [('5100', 2, 9000), ('5300', 1, 3000)])
        anna = r['members'][0]
        self.assertEqual((anna['ov'], anna['email'], anna['phone']), ('11 Testort', 'anna@example.org', '0170 1'))
        self.assertEqual((r['members'][2]['left_on'], r['unclear']), ('2026-03-31', 1))
        self.assertEqual([x['number'] for x in opliste.items(self.s, '5300', today=DAY)['members']], ['T3'])
        self.assertEqual([x['number'] for x in opliste.items(self.s, '5100', str(self.ov), today=DAY)['members']], ['T1'])
        self.assertEqual([x['number'] for x in opliste.items(self.s, '5100', 'none', today=DAY)['members']], ['T2'])
        for kv, ov in (('9999', None), ('5300', str(self.ov)), (None, 'x'), (None, '999999')):
            with self.assertRaises(ValueError):
                opliste.items(self.s, kv, ov, today=DAY)

    def test_pdf_csv_zip(self):
        self.assertTrue(opliste.pdf(self.s, '5100', today=DAY).startswith(b'%PDF'))
        self.assertTrue(opliste.pdf(self.s, None, today=DAY).startswith(b'%PDF'))
        self.assertTrue(opliste.pdf(self.s, '5700', today=DAY).startswith(b'%PDF'))     # ohne Mitglieder: leere Liste
        text = opliste.csv(self.s, '5100', today=DAY).decode('utf-8-sig')
        lines = text.splitlines()
        self.assertEqual(len(lines), 5)                                                    # Kopf + Anna 2025/2026 + Bernd 2025/2026
        self.assertIn('Beitragsjahr', lines[0])
        self.assertNotIn('Clara', text)
        with zipfile.ZipFile(io.BytesIO(opliste.zip_all(self.s, DAY))) as z:
            names = sorted(z.namelist())
        self.assertEqual([n.split('-')[2] for n in names], ['5100', '5100', '5300', '5300'])
        self.assertTrue(all(n.endswith(('.pdf', '.csv')) for n in names))


if __name__ == '__main__':
    unittest.main()
