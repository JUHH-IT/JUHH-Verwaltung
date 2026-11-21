"""Doppelte Mitglieder finden (ab 0.45) – nur erfundene Testdaten."""
import json
import tempfile
import unittest
from pathlib import Path

import doppelte
import test_rollen   # Anmeldung und Personen am Testserver (nicht als Name importiert, sonst liefen dessen Tests hier noch einmal)
import test_server
from core import Store

BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg', birth='2000-05-01', joined='2020-01-01', kv='5100')


class DoppelteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, first, last, **extra):
        self.n += 1
        return self.s.member({**BASE, 'number': f'T-{self.n}', 'first_name': first, 'last_name': last, **extra}, 'Test')

    def groups(self):
        return [(g['ids'], g['gruende']) for g in doppelte.gruppen(self.s)]

    def test_name_und_geburtsdatum_auch_mit_schreibvarianten(self):
        a = self.member('Jonas', 'Müller')
        b = self.member('Jonas', 'Mueller', street='Anderer Weg 5')          # Umlaut ausgeschrieben, andere Anschrift
        c = self.member('Müller', 'Jonas', street='Dritter Weg 7')           # Vor- und Nachname vertauscht
        self.member('Jonas', 'Müller', birth='2001-05-01', street='Vierter Weg 9')   # anderes Geburtsdatum: kein Treffer
        self.assertEqual(self.groups(), [([a, b, c], ['gleicher Name und Geburtsdatum'])])

    def test_name_und_anschrift(self):
        a = self.member('Lea', 'Probe', street='Musterstraße 12')
        b = self.member('Lea', 'Probe', street='Musterstr. 12', birth='1999-01-01')
        self.member('Lea', 'Probe', street='Musterstraße 14', birth='1998-01-01')   # andere Hausnummer
        self.assertEqual(self.groups(), [([a, b], ['gleicher Name und Anschrift'])])

    def test_mail_und_geburtsdatum(self):
        a = self.member('Tim', 'Albers', email='Familie@example.org')
        b = self.member('Timo', 'Alberts', street='Anderer Weg 5', email='familie@example.org ')
        self.member('Tom', 'Albers', street='Dritter Weg 7', email='familie@example.org', birth='2003-02-02')   # Geschwister: kein Treffer
        self.assertEqual(self.groups(), [([a, b], ['gleiche E-Mail-Adresse und Geburtsdatum'])])

    def test_abgekuerzter_vorname_aber_keine_zwillinge(self):
        a = self.member('Max', 'Beispiel')
        b = self.member('Maximilian', 'Beispiel', street='Anderer Weg 5')
        self.member('Anna', 'Beispiel', street='Dritter Weg 7')               # Zwilling mit anderem Vornamen
        self.member('Mia', 'Beispiel', street='Vierter Weg 9')
        self.assertEqual(self.groups(), [([a, b], ['ähnlicher Vorname, gleicher Nachname und Geburtsdatum'])])

    def test_mehrere_gruende_und_gruppen(self):
        a = self.member('Erika', 'Muster', email='erika@example.org')
        b = self.member('Erika', 'Muster', email='erika@example.org')         # gleiche Anschrift, Name, Geburt, E-Mail
        c = self.member('Ole', 'Probe', street='Anderer Weg 5', birth='1990-01-01')
        d = self.member('Ole', 'Probe', street='Dritter Weg 7', birth='1990-01-01')
        self.assertEqual(self.groups(), [([a, b], ['gleicher Name und Geburtsdatum', 'gleicher Name und Anschrift',
                                                   'gleiche E-Mail-Adresse und Geburtsdatum']),
                                         ([c, d], ['gleicher Name und Geburtsdatum'])])

    def test_kein_duplikat(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        c = self.member('Erika', 'Muster', street='Dritter Weg 7')
        doppelte.kein_duplikat(self.s, [a, b], 'Test')
        self.assertEqual(self.groups(), [([a, b, c], ['gleicher Name und Geburtsdatum'])])   # c hängt noch an beiden
        doppelte.kein_duplikat(self.s, [a, b, c, a], 'Test')
        self.assertEqual(self.groups(), [])
        doppelte.kein_duplikat(self.s, [a, b], 'Test')                                     # zweimal: kein Fehler
        row = self.s.db.execute("SELECT detail FROM audit WHERE action='Kein Duplikat bestätigt' ORDER BY id DESC").fetchone()
        self.assertEqual(json.loads(row[0]), {'ids': [a, b]})
        for bad in (None, [a], [a, a], [a, 'x'], [a, True], [a, 999], list(range(1, doppelte.MAX_GRUPPE + 2))):
            with self.assertRaises(ValueError, msg=bad):
                doppelte.kein_duplikat(self.s, bad, 'Test')

    def test_tabelle_bleibt_beim_neuen_start(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        doppelte.kein_duplikat(self.s, [a, b], 'Test')
        self.s.db.close()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.assertEqual(self.groups(), [])



class DoppelteHttpTests(unittest.TestCase):
    """Am laufenden Server: Doppelte sehen mit „Mitglieder lesen“, „kein Duplikat“ nur mit „Mitglieder ändern“."""
    setUpClass = classmethod(test_server.ServerHttpTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerHttpTests.tearDownClass.__func__)
    call = test_server.ServerHttpTests.call
    setUp, login, get, post, person = (getattr(test_rollen.RollenHttpTests, name) for name in ('setUp', 'login', 'get', 'post', 'person'))

    def test_rechte(self):
        officer = self.person('Mitglieder-Person', 'Landesmitgliederbeauftragter')
        member = self.get(officer, '/api/data?year=2026')[1]['members'][0]
        self.assertEqual(self.post(officer, '/api/member', {**BASE, 'number': 'M-2', 'first_name': 'Erika', 'last_name': 'Muster',
                                                            'birth': member['birth']})[0], 200)
        status, r = self.get(officer, '/api/doppelte')
        self.assertEqual((status, len(r['gruppen'])), (200, 1))
        self.assertIn('gleicher Name und Geburtsdatum', r['gruppen'][0]['gruende'])
        reader = self.person('leitung', 'Landesgeschäftsführer')
        self.assertEqual(self.get(reader, '/api/doppelte')[0], 200)
        self.assertEqual(self.post(reader, '/api/kein-duplikat', {'ids': r['gruppen'][0]['ids']})[0], 403)
        self.assertEqual(self.post(officer, '/api/kein-duplikat', {'ids': 'x'})[0], 400)
        self.assertEqual(self.post(officer, '/api/kein-duplikat', {'ids': r['gruppen'][0]['ids']})[0], 200)
        self.assertEqual(self.get(officer, '/api/doppelte')[1]['gruppen'], [])
        admin = self.person('technik', 'Administrator')                                               # keine Mitgliederdaten
        self.assertEqual(self.get(admin, '/api/doppelte')[0], 403)


if __name__ == '__main__':
    unittest.main()
