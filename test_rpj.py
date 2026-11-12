"""RPJ-Schattenhaushalt (rpj.py): Projekte, Bescheid, Mittelabrufe, Pflicht-Zuordnung beim Verbuchen – nur erfundene Testdaten."""
import tempfile
import unittest
import zipfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import berichte
import kasse
import rpj
import verbindlichkeiten as v
from core import Store

YEAR = date.today().year
TODAY = date.today().isoformat()


def day(offset):
    """Tag im laufenden Jahr, nie in der Zukunft."""
    return min((date(YEAR, 1, 1) + timedelta(days=offset)).isoformat(), TODAY)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, amount, when=None, ref='RPJ Test', name='Muster Tagungshaus'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{when or day(10)};{amount};{name};{ref};r{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'r{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def project(self, no=1, year=YEAR, **plan):
        return rpj.save_project(self.s, {'year': year, 'no': no, 'kind': 'Podiumsdiskussion', 'name': f'Testprojekt {no}', 'quarter': 'Q2',
                                         'participants': '30', 'plan': plan or {'a': '900', 'c': '400', 'e': '50'}}, 'Test')

    def book(self, bid, account, amount, target=None, category=None, title=None, kv='LV', **extra):
        part = {'amount': amount, 'account': account, 'kv': kv, **({'title': title} if title else {}), **extra}
        if target:
            part['rpj'] = {'target': target, 'category': category}
        self.s.book(bid, [part], 'Test', confirm_no_bill=True)

    def links(self):
        return self.s.rows('SELECT entry_id,project_id,category,request_id,amount FROM rpj_links ORDER BY id')


class RulesTests(Base):
    def test_kind_of(self):
        self.assertEqual([rpj.kind_of(a, t) for a, t in (('4380', 'H45'), ('4470', 'H45'), ('4380', 'H44'), ('8500', 'H19'), ('1592', 'H18'),
                                                          ('8500', 'H17'), ('1592', 'H19'))],
                         ['cost', 'cost', 'cost', 'fee', 'grant', None, 'fee'])

    def test_projects_plan_and_delete(self):
        pid = self.project(1, a='900', c='400', e='50', i='0')
        with self.assertRaises(ValueError) as cm:
            self.project(1)
        self.assertIn('gibt es', str(cm.exception))
        view = rpj.year_view(self.s, YEAR)
        p = view['projects'][0]
        self.assertEqual((p['cost_plan'], p['fee_plan'], p['rpj_plan'], p['plan']), (135000, 0, 135000, {'a': 90000, 'c': 40000, 'e': 5000}))
        trip = rpj.save_project(self.s, {'year': YEAR, 'no': 5, 'name': 'Bildungsreise', 'quarter': '', 'plan': {'b': '1.200', 'e': '25', 'h': '2400', 'i': '1125'}}, 'Test')
        p5 = [x for x in rpj.year_view(self.s, YEAR)['projects'] if x['id'] == trip][0]
        self.assertEqual((p5['cost_plan'], p5['fee_plan'], p5['rpj_plan'], p5['quarter']), (362500, 112500, 250000, None))
        self.assertEqual(rpj.year_view(self.s, YEAR)['cards']['applied'], 385000)
        rpj.save_project(self.s, {'id': pid, 'year': YEAR, 'no': 2, 'name': 'Umbenannt', 'status': 'cancelled', 'plan': {'a': '100'}}, 'Test')
        self.assertEqual(self.s.db.execute('SELECT no,name,status FROM rpj_projects WHERE id=?', (pid,)).fetchone()[:], (2, 'Umbenannt', 'cancelled'))
        for bad in ({'no': '', 'name': 'x'}, {'no': '3', 'name': ''}, {'no': '3', 'name': 'x', 'quarter': 'Q5'}, {'no': '3', 'name': 'x', 'plan': {'a': '-5'}},
                    {'no': '3', 'name': 'x', 'status': 'weg'}):
            with self.assertRaises(ValueError, msg=bad):
                rpj.save_project(self.s, {'year': YEAR, **bad}, 'Test')
        rpj.delete_project(self.s, pid, 'Test')
        self.assertEqual(len(rpj.year_view(self.s, YEAR)['projects']), 1)

    def test_notice_and_requests(self):
        with self.assertRaises(ValueError) as cm:
            rpj.save_request(self.s, {'year': YEAR, 'quarter': 'Q1', 'amount': '3000'}, 'Test')
        self.assertIn('Bescheid', str(cm.exception))
        rpj.save_year(self.s, {'year': YEAR, 'contingent': '10.000', 'notice_on': f'15.01.{YEAR}', 'reference': 'RPJ-Test-1', 'applied_on': f'20.12.{YEAR - 1}'}, 'Test')
        r1 = rpj.save_request(self.s, {'year': YEAR, 'quarter': '1', 'amount': '6000', 'requested_on': day(20)}, 'Test')
        with self.assertRaises(ValueError) as cm:
            rpj.save_request(self.s, {'year': YEAR, 'quarter': '2', 'amount': '4000,01'}, 'Test')
        self.assertIn('Kontingent', str(cm.exception))
        rpj.save_request(self.s, {'year': YEAR, 'quarter': '2', 'amount': '4000'}, 'Test')
        with self.assertRaises(ValueError):
            rpj.save_year(self.s, {'year': YEAR, 'contingent': '9.999'}, 'Test')    # schon 10.000 abgerufen
        cards = rpj.year_view(self.s, YEAR)['cards']
        self.assertEqual((cards['contingent'], cards['requested'], cards['available'], cards['received']), (1000000, 1000000, 0, 0))
        rpj.delete_request(self.s, r1, 'Test')
        self.assertEqual(rpj.year_view(self.s, YEAR)['cards']['requested'], 400000)


class BookingTests(Base):
    def setUp(self):
        super().setUp()
        self.p1 = self.project(1)
        rpj.save_year(self.s, {'year': YEAR, 'contingent': '10000'}, 'Test')
        self.r1 = rpj.save_request(self.s, {'year': YEAR, 'quarter': '1', 'amount': '3000', 'requested_on': day(5)}, 'Test')

    def test_cost_requires_project_and_category(self):
        bid = self.bank('-500')
        for target, category in ((None, None), (f'p:{self.p1}', ''), (f'r:{self.r1}', None), ('p:999', 'a')):
            with self.assertRaises(ValueError, msg=(target, category)):
                self.book(bid, '4380', '-500', target, category)
        self.assertEqual(self.s.db.execute('SELECT state FROM bank WHERE id=?', (bid,)).fetchone()[0], 'open')   # nichts gebucht
        self.book(bid, '4380', '-500', f'p:{self.p1}', 'a')
        self.assertEqual([(l['project_id'], l['category'], l['amount']) for l in self.links()], [(self.p1, 'a', -50000)])
        p = rpj.year_view(self.s, YEAR)['projects'][0]
        self.assertEqual((p['ist'], p['cost_ist'], p['rpj_ist']), ({'a': 50000}, 50000, 50000))
        recon = {r['kind']: r for r in rpj.year_view(self.s, YEAR)['reconciliation']}
        self.assertEqual((recon['cost']['household'], recon['cost']['linked'], recon['cost']['open']), (50000, 50000, 0))

    def test_split_parts_and_storno(self):
        p2 = self.project(2)
        bid = self.bank('-300')
        self.s.book(bid, [{'amount': '-200', 'account': '4380', 'kv': 'LV', 'rpj': {'target': f'p:{self.p1}', 'category': 'c'}},
                          {'amount': '-100', 'account': '4380', 'kv': 'LV', 'rpj': {'target': f'p:{p2}', 'category': 'e'}}], 'Test', confirm_no_bill=True)
        view = rpj.year_view(self.s, YEAR)
        self.assertEqual([x['cost_ist'] for x in view['projects']], [20000, 10000])
        self.s.reverse_bank(bid, 'Test')
        view = rpj.year_view(self.s, YEAR)
        self.assertEqual([x['cost_ist'] for x in view['projects']], [0, 0])     # storniert zählt nicht
        self.assertEqual(rpj.open_entries(self.s), [])
        self.book(bid, '4380', '-300', f'p:{p2}', 'h')
        self.assertEqual([x['cost_ist'] for x in rpj.year_view(self.s, YEAR)['projects']], [0, 30000])

    def test_grant_income_to_request_and_fee(self):
        bid = self.bank('2000', ref='Zuwendung RPJ Q1')
        with self.assertRaises(ValueError) as cm:
            self.book(bid, '1592', '2000')
        self.assertIn('Mittelabruf', str(cm.exception))
        self.book(bid, '1592', '2000', f'r:{self.r1}')
        over = self.bank('1000,01', ref='Zuwendung RPJ Q1 Rest')
        with self.assertRaises(ValueError) as cm:
            self.book(over, '1592', '1000,01', f'r:{self.r1}')
        self.assertIn('übersteigen', str(cm.exception))
        self.book(over, '1592', '1000,01', f'p:{self.p1}')            # Teilnahmebeitrag auf 1592 → Projekt, Kostenart i
        fee = self.bank('25', ref='Teilnahmebeitrag Reise')
        self.book(fee, '8500', '25', f'p:{self.p1}', 'a', title='H19')  # Titel H19: immer Kostenart i
        view = rpj.year_view(self.s, YEAR)
        self.assertEqual((view['requests'][0]['received'], view['requests'][0]['open']), (200000, 100000))
        p = view['projects'][0]
        self.assertEqual((p['fee_ist'], p['rpj_ist'], p['ist']), (102501, -102501, {'i': 102501}))
        self.assertEqual((view['cards']['received'], view['cards']['used'], view['cards']['balance']), (200000, -102501, 302501))
        back = self.bank('-150', ref='Rueckzahlung RPJ')
        self.book(back, '1592', '-150', f'r:{self.r1}')                 # Rückzahlung nicht verwendeter Mittel: eigener Wert, Abruf bleibt eingegangen
        view = rpj.year_view(self.s, YEAR)
        self.assertEqual((view['requests'][0]['received'], view['requests'][0]['refunded'], view['requests'][0]['open']), (200000, 15000, 100000))
        self.assertEqual((view['cards']['refunded'], view['cards']['balance']), (15000, 200000 - 15000 + 102501))
        r2 = rpj.save_request(self.s, {'year': YEAR, 'quarter': '2', 'amount': '500'}, 'Test')
        too_much = self.bank('-10', ref='Rueckzahlung ohne Eingang')
        with self.assertRaises(ValueError) as cm:
            self.book(too_much, '1592', '-10', f'r:{r2}')                   # Rückzahlung höchstens bis zu den Eingängen
        self.assertIn('Rückzahlungen', str(cm.exception))
        self.s.reverse_bank(back, 'Test')
        with self.assertRaises(ValueError):
            self.book(back, '4220', '-150', title='H18')                    # Titel passt nicht zum Konto
        with self.assertRaises(ValueError):
            rpj.save_request(self.s, {'id': self.r1, 'year': YEAR, 'quarter': '1', 'amount': '1999'}, 'Test')   # unter Eingang
        with self.assertRaises(ValueError):
            rpj.delete_request(self.s, self.r1, 'Test')
        self.book(back, '1592', '-150', f'r:{self.r1}')
        self.assertEqual(rpj.year_view(self.s, YEAR)['issues'], [])
        self.s.reverse_bank(bid, 'Test')                                      # Eingang storniert, Rückzahlung bleibt: Hinweis statt stiller Zahl
        self.assertTrue(any('Rückzahlungen' in x for x in rpj.year_view(self.s, YEAR)['issues']))

    def test_year_distance(self):
        old = self.project(9, year=YEAR - 1)
        far = self.project(8, year=YEAR - 2)
        bid = self.bank('-80')
        with self.assertRaises(ValueError) as cm:
            self.book(bid, '4380', '-80', f'p:{far}', 'a')
        self.assertIn('Haushaltsjahr', str(cm.exception))
        self.book(bid, '4380', '-80', f'p:{old}', 'a')                 # Rechnung im Folgejahr: erlaubt, Abstimmung zeigt es gesondert
        recon = {r['kind']: r for r in rpj.year_view(self.s, YEAR)['reconciliation']}['cost']
        self.assertEqual((recon['household'], recon['linked'], recon['other_years']), (8000, 0, 8000))
        prev = [x for x in rpj.year_view(self.s, YEAR - 1)['projects'] if x['id'] == old][0]
        self.assertEqual((prev['cost_ist'], prev['other_years']), (8000, 1))
        with self.assertRaises(ValueError):
            rpj.save_project(self.s, {'id': old, 'year': YEAR - 3, 'no': 9, 'name': 'x'}, 'Test')   # Buchung läge zu weit weg
        with self.assertRaises(ValueError):
            rpj.delete_project(self.s, old, 'Test')

    def test_cash_booking(self):
        kasse.set_opening(self.s, f'{YEAR}-01-01', '100', 'Test')
        c = kasse.create(self.s, {'kind': 'out', 'amount': '12,50', 'day': day(3), 'text': 'Getränke Podium'}, 'Test')
        with self.assertRaises(ValueError):
            self.book(c['id'], '4380', '-12,50')
        self.book(c['id'], '4380', '-12,50', f'p:{self.p1}', 'c')
        x = rpj.project_view(self.s, self.p1)
        self.assertEqual([(b['origin'], b['ref'], b['cost'], b['category']) for b in x['bookings']], [('Kasse', c['number'], 1250, 'c')])

    def test_bill_line_and_writeoff(self):
        data = {'creditor': 'Muster Tagungshaus', 'iban': 'DE89370400440532013000', 'ext_number': 'RE-1', 'issued': day(30), 'kv': 'LV',
                'lines': [{'description': 'Raummiete', 'amount': '900', 'account': '4380'}]}
        with self.assertRaises(ValueError) as cm:
            v.create(self.s, data, 'Test')
        self.assertIn('Position 1', str(cm.exception))
        data['lines'][0].update(rpj_target=f'p:{self.p1}', rpj_category='a')
        bill_id = v.create(self.s, data, 'Test')[0]
        self.assertEqual(rpj.project_view(self.s, self.p1)['project']['cost_ist'], 90000)
        bid = self.bank('-850', ref='RE-1', when=day(40))
        self.s.book(bid, [{'amount': '-850', 'bill_id': bill_id}], 'Test')
        v.write_off(self.s, bill_id, 'Skonto', 'Test')
        x = rpj.project_view(self.s, self.p1)
        self.assertEqual((x['project']['cost_ist'], [b['origin'] for b in x['bookings']]), (85000, ['Eingangsrechnung', 'Ausbuchung Eingangsrechnung']))
        self.assertEqual(rpj.open_entries(self.s), [])   # Bankzahlung der Rechnung ist kein RPJ-Posten (Verbindlichkeit)

    def test_assign_legacy_entries(self):
        bid = self.s.db.execute("INSERT INTO bank(booked,amount,name,reference,fingerprint,state,source) VALUES(?,?,?,?,?,?,?)",
                                (day(12), -40000, 'Hotel Muster', 'Altbuchung', 'fp-alt', 'booked', 'Master · Umsatz 1')).lastrowid
        self.s.journal(day(12), 'Altbuchung Excel', f'bank:{bid}', [{'account': '1420', 'kv': 'LV', 'debit': 0, 'credit': 40000},
                                                                     {'account': '4380', 'kv': 'LV', 'debit': 40000, 'credit': 0, 'title': 'H45'}])
        self.s.db.commit()
        items = rpj.open_entries(self.s)
        self.assertEqual([(x['amount'], x['open'], x['origin'], x['kind']) for x in items], [(-40000, -40000, 'Kontoauszug', 'cost')])
        eid = items[0]['id']
        with self.assertRaises(ValueError):
            rpj.assign(self.s, eid, [{'target': f'p:{self.p1}', 'category': 'a', 'amount': '-300'}, {'target': f'p:{self.p1}', 'category': 'c', 'amount': '-50'}], 'Test')
        rpj.assign(self.s, eid, [{'target': f'p:{self.p1}', 'category': 'a', 'amount': '-300'}, {'target': f'p:{self.p1}', 'category': 'c', 'amount': '-100'}], 'Test')
        self.assertEqual(rpj.open_entries(self.s), [])
        self.assertEqual(rpj.project_view(self.s, self.p1)['project']['ist'], {'a': 30000, 'c': 10000})
        rpj.assign(self.s, eid, [{'target': f'p:{self.p1}', 'category': 'h'}], 'Test')            # Änderung ersetzt
        self.assertEqual(rpj.project_view(self.s, self.p1)['project']['ist'], {'h': 40000})
        other = self.s.db.execute("SELECT id FROM entries WHERE account='1420'").fetchone()[0]
        with self.assertRaises(ValueError):
            rpj.assign(self.s, other, [{'target': f'p:{self.p1}', 'category': 'a'}], 'Test')
        self.assertIn('RPJ-Zuordnung geändert', [a['action'] for a in self.s.rows('SELECT action FROM audit')])


class ReviewTests(Base):
    def test_delete_after_storno_and_bad_input(self):
        pid = self.project(1)
        bid = self.bank('-50')
        self.book(bid, '4380', '-50', f'p:{pid}', 'a')
        with self.assertRaises(ValueError):
            rpj.delete_project(self.s, pid, 'Test')
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(rpj.year_view(self.s, YEAR)['projects'][0]['bookings'], 0)
        rpj.delete_project(self.s, pid, 'Test')                              # nur Zuordnungen stornierter Buchungen: löschbar
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM rpj_links').fetchone()[0], 0)
        for bad in ('x', ['a'], 5):
            with self.assertRaises(ValueError):
                rpj.save_project(self.s, {'year': YEAR, 'no': 2, 'name': 'x', 'plan': bad}, 'Test')
            with self.assertRaises(ValueError):
                rpj.assign(self.s, 1, bad, 'Test')
        for bad in (['x'], '13', [1.5], 'p'):
            with self.assertRaises(ValueError):
                rpj.assign_many(self.s, bad, 'p:1', 'a', 'Test')

    def test_import_date_column_is_not_a_quarter(self):
        raw = f'Jahr;Nr.;Veranstaltung;Datum;Quartal;a) Raummiete\n{YEAR};1;Podium;3.10.{YEAR};;100\n{YEAR};2;Reise;;Q4 {YEAR};100\n{YEAR};3;Falsch;;3.10.{YEAR};100\n'
        prev = rpj.preview_import(self.s, raw.encode(), 'x.csv')
        self.assertEqual([r.get('values', {}).get('quarter') for r in prev['rows']], [4, 4, None])   # Quartal aus dem Datum (3.10. → Q4)
        self.assertEqual([r['action'] for r in prev['rows']], ['new', 'new', 'error'])

    def test_xlsx_formula_without_value(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Jahr', 'Nr.', 'Veranstaltung', 'Quartal', 'a) Raummiete', 'c) Verpflegungskosten'])
        ws.append([YEAR, 1, 'Podium', 'Q1', 900, '=E2/2'])                  # nie in Excel gespeichert: kein Wert für die Formel
        ws.append([YEAR, 2, 'Workshop', 2, 1200.5, 300])
        buf = BytesIO()
        wb.save(buf)
        prev = rpj.preview_import(self.s, buf.getvalue(), 'x.xlsx')
        self.assertEqual(prev['rows'][0]['action'], 'error')
        self.assertIn('Formel', prev['rows'][0]['error'])
        self.assertEqual((prev['rows'][1]['plan'], prev['rows'][1]['values']['quarter']), ({'a': 120050, 'c': 30000}, 2))
        ws['F2'] = 500                                                      # berechnete Datei mit leerem Formelergebnis (=WENN(…;"";…))
        ws.append([YEAR, 3, 'Leer', 'Q3', 100, '=IF(1=2,1,"")'])
        buf = BytesIO()
        wb.save(buf)
        import zipfile, re as _re
        src, out = zipfile.ZipFile(BytesIO(buf.getvalue())), BytesIO()
        with zipfile.ZipFile(out, 'w') as z:                                # gespeicherter Wert wie von Excel/LibreOffice: t="str", leeres <v>
            for item in src.infolist():
                data = src.read(item)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    data = _re.sub(rb'<c r="F4"([^>]*)><f>(.*?)</f>(<v\s*/>|<v>.*?</v>)?</c>', rb'<c r="F4" t="str"><f>\2</f><v></v></c>', data)
                z.writestr(item, data)
        prev = rpj.preview_import(self.s, out.getvalue(), 'x.xlsx')
        self.assertEqual([r['action'] for r in prev['rows']], ['new', 'new', 'new'])

    def test_fiscal_year_of_rebooked_rows(self):
        bid = self.bank('-20', when=day(3))
        self.s.book(bid, [{'amount': '-20', 'account': '4210', 'kv': 'LV'}], 'Test', confirm_no_bill=True)
        self.s.db.execute('UPDATE journals SET year=? WHERE source=?', (YEAR - 1, f'bank:{bid}'))   # wie Excel-Blatt des Vorjahres
        self.s.db.commit()
        self.s.reverse_bank(bid, 'Test')
        row = [b for b in self.s.snapshot(YEAR)['bank'] if b['id'] == bid][0]
        self.assertEqual(row['fy'], YEAR - 1)


