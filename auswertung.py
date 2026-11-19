"""Auswertung der Mitgliederdaten (Wunsch Lennert Gust 10.11.2026: „ein Punkt Auswertung, wo ich die Mitgliederdaten auswerten kann“).

Nur lesend. Stichtag (Standard heute) und Auswahl Kreisverband/Ortsverband. Mitglied am Stichtag: eingetreten bis zum Stichtag und nicht
bis dahin ausgetreten (Austrittsdatum = letzter Tag der Mitgliedschaft).

Inhalt: Kennzahlen, Entwicklung je Jahr (Eintritte, Austritte, Bestand), Kreis- und Ortsverbände, Alter (Beitragsstaffel und Altersgrenze),
Mitgliedsdauer, Geschlecht, Vereinigungen, Beitrag und Zahlweg je Kreisverband (Mitgliederbuchhaltung) sowie Listen zum Handeln:
Altersgrenze nach Satzung § 2 Abs. 1 Nr. 4 (Mitgliedschaft endet mit Vollendung des 35. Lebensjahres, Ausnahme Amtsträger nach Abs. 2),
Jubiläen, Geburtstage, neue Mitglieder und Datenlücken. Das PDF enthält nur Zahlen, keine Namen – es kann an Vorstand oder Gremien gehen.
"""
from datetime import date, timedelta

from formate import euro, german_date
from pdfschrift import exclusive

AGE_LIMIT = 35                      # Satzung § 2 Abs. 1 Nr. 4: Mitgliedschaft endet mit Vollendung des 35. Lebensjahres
AGE_GROUPS = ((0, 13, 'unter 14'), (14, 18, '14–18'), (19, 21, '19–21'), (22, 25, '22–25'), (26, 29, '26–29'), (30, 34, '30–34'),
              (35, 200, '35 und älter'))
