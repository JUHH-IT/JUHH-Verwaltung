#!/usr/bin/env python3
"""Serverschutz des Betriebssystems: Sperrliste prüfen, nftables-Regelwerk erzeugen, Statusbericht schreiben.

Hilfsprogramm von server/haerten.sh (läuft als root mit dem Python des Systems, nur Standardbibliothek):

    serverschutz.py liste DROP4 DROP6     heruntergeladene Spamhaus-Listen prüfen und als letzte gute Liste ablegen
    serverschutz.py regelwerk             Regelwerk der Tabelle „inet ju_schutz“ ausgeben (für nft -f)
    serverschutz.py status [--zeigen]     /var/lib/ju-serverschutz/status.json schreiben (und lesbar ausgeben)

WURZEL (nur Tests): nachgestelltes Wurzelverzeichnis; Systembefehle kommen dann ausschließlich aus $WURZEL/stub.
Der Statusbericht enthält nie Adressen, Namen oder Geheimnisse – nur feste Texte, Anzahlen und Portnummern.
"""
import ast
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

W = os.environ.get('WURZEL', '').rstrip('/')
LIB = '/var/lib/ju-serverschutz'
CONF = '/etc/ju-finanzen/haertung.conf'
SYSCTL = '/etc/sysctl.d/80-ju-haertung.conf'
VERSION = 1

MAX_BYTES = 2_000_000
# plausible Größe der Listen (November 2026: rund 1750 IPv4- und 90 IPv6-Netze) und kleinste erlaubte Präfixlänge
GRENZEN = {4: (100, 20000, 8), 6: (5, 5000, 16)}
# Bereiche, die nie auf einer Sperrliste stehen dürfen – steht einer darin, ist die Liste kaputt und wird verworfen
GESCHUETZT = [ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16',
    '224.0.0.0/3', '::/8', 'fc00::/7', 'fe80::/10', 'ff00::/8')]
# vor der Sperrliste angenommen: Loopback, Link-Local (Konsole des Anbieters kommt von 169.254.0.1), Tailscale
FREI4 = '127.0.0.0/8, 169.254.0.0/16, 100.64.0.0/10'
FREI6 = '::1, fe80::/10, fd7a:115c:a1e0::/48'
STAENDE = ('ok', 'hinweis', 'kritisch')


class ListeFehler(Exception):
    pass


def pfad(p):
    return W + p


def befehl(*args, timeout=30):
    """Systembefehl ausführen → (Rückgabe, Ausgabe); fehlt er oder scheitert der Start: (127, '')."""
    exe = os.path.join(W, 'stub', args[0]) if W else shutil.which(args[0], path='/usr/sbin:/usr/bin:/sbin:/bin')
    if not exe or not os.access(exe, os.X_OK):
        return 127, ''
    try:
        r = subprocess.run([exe, *args[1:]], capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, 'LC_ALL': 'C.UTF-8'})
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ''


def lesen(p):
    try:
        with open(pfad(p), encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return None


def schreiben(p, text, modus):
    """Datei atomar ersetzen (neue Datei daneben, dann umbenennen)."""
    ziel = pfad(p)
    os.makedirs(os.path.dirname(ziel), mode=0o755, exist_ok=True)
    neu = f'{ziel}.neu.{os.getpid()}'
    with open(os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, modus), 'w', encoding='utf-8') as f:
        f.write(text)
    os.chmod(neu, modus)
    os.replace(neu, ziel)


def einstellung(name, vorgabe=''):
    """Wert aus haertung.conf (letzte Zeile NAME=…, ohne Kommentar am Zeilenende)."""
    wert = None
    for zeile in (lesen(CONF) or '').splitlines():
        if zeile.startswith(name + '='):
            wert = zeile.split('=', 1)[1].split('#')[0].strip()
    return wert if wert else vorgabe


def an(name, vorgabe='ja'):
    return einstellung(name, vorgabe) == 'ja'


# ---------------------------------------------------------------- Sperrliste

