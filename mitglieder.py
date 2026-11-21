"""Mitglieder-Stammdaten: Vorname/Nachname und Anschrift in Feldern, Prüfung, Mitgliederliste aus Excel/CSV importieren.

Wunsch Lennert Gust 29.10.2026: „der Name etc. muss auch noch besser in den Stammdaten werden“ und Mitglieder aus einer
Liste übernehmen (Entscheidung: Excel/CSV-Import; Name in Vor-/Nachname, Anschrift in Straße/PLZ/Ort getrennt).

Die bisherigen Felder name („Vorname Nachname“) und address (mehrzeilig) bleiben und werden beim Speichern aus den
Einzelfeldern gebildet – Rechnungen, PDF, E-Mail und Lastschrift lesen weiter name/address. Bestehende Mitglieder werden
einmalig aufgeteilt (nur die neuen Spalten; name/address bleiben unverändert).

Import: Kopfzeile wird erkannt (z. B. Mitgliedsnummer, Vorname, Nachname, Straße, PLZ, Ort, Geburtsdatum, Eintritt,
Kreisverband, E-Mail). Vorschau zeigt je Zeile neu / Änderung / unverändert / Fehler; übernommen wird nur, was ausgewählt
ist. Fehlt eine Spalte, bleibt der vorhandene Wert eines Mitglieds erhalten. Die Datei selbst wird nicht gespeichert.

Ab 0.27 (Wunsch Lennert Gust 10.11.2026): Export der CDU-Mitgliederverwaltung mit allen Spalten (LVKV_Nummer … Email_Dienstlich),
Kreisverband über die Nummer der Mitgliederverwaltung, Ortsverband aus den Stammdaten (gliederung.py; unbekannte Ortsverbände
legt der Import an), kennwortgeschützte Excel-Dateien (Kennwort nur für das Entschlüsseln im Arbeitsspeicher, nie gespeichert).
"""
import csv
import io
import json
import re
import unicodedata
from datetime import date, datetime, timedelta

from formate import email_address, euro, german_date, iso, money

# Angaben der Mitgliederverwaltung (Export der CDU-Mitgliederverwaltung, Wunsch Lennert Gust 08.11.2026: „bitte alles ergänzen, weil eine
# Mitgliederverwaltung folgen wird“). Alter und Jubiläumsjahre der Datei werden nicht gespeichert – sie ergeben sich aus Geburt und Eintritt.
# Ortsverband ab 0.27 über ov_id (Stammdaten gliederung.py); die Textspalten ov_number/ov_name aus 0.24 bleiben nur in der Datenbank.
TEXTS = ('salutation', 'letter_salutation', 'letter_salutation2', 'title', 'name_prefix', 'name_suffix', 'gender', 'nationality', 'clubs',
         'club', 'phone', 'phone_work', 'mobile', 'mobile_work', 'payment_method', 'address_extra2', 'left_reason')   # left_reason ab 0.37
EXTRA = TEXTS + ('kv_joined', 'external_balance', 'dues_free', 'guest', 'no_mail', 'email_work', 'ov_id')
FIELDS = ('number', 'first_name', 'last_name', 'address_extra', 'street', 'zip', 'city', 'country', 'email', 'birth', 'joined', 'left_on', 'kv',
          'dues') + EXTRA
LEGACY = ('ov_number', 'ov_name')
STORED = FIELDS + ('name', 'address')
LABELS = {'number': 'Mitgliedsnummer', 'first_name': 'Vorname', 'last_name': 'Nachname', 'address_extra': 'Adresszusatz (Name 2)',
          'street': 'Straße', 'zip': 'PLZ', 'city': 'Ort', 'country': 'Land', 'email': 'E-Mail privat', 'birth': 'Geburtsdatum', 'joined': 'Eintritt',
          'left_on': 'Austritt', 'left_reason': 'Austrittsgrund', 'kv': 'Kreisverband', 'dues': 'Jahresbeitrag',
          'salutation': 'Anrede', 'letter_salutation': 'Briefanrede 1', 'letter_salutation2': 'Briefanrede 2', 'title': 'Titel',
          'name_prefix': 'Adelsname/Namenszusatz', 'name_suffix': 'Zusatzname', 'gender': 'Geschlecht', 'nationality': 'Nationalität',
          'clubs': 'Vereinigungen', 'club': 'Verein', 'phone': 'Telefon privat', 'phone_work': 'Telefon dienstlich', 'mobile': 'Handy privat',
          'mobile_work': 'Handy dienstlich', 'email_work': 'E-Mail dienstlich', 'payment_method': 'Zahlungsart',
          'address_extra2': 'Adresszusatz 2 (Name 3)', 'kv_joined': 'Eintritt Kreisverband', 'external_balance': 'Kontostand Mitgliederverwaltung',
          'dues_free': 'Beitragsfrei', 'guest': 'Gastmitglied', 'no_mail': 'Postausschluss', 'ov_id': 'Ortsverband',
          # nur Importspalten
          'kv_number': 'Kreisverband (Nr.)', 'ov_number': 'Ortsverband (Nr.)', 'ov_name': 'Ortsbezeichnung'}
MAX_TEXT = 200
PRIVATE = ('gender', 'nationality', 'phone', 'phone_work', 'mobile', 'mobile_work', 'email_work', 'letter_salutation', 'letter_salutation2',
           'payment_method', 'external_balance', 'left_reason', 'note') + LEGACY   # nicht in der Gesamtliste (erst beim Öffnen eines Mitglieds)
PARTICLES = {'von', 'van', 'de', 'der', 'den', 'zu', 'vom', 'zum', 'zur', 'ten', 'ter', 'du', 'da', 'di', 'del', 'la', 'le', 'dos'}
MAX_ROWS = 5000


def next_id(db):
    """Nächste Mitglieds-id: nie eine schon einmal vergebene (auch nicht die eines gelöschten Mitglieds)."""
    return db.execute('SELECT MAX(x)+1 FROM (SELECT COALESCE(MAX(id),0) x FROM members UNION ALL SELECT COALESCE(MAX(id),0) FROM deleted_members)').fetchone()[0]


def insert(db, v):
    """Mitglied mit geprüften Werten (values) anlegen; liefert die id."""
    mid = next_id(db)
    db.execute(f"INSERT INTO members(id,{','.join(STORED)}) VALUES(?,{','.join('?' * len(STORED))})", [mid] + [v[k] for k in STORED])
    return mid


