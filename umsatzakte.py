"""Notiz und angeheftete Belege am Kontoumsatz (ab 0.58, REGELN Nr. 40, Wunsch Lennert Gust 23.11.2026).

Eigene Tabellen, damit `bank` bleibt, wie sie ist (`bank.note` trägt weiter den Grund für „ungültig“):
- `umsatz_notizen`: eine Notiz je Umsatz, mit wer und wann.
- `umsatz_belege`: beliebig viele Dateien je Umsatz. Die Datei liegt in Daten/Belege unter `attachment`
  (`Umsatz-<Umsatz>-<Beleg>.<Endung>`; die Belegnummer wird nie neu vergeben, weil die Sicherung vorhandene Dateien
  nicht überschreibt), `name` ist der Dateiname beim Anheften.
Geprüft wird die Datei mit `verbindlichkeiten.check_file`, gespeichert von `app.save_receipt` – wie alle Belege.
Nur Kontoumsätze (`ledger='bank'`); Kassenbelege tragen ihren Beleg in `bank.attachment` (kasse.py).
"""
from datetime import datetime
from pathlib import Path

import verbindlichkeiten

MAX_NOTIZ = 1000
MAX_BELEGE = 20   # je Umsatz
ARTEN = ('.pdf', '.png', '.jpg', '.jpeg')
# Ausgabe ohne Beleg: verbuchter Ausgang im Kontoauszug, an dem keine Datei hängt und der keiner Eingangsrechnung mit Datei
# zugeordnet ist (bei Teilbeträgen genügt eine). Offene, geparkte und ungültige Umsätze zählen nicht – sie sind noch nicht verbucht.
OHNE_BELEG = ("b.ledger='bank' AND b.state='booked' AND b.amount<0 "
              "AND NOT EXISTS(SELECT 1 FROM umsatz_belege u WHERE u.bank_id=b.id) "
              "AND NOT EXISTS(SELECT 1 FROM bill_payments p JOIN bills x ON x.id=p.bill_id WHERE p.bank_id=b.id AND x.attachment IS NOT NULL)")


