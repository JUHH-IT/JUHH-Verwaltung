"""Gliederung: Kreisverbände (mit Nummer der CDU-Mitgliederverwaltung) und Ortsverbände als Stammdaten.

Wunsch Lennert Gust 10.11.2026: „wir benötigen ein paar weitere Stammdaten: … Kreisverbände Ortsverbände“; je Mitglied einstellbar,
Kreisverband als Auswahlliste, Ortsverband nur aus dem gewählten Kreisverband.

Die Kreisverbände selbst bleiben fest (core.KVS: Kostenstellen 5100–5700, Schüler Union 5800) – an ihnen hängen Buchungen und Budgets.
Hier kommen dazu:
- die Nummer des Kreisverbands in der CDU-Mitgliederverwaltung (Spalte LVKV_Nummer, z. B. 701 = Altona) – änderbar, erkennt den
  Kreisverband beim Import;
- die Ortsverbände je Kreisverband (Spalten Ortsverband = Nummer, Ortsbezeichnung = Name). Nummer 0 = ohne Nummer (in der
  Mitgliederverwaltung z. B. „Kreisverband“ oder „OV Schüler Union“). Ein Ortsverband mit Nummer > 0 ist je Kreisverband über die
  Nummer eindeutig, ohne Nummer über den Namen.

Mitglieder verweisen über members.ov_id auf den Ortsverband (einzige Quelle; die Textspalten ov_number/ov_name aus 0.24 werden
einmalig in Ortsverbände überführt – die Texte bleiben unverändert stehen, werden aber nicht mehr gelesen oder geschrieben). Ein Ortsverband mit Mitgliedern wird nicht gelöscht, nur deaktiviert
(nicht mehr zur Auswahl; vorhandene Zuordnungen bleiben).
"""
import json
import re
import unicodedata
from datetime import datetime

# Nummern der CDU-Mitgliederverwaltung (Landesverband Hamburg 7, Kreisverbände 701–707) – Vorbelegung, in den Stammdaten änderbar
CDU_NUMBERS = {'5100': '701', '5200': '702', '5300': '703', '5400': '704', '5500': '705', '5600': '706', '5700': '707'}
MAX_NAME = 100
MAX_NUMBER = 9999
DIGITS = re.compile(r'[0-9]+')   # nur 0–9 (str.isdigit ließe auch „²“ oder arabische Ziffern zu)


def digits(text):
    return bool(DIGITS.fullmatch(str(text or '')))


def init(db):
    """Tabelle der Ortsverbände und Nummer der Mitgliederverwaltung je Kreisverband (additiv)."""
    db.executescript('''
    CREATE TABLE IF NOT EXISTS ortsverbaende(id INTEGER PRIMARY KEY,kv TEXT NOT NULL REFERENCES kv(code),
        number INTEGER NOT NULL DEFAULT 0 CHECK(number>=0 AND number<=9999),name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),created TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS ortsverbaende_nummer ON ortsverbaende(kv,number) WHERE number>0;
    CREATE UNIQUE INDEX IF NOT EXISTS ortsverbaende_name ON ortsverbaende(kv,name COLLATE NOCASE) WHERE number=0;
    ''')
    if 'cdu_number' not in [c[1] for c in db.execute('PRAGMA table_info(kv)')]:
        db.execute('ALTER TABLE kv ADD COLUMN cdu_number TEXT')
        for code, number in CDU_NUMBERS.items():
            db.execute('UPDATE kv SET cdu_number=? WHERE code=? AND cdu_number IS NULL', (number, code))
    db.commit()


def fold(text):
    """Vergleichsform eines Namens: ohne Groß-/Kleinschreibung, Akzente, Leer- und Satzzeichen („St. Georg / Borgfelde“ = „St.Georg/Borgfelde“)."""
    text = unicodedata.normalize('NFKD', str(text or '').casefold().replace('ß', 'ss'))
    return ''.join(c for c in text if c.isalnum() and not unicodedata.combining(c))


def clean_name(value):
    return ' '.join(str(value or '').split())


def label(ov):
    """Anzeige „12 Blankenese“; ohne Nummer nur der Name."""
    return f"{ov['number']} {ov['name']}" if ov['number'] else ov['name']


