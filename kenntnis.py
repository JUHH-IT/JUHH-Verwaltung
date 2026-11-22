"""Kenntnisnahme von Änderungen an den Mitglieder-Stammdaten (ab 0.54, REGELN Nr. 35, Wunsch Lennert Gust 22.11.2026).

Jede Änderung gilt sofort. Zusätzlich hält die Datenbank selbst fest, welche Angabe sich von welchem auf welchen Wert geändert hat
(Auslöser auf `members` – jede Stelle, die ein Mitglied ändert, wird erfasst, auch künftige). Die Mitgliederverwaltung sieht je
Mitglied einen Punkt mit Vorher und Jetzt und hakt ihn ab. Beträge (Jahresbeitrag, Kontostand) und die Notiz werden nie festgehalten.
Die alten Werte bleiben bis zum Abhaken und danach `FRIST` Tage; dann werden die Zeilen gelöscht (Datensparsamkeit).
Abschalten: Einstellungen → Schalter (`kenntnis` = no): dann hält die Datenbank nichts fest.
"""
from datetime import date, datetime, timedelta

import mitglieder
from formate import german_date, iban_pretty

FELDER = tuple(f for f in mitglieder.FIELDS if f not in ('dues', 'external_balance'))   # ohne Beträge (Mitgliederverwaltung arbeitet ohne)
JA_NEIN = ('dues_free', 'guest', 'no_mail', 'ov_fest')
DATUM = ('birth', 'joined', 'left_on', 'kv_joined')
ANGELEGT = 'angelegt'   # Feldname der Zeile „Mitglied neu angelegt“
AUSLOESER = 'mitglied_aenderung_v1'   # bei geänderter Feldliste hochzählen – init entfernt die alten
# Vorgänge des Protokolls, die Mitglieder ändern → Herkunft der Änderung (alles andere ordnet nichts zu)
ANLASS = {'Mitglied angelegt': 'hand', 'Mitglied geändert': 'hand', 'Austritt eingetragen': 'hand', 'Austritt zurückgenommen': 'hand',
          'Mitgliederliste importiert': 'import', 'Mitglieder zusammengeführt': 'zusammen'}
SAMMEL = {'import': 'Import der Mitgliederliste', 'abgleich': 'Abgleich der Ortsverbände'}   # viele Mitglieder auf einmal: ein Sammelpunkt
HERKUNFT = {'hand': 'von Hand', 'import': 'Import der Mitgliederliste', 'abgleich': 'Abgleich der Ortsverbände', 'zusammen': 'Zusammenführen',
            'programm': 'Programm'}
FRIST = 30          # Tage, die Abgehaktes sichtbar bleibt
MAX_AUSWAHL = 5000


def _jetzt():
    return datetime.now().isoformat(timespec='seconds')


def init(db):
    """Tabelle und Auslöser (additiv). Nach `mitglieder.init` aufrufen – die Auslöser nennen alle Spalten der Stammdaten."""
    db.execute('CREATE TABLE IF NOT EXISTS mitglied_aenderungen(id INTEGER PRIMARY KEY,'
               'member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,zeit TEXT NOT NULL,feld TEXT NOT NULL,alt TEXT,neu TEXT,'
               'actor TEXT,anlass TEXT,gesehen_am TEXT,gesehen_von TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS mitglied_aenderungen_offen ON mitglied_aenderungen(member_id) WHERE gesehen_am IS NULL')
    db.execute('CREATE INDEX IF NOT EXISTS mitglied_aenderungen_neu ON mitglied_aenderungen(id) WHERE actor IS NULL')
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'mitglied_aenderung_%'").fetchall():
        if not name.startswith(AUSLOESER + '_'):
            db.execute(f'DROP TRIGGER "{name}"')
    an = "COALESCE((SELECT value FROM settings WHERE key='kenntnis'),'yes')<>'no'"
    zeit = "strftime('%Y-%m-%dT%H:%M:%S','now','localtime')"
    leer = lambda f: '0' if f in JA_NEIN else "''"
    zeilen = ''.join(f"INSERT INTO mitglied_aenderungen(member_id,zeit,feld,alt,neu) SELECT NEW.id,{zeit},'{f}',OLD.{f},NEW.{f} "
                     f"WHERE COALESCE(OLD.{f},{leer(f)})<>COALESCE(NEW.{f},{leer(f)});" for f in FELDER)
    db.execute(f'CREATE TRIGGER IF NOT EXISTS {AUSLOESER}_u AFTER UPDATE ON members WHEN {an} BEGIN {zeilen} END')
    db.execute(f"CREATE TRIGGER IF NOT EXISTS {AUSLOESER}_i AFTER INSERT ON members WHEN {an} BEGIN "
               f"INSERT INTO mitglied_aenderungen(member_id,zeit,feld) VALUES(NEW.id,{zeit},'{ANGELEGT}'); END")
    # Zeilen ohne Person, die schon gespeichert sind, stammen von einer Änderung ohne Protokolleintrag (einmalige Übernahmen, Wartung):
    # sie gehören dem Programm. Sonst ordnete `zuordnen` sie später der nächsten Person zu, die irgendein Mitglied ändert.
    if db.execute('SELECT 1 FROM mitglied_aenderungen WHERE actor IS NULL LIMIT 1').fetchone():
        db.execute("UPDATE mitglied_aenderungen SET actor='Programm',anlass='programm' WHERE actor IS NULL")
    db.commit()


