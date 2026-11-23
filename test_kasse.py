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
import kontenrahmen
import mitgliederkonto
import rollen
import workflow
from core import Store
from formate import german_date

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
        self.assertEqual([(k['name'], k['account'], k['balance'], k['open']) for k in m['cash']['kassen']], [('Bargeldkasse', '1400', 6000, 1)])
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


class MehrereKassenTests(Base):
    """Ab 0.60: weitere Kassen (PayPal) neben der Bargeldkasse – eigenes Konto, eigene Belegnummern, eigener Bestand."""

    def paypal(self, amount='50', **more):
        return kasse.add(self.s, {'name': 'PayPal', 'account': '1430', 'prefix': 'PP', 'day': START, 'amount': amount, **more}, 'Test')

    def entry(self, kind, amount, offset=1, text='Testbeleg', name='', kasse_id=0):
        return kasse.create(self.s, {'kind': kind, 'amount': amount, 'day': self.day(offset), 'text': text, 'name': name, 'kasse': kasse_id}, 'Test')

    def lines(self, bid):
        return [(e['account'], e['debit'], e['credit']) for e in self.s.rows(
            'SELECT e.account,e.debit,e.credit FROM entries e JOIN journals j ON j.id=e.journal_id WHERE j.source=? ORDER BY e.id', (f'bank:{bid}',))]

    def test_alte_datenbank_laeuft_ohne_umstellung(self):
        """Stand 0.55: bank ohne Spalte kasse, keine Tabelle kassen – Belege, Beginn (cash_start) und K-Nummern bleiben, wie sie sind."""
        kasse.set_opening(self.s, START, '100', 'Test')
        old = self.entry('out', '10', text='Porto')
        before = (self.s.rows('SELECT id,booked,amount,bank_ref,ledger,state FROM bank'), self.s.rows('SELECT * FROM settings'),
                  self.s.rows('SELECT source FROM journals ORDER BY id'))
        self.s.db.execute('DROP TABLE kassen')
        self.s.db.execute('ALTER TABLE bank DROP COLUMN kasse')
        self.s.db.commit()
        self.s.db.close()
        self.s = Store(Path(self.tmp.name) / 't.db')                                 # Öffnen mit 0.60 ergänzt nur
        self.assertEqual((self.s.rows('SELECT id,booked,amount,bank_ref,ledger,state FROM bank'), self.s.rows('SELECT * FROM settings'),
                          self.s.rows('SELECT source FROM journals ORDER BY id')), before)
        self.assertEqual(self.s.rows('SELECT kasse FROM bank'), [{'kasse': 0}])
        self.assertEqual(kasse.liste(self.s), [{'id': 0, 'name': 'Bargeldkasse', 'account': '1400', 'prefix': 'K', 'art': 'bar', 'aktiv': 1, 'start': START}])
        self.assertEqual((kasse.start(self.s), kasse.balance(self.s)), (START, 9000))
        self.assertEqual(self.entry('out', '5')['number'], f'K-{YEAR}-0002')
        self.s.book(old['id'], [{'amount': '-10', 'account': '4220', 'kv': 'LV'}], 'Test')
        self.assertEqual(self.lines(old['id']), [('1400', 0, 1000), ('4220', 1000, 0)])

    def test_einrichten_konto_kuerzel_anfangsbestand(self):
        self.assertEqual(kasse.free_account(self.s), '1430')                           # frei: nicht 1400, 1410 Portokasse, 1420 Bank
        pp = self.paypal()
        k = kasse.eine(self.s, pp)
        self.assertEqual(k, {'id': pp, 'name': 'PayPal', 'account': '1430', 'prefix': 'PP', 'start': START, 'art': 'bar', 'aktiv': 1})
        self.assertEqual(self.s.rows("SELECT name,kind FROM accounts WHERE code='1430'"), [{'name': 'PayPal', 'kind': 'asset'}])
        self.assertEqual(kontenrahmen.group_of('1430')[1], 'Geldbestände')
        self.assertEqual((kasse.balance(self.s, kasse=pp), kasse.opening(self.s, pp)['amount'], kasse.start(self.s, pp)), (5000, 5000, START))
        self.assertEqual((kasse.start(self.s), kasse.opening(self.s), kasse.balance(self.s)), (None, None, 0))   # Bargeldkasse unberührt
        totals = berichte._totals(haushalt.report(self.s, YEAR))
        self.assertEqual((totals['income'][3] or 0, totals['expense'][3] or 0), (0, 0))                          # zählt nicht im Haushalt
        self.assertEqual(kasse.free_account(self.s), '1431')
        kasse.set_opening(self.s, START, '70', 'Test', pp)                             # ändern: gegengebucht, nur diese Kasse
        self.assertEqual((kasse.balance(self.s, kasse=pp), kasse.opening(self.s, pp)['amount']), (7000, 7000))
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)
        s = kasse.summary(self.s)
        self.assertEqual([(x['name'], x['start'], x['balance'], x['open']) for x in s['kassen']], [('Bargeldkasse', None, 0, 0), ('PayPal', START, 7000, 0)])
        self.assertEqual((s['open'], s['next_account']), (0, '1431'))

    def test_einrichten_weist_kollisionen_ab(self):
        self.paypal()
        ok = {'name': 'Stripe', 'account': '1431', 'prefix': 'ST', 'day': START, 'amount': '0'}
        for change, text in (({'account': '1400'}, '1401–1499'), ({'account': '1420'}, 'gibt es schon'), ({'account': '1410'}, 'gibt es schon'),
                             ({'account': '1430'}, 'gibt es schon'), ({'account': '4220'}, '1401–1499'), ({'account': '1500'}, '1401–1499'),
                             ({'account': '143'}, '1401–1499'), ({'name': 'paypal'}, 'gibt es schon'), ({'name': 'Bargeldkasse'}, 'gibt es schon'),
                             ({'name': ''}, 'Namen'), ({'name': 'A"B'}, 'Namen'), ({'prefix': 'PP'}, 'vergeben'), ({'prefix': 'K'}, 'vergeben'),
                             ({'prefix': 'E'}, 'vergeben'), ({'prefix': 'P1'}, 'Belegkürzel'), ({'prefix': ''}, 'Belegkürzel'),
                             ({'amount': '-1'}, 'negativ'), ({'day': (date.today() + timedelta(days=1)).isoformat()}, 'Zukunft')):
            with self.assertRaises(ValueError, msg=change) as cm:
                kasse.add(self.s, {**ok, **change}, 'Test')
            self.assertIn(text, str(cm.exception), change)
        self.assertEqual([k['name'] for k in kasse.liste(self.s)], ['Bargeldkasse', 'PayPal'])   # nichts halb angelegt
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM accounts WHERE code='1431'").fetchone()[0], 0)
        kasse.add(self.s, ok, 'Test')
        self.assertEqual([k['account'] for k in kasse.liste(self.s)], ['1400', '1430', '1431'])
        with self.assertRaises(ValueError):
            kasse.eine(self.s, 99)

    def test_beleg_bucht_das_konto_der_kasse_mit_eigenem_nummernkreis(self):
        kasse.set_opening(self.s, START, '100', 'Test')
        pp = self.paypal()
        bar = self.entry('out', '10', text='Porto')
        fee = self.entry('out', '1,50', text='Gebühr', kasse_id=pp)
        inc = self.entry('in', '20', text='Teilnahmebeitrag', kasse_id=pp)
        self.assertEqual((bar['number'], fee['number'], inc['number']), (f'K-{YEAR}-0001', f'PP-{YEAR}-0001', f'PP-{YEAR}-0002'))
        row = self.s.rows('SELECT ledger,kasse,state,amount,source FROM bank WHERE id=?', (fee['id'],))[0]
        self.assertEqual(row, {'ledger': 'cash', 'kasse': pp, 'state': 'open', 'amount': -150, 'source': 'PayPal · Ausgabe'})
        self.assertEqual((kasse.balance(self.s), kasse.balance(self.s, kasse=pp)), (9000, 6850))   # offene Belege zählen je Kasse
        self.s.book(fee['id'], [{'amount': '-1,50', 'account': '4225', 'kv': 'LV'}], 'Test')
        self.s.book(inc['id'], [{'amount': '20', 'account': '8590', 'kv': '5100'}], 'Test')
        self.s.book(bar['id'], [{'amount': '-10', 'account': '4220', 'kv': 'LV'}], 'Test')
        self.assertEqual(self.lines(fee['id']), [('1430', 0, 150), ('4225', 150, 0)])              # PayPal-Konto, nicht 1400/1420
        self.assertEqual(self.lines(inc['id']), [('1430', 2000, 0), ('8590', 0, 2000)])
        self.assertEqual(self.lines(bar['id']), [('1400', 0, 1000), ('4220', 1000, 0)])
        self.assertEqual((kasse.balance(self.s), kasse.balance(self.s, kasse=pp)), (9000, 6850))
        book = kasse.book(self.s, YEAR, pp)
        self.assertEqual(([x['number'] for x in book['lines']], book['income'], book['expense'], book['end'], book['kasse']['name']),
                         ([None, f'PP-{YEAR}-0001', f'PP-{YEAR}-0002'], 2000, 150, 6850, 'PayPal'))
        self.assertEqual([x['number'] for x in kasse.book(self.s, YEAR)['lines']], [None, f'K-{YEAR}-0001'])   # Bargeld-Kassenbuch ohne PayPal
        csv = kasse.csv(self.s, YEAR, pp).decode('utf-8-sig')
        self.assertIn(f'PP-{YEAR}-0002', csv)
        self.assertNotIn(f'K-{YEAR}-0001', csv)
        # Parken und Auflösen laufen über das Konto der Kasse
        out = self.entry('out', '5', offset=2, text='unklar', kasse_id=pp)
        workflow.park(self.s, out['id'], 'Beleg fehlt', 'Test')
        self.assertEqual(self.lines(out['id']), [('1430', 0, 500), ('1370', 500, 0)])
        self.assertEqual([(b['id'], b['kasse']) for b in workflow.details(self.s, YEAR)['parked']], [(out['id'], pp)])

    def test_bestand_je_kasse_nie_negativ(self):
        kasse.set_opening(self.s, START, '100', 'Test')
        pp = self.paypal('20')
        with self.assertRaises(ValueError) as cm:                                     # Bargeld reicht, PayPal nicht
            self.entry('out', '25', kasse_id=pp)
        self.assertIn('PayPal', str(cm.exception))
        self.assertIn('negativ', str(cm.exception))
        self.entry('out', '90')                                                        # Bargeld: unabhängig von PayPal
        with self.assertRaises(ValueError):
            self.entry('out', '15')
        inc = self.entry('in', '10', offset=0, kasse_id=pp)
        out = self.entry('out', '25', offset=0, kasse_id=pp)
        with self.assertRaises(ValueError):
            kasse.void(self.s, inc['id'], 'Tippfehler', 'Test')                         # sonst PayPal negativ
        with self.assertRaises(ValueError):
            kasse.create(self.s, {'kind': 'out', 'amount': '1', 'day': f'{YEAR - 1}-12-31', 'text': 'x', 'kasse': pp}, 'Test')   # vor Beginn
        with self.assertRaises(ValueError):
            kasse.create(self.s, {'kind': 'out', 'amount': '1', 'day': TODAY, 'text': 'x', 'kasse': 99}, 'Test')                 # unbekannte Kasse

    def test_ungueltig_und_storno(self):
        pp = self.paypal('50')
        out = self.entry('out', '30', text='Tippfehler', kasse_id=pp)
        with self.assertRaises(ValueError):                                           # Kassenbelege nie über den Weg der Kontoumsätze
            workflow.ungueltig(self.s, out['id'], 'doppelt', 'Test')
        kasse.void(self.s, out['id'], 'doppelt erfasst', 'Test')
        self.assertEqual((kasse.balance(self.s, kasse=pp), kasse.book(self.s, YEAR, pp)['void'][0]['note']), (5000, 'doppelt erfasst'))
        self.assertEqual(kasse.book(self.s, YEAR)['void'], [])                         # nicht im Kassenbuch der Bargeldkasse
        inc = self.entry('in', '10', text='Spende', kasse_id=pp)
        self.s.book(inc['id'], [{'amount': '10', 'account': '8590', 'kv': 'LV'}], 'Test')
        self.s.reverse_bank(inc['id'], 'Test')                                         # Storno: gegengebucht auf 1430, Beleg wieder offen, Bestand gleich
        self.assertEqual((self.s.rows('SELECT state FROM bank WHERE id=?', (inc['id'],))[0]['state'], kasse.balance(self.s, kasse=pp)), ('open', 6000))
        self.assertEqual(self.s.db.execute("SELECT SUM(e.debit)-SUM(e.credit) FROM entries e JOIN journals j ON j.id=e.journal_id "
                                           "WHERE e.account='1430' AND j.source NOT LIKE 'kasse-anfang:%'").fetchone()[0], 0)
        self.assertEqual([x['state'] for x in kasse.book(self.s, YEAR, pp)['lines'] if x['number'] == inc['number']], ['reversed', 'storno', 'open'])
        # Storno einer Aufladung aus dem Kontoauszug, deren Geld schon ausgegeben ist: abgelehnt (Bestand dieser Kasse würde negativ)
        auf = self.bank('-40', self.day(1), 'PayPal Aufladung')
        self.s.book(auf, [{'amount': '-40', 'account': '1430', 'kv': 'LV'}], 'Test')
        self.entry('out', '95', offset=2, text='Saal', kasse_id=pp)
        with self.assertRaises(ValueError) as cm:
            self.s.reverse_bank(auf, 'Test')
        self.assertIn('PayPal', str(cm.exception))
        self.assertIn('schon ausgegeben', str(cm.exception))
        self.assertEqual(self.s.rows('SELECT state FROM bank WHERE id=?', (auf,))[0]['state'], 'booked')   # zurückgerollt

    def test_umbuchung_ueber_2380_und_direkt_aus_dem_kontoauszug(self):
        kasse.set_opening(self.s, START, '100', 'Test')
        pp = self.paypal('0')
        w = self.entry('withdraw', '80', text='', kasse_id=pp)                         # Bank → PayPal
        self.assertEqual(self.s.rows('SELECT state,reference,source FROM bank WHERE id=?', (w['id'],))[0],
                         {'state': 'booked', 'reference': 'Umbuchung von der Bank', 'source': 'PayPal · Umbuchung von der Bank'})
        self.assertEqual(self.lines(w['id']), [('1430', 8000, 0), ('2380', 0, 8000)])
        self.assertEqual((kasse.balance(self.s, kasse=pp), kasse.balance(self.s), kasse.transit_open(self.s)), (8000, 10000, -8000))
        bid = self.bank('-80', self.day(1), 'PayPal Aufladung')
        self.s.book(bid, [{'amount': '-80', 'account': '2380', 'kv': 'LV'}], 'Test')
        self.assertEqual(kasse.transit_open(self.s), 0)
        d = self.entry('deposit', '30', offset=2, text='', kasse_id=pp)                # PayPal → Bank
        self.assertEqual(self.lines(d['id']), [('1430', 0, 3000), ('2380', 3000, 0)])
        self.assertTrue(kasse.is_transit(self.s.rows('SELECT source FROM bank WHERE id=?', (d['id'],))[0]))
        self.assertEqual(kasse.book(self.s, YEAR, pp)['missing_receipt'], 0)           # Umbuchung braucht keinen eigenen Beleg
        with self.assertRaises(ValueError):
            self.entry('deposit', '60', offset=2, text='', kasse_id=pp)                # mehr als da ist
        # Kontoumsatz direkt auf das PayPal-Konto: zählt im Kassenbuch dieser Kasse (wie bisher 1400), nie negativ, nicht vor Beginn
        back = self.bank('30', self.day(2), 'PayPal Auszahlung')
        self.s.book(back, [{'amount': '30', 'account': '2380', 'kv': 'LV'}], 'Test')
        self.assertEqual(kasse.transit_open(self.s), 0)
        zu_viel = self.bank('70', self.day(3), 'PayPal Auszahlung')
        with self.assertRaises(ValueError) as cm:
            self.s.book(zu_viel, [{'amount': '70', 'account': '1430', 'kv': 'LV'}], 'Test')
        self.assertIn('PayPal', str(cm.exception))
        direkt = self.bank('20', self.day(3), 'PayPal Auszahlung')
        self.s.book(direkt, [{'amount': '20', 'account': '1430', 'kv': 'LV'}], 'Test')
        self.assertEqual(self.lines(direkt), [('1420', 2000, 0), ('1430', 0, 2000)])
        book = kasse.book(self.s, YEAR, pp)
        self.assertEqual((book['lines'][-1]['kind'], book['end'], kasse.balance(self.s)), ('bank', 3000, 10000))
        frueh = self.bank('-5', f'{YEAR - 1}-12-30', 'vor Beginn')
        with self.assertRaises(ValueError) as cm:
            self.s.book(frueh, [{'amount': '-5', 'account': '1430', 'kv': 'LV'}], 'Test')
        self.assertIn('Beginn des Kassenbuchs', str(cm.exception))

    def test_beleg_nie_direkt_auf_eine_kasse(self):
        kasse.set_opening(self.s, START, '100', 'Test')
        pp = self.paypal('50')
        bar = self.entry('out', '10', text='bar zu PayPal')
        online = self.entry('out', '10', text='PayPal zu bar', kasse_id=pp)
        for bid, account, text in ((bar['id'], '1400', 'eigenes Konto'), (bar['id'], '1430', '2380'), (online['id'], '1430', 'eigenes Konto'),
                                   (online['id'], '1400', '2380')):
            with self.assertRaises(ValueError) as cm:
                self.s.book(bid, [{'amount': '-10', 'account': account, 'kv': 'LV'}], 'Test')
            self.assertIn(text, str(cm.exception), (bid, account))
        self.s.book(online['id'], [{'amount': '-10', 'account': '2380', 'kv': 'LV'}], 'Test')   # über den Geldtransit geht es
        self.assertEqual(self.lines(online['id']), [('1430', 0, 1000), ('2380', 1000, 0)])
        catalog = {a['code']: a for a in kontenrahmen.catalog(self.s)}
        self.assertEqual((catalog['1430']['bank'], catalog['1430']['household'], catalog['1430']['group']), (True, False, 'Geldbestände'))

    def test_berichte_je_kasse(self):
        kasse.set_opening(self.s, START, '80', 'Test')
        pp = self.paypal('40')
        self.entry('out', '20', text='Porto')
        self.entry('out', '5', text='Gebühr', kasse_id=pp)
        self.entry('out', '5', text='Gebühr 2', kasse_id=pp)
        m = berichte.monthly(self.s, TODAY)
        self.assertEqual([(k['name'], k['account'], k['balance'], k['open']) for k in m['cash']['kassen']],
                         [('Bargeldkasse', '1400', 6000, 1), ('PayPal', '1430', 3000, 2)])
        self.assertEqual(sorted(t for t in m['tasks'] if 'Kassenbeleg' in t),
                         ['1 Kassenbeleg(e) verbuchen (Kasse → Bargeldkasse → Buchungsplatz)', '2 Kassenbeleg(e) verbuchen (Kasse → PayPal → Buchungsplatz)'])
        self.assertEqual(m['open_bank']['count'], 0)
        self.assertTrue(berichte.monthly_pdf(self.s, TODAY).startswith(b'%PDF'))
        self.assertTrue(berichte.cash_pdf(self.s, YEAR, kasse_id=pp).startswith(b'%PDF'))
        buf = BytesIO()
        berichte.audit_package(self.s, YEAR, Path(self.tmp.name), buf)
        with zipfile.ZipFile(buf) as z:
            files = {n.split('/', 1)[1]: z.read(n) for n in z.namelist()}
        for name in (f'Kassenbuch-{YEAR}.csv', f'Kassenbuch-{YEAR}.pdf', f'Kassenbuch-PayPal-1430-{YEAR}.csv', f'Kassenbuch-PayPal-1430-{YEAR}.pdf'):
            self.assertIn(name, files)
        bar, online = files[f'Kassenbuch-{YEAR}.csv'].decode('utf-8-sig'), files[f'Kassenbuch-PayPal-1430-{YEAR}.csv'].decode('utf-8-sig')
        self.assertEqual((f'K-{YEAR}-0001' in bar, f'PP-{YEAR}' in bar, f'PP-{YEAR}-0002' in online, f'K-{YEAR}' in online), (True, False, True, False))
        self.assertFalse(any(f'PP-{YEAR}' in line for line in files[f'Kontoumsaetze-{YEAR}.csv'].decode('utf-8-sig').splitlines()))

    def test_rechte(self):
        for path in ('/api/kasse-neu', '/api/kasse-anfang', '/api/kasse-beleg', '/api/kasse-ungueltig'):
            self.assertFalse(rollen.allowed({'buchhaltung': 'lesen', 'verband': 'schreiben', 'mitglieder': 'schreiben'}, rollen.required('POST', path)), path)
            self.assertTrue(rollen.allowed({'buchhaltung': 'schreiben'}, rollen.required('POST', path)), path)
        self.paypal()
        self.assertEqual(rollen.SNAPSHOT['cash'], 'buchhaltung')                      # ohne Buchhaltung: gleiche Form, kein Inhalt
        self.assertEqual(rollen.blank(kasse.summary(self.s)), {'kassen': [], 'open': 0, 'next_account': '', 'transit': {'saldo': 0, 'bank': []}})

    # Befunde der unabhängigen Prüfung von 0.60: nur Kennungen von außen (K1), Geldtransit zugeordnet statt saldiert (W1),
    # Kontonummer, Namen und Dateinamen (H1–H3)

    def offen(self, until=None):
        """Offene Posten des Geldtransits als (Belegnummern je Kasse, Beträge der Kontoumsätze ohne Beleg)."""
        t = kasse.transit(self.s, until)
        return {k: [p['number'] for p in v] for k, v in t['kassen'].items()}, [p['amount'] for p in t['bank']]

    def auf_2380(self, amount, offset, ref='Umbuchung'):
        bid = self.bank(amount, self.day(offset), ref)
        self.s.book(bid, [{'amount': amount, 'account': '2380', 'kv': 'LV'}], 'Test')
        return bid

    def text(self, pdf):
        from pypdf import PdfReader
        return ' '.join(' '.join(page.extract_text().split()) for page in PdfReader(BytesIO(pdf)).pages)

    def test_nur_kennungen_von_aussen(self):
        kasse.set_opening(self.s, START, '100', 'Test')
        pp = self.paypal('50')
        self.assertEqual((kasse.eine(self.s)['account'], kasse.eine(self.s, '')['id'], kasse.eine(self.s, str(pp))['name'], kasse.eine(self.s, pp)['prefix']),
                         ('1400', 0, 'PayPal', 'PP'))
        vorher = self.s.rows('SELECT (SELECT COUNT(*) FROM journals) j,(SELECT COUNT(*) FROM bank) b')
        fremd = {'id': 0, 'name': 'x', 'account': '1420', 'prefix': '../../x', 'start': '2000-01-01'}
        for wert in (fremd, dict(kasse.eine(self.s, pp)), {'id': pp}, [pp], [], {}, 99, -1, True, False, 1.0, float('inf'), 'abc', ' 0 ', '1.0', b'1'):
            for aufruf in (lambda: kasse.eine(self.s, wert), lambda: kasse.set_opening(self.s, START, '5000', 'Test', wert),
                           lambda: kasse.create(self.s, {'kind': 'in', 'amount': '1', 'day': TODAY, 'text': 'x', 'kasse': wert}, 'Test'),
                           lambda: kasse.create(self.s, {'kind': 'withdraw', 'amount': '1', 'day': '2001-05-05', 'kasse': wert}, 'Test'),
                           lambda: kasse.book(self.s, YEAR, wert), lambda: kasse.csv(self.s, YEAR, wert), lambda: kasse.balance(self.s, kasse=wert),
                           lambda: kasse.next_number(self.s, YEAR, wert), lambda: kasse.opening(self.s, wert), lambda: kasse.start(self.s, wert)):
                with self.assertRaises(ValueError, msg=repr(wert)) as cm:
                    aufruf()
                self.assertEqual(str(cm.exception), 'Kasse nicht gefunden.', repr(wert))
        self.assertEqual(self.s.rows('SELECT (SELECT COUNT(*) FROM journals) j,(SELECT COUNT(*) FROM bank) b'), vorher)
        self.assertEqual(kasse.book(self.s, YEAR, kasse.eine(self.s, pp))['kasse']['name'], 'PayPal')      # intern: der Eintrag aus liste

    def test_konto_und_namen(self):
        gut = {'account': '1430', 'prefix': 'PP', 'day': START, 'amount': '0'}
        for konto in ('14٣٠', '１４３０', '14३०'):                                    # Ziffern anderer Schriften sind keine Kontonummer
            with self.assertRaises(ValueError) as cm:
                kasse.add(self.s, {**gut, 'name': 'PayPal', 'account': konto}, 'Test')
            self.assertIn('1401–1499', str(cm.exception), konto)
        for name in ('..', '--', '+49', '1 2', '& +'):
            with self.assertRaises(ValueError) as cm:
                kasse.add(self.s, {**gut, 'name': name}, 'Test')
            self.assertIn('mindestens einem Buchstaben', str(cm.exception), name)
        for name, text in (('Kasse', 'eigenen Namen'), ('kasse', 'eigenen Namen'), ('Bank', 'eigenen Namen'), (' bank ', 'eigenen Namen'),
                           ('Bargeldkasse', 'gibt es schon'), ('BARGELDKASSE', 'gibt es schon')):
            with self.assertRaises(ValueError) as cm:
                kasse.add(self.s, {**gut, 'name': name}, 'Test')
            self.assertIn(text, str(cm.exception), name)
        self.assertEqual((self.s.rows('SELECT * FROM kassen'), self.s.rows("SELECT * FROM accounts WHERE code='1430'")), ([], []))
        self.assertEqual(kasse.add(self.s, {**gut, 'name': 'Bankkasse Nord'}, 'Test'), 1)       # nur der ganze Name ist gesperrt

    def test_dateinamen_im_pruefpaket_eindeutig(self):
        kasse.add(self.s, {'name': 'A&B', 'account': '1430', 'prefix': 'AB', 'day': START, 'amount': '1'}, 'Test')
        kasse.add(self.s, {'name': 'A+B', 'account': '1431', 'prefix': 'AC', 'day': START, 'amount': '2'}, 'Test')
        buf = BytesIO()
        berichte.audit_package(self.s, YEAR, Path(self.tmp.name), buf)
        with zipfile.ZipFile(buf) as z:
            names = [n.split('/', 1)[1] for n in z.namelist()]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sorted(n for n in names if n.startswith('Kassenbuch')),
                         [f'Kassenbuch-A_B-1430-{YEAR}.csv', f'Kassenbuch-A_B-1430-{YEAR}.pdf', f'Kassenbuch-A_B-1431-{YEAR}.csv', f'Kassenbuch-A_B-1431-{YEAR}.pdf'])

    def test_transit_gegenlaeufig_bleibt_offen(self):
        """Gegenbeispiel der Prüfung: Bargeld-Abhebung 50 und PayPal-Umbuchung auf die Bank 50, beide ohne Kontoumsatz – der Saldo
        2380 ist 0, offen sind zwei Posten."""
        kasse.set_opening(self.s, START, '100', 'Test')
        pp = self.paypal('50')
        w = self.entry('withdraw', '50', offset=10, text='')
        d = self.entry('deposit', '50', offset=10, text='', kasse_id=pp)
        self.assertEqual(kasse.transit_open(self.s), 0)
        self.assertEqual(self.offen(), ({0: [w['number']], pp: [d['number']]}, []))
        s = kasse.summary(self.s)
        self.assertEqual(([[(p['number'], p['amount']) for p in k['transit']] for k in s['kassen']], s['transit']),
                         ([[(w['number'], 5000)], [(d['number'], -5000)]], {'saldo': 0, 'bank': []}))
        m = berichte.monthly(self.s, TODAY)
        aufgaben = [t for t in m['tasks'] if '2380' in t]
        self.assertEqual(len(aufgaben), 2)
        self.assertTrue(any('Bargeldkasse: 1 Beleg(e) ohne Gegenseite im Kontoauszug (50,00 €)' in t and w['number'] in t for t in aufgaben), aufgaben)
        self.assertTrue(any('PayPal: 1 Beleg(e) ohne Gegenseite im Kontoauszug (50,00 €)' in t and d['number'] in t for t in aufgaben), aufgaben)
        self.assertIn('1 · 50,00 €', self.text(berichte.monthly_pdf(self.s, TODAY)))
        _, notes = berichte.audit_files(self.s, YEAR, Path(self.tmp.name))
        self.assertEqual(sorted(n for n in notes if '2380' in n), sorted(aufgaben))
        for kid, nummer in ((0, w['number']), (pp, d['number'])):
            self.assertEqual([p['number'] for p in kasse.book(self.s, YEAR, kid)['transit']], [nummer])
            self.assertIn(f'1 Beleg(e) dieser Kasse ohne Gegenseite im Kontoauszug (50,00 €): {nummer}', self.text(berichte.cash_pdf(self.s, YEAR, kasse_id=kid)))
        # die Bankseite der Abhebung kommt: nur die Bargeldkasse ist ausgeglichen – ihr Kassenbuch meldet nichts mehr, PayPal bleibt offen
        self.auf_2380('-50', 10, 'Barabhebung')
        self.assertEqual((kasse.transit_open(self.s), self.offen()), (5000, ({pp: [d['number']]}, [])))
        bar = self.text(berichte.cash_pdf(self.s, YEAR))
        self.assertIn('bei dieser Kasse ausgeglichen', bar)
        self.assertNotIn('ohne Gegenseite', bar)
        self.assertIn('ohne Gegenseite', self.text(berichte.cash_pdf(self.s, YEAR, kasse_id=pp)))
        self.auf_2380('50', 11, 'PayPal Auszahlung')
        self.assertEqual((kasse.transit_open(self.s), self.offen()), (0, ({}, [])))
        self.assertEqual([t for t in berichte.monthly(self.s, TODAY)['tasks'] if '2380' in t], [])

    def test_transit_von_kasse_zu_kasse_ueber_die_bank(self):
        """Geld von PayPal in die Bargeldkasse läuft über die Bank: zwei Belege, zwei Kontoumsätze. Die beiden Belege gleichen sich
        nie gegenseitig aus – erst jeder mit seinem Kontoumsatz."""
        kasse.set_opening(self.s, START, '100', 'Test')
        pp = self.paypal('50')
        d = self.entry('deposit', '10', offset=11, text='', kasse_id=pp)
        w = self.entry('withdraw', '10', offset=11, text='')
        self.assertEqual(self.offen(), ({0: [w['number']], pp: [d['number']]}, []))
        self.auf_2380('10', 11, 'PayPal Auszahlung')
        self.assertEqual(self.offen(), ({0: [w['number']]}, []))
        self.auf_2380('-10', 12, 'Barabhebung')
        self.assertEqual(self.offen(), ({}, []))

    def test_transit_kontoumsatz_ohne_beleg_storno_und_naechster_tag(self):
        kasse.set_opening(self.s, START, '100', 'Test')
        pp = self.paypal('50')
        frueh = self.bank('-7', f'{YEAR - 1}-12-30', 'vor Beginn des Kassenbuchs')
        self.s.book(frueh, [{'amount': '-7', 'account': '2380', 'kv': 'LV'}], 'Test')
        self.assertEqual(self.offen(), ({}, []))                                       # davor kann es keinen Kassenbeleg geben – nur im Saldo
        self.assertEqual(kasse.summary(self.s)['transit'], {'saldo': 700, 'bank': []})
        self.assertEqual([t for t in berichte.monthly(self.s, TODAY)['tasks'] if '2380' in t], ['Saldo des Kontos 2380 nicht ausgeglichen (7,00 €) – Kontenblatt 2380 prüfen'])
        ohne = self.auf_2380('-30', 5, 'Durchlaufposten')                              # Kontoumsatz auf 2380, kein Kassenbeleg
        self.assertEqual(self.offen(), ({}, [-3000]))
        self.assertEqual(self.offen(self.day(4)), ({}, []))                            # zum Vortag gab es ihn noch nicht
        m = berichte.monthly(self.s, TODAY)
        self.assertEqual([t for t in m['tasks'] if '2380' in t],
                         [f'Geldtransit 2380: 1 Buchung(en) aus dem Kontoauszug ohne Kassenbeleg (30,00 €) – {german_date(self.day(5))} -30,00 € – '
                          'anderer Durchlaufposten oder Kassenbeleg fehlt'])
        self.assertIn('1 Buchung(en) auf 2380 ohne Kassenbeleg (30,00 €)', self.text(berichte.monthly_pdf(self.s, TODAY)))
        self.assertIn('bei dieser Kasse ausgeglichen', self.text(berichte.cash_pdf(self.s, YEAR)))       # das ist kein Posten der Bargeldkasse
        w = self.entry('withdraw', '30', offset=5, text='')                            # der Beleg kommt nach: ausgeglichen
        self.assertEqual(self.offen(), ({}, []))
        self.s.reverse_bank(ohne, 'Test')                                              # Storno des Kontoumsatzes: der Beleg ist wieder offen
        self.assertEqual(self.offen(), ({0: [w['number']]}, []))
        self.auf_2380('-30', 5)
        self.assertEqual(self.offen(), ({}, []))
        self.s.reverse_bank(w['id'], 'Test')                                           # Storno des Belegs: der Kontoumsatz steht ohne Beleg da
        self.assertEqual(self.offen(), ({}, [-3000]))
        kasse.void(self.s, w['id'], 'doppelt', 'Test')                                 # ungültige Belege zählen nicht
        self.assertEqual(self.offen(), ({}, [-3000]))
        # zwei gleiche Belege, ein Kontoumsatz: ausgeglichen ist der zeitlich nächste; eine Ausgabe auf 2380 gebucht ist ebenfalls ein Posten
        a = self.entry('withdraw', '30', offset=3, text='')
        b = self.entry('withdraw', '30', offset=6, text='')
        self.assertEqual(self.offen(), ({0: [a['number']]}, []))
        aus = self.entry('out', '4', offset=7, text='an die Bank', kasse_id=pp)
        self.s.book(aus['id'], [{'amount': '-4', 'account': '2380', 'kv': 'LV'}], 'Test')
        self.assertEqual(self.offen(), ({0: [a['number']], pp: [aus['number']]}, []))
        self.assertEqual(kasse.transit(self.s)['kassen'][pp][0]['amount'], -400)
        self.assertNotIn(b['number'], str(kasse.transit(self.s)))


