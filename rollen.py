"""Rollen und Rechte im Serverbetrieb (ab 0.34).

- Ein Recht ist ein Bereich (`AREAS`) mit einer Stufe: nichts, „lesen“ oder „schreiben“ (schreiben schließt lesen ein).
- Eine Rolle ist ein Name mit Rechten (`roles.rights`, JSON). Die Rollen und ihre Rechte sind in der Verwaltung
  einstellbar; `DEFAULT_ROLES` ist nur der Anfangsbestand. Eine Person kann mehrere Rollen haben (`user_roles`),
  es gilt je Bereich die höchste Stufe.
- Jede Adresse des Servers steht in `GET`/`POST` mit dem nötigen Recht (mehrere Einträge = eines davon genügt).
  Unbekannte Adressen verlangen „Verwaltung schreiben“ (`required`) – `test_rollen.py` prüft, dass keine Adresse fehlt.
- Die Gesamtdaten (`/api/data`) werden je Bereich geleert (`filter_snapshot`): gleiche Form, aber ohne Inhalt.
- Die Mac-App hat eine Person und prüft keine Rechte (`app.py` ruft diese Prüfung nur im Serverbetrieb).
- Es bleibt immer mindestens eine aktive Person mit „Verwaltung schreiben“ (`_check_admin_remains`).
"""
import json
import unicodedata
from datetime import datetime

LEVELS = ('', 'lesen', 'schreiben')
RANK = {level: rank for rank, level in enumerate(LEVELS)}

# Bereich, Bezeichnung, Erklärung für die Verwaltung
AREAS = (
    ('mitglieder', 'Mitgliederverwaltung', 'Mitglieder mit Stammdaten (auch Beitragshöhe und Zahlweg), Klärfälle mit dem Betrag der Zahlung, Karte, Gliederung, Import der Mitgliederliste'),
    ('buchhaltung', 'Buchhaltung', 'Kontoauszüge, Beitragseingänge, Kasse, Rechnungen, Offene Posten, Journal, Lastschrift, Beträge je Mitglied'),
    ('verband', 'Verband', 'Haushalt, Budgets der Kreisverbände, RPJ-Mittel'),
    ('auswertung', 'Auswertung', 'Mitgliederstatistik in Zahlen; Beitragsbeträge nur mit Buchhaltung, Listen mit Namen nur mit Mitgliederverwaltung'),
    ('einstellungen', 'Einstellungen', 'Stammdaten des Verbands, Schalter, E-Mail-Konto'),
    ('sicherung', 'Sicherung', 'lesen: Stand der Sicherung; schreiben: Sicherung herunterladen (enthält alle Daten)'),
    ('verwaltung', 'Benutzer und Rollen', 'Personen anlegen, Rollen und Rechte ändern'),
    ('sicherheit', 'Sicherheit', 'lesen: Lagebild, Ereignisse, Notfall auslösen; ändern: Sitzungen und Sperren, Notfall beenden, Wartung, Dateiprüfung (ab 0.35)'),
)
AREA_IDS = tuple(area for area, _, _ in AREAS)
ALL = {area: 'schreiben' for area in AREA_IDS}

READ_ALL = {'mitglieder': 'lesen', 'buchhaltung': 'lesen', 'verband': 'lesen', 'auswertung': 'lesen'}
LEITUNG = {**READ_ALL, 'sicherheit': 'lesen'}   # sieht die Sicherheitslage und darf im Ernstfall den Notfall auslösen
# shortcut: der Anfangsbestand gilt nur für eine Datenbank ohne Rollen – 0.34 lief nirgends produktiv, deshalb keine Ergänzung
# bestehender Rollen um „sicherheit“; bei einer späteren Rechte-Erweiterung mit bestehenden Rollen eine einmalige Übernahme bauen
DEFAULT_ROLES = (   # Entscheidung Lennert Gust 12.11.2026; in der Verwaltung änderbar
    ('Schatzmeister', {'mitglieder': 'lesen', 'buchhaltung': 'schreiben', 'verband': 'schreiben', 'auswertung': 'lesen',
                       'einstellungen': 'schreiben', 'sicherung': 'schreiben', 'sicherheit': 'lesen'}),
    ('Landesmitgliederbeauftragter', {'mitglieder': 'schreiben', 'auswertung': 'lesen'}),
    ('Landesgeschäftsführer', LEITUNG),
    ('Landesvorsitzender', LEITUNG),
    ('Administrator', {'verwaltung': 'schreiben', 'sicherung': 'lesen', 'sicherheit': 'schreiben'}),
)
OWNER_ROLES = ('Schatzmeister', 'Landesmitgliederbeauftragter', 'Administrator')   # zusammen: alles schreiben

