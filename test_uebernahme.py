"""Tests für die Übernahme der vorkontierten Excel-Masterdatei (nur erfundene Daten)."""
import base64
import http.cookiejar
import io
import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import date, datetime
from pathlib import Path

import openpyxl

import haushalt
import uebernahme
from core import Store

FIXTURES = Path(__file__).parent / 'tests' / 'fixtures'
ERIKA = 'ECHTZEITÜBERWEISUNG Erika Musterfrau DE02120300000000202051 Mitgliedsbeitrag 2026 Erika Musterfrau'
MAX = 'ECHTZEITÜBERWEISUNG Max Mustermann DE02120300000000202051 Beitrag 2026'
ROWS_2026 = [
    (date(2026, 6, 2), ERIKA, 8100, 30.0, None),
    (date(2026, 6, 3), 'LASTSCHRIFT Muster Telekom GmbH 12345678 Rechnung 06/2026 MREF ABC123', 4210, -39.99, None),
    (date(2026, 6, 5), 'ÜBERWEISUNG Druckerei Beispiel KG DE89370400440532013000 Flyer Sommerfest', 5500, -1234.56, 'KV Mitte'),
    (date(2026, 6, 5), 'ENTGELT Kontofuehrung 05/2026', 4225, -7.5, None),
    (date(2026, 6, 9), MAX, 8100, 20.0, None),
    (date(2026, 6, 9), MAX, 8100, 20.0, None),
    (date(2026, 6, 10), 'LASTSCHRIFT Ruecklastschrift Beitrag', 8100, -20.0, None),
    (date(2026, 6, 11), 'GUTSCHRIFT Unbekannt', None, 15.0, None),
    (date(2026, 6, 12), 'GUTSCHRIFT Klaerungsfall', 1370, 50.0, None),
    (date(2026, 6, 13), 'GUTSCHRIFT Land Hamburg Foerdermittel', 1592, 500.0, 'RPJ'),
    (date(2026, 6, 14), 'LASTSCHRIFT Irgendwas', 9999, -1.0, None),
    (date(2026, 6, 15), 'GUTSCHRIFT KV Rueckzahlung', 5100, 10.0, 'KV Mitte'),
]
ROWS_2025 = [
    (12345, datetime(2025, 3, 1), datetime(2025, 3, 2), 'GUTSCHRIFT Spende Kandidat', 1371, 100, 'Spendenkonto JH'),
    (12345, datetime(2026, 1, 10), datetime(2026, 1, 10), 'GUTSCHRIFT Beitrag 2025 spaet', 8100, 30, 'Landesverband'),
    (12345, None, None, 'kaputt', 8100, 5, None),
]


def master_xlsx():
    book = openpyxl.Workbook()
    book.active.title = 'Haushalt  GuV'
    sheet = book.create_sheet('Umsatz 2026')
    sheet.append(['Valuta', 'Verwendungszweck', 'Haushaltposten', 'Betrag', 'Kostenstelle '])
    for row in ROWS_2026:
        sheet.append(list(row))
    sheet = book.create_sheet('Umsatz 2025')
    sheet.append(['Kontonummer', 'Buchungsdatum', 'Valuta', 'Verwendungszweck', 'Haushaltposten', 'Betrag', 'Kostenstelle '])
    for row in ROWS_2025:
        sheet.append(list(row))
    book.create_sheet('Tabelle1').append(['Kontonummer', 'Buchungsdatum'])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


class UebernahmeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.raw = master_xlsx()

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_preview_statuses_and_summary(self):
        p = uebernahme.preview(self.s, self.raw, 'master.xlsx')
        self.assertEqual(p['sheets'], ['Umsatz 2026', 'Umsatz 2025'])
        self.assertIn('Haushalt  GuV', p['notes'][0])
        status = [r['status'] for r in p['rows']]
        self.assertEqual(status.count('neu'), 11)
        self.assertEqual((status.count('offen'), status.count('parken'), status.count('fehler')), (0, 3, 1))   # 1370, ohne und unbekannter Posten → Parkkonto
        self.assertIn('9999 ist im Programm nicht bekannt – auf das Parkkonto 1370 gebucht', ' '.join(r['note'] for r in p['rows']))
        self.assertIn('Kein Haushaltposten – auf das Parkkonto 1370 gebucht', ' '.join(r['note'] for r in p['rows']))
        notes = ' '.join(r['note'] for r in p['rows'])
        self.assertIn('Rücklastschrift', notes)
        self.assertIn('passt nicht zum Posten 5100', notes)
        self.assertIn('Bankdatum 2026 – Haushaltsjahr laut Blatt: 2025', notes)
        by_year = {s['year']: s for s in p['summary']}
        self.assertEqual((by_year['2025']['count'], by_year['2025']['fehler']), (3, 1))   # nach Blatt, inkl. Zeile ohne Datum
        self.assertEqual(by_year['2026']['count'], 12)

    def test_execute_books_like_haushalt(self):
        counts = uebernahme.execute(self.s, uebernahme.preview(self.s, self.raw, 'master.xlsx'), 'Test')
        self.assertEqual(counts, {'neu': 11, 'zuordnen': 0, 'parken': 3, 'offen': 0, 'vorhanden': 0, 'fehlt': 0, 'fehler': 1})
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)
        states = dict(self.s.db.execute('SELECT state,COUNT(*) FROM bank GROUP BY state').fetchall())
        self.assertEqual(states, {'booked': 11, 'parked': 3})                 # nichts bleibt ungebucht
        values, unmatched, _, _ = haushalt.actuals(self.s, 2026)
        self.assertEqual(values['H4'], 3000 + 2000 + 2000 - 2000)          # inkl. Rücklastschrift
        self.assertEqual(values['H27'], -3999)
        self.assertEqual(values['H34'], -750)
        self.assertEqual(values['H54'], -123456)                            # KV Mitte über Sammelkonto
        self.assertEqual(values['H50'], 1000)                               # Eingang mindert KV-Ausgaben
        self.assertEqual(values['H18'], 50000)
        self.assertEqual(unmatched, {'income': 0, 'expense': 0})
        v2025 = haushalt.actuals(self.s, 2025)[0]
        self.assertEqual((v2025['H6'], v2025['H4']), (10000, 3000))           # Januar 2026 im Blatt 2025 zählt zu 2025
        mitte = {b['code']: b for b in self.s.budget_view(2026)}['5500']
        self.assertEqual(mitte['spent'], 123456)
        parked = ' | '.join(r['title'] for r in self.s.rows("SELECT e.title FROM entries e WHERE e.account='1370'"))
        for reason in ('Excel-Übernahme: Posten 1370', 'Posten 9999 (im Programm nicht bekannt)', 'ohne Haushaltposten'):
            self.assertIn(reason, parked)                                    # Klärungsgrund im Reiter „Parkkonto 1370“
        with self.assertRaises(ValueError):
            uebernahme.preview(self.s, self.raw, 'master.xlsx')

    def test_existing_bank_rows_are_booked_not_duplicated(self):
        raw = (FIXTURES / 'umsaetze-kopf-oben.xls').read_bytes()
        bank = self.s.preview_bank(raw, 'bank.xls')
        self.s.import_bank(bank, [r['row_no'] for r in bank['rows'] if r['selected']], 'Test')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bank').fetchone()[0], 5)
        p = uebernahme.preview(self.s, self.raw, 'master.xlsx')
        status = [r['status'] for r in p['rows']]
        self.assertEqual(status.count('zuordnen'), 5)                       # 5 offene Bankumsätze werden verbucht
        self.assertEqual(status.count('fehlt'), 1)                          # zweite Zahlung von Max nicht im Kontoauszug → nicht anlegen
        counts = uebernahme.execute(self.s, p, 'Test')
        self.assertEqual((counts['zuordnen'], counts['fehlt']), (5, 1))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bank').fetchone()[0], 5 + 5 + 1 + 2)   # nur Zeilen nach dem Kontoauszug neu
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM bank WHERE state='open'").fetchone()[0], 0)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM bank WHERE state='parked'").fetchone()[0], 3)

    def test_reversed_bank_row_left_for_booking_desk(self):
        raw = (FIXTURES / 'umsaetze-kopf-oben.xls').read_bytes()
        bank = self.s.preview_bank(raw, 'bank.xls')
        self.s.import_bank(bank, [r['row_no'] for r in bank['rows'] if r['selected']], 'Test')
        row = self.s.rows('SELECT id,amount FROM bank ORDER BY id LIMIT 1')[0]
        kind = 'income' if row['amount'] > 0 else 'expense'
        account = self.s.db.execute('SELECT code FROM accounts WHERE kind=? AND code!=? ORDER BY code LIMIT 1', (kind, '8100')).fetchone()[0]
        self.s.book(row['id'], [{'account': account, 'kv': 'LV', 'title': 'H4', 'amount': f"{row['amount'] / 100:.2f}"}], 'Test', confirm_no_bill=True)
        self.s.reverse_bank(row['id'], 'Test')                               # wieder offen – gehört in den Buchungsplatz
        p = uebernahme.preview(self.s, self.raw, 'master.xlsx')
        hit = [r for r in p['rows'] if r.get('bank_id') == row['id']][0]
        self.assertEqual(hit['status'], 'vorhanden')
        self.assertIn('neu zuordnen', hit['note'])
        counts = uebernahme.execute(self.s, p, 'Test')
        self.assertEqual(counts['zuordnen'], 4)
        self.assertEqual(self.s.db.execute('SELECT state FROM bank WHERE id=?', (row['id'],)).fetchone()[0], 'open')
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)

    def test_bill_payment_not_booked_as_expense(self):
        import verbindlichkeiten
        verbindlichkeiten.create(self.s, {'creditor': 'Druckerei Beispiel KG', 'iban': 'DE89370400440532013000', 'ext_number': 'D-77',
                                          'issued': '2026-06-01', 'due': '', 'kv': '5500',
                                          'lines': [{'description': 'Flyer', 'amount': '1234,56', 'account': '4430', 'title': 'H32'}]}, 'Test')
        p = uebernahme.preview(self.s, self.raw, 'master.xlsx')
        hit = [r for r in p['rows'] if 'Druckerei Beispiel' in (r.get('reference') or '')][0]
        self.assertEqual((hit['status'], hit['plan']), ('offen', 'offen'))
        self.assertIn('Eingangsrechnung E-2026-', hit['note'])
        uebernahme.execute(self.s, p, 'Test')
        bid = self.s.db.execute("SELECT id FROM bank WHERE reference LIKE '%Druckerei Beispiel%'").fetchone()[0]
        self.assertEqual(self.s.db.execute('SELECT state FROM bank WHERE id=?', (bid,)).fetchone()[0], 'open')
        spent = self.s.db.execute("SELECT COALESCE(SUM(debit-credit),0) FROM entries WHERE account='5500'").fetchone()[0]
        self.assertEqual(spent, 0)                                           # keine zweite Ausgabe neben der Eingangsrechnung

    def test_reverse_imported_booking(self):
        uebernahme.execute(self.s, uebernahme.preview(self.s, self.raw, 'master.xlsx'), 'Test')
        bid = self.s.db.execute("SELECT id FROM bank WHERE state='booked' ORDER BY id LIMIT 1").fetchone()[0]
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)
        late = self.s.db.execute("SELECT id FROM bank WHERE reference LIKE '%spaet'").fetchone()[0]
        self.s.reverse_bank(late, 'Test')                                    # Storno bleibt im Haushaltsjahr 2025
        self.assertEqual(haushalt.actuals(self.s, 2025)[0]['H4'], 0)
        self.assertEqual(haushalt.actuals(self.s, 2026)[0]['H4'], 2000 + 2000 - 2000)   # erste Buchung (30 €) storniert

    def test_parked_resolution_keeps_year(self):
        uebernahme.execute(self.s, uebernahme.preview(self.s, self.raw, 'master.xlsx'), 'Test')
        bid = self.s.db.execute("SELECT id FROM bank WHERE state='parked' AND amount=5000").fetchone()[0]
        self.s.db.execute('UPDATE journals SET year=2025 WHERE source=?', (f'bank:{bid}',))   # simuliert Blatt 2025
        self.s.db.commit()
        self.s.book(bid, [{'amount': '50', 'account': '8500', 'kv': 'LV', 'title': 'H17'}], 'Test')
        self.assertEqual(haushalt.actuals(self.s, 2025)[0]['H17'], 5000)

    def test_old_database_gets_year_column(self):
        import sqlite3
        path = Path(self.tmp.name) / 'alt.db'
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE journals(id INTEGER PRIMARY KEY,day TEXT,description TEXT,source TEXT UNIQUE NOT NULL,reversal_of INTEGER UNIQUE REFERENCES journals(id))')
        con.execute("INSERT INTO journals(day,description,source) VALUES('2026-01-01','alt','x')")
        con.commit(); con.close()
        old = Store(path)
        self.assertIn('year', [c[1] for c in old.db.execute('PRAGMA table_info(journals)')])
        self.assertIsNone(old.db.execute('SELECT year FROM journals').fetchone()[0])
        old.db.close()

    def test_new_accounts_available(self):
        codes = {r['code']: r['kind'] for r in self.s.rows('SELECT * FROM accounts')}
        self.assertEqual(codes['8200'], 'income')
        self.assertEqual(codes['4441'], 'expense')
        self.assertEqual(codes['KV'], 'expense')
        self.assertEqual(codes['4210'], 'expense')
        self.assertNotIn('5100', codes)


