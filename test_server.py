"""Serverbetrieb (Ubuntu hinter Tailscale): Anmeldung mit Passwort, Adressen, Sitzungen, Verwaltung – nur erfundene Daten."""
import os
os.environ['JU_FINANZEN_OHNE_SCHLUESSELBUND'] = '1'
import io
import json
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

import app
import geheimnis
import verwaltung
import zugang
from core import Store

PASSWORT = 'Testpasswort-2026!'
ADRESSE = 'https://ju-finanzen.beispiel.ts.net'


class ZugangTests(unittest.TestCase):
    def test_hash_and_verify(self):
        h = zugang.hash_password(PASSWORT)
        self.assertTrue(h.startswith('scrypt$') and zugang.is_set(h) and PASSWORT not in h)
        self.assertTrue(zugang.verify(PASSWORT, h))
        self.assertFalse(zugang.verify(PASSWORT + 'x', h))
        self.assertNotEqual(h, zugang.hash_password(PASSWORT))                        # eigenes Salz je Hash
        for stored in ('', 'pbkdf2:sha256:abc', 'scrypt$1$1$1$00$00', 'scrypt$x$8$1$aa$bb', None):
            self.assertFalse(zugang.is_set(stored) and zugang.verify(PASSWORT, stored), stored)
        self.assertFalse(zugang.verify('', h))
        self.assertFalse(zugang.verify(None, h))

    def test_rules_for_new_passwords(self):
        for bad in ('kurz', 'a' * 20, 'x' * 300, 'gutes-passwort\n12'):
            with self.assertRaises(ValueError, msg=bad):
                zugang.hash_password(bad)

    def test_throttle_grows_and_resets(self):
        now = [1000.0]
        t = zugang.Throttle(clock=lambda: now[0])
        for _ in range(zugang.FREE_ATTEMPTS):
            self.assertEqual(t.wait(), 0)
            t.failed()
        self.assertEqual(t.wait(), 30)
        now[0] += 31
        self.assertEqual(t.wait(), 0)
        t.failed()
        self.assertEqual(t.wait(), 60)                                                # wächst
        for _ in range(10):
            t.failed()
        self.assertEqual(t.wait(), zugang.MAX_WAIT)                                    # höchstens 15 Minuten
        now[0] += zugang.WINDOW + 1
        self.assertEqual(t.wait(), 0)                                                  # alte Fehlversuche verfallen
        t.failed()
        t.succeeded()
        self.assertEqual(t.wait(), 0)

    def test_set_password_and_login_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 't.db')
            store.db.execute("INSERT INTO users(name,password) VALUES('Lennert-Gust','')")   # Altbestand Mac (ohne Passwort)
            store.db.commit()
            self.assertIsNone(zugang.check_login(store.db, 'Lennert-Gust', ''))
            self.assertIsNone(zugang.check_login(store.db, 'Lennert-Gust', 'irgendwas'))
            self.assertEqual(zugang.set_password(store.db, ' lennert-gust ', PASSWORT), 'Lennert-Gust')
            self.assertEqual(zugang.check_login(store.db, 'LENNERT-GUST', PASSWORT), 'Lennert-Gust')
            self.assertIsNone(zugang.check_login(store.db, 'Lennert-Gust', PASSWORT.lower()))
            self.assertIsNone(zugang.check_login(store.db, 'Unbekannt', PASSWORT))
            with self.assertRaises(ValueError):
                zugang.set_password(store.db, 'Neu', PASSWORT)                         # ohne create kein neuer Benutzer
            zugang.set_password(store.db, 'Kassenprüfer', PASSWORT, actor='Lennert-Gust', create=True)
            audit = store.rows("SELECT actor,action,detail FROM audit WHERE action LIKE '%Passwort%' OR action LIKE 'Benutzer%' ORDER BY id")
            self.assertEqual([a['action'] for a in audit], ['Passwort geändert', 'Benutzer angelegt (Serverbetrieb)'])
            self.assertTrue(all(PASSWORT not in json.dumps(a) for a in audit))
            self.assertNotIn(PASSWORT, json.dumps(store.rows('SELECT * FROM users')))
            store.db.close()


class ServerHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = (app.DATA, app.DB, app.BACKUPS, app.MODE, app.ADDRESSES, app.THROTTLE)
        app.DATA = Path(cls.tmp.name) / 'Daten'
        app.DB = app.DATA / 'finanzen.sqlite3'
        (app.DATA / 'Importe').mkdir(parents=True)
        app.BACKUPS = Path(cls.tmp.name) / 'Sicherungen'
        app.MODE = 'server'
        app.ADDRESSES = [ADRESSE]
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port
        cls.url = f'http://127.0.0.1:{cls.port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        app.DATA, app.DB, app.BACKUPS, app.MODE, app.ADDRESSES, app.THROTTLE = cls.saved
        app.SESSIONS.clear()
        cls.tmp.cleanup()

    def setUp(self):
        app.THROTTLE = zugang.Throttle()
        app.SESSIONS.clear()

    def call(self, path, data=None, cookie=None, csrf=None, host=None, origin=ADRESSE, headers=None):
        h = {'Host': host or 'ju-finanzen.beispiel.ts.net'}
        if data is not None:
            h['Content-Type'] = 'application/json'
            if origin:
                h['Origin'] = origin
        if cookie:
            h['Cookie'] = f'__Host-ju_session={cookie}; ju_session={cookie}'   # Name je nach HTTPS bzw. HTTP
        if csrf:
            h['X-CSRF-Token'] = csrf
        h.update(headers or {})
        req = urllib.request.Request(self.url + path, json.dumps(data).encode() if data is not None else None, h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b'{}'), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}'), e.headers

    def user(self, name='Lennert-Gust'):
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, name, PASSWORT, create=True)
        finally:
            store.db.close()

    def login(self, password=PASSWORT, **kw):
        status, body, headers = self.call('/api/login', {'name': 'Lennert-Gust', 'password': password}, **kw)
        cookie = headers.get('Set-Cookie', '')
        sid = cookie.split(';')[0].split('=', 1)[1] if status == 200 else None
        return status, body, sid, cookie

    def test_status_and_unconfigured(self):
        app.DB.unlink(missing_ok=True)
        status, body, _ = self.call('/api/status')
        self.assertEqual((status, body['mode'], body['auth'], body['configured'], body['user']), (200, 'server', 'password', False, None))
        self.assertEqual(self.login()[0], 401)                                         # noch kein Benutzer
        store = Store(app.DB)
        store.db.execute("INSERT INTO users(name,password) VALUES('Lennert-Gust','')")  # aus der Mac-App übernommen
        store.db.commit()
        store.db.close()
        self.assertFalse(self.call('/api/status')[1]['configured'])
        self.assertEqual(self.login('')[0], 401)                                       # leeres Passwort öffnet nichts

    def test_login_session_logout(self):
        self.user()
        self.assertEqual(self.call('/api/data?year=2026')[0], 401)
        self.assertEqual(self.login('falsch-falsch-falsch')[0], 401)
        status, body, sid, cookie = self.login()
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Secure', cookie)                                                # Host ist die https-Adresse
        self.assertEqual(self.call('/api/status', cookie=sid)[1]['user'], 'Lennert-Gust')
        self.assertEqual(self.call('/api/data?year=2026', cookie=sid)[0], 200)
        self.assertEqual(self.call('/api/settings', {'issuer': 'X'}, cookie=sid)[0], 403)   # ohne CSRF-Token
        self.assertEqual(self.call('/api/logout', {}, cookie=sid, csrf=body['csrf'])[0], 200)
        self.assertEqual(self.call('/api/data?year=2026', cookie=sid)[0], 401)

    def test_cookie_secure_only_over_https(self):
        self.user()
        _, _, _, cookie = self.login(host=f'127.0.0.1:{self.port}', origin=self.url)
        self.assertNotIn('Secure', cookie)                                             # z. B. SSH-Tunnel auf 127.0.0.1
        _, _, _, cookie = self.login(host=f'127.0.0.1:{self.port}', origin=ADRESSE, headers={'X-Forwarded-Proto': 'https'})
        self.assertIn('Secure', cookie)                                                # Tailscale Serve schreibt Host um

    def test_hosts_and_origins(self):
        self.user()
        self.assertEqual(self.call('/api/status', host='boese.example')[0], 403)
        self.assertEqual(self.call('/api/status', host='ju-finanzen.beispiel.ts.net:443')[0], 200)
        self.assertEqual(self.login(origin='https://boese.example')[0], 403)
        self.assertEqual(self.login(origin=None)[0], 403)
        self.assertEqual(self.login(origin='http://ju-finanzen.beispiel.ts.net')[0], 403)   # falsches Schema

    def test_mac_functions_are_off(self):
        self.user()
        _, body, sid, _ = self.login()
        for path in ('/api/login-code', '/api/login-device', '/api/stop'):
            self.assertEqual(self.call(path, {'token': app.STOP_TOKEN, 'code': 'x'})[0], 404, path)
        self.assertEqual(self.call('/api/shutdown', {}, cookie=sid, csrf=body['csrf'])[0], 404)
        self.assertTrue(self.thread.is_alive())

    def test_throttle_blocks_after_failures(self):
        self.user()
        for _ in range(zugang.FREE_ATTEMPTS):
            self.assertEqual(self.login('falsch-falsch-falsch')[0], 401)
        status, body, _, _ = self.login()
        self.assertEqual(status, 429)                                                  # auch das richtige Passwort wartet
        self.assertIn('Sekunden', body['error'])
        later = time.time() + 3600
        app.THROTTLE.clock = lambda: later                                            # eine Stunde später
        self.assertEqual(self.login()[0], 200)

    def test_throttle_counts_parallel_attempts_and_other_devices(self):
        self.user()
        results = []
        def bad():
            results.append(self.login('falsch-falsch-falsch')[0])
        threads = [threading.Thread(target=bad) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(results.count(401), zugang.FREE_ATTEMPTS)                # gleichzeitige Versuche zählen mit
        self.assertEqual(results.count(429), 20 - results.count(401))
        # anderes Gerät (Tailscale-Login hinter Tailscale Serve), anderer Name: nicht vom fremden Gerät ausgesperrt
        status, _, _ = self.call('/api/login', {'name': 'Zweiter', 'password': 'falsch-falsch-falsch'}, headers={'Tailscale-User-Login': 'andere@example.org'})
        self.assertEqual(status, 401)
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'Zweiter', PASSWORT, create=True)
        finally:
            store.db.close()
        status, _, _ = self.call('/api/login', {'name': 'Zweiter', 'password': PASSWORT}, headers={'Tailscale-User-Login': 'andere@example.org'})
        self.assertEqual(status, 200)

    def test_throttle_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = zugang.Throttle(path=Path(tmp) / '.anmeldesperre')
            for _ in range(zugang.FREE_ATTEMPTS):
                self.assertEqual(t.attempt(('geraet:x',)), 0)
            self.assertGreater(zugang.Throttle(path=Path(tmp) / '.anmeldesperre').wait(('geraet:x',)), 0)
            self.assertEqual(stat.S_IMODE((Path(tmp) / '.anmeldesperre').stat().st_mode), 0o600)

    def test_request_limits_before_reading(self):
        self.user()
        status, body, _ = self.call('/api/login', {'name': 'x', 'password': 'y' * 9000})
        self.assertEqual(status, 413)                                                  # Anmeldung: höchstens 8 KB
        self.assertEqual(self.call('/api/settings', {'x': 'y' * 100})[0], 401)         # ohne Sitzung: gar nicht erst lesen
        req = urllib.request.Request(self.url + '/api/login', b'[1,2,3]', {'Host': 'ju-finanzen.beispiel.ts.net', 'Origin': ADRESSE, 'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)                                       # kein Objekt → 400 statt 500
        import socket
        with socket.create_connection(('127.0.0.1', self.port)) as sock:
            sock.sendall(b'POST /api/login HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: http://127.0.0.1:%d\r\nContent-Type: application/json\r\nContent-Length: -1\r\n\r\n' % (self.port, self.port))
            self.assertIn(b' 400 ', sock.recv(200))

    def test_cross_site_requests_and_foreign_cookies(self):
        self.user()
        _, body, sid, cookie = self.login()
        self.assertTrue(cookie.startswith('__Host-ju_session='))                       # über HTTPS mit __Host-Präfix
        site = {'Sec-Fetch-Site': 'same-site'}                                         # z. B. andere Seite im Tailnet (*.ts.net)
        self.assertEqual(self.call('/api/pruefpaket.zip?year=2026', cookie=sid, headers=site)[0], 403)
        self.assertEqual(self.call('/api/settings', {'issuer': 'X'}, cookie=sid, csrf=body['csrf'], headers=site)[0], 403)
        self.assertEqual(self.call('/api/status', cookie=sid, headers={'Sec-Fetch-Site': 'same-origin'})[1]['user'], 'Lennert-Gust')
        self.assertEqual(self.call('/api/status', cookie=sid, headers={'Sec-Fetch-Site': 'none'})[1]['user'], 'Lennert-Gust')
        # fremde Cookies (z. B. von einem anderen Rechner im Tailnet gesetzt) verhindern das Lesen der Sitzung nicht
        h = {'Host': 'ju-finanzen.beispiel.ts.net', 'Cookie': f'x=a b; ju_session=falsch; __Host-ju_session={sid}; y="'}
        with urllib.request.urlopen(urllib.request.Request(self.url + '/api/status', headers=h)) as r:
            self.assertEqual(json.load(r)['user'], 'Lennert-Gust')

    def test_password_reset_on_server_ends_sessions(self):
        self.user()
        _, _, sid, _ = self.login()
        self.assertEqual(self.call('/api/data?year=2026', cookie=sid)[0], 200)
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'Lennert-Gust', 'Ganz-neues-Passwort-1', actor='Serververwaltung')   # wie verwaltung.py
        finally:
            store.db.close()
        self.assertEqual(self.call('/api/data?year=2026', cookie=sid)[0], 401)

    def test_backup_zip(self):
        self.user()
        _, _, sid, _ = self.login()
        (app.DATA / 'Belege').mkdir(exist_ok=True)
        (app.DATA / 'Belege' / 'ER-2026-001.pdf').write_bytes(b'%PDF-1.4 erfunden')
        req = urllib.request.Request(self.url + '/api/backup', headers={'Host': 'ju-finanzen.beispiel.ts.net', 'Cookie': f'__Host-ju_session={sid}'})
        with urllib.request.urlopen(req) as r:
            names = zipfile.ZipFile(io.BytesIO(r.read())).namelist()
        self.assertIn('Daten/finanzen.sqlite3', names)
        self.assertIn('Daten/Belege/ER-2026-001.pdf', names)

    def test_idle_and_absolute_timeout(self):
        self.user()
        _, _, sid, _ = self.login()
        app.SESSIONS[sid]['seen'] -= app.IDLE_MINUTES * 60 + 1
        self.assertEqual(self.call('/api/data?year=2026', cookie=sid)[0], 401)
        self.assertNotIn(sid, app.SESSIONS)
        _, _, sid, _ = self.login()
        app.SESSIONS[sid]['expires'] = time.time() - 1
        self.assertEqual(self.call('/api/status', cookie=sid)[1]['user'], None)

    def test_change_password(self):
        self.user()
        _, body, sid, _ = self.login()
        _, _, other, _ = self.login()
        neu = 'Neues-Passwort-2027?'
        cases = (({'current': 'falsch-falsch', 'new': neu, 'repeat': neu}, 'bisherige'),
                 ({'current': PASSWORT, 'new': neu, 'repeat': neu + 'x'}, 'stimmen nicht'),
                 ({'current': PASSWORT, 'new': 'kurz', 'repeat': 'kurz'}, '12 Zeichen'),
                 ({'current': PASSWORT, 'new': PASSWORT, 'repeat': PASSWORT}, 'anderes'))
        for data, text in cases:
            status, err, _ = self.call('/api/password', data, cookie=sid, csrf=body['csrf'])
            self.assertEqual(status, 400, text)
            self.assertIn(text, err['error'])
        app.THROTTLE = zugang.Throttle()
        self.assertEqual(self.call('/api/password', {'current': PASSWORT, 'new': neu, 'repeat': neu}, cookie=sid, csrf=body['csrf'])[0], 200)
        self.assertEqual(self.call('/api/status', cookie=sid)[1]['user'], 'Lennert-Gust')   # eigene Sitzung bleibt
        self.assertIsNone(self.call('/api/status', cookie=other)[1]['user'])               # andere Sitzungen enden
        self.assertEqual(self.login()[0], 401)
        self.assertEqual(self.login(neu)[0], 200)
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'Lennert-Gust', PASSWORT)
        finally:
            store.db.close()

    def test_gzip_for_browsers(self):
        import gzip as gz
        self.user()
        _, _, sid, _ = self.login()
        req = urllib.request.Request(self.url + '/api/data?year=2026', headers={'Host': 'ju-finanzen.beispiel.ts.net', 'Cookie': '__Host-ju_session=' + sid, 'Accept-Encoding': 'gzip, br'})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers['Content-Encoding'], 'gzip')
            self.assertIn('Accept-Encoding', r.headers['Vary'])
            self.assertIn('accounts', json.loads(gz.decompress(r.read())))
        with urllib.request.urlopen(urllib.request.Request(self.url + '/api/data?year=2026', headers={'Host': 'ju-finanzen.beispiel.ts.net', 'Cookie': '__Host-ju_session=' + sid})) as r:
            self.assertIsNone(r.headers['Content-Encoding'])                           # ohne Angabe unkomprimiert
            self.assertIn('accounts', json.loads(r.read()))

    def test_internal_errors_are_logged_without_content(self):
        self.user()
        _, body, sid, _ = self.login()
        out = io.StringIO()
        with unittest.mock.patch.object(app.kontenrahmen, 'accounts', side_effect=RuntimeError('Geheime Mitgliedsdaten Max Muster')), redirect_stdout(out):
            status, err, _ = self.call('/api/konten?year=2026', cookie=sid)
        self.assertEqual(status, 500)
        self.assertNotIn('Max Muster', err['error'])
        self.assertIn('RuntimeError', out.getvalue())
        self.assertNotIn('Max Muster', out.getvalue())


