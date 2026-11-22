"""Ortsverband nach der Adresse (ab 0.52, Wunsch Lennert Gust 22.11.2026): Gebiete der Ortsverbände, Vorschlag je Anschrift und
Abgleich aller aktiven Mitglieder.

Bezirk = Kreisverband (adressen.KV); Stadtteil → Ortsverband über die Tabelle `ortsverband_gebiete`, gepflegt unter Einstellungen →
Gliederung. Ein Stadtteil darf zu mehreren Ortsverbänden gehören (dann wählt die Person). Alles ist ein Vorschlag – übernommen wird nur,
was ausgewählt wird. Die Satzung erlaubt die Mitgliedschaft in einem anderen Kreis-/Ortsverband: Mitglieder mit „Ortsverband fest“
(`members.ov_fest`) schlägt der Abgleich nie vor. Verortet wird nur lokal (adressen.py); Antworten nennen je Mitglied nur Kennungen.
"""
import re
from datetime import date

import adressen
import gliederung
import mitglieder

SONDER = ('LV', '5800')   # Landesverband und Schüler Union: kein Kreisverband nach dem Wohnort
MAX_AUSWAHL = 5000


def init(db):
    """Tabelle der Gebiete (additiv): je Ortsverband die Stadtteile – Namen wie im amtlichen Adressbestand."""
    db.execute('CREATE TABLE IF NOT EXISTS ortsverband_gebiete(stadtteil TEXT NOT NULL CHECK(length(stadtteil) BETWEEN 1 AND 60),'
               'ov_id INTEGER NOT NULL REFERENCES ortsverbaende(id) ON DELETE CASCADE,PRIMARY KEY(stadtteil,ov_id))')
    db.commit()


def gebiete(db):
    """{ov_id: [Stadtteil, …]} – nur Ortsverbände mit Gebiet."""
    out = {}
    for stadtteil, ov in db.execute('SELECT stadtteil,ov_id FROM ortsverband_gebiete ORDER BY stadtteil COLLATE NOCASE'):
        out.setdefault(ov, []).append(stadtteil)
    return out


def stadtteile(data_dir):
    """Stadtteile aus den amtlichen Adressdaten [{'name', 'bezirk', 'kv'}]; ohne Adressdaten leer."""
    ix = adressen.index(data_dir)
    if not ix:
        return []
    try:
        return ix.stadtteile()
    finally:
        ix.close()


def _woerter(text):
    return {gliederung.fold(w) for w in re.split(r'[\s\-/,.()&+]+', str(text or '')) if gliederung.fold(w)}


def _enthaelt(name, stadtteil):
    """Stadtteil als ganze Wortfolge im Namen des Ortsverbands („Altona-Nord“ in „Altona-Nord/Sternschanze“, nicht „Hamm“ in „Hammerbrook“)."""
    w = lambda text: [gliederung.fold(x) for x in re.split(r'[\s\-/,.()&+]+', str(text or '')) if gliederung.fold(x)]   # noqa: E731
    n, s = w(name), w(stadtteil)
    return bool(s) and any(n[i:i + len(s)] == s for i in range(len(n) - len(s) + 1))


def namensvorschlag(ovs, teile, vorhanden):
    """Gebiet aus dem Namen für aktive Ortsverbände ohne Gebiet – nur ein Vorschlag zum Bestätigen. {ov_id: [Stadtteil]}
    Zuerst ganze Namen („Altona-Nord“ steht im Namen des Ortsverbands); Stadtteile ohne solchen Treffer danach über ihre langen
    Wortteile („Hamburg-Altstadt“ → Ortsverband „Altstadt“, „Hoheluft-West“ → „Eimsbüttel-Nord/Hoheluft“). „Hamburg“ allein zählt nie."""
    aktiv = [o for o in ovs if o['active']]
    ganz = {o['id']: [t['name'] for t in teile if t['kv'] == o['kv'] and _enthaelt(o['name'], t['name'])] for o in aktiv}
    vergeben = {n for treffer in ganz.values() for n in treffer}
    out = {}
    for o in aktiv:
        if o['id'] in vorhanden:
            continue
        woerter = _woerter(o['name'])
        lang = lambda name: {w for w in _woerter(name) if len(w) >= 5 and w != 'hamburg'}   # noqa: E731
        teil = [t['name'] for t in teile if t['kv'] == o['kv'] and t['name'] not in vergeben and lang(t['name']) and lang(t['name']) <= woerter]
        treffer = sorted(set(ganz[o['id']] + teil), key=gliederung.fold)
        if treffer:
            out[o['id']] = treffer
    return out


