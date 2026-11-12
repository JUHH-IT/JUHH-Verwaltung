"""Anmeldung mit Benutzername und Passwort im Serverbetrieb (Ubuntu-Server hinter Tailscale).

- Passwörter nur als scrypt-Hash in `users.password` (`scrypt$n$r$p$salz$hash`); leere oder ältere Einträge
  (Mac-Anmeldung über macOS, Version ≤ 0.19) gelten nie als gültiges Passwort – gesetzt wird es über
  `verwaltung.py passwort <Name>` auf dem Server oder „Passwort ändern“ in den Stammdaten.
- Sperre nach Fehlversuchen je Gerät (Tailscale-Login bzw. Adresse) und je Benutzername: ab 5 Fehlversuchen in
  15 Minuten wachsende Wartezeit bis 15 Minuten; zusätzlich eine Obergrenze für alle zusammen. Ein Versuch wird schon
  vor der Passwortprüfung vorgemerkt (gleichzeitige Anfragen zählen mit) und nach Erfolg gestrichen. Die Fehlversuche
  überstehen einen Neustart (Datei im Datenordner).
- Höchstens zwei scrypt-Prüfungen gleichzeitig (Speicher), gleiche Rechenzeit für unbekannte Namen.
- Passwörter werden nie protokolliert, gespeichert oder zurückgegeben.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

MIN_LENGTH = 12
MAX_LENGTH = 256
_N, _R, _P = 2 ** 16, 8, 2          # OWASP: N=2^16, r=8, p=2 (≈ 64 MiB, ~0,2 s je Prüfung)
_MAXMEM = 160 * 1024 * 1024
_DUMMY = None                        # Hash für unbekannte Namen (gleiche Rechenzeit, kein Rückschluss auf Benutzer)
_SLOTS = threading.BoundedSemaphore(2)

WINDOW = 15 * 60
FREE_ATTEMPTS = 5
MAX_WAIT = 15 * 60
GLOBAL_ATTEMPTS = 30                 # alle Geräte zusammen in 15 Minuten


def check_new(password):
    """Anforderungen an ein neues Passwort; Fehlermeldung als ValueError."""
    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        raise ValueError(f'Das Passwort muss mindestens {MIN_LENGTH} Zeichen lang sein.')
    if len(password) > MAX_LENGTH or any(c in password for c in '\r\n\0'):
        raise ValueError('Ungültiges Passwort.')
    if len(set(password)) < 5:
        raise ValueError('Das Passwort ist zu einfach.')


def _derive(password, salt, n, r, p, length):
    with _SLOTS:   # Speicherbedarf begrenzen: höchstens zwei Prüfungen gleichzeitig
        return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=length)


def hash_password(password):
    check_new(password)
    salt = secrets.token_bytes(16)
    return f'scrypt${_N}${_R}${_P}${salt.hex()}${_derive(password, salt, _N, _R, _P, 32).hex()}'


def is_set(stored):
    """Gilt der gespeicherte Eintrag als Passwort dieses Verfahrens?"""
    parts = str(stored or '').split('$')
    return len(parts) == 6 and parts[0] == 'scrypt'


def verify(password, stored):
    if not isinstance(password, str) or not password or len(password) > MAX_LENGTH or not is_set(stored):
        return False
    try:
        _, n, r, p, salt, digest = str(stored).split('$')
        n, r, p, salt, digest = int(n), int(r), int(p), bytes.fromhex(salt), bytes.fromhex(digest)
        if not (2 ** 10 <= n <= 2 ** 17 and 1 <= r <= 16 and 1 <= p <= 4 and 128 * r * n * p <= _MAXMEM and 16 <= len(salt) <= 64 and 16 <= len(digest) <= 64):
            return False
        return hmac.compare_digest(_derive(password, salt, n, r, p, len(digest)), digest)
    except (ValueError, OverflowError, MemoryError):
        return False


def burn_time(password):
    """Bei unbekanntem Benutzernamen oder unbrauchbarer Eingabe genauso lange rechnen wie bei einer echten Prüfung."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_urlsafe(24))
    usable = isinstance(password, str) and 0 < len(password) <= MAX_LENGTH
    verify(password if usable else 'x' * 16, _DUMMY)


def find_user(db, name):
    """Benutzer zu einem Namen (Groß-/Kleinschreibung und Leerzeichen am Rand egal)."""
    name = ' '.join(str(name or '').split())
    if not name or len(name) > 80:
        return None
    return db.execute('SELECT * FROM users WHERE lower(name)=lower(?)', (name,)).fetchone()


def check_login(db, name, password):
    """Name des Benutzers bei richtigem Passwort, sonst None – in jedem Fall mit einer vollen scrypt-Prüfung."""
    user = find_user(db, name)
    usable = isinstance(password, str) and 0 < len(password) <= MAX_LENGTH
    active = bool(user) and ('active' not in user.keys() or user['active'])   # deaktivierte Personen (rollen.py) melden sich nicht an
    if not user or not active or not is_set(user['password']) or not usable:
        burn_time(password)
        return None
    return user['name'] if verify(password, user['password']) else None


