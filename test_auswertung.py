"""Auswertung der Mitgliederdaten – nur erfundene Testdaten."""
import tempfile
import unittest
from pathlib import Path

import auswertung
import gliederung
from core import Store

BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg')
DAY = '2026-06-15'


class AuswertungTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'age_basis': 'jan1'}, 'Test')
        self.ov = gliederung.save_ov(self.s, {'kv': '5100', 'number': '11', 'name': 'Testort'}, 'Test')
        add = lambda number, first, kv, birth, joined, **x: self.s.member({**BASE, 'number': number, 'first_name': first, 'last_name': 'Test',
                                                                           'kv': kv, 'birth': birth, 'joined': joined, **x}, 'Test')
        self.a = add('T1', 'Alt', '5100', '1991-06-20', '2016-03-01', ov_id=self.ov, email='a@example.org', gender='M', clubs='JU,CDU')  # wird 35 am 20.06.
        self.b = add('T2', 'Jung', '5100', '2010-01-10', '2026-04-01', gender='W', clubs='JU')                                         # 16, neu 2026
        self.c = add('T3', 'Mitte', '5300', '2000-07-01', '2021-02-01', email='c@example.org', phone='040 1', guest='J')              # Jubiläum 5 Jahre
        self.d = add('T4', 'Weg', '5300', '1999-01-01', '2018-01-01', left_on='2026-03-31')                                           # ausgetreten 2026
        self.e = add('T5', 'Ü35', '5600', '1985-01-01', '2010-01-01', no_mail='J')                                                     # über 35
        self.f = add('T6', 'Später', '5600', '2001-01-01', '2026-09-01')                                                              # nach dem Stichtag

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_report(self):
        r = auswertung.report(self.s, DAY, today=DAY)
        c = r['cards']
        self.assertEqual((c['active'], c['guests'], c['joined_year'], c['left_year'], c['net_year']), (4, 1, 1, 1, 0))
        self.assertEqual(c['avg_age'], round((34 + 16 + 25 + 41) / 4, 1))
        self.assertEqual((c['with_email'], c['with_ov']), (0.5, 0.25))
        dev = {d['year']: d for d in r['development']}
        self.assertEqual((dev[2026]['joined'], dev[2026]['left'], dev[2026]['end'], dev[2025]['end'], dev[2017]['end']), (1, 1, 4, 4, 2))
        self.assertEqual(min(dev), 2017)                                                     # höchstens 10 Jahre
        kv = {k['code']: k for k in r['kv_rows']}
        self.assertEqual((kv['5100']['active'], kv['5300']['active'], kv['5300']['left_year'], kv['5100']['share']), (2, 1, 1, 0.5))
        self.assertNotIn('5800', kv)
        self.assertEqual(sum(o['active'] for o in r['ov_rows'] if o['label'] == '11 Testort'), 1)
        self.assertEqual(sum(o['active'] for o in r['ov_rows'] if o['label'] == 'ohne Ortsverband'), 3)   # je Kreisverband eine Zeile
        self.assertEqual({a['label']: a['count'] for a in r['ages'] if a['count']}, {'16–17': 1, '25–29': 1, '30–34': 1, '35 und älter': 1})
        self.assertEqual({g['label']: g['count'] for g in r['gender']}, {'männlich': 1, 'weiblich': 1, 'ohne Angabe': 2})
        self.assertEqual({g['label']: g['count'] for g in r['clubs']}, {'JU': 2, 'CDU': 1})
        lists = r['lists']
        self.assertEqual([(x['number'], x['reached']) for x in lists['age_limit']], [('T5', True), ('T1', False)])   # T1 wird am 20.06.2026 35
        self.assertEqual([(x['number'], x['years']) for x in lists['jubilees']], [('T1', 10), ('T3', 5)])
        self.assertEqual([x['number'] for x in lists['birthdays']], ['T1', 'T3'])         # 20.06. und 01.07. (innerhalb 30 Tagen)
        self.assertEqual([x['number'] for x in lists['new']], ['T2'])
        self.assertEqual({k: v['count'] for k, v in lists['gaps'].items()}, {'no_email': 2, 'no_ov': 3, 'no_phone': 3, 'no_zip': 0, 'no_mail': 1})

    def test_filters_and_errors(self):
        r = auswertung.report(self.s, DAY, kv='5100', today=DAY)
        self.assertEqual((r['cards']['active'], [k['code'] for k in r['kv_rows']]), (2, ['5100']))
        self.assertEqual(auswertung.report(self.s, DAY, kv='5100', ov=str(self.ov), today=DAY)['cards']['active'], 1)
        self.assertEqual(auswertung.report(self.s, DAY, kv='5100', ov='none', today=DAY)['cards']['active'], 1)
        self.assertEqual(auswertung.report(self.s, '2026-10-01', today='2026-10-01')['cards']['active'], 5)   # T6 nach Eintritt
        for kwargs in ({'kv': 'LV'}, {'kv': '9999'}, {'kv': '5300', 'ov': str(self.ov)}, {'ov': 'x'}, {'stichtag': '2026-13-01'}, {'stichtag': '1990-01-01'}):
            with self.assertRaises(ValueError, msg=kwargs):
                auswertung.report(self.s, **{'stichtag': DAY, **kwargs})

    def test_pdf_and_csv_without_names(self):
        pdf = auswertung.pdf(self.s, DAY)
        self.assertTrue(pdf.startswith(b'%PDF'))
        text = auswertung.csv(self.s, DAY).decode('utf-8-sig')
        self.assertNotRegex(text, r';\d+\.\d')                      # Dezimalkomma, nie Punkt (Excel läse ein Datum)
        ohne = [o for o in auswertung.report(self.s, DAY)['ov_rows'] if o['label'] == 'ohne Ortsverband']
        self.assertEqual(len(ohne), len({o['kv'] for o in ohne}))   # je Kreisverband eine Zeile
        self.assertIn('Altona', text)
        self.assertNotIn('T1', text)                                                         # keine Mitgliedsnummern oder Namen
        self.assertNotIn('Alt;', text)
        ov_line = next(line for line in text.splitlines() if 'Testort' in line)
        self.assertEqual(ov_line.split(';')[3:5], ['1', ''])                                   # 1 Mitglied: kein Durchschnittsalter
        small = auswertung.pdf(self.s, DAY, kv='5300')                                          # 1 Mitglied in der Auswahl
        self.assertTrue(small.startswith(b'%PDF'))
        kv_line = next(line for line in auswertung.csv(self.s, DAY, kv='5300').decode('utf-8-sig').splitlines() if line.startswith('Kreisverband;'))
        self.assertEqual(kv_line.split(';')[4], '')                                            # kein Alter, keine Beträge einer Person
        self.assertEqual(kv_line.split(';')[9:12], ['', '', ''])
        self.assertEqual(kv_line.split(';')[13:17], ['', '', '', ''])                          # auch kein Geschlecht, kein Frauenanteil einer Person
        self.assertNotIn('Altersgruppe', auswertung.csv(self.s, DAY, kv='5300').decode('utf-8-sig'))

    def test_new_figures(self):
        r = auswertung.report(self.s, DAY, today=DAY)
        c = r['cards']
        self.assertEqual((c['women'], c['men'], c['women_share'], c['no_birth']), (1, 1, None, 0))   # 1 : 1 – unter 3 Personen kein Anteil
        self.assertEqual({a['label']: (a['w'], a['m']) for a in r['ages'] if a['count']},
                         {'16–17': (1, 0), '25–29': (0, 0), '30–34': (0, 1), '35 und älter': (0, 0)})     # T3 und T5 ohne Angabe: nicht in der Pyramide
        self.assertEqual({a['label']: a['count'] for a in r['entry_ages'] if a['count']}, {'16–17': 1, '18–20': 1, '21–24': 1, '25–29': 1})
        self.assertEqual(r['months'], [0, 0, 0, 1, 0, 0])                                     # Januar bis Juni, Eintritt T2 im April
        self.assertEqual(r['joined_gender'], {'w': 1, 'm': 0, 'x': 0})
        self.assertEqual([(a['key'], a['count'], a['avg_age'], a['median_age']) for a in r['age_stats']],
                         [('w', 1, None, None), ('m', 1, None, None), ('x', 2, None, None)])   # unter 3 Personen kein Alter
        kv = {k['code']: k for k in r['kv_rows']}
        self.assertEqual([(kv[k]['w'], kv[k]['m'], kv[k]['x'], kv[k]['women_share'], kv[k]['net_year']) for k in ('5100', '5300', '5600')],
                         [(1, 1, 0, None, 1), (0, 0, 1, None, -1), (0, 0, 1, None, 0)])              # 5300/5600 nur „ohne Angabe“: kein Frauenanteil
        self.assertEqual((r['months_left'], sum(r['months_left'])), ([0, 0, 1, 0, 0, 0], c['left_year']))   # Austritt T4 am 31.03.
        self.assertEqual(c['joined_women_share'], None)
        ov = {(o['kv'], o['label']): o for o in r['ov_rows']}
        self.assertEqual([(o['w'], o['m'], o['x'], o['women_share'], o['band_n'], o['bands']) for o in (ov['5100', '11 Testort'], ov['5300', 'ohne Ortsverband'])],
                         [(0, 1, 0, None, [0, 0, 1], [0.0, 0.0, 1.0]), (0, 0, 1, None, [0, 0, 1], [0.0, 0.0, 1.0])])
        for k in r['kv_rows']:                                                                # Anteile ergeben 100 %
            self.assertEqual(k['w'] + k['m'] + k['x'], k['active'])
        self.assertEqual(sum(k['active'] for k in r['kv_rows']), c['active'])
        self.assertAlmostEqual(sum(k['share'] for k in r['kv_rows']), 1)
        for key in ('ages', 'entry_ages', 'tenure', 'gender', 'payment'):
            self.assertEqual(sum(x['count'] for x in r[key]), c['active'], key)
        self.assertEqual(sum(r['months']), c['joined_year'])
        self.assertEqual(sum(r['joined_gender'].values()), c['joined_year'])

    def test_empty_selection(self):
        r = auswertung.report(self.s, DAY, kv='5800', today=DAY)                              # Schüler Union ohne Mitglieder
        self.assertEqual((r['cards']['active'], r['cards']['women_share'], r['cards']['avg_age'], r['kv_rows']), (0, None, None, []))
        self.assertEqual({a['count'] for a in r['ages'] + r['entry_ages']}, {0})
        self.assertEqual((r['months'], r['months_left'], r['joined_gender']), ([0] * 6, [0] * 6, {'w': 0, 'm': 0, 'x': 0}))
        self.assertTrue(auswertung.pdf(self.s, DAY, kv='5800').startswith(b'%PDF'))
        self.assertIn('Monat 2026-06;;;;;0;0;', auswertung.csv(self.s, DAY, kv='5800').decode('utf-8-sig'))
        self.assertEqual(auswertung.report(self.s, '2026-01-01', today=DAY)['months'], [0])   # Stichtag im Januar: ein Monat

    def test_stichtag_vor_erstem_eintritt(self):
        """Stichtag vor dem ersten Eintritt der Auswahl (Prüfung #30): die Entwicklung zeigt das Jahr des Stichtags statt leer zu sein –
        die Oberfläche greift auf den letzten Eintrag zu."""
        r = auswertung.report(self.s, '2005-06-30', today=DAY)
        self.assertEqual(r['development'], [{'year': 2005, 'joined': 0, 'left': 0, 'end': 0, 'until': '2005-06-30'}])
        self.assertEqual((r['cards']['active'], r['months']), (0, [0] * 6))

    def test_redact_small_groups(self):
        import json
        r = auswertung.report(self.s, DAY, today=DAY)
        full = auswertung.redact(r)
        self.assertEqual((full['ages'], full['kv_rows'], full['joined_gender']), (r['ages'], r['kv_rows'], r['joined_gender']))
        red = auswertung.redact(r, amounts=False, names=False)
        self.assertEqual({a['label']: (a['count'], a['w'], a['m']) for a in red['ages'] if a['count']},
                         {'16–17': (1, None, 0), '25–29': (1, 0, 0), '30–34': (1, 0, None), '35 und älter': (1, 0, 0)})
        kv = {k['code']: k for k in red['kv_rows']}
        self.assertEqual((kv['5100']['w'], kv['5100']['m'], kv['5100']['x'], kv['5100']['women_share']), (None, None, 0, None))
        self.assertEqual((kv['5300']['x'], kv['5300']['women_share'], kv['5300']['soll']), (None, None, 0))
        self.assertEqual((red['joined_gender'], red['cards']['joined_women_share']), ({'w': None, 'm': 0, 'x': 0}, None))
        ov = {(o['kv'], o['label']): o for o in red['ov_rows']}                                # Ortsverbände mit 1 Person: nichts, was auf ihr beruht
        self.assertEqual([(o['active'], o['w'], o['m'], o['x'], o['avg_age'], o['women_share'], o['band_n'], o['bands']) for o in (ov['5100', '11 Testort'], ov['5300', 'ohne Ortsverband'])],
                         [(1, 0, None, 0, None, None, [0, 0, None], [0.0, 0.0, None]), (1, 0, 0, None, None, None, [0, 0, None], [0.0, 0.0, None])])
        text = json.dumps(red, ensure_ascii=False)
        for name in ('Test,', 'T1', 'Jung', 'Ü35', 'example.org'):                                    # keine Namen, Nummern, Kontaktdaten
            self.assertNotIn(name, text)
        lines = auswertung.csv(self.s, DAY).decode('utf-8-sig').splitlines()                    # PDF/CSV: Geschlecht unter 3 Personen nur „< 3“
        self.assertEqual(next(x for x in lines if x.startswith('Altersgruppe;16–17')).split(';')[13:16], ['< 3', '0', '0'])
        self.assertEqual(next(x for x in lines if x.startswith('Kreisverband;Altona')).split(';')[12:17], ['1', '< 3', '< 3', '0', ''])
        self.assertEqual(next(x for x in lines if x.startswith('Alter nach Geschlecht;weiblich')).split(';')[3:5], ['< 3', ''])
        self.assertEqual(len({len(x.split(';')) for x in lines}), 1)                           # jede Zeile gleich viele Spalten

    def test_vergleich_und_neue_kennzahlen(self):
        self.s.db.execute('UPDATE members SET geworben_von=? WHERE number=?', (self.a, 'T2'))
        r = auswertung.report(self.s, DAY, today=DAY)
        v, c = r['vergleich'], r['cards']
        self.assertEqual((v['stichtag'], v['active'], v['joined_year'], v['left_year']), ('2025-06-15', 4, 0, 0))   # T1, T3, T4, T5; T2 kam erst 2026
        self.assertEqual((v['avg_age'], v['women_share'], v['with_email'], v['with_ov'], v['with_mandate'], v['complete']),
                         (round((33 + 24 + 26 + 40) / 4, 1), None, 0.5, 0.25, 0, 0.25))
        self.assertEqual(set(v) - set(c), {'stichtag'})                                       # dieselben Kennzahlen wie am Stichtag
        self.assertEqual((c['complete'], c['avg_entry_age']), (0.25, round((24 + 16 + 20 + 25) / 4, 1)))   # vollständig ist nur ein Mitglied (Anschrift, E-Mail, Geburtsdatum, Ortsverband)
        self.assertEqual([(a['label'], a['w'], a['m'], a['x'], a['share']) for a in r['women_age']], [('unter 25', 1, 0, 0, None), ('ab 25', 0, 1, 2, None)])
        kv = {k['code']: k for k in r['kv_rows']}
        self.assertEqual([(kv[k]['with_email'], kv[k]['with_ov'], kv[k]['with_mandate'], kv[k]['complete']) for k in ('5100', '5300')],
                         [(0.5, 0.5, 0.0, 0.5), (1.0, 0.0, 0.0, 0.0)])   # vollständig braucht auch den Ortsverband – in 5300 hat ihn niemand
        self.assertEqual(r['recruited'], {'joined': 1, 'count': 1, 'share': 1.0})
        self.assertEqual(r['left_tenure'], {'total': 1, 'age_limit': 0, 'groups': [{'label': g[2], 'count': n} for g, n in zip(auswertung.LEFT_GROUPS, (0, 0, 0, 1))]})
        self.assertEqual([(a['year'], a['count']) for a in r['age_limit_ahead']], [(2026, 1), (2027, 0), (2028, 0), (2029, 0), (2030, 0), (2031, 0)])   # T1 am 20.06.2026
        cohort = {k['year']: k for k in r['cohorts']}
        self.assertEqual(sorted(cohort), list(range(2020, 2027)))
        self.assertEqual((cohort[2021], cohort[2026]), ({'year': 2021, 'joined': 1, 'kept': [None] * 4}, {'year': 2026, 'joined': 1, 'kept': []}))
        self.assertEqual(auswertung.report(self.s, '2024-02-29', today=DAY)['vergleich']['stichtag'], '2023-02-28')   # Schalttag
        empty = auswertung.report(self.s, DAY, kv='5800', today=DAY)                          # leere Auswahl: gleiche Form, keine Anteile
        self.assertEqual((empty['vergleich']['active'], empty['recruited'], empty['left_tenure']['total'], {a['count'] for a in empty['age_limit_ahead']},
                          {(k['joined'], tuple(k['kept'])) for k in empty['cohorts'] if k['year'] >= 2025}),
                         (0, {'joined': 0, 'count': 0, 'share': None}, 0, {0}, {(0, ())}))

    def test_redact_neue_schluessel(self):
        import json
        r = auswertung.report(self.s, DAY, today=DAY)
        red = auswertung.redact(r, amounts=False, names=False)
        self.assertEqual([(a['w'], a['m'], a['x'], a['share']) for a in red['women_age']], [(None, 0, 0, None), (0, None, None, None)])
        self.assertEqual(({g['label']: g['count'] for g in red['gender']}, {g['label']: g['count'] for g in red['clubs']}),
                         ({'männlich': None, 'weiblich': None, 'ohne Angabe': None}, {'JU': None, 'CDU': None}))
        for key in ('vergleich', 'cohorts'):                                                   # reine Zahlen: unverändert
            self.assertEqual(red[key], r[key], key)
        # Bindung: unter SMALL Personen kein Wert, sonst stünde „1 Mitglied wird 2026 35“ für eine Rolle ohne Mitgliederrecht da
        self.assertEqual(([g['count'] for g in red['left_tenure']['groups']], red['left_tenure']['age_limit'], red['left_tenure']['total']), ([0, 0, 0, None], 0, 1))
        self.assertEqual(([a['count'] for a in red['age_limit_ahead']], red['recruited']), ([None, 0, 0, 0, 0, 0], r['recruited']))
        self.assertEqual(auswertung.redact({**r, 'recruited': {'joined': 4, 'count': 2, 'share': 0.5}}, names=False)['recruited'], {'joined': 4, 'count': None, 'share': None})
        self.assertEqual(tuple(auswertung.redact(r)[k] for k in ('left_tenure', 'age_limit_ahead', 'recruited')), tuple(r[k] for k in ('left_tenure', 'age_limit_ahead', 'recruited')))
        self.assertEqual((auswertung.redact(r)['gender'], auswertung.redact(r)['women_age']), (r['gender'], r['women_age']))   # mit Mitgliederrecht alles
        self.assertNotRegex(json.dumps({k: red[k] for k in ('vergleich', 'cohorts', 'left_tenure', 'age_limit_ahead', 'recruited', 'women_age')}, ensure_ascii=False),
                            r'Test|T\d|example')


