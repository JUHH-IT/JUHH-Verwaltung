#!/usr/bin/env bash
# JU Hamburg Finanzen – Härtung des Betriebssystems (Ubuntu 24.04, ein Server hinter Tailscale).
#
# Aufruf über den Verwaltungsbefehl:
#   sudo ju-finanzen haerten --pruefen     nur berichten: je Maßnahme Stand → Änderung → Risiko (ändert nichts)
#   sudo ju-finanzen haerten               anwenden: sichert jede geänderte Datei vorher, beliebig oft wiederholbar
#   sudo ju-finanzen haerten --bestaetigen aus einem ZWEITEN Fenster: Änderungen an Firewall/SSH-Zugang behalten
#   sudo ju-finanzen haerten --zurueck     Stand vor dem letzten Lauf wiederherstellen (mit --pruefen: nur zeigen)
#   sudo ju-finanzen schutz-status         Statusbericht des Serverschutzes auf der Konsole
# Riskante Schalter – werden nur angenommen, wenn der Nachweis gelingt (sonst Abbruch, bevor etwas geändert wird):
#   --ssh-nur-schluessel   SSH-Anmeldung per Passwort abschalten (nur wenn ein Schlüssel hinterlegt und benutzt wurde)
#   --ssh-nur-tailscale    neue SSH-Verbindungen nur noch aus dem Tailnet (nur wenn Tailscale läuft und SSH dort geht)
#
# Ändert ein Lauf die Firewall oder einen der beiden SSH-Schalter, plant er VORHER mit systemd-run eine Rücknahme, die
# nicht an der Sitzung hängt (Unit ju-haertung-ruecknahme, Vorgabe 5 Minuten). Nur „haerten --bestaetigen“ aus einer
# anderen Anmeldesitzung sagt sie ab – das beweist, dass eine neue Anmeldung noch gelingt.
# --ohne-rueckfrage: keine Bestätigung nötig, es wird keine Rücknahme geplant (nur für Tests und die Rücknahme-Unit, die
# „--zurueck --ohne-rueckfrage“ aufruft und damit ausschließlich einen noch unbestätigten Lauf zurücknimmt).
#
# Maßnahmen und Schalter: /etc/ju-finanzen/haertung.conf (Vorlage server/haertung/haertung.conf), Beschreibung in
# SERVER.md, Abschnitt „Härtung“. Sicherung und Protokoll je Lauf: /var/backups/ju-haertung/<Zeit>/.
# Zeitgeber rufen auf: haerten.sh sperrliste (täglich) und haerten.sh status-schreiben (alle 15 Minuten).
#
# Grundsätze: niemanden aussperren. Geändert wird nur über tu() – mit --pruefen berichtet tu() bloß. Jede Maßnahme
# läuft für sich; scheitert sie, wird sie zurückgenommen und der Lauf geht mit den übrigen weiter (am Ende: Liste und
# Rückgabe 1). An der Art der SSH-Anmeldung und an vorhandenen Firewall-Regeln ändert das Skript ohne die Schalter nichts.
# WURZEL (nur Tests): nachgestelltes Wurzelverzeichnis; Systembefehle kommen dann ausschließlich aus $WURZEL/stub.
# shellcheck disable=SC1111        # deutsche Anführungszeichen in Meldungen sind Absicht
# shellcheck disable=SC2030,SC2031  # GEAENDERT/GETAN/RISKANT gelten bewusst nur innerhalb einer Maßnahme (eigene Shell)
# shellcheck disable=SC2329        # Maßnahmen und ihre Schritte ruft das Skript über massnahme() und tu() auf
set -euo pipefail
umask 022
export LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive

W="${WURZEL:-}"; W="${W%/}"
HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETC=/etc/ju-finanzen
CONF="$W$ETC/haertung.conf"
LIB=/var/lib/ju-serverschutz
SICHERUNGEN="$W/var/backups/ju-haertung"
UNITS=/etc/systemd/system
NFT_DATEI=$LIB/ju_schutz.nft        # Regeln: ändert nur ein haerten-Lauf (steht im MANIFEST)
MENGEN_DATEI=$LIB/ju_mengen.nft     # Sperrliste: tauscht auch der tägliche Zeitgeber aus (Arbeitsdatei, nie im MANIFEST)
RUECK_UNIT=ju-haertung-ruecknahme
SSH_DATEI=/etc/ssh/sshd_config.d/00-ju-haertung.conf
KERN_DATEI=/etc/sysctl.d/80-ju-haertung.conf
PY="${JU_PYTHON:-/usr/bin/python3}"
PAUSE="${JU_PAUSE:-3}"            # Sekunden bis zur Nachprüfung der Firewall (Tests: 0)
FRIST="${JU_FRIST:-300}"          # Sekunden bis zur geplanten Rücknahme (Tests: kürzer)
LOG=""; MANIFEST=""; SICHERUNG=""; TMP=""
PRUEFEN=0; ZURUECK=0; BESTAETIGEN=0; RUECKFRAGE=1; OPT_SCHLUESSEL=0; OPT_TAILSCALE=0; BEFEHL=haerten
APT_AKTUELL=0; GEAENDERT=0; GETAN=0; RISKANT=0; LAUFEND=""; MARKE=0

# „|| true“: ein abgerissenes Terminal darf weder Lauf noch Rücknahme mitten im Schritt beenden – das Protokoll bekommt die Zeile
aus()     { printf '%s\n' "$*" 2>/dev/null || true; if [ -n "$LOG" ]; then printf '%s\n' "$*" >> "$LOG"; fi; }
say()     { aus ""; aus "==> $*"; }
info()    { aus "    $*"; }
stand()   { aus "    Stand:    $*"; }
hinweis() { aus "    Hinweis:  $*"; }
risiko()  { aus "    Risiko:   $*"; }
die() {
  printf '\nFEHLER: %s\n' "$*" >&2 || true
  if [ -n "$LOG" ]; then printf 'FEHLER: %s\n' "$*" >> "$LOG"; fi
  if [ -n "$TMP" ]; then printf '%s' "$*" > "$TMP/grund"; fi   # massnahme() nennt den Grund in der Liste am Ende
  exit 1
}

