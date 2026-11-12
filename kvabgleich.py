"""Abgleich der KV-Budgets mit der Excel-Masterdatei (Blätter „KV <Name> 5100“ … „Schüler Union 5800“).

Je Blatt Jahresblöcke: „Beitrag FFA“ (FFK), „Budget MB JJJJ“ bzw. „Budgetstart JJJJ“ (Beitragsanteil, Schüler Union:
Budget), Kopfzeile Bezeichnung/Kategorie/Datum/Betrag/Empfänger, „EB-Wert“, Bewegungen, „Verfall …“, „SB-Wert“ und
die Schlusssumme. Verglichen werden die Bewegungen der Blöcke ab Beginn der KV-Budgets mit dem Budgetkonto im Programm
(gleicher Bereich und Betrag, Datum gleich oder bis WINDOW Tage abweichend, eindeutig). Die Masterdatei wird mit
derselben Regel nachgerechnet (kvbudget.run): Abweichungen im Stand erklären sich dann aus den nicht gefundenen
Bewegungen, Rechenfehler der Masterdatei (z. B. Verfall mit falschem Vorzeichen) zeigen sich getrennt.

Übernommen werden nur Jahreswerte (FFK, Beitragsanteil, Anfangsbestand im ersten Jahr) und kurze Bezeichnungen mit
Empfänger zu gefundenen Buchungen – nie Buchungen. Von Hand geänderte Bezeichnungen bleiben.
"""
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

import kvbudget
from formate import euro, iso

WINDOW = 5
SHEET = re.compile(r'(5[1-8]00)\s*$')
HEADER = ('Bezeichnung', 'Kategorie', 'Datum', 'Betrag')


def _text(value):
    return ' '.join(str(value).split()) if isinstance(value, str) else ''


def _day(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r'\s*\d{1,2}\.\d{1,2}\.\d{4}\s*', value):
        try:
            return iso(value.strip())
        except ValueError:
            return None
    return None


