"""Tests für keycloak.py gegen einen nachgebildeten Keycloak auf 127.0.0.1 (kein Internet)."""
import base64
import hashlib
import json
import re
import socket
import threading
import time
import unittest
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import keycloak

REALM = 'ju-hamburg'
ISSUER = f'https://srv1.example.ts.net/auth/realms/{REALM}'
CLIENT = 'ju-verwaltung'
SECRET = 'geheim-nur-fuer-tests-123'
RUECKKEHR = 'https://srv1.example.ts.net/api/keycloak/rueckkehr'


def _b64(daten):
    return base64.urlsafe_b64encode(daten).rstrip(b'=').decode('ascii')


def _jwt(claims):
    return '.'.join((_b64(b'{"alg":"RS256"}'), _b64(json.dumps(claims).encode()), _b64(b'signatur')))


class FakeKeycloak:
    """Token-Endpunkt (authorization_code mit PKCE, client_credentials) und die genutzten Admin-Endpunkte."""

    def __init__(self):
        self.codes = {}
        self.admin_tokens = set()
        self.token_anfragen = 0
        self.expires_in = 300
        self.users = {}
        self.logouts = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def antwort(self, status, daten=None, headers=None):
                body = b'' if daten is None else json.dumps(daten).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def eingabe(self):
                return self.rfile.read(int(self.headers.get('Content-Length') or 0))

            def do_POST(self):
                fake.verteilen(self, 'POST')

            def do_GET(self):
                fake.verteilen(self, 'GET')

            def do_PUT(self):
                fake.verteilen(self, 'PUT')

            def do_DELETE(self):
                fake.verteilen(self, 'DELETE')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def schliessen(self):
        self.server.shutdown()
        self.server.server_close()

    def code_ausstellen(self, url, **claims):
        """Wie Keycloak nach erfolgreichem Login: Code zu den Parametern der Anmelde-Adresse."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        code = uuid.uuid4().hex
        jetzt = int(time.time())
        basis = {'iss': ISSUER, 'aud': CLIENT, 'azp': CLIENT, 'sub': 'sub-1', 'preferred_username': 'anna',
                 'exp': jetzt + 300, 'iat': jetzt, 'auth_time': jetzt, 'nonce': q['nonce']}
        self.codes[code] = {'challenge': q['code_challenge'], 'redirect_uri': q['redirect_uri'],
                            'claims': {k: v for k, v in {**basis, **claims}.items() if v is not None}}
        return code

    def verteilen(self, h, methode):
        pfad = urllib.parse.urlsplit(h.path)
        if methode == 'POST' and pfad.path == f'/auth/realms/{REALM}/protocol/openid-connect/token':
            return self.token(h)
        admin = f'/auth/admin/realms/{REALM}'
        if not pfad.path.startswith(admin):
            return h.antwort(404)
        if h.headers.get('Authorization', '')[7:] not in self.admin_tokens:
            return h.antwort(401)
        teile = [urllib.parse.unquote(t) for t in pfad.path[len(admin):].strip('/').split('/')]
        daten = json.loads(h.eingabe() or b'null')
        if teile == ['users'] and methode == 'POST':
            if ' ' in daten['username']:
                return h.antwort(400, {'errorMessage': 'error-invalid-username'})
            if any(u['username'] == daten['username'] for u in self.users.values()):
                return h.antwort(409, {'errorMessage': 'User exists with same username'})
            kennung = str(uuid.uuid4())
            self.users[kennung] = {**daten, 'id': kennung, 'credentials': [
                {'id': uuid.uuid4().hex, 'type': c['type'], 'temporary': c.get('temporary')} for c in daten['credentials']]}
            return h.antwort(201, headers={'Location': f'http://127.0.0.1:{self.port}{admin}/users/{kennung}'})
        if teile == ['users', 'count']:
            return h.antwort(200, len(self.users))
        if teile == ['users'] and methode == 'GET':
            q = urllib.parse.parse_qs(pfad.query)
            assert q.get('exact') == ['true']
            return h.antwort(200, [{'id': u['id'], 'username': u['username']} for u in self.users.values()
                                   if u['username'] == q['username'][0].lower()])
        user = self.users.get(teile[1]) if len(teile) >= 2 else None
        if user is None:
            return h.antwort(404)
        rest = teile[2:]
        if not rest and methode == 'GET':
            return h.antwort(200, {k: v for k, v in user.items() if k != 'credentials'})
        if not rest and methode == 'PUT':
            user.update(daten)
            return h.antwort(204)
        if rest == ['reset-password'] and methode == 'PUT':
            user['credentials'] = [c for c in user['credentials'] if c['type'] != 'password']
            user['credentials'].append({'id': uuid.uuid4().hex, 'type': 'password', 'temporary': daten['temporary']})
            return h.antwort(204)
        if rest == ['credentials'] and methode == 'GET':
            return h.antwort(200, user['credentials'])
        if len(rest) == 2 and rest[0] == 'credentials' and methode == 'DELETE':
            user['credentials'] = [c for c in user['credentials'] if c['id'] != rest[1]]
            return h.antwort(204)
        if rest == ['logout'] and methode == 'POST':
            self.logouts.append(user['id'])
            return h.antwort(204)
        return h.antwort(404)

    def token(self, h):
        form = dict(urllib.parse.parse_qsl(h.eingabe().decode()))
        if form.get('client_id') != CLIENT or form.get('client_secret') != SECRET:
            return h.antwort(401, {'error': 'invalid_client'})
        if form.get('grant_type') == 'client_credentials':
            self.token_anfragen += 1
            token = f'adm-{uuid.uuid4().hex}'
            self.admin_tokens.add(token)
            return h.antwort(200, {'access_token': token, 'expires_in': self.expires_in})
        eintrag = self.codes.pop(form.get('code'), None)
        verifier = form.get('code_verifier', '')
        if (form.get('grant_type') != 'authorization_code' or eintrag is None
                or eintrag['redirect_uri'] != form.get('redirect_uri')
                or _b64(hashlib.sha256(verifier.encode()).digest()) != eintrag['challenge']):
            return h.antwort(400, {'error': 'invalid_grant', 'error_description': 'PKCE verification failed'})
        return h.antwort(200, {'access_token': 'zugang', 'id_token': _jwt(eintrag['claims']), 'token_type': 'Bearer'})


def env(port, **aenderung):
    werte = {'JU_FINANZEN_KEYCLOAK_ISSUER': ISSUER,
             'JU_FINANZEN_KEYCLOAK_INTERN': f'http://127.0.0.1:{port}/auth/realms/{REALM}',
             'JU_FINANZEN_KEYCLOAK_CLIENT': CLIENT, 'JU_FINANZEN_KEYCLOAK_SECRET': SECRET,
             'JU_FINANZEN_KEYCLOAK_RUECKKEHR': RUECKKEHR}
    werte.update({f'JU_FINANZEN_KEYCLOAK_{k}': v for k, v in aenderung.items()})
    return werte


class KonfigurationTests(unittest.TestCase):
    def test_gueltig(self):
        cfg = keycloak.aus_umgebung(env(8180, ISSUER=ISSUER + '/'))
        self.assertEqual(cfg.issuer, ISSUER)
        self.assertEqual(cfg.intern, f'http://127.0.0.1:8180/auth/realms/{REALM}')
        self.assertEqual(cfg.admin_basis, f'http://127.0.0.1:8180/auth/admin/realms/{REALM}')
        self.assertEqual((cfg.client_id, cfg.client_secret, cfg.redirect_uri), (CLIENT, SECRET, RUECKKEHR))
        self.assertEqual(keycloak.aus_umgebung(env(8180, INTERN=f'http://[::1]:8180/auth/realms/{REALM}')).admin_basis,
                         f'http://[::1]:8180/auth/admin/realms/{REALM}')

    def test_secret_nicht_im_repr(self):
        cfg = keycloak.aus_umgebung(env(8180))
        self.assertNotIn(SECRET, repr(cfg))
        self.assertNotIn(SECRET, str(cfg))
        self.assertNotIn(SECRET, repr(keycloak.Verwaltung(cfg)))

    def test_nicht_konfiguriert(self):
        self.assertIsNone(keycloak.aus_umgebung({}))
        self.assertIsNone(keycloak.aus_umgebung({'JU_FINANZEN_KEYCLOAK_ISSUER': '  '}))

    def test_halb_konfiguriert(self):
        with self.assertRaises(ValueError) as fehler:
            keycloak.aus_umgebung(env(8180, CLIENT=''))
        self.assertIn('JU_FINANZEN_KEYCLOAK_CLIENT', str(fehler.exception))
        with self.assertRaises(ValueError) as fehler:
            keycloak.aus_umgebung({'JU_FINANZEN_KEYCLOAK_SECRET': SECRET})
        self.assertNotIn(SECRET, str(fehler.exception))

    def test_unsicher(self):
        for aenderung in ({'ISSUER': ISSUER.replace('https', 'http')}, {'ISSUER': 'https://srv1.example.ts.net/auth'},
                          {'INTERN': f'http://srv1.example.ts.net:8180/auth/realms/{REALM}'},
                          {'INTERN': f'http://10.0.0.5:8180/auth/realms/{REALM}'},
                          {'INTERN': f'ftp://127.0.0.1/auth/realms/{REALM}'},
                          {'INTERN': 'http://127.0.0.1:8180/auth/realms/anderer'},
                          {'RUECKKEHR': 'http://srv1.example.ts.net/api/keycloak/rueckkehr'}):
            with self.subTest(aenderung=aenderung), self.assertRaises(ValueError) as fehler:
                keycloak.aus_umgebung(env(8180, **aenderung))
            self.assertNotIn(SECRET, str(fehler.exception))


class MitFake(unittest.TestCase):
    def setUp(self):
        self.fake = FakeKeycloak()
        self.addCleanup(self.fake.schliessen)
        self.cfg = keycloak.aus_umgebung(env(self.fake.port))


class AnmeldungTests(MitFake):
    def anmelden(self, vorgang_aendern=None, query=None, **claims):
        url, vorgang = keycloak.anmeldung_starten(self.cfg)
        query = query or {'code': [self.fake.code_ausstellen(url, **claims)], 'state': [vorgang['state']]}
        if vorgang_aendern:
            vorgang_aendern(vorgang)
        return keycloak.anmeldung_abschliessen(self.cfg, vorgang, query)

    def assertAbgelehnt(self, **kwargs):
        with self.assertRaises(keycloak.KeycloakFehler) as fehler:
            self.anmelden(**kwargs)
        self.assertNotIn(SECRET, str(fehler.exception))
        return str(fehler.exception)

    def test_anmelde_adresse_mit_pkce(self):
        url, vorgang = keycloak.anmeldung_starten(self.cfg)
        teile = urllib.parse.urlsplit(url)
        self.assertEqual(f'{teile.scheme}://{teile.netloc}{teile.path}', ISSUER + '/protocol/openid-connect/auth')
        q = dict(urllib.parse.parse_qsl(teile.query))
        self.assertEqual({k: q[k] for k in ('response_type', 'scope', 'client_id', 'redirect_uri', 'prompt', 'max_age',
                                             'code_challenge_method')},
                         {'response_type': 'code', 'scope': 'openid', 'client_id': CLIENT, 'redirect_uri': RUECKKEHR,
                          'prompt': 'login', 'max_age': '0', 'code_challenge_method': 'S256'})
        self.assertEqual((q['state'], q['nonce']), (vorgang['state'], vorgang['nonce']))
        self.assertEqual(q['code_challenge'], _b64(hashlib.sha256(vorgang['code_verifier'].encode()).digest()))
        self.assertNotIn(vorgang['code_verifier'], url)
        self.assertTrue(43 <= len(vorgang['code_verifier']) <= 128)
        self.assertNotEqual(keycloak.anmeldung_starten(self.cfg)[1]['state'], vorgang['state'])
        self.assertNotIn('kc_action', q)

    def test_passwort_aendern_nur_als_feste_aktion(self):
        url, _ = keycloak.anmeldung_starten(self.cfg, keycloak.PASSWORT_AKTION)
        self.assertEqual(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))['kc_action'], 'UPDATE_PASSWORD')
        for aktion in ('CONFIGURE_TOTP', 'delete_account', ''):
            with self.subTest(aktion=aktion), self.assertRaises(ValueError):
                keycloak.anmeldung_starten(self.cfg, aktion)

    def test_erfolg(self):
        self.assertEqual(self.anmelden(), {'sub': 'sub-1', 'benutzername': 'anna'})
        self.assertEqual(self.anmelden(amr=['pwd', 'otp'], aud=['account', CLIENT]), {'sub': 'sub-1', 'benutzername': 'anna'})
        self.assertEqual(self.anmelden(azp=None)['sub'], 'sub-1')

    def test_query_als_einfaches_dict(self):
        url, vorgang = keycloak.anmeldung_starten(self.cfg)
        code = self.fake.code_ausstellen(url)
        self.assertEqual(keycloak.anmeldung_abschliessen(self.cfg, vorgang, {'code': code, 'state': vorgang['state']})['sub'],
                         'sub-1')

    def test_falscher_code_verifier(self):
        self.assertIn('abgelaufen', self.assertAbgelehnt(vorgang_aendern=lambda v: v.update(code_verifier='x' * 64)))

    def test_state_falsch_oder_fehlend(self):
        self.assertIn('Sitzung', self.assertAbgelehnt(vorgang_aendern=lambda v: v.update(state='anders')))
        self.assertIn('Sitzung', self.assertAbgelehnt(query={'code': 'abc'}))
        self.assertIn('Sitzung', self.assertAbgelehnt(query={'code': 'abc', 'state': ['a', 'b']}))
        with self.assertRaises(keycloak.KeycloakFehler):
            keycloak.anmeldung_abschliessen(self.cfg, None, {'code': 'abc', 'state': 'abc'})

    def test_abgelaufen(self):
        self.assertIn('zu lange', self.assertAbgelehnt(vorgang_aendern=lambda v: v.update(erstellt=time.time() - 601)))
        self.assertIn('zu lange', self.assertAbgelehnt(vorgang_aendern=lambda v: v.update(erstellt=time.time() + 120)))

    def test_error_parameter(self):
        url, vorgang = keycloak.anmeldung_starten(self.cfg)
        for fehler, erwartet in (('access_denied', 'abgebrochen'), ('server_error', 'abgelehnt')):
            with self.subTest(fehler=fehler), self.assertRaises(keycloak.KeycloakFehler) as ausnahme:
                keycloak.anmeldung_abschliessen(self.cfg, vorgang, {'state': vorgang['state'], 'error': fehler,
                                                                    'error_description': '<script>Böse</script>'})
            self.assertIn(erwartet, str(ausnahme.exception))
            self.assertNotIn('Böse', str(ausnahme.exception))
        with self.assertRaises(keycloak.KeycloakFehler):
            keycloak.anmeldung_abschliessen(self.cfg, vorgang, {'state': vorgang['state']})

    def test_claims_falsch(self):
        jetzt = int(time.time())
        for claims in ({'iss': 'https://anderer.example.ts.net/auth/realms/ju-hamburg'}, {'iss': None},
                       {'aud': 'anderer-client'}, {'aud': ['account']}, {'aud': None}, {'azp': 'anderer-client'},
                       {'exp': jetzt - 1}, {'exp': None}, {'exp': 'morgen'}, {'iat': jetzt + 120}, {'iat': None},
                       {'nonce': 'falsch'}, {'nonce': None}, {'sub': None}, {'sub': ''},
                       {'auth_time': None}, {'auth_time': 'jetzt'}, {'auth_time': jetzt - 5}):
            with self.subTest(claims=claims):
                self.assertIn('ungültig', self.assertAbgelehnt(**claims))
        self.assertEqual(self.anmelden(iat=jetzt + 30)['sub'], 'sub-1')      # Uhren-Toleranz
        self.assertEqual(self.anmelden(auth_time=jetzt - 1)['sub'], 'sub-1')   # ganze Sekunden

    def test_sso_ohne_neue_anmeldung_abgelehnt(self):
        # Browser entfernt prompt=login/max_age: Keycloak bestätigt mit der alten Sitzung (auth_time vor dem Start)
        alt = time.time() - 300
        self.assertIn('ungültig', self.assertAbgelehnt(auth_time=int(alt)))

    def test_code_nur_einmal(self):
        url, vorgang = keycloak.anmeldung_starten(self.cfg)
        query = {'code': self.fake.code_ausstellen(url), 'state': vorgang['state']}
        keycloak.anmeldung_abschliessen(self.cfg, vorgang, query)
        with self.assertRaises(keycloak.KeycloakFehler):
            keycloak.anmeldung_abschliessen(self.cfg, vorgang, query)

    def test_falsches_secret_ohne_secret_in_meldung(self):
        cfg = keycloak.aus_umgebung(env(self.fake.port, SECRET='falsches-geheimnis-456'))
        url, vorgang = keycloak.anmeldung_starten(cfg)
        with self.assertRaises(keycloak.KeycloakFehler) as fehler:
            keycloak.anmeldung_abschliessen(cfg, vorgang, {'code': self.fake.code_ausstellen(url), 'state': vorgang['state']})
        self.assertIn('Client-Secret', str(fehler.exception))
        self.assertNotIn('falsches-geheimnis-456', str(fehler.exception))
        self.assertEqual(fehler.exception.status, 401)

    def test_abmelden_url(self):
        url = keycloak.abmelden_url(self.cfg, id_token_hint='abc', zurueck='https://srv1.example.ts.net/')
        self.assertTrue(url.startswith(ISSUER + '/protocol/openid-connect/logout?'))
        self.assertEqual(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)),
                         {'client_id': CLIENT, 'id_token_hint': 'abc', 'post_logout_redirect_uri': 'https://srv1.example.ts.net/'})
        self.assertEqual(urllib.parse.urlsplit(keycloak.abmelden_url(self.cfg)).query, f'client_id={CLIENT}')


class VerwaltungTests(MitFake):
    def setUp(self):
        super().setUp()
        self.kc = keycloak.Verwaltung(self.cfg)

    def neu(self, name='anna', app_pflicht=True):
        return self.kc.anlegen(name, 'Anna', 'Muster', f'{name}@example.org', True, keycloak.startpasswort(), app_pflicht)

    def otp_einrichten(self, kennung):
        self.fake.users[kennung]['credentials'].append({'id': uuid.uuid4().hex, 'type': 'otp'})

    def test_token_cache(self):
        self.assertTrue(self.kc.erreichbar())
        self.assertTrue(self.kc.erreichbar())
        self.assertEqual(self.fake.token_anfragen, 1)
        self.fake.expires_in = 20                     # kürzer als die Reserve: jedes Mal neu
        kc = keycloak.Verwaltung(self.cfg)
        kc.erreichbar()
        kc.erreichbar()
        self.assertEqual(self.fake.token_anfragen, 3)

    def test_token_cache_threadsicher(self):
        threads = [threading.Thread(target=self.kc.erreichbar) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.fake.token_anfragen, 1)

    def test_widerrufenes_token_wird_erneuert(self):
        self.kc.erreichbar()
        self.fake.admin_tokens.clear()
        self.assertFalse(self.kc.erreichbar())
        self.assertTrue(self.kc.erreichbar())
        self.assertEqual(self.fake.token_anfragen, 2)

    def test_nicht_erreichbar(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        kc = keycloak.Verwaltung(keycloak.aus_umgebung(env(port)))
        self.assertFalse(kc.erreichbar())
        with self.assertRaises(keycloak.KeycloakFehler) as fehler:
            kc.suchen('anna')
        self.assertIn('nicht erreichbar', str(fehler.exception))

    def test_kein_proxy(self):
        with mock.patch.dict('os.environ', {'http_proxy': 'http://127.0.0.1:9', 'HTTP_PROXY': 'http://127.0.0.1:9',
                                            'no_proxy': '', 'NO_PROXY': ''}):
            self.assertTrue(keycloak.Verwaltung(self.cfg).erreichbar())

    def test_nur_loopback(self):
        cfg = keycloak.Einstellungen(ISSUER, f'http://srv1.example.ts.net/auth/realms/{REALM}',
                                     f'http://srv1.example.ts.net/auth/admin/realms/{REALM}', CLIENT, SECRET, RUECKKEHR)
        self.assertFalse(keycloak.Verwaltung(cfg).erreichbar())

    def test_anlegen(self):
        kennung = self.neu()
        user = self.fake.users[kennung]
        self.assertEqual((user['username'], user['firstName'], user['lastName'], user['email'], user['enabled'],
                          user['emailVerified']), ('anna', 'Anna', 'Muster', 'anna@example.org', True, True))
        self.assertEqual(user['requiredActions'], ['UPDATE_PASSWORD', 'CONFIGURE_TOTP'])
        self.assertEqual(user['credentials'][0]['type'], 'password')
        self.assertTrue(user['credentials'][0]['temporary'])
        ohne = self.neu('bert', app_pflicht=False)
        self.assertEqual(self.fake.users[ohne]['requiredActions'], ['UPDATE_PASSWORD'])
        self.assertEqual(self.kc.suchen('Anna'), kennung)
        self.assertIsNone(self.kc.suchen('carla'))

    def test_anlegen_doppelt(self):
        self.neu()
        with self.assertRaises(keycloak.KeycloakFehler) as fehler:
            self.neu()
        self.assertEqual(str(fehler.exception), 'Benutzername oder E-Mail gibt es in Keycloak schon.')
        self.assertEqual(fehler.exception.status, 409)
        with self.assertRaises(keycloak.KeycloakFehler) as fehler:
            self.neu('max muster')
        self.assertEqual((str(fehler.exception), fehler.exception.status), (keycloak.ABGELEHNT, 400))

    def test_aendern_app_pflicht(self):
        kennung = self.neu(app_pflicht=False)
        aktionen = lambda: self.fake.users[kennung]['requiredActions']
        self.kc.aendern(kennung, 'Anna', 'Neu', 'neu@example.org', False, True)
        self.assertEqual(aktionen(), ['UPDATE_PASSWORD', 'CONFIGURE_TOTP'])
        user = self.fake.users[kennung]
        self.assertEqual((user['lastName'], user['email'], user['enabled']), ('Neu', 'neu@example.org', False))
        self.kc.aendern(kennung, 'Anna', 'Neu', 'neu@example.org', True, True)
        self.assertEqual(aktionen(), ['UPDATE_PASSWORD', 'CONFIGURE_TOTP'])   # nicht doppelt
        self.kc.aendern(kennung, 'Anna', 'Neu', 'neu@example.org', True, False)
        self.assertEqual(aktionen(), ['UPDATE_PASSWORD'])
        self.otp_einrichten(kennung)                  # App schon eingerichtet → kein neuer Pflichtschritt
        self.kc.aendern(kennung, 'Anna', 'Neu', 'neu@example.org', True, True)
        self.assertEqual(aktionen(), ['UPDATE_PASSWORD'])

    def test_unbekannte_person(self):
        with self.assertRaises(keycloak.KeycloakFehler) as fehler:
            self.kc.aendern('gibt-es-nicht', 'A', 'B', '', True, False)
        self.assertEqual(fehler.exception.status, 404)
        with self.assertRaises(keycloak.KeycloakFehler):
            self.kc.hat_app('')

    def test_hat_app_und_zuruecksetzen(self):
        kennung = self.neu(app_pflicht=False)
        self.assertFalse(self.kc.hat_app(kennung))
        self.otp_einrichten(kennung)
        self.otp_einrichten(kennung)
        self.assertTrue(self.kc.hat_app(kennung))
        self.kc.app_zuruecksetzen(kennung)
        self.assertFalse(self.kc.hat_app(kennung))
        self.assertEqual([c['type'] for c in self.fake.users[kennung]['credentials']], ['password'])
        self.assertEqual(self.fake.users[kennung]['requiredActions'], ['UPDATE_PASSWORD', 'CONFIGURE_TOTP'])

    def test_startpasswort_setzen_und_abmelden(self):
        kennung = self.neu()
        self.kc.startpasswort_setzen(kennung, keycloak.startpasswort())
        self.assertEqual([(c['type'], c['temporary']) for c in self.fake.users[kennung]['credentials']], [('password', True)])
        self.kc.abmelden(kennung)
        self.assertEqual(self.fake.logouts, [kennung])


class StartpasswortTests(unittest.TestCase):
    def test_format(self):
        muster = re.compile(r'^[A-Za-z2-9]{4}(-[A-Za-z2-9]{4}){3}$')
        gesehen = set()
        for _ in range(300):
            pw = keycloak.startpasswort()
            gesehen.add(pw)
            self.assertRegex(pw, muster)
            self.assertGreaterEqual(len(pw), 12)
            self.assertFalse(set(pw) & set('0O1lI'))
            self.assertTrue(any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw))
        self.assertEqual(len(gesehen), 300)


if __name__ == '__main__':
    unittest.main()
