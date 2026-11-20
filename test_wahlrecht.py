"""Mitgliederstatus und Wahlberechtigung (ab 0.37, Satzung § 37) – nur erfundene Testdaten."""
import json
import tempfile
import unittest
import urllib.request
from datetime import date
from io import BytesIO
from unittest import mock
from pathlib import Path

import gliederung
import lastschrift
import mitglieder
import test_rollen   # Anmeldung und Personen am Testserver (nicht als Name importiert, sonst liefen dessen Tests hier noch einmal)
import test_server
import wahlrecht
from core import Store

IBAN = 'DE89 3704 0044 0532 0130 00'
BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg')
STICHTAG, DANACH = '2027-01-30', '2027-02-02'      # Beitragsstichtag (Samstag) und ein Tag in der Frist des LMA


class Heute(date):
    """Fester heutiger Tag für Prüfungen, die mit dem Datum rechnen (Einstellungen speichern)."""
    @classmethod
    def today(cls):
        return cls(2026, 11, 19)


def pdf_text(raw):
    from pypdf import PdfReader
    return [page.extract_text() for page in PdfReader(BytesIO(raw)).pages]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'iban': IBAN, 'age_basis': 'jan1', 'fo_start': '2025', 'creditor_id': 'DE98ZZZ09999999999'}, 'Test')
        with self.s.db:
            self.s.db.execute("INSERT INTO settings VALUES('data_start','2025-01-01')")
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, number, first, kv='5100', joined='2020-01-01', pays=(), **extra):
        """Erfundenes Mitglied (über 22: Beitrag 30 € im Jahr) mit Zahlungen von je 30 € an den Tagen `pays`."""
        mid = self.s.member({**BASE, 'number': number, 'first_name': first, 'last_name': extra.pop('last_name', 'Test'), 'kv': kv,
                             'birth': '1995-03-01', 'joined': joined, **extra}, 'Test')
        for day in pays:
            self.pay(mid, '30', day)
        return mid

    def pay(self, member, amount, day):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};Zahler;Beitrag;k{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'k{self.n}.csv'), [2], 'Test')
        bid = self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
        self.s.book(bid, [{'account': '8100', 'kv': 'LV', 'amount': amount, 'members': [{'member_id': member}]}], 'Test')

    def status(self, stichtag=STICHTAG, today=DANACH):
        return {x['number']: (x['status'], x['gruende']) for x in wahlrecht.auswertung(self.s, stichtag, today)['mitglieder']}


