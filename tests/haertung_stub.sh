#!/usr/bin/env bash
# Nachgestellte Systembefehle für test_haertung.py: liegt als Verweis unter <Wurzel>/stub/<Befehl> (systemctl, ufw, nft …).
# Zustand in <Wurzel>/zustand, jeder Aufruf steht in <Wurzel>/aufrufe.log. Nur erfundene Daten (Dokumentations-Adressen).
set -u
W="$(cd "$(dirname "$0")/.." && pwd)"
Z="$W/zustand"
NAME="$(basename "$0")"
printf '%s %s\n' "$NAME" "$*" >> "$W/aufrufe.log"
if [ -e "$Z/scheitert-$NAME" ] && [ "$*" = "$(cat "$Z/scheitert-$NAME")" ]; then exit 1; fi   # Test: genau dieser Aufruf scheitert

case "$NAME" in
  systemctl)
    args=(); for a in "$@"; do case "$a" in -q|--now|--no-pager) ;; *) args+=("$a") ;; esac; done
    cmd="${args[0]:-}"; unit="${args[1]:-}"
    da() { [ -e "$Z/unit-$unit" ] || [ -e "$W/etc/systemd/system/$unit" ]; }
    case "$cmd" in
      is-enabled) if ! da; then echo not-found; exit 4; fi; cat "$Z/enabled-$unit" 2>/dev/null || echo disabled ;;
      is-active) cat "$Z/active-$unit" 2>/dev/null || echo inactive ;;
      enable) da || exit 1; echo enabled > "$Z/enabled-$unit"; case " $* " in *" --now "*) echo active > "$Z/active-$unit" ;; esac ;;
      disable) da || exit 1; echo disabled > "$Z/enabled-$unit"; case " $* " in *" --now "*) echo inactive > "$Z/active-$unit" ;; esac ;;
      start|restart|reload) da || exit 1; echo active > "$Z/active-$unit"
        if [ "$unit" = ju-serverschutz.service ]; then
          [ -f "$W/var/lib/ju-serverschutz/ju_schutz.nft" ] || exit 1; touch "$Z/tabelle"
          if [ -e "$Z/firewall-bricht" ]; then touch "$Z/nicht-erreichbar"; fi   # Test: nach dem Laden ist der Dienst weg
        fi ;;
      stop) echo inactive > "$Z/active-$unit"; if [ "$unit" = ju-serverschutz.service ]; then rm -f "$Z/tabelle"; fi ;;
      try-restart|daemon-reload) ;;
      *) exit 1 ;;
    esac ;;
  nft)
    case "$*" in
      "list table inet ju_schutz") [ -e "$Z/tabelle" ] ;;
      "delete table inet ju_schutz") rm -f "$Z/tabelle" ;;
      "-j list counters table inet ju_schutz") cat "$Z/zaehler.json" 2>/dev/null || echo '{"nftables": [{"counter": {"name": "sperrliste", "packets": 7}}, {"counter": {"name": "ssh_takt", "packets": 3}}]}' ;;
      "-c -f "*) grep -q 'table inet ju_schutz' "$3" ;;
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
      "--force enable") rm -f "$Z/ufw-aus"; echo "ENABLED=yes" > "$W/etc/ufw/ufw.conf" ;;
      "--force disable") touch "$Z/ufw-aus"; echo "ENABLED=no" > "$W/etc/ufw/ufw.conf" ;;
      reload) cp "$W/etc/ufw/user.rules" "$Z/ufw-regeln" ;;   # der Test hält user.rules im Format von „show added“
      allow*) echo "ufw $*" >> "$Z/ufw-regeln"; echo "ufw $*" >> "$W/etc/ufw/user.rules" ;;
      *) exit 1 ;;
    esac ;;
  sshd)
    case "$*" in
      -t) ! grep -qs KAPUTT "$W/etc/ssh/sshd_config.d/"*.conf ;;
      -T)  # wie sshd: der zuerst gelesene Wert gilt – erst die Dateien in sshd_config.d, dann die Vorgaben
        { cat "$W/etc/ssh/sshd_config.d/"*.conf 2>/dev/null; cat "$Z/sshd-vorgaben"; } | awk '
          /^[A-Za-z]/ { k = tolower($1); if (!(k in gesehen)) { gesehen[k] = 1; $1 = k; print tolower($0) } }' ;;
      *) exit 1 ;;
    esac ;;
  sysctl)
    [ "$1 $2" = "-q -w" ] || exit 1
    name="${3%%=*}"; mkdir -p "$(dirname "$W/proc/sys/${name//.//}")"; printf '%s\n' "${3#*=}" > "$W/proc/sys/${name//.//}" ;;
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
  journalctl) cat "$Z/journal" 2>/dev/null ;;
  findmnt) ;;
  *) exit 127 ;;
esac