class BulkTests(Base):
    def test_assign_many(self):
        pid = self.project(1)
        rows = []
        for n in range(3):
            bid = self.s.db.execute("INSERT INTO bank(booked,amount,name,reference,fingerprint,state,source) VALUES(?,?,?,?,?,?,?)",
                                    (day(20 + n), 7500, f'Teilnehmer {n}', 'Teilnahmebeitrag Reise', f'fp{n}', 'booked', 'Master · Umsatz 1')).lastrowid
            self.s.journal(day(20 + n), 'TB', f'bank:{bid}', [{'account': '1420', 'kv': 'LV', 'debit': 7500, 'credit': 0},
                                                             {'account': '1592', 'kv': 'LV', 'debit': 0, 'credit': 7500, 'title': 'H18'}])
        self.s.db.commit()
        ids = [x['id'] for x in rpj.open_entries(self.s)]
        with self.assertRaises(ValueError):
            rpj.assign_many(self.s, ids, 'p:999', '', 'Test')
        self.assertEqual(rpj.assign_many(self.s, ids[:2], f'p:{pid}', '', 'Test'), 2)
        self.assertEqual(rpj.project_view(self.s, pid)['project']['fee_ist'], 15000)
        with self.assertRaises(ValueError) as cm:
            rpj.assign_many(self.s, ids, f'p:{pid}', '', 'Test')                 # zwei schon zugeordnet: nichts ändern
        self.assertIn('schon zugeordnet', str(cm.exception))
        self.assertEqual(len(rpj.open_entries(self.s)), 1)


