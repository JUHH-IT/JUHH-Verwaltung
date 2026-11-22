"""Abwehr in der Anwendung (schutz.py, ab 0.39): Firewall, Sperrliste, Bedrohungsliste, Live-Verkehr, Serverschutz-Bericht.
Nur erfundene Daten, nie ins Netz – Adressen ausschließlich aus den Dokumentationsbereichen (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24, 2001:db8::/32)."""
import socket
import os
import io
import ipaddress
import json
import re
import sqlite3
import tempfile
import time
import unittest
import unittest.mock
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

import app
import rollen
import schutz
import sicherheit
import test_rollen
import test_server
import verwaltung
import zugang
from core import Store

PASSWORT = test_server.PASSWORT
IP = ipaddress.ip_address
AUSSEN = IP('203.0.113.50')
ECHTES_HOLEN = schutz.holen
BROWSER = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'


def drop_fixture():
    """Bedrohungsliste im Format von Spamhaus DROP (drop.txt, dropv6.txt) aus Dokumentationsnetzen."""
    v4 = ['; Spamhaus DROP List (erfundene Testdaten)', '; Last-Modified: heute', '']
    v4 += [f'192.0.2.{i * 4}/30 ; SBL{1000 + i}' for i in range(64)]             # 192.0.2.0/24 lückenlos
    v4 += ['198.51.100.16/28 ; SBL2000', '198.51.100.64/26 ; SBL2001']
    v6 = ['; Spamhaus IPv6 DROP List (erfundene Testdaten)', '2001:db8:dead::/48 ; SBL3000', '2001:db8:0:1::/64 ; SBL3001']
    return {schutz.QUELLEN[0]: '\n'.join(v4).encode(), schutz.QUELLEN[1]: '\n'.join(v6).encode()}


def kein_netz(url):
    raise AssertionError('Tests gehen nie ins Netz: ' + url)


