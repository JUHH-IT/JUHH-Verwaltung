"""Sicherheit im Serverbetrieb (ab 0.35): Ereignisse, Notfall- und Wartungsmodus, Dateiprüfung, Lagebild.

- Ereignisse (`sicherheit_ereignisse`): Anmeldungen, Fehlversuche, Sperren, verweigerte Zugriffe, Passwortbestätigungen,
  beendete Sitzungen, Notfall/Wartung, Sicherungen. Nie Passwörter, nie Mitglieder- oder Finanzdaten. Die Änderungen an
  Personen und Rollen stehen im Änderungsprotokoll (`audit`, rollen.AUDIT_ACTIONS) und werden hier dazugemischt.
- Zustand (`sicherheit_zustand`): Notfall, Wartung, Freischaltung des Notzugangs, letzte Dateiprüfung – bewusst nicht in
  `settings`, die gehen mit den Gesamtdaten an die Oberfläche.
- Sperre: Im Notfall- oder Wartungsmodus arbeiten nur Personen mit „Sicherheit ändern“ (`app.py` prüft jede Anfrage).
- Dateiprüfung: Programmdateien gegen die Prüfliste, die `server/install.sh` beim Installieren anlegt (`server/PRUEFLISTE`).
"""
import csv
import hashlib
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import rollen

ARTEN = {
    'anmeldung': 'Anmeldung',
    'anmeldung_fehl': 'Fehlanmeldung',
    'notzugang': 'Notzugang',   # Keycloak-Betrieb: Anmeldung mit dem Passwort der Verwaltung
    'notzugang_auf': 'Notzugang freigeschaltet',   # auf dem Server: sudo ju-finanzen notzugang
    'notzugang_zu': 'Notzugang geschlossen',
    'bestaetigung_fehl': 'Passwortbestätigung falsch',
    'verweigert': 'Zugriff verweigert',
    'sitzung_beendet': 'Sitzung beendet',
    'sperre_aufgehoben': 'Sperre aufgehoben',
    'notfall_an': 'Notfall ausgelöst',
    'notfall_aus': 'Notfall beendet',
    'wartung_an': 'Wartung eingeschaltet',
    'wartung_aus': 'Wartung ausgeschaltet',
    'sicherung': 'Sicherung heruntergeladen',
    'dateipruefung': 'Dateiprüfung',
    # Abwehr (schutz.py, ab 0.39)
    'schutz_sperre': 'Adresse gesperrt',
    'schutz_frei': 'Adresse freigegeben',
    'schutz_einstellung': 'Firewall-Einstellungen geändert',
    'schutz_liste': 'Bedrohungsliste geladen',
    'schutz_mitschnitt': 'Mitschnitt gestartet',
    'schutz_aus': 'Abwehr auf dem Server zurückgesetzt',   # sudo ju-finanzen schutz-aus
}
AUFBEWAHREN_TAGE = 400
UNGENUTZT_TAGE = 90
NOTZUGANG_HOECHSTENS = 240   # Minuten; Standard von `sudo ju-finanzen notzugang`: 60
PRUEFLISTE = 'server/PRUEFLISTE'
NICHT_PRUEFEN = ('.git',)   # venv und __pycache__ gehören dazu: ein eingeschleustes .pth oder .pyc wirkt genauso


