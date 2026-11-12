"""Starter der macOS-App „JU Hamburg Finanzen“ (nur Standardbibliothek).

Liegt im App-Bundle unter Contents/Resources/app/launcher.py. Erwartete Ordnerstruktur:

    JU Hamburg Finanzen/            ← Basisordner (unter Programme)
        JU Hamburg Finanzen.app
        Daten/                      ← Datenbank, importierte Kontoauszüge, programm.log
        Sicherungen/                ← automatische Sicherungen

Startet den lokalen Server im Hintergrund (falls er nicht schon läuft) und zeigt die Oberfläche
im eigenen Programmfenster (fenster.py). Rückfall auf den Standardbrowser, wenn das Fenster nicht
verfügbar ist, beim Öffnen die Wahltaste (⌥) gedrückt ist oder Daten/.im-browser-oeffnen existiert.
Fehler erscheinen als macOS-Dialog; Details stehen in Daten/programm.log.
"""
import faulthandler
import json
import os
import plistlib
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

APP_NAME = 'JU Hamburg Finanzen'
PORT = 8769
LOG_LIMIT = 1_000_000
BROWSER_MARKER = '.im-browser-oeffnen'
LAST_PROCESS = None  # für Tests


def locate(launcher_file):
    """(Programmordner im Bundle, Basisordner neben dem Bundle)."""
    app_dir = Path(launcher_file).resolve().parent      # …/X.app/Contents/Resources/app
    bundle = app_dir.parent.parent.parent                # …/X.app
    return app_dir, bundle.parent


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def status(port, timeout=1.0):
    try:
        with OPENER.open(f'http://127.0.0.1:{port}/api/status', timeout=timeout) as response:
            return json.load(response)
    except Exception:
        return None


def bundle_version(app_dir):
    """Version laut Info.plist des Bundles (None außerhalb eines Bundles)."""
    try:
        return plistlib.loads((app_dir.parent.parent / 'Info.plist').read_bytes()).get('CFBundleShortVersionString')
    except (OSError, plistlib.InvalidFileException):
        return None


