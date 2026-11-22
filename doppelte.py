"""Doppelte Mitglieder finden (ab 0.45, Wunsch Lennert Gust 21.11.2026: „eine Funktion, um doppelte Mitglieder zu finden“).

Zwei Mitglieder gelten als möglicherweise doppelt bei
  - gleichem Namen und gleichem Geburtsdatum,
  - gleichem Namen und gleicher Anschrift (Straße mit Hausnummer und PLZ),
  - gleicher E-Mail-Adresse und gleichem Geburtsdatum,
  - gleichem Nachnamen und Geburtsdatum, wenn ein Vorname den anderen abkürzt („Max“ / „Maximilian“) oder die Vornamen des einen
    alle im anderen stehen („Anna Maria“ / „Maria“).
Namen vergleichen ohne Groß-/Kleinschreibung, Akzente und Satzzeichen, „Müller“ = „Mueller“ = „Muller“, Reihenfolge egal
(„Berg Anna“ = „Anna Berg“); Straßen wie die Adressprüfung („Musterstr.“ = „Musterstraße“). Paare, die jemand als „kein Duplikat“
bestätigt hat, fallen weg (`mitglieder_keine_doppelten`). Zwillinge fallen nur auf, wenn ihre Vornamen sich so ähneln oder sie
dieselbe E-Mail-Adresse haben – dann „kein Duplikat“.
"""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import combinations

GRUENDE = {'name_geburt': 'gleicher Name und Geburtsdatum', 'name_anschrift': 'gleicher Name und Anschrift',
           'mail_geburt': 'gleiche E-Mail-Adresse und Geburtsdatum', 'vorname': 'ähnlicher Vorname, gleicher Nachname und Geburtsdatum'}
MAX_GRUPPE = 50


def init(db):
    db.executescript('CREATE TABLE IF NOT EXISTS mitglieder_keine_doppelten(a INTEGER NOT NULL,b INTEGER NOT NULL,actor TEXT NOT NULL,'
                     'time TEXT NOT NULL,PRIMARY KEY(a,b),CHECK(a<b));')


def _fold(text):
    import gliederung
    return re.sub(r'([aou])e', r'\1', gliederung.fold(text))   # ü → u (fold), ue → u: „Müller“ = „Mueller“ = „Muller“


def _woerter(text):
    return tuple(sorted(w for w in map(_fold, re.split(r'[\s\-]+', str(text or ''))) if w))


def _anschrift(m):
    import adressen
    name, nr, rest = adressen.split_street(m['street'])
    return (adressen.norm_street(name), nr, _fold(rest), (m['zip'] or '').strip()) if name and nr and m['zip'] else None


def _vornamen_passen(fa, fb):
    """Vornamen (Wortmengen) verschieden, aber einer steckt im anderen („Anna Maria“/„Maria“) oder ein Wort kürzt eins des anderen ab
    („Max“/„Maximilian“, mindestens drei Buchstaben)."""
    return fa != fb and (fa < fb or fb < fa or any(x != y and min(len(x), len(y)) >= 3 and (x.startswith(y) or y.startswith(x))
                                                   for x in fa for y in fb))


def paare(members):
    """{(a, b): {Grund, …}} für alle Paare möglicher Doppelter (a < b); members: Zeilen mit id, name, first_name, last_name, birth,
    street, zip, email."""
    keys, nachnamen = defaultdict(list), defaultdict(list)
    for m in members:
        name, birth, mail = _woerter(m['name']), m['birth'], (m['email'] or '').strip().casefold()
        if name and birth:
            keys[('name_geburt', name, birth)].append(m['id'])
        if name and _anschrift(m):
            keys[('name_anschrift', name, _anschrift(m))].append(m['id'])
        if mail and birth:
            keys[('mail_geburt', mail, birth)].append(m['id'])
        first, last = frozenset(_woerter(m['first_name'])), _woerter(m['last_name'])
        if first and last and birth:
            nachnamen[(last, birth)].append((first, m['id']))
    out = defaultdict(set)
    for (grund, *_), ids in keys.items():
        for pair in combinations(sorted(ids), 2):
            out[pair].add(grund)
    for leute in nachnamen.values():
        for (fa, a), (fb, b) in combinations(leute, 2):
            if _vornamen_passen(fa, fb):
                out[tuple(sorted((a, b)))].add('vorname')
    return out


