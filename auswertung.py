"""Auswertung der Mitgliederdaten (Wunsch Lennert Gust 10.11.2026: „ein Punkt Auswertung, wo ich die Mitgliederdaten auswerten kann“).

Nur lesend. Stichtag (Standard heute) und Auswahl Kreisverband/Ortsverband. Mitglied am Stichtag: eingetreten bis zum Stichtag und nicht
bis dahin ausgetreten (Austrittsdatum = letzter Tag der Mitgliedschaft).

Inhalt: Kennzahlen, Entwicklung je Jahr (Eintritte, Austritte, Bestand) und Eintritte je Monat, Kreis- und Ortsverbände, Altersstruktur
(Altersgruppen, je Geschlecht, Eintrittsalter), Mitgliedsdauer, Geschlecht (gesamt, je Kreisverband, Eintritte), Vereinigungen, Beitrag und Zahlweg je Kreisverband (Mitgliederbuchhaltung) sowie Listen zum Handeln:
Altersgrenze nach Satzung § 2 Abs. 1 Nr. 4 (Mitgliedschaft endet mit Vollendung des 35. Lebensjahres, Ausnahme Amtsträger nach Abs. 2),
Jubiläen, Geburtstage, neue Mitglieder und Datenlücken. Das PDF enthält nur Zahlen, keine Namen – es kann an Vorstand oder Gremien gehen.
"""
from datetime import date, timedelta

from formate import euro, german_date
from pdfschrift import exclusive

AGE_LIMIT = 35                      # Satzung § 2 Abs. 1 Nr. 4: Mitgliedschaft endet mit Vollendung des 35. Lebensjahres
# Altersgruppen nach Lebensphase (ab 0.38, vorher grob nach Beitragsstaffel): unter 16 = Schüler (Eintritt ab 14), 16–17 = in Hamburg schon
# wahlberechtigt, aber minderjährig, 18–20 und 21–24 = Ausbildung/Studium, 25–29 und 30–34 = Beruf, ab 35 = Altersgrenze der Satzung erreicht.
AGE_GROUPS = ((0, 15, 'unter 16'), (16, 17, '16–17'), (18, 20, '18–20'), (21, 24, '21–24'), (25, 29, '25–29'), (30, 34, '30–34'),
              (35, 200, '35 und älter'))
TENURE_GROUPS = ((0, 0, 'unter 1 Jahr'), (1, 2, '1–2 Jahre'), (3, 5, '3–5 Jahre'), (6, 10, '6–10 Jahre'), (11, 200, 'über 10 Jahre'))
JUBILEES = (5, 10, 15, 20)
LIST_LIMIT = 300
GENDER = {'m': 'männlich', 'w': 'weiblich', 'f': 'weiblich', 'd': 'divers', 'x': 'divers'}
SEXES = (('w', 'weiblich'), ('m', 'männlich'), ('x', 'divers/ohne Angabe'))
MONTHS = ('Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez')
BANDS = ((0, 17, 'unter 18'), (18, 24, '18–24'), (25, 200, 'ab 25'))   # Altersschwerpunkt je Ortsverband: minderjährig / Ausbildung, Studium / Beruf
SMALL = 3   # Gruppen unter 3 Personen: kein Durchschnittsalter, Merkmale im PDF/CSV nur als „< 3“ (sonst erkennbar, Prüfbefund 0.29)


def _years(born, day):
    """Vollendete Jahre am Tag day (Geburtstag am 29.02. in Nicht-Schaltjahren: 01.03.)."""
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def _birthday(born, year):
    try:
        return born.replace(year=year)
    except ValueError:                                        # 29. Februar
        return date(year, 3, 1)


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _active(m, day):
    joined, left = _day(m['joined']), _day(m['left_on'])
    return joined is not None and joined <= day and (left is None or left >= day)


def _who(m):
    return {'id': m['id'], 'number': m['number'], 'name': (f"{m['last_name']}, {m['first_name']}".strip(', ') if m['last_name'] else m['name']),
            'kv': m['kv'], 'ov_id': m['ov_id']}


