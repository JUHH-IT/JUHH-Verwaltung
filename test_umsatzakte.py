"""Notiz und Belege am Kontoumsatz (0.58, REGELN Nr. 40, Wunsch Lennert Gust 23.11.2026): eigene Ablage neben dem Grund für
„ungültig“, Dateiart und Größe wie bei allen Belegen, Kennzeichen in den Gesamtdaten, „Ausgaben ohne Beleg“, Prüfpaket,
Bereinigung und die Wege des Servers. Nur erfundene Daten."""
import base64
import csv
import http.cookiejar
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import app
import bereinigung
import berichte
import kasse
import test_server   # Passwort und schnelles Hashing der Tests
import umsatzakte
import verbindlichkeiten
import workflow
import zugang
from core import Store

PDF = b'%PDF-1.4 erfundener Beleg'
b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.belege = self.data / 'Belege'
        self.belege.mkdir()
        self.s = Store(self.data / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def umsatz(self, betrag='-50', tag='2026-06-01', name='Saal Muster GmbH', zweck='Rechnung 4711 Saalmiete'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{tag};{betrag};{name};{zweck};u{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f'auszug-{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def gebucht(self, betrag='-50', konto='4210', **mehr):
        bid = self.umsatz(betrag, **mehr)
        self.s.book(bid, [{'amount': betrag, 'account': konto, 'kv': 'LV'}], 'Test', confirm_no_bill=True)
        return bid

    def anheften(self, bid, name='Quittung.pdf', raw=PDF):
        """Wie der Server: prüfen, eintragen, Datei in den Belegordner schreiben."""
        suffix, inhalt = umsatzakte.datei(name, b64(raw))
        return umsatzakte.anheften(self.s, bid, name, suffix, len(inhalt), 'Lena', lambda rid, nummer, datei: (self.belege / datei).write_bytes(inhalt))

    def zeile(self, bid):
        return [b for b in self.s.snapshot(2026)['bank'] if b['id'] == bid][0]

    def protokoll(self, aktion):
        return [json.loads(r[0]) for r in self.s.db.execute('SELECT detail FROM audit WHERE action=? ORDER BY id', (aktion,))]


class NotizTests(Basis):
    def test_speichern_aendern_loeschen(self):
        bid = self.umsatz()
        self.assertEqual(umsatzakte.akte(self.s, bid), {'notiz': None, 'belege': []})
        self.assertFalse(self.zeile(bid)['hat_notiz'])
        n = umsatzakte.notiz(self.s, bid, '  Beleg folgt per Post.\r\nRückfrage beim Kreisverband  ', 'Lena')
        self.assertEqual((n['text'], n['von']), ('Beleg folgt per Post.\nRückfrage beim Kreisverband', 'Lena'))
        self.assertRegex(n['am'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$')
        zeile = self.zeile(bid)
        self.assertTrue(zeile['hat_notiz'])
        self.assertNotIn('Beleg folgt', json.dumps(zeile))                          # der Text steht nicht in den Gesamtdaten
        self.assertEqual(self.protokoll('Kontoumsatz: Notiz geändert'), [{'bank_id': bid, 'leer': False}])   # im Protokoll ohne den Text
        umsatzakte.notiz(self.s, bid, 'Beleg folgt per Post.\nRückfrage beim Kreisverband', 'Tom')   # unverändert: kein neuer Eintrag, wer bleibt
        self.assertEqual(umsatzakte.akte(self.s, bid)['notiz']['von'], 'Lena')
        self.assertEqual(len(self.protokoll('Kontoumsatz: Notiz geändert')), 1)
        self.assertEqual(umsatzakte.notiz(self.s, bid, 'Erledigt', 'Tom')['von'], 'Tom')
        self.assertIsNone(umsatzakte.notiz(self.s, bid, '   ', 'Tom'))               # leer = löschen
        self.assertFalse(self.zeile(bid)['hat_notiz'])
        self.assertEqual(self.protokoll('Kontoumsatz: Notiz geändert')[-1], {'bank_id': bid, 'leer': True})

    def test_grenzen(self):
        bid = self.umsatz()
        umsatzakte.notiz(self.s, bid, 'x' * umsatzakte.MAX_NOTIZ, 'Lena')
        for text in ('x' * (umsatzakte.MAX_NOTIZ + 1), None, 7):
            with self.assertRaises(ValueError):
                umsatzakte.notiz(self.s, bid, text, 'Lena')
        for fremd in (bid + 99, '1', None, True, 0, -1, 2 ** 70):   # 2**70: früher Fehler der Datenbank statt Meldung (Prüfung 0.58)
            with self.assertRaises(ValueError):
                umsatzakte.notiz(self.s, fremd, 'x', 'Lena')
            with self.assertRaises(ValueError):
                umsatzakte.akte(self.s, fremd)

    def test_notiz_ohne_steuerzeichen(self):
        """Steuer- und Richtungszeichen stehen sonst unverändert im Prüfpaket; der Zeilenumbruch bleibt."""
        bid = self.umsatz()
        umsatzakte.notiz(self.s, bid, 'Zeile 1\r\nZeile\x00 2\x1b\u202e Ende', 'Lena')
        self.assertEqual(umsatzakte.akte(self.s, bid)['notiz']['text'], 'Zeile 1\nZeile 2 Ende')

    def test_neben_dem_grund_fuer_ungueltig(self):
        bid = self.umsatz()
        umsatzakte.notiz(self.s, bid, 'Bank hat doppelt geliefert', 'Lena')
        workflow.ungueltig(self.s, bid, 'doppelt importiert', 'Lena')
        self.assertEqual(self.s.db.execute('SELECT note FROM bank WHERE id=?', (bid,)).fetchone()[0], 'doppelt importiert')
        self.assertEqual(umsatzakte.akte(self.s, bid)['notiz']['text'], 'Bank hat doppelt geliefert')
        workflow.gueltig(self.s, bid, 'Lena')                                        # der Grund geht, die Notiz bleibt
        self.assertEqual(umsatzakte.akte(self.s, bid)['notiz']['text'], 'Bank hat doppelt geliefert')

    def test_nur_kontoumsaetze(self):
        kasse.set_opening(self.s, '2026-01-01', '100', 'Lena')
        k = kasse.create(self.s, {'kind': 'out', 'amount': '5', 'day': '2026-01-02', 'text': 'Porto'}, 'Lena')
        with self.assertRaises(ValueError):
            umsatzakte.notiz(self.s, k['id'], 'x', 'Lena')
        with self.assertRaises(ValueError):
            self.anheften(k['id'])
        zeile = [b for b in self.s.snapshot(2026)['bank'] if b['id'] == k['id']][0]
        self.assertEqual((zeile['hat_notiz'], zeile['belege'], zeile['ohne_beleg']), (False, 0, False))


class BelegTests(Basis):
    def test_dateiart_und_groesse(self):
        self.assertEqual(umsatzakte.datei('a.PDF', b64(PDF)), ('.pdf', PDF))
        self.assertEqual(umsatzakte.datei('foto.jpeg', b64(b'\xff\xd8\xff\xe0 Bild'))[0], '.jpg')
        self.assertEqual(umsatzakte.datei('scan.png', b64(b'\x89PNG Bild'))[0], '.png')
        for name, raw in (('rechnung.xml', b'<Invoice/>'), ('seite.html', b'<html>'), ('makro.docm', b'PK'), ('ohne-endung', PDF), ('a.pdf.exe', PDF),
                          ('getarnt.pdf', b'MZ kein PDF'), ('getarnt.png', PDF), ('zu-gross.pdf', b'%PDF' + b'0' * verbindlichkeiten.MAX_FILE)):
            with self.assertRaises(ValueError, msg=name):
                umsatzakte.datei(name, b64(raw))
        with self.assertRaises(ValueError):
            umsatzakte.datei('a.pdf', 'kein base64 !')
        xrechnung = b64((Path(__file__).parent / 'tests' / 'beispiel-xrechnung.xml').read_bytes())   # als Eingangsrechnung erlaubt, hier nicht
        self.assertEqual(verbindlichkeiten.check_file('rechnung.xml', xrechnung)[0], '.xml')
        with self.assertRaises(ValueError):
            umsatzakte.datei('rechnung.xml', xrechnung)

    def test_anheften_ansehen_entfernen(self):
        bid = self.umsatz()
        eins = self.anheften(bid, 'Quittung Saal.pdf')
        zwei = self.anheften(bid, '../../etc/Foto vom Bon.JPG', b'\xff\xd8\xff Bild')
        belege = umsatzakte.akte(self.s, bid)['belege']
        self.assertEqual([(x['id'], x['name'], x['size'], x['von']) for x in belege],
                         [(eins, 'Quittung Saal.pdf', len(PDF), 'Lena'), (zwei, 'Foto vom Bon.JPG', 8, 'Lena')])   # nur der Dateiname, kein Pfad
        self.assertEqual(sorted(f.name for f in self.belege.iterdir()), [f'Umsatz-{bid}-{eins}.pdf', f'Umsatz-{bid}-{zwei}.jpg'])
        self.assertEqual(self.zeile(bid)['belege'], 2)
        self.assertNotIn('Quittung Saal', json.dumps(self.zeile(bid)))               # Dateinamen nur auf Abruf
        b = umsatzakte.beleg(self.s, eins, self.belege)
        self.assertEqual(b['pfad'].read_bytes(), PDF)
        self.assertEqual(umsatzakte.entfernen(self.s, eins, self.belege, 'Tom'), bid)
        self.assertEqual([f.name for f in self.belege.iterdir()], [f'Umsatz-{bid}-{zwei}.jpg'])   # die Datei ist wirklich gelöscht
        self.assertEqual([x['id'] for x in umsatzakte.akte(self.s, bid)['belege']], [zwei])
        self.assertEqual(self.protokoll('Kontoumsatz: Beleg angeheftet')[0], {'bank_id': bid, 'beleg': eins, 'datei': 'Quittung Saal.pdf'})
        self.assertEqual(self.protokoll('Kontoumsatz: Beleg entfernt'), [{'bank_id': bid, 'beleg': eins, 'datei': 'Quittung Saal.pdf'}])
        for fremd in (eins, 9999, '1', None, True):                                  # entfernt, unbekannt, keine Kennung
            with self.assertRaises(ValueError):
                umsatzakte.entfernen(self.s, fremd, self.belege, 'Tom')
            with self.assertRaises(ValueError):
                umsatzakte.beleg(self.s, fremd, self.belege)
        umsatzakte.entfernen(self.s, zwei, self.belege, 'Tom')
        drei = self.anheften(bid)                                                    # Kennung und Dateiname werden nie neu vergeben
        self.assertGreater(drei, zwei)

    def test_datei_ausserhalb_oder_fehlend(self):
        bid = self.umsatz()
        rid = self.anheften(bid)
        (self.data / 'geheim.pdf').write_bytes(PDF)
        self.s.db.execute("UPDATE umsatz_belege SET attachment='../geheim.pdf' WHERE id=?", (rid,))
        self.s.db.commit()
        self.assertIsNone(umsatzakte.beleg(self.s, rid, self.belege)['pfad'])        # nur der Dateiname zählt – nichts außerhalb des Ordners
        umsatzakte.entfernen(self.s, rid, self.belege, 'Tom')                        # der Eintrag geht, die fremde Datei bleibt
        self.assertTrue((self.data / 'geheim.pdf').exists())

    def test_speichern_scheitert_ohne_reste(self):
        bid = self.umsatz()

        def kaputt(rid, nummer, datei):
            raise ValueError('Platte voll')
        with self.assertRaises(ValueError) as cm:
            umsatzakte.anheften(self.s, bid, 'a.pdf', '.pdf', 5, 'Lena', kaputt)
        self.assertIn('nicht gespeichert', str(cm.exception))
        self.assertEqual((umsatzakte.akte(self.s, bid)['belege'], self.protokoll('Kontoumsatz: Beleg angeheftet')), ([], []))

    def test_hoechstzahl(self):
        bid = self.umsatz()
        for _ in range(umsatzakte.MAX_BELEGE):
            self.anheften(bid)
        with self.assertRaises(ValueError):
            self.anheften(bid)


class OhneBelegTests(Basis):
    def rechnung(self, betrag, mit_datei):
        bid, number, filename = verbindlichkeiten.create(self.s, {'creditor': 'Druckerei Muster GmbH', 'ext_number': f'RE-{betrag}', 'issued': '2026-05-20', 'kv': 'LV',
                                                                   'lines': [{'description': 'Flyer', 'amount': betrag, 'account': '4210'}]}, 'Test', '.pdf' if mit_datei else None)
        return bid, number

    def test_regel(self):
        ausgabe, mit_beleg, eingang, offen, ungueltig, geparkt = (self.gebucht('-50'), self.gebucht('-20'), self.gebucht('30', '8200'), self.umsatz('-7'),
                                                                   self.umsatz('-8'), self.umsatz('-9'))
        self.anheften(mit_beleg)
        workflow.ungueltig(self.s, ungueltig, 'doppelt importiert', 'Test')
        workflow.park(self.s, geparkt, 'Beleg fehlt', 'Test')
        mit_datei, ohne_datei = self.rechnung('40', True), self.rechnung('45', False)
        an_rechnung, an_rechnung_ohne = self.umsatz('-40'), self.umsatz('-45')
        self.s.book(an_rechnung, [{'amount': '-40', 'bill_id': mit_datei[0]}], 'Test')
        self.s.book(an_rechnung_ohne, [{'amount': '-45', 'bill_id': ohne_datei[0]}], 'Test')
        zeilen = {b['id']: b for b in self.s.snapshot(2026)['bank']}
        self.assertEqual({i for i, b in zeilen.items() if b['ohne_beleg']}, {ausgabe, an_rechnung_ohne})
        self.assertEqual(umsatzakte.ohne_beleg(self.s, 2026), 2)
        self.assertEqual(umsatzakte.ohne_beleg(self.s, 2025), 0)
        self.assertEqual(zeilen[an_rechnung]['bezug'], {'art': 'eingangsrechnung', 'titel': f'Eingangsrechnung {mit_datei[1]}', 'zusatz': 'Druckerei Muster GmbH'})
        self.assertNotIn('bezug', zeilen[ausgabe])
        self.s.reverse_bank(mit_beleg, 'Test')                                       # wieder offen: zählt nicht, der Beleg bleibt am Umsatz
        zeile = self.zeile(mit_beleg)
        self.assertEqual((zeile['state'], zeile['belege'], zeile['ohne_beleg']), ('open', 1, False))
        self.anheften(ausgabe)
        self.assertFalse(self.zeile(ausgabe)['ohne_beleg'])

    def test_umbuchung_auf_eigenes_geldkonto_zaehlt_nicht(self):
        """0.60: Abbuchung von PayPal direkt auf das Konto der Kasse, Geldtransit 2380, Bareinzahlung auf 1400 – Umbuchungen auf
        eigene Geldkonten sind keine Ausgaben ohne Beleg. Gemischt mit einer Ausgabe oder neu zugeordnet zählt der Umsatz."""
        kasse.set_opening(self.s, '2026-01-01', '100', 'Test')
        kasse.add(self.s, {'name': 'PayPal', 'account': '1430', 'prefix': 'PP', 'art': 'dienst', 'day': '2026-01-01', 'amount': '0'}, 'Test')
        paypal, transit, bar, ausgabe = self.gebucht('-12,50', '1430'), self.gebucht('-20', '2380'), self.gebucht('-30', '1400'), self.gebucht('-50')
        gemischt = self.umsatz('-60')
        self.s.book(gemischt, [{'amount': '-40', 'account': '2380', 'kv': 'LV'}, {'amount': '-20', 'account': '4210', 'kv': 'LV'}], 'Test', confirm_no_bill=True)
        geparkt = self.umsatz('-9')                                                   # erst geparkt, dann auf PayPal aufgelöst
        workflow.park(self.s, geparkt, 'Beleg fehlt', 'Test')
        self.s.book(geparkt, [{'amount': '-9', 'account': '1430', 'kv': 'LV'}], 'Test')
        ohne = lambda: {b['id'] for b in self.s.snapshot(2026)['bank'] if b['ohne_beleg']}  # noqa: E731
        self.assertEqual(ohne(), {ausgabe, gemischt})
        self.assertEqual(umsatzakte.ohne_beleg(self.s, 2026), 2)
        self.s.reverse_bank(transit, 'Test')                                          # es gilt die geltende Zuordnungsrunde
        self.s.book(transit, [{'amount': '-20', 'account': '4210', 'kv': 'LV'}], 'Test', confirm_no_bill=True)
        self.s.reverse_bank(ausgabe, 'Test')
        self.s.book(ausgabe, [{'amount': '-50', 'account': '1430', 'kv': 'LV'}], 'Test')
        self.assertEqual(ohne(), {transit, gemischt})
        self.assertEqual(umsatzakte.ohne_beleg(self.s, 2026), 2)

    def test_bezug_beitrag(self):
        eins, zwei = (self.s.member({'number': f'M{i}', 'first_name': v, 'last_name': 'Muster', 'street': 'Weg 1', 'zip': '20001', 'city': 'Hamburg',
                                     'email': '', 'birth': '2000-02-01', 'joined': '2024-01-01', 'kv': '5100'}, 'Test') for i, v in enumerate(('Erika', 'Max')))
        einzeln, sammel = self.umsatz('24', name='Erika Muster', zweck='Beitrag'), self.umsatz('48', name='Sammler', zweck='Beiträge')
        self.s.book(einzeln, [{'amount': '24', 'account': '8100', 'kv': '5100', 'members': [{'member_id': eins}]}], 'Test')
        self.s.book(sammel, [{'amount': '48', 'account': '8100', 'kv': '5100', 'members': [{'member_id': eins, 'amount': '24'}, {'member_id': zwei, 'amount': '24'}]}], 'Test')
        self.assertEqual(self.zeile(einzeln)['bezug'], {'art': 'beitrag', 'titel': '', 'zusatz': 'Erika Muster'})
        self.assertEqual(self.zeile(sammel)['bezug']['zusatz'], '2 Beiträge den Mitgliedern zugeordnet')
        self.s.reverse_bank(einzeln, 'Test')                                         # stornierte Runde zählt nicht mehr
        self.assertNotIn('bezug', self.zeile(einzeln))
        stand = self.s.snapshot(2026)['bank_stand']
        self.assertEqual(stand['bis'], '2026-06-01')
        self.assertRegex(stand['am'], r'^\d{4}-\d\d-\d\dT')


class PruefpaketTests(Basis):
    def dateien(self):
        out = io.BytesIO()
        berichte.audit_package(self.s, 2026, self.data, out)
        z = zipfile.ZipFile(out)
        return {n.split('/', 1)[1]: z.read(n) for n in z.namelist()}

    def test_notiz_und_belege_im_paket(self):
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00', 'age_basis': 'jan1',
                              'invoice_ready': 'yes', 'tax_note': 'Fiktiv'}, 'Test')
        mit, ohne, fehlt, alt = self.gebucht('-50'), self.gebucht('-20'), self.gebucht('-30'), self.gebucht('-9', tag='2025-12-30')
        rid = self.anheften(mit, 'Rechnung 4711.pdf')
        weg = self.anheften(fehlt, 'verloren.pdf')
        self.anheften(alt, 'vorjahr.pdf')
        (self.belege / f'Umsatz-{fehlt}-{weg}.pdf').unlink()
        umsatzakte.notiz(self.s, mit, '=SUMME(A1) Original folgt', 'Lena')
        f = self.dateien()
        self.assertEqual(f[f'Umsatzbelege/Umsatz-{mit}-{rid}.pdf'], PDF)
        self.assertEqual([n for n in f if n.startswith('Umsatzbelege/')], [f'Umsatzbelege/Umsatz-{mit}-{rid}.pdf'])   # nicht die des Vorjahres, nicht die fehlende
        zeilen = list(csv.reader(io.StringIO(f['Kontoumsaetze-2026.csv'].decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(zeilen[0][-2:], ['Notiz', 'Belege (Ordner Umsatzbelege)'])
        je = {int(r[0]): r for r in zeilen[1:]}
        self.assertEqual(je[mit][-2:], ["'=SUMME(A1) Original folgt", f'Umsatz-{mit}-{rid}.pdf (Rechnung 4711.pdf)'])   # Formelschutz gilt auch für die Notiz
        self.assertEqual(je[ohne][-2:], ['', ''])
        import pypdf
        text = ' '.join(' '.join(p.extract_text().split()) for p in pypdf.PdfReader(io.BytesIO(f['00-Uebersicht.pdf'])).pages)
        self.assertIn('Belegdatei fehlt (verloren.pdf)', text)
        self.assertIn('1 verbuchte Ausgabe im Kontoauszug ohne Beleg', text)         # der Umsatz mit fehlender Datei hat seinen eigenen Hinweis


class BereinigungTests(Basis):
    def test_notiz_und_belege_gehen_mit(self):
        alt, neu = self.gebucht('-12,34', konto='4290', tag='2024-12-30'), self.gebucht('-7', tag='2025-01-02')
        rid = self.anheften(alt, 'alt.pdf')
        bleibt = self.anheften(neu, 'neu.pdf')
        umsatzakte.notiz(self.s, alt, 'GEHEIM-NOTIZ-2024', 'Lena')
        umsatzakte.notiz(self.s, neu, 'bleibt', 'Lena')
        ung = self.umsatz('-5', tag='2024-11-01')
        workflow.ungueltig(self.s, ung, 'GEHEIM-GRUND-2024', 'Lena')
        ergebnis = bereinigung.purge_before(self.s, '2025-01-01', 'Test')
        self.assertEqual(ergebnis['umsatz_belege_von_hand_loeschen'], [f'Umsatz-{alt}-{rid}.pdf'])
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT bank_id FROM umsatz_notizen')], [neu])
        self.assertEqual([r[0] for r in self.s.db.execute('SELECT id FROM umsatz_belege')], [bleibt])
        protokoll = ' '.join(r[0] + r[1] for r in self.s.db.execute('SELECT action,detail FROM audit'))
        self.assertNotIn('GEHEIM', protokoll)                                        # auch der Grund für „ungültig“ (0.55) steht nicht mehr im Protokoll
        self.assertNotIn('alt.pdf', protokoll)
        self.assertIn('neu.pdf', protokoll)
        self.assertEqual(self.s.db.execute('PRAGMA foreign_key_check').fetchall(), [])


class ServerTests(unittest.TestCase):
    """Die Wege am laufenden Server: Anheften über app.save_receipt, Ansehen als Datei, Entfernen, CSRF."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        app.DATA = Path(cls.tmp.name)
        app.DB = app.DATA / 'finanzen.sqlite3'
        (app.DATA / 'Importe').mkdir()
        app.BACKUPS = app.DATA / 'Sicherungen-Test'
        store = Store(app.DB)
        try:
            zugang.set_password(store.db, 'Schatzmeisterin Test', test_server.PASSWORT, create=True)
            for betrag, ledger in ((-5000, 'bank'), (-700, 'cash')):
                store.db.execute("INSERT INTO bank(booked,amount,name,reference,fingerprint,ledger,bank_ref) VALUES('2026-03-01',?,'Saal Muster GmbH','Miete',?,?,?)",
                                 (betrag, f'f{betrag}', ledger, 'K-2026-0001' if ledger == 'cash' else None))
            store.db.commit()
            cls.bank, cls.kasse = (r[0] for r in store.db.execute('SELECT id FROM bank ORDER BY id'))
        finally:
            store.db.close()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def test_wege(self):
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def request(path, data=None, csrf=None):
            headers = {'Origin': self.url, **({'Content-Type': 'application/json'} if data is not None else {}), **({'X-CSRF-Token': csrf} if csrf else {})}
            return client.open(urllib.request.Request(self.url + path, json.dumps(data).encode() if data is not None else None, headers))

        def fehler(path, data=None, csrf=None):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                request(path, data, csrf)
            return cm.exception.code, json.load(cm.exception).get('error', '')

        self.assertEqual(fehler(f'/api/umsatz-akte?id={self.bank}')[0], 401)        # ohne Sitzung nichts
        self.assertEqual(fehler('/api/umsatz-beleg?id=1')[0], 401)
        csrf = json.load(request('/api/login', {'name': 'Schatzmeisterin Test', 'password': test_server.PASSWORT}))['csrf']
        datei = {'name': 'Rechnung Saal.pdf', 'content': b64(PDF)}
        for path, data in (('/api/umsatz-notiz', {'id': self.bank, 'text': 'x'}), ('/api/umsatz-beleg', {'id': self.bank, 'file': datei}),
                           ('/api/umsatz-beleg-entfernen', {'id': 1})):
            self.assertEqual(fehler(path, data)[0], 403, path)                       # ohne CSRF
        self.assertEqual(json.load(request('/api/umsatz-notiz', {'id': self.bank, 'text': 'Beleg folgt'}, csrf))['notiz']['von'], 'Schatzmeisterin Test')
        eins = json.load(request('/api/umsatz-beleg', {'id': self.bank, 'file': datei}, csrf))['id']
        zwei = json.load(request('/api/umsatz-beleg', {'id': self.bank, 'file': {'name': 'bon.png', 'content': b64(b'\x89PNG Bild')}}, csrf))['id']
        akte = json.load(request(f'/api/umsatz-akte?id={self.bank}'))
        self.assertEqual((akte['notiz']['text'], [x['name'] for x in akte['belege']]), ('Beleg folgt', ['Rechnung Saal.pdf', 'bon.png']))
        antwort = request(f'/api/umsatz-beleg?id={eins}')
        self.assertEqual((antwort.read(), antwort.headers['Content-Type'], antwort.headers['Content-Disposition']),
                         (PDF, 'application/pdf', f'inline; filename="Umsatz-{self.bank}-{eins}.pdf"'))
        self.assertEqual(antwort.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(request(f'/api/umsatz-beleg?id={zwei}').headers['Content-Type'], 'image/png')
        self.assertEqual(sorted(f.name for f in (app.DATA / 'Belege').iterdir()), [f'Umsatz-{self.bank}-{eins}.pdf', f'Umsatz-{self.bank}-{zwei}.png'])
        for kaputt in ('text', 7, ['x'], {'name': 7, 'content': 'x'}, {'name': 'a.pdf'}, None):   # früher 500 (Prüfung 0.58)
            self.assertEqual(fehler('/api/umsatz-beleg', {'id': self.bank, 'file': kaputt}, csrf)[0], 400, kaputt)
        self.assertEqual(fehler(f'/api/umsatz-akte?id={2 ** 70}')[0], 400)
        self.assertEqual(fehler(f'/api/umsatz-beleg?id={2 ** 70}')[0], 400)
        zeile = [b for b in json.load(request('/api/data?year=2026'))['bank'] if b['id'] == self.bank][0]
        self.assertEqual((zeile['hat_notiz'], zeile['belege']), (True, 2))
        for name, raw, teil in (('a.xml', b'<x/>', 'PDF, PNG oder JPG'), ('a.pdf', b'kein pdf', 'passt nicht'), ('a.exe', PDF, 'PDF, PNG oder JPG')):
            self.assertIn(teil, fehler('/api/umsatz-beleg', {'id': self.bank, 'file': {'name': name, 'content': b64(raw)}}, csrf)[1])
        self.assertEqual(fehler('/api/umsatz-beleg', {'id': self.bank, 'file': {'name': 'gross.pdf', 'content': b64(b'%PDF' + b'0' * 10_000_000)}}, csrf),
                         (400, 'Beleg ist zu groß (höchstens 10 MB).'))
        for fremd in (self.kasse, 999999, 'x', None):                                # Kassenbeleg, unbekannt, keine Kennung
            self.assertEqual(fehler('/api/umsatz-beleg', {'id': fremd, 'file': datei}, csrf)[0], 400)
            self.assertEqual(fehler('/api/umsatz-notiz', {'id': fremd, 'text': 'x'}, csrf)[0], 400)
        for fremd in (999999, 'x', None, True):                                      # kein Beleg mit dieser Kennung
            self.assertEqual(fehler('/api/umsatz-beleg-entfernen', {'id': fremd}, csrf)[0], 400)
        for adresse in (f'/api/umsatz-akte?id={self.kasse}', '/api/umsatz-akte?id=abc', '/api/umsatz-beleg?id=999999', '/api/umsatz-beleg?id=abc'):
            self.assertEqual(fehler(adresse)[0], 400, adresse)
        self.assertEqual(sorted(f.name for f in (app.DATA / 'Belege').iterdir()), [f'Umsatz-{self.bank}-{eins}.pdf', f'Umsatz-{self.bank}-{zwei}.png'])   # nichts Abgelehntes abgelegt
        self.assertEqual(json.load(request('/api/umsatz-beleg-entfernen', {'id': eins}, csrf)), {'bank_id': self.bank})
        self.assertEqual(fehler(f'/api/umsatz-beleg?id={eins}'), (400, 'Beleg nicht gefunden.'))
        self.assertEqual([f.name for f in (app.DATA / 'Belege').iterdir()], [f'Umsatz-{self.bank}-{zwei}.png'])


if __name__ == '__main__':
    unittest.main()
