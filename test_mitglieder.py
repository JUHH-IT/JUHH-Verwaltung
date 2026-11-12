"""Mitglieder: Namens- und Anschriftsfelder, Aufteilung alter Einträge, Import einer Mitgliederliste – nur erfundene Testdaten."""
import io
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import mitglieder as mg
from core import Store

KVS = {'LV', '5100', '5200', '5300', '5400', '5500', '5600', '5700', '5800'}
BASE = dict(number='M1', first_name='Erika', last_name='Muster', street='Musterweg 1', zip='20095', city='Hamburg',
            birth='2000-02-01', joined='2024-01-01', kv='5100')


class SplitTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(mg.split_name('Erika Muster'), ('Erika', 'Muster'))
        self.assertEqual(mg.split_name('  Anna  Lena von der Berg '), ('Anna Lena', 'von der Berg'))
        self.assertEqual(mg.split_name('Muster'), ('', 'Muster'))
        self.assertEqual(mg.split_name(''), ('', ''))

    def test_addresses(self):
        self.assertEqual(mg.split_address('Musterweg 1\n20095 Hamburg'), (None, 'Musterweg 1', '20095', 'Hamburg', None))
        self.assertEqual(mg.split_address('c/o Beispiel\nMusterweg 1\nD-20095 Hamburg'), ('c/o Beispiel', 'Musterweg 1', '20095', 'Hamburg', None))
        self.assertEqual(mg.split_address('Musterweg 1, 22301 Hamburg'), (None, 'Musterweg 1', '22301', 'Hamburg', None))
        self.assertEqual(mg.split_address('Ringstr. 3\n1010 Wien\nÖsterreich'), (None, 'Ringstr. 3', '1010', 'Wien', 'Österreich'))   # Land bleibt
        self.assertEqual(mg.split_address('Weg 1'), (None, 'Weg 1', None, None, None))
        self.assertEqual(mg.split_name('Muster, Erika'), ('Erika', 'Muster'))
        v = mg.values({**BASE, 'street': 'Ringstr. 3', 'zip': '1010', 'city': 'Wien', 'country': 'Österreich'}, KVS)
        self.assertEqual(v['address'], 'Ringstr. 3\n1010 Wien\nÖsterreich')
        self.assertIsNone(mg.values({**BASE, 'country': 'Deutschland'}, KVS)['country'])
        for domestic in ('DEU', 'GER', 'Bundesrepublik Deutschland', 'Hamburg', 'Schleswig-Holstein'):
            self.assertIsNone(mg.country(domestic), domestic)
        self.assertEqual(mg.split_address('Weg 1\n20095 Hamburg\nDeutschland')[4], None)

    def test_values(self):
        v = mg.values({**BASE, 'address_extra': 'c/o Beispiel', 'email': ' Erika@Example.org ', 'dues': '24'}, KVS)
        self.assertEqual((v['name'], v['address'], v['email'], v['dues']), ('Erika Muster', 'c/o Beispiel\nMusterweg 1\n20095 Hamburg', 'Erika@Example.org', 2400))
        legacy = mg.values({'number': 'M2', 'name': 'Max Beispiel', 'address': 'Weg 1', 'birth': '01.02.2000', 'joined': '2024-01-01', 'kv': '5200'}, KVS)
        self.assertEqual((legacy['first_name'], legacy['last_name'], legacy['street'], legacy['zip'], legacy['address']), ('Max', 'Beispiel', 'Weg 1', None, 'Weg 1'))
        for change, message in ((dict(zip=''), 'PLZ und Ort'), (dict(last_name=''), 'Nachname'), (dict(street=''), 'Straße'),
                                (dict(number=''), 'Mitgliedsnummer'), (dict(kv='LV'), 'Kreisverband'), (dict(birth='2025-01-01'), 'Geburtsdatum'),
                                (dict(joined=''), 'Eintrittsdatum'), (dict(zip='2009!'), 'PLZ'), (dict(left_on='2023-01-01'), 'Austritt'),
                                (dict(birth='31.02.2000'), 'Datum nicht erkannt')):
            with self.assertRaises(ValueError, msg=change) as cm:
                mg.values({**BASE, **change}, KVS)
            self.assertIn(message, str(cm.exception))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 't.db'
        self.s = Store(self.path)

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_existing_members_are_split_once(self):
        self.s.db.close()
        db = sqlite3.connect(self.path)                                                  # Mitglied wie vor 0.22 (ohne neue Spalten befüllt)
        db.execute("INSERT INTO members(number,name,address,email,birth,joined,kv) VALUES('A1','Anna von Berg','Allee 5\n22301 Hamburg',NULL,'2001-01-01','2024-01-01','5600')")
        db.execute("INSERT INTO members(number,name,address,email,birth,joined,kv) VALUES('A2','Huber, Jo','Ringstr. 3\n1010 Wien\nÖsterreich',NULL,'2001-01-01','2024-01-01','5600')")
        db.commit()
        db.close()
        self.s = Store(self.path)
        m = self.s.rows("SELECT * FROM members WHERE number='A1'")[0]
        self.assertEqual((m['first_name'], m['last_name'], m['street'], m['zip'], m['city']), ('Anna', 'von Berg', 'Allee 5', '22301', 'Hamburg'))
        self.assertEqual((m['name'], m['address']), ('Anna von Berg', 'Allee 5\n22301 Hamburg'))   # alte Felder unverändert
        jo = self.s.rows("SELECT * FROM members WHERE number='A2'")[0]
        self.assertEqual((jo['first_name'], jo['last_name'], jo['country']), ('Jo', 'Huber', 'Österreich'))
        self.s.update_member(jo['id'], {k: jo[k] for k in mg.FIELDS} | {'email': 'jo@example.org'}, 'Test')
        self.assertEqual(self.s.rows("SELECT address FROM members WHERE number='A2'")[0]['address'], 'Ringstr. 3\n1010 Wien\nÖsterreich')

    def test_create_update_and_sorting(self):
        a = self.s.member({**BASE, 'number': 'M1', 'first_name': 'Zoe', 'last_name': 'Adler'}, 'Test')
        self.s.member({**BASE, 'number': 'M2', 'first_name': 'Anton', 'last_name': 'Zander'}, 'Test')
        self.assertEqual([m['last_name'] for m in self.s.snapshot(2026)['members']], ['Adler', 'Zander'])
        self.s.update_member(a, {**BASE, 'number': 'M1', 'first_name': 'Zoe', 'last_name': 'Adler', 'city': 'Norderstedt', 'zip': '22846'}, 'Test')
        m = self.s.rows('SELECT * FROM members WHERE id=?', (a,))[0]
        self.assertEqual(m['address'], 'Musterweg 1\n22846 Norderstedt')
        detail = self.s.rows("SELECT detail FROM audit WHERE action='Mitglied geändert'")[0]['detail']
        self.assertIn('city', detail)


