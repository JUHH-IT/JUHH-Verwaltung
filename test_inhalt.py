"""INHALT.md muss zum Code passen (sonst suchen Entwickler und Agenten an der falschen Stelle); schneller Testlauf."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'werkzeuge'))
import inhalt   # noqa: E402
import tests as testlauf   # noqa: E402


class InhaltTests(unittest.TestCase):
    def test_inhaltsverzeichnis_ist_aktuell(self):
        self.assertEqual((ROOT / 'INHALT.md').read_text(encoding='utf-8'), inhalt.inhalt(),
                         'INHALT.md ist veraltet – bitte neu erzeugen: python3 werkzeuge/inhalt.py')

    def test_jedes_modul_hat_einen_bereich(self):
        self.assertNotIn('## Nicht zugeordnet', inhalt.inhalt(), 'neues Modul in werkzeuge/inhalt.py (BEREICHE) eintragen')

    def test_betroffene_tests(self):
        graph = {'test_a': {'a'}, 'test_b': {'b'}, 'a': {'kern'}, 'b': set(), 'kern': set(), 'test_repo': set()}
        self.assertEqual(testlauf.betroffen(['kern.py'], graph)[0], ['test_repo'])                # nur direkte Importe
        self.assertEqual(testlauf.betroffen(['kern.py'], graph, tief=True)[0], ['test_a', 'test_repo'])
        self.assertEqual(testlauf.betroffen(['a.py'], graph)[0], ['test_a', 'test_repo'])
        self.assertEqual(testlauf.betroffen(['b.py'], graph)[0], ['test_b', 'test_repo'])
        self.assertEqual(testlauf.betroffen(['test_a.py'], graph)[0], ['test_a', 'test_repo'])
        self.assertEqual(testlauf.betroffen([], graph), ([], []))
        tests, hinweise = testlauf.betroffen(['static/js/main.js'], graph)
        self.assertEqual(tests, ['test_repo'])
        self.assertIn('ui_smoke', hinweise[0])


if __name__ == '__main__':
    unittest.main()
