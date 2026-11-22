"""Mitgliedsnummer-Vorschlag und Bankverbindung am Mitglied (0.53) – nur erfundene Mitglieder und Beispiel-IBANs."""
import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

import doppelte
import lastschrift
import mitglieder
import mitgliederlisten
import rollen
from core import Store

CREDITOR = 'DE98ZZZ09999999999'                     # Beispiel-Gläubiger-ID der Bundesbank
IBAN = 'DE02120300000000202051'                     # Beispiel-IBANs (test_repo.EXAMPLE_IBANS)
IBAN2 = 'DE89370400440532013000'
BASE = dict(first_name='Erika', last_name='Muster', street='Musterweg 1', zip='20095', city='Hamburg', birth='2000-02-01',
            joined='2024-01-01', kv='5100')
STAMM = ('number', 'first_name', 'last_name', 'street', 'zip', 'city', 'birth', 'joined', 'kv')


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, number, **extra):
        return self.s.member({**BASE, 'number': number, **extra}, 'Test')

    def m(self, mid):
        return dict(self.s.db.execute('SELECT * FROM members WHERE id=?', (mid,)).fetchone())


class NummerTests(Basis):
    def test_vorschlag_im_haeufigsten_format(self):
        self.assertEqual(mitglieder.naechste_nummer(self.s.db), '')                       # ohne Mitglieder kein Vorschlag
        for n in ('A0000000007', 'A0000000123', 'K-1', '99'):
            self.member(n)
        self.assertEqual(mitglieder.naechste_nummer(self.s.db), 'A0000000124')
        self.member('A0000000124')
        self.assertEqual(mitglieder.naechste_nummer(self.s.db), 'A0000000125')
        self.member('A125')                                                               # gleiche Zahl ohne führende Nullen: überspringen
        self.assertEqual(mitglieder.naechste_nummer(self.s.db), 'A0000000126')
        with self.assertRaisesRegex(ValueError, 'schon vergeben'):                        # zweimal derselbe Vorschlag: verständliche Meldung
            self.member('A125')


