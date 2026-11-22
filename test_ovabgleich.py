"""Ortsverband nach Adresse und „geworben von“ (0.52) – nur erfundene Adressen und Mitglieder, kein Netzzugriff."""
import tempfile
import unittest
from pathlib import Path

import adressen
import doppelte
import gliederung
import mitglieder
import ovabgleich
from core import Store
from test_karte import feature


def bestand():
    """Zwei Bezirke: Altona mit drei Stadtteilen, Eimsbüttel mit einem."""
    out = [feature('Westweg', n, '20001', 'Altona', 'Teststadt-West', 9.90 + n / 10000, 53.55) for n in range(1, 11)]
    out += [feature('Ostweg', n, '20002', 'Altona', 'Teststadt-Ost', 9.92 + n / 10000, 53.55) for n in range(1, 11)]
    out += [feature('Grenzweg', n, '20003', 'Altona', 'Hamburg-Altstadt', 9.93, 53.55) for n in range(1, 4)]
    out += [feature('Nordweg', n, '20004', 'Eimsbüttel', 'Teststadt-Nord', 9.95, 53.58) for n in range(1, 11)]
    return out


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.s = Store(self.dir / 't.db')
        self.n = 0
        adressen.build(adressen.path(self.dir), bestand())

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def ov(self, kv, name, number=0):
        return gliederung.save_ov(self.s, {'kv': kv, 'number': number, 'name': name}, 'Test')

    def member(self, street, plz, kv='5100', city='Hamburg', **extra):
        self.n += 1
        return self.s.member({'number': f'K-{self.n}', 'first_name': 'Test', 'last_name': f'Person{self.n}', 'street': street, 'zip': plz,
                              'city': city, 'birth': '2000-02-01', 'joined': '2024-01-01', 'kv': kv, **extra}, 'Test')

    def m(self, mid):
        return dict(self.s.db.execute('SELECT * FROM members WHERE id=?', (mid,)).fetchone())


