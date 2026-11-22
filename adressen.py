"""Amtliche Hamburger Adressen (ab 0.32) – Grundlage für die Karte und die Adressprüfung beim Mitglied.

Wunsch Lennert Gust 10.11.2026: „wo welches Mitglied wohnt, bis in die Straßen“ und beim Anlegen eines Mitglieds prüfen, „ob es diese
Straße überhaupt gibt“. Dazu holt das Programm – nur auf ausdrücklichen Knopfdruck – den öffentlichen Adressbestand der Stadt
(Zentraler AdressService Hamburg, Urban Data Platform; Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung,
Datenlizenz Deutschland – Namensnennung – Version 2.0) und legt ihn in `Daten/adressen.sqlite3` ab. Geprüft und verortet wird danach
ausschließlich auf dem eigenen Rechner: Es geht nie eine Mitgliederadresse an einen fremden Dienst – der Abruf ist immer der ganze
Bestand, ohne Suchbegriff. Die Datei enthält keine Vereinsdaten und gehört nicht in die Sicherung (sie lässt sich jederzeit neu laden).

Aufbau der Datei: `strassen` (Name, normalisierter Name), `adressen` (Straße, Hausnummer, Zusatz, PLZ, Bezirk, Stadtteil, Koordinate),
`namen` (Bezirke und Stadtteile), `meta` (Stand, Anzahl). Nur dieses Modul schreibt sie.
"""
import difflib
import http.client
import json
import os
import re
import sqlite3
import ssl
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import zlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

FILE = 'adressen.sqlite3'
HOST = 'api.hamburg.de'
API = f'https://{HOST}/datasets/v1/gages_vereinfacht/collections/hauskoordinaten/items'
FIELDS = 'strassenname,hausnummer,hausnummernzusatz,postleitzahl,bezirke,postOrtsteil,geom'
SOURCE = 'Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung – Zentraler AdressService Hamburg'
LICENSE = 'Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0)'
PAGE = 10000
MAX_PAGES = 80                       # 800.000 Adressen – der Bestand hat gut 300.000
MAX_BYTES = 40_000_000               # je Seite, entpackt
MIN_ROWS = 200_000                   # weniger: Abruf unvollständig – den alten Bestand behalten
BOX = (9.6, 53.3, 10.4, 53.8)        # Hamburg ohne Neuwerk (West, Süd, Ost, Nord)
# Bezirk → Kreisverband (Bereich); die Kreisverbände der JU Hamburg entsprechen den sieben Bezirken
KV = {'Altona': '5100', 'Bergedorf': '5200', 'Eimsbüttel': '5300', 'Harburg': '5400', 'Hamburg-Mitte': '5500', 'Hamburg-Nord': '5600', 'Wandsbek': '5700'}

_lock = threading.Lock()
_job = {'state': 'idle', 'done': 0, 'total': 0, 'error': '', 'started': ''}


# ---------------------------------------------------------------- Schreibweisen

def _fold(text):
    """Klein, Umlaute als ae/oe/ue/ss, ohne Akzente, nur Buchstaben und Ziffern."""
    text = str(text or '').lower().replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    text = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in text if ch.isalnum() and ord(ch) < 128)


def norm_street(name):
    """Straßenname zum Vergleichen: „Musterstr.“, „Muster Straße“ und „Musterstrasse“ sind dasselbe."""
    key = _fold(name)
    key = re.sub(r'str$', 'strasse', key)
    return re.sub(r'^sankt', 'st', key)                  # „Sankt Pauli …“ = amtlich „St. Pauli …“


def split_street(text):
    """„Musterstraße 12 a“ → ('Musterstraße', 12, 'a'); ohne Hausnummer (name, None, '').
    Bereiche wie „12-14“ zählen mit der ersten Nummer; Zusätze nach einem Komma (c/o, Wohnung) bleiben weg."""
    text = ' '.join(str(text or '').replace(' ', ' ').split())
    text = text.split(',')[0].strip()
    m = re.match(r'^(?P<name>.*?[^\d\s])\s*(?P<nr>\d{1,4})\s*(?P<rest>.*)$', text)
    if not m or not re.search(r'[^\W\d_]', m.group('name')):
        return text, None, ''
    rest = m.group('rest').strip().lower()
    z = re.match(r'^([a-z])(?![a-z])', rest)             # einzelner Buchstabe: 12a, 12 a, 12a-c
    return m.group('name').strip(), int(m.group('nr')), z.group(1) if z else ''


