"""Anmeldung über Keycloak im Programm (ab 0.36) gegen den nachgebildeten Keycloak aus test_keycloak.py – nur erfundene Daten."""
import os
import hashlib
import http.client
import io
import json
import sqlite3
import tempfile
import time
import unittest
import unittest.mock
import urllib.parse
import uuid
from contextlib import redirect_stdout
from datetime import date, timedelta

import app
import keycloak
import rollen
import sicherheit
import test_keycloak   # FakeKeycloak und env – als Modul, sonst liefen dessen Tests hier noch einmal
import test_rollen
import test_server
import verwaltung
import zugang
from core import Store

PASSWORT = test_server.PASSWORT
HOST = 'ju-finanzen.beispiel.ts.net'


class KeycloakAppTests(unittest.TestCase):
    setUpClass = classmethod(test_server.ServerHttpTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerHttpTests.tearDownClass.__func__)
    call = test_server.ServerHttpTests.call

    def setUp(self):
        app.THROTTLE = zugang.Throttle()
        app.SESSIONS.clear()
        app.KC_VORGAENGE.clear()
        if app.DB.exists():
            app.DB.unlink()
        self.fake = test_keycloak.FakeKeycloak()
        self.addCleanup(self.fake.schliessen)
        self.cfg = keycloak.aus_umgebung(test_keycloak.env(self.fake.port))
        app.KEYCLOAK, app.KC_ADMIN = self.cfg, keycloak.Verwaltung(self.cfg)
        self.addCleanup(setattr, app, 'KEYCLOAK', None)
        self.addCleanup(setattr, app, 'KC_ADMIN', None)
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'Chefin', PASSWORT, create=True)   # erste Person: alle Rechte, Notzugang
            sicherheit.notzugang_setzen(store.db, 60, 'Test')                    # wie sudo ju-finanzen notzugang
            self.roles = {r[1]: r[0] for r in store.db.execute('SELECT id,name FROM roles')}
        finally:
            store.db.close()
        self.chefin = self.notzugang('Chefin')

    # ---------------------------------------------------------------- Hilfen

    def raw(self, path, cookies=None, headers=None):
        """GET ohne Weiterleitungen folgen (die führen zu Keycloak): (Status, Location, {Cookie-Name: Wert}, Set-Cookie-Zeilen)."""
        con = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        h = {'Host': HOST, **(headers or {})}
        if cookies:
            h['Cookie'] = '; '.join(f'{k}={v}' for k, v in cookies.items())
        con.request('GET', path, headers=h)
        r = con.getresponse()
        r.read()
        lines = r.msg.get_all('Set-Cookie') or []
        jar = {line.split('=', 1)[0]: line.split('=', 1)[1].split(';')[0] for line in lines}
        con.close()
        return r.status, r.getheader('Location'), jar, lines

    def notzugang(self, name, password=PASSWORT, confirm=True):
        status, body, headers = self.call('/api/login', {'name': name, 'password': password})
        self.assertEqual(status, 200, body)
        who = {'cookie': headers['Set-Cookie'].split(';')[0].split('=', 1)[1], 'csrf': body['csrf']}
        if confirm:
            self.assertEqual(self.post(who, '/api/bestaetigen', {'password': password})[0], 200)
        return who

    def post(self, who, path, data):
        status, body, _ = self.call(path, data, cookie=who['cookie'], csrf=who['csrf'])
        return status, body

    def get(self, who, path):
        status, body, _ = self.call(path, cookie=who['cookie'])
        return status, body

    def anlegen(self, name, *roles, **extra):
        status, body = self.post(self.chefin, '/api/benutzer', {**test_rollen.P(name), 'roles': [self.roles[r] for r in roles],
                                                                 'active': True, 'totp_pflicht': False, **extra})
        self.assertEqual(status, 200, body)
        return body

    def kid(self, name):
        store = Store(app.DB)
        try:
            return store.db.execute('SELECT keycloak_id FROM users WHERE name=?', (name,)).fetchone()[0]
        finally:
            store.db.close()

    def db(self, sql, *args):
        store = Store(app.DB)
        try:
            store.db.execute(sql, args)
            store.db.commit()
        finally:
            store.db.close()

    def starten(self, aktion='', cookies=None):
        status, location, jar, lines = self.raw('/api/keycloak/start' + (f'?aktion={aktion}' if aktion else ''), cookies)
        self.assertEqual(status, 302)
        return location, jar.get('__Host-ju_kc'), lines

    def zurueck(self, location, vid, query=None, cookies=None, headers=None, **claims):
        """Wie Keycloak nach dem Login: Code ausstellen und mit state zur Rückkehr-Adresse."""
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))['state']
        query = query or {'code': self.fake.code_ausstellen(location, **claims), 'state': state}
        jar = {**(cookies or {}), **({'__Host-ju_kc': vid} if vid else {})}
        return self.raw('/api/keycloak/rueckkehr?' + urllib.parse.urlencode(query), jar, headers)

    def kc_login(self, name, **claims):
        location, vid, _ = self.starten()
        status, ziel, jar, _ = self.zurueck(location, vid, sub=self.kid(name), **claims)
        self.assertEqual((status, ziel), (302, '/'))
        return {'cookie': jar['__Host-ju_session'], 'csrf': self.get({'cookie': jar['__Host-ju_session']}, '/api/status')[1]['csrf']}

    def ereignisse(self, art):
        store = Store(app.DB)
        try:
            return [dict(r) for r in store.db.execute('SELECT person,detail FROM sicherheit_ereignisse WHERE art=? ORDER BY id', (art,))]
        finally:
            store.db.close()

    # ---------------------------------------------------------------- Anmeldung

    def test_start_und_rueckkehr(self):
        self.anlegen('anna.test', 'Landesvorsitzender')
        location, vid, lines = self.starten()
        self.assertTrue(location.startswith(self.cfg.issuer + '/protocol/openid-connect/auth?'))
        kc_cookie = next(x for x in lines if x.startswith('__Host-ju_kc='))
        for flag in ('HttpOnly', 'SameSite=Lax', 'Path=/', 'Secure', 'Max-Age=600'):
            self.assertIn(flag, kc_cookie)
        self.assertNotIn(app.KC_VORGAENGE[vid]['code_verifier'], location)              # PKCE-Geheimnis bleibt auf dem Server
        status, ziel, jar, lines = self.zurueck(location, vid, sub=self.kid('anna.test'))
        self.assertEqual((status, ziel), (302, '/'))
        self.assertEqual(jar['__Host-ju_kc'], '')                                        # Vorgangs-Cookie gelöscht
        self.assertIn('SameSite=Strict', next(x for x in lines if x.startswith('__Host-ju_session=')))
        status, body = self.get({'cookie': jar['__Host-ju_session']}, '/api/status')
        self.assertEqual((body['user'], body['auth'], body['passwort_lokal']), ('anna.test', 'keycloak', False))
        self.assertEqual(self.ereignisse('anmeldung')[-1]['person'], 'anna.test')
        self.assertEqual(app.KC_VORGAENGE, {})
        # Passwort ändern: nur die feste Aktion UPDATE_PASSWORD geht an Keycloak
        location, _, _ = self.starten('passwort')
        self.assertEqual(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))['kc_action'], 'UPDATE_PASSWORD')
        self.assertEqual(self.raw('/api/keycloak/start?aktion=CONFIGURE_TOTP')[1], '/?anmeldung=fehler')

    def test_vorgang_falsch_oder_wiederverwendet(self):
        self.anlegen('anna.test')
        location, vid, _ = self.starten()
        status, ziel, _, _ = self.zurueck(location, vid, query={'code': 'x', 'state': 'falsch'})
        self.assertEqual(ziel, '/?anmeldung=fehler')
        self.assertEqual(self.zurueck(location, vid, sub=self.kid('anna.test'))[1], '/?anmeldung=abgelaufen')   # einmal verbraucht
        self.assertEqual(self.zurueck(location, None, sub=self.kid('anna.test'))[1], '/?anmeldung=abgelaufen')  # ohne Cookie
        location, vid, _ = self.starten()
        other, vid2, _ = self.starten()
        self.assertEqual(self.zurueck(location, vid2, sub=self.kid('anna.test'))[1], '/?anmeldung=fehler')      # fremder Vorgang
        self.assertEqual(self.zurueck(location, vid, sub=self.kid('anna.test'))[1], '/')

    def test_fehler_ohne_details_und_throttle(self):
        location, vid, _ = self.starten()
        out = io.StringIO()
        with redirect_stdout(out):
            ziel = self.zurueck(location, vid, query={'error': 'access_denied', 'error_description': '<b>x</b>', 'state':
                                dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))['state']})[1]
        self.assertEqual(ziel, '/?anmeldung=abgebrochen')
        self.assertNotIn('<b>', out.getvalue())
        for _ in range(zugang.FREE_ATTEMPTS):
            location, vid, _ = self.starten()
            self.zurueck(location, vid, query={'code': 'x', 'state': 'falsch'})
        location, vid, _ = self.starten()
        self.assertEqual(self.zurueck(location, vid, sub='egal')[1], '/?anmeldung=gesperrt')

    def test_herkunft_und_host(self):
        self.anlegen('anna.test')
        location, vid, _ = self.starten()
        self.assertEqual(self.zurueck(location, vid, headers={'Sec-Fetch-Site': 'cross-site'}, sub=self.kid('anna.test'))[0], 403)
        self.assertEqual(self.raw('/api/keycloak/start', headers={'Sec-Fetch-Site': 'same-site'})[0], 403)
        self.assertEqual(self.raw('/api/keycloak/start', headers={'Host': 'fremd.example.org'})[0], 403)
        self.assertEqual(self.zurueck(location, vid, headers={'Sec-Fetch-Site': 'same-origin'}, sub=self.kid('anna.test'))[1], '/')

    def test_nur_ueber_keycloak_id_nie_ueber_den_namen(self):
        self.anlegen('anna.test')
        self.db("UPDATE users SET keycloak_id=NULL WHERE name='anna.test'")
        location, vid, _ = self.starten()
        status, ziel, jar, _ = self.zurueck(location, vid, sub='unbekannt-1', preferred_username='anna.test')
        self.assertEqual((ziel, jar.get('__Host-ju_session')), ('/?anmeldung=kein-zugang', None))
        fehl = self.ereignisse('anmeldung_fehl')[-1]
        self.assertEqual((fehl['person'], json.loads(fehl['detail'])), ('', {'keycloak': 'unbekannt'}))

    def test_abgelaufene_oder_inaktive_person(self):
        self.anlegen('anna.test')
        self.db("UPDATE users SET gueltig_bis=? WHERE name='anna.test'", (date.today() - timedelta(days=1)).isoformat())
        location, vid, _ = self.starten()
        self.assertEqual(self.zurueck(location, vid, sub=self.kid('anna.test'))[1], '/?anmeldung=kein-zugang')
        self.db("UPDATE users SET gueltig_bis=NULL,active=0 WHERE name='anna.test'")
        location, vid, _ = self.starten()
        self.assertEqual(self.zurueck(location, vid, sub=self.kid('anna.test'))[1], '/?anmeldung=kein-zugang')
        self.assertEqual(json.loads(self.ereignisse('anmeldung_fehl')[-1]['detail']), {'keycloak': 'inaktiv'})
        konten = {k['name']: k for k in self.konten()}
        self.assertFalse(konten['anna.test']['aktiv'])

    def konten(self):
        store = Store(app.DB)
        try:
            return sicherheit.konten(store.db)
        finally:
            store.db.close()

    def test_sicherheit_zeigt_abgelaufene_als_inaktiv(self):
        self.anlegen('anna.test')
        self.db("UPDATE users SET gueltig_bis='2020-01-31' WHERE name='anna.test'")
        konten = {k['name']: k for k in self.konten()}
        self.assertFalse(konten['anna.test']['aktiv'])
        self.assertFalse(konten['anna.test']['ungenutzt'])
        self.assertTrue(konten['Chefin']['aktiv'])

    def test_app_pflicht_ohne_app_wird_abgelehnt(self):
        self.anlegen('anna.test', totp_pflicht=True)
        kid = self.kid('anna.test')
        self.assertIn('CONFIGURE_TOTP', self.fake.users[kid]['requiredActions'])
        location, vid, _ = self.starten()
        self.assertEqual(self.zurueck(location, vid, sub=kid)[1], '/?anmeldung=app')
        self.fake.users[kid]['credentials'].append({'id': uuid.uuid4().hex, 'type': 'otp'})
        location, vid, _ = self.starten()
        self.assertEqual(self.zurueck(location, vid, sub=kid)[1], '/')

    def test_sso_ohne_neue_anmeldung_abgelehnt(self):
        # Browser ohne prompt=login/max_age: Keycloak bestätigt mit der alten Sitzung – auth_time liegt vor dem Start
        self.anlegen('anna.test', 'Administrator')
        alt = int(time.time()) - 120
        location, vid, _ = self.starten()
        self.assertEqual(self.zurueck(location, vid, sub=self.kid('anna.test'), auth_time=alt)[1], '/?anmeldung=fehler')
        anna = self.kc_login('anna.test')
        jar = {'__Host-ju_session': anna['cookie']}
        location, vid, _ = self.starten('bestaetigen', jar)
        self.assertEqual(self.zurueck(location, vid, cookies=jar, sub=self.kid('anna.test'), auth_time=alt)[1], '/?anmeldung=fehler')
        self.assertEqual(self.post(anna, '/api/benutzer-startpasswort', {'id': self.uid('Chefin')})[0], 428)   # nicht bestätigt

    def test_vorgaenge_je_geraet_und_obergrenze(self):
        self.anlegen('anna.test')

        def start(ip):
            status, location, jar, _ = self.raw('/api/keycloak/start', headers={'X-Forwarded-For': ip})
            return location, jar.get('__Host-ju_kc')
        opfer = start('100.64.0.2')
        viele = [start('100.64.0.9') for _ in range(app.KC_JE_GERAET + 1)]   # ein Gerät verdrängt nur eigene Vorgänge
        self.assertEqual(sum(v['geraet'] == '100.64.0.9' for v in app.KC_VORGAENGE.values()), app.KC_JE_GERAET)
        self.assertEqual(self.zurueck(*viele[0], sub=self.kid('anna.test'))[1], '/?anmeldung=abgelaufen')
        for i in range(app.KC_MAX_VORGAENGE - len(app.KC_VORGAENGE)):          # viele Geräte: Speicher voll
            app.KC_VORGAENGE[f'fremd-{i}'] = {'erstellt': time.time(), 'geraet': f'100.65.0.{i}'}
        status, location, jar, _ = self.raw('/api/keycloak/start', headers={'X-Forwarded-For': '100.64.0.3'})
        self.assertEqual((status, location, jar), (302, '/?anmeldung=ueberlastet', {}))
        self.assertEqual(len(app.KC_VORGAENGE), app.KC_MAX_VORGAENGE)
        self.assertEqual(self.zurueck(*opfer, sub=self.kid('anna.test'))[1], '/')   # fremde Vorgänge bleiben

    def test_passwort_geaendert_beendet_andere_sitzungen(self):
        self.anlegen('anna.test')
        alt = self.kc_login('anna.test')

        def passwort(status_text):
            location, vid, _ = self.starten('passwort')
            state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))['state']
            query = {'code': self.fake.code_ausstellen(location, sub=self.kid('anna.test')), 'state': state, 'kc_action_status': status_text}
            status, ziel, jar, _ = self.zurueck(location, vid, query=query)
            self.assertEqual(ziel, '/')
            return {'cookie': jar['__Host-ju_session']}
        neu = passwort('cancelled')
        self.assertEqual(self.get(alt, '/api/status')[1]['user'], 'anna.test')        # abgebrochen: nichts beendet
        neu = passwort('success')
        self.assertIsNone(self.get(alt, '/api/status')[1]['user'])
        self.assertEqual(self.get(neu, '/api/status')[1]['user'], 'anna.test')        # die eigene, neue Sitzung bleibt

    def test_sitzung_beenden_meldet_auch_bei_keycloak_ab(self):
        self.anlegen('anna.test')
        anna = self.kc_login('anna.test')
        sid = hashlib.sha256(anna['cookie'].encode()).hexdigest()[:16]
        self.assertEqual(self.post(self.chefin, '/api/sitzung-beenden', {'id': sid})[0], 200)
        self.assertEqual(self.fake.logouts, [self.kid('anna.test')])
        self.assertIsNone(self.get(anna, '/api/status')[1]['user'])

    def test_abgleichen_nur_mit_angaben_und_passendem_namen(self):
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'Max Muster', PASSWORT, create=True)   # alter Name mit Leerzeichen
            zugang.set_password(store.db, 'ohne.angaben', PASSWORT, create=True)
        finally:
            store.db.close()
        self.db("UPDATE users SET vorname='Max',nachname='Muster',email='max.muster@example.org' WHERE name='Max Muster'")
        with redirect_stdout(io.StringIO()):
            body = self.post(self.chefin, '/api/benutzer-abgleichen', {'id': self.uid('ohne.angaben')})[1]
            self.assertIn('Vorname, Nachname und E-Mail', body['error'])
            body = self.post(self.chefin, '/api/benutzer-abgleichen', {'id': self.uid('Max Muster')})[1]
        self.assertIn('sudo ju-finanzen keycloak-verbinden "Max Muster" --kc-name', body['error'])
        self.assertEqual(self.fake.users, {})

    # ---------------------------------------------------------------- Notzugang, Passwort, Abmelden

    def test_notzugang_nur_nach_freischaltung(self):
        args = ['--data', str(app.DATA), '--backups', str(app.BACKUPS)]

        def befehl(*argv):
            with redirect_stdout(io.StringIO()) as out, unittest.mock.patch.dict(os.environ, {'JU_FINANZEN_ENV_DATEI': '/nicht/da'}):
                try:
                    return verwaltung.main([*args, 'notzugang', *argv]), out.getvalue()
                except SystemExit as e:
                    return e.code, out.getvalue()
        login = lambda: self.call('/api/login', {'name': 'Chefin', 'password': PASSWORT})[:2]
        self.assertEqual(befehl('--aus'), (0, 'Notzugang geschlossen. Anmeldung nur noch über Keycloak.\n'))
        self.assertEqual(login(), (401, {'error': 'Benutzername oder Passwort ist falsch.'}))   # wie ein falsches Passwort
        self.assertFalse(self.get(self.chefin, '/api/status')[1]['passwort_lokal'])   # bestätigen nur noch über Keycloak
        for falsch in ('0', '241', '-5'):
            with self.subTest(minuten=falsch):
                self.assertIn('1 bis 240 Minuten', befehl('--minuten', falsch)[0])
        code, out = befehl('--minuten', '5')
        self.assertEqual(code, 0)
        self.assertIn('Notzugang freigeschaltet bis', out)
        self.assertEqual(login()[0], 200)
        lage = self.get(self.chefin, '/api/sicherheit')[1]['lagebild']
        self.assertEqual(lage['notzugang']['von'], 'Serververwaltung')
        self.db("UPDATE sicherheit_zustand SET value=json_set(value,'$.bis','2000-01-01T00:00:00') WHERE key='notzugang'")   # abgelaufen
        self.assertEqual(login()[0], 401)
        self.assertIsNone(self.get(self.chefin, '/api/sicherheit')[1]['lagebild']['notzugang'])
        self.assertEqual([e['person'] for e in self.ereignisse('notzugang_auf')], ['Test', 'Serververwaltung'])
        self.assertEqual(len(self.ereignisse('notzugang_zu')), 1)

    def test_notzugang_nur_fuer_die_verwaltung(self):
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'kasse.test', PASSWORT, create=True)          # Passwort, aber ohne Verwaltungsrecht
        finally:
            store.db.close()
        status, body, _ = self.call('/api/login', {'name': 'kasse.test', 'password': PASSWORT})
        self.assertEqual((status, body['error']), (401, 'Benutzername oder Passwort ist falsch.'))
        self.assertEqual(self.call('/api/login', {'name': 'Chefin', 'password': 'falsch-falsch-falsch'})[1]['error'], body['error'])
        self.assertEqual(self.ereignisse('notzugang')[-1]['person'], 'Chefin')            # Anmeldung der Chefin in setUp
        self.assertTrue(self.get(self.chefin, '/api/status')[1]['passwort_lokal'])
        app.KEYCLOAK = None                                                              # Passwortbetrieb: wie bisher
        self.assertEqual(self.call('/api/login', {'name': 'kasse.test', 'password': PASSWORT})[0], 200)

    def test_passwort_aendern_und_bestaetigen_nur_mit_notzugang(self):
        self.anlegen('anna.test', 'Administrator')
        anna = self.kc_login('anna.test')
        neu = {'current': PASSWORT, 'new': PASSWORT + '-neu', 'repeat': PASSWORT + '-neu'}
        status, body = self.post(anna, '/api/password', neu)
        self.assertEqual(status, 400)
        self.assertIn('Keycloak', body['error'])
        self.assertEqual(self.post(anna, '/api/bestaetigen', {'password': PASSWORT})[0], 400)
        self.assertEqual(self.post(self.chefin, '/api/password', neu)[0], 200)            # Notzugang behält sein Passwort

    def test_bestaetigen_durch_erneute_anmeldung(self):
        self.anlegen('anna.test', 'Administrator')
        self.anlegen('bert.test')
        anna = self.kc_login('anna.test')
        jar = {'__Host-ju_session': anna['cookie']}
        self.assertEqual(self.post(anna, '/api/benutzer-startpasswort', {'id': 0})[0], 428)
        location, vid, _ = self.starten('bestaetigen', jar)
        self.assertEqual(self.zurueck(location, vid, cookies=jar, sub=self.kid('bert.test'))[1], '/?anmeldung=fehler')   # andere Person
        location, vid, _ = self.starten('bestaetigen', jar)
        self.assertEqual(self.zurueck(location, vid, cookies=jar, sub=self.kid('anna.test'))[1], '/?bestaetigt=1')
        self.assertEqual(self.post(anna, '/api/benutzer-startpasswort', {'id': self.uid('bert.test')})[0], 200)
        self.assertEqual(self.raw('/api/keycloak/start?aktion=bestaetigen')[1], '/?anmeldung=abgelaufen')   # ohne Sitzung

    def uid(self, name):
        store = Store(app.DB)
        try:
            return store.db.execute('SELECT id FROM users WHERE name=?', (name,)).fetchone()[0]
        finally:
            store.db.close()

    def test_abmelden_beendet_auch_keycloak(self):
        self.anlegen('anna.test')
        anna = self.kc_login('anna.test')
        self.assertEqual(self.post(anna, '/api/logout', {})[0], 200)
        self.assertEqual(self.fake.logouts, [self.kid('anna.test')])
        self.assertEqual(self.get(anna, '/api/data?year=2026')[0], 401)
        anna = self.kc_login('anna.test')
        app.KC_ADMIN = keycloak.Verwaltung(keycloak.aus_umgebung(test_keycloak.env(1)))   # Keycloak weg: Abmelden klappt trotzdem
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.post(anna, '/api/logout', {})[0], 200)
        self.assertEqual(self.get(anna, '/api/data?year=2026')[0], 401)

    # ---------------------------------------------------------------- Personen anlegen und pflegen

    def test_anlegen_gibt_startpasswort_genau_einmal_zurueck(self):
        out = io.StringIO()
        with redirect_stdout(out):
            body = self.anlegen('anna.test', 'Landesvorsitzender')
        pw = body['startpasswort']
        self.assertRegex(pw, r'^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$')
        kid = self.kid('anna.test')
        self.assertEqual(self.fake.users[kid]['username'], 'anna.test')
        self.assertEqual(self.fake.users[kid]['requiredActions'], ['UPDATE_PASSWORD'])
        self.assertNotIn(pw, out.getvalue())                                             # nicht im Protokoll des Dienstes
        con = sqlite3.connect(app.DB)
        try:
            self.assertNotIn(pw, '\n'.join(con.iterdump()))                              # nirgends in der Datenbank (Audit, Ereignisse …)
            self.assertEqual(con.execute("SELECT password FROM users WHERE name='anna.test'").fetchone()[0], '')
        finally:
            con.close()
        overview = self.get(self.chefin, '/api/benutzer')[1]
        self.assertNotIn(pw, json.dumps(overview))
        self.assertTrue(next(u for u in overview['users'] if u['name'] == 'anna.test')['keycloak_verbunden'])
        self.assertIn('Mit Keycloak verbunden', [a['action'] for a in overview['audit']])
        # Ändern: kein neues Startpasswort, Angaben gehen nach Keycloak; lokales Passwort wird abgelehnt
        body = self.anlegen('anna.test', 'Landesvorsitzender', id=self.uid('anna.test'), vorname='Anne')
        self.assertNotIn('startpasswort', body)
        self.assertEqual(self.fake.users[kid]['firstName'], 'Anne')
        status, body = self.post(self.chefin, '/api/benutzer', {**test_rollen.P('neu.test'), 'roles': [], 'password': PASSWORT})
        self.assertEqual(status, 400)
        self.assertIn('keine Passwörter', body['error'])

    def test_deaktivieren_meldet_ueberall_ab(self):
        self.anlegen('anna.test', 'Landesvorsitzender')
        anna = self.kc_login('anna.test')
        self.anlegen('anna.test', 'Landesvorsitzender', id=self.uid('anna.test'), active=False)
        self.assertFalse(self.fake.users[self.kid('anna.test')]['enabled'])
        self.assertIn(self.kid('anna.test'), self.fake.logouts)
        self.assertEqual(self.get(anna, '/api/data?year=2026')[0], 401)

    def test_keycloak_ausfall_beim_anlegen(self):
        app.KC_ADMIN = keycloak.Verwaltung(keycloak.aus_umgebung(test_keycloak.env(1)))   # Port 1: nicht erreichbar
        with redirect_stdout(io.StringIO()):
            body = self.anlegen('anna.test')
        self.assertIn('nicht erreichbar', body['keycloak_fehler'])
        self.assertNotIn('startpasswort', body)
        overview = self.get(self.chefin, '/api/benutzer')[1]
        self.assertFalse(next(u for u in overview['users'] if u['name'] == 'anna.test')['keycloak_verbunden'])   # gespeichert, nicht verbunden
        with redirect_stdout(io.StringIO()):
            status, body = self.post(self.chefin, '/api/benutzer-abgleichen', {'id': self.uid('anna.test')})
        self.assertEqual(status, 502)
        app.KC_ADMIN = keycloak.Verwaltung(self.cfg)
        status, body = self.post(self.chefin, '/api/benutzer-abgleichen', {'id': self.uid('anna.test')})
        self.assertEqual(status, 200, body)
        self.assertIn('startpasswort', body)
        self.assertEqual(self.post(self.chefin, '/api/benutzer-abgleichen', {'id': self.uid('anna.test')})[1], {'ok': True})

    def test_keycloak_wege_brauchen_bestaetigung_recht_und_keycloak(self):
        self.anlegen('anna.test', 'Landesvorsitzender', totp_pflicht=True)
        kid = self.kid('anna.test')
        self.fake.users[kid]['credentials'].append({'id': uuid.uuid4().hex, 'type': 'otp'})
        anna = self.kc_login('anna.test')
        uid = self.uid('anna.test')
        frisch = self.notzugang('Chefin', confirm=False)
        for path in ('/api/benutzer-abgleichen', '/api/benutzer-startpasswort', '/api/benutzer-app-zuruecksetzen'):
            self.assertEqual(self.post(frisch, path, {'id': uid})[0], 428, path)
            self.assertEqual(self.post(anna, path, {'id': uid})[0], 403, path)            # ohne „Verwaltung schreiben“
        status, body = self.post(self.chefin, '/api/benutzer-startpasswort', {'id': uid})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.fake.users[kid]['credentials'][-1]['temporary'])
        self.assertEqual(self.get(anna, '/api/data?year=2026')[0], 401)                  # überall abgemeldet
        self.assertIn(kid, self.fake.logouts)
        audit = json.dumps(self.get(self.chefin, '/api/benutzer')[1]['audit'])
        self.assertIn('Startpasswort neu vergeben', audit)
        self.assertNotIn(body['startpasswort'], audit)
        self.assertEqual(self.post(self.chefin, '/api/benutzer-app-zuruecksetzen', {'id': uid})[0], 200)
        self.assertEqual([c for c in self.fake.users[kid]['credentials'] if c['type'] == 'otp'], [])
        self.assertIn('CONFIGURE_TOTP', self.fake.users[kid]['requiredActions'])
        # eigene Person: Bestätigung wie bei anderen, die eigene Sitzung bleibt
        self.db("UPDATE users SET keycloak_id=? WHERE name='Chefin'", 'chefin-kc')
        self.fake.users['chefin-kc'] = {'id': 'chefin-kc', 'username': 'chefin', 'requiredActions': [], 'credentials': []}
        self.assertEqual(self.post(self.chefin, '/api/benutzer-startpasswort', {'id': self.uid('Chefin')})[0], 200)
        self.assertEqual(self.get(self.chefin, '/api/data?year=2026')[0], 200)
        app.KEYCLOAK = None                                                              # Passwortbetrieb: diese Wege gibt es nicht
        for path in ('/api/benutzer-abgleichen', '/api/benutzer-startpasswort', '/api/benutzer-app-zuruecksetzen'):
            self.assertEqual(self.post(self.chefin, path, {'id': uid})[0], 404, path)
        self.assertEqual(self.raw('/api/keycloak/start')[0], 404)

    def test_letzte_verwaltung_bleibt_geschuetzt(self):
        """Der Notzugang (Verwaltung mit lokalem Passwort) lässt sich auch im Keycloak-Betrieb nicht entfernen."""
        me = next(u for u in self.get(self.chefin, '/api/benutzer')[1]['users'] if u['name'] == 'Chefin')
        status, body = self.post(self.chefin, '/api/benutzer', {**test_rollen.P('Chefin'), 'id': me['id'], 'roles': [], 'active': True})
        self.assertEqual(status, 400)
        self.assertIn('Mindestens eine', body['error'])

    # ---------------------------------------------------------------- Start und Verwaltungsbefehl

    def test_start_nur_mit_vollstaendiger_einrichtung(self):
        saved = app.ADDRESSES
        try:
            app.ADDRESSES = ['https://srv1.example.ts.net']
            self.assertIsNone(app.keycloak_from_env({}))
            self.assertIsNone(app.keycloak_from_env({'JU_FINANZEN_ANMELDUNG': 'passwort', **test_keycloak.env(8180)}))
            self.assertEqual(app.keycloak_from_env({'JU_FINANZEN_ANMELDUNG': 'keycloak', **test_keycloak.env(8180)}).client_id, test_keycloak.CLIENT)
            for env in ({'JU_FINANZEN_ANMELDUNG': 'keycloak'}, {'JU_FINANZEN_ANMELDUNG': 'irgendwas'},
                        {'JU_FINANZEN_ANMELDUNG': 'keycloak', **test_keycloak.env(8180, CLIENT='')},
                        {'JU_FINANZEN_ANMELDUNG': 'keycloak', **test_keycloak.env(8180, RUECKKEHR='https://srv1.example.ts.net/anders')},
                        {'JU_FINANZEN_ANMELDUNG': 'keycloak', **test_keycloak.env(8180, ISSUER='https://fremd.example.ts.net/auth/realms/ju-hamburg')}):
                with self.subTest(env=env), self.assertRaises(ValueError) as e:
                    app.keycloak_from_env(env)
                self.assertNotIn(test_keycloak.SECRET, str(e.exception))
        finally:
            app.ADDRESSES = saved
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out, \
                unittest.mock.patch.dict(os.environ, {'JU_FINANZEN_ANMELDUNG': 'keycloak'}):
            for k in [k for k in os.environ if k.startswith('JU_FINANZEN_KEYCLOAK_')]:
                del os.environ[k]
            saved = (app.DATA, app.DB, app.BACKUPS, app.ADDRESSES, app.THROTTLE, app.KEYCLOAK, app.KC_ADMIN)
            try:
                self.assertEqual(app.main(['--modus', 'server', '--adresse', test_server.ADRESSE, '--data', f'{tmp}/D', '--no-browser']), 5)
            finally:
                app.DATA, app.DB, app.BACKUPS, app.ADDRESSES, app.THROTTLE, app.KEYCLOAK, app.KC_ADMIN = saved
        self.assertIn('nicht eingerichtet', out.getvalue())

    def test_verwaltungsbefehl_keycloak_verbinden(self):
        store = Store(app.DB)
        try:
            for name in ('alt.test', 'doppelt.test', 'Max Muster', 'ohne.angaben'):
                zugang.set_password(store.db, name, PASSWORT, create=True)
        finally:
            store.db.close()
        self.db("UPDATE users SET vorname='Test',nachname='Person',email=replace(name,' ','')||'@example.org' WHERE name!='ohne.angaben'")
        self.fake.users['vorhanden'] = {'id': 'vorhanden', 'username': 'doppelt.test', 'requiredActions': [], 'credentials': []}
        args = ['--data', str(app.DATA), '--backups', str(app.BACKUPS)]

        def run(*argv, umgebung=None):
            out = io.StringIO()
            with unittest.mock.patch.dict(os.environ, umgebung or {**test_keycloak.env(self.fake.port), 'JU_FINANZEN_ENV_DATEI': '/nicht/da'}), \
                    unittest.mock.patch('sys.stdin', io.StringIO('')), redirect_stdout(out):
                try:
                    return verwaltung.main([*args, *argv]), out.getvalue()
                except SystemExit as e:
                    return e.code, out.getvalue()

        code, out = run('keycloak-verbinden', 'alt.test')
        self.assertEqual(code, 0, out)
        pw = out.split('wird nicht gespeichert): ')[1].split()[0]
        kid = self.kid('alt.test')
        self.assertEqual(self.fake.users[kid]['username'], 'alt.test')
        con = sqlite3.connect(app.DB)
        try:
            self.assertNotIn(pw, '\n'.join(con.iterdump()))
        finally:
            con.close()
        self.assertIn('schon mit Keycloak verbunden', run('keycloak-verbinden', 'alt.test')[0])
        code, out = run('keycloak-verbinden', 'doppelt.test')                            # nicht interaktiv, ohne --ja
        self.assertIn('Nichts geändert', code)
        self.assertIsNone(self.kid('doppelt.test'))
        code, out = run('keycloak-verbinden', 'doppelt.test', '--ja')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.kid('doppelt.test'), 'vorhanden')
        self.assertNotIn('Startpasswort', out)
        self.assertIn('fehlen Vorname, Nachname oder E-Mail', run('keycloak-verbinden', 'ohne.angaben')[0])
        code, _ = run('keycloak-verbinden', 'Max Muster')
        self.assertIn('--kc-name max.muster', code)                                    # Vorschlag aus dem Namen
        self.assertIn('--kc-name max.mueller', run('keycloak-verbinden', 'Max Muster', '--kc-name', 'Max Müller')[0])
        self.assertIn('--kc-name erika.muster', run('keycloak-verbinden', 'Max Muster', '--kc-name', 'José')[0])
        self.assertIsNone(self.kid('Max Muster'))
        # Einstellungen nur aus der Datei des Dienstes (wie über sudo ju-finanzen: keine Secrets als Argumente)
        with tempfile.NamedTemporaryFile('w', suffix='.env', delete=False) as datei:
            datei.write('# Kommentar\nJU_FINANZEN_PORT=8769\nJU_FINANZEN_KEYCLOAK_SECRET=alt\n'
                        + ''.join(f'{k}={v}\n' for k, v in test_keycloak.env(self.fake.port).items()))
        self.addCleanup(os.unlink, datei.name)
        ohne = {k: v for k, v in os.environ.items() if not k.startswith('JU_FINANZEN_KEYCLOAK_')}
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            code, out = run('keycloak-verbinden', 'Max Muster', '--kc-name', 'Max.Muster', umgebung={**ohne, 'JU_FINANZEN_ENV_DATEI': datei.name})
        self.assertEqual(code, 0, out)
        self.assertIn('Anmeldename: max.muster', out)
        self.assertEqual(self.fake.users[self.kid('Max Muster')]['username'], 'max.muster')
        self.assertIn('nicht eingerichtet', self._ohne_env(args))

    def _ohne_env(self, args):
        with unittest.mock.patch.dict(os.environ, {'JU_FINANZEN_ENV_DATEI': '/nicht/da'}, clear=False):
            for k in [k for k in os.environ if k.startswith('JU_FINANZEN_KEYCLOAK_')]:
                del os.environ[k]
            try:
                verwaltung.main([*args, 'keycloak-verbinden', 'alt.test'])
            except SystemExit as e:
                return e.code
        return ''


if __name__ == '__main__':
    unittest.main()
