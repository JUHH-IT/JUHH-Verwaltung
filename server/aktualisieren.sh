#!/usr/bin/env bash
# JU Hamburg Finanzen – Update des Servers aus dem Git-Repository (z. B. privates Repository bei GitHub).
#
# Aufruf über den Verwaltungsbefehl:
#   sudo ju-finanzen aktualisieren              neueste freigegebene Version holen und installieren
#   sudo ju-finanzen aktualisieren 0.34         genau diese Version
#   sudo ju-finanzen aktualisieren --pruefen    nur zeigen, welche Version es gäbe (installiert nichts)
#
# Die Quelle ist ein Git-Checkout unter /opt/ju-finanzen/quelle (Einrichtung: SERVER.md, „Direkt aus GitHub“).
# Installiert werden nur freigegebene Versionen: ein Tag „v<Version>“, dessen app.py genau diese Version nennt.
# Nie automatisch und nie der Entwicklungsstand (main). Installation, Selbsttest und automatisches Zurückschalten
# übernimmt server/install.sh der neuen Version; die Daten bleiben unverändert.
set -euo pipefail

SOURCE=/opt/ju-finanzen/quelle
CHECK=0; INSTALL=1; WANT=""

say()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\nFEHLER: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --pruefen) CHECK=1 ;;
    --quelle) shift; SOURCE="${1:-}"; [ -n "$SOURCE" ] || die "--quelle braucht einen Ordner" ;;
    --ohne-installation) INSTALL=0 ;;   # nur für Tests: Stand holen und prüfen, install.sh nicht aufrufen
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) die "Unbekannte Option: $1 (Hilfe: --help)" ;;
    *) [ -z "$WANT" ] || die "Bitte nur eine Version angeben."
       WANT="${1#v}"; [[ "$WANT" =~ ^[0-9]+(\.[0-9]+)+$ ]] || die "Ungültige Version: $1 (Beispiel: 0.34)" ;;
  esac
  shift
done

command -v git >/dev/null 2>&1 || die "git fehlt (sudo apt install git)."
SOURCE="$(cd "$SOURCE" 2>/dev/null && pwd)" || die "Ordner der Quelle nicht gefunden. Einrichtung: SERVER.md, Abschnitt „Direkt aus GitHub“."
APP_ROOT="$(dirname "$SOURCE")"   # die Quelle liegt neben der Installation: /opt/ju-finanzen/quelle und /opt/ju-finanzen/current
[ -d "$SOURCE/.git" ] && [ -f "$SOURCE/app.py" ] && [ -f "$SOURCE/server/install.sh" ] \
  || die "$SOURCE ist kein Checkout des Programms. Einrichtung: SERVER.md, Abschnitt „Direkt aus GitHub“."
if [ "$CHECK" -eq 0 ] && [ "$INSTALL" -eq 1 ]; then
  [ "$(id -u)" -eq 0 ] || die "Bitte mit sudo ausführen: sudo ju-finanzen aktualisieren"
  # root führt gleich install.sh aus diesem Ordner aus – er darf deshalb niemand anderem gehören
  [ "$(stat -c %U "$SOURCE")" = root ] || die "$SOURCE gehört nicht root – abgebrochen."
fi

g() { git -C "$SOURCE" "$@"; }
version_in() { [ -f "$1" ] || return 0; sed -n "s/^VERSION='\(.*\)'.*/\1/p" "$1" | head -n 1; }
newer() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$2" ]; }   # ist $2 neuer als $1?

[ -z "$(g status --porcelain --untracked-files=no)" ] \
  || die "In $SOURCE wurden Dateien von Hand geändert – abgebrochen. Ansehen: git -C $SOURCE status"

INSTALLED="$(version_in "$APP_ROOT/current/app.py")"
CURRENT="${INSTALLED:-$(version_in "$SOURCE/app.py")}"
[ -n "$CURRENT" ] || die "Aktuelle Version nicht gefunden."

say "Freigegebene Versionen holen"
# ohne --force: ein nachträglich versetzter Tag ist ein Fehler, kein Update
g fetch --quiet --tags origin 2>/dev/null \
  || die "Abruf fehlgeschlagen. Entweder ist das Repository nicht erreichbar (Netz, Zugangsschlüssel), oder ein Versions-Tag wurde nachträglich verändert. Ansehen: git -C $SOURCE fetch --tags origin"

TAGS="$(g tag -l 'v[0-9]*' | grep -E '^v[0-9]+(\.[0-9]+)+$' | sed 's/^v//' | sort -V || true)"
[ -n "$TAGS" ] || die "Im Repository gibt es keine freigegebene Version (Tag v<Version>)."
LATEST="$(printf '%s\n' "$TAGS" | tail -n 1)"
TARGET="${WANT:-$LATEST}"
printf '%s\n' "$TAGS" | grep -qx -- "$TARGET" || die "Version $TARGET gibt es nicht. Neueste freigegebene Version: $LATEST"

info "installiert: $CURRENT      neueste freigegebene Version: $LATEST"
if [ "$TARGET" = "$CURRENT" ]; then
  info "Version $CURRENT ist bereits aktiv – nichts zu tun."
  exit 0
fi
newer "$CURRENT" "$TARGET" \
  || die "Version $TARGET ist älter als die aktive $CURRENT. Zurück zur vorherigen Version: sudo $APP_ROOT/current/server/install.sh --zurueck"

COMMIT="$(g rev-parse --verify --quiet "refs/tags/v$TARGET^{commit}")" || die "Tag v$TARGET lässt sich nicht lesen."
info "neu: $TARGET (Stand ${COMMIT:0:12})"
if [ "$CHECK" -eq 1 ]; then
  info "Nur Prüfung (--pruefen): nichts installiert. Installieren: sudo ju-finanzen aktualisieren"
  exit 0
fi

PREVIOUS="$(g rev-parse HEAD)"
back() { g checkout --quiet --detach "$PREVIOUS" 2>/dev/null || true; }
g checkout --quiet --detach "$COMMIT" || die "Stand $TARGET lässt sich nicht bereitstellen."
FOUND="$(version_in "$SOURCE/app.py")"
if [ "$FOUND" != "$TARGET" ]; then
  back
  die "Tag v$TARGET enthält Programmversion „${FOUND:-unbekannt}“ – passt nicht zusammen, nichts installiert."
fi
if [ "$INSTALL" -eq 0 ]; then
  info "Stand $TARGET bereitgestellt (ohne Installation)."
  exit 0
fi

say "Version $TARGET installieren"
if ! "$SOURCE/server/install.sh"; then
  back
  die "Installation von $TARGET fehlgeschlagen – die bisherige Version $CURRENT läuft weiter."
fi