if __name__ == '__main__':
    unittest.main()


class ArtUndPflegeTests(Base):
    """Ab 0.60 (Antworten Lennert Gust 23.11.2026): Art einer weiteren Kasse (Bargeld bzw. Zahlungsdienst ohne Geldtransit, Bestand
    darf im Minus stehen), umbenennen, stilllegen, wieder aktivieren, löschen."""

    def neu(self, name='PayPal', account='1430', prefix='PP', art='dienst', amount='0', day=START):
        return kasse.add(self.s, {'name': name, 'account': account, 'prefix': prefix, 'art': art, 'day': day, 'amount': amount}, 'Test')

    def beleg(self, kid, kind, amount, offset=1, text='Testbeleg'):
        return kasse.create(self.s, {'kind': kind, 'amount': amount, 'day': self.day(offset), 'text': text, 'kasse': kid}, 'Test')['id']

    def auf_konto(self, amount, offset, account='1430'):
        bid = self.bank(amount, self.day(offset), 'PAYPAL EUROPE')
        self.s.book(bid, [{'amount': amount, 'account': account, 'kv': 'LV'}], 'Test')
        return bid

    def lines(self, bid):
        return [(e['account'], e['debit'], e['credit']) for e in self.s.rows(
            'SELECT e.account,e.debit,e.credit FROM entries e JOIN journals j ON j.id=e.journal_id WHERE j.source=? ORDER BY e.id', (f'bank:{bid}',))]

    def stand(self, kid):
        return next(k for k in kasse.summary(self.s)['kassen'] if k['id'] == kid)

    def hinweise(self):
        return berichte._minus_notes(berichte._cash(self.s, TODAY))

    def text(self, pdf):
        from pypdf import PdfReader
        return ' '.join(' '.join(page.extract_text().split()) for page in PdfReader(BytesIO(pdf)).pages)

    def paket(self, year=YEAR):
        buf = BytesIO()
        berichte.audit_package(self.s, year, Path(self.tmp.name), buf)
        with zipfile.ZipFile(buf) as z:
            return {n.split('/', 1)[1]: z.read(n) for n in z.namelist()}

    def abgelehnt(self, text, call, *args, **kw):
        with self.assertRaises(ValueError) as cm:
            call(*args, **kw)
        self.assertIn(text, str(cm.exception))

    def test_zahlungsdienst_beleg_vor_dem_einzug_und_zurueck(self):
        """PayPal zieht vom Bankkonto ein: der Beleg wird vor dem Einzug angenommen (Bestand im Minus, Hinweis), der Kontoumsatz
        wird direkt auf das PayPal-Konto gebucht (Bestand 0, Hinweis weg) – ohne 2380. Auch die Gegenrichtung und Storno."""
        kasse.set_opening(self.s, START, '50', 'Test')
        pp = self.neu()
        self.assertEqual((kasse.eine(self.s, pp)['art'], self.stand(pp)['art'], self.stand(0)['art']), ('dienst', 'dienst', 'bar'))
        aus = self.beleg(pp, 'out', '12,50', text='Plakate')                           # Zahlung über PayPal, Einzug folgt
        self.assertEqual((kasse.balance(self.s, kasse=pp), self.stand(pp)['balance']), (-1250, -1250))
        self.assertEqual(self.hinweise(), ['PayPal: Bestand im Minus (-12,50 €) – der Einzug vom Bankkonto über 12,50 € fehlt noch '
                                           '(im Kontoauszug auf Konto 1430 buchen)'])
        m = berichte.monthly(self.s, TODAY)
        self.assertFalse(any('Minus' in t or 'negativ' in t for t in m['tasks']))      # Hinweis, keine Aufgabe und kein Fehler
        self.assertIn('Hinweis – PayPal: Bestand im Minus', self.text(berichte.monthly_pdf(self.s, TODAY)))
        self.assertIn('Hinweis – PayPal: Bestand im Minus', self.text(self.paket()['00-Uebersicht.pdf']))
        self.s.book(aus, [{'amount': '-12,50', 'account': '4220', 'kv': 'LV'}], 'Test')   # verbuchen geht auch im Minus
        self.assertEqual(self.lines(aus), [('1430', 0, 1250), ('4220', 1250, 0)])
        einzug = self.auf_konto('-12,50', 3)                                           # Abbuchung im Kontoauszug → direkt PayPal
        self.assertEqual(self.lines(einzug), [('1420', 0, 1250), ('1430', 1250, 0)])
        self.assertEqual((kasse.balance(self.s, kasse=pp), self.hinweise()), (0, []))
        self.assertNotIn('Minus (', self.text(berichte.monthly_pdf(self.s, TODAY)))
        ein = self.beleg(pp, 'in', '30', offset=4, text='Teilnahmebeitrag')            # Gegenrichtung: Guthaben wird ausgezahlt
        self.s.book(ein, [{'amount': '30', 'account': '8590', 'kv': '5100'}], 'Test')
        zurueck = self.auf_konto('30', 5)
        self.assertEqual(self.lines(zurueck), [('1420', 3000, 0), ('1430', 0, 3000)])
        self.assertEqual(kasse.balance(self.s, kasse=pp), 0)
        book = kasse.book(self.s, YEAR, pp)
        self.assertEqual([(x['label'], x['amount'], x['balance']) for x in book['lines']],
                         [('Kassenbeleg', -1250, -1250), ('Einzug vom Bankkonto', 1250, 0), ('Kassenbeleg', 3000, 3000), ('Auszahlung aufs Bankkonto', -3000, 0)])
        csv = kasse.csv(self.s, YEAR, pp).decode('utf-8-sig')
        self.assertIn('Einzug vom Bankkonto', csv)
        self.assertIn('Auszahlung aufs Bankkonto', csv)
        pdf = self.text(berichte.cash_pdf(self.s, YEAR, kasse_id=pp))
        self.assertIn('direkt auf Konto 1430', pdf)
        self.assertNotIn('Geldtransit', pdf)
        t = kasse.transit(self.s)                                                      # nichts lief über 2380
        self.assertEqual((t['saldo'], t['kassen'], t['bank'], self.stand(pp)['transit']), (0, {}, [], []))
        self.assertEqual(kasse.balance(self.s), 5000)                                  # Bargeldkasse unberührt
        self.s.reverse_bank(einzug, 'Test')                                            # Storno des Einzugs: wieder im Minus, erlaubt
        self.assertEqual((kasse.balance(self.s, kasse=pp), len(self.hinweise())), (-1250, 1))
        self.assertIn('Storno aus Kontoauszug', [x['label'] for x in kasse.book(self.s, YEAR, pp)['lines']])
        self.s.book(einzug, [{'amount': '-12,50', 'account': '1430', 'kv': 'LV'}], 'Test')
        self.s.reverse_bank(ein, 'Test')                                               # Storno der Einnahme nach der Auszahlung: -30, erlaubt
        kasse.void(self.s, ein, 'doppelt', 'Test')
        self.assertEqual(kasse.balance(self.s, kasse=pp), -3000)
        self.assertEqual(self.s.db.execute('SELECT SUM(debit)-SUM(credit) FROM entries').fetchone()[0], 0)
        self.abgelehnt('Beginn des Kassenbuchs', kasse.create, self.s, {'kind': 'out', 'amount': '1', 'day': f'{YEAR - 1}-12-30', 'text': 'x', 'kasse': pp}, 'Test')
        self.abgelehnt('negativ', kasse.set_opening, self.s, START, '-5', 'Test', pp)  # ein Kontostand zu Beginn ist nie negativ

    def test_zahlungsdienst_ohne_geldtransit(self):
        pp = self.neu(amount='100')
        self.assertEqual(list(kasse.kinds(kasse.eine(self.s, pp))), ['in', 'out'])
        for kind in ('withdraw', 'deposit'):
            self.abgelehnt('keine Umbuchung über 2380', kasse.create, self.s, {'kind': kind, 'amount': '10', 'day': self.day(1), 'kasse': pp}, 'Test')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bank').fetchone()[0], 0)
        self.assertEqual(self.stand(pp)['transit'], [])
        # von Hand auf 2380 kontiert (anderer Durchlaufposten): dann – und nur dann – ist bei dieser Kasse etwas im Geldtransit offen
        aus = self.beleg(pp, 'out', '10')
        self.s.book(aus, [{'amount': '-10', 'account': '2380', 'kv': 'LV'}], 'Test')
        self.assertEqual([x['amount'] for x in self.stand(pp)['transit']], [-1000])

    def test_art_bargeld_bleibt_gesperrt(self):
        """Die Sperre „nie negativ“ gilt für jede Kasse der Art Bargeld unverändert – beim Erfassen, aus dem Kontoauszug, bei
        ungültig, Storno und Anfangsbestand."""
        hand = self.neu('Handkasse', '1431', 'HK', art='bar', amount='20')
        ohne = kasse.add(self.s, {'name': 'Zweite Handkasse', 'account': '1432', 'prefix': 'ZH', 'day': START, 'amount': '0'}, 'Test')
        self.assertEqual([k['art'] for k in kasse.liste(self.s)], ['bar', 'bar', 'bar'])   # ohne Angabe: die strenge Art
        self.abgelehnt('negativ', self.beleg, hand, 'out', '25')
        self.abgelehnt('negativ', self.beleg, ohne, 'out', '1')
        self.abgelehnt('negativ', self.auf_konto, '30', 2, '1431')                     # Kontoauszug: mehr zurück, als da ist
        ein = self.beleg(hand, 'in', '10', offset=2)
        self.beleg(hand, 'out', '25', offset=3)
        self.abgelehnt('negativ', kasse.void, self.s, ein, 'Fehler', 'Test')
        self.abgelehnt('negativ', kasse.set_opening, self.s, START, '10', 'Test', hand)
        zu = self.auf_konto('-30', 3, '1431')                                          # Bank → Handkasse: Bestand 35
        self.beleg(hand, 'out', '32', offset=4)
        self.abgelehnt('negativ', self.s.reverse_bank, zu, 'Test')                     # Storno: das Bargeld ist schon ausgegeben
        self.assertEqual((kasse.balance(self.s, kasse=hand), self.hinweise()), (300, []))
        self.assertEqual(list(kasse.kinds(kasse.eine(self.s, hand))), ['in', 'out', 'withdraw', 'deposit'])   # Bargeld: weiter über 2380
        for art in ('x', 'cash', ['dienst'], {'art': 'dienst'}, 1):
            self.abgelehnt('Art wählen', kasse.add, self.s, {'name': 'Dritte', 'account': '1433', 'prefix': 'DR', 'art': art, 'day': START, 'amount': '0'}, 'Test')
        self.assertEqual(len(kasse.liste(self.s)), 3)

    def test_umbenennen(self):
        pp = self.neu()
        self.neu('Stripe', '1431', 'ST')
        self.beleg(pp, 'out', '5')
        kasse.rename(self.s, pp, '  PayPal   JU ', 'Test')
        k = kasse.eine(self.s, pp)
        self.assertEqual((k['name'], k['account'], k['prefix'], k['art']), ('PayPal JU', '1430', 'PP', 'dienst'))   # nur der Name
        self.assertEqual(self.s.rows("SELECT name FROM accounts WHERE code='1430'"), [{'name': 'PayPal JU'}])       # Kontenrahmen zieht mit
        self.assertEqual(berichte.cash_name(k, YEAR), f'Kassenbuch-PayPal JU-1430-{YEAR}')
        self.assertEqual(kasse.create(self.s, {'kind': 'out', 'amount': '1', 'day': self.day(1), 'text': 'x', 'kasse': pp}, 'Test')['number'], f'PP-{YEAR}-0002')
        self.assertEqual(self.s.rows("SELECT action FROM audit WHERE action LIKE 'Kasse um%'"), [{'action': 'Kasse umbenannt'}])
        kasse.rename(self.s, pp, 'PAYPAL JU', 'Test')                                  # eigene Schreibweise ändern
        for name, text in (('Stripe', 'gibt es schon'), ('stripe', 'gibt es schon'), ('Bargeldkasse', 'gibt es schon'), ('Kasse', 'Bargeldkasse bzw.'),
                           ('Bank', 'Bargeldkasse bzw.'), ('', 'Namen'), ('A"B', 'Namen'), ('1234', 'Namen'), ('x' * 41, 'Namen'), (None, 'Namen')):
            self.abgelehnt(text, kasse.rename, self.s, pp, name, 'Test')
        self.assertEqual(kasse.eine(self.s, pp)['name'], 'PAYPAL JU')
        for call in (lambda: kasse.rename(self.s, 0, 'Barkasse', 'Test'), lambda: kasse.set_active(self.s, 0, False, 'Test'),
                     lambda: kasse.delete(self.s, 0, 'Test')):
            self.abgelehnt('Bargeldkasse ist fest', call)                              # Kennung 0: weder umbenennen noch stilllegen noch löschen
        self.abgelehnt('nicht gefunden', kasse.rename, self.s, 99, 'Neu', 'Test')
        self.assertEqual(kasse.liste(self.s)[0], {'id': 0, 'name': 'Bargeldkasse', 'account': '1400', 'prefix': 'K', 'art': 'bar', 'aktiv': 1, 'start': None})

    def test_stilllegen_und_aktivieren(self):
        pp = self.neu(amount='40')
        self.abgelehnt('nicht 0', kasse.set_active, self.s, pp, False, 'Test')         # Bestand 40
        aus = self.beleg(pp, 'out', '40')
        self.abgelehnt('offene oder geparkte', kasse.set_active, self.s, pp, False, 'Test')
        workflow.park(self.s, aus, 'Beleg fehlt', 'Test')
        self.abgelehnt('offene oder geparkte', kasse.set_active, self.s, pp, False, 'Test')
        self.s.book(aus, [{'amount': '-40', 'account': '4220', 'kv': 'LV'}], 'Test')
        hand = self.neu('Handkasse', '1431', 'HK', art='bar')
        kasse.create(self.s, {'kind': 'withdraw', 'amount': '10', 'day': self.day(1), 'kasse': hand}, 'Test')
        kasse.create(self.s, {'kind': 'deposit', 'amount': '10', 'day': self.day(2), 'kasse': hand}, 'Test')
        self.abgelehnt('Geldtransit 2380', kasse.set_active, self.s, hand, False, 'Test')   # Bestand 0, aber Belege ohne Kontoumsatz
        kasse.set_active(self.s, pp, False, 'Test')
        self.assertEqual((kasse.eine(self.s, pp)['aktiv'], self.stand(pp)['aktiv'], self.stand(pp)['balance']), (0, 0, 0))
        self.abgelehnt('stillgelegt', self.beleg, pp, 'in', '5')
        self.abgelehnt('stillgelegt', kasse.set_opening, self.s, START, '50', 'Test', pp)
        self.abgelehnt('stillgelegt', self.auf_konto, '-5', 3)                         # auch nicht aus dem Kontoauszug
        self.abgelehnt('stillgelegt', self.s.reverse_bank, aus, 'Test')                # und kein Storno: der Bestand bliebe nicht 0
        self.assertEqual(kasse.balance(self.s, kasse=pp), 0)
        kasse.rename(self.s, pp, 'PayPal alt', 'Test')                                 # umbenennen geht auch stillgelegt
        kasse.set_active(self.s, pp, True, 'Test')
        self.assertEqual(kasse.eine(self.s, pp)['aktiv'], 1)
        self.beleg(pp, 'in', '5', offset=3)
        self.assertEqual([r['action'] for r in self.s.rows("SELECT action FROM audit WHERE action LIKE 'Kasse %' AND action NOT LIKE 'Kasse eing%' ORDER BY id")],
                         ['Kasse stillgelegt', 'Kasse umbenannt', 'Kasse wieder aktiviert'])

    def test_loeschen_nur_ohne_beleg_und_buchung(self):
        leer = self.neu()
        kasse.delete(self.s, leer, 'Test')
        self.assertEqual(([k['name'] for k in kasse.liste(self.s)], self.s.rows("SELECT code FROM accounts WHERE code='1430'")), (['Bargeldkasse'], []))
        self.assertEqual(self.s.rows("SELECT action FROM audit WHERE action='Kasse gelöscht'"), [{'action': 'Kasse gelöscht'}])
        neu = self.neu()                                                               # Konto und Kürzel sind wieder frei, die Kennung nicht
        self.assertGreater(neu, leer)
        mit_anfang = self.neu('Stripe', '1431', 'ST', amount='10')                     # Anfangsbestand ist eine Buchung
        self.abgelehnt('stilllegen', kasse.delete, self.s, mit_anfang, 'Test')
        beleg = self.beleg(neu, 'in', '5')
        kasse.void(self.s, beleg, 'Tippfehler', 'Test')                                # auch ein ungültiger Beleg bleibt sichtbar
        self.abgelehnt('stilllegen', kasse.delete, self.s, neu, 'Test')
        nur_konto = self.neu('Klarna', '1432', 'KL')
        self.auf_konto('-5', 2, '1432')
        self.abgelehnt('stilllegen', kasse.delete, self.s, nur_konto, 'Test')          # Buchung aus dem Kontoauszug
        self.assertEqual([k['name'] for k in kasse.liste(self.s)], ['Bargeldkasse', 'PayPal', 'Stripe', 'Klarna'])
        self.abgelehnt('nicht gefunden', kasse.delete, self.s, leer, 'Test')

    def test_berichte_und_pruefpaket_mit_stillgelegter_kasse(self):
        """Stillgelegt: nicht mehr in der Belegerfassung, aber in Kassenbuch, Monatsbericht und Prüfpaket der Jahre mit Bewegungen."""
        kasse.set_opening(self.s, START, '50', 'Test')
        pp = self.neu()
        ein = self.beleg(pp, 'in', '10')
        self.s.book(ein, [{'amount': '10', 'account': '8590', 'kv': '5100'}], 'Test')
        self.auf_konto('10', 2)
        alt = self.neu('Stripe', '1431', 'ST', day=f'{YEAR - 1}-06-01')                # Bewegungen nur im Vorjahr
        for kind, tag in (('in', f'{YEAR - 1}-06-02'), ('out', f'{YEAR - 1}-06-03')):
            b = kasse.create(self.s, {'kind': kind, 'amount': '7', 'day': tag, 'text': 'Vorjahr', 'kasse': alt}, 'Test')['id']
            self.s.book(b, [{'amount': '7' if kind == 'in' else '-7', 'account': '8590' if kind == 'in' else '4220', 'kv': 'LV'}], 'Test')
        kasse.set_active(self.s, pp, False, 'Test')
        kasse.set_active(self.s, alt, False, 'Test')
        self.assertEqual((kasse.bewegt(self.s, pp, YEAR), kasse.bewegt(self.s, alt, YEAR), kasse.bewegt(self.s, alt, YEAR - 1)), (True, False, True))
        self.assertEqual([(k['name'], k['aktiv']) for k in kasse.summary(self.s)['kassen']], [('Bargeldkasse', 1), ('PayPal', 0), ('Stripe', 0)])
        self.assertEqual([k['name'] for k in berichte.monthly(self.s, TODAY)['cash']['kassen']], ['Bargeldkasse', 'PayPal'])
        self.assertTrue(berichte.monthly_pdf(self.s, TODAY).startswith(b'%PDF'))
        book = kasse.book(self.s, YEAR, pp)                                            # Kassenbuch bleibt lesbar
        self.assertEqual(([x['label'] for x in book['lines']], book['end']), (['Kassenbeleg', 'Auszahlung aufs Bankkonto'], 0))
        self.assertIn('Die Kasse ist stillgelegt.', self.text(berichte.cash_pdf(self.s, YEAR, kasse_id=pp)))
        jetzt, vorjahr = self.paket(), self.paket(YEAR - 1)
        self.assertEqual(sorted(n for n in jetzt if n.startswith('Kassenbuch')),
                         [f'Kassenbuch-{YEAR}.csv', f'Kassenbuch-{YEAR}.pdf', f'Kassenbuch-PayPal-1430-{YEAR}.csv', f'Kassenbuch-PayPal-1430-{YEAR}.pdf'])
        self.assertEqual(sorted(n for n in vorjahr if n.startswith('Kassenbuch')),
                         [f'Kassenbuch-Stripe-1431-{YEAR - 1}.csv', f'Kassenbuch-Stripe-1431-{YEAR - 1}.pdf'])
        self.assertIn(f'ST-{YEAR - 1}-0002', vorjahr[f'Kassenbuch-Stripe-1431-{YEAR - 1}.csv'].decode('utf-8-sig'))

    def test_rechte(self):
        for path in ('/api/kasse-umbenennen', '/api/kasse-aktiv', '/api/kasse-loeschen'):
            self.assertFalse(rollen.allowed({'buchhaltung': 'lesen', 'verband': 'schreiben', 'mitglieder': 'schreiben', 'verwaltung': 'lesen'},
                                            rollen.required('POST', path)), path)
            self.assertTrue(rollen.allowed({'buchhaltung': 'schreiben'}, rollen.required('POST', path)), path)

    def test_tabelle_aus_frueherem_stand_wird_ergaenzt(self):
        """Tabelle `kassen` ohne Art und Stand (früherer Stand von 0.60): beide Spalten kommen dazu, die Kasse bleibt Art Bargeld und aktiv."""
        pp = self.neu(art='bar', amount='5')
        self.s.db.execute('ALTER TABLE kassen DROP COLUMN art')
        self.s.db.execute('ALTER TABLE kassen DROP COLUMN aktiv')
        self.s.db.commit()
        self.s.db.close()
        self.s = Store(Path(self.tmp.name) / 't.db')
        k = kasse.eine(self.s, pp)
        self.assertEqual((k['art'], k['aktiv'], kasse.balance(self.s, kasse=pp)), ('bar', 1, 500))

