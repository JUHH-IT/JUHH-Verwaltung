#!/usr/bin/env bash
# JU Hamburg Finanzen – Update des Servers aus dem Git-Repository (z. B. privates Repository bei GitHub).
#
# Aufruf über den Verwaltungsbefehl:
#   sudo ju-finanzen aktualisieren              neueste freigegebene Version holen und installieren
#   sudo ju-finanzen aktualisieren 0.34         genau diese Version
#   sudo ju-finanzen aktualisieren --pruefen    nur zeigen, welche Version es gäbe (installiert nichts)
#
# Die Quelle ist ein Git-Checkout unter /opt/ju-finanzen/quelle (Einrichtung: SERVER.md, „Direkt aus GitHub“).
# Installiert werden nur freigegebene Versionen: ein Tag „v<Version>“ auf dem Zweig main, der genauso im Repository
# steht und dessen app.py genau diese Version nennt. Nie automatisch und nie der bloße Entwicklungsstand. Installation, Selbsttest und automatisches Zurückschalten
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
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
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
  # root führt gleich install.sh aus diesem Ordner aus – nichts darin darf jemand anderem gehören oder für andere schreibbar sein
  [ -z "$(find "$SOURCE" \( ! -user root -o -perm /022 \) ! -type l -print -quit)" ] \
    || die "In $SOURCE gehört etwas nicht root oder ist für andere schreibbar – abgebrochen. Ansehen: sudo find $SOURCE \\( ! -user root -o -perm /022 \\) ! -type l"
fi

# keine Hooks aus dem Checkout ausführen; Meldungen von git immer englisch (werden unten ausgewertet)
g() { LC_ALL=C git -C "$SOURCE" -c core.hooksPath=/dev/null "$@"; }
version_in() { [ -f "$1" ] || return 0; sed -n "s/^VERSION='\(.*\)'.*/\1/p" "$1" | head -n 1; }
newer() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$2" ]; }   # ist $2 neuer als $1?

# auch zusätzliche (unversionierte oder ignorierte) Dateien zählen: install.sh würde sie mitkopieren
[ -z "$(g status --porcelain --ignored)" ] \
  || die "In $SOURCE wurden Dateien von Hand geändert oder hinzugefügt – abgebrochen. Ansehen: git -C $SOURCE status --ignored"

INSTALLED="$(version_in "$APP_ROOT/current/app.py")"
CURRENT="${INSTALLED:-$(version_in "$SOURCE/app.py")}"
[ -n "$CURRENT" ] || die "Aktuelle Version nicht gefunden."

say "Freigegebene Versionen holen"
g remote get-url origin >/dev/null 2>&1 || die "In $SOURCE ist kein Repository als „origin“ eingetragen. Einrichtung: SERVER.md, Abschnitt „Direkt aus GitHub“."
# ohne --force: ein nachträglich versetzter Tag ist ein Fehler, kein Update
if ! FETCH_ERROR="$(g fetch --tags origin 2>&1)"; then   # ohne --quiet: sonst fehlt der Grund
  case "$FETCH_ERROR" in
    *"would clobber existing tag"*)
      die "Eine freigegebene Version (Tag) wurde im Repository nachträglich verändert – nichts installiert. Bitte klären, wer den Tag versetzt hat; im Repository den Tag auf den alten Stand zurücksetzen (nie mit --force holen). Ansehen: git -C $SOURCE fetch --tags origin" ;;
    *) die "Abruf fehlgeschlagen: Das Repository ist nicht erreichbar (Netz oder Zugangsschlüssel). Ansehen: git -C $SOURCE fetch --tags origin" ;;
  esac
fi

# Es zählen nur Tags, die jetzt im Repository stehen – eine dort zurückgezogene Freigabe gilt nicht mehr
REMOTE="$(g ls-remote --tags origin 'v*')" || die "Abruf der freigegebenen Versionen fehlgeschlagen (Netz oder Zugangsschlüssel)."
TAGS="$(printf '%s\n' "$REMOTE" | sed -n 's#^[0-9a-f]*[[:space:]]*refs/tags/v\([0-9][0-9.]*\)$#\1#p' | grep -E '^[0-9]+(\.[0-9]+)+$' | sort -V || true)"
[ -n "$TAGS" ] || die "Im Repository gibt es keine freigegebene Version (Tag v<Version>)."
LATEST="$(printf '%s\n' "$TAGS" | tail -n 1)"
TARGET="${WANT:-$LATEST}"
printf '%s\n' "$TAGS" | grep -qxF -- "$TARGET" || die "Version $TARGET gibt es nicht. Neueste freigegebene Version: $LATEST"

info "installiert: $CURRENT      neueste freigegebene Version: $LATEST"
if [ "$TARGET" = "$CURRENT" ]; then
  info "Version $CURRENT ist bereits aktiv – nichts zu tun."
  exit 0
fi
newer "$CURRENT" "$TARGET" \
  || die "Version $TARGET ist älter als die aktive $CURRENT. Zurück zur vorherigen Version: sudo $APP_ROOT/current/server/install.sh --zurueck"

COMMIT="$(g rev-parse --verify --quiet "refs/tags/v$TARGET^{commit}")" || die "Tag v$TARGET lässt sich nicht lesen."
# der geholte Tag muss derselbe sein wie im Repository (bei Tags mit Beschreibung zählt der Commit dahinter)
REMOTE_COMMIT="$(printf '%s\n' "$REMOTE" | awk -v tag="refs/tags/v$TARGET" '$2 == tag "^{}" { peeled = $1 } $2 == tag { plain = $1 } END { print (peeled != "" ? peeled : plain) }')"
[ "$REMOTE_COMMIT" = "$COMMIT" ] || die "Tag v$TARGET stimmt nicht mit dem Repository überein – nichts installiert."
# freigegeben wird nur, was auf main liegt (dort gelten Pull Request und Tests)
g rev-parse --verify --quiet refs/remotes/origin/main >/dev/null || die "Im Repository fehlt der Zweig main."
g merge-base --is-ancestor "$COMMIT" refs/remotes/origin/main \
  || die "Tag v$TARGET liegt nicht auf dem Zweig main – nichts installiert."
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
