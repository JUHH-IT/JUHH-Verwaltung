"""Storno einer Bankzuordnung und Neuzuordnung desselben Umsatzes – nur erfundene Testdaten."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

import verbindlichkeiten as v
import workflow
from core import Store

IBAN = 'DE89370400440532013000'          # bekannte Beispiel-IBAN


class StornoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 't.db'
        self.s = Store(self.path)
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00',
                              'age_basis': 'jan1', 'invoice_ready': 'yes', 'tax_note': 'Fiktiv'}, 'Test')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, amount, name='Testperson', ref='Beitrag', key='r1', day='2026-06-01'):
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};{name};{ref};{key}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, key + '.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def dues(self):
        m = self.s.member(dict(number='M1', name='Testmitglied', address='Teststraße 1', email='', birth='2000-02-01',
                               joined='2024-01-01', kv='5100'), 'Test')
        return self.s.issue_dues(2026, [m], 'Test')[0]

    def bill(self):
        data = {'creditor': 'Druckerei Muster GmbH', 'iban': IBAN, 'ext_number': 'RE-4711', 'issued': '2026-10-20', 'due': '',
                'kv': 'LV', 'lines': [{'description': 'Flyer', 'amount': '119', 'account': '4430', 'title': 'H32'}]}
        return v.create(self.s, data, 'Test')[0]

    def state(self, bid):
        return tuple(self.s.db.execute('SELECT state,reversals FROM bank WHERE id=?', (bid,)).fetchone())

    def balanced(self):
        return self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0] in (0, None)

    def test_reversal_reopens_and_same_invoice_again(self):
        iid = self.dues()
        bid = self.bank('30')
        self.s.book(bid, [{'invoice_id': iid, 'amount': '30'}], 'Test')
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(self.state(bid), ('open', 1))
        self.assertEqual(self.s.invoices()[0]['paid'], 0)
        self.s.book(bid, [{'invoice_id': iid, 'amount': '30'}], 'Test')    # dieselbe Rechnung erneut – kein Konflikt
        self.assertEqual((self.state(bid), self.s.invoices()[0]['paid']), (('booked', 1), 3000))
        self.s.reverse_bank(bid, 'Test')
        self.s.book(bid, [{'invoice_id': iid, 'amount': '10'}, {'account': '8200', 'kv': 'LV', 'title': 'H5', 'amount': '20'}], 'Test')
        self.assertEqual((self.state(bid), self.s.invoices()[0]['paid']), (('booked', 2), 1000))
        sources = [r[0] for r in self.s.db.execute("SELECT source FROM journals WHERE source LIKE 'bank:%' OR source LIKE 'reverse-bank:%' ORDER BY id")]
        self.assertEqual(sources, [f'bank:{bid}', f'reverse-bank:{bid}', f'bank:{bid}/1', f'reverse-bank:{bid}/1', f'bank:{bid}/2'])
        archived = self.s.rows('SELECT kind,target_id,amount,reversal,reversed_on FROM reversed_allocations ORDER BY id')
        self.assertEqual([(a['kind'], a['target_id'], a['amount'], a['reversal']) for a in archived],
                         [('invoice', iid, 3000, 1), ('invoice', iid, 3000, 2)])
        self.assertEqual(archived[0]['reversed_on'], date.today().isoformat())
        self.assertTrue(self.balanced())
        with self.assertRaises(ValueError):
            self.s.reverse_bank(self.bank('5', key='r2'), 'Test')               # offener Umsatz ist nicht stornierbar

    def test_bill_payment_reversed_and_reassigned(self):
        bill = self.bill()
        bid = self.bank('-119', name='Druckerei Muster GmbH', ref=f'RE-4711 {IBAN}', day='2026-10-25')
        self.s.book(bid, [{'bill_id': bill, 'amount': '-119'}], 'Test')
        self.s.reverse_bank(bid, 'Test')
        row = [b for b in v.bills(self.s) if b['id'] == bill][0]
        self.assertEqual((row['open'], row['payment_reversed'], row['in_bank']), (11900, date.today().isoformat(), '2026-10-25'))
        self.assertEqual([m['id'] for m in v.matches(self.s, bid) if m['kind'] == 'bill' and m['strong']], [bill])   # wird wieder erkannt
        with self.assertRaises(ValueError):                                    # Sachkonto statt Eingangsrechnung: Schutz greift
            self.s.book(bid, [{'account': '4430', 'kv': 'LV', 'title': 'H32', 'amount': '-119'}], 'Test')
        self.s.book(bid, [{'bill_id': bill, 'amount': '-119'}], 'Test')
        row = [b for b in v.bills(self.s) if b['id'] == bill][0]
        self.assertEqual((row['open'], row['payment_reversed']), (0, None))    # Sperre durch die neue Zuordnung geklärt
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bill_payments').fetchone()[0], 1)
        self.assertEqual(self.s.rows('SELECT kind,target_id,amount FROM reversed_allocations'), [{'kind': 'bill', 'target_id': bill, 'amount': 11900}])
        liability = self.s.db.execute("SELECT SUM(credit-debit) FROM entries WHERE account='1600'").fetchone()[0]
        self.assertEqual(liability, 0)
        self.assertTrue(self.balanced())

    def test_bill_lock_stays_while_second_reversed_debit_open(self):
        data = {'creditor': 'Druckerei Muster GmbH', 'iban': IBAN, 'ext_number': 'RE-9', 'issued': '2026-10-20', 'due': '', 'kv': 'LV',
                'lines': [{'description': 'Flyer', 'amount': '200', 'account': '4430', 'title': 'H32'}]}
        bill = v.create(self.s, data, 'Test')[0]
        first = self.bank('-100', name='Druckerei Muster GmbH', ref='RE-9 Teil 1', key='t1', day='2026-10-25')
        second = self.bank('-100', name='Druckerei Muster GmbH', ref='RE-9 Teil 2', key='t2', day='2026-10-26')
        for bid in (first, second):
            self.s.book(bid, [{'bill_id': bill, 'amount': '-100'}], 'Test')
        for bid in (first, second):
            self.s.reverse_bank(bid, 'Test')
        self.s.book(first, [{'bill_id': bill, 'amount': '-100'}], 'Test')
        row = [b for b in v.bills(self.s) if b['id'] == bill][0]
        self.assertEqual((row['open'], bool(row['payment_reversed'])), (10000, True))   # zweite Abbuchung noch offen
        with self.assertRaises(ValueError):
            v.transfer(self.s, bill)
        self.s.book(second, [{'bill_id': bill, 'amount': '-100'}], 'Test')
        row = [b for b in v.bills(self.s) if b['id'] == bill][0]
        self.assertEqual((row['open'], row['payment_reversed']), (0, None))

    def test_reconcile_never_preselects_after_reversal(self):
        bill = self.bill()
        bid = self.bank('-119', name='Druckerei Muster GmbH', ref=f'RE-4711 {IBAN}', day='2026-10-25')
        self.assertTrue(v.proposals(self.s)[0]['confident'])
        self.s.book(bid, [{'bill_id': bill, 'amount': '-119'}], 'Test')
        self.s.reverse_bank(bid, 'Test')
        p = v.proposals(self.s)
        self.assertEqual([(x['bank_id'], x['id'], x['confident'], x.get('reversed')) for x in p], [(bid, bill, False, True)])

    def test_parked_reversed_parked_again_and_resolved(self):
        bid = self.bank('-40', ref='unklar')
        workflow.park(self.s, bid, 'Beleg fehlt', 'Test')
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(self.state(bid), ('open', 1))
        workflow.park(self.s, bid, 'Beleg noch immer offen', 'Test')
        parked = workflow.details(self.s, 2026)['parked']
        self.assertEqual([(p['id'], p['reason']) for p in parked], [(bid, 'Beleg noch immer offen')])
        self.s.book(bid, [{'account': '4430', 'kv': 'LV', 'title': 'H32', 'amount': '-40'}], 'Test')
        self.assertEqual(self.state(bid), ('booked', 1))
        self.s.reverse_bank(bid, 'Test')                                       # Parken und Auflösen gemeinsam gegengebucht
        self.assertEqual(self.state(bid), ('open', 2))
        self.assertEqual(self.s.db.execute("SELECT COALESCE(SUM(debit-credit),0) FROM entries WHERE account IN ('1370','4430','1420')").fetchone()[0], 0)
        self.assertTrue(self.balanced())

    def test_rebooking_keeps_budget_year(self):
        bid = self.bank('-40', ref='spaet', day='2026-01-05')
        self.s.book(bid, [{'account': '4430', 'kv': 'LV', 'title': 'H32', 'amount': '-40'}], 'Test')
        self.s.db.execute('UPDATE journals SET year=2025 WHERE source=?', (f'bank:{bid}',))   # z. B. Excel-Blatt 2025
        self.s.db.commit()
        self.s.reverse_bank(bid, 'Test')
        self.s.book(bid, [{'account': '4430', 'kv': 'LV', 'title': 'H32', 'amount': '-40'}], 'Test')
        self.assertEqual(self.s.db.execute('SELECT year FROM journals WHERE source=?', (f'bank:{bid}/1',)).fetchone()[0], 2025)

    def test_learned_example_removed_on_reversal(self):
        bid = self.bank('-12', name='Telefon AG', ref='Rechnung 42')
        self.s.book(bid, [{'account': '4430', 'kv': 'LV', 'title': 'H32', 'amount': '-12'}], 'Test', learn=True)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM booking_examples').fetchone()[0], 1)
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM booking_examples').fetchone()[0], 0)
        self.assertEqual(workflow.suggest(self.s, self.bank('-12', name='Telefon AG', ref='Rechnung 43', key='r2')), [])

    def test_reversal_without_reference(self):
        bid = self.bank('-7', ref='')
        self.s.book(bid, [{'account': '4430', 'kv': 'LV', 'title': 'H32', 'amount': '-7'}], 'Test')
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(self.state(bid), ('open', 1))

    def test_legacy_reversed_rows_reopened_on_start(self):
        """Bis 0.11 blieb ein stornierter Umsatz im Zustand „reversed“ mit seiner alten Zuordnung stehen."""
        iid = self.dues()
        bid = self.bank('30')
        self.s.book(bid, [{'invoice_id': iid, 'amount': '30'}], 'Test')
        j = self.s.db.execute('SELECT id FROM journals WHERE source=?', (f'bank:{bid}',)).fetchone()[0]
        entries = self.s.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?', (j,))
        learned = self.bank('-12', name='Telefon AG', ref='Rechnung 42', key='r2')
        self.s.book(learned, [{'account': '4430', 'kv': 'LV', 'title': 'H32', 'amount': '-12'}], 'Test', learn=True)
        jl = self.s.db.execute('SELECT id FROM journals WHERE source=?', (f'bank:{learned}',)).fetchone()[0]
        learned_entries = self.s.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?', (jl,))
        with self.s.db:                                                        # Storno wie in 0.11
            self.s.journal('2026-06-01', 'Storno Zuordnung: Beitrag', f'reverse-bank:{bid}', entries, j)
            self.s.journal('2026-06-01', 'Storno Zuordnung: Rechnung 42', f'reverse-bank:{learned}', learned_entries, jl)
            self.s.db.execute("UPDATE bank SET state='reversed' WHERE id IN (?,?)", (bid, learned))
        self.s.db.close()
        self.s = Store(self.path)
        self.assertEqual((self.state(bid), self.state(learned)), (('open', 1), ('open', 1)))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM booking_examples').fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM payments').fetchone()[0], 0)
        self.assertEqual(self.s.rows('SELECT kind,amount,reversal,reversed_on FROM reversed_allocations'),
                         [{'kind': 'invoice', 'amount': 3000, 'reversal': 1, 'reversed_on': None}])
        self.assertIn('Neuzuordnung', self.s.rows('SELECT action FROM audit ORDER BY id DESC LIMIT 1')[0]['action'])
        self.s.book(bid, [{'invoice_id': iid, 'amount': '30'}], 'Test')
        self.assertEqual(self.s.invoices()[0]['paid'], 3000)
        self.assertTrue(self.balanced())
        self.s.db.close()
        self.s = Store(self.path)                                              # zweiter Start ändert nichts mehr
        self.assertEqual(self.state(bid), ('booked', 1))


if __name__ == '__main__':
    unittest.main()
