#!/usr/bin/env bash
# Nachgestellte Systembefehle für test_haertung.py: liegt als Verweis unter <Wurzel>/stub/<Befehl> (systemctl, ufw, nft …).
# Zustand in <Wurzel>/zustand, jeder Aufruf steht in <Wurzel>/aufrufe.log. Nur erfundene Daten (Dokumentations-Adressen).
set -u
W="$(cd "$(dirname "$0")/.." && pwd)"
Z="$W/zustand"
NAME="$(basename "$0")"
printf '%s %s\n' "$NAME" "$*" >> "$W/aufrufe.log"
if [ -e "$Z/scheitert-$NAME" ] && [ "$*" = "$(cat "$Z/scheitert-$NAME")" ]; then exit 1; fi   # Test: genau dieser Aufruf scheitert
# Test: bei genau diesem Aufruf trifft ein Signal die ganze Prozessgruppe – wie Strg-C oder ein geschlossenes Fenster
if [ -e "$Z/signal-$NAME" ] && [ "$*" = "$(sed -n 2p "$Z/signal-$NAME")" ]; then
  s="$(sed -n 1p "$Z/signal-$NAME")"; rm -f "$Z/signal-$NAME"; kill -s "$s" 0; sleep 5
fi
LIB="$W/var/lib/ju-serverschutz"
# Tabelle neu geladen: Regeln aus ju_schutz.nft (Mengen leer), danach die Sperrliste aus ju_mengen.nft, falls vorhanden
tabelle_an() { touch "$Z/tabelle"; rm -f "$Z/mengen"; if [ -f "$LIB/ju_mengen.nft" ]; then cp "$LIB/ju_mengen.nft" "$Z/mengen"; fi; }

