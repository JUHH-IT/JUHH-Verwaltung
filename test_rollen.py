"""Rollen und Rechte im Serverbetrieb (ab 0.34) – nur erfundene Daten."""
import os
os.environ['JU_FINANZEN_OHNE_SCHLUESSELBUND'] = '1'
import json
import re
import tempfile
import unittest
from pathlib import Path

import app
import rollen
import test_server   # Testserver und Aufrufhilfe (nicht als Name importiert, sonst liefen dessen Tests hier noch einmal)
import zugang
from core import Store

PASSWORT = test_server.PASSWORT
ROOT = Path(__file__).resolve().parent
VOLL = {**rollen.ALL, 'auswertung': 'lesen'}   # die erste Person: alles ändern (in der Auswertung gibt es nichts zu ändern)


def role_id(db, name):
    return db.execute('SELECT id FROM roles WHERE name=?', (name,)).fetchone()[0]


class RollenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 't.db')
        self.addCleanup(self.store.db.close)
        self.db = self.store.db

    def test_default_roles_and_first_user(self):
        names = [r[0] for r in self.db.execute('SELECT name FROM roles ORDER BY id')]
        self.assertEqual(names, ['Schatzmeister', 'Landesmitgliederbeauftragter', 'Landesgeschäftsführer', 'Landesvorsitzender', 'Administrator'])
        zugang.set_password(self.db, 'Erste Person', PASSWORT, create=True)
        self.assertEqual(rollen.rights_of(self.db, 'Erste Person'), VOLL)        # erste Person: alles
        zugang.set_password(self.db, 'Zweite Person', PASSWORT, create=True)
        self.assertEqual(rollen.rights_of(self.db, 'Zweite Person'), {})               # weitere: erst nach Vergabe einer Rolle
        self.assertEqual(rollen.rights_of(self.db, 'Unbekannt'), {})

    def test_existing_database_keeps_full_access_and_takeover_from_mac(self):
        # Bestand vor 0.34: Person ohne Rollen-Tabellen → behält alle Rechte
        self.db.executescript('DROP TABLE user_roles; DROP TABLE roles;')
        self.db.execute("INSERT INTO users(name,password) VALUES('Mac-Person','')")
        self.db.commit()
        rollen.init(self.db)
        self.assertEqual(rollen.rights_of(self.db, 'Mac-Person'), VOLL)
        # auf dem Server kann sich die Mac-Person (ohne Passwort) nicht anmelden: die erste Person mit Passwort bekommt alles
        zugang.set_password(self.db, 'Server-Person', PASSWORT, create=True)
        self.assertEqual(rollen.rights_of(self.db, 'Server-Person'), VOLL)
        zugang.set_password(self.db, 'Dritte', PASSWORT, create=True)
        self.assertEqual(rollen.rights_of(self.db, 'Dritte'), {})

    def test_password_change_never_grants_rights(self):
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        zugang.set_password(self.db, 'Zweite', PASSWORT, create=True)
        self.db.execute('DELETE FROM user_roles')                                      # entarteter Zustand: niemand hat mehr Rechte
        self.db.commit()
        zugang.set_password(self.db, 'Zweite', PASSWORT + 'x', actor='Zweite')         # eigener Passwortwechsel über die Oberfläche
        self.assertEqual(rollen.rights_of(self.db, 'Zweite'), {})

    def test_rights_are_the_maximum_over_roles(self):
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        uid = rollen.save_user(self.db, {'name': 'Zwei Rollen', 'password': PASSWORT, 'roles': [
            role_id(self.db, 'Landesmitgliederbeauftragter'), role_id(self.db, 'Landesgeschäftsführer')]}, 'Erste')
        self.assertEqual(rollen.rights_of(self.db, 'Zwei Rollen'), {'mitglieder': 'schreiben', 'buchhaltung': 'lesen', 'verband': 'lesen', 'auswertung': 'lesen'})
        rollen.save_user(self.db, {'id': uid, 'name': 'Zwei Rollen', 'roles': [role_id(self.db, 'Landesvorsitzender')], 'active': False}, 'Erste')
        self.assertEqual(rollen.rights_of(self.db, 'Zwei Rollen'), {})                 # deaktiviert

    def test_allowed_and_required(self):
        reader = {'buchhaltung': 'lesen'}
        self.assertTrue(rollen.allowed(reader, rollen.required('GET', '/api/kasse')))
        self.assertFalse(rollen.allowed(reader, rollen.required('POST', '/api/book')))
        self.assertTrue(rollen.allowed({'buchhaltung': 'schreiben'}, rollen.required('POST', '/api/book')))
        self.assertTrue(rollen.allowed({}, rollen.required('GET', '/api/data')))
        self.assertTrue(rollen.allowed({'verband': 'lesen'}, rollen.required('GET', '/api/rpj')))   # eines von mehreren genügt
        # unbekannte Adresse: nur mit „Verwaltung schreiben“
        self.assertFalse(rollen.allowed({a: 'schreiben' for a in rollen.AREA_IDS if a != 'verwaltung'}, rollen.required('POST', '/api/neu-und-unbekannt')))
        self.assertTrue(rollen.allowed({'verwaltung': 'schreiben'}, rollen.required('GET', '/api/neu-und-unbekannt')))

    def test_snapshot_keeps_shape_without_content(self):
        snapshot = {'members': [{'id': 1, 'name': 'Erika Muster', 'paid': 4800, 'dues_soll': 4800, 'dues_basis': 'Staffel', 'balance': -1200, 'dues_issues': ['x']}],
                    'bank': [{'id': 1, 'amount': 5}], 'cash': {'open': 2, 'balance': 100, 'rows': [1]}, 'kv': [{'code': '5100'}],
                    'budgets': [1], 'dues_open': 3, 'settings': {'issuer': 'JU'}, 'year': 2026, 'data_start': None}
        nothing = rollen.filter_snapshot(snapshot, {})
        self.assertEqual(nothing['members'], [])
        self.assertEqual(nothing['bank'], [])
        self.assertEqual(nothing['cash'], {'open': 0, 'balance': 0, 'rows': []})
        self.assertEqual(nothing['dues_open'], 0)
        self.assertEqual((nothing['kv'], nothing['settings'], nothing['year']), (snapshot['kv'], {}, 2026))   # Stammdaten: nur Schalter
        with_switch = rollen.filter_snapshot({'settings': {'issuer': 'JU', 'iban': 'x', 'smtp_user': 'y', 'map_tiles': 'yes'}}, {'verwaltung': 'schreiben'})
        self.assertEqual(with_switch['settings'], {'map_tiles': 'yes'})
        self.assertEqual(rollen.filter_snapshot(snapshot, {'auswertung': 'lesen'})['settings'], {})
        for area in rollen.FACHLICH:
            self.assertEqual(rollen.filter_snapshot(snapshot, {area: 'lesen'})['settings'], snapshot['settings'], area)
        members_only = rollen.filter_snapshot(snapshot, {'mitglieder': 'schreiben'})
        self.assertEqual(members_only['members'], [{'id': 1, 'name': 'Erika Muster', 'paid': 0, 'dues_soll': 0, 'dues_basis': '', 'balance': 0, 'dues_issues': []}])
        self.assertEqual(members_only['bank'], [])
        self.assertEqual(rollen.filter_snapshot(snapshot, rollen.ALL), snapshot)
        self.assertEqual(set(nothing), set(snapshot))
        real = self.store.snapshot(2026)                                               # jeder Schlüssel der echten Gesamtdaten ist eingeordnet oder bewusst frei
        free = set(real) - set(rollen.SNAPSHOT)
        self.assertEqual(free, {'kv', 'accounts', 'invoice_accounts', 'settings', 'year', 'data_start'})

    def test_roles_can_be_changed_but_one_admin_remains(self):
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        admin = role_id(self.db, 'Administrator')
        with self.assertRaises(ValueError):                                            # Verwaltungsrecht der einzigen Person nehmen
            rollen.save_role(self.db, {'id': admin, 'name': 'Administrator', 'rights': {'sicherung': 'lesen'}}, 'Erste')
        erste = self.db.execute("SELECT id FROM users WHERE name='Erste'").fetchone()[0]
        with self.assertRaises(ValueError):                                            # … oder die Person deaktivieren
            rollen.save_user(self.db, {'id': erste, 'name': 'Erste', 'roles': [admin], 'active': False}, 'Erste')
        with self.assertRaises(ValueError):                                            # … oder ihr die Rolle nehmen
            rollen.save_user(self.db, {'id': erste, 'name': 'Erste', 'roles': [role_id(self.db, 'Schatzmeister')]}, 'Erste')
        self.assertEqual(rollen.rights_of(self.db, 'Erste'), VOLL)               # nichts davon wurde gespeichert
        rid = rollen.save_role(self.db, {'name': 'Kassenprüfer', 'rights': {'buchhaltung': 'lesen', 'verband': ''}}, 'Erste')
        self.assertEqual(json.loads(self.db.execute('SELECT rights FROM roles WHERE id=?', (rid,)).fetchone()[0]), {'buchhaltung': 'lesen'})
        for bad in ({'name': 'Kassenprüfer', 'rights': {}}, {'name': 'x', 'rights': {}}, {'name': 'Neu', 'rights': {'unbekannt': 'lesen'}},
                    {'name': 'Neu', 'rights': {'buchhaltung': 'alles'}}, {'name': '<b>Neu</b>', 'rights': {}}, {'name': 'Neu', 'rights': []}):
            with self.assertRaises(ValueError, msg=bad):
                rollen.save_role(self.db, bad, 'Erste')
        uid = rollen.save_user(self.db, {'name': 'Prüferin', 'password': PASSWORT, 'roles': [rid]}, 'Erste')
        with self.assertRaises(ValueError):
            rollen.delete_role(self.db, rid, 'Erste')                                  # noch vergeben
        rollen.save_user(self.db, {'id': uid, 'name': 'Prüferin', 'roles': []}, 'Erste')
        rollen.delete_role(self.db, rid, 'Erste')
        for bad in ({'name': 'Ohne Passwort', 'roles': []}, {'name': 'Kurz', 'password': 'kurz', 'roles': []}, {'name': 'Prüferin', 'password': PASSWORT, 'roles': []},
                    {'name': 'Neu', 'password': PASSWORT, 'roles': [9999]}, {'name': 'Neu', 'password': PASSWORT, 'roles': ['1']},
                    {'id': uid, 'name': 'Anderer Name', 'roles': []}):
            with self.assertRaises(ValueError, msg=bad.get('name')):
                rollen.save_user(self.db, bad, 'Erste')
        for bad in ({'id': uid, 'name': 'Prüferin', 'roles': [], 'active': 'false'}, {'id': 'abc', 'name': 'Prüferin', 'roles': []},
                    {'name': 'Erste\u200b', 'password': PASSWORT, 'roles': []}, {'name': 'PRÜFERIN', 'password': PASSWORT, 'roles': []},
                    {'name': 'Klingel\x07ton', 'password': PASSWORT, 'roles': []}):
            with self.assertRaises(ValueError, msg=repr(bad.get('name'))):
                rollen.save_user(self.db, bad, 'Erste')
        with self.assertRaises(ValueError):
            rollen.save_role(self.db, {'name': 'SCHATZMEISTER', 'rights': {}}, 'Erste')   # gleicher Name in anderer Schreibweise
        with self.assertRaises(ValueError):
            rollen.delete_role(self.db, 'abc', 'Erste')
        # Protokoll: Rollen und Rechte vorher und nachher – wer hat wem was gegeben
        rollen.save_user(self.db, {'id': uid, 'name': 'Prüferin', 'roles': [role_id(self.db, 'Schatzmeister')]}, 'Erste')
        vorsitz = role_id(self.db, 'Landesvorsitzender')
        rollen.save_role(self.db, {'id': vorsitz, 'name': 'Landesvorsitzender', 'rights': {'buchhaltung': 'schreiben'}}, 'Erste')
        log = rollen.overview(self.db)['audit']
        self.assertEqual(log[0]['action'], 'Rolle geändert')
        self.assertEqual(log[0]['detail']['rechte'], {'buchhaltung': 'schreiben'})
        self.assertEqual(log[0]['detail']['vorher']['rechte'], rollen.READ_ALL)
        self.assertEqual((log[1]['action'], log[1]['actor'], log[1]['detail']['rollen'], log[1]['detail']['vorher']['rollen']), ('Person geändert', 'Erste', ['Schatzmeister'], []))
        audit = json.dumps(self.store.rows('SELECT * FROM audit'), ensure_ascii=False)
        self.assertNotIn(PASSWORT, audit)
        self.assertNotIn(PASSWORT, json.dumps(rollen.overview(self.db), ensure_ascii=False))
        self.assertIn('Rolle angelegt', audit)

    def test_emergency_command_keeps_one_admin(self):
        import contextlib, io, verwaltung
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        self.db.execute("INSERT INTO users(name,password,active) VALUES('Zweite','',0)")
        self.db.commit()
        data = str(Path(self.tmp.name))
        import shutil
        shutil.copy(Path(self.tmp.name) / 't.db', Path(self.tmp.name) / 'finanzen.sqlite3')
        run = lambda *args: verwaltung.main(['--data', data, '--backups', data, *args])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                run('rolle', 'Erste', 'Administrator', '--entfernen')              # die einzige Verwaltungsperson
            run('rolle', 'Zweite', 'Landesvorsitzender')
            run('benutzer')
        text = out.getvalue()
        self.assertIn('wieder aktiv', text)
        self.assertIn('Erste: Passwort gesetzt; Rollen: Schatzmeister, Landesmitgliederbeauftragter, Administrator', text)
        self.assertIn('Zweite: kein Passwort (Anmeldung gesperrt); Rollen: Landesvorsitzender', text)

    def test_every_server_address_is_classified(self):
        """Neue Adressen in app.py müssen in rollen.GET bzw. rollen.POST stehen – und keine Einträge ohne Adresse."""
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        get_part, post_part = source.split('    def do_POST(self):')
        get_part = get_part.split('    def do_GET(self):')[1]
        post_part = post_part.split('\ndef new_login_code')[0]
        paths = lambda text: set(re.findall(r"'(/api/[A-Za-z0-9._-]+)'", text))
        get_paths, post_paths = paths(get_part), paths(post_part)
        self.assertGreater(len(get_paths), 60)
        self.assertGreater(len(post_paths), 90)
        self.assertEqual(get_paths - set(rollen.GET) - set(rollen.OUTSIDE), set())
        self.assertEqual(post_paths - set(rollen.POST) - set(rollen.OUTSIDE), set())
        self.assertEqual(set(rollen.GET) - get_paths, set())
        self.assertEqual(set(rollen.POST) - post_paths, set())
        for table in (rollen.GET, rollen.POST):
            for path, need in table.items():
                if need != rollen.FREE:
                    self.assertTrue(need and all(area in rollen.AREA_IDS and level in ('lesen', 'schreiben') for area, level in need), path)
        self.assertEqual(set(rollen.SNAPSHOT.values()) - set(rollen.AREA_IDS), set())


