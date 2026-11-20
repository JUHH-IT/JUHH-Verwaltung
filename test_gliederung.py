"""Gliederung (Kreisverbände mit Nummer der Mitgliederverwaltung, Ortsverbände), Mitglied mit Ortsverband, Import im Format des
Exports der CDU-Mitgliederverwaltung (alle Spalten) und kennwortgeschützte Excel-Dateien – nur erfundene Testdaten."""
import io
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import gliederung
import mitglieder as mg
from core import Store

try:
    import msoffcrypto   # noqa: F401
    from msoffcrypto.format.ooxml import OOXMLFile
except ImportError:      # z. B. Python 3.10: Paket erst ab 3.11
    OOXMLFile = None

BASE = dict(number='M1', first_name='Erika', last_name='Muster', street='Musterweg 1', zip='20095', city='Hamburg',
            birth='2000-02-01', joined='2024-01-01', kv='5100')
# Spalten wie im Export der CDU-Mitgliederverwaltung („ultimate alles“), Reihenfolge der Datei
HEAD = ['LVKV_Nummer', 'LVKV_Name', 'Ortsverband', 'Ortsbezeichnung', 'Mitgliedsnummer', 'Anrede', 'Briefanrede_1', 'Briefanrede_2', 'Titel',
        'Adelname', 'Vorname', 'Nachname', 'Zusatzname', 'Geburtsdatum', 'Jahre', 'Geschlecht', 'Nationalität', 'Verein_Eintritt', 'KV_Eintritt',
        'Jubiläumjahre', 'Vereine', 'Zahler_Zahlungsart', 'Jahresbeitrag', 'Kontostand', 'Postausschluss', 'Strasse', 'Name_2', 'Name_3', 'PLZ',
        'Ort', 'Land', 'Telefon_Privat', 'Telefon_Dienstlich', 'Handy_Privat', 'Handy_Dienstlich', 'Email_Privat', 'Email_Dienstlich']
PASSWORD = 'Test-Kennwort-123'   # erfunden


def person(kv_no, kv_name, ov_no, ov_name, number, first, last, **extra):
    row = dict(zip(HEAD, [kv_no, kv_name, ov_no, ov_name, number, 'Herrn', f'Lieber {first}', f'Hallo {first}', None, None, first, last, None,
                          datetime(2002, 8, 17), 24, 'M', 'D', datetime(2019, 9, 14), datetime(2019, 10, 3), 6, 'JU,CDU', None, None, 0, 'N',
                          'Teststraße 1', None, None, 21999, 'Teststadt', 'D', None, None, '+49170000000', None, f'{first.lower()}@example.org', None]))
    row.update(extra)
    return [row[h] for h in HEAD]


def xlsx(rows, head=HEAD):
    import openpyxl
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(head)
    for r in rows:
        sheet.append(r)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def encrypt(raw, password=PASSWORD):
    out = io.BytesIO()
    OOXMLFile(io.BytesIO(raw)).encrypt(password, out)
    return out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 't.db'
        self.s = Store(self.path)

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()


