#!/usr/bin/env bash
# Probe: Keycloak mit genau der Einrichtung aus server/keycloak-einrichten.sh (dieselben Funktionen), ohne systemd und
# Tailscale, auf 127.0.0.1:8180/auth mit hostname=<KC_PROBE_ADRESSE>/auth.
#
#   sudo tests/keycloak_probe.sh            alles: aufbauen, starten, einrichten (zweimal), prüfen, stoppen
#   sudo tests/keycloak_probe.sh start      aufbauen (nur beim ersten Mal), starten, einrichten – läuft weiter
#   sudo tests/keycloak_probe.sh pruefen    Prüfungen gegen die laufende Instanz
#   sudo tests/keycloak_probe.sh stop       Keycloak und PostgreSQL anhalten
#   sudo tests/keycloak_probe.sh zuruecksetzen   Datenbank „keycloak“ und Installation löschen (Download bleibt)
#
# KC_PROBE_DIR      Ordner für Download, Installation, Protokoll (Standard: ${TMPDIR:-/tmp}/ju-keycloak-probe). Darin nach
#                   „start“: client-secret (0600) und ju-finanzen.env (JU_FINANZEN_KEYCLOAK_* für das Programm).
# KC_PROBE_ADRESSE  Adresse des Programms (Standard https://srv1.example.ts.net)
# Braucht root, PostgreSQL 16 und Java 21 aus Ubuntu-Paketen, curl, python3. Ins Netz nur für den ersten Download.
# Nutzt Rolle und Datenbank „keycloak“ des lokalen PostgreSQL-Clusters – je Rechner nur ein Probe-Ordner zugleich.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=server/keycloak-einrichten.sh
. "$REPO/server/keycloak-einrichten.sh"

P="${KC_PROBE_DIR:-${TMPDIR:-/tmp}/ju-keycloak-probe}"
ADRESSE="${KC_PROBE_ADRESSE:-https://srv1.example.ts.net}"
KC_ROOT="$P/opt"; KC_DATA="$P/data"; ADMIN_FILE="$P/keycloak-admin.env"; WORK="$P/work"
# shellcheck disable=SC2034  # KCADM, ADMIN_FILE, WORK nutzen die Funktionen aus keycloak-einrichten.sh
KC_HOME="$KC_ROOT/$KC_VERSION"; KCADM="$KC_HOME/bin/kcadm.sh"; LOG="$P/keycloak.log"
unset JAVA_TOOL_OPTIONS   # wie auf dem Server: keine Java-Vorgaben aus der Umgebung

[ "$(id -u)" -eq 0 ] || die "Bitte als root ausführen."
install -d -m 711 "$P"; install -d -m 700 "$P/download" "$WORK"   # 711: der Benutzer keycloak muss zu opt/ und data/ durchgehen

laeuft() { pgrep -f "kc.home.dir=$KC_ROOT" >/dev/null; }

pg() {   # start|stop – der Ubuntu-Cluster (wie auf dem Server, dort über systemd)
  local c
  c="$(pg_lsclusters -h | awk 'NR == 1 { print $1 " " $2 }')"
  [ -n "$c" ] || die "Kein PostgreSQL-Cluster (apt install postgresql)."
  # shellcheck disable=SC2086
  pg_ctlcluster $c "$1" 2>/dev/null || [ "$1" = stop ] || pg_ctlcluster $c status >/dev/null
}

aufbauen() {
  benutzer_anlegen
  install -d -m 700 -o "$KC_USER" -g "$KC_USER" "$KC_DATA"
  kc_installieren "$KC_ROOT" "$P/download"
  pg start
  local db_pw
  db_pw="$(sed -n 's/^db-password=//p' "$KC_HOME/conf/keycloak.conf" 2>/dev/null || true)"
  [[ "$db_pw" =~ ^[0-9a-f]{48}$ ]] || db_pw="$(zufall)"
  datenbank_einrichten "$db_pw"
  kc_konfiguration "$KC_HOME/conf/keycloak.conf" "$ADRESSE" "$db_pw"   # hostname ist eine Laufzeit-Option
  theme_installieren "$KC_HOME" "$REPO/server"
  if [ ! -f "$P/.gebaut-$KC_VERSION" ]; then
    say "kc.sh build"
    kc_bauen "$KC_HOME" "$KC_DATA"
    touch "$P/.gebaut-$KC_VERSION"
  fi
  ln -sfn "$KC_VERSION" "$KC_ROOT/current"
  [ -f "$ADMIN_FILE" ] || (umask 077; printf 'KC_BOOTSTRAP_ADMIN_USERNAME=%s\nKC_BOOTSTRAP_ADMIN_PASSWORD=%s\n' "$KC_ADMIN" "$(zufall)" > "$ADMIN_FILE")
}

