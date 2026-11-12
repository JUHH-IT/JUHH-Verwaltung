"""Freigabe von Fremdrechnungen (§ 7 Abs. 4 FO) und Quartalsprüfung (§ 7 Abs. 3 FO) – nur erfundene Testdaten."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import freigabe as f
import verbindlichkeiten as v
from core import Store

IBAN = 'DE89370400440532013000'          # bekannte Beispiel-IBAN


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00',
                              'chair_name': 'Vorsitz Beispiel', 'treasurer_name': 'Schatz Beispiel'}, 'Test')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bill(self, amount='600', kind='external', issued='2026-08-14', ext='RE-1', creditor='Halle GmbH'):
        data = {'creditor': creditor, 'iban': IBAN, 'ext_number': ext, 'issued': issued, 'due': '', 'kv': 'LV', 'kind': kind,
                'lines': [{'description': 'Saalmiete', 'amount': amount, 'account': '4430', 'title': 'H32'}]}
        if kind == 'refund':  # Auslagenerstattung: Antrag eines Mitglieds mit Nachweis (Finanzordnung § 8)
            member = self.s.db.execute("SELECT id FROM members WHERE number='A1'").fetchone()
            member = member[0] if member else self.s.member(dict(number='A1', name='Anna Antrag', address='Weg 1', email='', birth='2000-01-01',
                                                                  joined='2024-01-01', kv='5100'), 'Test')
            data.update(member_id=member, submitted_on=issued, signed=True, complete=True, authorized=True, no_other=True)
            return v.create(self.s, data, 'Test', '.pdf')[0]
        return v.create(self.s, data, 'Test')[0]

    def row(self, bid):
        return [b for b in v.bills(self.s) if b['id'] == bid][0]

    def approve(self, bid, role, name, day=None, how='E-Mail'):
        f.approve(self.s, bid, role, {'name': name, 'approved_on': day or date.today().isoformat(), 'how': how}, 'Test')


class ApprovalTests(Base):
    def test_limit_and_kind(self):
        small, exact, big = self.bill('499,99', ext='A'), self.bill('500', ext='B'), self.bill('500,01', ext='C')
        refund = self.bill('900', kind='refund', ext='D')
        required = {bid: self.row(bid)['approval']['required'] for bid in (small, exact, big, refund)}
        self.assertEqual(required, {small: False, exact: False, big: True, refund: False})   # „über EUR 500,00“
        self.assertEqual(v.transfer(self.s, exact)['amount'], 50000)
        with self.assertRaises(ValueError) as e:
            self.approve(refund, 'chair', 'Vorsitz Beispiel')
        self.assertIn('braucht keine Freigabe', str(e.exception))

    def test_payment_blocked_until_both_approved(self):
        bid = self.bill()
        self.assertEqual(self.row(bid)['approval']['missing'], ['chair', 'treasurer'])
        with self.assertRaises(ValueError) as e:
            v.transfer(self.s, bid)
        self.assertIn('§ 7 Abs. 4', str(e.exception))
        with self.assertRaises(ValueError):
            v.mark_transferred(self.s, [bid], 'Test')
        self.approve(bid, 'chair', 'Vorsitz Beispiel')
        with self.assertRaises(ValueError) as e:
            v.transfer(self.s, bid)
        self.assertIn('Landesschatzmeister', str(e.exception))
        with self.assertRaises(ValueError) as e:                                  # Vier-Augen-Prinzip
            self.approve(bid, 'treasurer', 'vorsitz beispiel')
        self.assertIn('verschiedenen Personen', str(e.exception))
        with self.assertRaises(ValueError):
            self.approve(bid, 'chair', 'Noch Jemand')                             # Rolle schon freigegeben
        self.approve(bid, 'treasurer', 'Schatz Beispiel', how='schriftlich')
        a = self.row(bid)['approval']
        self.assertTrue(a['complete'])
        self.assertEqual((a['given']['chair']['name'], a['given']['treasurer']['how']), ('Vorsitz Beispiel', 'schriftlich'))
        self.assertEqual(v.transfer(self.s, bid)['amount'], 60000)
        v.mark_transferred(self.s, [bid], 'Test')

    def test_input_checks(self):
        bid = self.bill()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        for data in ({'name': '', 'approved_on': date.today().isoformat(), 'how': 'E-Mail'},
                     {'name': 'X', 'approved_on': tomorrow, 'how': 'E-Mail'},
                     {'name': 'X', 'approved_on': date.today().isoformat(), 'how': 'Brieftaube'}):
            with self.assertRaises(ValueError):
                f.approve(self.s, bid, 'chair', data, 'Test')
        with self.assertRaises(ValueError):
            f.approve(self.s, bid, 'kassierer', {'name': 'X', 'approved_on': date.today().isoformat(), 'how': 'E-Mail'}, 'Test')

    def test_revoke_only_before_transfer(self):
        bid = self.bill()
        self.approve(bid, 'chair', 'Vorsitz Beispiel')
        first = self.row(bid)['approval']['given']['chair']['id']
        f.revoke(self.s, first, 'Test')
        self.assertEqual(self.row(bid)['approval']['missing'], ['chair', 'treasurer'])
        self.approve(bid, 'chair', 'Vorsitz Beispiel')                            # nach Rücknahme erneut möglich
        self.approve(bid, 'treasurer', 'Schatz Beispiel')
        v.mark_transferred(self.s, [bid], 'Test')
        with self.assertRaises(ValueError):
            f.revoke(self.s, self.row(bid)['approval']['given']['chair']['id'], 'Test')
        v.mark_transferred(self.s, [bid], 'Test', undo=True)                       # Zurücknehmen der Anweisung bleibt möglich
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bill_approvals').fetchone()[0], 3)   # nichts gelöscht

    def test_paid_without_approval_is_flagged_not_blocked(self):
        bid = self.bill()
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-08-20;-600;Halle GmbH;RE-1 {IBAN};k1\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, 'k.csv'), [2], 'Test')
        bank = self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
        self.s.book(bank, [{'bill_id': bid, 'amount': '-600'}], 'Test')             # Bank muss stimmen
        self.assertTrue(self.row(bid)['approval']['unapproved_paid'])
        rep = f.report(self.s, 2026, 3)
        self.assertIn('ohne vollständige Freigabe angewiesen/bezahlt', rep['bills'][0]['findings'])
        self.assertEqual(f.pending(self.s), [])                                     # bezahlt → keine offene Freigabe mehr

    def test_limit_fixed_per_bill_and_capped(self):
        old = self.bill('300', ext='A')
        self.s.save_settings({'approval_limit': '250'}, 'Test')                # strenger: gilt für neue Rechnungen
        new = self.bill('300', ext='B')
        self.assertEqual((self.row(old)['approval']['required'], self.row(new)['approval']['required']), (False, True))
        for bad in ('0', '500,01', '1.000'):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                self.s.save_settings({'approval_limit': bad}, 'Test')            # höher als § 7 Abs. 4 nicht zulässig
        self.s.save_settings({'approval_limit': '500'}, 'Test')
        self.assertTrue(self.row(new)['approval']['required'])                   # festgeschrieben, keine Rückwirkung
        self.assertEqual(f.pending(self.s), [self.row(new)['number']])

    def test_late_approval_stays_visible(self):
        bid = self.bill()
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-08-20;-600;Halle GmbH;RE-1 {IBAN};k1\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, 'k.csv'), [2], 'Test')
        bank = self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
        self.s.book(bank, [{'bill_id': bid, 'amount': '-600'}], 'Test')
        self.approve(bid, 'chair', 'Vorsitz Beispiel', day='2026-08-10')         # rückdatiert, aber erst jetzt eingetragen
        self.approve(bid, 'treasurer', 'Schatz Beispiel', day='2026-08-10')
        a = self.row(bid)['approval']
        self.assertEqual((a['complete'], a['unapproved_paid'], a['late'], a['paid_on']), (True, False, 'recorded_after', '2026-08-20'))
        self.assertIn('Freigabe erst nach Zahlung eingetragen', f.report(self.s, 2026, 3)['bills'][0]['findings'])
        # nach der Zahlung lässt sich die Freigabe nicht mehr zurücknehmen
        with self.assertRaises(ValueError):
            f.revoke(self.s, a['given']['chair']['id'], 'Test')

    def test_approval_after_payment_date(self):
        bid = self.bill(ext='Z')
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-08-20;-600;Halle GmbH;RE-Z {IBAN};k2\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, 'k2.csv'), [2], 'Test')
        bank = self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
        self.s.book(bank, [{'bill_id': bid, 'amount': '-600'}], 'Test')
        self.approve(bid, 'chair', 'Vorsitz Beispiel', day='2026-08-25')
        self.approve(bid, 'treasurer', 'Schatz Beispiel', day='2026-08-25')
        self.assertIn('Zahlung vor Freigabe', f.report(self.s, 2026, 3)['bills'][0]['findings'])

    def test_refund_over_limit_flagged(self):
        bid = self.bill('900', kind='refund', ext='R')
        self.assertFalse(self.row(bid)['approval']['required'])
        self.assertIn('Auslagenerstattung über 500,00 € – Art prüfen', f.report(self.s, 2026, 3)['bills'][0]['findings'])

    def test_reconcile_does_not_preselect_unapproved(self):
        bid = self.bill()
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-08-20;-600;Halle GmbH;RE-1 {IBAN};k3\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, 'k3.csv'), [2], 'Test')
        p = v.proposals(self.s)
        self.assertEqual([(x['id'], x['confident'], x.get('unapproved')) for x in p], [(bid, False, True)])


class ReviewTests(Base):
    def test_quarter_bounds(self):
        self.assertEqual(f.bounds(2026, 1), ('2026-01-01', '2026-03-31'))
        self.assertEqual(f.bounds(2026, 3), ('2026-07-01', '2026-09-30'))
        self.assertEqual(f.bounds(2026, 4), ('2026-10-01', '2026-12-31'))
        self.assertEqual(f.deadline(2026, 3), '2026-10-28')                         # vier Wochen nach Quartalsende
        self.assertEqual(f.deadline(2026, 4), '2027-01-28')
        self.assertEqual(f.quarter_of('2026-09-30'), (2026, 3))
        with self.assertRaises(ValueError):
            f.bounds(2026, 5)

    def test_report_review_and_due(self):
        a = self.bill('80', ext='A', issued='2026-07-03')
        b = self.bill('600', ext='B', issued='2026-09-30', creditor='Druck AG')
        self.bill('50', ext='C', issued='2026-10-01')                                 # nächstes Quartal
        cancelled = self.bill('30', ext='D', issued='2026-08-01', creditor='Fehl GmbH')
        v.cancel(self.s, cancelled, 'Test')
        rep = f.report(self.s, 2026, 3)
        self.assertEqual([i['id'] for i in rep['bills']], [a, cancelled, b])
        self.assertEqual((rep['count'], rep['total'], rep['required'], rep['approved']), (2, 68000, 1, 0))
        self.assertEqual(rep['bills'][0]['findings'], ['Beleg fehlt'])
        self.assertEqual(rep['bills'][1]['findings'], [])                            # storniert: keine Auffälligkeit
        self.assertEqual(rep['bills'][2]['findings'], ['Beleg fehlt', 'Freigabe fehlt'])
        self.assertEqual(rep['findings'], 3)
        self.assertEqual(f.due(self.s, '2026-10-20'), [{'year': 2026, 'quarter': 3, 'label': 'Q3/2026', 'count': 2,
                                                        'recheck': False, 'deadline': '2026-10-28', 'overdue': False}])
        self.assertTrue(f.due(self.s, '2026-10-29')[0]['overdue'])
        today = '2026-10-20'          # fester Prüftag innerhalb der Prüffrist (28.10.), unabhängig vom Testdatum
        for data in ({'reviewer': '', 'reviewed_on': today, 'result': 'ok'},
                     {'reviewer': 'Schatz Beispiel', 'reviewed_on': '2026-09-30', 'result': 'ok'},     # vor Quartalsende
                     {'reviewer': 'Schatz Beispiel', 'reviewed_on': today, 'result': 'remarks', 'remarks': ''},
                     {'reviewer': 'Schatz Beispiel', 'reviewed_on': today, 'result': 'gut'}):
            with self.assertRaises(ValueError):
                f.record_review(self.s, 2026, 3, data, 'Test')
        f.record_review(self.s, 2026, 3, {'reviewer': 'Schatz Beispiel', 'reviewed_on': today, 'result': 'remarks',
                                          'remarks': 'Freigabe für Druck AG nachholen.'}, 'Test')
        self.assertEqual(f.due(self.s, '2026-10-29'), [])
        rev = f.report(self.s, 2026, 3)['reviews']
        self.assertEqual((rev[0]['bills'], rev[0]['total'], rev[0]['findings'], rev[0]['result'], rev[0]['late']), (2, 68000, 3, 'remarks', False))
        # nach der Prüfung erfasst → Nachprüfung fällig, Protokoll bleibt beim geprüften Stand
        late = self.bill('900', ext='E', issued='2026-09-15', creditor='Nach AG')
        self.assertEqual([(d['label'], d['count'], d['recheck']) for d in f.due(self.s, '2026-10-29')], [('Q3/2026', 1, True)])
        rep = f.report(self.s, 2026, 3)
        self.assertEqual((rep['unreviewed'], [i['id'] for i in rep['bills'] if not i['reviewed']]), (1, [late]))
        basis, review, later = f.protocol_basis(self.s, 2026, 3)
        self.assertEqual((basis['count'], basis['findings'], [i['id'] for i in later]), (2, 3, [late]))
        self.assertTrue(f.protocol_pdf(self.s, 2026, 3).startswith(b'%PDF'))

    def test_current_quarter_not_reviewable(self):
        y, q = f.quarter_of(date.today().isoformat())
        with self.assertRaises(ValueError) as e:
            f.record_review(self.s, y, q, {'reviewer': 'X', 'reviewed_on': date.today().isoformat(), 'result': 'ok'}, 'Test')
        self.assertIn('noch nicht abgeschlossen', str(e.exception))

    def test_protocol_pdf(self):
        self.bill('600', issued='2026-08-14')
        draft = f.protocol_pdf(self.s, 2026, 3)
        self.assertTrue(draft.startswith(b'%PDF'))
        f.record_review(self.s, 2026, 3, {'reviewer': 'Schatz Beispiel', 'reviewed_on': date.today().isoformat(), 'result': 'ok'}, 'Test')
        self.assertTrue(f.protocol_pdf(self.s, 2026, 3).startswith(b'%PDF'))
        self.assertTrue(f.protocol_pdf(self.s, 2025, 1).startswith(b'%PDF'))        # leeres Quartal

    def test_second_start_changes_nothing(self):
        self.bill('80', ext='A')
        self.s.db.close()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.assertEqual(self.s.db.execute('SELECT kind FROM bills').fetchone()[0], 'external')

if __name__ == '__main__':
    unittest.main()
