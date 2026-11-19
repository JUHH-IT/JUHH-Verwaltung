"""Härtung des Betriebssystems (server/haerten.sh, server/haertung/serverschutz.py) – ohne root, ohne Netz, ohne systemd.

Das Skript läuft gegen ein nachgestelltes Wurzelverzeichnis (WURZEL); Systembefehle kommen dort nur aus <Wurzel>/stub
(tests/haertung_stub.sh). Alle Adressen sind erfunden (Dokumentationsbereiche 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24,
2001:db8::/32). Nicht nachstellbar und deshalb hier ungeprüft: echtes systemd, ufw, Tailscale, fail2ban und sshd.
Das erzeugte Regelwerk prüft und lädt ein Test zusätzlich mit dem echten nft – nur als root mit nft (sonst übersprungen).
"""
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
HAERTEN = ROOT / 'server' / 'haerten.sh'
sys.path.insert(0, str(ROOT / 'server' / 'haertung'))
import serverschutz  # noqa: E402

LINUX = sys.platform == 'linux' and shutil.which('bash') and shutil.which('flock')


def drop4(anzahl=150):
    """Erfundene Sperrliste im Format von Spamhaus: /30-Netze aus den Dokumentationsbereichen."""
    netze = [n for basis in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24')
             for n in ipaddress.ip_network(basis).subnets(new_prefix=30)][:anzahl]
    kopf = '; Spamhaus DROP List 2026/11/19 - (c) 2026 The Spamhaus Project SLU\n; Last-Modified: Wed, 18 Nov 2026 12:50:31 GMT\n'
    return (kopf + ''.join(f'{n} ; SBL{100000 + i}\n' for i, n in enumerate(netze))).encode()


def drop6(anzahl=12):
    netze = list(ipaddress.ip_network('2001:db8::/32').subnets(new_prefix=48))[:anzahl]
    return ('; Spamhaus IPv6 DROP List\n' + ''.join(f'{n} ; SBL{200000 + i}\n' for i, n in enumerate(netze))).encode()


SS = '''\
udp   UNCONN 0      0         127.0.0.53%lo:53         0.0.0.0:*
udp   UNCONN 0      0      198.51.100.10%eth0:68       0.0.0.0:*
udp   UNCONN 0      0               0.0.0.0:41641      0.0.0.0:*
udp   UNCONN 0      0                  [::]:41641         [::]:*
tcp   LISTEN 0      4096          100.64.0.7:443       0.0.0.0:*
tcp   LISTEN 0      4096                   *:22              *:*
tcp   LISTEN 0      200            127.0.0.1:5432      0.0.0.0:*
tcp   LISTEN 0      128            127.0.0.1:8769      0.0.0.0:*
tcp   LISTEN 0      4096  [::ffff:127.0.0.1]:8180            *:*
tcp   LISTEN 0      4096  [::ffff:127.0.0.1]:9000            *:*
tcp   LISTEN 0      4096  [::ffff:127.0.0.1]:41873           *:*
tcp   LISTEN 0      4096 [fd7a:115c:a1e0::7]:443          [::]:*
'''
UFW = '''\
Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), deny (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
Anywhere on tailscale0     ALLOW IN    Anywhere
22/tcp                     ALLOW IN    203.0.113.5
Anywhere (v6) on tailscale0 ALLOW IN    Anywhere (v6)
'''


class ListeTests(unittest.TestCase):
    def test_gute_liste(self):
        netze = serverschutz.liste_lesen(drop4(), 4)
        self.assertEqual(len(netze), 150)
        self.assertEqual(str(netze[0]), '192.0.2.0/30')
        self.assertEqual(len(serverschutz.liste_lesen(drop6(), 6)), 12)

    def test_kaputte_listen_werden_verworfen(self):
        gut = drop4()
        for name, roh, familie in (
                ('zu klein', drop4(40), 4), ('leer', b'', 4), ('nur Kommentar', b'; nichts\n', 4),
                ('zu groß in Bytes', gut + b';' * (serverschutz.MAX_BYTES + 1), 4),
                ('Fehlerseite', b'<html><body>503 Service Unavailable</body></html>\n', 4),
                ('ungültige Zeile', gut + b'192.0.2.999/24 ; SBL1\n', 4), ('Text statt Netz', gut + b'beispiel.example\n', 4),
                ('Hostbits gesetzt', gut + b'192.0.2.1/24 ; SBL1\n', 4), ('Adresse ohne Maske', gut + b'192.0.2.7\n', 4),
                ('falsche Familie', gut + b'2001:db8:1::/48 ; SBL1\n', 4), ('IPv4 in der IPv6-Liste', drop6() + b'192.0.2.0/24\n', 6),
                ('alles', gut + b'0.0.0.0/0\n', 4), ('riesiges Netz', gut + b'192.0.0.0/7\n', 4),
                ('kein ASCII', gut + '192.0.2.0/24 ; ü\n'.encode(), 4)):
            with self.subTest(name):
                with self.assertRaises(serverschutz.ListeFehler):
                    serverschutz.liste_lesen(roh, familie)

    def test_zu_viele_netze(self):
        with mock.patch.dict(serverschutz.GRENZEN, {4: (100, 120, 8)}):
            with self.assertRaises(serverschutz.ListeFehler):
                serverschutz.liste_lesen(drop4(150), 4)

    def test_private_lokale_und_tailscale_bereiche_nie(self):
        for netz in ('10.0.0.0/8', '10.20.30.0/24', '172.16.5.0/24', '192.168.1.0/24', '127.0.0.0/8', '169.254.0.0/16',
                     '100.64.0.0/10', '100.100.100.0/24', '224.0.0.0/8', '0.0.0.0/8'):
            with self.subTest(netz):
                with self.assertRaises(serverschutz.ListeFehler):
                    serverschutz.liste_lesen(drop4() + f'{netz} ; SBL9\n'.encode(), 4)
        for netz in ('fd7a:115c:a1e0::/48', 'fc00::/8', 'fe80::/16', '::/16', 'ff02::/16'):
            with self.subTest(netz):
                with self.assertRaises(serverschutz.ListeFehler):
                    serverschutz.liste_lesen(drop6() + f'{netz} ; SBL9\n'.encode(), 6)

    def test_eigene_adressen_werden_ausgelassen(self):
        netze = serverschutz.liste_lesen(drop4(), 4)
        eigene = [ipaddress.ip_network('198.51.100.10/32'), ipaddress.ip_network('2001:db8:5::1/128')]
        behalten, ausgelassen = serverschutz.ohne_eigene(netze, eigene)
        self.assertEqual(ausgelassen, 1)
        self.assertFalse(any(ipaddress.ip_address('198.51.100.10') in n for n in behalten))
        self.assertEqual(serverschutz.ohne_eigene(serverschutz.liste_lesen(drop6(), 6), eigene)[1], 1)


