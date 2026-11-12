"""Karte der Mitglieder (ab 0.32, nur lesend): jedes aktive Mitglied an seiner Anschrift, die Kreisverbände als Gebiete.

Verortet wird mit den amtlichen Adressdaten auf dem eigenen Rechner (adressen.py) – keine Mitgliederadresse verlässt das Programm.
Die Gebiete (Bezirke = Kreisverbände, Stadtteile) liegen als vereinfachte Flächen in `karte-gebiete.json` (werkzeuge/gebiete_erzeugen.py).
Die Antwort enthält je Mitglied nur Kennung, Lage und Kreisverband; Namen hat die Oberfläche aus der Mitgliederliste.
"""
import json
from datetime import date
from pathlib import Path

import adressen
import mitglieder

AREAS = Path(__file__).resolve().parent / 'karte-gebiete.json'
_areas = None


def gebiete():
    """Bezirke (mit Kreisverband) und Stadtteile als Flächen – öffentliche Daten, einmal gelesen."""
    global _areas
    if _areas is None:
        _areas = json.loads(AREAS.read_text(encoding='utf-8'))
    return _areas


def _hint(result):
    if result['status'] == 'unbekannt':
        names = [f"{s['street']}{' (' + s['zip'] + ')' if s['zip'] else ''}" for s in result['suggestions'][:3]]
        return 'Straße in Hamburg nicht gefunden' + (f" – ähnlich: {', '.join(names)}" if names else '')
    if result['status'] == 'aehnlich':
        return f"Straße so nicht gefunden – gemeint ist wohl „{result['street']}“ (Lage vermutet)"
    if result['status'] == 'plz':
        zips = result['zips']
        return f"{result['street']} hat laut Adressdaten die PLZ {', '.join(zips)}" if zips else 'PLZ passt nicht zur Straße'
    if result['status'] == 'strasse':
        return f"{result['street']}: Hausnummer nicht im Adressbestand – Lage ungefähr"
    if result['status'] == 'ausserhalb':
        return 'wohnt außerhalb Hamburgs – Adresse nicht geprüft'
    if result['status'] == 'leer':
        return 'keine Straße angegeben'
    return ''


def data(store, data_dir, today=None):
    """Lage aller aktiven Mitglieder und die Listen „zu prüfen“. Ohne Adressdaten: available False."""
    today = today or date.today().isoformat()
    state = adressen.status(data_dir)
    ix = adressen.index(data_dir)
    if not ix:
        return {'available': False, 'state': state, 'points': [], 'check': {}, 'kv': {}, 'stadtteile': {}, 'members': 0}
    kvs = set(adressen.KV.values())
    points, kv_count, quarters = [], {}, {}
    check = {'unbekannt': [], 'schreibweise': [], 'plz': [], 'hausnummer': [], 'fremd': [], 'ausserhalb': [], 'leer': []}
    try:
        rows = store.rows("SELECT id,kv,street,zip,city,country FROM members WHERE COALESCE(left_on,'')='' OR left_on>? ORDER BY id", (today,))
        for m in rows:
            abroad = bool(m['country']) and mitglieder._fold(m['country']) not in mitglieder.DOMESTIC   # wie beim Speichern des Mitglieds
            r = ix.lookup(m['street'], m['zip'], m['city']) if not abroad else {'status': 'ausserhalb', 'lat': None}
            status = r['status']
            if status in ('unbekannt', 'ausserhalb', 'leer'):
                check[status].append({'id': m['id'], 'hint': _hint(r)})
                continue
            if status == 'aehnlich':
                check['schreibweise'].append({'id': m['id'], 'hint': _hint(r)})
            elif status == 'plz':
                check['plz'].append({'id': m['id'], 'hint': _hint(r)})
            elif status == 'strasse':
                check['hausnummer'].append({'id': m['id'], 'hint': _hint(r)})
            if r['lat'] is None:
                continue
            other = m['kv'] in kvs and bool(r['kv']) and r['kv'] != m['kv']
            if other:
                check['fremd'].append({'id': m['id'], 'hint': f"wohnt im Bezirk {r['bezirk']}"})
            points.append({'id': m['id'], 'lat': r['lat'], 'lon': r['lon'], 'exact': status == 'haus', 'kv': m['kv'], 'area': r['kv'],
                           'stadtteil': r['stadtteil'], 'other': other})
            kv_count[m['kv']] = kv_count.get(m['kv'], 0) + 1
            quarters[r['stadtteil']] = quarters.get(r['stadtteil'], 0) + 1
    finally:
        ix.close()
    return {'available': True, 'state': state, 'points': points, 'check': check, 'kv': kv_count, 'stadtteile': quarters, 'members': len(rows)}
