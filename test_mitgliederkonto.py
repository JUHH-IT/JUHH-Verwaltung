"""Mitgliederbuchhaltung (Beitragskonto je Mitglied) – nur erfundene Testdaten."""
import tempfile
import unittest
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

import beitraege
import berichte
import bereinigung
import kvbudget
import mitgliederkonto as mk
from core import Store, bank_source

IBAN = 'DE89 3704 0044 0532 0130 00'
BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg')
YEAR = date.today().year


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1\n20000 Hamburg', 'iban': IBAN, 'age_basis': 'jan1',
                              'invoice_ready': 'yes', 'tax_note': 'Fiktiver Hinweis', 'contact_email': 'kasse@example.org', 'fo_start': '2025'}, 'Test')
        with self.s.db:
            self.s.db.execute("INSERT INTO settings VALUES('data_start','2025-01-01')")
        self.n = 0
        # Erika: über 22 → 30 €; Karl: im 2. Quartal 2025 eingetreten (50 %), 19 Jahre → 20 €
        self.erika = self.member('JU-1001', 'Erika', 'Muster', '5100', birth='1995-03-01', joined='2020-01-01')
        self.karl = self.member('JU-1002', 'Karl', 'Beispiel', '5300', birth='2006-02-01', joined='2025-05-10')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, number, first, last, kv, birth='1990-01-01', joined='2020-01-01', **extra):
        return self.s.member({**BASE, 'number': number, 'first_name': first, 'last_name': last, 'kv': kv, 'birth': birth, 'joined': joined, **extra}, 'Test')

    def bank(self, amount, day, name='Erika Muster', ref='Mitgliedsbeitrag'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};{name};{ref};k{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'k{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def pay(self, member, amount, day, **kw):
        bid = self.bank(amount, day, **kw)
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': amount, 'members': [{'member_id': member}]}], 'Test')
        return bid

    def legacy(self, amount, day, name='Erika Muster', ref='Beitrag'):
        """8100-Buchung wie aus der Excel-Übernahme (ohne Mitglied)."""
        bid = self.bank(amount, day, name, ref)
        cents = int(round(float(amount.replace(',', '.')) * 100))
        with self.s.db:
            self.s.journal(day, ref, bank_source('bank', {'id': bid, 'reversals': 0}),
                           [{'account': '1420', 'kv': 'LV', 'debit': max(cents, 0), 'credit': max(-cents, 0)},
                            {'account': '8100', 'kv': 'LV', 'debit': max(-cents, 0), 'credit': max(cents, 0), 'title': 'H4'}], year=int(day[:4]))
            self.s.db.execute("UPDATE bank SET state='booked' WHERE id=?", (bid,))
        return self.s.db.execute("SELECT MAX(id) FROM entries WHERE account='8100'").fetchone()[0]

    def years(self, member, today):
        return {r['year']: r for r in mk.account(self.s, member, today)['years']}


class TargetTests(Base):
    def test_scale_from_2025_with_prorate(self):
        y = self.years(self.erika, '2026-06-01')
        self.assertEqual(sorted(y), [2025, 2026])                                 # ab Datenbeginn, nicht ab Eintritt 2020
        self.assertEqual((y[2025]['soll'], y[2026]['soll'], y[2025]['source']), (3000, 3000, 'calc'))
        self.assertIn('Staffel ab 22', y[2025]['basis'])
        k = self.years(self.karl, '2026-06-01')
        self.assertEqual((k[2025]['soll'], k[2025]['due'], k[2026]['soll']), (1000, '2025-05-10', 2000))   # 50 % von 20 €
        self.assertIn('2. Quartal', k[2025]['basis'])
        acc = mk.account(self.s, self.erika, '2026-06-01')
        self.assertEqual((acc['balance'], acc['arrears'], acc['arrear_years']), (-6000, 6000, [2025, 2026]))

    def test_payments_balance_credit_and_return_debit(self):
        self.pay(self.erika, '30', '2025-02-01')
        self.pay(self.erika, '60', '2026-01-15')                                   # 30 € zu viel → Guthaben
        acc = mk.account(self.s, self.erika, '2026-06-01')
        self.assertEqual((acc['balance'], acc['credit'], acc['arrears']), (3000, 3000, 0))
        self.assertEqual([(r['year'], r['paid'], r['open'], r['end_balance']) for r in acc['years']], [(2025, 3000, 0, 0), (2026, 6000, 0, 3000)])
        bid = self.bank('-60', '2026-02-01', ref='Rücklastschrift')
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '-60', 'members': [{'member_id': self.erika}]}], 'Test')
        acc = mk.account(self.s, self.erika, '2026-06-01')
        self.assertEqual((acc['balance'], acc['arrear_years']), (-3000, [2026]))
        self.assertEqual([x['balance'] for x in acc['postings']], [-3000, 0, -3000, 3000, -3000])   # Soll vor Zahlung am selben Tag
        # Stand eines früheren Tages: spätere Zahlungen zählen nicht
        self.assertEqual(mk.account(self.s, self.erika, '2025-12-31')['balance'], 0)

    def test_reversed_booking_not_counted(self):
        bid = self.pay(self.erika, '30', '2025-02-01')
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(mk.account(self.s, self.erika, '2025-12-31')['balance'], -3000)

    def test_invoice_defines_target_and_payment_counts(self):
        iid = self.s.issue_dues(2026, [self.erika], 'Test')[0]
        with self.s.db:
            self.s.db.execute('UPDATE invoices SET total=2500 WHERE id=?', (iid,))
        y = self.years(self.erika, '2026-06-01')
        self.assertEqual((y[2026]['soll'], y[2026]['source']), (2500, 'invoice'))
        number = self.s.rows('SELECT number FROM invoices WHERE id=?', (iid,))[0]['number']
        bid = self.bank('25', '2026-03-01', ref=number)
        self.s.book(bid, [{'invoice_id': iid, 'amount': '25'}], 'Test')
        y = self.years(self.erika, '2026-06-01')
        self.assertEqual((y[2026]['paid'], y[2026]['open']), (2500, 0))
        self.assertTrue(any(number in p['text'] for p in mk.account(self.s, self.erika, '2026-06-01')['postings']))

    def test_cancelled_invoice_needs_decision(self):
        iid = self.s.issue_dues(2026, [self.erika], 'Test')[0]
        with self.assertRaises(ValueError) as cm:                                    # offene Rechnung bestimmt das Soll
            mk.set_target(self.s, self.erika, 2026, '0', 'Befreiung', 'Test')
        self.assertIn('zuerst die rechnung stornieren', str(cm.exception).lower())
        self.s.reverse_invoice(iid, 'Test')
        acc = mk.account(self.s, self.erika, '2026-06-01')
        y = {r['year']: r for r in acc['years']}
        self.assertEqual((y[2026]['soll'], y[2026]['open']), (None, None))           # kein stiller Erlass
        self.assertIn('storniert – Soll festlegen', acc['issues'][0])
        mk.set_target(self.s, self.erika, 2026, '0', 'Befreiung (Beschluss)', 'Test')
        self.assertEqual(self.years(self.erika, '2026-06-01')[2026]['soll'], 0)

    def test_invoice_due_date_and_pilot(self):
        late = self.member('JU-1005', 'Nora', 'Spaet', '5100', birth='1990-01-01', joined=f'{YEAR}-08-15')
        iid = self.s.issue_dues(YEAR, [late], 'Test')[0]
        due = self.s.rows('SELECT due FROM invoices WHERE id=?', (iid,))[0]['due']
        row = self.years(late, date.today().isoformat())[YEAR]
        self.assertEqual((row['due'], row['due_now']), (due, due <= date.today().isoformat()))   # nicht vor dem Zahlungsziel fällig
        self.assertEqual(mk.account(self.s, late, date.today().isoformat())['arrears'], 0 if due > date.today().isoformat() else row['soll'])
        with self.s.db:
            self.s.db.execute("UPDATE invoices SET snapshot=json_set(snapshot,'$.pilot',1) WHERE id=?", (iid,))
        self.assertEqual(self.years(late, date.today().isoformat())[YEAR]['source'], 'calc')   # Pilotrechnung zählt nicht

    def test_fifo_over_year_change(self):
        # Nachzahlung für 2025 erst im März 2026 (Haushaltsjahr 2026): Rückstand 2025 gedeckt, 2026 offen
        self.pay(self.erika, '30', '2026-03-01')
        acc = mk.account(self.s, self.erika, '2026-06-01')
        y = {r['year']: r for r in acc['years']}
        self.assertEqual((y[2025]['paid'], y[2025]['open'], y[2026]['paid'], y[2026]['open']), (0, 0, 3000, 3000))
        self.assertEqual((acc['arrear_years'], y[2025]['end_balance'], y[2026]['end_balance']), ([2026], -3000, -3000))
        # Vorauszahlung im Dezember fürs Folgejahr: Guthaben, kein Rückstand
        self.pay(self.erika, '30', '2026-05-01')
        acc = mk.account(self.s, self.erika, '2026-06-01')
        self.assertEqual((acc['balance'], acc['arrear_years']), (0, []))

    def test_next_year_invoice_not_due_yet(self):
        with self.s.db:      # im Dezember gestellte Beitragsrechnung fürs Folgejahr (Satzung § 49 Abs. 4)
            self.s.db.execute("INSERT INTO invoices(number,kind,member_id,year,recipient,address,issued,due,total,kv,snapshot) "
                              "VALUES('B-X-1','dues',?,?,'Erika Muster','x',?,?,3000,'5100','{}')", (self.erika, YEAR + 1, f'{YEAR}-12-01', f'{YEAR + 1}-01-01'))
        acc = mk.account(self.s, self.erika, f'{YEAR}-12-10')
        row = {r['year']: r for r in acc['years']}[YEAR + 1]
        self.assertEqual((row['soll'], row['due_now']), (3000, False))
        self.assertEqual(acc['balance'], -3000 * (YEAR - 2024))                    # nur fällige Jahre

    def test_set_and_remove_target(self):
        with self.assertRaises(ValueError) as cm:
            mk.set_target(self.s, self.erika, 2025, '0', ' ', 'Test')
        self.assertIn('Grund', str(cm.exception))
        for amount, year, msg in (('-5', 2025, 'negativ'), ('5', 2024, 'Beitragsjahr'), ('5', YEAR + 2, 'Beitragsjahr')):
            with self.assertRaises(ValueError) as cm:
                mk.set_target(self.s, self.erika, year, amount, 'Test', 'Test')
            self.assertIn(msg, str(cm.exception))
        with self.assertRaises(ValueError) as cm:                                    # erst 2026 eingetreten: kein Beitragsjahr 2025
            mk.set_target(self.s, self.member('JU-1009', 'Ida', 'Neu', '5100', joined='2026-02-01'), 2025, '30', 'Test', 'Test')
        self.assertIn('Beitragsjahr 2026', str(cm.exception))
        mk.set_target(self.s, self.erika, 2025, '0', 'Beitragsbefreiung (Beschluss LaVo)', 'Test')
        mk.set_target(self.s, self.erika, 2025, '15', 'Erlass zur Hälfte', 'Test')       # ändert, legt nicht doppelt an
        y = self.years(self.erika, '2026-06-01')
        self.assertEqual((y[2025]['soll'], y[2025]['source'], y[2025]['basis']), (1500, 'set', 'festgelegt: Erlass zur Hälfte'))
        audit = self.s.rows("SELECT detail FROM audit WHERE action='Beitrags-Soll festgelegt' ORDER BY id")
        self.assertEqual(len(audit), 2)
        self.assertIn('"vorher"', audit[1]['detail'])
        mk.remove_target(self.s, self.erika, 2025, 'Test')
        self.assertEqual(self.years(self.erika, '2026-06-01')[2025]['soll'], 3000)
        with self.assertRaises(ValueError):
            mk.remove_target(self.s, self.erika, 2025, 'Test')

    def test_target_used_by_dues_run(self):
        mk.set_target(self.s, self.erika, 2026, '15', 'Erlass zur Hälfte', 'Test')
        mk.set_target(self.s, self.karl, 2026, '0', 'Befreiung', 'Test')
        self.assertEqual({r['id']: r['amount'] for r in self.s.dues_preview(2026) if r['id'] in (self.erika, self.karl)}, {self.erika: 1500, self.karl: None})
        ids = self.s.issue_dues(2026, [self.erika, self.karl], 'Test')
        self.assertEqual([r['total'] for r in self.s.rows('SELECT total FROM invoices')], [1500])   # keine Rechnung über 0 €
        self.assertEqual(len(ids), 1)

    def test_return_debit_without_payment(self):
        bid = self.bank('-30', '2026-02-01', ref='Rücklastschrift')
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '-30', 'members': [{'member_id': self.erika}]}], 'Test')
        acc = mk.account(self.s, self.erika, '2026-06-01')
        self.assertEqual((acc['arrears'], sum(r['open'] for r in acc['years'])), (9000, 9000))       # Offen = Rückstand

    def test_guest_and_dues_free(self):
        guest = self.member('JU-1006', 'Gina', 'Gast', '5100', guest='J')
        free = self.member('JU-1007', 'Fred', 'Frei', '5100', dues_free='J')
        g, f = self.years(guest, '2026-06-01'), self.years(free, '2026-06-01')
        self.assertEqual((g[2026]['soll'], g[2026]['basis']), (0, 'Gastmitglied (beitragsfrei)'))
        self.assertEqual((f[2026]['soll'], f[2026]['basis']), (0, 'beitragsfrei'))
        self.assertEqual(self.s.issue_dues(2026, [guest], 'Test'), [])                # Gastmitglied: keine Beitragsrechnung

    def test_issues_leaving_year_age_and_unconfirmed_basis(self):
        left = self.member('JU-1003', 'Lena', 'Austritt', '5100', left_on='2025-08-31')
        young = self.member('JU-1004', 'Tim', 'Jung', '5100', birth='2013-06-01', joined='2025-01-01')
        acc = mk.account(self.s, left, '2026-06-01')
        self.assertEqual([r['year'] for r in acc['years']], [2025])                  # nach dem Austrittsjahr kein Soll
        self.assertEqual(acc['years'][0]['soll'], None)
        self.assertIn('Austrittsjahr', acc['issues'][0])
        self.assertEqual(acc['balance'], 0)                                          # ungeklärtes Soll zählt nicht
        self.assertIn('unter 14', mk.account(self.s, young, '2026-06-01')['issues'][0])
        self.assertEqual({m['id'] for m in mk.issues(self.s, today='2026-06-01')}, {left, young})
        mk.set_target(self.s, left, 2025, '30', 'Austritt 31.08.2025 – voller Jahresbeitrag', 'Test')
        self.assertEqual(mk.account(self.s, left, '2026-06-01')['balance'], -3000)
        self.s.save_settings({'age_basis': ''}, 'Test')
        self.assertIn('Altersstichtag', mk.account(self.s, self.erika, '2026-06-01')['issues'][0])


