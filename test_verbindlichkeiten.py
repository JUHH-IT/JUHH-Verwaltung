"""Eingangsrechnungen, Zahlungsvorschlag, offene Posten und Abgleich – nur erfundene Testdaten."""
import base64
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import verbindlichkeiten as v
from core import Store

IBAN = 'DE89370400440532013000'          # bekannte Beispiel-IBAN
OTHER = 'DE02120300000000202051'         # zweite gültige Beispiel-IBAN


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00',
                              'age_basis': 'jan1', 'invoice_ready': 'yes', 'tax_note': 'Fiktiv'}, 'Test')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bill(self, amount='119', creditor='Druckerei Muster GmbH', iban=IBAN, ext='RE-4711', **extra):
        data = {'creditor': creditor, 'iban': iban, 'ext_number': ext, 'issued': '2026-10-20', 'due': '', 'kv': 'LV',
                'lines': [{'description': 'Flyer', 'amount': amount, 'account': '4430', 'title': 'H32'}], **extra}
        return v.create(self.s, data, 'Test')[0]

    def bank(self, amount, name='Druckerei Muster GmbH', ref='RE-4711', day='2026-10-25', key='r1'):
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};{name};{ref};{key}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, key + '.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def bill_row(self, bid):
        return [b for b in v.bills(self.s) if b['id'] == bid][0]


