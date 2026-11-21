"""Mitgliederverwaltung 0.43: Listen (CSV, Etiketten, Anschreiben), Stand des Beitrags ohne Beträge, Mitgliederakte – nur erfundene Daten."""
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import mitglieder
import mitgliederkonto
import mitgliederlisten
import test_rollen   # Anmeldung und Personen am Testserver (nicht als Name importiert, sonst liefen dessen Tests hier noch einmal)
import test_server
from core import Store

BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg', birth='1995-03-01', joined='2020-01-01', kv='5100')


def pdf_text(raw):
    from pypdf import PdfReader
    return [page.extract_text() for page in PdfReader(BytesIO(raw)).pages]


class Basis(unittest.TestCase):
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


class ListenTests(Basis):
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

    def test_excel_ein_blatt_je_kreisverband(self):
        import openpyxl
        from io import BytesIO
        other = self.s.member({**BASE, 'kv': '5300', 'number': 'T-4', 'first_name': '=HYPERLINK("x")', 'last_name': 'Formel'}, 'Test')
        body, kind, name = mitgliederlisten.datei(self.s, {'art': 'excel', 'ids': [self.a, self.b, other]}, 'Test')
        self.assertTrue(name.endswith('.xlsx') and kind.endswith('spreadsheetml.sheet'))
        book = openpyxl.load_workbook(BytesIO(body))
        self.assertEqual(book.sheetnames, ['Altona - Elbvororte', 'Eimsbüttel'])            # „/“ ist im Blattnamen nicht erlaubt
        first = book['Altona - Elbvororte']
        self.assertEqual([c.value for c in first[1]][:5], ['Mitgliedsnummer', 'Anrede', 'Titel', 'Vorname', 'Nachname'])
        self.assertEqual([r[0].value for r in first.iter_rows(min_row=2)], ['T-2', 'T-1'])
        self.assertTrue(first['A1'].font.bold)
        cell = book['Eimsbüttel']['D2']
        self.assertEqual((cell.value, cell.data_type), ('=HYPERLINK("x")', 's'))             # Text, keine Formel
        row = self.s.db.execute("SELECT detail FROM audit WHERE action='Mitgliederliste ausgegeben' ORDER BY id DESC").fetchone()
        self.assertEqual(json.loads(row[0]), {'art': 'excel', 'anzahl': 3})

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
        self.s.db.execute("UPDATE members SET letter_salutation='Liebe <i>Erika</i> & Co' WHERE id=?", (self.a,))
        members = mitgliederlisten.auswahl(self.s, [self.a, self.b, self.c])
        pages = pdf_text(mitgliederlisten.anschreiben(self.s.config(), members, 'Einladung <font size=40>', 'Erster Absatz.\n\nZweiter <b>Absatz</b>.'))
        self.assertEqual(len(pages), 2)                                  # Max hat Postausschluss
        self.assertIn('Liebe <i>Erika</i> & Co,', pages[0])                     # Briefanrede und Betreff bleiben Text
        self.assertIn('Einladung <font size=40>', pages[0])
        self.assertIn('Hallo Ole,', pages[1])
        self.assertIn('Zweiter <b>Absatz</b>.', pages[1])               # Text bleibt Text, kein Markup
        for betreff, text in (('', 'x'), ('x', ''), ('x' * 201, 'x'), ('x', 'x' * 5001), (None, 'x'), ('x', 3)):
            with self.assertRaises(ValueError):
                mitgliederlisten.anschreiben(self.s.config(), members, betreff, text)

    def test_protokoll_nur_art_und_anzahl(self):
        body, kind, name = mitgliederlisten.datei(self.s, {'art': 'etiketten', 'ids': [self.a, self.b]}, 'Test')
        self.assertEqual(kind, 'application/pdf')
        self.assertTrue(name.startswith('Adressetiketten-'))
        row = self.s.db.execute("SELECT detail FROM audit WHERE action='Mitgliederliste ausgegeben'").fetchone()
        self.assertEqual(json.loads(row[0]), {'art': 'etiketten', 'anzahl': 1})
        mitgliederlisten.datei(self.s, {'art': 'csv', 'ids': [self.a, self.b]}, 'Test')
        row = self.s.db.execute("SELECT detail FROM audit WHERE action='Mitgliederliste ausgegeben' ORDER BY id DESC").fetchone()
        self.assertEqual(json.loads(row[0]), {'art': 'csv', 'anzahl': 2})                  # CSV: alle, auch ohne Post
        with self.assertRaises(ValueError):
            mitgliederlisten.datei(self.s, {'art': 'alles', 'ids': [self.a]}, 'Test')

    def test_stand_des_beitrags_ohne_betrag(self):
        row = {'soll': 3000, 'due_now': True, 'open': 0, 'paid': 3000}
        self.assertEqual(mitgliederkonto.jahr_stand({**row, 'soll': None, 'open': None}), 'klaeren')
        self.assertEqual(mitgliederkonto.jahr_stand({**row, 'soll': 0}), 'frei')
        self.assertEqual(mitgliederkonto.jahr_stand({**row, 'open': 2000}), 'offen')
        self.assertEqual(mitgliederkonto.jahr_stand(row), 'bezahlt')
        self.assertEqual(mitgliederkonto.jahr_stand({**row, 'due_now': False, 'paid': 0}), 'spaeter')   # Rechnung noch nicht fällig
        self.assertEqual(mitgliederkonto.jahr_stand({**row, 'due_now': False}), 'bezahlt')             # vor der Fälligkeit bezahlt
        guest = self.s.member({**BASE, 'number': 'T-9', 'first_name': 'Gina', 'last_name': 'Gast', 'guest': '1'}, 'Test')
        self.assertEqual(next(x for x in self.s.snapshot(2026)['members'] if x['id'] == guest)['dues_state'], 'frei')
        member = next(x for x in self.s.snapshot(2026)['members'] if x['id'] == self.a)
        self.assertEqual(member['dues_state'], 'offen')
        mitglieder.notiz(self.s, self.a, 'vertraulich', 'Test')
        self.assertFalse(any(k in r for r in self.s.dues_preview(2026) for k in ('note', 'mobile', 'gender')))   # wie die Gesamtliste
        self.assertIn(2026, member['dues_open_years'])                      # fällig und nicht eingegangen (ab 2027 auch Folgejahre)