class AgeAndDeleteTests(Base):
    """0.28 (Wunsch Lennert Gust 10.11.2026): Beitrag automatisch nach Alter anzeigen, Mitglieder löschen – nur ohne Buchhaltungsbezug."""
    def test_dues_by_age(self):
        r = self.s.dues_by_age('2008-01-01', '2022-01-01', 2026)                         # 18 → 19: Grenze der Staffel
        self.assertEqual([(x['year'], x['amount']) for x in r], [(2026, 1000), (2027, 2000)])
        self.assertIn('Staffel', r[0]['basis'])
        young = self.s.dues_by_age('2014-01-01', '2025-01-01', 2026)
        self.assertEqual(young[0]['amount'], None)
        self.assertIn('unter 14', young[0]['basis'])
        later = self.s.dues_by_age('1999-05-05', '2027-03-01', 2026)                      # Eintritt erst im Folgejahr, 1. Quartal: 75 %
        self.assertEqual((later[0]['amount'], later[0]['basis'], later[1]['amount']), (None, 'noch kein Mitglied', 2250))
        self.assertIn('75 %', later[1]['basis'])
        for birth, joined in (('31.02.2000', '2020-01-01'), ('2020-01-01', '2019-01-01')):
            with self.assertRaises(ValueError):
                self.s.dues_by_age(birth, joined, 2026)
        # wie im Beitragslauf: Gast, beitragsfrei, Austritt
        self.assertEqual(self.s.dues_by_age('1990-01-01', '2020-01-01', 2026, guest='1')[0], {'year': 2026, 'amount': None, 'basis': 'Gastmitglied – kein Beitrag'})
        self.assertEqual(self.s.dues_by_age('1990-01-01', '2020-01-01', 2026, dues_free='1')[0]['basis'], 'beitragsfrei')
        left = self.s.dues_by_age('1990-01-01', '2020-01-01', 2026, left_on='2026-06-30')
        self.assertIn('Austrittsjahr', left[0]['basis'])
        self.assertEqual(left[1]['basis'], 'ausgetreten')

    def test_delete_only_without_bookkeeping(self):
        import mitglieder
        free = self.member('JU-1010', 'Lia', 'Lösch', '5100', dues='25', joined=f'{YEAR}-01-01')
        mk.set_target(self.s, free, YEAR, '0', 'Befreiung', 'Test')
        self.assertTrue(self.s.db.execute('SELECT COUNT(*) FROM dues_rates WHERE member_id=?', (free,)).fetchone()[0])
        mitglieder.delete(self.s, free, 'Test')
        for table in ('members', 'dues_rates', 'dues_targets'):
            column = 'id' if table == 'members' else 'member_id'
            self.assertEqual(self.s.db.execute(f'SELECT COUNT(*) FROM {table} WHERE {column}=?', (free,)).fetchone()[0], 0, table)
        self.assertIn('JU-1010', self.s.rows("SELECT detail FROM audit WHERE action='Mitglied gelöscht'")[0]['detail'])
        self.pay(self.erika, '30', '2026-02-01')
        with self.assertRaises(ValueError) as cm:
            mitglieder.delete(self.s, self.erika, 'Test')
        self.assertIn('Beitragszahlung', str(cm.exception))
        self.assertIn('Austritt', str(cm.exception))
        self.s.issue_dues(2026, [self.karl], 'Test')
        with self.assertRaises(ValueError) as cm:
            mitglieder.check_delete(self.s, self.karl)
        self.assertIn('1 Rechnung ', str(cm.exception))
        with self.assertRaises(ValueError):
            mitglieder.check_delete(self.s, 99999)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM members').fetchone()[0], 2)

    def test_arrears_need_confirmation_and_ids_are_never_reused(self):
        import mitglieder
        late = self.member('JU-1011', 'Rolf', 'Rückstand', '5100', joined='2025-01-01')    # Soll nach Staffel, keine Zahlung
        with self.assertRaises(ValueError) as cm:
            mitglieder.delete(self.s, late, 'Test')
        self.assertIn('rückstand', str(cm.exception))
        mitglieder.delete(self.s, late, 'Test', arrears_ok=True)
        self.assertIn('"rueckstand_bestaetigt": true', self.s.rows("SELECT detail FROM audit WHERE action='Mitglied gelöscht'")[-1]['detail'])
        again = self.member('JU-1012', 'Nora', 'Neu', '5100')
        self.assertGreater(again, late)                                                     # id des gelöschten Mitglieds nicht neu vergeben
        raw = ('Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband\n'
               'JU-1013;Ina;Import;Weg 1;20000;Hamburg;01.01.1990;01.01.2020;Altona\n').encode()
        import mitglieder as mg
        mg.apply(self.s, mg.preview(self.s, raw, 'x.csv'), [2], 'Test')
        self.assertGreater(self.s.db.execute("SELECT id FROM members WHERE number='JU-1013'").fetchone()[0], again)
        # Import-Übernahme, nachdem das Mitglied gelöscht wurde: Fehler statt „aktualisiert“
        raw2 = raw.replace(b'Weg 1', b'Weg 2')
        prev = mg.preview(self.s, raw2, 'x.csv')
        mitglieder.delete(self.s, self.s.db.execute("SELECT id FROM members WHERE number='JU-1013'").fetchone()[0], 'Test', arrears_ok=True)
        with self.assertRaises(ValueError) as cm:
            mg.apply(self.s, prev, [2], 'Test')
        self.assertIn('inzwischen gelöscht', str(cm.exception))