def wait_port_free(port, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not port_busy(port):
            return True
        time.sleep(0.2)
    return False


def request_stop(port, data):
    """Bittet die laufende Version, sich zu beenden (Schlüssel aus Daten/.steuerung)."""
    try:
        token = (data / '.steuerung').read_text(encoding='utf-8').strip()
    except OSError:
        return False
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/stop', json.dumps({'token': token}).encode(),
                                     {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'})
    try:
        OPENER.open(request, timeout=3).read()
        return True
    except Exception:
        return False


def terminate_old(port, data):
    """Rückfall für ältere Versionen ohne /api/stop: nur den eigenen Server dieses Datenordners beenden."""
    try:
        pids = subprocess.run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'], capture_output=True,
                              text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False
    stopped = False
    for pid in pids:
        command = subprocess.run(['ps', '-p', pid, '-o', 'command='], capture_output=True, text=True).stdout
        if 'app.py' in command and str(data) in command:
            os.kill(int(pid), signal.SIGTERM)
            stopped = True
    return stopped


def stop_gently(port, data, instance=None):
    """Beim Schließen des Fensters: den Server dieses Fensters bitten, sich zu beenden – nie erzwungen
    (ein E-Mail-Versand läuft weiter; der Server einer anderen Instanz, z. B. nach einem Update, bleibt unberührt)."""
    current = status(port)
    if not is_ours(current) or current.get('instance') != instance:
        return False
    return request_stop(port, data) and wait_port_free(port, 8)


def stop_running(port, data):
    if request_stop(port, data) and wait_port_free(port, 8):
        return True
    terminate_old(port, data)
    return wait_port_free(port, 8)


def port_busy(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def _quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def dialog(message):
    print(message, flush=True)
    script = (f'display dialog {_quote(message)} with title {_quote(APP_NAME)} '
              'buttons {"OK"} default button "OK" with icon caution')
    try:
        subprocess.run(['osascript', '-e', script], check=False)
    except FileNotFoundError:
        pass


def open_url(url):
    try:
        subprocess.run(['open', url], check=False)
    except FileNotFoundError:
        print('Bitte im Browser öffnen: ' + url, flush=True)


def start_url(data, port):
    try:
        url = (data / '.start-url').read_text(encoding='utf-8').strip()
        if url.startswith(f'http://127.0.0.1:{port}/'):
            return url
    except OSError:
        pass
    return f'http://127.0.0.1:{port}/'


def note(log, text):
    try:
        with open(log, 'a', encoding='utf-8') as out:
            out.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} Fenster: {text}\n')
    except OSError:
        pass


def capture_errors(log):
    """Fehlerausgaben (Python, PyObjC, macOS, Abstürze) ins Protokoll – beim Start aus dem Finder gingen sie verloren."""
    try:
        out = open(log, 'a', encoding='utf-8', buffering=1)
    except OSError:
        return
    os.dup2(out.fileno(), 2)
    sys.stderr = out
    faulthandler.enable(out)


def option_key_down():
    """Wahltaste (⌥) gerade gedrückt? Über CoreGraphics mit der Standardbibliothek – vor dem Laden von PyObjC."""
    try:
        import ctypes
        graphics = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        graphics.CGEventSourceFlagsState.restype = ctypes.c_uint64
        graphics.CGEventSourceFlagsState.argtypes = [ctypes.c_int32]
        return bool(graphics.CGEventSourceFlagsState(0) & 0x80000)   # CombinedSessionState, MaskAlternate
    except (OSError, AttributeError):
        return False


def window_module():
    """(Modul fenster, None) oder (None, Grund), z. B. ohne PyObjC oder vor macOS 11.3."""
    try:
        import fenster
        if not fenster.supported():
            return None, 'macOS 11.3 oder neuer erforderlich'
        return fenster, None
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'


def choose_window(data):
    """Gleich beim Öffnen entscheiden: (Modul, None) = Fenster, (None, Grund) = Browser.

    Browser, wenn die Wahltaste (⌥) gedrückt ist, Daten/.im-browser-oeffnen existiert oder das Fenster nicht
    verfügbar ist (ohne PyObjC, vor macOS 11.3). Die Wahltaste wird vor allem anderen abgefragt.
    """
    if option_key_down():
        return None, 'Wahltaste beim Öffnen gedrückt'
    if (data / BROWSER_MARKER).exists():
        return None, f'Daten/{BROWSER_MARKER} vorhanden'
    module, reason = window_module()
    if module is None:
        return None, f'Fenster nicht verfügbar: {reason}'
    return module, None


def login_code(port, data):
    """Einmaligen Anmeldecode beim Server holen (nur nach der Prüfung durch macOS aufrufen)."""
    token = (data / '.steuerung').read_text(encoding='utf-8').strip()
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/login-code', json.dumps({'token': token}).encode(),
                                     {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{port}'})
    with OPENER.open(request, timeout=5) as response:
        return json.load(response)['code']


def mac_check():
    """Touch ID / Apple Watch / Mac-Passwort prüfen (Browser-Modus): (ok, meldung, abgebrochen)."""
    try:
        import anmeldung
        return anmeldung.authenticate_and_wait()
    except Exception as exc:
        return False, f'Anmeldung über macOS nicht verfügbar ({type(exc).__name__}: {exc})', False


def open_browser(port, data, log):
    """Browser-Modus: erst macOS prüfen lassen, dann mit einmaligem Anmeldecode öffnen."""
    ok, message, cancelled = mac_check()
    if not ok:
        note(log, f'Anmeldung für den Browser nicht erfolgt: {message or "abgebrochen"}')
        if not cancelled:
            dialog(f'Anmeldung nicht möglich.\n\n{message}')
        return 0
    open_url(f'http://127.0.0.1:{port}/?login={login_code(port, data)}')
    note(log, 'Angemeldet über macOS – Oberfläche im Browser geöffnet.')
    return 0


def show(url, app_dir, data, port, window, state, capture=False):
    """Oberfläche im eigenen Programmfenster; Rückfall: Standardbrowser. Kehrt erst nach dem Schließen zurück.

    state ist der Status des Servers, mit dem das Fenster arbeitet – nur ihn beendet es beim Schließen.
    Angemeldet wird in beiden Fällen mit macOS (Touch ID, Apple Watch oder Mac-Passwort), nie mit eigenem Passwort.
    """
    module, reason = window
    log = data / 'programm.log'
    if module is None:
        note(log, f'Oberfläche im Browser ({reason}).')
        return open_browser(port, data, log)
    instance = (state or {}).get('instance')
    if not instance:
        note(log, f'Server {(state or {}).get("version", "?")} ohne Kennung – das Fenster beendet ihn beim Schließen.')

    def own_status():          # nur „unser“ Server zählt – nie den einer anderen Instanz beenden
        current = status(port)
        return current if is_ours(current) and current.get('instance') == instance else None

    def authenticate(done):
        import anmeldung
        anmeldung.authenticate(done)
    if capture:
        capture_errors(log)    # erneut: das Protokoll kann seit dem Start gewechselt haben
    try:
        return module.run(url, port=port, status=own_status, stop=lambda: stop_gently(port, data, instance),
                          log=lambda text: note(log, text), icon=app_dir.parent / 'AppIcon.icns',
                          authenticate=authenticate, login=lambda: login_code(port, data))
    except Exception:
        note(log, 'Start fehlgeschlagen – Oberfläche im Browser.\n' + traceback.format_exc())
        return open_browser(port, data, log)


def log_tail(log, lines=6):
    try:
        return '\n'.join(log.read_text(encoding='utf-8', errors='replace').splitlines()[-lines:])
    except OSError:
        return ''


def is_ours(state):
    return bool(state) and state.get('app') == APP_NAME


def main(launcher_file=__file__, port=PORT, python=sys.executable, wait=30.0, capture=False):
    """capture=True (Start als App): Fehlerausgaben des Prozesses ins Protokoll Daten/programm.log."""
    global LAST_PROCESS
    app_dir, base = locate(launcher_file)
    data, backups = base / 'Daten', base / 'Sicherungen'
    if not data.is_dir():
        dialog(f'Der Ordner „Daten“ liegt nicht neben der App.\n\nDie App muss im Ordner „{APP_NAME}“ '
               f'(unter Programme) bleiben. Für das Dock die App nur hineinziehen, nicht verschieben.\n\nErwartet: {data}')
        return 1
    if capture:
        capture_errors(data / 'programm.log')
    window = choose_window(data)      # Wahltaste sofort abfragen, nicht erst nach dem Serverstart
    running = status(port)
    if is_ours(running):
        expected = bundle_version(app_dir)
        if not expected or running.get('version') == expected:
            return show(start_url(data, port), app_dir, data, port, window, running, capture)
        if running.get('mail_busy'):  # nie mitten im E-Mail-Versand für ein Update beenden
            dialog('Gerade läuft ein E-Mail-Versand. Die neue Version wird gestartet, sobald du die App nach '
                   'Abschluss des Versands erneut öffnest.')
            return show(start_url(data, port), app_dir, data, port, window, running, capture)
        if not stop_running(port, data):   # ältere Version läuft noch → für das Update beenden
            dialog(f'Die laufende Version {running.get("version")} ließ sich nicht beenden. Bitte im Programm auf '
                   '„Beenden“ klicken oder den Mac neu starten und die App erneut öffnen.')
            return 1
    if port_busy(port):
        dialog(f'Port {port} ist durch ein anderes Programm belegt – vermutlich läuft noch eine alte Version '
               '(z. B. vom Schreibtisch). Bitte diese beenden und die App erneut öffnen.')
        return 1
    log = data / 'programm.log'
    if log.exists() and log.stat().st_size > LOG_LIMIT:
        log.replace(log.with_name('programm.alt.log'))
    with open(log, 'ab') as out:
        LAST_PROCESS = subprocess.Popen(
            [python, '-E', '-s', str(app_dir / 'app.py'), '--no-browser', '--port', str(port),
             '--data', str(data), '--backups', str(backups)],
            cwd=str(app_dir), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        current = status(port)
        if is_ours(current):
            return show(start_url(data, port), app_dir, data, port, window, current, capture)
        if LAST_PROCESS.poll() is not None:
            break
        time.sleep(0.2)
    dialog('Das Programm konnte nicht gestartet werden.\n\n' + log_tail(log) + f'\n\nProtokoll: {log}')
    return 1


if __name__ == '__main__':
    sys.exit(main(capture=True))