class RegelwerkTests(unittest.TestCase):
    def regelwerk(self, **mehr):
        return serverschutz.regelwerk(serverschutz.liste_lesen(drop4(), 4), serverschutz.liste_lesen(drop6(), 6), [22], **mehr)

    def test_nur_die_eigene_tabelle_und_ausnahmen_vor_der_sperrliste(self):
        text = self.regelwerk()
        regeln = [z.strip() for z in text.splitlines() if z.strip() and not z.strip().startswith('#')]
        self.assertFalse(any(z.startswith('flush') for z in regeln))
        self.assertEqual([z for z in regeln if z.startswith(('table', 'delete'))],
                         ['table inet ju_schutz', 'delete table inet ju_schutz', 'table inet ju_schutz {'])
        sperre = regeln.index('ip saddr @drop4 counter name sperrliste drop')
        for ausnahme in ('iifname "lo" accept', 'iifname "tailscale0" accept', 'ct state established,related accept',
                         'ip saddr { 127.0.0.0/8, 169.254.0.0/16, 100.64.0.0/10 } accept'):
            self.assertLess(regeln.index(ausnahme), sperre, ausnahme)
        self.assertNotIn('hook output', text)          # ausgehend wird nichts beschränkt
        self.assertNotIn('ssh_fremd drop', text)
        self.assertIn('ssh_fremd drop', self.regelwerk(nur_tailscale=True))
        self.assertIn('limit rate over 20/minute', self.regelwerk(takt=20))

    @unittest.skipUnless(shutil.which('nft') and hasattr(os, 'geteuid') and os.geteuid() == 0, 'braucht root und nft')
    def test_echtes_nft_nimmt_das_regelwerk(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in (('voll', self.regelwerk(nur_tailscale=True)), ('leer', serverschutz.regelwerk([], [], [22, 2222]))):
                datei = Path(tmp) / f'{name}.nft'
                datei.write_text(text, encoding='utf-8')
                r = subprocess.run(['nft', '-c', '-f', str(datei)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            # in einem eigenen Netz-Namensraum wirklich laden – zweimal (Ersetzen) neben einer fremden Tabelle
            if shutil.which('unshare') and subprocess.run(['unshare', '-n', 'true'], capture_output=True).returncode == 0:
                ablauf = (f'nft add table ip fremd && nft -f {tmp}/voll.nft && nft -f {tmp}/leer.nft && nft -f {tmp}/voll.nft'
                          ' && nft list table ip fremd >/dev/null && nft -j list table inet ju_schutz'
                          ' && nft delete table inet ju_schutz && nft list table ip fremd >/dev/null')
                r = subprocess.run(['unshare', '-n', 'sh', '-c', ablauf], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                mengen = {e['set']['name']: e['set'] for e in json.loads(r.stdout)['nftables'] if 'set' in e}
                self.assertTrue(mengen['drop4']['elem'] and mengen['drop6']['elem'])   # auto-merge fasst benachbarte Netze zusammen


class StatusLogikTests(unittest.TestCase):
    def test_v4_mapped_und_lokale_adressen_gelten_als_lokal(self):
        # Keycloak (Java) bindet 127.0.0.1 und erscheint in ss als [::ffff:127.0.0.1] – das ist kein offener Port
        self.assertEqual(serverschutz.lauschende(SS), {('udp', 41641), ('tcp', 22), ('udp', 68)})   # 68 = DHCP-Client an der Schnittstelle
        for adresse in ('[::ffff:127.0.0.1]', '127.0.0.53%lo', '[::1]', '100.64.0.7', '[fd7a:115c:a1e0::7]', '[fe80::1]%eth0', '169.254.0.2'):
            self.assertTrue(serverschutz.lokal(adresse), adresse)
        for adresse in ('*', '0.0.0.0', '[::]', '198.51.100.10', '[::ffff:198.51.100.10]', '[2001:db8::1]'):
            self.assertFalse(serverschutz.lokal(adresse), adresse)
        offen = serverschutz.lauschende(SS + 'tcp LISTEN 0 4096 *:8080 *:*\ntcp LISTEN 0 4096 [::ffff:198.51.100.10]:9001 *:*\n')
        self.assertEqual(offen - {('udp', 41641), ('tcp', 22), ('udp', 68)}, {('tcp', 8080), ('tcp', 9001)})

    def test_was_ufw_durchlaesst(self):
        lage = serverschutz.ufw_lage(UFW)
        self.assertTrue(lage['aktiv'] and lage['gesperrt'])
        self.assertEqual(serverschutz.ufw_durchlass(lage, 'tcp', 22), 'begrenzt')
        self.assertEqual(serverschutz.ufw_durchlass(lage, 'tcp', 8080), 'zu')        # tailscale0 öffnet nichts nach außen
        mehr = serverschutz.ufw_lage(UFW + '41641/udp ALLOW IN Anywhere\n8000:8100/tcp ALLOW IN Anywhere   # Kommentar\nOpenSSH LIMIT IN Anywhere\n')
        self.assertEqual(serverschutz.ufw_durchlass(mehr, 'udp', 41641), 'offen')
        self.assertEqual(serverschutz.ufw_durchlass(mehr, 'tcp', 41641), 'zu')
        self.assertEqual(serverschutz.ufw_durchlass(mehr, 'tcp', 8080), 'offen')
        self.assertEqual(serverschutz.ufw_durchlass(mehr, 'tcp', 22), 'offen')
        for aus in ('Status: inactive\n', UFW.replace('deny (incoming)', 'allow (incoming)')):
            self.assertEqual(serverschutz.ufw_durchlass(serverschutz.ufw_lage(aus), 'tcp', 8080), 'offen')

    def test_gebannte(self):
        self.assertEqual(serverschutz.gebannte("[{'sshd': ['192.0.2.9', '192.0.2.10']}, {'recidive': ['192.0.2.9']}]"), 3)
        self.assertEqual(serverschutz.gebannte('Fehler'), 0)


class Wurzel:
    """Nachgestellter Server nach der Bestandsaufnahme: ufw aktiv, SSH von einer Quelle, Passwort an, Docker aus."""
    BEFEHLE = ('systemctl nft ufw sshd sysctl tailscale curl dpkg-query apt-get apt-config fail2ban-client ss ip '
               'journalctl findmnt').split()

    def __init__(self, test):
        self.w = Path(tempfile.mkdtemp(prefix='ju-haertung-'))
        test.addCleanup(shutil.rmtree, self.w, True)
        self.z = self.w / 'zustand'
        for ordner in ('stub', 'zustand', 'etc/ufw', 'etc/default', 'etc/ssh/sshd_config.d', 'etc/ju-finanzen', 'root/.ssh',
                       'var/lib/ju-finanzen', 'var/log', 'var/tmp', 'tmp', 'run', 'etc/systemd/system'):
            (self.w / ordner).mkdir(parents=True)
        for name in self.BEFEHLE:
            (self.w / 'stub' / name).symlink_to(ROOT / 'tests' / 'haertung_stub.sh')
        for ordner, modus in (('tmp', 0o1777), ('var/tmp', 0o1777), ('var/lib/ju-finanzen', 0o700), ('etc/ju-finanzen', 0o750)):
            (self.w / ordner).chmod(modus)
        self.datei('etc/ju-finanzen/ju-finanzen.env', 'JU_FINANZEN_PORT=8769\n', 0o640)
        self.datei('etc/ju-finanzen/keycloak-admin.env', 'KC_BOOTSTRAP_ADMIN_USERNAME=ju-einrichtung\n', 0o644)   # zu offen
        self.datei('etc/default/ufw', 'IPV6=yes\nDEFAULT_INPUT_POLICY="DROP"\n')
        regeln = 'ufw allow in on tailscale0\nufw allow from 203.0.113.5 to any port 22 proto tcp\n'
        self.datei('etc/ufw/user.rules', regeln)
        self.datei('etc/ufw/user6.rules', '')
        self.datei('etc/ufw/ufw.conf', 'ENABLED=yes\n')
        self.datei('etc/ufw/sysctl.conf', '# ufw\nnet/ipv4/conf/all/accept_redirects=0\nnet/ipv4/conf/all/log_martians=0\n'
                                          'net/ipv4/conf/default/log_martians=0\n')
        self.datei('etc/ssh/sshd_config', 'Include /etc/ssh/sshd_config.d/*.conf\nKbdInteractiveAuthentication no\n')
        self.datei('etc/ssh/sshd_config.d/50-cloud-init.conf', 'PasswordAuthentication yes\n')
        self.datei('etc/passwd', 'root:x:0:0:root:/root:/bin/bash\njufinanzen:x:999:999::/var/lib/ju-finanzen:/usr/sbin/nologin\n'
                                 'postgres:x:110:110::/var/lib/postgresql:/bin/bash\n')
        self.datei('etc/shadow', 'root:$y$erfunden:20000:0:99999:7:::\njufinanzen:!:20000::::::\npostgres:!:20000::::::\n', 0o640)
        kern = re.search(r"^KERN='(.*?)'$", HAERTEN.read_text(encoding='utf-8'), re.S | re.M).group(1)
        self.kern = {zeile.split('|')[0]: zeile.split('|')[1] for zeile in kern.splitlines()}
        for name, soll in self.kern.items():
            self.kernwert(name, soll)
        for name, ist in (('net.ipv4.conf.all.send_redirects', '1'), ('net.ipv4.conf.default.send_redirects', '1'),
                          ('net.ipv4.conf.all.log_martians', '0'), ('net.ipv4.conf.default.log_martians', '0'),
                          ('kernel.kptr_restrict', '1'), ('net.ipv4.ip_forward', '1'), ('kernel.yama.ptrace_scope', '2'),
                          ('kernel.core_pattern', '|/usr/share/apport/apport -p%p -s%s')):
            self.kernwert(name, ist)
        self.zustand('ufw-regeln', regeln)
        self.zustand('ufw-status', '\n'.join(UFW.splitlines()[7:]) + '\n')
        self.zustand('sshd-vorgaben', 'Port 22\nPermitRootLogin yes\nPasswordAuthentication yes\nPubkeyAuthentication yes\n'
                                      'MaxAuthTries 6\nLoginGraceTime 120\nX11Forwarding yes\nAllowTcpForwarding yes\n')
        self.zustand('pakete', 'ufw\nnftables\nunattended-upgrades\n')
        self.zustand('drop.txt', drop4().decode())
        self.zustand('dropv6.txt', drop6().decode())
        self.zustand('ss', SS)
        self.zustand('ip-addr', json.dumps([{'ifname': 'eth0', 'addr_info': [{'local': '198.51.100.10', 'prefixlen': 24}]}]))
        self.zustand('apt-simulation', 'Inst libbeispiel1 [1.0] (1.1 Ubuntu:24.04/noble-security [amd64])\nInst beispiel [2] (3 Ubuntu:24.04/noble-updates [amd64])\n')
        for unit, an, aktiv in (('ssh.service', 'disabled', 'active'), ('docker.service', 'disabled', 'inactive'),
                                ('docker.socket', 'disabled', 'inactive'), ('containerd.service', 'disabled', 'inactive'),
                                ('apt-daily.timer', 'enabled', 'active'), ('apt-daily-upgrade.timer', 'enabled', 'active'),
                                ('unattended-upgrades.service', 'enabled', 'active'), ('ju-finanzen.service', 'enabled', 'active'),
                                ('tailscaled.service', 'enabled', 'active'), ('systemd-journald.service', 'static', 'active')):
            self.einheit(unit, an, aktiv)

    def datei(self, name, text, modus=0o644):
        ziel = self.w / name
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(text, encoding='utf-8')
        ziel.chmod(modus)

    def zustand(self, name, text):
        (self.z / name).write_text(text, encoding='utf-8')

    def einheit(self, unit, an, aktiv):
        self.zustand(f'unit-{unit}', '')
        self.zustand(f'enabled-{unit}', an + '\n')
        self.zustand(f'active-{unit}', aktiv + '\n')

    def kernwert(self, name, wert=None):
        ziel = self.w / 'proc/sys' / name.replace('.', '/')
        if wert is None:
            return ziel.read_text(encoding='utf-8').strip()
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(wert + '\n', encoding='utf-8')

    def lies(self, name):
        return (self.w / name).read_text(encoding='utf-8')

    def lauf(self, *args, stdin=subprocess.DEVNULL):
        env = {**os.environ, 'WURZEL': str(self.w), 'JU_PYTHON': sys.executable, 'JU_PAUSE': '0', 'JU_FRIST': '1', 'TZ': 'Europe/Berlin'}
        env.pop('SUDO_USER', None)
        return subprocess.run(['bash', str(HAERTEN), *args], env=env, capture_output=True, text=True, stdin=stdin, timeout=120)

    def bild(self, ohne=('aufrufe.log',)):
        """Alles unter der Wurzel: Pfad → (Rechte, Inhalt) – zum Vergleich vorher/nachher."""
        bild = {}
        for pfad in sorted(self.w.rglob('*')):
            rel = str(pfad.relative_to(self.w))
            if rel.startswith(tuple(ohne)):
                continue
            modus = stat.S_IMODE(pfad.lstat().st_mode)
            if pfad.is_symlink() or pfad.is_dir():
                bild[rel] = (modus, 'Ordner/Verweis')
            else:
                bild[rel] = (modus, hashlib.sha256(pfad.read_bytes()).hexdigest())
        return bild

    def aufrufe(self):
        return self.lies('aufrufe.log').splitlines() if (self.w / 'aufrufe.log').exists() else []


AENDERND = re.compile(r'^(systemctl (enable|disable|start|stop|restart|reload|try-restart|daemon-reload)|ufw (allow|default|--force|reload)'
                      r'|nft (-f|delete)|sysctl|apt-get (install|update)|curl .*spamhaus)')
# was ein Lauf außerhalb der gesicherten Dateien hinterlässt: Sicherungen, Arbeitsdaten des Serverschutzes, Sperre, leeres Protokoll
NEBENBEI = ('aufrufe.log', 'zustand', 'var/backups', 'var/lib/ju-serverschutz', 'run/lock', 'var/log/fail2ban.log',
            'etc/fail2ban', 'etc/security', 'etc/sysctl.d', 'etc/apt', 'etc/systemd/journald.conf.d')


@unittest.skipUnless(LINUX, 'braucht Linux mit bash und flock')
class HaertenTests(unittest.TestCase):
    def setUp(self):
        self.s = Wurzel(self)

    def gut(self, *args):
        r = self.s.lauf(*args)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
        return r.stdout

    def test_pruefen_aendert_nichts_und_nennt_die_lage(self):
        vorher = self.s.bild()
        out = self.gut('--pruefen')
        self.assertEqual(self.s.bild(), vorher)
        self.assertEqual([a for a in self.s.aufrufe() if AENDERND.match(a)], [])
        self.assertIn('Befund:   Passwort-Anmeldung für root offen, durch ufw auf eine Quelle begrenzt', out)
        self.assertIn('ufw aktiv, eingehend gesperrt', out)
        self.assertIn('Docker: kein Container', out)
        self.assertNotIn('docker.service abschalten', out)            # ist schon aus
        self.assertNotIn('alles auf tailscale0 erlauben', out)        # Regel gibt es schon
        self.assertNotIn('SSH-Port 22 offen lassen', out)             # ufw ist an: an SSH-Regeln wird nichts geändert
        for erwartet in ('Änderung: ufw: Tailscale-UDP 41641 erlauben', 'Änderung: Paket fail2ban', 'net.ipv4.conf.all.log_martians (ist 0, soll 1)',
                         'net.ipv4.conf.all.send_redirects (ist 1, soll 0)', 'Änderung: /etc/ssh/sshd_config.d/00-ju-haertung.conf anlegen',
                         'Rechte von /etc/ju-finanzen/keycloak-admin.env: 644', 'Risiko:', 'nichts wurde geändert'):
            self.assertIn(erwartet, out)
        self.assertNotIn('kernel.yama.ptrace_scope (', out)           # der strengere vorhandene Wert bleibt
        # mit den riskanten Schaltern: nur die Ablehnung berichten
        out = self.gut('--pruefen', '--ssh-nur-schluessel')
        self.assertIn('ABGELEHNT: SSH nur mit Schlüssel: für das Konto root ist kein SSH-Schlüssel hinterlegt', out)
        self.assertEqual(self.s.bild(), vorher)

    def test_anwenden_wiederholen_zurueck(self):
        vorher = self.s.bild(NEBENBEI)
        out = self.gut()
        self.assertIn('Fertig:', out)
        # 1 Firewall: nur ergänzt, eigene Tabelle geladen, Ausnahmen und Sperrliste ohne die eigene Adresse
        self.assertEqual(self.s.lies('zustand/ufw-regeln').splitlines()[-1], 'ufw allow 41641/udp')
        self.assertTrue((self.s.z / 'tabelle').exists())
        regelwerk = self.s.lies('var/lib/ju-serverschutz/ju_schutz.nft')
        self.assertIn('192.0.2.0/30', regelwerk)
        self.assertNotIn('198.51.100.8/30', regelwerk)                # darin läge die eigene Adresse 198.51.100.10
        self.assertEqual(len(self.s.lies('var/lib/ju-serverschutz/drop4.txt').splitlines()), 150)
        self.assertEqual(self.s.lies('zustand/enabled-ju-serverschutz.service').strip(), 'enabled')
        self.assertEqual(self.s.lies('zustand/enabled-ju-serverschutz-liste.timer').strip(), 'enabled')
        # 3 fail2ban: Tailscale und die Konsole des Anbieters werden nie gesperrt
        jail = self.s.lies('etc/fail2ban/jail.d/ju-haertung.local')
        self.assertIn('ignoreip = 127.0.0.0/8 ::1 169.254.0.0/16 100.64.0.0/10 fd7a:115c:a1e0::/48\n', jail)
        self.assertIn('[recidive]', jail)
        # 4 Kern: geändert wird nur, was abweicht; rp_filter bleibt 2; ufw setzt denselben Wert
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.log_martians'), '1')
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.rp_filter'), '2')
        self.assertEqual(self.s.kernwert('net.ipv4.ip_forward'), '0')
        self.assertEqual(self.s.kernwert('kernel.yama.ptrace_scope'), '2')
        self.assertIn('net/ipv4/conf/all/log_martians=1', self.s.lies('etc/ufw/sysctl.conf'))
        kern = self.s.lies('etc/sysctl.d/80-ju-haertung.conf')
        self.assertLess(kern.index('net.ipv4.ip_forward = 0'), kern.index('net.ipv4.tcp_syncookies = 1'))
        self.assertEqual([z for z in kern.splitlines() if z and not z.startswith('#') and not re.fullmatch(r'[a-z0-9_.]+ = \d+', z)], [])
        # 7 SSH: Grundhärtung, aber weder Passwort noch root-Anmeldung angefasst
        ssh = self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf')
        self.assertIn('MaxAuthTries 3', ssh)
        self.assertIn('AllowTcpForwarding local', ssh)
        self.assertNotRegex(ssh, r'(?m)^(PasswordAuthentication|PermitRootLogin)')
        self.assertIn('sshd -t', self.s.aufrufe())
        self.assertIn('systemctl reload ssh.service', self.s.aufrufe())
        # 8 Rechte, Core-Dumps, journald
        self.assertEqual(stat.S_IMODE((self.s.w / 'etc/ju-finanzen/keycloak-admin.env').stat().st_mode), 0o600)
        self.assertEqual(self.s.kernwert('kernel.core_pattern'), '|/bin/false')
        self.assertIn('SystemMaxUse=1G', self.s.lies('etc/systemd/journald.conf.d/60-ju-haertung.conf'))
        self.assertIn('Automatic-Reboot "false"', self.s.lies('etc/apt/apt.conf.d/52ju-haertung'))
        # 9 Statusbericht: für den Dienst lesbar, ohne Adressen
        status = self.s.w / 'var/lib/ju-serverschutz/status.json'
        self.assertEqual(stat.S_IMODE(status.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(status.parent.stat().st_mode), 0o755)
        # Sicherung: Ordner nur für root, Protokoll, Manifest
        laeufe = sorted((self.s.w / 'var/backups/ju-haertung').iterdir())
        self.assertEqual(len(laeufe), 1)
        self.assertEqual(stat.S_IMODE(laeufe[0].stat().st_mode), 0o700)
        self.assertIn('Ändere:', (laeufe[0] / 'protokoll.log').read_text(encoding='utf-8'))
        self.assertEqual((laeufe[0] / 'dateien/etc/ufw/sysctl.conf').read_text(encoding='utf-8').count('log_martians=0'), 2)

        # zweiter Lauf: nichts zu tun, keine neue Sicherung, kein ändernder Aufruf
        zwischen, n = self.s.bild(('aufrufe.log', 'var/lib/ju-serverschutz/status.json', 'var/lib/ju-serverschutz/zaehler.json')), len(self.s.aufrufe())
        out = self.gut()
        self.assertIn('Alles war bereits umgesetzt', out)
        self.assertEqual(self.s.bild(('aufrufe.log', 'var/lib/ju-serverschutz/status.json', 'var/lib/ju-serverschutz/zaehler.json')), zwischen)
        self.assertEqual([a for a in self.s.aufrufe()[n:] if AENDERND.match(a)], [])
        self.assertIn('Änderung: keine', self.gut('--pruefen'))
        self.assertIn('0 Änderungen stünden an', self.gut('--pruefen'))

        # zurück: jede geänderte Datei, jedes Recht, jeder Kern-Wert und jeder Dienst wie vor dem Lauf
        self.assertIn('würde zurücknehmen: datei /etc/ssh/sshd_config.d/00-ju-haertung.conf', self.gut('--zurueck', '--pruefen'))
        out = self.gut('--zurueck')
        self.assertEqual(self.s.bild(NEBENBEI), vorher)
        for rest in ('etc/fail2ban/jail.d/ju-haertung.local', 'etc/sysctl.d/80-ju-haertung.conf', 'etc/apt/apt.conf.d/52ju-haertung',
                     'etc/systemd/journald.conf.d/60-ju-haertung.conf', 'var/lib/ju-serverschutz/ju_schutz.nft'):
            self.assertFalse((self.s.w / rest).exists(), rest)
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertEqual(self.s.lies('zustand/ufw-regeln'), self.s.lies('etc/ufw/user.rules'))
        self.assertNotIn('41641', self.s.lies('zustand/ufw-regeln'))
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.log_martians'), '0')
        self.assertEqual(self.s.kernwert('net.ipv4.ip_forward'), '1')
        self.assertEqual(self.s.kernwert('kernel.core_pattern'), '|/usr/share/apport/apport -p%p -s%s')
        self.assertEqual(self.s.lies('zustand/enabled-fail2ban.service').strip(), 'disabled')
        self.assertEqual(self.s.lies('zustand/active-ju-serverschutz-liste.timer').strip(), 'inactive')
        self.assertNotIn('nft delete table inet filter', '\n'.join(self.s.aufrufe()))
        r = self.s.lauf('--zurueck')
        self.assertEqual(r.returncode, 1)
        self.assertIn('keinen Lauf, der sich zurücknehmen lässt', r.stderr)

    def test_fehler_in_einer_massnahme_nimmt_sie_zurueck(self):
        self.s.zustand('scheitert-sshd', '-t')
        r = self.s.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn('sshd -t meldet einen Fehler', r.stderr)
        self.assertIn('ist gescheitert und wurde zurückgenommen', r.stderr)
        self.assertFalse((self.s.w / 'etc/ssh/sshd_config.d/00-ju-haertung.conf').exists())
        self.assertNotIn('systemctl reload ssh.service', self.s.aufrufe())
        self.assertTrue((self.s.w / 'etc/sysctl.d/80-ju-haertung.conf').exists())        # frühere Maßnahmen bleiben
        self.assertFalse((self.s.w / 'etc/systemd/journald.conf.d').exists())            # spätere wurden nicht begonnen
        (self.s.z / 'scheitert-sshd').unlink()
        self.assertIn('Fertig:', self.gut())                                             # danach einfach erneut

    def test_firewall_wird_zurueckgenommen_wenn_danach_etwas_nicht_mehr_erreichbar_ist(self):
        self.s.zustand('firewall-bricht', '')
        vorher = self.s.bild(NEBENBEI)
        r = self.s.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn('Nach der Firewall-Änderung nicht mehr erreichbar: Programm', r.stderr)
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertFalse((self.s.w / 'var/lib/ju-serverschutz/ju_schutz.nft').exists())
        self.assertNotIn('41641', self.s.lies('zustand/ufw-regeln'))
        self.assertIn('ufw reload', self.s.aufrufe())
        self.assertEqual(self.s.bild(NEBENBEI + ('etc/ju-finanzen/haertung.conf',)), {k: v for k, v in vorher.items()})

    def test_rueckfrage_am_terminal(self):
        import pty
        # keine Eingabe (die Verbindung hängt): die Firewall-Änderung wird nach der Frist von selbst zurückgenommen
        haupt, neben = pty.openpty()
        self.addCleanup(os.close, haupt)
        self.addCleanup(os.close, neben)
        r = self.s.lauf(stdin=neben)
        self.assertEqual(r.returncode, 1)
        self.assertIn('Keine Bestätigung innerhalb von 1 Sekunden', r.stderr)
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertNotIn('41641', self.s.lies('zustand/ufw-regeln'))
        # Eingabetaste: die Änderung bleibt; --ohne-rueckfrage fragt gar nicht
        os.write(haupt, b'\n')
        r = self.s.lauf(stdin=neben)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Zum Behalten innerhalb von 1 Sekunden die Eingabetaste drücken', r.stdout)
        self.assertTrue((self.s.z / 'tabelle').exists())
        self.s.lauf('--zurueck')
        self.assertNotIn('Eingabetaste', self.gut_mit(neben, '--ohne-rueckfrage'))

    def gut_mit(self, stdin, *args):
        r = self.s.lauf(*args, stdin=stdin)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_ufw_aus_ssh_bleibt_offen_wie_vorgefunden(self):
        # auch eine Regel „nur von einer Adresse“ zählt nicht: ohne Firewall war SSH bisher von überall erreichbar
        self.s.zustand('ufw-aus', '')
        self.s.zustand('ufw-regeln', 'ufw allow from 203.0.113.5 to any port 22 proto tcp\n')
        self.s.datei('etc/ufw/user.rules', 'ufw allow from 203.0.113.5 to any port 22 proto tcp\n')
        self.s.datei('etc/default/ufw', 'IPV6=yes\nDEFAULT_INPUT_POLICY="ACCEPT"\n')
        out = self.gut('--pruefen')
        self.assertIn('ufw AUS', out)
        self.assertIn('SSH-Port 22 offen lassen wie vorgefunden', out)
        self.gut()
        regeln = self.s.lies('zustand/ufw-regeln').splitlines()
        self.assertEqual(regeln[1:], ['ufw allow 22/tcp', 'ufw allow in on tailscale0', 'ufw allow 41641/udp'])
        aufrufe = self.s.aufrufe()
        self.assertLess(aufrufe.index('ufw allow 22/tcp'), aufrufe.index('ufw --force enable'))   # erst öffnen, dann einschalten
        self.assertFalse((self.s.z / 'ufw-aus').exists())
        self.gut('--zurueck')
        self.assertTrue((self.s.z / 'ufw-aus').exists())

    def test_ssh_nur_schluessel_nur_mit_nachweis(self):
        vorher = self.s.bild()
        r = self.s.lauf('--ssh-nur-schluessel')
        self.assertEqual(r.returncode, 1)
        self.assertIn('Abgelehnt – nichts geändert', r.stderr)
        self.assertEqual(self.s.bild(('aufrufe.log', 'run')), {k: v for k, v in vorher.items() if not k.startswith('run')})
        # Schlüssel hinterlegt, aber noch nie damit angemeldet: weiter abgelehnt
        self.s.datei('root/.ssh/authorized_keys', 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBeispielBeispielBeispielBeispielBeispielBeispi beispiel\n', 0o600)
        r = self.s.lauf('--ssh-nur-schluessel')
        self.assertEqual(r.returncode, 1)
        self.assertIn('keine Anmeldung damit gelungen', r.stdout)
        self.s.zustand('journal', 'Accepted password for root from 203.0.113.5 port 50000 ssh2\n'
                                  'Accepted publickey for root from 203.0.113.5 port 50001 ssh2: ED25519 SHA256:Beispiel\n')
        self.gut()                                                     # Grundhärtung zuerst, der Schalter danach als eigener Lauf
        out = self.gut('--ssh-nur-schluessel')
        self.assertIn('WARNUNG: Ohne Passwort-Anmeldung funktioniert das Browser-Terminal des Anbieters möglicherweise nicht mehr', out)
        self.assertIn('PasswordAuthentication no', self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf'))
        self.assertIn('SSH_NUR_SCHLUESSEL=ja', self.s.lies('etc/ju-finanzen/haertung.conf'))
        self.assertIn('Alles war bereits umgesetzt', self.gut())       # ein späterer Lauf ohne Schalter schaltet es nicht zurück
        self.assertIn('PasswordAuthentication no', self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf'))
        self.gut('--zurueck')
        self.assertNotIn('PasswordAuthentication no', self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf'))
        self.assertIn('SSH_NUR_SCHLUESSEL=nein', self.s.lies('etc/ju-finanzen/haertung.conf'))

    def test_ssh_nur_tailscale_nur_wenn_tailscale_laeuft(self):
        self.s.zustand('tailscale-status', '{"BackendState": "Stopped"}')
        r = self.s.lauf('--ssh-nur-tailscale')
        self.assertEqual(r.returncode, 1)
        self.assertIn('Tailscale läuft nicht', r.stdout)
        self.s.zustand('tailscale-status', '{"BackendState": "Running"}')
        self.s.zustand('tailscale-prefs', '{"RunSSH": false, "AdvertiseRoutes": null}')
        self.s.zustand('ss', 'tcp ESTAB 0 0 198.51.100.10:22 203.0.113.5:50000\n')   # die laufende Sitzung kommt nicht über Tailscale
        r = self.s.lauf('--ssh-nur-tailscale')
        self.assertEqual(r.returncode, 1)
        self.assertIn('kein SSH-Weg über das Tailnet nachgewiesen', r.stdout)
        self.assertFalse((self.s.w / 'var/backups/ju-haertung').exists())
        self.s.zustand('ss', 'tcp ESTAB 0 0 100.64.0.7:22 100.64.0.9:50000\n')        # Nachweis: SSH-Sitzung über das Tailnet
        self.gut('--ssh-nur-tailscale')
        regelwerk = self.s.lies('var/lib/ju-serverschutz/ju_schutz.nft')
        self.assertIn('ct state new counter name ssh_fremd drop', regelwerk)
        self.assertLess(regelwerk.index('169.254.0.0/16'), regelwerk.index('ssh_fremd drop'))   # Konsole des Anbieters bleibt
        self.assertIn('Alles war bereits umgesetzt', self.gut())

    def test_schalter_nein_entfernt_die_eigenen_dateien(self):
        self.gut()
        conf = self.s.lies('etc/ju-finanzen/haertung.conf')
        self.s.datei('etc/ju-finanzen/haertung.conf', conf.replace('FIREWALL=ja', 'FIREWALL=nein').replace('SSH=ja', 'SSH=nein')
                     .replace('STATUS=ja', 'STATUS=nein'))
        self.gut()
        for weg in ('var/lib/ju-serverschutz/ju_schutz.nft', 'etc/systemd/system/ju-serverschutz.service',
                    'etc/ssh/sshd_config.d/00-ju-haertung.conf', 'var/lib/ju-serverschutz/status.json'):
            self.assertFalse((self.s.w / weg).exists(), weg)
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertIn('41641', self.s.lies('zustand/ufw-regeln'))      # ufw bleibt, wie es ist
        self.s.datei('etc/ju-finanzen/haertung.conf', conf.replace('KERN=ja', 'KERN=vielleicht'))
        self.assertIn('KERN muss ja oder nein sein', self.s.lauf('--pruefen').stderr)

    def test_docker_mit_containern_bleibt(self):
        self.s.einheit('docker.service', 'enabled', 'active')
        (self.s.w / 'var/lib/docker/containers/beispiel').mkdir(parents=True)
        out = self.gut()
        self.assertIn('Docker: 1 Container vorhanden – bleibt an', out)
        self.assertEqual(self.s.lies('zustand/active-docker.service').strip(), 'active')
        self.assertEqual(self.s.kernwert('net.ipv4.ip_forward'), '1')   # Weiterleitung bleibt für die Container
        shutil.rmtree(self.s.w / 'var/lib/docker/containers/beispiel')
        self.gut()
        self.assertEqual(self.s.lies('zustand/active-docker.service').strip(), 'inactive')

    def test_sperrliste_taeglich_schlechte_liste_laesst_die_gute(self):
        self.gut()
        gut = self.s.lies('var/lib/ju-serverschutz/drop4.txt')
        regelwerk = self.s.lies('var/lib/ju-serverschutz/ju_schutz.nft')
        for name, inhalt in (('privat', drop4().decode() + '10.0.0.0/8 ; SBL1\n'), ('klein', drop4(20).decode()), ('kaputt', 'kaputt\n')):
            with self.subTest(name):
                self.s.zustand('drop.txt', inhalt)
                self.assertEqual(self.s.lauf('sperrliste').returncode, 1)
                self.assertEqual(self.s.lies('var/lib/ju-serverschutz/drop4.txt'), gut)
                self.assertEqual(self.s.lies('var/lib/ju-serverschutz/ju_schutz.nft'), regelwerk)
                self.assertTrue((self.s.w / 'var/lib/ju-serverschutz/sperrliste.fehler').exists())
        self.s.zustand('drop.txt', drop4(120).decode())
        self.assertEqual(self.s.lauf('sperrliste').returncode, 0)
        self.assertEqual(len(self.s.lies('var/lib/ju-serverschutz/drop4.txt').splitlines()), 120)
        self.assertFalse((self.s.w / 'var/lib/ju-serverschutz/sperrliste.fehler').exists())
        self.assertNotIn('198.51.100.8/30', self.s.lies('var/lib/ju-serverschutz/ju_schutz.nft'))
        self.assertEqual(self.s.aufrufe()[-1], 'systemctl reload ju-serverschutz.service')
        # Notaus bleibt Notaus: bei gestoppter Tabelle lädt der Zeitgeber sie nicht wieder
        self.s.zustand('active-ju-serverschutz.service', 'inactive\n')
        self.s.zustand('drop.txt', drop4(130).decode())
        n = len(self.s.aufrufe())
        self.assertEqual(self.s.lauf('sperrliste').returncode, 0)
        self.assertNotIn('systemctl reload ju-serverschutz.service', self.s.aufrufe()[n:])

    def test_statusbericht(self):
        self.gut()
        self.s.einheit('ju-keycloak.service', 'enabled', 'active')
        self.s.datei('etc/systemd/system/ju-keycloak.service', '[Unit]\n')
        self.s.einheit('postgresql.service', 'enabled', 'active')
        (self.s.w / 'run/reboot-required').write_text('', encoding='utf-8')
        out = self.gut('status')
        roh = self.s.lies('var/lib/ju-serverschutz/status.json')
        daten = json.loads(roh)
        self.assertEqual(daten['version'], 1)
        self.assertRegex(daten['zeit'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$')
        staende = {p['id']: p['stand'] for p in daten['pruefungen']}
        self.assertTrue(all(set(p) == {'id', 'titel', 'stand', 'detail'} and p['stand'] in serverschutz.STAENDE for p in daten['pruefungen']))
        self.assertEqual(staende, {
            'firewall': 'ok', 'schutztabelle': 'ok', 'sperrliste': 'ok', 'fail2ban': 'ok', 'ssh_passwort': 'hinweis', 'ssh_erreichbar': 'ok',
            'ports': 'ok', 'updates': 'ok', 'updates_offen': 'hinweis', 'neustart': 'hinweis', 'kern': 'ok', 'tailscale': 'ok',
            'dienste': 'ok', 'docker': 'ok', 'keycloak_sicherung': 'hinweis', 'rechte': 'ok', 'platz': staende['platz']})
        self.assertEqual(daten['zahlen'], {'sperrliste_netze': 162, 'sperrliste_stand': daten['zahlen']['sperrliste_stand'], 'gebannt': 2,
                                           'abgewiesen_24h': 0, 'updates_offen': 2, 'neustart_noetig': True})
        self.assertRegex(daten['zahlen']['sperrliste_stand'], r'^\d{4}-\d\d-\d\dT')
        # keine Adressen, Namen oder Geheimnisse im Bericht
        ohne_zeiten = re.sub(r'\d{4}-\d\d-\d\dT[\d:+]+', '', roh)
        self.assertNotRegex(ohne_zeiten, r'\d+\.\d+\.\d+\.\d+|[0-9a-f]{1,4}:[0-9a-f:]{3,}|ju-einrichtung|example|eth0|SBL')
        self.assertIn('HINWEIS   SSH-Anmeldung per Passwort: an; SSH ist durch die Firewall auf einzelne Quellen begrenzt', out)
        # Zähler: Zuwachs seit dem letzten Bericht, auch über ein Neuladen der Tabelle hinweg
        self.s.zustand('zaehler.json', '{"nftables": [{"counter": {"name": "sperrliste", "packets": 25}}]}')
        self.gut('status-schreiben')
        self.assertEqual(json.loads(self.s.lies('var/lib/ju-serverschutz/status.json'))['zahlen']['abgewiesen_24h'], 15)
        self.s.zustand('zaehler.json', '{"nftables": [{"counter": {"name": "sperrliste", "packets": 4}}]}')
        self.gut('status-schreiben')
        self.assertEqual(json.loads(self.s.lies('var/lib/ju-serverschutz/status.json'))['zahlen']['abgewiesen_24h'], 19)
        # kritisch: Tabelle weg, Dienst steht, ein Port offen, den ufw durchlässt; fehlende Sicherung nach zwei Tagen
        (self.s.z / 'tabelle').unlink()
        self.s.zustand('active-ju-keycloak.service', 'failed\n')
        self.s.zustand('ss', SS + 'tcp   LISTEN 0      4096                   *:8080            *:*\n'
                                  'tcp   LISTEN 0      4096                   *:5432            *:*\n')
        self.s.zustand('ufw-status', self.s.lies('zustand/ufw-status') + '8080/tcp                   ALLOW IN    Anywhere\n')
        self.s.datei('var/backups/ju-keycloak/keycloak-2026-11-19.dump', '', 0o600)
        self.gut('status-schreiben')
        daten = json.loads(self.s.lies('var/lib/ju-serverschutz/status.json'))
        staende = {p['id']: (p['stand'], p['detail']) for p in daten['pruefungen']}
        self.assertEqual(staende['schutztabelle'][0], 'kritisch')
        self.assertEqual(staende['dienste'], ('kritisch', 'läuft nicht: ju-keycloak'))
        self.assertEqual(staende['ports'], ('kritisch', 'unerwartet aus dem Internet erreichbar: 8080/tcp'))
        self.assertEqual(staende['keycloak_sicherung'][0], 'ok')
        # nur von der Firewall gesperrte Ports sind ein Hinweis, kein Alarm
        self.s.zustand('ufw-status', '\n'.join(UFW.splitlines()[7:]) + '\n')
        self.gut('status-schreiben')
        staende = {p['id']: (p['stand'], p['detail']) for p in json.loads(self.s.lies('var/lib/ju-serverschutz/status.json'))['pruefungen']}
        self.assertEqual(staende['ports'], ('hinweis', 'lauscht auf allen Adressen, von der Firewall gesperrt: 5432/tcp, 8080/tcp'))

    def test_abgewiesen_verlauf_vergisst_nach_24_stunden(self):
        with mock.patch.object(serverschutz, 'W', str(self.s.w)):
            jetzt = time.time()
            self.assertEqual(serverschutz.abgewiesen_24h(100, jetzt - 100000), 0)
            self.assertEqual(serverschutz.abgewiesen_24h(130, jetzt - 90000), 30)
            self.assertEqual(serverschutz.abgewiesen_24h(135, jetzt - 1000), 5)        # die 30 sind über 24 Stunden her
            self.assertEqual(serverschutz.abgewiesen_24h(140, jetzt), 10)
            self.assertEqual(serverschutz.abgewiesen_24h(None, jetzt), 10)     # Tabelle nicht lesbar: Stand bleibt


class EinbindungTests(unittest.TestCase):
    def test_verwaltungsbefehl_und_units(self):
        befehl = (ROOT / 'server' / 'ju-finanzen').read_text(encoding='utf-8')
        self.assertIn('haerten|schutz-status)', befehl)
        self.assertLess(befehl.index('haerten|schutz-status)'), befehl.index('*) run "$@"'))   # nicht an verwaltung.py durchreichen
        hilfe = befehl.splitlines()[1:19]
        self.assertTrue(all(z.startswith('#') for z in hilfe) and 'schutz-status' in hilfe[-1] and befehl.splitlines()[19] == 'set -euo pipefail')
        for unit in ('ju-serverschutz.service', 'ju-serverschutz-liste.service', 'ju-serverschutz-liste.timer',
                     'ju-serverschutz-status.service', 'ju-serverschutz-status.timer'):
            text = (ROOT / 'server' / 'haertung' / unit).read_text(encoding='utf-8')
            self.assertNotIn('flush ruleset\n', text.replace('„flush ruleset“', ''))
            for zeile in re.findall(r'^Exec\w+=-?(\S+)', text, re.M):
                self.assertIn(zeile, ('/usr/sbin/nft', '/opt/ju-finanzen/current/server/haerten.sh'))
        # der Dienst des Programms darf die Datei lesen: ProtectSystem=strict macht nur schreibgeschützt, nichts unsichtbar
        dienst = (ROOT / 'server' / 'ju-finanzen.service').read_text(encoding='utf-8')
        self.assertNotRegex(dienst, r'(?m)^(InaccessiblePaths|TemporaryFileSystem|ProtectSystem=(?!strict))')

    def test_vorlage_der_schalter_ist_vollstaendig(self):
        vorlage = (ROOT / 'server' / 'haertung' / 'haertung.conf').read_text(encoding='utf-8')
        skript = HAERTEN.read_text(encoding='utf-8') + (ROOT / 'server' / 'haertung' / 'serverschutz.py').read_text(encoding='utf-8')
        gelesen = set(re.findall(r'\b(?:an|wert) ([A-Z][A-Z0-9_]+) ', skript)) | set(re.findall(r"(?:an|einstellung)\('([A-Z_0-9]+)'", skript))
        self.assertEqual(gelesen, set(re.findall(r'^([A-Z][A-Z0-9_]+)=', vorlage, re.M)))


if __name__ == '__main__':
    unittest.main()