def gruppen(store):
    """Mögliche Doppelte als Gruppen: [{'ids': [...], 'gruende': [Text, …]}] – Paare mit „kein Duplikat“ zählen nicht."""
    members = store.rows('SELECT id,name,first_name,last_name,birth,street,zip,email FROM members')
    nein = {tuple(r) for r in store.db.execute('SELECT a,b FROM mitglieder_keine_doppelten')}
    found = {p: g for p, g in paare(members).items() if p not in nein}
    parent = {}

    def root(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for a, b in found:
        parent[root(b)] = root(a)
    groups = defaultdict(lambda: {'ids': set(), 'gruende': set()})
    for (a, b), gruende in found.items():
        g = groups[root(a)]
        g['ids'] |= {a, b}
        g['gruende'] |= gruende
    names = {m['id']: _fold(m['last_name'] or m['name']) for m in members}
    return sorted(({'ids': sorted(g['ids']), 'gruende': [GRUENDE[k] for k in GRUENDE if k in g['gruende']]} for g in groups.values()),
                  key=lambda g: (min(names[i] for i in g['ids']), g['ids']))


def kein_duplikat(store, ids, actor):
    """Mitglieder einer Gruppe als „kein Duplikat“ bestätigen (alle Paare untereinander); im Protokoll nur die Kennungen."""
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < 2 ** 62 for i in ids) or len(set(ids)) < 2:
        raise ValueError('Bitte mindestens zwei Mitglieder angeben.')
    if len(set(ids)) > MAX_GRUPPE:
        raise ValueError(f'Höchstens {MAX_GRUPPE} Mitglieder auf einmal.')
    ids = sorted(set(ids))
    if store.db.execute(f"SELECT COUNT(*) FROM members WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()[0] != len(ids):
        raise ValueError('Mitglied nicht gefunden.')
    now = datetime.now().isoformat(timespec='seconds')
    with store.db:
        store.db.executemany('INSERT OR IGNORE INTO mitglieder_keine_doppelten(a,b,actor,time) VALUES(?,?,?,?)',
                             [(a, b, actor, now) for a, b in combinations(ids, 2)])
        store.audit(actor, 'Kein Duplikat bestätigt', {'ids': ids})


# ---------------------------------------------------------------- Zusammenführen (ab 0.47)

# Angaben, die beim bleibenden Mitglied ergänzt werden, wenn sie dort fehlen (Anschrift ist Pflicht und bleibt immer die des bleibenden)
ERGAENZEN = ('salutation', 'title', 'name_prefix', 'name_suffix', 'letter_salutation', 'letter_salutation2', 'gender', 'nationality',
             'clubs', 'club', 'email', 'email_work', 'phone', 'phone_work', 'mobile', 'mobile_work', 'payment_method')
# bleiben beim bleibenden Mitglied – Unterschiede zeigen
VERGLEICH = ('number', 'first_name', 'last_name', 'birth', 'street', 'zip', 'city', 'kv', 'left_on', 'dues', 'dues_free', 'guest')
# alle Tabellen mit member_id außer dues_rates (Beitragsverlauf: der des bleibenden gilt); alle gehören zur Buchhaltung („Buchhaltung ändern“)
UMHAENGEN = (('invoices', 'Rechnungen'), ('dues_assignments', 'Beitragszahlungen'), ('mandates', 'SEPA-Mandate'), ('refunds', 'Auslagenerstattungen'),
             ('dues_targets', 'festgelegte Beiträge'), ('sepa_mandates', 'gemerkte Mandatsreferenzen'))
# offene Beitragsrechnung (ohne Pilot) bestimmt das Soll – daneben keine Festlegung desselben Jahres (wie mitgliederkonto.set_target)
OFFENE_BEITRAGSRECHNUNG = ("SELECT year FROM invoices WHERE kind='dues' AND member_id=? AND status='issued' "
                           "AND NOT COALESCE(json_extract(snapshot,'$.pilot'),0) INTERSECT SELECT year FROM dues_targets WHERE member_id=?")


def _id(value):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 2 ** 62:
        raise ValueError('Ungültiges Mitglied.')
    return value


def _zeigen(key, value):
    from core import KVS
    from formate import euro, german_date
    if value in (None, '') and key not in ('dues_free', 'guest'):
        return '—'
    if key in ('birth', 'joined', 'left_on', 'kv_joined'):
        return german_date(value)
    if key in ('dues_free', 'guest'):
        return 'ja' if value else 'nein'
    if key == 'kv':
        return dict(KVS).get(value, value)
    return euro(value) if key == 'dues' else str(value)


def _endete_vor(left_on, joined):
    """Endete die Mitgliedschaft (letzter Tag `left_on`) mit Lücke vor `joined`? Austritt am Vortag = lückenlos, keine Lücke."""
    if not left_on:
        return False
    try:
        return (date.fromisoformat(left_on) + timedelta(days=1)).isoformat() < joined
    except ValueError:
        return left_on < joined


def vorschau(store, keep, drop, buchhaltung=False):
    """Was beim Zusammenführen passiert: bleibendes und entfallendes Mitglied, ergänzte Angaben, Unterschiede, umgehängte Einträge,
    Hindernisse (gleiche Rechnung/Festlegung im selben Jahr, zwei aktive Mandate, Buchhaltung ohne Recht „Buchhaltung ändern“)."""
    import mitglieder
    keep, drop = _id(keep), _id(drop)
    if keep == drop:
        raise ValueError('Bitte zwei verschiedene Mitglieder wählen.')
    a, b = (store.db.execute('SELECT * FROM members WHERE id=?', (i,)).fetchone() for i in (keep, drop))
    if not a or not b:
        raise ValueError('Mitglied nicht gefunden.')
    a, b = dict(a), dict(b)
    neu = {k: b[k] for k in ERGAENZEN if not a.get(k) and b.get(k)}
    hinweise = []
    # Wiedereintritt: eine Mitgliedschaft endete, bevor die andere begann – dann keine Daten der alten übernehmen
    b_vorher, a_vorher = _endete_vor(b['left_on'], a['joined']), _endete_vor(a['left_on'], b['joined'])
    if b_vorher:
        hinweise.append(f"Eintritt ({_zeigen('joined', a['joined'])}), Ortsverband und Eintritt Kreisverband bleiben die des bleibenden "
                        f"Mitglieds: die frühere Mitgliedschaft ({_zeigen('joined', b['joined'])} bis {_zeigen('left_on', b['left_on'])}) endete vorher.")
    elif a_vorher:
        hinweise.append(f"Das bleibende Mitglied ist zum {_zeigen('left_on', a['left_on'])} ausgetreten, das entfallende trat "
                        f"{_zeigen('joined', b['joined'])} wieder ein – vermutlich ein Wiedereintritt: das neuere behalten oder „Kein Duplikat“.")
    else:
        if a['kv'] == b['kv']:                           # Ortsverband und Eintritt in den Kreisverband nur im selben Kreisverband
            neu.update({k: b[k] for k in ('ov_id', 'kv_joined') if not a.get(k) and b.get(k)})
        if b['joined'] < a['joined']:
            neu['joined'] = b['joined']                  # dieselbe Mitgliedschaft: der frühere Eintritt zählt
    if b['no_mail'] and not a['no_mail']:
        neu['no_mail'] = 1                               # Postausschluss bleibt bestehen
    if b.get('note') and a['left_on'] and a['left_on'] <= date.today().isoformat():
        hinweise.append('Die Notiz des entfallenden Mitglieds wird nicht übernommen – das bleibende ist ausgetreten (Datenschutz).')
    elif b.get('note'):
        neu['note'] = f"{a['note']}\n—\n{b['note']}" if a.get('note') else b['note']
    zahl = {table: store.db.execute(f'SELECT COUNT(*) FROM {table} WHERE member_id=?', (drop,)).fetchone()[0] for table, _ in UMHAENGEN}
    # UNIQUE(member_id, year, kind) – auch stornierte Rechnungen bleiben als Beleg bei ihrem Mitglied
    konflikte = [f'Beide haben eine {"Beitragsrechnung" if kind == "dues" else "Rechnung"} {year} (auch stornierte zählen: eine Rechnung bleibt '
                 'als Beleg bei ihrem Mitglied) – so lassen sie sich nicht zusammenführen.'
                 for year, kind in store.db.execute('SELECT year,kind FROM invoices WHERE member_id=? AND year IS NOT NULL INTERSECT '
                                                    'SELECT year,kind FROM invoices WHERE member_id=? AND year IS NOT NULL', (keep, drop))]
    konflikte += [f'Für beide ist der Beitrag {year} festgelegt – eine Festlegung entfernen.' for (year,) in store.db.execute(
        'SELECT year FROM dues_targets WHERE member_id=? INTERSECT SELECT year FROM dues_targets WHERE member_id=?', (keep, drop))]
    konflikte += [f'Für {year} hat eines eine offene Beitragsrechnung, das andere einen festgelegten Beitrag – erst die Festlegung entfernen '
                  '(oder die Rechnung stornieren – geht nur ohne zugeordnete Zahlung und außerhalb einer offenen Lastschriftdatei).' for (year,) in store.db.execute(
                      f'SELECT year FROM ({OFFENE_BEITRAGSRECHNUNG}) UNION SELECT year FROM ({OFFENE_BEITRAGSRECHNUNG})', (keep, drop, drop, keep))]
    if all(store.db.execute("SELECT 1 FROM mandates WHERE member_id=? AND status='active'", (i,)).fetchone() for i in (keep, drop)):
        konflikte.append('Beide haben ein aktives SEPA-Mandat – eins widerrufen.')
    if len(neu.get('note') or '') > mitglieder.MAX_NOTE:
        konflikte.append(f'Zusammen wären die Notizen länger als {mitglieder.MAX_NOTE} Zeichen – erst eine kürzen.')
    if not buchhaltung and any(zahl.values()):
        konflikte.append('Das entfallende Mitglied hat Rechnungen, Zahlungen, Mandate, Erstattungen oder Festlegungen – umhängen darf nur, '
                         'wer „Buchhaltung ändern“ hat.')
    person = lambda m: {'id': m['id'], 'number': m['number'], 'name': m['name']}
    bezeichnung = lambda k: f"Eintritt ({_zeigen(k, neu[k])})" if k == 'joined' else mitglieder.LABELS.get(k, 'Notiz' if k == 'note' else k)
    return {'bleibt': person(a), 'entfaellt': person(b), 'neu': neu, 'ergaenzt': [bezeichnung(k) for k in neu],
            'unterschiede': [[mitglieder.LABELS[k], _zeigen(k, a[k]), _zeigen(k, b[k])] for k in VERGLEICH
                             if k != 'number' and _zeigen(k, a[k]) != _zeigen(k, b[k])],
            'umhaengen': [[label, zahl[t]] for t, label in UMHAENGEN if zahl[t]], 'konflikte': konflikte, 'hinweise': hinweise}


def pruefen(store, keep, drop, buchhaltung=False):
    """Vorschau oder ValueError mit allen Hindernissen – vor der Sicherung und erneut in der Transaktion."""
    v = vorschau(store, keep, drop, buchhaltung)
    if v['konflikte']:
        raise ValueError(' '.join(v['konflikte']))
    return v


def zusammenfuehren(store, keep, drop, actor, buchhaltung=False):
    """Mitglied `drop` in `keep` aufgehen lassen (vorher sichert der Aufrufer die Datenbank): fehlende Angaben ergänzen, Rechnungen,
    Zahlungen, Mandate, Erstattungen, Festlegungen und gemerkte Mandatsreferenzen umhängen, `drop` löschen (Kennung bleibt gesperrt).
    Prüft in der Transaktion erneut; im Protokoll nur Kennungen, Nummern, Feldnamen und Anzahlen."""
    import mitglieder
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        v = pruefen(store, keep, drop, buchhaltung)
        neu = v['neu']
        if neu:
            a = {**dict(store.db.execute('SELECT * FROM members WHERE id=?', (keep,)).fetchone()), **neu}
            neu['name'], neu['address'] = mitglieder.compose(a)
            store.db.execute(f"UPDATE members SET {','.join(f'{k}=?' for k in neu)} WHERE id=?", (*neu.values(), keep))
        for table, _ in UMHAENGEN:
            store.db.execute(f'UPDATE {table} SET member_id=? WHERE member_id=?', (keep, drop))
        store.db.execute('DELETE FROM dues_rates WHERE member_id=?', (drop,))   # Beitragsverlauf: der des bleibenden Mitglieds gilt
        store.db.execute('DELETE FROM mitglieder_keine_doppelten WHERE a=? OR b=?', (drop, drop))
        store.db.execute('DELETE FROM members WHERE id=?', (drop,))
        store.db.execute('INSERT OR IGNORE INTO deleted_members(id,deleted) VALUES(?,?)', (drop, datetime.now().isoformat(timespec='seconds')))
        store.audit(actor, 'Mitglieder zusammengeführt', {'id': keep, 'number': v['bleibt']['number'],   # id: im Verlauf des bleibenden Mitglieds
                                                          'entfaellt': {'id': drop, 'number': v['entfaellt']['number']},
                                                          'ergaenzt': sorted(k for k in neu if k not in ('name', 'address')),
                                                          'umgehaengt': {label: n for label, n in v['umhaengen']}})
    return v