class ZahlungTests(Basis):
    """Stand des Beitrags in Liste und Akte gleich: Zahlungen decken die ältesten fälligen Beiträge zuerst."""
    def setUp(self):
        super().setUp()
        self.s.save_settings({'fo_start': '2025'}, 'Test')
        with self.s.db:
            self.s.db.execute("INSERT INTO settings VALUES('data_start','2025-01-01')")

    def test_zahlung_deckt_erst_das_vorjahr(self):
        raw = 'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-03-01;30;Zahler;Beitrag;k1\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, 'k1.csv'), [2], 'Test')
        bid = self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': '30', 'members': [{'member_id': self.a}]}], 'Test')
        member = next(x for x in self.s.snapshot(2026)['members'] if x['id'] == self.a)
        jahre = {y['year']: mitgliederkonto.jahr_stand(y) for y in mitgliederkonto.account(self.s, self.a)['years']}
        self.assertEqual(jahre, {2025: 'bezahlt', 2026: 'offen'})
        self.assertEqual((member['dues_state'], member['dues_open_years']), ('offen', [2026]))   # Liste wie Akte, nicht „bezahlt“


class AkteTests(Basis):
    """Notiz, Verlauf und alle Angaben eines Mitglieds (Akte)."""

    def test_notiz_und_verlauf(self):
        mitglieder.notiz(self.s, self.a, '  Rückruf vereinbart \r\n', 'Test')
        self.assertEqual(self.s.db.execute('SELECT note FROM members WHERE id=?', (self.a,)).fetchone()[0], 'Rückruf vereinbart')
        mitglieder.notiz(self.s, self.a, 'Rückruf vereinbart', 'Test')                       # unverändert: kein Eintrag
        old = dict(self.s.db.execute('SELECT * FROM members WHERE id=?', (self.a,)).fetchone())
        self.s.update_member(self.a, {**old, 'email': 'neu@example.org', 'note': 'ignoriert'}, 'Test')   # Formular ändert die Notiz nie
        self.assertEqual(self.s.db.execute('SELECT note FROM members WHERE id=?', (self.a,)).fetchone()[0], 'Rückruf vereinbart')
        mitglieder.austritt(self.s, self.a, '2026-12-31', '', 'Test')
        mitglieder.notiz(self.s, self.a, '', 'Test')
        texts = [x['text'] for x in mitglieder.verlauf(self.s, self.a)]
        self.assertEqual(texts, ['Notiz gelöscht', 'Austritt zum 31.12.2026 eingetragen', 'Geändert: E-Mail privat', 'Notiz geändert', 'Mitglied angelegt'])
        self.assertEqual([x['text'] for x in mitglieder.verlauf(self.s, self.b)], ['Mitglied angelegt'])   # nur Einträge dieses Mitglieds
        with self.s.db:
            self.s.audit('Test', 'Rechnung storniert', {'id': self.a})                         # andere Art mit derselben Kennung
        self.assertEqual(len(mitglieder.verlauf(self.s, self.a)), 5)
        details = ' '.join(r[0] for r in self.s.db.execute("SELECT detail FROM audit WHERE action='Notiz zum Mitglied geändert'"))
        self.assertNotIn('Rückruf', details)                                                  # der Text steht nie im Protokoll
        for bad in ((self.a, 'x' * 1001), ('1', 'x'), (True, 'x'), (self.a, None), (999, 'x')):
            with self.assertRaises(ValueError, msg=bad):
                mitglieder.notiz(self.s, *bad, 'Test')

    def test_alle_angaben(self):
        m = dict(self.s.db.execute('SELECT * FROM members WHERE id=?', (self.b,)).fetchone())
        rows = dict(mitglieder.angaben(self.s, m))
        self.assertEqual((rows['Mitgliedsnummer'], rows['Geburtsdatum'], rows['Postausschluss'], rows['Kreisverband']),
                         ('T-2', '01.03.1995', 'ja', 'Altona / Elbvororte'))
        self.assertNotIn('Gastmitglied', rows)                                                # Nein-Angaben fallen weg
        self.assertNotIn('Austritt', rows)
        self.assertNotIn('Ortsverband', dict(mitglieder.angaben(self.s, {**m, 'ov_id': 999})))   # unbekannter Ortsverband: keine leere Zeile


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
        status, akte = self.get(officer, f"/api/member-account?id={member['id']}")
        self.assertEqual(status, 200)
        self.assertIn(2026, [j['year'] for j in akte['jahre'] if j['stand'] == 'offen'])           # Stand je Jahr ohne Betrag
        self.assertEqual((akte['years'], akte['balance']), ([], 0))                                 # Beitragskonto bleibt leer
        self.assertIn(['Mitgliedsnummer', 'M-1'], akte['angaben'])
        self.assertEqual(self.post(officer, '/api/member-notiz', {'id': member['id'], 'text': 'Rückruf'})[0], 200)
        self.assertEqual(self.get(officer, f"/api/member-account?id={member['id']}")[1]['member']['note'], 'Rückruf')
        self.assertNotIn('note', member)                                                          # nicht in der Gesamtliste

        admin = self.person('technik', 'Administrator')                                               # keine Mitgliederdaten
        reader = self.person('leitung', 'Landesgeschäftsführer')                                      # lesen ja, Notiz nein
        self.assertEqual(self.post(reader, '/api/member-notiz', {'id': member['id'], 'text': 'x'})[0], 403)
        self.assertEqual(self.file(admin, '/api/mitglieder-liste', {'art': 'csv', 'ids': [member['id']]})[0], 403)
        status, r = self.get(admin, '/api/regelwerk')                                                  # Regelwerk: alle Angemeldeten
        self.assertEqual(status, 200)
        self.assertTrue(r['regeln'])


if __name__ == '__main__':
    unittest.main()