class StichtagTests(Base):
    def test_vorschlag(self):
        s = wahlrecht.stichtage({}, '2026-11-19')
        self.assertEqual((s['beitrag'], s['beitritt'], s['ab'], s['letzter']), ('2027-01-30', '2026-11-30', 2027, '2026-01-31'))
        self.assertEqual(date.fromisoformat(s['beitrag']).weekday(), 5)                       # 31.01.2027 ist ein Sonntag: Samstag zählt als Werktag
        for day in ('2027-01-30', '2027-01-31', '2027-02-06'):                                 # bleibt in der Frist des LMA (7 Tage) der aktuelle
            self.assertEqual(wahlrecht.stichtage({}, day)['beitrag'], '2027-01-30', day)
        s = wahlrecht.stichtage({}, '2027-02-07')
        self.assertEqual((s['beitrag'], s['beitritt'], s['letzter']), ('2028-01-31', '2027-11-30', '2027-01-30'))   # 31.01.2028 ist ein Montag
        self.assertEqual(s['vorschlag']['beitrag'], '2028-01-31')
        self.assertEqual(wahlrecht.stichtage({}, '2027-02-02')['vorschlag']['beitrag'], '2028-01-31')   # Vorschlag nie in der Vergangenheit (Frist läuft noch)
        self.assertEqual(wahlrecht.stichtage({}, '2027-01-30')['vorschlag']['beitrag'], '2027-01-30')   # am Stichtag selbst noch er
        self.assertEqual([wahlrecht.beitritt_vor(date.fromisoformat(d)).isoformat() for d in ('2027-01-30', '2027-11-30', '2027-12-01')],
                         ['2026-11-30', '2026-11-30', '2027-11-30'])                            # der 30.11. liegt immer vor dem Stichtag, nie auf ihm
        self.assertEqual([wahlrecht.letzter_werktag_januar(y).isoformat() for y in (2026, 2029, 2032, 2038)],
                         ['2026-01-31', '2029-01-31', '2032-01-31', '2038-01-30'])              # Samstag bleibt, Sonntag weicht auf Samstag

    @mock.patch.object(wahlrecht, 'date', Heute)
    def test_einstellungen(self):
        self.assertEqual((self.s.config()['wahlrecht_ab'], self.s.config()['wahl_beitragsstichtag']), ('2027', ''))
        self.s.save_settings({'wahl_beitragsstichtag': '29.01.2027'}, 'Test')                  # frei überschreibbar
        s = wahlrecht.stichtage(self.s.config(), '2026-11-19')
        self.assertEqual((s['beitrag'], s['beitritt'], s['vorschlag']['beitrag']), ('2027-01-29', '2026-11-30', '2027-01-30'))
        self.s.save_settings({'wahl_beitrittsstichtag': '2026-11-15', 'wahlrecht_ab': '2028'}, 'Test')
        s = wahlrecht.stichtage(self.s.config(), '2026-11-19')
        self.assertEqual((s['beitritt'], s['ab']), ('2026-11-15', 2028))
        for bad in ({'wahl_beitrittsstichtag': '2027-01-29'}, {'wahl_beitrittsstichtag': '2027-03-01'}, {'wahl_beitragsstichtag': 'bald'},
                    {'wahlrecht_ab': '27'}, {'wahlrecht_ab': ''}, {'wahlrecht_ab': '2025'}, {'wahlrecht_ab': '2101'}, {'wahlrecht_ab': '20x7'}):
            with self.assertRaises(ValueError, msg=bad):
                self.s.save_settings(bad, 'Test')
        self.s.save_settings({'wahlrecht_ab': '2026'}, 'Test')
        self.s.save_settings({'wahlrecht_ab': '2100'}, 'Test')
        self.s.save_settings({'wahl_beitragsstichtag': '2027-01-29', 'wahlrecht_ab': '2027'}, 'Test')
        s = wahlrecht.stichtage(self.s.config(), '2027-03-01')                                  # vorbei: wieder der Vorschlag, der eingestellte bleibt wählbar
        self.assertEqual((s['beitrag'], s['beitritt'], s['letzter']), ('2028-01-31', '2027-11-30', '2027-01-29'))
        s = wahlrecht.stichtage(self.s.config(), '2028-03-01')                                  # Einstellung aus einem früheren Jahr ist überholt
        self.assertEqual((s['beitrag'], s['beitritt'], s['letzter']), ('2029-01-31', '2028-11-30', '2028-01-31'))
        self.s.save_settings({'wahl_beitrittsstichtag': '', 'wahl_beitragsstichtag': ''}, 'Test')   # leer = wieder der Vorschlag
        self.assertEqual(wahlrecht.stichtage(self.s.config(), '2026-11-19')['beitrag'], '2027-01-30')

    def test_ueberholter_stichtag_sperrt_nichts(self):
        """W4: Das Formular schickt den alten Beitragsstichtag mit – ist er überholt, darf er einen neuen Beitrittsstichtag nicht blockieren."""
        with mock.patch.object(wahlrecht, 'date', Heute):
            self.s.save_settings({'wahl_beitrittsstichtag': '2026-11-15', 'wahl_beitragsstichtag': '2027-01-29'}, 'Test')

        class Spaeter(date):
            @classmethod
            def today(cls):
                return cls(2027, 10, 1)
        with mock.patch.object(wahlrecht, 'date', Spaeter):
            self.s.save_settings({'wahl_beitrittsstichtag': '2027-11-15', 'wahl_beitragsstichtag': '2027-01-29'}, 'Test')   # ging vorher nicht
            s = wahlrecht.stichtage(self.s.config())
            self.assertEqual((s['beitrag'], s['beitritt'], s['letzter']), ('2028-01-31', '2027-11-15', '2027-01-29'))
            self.s.save_settings({'issuer': 'JU Test 2'}, 'Test')                               # andere Einstellungen bleiben unberührt speicherbar
            with self.assertRaises(ValueError):                                                 # gegen den geltenden Stichtag (31.01.2028) wird weiter geprüft
                self.s.save_settings({'wahl_beitrittsstichtag': '2028-02-15', 'wahl_beitragsstichtag': '2027-01-29'}, 'Test')
        wahlrecht.pruefe_einstellungen({'wahl_beitrittsstichtag': '2026-11-30'}, '2028-05-01')  # überholter Beitrittsstichtag zählt nicht, sperrt nicht
        with self.assertRaises(ValueError):
            wahlrecht.pruefe_einstellungen({'wahl_beitrittsstichtag': '2029-01-31'}, '2028-05-01')