def init(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS umsatz_notizen(bank_id INTEGER PRIMARY KEY REFERENCES bank(id),text TEXT NOT NULL,von TEXT,am TEXT);
        CREATE TABLE IF NOT EXISTS umsatz_belege(id INTEGER PRIMARY KEY AUTOINCREMENT,bank_id INTEGER NOT NULL REFERENCES bank(id),
            name TEXT NOT NULL,attachment TEXT,size INTEGER NOT NULL,von TEXT,am TEXT);
        CREATE INDEX IF NOT EXISTS umsatz_belege_bank ON umsatz_belege(bank_id);
    """)
    db.commit()


def _jetzt():
    return datetime.now().isoformat(timespec='seconds')


def _umsatz(store, bid):
    if not isinstance(bid, int) or isinstance(bid, bool) or not 0 < bid < 2 ** 62:   # auch zu große Zahlen: sonst Fehler der Datenbank statt Meldung
        raise ValueError('Ungültige Angaben.')
    b = store.db.execute("SELECT id FROM bank WHERE id=? AND ledger='bank'", (bid,)).fetchone()
    if not b:
        raise ValueError('Kontoumsatz nicht gefunden.')
    return b


def ergaenzen(store, rows):
    """Umsätze der Gesamtdaten um das ergänzen, was die Liste der Kontoumsätze zeigt – ohne Notiztext und Dateinamen:
    `hat_notiz`, `belege` (Anzahl), `ohne_beleg` (Regel `OHNE_BELEG`) und bei verbuchten Kontoumsätzen `bezug`
    (`{art, titel, zusatz}`: Rechnung bzw. Eingangsrechnung mit Nummer, Beitrag mit Mitglied bzw. Anzahl)."""
    from core import bank_source
    db = store.db
    notiz = {r[0] for r in db.execute('SELECT bank_id FROM umsatz_notizen')}
    belege = {r[0]: r[1] for r in db.execute('SELECT bank_id,COUNT(*) FROM umsatz_belege GROUP BY bank_id')}
    ohne = {r[0] for r in db.execute(f'SELECT b.id FROM bank b WHERE {OHNE_BELEG}')}
    rechnung, eingang, beitrag = {}, {}, {}
    for r in db.execute('SELECT p.bank_id,i.number,i.recipient FROM payments p JOIN invoices i ON i.id=p.invoice_id ORDER BY p.id'):
        rechnung.setdefault(r[0], []).append((r[1], r[2]))
    for r in db.execute('SELECT p.bank_id,x.number,x.creditor FROM bill_payments p JOIN bills x ON x.id=p.bill_id ORDER BY p.id'):
        eingang.setdefault(r[0], []).append((r[1], r[2]))
    for r in db.execute('SELECT j.source,m.name FROM dues_assignments a JOIN entries e ON e.id=a.entry_id JOIN journals j ON j.id=e.journal_id '
                        'JOIN members m ON m.id=a.member_id UNION ALL SELECT j.source,x.name FROM dues_external x '
                        'JOIN entries e ON e.id=x.entry_id JOIN journals j ON j.id=e.journal_id'):
        beitrag.setdefault(r[0], []).append(r[1])

    def mit_nummer(art, eins, viele, treffer):
        return {'art': art, 'titel': f'{eins} {treffer[0][0]}' if len(treffer) == 1 else f'{len(treffer)} {viele}',
                'zusatz': treffer[0][1] if len(treffer) == 1 else ', '.join(t[0] for t in treffer)}

    for b in rows:
        b.update(hat_notiz=b['id'] in notiz, belege=belege.get(b['id'], 0), ohne_beleg=b['id'] in ohne)
        if b['state'] != 'booked' or b['ledger'] != 'bank':
            continue
        namen = [n for kind in ('bank', 'resolve') for n in beitrag.get(bank_source(kind, b), [])]   # nur die geltende Zuordnungsrunde
        if b['id'] in eingang:
            b['bezug'] = mit_nummer('eingangsrechnung', 'Eingangsrechnung', 'Eingangsrechnungen', eingang[b['id']])
        elif b['id'] in rechnung:
            b['bezug'] = mit_nummer('rechnung', 'Rechnung', 'Rechnungen', rechnung[b['id']])
        elif namen:
            b['bezug'] = {'art': 'beitrag', 'titel': '', 'zusatz': namen[0] if len(namen) == 1 else f'{len(namen)} Beiträge den Mitgliedern zugeordnet'}
    return rows


def stand(store):
    """Stand des Kontoauszugs für den Satz über der Liste: `am` = letzter Import (Zeitpunkt), `bis` = jüngster Buchungstag."""
    return {'am': store.db.execute('SELECT MAX(created) FROM imports').fetchone()[0] or '',
            'bis': store.db.execute("SELECT MAX(booked) FROM bank WHERE ledger='bank' AND state!='void'").fetchone()[0] or ''}


def ohne_beleg(store, year):
    """Anzahl verbuchter Ausgaben eines Jahres ohne Beleg (Regel `OHNE_BELEG`) – für das Prüfpaket."""
    return store.db.execute(f'SELECT COUNT(*) FROM bank b WHERE {OHNE_BELEG} AND substr(b.booked,1,4)=?', (str(year),)).fetchone()[0]


def akte(store, bid):
    """Notiz (Text, wer, wann) und Belege (Kennung, Name, Größe, wer, wann) eines Kontoumsatzes – Abruf für die Oberfläche."""
    _umsatz(store, bid)
    n = store.db.execute('SELECT text,von,am FROM umsatz_notizen WHERE bank_id=?', (bid,)).fetchone()
    return {'notiz': dict(n) if n else None,
            'belege': store.rows('SELECT id,name,size,von,am FROM umsatz_belege WHERE bank_id=? ORDER BY id', (bid,))}


def notiz(store, bid, text, actor):
    """Notiz zum Kontoumsatz speichern (leer = löschen) – im Protokoll ohne den Text. → gespeicherte Notiz oder None."""
    _umsatz(store, bid)
    if not isinstance(text, str):
        raise ValueError('Ungültige Angaben.')
    text = ''.join(c for c in text.replace('\r\n', '\n') if c == '\n' or (c.isprintable() and c not in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069')).strip()   # keine Steuer- und Richtungszeichen (stehen sonst im Prüfpaket)
    if len(text) > MAX_NOTIZ:
        raise ValueError(f'Notiz: höchstens {MAX_NOTIZ} Zeichen.')
    alt = store.db.execute('SELECT text FROM umsatz_notizen WHERE bank_id=?', (bid,)).fetchone()
    if text != (alt['text'] if alt else ''):
        with store.db:
            if text:
                store.db.execute('INSERT INTO umsatz_notizen(bank_id,text,von,am) VALUES(?,?,?,?) ON CONFLICT(bank_id) DO UPDATE SET '
                                 'text=excluded.text,von=excluded.von,am=excluded.am', (bid, text, actor, _jetzt()))
            else:
                store.db.execute('DELETE FROM umsatz_notizen WHERE bank_id=?', (bid,))
            store.audit(actor, 'Kontoumsatz: Notiz geändert', {'bank_id': bid, 'leer': not text})
    return akte(store, bid)['notiz']


def datei(name, content_b64):
    """Belegdatei prüfen wie alle Belege (`verbindlichkeiten.check_file`), aber ohne E-Rechnung: nur PDF, PNG, JPG. → (Endung, Bytes)"""
    if not str(name).lower().endswith(ARTEN):
        raise ValueError('Beleg bitte als PDF, PNG oder JPG anheften.')
    return verbindlichkeiten.check_file(name, content_b64)


def anheften(store, bid, name, suffix, size, actor, speichern):
    """Beleg am Kontoumsatz eintragen und die Datei über `speichern(Kennung, Nummer, Dateiname)` ablegen lassen
    (app.save_receipt; scheitert das Speichern, bleibt kein Eintrag zurück). → Kennung des Belegs."""
    _umsatz(store, bid)
    if store.db.execute('SELECT COUNT(*) FROM umsatz_belege WHERE bank_id=?', (bid,)).fetchone()[0] >= MAX_BELEGE:
        raise ValueError(f'Höchstens {MAX_BELEGE} Belege je Umsatz.')
    name = ''.join(c for c in Path(str(name).replace('\\', '/')).name if c.isprintable())[:120] or 'Beleg' + suffix
    with store.db:
        rid = store.db.execute('INSERT INTO umsatz_belege(bank_id,name,size,von,am) VALUES(?,?,?,?,?)', (bid, name, size, actor, _jetzt())).lastrowid
        nummer = f'Umsatz-{bid}-{rid}'
        store.db.execute('UPDATE umsatz_belege SET attachment=? WHERE id=?', (nummer + suffix, rid))
    try:
        speichern(rid, nummer, nummer + suffix)
    except ValueError:
        with store.db:
            store.db.execute('DELETE FROM umsatz_belege WHERE id=?', (rid,))
        raise ValueError('Der Beleg konnte nicht gespeichert werden. Bitte erneut versuchen.') from None
    with store.db:
        store.audit(actor, 'Kontoumsatz: Beleg angeheftet', {'bank_id': bid, 'beleg': rid, 'datei': name})
    return rid


def beleg(store, rid, folder):
    """Angehefteter Beleg mit dem Pfad seiner Datei in `folder` (Daten/Belege) – `pfad` ist None, wenn die Datei fehlt oder nicht
    dorthin gehört. Unbekannte Kennung: ValueError."""
    b = store.db.execute('SELECT * FROM umsatz_belege WHERE id=?', (rid,)).fetchone() if isinstance(rid, int) and not isinstance(rid, bool) and 0 < rid < 2 ** 62 else None
    if not b:
        raise ValueError('Beleg nicht gefunden.')
    root = Path(folder).resolve()
    target = (root / Path(str(b['attachment'] or '-')).name).resolve()
    ok = root in target.parents and target.is_file() and target.suffix in ARTEN
    return {**dict(b), 'pfad': target if ok else None}


def entfernen(store, rid, folder, actor):
    """Beleg vom Kontoumsatz entfernen: erst die Datei in `folder` wirklich löschen, dann den Eintrag; steht im Protokoll."""
    b = beleg(store, rid, folder)
    try:
        if b['pfad']:
            b['pfad'].unlink()
    except OSError:
        raise ValueError('Die Belegdatei ließ sich nicht löschen. Der Beleg bleibt angeheftet.') from None
    with store.db:
        store.db.execute('DELETE FROM umsatz_belege WHERE id=?', (rid,))
        store.audit(actor, 'Kontoumsatz: Beleg entfernt', {'bank_id': b['bank_id'], 'beleg': rid, 'datei': b['name']})
    return b['bank_id']


def fuer_jahr(store, year):
    """Notizen und Belege der Kontoumsätze eines Jahres für das Prüfpaket: `{Umsatz: {'notiz': Text, 'belege': [Zeilen]}}`."""
    out = {}
    for r in store.rows("SELECT n.bank_id,n.text FROM umsatz_notizen n JOIN bank b ON b.id=n.bank_id WHERE substr(b.booked,1,4)=?", (str(year),)):
        out.setdefault(r['bank_id'], {'notiz': '', 'belege': []})['notiz'] = r['text']
    for r in store.rows("SELECT u.* FROM umsatz_belege u JOIN bank b ON b.id=u.bank_id WHERE substr(b.booked,1,4)=? ORDER BY u.id", (str(year),)):
        out.setdefault(r['bank_id'], {'notiz': '', 'belege': []})['belege'].append(r)
    return out