def init(db):
    """Neue Spalten anlegen und vorhandene Mitglieder einmalig aufteilen (additiv; name/address bleiben)."""
    cols = [c[1] for c in db.execute('PRAGMA table_info(members)')]
    added = [col for col in ('first_name', 'last_name', 'address_extra', 'street', 'zip', 'city', 'country') if col not in cols]
    for col in added:
        db.execute(f'ALTER TABLE members ADD COLUMN {col} TEXT')
    extra = {**{col: 'TEXT' for col in TEXTS + LEGACY + ('kv_joined', 'email_work', 'note')}, 'external_balance': 'INTEGER',
             'dues_free': 'INTEGER NOT NULL DEFAULT 0 CHECK(dues_free IN (0,1))', 'guest': 'INTEGER NOT NULL DEFAULT 0 CHECK(guest IN (0,1))',
             'no_mail': 'INTEGER NOT NULL DEFAULT 0 CHECK(no_mail IN (0,1))', 'ov_id': 'INTEGER REFERENCES ortsverbaende(id)'}
    more = [col for col in extra if col not in cols]
    for col in more:                              # ab 0.24 bzw. 0.27: Angaben der Mitgliederverwaltung (additiv)
        db.execute(f'ALTER TABLE members ADD COLUMN {col} {extra[col]}')
    db.execute('CREATE TABLE IF NOT EXISTS dues_rates(member_id INTEGER NOT NULL REFERENCES members(id),valid_from INTEGER NOT NULL,'
               'amount INTEGER CHECK(amount IS NULL OR amount>=0),created TEXT NOT NULL,PRIMARY KEY(member_id,valid_from))')
    # ab 0.28: Nummern gelöschter Mitglieder (nur id + Zeitpunkt), damit eine id nie an eine andere Person geht (Vorschau, zweiter Tab)
    db.execute('CREATE TABLE IF NOT EXISTS deleted_members(id INTEGER PRIMARY KEY,deleted TEXT NOT NULL)')
    if more and not added:
        db.commit()
    todo = db.execute('SELECT id,name,address FROM members WHERE last_name IS NULL').fetchall()
    if not added and not todo:
        return                                    # nichts zu tun: keine Schreibsperre je Anfrage
    for m in todo:
        first, last = split_name(m[1])
        extra, street, zip_code, city, country = split_address(m[2])
        db.execute('UPDATE members SET first_name=?,last_name=?,address_extra=?,street=?,zip=?,city=?,country=? WHERE id=?',
                   (first, last, extra, street, zip_code, city, country, m[0]))
    db.commit()


def _clean(value):
    return ' '.join(str(value or '').split())


def split_name(name):
    """„Anna von Berg“ → („Anna“, „von Berg“), „Berg, Anna“ → („Anna“, „Berg“); ein einzelnes Wort ist der Nachname."""
    text = _clean(name)
    if text.count(',') == 1:
        last, first = (_clean(x) for x in text.split(','))
        if last and first:
            return first, last
    words = text.split(' ')
    if len(words) < 2:
        return '', words[0] if words else ''
    i = len(words) - 1
    while i > 1 and words[i - 1].casefold() in PARTICLES:
        i -= 1
    return ' '.join(words[:i]), ' '.join(words[i:])


ZIP_CITY = re.compile(r'^(?:D-?\s*)?(\d{4,5})\s+(.+)$')
DOMESTIC = {'deutschland', 'germany', 'de', 'd', 'deu', 'ger', 'brd', 'bundesrepublikdeutschland', 'bundesrepublik'}
STATES = {'badenwurttemberg', 'bayern', 'berlin', 'brandenburg', 'bremen', 'hamburg', 'hessen', 'mecklenburgvorpommern', 'niedersachsen',
          'nordrheinwestfalen', 'rheinlandpfalz', 'saarland', 'sachsen', 'sachsenanhalt', 'schleswigholstein', 'thuringen'}


def country(value):
    """Land für die Anschrift: leer bei Deutschland (auch DEU, GER …) und bei einem Bundesland (Spalte „Land“ = Bundesland)."""
    text = _clean(value)
    return None if not text or _fold(text) in DOMESTIC | STATES else text


def split_address(address):
    """Mehrzeilige Anschrift → (Zusatz, Straße, PLZ, Ort, Land); Zeilen nach „PLZ Ort“ (z. B. Österreich) sind das Land.
    Ohne erkennbare PLZ bleibt alles in Straße."""
    lines = [_clean(line) for line in str(address or '').replace(',', '\n').splitlines() if _clean(line)]
    for i in range(len(lines) - 1, -1, -1):
        m = ZIP_CITY.match(lines[i])
        if m and i >= 1:
            before = lines[:i]
            return (' '.join(before[:-1]) or None), before[-1], m.group(1), m.group(2), country(' '.join(lines[i + 1:]))
    return None, ' '.join(lines), None, None, None


def compose(v):
    """name und address aus den Einzelfeldern (für Rechnungen, PDF, E-Mail); Namenszusatz (z. B. „von“) vor dem Nachnamen."""
    prefix = v.get('name_prefix')
    if prefix and _clean(v['last_name']).casefold().startswith(_clean(prefix).casefold() + ' '):   # Nachname enthält den Zusatz schon („von Berg“)
        prefix = None
    name = ' '.join(x for x in (v['first_name'], prefix, v['last_name']) if x).strip()
    address = '\n'.join(x for x in (v.get('address_extra'), v.get('address_extra2'), v['street'], f"{v['zip']} {v['city']}".strip(),
                                    v.get('country')) if x)
    return name, address


def _date(value, label, required=True):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _clean(value)
    if not text:
        if required:
            raise ValueError(f'{label} fehlt.')
        return None
    try:
        return iso(text.split(' ')[0])
    except ValueError:
        raise ValueError(f'{label}: Datum nicht erkannt („{text[:20]}“).') from None


def left_day(value, joined):
    """Austrittsdatum prüfen (auch künftig erlaubt, nie vor dem Eintritt) – eine Prüfung für Formular, Import und „Austritt eintragen“."""
    left = _date(value, 'Austritt', required=False)
    if left and left < joined:
        raise ValueError('Austritt kann nicht vor dem Eintritt liegen.')
    return left


