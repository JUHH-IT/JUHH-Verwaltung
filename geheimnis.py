"""Passwort für den E-Mail-Versand: auf dem Server in einer Datei, sonst nur im Arbeitsspeicher.

Das Passwort wird nie in der Datenbank, in Sicherungen oder im Protokoll gespeichert.
Ablage: Datei im Ordner aus JU_FINANZEN_GEHEIMNISSE (nur für den Dienstbenutzer lesbar, 0600, außerhalb von Daten und
Sicherungen) – Ort „datei“. Ohne diesen Ordner (oder auf Wunsch) gilt das Passwort nur bis zum Neustart des Servers.
Die Ablage wird je Konto höchstens einmal gelesen und das Ergebnis im Arbeitsspeicher gehalten.
"""
import json
import os
import tempfile
import threading
from pathlib import Path

SERVICE = 'JU Hamburg Finanzen – E-Mail-Versand'
_cache = {}  # Konto → (Passwort oder None, Ort: 'datei' | 'sitzung' | None)
_lock = threading.Lock()


class FileStore:
    """Ablage auf dem Server: eine JSON-Datei {Konto: Passwort} mit Rechten 0600 im eigenen Ordner."""
    label = 'datei'

    def __init__(self, folder):
        self.folder = Path(folder)
        self.file = self.folder / 'e-mail-passwort.json'

    def _read(self):
        try:
            data = json.loads(self.file.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data):
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.folder, prefix='.neu-')   # mkstemp legt mit 0600 an
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.file)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get_password(self, service, account):
        value = self._read().get(account)
        return value if isinstance(value, str) else None

    def set_password(self, service, account, password):
        data = self._read()
        data[account] = password
        self._write(data)

    def delete_password(self, service, account):
        data = self._read()
        if data.pop(account, None) is not None:
            self._write(data)


def _ablage():
    folder = os.environ.get('JU_FINANZEN_GEHEIMNISSE')
    return FileStore(folder) if folder else None


def available():
    return _ablage() is not None


def _lookup(account):
    """Einmal je Konto in der Ablage nachsehen; danach aus dem Arbeitsspeicher."""
    with _lock:
        if account in _cache:
            return _cache[account]
    value, origin = None, None
    backend = _ablage()
    if backend is not None and account:
        try:
            value = backend.get_password(SERVICE, account)
        except Exception:
            value = None
        origin = backend.label if value else None
    with _lock:
        _cache.setdefault(account, (value, origin))
        return _cache[account]


def save(account, password, remember=True):
    """Speichert das Passwort; liefert 'datei' oder 'sitzung'."""
    if not account:
        raise ValueError('Zuerst den Benutzernamen des E-Mail-Kontos speichern.')
    if not password or len(password) > 512 or any(c in password for c in '\r\n\0'):
        raise ValueError('Ungültiges Passwort.')
    backend = _ablage()
    origin = 'sitzung'
    if backend is not None:
        try:
            if remember:
                backend.set_password(SERVICE, account, password)
                origin = backend.label
            else:
                backend.delete_password(SERVICE, account)  # älteren Eintrag nicht weiterverwenden
        except Exception:
            pass
    with _lock:
        _cache[account] = (password, origin)
    return origin


def load(account):
    return _lookup(account)[0] if account else None


def where(account):
    """'datei', 'sitzung' oder None – ohne das Passwort preiszugeben."""
    return _lookup(account)[1] if account else None


def forget(account):
    backend = _ablage()
    if backend is not None and account:
        try:
            backend.delete_password(SERVICE, account)
        except Exception:
            pass
    with _lock:
        _cache[account] = (None, None)