LEGACY_DONE = 'ov_legacy_linked'


def link_legacy(db):
    """Einmalig (Merker in settings): Ortsverband-Angaben aus 0.24 (Textspalten ov_number/ov_name) in Ortsverbände überführen und
    verknüpfen. Die Texte bleiben stehen (nichts geht verloren); danach liest sie niemand mehr. Je Anfrage nur ein lesender Blick."""
    if db.execute('SELECT 1 FROM settings WHERE key=?', (LEGACY_DONE,)).fetchone():
        return 0
    with db:
        if not db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (LEGACY_DONE, datetime.now().isoformat(timespec='seconds'))).rowcount:
            return 0                                  # gleichzeitige Anfrage war schneller
        todo = db.execute("SELECT id,kv,ov_number,ov_name FROM members WHERE ov_id IS NULL AND kv!='LV' "
                          "AND (COALESCE(ov_name,'')!='' OR COALESCE(ov_number,'')!='')").fetchall()
        if not todo:
            return 0
        rows = [dict(r) for r in db.execute('SELECT * FROM ortsverbaende')]
        for mid, kv, number, name in todo:
            num = str(number or '').strip()
            n = int(num) if digits(num) and int(num) <= MAX_NUMBER else 0
            nm = clean_name(name)[:MAX_NAME] or (f'Ortsverband {num}'[:MAX_NAME] if num else 'Ortsverband')
            found, _ = match(rows, kv, n, nm)
            if not found:
                found = _insert(db, kv, n, nm)
                rows.append(found)
            db.execute('UPDATE members SET ov_id=? WHERE id=?', (found['id'], mid))
        db.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)',
                   (datetime.now().isoformat(timespec='seconds'), 'Programm', 'Ortsverbände aus Mitgliederangaben übernommen',
                    json.dumps({'mitglieder': len(todo)})))
    return len(todo)


def kvs(store):
    """Kreisverbände in der festen Reihenfolge von core.KVS mit Nummer der Mitgliederverwaltung."""
    from core import KVS
    numbers = {r['code']: r['cdu_number'] for r in store.db.execute('SELECT code,cdu_number FROM kv')}
    return [{'code': code, 'name': name, 'cdu_number': numbers.get(code) or ''} for code, name in KVS]


def rows(db):
    return [dict(r) for r in db.execute('SELECT id,kv,number,name,active FROM ortsverbaende')]


def ovs(store):
    """Alle Ortsverbände mit Anzahl der zugeordneten Mitglieder (für Auswahl, Filter und Stammdaten)."""
    return [dict(r) for r in store.db.execute(
        'SELECT o.id,o.kv,o.number,o.name,o.active,(SELECT COUNT(*) FROM members m WHERE m.ov_id=o.id) members '
        'FROM ortsverbaende o ORDER BY o.kv,o.number=0,o.number,o.name COLLATE NOCASE')]


def ov_map(db):
    """{id: Ortsverband} für die Prüfung der Mitgliedsdaten (mitglieder.values)."""
    return {r['id']: r for r in rows(db)}


def match(ovs_list, kv, number, name):
    """Vorhandenen Ortsverband finden: mit Nummer über (Kreisverband, Nummer), ohne Nummer über den Namen.
    Liefert (Ortsverband oder None, Hinweis) – Hinweis, wenn der Name im Programm anders lautet."""
    if number:
        for o in ovs_list:
            if o['kv'] == kv and o['number'] == number:
                note = '' if not name or fold(o['name']) == fold(name) else \
                    f"Ortsverband {number} heißt im Programm „{o['name']}“, in der Datei „{clean_name(name)[:40]}“ – Name im Programm bleibt."
                return o, note
        return None, ''
    key = fold(name)
    for o in ovs_list:
        if o['kv'] == kv and o['number'] == 0 and fold(o['name']) == key:
            return o, ''
    return None, ''


def _insert(db, kv, number, name):
    oid = db.execute('INSERT INTO ortsverbaende(kv,number,name,active,created) VALUES(?,?,?,1,?)',
                     (kv, number, name, datetime.now().isoformat(timespec='seconds'))).lastrowid
    return {'id': oid, 'kv': kv, 'number': number, 'name': name, 'active': 1}


