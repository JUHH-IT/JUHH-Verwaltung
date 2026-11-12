"""Mitgliedsbeiträge (8100) einem Mitglied zuordnen und KV-Budgets aus Ist-Werten – nur erfundene Testdaten."""
import json
import tempfile
import unittest
from pathlib import Path

import beitraege
import kvbudget
import workflow
from core import Store, bank_source

BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg', birth='2000-02-01', joined='2024-01-01')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.n = 0
        self.erika = self.s.member({**BASE, 'number': 'JU-1001', 'first_name': 'Erika', 'last_name': 'Muster', 'kv': '5100'}, 'Test')
        self.karl = self.s.member({**BASE, 'number': 'JU-1002', 'first_name': 'Karl', 'last_name': 'Beispiel', 'kv': '5300'}, 'Test')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, amount, day='2026-03-02', name='Erika Muster', ref='Mitgliedsbeitrag 2026'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};{name};{ref};k{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'k{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def legacy_8100(self, amount, day='2026-02-01', name='Erika Muster', ref='Beitrag', year=None):
        """8100-Buchung wie aus der Excel-Übernahme (ohne Mitglied, Bereich LV)."""
        bid = self.bank(amount, day, name, ref)
        cents = int(round(float(amount.replace(',', '.')) * 100))
        with self.s.db:
            self.s.journal(day, ref, bank_source('bank', {'id': bid, 'reversals': 0}),
                           [{'account': '1420', 'kv': 'LV', 'debit': max(cents, 0), 'credit': max(-cents, 0)},
                            {'account': '8100', 'kv': 'LV', 'debit': max(-cents, 0), 'credit': max(cents, 0), 'title': 'H4'}], year=year or int(day[:4]))
            self.s.db.execute("UPDATE bank SET state='booked' WHERE id=?", (bid,))
        return self.s.db.execute("SELECT MAX(id) FROM entries WHERE account='8100'").fetchone()[0]


class BookingTests(Base):
    def test_member_required_and_entries_per_member(self):
        bid = self.bank('60')
        with self.assertRaises(ValueError) as cm:
            self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '60'}], 'Test')
        self.assertIn('Mitglied zuordnen', str(cm.exception))
        for members, message in (([{'member_id': self.erika, 'amount': '30'}], 'ergeben nicht'),
                                 ([{'member_id': self.erika, 'amount': '30'}, {'member_id': self.erika, 'amount': '30'}], 'doppelt'),
                                 ([{'member_id': 999}], 'nicht gefunden'),
                                 ([{'member_id': self.erika, 'amount': '70'}, {'member_id': self.karl, 'amount': '-10'}], 'Richtung')):
            with self.assertRaises(ValueError, msg=message) as cm:
                self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '60', 'members': members}], 'Test')
            self.assertIn(message, str(cm.exception))
        self.assertEqual(self.s.rows("SELECT state FROM bank WHERE id=?", (bid,))[0]['state'], 'open')
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '60', 'members': [{'member_id': self.erika, 'amount': '24'},
                                                                                     {'member_id': self.karl, 'amount': '36'}]}], 'Test', learn=True)
        rows = self.s.rows("SELECT e.kv,e.credit,e.title,a.member_id,a.year,a.amount FROM entries e JOIN dues_assignments a ON a.entry_id=e.id ORDER BY e.id")
        self.assertEqual(rows, [{'kv': '5100', 'credit': 2400, 'title': 'H4', 'member_id': self.erika, 'year': 2026, 'amount': 2400},
                                {'kv': '5300', 'credit': 3600, 'title': 'H4', 'member_id': self.karl, 'year': 2026, 'amount': 3600}])
        self.assertEqual(beitraege.summary(self.s, 2026), {self.erika: 2400, self.karl: 3600})
        # Lernbeispiel mit Mitgliedern → nächster Vorschlag setzt sie wieder ein
        example = json.loads(self.s.db.execute('SELECT parts FROM booking_examples').fetchone()[0])
        self.assertEqual(example[0]['members'], [{'member_id': self.erika, 'cents': 2400}, {'member_id': self.karl, 'cents': 3600}])
        nxt = self.bank('60', day='2027-03-01')
        self.assertEqual(workflow.suggest(self.s, nxt)[0]['parts'][0]['members'][1]['member_id'], self.karl)

    def test_single_member_without_amount_and_budget(self):
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00', 'age_basis': 'jan1'}, 'Test')
        bid = self.bank('30')
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '30', 'members': [{'member_id': self.erika}]}], 'Test')
        altona = {b['code']: b for b in kvbudget.year_overview(self.s, 2026)}['5100']
        self.assertEqual((altona['paid'], altona['paid_assigned'], altona['share'], altona['ist_budget']), (3000, 3000, 750, 750))
        self.assertEqual(kvbudget.ledger(self.s, '5100', '2026-06-01')['available'], 750)
        self.assertEqual(kvbudget.ledger(self.s, '5100', '2026-06-01')['years'][0]['dues_paid'], 3000)
        # Storno: Zuordnung zählt nicht mehr, Zahlung wird erneut zugeordnet (jetzt Karl)
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(kvbudget.year_overview(self.s, 2026)[1]['paid'], 0)
        self.assertEqual(beitraege.summary(self.s, 2026), {})
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '30', 'members': [{'member_id': self.karl}]}], 'Test')
        view = {b['code']: b for b in kvbudget.year_overview(self.s, 2026)}
        self.assertEqual((view['5100']['paid'], view['5300']['paid']), (0, 3000))
        self.assertEqual(beitraege.entries(self.s, 2026, only_open=True), [])
        entry = self.s.db.execute('SELECT entry_id FROM dues_assignments ORDER BY id DESC').fetchone()[0]
        with self.assertRaises(ValueError) as cm:                                           # Zeile ist für Karls KV gebucht
            beitraege.assign(self.s, entry, [{'member_id': self.erika}], 'Test')
        self.assertIn('stornieren', str(cm.exception))


