#!/usr/bin/env bash
# JU Hamburg Finanzen – Keycloak (Anmeldung mit Zwei-Faktor) auf demselben Ubuntu-24.04-Server einrichten.
#
# Aufruf über den Verwaltungsbefehl (idempotent, beliebig oft wiederholbar):
#   sudo ju-finanzen keycloak-einrichten
#
# Was das Skript tut:
#   - Systempakete openjdk-21-jre-headless und postgresql (Sicherheitsupdates über apt)
#   - PostgreSQL: Rolle und Datenbank „keycloak“ (Passwort zufällig, Verbindung nur über 127.0.0.1)
#   - Keycloak $KC_VERSION nach /opt/keycloak/<Version> (nur mit passender SHA-256), Verweis /opt/keycloak/current,
#     Programm gehört root; beschreibbar ist nur /var/lib/keycloak (= data/), Dienst ju-keycloak als Benutzer keycloak
#   - Anmeldeseiten im Aussehen der Anwendung: server/keycloak-theme/ju-hamburg nach /opt/keycloak/<Version>/themes
#   - Realm „ju-hamburg“ (mit loginTheme) und Client „ju-verwaltung“ (kcadm.sh gegen http://127.0.0.1:8180/auth)
#   - Werte JU_FINANZEN_KEYCLOAK_* in /etc/ju-finanzen/ju-finanzen.env (JU_FINANZEN_ANMELDUNG bleibt unverändert)
#   - Tailscale Serve: nur /auth/realms/ju-hamburg und /auth/resources (keine Admin-Konsole, kein master-Realm)
#   - tägliche Sicherung der Keycloak-Datenbank (Timer ju-keycloak-sicherung)
#
# Die Funktionen nutzt auch tests/keycloak_probe.sh (dieselbe Einrichtung ohne systemd und Tailscale).
set -euo pipefail
umask 022

KC_VERSION=26.8.2
KC_SHA256=dd124787283918bc597628887168333465ee2a811a389e0003569e52be7e487f
KC_URL="https://github.com/keycloak/keycloak/releases/download/$KC_VERSION/keycloak-$KC_VERSION.tar.gz"
KC_ROOT=/opt/keycloak
KC_DATA=/var/lib/keycloak
KC_USER=keycloak
KC_PORT=8180
KC_HEALTH=http://127.0.0.1:9000/auth/health/ready   # Management-Schnittstelle (erbt http-host 127.0.0.1)
KC_REALM=ju-hamburg
KC_CLIENT=ju-verwaltung
KC_THEME=ju-hamburg
KC_ADMIN=ju-einrichtung
ETC=/etc/ju-finanzen
ENV_FILE=$ETC/ju-finanzen.env
ADMIN_FILE=$ETC/keycloak-admin.env
BOOTSTRAP_FILE=/run/ju-keycloak-bootstrap.env       # nur für den ersten Start, danach gelöscht
SERVICE=ju-keycloak

say()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\nFEHLER: %s\n' "$*" >&2; exit 1; }

zufall() { od -An -N24 -tx1 /dev/urandom | tr -d ' \n'; }   # 48 Hex-Zeichen, sicher in SQL, sed und Env-Dateien

# Datei Schlüssel Wert – Zeile ersetzen bzw. anhängen; Besitzer und Rechte der Datei bleiben
env_setzen() {
  local file="$1" key="$2" value="$3" tmp
  [[ "$key" =~ ^[A-Z_]+$ && "$value" != *$'\n'* ]] || die "Ungültiger Eintrag für $key."
  tmp="$(mktemp "$file.XXXXXX")"
  { grep -v "^$key=" "$file" || true; printf '%s=%s\n' "$key" "$value"; } > "$tmp"
  chown --reference="$file" "$tmp"; chmod --reference="$file" "$tmp"
  mv -f "$tmp" "$file"
}