class GliederungTests(Base):
    def test_kv_numbers(self):
        kvs = {k['code']: k['cdu_number'] for k in gliederung.kvs(self.s)}
        self.assertEqual((kvs['5100'], kvs['5700'], kvs['LV'], kvs['5800']), ('701', '707', '', ''))
        self.assertEqual(gliederung.kv_by_number(self.s)['706'], '5600')
        with self.assertRaises(ValueError):
            gliederung.set_cdu_numbers(self.s, {'5100': '702'}, 'Test')                 # doppelt
        with self.assertRaises(ValueError):
            gliederung.set_cdu_numbers(self.s, {'5100': '7a1'}, 'Test')
        with self.assertRaises(ValueError):
            gliederung.set_cdu_numbers(self.s, {'LV': '700'}, 'Test')
        self.assertEqual(gliederung.set_cdu_numbers(self.s, {'5800': '799', '5100': '701'}, 'Test'), 1)
        self.assertEqual(gliederung.kv_by_number(self.s)['799'], '5800')
        self.assertEqual(gliederung.resolve_kv(gliederung.kv_by_number(self.s), {'5100', '5600'}, '701', 'Hamburg-Altona'), ('5100', ''))
        code, note = gliederung.resolve_kv(gliederung.kv_by_number(self.s), {'5100', '5600'}, '790', 'Hamburg-Nord')
        self.assertEqual(code, '5600')
        self.assertIn('unbekannt', note)
        with self.assertRaises(ValueError) as cm:                                          # Nummer und Name widersprechen sich
            gliederung.resolve_kv(gliederung.kv_by_number(self.s), {'5100', '5600'}, '701', 'Hamburg-Nord')
        self.assertIn('passen nicht zusammen', str(cm.exception))
        for odd in ('²', '٧٠١'):                                                           # nur Ziffern 0–9
            with self.assertRaises(ValueError):
                gliederung.set_cdu_numbers(self.s, {'5800': odd}, 'Test')
            with self.assertRaises(ValueError):
                gliederung.save_ov(self.s, {'kv': '5100', 'number': odd, 'name': 'X'}, 'Test')

    def test_ortsverband_crud(self):
        a = gliederung.save_ov(self.s, {'kv': '5100', 'number': '12', 'name': ' Testdorf  Nord '}, 'Test')
        zero = gliederung.save_ov(self.s, {'kv': '5100', 'number': '', 'name': 'Kreisverband'}, 'Test')
        gliederung.save_ov(self.s, {'kv': '5200', 'number': '12', 'name': 'Anderer KV'}, 'Test')    # gleiche Nummer, anderer KV: erlaubt
        for data, message in (({'kv': '5100', 'number': '12', 'name': 'X'}, 'schon den Ortsverband 12'),
                              ({'kv': '5100', 'number': '0', 'name': 'kreisverband'}, 'ohne Nummer'),
                              ({'kv': 'LV', 'number': '1', 'name': 'X'}, 'Kreisverband'),
                              ({'kv': '5100', 'number': '1a', 'name': 'X'}, 'Nummer'),
                              ({'kv': '5100', 'number': '3', 'name': ''}, 'Name'),
                              ({'kv': '5100', 'number': '3', 'name': 'x' * 101}, 'höchstens')):
            with self.assertRaises(ValueError, msg=data) as cm:
                gliederung.save_ov(self.s, data, 'Test')
            self.assertIn(message, str(cm.exception))
        ovs = {o['id']: o for o in gliederung.ovs(self.s)}
        self.assertEqual((ovs[a]['name'], ovs[a]['number'], ovs[a]['members']), ('Testdorf Nord', 12, 0))
        self.assertEqual([o['id'] for o in gliederung.ovs(self.s) if o['kv'] == '5100'], [a, zero])   # mit Nummer zuerst
        m = self.s.member({**BASE, 'ov_id': a}, 'Test')
        with self.assertRaises(ValueError) as cm:
            gliederung.delete_ov(self.s, a, 'Test')
        self.assertIn('deaktivieren', str(cm.exception))
        with self.assertRaises(ValueError):
            gliederung.save_ov(self.s, {'kv': '5300', 'number': '12', 'name': 'Testdorf Nord'}, 'Test', a)   # KV mit Mitgliedern
        gliederung.save_ov(self.s, {'kv': '5100', 'number': '12', 'name': 'Testdorf'}, 'Test', a)          # umbenennen
        gliederung.set_active(self.s, a, False, 'Test')
        self.assertEqual(self.s.rows('SELECT ov_id FROM members WHERE id=?', (m,))[0]['ov_id'], a)          # Zuordnung bleibt
        with self.assertRaises(ValueError):
            gliederung.set_active(self.s, a, 'nein', 'Test')
        gliederung.delete_ov(self.s, zero, 'Test')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM ortsverbaende WHERE id=?', (zero,)).fetchone()[0], 0)
        actions = [r['action'] for r in self.s.rows('SELECT action FROM audit')]
        for action in ('Ortsverband angelegt', 'Ortsverband geändert', 'Ortsverband deaktiviert', 'Ortsverband gelöscht'):
            self.assertIn(action, actions)
        snap = self.s.snapshot(2026)
        self.assertEqual({o['id'] for o in snap['ovs']} >= {a}, True)
        self.assertEqual([k for k in snap['kv'] if k['code'] == '5300'][0]['cdu_number'], '703')

    def test_member_ortsverband_belongs_to_kv(self):
        a = gliederung.save_ov(self.s, {'kv': '5100', 'number': '11', 'name': 'Testort'}, 'Test')
        b = gliederung.save_ov(self.s, {'kv': '5600', 'number': '61', 'name': 'Nordort'}, 'Test')
        mid = self.s.member({**BASE, 'ov_id': str(a)}, 'Test')
        with self.assertRaises(ValueError) as cm:
            self.s.member({**BASE, 'number': 'M2', 'ov_id': b}, 'Test')
        self.assertIn('gehört nicht', str(cm.exception))
        with self.assertRaises(ValueError):
            self.s.member({**BASE, 'number': 'M3', 'ov_id': 9999}, 'Test')
        with self.assertRaises(ValueError):
            self.s.member({**BASE, 'number': 'M4', 'ov_id': True}, 'Test')
        off = gliederung.save_ov(self.s, {'kv': '5100', 'number': '19', 'name': 'Altort'}, 'Test')
        gliederung.set_active(self.s, off, False, 'Test')
        with self.assertRaises(ValueError) as cm:                                          # deaktiviert: nicht neu zuordnen
            self.s.member({**BASE, 'number': 'M5', 'ov_id': off}, 'Test')
        self.assertIn('deaktiviert', str(cm.exception))
        gliederung.set_active(self.s, off, True, 'Test')
        keep = self.s.member({**BASE, 'number': 'M6', 'ov_id': off}, 'Test')
        gliederung.set_active(self.s, off, False, 'Test')
        self.s.update_member(keep, {**BASE, 'number': 'M6', 'ov_id': off, 'city': 'Neustadt', 'zip': '21998'}, 'Test')   # bisheriger bleibt möglich
        with self.assertRaises(ValueError):
            self.s.update_member(mid, {**BASE, 'kv': '5600'}, 'Test')                   # KV gewechselt, alter OV passt nicht
        self.s.update_member(mid, {**BASE, 'kv': '5600', 'ov_id': b}, 'Test')
        self.s.update_member(mid, {**BASE, 'kv': '5600', 'city': 'Norderstedt', 'zip': '22846'}, 'Test')   # ov_id nicht mitgeschickt: bleibt
        self.assertEqual(self.s.rows('SELECT ov_id FROM members WHERE id=?', (mid,))[0]['ov_id'], b)
        self.s.update_member(mid, {**BASE, 'kv': '5600', 'ov_id': ''}, 'Test')            # leer = kein Ortsverband
        self.assertIsNone(self.s.rows('SELECT ov_id FROM members WHERE id=?', (mid,))[0]['ov_id'])

    def test_new_fields_and_address(self):
        v = mg.values({**BASE, 'address_extra': 'c/o Beispiel', 'address_extra2': 'Hinterhaus', 'no_mail': 'J', 'email_work': ' Erika@Firma.example ',
                       'phone_work': '040 1', 'mobile_work': '0170 2', 'letter_salutation2': 'Hallo Erika', 'name_suffix': 'jun.'}, {'5100'})
        self.assertEqual(v['address'], 'c/o Beispiel\nHinterhaus\nMusterweg 1\n20095 Hamburg')
        self.assertEqual((v['no_mail'], v['email_work'], v['name']), (1, 'Erika@Firma.example', 'Erika Muster'))
        with self.assertRaises(ValueError):
            mg.values({**BASE, 'email_work': 'kein-mail'}, {'5100'})
        mid = self.s.member({**BASE, 'phone_work': '040 1', 'email_work': 'e@firma.example'}, 'Test')
        member = [m for m in self.s.snapshot(2026)['members'] if m['id'] == mid][0]
        for private in ('phone_work', 'email_work', 'mobile_work', 'letter_salutation2', 'ov_name'):
            self.assertNotIn(private, member)                                             # erst beim Öffnen des Mitglieds
        self.assertIn('ov_id', member)

    def test_legacy_ortsverband_text_is_linked_once(self):
        self.s.member({**BASE, 'number': 'A1'}, 'Test')
        self.s.member({**BASE, 'number': 'A2'}, 'Test')
        self.s.member({**BASE, 'number': 'A3', 'kv': '5600'}, 'Test')
        with self.s.db:   # Datenbank wie aus 0.26: Ortsverband nur als Text, Übernahme noch nicht gelaufen
            self.s.db.execute("DELETE FROM settings WHERE key='ov_legacy_linked'")
            self.s.db.execute("UPDATE members SET ov_number='12',ov_name='Testort' WHERE number IN ('A1','A2')")
            self.s.db.execute("UPDATE members SET ov_number='0',ov_name='OV Schüler Union' WHERE number='A3'")
        self.s.db.close()
        self.s = Store(self.path)
        rows = {m['number']: m for m in self.s.rows('SELECT * FROM members')}
        self.assertEqual(rows['A1']['ov_id'], rows['A2']['ov_id'])
        self.assertEqual(rows['A1']['ov_name'], 'Testort')                                 # alter Text bleibt stehen (nichts geht verloren)
        ovs = {o['id']: o for o in gliederung.ovs(self.s)}
        self.assertEqual((ovs[rows['A1']['ov_id']]['number'], ovs[rows['A1']['ov_id']]['members']), (12, 2))
        self.assertEqual((ovs[rows['A3']['ov_id']]['kv'], ovs[rows['A3']['ov_id']]['name']), ('5600', 'OV Schüler Union'))
        self.assertEqual(gliederung.link_legacy(self.s.db), 0)                              # nur einmal
        self.s.update_member(rows['A1']['id'], {**BASE, 'number': 'A1', 'ov_id': ''}, 'Test')   # Ortsverband entfernt …
        self.s.db.close()
        self.s = Store(self.path)
        self.assertIsNone(self.s.rows("SELECT ov_id FROM members WHERE number='A1'")[0]['ov_id'])   # … und nicht wieder verknüpft