FREE = 'frei'   # jede angemeldete Person


def _w(*areas): return tuple((area, 'schreiben') for area in areas)
def _r(*areas): return tuple((area, 'lesen') for area in areas)


_B, _M, _V = 'buchhaltung', 'mitglieder', 'verband'
GET = {
    # die Oberfläche lädt Gesamtdaten, Haushalt und Arbeitsdaten immer – der Server leert, wofür das Recht fehlt
    '/api/data': FREE, '/api/haushalt': FREE, '/api/workflow': FREE, '/api/karte-gebiete': FREE,
    **{path: _r(_B) for path in (
        '/api/suggestions', '/api/dues-preview', '/api/invoice.pdf', '/api/invoices.pdf', '/api/preview.pdf',
        '/api/mail-preview', '/api/kasse', '/api/kasse.pdf', '/api/kasse.csv', '/api/kasse-datei', '/api/bill-file',
        '/api/refund-form.pdf', '/api/bill-transfers', '/api/bill-qr.svg', '/api/monatsbericht.pdf', '/api/pruefpaket.zip',
        '/api/konten', '/api/konto', '/api/konto.csv', '/api/beitraege-offen', '/api/member-dues', '/api/member-account.pdf',
        '/api/op-liste', '/api/op-liste.pdf', '/api/op-liste.csv', '/api/op-listen.zip', '/api/mitgliederkonten.csv',
        '/api/donations', '/api/sepa', '/api/mandate.pdf', '/api/reminders', '/api/quarter-review',
        '/api/quarter-review.pdf', '/api/matches', '/api/abgleich', '/api/mail-status', '/api/journal.csv')},
    **{path: _r(_V) for path in ('/api/haushalt.pdf', '/api/rpj-projekt', '/api/rpj.pdf', '/api/rpj-projekt.pdf', '/api/rpj.csv',
                                  '/api/rpj-buchungen.csv', '/api/rpj-vorlage.csv', '/api/kv-ledger', '/api/kv.pdf', '/api/kv-detail')},
    # der Buchungsplatz braucht Haushaltstitel und RPJ-Projekte
    **{path: _r(_V, _B) for path in ('/api/haushalt-reference', '/api/rpj', '/api/rpj-offen')},
    **{path: _r(_M) for path in ('/api/karte', '/api/klaerfaelle', '/api/member-account')},
    '/api/beitrag-nach-alter': _r(_M, _B),
    '/api/adressen-status': _r(_M, 'einstellungen', 'verwaltung'),
    **{path: _r('auswertung') for path in ('/api/auswertung', '/api/auswertung.pdf', '/api/auswertung.csv')},
    '/api/backup': _w('sicherung'),
    # Lastschriftdatei: enthält die IBANs im Klartext und vermerkt den Abruf – das ist kein Lesen
    '/api/sepa.xml': _w(_B),
    '/api/benutzer': _r('verwaltung'),
    # Sicherheit (0.35): Lage und Ereignisse lesen; Sitzungen, Konten und System nur mit „ändern“
    **{path: _r('sicherheit') for path in ('/api/sicherheit', '/api/sicherheit-ereignisse', '/api/sicherheit-ereignisse.csv')},
    '/api/sicherheit-verwaltung': _w('sicherheit'),
}
POST = {
    '/api/logout': FREE, '/api/password': FREE,
    **{path: _w('einstellungen') for path in ('/api/settings', '/api/mail-password', '/api/mail-forget', '/api/mail-check', '/api/mail-test')},
    **{path: _w(_V) for path in (
        '/api/haushalt-plan', '/api/haushalt-import', '/api/kv-plan', '/api/kv-values', '/api/kv-label', '/api/preview-kv-master',
        '/api/import-kv-master', '/api/rpj-jahr', '/api/rpj-projekt', '/api/rpj-projekt-loeschen', '/api/rpj-abruf',
        '/api/rpj-abruf-loeschen', '/api/rpj-import-vorschau', '/api/rpj-import', '/api/kv-close', '/api/kv-transfer',
        '/api/kv-transfer-reverse', '/api/budget')},
    **{path: _w(_V, _B) for path in ('/api/rpj-zuordnen', '/api/rpj-zuordnen-mehrere', '/api/cash-donation')},
    **{path: _w(_M) for path in (
        '/api/member', '/api/members-preview', '/api/members-import', '/api/gliederung-vorschau', '/api/gliederung-import',
        '/api/ortsverband', '/api/ortsverband-status', '/api/ortsverband-loeschen', '/api/kv-nummern', '/api/klaerfall-klaeren',
        '/api/klaerfall-notiz', '/api/member-update', '/api/member-delete')},
    '/api/adresse-pruefen': _r(_M), '/api/strassen-suche': _r(_M),
    '/api/adressen-laden': _w(_M, 'verwaltung'),
    **{path: _w(_B) for path in (
        '/api/sepa-vorschau', '/api/sepa-begleitzettel', '/api/sepa-mandat-vergessen', '/api/sepa-aufteilen', '/api/beitrag-zuordnen',
        '/api/klaerfall-melden', '/api/member-target', '/api/beitraege-vorschlaege', '/api/einvoice', '/api/bill', '/api/kasse-beleg',
        '/api/kasse-anfang', '/api/kasse-ungueltig', '/api/bill-cancel', '/api/bill-transferred', '/api/abgleich', '/api/bill-release',
        '/api/mandate', '/api/mandate-revoke', '/api/sepa-batch', '/api/sepa-batch-status', '/api/sepa-debit-remove',
        '/api/sepa-debit-reject', '/api/debit-notice', '/api/bill-approve', '/api/bill-approval-revoke', '/api/quarter-review',
        '/api/bill-writeoff', '/api/series-preview', '/api/series', '/api/mail-send', '/api/mail-cancel', '/api/dues', '/api/invoice',
        '/api/cancel-invoice', '/api/preview-bank', '/api/preview-master', '/api/import-master', '/api/import-bank', '/api/book',
        '/api/park', '/api/forget', '/api/reverse-bank')},
    **{path: _w('verwaltung') for path in ('/api/benutzer', '/api/rolle', '/api/rolle-loeschen')},
    '/api/bestaetigen': FREE,
    '/api/notfall-an': _r('sicherheit'),   # viele dürfen auslösen, nur „ändern“ darf beenden (ein übernommenes Konto hebt ihn nicht auf)
    **{path: _w('sicherheit') for path in ('/api/notfall-aus', '/api/wartung', '/api/sitzung-beenden', '/api/sperre-aufheben', '/api/dateipruefung')},
}
# ohne Sitzung bzw. nur in der Mac-App (dort keine Rechteprüfung)
OUTSIDE = ('/api/status', '/api/login', '/api/stop', '/api/login-code', '/api/login-device', '/api/shutdown')

