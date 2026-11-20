"""Wächter für das Repository (ab 0.33, GitHub): im Quellcode stehen nie Daten, Schlüssel oder echte Kontoangaben.

Geprüft werden alle von Git verwalteten Dateien. Ohne Git (Serverpaket) wird der Test übersprungen.
Fehlermeldungen nennen nur Datei und Zeile, nie den gefundenen Wert – sie stehen sonst im Protokoll von GitHub.
Der Wächter ist ein Netz gegen Versehen, kein Ersatz für die Datenregel in AGENTS.md.
"""
import os
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent

# Dateiarten, die nur Daten oder Geheimnisse enthalten können (Datenbank, Sicherung, Kontoauszug, Liste, Beleg, Schlüssel)
FORBIDDEN_SUFFIXES = {'.sqlite3', '.sqlite', '.db', '.zip', '.gz', '.tar', '.7z', '.xlsx', '.xlsm', '.xls', '.ods', '.csv',
                      '.pdf', '.jpg', '.jpeg', '.heic', '.tif', '.tiff', '.p12', '.pfx', '.pem', '.key', '.env', '.log',
                      '.bak', '.dump', '.sql'}
FORBIDDEN_PARTS = ('.sqlite3', '.sqlite', '.env')          # auch mitten im Namen: sicherung.sqlite3.alt, .env.local
FORBIDDEN_FOLDERS = {'daten', 'sicherungen', 'belege', 'unterlagen', 'wheels'}   # als Ordner an beliebiger Stelle
# erfundene Musterdateien für die Tests (tests/make_fixtures.py) und die leere Vorlage für Kontoauszüge
ALLOWED_FILES = {'tests/fixtures/umsaetze-kopf-oben.xls', 'tests/fixtures/umsaetze-kopf-unten.xls', 'Kontoauszug-Vorlage.csv'}
MAX_BYTES = 600_000

# öffentlich bekannte Beispiel-IBANs (Dokumentation der Banken, Lehrbücher) – nur diese dürfen im Quellcode stehen
EXAMPLE_IBANS = {'DE89370400440532013000', 'DE02120300000000202051', 'DE27100777770209299700', 'DE44500105175407324931',
                 'DE91100000000123456789', 'CH9300762011623852957'}
# Länge und Aufbau je Land (nach Ländercode und Prüfziffer) – der Aufbau hält Zufallstreffer in Code und Prüfsummen fern
IBAN_FORMS = {
    'DE': r'\d{18}', 'AT': r'\d{16}', 'BE': r'\d{12}', 'DK': r'\d{14}', 'ES': r'\d{20}', 'LU': r'\d{3}[A-Z0-9]{13}',
    'PL': r'\d{24}', 'PT': r'\d{21}', 'SE': r'\d{20}', 'CZ': r'\d{20}', 'NL': r'[A-Z]{4}\d{10}', 'GB': r'[A-Z]{4}\d{14}',
    'CH': r'\d{5}[A-Z0-9]{12}', 'LI': r'\d{5}[A-Z0-9]{12}', 'FR': r'\d{10}[A-Z0-9]{11}\d{2}', 'IT': r'[A-Z]\d{10}[A-Z0-9]{12}',
}
IBAN_START = re.compile(r'(?=(' + '|'.join(IBAN_FORMS) + r')(\d{2}))')
SEPARATORS = re.compile(r'[ \t  .\-]')          # Schreibweisen: Leerzeichen, Tab, geschützt, Punkt, Bindestrich

EXAMPLE_MAIL = re.compile(r'(^|\.)(example\.(org|com|net)|[a-z0-9-]+\.example|users\.noreply\.github\.com)$')
MAIL = re.compile(r'([A-Za-z0-9._+-]+)\s?(?:@|%40|\(at\)|\[at\])\s?((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})')
NO_MAIL_ENDINGS = {'png', 'jpg', 'svg', 'js', 'mjs', 'css', 'py', 'md', 'json', 'html', 'sh', 'txt', 'yml', 'git'}
GIT_ADDRESS = ('git', 'github.com')   # Adresse des Repositorys (git@github.com:Konto/Repository.git), keine E-Mail-Adresse
NO_TEXT_SCAN = ('static/vendor/',)   # unveränderte fremde Bibliothek (Prüfsumme in test_karte.py)