class MusterImportTests(Base):
    def test_cdu_export_all_columns(self):
        known = gliederung.save_ov(self.s, {'kv': '5300', 'number': '31', 'name': 'Testviertel'}, 'Test')
        off = gliederung.save_ov(self.s, {'kv': '5600', 'number': '63', 'name': 'Altviertel'}, 'Test')
        gliederung.set_active(self.s, off, False, 'Test')
        rows = [
            person(701, 'Hamburg-Altona', 12, 'Testdorf', 'T0000000001', 'Max', 'Probe', Titel='Dr.', Adelname='von', Zusatzname='jun.',
                   Name_2='c/o Beispiel', Name_3='3. OG', Telefon_Dienstlich='040 999', Handy_Dienstlich='+49171000000',
                   Email_Dienstlich='max@firma.example', Postausschluss='J', Jahresbeitrag=30, Kontostand=-30, Zahler_Zahlungsart='R'),
            person(702, 'Hamburg-Bergedorf', 0, 'Kreisverband', 'T0000000002', 'Lea', 'Test', PLZ=1067, Ort='Dresden'),
            person(703, 'Hamburg-Eimsbüttel', 31, 'Testviertel-Süd', 'T0000000003', 'Ben', 'Beispiel'),
            person(706, 'Hamburg-Nord', 63, 'Altviertel', 'T0000000004', 'Ida', 'Muster'),
            person(707, 'Hamburg-Wandsbek', 0, 'OV Schüler Union', 'T0000000005', 'Tom', 'Schüler', Land='A', PLZ=1010, Ort='Wien'),
            person(707, 'Hamburg-Wandsbek', 0, 'OV Schüler Union', 'T0000000006', 'Pia', 'Schülerin'),
            person(799, 'Mondstadt', 1, 'Nirgendwo', 'T0000000007', 'Udo', 'Fehler'),
        ]
        p = mg.preview(self.s, xlsx(rows), 'ultimate.xlsx')
        self.assertEqual(p['counts'], {'new': 6, 'update': 0, 'same': 0, 'error': 1})
        for label in ('Kreisverband (Nr.)', 'Ortsverband (Nr.)', 'Ortsbezeichnung', 'Briefanrede 2', 'Zusatzname', 'Adresszusatz 2 (Name 3)',
                      'Telefon dienstlich', 'Handy dienstlich', 'E-Mail dienstlich', 'Postausschluss'):
            self.assertIn(label, p['columns'])
        self.assertNotIn('Jahre', ' '.join(p['columns']).split(', '))
        by = {r['number']: r for r in p['rows']}
        mx = by['T0000000001']['values']
        self.assertEqual((mx['kv'], mx['name'], mx['name_suffix'], mx['address_extra'], mx['address_extra2'], mx['no_mail']),
                         ('5100', 'Max von Probe', 'jun.', 'c/o Beispiel', '3. OG', 1))
        self.assertEqual(mx['address'], 'c/o Beispiel\n3. OG\nTeststraße 1\n21999 Teststadt')
        self.assertEqual((mx['phone_work'], mx['mobile_work'], mx['email_work'], mx['letter_salutation2'], mx['dues'], mx['external_balance']),
                         ('040 999', '+49171000000', 'max@firma.example', 'Hallo Max', 3000, -3000))
        self.assertEqual((mx['birth'], mx['joined'], mx['kv_joined'], mx['clubs'], mx['country']), ('2002-08-17', '2019-09-14', '2019-10-03', 'JU,CDU', None))
        self.assertEqual(by['T0000000001']['ov'], '12 Testdorf (neu)')
        self.assertEqual(by['T0000000002']['values']['zip'], '01067')                       # Excel-Zahl ohne führende Null
        self.assertEqual(by['T0000000005']['values']['zip'], '1010')                        # Ausland: PLZ bleibt
        self.assertEqual(by['T0000000003']['values']['ov_id'], known)                       # gleiche Nummer, anderer Name: vorhandener OV
        self.assertIn('heißt im Programm', by['T0000000003']['warning'])
        self.assertEqual(by['T0000000004']['values']['ov_id'], off)
        self.assertIn('deaktiviert', by['T0000000004']['warning'])
        self.assertIn('Kreisverband nicht erkannt', by['T0000000007']['error'])
        self.assertEqual([(o['kv'], o['number'], o['name'], o['rows']) for o in p['new_ovs']],
                         [('5100', 12, 'Testdorf', 1), ('5200', 0, 'Kreisverband', 1), ('5700', 0, 'OV Schüler Union', 2)])
        result = mg.apply(self.s, p, [2, 3, 6, 7], 'Test')                                  # Max, Lea, Tom, Pia
        self.assertEqual(result, {'new': 4, 'updated': 0, 'ov_created': 3})
        stored = {m['number']: m for m in self.s.rows('SELECT * FROM members')}
        self.assertEqual(stored['T0000000005']['ov_id'], stored['T0000000006']['ov_id'])   # ein OV für beide
        audit = self.s.rows("SELECT detail FROM audit WHERE action='Mitgliederliste importiert'")[0]['detail']
        self.assertNotIn('Max', audit)
        # zweiter Import derselben Datei: OV vorhanden, nichts geändert
        again = {r['number']: r for r in mg.preview(self.s, xlsx(rows), 'ultimate.xlsx')['rows']}
        self.assertEqual((again['T0000000001']['action'], again['T0000000001']['ov']), ('same', '12 Testdorf'))
        # Ortsverband in der Datei leer: Zuordnung wird gelöscht (mit Hinweis)
        rows[0][2], rows[0][3] = None, None
        cleared = [r for r in mg.preview(self.s, xlsx(rows[:1]), 'x.xlsx')['rows']][0]
        self.assertEqual((cleared['action'], cleared['values']['ov_id']), ('update', None))
        self.assertIn('Ortsverband', cleared['warning'])

    def test_kv_change_without_ov_columns_drops_ov(self):
        ov = gliederung.save_ov(self.s, {'kv': '5100', 'number': '11', 'name': 'Testort'}, 'Test')
        self.s.member({**BASE, 'ov_id': ov}, 'Test')
        raw = ('Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;LVKV_Nummer\n'
               'M1;Erika;Muster;Musterweg 1;20095;Hamburg;01.02.2000;01.01.2024;706\n').encode()
        row = mg.preview(self.s, raw, 'x.csv')['rows'][0]
        self.assertEqual((row['action'], row['values']['kv'], row['values']['ov_id']), ('update', '5600', None))
        self.assertIn('Ortsverband entfällt', row['warning'])

    def test_ov_errors(self):
        raw = ('Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;LVKV_Nummer;LVKV_Name;Ortsverband;Ortsbezeichnung;Email_Dienstlich\n'
               'M1;Erika;Muster;Weg 1;20095;Hamburg;01.02.2000;01.01.2024;701;;12a;Testort;\n'
               'M2;Erika;Muster;Weg 1;20095;Hamburg;01.02.2000;01.01.2024;701;;12;;\n'
               'M3;Erika;Muster;Weg 1;20095;Hamburg;01.02.2000;01.01.2024;701;;0;;kaputt@\n'
               'M4;Erika;Muster;Weg 1;20095;Hamburg;01.02.2000;01.01.2024;701;Hamburg-Nord;1;X;\n'
               'M5;Erika;Muster;Weg 1;20095;Hamburg;01.02.2000;01.01.2024;701;;²;X;\n').encode()
        rows = mg.preview(self.s, raw, 'x.csv')['rows']
        self.assertIn('Nummer des Ortsverbands', rows[0]['error'])
        self.assertIn('keinen Namen', rows[1]['error'])
        self.assertEqual((rows[2]['action'], rows[2]['values']['ov_id'], rows[2]['values']['email_work']), ('new', None, None))   # „0“ ohne Namen = kein OV
        self.assertIn('Dienstliche E-Mail', rows[2]['warning'])                            # ungültig: Hinweis statt Fehler
        self.assertIn('passen nicht zusammen', rows[3]['error'])                           # 701 ist nicht Nord
        self.assertIn('Nummer des Ortsverbands', rows[4]['error'])