class OverviewTests(Base):
    def test_overview_summary_arrears_and_reconciliation(self):
        self.pay(self.erika, '30', '2025-02-01')
        self.legacy('20', '2025-03-01', name='Unbekannt')
        rows = mk.overview(self.s, 2025, '2025-12-31')
        self.assertEqual((rows[self.erika]['soll'], rows[self.erika]['paid'], rows[self.erika]['balance']), (3000, 3000, 0))
        self.assertEqual((rows[self.karl]['soll'], rows[self.karl]['balance']), (1000, -1000))
        s = mk.summary(self.s, 2025, '2025-12-31')
        self.assertEqual((s['soll'], s['paid'], s['arrears'], s['arrears_count']), (4000, 3000, 1000, 1))
        self.assertEqual(s['reconciliation'], {'year': 2025, 'total': 5000, 'assigned': 3000, 'external': 0, 'open': 2000, 'open_count': 1, 'in_case': 0, 'case_count': 0,
                                               'invoice_paid': 0})
        self.assertEqual([r['id'] for r in mk.arrears(self.s, today='2025-12-31')], [self.karl])
        self.assertEqual(mk.arrears(self.s, kv='5100', today='2025-12-31'), [])
        csv = mk.overview_csv(self.s, 2025, today='2025-12-31').decode('utf-8-sig')
        self.assertIn('Saldo am 31.12.2025', csv.splitlines()[0])
        self.assertIn('JU-1002;Beispiel;Karl;', csv)
        self.assertIn(';-10,00;', csv)
        snap = self.s.snapshot(2025)
        erika = next(m for m in snap['members'] if m['id'] == self.erika)
        self.assertEqual((erika['paid'], erika['dues_soll']), (3000, 3000))
        self.assertIn('dues_book', snap)

    def test_pdfs(self):
        self.pay(self.erika, '30', '2025-02-01')
        import opliste
        for data in (berichte.member_pdf(self.s, self.erika, '2026-06-01'), opliste.pdf(self.s, None, None, '2026-06-01'),
                     opliste.pdf(self.s, '5300', None, '2026-06-01')):
            self.assertTrue(data.startswith(b'%PDF'))

    def test_monthly_report_and_audit_package(self):
        self.legacy('20', '2025-03-01', name='Unbekannt')
        m = berichte.monthly(self.s, '2025-12-31')
        self.assertEqual(m['dues']['reconciliation']['open_count'], 1)
        self.assertTrue(any('einem Mitglied zuordnen' in t for t in m['tasks']))
        self.assertTrue(berichte.monthly_pdf(self.s, '2025-12-31').startswith(b'%PDF'))
        buf = BytesIO()
        berichte.audit_package(self.s, 2025, Path(self.tmp.name), buf)
        with zipfile.ZipFile(buf) as z:
            names = z.namelist()
        self.assertTrue(any(n.endswith('Mitgliederkonten-2025.csv') for n in names))


