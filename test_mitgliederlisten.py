"""Listen der Mitgliederverwaltung (ab 0.43): Auswahl als CSV, Etiketten, Anschreiben; Stand des Beitrags ohne Beträge – nur erfundene Daten."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import mitgliederkonto
import mitgliederlisten
import test_rollen   # Anmeldung und Personen am Testserver (nicht als Name importiert, sonst liefen dessen Tests hier noch einmal)
import test_server
from core import Store

BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg', birth='1995-03-01', joined='2020-01-01', kv='5100')


def pdf_text(raw):
    from pypdf import PdfReader
    return [page.extract_text() for page in PdfReader(BytesIO(raw)).pages]


class ListenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Beispielweg 2\n20095 Hamburg', 'age_basis': 'jan1'}, 'Test')
        self.a = self.s.member({**BASE, 'number': 'T-1', 'first_name': 'Erika', 'last_name': 'Muster', 'salutation': 'Frau',
                                'letter_salutation': 'Liebe Erika', 'email': 'erika@example.org', 'mobile': '0151 00000000'}, 'Test')
        self.b = self.s.member({**BASE, 'number': 'T-2', 'first_name': 'Max', 'last_name': 'Beispiel', 'no_mail': '1'}, 'Test')
        self.c = self.s.member({**BASE, 'number': 'T-3', 'first_name': 'Ole', 'last_name': 'Probe', 'title': 'Dr.'}, 'Test')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_auswahl_prueft_die_eingabe(self):
        for bad in (None, [], 'x', [True], [1.5], ['1'], list(range(1, mitgliederlisten.MAX + 2)), [self.a, 999]):
            with self.assertRaises(ValueError, msg=bad):
                mitgliederlisten.auswahl(self.s, bad)
        self.assertEqual([m['number'] for m in mitgliederlisten.auswahl(self.s, [self.c, self.a, self.b, self.a])], ['T-2', 'T-1', 'T-3'])

    def test_csv_mit_kontakt_und_formelschutz(self):
        self.s.db.execute("UPDATE members SET city='=Hamburg' WHERE id=?", (self.c,))
        text = mitgliederlisten.csv(self.s, mitgliederlisten.auswahl(self.s, [self.a, self.c])).decode('utf-8-sig')
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith('Mitgliedsnummer;Anrede;Titel;Vorname;Nachname;Briefanrede'))
        self.assertIn('T-1;Frau;;Erika;Muster;Liebe Erika;;Musterweg 1;20095;Hamburg;;erika@example.org;;0151 00000000', text)
        self.assertIn("'=Hamburg", text)
        self.assertEqual(len(lines), 3)

    def test_post_nur_mit_anschrift_und_ohne_postausschluss(self):
        members = mitgliederlisten.auswahl(self.s, [self.a, self.b, self.c])
        self.assertEqual([m['number'] for m in members if mitgliederlisten.postfaehig(m)], ['T-1', 'T-3'])
        text = ' '.join(pdf_text(mitgliederlisten.etiketten(members)))
        self.assertIn('Frau', text)
        self.assertIn('Dr. Ole Probe', text)
        self.assertNotIn('Max', text)
        with self.assertRaises(ValueError):
            mitgliederlisten.etiketten(mitgliederlisten.auswahl(self.s, [self.b]))

    def test_etiketten_neue_seite_nach_24(self):
        ids = [self.s.member({**BASE, 'number': f'E-{n}', 'first_name': 'Test', 'last_name': f'Person {n}'}, 'Test') for n in range(25)]
        self.assertEqual(len(pdf_text(mitgliederlisten.etiketten(mitgliederlisten.auswahl(self.s, ids)))), 2)

    def test_anschreiben_je_mitglied(self):
        members = mitgliederlisten.auswahl(self.s, [self.a, self.b, self.c])
        pages = pdf_text(mitgliederlisten.anschreiben(self.s.config(), members, 'Einladung', 'Erster Absatz.\n\nZweiter <b>Absatz</b>.'))
        self.assertEqual(len(pages), 2)                                  # Max hat Postausschluss
        self.assertIn('Liebe Erika,', pages[0])
        self.assertIn('Hallo Ole,', pages[1])
        self.assertIn('Zweiter <b>Absatz</b>.', pages[1])               # Text bleibt Text, kein Markup
        self.assertIn('Einladung', pages[0])
        for betreff, text in (('', 'x'), ('x', ''), ('x' * 201, 'x'), ('x', 'x' * 5001), (None, 'x'), ('x', 3)):
            with self.assertRaises(ValueError):
                mitgliederlisten.anschreiben(self.s.config(), members, betreff, text)

    def test_protokoll_nur_art_und_anzahl(self):
        body, kind, name = mitgliederlisten.datei(self.s, {'art': 'etiketten', 'ids': [self.a, self.b]}, 'Test')
        self.assertEqual(kind, 'application/pdf')
        self.assertTrue(name.startswith('Adressetiketten-'))
        row = self.s.db.execute("SELECT detail FROM audit WHERE action='Mitgliederliste ausgegeben'").fetchone()
        self.assertEqual(json.loads(row[0]), {'art': 'etiketten', 'anzahl': 1})
        with self.assertRaises(ValueError):
            mitgliederlisten.datei(self.s, {'art': 'alles', 'ids': [self.a]}, 'Test')

    def test_stand_des_beitrags_ohne_betrag(self):
        m = {'guest': 0, 'dues_free': 0}
        self.assertEqual(mitgliederkonto.stand({'soll': None, 'paid': 0}, m), 'klaeren')
        self.assertEqual(mitgliederkonto.stand({'soll': 0, 'paid': 0}, m), 'frei')
        self.assertEqual(mitgliederkonto.stand({'soll': 3000, 'paid': 0}, {'guest': 1, 'dues_free': 0}), 'frei')
        self.assertEqual(mitgliederkonto.stand({'soll': 3000, 'paid': 1000}, m), 'offen')
        self.assertEqual(mitgliederkonto.stand({'soll': 3000, 'paid': 3000}, m), 'bezahlt')
        member = next(x for x in self.s.snapshot(2026)['members'] if x['id'] == self.a)
        self.assertEqual(member['dues_state'], 'offen')
        self.assertIn(2026, member['dues_open_years'])                      # fällig und nicht eingegangen (ab 2027 auch Folgejahre)


class ListenHttpTests(unittest.TestCase):
    """Am laufenden Server: Liste nur mit „Mitglieder lesen“ und Passwortbestätigung; Stand des Beitrags auch ohne Buchhaltung."""
    setUpClass = classmethod(test_server.ServerHttpTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerHttpTests.tearDownClass.__func__)
    call = test_server.ServerHttpTests.call
    setUp, login, get, post, person = (getattr(test_rollen.RollenHttpTests, name) for name in ('setUp', 'login', 'get', 'post', 'person'))

    def file(self, who, path, data):
        request = urllib.request.Request(self.url + path, json.dumps(data).encode(), {
            'Host': 'ju-finanzen.beispiel.ts.net', 'Content-Type': 'application/json', 'Origin': test_server.ADRESSE,
            'X-CSRF-Token': who['csrf'], 'Cookie': f"__Host-ju_session={who['cookie']}; ju_session={who['cookie']}"})
        try:
            with urllib.request.urlopen(request) as r:
                return r.status, r.headers.get('Content-Type'), r.headers.get('Content-Disposition'), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get('Content-Type'), None, e.read()

    def test_rechte_und_bestaetigung(self):
        officer = self.person('Mitglieder-Person', 'Landesmitgliederbeauftragter')
        member = self.get(officer, '/api/data?year=2026')[1]['members'][0]
        self.assertEqual((member['balance'], member['dues_state']), (0, 'offen'))                    # Stand ja, Betrag nein
        self.assertIn(2026, member['dues_open_years'])
        status, kind, disposition, body = self.file(officer, '/api/mitglieder-liste', {'art': 'csv', 'ids': [member['id']]})
        self.assertEqual((status, kind), (200, 'text/csv; charset=utf-8'), body)
        self.assertIn('attachment', disposition)
        self.assertIn('M-1;', body.decode('utf-8-sig'))
        self.assertEqual(self.file(officer, '/api/mitglieder-liste', {'art': 'csv', 'ids': ['x']})[0], 400)
        unconfirmed = self.login('Mitglieder-Person', confirm=False)
        status, _, _, body = self.file(unconfirmed, '/api/mitglieder-liste', {'art': 'csv', 'ids': [member['id']]})
        self.assertEqual((status, json.loads(body).get('confirm')), (428, True))
        self.assertEqual(self.get(officer, '/api/regelwerk')[0], 200)

        admin = self.person('technik', 'Administrator')                                               # keine Mitgliederdaten
        self.assertEqual(self.file(admin, '/api/mitglieder-liste', {'art': 'csv', 'ids': [member['id']]})[0], 403)
        status, r = self.get(admin, '/api/regelwerk')                                                  # Regelwerk: alle Angemeldeten
        self.assertEqual(status, 200)
        self.assertTrue(r['regeln'])


if __name__ == '__main__':
    unittest.main()