# Gesamtdaten: welcher Schlüssel gehört zu welchem Bereich (alles andere sehen alle Angemeldeten)
SNAPSHOT = {
    'members': _M, 'ovs': _M, 'cases': _M,
    'invoices': _B, 'bank': _B, 'dues_open': _B, 'dues_open_all': _B, 'bills': _B, 'reviews_due': _B, 'mandates': _B,
    'sepa_ready': _B, 'sepa_check': _B, 'cash': _B, 'cash_overdue': _B, 'reminders_ready': _B, 'next_numbers': _B,
    'journal': _B, 'unallocated_dues': _B, 'dues_book': _B, 'audit': _B,
    'rpj': _V, 'budgets': _V, 'kv_closing_due': _V, 'kv_transfers': _V,
}
MEMBER_AMOUNTS = ('paid', 'dues_soll', 'dues_basis', 'balance', 'dues_issues')   # Beträge aus der Buchhaltung je Mitglied
# Wer keinen fachlichen Bereich lesen darf (nur Verwaltung/Sicherung oder gar keine Rolle), bekommt von den Stammdaten
# nur, was die Oberfläche zum Anzeigen braucht – keine Bankverbindung, kein E-Mail-Konto, keine Vorlagen
SETTINGS_FREE = ('map_tiles', 'kv_share_auto', 'kv_share_auto_from', 'age_basis')
FACHLICH = (_M, _B, _V, 'einstellungen')


def required(method, path):
    """Nötiges Recht für eine Adresse; unbekannte Adressen verlangen „Verwaltung schreiben“."""
    return (POST if method == 'POST' else GET).get(path, _w('verwaltung'))


def allowed(rights, need):
    if need == FREE:
        return True
    return any(RANK.get(rights.get(area, ''), 0) >= RANK[level] for area, level in need)


def can(rights, area, level='lesen'):
    return RANK.get(rights.get(area, ''), 0) >= RANK[level]