class BankTests(Basis):
    def test_iban_pruefen_speichern_anzeigen(self):
        mid = self.member('M1', iban='de02 1203 0000 0000 2020 51', kontoinhaber='Erika Muster')
        self.assertEqual((self.m(mid)['iban'], self.m(mid)['kontoinhaber']), (IBAN, 'Erika Muster'))   # kompakt, groß
        self.assertIn(('IBAN', 'DE02 1203 0000 0000 2020 51'), mitglieder.angaben(self.s, self.m(mid)))
        with self.assertRaisesRegex(ValueError, 'IBAN ungültig'):
            self.s.update_member(mid, {**{k: self.m(mid)[k] for k in STAMM}, 'iban': 'DE02120300000000202052'}, 'Test')
        self.s.update_member(mid, {**{k: self.m(mid)[k] for k in STAMM}}, 'Test')
        self.assertEqual(self.m(mid)['iban'], IBAN)                                        # nicht mitgeschickt: bleibt
        self.s.update_member(mid, {**{k: self.m(mid)[k] for k in STAMM}, 'iban': ''}, 'Test')
        self.assertEqual((self.m(mid)['iban'], self.m(mid)['kontoinhaber']), (None, None))  # ohne IBAN kein Kontoinhaber

    def test_nur_ewr_und_bindestriche(self):
        mid = self.member('M1', iban='DE02-1203-0000-0000-2020-51')
        self.assertEqual(self.m(mid)['iban'], IBAN)
        with self.assertRaisesRegex(ValueError, 'außerhalb des EWR'):
            self.member('M2', iban='CH93 0076 2011 6238 5295 7')

    def test_verfallenes_mandat_und_kontowechsel(self):
        self.s.save_settings({'creditor_id': CREDITOR}, 'Test')
        alt, neu = self.member('M1', iban=IBAN), self.member('M2', iban=IBAN)
        lastschrift.create_mandate(self.s, alt, {'iban': IBAN2, 'holder': 'Alt Inhaber', 'signed_on': '2021-01-15'}, 'Test')   # 36 Monate ungenutzt
        lastschrift.create_mandate(self.s, neu, {'iban': IBAN2, 'holder': 'Alt Inhaber', 'signed_on': '2026-01-15'}, 'Test')   # gültig, anderes Konto
        liste = {m['id']: m for m in self.s.snapshot(2026)['members']}
        self.assertEqual((liste[alt]['sepa_mandat'], liste[neu]['sepa_mandat'], liste[neu]['iban_abweichend']), (False, True, True))
        body, _, _ = mitgliederlisten.datei(self.s, {'art': 'mandate', 'ids': [alt, neu]}, 'Test')
        for seite in (p.extract_text() for p in PdfReader(io.BytesIO(body)).pages):  # beide: neues Mandat mit den Bankdaten des Mitglieds
            self.assertIn('DE02 1203 0000 0000 2020 51', seite)
            self.assertIn('wird mit der ersten Beitragsrechnung mitgeteilt', seite)
            self.assertNotIn('Alt Inhaber', seite)

    def test_verfall_allein_und_serie_ohne_mandatsdaten(self):
        self.s.save_settings({'creditor_id': CREDITOR}, 'Test')
        gleich, ohne, gueltig = self.member('M1', iban=IBAN2), self.member('M2'), self.member('M3', iban=IBAN2)
        for mid, signiert in ((gleich, '2021-01-15'), (ohne, '2021-01-15'), (gueltig, '2026-01-15')):   # zwei verfallen, eines gültig
            lastschrift.create_mandate(self.s, mid, {'iban': IBAN2, 'holder': 'Alt Inhaber', 'signed_on': signiert}, 'Test')
        for mid in (gleich, ohne):                                                          # Einzelformular der Buchhaltung
            seite = PdfReader(io.BytesIO(lastschrift.mandate_pdf(self.s, mid))).pages[0].extract_text()
            self.assertIn('wird mit der ersten Beitragsrechnung mitgeteilt', seite)
            self.assertNotIn('JUHH-', seite)
        einzeln = PdfReader(io.BytesIO(lastschrift.mandate_pdf(self.s, gueltig))).pages[0].extract_text()
        self.assertIn('JUHH-M3-1', einzeln)                                                  # gültig: die Buchhaltung sieht ihr Mandat
        body, _, _ = mitgliederlisten.datei(self.s, {'art': 'mandate', 'ids': [gueltig]}, 'Test')
        serie = PdfReader(io.BytesIO(body)).pages[0].extract_text()
        self.assertNotIn('JUHH-', serie)                                                    # Serie (Mitgliederverwaltung): nie Mandatsdaten
        self.assertNotIn('Alt Inhaber', serie)

    def test_rechte_gesamtliste(self):
        self.member('M1', iban=IBAN)
        for rechte in ({'mitglieder': 'lesen'}, {'mitglieder': 'schreiben', 'auswertung': 'lesen'}, {'buchhaltung': 'schreiben'}):
            for m in rollen.filter_snapshot(self.s.snapshot(2026), rechte)['members']:
                self.assertNotIn('iban', m, rechte)
                self.assertNotIn('kontoinhaber', m, rechte)

    def test_gesamtliste_nur_ja_nein(self):
        self.s.save_settings({'creditor_id': CREDITOR}, 'Test')
        ohne, mit, gedeckt = self.member('M1', iban=IBAN), self.member('M2'), self.member('M3', iban=IBAN2)
        lastschrift.create_mandate(self.s, gedeckt, {'iban': IBAN2, 'signed_on': '2026-01-15'}, 'Test')
        liste = {m['id']: m for m in self.s.snapshot(2026)['members']}
        self.assertNotIn('iban', liste[ohne])                                              # Bankdaten erst beim Öffnen des Mitglieds
        self.assertNotIn('kontoinhaber', liste[ohne])
        self.assertEqual([(liste[i]['hat_iban'], liste[i]['sepa_mandat']) for i in (ohne, mit, gedeckt)], [(True, False), (False, False), (True, True)])

    def test_mandatsformulare_als_serie(self):
        a, b = self.member('M1', iban=IBAN), self.member('M2', first_name='Karl', iban=IBAN2, kontoinhaber='Konto Inhaberin')
        with self.assertRaisesRegex(ValueError, 'Gläubiger-ID'):
            mitgliederlisten.datei(self.s, {'art': 'mandate', 'ids': [a, b]}, 'Test')
        self.s.save_settings({'creditor_id': CREDITOR}, 'Test')
        body, kind, name = mitgliederlisten.datei(self.s, {'art': 'mandate', 'ids': [a, b]}, 'Test')
        self.assertEqual(kind, 'application/pdf')
        self.assertTrue(name.startswith('SEPA-Mandate-'))
        seiten = [p.extract_text() for p in PdfReader(io.BytesIO(body)).pages]
        self.assertEqual(len(seiten), 2)                                                   # eine Seite je Mitglied
        self.assertIn('DE02 1203 0000 0000 2020 51', seiten[0])
        self.assertIn('Erika Muster', seiten[0])                                           # ohne Kontoinhaber: das Mitglied
        self.assertIn('DE89 3704 0044 0532 0130 00', seiten[1])
        self.assertIn('Konto Inhaberin', seiten[1])
        audit = self.s.db.execute("SELECT detail FROM audit WHERE action='Mitgliederliste ausgegeben' ORDER BY id DESC").fetchone()[0]
        self.assertNotIn('DE02', audit)                                                    # Protokoll nur Art und Anzahl

    def test_import_uebernimmt_gueltige_iban(self):
        raw = ('Mitgliedsnummer;Vorname;Name;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband;IBAN;Kontoinhaber\n'
               f'M1;Erika;Muster;Musterweg 1;20095;Hamburg;01.02.2000;01.01.2024;Altona;{IBAN};Erika Muster\n'
               'M2;Karl;Probe;Musterweg 2;20095;Hamburg;01.02.2000;01.01.2024;Altona;DE00123;Karl Probe\n').encode()
        p = mitglieder.preview(self.s, raw, 'liste.csv')
        rows = {r['number']: r for r in p['rows']}
        self.assertEqual(rows['M1']['values']['iban'], IBAN)
        self.assertIsNone(rows['M2']['values']['iban'])                                    # ungültig: nicht übernommen, Zeile bleibt
        self.assertIsNone(rows['M2']['values']['kontoinhaber'])
        self.assertIn('IBAN ungültig', rows['M2']['warning'])
        self.assertEqual(rows['M2']['action'], 'new')
        mitglieder.apply(self.s, p, [r['row_no'] for r in p['rows']], 'Test')
        m1 = self.s.db.execute("SELECT iban,kontoinhaber FROM members WHERE number='M1'").fetchone()
        self.assertEqual(tuple(m1), (IBAN, 'Erika Muster'))

    def test_import_aktualisiert_nur_als_paar(self):
        kopf = 'Mitgliedsnummer;Vorname;Name;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband;IBAN'
        zeile = lambda nr, iban: f'{nr};Erika;Muster;Musterweg 1;20095;Hamburg;01.02.2000;01.01.2024;Altona;{iban}'   # noqa: E731
        leer, falsch, wechsel = (self.member(n, iban=IBAN, kontoinhaber='Mutter Muster') for n in ('M1', 'M2', 'M3'))
        raw = '\n'.join([kopf, zeile('M1', ''), zeile('M2', 'DE00123'), zeile('M3', IBAN2)]).encode()
        p = mitglieder.preview(self.s, raw, 'liste.csv')
        mitglieder.apply(self.s, p, [r['row_no'] for r in p['rows'] if r['action'] in ('new', 'update')], 'Test')
        self.assertEqual((self.m(leer)['iban'], self.m(leer)['kontoinhaber']), (IBAN, 'Mutter Muster'))       # leere Zelle lässt stehen
        self.assertEqual((self.m(falsch)['iban'], self.m(falsch)['kontoinhaber']), (IBAN, 'Mutter Muster'))   # ungültig: beides bleibt
        self.assertEqual((self.m(wechsel)['iban'], self.m(wechsel)['kontoinhaber']), (IBAN2, None))           # neues Konto: alter Inhaber entfällt
        hinweis = {r['number']: r['warning'] for r in p['rows']}
        self.assertIn('Kontoinhaber ist jetzt das Mitglied', hinweis['M3'])
        self.assertNotIn('Leere Zelle', hinweis['M3'])

    def test_import_kontoinhaber_und_ewr(self):
        gleich, nur_inhaber = self.member('M1', iban=IBAN, kontoinhaber='Mutter Muster'), self.member('M2', iban=IBAN, kontoinhaber='Vater Muster')
        zeile = 'M{};Erika;Muster;Musterweg 1;20095;Hamburg;01.02.2000;01.01.2024;Altona;{};{}'
        raw = '\n'.join(['Mitgliedsnummer;Vorname;Name;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband;IBAN;Kontoinhaber',
                         zeile.format(1, IBAN, ''), zeile.format(3, 'CH9300762011623852957', 'Fremd')]).encode()
        p = mitglieder.preview(self.s, raw, 'liste.csv')
        rows = {r['number']: r for r in p['rows']}
        self.assertEqual(rows['M1']['values']['kontoinhaber'], 'Mutter Muster')            # gleiches Konto, leere Zelle: Inhaber bleibt
        self.assertEqual((rows['M3']['action'], rows['M3']['values']['iban']), ('new', None))   # CH: Hinweis, keine Fehlerzeile
        self.assertIn('außerhalb des EWR', rows['M3']['warning'])
        raw = ('Mitgliedsnummer;Vorname;Name;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband;Kontoinhaber\n'
               'M2;Erika;Muster;Musterweg 1;20095;Hamburg;01.02.2000;01.01.2024;Altona;\n').encode()
        row = mitglieder.preview(self.s, raw, 'liste.csv')['rows'][0]
        self.assertEqual((row['action'], row['values']['kontoinhaber']), ('same', 'Vater Muster'))   # Inhaber ohne IBAN-Spalte: ignoriert

    def test_zusammenfuehren_uebernimmt_bankverbindung_als_paar(self):
        keep, drop = self.member('M1'), self.member('M2', iban=IBAN, kontoinhaber='Erika Muster')
        doppelte.zusammenfuehren(self.s, keep, drop, 'Test')
        self.assertEqual((self.m(keep)['iban'], self.m(keep)['kontoinhaber']), (IBAN, 'Erika Muster'))
        other = self.member('M3', iban=IBAN2)
        drop2 = self.member('M4', iban=IBAN, kontoinhaber='Fremd')
        doppelte.zusammenfuehren(self.s, other, drop2, 'Test')
        self.assertEqual((self.m(other)['iban'], self.m(other)['kontoinhaber']), (IBAN2, None))   # vorhandene Bankverbindung bleibt ganz


if __name__ == '__main__':
    unittest.main()