class ImportTests(Base):
    CSV = ('Jahr;Nr.;Maßnahme;Veranstaltung;Quartal;Teilnehmer;Antrag vom;a) Raummiete;b) An- und Abreisekosten;c) Verpflegungskosten;'
           'd) Referentenkosten;e) Marketing / Bewerbungskosten;f) Reisekosten;g) Eintritte;h) sonstige Veranstaltungskosten;'
           'i) Teilnahmebeiträge;j) Technische Ausstattung;k) Geschäftsführungskosten\n'
           f'{YEAR};1;Podiumsdiskussion;Erfundenes Thema A;Q1;30-35;20.12.{YEAR - 1};900;;400;;50;;;;;;\n'
           f'{YEAR};2;Bildungsreise;Erfundene Reise B;Q3;20;20.12.{YEAR - 1};;5.010,96;;;50;;;4.500;3.000;;\n'
           f'{YEAR};3;Personalkosten;Jugendbildungsreferent;;;;;;;;;;;;;;2.250\n')

    def test_preview_and_apply(self):
        prev = rpj.preview_import(self.s, self.CSV.encode('utf-8'), 'rpj.csv')
        self.assertEqual((prev['counts'], prev['years']), ({'new': 3, 'update': 0, 'same': 0, 'error': 0}, [YEAR]))
        self.assertEqual(prev['totals'][YEAR]['rpj'], 135000 + 656096 + 225000)
        self.assertEqual(prev['rows'][1]['plan'], {'b': 501096, 'e': 5000, 'h': 450000, 'i': 300000})
        done = rpj.apply_import(self.s, prev, [r['row_no'] for r in prev['rows']], 'Test')
        self.assertEqual(done, {'new': 3, 'update': 0})
        self.assertEqual(rpj.year_info(self.s, YEAR)['applied_on'], f'{YEAR - 1}-12-20')
        view = rpj.year_view(self.s, YEAR)
        self.assertEqual((view['cards']['applied'], [p['quarter'] for p in view['projects']]), (1016096, [1, 3, None]))
        # zweiter Durchlauf: unverändert; geänderte Zeile wird erkannt, Status bleibt
        pid = view['projects'][0]['id']
        rpj.save_project(self.s, {'id': pid, 'year': YEAR, 'no': 1, 'kind': 'Podiumsdiskussion', 'name': 'Erfundenes Thema A', 'quarter': 'Q1',
                                  'participants': '30-35', 'status': 'done', 'attended': '28', 'plan': {'a': '900', 'c': '400', 'e': '50'}}, 'Test')
        again = rpj.preview_import(self.s, self.CSV.replace(';900;;400;', ';950;;400;').encode('cp1252'), 'rpj.csv')
        self.assertEqual(again['counts'], {'new': 0, 'update': 1, 'same': 2, 'error': 0})
        self.assertEqual(again['rows'][0]['changes'], ['plan'])
        rpj.apply_import(self.s, again, [again['rows'][0]['row_no']], 'Test')
        row = self.s.db.execute('SELECT status,attended FROM rpj_projects WHERE id=?', (pid,)).fetchone()
        self.assertEqual((row[0], row[1], rpj.project_view(self.s, pid)['project']['plan']['a']), ('done', 28, 95000))
        with self.assertRaises(ValueError):
            rpj.apply_import(self.s, again, [again['rows'][1]['row_no']], 'Test')   # unverändert: nichts zu tun

    def test_errors_and_template(self):
        bad = self.CSV + f'{YEAR};1;Doppelt;Noch einmal;Q2;;;1;;;;;;;;;;\n{YEAR};4;x;y;Q7;;;1;;;;;;;;;;\n'
        prev = rpj.preview_import(self.s, bad.encode(), 'rpj.csv')
        self.assertEqual(prev['counts']['error'], 2)
        with self.assertRaises(ValueError):
            rpj.preview_import(self.s, b'Name;Betrag\nx;1\n', 'x.csv')
        tpl = rpj.template_csv().decode('utf-8-sig')
        self.assertEqual(rpj.preview_import(self.s, tpl.encode(), 'vorlage.csv')['counts']['new'], 1)