def blank(value):
    """Gleiche Form ohne Inhalt – die Oberfläche bekommt leere Listen und Nullen statt fehlender Schlüssel."""
    if isinstance(value, dict):
        return {key: blank(item) for key, item in value.items()}
    if isinstance(value, list):
        return []
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    return '' if isinstance(value, str) else None


def filter_snapshot(snapshot, rights):
    result = {}
    for key, value in snapshot.items():
        area = SNAPSHOT.get(key)
        result[key] = value if area is None or can(rights, area) else blank(value)
    if can(rights, _M) and not can(rights, _B):
        result['members'] = [{**m, **{key: blank(m.get(key)) for key in MEMBER_AMOUNTS}} for m in result['members']]
    if isinstance(result.get('settings'), dict) and not any(can(rights, area) for area in FACHLICH):
        result['settings'] = {key: value for key, value in result['settings'].items() if key in SETTINGS_FREE}
    return result


def clean_rights(raw):
    """Rechte aus einer Eingabe: nur bekannte Bereiche und Stufen."""
    if not isinstance(raw, dict):
        raise ValueError('Ungültige Rechte.')
    result = {}
    for area, level in raw.items():
        if area not in AREA_IDS or level not in LEVELS:
            raise ValueError('Ungültige Rechte.')
        if level:
            result[area] = level
    return result


def init(db):
    inside = db.in_transaction   # Aufruf mitten in einer Transaktion (on_user_created): nichts vorzeitig festschreiben
    db.execute("CREATE TABLE IF NOT EXISTS roles(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,rights TEXT NOT NULL DEFAULT '{}')")
    db.execute('CREATE TABLE IF NOT EXISTS user_roles(user_id INTEGER NOT NULL REFERENCES users(id),role_id INTEGER NOT NULL REFERENCES roles(id),PRIMARY KEY(user_id,role_id))')
    if 'active' not in {row[1] for row in db.execute('PRAGMA table_info(users)')}:
        db.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))')
    if not db.execute('SELECT 1 FROM roles LIMIT 1').fetchone():
        # Anfangsbestand; wer bisher angemeldet war, durfte alles und behält das
        for name, rights in DEFAULT_ROLES:
            db.execute('INSERT INTO roles(name,rights) VALUES(?,?)', (name, json.dumps(rights, ensure_ascii=False)))
        for user in db.execute('SELECT id FROM users').fetchall():
            _grant_owner(db, user[0])
    if not inside:
        db.commit()


def _grant_owner(db, user_id):
    for name in OWNER_ROLES:
        role = db.execute('SELECT id FROM roles WHERE name=?', (name,)).fetchone()
        if role:
            db.execute('INSERT OR IGNORE INTO user_roles(user_id,role_id) VALUES(?,?)', (user_id, role[0]))


def _admins(db, with_password=True):
    """Aktive Personen mit „Verwaltung schreiben“ – im Serverbetrieb zählen nur solche mit gesetztem Passwort
    (eine aus der Mac-App übernommene Person ohne Passwort kann sich dort nicht anmelden)."""
    rows = db.execute('SELECT u.id,r.rights,u.password FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE u.active=1').fetchall()
    return {row[0] for row in rows if json.loads(row[1] or '{}').get('verwaltung') == 'schreiben'
            and (not with_password or str(row[2] or '').startswith('scrypt$'))}


def on_user_created(db, user_id):
    """Neue Person über den Verwaltungsbefehl oder die Mac-Anmeldung: gibt es noch niemanden für die Verwaltung,
    bekommt sie alle Rechte (erste Person). Sonst bleibt sie ohne Rolle, bis die Verwaltung eine vergibt."""
    init(db)   # Datenbank aus einer älteren Version (z. B. gerade übernommen): Tabellen und Anfangsbestand anlegen
    user = db.execute('SELECT password FROM users WHERE id=?', (user_id,)).fetchone()
    if not _admins(db, with_password=bool(user and str(user[0] or '').startswith('scrypt$'))):
        _grant_owner(db, user_id)


def rights_of(db, name):
    """Rechte einer Person: je Bereich die höchste Stufe ihrer Rollen; inaktiv oder unbekannt = keine."""
    rows = db.execute('SELECT r.rights FROM users u JOIN user_roles ur ON ur.user_id=u.id JOIN roles r ON r.id=ur.role_id WHERE u.name=? AND u.active=1', (name,)).fetchall()
    result = {}
    for row in rows:
        for area, level in json.loads(row[0] or '{}').items():
            if area in AREA_IDS and RANK.get(level, 0) > RANK.get(result.get(area, ''), 0):
                result[area] = level
    return result


