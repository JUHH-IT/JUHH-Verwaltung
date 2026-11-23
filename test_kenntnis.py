"""Kenntnisnahme von Änderungen an Stammdaten (0.54) – nur erfundene Mitglieder."""
import tempfile
import unittest
from unittest import mock
from datetime import date, timedelta
from pathlib import Path

import gliederung
import kenntnis
import mitglieder
import rollen
import stammdaten
from core import Store


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, **extra):
        self.n += 1
        return self.s.member({'number': f'K-{self.n}', 'first_name': 'Test', 'last_name': f'Person{self.n}', 'street': 'Musterweg 1', 'zip': '20001',
                              'city': 'Hamburg', 'birth': '2000-02-01', 'joined': '2024-01-01', 'kv': '5100', **extra}, 'Anna')

    def aendern(self, mid, actor, **neu):
        alt = dict(self.s.db.execute('SELECT * FROM members WHERE id=?', (mid,)).fetchone())
        self.s.update_member(mid, {**{k: alt[k] for k in mitglieder.FIELDS}, **neu}, actor)

    def gesehen(self):
        """Alles Bisherige abhaken – die Tests beginnen danach mit leerer Liste."""
        offen = kenntnis.liste(self.s)
        if offen['offen']:
            kenntnis.abhaken(self.s, [{'id': p['id'], 'sammel': p['sammel']} for p in offen['offen']], offen['stand'], 'Test')

    def offen(self):
        return kenntnis.liste(self.s)['offen']