class RollenHttpTests(unittest.TestCase):
    """Die Prüfung am laufenden Server: was eine Rolle nicht darf, lehnt der Server ab – unabhängig von der Oberfläche."""
    setUpClass = classmethod(test_server.ServerHttpTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerHttpTests.tearDownClass.__func__)
    call = test_server.ServerHttpTests.call

    def setUp(self):
        app.THROTTLE = zugang.Throttle()
        app.SESSIONS.clear()
        if app.DB.exists():
            app.DB.unlink()
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'Chefin', PASSWORT, create=True)
            kv = store.db.execute('SELECT code FROM kv ORDER BY code LIMIT 1').fetchone()[0]
            store.db.execute("INSERT INTO members(number,name,address,email,birth,joined,kv) VALUES('M-1','Erika Muster','Musterweg 1, 20095 Hamburg','erika@example.org','2000-01-01','2020-01-01',?)", (kv,))
            store.db.execute("INSERT INTO bank(booked,amount,name,reference,fingerprint) VALUES('2026-03-01',4800,'Erika Muster','Beitrag','f1')")
            store.db.commit()
            self.roles = {r[1]: r[0] for r in store.db.execute('SELECT id,name FROM roles')}
        finally:
            store.db.close()
        self.chefin = self.login('Chefin')

    def login(self, name, password=PASSWORT):
        status, body, headers = self.call('/api/login', {'name': name, 'password': password})
        self.assertEqual(status, 200, body)
        return {'cookie': headers.get('Set-Cookie', '').split(';')[0].split('=', 1)[1], 'csrf': body['csrf']}

    def get(self, who, path):
        status, body, _ = self.call(path, cookie=who['cookie'])
        return status, body

    def post(self, who, path, data):
        status, body, _ = self.call(path, data, cookie=who['cookie'], csrf=who['csrf'])
        return status, body

    def person(self, name, *roles):
        status, body = self.post(self.chefin, '/api/benutzer', {'name': name, 'password': PASSWORT, 'roles': [self.roles[r] for r in roles], 'active': True})
        self.assertEqual(status, 200, body)
        return self.login(name)

    def test_member_officer_sees_members_without_amounts_and_no_accounting(self):
        who = self.person('Mitglieder-Person', 'Landesmitgliederbeauftragter')
        self.assertEqual(self.get(who, '/api/status')[1]['rights'], {'mitglieder': 'schreiben', 'auswertung': 'lesen'})
        status, data = self.get(who, '/api/data?year=2026')
        self.assertEqual(status, 200)
        self.assertEqual([m['name'] for m in data['members']], ['Erika Muster'])
        self.assertEqual((data['members'][0]['paid'], data['members'][0]['balance'], data['members'][0]['dues_soll']), (0, 0, 0))
        self.assertEqual((data['bank'], data['invoices'], data['journal'], data['budgets'], data['audit']), ([], [], [], [], []))
        member = data['members'][0]['id']
        status, account = self.get(who, f'/api/member-account?id={member}')
        self.assertEqual(status, 200)
        self.assertEqual(account['member']['name'], 'Erika Muster')
        self.assertFalse(any(account[k] for k in account if k not in ('member', 'first', 'today')))   # Beitragskonto leer
        for path in ('/api/kasse?year=2026', '/api/konten?year=2026', '/api/journal.csv?year=2026', '/api/op-liste?kv=', '/api/rpj?year=2026',
                     '/api/benutzer', '/api/backup', f'/api/member-dues?id={member}', '/api/monatsbericht.pdf?year=2026&month=1'):
            self.assertEqual(self.get(who, path)[0], 403, path)
        for path, data in (('/api/book', {}), ('/api/settings', {}), ('/api/benutzer', {}), ('/api/rolle', {}), ('/api/import-bank', {}), ('/api/kv-close', {})):
            self.assertEqual(self.post(who, path, data)[0], 403, path)
        self.assertEqual(self.get(who, '/api/workflow?year=2026')[1]['parked'], [])                   # gleiche Form, kein Inhalt
        self.assertEqual(self.get(who, '/api/haushalt?year=2026')[1]['rows'], [])
        self.assertEqual(self.get(who, '/api/karte')[0], 200)
        status, report = self.get(who, '/api/auswertung?year=2026')
        self.assertEqual(status, 200)
        self.assertFalse(report['amounts'])                                                           # Auswertung ohne Beitragsbeträge
        self.assertTrue(report['kv_rows'])
        self.assertTrue(all((k['soll'], k['paid'], k['arrears'], k['arrears_count'], k['paid_share']) == (0, 0, 0, 0, None) for k in report['kv_rows']))
        self.assertTrue(self.get(self.chefin, '/api/auswertung?year=2026')[1]['amounts'])
        self.assertIn('issuer', self.get(who, '/api/data?year=2026')[1]['settings'])                  # fachliche Rolle: Stammdaten lesbar
        self.assertNotEqual(self.post(who, '/api/klaerfall-notiz', {})[0], 403)                        # darf (scheitert nur an den fehlenden Angaben)

    def test_read_only_roles_cannot_change_anything(self):
        for role in ('Landesgeschäftsführer', 'Landesvorsitzender'):
            who = self.person(role + ' Person', role)
            status, data = self.get(who, '/api/data?year=2026')
            self.assertEqual(len(data['members']), 1)
            self.assertEqual(len(data['bank']), 1)
            self.assertEqual(self.get(who, '/api/kasse?year=2026')[0], 200)
            self.assertEqual(self.get(who, '/api/rpj?year=2026')[0], 200)
            for path in ('/api/book', '/api/member', '/api/member-update', '/api/settings', '/api/kv-values', '/api/rpj-projekt', '/api/benutzer',
                         '/api/klaerfall-klaeren', '/api/mail-send', '/api/adressen-laden'):
                self.assertEqual(self.post(who, path, {})[0], 403, (role, path))
            self.assertEqual(self.get(who, '/api/backup')[0], 403)
            self.assertEqual(self.get(who, '/api/benutzer')[0], 403)
            # Lastschriftdatei: enthält IBANs und vermerkt den Abruf – nicht mit Leserecht
            self.assertEqual(self.get(who, '/api/sepa.xml?batch=1')[0], 403)
        store = Store(app.DB)
        try:
            self.assertEqual(store.rows("SELECT action FROM audit WHERE actor LIKE 'Landes%'"), [])   # Lesen hinterlässt keine Änderung
        finally:
            store.db.close()

    def test_administrator_manages_people_but_sees_no_data(self):
        who = self.person('Technik', 'Administrator')
        status, data = self.get(who, '/api/data?year=2026')
        self.assertEqual((data['members'], data['bank'], data['invoices']), ([], [], []))
        self.assertEqual(set(data['settings']) - set(rollen.SETTINGS_FREE), set())                    # keine Bankverbindung, kein E-Mail-Konto
        self.assertEqual(self.get(who, '/api/karte')[0], 403)
        self.assertEqual(self.get(who, '/api/kasse?year=2026')[0], 403)
        self.assertEqual(self.get(who, '/api/backup')[0], 403)                                         # Sicherung herunterladen = alle Daten
        status, overview = self.get(who, '/api/benutzer')
        self.assertEqual(status, 200)
        self.assertEqual({u['name'] for u in overview['users']}, {'Chefin', 'Technik'})
        self.assertNotIn('scrypt', json.dumps(overview))
        self.assertEqual(self.post(who, '/api/benutzer', {'name': 'Neue Person', 'password': PASSWORT, 'roles': [self.roles['Landesvorsitzender']]})[0], 200)
        # eine Person ohne Rolle sieht nur die leere Startseite
        self.assertEqual(self.post(who, '/api/benutzer', {'name': 'Ohne Rolle', 'password': PASSWORT, 'roles': []})[0], 200)
        nobody = self.login('Ohne Rolle')
        self.assertEqual(self.get(nobody, '/api/status')[1]['rights'], {})
        self.assertEqual(self.get(nobody, '/api/data?year=2026')[1]['members'], [])
        self.assertNotIn('issuer', self.get(nobody, '/api/data?year=2026')[1]['settings'])
        self.assertEqual(self.get(nobody, '/api/auswertung.pdf?year=2026')[0], 403)
        for path in ('/api/karte', '/api/kasse?year=2026', '/api/benutzer', '/api/auswertung?year=2026'):
            self.assertEqual(self.get(nobody, path)[0], 403, path)
        self.assertEqual(self.post(nobody, '/api/password', {'current': PASSWORT, 'new': PASSWORT + '-neu', 'repeat': PASSWORT + '-neu'})[0], 200)

    def test_changes_take_effect_immediately_and_deactivation_ends_the_session(self):
        who = self.person('Wechsel', 'Landesvorsitzender')
        self.assertEqual(self.get(who, '/api/kasse?year=2026')[0], 200)
        status, overview = self.get(self.chefin, '/api/benutzer')
        user = next(u for u in overview['users'] if u['name'] == 'Wechsel')
        role = next(r for r in overview['roles'] if r['name'] == 'Landesvorsitzender')
        self.assertEqual(self.post(self.chefin, '/api/rolle', {'id': role['id'], 'name': role['name'], 'rights': {'auswertung': 'lesen'}})[0], 200)
        self.assertEqual(self.get(who, '/api/kasse?year=2026')[0], 403)                                # Rolle geändert: gilt sofort
        self.assertEqual(self.post(self.chefin, '/api/benutzer', {'id': user['id'], 'name': 'Wechsel', 'roles': user['roles'], 'active': False})[0], 200)
        self.assertEqual(self.get(who, '/api/data?year=2026')[0], 401)                                 # deaktiviert: Sitzung endet
        self.assertEqual(self.call('/api/login', {'name': 'Wechsel', 'password': PASSWORT})[0], 401)
        self.assertEqual(self.post(self.chefin, '/api/benutzer', {'id': user['id'], 'name': 'Wechsel', 'roles': user['roles'], 'active': True, 'password': PASSWORT + '-neu'})[0], 200)
        self.assertEqual(self.call('/api/login', {'name': 'Wechsel', 'password': PASSWORT + '-neu'})[0], 200)

    def test_management_only_in_server_mode(self):
        app.MODE = 'mac'
        try:
            self.assertEqual(self.get(self.chefin, '/api/benutzer')[0], 404)
            for path in ('/api/benutzer', '/api/rolle', '/api/rolle-loeschen'):
                self.assertEqual(self.post(self.chefin, path, {'name': 'Neu', 'password': PASSWORT, 'roles': []})[0], 404, path)
        finally:
            app.MODE = 'server'
        self.assertEqual(self.get(self.chefin, '/api/benutzer')[0], 200)

    def test_session_without_fingerprint_is_invalid(self):
        for s in app.SESSIONS.values():
            s['fp'] = None
        self.assertEqual(self.get(self.chefin, '/api/data?year=2026')[0], 401)

    def test_last_admin_is_protected_over_http(self):
        status, overview = self.get(self.chefin, '/api/benutzer')
        me = next(u for u in overview['users'] if u['name'] == 'Chefin')
        status, body = self.post(self.chefin, '/api/benutzer', {'id': me['id'], 'name': 'Chefin', 'roles': [], 'active': True})
        self.assertEqual(status, 400)
        self.assertIn('Mindestens eine', body['error'])
        self.assertEqual(self.get(self.chefin, '/api/benutzer')[0], 200)


if __name__ == '__main__':
    unittest.main()
