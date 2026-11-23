"""Kenntnisnahme von Änderungen an den Mitglieder-Stammdaten (ab 0.54, REGELN Nr. 35, Wunsch Lennert Gust 22.11.2026).

Jede Änderung gilt sofort. Zusätzlich hält die Datenbank selbst fest, welche Angabe sich von welchem auf welchen Wert geändert hat
(Auslöser auf `members` – jede Stelle, die ein Mitglied ändert, wird erfasst, auch künftige). Die Mitgliederverwaltung sieht je
Mitglied einen Punkt mit Vorher und Jetzt und hakt ihn ab. Beträge (Jahresbeitrag, Kontostand) und die Notiz werden nie festgehalten.
Die alten Werte bleiben bis zum Abhaken und danach `FRIST` Tage; dann werden die Zeilen gelöscht (Datensparsamkeit).
Abschalten: Einstellungen → Schalter (`kenntnis` = no): dann hält die Datenbank nichts fest.
Ab 0.55: einzelne Angaben lassen sich ausnehmen (`kenntnis_ohne`), eine Höchstfrist löscht nie abgehakte Änderungen
(`kenntnis_hoechstfrist`, 0 = keine), und das Abhaken steht im Verlauf des Mitglieds (`ABGEHAKT` im Protokoll mit den Kennungen).
"""
from datetime import date, datetime, timedelta

import mitglieder
from formate import german_date

FELDER = tuple(f for f in mitglieder.FIELDS if f not in ('dues', 'external_balance'))   # ohne Beträge (Mitgliederverwaltung arbeitet ohne)
JA_NEIN = ('dues_free', 'guest', 'no_mail', 'ov_fest')
DATUM = ('birth', 'joined', 'left_on', 'kv_joined')
ANGELEGT = 'angelegt'   # Feldname der Zeile „Mitglied neu angelegt“
AUSLOESER = 'mitglied_aenderung_v2'   # bei geänderter Feldliste hochzählen – init entfernt die alten
# Vorgänge des Protokolls, die Mitglieder ändern → Herkunft der Änderung (alles andere ordnet nichts zu)
ANLASS = {'Mitglied angelegt': 'hand', 'Mitglied geändert': 'hand', 'Austritt eingetragen': 'hand', 'Austritt zurückgenommen': 'hand',
          'Mitgliederliste importiert': 'import', 'Mitglieder zusammengeführt': 'zusammen',
          'Mitglied gelöscht': 'hand'}   # Löschen nimmt bei den Geworbenen den Verweis „geworben von“ weg
SAMMEL = {'import': 'Import der Mitgliederliste', 'abgleich': 'Abgleich der Ortsverbände'}   # viele Mitglieder auf einmal: ein Sammelpunkt
HERKUNFT = {'hand': 'von Hand', 'import': 'Import der Mitgliederliste', 'abgleich': 'Abgleich der Ortsverbände', 'zusammen': 'Zusammenführen',
            'programm': 'Programm'}
FRIST = 30          # Tage, die Abgehaktes sichtbar bleibt
MAX_AUSWAHL = 5000
HOECHSTFRISTEN = ('0', '90', '180', '365')   # Tage; 0 = nie abgehakte Änderungen bleiben, bis jemand abhakt
ABGEHAKT = 'Änderungen zur Kenntnis genommen'   # Vorgang im Protokoll; mitglieder.verlauf zeigt ihn am Mitglied


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
    ohne = "(','||COALESCE((SELECT value FROM settings WHERE key='kenntnis_ohne'),'')||',')"   # ausgenommene Angaben: gar nicht erst festhalten
    zeilen = ''.join(f"INSERT INTO mitglied_aenderungen(member_id,zeit,feld,alt,neu) SELECT NEW.id,{zeit},'{f}',OLD.{f},NEW.{f} "
                     f"WHERE COALESCE(OLD.{f},{leer(f)})<>COALESCE(NEW.{f},{leer(f)}) AND instr({ohne},',{f},')=0;" for f in FELDER)
    db.execute(f'CREATE TRIGGER IF NOT EXISTS {AUSLOESER}_u AFTER UPDATE ON members WHEN {an} BEGIN {zeilen} END')
    db.execute(f"CREATE TRIGGER IF NOT EXISTS {AUSLOESER}_i AFTER INSERT ON members WHEN {an} BEGIN "
               f"INSERT INTO mitglied_aenderungen(member_id,zeit,feld) VALUES(NEW.id,{zeit},'{ANGELEGT}'); END")
    # Zeilen ohne Person, die schon gespeichert sind, stammen von einer Änderung ohne Protokolleintrag (einmalige Übernahmen, Wartung):
    # sie gehören dem Programm. Sonst ordnete `zuordnen` sie später der nächsten Person zu, die irgendein Mitglied ändert.
    if db.execute('SELECT 1 FROM mitglied_aenderungen WHERE actor IS NULL LIMIT 1').fetchone():
        db.execute("UPDATE mitglied_aenderungen SET actor='Programm',anlass='programm' WHERE actor IS NULL")
    db.commit()


