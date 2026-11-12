"""Erzeugt karte-gebiete.json: Bezirke und Stadtteile Hamburgs als vereinfachte Flächen für die Karte (0.32).

Quelle: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung – ALKIS Verwaltungsgrenzen,
Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0). Abruf (GeoJSON, EPSG:4326):
  https://geodienste.hamburg.de/HH_WFS_Verwaltungsgrenzen?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature
      &TYPENAMES=de.hh.up:bezirke&OUTPUTFORMAT=application/geo%2Bjson&SRSNAME=EPSG:4326      (ebenso de.hh.up:stadtteile)
Aufruf: python3 werkzeuge/gebiete_erzeugen.py bezirke.json stadtteile.json [Stand JJJJ-MM-TT]
Die Grenzen ändern sich sehr selten; die Datei liegt im Repository, damit die Karte ohne weiteren Abruf funktioniert.
Vereinfachung: Douglas-Peucker mit etwa 8 m Toleranz, Koordinaten auf 5 Nachkommastellen (rund 1 m). Die Insel Neuwerk
(Bezirk Hamburg-Mitte, Nordsee) bleibt weg, damit der Kartenausschnitt die Stadt zeigt.
"""
import json
import math
import sys
from datetime import date
from pathlib import Path

TOLERANCE = 0.00008          # Grad, in der Breite etwa 9 m
WEST = 9.6                   # westlich davon: Neuwerk/Scharhörn
KV = {'Altona': '5100', 'Bergedorf': '5200', 'Eimsbüttel': '5300', 'Harburg': '5400', 'Hamburg-Mitte': '5500', 'Hamburg-Nord': '5600', 'Wandsbek': '5700'}


def simplify(points, tol):
    """Douglas-Peucker ohne Rekursion; Längen mit cos(53,5°) entzerrt."""
    if len(points) < 4:
        return points
    k = math.cos(math.radians(53.55))
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay, bx, by = points[a][0] * k, points[a][1], points[b][0] * k, points[b][1]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, index = 0.0, None
        for i in range(a + 1, b):
            px, py = points[i][0] * k, points[i][1]
            d = abs(dy * (px - ax) - dx * (py - ay)) / norm if norm else math.hypot(px - ax, py - ay)
            if d > worst:
                worst, index = d, i
        if index is not None and worst > tol:
            keep[index] = True
            stack += [(a, index), (index, b)]
    return [p for p, k_ in zip(points, keep) if k_]


def ring(points):
    # geschlossener Ring: an der Mitte teilen, damit Anfang = Ende die Vereinfachung nicht aushebelt
    half = len(points) // 2
    out = simplify(points[:half + 1], TOLERANCE)[:-1] + simplify(points[half:], TOLERANCE)
    out = [[round(x, 5), round(y, 5)] for x, y in out]
    clean = [out[0]]
    for p in out[1:]:
        if p != clean[-1]:
            clean.append(p)
    return clean if len(clean) >= 4 else None


def polygons(geometry):
    polys = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
    out = []
    for poly in polys:
        if max(x for x, _ in poly[0]) < WEST:
            continue
        rings = [r for r in (ring([(p[0], p[1]) for p in part]) for part in poly) if r]
        if rings and len(rings[0]) >= 4:
            out.append(rings)
    return out


def main(bezirke, stadtteile, stand):
    b = json.loads(Path(bezirke).read_text(encoding='utf-8'))['features']
    s = json.loads(Path(stadtteile).read_text(encoding='utf-8'))['features']
    name = lambda f, *keys: next(str(f['properties'][k]) for k in keys if f['properties'].get(k))   # noqa: E731
    out = {'stand': stand, 'quelle': 'Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung – ALKIS Verwaltungsgrenzen',
           'lizenz': 'Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0)',
           'bezirke': sorted(({'name': name(f, 'bezirk_name'), 'kv': KV[name(f, 'bezirk_name')], 'flaechen': polygons(f['geometry'])} for f in b), key=lambda x: x['name']),
           'stadtteile': sorted(({'name': name(f, 'stadtteil_name'), 'bezirk': name(f, 'bezirk_name'), 'flaechen': polygons(f['geometry'])} for f in s), key=lambda x: x['name'])}
    assert len(out['bezirke']) == 7 and all(x['flaechen'] for x in out['bezirke']), 'Bezirke unvollständig'
    out['stadtteile'] = [x for x in out['stadtteile'] if x['flaechen']]
    target = Path(__file__).resolve().parent.parent / 'karte-gebiete.json'
    target.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    count = sum(len(r) for x in out['bezirke'] + out['stadtteile'] for p in x['flaechen'] for r in p)
    print(f"{target.name}: {len(out['bezirke'])} Bezirke, {len(out['stadtteile'])} Stadtteile, {count} Punkte, {target.stat().st_size // 1024} KB")


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat())
