"""Endgültiges Löschen von Daten vor einem Stichtag (Anweisung Lennert Gust 29.10.2026: „die 2024er Daten
vollständig löschen“, einschließlich der Sicherungen).

Gelöscht werden Bankumsätze vor dem Stichtag mit ihren Buchungen sowie Angaben dazu im Änderungsprotokoll;
danach wird die Datei mit VACUUM neu geschrieben, damit nichts im freien Speicher der Datenbank zurückbleibt.
Der Stichtag wird als Datenbeginn gespeichert: Kontoauszug und Excel-Übernahme übernehmen ältere Zeilen nicht mehr.

Nur bei beendetem Programm und auf einer Kopie der Datenbank ausführen (Aufruf siehe unten); die Kopie ersetzt nach
Prüfung die Datenbank im Ordner Daten. Rechnungen, Eingangsrechnungen, Zahlungen oder Lastschriften vor dem
Stichtag werden nicht automatisch gelöscht – dann bricht die Bereinigung ab.

    python3 bereinigung.py <kopie.sqlite3> 2025-01-01 "Name"
"""
import json
import re
import sys
from datetime import date

from core import Store


def _bank_journals(store, ids):
    """Journale, die zu den Bankumsätzen gehören (bank:7, bank:7/1, resolve:7 …)."""
    pattern = re.compile(r'^[a-z-]+:(\d+)(/\d+)?$')
    out = []
    for j in store.rows('SELECT id,source FROM journals'):
        m = pattern.match(j['source'] or '')
        if m and int(m.group(1)) in ids and j['source'].split(':')[0] in ('bank', 'resolve', 'reverse-bank', 'reverse-resolve'):
            out.append(j['id'])
    return out


def check(store, cutoff):
    """Was gelöscht würde – und was die Bereinigung verhindert."""
    year = int(cutoff[:4])
    ids = {r[0] for r in store.db.execute('SELECT id FROM bank WHERE booked<?', (cutoff,))}
    journals = set(_bank_journals(store, ids))
    old = {r[0] for r in store.db.execute('SELECT id FROM journals WHERE COALESCE(year,CAST(substr(day,1,4) AS INTEGER))<? OR day<?', (year, cutoff))}
    blockers = []
    if not cutoff.endswith('-01-01'):
        blockers.append('Stichtag muss ein 1. Januar sein (ganze Haushaltsjahre)')
    import kvbudget
    if kvbudget.start(store) < cutoff and store.db.execute('SELECT 1 FROM kv_year_values LIMIT 1').fetchone():
        blockers.append(f'Die KV-Budgets beginnen vor dem Stichtag ({kvbudget.start(store)[:4]}) – sonst änderten sich Überträge unbemerkt. '
                        f'Zuerst in den Stammdaten „KV-Finanzrahmen ab Haushaltsjahr“ auf {year} setzen und den Anfangsbestand {year} eintragen')
    if old - journals:
        blockers.append(f'{len(old - journals)} Buchungen gehören zu einem Haushaltsjahr vor dem Stichtag, aber zu keinem gelöschten Bankumsatz '
                        '(z. B. Zahlung im Januar für das Vorjahr) – vorher im Programm klären')
    later = store.db.execute(f"SELECT COUNT(*) FROM journals WHERE id IN ({','.join('?' * len(journals)) or 'NULL'}) "
                             "AND COALESCE(year,CAST(substr(day,1,4) AS INTEGER))>=?", (*journals, year)).fetchone()[0]
    if later:
        blockers.append(f'{later} Buchungen dieser Umsätze zählen zu einem späteren Haushaltsjahr')
    for table, column in (('payments', 'bank_id'), ('bill_payments', 'bank_id'), ('reversed_allocations', 'bank_id')):
        n = store.db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({','.join('?' * len(ids)) or 'NULL'})", tuple(ids)).fetchone()[0]
        if n:
            blockers.append(f'{n} Einträge in {table} verweisen auf diese Umsätze')
    # RPJ (Nachbarjahr erlaubt): gültige Zuordnungen dürfen nicht über den Stichtag hinweg reichen – sonst änderten sich Zahlen unbemerkt
    skip = kvbudget._excluded_journals(store)
    old_links = later_links = 0
    for r in store.rows('SELECT e.journal_id,COALESCE(p.year,q.year) owner FROM rpj_links l JOIN entries e ON e.id=l.entry_id '
                        'LEFT JOIN rpj_projects p ON p.id=l.project_id LEFT JOIN rpj_requests q ON q.id=l.request_id'):
        if r['journal_id'] in skip:
            continue                                    # stornierte Buchung: Zuordnung zählt nicht und wird mit gelöscht
        if r['journal_id'] in journals and r['owner'] >= year:
            later_links += 1
        elif r['journal_id'] not in journals and r['owner'] < year:
            old_links += 1
    if old_links:
        blockers.append(f'{old_links} RPJ-Zuordnungen späterer Buchungen verweisen auf Projekte bzw. Mittelabrufe vor dem Stichtag – vorher umhängen')
    if later_links:
        blockers.append(f'{later_links} RPJ-Zuordnungen zu löschender Buchungen gehören zu Projekten bzw. Mittelabrufen ab {year} – '
                        f'die RPJ-Zahlen {year} würden sich ändern; vorher klären')
    for table, column in (('invoices', 'issued'), ('bills', 'issued'), ('cash_donations', 'received_on'), ('debit_batches', 'created'), ('bill_reviews', 'year')):
        limit = year if column == 'year' else cutoff
        n = store.db.execute(f'SELECT COUNT(*) FROM {table} WHERE {column}<?', (limit,)).fetchone()[0]
        if n:
            blockers.append(f'{n} Einträge in {table} liegen vor dem Stichtag')
    return {'bank': sorted(ids), 'journals': sorted(journals), 'blockers': blockers}


