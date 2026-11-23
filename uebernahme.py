"""Übernahme der vorkontierten Umsätze aus der Excel-Masterdatei („Umsatz JJJJ“-Blätter).

Jede Zeile wird als Bankumsatz mit Buchung angelegt – mit denselben Journalstrukturen wie beim
Verbuchen im Programm (Quelle ``bank:<id>``), damit Storno, Haushalt und KV-Budgets wie gewohnt
funktionieren. Fachliche Regeln (Freigabe 28.10.2026):

* Haushaltposten = Sachkonto der Excel-Vorlage; Titel wie im Gesamthaushalt.
* 8100 Mitgliedsbeiträge direkt, ohne Beitragsrechnung; Ausgänge auf 8100 sind Rücklastschriften
  bzw. Erstattungen und mindern die Beitragseinnahmen.
* 5100–5800: Sammelkonto „KV“ mit der jeweiligen KV-Kennung (Eingänge mindern die KV-Ausgaben).
* 1370: Umsatz wird geparkt. Zeilen ohne oder mit unbekanntem Haushaltposten werden ebenfalls auf das
  Parkkonto 1370 gebucht (Entscheidung Lennert Gust 29.10.2026: „alles, was nicht zugeordnet werden kann,
  auf ein Parkkonto“; vorher offen bzw. übersprungen). Offen bleiben nur Umsätze, die zu einer erfassten
  Rechnung gehören – sie werden im Buchungsplatz der Rechnung zugeordnet.
* 1371 und 1592 werden wie im Excel-Haushalt als Einnahmetitel (netto) geführt.
* Haushaltsjahr = Jahr des Blatts („Umsatz 2025“), auch wenn das Bankdatum im Folgejahr liegt.

Abgleich mit importierten Kontoauszügen: Im Zeitraum der vorhandenen Bankumsätze legt die Übernahme keine
neuen Umsätze an, sondern verbucht die vorhandenen. Gefunden wird zuerst über den Fingerabdruck, sonst über
gleichen Betrag, Datum ±5 Tage und den ähnlichsten Verwendungszweck – nur bei eindeutigem Treffer. Was dort nicht
eindeutig gefunden wird, wird nicht angelegt (Status „fehlt“) und bleibt für den Buchungsplatz.
"""
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta

from bankimport import split_combined
from formate import cents_from_number, iso, money
from core import bank_source
from haushalt import CODE_TITLES, KV_ROWS, TITLES

SHEET = re.compile(r'^\s*Umsatz\s+(\d{4})\s*$', re.IGNORECASE)
COLUMNS = {
    'booked': ['buchungsdatum', 'valuta', 'datum'],
    'text': ['verwendungszweck', 'text'],
    'posten': ['haushaltposten', 'haushaltsposten', 'konto', 'sachkonto'],
    'amount': ['betrag'],
    'kst': ['kostenstelle'],
}
PARK = '1370'
SOURCE_NOTE = 'Excel-Übernahme'
WINDOW_DAYS = 5              # Buchungs- und Wertstellungsdatum weichen zwischen Excel und Kontoauszug ab
STATUSES = ('neu', 'zuordnen', 'parken', 'offen', 'vorhanden', 'fehlt', 'fehler')


def _norm(value):
    return ' '.join(str(value or '').strip().casefold().split())


def _header(row):
    names = [_norm(v) for v in row]
    found = {}
    for field, aliases in COLUMNS.items():
        for alias in aliases:
            if alias in names:
                found[field] = names.index(alias)
                break
    return found if {'booked', 'text', 'amount'} <= found.keys() else None