def _gender(m):
    return GENDER.get(str(m['gender'] or '').strip().casefold()[:1], 'ohne Angabe')


def _sex(m):
    """'w', 'm' oder 'x'. In Kreuztabellen (Alter, Kreisverband, Eintritte) zählen divers und ohne Angabe zusammen:
    „divers“ allein wäre dort fast immer eine einzelne, erkennbare Person."""
    g = _gender(m)
    return g[0] if g in ('männlich', 'weiblich') else 'x'


def _split(members):
    out = {'w': 0, 'm': 0, 'x': 0}
    for m in members:
        out[_sex(m)] += 1
    return out


def _median(values):
    v, k = sorted(values), len(values) // 2
    return (v[k] if len(v) % 2 else (v[k - 1] + v[k]) / 2) if v else None


def _avg(values):
    return round(sum(values) / len(values), 1) if values else None


def _women(w, m, hidden=False):
    """Frauenanteil: weiblich im Verhältnis zu weiblich + männlich. Divers/ohne Angabe steht nicht im Nenner – sonst drückten Datenlücken
    den Anteil, und aus Anteil und Summe ließe sich „divers“ zurückrechnen. Kein Anteil unter SMALL Personen; `hidden` (PDF/CSV):
    auch dann keiner, wenn eine der beiden Zahlen nur als „< 3“ erscheint."""
    if w is None or m is None or w + m < SMALL or (hidden and (0 < w < SMALL or 0 < m < SMALL)):
        return None
    return round(w / (w + m), 4)


def _bands(counts, hidden=False):
    """Anteile der Altersbänder BANDS an den Mitgliedern mit Geburtsdatum; `hidden`: kein Wert, der auf weniger als SMALL Personen beruht."""
    total = sum(counts)
    return [None if not total or (hidden and 0 < n < SMALL) else round(n / total, 4) for n in counts]


def _groups(values, groups):
    return [{'label': label, 'count': sum(1 for v in values if lo <= v <= hi)} for lo, hi, label in groups]


def _filter(store, kv, ov):
    from core import KVS
    if kv and (kv not in dict(KVS) or kv == 'LV'):
        raise ValueError('Unbekannter Kreisverband.')
    if ov not in (None, '', 'none'):
        try:
            ov = int(ov)
        except (TypeError, ValueError):
            raise ValueError('Unbekannter Ortsverband.') from None
        row = store.db.execute('SELECT kv FROM ortsverbaende WHERE id=?', (ov,)).fetchone()
        if not row or (kv and row['kv'] != kv):
            raise ValueError('Der Ortsverband gehört nicht zum gewählten Kreisverband.')
    return kv or None, (ov if ov not in ('',) else None)