def csv_bytes(text, encoding='utf-8'):
    return text.encode(encoding)


class ImportTests(StoreTests):
    def test_csv_preview_and_apply(self):
        self.s.member({**BASE, 'number': 'M1', 'email': 'alt@example.org'}, 'Test')
        self.s.member({**BASE, 'number': 'M9', 'first_name': 'Karl', 'last_name': 'Beispiel'}, 'Test')
        raw = csv_bytes('Mitgliederliste JU Hamburg (Testdaten)\n\n'
                        'Mitgliedsnummer;Vorname;Name;Straße;Hausnr.;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband\n'
                        'M1;Erika;Muster;Musterweg;1;20095;Hamburg;01.02.2000;01.01.2024;Altona/Elbvororte\n'          # unverändert
                        'M9;Karl;Beispiel;Neue Straße;7;22301;Hamburg;01.02.2000;01.01.2024;KV Hamburg-Nord\n'         # Umzug + KV
                        'M3;Lea;Probe;Weg;2;21029;Hamburg;15.06.2003;01.03.2025;Bergedorf\n'                          # neu
                        'M4;Ole;;Weg;3;21029;Hamburg;15.06.2003;01.03.2025;Bergedorf\n'                              # Fehler: Nachname
                        'M3;Lea;Probe;Weg;2;21029;Hamburg;15.06.2003;01.03.2025;Bergedorf\n'                          # doppelt
                        'M5;Erika;Muster;Weg;9;20095;Hamburg;01.02.2000;01.01.2025;Mondstadt\n', 'cp1252')            # KV unbekannt
        p = mg.preview(self.s, raw, 'liste.csv')
        actions = {r['row_no']: (r['action'], r['error'] or '') for r in p['rows']}
        self.assertEqual(actions[4][0], 'same')
        self.assertEqual(actions[5][0], 'update')
        self.assertEqual(actions[6][0], 'new')
        self.assertIn('Nachname', actions[7][1])
        self.assertIn('doppelt', actions[8][1])
        self.assertIn('Kreisverband nicht erkannt', actions[9][1])
        self.assertEqual(p['counts'], {'new': 1, 'update': 1, 'same': 1, 'error': 3})
        update = [r for r in p['rows'] if r['row_no'] == 5][0]
        self.assertEqual(set(update['changes']), {'Straße', 'PLZ', 'Kreisverband'})
        self.assertEqual(update['values']['kv'], '5600')
        with self.assertRaises(ValueError):
            mg.apply(self.s, p, [4], 'Test')                                               # unverändert: nichts zu übernehmen
        result = mg.apply(self.s, p, [5, 6], 'Test')
        self.assertEqual(result, {'new': 1, 'updated': 1, 'ov_created': 0})
        m1 = self.s.rows("SELECT * FROM members WHERE number='M1'")[0]
        self.assertEqual(m1['email'], 'alt@example.org')                                   # Spalte fehlt → Wert bleibt
        m9 = self.s.rows("SELECT * FROM members WHERE number='M9'")[0]
        self.assertEqual((m9['street'], m9['kv'], m9['address']), ('Neue Straße 7', '5600', 'Neue Straße 7\n22301 Hamburg'))
        self.assertEqual(self.s.rows("SELECT birth,joined FROM members WHERE number='M3'")[0], {'birth': '2003-06-15', 'joined': '2025-03-01'})
        audit = self.s.rows("SELECT detail FROM audit WHERE action='Mitgliederliste importiert'")[0]['detail']
        self.assertNotIn('Lea', audit)                                                     # keine Namen im Protokoll
        self.assertIn('"neu": 1', audit)

    def test_xlsx_full_name_and_twin(self):
        import openpyxl
        self.s.member({**BASE, 'number': 'M1'}, 'Test')
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(['Mitglied-Nr', 'Name', 'Anschrift', 'PLZ', 'Ort', 'Geburtsdatum', 'Eintrittsdatum', 'KV', 'E-Mail', 'Jahresbeitrag'])
        sheet.append([1002, 'Erika Muster', 'Musterweg 1', 20095, 'Hamburg', datetime(2000, 2, 1), datetime(2025, 5, 1), 5300, 'e@example.org', 24])
        sheet.append([1003, 'Jan de Vries', 'Elbchaussee 3', '22763', 'Hamburg', '03.04.2004', '2025-05-01', 'Eimsbüttel', '', None])
        buf = io.BytesIO()
        book.save(buf)
        p = mg.preview(self.s, buf.getvalue(), 'liste.xlsx')
        first, second = p['rows']
        self.assertEqual((first['action'], first['number'], first['values']['kv'], first['values']['dues'], first['values']['zip']), ('new', '1002', '5300', 2400, '20095'))
        self.assertIn('Gleiche Person', first['warning'])
        self.assertEqual((second['values']['first_name'], second['values']['last_name'], second['values']['birth']), ('Jan', 'de Vries', '2004-04-03'))
        self.assertIn('Name', p['columns'])

    def test_leading_zeros_and_cleared_columns(self):
        self.s.member({**BASE, 'number': '0012345', 'email': 'alt@example.org', 'address_extra': 'c/o Alt'}, 'Test')
        raw = csv_bytes('Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband;E-Mail;Adresszusatz\n'
                        '12345;Erika;Muster;Musterweg 1;20095;Hamburg;01.02.2000;01.01.2024;Altona;;\n')
        row = mg.preview(self.s, raw, 'liste.csv')['rows'][0]
        self.assertEqual((row['action'], row['number']), ('update', '0012345'))             # Excel ohne führende Nullen: dasselbe Mitglied
        self.assertIn('führende Nullen', row['warning'])
        self.assertEqual((row['values']['email'], row['values']['address_extra']), (None, None))   # vorhandene Spalte leer = leeren
        self.assertEqual(set(row['changes']), {'E-Mail privat', 'Adresszusatz (Name 2)'})

    def test_similar_number_of_other_person_is_new(self):
        self.s.member({**BASE, 'number': '0012345', 'last_name': 'Andere'}, 'Test')
        raw = csv_bytes('Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband\n'
                        '12345;Erika;Muster;Musterweg 1;20095;Hamburg;01.02.2000;01.01.2024;Altona\n')
        row = mg.preview(self.s, raw, 'liste.csv')['rows'][0]
        self.assertEqual((row['action'], row['number']), ('new', '12345'))
        self.assertIn('anderen Mitglied', row['warning'])

    def test_cdu_export(self):
        """Export der CDU-Mitgliederverwaltung (Spaltenköpfe wie finanzen_mitglieder_gesamt.csv, Windows-1252, CRLF) – erfundene Personen."""
        self.s.member({**BASE, 'number': 'A100', 'first_name': 'Alt', 'last_name': 'Mitglied'}, 'Test')    # fehlt in der Datei
        head = ('LVKV_Name;Ortsverband;Ortsbezeichnung;Mitgliedsnummer;Anrede;Briefanrede_1;Titel;Adelname;Vorname;Nachname;Strasse;PLZ;PLZ_Ort;'
                'Nationalität;Geschlecht;Geburtstag;Geburtsmonat;Geburtsjahr;Jahre;Vereine;Verein_Eintritt;KV_Eintritt;Jubiläumjahre;Beitragsfrei;'
                'Gastmitglied;Verein;Kontostand;Jahresbeitrag;Zahler_Zahlungsart;Telefon_Privat;Handy_Privat;Email_Privat')
        rows = ['Hamburg-Altona;12;Testort;T0000000001;Herrn;Sehr geehrter Herr Probe;Dr.;von;Max;Probe;Teststraße 1;21999;21999 Teststadt;D;M;'
                '17;8;2002;24;CDU,JU;14.09.22;03.10.22;3;N;N;JU;-30;30;R;040 123;0170 123;max@example.org',
                'Hamburg-Nord;3;Nordort;T0000000002;Frau;Liebe Lea;;;Lea;Gast;Weg 2;22301;22301 Hamburg;D;W;1;12;2005;20;JU;01.07.25;01.07.25;1;N;J;JU;0;;'
                ';;;',
                'Hamburg-Wandsbek;7;Ostort;T0000000003;Herrn;;;;Ben;Frei;Allee 3;22041;22041 Hamburg;D;M;30;6;1999;27;JU;15.01.20;15.01.20;6;J;N;JU;0;0;L;;;',
                'Hamburg-Mitte;1;Mitteort;T0000000004;Herrn;;;;Udo;Ander;Gasse 4;20095;20095 Hamburg;D;M;2;2;2000;26;CDU;01.01.20;01.01.20;6;N;N;CDU;0;30;R;;;']
        raw = ('\r\n'.join([head] + rows) + '\r\n').encode('cp1252')
        p = mg.preview(self.s, raw, 'finanzen_mitglieder_gesamt.csv')
        self.assertEqual(p['counts'], {'new': 4, 'update': 0, 'same': 0, 'error': 0})
        self.assertEqual(p['guests'], 1)
        self.assertEqual([m['number'] for m in p['missing']], ['A100'])                     # nicht mehr in der Liste: Austritt prüfen
        self.assertIn('nicht Junge Union', [r for r in p['rows'] if r['number'] == 'T0000000004'][0]['warning'])
        max_ = [r for r in p['rows'] if r['number'] == 'T0000000001'][0]['values']
        self.assertEqual((max_['kv'], max_['birth'], max_['joined'], max_['kv_joined'], max_['city'], max_['zip'], max_['dues']),
                         ('5100', '2002-08-17', '2022-09-14', '2022-10-03', 'Teststadt', '21999', 3000))
        self.assertEqual((max_['name'], max_['title'], max_['name_prefix'], max_['salutation'], max_['letter_salutation']),
                         ('Max von Probe', 'Dr.', 'von', 'Herrn', 'Sehr geehrter Herr Probe'))
        self.assertEqual((max_['gender'], max_['nationality'], max_['ov_id'], max_['clubs'], max_['club']), ('M', 'D', None, 'CDU,JU', 'JU'))
        max_row = [r for r in p['rows'] if r['number'] == 'T0000000001'][0]
        self.assertEqual((max_row['new_ov'], max_row['ov']), ({'kv': '5100', 'number': 12, 'name': 'Testort'}, '12 Testort (neu)'))   # Ortsverband neu
        self.assertEqual(len(p['new_ovs']), 4)
        self.assertEqual((max_['phone'], max_['mobile'], max_['email'], max_['payment_method'], max_['external_balance']),
                         ('040 123', '0170 123', 'max@example.org', 'R', -3000))
        self.assertEqual((max_['guest'], max_['dues_free']), (0, 0))
        lea = [r for r in p['rows'] if r['number'] == 'T0000000002'][0]['values']
        self.assertEqual((lea['guest'], lea['dues'], lea['kv']), (1, None, '5600'))
        ben = [r for r in p['rows'] if r['number'] == 'T0000000003'][0]['values']
        self.assertEqual((ben['dues_free'], ben['dues']), (1, 0))
        self.assertEqual(mg.apply(self.s, p, [2, 3, 4], 'Test')['ov_created'], 3)
        stored = {m['number']: m for m in self.s.rows('SELECT * FROM members')}
        ov = self.s.rows('SELECT * FROM ortsverbaende WHERE id=?', (stored['T0000000001']['ov_id'],))[0]
        self.assertEqual((ov['kv'], ov['number'], ov['name']), ('5100', 12, 'Testort'))
        self.assertEqual(stored['T0000000001']['address'], 'Teststraße 1\n21999 Teststadt')
        self.assertIsNone(self.s.dues_calc(stored['T0000000002'], 2026))                    # Gastmitglied: kein Beitrag
        self.assertIsNone(self.s.dues_calc(stored['T0000000003'], 2026))                    # beitragsfrei: keine Beitragsrechnung
        # Bearbeiten im Programm ohne die Zusatzfelder: sie bleiben erhalten
        self.s.update_member(stored['T0000000001']['id'], {**{k: stored['T0000000001'][k] for k in mg.FIELDS if k not in mg.EXTRA},
                                                          'city': 'Neustadt'}, 'Test')
        again = self.s.rows("SELECT city,mobile,title FROM members WHERE number='T0000000001'")[0]
        self.assertEqual(again, {'city': 'Neustadt', 'mobile': '0170 123', 'title': 'Dr.'})
        # erneuter Import derselben Datei: unverändert
        self.assertEqual(mg.preview(self.s, raw, 'x.csv')['counts']['same'], 2)
        with self.assertRaises(ValueError):
            mg.values({**BASE, 'guest': 'vielleicht'}, KVS)

    def test_dues_history_name_prefix_and_checks(self):
        """Prüfbefunde 0.24: Jahresbeitrag nicht rückwirkend, Namenszusatz nicht doppelt, Hinweise gesammelt, PLZ aus PLZ_Ort, Datumsgrenze."""
        from datetime import date
        year = date.today().year
        self.s.save_settings({'age_basis': 'jan1'}, 'Test')
        head = 'Mitgliedsnummer;Vorname;Nachname;Adelname;Strasse;PLZ;PLZ_Ort;Geburtsdatum;Verein_Eintritt;LVKV_Name;Jahresbeitrag;Verein\n'
        raw = (head + f'X1;Anna;von Berg;von;Weg 1;;22765 Hamburg;01.06.{year - 23};01.01.20;Hamburg-Altona;30;CDU\n').encode('cp1252')
        p = mg.preview(self.s, raw, 'x.csv')
        row = p['rows'][0]
        self.assertEqual((row['action'], row['values']['zip'], row['values']['name']), ('new', '22765', 'Anna von Berg'))
        self.assertIn('nicht Junge Union', row['warning'])
        mg.apply(self.s, p, [2], 'Test')
        m = self.s.rows("SELECT * FROM members WHERE number='X1'")[0]
        # Jahresbeitrag der Datei gilt ab diesem Jahr; das Vorjahr bleibt bei der Staffel (Alter 21 → 20 €)
        self.assertEqual((self.s.dues_calc(m, year)['amount'], self.s.dues_calc(m, year - 1)['amount']), (3000, 2000))
        self.s.update_member(m['id'], {**{k: m[k] for k in mg.FIELDS}, 'dues': '25'}, 'Test')
        m = self.s.rows("SELECT * FROM members WHERE number='X1'")[0]
        self.assertEqual((self.s.dues_calc(m, year)['amount'], self.s.dues_calc(m, year - 1)['amount']), (2500, 2000))
        # Wert vor 0.24 ohne Verlauf gilt für alle Jahre, bis er sich ändert
        legacy = self.s.member({**BASE, 'number': 'L1', 'dues': '15'}, 'Test')
        with self.s.db:
            self.s.db.execute('DELETE FROM dues_rates WHERE member_id=?', (legacy,))
        lm = self.s.rows('SELECT * FROM members WHERE id=?', (legacy,))[0]
        self.assertEqual(self.s.dues_calc(lm, year - 1)['amount'], 1500)
        self.s.update_member(legacy, {**{k: lm[k] for k in mg.FIELDS}, 'dues': '30'}, 'Test')
        lm = self.s.rows('SELECT * FROM members WHERE id=?', (legacy,))[0]
        self.assertEqual((self.s.dues_calc(lm, year - 1)['amount'], self.s.dues_calc(lm, year)['amount']), (1500, 3000))
        # älterer Aufrufer mit „name“: Zusatz nicht doppelt
        self.s.update_member(m['id'], {**{k: m[k] for k in mg.FIELDS if k not in ('first_name', 'last_name')}, 'name': 'Anna von Berg'}, 'Test')
        self.assertEqual(self.s.rows('SELECT name FROM members WHERE id=?', (m['id'],))[0]['name'], 'Anna von Berg')
        with self.assertRaises(ValueError) as cm:
            mg.values({**BASE, 'joined': '01.01.68'}, KVS)                                  # zweistellig → 2068
        self.assertIn('Zukunft', str(cm.exception))
        with self.assertRaises(ValueError):
            mg.values({**BASE, 'street': 'x' * 300}, KVS)

    def test_header_not_found_and_limits(self):
        with self.assertRaises(ValueError) as cm:
            mg.preview(self.s, b'a;b;c\n1;2;3\n', 'x.csv')
        self.assertIn('Kopfzeile', str(cm.exception))
        self.assertEqual(mg.kv_code('5100', KVS), '5100')
        self.assertEqual(mg.kv_code('Schüler Union', KVS), '5800')
        self.assertEqual(mg.kv_code('Hamburg-Mitte', KVS), '5500')
        self.assertIsNone(mg.kv_code('', KVS))
        self.assertEqual(mg.map_header(['Vorname', 'Name', 'PLZ'])['last_name'], 1)       # „Name“ neben „Vorname“ = Nachname


if __name__ == '__main__':
    unittest.main()