benutzer_anlegen() {
  id "$KC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$KC_DATA" --no-create-home --shell /usr/sbin/nologin --user-group "$KC_USER"
}

# Passwort – Rolle und Datenbank „keycloak“ anlegen bzw. das Passwort setzen; das Passwort steht nur in stdin, nie in argv
datenbank_einrichten() {
  (cd / && runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1) <<SQL
SELECT 'CREATE ROLE keycloak LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keycloak')\gexec
ALTER ROLE keycloak WITH LOGIN PASSWORD '$1';
SELECT 'CREATE DATABASE keycloak OWNER keycloak ENCODING ''UTF8'' TEMPLATE template0' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
SQL
}

# Basisordner Ablage – Keycloak nach <Basis>/<Version>; Abbruch, wenn die Prüfsumme nicht stimmt
kc_installieren() {
  local home="$1/$KC_VERSION" archiv="$2/keycloak-$KC_VERSION.tar.gz"
  [ -x "$home/bin/kc.sh" ] && return 0
  if [ ! -f "$archiv" ]; then
    info "Keycloak $KC_VERSION herunterladen"
    curl -fsSL --proto '=https' -o "$archiv.neu" "$KC_URL" || die "Download fehlgeschlagen: $KC_URL"
    mv -f "$archiv.neu" "$archiv"
  fi
  printf '%s  %s\n' "$KC_SHA256" "$archiv" | sha256sum -c --quiet - >/dev/null 2>&1 \
    || { rm -f "$archiv"; die "Prüfsumme von keycloak-$KC_VERSION.tar.gz stimmt nicht – nichts installiert."; }
  install -d -m 755 "$1"
  rm -rf -- "$home.neu"; mkdir "$home.neu"
  tar -xzf "$archiv" -C "$home.neu" --strip-components=1 --no-same-owner
  mv -T "$home.neu" "$home"
}

# Konfigdatei Adresse DB-Passwort
kc_konfiguration() {
  local tmp
  tmp="$(mktemp "$1.XXXXXX")"
  cat > "$tmp" <<EOF
# JU Hamburg Finanzen – geschrieben von server/keycloak-einrichten.sh (wird bei jedem Lauf überschrieben)
db=postgres
db-url-host=127.0.0.1
db-url-database=keycloak
db-username=keycloak
db-password=$3
# ein einzelner Server: kein Cluster (sonst öffnet Infinispan/JGroups weitere Ports)
cache=local
health-enabled=true
http-enabled=true
http-host=127.0.0.1
http-port=$KC_PORT
http-relative-path=/auth
hostname=$2/auth
hostname-backchannel-dynamic=true
# Admin-Konsole nur über 127.0.0.1 (SSH-Tunnel); Tailscale Serve veröffentlicht sie nicht
hostname-admin=http://127.0.0.1:$KC_PORT/auth
proxy-headers=xforwarded
proxy-trusted-addresses=127.0.0.1
EOF
  chown root:"$KC_USER" "$tmp"; chmod 640 "$tmp"; mv -f "$tmp" "$1"
}

# Programmordner Quellordner(server/) – Anmeldeseiten im Aussehen der Anwendung nach themes/ (gehört root, nur lesbar).
# Nur bei angehaltenem Keycloak aufrufen: Vorlagen und Texte liest Keycloak beim Start (Theme-Cache).
theme_installieren() {
  local ziel="$1/themes/$KC_THEME" h
  [ -f "$2/keycloak-theme/$KC_THEME/login/theme.properties" ] || die "Theme fehlt: $2/keycloak-theme/$KC_THEME"
  rm -rf -- "$ziel"
  cp -R --no-preserve=all "$2/keycloak-theme/$KC_THEME" "$ziel"
  # Browser behalten Theme-Dateien 30 Tage, und die Adresse ändert sich nur mit der Keycloak-Version – daher die Prüfsumme anhängen
  h="$(cat "$ziel"/login/resources/css/* "$ziel"/login/resources/img/* | sha256sum | cut -c1-8)"
  sed -i "s|css/$KC_THEME\.css|&?v=$h|" "$ziel/login/theme.properties"
  sed -i "s|img/logo\.svg|&?v=$h|" "$ziel/login/resources/css/$KC_THEME.css"
  chown -R root:root "$ziel"; chmod -R u=rwX,go=rX "$ziel"
  [ ! -L "$KC_DATA/tmp" ] || die "$KC_DATA/tmp ist ein Verweis – bitte prüfen."
  rm -rf -- "$KC_DATA/tmp/kc-gzip-cache"   # gepackte Theme-Dateien hebt Keycloak über Neustarts auf – sonst käme das alte CSS
}