# Test-Schalter gelten nie für die installierte Version: sonst ließe sich root ein fremdes Wurzelverzeichnis oder Python unterschieben
if [ "$(id -u)" -eq 0 ] && [[ "$(readlink -f "$HIER")" == /opt/ju-finanzen/* ]] && [ -n "${WURZEL:-}${JU_PYTHON:-}${JU_FRIST:-}${JU_PAUSE:-}" ]; then
  die "WURZEL, JU_PYTHON, JU_FRIST und JU_PAUSE sind Test-Schalter und für die installierte Version gesperrt (unset WURZEL JU_PYTHON JU_FRIST JU_PAUSE)."
fi

# Systembefehle: im Test nur aus $WURZEL/stub – so fasst ein Test nie das echte System an
hat() { if [ -n "$W" ]; then [ -x "$W/stub/$1" ]; else command -v "$1" >/dev/null 2>&1; fi; }
# 9>&-: Kinder erben die Sperrdatei nicht – ein von apt gestarteter Dienst hielte sie sonst über das Ende des Laufs hinaus
x()   { local c="$1"; shift; if [ -n "$W" ]; then "$W/stub/$c" "$@" 9>&-; else command "$c" "$@" 9>&-; fi; }
py()  { WURZEL="$W" "$PY" "$HIER/haertung/serverschutz.py" "$@" 9>&-; }
# sshd -t/-T brauchen /run/sshd; der Ordner fehlt, solange ssh.service seit dem Start nie lief (Socket-Aktivierung)
sshd_bereit() { if [ ! -d "$W/run/sshd" ] && hat sshd; then install -d -m 755 "$W/run/sshd"; fi; }
sshd_x() { if [ "$PRUEFEN" -eq 0 ]; then sshd_bereit; fi; x sshd "$@"; }   # der Prüflauf legt auch diesen Ordner nicht an
enthaelt() { grep -E -- "$1" >/dev/null; }   # ohne -q: liest die Eingabe zu Ende (sonst bricht pipefail die Pipe)

wert() {   # NAME VORGABE – Wert aus haertung.conf (die Datei wird gelesen, nie ausgeführt)
  local v=""
  if [ -f "$CONF" ]; then v="$(sed -n "s/^$1=//p" "$CONF" | tail -n 1 | tr -d '\r' | sed 's/[[:space:]]*#.*$//; s/[[:space:]]*$//')"; fi
  printf '%s' "${v:-$2}"
}
an() {     # NAME VORGABE – Schalter ja/nein
  case "$(wert "$1" "$2")" in ja) return 0 ;; nein) return 1 ;; *) die "$CONF: $1 muss ja oder nein sein." ;; esac
}

# ---------------------------------------------------------------- Ändern, Sichern, Zurücknehmen

# tu "Beschreibung" Befehl … – die einzige Stelle, an der etwas geändert wird; --pruefen berichtet nur
tu() {
  local text="$1"; shift
  echo x >> "$ZAEHLER"; GETAN=1
  if [ "$PRUEFEN" -eq 1 ]; then
    if [ "$RISKANT" -eq 1 ]; then : > "$TMP/riskant"; fi
    aus "    Änderung: $text"; return 0
  fi
  if [ "$RISKANT" -eq 1 ]; then ruecknahme_planen; fi   # vor der Änderung – ohne Plan findet sie nicht statt
  aus "    Ändere:   $text"
  "$@"
}

gemerkt() { awk -F'\t' -v a="$1" -v b="$2" '$1 == a && $2 == b { f = 1 } END { exit !f }' "$MANIFEST"; }

merke_datei() {   # Datei vor der ersten Änderung dieses Laufs in die Sicherung legen
  local p="$1"
  if gemerkt datei "$p"; then return 0; fi
  if [ -e "$W$p" ]; then
    install -d -m 700 "$SICHERUNG/dateien$(dirname "$p")"
    cp -a "$W$p" "$SICHERUNG/dateien$p"
    printf 'datei\t%s\tvorhanden\n' "$p" >> "$MANIFEST"
  else
    printf 'datei\t%s\tneu\n' "$p" >> "$MANIFEST"
  fi
}

# datei_setzen ZIEL MODUS < Inhalt – schreibt nur bei Abweichung, sichert vorher, ersetzt in einem Schritt
datei_setzen() {
  local p="$1" modus="$2" tmp
  tmp="$(mktemp "$TMP/datei.XXXXXX")"
  cat > "$tmp"
  if [ -f "$W$p" ] && cmp -s "$tmp" "$W$p" && [ "$(stat -c %a "$W$p")" = "$modus" ]; then return 0; fi
  GEAENDERT=1
  tu "$p $(if [ -e "$W$p" ]; then echo anpassen; else echo anlegen; fi)" datei_schreiben "$tmp" "$p" "$modus"
}
datei_schreiben() {
  merke_datei "$2"
  mkdir -p "$(dirname "$W$2")"   # nicht „install -d -m“: das würde die Rechte eines vorhandenen Ordners ändern
  install -m "$3" "$1" "$W$2.ju-neu"
  mv -f "$W$2.ju-neu" "$W$2"
  summe_merken "$2"
}
# Stand nach der eigenen Änderung: --zurueck überschreibt eine Datei nicht, die danach jemand von Hand geändert hat
summe() { if [ -e "$W$1" ]; then sha256sum < "$W$1" | cut -d' ' -f1; else echo -; fi; }
summe_merken() { printf 'summe\t%s\t%s\n' "$1" "$(summe "$1")" >> "$MANIFEST"; }
datei_entfernen() {
  if [ -e "$W$1" ]; then GEAENDERT=1; tu "$1 entfernen" datei_loeschen "$1"; fi
}
datei_loeschen() { merke_datei "$1"; rm -f -- "$W$1"; summe_merken "$1"; }

rechte_soll() {   # PFAD MODUS BESITZER – nur vorhandene Pfade, nie Verweise
  local p="$1" ist
  if [ ! -e "$W$p" ]; then return 0; fi
  if [ -L "$W$p" ]; then hinweis "$p ist ein symbolischer Verweis – nicht angefasst"; return 0; fi
  ist="$(stat -c '%a %U:%G' "$W$p")"
  if [ -n "$W" ]; then ist="${ist%% *} $3"; fi   # Tests: nur die Rechte zählen, Besitzer bleiben
  if [ "$ist" = "$2 $3" ]; then return 0; fi
  tu "Rechte von $p: $ist → $2 $3" rechte_setzen "$p" "$2" "$3" "$ist"
}
rechte_setzen() {
  printf 'rechte\t%s\t%s\n' "$1" "$4" >> "$MANIFEST"
  chmod "$2" "$W$1"
  if [ -z "$W" ]; then chown "$3" "$W$1"; fi
}

sys_wert() { local v; v="$(x systemctl "$1" "$2" 2>/dev/null | head -n 1 || true)"; printf '%s' "${v:-unbekannt}"; }
dienst_merken() {
  if gemerkt dienst "$1"; then return 0; fi
  local e a
  e="$(sys_wert is-enabled "$1")"; a="$(sys_wert is-active "$1")"
  printf 'dienst\t%s\t%s\t%s\n' "$1" "$e" "$a" >> "$MANIFEST"
}
dienst_schalten() { dienst_merken "$1"; x systemctl "$2" --now -q "$1"; }
dienst_an() {
  if [ "$(sys_wert is-enabled "$1")" = enabled ] && [ "$(sys_wert is-active "$1")" = active ]; then return 0; fi
  tu "$1 einschalten" dienst_schalten "$1" enable
}
dienst_aus() {
  if [ "$(sys_wert is-enabled "$1")" != enabled ] && [ "$(sys_wert is-active "$1")" != active ]; then return 0; fi
  tu "$1 abschalten (wieder an: sudo systemctl enable --now $1)" dienst_schalten "$1" disable
}
units_neu() { if [ "$GEAENDERT" -eq 1 ]; then tu "systemd: geänderte Units einlesen" x systemctl daemon-reload; fi; }
unit_setzen() { datei_setzen "$UNITS/$1" 644 < "$HIER/haertung/$1"; }

# shellcheck disable=SC2016
paket_da() { hat dpkg-query && x dpkg-query -W -f='${Status}' "$1" 2>/dev/null | enthaelt 'install ok installed'; }
paket() {
  if paket_da "$1"; then return 0; fi
  tu "Paket $1 aus den Ubuntu-Quellen installieren" paket_installieren "$1"
}
paket_installieren() {
  # Zustand vor der Installation: --zurueck schaltet einen neu installierten Dienst wieder ab. Nie nftables.service:
  # dessen ExecStop ist „flush ruleset“ und löschte die Regeln von ufw und Tailscale – gebraucht wird nur der Befehl nft.
  if [ "$1" != nftables ]; then dienst_merken "$1.service"; fi
  printf 'paket\t%s\n' "$1" >> "$MANIFEST"
  # wartet bis zu zwei Minuten, falls gerade ein anderes Update läuft (unattended-upgrades hält dann die Sperre von dpkg)
  if [ "$APT_AKTUELL" -eq 0 ]; then x apt-get -o DPkg::Lock::Timeout=120 update -qq || true; APT_AKTUELL=1; fi
  x apt-get -o DPkg::Lock::Timeout=120 install -y -qq "$1" >/dev/null \
    || die "Das Paket $1 ließ sich nicht installieren. Was tun: einige Minuten warten (oft läuft gerade ein anderes Update), dann „sudo apt-get update && sudo apt-get install $1“ von Hand ausführen, die Meldung lesen und danach erneut: sudo ju-finanzen haerten"
}

conf_setzen() {   # NAME WERT – Schalter in haertung.conf eintragen (Datei entsteht bei Bedarf aus der Vorlage)
  local quelle="$CONF" tmp
  if [ ! -f "$quelle" ]; then quelle="$HIER/haertung/haertung.conf"; fi
  tmp="$(mktemp "$TMP/conf.XXXXXX")"
  if grep -q "^$1=" "$quelle"; then sed "s|^$1=.*|$1=$2|" "$quelle" > "$tmp"; else { cat "$quelle"; printf '%s=%s\n' "$1" "$2"; } > "$tmp"; fi
  datei_setzen "$ETC/haertung.conf" 644 < "$tmp"
}

# Manifest eines Laufs ab Zeile MARKE rückwärts zurücknehmen: erst Dienste (solange ihre Units noch da sind), dann
# Dateien, Rechte, Kern-Werte und die eigenen Änderungen an ufw; zuletzt lesen die betroffenen Dienste den alten Stand ein.
zurueck_ab() {
  local sich="$1" marke="$2" teil art a b c soll fehler=0
  local ssh=0 journald=0 units=0 f2b=0 nft=0
  teil="$(mktemp "$TMP/zurueck.XXXXXX")"
  tail -n "+$((marke + 1))" "$sich/MANIFEST" | tac > "$teil"
  while IFS=$'\t' read -r art a b c; do
    if [ "$art" != dienst ] || [ "$a" = nftables.service ]; then continue; fi   # nftables.service nie: ExecStop ist „flush ruleset“
    case "$b" in
      enabled) x systemctl enable -q "$a" || fehler=1 ;;
      disabled|not-found|unbekannt) x systemctl disable -q "$a" 2>/dev/null || true ;;
    esac
    case "$c" in
      active) x systemctl start "$a" || fehler=1 ;;
      inactive|failed|not-found) x systemctl stop "$a" 2>/dev/null || true ;;
    esac
    aus "    Zurück:   Dienst $a (war: ${b}, ${c})"
  done < "$teil"
  while IFS=$'\t' read -r art a b c; do
    case "$art" in
      datei)
        soll="$(awk -F'\t' -v p="$a" '$1 == "summe" && $2 == p { s = $3 } END { print s }' "$sich/MANIFEST")"
        if [ -n "$soll" ] && [ "$(summe "$a")" != "$soll" ]; then
          aus "    HINWEIS:  $a wurde seit dem Lauf von Hand geändert – bleibt, wie es ist$(if [ "$b" = vorhanden ]; then echo " (Fassung vor dem Lauf: ${sich#"$W"}/dateien$a)"; fi)"
          continue
        fi
        if [ "$b" = vorhanden ]; then
          if ! { cp -a "$sich/dateien$a" "$W$a.ju-zurueck" && mv -f "$W$a.ju-zurueck" "$W$a"; }; then fehler=1; fi
        else
          rm -f -- "$W$a"
        fi
        aus "    Zurück:   $a ($(if [ "$b" = vorhanden ]; then echo wiederhergestellt; else echo entfernt; fi))"
        case "$a" in
          /etc/ssh/*) ssh=1 ;;
          /etc/systemd/journald.conf.d/*) journald=1 ;;
          "$UNITS"/*) units=1 ;;
          /etc/fail2ban/*) f2b=1 ;;
          "$NFT_DATEI") nft=1 ;;
        esac ;;
      rechte)
        chmod "${b%% *}" "$W$a" || fehler=1
        if [ -z "$W" ]; then chown "${b##* }" "$W$a" || fehler=1; fi
        aus "    Zurück:   Rechte von $a ($b)" ;;
      sysctl)
        x sysctl -q -w "$a=$b" 2>/dev/null || aus "    Hinweis:  $a lässt sich erst mit einem Neustart zurückstellen" ;;
      ufw)   # nur der Gegenbefehl zur eigenen Änderung – Regeln, die seither jemand ergänzt hat, bleiben
        # shellcheck disable=SC2086  # der Gegenbefehl besteht aus festen Wörtern und Portnummern
        x ufw $a >/dev/null || fehler=1
        aus "    Zurück:   ufw $a" ;;
      ufwsysctl)
        if [ -f "$W/etc/ufw/sysctl.conf" ]; then sed -i "s|^$a=.*|$a=$b|" "$W/etc/ufw/sysctl.conf" || fehler=1; fi
        aus "    Zurück:   /etc/ufw/sysctl.conf: $a=$b" ;;
      paket) aus "    Hinweis:  Paket $a bleibt installiert (entfernen: sudo apt remove $a)" ;;
    esac
  done < "$teil"
  if [ "$units" -eq 1 ]; then x systemctl daemon-reload || fehler=1; fi
  if [ "$nft" -eq 1 ]; then
    if [ -f "$W$NFT_DATEI" ] && [ "$(sys_wert is-active ju-serverschutz.service)" = active ]; then
      x systemctl restart ju-serverschutz.service || fehler=1
    elif hat nft && x nft list table inet ju_schutz >/dev/null 2>&1; then
      x nft delete table inet ju_schutz || fehler=1   # nur die eigene Tabelle – ufw und Tailscale bleiben
    fi
  fi
  if [ "$ssh" -eq 1 ]; then
    if sshd_x -t; then ssh_neu_laden; else fehler=1; aus "    FEHLER:   sshd -t meldet Fehler – SSH nicht neu geladen"; fi
  fi
  if [ "$journald" -eq 1 ]; then x systemctl try-restart systemd-journald.service || fehler=1; fi
  if [ "$f2b" -eq 1 ] && [ "$(sys_wert is-active fail2ban.service)" = active ]; then x systemctl restart fail2ban.service || fehler=1; fi
  rm -f "$teil"
  return "$fehler"
}

# massnahme NUMMER TITEL FUNKTION – eigene Shell mit „set -e“: jeder Fehler beendet nur diese Maßnahme. Sie wird
# zurückgenommen, der Lauf geht mit den übrigen weiter; am Ende steht die Liste „nicht angewendet“ (Rückgabe 1).
massnahme() {
  local vorher rc grund
  say "$1. $2"
  vorher="$(wc -l < "$ZAEHLER")"
  if [ "$PRUEFEN" -eq 0 ]; then MARKE="$(wc -l < "$MANIFEST")"; fi
  : > "$TMP/grund"
  LAUFEND="$2"
  set +e
  ( set -e; GEAENDERT=0; GETAN=0; RISKANT=0; "$3" )
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    if [ "$PRUEFEN" -eq 0 ]; then
      aus "    Fehler in dieser Maßnahme – sie wird zurückgenommen; der Lauf geht mit den übrigen Maßnahmen weiter."
      massnahme_zurueck || aus "    Zurücknehmen unvollständig – bitte die Meldungen oben prüfen."
    fi
    head -n "$vorher" "$ZAEHLER" > "$ZAEHLER.neu" && mv -f "$ZAEHLER.neu" "$ZAEHLER"
    grund="$(cat "$TMP/grund")"
    printf '%s (%s)\n' "$2" "${grund:-unerwarteter Fehler – Meldung oben}" >> "$TMP/nicht"
  elif [ "$(wc -l < "$ZAEHLER")" = "$vorher" ]; then
    if [ "$PRUEFEN" -eq 1 ]; then aus "    Änderung: keine"; else info "bereits umgesetzt – nichts geändert"; fi
  fi
  LAUFEND=""
}
massnahme_zurueck() {
  local rc=0
  zurueck_ab "$SICHERUNG" "$MARKE" || rc=1
  head -n "$MARKE" "$MANIFEST" > "$MANIFEST.neu" && mv -f "$MANIFEST.neu" "$MANIFEST"
  return "$rc"
}
nicht_melden() {   # $1 = Überschrift; Rückgabe 1, wenn eine Maßnahme ausfiel
  if [ ! -s "$TMP/nicht" ]; then return 0; fi
  say "$1"
  while IFS= read -r zeile; do aus "    - $zeile"; done < "$TMP/nicht"
  return 1
}

# INT/TERM/HUP (Strg-C, Fenster geschlossen, Verbindung abgerissen): die begonnene Maßnahme zurücknehmen. Eine schon
# geplante Rücknahme bleibt bestehen – sie hängt nicht an dieser Sitzung und nimmt den Rest des Laufs zurück.
abbruch() {
  trap '' INT TERM HUP
  set +e
  if [ "$1" = HUP ]; then exec >/dev/null 2>&1; fi   # das Terminal ist weg; die Meldungen stehen im Protokoll
  aus ""; aus "ABGEBROCHEN (Signal $1)."
  if [ -n "$LAUFEND" ] && [ -n "$MANIFEST" ]; then
    aus "    Die begonnene Maßnahme „$LAUFEND“ wird zurückgenommen."
    massnahme_zurueck || aus "    Zurücknehmen unvollständig – bitte prüfen: sudo ju-finanzen haerten --zurueck"
  fi
  if [ -n "$SICHERUNG" ] && [ -e "$SICHERUNG/RUECKNAHME" ]; then
    aus "    Die geplante Rücknahme bleibt bestehen: Ohne „sudo ju-finanzen haerten --bestaetigen“ aus einem zweiten Fenster wird der Lauf von selbst zurückgenommen."
  elif [ -n "$MANIFEST" ] && [ -s "$MANIFEST" ]; then
    aus "    Frühere Maßnahmen dieses Laufs bleiben. Alles zurück: sudo ju-finanzen haerten --zurueck"
  fi
  exit 130
}

# ---------------------------------------------------------------- Rücknahme, die nicht an der Sitzung hängt

# Kennung der Anmeldesitzung: Audit-Sitzung (vergibt die Anmeldung, alle Kinder erben sie), ersatzweise SSH-Verbindung bzw. Terminal
sitzung() {
  local s
  s="$(cat "$W/proc/self/sessionid" 2>/dev/null || true)"
  case "$s" in ""|4294967295) s="${SSH_CONNECTION:-$(if tty -s; then tty; fi)}" ;; esac
  printf '%s' "${s:-unbekannt}"
}
# Ordner des Laufs, dessen Rücknahme noch aussteht (leer: keiner). Datei RUECKNAHME: Zeile 1 Ende der Frist, Zeile 2 Sitzung.
ausstehend() { local f; for f in "$SICHERUNGEN"/*/RUECKNAHME; do if [ -f "$f" ]; then dirname "$f"; fi; done | tail -n 1; }
dauer() { if [ "$1" -ge 120 ]; then echo "$((($1 + 59) / 60)) Minuten"; else echo "$1 Sekunden"; fi; }
frist_text() {   # $1 = Ordner des Laufs
  local ende rest
  ende="$(sed -n 1p "$1/RUECKNAHME")"; rest=$((ende - $(date +%s)))
  if [ "$rest" -gt 0 ]; then echo "In $(dauer "$rest") (um $(date -d "@$ende" +%H:%M:%S) Uhr) werden die Änderungen von selbst zurückgenommen."
  else echo "Die Frist ist abgelaufen: Die Rücknahme läuft gerade – oder sie fand nicht statt (z. B. nach einem Neustart des Servers)."; fi
}
ausstehend_zeigen() {   # --pruefen und schutz-status
  local a
  a="$(ausstehend)"
  if [ -z "$a" ]; then return 0; fi
  say "ACHTUNG: Der Lauf $(basename "$a") ist noch nicht bestätigt."
  info "$(frist_text "$a")"
  info "Behalten: ein ZWEITES Fenster öffnen, dort neu anmelden und ausführen:  sudo ju-finanzen haerten --bestaetigen"
  info "Sofort zurücknehmen:  sudo ju-finanzen haerten --zurueck"
}
# Vor der ersten Änderung an Firewall oder SSH-Zugang (aus tu): Rücknahme als eigene Timer-Unit planen. Sie überlebt das
# Ende der Sitzung. Lässt sie sich nicht planen, scheitert die Maßnahme hier – bevor sie etwas geändert hat.
ruecknahme_planen() {
  if [ "$RUECKFRAGE" -eq 0 ] || [ -e "$SICHERUNG/RUECKNAHME" ]; then return 0; fi
  hat systemd-run || die "nicht angewendet, weil sich die automatische Rücknahme nicht planen lässt (systemd-run fehlt)"
  x systemd-run --quiet --collect --unit="$RUECK_UNIT" --on-active="$FRIST" --timer-property=AccuracySec=1s \
      --description="JU Hamburg Finanzen – Härtung zurücknehmen, falls sie nicht bestätigt wird" \
      "$HIER/haerten.sh" --zurueck --ohne-rueckfrage \
    || die "nicht angewendet, weil sich die automatische Rücknahme nicht planen ließ (systemd-run meldet einen Fehler)"
  printf '%s\n%s\n' "$(($(date +%s) + FRIST))" "$SITZUNG" > "$SICHERUNG/RUECKNAHME"
  aus "    Geplant:  automatische Rücknahme in $(dauer "$FRIST"), falls dieser Lauf nicht aus einem zweiten Fenster bestätigt wird"
}
ruecknahme_absagen() {
  x systemctl stop "$RUECK_UNIT.timer" 2>/dev/null || true   # schon abgelaufen oder nach einem Neustart: nichts zu stoppen
  [ "$(sys_wert is-active "$RUECK_UNIT.timer")" != active ] || die "Die geplante Rücknahme ließ sich nicht absagen (sudo systemctl stop $RUECK_UNIT.timer)."
}

# ---------------------------------------------------------------- Lage: SSH und ufw

SSH_PORTS=22; UFW_AKTIV=0; UFW_EINGANG=offen; UFW_REGELN=""; SSH_LAGE=offen; SSH_QUELLEN=0; SSH_FREI=0
eff() { if hat sshd; then sshd_x -T 2>/dev/null | awk -v k="$1" '$1 == k { print $2; exit }'; fi; }

lage_ermitteln() {
  local p o b ports=""
  if hat sshd; then ports="$({ sshd_x -T 2>/dev/null || true; } | awk '$1 == "port" && $2 ~ /^[0-9]+$/ { print $2 }' | sort -un | tr '\n' ' ')"; fi
  SSH_PORTS="${ports:-22}"
  if hat ufw; then
    if x ufw status 2>/dev/null | enthaelt '^Status: active'; then UFW_AKTIV=1; fi
    UFW_REGELN="$(x ufw show added 2>/dev/null || true)"
  fi
  case "$(sed -n 's/^DEFAULT_INPUT_POLICY="\(.*\)"/\1/p' "$W/etc/default/ufw" 2>/dev/null | tail -n 1)" in
    DROP|REJECT) UFW_EINGANG=gesperrt ;;
  esac
  # Regeln von ufw, die den SSH-Port erlauben: SSH_FREI = von überall, SSH_QUELLEN = nur von bestimmten Adressen
  for p in $SSH_PORTS; do
    read -r o b < <(printf '%s\n' "$UFW_REGELN" | awk -v p="$p" '
      $1 == "ufw" && ($2 == "allow" || $2 == "limit") {
        z = " " $0 " "
        if (z !~ (" " p "(/tcp)? ") && z !~ / (OpenSSH|ssh) /) next
        if (z ~ / on tailscale/) next
        if (z ~ / from / && z !~ / from any /) b++; else o++
      }
      END { print o + 0, b + 0 }')
    SSH_FREI=$((SSH_FREI + o)); SSH_QUELLEN=$((SSH_QUELLEN + b))
  done
  SSH_LAGE=zu
  if [ "$SSH_QUELLEN" -gt 0 ]; then SSH_LAGE=begrenzt; fi
  if [ "$SSH_FREI" -gt 0 ]; then SSH_LAGE=offen; fi
  if [ "$UFW_AKTIV" -eq 0 ] || [ "$UFW_EINGANG" != gesperrt ]; then SSH_LAGE=offen; fi
}
ssh_lage_text() {
  case "$SSH_LAGE" in
    offen) echo "aus dem ganzen Internet erreichbar" ;;
    begrenzt) echo "durch ufw auf $(if [ "$SSH_QUELLEN" -eq 1 ]; then echo "eine Quelle"; else echo "$SSH_QUELLEN Quellen"; fi) begrenzt" ;;
    *) echo "von außen gesperrt (nur Tailscale und Konsole des Anbieters)" ;;
  esac
}