def austritt(store, member_id, left_on, reason, actor):
    """Austritt eintragen bzw. ändern (left_on = Datum, Grund optional) oder zurücknehmen (left_on leer) – ab 0.37.
    Ändert nur Austritt und Grund; der Status (wahlrecht.py) ergibt sich daraus. Das Protokoll nennt die Daten, nicht den Grund."""
    if type(member_id) is not int:                                  # kein true, kein 1.9 – nur die Nummer aus der Mitgliederliste
        raise ValueError('Bitte ein Mitglied angeben.')
    if reason is not None and not isinstance(reason, str):
        raise ValueError('Austrittsgrund: bitte als Text angeben.')
    m = store.db.execute('SELECT id,number,joined,left_on,left_reason FROM members WHERE id=?', (member_id,)).fetchone()
    if not m:
        raise ValueError('Mitglied nicht gefunden.')
    left = left_day(left_on, m['joined'])
    # Obergrenze nur hier (Tippfehler wie „2207“ fallen so auf) – Formular und Import nehmen weiter auch ferne Daten aus dem Bestand an
    if left and left > f'{date.today().year + 2}-12-31':
        raise ValueError(f'Austritt {left} liegt zu weit in der Zukunft (höchstens Ende {date.today().year + 2}) – Datum prüfen.')
    reason = (_clean(reason) or None) if left else None
    if reason and len(reason) > MAX_TEXT:
        raise ValueError(f'Austrittsgrund: höchstens {MAX_TEXT} Zeichen.')
    if not left and not m['left_on']:
        raise ValueError('Für dieses Mitglied ist kein Austritt eingetragen.')
    if (left, reason) == (m['left_on'], m['left_reason']):
        return m['id']                                              # nichts geändert: kein Protokolleintrag
    with store.db:
        store.db.execute('UPDATE members SET left_on=?,left_reason=? WHERE id=?', (left, reason, m['id']))
        store.audit(actor, 'Austritt eingetragen' if left else 'Austritt zurückgenommen',
                    {'id': m['id'], 'number': m['number'], 'vorher': {'austritt': m['left_on'], 'grund': bool(m['left_reason'])},
                     'nachher': {'austritt': left, 'grund': bool(reason)}})
    notizen_bereinigen(store)   # Austritt heute oder früher: die Notiz entfällt sofort
    return m['id']


MAX_NOTE = 1000
VERLAUF = ('Mitglied angelegt', 'Mitglied geändert', 'Austritt eingetragen', 'Austritt zurückgenommen', 'Notiz zum Mitglied geändert',
           'Mitglieder zusammengeführt')


def notiz(store, member_id, text, actor):
    """Notiz der Mitgliederverwaltung zum Mitglied speichern (ab 0.43; leer = löschen) – im Protokoll ohne den Text."""
    if not isinstance(member_id, int) or isinstance(member_id, bool) or not isinstance(text, str):
        raise ValueError('Ungültige Angaben.')
    text = text.replace('\r\n', '\n').strip() or None
    if text and len(text) > MAX_NOTE:
        raise ValueError(f'Notiz: höchstens {MAX_NOTE} Zeichen.')
    m = store.db.execute('SELECT id,number,note,left_on FROM members WHERE id=?', (member_id,)).fetchone()
    if not m:
        raise ValueError('Mitglied nicht gefunden.')
    if text and m['left_on'] and m['left_on'] <= date.today().isoformat():
        raise ValueError('Für ausgetretene Mitglieder werden keine Notizen gespeichert (Datenschutz).')
    if text == m['note']:
        return
    with store.db:
        store.db.execute('UPDATE members SET note=? WHERE id=?', (text, member_id))
        store.audit(actor, 'Notiz zum Mitglied geändert', {'id': member_id, 'number': m['number'], 'leer': not text})


def notizen_bereinigen(store, today=None):
    """Notizen ausgetretener Mitglieder löschen (ab 0.47, DSGVO: der Zweck endet mit der Mitgliedschaft, keine Aufbewahrungspflicht)
    – am Tag des Austritts; läuft stündlich und nach „Austritt eintragen“. → Anzahl gelöschter Notizen."""
    day = (today or date.today()).isoformat()
    with store.db:
        n = store.db.execute('UPDATE members SET note=NULL WHERE note IS NOT NULL AND left_on IS NOT NULL AND left_on<=?', (day,)).rowcount
        if n:
            store.audit('Programm', 'Notizen ausgetretener Mitglieder gelöscht', {'anzahl': n})
    return n


def verlauf(store, member_id, limit=50):
    """Änderungen an einem Mitglied aus dem Protokoll (neueste zuerst): Zeit, wer, was – nur Feldnamen und Daten, keine Werte."""
    rows = store.db.execute(f"SELECT time,actor,action,detail FROM audit WHERE action IN ({','.join('?' * len(VERLAUF))}) "
                            "AND json_extract(detail,'$.id')=? ORDER BY id DESC LIMIT ?", (*VERLAUF, member_id, limit)).fetchall()
    out = []
    for time, actor, action, raw in rows:
        detail = json.loads(raw or '{}')
        text = action
        if action == 'Mitglied geändert' and detail.get('fields'):
            text = 'Geändert: ' + ', '.join(LABELS.get(f, f) for f in detail['fields'])
        elif action == 'Austritt eingetragen' and (detail.get('nachher') or {}).get('austritt'):
            text = f"Austritt zum {german_date(detail['nachher']['austritt'])} eingetragen"
        elif action == 'Notiz zum Mitglied geändert':
            text = 'Notiz gelöscht' if detail.get('leer') else 'Notiz geändert'
        elif action == 'Mitglieder zusammengeführt':
            text = f"Zusammengeführt mit Nr. {(detail.get('entfaellt') or {}).get('number', '?')}"
            if detail.get('ergaenzt'):
                text += ' – ergänzt: ' + ', '.join(LABELS.get(f, 'Notiz' if f == 'note' else f) for f in detail['ergaenzt'])
        out.append({'time': time, 'actor': actor, 'text': text})
    return out


def angaben(store, m):
    """Alle Angaben eines Mitglieds als [(Bezeichnung, Wert)] in der Reihenfolge der Mitgliederverwaltung – leere weggelassen."""
    import gliederung
    from core import KVS
    ovs = {o['id']: gliederung.label(o) for o in gliederung.ovs(store)}
    out = []
    for key, label in LABELS.items():
        value = m.get(key)
        if key in ('kv_number', 'ov_number', 'ov_name') or value is None or value == '' or (key in ('dues_free', 'guest', 'no_mail') and not value):
            continue
        if key in ('birth', 'joined', 'left_on', 'kv_joined'):
            value = german_date(value)
        elif key in ('dues_free', 'guest', 'no_mail'):
            value = 'ja'
        elif key in ('dues', 'external_balance'):
            value = euro(value)
        elif key == 'kv':
            value = dict(KVS).get(value, value)
        elif key == 'ov_id':
            value = ovs.get(value)
            if not value:
                continue
        out.append((label, str(value)))
    return out


def record_dues(db, member_id, old, new, year=None):
    """Individuellen Jahresbeitrag ab einem Jahr festhalten (Tabelle dues_rates): ändert er sich – z. B. durch einen neuen Import der
    Mitgliederliste –, gilt der neue Wert ab dem laufenden Jahr; frühere Jahre behalten ihren Beitrag (Staffel bzw. bisheriger Wert).
    Ein vor 0.24 eingetragener Wert ohne Verlauf gilt weiter für alle früheren Jahre."""
    if old == new:
        return
    year = year or date.today().year
    now = datetime.now().isoformat(timespec='seconds')
    if old is not None and not db.execute('SELECT 1 FROM dues_rates WHERE member_id=?', (member_id,)).fetchone():
        db.execute('INSERT INTO dues_rates(member_id,valid_from,amount,created) VALUES(?,0,?,?)', (member_id, old, now))
    db.execute('INSERT INTO dues_rates(member_id,valid_from,amount,created) VALUES(?,?,?,?) ON CONFLICT(member_id,valid_from) '
               'DO UPDATE SET amount=excluded.amount,created=excluded.created', (member_id, year, new, now))