def fingerprint(stored):
    """Kurzer Fingerabdruck des gespeicherten Hashes: Sitzungen enden, sobald das Passwort neu gesetzt wird."""
    return hashlib.sha256(str(stored or '').encode()).hexdigest()[:16]


def set_password(db, name, password, actor=None, create=False):
    """Passwort setzen (Benutzer ggf. anlegen); schreibt einen Eintrag ins Änderungsprotokoll ohne das Passwort."""
    clean = ' '.join(str(name or '').split())
    if not clean or len(clean) > 80 or any(c in clean for c in '<>"\'\\'):
        raise ValueError('Ungültiger Benutzername.')
    hashed = hash_password(password)
    with db:
        db.execute('BEGIN IMMEDIATE')
        user = find_user(db, clean)
        if user:
            db.execute('UPDATE users SET password=? WHERE id=?', (hashed, user['id']))
            clean = user['name']
            what = 'Passwort geändert'
        elif create:
            created = db.execute('INSERT INTO users(name,password) VALUES(?,?)', (clean, hashed)).lastrowid
            what = 'Benutzer angelegt (Serverbetrieb)'
        else:
            raise ValueError('Benutzer nicht gefunden.')
        db.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)',
                   (datetime.now().isoformat(timespec='seconds'), actor or clean, what, json.dumps({'benutzer': clean}, ensure_ascii=False)))
        if create:   # nur über den Verwaltungsbefehl auf dem Server: die erste Person, die sich anmelden kann, bekommt alle Rechte
            import rollen
            rollen.on_user_created(db, user['id'] if user else created)   # in derselben Transaktion wie das Anlegen
    return clean


class Throttle:
    """Sperre nach wiederholten Fehlversuchen je Schlüssel (Gerät, Benutzername) und für alle zusammen.

    attempt(keys) merkt den Versuch unter Lock vor und liefert 0 – oder die Wartezeit in Sekunden, dann ohne Vormerkung.
    succeeded(keys) streicht die Fehlversuche dieser Schlüssel (die Obergrenze für alle zählt weiter herunter).
    path: Datei für die Fehlversuche (überstehen einen Neustart); None = nur im Arbeitsspeicher."""

    def __init__(self, clock=time.time, path=None):
        self.clock = clock
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.keys = {}
        self.all = []
        self._load()

    def _load(self):
        if not self.path:
            return
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            now = self.clock()
            self.all = [float(t) for t in data.get('all', []) if now - float(t) < WINDOW]
            self.keys = {str(k): [float(t) for t in v if now - float(t) < WINDOW] for k, v in data.get('keys', {}).items()}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self):
        if not self.path:
            return
        try:
            tmp = self.path.with_name(self.path.name + '.neu')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'all': self.all, 'keys': {k: v for k, v in self.keys.items() if v}}, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _prune(self, now):
        self.all = [t for t in self.all if now - t < WINDOW]
        for k in list(self.keys):
            self.keys[k] = [t for t in self.keys[k] if now - t < WINDOW]
            if not self.keys[k]:
                del self.keys[k]

    @staticmethod
    def _pause(times, free, now):
        extra = len(times) - free
        if extra < 0:
            return 0
        return max(0, int(times[-1] + min(MAX_WAIT, 30 * 2 ** extra) - now + 0.999))

    def wait(self, keys=('*',)):
        """Sekunden bis zum nächsten erlaubten Versuch für diese Schlüssel (0 = sofort), ohne Vormerkung."""
        with self.lock:
            now = self.clock()
            self._prune(now)
            return max([self._pause(self.all, GLOBAL_ATTEMPTS, now)] + [self._pause(self.keys.get(k, []), FREE_ATTEMPTS, now) for k in keys])

    def attempt(self, keys=('*',)):
        with self.lock:
            now = self.clock()
            self._prune(now)
            wait = max([self._pause(self.all, GLOBAL_ATTEMPTS, now)] + [self._pause(self.keys.get(k, []), FREE_ATTEMPTS, now) for k in keys])
            if wait:
                return wait
            self.all.append(now)
            for k in keys:
                self.keys.setdefault(k, []).append(now)
            self._save()
            return 0

    def failed(self, keys=('*',)):
        """Fehlversuch ohne vorherige Vormerkung (z. B. falsches altes Passwort beim Ändern)."""
        with self.lock:
            now = self.clock()
            self.all.append(now)
            for k in keys:
                self.keys.setdefault(k, []).append(now)
            self._save()

    def succeeded(self, keys=('*',)):
        with self.lock:
            now = self.clock()
            for k in keys:
                self.keys.pop(k, None)
            if self.all:
                self.all.pop()     # der vorgemerkte, erfolgreiche Versuch zählt nicht als Fehlversuch
            self._prune(now)
            self._save()