def report(store, stichtag=None, kv=None, ov=None, today=None):
    """Auswertung zum Stichtag (JJJJ-MM-TT); kv: Kostenstelle, ov: id oder 'none' (ohne Ortsverband)."""
    import gliederung
    import mitgliederkonto
    from core import KVS
    today = _day(today) or date.today()
    day = _day(stichtag) if stichtag else today
    if not day or not date(2000, 1, 1) <= day <= today + timedelta(days=366):
        raise ValueError('Stichtag ungültig.')
    kv, ov = _filter(store, kv, ov)
    names = dict(KVS)
    ovs = {o['id']: o for o in gliederung.ovs(store)}
    every = store.rows('SELECT id,number,first_name,last_name,name,kv,ov_id,birth,joined,left_on,gender,clubs,email,phone,mobile,zip,city,'
                       'guest,dues_free,no_mail,payment_method FROM members')

    def selected(m):
        return (not kv or m['kv'] == kv) and (ov is None or (ov == 'none' and not m['ov_id']) or m['ov_id'] == ov)
    pool = [m for m in every if selected(m)]
    active = [m for m in pool if _active(m, day)]
    year = day.year
    ages = {m['id']: _years(_day(m['birth']), day) for m in active if _day(m['birth'])}
    tenure = {m['id']: _years(_day(m['joined']), day) for m in active}
    joined_year = [m for m in pool if _day(m['joined']) and _day(m['joined']).year == year and _day(m['joined']) <= day]
    left_year = [m for m in pool if _day(m['left_on']) and _day(m['left_on']).year == year and _day(m['left_on']) <= day]
    mandates = {r[0] for r in store.db.execute("SELECT member_id FROM mandates WHERE status='active'")}
    n = len(active)
    share = (lambda k: round(k / n, 4) if n else 0)
    sex = {m['id']: _sex(m) for m in active}
    split = _split(active)
    cards = {'active': n, 'guests': sum(1 for m in active if m['guest']), 'dues_free': sum(1 for m in active if m['dues_free']),
             'joined_year': len(joined_year), 'left_year': len(left_year), 'net_year': len(joined_year) - len(left_year),
             'avg_age': _avg(ages.values()), 'median_age': _median(ages.values()), 'no_birth': n - len(ages),
             'women': split['w'], 'men': split['m'], 'women_share': _women(split['w'], split['m']),
             'joined_women_share': _women(*[_split(joined_year)[k] for k in 'wm']),
             'with_email': share(sum(1 for m in active if m['email'])), 'with_mandate': share(sum(1 for m in active if m['id'] in mandates)),
             'with_ov': share(sum(1 for m in active if m['ov_id']))}

    # Entwicklung: höchstens die letzten 10 Jahre bis zum Stichtag; Bestand am 31.12. bzw. am Stichtag
    first = min((_day(m['joined']).year for m in pool if _day(m['joined'])), default=year)
    development = []
    for y in range(max(first, year - 9), year + 1):
        end = min(date(y, 12, 31), day)
        development.append({'year': y, 'joined': sum(1 for m in pool if _day(m['joined']) and _day(m['joined']).year == y and _day(m['joined']) <= end),
                            'left': sum(1 for m in pool if _day(m['left_on']) and _day(m['left_on']).year == y and _day(m['left_on']) <= end),
                            'end': sum(1 for m in pool if _active(m, end)), 'until': end.isoformat()})

    # Beitrag je Kreisverband (Mitgliederbuchhaltung: Soll und gezahlt im Jahr des Stichtags, Rückstand Stand Stichtag bzw. heute)
    book = mitgliederkonto.overview(store, year, min(day, today).isoformat())
    by_kv = []
    for code, name in KVS:
        if code == 'LV' or (kv and code != kv):
            continue
        mine = [m for m in active if m['kv'] == code]
        everyone = [m for m in pool if m['kv'] == code]
        if not mine and not everyone:
            continue
        kv_ages = [ages[m['id']] for m in mine if m['id'] in ages]
        kv_split = _split(mine)
        joined_kv, left_kv = sum(1 for m in joined_year if m['kv'] == code), sum(1 for m in left_year if m['kv'] == code)
        soll = sum(book[m['id']]['soll'] or 0 for m in everyone if m['id'] in book)
        paid = sum(book[m['id']]['paid'] for m in everyone if m['id'] in book)
        by_kv.append({'code': code, 'name': name, 'active': len(mine), 'share': share(len(mine)),
                      'avg_age': _avg(kv_ages), **kv_split,
                      'women_share': _women(kv_split['w'], kv_split['m']),
                      'joined_year': joined_kv, 'left_year': left_kv, 'net_year': joined_kv - left_kv,
                      'guests': sum(1 for m in mine if m['guest']), 'mandates': sum(1 for m in mine if m['id'] in mandates),
                      'soll': soll, 'paid': paid, 'paid_share': round(paid / soll, 4) if soll else None,
                      'arrears': sum(book[m['id']]['arrears'] for m in everyone if m['id'] in book),
                      'arrears_count': sum(1 for m in everyone if m['id'] in book and book[m['id']]['arrears'])})

    by_ov = {}
    for m in active:
        key = m['ov_id'] or ('ohne', m['kv'])   # ohne Ortsverband: eine Zeile je Kreisverband (sonst stünde der Kreisverband des ersten Mitglieds da)
        row = by_ov.setdefault(key, {'id': m['ov_id'], 'label': gliederung.label(ovs[m['ov_id']]) if m['ov_id'] in ovs else 'ohne Ortsverband',
                                     'kv': ovs[m['ov_id']]['kv'] if m['ov_id'] in ovs else m['kv'], 'active': 0, 'ages': [], 'w': 0, 'm': 0, 'x': 0})
        row['active'] += 1
        if m['id'] in ages:
            row['ages'].append(ages[m['id']])
        row[sex[m['id']]] += 1
    ov_rows = []
    for row in sorted(by_ov.values(), key=lambda r: (-r['active'], r['label'])):
        row_ages = row.pop('ages')
        band_n = [sum(1 for a in row_ages if lo <= a <= hi) for lo, hi, _ in BANDS]
        ov_rows.append({**row, 'avg_age': _avg(row_ages), 'women_share': _women(row['w'], row['m']), 'band_n': band_n, 'bands': _bands(band_n)})

    genders, clubs, methods = {}, {}, {}
    for m in active:
        g = _gender(m)
        genders[g] = genders.get(g, 0) + 1
        for c in {x.strip().upper() for x in str(m['clubs'] or '').replace(';', ',').split(',') if x.strip()}:
            clubs[c] = clubs.get(c, 0) + 1
        way = 'SEPA-Mandat' if m['id'] in mandates else 'Überweisung'
        methods[way] = methods.get(way, 0) + 1

    # Altersstruktur: je Altersgruppe gesamt sowie weiblich/männlich (Alterspyramide); Alter je Geschlecht erst ab SMALL Personen
    age_rows = [{'label': label, 'count': sum(1 for a in ages.values() if lo <= a <= hi),
                 **{k: sum(1 for i, a in ages.items() if lo <= a <= hi and sex[i] == k) for k in 'wm'}} for lo, hi, label in AGE_GROUPS]
    age_stats = []
    for key, label in SEXES:
        mine = [ages[i] for i in ages if sex[i] == key]
        age_stats.append({'key': key, 'label': label, 'count': split[key], 'avg_age': _age(len(mine), _avg(mine)), 'median_age': _age(len(mine), _median(mine))})
    entry = [_years(_day(m['birth']), _day(m['joined'])) for m in active if _day(m['birth'])]
    months = [sum(1 for m in joined_year if _day(m['joined']).month == k) for k in range(1, day.month + 1)]
    months_left = [sum(1 for m in left_year if _day(m['left_on']).month == k) for k in range(1, day.month + 1)]

    lists = _lists(active, pool, ages, tenure, day, mandates)
    return {'stichtag': day.isoformat(), 'year': year, 'kv': kv, 'ov': ov, 'cards': cards, 'development': development, 'kv_rows': by_kv,
            'ov_rows': ov_rows, 'ages': age_rows, 'age_stats': age_stats, 'entry_ages': _groups(entry, AGE_GROUPS),
            'tenure': _groups(list(tenure.values()), TENURE_GROUPS), 'months': months, 'months_left': months_left, 'joined_gender': _split(joined_year),
            'gender': sorted(({'label': k, 'count': v} for k, v in genders.items()), key=lambda x: -x['count']),
            'clubs': sorted(({'label': k, 'count': v} for k, v in clubs.items()), key=lambda x: (-x['count'], x['label'])),
            'payment': sorted(({'label': k, 'count': v} for k, v in methods.items()), key=lambda x: -x['count']),
            'lists': lists, 'kv_names': names}


