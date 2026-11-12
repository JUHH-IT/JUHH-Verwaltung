"""Auslieferung der Oberfläche: nur Dateien aus static/, keine Pfadausbrüche."""
import unittest

import app


class StaticTests(unittest.TestCase):
    def test_allowed_files(self):
        self.assertEqual(app.static_file('/').name, 'index.html')
        self.assertEqual(app.static_file('/js/main.js').name, 'main.js')
        self.assertEqual(app.static_file('/css/app.css').name, 'app.css')
        self.assertEqual(app.static_file('/JU-Hamburg-Finanzen.svg').suffix, '.svg')

    def test_rejected_paths(self):
        for path in ['/../app.py', '/js/../../core.py', '/.git/config', '/js/.hidden.js', '//etc/passwd',
                     '/js/main.py', '/nicht-da.js', '/js/', '/%2e%2e/app.py']:
            with self.subTest(path=path):
                self.assertIsNone(app.static_file(path))

    def test_every_module_is_served(self):
        for file in (app.STATIC / 'js').rglob('*.js'):
            rel = '/' + file.relative_to(app.STATIC).as_posix()
            with self.subTest(rel=rel):
                self.assertEqual(app.static_file(rel), file.resolve())


if __name__ == '__main__':
    unittest.main(verbosity=2)