def overview(db):
    """Personen, Rollen und Bereiche für die Verwaltung – ohne Passwörter."""
    roles = [{'id': r[0], 'name': r[1], 'rights': json.loads(r[2] or '{}'),
              'users': db.execute('SELECT COUNT(*) FROM user_roles WHERE role_id=?', (r[0],)).fetchone()[0]}
             for r in db.execute('SELECT id,name,rights FROM roles ORDER BY id').fetchall()]
    users = []
    for u in db.execute('SELECT id,name,password,active FROM users ORDER BY name COLLATE NOCASE').fetchall():
        users.append({'id': u[0], 'name': u[1], 'active': bool(u[3]), 'has_password': str(u[2] or '').startswith('scrypt$'),
                      'roles': [r[0] for r in db.execute('SELECT role_id FROM user_roles WHERE user_id=? ORDER BY role_id', (u[0],)).fetchall()]})
    # Protokoll der Verwaltung: wer hat wem welche Rolle gegeben, welche Rechte geändert (ohne Passwörter)
    audit = [{'time': a[0], 'actor': a[1], 'action': a[2], 'detail': json.loads(a[3] or '{}')} for a in db.execute(
        f"SELECT time,actor,action,detail FROM audit WHERE action IN ({','.join('?' * len(AUDIT_ACTIONS))}) ORDER BY id DESC LIMIT 50", AUDIT_ACTIONS).fetchall()]
    return {'users': users, 'roles': roles, 'areas': [{'id': a, 'label': label, 'hint': hint} for a, label, hint in AREAS], 'levels': list(LEVELS), 'audit': audit}


AUDIT_ACTIONS = ('Person angelegt', 'Person geändert', 'Rolle angelegt', 'Rolle geändert', 'Rolle gelöscht', 'Rolle vergeben', 'Rolle entzogen',
                 'Benutzer angelegt (Serverbetrieb)', 'Passwort geändert', 'Benutzer eingerichtet (Anmeldung über macOS)')


def _audit(db, actor, action, detail):
    db.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)',
               (datetime.now().isoformat(timespec='seconds'), actor, action, json.dumps(detail, ensure_ascii=False)))


def _check_admin_remains(db):
    if not _admins(db):
        raise ValueError('Mindestens eine aktive Person muss „Benutzer und Rollen“ ändern dürfen – so nicht gespeichert.')


def _name(value, what, limit):
    """Name in einheitlicher Schreibweise (NFKC); ohne Steuer- und unsichtbare Zeichen – sonst gäbe es Doppelgänger im Protokoll."""
    clean = ' '.join(unicodedata.normalize('NFKC', str(value or '')).split())
    if not 2 <= len(clean) <= limit or any(c in clean for c in '<>"\'\\') or any(unicodedata.category(c).startswith('C') for c in clean):
        raise ValueError(f'Ungültiger Name für {what}.')
    return clean


def _same_name(db, table, name):
    """Kennung eines Eintrags mit gleichem Namen ohne Rücksicht auf Groß-/Kleinschreibung (auch Umlaute – SQLite kann das nur für ASCII)."""
    for row in db.execute(f'SELECT id,name FROM {table}').fetchall():   # table ist fest ('users' oder 'roles'), nie eine Eingabe
        if unicodedata.normalize('NFKC', row[1]).casefold() == name.casefold():
            return row[0]
    return None


