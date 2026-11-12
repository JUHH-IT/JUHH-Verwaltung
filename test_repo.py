"""Wächter für das Repository (ab 0.33, GitHub): im Quellcode stehen nie Daten, Schlüssel oder echte Kontoangaben.

Geprüft werden alle von Git verwalteten Dateien. Ohne Git (Serverpaket, Mac-App) wird der Test übersprungen.
Fehlermeldungen nennen nur Datei und Zeile, nie den gefundenen Wert – sie stehen sonst im Protokoll von GitHub.
"""
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent

# Dateiarten, die nur Daten oder Geheimnisse enthalten können (Datenbank, Sicherung, Kontoauszug, Beleg, Schlüssel)
FORBIDDEN_SUFFIXES = {'.sqlite3', '.sqlite', '.db', '.zip', '.gz', '.tar', '.7z', '.xlsx', '.xlsm', '.xls', '.ods',
                      '.pdf', '.p12', '.pfx', '.pem', '.key', '.env', '.log', '.bak', '.dump'}
FORBIDDEN_FOLDERS = ('Daten/', 'Sicherungen/', 'Belege/', 'Unterlagen/', 'server/wheels/')
# erfundene Musterdateien für die Tests (tests/make_fixtures.py)
ALLOWED_FILES = {'tests/fixtures/umsaetze-kopf-oben.xls', 'tests/fixtures/umsaetze-kopf-unten.xls'}
MAX_BYTES = 600_000

# öffentlich bekannte Beispiel-IBANs (Dokumentation der Banken, Lehrbücher) – nur diese dürfen im Quellcode stehen
EXAMPLE_IBANS = {'DE89370400440532013000', 'DE02120300000000202051', 'DE27100777770209299700', 'DE44500105175407324931',
                 'DE91100000000123456789', 'CH9300762011623852957'}
EXAMPLE_MAIL = re.compile(r'(^|\.)(example\.(org|com|net)|[a-z0-9-]+\.example|users\.noreply\.github\.com)$')
NO_TEXT_SCAN = ('static/vendor/',)   # unveränderte fremde Bibliothek (Prüfsumme in test_karte.py)

IBAN = re.compile(r'(?<![A-Za-z0-9])([A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?)(?![A-Za-z0-9])')
MAIL = re.compile(r'([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)')
GIT_ADDRESS = ('git', 'github.com')   # Adresse des Repositorys (git@github.com:Konto/Repository.git), keine E-Mail-Adresse
SECRETS = [re.compile(p) for p in (
    '-----BEGIN' + r' [A-Z ]*PRIVATE KEY-----',          # privater Schlüssel
    'gh[pousr]' + r'_[A-Za-z0-9]{30,}', 'github' + r'_pat_[A-Za-z0-9_]{30,}',   # GitHub-Zugang
    'tskey' + r'-[a-z]+-[A-Za-z0-9-]{16,}',              # Tailscale-Schlüssel
    'sk' + r'-ant-[A-Za-z0-9_-]{20,}', 'AKIA' + r'[0-9A-Z]{16}',
)]


def iban_valid(text):
    """Prüfziffer nach ISO 13616 – so fallen Zeichenfolgen, die nur wie eine IBAN aussehen, nicht auf."""
    raw = text.replace(' ', '')
    if not 15 <= len(raw) <= 34:
        return False
    moved = raw[4:] + raw[:4]
    return int(''.join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def tracked_files():
    if not shutil.which('git') or not (ROOT / '.git').exists():
        return None
    try:
        out = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [name for name in out.decode('utf-8').split('\0') if name]


class RepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = tracked_files()
        if cls.files is None:
            raise unittest.SkipTest('kein Git-Stand (Serverpaket oder Mac-App)')

    def texts(self):
        for name in self.files:
            if name.startswith(NO_TEXT_SCAN):
                continue
            data = (ROOT / name).read_bytes()
            if b'\0' in data[:8192]:
                continue
            yield name, data.decode('utf-8', errors='replace')

    def test_no_data_files(self):
        self.assertGreater(len(self.files), 100)
        for name in self.files:
            with self.subTest(file=name):
                if name in ALLOWED_FILES:
                    continue
                self.assertFalse(name.startswith(FORBIDDEN_FOLDERS), 'Datenordner gehören nicht ins Repository')
                self.assertNotIn(pathlib.PurePosixPath(name).suffix.lower(), FORBIDDEN_SUFFIXES,
                                 'Dateiart nur für Daten oder Geheimnisse – nicht ins Repository')
                self.assertLessEqual((ROOT / name).stat().st_size, MAX_BYTES, 'ungewöhnlich große Datei')

    def test_only_example_ibans(self):
        for name, text in self.texts():
            for number, line in enumerate(text.splitlines(), 1):
                for found in IBAN.findall(line):
                    if iban_valid(found) and found.replace(' ', '') not in EXAMPLE_IBANS:
                        self.fail(f'{name}:{number}: gültige IBAN, die keine bekannte Beispiel-IBAN ist')

    def test_only_example_mail_addresses(self):
        for name, text in self.texts():
            for number, line in enumerate(text.splitlines(), 1):
                for user, domain in MAIL.findall(line):
                    if (user, domain) != GIT_ADDRESS and not EXAMPLE_MAIL.search(domain.lower()):
                        self.fail(f'{name}:{number}: E-Mail-Adresse außerhalb der Beispiel-Domains (example.org, *.example)')

    def test_no_secrets(self):
        for name, text in self.texts():
            for number, line in enumerate(text.splitlines(), 1):
                if any(pattern.search(line) for pattern in SECRETS):
                    self.fail(f'{name}:{number}: sieht nach einem Schlüssel oder Zugangstoken aus')

    def test_gitignore_keeps_data_out(self):
        ignore = (ROOT / '.gitignore').read_text(encoding='utf-8').split()
        for entry in ('Daten/', 'Sicherungen/', '*.sqlite3', '*.zip', '*.xlsx', '*.pdf', '*.env', '*.log', '*.pem', '*.key'):
            self.assertIn(entry, ignore)

    def test_iban_check(self):
        self.assertTrue(iban_valid('DE89 3704 0044 0532 0130 00'))
        self.assertFalse(iban_valid('DE88 3704 0044 0532 0130 00'))
        self.assertFalse(iban_valid('DE98ZZZ09999999999'))   # Gläubiger-ID ist keine IBAN
        self.assertTrue(all(iban_valid(i) for i in EXAMPLE_IBANS))


if __name__ == '__main__':
    unittest.main(verbosity=2)