def an(db):
    """Ist die Kenntnisnahme eingeschaltet (Einstellung `kenntnis`, Vorgabe an)?"""
    row = db.execute("SELECT value FROM settings WHERE key='kenntnis'").fetchone()
    return not row or row[0] != 'no'


def zuordnen(db, actor, action, detail=None):
    """Frisch festgehaltene Änderungen dieser Verbindung der Person und dem Vorgang zuordnen – ruft `Store.audit` bei jedem Eintrag."""
    anlass = ANLASS.get(action)
    if not anlass or not db.execute('SELECT 1 FROM mitglied_aenderungen WHERE actor IS NULL LIMIT 1').fetchone():
        return
    if isinstance(detail, dict) and detail.get('grund') == 'Ortsverband nach Adresse':
        anlass = 'abgleich'
    eigen = db.execute("SELECT value FROM settings WHERE key='kenntnis_eigene'").fetchone()
    gesehen = None
    if eigen and eigen[0] == 'yes':   # eigene Änderungen der Mitgliederverwaltung gelten als gesehen
        import rollen
        if rollen.can(rollen.rights_of(db, actor), 'mitglieder', 'schreiben'):
            gesehen = _jetzt()
    db.execute('UPDATE mitglied_aenderungen SET actor=?,anlass=?,gesehen_am=?,gesehen_von=? WHERE actor IS NULL',
               (actor, anlass, gesehen, actor if gesehen else None))


def _gleich(feld, a, b):
    leer = 0 if feld in JA_NEIN else ''
    return (leer if a is None else a) == (leer if b is None else b)


def _punkte(rows):
    """Zeilen (nach id) zu Punkten je Schlüssel: je Angabe der erste alte und der letzte neue Wert; ohne Unterschied entfällt die Angabe."""
    punkte = {}
    for r in rows:
        p = punkte.setdefault(r['schluessel'], {'id': r['member_id'], 'felder': {}, 'neu': False, 'bis': 0})
        p['bis'], p['zeit'], p['actor'], p['anlass'] = r['id'], r['zeit'], r['actor'], r['anlass']
        p['gesehen_am'], p['gesehen_von'] = r['gesehen_am'], r['gesehen_von']
        if r['feld'] == ANGELEGT:
            p['neu'] = True
        elif r['feld'] in p['felder']:
            p['felder'][r['feld']][1] = r['neu']
        else:
            p['felder'][r['feld']] = [r['alt'], r['neu']]
    for p in punkte.values():
        p['felder'] = {f: v for f, v in p['felder'].items() if not _gleich(f, v[0], v[1])}
    return [p for p in punkte.values() if p['neu'] or p['felder']]


def _offen(db):
    rows = db.execute('SELECT *,member_id AS schluessel FROM mitglied_aenderungen WHERE gesehen_am IS NULL ORDER BY id').fetchall()
    return _punkte(rows)


def offen_ids(db):
    """Kennungen der Mitglieder mit offener Änderung – für die Gesamtdaten (Zahl im Menü, Liste „Änderung offen“)."""
    return sorted(p['id'] for p in _offen(db)) if an(db) else []


def _wert(store, feld, value, cache):
    if value is None or value == '':
        return ''
    if feld in JA_NEIN:
        return 'ja' if str(value) not in ('0', '') else 'nein'
    if feld in DATUM:
        return german_date(value)
    if feld == 'kv':
        return cache['kv'].get(value, value)
    if feld == 'ov_id':
        return cache['ov'].get(int(value), 'gelöschter Ortsverband') if str(value).isdigit() else ''
    if feld == 'geworben_von':
        w = store.db.execute('SELECT name,number FROM members WHERE id=?', (value,)).fetchone()
        return f"{w['name']} ({w['number']})" if w else 'gelöschtes Mitglied'
    if feld == 'iban':
        return iban_pretty(value)
    return str(value)


