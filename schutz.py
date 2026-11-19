"""Abwehr in der Anwendung im Serverbetrieb (ab 0.39): Firewall, Sperrliste, Bedrohungsliste, Live-Verkehr, Prüfungen.

- Ein Einstieg vor jeder Anfrage (`eingang`, aus `app.Handler.abwehr`) und einer danach (`nachher`): gesperrte Adresse
  (von Hand, automatisch, Bedrohungsliste Spamhaus DROP), Fallen für Scanner-Pfade, Angriffswerkzeuge, Angriffsmuster,
  Fehlerbremse. Treffer zählen je Adresse; ab der Schwelle automatische Sperre auf Zeit.
- Die Adresse des Aufrufers kommt nur aus `aufrufer` (X-Forwarded-For gilt allein von 127.0.0.1/::1).
- Sparsam: Zähler, Live-Verkehr und der geladene Stand liegen im Arbeitsspeicher – normale Anfragen kosten keinen
  Datenbankzugriff. Geschrieben wird nur bei Sperren, Ereignissen und (gebündelt) für die Stundenstatistik.
- Einstellungen und Bedrohungsliste stehen in `sicherheit_zustand` (nicht in `settings`), Sperren in `schutz_sperren`.
- Nie sperrbar: 127.0.0.1/::1, die Liste „nie sperren“, beim Sperren von Hand die eigene Adresse. Notausgang:
  `verwaltung.py schutz-aus`. Nie Inhalte, Sitzungen oder Personen im Verkehr – nur Adresse, Methode, Pfad, Status.
"""
import http.client
import ipaddress
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from bisect import bisect_right
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

import rollen
import sicherheit

MODI = {'aus': 'aus', 'protokollieren': 'nur protokollieren', 'abweisen': 'abweisen'}
SPERRMINUTEN = (10, 60, 360, 1440, 10080)   # 10 Minuten … 7 Tage
VORGABEN = {'modus': 'abweisen', 'auto': True, 'schwelle': 5, 'sperrminuten': 60, 'fehler': 30, 'liste': True, 'nie': []}
# Notfallmodus: Sperre ab dem ersten Treffer für 7 Tage, strenge Fehlerbremse, Bedrohungsliste an („nie sperren“ bleibt)
NOTFALL = {'modus': 'abweisen', 'auto': True, 'schwelle': 1, 'sperrminuten': 10080, 'fehler': 10, 'liste': True}
FENSTER = 600            # Treffer zählen 10 Minuten
FEHLERFENSTER = 300      # Fehlerbremse: 5 Minuten
# Fehlerbremse zählt nur diese Antworten und nur ohne Anmeldung. Bewusst nicht: 401 (abgelaufene Sitzung, falsches Passwort –
# dafür gibt es zugang.Throttle), 428 (Bestätigung), 429 (Anmeldesperre), 503 (Notfall/Wartung)
FEHLER_STATUS = (400, 403, 404, 413, 414)   # 405 nicht: HEAD/OPTIONS von Verfügbarkeitsprüfungen sollen niemanden sperren
HARMLOS = ('/favicon.ico', '/robots.txt', '/apple-touch-icon.png', '/apple-touch-icon-precomposed.png')   # fragt jeder Browser
NACHLADEN = 10           # Sekunden: so schnell wirkt eine Änderung von außen (verwaltung.py schutz-aus)
STATISTIK_ALLE = 30      # Sekunden: Stundenstatistik höchstens so oft schreiben
VERKEHR_MAX = 500
MITSCHNITT_MINUTEN = 15
MAX_AUTO = 5000          # automatische Sperren gleichzeitig; darüber verdrängt eine neue die mit dem frühesten Ablauf
QUELLEN = ('https://www.spamhaus.org/drop/drop.txt', 'https://www.spamhaus.org/drop/dropv6.txt')
LISTE_BYTES = 2_000_000
LISTE_NETZE = (50, 20000)   # plausibel (Stand 11/2026: rund 1.800 Netze)
LISTE_PRAEFIX = {4: 10, 6: 24}   # größere Netze stehen nie auf der Bedrohungsliste (Spamhaus führt keine /8)
LISTE_IPV4_ADRESSEN = 60_000_000   # abgedeckte IPv4-Adressen höchstens (gemessen 19.11.2026: 15.025.664 in 1.747 Netzen – rund das Vierfache)
HOLEN_FRIST = 60         # Sekunden für den ganzen Abruf einer Quelle (eine tröpfelnde Quelle hält den Stundenlauf nicht auf)
LISTE_ALT_TAGE = 3
SERVERSCHUTZ = '/var/lib/ju-serverschutz/status.json'
SERVERSCHUTZ_BYTES = 65536
SERVERSCHUTZ_MINUTEN = 60
ZAHLEN = {'sperrliste_netze': int, 'sperrliste_stand': str, 'gebannt': int, 'abgewiesen_24h': int, 'updates_offen': int, 'neustart_noetig': bool}
STAENDE = ('ok', 'hinweis', 'kritisch')
# Netze, die nie auf eine Sperr- oder Bedrohungsliste gehören: eigener Rechner, private Netze, Tailscale (100.64.0.0/10)
INTERN = [ipaddress.ip_network(n) for n in ('0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
                                            '192.168.0.0/16', '224.0.0.0/3', '::/127', 'fc00::/7', 'fe80::/10', 'ff00::/8')]
LOKAL = (ipaddress.ip_network('127.0.0.0/8'), ipaddress.ip_network('::1/128'))
ULA = ipaddress.ip_network('fc00::/7')   # private IPv6, z. B. Tailscale fd7a:115c:a1e0::/48 – alle Geräte in einem Präfix
KLEINSTES_PRAEFIX = {4: 8, 6: 19}   # größere Netze nimmt weder die Sperrliste noch die Bedrohungsliste an

# Fallen: Pfade, die es hier nie gibt – wer sie aufruft, sucht nach Schwachstellen (statische Dateien: nur .html/.js/.css/.svg)
FALLEN = re.compile(r'(?i)/\.(?!well-known/)|^/(?:wp-|wordpress|xmlrpc|phpmyadmin|pma/|myadmin|adminer|cgi-bin|actuator|solr|'
                    r'boaform|hnap1|owa/|autodiscover|server-status|manager/html|_ignition|telescope)|'
                    r'^/(?!api/).*\.(?:php\d?|aspx?|jsp|cgi|sql|bak|old|ini|ya?ml|conf|pem|key|tar|gz|zip|rar|7z)$')
WERKZEUGE = re.compile(r'(?i)\b(?:sqlmap|nikto|nmap|masscan|acunetix|nessus|openvas|wpscan|dirbuster|gobuster|feroxbuster|ffuf|zgrab|'
                       r'nuclei|havij|w3af|whatweb|jaeles|xsstrike|commix)\b')