def _distance(a, b, stop):
    """Zahl der Änderungen (Buchstabe fehlt, zu viel, ersetzt) zwischen zwei Namen; bricht ab, sobald es mehr als `stop` sind."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > stop:
            return stop + 1
        prev = cur
    return prev[-1]


def _parts(text):
    """Teile eines Anschriftfelds vor/nach einem Komma, die mit einer Zahl zuerst (eine c/o-Zeile ohne Nummer zuletzt), höchstens drei."""
    text = ' '.join(str(text or '').replace('\u00a0', ' ').split())
    parts = [p.strip() for p in text.split(',') if p.strip()]
    return sorted(parts, key=lambda p: not re.search(r'\d', p))[:3]


def candidates(text):
    """Mögliche Lesarten eines Anschriftfelds, die wahrscheinlichste zuerst: (Straßenname, Hausnummer oder None, Zusatz, Text nach der Nummer).
    Je Teil vor/nach einem Komma: das Ganze als Name (amtliche Namen mit Ziffern, „Weg Nr. 12“), dann jede Trennstelle vor einer
    Zahl – die mit dem längsten Namen zuerst –, zuletzt eine vorangestellte Hausnummer („7 Musterweg“)."""
    out = []
    for part in _parts(text):
        out.append((part, None, '', ''))
        for m in sorted(re.finditer(r'(?<!\d)(\d{1,4})(?!\d)', part), key=lambda m: -m.start()):
            full = part[:m.start()].strip()
            out_name = re.sub(r'\s+nr\.?$', '', full, flags=re.IGNORECASE)
            names = [full] if out_name == full else [full, out_name]   # „Weg Nr. 396 5“: Name mit „Nr.“ zuerst, dann ohne („Musterweg Nr. 7“)
            z = re.match(r'^\s*([a-zA-Z])(?![a-zA-Z])', part[m.end():])
            rest = ' '.join(part[m.end():].lower().split())
            for name in names:
                if re.search(r'[^\W\d_]', name):
                    out.append((name, int(m.group(1)), z.group(1).lower() if z else '', rest))
        lead = re.match(r'^(\d{1,4})\s*([a-zA-Z]?)(?:\s*[-–/]\s*\d{1,4}\s*[a-zA-Z]?)?\.?\s+(\D.*)$', part)
        if lead and re.search(r'[^\W\d_]{3}', lead.group(3)):
            out.append((lead.group(3).strip(), int(lead.group(1)), lead.group(2).lower(), ''))
    return out


def _stem(key):
    """Namensteil ohne die übliche Endung („…straße“, „…weg“) – daran misst sich, wie viel Abweichung ein Tippfehler haben darf."""
    return re.sub(r'(strasse|chaussee|allee|platz|stieg|twiete|redder|damm|ring|kamp|weg)$', '', key)


def is_hamburg(city):
    key = _fold(city)
    return not key or 'hamburg' in key or key == 'hh'


# ---------------------------------------------------------------- Datei

def path(data_dir):
    return Path(data_dir) / FILE


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE namen(id INTEGER PRIMARY KEY, art TEXT NOT NULL, name TEXT NOT NULL, UNIQUE(art, name));
CREATE TABLE strassen(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, norm TEXT NOT NULL);
CREATE TABLE adressen(strasse INTEGER NOT NULL, nr INTEGER NOT NULL, zusatz TEXT NOT NULL, plz TEXT NOT NULL,
    bezirk INTEGER NOT NULL, stadtteil INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL);
"""
INDEXES = """
CREATE INDEX strassen_norm ON strassen(norm);
CREATE INDEX adressen_strasse ON adressen(strasse, nr);
"""