class RegelTests(Base):
    def setUp(self):
        super().setUp()
        self.ov = gliederung.save_ov(self.s, {'kv': '5100', 'number': '11', 'name': 'Testort'}, 'Test')
        alle = ('2025-03-01', '2026-03-01', '2027-01-15')
        self.voll = self.member('W01', 'Voll', ov_id=self.ov, pays=alle)                             # alle drei Jahre bezahlt
        self.offen = self.member('W02', 'Offen', ov_id=self.ov, pays=alle[:2])                       # 2027 fehlt
        self.spaet = self.member('W03', 'Spät', pays=alle[:2] + ('2027-02-01',))                     # Zahlungseingang nach dem Stichtag
        self.frei = self.member('W04', 'Frei', dues_free='J')                                        # beitragsfrei
        self.gast = self.member('W05', 'Gast', guest='J')
        self.neu = self.member('W06', 'Neu', joined='2026-12-05', dues_free='J')                     # nach dem Beitrittsstichtag 30.11.2026
        self.mandat = self.member('W07', 'Mandat', kv='5300', pays=alle[:2])                         # SEPA-Mandat, aber 2027 nicht eingegangen
        lastschrift.create_mandate(self.s, self.mandat, {'iban': IBAN, 'signed_on': '2025-01-10', 'holder': 'Mandat Test'}, 'Test')
        self.rueck = self.member('W08', 'Rück', kv='5300', pays=alle)                                # 2027 gezahlt, dann Rücklastschrift
        self.pay(self.rueck, '-30', '2027-01-20')
        self.weg = self.member('W09', 'Weg', kv='5300', pays=alle[:1], left_on='2026-12-31')         # vor dem Stichtag ausgetreten
        self.geht = self.member('W10', 'Geht', kv='5300', pays=alle[:2], left_on='2027-06-30')       # Austritt nach dem Stichtag: Soll 2027 zu klären
        self.kommt = self.member('W11', 'Kommt', kv='5300', joined='2027-03-01')                     # tritt erst nach dem Stichtag ein
        self.luecke = self.member('W12', 'Lücke', kv='5300')                                         # 2025 nie bezahlt, danach nichts geschuldet
        with self.s.db:
            for year in (2026, 2027, 2028):
                self.s.db.execute("INSERT INTO dues_targets(member_id,year,amount,reason,created,actor) VALUES(?,?,0,'Test','2026-01-01','Test')", (self.luecke, year))

    def test_liste_zum_beitragsstichtag(self):
        self.member('G01', 'Punktgenau', pays=('2025-03-01', '2026-03-01', STICHTAG))                # Zahlungseingang am Stichtag selbst zählt
        self.member('G02', 'November', joined='2026-11-30', dues_free='J')                           # Eintritt am Beitrittsstichtag selbst zählt
        r = self.status()
        self.assertEqual((r['G01'], r['G02']), (('wahlberechtigt', []), ('wahlberechtigt', [])))
        self.assertEqual(self.status('2027-01-29', DANACH)['G01'], ('nicht', ['Beitrag offen: 2027']))   # einen Tag vorher noch nicht eingegangen
        self.assertEqual(self.status('2027-01-01', DANACH)['W02'], ('nicht', ['Beitrag offen: 2027']))   # am 01.01. fällig: am Tag selbst schon offen
        self.assertEqual(self.status('2026-12-31', DANACH)['W02'], ('wahlberechtigt', []))               # … am Tag davor noch nicht
        self.assertEqual(r['W01'], ('wahlberechtigt', []))
        self.assertEqual(r['W02'], ('nicht', ['Beitrag offen: 2027']))
        self.assertEqual(r['W03'], ('nicht', ['Beitrag offen: 2027']))                               # Eingang am 01.02. zählt nicht
        self.assertEqual(r['W04'], ('wahlberechtigt', []))
        self.assertEqual(r['W05'], ('nicht', ['Gastmitglied']))
        self.assertEqual(r['W06'], ('nicht', ['nach dem Beitrittsstichtag eingetreten']))
        self.assertEqual(r['W07'], ('nicht', ['Beitrag offen: 2027']))                               # Mandat allein reicht nicht
        self.assertEqual(r['W08'], ('nicht', ['Beitrag offen: 2027']))                               # Rücklastschrift
        self.assertEqual(r['W09'], ('ausgetreten', ['Austritt 31.12.2026']))
        self.assertEqual(r['W10'], ('nicht', ['Soll zu klären: 2027 (Austrittsjahr)']))              # noch Mitglied, Austrittsjahr ungeklärt
        self.assertEqual(r['W11'], ('nicht', ['Eintritt erst am 01.03.2027']))                       # heutiges Mitglied, am Stichtag noch keines
        self.assertEqual(r['W12'], ('nicht', ['Beitrag offen: 2025']))
        self.assertFalse(any('€' in g or ',' in g.replace(', ', '') for _, gs in r.values() for g in gs))   # Gründe ohne Beträge

    def test_fenster_und_datenbeginn(self):
        self.assertEqual(self.status('2028-01-31', '2028-02-01')['W12'], ('wahlberechtigt', []))     # 2025 liegt nicht mehr im Fenster 2026–2028
        # Stichtag 31.01.2026: 2024 liegt vor dem Datenbeginn und gilt als bezahlt; 2026 muss bis dahin eingegangen sein
        frueh = self.member('W13', 'Früh', pays=('2025-03-01', '2026-01-10'))
        r = self.status('2026-01-31', DANACH)
        self.assertEqual((r['W13'], r['W01']), (('wahlberechtigt', []), ('nicht', ['Beitrag offen: 2026'])))
        self.assertEqual(self.s.db.execute('SELECT joined FROM members WHERE id=?', (frueh,)).fetchone()[0], '2020-01-01')

    def test_verrechnung_aelteste_zuerst(self):
        """W3 (REGELN Nr. 25, vereinbart): Zahlungen decken die ältesten Beiträge zuerst – auch ein Jahr vor dem Dreijahresfenster. Wer 2025
        nie bezahlt hat, dessen Zahlungen 2026–2028 gelten für 2025–2027; 2028 bleibt offen. Das ist strenger als der Wortlaut des § 37
        (Fenster 2026–2028 wäre bezahlt). Vor dem Stichtag Januar 2028 wird je Jahr des Fensters gerechnet – dann diesen Test umdrehen."""
        self.member('V01', 'Altlast', pays=('2026-03-01', '2027-03-01', '2028-01-15'))
        self.assertEqual(self.status('2028-01-31', '2028-02-01')['V01'], ('nicht', ['Beitrag offen: 2028']))
        self.assertEqual(self.status(STICHTAG, DANACH)['V01'][0], 'nicht')                            # 2027 liegt 2025 noch im Fenster: so oder so nicht

    def test_ruecklastschrift_nach_dem_stichtag(self):
        """W1: Zahlung vor, Rücklastschrift nach dem Stichtag – die Liste bleibt beim Stand am Stichtag, weist aber darauf hin."""
        alle = ('2025-03-01', '2026-03-01', '2027-01-10')
        self.member('R01', 'Platzer', pays=alle)
        self.pay(self.s.db.execute("SELECT id FROM members WHERE number='R01'").fetchone()[0], '-30', '2027-02-03')
        self.member('R02', 'Später', pays=alle[:2] + ('2027-02-01',))                                 # nach dem Stichtag gezahlt und zurückgebucht: kein Hinweis
        self.pay(self.s.db.execute("SELECT id FROM members WHERE number='R02'").fetchone()[0], '-30', '2027-02-03')
        liste = wahlrecht.auswertung(self.s, STICHTAG, '2027-02-05')
        by = {x['number']: x for x in liste['mitglieder']}
        self.assertEqual((by['R01']['status'], by['R01']['gruende'], by['R01']['hinweis']), ('wahlberechtigt', [], wahlrecht.RUECK))
        self.assertEqual((by['R02']['status'], by['R02']['hinweis'], by['W01']['hinweis'], by['W08']['hinweis']), ('nicht', '', '', ''))   # W08: Rücklastschrift vor dem Stichtag
        self.assertEqual(liste['hinweise'], 1)
        self.assertEqual(wahlrecht.auswertung(self.s, STICHTAG, DANACH)['hinweise'], 0)               # am 02.02. war noch nichts zurückgebucht
        heute = wahlrecht.auswertung(self.s, None, '2027-02-05')                                      # laufender Status: schlicht nicht wahlberechtigt
        self.assertEqual([(x['status'], x['gruende'], x['hinweis']) for x in heute['mitglieder'] if x['number'] == 'R01'], [('nicht', ['Beitrag offen: 2027'], '')])
        self.assertEqual(heute['hinweise'], 0)
        line = next(x for x in wahlrecht.csv(self.s, STICHTAG, '2027-02-05').decode('utf-8-sig').splitlines() if ';R01;' in x)
        self.assertTrue(line.endswith(';wahlberechtigt;;' + wahlrecht.RUECK), line)
        text = ' '.join(' '.join(pdf_text(wahlrecht.pdf(self.s, STICHTAG, '2027-02-05'))).split())
        self.assertIn('1 Mitglied(er) mit Hinweis', text)
        self.assertIn('Rücklastschrift nach dem Stichtag', text)

    def test_laufender_status(self):
        r = self.status(None, DANACH)                                # in der Frist des LMA gilt noch der Beitrittsstichtag 30.11.2026
        self.assertEqual((r['W03'], r['W06'], r['W02']), (('wahlberechtigt', []), ('nicht', ['nach dem Beitrittsstichtag eingetreten']),
                                                          ('nicht', ['Beitrag offen: 2027'])))
        self.assertEqual(self.status(None, '2027-02-08')['W06'], ('wahlberechtigt', []))             # nächster (30.11.2027) ist noch nicht erreicht
        self.assertEqual(self.status(None, '2026-12-10')['W06'], ('nicht', ['nach dem Beitrittsstichtag eingetreten']))   # 30.11.2026 erreicht
        self.assertEqual(self.status(None, '2026-12-31')['W09'][0], 'ausgetreten')                   # Austrittsdatum erreicht
        self.assertEqual(self.status(None, '2026-12-30')['W09'][0], 'nicht')
        self.assertEqual([wahlrecht.auswertung(self.s, None, day)['beitritt_zaehlt'] for day in ('2027-11-29', '2027-11-30')], [False, True])   # ab dem Tag selbst
        full = wahlrecht.auswertung(self.s, None, '2027-02-08')
        self.assertEqual((full['laufend'], full['beitritt_zaehlt'], full['vorschau'], full['gilt']), (True, False, False, True))

    def test_vor_wahlrecht_ab_kein_status_in_der_liste(self):
        self.assertEqual(wahlrecht.laufend(self.s, today='2026-11-19'), {})
        self.assertEqual(wahlrecht.laufend(self.s, today=DANACH)[self.offen], {'status': 'nicht', 'gruende': ['Beitrag offen: 2027']})
        year = date.today().year                                      # die Gesamtdaten rechnen mit dem echten heutigen Tag
        self.s.save_settings({'wahlrecht_ab': str(year + 1)}, 'Test')
        snapshot = self.s.snapshot(year)
        self.assertTrue(all(m['wahl'] is None for m in snapshot['members']))
        self.assertEqual(snapshot['wahl']['ab'], year + 1)
        self.s.save_settings({'wahlrecht_ab': str(year)}, 'Test')
        members = {m['number']: m for m in self.s.snapshot(year)['members']}
        self.assertTrue(all(m['wahl']['status'] in wahlrecht.STATUS for m in members.values()))
        self.assertEqual(members['W05']['wahl'], {'status': 'nicht', 'gruende': ['Gastmitglied']})
        self.assertNotIn('left_reason', members['W09'])               # Austrittsgrund erst beim Öffnen des Mitglieds

    def test_zusammenfassung_und_vorschau(self):
        r = wahlrecht.auswertung(self.s, STICHTAG, DANACH)
        self.assertEqual(r['gesamt'], {'wahlberechtigt': 2, 'nicht': 9, 'ausgetreten': 1})
        self.assertEqual([(k['code'], k['wahlberechtigt'], k['nicht']) for k in r['kv']], [('5100', 2, 4), ('5300', 0, 5)])
        self.assertEqual([(o['label'], o['wahlberechtigt'], o['nicht']) for o in r['kv'][0]['ovs']], [('11 Testort', 1, 1), ('ohne Ortsverband', 1, 3)])
        self.assertEqual((r['stichtag'], r['beitrittsstichtag'], r['laufend'], r['vorschau'], r['gilt']), (STICHTAG, '2026-11-30', False, False, True))
        self.assertEqual([x['number'] for x in r['mitglieder']][:3], ['W02', 'W01', 'W04'])           # je Kreis-/Ortsverband nach Name
        for number, last in (('U1', 'Özil'), ('U2', 'Zander'), ('U3', 'Ohm'), ('U4', 'ärmel'), ('U5', 'Aal')):
            self.member(number, 'Uta', kv='5600', last_name=last, dues_free='J')
        self.assertEqual([x['name'].split(',')[0] for x in wahlrecht.auswertung(self.s, STICHTAG, DANACH)['mitglieder'] if x['kv'] == '5600'],
                         ['Aal', 'ärmel', 'Ohm', 'Özil', 'Zander'])                                   # Umlaute beim Grundbuchstaben, nicht hinter Z
        spaet = wahlrecht.auswertung(self.s, STICHTAG, '2027-06-01')                                  # Liste später abgerufen: weiter der Beitrittsstichtag
        self.assertEqual(spaet['beitrittsstichtag'], '2026-11-30')                                    # dieses Stichtags, nicht der nächste (30.11.2027)
        self.assertEqual(next(x['gruende'] for x in spaet['mitglieder'] if x['number'] == 'W06'), ['nach dem Beitrittsstichtag eingetreten'])
        early = wahlrecht.auswertung(self.s, STICHTAG, '2026-11-19')                                  # Stichtag in der Zukunft: Stand der Zahlungen heute
        self.assertEqual((early['vorschau'], early['gilt']), (True, True))
        self.assertFalse(wahlrecht.auswertung(self.s, '2026-01-31', '2026-11-19')['gilt'])            # 2026 wird nicht ausgewertet
        self.s.save_settings({'wahl_beitrittsstichtag': '2026-12-10'}, 'Test')                        # eigener Beitrittsstichtag gilt für die Liste
        self.assertEqual(self.status()['W06'], ('wahlberechtigt', []))
        self.assertEqual(wahlrecht.auswertung(self.s, '2028-01-31', '2028-02-01')['beitrittsstichtag'], '2027-11-30')   # … nicht für das Jahr darauf
        for bad in ('morgen', '1999-01-31', '2031-01-31'):
            with self.assertRaises(ValueError):
                wahlrecht.auswertung(self.s, bad, DANACH)

    def test_pdf_und_csv(self):
        self.member('W14', 'Formel', last_name='=SUMME(1;2)', dues_free='J')
        for stichtag, today in ((STICHTAG, DANACH), (STICHTAG, '2026-11-19'), (None, DANACH)):
            self.assertTrue(wahlrecht.pdf(self.s, stichtag, today).startswith(b'%PDF'))
        pages = pdf_text(wahlrecht.pdf(self.s, STICHTAG, DANACH))
        text = ' '.join(pages)
        self.assertTrue('W01' in text and 'W10' in text and 'Soll zu klären: 2027 (Austrittsjahr)' in ' '.join(text.split()))
        self.assertNotIn('W09', text)                                                                 # Ausgetretene stehen nicht im Bericht
        for n, page in enumerate(pages, 1):                                                           # jede Seite: vertraulich, Seite x von y
            self.assertIn(f'Seite {n} von {len(pages)}', page)
            self.assertIn('Vertraulich', page)
        raw = wahlrecht.csv(self.s, STICHTAG, DANACH)
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        lines = raw.decode('utf-8-sig').splitlines()
        self.assertEqual(lines[0], 'Kreisverband;Ortsverband;Mitgliedsnummer;Name;Status am 30.01.2027;Grund;Hinweis')
        self.assertEqual(len(lines), 1 + 12)                                                          # ohne das ausgetretene Mitglied
        self.assertTrue(any(";W14;\"'=SUMME(1;2), Formel\";wahlberechtigt;" in line for line in lines), lines)   # Formelschutz
        self.assertIn(';11 Testort;W02;Test, Offen;nicht wahlberechtigt;Beitrag offen: 2027;', lines[1])
        self.assertNotIn('W09', raw.decode('utf-8-sig'))


