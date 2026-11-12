"""Kassenbuch (kasse.py): Kassenbelege wie Bankumsätze verbuchen – nur erfundene Testdaten."""
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import beitraege
import berichte
import haushalt
import kasse
import mitgliederkonto
import workflow
from core import Store

YEAR = date.today().year
TODAY = date.today().isoformat()
START = f'{YEAR}-01-01'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def day(self, offset):
        """Tag im laufenden Jahr, nie in der Zukunft."""
        return min((date.fromisoformat(START) + timedelta(days=offset)).isoformat(), TODAY)

    def entry(self, kind, amount, offset=1, text='Testbeleg', name=''):
        return kasse.create(self.s, {'kind': kind, 'amount': amount, 'day': self.day(offset), 'text': text, 'name': name}, 'Test')

    def bank(self, amount, day, ref='x'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};Test;{ref};k{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'k{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]


class CashTests(Base):
    def test_setup_opening_and_balance(self):
        with self.assertRaises(ValueError) as cm:
            self.entry('out', '5')
        self.assertIn('Anfangsbestand', str(cm.exception))
        kasse.set_opening(self.s, START, '250', 'Test')
        self.assertEqual((kasse.start(self.s), kasse.balance(self.s), kasse.opening(self.s)['amount']), (START, 25000, 25000))
        totals = berichte._totals(haushalt.report(self.s, YEAR))
        self.assertEqual((totals['income'][3] or 0, totals['expense'][3] or 0), (0, 0))   # zählt nicht im Haushalt
        kasse.set_opening(self.s, START, '200', 'Test')                                  # ändern: alte Buchung gegengebucht
        self.assertEqual((kasse.balance(self.s), kasse.opening(self.s)['amount']), (20000, 20000))
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM journals WHERE source LIKE 'kasse-anfang:%'").fetchone()[0], 3)
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)
        with self.assertRaises(ValueError):
            kasse.set_opening(self.s, START, '-1', 'Test')

    def test_book_like_bank_with_member_and_budget(self):
        kasse.set_opening(self.s, START, '100', 'Test')
        out = self.entry('out', '12,50', text='Druckerpatrone')
        row = self.s.rows('SELECT * FROM bank WHERE id=?', (out['id'],))[0]
        self.assertEqual((row['ledger'], row['state'], row['amount'], row['bank_ref']), ('cash', 'open', -1250, f'K-{YEAR}-0001'))
        self.assertEqual(kasse.balance(self.s), 8750)                                   # offener Beleg zählt schon
        with self.assertRaises(ValueError):
            self.s.book(out['id'], [{'amount': '-12,50', 'account': '1400', 'kv': 'LV'}], 'Test')   # nicht auf sich selbst
        self.s.book(out['id'], [{'amount': '-12,50', 'account': '4210', 'kv': '5100'}], 'Test')
        lines = self.s.rows("SELECT e.account,e.kv,e.debit,e.credit FROM entries e JOIN journals j ON j.id=e.journal_id WHERE j.source=? ORDER BY e.id",
                            (f"bank:{out['id']}",))
        self.assertEqual(lines, [{'account': '1400', 'kv': 'LV', 'debit': 0, 'credit': 1250}, {'account': '4210', 'kv': '5100', 'debit': 1250, 'credit': 0}])
        self.assertEqual(kasse.balance(self.s), 8750)
        # Mitgliedsbeitrag bar: Mitglied Pflicht, zählt im Beitragskonto
        mid = self.s.member(dict(number='M1', first_name='Erika', last_name='Muster', street='Weg 1', zip='20095', city='Hamburg',
                                 birth='1995-01-01', joined='2020-01-01', kv='5100'), 'Test')
        inc = self.entry('in', '30', text='Beitrag bar', name='Erika Muster')
        with self.assertRaises(ValueError):
            self.s.book(inc['id'], [{'amount': '30', 'account': '8100', 'kv': 'LV'}], 'Test')
        self.s.book(inc['id'], [{'amount': '30', 'account': '8100', 'kv': 'LV', 'members': [{'member_id': mid}]}], 'Test')
        self.assertEqual(beitraege.by_member(self.s, mid)[0]['amount'], 3000)
        self.assertEqual({r['year']: r['paid'] for r in mitgliederkonto.account(self.s, mid)['years']}[YEAR], 3000)
        self.assertEqual(kasse.balance(self.s), 11750)
        book = kasse.book(self.s, YEAR)
        self.assertEqual([x['kind'] for x in book['lines']], ['anfang', 'beleg', 'beleg'])
        self.assertEqual((book['income'], book['expense'], book['end']), (3000, 1250, 11750))
        line = next(x for x in book['lines'] if x['number'] == out['number'])
        self.assertEqual([(p['account'], p['kv'], p['amount']) for p in line['posted']], [('4210', '5100', -1250)])

    def test_never_negative_dates_and_void(self):
        kasse.set_opening(self.s, START, '20', 'Test')
        with self.assertRaises(ValueError) as cm:
            self.entry('out', '25')
        self.assertIn('negativ', str(cm.exception))
        with self.assertRaises(ValueError):
            kasse.create(self.s, {'kind': 'out', 'amount': '1', 'day': f'{YEAR - 1}-12-31', 'text': 'x'}, 'Test')    # vor Beginn
        with self.assertRaises(ValueError):
            kasse.create(self.s, {'kind': 'out', 'amount': '1', 'day': (date.today() + timedelta(days=1)).isoformat(), 'text': 'x'}, 'Test')
        with self.assertRaises(ValueError):
            kasse.create(self.s, {'kind': 'out', 'amount': '1', 'day': TODAY, 'text': ''}, 'Test')                   # Text Pflicht
        inc = self.entry('in', '10', offset=0)
        out = self.entry('out', '25', offset=0)                                        # gleicher Tag: Eingang zuerst
        with self.assertRaises(ValueError):
            kasse.void(self.s, inc['id'], 'Tippfehler', 'Test')                         # sonst negativ
        kasse.void(self.s, out['id'], 'doppelt erfasst', 'Test')
        self.assertEqual(kasse.balance(self.s), 3000)
        self.assertEqual(kasse.book(self.s, YEAR)['void'][0]['note'], 'doppelt erfasst')
        self.s.book(inc['id'], [{'amount': '10', 'account': '8590', 'kv': 'LV'}], 'Test')
        with self.assertRaises(ValueError):
            kasse.void(self.s, inc['id'], 'x', 'Test')                                   # verbucht: erst stornieren
        self.assertEqual(self.entry('out', '1')['number'], f'K-{YEAR}-0003')

    def test_transit_with_bank_and_park_and_reverse(self):
        kasse.set_opening(self.s, START, '0', 'Test')
        w = self.entry('withdraw', '100', text='')
        self.assertEqual(self.s.rows('SELECT state,reference,source FROM bank WHERE id=?', (w['id'],))[0],
                         {'state': 'booked', 'reference': 'Abhebung von der Bank', 'source': 'Kassenbuch · Abhebung von der Bank'})
        self.assertEqual((kasse.balance(self.s), kasse.transit_open(self.s)), (10000, -10000))
        bid = self.bank('-100', self.day(1), 'Barabhebung')
        self.s.book(bid, [{'amount': '-100', 'account': '2380', 'kv': 'LV'}], 'Test')
        self.assertEqual(kasse.transit_open(self.s), 0)
        # Kasse auf die Bank: im Kontoauszug direkt auf 1400 gebucht → zählt im Kassenbuch; zu viel → negativ
        dep = self.bank('150', self.day(2), 'Bareinzahlung')
        with self.assertRaises(ValueError):
            self.s.book(dep, [{'amount': '150', 'account': '1400', 'kv': 'LV'}], 'Test')
        self.assertEqual(self.s.rows('SELECT state FROM bank WHERE id=?', (dep,))[0]['state'], 'open')   # zurückgerollt
        out = self.entry('out', '40', offset=3, text='Getränke')
        workflow.park(self.s, out['id'], 'Beleg fehlt', 'Test')
        self.assertEqual(self.s.rows("SELECT e.account FROM entries e JOIN journals j ON j.id=e.journal_id WHERE j.source=? ORDER BY e.id",
                                     (f"bank:{out['id']}",)), [{'account': '1400'}, {'account': '1370'}])
        self.assertEqual(kasse.balance(self.s), 6000)
        self.assertEqual([b['id'] for b in workflow.details(self.s, YEAR)['parked'] if b['ledger'] == 'cash'], [out['id']])
        self.s.book(out['id'], [{'amount': '-40', 'account': '4360', 'kv': 'LV'}], 'Test')   # Parkkonto aufgelöst
        self.s.reverse_bank(out['id'], 'Test')                                         # Storno → wieder offen, Bestand gleich
        self.assertEqual((kasse.balance(self.s), self.s.rows('SELECT state FROM bank WHERE id=?', (out['id'],))[0]['state']), (6000, 'open'))
        lines = kasse.book(self.s, YEAR)['lines']
        self.assertTrue(any(x['storno'] for x in lines))
        self.assertEqual(lines[-1]['balance'], 6000)

    def test_opening_after_entries_and_bank_import_unaffected(self):
        kasse.set_opening(self.s, START, '50', 'Test')
        self.entry('out', '5', offset=2)
        later = self.day(5)
        if later > self.day(2):
            with self.assertRaises(ValueError):
                kasse.set_opening(self.s, later, '50', 'Test')                          # Belege vor dem neuen Beginn
        # Belegnummer stört den Kontoauszug-Import nicht
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{self.day(1)};5;Test;x;K-{YEAR}-0001\n'.encode()
        p = self.s.preview_bank(raw, 'a.csv')
        self.assertFalse(p['rows'][0].get('duplicate'))

    def test_book_totals_after_corrections(self):
        """Prüfbefund: Kopf geht auf (Übertrag + Anfangsbestand + Einnahmen − Ausgaben = Bestand), Storno-Runden netto, Storno unter Original."""
        kasse.set_opening(self.s, START, '250', 'Test')
        kasse.set_opening(self.s, START, '200', 'Test')
        inc = self.entry('in', '30', offset=1, text='Bar')
        self.s.book(inc['id'], [{'amount': '30', 'account': '8590', 'kv': 'LV'}], 'Test')
        self.s.reverse_bank(inc['id'], 'Test')
        self.s.book(inc['id'], [{'amount': '30', 'account': '8590', 'kv': 'LV'}], 'Test')
        out = self.entry('out', '60', offset=1, text='Saal')
        workflow.park(self.s, out['id'], 'Beleg fehlt', 'Test')
        self.s.book(out['id'], [{'amount': '-60', 'account': '4360', 'kv': 'LV'}], 'Test')
        b = kasse.book(self.s, YEAR)
        self.assertEqual((b['opening_in_year'], b['income'], b['expense'], b['end']), (20000, 3000, 6000, 17000))
        self.assertEqual(b['carry'] + b['opening_in_year'] + b['income'] - b['expense'], b['end'])
        mine = [x for x in b['lines'] if x['number'] == inc['number']]
        self.assertEqual([(x['amount'], x['state']) for x in mine], [(3000, 'reversed'), (-3000, 'storno'), (3000, 'booked')])
        parked = [x for x in b['lines'] if x['number'] == out['number']][0]
        self.assertEqual([(p['account'], p['amount']) for p in parked['posted']], [('4360', -6000)])   # Kontierung nach dem Auflösen
        self.assertTrue(all(x['balance'] >= 0 for x in b['lines']))

    def test_transit_is_atomic_and_bank_to_cash_needs_cash_book(self):
        bid = self.bank('-50', self.day(1), 'Bargeld')
        with self.assertRaises(ValueError) as cm:                                     # Kasse noch nicht eingerichtet
            self.s.book(bid, [{'amount': '-50', 'account': '1400', 'kv': 'LV'}], 'Test')
        self.assertIn('Kasse einrichten', str(cm.exception))
        kasse.set_opening(self.s, self.day(2), '0', 'Test')
        with self.assertRaises(ValueError) as cm:                                     # vor Beginn des Kassenbuchs
            self.s.book(bid, [{'amount': '-50', 'account': '1400', 'kv': 'LV'}], 'Test')
        self.assertIn('Beginn des Kassenbuchs', str(cm.exception))
        # Einzahlung mit Rechnungsnummer einer offenen Eingangsrechnung: wird trotzdem in einem Schritt gegen 2380 gebucht
        import verbindlichkeiten
        verbindlichkeiten.create(self.s, {'kind': 'external', 'creditor': 'Druckerei Test', 'ext_number': 'RE-4711', 'issued': self.day(0),
                                          'iban': '', 'lines': [{'description': 'Flyer', 'amount': '20', 'account': '4430', 'title': 'H32'}], 'kv': 'LV'}, 'Test')
        self.entry('withdraw', '20', offset=2, text='')
        dep = self.entry('deposit', '20', offset=2, text='Einzahlung RE-4711')
        self.assertEqual(self.s.rows('SELECT state FROM bank WHERE id=?', (dep['id'],))[0]['state'], 'booked')
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM bank WHERE ledger='cash' AND state='open'").fetchone()[0], 0)

    def test_reports(self):
        kasse.set_opening(self.s, START, '80', 'Test')
        self.entry('out', '20', text='Porto')
        m = berichte.monthly(self.s, TODAY)
        self.assertEqual((m['cash']['balance'], m['cash']['open']), (6000, 1))
        self.assertTrue(any('Kassenbeleg' in t for t in m['tasks']))
        self.assertEqual(m['open_bank']['count'], 0)                                    # Kassenbelege nicht als Bankumsätze
        self.assertTrue(berichte.monthly_pdf(self.s, TODAY).startswith(b'%PDF'))
        self.assertTrue(berichte.cash_pdf(self.s, YEAR).startswith(b'%PDF'))
        csv = kasse.csv(self.s, YEAR).decode('utf-8-sig')
        self.assertIn(f'K-{YEAR}-0001', csv)
        self.assertIn('Übertrag', csv)
        buf = BytesIO()
        berichte.audit_package(self.s, YEAR, Path(self.tmp.name), buf)
        with zipfile.ZipFile(buf) as z:
            names = z.namelist()
            overview = z.read([n for n in names if n.endswith('00-Uebersicht.pdf')][0])
        self.assertTrue(any(n.endswith(f'Kassenbuch-{YEAR}.csv') for n in names))
        self.assertTrue(overview.startswith(b'%PDF'))
        self.assertFalse(any(f'K-{YEAR}' in line for line in
                             zipfile.ZipFile(buf).read([n for n in names if n.endswith(f'Kontoumsaetze-{YEAR}.csv')][0]).decode('utf-8-sig').splitlines()))


if __name__ == '__main__':
    unittest.main()