def felder():
    """Angaben, die festgehalten werden können, mit Beschriftung – für die Auswahl „ausnehmen“ in den Einstellungen."""
    return [[f, mitglieder.LABELS.get(f, f)] for f in FELDER]


def ohne(db):
    """Angaben, die nicht festgehalten werden (Einstellung `kenntnis_ohne`, mit Komma getrennt)."""
    row = db.execute("SELECT value FROM settings WHERE key='kenntnis_ohne'").fetchone()
    return {f for f in (row[0] if row else '').split(',') if f in FELDER}


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


def _sammel(r):
    """Sammelschlüssel einer Zeile: Import und Abgleich je Person und Tag; alles andere (von Hand, Zusammenführen, Programm) = einzeln."""
    return f"{r['anlass']}|{r['actor']}|{r['zeit'][:10]}" if r['anlass'] in SAMMEL else None


def _punkte(rows, schluessel=lambda r: ()):
    """Zeilen (nach id) zu Punkten je Mitglied und Herkunft: was von Hand geändert wurde, steht nie in einem Sammelpunkt und umgekehrt.
    Je Angabe der erste alte und der letzte neue Wert; ohne Unterschied entfällt die Angabe. Mehrere Personen stehen alle am Punkt."""
    punkte = {}
    for r in rows:
        sammel = _sammel(r)
        p = punkte.setdefault((r['member_id'], sammel, *schluessel(r)), {'id': r['member_id'], 'sammel': sammel, 'felder': {}, 'neu': False,
                                                                        'bis': 0, 'personen': [], 'anlaesse': []})
        p['bis'], p['zeit'] = r['id'], r['zeit']
        p['gesehen_am'], p['gesehen_von'] = r['gesehen_am'], r['gesehen_von']
        for liste, wert in ((p['personen'], r['actor'] or 'Programm'), (p['anlaesse'], r['anlass'] or 'programm')):
            if wert not in liste:
                liste.append(wert)
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
    return _punkte(db.execute('SELECT * FROM mitglied_aenderungen WHERE gesehen_am IS NULL ORDER BY id').fetchall())


def kurz(db):
    """Für die Gesamtdaten: eingeschaltet?, Kennungen der Mitglieder mit offener Änderung (Liste „Änderung offen“, Lage im Cockpit) und
    Zahl der offenen Punkte (Menü – dieselbe Zahl wie „Offen“ auf der Seite; ein Mitglied kann einzeln und in einem Sammelpunkt stehen)."""
    punkte = _offen(db) if an(db) else []
    return {'an': an(db), 'offen': sorted({p['id'] for p in punkte}), 'punkte': len(punkte)}


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
    if feld == 'iban':   # volle IBAN nur im geöffneten Mitglied (REGELN Nr. 34 b) – hier wie bei den Mandaten verkürzt
        import lastschrift
        return lastschrift.masked(value)
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
        anlass = p['anlaesse'][-1]
        out.append({'id': p['id'], 'name': m['name'], 'number': m['number'], 'kv': m['kv'], 'ov': cache['ov'].get(m['ov_id'], ''),
                    'zeit': p['zeit'], 'actor': ', '.join(p['personen']), 'anlass': anlass,
                    'herkunft': ', '.join(HERKUNFT.get(a, a) for a in p['anlaesse']),
                    'sammel': p['sammel'], 'sammel_titel': SAMMEL.get(anlass) if p['sammel'] else None, 'neu': p['neu'], 'bis': p['bis'],
                    'gesehen_am': p['gesehen_am'], 'gesehen_von': p['gesehen_von'],
                    'felder': [{'feld': f, 'titel': mitglieder.LABELS.get(f, f), 'alt': _wert(store, f, v[0], cache),
                                'neu': _wert(store, f, v[1], cache)} for f, v in p['felder'].items()] if not p['neu'] else []})
    return out


def liste(store, today=None):
    """Offene und in den letzten `FRIST` Tagen abgehakte Änderungen je Mitglied; `stand` = letzte gesehene Zeile (für `abhaken`)."""
    if not an(store.db):
        return {'an': False, 'offen': [], 'abgehakt': [], 'stand': 0, 'frist': FRIST}
    ab = ((today or date.today()) - timedelta(days=FRIST)).isoformat()
    rows = store.db.execute('SELECT * FROM mitglied_aenderungen WHERE gesehen_am>=? ORDER BY id', (ab,)).fetchall()
    stand = store.db.execute('SELECT COALESCE(MAX(id),0) FROM mitglied_aenderungen').fetchone()[0]
    return {'an': True, 'offen': _fertig(store, _offen(store.db)), 'abgehakt': _fertig(store, _punkte(rows, lambda r: (r['gesehen_am'], r['gesehen_von']))), 'stand': stand, 'frist': FRIST}