def flag(value, label):
    """Ja/Nein-Angabe (J/N, ja/nein, 1/0, x) → 1/0; leer = 0."""
    if isinstance(value, bool) or isinstance(value, int):
        return int(bool(value))
    text = _clean(value).casefold()
    if text in ('', 'n', 'nein', '0', 'no', 'false', 'f', '-'):
        return 0
    if text in ('j', 'ja', '1', 'y', 'yes', 'true', 'x', 'on'):
        return 1
    raise ValueError(f'{label}: „{text[:10]}“ nicht erkannt (J oder N).')


def values(data, kvs, ovs=None, inactive=None):
    """Geprüfte Stammdaten eines Mitglieds. Ältere Aufrufe mit name/address werden aufgeteilt.
    ovs: {id: Ortsverband} (gliederung.ov_map) – ein Ortsverband muss zum Kreisverband des Mitglieds gehören.
    inactive: erlaubte deaktivierte Ortsverbände (z. B. der bisherige des Mitglieds); None = alle (Import mit Hinweis)."""
    data = dict(data)
    if not _clean(data.get('last_name')) and _clean(data.get('name')):
        data['first_name'], data['last_name'] = split_name(data['name'])
    if not _clean(data.get('street')) and _clean(data.get('address')):
        data['address_extra'], data['street'], data['zip'], data['city'], data['country'] = split_address(data['address'])
        if not data['zip']:                      # ältere Freitext-Anschrift ohne PLZ: unverändert übernehmen
            data['zip'], data['city'] = '', ''
    v = {k: _clean(data.get(k)) for k in ('number', 'first_name', 'last_name', 'address_extra', 'street', 'zip', 'city', 'country')}
    if not v['number']:
        raise ValueError('Mitgliedsnummer fehlt.')
    for k, limit in (('number', 50), ('first_name', MAX_TEXT), ('last_name', MAX_TEXT), ('address_extra', MAX_TEXT), ('street', MAX_TEXT),
                     ('city', MAX_TEXT), ('country', 100)):
        if len(v[k]) > limit:
            raise ValueError(f'{LABELS[k]}: höchstens {limit} Zeichen.')
    if not v['last_name']:
        raise ValueError('Nachname fehlt.')
    if not v['street']:
        raise ValueError('Straße und Hausnummer fehlen.')
    if (v['zip'] or v['city'] or not _clean(data.get('address'))) and not (v['zip'] and v['city']):
        raise ValueError('PLZ und Ort sind erforderlich.')
    if v['zip'] and not re.fullmatch(r'[0-9A-Za-z -]{3,10}', v['zip']):
        raise ValueError('PLZ prüfen.')
    v['birth'] = _date(data.get('birth'), 'Geburtsdatum')
    v['joined'] = _date(data.get('joined'), 'Eintrittsdatum')
    latest = (date.today() + timedelta(days=366)).isoformat()      # zweistellige Jahre („01.01.30“ → 2030) fallen so auf
    if v['joined'] > latest:
        raise ValueError(f"Eintritt {v['joined']} liegt zu weit in der Zukunft – Datum prüfen.")
    if v['birth'] >= v['joined']:
        raise ValueError('Geburtsdatum muss vor dem Eintritt liegen.')
    v['left_on'] = left_day(data.get('left_on'), v['joined'])
    if data.get('kv') not in kvs or data['kv'] == 'LV':
        raise ValueError('Bitte einen Kreisverband auswählen.')
    v['kv'] = data['kv']
    dues = _clean(data.get('dues') if data.get('dues') is not None else '')
    v['dues'] = money(dues) if dues else None
    if v['dues'] is not None and v['dues'] < 0:
        raise ValueError('Jahresbeitrag darf nicht negativ sein.')
    v['email'] = email_address(data.get('email')) or None
    v['email_work'] = email_address(data.get('email_work')) or None
    ov = data.get('ov_id')
    if isinstance(ov, bool):
        raise ValueError('Ortsverband ungültig.')
    if ov in (None, '', 0, '0'):
        v['ov_id'] = None
    else:
        try:
            ov = int(ov)
        except (TypeError, ValueError):
            raise ValueError('Ortsverband ungültig.') from None
        found = (ovs or {}).get(ov)
        if not found:
            raise ValueError('Ortsverband nicht gefunden – bitte neu auswählen.')
        if found['kv'] != v['kv']:
            raise ValueError(f"Ortsverband „{found['name']}“ gehört nicht zum gewählten Kreisverband.")
        if not found['active'] and inactive is not None and ov not in inactive:
            raise ValueError(f"Ortsverband „{found['name']}“ ist deaktiviert – bitte einen aktiven wählen (Stammdaten → Gliederung).")
        v['ov_id'] = ov
    for k in TEXTS:
        v[k] = _clean(data.get(k)) or None
        if v[k] and len(v[k]) > MAX_TEXT:
            raise ValueError(f'{LABELS[k]}: höchstens {MAX_TEXT} Zeichen.')
    if not v['left_on']:
        v['left_reason'] = None                   # ohne Austritt kein Austrittsgrund (Feld „Austritt“ im Formular geleert)
    v['kv_joined'] = _date(data.get('kv_joined'), 'Eintritt Kreisverband', required=False)
    if v['kv_joined'] and v['kv_joined'] > latest:
        raise ValueError(f"Eintritt Kreisverband {v['kv_joined']} liegt zu weit in der Zukunft – Datum prüfen.")
    raw = data.get('external_balance')
    v['external_balance'] = raw if isinstance(raw, int) and not isinstance(raw, bool) else (money(_clean(raw)) if _clean(raw) else None)
    v['dues_free'], v['guest'] = flag(data.get('dues_free'), 'Beitragsfrei'), flag(data.get('guest'), 'Gastmitglied')
    v['no_mail'] = flag(data.get('no_mail'), 'Postausschluss')
    v['country'] = country(v['country']) or ''   # Inland bzw. Bundesland: kein Land in der Anschrift
    for k in ('address_extra', 'zip', 'city', 'country'):
        v[k] = v[k] or None
    v['name'], v['address'] = compose({**v, 'zip': v['zip'] or '', 'city': v['city'] or ''})
    if not v['zip'] and _clean(data.get('address')):
        v['address'] = str(data['address']).strip()          # alte Anschrift ohne PLZ bleibt wörtlich
    return v


# ---------------------------------------------------------------- Import

def _fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').casefold().replace('ß', 'ss'))
    return ''.join(c for c in text if c.isalnum() and not unicodedata.combining(c))