def _num(value):
    """Zahl aus Excel in Cent; berechnete Zellen (z. B. 1068,9299999) kaufmännisch auf ganze Cent gerundet."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value or value in (float('inf'), float('-inf')):
        return None
    return int((Decimal(repr(float(value))) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


MAX_ROWS, MAX_COLS = 3000, 20          # KV-Blätter sind klein; schützt vor Dateien mit riesigem Blattbereich


def _block():
    return {'ffk': None, 'mb': None, 'mb_year': None, 'rows': [], 'verfall': [], 'eb': None, 'sb': None, 'total': None, 'header': None}


def parse(raw):
    """Masterdatei → {Bereich: {'sheet', 'blocks': [...]}}; Hinweise zu nicht lesbaren Blättern."""
    import openpyxl
    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise ValueError('Excel-Datei (.xlsx) konnte nicht gelesen werden.')
    known, out, notes = dict(kvbudget.areas()), {}, []
    for sheet in book.worksheets:
        m = SHEET.search(sheet.title)
        if not m or m.group(1) not in known:
            continue
        try:
            blocks = _sheet_blocks(sheet)
        except Exception:   # kaputtes Blatt (z. B. fehlerhaftes XML): Hinweis statt Abbruch
            notes.append(f'Blatt „{sheet.title}“ konnte nicht gelesen werden.')
            continue
        if blocks:
            out[m.group(1)] = {'sheet': sheet.title, 'blocks': blocks}
        else:
            notes.append(f'Blatt „{sheet.title}“: keine Jahresblöcke erkannt.')
    if not out:
        raise ValueError('Keine KV-Blätter („KV … 5100“ bis „Schüler Union 5800“) mit Jahresblöcken gefunden.')
    return out, notes


def _sheet_blocks(sheet):
    """Jahresblöcke eines Blatts. Ein Block beginnt mit „Beitrag FFA“ oder „Budget MB“ (Reihenfolge egal) vor seiner Kopfzeile."""
    blocks, cur = [], None
    for row_no, row in enumerate(sheet.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True), start=1):
        cells = list(row)
        texts = [_text(c) for c in cells]
        if all(h in texts for h in HEADER):
            if cur is None or cur['header'] is not None:            # Kopfzeile ohne Vorspann: neuer Block
                cur = _block()
                blocks.append(cur)
            cur['header'] = {h: texts.index(h) for h in HEADER + ('Empfänger',) if h in texts}
            continue
        lead = next(((i, t) for i, t in enumerate(texts[:3]) if t), None)
        value = next((_num(c) for c in cells[lead[0] + 1:lead[0] + 3] if _num(c) is not None), None) if lead else None
        dated = any(isinstance(c, (datetime, date)) for c in cells)   # Bewegungszeilen haben ein Datum, der Vorspann nicht
        ffk = lead and not dated and re.match(r'Beitrag\s+FF[AK]\b', lead[1], re.I)
        mb = lead and not dated and re.match(r'Budget(?:\s*MB|start)?\b\s*(\d{4})?\s*$', lead[1], re.I)
        if ffk or mb:
            key = 'ffk' if ffk else 'mb'
            if cur is None or cur['header'] is not None or cur[key] is not None:   # Vorspann eines neuen Blocks
                cur = _block()
                blocks.append(cur)
            cur[key] = value
            if mb:
                year = re.search(r'(\d{4})', lead[1])
                cur['mb_year'] = int(year.group(1)) if year else None
            continue
        header = cur['header'] if cur else None
        if not header:
            continue
        get = lambda name: cells[header[name]] if name in header and header[name] < len(cells) else None  # noqa: E731
        label, category, day, amount = _text(get('Bezeichnung')), _text(get('Kategorie')), _day(get('Datum')), _num(get('Betrag'))
        if amount is None:
            continue
        if day is None:
            if re.match(r'SB\b', label, re.I):
                cur['sb'] = amount
            elif not label and not category:
                cur['total'] = amount                       # Schlusssumme des Blocks
            continue
        if re.match(r'EB\b', label, re.I) or category.upper().startswith('EB'):
            cur['eb'], cur['eb_day'] = amount, day
        elif re.match(r'SB\b', label, re.I):
            cur['sb'] = amount
        elif label.lower().startswith('verfall'):
            cur['verfall'].append({'day': day, 'amount': amount, 'row': row_no})
        elif amount:
            cur['rows'].append({'day': day, 'amount': amount, 'label': (label or category)[:kvbudget.LABEL_MAX], 'category': category,
                                'recipient': _text(get('Empfänger'))[:60], 'row': row_no})
    for b in blocks:
        b['year'] = int(b['eb_day'][:4]) if b.get('eb_day') else b['mb_year'] or (min(int(r['day'][:4]) for r in b['rows']) if b['rows'] else None)
        b.pop('header', None)
    return [b for b in blocks if b['year']]


def _days(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _match(program, master):
    """Paare (Programm, Masterdatei, sicher). Gefunden = gleicher Betrag, Datum gleich oder bis WINDOW Tage, und zwar für beide Seiten
    der jeweils nächste Treffer. Sicher (Bezeichnung wird übernommen) nur, wenn es auf keiner Seite einen gleich guten zweiten Treffer gibt –
    z. B. zwei Erstattungen über denselben Betrag am selben Tag: gefunden, aber Bezeichnungen und Empfänger bleiben, wie sie sind."""
    cand = {}
    for pi, p in enumerate(program):
        for mi, m in enumerate(master):
            if p['amount'] == m['amount'] and _days(p['day'], m['day']) <= WINDOW:
                cand[(pi, mi)] = _days(p['day'], m['day'])
    pairs, used_p, used_m = [], set(), set()
    for (pi, mi), d in sorted(cand.items(), key=lambda x: (x[1], x[0][1], x[0][0])):
        if pi in used_p or mi in used_m:
            continue
        rivals_p = [x for (q, n), x in cand.items() if n == mi and q != pi and x <= d]   # andere Buchung, mindestens so nah
        rivals_m = [x for (q, n), x in cand.items() if q == pi and n != mi and x <= d]   # andere Masterzeile, mindestens so nah
        pairs.append((pi, mi, not rivals_p and not rivals_m))
        used_p.add(pi)
        used_m.add(mi)
    return pairs, [p for i, p in enumerate(program) if i not in used_p], [m for i, m in enumerate(master) if i not in used_m]


def _differs(own, program, master):
    """Muss die Bezeichnung aus der Masterdatei gespeichert werden? Von Hand geänderte und Umbuchungen bleiben."""
    if program['kind'] == 'umbuchung' or (own and own['source'] == 'manuell'):
        return False
    label, recipient = kvbudget._clean_text(master['label'], kvbudget.LABEL_MAX), kvbudget._clean_text(master['recipient'], 60) or None
    if own:
        return own['label'] != label or (own['recipient'] or None) != recipient
    return program['label'] != label or bool(recipient and recipient != program['recipient'])


def _values_from(store, code, blocks, first):
    """Jahreswerte aus den Blöcken ab dem ersten Jahr bis nächstes Jahr; Anfangsbestand = SB des Vorjahres (sonst EB − FFK − Beitragsanteil).
    Fehlt ein Wert in der Masterdatei, bleibt er im Programm, wie er ist (None) – nie stillschweigend 0. Ungültige Werte: Hinweis."""
    values, notes, by_year = {}, [], {}
    last = date.today().year + 1
    for b in blocks:
        by_year.setdefault(b['year'], b)
    for year, b in by_year.items():
        if year < first or (b['ffk'] is None and b['mb'] is None):
            continue
        if year > last:
            notes.append(f'Block {year}: liegt nach {last} und wird nicht übernommen.')
            continue
        values[year] = {'ffk': b['ffk'], 'dues_share': b['mb']}
        if b['mb'] is None:
            notes.append(f'Block {year}: „Budget MB“ fehlt oder ist leer – der Beitragsanteil im Programm bleibt unverändert.')
        if b['ffk'] is None and code not in kvbudget.NO_SHARE:
            notes.append(f'Block {year}: „Beitrag FFA“ fehlt oder ist leer – der Freundes- und Förderkreis im Programm bleibt unverändert.')
    prev, cur = by_year.get(first - 1), by_year.get(first)
    stated = (prev['sb'] if prev['sb'] is not None else prev['total']) if prev else None
    derived = cur['eb'] - (cur['ffk'] or 0) - (cur['mb'] or 0) if cur and cur['eb'] is not None else None
    opening = stated if stated is not None else derived
    if stated is not None and derived is not None and stated != derived:
        notes.append(f'Anfangsbestand {first}: SB {first - 1} ({euro(stated)}) passt nicht zum EB-Wert {first} '
                     f'({euro(cur["eb"])} − FFK − Beitragsanteil = {euro(derived)}). Übernommen: SB {first - 1}.')
    if opening is not None:
        values.setdefault(first, {'ffk': None, 'dues_share': None})['opening'] = opening
    for year in list(values):
        data = {k: v for k, v in values[year].items() if v is not None}
        if code in kvbudget.NO_SHARE and not data.get('ffk'):
            data.pop('ffk', None)
            values[year]['ffk'] = None
        try:
            kvbudget.check_values(store, year, code, data, cents=True)
        except ValueError as e:
            notes.append(f'Block {year}: {e} Werte dieses Jahres werden nicht übernommen.')
            del values[year]
    return values, by_year, notes


def _rows_from(blocks, first):
    return [r for b in blocks if b['year'] >= first for r in b['rows'] if r['day'] >= f'{first}-01-01']


def preview(store, raw, filename, today=None, parsed=None):
    today = today or date.today().isoformat()
    parsed, notes = parsed or parse(raw)
    first = int(kvbudget.start(store)[:4])
    ctx = kvbudget._context(store)
    earliest = min((b['year'] for a in parsed.values() for b in a['blocks'] if b['year'] >= int((store.data_start() or '2000')[:4])), default=first)
    if earliest < first:
        notes.append(f'Die KV-Budgets beginnen im Programm {first} (Stammdaten), die Masterdatei hat schon Werte für {earliest}. '
                     f'Für einen Abgleich ab {earliest} zuerst in den Stammdaten „KV-Finanzrahmen ab Haushaltsjahr“ auf {earliest} setzen.')
    areas = []
    for code, name in kvbudget.areas():
        sheet = parsed.get(code)
        if not sheet:
            continue
        values, by_year, area_notes = _values_from(store, code, sheet['blocks'], first)
        master_rows = _rows_from(sheet['blocks'], first)
        program = kvbudget.events(store, code, ctx=ctx)
        pairs, program_only, master_only = _match(program, master_rows)
        trial = dict(ctx['values'])
        for year, v in values.items():
            row = dict(trial.get((year, code)) or {'ffk': None, 'dues_share': None, 'opening': None})
            row.update({k: x for k, x in v.items() if x is not None})
            trial[(year, code)] = row
        led = kvbudget.ledger(store, code, today, ctx=ctx, values=trial)
        credits = {y: {'ffk': v['ffk'], 'dues_share': v['dues_share'] or 0, 'fixed': v['dues_share'] is not None, 'budget': code == '5800'}
                   for y, v in values.items()}
        master_events = [{'day': r['day'], 'kind': 'spende' if r['category'].lower() == 'spende' else 'ausgabe' if r['amount'] < 0 else 'gutschrift',
                          'amount': r['amount'], 'label': r['label']} for r in master_rows]
        redo = kvbudget.run(first, values.get(first, {}).get('opening') or 0, credits, master_events, today, lapse=code not in kvbudget.NO_LAPSE)
        lapses = []
        for y in led['years']:
            b = by_year.get(y['year'])
            stated = sum(v['amount'] for v in b['verfall']) if b and b['verfall'] else None
            again = next((x['lapsed'] for x in redo['years'] if x['year'] == y['year']), None)
            if y['lapsed'] or stated or again:
                lapses.append({'year': y['year'], 'program': -y['lapsed'], 'master': -again if again is not None else None, 'stated': stated})
                if stated is not None and again is not None and stated != -again:
                    area_notes.append(f'Masterdatei {y["year"]}: Verfall steht mit {euro(stated)}, nachgerechnet {euro(-again)}'
                                      + (' (Vorzeichen?)' if stated == again and stated else '') + '.')
        unsure = sum(1 for _, _, sure in pairs if not sure)
        if unsure:
            area_notes.append(f'{unsure} Bewegung(en) mit gleichem Betrag nah beieinander: gefunden, aber Bezeichnung und Empfänger werden '
                              'dort nicht übernommen (nicht eindeutig) – bei Bedarf im Budgetkonto von Hand setzen.')
        labels = sum(1 for pi, mi, sure in pairs if sure and _differs(ctx['labels'].get(program[pi]['journal']), program[pi], master_rows[mi]))
        changed = sum(1 for y, v in values.items() for k, x in v.items() if x is not None and (ctx['values'].get((y, code)) or {}).get(k) != x)
        areas.append({'code': code, 'name': name, 'sheet': sheet['sheet'],
                      'values': [{'year': y, **v, 'now': {k: (ctx['values'].get((y, code)) or {}).get(k) for k in ('ffk', 'dues_share', 'opening')}}
                                 for y, v in sorted(values.items())],
                      'values_changed': changed, 'matched': len(pairs), 'unsure': unsure, 'labels': labels,
                      'master_only': [{k: m[k] for k in ('day', 'amount', 'label', 'recipient', 'row')} for m in master_only],
                      'program_only': [{k: p[k] for k in ('day', 'amount', 'label', 'recipient', 'journal')} for p in program_only],
                      'lapses': lapses, 'program_balance': led['available'], 'master_balance': redo['years'][-1]['end'] if redo['years'] else 0,
                      'notes': area_notes})
    return {'filename': filename, 'hash': hashlib.sha256(raw).hexdigest(), 'first': first, 'today': today, 'areas': areas, 'notes': notes}


def execute(store, raw, actor):
    """Jahreswerte und Bezeichnungen übernehmen – neu berechnet aus derselben Datei, alles in einer Transaktion (alles oder nichts)."""
    parsed = parse(raw)
    first = int(kvbudget.start(store)[:4])
    counts = {'jahreswerte': 0, 'bezeichnungen': 0}
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        ctx = kvbudget._context(store)
        for code, _ in kvbudget.areas():
            sheet = parsed[0].get(code)
            if not sheet:
                continue
            values, _, _ = _values_from(store, code, sheet['blocks'], first)
            for year, v in sorted(values.items()):
                fields = {k: x for k, x in v.items() if x is not None}
                if fields and kvbudget.set_values(store, year, code, fields, actor, cents=True, source='Masterdatei', in_transaction=True):
                    counts['jahreswerte'] += 1
            master_rows = _rows_from(sheet['blocks'], first)
            program = kvbudget.events(store, code, ctx=ctx)
            pairs, _, _ = _match(program, master_rows)
            for pi, mi, sure in pairs:
                p, m = program[pi], master_rows[mi]
                if sure and _differs(ctx['labels'].get(p['journal']), p, m):
                    kvbudget.set_label(store, p['journal'], m['label'], m['recipient'], actor, source='Masterdatei', in_transaction=True)
                    counts['bezeichnungen'] += 1
        store.audit(actor, 'KV-Budgets mit Masterdatei abgeglichen', {'datei_hash': hashlib.sha256(raw).hexdigest()[:16], **counts})
    return counts