# Angriffsmuster: bewusst eindeutig. Geprüft werden Pfad und Abfrage jeder Anfrage und die JSON-Werte nur ohne Anmeldung –
# angemeldete Personen dürfen beliebigen Text speichern (Verwendungszwecke, Notizen).
MUSTER = [(gruppe, re.compile(ausdruck, re.I)) for gruppe, ausdruck in (
    ('SQL-Einschleusung', r'\bunion\b[\s/*()]+(?:all[\s/*]+)?select\b'),
    ('SQL-Einschleusung', r'\b(?:sleep|benchmark|pg_sleep|waitfor\s+delay)\s*\(\s*[\'"\d]'),
    ('SQL-Einschleusung', r'[\'"]\s*(?:or|and)\s+[\'"]?\d+[\'"]?\s*=\s*[\'"]?\d+'),
    ('SQL-Einschleusung', r'\binformation_schema\b|\bsqlite_master\b|\bload_file\s*\(|\binto\s+(?:out|dump)file\b'),
    ('SQL-Einschleusung', r';\s*(?:drop|truncate|alter)\s+table\b'),
    ('Skript-Einschleusung', r'<\s*script\b|<\s*iframe\b|<\s*object\b|<\s*embed\b'),
    ('Skript-Einschleusung', r'\bjavascript\s*:|\bvbscript\s*:'),
    ('Skript-Einschleusung', r'<[^>]{1,200}\bon(?:error|load|mouseover|focus|toggle|animationstart)\s*='),
    ('Pfadmanipulation', r'(?:\.\.[/\\]){2,}|/etc/(?:passwd|shadow|hosts)\b|\bproc/self/environ\b|\bwin\.ini\b'),
    ('Code-Ausführung', r'\$\{\s*jndi\s*:|\$\{\s*(?:env|sys|lower|upper)\s*:'),
    ('Code-Ausführung', r'\b(?:php|phar|expect|zip|glob)://'),
    ('Code-Ausführung', r'<\?php|\b(?:system|exec|passthru|shell_exec|popen|proc_open)\s*\(\s*[\'"$]'),
    ('Code-Ausführung', r'\b(?:base64_decode|gzinflate|str_rot13)\s*\(|\b__import__\s*\(|\bos\.system\s*\('),
    ('Kopfzeilen-Einschleusung', r'[\r\n][ \t]*(?:set-cookie|location|content-type)[ \t]*:'),   # nie \s* nach [\r\n]: quadratisch bei vielen Zeilenumbrüchen
)]
GEHEIM = ('password', 'current', 'new', 'repeat', 'code', 'token')   # Passwörter und Codes nie prüfen (beliebige Zeichen erlaubt)
PRUEFLAENGE = 20000   # shortcut: längere Texte nur bis hierher geprüft; erhöhen, wenn Adressen länger werden dürfen

UHR = time.time          # in Tests ersetzbar
_SCHLOSS = threading.Lock()
_Z = None                # geladener Stand (Einstellungen, Sperren, Index der Bedrohungsliste)
_ZAEHLER = {}            # Adresse → {'h': [Zeiten der Treffer], 'e': [Zeiten der Fehlerantworten]}
_VERKEHR = deque(maxlen=VERKEHR_MAX)
_STATISTIK = {}          # (Stunde, Art) → Anzahl, noch nicht geschrieben
_MERKER = {'statistik': 0.0, 'meldung': 0.0, 'weitere': 0, 'mitschnitt': 0.0, 'unlesbar': 0.0}   # unlesbar = zuletzt X-Forwarded-For unlesbar


def init(db):
    inside = db.in_transaction
    db.execute('CREATE TABLE IF NOT EXISTS schutz_sperren(id INTEGER PRIMARY KEY,netz TEXT NOT NULL,art TEXT NOT NULL,'
               "grund TEXT NOT NULL DEFAULT '',bis INTEGER NOT NULL DEFAULT 0,zeit INTEGER NOT NULL,von TEXT NOT NULL DEFAULT '')")
    db.execute('CREATE TABLE IF NOT EXISTS schutz_stunden(stunde TEXT NOT NULL,art TEXT NOT NULL,n INTEGER NOT NULL,PRIMARY KEY(stunde,art))')
    if not inside:
        db.commit()


# ---- Adresse des Aufrufers ----
def aufrufer(verbindung, weitergereicht=''):
    """Adresse des Aufrufers (einzige Stelle): X-Forwarded-For zählt nur, wenn die Verbindung von 127.0.0.1/::1 kommt
    (lokaler Proxy) – dann der letzte Eintrag, den der Proxy selbst angehängt hat (`weitergereicht` = alle Kopfzeilen mit
    Komma verbunden; ein Port wie 198.51.100.7:51234 oder [2001:db8::1]:443 wird abgestreift); sonst die Adresse der Verbindung."""
    try:
        ip = _adresse(verbindung)
    except ValueError:
        return None   # ohne lesbare Adresse wird nie gesperrt
    if ip.is_loopback and weitergereicht:
        letzter = weitergereicht.split(',')[-1].strip()
        if letzter.startswith('['):              # [2001:db8::1]:443
            letzter = letzter[1:].partition(']')[0]
        elif letzter.count(':') == 1:            # 198.51.100.7:51234 (IPv6 ohne Klammern hat mehrere Doppelpunkte)
            letzter = letzter.partition(':')[0]
        try:
            weiter = _adresse(letzter)
            if not weiter.is_loopback:
                return weiter
        except ValueError:
            pass
        # unlesbarer oder lokaler Eintrag vom eigenen Proxy (er reicht den Kopf des Aufrufers durch, statt die Adresse selbst
        # anzuhängen): wie ohne Kopf (nie sperrbar) statt alle auszusperren – die Konfigurationsprüfung meldet es
        _MERKER['unlesbar'] = UHR()
    return ip


def _adresse(text):
    ip = ipaddress.ip_address(str(text).strip().partition('%')[0])
    return ip.ipv4_mapped or ip if ip.version == 6 else ip


def _netz(text):
    """Adresse oder Netz aus einer Eingabe (Sperre von Hand, „nie sperren“); zu große Netze sind nie erlaubt."""
    try:
        netz = ipaddress.ip_network(str(text).strip(), strict=False)
    except ValueError:
        raise ValueError(f'„{str(text)[:60]}“ ist keine gültige Adresse und kein gültiges Netz (z. B. 203.0.113.7 oder 203.0.113.0/24).')
    if netz.prefixlen < KLEINSTES_PRAEFIX[netz.version]:
        raise ValueError(f'Das Netz {netz} ist zu groß.')
    return netz