def _fertig(store, punkte):
    """Punkte für die Oberfläche: Name, Gliederung, Angaben mit Bezeichnung und lesbaren Werten; neueste zuerst."""
    import gliederung
    from core import KVS
    cache = {'kv': dict(KVS), 'ov': {o['id']: gliederung.label(o) for o in gliederung.ovs(store)}}
    out = []
    for p in sorted(punkte, key=lambda p: -p['bis']):
        m = store.db.execute('SELECT name,number,kv,ov_id FROM members WHERE id=?', (p['id'],)).fetchone()
        if not m:
            continue
        anlass = p['anlass'] or 'programm'
        out.append({'id': p['id'], 'name': m['name'], 'number': m['number'], 'kv': m['kv'], 'ov': cache['ov'].get(m['ov_id'], ''),
                    'zeit': p['zeit'], 'actor': p['actor'] or 'Programm', 'anlass': anlass, 'herkunft': HERKUNFT.get(anlass, anlass),
                    'sammel': f"{anlass}|{p['actor']}|{p['zeit'][:10]}" if anlass in SAMMEL else None,
                    'sammel_titel': SAMMEL.get(anlass), 'neu': p['neu'], 'bis': p['bis'],
                    'gesehen_am': p['gesehen_am'], 'gesehen_von': p['gesehen_von'],
                    'felder': [{'feld': f, 'titel': mitglieder.LABELS.get(f, f), 'alt': _wert(store, f, v[0], cache),
                                'neu': _wert(store, f, v[1], cache)} for f, v in p['felder'].items()] if not p['neu'] else []})
    return out


def liste(store, today=None):
    """Offene und in den letzten `FRIST` Tagen abgehakte Änderungen je Mitglied; `stand` = letzte gesehene Zeile (für `abhaken`)."""
    if not an(store.db):
        return {'an': False, 'offen': [], 'abgehakt': [], 'stand': 0, 'frist': FRIST}
    ab = ((today or date.today()) - timedelta(days=FRIST)).isoformat()
    rows = store.db.execute("SELECT *,member_id||'|'||gesehen_am||'|'||gesehen_von AS schluessel FROM mitglied_aenderungen WHERE gesehen_am>=? ORDER BY id", (ab,)).fetchall()
    stand = store.db.execute('SELECT COALESCE(MAX(id),0) FROM mitglied_aenderungen').fetchone()[0]
    return {'an': True, 'offen': _fertig(store, _offen(store.db)), 'abgehakt': _fertig(store, _punkte(rows)), 'stand': stand, 'frist': FRIST}


def _kennungen(ids):
    if not isinstance(ids, list) or not ids or len(ids) > MAX_AUSWAHL:
        raise ValueError('Bitte mindestens ein Mitglied auswählen.')
    if any(not isinstance(x, int) or isinstance(x, bool) or not 0 < x < 2 ** 62 for x in ids):
        raise ValueError('Ungültige Auswahl.')
    return sorted(set(ids))


def abhaken(store, ids, bis, actor):
    """Offene Änderungen der Mitglieder `ids` zur Kenntnis nehmen – nur Zeilen bis `bis` (der Stand, den die Person gesehen hat)."""
    ids = _kennungen(ids)
    if not isinstance(bis, int) or isinstance(bis, bool) or bis < 1:
        raise ValueError('Bitte die Liste neu laden.')
    jetzt, zeilen, mitgliederzahl = _jetzt(), 0, 0
    with store.db:
        for mid in ids:
            n = store.db.execute('UPDATE mitglied_aenderungen SET gesehen_am=?,gesehen_von=? WHERE member_id=? AND gesehen_am IS NULL AND id<=?',
                                 (jetzt, actor, mid, bis)).rowcount
            zeilen += n
            mitgliederzahl += 1 if n else 0
        if mitgliederzahl:
            store.audit(actor, 'Änderungen zur Kenntnis genommen', {'mitglieder': mitgliederzahl, 'angaben': zeilen})
    return {'mitglieder': mitgliederzahl}


def bereinigen(store, today=None):
    """Abgehakte Zeilen nach `FRIST` Tagen löschen; offene Zeilen ohne Unterschied (hin und zurück geändert) als erledigt löschen."""
    ab = ((today or date.today()) - timedelta(days=FRIST)).isoformat()
    with store.db:
        n = store.db.execute('DELETE FROM mitglied_aenderungen WHERE gesehen_am IS NOT NULL AND gesehen_am<?', (ab,)).rowcount
        sichtbar = {p['id'] for p in _offen(store.db)}
        for (mid,) in store.db.execute('SELECT DISTINCT member_id FROM mitglied_aenderungen WHERE gesehen_am IS NULL AND actor IS NOT NULL').fetchall():
            if mid not in sichtbar:
                n += store.db.execute('DELETE FROM mitglied_aenderungen WHERE member_id=? AND gesehen_am IS NULL', (mid,)).rowcount
    return n