HEADERS = {
    'number': ('mitgliedsnummer', 'mitgliedsnr', 'mitgliednr', 'mitgliedernummer', 'mitgliedsnummerju', 'nr', 'nummer', 'mnr', 'mitgliedsid', 'id'),
    'first_name': ('vorname', 'vornamen', 'rufname'),
    'last_name': ('nachname', 'familienname', 'zuname'),
    'full_name': ('name', 'vollername', 'mitglied', 'vorundnachname', 'vornameundnachname'),
    'street': ('strasse', 'str', 'strassehausnummer', 'strasseundhausnummer', 'strassenr', 'anschrift', 'adresse', 'wohnanschrift'),
    'house': ('hausnummer', 'hausnr', 'hnr'),
    'address_extra': ('adresszusatz', 'zusatz', 'co', 'anschriftenzusatz', 'name2', 'adresszusatz1'),
    'address_extra2': ('name3', 'adresszusatz2'),
    'zip': ('plz', 'postleitzahl'),
    'city': ('ort', 'wohnort', 'stadt'),
    'country': ('land', 'staat', 'country'),
    'email': ('email', 'emailadresse', 'mail', 'emailprivat', 'emailadresseprivat'),
    'birth': ('geburtsdatum', 'gebdatum', 'geburtstag', 'geboren', 'gebam'),
    'joined': ('eintritt', 'eintrittsdatum', 'mitgliedseit', 'beitritt', 'beitrittsdatum', 'eintrittju', 'vereineintritt', 'eintrittverein'),
    'left_on': ('austritt', 'austrittsdatum'),
    'kv': ('kreisverband', 'kv', 'gliederung', 'verband', 'kvname', 'lvkvname', 'lvkv', 'kreisverbandname'),
    'kv_number': ('lvkvnummer', 'kvnummer', 'kvnr', 'lvkvnr', 'kreisverbandnummer'),
    'dues': ('jahresbeitrag', 'beitrag', 'beitragjahr', 'beitragprojahr'),
    # Export der CDU-Mitgliederverwaltung (z. B. finanzen_mitglieder_gesamt.csv)
    'birth_month': ('geburtsmonat',),
    'birth_year': ('geburtsjahr',),
    'zip_city': ('plzort', 'plzundort', 'postleitzahlort'),
    'salutation': ('anrede',),
    'letter_salutation': ('briefanrede1', 'briefanrede'),
    'letter_salutation2': ('briefanrede2',),
    'title': ('titel', 'akademischertitel'),
    'name_prefix': ('adelname', 'namenszusatz', 'adelstitel', 'vorsatzwort'),
    'name_suffix': ('zusatzname',),
    'gender': ('geschlecht',),
    'nationality': ('nationalitat', 'staatsangehorigkeit'),
    'ov_number': ('ortsverband', 'ovnummer', 'ovnr'),
    'ov_name': ('ortsbezeichnung', 'ortsverbandname', 'ovname'),
    'clubs': ('vereine', 'vereinigungen'),
    'club': ('verein',),
    'kv_joined': ('kveintritt', 'eintrittkv', 'eintrittkreisverband'),
    'phone': ('telefonprivat', 'telefon', 'tel'),
    'phone_work': ('telefondienstlich', 'telefongeschaftlich', 'teldienstlich'),
    'mobile': ('handyprivat', 'handy', 'mobil', 'mobiltelefon'),
    'mobile_work': ('handydienstlich', 'mobildienstlich', 'handygeschaftlich'),
    'email_work': ('emaildienstlich', 'emailgeschaftlich', 'emailadressedienstlich'),
    'payment_method': ('zahlerzahlungsart', 'zahlungsart'),
    'external_balance': ('kontostand',),
    'dues_free': ('beitragsfrei',),
    'guest': ('gastmitglied', 'gast'),
    'no_mail': ('postausschluss', 'keinepost'),
}
# „Jahre“ und „Jubiläumjahre“ der Mitgliederverwaltung werden nicht übernommen: sie ergeben sich aus Geburtsdatum und Eintritt.


def map_header(cells):
    """Spalte → Feld; None, wenn die Zeile keine Kopfzeile einer Mitgliederliste ist."""
    found = {}
    for i, cell in enumerate(cells):
        key = _fold(cell)
        for field, names in HEADERS.items():
            if key in names and field not in found:
                found[field] = i
                break
    if 'full_name' in found and 'first_name' in found and 'last_name' not in found:
        found['last_name'] = found.pop('full_name')            # „Name“ neben „Vorname“ ist der Nachname
    if 'birth' in found and 'birth_month' in found and 'birth_year' in found and _fold(cells[found['birth']]) == 'geburtstag':
        found['birth_day'] = found.pop('birth')                # Geburtstag/-monat/-jahr in drei Spalten (CDU-Mitgliederverwaltung)
    has_name = 'last_name' in found or 'full_name' in found
    return found if has_name and len(found) >= 3 else None


class PasswordNeeded(ValueError):
    """Kennwortgeschützte Datei: state = 'required' (Kennwort fehlt) oder 'wrong' (Kennwort falsch) – die Oberfläche fragt danach."""
    def __init__(self, message, state):
        super().__init__(message)
        self.state = state


OLE = b'\xd0\xcf\x11\xe0'
MAX_PASSWORD = 255   # Obergrenze von Excel


def unlock(raw, password=None):
    """Kennwortgeschützte Excel-Datei (Office-Verschlüsselung, „Kennwort zum Öffnen“) im Arbeitsspeicher entschlüsseln.
    Ungeschützte Dateien kommen unverändert zurück. Das Kennwort wird nirgends gespeichert, protokolliert oder gesichert."""
    if raw[:4] != OLE:
        return raw                                # .xlsx ohne Schutz (ZIP) oder CSV
    try:
        import msoffcrypto
        from msoffcrypto.exceptions import InvalidKeyError
    except ImportError:
        if 'EncryptionInfo'.encode('utf-16-le') in raw:
            raise ValueError('Die Datei ist mit einem Kennwort geschützt; zum Öffnen fehlt das Paket msoffcrypto-tool.') from None
        return raw
    try:
        office = msoffcrypto.OfficeFile(io.BytesIO(raw))
        encrypted = office.is_encrypted()
    except Exception:
        return raw                                # keine Office-Datei: die normale Prüfung meldet den Fehler
    if not encrypted:
        return raw
    if password is not None and not isinstance(password, str):
        raise ValueError('Ungültiges Kennwort.')
    if not password:
        raise PasswordNeeded('Die Datei ist mit einem Kennwort geschützt. Bitte das Kennwort eingeben.', 'required')
    if len(password) > MAX_PASSWORD:
        raise PasswordNeeded('Das Kennwort ist falsch.', 'wrong')
    out = io.BytesIO()
    try:
        import inspect
        if 'verify_password' in inspect.signature(office.load_key).parameters:
            office.load_key(password=password, verify_password=True)
        else:
            office.load_key(password=password)
        office.decrypt(out)
    except InvalidKeyError:
        raise PasswordNeeded('Das Kennwort ist falsch. Bitte erneut eingeben.', 'wrong') from None
    except Exception:
        raise ValueError('Die geschützte Datei konnte nicht entschlüsselt werden (Verschlüsselungsart nicht unterstützt?).') from None
    data = out.getvalue()
    if data[:2] != b'PK' and data[:4] != OLE:
        raise PasswordNeeded('Das Kennwort ist falsch. Bitte erneut eingeben.', 'wrong')
    return data


