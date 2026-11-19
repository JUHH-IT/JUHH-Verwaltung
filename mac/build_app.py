"""Baut „JU Hamburg Finanzen.app“ für macOS (Apple Silicon) aus dem Quellcode.

    python3 mac/build_app.py --out build              # nur bauen
    python3 mac/build_app.py --install <Basisordner>  # bauen und installieren

Die App ist eigenständig: Sie enthält eine eigene Python-Laufzeit (python-build-standalone,
Prüfsumme festgelegt) und die benötigten Bibliotheken. Gebaut wird in einem lokalen Arbeitsordner;
erst die fertige App wird an ihren Platz kopiert (dort wird nichts gelöscht). Beim Installieren wird eine
vorhandene App nicht gelöscht, sondern nach Sicherungen/Programmversionen verschoben – mit der
Endung „.app-archiv“, damit macOS sie nicht mehr als startbare App führt (Spotlight, Start über
die Bundle-ID). Zum Starten einer alten Version die Endung wieder in „.app“ ändern.
Läuft unter macOS und Linux (Bibliotheken werden für macOS/arm64 heruntergeladen).
"""
import argparse
import datetime
import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_NAME = 'JU Hamburg Finanzen'
BUNDLE_ID = 'de.lennertgust.ju-hamburg-finanzen'
PYTHON_VERSION = '3.13'
PYTHON_ARCHIVE = 'cpython-3.13.18+20261026-aarch64-apple-darwin-install_only_stripped.tar.gz'
PYTHON_URL = ('https://github.com/astral-sh/python-build-standalone/releases/download/20261026/'
              'cpython-3.13.18%2B20261026-aarch64-apple-darwin-install_only_stripped.tar.gz')
PYTHON_SHA256 = '8392b6ea51d94391befb2c31ebe75463f045d0b69dfcf5e1bb3234e1f5fa3405'
APP_FILES = ['app.py', 'core.py', 'formate.py', 'stammdaten.py', 'rechnung_pdf.py', 'versand.py', 'geheimnis.py', 'verbindlichkeiten.py', 'freigabe.py', 'pdfschrift.py', 'lastschrift.py', 'kvbudget.py', 'kvabgleich.py', 'berichte.py', 'erechnung.py', 'erstattung.py',
             'schriften/LiberationSans-Regular.ttf', 'schriften/LiberationSans-Bold.ttf', 'schriften/LICENSE-Liberation.txt',
             'bankimport.py', 'uebernahme.py', 'haushalt.py', 'workflow.py', 'sicherung.py', 'bereinigung.py',
             'kontenrahmen.py', 'mitglieder.py', 'gliederung.py', 'sepadatei.py', 'auswertung.py', 'wahlrecht.py', 'opliste.py', 'klaerfaelle.py', 'rollen.py', 'sicherheit.py', 'schutz.py', 'adressen.py', 'karte.py', 'karte-gebiete.json', 'beitraege.py', 'mitgliederkonto.py', 'kasse.py', 'rpj.py', 'zugang.py', 'verwaltung.py', 'keycloak.py',
             'haushalt-vorlage.json', 'Kontoauszug-Vorlage.csv']
# Python wird über einen Verweis in Contents/MacOS gestartet: So erkennt macOS den Prozess als diese App
# (Name in der Menüleiste, Symbol, Info.plist) – wichtig für das eigene Programmfenster (mac/fenster.py).
# Python findet seine Bibliotheken trotzdem, weil es Verweise auf die echte Programmdatei auflöst.
PYTHON_LINK = 'python3'
MAC_FILES = ['launcher.py', 'fenster.py', 'anmeldung.py']   # aus mac/ nach Resources/app
STUB = '''#!/bin/bash
# Startet JU Hamburg Finanzen mit der mitgelieferten Python-Laufzeit.
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$CONTENTS/MacOS/python3"
"$PYTHON" -E -s -c '' 2>/dev/null || PYTHON="$CONTENTS/Resources/python/bin/python3"   # Rückfall ohne Verweis
exec "$PYTHON" -E -s "$CONTENTS/Resources/app/launcher.py" "$@"
'''
STAGING = f'.{APP_NAME} (neu).app-neu'             # neue Version vor dem Austausch (von macOS nicht als App geführt)