class ReportTests(Base):
    def test_pdfs_csv_and_audit_package(self):
        pid = self.project(1)
        rpj.save_year(self.s, {'year': YEAR, 'contingent': '5000', 'notice_on': day(1)}, 'Test')
        rid = rpj.save_request(self.s, {'year': YEAR, 'quarter': '1', 'amount': '1350'}, 'Test')
        self.book(self.bank('1350', ref='Zuwendung'), '1592', '1350', f'r:{rid}')
        self.book(self.bank('-900', ref='Raummiete'), '4380', '-900', f'p:{pid}', 'a')
        legacy = self.bank('-10', ref='Altbuchung ohne Projekt')
        self.s.db.execute("UPDATE bank SET state='booked' WHERE id=?", (legacy,))
        self.s.journal(day(10), 'Alt', f'bank:{legacy}', [{'account': '1420', 'kv': 'LV', 'debit': 0, 'credit': 1000},
                                                          {'account': '4380', 'kv': 'LV', 'debit': 1000, 'credit': 0, 'title': 'H45'}])
        self.s.db.commit()
        for pdf in (berichte.rpj_pdf(self.s, YEAR), berichte.rpj_project_pdf(self.s, pid), berichte.monthly_pdf(self.s)):
            self.assertTrue(pdf.startswith(b'%PDF'))
        text = rpj.year_csv(self.s, YEAR).decode('utf-8-sig')
        self.assertIn('a) Raummiete;Ausgabe;900,00;900,00;0,00', text)
        bookings = rpj.bookings_csv(self.s, YEAR).decode('utf-8-sig')
        self.assertIn('Mittelabruf Q1/', bookings)
        self.assertIn('nicht zugeordnet', bookings)
        self.assertIn('Testprojekt 1;a) Raummiete;-900,00', rpj.bookings_csv(self.s, project_id=pid).decode('utf-8-sig'))
        files, notes = berichte.audit_files(self.s, YEAR, Path(self.tmp.name))
        self.assertTrue({f'RPJ-{YEAR}.pdf', f'RPJ-{YEAR}.csv', f'RPJ-Buchungen-{YEAR}.csv', f'RPJ-Projekte/RPJ-{YEAR}-Projekt-01.pdf'} <= set(files))
        self.assertTrue(any('RPJ-Buchungen' in n and 'keinem Projekt' in n for n in notes), notes)
        m = berichte.monthly(self.s)
        self.assertTrue(any('RPJ-Buchung' in t for t in m['tasks']))
        self.assertEqual(m['rpj']['received'], 135000)
        snap = self.s.snapshot(YEAR)['rpj']
        self.assertEqual((snap['open'], len(snap['projects']), snap['requests'][0]['received']), (1, 1, 135000))
        out = BytesIO()
        berichte.audit_package(self.s, YEAR, Path(self.tmp.name), out)
        self.assertIn(f'Kassenpruefung-{YEAR}/RPJ-{YEAR}.pdf', zipfile.ZipFile(out).namelist())


if __name__ == '__main__':
    unittest.main()