class StartTests(unittest.TestCase):
    def test_addresses_and_bind_rules(self):
        self.assertEqual(app.clean_address(' HTTPS://Ju.Beispiel.ts.net/ '), 'https://ju.beispiel.ts.net')
        self.assertEqual(app.clean_address('http://100.64.0.7:8769'), 'http://100.64.0.7:8769')
        for bad in ('ju.beispiel.ts.net', 'ftp://x', 'https://x/pfad', 'https://a:b@x', ''):
            with self.assertRaises(ValueError, msg=bad):
                app.clean_address(bad)
        self.assertEqual(app.check_bind('127.0.0.1'), '127.0.0.1')
        self.assertEqual(app.check_bind('100.64.0.7'), '100.64.0.7')
        self.assertEqual(app.check_bind('fd7a:115c:a1e0::1'), 'fd7a:115c:a1e0::1')
        for bad in ('0.0.0.0', '::', 'localhost', '203.0.113.5', '192.168.1.10'):
            with self.assertRaises(SystemExit, msg=bad):
                app.check_bind(bad)
        saved = app.ADDRESSES
        try:
            app.ADDRESSES = ['https://ju.beispiel.ts.net', 'http://100.64.0.7:8769']
            self.assertEqual(app.allowed_hosts(8769), {'127.0.0.1:8769', 'localhost:8769', 'ju.beispiel.ts.net', 'ju.beispiel.ts.net:443', '100.64.0.7:8769'})
        finally:
            app.ADDRESSES = saved

    def test_server_start_as_process(self):
        """Start wie unter systemd: Umgebungsvariablen, kein Browser, keine Steuerdateien, SIGTERM beendet sauber."""
        with tempfile.TemporaryDirectory() as tmp:
            port = 18000 + os.getpid() % 1000
            env = {**os.environ, 'JU_FINANZEN_MODUS': 'server', 'JU_FINANZEN_DATEN': f'{tmp}/Daten', 'JU_FINANZEN_SICHERUNGEN': f'{tmp}/Sicherungen',
                   'JU_FINANZEN_PORT': str(port), 'JU_FINANZEN_ADRESSEN': ADRESSE, 'PYTHONUNBUFFERED': '1'}
            proc = subprocess.Popen([sys.executable, str(Path(app.__file__)), '--no-browser'], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/status', headers={'Host': 'ju-finanzen.beispiel.ts.net'})) as r:
                            status = json.load(r)
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail('Server startet nicht')
                self.assertEqual((status['mode'], status['configured']), ('server', False))
                self.assertFalse((Path(tmp) / 'Daten' / '.steuerung').exists())
                self.assertFalse((Path(tmp) / 'Daten' / '.start-url').exists())
                proc.terminate()                                                           # SIGTERM wie systemctl stop
                out, _ = proc.communicate(timeout=10)
            finally:
                if proc.poll() is None:
                    proc.kill()
            self.assertEqual(proc.returncode, 0, out)
            self.assertIn('Serverbetrieb', out)
            self.assertIn('passwort', out)
            self.assertIn('Beendet', out)
            self.assertTrue((Path(tmp) / 'Daten' / 'finanzen.sqlite3').exists())

    def test_refuses_all_interfaces(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            saved = (app.DATA, app.DB, app.BACKUPS, app.MODE, app.ADDRESSES)
            try:
                with self.assertRaises(SystemExit):
                    app.main(['--modus', 'server', '--host', '0.0.0.0', '--data', f'{tmp}/D', '--no-browser'])
                self.assertEqual(app.main(['--modus', 'server', '--adresse', 'keine-adresse', '--data', f'{tmp}/D', '--no-browser']), 4)
            finally:
                app.DATA, app.DB, app.BACKUPS, app.MODE, app.ADDRESSES = saved


class GeheimnisDateiTests(unittest.TestCase):
    def test_mail_password_in_private_file(self):
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {'JU_FINANZEN_GEHEIMNISSE': f'{tmp}/geheim'}):
            geheimnis._cache.clear()
            try:
                self.assertTrue(geheimnis.available())
                self.assertEqual(geheimnis.save('kasse@example.org', 'smtp-test-passwort'), 'datei')
                f = Path(tmp) / 'geheim' / 'e-mail-passwort.json'
                self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode), 0o700)
                geheimnis._cache.clear()                                                   # wie nach einem Neustart
                self.assertEqual((geheimnis.load('kasse@example.org'), geheimnis.where('kasse@example.org')), ('smtp-test-passwort', 'datei'))
                self.assertEqual(geheimnis.save('kasse@example.org', 'nur-jetzt', remember=False), 'sitzung')
                geheimnis._cache.clear()
                self.assertIsNone(geheimnis.load('kasse@example.org'))                     # Datei-Eintrag entfernt
                geheimnis.save('kasse@example.org', 'smtp-test-passwort')
                geheimnis.forget('kasse@example.org')
                geheimnis._cache.clear()
                self.assertIsNone(geheimnis.load('kasse@example.org'))
                self.assertEqual(json.loads(f.read_text()), {})
            finally:
                geheimnis._cache.clear()


class VerwaltungTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.args = ['--data', str(self.base / 'Daten'), '--backups', str(self.base / 'Sicherungen'), '--port', '1']

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *argv, stdin=''):
        out = io.StringIO()
        with unittest.mock.patch('sys.stdin', io.StringIO(stdin)), redirect_stdout(out):
            try:
                code = verwaltung.main([*self.args, *argv])
            except SystemExit as e:                                                       # Fehlermeldung wie auf der Konsole
                return (e.code, out.getvalue() + e.code) if isinstance(e.code, str) else (e.code, out.getvalue())
        return code, out.getvalue()

    def mac_data(self, folder, entries=True):
        """Datenordner wie aus der Mac-App: Benutzer ohne Passwort, eine Buchung, ein Beleg."""
        folder.mkdir(parents=True)
        store = Store(folder / 'finanzen.sqlite3')
        store.db.execute("INSERT INTO users(name,password) VALUES('Lennert-Gust','')")
        if entries:
            store.db.execute("INSERT INTO members(number,name,address,birth,joined,kv) VALUES('T1','Test Person','Weg 1','2000-01-01','2024-01-01','5100')")
        store.db.commit()
        store.db.close()
        (folder / 'Belege').mkdir()
        (folder / 'Belege' / 'ER-2026-001.pdf').write_bytes(b'%PDF-1.4 Test')
        (folder / 'Importe').mkdir()

    def backup_zip(self, folder, extra=()):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.write(folder / 'finanzen.sqlite3', 'Daten/finanzen.sqlite3')
            z.write(folder / 'Belege' / 'ER-2026-001.pdf', 'Daten/Belege/ER-2026-001.pdf')
            for name, content in extra:
                z.writestr(name, content)
        target = self.base / 'JU-Sicherung.zip'
        target.write_bytes(buf.getvalue())
        return target

    def test_password_users_check_backup(self):
        code, out = self.run_cmd('passwort', 'Lennert-Gust', stdin=f'{PASSWORT}\n{PASSWORT}\n')
        self.assertEqual(code, 0, out)
        self.assertIn('angelegt', out)
        self.assertNotIn(PASSWORT, out)
        code, out = self.run_cmd('passwort', 'Zweiter', stdin=f'{PASSWORT}\n{PASSWORT}\n')
        self.assertIn('--neu', out)                                                       # Tippfehler legt keinen Benutzer an
        code, out = self.run_cmd('passwort', 'Lennert-Gust', stdin=f'{PASSWORT}\nanders-anders-anders\n')
        self.assertIn('stimmen nicht', out)
        code, out = self.run_cmd('passwort', 'Lennert-Gust', stdin='kurz\nkurz\n')
        self.assertIn('Nichts geändert', out)
        code, out = self.run_cmd('benutzer')
        self.assertIn('Lennert-Gust: Passwort gesetzt', out)
        code, out = self.run_cmd('pruefen')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['integritaet'], 'ok')
        code, out = self.run_cmd('sicherung')
        self.assertEqual(code, 0, out)
        self.assertEqual(len(list((self.base / 'Sicherungen' / 'Manuell').glob('sicherung-*.sqlite3'))), 1)

    def test_take_over_mac_backup_zip(self):
        self.mac_data(self.base / 'Mac')
        source = self.backup_zip(self.base / 'Mac', extra=[('Daten/../boese.txt', 'x'), ('Daten/Belege/.versteckt', 'x'), ('anderes.txt', 'x')])
        (self.base / 'Daten').mkdir()
        Store(self.base / 'Daten' / 'finanzen.sqlite3').db.close()                     # Neuinstallation: Dienst lief schon einmal
        code, out = self.run_cmd('uebernehmen', str(source))
        self.assertEqual(code, 0, out)
        self.assertIn('1 Mitglieder', out)
        self.assertIn('Soll = Haben: ja', out)
        self.assertIn('passwort "Lennert-Gust"', out)
        self.assertFalse((self.base / 'boese.txt').exists())
        self.assertFalse((self.base / 'Daten' / 'Belege' / '.versteckt').exists())
        self.assertTrue((self.base / 'Daten' / 'Belege' / 'ER-2026-001.pdf').is_file())
        self.assertEqual(stat.S_IMODE((self.base / 'Daten' / 'finanzen.sqlite3').stat().st_mode), 0o600)
        self.assertEqual(len(list((self.base / 'Sicherungen' / 'Vor Änderungen').glob('vor-uebernahme-*'))), 1)   # leere Neuinstallation zur Seite
        # vorhandene Daten nur mit --ersetzen; vorher gesichert, nichts gelöscht
        code, out = self.run_cmd('uebernehmen', str(source))
        self.assertIn('--ersetzen', out)
        code, out = self.run_cmd('uebernehmen', str(source), '--ersetzen')
        self.assertEqual(code, 0, out)
        kept = list((self.base / 'Sicherungen' / 'Vor Änderungen').glob('vor-uebernahme-*/Belege/ER-2026-001.pdf'))
        self.assertEqual(len(kept), 1)

    def test_take_over_refuses_bad_files_and_running_server(self):
        bad = self.base / 'kaputt.zip'
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('Daten/Belege/x.pdf', b'%PDF')
        self.assertIn('fehlt Daten/finanzen.sqlite3', self.run_cmd('uebernehmen', str(bad))[1])
        other = self.base / 'fremd.sqlite3'
        con = sqlite3.connect(other)
        con.execute('CREATE TABLE x(y)')
        con.commit()
        con.close()
        self.assertIn('keine Datenbank von JU', self.run_cmd('uebernehmen', str(other))[1])
        text = self.base / 'text.txt'
        text.write_text('hallo')
        self.assertIn('weder', self.run_cmd('uebernehmen', str(text))[1])
        self.assertFalse((self.base / 'Daten').exists())                                 # nichts angelegt
        self.mac_data(self.base / 'Mac')
        with unittest.mock.patch.object(verwaltung, 'server_running', return_value=True):
            self.assertIn('Dienst läuft', self.run_cmd('uebernehmen', str(self.backup_zip(self.base / 'Mac')))[1])

    def test_take_over_single_database_file(self):
        self.mac_data(self.base / 'Mac')
        code, out = self.run_cmd('uebernehmen', str(self.base / 'Mac' / 'finanzen.sqlite3'))
        self.assertEqual(code, 0, out)
        self.assertIn('1 Mitglieder', out)
        # Rücksicherung einer Tagessicherung: Belege des laufenden Datenordners bleiben erhalten
        (self.base / 'Daten' / 'Belege' / 'ER-2026-002.pdf').write_bytes(b'%PDF-1.4 neu')
        code, out = self.run_cmd('uebernehmen', str(self.base / 'Mac' / 'finanzen.sqlite3'), '--ersetzen')
        self.assertEqual(code, 0, out)
        self.assertTrue((self.base / 'Daten' / 'Belege' / 'ER-2026-002.pdf').is_file())


