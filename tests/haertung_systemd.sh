#!/usr/bin/env bash
# Geplante Rücknahme der Härtung (server/haerten.sh) auf ECHTEM systemd – von Hand, nicht in der CI (rund 20 Minuten,
# weil die Frist von fünf Minuten wirklich abläuft). test_haertung.py stellt systemd nur nach.
#
# Startet systemd als PID 1 in eigenen Namensräumen (pid, mount, net, uts, ipc, cgroup) auf einem Overlay über / . Alles
# Geschriebene liegt in einem tmpfs, das Netz ist leer – ufw und nft treffen den Rechner nicht. Das Overlay überlebt den
# „Neustart“ (systemd hart beendet und neu gestartet), /run und das Netz sind danach neu wie nach einem echten Start.
# Braucht root, unshare/nsenter, systemd, ufw, nft und sshd; nichts davon wird außerhalb des Overlays verändert.
# NICHT isoliert sind kernel.*- und fs.*-sysctl: Im Rig sind deshalb KERN und DATEIEN aus, am Ende wird verglichen.
#
#   sudo tests/haertung_systemd.sh [frist] [bestaetigen] [neustart] [gescheitert]      ohne Angabe: alle Szenen
# shellcheck disable=SC2016   # die Befehle für das Rig sollen erst dort ausgewertet werden
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausführen." >&2; exit 1; }
QUELLE="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; HALTER=""; START=""; FEHLER=0
H=/opt/ju-finanzen/current/server/haerten.sh
LAEUFE=/var/backups/ju-haertung
KERN='^(kernel\.(kptr|dmesg|yama|unpriv|core_pattern|randomize)|fs\.(protected|suid)|dev\.tty)'
sysctl -a 2>/dev/null | grep -E "$KERN" > "$T.sysctl"

strom_weg() { if [ -n "$START" ]; then kill -9 "$START" 2>/dev/null || true; wait "$START" 2>/dev/null || true; START=""; fi; }
ende() {
  strom_weg
  if [ -n "$HALTER" ]; then kill "$HALTER" 2>/dev/null || true; fi
  if sysctl -a 2>/dev/null | grep -E "$KERN" | cmp -s - "$T.sysctl"; then echo "ok      kernel.*/fs.* des Rechners unverändert"
  else echo "FEHLER  kernel.*/fs.* des Rechners haben sich geändert – Stand vorher: $T.sysctl"; FEHLER=1; fi
  rm -f "$T.lauf" "$T.aus"; if [ "$FEHLER" -eq 0 ]; then rm -f "$T.sysctl"; fi
  rmdir "$T" 2>/dev/null || true
  exit "$FEHLER"
}
trap ende EXIT

# Halter: eigener Mount-Namensraum mit dem tmpfs für die obere Schicht – bleibt über alle Systemstarts bestehen
unshare --mount --propagation private bash -c 'mount -t tmpfs -o size=2g tmpfs "$1"; mkdir "$1/upper" "$1/work" "$1/root"; exec sleep infinity' halter "$T" &
HALTER=$!; sleep 1