def gebiet_speichern(store, data_dir, ov_id, namen, actor):
    """Stadtteile eines Ortsverbands festlegen (ersetzt die bisherigen). Mit Adressdaten muss jeder Stadtteil im Bezirk des
    Kreisverbands liegen."""
    ov_id = _kennung(ov_id, 'Ortsverband')
    ov = store.db.execute('SELECT id,kv,name FROM ortsverbaende WHERE id=?', (ov_id,)).fetchone()
    if not ov:
        raise ValueError('Ortsverband nicht gefunden.')
    if not isinstance(namen, list) or len(namen) > 200 or not all(isinstance(n, str) for n in namen):
        raise ValueError('Stadtteile ungültig.')
    namen = sorted({' '.join(n.split()) for n in namen if n.strip()}, key=gliederung.fold)
    if any(len(n) > 60 for n in namen):
        raise ValueError('Stadtteile ungültig.')
    bekannt = {t['name']: t['kv'] for t in stadtteile(data_dir)}
    if bekannt:
        fremd = [n for n in namen if bekannt.get(n) != ov['kv']]
        if fremd:
            raise ValueError(f"Nicht im Bezirk des Kreisverbands: {', '.join(fremd[:5])}.")
    with store.db:
        store.db.execute('DELETE FROM ortsverband_gebiete WHERE ov_id=?', (ov_id,))
        store.db.executemany('INSERT INTO ortsverband_gebiete(stadtteil,ov_id) VALUES(?,?)', [(n, ov_id) for n in namen])
        store.audit(actor, 'Gebiet eines Ortsverbands geändert', {'id': ov_id, 'stadtteile': len(namen)})
    return namen


def ovs_fuer(db, kv, stadtteil):
    """Aktive Ortsverbände eines Kreisverbands, zu deren Gebiet der Stadtteil gehört (Vorschlag im Mitgliedsdialog und im Abgleich)."""
    if not kv or not stadtteil:
        return []
    return [r[0] for r in db.execute(
        'SELECT o.id FROM ortsverband_gebiete g JOIN ortsverbaende o ON o.id=g.ov_id WHERE g.stadtteil=? AND o.kv=? AND o.active=1 '
        'ORDER BY o.number=0,o.number,o.name COLLATE NOCASE', (stadtteil, kv))]


def _hinweis(r):
    import karte
    return karte._hint(r)


def abgleich(store, data_dir, today=None):
    """Alle aktiven Mitglieder mit dem Ortsverband nach ihrer Adresse vergleichen.
    aendern: genau ein Vorschlag (`ov`); mehrdeutig: Stadtteil gehört zu mehreren Ortsverbänden (`ovs`); ohne_gebiet: Stadtteil keinem
    Ortsverband zugeordnet; fehler: Adresse außerhalb Hamburgs, nicht gefunden oder leer (`art`, `hinweis`). `kv_anders`: der
    Wohnort liegt in einem anderen Kreisverband. Zahlen: passt, fest (Ortsverband fest), sonder (Landesverband/Schüler Union)."""
    today = today or date.today().isoformat()
    out = {'verfuegbar': False, 'gebiete': bool(store.db.execute('SELECT 1 FROM ortsverband_gebiete LIMIT 1').fetchone()),
           'aendern': [], 'mehrdeutig': [], 'ohne_gebiet': [], 'fehler': [], 'passt': 0, 'fest': 0, 'sonder': 0}
    ix = adressen.index(data_dir)
    if not ix:
        return out
    out['verfuegbar'] = True
    cache = {}
    try:
        for m in store.rows("SELECT id,kv,ov_id,ov_fest,street,zip,city,country FROM members WHERE COALESCE(left_on,'')='' OR left_on>? ORDER BY id",
                            (today,)):
            if m['kv'] in SONDER:
                out['sonder'] += 1
                continue
            if m['ov_fest']:
                out['fest'] += 1
                continue
            abroad = bool(m['country']) and mitglieder._fold(m['country']) not in mitglieder.DOMESTIC   # wie karte.data
            r = ix.lookup(m['street'], m['zip'], m['city']) if not abroad else {'status': 'ausserhalb', 'stadtteil': '', 'kv': ''}
            if r['status'] in ('unbekannt', 'ausserhalb', 'leer') or not r['stadtteil'] or not r['kv']:
                out['fehler'].append({'id': m['id'], 'art': r['status'] if r['status'] in ('unbekannt', 'ausserhalb', 'leer') else 'unbekannt',
                                      'hinweis': _hinweis(r) or 'Adresse ohne Stadtteil'})
                continue
            key = (r['kv'], r['stadtteil'])
            if key not in cache:
                cache[key] = ovs_fuer(store.db, *key)
            ovs = cache[key]
            eintrag = {'id': m['id'], 'kv': r['kv'], 'stadtteil': r['stadtteil'], 'kv_alt': m['kv'], 'ov_alt': m['ov_id'],
                       'kv_anders': r['kv'] != m['kv'], 'hinweis': _hinweis(r) if r['status'] != 'haus' else ''}
            if m['kv'] == r['kv'] and m['ov_id'] in ovs:
                out['passt'] += 1
            elif not ovs:
                out['ohne_gebiet'].append(eintrag)
            elif len(ovs) > 1:
                out['mehrdeutig'].append({**eintrag, 'ovs': ovs})
            else:
                out['aendern'].append({**eintrag, 'ov': ovs[0]})
    finally:
        ix.close()
    return out