def _schluessel(ip):
    """Zähl- und Sperreinheit: IPv4 einzeln, öffentliche IPv6 je /64 (ein Anschluss hat dort beliebig viele Adressen), private
    IPv6 (fc00::/7) einzeln – im Tailnet teilen sich alle Geräte ein Präfix, ein /64 sperrte sie alle."""
    return f'{ip}/{ip.max_prefixlen}' if ip.version == 4 or ip in ULA else str(ipaddress.IPv6Network((int(ip) >> 64 << 64, 64)))


# ---- geladener Stand ----
class _Stand:
    def __init__(self, pfad, e, sperren, index, liste_stand):
        self.pfad, self.e, self.geladen = str(pfad), e, UHR()
        self.nie = [ipaddress.ip_network(n) for n in e['nie']]
        self.hand = [(ipaddress.ip_network(netz), bis) for netz, art, bis in sperren if art == 'hand']
        self.auto = {netz: bis for netz, art, bis in sperren if art == 'auto'}
        self.index, self.liste_stand = index, liste_stand


def einstellungen(db):
    """Einstellungen der Firewall mit Vorgaben für alles, was nicht gespeichert ist."""
    e = {**VORGABEN, **(sicherheit._lesen(db, 'schutz') or {})}
    return {k: e[k] for k in VORGABEN}


def _index(netze):
    """Je IP-Version sortierte, zusammengefasste Bereiche (Anfänge, Enden) für die Suche mit bisect."""
    index = {}
    for version in (4, 6):
        bereiche = list(ipaddress.collapse_addresses(n for n in netze if n.version == version))
        index[version] = ([int(n.network_address) for n in bereiche], [int(n.broadcast_address) for n in bereiche])
    return index


def _gelistet(index, ip):
    anfang, ende = index[ip.version]
    i = bisect_right(anfang, int(ip)) - 1
    return i >= 0 and int(ip) <= ende[i]


def _zustand(pfad):
    """Stand aus dem Arbeitsspeicher; höchstens alle NACHLADEN Sekunden (und nach jeder Änderung) neu aus der Datenbank."""
    global _Z
    z = _Z
    if z is not None and z.pfad == str(pfad) and UHR() - z.geladen < NACHLADEN:
        return z
    e, sperren, info, netze = dict(VORGABEN), [], {}, None
    try:
        con = sqlite3.connect(f'file:{pfad}?mode=ro', uri=True, timeout=2)
        try:
            e = einstellungen(con)
            sperren = con.execute('SELECT netz,art,bis FROM schutz_sperren WHERE bis=0 OR bis>?', (int(UHR()),)).fetchall()
            info = sicherheit._lesen(con, 'bedrohungsliste_stand') or {}
            if z is None or z.pfad != str(pfad) or z.liste_stand != info.get('stand'):   # nur neu einlesen, wenn sie sich geändert hat
                netze = [ipaddress.ip_network(n) for n in sicherheit._lesen(con, 'bedrohungsliste') or []]
        finally:
            con.close()
    except (sqlite3.Error, ValueError):
        if z is not None and z.pfad == str(pfad):   # Datenbank gerade nicht lesbar: beim bisherigen Stand bleiben
            z.geladen = UHR()
            return z
    _Z = _Stand(pfad, e, sperren, _index(netze or []) if netze is not None or z is None else z.index, info.get('stand'))
    return _Z


def _aktuell(db):
    """Geladener Stand für Aufrufe mit Datenbankverbindung (Oberfläche, Verwaltung)."""
    return _Z if _Z is not None else _zustand(db.execute('PRAGMA database_list').fetchone()[2])


def vergessen(alles=False):
    """Stand bei der nächsten Anfrage neu laden (nach Änderungen); `alles` leert auch Zähler, Verkehr und Statistik (Tests)."""
    global _Z
    with _SCHLOSS:
        _Z = None
        if alles:
            _ZAEHLER.clear()
            _VERKEHR.clear()
            _STATISTIK.clear()
            _MERKER.update(statistik=0.0, meldung=0.0, weitere=0, mitschnitt=0.0, unlesbar=0.0)


# ---- Prüfung jeder Anfrage ----
def muster(*texte):
    """Gruppe des ersten Angriffsmusters in den Texten (auch ein- und zweifach URL-dekodiert) oder None."""
    for text in texte:
        text = str(text)[:PRUEFLAENGE]
        for variante in {text, unquote(text), unquote(unquote(text))}:
            for gruppe, ausdruck in MUSTER:
                if ausdruck.search(variante):
                    return gruppe
    return None


def _werte(wert, out, tiefe=0):
    if tiefe > 8 or len(out) > 500:
        return
    if isinstance(wert, str):
        out.append(wert)
    elif isinstance(wert, dict):
        for key, value in wert.items():
            if str(key).lower() not in GEHEIM:
                out.append(str(key))
                _werte(value, out, tiefe + 1)
    elif isinstance(wert, list):
        for value in wert:
            _werte(value, out, tiefe + 1)


def _nie(z, ip):
    return ip is None or ip.is_loopback or any(ip in netz for netz in z.nie)


def _gesperrt(z, ip, jetzt, angemeldet=False):
    """Grund der Sperre oder None. Sperren von Hand gelten immer; automatische Sperren und die Bedrohungsliste nicht für
    gültig angemeldete Sitzungen (sonst sperrt ein Scanner hinter demselben Anschluss alle aus)."""
    if any(ip in netz and (not bis or bis > jetzt) for netz, bis in z.hand):
        return 'von Hand'
    if angemeldet:
        return None
    if z.auto.get(_schluessel(ip), 0) > jetzt:
        return 'automatisch'
    if z.e['liste'] and _gelistet(z.index, ip):
        return 'Bedrohungsliste'
    return None


def _zaehlen(ip, art, fenster, jetzt):
    """Unter _SCHLOSS: Zeitpunkt vormerken, Anzahl im Fenster liefern."""
    if len(_ZAEHLER) > 5000:   # älteste Hälfte verwerfen
        for key in sorted(_ZAEHLER, key=lambda k: _ZAEHLER[k]['t'])[:2500]:
            del _ZAEHLER[key]
    e = _ZAEHLER.setdefault(_schluessel(ip), {'h': [], 'e': [], 't': jetzt})
    e[art] = [t for t in e[art] if t > jetzt - fenster][-600:] + [jetzt]
    e['t'] = jetzt
    return len(e[art])


def _zaehle_statistik(art, jetzt):
    key = (datetime.fromtimestamp(jetzt).strftime('%Y-%m-%dT%H'), art)
    _STATISTIK[key] = _STATISTIK.get(key, 0) + 1