class EnforcementTests(Base):
    def test_close_year_needs_all_dues_assigned(self):
        entry = self.legacy('30', '2025-03-01')
        with self.assertRaises(ValueError) as cm:
            kvbudget.close_year(self.s, 2025, 'Test', today='2026-02-01')
        self.assertIn('Beitragszahlungen des Jahres einem Mitglied zuordnen', str(cm.exception))
        beitraege.assign(self.s, entry, [{'member_id': self.erika}], 'Test')
        kvbudget.close_year(self.s, 2025, 'Test', today='2026-02-01')
        self.assertTrue(kvbudget.closings(self.s))

    def test_learned_payer_suggestion_only_single(self):
        first = self.legacy('30', '2025-03-01', name='Hans Vater', ref='Beitrag Kind')
        beitraege.assign(self.s, first, [{'member_id': self.karl}], 'Test')
        self.legacy('20', '2026-03-01', name='Hans Vater', ref='Beitrag')
        item = beitraege.open_list(self.s, 2026)[0]
        self.assertEqual(item['suggestions'], [{'member_id': self.karl, 'score': 3, 'name': False, 'learned': True}])
        self.assertIsNone(beitraege.unique_suggestion(item))                         # nie in der Sammelübernahme
        self.assertEqual(beitraege.assign_suggested(self.s, 2026, 'Test'), 0)
        # zahlt derselbe Zahler für zwei Mitglieder, gibt es keinen Vorschlag
        other = self.legacy('30', '2025-04-01', name='Hans Vater', ref='Beitrag')
        beitraege.assign(self.s, other, [{'member_id': self.erika}], 'Test')
        self.assertEqual(beitraege.open_list(self.s, 2026)[0]['suggestions'], [])


if __name__ == '__main__':
    unittest.main()