def app_version():
    for line in (REPO / 'app.py').read_text(encoding='utf-8').splitlines():
        if line.startswith('VERSION='):
            return line.split('=', 1)[1].strip().strip("'\"")
    raise SystemExit('VERSION in app.py nicht gefunden.')


def git_commit():
    try:
        return subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--short', 'HEAD'], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unbekannt'


def fetch_python(cache):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / PYTHON_ARCHIVE
    if not archive.exists():
        print('Lade Python-Laufzeit …', flush=True)
        partial = archive.with_name(archive.name + '.part')
        with urllib.request.urlopen(PYTHON_URL, timeout=120) as response, open(partial, 'wb') as out:
            shutil.copyfileobj(response, out)
        partial.replace(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != PYTHON_SHA256:
        raise SystemExit(f'Prüfsumme der Python-Laufzeit stimmt nicht ({digest}).')
    return archive


def build(out_dir, cache, name=None):
    """Baut die App in einem lokalen Arbeitsordner und kopiert sie fertig nach out_dir/name."""
    version = app_version()
    bundle = out_dir / (name or f'{APP_NAME}.app')
    if bundle.exists():
        raise SystemExit(f'Ziel existiert bereits: {bundle}')
    with tempfile.TemporaryDirectory(prefix='ju-finanzen-build-') as work:
        local = assemble(Path(work) / f'{APP_NAME}.app', cache, version)
        shutil.copytree(local, bundle, symlinks=True)
    print(f'Gebaut: {bundle} (Version {version})', flush=True)
    return bundle


def assemble(bundle, cache, version):
    contents = bundle / 'Contents'
    resources = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    resources.mkdir()

    with tarfile.open(fetch_python(cache)) as tar:
        tar.extractall(resources, filter='data')          # → Resources/python
    for unused in ('lib/python3.13/test', 'lib/python3.13/idlelib', 'lib/python3.13/tkinter',
                   'lib/python3.13/turtledemo', 'lib/python3.13/ensurepip'):
        shutil.rmtree(resources / 'python' / unused, ignore_errors=True)

    site = resources / 'python' / 'lib' / f'python{PYTHON_VERSION}' / 'site-packages'
    print('Installiere Bibliotheken für macOS/arm64 …', flush=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '--no-compile', '--disable-pip-version-check',
                    '--target', str(site), '--platform', 'macosx_11_0_arm64', '--python-version', PYTHON_VERSION,
                    '--implementation', 'cp', '--only-binary=:all:', '-r', str(REPO / 'requirements.txt')],
                   check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '--no-compile', '--disable-pip-version-check',
                    '--target', str(site), '--platform', 'macosx_11_0_arm64', '--python-version', PYTHON_VERSION,
                    '--implementation', 'cp', '--only-binary=:all:', '--no-deps', '-r', str(REPO / 'mac' / 'requirements-mac.txt')],
                   check=True)
    shutil.rmtree(site / 'PyObjCTest', ignore_errors=True)            # Testsuite von PyObjC (rund 20 MB)
    for symbols in list(site.rglob('*.dSYM')):                         # Debug-Symbole von PyObjC
        shutil.rmtree(symbols, ignore_errors=True)

    app = resources / 'app'
    app.mkdir()
    for name in APP_FILES:
        (app / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, app / name)
    shutil.copytree(REPO / 'static', app / 'static')
    for name in MAC_FILES:
        shutil.copy2(REPO / 'mac' / name, app / name)
    shutil.copy2(REPO / 'mac' / 'AppIcon.icns', resources / 'AppIcon.icns')
    (resources / 'de.lproj').mkdir()                                     # App ist deutschsprachig
    (resources / 'de.lproj' / 'InfoPlist.strings').write_text(f'CFBundleName = "{APP_NAME}";\n', encoding='utf-8')

    stub = contents / 'MacOS' / APP_NAME
    stub.write_text(STUB, encoding='utf-8')
    stub.chmod(0o755)
    (contents / 'MacOS' / PYTHON_LINK).symlink_to(Path('..') / 'Resources' / 'python' / 'bin' / 'python3')
    with open(contents / 'Info.plist', 'wb') as f:
        plistlib.dump({
            'CFBundleName': APP_NAME, 'CFBundleDisplayName': APP_NAME, 'CFBundleIdentifier': BUNDLE_ID,
            'CFBundleDevelopmentRegion': 'de', 'CFBundleLocalizations': ['de'],   # macOS-Dialoge (Sichern, Drucken) auf Deutsch
            'CFBundleExecutable': APP_NAME, 'CFBundleIconFile': 'AppIcon', 'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': version, 'CFBundleVersion': version, 'CFBundleInfoDictionaryVersion': '6.0',
            'LSMinimumSystemVersion': '11.0', 'LSApplicationCategoryType': 'public.app-category.finance',
            'NSHighResolutionCapable': True, 'LSArchitecturePriority': ['arm64'],
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},   # Programmfenster lädt http://127.0.0.1
        }, f)
    packages = sorted(p.name for p in site.glob('*.dist-info'))
    (resources / 'BUILD-INFO.json').write_text(json.dumps({
        'app': APP_NAME, 'version': version, 'commit': git_commit(), 'python': PYTHON_ARCHIVE,
        'packages': packages, 'built': datetime.datetime.now().isoformat(timespec='seconds')},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Bibliotheken: {", ".join(packages)}', flush=True)
    return bundle


ARCHIVE_SUFFIX = '.app-archiv'


def archive_app(app, archive_dir, stamp=None):
    """Verschiebt eine App ins Archiv, ohne dass macOS sie weiter als App führt; liefert den neuen Pfad."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    try:
        version = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())['CFBundleShortVersionString']
    except (OSError, KeyError, plistlib.InvalidFileException):
        version = 'alt'
    stamp = stamp or datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    target = archive_dir / f'{APP_NAME} {version} ({stamp}){ARCHIVE_SUFFIX}'
    if target.exists():
        raise SystemExit(f'Archiv existiert bereits: {target}')
    app.rename(target)
    return target


def retire_archived_apps(archive_dir):
    """Ältere Archive mit Endung „.app“ umbenennen (Stand bis 0.8); liefert die neuen Pfade."""
    if not archive_dir.is_dir():
        return []
    moved = []
    for old in sorted(archive_dir.glob('*.app')):
        new = old.with_name(old.name[:-len('.app')] + ARCHIVE_SUFFIX)
        if not new.exists():
            old.rename(new)
            moved.append(new)
    return moved


def install(base, cache):
    if not (base / 'Daten').is_dir():
        raise SystemExit(f'Kein Basisordner mit „Daten“: {base}')
    if (base / '.build-neu').exists():
        raise SystemExit(f'Vorheriger Build-Rest vorhanden, bitte prüfen: {base / ".build-neu"}')
    archive_dir = base / 'Sicherungen' / 'Programmversionen'
    rest = base / STAGING
    if rest.exists():          # abgebrochener Bau: beiseitelegen (umbenennen, nicht löschen)
        moved = archive_dir / 'Build-Reste' / f'{STAGING.lstrip(".")} {datetime.datetime.now():%Y%m%d-%H%M%S}'
        moved.parent.mkdir(parents=True, exist_ok=True)
        rest.rename(moved)
        print(f'Rest eines abgebrochenen Baus verschoben: {moved}', flush=True)
    new = build(base, cache, name=STAGING)
    target = base / f'{APP_NAME}.app'
    archived = archive_app(target, archive_dir) if target.exists() else None
    try:
        new.rename(target)
    except OSError:
        if archived is not None:
            archived.rename(target)   # alte Version zurück an ihren Platz
        raise
    for moved in retire_archived_apps(base / 'Sicherungen' / 'Programmversionen'):
        print(f'Archiv umbenannt: {moved.name}', flush=True)
    print(f'Installiert: {target}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--out', type=Path, help='Ordner, in den die App gebaut wird')
    group.add_argument('--install', type=Path, help='Basisordner (mit Daten/), in dem die App ersetzt wird')
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache' / 'ju-finanzen-build')
    args = parser.parse_args()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        build(args.out, args.cache)
    else:
        install(args.install, args.cache)


if __name__ == '__main__':
    main()