def _code(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def fingerprint(booked, amount, name, reference):
    """Gleiche Berechnung wie beim Kontoauszugsimport – erkennt dieselben Umsätze wieder."""
    return hashlib.sha256(json.dumps([booked, amount, name, reference], ensure_ascii=False).encode()).hexdigest()


def plan_row(code, amount, kst):
    """Ermittelt die Buchung für einen Haushaltposten: (status, account, kv, title, hinweis)."""
    if code is None:
        return 'parken', PARK, 'LV', None, 'Kein Haushaltposten – auf das Parkkonto 1370 gebucht.'
    if code == PARK:
        return 'parken', '1370', 'LV', None, ''
    if code in KV_ROWS:
        note = ''
        if kst and _norm(kst) not in ('', 'landesverband') and not _kst_matches(code, kst):
            note = f'Kostenstelle „{kst}“ passt nicht zum Posten {code}; Posten gilt.'
        return 'neu', 'KV', code, KV_ROWS[code], note
    if code in CODE_TITLES:
        title = CODE_TITLES[code]
        note = ''
        if code == '8100' and amount < 0:
            note = 'Ausgang auf 8100: Rücklastschrift/Erstattung.'
        elif TITLES[title]['kind'] == 'expense' and amount > 0:
            note = 'Eingang auf Ausgabenposten: mindert die Ausgaben.'
        return 'neu', code, 'LV', title, note
    # Entscheidung 29.10.2026: unbekannte Posten auf das Parkkonto (zuvor: offen, davor: überspringen)
    return 'parken', PARK, 'LV', None, f'Haushaltposten {code} ist im Programm nicht bekannt – auf das Parkkonto 1370 gebucht.'


def _kst_matches(code, kst):
    names = {'5100': 'altona', '5200': 'bergedorf', '5300': 'eimsb', '5400': 'harburg', '5500': 'mitte',
             '5600': 'nord', '5700': 'wandsb', '5800': 'sch'}
    return names[code] in _norm(kst)


def parse(raw):
    import openpyxl
    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise ValueError('Excel-Datei (.xlsx) konnte nicht gelesen werden.')
    rows, sheets, ignored = [], [], []
    for sheet in book.worksheets:
        match = SHEET.match(sheet.title)
        if not match:
            ignored.append(sheet.title)
            continue
        values = list(sheet.iter_rows(values_only=True))
        header = None
        for index, row in enumerate(values[:10]):
            header = _header(row)
            if header:
                break
        if not header:
            ignored.append(sheet.title)
            continue
        sheets.append(sheet.title)
        for offset, row in enumerate(values[index + 1:], start=index + 2):
            def get(field):
                i = header.get(field)
                return row[i] if i is not None and i < len(row) else None
            if all(get(f) in (None, '') for f in ('booked', 'text', 'amount', 'posten')):
                continue
            item = {'sheet': sheet.title, 'year_sheet': int(match.group(1)), 'row_no': offset}
            try:
                raw_date, raw_amount = get('booked'), get('amount')
                booked = raw_date.date().isoformat() if isinstance(raw_date, datetime) else (
                    raw_date.isoformat() if isinstance(raw_date, date) else iso(raw_date))
                amount = cents_from_number(raw_amount) if isinstance(raw_amount, (int, float)) else money(raw_amount)
                if not amount:
                    raise ValueError('Betrag fehlt oder ist 0.')
                reference = ' '.join(str(get('text') or '').split())
                kst = ' '.join(str(get('kst') or '').split())
                item.update(booked=booked, amount=amount, reference=reference, name=split_combined(reference),
                            code=_code(get('posten')), kst=kst)
            except (ValueError, TypeError, AttributeError) as e:
                item['error'] = str(e) if isinstance(e, ValueError) else 'Datum oder Betrag fehlt.'
            rows.append(item)
    if not sheets:
        raise ValueError('Keine Blätter „Umsatz JJJJ“ mit den Spalten Valuta/Buchungsdatum, Verwendungszweck und Betrag gefunden.')
    return rows, sheets, ignored


def _de(day):
    return f'{day[8:10]}.{day[5:7]}.{day[:4]}'


# Wörter ohne Aussagekraft für den Abgleich: Buchungsarten, SEPA-Kennzeichen, Absender, Allerweltswörter, Monate
STOPWORDS = {'sepa', 'gutschrift', 'lastschrift', 'ueberweisung', 'echtzeitueberweisung', 'dauerauftrag', 'einzug',
             'basislastschrift', 'firmenlastschrift', 'auftrag', 'buchung', 'direct', 'banking', 'online', 'datum',
             'eref', 'mref', 'cred', 'svwz', 'abwa', 'abwe', 'iban', 'bic', 'kref', 'notprovided', 'end', 'ref',
             'junge', 'union', 'hamburg', 'landesverband', 'kreisverband', 'und', 'der', 'die', 'das', 'fuer', 'von',
             'mit', 'beitrag', 'beitraege', 'mitgliedsbeitrag', 'mitgliedsbeitraege', 'jahresbeitrag', 'spende',
             'spenden', 'miete', 'rechnung', 'entgelt', 'entgelte', 'zinsen', 'kontofuehrung', 'gebuehr', 'gebuehren',
             'erstattung', 'auslagen', 'abschlag', 'januar', 'februar', 'maerz', 'april', 'mai', 'juni', 'juli',
             'august', 'september', 'oktober', 'november', 'dezember'}
_UMLAUTS = str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss'})
_IBAN = re.compile(r'[a-z]{2}\d{2}[a-z0-9]{8,30}')
_CREDITOR_ID = re.compile(r'[a-z]{2}\d{2}zzz[a-z0-9]*')       # Gläubiger-ID steht in jeder eigenen Lastschrift


def words(text):
    """Aussagekräftige Wörter: Namensbestandteile ab 3 Buchstaben, IBAN und lange Nummern (Referenz) ab 6 Ziffern."""
    found = set()
    for word in re.findall(r'[a-z0-9]+', _norm(text).translate(_UMLAUTS)):
        if word in STOPWORDS or _CREDITOR_ID.fullmatch(word):
            continue
        if word.isdigit():
            if len(word) >= 6:
                found.add(word)
        elif _IBAN.fullmatch(word):
            found.add(word)
        elif len(word) >= 3 and not any(ch.isdigit() for ch in word):
            found.add(word)
    return found


def strong(common):
    """Starker Bezug: gemeinsame IBAN bzw. lange Nummer oder mindestens zwei gemeinsame Namenswörter."""
    return any(any(ch.isdigit() for ch in w) for w in common) or len(common) >= 2


def _note(item, text):
    item['note'] = f'{item["note"]} {text}' if item.get('note') else text


def _bill_for(store, item, bank=None):
    """Erfasste Rechnung zu einem Umsatz – Eingangsrechnung bei Abbuchungen, Ausgangsrechnung bei Eingängen –
    geprüft mit dem Excel-Text und dem Text des Kontoauszugs."""
    texts = [item] + ([{**item, 'name': bank['name'], 'reference': bank['reference'], 'booked': bank['booked']}] if bank is not None else [])
    for variant in texts:
        hit = _bill_hit(store, variant, item['plan'])
        if hit:
            return hit
    return None


def _decide(store, item, match):
    """Status einer Excel-Zeile, nachdem feststeht, ob es den Umsatz schon gibt (match = Bankzeile oder None)."""
    status = item['plan']
    bill = _bill_for(store, item, match)
    if match and match['state'] == 'open' and match['reversals']:
        item.update(status='vorhanden', bank_id=match['id'])
        _note(item, 'Im Buchungsplatz storniert – bitte dort neu zuordnen (Rechnung oder Eingangsrechnung prüfen).')
    elif match and (match['state'] != 'open' or status not in ('neu', 'parken')):
        item.update(status='vorhanden', bank_id=match['id'])
    elif bill:
        # Umsatz gehört zu einer erfassten Rechnung: nicht nach Excel-Posten buchen, sonst doppelt im Haushalt
        note = f'Gehört zur {bill["label"]} {bill["number"]} ({bill["name"]}) – im Buchungsplatz zuordnen.'
        if match:
            item.update(status='vorhanden', bank_id=match['id'])
        else:
            item.update(plan='offen', status='offen')
        _note(item, note)
    elif match:   # vorhandenen Umsatz verbuchen bzw. parken
        item.update(status='parken' if status == 'parken' else 'zuordnen', bank_id=match['id'])
    if match and match['booked'][:4] != str(item['year_sheet']) and item['booked'][:4] == str(item['year_sheet']):
        _note(item, f'Bankdatum {match["booked"][:4]} – Haushaltsjahr laut Blatt: {item["year_sheet"]}.')


def _plan_key(item):
    return (item['plan'], item.get('code'), item.get('account'), item.get('kv'), item.get('title'), item['year_sheet'], item['amount'])


def match_loose(items, banks, days=WINDOW_DAYS):
    """Excel-Zeilen ohne Fingerabdruck-Treffer den freien Bankumsätzen zuordnen.

    Kandidat: gleicher Betrag (mit Vorzeichen), Datum höchstens `days` Tage auseinander und ein starker Textbezug
    (`strong`). Rangfolge: Wortähnlichkeit, dann Datumsnähe. Zugeordnet wird in einem Durchgang nur, was gegenseitig
    strikt am besten passt – kein Ausschlussverfahren. Gleichwertige Mehrdeutigkeit (gleiche Excel-Kontierung,
    dieselben Kandidaten, gleich viele) wird der Reihe nach verteilt: Die Buchungen sind identisch.
    Liefert ({id(item): bankzeile}, {id(item): 'tie'|'lost'} für nicht zugeordnete Zeilen mit Kandidaten,
    {id(item) gleichwertig verteilter Zeilen}).
    """
    by_amount = {}
    for bank in banks:
        by_amount.setdefault(bank['amount'], []).append(
            (bank, date.fromisoformat(bank['booked']), words(f'{bank["name"] or ""} {bank["reference"] or ""}')))
    scored = []
    for item in items:
        day, mine = date.fromisoformat(item['booked']), words(f'{item["name"]} {item["reference"]}')
        for bank, bank_day, theirs in by_amount.get(item['amount'], ()):
            gap = abs((bank_day - day).days)
            common = mine & theirs
            if gap > days or not strong(common):
                continue
            scored.append(((round(len(common) / len(mine | theirs), 9), -gap), id(item), bank))

    def strict_best(entries):
        entries = sorted(entries, key=lambda e: e[0], reverse=True)
        return entries[0] if len(entries) == 1 or entries[0][0] != entries[1][0] else None
    per_item, per_bank = {}, {}
    for entry in scored:
        per_item.setdefault(entry[1], []).append(entry)
        per_bank.setdefault(entry[2]['id'], []).append(entry)
    found, unclear = {}, {}
    for key, entries in per_item.items():
        best = strict_best(entries)
        if best is None:
            unclear[key] = 'tie'                       # mehrere gleich gute Umsätze
            continue
        other = strict_best(per_bank[best[2]['id']])
        if other is not None and other[1] == key:
            found[key] = best[2]
        else:
            unclear[key] = 'lost' if other is not None else 'tie'   # Umsatz passt besser zu einer anderen Zeile
    # gleichwertige Gruppen: k Zeilen mit gleicher Kontierung, die gleich gut auf dieselben k Umsätze passen
    taken = {bank['id'] for bank in found.values()}
    lookup = {id(item): item for item in items}

    def tops(entries, pick):
        if not entries:
            return None
        best = max(e[0] for e in entries)
        return frozenset(pick(e) for e in entries if e[0] == best)
    item_top = {key: tops([e for e in per_item[key] if e[2]['id'] not in taken], lambda e: e[2]['id']) for key in unclear}
    bank_top = {bid: tops([e for e in entries if e[1] in unclear], lambda e: e[1])
                for bid, entries in per_bank.items() if bid not in taken}
    groups = {}
    for key, ids in item_top.items():
        if ids:
            groups.setdefault((_plan_key(lookup[key]), ids), []).append(key)
    equal = set()
    for (_, ids), keys in groups.items():
        if len(keys) != len(ids) or any(bank_top.get(bid) != frozenset(keys) for bid in ids):
            continue
        banks_by_id = {e[2]['id']: e[2] for e in per_item[keys[0]]}
        ordered_items = sorted(keys, key=lambda k: (lookup[k]['booked'], lookup[k]['sheet'], lookup[k]['row_no']))
        ordered_banks = sorted((banks_by_id[bid] for bid in ids), key=lambda b: (b['booked'], b['id']))
        for key, bank in zip(ordered_items, ordered_banks):
            found[key] = bank
            unclear.pop(key)
            equal.add(key)
    return found, unclear, equal


def statement_spans(banks, statement_files=(), merge_days=10):
    """Zeiträume der importierten Kontoauszüge, zusammengefasst: [(von, bis)].

    Gezählt werden Zeilen mit Auszugsdatei (`import_hash`) und ältere Importe ohne Hash, deren Quelle eine
    importierte Datei ist – nie aus Excel angelegte Umsätze. Lücken bis `merge_days` Tage gelten als durchgehend.
    """
    spans = {}
    for b in banks:
        key = b['import_hash'] or (b['source'] if b['source'] in statement_files else None)
        if key:
            low, high = spans.get(key, (b['booked'], b['booked']))
            spans[key] = (min(low, b['booked']), max(high, b['booked']))
    merged = []
    for low, high in sorted(spans.values()):
        if merged and low <= _shift(merged[-1][1], merge_days):
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def _shift(day, delta):
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()


def preview(store, raw, filename):
    digest = hashlib.sha256(raw).hexdigest()
    if store.db.execute('SELECT 1 FROM imports WHERE hash=?', (digest,)).fetchone():
        raise ValueError('Diese Datei wurde bereits übernommen.')
    bank_state = _bank_state(store)
    rows, sheets, ignored = parse(raw)
    start, before = store.data_start(), {}
    if start:   # Daten vor dem Datenbeginn (z. B. gelöschtes Jahr 2024) werden nicht übernommen
        for r in rows:
            if r['year_sheet'] < int(start[:4]) or (r.get('booked') and r['booked'] < start):
                before[r['sheet']] = before.get(r['sheet'], 0) + 1
        rows = [r for r in rows if not (r['year_sheet'] < int(start[:4]) or (r.get('booked') and r['booked'] < start))]
        sheets = [s for s in sheets if any(r['sheet'] == s for r in rows)]
    banks = store.rows("SELECT * FROM bank WHERE ledger='bank' AND state!='void' ORDER BY id")   # Kassenbelege nie mit der Excel abgleichen
    existing, used = {}, set()
    for b in banks:
        existing.setdefault(b['fingerprint'], []).append(b)
    files = {r[0] for r in store.db.execute('SELECT filename FROM imports')}
    spans = statement_spans(banks, files)
    inside = lambda day: any(low <= day <= high for low, high in spans)                                   # noqa: E731
    pending = []
    for item in rows:
        if item.get('error'):
            item.update(plan='fehler', status='fehler', note=item['error'])
            continue
        item['fingerprint'] = fp = fingerprint(item['booked'], item['amount'], item['name'], item['reference'])
        status, account, kv, title, note = plan_row(item['code'], item['amount'], item['kst'])
        item.update(plan=status, status=status, account=account, kv=kv, title=title, note=note)
        if int(item['booked'][:4]) != item['year_sheet']:
            _note(item, f'Bankdatum {item["booked"][:4]} – Haushaltsjahr laut Blatt: {item["year_sheet"]}.')
        candidates = existing.get(fp) or []
        match = candidates.pop(0) if candidates else None
        if match:
            used.add(match['id'])
            _decide(store, item, match)
        else:
            pending.append(item)                       # unscharf gegen alle freien Umsätze (Auszug und frühere Übernahmen)
    free = [b for b in banks if b['id'] not in used]
    found, unclear, equal = match_loose(pending, free)
    taken = {b['id'] for b in found.values()}
    for item in pending:
        match = found.get(id(item))
        if match:
            _decide(store, item, match)
            how = 'gleichwertig der Reihe nach (gleiche Kontierung)' if id(item) in equal else 'über Betrag und Text'
            when = '' if match['booked'] == item['booked'] else f' am {_de(match["booked"])}'
            _note(item, f'Zugeordnet {how}: Kontoauszug{when} „{(match["name"] or match["reference"] or "")[:40]}“.')
            if id(item) in equal:
                item['equal'] = True
            continue
        nearby = [b for b in free if b['id'] not in taken and b['amount'] == item['amount']
                  and abs((date.fromisoformat(b['booked']) - date.fromisoformat(item['booked'])).days) <= WINDOW_DAYS]
        if unclear.get(id(item)) == 'tie':
            item['status'] = 'fehlt'
            _note(item, 'Mehrere passende Umsätze im Kontoauszug – nicht zugeordnet, wird nicht angelegt.')
        elif unclear.get(id(item)) == 'lost':
            item['status'] = 'fehlt'
            _note(item, 'Der passende Umsatz gehört eindeutiger zu einer anderen Excel-Zeile (doppelte Zeile in der Excel?) – wird nicht angelegt.')
        elif inside(item['booked']):
            item['status'] = 'fehlt'
            _note(item, 'Im Kontoauszug nicht gefunden – wird nicht angelegt. Prüfen, ob der Umsatz im Kontoauszug fehlt.')
        elif nearby:
            item['status'] = 'fehlt'                   # am Rand eines Auszugs bzw. neben früheren Übernahmen: lieber prüfen
            _note(item, f'Möglicherweise schon vorhanden (gleicher Betrag am {_de(nearby[0]["booked"])}) – wird nicht angelegt.')
        else:
            _decide(store, item, None)
    summary = {}
    for item in rows:
        key = str(item['year_sheet'])
        bucket = summary.setdefault(key, {'year': key, 'count': 0, 'cents': 0, **{s: 0 for s in STATUSES}})
        bucket['count'] += 1
        bucket[item['status']] += 1
        bucket['cents'] += item.get('amount') or 0
    notes = [f'Nicht übernommen (kein Umsatzblatt): {", ".join(ignored)}'] if ignored else []
    if before:
        notes.append(f'Vor dem Datenbeginn {_de(start)} nicht übernommen: ' + ', '.join(f'{s} ({n} Zeilen)' for s, n in before.items()))
    dues = sum(1 for item in rows if item.get('account') == '8100' and item['status'] in ('neu', 'zuordnen'))
    if dues:
        notes.append(f'{dues} Zeilen auf 8100 (Mitgliedsbeiträge) werden ohne Mitglied gebucht – danach unter „Buchhaltung → Beitragseingänge“ '
                     'jeder Zahlung ein Mitglied zuordnen (Pflicht; vorher kein KV-Budgetabschluss des Jahres).')
    if spans:
        notes.append('Kontoauszüge vorhanden: ' + ', '.join(f'{_de(a)} – {_de(b)}' for a, b in spans)
                     + '. In diesen Zeiträumen werden nur vorhandene Umsätze verbucht, keine neuen angelegt. '
                       'Fehlende Kontoauszüge vor der Übernahme importieren.')
    return {'hash': digest, 'filename': filename, 'sheets': sheets, 'summary': sorted(summary.values(), key=lambda s: s['year']),
            'rows': rows, 'notes': notes, 'bank_state': bank_state}


def _bank_state(store):
    """Stand der Bankumsätze zum Zeitpunkt der Vorschau – ändert er sich, ist die Vorschau veraltet."""
    return list(store.db.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(SUM(reversals),0), "
                                 "COALESCE(SUM(state='open'),0), COALESCE(SUM(state='booked'),0) FROM bank WHERE ledger='bank'").fetchone())


