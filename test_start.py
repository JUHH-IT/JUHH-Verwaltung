"""Tests für automatische Sicherung, Datenordner und den Mac-Starter."""
import json
import os
import shutil
import signal
import socket
import sqlite3
import stat
import sys
import tempfile
import importlib.util
import unittest
import urllib.error
import urllib.request
import unittest.mock
from datetime import date
from pathlib import Path

import sicherung
from app import mac_user_name

REPO = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('build_app', REPO / 'mac' / 'build_app.py')
BUILD = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BUILD)
sys.path.insert(0, str(REPO / 'mac'))
import launcher  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / 'finanzen.sqlite3'
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE t(x)')
        con.execute('INSERT INTO t VALUES(42)')
        con.commit()
        con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_once_per_day_and_pruning(self):
        target = self.tmp / 'Sicherungen'
        first = sicherung.daily_backup(self.db, target, today=date(2026, 1, 1))
        self.assertEqual(sqlite3.connect(first).execute('SELECT x FROM t').fetchone()[0], 42)
        self.assertIsNone(sicherung.daily_backup(self.db, target, today=date(2026, 1, 1)))
        for day in range(2, 6):
            sicherung.daily_backup(self.db, target, keep=3, today=date(2026, 1, day))
        names = sorted(p.name for p in target.iterdir())
        self.assertEqual(names, ['finanzen-2026-01-03.sqlite3', 'finanzen-2026-01-04.sqlite3', 'finanzen-2026-01-05.sqlite3'])

    def test_missing_database(self):
        self.assertIsNone(sicherung.daily_backup(self.tmp / 'fehlt.sqlite3', self.tmp / 'S'))