def _auswahl(punkte):
    """Eingabe prüfen: [{id: Mitglied, sammel: Sammelschlüssel oder None}] → [(id, None | (anlass, actor, tag))]."""
    if not isinstance(punkte, list) or not punkte or len(punkte) > MAX_AUSWAHL:
        raise ValueError('Bitte mindestens ein Mitglied auswählen.')
    out = []
    for p in punkte:
        mid, sammel = (p.get('id'), p.get('sammel')) if isinstance(p, dict) else (None, None)
        if not isinstance(mid, int) or isinstance(mid, bool) or not 0 < mid < 2 ** 62 or not (sammel is None or isinstance(sammel, str)):
            raise ValueError('Ungültige Auswahl.')
        teile = None
        if sammel is not None:
            anlass, _, rest = sammel.partition('|')
            actor, _, tag = rest.rpartition('|')
            if anlass not in SAMMEL or not actor or len(tag) != 10:
                raise ValueError('Ungültige Auswahl.')
            teile = (anlass, actor, tag)
        out.append((mid, teile))
    return out


def abhaken(store, punkte, bis, actor):
    """Offene Punkte zur Kenntnis nehmen – je Mitglied genau den gezeigten Ausschnitt (einzeln geändert oder ein Sammelpunkt) und nur
    Zeilen bis `bis` (der Stand, den die Person gesehen hat)."""
    punkte = _auswahl(punkte)
    if not isinstance(bis, int) or isinstance(bis, bool) or not 0 < bis < 2 ** 62:
        raise ValueError('Bitte die Liste neu laden.')
    jetzt, zeilen, mitgliederzahl = _jetzt(), 0, set()
    einzeln = f"COALESCE(anlass,'') NOT IN ({','.join('?' * len(SAMMEL))})"
    with store.db:
        for mid, sammel in punkte:
            wo, werte = ('anlass=? AND actor=? AND substr(zeit,1,10)=?', sammel) if sammel else (einzeln, tuple(SAMMEL))
            n = store.db.execute(f'UPDATE mitglied_aenderungen SET gesehen_am=?,gesehen_von=? WHERE member_id=? AND gesehen_am IS NULL AND id<=? AND {wo}',
                                 (jetzt, actor, mid, bis, *werte)).rowcount
            zeilen += n
            if n:
                mitgliederzahl.add(mid)
        if zeilen:
            store.audit(actor, ABGEHAKT, {'mitglieder': len(mitgliederzahl), 'angaben': zeilen, 'ids': sorted(mitgliederzahl)})   # ids: für den Verlauf am Mitglied
    return {'mitglieder': len(mitgliederzahl)}


def bereinigen(store, today=None):
    """Abgehakte Zeilen nach `FRIST` Tagen löschen; offene Zeilen ohne Unterschied (hin und zurück geändert) löschen; ist die
    Kenntnisnahme ausgeschaltet, alles löschen (dann soll das Programm keine alten Werte aufbewahren). Ebenso Zeilen ausgenommener
    Angaben und – mit Höchstfrist – offene Zeilen, die älter sind (das steht im Protokoll, nur mit der Anzahl).
    Läuft stündlich und gleich nach dem Speichern der Einstellungen."""
    heute = today or date.today()
    ab = (heute - timedelta(days=FRIST)).isoformat()
    with store.db:
        if not an(store.db):
            return store.db.execute('DELETE FROM mitglied_aenderungen').rowcount
        n = store.db.execute('DELETE FROM mitglied_aenderungen WHERE gesehen_am IS NOT NULL AND gesehen_am<?', (ab,)).rowcount
        weg = sorted(ohne(store.db))
        if weg:
            n += store.db.execute(f"DELETE FROM mitglied_aenderungen WHERE feld IN ({','.join('?' * len(weg))})", weg).rowcount
        frist = store.db.execute("SELECT value FROM settings WHERE key='kenntnis_hoechstfrist'").fetchone()
        tage = int(frist[0]) if frist and frist[0] in HOECHSTFRISTEN else 0
        if tage:
            alt = store.db.execute('DELETE FROM mitglied_aenderungen WHERE gesehen_am IS NULL AND zeit<?', ((heute - timedelta(days=tage)).isoformat(),)).rowcount
            if alt:
                store.audit('Programm', 'Nicht abgehakte Änderungen nach Höchstfrist gelöscht', {'angaben': alt, 'tage': tage})
            n += alt
        sichtbar = {(p['id'], p['sammel']) for p in _offen(store.db)}
        for r in store.db.execute('SELECT * FROM mitglied_aenderungen WHERE gesehen_am IS NULL AND actor IS NOT NULL').fetchall():
            if (r['member_id'], _sammel(r)) not in sichtbar:
                n += store.db.execute('DELETE FROM mitglied_aenderungen WHERE id=?', (r['id'],)).rowcount
    return n