def init(db):
    inside = db.in_transaction
    db.execute('CREATE TABLE IF NOT EXISTS sicherheit_ereignisse(id INTEGER PRIMARY KEY,zeit TEXT NOT NULL,art TEXT NOT NULL,'
               "person TEXT NOT NULL DEFAULT '',geraet TEXT NOT NULL DEFAULT '',detail TEXT NOT NULL DEFAULT '{}')")
    db.execute('CREATE INDEX IF NOT EXISTS sicherheit_ereignisse_zeit ON sicherheit_ereignisse(zeit)')
    db.execute('CREATE TABLE IF NOT EXISTS sicherheit_zustand(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    if not inside:
        db.commit()


def _jetzt():
    return datetime.now().isoformat(timespec='seconds')


def ereignis(db, art, person='', geraet='', detail=None):
    """Ereignis festhalten (eigene Transaktion). Ältere Einträge als AUFBEWAHREN_TAGE fallen dabei weg."""
    assert art in ARTEN, art
    with db:
        db.execute('INSERT INTO sicherheit_ereignisse(zeit,art,person,geraet,detail) VALUES(?,?,?,?,?)',
                   (_jetzt(), art, str(person or '')[:80], str(geraet or '')[:80], json.dumps(detail or {}, ensure_ascii=False)))
        db.execute('DELETE FROM sicherheit_ereignisse WHERE zeit<?', ((datetime.now() - timedelta(days=AUFBEWAHREN_TAGE)).isoformat(),))


def _lesen(db, key):
    row = db.execute('SELECT value FROM sicherheit_zustand WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _schreiben(db, key, value):
    if value is None:
        db.execute('DELETE FROM sicherheit_zustand WHERE key=?', (key,))
    else:
        db.execute('INSERT OR REPLACE INTO sicherheit_zustand(key,value) VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))


def sperre(db):
    """Aktive Sperre: Notfall vor Wartung; None = normaler Betrieb."""
    for art in ('notfall', 'wartung'):
        z = _lesen(db, art)
        if z:
            return {**z, 'art': art}
    return None


def darf_trotz_sperre(rights):
    return rollen.can(rights, 'sicherheit', 'schreiben')


def notfall_an(db, actor, grund, geraet=''):
    grund = ' '.join(str(grund or '').split())[:300]
    if len(grund) < 3:
        raise ValueError('Bitte kurz den Grund für den Notfall angeben.')
    with db:
        db.execute('BEGIN IMMEDIATE')   # zwei gleichzeitige Auslösungen: nur eine gilt
        if _lesen(db, 'notfall'):
            raise ValueError('Der Notfallmodus ist bereits aktiv.')
        _schreiben(db, 'notfall', {'seit': _jetzt(), 'von': actor, 'grund': grund})
    ereignis(db, 'notfall_an', actor, geraet, {'grund': grund})


def notfall_aus(db, actor, geraet=''):
    with db:
        db.execute('BEGIN IMMEDIATE')
        z = _lesen(db, 'notfall')
        if not z:
            raise ValueError('Der Notfallmodus ist nicht aktiv.')
        _schreiben(db, 'notfall', None)
    ereignis(db, 'notfall_aus', actor, geraet, {'seit': z['seit'], 'von': z['von']})


def wartung(db, actor, an, text='', geraet=''):
    with db:
        db.execute('BEGIN IMMEDIATE')
        aktiv = bool(_lesen(db, 'wartung'))
        if an == aktiv:
            return
        _schreiben(db, 'wartung', {'seit': _jetzt(), 'von': actor, 'text': ' '.join(str(text or '').split())[:300]} if an else None)
    ereignis(db, 'wartung_an' if an else 'wartung_aus', actor, geraet)


def notzugang_offen(db):
    """Freischaltung des Notzugangs ({'bis', 'von'}) oder None. Im Keycloak-Betrieb gilt die Passwort-Anmeldung nur solange."""
    z = _lesen(db, 'notzugang')
    return z if z and z.get('bis', '') > _jetzt() else None


def notzugang_setzen(db, minuten, actor, geraet=''):
    """Notzugang für `minuten` freischalten (1 … NOTZUGANG_HOECHSTENS) bzw. mit 0 schließen. Liefert das Ende oder None."""
    if not isinstance(minuten, int) or not 0 <= minuten <= NOTZUGANG_HOECHSTENS:
        raise ValueError(f'Der Notzugang lässt sich für 1 bis {NOTZUGANG_HOECHSTENS} Minuten freischalten.')
    bis = (datetime.now() + timedelta(minutes=minuten)).isoformat(timespec='seconds') if minuten else None
    with db:
        _schreiben(db, 'notzugang', {'bis': bis, 'von': actor} if bis else None)
    ereignis(db, 'notzugang_auf' if bis else 'notzugang_zu', actor, geraet, {'bis': bis} if bis else {})
    return bis


def ereignisse(db, art='', tage=30, suche='', limit=1000):
    """Sicherheitsereignisse und Änderungen an Personen/Rollen, neueste zuerst."""
    seit = (datetime.now() - timedelta(days=max(1, min(int(tage), AUFBEWAHREN_TAGE)))).isoformat(timespec='seconds')
    # shortcut: die Suche läuft über die jüngsten `limit` Einträge je Quelle, nicht über alles – reicht bei 400 Tagen Aufbewahrung
    rows = []
    if art != 'verwaltung':
        rows = [{'zeit': r[0], 'art': r[1], 'text': ARTEN.get(r[1], r[1]), 'person': r[2], 'geraet': r[3], 'detail': json.loads(r[4] or '{}')}
                for r in db.execute('SELECT zeit,art,person,geraet,detail FROM sicherheit_ereignisse WHERE zeit>=? AND (?=\'\' OR art=?) ORDER BY zeit DESC LIMIT ?',
                                    (seit, art, art, limit))]
    if not art or art == 'verwaltung':
        marks = ','.join('?' * len(rollen.AUDIT_ACTIONS))
        rows += [{'zeit': r[0], 'art': 'verwaltung', 'text': r[2], 'person': r[1], 'geraet': '', 'detail': json.loads(r[3] or '{}')}
                 for r in db.execute(f'SELECT time,actor,action,detail FROM audit WHERE action IN ({marks}) AND time>=? ORDER BY id DESC LIMIT ?',
                                     (*rollen.AUDIT_ACTIONS, seit, limit))]
    if suche:
        needle = suche.casefold()
        rows = [r for r in rows if needle in json.dumps(r, ensure_ascii=False).casefold()]
    rows.sort(key=lambda r: r['zeit'], reverse=True)
    return rows[:limit]


def ereignisse_csv(rows):
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['Zeit', 'Ereignis', 'Person', 'Gerät', 'Einzelheiten'])
    for r in rows:
        cells = [r['zeit'], r['text'], r['person'], r['geraet'], json.dumps(r['detail'], ensure_ascii=False)]
        w.writerow(["'" + c if c.startswith(('=', '+', '-', '@', '\t', '\r')) else c for c in cells])   # Formelschutz wie in berichte.py
    return '﻿' + out.getvalue()


def _dateien(app_dir):
    # wie `find -type f` in install.sh: Verknüpfungen (venv/bin/python, lib64) zählen nicht
    # shortcut: ein ausgetauschtes Verknüpfungsziel fällt so nicht auf; erst prüfen, wenn Verknüpfungen in der Prüfliste stehen
    for folder, dirs, files in os.walk(app_dir):
        dirs[:] = sorted(d for d in dirs if d not in NICHT_PRUEFEN and not os.path.islink(os.path.join(folder, d)))
        for name in sorted(files):
            p = Path(folder) / name
            rel = p.relative_to(app_dir).as_posix()
            if not p.is_symlink() and rel != PRUEFLISTE:
                yield rel, p


def dateipruefung(app_dir):
    """Programmdateien gegen die Prüfliste: veränderte, fehlende und zusätzliche Dateien (z. B. ein eingeschleustes Modul)."""
    liste = Path(app_dir) / PRUEFLISTE
    if not liste.is_file():
        return {'verfuegbar': False, 'zeit': _jetzt()}
    soll = {}
    for line in liste.read_text(encoding='utf-8').splitlines():
        digest, _, name = line.partition('  ')
        if name:
            soll[name[2:] if name.startswith('./') else name] = digest
    geaendert, zusaetzlich, gesehen = [], [], set()
    for rel, p in _dateien(app_dir):
        gesehen.add(rel)
        if rel not in soll:
            zusaetzlich.append(rel)
        elif hashlib.sha256(p.read_bytes()).hexdigest() != soll[rel]:
            geaendert.append(rel)
    fehlend = sorted(set(soll) - gesehen)
    return {'verfuegbar': True, 'zeit': _jetzt(), 'dateien': len(soll), 'geaendert': geaendert, 'fehlend': fehlend,
            'liste': hashlib.sha256(liste.read_bytes()).hexdigest()[:12],   # zum Abgleich mit `sha256sum server/PRUEFLISTE` auf dem Server
            'zusaetzlich': zusaetzlich, 'ok': not (geaendert or fehlend or zusaetzlich)}


def dateipruefung_lauf(db, app_dir, actor='', geraet=''):
    ergebnis = dateipruefung(app_dir)
    with db:
        _schreiben(db, 'dateipruefung', ergebnis)
    if ergebnis['verfuegbar']:
        ereignis(db, 'dateipruefung', actor or 'automatisch', geraet,
                 {'ok': ergebnis['ok'], 'geaendert': len(ergebnis['geaendert']), 'fehlend': len(ergebnis['fehlend']), 'zusaetzlich': len(ergebnis['zusaetzlich'])})
    return ergebnis


def letzte_dateipruefung(db, app_dir):
    """Letztes Ergebnis; älter als einen Tag → jetzt neu prüfen (dauert unter einer Sekunde)."""
    z = _lesen(db, 'dateipruefung')
    if not z or z.get('zeit', '') < (datetime.now() - timedelta(days=1)).isoformat():
        z = dateipruefung_lauf(db, app_dir)
    return z


def konten(db):
    """Personen mit letzter Anmeldung, Verwaltungs-/Sicherheitsrechten und Hinweisen (ungenutzt, ohne Passwort, deaktiviert;
    `app` = Authenticator-Pflicht im Keycloak-Betrieb)."""
    zuletzt = dict(db.execute("SELECT person,max(zeit) FROM sicherheit_ereignisse WHERE art IN ('anmeldung','notzugang') GROUP BY person").fetchall())
    grenze = (datetime.now() - timedelta(days=UNGENUTZT_TAGE)).isoformat()
    result = []
    # aktiv = darf hinein (rollen.GILT): abgelaufene Personen („gültig bis“ vorbei) zählen wie deaktivierte
    for name, password, active, app in db.execute(f'SELECT name,password,{rollen.GILT},totp_pflicht FROM users ORDER BY name COLLATE NOCASE').fetchall():
        rights = rollen.rights_of(db, name)
        result.append({'name': name, 'aktiv': bool(active), 'passwort': str(password or '').startswith('scrypt$'), 'app': bool(app),
                       'zuletzt': zuletzt.get(name, ''), 'ungenutzt': bool(active) and zuletzt.get(name, '') < grenze,
                       'privilegiert': any(rollen.can(rights, a, 'schreiben') for a in ('verwaltung', 'sicherheit', 'sicherung')),
                       'rechte': rights})
    return result


def lagebild(db, sitzungen, sperren, letzte_sicherung, app_dir):
    tag = (datetime.now() - timedelta(days=1)).isoformat()
    woche = (datetime.now() - timedelta(days=7)).isoformat()
    zaehle = lambda art, seit: db.execute('SELECT count(*) FROM sicherheit_ereignisse WHERE art=? AND zeit>=?', (art, seit)).fetchone()[0]
    k = konten(db)
    return {
        'sperre': sperre(db), 'notzugang': notzugang_offen(db),
        'fehlanmeldungen_24h': zaehle('anmeldung_fehl', tag), 'fehlanmeldungen_7t': zaehle('anmeldung_fehl', woche),
        'verweigert_7t': zaehle('verweigert', woche), 'bestaetigung_fehl_7t': zaehle('bestaetigung_fehl', woche),
        'sitzungen': len(sitzungen), 'gesperrt': len(sperren),
        'konten_aktiv': sum(1 for x in k if x['aktiv']), 'konten_ungenutzt': sum(1 for x in k if x['ungenutzt']),
        'konten_privilegiert': sum(1 for x in k if x['aktiv'] and x['privilegiert']),
        'letzte_sicherung': letzte_sicherung, 'dateipruefung': letzte_dateipruefung(db, app_dir),
    }