class GliederungImportTests(Base):
    HEAD2 = ['LVKV_Nummer', 'LVKV_Name', 'Ortsverband', 'Ortsbezeichnung', 'Vereine']

    def test_preview_and_apply(self):
        gliederung.save_ov(self.s, {'kv': '5300', 'number': '31', 'name': 'Testviertel'}, 'Test')
        rows = [[701, 'Hamburg-Altona', 11, 'Testort', 'JU'], [701, 'Hamburg-Altona', 11, 'Testort', 'JU,CDU'],
                [701, 'Hamburg-Altona', 0, 'Kreisverband', 'JU'], [703, 'Hamburg-Eimsbüttel', 31, 'Testviertel-Süd', 'JU'],
                [706, 'Hamburg-Nord', 0, 'OV Schüler Union', 'SUD'], [706, 'Hamburg-Nord', 60, 'bitte löschen', 'JU'],
                [799, 'Mondstadt', 1, 'X', 'JU'], [701, 'Hamburg-Altona', 'x', 'Y', 'JU'], [701, 'Hamburg-Mitte', 3, 'Z', 'JU'],
                [701, 'Hamburg-Altona', '²', 'Y', 'JU']]
        p = gliederung.preview(self.s, xlsx(rows, self.HEAD2), 'struktur.xlsx')
        self.assertEqual(p['counts'], {'new': 4, 'same': 1, 'rows': 10, 'errors': 4})
        self.assertIn('passen nicht zusammen', ' '.join(e['error'] for e in p['errors']))   # Widerspruch sperrt die Zeile
        items = [(x['kv'], x['number'], x['name'], x['rows'], x['action']) for x in p['items']]
        self.assertEqual(items, [('5100', 11, 'Testort', 2, 'new'), ('5100', 0, 'Kreisverband', 1, 'new'),
                                 ('5300', 31, 'Testviertel', 1, 'same'), ('5600', 60, 'bitte löschen', 1, 'new'),
                                 ('5600', 0, 'OV Schüler Union', 1, 'new')])
        self.assertIn('heißt im Programm', [x for x in p['items'] if x['number'] == 31][0]['note'])
        self.assertEqual([k['code'] for k in p['kvs']], ['5100', '5300', '5600'])
        self.assertEqual(gliederung.apply_import(self.s, p, 'Test'), {'created': 4})
        again = gliederung.preview(self.s, xlsx(rows, self.HEAD2), 'struktur.xlsx')
        self.assertEqual(again['counts']['new'], 0)
        with self.assertRaises(ValueError):
            gliederung.apply_import(self.s, again, 'Test')
        with self.assertRaises(ValueError) as cm:
            gliederung.preview(self.s, b'a;b\n1;2\n', 'x.csv')
        self.assertIn('LVKV_Nummer', str(cm.exception))

    def test_member_file_also_works(self):
        p = gliederung.preview(self.s, xlsx([person(705, 'Hamburg-Mitte', 54, 'Zentrum-Test', 'T1', 'Max', 'Probe')]), 'm.xlsx')
        self.assertEqual([(x['kv'], x['number'], x['name']) for x in p['items']], [('5500', 54, 'Zentrum-Test')])