def _bill_hit(store, item, status):
    """Eindeutig passende erfasste Rechnung für einen zu buchenden Umsatz (wie im Buchungsplatz), sonst None.

    Abbuchung → offene Eingangsrechnung; Eingang → offene Ausgangsrechnung (Nummer im Text, Betrag passt).
    """
    if status != 'neu' and not (status == 'parken' and item.get('code') != PARK):
        return None               # Posten 1370 bewusst geparkt; ohne/unbekannter Posten: Rechnung hat Vorrang (bleibt offen)
    import verbindlichkeiten
    bank = {k: item[k] for k in ('amount', 'name', 'reference', 'booked')}
    if item['amount'] < 0:
        hit = verbindlichkeiten.unique_bill(store, bank)
        return {**hit, 'label': 'Eingangsrechnung'} if hit else None
    found = verbindlichkeiten.invoice_candidates(store, bank)
    return {**found[0], 'label': 'Ausgangsrechnung'} if len(found) == 1 else None


def execute(store, data, actor):
    """Legt alle Zeilen der Vorschau in einer Transaktion an. Gibt Zähler je Status zurück."""
    counts = {status: 0 for status in STATUSES}
    source = data['filename']
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if store.db.execute('SELECT 1 FROM imports WHERE hash=?', (data['hash'],)).fetchone():
            raise ValueError('Diese Datei wurde bereits übernommen.')
        if data.get('bank_state') is not None and _bank_state(store) != list(data['bank_state']):
            raise ValueError('Seit der Vorschau haben sich die Bankumsätze geändert. Bitte die Vorschau neu laden.')
        for item in data['rows']:
            status = item['status']
            counts[status] += 1
            if status in ('vorhanden', 'fehlt', 'fehler'):
                continue
            bid, bank, day = item.get('bank_id'), None, item['booked']
            if bid:
                bank = store.db.execute('SELECT * FROM bank WHERE id=?', (bid,)).fetchone()
                if not bank or bank['state'] != 'open':
                    raise ValueError('Ein Bankumsatz wurde inzwischen verändert. Bitte die Vorschau neu laden.')
                rounds, day = bank['reversals'], bank['booked']          # Journal mit dem Bankdatum wie im Buchungsplatz
            else:
                cur = store.db.execute(
                    'INSERT INTO bank(booked,amount,name,reference,bank_ref,source,row_no,fingerprint) VALUES(?,?,?,?,?,?,?,?)',
                    (item['booked'], item['amount'], item['name'], item['reference'], None,
                     f'{source} · {item["sheet"]}', item['row_no'], item['fingerprint']))  # keine Kontoauszugsdatei → kein import_hash
                bid, rounds = cur.lastrowid, 0
            if item['plan'] == 'offen':
                continue
            if _bill_for(store, item, bank):
                raise ValueError('Eine Abbuchung passt inzwischen zu einer Eingangsrechnung. Bitte die Vorschau neu laden.')
            a = item['amount']
            label = (bank['reference'] or bank['name']) if item.get('equal') and bank is not None else (item['reference'] or item['name'])
            kst = f' · Kostenstelle {item["kst"]}' if item.get('kst') else ''
            if item['plan'] == 'parken':
                code = item.get('code')
                posten = 'ohne Haushaltposten' if not code else f'Posten {code}' + ('' if code == PARK else ' (im Programm nicht bekannt)')
                reason = f'{SOURCE_NOTE}: {posten}{kst}'
                store.journal(day, 'Geparkt: ' + reason, bank_source('bank', {'id': bid, 'reversals': rounds}), [
                    {'account': '1420', 'kv': 'LV', 'debit': max(a, 0), 'credit': max(-a, 0)},
                    {'account': '1370', 'kv': 'LV', 'debit': max(-a, 0), 'credit': max(a, 0), 'title': reason}],
                    year=item['year_sheet'])
                store.db.execute("UPDATE bank SET state='parked' WHERE id=?", (bid,))
                continue
            store.journal(day, f'{label} · {SOURCE_NOTE}{kst}', bank_source('bank', {'id': bid, 'reversals': rounds}), [
                {'account': '1420', 'kv': 'LV', 'debit': max(a, 0), 'credit': max(-a, 0)},
                {'account': item['account'], 'kv': item['kv'], 'debit': max(-a, 0), 'credit': max(a, 0), 'title': item['title']}],
                year=item['year_sheet'])
            store.db.execute("UPDATE bank SET state='booked' WHERE id=?", (bid,))
        store.db.execute('INSERT INTO imports VALUES(?,?,?)', (data['hash'], source, datetime.now().isoformat()))
        skipped = [{'blatt': r['sheet'], 'zeile': r['row_no'], 'datum': r.get('booked'), 'betrag': r.get('amount'), 'posten': r.get('code'),
                    'status': r['status'], 'hinweis': r.get('note') or ''}
                   for r in data['rows'] if r['status'] in ('fehlt', 'fehler') or (r['status'] == 'vorhanden' and r.get('note'))]
        store.audit(actor, 'Vorkontierte Excel übernommen', {'filename': source, 'sheets': data['sheets'], 'counts': counts,
                                                              'sha256': data['hash'], 'uebersprungen': skipped})
    return counts
