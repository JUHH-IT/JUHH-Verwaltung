"""Klärfälle der Mitgliederverwaltung (0.31) und Schalter „Beitragsanteil automatisch“ – nur erfundene Testdaten."""
import json
import unittest

import beitraege
import klaerfaelle
import kvbudget
import mitgliederkonto
from test_beitraege import Base, BASE

EXT = {'name': 'Otto Ehemalig', 'number': '', 'kv': '5100', 'reason': 'ausgetreten'}


class CaseTests(Base):
    def cases(self, status=None):
        return [c for c in klaerfaelle.items(self.s) if status is None or c['status'] == status]

    def test_report_and_resolve_member(self):
        eid = self.legacy_8100('30', name='Malte Unbekannt', ref='Beitrag 2026')
        self.assertEqual(self.s.snapshot(2026)['dues_open'], 1)
        cid = klaerfaelle.report(self.s, eid, {'reason': 'Name nicht gefunden'}, 'Kasse')
        c = self.cases()[0]
        self.assertEqual((c['id'], c['amount'], c['name'], c['status'], c['booked'], c['origin']), (cid, 3000, 'Malte Unbekannt', 'offen', False, 'Kontoauszug'))
        self.assertRegex(c['number'], r'^K-\d{4}-0001$')
        snap = self.s.snapshot(2026)
        self.assertEqual((snap['dues_open'], snap['cases']['active'], snap['cases']['unassigned'], snap['cases']['sum']), (0, 1, 1, 3000))
        entry = beitraege.entries(self.s, 2026)[0]
        self.assertEqual((entry['open'], entry['in_case'], len(entry['cases']), entry['members']), (0, 3000, 1, []))
        self.assertEqual(len(beitraege.pending(self.s, 2026)), 1)
        rec = mitgliederkonto.reconciliation(self.s, 2026)
        self.assertEqual((rec['open'], rec['in_case'], rec['case_count'], rec['open_count']), (3000, 3000, 1, 1))
        self.assertEqual(kvbudget.dues_paid(self.s, '5100'), {})
        with self.assertRaises(ValueError) as cm:       # schon gemeldet
            klaerfaelle.report(self.s, eid, {}, 'Kasse')
        self.assertIn('schon vollständig', str(cm.exception))
        with self.assertRaises(ValueError) as cm:       # Buchhaltung ordnet nicht an der Mitgliederverwaltung vorbei zu
            beitraege.assign(self.s, eid, [{'member_id': self.erika}], 'Kasse')
        self.assertIn('Klärfall', str(cm.exception))
        self.assertEqual(klaerfaelle.resolve(self.s, cid, {'member_id': self.erika, 'note': 'zweiter Vorname fehlte'}, 'MV'), 'mitglied')
        entry = beitraege.entries(self.s, 2026)[0]
        self.assertEqual((entry['open'], entry['in_case'], entry['members'][0]['member_id'], entry['members'][0]['amount']), (0, 0, self.erika, 3000))
        self.assertEqual(kvbudget.dues_paid(self.s, '5100'), {2026: 3000})
        c = self.cases()[0]
        self.assertEqual((c['status'], c['result'], c['note']), ('geklaert', 'mitglied', 'zweiter Vorname fehlte'))
        self.assertEqual(self.s.snapshot(2026)['cases']['active'], 0)
        with self.assertRaises(ValueError):             # nicht zweimal
            klaerfaelle.resolve(self.s, cid, {'member_id': self.karl}, 'MV')
        audit = [json.loads(r['detail']) for r in self.s.rows("SELECT detail FROM audit WHERE action LIKE 'Klärfall%'")]
        self.assertTrue(audit and all('Malte' not in json.dumps(a, ensure_ascii=False) for a in audit))   # keine Namen im Protokoll

    def test_resolve_external_no_dues_and_inquiry(self):
        a = self.legacy_8100('30', name='Otto Ehemalig')
        b = self.legacy_8100('25', name='Fremde Firma', ref='Rechnung 12')
        ca, cb = klaerfaelle.report(self.s, a, {}, 'Kasse'), klaerfaelle.report(self.s, b, {}, 'Kasse')
        klaerfaelle.update(self.s, ca, {'status': 'rueckfrage', 'kv': '5100', 'note': 'KV gefragt'}, 'MV')
        c = next(x for x in self.cases() if x['id'] == ca)
        self.assertEqual((c['status'], c['inquiry_kv'], c['note']), ('rueckfrage', '5100', 'KV gefragt'))
        self.assertEqual(self.s.snapshot(2026)['cases']['rueckfrage'], 1)
        with self.assertRaises(ValueError):
            klaerfaelle.update(self.s, ca, {'status': 'rueckfrage', 'kv': 'XX'}, 'MV')
        with self.assertRaises(ValueError):
            klaerfaelle.resolve(self.s, ca, {}, 'MV')
        with self.assertRaises(ValueError):             # Person außerhalb der Liste braucht Grund
            klaerfaelle.resolve(self.s, ca, {'external': {**EXT, 'reason': ''}}, 'MV')
        self.assertEqual(klaerfaelle.resolve(self.s, ca, {'external': EXT}, 'MV'), 'ehemalig')
        self.assertEqual(kvbudget.dues_paid(self.s, '5100'), {2026: 3000})          # zählt jetzt für den Kreisverband
        c = next(x for x in self.cases() if x['id'] == ca)
        self.assertEqual((c['status'], c['booked'], c['kv']), ('geklaert', True, '5100'))
        self.assertEqual(len(self.s.rows('SELECT 1 FROM dues_cases')), 2)           # kein zweiter Klärfall für die neue Zuordnung
        self.assertEqual(klaerfaelle.resolve(self.s, cb, {'no_dues': True, 'note': 'ist eine Spende'}, 'MV'), 'kein_beitrag')
        entry = next(x for x in beitraege.entries(self.s, 2026) if x['id'] == b)
        self.assertEqual((entry['open'], entry['in_case'], entry['returned']), (2500, 0, [{'name': 'Fremde Firma', 'note': 'ist eine Spende'}]))
        self.assertEqual(self.s.snapshot(2026)['dues_open'], 1)                     # wieder Aufgabe der Buchhaltung
        klaerfaelle.report(self.s, b, {'name': 'doch Beitrag'}, 'Kasse')            # erneut melden geht
        self.assertEqual(len(self.cases('offen')), 1)
        old = next(x for x in self.cases() if x['id'] == cb)
        self.assertEqual((old['status'], old['result']), ('geklaert', 'kein_beitrag'))   # Rückgabe ist damit erledigt, Ergebnis bleibt
        klaerfaelle.resolve(self.s, self.cases('offen')[0]['id'], {'no_dues': True}, 'MV')
        beitraege.assign(self.s, b, [{'member_id': self.erika}], 'Kasse')           # … oder die Buchhaltung ordnet doch zu
        entry = next(x for x in beitraege.entries(self.s, 2026) if x['id'] == b)
        self.assertEqual((entry['open'], entry['returned']), (0, []))
        self.assertEqual(self.cases('zurueck'), [])

    def test_external_becomes_case_and_survives_reassign(self):
        eid = self.legacy_8100('60', name='Familie Ahrens')
        beitraege.assign(self.s, eid, [{'member_id': self.erika, 'amount': '30'}, {'external': EXT, 'amount': '30'}], 'Kasse')
        c = self.cases()[0]
        self.assertEqual((c['status'], c['booked'], c['amount'], c['name'], c['kv']), ('offen', True, 3000, 'Otto Ehemalig', '5100'))
        self.assertEqual(self.s.snapshot(2026)['cases'], {**self.s.snapshot(2026)['cases'], 'active': 1, 'unassigned': 0})
        self.assertEqual(beitraege.pending(self.s, 2026), [])                       # zählt schon – sperrt den Abschluss nicht
        with self.assertRaises(ValueError):             # gebucht: „kein Beitrag“ ändert die Buchhaltung
            klaerfaelle.resolve(self.s, c['id'], {'no_dues': True}, 'MV')
        self.assertEqual(klaerfaelle.resolve(self.s, c['id'], {'confirm': True}, 'MV'), 'ehemalig')
        # Buchhaltung speichert dieselbe Zuordnung erneut: der Klärfall bleibt geklärt
        beitraege.assign(self.s, eid, [{'member_id': self.erika, 'amount': '30'}, {'external': EXT, 'amount': '30'}], 'Kasse')
        c = self.cases()[0]
        self.assertEqual((len(self.cases()), c['status'], c['booked']), (1, 'geklaert', True))
        # … und ordnet dann doch ein Mitglied zu: Klärfall erledigt, nicht mehr gebucht
        beitraege.assign(self.s, eid, [{'member_id': self.erika, 'amount': '30'}, {'member_id': self.karl, 'amount': '30'}], 'Kasse')
        c = self.cases()[0]
        self.assertEqual((c['status'], c['booked']), ('geklaert', False))
        self.assertEqual(self.s.rows('SELECT 1 FROM dues_external'), [])

    def test_open_external_case_to_member(self):
        eid = self.legacy_8100('30', name='Karl Beispiel alt')
        beitraege.assign(self.s, eid, [{'external': {**EXT, 'kv': '5300'}}], 'Kasse')
        cid = self.cases()[0]['id']
        self.assertEqual(kvbudget.dues_paid(self.s, '5300'), {2026: 3000})
        self.assertEqual(klaerfaelle.resolve(self.s, cid, {'member_id': self.karl, 'new': True}, 'MV'), 'neu')
        self.assertEqual(self.s.rows('SELECT 1 FROM dues_external'), [])
        self.assertEqual(beitraege.by_member(self.s, self.karl)[0]['amount'], 3000)
        self.assertEqual(self.cases()[0]['booked'], False)
        # Bestand aus 0.29/0.30: Zuordnung ohne Klärfall wird beim Start nachgetragen
        other = self.legacy_8100('20', name='Alt Bestand')
        with self.s.db:
            self.s.db.execute("INSERT INTO dues_external(entry_id,name,number,kv,year,amount,reason,created,actor) VALUES(?,?,?,?,?,?,?,?,?)",
                              (other, 'Alt Bestand', 'JU-9', '5100', 2026, 2000, 'ausgetreten', '2026-11-01T10:00:00', 'Kasse'))
        klaerfaelle.init(self.s.db)
        klaerfaelle.init(self.s.db)                     # zweimal: kein doppelter Klärfall
        mine = [c for c in self.cases() if c['entry_id'] == other]
        self.assertEqual([(c['status'], c['booked'], c['info']) for c in mine], [('offen', True, 'Mitgliedsnummer JU-9')])

    def test_booking_with_case_part_and_closing_block(self):
        bid = self.bank('60', name='Sammelüberweisung')
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '60', 'members': [
            {'member_id': self.erika, 'amount': '30'}, {'case': {'name': 'Unbekannt, Tim', 'reason': 'Nummer unbekannt'}, 'amount': '30'}]}], 'Kasse', learn=True)
        lines = self.s.rows("SELECT kv,credit FROM entries WHERE account='8100' ORDER BY id")
        self.assertEqual(lines, [{'kv': '5100', 'credit': 3000}, {'kv': 'LV', 'credit': 3000}])
        c = self.cases()[0]
        self.assertEqual((c['amount'], c['name'], c['reason'], c['booked']), (3000, 'Unbekannt, Tim', 'Nummer unbekannt', False))
        self.assertNotIn('Unbekannt', self.s.rows("SELECT detail FROM audit WHERE action='Bankumsatz verbucht'")[0]['detail'])
        with self.assertRaises(ValueError) as cm:
            kvbudget.close_year(self.s, 2026, 'Kasse', today='2027-02-01')
        self.assertIn('Klärfall', str(cm.exception))
        # anderer Kreisverband geht (Zeile ohne Kreisverband); Storno der Buchung lässt den Klärfall verschwinden
        klaerfaelle.resolve(self.s, c['id'], {'member_id': self.karl}, 'MV')
        self.assertEqual(kvbudget.dues_paid(self.s, '5300'), {2026: 3000})
        kvbudget.close_year(self.s, 2026, 'Kasse', today='2027-02-01')
        bid = self.bank('30', day='2027-03-02', name='Noch einer')
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '30', 'members': [{'case': {'name': 'Noch einer'}}]}], 'Kasse')
        self.assertEqual(len(self.cases('offen')), 1)
        self.s.reverse_bank(bid, 'Kasse')
        self.assertEqual(self.cases('offen'), [])
        self.assertEqual(self.s.snapshot(2027)['cases']['active'], 0)

    def test_report_open_and_kv_conflict(self):
        a, b = self.legacy_8100('30', name='A Unbekannt'), self.legacy_8100('20', name='B Unbekannt', day='2025-05-05')
        self.assertEqual(klaerfaelle.report_open(self.s, 2026, 'Kasse'), 1)
        self.assertEqual(klaerfaelle.report_open(self.s, None, 'Kasse'), 1)
        self.assertEqual(klaerfaelle.report_open(self.s, None, 'Kasse'), 0)
        self.assertEqual(sorted(c['entry_id'] for c in self.cases('offen')), sorted([a, b]))
        # Zeile ist schon für einen Kreisverband gebucht: Mitglied eines anderen Kreisverbands geht nicht
        with self.s.db:
            self.s.db.execute("UPDATE entries SET kv='5100' WHERE id=?", (a,))
        cid = next(c['id'] for c in self.cases() if c['entry_id'] == a)
        with self.assertRaises(ValueError) as cm:
            klaerfaelle.resolve(self.s, cid, {'member_id': self.karl}, 'MV')
        self.assertIn('anderen Kreisverband', str(cm.exception))
        klaerfaelle.resolve(self.s, cid, {'member_id': self.erika}, 'MV')
        self.assertEqual(self.cases('offen')[0]['suggestions'], [])

    # ---- Prüfbefunde der unabhängigen Prüfung 0.31 -------------------------------------------------------------------------------

    def test_returned_payment_is_not_reported_again_in_bulk(self):
        a, b = self.legacy_8100('30', name='Fremde Firma'), self.legacy_8100('20', name='B Unbekannt')
        klaerfaelle.resolve(self.s, klaerfaelle.report(self.s, a, {}, 'Kasse'), {'no_dues': True}, 'MV')
        self.assertEqual(klaerfaelle.report_open(self.s, 2026, 'Kasse'), 1)         # nur b – a liegt als „kein Beitrag“ bei der Buchhaltung
        self.assertEqual([c['entry_id'] for c in self.cases('offen')], [b])
        self.assertEqual(len(next(x for x in beitraege.entries(self.s, 2026) if x['id'] == a)['returned']), 1)
        klaerfaelle.report(self.s, a, {}, 'Kasse')                                  # einzeln und bewusst geht es weiter
        self.assertEqual(len(self.cases('offen')), 2)

    def test_closed_budget_year_keeps_its_area(self):
        eid = self.legacy_8100('30', name='Karl Beispiel alt')
        beitraege.assign(self.s, eid, [{'external': {**EXT, 'kv': '5300'}}], 'Kasse')
        cid = self.cases()[0]['id']
        kvbudget.close_year(self.s, 2026, 'Kasse', today='2027-02-01')
        with self.assertRaises(ValueError) as cm:       # Mitglied aus 5100: würde den abgeschlossenen Anteil von 5300 nach 5100 schieben
            klaerfaelle.resolve(self.s, cid, {'member_id': self.erika}, 'MV')
        self.assertIn('abgeschlossen', str(cm.exception))
        self.assertEqual(kvbudget.dues_paid(self.s, '5300'), {2026: 3000})
        self.assertEqual(klaerfaelle.resolve(self.s, cid, {'member_id': self.karl}, 'MV'), 'mitglied')   # gleicher Kreisverband: nichts verschiebt sich
        self.assertEqual((kvbudget.dues_paid(self.s, '5300'), kvbudget.dues_paid(self.s, '5100')), ({2026: 3000}, {}))

    def test_same_external_person_is_merged(self):
        eid = self.legacy_8100('60', name='Familie Ahrens')
        beitraege.assign(self.s, eid, [{'external': EXT, 'amount': '30'}, {'case': {'name': 'otto ehemalig'}, 'amount': '30'}], 'Kasse')
        booked, waiting = [c for c in self.cases() if c['booked']], [c for c in self.cases() if not c['booked']]
        self.assertEqual((len(booked), len(waiting)), (1, 1))
        klaerfaelle.resolve(self.s, waiting[0]['id'], {'external': {**EXT, 'name': 'OTTO  Ehemalig'}}, 'MV')
        self.assertEqual(self.s.rows('SELECT name,amount FROM dues_external'), [{'name': 'Otto Ehemalig', 'amount': 6000}])   # eine Zeile, nicht zwei
        self.assertEqual([(c['amount'], c['status']) for c in self.cases() if c['booked']], [(6000, 'offen')])                # … mit einem Klärfall
        entry = beitraege.entries(self.s, 2026)[0]
        self.assertEqual((entry['open'], entry['in_case'], [m['amount'] for m in entry['members']]), (0, 0, [6000]))
        self.assertEqual(kvbudget.dues_paid(self.s, '5100'), {2026: 6000})
        beitraege.assign(self.s, eid, [{'external': EXT, 'amount': '60'}], 'Kasse')  # erneut speichern bleibt möglich
        self.assertEqual(len(self.s.rows('SELECT 1 FROM dues_external')), 1)

    def test_merged_person_keeps_one_case_per_line(self):
        """Nachprüfung 0.31 (kritisch): Nach dem Zusammenzählen hängt an der Zeile genau ein Klärfall – wird er einem Mitglied zugeordnet,
        geht der ganze Betrag mit, nichts steht wieder offen, kein Klärfall zeigt auf eine gelöschte Zeile."""
        eid = self.legacy_8100('100', name='Familie Ahrens')
        beitraege.assign(self.s, eid, [{'external': EXT, 'amount': '60'}, {'case': {'name': 'Otto Ehemalig'}, 'amount': '40'}], 'Kasse')
        a = next(c for c in self.cases() if c['booked'])
        b = next(c for c in self.cases() if not c['booked'])
        klaerfaelle.resolve(self.s, b['id'], {'external': EXT}, 'MV')
        self.assertEqual(self.s.rows('SELECT amount FROM dues_external'), [{'amount': 10000}])
        self.assertEqual(self.s.rows('SELECT id,amount,status,external_id IS NOT NULL linked FROM dues_cases ORDER BY id'),
                         [{'id': a['id'], 'amount': 10000, 'status': 'offen', 'linked': 1}, {'id': b['id'], 'amount': 4000, 'status': 'geklaert', 'linked': 0}])
        self.assertIn('zusammen_mit_fall', self.s.rows("SELECT detail FROM audit WHERE action='Klärfall geklärt'")[0]['detail'])
        kvbudget.close_year(self.s, 2026, 'Kasse', today='2027-02-01')
        klaerfaelle.resolve(self.s, a['id'], {'member_id': self.erika}, 'MV')        # gleicher Kreisverband: auch nach dem Abschluss
        entry = beitraege.entries(self.s, 2026)[0]
        self.assertEqual((entry['open'], entry['in_case'], [(m['member_id'], m['amount']) for m in entry['members']]), (0, 0, [(self.erika, 10000)]))
        self.assertEqual(kvbudget.dues_paid(self.s, '5100'), {2026: 10000})          # Anteil des abgeschlossenen Jahres unverändert
        self.assertEqual(self.s.rows('SELECT 1 FROM dues_external'), [])
        self.assertEqual(self.s.rows('SELECT 1 FROM dues_cases WHERE external_id IS NOT NULL'), [])
        # eine neue Zeile einer anderen Zahlung bekommt womöglich die frei gewordene Kennung – kein alter Klärfall darf daran hängen
        other = self.legacy_8100('25', name='Anderer Zahler', day='2027-03-01')
        beitraege.assign(self.s, other, [{'external': {**EXT, 'name': 'Ganz Anders', 'kv': '5300'}}], 'Kasse')
        linked = self.s.rows('SELECT entry_id,amount FROM dues_cases WHERE external_id IS NOT NULL')
        self.assertEqual(linked, [{'entry_id': other, 'amount': 2500}])

    def test_same_person_other_area_is_refused(self):
        eid = self.legacy_8100('60', name='Familie Ahrens')
        beitraege.assign(self.s, eid, [{'external': EXT, 'amount': '30'}, {'case': {'name': 'Otto Ehemalig'}, 'amount': '30'}], 'Kasse')
        b = next(c for c in self.cases() if not c['booked'])
        with self.assertRaises(ValueError) as cm:
            klaerfaelle.resolve(self.s, b['id'], {'external': {**EXT, 'kv': '5300'}}, 'MV')
        self.assertIn('anderen Kreisverband', str(cm.exception))
        self.assertEqual(len(self.s.rows('SELECT 1 FROM dues_external')), 1)
        for bad in ({**EXT, 'name': ['Liste']}, {**EXT, 'reason': 5}, {**EXT, 'kv': ['5100']}, {**EXT, 'number': {'a': 1}, 'name': ''}):
            with self.assertRaises(ValueError, msg=repr(bad)):   # keine Listen/Zahlen als Text speichern
                klaerfaelle.resolve(self.s, b['id'], {'external': bad}, 'MV')
        self.assertEqual(self.cases('offen')[0]['origin'], 'Kontoauszug')

    def test_origin_comes_from_the_booking_not_the_request(self):
        import kasse
        from datetime import date
        today = date.today().isoformat()
        kasse.set_opening(self.s, f'{today[:4]}-01-01', '100', 'Test')
        a = kasse.create(self.s, {'kind': 'in', 'amount': '30', 'day': today, 'text': 'Beitrag bar', 'name': 'Bar Zahler'}, 'Test')
        self.s.book(a['id'], [{'account': '8100', 'kv': 'LV', 'amount': '30', 'members': [{'case': {'name': 'Bar Zahler', 'origin': 'SEPA-Datei'}}]}], 'Kasse')
        b = kasse.create(self.s, {'kind': 'in', 'amount': '20', 'day': today, 'text': 'Beitrag bar', 'name': 'Otto Ehemalig'}, 'Test')
        self.s.book(b['id'], [{'account': '8100', 'kv': 'LV', 'amount': '20', 'members': [{'external': {**EXT, 'origin': 'SEPA-Datei'}}]}], 'Kasse')
        eid = self.legacy_8100('10', name='Konto Zahler')
        beitraege.assign(self.s, eid, [{'external': {**EXT, 'name': 'Konto Zahler', 'origin': 'SEPA-Datei'}}], 'Kasse')
        self.assertEqual(sorted((c['name'], c['origin']) for c in self.cases()),
                         [('Bar Zahler', 'Kasse'), ('Konto Zahler', 'Kontoauszug'), ('Otto Ehemalig', 'Kasse')])

    def test_reuse_follows_person_and_area_not_amount(self):
        eid = self.legacy_8100('60', name='Familie Ahrens')
        beitraege.assign(self.s, eid, [{'member_id': self.erika, 'amount': '30'}, {'external': EXT, 'amount': '30'}], 'Kasse')
        cid = self.cases()[0]['id']
        klaerfaelle.resolve(self.s, cid, {'confirm': True}, 'MV')
        # anderer Betrag, dieselbe Person: bleibt geprüft, der Klärfall zeigt den neuen Betrag
        beitraege.assign(self.s, eid, [{'member_id': self.erika, 'amount': '40'}, {'external': EXT, 'amount': '20'}], 'Kasse')
        self.assertEqual([(c['id'], c['status'], c['amount'], c['booked']) for c in self.cases()], [(cid, 'geklaert', 2000, True)])
        # anderer Kreisverband: zählt woanders – neu zu prüfen
        beitraege.assign(self.s, eid, [{'member_id': self.erika, 'amount': '40'}, {'external': {**EXT, 'kv': '5300'}, 'amount': '20'}], 'Kasse')
        new = self.cases('offen')
        self.assertEqual([(c['booked'], c['kv'], c['amount']) for c in new], [(True, '5300', 2000)])
        self.assertEqual(next(c for c in self.cases() if c['id'] == cid)['booked'], False)

    def test_request_cannot_set_mandate_origin_or_odd_types(self):
        eid = self.legacy_8100('30', name='Malte Unbekannt')
        cid = klaerfaelle.report(self.s, eid, {'name': ['x'], 'info': {'a': 1}, 'reason': 7, 'origin': 'SEPA-Datei', 'creditor': 'DE98ZZZ', 'mandate': 'M-1'}, 'Kasse')
        row = self.s.rows('SELECT name,info,reason,origin,creditor,mandate FROM dues_cases')[0]
        self.assertEqual((row['name'], row['origin'], row['creditor'], row['mandate']), ('Malte Unbekannt', 'Kontoauszug', '', ''))
        self.assertNotIn('{', row['info'] + row['reason'])
        item = klaerfaelle.clean({'name': 'Tim', 'origin': 'SEPA-Datei', 'creditor': 'DE98ZZZ', 'mandate': 'M-1'})['case']
        self.assertEqual((item['origin'], item['creditor'], item['mandate']), ('Kontoauszug', '', ''))
        item = klaerfaelle.clean(klaerfaelle.FromFile({'name': 'Tim', 'origin': 'SEPA-Datei', 'creditor': 'DE98ZZZ', 'mandate': 'M-1'}))['case']
        self.assertEqual((item['origin'], item['creditor'], item['mandate']), ('SEPA-Datei', 'DE98ZZZ', 'M-1'))
        for bad in (True, [cid], {'id': cid}, 1.5, None, '', 'abc', 0, -1, 10 ** 30):
            with self.assertRaises(ValueError, msg=repr(bad)):
                klaerfaelle.resolve(self.s, bad, {'member_id': self.erika}, 'MV')
            with self.assertRaises(ValueError, msg=repr(bad)):
                klaerfaelle.report(self.s, bad, {}, 'Kasse')
        for bad in (True, [self.erika], 'abc', 0, 10 ** 30):
            with self.assertRaises(ValueError, msg=repr(bad)):
                klaerfaelle.resolve(self.s, cid, {'member_id': bad}, 'MV')
        for data in ({'no_dues': 'yes'}, {'confirm': 1}, {'external': 'x'}, []):
            with self.assertRaises(ValueError, msg=repr(data)):
                klaerfaelle.resolve(self.s, cid, data, 'MV')
        with self.assertRaises(ValueError):
            klaerfaelle.update(self.s, cid, {'status': ['rueckfrage']}, 'MV')
        with self.assertRaises(ValueError):
            klaerfaelle.update(self.s, cid, {'status': 'rueckfrage', 'kv': ['5100']}, 'MV')
        klaerfaelle.update(self.s, cid, {'note': 12345}, 'MV')                       # keine Zeichenkette: Notiz bleibt
        self.assertEqual(self.cases()[0]['note'], '')
        self.assertEqual(self.cases()[0]['status'], 'offen')

    def test_reversed_entry_and_bulk_year(self):
        bid = self.bank('30', name='Noch einer')
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '30', 'members': [{'case': {'name': 'Noch einer'}}]}], 'Kasse')
        cid = self.cases('offen')[0]['id']
        self.s.reverse_bank(bid, 'Kasse')
        for bad in (True, 'x', [2026], 10 ** 30):
            with self.assertRaises(ValueError, msg=repr(bad)):
                klaerfaelle.report_open(self.s, bad, 'Kasse')
        for call in (lambda: klaerfaelle.update(self.s, cid, {'note': 'x'}, 'MV'), lambda: klaerfaelle.resolve(self.s, cid, {'member_id': self.erika}, 'MV')):
            with self.assertRaises(ValueError) as cm:   # stornierte Buchung: weder Notiz noch Klärung
                call()
            self.assertIn('storniert', str(cm.exception))

    def test_refund_case(self):
        eid = self.legacy_8100('-30', name='Otto Ehemalig', ref='Ruecklastschrift')
        cid = klaerfaelle.report(self.s, eid, {}, 'Kasse')
        c = self.cases()[0]
        self.assertEqual((c['amount'], self.s.snapshot(2026)['cases']['sum']), (-3000, -3000))
        entry = beitraege.entries(self.s, 2026)[0]
        self.assertEqual((entry['open'], entry['in_case']), (0, -3000))
        klaerfaelle.resolve(self.s, cid, {'member_id': self.erika}, 'MV')
        self.assertEqual(kvbudget.dues_paid(self.s, '5100'), {2026: -3000})