# Nachweise für die riskanten Schalter: leere Ausgabe = erfüllt, sonst der Grund der Ablehnung
schluessel_nachweis() {
  local konto="${SUDO_USER:-root}" heim n
  heim="$(awk -F: -v k="$konto" '$1 == k { print $6 }' "$W/etc/passwd" 2>/dev/null || true)"
  n="$(grep -Ec '^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp[0-9]+|sk-[a-z0-9-]+@openssh\.com) AAAA' "$W${heim:-/nirgends}/.ssh/authorized_keys" 2>/dev/null || true)"
  if [ "${n:-0}" -lt 1 ]; then echo "für das Konto $konto ist kein SSH-Schlüssel hinterlegt (~/.ssh/authorized_keys)"; return 0; fi
  if [ "$(eff pubkeyauthentication)" != yes ]; then echo "der SSH-Dienst nimmt keine Schlüssel an (PubkeyAuthentication)"; return 0; fi
  if [ "$konto" = root ] && [ "$(eff permitrootlogin)" = no ]; then echo "root darf sich nicht per SSH anmelden (PermitRootLogin no)"; return 0; fi
  if ! hat journalctl || ! x journalctl -t sshd --since "-30 days" --no-pager -o cat 2>/dev/null | enthaelt "^Accepted publickey for $konto "; then
    echo "für $konto ist ein Schlüssel hinterlegt, aber in den letzten 30 Tagen keine Anmeldung damit gelungen – erst einmal mit dem Schlüssel anmelden, dann erneut aufrufen"
  fi
}
tailscale_nachweis() {
  local p
  if ! hat tailscale; then echo "Tailscale ist nicht installiert"; return 0; fi
  if ! x tailscale status --json 2>/dev/null | enthaelt '"BackendState": *"Running"'; then echo "Tailscale läuft nicht oder ist nicht verbunden"; return 0; fi
  if x tailscale debug prefs 2>/dev/null | enthaelt '"RunSSH": *true'; then return 0; fi
  for p in $SSH_PORTS; do
    if hat ss && x ss -Htn state established "( sport = :$p )" 2>/dev/null \
        | enthaelt '(^|[^0-9.])100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+:|\[fd7a:115c:a1e0:'; then return 0; fi
  done
  echo "kein SSH-Weg über das Tailnet nachgewiesen (Tailscale-SSH ist aus und keine SSH-Verbindung kommt gerade über Tailscale) – einmal über Tailscale anmelden und von dort erneut aufrufen"
}