starten() {   # ein Systemstart: neue Namensräume, Overlay einhängen, systemd als PID 1
  nsenter -t "$HALTER" -m unshare --mount --propagation private --pid --fork --net --uts --ipc --cgroup --kill-child bash -c '
    set -eu; R="$1/root"
    mount -t overlay overlay -o "lowerdir=/,upperdir=$1/upper,workdir=$1/work" "$R"
    mount -t proc proc "$R/proc"; mount -t sysfs sysfs "$R/sys"; mount -t cgroup2 cgroup2 "$R/sys/fs/cgroup"
    mount -t tmpfs -o mode=755 tmpfs "$R/run"; mount -t tmpfs -o mode=1777 tmpfs "$R/tmp"; mount -t tmpfs -o mode=755 tmpfs "$R/dev"
    for d in null zero full random urandom tty; do touch "$R/dev/$d"; mount --bind "/dev/$d" "$R/dev/$d"; done
    mkdir "$R/dev/pts" "$R/dev/shm"; mount -t devpts -o newinstance,ptmxmode=0666 devpts "$R/dev/pts"; ln -s pts/ptmx "$R/dev/ptmx"
    ln -s /proc/self/fd "$R/dev/fd"; : > "$R/dev/console"; mount --bind /dev/null "$R/dev/console"
    for u in docker.service docker.socket containerd.service postgresql.service postgresql@.service systemd-networkd.service \
        systemd-resolved.service systemd-timesyncd.service systemd-logind.service getty@.service console-getty.service \
        snapd.service snapd.socket apt-daily.service apt-daily-upgrade.service; do ln -sf /dev/null "$R/etc/systemd/system/$u"; done
    ip link set lo up; hostname rig
    cd "$R"; mkdir -p oldroot; pivot_root . oldroot; umount -l /oldroot
    exec env -i container=rig PATH=/usr/sbin:/usr/bin:/sbin:/bin /lib/systemd/systemd --system --log-target=journal --show-status=false
  ' rig "$T" &
  START=$!
  for _ in $(seq 1 60); do if drin 'systemctl is-system-running 2>/dev/null | grep -Eq "running|degraded"' 2>/dev/null; then return 0; fi; sleep 1; done
  echo "systemd startet im Rig nicht." >&2; exit 1
}
drin() { nsenter -t "$(pgrep -P "$START" | head -n 1)" -a env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin TERM=dumb HOME=/root bash -c "$1"; }
# ein „Fenster“ = eine eigene Anmeldung: das Setzen der loginuid vergibt wie pam_loginuid eine neue Audit-Sitzung
fenster() { drin "echo 0 > /proc/self/loginuid; $H $*"; }
pruefe() { local text="$1"; shift; if "$@"; then echo "ok      $text"; else echo "FEHLER  $text"; FEHLER=1; fi; }
enthaelt() { grep -q -- "$2" <<< "$1"; }
uhr() { env -u TZ date -d "@$1" +%T; }   # wie im Rig: Zeitzone des Rechners
zwischen() { [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]; }
warte_bis() { local bis="$1"; while [ "$(date +%s)" -lt "$bis" ]; do sleep 2; done; }
marke() { drin "sed -n 's/^$1=//p' $LAEUFE/*/RUECKNAHME"; }
gehaertet() { drin 'nft list table inet ju_schutz >/dev/null 2>&1 && [ -e /etc/ssh/sshd_config.d/00-ju-haertung.conf ]'; }
zurueck_um() { date -d "$(drin "sed -n 's/^--- \(.*\) Zurücknehmen (geplante.*/\1/p' $LAEUFE/*/protokoll.log | tail -n 1")" +%s; }
lauf() {   # haerten in einem ersten Fenster; fail2ban startet im leeren Netz nicht – der Lauf endet deshalb mit 1, der Rest gilt
  fenster > "$T.lauf" 2>&1 || true
  pruefe "Lauf angewendet, Schutztabelle geladen, SSH-Datei geschrieben" gehaertet
  pruefe "Abschluss nennt die Frist: $(grep -o 'In 5 Minuten (um [0-9:]* Uhr)' "$T.lauf" || echo '(fehlt)')" grep -q "In 5 Minuten (um $(uhr "$(marke ende)") Uhr)" "$T.lauf"
}

starten
drin "mkdir -p /opt/ju-finanzen/releases/rig /etc/ju-finanzen && cp -r '$QUELLE/server' /opt/ju-finanzen/releases/rig/ && ln -sfn releases/rig /opt/ju-finanzen/current
      sed 's/^KERN=ja/KERN=nein/; s/^DATEIEN=ja/DATEIEN=nein/' '$QUELLE/server/haertung/haertung.conf' > /etc/ju-finanzen/haertung.conf"

if [ $# -eq 0 ]; then set -- frist bestaetigen neustart gescheitert; fi
for szene in "$@"; do
  echo; echo "=== Szene $szene ($(date +%T))"
  case "$szene" in
    frist)   # niemand bestätigt, die Sitzung ist zu Ende, systemd liest zwischendurch neu ein: Rücknahme zur angekündigten Zeit
      lauf; soll="$(marke ende)"
      drin 'systemctl daemon-reload; sleep 20; systemctl daemon-reload'
      pruefe "Timer wartet nach zweimal daemon-reload weiter auf $(uhr "$soll")" enthaelt "$(drin "TZ=UTC systemctl show ju-haertung-ruecknahme.timer -p NextElapseUSecRealtime --value")" "$(date -u -d "@$soll" +%T)"
      warte_bis $((soll + 15))
      ist="$(zurueck_um)"; echo "        angekündigt $(uhr "$soll"), zurückgenommen $(uhr "$ist") – Abweichung $((ist - soll)) s"
      pruefe "Rücknahme höchstens 5 s nach der angekündigten Zeit" zwischen $((ist - soll)) 0 5
      pruefe "Stand von vorher: keine Schutztabelle, keine SSH-Datei, Units der Rücknahme entfernt" \
        drin '! nft list table inet ju_schutz >/dev/null 2>&1 && [ ! -e /etc/ssh/sshd_config.d/00-ju-haertung.conf ] && ! ls /etc/systemd/system/ju-haertung-* /run/systemd/system/ju-haertung-* >/dev/null 2>&1'
      pruefe "--bestaetigen danach nennt die Rücknahme von selbst" enthaelt "$(fenster --bestaetigen 2>&1 || true)" "von selbst zurückgenommen" ;;
    bestaetigen)   # dieselbe Anmeldung und eine unerkennbare werden abgelehnt, eine neue angenommen; danach wartet kein Timer mehr
      drin "echo 0 > /proc/self/loginuid; $H >/dev/null 2>&1; $H --bestaetigen" > "$T.aus" 2>&1 || true
      pruefe "dieselbe Anmeldung: abgelehnt" grep -q "derselben Anmeldung" "$T.aus"
      if [ "$(cat /proc/self/sessionid)" = 4294967295 ]; then   # nur prüfbar, wenn dieser Aufruf selbst keine Audit-Sitzung hat
        pruefe "Anmeldung nicht erkennbar: abgelehnt" enthaelt "$(drin "$H --bestaetigen" 2>&1 || true)" "interaktiv neu anmelden"
      fi
      pruefe "Timer der Frist wartet weiter" enthaelt "$(drin 'systemctl show ju-haertung-ruecknahme.timer -p SubState --value')" waiting
      pruefe "neue Anmeldung: bestätigt" enthaelt "$(fenster --bestaetigen 2>&1 || true)" "Bestätigt: Die Änderungen"
      pruefe "kein Timer der Frist mehr, Marke weg, Härtung gilt" drin "! systemctl is-active -q ju-haertung-ruecknahme.timer && [ ! -e /run/systemd/system/ju-haertung-ruecknahme.timer ] && ! ls $LAEUFE/*/RUECKNAHME >/dev/null 2>&1 && nft list table inet ju_schutz >/dev/null"
      fenster --zurueck > /dev/null 2>&1 || true
      pruefe "--zurueck des bestätigten Laufs entfernt auch die Units der Rücknahme" drin '! ls /etc/systemd/system/ju-haertung-* >/dev/null 2>&1' ;;
    neustart)   # Strom weg in der Frist: nach dem Start gilt der unbestätigte Stand höchstens rund zwei Minuten
      lauf; strom_weg; gestartet="$(date +%s)"; starten
      pruefe "nach dem Start: der Timer der Frist ist weg, der Timer für den Neustart wartet" drin '[ ! -e /run/systemd/system/ju-haertung-ruecknahme.timer ] && [ "$(systemctl show ju-haertung-neustart.timer -p SubState --value)" = waiting ]'
      pruefe "der unbestätigte Stand ist nach dem Start zunächst geladen (ju-serverschutz.service)" gehaertet
      pruefe "--pruefen kündigt die Rücknahme nach dem Neustart an" enthaelt "$(drin "$H --pruefen" 2>&1 || true)" "Etwa zwei Minuten nach dem Start"
      warte_bis $((gestartet + 150))
      pruefe "unbestätigter Lauf ist zurückgenommen" drin "! nft list table inet ju_schutz >/dev/null 2>&1 && ! ls $LAEUFE/*/RUECKNAHME >/dev/null 2>&1"
      echo "        Start des Systems $(uhr "$gestartet"), zurückgenommen $(uhr "$(zurueck_um)") – $(($(zurueck_um) - gestartet)) s nach dem Start" ;;
    gescheitert)   # die Rücknahme scheitert zweimal (sshd -t meldet einen Fehler), systemd wiederholt sie im Minutentakt, der dritte Versuch gelingt
      lauf; soll="$(marke ende)"
      drin 'echo "KeinGueltigesSchluesselwort ja" > /etc/ssh/sshd_config.d/zz-rig-kaputt.conf'
      warte_bis $((soll + 75))
      pruefe "zwei Versuche im Abstand von rund 60 s" [ "$(drin "wc -l < $LAEUFE/*/TEILWEISE")" = 2 ]
      drin "cut -f1 $LAEUFE/*/TEILWEISE" | sed 's/^/        Versuch: /'
      pruefe "--pruefen meldet die gescheiterte Rücknahme" enthaelt "$(drin "$H --pruefen" 2>&1 || true)" "RÜCKNAHME GESCHEITERT"
      pruefe "schutz-status und der Bericht für das Programm melden sie" enthaelt "$(drin "$H status; cat /var/lib/ju-serverschutz/status.json" 2>&1 || true)" '"detail": "Rücknahme gescheitert'
      drin 'rm /etc/ssh/sshd_config.d/zz-rig-kaputt.conf'
      warte_bis $((soll + 140))
      pruefe "der dritte Versuch hat zurückgenommen" drin "! nft list table inet ju_schutz >/dev/null 2>&1 && ! ls $LAEUFE/*/RUECKNAHME $LAEUFE/*/TEILWEISE >/dev/null 2>&1" ;;
    *) echo "unbekannte Szene: $szene" >&2; exit 1 ;;
  esac
  drin "rm -rf $LAEUFE"   # jede Szene beginnt ohne frühere Läufe
done
echo; if [ "$FEHLER" -eq 0 ]; then echo "Alle Szenen bestanden."; else echo "MINDESTENS EINE PRÜFUNG IST GESCHEITERT."; fi