class AustrittTests(Base):
    def test_eintragen_aendern_zuruecknehmen(self):
        mid = self.member('A1', 'Anna')
        mitglieder.austritt(self.s, mid, '31.12.2027', '  Umzug   nach Kiel ', 'Test')                # künftiges Datum erlaubt
        row = self.s.db.execute('SELECT left_on,left_reason FROM members WHERE id=?', (mid,)).fetchone()
        self.assertEqual(tuple(row), ('2027-12-31', 'Umzug nach Kiel'))
        log = self.s.db.execute('SELECT action,detail FROM audit ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual((log[0], json.loads(log[1])), ('Austritt eingetragen', {
            'id': mid, 'number': 'A1', 'vorher': {'austritt': None, 'grund': False}, 'nachher': {'austritt': '2027-12-31', 'grund': True}}))
        self.assertNotIn('Kiel', log[1])                                                               # der Grund steht nicht im Protokoll
        count = lambda: self.s.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
        before = count()
        mitglieder.austritt(self.s, mid, '2027-12-31', 'Umzug nach Kiel', 'Test')                     # unverändert gespeichert: kein Protokolleintrag
        self.assertEqual(count(), before)
        mitglieder.austritt(self.s, mid, '2027-12-31', 'Umzug nach Kiel und zurück', 'Test')          # nur der Grund geändert: protokolliert
        self.assertEqual(count(), before + 1)
        mitglieder.austritt(self.s, mid, '2026-06-30', '', 'Test')
        self.assertEqual(wahlrecht.auswertung(self.s, None, '2026-06-30')['mitglieder'][0]['status'], 'ausgetreten')
        self.assertEqual(wahlrecht.auswertung(self.s, None, '2026-06-29')['mitglieder'][0]['status'], 'nicht')
        mitglieder.austritt(self.s, mid, None, 'egal', 'Test')
        row = self.s.db.execute('SELECT left_on,left_reason FROM members WHERE id=?', (mid,)).fetchone()
        self.assertEqual(tuple(row), (None, None))
        log = self.s.db.execute('SELECT action,detail FROM audit ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual((log[0], json.loads(log[1])['vorher'], json.loads(log[1])['nachher']),
                         ('Austritt zurückgenommen', {'austritt': '2026-06-30', 'grund': False}, {'austritt': None, 'grund': False}))

    def test_pruefung(self):
        mid = self.member('A1', 'Anna')
        for left, reason, text in (('2019-12-31', '', 'vor dem Eintritt'), ('bald', '', 'Datum nicht erkannt'), ('2026-06-30', 'x' * 201, 'höchstens 200'),
                                   (None, '', 'kein Austritt eingetragen')):
            with self.assertRaises(ValueError) as error:
                mitglieder.austritt(self.s, mid, left, reason, 'Test')
            self.assertIn(text, str(error.exception))
        limit = date.today().year + 2
        with self.assertRaises(ValueError) as error:                                                  # Tippfehler im Jahr
            mitglieder.austritt(self.s, mid, f'{limit + 1}-01-01', '', 'Test')
        self.assertIn(f'zu weit in der Zukunft (höchstens Ende {limit})', str(error.exception))
        for reason in (123, ['Umzug'], {'a': 1}, True):                                               # Grund muss Text sein
            with self.assertRaises(ValueError) as error:
                mitglieder.austritt(self.s, mid, '2026-06-30', reason, 'Test')
            self.assertIn('als Text', str(error.exception))
        for bad in (999, None, True, 1.9, str(mid), [mid]):                                           # Mitglied nur als ganze Zahl
            with self.assertRaises(ValueError) as error:
                mitglieder.austritt(self.s, bad, '2026-06-30', '', 'Test')
            self.assertIn('Mitglied', str(error.exception))
        self.assertIsNone(self.s.db.execute('SELECT left_on FROM members WHERE id=?', (mid,)).fetchone()[0])
        mitglieder.austritt(self.s, mid, f'{limit}-12-31', None, 'Test')                              # Ende des übernächsten Jahres geht noch
        mitglieder.austritt(self.s, mid, None, None, 'Test')
        self.assertIsNone(self.s.db.execute('SELECT left_on FROM members WHERE id=?', (mid,)).fetchone()[0])

    def test_formularfeld_nutzt_dieselbe_pruefung(self):
        mid = self.member('A1', 'Anna')
        mitglieder.austritt(self.s, mid, '2026-06-30', 'Kündigung', 'Test')
        old = dict(self.s.db.execute('SELECT * FROM members WHERE id=?', (mid,)).fetchone())
        with self.assertRaises(ValueError) as error:
            self.s.update_member(mid, {**old, 'left_on': '2019-01-01'}, 'Test')
        self.assertIn('vor dem Eintritt', str(error.exception))
        self.s.update_member(mid, {**{k: v for k, v in old.items() if k != 'left_reason'}, 'left_on': '2026-07-31'}, 'Test')   # Formular schickt den Grund nicht
        self.assertEqual(self.s.db.execute('SELECT left_reason FROM members WHERE id=?', (mid,)).fetchone()[0], 'Kündigung')
        self.s.update_member(mid, {**old, 'left_on': ''}, 'Test')                                     # Austritt im Formular geleert: Grund entfällt
        self.assertEqual(tuple(self.s.db.execute('SELECT left_on,left_reason FROM members WHERE id=?', (mid,)).fetchone()), (None, None))