class GebieteTests(Basis):
    def test_namensvorschlag_nur_ganze_woerter(self):
        teile = [{'name': 'Hamm', 'kv': '5500'}, {'name': 'Hammerbrook', 'kv': '5500'}, {'name': 'Altona-Nord', 'kv': '5100'}]
        ovs = [{'id': 1, 'kv': '5500', 'name': 'Hammerbrook', 'active': 1}, {'id': 2, 'kv': '5100', 'name': 'Altona-Nord/Sternschanze', 'active': 1}]
        self.assertEqual(ovabgleich.namensvorschlag(ovs, teile, {}), {1: ['Hammerbrook'], 2: ['Altona-Nord']})

    def test_stadtteile_und_namensvorschlag(self):
        teile = ovabgleich.stadtteile(self.dir)
        self.assertEqual([(t['name'], t['kv']) for t in teile],
                         [('Hamburg-Altstadt', '5100'), ('Teststadt-Ost', '5100'), ('Teststadt-West', '5100'), ('Teststadt-Nord', '5300')])
        west, alt, sued, nord = self.ov('5100', 'Teststadt-West', 1), self.ov('5100', 'Altstadt'), self.ov('5100', 'Hamburg-Süd'), self.ov('5300', 'Teststadt-Nord')
        ost = self.ov('5100', 'Teststadt-Ost')
        ovabgleich.gebiet_speichern(self.s, self.dir, nord, ['Teststadt-Nord'], 'Test')
        v = ovabgleich.namensvorschlag(gliederung.ovs(self.s), teile, ovabgleich.gebiete(self.s.db))
        self.assertEqual((v[west], v[ost]), (['Teststadt-West'], ['Teststadt-Ost']))   # ganze Namen zuerst: „Teststadt“ allein verteilt nichts
        self.assertEqual(v[alt], ['Hamburg-Altstadt'])                     # Wortteil „Altstadt“
        self.assertNotIn(sued, v)                                         # „Hamburg“ allein zählt nicht
        self.assertNotIn(nord, v)                                         # hat schon ein Gebiet
        gliederung.set_active(self.s, ost, False, 'Test')
        v = ovabgleich.namensvorschlag(gliederung.ovs(self.s), teile, ovabgleich.gebiete(self.s.db))
        self.assertNotIn(ost, v)                                          # deaktiviert
        self.assertEqual(v[west], ['Teststadt-Ost', 'Teststadt-West'])    # Nachbar ohne eigenen Ortsverband: über den Wortteil, zum Bestätigen

    def test_gebiet_speichern_prueft_und_ersetzt(self):
        west, nord = self.ov('5100', 'West'), self.ov('5300', 'Nord')
        self.assertEqual(ovabgleich.gebiet_speichern(self.s, self.dir, west, ['Teststadt-West', ' Teststadt-Ost ', 'Teststadt-West'], 'Test'),
                         ['Teststadt-Ost', 'Teststadt-West'])
        with self.assertRaisesRegex(ValueError, 'Nicht im Bezirk'):
            ovabgleich.gebiet_speichern(self.s, self.dir, west, ['Teststadt-Nord'], 'Test')
        with self.assertRaisesRegex(ValueError, 'Nicht im Bezirk'):
            ovabgleich.gebiet_speichern(self.s, self.dir, west, ['Gibtsnicht'], 'Test')
        for falsch in ('Teststadt-West', [1], ['x' * 61]):
            with self.assertRaises(ValueError):
                ovabgleich.gebiet_speichern(self.s, self.dir, west, falsch, 'Test')
        ovabgleich.gebiet_speichern(self.s, self.dir, west, ['Teststadt-West'], 'Test')
        ovabgleich.gebiet_speichern(self.s, self.dir, nord, ['Teststadt-Nord'], 'Test')
        self.assertEqual(ovabgleich.gebiete(self.s.db), {west: ['Teststadt-West'], nord: ['Teststadt-Nord']})
        self.assertEqual(self.s.db.execute("SELECT detail FROM audit WHERE action='Gebiet eines Ortsverbands geändert' ORDER BY id DESC").fetchone()[0],
                         f'{{"id": {nord}, "stadtteile": 1}}')
        gliederung.delete_ov(self.s, nord, 'Test')                        # Gebiet geht mit dem Ortsverband
        self.assertEqual(ovabgleich.gebiete(self.s.db), {west: ['Teststadt-West']})
        self.assertEqual(ovabgleich.ovs_fuer(self.s.db, '5100', 'Teststadt-West'), [west])
        self.assertEqual(ovabgleich.ovs_fuer(self.s.db, '5300', 'Teststadt-West'), [])   # nur im eigenen Kreisverband
        gliederung.set_active(self.s, west, False, 'Test')
        self.assertEqual(ovabgleich.ovs_fuer(self.s.db, '5100', 'Teststadt-West'), [])   # deaktivierte nie vorschlagen

    def test_ohne_adressdaten(self):
        adressen.path(self.dir).unlink()
        self.member('Westweg 1', '20001')
        self.assertEqual(ovabgleich.stadtteile(self.dir), [])
        self.assertFalse(ovabgleich.abgleich(self.s, self.dir)['verfuegbar'])
        west = self.ov('5100', 'West')
        self.assertEqual(ovabgleich.gebiet_speichern(self.s, self.dir, west, ['Teststadt-West'], 'Test'), ['Teststadt-West'])   # ohne Bestand ungeprüft