case "$NAME" in
  systemctl)
    args=(); for a in "$@"; do case "$a" in -q|--now|--no-pager|--value|-p) ;; *) args+=("$a") ;; esac; done
    cmd="${args[0]:-}"; unit="${args[1]:-}"
    da() { [ -e "$Z/unit-$unit" ] || [ -e "$W/etc/systemd/system/$unit" ] || [ -e "$W/run/systemd/system/$unit" ]; }
    case "$cmd" in
      show)   # show UNIT -p EIGENSCHAFT --value; Zeiten wie systemd in der Zeitzone des Aufrufers
        case "${args[2]:-}" in
          SubState) cat "$Z/sub-$unit" 2>/dev/null || echo dead ;;
          ActiveState) cat "$Z/active-$unit" 2>/dev/null || echo inactive ;;
          NextElapseUSecRealtime) if [ -s "$Z/next-$unit" ]; then date -d "@$(cat "$Z/next-$unit")" '+%a %Y-%m-%d %H:%M:%S %Z'; else echo; fi ;;
          *) exit 1 ;;
        esac ;;
      is-enabled) if ! da; then echo not-found; exit 4; fi; cat "$Z/enabled-$unit" 2>/dev/null || echo disabled ;;
      is-active) cat "$Z/active-$unit" 2>/dev/null || echo inactive ;;
      enable) da || exit 1; echo enabled > "$Z/enabled-$unit"; case " $* " in *" --now "*) echo active > "$Z/active-$unit" ;; esac ;;
      disable) da || exit 1; echo disabled > "$Z/enabled-$unit"; case " $* " in *" --now "*) echo inactive > "$Z/active-$unit"; rm -f "$Z/sub-$unit" "$Z/next-$unit" ;; esac ;;
      start|restart|reload) da || exit 1; echo active > "$Z/active-$unit"
        case "$unit" in *.timer)   # ein Timer wartet danach; steht in seiner Datei eine feste Uhrzeit (OnCalendar), ist sie der nächste Ablauf
          if [ -e "$Z/timer-startet-nicht" ]; then echo inactive > "$Z/active-$unit"; exit 0; fi   # Test: systemd meldet Erfolg, der Timer wartet aber nicht
          wann="$(sed -n 's/^OnCalendar=//p' "$W/run/systemd/system/$unit" "$W/etc/systemd/system/$unit" 2>/dev/null | head -n 1)"
          if [ -n "$wann" ]; then date -d "$wann" +%s > "$Z/next-$unit"; fi
          echo waiting > "$Z/sub-$unit" ;;
        esac
        if [ "$unit" = ju-serverschutz.service ]; then
          [ -f "$LIB/ju_schutz.nft" ] || exit 1; tabelle_an
          if [ -e "$Z/firewall-bricht" ]; then touch "$Z/nicht-erreichbar"; fi   # Test: nach dem Laden ist der Dienst weg
        fi ;;
      stop)
        if [ -e "$Z/stopp-wirkt-nicht" ]; then exit 0; fi   # Test: systemd meldet Erfolg, die Unit läuft aber weiter
        echo inactive > "$Z/active-$unit"; rm -f "$Z/sub-$unit" "$Z/next-$unit"
        if [ "$unit" = ju-serverschutz.service ]; then rm -f "$Z/tabelle" "$Z/mengen"; fi ;;
      try-restart|daemon-reload) ;;
      *) exit 1 ;;
    esac ;;
  nft)
    case "$*" in
      "list table inet ju_schutz") [ -e "$Z/tabelle" ] ;;
      "delete table inet ju_schutz") rm -f "$Z/tabelle" "$Z/mengen" ;;
      "-j list set inet ju_schutz drop4")
        [ -e "$Z/tabelle" ] || exit 1
        if grep -qs '^add element inet ju_schutz drop4' "$Z/mengen"; then echo '{"nftables": [{"set": {"name": "drop4", "elem": [{"prefix": {"addr": "192.0.2.0", "len": 30}}]}}]}'
        else echo '{"nftables": [{"set": {"name": "drop4"}}]}'; fi ;;
      "-j list counters table inet ju_schutz") cat "$Z/zaehler.json" 2>/dev/null || echo '{"nftables": [{"counter": {"name": "sperrliste", "packets": 7}}, {"counter": {"name": "ssh_takt", "packets": 3}}]}' ;;
      "-c -f "*) if grep -q '^flush set' "$3"; then [ -e "$Z/tabelle" ]; else grep -q 'table inet ju_schutz' "$3"; fi ;;   # Mengen brauchen die Tabelle
      "-f "*) [ -e "$Z/tabelle" ] && grep -q '^flush set inet ju_schutz drop4' "$2" && cp "$2" "$Z/mengen" ;;
      *) exit 1 ;;
    esac ;;
  ufw)
    case "$*" in
      status|"status verbose")
        if [ -e "$Z/ufw-aus" ]; then echo "Status: inactive"; exit 0; fi
        echo "Status: active"; echo "Logging: on (low)"
        echo "Default: $(grep -q 'DEFAULT_INPUT_POLICY="DROP"' "$W/etc/default/ufw" && echo deny || echo allow) (incoming), allow (outgoing), deny (routed)"
        echo; echo "To                         Action      From"; echo "--                         ------      ----"
        cat "$Z/ufw-status" 2>/dev/null ;;
      "show added") echo "Added user rules (see 'ufw status' for running firewall):"; cat "$Z/ufw-regeln" 2>/dev/null ;;
      "default deny incoming") sed -i 's/^DEFAULT_INPUT_POLICY=.*/DEFAULT_INPUT_POLICY="DROP"/' "$W/etc/default/ufw" ;;
      "default allow incoming") sed -i 's/^DEFAULT_INPUT_POLICY=.*/DEFAULT_INPUT_POLICY="ACCEPT"/' "$W/etc/default/ufw" ;;
      "--force enable") rm -f "$Z/ufw-aus"; echo "ENABLED=yes" > "$W/etc/ufw/ufw.conf" ;;
      "--force disable") touch "$Z/ufw-aus"; echo "ENABLED=no" > "$W/etc/ufw/ufw.conf" ;;
      allow*) echo "ufw $*" >> "$Z/ufw-regeln"; echo "ufw $*" >> "$W/etc/ufw/user.rules" ;;   # der Test hält user.rules im Format von „show added“
      "delete allow"*) shift; for f in "$Z/ufw-regeln" "$W/etc/ufw/user.rules"; do grep -vxF "ufw $*" "$f" > "$f.neu"; mv "$f.neu" "$f"; done ;;
      *) exit 1 ;;
    esac ;;
  sshd)
    [ -d "$W/run/sshd" ] || { echo "Missing privilege separation directory: /run/sshd" >&2; exit 255; }
    case "$*" in
      -t) ! grep -qs KAPUTT "$W/etc/ssh/sshd_config.d/"*.conf ;;
      -T)  # wie sshd: der zuerst gelesene Wert gilt – erst die Dateien in sshd_config.d, dann die Vorgaben
        { cat "$W/etc/ssh/sshd_config.d/"*.conf 2>/dev/null; cat "$Z/sshd-vorgaben"; } | awk '
          /^[A-Za-z]/ { k = tolower($1); if (!(k in gesehen)) { gesehen[k] = 1; $1 = k; print tolower($0) } }' ;;
      *) exit 1 ;;
    esac ;;
  sysctl)
    setzen() {   # wie der Kern: das Umschalten der Weiterleitung setzt accept_redirects auf „nicht Router = annehmen“ zurück
      mkdir -p "$(dirname "$W/proc/sys/${1//.//}")"; printf '%s\n' "$2" > "$W/proc/sys/${1//.//}"
      if [ "$1" = net.ipv4.ip_forward ]; then echo $((1 - $2)) > "$W/proc/sys/net/ipv4/conf/all/accept_redirects"; fi
    }
    case "$1 $2" in
      "-q -w") setzen "${3%%=*}" "${3#*=}" ;;
      "-q -p") while IFS= read -r zeile; do case "$zeile" in [a-z]*" = "*) setzen "${zeile%% = *}" "${zeile#* = }" ;; esac; done < "$3" ;;
      *) exit 1 ;;
    esac ;;
  tailscale)
    case "$*" in
      "status --json") cat "$Z/tailscale-status" 2>/dev/null || echo '{"BackendState": "Running"}' ;;
      "debug prefs") cat "$Z/tailscale-prefs" 2>/dev/null || echo '{"RunSSH": true, "AdvertiseRoutes": null}' ;;
      *) exit 1 ;;
    esac ;;
  curl)
    ziel=""; url=""
    while [ $# -gt 0 ]; do case "$1" in -o) ziel="$2"; shift ;; http*) url="$1" ;; esac; shift; done
    case "$url" in
      https://www.spamhaus.org/drop/drop.txt) cp "$Z/drop.txt" "$ziel" ;;
      https://www.spamhaus.org/drop/dropv6.txt) cp "$Z/dropv6.txt" "$ziel" ;;
      http://127.0.0.1:*|http://archive.ubuntu.com/*) [ ! -e "$Z/nicht-erreichbar" ] ;;
      *) exit 1 ;;
    esac ;;
  dpkg-query) if grep -qsx "$3" "$Z/pakete"; then printf 'install ok installed'; else exit 1; fi ;;
  apt-get)
    if [ "$1" = -o ]; then shift 2; fi
    case "$1" in
      update) ;;
      install) echo "${*: -1}" >> "$Z/pakete"; touch "$Z/unit-fail2ban.service" ;;
      -s) cat "$Z/apt-simulation" 2>/dev/null ;;
      *) exit 1 ;;
    esac ;;
  apt-config) printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' ;;
  fail2ban-client)
    case "$*" in
      -t|ping) ;;
      banned) echo "[{'sshd': ['192.0.2.9', '2001:db8::9']}, {'recidive': []}]" ;;
      *) exit 1 ;;
    esac ;;
  ss) cat "$Z/ss" 2>/dev/null ;;
  ip) case "$*" in "-j addr") cat "$Z/ip-addr" 2>/dev/null ;; *) echo '[]' ;; esac ;;
  journalctl)   # Zeilen mit „@<Unixzeit> “ davor tragen ihre Zeit, alle anderen sind alt; „--since @N“ zeigt nur Zeilen ab N
    seit=0
    while [ $# -gt 0 ]; do if [ "$1" = --since ]; then case "$2" in @*) seit="${2#@}" ;; esac; fi; shift; done
    awk -v seit="$seit" '{ t = 0; if ($1 ~ /^@[0-9]+$/) { t = substr($1, 2) + 0; sub(/^@[0-9]+ /, "") } if (t >= seit) print }' "$Z/journal" 2>/dev/null ;;
  findmnt) ;;
  *) exit 127 ;;
esac