class RechteTests(unittest.TestCase):
    """Die neuen Wege am laufenden Server: Wahlberechtigung lesen = „Mitglieder lesen“, Austritt = „Mitglieder schreiben“."""
    setUpClass = classmethod(test_server.ServerHttpTests.setUpClass.__func__)
    tearDownClass = classmethod(test_server.ServerHttpTests.tearDownClass.__func__)
    call = test_server.ServerHttpTests.call
    setUp, login, get, post, person = (getattr(test_rollen.RollenHttpTests, name) for name in ('setUp', 'login', 'get', 'post', 'person'))

    def file(self, who, path):
        request = urllib.request.Request(self.url + path, headers={'Host': 'ju-finanzen.beispiel.ts.net',
                                                                   'Cookie': f"__Host-ju_session={who['cookie']}; ju_session={who['cookie']}"})
        with urllib.request.urlopen(request) as r:
            return r.status, r.headers.get('Content-Type'), r.read()

    def test_rechte(self):
        officer = self.person('Mitglieder-Person', 'Landesmitgliederbeauftragter')                   # Mitglieder schreiben, keine Buchhaltung
        status, r = self.get(officer, '/api/wahlrecht?stichtag=2027-01-30')
        self.assertEqual((status, [x['name'] for x in r['mitglieder']], r['stichtag']), (200, ['Muster, Erika'], '2027-01-30'))
        self.assertEqual(self.get(officer, '/api/wahlrecht')[1]['laufend'], True)
        self.assertEqual(self.get(officer, '/api/wahlrecht?stichtag=quatsch')[0], 400)
        self.assertEqual(self.file(officer, '/api/wahlrecht.pdf?stichtag=2027-01-30')[:2], (200, 'application/pdf'))
        status, kind, body = self.file(officer, '/api/wahlrecht.csv?stichtag=2027-01-30')
        self.assertEqual((status, kind), (200, 'text/csv; charset=utf-8'))
        self.assertIn('Muster, Erika;nicht wahlberechtigt;Beitrag offen: 2026, 2027', body.decode('utf-8-sig'))
        member = self.get(officer, '/api/data?year=2026')[1]['members'][0]
        self.assertIn('wahl', member)                                                                 # Status ohne Beträge: mit „Mitglieder lesen“ sichtbar
        self.assertEqual((member['balance'], member['dues_issues']), (0, []))
        self.assertEqual(self.get(officer, f"/api/member-account?id={member['id']}")[1]['austritt'],
                         {'offen': True, 'mandat': False, 'festgelegt': []})                          # Hinweise: nur Ja/Nein und Jahre, keine Beträge
        for body in ({'left_on': '2026-12-31'}, {'id': True, 'left_on': '2026-12-31'}, {'id': 1.9, 'left_on': '2026-12-31'},
                     {'id': member['id'], 'left_on': '2026-12-31', 'reason': {'a': 1}}):
            self.assertEqual(self.post(officer, '/api/member-austritt', body)[0], 400, body)
        self.assertEqual(self.post(officer, '/api/member-austritt', {'id': member['id'], 'left_on': '2019-01-01'}),
                         (400, {'error': 'Austritt kann nicht vor dem Eintritt liegen.'}))
        self.assertEqual(self.post(officer, '/api/member-austritt', {'id': member['id'], 'left_on': '2026-12-31', 'reason': 'Umzug'})[0], 200)
        status, account = self.get(officer, f"/api/member-account?id={member['id']}")
        self.assertEqual((account['member']['left_on'], account['member']['left_reason']), ('2026-12-31', 'Umzug'))
        self.assertEqual(self.get(officer, '/api/wahlrecht?stichtag=2027-01-30')[1]['gesamt'], {'wahlberechtigt': 0, 'nicht': 0, 'ausgetreten': 1})
        self.assertEqual(self.post(officer, '/api/member-austritt', {'id': member['id'], 'left_on': None})[0], 200)

        reader = self.person('leitung', 'Landesgeschäftsführer')                                      # alles lesen, nichts ändern
        self.assertEqual(self.get(reader, '/api/wahlrecht')[0], 200)
        self.assertEqual(self.post(reader, '/api/member-austritt', {'id': member['id'], 'left_on': '2026-12-31'})[0], 403)

        admin = self.person('technik', 'Administrator')                                               # keine Mitgliederdaten
        self.assertEqual(self.post(self.chefin, '/api/rolle', {'name': 'Statistik', 'rights': {'auswertung': 'lesen'}})[0], 200)
        store = Store(test_server.app.DB)
        try:
            self.roles = {r[1]: r[0] for r in store.db.execute('SELECT id,name FROM roles')}
        finally:
            store.db.close()
        numbers = self.person('zahlen', 'Statistik')                                                  # nur Auswertung: Zahlen, keine Namen
        for who in (admin, numbers):
            for path in ('/api/wahlrecht', '/api/wahlrecht.pdf', '/api/wahlrecht.csv', f"/api/member-account?id={member['id']}"):
                self.assertEqual(self.get(who, path)[0], 403, path)
            self.assertEqual(self.post(who, '/api/member-austritt', {'id': member['id'], 'left_on': '2026-12-31'})[0], 403)
            data = self.get(who, '/api/data?year=2026')[1]
            self.assertEqual(data['members'], [])
            self.assertEqual(set(data['wahl']), {'ab', 'beitrag', 'beitritt', 'vorschlag', 'letzter'})   # nur Stichtage, keine Mitgliederdaten
        self.assertIsNone(self.get(self.chefin, '/api/member-account?id=' + str(member['id']))[1]['member']['left_on'])


if __name__ == '__main__':
    unittest.main()
