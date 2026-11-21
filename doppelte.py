"""Doppelte Mitglieder finden (ab 0.45, Wunsch Lennert Gust 21.11.2026: „eine Funktion, um doppelte Mitglieder zu finden“).

Nur Hinweise, nichts wird zusammengeführt oder gelöscht. Zwei Mitglieder gelten als möglicherweise doppelt bei
  - gleichem Namen und gleichem Geburtsdatum,
  - gleichem Namen und gleicher Anschrift (Straße mit Hausnummer und PLZ),
  - gleicher E-Mail-Adresse und gleichem Geburtsdatum,
  - gleichem Nachnamen und Geburtsdatum, wenn ein Vorname den anderen abkürzt („Max“ / „Maximilian“).
Namen vergleichen ohne Groß-/Kleinschreibung, Akzente und Satzzeichen, „ü“ = „ue“, Reihenfolge egal („Berg Anna“ = „Anna Berg“);
Straßen wie die Adressprüfung („Musterstr.“ = „Musterstraße“). Paare, die jemand als „kein Duplikat“ bestätigt hat, fallen weg
(`mitglieder_keine_doppelten`). Zwillinge mit verschiedenen Vornamen fallen nicht auf.
"""
import re
from collections import defaultdict
from datetime import datetime
from itertools import combinations

GRUENDE = {'name_geburt': 'gleicher Name und Geburtsdatum', 'name_anschrift': 'gleicher Name und Anschrift',
           'mail_geburt': 'gleiche E-Mail-Adresse und Geburtsdatum', 'vorname': 'ähnlicher Vorname, gleicher Nachname und Geburtsdatum'}
MAX_GRUPPE = 50


def init(db):
    db.executescript('CREATE TABLE IF NOT EXISTS mitglieder_keine_doppelten(a INTEGER NOT NULL,b INTEGER NOT NULL,actor TEXT NOT NULL,'
                     'time TEXT NOT NULL,PRIMARY KEY(a,b),CHECK(a<b));')


def _fold(text):
    import gliederung
    text = str(text or '').casefold()
    for umlaut, ersatz in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue')):
        text = text.replace(umlaut, ersatz)
    return gliederung.fold(text)


def _woerter(text):
    return tuple(sorted(w for w in map(_fold, re.split(r'[\s\-]+', str(text or ''))) if w))


def _anschrift(m):
    import adressen
    name, nr, rest = adressen.split_street(m['street'])
    return (adressen.norm_street(name), nr, _fold(rest), (m['zip'] or '').strip()) if name and nr and m['zip'] else None


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
        first, last = ''.join(_woerter(m['first_name'])), _woerter(m['last_name'])
        if first and last and birth:
            nachnamen[(last, birth)].append((first, m['id']))
    out = defaultdict(set)
    for (grund, *_), ids in keys.items():
        for pair in combinations(sorted(ids), 2):
            out[pair].add(grund)
    for leute in nachnamen.values():
        for (fa, a), (fb, b) in combinations(leute, 2):
            if fa != fb and min(len(fa), len(fb)) >= 3 and (fa.startswith(fb) or fb.startswith(fa)):
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
    if not isinstance(ids, list) or not 2 <= len(set(ids)) <= MAX_GRUPPE or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ValueError('Bitte mindestens zwei Mitglieder angeben.')
    ids = sorted(set(ids))
    if store.db.execute(f"SELECT COUNT(*) FROM members WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()[0] != len(ids):
        raise ValueError('Mitglied nicht gefunden.')
    now = datetime.now().isoformat(timespec='seconds')
    with store.db:
        store.db.executemany('INSERT OR IGNORE INTO mitglieder_keine_doppelten(a,b,actor,time) VALUES(?,?,?,?)',
                             [(a, b, actor, now) for a, b in combinations(ids, 2)])
        store.audit(actor, 'Kein Duplikat bestätigt', {'ids': ids})