def ensure(db, kv, number, name):
    """Ortsverband finden oder anlegen (innerhalb einer laufenden Transaktion des Aufrufers). Liefert (id, neu angelegt)."""
    found, _ = match(rows(db), kv, number, name)
    if found:
        return found['id'], False
    return _insert(db, kv, number, name)['id'], True


def _clean(store, data, current=None):
    from core import KVS
    kv = str(data.get('kv') or '')
    if kv not in dict(KVS) or kv == 'LV':
        raise ValueError('Bitte einen Kreisverband wählen.')
    raw = str(data.get('number') if data.get('number') is not None else '').strip() or '0'
    if not digits(raw) or int(raw) > MAX_NUMBER:
        raise ValueError(f'Nummer des Ortsverbands: 0 bis {MAX_NUMBER} (0 = ohne Nummer).')
    number, name = int(raw), clean_name(data.get('name'))
    if not name:
        raise ValueError('Name des Ortsverbands fehlt.')
    if len(name) > MAX_NAME:
        raise ValueError(f'Name des Ortsverbands: höchstens {MAX_NAME} Zeichen.')
    for o in rows(store.db):
        if current and o['id'] == current['id'] or o['kv'] != kv:
            continue
        if number and o['number'] == number:
            raise ValueError(f"Im Kreisverband gibt es schon den Ortsverband {number} („{o['name']}“).")
        if not number and not o['number'] and fold(o['name']) == fold(name):
            raise ValueError(f"Im Kreisverband gibt es schon den Ortsverband „{o['name']}“ ohne Nummer.")
    return kv, number, name


def save_ov(store, data, actor, ov_id=None):
    """Ortsverband anlegen oder ändern (Nummer, Name; Kreisverband nur ohne zugeordnete Mitglieder)."""
    current = None
    if ov_id is not None:
        current = store.db.execute('SELECT * FROM ortsverbaende WHERE id=?', (ov_id,)).fetchone()
        if not current:
            raise ValueError('Ortsverband nicht gefunden.')
        current = dict(current)
    kv, number, name = _clean(store, data, current)
    with store.db:
        if current:
            if kv != current['kv'] and store.db.execute('SELECT 1 FROM members WHERE ov_id=?', (ov_id,)).fetchone():
                raise ValueError('Dem Ortsverband sind Mitglieder zugeordnet – der Kreisverband kann nicht geändert werden.')
            store.db.execute('UPDATE ortsverbaende SET kv=?,number=?,name=? WHERE id=?', (kv, number, name, ov_id))
            store.audit(actor, 'Ortsverband geändert', {'id': ov_id, 'vorher': label(current), 'nachher': label({'number': number, 'name': name}),
                                                         'kv': kv})
            return ov_id
        new = _insert(store.db, kv, number, name)['id']
        store.audit(actor, 'Ortsverband angelegt', {'id': new, 'kv': kv, 'ortsverband': label({'number': number, 'name': name})})
        return new


def set_active(store, ov_id, active, actor):
    if not isinstance(active, bool):
        raise ValueError('Ungültiger Status.')
    row = store.db.execute('SELECT * FROM ortsverbaende WHERE id=?', (ov_id,)).fetchone()
    if not row:
        raise ValueError('Ortsverband nicht gefunden.')
    with store.db:
        store.db.execute('UPDATE ortsverbaende SET active=? WHERE id=?', (int(active), ov_id))
        store.audit(actor, 'Ortsverband aktiviert' if active else 'Ortsverband deaktiviert', {'id': ov_id, 'ortsverband': label(row)})


def delete_ov(store, ov_id, actor):
    row = store.db.execute('SELECT * FROM ortsverbaende WHERE id=?', (ov_id,)).fetchone()
    if not row:
        raise ValueError('Ortsverband nicht gefunden.')
    count = store.db.execute('SELECT COUNT(*) FROM members WHERE ov_id=?', (ov_id,)).fetchone()[0]
    if count:
        raise ValueError(f"„{label(row)}“ sind {count} Mitglieder zugeordnet – statt löschen bitte deaktivieren.")
    with store.db:
        store.db.execute('DELETE FROM ortsverbaende WHERE id=?', (ov_id,))
        store.audit(actor, 'Ortsverband gelöscht', {'id': ov_id, 'kv': row['kv'], 'ortsverband': label(row)})


