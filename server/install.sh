#!/usr/bin/env bash
# JU Hamburg Finanzen – Installation und Aktualisierung auf Ubuntu 24.04 (Serverbetrieb hinter Tailscale).
#
# Aufruf als root aus dem entpackten Paket:
#   sudo ./server/install.sh                    Erstinstallation bzw. Update (Daten bleiben unverändert)
#   sudo ./server/install.sh --tailscale        zusätzlich Tailscale Serve einrichten (HTTPS-Adresse im Tailnet)
#   sudo ./server/install.sh --adresse https://ju-finanzen.beispiel.ts.net   Adresse selbst festlegen
#   sudo ./server/install.sh --zurueck          vorherige Programmversion wieder aktivieren
#   sudo ./server/install.sh --pruefen          nur prüfen, was installiert werden würde (ändert nichts)
#
# Aufbau auf dem Server:
#   /opt/ju-finanzen/releases/<Version-Zeit>/  Programm (root gehört es, nur lesbar für den Dienst) mit eigenem venv
#   /opt/ju-finanzen/current                   Verweis auf die aktive Version
#   /var/lib/ju-finanzen/Daten                 Datenbank, Belege, Importe (nur Dienstbenutzer „jufinanzen“)
#   /var/lib/ju-finanzen/Sicherungen           automatische Sicherungen
#   /var/lib/ju-finanzen/geheim                Passwort des E-Mail-Kontos (falls gespeichert)
#   /etc/ju-finanzen/ju-finanzen.env           Einstellungen des Dienstes
#   /etc/systemd/system/ju-finanzen.service    Dienst; /usr/local/bin/ju-finanzen Verwaltungsbefehl
set -euo pipefail
umask 022

APP_ROOT=/opt/ju-finanzen
DATA_ROOT=/var/lib/ju-finanzen
ETC=/etc/ju-finanzen
ENV_FILE=$ETC/ju-finanzen.env
SERVICE=ju-finanzen
SERVICE_USER=jufinanzen
KEEP_RELEASES=3
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\nFEHLER: %s\n' "$*" >&2; exit 1; }

TAILSCALE=0; ADDRESS=""; ROLLBACK=0; DRY=0; PORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tailscale) TAILSCALE=1 ;;
    --adresse) shift; ADDRESS="${1:-}"; [ -n "$ADDRESS" ] || die "--adresse braucht einen Wert, z. B. https://ju-finanzen.beispiel.ts.net" ;;
    --port) shift; PORT="${1:-}"; [[ "$PORT" =~ ^[0-9]{2,5}$ ]] || die "--port braucht eine Zahl" ;;
    --zurueck) ROLLBACK=1 ;;
    --pruefen) DRY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "Unbekannte Option: $1 (Hilfe: --help)" ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || die "Bitte mit sudo ausführen: sudo $0 $*"
HAVE_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then HAVE_SYSTEMD=1; fi

wait_ready() {
  local port="$1"
  for _ in $(seq 1 40); do
    if python3 - "$port" <<'PY' 2>/dev/null; then return 0; fi
import json, sys, urllib.request
with urllib.request.urlopen(f'http://127.0.0.1:{sys.argv[1]}/api/status', timeout=2) as r:
    sys.exit(0 if json.load(r).get('mode') == 'server' else 1)
PY
    sleep 0.5
  done
  return 1
}

env_value() { [ -f "$ENV_FILE" ] && sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 || true; }

# ---------------------------------------------------------------- Zurück zur vorherigen Version
if [ "$ROLLBACK" -eq 1 ]; then
  current="$(readlink -f "$APP_ROOT/current" 2>/dev/null || true)"
  [ -n "$current" ] || die "Keine Installation gefunden."
  # die beim letzten Update ersetzte Version (gemerkt von install.sh) – nicht einfach „die andere“, damit ein zweites
  # --zurueck nicht wieder auf eine fehlerhafte neuere Version springt
  previous="$(cat "$APP_ROOT/vorherige-version" 2>/dev/null || true)"
  [ -n "$previous" ] && [ -d "$previous" ] && [ "$(dirname "$previous")" = "$APP_ROOT/releases" ] && [ "$previous" != "$current" ] \
    || die "Keine vorherige Version gemerkt (nur einmal je Update möglich)."
  say "Zurück auf $(basename "$previous") (bisher $(basename "$current"))"
  ln -sfn "$previous" "$APP_ROOT/current.neu" && mv -Tf "$APP_ROOT/current.neu" "$APP_ROOT/current"
  rm -f "$APP_ROOT/vorherige-version"
  if [ "$HAVE_SYSTEMD" -eq 1 ]; then
    systemctl restart "$SERVICE"
    wait_ready "$(env_value JU_FINANZEN_PORT)" || die "Dienst startet nicht – Protokoll: sudo journalctl -u $SERVICE -n 50"
  fi
  info "Fertig. Die Datenbank wurde nicht verändert (Änderungen an der Datenbank sind immer nur Ergänzungen)."
  exit 0
