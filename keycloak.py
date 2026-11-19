"""Anmeldung über Keycloak im Serverbetrieb (ab 0.36).

Keycloak prüft nur Passwort und Authenticator-App (TOTP). Personen, Rollen und Rechte verwaltet das Programm selbst
(`rollen.py`); es legt die Personen über die Admin-API in Keycloak an und hält sie dort aktuell.

- Anmeldung: Authorization Code mit PKCE (S256) beim vertraulichen Client; der Browser sieht nur den öffentlichen
  Issuer (`https://…/auth/realms/<realm>`), das Programm spricht Keycloak direkt über 127.0.0.1 an (Backchannel).
- Admin-API: Token des Service-Accounts (client_credentials), im Arbeitsspeicher bis 30 s vor Ablauf.
- Anfragen nur an 127.0.0.1/::1, ohne Proxy, ohne Weiterleitungen, Zeitlimit 10 s.
- Client-Secret, Passwörter und Tokens erscheinen nie in Fehlermeldungen, Protokollen oder repr.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

TIMEOUT = 10
VORGANG_SEKUNDEN = 10 * 60          # so lange darf eine begonnene Anmeldung dauern
UHR_TOLERANZ = 60                   # iat darf so weit in der Zukunft liegen (Uhren von Server und Keycloak)
TOKEN_RESERVE = 30                  # Admin-Token so viele Sekunden vor Ablauf erneuern
APP_AKTION = 'CONFIGURE_TOTP'
PASSWORT_AKTION = 'UPDATE_PASSWORD'
LOOPBACK = ('127.0.0.1', '::1')
# gut vorlesbar: ohne 0/O, 1/l/I
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
ABGELEHNT = 'Keycloak hat die Angaben abgelehnt (z. B. Benutzername oder E-Mail ungültig).'


class KeycloakFehler(Exception):
    """Fehler mit deutscher, nutzerfreundlicher Meldung – nie mit Tokens, Passwörtern oder Antwortinhalten."""

    def __init__(self, meldung, status=None):
        super().__init__(meldung)
        self.status = status


@dataclass(frozen=True)
class Einstellungen:
    issuer: str                                  # Browser-Issuer, https://…/auth/realms/ju-hamburg
    intern: str                                  # Backchannel, http://127.0.0.1:8180/auth/realms/ju-hamburg
    admin_basis: str                             # http://127.0.0.1:8180/auth/admin/realms/ju-hamburg
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str


def _realm(url):
    teile = urllib.parse.urlsplit(url).path.rstrip('/').split('/')
    return teile[-1] if len(teile) >= 2 and teile[-2] == 'realms' and teile[-1] else None


def aus_umgebung(env=os.environ):
    """Einstellungen aus JU_FINANZEN_KEYCLOAK_*; None ohne Keycloak, ValueError bei halber oder unsicherer Angabe."""
    namen = ('ISSUER', 'INTERN', 'CLIENT', 'SECRET', 'RUECKKEHR')
    werte = {n: (env.get(f'JU_FINANZEN_KEYCLOAK_{n}') or '').strip() for n in namen}
    if not any(werte.values()):
        return None
    fehlt = [f'JU_FINANZEN_KEYCLOAK_{n}' for n in namen if not werte[n]]
    if fehlt:
        raise ValueError('Keycloak ist nur halb eingerichtet – es fehlt: ' + ', '.join(fehlt))
    issuer, intern = werte['ISSUER'].rstrip('/'), werte['INTERN'].rstrip('/')
    i, b, r = (urllib.parse.urlsplit(u) for u in (issuer, intern, werte['RUECKKEHR']))
    if i.scheme != 'https' or not i.hostname or i.query or i.fragment or not _realm(issuer):
        raise ValueError('JU_FINANZEN_KEYCLOAK_ISSUER muss eine https-Adresse der Form https://…/realms/<Realm> sein.')
    if b.scheme not in ('http', 'https') or b.hostname not in LOOPBACK or b.query or b.fragment or not _realm(intern):
        raise ValueError('JU_FINANZEN_KEYCLOAK_INTERN muss auf 127.0.0.1 oder ::1 zeigen (…/realms/<Realm>).')
    if _realm(issuer) != _realm(intern):
        raise ValueError('JU_FINANZEN_KEYCLOAK_ISSUER und _INTERN nennen verschiedene Realms.')
    if r.scheme != 'https' or not r.hostname or r.fragment:
        raise ValueError('JU_FINANZEN_KEYCLOAK_RUECKKEHR muss eine https-Adresse sein.')
    basis, _, realm = intern.rpartition('/realms/')
    return Einstellungen(issuer=issuer, intern=intern, admin_basis=f'{basis}/admin/realms/{realm}',
                         client_id=werte['CLIENT'], client_secret=werte['SECRET'], redirect_uri=werte['RUECKKEHR'])


# ---------------------------------------------------------------- HTTP

class _OhneUmleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                              # 3xx wird zum Fehler – ein Token folgt nie einer Weiterleitung


# ohne Proxy: http_proxy aus der Umgebung würde Secret und Tokens sonst über einen fremden Rechner schicken
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _OhneUmleitung)


def _http(methode, url, *, form=None, daten=None, token=None):
    """(Status, Header, Inhalt als Bytes). Netzfehler → KeycloakFehler; HTTP-Fehler kommen als Status zurück."""
    if urllib.parse.urlsplit(url).hostname not in LOOPBACK:
        raise KeycloakFehler('Keycloak darf nur über 127.0.0.1 angesprochen werden.')
    headers = {'Accept': 'application/json'}
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode('utf-8')
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    elif daten is not None:
        body = json.dumps(daten).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    anfrage = urllib.request.Request(url, data=body, headers=headers, method=methode)
    try:
        with _OPENER.open(anfrage, timeout=TIMEOUT) as antwort:
            return antwort.status, antwort.headers, antwort.read(2_000_000)
    except urllib.error.HTTPError as e:
        e.close()                                # Antwortkörper bleibt ungelesen (kann Details enthalten)
        return e.code, e.headers, b''
    except (urllib.error.URLError, OSError, ValueError):
        raise KeycloakFehler('Keycloak ist nicht erreichbar. Bitte später erneut versuchen.') from None


def _json(inhalt):
    try:
        return json.loads(inhalt.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise KeycloakFehler('Keycloak hat eine unlesbare Antwort geschickt.') from None


def _token_anfrage(cfg, form):
    form = {**form, 'client_id': cfg.client_id, 'client_secret': cfg.client_secret}   # client_secret_post
    status, _, inhalt = _http('POST', f'{cfg.intern}/protocol/openid-connect/token', form=form)
    if status == 400:
        raise KeycloakFehler('Die Anmeldung ist abgelaufen oder wurde schon verwendet. Bitte neu anmelden.', status)
    if status == 401:
        raise KeycloakFehler('Keycloak lehnt das Programm ab – Client und Client-Secret prüfen.', status)
    if status != 200:
        raise KeycloakFehler(f'Keycloak hat die Anmeldung nicht bestätigt (Status {status}).', status)
    antwort = _json(inhalt)
    if not isinstance(antwort, dict):
        raise KeycloakFehler('Keycloak hat eine unlesbare Antwort geschickt.')
    return antwort


# ---------------------------------------------------------------- Anmeldung

def _b64(daten):
    return base64.urlsafe_b64encode(daten).rstrip(b'=').decode('ascii')


def anmeldung_starten(cfg, aktion=None):
    """(Adresse für den Browser, Vorgang). Der Vorgang bleibt auf dem Server (Sitzung), nie im Browser.
    `aktion` = PASSWORT_AKTION: Keycloak fragt nach der Anmeldung ein neues Passwort ab (kc_action); andere Aktionen gibt es nicht."""
    if aktion not in (None, PASSWORT_AKTION):
        raise ValueError('Unbekannte Keycloak-Aktion.')
    vorgang = {'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32),
               'code_verifier': secrets.token_urlsafe(64), 'erstellt': time.time()}
    parameter = {
        'response_type': 'code', 'scope': 'openid', 'client_id': cfg.client_id, 'redirect_uri': cfg.redirect_uri,
        'state': vorgang['state'], 'nonce': vorgang['nonce'], 'prompt': 'login', 'max_age': '0', 'code_challenge_method': 'S256',
        'code_challenge': _b64(hashlib.sha256(vorgang['code_verifier'].encode('ascii')).digest()),
    }
    if aktion:
        parameter['kc_action'] = aktion
    return f'{cfg.issuer}/protocol/openid-connect/auth?{urllib.parse.urlencode(parameter)}', vorgang


def _eins(query, name):
    """Ein Wert aus der Rückkehr-Adresse – als dict von Texten oder von Listen (parse_qs)."""
    wert = query.get(name)
    if isinstance(wert, (list, tuple)):
        if len(wert) != 1:
            return None
        wert = wert[0]
    return wert if isinstance(wert, str) and wert else None


def _gleich(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


def _claims(id_token):
    teile = id_token.split('.') if isinstance(id_token, str) else []
    if len(teile) != 3:
        raise KeycloakFehler('Keycloak hat kein gültiges ID-Token geschickt.')
    try:
        claims = json.loads(base64.urlsafe_b64decode(teile[1] + '=' * (-len(teile[1]) % 4)))
    except ValueError:
        raise KeycloakFehler('Keycloak hat kein gültiges ID-Token geschickt.') from None
    if not isinstance(claims, dict):
        raise KeycloakFehler('Keycloak hat kein gültiges ID-Token geschickt.')
    return claims


def anmeldung_abschliessen(cfg, vorgang, query):
    """Rückkehr aus Keycloak prüfen und Code einlösen → {'sub', 'benutzername'}; sonst KeycloakFehler.
    Verlangt eine frische Anmeldung (auth_time nach dem Start): prompt=login und max_age=0 stehen in der Adresse, die der Browser
    ändern kann – ohne diese Prüfung käme eine bestehende Keycloak-Sitzung ohne Passwort durch."""
    neu = 'Bitte die Anmeldung neu starten.'
    if not isinstance(vorgang, dict) or not _gleich(_eins(query, 'state'), vorgang.get('state')):
        raise KeycloakFehler('Die Anmeldung passt nicht zu dieser Sitzung. ' + neu)
    erstellt = vorgang.get('erstellt')
    if not isinstance(erstellt, (int, float)) or not 0 <= time.time() - erstellt <= VORGANG_SEKUNDEN:
        raise KeycloakFehler('Die Anmeldung hat zu lange gedauert. ' + neu)
    fehler = _eins(query, 'error')
    if fehler:                                   # Text von außen – nie in die Meldung übernehmen
        if fehler == 'access_denied':
            raise KeycloakFehler('Die Anmeldung wurde abgebrochen.')
        raise KeycloakFehler('Keycloak hat die Anmeldung abgelehnt. ' + neu)
    code = _eins(query, 'code')
    if not code:
        raise KeycloakFehler('Keycloak hat keinen Anmeldecode zurückgegeben. ' + neu)
    antwort = _token_anfrage(cfg, {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': cfg.redirect_uri,
                                   'code_verifier': vorgang.get('code_verifier', '')})
    # Keine Signaturprüfung: Das ID-Token kommt direkt vom Token-Endpunkt (Backchannel über 127.0.0.1, nicht über den
    # Browser). Nach OIDC Core 3.1.3.7 Nr. 6 darf dann die Herkunft der Verbindung die Signatur ersetzen; auf dem
    # Loopback-Gerät kann nur ein Prozess desselben Servers antworten.
    c = _claims(antwort.get('id_token'))
    jetzt = time.time()
    aud = c.get('aud')
    aud = [aud] if isinstance(aud, str) else aud if isinstance(aud, list) else []
    pruefungen = (
        _gleich(c.get('iss'), cfg.issuer),
        cfg.client_id in aud,
        'azp' not in c or c.get('azp') == cfg.client_id,
        isinstance(c.get('exp'), (int, float)) and c['exp'] > jetzt,
        isinstance(c.get('iat'), (int, float)) and c['iat'] <= jetzt + UHR_TOLERANZ,
        _gleich(c.get('nonce'), vorgang.get('nonce')),
        isinstance(c.get('sub'), str) and bool(c['sub']),
        isinstance(c.get('auth_time'), (int, float)) and c['auth_time'] >= erstellt - 2,   # auth_time in ganzen Sekunden
    )
    if not all(pruefungen):
        raise KeycloakFehler('Die Bestätigung von Keycloak ist ungültig. ' + neu)
    return {'sub': c['sub'], 'benutzername': c.get('preferred_username') or ''}


def abmelden_url(cfg, id_token_hint=None, zurueck=None):
    """Adresse für die Abmeldung bei Keycloak (RP-initiated Logout)."""
    parameter = {'client_id': cfg.client_id}
    if id_token_hint:
        parameter['id_token_hint'] = id_token_hint
    if zurueck:
        parameter['post_logout_redirect_uri'] = zurueck
    return f'{cfg.issuer}/protocol/openid-connect/logout?{urllib.parse.urlencode(parameter)}'


# ---------------------------------------------------------------- Admin-API

class Verwaltung:
    """Personen in Keycloak anlegen und pflegen (Service-Account des Clients). Threadsicher."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._sperre = threading.Lock()
        self._token = None
        self._gueltig_bis = 0.0

    def __repr__(self):
        return f'Verwaltung({self.cfg.admin_basis!r})'

    def _admin_token(self):
        with self._sperre:
            if self._token and time.monotonic() < self._gueltig_bis:
                return self._token
            antwort = _token_anfrage(self.cfg, {'grant_type': 'client_credentials'})
            token, dauer = antwort.get('access_token'), antwort.get('expires_in')
            if not isinstance(token, str) or not token or not isinstance(dauer, (int, float)):
                raise KeycloakFehler('Keycloak hat kein Zugangstoken für die Verwaltung geschickt.')
            self._token, self._gueltig_bis = token, time.monotonic() + dauer - TOKEN_RESERVE
            return token

    def _api(self, methode, pfad, daten=None, erlaubt=()):
        """(Status, Header, JSON oder None). Fehlerstatus außer `erlaubt` → KeycloakFehler."""
        status, headers, inhalt = _http(methode, self.cfg.admin_basis + pfad, daten=daten, token=self._admin_token())
        if status == 401:
            with self._sperre:
                self._token = None               # Token widerrufen o. Ä. – beim nächsten Aufruf neu holen
        if status >= 400 and status not in erlaubt:
            if status in (401, 403):
                raise KeycloakFehler('Dem Programm fehlen Rechte in Keycloak (Service-Account prüfen).', status)
            if status == 404:
                raise KeycloakFehler('Diese Person gibt es in Keycloak nicht (mehr).', status)
            if status >= 500:
                raise KeycloakFehler(f'Keycloak meldet einen Fehler (Status {status}).', status)
            raise KeycloakFehler(f'Keycloak hat die Änderung abgelehnt (Status {status}).', status)
        return status, headers, (_json(inhalt) if inhalt.strip() else None)

    @staticmethod
    def _pfad(keycloak_id):
        if not isinstance(keycloak_id, str) or not keycloak_id:
            raise KeycloakFehler('Keine Keycloak-Kennung angegeben.')
        return '/users/' + urllib.parse.quote(keycloak_id, safe='')

    def anlegen(self, benutzername, vorname, nachname, email, aktiv, startpasswort, app_pflicht):
        aktionen = [PASSWORT_AKTION] + ([APP_AKTION] if app_pflicht else [])
        daten = {'username': benutzername, 'firstName': vorname or '', 'lastName': nachname or '',
                 'enabled': bool(aktiv), 'requiredActions': aktionen,
                 'credentials': [{'type': 'password', 'value': startpasswort, 'temporary': True}]}
        if email:
            daten.update(email=email, emailVerified=True)
        status, headers, _ = self._api('POST', '/users', daten, erlaubt=(400, 409))
        if status == 409:
            raise KeycloakFehler('Benutzername oder E-Mail gibt es in Keycloak schon.', 409)
        if status == 400:
            raise KeycloakFehler(ABGELEHNT, 400)
        kennung = (headers.get('Location') or '').rstrip('/').rpartition('/')[2]
        if status != 201 or not kennung:
            raise KeycloakFehler('Keycloak hat die neue Person nicht bestätigt.')
        return urllib.parse.unquote(kennung)

    def suchen(self, benutzername):
        _, _, treffer = self._api('GET', '/users?' + urllib.parse.urlencode({'username': benutzername, 'exact': 'true'}))
        for person in treffer or []:
            if isinstance(person, dict) and str(person.get('username', '')).lower() == benutzername.lower():
                return person.get('id')
        return None

    def _aktionen(self, keycloak_id):
        """Offene Pflichtschritte der Person (requiredActions)."""
        _, _, person = self._api('GET', self._pfad(keycloak_id))
        aktionen = (person or {}).get('requiredActions') if isinstance(person, dict) else None
        return [a for a in aktionen or [] if isinstance(a, str)]

    def aendern(self, keycloak_id, vorname, nachname, email, aktiv, app_pflicht):
        vorher = self._aktionen(keycloak_id)
        aktionen = [a for a in vorher if a != APP_AKTION]
        if app_pflicht and (APP_AKTION in vorher or not self.hat_app(keycloak_id)):
            aktionen.append(APP_AKTION)
        daten = {'firstName': vorname or '', 'lastName': nachname or '', 'email': email or '',
                 'enabled': bool(aktiv), 'requiredActions': aktionen}
        status, _, _ = self._api('PUT', self._pfad(keycloak_id), daten, erlaubt=(400, 409))
        if status == 409:
            raise KeycloakFehler('Diese E-Mail-Adresse gibt es in Keycloak schon.', 409)
        if status == 400:
            raise KeycloakFehler(ABGELEHNT, 400)

    def startpasswort_setzen(self, keycloak_id, passwort):
        status, _, _ = self._api('PUT', self._pfad(keycloak_id) + '/reset-password',
                                 {'type': 'password', 'value': passwort, 'temporary': True}, erlaubt=(400,))
        if status == 400:
            raise KeycloakFehler('Das Passwort erfüllt die Passwortregeln von Keycloak nicht.', 400)

    def _app_zugaenge(self, keycloak_id):
        _, _, zugaenge = self._api('GET', self._pfad(keycloak_id) + '/credentials')
        return [z for z in zugaenge or [] if isinstance(z, dict) and z.get('type') == 'otp']

    def hat_app(self, keycloak_id):
        return bool(self._app_zugaenge(keycloak_id))

    def app_zuruecksetzen(self, keycloak_id):
        """Authenticator-App entfernen; bei der nächsten Anmeldung muss eine neue eingerichtet werden."""
        for zugang in self._app_zugaenge(keycloak_id):
            self._api('DELETE', f"{self._pfad(keycloak_id)}/credentials/{urllib.parse.quote(str(zugang.get('id')), safe='')}")
        aktionen = [a for a in self._aktionen(keycloak_id) if a != APP_AKTION] + [APP_AKTION]
        self._api('PUT', self._pfad(keycloak_id), {'requiredActions': aktionen})

    def abmelden(self, keycloak_id):
        """Alle Keycloak-Sitzungen der Person beenden."""
        self._api('POST', self._pfad(keycloak_id) + '/logout')

    def erreichbar(self):
        try:
            self._api('GET', '/users/count')
            return True
        except KeycloakFehler:
            return False


def startpasswort():
    """Zufälliges, gut vorlesbares Startpasswort: 4 Gruppen à 4 Zeichen (19 Zeichen), mit Ziffer und Buchstaben."""
    while True:
        text = ''.join(secrets.choice(ALPHABET) for _ in range(16))
        if any(c.isdigit() for c in text) and any(c.isalpha() for c in text):
            return '-'.join(text[i:i + 4] for i in range(0, 16, 4))