def _statistik_schreiben(db):
    with _SCHLOSS:
        offen = dict(_STATISTIK)
        _STATISTIK.clear()
        _MERKER['statistik'] = UHR()
    if offen:
        with db:
            db.executemany('INSERT INTO schutz_stunden(stunde,art,n) VALUES(?,?,?) ON CONFLICT(stunde,art) DO UPDATE SET n=n+excluded.n',
                           [(stunde, art, n) for (stunde, art), n in offen.items()])
            db.execute('DELETE FROM schutz_stunden WHERE stunde<?', ((datetime.fromtimestamp(UHR()) - timedelta(days=8)).strftime('%Y-%m-%dT%H'),))


def _schreiben(pfad, aufgabe):
    """Schreibzugriff aus der Prüfung einer Anfrage (nur bei Auffälligkeiten) – ein Fehler dort hält keine Anfrage auf."""
    try:
        con = sqlite3.connect(pfad, timeout=5)
        try:
            aufgabe(con)
        finally:
            con.close()
    except sqlite3.Error:
        pass


def _treffer(z, pfad, ip, art, grund, jetzt, zaehler='h'):
    """Treffer festhalten; ab der Schwelle automatisch sperren. `zaehler`: 'h' = zählen, None = nur Statistik (angemeldet
    oder von einer fremden Seite ausgelöst)."""
    verdraengt = None
    with _SCHLOSS:
        _zaehle_statistik(art, jetzt)
        n = _zaehlen(ip, zaehler, FENSTER, jetzt) if zaehler else 0
        key = _schluessel(ip)
        sperren = bool(zaehler and z.e['auto'] and n >= z.e['schwelle'] and z.auto.get(key, 0) <= jetzt)
        if sperren and key not in z.auto and len(z.auto) >= MAX_AUTO:   # voll: lieber die bald ablaufende Sperre opfern als niemanden sperren
            verdraengt = min(z.auto, key=z.auto.get)
            del z.auto[verdraengt]
        if sperren:
            bis = z.auto[key] = int(jetzt + 60 * z.e['sperrminuten'])
            _zaehle_statistik('sperre', jetzt)
            melden, weitere = jetzt - _MERKER['meldung'] >= 60, _MERKER['weitere']   # höchstens ein Ereignis je Minute, weitere als Anzahl
            _MERKER.update({'meldung': jetzt, 'weitere': 0} if melden else {'weitere': weitere + 1})
        faellig = sperren or jetzt - _MERKER['statistik'] >= STATISTIK_ALLE
    if not faellig:
        return

    def aufgabe(db):
        if sperren:
            with db:
                db.execute("DELETE FROM schutz_sperren WHERE art='auto' AND (netz IN (?,?) OR (bis>0 AND bis<?))", (key, verdraengt, int(jetzt) - 86400))
                db.execute("INSERT INTO schutz_sperren(netz,art,grund,bis,zeit,von) VALUES(?,'auto',?,?,?,'Firewall')", (key, grund[:120], bis, int(jetzt)))
            if melden:
                sicherheit.ereignis(db, 'schutz_sperre', 'Firewall', key, {'art': 'automatisch', 'grund': grund[:120], 'minuten': z.e['sperrminuten'],
                                                                         **({'weitere': weitere} if weitere else {})})
        _statistik_schreiben(db)
    _schreiben(pfad, aufgabe)


def eingang(pfad, ip, ziel, agent='', angemeldet=False, fremd=False):
    """Prüfung VOR der Verarbeitung einer Anfrage. Liefert (Status, Art): Status 403/404 = so abweisen, None = durchlassen;
    Art = Text für den Live-Verkehr ('' = unauffällig). `ziel` = Pfad mit Abfrage, `fremd` = von einer fremden Seite ausgelöst
    (wird abgewiesen, aber nie gezählt: sonst könnte jede Webseite mit ein paar unsichtbaren Bildern ihre Besucher vom
    Server aussperren – der Browser schickt sie ohne Sitzungscookie. Ein Scanner, der Sec-Fetch-Site fälscht, entgeht so
    der Sperre, bleibt aber bei jedem Treffer abgewiesen). Angemeldet: die Abfrage (Suchtexte)
    wird nicht auf Muster geprüft, der Pfad schon."""
    z = _zustand(pfad)
    if z.e['modus'] == 'aus' or _nie(z, ip):
        return None, ''
    weisen, jetzt = z.e['modus'] == 'abweisen', UHR()
    grund = _gesperrt(z, ip, jetzt, angemeldet)
    if grund:
        if weisen:
            with _SCHLOSS:
                _zaehle_statistik('abgewiesen', jetzt)
        return (403 if weisen else None), f'gesperrt ({grund})' + ('' if weisen else ' – nur protokolliert')
    weg = ziel.partition('?')[0]
    if FALLEN.search(weg):
        art, status, kurz = 'Falle', 404, 'falle'
    elif agent and WERKZEUGE.search(agent[:300]):
        art, status, kurz = 'Angriffswerkzeug', 403, 'werkzeug'
    else:
        gruppe = muster(weg if angemeldet else ziel)
        if not gruppe:
            return None, ''
        art, status, kurz = 'Angriffsmuster: ' + gruppe, 403, 'muster'
    _treffer(z, pfad, ip, kurz, art, jetzt, None if angemeldet or fremd else 'h')
    return (status if weisen else None), art + ('' if weisen else ' – nur protokolliert')


def inhalt(pfad, ip, daten):
    """JSON-Werte einer Anfrage OHNE Anmeldung prüfen (Passwortfelder nie). Liefert (Status, Art) wie `eingang`."""
    z = _zustand(pfad)
    if z.e['modus'] == 'aus' or _nie(z, ip):
        return None, ''
    werte = []
    _werte(daten, werte)
    gruppe = muster(*werte)
    if not gruppe:
        return None, ''
    weisen = z.e['modus'] == 'abweisen'
    _treffer(z, pfad, ip, 'muster', 'Angriffsmuster: ' + gruppe, UHR())
    return (403 if weisen else None), 'Angriffsmuster: ' + gruppe + ('' if weisen else ' – nur protokolliert')