def liste_lesen(roh, familie):
    """Spamhaus-DROP-Text prüfen → sortierte Netze. Jede Abweichung verwirft die ganze Liste (ListeFehler)."""
    if len(roh) > MAX_BYTES:
        raise ListeFehler('Liste ist zu groß')
    try:
        text = roh.decode('ascii')
    except UnicodeDecodeError:
        raise ListeFehler('Liste enthält unerwartete Zeichen') from None
    kleinste, groesste, praefix = GRENZEN[familie]
    netze = set()
    for nummer, zeile in enumerate(text.splitlines(), 1):
        eintrag = zeile.split(';', 1)[0].strip()
        if not eintrag:
            continue
        try:
            netz = ipaddress.ip_network(eintrag, strict=True)
        except ValueError:
            raise ListeFehler(f'Zeile {nummer} ist kein gültiges Netz') from None
        if netz.version != familie or '/' not in eintrag:
            raise ListeFehler(f'Zeile {nummer} gehört nicht in die IPv{familie}-Liste')
        if netz.prefixlen < praefix:
            raise ListeFehler(f'Zeile {nummer}: Netz ist unplausibel groß (/{netz.prefixlen})')
        if any(netz.version == g.version and netz.overlaps(g) for g in GESCHUETZT):
            raise ListeFehler(f'Zeile {nummer} liegt in einem privaten, lokalen oder Tailscale-Bereich')
        netze.add(netz)
    if not kleinste <= len(netze) <= groesste:
        raise ListeFehler(f'unplausible Anzahl: {len(netze)} Netze (erwartet {kleinste} bis {groesste})')
    return sorted(netze)


def eigene_netze():
    """Adressen dieses Servers, sein Standard-Gateway und NIE_SPERREN – Einträge, die sie treffen, werden ausgelassen."""
    gefunden = []
    for args, schluessel in ((('ip', '-j', 'addr'), 'local'), (('ip', '-j', 'route', 'show', 'default'), 'gateway'),
                             (('ip', '-j', '-6', 'route', 'show', 'default'), 'gateway')):
        rc, out = befehl(*args)
        try:
            daten = json.loads(out) if rc == 0 and out.strip() else []
        except ValueError:
            daten = []
        for eintrag in daten:
            for teil in [eintrag] + list(eintrag.get('addr_info') or []):
                if teil.get(schluessel):
                    gefunden.append(teil[schluessel])
    gefunden += einstellung('NIE_SPERREN').replace(',', ' ').split()
    netze = []
    for wert in gefunden:
        try:
            netze.append(ipaddress.ip_network(wert, strict=False))
        except ValueError:
            pass
    return netze


def ohne_eigene(netze, eigene):
    """(behalten, ausgelassen): Listeneinträge, die eine eigene Adresse treffen, werden nie gesperrt."""
    behalten = [n for n in netze if not any(n.version == e.version and n.overlaps(e) for e in eigene)]
    return behalten, len(netze) - len(behalten)


def gute_liste(familie):
    """Letzte gute Liste aus /var/lib/ju-serverschutz (leer, wenn es keine gibt oder SPERRLISTE=nein)."""
    if not an('SPERRLISTE'):
        return []
    netze = []
    for zeile in (lesen(f'{LIB}/drop{familie}.txt') or '').splitlines():
        try:
            netze.append(ipaddress.ip_network(zeile.strip()))
        except ValueError:
            pass
    return netze


def liste_uebernehmen(datei4, datei6):
    """Beide heruntergeladenen Listen prüfen; nur wenn beide gut sind, ersetzen sie die letzte gute Liste."""
    try:
        gepruefte = {}
        for familie, datei in ((4, datei4), (6, datei6)):
            with open(datei, 'rb') as f:
                gepruefte[familie] = liste_lesen(f.read(MAX_BYTES + 1), familie)
    except (ListeFehler, OSError) as fehler:
        grund = str(fehler) if isinstance(fehler, ListeFehler) else 'Datei nicht lesbar'
        schreiben(f'{LIB}/sperrliste.fehler', f'{jetzt()} {grund}\n', 0o600)
        print(f'Sperrliste verworfen: {grund} – die letzte gute Liste bleibt.', file=sys.stderr)
        return 1
    for familie, netze in gepruefte.items():
        schreiben(f'{LIB}/drop{familie}.txt', ''.join(f'{n}\n' for n in netze), 0o600)
    try:
        os.remove(pfad(f'{LIB}/sperrliste.fehler'))
    except OSError:
        pass
    print(f'Sperrliste übernommen: {len(gepruefte[4])} IPv4- und {len(gepruefte[6])} IPv6-Netze.')
    return 0


# ---------------------------------------------------------------- Regelwerk