def read_table(raw, filename, mapper, password=None, missing='Kopfzeile nicht erkannt.'):
    """Erste Tabelle (Excel .xlsx/.xls, auch kennwortgeschützt, oder CSV) mit erkannter Kopfzeile:
    (Zeilennummer der Kopfzeile, Zuordnung Feld → Spalte, Datenzeilen mit Zeilennummer)."""
    raw = unlock(raw, password)
    def scan(rows):
        for index, row in enumerate(rows[:15]):
            mapping = mapper(row)
            if mapping:
                return index, mapping, [(index + 2 + n, r) for n, r in enumerate(rows[index + 1:]) if any(_clean(c) for c in r)]
        return None
    if raw[:2] == b'PK':
        import openpyxl
        try:
            book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:
            raise ValueError('Excel-Datei (.xlsx) konnte nicht gelesen werden.') from None
        for sheet in book.worksheets:
            found = scan([list(r) for r in sheet.iter_rows(values_only=True, max_row=MAX_ROWS + 20)])
            if found:
                return found
    elif raw[:4] == OLE:
        import xlrd
        try:
            book = xlrd.open_workbook(file_contents=raw)
        except Exception:
            raise ValueError('Excel-Datei (.xls) konnte nicht gelesen werden.') from None
        for sheet in book.sheets():
            rows = []
            for r in range(min(sheet.nrows, MAX_ROWS + 20)):
                row = []
                for c in range(sheet.ncols):
                    cell = sheet.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        row.append(int(cell.value) if float(cell.value).is_integer() else cell.value)
                    else:
                        row.append(str(cell.value))
                rows.append(row)
            found = scan(rows)
            if found:
                return found
    else:
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252')
        lines = text.splitlines()
        sample = '\n'.join(lines[:15])
        delimiter = max((';', '\t', ','), key=sample.count)
        try:   # newline='': csv erkennt CR (CSV für Macintosh), LF und CRLF, auch in Feldern mit Zeilenumbruch
            found = scan(list(csv.reader(io.StringIO(text, newline=''), delimiter=delimiter)))
        except csv.Error:
            raise ValueError('Die CSV-Datei ist nicht lesbar (Trennzeichen, Anführungszeichen oder Zeilenenden prüfen).') from None
        if found:
            return found
    raise ValueError(missing)


def _table(raw, filename, password=None):
    return read_table(raw, filename, map_header, password,
                      'Kopfzeile nicht erkannt. Benötigt werden z. B. Mitgliedsnummer, Vorname, Nachname, Straße, PLZ, Ort, '
                      'Geburtsdatum, Eintritt und Kreisverband.')


KV_WORDS = [('5100', ('altona', 'elbvororte')), ('5200', ('bergedorf',)), ('5300', ('eimsbuttel', 'eimsbuettel')), ('5400', ('harburg',)),
            ('5500', ('mitte',)), ('5700', ('wandsbek',)), ('5800', ('schulerunion', 'schuelerunion')), ('5600', ('nord',))]


def kv_code(value, kvs):
    """Kreisverband aus Kennziffer oder Namen („KV Hamburg-Nord“, „Altona/Elbvororte“, „5300“)."""
    text = _fold(value)
    if not text:
        return None
    digits = re.sub(r'\D', '', str(value))
    if digits in kvs:
        return digits
    for code, words in KV_WORDS:
        if any(w in text for w in words):
            return code
    return None


def _cell(row, mapping, field):
    i = mapping.get(field)
    return row[i] if i is not None and i < len(row) else None