def redact(r, amounts=True, names=True):
    """Auswertung für eine Rolle (rollen.py, ab 0.34): ohne Buchhaltung keine Beitragsbeträge, ohne Mitgliederverwaltung
    keine Listen mit Namen und in den Kreuztabellen nach Geschlecht (Alter, Kreis- und Ortsverband, Eintritte) sowie beim Alter je
    Ortsverband keine Werte, die auf weniger als SMALL Personen beruhen (None = „< 3“). `amounts` in der Antwort sagt der Oberfläche, ob Beträge enthalten sind."""
    r = {**r, 'amounts': bool(amounts)}
    if not amounts:
        r['kv_rows'] = [{**k, 'soll': 0, 'paid': 0, 'paid_share': None, 'arrears': 0, 'arrears_count': 0} for k in r['kv_rows']]
    if not names:
        import rollen
        r['lists'] = rollen.blank(r['lists'])
        veil = lambda row, keys: {**row, **{k: None for k in keys if 0 < (row[k] or 0) < SMALL}}
        r['ages'] = [veil(a, 'wm') for a in r['ages']]
        r['joined_gender'] = veil(r['joined_gender'], 'wmx')
        r['cards'] = {**r['cards'], 'joined_women_share': _women(r['joined_gender']['w'], r['joined_gender']['m'])}
        r['kv_rows'] = [{**k, 'women_share': _women(k['w'], k['m'])} for k in (veil(k, 'wmx') for k in r['kv_rows'])]
        r['ov_rows'] = [{**o, 'women_share': _women(o['w'], o['m']), 'avg_age': _age(o['active'], o['avg_age']), 'bands': _bands(n, True),
                         'band_n': [None if 0 < k < SMALL else k for k in n]} for o, n in ((veil(o, 'wmx'), o['band_n']) for o in r['ov_rows'])]
    return r