class _Writer:
    """Schreibt den Bestand in eine neue Datei (innerhalb von build bzw. download)."""

    def __init__(self, target):
        self.db = sqlite3.connect(target)
        self.db.executescript(SCHEMA)
        self.streets, self.names, self.seen, self.rows, self.skipped = {}, {}, set(), 0, 0

    def _name(self, art, name):
        key = (art, name)
        if key not in self.names:
            self.names[key] = self.db.execute('INSERT INTO namen(art,name) VALUES(?,?)', key).lastrowid
        return self.names[key]

    def add(self, features):
        """GeoJSON-Features der Schnittstelle; Unbrauchbares (ohne Straße, Nummer, PLZ oder Koordinate in Hamburg) wird übergangen."""
        batch = []
        for f in features:
            p = f.get('properties') if isinstance(f, dict) else None
            g = f.get('geometry') if isinstance(f, dict) else None
            if not isinstance(p, dict) or not isinstance(g, dict) or g.get('type') != 'Point':
                self.skipped += 1
                continue
            street, plz = p.get('strassenname'), p.get('postleitzahl')
            xy = g.get('coordinates')
            try:
                nr = int(str(p.get('hausnummer')).strip())
                lon, lat = float(xy[0]), float(xy[1])
            except (TypeError, ValueError, IndexError, KeyError):
                self.skipped += 1
                continue
            if not isinstance(street, str) or not street.strip() or not isinstance(plz, str) or not re.fullmatch(r'\d{5}', plz) \
                    or not (BOX[0] <= lon <= BOX[2] and BOX[1] <= lat <= BOX[3]) or not 0 < nr < 10000:
                self.skipped += 1
                continue
            street = ' '.join(street.split())[:120]
            if _fold(street) in PLACEHOLDERS:             # Platzhalter der Stadt („Straßenname unbekannt“), keine Anschrift
                self.skipped += 1
                continue
            zusatz = ' '.join(str(p.get('hausnummernzusatz') or '').lower().split())[:20]
            key = (street, nr, zusatz, plz)
            if key in self.seen:                      # Seiten können sich bei einer Änderung während des Abrufs überschneiden
                continue
            self.seen.add(key)
            sid = self.streets.get(street)
            if sid is None:
                sid = self.streets[street] = self.db.execute('INSERT INTO strassen(name,norm) VALUES(?,?)', (street, norm_street(street))).lastrowid
            batch.append((sid, nr, zusatz, plz, self._name('bezirk', str(p.get('bezirke') or '')[:60]),
                          self._name('stadtteil', str(p.get('postOrtsteil') or '')[:60]), round(lat, 6), round(lon, 6)))
        self.db.executemany('INSERT INTO adressen VALUES(?,?,?,?,?,?,?,?)', batch)
        self.rows += len(batch)
        return len(batch)

    def finish(self, stand, reported=0):
        self.db.executescript(INDEXES)
        meta = {'stand': stand, 'adressen': str(self.rows), 'strassen': str(len(self.streets)), 'quelle': SOURCE, 'lizenz': LICENSE,
                'gemeldet': str(reported or ''), 'uebergangen': str(self.skipped)}   # von der Stadt gemeldete Anzahl; unbrauchbare Zeilen
        self.db.executemany('INSERT INTO meta VALUES(?,?)', meta.items())
        self.db.commit()
        self.db.execute('VACUUM')
        self.db.close()


def build(target, features, stand=None):
    """Bestand aus Features anlegen (Tests, Probeläufe) – ersetzt die Datei erst, wenn alles geschrieben ist."""
    target = Path(target)
    tmp = target.with_name(target.name + '.neu')
    tmp.unlink(missing_ok=True)
    writer = _Writer(tmp)
    try:
        writer.add(features)
        writer.finish(stand or datetime.now().isoformat(timespec='seconds'))
    except BaseException:
        writer.db.close()
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, target)
    return writer.rows


# ---------------------------------------------------------------- Abruf

class Incomplete(ValueError):
    """Abruf abgebrochen, weil zu wenig oder zu viel ankam – der Text ist für die Oberfläche gedacht."""