class AktualisierenTests(unittest.TestCase):
    """server/aktualisieren.sh: Update aus dem Git-Repository – nur freigegebene Versionen (Tag v<Version>), nie rückwärts.

    Das „Repository bei GitHub“ ist hier ein Ordner im Temp-Verzeichnis; install.sh ist eine Attrappe."""
    SCRIPT = Path(__file__).resolve().parent / 'server' / 'aktualisieren.sh'

    @classmethod
    def setUpClass(cls):
        import shutil
        if sys.platform == 'win32' or not shutil.which('git') or not shutil.which('bash'):
            raise unittest.SkipTest('braucht git und bash')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'LC_ALL': 'C.UTF-8',
                    'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.org',
                    'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.org'}
        self.origin = self.base / 'origin'
        (self.origin / 'server').mkdir(parents=True)
        self.git(self.origin, 'init', '--quiet')
        for version in ('0.2', '0.9', '0.10'):
            self.release(version)
        self.source = self.base / 'opt' / 'quelle'
        self.source.parent.mkdir()
        self.git(self.base, 'clone', '--quiet', '--branch', 'v0.9', str(self.origin), str(self.source))

    def git(self, folder, *args):
        return subprocess.run(['git', '-C', str(folder), *args], env=self.env, capture_output=True, text=True, check=True).stdout.strip()

    def release(self, version, tag=None, installer='#!/bin/sh\necho installiert > "$(dirname "$0")/../../installiert"\n'):
        (self.origin / 'app.py').write_text(f"VERSION='{version}'\n", encoding='utf-8')
        script = self.origin / 'server' / 'install.sh'
        script.write_text(installer, encoding='utf-8')
        script.chmod(0o755)
        self.git(self.origin, 'add', '-A')
        self.git(self.origin, 'commit', '--quiet', '--allow-empty', '-m', f'Version {version}')
        self.git(self.origin, 'tag', tag or f'v{version}')

    def run_script(self, *args):
        return subprocess.run(['bash', str(self.SCRIPT), '--quelle', str(self.source), *args], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def head(self):
        return self.git(self.source, 'rev-parse', 'HEAD')

    def version(self):
        return (self.source / 'app.py').read_text(encoding='utf-8').strip()

    def test_check_only_shows_newest_release(self):
        before = self.head()
        self.release('0.11', tag='vorschau')                 # kein Versions-Tag: zählt nicht
        self.git(self.origin, 'tag', 'v0.99-test')           # ebenso
        result = self.run_script('--pruefen')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('installiert: 0.9', result.stdout)
        self.assertIn('neu: 0.10', result.stdout)            # 0.10 ist neuer als 0.9 (nicht alphabetisch)
        self.assertEqual(self.head(), before)

    def test_update_to_newest_and_idempotent(self):
        result = self.run_script('--ohne-installation')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.version(), "VERSION='0.10'")
        self.assertEqual(self.head(), self.git(self.origin, 'rev-parse', 'v0.10^{commit}'))
        self.assertFalse((self.base / 'opt' / 'installiert').exists())
        (self.base / 'opt' / 'current').mkdir()              # installiert ist jetzt 0.10
        (self.base / 'opt' / 'current' / 'app.py').write_text("VERSION='0.10'\n", encoding='utf-8')
        again = self.run_script('--ohne-installation')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('bereits aktiv', again.stdout)

    def test_installed_version_counts_not_the_checkout(self):
        (self.base / 'opt' / 'current').mkdir()
        (self.base / 'opt' / 'current' / 'app.py').write_text("VERSION='0.10'\n", encoding='utf-8')
        result = self.run_script('--pruefen')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('installiert: 0.10', result.stdout)
        self.assertIn('bereits aktiv', result.stdout)

    def test_refuses_older_unknown_and_malformed_versions(self):
        self.run_script('--ohne-installation')
        before = self.head()
        for args, text in ((['0.2'], 'älter'), (['9.9'], 'gibt es nicht'), (['0.9; rm -rf x'], 'Ungültige Version'),
                           (['--unsinn'], 'Unbekannte Option'), (['0.9', '0.10'], 'nur eine Version')):
            with self.subTest(args=args):
                result = self.run_script('--ohne-installation', *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(text, result.stderr)
                self.assertEqual(self.head(), before)

    def test_tag_must_match_program_version(self):
        self.release('0.12', tag='v0.11')                    # Tag und Programmversion passen nicht zusammen
        before = self.head()
        result = self.run_script('--ohne-installation')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('passt nicht zusammen', result.stderr)
        self.assertEqual(self.head(), before)

    def test_moved_tag_is_an_error(self):
        self.run_script('--ohne-installation')
        before = self.head()
        self.release('0.10', tag='zwischenstand')
        self.git(self.origin, 'tag', '--force', 'v0.10')     # freigegebene Version nachträglich versetzt
        self.release('0.11')
        result = self.run_script('--ohne-installation')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nachträglich verändert', result.stderr)
        self.assertEqual(self.head(), before)

    def test_local_changes_and_wrong_folder_stop(self):
        (self.source / 'app.py').write_text("VERSION='0.9'\nprint('von Hand')\n", encoding='utf-8')
        result = self.run_script('--ohne-installation')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('von Hand geändert', result.stderr)
        other = subprocess.run(['bash', str(self.SCRIPT), '--quelle', str(self.base), '--pruefen'], env=self.env,
                               capture_output=True, text=True, timeout=60)
        self.assertNotEqual(other.returncode, 0)
        self.assertIn('kein Checkout', other.stderr)

    def test_install_step(self):
        if os.geteuid() != 0:                                # ohne root: klare Meldung, nichts verändert
            before = self.head()
            result = self.run_script()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('sudo', result.stderr)
            self.assertEqual(self.head(), before)
            return
        result = self.run_script()                            # als root (Testumgebung): die Attrappe wird aufgerufen
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.version(), "VERSION='0.10'")
        self.assertTrue((self.base / 'opt' / 'installiert').is_file())
        (self.base / 'opt' / 'current').mkdir()
        (self.base / 'opt' / 'current' / 'app.py').write_text("VERSION='0.10'\n", encoding='utf-8')
        before = self.head()
        self.release('0.11', installer='#!/bin/sh\nexit 3\n')   # Installation scheitert: Quelle zurück auf den alten Stand
        failed = self.run_script()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('läuft weiter', failed.stderr)
        self.assertEqual(self.head(), before)


if __name__ == '__main__':
    unittest.main()
