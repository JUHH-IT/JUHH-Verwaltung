"""Modul Sicherheit (ab 0.35): Ereignisse, Notfall, Wartung, Passwortbestätigung, Sitzungen, Sperren, Dateiprüfung – nur erfundene Daten."""
import os
os.environ['JU_FINANZEN_OHNE_SCHLUESSELBUND'] = '1'
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import app
import rollen
import sicherheit
import test_rollen
import test_server
import zugang
from core import Store

PASSWORT = test_server.PASSWORT


class SicherheitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 't.db')
        self.addCleanup(self.store.db.close)
        self.db = self.store.db
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)

    def test_notfall_und_wartung(self):
        self.assertIsNone(sicherheit.sperre(self.db))
        with self.assertRaises(ValueError):
            sicherheit.notfall_an(self.db, 'Erste', '  ')                                   # Grund ist Pflicht
        sicherheit.wartung(self.db, 'Erste', True, 'Update')
        self.assertEqual((sicherheit.sperre(self.db)['art'], sicherheit.sperre(self.db)['text']), ('wartung', 'Update'))
        sicherheit.notfall_an(self.db, 'Erste', 'Verdacht auf fremden Zugang')
        self.assertEqual(sicherheit.sperre(self.db)['art'], 'notfall')                     # Notfall vor Wartung
        with self.assertRaises(ValueError):
            sicherheit.notfall_an(self.db, 'Erste', 'noch einmal')
        sicherheit.notfall_aus(self.db, 'Erste')
        self.assertEqual(sicherheit.sperre(self.db)['art'], 'wartung')
        sicherheit.wartung(self.db, 'Erste', False)
        self.assertIsNone(sicherheit.sperre(self.db))
        with self.assertRaises(ValueError):
            sicherheit.notfall_aus(self.db, 'Erste')
        arten = [r['art'] for r in sicherheit.ereignisse(self.db)]
        for art in ('wartung_an', 'notfall_an', 'notfall_aus', 'wartung_aus'):
            self.assertIn(art, arten)
        self.assertNotIn('notfall', self.store.settings())                                  # Zustand nicht in den Stammdaten

    def test_ereignisse_filter_csv_und_verwaltung(self):
        sicherheit.ereignis(self.db, 'anmeldung', 'Erste', 'gerät-1')
        sicherheit.ereignis(self.db, 'anmeldung_fehl', '', 'gerät-2', {'name_bekannt': False})
        self.db.execute("INSERT INTO sicherheit_ereignisse(zeit,art,person) VALUES('2020-01-01T00:00:00','anmeldung','Alt')")
        self.db.commit()
        alle = sicherheit.ereignisse(self.db)
        self.assertIn('verwaltung', {r['art'] for r in alle})                              # Benutzer angelegt (Änderungsprotokoll)
        self.assertNotIn('Alt', {r['person'] for r in alle})                                # älter als der Zeitraum
        self.assertEqual([r['geraet'] for r in sicherheit.ereignisse(self.db, art='anmeldung_fehl')], ['gerät-2'])
        self.assertEqual(len(sicherheit.ereignisse(self.db, suche='GERÄT-1')), 1)
        sicherheit.ereignis(self.db, 'verweigert', '=HYPERLINK("x")', '', {'adresse': '/api/kasse'})
        text = sicherheit.ereignisse_csv(sicherheit.ereignisse(self.db))
        self.assertTrue(text.startswith('﻿Zeit;Ereignis'))
        self.assertIn("'=HYPERLINK", text)                                                 # Formelschutz
        self.assertNotIn(";';", text)                                                         # leere Zellen bleiben leer
        self.assertEqual(len(sicherheit.ereignisse(self.db, limit=2)), 2)
        with self.assertRaises(AssertionError):
            sicherheit.ereignis(self.db, 'unbekannt')

    def test_konten(self):
        rollen.save_user(self.db, test_rollen.P('Zweite', password=PASSWORT, roles=[]), 'Erste')
        sicherheit.ereignis(self.db, 'anmeldung', 'Erste')
        k = {x['name']: x for x in sicherheit.konten(self.db)}
        self.assertTrue(k['Erste']['privilegiert'] and k['Erste']['passwort'] and not k['Erste']['ungenutzt'])
        self.assertTrue(k['Zweite']['ungenutzt'] and not k['Zweite']['privilegiert'])

    def test_dateipruefung(self):
        root = Path(self.tmp.name) / 'programm'
        (root / 'server').mkdir(parents=True)
        (root / 'venv').mkdir()
        (root / 'app.py').write_text('print(1)\n')
        (root / 'b.py').write_text('x = 2\n')
        (root / 'venv' / 'site.py').write_text('venv gehört dazu\n')
        os.symlink('/usr/bin/env', root / 'venv' / 'python')                                # Verknüpfungen zählen nicht (wie find -type f)
        self.assertFalse(sicherheit.dateipruefung(root)['verfuegbar'])
        lines = [f"{hashlib.sha256((root / n).read_bytes()).hexdigest()}  ./{n}" for n in ('app.py', 'b.py', 'venv/site.py')]
        (root / 'server' / 'PRUEFLISTE').write_text('\n'.join(lines) + '\n')
        self.assertTrue(sicherheit.dateipruefung(root)['ok'])
        self.assertEqual(len(sicherheit.dateipruefung(root)['liste']), 12)
        (root / 'app.py').write_text('print(2)\n')
        (root / 'b.py').unlink()
        (root / 'sitecustomize.py').write_text('eingeschleust\n')
        (root / 'venv' / 'zzz.pth').write_text('import boese\n')                          # .pth in der venv wirkt beim Start
        (root / '__pycache__').mkdir()
        (root / '__pycache__' / 'app.cpython-312.pyc').write_bytes(b'x')
        r = sicherheit.dateipruefung_lauf(self.db, root, 'Erste')
        self.assertEqual((r['ok'], r['geaendert'], r['fehlend']), (False, ['app.py'], ['b.py']))
        self.assertEqual(sorted(r['zusaetzlich']), ['__pycache__/app.cpython-312.pyc', 'sitecustomize.py', 'venv/zzz.pth'])
        self.assertEqual(sicherheit.letzte_dateipruefung(self.db, root)['geaendert'], ['app.py'])   # gespeichert, nicht älter als ein Tag
        self.assertEqual(sicherheit.ereignisse(self.db, art='dateipruefung')[0]['detail']['zusaetzlich'], 3)

    def test_throttle_gesperrt_und_freigeben(self):
        t = zugang.Throttle()
        for _ in range(zugang.FREE_ATTEMPTS + 1):
            t.attempt(('geraet:a', 'name:x'))
        self.assertEqual({k for k, _, _ in t.gesperrt()}, {'geraet:a', 'name:x'})
        t.freigeben('name:x')
        self.assertEqual({k for k, _, _ in t.gesperrt()}, {'geraet:a'})
        self.assertEqual(t.wait(('name:x',)), 0)