TENURE_GROUPS = ((0, 0, 'unter 1 Jahr'), (1, 2, '1–2 Jahre'), (3, 5, '3–5 Jahre'), (6, 10, '6–10 Jahre'), (11, 200, 'über 10 Jahre'))
JUBILEES = (5, 10, 15, 20)
LIST_LIMIT = 300
GENDER = {'m': 'männlich', 'w': 'weiblich', 'f': 'weiblich', 'd': 'divers', 'x': 'divers'}


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
    age_values = sorted(ages.values())
    median = (age_values[len(age_values) // 2] if len(age_values) % 2 else (age_values[len(age_values) // 2 - 1] + age_values[len(age_values) // 2]) / 2) if age_values else None
    n = len(active)
    share = (lambda k: round(k / n, 4) if n else 0)
    cards = {'active': n, 'guests': sum(1 for m in active if m['guest']), 'dues_free': sum(1 for m in active if m['dues_free']),
             'joined_year': len(joined_year), 'left_year': len(left_year), 'net_year': len(joined_year) - len(left_year),
             'avg_age': round(sum(age_values) / len(age_values), 1) if age_values else None, 'median_age': median,
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
        soll = sum(book[m['id']]['soll'] or 0 for m in everyone if m['id'] in book)
        paid = sum(book[m['id']]['paid'] for m in everyone if m['id'] in book)
        by_kv.append({'code': code, 'name': name, 'active': len(mine), 'share': share(len(mine)),
                      'avg_age': round(sum(kv_ages) / len(kv_ages), 1) if kv_ages else None,
                      'joined_year': sum(1 for m in joined_year if m['kv'] == code), 'left_year': sum(1 for m in left_year if m['kv'] == code),
                      'guests': sum(1 for m in mine if m['guest']), 'mandates': sum(1 for m in mine if m['id'] in mandates),
                      'soll': soll, 'paid': paid, 'paid_share': round(paid / soll, 4) if soll else None,
                      'arrears': sum(book[m['id']]['arrears'] for m in everyone if m['id'] in book),
                      'arrears_count': sum(1 for m in everyone if m['id'] in book and book[m['id']]['arrears'])})

    by_ov = {}
    for m in active:
        key = m['ov_id'] or 0
        row = by_ov.setdefault(key, {'id': m['ov_id'], 'label': gliederung.label(ovs[m['ov_id']]) if m['ov_id'] in ovs else 'ohne Ortsverband',
                                     'kv': ovs[m['ov_id']]['kv'] if m['ov_id'] in ovs else m['kv'], 'active': 0, 'ages': []})
        row['active'] += 1
        if m['id'] in ages:
            row['ages'].append(ages[m['id']])
    ov_rows = sorted(({**{k: v for k, v in r.items() if k != 'ages'}, 'avg_age': round(sum(r['ages']) / len(r['ages']), 1) if r['ages'] else None}
                      for r in by_ov.values()), key=lambda r: (-r['active'], r['label']))

    genders, clubs, methods = {}, {}, {}
    for m in active:
        g = GENDER.get(str(m['gender'] or '').strip().casefold()[:1], 'ohne Angabe')
        genders[g] = genders.get(g, 0) + 1
        for c in {x.strip().upper() for x in str(m['clubs'] or '').replace(';', ',').split(',') if x.strip()}:
            clubs[c] = clubs.get(c, 0) + 1
        way = 'SEPA-Mandat' if m['id'] in mandates else 'Überweisung'
        methods[way] = methods.get(way, 0) + 1

    lists = _lists(active, pool, ages, tenure, day, mandates)
    return {'stichtag': day.isoformat(), 'year': year, 'kv': kv, 'ov': ov, 'cards': cards, 'development': development, 'kv_rows': by_kv,
            'ov_rows': ov_rows, 'ages': _groups(list(ages.values()), AGE_GROUPS), 'tenure': _groups(list(tenure.values()), TENURE_GROUPS),
            'gender': sorted(({'label': k, 'count': v} for k, v in genders.items()), key=lambda x: -x['count']),
            'clubs': sorted(({'label': k, 'count': v} for k, v in clubs.items()), key=lambda x: (-x['count'], x['label'])),
            'payment': sorted(({'label': k, 'count': v} for k, v in methods.items()), key=lambda x: -x['count']),
            'lists': lists, 'kv_names': names}


def redact(r, amounts=True, names=True):
    """Auswertung für eine Rolle (rollen.py, ab 0.34): ohne Buchhaltung keine Beitragsbeträge, ohne Mitgliederverwaltung
    keine Listen mit Namen. `amounts` in der Antwort sagt der Oberfläche, ob Beträge enthalten sind."""
    r = {**r, 'amounts': bool(amounts)}
    if not amounts:
        r['kv_rows'] = [{**k, 'soll': 0, 'paid': 0, 'paid_share': None, 'arrears': 0, 'arrears_count': 0} for k in r['kv_rows']]
    if not names:
        import rollen
        r['lists'] = rollen.blank(r['lists'])
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


SMALL = 3   # Gruppen unter 3 Personen: im PDF/CSV kein Durchschnittsalter, Merkmale nur als „< 3“ (sonst erkennbar, Prüfbefund 0.29)


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
    if r['kv_rows']:
        story += [p(f"Kreisverbände · Beiträge {r['year']}", 'h2'),
                  berichte._table([['Kreisverband', 'Mitglieder', 'Anteil', 'Ø Alter', 'Eintritte', 'Austritte', 'Beitrag Soll', 'gezahlt', 'Quote']] +
                                  [[k['name'], k['active'], _pct(k['share']), num(_age(k['active'], k['avg_age'])), k['joined_year'], k['left_year'],
                                    *((euro(k['soll']), euro(k['paid']), _pct(k['paid_share'])) if k['active'] >= SMALL and amounts else ('—', '—', '—'))]
                                   for k in r['kv_rows']],
                                  [92, 50, 40, 40, 44, 46, 62, 62, 40], st, numeric=(1, 2, 3, 4, 5, 6, 7, 8), pad=3)]
    if r['ov_rows']:
        story += [p('Ortsverbände', 'h2'), berichte._table([['Ortsverband', 'Kreisverband', 'Mitglieder', 'Ø Alter']] +
                                                          [[o['label'], r['kv_names'].get(o['kv'], o['kv']), o['active'], num(_age(o['active'], o['avg_age']))] for o in r['ov_rows']],
                                                          [190, 120, 60, 60], st, numeric=(2, 3))]
    if c['active'] < SMALL:
        story.append(p(f'Weniger als {SMALL} Mitglieder in der Auswahl: Verteilungen nach Alter, Dauer, Geschlecht, Vereinigungen und Zahlweg '
                       'sind ausgeblendet (sonst wären einzelne Personen erkennbar).', 'body'))
    for title, key in (('Altersgruppen', 'ages'), ('Mitgliedsdauer', 'tenure'), ('Geschlecht', 'gender'), ('Vereinigungen', 'clubs'), ('Zahlweg', 'payment')):
        if r[key] and c['active'] >= SMALL:
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
        p('Mitglied am Stichtag: eingetreten bis zum Stichtag und nicht vorher ausgetreten. Alter in vollendeten Jahren am Stichtag. Beiträge laut '
          'Mitgliederbuchhaltung (Soll nach Rechnung, Festlegung oder Staffel; gezahlt = zugeordnete Zahlungen des Jahres). Enthält keine '
          f'Namen; Gruppen unter {SMALL} Personen ohne Durchschnittsalter und Beitragsbeträge, Geschlecht und Vereinigungen dann nur als „< {SMALL}“.', 'small')]
    return berichte._pdf(story, 'Mitgliederstatistik')


def csv(store, stichtag=None, kv=None, ov=None, amounts=True):
    """Kennzahlen je Kreis- und Ortsverband als CSV (nur Zahlen); `amounts=False`: ohne Beitragsbeträge."""
    import berichte
    r = report(store, stichtag, kv, ov)
    rows = [['Kreisverband', k['name'], '', k['active'], _age(k['active'], k['avg_age']), k['joined_year'], k['left_year'], k['guests'], k['mandates'],
             *((berichte._eur(k['soll']), berichte._eur(k['paid']), berichte._eur(k['arrears'])) if k['active'] >= SMALL and amounts else ('', '', ''))] for k in r['kv_rows']]
    rows += [['Ortsverband', r['kv_names'].get(o['kv'], o['kv']), o['label'], o['active'], _age(o['active'], o['avg_age']), '', '', '', '', '', '', ''] for o in r['ov_rows']]
    return berichte._csv(rows, ['Ebene', 'Kreisverband', 'Ortsverband', f"Mitglieder am {german_date(r['stichtag'])}", 'Ø Alter', f"Eintritte {r['year']}",
                                f"Austritte {r['year']}", 'Gäste', 'SEPA-Mandate', f"Beitrag Soll {r['year']}", f"gezahlt {r['year']}", 'Rückstand'])