def _kennung(x, name, leer=False):
    """Ganze Zahl aus der Eingabe (int oder Ziffernfolge, nie bool oder Kommazahl); leer=True erlaubt None."""
    if x is None and leer:
        return None
    if isinstance(x, int) and not isinstance(x, bool) and 0 < x < 2 ** 62:
        return x
    if isinstance(x, str) and x.strip().isdigit() and 0 < len(x.strip()) < 19:
        return int(x)
    raise ValueError(f'{name}: ungültige Auswahl.')


def auswahl(aenderungen, fest):
    """Eingabe der Übernahme prüfen (vor der Sicherung): [{id, ov, ov_alt, kv_alt}] und [id]. ov_alt/kv_alt = Stand, den die Person
    gesehen hat – weicht das Mitglied inzwischen davon ab (von Hand geändert), wird es nicht überschrieben."""
    if not isinstance(aenderungen, list) or not isinstance(fest, list) or len(aenderungen) + len(fest) > MAX_AUSWAHL \
            or not all(isinstance(a, dict) for a in aenderungen):
        raise ValueError('Änderungen: ungültige Auswahl.')
    paare = {}
    for a in aenderungen:
        mid = _kennung(a.get('id'), 'Änderungen')
        if mid in paare or not isinstance(a.get('kv_alt'), str):
            raise ValueError('Änderungen: ungültige Auswahl.')
        paare[mid] = (_kennung(a.get('ov'), 'Änderungen'), _kennung(a.get('ov_alt'), 'Änderungen', leer=True), a['kv_alt'])
    fest_ids = {_kennung(x, 'Ortsverband fest') for x in fest} - set(paare)
    if not paare and not fest_ids:
        raise ValueError('Nichts ausgewählt.')
    return paare, fest_ids


def uebernehmen(store, data_dir, aenderungen, fest, actor, today=None):
    """Ausgewählte Vorschläge übernehmen – der Kreisverband folgt dem Ortsverband – und Mitglieder als „Ortsverband fest“ markieren.
    In einer Transaktion wird alles neu berechnet: Passt ein Vorschlag nicht mehr (Adresse, Gebiet oder Ortsverband inzwischen
    geändert) oder steht ein Mitglied gar nicht im Abgleich, bleibt es unverändert und zählt als übersprungen. Der Aufrufer prüft
    vorher mit `auswahl` und sichert die Datenbank."""
    paare, fest_ids = auswahl(aenderungen, fest)
    ovs = gliederung.ov_map(store.db)
    geaendert = uebersprungen = markiert = 0
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')                 # Abgleich und Änderung sehen denselben Stand
        jetzt = abgleich(store, data_dir, today)
        erlaubt = {e['id']: {e['ov']} for e in jetzt['aendern']} | {e['id']: set(e['ovs']) for e in jetzt['mehrdeutig']}
        im_abgleich = set(erlaubt) | {e['id'] for e in jetzt['ohne_gebiet'] + jetzt['fehler']}
        for mid, (ov, ov_alt, kv_alt) in paare.items():
            old = store.db.execute('SELECT kv,ov_id,number FROM members WHERE id=?', (mid,)).fetchone()
            if not old or ov not in erlaubt.get(mid, ()) or ov not in ovs or (old['kv'], old['ov_id']) != (kv_alt, ov_alt):
                uebersprungen += 1
                continue
            kv = ovs[ov]['kv']
            store.db.execute('UPDATE members SET kv=?,ov_id=? WHERE id=?', (kv, ov, mid))
            store.audit(actor, 'Mitglied geändert', {'id': mid, 'number': old['number'], 'grund': 'Ortsverband nach Adresse',
                                                     'fields': [k for k, neu in (('kv', kv), ('ov_id', ov)) if old[k] != neu]})
            geaendert += 1
        for mid in sorted(fest_ids):
            old = store.db.execute('SELECT number,ov_fest FROM members WHERE id=?', (mid,)).fetchone()
            if not old or old['ov_fest'] or mid not in im_abgleich:   # nur Mitglieder, die der Abgleich gerade nennt (aktiv, nicht LV/SU)
                uebersprungen += 1
                continue
            store.db.execute('UPDATE members SET ov_fest=1 WHERE id=?', (mid,))
            store.audit(actor, 'Mitglied geändert', {'id': mid, 'number': old['number'], 'fields': ['ov_fest']})
            markiert += 1
        store.audit(actor, 'Ortsverbände nach Adresse abgeglichen', {'geaendert': geaendert, 'fest': markiert, 'uebersprungen': uebersprungen})
    return {'geaendert': geaendert, 'fest': markiert, 'uebersprungen': uebersprungen}
