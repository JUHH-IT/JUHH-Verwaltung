"""Automatische tägliche Sicherung der Datenbank beim Programmstart."""
import sqlite3
from datetime import date
from pathlib import Path

PREFIX = 'finanzen-'


def last_daily(backup_dir):
    """Datum (JJJJ-MM-TT) der jüngsten automatischen Sicherung oder ''."""
    try:
        days = [p.name[len(PREFIX):-len('.sqlite3')] for p in Path(backup_dir).glob(f'{PREFIX}*.sqlite3')]
    except OSError:
        return ''
    return max((d for d in days if _datum(d)), default='')   # fremde Dateien (ohne gültiges Datum im Namen) zählen nicht


def _datum(text):
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def daily_backup(db_path, backup_dir, keep=30, today=None):
    """Legt höchstens eine Sicherung pro Tag an und behält die letzten ``keep`` Stände.

    Gibt den Pfad der neuen Sicherung zurück oder None, wenn nichts zu tun war.
    Verwendet die SQLite-Backup-Schnittstelle, damit auch eine geöffnete Datenbank
    konsistent gesichert wird.
    """
    db_path, backup_dir = Path(db_path), Path(backup_dir)
    if not db_path.exists():
        return None
    today = today or date.today()
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f'{PREFIX}{today.isoformat()}.sqlite3'
    if target.exists():
        return None
    partial = target.with_name(target.name + '.teilweise')
    source = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(partial)
        try:
            source.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Integritätsprüfung der Sicherung fehlgeschlagen.')
        finally:
            dest.close()
    finally:
        source.close()
    partial.replace(target)
    for old in sorted(backup_dir.glob(f'{PREFIX}*.sqlite3'))[:-keep]:
        old.unlink()
    return target


def mirror_files(source, target):
    """Neue Dateien (z. B. Belege) in die Sicherung kopieren; vorhandene werden nie überschrieben. Liefert die Anzahl."""
    import shutil
    source, target = Path(source), Path(target)
    if not source.is_dir():
        return 0
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in source.iterdir():
        if f.is_file() and not (target / f.name).exists():
            shutil.copy2(f, target / f.name)
            copied += 1
    return copied


def named_backup(db_path, backup_dir, label):
    """Sicherung vor einer größeren Änderung (z. B. Excel-Übernahme). Gibt den Pfad zurück."""
    from datetime import datetime
    db_path, backup_dir = Path(db_path), Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f'{label}-{datetime.now().strftime("%Y%m%d-%H%M%S")}.sqlite3'
    source = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return target