class BillTests(Base):
    def test_create_books_expense_against_liability(self):
        bid = self.bill()
        b = self.bill_row(bid)
        self.assertEqual((b['number'], b['open'], b['due']), (f'E-{date.today().year}-00001', 11900, '2026-11-03'))
        rows = self.s.rows("SELECT e.account,e.debit,e.credit,j.year FROM entries e JOIN journals j ON j.id=e.journal_id WHERE j.source=?", ('bill:%d' % bid,))
        self.assertEqual(sorted((r['account'], r['debit'], r['credit']) for r in rows), [('1600', 0, 11900), ('4430', 11900, 0)])
        self.assertEqual(rows[0]['year'], 2026)
        self.assertEqual(self.bill(ext='RE-4712') - bid, 1)

    def test_validation(self):
        for extra in [{'iban': 'DE00123'}, {'creditor': ''}, {'lines': []}, {'issued': '2026-10-20', 'due': '2026-10-01'},
                      {'lines': [{'description': 'X', 'amount': '5', 'account': '8500', 'title': 'H17'}]},
                      {'lines': [{'description': 'X', 'amount': '5', 'account': '4430', 'title': ''}]}, {'kv': 'XX'}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                data = {'creditor': 'A', 'iban': IBAN, 'ext_number': 'N1', 'issued': '2026-10-20', 'due': '', 'kv': 'LV',
                        'lines': [{'description': 'Flyer', 'amount': '10', 'account': '4430', 'title': 'H32'}]}
                data.update(extra)
                v.create(self.s, data, 'Test')

    def test_duplicate_supplier_number(self):
        self.bill()
        for extra in [{}, {'ext': 're 4711'}, {'ext': '', 'creditor': 'DRUCKEREI muster gmbh'}]:  # gleiche Nummer bzw. gleicher Betrag + Datum
            with self.subTest(extra=extra), self.assertRaises(ValueError) as cm:
                self.bill(**extra)
            self.assertIn(v.DUPLICATE, str(cm.exception))
        self.bill(allow_duplicate=True)
        self.bill(ext='RE-4712', amount='120')  # andere Rechnung desselben Kreditors

    def test_date_plausibility(self):
        for issued, due in [('01.02.62', ''), ('2020-01-01', ''), ('2026-10-20', '2028-01-01')]:
            with self.subTest(issued=issued), self.assertRaises(ValueError):
                self.bill(issued=issued, due=due, ext='X-' + issued)

    def test_cancel_reverses(self):
        bid = self.bill()
        v.cancel(self.s, bid, 'Test')
        self.assertEqual(self.bill_row(bid)['open'], 0)
        self.assertEqual(self.s.db.execute("SELECT SUM(debit-credit) FROM entries WHERE account='1600'").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            v.cancel(self.s, bid, 'Test')

    def test_transfer_data_and_qr(self):
        bid = self.bill()
        t = v.transfer(self.s, bid)
        self.assertEqual((t['iban'], t['amount'], t['reference']), ('DE89 3704 0044 0532 0130 00', 11900, 'RE-4711'))
        v.mark_transferred(self.s, [bid], 'Test')
        self.assertEqual(self.bill_row(bid)['transferred'], date.today().isoformat())
        v.mark_transferred(self.s, [bid], 'Test', undo=True)
        self.assertIsNone(self.bill_row(bid)['transferred'])
        no_iban = self.bill(ext='X1', iban='')
        with self.assertRaises(ValueError):
            v.transfer(self.s, no_iban)

    def test_file_check(self):
        pdf = base64.b64encode(b'%PDF-1.4 test').decode()
        self.assertEqual(v.check_file('beleg.PDF', pdf)[0], '.pdf')
        for name, content in [('beleg.exe', pdf), ('beleg.pdf', base64.b64encode(b'MZ...').decode()), ('beleg.png', pdf)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                v.check_file(name, content)


class PaymentAndMatchTests(Base):
    def test_bank_outflow_pays_bill(self):
        bid = self.bill()
        bank = self.bank('-119,00')
        self.s.book(bank, [{'amount': '-119.00', 'bill_id': bid}], 'Test')
        self.assertEqual(self.bill_row(bid)['open'], 0)
        self.assertEqual(self.s.db.execute("SELECT SUM(credit-debit) FROM entries WHERE account='1600'").fetchone()[0], 0)
        self.assertEqual(self.s.db.execute("SELECT SUM(debit-credit) FROM entries WHERE account='1420'").fetchone()[0], -11900)

    def test_partial_and_overpayment(self):
        bid = self.bill()
        self.s.book(self.bank('-100', key='a'), [{'amount': '-100', 'bill_id': bid}], 'Test')
        self.assertEqual(self.bill_row(bid)['open'], 1900)
        with self.assertRaises(ValueError):
            self.s.book(self.bank('-50', key='b'), [{'amount': '-50', 'bill_id': bid}], 'Test')
        with self.assertRaises(ValueError):  # Gutschrift kann keine Eingangsrechnung bezahlen
            self.s.book(self.bank('19', key='c'), [{'amount': '19', 'bill_id': bid}], 'Test')

    def test_reversed_bank_reopens_bill(self):
        bid = self.bill()
        bank = self.bank('-119')
        self.s.book(bank, [{'amount': '-119', 'bill_id': bid}], 'Test')
        self.s.reverse_bank(bank, 'Test')
        self.assertEqual(self.bill_row(bid)['open'], 11900)

    def test_matching_evidence(self):
        bid = self.bill()
        self.bill(amount='119', creditor='Andere Firma', iban=OTHER, ext='A-1')
        bank = self.bank('-119', name='Druckerei Muster GmbH', ref=f'RE-4711 IBAN {IBAN}')
        found = v.matches(self.s, bank)
        self.assertEqual(found[0]['id'], bid)
        self.assertEqual(set(found[0]['evidence']), {'IBAN', 'Rechnungsnummer', 'Name'})
        self.assertTrue(found[0]['confident'])
        self.assertFalse(found[1]['strong'])

    def test_short_reference_only_as_word(self):
        bid = self.bill(ext='RE-1', iban='')
        self.bank('-119', name='X', ref='RE-12 Sammel', key='a')
        self.assertEqual(v.proposals(self.s), [])
        self.bank('-119', name='X', ref='Zahlung RE-1', key='b')
        self.assertEqual([p['id'] for p in v.proposals(self.s)], [bid])

    def test_proposals_and_apply(self):
        bill = self.bill()
        member = self.s.member(dict(number='M1', name='Erika', address='Weg 1', email='', birth='2000-01-01', joined='2024-01-01', kv='5100'), 'Test')
        iid = self.s.issue_dues(2026, [member], 'Test')[0]
        number = self.s.invoices()[0]['number']
        out = self.bank('-119', ref='Rechnung RE-4711', key='o')
        inn = self.bank('30', name='Erika', ref=f'Beitrag {number}', key='i')
        self.bank('-55', name='Unbekannt', ref='ohne Bezug', key='u')
        props = v.proposals(self.s, 2026)
        self.assertEqual({(p['kind'], p['id']) for p in props}, {('bill', bill), ('invoice', iid)})
        self.assertEqual(v.apply(self.s, props, 'Test'), (2, None))
        self.assertEqual(self.bill_row(bill)['open'], 0)
        self.assertEqual([i['paid'] for i in self.s.invoices()], [3000])
        self.assertEqual({b['id']: b['state'] for b in self.s.rows('SELECT id,state FROM bank')}[out], 'booked')
        self.assertEqual(v.proposals(self.s, 2026), [])

    def test_no_proposal_when_ambiguous(self):
        self.bill(ext='RE-1')
        self.bill(ext='RE-2')
        self.bank('-119', name='Druckerei Muster GmbH', ref='Sammelzahlung', key='x')  # nur Name, kein starker Treffer
        self.assertEqual(v.proposals(self.s), [])
        bid = self.bill(amount='50', ext='RE-3')
        self.bank('-50', ref='RE-3', key='y')
        self.bank('-50', ref='RE-3 nochmal', key='z')  # zwei Umsätze beanspruchen dieselbe Rechnung
        self.assertEqual([p for p in v.proposals(self.s) if p['id'] == bid], [])

    def test_apply_rechecks(self):
        bid = self.bill()
        bank = self.bank('-119')
        props = v.proposals(self.s)
        v.cancel(self.s, bid, 'Test')
        with self.assertRaises(ValueError):
            v.apply(self.s, props, 'Test')
        self.assertEqual(self.s.rows('SELECT state FROM bank WHERE id=?', (bank,))[0]['state'], 'open')


if __name__ == '__main__':
    unittest.main(verbosity=2)


class LiabilityAccountTests(Base):
    def test_change_account_keeps_old_bills(self):
        old = self.bill(ext='A-1')
        self.s.save_settings({'liability_account': '3300', 'liability_name': 'Verbindlichkeiten aLuL'}, 'Test')
        self.assertEqual(self.s.db.execute("SELECT name,kind FROM accounts WHERE code='3300'").fetchone()[:], ('Verbindlichkeiten aLuL', 'liability'))
        new = self.bill(ext='A-2')
        rows = {r['id']: r['liability'] for r in v.bills(self.s)}
        self.assertEqual((rows[old], rows[new]), ('1600', '3300'))
        self.s.book(self.bank('-119', ref='A-1', key='a'), [{'amount': '-119', 'bill_id': old}], 'Test')
        self.s.book(self.bank('-119', ref='A-2', key='b'), [{'amount': '-119', 'bill_id': new}], 'Test')
        for code in ('1600', '3300'):  # jede Rechnung wird gegen ihr eigenes Konto ausgeglichen
            self.assertEqual(self.s.db.execute('SELECT SUM(debit-credit) FROM entries WHERE account=?', (code,)).fetchone()[0], 0)

    def test_upgrade_from_0_10_adds_column(self):
        import sqlite3
        path = Path(self.tmp.name) / 'alt.db'
        old = sqlite3.connect(path)
        old.executescript("CREATE TABLE bills(id INTEGER PRIMARY KEY,number TEXT UNIQUE NOT NULL,creditor TEXT NOT NULL,iban TEXT,bic TEXT,"
                          "ext_number TEXT,reference TEXT NOT NULL,issued TEXT NOT NULL,due TEXT NOT NULL,total INTEGER NOT NULL,kv TEXT NOT NULL,"
                          "status TEXT NOT NULL DEFAULT 'open',transferred TEXT,attachment TEXT,note TEXT,created TEXT NOT NULL,actor TEXT,"
                          "written_off INTEGER NOT NULL DEFAULT 0,written_off_on TEXT,payment_reversed TEXT);"
                          "INSERT INTO bills(number,creditor,reference,issued,due,total,kv,created) VALUES('E-2026-00001','Alt','X','2026-10-01','2026-10-15',100,'LV','x');")
        old.commit()
        old.close()
        store = Store(path)
        self.assertEqual(store.db.execute('SELECT liability,kind FROM bills').fetchone()[:], ('1600', 'external'))
        self.assertFalse(v.bills(store)[0]['approval']['required'])          # 1 € – keine Freigabe nötig
        store.db.close()

    def test_account_conflicts_rejected(self):
        for code in ('4210', '8100', '12', 'abc'):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.s.save_settings({'liability_account': code}, 'Test')
        self.assertEqual(self.s.config()['liability_account'], '1600')


class ReviewFindingTests(Base):
    def test_no_false_reference_matches(self):
        self.bill(creditor='Druckerei Alpha', iban=OTHER, ext='')  # Belegnummer E-…-00001
        bank = self.bank('-119', name='Catering Beta GmbH', ref=f'Rechnung RE-{date.today().year}-00001')
        self.assertEqual([c for c in v.matches(self.s, bank) if c['strong']], [])

    def test_other_iban_and_early_date_are_not_strong(self):
        self.bill(iban=IBAN)
        conflict = self.bank('-119', ref=f'RE-4711 {OTHER}', key='a')
        self.assertEqual(v.matches(self.s, conflict)[0]['against'], ['andere IBAN'])
        early = self.bank('-119', ref='RE-4711', day='2026-09-01', key='b')
        self.assertEqual(v.matches(self.s, early)[0]['against'], ['vor Rechnungsdatum'])
        self.assertEqual(v.proposals(self.s), [])

    def test_iban_found_inside_combined_text(self):
        bid = self.bill(ext='')
        bank = self.bank('-119', name='X', ref=f'Druckerei {IBAN} RE Flyer')
        self.assertEqual(v.matches(self.s, bank)[0]['evidence'][0], 'IBAN')
        self.assertEqual([p['id'] for p in v.proposals(self.s)], [bid])

    def test_reversed_payment_locks_bill(self):
        bid = self.bill()
        bank = self.bank('-119')
        self.s.book(bank, [{'amount': '-119', 'bill_id': bid}], 'Test')
        self.s.reverse_bank(bank, 'Test')
        b = self.bill_row(bid)
        self.assertEqual((b['open'], b['payment_reversed']), (11900, date.today().isoformat()))
        with self.assertRaises(ValueError):
            v.transfer(self.s, bid)  # nicht erneut überweisen, bevor geklärt
        v.release(self.s, bid, 'Test')
        self.assertEqual(v.transfer(self.s, bid)['amount'], 11900)

    def test_booking_guard_against_double_expense(self):
        bid = self.bill()
        bank = self.bank('-119', ref='RE-4711')
        part = [{'amount': '-119', 'account': '4210', 'kv': 'LV', 'title': 'H27'}]
        with self.assertRaises(ValueError) as cm:
            self.s.book(bank, part, 'Test')
        self.assertIn(self.s.BILL_CONFLICT, str(cm.exception))
        self.s.book(bank, part, 'Test', confirm_no_bill=True)  # ausdrücklich bestätigt
        self.assertEqual(self.bill_row(bid)['open'], 11900)

    def test_pending_in_bank_flags_bill(self):
        bid = self.bill()
        self.bank('-119', ref='RE-4711')
        self.assertEqual(self.bill_row(bid)['in_bank'], '2026-10-25')
        self.assertEqual(v.transfer(self.s, bid)['in_bank'], '2026-10-25')

    def test_write_off_rest(self):
        bid = self.bill()
        with self.assertRaises(ValueError):
            v.write_off(self.s, bid, 'Skonto', 'Test')  # ohne Zahlung: stornieren statt ausbuchen
        self.s.book(self.bank('-116,62'), [{'amount': '-116,62', 'bill_id': bid}], 'Test')
        with self.assertRaises(ValueError):
            v.write_off(self.s, bid, '', 'Test')
        v.write_off(self.s, bid, 'Skonto 2 %', 'Test')
        self.assertEqual(self.bill_row(bid)['open'], 0)
        self.assertEqual(self.s.db.execute("SELECT SUM(credit-debit) FROM entries WHERE account='1600'").fetchone()[0], 0)
        self.assertEqual(self.s.db.execute("SELECT SUM(debit-credit) FROM entries WHERE account='4430'").fetchone()[0], 11662)

    def test_apply_only_current_proposals(self):
        bid = self.bill()
        bank = self.bank('-119')
        with self.assertRaises(ValueError):  # nicht vorgeschlagen (anderer Beleg) → nichts gebucht
            v.apply(self.s, [{'bank_id': bank, 'kind': 'bill', 'id': bid + 99}], 'Test')
        self.assertEqual(self.s.rows('SELECT state FROM bank WHERE id=?', (bank,))[0]['state'], 'open')

    def test_api_input_checks(self):
        bid = self.bill()
        with self.assertRaises(ValueError):
            v.mark_transferred(self.s, '12', 'Test')
        bank = self.bank('-119')
        with self.assertRaises(ValueError):
            self.s.book(bank, [{'amount': '-119', 'bill_id': bid}], 'Test', learn=True)