def ssh_ports():
    rc, out = befehl('sshd', '-T')
    ports = sorted({int(z.split()[1]) for z in out.splitlines() if re.fullmatch(r'port \d+', z.strip())}) if rc == 0 else []
    return ports or [22]


def regelwerk(netze4, netze6, ports, takt=12, nur_tailscale=False):
    """Text für „nft -f“: ersetzt genau die Tabelle inet ju_schutz in einem Schritt (nie „flush ruleset“)."""
    def menge(name, typ, netze):
        inhalt = ''
        if netze:
            inhalt = '\n\t\telements = {\n' + ''.join(f'\t\t\t{n},\n' for n in netze) + '\t\t}'
        return f'\tset {name} {{\n\t\ttype {typ}; flags interval; auto-merge;{inhalt}\n\t}}\n'

    p = ', '.join(str(int(x)) for x in ports)
    takt = max(1, min(int(takt), 600))
    fremd = f'\t\ttcp dport {{ {p} }} ct state new counter name ssh_fremd drop\n' if nur_tailscale else ''
    return f'''#!/usr/sbin/nft -f
# JU Hamburg Finanzen – Schutztabelle. Erzeugt von server/haertung/serverschutz.py, nicht von Hand ändern.
# Ersetzt nur die eigene Tabelle (nie „flush ruleset“): die Regeln von ufw und Tailscale bleiben unberührt.
table inet ju_schutz
delete table inet ju_schutz
table inet ju_schutz {{
	counter sperrliste {{ }}
	counter ungueltig {{ }}
	counter ssh_takt {{ }}
	counter ssh_fremd {{ }}
{menge('drop4', 'ipv4_addr', netze4)}{menge('drop6', 'ipv6_addr', netze6)}	set ssh_takt4 {{
		type ipv4_addr; size 65536; flags dynamic,timeout; timeout 2m;
	}}
	set ssh_takt6 {{
		type ipv6_addr; size 65536; flags dynamic,timeout; timeout 2m;
	}}
	chain eingang {{
		# vor ufw (Priorität 0), nach der Verbindungsverfolgung (-200). „accept“ beendet nur diese Kette, danach
		# entscheidet weiter ufw – diese Tabelle kann nur zusätzlich verwerfen, nie etwas öffnen.
		type filter hook input priority -150; policy accept;
		iifname "lo" accept
		iifname "tailscale0" accept
		ct state established,related accept
		ip saddr {{ {FREI4} }} accept
		ip6 saddr {{ {FREI6} }} accept
		ip saddr @drop4 counter name sperrliste drop
		ip6 saddr @drop6 counter name sperrliste drop
		meta l4proto ipv6-icmp accept
		ct state invalid counter name ungueltig drop
{fremd}		tcp dport {{ {p} }} ct state new add @ssh_takt4 {{ ip saddr limit rate over {takt}/minute burst {takt} packets }} counter name ssh_takt drop
		tcp dport {{ {p} }} ct state new add @ssh_takt6 {{ ip6 saddr and ffff:ffff:ffff:ffff:: limit rate over {takt}/minute burst {takt} packets }} counter name ssh_takt drop
	}}
}}
'''


def regelwerk_aktuell():
    eigene = eigene_netze()
    netze4, _ = ohne_eigene(gute_liste(4), eigene)
    netze6, _ = ohne_eigene(gute_liste(6), eigene)
    try:
        takt = int(einstellung('SSH_NEU_JE_MINUTE', '12'))
    except ValueError:
        takt = 12
    return regelwerk(netze4, netze6, ssh_ports(), takt, an('SSH_NUR_TAILSCALE', 'nein'))


# ---------------------------------------------------------------- Statusbericht

def jetzt():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def iso(sekunden):
    return datetime.fromtimestamp(sekunden).astimezone().isoformat(timespec='seconds')


def lokal(adresse):
    """Lauscht die Adresse nur lokal? Loopback (auch ::ffff:127.0.0.1), Link-Local oder Tailscale."""
    adresse = adresse.split('%')[0].strip('[]')
    if adresse in ('*', ''):
        return False
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network('100.64.0.0/10')
            or ip in ipaddress.ip_network('fd7a:115c:a1e0::/48'))


def lauschende(ss_text):
    """Ausgabe von „ss -Htuln“ → Menge (Protokoll, Port), die nicht nur lokal lauscht."""
    offen = set()
    for zeile in ss_text.splitlines():
        teile = zeile.split()
        if len(teile) < 5 or teile[0] not in ('tcp', 'udp'):
            continue
        adresse, _, port = teile[4].rpartition(':')
        if port.isdigit() and not lokal(adresse):
            offen.add((teile[0], int(port)))
    return offen