def nachher(pfad, ip, methode, ziel, status, art='', angemeldet=False, fremd=False):
    """Nach jeder Antwort: Fehlerbremse (viele Fehlerantworten einer Adresse ohne Anmeldung = Treffer) und Live-Verkehr.
    Unauffällige Anfragen kosten ohne laufenden Mitschnitt nichts."""
    z, jetzt, weg = _zustand(pfad), UHR(), ziel.partition('?')[0]
    if (not art and status in FEHLER_STATUS and not angemeldet and not fremd and z.e['modus'] != 'aus' and z.e['fehler']
            and weg not in HARMLOS and not _nie(z, ip)):
        art = 'Fehlerantwort'
        with _SCHLOSS:
            n = _zaehlen(ip, 'e', FEHLERFENSTER, jetzt)
        if n >= z.e['fehler']:
            art = 'Fehlerbremse'
            _treffer(z, pfad, ip, 'fehler', 'zu viele Fehlerantworten', jetzt)
    if not art and _MERKER['mitschnitt'] <= jetzt:
        return
    weg = ''.join(c for c in weg[:120] if c.isprintable())
    with _SCHLOSS:
        for e in list(_VERKEHR)[-20:]:   # dieselbe Auffälligkeit derselben Adresse binnen einer Minute: zählen statt einzeln merken
            if art and e['adresse'] == str(ip) and e['art'] == art and e['pfad'] == weg and e['zeit'] > jetzt - 60:
                e.update(n=e['n'] + 1, zeit=jetzt, status=status)
                return
        _VERKEHR.append({'zeit': jetzt, 'adresse': str(ip), 'methode': str(methode)[:7], 'pfad': weg, 'status': status, 'art': art, 'n': 1})


# ---- Einstellungen, Sperrliste, Mitschnitt ----
def _iso(t):
    return datetime.fromtimestamp(t).isoformat(timespec='seconds') if t else ''


def einstellungen_speichern(db, daten, actor, eigene=None, geraet=''):
    """Einstellungen prüfen und speichern (nur genannte Felder). Schutz vor Aussperrung: die Bedrohungsliste lässt sich nicht
    einschalten, solange die eigene Adresse darauf steht und nicht unter „nie sperren“."""
    alt = einstellungen(db)
    neu = {**alt, **{k: daten[k] for k in VORGABEN if k in daten}}
    if neu['modus'] not in MODI:
        raise ValueError('Unbekannter Firewall-Modus.')
    for key in ('auto', 'liste'):
        if not isinstance(neu[key], bool):
            raise ValueError('Ungültige Angabe.')
    for key, low, high, text in (('schwelle', 1, 50, 'Treffer bis zur Sperre: 1 bis 50.'), ('fehler', 0, 500, 'Fehlerbremse: 0 (aus) bis 500 Fehlerantworten.')):
        if isinstance(neu[key], bool) or not isinstance(neu[key], int) or not low <= neu[key] <= high:
            raise ValueError(text)
    if neu['sperrminuten'] not in SPERRMINUTEN:
        raise ValueError('Ungültige Sperrdauer.')
    if not isinstance(neu['nie'], list) or len(neu['nie']) > 50:
        raise ValueError('„Nie sperren“: höchstens 50 Adressen oder Netze.')
    neu['nie'] = sorted({str(_netz(n)) for n in neu['nie'] if str(n).strip()})
    if neu['liste'] and eigene is not None and not any(eigene in ipaddress.ip_network(n) for n in neu['nie']) and _gelistet(_aktuell(db).index, eigene):
        raise ValueError(f'Deine aktuelle Adresse {eigene} steht auf der Bedrohungsliste – bitte zuerst unter „nie sperren“ eintragen.')
    geaendert = [k for k in VORGABEN if neu[k] != alt[k]]
    if geaendert:
        with db:
            sicherheit._schreiben(db, 'schutz', neu)
            frei = _scharf(db, alt['modus'], neu['modus'])
        sicherheit.ereignis(db, 'schutz_einstellung', actor, geraet, {'geaendert': geaendert, 'modus': MODI[neu['modus']]})
        if frei:
            sicherheit.ereignis(db, 'schutz_frei', actor, geraet, {'anzahl': frei, 'art': 'automatisch', 'grund': 'Firewall auf „abweisen“ gestellt'})
        vergessen()
    return neu


def _scharf(db, alt, neu):
    """Beim Umschalten auf „abweisen“ (in einer Transaktion): automatische Sperren aus der Zeit davor aufheben und neu zählen –
    sie entstanden ungeprüft und sperrten sonst nachträglich, auch die eigene Person nach dem Ende ihrer Sitzung. Sperren von
    Hand bleiben. Liefert die Anzahl."""
    if alt == neu or neu != 'abweisen':
        return 0
    with _SCHLOSS:
        _ZAEHLER.clear()
    return db.execute("DELETE FROM schutz_sperren WHERE art='auto'").rowcount


def sperren(db, netz, grund, minuten, actor, eigene=None, geraet=''):
    """Adresse oder Netz von Hand sperren (minuten 0 = unbefristet). Nie: die eigene Adresse, 127.0.0.1, „nie sperren“."""
    netz = _netz(netz)
    grund = ' '.join(str(grund or '').split())[:120]
    if isinstance(minuten, bool) or not isinstance(minuten, int) or not 0 <= minuten <= 525600:
        raise ValueError('Ungültige Dauer.')
    if any(netz.overlaps(n) for n in LOKAL if n.version == netz.version):
        raise ValueError('Der eigene Rechner (127.0.0.1) lässt sich nicht sperren.')
    if eigene is not None and eigene in netz:
        raise ValueError(f'{eigene} ist deine eigene aktuelle Adresse – nicht gesperrt.')
    for n in einstellungen(db)['nie']:
        if ipaddress.ip_network(n).version == netz.version and netz.overlaps(ipaddress.ip_network(n)):
            raise ValueError(f'{n} steht unter „nie sperren“ – bitte dort zuerst entfernen.')
    jetzt = int(UHR())
    with db:
        db.execute("DELETE FROM schutz_sperren WHERE art='hand' AND netz=?", (str(netz),))
        db.execute("INSERT INTO schutz_sperren(netz,art,grund,bis,zeit,von) VALUES(?,'hand',?,?,?,?)",
                   (str(netz), grund, jetzt + 60 * minuten if minuten else 0, jetzt, str(actor)[:80]))
    sicherheit.ereignis(db, 'schutz_sperre', actor, geraet, {'art': 'von Hand', 'netz': str(netz), 'grund': grund, **({'minuten': minuten} if minuten else {})})
    vergessen()
    return str(netz)


def freigeben(db, wen, actor, geraet=''):
    """Sperre freigeben: Kennung einer Sperre oder 'auto' für alle automatischen. Liefert die Anzahl."""
    if wen == 'auto':
        rows = db.execute("SELECT id,netz FROM schutz_sperren WHERE art='auto'").fetchall()
    else:
        rows = db.execute('SELECT id,netz FROM schutz_sperren WHERE id=?', (wen if isinstance(wen, int) and not isinstance(wen, bool) else -1,)).fetchall()
        if not rows:
            raise ValueError('Diese Sperre gibt es nicht mehr.')
    if rows:
        with db:
            db.executemany('DELETE FROM schutz_sperren WHERE id=?', [(r[0],) for r in rows])
        sicherheit.ereignis(db, 'schutz_frei', actor, geraet, {'netz': rows[0][1]} if wen != 'auto' else {'anzahl': len(rows), 'art': 'automatisch'})
        with _SCHLOSS:
            for r in rows:   # Treffer dieser Adressen zählen neu
                _ZAEHLER.pop(r[1], None)
        vergessen()
    return len(rows)


