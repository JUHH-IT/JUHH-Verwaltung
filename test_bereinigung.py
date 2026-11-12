"""Löschen von Daten vor einem Stichtag (bereinigung.py) – nur erfundene Testdaten."""
import os
os.environ['JU_FINANZEN_OHNE_SCHLUESSELBUND'] = '1'
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import bereinigung
import uebernahme
from core import Store
from test_uebernahme import master_xlsx


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 't.db'
        self.s = Store(self.path)
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, day, amount, ref):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};Beispiel;{ref};r{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'a{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def test_purge_2024(self):
        old = self.bank('2024-12-30', '-12,34', 'GEHEIM-ALT-2024 Druck')
        self.s.book(old, [{'account': '4290', 'kv': 'LV', 'amount': '-12,34'}], 'Test', confirm_no_bill=True)
        parked = self.bank('2024-11-04', '5', 'Parken alt')
        self.s.db.execute("UPDATE bank SET state='parked' WHERE id=?", (parked,))
        self.s.journal('2024-11-04', 'Geparkt', f'bank:{parked}', [{'account': '1420', 'kv': 'LV', 'debit': 500, 'credit': 0},
                                                                    {'account': '1370', 'kv': 'LV', 'debit': 0, 'credit': 500}], year=2024)
        self.s.db.commit()
        self.s.db.execute("INSERT INTO audit(time,actor,action,detail) VALUES('t','Test','Bankumsatz auf 1370 geparkt',?)", (json.dumps({'bank_id': parked, 'reason': 'GEHEIM-GRUND-2024'}),))
        self.s.db.execute("INSERT INTO household_plan VALUES(2024,'H4',1000,'Test')")
        self.s.db.execute("INSERT INTO household_plan VALUES(2025,'H4',2000,'Test')")
        self.s.db.commit()
        new = self.bank('2025-01-02', '-7', 'Neu 2025')
        self.s.book(new, [{'account': '4225', 'kv': 'LV', 'amount': '-7'}], 'Test', confirm_no_bill=True)
        self.s.audit('Test', 'Vorkontierte Excel übernommen', {'filename': 'm.xlsx', 'sheets': ['Umsatz 2026', 'Umsatz 2024'], 'counts': {'neu': 3},
                     'uebersprungen': [{'blatt': 'Umsatz 2024', 'datum': '2024-12-31', 'betrag': -584}, {'blatt': 'Umsatz 2026', 'datum': '2026-01-27', 'betrag': -3230}]})
        self.s.db.commit()
        result = bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertEqual((result['umsaetze'], result['buchungen'], result['protokolleintraege_bereinigt'], result['planwerte']), (2, 2, 1, 1))
        self.assertGreaterEqual(result['protokolleintraege_entfernt'], 2)                 # verbucht + geparkt zu gelöschten Umsätzen
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT year FROM household_plan')], [2025])
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT id FROM bank')], [new])
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM journals WHERE day<'2025-01-01'").fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)
        self.assertEqual(self.s.data_start(), '2025-01-01')
        detail = json.loads(self.s.db.execute("SELECT detail FROM audit WHERE action='Vorkontierte Excel übernommen'").fetchone()[0])
        self.assertEqual((detail['sheets'], [r['datum'] for r in detail['uebersprungen']]), (['Umsatz 2026'], ['2026-01-27']))
        self.assertNotIn('counts', detail)
        self.assertIn('Daten vor dem 2025-01-01 endgültig gelöscht', [r[0] for r in self.s.db.execute('SELECT action FROM audit')])
        self.assertEqual(self.s.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.s.db.close()
        content = self.path.read_bytes()
        self.assertNotIn(b'GEHEIM-ALT-2024', content)                               # auch nicht im freien Speicher der Datei
        self.assertNotIn(b'GEHEIM-GRUND-2024', content)                             # Klärungsgrund im Protokoll ebenfalls weg
        self.s = Store(self.path)
        # künftige Importe übernehmen nichts vor dem Datenbeginn
        raw = 'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2024-12-30;-12,34;Beispiel;GEHEIM-ALT-2024 Druck;x1\n2025-03-01;3;Beispiel;Neu;x2\n'.encode()
        preview = self.s.preview_bank(raw, 'export.csv')
        self.assertEqual(([r['booked'] for r in preview['rows']], preview['before_start']), (['2025-03-01'], 1))

    def test_purge_with_dues_assignment(self):
        mid = self.s.member(dict(number='M1', first_name='Erika', last_name='Muster', street='Weg 1', zip='20095', city='Hamburg',
                                 birth='2000-01-01', joined='2020-01-01', kv='5100'), 'Test')
        old = self.bank('2024-06-01', '30', 'Beitrag alt')
        self.s.book(old, [{'account': '8100', 'kv': 'LV', 'amount': '30', 'members': [{'member_id': mid}]}], 'Test')
        new = self.bank('2025-06-01', '30', 'Beitrag neu')
        self.s.book(new, [{'account': '8100', 'kv': 'LV', 'amount': '30', 'members': [{'member_id': mid}]}], 'Test')
        import beitraege
        legacy = self.bank('2024-07-01', '30', 'Beitrag ohne Mitglied alt')
        with self.s.db:
            self.s.journal('2024-07-01', 'Beitrag', f'bank:{legacy}', [{'account': '1420', 'kv': 'LV', 'debit': 3000, 'credit': 0},
                           {'account': '8100', 'kv': 'LV', 'debit': 0, 'credit': 3000, 'title': 'H4'}], year=2024)
            self.s.db.execute("UPDATE bank SET state='booked' WHERE id=?", (legacy,))
        entry = self.s.db.execute("SELECT MAX(id) FROM entries WHERE account='8100'").fetchone()[0]
        beitraege.assign(self.s, entry, [{'member_id': mid}], 'Test')
        with self.s.db:
            self.s.audit('Test', 'Beiträge nach Vorschlag zugeordnet', {'jahr': None, 'anzahl': 2, 'zuordnungen': [{'entry': entry, 'member': mid, 'cents': 3000},
                                                                                                            {'entry': 999999, 'member': mid, 'cents': 1}]})
        bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT year FROM dues_assignments')], [2025])
        self.assertEqual(self.s.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM audit WHERE action='Mitgliedsbeitrag zugeordnet'").fetchone()[0], 0)
        bulk = json.loads(self.s.db.execute("SELECT detail FROM audit WHERE action='Beiträge nach Vorschlag zugeordnet'").fetchone()[0])
        self.assertEqual((bulk['anzahl'], [z['entry'] for z in bulk['zuordnungen']]), (1, [999999]))

    def test_purge_with_sepa_collector(self):
        """Prüfbefund W2 (0.29): Sammelbuchung, die mit einer SEPA-Datei aufgeteilt wurde, lässt sich bereinigen."""
        import sepadatei
        from test_sepadatei import pain008
        mid = self.s.member(dict(number='A0000000001', first_name='Erika', last_name='Muster', street='Weg 1', zip='20095', city='Hamburg',
                                 birth='2000-01-01', joined='2020-01-01', kv='5100'), 'Test')
        bid = self.bank('2024-03-05', '60', 'SEPA-Sammler')
        txs = [('30.00', 'E. Muster', 'DE89370400440532013000', 'X-1', 'E-1', 'Beitrag A0000000001'),
               ('30.00', 'Otto Ehemalig', 'DE02120300000000202051', 'X-2', 'E-2', 'Beitrag')]
        prev = sepadatei.preview(self.s, pain008('2024-03-04', txs), 'alt.xml')
        ex = {'name': 'Otto Ehemalig', 'number': '', 'kv': '5100', 'reason': 'ausgetreten'}
        fid = sepadatei.apply(self.s, prev, 0, bid, {'1': {'external': ex}}, 'Test')['id']
        sepadatei.set_stored(self.s, fid, 'SEPA-2024-03-04-test.xml')
        result = bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertEqual(result['sepa_belege_von_hand_loeschen'], ['SEPA-2024-03-04-test.xml'])
        for table in ('sepa_files', 'dues_assignments', 'dues_external', 'bank'):
            self.assertEqual(self.s.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM audit WHERE action='SEPA-Sammler aufgeteilt'").fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_purge_with_cases(self):
        """Prüfbefund 0.31: Klärfälle gelöschter Zahlungen und ihr Protokoll verschwinden, die späteren bleiben."""
        import klaerfaelle
        mid = self.s.member(dict(number='M1', first_name='Erika', last_name='Muster', street='Weg 1', zip='20095', city='Hamburg',
                                 birth='2000-01-01', joined='2020-01-01', kv='5100'), 'Test')
        entries = []
        for day in ('2024-06-01', '2024-07-01', '2025-06-01'):
            bid = self.bank(day, '30', 'Beitrag unklar')
            with self.s.db:
                self.s.journal(day, 'Beitrag', f'bank:{bid}', [{'account': '1420', 'kv': 'LV', 'debit': 3000, 'credit': 0},
                               {'account': '8100', 'kv': 'LV', 'debit': 0, 'credit': 3000, 'title': 'H4'}], year=int(day[:4]))
                self.s.db.execute("UPDATE bank SET state='booked' WHERE id=?", (bid,))
            entries.append(self.s.db.execute("SELECT MAX(id) FROM entries WHERE account='8100'").fetchone()[0])
        first = klaerfaelle.report(self.s, entries[0], {}, 'Test')
        self.assertEqual(klaerfaelle.report_open(self.s, None, 'Test'), 2)              # ein alter und ein neuer in einer Sammelmeldung
        klaerfaelle.update(self.s, first, {'status': 'rueckfrage', 'kv': '5100'}, 'Test')
        klaerfaelle.resolve(self.s, first, {'member_id': mid}, 'Test')
        bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT entry_id FROM dues_cases')], [entries[2]])
        self.assertEqual(self.s.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        left = {r[0]: json.loads(r[1]) for r in self.s.db.execute("SELECT action,detail FROM audit WHERE action LIKE 'Klärf%'")}
        self.assertEqual(list(left), ['Klärfälle gemeldet'])
        self.assertEqual((left['Klärfälle gemeldet']['anzahl'], [f['entry'] for f in left['Klärfälle gemeldet']['faelle']]), (1, [entries[2]]))
        self.assertEqual(len(klaerfaelle.items(self.s)), 1)

    def test_purge_removes_dues_targets_and_their_audit(self):
        import mitgliederkonto
        mid = self.s.member(dict(number='M1', first_name='Erika', last_name='Muster', street='Weg 1', zip='20095', city='Hamburg',
                                 birth='2000-01-01', joined='2020-01-01', kv='5100'), 'Test')
        with self.s.db:      # Festlegung für 2024 (vor 0.24 nicht möglich, hier direkt) und deren Protokoll
            self.s.db.execute("INSERT INTO dues_targets(member_id,year,amount,reason,created) VALUES(?,2024,0,'Grund alt','2024-12-01')", (mid,))
            self.s.audit('Test', 'Beitrags-Soll festgelegt', {'member': mid, 'number': 'M1', 'year': 2024, 'cents': 0, 'grund': 'Grund alt'})
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        mitgliederkonto.set_target(self.s, mid, 2025, '0', 'Grund neu', 'Test')
        bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT year FROM dues_targets')], [2025])
        reasons = [json.loads(r[0])['grund'] for r in self.s.db.execute("SELECT detail FROM audit WHERE action='Beitrags-Soll festgelegt'")]
        self.assertEqual(reasons, ['Grund neu'])

    def test_purge_with_rpj(self):
        import rpj
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        old_p = rpj.save_project(self.s, {'year': 2024, 'no': 1, 'name': 'Altprojekt', 'plan': {'a': '100'}}, 'Test')
        new_p = rpj.save_project(self.s, {'year': 2025, 'no': 1, 'name': 'Neues Projekt', 'plan': {'a': '100'}}, 'Test')
        rpj.save_year(self.s, {'year': 2024, 'contingent': '1000'}, 'Test')
        rpj.save_request(self.s, {'year': 2024, 'quarter': '4', 'amount': '500', 'requested_on': '2024-10-01'}, 'Test')
        a = self.bank('2024-11-01', '-40', 'Saal alt')
        self.s.book(a, [{'account': '4380', 'kv': 'LV', 'amount': '-40', 'rpj': {'target': f'p:{old_p}', 'category': 'a'}}], 'Test', confirm_no_bill=True)
        b = self.bank('2025-01-10', '-60', 'Saal Rechnung im Januar')
        self.s.book(b, [{'account': '4380', 'kv': 'LV', 'amount': '-60', 'rpj': {'target': f'p:{old_p}', 'category': 'a'}}], 'Test', confirm_no_bill=True)
        with self.assertRaises(ValueError) as cm:      # Buchung 2025 hängt am Projekt 2024
            bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertIn('RPJ-Zuordnungen', str(cm.exception))
        entry = self.s.db.execute("SELECT e.id FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account='4380' AND j.day='2025-01-10'").fetchone()[0]
        rpj.assign(self.s, entry, [{'target': f'p:{new_p}', 'category': 'a'}], 'Test')
        bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT year FROM rpj_projects')], [2025])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM rpj_requests').fetchone()[0] + self.s.db.execute('SELECT COUNT(*) FROM rpj_years').fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM rpj_links').fetchone()[0], 1)
        self.assertEqual(self.s.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertFalse([r for r in self.s.rows("SELECT detail FROM audit WHERE action LIKE 'RPJ-%'") if 'Altprojekt' in r['detail'] or '"jahr": 2024' in r['detail']])

    def test_purge_rpj_links_across_cutoff(self):
        import rpj
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        p25 = rpj.save_project(self.s, {'year': 2025, 'no': 1, 'name': 'Projekt 2025', 'plan': {'a': '100'}}, 'Test')
        dec = self.bank('2024-12-20', '-30', 'Anzahlung Dezember')
        self.s.book(dec, [{'account': '4380', 'kv': 'LV', 'amount': '-30', 'rpj': {'target': f'p:{p25}', 'category': 'a'}}], 'Test', confirm_no_bill=True)
        with self.assertRaises(ValueError) as cm:      # gelöschte Vorjahresbuchung gehört zum Projekt 2025: Zahlen 2025 würden sich ändern
            bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertIn('ab 2025', str(cm.exception))
        self.s.reverse_bank(dec, 'Test')                # storniert: zählt nicht, sperrt nicht mehr
        self.s.db.execute('DELETE FROM rpj_plan'); self.s.db.commit()
        p24 = rpj.save_project(self.s, {'year': 2024, 'no': 1, 'name': 'Projekt 2024'}, 'Test')
        jan = self.bank('2025-01-05', '-20', 'Rechnung Januar')
        self.s.book(jan, [{'account': '4380', 'kv': 'LV', 'amount': '-20', 'rpj': {'target': f'p:{p24}', 'category': 'a'}}], 'Test', confirm_no_bill=True)
        self.s.reverse_bank(jan, 'Test')               # stornierte Zuordnung zum Altprojekt sperrt nicht dauerhaft
        self.s.book(jan, [{'account': '4380', 'kv': 'LV', 'amount': '-20', 'rpj': {'target': f'p:{p25}', 'category': 'a'}}], 'Test', confirm_no_bill=True)
        bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT year FROM rpj_projects')], [2025])
        self.assertEqual(self.s.db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(rpj.project_view(self.s, p25)['project']['cost_ist'], 2000)

    def test_purge_rpj_without_old_bank_rows(self):
        import rpj
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        p24 = rpj.save_project(self.s, {'year': 2024, 'no': 1, 'name': 'Projekt 2024'}, 'Test')
        jan = self.bank('2025-01-05', '-20', 'Rechnung Januar')
        self.s.book(jan, [{'account': '4380', 'kv': 'LV', 'amount': '-20', 'rpj': {'target': f'p:{p24}', 'category': 'a'}}], 'Test', confirm_no_bill=True)
        with self.assertRaises(ValueError) as cm:      # keine Umsätze vor dem Stichtag – Sperre greift trotzdem
            bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertIn('vor dem Stichtag', str(cm.exception))

    def test_blocked_by_other_old_data(self):
        self.bank('2024-12-30', '-1', 'Alt')
        self.s.db.execute("INSERT INTO cash_donations(received_on,donor,amount,kv,receiver1,receiver2,receipt,created) VALUES('2024-12-01','Spender',100,'LV','A','B','Q1','x')")
        self.s.db.commit()
        with self.assertRaises(ValueError) as cm:
            bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertIn('cash_donations', str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            bereinigung.purge_before(self.s, '2025-07-01', 'Test')
        self.assertIn('1. Januar', str(cm.exception))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bank').fetchone()[0], 1)  # nichts gelöscht

    def test_excel_sheets_before_start_ignored(self):
        self.s.db.execute("INSERT INTO settings VALUES('data_start','2026-01-01')")
        self.s.db.commit()
        p = uebernahme.preview(self.s, master_xlsx(), 'master.xlsx')
        self.assertEqual(p['sheets'], ['Umsatz 2026'])
        self.assertTrue(all(r['year_sheet'] >= 2026 and r.get('booked', '9') >= '2026-01-01' for r in p['rows'] if r.get('booked')))
        self.assertIn('Vor dem Datenbeginn 01.01.2026 nicht übernommen: Umsatz 2025', ' '.join(p['notes']))


if __name__ == '__main__':
    unittest.main()
