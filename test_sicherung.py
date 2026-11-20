"""Tests für die tägliche automatische Sicherung (sicherung.py)."""
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import sicherung


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


if __name__ == '__main__':
    unittest.main()