def aus(db, actor='Serververwaltung'):
    """Notausgang (verwaltung.py schutz-aus): alle Sperren löschen, Firewall auf „nur protokollieren“. Liefert die Anzahl."""
    with db:
        n = db.execute('DELETE FROM schutz_sperren').rowcount
        sicherheit._schreiben(db, 'schutz', {**einstellungen(db), 'modus': 'protokollieren'})
    sicherheit.ereignis(db, 'schutz_aus', actor, 'Server', {'sperren': n})
    vergessen()
    return n


def notfall(db, an):
    """Notfallmodus: Vorlage NOTFALL anwenden und den vorherigen Stand merken bzw. beim Beenden zurückbringen – je Feld und
    nur, solange es noch den Wert der Vorlage hat (was während des Notfalls von Hand geändert wurde, bleibt)."""
    e, frei = einstellungen(db), 0
    with db:
        if an:
            sicherheit._schreiben(db, 'schutz_vorher', {k: e[k] for k in NOTFALL})
            sicherheit._schreiben(db, 'schutz', {**e, **NOTFALL})
            frei = _scharf(db, e['modus'], NOTFALL['modus'])
        else:
            vorher = sicherheit._lesen(db, 'schutz_vorher') or {}
            sicherheit._schreiben(db, 'schutz', {**e, **{k: v for k, v in vorher.items() if k in NOTFALL and e[k] == NOTFALL[k]}})
            sicherheit._schreiben(db, 'schutz_vorher', None)
    if frei:
        sicherheit.ereignis(db, 'schutz_frei', 'Notfallmodus', '', {'anzahl': frei, 'art': 'automatisch', 'grund': 'Firewall auf „abweisen“ gestellt'})
    vergessen()


def mitschnitt(db, an, actor, geraet=''):
    """Mitschnitt aller Anfragen für MITSCHNITT_MINUTEN starten bzw. beenden (nur Adresse, Methode, Pfad, Status)."""
    _MERKER['mitschnitt'] = UHR() + 60 * MITSCHNITT_MINUTEN if an else 0.0
    if an:
        sicherheit.ereignis(db, 'schutz_mitschnitt', actor, geraet, {'minuten': MITSCHNITT_MINUTEN})
    return _iso(_MERKER['mitschnitt'])


def status(db, eigene=None):
    """Übersicht für den Reiter „Firewall & Sperren“: Einstellungen, Sperrliste, Bedrohungsliste, Live-Verkehr."""
    jetzt = int(UHR())
    rows = db.execute('SELECT id,netz,art,grund,bis,zeit,von FROM schutz_sperren WHERE bis=0 OR bis>? ORDER BY art DESC,zeit DESC LIMIT 300', (jetzt,)).fetchall()
    z = _aktuell(db)
    with _SCHLOSS:
        verkehr = [{**e, 'zeit': _iso(e['zeit'])} for e in list(_VERKEHR)[:-201:-1]]
    return {'einstellungen': einstellungen(db), 'modi': MODI, 'sperrminuten': list(SPERRMINUTEN), 'notfall_vorlage': bool(sicherheit._lesen(db, 'schutz_vorher')),
            'sperren': [{'id': r[0], 'netz': r[1], 'art': r[2], 'grund': r[3], 'bis': _iso(r[4]), 'seit': _iso(r[5]), 'von': r[6]} for r in rows],
            'liste': liste_info(db), 'meine_adresse': str(eigene or ''), 'geschuetzt': eigene is not None and _nie(z, eigene),
            'eigene_gelistet': eigene is not None and _gelistet(z.index, eigene),
            'mitschnitt_bis': _iso(_MERKER['mitschnitt']) if _MERKER['mitschnitt'] > jetzt else '', 'verkehr': verkehr}


def abwehr(db):
    """Abwehr der letzten 24 Stunden für das Lagebild: Treffer nach Art, Abweisungen, Sperren – gesamt und je Stunde."""
    _statistik_schreiben(db)
    jetzt = datetime.fromtimestamp(UHR())
    stunden = [(jetzt - timedelta(hours=h)).strftime('%Y-%m-%dT%H') for h in range(23, -1, -1)]
    werte = {}
    for stunde, art, n in db.execute('SELECT stunde,art,n FROM schutz_stunden WHERE stunde>=?', (stunden[0],)):
        werte.setdefault(stunde, {})[art] = n
    arten = ('falle', 'werkzeug', 'muster', 'fehler')
    verlauf = [{'stunde': s[11:], 'treffer': sum(werte.get(s, {}).get(a, 0) for a in arten), 'abgewiesen': werte.get(s, {}).get('abgewiesen', 0),
                'sperren': werte.get(s, {}).get('sperre', 0)} for s in stunden]
    summe = lambda art: sum(w.get(art, 0) for w in werte.values())
    return {'modus': einstellungen(db)['modus'], 'treffer': {a: summe(a) for a in arten}, 'treffer_gesamt': sum(v['treffer'] for v in verlauf),
            'abgewiesen': summe('abgewiesen'), 'sperren': summe('sperre'), 'stunden': verlauf,
            'gesperrt': db.execute('SELECT count(*) FROM schutz_sperren WHERE bis=0 OR bis>?', (int(UHR()),)).fetchone()[0]}


# ---- Bedrohungsliste (Spamhaus DROP) ----
class _KeineWeiterleitung(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, 'Weiterleitung abgelehnt', headers, fp)


def holen(url):
    """Eine Quelle der Bedrohungsliste abrufen: nur https, ohne Weiterleitung, höchstens LISTE_BYTES + 1 Bytes und HOLEN_FRIST
    Sekunden insgesamt (timeout gilt nur je Lesevorgang – eine tröpfelnde Quelle hielte sonst den Stundenlauf auf). In Tests ersetzt."""
    if not url.startswith('https://'):
        raise ValueError('Die Bedrohungsliste wird nur über https geladen.')
    import versand
    opener = urllib.request.build_opener(_KeineWeiterleitung, urllib.request.HTTPSHandler(context=versand.tls_context()))
    ende, teile, groesse = UHR() + HOLEN_FRIST, [], 0
    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'JU-Finanzen'}), timeout=20) as antwort:
        while groesse <= LISTE_BYTES:
            if UHR() > ende:
                raise TimeoutError('Die Bedrohungsliste kam nicht rechtzeitig.')
            teil = antwort.read1(65536)   # liefert, was da ist, statt auf volle 64 KB zu warten
            if not teil:
                break
            teile.append(teil)
            groesse += len(teil)
    return b''.join(teile)[:LISTE_BYTES + 1]


