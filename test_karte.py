"""Karte und amtliche Adressdaten (0.32) – nur erfundene Adressen und Mitglieder, kein Netzzugriff."""
import hashlib
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

import adressen
import karte
from core import Store

REPO = Path(__file__).resolve().parent


def feature(street, nr, plz, bezirk, stadtteil, lon, lat, zusatz=None):
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {'strassenname': street, 'hausnummer': str(nr), 'hausnummernzusatz': zusatz, 'postleitzahl': plz, 'bezirke': bezirk, 'postOrtsteil': stadtteil}}


def fixtures():
    """Erfundener Mini-Bestand: zwei Bezirke, eine gleichnamige Straße in zwei Stadtteilen."""
    out = [feature('Testallee', n, '20001', 'Altona', 'Teststadt-West', 9.90 + n / 10000, 53.55) for n in range(1, 21)]
    out += [feature('Testallee', 7, '20001', 'Altona', 'Teststadt-West', 9.9008, 53.5501, 'a')]
    out += [feature('Beispielstraße', n, '20002', 'Eimsbüttel', 'Teststadt-Nord', 9.95 + n / 10000, 53.58) for n in range(1, 11)]
    out += [feature('Doppelweg', n, '20003', 'Altona', 'Teststadt-West', 9.91, 53.56) for n in (1, 2, 3)]
    out += [feature('Doppelweg', n, '20004', 'Eimsbüttel', 'Teststadt-Nord', 9.96, 53.59) for n in (1, 2, 3)]
    out += [feature('Mühlenkamp', 5, '20002', 'Eimsbüttel', 'Teststadt-Nord', 9.955, 53.581)]
    out += [feature('Langer Weg', n, '20005', 'Altona', 'Teststadt-West', 9.92 + n / 1000, 53.57) for n in range(1, 21)]      # zwei PLZ-Abschnitte
    out += [feature('Langer Weg', n, '20006', 'Eimsbüttel', 'Teststadt-Nord', 9.913 + n / 1000, 53.57) for n in range(21, 41)]
    out += [feature('Platz am 9. Testgrad', n, '20002', 'Eimsbüttel', 'Teststadt-Nord', 9.956, 53.582) for n in (1, 2)]
    out += [feature('Gotenstraße', n, '20007', 'Altona', 'Teststadt-West', 9.925, 53.56) for n in (1, 2, 3)]
    out += [feature('Hofweg', 5, '20007', 'Altona', 'Teststadt-West', lon, 53.561, zusatz) for lon, zusatz in ((9.931, 'b'), (9.932, 'Haus 2'), (9.93, None))]
    return out


def ui_fixtures():
    """Bestand für die Browser-Durchläufe (tests/ui_start.py): dazu die Straßen der dortigen Mustermitglieder, Lage mitten in Hamburg."""
    out = fixtures()
    out += [feature('Musterweg', n, '20000', 'Altona', 'Ottensen', 9.925 + n / 2000, 53.553) for n in range(1, 11)]
    out += [feature('Neuer Weg', n, '20000', 'Eimsbüttel', 'Eimsbüttel', 9.955 + n / 2000, 53.575) for n in range(1, 11)]
    out += [feature('Testweg', n, '22301', 'Hamburg-Nord', 'Winterhude', 10.0 + n / 2000, 53.59) for n in range(1, 6)]   # „Weg 2, 22301“ gibt es nicht
    return out


class AddressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        wait, cool = adressen.RETRY_WAIT, adressen.COOLDOWN
        adressen.RETRY_WAIT, adressen.COOLDOWN = (0, 0, 0), 0   # Tests warten nicht zwischen den Versuchen und nach einem Abruf
        self.addCleanup(lambda: (setattr(adressen, 'RETRY_WAIT', wait), setattr(adressen, 'COOLDOWN', cool), adressen._job.pop('finished', None)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_spelling(self):
        best = lambda text: next((c for c in adressen.candidates(text) if c[1] is not None), None)   # noqa: E731
        self.assertEqual(adressen.candidates('Platz am 10. Längengrad 1')[:2], [('Platz am 10. Längengrad 1', None, '', ''), ('Platz am 10. Längengrad', 1, '', '')])
        self.assertEqual(best('c/o Beispiel, Musterweg 7'), ('Musterweg', 7, '', ''))
        self.assertEqual(best('7 Musterweg'), ('Musterweg', 7, '', ''))
        self.assertEqual(best('12-14 Musterweg'), ('Musterweg', 12, '', ''))
        self.assertIn(('Musterweg', 7, '', ''), adressen.candidates('Musterweg Nr. 7'))
        self.assertIn(('Musterweg', 12, '', 'haus 3'), adressen.candidates('Musterweg 12 Haus 3'))
        self.assertEqual(adressen.candidates(''), [])
        self.assertEqual(adressen.norm_street('Sankt Test Markt'), adressen.norm_street('St. Test-Markt'))
        for text, expected in (('Musterstraße 12 a', ('Musterstraße', 12, 'a')), ('Musterstr. 12a', ('Musterstr.', 12, 'a')), ('Beim Schlump 3', ('Beim Schlump', 3, '')),
                               ('Am Markt 1-3', ('Am Markt', 1, '')), ('Weg', ('Weg', None, '')), ('Lange Reihe 12, 3. OG', ('Lange Reihe', 12, '')),
                               ('  Elbchaussee   100b ', ('Elbchaussee', 100, 'b')), ('', ('', None, '')), ('12', ('12', None, ''))):
            self.assertEqual(adressen.split_street(text), expected, text)
        same = {adressen.norm_street(x) for x in ('Mönckebergstraße', 'Moenckebergstrasse', 'mönckebergstr.', 'Mönckeberg-Straße', 'MÖNCKEBERGSTR')}
        self.assertEqual(len(same), 1)
        self.assertTrue(adressen.is_hamburg('') and adressen.is_hamburg('Hamburg-Altona') and adressen.is_hamburg('HH') and adressen.is_hamburg('Hansestadt Hamburg')
                        and adressen.is_hamburg('22765 Hamburg') and not adressen.is_hamburg('Schenefeld'))

    def test_build_and_lookup(self):
        self.assertEqual(adressen.check(self.dir, 'Testallee 1', '20001', 'Hamburg')['status'], 'keine_daten')
        self.assertEqual((adressen.status(self.dir)['available'], adressen.status(self.dir)['uebergangen']), (False, 0))
        bad = [{'properties': {'strassenname': 'Ohne Lage', 'hausnummer': '1', 'postleitzahl': '20001'}, 'geometry': None}, 'kein Objekt',
               feature('Straßennname unbekannt', 1, '25482', 'Altona', 'X', 9.9, 53.5),      # Platzhalter der Stadt: keine Anschrift, keine Hamburger PLZ
               feature('Straßenname unbekannt', 2, '25482', 'Altona', 'X', 9.9, 53.5),
               feature('Draußen', 1, '20001', 'Altona', 'X', 8.5, 53.9), feature('Ohne PLZ', 1, 'abc', 'Altona', 'X', 9.9, 53.5), feature('Null', 0, '20001', 'Altona', 'X', 9.9, 53.5)]
        rows = adressen.build(adressen.path(self.dir), fixtures() + fixtures()[:5] + bad, stand='2026-11-10T12:00:00')
        self.assertEqual(rows, len(fixtures()))                                          # Doppelte und Unbrauchbares bleiben weg
        st = adressen.status(self.dir)
        self.assertEqual((st['available'], st['adressen'], st['strassen'], st['stand']), (True, len(fixtures()), 8, '2026-11-10T12:00:00'))
        db = adressen.connect(self.dir)
        self.assertEqual(dict(db.execute("SELECT key,value FROM meta WHERE key='uebergangen'").fetchall()), {'uebergangen': '7'})
        self.assertEqual(adressen.status(self.dir)['uebergangen'], 7)
        db.close()
        self.assertFalse(list(self.dir.glob('*.neu')))
        ix = adressen.index(self.dir)
        self.addCleanup(ix.close)
        r = ix.lookup('Testallee 7a', '20001', 'Hamburg')
        self.assertEqual((r['status'], r['lat'], r['bezirk'], r['stadtteil'], r['kv'], r['street']), ('haus', 53.5501, 'Altona', 'Teststadt-West', '5100', 'Testallee'))
        self.assertEqual(ix.lookup('testallee 7', '20001')['lat'], 53.55)                # ohne Zusatz: die Nummer selbst
        self.assertEqual(ix.lookup('Test-Allee 7 b', '20001', 'HH')['status'], 'haus')   # Zusatz unbekannt: Hausnummer zählt
        r = ix.lookup('Testallee 99', '20001', 'Hamburg')
        self.assertEqual((r['status'], r['lon']), ('strasse', 9.902))                    # nächste Hausnummer (20)
        self.assertEqual(ix.lookup('Testallee', '20001', 'Hamburg')['status'], 'strasse')
        r = ix.lookup('Testallee 3', '20009', 'Hamburg')
        self.assertEqual((r['status'], r['zips'], r['lat'] is not None), ('plz', ['20001'], True))
        r = ix.lookup('Doppelweg 2', '20009', 'Hamburg')                                 # gleichnamige Straße, PLZ passt zu keiner: Lage offen
        self.assertEqual((r['status'], r['zips'], r['lat']), ('plz', ['20003', '20004'], None))
        # Prüfbefund 0.32: lange Straße mit zwei PLZ-Abschnitten – die Hausnummer entscheidet, nicht die PLZ
        r = ix.lookup('Langer Weg 30', '20005', 'Hamburg')                               # Nr. 30 liegt im Abschnitt 20006
        self.assertEqual((r['status'], r['zips'], r['lon'], r['kv']), ('plz', ['20006'], 9.943, '5300'))
        self.assertEqual(ix.lookup('Langer Weg 30', '20006', 'Hamburg')['status'], 'haus')
        self.assertEqual((ix.lookup('Langer Weg 30', '', 'Hamburg')['status'], ix.lookup('Langer Weg 30', '', 'Hamburg')['zip']), ('haus', '20006'))
        r = ix.lookup('Langer Weg 99', '20005', 'Hamburg')                               # Nummer gibt es nirgends: nächste im genannten Abschnitt
        self.assertEqual((r['status'], r['zip']), ('strasse', '20005'))
        r = ix.lookup('Langer Weg', '20009', 'Hamburg')
        self.assertEqual((r['status'], r['zips'], r['lat']), ('plz', ['20005', '20006'], None))
        self.assertEqual(ix.lookup('Platz am 9. Testgrad 2', '20002', 'Hamburg')['status'], 'haus')     # amtlicher Name mit Ziffer
        self.assertEqual(ix.lookup('c/o Beispiel, Testallee 7', '20001', 'Hamburg')['lat'], 53.55)
        self.assertEqual(ix.lookup('7 Testallee', '20001', 'Hamburg')['status'], 'haus')
        self.assertEqual(ix.lookup('Testalle 3', '20001', 'Teststadt-West')['status'], 'aehnlich')
        self.assertEqual(ix.lookup('Testalle 3', '20001', 'HH-Teststadt-West')['status'], 'aehnlich')
        self.assertEqual(ix.lookup('Erfundene Gasse 3', '20001', 'HH Teststadt-West')['status'], 'unbekannt')
        # Nachprüfung 0.32: ein Stadtteilname als Ort zählt nur mit Hamburger PLZ (Hamm, Neustadt, Horn gibt es auch anderswo)
        self.assertEqual(ix.lookup('Testallee 3', '59065', 'Teststadt-West')['status'], 'ausserhalb')
        self.assertEqual(ix.lookup('Testallee 3', '59065', 'Hamburg')['status'], 'plz')            # Ort Hamburg: PLZ vertippt
        self.assertEqual(ix.lookup('Testalle 3', '20001', 'Pinneberg')['status'], 'ausserhalb')    # anderer Ort: keine Vermutung
        # Tippfehler folgt derselben Regel wie die richtige Schreibweise: die Hausnummer entscheidet über die Lage
        r = ix.lookup('Lange Weg 30', '20005', 'Hamburg')
        self.assertEqual((r['status'], r['lon'], r['suggestions']), ('aehnlich', 9.943, [{'street': 'Langer Weg', 'zip': '20006'}]))
        r = ix.lookup('Whg. 4, Testalle 3', '20001', 'Hamburg')
        self.assertEqual((r['status'], r['typed'], r['lon']), ('aehnlich', 'Testalle', 9.9003))
        self.assertEqual(ix.lookup('Testalle 3, Hinterhaus 12', '20001', 'Hamburg')['lon'], 9.9003)   # Zusatz mit längerem Namen und Zahl
        self.assertEqual(ix.lookup('Erfundene Gasse 3', '20001', 'Hamburg')['typed'], 'Erfundene Gasse')
        self.assertEqual(ix.lookup('Testallee 3', '2O001', 'Teststadt-West')['status'], 'plz')        # PLZ vertippt, Stadtteil als Ort
        self.assertEqual(ix.lookup('Hofweg 5 Haus 22', '20007', 'Hamburg')['lon'], 9.93)             # „Haus 22“ ist nicht „Haus 2“
        self.assertEqual(ix.lookup('7a Testalle', '20001', 'Hamburg')['lat'], 53.5501)
        self.assertEqual(ix.lookup('Gartenstraße 3', '20007', 'Hamburg')['status'], 'unbekannt')   # „Gotenstraße“ ist zu weit weg (kurzer Namensteil)
        self.assertEqual(ix.lookup('Gotenstrase 3', '20007', 'Hamburg')['status'], 'aehnlich')
        # falsche PLZ ohne bzw. mit unbekannter Hausnummer: Mitte bzw. nächste Nummer, nicht der erste Datensatz
        self.assertEqual(ix.lookup('Testallee', '20009', 'Hamburg')['lon'], 9.901)
        self.assertEqual(ix.lookup('Testallee 99', '20009', 'Hamburg')['lon'], 9.902)
        # mehrere Gebäude an einer Hausnummer: Buchstabe, mehrteiliger Zusatz, sonst das Gebäude ohne Zusatz
        self.assertEqual((ix.lookup('Hofweg 5', '20007', 'Hamburg')['lon'], ix.lookup('Hofweg 5 Haus 2', '20007', 'Hamburg')['lon'],
                          ix.lookup('Hofweg 5b', '20007', 'Hamburg')['lon'], ix.lookup('Hofweg 5 c', '20007', 'Hamburg')['lon']), (9.93, 9.932, 9.931, 9.93))
        self.assertEqual(ix.lookup('Doppelweg 2', '20004', 'Hamburg')['kv'], '5300')
        r = ix.lookup('Testalle 3', '20001', 'Hamburg')                                # ein Buchstabe fehlt, einzige ähnliche Straße der PLZ
        self.assertEqual((r['status'], r['street'], r['lon'], r['suggestions']), ('aehnlich', 'Testallee', 9.9003, [{'street': 'Testallee', 'zip': '20001'}]))
        self.assertEqual(ix.lookup('Testalle', '20001', 'Hamburg')['status'], 'aehnlich')
        r = ix.lookup('Testalle 3', '', 'Hamburg')                                     # ohne PLZ keine Vermutung, nur Vorschläge
        self.assertEqual((r['status'], r['lat'], r['suggestions'][0]['street']), ('unbekannt', None, 'Testallee'))
        self.assertEqual(ix.lookup('Tesalleh 3', '20001', 'Hamburg')['status'], 'unbekannt')     # zu weit weg (kurzer Name: höchstens ein Buchstabe)
        self.assertEqual(ix.lookup('Beispielstrase 3', '20002', 'Hamburg')['status'], 'aehnlich')
        self.assertEqual(ix.lookup('Beispielstraße 3', '20002', 'Teststadt-Nord')['status'], 'haus')    # Stadtteil als Ort
        self.assertEqual(ix.lookup('Erfundene Gasse 3', '20002', 'Teststadt-Nord')['status'], 'unbekannt')   # … gilt als Hamburg
        self.assertEqual(ix.lookup('Erfundene Gasse 3', '20002', 'Pinneberg')['status'], 'ausserhalb')
        self.assertEqual(ix.lookup('Muehlenkamp 5', '20002', 'Hamburg')['status'], 'haus')
        self.assertEqual(ix.lookup('Erfundene Gasse 3', '20001', 'Hamburg')['status'], 'unbekannt')
        self.assertEqual(ix.lookup('Doppelwe 2', '20003', 'Hamburg')['kv'], '5100')      # Tippfehler: nur Straßen derselben PLZ zählen
        self.assertEqual((adressen._distance('abc', 'abd', 3), adressen._distance('abc', 'abcd', 3), adressen._distance('abc', 'xyz9999', 2)), (1, 1, 3))
        self.assertEqual(ix.lookup('Erfundene Gasse 3', '99999', 'Hamburg')['status'], 'ausserhalb')   # PLZ gibt es in Hamburg nicht
        self.assertEqual(ix.lookup('Testallee 3', '25421', 'Pinneberg')['status'], 'ausserhalb')        # gleichnamige Straße woanders
        self.assertEqual(ix.lookup('Hauptstraße 5', '22869', 'Schenefeld')['status'], 'ausserhalb')
        self.assertEqual(ix.lookup('', '20001', 'Hamburg')['status'], 'leer')
        self.assertEqual(ix.lookup(None, None, None)['status'], 'leer')
        self.assertEqual([(s['street'], s['zip']) for s in ix.streets('doppel')], [('Doppelweg', '20003'), ('Doppelweg', '20004')])
        self.assertEqual(ix.streets('te')[:1], [])                                       # zu kurz
        self.assertEqual(ix.streets('allee')[0]['street'], 'Testallee')                  # enthält

    def test_download_pages_and_keeps_old_file_on_failure(self):
        data = fixtures()
        calls = []

        def fetch(offset):
            calls.append(offset)
            return {'numberMatched': len(data), 'features': data[offset:offset + adressen.PAGE]}

        old_page = adressen.PAGE
        adressen.PAGE = 10
        self.addCleanup(setattr, adressen, 'PAGE', old_page)
        seen = []
        self.assertEqual(adressen.download(self.dir, fetch, lambda d, t: seen.append((d, t)), minimum=5), len(data))
        self.assertEqual(calls, list(range(0, len(data), 10)))
        self.assertEqual(seen[-1], (len(data), len(data)))
        before = adressen.path(self.dir).read_bytes()
        with self.assertRaises(ValueError) as cm:                                        # zu wenig: alter Bestand bleibt
            adressen.download(self.dir, lambda offset: {'numberMatched': 3, 'features': data[:3] if not offset else []}, minimum=5)
        self.assertIn('unvollständig', str(cm.exception))
        with self.assertRaises(ValueError):                                              # Seite fehlt mittendrin (90 %-Regel)
            adressen.download(self.dir, lambda offset: {'numberMatched': len(data), 'features': data[:10] if not offset else []}, minimum=5)
        with self.assertRaises(urllib.error.URLError):
            adressen.download(self.dir, lambda offset: (_ for _ in ()).throw(urllib.error.URLError('kein Netz')), minimum=5)
        self.assertEqual(adressen.path(self.dir).read_bytes(), before)
        self.assertFalse(list(self.dir.glob('*.neu')))

    def test_download_retries_transient_failures(self):
        data = fixtures()
        old = adressen.PAGE
        adressen.PAGE = 10
        self.addCleanup(setattr, adressen, 'PAGE', old)
        tries = []

        def flaky(offset):
            tries.append(offset)
            if tries.count(offset) < 3:                                                  # jede Seite scheitert zweimal
                import http.client
                raise urllib.error.HTTPError('u', 503, 'x', None, None) if offset == 10 else http.client.IncompleteRead(b'') if offset else TimeoutError()
            return {'numberMatched': len(data), 'features': data[offset:offset + 10]}

        self.assertEqual(adressen.download(self.dir, flaky, minimum=5), len(data))
        self.assertEqual(tries.count(0), 3)
        with self.assertRaises(urllib.error.HTTPError):                                  # dauerhaft gestört: nach vier Versuchen Schluss
            adressen.download(self.dir, lambda offset: (_ for _ in ()).throw(urllib.error.HTTPError('u', 503, 'x', None, None)), minimum=5)
        calls = []
        with self.assertRaises(urllib.error.HTTPError):                                  # 404 ist kein vorübergehender Fehler
            adressen.download(self.dir, lambda offset: (calls.append(1), (_ for _ in ()).throw(urllib.error.HTTPError('u', 404, 'x', None, None)))[1], minimum=5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(adressen.status(self.dir)['adressen'], len(data))

    def test_job_reports_state_and_errors_as_text(self):
        data = fixtures()
        old = adressen.MIN_ROWS
        adressen.MIN_ROWS = 5
        self.addCleanup(setattr, adressen, 'MIN_ROWS', old)
        self.addCleanup(adressen._job.update, state='idle', error='', done=0, total=0)
        self.assertTrue(adressen.start(self.dir, lambda offset: (_ for _ in ()).throw(urllib.error.URLError('x')), wait=True))
        job = adressen.status(self.dir)['job']
        self.assertEqual(job['state'], 'error')
        self.assertIn('Keine Verbindung', job['error'])
        adressen._job.update(state='running')
        self.assertFalse(adressen.start(self.dir, lambda offset: None))                  # läuft schon: kein zweiter Abruf
        adressen._job.update(state='idle')
        self.assertTrue(adressen.start(self.dir, lambda offset: {'numberMatched': len(data), 'features': data[offset:offset + adressen.PAGE]}, wait=True))
        st = adressen.status(self.dir)
        self.assertEqual((st['job']['state'], st['job']['done'], st['available'], st['adressen']), ('done', len(data), True, len(data)))
        self.assertTrue(adressen.start(self.dir, lambda offset: {'numberMatched': 3, 'features': data[:3] if not offset else []}, wait=True))
        st = adressen.status(self.dir)                                                   # zu wenig: Fehler als Text, Bestand bleibt
        self.assertEqual((st['job']['state'], 'unvollständig' in st['job']['error'], st['adressen']), ('error', True, len(data)))
        self.assertNotIn('finished', st['job'])
        full = lambda offset: {'numberMatched': len(data), 'features': data[offset:offset + adressen.PAGE]}   # noqa: E731
        self.assertTrue(adressen.start(self.dir, full, wait=True))
        adressen.COOLDOWN = 120                                                          # eben erst geladen: kein neuer Abruf (Doppelklick, Dauerabruf)
        self.assertFalse(adressen.start(self.dir, full, wait=True))
        adressen.COOLDOWN = 0
        self.assertIn('nicht abrufbar', adressen._message(urllib.error.HTTPError('u', 503, 'x', None, None)))
        self.assertIn('Speicherplatz', adressen._message(OSError(28, 'No space left on device')))
        import ssl
        self.assertIn('Keine Verbindung', adressen._message(ssl.SSLError('x')))
        self.assertIn('unvollständig', adressen._message(json.JSONDecodeError('Expecting value', 'x', 0)))   # kein englischer Text
        self.assertTrue(adressen.build(adressen.path(self.dir), data + [{'properties': {'strassenname': 'X', 'hausnummer': '1', 'postleitzahl': '20001'},
                                                                           'geometry': {'type': 'Point', 'coordinates': {'x': 1}}}]))   # Koordinate als Objekt: übergangen
        self.assertNotIn('geheim', adressen._message(RuntimeError('geheim')))            # unerwartete Fehler ohne Inhalt

    def test_download_stays_on_the_official_host(self):
        handler = adressen._SameHost()
        req = adressen.urllib.request.Request(adressen.API)
        for url in ('https://example.org/x', 'http://api.hamburg.de/x', 'file:///etc/passwd'):
            with self.assertRaises(urllib.error.HTTPError, msg=url):
                handler.redirect_request(req, None, 302, 'Found', {}, url)
        self.assertTrue(adressen.API.startswith('https://api.hamburg.de/'))
        self.assertNotIn('strassenname=', adressen.API)                                  # nie ein Suchbegriff: immer der ganze Bestand


class MapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.s = Store(self.dir / 't.db')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, street, plz, city='Hamburg', kv='5100', **extra):
        self.n += 1
        return self.s.member({'number': f'K-{self.n}', 'first_name': 'Test', 'last_name': f'Person{self.n}', 'street': street, 'zip': plz, 'city': city,
                              'birth': '2000-02-01', 'joined': '2024-01-01', 'kv': kv, **extra}, 'Test')

    def test_without_address_data(self):
        self.member('Testallee 1', '20001')
        d = karte.data(self.s, self.dir)
        self.assertEqual((d['available'], d['points'], d['state']['available']), (False, [], False))

    def test_members_on_the_map_and_lists_to_check(self):
        adressen.build(adressen.path(self.dir), fixtures())
        a = self.member('Testallee 7a', '20001')
        b = self.member('Beispielstr. 3', '20002', kv='5100')                            # wohnt in Eimsbüttel, Mitglied in Altona
        c = self.member('Testallee 99', '20001')                                         # Hausnummer unbekannt
        d = self.member('Testalle 3', '20001')                                           # Tippfehler
        e = self.member('Hauptstraße 5', '22869', city='Schenefeld')
        f = self.member('Testallee 2', '20009')                                          # falsche PLZ, Lage eindeutig
        g = self.member('Rue de Test 1', '75001', city='Paris', country='Frankreich')
        h = self.member('Testallee 4', '20001', left_on='2025-12-31')                    # ausgetreten: nicht auf der Karte
        i = self.member('Beispielstraße 2', '20002', kv='5800')                          # Schüler Union: kein Bezirksvergleich
        j = self.member('Testallee 5', '20001', left_on='2099-12-31')                    # Austritt in der Zukunft: noch aktiv
        out = karte.data(self.s, self.dir, today='2026-11-10')
        self.assertTrue(out['available'])
        points = {p['id']: p for p in out['points']}
        self.assertEqual(sorted(points), sorted([a, b, c, d, f, i, j]))
        self.assertEqual((points[a]['lat'], points[a]['exact'], points[a]['kv'], points[a]['area'], points[a]['other']), (53.5501, True, '5100', '5100', False))
        self.assertEqual((points[b]['area'], points[b]['other'], points[i]['other'], points[c]['exact']), ('5300', True, False, False))
        self.assertEqual(set(points[a]), {'id', 'lat', 'lon', 'exact', 'kv', 'area', 'stadtteil', 'other'})   # keine Namen, keine Anschrift
        ids = {k: [x['id'] for x in v] for k, v in out['check'].items()}
        k = self.member('Erfundene Gasse 3', '20001')                                    # gibt es nicht
        out = karte.data(self.s, self.dir, today='2026-11-10')
        ids = {k_: [x['id'] for x in v] for k_, v in out['check'].items()}
        self.assertEqual(ids, {'unbekannt': [k], 'schreibweise': [d], 'plz': [f], 'hausnummer': [c], 'fremd': [b], 'ausserhalb': [e, g], 'leer': []})
        self.assertIn('Testallee', out['check']['schreibweise'][0]['hint'])
        self.assertEqual(points[d]['exact'], False)                                      # Lage vermutet: blasser Punkt
        self.assertIn('20001', out['check']['plz'][0]['hint'])
        self.assertIn('Eimsbüttel', out['check']['fremd'][0]['hint'])
        self.assertEqual((out['kv'], out['stadtteile'], out['members']), ({'5100': 6, '5800': 1}, {'Teststadt-West': 5, 'Teststadt-Nord': 2}, 10))
        self.assertNotIn(h, points)

    def test_areas_file(self):
        g = karte.gebiete()
        self.assertEqual(sorted(b['kv'] for b in g['bezirke']), ['5100', '5200', '5300', '5400', '5500', '5600', '5700'])
        self.assertEqual({b['name']: b['kv'] for b in g['bezirke']}, adressen.KV)
        self.assertGreater(len(g['stadtteile']), 95)
        self.assertTrue(all(s['bezirk'] in adressen.KV for s in g['stadtteile']))
        box = adressen.BOX
        for area in g['bezirke'] + g['stadtteile']:
            for polygon in area['flaechen']:
                for ring in polygon:
                    self.assertGreaterEqual(len(ring), 4)
                    self.assertEqual(ring[0], ring[-1])
                    self.assertTrue(all(box[0] <= x <= box[2] and box[1] <= y <= box[3] for x, y in ring), area['name'])
        self.assertLess(karte.AREAS.stat().st_size, 400_000)
        self.assertIn('dl-de/by-2-0', g['lizenz'])

    def test_vendored_map_library_and_csp(self):
        import app
        js = REPO / 'static' / 'vendor' / 'leaflet' / 'leaflet.js'
        self.assertEqual(hashlib.sha256(js.read_bytes()).hexdigest(), LEAFLET_JS)        # unverändert bis auf die entfernte Source-Map-Zeile
        self.assertEqual(hashlib.sha256((js.parent / 'leaflet.css').read_bytes()).hexdigest(), 'a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6')
        self.assertIn('BSD 2-Clause', (js.parent / 'LICENSE').read_text())
        self.assertNotIn('sourceMappingURL', js.read_text(encoding='utf-8'))
        self.assertEqual(app.static_file('/vendor/leaflet/leaflet.js'), js.resolve())
        self.assertIsNone(app.static_file('/vendor/leaflet/LICENSE'))
        source = (REPO / 'app.py').read_text(encoding='utf-8')
        self.assertIn("img-src 'self' data: https://tile.openstreetmap.org;", source)   # nur Kartenbilder, sonst nichts von außen
        self.assertIn("script-src 'self';", source)
        self.assertIn("connect-src 'self';", source)


LEAFLET_JS = '2d98625bad9de241c7e78b06eb1d5f917c2f57ec6c84c3092a00438a01dab758'

if __name__ == '__main__':
    unittest.main()