fi

# ---------------------------------------------------------------- Prüfungen
[ -f "$SRC/app.py" ] && [ -f "$SRC/server/requirements-server.txt" ] || die "Bitte aus dem entpackten Paket aufrufen (app.py und server/ fehlen)."
APP_VERSION="$(sed -n "s/^VERSION='\(.*\)'.*/\1/p" "$SRC/app.py" | head -n 1)"
[[ "$APP_VERSION" =~ ^[0-9][0-9.]*$ ]] || die "Programmversion nicht gefunden."
OS="$( (. /etc/os-release 2>/dev/null && printf '%s %s' "${ID:-?}" "${VERSION_ID:-?}") || echo '? ?')"   # eigene Shell: os-release setzt u. a. VERSION
[ "$OS" = "ubuntu 24.04" ] || info "Hinweis: getestet mit Ubuntu 24.04, gefunden: $OS"
PYTHON="${JU_PYTHON:-/usr/bin/python3}"   # JU_PYTHON nur für Tests mit einem anderen Python
[ -x "$PYTHON" ] || die "python3 fehlt (sudo apt install python3)."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10 oder neuer nötig."

say "JU Hamburg Finanzen $APP_VERSION installieren"
info "Paket:   $SRC"
info "Python:  $("$PYTHON" --version 2>&1), Architektur $(uname -m)"
[ "$HAVE_SYSTEMD" -eq 1 ] || info "Hinweis: systemd läuft hier nicht – Dienst wird nur eingerichtet, nicht gestartet."
if [ "$DRY" -eq 1 ]; then
  info "Nur Prüfung (--pruefen): nichts geändert."
  exit 0
fi

# ---------------------------------------------------------------- Pakete
if ! "$PYTHON" -c 'import ensurepip, venv' >/dev/null 2>&1; then
  say "Systempaket python3-venv installieren"
  command -v apt-get >/dev/null 2>&1 || die "python3-venv fehlt und apt-get ist nicht vorhanden."
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv ca-certificates >/dev/null
fi

# ---------------------------------------------------------------- Benutzer und Ordner
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  say "Dienstbenutzer $SERVICE_USER anlegen (ohne Anmeldung)"
  useradd --system --home-dir "$DATA_ROOT" --no-create-home --shell /usr/sbin/nologin --user-group "$SERVICE_USER"
fi
install -d -m 755 -o root -g root "$APP_ROOT" "$APP_ROOT/releases"
# Datenordner: nur beim ersten Mal anlegen. In vorhandenen Ordnern legt root nichts an und ändert keine Rechte – dort darf der
# Dienstbenutzer Einträge ersetzen (z. B. durch Verweise). Die Unterordner legt systemd (StateDirectory) bzw. das Programm an.
[ -L "$DATA_ROOT" ] && die "$DATA_ROOT ist ein symbolischer Verweis – abgebrochen."
[ -d "$DATA_ROOT" ] || install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_ROOT"
install -d -m 750 -o root -g "$SERVICE_USER" "$ETC"

# ---------------------------------------------------------------- Programm
RELEASE="$APP_ROOT/releases/$APP_VERSION-$(date +%Y%m%d-%H%M%S)"
say "Programm nach $RELEASE kopieren"
mkdir -p "$(dirname "$RELEASE")"
mkdir "$RELEASE"    # ohne -p: zwei Läufe in derselben Sekunde teilen sich keinen Ordner
# scheitert ein Schritt bis zum Selbsttest (z. B. PyPI nicht erreichbar, Sitzung abgerissen), bleibt kein halber Ordner zurück
trap 'rm -rf -- "$RELEASE"' ERR
trap 'rm -rf -- "$RELEASE"; exit 130' INT TERM HUP
# nur Programmdateien – keine Daten, keine Mac-App, kein Git
tar -C "$SRC" --exclude='./.git' --exclude='./.github' --exclude='*/__pycache__' --exclude='./Daten' --exclude='./Sicherungen' \
    --exclude='./server/wheels' --exclude='*.sqlite3' --exclude='*.zip' -cf - . | tar -C "$RELEASE" -xf -