@unittest.skipIf(OOXMLFile is None, 'msoffcrypto-tool nicht installiert')
class PasswordTests(Base):
    def test_encrypted_member_file(self):
        raw = encrypt(xlsx([person(701, 'Hamburg-Altona', 11, 'Testort', 'T0000000001', 'Max', 'Probe')]))
        self.assertEqual(raw[:4], mg.OLE)
        with self.assertRaises(mg.PasswordNeeded) as cm:
            mg.preview(self.s, raw, 'geschuetzt.xlsx')
        self.assertEqual(cm.exception.state, 'required')
        for wrong in ('Irrtum-999', 'x' * 300):
            with self.assertRaises(mg.PasswordNeeded) as cm:
                mg.preview(self.s, raw, 'geschuetzt.xlsx', wrong)
            self.assertEqual(cm.exception.state, 'wrong')
            self.assertNotIn(wrong, str(cm.exception))
        p = mg.preview(self.s, raw, 'geschuetzt.xlsx', PASSWORD)
        self.assertEqual((p['counts']['new'], p['rows'][0]['values']['kv']), (1, '5100'))
        self.assertNotIn(PASSWORD, repr(p))                                                # Kennwort nicht in der Vorschau
        g = gliederung.preview(self.s, raw, 'geschuetzt.xlsx', PASSWORD)
        self.assertEqual(g['counts']['new'], 1)
        with self.assertRaises(mg.PasswordNeeded):
            gliederung.preview(self.s, raw, 'geschuetzt.xlsx')

    def test_unencrypted_files_untouched(self):
        plain = xlsx([person(701, 'Hamburg-Altona', 11, 'Testort', 'T1', 'Max', 'Probe')])
        self.assertIs(mg.unlock(plain, 'egal'), plain)
        self.assertEqual(mg.unlock(b'a;b\n', None), b'a;b\n')
        broken = mg.OLE + b'\x00' * 600                                                     # kaputte Datei: normale Fehlermeldung
        with self.assertRaises(ValueError) as cm:
            mg.preview(self.s, broken, 'x.xls')
        self.assertNotIsInstance(cm.exception, mg.PasswordNeeded)


if __name__ == '__main__':
    unittest.main()