class FesthaltenTests(Basis):
    def test_neues_mitglied_ist_ein_punkt_ohne_angaben(self):
        mid = self.member()
        [p] = self.offen()
        self.assertEqual((p['id'], p['neu'], p['felder'], p['actor'], p['anlass']), (mid, True, [], 'Anna', 'hand'))

    def test_aenderung_mit_vorher_und_jetzt(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', street='Beispielallee 4', zip='20002')
        [p] = self.offen()
        self.assertEqual(p['actor'], 'Bernd')
        self.assertFalse(p['neu'])
        self.assertEqual({f['feld']: (f['alt'], f['neu']) for f in p['felder']}, {'street': ('Musterweg 1', 'Beispielallee 4'), 'zip': ('20001', '20002')})
        self.assertEqual([f['titel'] for f in p['felder'] if f['feld'] == 'street'], ['Straße'])

    def test_mehrere_aenderungen_zeigen_ersten_alten_und_letzten_neuen_wert(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', city='Kiel')
        self.aendern(mid, 'Clara', city='Bremen')
        [p] = self.offen()
        self.assertEqual([(f['alt'], f['neu']) for f in p['felder']], [('Hamburg', 'Bremen')])
        self.assertEqual(p['actor'], 'Bernd, Clara')

    def test_hin_und_zurueck_ist_kein_punkt(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', city='Kiel')
        self.aendern(mid, 'Bernd', city='Hamburg')
        self.assertEqual(self.offen(), [])
        self.assertEqual(kenntnis.kurz(self.s.db), {'an': True, 'offen': [], 'punkte': 0})
        kenntnis.bereinigen(self.s)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mitglied_aenderungen WHERE gesehen_am IS NULL').fetchone()[0], 0)

    def test_betraege_und_notiz_werden_nie_festgehalten(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', dues=4800)
        mitglieder.notiz(self.s, mid, 'vertraulich', 'Bernd')
        self.assertEqual(self.offen(), [])
        felder = {r[0] for r in self.s.db.execute('SELECT feld FROM mitglied_aenderungen')}
        self.assertFalse(felder & {'dues', 'external_balance', 'note', 'name', 'address'})

    def test_lesbare_werte(self):
        ov = gliederung.save_ov(self.s, {'kv': '5100', 'number': 12, 'name': 'Testdorf'}, 'Test')
        werber = self.member()
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', ov_id=ov, guest=1, left_on='2026-12-31', geworben_von=werber, iban='DE02120300000000202051')
        werte = {f['feld']: (f['alt'], f['neu']) for f in self.offen()[0]['felder']}
        self.assertEqual(werte['ov_id'][0], '')
        self.assertIn('Testdorf', werte['ov_id'][1])
        self.assertEqual(werte['guest'], ('nein', 'ja'))
        self.assertEqual(werte['left_on'], ('', '31.12.2026'))
        self.assertIn('K-1', werte['geworben_von'][1])
        self.assertEqual(werte['iban'][1], 'DE02 •••• 2051')

    def test_austritt_und_andere_wege_werden_erfasst(self):
        mid = self.member()
        self.gesehen()
        mitglieder.austritt(self.s, mid, '2026-12-31', 'Umzug', 'Bernd')
        [p] = self.offen()
        self.assertEqual(p['actor'], 'Bernd')
        self.assertEqual({f['feld'] for f in p['felder']}, {'left_on', 'left_reason'})

    def test_aenderung_ohne_protokoll_gehoert_dem_programm(self):
        mid = self.member()
        self.gesehen()
        self.s.db.execute("UPDATE members SET city='Kiel' WHERE id=?", (mid,))
        self.s.db.commit()
        Store(Path(self.tmp.name) / 't.db').db.close()   # nächste Verbindung: gespeicherte Zeile ohne Person bekommt das Programm
        self.assertEqual(self.s.db.execute('SELECT actor,anlass FROM mitglied_aenderungen WHERE gesehen_am IS NULL').fetchall()[0][:], ('Programm', 'programm'))
        [p] = self.offen()
        self.assertEqual((p['actor'], p['anlass']), ('Programm', 'programm'))

    def test_fremder_protokolleintrag_ordnet_nichts_zu(self):
        mid = self.member()
        self.gesehen()
        self.s.db.execute("UPDATE members SET city='Kiel' WHERE id=?", (mid,))
        self.s.audit('Dora', 'Bankumsatz verbucht', {'id': 1})
        self.s.db.commit()
        self.assertEqual(self.offen()[0]['actor'], 'Programm')

    def test_gespeicherte_zeile_ohne_person_wird_nie_der_naechsten_person_zugeordnet(self):
        a, b = self.member(), self.member()
        self.gesehen()
        self.s.db.execute("UPDATE members SET city='Kiel' WHERE id=?", (a,))   # Änderung ohne Protokoll, gespeichert
        self.s.db.commit()
        zweite = Store(Path(self.tmp.name) / 't.db')   # nächste Anfrage: eigene Verbindung
        self.addCleanup(zweite.db.close)
        alt = dict(zweite.db.execute('SELECT * FROM members WHERE id=?', (b,)).fetchone())
        zweite.update_member(b, {**{k: alt[k] for k in mitglieder.FIELDS}, 'city': 'Bremen'}, 'Bernd')
        self.assertEqual({p['id']: p['actor'] for p in self.offen()}, {a: 'Programm', b: 'Bernd'})

    def test_geloeschtes_mitglied_nimmt_seine_zeilen_mit(self):
        mid = self.member()
        mitglieder.delete(self.s, mid, 'Bernd', arrears_ok=True)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mitglied_aenderungen').fetchone()[0], 0)

    def test_jede_spalte_der_stammdaten_ist_erfasst_oder_bewusst_ausgenommen(self):
        spalten = {r[1] for r in self.s.db.execute('PRAGMA table_info(members)')}
        ausgenommen = {'id', 'name', 'address', 'dues', 'external_balance', 'note', *mitglieder.LEGACY}
        self.assertEqual(spalten - set(kenntnis.FELDER) - ausgenommen, set(), 'neue Spalte: in kenntnis.FELDER aufnehmen (AUSLOESER hochzählen) oder hier ausnehmen')

    def test_init_ist_wiederholbar_und_entfernt_alte_ausloeser(self):
        self.s.db.execute('CREATE TRIGGER mitglied_aenderung_v0_u AFTER UPDATE ON members BEGIN SELECT 1; END')
        kenntnis.init(self.s.db)
        kenntnis.init(self.s.db)
        namen = sorted(r[0] for r in self.s.db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'mitglied_aenderung_%'"))
        self.assertEqual(namen, [kenntnis.AUSLOESER + '_i', kenntnis.AUSLOESER + '_u'])


class AbhakenTests(Basis):
    def test_abhaken_nur_bis_zum_gesehenen_stand(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', city='Kiel')
        stand = kenntnis.liste(self.s)['stand']
        self.aendern(mid, 'Clara', zip='24103')   # kommt dazu, nachdem die Liste geladen wurde
        self.assertEqual(kenntnis.abhaken(self.s, [{'id': mid}], stand, 'Lena'), {'mitglieder': 1})
        [p] = self.offen()
        self.assertEqual([f['feld'] for f in p['felder']], ['zip'])
        [a] = [x for x in kenntnis.liste(self.s)['abgehakt'] if x['gesehen_von'] == 'Lena']
        self.assertEqual((a['gesehen_von'], [f['feld'] for f in a['felder']]), ('Lena', ['city']))

    def test_protokoll_nur_mit_anzahlen(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', city='Kiel', street='Geheimweg 9')
        kenntnis.abhaken(self.s, [{'id': mid}], kenntnis.liste(self.s)['stand'], 'Lena')
        row = self.s.db.execute("SELECT detail FROM audit WHERE action='Änderungen zur Kenntnis genommen' ORDER BY id DESC").fetchone()
        self.assertEqual(row[0], '{"mitglieder": 1, "angaben": 2, "ids": [%d]}' % mid)   # Anzahlen und Kennungen, nie Werte
        self.assertNotIn('Geheimweg', ' '.join(r[0] for r in self.s.db.execute('SELECT detail FROM audit')))

    def test_abhaken_steht_im_verlauf_des_mitglieds(self):
        """0.55: wer wann zur Kenntnis genommen hat, steht dauerhaft im Verlauf des Mitglieds – auch nach dem Löschen der Zeilen."""
        mid, anderes = self.member(), self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', city='Kiel')
        kenntnis.abhaken(self.s, [{'id': mid}], kenntnis.liste(self.s)['stand'], 'Lena')
        kenntnis.bereinigen(self.s, date.today() + timedelta(days=kenntnis.FRIST + 1))
        erster = mitglieder.verlauf(self.s, mid)[0]
        self.assertEqual((erster['actor'], erster['text']), ('Lena', 'Änderung zur Kenntnis genommen'))
        self.assertNotIn('Lena', [v['actor'] for v in mitglieder.verlauf(self.s, anderes)])

    def test_ausgenommene_angaben(self):
        """0.55: `kenntnis_ohne` – die Angabe wird nicht mehr festgehalten, schon Festgehaltenes ist nach dem Speichern weg."""
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', phone='040 1', city='Kiel')
        self.assertEqual([f['feld'] for f in kenntnis.liste(self.s)['offen'][0]['felder']], ['city', 'phone'])
        self.s.save_settings({'kenntnis_ohne': 'phone,mobile'}, 'Lena')
        self.assertEqual(self.s.config()['kenntnis_ohne'], 'phone,mobile')
        self.assertEqual([f['feld'] for f in kenntnis.liste(self.s)['offen'][0]['felder']], ['city'])
        self.aendern(mid, 'Bernd', phone='040 2', mobile='0170 1', street='Neuer Weg 3')
        self.assertEqual([f['feld'] for f in kenntnis.liste(self.s)['offen'][0]['felder']], ['city', 'street'])
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM mitglied_aenderungen WHERE feld IN ('phone','mobile')").fetchone()[0], 0)
        self.s.save_settings({'kenntnis_ohne': ''}, 'Lena')
        self.aendern(mid, 'Bernd', phone='040 3')
        self.assertIn('phone', [f['feld'] for f in kenntnis.liste(self.s)['offen'][0]['felder']])
        for falsch in ('dues', 'gibtsnicht', 'phone;DROP'):
            with self.assertRaises(ValueError, msg=falsch):
                self.s.save_settings({'kenntnis_ohne': falsch}, 'Lena')

    def test_hoechstfrist(self):
        """0.55: ohne Höchstfrist (Vorgabe) bleibt Offenes liegen; mit Höchstfrist löscht die Bereinigung ältere offene Zeilen und
        schreibt das mit der Anzahl ins Protokoll."""
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', city='Kiel')
        in_100 = date.today() + timedelta(days=100)
        self.assertEqual(stammdaten.DEFAULTS['kenntnis_hoechstfrist'], '0')
        kenntnis.bereinigen(self.s, in_100)
        self.assertEqual(len(kenntnis.liste(self.s)['offen']), 1)
        self.s.save_settings({'kenntnis_hoechstfrist': '90'}, 'Lena')       # speichert und bereinigt mit dem heutigen Tag: noch nichts zu alt
        self.assertEqual(len(kenntnis.liste(self.s)['offen']), 1)
        kenntnis.bereinigen(self.s, in_100)
        self.assertEqual(kenntnis.liste(self.s)['offen'], [])
        row = self.s.db.execute("SELECT actor,detail FROM audit WHERE action='Nicht abgehakte Änderungen nach Höchstfrist gelöscht'").fetchone()
        self.assertEqual(tuple(row), ('Programm', '{"angaben": 1, "tage": 90}'))
        for falsch in ('30', '-1', 'x', ''):
            with self.assertRaises(ValueError, msg=falsch):
                self.s.save_settings({'kenntnis_hoechstfrist': falsch}, 'Lena')

    def test_ungueltige_auswahl(self):
        P = lambda *ids: [{'id': i} for i in ids]
        for ids, bis in (([], 5), ('1', 5), ([1], 5), (P(True), 5), (P(1.5), 5), (P(1), None), (P(1), 0), (P(1), 2 ** 70), ([{'id': 1, 'sammel': 'hand|x|2026-01-01'}], 5),
                         ([{'id': 1, 'sammel': 'import||2026-01-01'}], 5), ([{'id': 1, 'sammel': 7}], 5), (P(*range(1, kenntnis.MAX_AUSWAHL + 2)), 5)):
            with self.assertRaises(ValueError, msg=(str(ids)[:20], bis)):
                kenntnis.abhaken(self.s, ids, bis, 'Lena')

    def test_nichts_offen_schreibt_kein_protokoll(self):
        mid = self.member()
        self.gesehen()
        vorher = self.s.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
        self.assertEqual(kenntnis.abhaken(self.s, [{'id': mid}], 99, 'Lena'), {'mitglieder': 0})
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0], vorher)

    def test_abgehaktes_wird_nach_der_frist_geloescht(self):
        mid = self.member()
        self.gesehen()
        self.assertEqual(len(kenntnis.liste(self.s)['abgehakt']), 1)
        spaeter = date.today() + timedelta(days=kenntnis.FRIST + 1)
        self.assertEqual(kenntnis.liste(self.s, spaeter)['abgehakt'], [])
        self.assertEqual(kenntnis.bereinigen(self.s, spaeter), 1)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mitglied_aenderungen WHERE member_id=?', (mid,)).fetchone()[0], 0)


class HerkunftTests(Basis):
    """Prüfbefund 0.54: Was von Hand geändert wurde, darf nie unter fremdem Namen in einem Sammelpunkt verschwinden."""

    def importieren(self, actor, *ids, **neu):
        with self.s.db:
            for mid in ids:
                self.s.db.execute(f"UPDATE members SET {','.join(k + '=?' for k in neu)} WHERE id=?", (*neu.values(), mid))
            self.s.audit(actor, 'Mitgliederliste importiert', {'datei': 'liste.xlsx', 'neu': 0, 'aktualisiert': len(ids), 'ortsverbaende_neu': 0})

    def test_von_hand_und_import_am_selben_mitglied_sind_zwei_punkte(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Anna', iban='DE02120300000000202051')
        self.importieren('Bernd', mid, street='Importweg 3')
        punkte = {p['sammel'] is not None: p for p in self.offen()}
        self.assertEqual((punkte[False]['actor'], punkte[False]['anlass'], [f['feld'] for f in punkte[False]['felder']]), ('Anna', 'hand', ['iban']))
        self.assertEqual((punkte[True]['actor'], punkte[True]['anlass'], [f['feld'] for f in punkte[True]['felder']]), ('Bernd', 'import', ['street']))
        self.assertEqual(kenntnis.kurz(self.s.db), {'an': True, 'offen': [mid], 'punkte': 2})                                   # ein Mitglied, zwei Punkte
        stand = kenntnis.liste(self.s)['stand']
        kenntnis.abhaken(self.s, [{'id': mid, 'sammel': punkte[True]['sammel']}], stand, 'Lena')   # „Alle abhaken“ im Sammelpunkt
        [rest] = self.offen()
        self.assertEqual((rest['actor'], [f['feld'] for f in rest['felder']]), ('Anna', ['iban']))   # Annas Änderung bleibt offen
        kenntnis.abhaken(self.s, [{'id': mid}], stand, 'Lena')
        self.assertEqual(self.offen(), [])
        self.assertEqual({(a['actor'], a['anlass']) for a in kenntnis.liste(self.s)['abgehakt'] if a['gesehen_von'] == 'Lena'}, {('Anna', 'hand'), ('Bernd', 'import')})

    def test_mehrere_personen_stehen_alle_am_punkt(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Anna', city='Kiel')
        self.aendern(mid, 'Bernd', zip='24103')
        [p] = self.offen()
        self.assertEqual(p['actor'], 'Anna, Bernd')

    def test_loeschen_des_werbers_gehoert_der_loeschenden_person(self):
        werber = self.member()
        mid = self.member(geworben_von=werber)
        self.gesehen()
        mitglieder.delete(self.s, werber, 'Clara', arrears_ok=True)
        [p] = self.offen()
        self.assertEqual((p['id'], p['actor'], p['anlass'], [f['feld'] for f in p['felder']]), (mid, 'Clara', 'hand', ['geworben_von']))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mitglied_aenderungen WHERE actor IS NULL').fetchone()[0], 0)

    def test_iban_steht_in_der_liste_nur_verkuerzt(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Anna', iban='DE02120300000000202051')
        [f] = self.offen()[0]['felder']
        self.assertEqual((f['alt'], f['neu']), ('', 'DE02 •••• 2051'))


class EinstellungTests(Basis):
    def einstellen(self, **werte):
        with self.s.db:
            for key, value in werte.items():
                self.s.db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

    def test_vorgaben(self):
        self.assertEqual((stammdaten.DEFAULTS['kenntnis'], stammdaten.DEFAULTS['kenntnis_eigene']), ('yes', 'no'))

    def test_ausgeschaltet_haelt_nichts_fest(self):
        self.einstellen(kenntnis='no')
        mid = self.member()
        self.aendern(mid, 'Bernd', city='Kiel')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mitglied_aenderungen').fetchone()[0], 0)
        self.assertEqual(kenntnis.liste(self.s), {'an': False, 'offen': [], 'abgehakt': [], 'stand': 0, 'frist': kenntnis.FRIST})
        self.assertEqual(self.s.snapshot(2026)['kenntnis'], {'an': False, 'offen': [], 'punkte': 0})

    def test_ausschalten_loescht_festgehaltenes(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', iban='DE02120300000000202051')
        self.einstellen(kenntnis='no')
        self.assertEqual(kenntnis.bereinigen(self.s), self.n + 1)   # je Mitglied „angelegt“ und die offene Änderung
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mitglied_aenderungen').fetchone()[0], 0)

    def test_eigene_aenderungen_gelten_als_gesehen(self):
        rechte = mock.patch.object(rollen, 'rights_of', lambda db, name: {'mitglieder': 'schreiben' if name == 'Lena' else 'lesen'})
        rechte.start()
        self.addCleanup(rechte.stop)
        self.einstellen(kenntnis_eigene='yes')
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Lena', city='Kiel')     # darf Mitglieder ändern: gilt als gesehen
        self.assertEqual(self.offen(), [])
        self.assertEqual(kenntnis.liste(self.s)['abgehakt'][0]['gesehen_von'], 'Lena')
        self.aendern(mid, 'Gero', city='Bremen')   # ohne das Recht: bleibt offen
        self.assertEqual([p['actor'] for p in self.offen()], ['Gero'])

    def test_gesamtdaten_nennen_nur_kennungen(self):
        mid = self.member()
        self.assertEqual(self.s.snapshot(2026)['kenntnis'], {'an': True, 'offen': [mid], 'punkte': 1})
        ohne = rollen.filter_snapshot(self.s.snapshot(2026), {'buchhaltung': 'schreiben'})
        self.assertEqual(ohne['kenntnis'], {'an': False, 'offen': [], 'punkte': 0})


class SammelTests(Basis):
    def test_import_ist_ein_sammelpunkt(self):
        a, b = self.member(), self.member()
        self.gesehen()
        with self.s.db:
            self.s.db.execute("UPDATE members SET city='Kiel' WHERE id IN (?,?)", (a, b))
            self.s.audit('Bernd', 'Mitgliederliste importiert', {'datei': 'liste.xlsx', 'neu': 0, 'aktualisiert': 2, 'ortsverbaende_neu': 0})
        offen = self.offen()
        self.assertEqual({p['anlass'] for p in offen}, {'import'})
        self.assertEqual(len({p['sammel'] for p in offen}), 1)
        self.assertEqual(offen[0]['sammel_titel'], 'Import der Mitgliederliste')

    def test_von_hand_ist_kein_sammelpunkt(self):
        mid = self.member()
        self.gesehen()
        self.aendern(mid, 'Bernd', city='Kiel')
        self.assertIsNone(self.offen()[0]['sammel'])


if __name__ == '__main__':
    unittest.main()