stoppen() {
  if laeuft; then
    pkill -TERM -f "kc.home.dir=$KC_ROOT" || true
    for _ in $(seq 1 60); do laeuft || break; sleep 1; done
    laeuft && pkill -KILL -f "kc.home.dir=$KC_ROOT" || true
  fi
  pg stop
  info "Keycloak und PostgreSQL angehalten."
}

starten() {
  laeuft && stoppen
  aufbauen
  say "Keycloak starten ($ADRESSE)"
  local als=() wer=root   # als Benutzer keycloak, wenn er den Ordner erreicht (sonst root; geprüft wird trotzdem, wohin geschrieben wird)
  if runuser -u "$KC_USER" -- test -x "$KC_ROOT/current/bin/kc.sh" 2>/dev/null; then als=(runuser -u "$KC_USER" --); wer="Benutzer $KC_USER"; fi
  touch "$P/.start"
  # shellcheck disable=SC1090
  ( set -a; . "$ADMIN_FILE"; set +a
    export JAVA_OPTS_KC_HEAP="-Xms256m -Xmx768m"
    cd / && exec setsid "${als[@]}" "$KC_ROOT/current/bin/kc.sh" start --optimized ) > "$LOG" 2>&1 < /dev/null &
  kc_warten || { tail -n 40 "$LOG" >&2; die "Keycloak startet nicht."; }
  info "läuft als $wer: http://127.0.0.1:$KC_PORT/auth"
  einrichten
}

einrichten() {
  say "Realm und Client einrichten (Funktion aus server/keycloak-einrichten.sh)"
  KC_CLI_PASSWORD="$(sed -n 's/^KC_BOOTSTRAP_ADMIN_PASSWORD=//p' "$ADMIN_FILE")" realm_einrichten "$ADRESSE" "$P/client-secret"
  [ -f "$P/ju-finanzen.env" ] || install -m 600 /dev/null "$P/ju-finanzen.env"
  werte_eintragen "$P/ju-finanzen.env" "$ADRESSE" "$P/client-secret"
  info "Client-Secret: $P/client-secret   Werte für das Programm: $P/ju-finanzen.env"
}

