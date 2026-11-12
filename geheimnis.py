"""Passwort für den E-Mail-Versand: im Schlüsselbund des Betriebssystems, sonst nur im Arbeitsspeicher.

Das Passwort wird nie in der Datenbank, in Sicherungen oder im Protokoll gespeichert.
macOS: Schlüsselbund (Keychain), Windows: Anmeldeinformationsverwaltung – jeweils über „keyring“.
Server (Ubuntu): Datei im Ordner aus JU_FINANZEN_GEHEIMNISSE (nur für den Dienstbenutzer lesbar, 0600,
außerhalb von Daten und Sicherungen) – Ort „datei“.
Ohne verfügbaren Schlüsselbund (oder auf Wunsch) gilt das Passwort nur bis zum Beenden des Programms.
Der Schlüsselbund wird je Konto höchstens einmal gelesen und das Ergebnis im Arbeitsspeicher gehalten
(vermeidet wiederholte Rückfragen von macOS).
"""
import json
import os
import tempfile
import threading
from pathlib import Path

SERVICE = 'JU Hamburg Finanzen – E-Mail-Versand'
_cache = {}  # Konto → (Passwort oder None, Ort: 'schluesselbund' | 'datei' | 'sitzung' | None)
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


def _keyring():
    if os.environ.get('JU_FINANZEN_GEHEIMNISSE'):
        return FileStore(os.environ['JU_FINANZEN_GEHEIMNISSE'])
    if os.environ.get('JU_FINANZEN_OHNE_SCHLUESSELBUND'):
        return None
    try:
        import keyring
        from keyring.backends import fail
        backend = keyring.get_keyring()
        name = type(backend).__module__ + '.' + type(backend).__name__
        if isinstance(backend, fail.Keyring) or 'null' in name.lower() or ('chainer' in name.lower() and not backend.backends):
            return None
        return backend
    except Exception:
        return None


def available():
    return _keyring() is not None


def _lookup(account):
    """Einmal je Konto im Schlüsselbund nachsehen; danach aus dem Arbeitsspeicher."""
    with _lock:
        if account in _cache:
            return _cache[account]
    value, origin = None, None
    backend = _keyring()
    if backend is not None and account:
        try:
            value = backend.get_password(SERVICE, account)
        except Exception:
            value = None
        origin = getattr(backend, 'label', 'schluesselbund') if value else None
    with _lock:
        _cache.setdefault(account, (value, origin))
        return _cache[account]


def save(account, password, remember=True):
    """Speichert das Passwort; liefert 'schluesselbund', 'datei' oder 'sitzung'."""
    if not account:
        raise ValueError('Zuerst den Benutzernamen des E-Mail-Kontos speichern.')
    if not password or len(password) > 512 or any(c in password for c in '\r\n\0'):
        raise ValueError('Ungültiges Passwort.')
    backend = _keyring()
    origin = 'sitzung'
    if backend is not None:
        try:
            if remember:
                backend.set_password(SERVICE, account, password)
                origin = getattr(backend, 'label', 'schluesselbund')
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
    """'schluesselbund', 'datei', 'sitzung' oder None – ohne das Passwort preiszugeben."""
    return _lookup(account)[1] if account else None


def forget(account):
    backend = _keyring()
    if backend is not None and account:
        try:
            backend.delete_password(SERVICE, account)
        except Exception:
            pass
    with _lock:
        _cache[account] = (None, None)