# Programmordner Datenordner – Build mit der Konfiguration; danach gehört alles root, data/ zeigt auf den Datenordner
kc_bauen() {
  local log
  log="$(mktemp)"
  rm -rf -- "$1/data"   # Verweis auf den Datenordner entfernen: der Build läuft als root und soll dort nichts anlegen
  "$1/bin/kc.sh" build > "$log" 2>&1 || { tail -n 30 "$log" >&2; rm -f "$log"; die "kc.sh build fehlgeschlagen."; }
  rm -f "$log"
  rm -rf -- "$1/data"
  chown -R root:root "$1"; chmod -R go-w "$1"
  chown root:"$KC_USER" "$1/conf/keycloak.conf"; chmod 640 "$1/conf/keycloak.conf"
  ln -sfn "$2" "$1/data"
}

kc_warten() {
  for _ in $(seq 1 180); do
    curl -fsS -m 2 -o /dev/null "$KC_HEALTH" 2>/dev/null && return 0
    sleep 1
  done
  return 1
}

kc() { "$KCADM" "$@" --config "${WORK:?}/kcadm.config"; }
kc_anmelden() {   # Admin-Passwort aus KC_CLI_PASSWORD (Umgebung, nie in der Befehlszeile)
  kc config credentials --server "http://127.0.0.1:$KC_PORT/auth" --realm master --user "$KC_ADMIN" >/dev/null 2>&1 \
    || die "Anmeldung als $KC_ADMIN an Keycloak fehlgeschlagen (Passwort in $ADMIN_FILE passt nicht). Siehe SERVER.md, Keycloak → Notzugang."
}