def liste_lesen(text):
    """Netze aus einer DROP-Datei („Netz ; SBL-Nummer“, Kommentare mit ;). Jede Zeile muss ein plausibles öffentliches Netz
    sein – eine einzige unlesbare Zeile verwirft die ganze Datei (dann bleibt die alte Liste)."""
    netze = []
    for zeile in text.splitlines():
        eintrag = zeile.partition(';')[0].strip()
        if not eintrag:
            continue
        try:
            netz = ipaddress.ip_network(eintrag)
        except ValueError:
            raise ValueError('Die Bedrohungsliste enthält eine unlesbare Zeile.')
        if netz.prefixlen < LISTE_PRAEFIX[netz.version] or any(netz.overlaps(n) for n in INTERN if n.version == netz.version):
            raise ValueError('Die Bedrohungsliste enthält ein unplausibles Netz.')
        netze.append(netz)
    return netze


def liste_laden(db, actor='automatisch', geraet=''):
    """Bedrohungsliste neu laden und prüfen; bei jedem Fehler bleibt die bisherige Liste. Liefert Stand und Anzahl."""
    netze = []
    try:
        for url in QUELLEN:
            roh = holen(url)
            if len(roh) > LISTE_BYTES:
                raise ValueError('Die Bedrohungsliste ist zu groß.')
            netze += liste_lesen(roh.decode('ascii'))
    except (OSError, http.client.HTTPException, UnicodeDecodeError) as e:   # urllib.error.URLError ist ein OSError
        raise ValueError('Die Bedrohungsliste konnte nicht geladen werden' + (' (kein gültiger Text).' if isinstance(e, UnicodeDecodeError) else '. Bitte später erneut versuchen.'))
    flaeche = sum(n.num_addresses for n in ipaddress.collapse_addresses(n for n in netze if n.version == 4))
    if not LISTE_NETZE[0] <= len(netze) <= LISTE_NETZE[1] or flaeche > LISTE_IPV4_ADRESSEN:
        raise ValueError(f'Die Bedrohungsliste ist unvollständig oder unplausibel ({len(netze)} Netze, {flaeche} IPv4-Adressen).')
    info = {'stand': sicherheit._jetzt(), 'anzahl': len(netze)}
    with db:
        sicherheit._schreiben(db, 'bedrohungsliste', sorted({str(n) for n in netze}))
        sicherheit._schreiben(db, 'bedrohungsliste_stand', info)
    sicherheit.ereignis(db, 'schutz_liste', actor, geraet, {'anzahl': len(netze)})
    vergessen()
    return liste_info(db)


def liste_info(db):
    """Stand der Bedrohungsliste: {'stand', 'anzahl', 'veraltet'} – ohne Liste stand = ''."""
    info = sicherheit._lesen(db, 'bedrohungsliste_stand') or {'stand': '', 'anzahl': 0}
    grenze = (datetime.now() - timedelta(days=LISTE_ALT_TAGE)).isoformat()
    return {**info, 'quelle': 'Spamhaus DROP', 'veraltet': info['stand'] < grenze}


def stuendlich(db):
    """Hintergrundlauf (app.py, jede Stunde): Stundenstatistik schreiben; Bedrohungsliste neu laden, wenn eingeschaltet und
    älter als einen Tag. Liefert True, wenn die Liste geladen wurde."""
    _statistik_schreiben(db)
    if not einstellungen(db)['liste'] or liste_info(db)['stand'] > (datetime.now() - timedelta(days=1)).isoformat():
        return False
    liste_laden(db)
    return True


# ---- Bericht des Serverschutzes (Datei von root, wird nur gelesen und wie fremde Eingabe behandelt) ----
def _text(wert, laenge):
    return ''.join(c for c in ' '.join(str(wert).split()) if c.isprintable())[:laenge] if isinstance(wert, (str, int, float)) and not isinstance(wert, bool) else ''


def serverschutz(pfad=None, jetzt=None):
    """Bericht der Betriebssystem-Härtung lesen: {'vorhanden', 'aktuell', 'hinweis', 'zeit', 'pruefungen', 'zahlen'}.
    Nur erwartete Felder, Texte gekürzt; fehlt die Datei oder ist sie älter als 60 Minuten: nicht aktuell."""
    leer = {'vorhanden': False, 'aktuell': False, 'hinweis': 'Kein aktueller Bericht vom Server.', 'zeit': '', 'pruefungen': [], 'zahlen': {}}
    datei = Path(pfad or os.environ.get('JU_FINANZEN_SERVERSCHUTZ') or SERVERSCHUTZ)
    try:
        if not datei.is_file():
            return leer
        with open(datei, 'rb') as f:
            roh = f.read(SERVERSCHUTZ_BYTES + 1)
        daten = json.loads(roh.decode('utf-8')) if len(roh) <= SERVERSCHUTZ_BYTES else None
        zeit = datetime.fromisoformat(daten['zeit'])   # kein Objekt (nur ein dict hat ['zeit']), zu groß, ohne Zeit: TypeError/KeyError
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return {**leer, 'vorhanden': True, 'hinweis': 'Der Bericht vom Server ist nicht lesbar.'}
    if daten.get('version') != 1:
        return {**leer, 'vorhanden': True, 'hinweis': 'Der Bericht vom Server hat ein unbekanntes Format.'}
    if zeit.tzinfo:   # mit Zeitzone (z. B. `date -Is`): in Ortszeit wie alle Zeiten des Programms
        zeit = zeit.astimezone().replace(tzinfo=None)
    alter = ((jetzt or datetime.now()) - zeit).total_seconds()
    aktuell = -300 <= alter <= 60 * SERVERSCHUTZ_MINUTEN
    pruefungen = [{'id': _text(p.get('id'), 40), 'titel': _text(p.get('titel'), 80) or 'Prüfung',
                   'stand': p.get('stand') if p.get('stand') in STAENDE else 'hinweis', 'detail': _text(p.get('detail'), 200)}
                  for p in (daten.get('pruefungen') if isinstance(daten.get('pruefungen'), list) else [])[:30] if isinstance(p, dict)]
    roh_zahlen = daten.get('zahlen') if isinstance(daten.get('zahlen'), dict) else {}
    zahlen = {}
    for key, typ in ZAHLEN.items():
        wert = roh_zahlen.get(key)
        if typ is int and isinstance(wert, int) and not isinstance(wert, bool) and 0 <= wert < 10 ** 9:
            zahlen[key] = wert
        elif typ is bool and isinstance(wert, bool):
            zahlen[key] = wert
        elif typ is str and isinstance(wert, str):
            try:
                datetime.fromisoformat(wert)
                zahlen[key] = wert[:32]
            except ValueError:
                pass
    return {'vorhanden': True, 'aktuell': aktuell, 'hinweis': '' if aktuell else 'Kein aktueller Bericht vom Server.',
            'zeit': zeit.isoformat(timespec='seconds'),
            'pruefungen': pruefungen, 'zahlen': zahlen}