class _SameHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme != 'https' or urlparse(newurl).hostname != HOST:
            raise urllib.error.HTTPError(newurl, code, 'Weiterleitung zu einer anderen Adresse abgelehnt', headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(offset, version=''):
    """Eine Seite des Adressbestands (ohne Suchbegriff – immer der ganze Bestand der Reihe nach)."""
    import versand
    query = urlencode({'f': 'json', 'limit': PAGE, 'offset': int(offset), 'properties': FIELDS, 'sortby': 'hausschluessel'})
    req = urllib.request.Request(f'{API}?{query}', headers={'Accept': 'application/geo+json', 'Accept-Encoding': 'gzip',
                                                           'User-Agent': f'JU-Hamburg-Finanzen/{version or "0"} (Adressabgleich)'})
    opener = urllib.request.build_opener(_SameHost, urllib.request.HTTPSHandler(context=versand.tls_context()))
    with opener.open(req, timeout=60) as response:
        raw = response.read(MAX_BYTES + 1)
        if response.headers.get('Content-Encoding', '').lower() == 'gzip':
            unpack = zlib.decompressobj(16 + zlib.MAX_WBITS)
            raw = unpack.decompress(raw, MAX_BYTES + 1)
            if unpack.unconsumed_tail:
                raise Incomplete('Die Antwort der Adressdaten ist unerwartet groß – Abruf abgebrochen.')
    if len(raw) > MAX_BYTES:
        raise Incomplete('Die Antwort der Adressdaten ist unerwartet groß – Abruf abgebrochen.')
    data = json.loads(raw.decode('utf-8'))               # abgeschnittene Antwort: ValueError → Meldung „unvollständig“ (_message)
    if not isinstance(data, dict) or not isinstance(data.get('features'), list):
        raise ValueError('unerwartetes Format')
    return data


PLACEHOLDERS = {'strassennnameunbekannt', 'strassennameunbekannt'}   # so (auch mit dem Schreibfehler) im Bestand der Stadt
COOLDOWN = 120                       # Sekunden nach einem gelungenen Abruf, in denen kein neuer startet
RETRY_WAIT = (2, 5, 12)              # Sekunden vor dem 2., 3. und 4. Versuch je Seite


def _retry(fetch, offset):
    """Eine Seite holen; bei vorübergehenden Störungen (Zeitüberschreitung, Verbindungsfehler, 429/5xx) bis zu dreimal neu versuchen."""
    for wait in (*RETRY_WAIT, None):
        try:
            return fetch(offset)
        except urllib.error.HTTPError as error:
            if wait is None or error.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, ssl.SSLError):
            if wait is None:                             # auch abgebrochene Übertragung oder gestörte Verschlüsselung
                raise
        time.sleep(wait)


def download(data_dir, fetch=None, progress=None, minimum=None):
    """Ganzen Bestand abrufen und die Datei ersetzen; bricht ohne Änderung ab, wenn zu wenig ankommt."""
    fetch = fetch or fetch_page
    minimum = MIN_ROWS if minimum is None else minimum
    target = path(data_dir)
    tmp = target.with_name(target.name + '.neu')
    tmp.unlink(missing_ok=True)
    writer = _Writer(tmp)
    try:
        total = 0
        for page in range(MAX_PAGES):
            data = _retry(fetch, page * PAGE)
            features = data['features']
            if isinstance(data.get('numberMatched'), int):
                total = data['numberMatched']
            writer.add(features)
            if progress:
                progress(min(page * PAGE + len(features), total or 10 ** 9), total)
            if len(features) < PAGE or (total and (page + 1) * PAGE >= total):
                break
        else:
            raise Incomplete('Der Adressbestand ist unerwartet groß – Abruf abgebrochen.')
        if writer.rows < minimum or (total and writer.rows < total * 0.9):
            raise Incomplete(f'Der Abruf ist unvollständig ({writer.rows} Adressen) – der bisherige Bestand bleibt.')
        writer.finish(datetime.now().isoformat(timespec='seconds'), total)
    except BaseException:
        writer.db.close()
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, target)
    return writer.rows