class BindungTests(unittest.TestCase):
    """Kohorten, Austritte nach Dauer, Altersgrenze und Werbung – reine Funktionen über erfundene Zeilen."""
    TAG = auswertung._day(DAY)

    @staticmethod
    def row(joined='', left='', birth='', **more):
        return {'joined': joined, 'left_on': left, 'birth': birth, 'geworben_von': None, 'geworben_text': '', **more}

    def test_kohorten(self):
        row = self.row
        pool = [row('2020-05-01', '2021-04-30'),            # geht am Tag vor dem ersten Jahrestag
                row('2020-05-01', '2021-05-01'),            # Austritt = letzter Tag: am ersten Jahrestag noch Mitglied
                row('2020-05-01', '2024-12-31'), row('2020-05-01'),
                row('2023-01-01'), row('2023-07-01'),       # unter 3 Eintritte: kein Anteil
                row('2024-12-31'), row('2024-12-31', '2025-12-30'), row('2024-12-31', '2027-01-01'),
                row('2025-01-01'), row('2025-02-01'), row('2025-03-01'),
                row('2026-06-15'), row('2026-06-16'),       # Eintritt am Stichtag zählt, danach nicht
                row(''), row('2019-12-31')]
        k = {c['year']: c for c in auswertung.cohorts(pool, self.TAG)}
        self.assertEqual(sorted(k), [2020, 2021, 2022, 2023, 2024, 2025, 2026])
        self.assertEqual((k[2020]['joined'], k[2020]['kept']), (4, [0.75, 0.5, 0.5, 0.5, 0.25]))
        self.assertEqual((k[2021]['joined'], k[2021]['kept'], k[2022]['kept']), (0, [None] * 4, [None] * 3))
        self.assertEqual((k[2023]['joined'], k[2023]['kept']), (2, [None, None]))
        self.assertEqual((k[2024]['joined'], k[2024]['kept']), (3, [0.6667]))                  # zweites Jahr erst ab 31.12.2026
        self.assertEqual((k[2025]['joined'], k[2025]['kept'], k[2026]['joined'], k[2026]['kept']), (3, [], 1, []))
        self.assertEqual(auswertung.cohorts(pool, auswertung._day('2026-12-31'))[4]['kept'], [0.6667, 0.6667])   # 2024 am 31.12.2026: zwei Jahre
        leap = [row('2020-02-29', '2021-02-28'), row('2020-02-29', '2021-03-01'), row('2020-02-29')]              # Jahrestag des 29.02. ist der 01.03.
        self.assertEqual(auswertung.cohorts(leap, self.TAG)[0]['kept'][:2], [0.6667, 0.3333])

    def test_austritte_nach_dauer(self):
        row = self.row
        left = [row('2026-01-01', '2026-03-31'), row('2025-03-31', '2026-03-30'),             # 0 Jahre: im 1. Jahr (einen Tag vor dem Jahrestag)
                row('2025-03-31', '2026-03-31'), row('2023-04-01', '2026-03-31'),             # 1 und 2 Jahre
                row('2023-03-31', '2026-03-31'),                                              # 3 Jahre
                row('2010-01-01', '2026-06-19', '1991-06-20'),                                # letzter Tag vor dem 35. Geburtstag: Altersgrenze
                row('2010-01-01', '2026-06-19', '1991-06-21'),                                # am Tag danach noch 34
                row('', '2026-02-01', '1980-01-01')]                                          # ohne Eintrittsdatum: nur in total
        out = auswertung.left_by_tenure(left)
        self.assertEqual((out['total'], [g['count'] for g in out['groups']], out['age_limit']), (8, [2, 2, 1, 2], 2))
        self.assertEqual(auswertung.left_by_tenure([]), {'total': 0, 'age_limit': 0, 'groups': [{'label': g[2], 'count': 0} for g in auswertung.LEFT_GROUPS]})

    def test_altersgrenze_vorschau(self):
        births = ('1991-06-15', '1991-06-16', '1991-12-31', '1992-02-29', '1996-12-31', '1997-01-01', '', '1980-01-01')
        out = auswertung.age_limit_ahead([self.row(birth=b) for b in births], self.TAG)
        self.assertEqual([(a['year'], a['count']) for a in out], [(2026, 2), (2027, 1), (2028, 0), (2029, 0), (2030, 0), (2031, 1)])   # 15.06. schon erreicht

    def test_geworben(self):
        row = self.row
        joined = [row(geworben_von=5), row(geworben_text=' Tante Erna '), row(geworben_text='  '), row()]
        self.assertEqual(auswertung.recruited(joined), {'joined': 4, 'count': 2, 'share': 0.5})
        self.assertEqual(auswertung.recruited([]), {'joined': 0, 'count': 0, 'share': None})

    def test_vollstaendig(self):
        """Dieselbe Regel wie `fehlt` in static/js/mitglieder-listen.js (Cockpit und Listen)."""
        full = {'birth': '2000-01-01', 'email': 'a@example.org', 'street': 'Testweg 1', 'zip': '20095', 'city': 'Hamburg', 'ov_id': 3}
        self.assertTrue(auswertung._complete(full))
        for key in full:
            self.assertFalse(auswertung._complete({**full, key: ''}), key)