UFW_ZEILE = re.compile(r'^(.+?)\s+(ALLOW|LIMIT|DENY|REJECT)(?: (IN|OUT|FWD))?\s+(.+?)\s*$')


def ufw_lage(text):
    """Ausgabe von „ufw status verbose“ → aktiv, eingehend gesperrt, Regeln [(Ziel, Aktion, Quelle)]."""
    regeln = []
    for zeile in text.splitlines():
        m = UFW_ZEILE.match(re.sub(r'\s+#.*$', '', zeile))   # ohne Kommentar der Regel
        if m and m.group(3) in (None, 'IN'):
            regeln.append((m.group(1).replace(' (v6)', '').strip(), m.group(2), m.group(4).replace(' (v6)', '').strip()))
    return {'aktiv': bool(re.search(r'^Status: active', text, re.M)),
            'gesperrt': bool(re.search(r'^Default: (deny|reject) \(incoming\)', text, re.M)), 'regeln': regeln}


def ufw_durchlass(lage, proto, port):
    """'offen' (von überall), 'begrenzt' (nur bestimmte Quellen) oder 'zu' – was ufw für diesen Port tut."""
    if not lage['aktiv'] or not lage['gesperrt']:
        return 'offen'
    ergebnis = 'zu'
    for ziel, aktion, quelle in lage['regeln']:
        if aktion not in ('ALLOW', 'LIMIT') or re.search(r' on tailscale\d*$', ziel):
            continue
        ziel = re.sub(r' on \S+$', '', ziel)
        angabe = ziel.split()[-1]
        if angabe == 'OpenSSH':
            angabe = '22/tcp'
        m = re.fullmatch(r'([\d,:]+)(?:/(tcp|udp))?', angabe)
        if m:
            trifft = (m.group(2) in (None, proto)) and any(
                int(a) <= port <= int(b or a) for a, _, b in (t.partition(':') for t in m.group(1).split(',') if t))
        else:
            # shortcut: andere Anwendungsprofile (z. B. „Nginx Full“) werden nicht aufgelöst – bei Bedarf „ufw app info“ auswerten
            trifft = angabe == 'Anywhere'
        if trifft:
            if quelle == 'Anywhere':
                return 'offen'
            ergebnis = 'begrenzt'
    return ergebnis


def gebannte(text):
    """Ausgabe von „fail2ban-client banned“ ([{'sshd': [...]}, …]) → Anzahl gesperrter Adressen."""
    try:
        return sum(len(adressen) for jail in ast.literal_eval(text.strip()) for adressen in jail.values())
    except (ValueError, SyntaxError, AttributeError, TypeError):
        return 0


def abgewiesen_24h(summe, zeit=None):
    """Verworfene Pakete der letzten 24 Stunden aus dem Zählerstand der Tabelle (übersteht das Neuladen der Tabelle)."""
    zeit = zeit or time.time()
    try:
        verlauf = json.loads(lesen(f'{LIB}/zaehler.json') or '')
        letzter, proben = int(verlauf['letzter']), [(float(t), int(n)) for t, n in verlauf['proben']]
    except (ValueError, KeyError, TypeError):
        letzter, proben = None, []
    if summe is not None:
        # beim ersten Mal nur merken; nach einem Neuladen der Tabelle beginnt der Zähler bei 0
        zuwachs = 0 if letzter is None else summe - letzter if summe >= letzter else summe
        proben = [(t, n) for t, n in proben if zeit - 86400 < t <= zeit] + [(zeit, zuwachs)]
        schreiben(f'{LIB}/zaehler.json', json.dumps({'letzter': summe, 'proben': proben}), 0o600)
    return sum(n for t, n in proben if zeit - 86400 < t <= zeit)


def kern_abweichungen():
    """Anzahl der Einstellungen aus der eigenen sysctl-Datei, die im laufenden Kern anders stehen (None: Datei fehlt)."""
    text = lesen(SYSCTL)
    if text is None:
        return None
    falsch = 0
    for zeile in text.splitlines():
        if '=' in zeile and not zeile.lstrip().startswith(('#', ';')):
            name, soll = (t.strip() for t in zeile.split('=', 1))
            ist = lesen('/proc/sys/' + name.replace('.', '/'))
            falsch += ist is not None and ist.split() != soll.split()
    return falsch