def _lists(active, pool, ages, tenure, day, mandates):
    """Listen zum Handeln (je höchstens LIST_LIMIT Einträge, sortiert nach Dringlichkeit)."""
    limit_rows, jubilees, birthdays, new = [], [], [], []
    for m in active:
        born, joined = _day(m['birth']), _day(m['joined'])
        if born:
            end = _birthday(born, born.year + AGE_LIMIT)          # Vollendung des 35. Lebensjahres
            if end <= day + timedelta(days=365):
                limit_rows.append({**_who(m), 'on': end.isoformat(), 'reached': end <= day})
            nxt = _birthday(born, day.year)
            if nxt < day:
                nxt = _birthday(born, day.year + 1)
            if (nxt - day).days <= 30:
                birthdays.append({**_who(m), 'on': nxt.isoformat(), 'age': nxt.year - born.year})
        if joined:
            years = day.year - joined.year
            if years in JUBILEES:
                jubilees.append({**_who(m), 'on': _birthday(joined, day.year).isoformat(), 'years': years})
            if 0 <= (day - joined).days <= 90:
                new.append({**_who(m), 'on': joined.isoformat()})
    gaps = {'no_email': [_who(m) for m in active if not m['email']],
            'no_ov': [_who(m) for m in active if not m['ov_id']],
            'no_phone': [_who(m) for m in active if not m['phone'] and not m['mobile']],
            'no_zip': [_who(m) for m in active if not m['zip']],
            'no_mail': [_who(m) for m in active if m['no_mail']]}
    key = lambda r: (r['on'], r['name'])
    return {'age_limit': sorted(limit_rows, key=key)[:LIST_LIMIT], 'jubilees': sorted(jubilees, key=lambda r: (-r['years'], r['on'], r['name']))[:LIST_LIMIT],
            'birthdays': sorted(birthdays, key=key)[:LIST_LIMIT], 'new': sorted(new, key=key, reverse=True)[:LIST_LIMIT],
            'gaps': {k: {'count': len(v), 'items': sorted(v, key=lambda r: r['name'])[:LIST_LIMIT]} for k, v in gaps.items()}}


# ---------------------------------------------------------------- PDF und CSV (nur Zahlen)

def _scope(store, r):
    import gliederung
    parts = [r['kv_names'].get(r['kv'], r['kv'])] if r['kv'] else ['alle Kreisverbände']
    if r['ov'] == 'none':
        parts.append('ohne Ortsverband')
    elif r['ov']:
        o = next((x for x in gliederung.ovs(store) if x['id'] == r['ov']), None)
        parts.append(f"OV {gliederung.label(o)}" if o else '')
    return ' · '.join(p for p in parts if p)


