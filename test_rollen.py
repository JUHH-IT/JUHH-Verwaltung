"""Rollen und Rechte im Serverbetrieb (ab 0.34) – nur erfundene Daten."""
import json
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import app
import rollen
import test_server   # Testserver und Aufrufhilfe (nicht als Name importiert, sonst liefen dessen Tests hier noch einmal)
import zugang
from core import Store

PASSWORT = test_server.PASSWORT
ROOT = Path(__file__).resolve().parent
VOLL = {**rollen.ALL, 'auswertung': 'lesen'}   # die erste Person: alles ändern (in der Auswertung gibt es nichts zu ändern)


def P(name, **extra):
    """Pflichtangaben einer erfundenen Person zum Benutzernamen (Vorname, Nachname, E-Mail)."""
    return {'name': name, 'vorname': 'Test', 'nachname': name, 'email': re.sub(r'[^a-z0-9.-]', '', name.lower()) + '@example.org', **extra}


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
        uid = rollen.save_user(self.db, {**P('Zwei-Rollen'), 'password': PASSWORT, 'roles': [
            role_id(self.db, 'Landesmitgliederbeauftragter'), role_id(self.db, 'Landesgeschäftsführer')]}, 'Erste')
        self.assertEqual(rollen.rights_of(self.db, 'Zwei-Rollen'), {'mitglieder': 'schreiben', 'buchhaltung': 'lesen', 'verband': 'lesen', 'auswertung': 'lesen', 'sicherheit': 'lesen'})
        rollen.save_user(self.db, {**P('Zwei-Rollen'), 'id': uid, 'roles': [role_id(self.db, 'Landesvorsitzender')], 'active': False}, 'Erste')
        self.assertEqual(rollen.rights_of(self.db, 'Zwei-Rollen'), {})                 # deaktiviert

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
        self.assertEqual(free, {'kv', 'accounts', 'invoice_accounts', 'settings', 'year', 'data_start', 'wahl', 'kenntnis_felder'})   # wahl: nur die Stichtage (0.37); kenntnis_felder: nur Beschriftungen (0.55)

    def test_roles_can_be_changed_but_one_admin_remains(self):
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        admin = role_id(self.db, 'Administrator')
        with self.assertRaises(ValueError):                                            # Verwaltungsrecht der einzigen Person nehmen
            rollen.save_role(self.db, {'id': admin, 'name': 'Administrator', 'rights': {'sicherung': 'lesen'}}, 'Erste')
        erste = self.db.execute("SELECT id FROM users WHERE name='Erste'").fetchone()[0]
        with self.assertRaises(ValueError):                                            # … oder die Person deaktivieren
            rollen.save_user(self.db, {**P('Erste'), 'id': erste, 'roles': [admin], 'active': False}, 'Erste')
        with self.assertRaises(ValueError):                                            # … oder ihr die Rolle nehmen
            rollen.save_user(self.db, {**P('Erste'), 'id': erste, 'roles': [role_id(self.db, 'Schatzmeister')]}, 'Erste')
        self.assertEqual(rollen.rights_of(self.db, 'Erste'), VOLL)               # nichts davon wurde gespeichert
        rid = rollen.save_role(self.db, {'name': 'Kassenprüfer', 'rights': {'buchhaltung': 'lesen', 'verband': ''}}, 'Erste')
        self.assertEqual(json.loads(self.db.execute('SELECT rights FROM roles WHERE id=?', (rid,)).fetchone()[0]), {'buchhaltung': 'lesen'})
        for bad in ({'name': 'Kassenprüfer', 'rights': {}}, {'name': 'x', 'rights': {}}, {'name': 'Neu', 'rights': {'unbekannt': 'lesen'}},
                    {'name': 'Neu', 'rights': {'buchhaltung': 'alles'}}, {'name': '<b>Neu</b>', 'rights': {}}, {'name': 'Neu', 'rights': []}):
            with self.assertRaises(ValueError, msg=bad):
                rollen.save_role(self.db, bad, 'Erste')
        uid = rollen.save_user(self.db, {**P('Pruefer'), 'password': PASSWORT, 'roles': [rid]}, 'Erste')
        with self.assertRaises(ValueError):
            rollen.delete_role(self.db, rid, 'Erste')                                  # noch vergeben
        rollen.save_user(self.db, {**P('Pruefer'), 'id': uid, 'roles': []}, 'Erste')
        rollen.delete_role(self.db, rid, 'Erste')
        for bad in (P('Ohne-Passwort', roles=[]), P('Kurz', password='kurz', roles=[]), P('Pruefer', email='neu@example.org', password=PASSWORT, roles=[]),
                    P('Neu', password=PASSWORT, roles=[9999]), P('Neu', password=PASSWORT, roles=['1']), P('Anderer-Name', id=uid, roles=[])):
            with self.assertRaises(ValueError, msg=bad.get('name')):
                rollen.save_user(self.db, bad, 'Erste')
        for bad in (P('Pruefer', id=uid, roles=[], active='false'), P('Pruefer', id='abc', roles=[]),
                    P('Erste\u200b', email='z@example.org', password=PASSWORT, roles=[]), P('PRUEFER', email='anders@example.org', password=PASSWORT, roles=[]),
                    P('Klingel\x07ton', email='k@example.org', password=PASSWORT, roles=[])):
            with self.assertRaises(ValueError, msg=repr(bad.get('name'))):
                rollen.save_user(self.db, bad, 'Erste')
        with self.assertRaises(ValueError):
            rollen.save_role(self.db, {'name': 'SCHATZMEISTER', 'rights': {}}, 'Erste')   # gleicher Name in anderer Schreibweise
        with self.assertRaises(ValueError):
            rollen.delete_role(self.db, 'abc', 'Erste')
        # Protokoll: Rollen und Rechte vorher und nachher – wer hat wem was gegeben
        rollen.save_user(self.db, {**P('Pruefer'), 'id': uid, 'roles': [role_id(self.db, 'Schatzmeister')]}, 'Erste')
        vorsitz = role_id(self.db, 'Landesvorsitzender')
        rollen.save_role(self.db, {'id': vorsitz, 'name': 'Landesvorsitzender', 'rights': {'buchhaltung': 'schreiben'}}, 'Erste')
        log = rollen.overview(self.db)['audit']
        self.assertEqual(log[0]['action'], 'Rolle geändert')
        self.assertEqual(log[0]['detail']['rechte'], {'buchhaltung': 'schreiben'})
        self.assertEqual(log[0]['detail']['vorher']['rechte'], rollen.LEITUNG)
        self.assertEqual((log[1]['action'], log[1]['actor'], log[1]['detail']['rollen'], log[1]['detail']['vorher']['rollen']), ('Person geändert', 'Erste', ['Schatzmeister'], []))
        audit = json.dumps(self.store.rows('SELECT * FROM audit'), ensure_ascii=False)
        self.assertNotIn(PASSWORT, audit)
        self.assertNotIn(PASSWORT, json.dumps(rollen.overview(self.db), ensure_ascii=False))
        self.assertIn('Rolle angelegt', audit)

    def test_emergency_command_keeps_one_admin(self):
        import contextlib, io, verwaltung
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        self.db.execute("INSERT INTO users(name,password,active,gueltig_bis) VALUES('Zweite','',0,'2020-01-31')")
        self.db.execute("UPDATE users SET vorname='Erika',nachname='Muster',funktion='Landesschatzmeisterin' WHERE name='Erste'")
        self.db.execute("INSERT INTO users(name,password,gueltig_bis) VALUES('Dritte','','2020-01-31')")
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
        self.assertIn('Erste (Erika Muster, Landesschatzmeisterin): Passwort gesetzt; Rollen: Schatzmeister, Landesmitgliederbeauftragter, Administrator', text)
        self.assertIn('Zweite: kein Passwort (Anmeldung gesperrt); Rollen: Landesvorsitzender', text)   # Notbefehl: wieder aktiv, ohne Ablaufdatum
        self.assertIn('Dritte: kein Passwort (Anmeldung gesperrt), abgelaufen; Rollen: keine', text)

    def test_person_fields_are_checked_and_logged(self):
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        kv = self.db.execute('SELECT code FROM kv ORDER BY code LIMIT 1').fetchone()[0]
        future = (date.today() + timedelta(days=30)).isoformat()
        full = P('erika.muster', vorname=' Erika ', nachname='Muster-Beispiel', email='Erika.Muster@example.org', telefon='+49 (40) 123-456',
                 funktion='Landesschatzmeisterin', kreisverband=kv, gueltig_bis=future, totp_pflicht=False, keycloak_id='geschmuggelt',
                 password=PASSWORT, roles=[])
        uid = rollen.save_user(self.db, full, 'Erste')
        u = next(x for x in rollen.overview(self.db)['users'] if x['id'] == uid)
        self.assertEqual((u['vorname'], u['nachname'], u['email'], u['telefon'], u['funktion'], u['kreisverband'], u['gueltig_bis']),
                         ('Erika', 'Muster-Beispiel', 'Erika.Muster@example.org', '+49 (40) 123-456', 'Landesschatzmeisterin', kv, future))
        self.assertEqual((u['totp_pflicht'], u['keycloak_verbunden'], u['abgelaufen'], u['active']), (False, False, False, True))   # keycloak_id nicht über die Eingabe
        self.assertNotIn('keycloak_id', u)
        self.assertIn({'code': kv, 'name': self.db.execute('SELECT name FROM kv WHERE code=?', (kv,)).fetchone()[0]}, rollen.overview(self.db)['kreisverbaende'])
        erste = next(x for x in rollen.overview(self.db)['users'] if x['name'] == 'Erste')
        self.assertEqual((erste['vorname'], erste['totp_pflicht'], erste['gueltig_bis']), (None, True, None))   # Notbefehl: ohne Angaben, App-Pflicht Standard
        for key, value in (('name', 'zwei worte'), ('name', '-x'), ('name', 'jürgen'), ('name', 'a'), ('name', 'x' * 41), ('vorname', ''), ('nachname', 'N' * 61),
                           ('vorname', '<b>'), ('email', ''), ('email', 'keine-adresse'), ('email', 'ERIKA.MUSTER@example.org'), ('telefon', '040 123 abc'),
                           ('telefon', '1' * 31), ('funktion', 'F' * 81), ('kreisverband', 'gibt-es-nicht'), ('gueltig_bis', '31.12.2030'),
                           ('gueltig_bis', '2020-01-01'), ('totp_pflicht', 'ja')):
            with self.assertRaises(ValueError, msg=(key, value)):
                rollen.save_user(self.db, {**full, 'name': 'neue.person', 'email': 'neu@example.org', key: value}, 'Erste')
        self.assertIsNotNone(rollen.save_user(self.db, P('Kurz_1', vorname='A', nachname='B', password=PASSWORT, roles=[]), 'Erste'))   # ein Zeichen genügt
        other = rollen.save_user(self.db, P('andere', password=PASSWORT, roles=[]), 'Erste')
        with self.assertRaises(ValueError):                                            # E-Mail einer anderen Person (andere Schreibweise)
            rollen.save_user(self.db, P('andere', id=other, email='erika.muster@EXAMPLE.org', roles=[]), 'Erste')
        rollen.save_user(self.db, {**full, 'id': uid, 'password': None, 'telefon': '', 'funktion': 'Kassenwartin', 'kreisverband': None, 'totp_pflicht': True}, 'Erste')
        log = rollen.overview(self.db)['audit'][0]
        self.assertEqual((log['action'], log['detail']['funktion'], log['detail']['telefon'], log['detail']['totp_pflicht']), ('Person geändert', 'Kassenwartin', None, True))
        self.assertEqual((log['detail']['vorher']['funktion'], log['detail']['vorher']['kreisverband'], log['detail']['vorher']['totp_pflicht']), ('Landesschatzmeisterin', kv, False))
        self.assertNotIn(PASSWORT, json.dumps(self.store.rows('SELECT * FROM audit'), ensure_ascii=False))

    def test_expired_access_counts_as_inactive(self):
        zugang.set_password(self.db, 'Erste', PASSWORT, create=True)
        uid = rollen.save_user(self.db, P('befristet', password=PASSWORT, roles=[role_id(self.db, 'Administrator')], gueltig_bis=date.today().isoformat()), 'Erste')
        self.assertEqual(zugang.check_login(self.db, 'befristet', PASSWORT), 'befristet')   # „gültig bis“ heute: noch gültig
        self.assertEqual(rollen.rights_of(self.db, 'befristet')['verwaltung'], 'schreiben')
        self.db.execute('UPDATE users SET gueltig_bis=? WHERE id=?', ((date.today() - timedelta(days=1)).isoformat(), uid))
        self.db.commit()
        self.assertEqual(rollen.rights_of(self.db, 'befristet'), {})
        self.assertIsNone(zugang.check_login(self.db, 'befristet', PASSWORT))
        self.assertTrue(next(u for u in rollen.overview(self.db)['users'] if u['id'] == uid)['abgelaufen'])
        erste = self.db.execute("SELECT id FROM users WHERE name='Erste'").fetchone()[0]
        with self.assertRaises(ValueError):                                            # die abgelaufene Person zählt nicht als Verwaltung
            rollen.save_user(self.db, P('Erste', id=erste, roles=[]), 'befristet')
        past = (date.today() - timedelta(days=1)).isoformat()
        rollen.save_user(self.db, P('befristet', id=uid, gueltig_bis=past, funktion='Technik', roles=[]), 'Erste')   # altes Datum darf stehen bleiben
        rollen.save_user(self.db, P('befristet', id=uid, gueltig_bis=None, roles=[]), 'Erste')                      # verlängert: wieder gültig
        self.assertEqual(zugang.check_login(self.db, 'befristet', PASSWORT), 'befristet')

    def test_old_database_without_new_columns_is_migrated(self):
        import sqlite3
        path = Path(self.tmp.name) / 'alt.db'
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,password TEXT NOT NULL)')
        con.execute("INSERT INTO users(name,password) VALUES('Alte Person','')")   # Name mit Leerzeichen aus älterer Version bleibt gültig
        con.commit()
        con.close()
        store = Store(path)
        self.addCleanup(store.db.close)
        columns = {row[1] for row in store.db.execute('PRAGMA table_info(users)')}
        self.assertEqual({c for c, _ in rollen.USER_COLUMNS} - columns, set())
        row = store.db.execute("SELECT active,totp_pflicht,sitzung_stand,vorname,gueltig_bis,keycloak_id FROM users WHERE name='Alte Person'").fetchone()
        self.assertEqual(tuple(row), (1, 1, 0, None, None, None))
        self.assertEqual(rollen.rights_of(store.db, 'Alte Person'), VOLL)
        uid = store.db.execute("SELECT id FROM users WHERE name='Alte Person'").fetchone()[0]
        rollen.save_user(store.db, P('Alte Person', id=uid, email='alt@example.org', password=PASSWORT, roles=[role_id(store.db, 'Administrator')]), 'Alte Person')
        rollen.init(store.db)                                                          # zweimal: nichts doppelt

    def test_every_server_address_is_classified(self):
        """Neue Adressen in app.py müssen in rollen.GET bzw. rollen.POST stehen – und keine Einträge ohne Adresse."""
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        get_part, post_part = source.split('    def do_POST(self):')
        get_part = get_part.split('    def do_GET(self):')[1]
        post_part = post_part.split('\ndef write_private')[0]
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

    def login(self, name, password=PASSWORT, confirm=True):
        status, body, headers = self.call('/api/login', {'name': name, 'password': password})
        self.assertEqual(status, 200, body)
        who = {'cookie': headers.get('Set-Cookie', '').split(';')[0].split('=', 1)[1], 'csrf': body['csrf']}
        if confirm:   # Passwortbestätigung für kritische Aktionen (0.35) – geprüft in test_sicherheit.py
            self.assertEqual(self.post(who, '/api/bestaetigen', {'password': password})[0], 200)
        return who

    def get(self, who, path):
        status, body, _ = self.call(path, cookie=who['cookie'])
        return status, body

    def post(self, who, path, data):
        status, body, _ = self.call(path, data, cookie=who['cookie'], csrf=who['csrf'])
        return status, body

    def person(self, name, *roles):
        status, body = self.post(self.chefin, '/api/benutzer', {**P(name), 'password': PASSWORT, 'roles': [self.roles[r] for r in roles], 'active': True})
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
        self.assertFalse(any(account[k] for k in account if k not in ('member', 'first', 'today', 'austritt', 'jahre', 'verlauf', 'angaben')))   # Beitragskonto leer
        self.assertTrue(all(set(j) == {'year', 'stand'} for j in account['jahre']))                  # Akte (0.43): Stand je Jahr ohne Betrag
        self.assertEqual(account['austritt'], {'offen': True, 'mandat': False, 'festgelegt': []})     # Hinweise zum Austritt: nur Ja/Nein, Jahre mit festem Soll (0.37)
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

    def test_umsatz_notiz_und_belege_nach_recht(self):
        """0.60: Notiz und Belege am Kontoumsatz – ansehen mit „Buchhaltung lesen“, ändern nur mit „Buchhaltung ändern“, ohne Buchhaltung nichts."""
        import base64
        datei = {'name': 'quittung.pdf', 'content': base64.b64encode(b'%PDF-1.4 erfunden').decode()}
        schatz = self.person('Schatz-Person', 'Schatzmeister')
        self.assertEqual(self.post(schatz, '/api/umsatz-notiz', {'id': 1, 'text': 'Beleg folgt'})[0], 200)
        status, body = self.post(schatz, '/api/umsatz-beleg', {'id': 1, 'file': datei})
        self.assertEqual(status, 200, body)
        beleg = body['id']
        leser = self.person('Lese-Person', 'Landesgeschäftsführer')
        status, akte = self.get(leser, '/api/umsatz-akte?id=1')
        self.assertEqual((status, akte['notiz']['text'], [x['name'] for x in akte['belege']]), (200, 'Beleg folgt', ['quittung.pdf']))
        for path, data in (('/api/umsatz-notiz', {'id': 1, 'text': 'geändert'}), ('/api/umsatz-beleg', {'id': 1, 'file': datei}),
                           ('/api/umsatz-beleg-entfernen', {'id': beleg})):
            self.assertEqual(self.post(leser, path, data)[0], 403, path)
        fremd = self.person('Mitglieder-Person', 'Landesmitgliederbeauftragter')
        for path in ('/api/umsatz-akte?id=1', f'/api/umsatz-beleg?id={beleg}'):
            self.assertEqual(self.get(fremd, path)[0], 403, path)
        self.assertEqual(self.post(fremd, '/api/umsatz-notiz', {'id': 1, 'text': 'x'})[0], 403)
        self.assertEqual(self.get(fremd, '/api/data?year=2026')[1]['bank_stand'], {'am': '', 'bis': ''})   # gleiche Form, kein Inhalt
        self.assertEqual(self.get(schatz, '/api/umsatz-akte?id=1')[1]['notiz']['text'], 'Beleg folgt')      # nichts davon hat etwas geändert
        self.assertEqual(self.post(schatz, '/api/umsatz-beleg-entfernen', {'id': beleg})[0], 200)

    def test_read_only_roles_cannot_change_anything(self):
        for number, role in enumerate(('Landesgeschäftsführer', 'Landesvorsitzender')):
            who = self.person(f'leitung{number}', role)
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
            self.assertEqual(store.rows("SELECT action FROM audit WHERE actor LIKE 'leitung%'"), [])   # Lesen hinterlässt keine Änderung
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
        self.assertEqual(self.post(who, '/api/benutzer', {**P('Neue.Person'), 'password': PASSWORT, 'roles': [self.roles['Landesvorsitzender']]})[0], 200)
        # eine Person ohne Rolle sieht nur die leere Startseite
        self.assertEqual(self.post(who, '/api/benutzer', {**P('Ohne.Rolle'), 'password': PASSWORT, 'roles': []})[0], 200)
        nobody = self.login('Ohne.Rolle')
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
        self.assertEqual(self.post(self.chefin, '/api/benutzer', {**P('Wechsel'), 'id': user['id'], 'roles': user['roles'], 'active': False})[0], 200)
        self.assertEqual(self.get(who, '/api/data?year=2026')[0], 401)                                 # deaktiviert: Sitzung endet
        self.assertEqual(self.call('/api/login', {'name': 'Wechsel', 'password': PASSWORT})[0], 401)
        self.assertEqual(self.post(self.chefin, '/api/benutzer', {**P('Wechsel'), 'id': user['id'], 'roles': user['roles'], 'active': True, 'password': PASSWORT + '-neu'})[0], 200)
        self.assertEqual(self.call('/api/login', {'name': 'Wechsel', 'password': PASSWORT + '-neu'})[0], 200)

    def test_ending_sessions_and_expiry_end_the_session(self):
        who = self.person('sitzung', 'Landesvorsitzender')
        self.assertEqual(self.get(who, '/api/data?year=2026')[0], 200)
        store = Store(app.DB)
        try:
            uid = store.db.execute("SELECT id FROM users WHERE name='sitzung'").fetchone()[0]
            rollen.sitzungen_beenden(store.db, uid)
        finally:
            store.db.close()
        self.assertEqual(self.get(who, '/api/data?year=2026')[0], 401)                 # Sitzungsstand hochgezählt: abgemeldet
        self.assertEqual(self.get(self.chefin, '/api/data?year=2026')[0], 200)         # andere Personen nicht
        who = self.login('sitzung')
        self.assertEqual(self.get(who, '/api/data?year=2026')[0], 200)
        store = Store(app.DB)
        try:
            store.db.execute("UPDATE users SET gueltig_bis='2020-01-31' WHERE id=?", (uid,))
            store.db.commit()
        finally:
            store.db.close()
        self.assertEqual(self.get(who, '/api/data?year=2026')[0], 401)                 # abgelaufen: Sitzung endet
        self.assertEqual(self.call('/api/login', {'name': 'sitzung', 'password': PASSWORT})[0], 401)

    def test_session_without_fingerprint_is_invalid(self):
        for s in app.SESSIONS.values():
            s['fp'] = None
        self.assertEqual(self.get(self.chefin, '/api/data?year=2026')[0], 401)

    def test_last_admin_is_protected_over_http(self):
        status, overview = self.get(self.chefin, '/api/benutzer')
        me = next(u for u in overview['users'] if u['name'] == 'Chefin')
        status, body = self.post(self.chefin, '/api/benutzer', {**P('Chefin'), 'id': me['id'], 'roles': [], 'active': True})
        self.assertEqual(status, 400)
        self.assertIn('Mindestens eine', body['error'])
        self.assertEqual(self.get(self.chefin, '/api/benutzer')[0], 200)


if __name__ == '__main__':
    unittest.main()