def _message(error):
    if isinstance(error, urllib.error.HTTPError):
        return f'Die Adressdaten der Stadt sind gerade nicht abrufbar (Fehler {error.code}). Später noch einmal versuchen.'
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, ssl.SSLError)):
        return 'Keine Verbindung zu den Adressdaten der Stadt (api.hamburg.de) – Internetverbindung prüfen und noch einmal versuchen.'
    if isinstance(error, Incomplete):
        return str(error)[:200]
    if isinstance(error, (OSError, sqlite3.Error)):
        return 'Die Adressdaten konnten nicht gespeichert werden – Speicherplatz und Schreibrechte im Datenordner prüfen.'
    return 'Die Antwort der Adressdaten war unvollständig oder hat ein unerwartetes Format – später noch einmal versuchen.'


def start(data_dir, fetch=None, version='', wait=False):
    """Abruf im Hintergrund starten (läuft schon einer, bleibt es bei dem); Stand über status()."""
    with _lock:
        if _job['state'] == 'running' or time.monotonic() - _job.get('finished', -COOLDOWN) < COOLDOWN:
            return False                                 # läuft schon bzw. eben erst geladen (kein Dauerabruf, auch nicht durch Doppelklick)
        _job.update(state='running', done=0, total=0, error='', started=datetime.now().isoformat(timespec='seconds'))

    def progress(done, total):
        with _lock:
            _job.update(done=done, total=total)

    def run():
        try:
            download(data_dir, fetch or (lambda offset: fetch_page(offset, version)), progress)
            with _lock:
                _job.update(state='done', finished=time.monotonic())
        except Exception as error:   # noqa: BLE001 – der Auftrag meldet jeden Fehler als Text, nie als Absturz
            with _lock:
                _job.update(state='error', error=_message(error))

    thread = threading.Thread(target=run, daemon=True, name='adressdaten')
    thread.start()
    if wait:
        thread.join()
    return True


def status(data_dir):
    """Stand für die Oberfläche: vorhanden?, Stand, Anzahl, laufender Abruf."""
    with _lock:
        job = {k: v for k, v in _job.items() if k != 'finished'}
    out = {'available': False, 'stand': '', 'adressen': 0, 'strassen': 0, 'uebergangen': 0, 'quelle': SOURCE, 'lizenz': LICENSE, 'job': job}
    db = connect(data_dir)
    if db:
        try:
            meta = dict(db.execute('SELECT key,value FROM meta').fetchall())
            out.update(available=True, stand=meta.get('stand', ''), adressen=int(meta.get('adressen') or 0), strassen=int(meta.get('strassen') or 0),
                       uebergangen=int(meta.get('uebergangen') or 0))
        except (sqlite3.Error, ValueError):
            pass
        finally:
            db.close()
    return out


def connect(data_dir):
    """Lesende Verbindung zur Adressdatei oder None, wenn sie (noch) fehlt bzw. unlesbar ist."""
    target = path(data_dir)
    if not target.is_file():
        return None
    try:
        db = sqlite3.connect(f'file:{target.as_posix()}?mode=ro', uri=True, check_same_thread=False)
        db.execute('SELECT 1 FROM meta LIMIT 1').fetchone()
        return db
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------- Prüfen und Verorten

