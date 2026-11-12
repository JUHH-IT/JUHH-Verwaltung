"""Abgleich der KV-Budgets mit der Masterdatei – erfundene Arbeitsmappe im Aufbau der echten KV-Blätter."""
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import kvabgleich as a
import kvbudget as k
from core import Store


def workbook(verfall=583.17, extra=True):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Haushalt  GuV'
    sheet = wb.create_sheet('KV Altona 5100')
    rows = [['KV Altona / Elbvororte'], [], ['Stand'], [], ['Beitrag FFA', 100], ['Budgetstart 2024', 821],
            ['Bezeichnung', 'Kategorie', 'Datum', 'Betrag', 'Empfänger'],
            ['EB-Wert 2024', 'EB-Wert', datetime(2024, 1, 1), 921],
            ['Stammtisch', 'Aufwandserstattung', datetime(2024, 3, 1), -100, 'Erika Beispiel'],
            ['Tippfehler im Jahr', 'IT', datetime(2025, 3, 15), -8.5],                     # steht im Block 2024: zählt nicht
            ['SB -Wert 2024', '', datetime(2024, 12, 31), 591.6699999999998], [],          # berechnete Zelle
            ['Beitrag FFA', 200], ['Budget MB 2025', 594.99],
            ['Bezeichnung', 'Kategorie', 'Datum', 'Betrag', 'Empfänger'],
            ['EB-Wert 2025', 'EB-Wert', datetime(2025, 1, 1), 1386.66],
            ['Webhosting', 'IT', datetime(2025, 1, 15), -8.5, 'Hoster Beispiel'],
            ['Verfall Budget 2024 gemäß §6 Abschnitt 3 FO', '', datetime(2025, 1, 31), verfall],
            ['Hamburgtag Getränke', 'Aufwandserstattung', datetime(2025, 4, 12), -240, 'Landesverband'],
            ['Hamburgtag Getränke', 'Aufwandserstattung', datetime(2025, 4, 12), 0],          # Platzhalter
            ['Spende Sommerfest', 'Spende', datetime(2025, 9, 2), 200]]
    if extra:
        rows.append(['Nur in der Master', 'Aufwandserstattung', datetime(2025, 5, 5), -33.4, 'Max Beispiel'])
    rows.append([None, None, None, 1234.56])
    for r in rows:
        sheet.append(r)
    su = wb.create_sheet('Schüler Union 5800')
    for r in [['Schüler Union'], [], [], [], ['Beitrag FFA', 0], ['Budget MB 2025', 350],
              ['Bezeichnung', 'Kategorie', 'Datum', 'Betrag', 'Empfänger'],
              ['EB-Wert 2025', 'EB-Wert', datetime(2025, 1, 1), 334.82],
              ['Spende Schulfest', 'Spende', datetime(2025, 2, 5), 1400]]:
        su.append(r)
    wb.create_sheet('KV Nord 5600').append(['leer'])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class AbgleichTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00', 'fo_start': '2025'}, 'Test')
        self.n = 0
        self.book('-8,50', '2025-01-15', 'Hoster Beispiel', 'EREF+R-2025-0117 SVWZ+Webhosting Januar')
        self.drinks = self.book('-240', '2025-04-14', 'Landesverband JU', 'Getraenke Hamburgtag')   # zwei Tage später gebucht
        self.book('200', '2025-09-02', 'Spender Beispiel', 'Spende Sommerfest', account='8200', title='H5')
        self.book('-32,30', '2025-06-01', 'Anderer Beispiel', 'nur im Programm')
        with self.s.db:
            self.s.journal('2025-02-05', 'Gutschrift Schulfest', 'test:1', [{'account': '1420', 'kv': 'LV', 'debit': 140000, 'credit': 0},
                                                                           {'account': 'KV', 'kv': '5800', 'debit': 0, 'credit': 140000, 'title': 'H57'}])

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def book(self, amount, day, name, ref, account='4430', title='H32', kv='5100'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};{name};{ref};k{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'k{self.n}.csv'), [2], 'Test')
        bid = self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
        self.s.book(bid, [{'account': account, 'kv': kv, 'title': title, 'amount': amount}], 'Test', confirm_no_bill=True)
        return self.s.db.execute("SELECT id FROM journals WHERE source=?", (f'bank:{bid}',)).fetchone()[0]

    def test_parse_blocks(self):
        parsed, notes = a.parse(workbook())
        self.assertEqual(sorted(parsed), ['5100', '5800'])
        self.assertTrue(any('KV Nord 5600' in n for n in notes))
        b24, b25 = parsed['5100']['blocks']
        self.assertEqual((b24['year'], b24['ffk'], b24['mb'], b24['sb']), (2024, 10000, 82100, 59167))
        self.assertEqual((b25['year'], b25['ffk'], b25['mb'], b25['eb'], b25['total']), (2025, 20000, 59499, 138666, 123456))
        self.assertEqual([r['amount'] for r in b25['rows']], [-850, -24000, 20000, -3340])      # Platzhalter 0 und Verfall nicht
        self.assertEqual(b25['verfall'][0]['amount'], 58317)
        with self.assertRaises(ValueError):
            a.parse(b'kein excel')
        self.assertEqual((a._num(1068.9299999999998), a._num(-0.005), a._num(float('nan')), a._num(True)), (106893, -1, None, None))

    def test_preview_compares_with_same_rule(self):
        p = a.preview(self.s, workbook(), 'Master.xlsx', today='2025-12-31')
        altona = next(x for x in p['areas'] if x['code'] == '5100')
        self.assertEqual(altona['values'][0]['year'], 2025)
        self.assertEqual({k: altona['values'][0][k] for k in ('ffk', 'dues_share', 'opening')}, {'ffk': 20000, 'dues_share': 59499, 'opening': 59167})
        self.assertEqual((altona['matched'], altona['labels'], altona['values_changed']), (3, 2, 3))   # Spende: Text schon gleich
        self.assertEqual([(m['day'], m['amount']) for m in altona['master_only']], [('2025-05-05', -3340)])
        self.assertEqual([(m['day'], m['amount']) for m in altona['program_only']], [('2025-06-01', -3230)])
        self.assertEqual(altona['lapses'], [{'year': 2025, 'program': -58317, 'master': -58317, 'stated': 58317}])
        self.assertTrue(any('Vorzeichen' in n for n in altona['notes']))
        self.assertEqual(altona['program_balance'] - altona['master_balance'], -3230 + 3340)    # erklärt durch die zwei Zeilen
        su = next(x for x in p['areas'] if x['code'] == '5800')
        self.assertEqual(su['values'][0]['opening'], -1518)                                      # EB − FFK − Budget
        self.assertEqual((su['matched'], su['master_only'], su['program_only']), (1, [], []))
        self.assertEqual(k.year_values(self.s), {})                                              # Vorschau ändert nichts

    def test_execute_values_and_labels(self):
        counts = a.execute(self.s, workbook(), 'Test')
        self.assertEqual(counts, {'jahreswerte': 2, 'bezeichnungen': 3})
        values = k.year_values(self.s)
        self.assertEqual((values[(2025, '5100')]['ffk'], values[(2025, '5100')]['dues_share'], values[(2025, '5100')]['opening']), (20000, 59499, 59167))
        self.assertEqual(values[(2025, '5800')]['dues_share'], 35000)
        led = k.ledger(self.s, '5100', today='2025-12-31')
        drinks = next(x for x in led['lines'] if x.get('journal') == self.drinks)
        self.assertEqual((drinks['label'], drinks['recipient'], drinks['auto']), ('Hamburgtag Getränke', 'Landesverband', False))
        self.assertEqual(led['years'][0]['lapsed'], 58317)
        k.set_label(self.s, self.drinks, 'Getränke Hamburg-Tag (eigene Bezeichnung)', '', 'Test')
        self.assertEqual(a.execute(self.s, workbook(), 'Test'), {'jahreswerte': 0, 'bezeichnungen': 0})   # nichts doppelt, Handeingabe bleibt
        self.assertEqual(next(x for x in k.ledger(self.s, '5100', today='2025-12-31')['lines'] if x.get('journal') == self.drinks)['label'],
                         'Getränke Hamburg-Tag (eigene Bezeichnung)')
        actions = [r[0] for r in self.s.db.execute('SELECT action FROM audit')]
        self.assertIn('KV-Budgets mit Masterdatei abgeglichen', actions)

    def test_start_year_hint(self):
        self.s.save_settings({'fo_start': '2026'}, 'Test')
        p = a.preview(self.s, workbook(), 'Master.xlsx', today='2026-03-01')
        self.assertTrue(any('ab Haushaltsjahr' in n for n in p['notes']))