class AbgleichTests(Basis):
    def setUp(self):
        super().setUp()
        self.west, self.ost, self.ost2, self.nord = self.ov('5100', 'West'), self.ov('5100', 'Ost A'), self.ov('5100', 'Ost B'), self.ov('5300', 'Nord')
        for ov, teile in ((self.west, ['Teststadt-West']), (self.ost, ['Teststadt-Ost']), (self.ost2, ['Teststadt-Ost']), (self.nord, ['Teststadt-Nord'])):
            ovabgleich.gebiet_speichern(self.s, self.dir, ov, teile, 'Test')

    def test_abgleich_und_uebernehmen(self):
        passt = self.member('Westweg 1', '20001', ov_id=self.west)
        ohne_ov = self.member('Westweg 2', '20001')                                       # noch kein Ortsverband
        falsch = self.member('Westweg 3', '20001', ov_id=self.ost)                        # wohnt im Gebiet von West
        umzug = self.member('Nordweg 1', '20004', ov_id=self.west)                        # wohnt in Eimsbüttel
        zwei = self.member('Ostweg 1', '20002')                                           # Stadtteil gehört zu zwei Ortsverbänden
        schon = self.member('Ostweg 2', '20002', ov_id=self.ost2)                         # einer der beiden: passt
        leer = self.member('Grenzweg 1', '20003')                                         # Stadtteil ohne Ortsverband
        weg = self.member('Hauptstraße 5', '22869', city='Schenefeld')                    # außerhalb
        unbekannt = self.member('Gibtsnichtweg 1', '20001')
        fest = self.member('Nordweg 2', '20004', ov_id=self.west, ov_fest='1')            # Ausnahme laut Satzung
        su = self.member('Nordweg 3', '20004', kv='5800')                                 # Schüler Union
        self.member('Westweg 4', '20001', ov_id=self.ost, left_on='2025-12-31')           # ausgetreten
        a = ovabgleich.abgleich(self.s, self.dir, today='2026-11-22')
        self.assertTrue(a['verfuegbar'] and a['gebiete'])
        self.assertEqual({e['id']: (e['ov'], e['kv'], e['kv_anders']) for e in a['aendern']},
                         {ohne_ov: (self.west, '5100', False), falsch: (self.west, '5100', False), umzug: (self.nord, '5300', True)})
        self.assertEqual([(e['id'], e['ovs']) for e in a['mehrdeutig']], [(zwei, [self.ost, self.ost2])])
        self.assertEqual([(e['id'], e['stadtteil']) for e in a['ohne_gebiet']], [(leer, 'Hamburg-Altstadt')])
        self.assertEqual({e['id']: e['art'] for e in a['fehler']}, {weg: 'ausserhalb', unbekannt: 'unbekannt'})
        self.assertEqual((a['passt'], a['fest'], a['sonder']), (2, 1, 1))
        self.assertNotIn('name', a['aendern'][0])                                         # nur Kennungen, keine Namen
        stand = {e['id']: e for e in a['aendern'] + a['mehrdeutig']}
        wahl = lambda mid, ov: {'id': mid, 'ov': ov, 'ov_alt': stand[mid]['ov_alt'], 'kv_alt': stand[mid]['kv_alt']}   # noqa: E731
        r = ovabgleich.uebernehmen(self.s, self.dir, [wahl(falsch, self.west), wahl(umzug, self.nord), wahl(zwei, self.ost2),
                                                      wahl(ohne_ov, self.ost)],   # ohne_ov: kein Vorschlag für „Ost A“ → übersprungen
                                   [leer, falsch], 'Test', today='2026-11-22')
        self.assertEqual(r, {'geaendert': 3, 'fest': 1, 'uebersprungen': 1})
        self.assertEqual((self.m(falsch)['ov_id'], self.m(umzug)['kv'], self.m(umzug)['ov_id'], self.m(zwei)['ov_id'], self.m(ohne_ov)['ov_id']),
                         (self.west, '5300', self.nord, self.ost2, None))
        self.assertEqual((self.m(leer)['ov_fest'], self.m(falsch)['ov_fest']), (1, 0))   # wer geändert wird, wird nicht zugleich fest
        self.assertEqual(self.m(passt)['ov_id'], self.west)
        self.assertEqual(mitglieder.verlauf(self.s, umzug)[0]['text'], 'Geändert: Kreisverband, Ortsverband (Ortsverband nach Adresse)')
        self.assertEqual(self.s.db.execute("SELECT detail FROM audit WHERE action='Ortsverbände nach Adresse abgeglichen'").fetchone()[0],
                         '{"geaendert": 3, "fest": 1, "uebersprungen": 1}')
        b = ovabgleich.abgleich(self.s, self.dir, today='2026-11-22')
        self.assertEqual(([e['id'] for e in b['aendern']], b['ohne_gebiet'], b['fest']), ([ohne_ov], [], 2))

    def test_eingaben_werden_geprueft(self):
        mid = self.member('Westweg 3', '20001', ov_id=self.ost)
        ok = {'id': mid, 'ov': self.west, 'ov_alt': self.ost, 'kv_alt': '5100'}
        for aendern, fest in (([], []), ('x', []), ([{**ok, 'ov': 'x'}], []), ([{**ok, 'id': True}], []), ([], [None]), ([{**ok, 'ov': 3.7}], []),
                              ([], [2.0]), ([ok, {**ok, 'ov': self.ost}], []), ([[mid, self.west]], []), ([{'id': mid, 'ov': self.west}], [])):
            with self.assertRaises(ValueError, msg=repr((aendern, fest))):
                ovabgleich.auswahl(aendern, fest)
            with self.assertRaises(ValueError, msg=repr((aendern, fest))):
                ovabgleich.uebernehmen(self.s, self.dir, aendern, fest, 'Test')
        self.assertEqual(self.m(mid)['ov_id'], self.ost)

    def test_handaenderung_und_fremde_kennungen_werden_nicht_ueberschrieben(self):
        mid = self.member('Westweg 3', '20001', ov_id=self.ost)
        alt = self.member('Westweg 4', '20001', ov_id=self.ost, left_on='2025-12-31')        # ausgetreten: nicht im Abgleich
        e = next(x for x in ovabgleich.abgleich(self.s, self.dir, today='2026-11-22')['aendern'] if x['id'] == mid)
        stamm = {k: self.m(mid)[k] for k in ('number', 'first_name', 'last_name', 'street', 'zip', 'city', 'birth', 'joined', 'kv')}
        self.s.update_member(mid, {**stamm, 'ov_id': self.ost2}, 'Test')                    # inzwischen von Hand: Ost B
        r = ovabgleich.uebernehmen(self.s, self.dir, [{'id': mid, 'ov': e['ov'], 'ov_alt': e['ov_alt'], 'kv_alt': e['kv_alt']}], [alt], 'Test',
                                   today='2026-11-22')
        self.assertEqual(r, {'geaendert': 0, 'fest': 0, 'uebersprungen': 2})
        self.assertEqual((self.m(mid)['ov_id'], self.m(alt)['ov_fest']), (self.ost2, 0))

    def test_import_der_cdu_datei_gilt_mit_hinweis(self):
        mid = self.member('Westweg 3', '20001', ov_id=self.west, ov_fest='1')
        raw = ('Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;LVKV_Nummer;Ortsverband;Ortsbezeichnung\n'
               f"{self.m(mid)['number']};Test;Person1;Westweg 3;20001;Hamburg;01.02.2000;01.01.2024;701;0;Ost A\n").encode()
        row = mitglieder.preview(self.s, raw, 'liste.csv')['rows'][0]
        self.assertEqual(row['values']['ov_id'], self.ost)                                  # Entscheidung 22.11.2026: die CDU-Datei gilt
        self.assertIn('ersetzt den im Programm (dort „Ortsverband fest“)', row['warning'])
        self.assertEqual(row['values']['ov_fest'], 1)                                       # das Kennzeichen selbst bleibt

    def test_kreisverbandswechsel_des_ortsverbands_leert_das_gebiet(self):
        gliederung.save_ov(self.s, {'kv': '5300', 'number': 0, 'name': 'West'}, 'Test', self.west)
        self.assertNotIn(self.west, ovabgleich.gebiete(self.s.db))