NUR_SCHLUESSEL=0; NUR_TAILSCALE=0; ABGELEHNT=""
schalter_pruefen() {   # entscheidet vor jeder Änderung; ohne Nachweis bricht der Lauf ab, bevor etwas geändert ist
  local grund
  if [ "$OPT_SCHLUESSEL" -eq 1 ] || [ "$(wert SSH_NUR_SCHLUESSEL nein)" = ja ]; then
    if grep -qs '^PasswordAuthentication no' "$W$SSH_DATEI"; then NUR_SCHLUESSEL=1
    else grund="$(schluessel_nachweis)"
      if [ -z "$grund" ]; then NUR_SCHLUESSEL=1; else ABGELEHNT="${ABGELEHNT}SSH nur mit Schlüssel: $grund"$'\n'; fi
    fi
  fi
  if [ "$OPT_TAILSCALE" -eq 1 ] || [ "$(wert SSH_NUR_TAILSCALE nein)" = ja ]; then
    if grep -qs 'counter name ssh_fremd drop' "$W$NFT_DATEI"; then NUR_TAILSCALE=1
    else grund="$(tailscale_nachweis)"
      if [ -z "$grund" ]; then NUR_TAILSCALE=1; else ABGELEHNT="${ABGELEHNT}SSH nur aus dem Tailnet: $grund"$'\n'; fi
    fi
  fi
  if [ -n "$ABGELEHNT" ]; then
    say "Riskante Schalter"
    printf '%s' "$ABGELEHNT" | while IFS= read -r grund; do aus "    ABGELEHNT: $grund"; done
    if [ "$PRUEFEN" -eq 0 ]; then die "Abgelehnt – nichts geändert. Die Schalter weglassen (bzw. in $CONF auf nein stellen) oder die Voraussetzung schaffen."; fi
  fi
}

# ---------------------------------------------------------------- Maßnahmen

m_schalter() {
  if [ -f "$CONF" ]; then stand "$ETC/haertung.conf vorhanden"; else
    stand "$ETC/haertung.conf fehlt – es gelten die Vorgaben"
    datei_setzen "$ETC/haertung.conf" 644 < "$HIER/haertung/haertung.conf"
  fi
}

# Was jetzt erreichbar ist (ein Wort je Punkt) – vor und nach einer Firewall-Änderung verglichen
erreichbar() {
  local port
  if hat tailscale && x tailscale status --json 2>/dev/null | enthaelt '"BackendState": *"Running"'; then echo Tailscale; fi
  if hat curl; then
    port="$(sed -n 's/^JU_FINANZEN_PORT=//p' "$W$ETC/ju-finanzen.env" 2>/dev/null | tail -n 1 || true)"
    if x curl -fsS -m 5 -o /dev/null "http://127.0.0.1:${port:-8769}/api/status" 2>/dev/null; then echo Programm; fi
    if x curl -fsS -m 5 -o /dev/null http://127.0.0.1:9000/auth/health/ready 2>/dev/null; then echo Keycloak; fi
    if x curl -fsS -m 10 -o /dev/null http://archive.ubuntu.com/ubuntu/dists/noble/Release.gpg 2>/dev/null; then echo Internet-ausgehend; fi
  fi
}
nachpruefung() {   # $1 = Erreichbarkeit vorher; alles, was vorher ging, muss weiter gehen – sonst scheitert die Maßnahme
  local nachher punkt
  sleep "$PAUSE"
  nachher=" $(erreichbar | tr '\n' ' ')"
  for punkt in $1; do
    case "$nachher" in *" $punkt "*) ;; *) die "Nach der Firewall-Änderung nicht mehr erreichbar: $punkt" ;; esac
  done
  aus "    Geprüft:  weiter erreichbar: ${1:-(vorher war nichts davon erreichbar)}"
}

ufw_tun() {   # GEGENBEFEHL BEFEHL… – ufw ändern und merken, womit --zurueck genau diese eine Änderung zurücknimmt
  local gegen="$1"; shift
  printf 'ufw\t%s\n' "$gegen" >> "$MANIFEST"
  x ufw "$@" >/dev/null
}
tabelle_laden() {
  dienst_merken ju-serverschutz.service
  x systemctl daemon-reload
  x systemctl enable -q ju-serverschutz.service
  x systemctl restart ju-serverschutz.service   # ExecStart: nft -f – ersetzt nur die eigene Tabelle, dann die Sperrliste
  x nft list table inet ju_schutz >/dev/null || die "Die Tabelle inet ju_schutz ist nach dem Laden nicht vorhanden."
}
tabelle_entfernen() {
  dienst_merken ju-serverschutz.service
  x systemctl disable --now -q ju-serverschutz.service 2>/dev/null || true
  if hat nft && x nft list table inet ju_schutz >/dev/null 2>&1; then x nft delete table inet ju_schutz; fi
}

m_firewall() {
  local vorher="" p tmp ufw_vorher
  RISKANT=1   # jede Änderung hier plant vorher die Rücknahme und gilt erst nach „haerten --bestaetigen“ als behalten
  if ! an FIREWALL ja; then
    stand "abgeschaltet (FIREWALL=nein) – ufw bleibt, wie es ist"
    if [ -e "$W$UNITS/ju-serverschutz.service" ] || [ -e "$W$NFT_DATEI" ]; then
      tu "Schutztabelle inet ju_schutz entfernen (nur die eigene Tabelle)" tabelle_entfernen
      datei_entfernen "$UNITS/ju-serverschutz.service"; datei_entfernen "$NFT_DATEI"; units_neu
    fi
    return 0
  fi
  if [ "$PRUEFEN" -eq 0 ]; then vorher="$(erreichbar | tr '\n' ' ')"; fi
  ufw_vorher="$(wc -l < "$ZAEHLER")"

  # --- ufw: Grundlage. Vorhandene Regeln bleiben; ergänzt wird nur, was wirklich fehlt – jede Ergänzung mit ihrem Gegenbefehl.
  paket ufw
  stand "ufw $(if [ "$UFW_AKTIV" -eq 1 ]; then echo aktiv; else echo AUS; fi), eingehend $UFW_EINGANG; SSH (Port ${SSH_PORTS% }) ist $(ssh_lage_text)"
  if [ "$SSH_LAGE" = offen ]; then hinweis "SSH ist öffentlich offen – bleibt wie vorgefunden (fail2ban und SSH-Takt schützen; enger: --ssh-nur-tailscale)"; fi
  if ! grep -qs '^IPV6=yes' "$W/etc/default/ufw"; then hinweis "ufw filtert IPv6 nicht (IPV6=yes fehlt in /etc/default/ufw) – bitte prüfen"; fi
  if { [ "$UFW_AKTIV" -eq 0 ] || [ "$UFW_EINGANG" != gesperrt ]; } && [ "$SSH_FREI" -eq 0 ]; then
    for p in $SSH_PORTS; do   # SSH war bisher ohne wirksame Firewall von überall erreichbar: vor dem Sperren ausdrücklich offen lassen
      tu "ufw: SSH-Port $p offen lassen wie vorgefunden (damit niemand ausgesperrt wird)" ufw_tun "delete allow $p/tcp" allow "$p/tcp"
    done
  fi
  if ! printf '%s\n' "$UFW_REGELN" | enthaelt '^ufw allow in on tailscale0( comment .*)?$'; then
    tu "ufw: alles auf tailscale0 erlauben (Zugriff aus dem Tailnet)" ufw_tun "delete allow in on tailscale0" allow in on tailscale0
  fi
  # tailscaled öffnet seinen Port 41641/udp selbst (Kette ts-input) – in ufw nur nötig bei „tailscale up --netfilter-mode=off|nodivert“
  if an TAILSCALE_PORT nein && ! printf '%s\n' "$UFW_REGELN" | enthaelt '^ufw allow 41641/udp( |$)'; then
    tu "ufw: Tailscale-UDP 41641 erlauben (TAILSCALE_PORT=ja)" ufw_tun "delete allow 41641/udp" allow 41641/udp
  fi
  if [ "$UFW_EINGANG" != gesperrt ]; then tu "ufw: eingehend grundsätzlich sperren (default deny incoming)" ufw_tun "default allow incoming" default deny incoming; fi
  if [ "$UFW_AKTIV" -eq 0 ]; then tu "ufw einschalten" ufw_tun "--force disable" --force enable; fi
  if [ "$(wc -l < "$ZAEHLER")" = "$ufw_vorher" ]; then info "ufw: nichts zu ergänzen – an ufw wird nichts geändert"; fi

  # --- eigene Tabelle neben ufw
  paket nftables
  if [ "$(sys_wert is-enabled nftables.service)" = enabled ]; then
    hinweis "nftables.service ist eingeschaltet: /etc/nftables.conf beginnt üblicherweise mit „flush ruleset“ und löscht beim Start die Regeln von ufw – bitte prüfen (nur Meldung)"
  fi
  if [ "$NUR_TAILSCALE" -eq 1 ]; then
    hinweis "SSH nur aus dem Tailnet: neue SSH-Verbindungen von außen verwirft die Schutztabelle (Tailscale und die Konsole des Anbieters bleiben)"
  fi
  tmp="$(mktemp "$TMP/regelwerk.XXXXXX")"
  # der SSH-Zustand der Tabelle kommt aus diesem Lauf (Schalter mit Nachweis), nicht aus einer von Hand geänderten Datei
  py regelwerk "--ssh-nur-tailscale=$(if [ "$NUR_TAILSCALE" -eq 1 ]; then echo ja; else echo nein; fi)" > "$tmp" || die "Das Regelwerk ließ sich nicht erzeugen."
  if hat nft; then
    x nft -c -f "$tmp" || die "nft lehnt das Regelwerk ab (nft -c) – nichts geladen."
  fi
  datei_setzen "$NFT_DATEI" 600 < "$tmp"
  unit_setzen ju-serverschutz.service
  if [ "$GEAENDERT" -eq 1 ] || [ "$(sys_wert is-active ju-serverschutz.service)" != active ] \
      || [ "$(sys_wert is-enabled ju-serverschutz.service)" != enabled ] || ! x nft list table inet ju_schutz >/dev/null 2>&1; then
    tu "Schutztabelle inet ju_schutz laden (Sperrliste, ungültige Pakete, SSH-Takt $(wert SSH_NEU_JE_MINUTE 12)/Minute je Quelle) und beim Start laden lassen" tabelle_laden
  fi
  if [ "$PRUEFEN" -eq 0 ] && [ "$GETAN" -eq 1 ]; then nachpruefung "$vorher"; fi
  # erst nach bestandener Nachprüfung festhalten: scheitert die Maßnahme, bleibt der Schalter, wie er war
  if [ "$NUR_TAILSCALE" -eq 1 ] && [ "$(wert SSH_NUR_TAILSCALE nein)" != ja ]; then conf_setzen SSH_NUR_TAILSCALE ja; fi
  risiko "gering. Die Tabelle kann nur zusätzlich verwerfen (Sperrliste, kaputte Pakete, zu viele neue SSH-Verbindungen); lo, tailscale0, bestehende Verbindungen, 169.254.0.0/16 und 100.64.0.0/10 sind ausgenommen. Notaus: sudo systemctl stop ju-serverschutz"
}