class SicherheitHttpTests(unittest.TestCase):
    """Am laufenden Server: Sperren, Passwortbestätigung, Ereignisse – unabhängig von der Oberfläche."""
    setUpClass = classmethod(test_server.ServerHttpTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerHttpTests.tearDownClass.__func__)
    call = test_server.ServerHttpTests.call
    setUp = test_rollen.RollenHttpTests.setUp
    login = test_rollen.RollenHttpTests.login
    get = test_rollen.RollenHttpTests.get
    post = test_rollen.RollenHttpTests.post
    person = test_rollen.RollenHttpTests.person

    def events(self, art):
        return [r for r in self.get(self.chefin, f'/api/sicherheit-ereignisse?art={art}&tage=7')[1]['ereignisse']]

    def test_anmeldung_und_fehlversuche_im_protokoll(self):
        self.assertEqual(self.call('/api/login', {'name': 'Chefin', 'password': 'falsch-falsch-falsch'})[0], 401)
        self.assertEqual(self.call('/api/login', {'name': 'mein-Passwort-als-Name', 'password': 'x'})[0], 401)
        fehl = self.events('anmeldung_fehl')
        self.assertEqual(sorted(r['person'] for r in fehl), ['', 'Chefin'])                # unbekannte Namen nicht ins Protokoll
        self.assertNotIn('mein-Passwort-als-Name', json.dumps(fehl))
        self.assertTrue(self.events('anmeldung'))
        status, lage = self.get(self.chefin, '/api/sicherheit')
        self.assertEqual((status, lage['lagebild']['fehlanmeldungen_24h']), (200, 2))
        self.assertFalse(lage['lagebild']['dateipruefung']['verfuegbar'])                  # Entwicklungsstand ohne Prüfliste
        import urllib.request
        req = urllib.request.Request(self.url + '/api/sicherheit-ereignisse.csv?tage=7',
                                     headers={'Host': 'ju-finanzen.beispiel.ts.net', 'Cookie': f"__Host-ju_session={self.chefin['cookie']}"})
        with urllib.request.urlopen(req) as r:
            self.assertIn('Fehlanmeldung', r.read().decode('utf-8-sig'))

    def test_passwortbestaetigung(self):
        who = self.login('Chefin', confirm=False)
        status, body = self.post(who, '/api/rolle', {'name': 'Neue Rolle', 'rights': {}})
        self.assertEqual((status, body.get('confirm')), (428, True))
        self.assertEqual(self.get(who, '/api/backup')[0], 428)
        self.assertEqual(self.post(who, '/api/bestaetigen', {'password': 'falsch'})[0], 400)
        self.assertEqual(self.events('bestaetigung_fehl')[0]['person'], 'Chefin')
        self.assertEqual(self.post(who, '/api/bestaetigen', {'password': PASSWORT})[0], 200)
        self.assertEqual(self.post(who, '/api/rolle', {'name': 'Neue Rolle', 'rights': {}})[0], 200)
        for s in app.SESSIONS.values():
            s['confirmed'] -= app.CONFIRM_SECONDS + 1                                       # nach 5 Minuten wieder nötig
        self.assertEqual(self.post(who, '/api/rolle', {'name': 'Noch eine', 'rights': {}})[0], 428)

    def test_notfall(self):
        vorsitz = self.person('Vorsitz', 'Landesvorsitzender')                               # „Sicherheit lesen“: darf auslösen
        mitglieder = self.person('Mitglieder', 'Landesmitgliederbeauftragter')
        technik = self.person('Technik', 'Administrator')
        self.assertEqual(self.post(mitglieder, '/api/notfall-an', {'grund': 'Test'})[0], 403)
        status, body = self.post(vorsitz, '/api/notfall-an', {'grund': 'Unbekannte Anmeldung gesehen'})
        self.assertEqual((status, body['beendet']), (200, 1))                              # die Mitgliederbeauftragte ist abgemeldet
        self.assertEqual(self.get(mitglieder, '/api/data?year=2026')[0], 401)
        status, body = self.get(vorsitz, '/api/data?year=2026')                              # Auslöser bleibt angemeldet, aber gesperrt
        self.assertEqual((status, body['sperre']), (503, 'notfall'))
        self.assertEqual(self.get(vorsitz, '/api/sicherheit')[0], 200)
        sperre = self.get(vorsitz, '/api/status')[1]['sperre']
        self.assertEqual((sperre['fuer_mich'], sperre['grund'], sperre['von']), (True, 'Unbekannte Anmeldung gesehen', 'Vorsitz'))
        self.assertEqual(self.post(vorsitz, '/api/notfall-aus', {})[0], 403)               # beenden nur mit „Sicherheit ändern“
        # ein übernommenes Konto darf im Notfall nicht das Passwort ändern und die echte Person aussperren
        self.assertEqual(self.post(vorsitz, '/api/password', {'current': PASSWORT, 'new': PASSWORT + '-neu', 'repeat': PASSWORT + '-neu'})[0], 503)
        self.assertEqual(self.post(vorsitz, '/api/notfall-an', {'grund': 'noch einmal', 'alle_abmelden': True})[0], 400)
        neu = self.login('Mitglieder', confirm=False)                                        # neue Anmeldung geht, Arbeit nicht
        self.assertEqual(self.get(neu, '/api/data?year=2026')[0], 503)
        self.assertNotIn('grund', self.get(neu, '/api/status')[1]['sperre'])               # Grund nur mit „Sicherheit lesen“
        self.assertEqual(self.get(self.chefin, '/api/data?year=2026')[0], 200)             # „Sicherheit ändern“ arbeitet weiter
        self.assertFalse(self.get(self.chefin, '/api/status')[1]['sperre']['fuer_mich'])
        self.assertEqual(self.post(self.chefin, '/api/mail-send', {'ids': []})[0], 503)   # E-Mail-Versand angehalten, auch für sie
        self.assertEqual(self.get(technik, '/api/data?year=2026')[0], 200)                 # Administrator blieb angemeldet
        self.assertEqual(self.post(self.chefin, '/api/notfall-aus', {})[0], 200)
        self.assertEqual(self.get(neu, '/api/data?year=2026')[0], 200)
        self.assertIsNone(self.get(neu, '/api/status')[1]['sperre'])
        arten = [r['art'] for r in self.events('')]
        self.assertIn('notfall_an', arten)
        self.assertIn('notfall_aus', arten)

    def test_notfall_alle_abmelden_und_wartung(self):
        technik = self.person('Technik', 'Administrator')
        self.assertEqual(self.post(self.chefin, '/api/notfall-an', {'grund': 'Übung', 'alle_abmelden': True})[1]['beendet'], 1)
        self.assertEqual(self.get(technik, '/api/data?year=2026')[0], 401)
        self.assertEqual(self.post(self.chefin, '/api/notfall-aus', {})[0], 200)
        vorsitz = self.person('Vorsitz', 'Landesvorsitzender')
        self.assertEqual(self.post(vorsitz, '/api/wartung', {'an': True})[0], 403)
        self.assertEqual(self.post(self.chefin, '/api/wartung', {'an': True, 'text': 'Update am Abend'})[0], 200)
        self.assertEqual(self.get(vorsitz, '/api/data?year=2026')[1].get('sperre'), 'wartung')
        self.assertEqual(self.get(vorsitz, '/api/status')[1]['sperre']['text'], 'Update am Abend')
        self.assertEqual(self.get(vorsitz, '/api/data?year=2026')[0], 503)
        self.assertEqual(self.post(self.chefin, '/api/mail-send', {'ids': []})[0] != 503, True)   # Wartung hält den Versand nicht an
        self.post(self.chefin, '/api/wartung', {'an': False})
        self.assertEqual(self.get(vorsitz, '/api/data?year=2026')[0], 200)

    def test_sitzungen_sperren_und_verweigert(self):
        vorsitz = self.person('Vorsitz', 'Landesvorsitzender')
        self.assertEqual(self.get(vorsitz, '/api/sicherheit-verwaltung')[0], 403)          # nur „Sicherheit ändern“
        self.assertEqual(self.get(vorsitz, '/api/kasse?year=2026')[0], 200)
        self.assertEqual(self.post(vorsitz, '/api/book', {})[0], 403)
        self.assertEqual([r['detail']['adresse'] for r in self.events('verweigert')], ['/api/sicherheit-verwaltung'])   # je Minute einmal
        for x in app.SESSIONS.values():
            x['verweigert_zeit'] = 0
        self.get(vorsitz, '/api/x' + 'y' * 5000)
        self.assertEqual(len(self.events('verweigert')[0]['detail']['adresse']), 120)            # gekürzt
        for _ in range(zugang.FREE_ATTEMPTS + 1):
            self.call('/api/login', {'name': 'Vorsitz', 'password': 'falsch-falsch'})
        status, data = self.get(self.chefin, '/api/sicherheit-verwaltung')
        self.assertEqual(status, 200)
        self.assertNotIn(self.chefin['cookie'], json.dumps(data))                           # nie den Sitzungsschlüssel ausliefern
        andere = [x for x in data['sitzungen'] if x['person'] == 'Vorsitz']
        self.assertEqual(len(andere), 1)
        sperre = next(x for x in data['sperren'] if x['wert'] == 'Vorsitz')
        self.assertNotIn('name:', json.dumps(data['sperren']))                               # Kennung statt Schlüssel
        self.assertEqual(sperre['art'], 'Name')
        eigene = next(x for x in data['sitzungen'] if x['eigene'])
        self.assertEqual(self.post(self.chefin, '/api/sitzung-beenden', {'id': eigene['id']})[0], 400)
        self.assertEqual(self.post(self.chefin, '/api/sitzung-beenden', {'id': andere[0]['id']})[0], 200)
        self.assertEqual(self.get(vorsitz, '/api/data?year=2026')[0], 401)
        for x in data['sperren']:                                                            # Name und Gerät sind gesperrt
            self.assertEqual(self.post(self.chefin, '/api/sperre-aufheben', {'key': x['key']})[0], 200)
        self.assertEqual(self.post(self.chefin, '/api/sperre-aufheben', {'key': sperre['key']})[0], 400)   # schon frei
        self.assertEqual(self.call('/api/login', {'name': 'Vorsitz', 'password': PASSWORT})[0], 200)
        self.assertTrue({x['name'] for x in data['konten']} >= {'Chefin', 'Vorsitz'})
        self.assertIn('version', data['system'])
        self.assertEqual(self.post(self.chefin, '/api/dateipruefung', {})[1]['ergebnis']['verfuegbar'], False)

    def test_unbekannter_name_nicht_im_protokoll(self):
        for _ in range(zugang.FREE_ATTEMPTS + 1):
            self.call('/api/login', {'name': 'Mein-Geheimes-Passwort-7', 'password': 'x'})
        data = self.get(self.chefin, '/api/sicherheit-verwaltung')[1]
        self.assertNotIn('geheimes', json.dumps(data).lower())
        sperre = next(x for x in data['sperren'] if x['art'] == 'Name')
        self.assertEqual(sperre['wert'], 'unbekannter Name')
        self.assertEqual(self.post(self.chefin, '/api/sperre-aufheben', {'key': sperre['key']})[0], 200)
        self.assertNotIn('geheimes', json.dumps(self.events('')).lower())

    def test_bestaetigung_umfasst_kritische_adressen(self):
        for method, path in app.CONFIRM:
            self.assertIn(path, rollen.POST if method == 'POST' else rollen.GET, path)
        for path in ('/api/settings', '/api/member-delete', '/api/members-import', '/api/rolle-loeschen'):
            self.assertIn(('POST', path), app.CONFIRM)
        for path in ('/api/backup', '/api/pruefpaket.zip', '/api/op-listen.zip', '/api/sepa.xml'):
            self.assertIn(('GET', path), app.CONFIRM)
        who = self.login('Chefin', confirm=False)
        self.assertEqual(self.post(who, '/api/settings', {'smtp_host': 'smtp.fremd.example'})[0], 428)
        self.assertEqual(self.get(who, '/api/pruefpaket.zip?year=2026')[0], 428)

    def test_nur_im_serverbetrieb(self):
        app.MODE = 'mac'
        try:
            self.assertEqual(self.get(self.chefin, '/api/sicherheit')[0], 404)
            self.assertEqual(self.post(self.chefin, '/api/notfall-an', {'grund': 'Test'})[0], 404)
        finally:
            app.MODE = 'server'


if __name__ == '__main__':
    unittest.main()