class AltersgruppenTests(unittest.TestCase):
    """Grenzen der Altersgruppen: Geburtstag am Stichtag zählt schon zum neuen Alter."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'age_basis': 'jan1'}, 'Test')
        rows = (('2010-06-15', 'W'), ('2010-06-16', 'W'), ('2008-06-15', 'W'), ('2008-06-16', 'M'), ('2005-06-15', 'M'), ('2005-06-16', 'M'),
                ('2001-06-15', 'W'), ('2001-06-16', 'D'), ('1996-06-15', 'M'), ('1996-06-16', ''), ('1991-06-15', 'W'), ('1991-06-16', 'M'))
        for i, (birth, gender) in enumerate(rows):
            self.s.member({**BASE, 'number': f'G{i}', 'first_name': 'Grenze', 'last_name': 'Test', 'kv': '5100', 'birth': birth,
                           'joined': '2026-06-15' if i == 0 else '2025-06-15', 'gender': gender}, 'Test')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_boundaries_and_pyramid(self):
        r = auswertung.report(self.s, DAY, today=DAY)
        self.assertEqual([(a['label'], a['count'], a['w'], a['m']) for a in r['ages']],
                         [('unter 16', 1, 1, 0), ('16–17', 2, 1, 1), ('18–20', 2, 1, 1), ('21–24', 2, 0, 1), ('25–29', 2, 1, 0), ('30–34', 2, 0, 2),
                          ('35 und älter', 1, 1, 0)])                                          # 16., 18., 21., 25., 30. und 35. Geburtstag am Stichtag
        self.assertEqual(sum(a['count'] for a in r['ages']), 12)
        self.assertEqual([(a['count'], a['avg_age'], a['median_age']) for a in r['age_stats']],
                         [(5, round((16 + 15 + 18 + 25 + 35) / 5, 1), 18), (5, round((17 + 21 + 20 + 30 + 34) / 5, 1), 21), (2, None, None)])
        self.assertEqual([a['count'] for a in r['entry_ages']], [1, 3, 2, 2, 2, 2, 0])            # ein Jahr jünger; G0 tritt am 16. Geburtstag ein
        self.assertEqual((r['months'], r['joined_gender']), ([0, 0, 0, 0, 0, 1], {'w': 1, 'm': 0, 'x': 0}))   # Eintritt am Stichtag zählt
        self.assertEqual((r['cards']['women_share'], r['kv_rows'][0]['women_share']), (0.5, 0.5))   # 5 : 5, die 2 divers/ohne Angabe zählen nicht mit
        ov = r['ov_rows'][0]                                                                  # alle ohne Ortsverband: eine Zeile
        self.assertEqual((ov['active'], ov['w'], ov['m'], ov['x'], ov['women_share'], ov['band_n'], ov['bands']), (12, 5, 5, 2, 0.5, [3, 4, 5], [0.25, 0.3333, 0.4167]))
        red = auswertung.redact(r, names=False)['ov_rows'][0]                                 # ab 3 Personen je Wert bleibt alles stehen, nur x = 2 nicht
        self.assertEqual((red['w'], red['m'], red['x'], red['women_share'], red['avg_age'], red['band_n'], red['bands']), (5, 5, None, 0.5, ov['avg_age'], [3, 4, 5], ov['bands']))
        lines = auswertung.csv(self.s, DAY).decode('utf-8-sig').splitlines()
        self.assertEqual(next(x for x in lines if x.startswith('Ortsverband;')).split(';')[13:], ['5', '5', '< 3', '50,0', '25,0', '33,3', '41,7'])
        self.assertEqual(next(x for x in lines if x.startswith('Kreisverband;')).split(';')[13:17], ['5', '5', '< 3', '50,0'])
        self.assertEqual(next(x for x in lines if x.startswith('Eintritte nach Geschlecht')).split(';')[13:17], ['< 3', '0', '0', ''])
        self.assertTrue(auswertung.pdf(self.s, DAY).startswith(b'%PDF'))
        self.s.db.execute("UPDATE members SET birth='' WHERE number='G11'")
        r = auswertung.report(self.s, DAY, today=DAY)
        self.assertEqual((r['cards']['no_birth'], sum(a['count'] for a in r['ages']), r['cards']['men']), (1, 11, 5))


    def test_frauenanteil(self):
        w = auswertung._women
        self.assertEqual([w(2, 1), w(0, 3), w(1, 1), w(0, 0), w(None, 5), w(5, None)], [0.6667, 0.0, None, None, None, None])
        self.assertEqual([w(5, 2, hidden=True), w(2, 5, hidden=True), w(5, 3, hidden=True), w(0, 3, hidden=True)], [None, None, 0.625, 0.0])   # PDF/CSV: „< 3“ nicht zurückrechenbar
        self.assertEqual(auswertung._bands([1, 4, 0], hidden=True), [None, 0.8, 0.0])
        self.assertEqual(auswertung._bands([0, 0, 0]), [None, None, None])
        self.s.db.execute("UPDATE members SET gender=''")                                      # nur „ohne Angabe“: kein Frauenanteil, auch nicht 0 %
        r = auswertung.report(self.s, DAY, today=DAY)
        self.assertEqual((r['cards']['active'], r['cards']['women'], r['cards']['women_share'], r['kv_rows'][0]['women_share'], r['ov_rows'][0]['women_share']),
                         (12, 0, None, None, None))
        self.assertTrue(auswertung.pdf(self.s, DAY).startswith(b'%PDF'))
        self.assertEqual(next(x for x in auswertung.csv(self.s, DAY).decode('utf-8-sig').splitlines() if x.startswith('Kreisverband;')).split(';')[13:17], ['0', '0', '12', ''])


if __name__ == '__main__':
    unittest.main()
