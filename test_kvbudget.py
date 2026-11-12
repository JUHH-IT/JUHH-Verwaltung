"""KV-Budgets (Finanzordnung § 6) und Spenden (§ 2) – nur erfundene Testdaten."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import kvbudget as k
import workflow
from core import Store


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00', 'age_basis': 'jan1'}, 'Test')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, amount, day, ref='x'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};Test;{ref};k{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'k{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def book(self, amount, day, account, kv='5100', title='H5'):
        bid = self.bank(amount, day)
        self.s.book(bid, [{'account': account, 'kv': kv, 'title': title, 'amount': amount}], 'Test', confirm_no_bill=True)
        return bid

    def credit(self, cents, day, kv='5100'):
        """Gutschrift auf ein Ausgabenkonto (wie aus der Excel-Übernahme, z. B. Spende über das KV-Sammelkonto)."""
        with self.s.db:
            return self.s.journal(day, 'Gutschrift', f'test:{day}:{cents}', [{'account': '1420', 'kv': 'LV', 'debit': cents, 'credit': 0},
                                                                            {'account': 'KV', 'kv': kv, 'debit': 0, 'credit': cents, 'title': 'H50'}])

    def dues_paid(self, kv='5100', day='2026-02-10', number='M1'):
        mid = self.s.member(dict(number=number, name='Test Mitglied', address='Weg 1', email='', birth='2000-02-01', joined='2024-01-01', kv=kv), 'Test')
        iid = self.s.issue_dues(2026, [mid], 'Test')[0]
        self.s.book(self.bank('30', day), [{'invoice_id': iid, 'amount': '30'}], 'Test')
        return iid


class LedgerTests(Base):
    """Regel wie Masterdatei (08.11.2026): Jahreswerte ab 01.01., Rest des Vorjahres bis 31.01., dann Verfall; Minus wird vorgetragen."""

    def start2025(self):
        self.s.save_settings({'fo_start': '2025'}, 'Test')

    def test_year_values_january_and_lapse(self):
        self.start2025()
        k.set_values(self.s, 2025, '5100', {'ffk': '200', 'dues_share': '594,99', 'opening': '591,67'}, 'Test')
        self.book('-8,50', '2025-01-15', '4430', title='H32')                       # Januar: verbraucht zuerst den Rest 2024
        self.book('-240', '2025-04-12', '4430', title='H32')
        self.book('200', '2025-09-02', '8200')                                     # Spende für den KV: ganz
        self.credit(2000, '2025-10-01')                                           # Gutschrift auf das Ausgabenkonto
        self.book('-137,59', '2026-01-12', '4430', title='H32')
        led = k.ledger(self.s, '5100', today='2026-03-01')
        y25, y26 = led['years']
        self.assertEqual((y25['carry'], y25['lapsed'], y25['ffk'], y25['dues_share'], y25['end']), (59167, 58317, 20000, 59499, 77499))
        self.assertEqual((y25['gains'], y25['donations'], y25['spent'], y25['rest_lapsed']), (22000, 20000, 24850, 63740))
        self.assertEqual((y26['carry'], y26['lapsed'], y26['end'], led['available']), (77499, 63740, 0, 0))
        lines = [(x['kind'], x['amount'], x['balance']) for x in led['lines'] if x['year'] == 2025]
        self.assertEqual(lines, [('anfang', 59167, 59167), ('ffk', 20000, 79167), ('beitrag', 59499, 138666), ('ausgabe', -850, 137816),
                                 ('verfall', -58317, 79499), ('ausgabe', -24000, 55499), ('spende', 20000, 75499), ('gutschrift', 2000, 77499)])
        january = k.ledger(self.s, '5100', today='2026-01-20')                    # Rest 2025 noch bis 31.01. verfügbar
        self.assertEqual((january['available'], january['next']), (63740, [{'until': '2026-01-31', 'amount': 63740, 'label': 'Rest 2025'}]))
        self.assertEqual(k.ledger(self.s, '5100', today='2025-06-30')['next'], [{'until': '2026-01-31', 'amount': 55499, 'label': 'Rest 2025'}])
        view = {b['code']: b for b in k.year_overview(self.s, 2025)}['5100']
        self.assertEqual((view['carry'], view['old_lapsed'], view['ffk'], view['share'], view['gains'], view['spent']), (59167, 58317, 20000, 59499, 22000, 24850))

    def test_minus_is_carried_forward(self):
        self.start2025()
        k.set_values(self.s, 2025, '5300', {'ffk': '630', 'dues_share': '530,15', 'opening': '-202,27'}, 'Test')
        k.set_values(self.s, 2026, '5300', {'ffk': '100'}, 'Test')
        self.book('-1000', '2025-03-06', '4430', kv='5300', title='H32')
        led = k.ledger(self.s, '5300', today='2026-05-01')
        y25, y26 = led['years']
        self.assertEqual([x['balance'] for x in led['lines'] if x['year'] == 2025][:3], [-20227, 42773, 95788])   # EB-Wert 957,88
        self.assertEqual((y25['end'], y25['lapsed'], y26['carry'], y26['lapsed'], led['available']), (-4212, 0, -4212, 0, 5788))
        self.assertEqual(led['uncovered'], 0)
        self.assertNotIn('verfall', [x['kind'] for x in led['lines']])
        self.assertEqual(k.ledger(self.s, '5300', today='2025-12-31')['uncovered'], 4212)

    def test_schueler_union_budget_without_lapse(self):
        self.start2025()
        k.set_values(self.s, 2025, '5800', {'dues_share': '350', 'opening': '-15,18'}, 'Test')
        k.set_values(self.s, 2026, '5800', {'dues_share': '350'}, 'Test')
        self.credit(140000, '2025-02-05', kv='5800')                              # Gutschrift für die Schüler Union
        self.book('-500', '2025-07-21', '4430', kv='5800', title='H32')
        self.book('-89,42', '2026-01-12', '4430', kv='5800', title='H32')
        led = k.ledger(self.s, '5800', today='2026-03-01')
        self.assertEqual((led['years'][1]['carry'], led['available'], led['lapsed'], led['next']), (123482, 149540, 0, []))
        labels = [x['label'] for x in led['lines'] if x['year'] == 2025]
        self.assertEqual(labels[:2], ['Anfangsbestand 01.01.2025', 'Budget 2025'])    # kein FFK-Punkt, kein 25-%-Anteil
        self.assertNotIn('ffk', [x['kind'] for x in led['lines']])

    def test_computed_share_until_fixed(self):
        self.dues_paid()                                                         # 30 € gezahlt 2026 → 7,50 €
        led = k.ledger(self.s, '5100', today='2026-10-28')
        self.assertEqual(led['available'], 750)
        self.assertEqual(led['lines'][-1]['label'], 'Beitragsanteil 2026 (25 % der gezahlten Beiträge)')
        k.set_values(self.s, 2026, '5100', {'dues_share': '100'}, 'Test')        # festgestellter Betrag hat Vorrang
        led = k.ledger(self.s, '5100', today='2026-10-28')
        self.assertEqual((led['available'], led['years'][0]['dues_fixed'], led['years'][0]['dues_paid']), (10000, True, 3000))
        self.assertEqual(k.ledger(self.s, '5100', today='2026-01-31', until='2026-01-31')['available'], 10000)
        self.assertEqual(k.ledger(self.s, '5800', today='2026-10-28')['available'], 0)   # Schüler Union: kein 25-%-Anteil

    def test_values_validation_and_audit(self):
        self.start2025()
        for year, kv, data, text in ((2024, '5100', {'ffk': '1'}, '2025'), (2026, '5100', {'opening': '1'}, 'ersten Jahr'),
                                     (2025, '5100', {'ffk': '-1'}, 'positiven'), (2025, 'LV', {'ffk': '1'}, 'Kreisverbände'),
                                     (2099, '5100', {'ffk': '1'}, 'bis')):
            with self.subTest(year=year, data=data), self.assertRaises(ValueError) as cm:
                k.set_values(self.s, year, kv, data, 'Test')
            self.assertIn(text, str(cm.exception))
        self.assertTrue(k.set_values(self.s, 2025, '5100', {'ffk': '10', 'opening': '-5'}, 'Test'))
        self.assertFalse(k.set_values(self.s, 2025, '5100', {'ffk': '10,00'}, 'Test'))      # unverändert
        self.assertTrue(k.set_values(self.s, 2025, '5100', {'ffk': '', 'opening': ''}, 'Test'))
        self.assertEqual(k.year_values(self.s), {})
        actions = [r[0] for r in self.s.db.execute("SELECT action FROM audit WHERE action='KV-Jahreswerte geändert'")]
        self.assertEqual(len(actions), 2)

    def test_labels_short_text_and_recipient(self):
        bid = self.bank('-12,50', '2026-03-01', 'EREF+NOTPROVIDED SVWZ+Erstattung Getraenke Stammtisch IBAN DE89370400440532013000 BIC COBADEFFXXX')
        self.s.book(bid, [{'account': '4430', 'kv': '5100', 'title': 'H32', 'amount': '-12,50'}], 'Test', confirm_no_bill=True)
        line = k.ledger(self.s, '5100', today='2026-10-28')['lines'][-1]
        self.assertEqual((line['label'], line['recipient'], line['auto']), ('Erstattung Getraenke Stammtisch', 'Test', True))
        k.set_label(self.s, line['journal'], 'Getränke Stammtisch März', 'Erika Beispiel', 'Test')
        line = k.ledger(self.s, '5100', today='2026-10-28')['lines'][-1]
        self.assertEqual((line['label'], line['recipient'], line['auto']), ('Getränke Stammtisch März', 'Erika Beispiel', False))
        k.set_label(self.s, line['journal'], '', '', 'Test')                       # leer = wieder automatisch
        self.assertTrue(k.ledger(self.s, '5100', today='2026-10-28')['lines'][-1]['auto'])
        lv = self.book('-5', '2026-03-02', '4430', kv='LV', title='H32')
        jid = self.s.db.execute("SELECT id FROM journals WHERE source=?", (f'bank:{lv}',)).fetchone()[0]
        with self.assertRaises(ValueError):
            k.set_label(self.s, jid, 'x', '', 'Test')
        self.assertEqual(k.short_text('Rechnung 2026-17 · Excel-Übernahme · Kostenstelle KV Nord'), 'Rechnung 2026-17')
        self.assertTrue(len(k.short_text('Wort ' * 40)) <= 45 and k.short_text('Wort ' * 40).endswith('…'))
        for raw, short in (('LASTSCHRIFT Webhoster Beispiel AG KD-Nr. K123456789/ RG-Nr. 100200300 vom 01.02.2026', 'Webhoster Beispiel AG vom 01.02.2026'),
                           ('SEPA-GUTSCHRIFT Webhoster Beispiel AG KD-Nr. K123456789', 'Webhoster Beispiel AG'),
                           ('ÜBERWEISUNG (DIRECT BANKING Erika Beispiel / DE02760100850000123456 Auslagen Stammtisch',
                            'Erika Beispiel Auslagen Stammtisch'),
                           ('ECHTZEITGUTSCHRIFT Max Beispiel Spende Sommerfest', 'Max Beispiel Spende Sommerfest'),
                           ('Erstattung Getraenke Hamburgtag', 'Erstattung Getraenke Hamburgtag'),
                           ('LASTSCHRIFT Webhoster Beispiel AG MANDATSREFERENZ 92 GLAEUBIGER-ID DE98ZZZ09999999999', 'Webhoster Beispiel AG'),
                           ('Lastschrift Webhoster Beispiel AG Mandatsreferenz M-1 Gläubiger-ID DE98ZZZ0999', 'Webhoster Beispiel AG'),
                           ('ÜBERWEISUNG Erika Beispiel DE0276 04.07.2026 H5100 Auslagen', 'Erika Beispiel 04.07.2026 Auslagen'),
                           ('Sommerfest Altona / Mitte', 'Sommerfest Altona / Mitte')):
            with self.subTest(raw=raw):
                self.assertEqual(k.short_text(raw), short)

    def test_storno_and_parked_count_nothing(self):
        """Geparkt, aus dem Parkkonto aufgelöst, dann storniert: weder Ausgabe noch Spende bleiben stehen."""
        for amount, account in (('-40', '4430'), ('100', '8200')):
            bid = self.bank(amount, '2026-05-02')
            workflow.park(self.s, bid, 'Beleg fehlt', 'Test')
            self.s.book(bid, [{'account': account, 'kv': '5100', 'title': 'H32' if account == '4430' else 'H5', 'amount': amount}], 'Test', confirm_no_bill=True)
            self.s.reverse_bank(bid, 'Test')
        led = k.ledger(self.s, '5100', today='2026-10-28')
        self.assertEqual((led['spent'], led['available'], [x['kind'] for x in led['lines']]), (0, 0, ['anfang']))
        self.assertEqual(k.donations(self.s, 2026), [])

    def test_start_and_future_dates(self):
        self.book('-20', '2025-06-01', '4430', title='H32')                      # vor dem Beginn (Standard 2026)
        self.assertEqual(k.ledger(self.s, '5100', today='2026-10-28')['available'], 0)
        self.s.save_settings({'fo_start': '2025-01-01'}, 'Test')
        self.assertEqual(k.ledger(self.s, '5100', today='2026-10-28')['available'], -2000)
        with self.assertRaises(ValueError):
            self.s.save_settings({'fo_start': '1999-01-01'}, 'Test')
        with self.s.db:
            self.s.journal('2027-06-01', 'Rechnung mit künftigem Datum', 'test:1', [{'account': '4470', 'kv': '5100', 'debit': 100, 'credit': 0, 'title': 'H44'},
                                                                                   {'account': '1600', 'kv': '5100', 'debit': 0, 'credit': 100, 'title': 'x'}])
        self.assertEqual(k.ledger(self.s, '5100', today='2026-10-28')['available'], -2000)   # zählt erst ab dem Datum

    def test_run_is_pure(self):
        result = k.run(2025, 1000, {2025: {'ffk': 500, 'dues_share': 0}}, [{'day': '2025-01-10', 'kind': 'ausgabe', 'amount': -300},
                                                                         {'day': '2024-12-31', 'kind': 'ausgabe', 'amount': -1}], '2025-03-01')
        self.assertEqual([(x['kind'], x['amount']) for x in result['lines']], [('anfang', 1000), ('ffk', 500), ('ausgabe', -300), ('verfall', -700)])
        self.assertEqual(k.run(2026, 0, {}, [], '2025-05-01'), {'years': [], 'lines': []})


class ClosingTests(Base):
    def test_close_year(self):
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        k.set_values(self.s, 2025, '5100', {'ffk': '100'}, 'Test')
        k.set_values(self.s, 2025, '5300', {'ffk': '10'}, 'Test')
        self.book('-20', '2025-06-01', '4430', title='H32')
        self.book('-30', '2026-01-10', '4430', title='H32')                       # Januar: verbraucht Rest 2025
        self.book('-50', '2025-06-01', '4430', kv='5300', title='H32')
        self.assertEqual(k.closing_due(self.s, today='2026-01-31'), [])
        with self.assertRaises(ValueError):
            k.close_year(self.s, 2025, 'Test', today='2026-01-31')               # erst nach Ablauf des 31.01.
        with self.assertRaises(ValueError):
            k.close_year(self.s, 2024, 'Test', today='2026-02-01')               # vor Beginn der KV-Budgets
        self.assertEqual(k.closing_due(self.s, today='2026-02-01'), [2025])
        k.close_year(self.s, 2025, 'Test', today='2026-02-01')
        rows = {r['kv']: r for r in k.closings(self.s)}
        self.assertEqual((rows['5100']['share'], rows['5100']['used'], rows['5100']['lapsed'], rows['5100']['carried'], rows['5100']['changed']),
                         (10000, 2000, 5000, 0, False))
        self.assertEqual((rows['5300']['lapsed'], rows['5300']['carried']), (0, -4000))
        self.assertEqual(k.closing_due(self.s, today='2026-02-01'), [])
        with self.assertRaises(ValueError):
            k.close_year(self.s, 2025, 'Test', today='2026-02-01')
        self.book('-1', '2025-12-01', '4430', title='H32')                       # nachträglich gebucht
        self.assertTrue({r['kv']: r for r in k.closings(self.s)}['5100']['changed'])


class DonationTests(Base):
    def test_donation_list(self):
        self.book('100', '2026-03-01', '8200')
        self.book('40', '2026-03-02', '8202')                                    # Förderkreis: nur über den Jahresbetrag je KV
        self.book('500', '2026-03-03', '1371', kv='LV', title='H6')
        rows = k.donations(self.s, 2026)
        self.assertEqual([(r['account'], r['amount'], r['kv_share']) for r in rows], [('8200', 10000, 10000), ('8202', 4000, 0), ('1371', 50000, 0)])
        self.assertEqual(rows[0]['until'], '2027-01-31')
        self.assertEqual(k.ledger(self.s, '5100', today='2026-10-28')['available'], 10000)

    def test_cash_donation_rules(self):
        today = date.today()
        base = {'received_on': (today - timedelta(days=40)).isoformat(), 'donor': 'Max Spender', 'amount': '50', 'kv': 'LV',
                'receiver1': 'Vorsitz Beispiel', 'receiver2': 'Schatz Beispiel', 'receipt': 'Q-2026-001'}
        for bad in ({'receiver2': 'vorsitz beispiel'}, {'receipt': ''}, {'received_on': (today + timedelta(days=1)).isoformat()},
                    {'deposited_on': (today - timedelta(days=50)).isoformat()}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                k.save_cash(self.s, {**base, **bad}, 'Test')
        cid = k.save_cash(self.s, base, 'Test')
        row = k.cash_list(self.s)[0]
        self.assertTrue(row['overdue'])
        self.assertIn('CDU-Buchhaltung noch nicht informiert', row['issues'])
        k.save_cash(self.s, {**base, 'deposited_on': (today - timedelta(days=5)).isoformat(), 'cdu_informed_on': today.isoformat()}, 'Test', cid)
        row = k.cash_list(self.s)[0]
        self.assertEqual((row['overdue'], row['issues']), (False, ['später als 30 Tage eingezahlt']))


class TransferTests(Base):
    """Budget-Umbuchung zwischen Bereichen (Kostenbeteiligung, Wunsch 29.10.2026)."""

    def test_kv_participates_in_costs_of_another(self):
        import haushalt
        self.dues_paid(kv='5500', number='M1')                                     # Mitte: 7,50 € Anteil
        self.dues_paid(kv='5600', number='M2')                                     # Nord: 7,50 € Anteil
        self.book('-12', '2026-06-01', '4473', kv='5600', title='H43')              # Nord bezahlt die Feier
        jid = k.transfer(self.s, {'to': '5500', 'from': '5600', 'amount': '4,00', 'day': '2026-06-02',
                                  'account': '4473', 'reason': 'Beteiligung gemeinsames Sommerfest'}, 'Test')
        mitte, nord = k.ledger(self.s, '5500', '2026-10-28'), k.ledger(self.s, '5600', '2026-10-28')
        self.assertEqual((mitte['spent'], mitte['available']), (400, 350))
        self.assertEqual((nord['spent'], nord['years'][0]['gains'], nord['available']), (1200, 400, -50))   # 12 € − 4 € entlastet
        self.assertEqual(nord['uncovered'], 50)                                    # 7,50 € − 12 € + 4 € = −0,50 € (wird vorgetragen)
        self.assertEqual([x['label'] for x in mitte['lines'] if x['kind'] == 'umbuchung'], ['Umbuchung an KV Nord: Beteiligung gemeinsames Sommerfest'])
        self.assertEqual([x['label'] for x in nord['lines'] if x['kind'] == 'umbuchung'], ['Umbuchung von KV Mitte: Beteiligung gemeinsames Sommerfest'])
        values = haushalt.actuals(self.s, 2026)[0]
        self.assertEqual((values['H54'], values['H55']), (-400, -800))             # KV-Zeilen verschieben sich
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)
        listed = k.transfers(self.s, 2026)
        self.assertEqual([(t['id'], t['to'], t['from'], t['amount'], t['reversed']) for t in listed], [(jid, '5500', '5600', 400, False)])
        self.assertIn('KV Mitte beteiligt sich an Kosten von KV Nord', listed[0]['description'])
        self.assertEqual(k.transfers(self.s, 2025), [])
        k.reverse_transfer(self.s, jid, 'falscher Betrag', 'Test')
        self.assertTrue(k.transfers(self.s, 2026)[0]['reversed'])
        self.assertEqual(k.ledger(self.s, '5500', '2026-10-28')['spent'], 0)
        self.assertEqual(haushalt.actuals(self.s, 2026)[0]['H54'], 0)
        with self.assertRaises(ValueError):
            k.reverse_transfer(self.s, jid, 'noch einmal', 'Test')                 # nur einmal stornierbar
        actions = [r[0] for r in self.s.db.execute('SELECT action FROM audit')]
        self.assertIn('Budget umgebucht', actions)
        self.assertIn('Budget-Umbuchung storniert', actions)

    def test_collective_account_and_landesverband(self):
        import haushalt
        k.transfer(self.s, {'to': '5100', 'from': '5200', 'amount': '10', 'day': '2026-03-01', 'reason': 'Stammtisch'}, 'Test')   # Sammelkonto KV
        rows = self.s.rows("SELECT kv,title,debit,credit FROM entries e JOIN journals j ON j.id=e.journal_id WHERE j.source='kvumbuchung:1' ORDER BY kv")
        self.assertEqual([(r['kv'], r['title']) for r in rows], [('5100', 'H50'), ('5200', 'H51')])  # je Kreisverband seine Zeile
        with self.assertRaises(ValueError) as cm:
            k.transfer(self.s, {'to': '5100', 'from': 'LV', 'amount': '10', 'reason': 'Feier'}, 'Test')
        self.assertIn('Sammelkonto KV', str(cm.exception))
        k.transfer(self.s, {'to': '5100', 'from': 'LV', 'amount': '10', 'account': '4473', 'reason': 'Sommerfest LV'}, 'Test')
        values = haushalt.actuals(self.s, int(date.today().year))[0]
        self.assertEqual(values['H43'], 1000)                                      # Landesverband entlastet (Titel Sommerfest)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM journals WHERE source LIKE 'kvumbuchung:%'").fetchone()[0], 2)

    def test_future_dates(self):
        self.book('100', '2026-03-01', '8200', kv='5300', title='H5')                 # Spende: 100 € für KV Eimsbüttel
        with self.assertRaises(ValueError) as cm:
            k.transfer(self.s, {'to': '5300', 'from': '5200', 'amount': '1', 'day': '2099-06-01', 'reason': 'Tippfehler'}, 'Test')
        self.assertIn('Zukunft', str(cm.exception))
        self.s.journal('2027-06-01', 'Rechnung mit künftigem Datum', 'test:1', [{'account': '4470', 'kv': '5300', 'debit': 100, 'credit': 0, 'title': 'H44'},
                                                                               {'account': '1600', 'kv': '5300', 'debit': 0, 'credit': 100, 'title': 'x'}])
        led = k.ledger(self.s, '5300', today='2026-10-28')
        self.assertEqual((led['available'], led['lapsed']), (10000, 0))          # nichts vorzeitig verfallen

    def test_validation(self):
        for data, text in (({'to': '5100', 'from': '5100', 'amount': '1', 'reason': 'xyz'}, 'zwei verschiedene'),
                           ({'to': '5100', 'from': '5200', 'amount': '0', 'reason': 'xyz'}, 'größer als 0'),
                           ({'to': '5100', 'from': '5200', 'amount': '1', 'reason': ''}, 'Grund'),
                           ({'to': '5100', 'from': '5200', 'amount': '1', 'reason': 'xyz', 'account': '8200'}, 'Ausgaben'),
                           ({'to': '5100', 'from': '9999', 'amount': '1', 'reason': 'xyz'}, 'zwei verschiedene'),
                           ({'to': '5100', 'from': '5200', 'amount': '1', 'reason': 'xyz', 'bill_id': 99}, 'nicht gefunden')):
            with self.assertRaises(ValueError) as cm:
                k.transfer(self.s, data, 'Test')
            self.assertIn(text, str(cm.exception))
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM journals").fetchone()[0], 0)


class ReviewTests(Base):
    """Befunde der unabhängigen Prüfung (08.11.2026)."""

    def test_transfer_labels_belong_to_both_areas(self):
        jid = k.transfer(self.s, {'to': '5100', 'from': '5200', 'amount': '50', 'day': '2026-06-02', 'reason': 'Stammtisch'}, 'Test')
        with self.assertRaises(ValueError) as cm:
            k.set_label(self.s, jid, 'Zuschuss', 'KV Altona', 'Test')
        self.assertIn('Umbuchungen', str(cm.exception))
        with self.s.db:                                                          # auch eine alte Bezeichnung wird nicht angezeigt
            self.s.db.execute("INSERT INTO kv_labels VALUES(?,?,?,?,?,?)", (jid, 'falsch', 'x', 'Masterdatei', '2026-01-01', 'Test'))
        self.assertEqual([x['label'] for x in k.ledger(self.s, '5200', today='2026-10-28')['lines'] if x['kind'] == 'umbuchung'],
                         ['Umbuchung von KV Altona / Elbvororte: Stammtisch'])

    def test_plan_comparison_by_fiscal_year_without_donations(self):
        k.set_plan(self.s, 2026, '5100', '500', 'Test')
        self.book('-900', '2026-03-01', 'KV', title='H50')
        self.book('400', '2026-03-02', '8200')
        row = {b['code']: b for b in k.year_overview(self.s, 2026)}['5100']
        self.assertEqual((row['plan_spent'], row['plan_remaining'], row['spent'], row['gains']), (90000, -40000, 90000, 40000))

    def test_refund_in_january_restores_old_rest(self):
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        k.set_values(self.s, 2025, '5100', {'ffk': '10'}, 'Test')
        self.book('-10', '2026-01-10', '4430', title='H32')
        self.credit(1000, '2026-01-20')                                           # Erstattung derselben Ausgabe
        led = k.ledger(self.s, '5100', today='2026-03-01')
        self.assertEqual((led['years'][1]['lapsed'], led['available']), (1000, 0))
        k.set_values(self.s, 2026, '5100', {'ffk': '5'}, 'Test')
        self.book('100', '2026-01-25', '8200')                                    # Spende im Januar: fürs neue Jahr
        self.assertEqual(k.ledger(self.s, '5100', today='2026-03-01')['available'], 500 + 10000)

    def test_old_rule_closings_are_not_compared(self):
        with self.s.db:
            self.s.db.execute("INSERT INTO kv_closings(year,kv,share,used,lapsed,closed_on,actor) VALUES(2026,'5100',750,0,750,'2027-02-01','Alt')")
        row = k.closings(self.s)[0]
        self.assertEqual((row['old_rule'], row['changed'], row['rule']), (True, False, 'bis 0.22'))

    def test_closing_reconciles(self):
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        k.set_values(self.s, 2025, '5100', {'ffk': '100', 'opening': '-5'}, 'Test')
        self.book('-20', '2025-06-01', '4430', title='H32')
        self.book('-30', '2026-01-10', '4430', title='H32')
        k.close_year(self.s, 2025, 'Test', today='2026-02-01')
        c = {r['kv']: r for r in k.closings(self.s)}['5100']
        self.assertEqual((c['carry_in'], c['share'], c['used'], c['jan_used'], c['lapsed'], c['carried'], c['rule']),
                         (-500, 10000, 2000, 3000, 4500, 0, 'Masterdatei'))
        self.assertEqual(c['carry_in'] + c['share'] - c['used'], c['jan_used'] + c['lapsed'] + c['carried'])

    def test_next_year_values_and_su_without_ffk(self):
        nxt = date.today().year + 1
        k.set_values(self.s, nxt, '5100', {'ffk': '300', 'dues_share': '50'}, 'Test')
        row = {b['code']: b for b in k.year_overview(self.s, nxt)}['5100']
        self.assertEqual((row['ffk'], row['share'], row['share_fixed']), (30000, 5000, True))
        with self.assertRaises(ValueError):
            k.set_values(self.s, nxt, '5800', {'ffk': '100'}, 'Test')
        self.assertTrue(k.set_values(self.s, nxt, '5800', {'ffk': '0', 'dues_share': '350'}, 'Test'))

    def test_january_pdf_shows_what_is_left(self):
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        k.set_values(self.s, 2025, '5100', {'ffk': '500'}, 'Test')
        self.book('-200', '2026-01-10', '4430', title='H32')
        s = k.statement_for(k.ledger(self.s, '5100', today='2026-01-20'), 2025)
        self.assertEqual((s['balance'], s['open_next'], s['rest_lapsed']), (50000, 30000, None))

    def test_donor_name_not_shown_as_recipient(self):
        self.book('100', '2026-03-01', '8200')
        line = [x for x in k.ledger(self.s, '5100', today='2026-10-28')['lines'] if x['kind'] == 'spende'][0]
        self.assertIsNone(line['recipient'])

    def test_cleanup_blocked_when_budgets_start_earlier(self):
        import bereinigung
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        k.set_values(self.s, 2025, '5100', {'opening': '30'}, 'Test')
        self.assertTrue(any('KV-Budgets beginnen vor dem Stichtag' in b for b in bereinigung.check(self.s, '2026-01-01')['blockers']))


if __name__ == '__main__':
    unittest.main()