# Adresse Secret-Datei – Realm und Client einrichten (idempotent). Erwartet KCADM, WORK und das Admin-Passwort in
# KC_CLI_PASSWORD. Das Client-Secret landet nur in der Secret-Datei (0600).
realm_einrichten() {
  local adresse="$1" r="$KC_REALM" id scope rm x koerper="" rollen=(manage-users view-users query-users)
  kc_anmelden
  kc get "realms/$r" --fields realm >/dev/null 2>&1 || kc create realms -s realm="$r" -s enabled=true
  # brute force: wie die Anmeldung des Programms 5 Fehlversuche, Wartezeit wachsend bis 15 Minuten (Standard)
  kc update "realms/$r" -s enabled=true -s displayName='JU Hamburg' -s loginTheme="$KC_THEME" \
    -s internationalizationEnabled=true -s 'supportedLocales=["de"]' -s defaultLocale=de \
    -s registrationAllowed=false -s resetPasswordAllowed=false -s rememberMe=false \
    -s bruteForceProtected=true -s failureFactor=5 \
    -s 'passwordPolicy=length(12) and notUsername and passwordHistory(3)' \
    -s otpPolicyType=totp -s otpPolicyAlgorithm=HmacSHA1 -s otpPolicyDigits=6 -s otpPolicyPeriod=30 \
    -s ssoSessionIdleTimeout=1800 -s ssoSessionMaxLifespan=36000
  # Account-Konsole sperren: niemand soll seine Zwei-Faktor-Anmeldung selbst entfernen können
  kc remove-roles -r "$r" --rname "default-roles-$r" --cclientid account --rolename manage-account --rolename view-profile
  # Pflichtschritte nur Passwort ändern und App einrichten: jeder andere ließe sich über kc_action in der Anmelde-Adresse
  # selbst anstoßen (z. B. delete_credential = App entfernen, UPDATE_PROFILE = Name/E-Mail ändern)
  kc get authentication/required-actions -r "$r" --fields alias,enabled --format csv --noquotes | while IFS=, read -r a an; do
    case "$a" in UPDATE_PASSWORD|CONFIGURE_TOTP) soll=true ;; *) soll=false ;; esac
    [ "$an" = "$soll" ] || kc update "authentication/required-actions/$a" -r "$r" -s enabled="$soll"
  done
  # Passwort-Grant (Direct Access) bei keinem Client des Realms – auch nicht bei admin-cli (Passwortprüfung ohne App)
  kc get clients -r "$r" --fields id,directAccessGrantsEnabled --format csv --noquotes | while IFS=, read -r c an; do
    [ "$an" = false ] || kc update "clients/$c" -r "$r" -s directAccessGrantsEnabled=false
  done

  id="$(kc get clients -r "$r" -q clientId="$KC_CLIENT" --fields id --format csv --noquotes)"
  [ -n "$id" ] || id="$(kc create clients -r "$r" -s clientId="$KC_CLIENT" -i)"
  kc update "clients/$id" -r "$r" -s enabled=true -s protocol=openid-connect -s publicClient=false -s bearerOnly=false \
    -s standardFlowEnabled=true -s directAccessGrantsEnabled=false -s implicitFlowEnabled=false -s serviceAccountsEnabled=true \
    -s "redirectUris=[\"$adresse/api/keycloak/rueckkehr\"]" -s 'webOrigins=[]' -s "baseUrl=$adresse/" \
    -s 'attributes."pkce.code.challenge.method"=S256' -s "attributes.\"post.logout.redirect.uris\"=$adresse/"
  kc add-roles -r "$r" --uusername "service-account-$KC_CLIENT" --cclientid realm-management \
    --rolename "${rollen[0]}" --rolename "${rollen[1]}" --rolename "${rollen[2]}"
  # „Full scope allowed“ aus (veraltet, Keycloak warnt bei jeder Anmeldung): Tokens tragen nur noch die Rollen, die hier
  # dem Client zugeordnet sind – die der Nutzerverwaltung, Anmelde-Tokens von Personen gar keine. Erst zuordnen, dann
  # abschalten: sonst bekäme die Nutzerverwaltung dazwischen 403, und Anmeldungen mit App-Pflicht scheiterten
  rm="$(kc get clients -r "$r" -q clientId=realm-management --fields id --format csv --noquotes)"
  [ -n "$rm" ] || die "Client realm-management fehlt im Realm $r."
  for x in "${rollen[@]}"; do koerper+="${koerper:+,}$(kc get "clients/$rm/roles/$x" -r "$r" --fields id,name)"; done
  printf '[%s]' "$koerper" | kc create "clients/$id/scope-mappings/clients/$rm" -r "$r" -f -
  kc update "clients/$id" -r "$r" -s fullScopeAllowed=false
  # die Admin-API (Keycloak 26.8) nimmt nur Tokens mit dem Scope kc-admin-api (Audience <Adresse>/auth/admin/realms/…)
  scope="$(kc get client-scopes -r "$r" --fields id,name --format csv --noquotes | sed -n 's/,kc-admin-api$//p')"
  [ -n "$scope" ] || die "Client-Scope kc-admin-api fehlt im Realm $r."
  kc update "clients/$id/default-client-scopes/$scope" -r "$r"
  (umask 077; kc get "clients/$id/client-secret" -r "$r" --fields value --format csv --noquotes > "$2")
  [ -s "$2" ] || die "Client-Secret von $KC_CLIENT nicht lesbar."
}