class ReviewTests(AbgleichTests):
    """Befunde der unabhängigen Prüfung: fehlende Werte, unklare Treffer, lange Texte, ungültige Werte."""

    def book_on(self, *args, **kwargs):
        return self.book(*args, **kwargs)

    def sheet(self, rows):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'KV Bergedorf 5200'
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_missing_mb_keeps_program_value_and_order_does_not_matter(self):
        raw = self.sheet([['Budget MB 2025', 99.17], ['Beitrag FFA', 100], ['Bezeichnung', 'Kategorie', 'Datum', 'Betrag', 'Empfänger'],
                          ['EB-Wert 2025', 'EB-Wert', datetime(2025, 1, 1), 199.17], [],
                          ['Beitrag FFA', 50], ['Bezeichnung', 'Kategorie', 'Datum', 'Betrag', 'Empfänger'],
                          ['Ausgabe', 'IT', datetime(2026, 2, 1), -5]])
        k.set_values(self.s, 2026, '5200', {'dues_share': '12'}, 'Test')
        p = a.preview(self.s, raw, 'm.xlsx', today='2026-03-01')
        area = p['areas'][0]
        self.assertEqual([(v['year'], v['ffk'], v['dues_share']) for v in area['values']], [(2025, 10000, 9917), (2026, 5000, None)])
        self.assertTrue(any('Budget MB“ fehlt' in n for n in area['notes']))
        a.execute(self.s, raw, 'Test')
        self.assertEqual(k.year_values(self.s)[(2026, '5200')]['dues_share'], 1200)          # nicht auf 0 gesetzt

    def test_ambiguous_matches_keep_labels(self):
        anna = self.book('-50', '2025-03-10', 'Anna Beispiel', 'Erstattung', kv='5200')
        bernd = self.book('-50', '2025-03-10', 'Bernd Beispiel', 'Erstattung', kv='5200')
        right = self.book('-100', '2025-04-05', 'Pizzeria Beispiel', 'Vorstand', kv='5200')
        raw = self.sheet([['Beitrag FFA', 10], ['Budget MB 2025', 0], ['Bezeichnung', 'Kategorie', 'Datum', 'Betrag', 'Empfänger'],
                          ['Flyer', 'Aufwandserstattung', datetime(2025, 3, 10), -50, 'Bernd Beispiel'],
                          ['Pizza Stammtisch', 'Aufwandserstattung', datetime(2025, 3, 10), -50, 'Anna Beispiel'],
                          ['Pizza Vorstand', 'Haushalt', datetime(2025, 4, 1), -100, 'Andere Pizzeria'],
                          ['Pizza Vorstand richtig', 'Haushalt', datetime(2025, 4, 6), -100, 'Pizzeria Beispiel']])
        area = a.preview(self.s, raw, 'm.xlsx', today='2025-12-31')['areas'][0]
        self.assertEqual((area['matched'], area['unsure']), (3, 2))
        self.assertEqual([m['label'] for m in area['master_only']], ['Pizza Vorstand'])
        a.execute(self.s, raw, 'Test')
        labels = {x['journal']: x for x in k.ledger(self.s, '5200', today='2025-12-31')['lines'] if x.get('journal')}
        self.assertTrue(labels[anna]['auto'] and labels[bernd]['auto'])                       # nicht vertauscht
        self.assertEqual((labels[right]['label'], labels[right]['recipient']), ('Pizza Vorstand richtig', 'Pizzeria Beispiel'))

    def test_long_labels_and_invalid_values(self):
        jid = self.book('-7', '2025-05-05', 'Beispiel', 'Text', kv='5200')
        raw = self.sheet([['Beitrag FFA', -50], ['Budget MB 2025', 10], ['Bezeichnung', 'Kategorie', 'Datum', 'Betrag', 'Empfänger'],
                          ['Sehr lange Bezeichnung ' * 6, 'IT', datetime(2025, 5, 5), -7]])
        area = a.preview(self.s, raw, 'm.xlsx', today='2025-12-31')['areas'][0]
        self.assertEqual(area['values'], [])
        self.assertTrue(any('positiven Betrag' in n for n in area['notes']))
        self.assertEqual(a.execute(self.s, raw, 'Test'), {'jahreswerte': 0, 'bezeichnungen': 1})
        self.assertEqual(a.execute(self.s, raw, 'Test'), {'jahreswerte': 0, 'bezeichnungen': 0})   # wird fertig
        self.assertLessEqual(len(next(x for x in k.ledger(self.s, '5200', today='2025-12-31')['lines'] if x.get('journal') == jid)['label']), 80)


if __name__ == '__main__':
    unittest.main()