PLAN_TABLES = ('household_plan', 'budgets', 'kv_closings', 'kv_year_values', 'dues_targets', 'rpj_years')   # reine Jahreswerte: werden mit gelöscht
RPJ_ACTIONS = ('RPJ-Buchung zugeordnet', 'RPJ-Zuordnung geändert')
BULK_KEYS = {'Beiträge nach Vorschlag zugeordnet': 'zuordnungen', 'RPJ-Buchungen gemeinsam zugeordnet': 'zeilen',
             'Klärfälle gemeldet': 'faelle'}   # Sammelzuordnungen im Protokoll
CASE_ACTIONS = ('Klärfall gemeldet', 'Klärfall geklärt', 'Klärfall: Rückfrage beim Kreisverband', 'Klärfall: Rückfrage beendet')   # klaerfaelle.py, je mit 'entry'


def _about_deleted(detail, ids):
    """Protokolleintrag zu einem gelöschten Bankumsatz (verbucht, geparkt, storniert, Lernbeispiel …)?"""
    for key in ('id', 'bank_id', 'bank'):
        value = detail.get(key)
        if isinstance(value, int) and value in ids:
            return True
    return False


def _redact(detail, cutoff):
    """Angaben vor dem Stichtag aus einem Protokolleintrag entfernen (z. B. übersprungene Excel-Zeilen)."""
    year = int(cutoff[:4])
    changed = False
    if isinstance(detail.get('uebersprungen'), list):
        kept = [r for r in detail['uebersprungen'] if not (r.get('datum') and r['datum'] < cutoff) and not str(r.get('blatt', '')).endswith(tuple(str(y) for y in range(2000, year)))]
        changed |= len(kept) != len(detail['uebersprungen'])
        detail['uebersprungen'] = kept
    if isinstance(detail.get('sheets'), list):
        kept = [s for s in detail['sheets'] if not any(s.endswith(str(y)) for y in range(2000, year))]
        changed |= len(kept) != len(detail['sheets'])
        detail['sheets'] = kept
    if changed and 'counts' in detail:
        detail.pop('counts')                   # Zähler enthielten die gelöschten Zeilen
    if changed:
        detail['bereinigt'] = f'Angaben vor dem {cutoff} am {date.today().isoformat()} entfernt'
    return changed


