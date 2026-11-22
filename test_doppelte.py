"""Doppelte Mitglieder finden (ab 0.45) – nur erfundene Testdaten."""
import json
import tempfile
import unittest
from pathlib import Path

import doppelte
import mitglieder
import mitgliederkonto
import test_rollen   # Anmeldung und Personen am Testserver (nicht als Name importiert, sonst liefen dessen Tests hier noch einmal)
import test_server
from core import Store

BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg', birth='2000-05-01', joined='2020-01-01', kv='5100')


class DoppelteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, first, last, **extra):
        self.n += 1
        return self.s.member({**BASE, 'number': f'T-{self.n}', 'first_name': first, 'last_name': last, **extra}, 'Test')

    def groups(self):
        return [(g['ids'], g['gruende']) for g in doppelte.gruppen(self.s)]

    def test_name_und_geburtsdatum_auch_mit_schreibvarianten(self):
        a = self.member('Jonas', 'Müller')
        b = self.member('Jonas', 'Mueller', street='Anderer Weg 5')          # Umlaut ausgeschrieben, andere Anschrift
        c = self.member('Müller', 'Jonas', street='Dritter Weg 7')           # Vor- und Nachname vertauscht
        self.member('Jonas', 'Müller', birth='2001-05-01', street='Vierter Weg 9')   # anderes Geburtsdatum: kein Treffer
        self.assertEqual(self.groups(), [([a, b, c], ['gleicher Name und Geburtsdatum'])])

    def test_umlaut_ohne_e_und_zerlegte_zeichen(self):
        a = self.member('Jonas', 'Müller')
        b = self.member('Jonas', 'Muller', street='Anderer Weg 5')
        c = self.member('Jonas', 'Mu\u0308ller', street='Dritter Weg 7')               # „ü“ als u + Trema (eingefügter Text)
        self.assertEqual(self.groups(), [([a, b, c], ['gleicher Name und Geburtsdatum'])])

    def test_name_und_anschrift_nur_mit_hausnummer(self):
        self.member('Lea', 'Probe', street='Musterweg')
        self.member('Lea', 'Probe', street='Musterweg', birth='1999-01-01')
        self.assertEqual(self.groups(), [])

    def test_name_und_anschrift(self):
        a = self.member('Lea', 'Probe', street='Musterstraße 12')
        b = self.member('Lea', 'Probe', street='Musterstr. 12', birth='1999-01-01')
        self.member('Lea', 'Probe', street='Musterstraße 14', birth='1998-01-01')   # andere Hausnummer
        self.assertEqual(self.groups(), [([a, b], ['gleicher Name und Anschrift'])])

    def test_mail_und_geburtsdatum(self):
        a = self.member('Tim', 'Albers', email='Familie@example.org')
        b = self.member('Timo', 'Alberts', street='Anderer Weg 5', email='familie@example.org ')
        self.member('Tom', 'Albers', street='Dritter Weg 7', email='familie@example.org', birth='2003-02-02')   # Geschwister: kein Treffer
        self.assertEqual(self.groups(), [([a, b], ['gleiche E-Mail-Adresse und Geburtsdatum'])])

    def test_abgekuerzter_vorname_aber_keine_zwillinge(self):
        a = self.member('Max', 'Beispiel')
        b = self.member('Maximilian', 'Beispiel', street='Anderer Weg 5')
        self.member('Anna', 'Beispiel', street='Dritter Weg 7')               # Zwilling mit anderem Vornamen
        self.member('Mia', 'Beispiel', street='Vierter Weg 9')
        self.assertEqual(self.groups(), [([a, b], ['ähnlicher Vorname, gleicher Nachname und Geburtsdatum'])])

    def test_vornamen_unabhaengig_von_der_reihenfolge(self):
        pairs = (('Anna Maria', 'Maria'), ('Peter Max', 'Peter'), ('Max Peter', 'Max'), ('Maximilian', 'Max'))
        expected = []
        for n, (long, short) in enumerate(pairs):
            last = f'Familie{chr(65 + n)}'
            a = self.member(long, last, street=f'Weg {n} 1')
            b = self.member(short, last, street=f'Gasse {n} 2')
            expected.append(([a, b], ['ähnlicher Vorname, gleicher Nachname und Geburtsdatum']))
        self.member('Jo', 'Kurz')                                                          # zwei Buchstaben: zu kurz
        self.member('Jonas', 'Kurz', street='Anderer Weg 5')
        self.member('Anna Maria', 'Zwilling', street='Dritter Weg 7')                      # nur ein gemeinsamer Vorname
        self.member('Maria Sophie', 'Zwilling', street='Vierter Weg 9')
        self.assertEqual(sorted(self.groups()), sorted(expected))

    def test_mehrere_gruende_und_gruppen(self):
        a = self.member('Erika', 'Muster', email='erika@example.org')
        b = self.member('Erika', 'Muster', email='erika@example.org')         # gleiche Anschrift, Name, Geburt, E-Mail
        c = self.member('Ole', 'Probe', street='Anderer Weg 5', birth='1990-01-01')
        d = self.member('Ole', 'Probe', street='Dritter Weg 7', birth='1990-01-01')
        self.assertEqual(self.groups(), [([a, b], ['gleicher Name und Geburtsdatum', 'gleicher Name und Anschrift',
                                                   'gleiche E-Mail-Adresse und Geburtsdatum']),
                                         ([c, d], ['gleicher Name und Geburtsdatum'])])

    def test_kein_duplikat(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        c = self.member('Erika', 'Muster', street='Dritter Weg 7')
        doppelte.kein_duplikat(self.s, [a, b], 'Test')
        self.assertEqual(self.groups(), [([a, b, c], ['gleicher Name und Geburtsdatum'])])   # c hängt noch an beiden
        doppelte.kein_duplikat(self.s, [a, b, c, a], 'Test')
        self.assertEqual(self.groups(), [])
        doppelte.kein_duplikat(self.s, [a, b], 'Test')                                     # zweimal: kein Fehler
        row = self.s.db.execute("SELECT detail FROM audit WHERE action='Kein Duplikat bestätigt' ORDER BY id DESC").fetchone()
        self.assertEqual(json.loads(row[0]), {'ids': [a, b]})
        for bad in (None, [a], [a, a], [a, 'x'], [a, True], [a, 999], [a, 2 ** 70]):
            with self.assertRaises(ValueError, msg=bad):
                doppelte.kein_duplikat(self.s, bad, 'Test')
        with self.assertRaisesRegex(ValueError, 'Höchstens'):
            doppelte.kein_duplikat(self.s, list(range(1, doppelte.MAX_GRUPPE + 2)), 'Test')

    def test_tabelle_bleibt_beim_neuen_start(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        doppelte.kein_duplikat(self.s, [a, b], 'Test')
        self.s.db.close()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.assertEqual(self.groups(), [])


class ZusammenfuehrenTests(unittest.TestCase):
    """Zusammenführen (0.47): Angaben ergänzen, Buchhaltung umhängen, Hindernisse, Protokoll ohne Inhalte."""
    tearDown, member = DoppelteTests.tearDown, DoppelteTests.member

    def setUp(self):
        DoppelteTests.setUp(self)
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Beispielweg 2', 'iban': 'DE89 3704 0044 0532 0130 00', 'age_basis': 'jan1'}, 'Test')

    def row(self, mid):
        r = self.s.db.execute('SELECT * FROM members WHERE id=?', (mid,)).fetchone()
        return dict(r) if r else None

    def mandat(self, mid, ref):
        with self.s.db:
            self.s.db.execute("INSERT INTO mandates(member_id,reference,holder,iban,signed_on,created) VALUES(?,?,?,?,?,?)",
                              (mid, ref, 'Test Halter', 'DE02120300000000202051', '2026-01-01', '2026-01-01'))

    def test_vorschau_und_zusammenfuehren(self):
        a = self.member('Erika', 'Muster', joined='2021-01-01')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5', joined='2019-05-01', email='erika@example.org', mobile='0151 0000000', no_mail='1')
        c = self.member('Erika', 'Muster', street='Dritter Weg 7', birth='1990-01-01', email='erika@example.org')
        mitglieder.notiz(self.s, a, 'Notiz A', 'Test')
        mitglieder.notiz(self.s, b, 'Notiz B', 'Test')
        self.s.issue_dues(2026, [b], 'Test')
        mitgliederkonto.set_target(self.s, b, 2027, 0, 'Test-Befreiung', 'Test')
        self.mandat(b, 'TEST-1')
        doppelte.kein_duplikat(self.s, [b, c], 'Test')
        self.assertEqual(doppelte.vorschau(self.s, a, b)['konflikte'], ['Das entfallende Mitglied hat Rechnungen, Zahlungen, Mandate, Erstattungen '
                                                                         'oder Festlegungen – umhängen darf nur, wer „Buchhaltung ändern“ hat.'])
        v = doppelte.vorschau(self.s, a, b, True)
        self.assertEqual((v['bleibt']['number'], v['entfaellt']['number'], v['konflikte']), ('T-1', 'T-2', []))
        self.assertEqual(v['ergaenzt'], ['E-Mail privat', 'Handy privat', 'Eintritt (01.05.2019)', 'Postausschluss', 'Notiz'])   # Anschrift: a hat eine
        self.assertEqual(v['umhaengen'], [['Rechnungen', 1], ['SEPA-Mandate', 1], ['festgelegte Beiträge', 1]])
        self.assertEqual(v['unterschiede'], [['Straße', 'Musterweg 1', 'Anderer Weg 5']])   # es gilt die des bleibenden
        doppelte.zusammenfuehren(self.s, a, b, 'Test', True)
        self.assertIsNone(self.row(b))
        m = self.row(a)
        self.assertEqual((m['email'], m['mobile'], m['joined'], m['no_mail'], m['street'], m['note']),
                         ('erika@example.org', '0151 0000000', '2019-05-01', 1, 'Musterweg 1', 'Notiz A\n—\nNotiz B'))
        for table in ('invoices', 'mandates', 'dues_targets'):
            self.assertEqual([tuple(r) for r in self.s.db.execute(f'SELECT member_id FROM {table}')], [(a,)], table)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM dues_rates WHERE member_id=?', (b,)).fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mitglieder_keine_doppelten').fetchone()[0], 0)
        self.assertGreater(self.member('Neu', 'Person'), c)                                  # die Kennung von b wird nie neu vergeben
        self.assertIn(b, [r[0] for r in self.s.db.execute('SELECT id FROM deleted_members')])
        raw = self.s.db.execute("SELECT detail FROM audit WHERE action='Mitglieder zusammengeführt'").fetchone()[0]
        self.assertEqual(json.loads(raw), {'id': a, 'number': 'T-1', 'entfaellt': {'id': b, 'number': 'T-2'},
                                           'ergaenzt': ['email', 'joined', 'mobile', 'no_mail', 'note'],
                                           'umgehaengt': {'Rechnungen': 1, 'SEPA-Mandate': 1, 'festgelegte Beiträge': 1}})
        for secret in ('Notiz B', 'erika@', '0151'):
            self.assertNotIn(secret, raw)
        verlauf = [x['text'] for x in mitglieder.verlauf(self.s, a)]
        self.assertEqual(verlauf[0], 'Zusammengeführt mit Nr. T-2 – ergänzt: E-Mail privat, Eintritt, Handy privat, Postausschluss, Notiz')
        self.assertIn('Notiz geändert (Nr. T-2)', verlauf)                                  # Verlauf des aufgegangenen Mitglieds bleibt sichtbar
        self.assertIn('Mitglied angelegt (Nr. T-2)', verlauf)
        self.assertNotIn('Mitglied angelegt (Nr. T-3)', verlauf)

    def test_anschrift_bleibt_und_ortsverband_nur_im_selben_kv(self):
        import gliederung
        with self.s.db:
            ov, _ = gliederung.ensure(self.s.db, '5100', '1', 'Test-OV')
        a = self.member('Ole', 'Probe', kv='5200')
        b = self.member('Ole', 'Probe', street='Anderer Weg 5', zip='22767', address_extra='c/o Test', ov_id=ov)
        v = doppelte.vorschau(self.s, a, b)
        self.assertEqual(v['neu'], {})                                                       # anderer Kreisverband: kein Ortsverband
        self.assertEqual(v['unterschiede'][:2], [['Straße', 'Musterweg 1', 'Anderer Weg 5'], ['PLZ', '20095', '22767']])
        self.assertEqual(v['unterschiede'][2][0], 'Kreisverband')
        before = self.row(a)['address']
        doppelte.zusammenfuehren(self.s, a, b, 'Test')
        self.assertEqual(self.row(a)['address'], before)
        c = self.member('Ole', 'Probe', street='Dritter Weg 7')
        d = self.member('Ole', 'Probe', street='Vierter Weg 9', ov_id=ov)
        self.assertEqual(doppelte.vorschau(self.s, c, d)['neu'], {'ov_id': ov})

    def test_hindernisse_aendern_nichts(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        self.s.issue_dues(2026, [a, b], 'Test')
        self.mandat(a, 'TEST-1')
        self.mandat(b, 'TEST-2')
        mitglieder.notiz(self.s, a, 'x' * 600, 'Test')
        mitglieder.notiz(self.s, b, 'y' * 600, 'Test')
        konflikte = doppelte.vorschau(self.s, a, b, True)['konflikte']
        self.assertEqual(len(konflikte), 3, konflikte)
        self.assertIn('Beide haben eine Beitragsrechnung 2026', konflikte[0])
        with self.assertRaisesRegex(ValueError, 'Beitragsrechnung 2026'):
            doppelte.zusammenfuehren(self.s, a, b, 'Test', True)
        self.assertIsNotNone(self.row(b))
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM audit WHERE action='Mitglieder zusammengeführt'").fetchone()[0], 0)
        for keep, drop in ((a, a), (a, 999), ('1', b), (True, b), (a, 2 ** 70), (None, b)):
            with self.assertRaises(ValueError, msg=(keep, drop)):
                doppelte.vorschau(self.s, keep, drop)

    def test_ausgetretenes_bleibendes_mitglied_bekommt_keine_notiz(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        mitglieder.notiz(self.s, b, 'Notiz B', 'Test')
        mitglieder.austritt(self.s, a, '2025-12-31', '', 'Test')
        v = doppelte.vorschau(self.s, a, b)
        self.assertNotIn('note', v['neu'])
        self.assertIn('Notiz des entfallenden Mitglieds wird nicht übernommen', v['hinweise'][0])

    def test_stornierte_rechnung_bleibt_hindernis(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        self.s.issue_dues(2026, [a, b], 'Test')
        bill = self.s.db.execute("SELECT id FROM invoices WHERE member_id=?", (b,)).fetchone()[0]
        self.s.reverse_invoice(bill, 'Test')
        konflikte = doppelte.vorschau(self.s, a, b, True)['konflikte']
        self.assertEqual(len(konflikte), 1)
        self.assertIn('auch stornierte zählen', konflikte[0])
        self.assertNotIn('stornieren', konflikte[0])                                      # kein Rat, der nicht hilft

    def test_rechnung_beim_einen_festlegung_beim_anderen(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        self.s.issue_dues(2026, [a], 'Test')
        mitgliederkonto.set_target(self.s, b, 2026, 0, 'Test-Befreiung', 'Test')
        for keep, drop in ((a, b), (b, a)):
            konflikte = doppelte.vorschau(self.s, keep, drop, True)['konflikte']
            self.assertEqual(len(konflikte), 1, (keep, drop))
            self.assertIn('Für 2026 hat eines eine offene Beitragsrechnung', konflikte[0])
        with self.assertRaisesRegex(ValueError, 'offene Beitragsrechnung'):
            doppelte.zusammenfuehren(self.s, a, b, 'Test', True)
        self.assertEqual(self.s.db.execute('SELECT member_id FROM dues_targets').fetchone()[0], b)

    def test_festlegung_umhaengen_nur_mit_buchhaltung(self):
        a = self.member('Erika', 'Muster')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5')
        mitgliederkonto.set_target(self.s, b, 2026, 0, 'Test-Befreiung', 'Test')
        self.assertIn('„Buchhaltung ändern“', doppelte.vorschau(self.s, a, b)['konflikte'][0])
        self.assertEqual(doppelte.vorschau(self.s, a, b, True)['konflikte'], [])

    def test_wiedereintritt_kreisverband_und_beitragsfrei(self):
        a = self.member('Erika', 'Muster', joined='2026-01-01')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5', joined='2015-01-01', left_on='2018-12-31', kv_joined='2016-03-01',
                        dues_free='1', guest='1')
        v = doppelte.vorschau(self.s, a, b)
        self.assertEqual(v['neu'], {})                                                     # Wiedereintritt: nichts aus der alten Mitgliedschaft
        self.assertIn('endete vorher', v['hinweise'][0])
        andersherum = doppelte.vorschau(self.s, b, a)                                      # die alte bleibt: ebenfalls ein Hinweis
        self.assertIn('vermutlich ein Wiedereintritt', andersherum['hinweise'][0])
        self.assertNotIn('joined', andersherum['neu'])
        labels = [u[0] for u in v['unterschiede']]
        self.assertIn('Beitragsfrei', labels)
        self.assertIn('Gastmitglied', labels)
        c = self.member('Ole', 'Probe', kv='5200')
        d = self.member('Ole', 'Probe', street='Anderer Weg 5', kv_joined='2021-01-01')    # anderer Kreisverband: kein Eintritt KV
        self.assertNotIn('kv_joined', doppelte.vorschau(self.s, c, d)['neu'])
        e = self.member('Ole', 'Probe', street='Dritter Weg 7')
        self.assertEqual(doppelte.vorschau(self.s, e, d)['neu'], {'kv_joined': '2021-01-01'})

    def test_wiedereintritt_ohne_ortsverband_und_lueckenloser_wechsel(self):
        import gliederung
        with self.s.db:
            ov, _ = gliederung.ensure(self.s.db, '5100', '1', 'Test-OV')
        a = self.member('Erika', 'Muster', joined='2021-01-02')
        b = self.member('Erika', 'Muster', street='Anderer Weg 5', joined='2015-01-01', left_on='2020-12-31', ov_id=ov, kv_joined='2016-03-01')
        v = doppelte.vorschau(self.s, a, b)                                                # ein Tag Lücke: Wiedereintritt
        self.assertEqual(v['neu'], {})
        self.assertIn('Ortsverband und Eintritt Kreisverband bleiben', v['hinweise'][0])
        with self.s.db:
            self.s.db.execute("UPDATE members SET joined='2021-01-01' WHERE id=?", (a,))   # Austritt am Vortag: lückenlos
        v = doppelte.vorschau(self.s, a, b)
        self.assertEqual(v['neu'], {'ov_id': ov, 'kv_joined': '2016-03-01', 'joined': '2015-01-01'})
        self.assertEqual(v['hinweise'], [])


class DoppelteHttpTests(unittest.TestCase):
    """Am laufenden Server: Doppelte sehen mit „Mitglieder lesen“, „kein Duplikat“ nur mit „Mitglieder ändern“."""
    setUpClass = classmethod(test_server.ServerHttpTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerHttpTests.tearDownClass.__func__)
    call = test_server.ServerHttpTests.call
    setUp, login, get, post, person = (getattr(test_rollen.RollenHttpTests, name) for name in ('setUp', 'login', 'get', 'post', 'person'))

    def test_rechte(self):
        officer = self.person('Mitglieder-Person', 'Landesmitgliederbeauftragter')
        member = self.get(officer, '/api/data?year=2026')[1]['members'][0]
        self.assertEqual(self.post(officer, '/api/member', {**BASE, 'number': 'M-2', 'first_name': 'Erika', 'last_name': 'Muster',
                                                            'birth': member['birth']})[0], 200)
        status, r = self.get(officer, '/api/doppelte')
        self.assertEqual((status, len(r['gruppen'])), (200, 1))
        self.assertIn('gleicher Name und Geburtsdatum', r['gruppen'][0]['gruende'])
        reader = self.person('leitung', 'Landesgeschäftsführer')
        self.assertEqual(self.get(reader, '/api/doppelte')[0], 200)
        self.assertEqual(self.post(reader, '/api/kein-duplikat', {'ids': r['gruppen'][0]['ids']})[0], 403)
        self.assertEqual(self.post(officer, '/api/kein-duplikat', {'ids': 'x'})[0], 400)
        self.assertEqual(self.post(officer, '/api/kein-duplikat', {'ids': r['gruppen'][0]['ids']})[0], 200)
        self.assertEqual(self.get(officer, '/api/doppelte')[1]['gruppen'], [])
        admin = self.person('technik', 'Administrator')                                               # keine Mitgliederdaten
        self.assertEqual(self.get(admin, '/api/doppelte')[0], 403)

    def test_zusammenfuehren(self):
        officer = self.person('Mitglieder-Person', 'Landesmitgliederbeauftragter')
        ids = [self.post(officer, '/api/member', {**BASE, 'number': n, 'first_name': 'Lea', 'last_name': 'Zweifach', 'email': mail})[1]['id']
               for n, mail in (('M-10', None), ('M-11', 'lea@example.org'))]
        pair = {'keep': ids[0], 'drop': ids[1]}
        reader = self.person('leitung', 'Landesgeschäftsführer')
        self.assertEqual(self.post(reader, '/api/zusammenfuehren-vorschau', pair)[0], 200)
        self.assertEqual(self.post(reader, '/api/zusammenfuehren', pair)[0], 403)
        self.assertEqual(self.post(self.person('technik', 'Administrator'), '/api/zusammenfuehren-vorschau', pair)[0], 403)
        status, v = self.post(officer, '/api/zusammenfuehren-vorschau', pair)
        self.assertEqual((status, v['ergaenzt'], 'neu' in v), (200, ['E-Mail privat'], False))   # Werte (auch Notizen) nie in der Vorschau
        self.assertEqual(self.post(officer, '/api/zusammenfuehren', {'keep': ids[0], 'drop': ids[0]})[0], 400)
        status, r = self.post(officer, '/api/zusammenfuehren', pair)
        self.assertEqual(status, 200)
        self.assertTrue(r['backup'].startswith('vor-zusammenfuehren'))
        members = {m['id']: m for m in self.get(officer, '/api/data?year=2026')[1]['members']}
        self.assertEqual((ids[1] in members, members[ids[0]]['email']), (False, 'lea@example.org'))

    def test_hindernis_ohne_sicherung_und_buchhaltung_nur_mit_recht(self):
        import app
        officer = self.person('Mitglieder-Person', 'Landesmitgliederbeauftragter')
        ids = [self.post(officer, '/api/member', {**BASE, 'number': n, 'first_name': 'Ida', 'last_name': 'Dreifach'})[1]['id']
               for n in ('M-20', 'M-21')]
        store = Store(app.DB)
        try:
            with store.db:   # Testdaten direkt: Festlegung für das entfallende Mitglied
                store.db.execute("INSERT INTO dues_targets(member_id,year,amount,reason,created) VALUES(?,2026,0,'Test','2026-01-01')", (ids[1],))
        finally:
            store.db.close()
        folder = app.backups_dir() / 'Vor Änderungen'
        count = lambda: len(list(folder.glob('vor-zusammenfuehren-*'))) if folder.is_dir() else 0
        before, pair = count(), {'keep': ids[0], 'drop': ids[1]}
        status, r = self.post(officer, '/api/zusammenfuehren', pair)
        self.assertEqual((status, count()), (400, before))                                  # Hindernis: keine Sicherung
        self.assertIn('Buchhaltung ändern', r['error'])
        both = self.person('Beides-Person', 'Landesmitgliederbeauftragter', 'Schatzmeister')
        self.assertEqual(self.post(both, '/api/zusammenfuehren', pair)[0], 200)
        self.assertEqual(count(), before + 1)


if __name__ == '__main__':
    unittest.main()