# ---- Konfigurationsprüfung ----
def pruefung(db, lage, bericht, keycloak=False, https=False):
    """Konfigurationsprüfung für das Lagebild: Liste aus {'id', 'titel', 'stand' ok|hinweis|kritisch, 'detail', 'ziel'}.
    `lage` = sicherheit.lagebild, `bericht` = serverschutz(); `ziel` = Reiter der Seite Sicherheit bzw. 'seite:<Seite>'."""
    e, liste, d = einstellungen(db), liste_info(db), lage.get('dateipruefung') or {}
    unlesbar = _MERKER['unlesbar'] if UHR() - _MERKER['unlesbar'] < 86400 else 0   # in den letzten 24 Stunden
    ohne_app = db.execute(f'SELECT count(*) FROM users WHERE {rollen.GILT} AND NOT totp_pflicht').fetchone()[0]
    verwalter = sum(1 for k in sicherheit.konten(db) if k['aktiv'] and rollen.can(k['rechte'], 'verwaltung', 'schreiben'))
    tage = lambda iso: (datetime.now() - datetime.fromisoformat(iso[:19])).days if iso else 9999
    befunde = len(d.get('geaendert', [])) + len(d.get('fehlend', [])) + len(d.get('zusaetzlich', []))
    schlimmster = max((STAENDE.index(p['stand']) for p in bericht['pruefungen']), default=0)
    p = lambda id, titel, stand, detail, ziel='': {'id': id, 'titel': titel, 'stand': stand, 'detail': detail, 'ziel': ziel}
    return [
        p('keycloak', 'Anmeldung über Keycloak', 'ok' if keycloak else 'hinweis',
          'Anmeldung mit zweitem Faktor über Keycloak.' if keycloak else 'Anmeldung nur mit Passwort – ohne zweiten Faktor.'),
        p('authenticator', 'Authenticator-Pflicht für alle', 'ok' if keycloak and not ohne_app else 'hinweis',
          'Alle aktiven Personen brauchen die Authenticator-App.' if keycloak and not ohne_app else
          f'{ohne_app} aktive Person(en) ohne Authenticator-Pflicht.' if keycloak else 'Erst mit Anmeldung über Keycloak möglich.', 'seite:users'),
        p('notzugang', 'Notzugang geschlossen', 'hinweis' if lage.get('notzugang') else 'ok',
          'Der Notzugang (Passwort-Anmeldung der Verwaltung) ist gerade offen.' if lage.get('notzugang') else 'Passwort-Anmeldung der Verwaltung ist geschlossen.'),
        p('firewall', 'Firewall aktiv', {'abweisen': 'ok', 'protokollieren': 'hinweis', 'aus': 'kritisch'}[e['modus']],
          {'abweisen': 'Angriffe werden abgewiesen' + ('.' if e['auto'] else ' – automatisches Sperren ist aus.'),
           'protokollieren': 'Die Firewall protokolliert nur und weist nichts ab.', 'aus': 'Die Firewall ist ausgeschaltet.'}[e['modus']], 'firewall'),
        p('aufrufer', 'Adresse jedes Aufrufers lesbar', 'kritisch' if unlesbar else 'ok',
          f'Aufruferadresse nicht lesbar oder lokal – Abwehr für diese Anfragen wirkungslos (Proxy-Einstellung prüfen). Zuletzt {_iso(unlesbar)}.' if unlesbar
          else 'Der Proxy reicht für jede Anfrage eine lesbare Adresse weiter.', 'firewall'),
        p('bedrohungsliste', 'Bedrohungsliste aktuell', 'ok' if e['liste'] and not liste['veraltet'] else 'hinweis',
          'Die Bedrohungsliste ist ausgeschaltet.' if not e['liste'] else 'Noch nicht geladen – „Jetzt laden“.' if not liste['stand'] else
          f'Älter als {LISTE_ALT_TAGE} Tage – der tägliche Abruf scheitert.' if liste['veraltet'] else f'{liste["anzahl"]} Netze (Spamhaus DROP).', 'firewall'),
        p('dateipruefung', 'Dateiprüfung ohne Befund', 'hinweis' if not d.get('verfuegbar') or tage(d.get('zeit', '')) > 2 else 'ok' if d.get('ok') else 'kritisch',
          'Keine Prüfliste vorhanden (nur nach Installation auf dem Server).' if not d.get('verfuegbar') else
          'Die letzte Prüfung ist älter als 2 Tage.' if tage(d.get('zeit', '')) > 2 else
          'Programmdateien unverändert.' if d.get('ok') else f'{befunde} Befund(e) – bitte ansehen.', 'system'),
        p('sicherung', 'Sicherung aktuell', 'ok' if tage(lage.get('letzte_sicherung') or '') <= 2 else 'kritisch',
          'Letzte automatische Sicherung höchstens 2 Tage alt.' if tage(lage.get('letzte_sicherung') or '') <= 2 else 'Keine Sicherung aus den letzten 2 Tagen.', 'system'),
        p('https', 'HTTPS und sichere Cookies', 'ok' if https else 'kritisch',
          'Alle Adressen laufen über HTTPS; die Sitzung ist an die Adresse gebunden (__Host-, Secure, HttpOnly, SameSite=Strict).' if https
          else 'Mindestens eine Adresse läuft ohne HTTPS – Anmeldung und Sitzung wären mitlesbar.'),
        p('ungenutzt', 'Keine ungenutzten Zugänge', 'hinweis' if lage.get('konten_ungenutzt') else 'ok',
          f'{lage["konten_ungenutzt"]} aktive(r) Zugang/Zugänge seit über {sicherheit.UNGENUTZT_TAGE} Tagen ohne Anmeldung.' if lage.get('konten_ungenutzt')
          else 'Alle aktiven Zugänge werden genutzt.', 'sitzungen'),
        p('verwaltung', 'Mehr als eine Person für die Verwaltung', 'ok' if verwalter > 1 else 'hinweis',
          f'{verwalter} aktive Personen können die Verwaltung ändern.' if verwalter > 1 else 'Nur eine Person kann die Verwaltung ändern – fällt sie aus, hilft nur der Server.', 'seite:users'),
        p('serverschutz', 'Serverschutz (Betriebssystem)', STAENDE[schlimmster] if bericht['aktuell'] else 'hinweis',
          bericht['hinweis'] or f'{len(bericht["pruefungen"])} Prüfungen des Servers, schlechtester Stand: {("in Ordnung", "Hinweis", "kritisch")[schlimmster]}.', 'system'),
    ]