class SwitchTests(Base):
    def test_auto_share(self):
        eid = self.legacy_8100('100')
        beitraege.assign(self.s, eid, [{'member_id': self.erika}], 'Kasse')
        kvbudget.set_values(self.s, 2026, '5100', {'dues_share': '400'}, 'Kasse')
        row = lambda: next(r for r in kvbudget.year_overview(self.s, 2026) if r['code'] == '5100')   # noqa: E731
        self.assertEqual((row()['share'], row()['share_fixed'], row()['share_auto']), (40000, True, False))
        with self.assertRaises(ValueError):             # an ohne Jahr
            self.s.save_settings({'kv_share_auto': 'yes'}, 'Admin')
        self.s.save_settings({'kv_share_auto': 'yes', 'kv_share_auto_from': '2027'}, 'Admin')
        self.assertEqual((row()['share'], row()['share_auto']), (40000, False))     # 2026 bleibt beim festgestellten Betrag
        self.s.save_settings({'kv_share_auto_from': '2026'}, 'Admin')
        self.assertEqual((row()['share'], row()['share_fixed'], row()['share_auto']), (2500, False, True))   # 25 % von 100 €
        self.assertEqual(kvbudget.ledger(self.s, '5100', today='2026-12-01')['years'][0]['dues_share'], 2500)
        su = next(r for r in kvbudget.year_overview(self.s, 2026) if r['code'] == '5800')
        self.assertFalse(su['share_auto'])              # Schüler Union: festes Budget
        self.s.save_settings({'kv_share_auto': 'no'}, 'Admin')
        self.assertEqual(row()['share'], 40000)
        with self.assertRaises(ValueError):
            self.s.save_settings({'kv_share_auto': 'yes', 'kv_share_auto_from': '2019'}, 'Admin')

    def test_closed_year_is_protected(self):
        kvbudget.close_year(self.s, 2026, 'Kasse', today='2027-02-01')
        with self.assertRaises(ValueError) as cm:
            self.s.save_settings({'kv_share_auto': 'yes', 'kv_share_auto_from': '2026'}, 'Admin')
        self.assertIn('abgeschlossen', str(cm.exception))
        self.s.save_settings({'kv_share_auto': 'yes', 'kv_share_auto_from': '2027'}, 'Admin')
        self.assertEqual(self.s.config()['kv_share_auto_from'], '2027')

    def test_switch_cannot_reach_behind_a_closed_year(self):
        self.s.save_settings({'kv_share_auto': 'yes', 'kv_share_auto_from': '2026'}, 'Admin')
        kvbudget.close_year(self.s, 2026, 'Kasse', today='2027-02-01')
        for change in ({'kv_share_auto': 'no'}, {'kv_share_auto_from': '2027'}):   # beides würde 2026 und damit den Übertrag ändern
            with self.assertRaises(ValueError) as cm:
                self.s.save_settings(change, 'Admin')
            self.assertIn('nicht mehr ausschalten', str(cm.exception))             # die Meldung sagt, was gilt (Nachprüfung 0.31)
        self.assertEqual(kvbudget.auto_from(self.s.config()), 2026)

    def test_fixed_share_of_a_later_year_does_not_count(self):
        from datetime import date
        nxt = date.today().year + 1                      # Jahr nach heute: noch ohne Budgetkonto
        kvbudget.set_values(self.s, nxt, '5100', {'dues_share': '400'}, 'Kasse')
        row = lambda: next(r for r in kvbudget.year_overview(self.s, nxt) if r['code'] == '5100')   # noqa: E731
        self.assertEqual((row()['share'], row()['share_fixed']), (40000, True))
        self.s.save_settings({'kv_share_auto': 'yes', 'kv_share_auto_from': str(nxt)}, 'Admin')
        self.assertEqual((row()['share'], row()['share_fixed'], row()['share_auto']), (0, False, True))


if __name__ == '__main__':
    unittest.main()