fehler_merken() { mkdir -p "$W$LIB"; printf '%s %s\n' "$(date -Is)" "$1" > "$W$LIB/sperrliste.fehler"; aus "    Sperrliste: $1 – die letzte gute Liste bleibt."; }
# Listen laden und prüfen; nur zwei gute Listen ersetzen die bisherigen. Rückgabe 1 = letzte gute Liste bleibt.
sperrliste_holen() {
  local tmp f
  tmp="$(mktemp -d "$TMP/liste.XXXXXX")"
  if ! hat curl; then fehler_merken "curl fehlt"; return 1; fi
  for f in drop dropv6; do
    if ! x curl -fsS --proto '=https' --proto-redir '=https' --max-time 60 --max-filesize 2000000 \
        -o "$tmp/$f.txt" "https://www.spamhaus.org/drop/$f.txt"; then
      fehler_merken "Abruf von $f.txt gescheitert"; return 1
    fi
  done
  py liste "$tmp/drop.txt" "$tmp/dropv6.txt" || return 1
  mengen_laden
}
# Nur die Mengen drop4/drop6 austauschen (flush + add in einem nft-Schritt, vorher nft -c) – nie die Regeln: Der Zeitgeber
# kann so den SSH-Zustand der Tabelle nicht ändern; das tut allein ein haerten-Lauf mit Nachweis.
# Ist die Tabelle nicht geladen (Notaus), bleibt sie aus; die Datei gilt dann ab dem nächsten Start des Dienstes.
mengen_laden() {
  local tmp
  tmp="$(mktemp "$TMP/mengen.XXXXXX")"
  if ! py mengen > "$tmp"; then aus "    Sperrliste: Die Mengen ließen sich nicht erzeugen – die bisherigen bleiben."; return 1; fi
  if hat nft && x nft list table inet ju_schutz >/dev/null 2>&1; then
    if ! x nft -c -f "$tmp" || ! x nft -f "$tmp"; then aus "    Sperrliste: nft lehnt die neuen Mengen ab – die bisherigen bleiben geladen."; return 1; fi
  fi
  mkdir -p "$W$LIB"
  install -m 600 "$tmp" "$W$MENGEN_DATEI.neu" && mv -f "$W$MENGEN_DATEI.neu" "$W$MENGEN_DATEI"
}

m_sperrliste() {
  local n=0 alt=1
  if ! an SPERRLISTE ja; then
    stand "abgeschaltet (SPERRLISTE=nein)"
    if grep -qs '^add element' "$W$MENGEN_DATEI"; then tu "Sperrliste aus der Schutztabelle nehmen" mengen_laden; fi
    dienst_aus ju-serverschutz-liste.timer
    datei_entfernen "$UNITS/ju-serverschutz-liste.timer"; datei_entfernen "$UNITS/ju-serverschutz-liste.service"; units_neu
    return 0
  fi
  if [ -f "$W$LIB/drop4.txt" ]; then
    n="$(cat "$W$LIB/drop4.txt" "$W$LIB/drop6.txt" 2>/dev/null | wc -l)"
    if [ -n "$(find "$W$LIB/drop4.txt" -mmin -1440 2>/dev/null)" ]; then alt=0; fi
    stand "letzte gute Liste: $n Netze vom $(date -r "$W$LIB/drop4.txt" '+%d.%m.%Y %H:%M')"
  else
    stand "noch keine Liste geladen"
  fi
  unit_setzen ju-serverschutz-liste.service; unit_setzen ju-serverschutz-liste.timer; units_neu
  dienst_an ju-serverschutz-liste.timer
  if [ "$alt" -eq 1 ]; then
    tu "Sperrliste jetzt laden und prüfen (https://www.spamhaus.org/drop/, danach täglich)" sperrliste_erstmals
  fi
  risiko "gering. Gesperrt werden nur Netze der Spamhaus-DROP-Liste (gekaperte bzw. von Kriminellen betriebene Netze). Eine kaputte, zu kleine oder zu große Liste und jede Liste mit privaten, Tailscale- oder eigenen Adressen wird nicht übernommen."
}
sperrliste_erstmals() {
  if ! sperrliste_holen; then aus "    Hinweis:  Die Sperrliste ließ sich jetzt nicht laden – der Zeitgeber versucht es täglich erneut."; fi
}

m_fail2ban() {
  local ignorieren
  if ! an FAIL2BAN ja; then
    stand "abgeschaltet (FAIL2BAN=nein)"
    dienst_aus fail2ban.service
    datei_entfernen /etc/fail2ban/jail.d/ju-haertung.local
    return 0
  fi
  if paket_da fail2ban; then stand "fail2ban installiert, Dienst $(sys_wert is-active fail2ban.service)"; else stand "fail2ban ist nicht installiert"; fi
  paket fail2ban
  paket python3-systemd   # für backend = systemd (liest das Journal)
  ignorieren="127.0.0.0/8 ::1 169.254.0.0/16 100.64.0.0/10 fd7a:115c:a1e0::/48 $(wert NIE_SPERREN "" | tr -s ', ' ' ')"
  datei_setzen /etc/fail2ban/jail.d/ju-haertung.local 644 <<EOF
# JU Hamburg Finanzen – geschrieben von server/haerten.sh (wird bei jedem Lauf überschrieben)
[DEFAULT]
# nie sperren: Loopback, Konsole des Anbieters (169.254.0.0/16), Tailscale, NIE_SPERREN aus haertung.conf
ignoreip = ${ignorieren% }
backend = systemd
banaction = nftables-multiport
banaction_allports = nftables-allports

[sshd]
enabled = true
port = $(printf '%s' "${SSH_PORTS% }" | tr ' ' ',')
journalmatch = _SYSTEMD_UNIT=ssh.service + _SYSTEMD_UNIT=sshd.service + _COMM=sshd
maxretry = 5
findtime = 10m
bantime = 1h
bantime.increment = true
bantime.maxtime = 1w

# Wiederholungstäter: wer binnen eines Tages dreimal gesperrt wurde, bleibt eine Woche auf allen Ports draußen
[recidive]
enabled = true
backend = auto
logpath = /var/log/fail2ban.log
maxretry = 3
findtime = 1d
bantime = 1w
EOF
  if [ "$GEAENDERT" -eq 1 ] || [ "$(sys_wert is-active fail2ban.service)" != active ] || [ "$(sys_wert is-enabled fail2ban.service)" != enabled ]; then
    tu "fail2ban einschalten und mit den neuen Regeln starten (Einstellungen vorher geprüft)" fail2ban_starten
  fi
  risiko "gering. Wer sich fünfmal in zehn Minuten falsch anmeldet, wird für eine Stunde gesperrt (wachsend bis eine Woche). Entsperren: sudo fail2ban-client unban <Adresse>"
}
fail2ban_starten() {
  dienst_merken fail2ban.service
  # der Abschnitt recidive liest das Protokoll von fail2ban – ohne die Datei startet fail2ban nicht
  if [ ! -e "$W/var/log/fail2ban.log" ]; then install -D -m 640 /dev/null "$W/var/log/fail2ban.log"; fi
  x fail2ban-client -t >/dev/null || die "fail2ban lehnt die Einstellungen ab (fail2ban-client -t)."
  x systemctl enable -q fail2ban.service
  x systemctl restart fail2ban.service
  for _ in $(seq 1 20); do
    if x fail2ban-client ping >/dev/null 2>&1; then return 0; fi
    sleep "$((PAUSE > 0))"
  done
  die "fail2ban startet nicht (sudo journalctl -u fail2ban -n 30)."
}

# Name | Soll | Begründung | mind = ein strengerer vorhandener Wert bleibt
KERN='net.ipv4.tcp_syncookies|1|SYN-Flut abfangen
net.ipv4.tcp_rfc1337|1|Angriffe auf Verbindungen im Zustand TIME-WAIT
net.ipv4.conf.all.rp_filter|2|gefälschte Absender verwerfen – locker (2), weil strikt (1) Tailscale stört
net.ipv4.conf.default.rp_filter|2|dasselbe für neue Schnittstellen
net.ipv4.conf.all.accept_redirects|0|keine fremde Umleitung annehmen
net.ipv4.conf.default.accept_redirects|0|keine fremde Umleitung annehmen
net.ipv4.conf.all.secure_redirects|0|auch nicht vom Gateway
net.ipv4.conf.default.secure_redirects|0|auch nicht vom Gateway
net.ipv4.conf.all.send_redirects|0|der Server ist kein Router
net.ipv4.conf.default.send_redirects|0|der Server ist kein Router
net.ipv4.conf.all.accept_source_route|0|kein Source-Routing
net.ipv4.conf.default.accept_source_route|0|kein Source-Routing
net.ipv4.conf.all.log_martians|1|unmögliche Absender protokollieren
net.ipv4.conf.default.log_martians|1|unmögliche Absender protokollieren
net.ipv4.icmp_echo_ignore_broadcasts|1|keine Antwort auf Broadcast-Ping
net.ipv4.icmp_ignore_bogus_error_responses|1|falsche ICMP-Fehler nicht protokollieren
net.ipv6.conf.all.accept_redirects|0|keine fremde Umleitung annehmen
net.ipv6.conf.default.accept_redirects|0|keine fremde Umleitung annehmen
net.ipv6.conf.all.accept_source_route|0|kein Source-Routing
net.ipv6.conf.default.accept_source_route|0|kein Source-Routing
kernel.kptr_restrict|2|Adressen des Kerns verbergen|mind
kernel.dmesg_restrict|1|Kernprotokoll nur für root
kernel.yama.ptrace_scope|1|fremde Prozesse nicht ausspähen (1 genügt, mehr stört die Diagnose von Java)|mind
kernel.unprivileged_bpf_disabled|2|BPF nur für root (Tailscale läuft als root)
net.core.bpf_jit_harden|2|BPF-Übersetzer härten|mind
kernel.randomize_va_space|2|zufällige Speicheradressen
fs.protected_hardlinks|1|Verweis-Tricks in /tmp
fs.protected_symlinks|1|Verweis-Tricks in /tmp
fs.protected_fifos|2|untergeschobene Dateien in /tmp|mind
fs.protected_regular|2|untergeschobene Dateien in /tmp|mind
fs.suid_dumpable|0|keine Speicherabzüge von Programmen mit erhöhten Rechten
dev.tty.ldisc_autoload|0|keine Terminal-Module auf Zuruf laden'