class AbgleichTests(unittest.TestCase):
    """Excel-Zeilen im Zeitraum importierter Kontoauszüge: vorhandene Umsätze verbuchen, nie doppelt anlegen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, day, amount, name, reference, statement='auszug-1'):
        cur = self.s.db.execute('INSERT INTO bank(booked,amount,name,reference,bank_ref,source,row_no,fingerprint,import_hash) '
                                'VALUES(?,?,?,?,?,?,?,?,?)', (day, amount, name, reference, None, 'auszug.xls', 1,
                                                              f'fp-{day}-{amount}-{name}', statement))
        self.s.db.commit()
        return cur.lastrowid

    def master(self, rows):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'Umsatz 2026'
        sheet.append(['Valuta', 'Verwendungszweck', 'Haushaltposten', 'Betrag', 'Kostenstelle'])
        for row in rows:
            sheet.append(list(row))
        out = io.BytesIO()
        book.save(out)
        return out.getvalue()

    def preview(self, rows):
        p = uebernahme.preview(self.s, self.master(rows), 'master.xlsx')
        return p, {r['reference']: r for r in p['rows']}

    def test_other_text_and_date_still_found(self):
        spende = self.bank('2026-03-02', 25000, 'Beispiel Immobilien GmbH', 'SEPA-GUTSCHRIFT Beispiel Immobilien GmbH Spende Wahlkampf')
        miete = self.bank('2026-03-05', -45000, 'Muster Vermietung', 'DAUERAUFTRAG Muster Vermietung Miete März')
        later = self.bank('2026-04-30', 3000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau Beitrag')
        p, got = self.preview([
            (date(2026, 3, 2), 'Spende Wahlkampf Beispiel Immobilien', 1371, 250.0, None),          # anderer Text, gleicher Tag
            (date(2026, 3, 3), 'Miete Maerz Muster Vermietung', 4210, -450.0, None),               # 2 Tage früher, ae statt ä
            (date(2026, 1, 15), 'Altes Beispiel vor dem Kontoauszug', 8100, 10.0, None),           # vor dem Zeitraum → neu
            (date(2026, 4, 1), 'Unbekannte Gutschrift Beispiel', 8100, 99.0, None),                # im Zeitraum, nicht da → fehlt
            (date(2026, 5, 20), 'Nach dem Kontoauszug', 8100, 12.0, None),                         # danach → neu
        ])
        self.assertEqual((got['Spende Wahlkampf Beispiel Immobilien']['status'], got['Spende Wahlkampf Beispiel Immobilien']['bank_id']), ('zuordnen', spende))
        self.assertEqual((got['Miete Maerz Muster Vermietung']['status'], got['Miete Maerz Muster Vermietung']['bank_id']), ('zuordnen', miete))
        self.assertEqual(got['Altes Beispiel vor dem Kontoauszug']['status'], 'neu')
        self.assertEqual(got['Nach dem Kontoauszug']['status'], 'neu')
        self.assertEqual(got['Unbekannte Gutschrift Beispiel']['status'], 'fehlt')
        self.assertIn('nicht gefunden', got['Unbekannte Gutschrift Beispiel']['note'])
        self.assertIn('Kontoauszug am 05.03.2026 „Muster Vermietung“', got['Miete Maerz Muster Vermietung']['note'])
        self.assertIn('02.03.2026 – 30.04.2026', ' '.join(p['notes']))
        counts = uebernahme.execute(self.s, p, 'Test')
        self.assertEqual((counts['zuordnen'], counts['neu'], counts['fehlt']), (2, 2, 1))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bank').fetchone()[0], 5)
        states = dict(self.s.db.execute('SELECT id,state FROM bank').fetchall())
        self.assertEqual((states[spende], states[miete], states[later]), ('booked', 'booked', 'open'))
        day = self.s.db.execute('SELECT day FROM journals WHERE source=?', (f'bank:{miete}',)).fetchone()[0]
        self.assertEqual(day, '2026-03-05')                                 # Journal mit dem Bankdatum
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)
        audit = json.loads(self.s.db.execute("SELECT detail FROM audit WHERE action='Vorkontierte Excel übernommen'").fetchone()[0])
        self.assertEqual([(x['status'], x['betrag']) for x in audit['uebersprungen']], [('fehlt', 9900)])

    def test_same_amount_same_day_matched_by_name(self):
        erika = self.bank('2026-05-04', 3000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau Mitgliedsbeitrag')
        max_ = self.bank('2026-05-04', 3000, 'Max Mustermann', 'SEPA-GUTSCHRIFT Max Mustermann Mitgliedsbeitrag')
        _, got = self.preview([(date(2026, 5, 4), 'Beitrag Max Mustermann', 8100, 30.0, None),
                               (date(2026, 5, 4), 'Beitrag Erika Musterfrau', 8100, 30.0, None)])
        self.assertEqual((got['Beitrag Max Mustermann']['bank_id'], got['Beitrag Erika Musterfrau']['bank_id']), (max_, erika))

    def test_weak_or_ambiguous_is_not_guessed(self):
        self.bank('2026-05-04', 3000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau Beitrag')
        self.bank('2026-05-04', 3000, 'Max Mustermann', 'SEPA-GUTSCHRIFT Max Mustermann Beitrag')
        self.bank('2026-05-04', 10000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau Jahresbeitrag 2026')
        self.bank('2026-05-13', -3000, 'Muster Telekom', 'LASTSCHRIFT Muster Telekom Rechnung')
        self.bank('2026-05-14', 3000, 'Ruecklastschrift Fremd', 'GUTSCHRIFT')
        p, got = self.preview([
            (date(2026, 5, 4), 'Mitgliedsbeitrag', 8100, 30.0, None),                      # nur Allerweltswort (Beitrag?) → kein Treffer
            (date(2026, 5, 4), 'Spende WK', 1371, 100.0, None),                           # gleicher Betrag/Tag, kein Bezug
            (date(2026, 5, 10), 'LASTSCHRIFT Rücklastschrift Beitrag', 8100, -30.0, None),  # nur Buchungsart gemeinsam
            (date(2026, 5, 14), 'Rücklastschrift Fremd', 8100, -30.0, None),              # Vorzeichen passt nicht zur Gutschrift
        ])
        self.assertEqual([r['status'] for r in p['rows']], ['fehlt'] * 4)
        uebernahme.execute(self.s, p, 'Test')
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM bank WHERE state='open'").fetchone()[0], 5)

    def test_tie_and_no_elimination(self):
        self.bank('2026-05-04', 3000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau Musterverein Ortsgruppe')
        self.bank('2026-05-04', 3000, 'Max Mustermann', 'SEPA-GUTSCHRIFT Max Mustermann Musterverein Ortsgruppe')
        _, got = self.preview([(date(2026, 5, 4), 'Musterverein Ortsgruppe', 8100, 30.0, None),   # beide gleich gut → mehrdeutig
                               (date(2026, 5, 6), 'Erika Beitrag', 8100, 30.0, None)])            # nur ein Namenswort → zu schwach
        self.assertEqual(got['Musterverein Ortsgruppe']['status'], 'fehlt')                     # bekommt nicht den „Rest“
        self.assertIn('Mehrere passende Umsätze', got['Musterverein Ortsgruppe']['note'])
        self.assertEqual(got['Erika Beitrag']['status'], 'fehlt')

    def test_equal_bookings_are_distributed(self):
        first = self.bank('2026-04-11', 3000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau Beitrag Kinder')
        second = self.bank('2026-04-11', 3000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau Beitrag Kinder')
        p, _ = self.preview([(date(2026, 4, 11), 'Erika Musterfrau Beitrag', 8100, 30.0, None),
                             (date(2026, 4, 11), 'Erika Musterfrau Beitrag', 8100, 30.0, None)])
        self.assertEqual(sorted(r['bank_id'] for r in p['rows']), [first, second])
        self.assertTrue(all('gleichwertig' in r['note'] for r in p['rows']))
        uebernahme.execute(self.s, p, 'Test')
        texts = [r[0] for r in self.s.db.execute('SELECT description FROM journals ORDER BY id')]
        self.assertTrue(all(t.startswith('SEPA-GUTSCHRIFT Erika Musterfrau') for t in texts))    # Text des Kontoauszugs
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM bank WHERE state='booked'").fetchone()[0], 2)

    def test_equal_pair_next_to_clear_match(self):
        """Wie in den echten Daten: eine Zeile passt eindeutig, zwei weitere sind gleichwertig."""
        clear = self.bank('2026-04-11', 3000, 'Erika Musterfrau', 'Erika Musterfrau Kinderbeitrag Familie Musterhaus')
        a = self.bank('2026-04-11', 3000, 'Max Mustermann', 'Max Mustermann Familie Musterhaus')
        b = self.bank('2026-04-11', 3000, 'Max Mustermann', 'Max Mustermann Familie Musterhaus')
        p, _ = self.preview([(date(2026, 4, 11), 'Erika Musterfrau Kinderbeitrag Familie Musterhaus', 8100, 30.0, None),
                             (date(2026, 4, 11), 'Max Mustermann Familie', 8100, 30.0, None),
                             (date(2026, 4, 11), 'Max Mustermann Familie', 8100, 30.0, None)])
        self.assertEqual([r['bank_id'] for r in p['rows']], [clear, a, b])
        self.assertEqual([('gleichwertig' in r['note']) for r in p['rows']], [False, True, True])

    def test_double_excel_row_loses(self):
        self.bank('2026-01-27', -70686, 'Druckerei Beispiel', 'Druckerei Beispiel Plakate Wahlkampf Auftrag')
        p, got = self.preview([(date(2026, 1, 27), 'Druckerei Beispiel Plakate Wahlkampf', 4210, -706.86, None),
                               (date(2026, 1, 27), 'Druckerei Beispiel Plakate', 4210, -706.86, None)])
        self.assertEqual(got['Druckerei Beispiel Plakate Wahlkampf']['status'], 'zuordnen')
        self.assertEqual(got['Druckerei Beispiel Plakate']['status'], 'fehlt')
        self.assertIn('doppelte Zeile in der Excel', got['Druckerei Beispiel Plakate']['note'])

    def test_different_bookings_stay_ambiguous(self):
        self.bank('2026-04-11', 3000, 'Erika Musterfrau', 'Erika Musterfrau Zahlung')
        self.bank('2026-04-11', 3000, 'Erika Musterfrau', 'Erika Musterfrau Zahlung')
        p, _ = self.preview([(date(2026, 4, 11), 'Erika Musterfrau Beitrag', 8100, 30.0, None),
                             (date(2026, 4, 11), 'Erika Musterfrau Kreis', 5100, 30.0, None)])     # andere Kontierung
        self.assertEqual([r['status'] for r in p['rows']], ['fehlt', 'fehlt'])

    def test_edge_without_text_is_not_created(self):
        self.bank('2026-03-01', -4500, 'Vermieter GmbH', 'DAUERAUFTRAG Vermieter GmbH')
        self.bank('2026-03-31', 100, 'Bank', 'Zinsen')
        _, got = self.preview([(date(2026, 2, 27), 'Miete Geschaeftsstelle Maerz', 4210, -45.0, None)])
        row = got['Miete Geschaeftsstelle Maerz']
        self.assertEqual(row['status'], 'fehlt')
        self.assertIn('Möglicherweise schon vorhanden (gleicher Betrag am 01.03.2026)', row['note'])

    def test_old_statement_without_hash_counts(self):
        self.s.db.execute("INSERT INTO imports VALUES('h-alt','alt.xls','2026-01-01')")
        self.s.db.execute("INSERT INTO bank(booked,amount,name,reference,source,row_no,fingerprint) VALUES('2026-02-01',100,'Bank','Zinsen','alt.xls',1,'x1')")
        self.s.db.execute("INSERT INTO bank(booked,amount,name,reference,source,row_no,fingerprint) VALUES('2026-02-28',100,'Bank','Zinsen','alt.xls',2,'x2')")
        self.s.db.commit()
        _, got = self.preview([(date(2026, 2, 10), 'Spende Unbekannt Beispiel', 1371, 77.0, None)])
        self.assertEqual(got['Spende Unbekannt Beispiel']['status'], 'fehlt')

    def test_creditor_id_and_weak_words(self):
        self.assertEqual(uebernahme.words('LASTSCHRIFT DE98ZZZ09999999999 Mitgliedsbeitrag März 2026'), set())
        self.assertFalse(uebernahme.strong({'erika'}))
        self.assertTrue(uebernahme.strong({'erika', 'musterfrau'}))
        self.assertTrue(uebernahme.strong({'de89370400440532013000'}))

    def test_outgoing_invoice_not_booked_twice(self):
        member = self.s.member({'number': 'T1', 'name': 'Erika Musterfrau', 'address': 'Weg 1', 'birth': '2000-01-01',
                                'joined': '2024-01-01', 'kv': '5100'}, 'Test')
        self.s.save_settings({'issuer': 'Test', 'issuer_address': 'Test 1', 'iban': 'DE89 3704 0044 0532 0130 00', 'age_basis': 'jan1'}, 'Test')
        number = self.s.rows('SELECT number FROM invoices WHERE id=?', (self.s.issue_dues(2026, [member], 'Test')[0],))[0]['number']
        total = self.s.rows('SELECT total FROM invoices WHERE number=?', (number,))[0]['total']
        bid = self.bank('2026-05-04', total, 'Erika Musterfrau', f'SEPA-GUTSCHRIFT Erika Musterfrau {number}')
        self.bank('2026-05-31', 100, 'Bank', 'Zinsen')
        p, got = self.preview([(date(2026, 5, 4), f'Erika Musterfrau Beitrag {number}', 8100, total / 100, None)])
        row = list(got.values())[0]
        self.assertEqual((row['status'], row['bank_id']), ('vorhanden', bid))
        self.assertIn('Ausgangsrechnung', row['note'])

    def test_modified_master_again_no_duplicate(self):
        first = self.master([(date(2025, 3, 2), 'Spende Beispiel Immobilien Wahlkampf', 1371, 250.0, None)])
        uebernahme.execute(self.s, uebernahme.preview(self.s, first, 'master.xlsx'), 'Test')
        p, got = self.preview([(date(2025, 3, 2), 'Spende Beispiel Immobilien Wahlkampf Nachtrag', 1371, 250.0, None)])
        self.assertEqual(list(got.values())[0]['status'], 'vorhanden')      # schon aus der ersten Übernahme verbucht
        uebernahme.execute(self.s, p, 'Test')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bank').fetchone()[0], 1)

    def test_statement_after_master_warns(self):
        uebernahme.execute(self.s, uebernahme.preview(self.s, self.master([(date(2026, 6, 2), 'Erika Musterfrau Spende', 1371, 30.0, None)]),
                                                      'master.xlsx'), 'Test')
        raw = (FIXTURES / 'umsaetze-kopf-oben.xls').read_bytes()
        rows = {r['row_no']: r for r in self.s.preview_bank(raw, 'bank.xls')['rows']}
        hit = [r for r in rows.values() if r.get('amount') == 3000][0]
        self.assertFalse(hit['selected'])
        self.assertIn('Excel-Übernahme', hit['warning'])

    def test_preview_outdated_after_reversal(self):
        bid = self.bank('2026-05-04', 3000, 'Erika Musterfrau', 'Erika Musterfrau Spende Beispiel')
        p, _ = self.preview([(date(2026, 5, 4), 'Erika Musterfrau Spende Beispiel', 1371, 30.0, None)])
        workflow_park = __import__('workflow')
        workflow_park.park(self.s, bid, 'Test', 'Test')
        self.s.reverse_bank(bid, 'Test')                                   # wieder offen, aber storniert
        with self.assertRaises(ValueError):
            uebernahme.execute(self.s, p, 'Test')

    def test_year_note_from_bank_date(self):
        self.bank('2026-01-02', -4500, 'Muster Vermietung', 'Muster Vermietung Geschaeftsstelle')
        self.bank('2026-01-31', 100, 'Bank', 'Zinsen')
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'Umsatz 2025'
        sheet.append(['Valuta', 'Verwendungszweck', 'Haushaltposten', 'Betrag', 'Kostenstelle'])
        sheet.append([date(2025, 12, 30), 'Muster Vermietung Geschaeftsstelle', 4210, -45.0, None])
        out = io.BytesIO()
        book.save(out)
        row = uebernahme.preview(self.s, out.getvalue(), 'm.xlsx')['rows'][0]
        self.assertEqual(row['status'], 'zuordnen')
        self.assertIn('Bankdatum 2026 – Haushaltsjahr laut Blatt: 2025', row['note'])

    def test_many_rows_fast(self):
        import time
        tag = lambda i: ''.join(chr(97 + int(d)) for d in f'{i:04d}')                    # noqa: E731  eindeutige Namenswörter
        banks = [{'id': i, 'booked': f'2026-{1 + i % 12:02d}-{1 + i % 28:02d}', 'amount': 3000, 'name': f'Vor{tag(i)} Nach{tag(i)}',
                  'reference': f'Beitrag Vor{tag(i)} Nach{tag(i)}'} for i in range(1500)]
        items = [{'booked': b['booked'], 'amount': 3000, 'name': b['name'], 'reference': b['reference'], 'plan': 'neu',
                  'year_sheet': 2026, 'sheet': 'Umsatz 2026', 'row_no': b['id']} for b in banks]
        start = time.monotonic()
        found, unclear, _ = uebernahme.match_loose(items, banks)
        self.assertEqual((len(found), len(unclear)), (1500, 0))
        self.assertLess(time.monotonic() - start, 20)

    def test_edges_and_gaps(self):
        start = self.bank('2026-01-05', -4500, 'Muster Vermietung', 'DAUERAUFTRAG Muster Vermietung Miete', 'auszug-1')
        self.bank('2026-03-31', 100, 'Bank', 'Zinsen', 'auszug-1')
        self.bank('2026-06-01', 100, 'Bank', 'Zinsen', 'auszug-2')                              # Lücke April/Mai
        self.bank('2026-08-31', 100, 'Bank', 'Zinsen', 'auszug-2')
        p, got = self.preview([
            (date(2026, 1, 2), 'Miete Muster Vermietung', 4210, -45.0, None),        # 3 Tage vor Auszugsbeginn, steht im Auszug
            (date(2026, 1, 1), 'Neujahrsspende Beispiel', 1371, 50.0, None),          # vor dem Auszug, kein Kandidat → neu
            (date(2026, 5, 2), 'Spende in der Lücke', 1371, 20.0, None),              # zwischen zwei Auszügen → neu
            (date(2026, 7, 1), 'Spende im zweiten Auszug', 1371, 20.0, None),         # im Auszug, fehlt dort → fehlt
        ])
        self.assertEqual((got['Miete Muster Vermietung']['status'], got['Miete Muster Vermietung']['bank_id']), ('zuordnen', start))
        self.assertEqual(got['Neujahrsspende Beispiel']['status'], 'neu')
        self.assertEqual(got['Spende in der Lücke']['status'], 'neu')
        self.assertEqual(got['Spende im zweiten Auszug']['status'], 'fehlt')
        self.assertIn('05.01.2026 – 31.03.2026, 01.06.2026 – 31.08.2026', ' '.join(p['notes']))

    def test_excel_rows_do_not_count_as_statement(self):
        uebernahme.execute(self.s, uebernahme.preview(self.s, self.master([(date(2026, 2, 1), 'Spende Alt', 1371, 5.0, None)]), 'alt.xlsx'), 'Test')
        self.assertEqual(uebernahme.statement_spans(self.s.rows('SELECT * FROM bank')), [])

    def test_bill_found_with_bank_text(self):
        import verbindlichkeiten
        verbindlichkeiten.create(self.s, {'creditor': 'Druckerei Beispiel KG', 'iban': 'DE89370400440532013000', 'ext_number': 'D-77',
                                          'issued': '2026-06-01', 'due': '', 'kv': '5500',
                                          'lines': [{'description': 'Flyer', 'amount': '1234,56', 'account': '4430', 'title': 'H32'}]}, 'Test')
        bid = self.bank('2026-06-05', -123456, 'Druckerei Beispiel KG', 'ÜBERWEISUNG Druckerei Beispiel KG DE89370400440532013000 Flyer D-77')
        p, got = self.preview([(date(2026, 6, 3), 'Druckerei Beispiel Flyer Sommerfest', 5500, -1234.56, 'KV Mitte')])
        hit = got['Druckerei Beispiel Flyer Sommerfest']
        self.assertEqual((hit['status'], hit['bank_id']), ('vorhanden', bid))
        self.assertIn('Eingangsrechnung', hit['note'])
        uebernahme.execute(self.s, p, 'Test')
        self.assertEqual(self.s.db.execute("SELECT COALESCE(SUM(debit-credit),0) FROM entries WHERE account='KV'").fetchone()[0], 0)

    def test_notes_kept_and_unknown_code_open(self):
        self.bank('2026-05-04', 3000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau')
        self.bank('2026-05-31', 100, 'Bank', 'Zinsen')
        _, got = self.preview([(date(2026, 5, 5), 'Erika Musterfrau Kreisbeitrag', 5100, 30.0, 'KV Mitte'),
                               (date(2026, 5, 6), 'Unbekannt Beispiel', 9999, 7.0, None)])
        note = got['Erika Musterfrau Kreisbeitrag']['note']
        self.assertIn('passt nicht zum Posten 5100', note)
        self.assertIn('Zugeordnet über Betrag und Text', note)
        self.assertEqual(got['Unbekannt Beispiel']['status'], 'fehlt')        # im Auszug nicht vorhanden → nicht anlegen
        self.assertIn('9999 ist im Programm nicht bekannt', got['Unbekannt Beispiel']['note'])

    def test_unknown_code_with_bank_row_is_parked(self):
        bid = self.bank('2026-05-06', 70000, 'Beispiel Verlag GmbH', 'GUTSCHRIFT Beispiel Verlag GmbH Anzeige Heft 3')
        p, got = self.preview([(date(2026, 5, 6), 'Beispiel Verlag GmbH Anzeige Heft 3', 8800, 700.0, None)])
        hit = got['Beispiel Verlag GmbH Anzeige Heft 3']
        self.assertEqual((hit['status'], hit['bank_id']), ('parken', bid))
        counts = uebernahme.execute(self.s, p, 'Test')
        self.assertEqual((counts['parken'], counts['zuordnen'], counts['offen']), (1, 0, 0))
        self.assertEqual(self.s.db.execute('SELECT state FROM bank WHERE id=?', (bid,)).fetchone()[0], 'parked')
        title = self.s.db.execute("SELECT title FROM entries WHERE account='1370'").fetchone()[0]
        self.assertIn('Posten 8800 (im Programm nicht bekannt)', title)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bank').fetchone()[0], 1)   # kein zweiter Umsatz

    def test_bill_wins_over_parking_unknown_code(self):
        import verbindlichkeiten
        verbindlichkeiten.create(self.s, {'creditor': 'Druckerei Beispiel KG', 'iban': 'DE89370400440532013000', 'ext_number': 'D-77',
                                          'issued': '2026-06-01', 'due': '', 'kv': 'LV',
                                          'lines': [{'description': 'Flyer', 'amount': '1234,56', 'account': '4430', 'title': 'H32'}]}, 'Test')
        bid = self.bank('2026-06-05', -123456, 'Druckerei Beispiel KG', 'ÜBERWEISUNG Druckerei Beispiel KG DE89370400440532013000 Flyer D-77')
        for code in (None, 8800):
            _, got = self.preview([(date(2026, 6, 4), 'Druckerei Beispiel Flyer D-77', code, -1234.56, None)])
            hit = got['Druckerei Beispiel Flyer D-77']
            self.assertEqual((hit['status'], hit['bank_id']), ('vorhanden', bid), code)       # bleibt offen für die Rechnung
            self.assertIn('Eingangsrechnung', hit['note'])
        _, got = self.preview([(date(2026, 6, 4), 'Druckerei Beispiel Flyer D-77', 1370, -1234.56, None)])
        self.assertEqual(got['Druckerei Beispiel Flyer D-77']['status'], 'parken')            # 1370 bewusst geparkt

    def test_parking_reason_not_mixed_between_codes(self):
        self.bank('2026-05-06', 1500, 'Beispiel Bank', 'Gutschrift Beispiel Bank')
        self.bank('2026-05-06', 1500, 'Beispiel Bank', 'Gutschrift Beispiel Bank 2')
        _, got = self.preview([(date(2026, 5, 6), 'Beispiel Bank Gutschrift A', 8800, 15.0, 'KV Nord'),
                               (date(2026, 5, 6), 'Beispiel Bank Gutschrift B', None, 15.0, None)])
        for text in ('Beispiel Bank Gutschrift A', 'Beispiel Bank Gutschrift B'):
            self.assertNotIn('gleichwertig', got[text]['note'], text)                          # verschiedene Klärungsgründe

    def test_stale_preview_rejected(self):
        self.bank('2026-05-04', 3000, 'Erika Musterfrau', 'SEPA-GUTSCHRIFT Erika Musterfrau')
        p, _ = self.preview([(date(2026, 1, 4), 'Alte Spende Beispiel', 1371, 30.0, None)])
        self.bank('2026-01-04', 3000, 'Alte Spende Beispiel', 'GUTSCHRIFT', 'auszug-0')         # später importierter Auszug
        with self.assertRaises(ValueError):
            uebernahme.execute(self.s, p, 'Test')

    def test_words(self):
        self.assertEqual(uebernahme.words('SEPA-GUTSCHRIFT Rücklastschrift Müller 2026 DE89370400440532013000 EREF: 12'),
                         {'ruecklastschrift', 'mueller', 'de89370400440532013000'})


class UebernahmeHttpTests(unittest.TestCase):
    def test_preview_import_and_backup(self):
        import app
        tmp = tempfile.TemporaryDirectory()
        old = app.DATA, app.DB, app.BACKUPS
        try:
            app.DATA = Path(tmp.name) / 'Daten'
            app.DATA.mkdir()
            app.DB = app.DATA / 'finanzen.sqlite3'
            app.BACKUPS = Path(tmp.name) / 'Sicherungen'
            server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f'http://127.0.0.1:{server.server_port}'
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

            def post(path, data, csrf=''):
                req = urllib.request.Request(url + path, json.dumps(data).encode(),
                                             {'Content-Type': 'application/json', 'Origin': url, 'X-CSRF-Token': csrf})
                return json.load(client.open(req))

            code = post('/api/login-code', {'token': app.STOP_TOKEN})['code']
            csrf = post('/api/login-device', {'code': code})['csrf']
            p = post('/api/preview-master', {'filename': 'master.xlsx', 'content': base64.b64encode(master_xlsx()).decode()}, csrf)
            self.assertEqual(len(p['rows']), 15)
            self.assertNotIn('fingerprint', p['rows'][0])
            r = post('/api/import-master', {'token': p['token']}, csrf)
            self.assertEqual(r['counts']['neu'], 11)
            self.assertTrue((app.BACKUPS / 'Vor Änderungen' / r['backup']).exists())
            self.assertEqual(len(list((app.DATA / 'Importe').glob('*.xlsx'))), 1)
            server.shutdown()
            server.server_close()
        finally:
            app.DATA, app.DB, app.BACKUPS = old
            tmp.cleanup()


if __name__ == '__main__':
    unittest.main(verbosity=2)