def _id(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('Ungültige Angabe.')
    return value


def _role_names(db, user_id):
    return [r[0] for r in db.execute('SELECT ro.name FROM user_roles ur JOIN roles ro ON ro.id=ur.role_id WHERE ur.user_id=? ORDER BY ro.id', (user_id,)).fetchall()]


def save_role(db, data, actor):
    """Rolle anlegen oder ändern (`id` leer = neu). Liefert die Kennung. Das Protokoll hält Rechte vorher und nachher fest."""
    name = _name(data.get('name'), 'die Rolle', 40)
    rights = clean_rights({} if data.get('rights') is None else data['rights'])
    with db:
        db.execute('BEGIN IMMEDIATE')
        other = _same_name(db, 'roles', name)
        if data.get('id') is not None:
            role = db.execute('SELECT id,name,rights FROM roles WHERE id=?', (_id(data['id']),)).fetchone()
            if not role:
                raise ValueError('Rolle nicht gefunden.')
            if other is not None and other != role[0]:
                raise ValueError('Eine Rolle mit diesem Namen gibt es schon.')
            db.execute('UPDATE roles SET name=?,rights=? WHERE id=?', (name, json.dumps(rights, ensure_ascii=False), role[0]))
            rid = role[0]
            _check_admin_remains(db)
            _audit(db, actor, 'Rolle geändert', {'rolle': name, 'vorher': {'name': role[1], 'rechte': json.loads(role[2] or '{}')}, 'rechte': rights})
        else:
            if other is not None:
                raise ValueError('Eine Rolle mit diesem Namen gibt es schon.')
            rid = db.execute('INSERT INTO roles(name,rights) VALUES(?,?)', (name, json.dumps(rights, ensure_ascii=False))).lastrowid
            _audit(db, actor, 'Rolle angelegt', {'rolle': name, 'rechte': rights})
    return rid


def delete_role(db, role_id, actor):
    with db:
        db.execute('BEGIN IMMEDIATE')
        role = db.execute('SELECT id,name FROM roles WHERE id=?', (_id(role_id),)).fetchone()
        if not role:
            raise ValueError('Rolle nicht gefunden.')
        if db.execute('SELECT 1 FROM user_roles WHERE role_id=? LIMIT 1', (role[0],)).fetchone():
            raise ValueError('Diese Rolle ist noch Personen zugeordnet. Bitte zuerst dort entfernen.')
        db.execute('DELETE FROM roles WHERE id=?', (role[0],))
        _audit(db, actor, 'Rolle gelöscht', {'rolle': role[1]})


def save_user(db, data, actor):
    """Person anlegen oder ändern: Name, aktiv, Rollen, optional neues Passwort (neu: Pflicht). Liefert die Kennung.

    Das Passwort wird nur als Hash gespeichert und nie protokolliert. Ein neu gesetztes Passwort beendet die Sitzungen
    dieser Person (Fingerabdruck in `app.user_fingerprint`), ebenso das Deaktivieren. Das Protokoll nennt die Rollen
    vorher und nachher – so bleibt nachvollziehbar, wer wem welche Rechte gegeben hat."""
    import zugang
    name = _name(data.get('name'), 'die Person', 80)
    roles = data.get('roles')
    if not isinstance(roles, list) or not all(isinstance(r, int) and not isinstance(r, bool) for r in roles):
        raise ValueError('Ungültige Rollen.')
    if not isinstance(data.get('active', True), bool):
        raise ValueError('Ungültige Angabe zu „aktiv“.')
    password = data.get('password') or None
    hashed = zugang.hash_password(password) if password else None   # prüft die Anforderungen
    active = 1 if data.get('active', True) else 0
    with db:
        db.execute('BEGIN IMMEDIATE')
        known = {r[0] for r in db.execute('SELECT id FROM roles').fetchall()}
        if not set(roles) <= known:
            raise ValueError('Ungültige Rollen.')
        other = _same_name(db, 'users', name)
        before = None
        if data.get('id') is not None:
            user = db.execute('SELECT id,name,active FROM users WHERE id=?', (_id(data['id']),)).fetchone()
            if not user:
                raise ValueError('Person nicht gefunden.')
            if name != user[1]:
                raise ValueError('Der Name einer Person lässt sich nicht ändern (er steht im Änderungsprotokoll). Bitte eine neue Person anlegen.')
            uid = user[0]
            before = {'aktiv': bool(user[2]), 'rollen': _role_names(db, uid)}
            db.execute('UPDATE users SET active=? WHERE id=?', (active, uid))
            if hashed:
                db.execute('UPDATE users SET password=? WHERE id=?', (hashed, uid))
            what = 'Person geändert'
        else:
            if other is not None:
                raise ValueError('Diesen Namen gibt es schon.')
            if not hashed:
                raise ValueError('Bitte ein Passwort für die neue Person vergeben (sie ändert es danach selbst).')
            uid = db.execute('INSERT INTO users(name,password,active) VALUES(?,?,?)', (name, hashed, active)).lastrowid
            what = 'Person angelegt'
        db.execute('DELETE FROM user_roles WHERE user_id=?', (uid,))
        db.executemany('INSERT INTO user_roles(user_id,role_id) VALUES(?,?)', [(uid, r) for r in sorted(set(roles))])
        _check_admin_remains(db)
        detail = {'benutzer': name, 'aktiv': bool(active), 'rollen': _role_names(db, uid), 'passwort_neu': bool(hashed)}
        if before:
            detail['vorher'] = before
        _audit(db, actor, what, detail)
    return uid