def aktiv(unit):
    return befehl('systemctl', 'is-active', unit)[1].strip() == 'active'


def modus(p):
    try:
        return os.stat(pfad(p)).st_mode & 0o777
    except OSError:
        return None


def bericht():
    pruefungen, zahlen = [], {}

    def pruefung(id_, titel, stand, detail):
        pruefungen.append({'id': id_, 'titel': titel, 'stand': stand, 'detail': detail})

    # Firewall
    lage = ufw_lage(befehl('ufw', 'status', 'verbose')[1])
    if lage['aktiv'] and lage['gesperrt']:
        pruefung('firewall', 'Firewall aktiv', 'ok', 'ufw aktiv, eingehend gesperrt')
    else:
        pruefung('firewall', 'Firewall aktiv', 'kritisch', 'ufw ist aus' if not lage['aktiv'] else 'eingehend nicht gesperrt')

    tabelle = befehl('nft', 'list', 'table', 'inet', 'ju_schutz')[0] == 0
    if not an('FIREWALL'):
        pruefung('schutztabelle', 'Schutztabelle geladen', 'hinweis', 'abgeschaltet (FIREWALL=nein)')
    else:
        pruefung('schutztabelle', 'Schutztabelle geladen', 'ok' if tabelle else 'kritisch',
                 'Tabelle ju_schutz geladen' if tabelle else 'Tabelle ju_schutz fehlt')

    # Sperrliste
    try:
        alter = time.time() - os.stat(pfad(f'{LIB}/drop4.txt')).st_mtime
        zahlen['sperrliste_netze'] = len(gute_liste(4)) + len(gute_liste(6))
        zahlen['sperrliste_stand'] = iso(time.time() - alter)
    except OSError:
        alter, zahlen['sperrliste_netze'], zahlen['sperrliste_stand'] = None, 0, ''
    if not an('SPERRLISTE'):
        pruefung('sperrliste', 'Sperrliste aktuell', 'hinweis', 'abgeschaltet (SPERRLISTE=nein)')
    elif alter is None:
        pruefung('sperrliste', 'Sperrliste aktuell', 'kritisch', 'noch keine Liste geladen')
    else:
        tage = int(alter // 86400)
        stand = 'ok' if tage < 3 else 'hinweis' if tage < 14 else 'kritisch'
        detail = f'{zahlen["sperrliste_netze"]} Netze, ' + ('von heute' if tage == 0 else f'{tage} Tage alt')
        if lesen(f'{LIB}/sperrliste.fehler') and stand == 'ok' and alter > 86400 + 3 * 3600:
            stand = 'hinweis'
        if lesen(f'{LIB}/sperrliste.fehler'):
            detail += ', letzter Abruf gescheitert'
        pruefung('sperrliste', 'Sperrliste aktuell', stand, detail)

    # fail2ban
    rc, out = befehl('fail2ban-client', 'banned')
    zahlen['gebannt'] = gebannte(out) if rc == 0 else 0
    if not an('FAIL2BAN'):
        pruefung('fail2ban', 'fail2ban läuft', 'hinweis', 'abgeschaltet (FAIL2BAN=nein)')
    elif aktiv('fail2ban.service') and rc == 0:
        pruefung('fail2ban', 'fail2ban läuft', 'ok', f'{zahlen["gebannt"]} Adressen gesperrt')
    else:
        pruefung('fail2ban', 'fail2ban läuft', 'kritisch', 'Dienst läuft nicht')

    # SSH
    ports = ssh_ports()
    rc, sshd = befehl('sshd', '-T')
    passwort = re.search(r'^passwordauthentication (\S+)', sshd, re.M)
    durchlass = 'zu' if an('SSH_NUR_TAILSCALE', 'nein') and tabelle else max(
        (ufw_durchlass(lage, 'tcp', p) for p in ports), key=('zu', 'begrenzt', 'offen').index)
    wo = {'zu': 'nur über Tailscale und die Konsole des Anbieters', 'begrenzt': 'durch die Firewall auf einzelne Quellen begrenzt',
          'offen': 'aus dem Internet erreichbar'}[durchlass]
    if rc != 0 or not passwort:
        pruefung('ssh_passwort', 'SSH-Anmeldung per Passwort', 'hinweis', 'Einstellung nicht lesbar')
    elif passwort.group(1) == 'no':
        pruefung('ssh_passwort', 'SSH-Anmeldung per Passwort', 'ok', 'aus – Anmeldung nur mit Schlüssel')
    else:
        pruefung('ssh_passwort', 'SSH-Anmeldung per Passwort', 'kritisch' if durchlass == 'offen' and not an('FAIL2BAN') else 'hinweis',
                 f'an; SSH ist {wo}')
    pruefung('ssh_erreichbar', 'SSH-Zugang aus dem Internet', 'hinweis' if durchlass == 'offen' else 'ok', f'SSH ist {wo}')

    # Ports: erwartet sind nur SSH und Tailscale (41641/udp); der DHCP-Client (68/udp) lauscht auf der Schnittstelle
    rc, ss = befehl('ss', '-Htuln')
    erwartet = {('tcp', p) for p in ports} | {('udp', 41641), ('udp', 68)}
    fremd = sorted(lauschende(ss) - erwartet)
    durch = [f'{port}/{proto}' for proto, port in fremd if ufw_durchlass(lage, proto, port) == 'offen']
    if rc != 0:
        pruefung('ports', 'Öffentlich lauschende Ports', 'hinweis', 'nicht prüfbar')
    elif durch:
        pruefung('ports', 'Öffentlich lauschende Ports', 'kritisch', 'unerwartet aus dem Internet erreichbar: ' + ', '.join(durch[:8]))
    elif fremd:
        pruefung('ports', 'Öffentlich lauschende Ports', 'hinweis', 'lauscht auf allen Adressen, von der Firewall gesperrt: '
                 + ', '.join(f'{port}/{proto}' for proto, port in fremd[:8]))
    else:
        pruefung('ports', 'Öffentlich lauschende Ports', 'ok', 'nur SSH und Tailscale')

    # Updates
    apt = befehl('apt-config', 'dump')[1]
    periodisch = all(re.search(rf'^APT::Periodic::{k} "[1-9]\d*";', apt, re.M) for k in ('Update-Package-Lists', 'Unattended-Upgrade'))
    if periodisch and 'APT::Periodic::Enable "0"' not in apt and aktiv('apt-daily-upgrade.timer'):
        pruefung('updates', 'Automatische Sicherheitsupdates', 'ok', 'eingeschaltet')
    else:
        pruefung('updates', 'Automatische Sicherheitsupdates', 'kritisch', 'nicht eingeschaltet')
    rc, sim = befehl('apt-get', '-s', '-o', 'Debug::NoLocking=true', 'upgrade', timeout=120)
    inst = [z for z in sim.splitlines() if z.startswith('Inst ')] if rc == 0 else []
    zahlen['updates_offen'] = len(inst)
    sicherheit = sum('security' in z.lower() for z in inst)
    if sicherheit:
        pruefung('updates_offen', 'Offene Updates', 'hinweis', f'{len(inst)} offen, davon {sicherheit} Sicherheitsupdates')
    else:
        pruefung('updates_offen', 'Offene Updates', 'ok', f'{len(inst)} offen, keine Sicherheitsupdates')
    zahlen['neustart_noetig'] = os.path.exists(pfad('/run/reboot-required'))
    pruefung('neustart', 'Neustart nötig', 'hinweis' if zahlen['neustart_noetig'] else 'ok',
             'nach einem Update steht ein Neustart aus' if zahlen['neustart_noetig'] else 'nein')

    # Kern
    falsch = kern_abweichungen()
    if not an('KERN'):
        pruefung('kern', 'Kern-Einstellungen', 'hinweis', 'abgeschaltet (KERN=nein)')
    elif falsch is None:
        pruefung('kern', 'Kern-Einstellungen', 'kritisch', 'nicht eingerichtet')
    else:
        pruefung('kern', 'Kern-Einstellungen', 'hinweis' if falsch else 'ok',
                 f'{falsch} Einstellungen weichen ab' if falsch else 'wie vorgesehen')

    # Tailscale und Dienste
    rc, ts = befehl('tailscale', 'status', '--json')
    if rc == 127:
        pruefung('tailscale', 'Tailscale verbunden', 'hinweis', 'nicht installiert')
    else:
        laeuft = bool(re.search(r'"BackendState":\s*"Running"', ts))
        pruefung('tailscale', 'Tailscale verbunden', 'ok' if laeuft else 'kritisch', 'verbunden' if laeuft else 'nicht verbunden')
    keycloak = os.path.exists(pfad('/etc/systemd/system/ju-keycloak.service'))
    dienste = ['ju-finanzen'] + (['ju-keycloak', 'postgresql'] if keycloak else []) + (['tailscaled'] if rc != 127 else [])
    aus = [d for d in dienste if not aktiv(d + '.service')]
    pruefung('dienste', 'Dienste laufen', 'kritisch' if aus else 'ok',
             'läuft nicht: ' + ', '.join(aus) if aus else f'{len(dienste)} Dienste laufen')
    if aktiv('docker.service') or aktiv('containerd.service'):
        pruefung('docker', 'Unnötige Dienste', 'hinweis', 'Docker läuft und kann Ports an der Firewall vorbei öffnen')
    else:
        pruefung('docker', 'Unnötige Dienste', 'ok', 'Docker ist aus')

    # Keycloak-Sicherung (nur root kann den Ordner lesen)
    if keycloak:
        try:
            ordner = pfad('/var/backups/ju-keycloak')
            neueste = max(os.stat(os.path.join(ordner, n)).st_mtime for n in os.listdir(ordner) if n.endswith('.dump'))
            tage = int((time.time() - neueste) // 86400)
        except (OSError, ValueError):
            tage = None
        if tage is None or tage >= 2:
            pruefung('keycloak_sicherung', 'Keycloak-Sicherung', 'hinweis', 'keine Sicherung' if tage is None else f'{tage} Tage alt')
        else:
            pruefung('keycloak_sicherung', 'Keycloak-Sicherung', 'ok', 'höchstens einen Tag alt')

    # Rechte der Einstellungen und Geheimnisse: nichts für „andere“ lesbar
    offen = [n for n in ('/etc/ju-finanzen', '/etc/ju-finanzen/ju-finanzen.env', '/etc/ju-finanzen/keycloak-admin.env',
                         '/var/lib/ju-finanzen', '/var/backups/ju-keycloak', '/var/backups/ju-haertung')
             if (modus(n) or 0) & 0o007]
    pruefung('rechte', 'Rechte der Einstellungen', 'kritisch' if offen else 'ok',
             f'{len(offen)} Pfade sind für andere Konten lesbar' if offen else 'nur für root und den Dienst lesbar')

    # Plattenplatz
    try:
        platte = shutil.disk_usage(pfad('/var/lib') if os.path.isdir(pfad('/var/lib')) else '/')
        frei = int(platte.free * 100 / platte.total)
        pruefung('platz', 'Plattenplatz', 'ok' if frei >= 15 else 'hinweis' if frei >= 5 else 'kritisch', f'{frei} % frei')
    except OSError:
        pruefung('platz', 'Plattenplatz', 'hinweis', 'nicht prüfbar')

    summe = None
    if tabelle:
        try:
            daten = json.loads(befehl('nft', '-j', 'list', 'counters', 'table', 'inet', 'ju_schutz')[1])['nftables']
            summe = sum(e['counter']['packets'] for e in daten if 'counter' in e)
        except (ValueError, KeyError, TypeError):
            summe = None
    zahlen['abgewiesen_24h'] = abgewiesen_24h(summe)
    return {'zeit': jetzt(), 'version': VERSION, 'pruefungen': pruefungen, 'zahlen': zahlen}


def status(zeigen):
    daten = bericht()
    schreiben(f'{LIB}/status.json', json.dumps(daten, ensure_ascii=False, indent=1) + '\n', 0o644)
    if zeigen:
        print(f'Serverschutz – Stand {daten["zeit"]}')
        for p in daten['pruefungen']:
            print(f'  {p["stand"].upper():<9} {p["titel"]}: {p["detail"]}')
        z = daten['zahlen']
        print(f'  Sperrliste: {z["sperrliste_netze"]} Netze · gesperrt durch fail2ban: {z["gebannt"]} · '
              f'abgewiesene Pakete (24 h): {z["abgewiesen_24h"]} · offene Updates: {z["updates_offen"]}')
    return 0


def main(argv):
    if argv[:1] == ['liste'] and len(argv) == 3:
        return liste_uebernehmen(argv[1], argv[2])
    if argv == ['regelwerk']:
        sys.stdout.write(regelwerk_aktuell())
        return 0
    if argv[:1] == ['status'] and argv[1:] in ([], ['--zeigen']):
        return status(bool(argv[1:]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
