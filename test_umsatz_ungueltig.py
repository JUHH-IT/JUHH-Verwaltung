"""Kontoumsatz für ungültig erklären und strengere Prüfung beim Import (0.55, Wunsch Lennert Gust 23.11.2026: nach dem Import
stand ein Umsatz doppelt da – er muss sich entfernen lassen, und der Import soll das verhindern). Nur erfundene Daten."""
import tempfile
import unittest
from pathlib import Path

import berichte
import kasse
import rollen
import workflow
from core import Store

KOPF = 'Datum;Betrag;Name;Verwendungszweck\n'


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def vorschau(self, *zeilen, kopf=KOPF):
        self.n += 1
        return self.s.preview_bank((kopf + ''.join(z + '\n' for z in zeilen)).encode(), f'auszug-{self.n}.csv')

    def umsatz(self, zeile='2026-06-01;-50;Saal Muster GmbH;Rechnung 4711 Saalmiete Juni'):
        p = self.vorschau(zeile)
        self.s.import_bank(p, [r['row_no'] for r in p['rows']], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def stand(self, bid):
        return tuple(self.s.db.execute('SELECT state,note FROM bank WHERE id=?', (bid,)).fetchone())


class UmsatzUngueltigTests(Basis):
    def test_ungueltig_und_wieder_gueltig(self):
        bid = self.umsatz()
        with self.assertRaises(ValueError):
            workflow.ungueltig(self.s, bid, '  ', 'Lena')                       # ohne Grund nicht
        workflow.ungueltig(self.s, bid, ' doppelt   importiert ', 'Lena')
        self.assertEqual(self.stand(bid), ('void', 'doppelt importiert'))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM bank').fetchone()[0], 1)   # gelöscht wird nichts
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 0)   # und nichts gebucht
        row = self.s.db.execute("SELECT actor,detail FROM audit WHERE action='Kontoumsatz ungültig'").fetchone()
        self.assertEqual(tuple(row), ('Lena', '{"bank_id": %d, "grund": "doppelt importiert"}' % bid))
        [b] = [b for b in self.s.snapshot(2026)['bank'] if b['id'] == bid]
        self.assertEqual((b['state'], b['note']), ('void', 'doppelt importiert'))
        for versuch in (lambda: self.s.book(bid, [{'amount': '-50', 'account': '4210', 'kv': 'LV'}], 'Lena'),
                        lambda: workflow.park(self.s, bid, 'unklar', 'Lena'), lambda: workflow.ungueltig(self.s, bid, 'noch mal', 'Lena')):
            with self.assertRaises(ValueError):
                versuch()
        workflow.gueltig(self.s, bid, 'Lena')
        self.assertEqual(self.stand(bid), ('open', None))
        with self.assertRaises(ValueError):
            workflow.gueltig(self.s, bid, 'Lena')                                # war nicht ungültig

    def test_nur_offene_kontoumsaetze(self):
        bid = self.umsatz()
        workflow.park(self.s, bid, 'unklar', 'Lena')
        with self.assertRaises(ValueError) as cm:
            workflow.ungueltig(self.s, bid, 'doppelt', 'Lena')
        self.assertIn('Nur offene', str(cm.exception))
        kasse.set_opening(self.s, '2026-01-01', '100', 'Lena')
        beleg = kasse.create(self.s, {'kind': 'out', 'day': '2026-02-01', 'amount': '5', 'text': 'Porto'}, 'Lena')
        with self.assertRaises(ValueError):
            workflow.ungueltig(self.s, beleg['id'], 'doppelt', 'Lena')           # Kassenbelege: kasse.void
        with self.assertRaises(ValueError):
            workflow.ungueltig(self.s, 9999, 'doppelt', 'Lena')

    def test_ungueltiger_umsatz_zaehlt_nicht(self):
        a, b = self.umsatz(), self.umsatz('2026-06-02;-50;Saal Muster GmbH;Rechnung 4711 Saalmiete Juni (Kopie)')
        self.assertEqual(berichte._month_flows(self.s, '2026-06-01', '2026-06-30')['bank_count'], 2)
        workflow.ungueltig(self.s, b, 'doppelt importiert', 'Lena')
        flows = berichte._month_flows(self.s, '2026-06-01', '2026-06-30')
        self.assertEqual((flows['bank_count'], flows['bank_out']), (1, -5000))
        self.assertEqual([x['id'] for x in self.s.snapshot(2026)['bank'] if x['state'] == 'open'], [a])

    def test_rechte(self):
        lesen, schreiben = {'buchhaltung': 'lesen'}, {'buchhaltung': 'schreiben'}
        for path in ('/api/umsatz-ungueltig', '/api/umsatz-gueltig'):
            self.assertFalse(rollen.allowed(lesen, rollen.required('POST', path)), path)
            self.assertTrue(rollen.allowed(schreiben, rollen.required('POST', path)), path)


class AehnlicherUmsatzTests(Basis):
    """Beim Import: dieselbe Zahlung aus einem zweiten Abruf weicht oft nur leicht ab – abgewählt und markiert, nicht gesperrt."""

    def test_gleicher_zweck_andere_schreibweise_und_ein_tag_spaeter(self):
        self.umsatz()
        [r] = self.vorschau('2026-06-02;-50;SAAL MUSTER GMBH;Rechnung  4711 / Saalmiete-Juni')['rows']
        self.assertFalse(r['selected'])
        self.assertIn('Sehr ähnlicher Umsatz', r['warning'])
        self.assertFalse(r.get('duplicate'))                                     # abgewählt, aber auswählbar: es könnte eine echte zweite Zahlung sein

    def test_gleicher_tag_gleicher_name(self):
        self.umsatz('2026-06-01;24;Mara Muster;Beitrag')
        [r] = self.vorschau('2026-06-01;24;Mara  Muster;Mitgliedsbeitrag 2026')['rows']
        self.assertFalse(r['selected'])

    def test_keine_warnung_bei_anderen_zahlungen(self):
        self.umsatz('2026-06-01;24;Mara Muster;Beitrag 2026 Mara Muster')
        rows = self.vorschau('2026-06-01;24;Ole Beispiel;Beitrag 2026 Ole Beispiel',     # anderer Name, anderer Zweck
                             '2026-06-02;24;Mara Muster;Spende Sommerfest',              # anderer Tag, anderer Zweck
                             '2026-06-20;24;Mara Muster;Beitrag 2026 Mara Muster',       # gleicher Zweck, aber fast drei Wochen später
                             '2026-06-01;25;Mara Muster;Beitrag 2026 Mara Muster')['rows']   # anderer Betrag
        self.assertEqual([r['selected'] for r in rows], [True, True, True, True])

    def test_zweimal_in_einer_datei(self):
        rows = self.vorschau('2026-06-01;-50;Saal Muster GmbH;Rechnung 4711 Saalmiete Juni', '2026-06-01;-50;Saal Muster GmbH;RECHNUNG 4711 SAALMIETE JUNI')['rows']
        self.assertEqual([r['selected'] for r in rows], [True, False])

    def test_verschiedene_bankreferenzen_sind_zwei_umsaetze(self):
        kopf = 'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n'
        rows = self.vorschau('2026-06-01;24;Mara Muster;Beitrag 2026;R-1', '2026-06-01;24;MARA MUSTER;Beitrag 2026 ;R-2', kopf=kopf)['rows']
        self.assertEqual([r['selected'] for r in rows], [True, True])


if __name__ == '__main__':
    unittest.main()