def _text(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _clean(value)


def _ov_from_row(row, mapping, kv, ov_list):
    """Ortsverband einer Importzeile: (id oder None, neuer Ortsverband oder None, Hinweise). Fehler als ValueError."""
    import gliederung
    num_text = _text(_cell(row, mapping, 'ov_number')) if 'ov_number' in mapping else ''
    name = gliederung.clean_name(_text(_cell(row, mapping, 'ov_name'))) if 'ov_name' in mapping else ''
    if num_text and (not gliederung.digits(num_text) or int(num_text) > gliederung.MAX_NUMBER):
        raise ValueError(f'Nummer des Ortsverbands nicht erkannt („{num_text[:10]}“).')
    number = int(num_text or 0)
    if not number and not name:
        return None, None, []                     # leer bzw. 0 ohne Bezeichnung (CSV „0“ wie Excel-Zahl 0): kein Ortsverband
    found, note = gliederung.match(ov_list, kv, number, name)
    if found:
        notes = [note] if note else []
        if not found['active']:
            notes.append(f"Ortsverband „{gliederung.label(found)}“ ist deaktiviert.")
        return found['id'], None, notes
    if not name:
        raise ValueError(f'Ortsverband {number} ist in den Stammdaten unbekannt und hat in der Datei keinen Namen.')
    if len(name) > gliederung.MAX_NAME:
        raise ValueError(f'Name des Ortsverbands: höchstens {gliederung.MAX_NAME} Zeichen.')
    return None, {'kv': kv, 'number': number, 'name': name}, []


def preview(store, raw, filename, password=None):
    """Vorschau eines Imports: je Zeile neu / update / same / error, nichts wird gespeichert."""
    from core import KVS
    import gliederung
    kvs = {k for k, _ in KVS}
    _, mapping, rows = _table(raw, filename, password)
    if len(rows) > MAX_ROWS:
        raise ValueError(f'Höchstens {MAX_ROWS} Mitglieder je Datei.')
    existing = {m['number']: m for m in store.rows('SELECT * FROM members')}
    people = {(_fold(m['first_name']), _fold(m['last_name']), m['birth']): m['number'] for m in existing.values()}
    stripped = {}
    for number, m in existing.items():
        stripped.setdefault(str(number).lstrip('0'), m)
    numbers = gliederung.kv_by_number(store)
    ov_list = gliederung.rows(store.db)
    ov_by_id = {o['id']: o for o in ov_list}
    has_ov = 'ov_number' in mapping or 'ov_name' in mapping
    has_kv = 'kv' in mapping or 'kv_number' in mapping
    new_ovs = {}
    seen, out = set(), []
    for row_no, row in rows:
        data = {}
        warnings = []                                              # alle Hinweise sammeln (keiner überschreibt einen anderen)
        for field in FIELDS:
            if field in mapping and field not in ('birth', 'joined', 'left_on', 'kv', 'kv_joined', 'ov_id'):
                data[field] = _text(_cell(row, mapping, field))
        for field in ('birth', 'joined', 'left_on', 'kv_joined'):
            if field in mapping:
                data[field] = _cell(row, mapping, field)
        if 'birth_day' in mapping:
            parts = [_text(_cell(row, mapping, f)) for f in ('birth_day', 'birth_month', 'birth_year')]
            data['birth'] = '.'.join(x.zfill(2) for x in parts[:2]) + '.' + parts[2] if all(parts) else ''
        if 'zip_city' in mapping:
            zc = ZIP_CITY.match(_text(_cell(row, mapping, 'zip_city')))
            if 'city' not in mapping:
                data['city'] = zc.group(2) if zc else _text(_cell(row, mapping, 'zip_city'))
            if not data.get('zip') and zc:
                data['zip'] = zc.group(1)
        if 'full_name' in mapping and 'last_name' not in mapping:
            data['first_name'], data['last_name'] = split_name(_text(_cell(row, mapping, 'full_name')))
        if 'house' in mapping and data.get('street') is not None:
            data['street'] = _clean(f"{data['street']} {_text(_cell(row, mapping, 'house'))}")
        kv_raw = _text(_cell(row, mapping, 'kv')) if 'kv' in mapping else ''
        kv_num = _text(_cell(row, mapping, 'kv_number')) if 'kv_number' in mapping else ''
        kv_error = None
        try:
            data['kv'], kv_note = gliederung.resolve_kv(numbers, kvs, kv_num, kv_raw)
        except ValueError as e:                                    # Nummer und Name widersprechen sich
            data['kv'], kv_note, kv_error = None, '', str(e)
        if kv_note:
            warnings.append(kv_note)
        item = {'row_no': row_no, 'action': 'error', 'error': None, 'warning': '', 'changes': [], 'new_ov': None}
        club = data.get('club')
        if club and _fold(club) not in ('ju', 'jungeunion', 'jungeunionhamburg'):
            warnings.append(f'Verein laut Datei „{club[:20]}“ – nicht Junge Union, bitte prüfen.')
        old = existing.get(data.get('number'))
        similar = stripped.get(str(data.get('number') or '').lstrip('0')) if not old and data.get('number') else None
        if similar and _fold(similar['last_name']) == _fold(data.get('last_name') or split_name(data.get('name') or '')[1]):
            old = similar                                         # Excel hat führende Nullen weggelassen (0012345 → 12345)
            data['number'] = old['number']
            warnings.append(f"Mitgliedsnummer {old['number']} ohne führende Nullen in der Datei – bitte prüfen.")
        elif similar:
            warnings.append(f"Ähnliche Mitgliedsnummer {similar['number']} gehört zu einem anderen Mitglied – bitte prüfen.")
        zip_cell = _cell(row, mapping, 'zip')
        land = data.get('country') if 'country' in mapping else (old['country'] if old else None)
        if isinstance(zip_cell, (int, float)) and not isinstance(zip_cell, bool) and data.get('zip', '').isdigit() \
                and len(data['zip']) < 5 and country(land) is None:
            data['zip'] = data['zip'].zfill(5)                    # Excel-Zahl ohne führende Null (01067 → 1067)
        try:
            merged = {**({k: old[k] for k in FIELDS} if old else {}), **data}   # vorhandene Spalten gelten, auch leer
            if kv_error:
                raise ValueError(kv_error)
            if not has_kv and old:
                merged['kv'] = old['kv']
            elif not data['kv']:
                shown = ' '.join(x for x in (kv_num, kv_raw) if x)
                raise ValueError(f'Kreisverband nicht erkannt („{shown[:30]}“).' if shown else 'Kreisverband fehlt.')
            if has_ov:
                merged['ov_id'], item['new_ov'], notes = _ov_from_row(row, mapping, merged['kv'], ov_list)
                warnings += notes
            elif merged.get('ov_id') and (ov_by_id.get(merged['ov_id']) or {}).get('kv') != merged['kv']:
                merged['ov_id'] = None
                warnings.append('Ortsverband entfällt: Kreisverband laut Datei ist ein anderer.')
            if merged.get('email_work'):
                try:
                    email_address(merged['email_work'])
                except ValueError:                                 # Freitext der Mitgliederverwaltung: nicht die ganze Zeile sperren
                    warnings.append(f"Dienstliche E-Mail „{str(merged['email_work'])[:40]}“ ungültig – wird nicht übernommen.")
                    merged['email_work'] = None
            for key in ('dues', 'external_balance'):
                if merged.get(key) is not None and not isinstance(merged[key], str):
                    merged[key] = f"{merged[key] / 100:.2f}".replace('.', ',')
            v = values(merged, kvs, ov_by_id)
            if v['number'] in seen:
                raise ValueError('Mitgliedsnummer kommt in der Datei doppelt vor.')
            seen.add(v['number'])
            item['values'] = v
            if old:
                item['changes'] = [LABELS[k] for k in FIELDS if (old[k] or None) != (v[k] or None)]
                if item['new_ov'] and LABELS['ov_id'] not in item['changes']:
                    item['changes'].append(LABELS['ov_id'])
                item['action'] = 'update' if item['changes'] else 'same'
                item['member_id'] = old['id']
                cleared = [LABELS[k] for k in FIELDS if old[k] not in (None, '') and v[k] in (None, '') and not (k == 'ov_id' and item['new_ov'])]
                if cleared:
                    warnings.append('Leere Zelle löscht: ' + ', '.join(cleared) + ' – bitte prüfen.')
            else:
                item['action'] = 'new'
                twin = people.get((_fold(v['first_name']), _fold(v['last_name']), v['birth']))
                if twin:
                    warnings.append(f'Gleiche Person mit anderer Mitgliedsnummer vorhanden ({twin}).')
            if item['new_ov']:
                o = item['new_ov']
                g = new_ovs.setdefault((o['kv'], o['number'], gliederung.fold(o['name']) if not o['number'] else ''), {**o, 'rows': 0})
                g['rows'] += 1
        except (ValueError, TypeError) as e:
            item['error'] = str(e)
            item['new_ov'] = None
        item['warning'] = ' '.join(warnings)
        item['name'] = (item.get('values') or {}).get('name') or _clean(f"{data.get('first_name') or ''} {data.get('last_name') or ''}")
        item['number'] = (item.get('values') or {}).get('number') or data.get('number') or ''
        item['kv'] = (item.get('values') or {}).get('kv') or data.get('kv')
        ov_id = (item.get('values') or {}).get('ov_id')
        item['ov'] = gliederung.label(item['new_ov']) + ' (neu)' if item['new_ov'] else gliederung.label(ov_by_id[ov_id]) if ov_id in ov_by_id else ''
        out.append(item)
    counts = {a: sum(1 for r in out if r['action'] == a) for a in ('new', 'update', 'same', 'error')}
    in_file = {str(r['number']).lstrip('0') for r in out if r['number']}
    missing = [{'number': m['number'], 'name': m['name'], 'kv': m['kv']} for m in existing.values()     # Austritt prüfen
               if str(m['number']).lstrip('0') not in in_file and not m['left_on']]
    extra = [label for f, label in (('birth_day', 'Geburtsdatum'), ('zip_city', 'PLZ/Ort')) if f in mapping]
    return {'filename': filename, 'columns': [LABELS.get(f, f) for f in mapping if f in LABELS] + (['Name'] if 'full_name' in mapping else []) + extra,
            'rows': out, 'counts': counts, 'guests': sum(1 for r in out if (r.get('values') or {}).get('guest')),
            'missing': sorted(missing, key=lambda m: (m['kv'], m['name'])),
            'new_ovs': sorted(new_ovs.values(), key=lambda o: (o['kv'], o['number'] == 0, o['number'], gliederung.fold(o['name'])))}


def apply(store, prev, selected, actor):
    """Ausgewählte Zeilen der Vorschau übernehmen (neu anlegen bzw. aktualisieren) – alles oder nichts."""
    chosen = {int(n) for n in selected}
    rows = [r for r in prev['rows'] if r['row_no'] in chosen]
    if not rows:
        raise ValueError('Keine Zeilen ausgewählt.')
    if any(r['action'] not in ('new', 'update') for r in rows):
        raise ValueError('Nur neue oder geänderte Zeilen ohne Fehler können übernommen werden.')
    import gliederung
    cols = STORED
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        new = updated = created = 0
        for r in rows:
            v = dict(r['values'])
            if r.get('new_ov'):                                   # unbekannter Ortsverband aus der Datei: jetzt anlegen
                o = r['new_ov']
                v['ov_id'], added = gliederung.ensure(store.db, o['kv'], o['number'], o['name'])
                created += added
            elif v.get('ov_id') and not store.db.execute('SELECT 1 FROM ortsverbaende WHERE id=? AND kv=?', (v['ov_id'], v['kv'])).fetchone():
                raise ValueError('Ein Ortsverband wurde inzwischen geändert oder gelöscht – Datei bitte erneut einlesen.')
            if r['action'] == 'new':
                if store.db.execute('SELECT 1 FROM members WHERE number=?', (v['number'],)).fetchone():
                    raise ValueError(f"Mitgliedsnummer {v['number']} wurde inzwischen angelegt – Datei bitte erneut einlesen.")
                mid = insert(store.db, v)
                record_dues(store.db, mid, None, v['dues'])
                new += 1
            else:
                before = store.db.execute('SELECT dues FROM members WHERE id=?', (r['member_id'],)).fetchone()
                if not store.db.execute(f"UPDATE members SET {','.join(k + '=?' for k in cols)} WHERE id=? AND number=?",
                                        [v[k] for k in cols] + [r['member_id'], v['number']]).rowcount:
                    raise ValueError(f"Mitglied {v['number']} wurde inzwischen gelöscht oder geändert – Datei bitte erneut einlesen.")
                if before:
                    record_dues(store.db, r['member_id'], before[0], v['dues'])
                updated += 1
        store.audit(actor, 'Mitgliederliste importiert', {'datei': prev['filename'], 'neu': new, 'aktualisiert': updated, 'ortsverbaende_neu': created})
    notizen_bereinigen(store)   # mit der Datei eingetragene Austritte
    return {'new': new, 'updated': updated, 'ov_created': created}


# ---------------------------------------------------------------- Löschen

# Was ein Mitglied an Buchhaltung bindet (Aufbewahrungspflicht): dann nur Austritt eintragen, nicht löschen.
BLOCKERS = (('invoices', 'member_id', ('Rechnung', 'Rechnungen')), ('dues_assignments', 'member_id', ('zugeordnete Beitragszahlung', 'zugeordnete Beitragszahlungen')),
            ('mandates', 'member_id', ('SEPA-Mandat', 'SEPA-Mandate')), ('refunds', 'member_id', ('Auslagenerstattung', 'Auslagenerstattungen')))
OWNED = ('dues_rates', 'dues_targets', 'sepa_mandates')   # gehören nur zum Mitglied (Beitragsverlauf, Soll-Festlegungen, gemerkte Mandatsreferenzen) und gehen mit


def delete_blockers(db, member_id):
    """Gründe gegen das Löschen: [„3 Rechnungen“, …]; leer = löschbar."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = []
    for table, column, label in BLOCKERS:
        if table in tables:
            n = db.execute(f'SELECT COUNT(*) FROM {table} WHERE {column}=?', (member_id,)).fetchone()[0]
            if n:
                out.append(f'{n} {label[0] if n == 1 else label[1]}')
    return out


def check_delete(store, member_id, arrears_ok=False):
    """Löschbar? Sperrt bei Buchhaltungsbezug; ein offener Rückstand (Soll nach Staffel ohne Zahlung) verlangt eine ausdrückliche Bestätigung."""
    m = store.db.execute('SELECT id,number FROM members WHERE id=?', (member_id,)).fetchone()
    if not m:
        raise ValueError('Mitglied nicht gefunden.')
    blockers = delete_blockers(store.db, member_id)
    if blockers:
        raise ValueError('Das Mitglied hat ' + ', '.join(blockers) + ' und kann deshalb nicht gelöscht werden (Aufbewahrung der '
                         'Buchhaltung). Stattdessen den Austritt eintragen.')
    if arrears_ok is not True:
        import mitgliederkonto
        from formate import euro
        arrears = mitgliederkonto.account(store, member_id)['arrears']
        if arrears:
            raise ValueError(f'Offener Beitragsrückstand von {euro(arrears)} – er entfällt mit dem Löschen. Bitte ausdrücklich bestätigen '
                             'oder stattdessen den Austritt eintragen.')
    return m


def delete(store, member_id, actor, arrears_ok=False):
    """Mitglied ohne Buchhaltungsbezug endgültig löschen (vorher sichert der Aufrufer die Datenbank). Prüft in der Transaktion erneut."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        m = check_delete(store, member_id, arrears_ok)
        tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in OWNED:
            if table in tables:
                store.db.execute(f'DELETE FROM {table} WHERE member_id=?', (member_id,))
        store.db.execute('DELETE FROM members WHERE id=?', (member_id,))
        store.db.execute('INSERT OR IGNORE INTO deleted_members(id,deleted) VALUES(?,?)', (member_id, datetime.now().isoformat(timespec='seconds')))
        store.audit(actor, 'Mitglied gelöscht', {'id': member_id, 'number': m['number'], 'rueckstand_bestaetigt': arrears_ok is True})
