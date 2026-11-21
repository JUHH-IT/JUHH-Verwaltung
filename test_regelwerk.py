"""Regelwerk (ab 0.43): Werte kommen aus den Stammdaten, Termine aus den Stichtagen – nur erfundene Einstellungen."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

import regelwerk
import stammdaten
from core import Store


def cfg(**changes):
    return {**stammdaten.DEFAULTS, **changes}


class RegelwerkTests(unittest.TestCase):
    def test_zweiter_freitag_im_dezember(self):
        self.assertEqual(regelwerk.zweiter_freitag_dezember(2026), date(2026, 12, 11))   # 1. Dezember ist ein Dienstag
        self.assertEqual(regelwerk.zweiter_freitag_dezember(2028), date(2028, 12, 8))    # 1. Dezember ist ein Freitag

    def test_termine_folgen_den_stichtagen(self):
        t = {x['key']: x for x in regelwerk.termine(cfg(), date(2026, 11, 21))}
        self.assertEqual((t['beitritt']['tag'], t['rechnung']['tag'], t['beitrag']['tag'], t['lma']['tag']),
                         ('2026-11-30', '2026-12-11', '2027-01-30', '2027-02-06'))
        self.assertEqual([x['key'] for x in regelwerk.termine(cfg(), date(2026, 11, 21))], ['beitritt', 'rechnung', 'beitrag', 'lma'])
        later = {x['key']: x for x in regelwerk.termine(cfg(), date(2026, 12, 14))}
        self.assertEqual(later['rechnung']['tag'], '2027-12-10')                              # Termin vorbei: der des nächsten Jahres
        self.assertTrue(later['beitritt']['vorbei'])
        own = {x['key']: x['tag'] for x in regelwerk.termine(cfg(wahl_beitragsstichtag='2027-01-29'), date(2026, 11, 21))}
        self.assertEqual((own['beitrag'], own['lma']), ('2027-01-29', '2027-02-05'))

    def test_werte_aus_den_stammdaten(self):
        rows = {x['id']: x for x in regelwerk.regeln(cfg(), date(2026, 11, 21))['regeln']}
        self.assertEqual(rows['beitrag-alter']['wert'], '14–18 Jahre 10 € · 19–21 Jahre 20 € · ab 22 Jahren 30 €')
        self.assertEqual(rows['freigabe']['wert'], 'Grenze 500,00 € · ohne beide Freigaben keine Zahlung')
        self.assertIn('75 % · 50 % · 25 % · 0 %', rows['eintritt']['wert'])
        self.assertIn('Beitragsstichtag 30.01.2027', rows['wahlrecht']['wert'])
        changed = {x['id']: x for x in regelwerk.regeln(cfg(dues_old='3550', dues_mid_max='23', dues_prorate='no', age_basis='dec31',
                                                            approval_limit='40000'), date(2026, 11, 21))['regeln']}
        self.assertEqual(changed['beitrag-alter']['wert'], '14–18 Jahre 10 € · 19–23 Jahre 20 € · ab 24 Jahren 35,50 €')
        self.assertIn('ausgeschaltet', changed['eintritt']['wert'])
        self.assertEqual(changed['altersstichtag']['wert'], 'Alter am 31.12. des Beitragsjahres')
        self.assertIn('400,00 €', changed['freigabe']['wert'])

    def test_jede_regel_ist_vollstaendig(self):
        r = regelwerk.regeln(cfg(), date(2026, 11, 21))
        bereiche = {b['id'] for b in r['bereiche']}
        self.assertEqual(len({x['id'] for x in r['regeln']}), len(r['regeln']))
        for x in r['regeln']:
            self.assertIn(x['bereich'], bereiche, x['id'])
            self.assertIn(x['stand'], r['stand'], x['id'])
            self.assertTrue(all(isinstance(x[k], str) and x[k] for k in ('quelle', 'titel', 'text', 'wert')), x['id'])
        self.assertEqual(r['quellen'], ['Satzung vom 11.04.2026', 'Finanzordnung vom 13.03.2024'])

    def test_store_mit_gespeicherten_einstellungen(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(Path(tmp) / 't.db')
            try:
                s.save_settings({'dues_young': '12'}, 'Test')
                rows = {x['id']: x for x in regelwerk.regeln(s.config())['regeln']}
                self.assertTrue(rows['beitrag-alter']['wert'].startswith('14–18 Jahre 12 €'))
            finally:
                s.db.close()


if __name__ == '__main__':
    unittest.main()
