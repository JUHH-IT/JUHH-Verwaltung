"""Tests für Betragsformate und Kontoauszugsimport (nur erfundene Daten)."""
import tempfile
import unittest
from pathlib import Path

from bankimport import parse_statement, split_combined
from core import Store
from formate import cents_from_number, money

FIXTURES = Path(__file__).parent / 'tests' / 'fixtures'


class MoneyTests(unittest.TestCase):
    def test_german_and_english_formats(self):
        cases = {'1.500': 150000, '1.500,00': 150000, '12.000': 1200000, '1.234.567,89': 123456789,
                 '12,5': 1250, '12.50': 1250, '12.5': 1250, '30': 3000, '-1.234,56': -123456,
                 '1,234.56': 123456, '30,00-': -3000, '+5': 500, '−5,00': -500, '30,00 €': 3000,
                 '1 234,56': 123456, '0,01': 1}
        for text, cents in cases.items():
            with self.subTest(text=text):
                self.assertEqual(money(text), cents)

    def test_ambiguous_or_invalid_rejected(self):
        for text in ['1,234', '1.2345', '0,005', '', 'abc', '1,2,3', '--5', 'nan', 'inf', '1e5']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                money(text)

    def test_cents_from_cell_numbers(self):
        self.assertEqual(cents_from_number(19.99), 1999)
        self.assertEqual(cents_from_number(-1234.56), -123456)
        self.assertEqual(cents_from_number(12.5), 1250)
        with self.assertRaises(ValueError):
            cents_from_number(0.001)


class ParseTests(unittest.TestCase):
    def rows(self, name):
        return parse_statement((FIXTURES / name).read_bytes(), name)

    def test_xls_header_top_and_bottom_identical(self):
        top, bottom = self.rows('umsaetze-kopf-oben.xls'), self.rows('umsaetze-kopf-unten.xls')
        self.assertEqual(len(top), 6)
        key = lambda r: (r['booked'], r['amount'], r['name'], r['reference'])
        self.assertEqual(sorted(map(key, top)), sorted(map(key, bottom)))
        self.assertTrue(all('error' not in r for r in top + bottom))

    def test_xls_values(self):
        by_amount = {r['amount']: r for r in self.rows('umsaetze-kopf-unten.xls')}
        erika = by_amount[3000]
        self.assertEqual(erika['booked'], '2026-06-02')
        self.assertEqual(erika['name'], 'Erika Musterfrau')
        self.assertTrue(erika['reference'].startswith('ECHTZEITÜBERWEISUNG Erika Musterfrau DE02'))
        self.assertIsNone(erika['bank_ref'])
        self.assertEqual(by_amount[-123456]['name'], 'Druckerei Beispiel KG')
        self.assertEqual(by_amount[-3999]['name'], 'Muster Telekom GmbH')
        self.assertEqual(by_amount[-750]['booked'], '2026-06-05')

    def test_split_combined(self):
        self.assertEqual(split_combined('LASTSCHRIFT Firma AB GmbH DE89370400440532013000 Zweck 1'), 'Firma AB GmbH')
        self.assertEqual(split_combined('Gutschrift ohne Typ 2026'), 'Gutschrift ohne Typ')
        self.assertEqual(split_combined(''), '')

    def test_sparkasse_csv(self):
        raw = ('"Auftragskonto";"Buchungstag";"Valutadatum";"Buchungstext";"Verwendungszweck";'
               '"Beguenstigter/Zahlungspflichtiger";"Kontonummer/IBAN";"BIC (SWIFT-Code)";"Betrag";"Waehrung";"Info";"Kundenreferenz (End-to-End)"\n'
               '"DE00";"30.06.26";"01.07.26";"GUTSCHR";"Beitrag";"Max Test";"DE11";"X";"1.500,00";"EUR";"";"NOTPROVIDED"\n'
               '"DE00";"30.06.26";"30.06.26";"GUTSCHR";"Beitrag";"Max Test";"DE11";"X";"30,00";"USD";"";"NOTPROVIDED"\n'
               ).encode('cp1252')
        first, second = parse_statement(raw, 'spk.csv')
        self.assertEqual(first['booked'], '2026-06-30')   # Buchungstag hat Vorrang vor Valuta
        self.assertEqual(first['amount'], 150000)
        self.assertEqual(first['name'], 'Max Test')
        self.assertIsNone(first['bank_ref'])
        self.assertIn('EUR', second['error'])

    def test_csv_with_preamble_and_template(self):
        raw = 'Kontoauszug\nKonto;DE00\n\nDatum;Betrag;Name;Verwendungszweck;Bankreferenz\n01.06.2026;-12,50;A;B;R1\n'.encode()
        (row,) = parse_statement(raw, 'x.csv')
        self.assertEqual((row['row_no'], row['booked'], row['amount'], row['bank_ref']), (5, '2026-06-01', -1250, 'R1'))
        template = (Path(__file__).parent / 'Kontoauszug-Vorlage.csv').read_bytes()
        self.assertEqual([r['amount'] for r in parse_statement(template, 'v.csv')], [3000, -1250])

    def test_unknown_formats(self):
        with self.assertRaises(ValueError):
            parse_statement(b'PK\x03\x04', 'x.xlsx')
        with self.assertRaises(ValueError):
            parse_statement('a;b\n1;2\n'.encode(), 'x.csv')


class StoreImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_xls_import_duplicates_and_overlap(self):
        raw = (FIXTURES / 'umsaetze-kopf-oben.xls').read_bytes()
        preview = self.s.preview_bank(raw, 'a.xls')
        selected = [r['row_no'] for r in preview['rows'] if r['selected']]
        self.assertEqual(len(selected), 5)                      # identische Zeile zur Prüfung abgewählt
        self.assertEqual(self.s.import_bank(preview, selected, 'Test'), 5)
        again = self.s.preview_bank((FIXTURES / 'umsaetze-kopf-unten.xls').read_bytes(), 'b.xls')
        self.assertFalse(any(r['selected'] for r in again['rows']))   # überlappender Export: alles markiert
        with self.assertRaises(ValueError):
            self.s.preview_bank(raw, 'a.xls')                       # gleiche Datei

    def test_learning_without_name_column(self):
        raw = ('Datum;Betrag;Verwendungszweck\n'
               '01.06.2026;-39,99;LASTSCHRIFT Muster Telekom GmbH 11111 Rechnung 06/2026\n'
               '01.07.2026;-39,99;LASTSCHRIFT Muster Telekom GmbH 22222 Rechnung 07/2026\n').encode()
        preview = self.s.preview_bank(raw, 'c.csv')
        self.s.import_bank(preview, [2, 3], 'Test')
        first, second = [r['id'] for r in self.s.rows('SELECT id FROM bank ORDER BY booked')]
        parts = [{'amount': '-39,99', 'account': '4232', 'kv': 'LV', 'title': 'H27'}]
        self.s.book(first, parts, 'Test', learn=True)
        import workflow
        (suggestion,) = workflow.suggest(self.s, second)
        self.assertEqual(suggestion['parts'][0]['account'], '4232')


if __name__ == '__main__':
    unittest.main(verbosity=2)