SECRETS = [re.compile(p) for p in (
    '-----BEGIN' + r' [A-Z ]*PRIVATE KEY-----',          # privater Schlüssel
    'gh[pousr]' + r'_[A-Za-z0-9]{30,}', 'github' + r'_pat_[A-Za-z0-9_]{30,}',   # GitHub-Zugang
    'tskey' + r'-[a-z]+-[A-Za-z0-9-]{16,}',              # Tailscale-Schlüssel
    'sk' + r'-ant-[A-Za-z0-9_-]{20,}', 'AKIA' + r'[0-9A-Z]{16}',
)]


def iban_valid(text):
    """Prüfziffer nach ISO 13616 – so fallen Zeichenfolgen, die nur wie eine IBAN aussehen, nicht auf."""
    raw = SEPARATORS.sub('', text).upper()
    if not 15 <= len(raw) <= 34 or not raw.isalnum() or not raw.isascii():
        return False
    moved = raw[4:] + raw[:4]
    return int(''.join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def ibans_in(line):
    """Gültige IBANs einer Zeile – auch klein geschrieben, mit Trennzeichen oder direkt an ein Wort gehängt."""
    text = SEPARATORS.sub('', line).upper()
    found = set()
    for match in IBAN_START.finditer(text):
        country = match.group(1)
        rest = re.match(IBAN_FORMS[country], text[match.start() + 4:])
        if rest:
            candidate = text[match.start():match.start() + 4 + rest.end()]
            if iban_valid(candidate):
                found.add(candidate)
    return found


def mail_domains(line):
    for user, domain in MAIL.findall(line):
        domain = domain.lower()
        if (user, domain) == GIT_ADDRESS or domain.rsplit('.', 1)[-1] in NO_MAIL_ENDINGS:
            continue
        yield domain


def forbidden_name(name):
    """Grund, warum eine Datei dieses Namens nicht ins Repository gehört – oder None."""
    if name in ALLOWED_FILES:
        return None
    path = pathlib.PurePosixPath(name)
    lower = path.name.lower()
    if any(part.lower() in FORBIDDEN_FOLDERS for part in path.parts[:-1]):
        return 'Datenordner gehören nicht ins Repository'
    if path.suffix.lower() in FORBIDDEN_SUFFIXES or lower in FORBIDDEN_SUFFIXES:
        return 'Dateiart nur für Daten oder Geheimnisse – nicht ins Repository'
    # Vorlagen mit erfundenen Werten enden auf .beispiel (server/ju-finanzen.env.beispiel)
    if not lower.endswith('.beispiel') and any(part in lower for part in FORBIDDEN_PARTS):
        return 'Dateiart nur für Daten oder Geheimnisse – nicht ins Repository'
    return None


def decode(data):
    """Text einer Datei oder None für Binärdateien; Tabellen-Exporte in UTF-16 zählen als Text."""
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16', errors='replace')
    if b'\0' in data[:8192]:
        return None
    return data.decode('utf-8', errors='replace')


def tracked_files():
    if not shutil.which('git') or not (ROOT / '.git').exists():
        return None
    # ohne GIT_*-Variablen: in einem Git-Hook zeigt GIT_DIR sonst auf ein anderes Repository
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    try:
        out = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, check=True, timeout=30, env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [name for name in out.decode('utf-8').split('\0') if name]


class RepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = tracked_files()
        if files is None:
            raise unittest.SkipTest('kein Git-Stand (Serverpaket)')
        # gelöschte, aber noch gelistete Dateien können nichts mehr enthalten
        cls.files = [name for name in files if (ROOT / name).is_file()]

    def texts(self):
        for name in self.files:
            if name.startswith(NO_TEXT_SCAN):
                continue
            text = decode((ROOT / name).read_bytes())
            if text is not None:
                yield name, text

    def test_no_data_files(self):
        self.assertGreater(len(self.files), 100)
        for name in self.files:
            with self.subTest(file=name):
                self.assertIsNone(forbidden_name(name))
                self.assertLessEqual((ROOT / name).stat().st_size, MAX_BYTES, 'ungewöhnlich große Datei')

    def test_only_example_ibans(self):
        for name, text in self.texts():
            for number, line in enumerate(text.splitlines(), 1):
                if ibans_in(line) - EXAMPLE_IBANS:
                    self.fail(f'{name}:{number}: gültige IBAN, die keine bekannte Beispiel-IBAN ist')

    def test_only_example_mail_addresses(self):
        for name, text in self.texts():
            for number, line in enumerate(text.splitlines(), 1):
                for domain in mail_domains(line):
                    if not EXAMPLE_MAIL.search(domain):
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


class GuardLogicTests(unittest.TestCase):
    """Die Prüflogik selbst – läuft auch ohne Git. Die Proben sind aus Teilen zusammengesetzt, damit sie hier nicht anschlagen."""
    FAKE = 'DE75' + '5121' + '0800' + '1245' + '1261' + '99'      # verbreitete Test-IBAN, nicht in EXAMPLE_IBANS
    FAKE_AT = 'AT61' + '1904' + '3002' + '3457' + '3201'

    def test_iban_check(self):
        self.assertTrue(iban_valid('DE89 3704 0044 0532 0130 00'))
        self.assertFalse(iban_valid('DE88 3704 0044 0532 0130 00'))
        self.assertFalse(iban_valid('DE98ZZZ09999999999'))   # Gläubiger-ID ist keine IBAN
        self.assertTrue(all(iban_valid(i) for i in EXAMPLE_IBANS))
        self.assertTrue(iban_valid(self.FAKE) and iban_valid(self.FAKE_AT))

    def test_iban_spellings_are_found(self):
        grouped = ' '.join(self.FAKE[i:i + 4] for i in range(0, 22, 4))
        for line in (self.FAKE, grouped, grouped.lower(), grouped.replace(' ', '-'), grouped.replace(' ', '.'),
                     grouped.replace(' ', '\t'), grouped.replace(' ', ' '), 'IBAN' + self.FAKE, f'iban: "{grouped}",',
                     ' '.join(self.FAKE_AT[i:i + 4] for i in range(0, 20, 4)) + ' BIC', self.FAKE_AT + ' 1250'):
            with self.subTest(line=len(line)):
                self.assertEqual(len(ibans_in(line)), 1)
        self.assertEqual(ibans_in('DE89 3704 0044 0532 0130 00'), {'DE89370400440532013000'})

    def test_no_false_iban_alarms(self):
        for line in ('sha256:3e17ed538242334bf70832644a32a7aae3d83b57567f9fd60a26257e992b79ba', 'DE98ZZZ09999999999',
                     'DE88 3704 0044 0532 0130 00', 'Version 0.33 vom 12.11.2026, Konto 1370 an 8100', 'NL18 ist kein Konto'):
            with self.subTest(line=line[:12]):
                self.assertEqual(ibans_in(line), set())

    def test_mail_detection(self):
        hit = lambda line: [d for d in mail_domains(line) if not EXAMPLE_MAIL.search(d)]
        for line in ('jemand@' + 'gmx.de', 'jemand (at) ' + 'web.de', 'jemand%40' + 'web.de', 'A.B@' + 'Firma-X.com'):
            with self.subTest(line=len(line)):
                self.assertEqual(len(hit(line)), 1)
        for line in ('kasse@example.org', 'x@firma.example', 'git@github.com:Konto/Repo.git', 'logo@2x.png', 'paket@1.2.3',
                     'actions/checkout@v5', '49.1+dev@users.noreply.github.com'):
            with self.subTest(line=line):
                self.assertEqual(hit(line), [])

    def test_file_names(self):
        for name in ('.env', 'konfig/.env', '.env.local', 'sicherung.sqlite3.alt', 'x/Daten/a.txt', 'daten/a.py', 'export.csv',
                     'Mitglieder.XLSX', 'beleg.jpg', 'Sicherungen/2026.zip', 'server/wheels/a.whl', 'schluessel.pem', 'a.log'):
            with self.subTest(name=name):
                self.assertIsNotNone(forbidden_name(name))
        for name in ('app.py', 'static/js/main.js', 'server/ju-finanzen.env.beispiel', 'tests/fixtures/umsaetze-kopf-oben.xls',
                     'Kontoauszug-Vorlage.csv', 'static/img/logo-rechnung.png', 'adressdaten.js', 'tests/beispiel-xrechnung.xml'):
            with self.subTest(name=name):
                self.assertIsNone(forbidden_name(name))

    def test_utf16_counts_as_text(self):
        self.assertIn('IBAN', decode('IBAN;Name\r\n'.encode('utf-16')))
        self.assertIsNone(decode(b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