kern_ist() { cat "$W/proc/sys/${1//.//}" 2>/dev/null | tr '\t' ' ' || true; }
# Erste fremde Datei, die die Weiterleitung ausdrücklich einschaltet (z. B. 99-tailscale.conf für Subnetz-Routen) – leer: keine
forward_fremd() {
  { grep -Els '^[[:space:]]*net[./]ipv4[./]ip_forward[[:space:]]*=[[:space:]]*1' "$W/etc/sysctl.conf" "$W"/etc/sysctl.d/*.conf \
      "$W"/usr/lib/sysctl.d/*.conf "$W/etc/ufw/sysctl.conf" 2>/dev/null || true; } | grep -vxF "$W$KERN_DATEI" | head -n 1 || true
}
m_kern() {
  local name soll grund mind ist alt datei abweichend ufwneu ufwalt fremd n=0 fehlt=0 weiter=0
  if ! an KERN ja; then
    stand "abgeschaltet (KERN=nein)"
    if [ -e "$W$KERN_DATEI" ]; then datei_entfernen "$KERN_DATEI"; hinweis "die laufenden Werte gelten bis zum nächsten Neustart weiter"; fi
    return 0
  fi
  datei="$(mktemp "$TMP/kern.XXXXXX")"; abweichend="$(mktemp "$TMP/kern.XXXXXX")"; ufwneu="$(mktemp "$TMP/kern.XXXXXX")"; ufwalt="$(mktemp "$TMP/kern.XXXXXX")"
  fremd="$(forward_fremd)"; fremd="${fremd#"$W"}"
  if [ -f "$W/etc/ufw/sysctl.conf" ]; then cat "$W/etc/ufw/sysctl.conf" > "$ufwneu"; fi
  {
    echo "# JU Hamburg Finanzen – Kern-Einstellungen, geschrieben von server/haerten.sh (wird bei jedem Lauf überschrieben)."
    echo "# Bewusst nicht gesetzt: net.ipv6.conf.*.accept_ra und net.ipv6.conf.*.forwarding (Netz des Anbieters, Tailscale),"
    echo "# kernel.unprivileged_userns_clone, kernel.kexec_load_disabled, kernel.sysrq."
    # Weiterleitung: nur aus, wenn weder Docker-Container noch von Tailscale angebotene Routen sie brauchen und keine
    # andere Datei sie einschaltet. Steht als Erstes: der Kern setzt beim Umschalten accept_redirects auf seine Vorgabe
    # zurück – die Redirect-Werte müssen deshalb danach gesetzt werden (kern_anwenden wendet die Datei der Reihe nach an).
    if [ "$(kern_ist net.ipv4.ip_forward)" = 1 ] || grep -qs '^net.ipv4.ip_forward = 0' "$W$KERN_DATEI"; then
      if [ -z "$fremd" ] && [ "$(docker_container)" -eq 0 ] && [ "$(sys_wert is-active docker.service)" != active ] && hat tailscale \
          && x tailscale debug prefs 2>/dev/null | enthaelt '"AdvertiseRoutes": *(null|\[ *\])'; then
        echo "# keine Weiterleitung zwischen Netzen (weder Docker noch Tailscale-Routen brauchen sie)"
        echo "net.ipv4.ip_forward = 0"
        if [ "$(kern_ist net.ipv4.ip_forward)" != 0 ]; then printf 'net.ipv4.ip_forward|0|1\n' >> "$abweichend"; fi
      else
        weiter=1
      fi
    fi
    while IFS='|' read -r name soll grund mind; do
      ist="$(kern_ist "$name")"
      if [ -z "$ist" ]; then fehlt=$((fehlt + 1)); echo "# $name gibt es in diesem Kern nicht"; continue; fi
      if [ "$mind" = mind ] && [[ "$ist" =~ ^[0-9]+$ ]] && [ "$ist" -gt "$soll" ]; then soll="$ist"; fi
      if [ "$name" = kernel.unprivileged_bpf_disabled ] && [ "$ist" = 1 ]; then soll=1; fi   # 1 = endgültig aus, lässt sich nicht mehr ändern
      echo "# $grund"
      echo "$name = $soll"
      if [ "$ist" != "$soll" ]; then printf '%s|%s|%s\n' "$name" "$soll" "$ist" >> "$abweichend"; fi
      # ufw setzt beim Start eigene Werte aus /etc/ufw/sysctl.conf – dort denselben Wert eintragen, sonst gewinnt ufw
      alt="$(sed -n "s|^${name//.//}=||p" "$ufwneu" | tail -n 1)"
      if [ -n "$alt" ] && [ "$alt" != "$soll" ]; then
        printf '%s\t%s\n' "${name//.//}" "$alt" >> "$ufwalt"
        sed -i "s|^${name//.//}=.*|${name//.//}=$soll|" "$ufwneu"
      fi
    done <<< "$KERN"
  } > "$datei"
  n="$(wc -l < "$abweichend")"
  stand "$n Einstellungen weichen ab$(if [ "$n" -gt 0 ]; then echo ":"; fi)"
  while IFS='|' read -r name soll ist; do aus "              $name (ist $ist, soll $soll)"; done < "$abweichend"
  if [ "$weiter" -eq 1 ] && [ -n "$fremd" ]; then hinweis "net.ipv4.ip_forward=1 bleibt: $fremd schaltet die Weiterleitung ausdrücklich ein (nur Meldung; ufw sperrt weitergeleitete Pakete ohnehin)"
  elif [ "$weiter" -eq 1 ]; then hinweis "net.ipv4.ip_forward=1 bleibt: Docker-Container oder Tailscale-Routen könnten die Weiterleitung brauchen bzw. es ließ sich nicht prüfen (nur Meldung; ufw sperrt weitergeleitete Pakete ohnehin)"; fi
  if [ "$(kern_ist net.ipv6.conf.all.forwarding)" = 1 ]; then hinweis "net.ipv6.conf.all.forwarding=1 (nur Meldung, wird nicht geändert)"; fi
  datei_setzen "$KERN_DATEI" 644 < "$datei"
  if [ -s "$ufwalt" ]; then tu "/etc/ufw/sysctl.conf: $(wc -l < "$ufwalt") Zeilen auf dieselben Werte stellen (sonst setzt ufw sie beim Start zurück)" ufw_sysctl_setzen "$ufwneu" "$ufwalt"; fi
  if [ "$n" -gt 0 ]; then tu "$n Kern-Einstellungen sofort anwenden (ohne Neustart)" kern_anwenden "$abweichend"; fi
  risiko "gering. Alle Werte sind mit Tailscale, PostgreSQL und Java verträglich; rp_filter bleibt locker (2). Zurück: --zurueck stellt die vorherigen Werte wieder ein."
}
kern_anwenden() {   # $1 = Abweichungen: Name|Soll|Ist
  # rückwärts gemerkt: --zurueck liest das MANIFEST rückwärts und stellt so ip_forward vor den Redirect-Werten zurück
  tac "$1" | awk -F'|' '{ printf "sysctl\t%s\t%s\n", $1, $3 }' >> "$MANIFEST"
  # die ganze eigene Datei der Reihe nach – einzelne Werte reichten nicht: ip_forward=0 schaltet accept_redirects wieder ein
  x sysctl -q -p "$W$KERN_DATEI"
}
ufw_sysctl_setzen() {   # $1 = neuer Inhalt, $2 = geänderte Zeilen: Name<Tab>alter Wert – --zurueck stellt nur diese Zeilen zurück
  awk -F'\t' '{ printf "ufwsysctl\t%s\t%s\n", $1, $2 }' "$2" >> "$MANIFEST"
  install -m "$(stat -c %a "$W/etc/ufw/sysctl.conf")" "$1" "$W/etc/ufw/sysctl.conf.ju-neu"
  mv -f "$W/etc/ufw/sysctl.conf.ju-neu" "$W/etc/ufw/sysctl.conf"
}

m_updates() {
  local neustart=false
  if ! an UPDATES ja; then
    stand "abgeschaltet (UPDATES=nein) – die Einstellungen von Ubuntu gelten unverändert"
    datei_entfernen /etc/apt/apt.conf.d/52ju-haertung
    return 0
  fi
  if an UPDATES_NEUSTART nein; then neustart=true; fi
  stand "unattended-upgrades $(sys_wert is-active unattended-upgrades.service), Zeitgeber $(sys_wert is-active apt-daily-upgrade.timer); Neustart nötig: $(if [ -e "$W/run/reboot-required" ]; then echo ja; else echo nein; fi)"
  paket unattended-upgrades
  datei_setzen /etc/apt/apt.conf.d/52ju-haertung 644 <<EOF
// JU Hamburg Finanzen – geschrieben von server/haerten.sh (wird bei jedem Lauf überschrieben)
// Paketlisten täglich holen und Sicherheitsupdates täglich einspielen
APT::Periodic::Enable "1";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
// Neustart nach Kernel-Updates: UPDATES_NEUSTART in /etc/ju-finanzen/haertung.conf (Vorgabe nein – nur melden)
Unattended-Upgrade::Automatic-Reboot "$neustart";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
EOF
  dienst_an apt-daily.timer
  dienst_an apt-daily-upgrade.timer
  if [ -e "$W/run/reboot-required" ]; then hinweis "Ein Neustart steht aus (Kernel-Update): sudo reboot – zu einer passenden Zeit, nicht automatisch"; fi
  if hat tailscale; then hinweis "Tailscale kommt aus einer eigenen Paketquelle und wird von unattended-upgrades nicht aktualisiert: sudo tailscale set --auto-update (nur Meldung)"; fi
  risiko "gering. Ubuntu spielt nur Sicherheitsupdates ein; PostgreSQL und Java starten dabei kurz neu. $(if [ "$neustart" = true ]; then echo "Der Server startet nach Kernel-Updates um 04:30 von selbst neu."; else echo "Kein automatischer Neustart – der Statusbericht meldet ihn."; fi)"
}

docker_container() { { find "$W/var/lib/docker/containers" -mindepth 1 -maxdepth 1 -type d 2>/dev/null || true; } | wc -l; }
m_dienste() {
  local n u gefunden=0
  n="$(docker_container)"
  for u in docker.service docker.socket containerd.service; do
    case "$(sys_wert is-enabled "$u")" in unbekannt|not-found) ;; *) gefunden=1 ;; esac
  done
  if [ "$gefunden" -eq 0 ]; then stand "Docker ist nicht installiert"
  elif ! an DOCKER_AUS ja; then stand "abgeschaltet (DOCKER_AUS=nein) – Docker bleibt, wie es ist"
  elif [ "$n" -gt 0 ]; then
    stand "Docker: $n Container vorhanden – bleibt an (nur Meldung)"
    hinweis "Docker öffnet veröffentlichte Ports an ufw vorbei. Werden die Container nicht gebraucht: entfernen und erneut aufrufen."
  else
    stand "Docker: kein Container; docker $(sys_wert is-active docker.service)/$(sys_wert is-enabled docker.service), containerd $(sys_wert is-active containerd.service)/$(sys_wert is-enabled containerd.service)"
    for u in docker.socket docker.service containerd.service; do dienst_aus "$u"; done
  fi
  for u in snapd.service ModemManager.service multipathd.service udisks2.service cups.service avahi-daemon.service rpcbind.service apache2.service nginx.service; do
    if [ "$(sys_wert is-active "$u")" = active ]; then hinweis "$u läuft – wird für dieses Programm nicht gebraucht (nur Meldung, nichts geändert)"; fi
  done
  info "Dienste des Anbieters (monarx-agent, qemu-guest-agent) bleiben unberührt."
  risiko "gering. Abgeschaltet wird nur Docker ohne Container; nichts wird deinstalliert."
}

ssh_neu_laden() {   # laufende Sitzungen bleiben; bei Socket-Aktivierung (ssh.socket) liest die nächste Verbindung neu
  if [ "$(sys_wert is-active ssh.service)" = active ]; then x systemctl reload ssh.service; fi
}
ssh_anwenden() {
  sshd_x -t || die "sshd -t meldet einen Fehler in den SSH-Einstellungen – nichts neu geladen."
  if [ "$NUR_SCHLUESSEL" -eq 1 ] && [ "$(eff passwordauthentication)" != no ]; then
    die "Die Passwort-Anmeldung bliebe trotz der neuen Datei an (eine andere Einstellung hat Vorrang) – abgebrochen."
  fi
  ssh_neu_laden
}
m_ssh() {
  local konten
  if ! hat sshd; then stand "kein SSH-Dienst gefunden – nichts zu tun"; return 0; fi
  if [ ! -d "$W/run/sshd" ]; then hinweis "/run/sshd fehlt (der SSH-Dienst lief seit dem Start des Servers noch nicht): Der Prüflauf kann die geltenden SSH-Werte deshalb nicht lesen und legt nichts an; „haerten“ legt den Ordner an."; fi
  # Konten mit Anmelde-Shell und gesetztem Passwort (gesperrte Konten wie postgres zählen nicht)
  konten="$(awk -F: 'NR == FNR { if ($2 ~ /^\$/) pw[$1] = 1; next }
    ($1 in pw) && $7 !~ /(nologin|false|sync|shutdown|halt)$/ { printf "%s%s", (n++ ? ", " : ""), $1 }' "$W/etc/shadow" "$W/etc/passwd" 2>/dev/null || true)"
  stand "MaxAuthTries $(eff maxauthtries), LoginGraceTime $(eff logingracetime), X11Forwarding $(eff x11forwarding), AllowTcpForwarding $(eff allowtcpforwarding), PermitRootLogin $(eff permitrootlogin), PasswordAuthentication $(eff passwordauthentication)"
  if [ "$(eff passwordauthentication)" = yes ]; then
    aus "    Befund:   Passwort-Anmeldung für ${konten:-(kein Konto mit Passwort gefunden)} offen, $(ssh_lage_text)"
  else
    aus "    Befund:   Anmeldung nur mit Schlüssel; SSH ist $(ssh_lage_text)"
  fi
  if ! an SSH ja; then
    stand "abgeschaltet (SSH=nein)"
    if [ -e "$W$SSH_DATEI" ]; then datei_entfernen "$SSH_DATEI"; tu "SSH-Einstellungen prüfen (sshd -t) und neu laden" ssh_anwenden; fi
    return 0
  fi
  # die Passwort-Anmeldung abzuschalten kann aussperren: vorher die Rücknahme planen, behalten erst nach „haerten --bestaetigen“
  if [ "$NUR_SCHLUESSEL" -eq 1 ] && ! grep -qs '^PasswordAuthentication no' "$W$SSH_DATEI"; then RISKANT=1; fi
  if ! grep -Eiqs '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*\.conf' "$W/etc/ssh/sshd_config"; then
    hinweis "/etc/ssh/sshd_config bindet /etc/ssh/sshd_config.d/*.conf nicht ein – SSH bleibt unverändert (nur Meldung)"
    return 0
  fi
  datei_setzen "$SSH_DATEI" 644 <<EOF
# JU Hamburg Finanzen – SSH-Grundhärtung, geschrieben von server/haerten.sh (wird bei jedem Lauf überschrieben).
# Die Datei steht absichtlich vorn (00-): bei sshd gilt der zuerst gelesene Wert.
# Wer sich wie anmelden darf (PermitRootLogin, Passwort) ändert nur „haerten --ssh-nur-schluessel“.
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
# nur lokale Weiterleitung: der Tunnel zur Keycloak-Konsole (ssh -L 8180:127.0.0.1:8180) geht weiter
AllowTcpForwarding local
PermitEmptyPasswords no
# hängende Verbindungen nach zehn Minuten ohne Antwort beenden
ClientAliveInterval 300
ClientAliveCountMax 2
$(if [ "$NUR_SCHLUESSEL" -eq 1 ]; then printf '# Anmeldung nur mit Schlüssel (haerten --ssh-nur-schluessel)\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n'; fi)
EOF
  if [ "$NUR_SCHLUESSEL" -eq 1 ]; then
    hinweis "WARNUNG: Ohne Passwort-Anmeldung funktioniert das Browser-Terminal des Anbieters möglicherweise nicht mehr. Rückwege: Tailscale-SSH, der Schlüssel, der Rettungsmodus des Anbieters. Diese Sitzung offen lassen und in einem zweiten Fenster die Anmeldung mit Schlüssel testen."
  fi
  if [ "$GEAENDERT" -eq 1 ]; then tu "SSH-Einstellungen prüfen (sshd -t) und neu laden – laufende Sitzungen bleiben" ssh_anwenden; fi
  # erst nach dem geprüften Neuladen festhalten: scheitert die Maßnahme, bleibt der Schalter, wie er war
  if [ "$NUR_SCHLUESSEL" -eq 1 ] && [ "$(wert SSH_NUR_SCHLUESSEL nein)" != ja ]; then conf_setzen SSH_NUR_SCHLUESSEL ja; fi
  if [ "$PRUEFEN" -eq 0 ] && [ "$(eff maxauthtries)" != 3 ]; then hinweis "MaxAuthTries wirkt nicht – eine andere Einstellung hat Vorrang (sshd -T zeigt die geltenden Werte)"; fi
  risiko "gering. Wer anmelden darf, bleibt unverändert. MaxAuthTries 3: Wer sich von außen mit dem Passwort anmeldet und Schlüssel im SSH-Agenten hat, nimmt „ssh -o PubkeyAuthentication=no …“ – sonst verbrauchen die Schlüssel die drei Versuche, und solche Fehlverbindungen zählen bei fail2ban mit. Rückweg bei jedem SSH-Problem: Konsole des Anbieters, dann sudo ju-finanzen haerten --zurueck"
}

core_setzen() {
  printf 'sysctl\tkernel.core_pattern\t%s\n' "$(kern_ist kernel.core_pattern)" >> "$MANIFEST"
  x sysctl -q -w 'kernel.core_pattern=|/bin/false'
}
m_dateien() {
  local f opt
  if ! an DATEIEN ja; then
    stand "abgeschaltet (DATEIEN=nein)"
    for f in /etc/security/limits.d/60-ju-haertung.conf /etc/sysctl.d/80-ju-haertung-core.conf /etc/systemd/journald.conf.d/60-ju-haertung.conf; do datei_entfernen "$f"; done
    return 0
  fi
  stand "Rechte von $ETC, der Geheimnisdateien und der Sicherungsordner; Speicherabzüge; Protokoll"
  rechte_soll "$ETC" 750 root:jufinanzen
  rechte_soll "$ETC/ju-finanzen.env" 640 root:jufinanzen
  rechte_soll "$ETC/keycloak-admin.env" 600 root:root
  rechte_soll /var/backups/ju-keycloak 700 root:root
  for f in "$W"/var/backups/ju-keycloak/keycloak-*.dump; do if [ -f "$f" ]; then rechte_soll "${f#"$W"}" 600 root:root; fi; done
  rechte_soll /var/backups/ju-haertung 700 root:root
  rechte_soll /tmp 1777 root:root
  rechte_soll /var/tmp 1777 root:root
  # Datenordner des Dienstes: nur ansehen – dort ändert root nichts (der Dienstbenutzer könnte Einträge durch Verweise ersetzen)
  for f in /var/lib/ju-finanzen /var/lib/ju-finanzen/geheim /var/lib/ju-finanzen/Daten /var/lib/ju-finanzen/Sicherungen; do
    if [ -d "$W$f" ] && [ "$(stat -c %a "$W$f")" != 700 ]; then hinweis "$f hat die Rechte $(stat -c %a "$W$f") statt 700 (nur Meldung; systemd stellt sie beim Start des Dienstes richtig)"; fi
  done
  if hat findmnt; then
    opt="$(x findmnt -no OPTIONS /tmp 2>/dev/null || true)"
    if [ -z "$opt" ]; then hinweis "/tmp ist kein eigenes Dateisystem (nur Meldung: ein Umstellen braucht einen Neustart und kann apt und Java stören; die Dienste haben ein eigenes /tmp)"
    elif ! printf '%s' "$opt" | enthaelt nosuid || ! printf '%s' "$opt" | enthaelt nodev; then hinweis "/tmp ist ohne nosuid,nodev eingehängt (nur Meldung)"; fi
  fi
  # Speicherabzüge (Core-Dumps) können Mitglieder- und Bankdaten enthalten
  dienst_aus apport.service
  datei_setzen /etc/security/limits.d/60-ju-haertung.conf 644 <<'EOF'
# JU Hamburg Finanzen – keine Speicherabzüge (Core-Dumps): sie könnten Mitglieder- und Bankdaten enthalten
*    hard core 0
root hard core 0
EOF
  datei_setzen /etc/sysctl.d/80-ju-haertung-core.conf 644 <<'EOF'
# JU Hamburg Finanzen – Speicherabzüge (Core-Dumps) verwerfen statt speichern
kernel.core_pattern = |/bin/false
EOF
  if [ -e "$W/proc/sys/kernel/core_pattern" ] && [ "$(kern_ist kernel.core_pattern)" != "|/bin/false" ]; then
    tu "Speicherabzüge sofort abschalten (kernel.core_pattern)" core_setzen
  fi
  GEAENDERT=0
  datei_setzen /etc/systemd/journald.conf.d/60-ju-haertung.conf 644 <<'EOF'
# JU Hamburg Finanzen – Protokoll dauerhaft auf der Platte, aber begrenzt
[Journal]
Storage=persistent
SystemMaxUse=1G
SystemKeepFree=2G
MaxFileSec=1month
EOF
  if [ "$GEAENDERT" -eq 1 ]; then tu "journald mit den neuen Grenzen neu starten" x systemctl try-restart systemd-journald.service; fi
  risiko "gering. Rechte werden nur enger gestellt; Fehlerberichte von Ubuntu (apport) entfallen; das Protokoll belegt höchstens 1 GB."
}

m_status() {
  local f
  if ! an STATUS ja; then
    stand "abgeschaltet (STATUS=nein)"
    dienst_aus ju-serverschutz-status.timer
    for f in "$UNITS/ju-serverschutz-status.timer" "$UNITS/ju-serverschutz-status.service" "$LIB/status.json"; do datei_entfernen "$f"; done
    units_neu
    return 0
  fi
  if [ -f "$W$LIB/status.json" ]; then stand "letzter Bericht vom $(date -r "$W$LIB/status.json" '+%d.%m.%Y %H:%M')"; else stand "noch kein Bericht"; fi
  unit_setzen ju-serverschutz-status.service; unit_setzen ju-serverschutz-status.timer; units_neu
  dienst_an ju-serverschutz-status.timer
  if [ ! -d "$W$LIB" ] || [ "$(stat -c %a "$W$LIB")" != 755 ]; then tu "$LIB für das Programm lesbar machen (root, 755)" install -d -m 755 "$W$LIB"; fi
  risiko "keines. Der Bericht enthält nur Zustände und Anzahlen – keine Adressen, Namen oder Geheimnisse; das Programm kann ihn nur lesen."
}

# ---------------------------------------------------------------- Ablauf

while [ $# -gt 0 ]; do
  case "$1" in
    --pruefen) PRUEFEN=1 ;;
    --zurueck) ZURUECK=1 ;;
    --bestaetigen) BESTAETIGEN=1 ;;
    --ssh-nur-schluessel) OPT_SCHLUESSEL=1 ;;
    --ssh-nur-tailscale) OPT_TAILSCALE=1 ;;
    --ohne-rueckfrage) RUECKFRAGE=0 ;;
    sperrliste|status|status-schreiben) BEFEHL="$1" ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) die "Unbekannte Option: $1 (Hilfe: sudo ju-finanzen haerten --help)" ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || [ -n "$W" ] || die "Bitte mit sudo ausführen: sudo ju-finanzen haerten"
[ -x "$PY" ] || die "python3 fehlt ($PY)."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
trap 'abbruch INT' INT; trap 'abbruch TERM' TERM; trap 'abbruch HUP' HUP
ZAEHLER="$TMP/aenderungen"; : > "$ZAEHLER"
SITZUNG="$(sitzung)"   # jetzt bestimmen: später ist die Eingabe mancher Schritte eine Datei und kein Terminal mehr

case "$BEFEHL" in
  status) sshd_bereit; py status --zeigen; ausstehend_zeigen; exit 0 ;;
  status-schreiben) if an STATUS ja; then sshd_bereit; py status; fi; exit 0 ;;
esac

# Nie zwei ändernde Läufe zugleich (auch nicht Zeitgeber und Handaufruf); --pruefen ändert nichts und braucht das nicht.
# Die Sperrdatei liegt in /run (nur root): in /run/lock dürfte jedes Konto sie vorab anlegen oder selbst halten.
if [ "$PRUEFEN" -eq 0 ]; then
  exec 9> "$W/run/ju-haertung.lock"
  if [ "$ZURUECK" -eq 1 ] && [ "$RUECKFRAGE" -eq 0 ]; then
    # Die geplante Rücknahme wartet auf einen noch laufenden Lauf – und findet danach in jedem Fall statt, auch wenn er hängt.
    flock -w "$FRIST" 9 || aus "Die Sperre wurde nicht frei – die geplante Rücknahme läuft trotzdem."
  elif ! flock -n 9; then
    if [ "$BEFEHL" = sperrliste ]; then exit 0; fi
    die "Die Härtung läuft gerade schon (oder der Zeitgeber der Sperrliste) – bitte in einer Minute erneut."
  fi
fi

if [ "$BEFEHL" = sperrliste ]; then
  if ! an SPERRLISTE ja; then exit 0; fi
  sperrliste_holen; exit $?
fi

AUSSTEHEND="$(ausstehend)"

if [ "$BESTAETIGEN" -eq 1 ]; then
  [ "$PRUEFEN$ZURUECK" = 00 ] || die "--bestaetigen steht für sich allein: sudo ju-finanzen haerten --bestaetigen"
  if [ -z "$AUSSTEHEND" ]; then say "Es steht keine Rücknahme aus – nichts zu bestätigen."; exit 0; fi
  LOG="$AUSSTEHEND/protokoll.log"
  if [ "$SITZUNG" = unbekannt ] || [ "$(sed -n 2p "$AUSSTEHEND/RUECKNAHME")" = "$SITZUNG" ]; then
    die "Nicht bestätigt: Dieser Aufruf kommt aus derselben Anmeldung, die den Lauf gestartet hat (oder die Anmeldung lässt sich nicht erkennen) – das beweist nicht, dass eine NEUE Anmeldung noch gelingt. Bitte ein zweites Fenster öffnen (Browser-Terminal des Anbieters oder SSH), dort neu anmelden und den Befehl dort ausführen. $(frist_text "$AUSSTEHEND")"
  fi
  ruecknahme_absagen
  rm -f "$AUSSTEHEND/RUECKNAHME"
  aus "--- $(date -Is) bestätigt aus einer zweiten Anmeldung – die geplante Rücknahme ist abgesagt"
  say "Bestätigt: Die Änderungen des Laufs $(basename "$AUSSTEHEND") bleiben. Die geplante Rücknahme ist abgesagt."
  info "Später doch zurück: sudo ju-finanzen haerten --zurueck        Bericht: sudo ju-finanzen schutz-status"
  exit 0
fi

if [ "$ZURUECK" -eq 1 ]; then
  LETZTE="$AUSSTEHEND"   # ein unbestätigter Lauf ist immer der letzte: solange er aussteht, beginnt kein neuer
  if [ -z "$LETZTE" ] && [ "$RUECKFRAGE" -eq 0 ]; then say "Keine Rücknahme ausstehend – nichts zu tun."; exit 0; fi
  if [ -z "$LETZTE" ]; then
    while IFS= read -r d; do
      if [ -s "$d/MANIFEST" ] && [ ! -e "$d/ZURUECKGESPIELT" ]; then LETZTE="$d"; fi
    done < <(find "$SICHERUNGEN" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
  fi
  [ -n "$LETZTE" ] || die "Es gibt keinen Lauf, der sich zurücknehmen lässt ($SICHERUNGEN)."
  say "Stand vor dem Lauf $(basename "$LETZTE") wiederherstellen"
  if [ "$PRUEFEN" -eq 1 ]; then
    awk -F'\t' '$1 != "summe" { printf "    würde zurücknehmen: %s %s\n", $1, $2 }' "$LETZTE/MANIFEST"
    info "Nur Prüfung (--pruefen): nichts geändert."
    exit 0
  fi
  trap '' INT TERM HUP   # eine begonnene Rücknahme läuft zu Ende – auch wenn das Fenster zugeht
  LOG="$LETZTE/protokoll.log"
  aus "--- $(date -Is) Zurücknehmen$(if [ "$RUECKFRAGE" -eq 0 ]; then echo " (geplante Rücknahme: der Lauf wurde nicht bestätigt)"; fi)"
  if zurueck_ab "$LETZTE" 0; then
    date -Is > "$LETZTE/ZURUECKGESPIELT"
    rm -f "$LETZTE/RUECKNAHME"
    if hat systemctl; then x systemctl stop "$RUECK_UNIT.timer" 2>/dev/null || true; fi
    say "Fertig: Der Stand vor dem Lauf $(basename "$LETZTE") gilt wieder."
    info "Ein weiteres --zurueck nimmt den Lauf davor zurück. Neu härten: sudo ju-finanzen haerten"
  else
    die "Das Zurücknehmen war unvollständig – bitte die Meldungen oben prüfen. Sicherung: $LETZTE"
  fi
  # Arbeitsdaten des Serverschutzes stehen in keiner Sicherung: ohne Zeitgeber bzw. Tabelle wären sie nur ein veralteter
  # Stand, den das Programm weiter als „ok“ zeigte. Installierte Pakete bleiben.
  if [ -f "$W$UNITS/ju-serverschutz-status.timer" ]; then py status >/dev/null || true; else rm -f "$W$LIB/status.json"; fi
  if [ ! -f "$W$NFT_DATEI" ]; then rm -f "$W$LIB/drop4.txt" "$W$LIB/drop6.txt" "$W$MENGEN_DATEI" "$W$LIB/zaehler.json" "$W$LIB/sperrliste.fehler"; fi
  exit 0
fi

if [ "$PRUEFEN" -eq 0 ] && [ -n "$AUSSTEHEND" ]; then
  die "Der Lauf $(basename "$AUSSTEHEND") ist noch nicht bestätigt. Erst entscheiden: behalten mit „sudo ju-finanzen haerten --bestaetigen“ (aus einem zweiten Fenster, dort neu angemeldet) oder zurücknehmen mit „sudo ju-finanzen haerten --zurueck“."
fi
lage_ermitteln
if [ "$PRUEFEN" -eq 0 ]; then
  schalter_pruefen   # vor jeder Änderung: ohne Nachweis endet der Lauf hier
  if [ -z "$W" ]; then
    [ -d /run/systemd/system ] || die "systemd läuft hier nicht – die Härtung ist für den Server gedacht."
    [ "$(readlink -f "$HIER")" = "$(readlink -f /opt/ju-finanzen/current/server)" ] \
      || die "Bitte die installierte Version aufrufen: sudo ju-finanzen haerten (die Zeitgeber verweisen auf /opt/ju-finanzen/current)."
  fi
  SICHERUNG="$SICHERUNGEN/$(date +%Y%m%d-%H%M%S)"
  install -d -m 700 "$SICHERUNGEN"
  mkdir -m 700 "$SICHERUNG"   # ohne -p: zwei Läufe in derselben Sekunde teilen sich keinen Ordner
  MANIFEST="$SICHERUNG/MANIFEST"; : > "$MANIFEST"
  LOG="$SICHERUNG/protokoll.log"; : > "$LOG"
  say "Härtung des Betriebssystems anwenden"
  info "Sicherung und Protokoll dieses Laufs: ${SICHERUNG#"$W"}"
else
  say "Härtung des Betriebssystems prüfen (--pruefen: es wird nichts geändert)"
  info "So liest sich der Bericht – je Maßnahme:  Stand = wie es jetzt ist · Änderung = was „haerten“ tun würde ·"
  info "Risiko = was dabei schiefgehen könnte · Hinweis = nur eine Meldung, daran wird nichts geändert."
  ausstehend_zeigen
  schalter_pruefen
fi

massnahme 0 "Schalter ($ETC/haertung.conf)" m_schalter
massnahme 1 "Firewall (ufw und Schutztabelle inet ju_schutz)" m_firewall
massnahme 2 "Sperrliste (Spamhaus DROP)" m_sperrliste
massnahme 3 "fail2ban (SSH)" m_fail2ban
massnahme 4 "Kern-Einstellungen (sysctl)" m_kern
massnahme 5 "Automatische Sicherheitsupdates" m_updates
massnahme 6 "Unnötige Dienste (Docker)" m_dienste
massnahme 7 "SSH-Grundhärtung" m_ssh
massnahme 8 "Rechte, Speicherabzüge, Protokoll" m_dateien
massnahme 9 "Statusbericht für das Programm" m_status

N="$(wc -l < "$ZAEHLER")"
RC=0
if [ "$PRUEFEN" -eq 1 ]; then
  say "Prüfung beendet: $N Änderungen stünden an – nichts wurde geändert."
  nicht_melden "Nicht prüfbar (bitte erst klären):" || RC=1
  if [ "$NUR_SCHLUESSEL$NUR_TAILSCALE" = 00 ]; then info "Wer sich wie anmelden darf (Passwort, root) und der SSH-Port bleiben, wie sie sind."; fi
  info "Anwenden: sudo ju-finanzen haerten        Zurück danach: sudo ju-finanzen haerten --zurueck"
  if [ -e "$TMP/riskant" ] && [ "$RUECKFRAGE" -eq 1 ]; then
    info "Die Änderungen an Firewall bzw. SSH-Zugang gelten zuerst nur auf Probe: Ohne „sudo ju-finanzen haerten --bestaetigen“"
    info "aus einem zweiten Fenster nimmt der Server den ganzen Lauf nach $(dauer "$FRIST") von selbst zurück."
  fi
  exit "$RC"
fi
if an STATUS ja; then py status >/dev/null || info "Hinweis: Der Statusbericht ließ sich nicht schreiben."; fi
if [ ! -s "$MANIFEST" ]; then
  if [ -e "$SICHERUNG/RUECKNAHME" ]; then x systemctl stop "$RUECK_UNIT.timer" 2>/dev/null || true; fi   # nichts geblieben, was sie zurücknähme
  rm -rf -- "$SICHERUNG"; LOG=""
  if [ -s "$TMP/nicht" ]; then say "Fertig: nichts geändert."; else say "Fertig: Alles war bereits umgesetzt – nichts geändert."; fi
else
  say "Fertig: $N Änderungen."
  info "Zurück auf den Stand vor diesem Lauf:  sudo ju-finanzen haerten --zurueck"
fi
nicht_melden "NICHT ANGEWENDET (zurückgenommen; die übrigen Maßnahmen gelten) – nach dem Beheben erneut: sudo ju-finanzen haerten" || RC=1
info "Bericht: sudo ju-finanzen schutz-status"
if [ -n "$SICHERUNG" ] && [ -e "$SICHERUNG/RUECKNAHME" ]; then
  say "WICHTIG – noch ein Schritt: Die Änderungen gelten erst auf Probe."
  info "$(frist_text "$SICHERUNG")"
  info "Öffne jetzt ein ZWEITES Fenster, melde dich dort neu an und führe aus:"
  info ""
  info "    sudo ju-finanzen haerten --bestaetigen"
  info ""
  info "Dieses Fenster so lange offen lassen. Gelingt die neue Anmeldung nicht: nichts tun – der alte Stand kommt von selbst zurück."
fi
exit "$RC"