# Env-Datei Adresse Secret-Datei – Werte für das Programm (JU_FINANZEN_ANMELDUNG bleibt, wie sie ist)
werte_eintragen() {
  env_setzen "$1" JU_FINANZEN_KEYCLOAK_ISSUER "$2/auth/realms/$KC_REALM"
  env_setzen "$1" JU_FINANZEN_KEYCLOAK_INTERN "http://127.0.0.1:$KC_PORT/auth/realms/$KC_REALM"
  env_setzen "$1" JU_FINANZEN_KEYCLOAK_CLIENT "$KC_CLIENT"
  env_setzen "$1" JU_FINANZEN_KEYCLOAK_SECRET "$(cat "$3")"
  env_setzen "$1" JU_FINANZEN_KEYCLOAK_RUECKKEHR "$2/api/keycloak/rueckkehr"
}

main() {
  case "${1:-}" in -h|--help) sed -n '2,18p' "$0"; exit 0 ;; "") ;; *) die "Unbekannte Option: $1 (Hilfe: --help)" ;; esac
  [ "$(id -u)" -eq 0 ] || die "Bitte mit sudo ausführen: sudo ju-finanzen keycloak-einrichten"
  command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] || die "systemd läuft hier nicht."
  [ -f "$ENV_FILE" ] || die "$ENV_FILE fehlt – zuerst das Programm installieren (server/install.sh)."
  local src adresse a db_pw before
  src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # erste https-Adresse aus JU_FINANZEN_ADRESSEN (die Adresse von Tailscale Serve)
  adresse=""
  IFS=, read -ra list <<< "$(sed -n 's/^JU_FINANZEN_ADRESSEN=//p' "$ENV_FILE" | tail -n 1 | tr -d '\r ')"
  for a in "${list[@]}"; do case "$a" in https://*) adresse="${a%/}"; break ;; esac; done
  [[ "$adresse" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] \
    || die "Keine https-Adresse in JU_FINANZEN_ADRESSEN ($ENV_FILE) – zuerst server/install.sh --tailscale."
  WORK="$(mktemp -d)"
  trap 'rm -rf -- "$WORK"; rm -f -- "$BOOTSTRAP_FILE"' EXIT

  say "Keycloak $KC_VERSION für $adresse einrichten"
  local pakete=()
  for p in openjdk-21-jre-headless postgresql; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || pakete+=("$p")
  done
  if [ "${#pakete[@]}" -gt 0 ]; then
    info "Systempakete installieren: ${pakete[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${pakete[@]}" >/dev/null
  fi
  systemctl enable --now --quiet postgresql

  say "Datenbank und Programm"
  benutzer_anlegen
  install -d -m 700 -o "$KC_USER" -g "$KC_USER" "$KC_DATA"
  kc_installieren "$KC_ROOT" "$WORK"
  local home="$KC_ROOT/$KC_VERSION"
  db_pw="$(sed -n 's/^db-password=//p' "$KC_ROOT/current/conf/keycloak.conf" 2>/dev/null || true)"
  [[ "$db_pw" =~ ^[0-9a-f]{48}$ ]] || db_pw="$(zufall)"
  datenbank_einrichten "$db_pw"
  before="$(readlink "$KC_ROOT/current" 2>/dev/null || true)"
  if [ -n "$before" ] && [ "$before" != "$KC_VERSION" ] && systemctl is-active --quiet "$SERVICE"; then
    info "Neue Keycloak-Version ($before → $KC_VERSION): Datenbank vorher sichern"
    /usr/local/bin/ju-finanzen keycloak-sicherung
  fi
  [ -f "$src/keycloak-theme/$KC_THEME/login/theme.properties" ] || die "Theme fehlt: $src/keycloak-theme/$KC_THEME"
  systemctl stop "$SERVICE" 2>/dev/null || true
  kc_konfiguration "$home/conf/keycloak.conf" "$adresse" "$db_pw"
  theme_installieren "$home" "$src"
  info "kc.sh build (dauert etwa eine Minute)"
  kc_bauen "$home" "$KC_DATA"
  ln -sfn "$KC_VERSION" "$KC_ROOT/current.neu" && mv -Tf "$KC_ROOT/current.neu" "$KC_ROOT/current"

  say "Dienst $SERVICE starten"
  install -m 644 -o root -g root "$src/ju-keycloak.service" "/etc/systemd/system/$SERVICE.service"
  install -m 644 -o root -g root "$src/ju-keycloak-sicherung.service" "$src/ju-keycloak-sicherung.timer" /etc/systemd/system/
  if [ ! -f "$ADMIN_FILE" ]; then
    # erster Admin im master-Realm: nur für diese Einrichtung und kcadm; legt Keycloak nur beim Anlegen des master-Realms an
    (umask 077; printf 'KC_BOOTSTRAP_ADMIN_USERNAME=%s\nKC_BOOTSTRAP_ADMIN_PASSWORD=%s\n' "$KC_ADMIN" "$(zufall)" > "$ADMIN_FILE.neu")
    mv -f "$ADMIN_FILE.neu" "$ADMIN_FILE"
  fi
  chown root:root "$ADMIN_FILE"; chmod 600 "$ADMIN_FILE"
  # nur solange es den master-Realm noch nicht gibt (auch nach einem abgebrochenen ersten Lauf)
  if [ "$(cd / && runuser -u postgres -- psql -X -tAc "SELECT 1 FROM realm WHERE name = 'master'" keycloak 2>/dev/null || true)" != 1 ]; then
    install -m 600 "$ADMIN_FILE" "$BOOTSTRAP_FILE"
  fi
  systemctl daemon-reload
  systemctl enable --quiet "$SERVICE" ju-keycloak-sicherung.timer
  systemctl start ju-keycloak-sicherung.timer
  systemctl restart "$SERVICE"
  kc_warten || die "Keycloak startet nicht. Protokoll: sudo journalctl -u $SERVICE -n 80"
  rm -f -- "$BOOTSTRAP_FILE"
  info "Keycloak läuft (127.0.0.1:$KC_PORT/auth)."

  say "Realm $KC_REALM und Client $KC_CLIENT"
  KCADM="$home/bin/kcadm.sh"
  KC_CLI_PASSWORD="$(sed -n 's/^KC_BOOTSTRAP_ADMIN_PASSWORD=//p' "$ADMIN_FILE")" realm_einrichten "$adresse" "$WORK/secret"
  werte_eintragen "$ENV_FILE" "$adresse" "$WORK/secret"
  info "Werte JU_FINANZEN_KEYCLOAK_* in $ENV_FILE eingetragen (JU_FINANZEN_ANMELDUNG unverändert)."

  if command -v tailscale >/dev/null 2>&1; then
    say "Tailscale Serve: nur Anmeldeseiten von Keycloak (keine Admin-Konsole)"
    # --set-path: Tailscale schneidet den Pfad ab und hängt den Rest an das Ziel – daher steht der Pfad im Ziel noch einmal
    for p in "/auth/realms/$KC_REALM" /auth/resources; do
      tailscale serve --bg --https=443 --set-path "$p" "http://127.0.0.1:$KC_PORT$p" >/dev/null \
        || die "Tailscale Serve für $p nicht eingerichtet. Von Hand: sudo tailscale serve --bg --https=443 --set-path $p http://127.0.0.1:$KC_PORT$p"
    done
    info "$adresse/auth/realms/$KC_REALM und /auth/resources → Keycloak; alles andere weiter → Programm."
  else
    info "Tailscale fehlt – Serve-Regeln nicht eingerichtet (SERVER.md, Keycloak)."
  fi

  say "Fertig: Keycloak $KC_VERSION"
  info "Status:  sudo ju-finanzen keycloak-status"
  info "Das Programm liest die neuen Werte erst nach: sudo ju-finanzen neustart"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