class LauncherBase(unittest.TestCase):
    """Baut eine Bundle-Struktur nach und startet den Server wie die Mac-App."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp()) / 'JU Hamburg Finanzen'
        self.app_dir = self.base / 'JU Hamburg Finanzen.app' / 'Contents' / 'Resources' / 'app'
        self.app_dir.mkdir(parents=True)
        for name in BUILD.APP_FILES:  # dieselbe Dateiliste wie die echte App
            (self.app_dir / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, self.app_dir / name)
        shutil.copytree(REPO / 'static', self.app_dir / 'static')
        for name in BUILD.MAC_FILES:
            shutil.copy2(REPO / 'mac' / name, self.app_dir / name)
        self.opened, self.dialogs, self.checks = [], [], []
        self.check_result = (True, '', False)             # Touch ID / Mac-Passwort (nur auf dem Mac) nachgebildet
        self._orig = launcher.open_url, launcher.dialog, launcher.mac_check
        launcher.open_url, launcher.dialog = self.opened.append, self.dialogs.append
        launcher.mac_check = lambda: self.checks.append(1) or self.check_result
        launcher.LAST_PROCESS = None

    def tearDown(self):
        launcher.open_url, launcher.dialog, launcher.mac_check = self._orig
        proc = launcher.LAST_PROCESS
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(10)
        shutil.rmtree(self.base.parent)

    def sign_in(self, url, port):
        """Anmeldecode aus der geöffneten Adresse einlösen wie die Oberfläche – liefert den Benutzernamen."""
        import http.cookiejar
        code = url.split('?login=', 1)[1]
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/login-device', json.dumps({'code': code}).encode(),
                                         {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'})
        client.open(request, timeout=5).read()
        return json.load(client.open(f'http://127.0.0.1:{port}/api/status', timeout=5))['user']


class LauncherTests(LauncherBase):
    def test_requires_data_folder(self):
        self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=free_port()), 1)
        self.assertIn('Daten', self.dialogs[0])

    def test_start_reuse_and_mac_login(self):
        (self.base / 'Daten').mkdir()
        port = free_port()
        self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=port, wait=20), 0, self.dialogs)
        self.assertTrue(self.opened[0].startswith(f'http://127.0.0.1:{port}/?login='))   # Browser nur nach macOS-Prüfung
        self.assertEqual(self.checks, [1])
        self.assertIn('Angemeldet über macOS', (self.base / 'Daten' / 'programm.log').read_text(encoding='utf-8'))
        self.assertEqual(self.sign_in(self.opened[0], port), mac_user_name())          # erster Start legt Benutzer an
        url_file = self.base / 'Daten' / '.start-url'
        self.assertEqual(stat.S_IMODE(url_file.stat().st_mode), 0o600)
        self.assertNotIn('setup', url_file.read_text(encoding='utf-8'))
        first = launcher.LAST_PROCESS
        self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=port), 0)   # läuft schon → nur öffnen
        self.assertIs(launcher.LAST_PROCESS, first)
        self.assertNotEqual(self.opened[1], self.opened[0])                              # jedes Mal ein neuer Code

    def test_no_browser_without_mac_check(self):
        (self.base / 'Daten').mkdir()
        port = free_port()
        self.check_result = (False, 'Touch ID ist gesperrt.', False)
        self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=port, wait=20), 0)
        self.assertEqual(self.opened, [])
        self.assertIn('Touch ID ist gesperrt', self.dialogs[0])
        self.check_result = (False, 'Abgebrochen', True)                                # Abbruch: ohne Meldung
        self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=port), 0)
        self.assertEqual((self.opened, len(self.dialogs)), ([], 1))

    def test_login_code_needs_control_key(self):
        (self.base / 'Daten').mkdir()
        port = free_port()
        self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=port, wait=20), 0, self.dialogs)
        (self.base / 'Daten' / '.steuerung').write_text('falsch', encoding='utf-8')
        with self.assertRaises(urllib.error.HTTPError):
            launcher.login_code(port, self.base / 'Daten')
        self.assertEqual(launcher.status(port)['version'], json.loads(json.dumps(launcher.status(port)))['version'])
        self.assertTrue((self.base / 'Daten' / 'finanzen.sqlite3').exists())
        self.assertIn('Daten:', (self.base / 'Daten' / 'programm.log').read_text(encoding='utf-8'))

    def test_update_restarts_older_version(self):
        (self.base / 'Daten').mkdir()
        port = free_port()
        self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=port, wait=20), 0, self.dialogs)
        old = launcher.LAST_PROCESS
        info = self.app_dir.parent.parent / 'Info.plist'                 # neue Version „installiert“
        info.write_bytes(__import__('plistlib').dumps({'CFBundleShortVersionString': '99.0'}))
        try:
            self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=port, wait=20), 0, self.dialogs)
            self.assertEqual(old.wait(10), 0)                                   # alte Version sauber beendet
            self.assertIsNot(launcher.LAST_PROCESS, old)
        finally:
            info.unlink()

    def test_foreign_port(self):
        (self.base / 'Daten').mkdir()
        with socket.socket() as other:
            other.bind(('127.0.0.1', 0))
            other.listen()
            self.assertEqual(launcher.main(self.app_dir / 'launcher.py', port=other.getsockname()[1], wait=2), 1)
        self.assertIn('belegt', self.dialogs[0])


class FakeWindow:
    """Ersatz für mac/fenster.py (PyObjC gibt es nur auf dem Mac)."""

    def __init__(self, supported=True, option_key=False, error=None):
        self._supported, self._option_key, self.error, self.calls = supported, option_key, error, []
        self.port, self.asked_while = None, []

    def supported(self):
        return self._supported

    def option_key_down(self):       # ersetzt launcher.option_key_down (CoreGraphics gibt es nur auf dem Mac)
        self.asked_while.append(launcher.status(self.port) if self.port else 'unbekannt')
        return self._option_key

    def run(self, url, **options):
        self.calls.append((url, options))
        if self.error:
            raise self.error
        return 0


class WindowTests(LauncherBase):
    """Eigenes Programmfenster statt Browser – mit Rückfall auf den Browser."""

    def start(self, window, marker=False):
        (self.base / 'Daten').mkdir()
        if marker:
            (self.base / 'Daten' / launcher.BROWSER_MARKER).touch()
        self.port = window.port = free_port()
        saved, original = sys.modules.get('fenster'), launcher.option_key_down
        sys.modules['fenster'], launcher.option_key_down = window, window.option_key_down
        try:
            return launcher.main(self.app_dir / 'launcher.py', port=self.port, wait=20)
        finally:
            launcher.option_key_down = original
            if saved is None:
                sys.modules.pop('fenster', None)
            else:
                sys.modules['fenster'] = saved

    def log(self):
        return (self.base / 'Daten' / 'programm.log').read_text(encoding='utf-8')

    def test_window_instead_of_browser_and_closing_stops_server(self):
        window = FakeWindow()
        self.assertEqual(self.start(window), 0, self.dialogs)
        self.assertEqual(self.opened, [])
        url, options = window.calls[0]
        self.assertEqual(url, f'http://127.0.0.1:{self.port}/')
        self.assertEqual(self.sign_in(f'{url}?login={options["login"]()}', self.port), mac_user_name())
        self.assertTrue(callable(options['authenticate']))
        self.assertEqual(self.checks, [])                        # im Fenster prüft das Fenster selbst
        self.assertEqual(options['port'], self.port)
        self.assertEqual(options['icon'].name, 'AppIcon.icns')
        self.assertEqual(options['status']()['app'], launcher.APP_NAME)
        self.assertEqual(window.asked_while, [None])             # Wahltaste abgefragt, bevor der Server lief
        options['log']('Testeintrag')
        self.assertIn('Fenster: Testeintrag', self.log())
        self.assertFalse(launcher.stop_gently(self.port, self.base / 'Daten', 'andere-instanz'))
        self.assertIsNotNone(launcher.status(self.port))         # fremde Instanz: Server bleibt
        self.assertTrue(options['stop']())                       # Fenster geschlossen → Server beendet
        self.assertEqual(launcher.LAST_PROCESS.wait(10), 0)
        self.assertIsNone(launcher.status(self.port))

    def test_failing_window_falls_back_to_browser(self):
        window = FakeWindow(error=RuntimeError('WebKit fehlt'))
        self.assertEqual(self.start(window), 0, self.dialogs)
        self.assertEqual(len(window.calls), 1)
        self.assertTrue(self.opened[0].startswith(f'http://127.0.0.1:{self.port}/?login='))
        self.assertIn('Start fehlgeschlagen', self.log())
        self.assertIn('WebKit fehlt', self.log())

    def test_browser_on_request_or_old_macos(self):
        for window, marker, logged in ((FakeWindow(option_key=True), False, 'Wahltaste'),
                                       (FakeWindow(supported=False), False, 'macOS 11.3'),
                                       (FakeWindow(), True, launcher.BROWSER_MARKER)):
            with self.subTest(option_key=window._option_key, supported=window._supported, marker=marker):
                self.assertEqual(self.start(window, marker), 0, self.dialogs)
                self.assertEqual(window.calls, [])
                self.assertEqual(len(self.opened), 1)
                self.assertIn('Oberfläche im Browser', self.log())
                self.assertIn(logged, self.log())
                self.tearDown()
                self.setUp()

    def test_server_without_instance(self):
        """Ältere Version ohne Kennung (Update wartet auf E-Mail-Versand): Fenster beendet sie beim Schließen."""
        state = {'app': launcher.APP_NAME, 'version': '0.17'}
        (self.base / 'Daten').mkdir()
        window = FakeWindow()
        with unittest.mock.patch.object(launcher, 'status', lambda port, timeout=1.0: dict(state)):
            self.assertEqual(launcher.show('http://127.0.0.1:1/', self.app_dir, self.base / 'Daten', 1,
                                           (window, None), state), 0)
            options = window.calls[0][1]
            self.assertEqual(options['status'](), state)
        self.assertIn('ohne Kennung', self.log())

    def test_option_key_without_coregraphics(self):
        if sys.platform != 'darwin':
            self.assertFalse(launcher.option_key_down())

    def test_without_pyobjc_browser_is_used(self):
        module, reason = launcher.window_module()            # unter Linux: kein PyObjC
        if module is not None:
            self.skipTest('PyObjC vorhanden')
        self.assertIn('Error', reason)


if __name__ == '__main__':
    unittest.main(verbosity=2)
