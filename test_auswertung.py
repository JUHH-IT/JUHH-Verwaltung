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
        self.assertEqual({o['label']: o['active'] for o in r['ov_rows']}, {'11 Testort': 1, 'ohne Ortsverband': 3})
        self.assertEqual({a['label']: a['count'] for a in r['ages'] if a['count']}, {'14–18': 1, '22–25': 1, '30–34': 1, '35 und älter': 1})
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
        self.assertIn('Altona', text)
        self.assertNotIn('T1', text)                                                         # keine Mitgliedsnummern oder Namen
        self.assertNotIn('Alt;', text)
        ov_line = next(line for line in text.splitlines() if 'Testort' in line)
        self.assertEqual(ov_line.split(';')[3:5], ['1', ''])                                   # 1 Mitglied: kein Durchschnittsalter
        small = auswertung.pdf(self.s, DAY, kv='5300')                                          # 1 Mitglied in der Auswahl
        self.assertTrue(small.startswith(b'%PDF'))
        kv_line = next(line for line in auswertung.csv(self.s, DAY, kv='5300').decode('utf-8-sig').splitlines() if line.startswith('Kreisverband;'))
        self.assertEqual(kv_line.split(';')[4], '')                                            # kein Alter, keine Beträge einer Person
        self.assertEqual(kv_line.split(';')[-3:], ['', '', ''])


if __name__ == '__main__':
    unittest.main()