def set_cdu_numbers(store, data, actor):
    """Nummern der Mitgliederverwaltung je Kreisverband: {code: Nummer}; leer = keine Nummer. Jede Nummer höchstens einmal."""
    from core import KVS
    if not isinstance(data, dict):
        raise ValueError('Ungültige Angaben.')
    current = {k['code']: k['cdu_number'] for k in kvs(store)}
    new = dict(current)
    for code, value in data.items():
        if code not in dict(KVS) or code == 'LV':
            raise ValueError('Unbekannter Kreisverband.')
        number = str(value if value is not None else '').strip()
        if number and (not digits(number) or len(number) > 6):
            raise ValueError(f'{dict(KVS)[code]}: Nummer der Mitgliederverwaltung nur aus Ziffern (z. B. 701).')
        new[code] = number
    used = [n for n in new.values() if n]
    if len(used) != len(set(used)):
        raise ValueError('Jede Nummer der Mitgliederverwaltung darf nur einem Kreisverband gehören.')
    changed = {code: [current[code], new[code]] for code in new if (current[code] or '') != (new[code] or '')}
    if not changed:
        return 0
    with store.db:
        for code in changed:
            store.db.execute('UPDATE kv SET cdu_number=? WHERE code=?', (new[code] or None, code))
        store.audit(actor, 'Nummern der Mitgliederverwaltung geändert', changed)
    return len(changed)


def kv_by_number(store):
    """{Nummer der Mitgliederverwaltung: Kostenstelle} für den Import."""
    return {r['cdu_number']: r['code'] for r in store.db.execute("SELECT code,cdu_number FROM kv WHERE COALESCE(cdu_number,'')!=''")}


def resolve_kv(numbers, kvs_set, number_raw, name_raw):
    """Kreisverband einer Importzeile: zuerst über die Nummer der Mitgliederverwaltung, sonst über den Namen.
    Liefert (Kostenstelle oder None, Hinweis). Widersprechen sich Nummer und Name, ist die Zeile ein Fehler (ValueError) – sonst
    landeten Mitglieder bzw. Ortsverbände still in der falschen Kostenstelle (Budget, Beitragsanteil)."""
    from mitglieder import kv_code
    number = str(number_raw or '').strip()
    if number.endswith('.0'):
        number = number[:-2]
    by_name = kv_code(name_raw, kvs_set) if clean_name(name_raw) else None
    if number and number in numbers:
        code = numbers[number]
        if by_name and by_name != code:
            raise ValueError(f'Kreisverband-Nummer {number} und Name „{clean_name(name_raw)[:30]}“ passen nicht zusammen – '
                             'Nummer unter Stammdaten → Gliederung prüfen.')
        return code, ''
    if number and by_name:
        return by_name, f'Kreisverband-Nummer {number} ist unbekannt – nach dem Namen zugeordnet (Nummer in den Stammdaten eintragen).'
    return by_name, ''


# ---------------------------------------------------------------- Import der Gliederung

HEADERS = {
    'kv_number': ('lvkvnummer', 'kvnummer', 'kvnr', 'lvkvnr', 'kreisverbandnummer'),
    'kv': ('lvkvname', 'kreisverband', 'kvname', 'kreisverbandname', 'kv'),
    'ov_number': ('ortsverband', 'ovnummer', 'ovnr', 'ortsverbandnummer'),
    'ov_name': ('ortsbezeichnung', 'ortsverbandname', 'ovname'),
}


def map_header(cells):
    found = {}
    for i, cell in enumerate(cells):
        key = fold(cell)
        for field, names in HEADERS.items():
            if key in names and field not in found:
                found[field] = i
                break
    return found if ('kv_number' in found or 'kv' in found) and 'ov_name' in found else None