class Basis(unittest.TestCase):
    """Eigene Datenbank, eigene Uhr, leerer Arbeitsspeicher der Abwehr; jeder Abruf nach außen wäre ein Fehler."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pfad = Path(self.tmp.name) / 'finanzen.sqlite3'
        self.store = Store(self.pfad)
        self.addCleanup(self.store.db.close)
        self.db = self.store.db
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        self.jetzt = [time.time()]
        patches = (unittest.mock.patch.object(schutz, 'UHR', lambda: self.jetzt[0]), unittest.mock.patch.object(schutz, 'holen', kein_netz))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        schutz.vergessen(alles=True)
        self.addCleanup(schutz.vergessen, True)

    def weiter(self, sekunden):
        self.jetzt[0] += sekunden

    def anfrage(self, ip=AUSSEN, ziel='/', antwort=200, agent=BROWSER, **kw):
        """Eine Anfrage wie in app.Handler: Prüfung vorher, Antwort, Prüfung nachher. Liefert (Status der Abwehr, Art)."""
        status, art = schutz.eingang(self.pfad, ip, ziel, agent, **kw)
        schutz.nachher(self.pfad, ip, 'GET', ziel, status or antwort, art, **kw)
        return status, art

    def setze(self, **werte):
        return schutz.einstellungen_speichern(self.db, werte, 'Erste')

    def arten(self):
        return [r['art'] for r in sicherheit.ereignisse(self.db)]


class AufruferTests(unittest.TestCase):
    def test_weitergereichte_adresse_nur_vom_eigenen_proxy(self):
        self.assertEqual(schutz.aufrufer('127.0.0.1', '203.0.113.9'), IP('203.0.113.9'))
        self.assertEqual(schutz.aufrufer('::1', ' 2001:db8::5 '), IP('2001:db8::5'))
        self.assertEqual(schutz.aufrufer('127.0.0.1', '198.51.100.1, 203.0.113.9'), IP('203.0.113.9'))   # der letzte Eintrag stammt vom Proxy
        # von außen (Verbindung nicht von 127.0.0.1): der Kopf ist wirkungslos – niemand kann sich eine fremde Adresse geben
        self.assertEqual(schutz.aufrufer('198.51.100.7', '203.0.113.9'), IP('198.51.100.7'))
        self.assertEqual(schutz.aufrufer('198.51.100.7', '127.0.0.1'), IP('198.51.100.7'))
        self.assertEqual(schutz.aufrufer('2001:db8::7', '127.0.0.1'), IP('2001:db8::7'))
        self.assertEqual(schutz.aufrufer('127.0.0.1'), IP('127.0.0.1'))
        self.assertEqual(schutz.aufrufer('127.0.0.1', 'unsinn'), IP('127.0.0.1'))                           # unlesbar: wie ohne Kopf
        self.assertEqual(schutz.aufrufer('::ffff:203.0.113.9'), IP('203.0.113.9'))                          # IPv4 über IPv6-Socket
        self.assertIsNone(schutz.aufrufer('keine-adresse'))

    def test_handler_nutzt_nur_diese_funktion(self):
        class H(app.Handler):
            def __init__(self, verbindung, *zeilen, **kopf):
                self.client_address, self.headers = (verbindung, 1), schutz.http.client.HTTPMessage()
                for name, wert in [*zeilen, *kopf.items()]:
                    self.headers[name] = wert   # wie http.server: jede Kopfzeile einzeln, auch doppelte
        gefaelscht = H('198.51.100.7', **{'X-Forwarded-For': '203.0.113.9', 'Tailscale-User-Login': 'chef@example.org'})
        self.assertEqual((str(gefaelscht.adresse()), gefaelscht.device()), ('198.51.100.7', '198.51.100.7'))
        proxy = H('127.0.0.1', **{'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual((str(proxy.adresse()), proxy.device()), ('203.0.113.9', '203.0.113.9'))
        self.assertEqual(H('127.0.0.1', **{'X-Forwarded-For': '203.0.113.9', 'Tailscale-User-Login': 'chef@example.org'}).device(), 'chef@example.org')
        # zwei Kopfzeilen: die erste schreibt der Aufrufer, die zweite hängt der Proxy an – es zählt die letzte
        zwei = H('127.0.0.1', ('X-Forwarded-For', '127.0.0.1'), ('X-Forwarded-For', '198.51.100.66'))
        self.assertEqual(str(zwei.adresse()), '198.51.100.66')
        quelle = (Path(app.__file__).read_text(encoding='utf-8'))
        self.assertEqual(quelle.count("'X-Forwarded-For'"), 1)                                             # nur in Handler.adresse

    def test_schluessel(self):
        self.assertEqual(schutz._schluessel(IP('203.0.113.9')), '203.0.113.9/32')
        self.assertEqual(schutz._schluessel(IP('2001:db8:1:2:3:4:5:6')), '2001:db8:1:2::/64')            # IPv6 je Anschluss
        self.assertEqual(schutz._schluessel(IP('::2')), '::/64')
        # private IPv6 (fc00::/7): Tailscale gibt allen Geräten Adressen aus EINEM Präfix (fd7a:115c:a1e0::/48) – je Gerät zählen
        self.assertEqual(schutz._schluessel(IP('fd7a:115c:a1e0:ab12:4843:cd96:6263:1111')), 'fd7a:115c:a1e0:ab12:4843:cd96:6263:1111/128')
        self.assertEqual(schutz._schluessel(IP('fc00::1')), 'fc00::1/128')
        self.assertEqual(schutz._schluessel(IP('2001:db8:1::1')), '2001:db8:1::/64')                     # Dokumentationsnetz bleibt öffentlich

    def test_port_und_unlesbarer_eintrag(self):
        with unittest.mock.patch.object(schutz, 'UHR', lambda: 1_000_000.0):
            schutz.vergessen(alles=True)
            self.addCleanup(schutz.vergessen, True)
            self.assertEqual(schutz.aufrufer('127.0.0.1', '198.51.100.70:51234'), IP('198.51.100.70'))   # Proxy hängt den Port an
            self.assertEqual(schutz.aufrufer('127.0.0.1', '203.0.113.1, [2001:db8::1]:443'), IP('2001:db8::1'))
            self.assertEqual(schutz.aufrufer('127.0.0.1', '2001:db8::1'), IP('2001:db8::1'))              # ohne Klammern: kein Port
            self.assertEqual(schutz._MERKER['unlesbar'], 0.0)
            for kopf in ('unknown', '198.51.100.70:x:y', '[kaputt]:443'):
                self.assertEqual(schutz.aufrufer('127.0.0.1', kopf), IP('127.0.0.1'))                    # wie lokal – kein Aussperren
            self.assertEqual(schutz._MERKER['unlesbar'], 1_000_000.0)                                    # aber vermerkt (Konfigurationsprüfung)
            schutz.vergessen(alles=True)
            for kopf in ('127.0.0.1', '198.51.100.70, ::1'):                                             # Proxy reicht den Kopf des Aufrufers nur durch
                self.assertEqual(schutz.aufrufer('127.0.0.1', kopf), IP('127.0.0.1'))
                self.assertEqual(schutz._MERKER['unlesbar'], 1_000_000.0, kopf)
                schutz._MERKER['unlesbar'] = 0.0


class MusterTests(unittest.TestCase):
    ANGRIFFE = (
        '/api/data?year=2026 UNION SELECT name,password FROM users', '/api/data?year=1/**/union/**/all/**/select/**/1',
        '/?q=1%27%20OR%20%271%27=%271', '/?q=%253Cscript%253Ealert(1)%253C/script%253E', '/?x=<img src=x onerror=alert(1)>',
        '/static/../../../../etc/passwd', '/?f=..%2f..%2f..%2fwin.ini', '/?x=${jndi:ldap://boese.example/a}', '/?u=php://input',
        '/?a=1;DROP TABLE users', '/?id=1 AND sleep(5)', '/?t=sqlite_master', '/?c=<?php system("id") ?>', '/?h=javascript:alert(1)',
        '/?n=name%0d%0aSet-Cookie:%20a=b', "/?p=__import__('os')",
    )
    NORMAL = (
        'Mitgliedsbeitrag 2026 – Müller & Söhne (Rate 1/2), Kd.-Nr. 4711', 'Erstattung: Fahrtkosten Hamburg–Berlin; Beleg 12/2026 <Kreisverband Nord>',
        'Spende „Sommerfest“ – 50 % für Getränke & Grill; Rest: Miete', "Rechnung 2026-0042 / O'Brien's Catering: 1 Kiste = 12 Flaschen",
        'Junge Union Hamburg: Auswahl (Select) der Delegierten; Drop-in-Treffen am Tisch', 'Location: Rathaus Altona; Set-Cookie-Schulung der IT',
        'SEPA-Lastschrift DE98ZZZ09999999999 Mandat M-1/2026 "Beitrag" und Spende', 'Kto. 1370 an 8100; Union Investment – Depot oder Girokonto?',
        'Bücher & Skripte für das Seminar <Rhetorik> (Teil 1 von 2)', '../Belege/2026 siehe Ordner; Datei system(alt).pdf',
        '/api/sicherheit-ereignisse?art=anmeldung&tage=30&suche=M%C3%BCller%20%26%20S%C3%B6hne', '/api/invoices.pdf?ids=1,2,3',
        '/api/monatsbericht.pdf?year=2026&month=1', '/api/op-liste.csv?kv=5100&ov=none', '/js/views/sicherheit.js', '/',
        '/api/keycloak/rueckkehr?state=abc&session_state=x&iss=https%3A%2F%2Fju.example%2Fauth%2Frealms%2Fju&code=a.b.c',
    )

    def test_muster_treffen_angriffe(self):
        for text in self.ANGRIFFE:
            with self.subTest(text=text[:40]):
                self.assertIsNotNone(schutz.muster(text))

    def test_normale_eingaben_bleiben_frei(self):
        for text in self.NORMAL:
            with self.subTest(text=text[:40]):
                self.assertIsNone(schutz.muster(text))

    def test_muster_bleiben_schnell(self):
        # keine Muster mit ausufernder Laufzeit: lange Läufe genau der Zeichen, die die Quantoren fressen – je Eingabe gemessen
        laeufe = (' ', '\t', '\n', '\r', '\r\n', '.', '/', '\\', '../', '..\\', '%0a', '%0d', '%20', '%2e', '%2f', '%25', '%250a', '<', "'", '"', '(', '*', '/*', '=', ':', '${', 'a')
        texte = ['/?x=' + '%0a' * 6666, '\n' * 20000, "' or " * 4000, 'union ' * 3000, '/?x=%0a' + '%09' * 6600]
        texte += [anfang + lauf * (schutz.PRUEFLAENGE // len(lauf)) for lauf in laeufe for anfang in ('', '/?x=', 'union', "'", '<a', '\n', 'sleep', '${', '/x.')]
        for text in texte:
            for name, pruefen in (('muster', schutz.muster), ('FALLEN', schutz.FALLEN.search), ('WERKZEUGE', schutz.WERKZEUGE.search)):
                start = time.perf_counter()
                pruefen(text)
                self.assertLess(time.perf_counter() - start, 0.2, f'{name}: {text[:12]!r}… ({len(text)} Zeichen)')

    def test_fallen(self):
        for weg in ('/wp-login.php', '/.env', '/.git/config', '/phpmyadmin/index', '/cgi-bin/luci', '/api/.env', '/backup.zip', '/index.php',
                    '/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php', '/server-status', '/xmlrpc.php', '/config.yml', '/WP-ADMIN/', '/js/.htaccess'):
            with self.subTest(weg=weg):
                self.assertTrue(schutz.FALLEN.search(weg))
        for weg in ('/', '/api/op-listen.zip', '/api/pruefpaket.zip', '/api/backup', '/api/kasse.csv', '/.well-known/security.txt', '/favicon.ico',
                    '/api/keycloak/rueckkehr', '/unbekannt'):
            with self.subTest(weg=weg):
                self.assertFalse(schutz.FALLEN.search(weg))
        static = Path(app.__file__).parent / 'static'
        dateien = ['/' + p.relative_to(static).as_posix() for p in static.rglob('*') if p.is_file()]
        self.assertGreater(len(dateien), 40)
        self.assertEqual([d for d in dateien if schutz.FALLEN.search(d)], [])               # keine Datei der Oberfläche ist eine Falle
        quelle = Path(app.__file__).read_text(encoding='utf-8')
        wege = set(re.findall(r"'(/api/[A-Za-z0-9._/-]+)'", quelle))
        self.assertEqual([w for w in wege if schutz.FALLEN.search(w) or schutz.muster(w)], [])   # kein eigener Weg auch

    def test_werkzeuge(self):
        for agent in ('sqlmap/1.7#stable (https://sqlmap.example)', 'Mozilla/5.0 (X11; Linux x86_64) Nikto/2.5.0', 'Nuclei - Open-source project', 'WPScan v3.8'):
            self.assertTrue(schutz.WERKZEUGE.search(agent), agent)
        for agent in (BROWSER, 'curl/8.5.0', 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148 Safari/604.1', ''):
            self.assertFalse(schutz.WERKZEUGE.search(agent), agent)


class FirewallTests(Basis):
    def test_vorgaben(self):
        self.assertEqual(schutz.einstellungen(self.db), {'modus': 'abweisen', 'auto': True, 'schwelle': 5, 'sperrminuten': 60, 'fehler': 30, 'liste': True, 'nie': []})
        self.assertEqual((schutz.FENSTER, schutz.FEHLERFENSTER, schutz.MITSCHNITT_MINUTEN), (600, 300, 15))
        self.assertEqual((min(schutz.SPERRMINUTEN), max(schutz.SPERRMINUTEN)), (10, 7 * 24 * 60))

    def test_einstellungen_werden_geprueft(self):
        for falsch in ({'modus': 'egal'}, {'schwelle': 0}, {'schwelle': True}, {'schwelle': '5'}, {'sperrminuten': 61}, {'fehler': -1}, {'fehler': 501},
                       {'auto': 1}, {'liste': 'ja'}, {'nie': 'x'}, {'nie': ['kein netz']}, {'nie': ['0.0.0.0/0']}, {'nie': ['203.0.113.1'] * 51}):
            with self.subTest(falsch=falsch), self.assertRaises(ValueError):
                self.setze(**falsch)
        self.assertEqual(self.setze(nie=['203.0.113.9', ' 2001:db8:5::1/64 ', ''], schwelle=3)['nie'], ['2001:db8:5::/64', '203.0.113.9/32'])
        self.assertEqual(schutz.einstellungen(self.db)['schwelle'], 3)
        self.assertEqual(sicherheit.ereignisse(self.db, art='schutz_einstellung')[0]['detail']['geaendert'], ['schwelle', 'nie'])
        self.setze(schwelle=3)                                                              # unverändert: kein weiteres Ereignis
        self.assertEqual(len(sicherheit.ereignisse(self.db, art='schutz_einstellung')), 1)
        self.assertNotIn('schutz', self.store.settings())                                   # nicht in den Stammdaten der Oberfläche

    def test_fallen_werkzeuge_und_muster_werden_abgewiesen(self):
        self.assertEqual(self.anfrage(ziel='/wp-login.php'), (404, 'Falle'))
        self.assertEqual(self.anfrage(agent='sqlmap/1.7'), (403, 'Angriffswerkzeug'))
        self.assertEqual(self.anfrage(ziel='/api/data?year=1%20union%20select%201'), (403, 'Angriffsmuster: SQL-Einschleusung'))
        self.assertEqual(self.anfrage(ziel='/api/status'), (None, ''))
        self.assertEqual(self.anfrage(ziel='/unbekannt', antwort=404), (None, ''))          # unbekannter Pfad: nur Fehlerbremse

    def test_zaehler_fenster_automatische_sperre_und_ablauf(self):
        for _ in range(4):
            self.assertEqual(self.anfrage(ziel='/.env')[0], 404)
        self.assertEqual(self.anfrage(), (None, ''))                                        # vier Treffer: noch frei
        self.weiter(schutz.FENSTER + 1)                                                     # Treffer verfallen nach 10 Minuten
        for _ in range(4):
            self.anfrage(ziel='/.env')
        self.assertEqual(self.anfrage(), (None, ''))
        self.anfrage(ziel='/.env')                                                          # fünfter Treffer im Fenster
        self.assertEqual(self.anfrage(), (403, 'gesperrt (automatisch)'))
        self.assertEqual(self.anfrage(ziel='/.env'), (403, 'gesperrt (automatisch)'))       # gesperrt: knapp, ohne weitere Prüfung
        self.assertEqual(self.anfrage(ip=IP('203.0.113.51')), (None, ''))                   # andere Adresse nicht
        row = self.db.execute('SELECT netz,art,grund,bis FROM schutz_sperren').fetchall()
        self.assertEqual([(r[0], r[1], r[2]) for r in row], [('203.0.113.50/32', 'auto', 'Falle')])
        self.assertEqual(row[0][3], int(self.jetzt[0]) + 3600)
        e = sicherheit.ereignisse(self.db, art='schutz_sperre')[0]
        self.assertEqual((e['person'], e['geraet'], e['detail']), ('Firewall', '203.0.113.50/32', {'art': 'automatisch', 'grund': 'Falle', 'minuten': 60}))
        self.assertEqual(self.anfrage(angemeldet=True), (None, ''))                         # gültige Sitzung kommt trotz automatischer Sperre durch
        self.weiter(3599)
        self.assertEqual(self.anfrage()[0], 403)
        self.weiter(2)
        self.assertEqual(self.anfrage(), (None, ''))                                        # Ablauf nach 60 Minuten

    def test_ipv6_sperrt_den_anschluss(self):
        self.setze(schwelle=2)
        for _ in range(2):
            self.anfrage(ip=IP('2001:db8:1::1'), ziel='/.git/HEAD')
        self.assertEqual(self.anfrage(ip=IP('2001:db8:1::beef'))[0], 403)                   # gleiches /64
        self.assertEqual(self.anfrage(ip=IP('2001:db8:2::1'))[0], None)

    def test_tailnet_geraete_zaehlen_einzeln(self):
        geraet_a, geraet_b = IP('fd7a:115c:a1e0:ab12:4843:cd96:6263:1111'), IP('fd7a:115c:a1e0:ab12:4843:cd96:6255:2222')   # gleiches /64
        for _ in range(5):
            self.anfrage(ip=geraet_a, ziel='/.git/config')
        self.assertEqual(self.anfrage(ip=geraet_a)[0], 403)
        self.assertEqual(self.anfrage(ip=geraet_b), (None, ''))                             # ein Gerät sperrt nicht das ganze Tailnet
        schutz.notfall(self.db, True)                                                       # Notfall: Sperre ab dem ersten Treffer
        self.anfrage(ip=IP('fd7a:115c:a1e0::3'), ziel='/.env')
        self.assertEqual(self.anfrage(ip=IP('fd7a:115c:a1e0::3'))[0], 403)
        self.assertEqual(self.anfrage(ip=IP('fd7a:115c:a1e0::4')), (None, ''))
        self.assertEqual(self.anfrage(ip=geraet_b), (None, ''))

    def test_hoechstzahl_automatischer_sperren_verdraengt_die_aelteste(self):
        self.setze(schwelle=1)
        with unittest.mock.patch.object(schutz, 'MAX_AUTO', 3):
            for i in range(1, 5):
                self.anfrage(ip=IP(f'198.51.100.{i}'), ziel='/.env')
                self.weiter(1)
            self.assertEqual(self.anfrage(ip=IP('198.51.100.4'))[0], 403)                   # die neueste Sperre gilt
            self.assertEqual(self.anfrage(ip=IP('198.51.100.1')), (None, ''))               # die mit dem frühesten Ablauf ist verdrängt
            self.assertEqual(self.anfrage(ip=IP('198.51.100.2'))[0], 403)
            netze = sorted(r[0] for r in self.db.execute("SELECT netz FROM schutz_sperren WHERE art='auto'"))
            self.assertEqual(netze, ['198.51.100.2/32', '198.51.100.3/32', '198.51.100.4/32'])
            self.weiter(schutz.NACHLADEN + 1)                                               # auch nach dem Nachladen aus der Datenbank
            self.assertEqual(self.anfrage(ip=IP('198.51.100.1')), (None, ''))

    def test_ereignisse_gebuendelt(self):
        self.setze(schwelle=1)
        for i in range(1, 6):
            self.anfrage(ip=IP(f'198.51.100.{i}'), ziel='/.env')
        self.assertEqual(self.db.execute('SELECT count(*) FROM schutz_sperren').fetchone()[0], 5)
        self.assertEqual(len(sicherheit.ereignisse(self.db, art='schutz_sperre')), 1)       # höchstens ein Ereignis je Minute
        self.weiter(61)
        self.anfrage(ip=IP('198.51.100.9'), ziel='/.env')
        self.assertEqual(sicherheit.ereignisse(self.db, art='schutz_sperre')[0]['detail']['weitere'], 4)

    def test_angemeldet_und_fremde_seite_zaehlen_nicht(self):
        for _ in range(20):
            self.assertEqual(self.anfrage(ziel='/wp-login.php', angemeldet=True)[0], 404)   # abgewiesen, aber kein Treffer
        for _ in range(200):
            self.assertEqual(self.anfrage(ziel='/wp-login.php', fremd=True)[0], 404)        # <img src=…/wp-login.php> auf fremder Seite
        self.assertEqual(self.anfrage(), (None, ''))                                        # keine Webseite sperrt ihre Besucher aus
        schutz.notfall(self.db, True)                                                       # auch nicht mit der Schwelle 1 des Notfalls
        self.anfrage(ziel='/.env', fremd=True)
        self.assertEqual(self.anfrage(), (None, ''))
        self.assertEqual(self.db.execute('SELECT count(*) FROM schutz_sperren').fetchone()[0], 0)

    def test_angemeldete_suchen_frei_der_pfad_wird_weiter_geprueft(self):
        for _ in range(10):
            self.assertEqual(self.anfrage(ziel='/api/sicherheit-ereignisse?suche=%3Cscript', angemeldet=True), (None, ''))
            self.assertEqual(self.anfrage(ziel="/api/data?suche=x' OR '1'='1", angemeldet=True), (None, ''))
        self.assertEqual(self.anfrage(ziel='/api/datei/etc/passwd', angemeldet=True), (403, 'Angriffsmuster: Pfadmanipulation'))
        self.assertEqual(self.anfrage(ziel='/api/sicherheit-ereignisse?suche=%3Cscript'), (403, 'Angriffsmuster: Skript-Einschleusung'))   # ohne Anmeldung weiter

    def test_fehlerbremse(self):
        for status in (401, 428, 429, 503, 200, 302):                                       # zählen nie
            for _ in range(40):
                self.anfrage(ziel='/api/data', antwort=status)
        for _ in range(200):
            self.anfrage(ziel='/api/book', antwort=400, angemeldet=True)                    # Buchhaltung mit vielen Eingabefehlern
            self.anfrage(ziel='/favicon.ico', antwort=404)                                  # fragt jeder Browser
            self.anfrage(ziel='/x', antwort=404, fremd=True)
            self.anfrage(ip=IP('127.0.0.1'), ziel='/x', antwort=404)
        self.assertEqual(self.anfrage(), (None, ''))
        self.assertEqual(schutz.abwehr(self.db)['treffer_gesamt'], 0)
        for _ in range(29):
            self.anfrage(ziel='/gibt-es-nicht', antwort=404)
        self.assertEqual(schutz.abwehr(self.db)['treffer']['fehler'], 0)                    # 29 Fehlerantworten: noch kein Treffer
        for _ in range(4):
            self.anfrage(ziel='/gibt-es-nicht', antwort=404)
        self.assertEqual(self.anfrage(), (None, ''))                                        # 33 = vier Treffer
        self.anfrage(ziel='/api/login', antwort=400)
        self.assertEqual(self.anfrage(), (403, 'gesperrt (automatisch)'))
        self.assertEqual(self.db.execute('SELECT grund FROM schutz_sperren').fetchone()[0], 'zu viele Fehlerantworten')
        schutz.freigeben(self.db, 'auto', 'Erste')
        self.weiter(schutz.FEHLERFENSTER + 1)                                               # Fenster 5 Minuten
        for _ in range(29):
            self.anfrage(ziel='/gibt-es-nicht', antwort=404)
        self.assertEqual(self.anfrage(), (None, ''))
        self.setze(fehler=0)                                                                # 0 = Fehlerbremse aus
        for _ in range(100):
            self.anfrage(ip=IP('198.51.100.3'), ziel='/gibt-es-nicht', antwort=404)
        self.assertEqual(self.anfrage(ip=IP('198.51.100.3')), (None, ''))

    def test_nie_sperren_und_eigener_rechner(self):
        self.setze(nie=['203.0.113.0/24', '2001:db8:aa::/48'])
        for ip in (AUSSEN, IP('2001:db8:aa::1'), IP('127.0.0.1'), IP('::1'), None):
            for _ in range(20):
                self.assertEqual(self.anfrage(ip=ip, ziel='/.env', antwort=404), (None, ''))
                self.assertEqual(self.anfrage(ip=ip, agent='sqlmap', antwort=404), (None, ''))
        self.assertEqual(self.db.execute('SELECT count(*) FROM schutz_sperren').fetchone()[0], 0)
        for netz in ('203.0.113.9', '203.0.0.0/8', '2001:db8:aa:1::/64'):
            with self.assertRaisesRegex(ValueError, 'nie sperren'):
                schutz.sperren(self.db, netz, 'Test', 0, 'Erste')
        for netz, text in (('127.0.0.1', 'eigene Rechner'), ('::1', 'eigene Rechner'), ('127.0.0.0/8', 'eigene Rechner'), ('0.0.0.0/0', 'zu groß'),
                           ('::/0', 'zu groß'), ('unsinn', 'keine gültige Adresse')):
            with self.assertRaisesRegex(ValueError, text):
                schutz.sperren(self.db, netz, 'Test', 0, 'Erste')

    def test_sperren_von_hand_freigeben_und_eigene_adresse(self):
        with self.assertRaisesRegex(ValueError, 'eigene aktuelle Adresse'):
            schutz.sperren(self.db, '198.51.100.0/24', 'Test', 0, 'Erste', eigene=IP('198.51.100.7'))
        with self.assertRaisesRegex(ValueError, 'eigene aktuelle Adresse'):
            schutz.sperren(self.db, '2001:db8:7::/48', 'Test', 0, 'Erste', eigene=IP('2001:db8:7::1'))
        for minuten in (-1, 525601, '60', True):
            with self.assertRaises(ValueError):
                schutz.sperren(self.db, '198.51.100.0/24', 'Test', minuten, 'Erste')
        self.assertEqual(schutz.sperren(self.db, '198.51.100.77/24', '  Scan am   Wochenende ', 0, 'Erste', eigene=AUSSEN, geraet='203.0.113.50'), '198.51.100.0/24')
        self.assertEqual(self.anfrage(ip=IP('198.51.100.255')), (403, 'gesperrt (von Hand)'))
        self.assertEqual(self.anfrage(ip=IP('198.51.100.0'), angemeldet=True)[0], 403)      # von Hand gilt auch für Angemeldete
        self.assertEqual(self.anfrage(ip=IP('198.51.101.0'))[0], None)
        s = schutz.status(self.db, AUSSEN)['sperren']
        self.assertEqual([(x['netz'], x['art'], x['grund'], x['bis'], x['von']) for x in s], [('198.51.100.0/24', 'hand', 'Scan am Wochenende', '', 'Erste')])
        schutz.sperren(self.db, '198.51.100.0/24', 'neu', 10, 'Erste')                      # gleiche Sperre: ersetzt, jetzt mit Ablauf
        self.assertEqual(len(schutz.status(self.db)['sperren']), 1)
        self.weiter(601)
        self.assertEqual(self.anfrage(ip=IP('198.51.100.5'))[0], None)                      # abgelaufen
        kennung = schutz.sperren(self.db, '2001:db8:9::1', '', 0, 'Erste') and schutz.status(self.db)['sperren'][0]['id']
        self.assertEqual(self.anfrage(ip=IP('2001:db8:9::1'))[0], 403)
        self.assertEqual(schutz.freigeben(self.db, kennung, 'Erste'), 1)
        self.assertEqual(self.anfrage(ip=IP('2001:db8:9::1'))[0], None)
        for falsch in (kennung, 'x', None, True):
            with self.assertRaises(ValueError):
                schutz.freigeben(self.db, falsch, 'Erste')
        self.assertEqual(schutz.freigeben(self.db, 'auto', 'Erste'), 0)
        arten = self.arten()
        self.assertEqual((arten.count('schutz_sperre'), arten.count('schutz_frei')), (3, 1))

    def test_modus_protokollieren_weist_nie_ab(self):
        self.setze(modus='protokollieren')
        schutz.sperren(self.db, '198.51.100.9', 'Test', 0, 'Erste')
        self.assertEqual(self.anfrage(ip=IP('198.51.100.9')), (None, 'gesperrt (von Hand) – nur protokolliert'))
        for _ in range(5):
            self.assertEqual(self.anfrage(ziel='/.env', antwort=404), (None, 'Falle – nur protokolliert'))
        self.assertEqual(self.anfrage(agent='nikto', antwort=200), (None, 'gesperrt (automatisch) – nur protokolliert'))
        self.assertEqual(self.db.execute("SELECT count(*) FROM schutz_sperren WHERE art='auto'").fetchone()[0], 1)   # vermerkt, was gesperrt würde
        a = schutz.abwehr(self.db)
        self.assertEqual((a['treffer']['falle'], a['abgewiesen'], a['sperren']), (5, 0, 1))
        self.assertEqual(schutz.inhalt(self.pfad, AUSSEN, {'name': '<script>x</script>'})[0], None)
        self.setze(modus='aus')                                                             # aus: keine Prüfung, kein Zähler
        self.assertEqual(self.anfrage(ziel='/.env', antwort=404), (None, ''))
        self.assertEqual(self.anfrage(ip=IP('198.51.100.9')), (None, ''))
        self.assertEqual(schutz.abwehr(self.db)['treffer']['falle'], 5)
        self.setze(modus='abweisen')
        self.assertEqual(self.anfrage(), (None, ''))                                        # vorgemerkte automatische Sperren gelten nie nachträglich
        self.assertEqual(self.anfrage(ip=IP('198.51.100.9'))[0], 403)                       # Sperren von Hand bleiben
        e = sicherheit.ereignisse(self.db, art='schutz_frei')[0]
        self.assertEqual((e['person'], e['detail']), ('Erste', {'anzahl': 1, 'art': 'automatisch', 'grund': 'Firewall auf „abweisen“ gestellt'}))
        for _ in range(5):
            self.anfrage(ziel='/.env')
        self.setze(modus='abweisen', schwelle=6)                                            # bleibt „abweisen“: Sperren bleiben
        self.assertEqual(self.anfrage()[0], 403)

    def test_inhalt_nur_ohne_anmeldung_und_nie_passwoerter(self):
        self.assertEqual(schutz.inhalt(self.pfad, AUSSEN, {'name': "admin' OR '1'='1", 'password': 'x'}), (403, 'Angriffsmuster: SQL-Einschleusung'))
        self.assertEqual(schutz.inhalt(self.pfad, AUSSEN, {'a': [{'b': {'<script>alert(1)</script>': 1}}]})[0], 403)   # auch Schlüssel und verschachtelt
        for daten in ({'name': 'erika.muster', 'password': "x' OR '1'='1 <script> ${jndi:ldap://x}"}, {'current': 'a;drop table x', 'new': '../../etc/passwd', 'repeat': '<?php'},
                      {'name': 'Müller & Söhne', 'code': 'javascript:1', 'token': 'union select 1'}, {}, {'n': 5, 'x': None, 'l': [1, 2.5, True]}):
            self.assertEqual(schutz.inhalt(self.pfad, AUSSEN, daten), (None, ''))
        self.assertEqual(schutz.inhalt(self.pfad, IP('127.0.0.1'), {'name': '<script>'}), (None, ''))
        # Verwendungszwecke angemeldeter Personen prüft niemand: app.py ruft `inhalt` nur ohne Sitzung (siehe SchutzHttpTests)
        quelle = Path(app.__file__).read_text(encoding='utf-8')
        self.assertEqual(quelle.count('self.abwehr(data)'), 1)
        self.assertIn('if s is None and self.abwehr(data): return', quelle)

    def test_verkehr_nur_bei_auffaelligkeiten_oder_mitschnitt(self):
        self.anfrage(ziel='/api/data?year=2026')
        self.assertEqual(schutz.status(self.db)['verkehr'], [])                             # normale Anfragen: nichts
        self.anfrage(ziel='/wp-login.php?log=admin&pwd=geheim')
        self.anfrage(ziel='/wp-login.php?log=root&pwd=geheim2')
        self.anfrage(ziel='/nichts/\x1b[31mda', antwort=404)
        v = schutz.status(self.db)['verkehr']
        self.assertEqual([(x['pfad'], x['status'], x['art'], x['n']) for x in v], [('/nichts/[31mda', 404, 'Fehlerantwort', 1), ('/wp-login.php', 404, 'Falle', 2)])
        self.assertEqual(set(v[0]), {'zeit', 'adresse', 'methode', 'pfad', 'status', 'art', 'n'})   # nie Inhalte, Sitzungen, Personen
        self.assertNotIn('geheim', json.dumps(v))
        bis = schutz.mitschnitt(self.db, True, 'Erste')
        self.assertEqual(bis, datetime.fromtimestamp(self.jetzt[0] + 900).isoformat(timespec='seconds'))   # höchstens 15 Minuten
        self.anfrage(ziel='/api/data?year=2026', angemeldet=True)
        self.assertEqual([(x['pfad'], x['art']) for x in schutz.status(self.db)['verkehr']][0], ('/api/data', ''))
        self.assertEqual(schutz.status(self.db)['mitschnitt_bis'], bis)
        self.weiter(901)
        self.anfrage(ziel='/api/kasse')
        self.assertEqual(len(schutz.status(self.db)['verkehr']), 3)
        self.assertEqual(schutz.status(self.db)['mitschnitt_bis'], '')
        schutz.mitschnitt(self.db, True, 'Erste')
        self.assertEqual(schutz.mitschnitt(self.db, False, 'Erste'), '')
        self.anfrage(ziel='/api/kasse')
        self.assertEqual(len(schutz.status(self.db)['verkehr']), 3)
        self.assertEqual(self.arten().count('schutz_mitschnitt'), 2)
        for i in range(schutz.VERKEHR_MAX + 50):                                            # Ringpuffer
            schutz.nachher(self.pfad, AUSSEN, 'GET', f'/x{i}', 404, 'Falle')
        self.assertEqual(len(schutz._VERKEHR), schutz.VERKEHR_MAX)
        self.assertEqual(len(schutz.status(self.db)['verkehr']), 200)

    def test_abwehr_der_letzten_24_stunden(self):
        self.anfrage(ziel='/.env')
        self.anfrage(agent='sqlmap')
        self.anfrage(ziel='/?x=<script>')
        self.weiter(3600)
        self.setze(schwelle=1)
        self.anfrage(ip=IP('198.51.100.1'), ziel='/.env')
        for _ in range(3):
            self.anfrage(ip=IP('198.51.100.1'))
        a = schutz.abwehr(self.db)
        self.assertEqual(a['treffer'], {'falle': 2, 'werkzeug': 1, 'muster': 1, 'fehler': 0})
        self.assertEqual((a['treffer_gesamt'], a['abgewiesen'], a['sperren'], a['gesperrt'], a['modus']), (4, 3, 1, 1, 'abweisen'))
        self.assertEqual(len(a['stunden']), 24)
        self.assertEqual(a['stunden'][-1], {'stunde': datetime.fromtimestamp(self.jetzt[0]).strftime('%H'), 'treffer': 1, 'abgewiesen': 3, 'sperren': 1})
        self.assertEqual(a['stunden'][-2]['treffer'], 3)
        self.weiter(25 * 3600)
        self.assertEqual(schutz.abwehr(self.db)['treffer_gesamt'], 0)                       # älter als 24 Stunden

    def test_1000_normale_anfragen_ohne_schreibzugriff(self):
        self.anfrage()                                                                      # Stand einmal laden
        vorher = self.db.execute('PRAGMA data_version').fetchone()[0]
        ziele = ('/', '/js/main.js', '/api/status', '/api/data?year=2026', '/api/invoices.pdf?ids=' + ','.join(map(str, range(200))))
        with unittest.mock.patch.object(schutz.sqlite3, 'connect', side_effect=AssertionError('Datenbankzugriff bei normaler Anfrage')) as verbinden:
            start = time.perf_counter()
            for i in range(1000):
                self.assertEqual(self.anfrage(ip=IP(f'198.51.100.{i % 250}'), ziel=ziele[i % 5], angemeldet=i % 2 == 0), (None, ''))
            dauer = time.perf_counter() - start
        self.assertEqual(verbinden.call_count, 0)                                           # weder lesen noch schreiben
        self.assertEqual(self.db.execute('PRAGMA data_version').fetchone()[0], vorher)      # nichts von außen geschrieben
        self.assertEqual((len(schutz._VERKEHR), schutz._ZAEHLER, schutz._STATISTIK), (0, {}, {}))
        self.assertLess(dauer, 2)                                                           # üblich: unter 0,1 s

    def test_aenderung_von_aussen_wirkt_nach_kurzer_zeit(self):
        self.anfrage()
        con = sqlite3.connect(self.pfad)                                                    # anderer Prozess (verwaltung.py)
        con.execute("INSERT INTO schutz_sperren(netz,art,bis,zeit) VALUES('203.0.113.50/32','hand',0,0)")
        con.commit()
        con.close()
        self.assertEqual(self.anfrage()[0], None)
        self.weiter(schutz.NACHLADEN + 1)
        self.assertEqual(self.anfrage()[0], 403)

    def test_ohne_datenbank_gelten_die_vorgaben(self):
        self.assertEqual(schutz.eingang(Path(self.tmp.name) / 'fehlt.sqlite3', AUSSEN, '/.env'), (404, 'Falle'))
        self.assertEqual(schutz.eingang(Path(self.tmp.name) / 'fehlt.sqlite3', AUSSEN, '/'), (None, ''))


class BedrohungslisteTests(Basis):
    def lade(self, quellen=None):
        with unittest.mock.patch.object(schutz, 'holen', lambda url: (quellen or drop_fixture())[url]):
            return schutz.liste_laden(self.db, 'Erste')

    def test_parser(self):
        netze = schutz.liste_lesen(drop_fixture()[schutz.QUELLEN[0]].decode())
        self.assertEqual((len(netze), str(netze[0]), str(netze[-1])), (66, '192.0.2.0/30', '198.51.100.64/26'))
        self.assertEqual(schutz.liste_lesen('; nur Kommentar\n\n   \n'), [])
        for kaputt in ('kein-netz ; SBL1', '192.0.2.1/30 ; SBL1', '192.0.2.0/33', '<html>Fehler</html>', '192.0.2.0/24 SBL1'):   # Hostbits, Unsinn, HTML-Fehlerseite
            with self.subTest(kaputt=kaputt), self.assertRaisesRegex(ValueError, 'unlesbare Zeile'):
                schutz.liste_lesen('192.0.2.0/30 ; SBL1\n' + kaputt)
        for unplausibel in ('0.0.0.0/0', '10.0.0.0/8', '127.0.0.0/8', '100.64.0.0/10', '192.168.1.0/24', '96.0.0.0/3', '::/0', 'fd7a:115c:a1e0::/48', '::1/128', '2000::/3',
                            '192.0.0.0/9', '2001::/23'):                                                  # größer als /10 bzw. /24
            with self.subTest(netz=unplausibel), self.assertRaisesRegex(ValueError, 'unplausibles Netz'):
                schutz.liste_lesen(unplausibel + ' ; SBL1')

    def test_laden_und_treffer_an_netzgrenzen(self):
        info = self.lade()
        self.assertEqual((info['anzahl'], info['veraltet'], info['quelle']), (68, False, 'Spamhaus DROP'))
        for ip, gelistet in (('192.0.2.0', True), ('192.0.2.255', True), ('192.0.1.255', False), ('192.0.3.0', False),
                             ('198.51.100.15', False), ('198.51.100.16', True), ('198.51.100.31', True), ('198.51.100.32', False),
                             ('198.51.100.63', False), ('198.51.100.64', True), ('198.51.100.127', True), ('198.51.100.128', False),
                             ('2001:db8:dead::', True), ('2001:db8:dead:ffff:ffff:ffff:ffff:ffff', True), ('2001:db8:deae::', False),
                             ('2001:db8:dead::1', True), ('2001:db8:deac:ffff:ffff:ffff:ffff:ffff', False),
                             ('2001:db8:0:1::', True), ('2001:db8:0:1:ffff:ffff:ffff:ffff', True), ('2001:db8:0:2::', False), ('2001:db8:0:0:ffff:ffff:ffff:ffff', False)):
            with self.subTest(ip=ip):
                self.assertEqual(self.anfrage(ip=IP(ip)), (403, 'gesperrt (Bedrohungsliste)') if gelistet else (None, ''))
        self.assertEqual(self.anfrage(ip=IP('192.0.2.7'), angemeldet=True), (None, ''))     # gültige Sitzung
        self.setze(liste=False)
        self.assertEqual(self.anfrage(ip=IP('192.0.2.7')), (None, ''))                      # ausgeschaltet
        self.assertEqual(sicherheit.ereignisse(self.db, art='schutz_liste')[0]['detail'], {'anzahl': 68})
        self.assertEqual(schutz.abwehr(self.db)['abgewiesen'], 11)

    def test_fehler_lassen_die_alte_liste(self):
        self.lade()
        stand = schutz.liste_info(self.db)
        gut = drop_fixture()
        faelle = {
            'unlesbare Zeile': {**gut, schutz.QUELLEN[1]: b'2001:db8:dead::/48 ; SBL1\nkaputt'},
            'zu groß': {**gut, schutz.QUELLEN[0]: b'; ' + b'x' * schutz.LISTE_BYTES},
            'unvollständig': {schutz.QUELLEN[0]: b'192.0.2.0/24 ; SBL1\n' * 10, schutz.QUELLEN[1]: b''},
            'unplausibles Netz': {**gut, schutz.QUELLEN[0]: gut[schutz.QUELLEN[0]] + b'\n0.0.0.0/1 ; SBL9'},
            'kein gültiger Text': {**gut, schutz.QUELLEN[0]: b'\xff\xfe\x00'},
        }
        for text, quellen in faelle.items():
            with self.subTest(fall=text), self.assertRaisesRegex(ValueError, text):
                self.lade(quellen)
        with unittest.mock.patch.object(schutz, 'LISTE_NETZE', (1, 60)), self.assertRaisesRegex(ValueError, 'unplausibel'):
            self.lade()                                                                     # zu viele Netze
        flaeche = 256 + 16 + 64                                                             # IPv4-Adressen der erfundenen Liste
        with unittest.mock.patch.object(schutz, 'LISTE_IPV4_ADRESSEN', flaeche - 1), self.assertRaisesRegex(ValueError, 'unplausibel'):
            self.lade()                                                                     # deckt zu viele Adressen ab
        with unittest.mock.patch.object(schutz, 'LISTE_IPV4_ADRESSEN', flaeche):
            self.assertEqual(self.lade()['anzahl'], 68)
        stand = schutz.liste_info(self.db)
        for fehler in (OSError('keine Verbindung'), TimeoutError(), schutz.urllib.error.URLError('dns'), schutz.http.client.IncompleteRead(b'')):
            with unittest.mock.patch.object(schutz, 'holen', side_effect=fehler), self.assertRaisesRegex(ValueError, 'konnte nicht geladen werden'):
                schutz.liste_laden(self.db)
        self.assertEqual(schutz.liste_info(self.db), stand)
        self.assertEqual(self.anfrage(ip=IP('192.0.2.7'))[0], 403)                          # die alte Liste gilt weiter
        self.assertEqual(len(sicherheit.ereignisse(self.db, art='schutz_liste')), 2)

    def test_abruf_nur_https_ohne_weiterleitung(self):
        with self.assertRaisesRegex(ValueError, 'nur über https'):
            ECHTES_HOLEN('http://www.example.org/drop.txt')                                 # bricht vor jedem Abruf ab
        self.assertTrue(all(q.startswith('https://www.spamhaus.org/drop/') for q in schutz.QUELLEN))
        # den Opener, den holen() baut, gegen einen Server auf 127.0.0.1 mit Weiterleitung schicken (statt ins Netz)
        import http.server
        import threading
        aufgerufen = []

        class Weiterleitung(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                aufgerufen.append(self.path)
                self.send_response(302)
                self.send_header('Location', '/ziel')
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *a):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Weiterleitung)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        echt, gebaut = schutz.urllib.request.build_opener, []

        def bauen(*handler):
            gebaut.append(echt(*handler))
            return unittest.mock.Mock(open=unittest.mock.Mock(side_effect=OSError('kein Netz im Test')))
        with unittest.mock.patch.object(schutz.urllib.request, 'build_opener', bauen), self.assertRaises(OSError):
            ECHTES_HOLEN('https://www.example.org/drop.txt')
        with self.assertRaises(schutz.urllib.error.HTTPError) as fehler:
            gebaut[0].open(f'http://127.0.0.1:{server.server_port}/drop.txt', timeout=5)
        self.assertEqual((fehler.exception.code, aufgerufen), (302, ['/drop.txt']))           # der Weiterleitung wird nie gefolgt

    def test_abruf_mit_gesamtfrist_und_groessengrenze(self):
        class Antwort:
            """Antwort, die tröpfelt: jedes Stück kostet Zeit auf der Uhr der Abwehr."""
            def __init__(antwort, stueck, sekunden, gesamt):
                antwort.stueck, antwort.sekunden, antwort.rest, antwort.lesen = stueck, sekunden, gesamt, 0

            def __enter__(antwort):
                return antwort

            def __exit__(antwort, *a):
                return False

            def read1(antwort, n=-1):
                antwort.lesen += 1
                self.weiter(antwort.sekunden)
                teil = antwort.stueck[:max(0, min(n if n > 0 else len(antwort.stueck), antwort.rest))]
                antwort.rest -= len(teil)
                return teil
            read = read1

        def holen(antwort):
            opener = unittest.mock.Mock(open=unittest.mock.Mock(return_value=antwort))
            with unittest.mock.patch.object(schutz.urllib.request, 'build_opener', return_value=opener):
                return ECHTES_HOLEN(schutz.QUELLEN[0])
        self.assertEqual(holen(Antwort(b'192.0.2.0/30 ; SBL1\n', 0.01, 20 * 3)), b'192.0.2.0/30 ; SBL1\n' * 3)
        langsam = Antwort(b'1', 1, 10 ** 9)                                                 # ein Byte je Sekunde, ohne Ende
        with self.assertRaises(TimeoutError):
            holen(langsam)
        self.assertLessEqual(langsam.lesen, schutz.HOLEN_FRIST + 1)                         # nach der Gesamtfrist abgebrochen
        riesig = Antwort(b'x' * 65536, 0, 10 ** 12)
        self.assertEqual(len(holen(riesig)), schutz.LISTE_BYTES + 1)                       # nie mehr als die Grenze gelesen
        self.assertLessEqual(riesig.lesen, schutz.LISTE_BYTES // 65536 + 2)
        with unittest.mock.patch.object(schutz, 'holen', lambda url: holen(Antwort(b'1', 1, 10 ** 9))), self.assertRaisesRegex(ValueError, 'konnte nicht geladen werden'):
            schutz.liste_laden(self.db)                                                     # der Stundenlauf hängt nicht

    def test_stuendlich_laedt_taeglich(self):
        with unittest.mock.patch.object(schutz, 'holen', lambda url: drop_fixture()[url]):
            self.assertTrue(schutz.stuendlich(self.db))                                     # noch keine Liste
            self.assertFalse(schutz.stuendlich(self.db))                                    # frisch
            alt = (datetime.now() - timedelta(hours=25)).isoformat(timespec='seconds')
            with self.db:
                sicherheit._schreiben(self.db, 'bedrohungsliste_stand', {'stand': alt, 'anzahl': 68})
            self.assertTrue(schutz.stuendlich(self.db))                                     # älter als ein Tag
        self.setze(liste=False)
        with self.db:
            sicherheit._schreiben(self.db, 'bedrohungsliste_stand', {'stand': alt, 'anzahl': 68})
        self.assertFalse(schutz.stuendlich(self.db))                                        # ausgeschaltet: kein Abruf (holen = kein_netz)
        with self.db:
            sicherheit._schreiben(self.db, 'bedrohungsliste_stand', {'stand': (datetime.now() - timedelta(days=4)).isoformat(), 'anzahl': 68})
        self.assertTrue(schutz.liste_info(self.db)['veraltet'])                             # älter als 3 Tage

    def test_eigene_adresse_auf_der_liste(self):
        self.setze(liste=False)
        self.lade()
        with self.assertRaisesRegex(ValueError, 'steht auf der Bedrohungsliste'):
            schutz.einstellungen_speichern(self.db, {'liste': True}, 'Erste', eigene=IP('192.0.2.7'))
        self.assertFalse(schutz.einstellungen(self.db)['liste'])
        self.assertTrue(schutz.status(self.db, IP('192.0.2.7'))['eigene_gelistet'])
        schutz.einstellungen_speichern(self.db, {'liste': True, 'nie': ['192.0.2.7']}, 'Erste', eigene=IP('192.0.2.7'))
        self.assertEqual(self.anfrage(ip=IP('192.0.2.7')), (None, ''))                      # „nie sperren“ geht vor
        self.assertTrue(schutz.status(self.db, IP('192.0.2.7'))['geschuetzt'])
        self.assertEqual(self.anfrage(ip=IP('192.0.2.8'))[0], 403)


class NotfallUndVerwaltungTests(Basis):
    def test_notfall_vorlage_und_rueckkehr(self):
        self.setze(modus='protokollieren', schwelle=7, sperrminuten=360, fehler=40, liste=False, auto=False, nie=['203.0.113.9'])
        schutz.notfall(self.db, True)
        e = schutz.einstellungen(self.db)
        self.assertEqual({k: e[k] for k in schutz.NOTFALL}, {'modus': 'abweisen', 'auto': True, 'schwelle': 1, 'sperrminuten': 10080, 'fehler': 10, 'liste': True})
        self.assertEqual(e['nie'], ['203.0.113.9/32'])                                      # „nie sperren“ bleibt
        self.assertTrue(schutz.status(self.db)['notfall_vorlage'])
        self.anfrage(ziel='/.env')
        self.assertEqual(self.anfrage()[0], 403)                                            # Sperre ab dem ersten Treffer
        self.assertEqual(self.db.execute('SELECT bis FROM schutz_sperren').fetchone()[0], int(self.jetzt[0]) + 7 * 86400)
        self.setze(fehler=20, nie=['203.0.113.9', '198.51.100.1'])                          # während des Notfalls von Hand geändert
        schutz.notfall(self.db, False)
        e = schutz.einstellungen(self.db)
        self.assertEqual((e['modus'], e['schwelle'], e['sperrminuten'], e['liste'], e['auto']), ('protokollieren', 7, 360, False, False))
        self.assertEqual((e['fehler'], e['nie']), (20, ['198.51.100.1/32', '203.0.113.9/32']))   # bleibt, wie von Hand gesetzt
        self.assertFalse(schutz.status(self.db)['notfall_vorlage'])
        schutz.notfall(self.db, False)                                                      # ohne gemerkten Stand: ändert nichts
        self.assertEqual(schutz.einstellungen(self.db), e)

    def test_verwaltungsbefehl_schutz_aus(self):
        self.setze(schwelle=1)
        self.anfrage(ziel='/.env')
        schutz.sperren(self.db, '198.51.100.0/24', 'Test', 0, 'Erste')
        self.assertEqual(self.anfrage()[0], 403)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(verwaltung.main(['--data', self.tmp.name, 'schutz-aus']), 0)
        self.assertIn('2 Sperre(n) gelöscht', out.getvalue())
        self.assertNotIn('203.0.113', out.getvalue())                                       # nur Anzahlen
        self.assertEqual(schutz.einstellungen(self.db)['modus'], 'protokollieren')
        self.assertEqual(self.db.execute('SELECT count(*) FROM schutz_sperren').fetchone()[0], 0)
        e = sicherheit.ereignisse(self.db, art='schutz_aus')[0]
        self.assertEqual((e['person'], e['detail']), ('Serververwaltung', {'sperren': 2}))
        self.weiter(schutz.NACHLADEN + 1)                                                   # der laufende Dienst merkt es nach wenigen Sekunden
        self.assertEqual(self.anfrage(), (None, ''))
        self.assertEqual(self.anfrage(ip=IP('198.51.100.5')), (None, ''))
        with redirect_stdout(io.StringIO()):                                                # wiederholbar
            self.assertEqual(verwaltung.main(['--data', self.tmp.name, 'schutz-aus']), 0)

    def test_scharfstellen_vergisst_vorgemerkte_sperren_und_zaehler(self):
        self.setze(modus='protokollieren')
        for _ in range(5):
            self.anfrage(ziel='/.env')                                                      # vorgemerkt: Sperre und vier Treffer im Zähler
        for _ in range(4):
            self.anfrage(ip=IP('198.51.100.8'), ziel='/.env')
        self.assertEqual(self.db.execute('SELECT count(*) FROM schutz_sperren').fetchone()[0], 1)
        schutz.notfall(self.db, True)                                                       # stellt auf „abweisen“
        self.assertEqual(self.db.execute('SELECT count(*) FROM schutz_sperren').fetchone()[0], 0)
        self.assertEqual(self.anfrage(), (None, ''))                                        # wer nur vorgemerkt war, ist nicht ausgesperrt
        schutz.notfall(self.db, False)
        self.setze(modus='abweisen')
        self.anfrage(ip=IP('198.51.100.8'), ziel='/.env')                                   # die vier alten Treffer zählen nicht mehr mit
        self.assertEqual(self.anfrage(ip=IP('198.51.100.8')), (None, ''))
        self.setze(schwelle=6)                                                              # andere Einstellung, gleicher Modus: nichts wird gelöscht
        for _ in range(5):
            self.anfrage(ip=IP('198.51.100.8'), ziel='/.env')
        self.assertEqual(self.anfrage(ip=IP('198.51.100.8'))[0], 403)
        self.setze(schwelle=7)
        self.assertEqual(self.anfrage(ip=IP('198.51.100.8'))[0], 403)

    def test_notfall_aus_auf_dem_server_bringt_die_firewall_zurueck(self):
        self.setze(schwelle=9)
        sicherheit.notfall_an(self.db, 'Erste', 'Übung mit erfundenem Verdacht')
        schutz.notfall(self.db, True)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(verwaltung.main(['--data', self.tmp.name, 'notfall-aus']), 0)
        self.assertEqual(schutz.einstellungen(self.db)['schwelle'], 9)


class ServerschutzTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.datei = Path(self.tmp.name) / 'status.json'
        self.jetzt = datetime(2026, 11, 19, 12, 0, 0)

    def bericht(self, daten=None, roh=None, **kw):
        self.datei.write_bytes(roh if roh is not None else json.dumps(daten).encode())
        return schutz.serverschutz(self.datei, self.jetzt, **kw)

    def gut(self, **mehr):
        return {'zeit': '2026-11-19T11:50:00', 'version': 1,
                'pruefungen': [{'id': 'firewall', 'titel': 'Firewall aktiv', 'stand': 'ok', 'detail': 'nftables, Standard: verwerfen'},
                               {'id': 'ssh', 'titel': 'SSH nur mit Schlüssel', 'stand': 'kritisch', 'detail': 'Passwort-Anmeldung ist erlaubt'}],
                'zahlen': {'sperrliste_netze': 1837, 'sperrliste_stand': '2026-11-19T04:00:00', 'gebannt': 3, 'abgewiesen_24h': 412, 'updates_offen': 0, 'neustart_noetig': False},
                **mehr}

    def test_fehlt(self):
        r = schutz.serverschutz(Path(self.tmp.name) / 'fehlt.json')
        self.assertEqual((r['vorhanden'], r['aktuell'], r['hinweis'], r['pruefungen'], r['zahlen']), (False, False, 'Kein aktueller Bericht vom Server.', [], {}))
        self.assertFalse(schutz.serverschutz(self.tmp.name)['vorhanden'])                    # ein Ordner ist keine Datei

    def test_aktuell(self):
        r = self.bericht(self.gut())
        self.assertEqual((r['vorhanden'], r['aktuell'], r['hinweis'], r['zeit']), (True, True, '', '2026-11-19T11:50:00'))
        self.assertEqual([p['stand'] for p in r['pruefungen']], ['ok', 'kritisch'])
        self.assertEqual(r['zahlen'], self.gut()['zahlen'])

    def test_pfad_aus_der_umgebung(self):
        self.datei.write_text(json.dumps(self.gut(zeit=datetime.now().isoformat(timespec='seconds'))))
        with unittest.mock.patch.dict(os.environ, {'JU_FINANZEN_SERVERSCHUTZ': str(self.datei)}):
            self.assertTrue(schutz.serverschutz()['aktuell'])
        with unittest.mock.patch.dict(os.environ, {'JU_FINANZEN_SERVERSCHUTZ': ''}), unittest.mock.patch.object(schutz, 'SERVERSCHUTZ', str(self.datei)):
            self.assertTrue(schutz.serverschutz()['aktuell'])
        self.assertEqual(schutz.SERVERSCHUTZ, '/var/lib/ju-serverschutz/status.json')

    def test_alt_und_zeitzone(self):
        r = self.bericht(self.gut(zeit='2026-11-19T10:59:59'))
        self.assertEqual((r['vorhanden'], r['aktuell'], r['hinweis']), (True, False, 'Kein aktueller Bericht vom Server.'))
        self.assertEqual(len(r['pruefungen']), 2)                                            # der alte Stand bleibt sichtbar
        self.assertTrue(self.bericht(self.gut(zeit='2026-11-19T11:00:00'))['aktuell'])       # genau 60 Minuten
        self.assertFalse(self.bericht(self.gut(zeit='2026-11-19T12:06:00'))['aktuell'])      # aus der Zukunft
        ortszeit = datetime.now().astimezone()                                               # mit Zeitzone, wie `date -Is`
        self.datei.write_text(json.dumps(self.gut(zeit=ortszeit.isoformat(timespec='seconds'))))
        self.assertTrue(schutz.serverschutz(self.datei)['aktuell'])
        self.datei.write_text(json.dumps(self.gut(zeit=(ortszeit - timedelta(minutes=61)).isoformat(timespec='seconds'))))
        self.assertFalse(schutz.serverschutz(self.datei)['aktuell'])

    def test_kaputt_und_uebergross(self):
        for roh in (b'', b'{kaputt', b'[1, 2]', b'"text"', b'null', b'{"version": 1}', b'{"zeit": 5, "version": 1}', b'{"zeit": "gestern", "version": 1}',
                    b'\xff\xfe', b'[' * 5000, json.dumps(self.gut()).encode() + b' ' * schutz.SERVERSCHUTZ_BYTES):
            with self.subTest(roh=roh[:20]):
                r = self.bericht(roh=roh)
                self.assertEqual((r['vorhanden'], r['aktuell'], r['hinweis'], r['pruefungen'], r['zahlen']), (True, False, 'Der Bericht vom Server ist nicht lesbar.', [], {}))
        self.assertEqual(self.bericht(self.gut(version=2))['hinweis'], 'Der Bericht vom Server hat ein unbekanntes Format.')

    def test_nur_erwartete_felder_und_gekuerzte_texte(self):
        r = self.bericht(self.gut(
            geheim='nicht anzeigen',
            pruefungen=[{'id': 'x' * 100, 'titel': 'T' * 200, 'stand': 'super', 'detail': '<script>alert(1)</script>\n\x1b[31mrot\x00 ' + 'd' * 500, 'extra': 1},
                        'kein Objekt', {'titel': 7, 'stand': ['ok']}, *[{'id': str(i), 'titel': 'viele', 'stand': 'ok', 'detail': ''} for i in range(50)]],
            zahlen={'sperrliste_netze': '1837', 'sperrliste_stand': 'kein Datum', 'gebannt': -1, 'abgewiesen_24h': True, 'updates_offen': 4,
                    'neustart_noetig': 'ja', 'passwort': 'geheim', 'riesig': 10 ** 30}))
        self.assertEqual(set(r), {'vorhanden', 'aktuell', 'hinweis', 'zeit', 'pruefungen', 'zahlen'})
        self.assertEqual(len(r['pruefungen']), 29)                                           # höchstens 30 gelesen, „kein Objekt“ fällt weg
        erste = r['pruefungen'][0]
        self.assertEqual((len(erste['id']), len(erste['titel']), erste['stand'], len(erste['detail'])), (40, 80, 'hinweis', 200))
        self.assertTrue(erste['detail'].startswith('<script>alert(1)</script> [31mrot'))     # bleibt Text – die Oberfläche maskiert (html``)
        self.assertEqual(r['pruefungen'][1], {'id': '', 'titel': '7', 'stand': 'hinweis', 'detail': ''})
        self.assertEqual(r['zahlen'], {'updates_offen': 4})
        self.assertNotIn('geheim', json.dumps(r))


class PruefungTests(Basis):
    def pruefung(self, bericht=None, **kw):
        lage = sicherheit.lagebild(self.db, [], [], kw.pop('sicherung', datetime.now().date().isoformat()), self.tmp.name)
        liste = schutz.pruefung(self.db, lage, bericht or schutz.serverschutz(Path(self.tmp.name) / 'fehlt.json'), **kw)
        return {p['id']: p for p in liste}

    def test_liste(self):
        p = self.pruefung()
        self.assertEqual(list(p), ['keycloak', 'authenticator', 'notzugang', 'firewall', 'aufrufer', 'bedrohungsliste', 'dateipruefung', 'sicherung', 'https',
                                   'ungenutzt', 'verwaltung', 'serverschutz'])
        for eintrag in p.values():
            self.assertEqual(set(eintrag), {'id', 'titel', 'stand', 'detail', 'ziel', 'aktion'})
            self.assertIn(eintrag['stand'], schutz.STAENDE)
            self.assertTrue(eintrag['titel'] and eintrag['detail'])
        staende = {k: v['stand'] for k, v in p.items()}
        self.assertEqual(staende, {'keycloak': 'hinweis', 'authenticator': 'hinweis', 'notzugang': 'ok', 'firewall': 'ok', 'aufrufer': 'ok', 'bedrohungsliste': 'hinweis',
                                   'dateipruefung': 'hinweis', 'sicherung': 'ok', 'https': 'kritisch', 'ungenutzt': 'hinweis', 'verwaltung': 'hinweis',
                                   'serverschutz': 'hinweis'})
        self.assertEqual((p['firewall']['ziel'], p['serverschutz']['ziel'], p['verwaltung']['ziel']), ('abwehr', 'server', 'seite:users'))
        self.assertIn('Kein aktueller Bericht', p['serverschutz']['detail'])

    def test_unlesbare_aufruferadresse_ist_kritisch(self):
        self.assertEqual(schutz.aufrufer('127.0.0.1', 'unknown'), IP('127.0.0.1'))
        a = self.pruefung()['aufrufer']
        self.assertEqual((a['stand'], a['ziel']), ('kritisch', 'abwehr'))
        self.assertIn('Aufruferadresse nicht lesbar oder lokal – Abwehr für diese Anfragen wirkungslos (Proxy-Einstellung prüfen)', a['detail'])
        self.weiter(86401)
        self.assertEqual(self.pruefung()['aufrufer']['stand'], 'ok')                       # nur, wenn es in den letzten 24 Stunden vorkam

    def test_alles_in_ordnung(self):
        rollen.save_user(self.db, test_rollen.P('Zweite', password=PASSWORT, roles=[r[0] for r in self.db.execute("SELECT id FROM roles WHERE name='Administrator'")]), 'Erste')
        self.db.execute('UPDATE users SET totp_pflicht=1')
        self.db.commit()
        for name in ('Erste', 'Zweite'):
            sicherheit.ereignis(self.db, 'anmeldung', name)
        with unittest.mock.patch.object(schutz, 'holen', lambda url: drop_fixture()[url]):
            schutz.liste_laden(self.db)
        bericht = {'vorhanden': True, 'aktuell': True, 'hinweis': '', 'zeit': '', 'zahlen': {}, 'pruefungen': [{'id': 'a', 'titel': 'A', 'stand': 'ok', 'detail': ''}]}
        p = self.pruefung(bericht, keycloak=True, https=True)
        self.assertEqual({k: v['stand'] for k, v in p.items() if v['stand'] != 'ok'}, {'dateipruefung': 'hinweis'})   # ohne Prüfliste (kein Server)
        bericht['pruefungen'].append({'id': 'b', 'titel': 'B', 'stand': 'kritisch', 'detail': ''})
        self.setze(modus='aus')
        sicherheit.notzugang_setzen(self.db, 30, 'Serververwaltung')
        p = self.pruefung(bericht, keycloak=True, https=True, sicherung=(datetime.now() - timedelta(days=4)).date().isoformat())
        self.assertEqual({k: v['stand'] for k, v in p.items() if v['stand'] != 'ok'},
                         {'notzugang': 'hinweis', 'firewall': 'kritisch', 'dateipruefung': 'hinweis', 'sicherung': 'kritisch', 'serverschutz': 'kritisch'})
        bis = datetime.fromisoformat(sicherheit.notzugang_offen(self.db)['bis'])
        self.assertIn(f"offen bis {bis:%d.%m.%Y, %H:%M} Uhr", p['notzugang']['detail'])         # Ende des Notzugangs steht im Text
        self.setze(modus='protokollieren', liste=False)
        p = self.pruefung(bericht, keycloak=True, https=True)
        self.assertEqual((p['firewall']['stand'], p['bedrohungsliste']['stand']), ('hinweis', 'hinweis'))
        self.assertFalse(p['bedrohungsliste']['aktion'])                                    # ausgeschaltet: „Jetzt laden“ behebt es nicht


class SchutzHttpTests(unittest.TestCase):
    """Am laufenden Server: der eine Einstieg vor jeder Anfrage, Rechte der Wege, Bestätigung, Schutz vor Aussperrung."""
    setUpClass = classmethod(test_server.ServerHttpTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerHttpTests.tearDownClass.__func__)
    call = test_server.ServerHttpTests.call
    login = test_rollen.RollenHttpTests.login
    get = test_rollen.RollenHttpTests.get
    post = test_rollen.RollenHttpTests.post
    person = test_rollen.RollenHttpTests.person
    VON = {'X-Forwarded-For': '203.0.113.50'}                                              # wie vom lokalen Proxy weitergereicht

    def setUp(self):
        schutz.vergessen(alles=True)
        self.addCleanup(schutz.vergessen, True)
        patch = unittest.mock.patch.object(schutz, 'holen', kein_netz)
        patch.start()
        self.addCleanup(patch.stop)
        test_rollen.RollenHttpTests.setUp(self)

    def roh(self, path, data=None, who=None, **headers):
        status, body, _ = self.call(path, data, cookie=who and who['cookie'], csrf=who and who['csrf'], headers=headers)
        return status, body

    def aussen(self, path, data=None, who=None):
        return self.roh(path, data, who, **self.VON)

    def seite(self, path, **headers):
        """Statische Datei bzw. unbekannter Pfad (kein JSON): nur der Status."""
        req = test_server.urllib.request.Request(self.url + path, headers={'Host': 'ju-finanzen.beispiel.ts.net', **headers})
        try:
            with test_server.urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except test_server.urllib.error.HTTPError as e:
            return e.code, e.read()

    def zeilen(self, methode, path, *kopf):
        """Echte HTTP-Anfrage mit frei wählbaren (auch doppelten) Kopfzeilen; liefert Status und Körper."""
        verbindung = schutz.http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            verbindung.putrequest(methode, path, skip_host=True, skip_accept_encoding=True)
            for name, wert in (('Host', 'ju-finanzen.beispiel.ts.net'), *kopf):
                verbindung.putheader(name, wert)
            verbindung.endheaders()
            antwort = verbindung.getresponse()
            return antwort.status, antwort.read()
        finally:
            verbindung.close()

    def test_mehrere_weitergereichte_kopfzeilen_zaehlt_die_des_proxys(self):
        angreifer, opfer = '198.51.100.66', '203.0.113.77'
        for _ in range(5):   # erste Zeile schreibt der Aufrufer selbst, die zweite hängt der Proxy an
            self.assertEqual(self.zeilen('GET', '/.env', ('X-Forwarded-For', '127.0.0.1'), ('X-Forwarded-For', angreifer))[0], 404)
        self.assertEqual(self.zeilen('GET', '/', ('X-Forwarded-For', angreifer))[0], 403)       # gesperrt wird die echte Adresse
        for _ in range(5):
            self.zeilen('GET', '/.env', ('X-Forwarded-For', opfer), ('X-Forwarded-For', '198.51.100.67'))
        self.assertEqual(self.zeilen('GET', '/', ('X-Forwarded-For', opfer))[0], 200)           # niemand lässt eine fremde Adresse sperren
        self.assertEqual(self.zeilen('GET', '/', ('X-Forwarded-For', '198.51.100.67'))[0], 403)
        self.assertEqual(self.zeilen('GET', '/', ('X-Forwarded-For', '203.0.113.5:51234'))[0], 200)   # Port des Proxys stört nicht

    def test_andere_methoden_laufen_durch_die_abwehr(self):
        for methode in ('HEAD', 'OPTIONS', 'PUT', 'DELETE', 'PATCH'):
            status, body = self.zeilen(methode, '/', ('X-Forwarded-For', '203.0.113.60'))
            self.assertEqual((status, body if methode == 'HEAD' else b''), (405, b''), methode)   # HEAD ohne Körper
        for _ in range(5):
            self.assertEqual(self.zeilen('OPTIONS', '/.env', ('X-Forwarded-For', '203.0.113.61'))[0], 404)   # Fallen gelten auch hier
        self.assertEqual(self.zeilen('GET', '/', ('X-Forwarded-For', '203.0.113.61'))[0], 403)
        self.assertEqual(self.zeilen('HEAD', '/', ('X-Forwarded-For', '203.0.113.61')), (403, b''))   # gesperrt auch für HEAD
        self.assertEqual(self.zeilen('DELETE', '/api/status', ('X-Forwarded-For', '203.0.113.61'))[0], 403)
        for methode in ('PROPFIND', 'TRACE', 'get'):                                         # auch was http.server sonst selbst mit 501 beantwortete
            self.assertEqual(self.zeilen(methode, '/', ('X-Forwarded-For', '203.0.113.61'))[0], 403, methode)
            self.assertEqual(self.zeilen(methode, '/', ('X-Forwarded-For', '203.0.113.62'))[0], 405, methode)
        for _ in range(5):
            self.zeilen('PROPFIND', '/.env', ('X-Forwarded-For', '203.0.113.63'))
        self.assertEqual(self.zeilen('GET', '/', ('X-Forwarded-For', '203.0.113.63'))[0], 403)

    def test_head_ohne_koerper_und_ohne_fehlerbremse(self):
        with socket.create_connection(('127.0.0.1', self.port), timeout=10) as s:   # http.client verschluckt den Körper einer HEAD-Antwort
            s.sendall(b'HEAD / HTTP/1.0\r\nHost: ju-finanzen.beispiel.ts.net\r\nX-Forwarded-For: 203.0.113.64\r\n\r\n')
            antwort = b''
            while teil := s.recv(65536):
                antwort += teil
        kopf, _, koerper = antwort.partition(b'\r\n\r\n')
        self.assertEqual((kopf.split(b'\r\n')[0].split()[1], koerper), (b'405', b''))
        for _ in range(60):                                                                  # Verfügbarkeitsprüfung mit HEAD: nie gesperrt
            self.assertEqual(self.zeilen('HEAD', '/', ('X-Forwarded-For', '203.0.113.64'))[0], 405)
        self.assertEqual(self.zeilen('GET', '/', ('X-Forwarded-For', '203.0.113.64'))[0], 200)

    def test_protokollieren_dann_abweisen_sperrt_niemanden_nachtraeglich(self):
        store = Store(app.DB)
        try:
            schutz.aus(store.db)                                                             # Notausgang: nur protokollieren
        finally:
            store.db.close()
        for _ in range(6):
            self.assertEqual(self.seite('/.env', **self.VON)[0], 404)                        # vermerkt, was gesperrt würde
        self.assertEqual(self.seite('/', **self.VON)[0], 200)
        self.assertEqual(self.aussen('/api/schutz-einstellungen', {'modus': 'abweisen'}, self.chefin)[0], 200)
        self.assertEqual(self.seite('/', **self.VON)[0], 200)                                # auch nach dem Ende der Sitzung nicht ausgesperrt
        self.assertEqual(self.get(self.chefin, '/api/schutz')[1]['sperren'], [])

    def test_sicherungsdatei_ohne_datum_stoert_das_lagebild_nicht(self):
        ordner = app.backups_dir() / 'Automatisch'
        ordner.mkdir(parents=True, exist_ok=True)
        for name in ('finanzen-kopie-1234.sqlite3', 'finanzen-2026-11-18.sqlite3', 'finanzen-9999-99-99.sqlite3'):
            (ordner / name).write_bytes(b'')
            self.addCleanup((ordner / name).unlink)
        status, body = self.get(self.chefin, '/api/sicherheit')
        self.assertEqual((status, body['lagebild']['letzte_sicherung']), (200, '2026-11-18'))

    def test_rechte_und_bestaetigung(self):
        self.assertEqual(rollen.required('GET', '/api/schutz'), (('sicherheit', 'lesen'),))
        for path in ('/api/schutz-einstellungen', '/api/schutz-sperren', '/api/schutz-freigeben', '/api/schutz-liste-laden', '/api/schutz-mitschnitt'):
            self.assertEqual(rollen.required('POST', path), (('sicherheit', 'schreiben'),), path)
            self.assertIn(('POST', path), app.CONFIRM, path)                                 # auch der Mitschnitt: zeigt Adresse und Pfad aller
        vorsitz = self.person('Vorsitz', 'Landesvorsitzender')                               # „Sicherheit lesen“
        mitglieder = self.person('Mitglieder', 'Landesmitgliederbeauftragter')
        self.assertEqual(self.get(mitglieder, '/api/schutz')[0], 403)
        status, body = self.get(vorsitz, '/api/schutz')
        self.assertEqual((status, body['einstellungen']['modus'], body['sperren'], body['verkehr']), (200, 'abweisen', [], []))
        for path in ('/api/schutz-einstellungen', '/api/schutz-sperren', '/api/schutz-freigeben', '/api/schutz-liste-laden', '/api/schutz-mitschnitt'):
            self.assertEqual(self.post(vorsitz, path, {})[0], 403, path)                     # ändern nur mit „Sicherheit ändern“
        frisch = self.login('Chefin', confirm=False)
        for path in ('/api/schutz-einstellungen', '/api/schutz-sperren', '/api/schutz-freigeben', '/api/schutz-liste-laden', '/api/schutz-mitschnitt'):
            self.assertEqual(self.post(frisch, path, {})[0], 428, path)                      # erst nach Passwortbestätigung
        self.assertEqual(self.get(self.chefin, '/api/schutz')[1]['einstellungen'], schutz.VORGABEN)
        status, body = self.post(self.chefin, '/api/schutz-mitschnitt', {'an': True})
        self.assertEqual((status, bool(body['bis'])), (200, True))
        self.assertEqual(self.post(self.chefin, '/api/schutz-einstellungen', {'schwelle': 3})[1]['einstellungen']['schwelle'], 3)
        self.assertEqual(self.post(self.chefin, '/api/schutz-einstellungen', {'schwelle': 0})[0], 400)
        self.assertEqual(self.post(self.chefin, '/api/schutz-liste-laden', {})[0], 500)       # Test ohne Netz: der Abruf ist gesperrt

    def test_liste_laden_ueber_den_weg(self):
        with unittest.mock.patch.object(schutz, 'holen', lambda url: drop_fixture()[url]):
            status, body = self.post(self.chefin, '/api/schutz-liste-laden', {})
        self.assertEqual((status, body['liste']['anzahl']), (200, 68))
        self.assertEqual(self.roh('/api/status', **{'X-Forwarded-For': '192.0.2.200'})[0], 403)
        self.assertEqual(self.get(self.chefin, '/api/schutz')[1]['liste']['anzahl'], 68)
        with unittest.mock.patch.object(schutz, 'holen', side_effect=OSError('aus')):
            status, body = self.post(self.chefin, '/api/schutz-liste-laden', {})
        self.assertEqual((status, 'konnte nicht geladen werden' in body['error']), (400, True))

    def test_einstieg_vor_jeder_anfrage(self):
        self.assertEqual(self.seite('/wp-login.php', **self.VON), (404, b'Nicht gefunden.'))  # Falle: wie jede unbekannte Adresse
        self.assertEqual(self.seite('/gibt-es-nicht', **self.VON), (404, b'Nicht gefunden.'))
        self.assertEqual(self.seite('/', **{**self.VON, 'User-Agent': 'sqlmap/1.7'}), (403, b'Zugriff verweigert.'))
        self.assertEqual(self.aussen('/api/status?x=%3Cscript%3E'), (403, {'error': 'Zugriff verweigert.'}))   # knapp, ohne Details
        self.assertEqual(self.aussen('/api/login', {'name': "x' OR '1'='1", 'password': 'egal-egal-egal'})[0], 403)
        self.assertEqual(self.seite('/', **self.VON)[0], 200)                                # vier Treffer: noch frei
        self.assertEqual(self.seite('/.env', **self.VON)[0], 404)
        for path in ('/', '/js/main.js', '/gibt-es-nicht', '/wp-login.php'):                 # fünfter Treffer: alles gesperrt, auch statische Dateien
            self.assertEqual(self.seite(path, **self.VON), (403, b'Zugriff verweigert.'), path)
        self.assertEqual(self.aussen('/api/status'), (403, {'error': 'Zugriff verweigert.'}))
        self.assertEqual(self.aussen('/api/login', {'name': 'Chefin', 'password': PASSWORT})[0], 403)
        self.assertEqual(self.roh('/api/status', **{'X-Forwarded-For': '203.0.113.51'})[0], 200)   # andere Adresse
        self.assertEqual(self.roh('/api/status')[0], 200)                                    # der Server selbst (127.0.0.1) ist nie gesperrt
        self.assertEqual(self.aussen('/api/schutz', who=self.chefin)[0], 200)                # gültige Sitzung kommt durch und kann freigeben
        status, body = self.aussen('/api/schutz', who=self.chefin)
        self.assertEqual([(s['netz'], s['art']) for s in body['sperren']], [('203.0.113.50/32', 'auto')])
        self.assertEqual((body['meine_adresse'], body['geschuetzt']), ('203.0.113.50', False))
        arten = [v['art'] for v in body['verkehr']]
        self.assertIn('gesperrt (automatisch)', arten)
        self.assertIn('Angriffsmuster: Skript-Einschleusung', arten)
        self.assertTrue(all('?' not in v['pfad'] for v in body['verkehr']))
        self.assertEqual(self.aussen('/api/schutz-freigeben', {'id': body['sperren'][0]['id']}, self.chefin)[0], 200)
        self.assertEqual(self.aussen('/api/status')[0], 200)
        lage = self.get(self.chefin, '/api/sicherheit')[1]
        self.assertEqual((lage['abwehr']['treffer_gesamt'], lage['abwehr']['sperren'], lage['abwehr']['abgewiesen']), (5, 1, 6))
        self.assertEqual(len(lage['pruefung']), 12)
        self.assertEqual((lage['serverschutz']['vorhanden'], lage['lagebild']['sitzungen']), (False, 1))

    def test_angemeldete_duerfen_jeden_text_speichern(self):
        text = "<script>alert('x')</script> ' OR '1'='1 UNION SELECT * FROM users; DROP TABLE bank ../../etc/passwd ${jndi:ldap://x}"
        for _ in range(8):
            self.assertEqual(self.aussen('/api/wartung', {'an': True, 'text': text[:300]}, self.chefin)[0], 200)
            self.assertEqual(self.aussen('/api/wartung', {'an': False}, self.chefin)[0], 200)
        for _ in range(8):                                                                   # Suchtexte Angemeldeter in der Adresse auch
            self.assertEqual(self.aussen('/api/sicherheit-ereignisse?suche=%3Cscript%3E', who=self.chefin)[0], 200)
        self.assertEqual(self.aussen('/api/status')[0], 200)
        # Passwörter prüft die Firewall nie: falsches Passwort mit Angriffszeichen ist nur eine Fehlanmeldung
        self.assertEqual(self.aussen('/api/login', {'name': 'Chefin', 'password': "x' OR '1'='1 <script>"})[0], 401)
        self.assertEqual(self.get(self.chefin, '/api/sicherheit')[1]['abwehr']['treffer_gesamt'], 0)

    def test_fehlerbremse_laesst_anmeldung_und_sicherheit_benutzbar(self):
        for _ in range(40):
            self.assertEqual(self.aussen('/api/data?year=2026')[0], 401)                     # abgelaufene Sitzung fragt weiter: zählt nicht
        frisch = self.login('Chefin', confirm=False)
        for _ in range(40):
            self.assertEqual(self.aussen('/api/schutz-sperren', {'netz': '198.51.100.1'}, frisch)[0], 428)
            self.assertEqual(self.aussen('/api/schutz-einstellungen', {'modus': 'unsinn'}, self.chefin)[0], 400)   # Eingabefehler Angemeldeter
        self.assertEqual(self.aussen('/api/status')[0], 200)
        for i in range(34):                                                                  # ohne Anmeldung: 30 Fehler + 4 weitere = gesperrt
            self.assertEqual(self.seite(f'/unbekannt-{i}', **self.VON)[0], 404)
        self.assertEqual(self.seite('/', **self.VON)[0], 403)
        self.assertEqual(self.aussen('/api/schutz', who=self.chefin)[0], 200)

    def test_schutz_vor_aussperrung(self):
        status, body = self.aussen('/api/schutz-sperren', {'netz': '203.0.113.0/24', 'grund': 'Test'}, self.chefin)
        self.assertEqual((status, 'eigene aktuelle Adresse' in body['error']), (400, True))
        self.assertEqual(self.aussen('/api/schutz-sperren', {'netz': '127.0.0.1'}, self.chefin)[0], 400)
        status, body = self.aussen('/api/schutz-sperren', {'netz': '198.51.100.9', 'grund': 'Scan', 'minuten': 60}, self.chefin)
        self.assertEqual((status, body['netz']), (200, '198.51.100.9/32'))
        self.assertEqual(self.roh('/api/status', **{'X-Forwarded-For': '198.51.100.9'})[0], 403)
        self.assertEqual(self.roh('/api/status', **{'X-Forwarded-For': '203.0.113.9, 198.51.100.9'})[0], 403)   # zählt der Eintrag des Proxys
        self.assertEqual(self.roh('/api/status', **{'X-Forwarded-For': '198.51.100.9, 203.0.113.9'})[0], 200)   # ein vorangestellter nicht
        self.assertEqual(self.aussen('/api/schutz-einstellungen', {'nie': ['198.51.100.0/24']}, self.chefin)[0], 200)
        self.assertEqual(self.roh('/api/status', **{'X-Forwarded-For': '198.51.100.9'})[0], 200)   # „nie sperren“ geht vor
        store = Store(app.DB)
        try:
            arten = [r['art'] for r in sicherheit.ereignisse(store.db)]
        finally:
            store.db.close()
        self.assertEqual((arten.count('schutz_sperre'), arten.count('schutz_einstellung')), (1, 1))

    def test_notfall_stellt_die_firewall_scharf_und_zurueck(self):
        self.assertEqual(self.post(self.chefin, '/api/schutz-einstellungen', {'modus': 'protokollieren', 'schwelle': 8})[0], 200)
        self.assertEqual(self.post(self.chefin, '/api/notfall-an', {'grund': 'Übung mit erfundenem Verdacht'})[0], 200)
        body = self.get(self.chefin, '/api/schutz')[1]
        self.assertEqual((body['einstellungen']['modus'], body['einstellungen']['schwelle'], body['notfall_vorlage']), ('abweisen', 1, True))
        self.assertEqual(self.seite('/.env', **self.VON)[0], 404)
        self.assertEqual(self.seite('/', **self.VON)[0], 403)                                # Sperre ab dem ersten Treffer
        self.assertEqual(self.post(self.chefin, '/api/notfall-aus', {})[0], 200)
        body = self.get(self.chefin, '/api/schutz')[1]
        self.assertEqual((body['einstellungen']['modus'], body['einstellungen']['schwelle'], body['notfall_vorlage']), ('protokollieren', 8, False))


if __name__ == '__main__':
    unittest.main()