def _pct(v):
    return '—' if v is None else f'{v * 100:.0f} %'


def _age(n, value):
    return None if n < SMALL else value


def _few(count):
    return f'< {SMALL}' if 0 < count < SMALL else count


@exclusive
def pdf(store, stichtag=None, kv=None, ov=None, amounts=True):
    """Mitgliederstatistik als PDF – nur Zahlen, keine Namen (für Vorstand und Gremien); `amounts=False`: ohne Beitragsbeträge."""
    import berichte
    r = report(store, stichtag, kv, ov)
    st, cfg, c = berichte._styles(), store.config(), r['cards']
    p = berichte._para(st)
    num = lambda v: '—' if v is None else f'{v}'.replace('.', ',')
    story = [p('Mitgliederstatistik', 'title'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · {_scope(store, r)} · Stichtag {german_date(r['stichtag'])}", 'small'),
             berichte._table([['Mitglieder', f"Eintritte {r['year']}", f"Austritte {r['year']}", 'Ø Alter', 'Gäste', 'mit E-Mail', 'mit SEPA-Mandat'],
                              [c['active'], c['joined_year'], c['left_year'], num(_age(c['active'], c['avg_age'])), c['guests'], _pct(c['with_email']), _pct(c['with_mandate'])]],
                             [62, 62, 62, 52, 45, 62, 75], st, numeric=(0, 1, 2, 3, 4, 5, 6)),
             p('Entwicklung', 'h2'),
             berichte._table([['Jahr', 'Eintritte', 'Austritte', 'Bestand']] + [[d['year'], d['joined'], d['left'], f"{d['end']} (Stand {german_date(d['until'])})"]
                                                                                for d in r['development']], [60, 70, 70, 160], st, numeric=(1, 2))]
    shown = c['active'] >= SMALL
    sexes = lambda row: [_few(row['w']), _few(row['m']), _few(row['count'] - row['w'] - row['m'])]   # weiblich, männlich, divers/ohne Angabe
    if len(r['months']) > 1:
        story += [p(f"Ein- und Austritte {r['year']} je Monat", 'h2'),
                  berichte._table([['', *MONTHS[:len(r['months'])]], ['Eintritte', *r['months']], ['Austritte', *r['months_left']]],
                                  [60] + [36] * len(r['months']), st, numeric=tuple(range(1, 13)))]
    if r['kv_rows']:
        story += [p(f"Kreisverbände {r['year']}", 'h2'),
                  berichte._table([['Kreisverband', 'Mitglieder', 'Anteil', 'Ø Alter', 'Eintritte', 'Austritte', 'Saldo', 'Beitrag Soll', 'gezahlt', 'Quote']] +
                                  [[k['name'], k['active'], _pct(k['share']), num(_age(k['active'], k['avg_age'])), k['joined_year'], k['left_year'], f"{k['net_year']:+d}",
                                    *((euro(k['soll']), euro(k['paid']), _pct(k['paid_share'])) if k['active'] >= SMALL and amounts else ('—', '—', '—'))]
                                   for k in r['kv_rows']],
                                  [88, 48, 38, 38, 42, 44, 36, 62, 62, 40], st, numeric=tuple(range(1, 10)), pad=3)]
    if r['ov_rows']:
        band = lambda n, part: _few(n) if 0 < n < SMALL else _pct(part)
        story += [p('Ortsverbände', 'h2'),
                  berichte._table([['Ortsverband', 'Kreisverband', 'Mitglieder', 'Frauenanteil', 'Ø Alter', *(b[2] for b in BANDS)]] +
                                  [[o['label'], r['kv_names'].get(o['kv'], o['kv']), o['active'], _pct(_women(o['w'], o['m'], True)), num(_age(o['active'], o['avg_age'])),
                                    *map(band, o['band_n'], o['bands'])] for o in r['ov_rows']],
                                  [138, 100, 50, 56, 40, 40, 40, 40], st, numeric=(2, 3, 4, 5, 6, 7), pad=3)]
    if shown:
        story += [p('Geschlecht je Kreisverband und bei den Eintritten', 'h2'),
                  berichte._table([['', 'gesamt', 'weiblich', 'männlich', 'divers/ohne Angabe', 'Frauenanteil']] +
                                  [[label, row['count'], *sexes(row), _pct(_women(row['w'], row['m'], True))]
                                   for label, row in [(k['name'], {**k, 'count': k['active']}) for k in r['kv_rows']] +
                                   [(f"Eintritte {r['year']}", {**r['joined_gender'], 'count': c['joined_year']})]],
                                  [150, 55, 55, 55, 95, 66], st, numeric=(1, 2, 3, 4, 5)),
                  p('Altersstruktur', 'h2'),
                  berichte._table([['Altersgruppe', 'Mitglieder', 'Anteil', 'weiblich', 'männlich', 'divers/ohne Angabe']] +
                                  [[a['label'], a['count'], _pct(a['count'] / c['active']), *sexes(a)] for a in r['ages']],
                                  [150, 55, 55, 55, 55, 95], st, numeric=(1, 2, 3, 4, 5)),
                  p('Alter nach Geschlecht', 'h2'),
                  berichte._table([['', 'Mitglieder', 'Ø Alter', 'Median']] +
                                  [[a['label'], _few(a['count']), num(a['avg_age']), num(a['median_age'])] for a in r['age_stats']] +
                                  [['gesamt', c['active'], num(c['avg_age']), num(c['median_age'])]], [150, 55, 55, 55], st, numeric=(1, 2, 3))]
    if c['active'] < SMALL:
        story.append(p(f'Weniger als {SMALL} Mitglieder in der Auswahl: Verteilungen nach Alter, Eintrittsalter, Dauer, Geschlecht, Vereinigungen und Zahlweg '
                       'sind ausgeblendet (sonst wären einzelne Personen erkennbar).', 'body'))
    for title, key in (('Eintrittsalter (Alter beim Eintritt)', 'entry_ages'), ('Mitgliedsdauer', 'tenure'), ('Geschlecht', 'gender'), ('Vereinigungen', 'clubs'), ('Zahlweg', 'payment')):
        if r[key] and shown:
            small = key in ('gender', 'clubs')                # Merkmale einzelner Personen
            story += [p(title, 'h2'), berichte._table([['', 'Mitglieder', 'Anteil']] + [
                [x['label'], _few(x['count']) if small else x['count'], '' if small and 0 < x['count'] < SMALL else _pct(x['count'] / c['active'] if c['active'] else 0)]
                for x in r[key]], [190, 60, 60], st, numeric=(1, 2))]
    lists = r['lists']
    story += [p('Hinweise', 'h2'), berichte._table([['', 'Anzahl']] + [
        [f'Altersgrenze (Satzung § 2 Abs. 1 Nr. 4) erreicht', sum(1 for x in lists['age_limit'] if x['reached'])],
        ['Altersgrenze in den nächsten 12 Monaten', sum(1 for x in lists['age_limit'] if not x['reached'])],
        [f"Jubiläen {r['year']} ({', '.join(str(j) for j in JUBILEES)} Jahre)", len(lists['jubilees'])],
        ['ohne E-Mail-Adresse', lists['gaps']['no_email']['count']], ['ohne Ortsverband', lists['gaps']['no_ov']['count']],
        ['ohne Telefon/Handy', lists['gaps']['no_phone']['count']], ['Postausschluss', lists['gaps']['no_mail']['count']]], [250, 60], st, numeric=(1,)),
        p('Mitglied am Stichtag: eingetreten bis zum Stichtag und nicht vorher ausgetreten. Alter in vollendeten Jahren am Stichtag. Frauenanteil: weiblich im Verhältnis zu weiblich + männlich '
          f'(divers/ohne Angabe zählt nicht mit; kein Anteil unter {SMALL} Personen). Unter 18 / 18–24 / ab 25: Anteil an den Mitgliedern des Ortsverbands. Beiträge laut '
          'Mitgliederbuchhaltung (Soll nach Rechnung, Festlegung oder Staffel; gezahlt = zugeordnete Zahlungen des Jahres). Enthält keine '
          f'Namen; Gruppen unter {SMALL} Personen ohne Durchschnittsalter und Beitragsbeträge, Geschlecht und Vereinigungen dann nur als „< {SMALL}“ '
          '(auch je Altersgruppe, Kreis- und Ortsverband).', 'small')]
    return berichte._pdf(story, 'Mitgliederstatistik')


def csv(store, stichtag=None, kv=None, ov=None, amounts=True):
    """Kennzahlen je Kreis- und Ortsverband, Altersgruppen, Eintrittsalter, Geschlecht, Ein- und Austritte je Monat als CSV (nur Zahlen);
    `amounts=False`: ohne Beitragsbeträge."""
    import berichte
    r = report(store, stichtag, kv, ov)
    c, shown = r['cards'], r['cards']['active'] >= SMALL
    sexes = lambda w, m, x: [_few(w), _few(m), _few(x)]
    part = lambda v: None if v is None else int(v * 1000 + 0.5) / 10             # Anteil in Prozent, gerundet wie in der Oberfläche
    women = lambda w, m: part(_women(w, m, True))
    row = lambda *cells: [*cells, *[''] * (20 - len(cells))]
    rows = [row('Kreisverband', k['name'], '', k['active'], _age(k['active'], k['avg_age']), k['joined_year'], k['left_year'], k['guests'], k['mandates'],
                *((berichte._eur(k['soll']), berichte._eur(k['paid']), berichte._eur(k['arrears'])) if k['active'] >= SMALL and amounts else ('', '', '')),
                k['net_year'], *(sexes(k['w'], k['m'], k['x']) + [women(k['w'], k['m'])] if shown else ())) for k in r['kv_rows']]
    rows += [row('Ortsverband', r['kv_names'].get(o['kv'], o['kv']), o['label'], o['active'], _age(o['active'], o['avg_age']), '', '', '', '', '', '', '', '',
                 *sexes(o['w'], o['m'], o['x']), women(o['w'], o['m']), *(_few(n) if 0 < n < SMALL else part(v) for n, v in zip(o['band_n'], o['bands'])))
             for o in r['ov_rows']]
    rows += [row(f"Monat {r['year']}-{i + 1:02d}", '', '', '', '', n, r['months_left'][i]) for i, n in enumerate(r['months'])]
    if shown:                                                                      # wie im PDF: unter SMALL Mitgliedern keine Verteilungen
        j = r['joined_gender']
        rows += [row('Eintritte nach Geschlecht', '', '', '', '', c['joined_year'], '', '', '', '', '', '', '', *sexes(j['w'], j['m'], j['x']), women(j['w'], j['m']))]
        rows += [row('Altersgruppe', a['label'], '', a['count'], '', '', '', '', '', '', '', '', '', *sexes(a['w'], a['m'], a['count'] - a['w'] - a['m'])) for a in r['ages']]
        rows += [row('Alter nach Geschlecht', a['label'], '', _few(a['count']), a['avg_age']) for a in r['age_stats']]
        rows += [row('Eintrittsalter', a['label'], '', a['count']) for a in r['entry_ages']]
    rows = [[str(c).replace('.', ',') if isinstance(c, float) else c for c in row] for row in rows]   # Dezimalkomma: Excel läse „24.5“ als Datum
    return berichte._csv(rows, ['Ebene', 'Kreisverband/Gruppe', 'Ortsverband', f"Mitglieder am {german_date(r['stichtag'])}", 'Ø Alter', f"Eintritte {r['year']}",
                                f"Austritte {r['year']}", 'Gäste', 'SEPA-Mandate', f"Beitrag Soll {r['year']}", f"gezahlt {r['year']}", 'Rückstand',
                                f"Saldo {r['year']}", 'weiblich', 'männlich', 'divers/ohne Angabe',
                                'Frauenanteil % (weiblich : weiblich + männlich)', *(f'{b[2]} %' for b in BANDS)])
