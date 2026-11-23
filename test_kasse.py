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
    """Ab 0.57: weitere Kassen (PayPal) neben der Bargeldkasse – eigenes Konto, eigene Belegnummern, eigener Bestand."""

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
        self.s = Store(Path(self.tmp.name) / 't.db')                                 # Öffnen mit 0.57 ergänzt nur
        self.assertEqual((self.s.rows('SELECT id,booked,amount,bank_ref,ledger,state FROM bank'), self.s.rows('SELECT * FROM settings'),
                          self.s.rows('SELECT source FROM journals ORDER BY id')), before)
        self.assertEqual(self.s.rows('SELECT kasse FROM bank'), [{'kasse': 0}])
        self.assertEqual(kasse.liste(self.s), [{'id': 0, 'name': 'Bargeldkasse', 'account': '1400', 'prefix': 'K', 'start': START}])
        self.assertEqual((kasse.start(self.s), kasse.balance(self.s)), (START, 9000))
        self.assertEqual(self.entry('out', '5')['number'], f'K-{YEAR}-0002')
        self.s.book(old['id'], [{'amount': '-10', 'account': '4220', 'kv': 'LV'}], 'Test')
        self.assertEqual(self.lines(old['id']), [('1400', 0, 1000), ('4220', 1000, 0)])

    def test_einrichten_konto_kuerzel_anfangsbestand(self):
        self.assertEqual(kasse.free_account(self.s), '1430')                           # frei: nicht 1400, 1410 Portokasse, 1420 Bank
        pp = self.paypal()
        k = kasse.eine(self.s, pp)
        self.assertEqual(k, {'id': pp, 'name': 'PayPal', 'account': '1430', 'prefix': 'PP', 'start': START})
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
        for name in (f'Kassenbuch-{YEAR}.csv', f'Kassenbuch-{YEAR}.pdf', f'Kassenbuch-PayPal-{YEAR}.csv', f'Kassenbuch-PayPal-{YEAR}.pdf'):
            self.assertIn(name, files)
        bar, online = files[f'Kassenbuch-{YEAR}.csv'].decode('utf-8-sig'), files[f'Kassenbuch-PayPal-{YEAR}.csv'].decode('utf-8-sig')
        self.assertEqual((f'K-{YEAR}-0001' in bar, f'PP-{YEAR}' in bar, f'PP-{YEAR}-0002' in online, f'K-{YEAR}' in online), (True, False, True, False))
        self.assertFalse(any(f'PP-{YEAR}' in line for line in files[f'Kontoumsaetze-{YEAR}.csv'].decode('utf-8-sig').splitlines()))

    def test_rechte(self):
        for path in ('/api/kasse-neu', '/api/kasse-anfang', '/api/kasse-beleg', '/api/kasse-ungueltig'):
            self.assertFalse(rollen.allowed({'buchhaltung': 'lesen', 'verband': 'schreiben', 'mitglieder': 'schreiben'}, rollen.required('POST', path)), path)
            self.assertTrue(rollen.allowed({'buchhaltung': 'schreiben'}, rollen.required('POST', path)), path)
        self.paypal()
        self.assertEqual(rollen.SNAPSHOT['cash'], 'buchhaltung')                      # ohne Buchhaltung: gleiche Form, kein Inhalt
        self.assertEqual(rollen.blank(kasse.summary(self.s)), {'kassen': [], 'open': 0, 'next_account': '', 'transit': 0})


if __name__ == '__main__':
    unittest.main()