class Index:
    """Nachschlagen in der Adressdatei – eine Instanz je Anfrage (Karte: viele Mitglieder nacheinander)."""

    def __init__(self, db):
        self.db = db
        self._norms = None
        self._zips = None
        self._rows = {}
        self._by_zip = {}
        self._places = None
        self.names = dict(db.execute('SELECT id,name FROM namen').fetchall())

    def close(self):
        self.db.close()

    @property
    def norms(self):
        if self._norms is None:
            self._norms = {}
            for sid, name, norm in self.db.execute('SELECT id,name,norm FROM strassen'):
                self._norms.setdefault(norm, []).append((sid, name))
        return self._norms

    @property
    def places(self):
        """Namen der Bezirke und Stadtteile (als Ort in der Anschrift gilt das als Hamburg), auch ohne „Hamburg-“ davor."""
        if self._places is None:
            keys = {_fold(n) for n in self.names.values()}
            self._places = {k for k in keys | {k[len('hamburg'):] for k in keys if k.startswith('hamburg')} if len(k) >= 4}
        return self._places

    @property
    def zips(self):
        if self._zips is None:
            self._zips = {r[0] for r in self.db.execute('SELECT DISTINCT plz FROM adressen')}
        return self._zips

    def stadtteile(self):
        """Stadtteile des Bestands mit Bezirk und Kreisverband, sortiert: [{'name', 'bezirk', 'kv'}] (Gebiete der Ortsverbände, ab 0.52)."""
        pairs = self.db.execute('SELECT DISTINCT bezirk,stadtteil FROM adressen').fetchall()
        out = [{'name': self.names.get(s, ''), 'bezirk': self.names.get(b, ''), 'kv': KV.get(self.names.get(b, ''), '')} for b, s in pairs]
        return sorted((x for x in out if x['name']), key=lambda x: (x['kv'], _fold(x['name'])))

    def _street_rows(self, ids):
        rows = self._rows.get(ids)
        if rows is None:                                 # Karte: viele Mitglieder derselben Straße
            marks = ','.join('?' * len(ids))
            rows = self._rows[ids] = self.db.execute(f'SELECT strasse,nr,zusatz,plz,bezirk,stadtteil,lat,lon FROM adressen WHERE strasse IN ({marks})', ids).fetchall()
        return rows

    def _near(self, key, plz):
        """Vermutlich gemeinte Straße bei einem Tippfehler: (id, Name) der einzigen Straße DERSELBEN PLZ, die sich nur um einen Buchstaben
        unterscheidet (zwei, wenn beide Namen ohne die übliche Endung mindestens acht Zeichen haben) – und keine zweite, die fast
        genauso nah ist. Sonst None (dann nur Vorschläge). „Gartenstraße“ ist so nicht „Gotenstraße“."""
        stem = _stem(key)
        if len(key) < 6 or len(stem) < 4 or not plz:
            return None
        if plz not in self._by_zip:
            self._by_zip[plz] = [(sid, name, norm, len(_stem(norm))) for sid, name, norm in self.db.execute(
                'SELECT DISTINCT s.id,s.name,s.norm FROM adressen a JOIN strassen s ON s.id=a.strasse WHERE a.plz=?', (plz,))]
        found = []
        for sid, name, norm, size in self._by_zip[plz]:
            if abs(len(norm) - len(key)) > 3:
                continue
            d = _distance(key, norm, 3)
            if d <= 3:
                found.append((d, 1 if min(size, len(stem)) < 8 else 2, sid, name))
        found.sort()
        if not found or found[0][0] > found[0][1] or (len(found) > 1 and found[1][0] <= found[0][0] + 1):
            return None
        return found[0][2], found[0][3]

    def _similar(self, key, plz):
        """Ähnliche Straßennamen (Tippfehler) – bevorzugt mit der angegebenen PLZ."""
        close = difflib.get_close_matches(key, list(self.norms), n=6, cutoff=0.84)
        out = []
        for norm in close:
            for sid, name in self.norms[norm]:
                zips = [r[0] for r in self.db.execute('SELECT DISTINCT plz FROM adressen WHERE strasse=? ORDER BY plz', (sid,))]
                out.append({'street': name, 'zips': zips[:6], 'match': bool(plz and plz in zips)})
        out.sort(key=lambda x: not x['match'])
        return [{'street': x['street'], 'zip': (plz if x['match'] else (x['zips'][0] if len(x['zips']) == 1 else ''))} for x in out[:4]]

    def _result(self, status, row=None, **extra):
        out = {'status': status, 'lat': None, 'lon': None, 'bezirk': '', 'stadtteil': '', 'kv': '', 'street': '', 'zip': '', 'zips': [], 'suggestions': [],
               'typed': ''}   # typed: der als Straßenname gelesene Teil der Eingabe (zum Ersetzen durch einen Vorschlag)
        if row:
            out.update(lat=row[6], lon=row[7], bezirk=self.names.get(row[4], ''), stadtteil=self.names.get(row[5], ''), zip=row[3])
            out['kv'] = KV.get(out['bezirk'], '')
        out.update(extra)
        return out

    def _parse(self, street):
        """Straße, Hausnummer, Zusatz (ein Buchstabe) und den Text nach der Nummer aus dem Feld lesen – so, dass ein amtlicher
        Straßenname herauskommt, wenn es einen gibt (auch Namen mit Ziffern wie „Platz am 10. Längengrad“ und Zusätze vor dem Komma
        wie „c/o …, Musterweg 3“). Sonst die übliche Lesart des Teils mit der Hausnummer (für Tippfehler und Vorschläge)."""
        options = candidates(street)
        for option in options:
            if norm_street(option[0]) in self.norms:
                return option
        return self._readings(street)[0]

    @staticmethod
    def _readings(street):
        """Übliche Lesarten ohne bekannte Straße, die wahrscheinlichste zuerst: je Teil mit Zahl Name + Nummer (längster Name vorn –
        „Whg. 4, Musterwegg 3“), dann eine vorangestellte Hausnummer („7a Musterwegg“), sonst das Feld als Ganzes."""
        usual = [(*x, '') for x in (split_street(part) for part in _parts(street)) if x[1] is not None]
        usual.sort(key=lambda x: -len(_fold(x[0])))
        lead = [o for o in candidates(street) if o[1] is not None and o not in usual][-1:]
        return usual + (lead if not usual else []) or [(*split_street(street), '')]

    @staticmethod
    def _pick(rows, zusatz, rest):
        """Unter mehreren Gebäuden derselben Hausnummer: genau dieser Buchstabe, sonst ein mehrteiliger Zusatz aus dem Text
        („Haus 4“, „Hth“), sonst das Gebäude ohne Zusatz, sonst das erste."""
        hit = next((r for r in rows if r[2] == zusatz), None) if zusatz else None
        if not hit and rest:
            hit = max((r for r in rows if len(r[2]) > 1 and (rest == r[2] or rest.startswith(r[2] + ' '))), key=lambda r: len(r[2]), default=None)
        return hit or next((r for r in rows if not r[2]), rows[0])

    def lookup(self, street, plz='', city=''):
        """Anschrift prüfen. status: 'haus' (Hausnummer gefunden), 'strasse' (Straße gefunden, Hausnummer nicht – Lage ungefähr),
        'plz' (Straße/Hausnummer gefunden, aber mit anderer PLZ – `zips` nennt die richtige), 'aehnlich' (Tippfehler: genau eine sehr
        ähnliche Straße in derselben PLZ – Lage vermutet, Schreibweise prüfen), 'unbekannt' (Straße gibt es in Hamburg nicht –
        Vorschläge), 'ausserhalb' (nicht Hamburg – ungeprüft), 'leer' (keine Straße angegeben)."""
        name, nr, zusatz, rest = self._parse(street)
        plz = re.sub(r'\D', '', str(plz or ''))[:5]
        if not name.strip():
            return self._result('leer')
        place = _fold(city)
        # Ein Stadtteil oder Bezirk als Ort („Bergedorf“, „HH-Altona“) zählt nur mit einer Hamburger PLZ bzw. ohne PLZ als Hamburg –
        # Hamm, Neustadt, Horn oder Eppendorf gibt es auch anderswo (Nachprüfung 0.32)
        foreign = len(plz) == 5 and plz not in self.zips                          # vollständige PLZ, die es in Hamburg nicht gibt
        hamburg = is_hamburg(city) or (not foreign and (place in self.places or (place.startswith('hh') and place[2:] in self.places)))
        key = norm_street(name)
        streets = self.norms.get(key) or []
        typo = None
        if not streets:
            if not hamburg or foreign:
                return self._result('ausserhalb')       # anderer Ort (auch mit einer PLZ, die Hamburg mit dem Umland teilt) bzw. fremde PLZ
            near = None
            for reading in self._readings(street):       # „Reperbahn 1, Hinterhaus 2“: jede Lesart auf einen Tippfehler prüfen
                near = self._near(norm_street(reading[0]), plz)
                if near:
                    name, nr, zusatz, rest = reading
                    break
            if not near:
                return self._result('unbekannt', typed=name, suggestions=self._similar(key, plz))
            streets, typo = [near], near[1]              # Tippfehler: genau eine sehr ähnliche Straße in derselben PLZ
        rows = self._street_rows(tuple(s[0] for s in streets))
        official = {s[0]: s[1] for s in streets}
        same_zip = [r for r in rows if r[3] == plz]
        if not hamburg and not same_zip:                 # gleichnamige Straße in einem anderen Ort
            return self._result('ausserhalb')
        result = self._locate(rows, official, same_zip, nr, zusatz, rest, plz)
        if typo:                                         # Lage wie bei richtiger Schreibweise, aber als Vermutung gekennzeichnet
            if result['lat'] is None:
                return self._result('unbekannt', typed=name, suggestions=[{'street': typo, 'zip': ''}])
            return {**result, 'status': 'aehnlich', 'zips': [], 'typed': name, 'suggestions': [{'street': typo, 'zip': result['zip']}]}
        return result

    def _locate(self, rows, official, same_zip, nr, zusatz, rest, plz):
        """Lage in einer bekannten Straße. Erst die Hausnummer – in der ganzen Straße: Lange Straßen haben je Abschnitt eine andere
        PLZ (Prüfbefund 0.32). Passt die PLZ nicht: eindeutig → richtige PLZ und Lage, sonst nur die PLZ zur Wahl."""
        def found(status, row):
            return self._result(status, row, street=official[row[0]])

        def middle(pool):
            return min(pool, key=lambda r: (abs(r[1] - nr), r[2])) if nr is not None else sorted(pool, key=lambda r: (r[1], r[2]))[len(pool) // 2]

        def other_zip(pool, row=None):
            zips = sorted({r[3] for r in pool})
            if len(zips) == 1:
                row = row or middle(pool)
                return self._result('plz', row, street=official[row[0]], zips=zips)
            return self._result('plz', street=official[pool[0][0]], zips=zips[:6])

        if nr is not None:
            exact = [r for r in rows if r[1] == nr]
            here = [r for r in exact if r[3] == plz]
            if here:
                return found('haus', self._pick(here, zusatz, rest))
            if exact:
                if not plz and len({r[3] for r in exact}) == 1:
                    return found('haus', self._pick(exact, zusatz, rest))
                return other_zip(exact, self._pick(exact, zusatz, rest))
            if same_zip or not plz:                      # Hausnummer gibt es nirgends in der Straße: nächste Nummer, Lage ungefähr
                pool = same_zip or rows
                if not same_zip and len({r[3] for r in pool}) > 1:
                    return other_zip(pool)
                return found('strasse', middle(pool))
            return other_zip(rows)
        if same_zip or (not plz and len({r[3] for r in rows}) == 1):
            return found('strasse', middle(same_zip or rows))
        return other_zip(rows)

    def streets(self, query, limit=8):
        """Straßensuche für die Karte: Namen, die mit dem Suchtext beginnen (sonst: ihn enthalten), mit Lage."""
        key = norm_street(query)
        if len(key) < 3:
            return []
        hits = sorted((n for n in self.norms if n.startswith(key)), key=len)[:limit]
        if len(hits) < limit:
            hits += [n for n in sorted(self.norms, key=len) if key in n and n not in hits][:limit - len(hits)]
        out = []
        for norm in hits:
            for sid, name in self.norms[norm]:
                for plz, lat, lon, stadtteil in self.db.execute('SELECT plz,AVG(lat),AVG(lon),MIN(stadtteil) FROM adressen WHERE strasse=? GROUP BY plz ORDER BY plz', (sid,)):
                    out.append({'street': name, 'zip': plz, 'lat': round(lat, 6), 'lon': round(lon, 6), 'stadtteil': self.names.get(stadtteil, '')})
        return out[:limit * 2]


def index(data_dir):
    db = connect(data_dir)
    return Index(db) if db else None


def check(data_dir, street, plz='', city=''):
    """Eine Anschrift prüfen (Mitgliedsdialog); ohne Adressdatei: status 'keine_daten'."""
    ix = index(data_dir)
    if not ix:
        return {'status': 'keine_daten', 'lat': None, 'lon': None, 'bezirk': '', 'stadtteil': '', 'kv': '', 'street': '', 'zip': '', 'zips': [], 'suggestions': [], 'typed': ''}
    try:
        return ix.lookup(street, plz, city)
    finally:
        ix.close()