class WerberTests(Basis):
    def test_geworben_von_und_ortsverband_fest(self):
        werber = self.member('Westweg 1', '20001')
        neu = self.member('Westweg 2', '20001', geworben_von=str(werber), ov_fest='1')
        self.assertEqual((self.m(neu)['geworben_von'], self.m(neu)['ov_fest']), (werber, 1))
        self.assertIn(('Geworben von', f"Test Person1 (K-1)"), mitglieder.angaben(self.s, self.m(neu)))
        self.assertIn(('Ortsverband fest (nicht nach Adresse)', 'ja'), mitglieder.angaben(self.s, self.m(neu)))
        for falsch in ('9999', 'abc', True, str(neu)):
            with self.assertRaisesRegex(ValueError, 'Geworben von'):
                self.s.update_member(neu, {**{k: self.m(neu)[k] for k in ('number', 'first_name', 'last_name', 'street', 'zip', 'city', 'birth', 'joined', 'kv')},
                                           'geworben_von': falsch}, 'Test')
        self.s.update_member(neu, {**{k: self.m(neu)[k] for k in ('number', 'first_name', 'last_name', 'street', 'zip', 'city', 'birth', 'joined', 'kv')}},
                             'Test')
        self.assertEqual(self.m(neu)['geworben_von'], werber)                            # nicht mitgeschickt: bleibt
        mitglieder.delete(self.s, werber, 'Test', arrears_ok=True)
        self.assertIsNone(self.m(neu)['geworben_von'])                                    # Verweis entfällt mit dem Löschen

    def test_geworben_von_als_freitext(self):
        stamm = ('number', 'first_name', 'last_name', 'street', 'zip', 'city', 'birth', 'joined', 'kv')
        neu = self.member('Westweg 2', '20001', geworben_text='  Infostand Uni ')
        self.assertEqual((self.m(neu)['geworben_text'], self.m(neu)['geworben_von']), ('Infostand Uni', None))
        self.assertIn(('Geworben von (kein Mitglied)', 'Infostand Uni'), mitglieder.angaben(self.s, self.m(neu)))
        with self.assertRaisesRegex(ValueError, 'höchstens 200'):
            self.s.update_member(neu, {**{k: self.m(neu)[k] for k in stamm}, 'geworben_text': 'x' * 201}, 'Test')
        werber = self.member('Westweg 1', '20001')
        self.s.update_member(neu, {**{k: self.m(neu)[k] for k in stamm}, 'geworben_von': str(werber), 'geworben_text': 'Infostand Uni'}, 'Test')
        self.assertEqual((self.m(neu)['geworben_von'], self.m(neu)['geworben_text']), (werber, None))   # Mitglied geht vor, Text entfällt

    def test_zusammenfuehren_haengt_werber_um(self):
        alt, neu = self.member('Westweg 1', '20001'), self.member('Westweg 1', '20001')
        geworben = self.member('Westweg 2', '20001', geworben_von=str(neu))
        selbst = self.member('Westweg 3', '20001')
        self.s.update_member(alt, {**{k: self.m(alt)[k] for k in ('number', 'first_name', 'last_name', 'street', 'zip', 'city', 'birth', 'joined', 'kv')},
                                   'geworben_von': str(selbst)}, 'Test')
        doppelte.zusammenfuehren(self.s, alt, neu, 'Test')
        self.assertEqual(self.m(geworben)['geworben_von'], alt)
        doppelte.zusammenfuehren(self.s, alt, selbst, 'Test')                             # wer alt geworben hat, geht in alt auf
        self.assertIsNone(self.m(alt)['geworben_von'])                                    # nie sich selbst geworben
        bleibt, geht = self.member('Westweg 5', '20001'), self.member('Westweg 5', '20001', geworben_text='Infostand Uni', ov_fest='1')
        doppelte.zusammenfuehren(self.s, bleibt, geht, 'Test')                            # Angaben des aufgehenden bleiben erhalten
        self.assertEqual((self.m(bleibt)['geworben_text'], self.m(bleibt)['ov_fest']), ('Infostand Uni', 1))


if __name__ == '__main__':
    unittest.main()