pruefen() {
  laeuft || die "Keycloak läuft nicht (tests/keycloak_probe.sh start)."
  say "Prüfungen"
  KC_CLI_PASSWORD="$(sed -n 's/^KC_BOOTSTRAP_ADMIN_PASSWORD=//p' "$ADMIN_FILE")" kc_anmelden
  kc get "realms/$KC_REALM" > "$WORK/realm.json"
  kc get clients -r "$KC_REALM" -q clientId="$KC_CLIENT" > "$WORK/client.json"
  kc get "roles/default-roles-$KC_REALM/composites" -r "$KC_REALM" > "$WORK/default-roles.json"
  kc get authentication/required-actions -r "$KC_REALM" > "$WORK/required-actions.json"
  kc get clients -r "$KC_REALM" --fields clientId,directAccessGrantsEnabled > "$WORK/clients.json"
  # Theme gehört root und ist nur für root schreibbar (leere Ausgabe = in Ordnung)
  find "$KC_HOME/themes/$KC_THEME" \( ! -user root -o -perm /022 \) -print > "$WORK/theme-rechte"
  # seit dem Start nichts außerhalb von data/ geschrieben
  find -L "$KC_HOME" -path "$KC_HOME/data" -prune -o -newer "$P/.start" -print > "$WORK/geschrieben"
  grep -c 'full-scope-allowed' "$LOG" > "$WORK/full-scope" || true
  python3 -I - "$ADRESSE" "$P/client-secret" "$WORK" <<'PY'
import base64, json, pathlib, re, secrets, sys, urllib.error, urllib.parse, urllib.request

adresse, secret_file, work = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
intern = 'http://127.0.0.1:8180/auth'
realm = f'{intern}/realms/ju-hamburg'
issuer = f'{adresse}/auth/realms/ju-hamburg'
secret = pathlib.Path(secret_file).read_text().strip()
rueckkehr = f'{adresse}/api/keycloak/rueckkehr'
fehler = []

class KeinUmleiten(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

http = urllib.request.build_opener(urllib.request.ProxyHandler({}), KeinUmleiten())

def call(method, url, data=None, token=None, form=False, auth=None):
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if auth:
        headers['Authorization'] = 'Basic ' + base64.b64encode(auth.encode()).decode()
    if data is not None:
        if form:
            data = urllib.parse.urlencode(data).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
    try:
        with http.open(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=20) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()

def check(name, ok, detail=''):
    print(f"    {'OK    ' if ok else 'FEHLER'} {name}{' – ' + detail if detail and not ok else ''}")
    if not ok:
        fehler.append(name)

# 1. Discovery über 127.0.0.1: Issuer ist die öffentliche Adresse, Backchannel bleibt intern
s, _, body = call('GET', f'{realm}/.well-known/openid-configuration')
d = json.loads(body)
check('Discovery: issuer = öffentliche Adresse', d.get('issuer') == issuer, d.get('issuer', ''))
check('Discovery: authorization_endpoint öffentlich', d['authorization_endpoint'].startswith(issuer + '/'))
check('Discovery: token_endpoint intern (backchannel-dynamic)', d['token_endpoint'].startswith(realm + '/'), d['token_endpoint'])
check('Discovery: PKCE S256 angeboten', 'S256' in d.get('code_challenge_methods_supported', []))

# 2. Client-Credentials-Token für ju-verwaltung
s, _, body = call('POST', d['token_endpoint'], {'grant_type': 'client_credentials'}, form=True, auth=f'ju-verwaltung:{secret}')
token = json.loads(body).get('access_token', '') if s == 200 else ''
check('Client-Credentials-Token', bool(token), f'Status {s}')
claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==')) if token else {}
check('Token: iss = öffentliche Adresse', claims.get('iss') == issuer, str(claims.get('iss')))
check('Token: azp = ju-verwaltung', claims.get('azp') == 'ju-verwaltung')
rollen = {k: sorted(v.get('roles', [])) for k, v in claims.get('resource_access', {}).items()}
# query-groups steckt in view-users (zusammengesetzte Rolle) und kommt deshalb mit
check('Token: nur die Rollen der Nutzerverwaltung (Full scope allowed aus)', not claims.get('realm_access')
      and rollen == {'realm-management': ['manage-users', 'query-groups', 'query-users', 'view-users']}, f"{claims.get('realm_access')} {rollen}")
s, _, body = call('POST', d['token_endpoint'], {'grant_type': 'password', 'username': 'x', 'password': 'y'}, form=True,
                  auth=f'ju-verwaltung:{secret}')
check('Passwort-Grant (Direct Access) abgelehnt', s in (400, 401) and b'unauthorized_client' in body, f'Status {s}')

# 3. Service-Account: Testbenutzer anlegen, lesen, Passwortrichtlinie, löschen; mehr Rechte hat er nicht
admin = f'{intern}/admin/realms/ju-hamburg'
name = 'probe-' + secrets.token_hex(4)
s, h, _ = call('POST', f'{admin}/users', {'username': name, 'enabled': True, 'requiredActions': ['CONFIGURE_TOTP']}, token=token)
check('Service-Account legt Benutzer an', s == 201, f'Status {s}')
uid = h.get('Location', '').rsplit('/', 1)[-1]
s, _, body = call('GET', f'{admin}/users?' + urllib.parse.urlencode({'username': name, 'exact': 'true'}), token=token)
found = json.loads(body) if s == 200 else []
check('Service-Account liest Benutzer', len(found) == 1 and found[0]['id'] == uid, f'Status {s}')
check('Benutzer muss TOTP einrichten (Required Action)', bool(found) and 'CONFIGURE_TOTP' in found[0].get('requiredActions', []))
def passwort(wert):
    s, _, body = call('PUT', f'{admin}/users/{uid}/reset-password', {'type': 'password', 'value': wert, 'temporary': False}, token=token)
    return s, body.decode('utf-8', 'replace')

s, body = passwort('kurz')
check('Passwortrichtlinie: zu kurz abgelehnt', s == 400 and 'invalidPasswordMinLength' in body, f'{s} {body[:80]}')
s, body = passwort(name + 'x' * 12)
check('Passwortrichtlinie: lang genug angenommen', s == 204, f'{s} {body[:80]}')
s, body = passwort(name)
check('Passwortrichtlinie: Benutzername abgelehnt', s == 400 and 'invalidPassword' in body, f'{s} {body[:80]}')
s, body = passwort(name + 'x' * 12)
check('Passwortrichtlinie: letztes Passwort wiederholen abgelehnt (passwordHistory)', s == 400 and 'invalidPasswordHistory' in body,
      f'{s} {body[:80]}')
call('PUT', f'{admin}/users/{uid}', {'requiredActions': []}, token=token)   # sonst scheitert der Grant schon am Pflichtschritt
for cid in ('admin-cli', 'account-console'):   # öffentliche Standard-Clients des Realms (ju-verwaltung: oben)
    s, _, body = call('POST', d['token_endpoint'], {'grant_type': 'password', 'client_id': cid, 'username': name,
                      'password': name + 'x' * 12}, form=True)
    check(f'Passwort-Grant mit {cid} abgelehnt (unauthorized_client)', s in (400, 401) and b'unauthorized_client' in body, f'Status {s}')
s, _, _ = call('DELETE', f'{admin}/users/{uid}', token=token)
check('Service-Account löscht Benutzer', s == 204, f'Status {s}')
s, _, body = call('GET', f'{admin}/users?' + urllib.parse.urlencode({'username': name, 'exact': 'true'}), token=token)
check('Benutzer ist weg', s == 200 and json.loads(body) == [])
s1, _, _ = call('GET', f'{admin}/clients', token=token)
s2, _, _ = call('GET', f'{admin}/authentication/flows', token=token)
check('Service-Account sieht weder Clients noch Anmeldeabläufe', (s1, s2) == (403, 403), f'Status {s1}, {s2}')
s, _, _ = call('GET', f'{intern}/admin/realms/master/users', token=token)
check('Service-Account hat keinen Zugriff auf master', s in (401, 403), f'Status {s}')

# 4. Browser-Anmeldung: PKCE Pflicht, Redirect-URI genau, Seite deutsch
q = {'client_id': 'ju-verwaltung', 'response_type': 'code', 'redirect_uri': rueckkehr, 'scope': 'openid', 'state': 'probe'}
s, h, _ = call('GET', f'{realm}/protocol/openid-connect/auth?' + urllib.parse.urlencode(q))
loc = h.get('Location', '')
check('ohne PKCE: Fehler an die Rückkehr-Adresse', s == 302 and loc.startswith(rueckkehr + '?') and 'error=invalid_request' in loc
      and 'code_challenge' in urllib.parse.unquote_plus(loc), loc[:120])
s, _, _ = call('GET', f'{realm}/protocol/openid-connect/auth?' + urllib.parse.urlencode({**q, 'redirect_uri': rueckkehr + 'x',
               'code_challenge': 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM', 'code_challenge_method': 'S256'}))
check('fremde Redirect-URI abgelehnt', s == 400, f'Status {s}')
s, h, body = call('GET', f'{realm}/protocol/openid-connect/auth?' + urllib.parse.urlencode({**q,
                  'code_challenge': 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM', 'code_challenge_method': 'S256'}))
html = body.decode('utf-8', 'replace')
check('mit PKCE: Anmeldeseite', s == 200 and 'kc-form-login' in html, f'Status {s}')
check('Anmeldeseite deutsch, Titel JU Hamburg Verwaltung', 'lang="de"' in html and '<title>JU Hamburg Verwaltung</title>' in html)
check('Anmeldeseite ohne „Passwort vergessen“, Registrierung, Angemeldet bleiben',
      'login-actions/reset-credentials' not in html and 'registrations' not in html and 'rememberMe' not in html)
check('Ressourcen unter /auth/resources', '/auth/resources/' in html)

# 4a. Aussehen der Anwendung (Theme ju-hamburg): eigenes CSS und Logo, nur freigegebene Pfade, CSP wie von Keycloak
pfade = {p.removeprefix(adresse) for p in re.findall(r'(?:href|src|action)="([^"#][^"]*)"', html) + re.findall(r'"(/auth/[^"]+)"', html)}
fremd = sorted(p for p in pfade if not p.startswith(('/auth/resources/', '/auth/realms/ju-hamburg/')))
check('Anmeldeseite braucht nur /auth/resources und /auth/realms/ju-hamburg (Tailscale Serve)', bool(pfade) and not fremd, ', '.join(fremd)[:200])
check('Content-Security-Policy unverändert (Standard von Keycloak)',
      h.get('Content-Security-Policy') == "frame-src 'self'; frame-ancestors 'self'; object-src 'none';", str(h.get('Content-Security-Policy')))
css = re.search(r'href="(/auth/resources/[^"/]+/login/ju-hamburg)/(css/ju-hamburg\.css\?v=[0-9a-f]{8})"', html)
check('Theme: CSS der Anwendung eingebunden (mit Prüfsumme gegen alte Browser-Kopien)', bool(css))
if css:
    basis = 'http://127.0.0.1:8180' + css.group(1)
    s, h2, body = call('GET', f'{basis}/{css.group(2)}')
    check('Theme: CSS unter /auth/resources abrufbar', s == 200 and h2.get('Content-Type', '').startswith('text/css') and b'--ju-teal' in body, f'Status {s}')
    logo = re.search(rb"--keycloak-logo-url: url\('\.\./(img/logo\.svg\?v=[0-9a-f]{8})'\)", body)
    check('Theme: eigenes Logo statt Keycloak-Logo', bool(logo) and 'keycloak-logo' not in html)
    for datei in ((logo.group(1).decode() if logo else 'img/logo.svg'), 'img/favicon.svg'):
        s, h2, body = call('GET', f'{basis}/{datei}')
        check(f'Theme: {datei.split("?")[0]} unter /auth/resources abrufbar', s == 200 and h2.get('Content-Type', '').startswith('image/svg')
              and body.lstrip().startswith(b'<svg'), f'Status {s}')

s, _, body = call('GET', f'{intern}/admin/master/console/')
konsole = body.decode('utf-8', 'replace')
check('Admin-Konsole meldet an und verwaltet über 127.0.0.1 (hostname-admin, SSH-Tunnel)', s == 200
      and f'"authUrl": "{intern}"' in konsole and f'"adminBaseUrl": "{intern}"' in konsole, f'Status {s}')

# 5. Einstellungen (über kcadm gelesen)
r = json.loads((work / 'realm.json').read_text())
want = {'displayName': 'JU Hamburg', 'loginTheme': 'ju-hamburg', 'internationalizationEnabled': True, 'defaultLocale': 'de', 'registrationAllowed': False,
        'resetPasswordAllowed': False, 'rememberMe': False, 'bruteForceProtected': True, 'failureFactor': 5,
        'passwordPolicy': 'length(12) and notUsername and passwordHistory(3)', 'otpPolicyType': 'totp',
        'otpPolicyAlgorithm': 'HmacSHA1', 'otpPolicyDigits': 6, 'otpPolicyPeriod': 30, 'ssoSessionIdleTimeout': 1800,
        'ssoSessionMaxLifespan': 36000}
for k, v in want.items():
    check(f'Realm {k} = {v}', r.get(k) == v, repr(r.get(k)))
check('Realm: Browserflow = Standard „browser“', r.get('browserFlow') == 'browser')
aktiv = sorted(a['alias'] for a in json.loads((work / 'required-actions.json').read_text()) if a.get('enabled'))
check('Pflichtschritte: nur UPDATE_PASSWORD und CONFIGURE_TOTP aktiv', aktiv == ['CONFIGURE_TOTP', 'UPDATE_PASSWORD'], ', '.join(aktiv))
direkt = [x['clientId'] for x in json.loads((work / 'clients.json').read_text()) if x.get('directAccessGrantsEnabled')]
check('kein Client mit Direct Access Grants', direkt == [], ', '.join(direkt))
roles = {x['name'] for x in json.loads((work / 'default-roles.json').read_text())}
check('Default-Roles ohne manage-account/view-profile', not roles & {'manage-account', 'view-profile'}, ', '.join(sorted(roles)))
c = json.loads((work / 'client.json').read_text())[0]
for k, v in {'publicClient': False, 'standardFlowEnabled': True, 'directAccessGrantsEnabled': False, 'implicitFlowEnabled': False,
             'serviceAccountsEnabled': True, 'fullScopeAllowed': False, 'redirectUris': [rueckkehr], 'webOrigins': []}.items():
    check(f'Client {k} = {v}', c.get(k) == v, repr(c.get(k)))
check('Client PKCE S256 Pflicht', c['attributes'].get('pkce.code.challenge.method') == 'S256')
check('Client Logout-Rückkehr', c['attributes'].get('post.logout.redirect.uris') == adresse + '/')
check('Client baseUrl („Zurück zur Anwendung“ auf Fehlerseiten)', c.get('baseUrl') == adresse + '/')

# 6. Betrieb: nur 127.0.0.1, kein Cluster-Port (JGroups 7800), nichts außerhalb von data/ geschrieben
def lauschend():
    for datei in ('/proc/net/tcp', '/proc/net/tcp6'):
        if not pathlib.Path(datei).exists():   # ohne IPv6
            continue
        for zeile in pathlib.Path(datei).read_text().splitlines()[1:]:
            lokal, status = zeile.split()[1], zeile.split()[3]
            if status == '0A':   # LISTEN
                adr, port = lokal.split(':')
                ip = '.'.join(str(b) for b in reversed(bytes.fromhex(adr))) if len(adr) == 8 else adr
                yield f'{ip}:{int(port, 16)}'
offen = sorted({p for p in lauschend() if p.rsplit(':', 1)[1] in ('8180', '9000', '7800', '57800')})
check('8180 und 9000 nur auf 127.0.0.1, kein Cluster-Port', offen == ['127.0.0.1:8180', '127.0.0.1:9000'], ', '.join(offen))
geschrieben = (work / 'geschrieben').read_text().split()
check('nichts außerhalb von data/ geschrieben', geschrieben == [], ', '.join(geschrieben[:5]))
warnung = (work / 'full-scope').read_text().strip()
check('Protokoll ohne Warnung „Full scope allowed“', warnung == '0', f'{warnung} Zeilen')
rechte = (work / 'theme-rechte').read_text().split()
check('Theme gehört root, für andere nur lesbar', rechte == [], ', '.join(rechte[:5]))

print(f"\n    {'Alle Prüfungen bestanden.' if not fehler else str(len(fehler)) + ' Prüfung(en) fehlgeschlagen.'}")
sys.exit(1 if fehler else 0)
PY
  rm -f "$WORK/client.json" "$WORK/kcadm.config"   # Secret bzw. Admin-Token
}

case "${1:-alles}" in
  alles)
    trap stoppen EXIT
    starten
    cp "$P/client-secret" "$WORK/secret-1"
    einrichten   # zweiter Lauf: idempotent, Secret bleibt
    cmp -s "$P/client-secret" "$WORK/secret-1" || die "Zweiter Lauf hat das Secret geändert."
    info "Zweiter Lauf: Secret unverändert."
    rm -f "$WORK/secret-1"
    pruefen ;;
  start) starten ;;
  pruefen) pruefen ;;
  stop) stoppen ;;
  zuruecksetzen)
    stoppen; pg start
    (cd / && runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -c 'DROP DATABASE IF EXISTS keycloak' -c 'DROP ROLE IF EXISTS keycloak')
    pg stop
    rm -rf -- "$KC_ROOT" "$KC_DATA" "$WORK" "$ADMIN_FILE" "$P/client-secret" "$P/ju-finanzen.env" "$P"/.gebaut-* "$P/.start"
    info "Zurückgesetzt (Download bleibt in $P/download)." ;;
  *) sed -n '2,16p' "$0"; exit 1 ;;
esac