# Installation aus einem Git-Checkout (GitHub): den Stand festhalten, damit er sich mit dem freigegebenen vergleichen lässt
if [ ! -f "$RELEASE/server/GIT-STAND" ] && [ -d "$SRC/.git" ] && command -v git >/dev/null 2>&1; then
  git -C "$SRC" rev-parse --verify --quiet HEAD > "$RELEASE/server/GIT-STAND" 2>/dev/null || rm -f "$RELEASE/server/GIT-STAND"
fi
say "Python-Umgebung mit festen Versionen (Prüfsummen) anlegen"
"$PYTHON" -m venv "$RELEASE/venv"
PIP=("$RELEASE/venv/bin/pip" install --quiet --disable-pip-version-check --no-cache-dir --require-hashes --only-binary=:all:)
if [ -d "$SRC/server/wheels" ] && ls "$SRC"/server/wheels/*.whl >/dev/null 2>&1; then
  info "aus dem Paket (ohne Internet): server/wheels"
  "${PIP[@]}" --no-index --find-links "$SRC/server/wheels" -r "$SRC/server/requirements-server.txt"
else
  info "von PyPI"
  "${PIP[@]}" -r "$SRC/server/requirements-server.txt"
fi
"$RELEASE/venv/bin/python" -m compileall -q "$RELEASE" -x '/venv/' >/dev/null || true
"$RELEASE/venv/bin/python" -m compileall -q "$RELEASE/venv" >/dev/null 2>&1 || true
( cd "$RELEASE" && "$RELEASE/venv/bin/python" -c 'import reportlab, openpyxl, xlrd, certifi, msoffcrypto, cryptography.hazmat.primitives.ciphers, app, verwaltung; print("    Selbsttest: Module geladen, Version", app.VERSION)' ) \
  || { rm -rf "$RELEASE"; die "Selbsttest fehlgeschlagen – nichts umgestellt."; }
# Prüfliste aller Programmdateien samt venv und __pycache__ für die Dateiprüfung (sicherheit.py) – zuletzt, nach venv,
# compileall und Selbsttest; danach schreibt niemand mehr in den Ordner (gehört root, der Dienst darf nur lesen)
( cd "$RELEASE" && find . -type f ! -path './server/PRUEFLISTE' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum ) > "$RELEASE/server/PRUEFLISTE"
chown -R root:root "$RELEASE"
chmod -R go-w "$RELEASE"
trap - ERR INT TERM HUP

# ---------------------------------------------------------------- Einstellungen
if [ -z "$PORT" ]; then PORT="$(env_value JU_FINANZEN_PORT)"; fi
PORT="${PORT:-8769}"
TS_NAME=""
if [ "$TAILSCALE" -eq 1 ]; then
  command -v tailscale >/dev/null 2>&1 || die "Tailscale ist nicht installiert: curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
  TS_NAME="$(tailscale status --json 2>/dev/null | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
  [ -n "$TS_NAME" ] || die "Tailscale ist nicht angemeldet (sudo tailscale up) oder MagicDNS ist aus."
  [ -n "$ADDRESS" ] || ADDRESS="https://$TS_NAME"
fi
if [ ! -f "$ENV_FILE" ]; then
  say "Einstellungen anlegen: $ENV_FILE"
  install -m 640 -o root -g "$SERVICE_USER" "$SRC/server/ju-finanzen.env.beispiel" "$ENV_FILE"
  sed -i "s#^JU_FINANZEN_PORT=.*#JU_FINANZEN_PORT=$PORT#" "$ENV_FILE"
fi
if [ -n "$ADDRESS" ]; then
  [[ "$ADDRESS" =~ ^https?://[A-Za-z0-9.:-]+$ ]] || die "Ungültige Adresse: $ADDRESS (Beispiel: https://ju-finanzen.beispiel.ts.net)"
  if grep -q '^JU_FINANZEN_ADRESSEN=' "$ENV_FILE"; then
    sed -i "s#^JU_FINANZEN_ADRESSEN=.*#JU_FINANZEN_ADRESSEN=$ADDRESS#" "$ENV_FILE"
  else
    printf 'JU_FINANZEN_ADRESSEN=%s\n' "$ADDRESS" >> "$ENV_FILE"
  fi
  info "Adresse: $ADDRESS"
fi
if [ -n "$PORT" ] && grep -q '^JU_FINANZEN_PORT=' "$ENV_FILE"; then sed -i "s#^JU_FINANZEN_PORT=.*#JU_FINANZEN_PORT=$PORT#" "$ENV_FILE"; fi
chown root:"$SERVICE_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"

install -m 644 -o root -g root "$SRC/server/ju-finanzen.service" "/etc/systemd/system/$SERVICE.service"
install -m 755 -o root -g root "$SRC/server/ju-finanzen" /usr/local/bin/ju-finanzen

# ---------------------------------------------------------------- Umschalten und starten
PREVIOUS="$(readlink -f "$APP_ROOT/current" 2>/dev/null || true)"
ln -sfn "$RELEASE" "$APP_ROOT/current.neu" && mv -Tf "$APP_ROOT/current.neu" "$APP_ROOT/current"
if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then printf '%s\n' "$PREVIOUS" > "$APP_ROOT/vorherige-version"; fi
if [ "$HAVE_SYSTEMD" -eq 1 ]; then
  say "Dienst starten"
  systemctl daemon-reload
  systemctl enable --quiet "$SERVICE"
  systemctl restart "$SERVICE"
  if ! wait_ready "$PORT"; then
    if [ -n "$PREVIOUS" ] && [ -d "$PREVIOUS" ]; then
      ln -sfn "$PREVIOUS" "$APP_ROOT/current.neu" && mv -Tf "$APP_ROOT/current.neu" "$APP_ROOT/current"
      rm -f "$APP_ROOT/vorherige-version"
      systemctl restart "$SERVICE" || true
      die "Neue Version startet nicht – vorherige Version $(basename "$PREVIOUS") wieder aktiv. Protokoll: sudo journalctl -u $SERVICE -n 80"
    fi
    die "Dienst startet nicht. Protokoll: sudo journalctl -u $SERVICE -n 80"
  fi
  info "Dienst läuft (127.0.0.1:$PORT)."
fi

if [ "$TAILSCALE" -eq 1 ]; then
  say "Tailscale Serve einrichten (nur im eigenen Tailnet, HTTPS)"
  if tailscale serve --bg --https=443 "http://127.0.0.1:$PORT" >/dev/null; then
    info "Erreichbar unter https://$TS_NAME (nur für Geräte im Tailnet)."
  else
    info "Tailscale Serve konnte nicht eingerichtet werden. Meist fehlt die Freigabe für HTTPS-Zertifikate:"
    info "Tailscale-Verwaltung → DNS → „HTTPS Certificates“ aktivieren, dann erneut: sudo tailscale serve --bg --https=443 http://127.0.0.1:$PORT"
  fi
fi

# alte Versionen aufräumen (die aktive und die vorherige bleiben immer)
ls -1dt "$APP_ROOT"/releases/*/ 2>/dev/null | sed 's#/$##' | tail -n "+$((KEEP_RELEASES + 1))" | while read -r old; do
  [ "$old" = "$(readlink -f "$APP_ROOT/current")" ] || [ "$old" = "$PREVIOUS" ] || rm -rf -- "$old"
done

say "Fertig: JU Hamburg Finanzen $APP_VERSION"
if ! runuser -u "$SERVICE_USER" -- "$APP_ROOT/current/venv/bin/python" - "$DATA_ROOT/Daten/finanzen.sqlite3" <<'PY' 2>/dev/null; then   # als Dienstbenutzer: root öffnet keine Dateien des Dienstes
import sqlite3, sys
sys.path.insert(0, '/opt/ju-finanzen/current')
import zugang
con = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
sys.exit(0 if any(zugang.is_set(r[0]) for r in con.execute('SELECT password FROM users')) else 1)
PY
  info "Nächste Schritte:"
  info "  1. Daten vom Mac übernehmen (ZIP aus Stammdaten → Sicherung):  sudo ju-finanzen uebernehmen /pfad/JU-Sicherung.zip"
  info "  2. Passwort setzen:                                             sudo ju-finanzen passwort \"Lennert-Gust\""
fi
info "Adresse(n): $(env_value JU_FINANZEN_ADRESSEN | sed 's/^$/noch keine – mit --tailscale oder --adresse festlegen/')"
info "Protokoll:  sudo journalctl -u $SERVICE -f        Verwaltung: sudo ju-finanzen --help"