class AssignTests(Base):
    def test_legacy_entries_list_suggest_assign(self):
        e1 = self.legacy_8100('30', name='MUSTER, ERIKA', ref='Beitrag 2026')
        e2 = self.legacy_8100('60', name='Kreisverband Eimsbüttel', ref='Sammelüberweisung JU-1002 und weitere')
        e3 = self.legacy_8100('-30', day='2026-04-01', name='Bank', ref='Rücklastschrift Muster')
        items = {i['id']: i for i in beitraege.open_list(self.s, 2026)}
        self.assertEqual(set(items), {e1, e2, e3})
        self.assertEqual(items[e1]['suggestions'][0]['member_id'], self.erika)            # Name im Auftraggeber
        self.assertEqual(items[e2]['suggestions'][0]['member_id'], self.karl)             # Mitgliedsnummer im Verwendungszweck
        self.assertEqual(items[e1]['payer'], 'MUSTER, ERIKA')
        beitraege.assign(self.s, e1, [{'member_id': self.erika}], 'Test')
        with self.assertRaises(ValueError):
            beitraege.assign(self.s, e2, [{'member_id': self.karl, 'amount': '30'}], 'Test')   # Summe passt nicht
        beitraege.assign(self.s, e2, [{'member_id': self.karl, 'amount': '30'}, {'member_id': self.erika, 'amount': '30'}], 'Test')
        beitraege.assign(self.s, e3, [{'member_id': self.erika}], 'Test')                # Rücklastschrift mindert
        self.assertEqual(beitraege.summary(self.s, 2026), {self.erika: 3000, self.karl: 3000})
        self.assertEqual(beitraege.open_list(self.s, 2026), [])
        self.assertEqual(kvbudget.dues_paid(self.s, '5100'), {2026: 3000})
        # Zuordnung ändern: ersetzt die alte, Protokoll mit vorher/nachher
        beitraege.assign(self.s, e1, [{'member_id': self.karl}], 'Test')
        self.assertEqual(beitraege.summary(self.s, 2026), {self.erika: 0, self.karl: 6000})
        detail = json.loads(self.s.rows("SELECT detail FROM audit WHERE action='Beitragszuordnung geändert'")[0]['detail'])
        self.assertEqual((detail['vorher'][0]['id'], detail['members'][0]['id']), (self.erika, self.karl))
        self.assertEqual(kvbudget.detail(self.s, '5300', 2026)['paying_members'], 1)

    def test_assign_unique_suggestions_in_bulk(self):
        self.s.member({**BASE, 'number': 'JU-1003', 'first_name': 'Erik', 'last_name': 'Muster', 'kv': '5600'}, 'Test')
        e1 = self.legacy_8100('30', name='Karl Beispiel', ref='Beitrag')              # eindeutig: Karl
        e2 = self.legacy_8100('30', name='Erika und Erik Muster', ref='Beitrag Familie')   # Erika oder Erik: nicht eindeutig
        e3 = self.legacy_8100('30', name='Unbekannt', ref='Beitrag')                   # kein Vorschlag
        tie = {i['id']: i for i in beitraege.open_list(self.s, 2026)}[e2]['suggestions']
        self.assertEqual((len(tie), tie[0]['score'] == tie[1]['score']), (2, True))
        self.assertEqual(beitraege.assign_suggested(self.s, 2026, 'Test'), 1)
        self.assertEqual({i['id'] for i in beitraege.open_list(self.s, 2026)}, {e2, e3})
        self.assertEqual(beitraege.summary(self.s, 2026), {self.karl: 3000})
        detail = json.loads(self.s.rows("SELECT detail FROM audit WHERE action='Beiträge nach Vorschlag zugeordnet'")[0]['detail'])
        self.assertEqual((detail['anzahl'], detail['zuordnungen'][0]['entry']), (1, e1))
        self.assertEqual(beitraege.assign_suggested(self.s, 2026, 'Test'), 0)

    def test_bulk_skips_other_kv_and_partial_numbers(self):
        self.s.member({**BASE, 'number': '4711', 'first_name': 'Ole', 'last_name': 'Nummer', 'kv': '5100'}, 'Test')
        bid = self.bank('30', day='2026-02-01', name='Erika Muster', ref='Beitrag')
        with self.s.db:                                                                    # Zeile von vor 0.22 mit Bereich KV 5300
            self.s.journal('2026-02-01', 'Beitrag', bank_source('bank', {'id': bid, 'reversals': 0}),
                           [{'account': '1420', 'kv': 'LV', 'debit': 3000, 'credit': 0}, {'account': '8100', 'kv': '5300', 'debit': 0, 'credit': 3000, 'title': 'H4'}])
            self.s.db.execute("UPDATE bank SET state='booked' WHERE id=?", (bid,))
        partial = self.legacy_8100('30', name='Kasse', ref='Mitgl 47112 Beitrag')         # 4711 nur Teil einer längeren Nummer
        items = {i['id']: i for i in beitraege.open_list(self.s, 2026)}
        self.assertEqual(items[partial]['suggestions'], [])
        self.assertEqual(beitraege.assign_suggested(self.s, None, 'Test'), 0)              # Erika (5100) passt nicht zum Bereich 5300
        self.assertEqual(len(beitraege.open_list(self.s)), 2)

    def test_cutoff_and_payment_year(self):
        import berichte
        e = self.legacy_8100('400', day='2026-04-15')
        beitraege.assign(self.s, e, [{'member_id': self.erika}], 'Test')
        self.assertEqual(kvbudget.ledger(self.s, '5100', '2026-03-31', until='2026-03-31')['available'], 0)   # Stand März: noch nicht gezahlt
        self.assertEqual(kvbudget.ledger(self.s, '5100', '2026-04-30', until='2026-04-30')['available'], 10000)
        march = {k['code']: k for k in berichte.monthly(self.s, '2026-03-31')['kv']}['5100']
        self.assertEqual((march['available'], march['paid'], march['ist_budget']), (0, 0, 0))
        # Beitragsjahr 2026 (Excel-Blatt), gezahlt im Januar 2027: zählt nicht, steht unter „Jahreszuordnung klären“
        late = self.legacy_8100('30', day='2027-01-10', year=2026)
        beitraege.assign(self.s, late, [{'member_id': self.karl}], 'Test')
        self.assertEqual(kvbudget.dues_paid(self.s, '5300'), {})
        self.assertEqual([r['amount'] for r in beitraege.year_mismatch(self.s)], [3000])
        self.assertIn('JU-1002', self.s.snapshot(2026)['unallocated_dues'][0]['number'])
        self.assertEqual(kvbudget.detail(self.s, '5300', 2026)['payments'], [])

    def test_only_bank_payments_on_8100(self):
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00', 'age_basis': 'jan1'}, 'Test')
        iid = self.s.issue_dues(2026, [self.erika], 'Test')[0]                               # Sollbuchung der Beitragsrechnung
        soll = self.s.db.execute("SELECT e.id FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account='8100' AND j.source=?",
                                 (f'invoice:{iid}',)).fetchone()[0]
        self.assertEqual(beitraege.open_list(self.s), [])
        with self.assertRaises(ValueError):
            beitraege.assign(self.s, soll, [{'member_id': self.erika}], 'Test')
        other = self.s.db.execute("SELECT id FROM entries WHERE account='1500'").fetchone()[0]
        with self.assertRaises(ValueError):
            beitraege.assign(self.s, other, [{'member_id': self.erika}], 'Test')


