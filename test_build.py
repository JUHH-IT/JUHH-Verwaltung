"""Archivierung alter App-Versionen beim Installieren (ohne Netz, ohne Bauen)."""
import importlib.util
import plistlib
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

spec = importlib.util.spec_from_file_location('build_app', Path(__file__).parent / 'mac' / 'build_app.py')
build_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_app)


def fake_app(path, version):
    (path / 'Contents').mkdir(parents=True)
    (path / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': version}))
    return path


class AppFilesTests(unittest.TestCase):
    def test_every_program_module_is_bundled(self):
        repo = Path(__file__).parent
        modules = {p.name for p in repo.glob('*.py') if not p.name.startswith('test_') and p.name != 'mac_start.py'}
        self.assertEqual(modules - set(build_app.APP_FILES), set())
        for name in build_app.APP_FILES:
            self.assertTrue((repo / name).is_file(), name)

    def test_mac_files_and_python_link(self):
        repo = Path(__file__).parent
        self.assertEqual(sorted(p.name for p in (repo / 'mac').glob('*.py') if p.name != 'build_app.py'), sorted(build_app.MAC_FILES))
        self.assertIn(f'PYTHON="$CONTENTS/MacOS/{build_app.PYTHON_LINK}"', build_app.STUB)
        self.assertIn('exec "$PYTHON" -E -s "$CONTENTS/Resources/app/launcher.py"', build_app.STUB)
        for name in ('pyobjc-core', 'pyobjc-framework-Cocoa', 'pyobjc-framework-WebKit'):
            self.assertIn(name + '==', (repo / 'mac' / 'requirements-mac.txt').read_text(encoding='utf-8'))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.archive = self.base / 'Sicherungen' / 'Programmversionen'

    def tearDown(self):
        self.tmp.cleanup()

    def test_archived_app_is_no_longer_an_app(self):
        app = fake_app(self.base / 'JU Hamburg Finanzen.app', '0.7')
        moved = build_app.archive_app(app, self.archive, stamp='20261028-120000')
        self.assertFalse(app.exists())
        self.assertEqual(moved.name, 'JU Hamburg Finanzen 0.7 (20261028-120000).app-archiv')
        self.assertTrue((moved / 'Contents' / 'Info.plist').is_file())
        self.assertEqual(list(self.archive.glob('*.app')), [])

    def test_unknown_version(self):
        app = self.base / 'JU Hamburg Finanzen.app'
        app.mkdir()
        self.assertIn(' alt (', build_app.archive_app(app, self.archive, stamp='x').name)

    def test_existing_archive_is_not_overwritten(self):
        build_app.archive_app(fake_app(self.base / 'JU Hamburg Finanzen.app', '0.7'), self.archive, stamp='s')
        with self.assertRaises(SystemExit):
            build_app.archive_app(fake_app(self.base / 'JU Hamburg Finanzen.app', '0.7'), self.archive, stamp='s')

    def test_retire_old_archives(self):
        fake_app(self.archive / 'JU Hamburg Finanzen 0.6 (a).app', '0.6')
        fake_app(self.archive / 'JU Hamburg Finanzen 0.7 (b).app', '0.7')
        moved = build_app.retire_archived_apps(self.archive)
        self.assertEqual([m.name for m in moved], ['JU Hamburg Finanzen 0.6 (a).app-archiv', 'JU Hamburg Finanzen 0.7 (b).app-archiv'])
        self.assertEqual(list(self.archive.glob('*.app')), [])
        self.assertEqual(build_app.retire_archived_apps(self.archive), [])
        self.assertEqual(build_app.retire_archived_apps(self.base / 'fehlt'), [])


class InstallTests(unittest.TestCase):
    """Austausch der App: neue Version wird fertig kopiert, alte archiviert – im Basisordner wird nichts gelöscht."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / 'Daten').mkdir()
        fake_app(self.base / 'JU Hamburg Finanzen.app', '0.17')
        self.built = []

        def fake_build(out_dir, cache, name=None):
            bundle = fake_app(out_dir / name, '0.18')
            self.built.append(bundle)
            return bundle
        self.patch = unittest.mock.patch.object(build_app, 'build', fake_build)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_install_swaps_and_archives(self):
        build_app.install(self.base, self.base / 'cache')
        self.assertEqual(self.built[0].name, build_app.STAGING)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ['Daten', 'JU Hamburg Finanzen.app', 'Sicherungen'])
        info = plistlib.loads((self.base / 'JU Hamburg Finanzen.app' / 'Contents' / 'Info.plist').read_bytes())
        self.assertEqual(info['CFBundleShortVersionString'], '0.18')
        archived = list((self.base / 'Sicherungen' / 'Programmversionen').iterdir())
        self.assertEqual(len(archived), 1)
        self.assertTrue(archived[0].name.startswith('JU Hamburg Finanzen 0.17 ('))

    def test_leftover_is_moved_aside(self):
        (self.base / build_app.STAGING / 'Contents').mkdir(parents=True)
        build_app.install(self.base, self.base / 'cache')
        rests = list((self.base / 'Sicherungen' / 'Programmversionen' / 'Build-Reste').iterdir())
        self.assertEqual(len(rests), 1)
        self.assertFalse(rests[0].name.endswith('.app'))
        self.assertFalse((self.base / build_app.STAGING).exists())
        self.assertTrue((self.base / 'JU Hamburg Finanzen.app').exists())

    def test_old_app_returns_if_swap_fails(self):
        def broken_build(out_dir, cache, name=None):
            return out_dir / name                     # nichts gebaut → Umbenennen scheitert
        with unittest.mock.patch.object(build_app, 'build', broken_build):
            with self.assertRaises(OSError):
                build_app.install(self.base, self.base / 'cache')
        info = plistlib.loads((self.base / 'JU Hamburg Finanzen.app' / 'Contents' / 'Info.plist').read_bytes())
        self.assertEqual(info['CFBundleShortVersionString'], '0.17')
        self.assertEqual(list((self.base / 'Sicherungen' / 'Programmversionen').iterdir()), [])

    def test_stub_falls_back_without_link(self):
        """Startskript: Python über den Verweis in Contents/MacOS, sonst direkt (mit bash geprüft)."""
        import os
        import subprocess
        contents = self.base / 'X.app' / 'Contents'
        for folder in ('MacOS', 'Resources/python/bin', 'Resources/app'):
            (contents / folder).mkdir(parents=True)
        (contents / 'Resources/python/bin/python3').symlink_to(sys.executable)
        (contents / 'Resources/app/launcher.py').write_text('import sys; print(sys.executable)', encoding='utf-8')
        stub = contents / 'MacOS' / 'start'
        stub.write_text(build_app.STUB, encoding='utf-8')
        stub.chmod(0o755)
        link = contents / 'MacOS' / build_app.PYTHON_LINK
        link.symlink_to(Path('..') / 'Resources' / 'python' / 'bin' / 'python3')
        self.assertEqual(subprocess.run([str(stub)], capture_output=True, text=True).stdout.strip(), str(link))
        link.unlink()
        link.symlink_to('fehlt')                       # kaputter Verweis → Rückfall
        out = subprocess.run([str(stub)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, str(contents / 'Resources/python/bin/python3'))
        self.assertTrue(os.access(stub, os.X_OK))

    def test_requires_data_folder(self):
        (self.base / 'Daten').rmdir()
        with self.assertRaises(SystemExit):
            build_app.install(self.base, self.base / 'cache')


if __name__ == '__main__':
    unittest.main(verbosity=2)