def preview(store, raw, filename, password=None):
    """Ortsverbände aus einer Datei wie dem Export der Mitgliederverwaltung (eine Zeile je Mitglied oder je Ortsverband):
    LVKV_Nummer, LVKV_Name, Ortsverband, Ortsbezeichnung. Nichts wird gespeichert."""
    import mitglieder
    from core import KVS
    kvs_set = {k for k, _ in KVS}
    _, mapping, data = mitglieder.read_table(raw, filename, map_header, password,
                                             'Kopfzeile nicht erkannt. Benötigt werden LVKV_Nummer oder LVKV_Name sowie Ortsverband und Ortsbezeichnung.')
    numbers = kv_by_number(store)
    existing = rows(store.db)
    cell = lambda row, f: row[mapping[f]] if f in mapping and mapping[f] < len(row) else None
    groups, errors, kv_seen = {}, [], {}
    for row_no, row in data:
        num_raw, name_raw = cell(row, 'kv_number'), cell(row, 'kv')
        num_text = mitglieder._text(num_raw)
        try:
            code, note = resolve_kv(numbers, kvs_set, num_text, mitglieder._text(name_raw))
        except ValueError as e:
            errors.append({'row_no': row_no, 'error': str(e)})
            continue
        if not code or code == 'LV':
            errors.append({'row_no': row_no, 'error': f"Kreisverband nicht erkannt („{(num_text + ' ' + mitglieder._text(name_raw)).strip()[:40]}“)."})
            continue
        seen = kv_seen.setdefault(code, {'code': code, 'file_number': num_text, 'file_name': mitglieder._text(name_raw), 'notes': set()})
        if note:
            seen['notes'].add(note)
        ov_num = mitglieder._text(cell(row, 'ov_number')) or '0'
        ov_name = clean_name(mitglieder._text(cell(row, 'ov_name')))
        if not digits(ov_num) or int(ov_num) > MAX_NUMBER:
            errors.append({'row_no': row_no, 'error': f'Nummer des Ortsverbands nicht erkannt („{ov_num[:10]}“).'})
            continue
        number = int(ov_num)
        if not ov_name and not number:
            continue                                   # Zeile ohne Ortsverband
        if len(ov_name) > MAX_NAME:
            errors.append({'row_no': row_no, 'error': f'Name des Ortsverbands länger als {MAX_NAME} Zeichen.'})
            continue
        key = (code, number, fold(ov_name) if not number else '')
        g = groups.setdefault(key, {'kv': code, 'number': number, 'name': ov_name or f'Ortsverband {number}', 'rows': 0, 'names': set()})
        g['rows'] += 1
        if ov_name:
            g['names'].add(ov_name)
    items = []
    for g in groups.values():
        found, note = match(existing, g['kv'], g['number'], g['name'])
        notes = [note] if note else []
        if len(g['names']) > 1:
            notes.append('In der Datei mit verschiedenen Namen: ' + ', '.join(sorted(g['names']))[:120])
        items.append({'kv': g['kv'], 'number': g['number'], 'name': found['name'] if found else g['name'], 'rows': g['rows'],
                      'action': 'same' if found else 'new', 'id': found['id'] if found else None, 'active': found['active'] if found else 1,
                      'note': ' '.join(notes)})
    order = {code: i for i, (code, _) in enumerate(KVS)}
    items.sort(key=lambda x: (order.get(x['kv'], 99), x['number'] == 0, x['number'], fold(x['name'])))
    kv_list = [{**{k: v for k, v in kv_seen[c].items() if k != 'notes'}, 'note': ' '.join(sorted(kv_seen[c]['notes']))}
               for c in sorted(kv_seen, key=lambda c: order.get(c, 99))]
    return {'filename': filename, 'items': items, 'errors': errors, 'kvs': kv_list,
            'counts': {'new': sum(1 for x in items if x['action'] == 'new'), 'same': sum(1 for x in items if x['action'] == 'same'),
                       'rows': len(data), 'errors': len(errors)}}


def apply_import(store, prev, actor):
    """Neue Ortsverbände der Vorschau anlegen (vorhandene bleiben unverändert) – alles oder nichts."""
    new = [x for x in prev['items'] if x['action'] == 'new']
    if not new:
        raise ValueError('Keine neuen Ortsverbände in der Datei.')
    created = 0
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        for x in new:
            _, added = ensure(store.db, x['kv'], x['number'], x['name'])
            created += added
        store.audit(actor, 'Gliederung importiert', {'datei': prev['filename'], 'ortsverbaende_neu': created})
    return {'created': created}