def purge_before(store, cutoff, actor):
    """Löscht Bankumsätze vor dem Stichtag samt Buchungen, bereinigt das Protokoll, setzt den Datenbeginn."""
    cutoff = date.fromisoformat(cutoff).isoformat()
    found = check(store, cutoff)
    if found['blockers']:
        raise ValueError('Bereinigung nicht möglich: ' + '; '.join(found['blockers']))
    ids, journals = found['bank'], found['journals']
    marks = lambda seq: ','.join('?' * len(seq)) or 'NULL'  # noqa: E731
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        entry_ids = {r[0] for r in store.db.execute(f'SELECT id FROM entries WHERE journal_id IN ({marks(journals)})', journals)}
        store.db.execute(f'DELETE FROM dues_assignments WHERE entry_id IN (SELECT id FROM entries WHERE journal_id IN ({marks(journals)}))', journals)
        store.db.execute(f'DELETE FROM dues_cases WHERE entry_id IN (SELECT id FROM entries WHERE journal_id IN ({marks(journals)}))', journals)
        store.db.execute(f'DELETE FROM dues_external WHERE entry_id IN (SELECT id FROM entries WHERE journal_id IN ({marks(journals)}))', journals)
        store.db.execute(f'DELETE FROM rpj_links WHERE entry_id IN (SELECT id FROM entries WHERE journal_id IN ({marks(journals)}))', journals)
        old_year = int(cutoff[:4])   # RPJ-Projekte, Plan und Mittelabrufe der gelöschten Jahre (Prüfung oben: keine späteren Buchungen daran)
        store.db.execute('DELETE FROM rpj_links WHERE project_id IN (SELECT id FROM rpj_projects WHERE year<?) OR request_id IN (SELECT id FROM rpj_requests WHERE year<?)', (old_year, old_year))
        store.db.execute('DELETE FROM rpj_plan WHERE project_id IN (SELECT id FROM rpj_projects WHERE year<?)', (old_year,))
        rpj_rows = store.db.execute('DELETE FROM rpj_projects WHERE year<?', (old_year,)).rowcount + store.db.execute('DELETE FROM rpj_requests WHERE year<?', (old_year,)).rowcount
        store.db.execute(f'DELETE FROM kv_labels WHERE journal_id IN ({marks(journals)}) OR journal_id IN (SELECT id FROM journals WHERE reversal_of IN ({marks(journals)}))', journals + journals)
        store.db.execute(f'DELETE FROM entries WHERE journal_id IN ({marks(journals)})', journals)
        store.db.execute(f'DELETE FROM journals WHERE reversal_of IN ({marks(journals)})', journals)
        store.db.execute(f'DELETE FROM journals WHERE id IN ({marks(journals)})', journals)
        store.db.execute(f'DELETE FROM booking_examples WHERE bank_id IN ({marks(ids)})', ids)
        sepa = [r['stored'] for r in store.db.execute(f'SELECT stored FROM sepa_files WHERE bank_id IN ({marks(ids)})', ids) if r['stored']]
        store.db.execute(f'DELETE FROM sepa_files WHERE bank_id IN ({marks(ids)})', ids)   # SEPA-Dateien zu gelöschten Sammelbuchungen
        store.db.execute(f'DELETE FROM bank WHERE id IN ({marks(ids)})', ids)
        plans = 0
        for table in PLAN_TABLES:
            plans += store.db.execute(f'DELETE FROM {table} WHERE year<?', (int(cutoff[:4]),)).rowcount
        redacted = removed = 0
        deleted = set(ids)
        for r in store.rows("SELECT id,action,detail FROM audit"):
            try:
                detail = json.loads(r['detail'] or '{}')
            except ValueError:
                continue
            if not isinstance(detail, dict):
                continue
            if (r['action'].startswith('Bankumsatz') or 'Lernbeispiel' in r['action'] or r['action'] == 'SEPA-Sammler aufgeteilt') and _about_deleted(detail, deleted) \
                    or (r['action'] in ('Mitgliedsbeitrag zugeordnet', 'Beitragszuordnung geändert') + CASE_ACTIONS + RPJ_ACTIONS
                        and detail.get('entry') in entry_ids) \
                    or (r['action'].startswith('RPJ-') and isinstance(detail.get('jahr'), int) and detail['jahr'] < int(cutoff[:4])):
                store.db.execute('DELETE FROM audit WHERE id=?', (r['id'],))   # Inhalt betrifft nur gelöschte Umsätze bzw. Buchungen
                removed += 1
            elif r['action'] in ('Beitrags-Soll festgelegt', 'Festgelegtes Beitrags-Soll entfernt') and int(detail.get('year') or 9999) < int(cutoff[:4]):
                store.db.execute('DELETE FROM audit WHERE id=?', (r['id'],))   # Festlegung (mit Grund) eines gelöschten Jahres
                removed += 1
            elif r['action'] in BULK_KEYS and any(z.get('entry') in entry_ids for z in detail.get(BULK_KEYS[r['action']], [])):
                key = BULK_KEYS[r['action']]
                kept = [z for z in detail[key] if z.get('entry') not in entry_ids]
                if kept:
                    detail.update({key: kept, 'anzahl': len(kept), 'bereinigt': f'Angaben vor dem {cutoff} am {date.today().isoformat()} entfernt'})
                    store.db.execute('UPDATE audit SET detail=? WHERE id=?', (json.dumps(detail, ensure_ascii=False), r['id']))
                    redacted += 1
                else:
                    store.db.execute('DELETE FROM audit WHERE id=?', (r['id'],))
                    removed += 1
            elif _redact(detail, cutoff):
                store.db.execute('UPDATE audit SET detail=? WHERE id=?', (json.dumps(detail, ensure_ascii=False), r['id']))
                redacted += 1
        store.db.execute("INSERT INTO settings(key,value) VALUES('data_start',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (cutoff,))
        result = {'umsaetze': len(ids), 'buchungen': len(journals), 'planwerte': plans + rpj_rows, 'protokolleintraege_bereinigt': redacted,
                  'protokolleintraege_entfernt': removed, 'datenbeginn': cutoff,
                  **({'sepa_belege_von_hand_loeschen': sepa} if sepa else {})}   # Dateien in Daten/Belege (wie Daten/Importe, siehe REGELN Punkt 11)
        store.audit(actor, f'Daten vor dem {cutoff} endgültig gelöscht', result)
    store.db.execute('VACUUM')                  # gelöschte Inhalte auch aus dem freien Speicher der Datei entfernen
    return result


if __name__ == '__main__':
    from pathlib import Path
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    if not path.is_file() or path.read_bytes()[:16] != b'SQLite format 3\x00':
        sys.exit(f'{path}: keine vorhandene SQLite-Datenbank.')
    if path.parent.name == 'Daten' or (path.parent / '.steuerung').exists():
        sys.exit('Nur auf einer Kopie ausführen, nicht auf der Datenbank im Ordner Daten.')
    import sqlite3
    probe = sqlite3.connect(path)
    known = probe.execute("SELECT 1 FROM sqlite_master WHERE name='bank'").fetchone()
    probe.close()
    if not known:
        sys.exit('Keine Datenbank des Finanzprogramms.')
    s = Store(path)
    print(json.dumps(purge_before(s, sys.argv[2], sys.argv[3]), ensure_ascii=False))
    print('integrity', s.db.execute('PRAGMA integrity_check').fetchone()[0])
    s.db.close()