class ExternalTests(Base):
    """Zahlungen von Personen, die nicht (mehr) in der Mitgliederliste stehen (z. B. ausgetreten, Daten gelöscht)."""
    EX = {'name': 'Otto Ehemalig', 'number': 'JU-0999', 'kv': '5300', 'reason': beitraege.EXTERNAL_REASON}

    def external(self, year=None):
        """Zuordnungen an Personen außerhalb der Mitgliederliste (ohne stornierte Buchungen)."""
        return [m for x in beitraege.entries(self.s, year) for m in x['members'] if m.get('external')]

    def test_book_income_and_refund_for_former_member(self):
        bid = self.bank('90', name='Otto Ehemalig', ref='Beitrag 2026')
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '90', 'members': [{'member_id': self.erika, 'amount': '30'},
                                                                                     {'external': self.EX, 'amount': '60'}]}], 'Test', learn=True)
        rows = self.s.rows('SELECT e.kv,e.credit,x.name,x.number,x.year,x.amount,x.reason FROM entries e JOIN dues_external x ON x.entry_id=e.id')
        self.assertEqual(rows, [{'kv': '5300', 'credit': 6000, 'name': 'Otto Ehemalig', 'number': 'JU-0999', 'year': 2026, 'amount': 6000,
                                 'reason': beitraege.EXTERNAL_REASON}])
        self.assertEqual(beitraege.summary(self.s, 2026), {self.erika: 3000})              # kein Beitragskonto für Externe
        booked = json.loads(self.s.rows("SELECT detail FROM audit WHERE action='Bankumsatz verbucht'")[0]['detail'])
        self.assertNotIn('Otto', json.dumps(booked))                                           # Protokoll ohne Namen außerhalb der Liste
        self.assertEqual(booked['parts'][0]['members'][1], {'id': 'extern', 'nummer': 'JU-0999', 'amount': '60'})
        self.assertEqual(kvbudget.dues_paid(self.s, '5300'), {2026: 6000})                 # zählt aber für den Anteil des KV
        self.assertEqual(beitraege.entries(self.s, 2026, only_open=True), [])
        ext = [m for x in beitraege.entries(self.s, 2026) for m in x['members'] if m.get('external')]
        self.assertEqual([(m['name'], m['amount'], m['member_id']) for m in ext], [('Otto Ehemalig', 6000, None)])
        detail = kvbudget.detail(self.s, '5300', 2026)
        self.assertEqual(([p['name'] for p in detail['payments']], detail['paying_members']), (['Otto Ehemalig'], 0))
        self.assertEqual(beitraege.learned(self.s), {})                                    # nie als „gleicher Zahler“ lernen
        example = json.loads(self.s.db.execute('SELECT parts FROM booking_examples').fetchone()[0])
        self.assertEqual(example[0]['members'][1], {'external': {**self.EX}, 'cents': 6000})
        self.assertEqual(len(self.external(2026)), 1)
        import mitgliederkonto
        rec = mitgliederkonto.reconciliation(self.s, 2026)
        self.assertEqual((rec['total'], rec['assigned'], rec['external'], rec['open']), (9000, 9000, 6000, 0))
        # Rückzahlung an die Person (Ausgabe auf 8100)
        out = self.bank('-60', day='2026-05-02', name='Otto Ehemalig', ref='Erstattung Beitrag')
        self.s.book(out, [{'account': '8100', 'kv': 'LV', 'amount': '-60', 'members': [{'external': self.EX}]}], 'Test')
        self.assertEqual(kvbudget.dues_paid(self.s, '5300'), {2026: 0})                     # 60 − 60
        self.assertEqual(len(self.external(2026)), 2)
        # Storno: Externe zählen nicht mehr
        self.s.reverse_bank(out, 'Test')
        self.assertEqual(kvbudget.dues_paid(self.s, '5300'), {2026: 6000})
        self.assertEqual(len(self.external(2026)), 1)

    def test_assign_existing_entry_and_validation(self):
        e = self.legacy_8100('30', name='Unbekannt', ref='Beitrag')
        bad = ({**self.EX, 'name': ' '}, 'Namen'), ({**self.EX, 'reason': ''}, 'Grund'), ({**self.EX, 'kv': '9999'}, 'Kreisverband'), \
              ({**self.EX, 'number': 'JU-1001'}, 'gehört zu einem Mitglied'), ({**self.EX, 'number': 'ju 01001'}, 'gehört zu einem Mitglied'), ({**self.EX, 'number': '1001'}, 'gehört zu einem Mitglied'), \
              ({**self.EX, 'name': 'x' * 121}, 'zu lang'), ('Otto', 'fehlen')
        for ext, message in bad:
            with self.assertRaises(ValueError, msg=message) as cm:
                beitraege.assign(self.s, e, [{'external': ext}], 'Test')
            self.assertIn(message, str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            beitraege.assign(self.s, e, [{'external': self.EX, 'amount': '15'}, {'external': {**self.EX, 'name': 'otto  EHEMALIG'}, 'amount': '15'}], 'Test')
        self.assertIn('doppelt', str(cm.exception))
        beitraege.assign(self.s, e, [{'external': {**self.EX, 'number': ''}}], 'Test')
        self.assertEqual(beitraege.open_list(self.s, 2026), [])
        self.assertEqual(kvbudget.dues_paid(self.s, '5300'), {2026: 3000})
        detail = json.loads(self.s.rows("SELECT detail FROM audit WHERE action='Mitgliedsbeitrag zugeordnet'")[0]['detail'])
        self.assertEqual(detail['members'], [{'id': 'extern', 'nummer': '', 'cents': 3000}])
        self.assertNotIn('Otto', json.dumps(detail))                                          # Protokoll ohne Namen
        # ändern: jetzt doch einem Mitglied der Liste – ersetzt die Zuordnung
        beitraege.assign(self.s, e, [{'member_id': self.erika}], 'Test')
        self.assertEqual((self.external(), kvbudget.dues_paid(self.s, '5300')), ([], {}))
        self.assertEqual(beitraege.summary(self.s, 2026), {self.erika: 3000})
        # Jahreszuordnung klären auch für Externe
        late = self.legacy_8100('30', day='2027-01-10', year=2026)
        beitraege.assign(self.s, late, [{'external': self.EX}], 'Test')
        self.assertIn('nicht in der Mitgliederliste', beitraege.year_mismatch(self.s)[0]['number'])

    def test_cleanup_removes_external(self):
        import bereinigung
        e = self.legacy_8100('30', day='2025-02-01', name='Unbekannt', ref='Beitrag')
        beitraege.assign(self.s, e, [{'external': self.EX}], 'Test')
        bereinigung.purge_before(self.s, '2026-01-01', 'Test')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM dues_external').fetchone()[0], 0)


class PlanTests(Base):
    def test_single_plan_value(self):
        kvbudget.set_plan(self.s, 2026, '5100', '1.200', 'Test')
        self.assertEqual(self.s.db.execute("SELECT amount FROM household_plan WHERE year=2026 AND title='H50'").fetchone()[0], -120000)
        self.assertEqual({b['code']: b for b in kvbudget.year_overview(self.s, 2026)}['5100']['plan'], 120000)
        kvbudget.set_plan(self.s, 2026, '5100', '', 'Test')
        self.assertIsNone({b['code']: b for b in kvbudget.year_overview(self.s, 2026)}['5100']['plan'])
        for kv, amount in (('LV', '10'), ('5100', '-5'), ('XX', '1')):
            with self.assertRaises(ValueError):
                kvbudget.set_plan(self.s, 2026, kv, amount, 'Test')

    def test_old_working_budgets_merged_once(self):
        path = Path(self.tmp.name) / 't.db'
        with self.s.db:
            self.s.db.execute("DELETE FROM settings WHERE key='kv_plans_merged'")
            self.s.db.execute('INSERT INTO budgets VALUES(2026,?,50000)', ('5100',))
            self.s.db.execute('INSERT INTO budgets VALUES(2026,?,30000)', ('5200',))
            self.s.db.execute("INSERT INTO household_plan VALUES(2026,'H51',-20000,'Excel')")
        self.s.db.close()
        self.s = Store(path)
        plan = {r['title']: r['amount'] for r in self.s.rows('SELECT title,amount FROM household_plan WHERE year=2026')}
        self.assertEqual((plan['H50'], plan['H51']), (-50000, -30000))                    # bisher wirksamer Wert (Arbeitsbudget) bleibt
        detail = json.loads(self.s.rows("SELECT detail FROM audit WHERE action LIKE 'KV-Planwerte%'")[0]['detail'])
        self.assertEqual(detail['haushaltswert_ersetzt'], [{'year': 2026, 'kv': '5200', 'arbeitsbudget': 30000, 'haushalt_vorher': 20000}])
        with self.s.db:
            self.s.db.execute("DELETE FROM household_plan WHERE title='H50'")
        self.s.db.close()
        self.s = Store(path)                                                              # nur einmal
        self.assertIsNone(self.s.db.execute("SELECT 1 FROM household_plan WHERE title='H50'").fetchone())


if __name__ == '__main__':
    unittest.main()
