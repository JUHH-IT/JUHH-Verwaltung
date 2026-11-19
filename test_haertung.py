"""Härtung des Betriebssystems (server/haerten.sh, server/haertung/serverschutz.py) – ohne root, ohne Netz, ohne systemd.

Das Skript läuft gegen ein nachgestelltes Wurzelverzeichnis (WURZEL); Systembefehle kommen dort nur aus <Wurzel>/stub
(tests/haertung_stub.sh). Alle Adressen sind erfunden (Dokumentationsbereiche 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24,
2001:db8::/32). Nicht nachstellbar und deshalb hier ungeprüft: echtes systemd (auch systemd-run und die geplante
Rücknahme als Timer-Unit), ufw, Tailscale, fail2ban und sshd.
Das erzeugte Regelwerk prüft und lädt ein Test zusätzlich mit dem echten nft – nur als root mit nft (sonst übersprungen).
"""
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
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
        return serverschutz.regelwerk([22], **mehr)

    def mengen(self):
        return serverschutz.mengen(serverschutz.liste_lesen(drop4(), 4), serverschutz.liste_lesen(drop6(), 6))

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
        # IPv6: Link-Local und die Nachbar-/Router-Erkennung (ICMPv6 133–136) stehen vor der Sperrliste
        sperre6 = regeln.index('ip6 saddr @drop6 counter name sperrliste drop')
        for ausnahme in ('ip6 saddr { ::1, fe80::/10, fd7a:115c:a1e0::/48 } accept',
                         'icmpv6 type { nd-router-solicit, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert } accept'):
            self.assertLess(regeln.index(ausnahme), sperre6, ausnahme)
        self.assertNotIn('hook output', text)          # ausgehend wird nichts beschränkt
        self.assertNotIn('ssh_fremd drop', text)
        self.assertIn('ssh_fremd drop', self.regelwerk(nur_tailscale=True))
        self.assertIn('limit rate over 20/minute', self.regelwerk(takt=20))

    def test_mengen_tauschen_nur_die_sperrliste(self):
        # die Regeln enthalten keine Adressen der Liste; der Austausch fasst nur drop4/drop6 an – nie Regeln, nie die Tabelle
        self.assertNotIn('192.0.2.0/30', self.regelwerk())
        zeilen = [z for z in self.mengen().splitlines() if z and not z.startswith(('#', '\t', '}'))]
        self.assertEqual(zeilen, ['flush set inet ju_schutz drop4', 'flush set inet ju_schutz drop6',
                                  'add element inet ju_schutz drop4 {', 'add element inet ju_schutz drop6 {'])
        self.assertEqual([z for z in serverschutz.mengen([], []).splitlines() if not z.startswith('#')],
                         ['flush set inet ju_schutz drop4', 'flush set inet ju_schutz drop6'])

    @unittest.skipUnless(shutil.which('nft') and hasattr(os, 'geteuid') and os.geteuid() == 0, 'braucht root und nft')
    def test_echtes_nft_nimmt_das_regelwerk(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in (('voll', self.regelwerk(nur_tailscale=True)), ('leer', serverschutz.regelwerk([22, 2222])),
                               ('mengen', self.mengen()), ('ohne', serverschutz.mengen([], []))):
                datei = Path(tmp) / f'{name}.nft'
                datei.write_text(text, encoding='utf-8')
            for name in ('voll', 'leer'):
                r = subprocess.run(['nft', '-c', '-f', f'{tmp}/{name}.nft'], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            # in einem eigenen Netz-Namensraum wirklich laden – Regeln zweimal (Ersetzen) neben einer fremden Tabelle, dann
            # die Mengen austauschen (zweimal, mit nft -c davor): die Regeln bleiben dabei, wie sie sind
            if shutil.which('unshare') and subprocess.run(['unshare', '-n', 'true'], capture_output=True).returncode == 0:
                ablauf = (f'nft add table ip fremd && nft -f {tmp}/voll.nft && nft -f {tmp}/leer.nft && nft -f {tmp}/voll.nft'
                          f' && nft -c -f {tmp}/mengen.nft && nft -f {tmp}/mengen.nft && nft -f {tmp}/ohne.nft && nft -f {tmp}/mengen.nft'
                          ' && nft list table ip fremd >/dev/null && nft -j list table inet ju_schutz'
                          ' && nft delete table inet ju_schutz && nft list table ip fremd >/dev/null')
                r = subprocess.run(['unshare', '-n', 'sh', '-c', ablauf], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                mengen = {e['set']['name']: e['set'] for e in json.loads(r.stdout)['nftables'] if 'set' in e}
                self.assertTrue(mengen['drop4']['elem'] and mengen['drop6']['elem'])   # auto-merge fasst benachbarte Netze zusammen
                self.assertIn('ssh_fremd', r.stdout)                                   # die Regel hat den Austausch überstanden
                # ohne geladene Tabelle lehnt nft die Mengen ab – so bleibt ein Notaus ein Notaus
                r = subprocess.run(['unshare', '-n', 'nft', '-c', '-f', f'{tmp}/mengen.nft'], capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0)


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
        # „ufw status verbose“ nennt bei Anwendungsprofilen die Ports und hängt den Namen an
        profil = serverschutz.ufw_lage(UFW.replace('22/tcp                     ALLOW IN    203.0.113.5\n', '')
                                       + '22/tcp (OpenSSH)           ALLOW IN    Anywhere\n'
                                         '80,443/tcp (Nginx Full)    ALLOW IN    Anywhere\n'
                                         '22/tcp (OpenSSH (v6))      ALLOW IN    Anywhere (v6)\n'
                                         '6000:6007/udp (Beispiel)   ALLOW IN    198.51.100.7\n')
        self.assertEqual(serverschutz.ufw_durchlass(profil, 'tcp', 22), 'offen')
        self.assertEqual(serverschutz.ufw_durchlass(profil, 'tcp', 443), 'offen')
        self.assertEqual(serverschutz.ufw_durchlass(profil, 'tcp', 80), 'offen')
        self.assertEqual(serverschutz.ufw_durchlass(profil, 'udp', 443), 'zu')
        self.assertEqual(serverschutz.ufw_durchlass(profil, 'tcp', 8080), 'zu')
        self.assertEqual(serverschutz.ufw_durchlass(profil, 'udp', 6003), 'begrenzt')
        for aus in ('Status: inactive\n', UFW.replace('deny (incoming)', 'allow (incoming)')):
            self.assertEqual(serverschutz.ufw_durchlass(serverschutz.ufw_lage(aus), 'tcp', 8080), 'offen')

    def test_gebannte(self):
        self.assertEqual(serverschutz.gebannte("[{'sshd': ['192.0.2.9', '192.0.2.10']}, {'recidive': ['192.0.2.9']}]"), 3)
        self.assertEqual(serverschutz.gebannte('Fehler'), 0)


class Wurzel:
    """Nachgestellter Server nach der Bestandsaufnahme: ufw aktiv, SSH von einer Quelle, Passwort an, Docker aus."""
    BEFEHLE = ('systemctl systemd-run nft ufw sshd sysctl tailscale curl dpkg-query apt-get apt-config fail2ban-client ss ip '
               'journalctl findmnt').split()

    def __init__(self, test):
        self.w = Path(tempfile.mkdtemp(prefix='ju-haertung-'))
        test.addCleanup(shutil.rmtree, self.w, True)
        self.z = self.w / 'zustand'
        for ordner in ('stub', 'zustand', 'etc/ufw', 'etc/default', 'etc/ssh/sshd_config.d', 'etc/ju-finanzen', 'root/.ssh',
                       'var/lib/ju-finanzen', 'var/log', 'var/tmp', 'tmp', 'run/sshd', 'proc/self', 'etc/systemd/system'):
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
        self.datei('etc/ufw/sysctl.conf', '# ufw\n#net/ipv4/ip_forward=1\nnet/ipv4/conf/all/accept_redirects=0\n'
                                          'net/ipv4/conf/all/log_martians=0\nnet/ipv4/conf/default/log_martians=0\n')
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

    def lauf(self, *args, sitzung='7', frist='300', gruppe=False):
        """haerten.sh gegen die Wurzel. sitzung = Kennung der Anmeldesitzung; gruppe = eigene Prozessgruppe (für Signale)."""
        (self.w / 'proc/self/sessionid').write_text(sitzung, encoding='utf-8')
        env = {**os.environ, 'WURZEL': str(self.w), 'JU_PYTHON': sys.executable, 'JU_PAUSE': '0', 'JU_FRIST': frist, 'TZ': 'Europe/Berlin'}
        for name in ('SUDO_USER', 'SSH_CONNECTION'):
            env.pop(name, None)
        mehr = {}
        if gruppe:   # Signale gelten wie am Terminal der ganzen Gruppe; ein vom Aufrufer geerbtes „ignorieren“ ließe sich nicht abfangen
            mehr = {'start_new_session': True,
                    'preexec_fn': lambda: [signal.signal(s, signal.SIG_DFL) for s in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM)]}
        return subprocess.run(['bash', str(HAERTEN), *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=120, **mehr)

    def bild(self, ohne=('aufrufe.log', 'proc/self')):
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

    def manifest(self):
        """MANIFEST des einzigen Laufs als Liste von Feldern."""
        laeufe = sorted((self.w / 'var/backups/ju-haertung').iterdir())
        assert len(laeufe) == 1, laeufe
        return [z.split('\t') for z in (laeufe[0] / 'MANIFEST').read_text(encoding='utf-8').splitlines()]


AENDERND = re.compile(r'^(systemctl (enable|disable|start|stop|restart|reload|try-restart|daemon-reload)|ufw (allow|delete|default|--force|reload)'
                      r'|nft (-f|delete)|sysctl|apt-get .*(install|update)|systemd-run|curl .*spamhaus)')
UFW_AENDERND = re.compile(r'^ufw (allow|delete|default|--force|reload|reset|deny|limit|insert)')
# was ein Lauf außerhalb der gesicherten Dateien hinterlässt: Sicherungen, Arbeitsdaten des Serverschutzes, Sperre, leeres Protokoll
NEBENBEI = ('aufrufe.log', 'proc/self', 'zustand', 'var/backups', 'var/lib/ju-serverschutz', 'run/ju-haertung.lock', 'var/log/fail2ban.log',
            'etc/fail2ban', 'etc/security', 'etc/sysctl.d', 'etc/apt', 'etc/systemd/journald.conf.d')
LIB = 'var/lib/ju-serverschutz'
PLAN = 'systemd-run --quiet --collect --unit=ju-haertung-ruecknahme --on-active=300 '
STOPP = 'systemctl stop ju-haertung-ruecknahme.timer'


@unittest.skipUnless(LINUX, 'braucht Linux mit bash und flock')
class HaertenTests(unittest.TestCase):
    def setUp(self):
        self.s = Wurzel(self)

    def gut(self, *args, **mehr):
        r = self.s.lauf(*args, **mehr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
        return r.stdout

    def anwenden(self, *args):
        """Anwenden ohne geplante Rücknahme (--ohne-rueckfrage) – für alles, was nicht die Rücknahme selbst prüft."""
        return self.gut('--ohne-rueckfrage', *args)

    def geplante_ruecknahme(self, **mehr):
        """Führt aus, was die Timer-Unit nach Ablauf der Frist ausführen würde: den von systemd-run aufgezeichneten Befehl."""
        zeile = [a for a in self.s.aufrufe() if a.startswith('systemd-run ')][-1]
        self.assertTrue(zeile.startswith(PLAN), zeile)
        befehl, _, args = zeile.partition(' --description=')[2].partition('haerten.sh ')
        self.assertTrue(befehl.endswith(str(HAERTEN.parent) + '/'), zeile)
        self.assertEqual(args.split(), ['--zurueck', '--ohne-rueckfrage'])
        return self.s.lauf(*args.split(), **mehr)

    def test_pruefen_aendert_nichts_und_nennt_die_lage(self):
        vorher = self.s.bild()
        out = self.gut('--pruefen')
        self.assertEqual(self.s.bild(), vorher)
        self.assertEqual([a for a in self.s.aufrufe() if AENDERND.match(a)], [])
        self.assertIn('Befund:   Passwort-Anmeldung für root offen, durch ufw auf eine Quelle begrenzt', out)
        self.assertIn('ufw aktiv, eingehend gesperrt', out)
        self.assertIn('ufw: nichts zu ergänzen – an ufw wird nichts geändert', out)   # auf diesem Server fehlt in ufw nichts
        self.assertNotIn('Änderung: ufw', out)
        self.assertNotIn('41641', out)                                # Tailscale öffnet seinen Port selbst
        self.assertIn('Docker: kein Container', out)
        self.assertNotIn('docker.service abschalten', out)            # ist schon aus
        for erwartet in ('Änderung: Paket fail2ban', 'net.ipv4.conf.all.log_martians (ist 0, soll 1)',
                         'net.ipv4.conf.all.send_redirects (ist 1, soll 0)', 'Änderung: /etc/ssh/sshd_config.d/00-ju-haertung.conf anlegen',
                         'Änderung: /etc/ufw/sysctl.conf: 2 Zeilen auf dieselben Werte stellen',
                         'Rechte von /etc/ju-finanzen/keycloak-admin.env: 644', 'Risiko:', 'nichts wurde geändert',
                         'ssh -o PubkeyAuthentication=no', 'zählen bei fail2ban mit',
                         # die Firewall-Änderung gilt erst auf Probe – das sagt schon der Prüflauf
                         'Ohne „sudo ju-finanzen haerten --bestaetigen“', 'nach 5 Minuten von selbst zurück'):
            self.assertIn(erwartet, out)
        self.assertNotIn('IdentitiesOnly', out)
        self.assertNotIn('kernel.yama.ptrace_scope (', out)           # der strengere vorhandene Wert bleibt
        # mit den riskanten Schaltern: nur die Ablehnung berichten
        out = self.gut('--pruefen', '--ssh-nur-schluessel')
        self.assertIn('ABGELEHNT: SSH nur mit Schlüssel: für das Konto root ist kein SSH-Schlüssel hinterlegt', out)
        self.assertEqual(self.s.bild(), vorher)

    def test_anwenden_wiederholen_zurueck(self):
        vorher = self.s.bild(NEBENBEI)
        out = self.anwenden()
        self.assertIn('Fertig:', out)
        self.assertNotIn('ZWEITES Fenster', out)                      # --ohne-rueckfrage plant keine Rücknahme
        self.assertEqual([a for a in self.s.aufrufe() if a.startswith('systemd-run')], [])
        # 1 Firewall: an ufw fehlt nichts – kein einziger ändernder Aufruf, keine ufw-Datei in der Sicherung
        self.assertEqual([a for a in self.s.aufrufe() if UFW_AENDERND.match(a)], [])
        manifest = self.s.manifest()
        self.assertEqual([z for z in manifest if z[0] == 'ufw' or '/etc/ufw/' in z[1] or z[1] == '/etc/default/ufw'], [])
        self.assertIn(['ufwsysctl', 'net/ipv4/conf/all/log_martians', '0'], manifest)
        # eigene Tabelle geladen; die Sperrliste steht in den Mengen, ohne die eigene Adresse – nie in den Regeln
        self.assertTrue((self.s.z / 'tabelle').exists())
        mengen = self.s.lies(f'{LIB}/ju_mengen.nft')
        self.assertIn('192.0.2.0/30', mengen)
        self.assertNotIn('198.51.100.8/30', mengen)                   # darin läge die eigene Adresse 198.51.100.10
        self.assertEqual(self.s.lies('zustand/mengen'), mengen)       # und so sind sie geladen
        self.assertNotIn('192.0.2.0/30', self.s.lies(f'{LIB}/ju_schutz.nft'))
        self.assertEqual(len(self.s.lies(f'{LIB}/drop4.txt').splitlines()), 150)
        self.assertEqual(self.s.lies('zustand/enabled-ju-serverschutz.service').strip(), 'enabled')
        self.assertEqual(self.s.lies('zustand/enabled-ju-serverschutz-liste.timer').strip(), 'enabled')
        self.assertFalse([a for a in self.s.aufrufe() if a.startswith('curl') and '--max-redirs' in a])
        # 3 fail2ban: Tailscale und die Konsole des Anbieters werden nie gesperrt
        jail = self.s.lies('etc/fail2ban/jail.d/ju-haertung.local')
        self.assertIn('ignoreip = 127.0.0.0/8 ::1 169.254.0.0/16 100.64.0.0/10 fd7a:115c:a1e0::/48\n', jail)
        self.assertIn('[recidive]', jail)
        self.assertIn('apt-get -o DPkg::Lock::Timeout=120 install -y -qq fail2ban', self.s.aufrufe())
        # 4 Kern: geändert wird nur, was abweicht; rp_filter bleibt 2; ufw setzt denselben Wert
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.log_martians'), '1')
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.rp_filter'), '2')
        self.assertEqual(self.s.kernwert('net.ipv4.ip_forward'), '0')
        # der Kern schaltet accept_redirects wieder ein, sobald die Weiterleitung ausgeht – der Wert muss danach erneut gesetzt sein
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.accept_redirects'), '0')
        self.assertEqual(self.s.kernwert('kernel.yama.ptrace_scope'), '2')
        self.assertIn('net/ipv4/conf/all/log_martians=1', self.s.lies('etc/ufw/sysctl.conf'))
        kern = self.s.lies('etc/sysctl.d/80-ju-haertung.conf')
        self.assertLess(kern.index('net.ipv4.ip_forward = 0'), kern.index('net.ipv4.tcp_syncookies = 1'))
        self.assertLess(kern.index('net.ipv4.ip_forward = 0'), kern.index('net.ipv4.conf.all.accept_redirects = 0'))
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
        status = self.s.w / LIB / 'status.json'
        self.assertEqual(stat.S_IMODE(status.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(status.parent.stat().st_mode), 0o755)
        # Sicherung: Ordner nur für root, Protokoll, Manifest; die Sperrdatei liegt in /run (nur root), nicht in /run/lock
        laeufe = sorted((self.s.w / 'var/backups/ju-haertung').iterdir())
        self.assertEqual(stat.S_IMODE(laeufe[0].stat().st_mode), 0o700)
        self.assertIn('Ändere:', (laeufe[0] / 'protokoll.log').read_text(encoding='utf-8'))
        self.assertTrue((self.s.w / 'run/ju-haertung.lock').exists())
        self.assertFalse((self.s.w / 'run/lock').exists())

        # zweiter Lauf: nichts zu tun, keine neue Sicherung, kein ändernder Aufruf
        fluechtig = ('aufrufe.log', 'proc/self', f'{LIB}/status.json', f'{LIB}/zaehler.json')
        zwischen, n = self.s.bild(fluechtig), len(self.s.aufrufe())
        out = self.anwenden()
        self.assertIn('Alles war bereits umgesetzt', out)
        self.assertEqual(self.s.bild(fluechtig), zwischen)
        self.assertEqual([a for a in self.s.aufrufe()[n:] if AENDERND.match(a)], [])
        self.assertIn('Änderung: keine', self.gut('--pruefen'))
        self.assertIn('0 Änderungen stünden an', self.gut('--pruefen'))
        self.assertIn('Alles war bereits umgesetzt', self.gut())       # ohne Änderung an der Firewall: nichts zu bestätigen
        self.assertEqual([a for a in self.s.aufrufe() if a.startswith('systemd-run')], [])

        # zurück: jede geänderte Datei, jedes Recht, jeder Kern-Wert und jeder Dienst wie vor dem Lauf
        zeigen = self.gut('--zurueck', '--pruefen')
        self.assertIn('würde zurücknehmen: datei /etc/ssh/sshd_config.d/00-ju-haertung.conf', zeigen)
        self.assertNotIn('summe', zeigen)
        out = self.gut('--zurueck')
        self.assertEqual(self.s.bild(NEBENBEI), vorher)
        # auch die Arbeitsdaten sind weg: ein alter Statusbericht meldete dem Programm sonst weiter „ok“
        for rest in ('etc/fail2ban/jail.d/ju-haertung.local', 'etc/sysctl.d/80-ju-haertung.conf', 'etc/apt/apt.conf.d/52ju-haertung',
                     'etc/systemd/journald.conf.d/60-ju-haertung.conf', f'{LIB}/ju_schutz.nft', f'{LIB}/ju_mengen.nft',
                     f'{LIB}/status.json', f'{LIB}/drop4.txt', f'{LIB}/drop6.txt', f'{LIB}/zaehler.json'):
            self.assertFalse((self.s.w / rest).exists(), rest)
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertEqual([a for a in self.s.aufrufe() if UFW_AENDERND.match(a)], [])
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.log_martians'), '0')
        self.assertEqual(self.s.kernwert('net.ipv4.ip_forward'), '1')
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.accept_redirects'), '0')
        self.assertEqual(self.s.kernwert('kernel.core_pattern'), '|/usr/share/apport/apport -p%p -s%s')
        self.assertEqual(self.s.lies('zustand/enabled-fail2ban.service').strip(), 'disabled')
        self.assertEqual(self.s.lies('zustand/active-ju-serverschutz-liste.timer').strip(), 'inactive')
        self.assertNotIn('nft delete table inet filter', '\n'.join(self.s.aufrufe()))
        r = self.s.lauf('--zurueck')
        self.assertEqual(r.returncode, 1)
        self.assertIn('keinen Lauf, der sich zurücknehmen lässt', r.stderr)

    # ---------------------------------------------------------------- geplante Rücknahme (hängt nicht an der Sitzung)

    def test_ruecknahme_wird_vorher_geplant_und_nur_aus_anderer_sitzung_abgesagt(self):
        out = self.gut()
        for erwartet in ('WICHTIG – noch ein Schritt', 'In 5 Minuten (um ', 'werden die Änderungen von selbst zurückgenommen',
                         'Öffne jetzt ein ZWEITES Fenster, melde dich dort neu an und führe aus:', 'sudo ju-finanzen haerten --bestaetigen'):
            self.assertIn(erwartet, out)
        self.assertNotIn('Eingabetaste', out)
        aufrufe = self.s.aufrufe()
        geplant = [i for i, a in enumerate(aufrufe) if a.startswith('systemd-run')]
        self.assertEqual(len(geplant), 1)
        self.assertTrue(aufrufe[geplant[0]].startswith(PLAN) and aufrufe[geplant[0]].endswith('haerten.sh --zurueck --ohne-rueckfrage'))
        # geplant, bevor irgendetwas an der Firewall geändert ist
        self.assertLess(geplant[0], min(i for i, a in enumerate(aufrufe) if a.startswith(('systemctl daemon-reload', 'systemctl restart'))))
        # Prüflauf und Statusbericht zeigen die ausstehende Rücknahme mit Restzeit; ein neuer Lauf beginnt nicht
        for zeigen in (self.gut('--pruefen'), self.gut('status')):
            self.assertIn('ist noch nicht bestätigt', zeigen)
            self.assertRegex(zeigen, r'In 5 Minuten \(um \d\d:\d\d:\d\d Uhr\) werden die Änderungen von selbst zurückgenommen')
        r = self.s.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn('ist noch nicht bestätigt', r.stderr)
        # dieselbe Anmeldung beweist nichts: abgelehnt, die Rücknahme bleibt geplant
        r = self.s.lauf('--bestaetigen', sitzung='7')
        self.assertEqual(r.returncode, 1)
        self.assertIn('derselben Anmeldung', r.stderr)
        self.assertNotIn(STOPP, self.s.aufrufe())
        self.assertEqual(self.s.lies('zustand/active-ju-haertung-ruecknahme.timer').strip(), 'active')
        r = self.s.lauf('--bestaetigen', sitzung='4294967295')         # Anmeldung nicht erkennbar (keine Audit-Sitzung, kein Terminal)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(STOPP, self.s.aufrufe())
        # eine andere Anmeldung: angenommen, die Timer-Unit wird gestoppt, alles bleibt
        self.assertIn('Bestätigt: Die Änderungen', self.gut('--bestaetigen', sitzung='8'))
        self.assertIn(STOPP, self.s.aufrufe())
        self.assertTrue((self.s.z / 'tabelle').exists())
        self.assertNotIn('noch nicht bestätigt', self.gut('--pruefen'))
        self.assertIn('nichts zu bestätigen', self.gut('--bestaetigen', sitzung='8'))
        # liefe die Unit jetzt doch noch (Stoppen verpasst), nähme sie nichts zurück: sie gilt nur einem unbestätigten Lauf
        r = self.geplante_ruecknahme()
        self.assertEqual(r.returncode, 0)
        self.assertIn('Keine Rücknahme ausstehend', r.stdout)
        self.assertTrue((self.s.z / 'tabelle').exists())
        self.assertTrue((self.s.w / 'etc/sysctl.d/80-ju-haertung.conf').exists())

    def test_ohne_bestaetigung_kommt_nach_der_frist_der_alte_stand(self):
        vorher = self.s.bild(NEBENBEI)
        self.gut()
        self.assertTrue((self.s.z / 'tabelle').exists())
        r = self.geplante_ruecknahme()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.s.bild(NEBENBEI), vorher)
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertFalse((self.s.w / LIB / 'status.json').exists())
        self.assertIn('nichts zu bestätigen', self.gut('--bestaetigen', sitzung='8'))
        self.assertIn('der Lauf wurde nicht bestätigt', next((self.s.w / 'var/backups/ju-haertung').iterdir()).joinpath('protokoll.log').read_text(encoding='utf-8'))

    def test_signal_nach_dem_anwenden_laesst_die_ruecknahme_geplant(self):
        # HUP: Fenster zu, während eine spätere Maßnahme läuft – die Firewall ist da schon geändert.
        # INT: Strg-C mitten in der Firewall-Maßnahme, die Tabelle ist gerade geladen.
        for name, bei, argumente, tabelle in (('HUP', 'fail2ban-client', '-t', True), ('INT', 'nft', 'list table inet ju_schutz', False)):
            with self.subTest(name):
                self.s = Wurzel(self)
                vorher = self.s.bild(NEBENBEI)
                self.s.zustand(f'signal-{bei}', f'{name}\n{argumente}\n')
                r = self.s.lauf(gruppe=True)
                self.assertEqual(r.returncode, 130, r.stdout[-2000:] + r.stderr)
                self.assertNotIn('ZWEITES Fenster', r.stdout)                         # der Lauf kam nicht bis zum Ende
                self.assertEqual(len([a for a in self.s.aufrufe() if a.startswith(PLAN)]), 1)
                self.assertNotIn(STOPP, self.s.aufrufe())                             # die Rücknahme bleibt geplant
                self.assertEqual((self.s.z / 'tabelle').exists(), tabelle)            # nur die begonnene Maßnahme ist zurückgenommen
                self.assertFalse((self.s.w / 'etc/fail2ban/jail.d/ju-haertung.local').exists())
                self.assertFalse((self.s.w / 'etc/sysctl.d/80-ju-haertung.conf').exists())   # spätere Maßnahmen begannen nicht
                protokoll = next((self.s.w / 'var/backups/ju-haertung').iterdir()).joinpath('protokoll.log').read_text(encoding='utf-8')
                self.assertIn(f'ABGEBROCHEN (Signal {name})', protokoll)
                self.assertIn('Die geplante Rücknahme bleibt bestehen', protokoll)
                self.assertIn('ist noch nicht bestätigt', self.gut('--pruefen'))
                # niemand bestätigt: nach der Frist gilt wieder der Stand von vorher
                self.assertEqual(self.geplante_ruecknahme().returncode, 0)
                self.assertEqual(self.s.bild(NEBENBEI), vorher)
                self.assertFalse((self.s.z / 'tabelle').exists())

    def test_ohne_planbare_ruecknahme_wird_die_firewall_nicht_angewendet(self):
        (self.s.w / 'stub/systemd-run').unlink()
        r = self.s.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn('NICHT ANGEWENDET', r.stdout)
        self.assertRegex(r.stdout, r'- Firewall \(ufw und Schutztabelle inet ju_schutz\) \(nicht angewendet, weil sich die automatische Rücknahme nicht planen lässt')
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertFalse((self.s.w / LIB / 'ju_schutz.nft').exists())
        self.assertFalse((self.s.w / 'etc/systemd/system/ju-serverschutz.service').exists())
        self.assertNotIn('ZWEITES Fenster', r.stdout)
        for rest in ('etc/sysctl.d/80-ju-haertung.conf', 'etc/fail2ban/jail.d/ju-haertung.local', 'etc/ssh/sshd_config.d/00-ju-haertung.conf'):
            self.assertTrue((self.s.w / rest).exists(), rest)             # der Rest gilt
        # der SSH-Sonderschalter braucht die Rücknahme ebenso
        self.s.datei('root/.ssh/authorized_keys', 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBeispielBeispielBeispielBeispielBeispielBeispi beispiel\n', 0o600)
        self.s.zustand('journal', 'Accepted publickey for root from 203.0.113.5 port 50001 ssh2: ED25519 SHA256:Beispiel\n')
        r = self.s.lauf('--ssh-nur-schluessel')
        self.assertEqual(r.returncode, 1)
        self.assertIn('- SSH-Grundhärtung (nicht angewendet, weil', r.stdout)
        self.assertNotIn('PasswordAuthentication no', self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf'))
        self.assertNotIn('SSH_NUR_SCHLUESSEL=ja', self.s.lies('etc/ju-finanzen/haertung.conf'))

    def test_sperrdatei_haelt_die_geplante_ruecknahme_nicht_auf(self):
        self.gut()
        with open(self.s.w / 'run/ju-haertung.lock', 'w', encoding='utf-8') as sperre:   # ein abgestürzter Lauf, dessen Kind die Sperre noch hält
            fcntl.flock(sperre, fcntl.LOCK_EX)
            r = self.s.lauf('--zurueck')
            self.assertEqual(r.returncode, 1)
            self.assertIn('läuft gerade schon', r.stderr)
            self.assertTrue((self.s.z / 'tabelle').exists())
            r = self.geplante_ruecknahme(frist='1')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('Die Sperre wurde nicht frei – die geplante Rücknahme läuft trotzdem', r.stdout)
            self.assertFalse((self.s.z / 'tabelle').exists())

    def test_ssh_nur_schluessel_gilt_erst_nach_bestaetigung(self):
        self.s.datei('root/.ssh/authorized_keys', 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBeispielBeispielBeispielBeispielBeispielBeispi beispiel\n', 0o600)
        self.s.zustand('journal', 'Accepted publickey for root from 203.0.113.5 port 50001 ssh2: ED25519 SHA256:Beispiel\n')
        self.anwenden()
        n = len(self.s.aufrufe())
        out = self.gut('--ssh-nur-schluessel')
        self.assertIn('ZWEITES Fenster', out)
        aufrufe = self.s.aufrufe()[n:]
        self.assertLess(next(i for i, a in enumerate(aufrufe) if a.startswith(PLAN)), aufrufe.index('systemctl reload ssh.service'))
        self.assertIn('PasswordAuthentication no', self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf'))
        self.assertEqual(self.geplante_ruecknahme().returncode, 0)
        self.assertNotIn('PasswordAuthentication no', self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf'))
        self.assertIn('SSH_NUR_SCHLUESSEL=nein', self.s.lies('etc/ju-finanzen/haertung.conf'))

    # ---------------------------------------------------------------- einzelne Maßnahmen

    def test_fehler_in_einer_massnahme_nimmt_nur_sie_zurueck(self):
        self.s.zustand('scheitert-sshd', '-t')
        r = self.s.lauf('--ohne-rueckfrage')
        self.assertEqual(r.returncode, 1)
        self.assertIn('sshd -t meldet einen Fehler', r.stderr)
        self.assertIn('NICHT ANGEWENDET', r.stdout)
        self.assertIn('- SSH-Grundhärtung (sshd -t meldet einen Fehler in den SSH-Einstellungen – nichts neu geladen.)', r.stdout)
        self.assertFalse((self.s.w / 'etc/ssh/sshd_config.d/00-ju-haertung.conf').exists())
        self.assertNotIn('systemctl reload ssh.service', self.s.aufrufe())
        self.assertTrue((self.s.w / 'etc/sysctl.d/80-ju-haertung.conf').exists())        # frühere Maßnahmen bleiben
        self.assertTrue((self.s.w / 'etc/systemd/journald.conf.d/60-ju-haertung.conf').exists())   # spätere laufen trotzdem
        self.assertTrue((self.s.w / LIB / 'status.json').exists())
        (self.s.z / 'scheitert-sshd').unlink()
        self.assertIn('Fertig: 2 Änderungen', self.anwenden())                          # danach einfach erneut: nur SSH fehlt noch

    def test_paket_laesst_sich_nicht_installieren(self):
        self.s.zustand('scheitert-apt-get', '-o DPkg::Lock::Timeout=120 install -y -qq fail2ban')
        r = self.s.lauf('--ohne-rueckfrage')
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stdout, r'- fail2ban \(SSH\) \(Das Paket fail2ban ließ sich nicht installieren\. Was tun: einige Minuten warten')
        self.assertIn('sudo apt-get update && sudo apt-get install fail2ban', r.stdout)
        self.assertFalse((self.s.w / 'etc/fail2ban/jail.d/ju-haertung.local').exists())
        self.assertTrue((self.s.z / 'tabelle').exists())
        self.assertTrue((self.s.w / 'etc/ssh/sshd_config.d/00-ju-haertung.conf').exists())

    def test_firewall_wird_zurueckgenommen_wenn_danach_etwas_nicht_mehr_erreichbar_ist(self):
        self.s.zustand('firewall-bricht', '')
        r = self.s.lauf('--ohne-rueckfrage')
        self.assertEqual(r.returncode, 1)
        self.assertIn('Nach der Firewall-Änderung nicht mehr erreichbar: Programm', r.stderr)
        self.assertIn('- Firewall (ufw und Schutztabelle inet ju_schutz) (Nach der Firewall-Änderung nicht mehr erreichbar: Programm)', r.stdout)
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertFalse((self.s.w / LIB / 'ju_schutz.nft').exists())
        self.assertFalse((self.s.w / 'etc/systemd/system/ju-serverschutz.service').exists())
        self.assertEqual([a for a in self.s.aufrufe() if UFW_AENDERND.match(a)], [])

    def test_scheitert_allein_die_firewall_bleibt_keine_ruecknahme_geplant(self):
        # nur die Firewall-Maßnahme ändert etwas und wird zurückgenommen: es bleibt nichts, was eine Rücknahme zurücknähme
        self.anwenden()
        self.s.datei('etc/ju-finanzen/haertung.conf', self.s.lies('etc/ju-finanzen/haertung.conf').replace('SSH_NEU_JE_MINUTE=12', 'SSH_NEU_JE_MINUTE=20'))
        regeln = self.s.lies(f'{LIB}/ju_schutz.nft')
        self.s.zustand('firewall-bricht', '')
        r = self.s.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len([a for a in self.s.aufrufe() if a.startswith(PLAN)]), 1)
        self.assertIn(STOPP, self.s.aufrufe())
        self.assertNotIn('ZWEITES Fenster', r.stdout)
        self.assertEqual(self.s.lies(f'{LIB}/ju_schutz.nft'), regeln)
        self.assertTrue((self.s.z / 'tabelle').exists())               # die bisherige Tabelle ist wieder geladen
        self.assertEqual(len(list((self.s.w / 'var/backups/ju-haertung').iterdir())), 1)
        self.assertNotIn('noch nicht bestätigt', self.gut('--pruefen'))

    def test_nftables_dienst_wird_nie_gestoppt_oder_gestartet(self):
        # nftables.service: ExecStop ist „flush ruleset“ – das löschte die Regeln von ufw und Tailscale
        self.s.zustand('pakete', 'ufw\nunattended-upgrades\n')
        self.anwenden()
        self.assertIn('apt-get -o DPkg::Lock::Timeout=120 install -y -qq nftables', self.s.aufrufe())
        self.assertFalse([z for z in self.s.manifest() if z[0] == 'dienst' and z[1] == 'nftables.service'])
        self.gut('--zurueck')
        self.assertEqual([a for a in self.s.aufrufe() if re.match(r'systemctl (stop|start|restart|enable|disable)\b.*nftables', a)], [])

    def test_ufw_aus_ssh_bleibt_offen_und_zurueck_nimmt_nur_das_eigene(self):
        # auch eine Regel „nur von einer Adresse“ zählt nicht: ohne Firewall war SSH bisher von überall erreichbar
        self.s.zustand('ufw-aus', '')
        self.s.zustand('ufw-regeln', 'ufw allow from 203.0.113.5 to any port 22 proto tcp\n')
        self.s.datei('etc/ufw/user.rules', 'ufw allow from 203.0.113.5 to any port 22 proto tcp\n')
        self.s.datei('etc/default/ufw', 'IPV6=yes\nDEFAULT_INPUT_POLICY="ACCEPT"\n')
        out = self.gut('--pruefen')
        self.assertIn('ufw AUS', out)
        self.assertIn('SSH-Port 22 offen lassen wie vorgefunden', out)
        self.anwenden()
        self.assertEqual(self.s.lies('zustand/ufw-regeln').splitlines()[1:], ['ufw allow 22/tcp', 'ufw allow in on tailscale0'])
        aufrufe = self.s.aufrufe()
        self.assertLess(aufrufe.index('ufw allow 22/tcp'), aufrufe.index('ufw --force enable'))   # erst öffnen, dann einschalten
        self.assertFalse((self.s.z / 'ufw-aus').exists())
        self.assertEqual([z[1] for z in self.s.manifest() if z[0] == 'ufw'],
                         ['delete allow 22/tcp', 'delete allow in on tailscale0', 'default allow incoming', '--force disable'])
        self.assertFalse([z for z in self.s.manifest() if z[0] == 'datei' and 'ufw' in z[1]])
        # später ergänzt jemand eine Regel, eine Zeile in /etc/ufw/sysctl.conf und einen Schalter – das alles übersteht --zurueck
        for datei in ('zustand/ufw-regeln', 'etc/ufw/user.rules'):
            with open(self.s.w / datei, 'a', encoding='utf-8') as f:
                f.write('ufw allow 443/tcp\n')
        with open(self.s.w / 'etc/ufw/sysctl.conf', 'a', encoding='utf-8') as f:
            f.write('net/ipv4/tcp_sack=0\n')
        self.s.datei('etc/ju-finanzen/haertung.conf', self.s.lies('etc/ju-finanzen/haertung.conf').replace('NIE_SPERREN=', 'NIE_SPERREN=203.0.113.7'))
        n = len(self.s.aufrufe())
        out = self.gut('--zurueck')
        self.assertEqual(self.s.lies('zustand/ufw-regeln').splitlines(), ['ufw allow from 203.0.113.5 to any port 22 proto tcp', 'ufw allow 443/tcp'])
        self.assertEqual(self.s.lies('etc/ufw/user.rules'), self.s.lies('zustand/ufw-regeln'))
        self.assertEqual([a for a in self.s.aufrufe()[n:] if a.startswith('ufw')],
                         ['ufw --force disable', 'ufw default allow incoming', 'ufw delete allow in on tailscale0', 'ufw delete allow 22/tcp'])
        self.assertTrue((self.s.z / 'ufw-aus').exists())
        self.assertIn('DEFAULT_INPUT_POLICY="ACCEPT"', self.s.lies('etc/default/ufw'))
        self.assertEqual(self.s.lies('etc/ufw/sysctl.conf').splitlines()[2:],
                         ['net/ipv4/conf/all/accept_redirects=0', 'net/ipv4/conf/all/log_martians=0', 'net/ipv4/conf/default/log_martians=0', 'net/ipv4/tcp_sack=0'])
        # eine seit dem Lauf von Hand geänderte Datei wird nicht überschrieben oder gelöscht, sondern gemeldet
        self.assertIn('HINWEIS:  /etc/ju-finanzen/haertung.conf wurde seit dem Lauf von Hand geändert – bleibt, wie es ist', out)
        self.assertIn('NIE_SPERREN=203.0.113.7', self.s.lies('etc/ju-finanzen/haertung.conf'))
        self.assertFalse((self.s.w / 'etc/ssh/sshd_config.d/00-ju-haertung.conf').exists())

    def test_tailscale_regel_mit_kommentar_und_schalter_fuer_den_port(self):
        self.s.zustand('ufw-regeln', "ufw allow in on tailscale0 comment 'Tailnetz'\nufw allow from 203.0.113.5 to any port 22 proto tcp\n")
        out = self.gut('--pruefen')
        self.assertNotIn('alles auf tailscale0 erlauben', out)        # die Regel gibt es – auch mit Kommentar
        self.assertIn('ufw: nichts zu ergänzen', out)
        # TAILSCALE_PORT=ja (nur für --netfilter-mode=off/nodivert): der Port wird ergänzt und mit --zurueck wieder entfernt
        vorlage = (ROOT / 'server' / 'haertung' / 'haertung.conf').read_text(encoding='utf-8')
        self.s.datei('etc/ju-finanzen/haertung.conf', vorlage.replace('TAILSCALE_PORT=nein', 'TAILSCALE_PORT=ja'))
        self.anwenden()
        self.assertEqual(self.s.lies('zustand/ufw-regeln').splitlines()[-1], 'ufw allow 41641/udp')
        self.assertEqual([a for a in self.s.aufrufe() if UFW_AENDERND.match(a)], ['ufw allow 41641/udp'])
        self.gut('--zurueck')
        self.assertNotIn('41641', self.s.lies('zustand/ufw-regeln'))
        self.assertIn("comment 'Tailnetz'", self.s.lies('zustand/ufw-regeln'))

    def test_weiterleitung_bleibt_wenn_eine_andere_datei_sie_einschaltet(self):
        self.s.datei('etc/sysctl.d/99-tailscale.conf', 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n')
        out = self.anwenden()
        self.assertIn('net.ipv4.ip_forward=1 bleibt: /etc/sysctl.d/99-tailscale.conf schaltet die Weiterleitung ausdrücklich ein', out)
        self.assertEqual(self.s.kernwert('net.ipv4.ip_forward'), '1')
        self.assertNotIn('ip_forward', self.s.lies('etc/sysctl.d/80-ju-haertung.conf'))
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.accept_redirects'), '0')
        self.assertEqual(self.s.kernwert('net.ipv4.conf.all.log_martians'), '1')

    def test_sshd_braucht_run_sshd(self):
        # ssh.service lief seit dem Start nie (Socket-Aktivierung): /run/sshd fehlt, sshd -t und -T scheitern daran
        (self.s.w / 'run/sshd').rmdir()
        vorher = self.s.bild()
        out = self.gut('--pruefen')
        self.assertEqual(self.s.bild(), vorher)                       # der Prüflauf legt auch diesen Ordner nicht an
        self.assertIn('/run/sshd fehlt', out)
        out = self.anwenden()
        self.assertTrue((self.s.w / 'run/sshd').is_dir())
        self.assertIn('Befund:   Passwort-Anmeldung für root offen', out)
        self.assertIn('MaxAuthTries 3', self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf'))
        self.assertIn('systemctl reload ssh.service', self.s.aufrufe())
        (self.s.w / 'run/sshd').rmdir()                                # auch der Statusbericht liest die geltenden SSH-Werte
        self.assertNotIn('Einstellung nicht lesbar', self.gut('status'))
        (self.s.w / 'run/sshd').rmdir()                                # und das Zurücknehmen prüft mit sshd -t
        self.gut('--zurueck')
        self.assertFalse((self.s.w / 'etc/ssh/sshd_config.d/00-ju-haertung.conf').exists())

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0 and shutil.which('unshare'), 'braucht root und unshare')
    def test_testschalter_gelten_nicht_fuer_die_installierte_version(self):
        # eigener Mount-Namensraum: dort liegt das Skript unter /opt/ju-finanzen/… – am echten /opt ändert sich nichts
        vorbereiten = 'mount -t tmpfs none /opt && mkdir -p /opt/ju-finanzen/current && cp -r "$0" /opt/ju-finanzen/current/server && '
        if subprocess.run(['unshare', '-m', 'sh', '-c', vorbereiten + 'true', str(ROOT / 'server')], capture_output=True).returncode != 0:
            self.skipTest('Mount-Namensraum hier nicht möglich')
        sauber = {k: v for k, v in os.environ.items() if k not in ('WURZEL', 'JU_PYTHON', 'JU_FRIST', 'JU_PAUSE')}
        for name, wert in (('WURZEL', str(self.s.w)), ('JU_PYTHON', sys.executable), ('JU_FRIST', '1'), ('JU_PAUSE', '0')):
            with self.subTest(name):
                r = subprocess.run(['unshare', '-m', 'sh', '-c', vorbereiten + 'bash /opt/ju-finanzen/current/server/haerten.sh --pruefen',
                                    str(ROOT / 'server')], env={**sauber, name: wert}, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 1, r.stdout[-500:])
                self.assertIn('sind Test-Schalter und für die installierte Version gesperrt', r.stderr)
                self.assertEqual(r.stdout, '')
        r = subprocess.run(['unshare', '-m', 'sh', '-c', vorbereiten + 'bash /opt/ju-finanzen/current/server/haerten.sh --help',
                            str(ROOT / 'server')], env=sauber, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ssh_nur_schluessel_nur_mit_nachweis(self):
        vorher = self.s.bild()
        r = self.s.lauf('--ssh-nur-schluessel')
        self.assertEqual(r.returncode, 1)
        self.assertIn('Abgelehnt – nichts geändert', r.stderr)
        self.assertEqual(self.s.bild(('aufrufe.log', 'proc/self', 'run/ju-haertung.lock')), vorher)
        # Schlüssel hinterlegt, aber noch nie damit angemeldet: weiter abgelehnt
        self.s.datei('root/.ssh/authorized_keys', 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBeispielBeispielBeispielBeispielBeispielBeispi beispiel\n', 0o600)
        r = self.s.lauf('--ssh-nur-schluessel')
        self.assertEqual(r.returncode, 1)
        self.assertIn('keine Anmeldung damit gelungen', r.stdout)
        self.s.zustand('journal', 'Accepted password for root from 203.0.113.5 port 50000 ssh2\n'
                                  'Accepted publickey for root from 203.0.113.5 port 50001 ssh2: ED25519 SHA256:Beispiel\n')
        self.anwenden()                                                # Grundhärtung zuerst, der Schalter danach als eigener Lauf
        out = self.anwenden('--ssh-nur-schluessel')
        self.assertIn('WARNUNG: Ohne Passwort-Anmeldung funktioniert das Browser-Terminal des Anbieters möglicherweise nicht mehr', out)
        self.assertIn('PasswordAuthentication no', self.s.lies('etc/ssh/sshd_config.d/00-ju-haertung.conf'))
        self.assertIn('SSH_NUR_SCHLUESSEL=ja', self.s.lies('etc/ju-finanzen/haertung.conf'))
        self.assertIn('Alles war bereits umgesetzt', self.anwenden())  # ein späterer Lauf ohne Schalter schaltet es nicht zurück
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
        self.anwenden('--ssh-nur-tailscale')
        regelwerk = self.s.lies(f'{LIB}/ju_schutz.nft')
        self.assertIn('ct state new counter name ssh_fremd drop', regelwerk)
        self.assertLess(regelwerk.index('169.254.0.0/16'), regelwerk.index('ssh_fremd drop'))   # Konsole des Anbieters bleibt
        self.assertIn('SSH_NUR_TAILSCALE=ja', self.s.lies('etc/ju-finanzen/haertung.conf'))
        self.assertIn('Alles war bereits umgesetzt', self.anwenden())

    def test_zeitgeber_aendert_nie_die_regeln(self):
        # Schalter von Hand auf ja, ohne Nachweis: der Handlauf lehnt ab – und auch der nächtliche Zeitgeber lädt ihn nicht
        self.anwenden()
        regeln = self.s.lies(f'{LIB}/ju_schutz.nft')
        self.s.datei('etc/ju-finanzen/haertung.conf', self.s.lies('etc/ju-finanzen/haertung.conf').replace('SSH_NUR_TAILSCALE=nein', 'SSH_NUR_TAILSCALE=ja'))
        self.s.zustand('tailscale-status', '{"BackendState": "Stopped"}')
        r = self.s.lauf()
        self.assertEqual(r.returncode, 1)
        self.assertIn('Abgelehnt – nichts geändert', r.stderr)
        self.s.zustand('drop.txt', drop4(120).decode())
        n = len(self.s.aufrufe())
        self.assertEqual(self.s.lauf('sperrliste').returncode, 0)
        self.assertEqual(self.s.lies(f'{LIB}/ju_schutz.nft'), regeln)
        self.assertNotIn('ssh_fremd drop', self.s.lies(f'{LIB}/ju_schutz.nft'))
        self.assertEqual(self.s.lies('zustand/mengen').count('/30,'), 119)             # die Liste selbst ist ausgetauscht (ohne die eigene Adresse)
        neu = self.s.aufrufe()[n:]
        self.assertFalse([a for a in neu if a.startswith('systemctl') and 'ju-serverschutz.service' in a and 'is-' not in a], neu)
        self.assertEqual([a.split()[:2] for a in neu if a.startswith('nft -')], [['nft', '-c'], ['nft', '-f']])   # erst prüfen, dann ein Schritt
        # der Statusbericht richtet sich nach den geladenen Regeln, nicht nach dem Schalter
        self.assertIn('SSH ist durch die Firewall auf einzelne Quellen begrenzt', self.gut('status'))

    def test_schalter_nein_entfernt_die_eigenen_dateien(self):
        self.anwenden()
        conf = self.s.lies('etc/ju-finanzen/haertung.conf')
        self.s.datei('etc/ju-finanzen/haertung.conf', conf.replace('FIREWALL=ja', 'FIREWALL=nein').replace('SSH=ja', 'SSH=nein')
                     .replace('STATUS=ja', 'STATUS=nein').replace('SPERRLISTE=ja', 'SPERRLISTE=nein'))
        regeln = self.s.lies('zustand/ufw-regeln')
        self.anwenden()
        for weg in (f'{LIB}/ju_schutz.nft', 'etc/systemd/system/ju-serverschutz.service', 'etc/systemd/system/ju-serverschutz-liste.timer',
                    'etc/ssh/sshd_config.d/00-ju-haertung.conf', f'{LIB}/status.json'):
            self.assertFalse((self.s.w / weg).exists(), weg)
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertNotIn('add element', self.s.lies(f'{LIB}/ju_mengen.nft'))             # beim nächsten Start bleibt die Liste leer
        self.assertEqual(self.s.lies('zustand/ufw-regeln'), regeln)   # ufw bleibt, wie es ist
        self.assertIn('Alles war bereits umgesetzt', self.anwenden())
        self.s.datei('etc/ju-finanzen/haertung.conf', conf.replace('KERN=ja', 'KERN=vielleicht'))
        r = self.s.lauf('--pruefen')
        self.assertEqual(r.returncode, 1)
        self.assertIn('KERN muss ja oder nein sein', r.stderr)

    def test_docker_mit_containern_bleibt(self):
        self.s.einheit('docker.service', 'enabled', 'active')
        (self.s.w / 'var/lib/docker/containers/beispiel').mkdir(parents=True)
        out = self.anwenden()
        self.assertIn('Docker: 1 Container vorhanden – bleibt an', out)
        self.assertEqual(self.s.lies('zustand/active-docker.service').strip(), 'active')
        self.assertEqual(self.s.kernwert('net.ipv4.ip_forward'), '1')   # Weiterleitung bleibt für die Container
        shutil.rmtree(self.s.w / 'var/lib/docker/containers/beispiel')
        self.anwenden()
        self.assertEqual(self.s.lies('zustand/active-docker.service').strip(), 'inactive')

    def test_sperrliste_taeglich_schlechte_liste_laesst_die_gute(self):
        self.anwenden()
        gut = self.s.lies(f'{LIB}/drop4.txt')
        regeln, mengen = self.s.lies(f'{LIB}/ju_schutz.nft'), self.s.lies(f'{LIB}/ju_mengen.nft')
        for name, inhalt in (('privat', drop4().decode() + '10.0.0.0/8 ; SBL1\n'), ('klein', drop4(20).decode()), ('kaputt', 'kaputt\n')):
            with self.subTest(name):
                self.s.zustand('drop.txt', inhalt)
                self.assertEqual(self.s.lauf('sperrliste').returncode, 1)
                self.assertEqual(self.s.lies(f'{LIB}/drop4.txt'), gut)
                self.assertEqual(self.s.lies(f'{LIB}/ju_mengen.nft'), mengen)
                self.assertEqual(self.s.lies('zustand/mengen'), mengen)
                self.assertTrue((self.s.w / LIB / 'sperrliste.fehler').exists())
        self.s.zustand('drop.txt', drop4(120).decode())
        self.assertEqual(self.s.lauf('sperrliste').returncode, 0)
        self.assertEqual(len(self.s.lies(f'{LIB}/drop4.txt').splitlines()), 120)
        self.assertFalse((self.s.w / LIB / 'sperrliste.fehler').exists())
        self.assertNotIn('198.51.100.8/30', self.s.lies(f'{LIB}/ju_mengen.nft'))
        self.assertEqual(self.s.lies('zustand/mengen'), self.s.lies(f'{LIB}/ju_mengen.nft'))
        self.assertEqual(self.s.lies(f'{LIB}/ju_schutz.nft'), regeln)                 # die Regeln fasst der Zeitgeber nie an
        self.assertTrue(self.s.aufrufe()[-1].startswith('nft -f '))
        # lehnt nft die neuen Mengen ab, bleiben die bisherigen geladen und für den nächsten Start gemerkt
        mengen = self.s.lies(f'{LIB}/ju_mengen.nft')
        self.s.zustand('drop.txt', drop4(125).decode())
        (self.s.w / 'stub/nft').unlink()
        self.s.datei('stub/nft', '#!/bin/sh\ncase "$*" in "list table inet ju_schutz") exit 0 ;; *) exit 1 ;; esac\n', 0o755)
        self.assertEqual(self.s.lauf('sperrliste').returncode, 1)
        self.assertEqual(self.s.lies(f'{LIB}/ju_mengen.nft'), mengen)
        (self.s.w / 'stub/nft').unlink()
        (self.s.w / 'stub/nft').symlink_to(ROOT / 'tests' / 'haertung_stub.sh')
        # Notaus bleibt Notaus: bei gestoppter Tabelle lädt der Zeitgeber nichts – die Liste gilt ab dem nächsten Start
        (self.s.z / 'tabelle').unlink()
        (self.s.z / 'mengen').unlink()
        self.s.zustand('drop.txt', drop4(130).decode())
        n = len(self.s.aufrufe())
        self.assertEqual(self.s.lauf('sperrliste').returncode, 0)
        self.assertFalse([a for a in self.s.aufrufe()[n:] if a.startswith(('nft -f', 'systemctl'))])
        self.assertFalse((self.s.z / 'tabelle').exists())
        self.assertEqual(self.s.lies(f'{LIB}/ju_mengen.nft').count('/30,'), 129)

    def test_statusbericht(self):
        self.anwenden()
        self.s.einheit('ju-keycloak.service', 'enabled', 'active')
        self.s.datei('etc/systemd/system/ju-keycloak.service', '[Unit]\n')
        self.s.einheit('postgresql.service', 'enabled', 'active')
        (self.s.w / 'run/reboot-required').write_text('', encoding='utf-8')
        out = self.gut('status')
        roh = self.s.lies(f'{LIB}/status.json')
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
        self.assertNotIn('noch nicht bestätigt', out)
        # Zähler: Zuwachs seit dem letzten Bericht, auch über ein Neuladen der Tabelle hinweg
        self.s.zustand('zaehler.json', '{"nftables": [{"counter": {"name": "sperrliste", "packets": 25}}]}')
        self.gut('status-schreiben')
        self.assertEqual(json.loads(self.s.lies(f'{LIB}/status.json'))['zahlen']['abgewiesen_24h'], 15)
        self.s.zustand('zaehler.json', '{"nftables": [{"counter": {"name": "sperrliste", "packets": 4}}]}')
        self.gut('status-schreiben')
        self.assertEqual(json.loads(self.s.lies(f'{LIB}/status.json'))['zahlen']['abgewiesen_24h'], 19)

        def staende():
            self.gut('status-schreiben')
            return {p['id']: (p['stand'], p['detail']) for p in json.loads(self.s.lies(f'{LIB}/status.json'))['pruefungen']}

        # Sperrliste: die Datei allein genügt nicht – „ok“ nur, wenn die Mengen in der geladenen Tabelle Einträge haben
        mengen = self.s.lies('zustand/mengen')
        (self.s.z / 'mengen').unlink()
        self.assertEqual(staende()['sperrliste'], ('kritisch', 'Liste vorhanden, aber nicht in der Schutztabelle geladen'))
        self.s.zustand('mengen', mengen)
        self.assertEqual(staende()['sperrliste'], ('ok', '162 Netze, von heute'))
        # der tägliche Zeitgeber läuft nicht mehr: weder Liste noch Fehlermeldung sind in den letzten zwei Tagen neu geschrieben
        alt = time.time() - 2.5 * 86400
        os.utime(self.s.w / LIB / 'drop4.txt', (alt, alt))
        self.assertEqual(staende()['sperrliste'], ('hinweis', '162 Netze, 2 Tage alt, der tägliche Zeitgeber ist seit 2 Tagen nicht gelaufen'))
        os.utime(self.s.w / LIB / 'drop4.txt')
        # kritisch: Tabelle weg, Dienst steht, ein Port offen, den ufw durchlässt; fehlende Sicherung nach zwei Tagen
        (self.s.z / 'tabelle').unlink()
        self.s.zustand('active-ju-keycloak.service', 'failed\n')
        self.s.zustand('ss', SS + 'tcp   LISTEN 0      4096                   *:8080            *:*\n'
                                  'tcp   LISTEN 0      4096                   *:5432            *:*\n')
        self.s.zustand('ufw-status', self.s.lies('zustand/ufw-status') + '8080/tcp                   ALLOW IN    Anywhere\n')
        self.s.datei('var/backups/ju-keycloak/keycloak-2026-11-19.dump', '', 0o600)
        stand = staende()
        self.assertEqual(stand['schutztabelle'][0], 'kritisch')
        self.assertEqual(stand['sperrliste'][0], 'kritisch')              # ohne Tabelle wirkt auch die Liste nicht
        self.assertEqual(stand['dienste'], ('kritisch', 'läuft nicht: ju-keycloak'))
        self.assertEqual(stand['ports'], ('kritisch', 'unerwartet aus dem Internet erreichbar: 8080/tcp'))
        self.assertEqual(stand['keycloak_sicherung'][0], 'ok')
        # nur von der Firewall gesperrte Ports sind ein Hinweis, kein Alarm
        self.s.zustand('ufw-status', '\n'.join(UFW.splitlines()[7:]) + '\n')
        self.assertEqual(staende()['ports'], ('hinweis', 'lauscht auf allen Adressen, von der Firewall gesperrt: 5432/tcp, 8080/tcp'))
        # SPERRLISTE=ja ohne FIREWALL: die Liste wird geholt, hält aber niemanden ab
        self.s.datei('etc/ju-finanzen/haertung.conf', self.s.lies('etc/ju-finanzen/haertung.conf').replace('FIREWALL=ja', 'FIREWALL=nein'))
        self.assertEqual(staende()['sperrliste'], ('hinweis', 'Liste vorhanden, wirkt aber nicht (FIREWALL=nein)'))

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
        self.assertNotIn('schutz-aus)', befehl)                       # schutz-aus (ab 0.39) reicht der Befehl an verwaltung.py durch
        hilfe = befehl.splitlines()[1:21]
        self.assertTrue(all(z.startswith('#') for z in hilfe) and befehl.splitlines()[21] == 'set -euo pipefail')
        self.assertIn("sed -n '2,21p'", befehl)
        for wort in ('haerten --bestaetigen', 'schutz-status', 'schutz-aus'):
            self.assertEqual(sum(wort in z for z in hilfe), 1, wort)
        for unit in ('ju-serverschutz.service', 'ju-serverschutz-liste.service', 'ju-serverschutz-liste.timer',
                     'ju-serverschutz-status.service', 'ju-serverschutz-status.timer'):
            text = (ROOT / 'server' / 'haertung' / unit).read_text(encoding='utf-8')
            self.assertNotIn('flush ruleset\n', text.replace('„flush ruleset“', ''))
            for zeile in re.findall(r'^Exec\w+=-?(\S+)', text, re.M):
                self.assertIn(zeile, ('/usr/sbin/nft', '/opt/ju-finanzen/current/server/haerten.sh'))
        # beim Start: erst die Regeln, dann die Sperrliste – deren Fehlen oder Scheitern lässt die Regeln stehen
        schutz = (ROOT / 'server' / 'haertung' / 'ju-serverschutz.service').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'^ExecStart=(.*)$', schutz, re.M), ['/usr/sbin/nft -f /var/lib/ju-serverschutz/ju_schutz.nft',
                                                                         '-/usr/sbin/nft -f /var/lib/ju-serverschutz/ju_mengen.nft'])
        # der Dienst des Programms darf die Datei lesen: ProtectSystem=strict macht nur schreibgeschützt, nichts unsichtbar
        dienst = (ROOT / 'server' / 'ju-finanzen.service').read_text(encoding='utf-8')
        self.assertNotRegex(dienst, r'(?m)^(InaccessiblePaths|TemporaryFileSystem|ProtectSystem=(?!strict))')

    def test_hilfe_des_skripts_ist_vollstaendig(self):
        r = subprocess.run(['bash', str(HAERTEN), '--help'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(all(z.startswith('#') for z in r.stdout.splitlines()))
        self.assertIn('--bestaetigen', r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith('noch unbestätigten Lauf zurücknimmt).'))

    def test_vorlage_der_schalter_ist_vollstaendig(self):
        vorlage = (ROOT / 'server' / 'haertung' / 'haertung.conf').read_text(encoding='utf-8')
        skript = HAERTEN.read_text(encoding='utf-8') + (ROOT / 'server' / 'haertung' / 'serverschutz.py').read_text(encoding='utf-8')
        gelesen = set(re.findall(r'\b(?:an|wert) ([A-Z][A-Z0-9_]+) ', skript)) | set(re.findall(r"(?:an|einstellung)\('([A-Z_0-9]+)'", skript))
        self.assertEqual(gelesen, set(re.findall(r'^([A-Z][A-Z0-9_]+)=', vorlage, re.M)))


if __name__ == '__main__':
    unittest.main()
