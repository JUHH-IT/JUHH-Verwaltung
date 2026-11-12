"""OP-Listen (offene Posten) der Mitgliedsbeiträge je Kreisverband – Wunsch Lennert Gust 10.11.2026: Die Kreisverbände sollen sehen,
bei welchen Mitgliedern was offen ist.

Nur lesend. Grundlage ist das Beitragskonto (mitgliederkonto._account): fälliges Soll je Beitragsjahr minus Zahlungen, die
Zahlungen decken die ältesten fälligen Beiträge zuerst – dieselben Zahlen wie Beitragskonto, Rückstandsliste und Monatsbericht.
Kreisverband und Ortsverband = heutige Zuordnung des Mitglieds. Die Listen enthalten Kontaktdaten und sind vertraulich
(nur für die Mitgliederbetreuung des Kreisverbands).
"""
import io
import re
import zipfile
from datetime import date

import berichte
from formate import euro, german_date
from pdfschrift import exclusive

NOTE = ('Offen = fälliger Beitrag (Satzung § 49) minus Zahlungen seit Datenbeginn; Zahlungen decken die ältesten Beiträge zuerst. '
        'Kreis- und Ortsverband = heutige Zuordnung des Mitglieds. „Lastschrift“: gültiges SEPA-Mandat – der Beitrag gilt nach § 49 Abs. 5 '
        'als gezahlt, solange der Einzug nicht scheitert. Stand der Buchhaltung – nur so aktuell wie der zuletzt importierte Kontoauszug.')
CONFIDENTIAL = ('Vertraulich: enthält personenbezogene Daten – nur für die Mitgliederbetreuung des Kreisverbands, nicht weitergeben, '
                'nach Gebrauch löschen.')


def _names(store):
    from core import KVS
    return dict(KVS)


def items(store, kv=None, ov=None, today=None):
    """Offene Posten je Mitglied: offene Beitragsjahre (Jahr, Grundlage, Soll, gezahlt, offen, fällig seit), Erinnerungen, Lastschrift.
    kv: Kreisverband (Code) oder None = alle; ov: Ortsverband-ID, 'none' = ohne Ortsverband, None = alle."""
    import gliederung
    import mitgliederkonto
    names = _names(store)
    if kv and kv not in names:
        raise ValueError('Unbekannter Kreisverband.')
    ov_id = None
    if ov not in (None, ''):
        if ov != 'none':
            try:
                ov_id = int(ov)
            except (TypeError, ValueError):
                raise ValueError('Unbekannter Ortsverband.') from None
            row = store.db.execute('SELECT kv FROM ortsverbaende WHERE id=?', (ov_id,)).fetchone()
            if not row or (kv and row['kv'] != kv):
                raise ValueError('Der Ortsverband gehört nicht zum gewählten Kreisverband.')
    today = today or date.today().isoformat()
    ctx = mitgliederkonto._context(store, today)
    ovs = {o['id']: gliederung.label(o) for o in gliederung.ovs(store)}
    mandates = {r[0] for r in store.db.execute("SELECT member_id FROM mandates WHERE status='active'")}
    reminded = {}
    for r in store.rows("SELECT i.member_id,i.year,COUNT(*) n,MAX(ml.time) last FROM mailings ml JOIN invoices i ON i.id=ml.invoice_id "
                        "WHERE ml.kind='reminder' AND ml.status IN ('sent','unknown') AND i.member_id IS NOT NULL GROUP BY i.member_id,i.year"):
        reminded[(r['member_id'], r['year'])] = {'count': r['n'], 'last': str(r['last'])[:10]}
    out, unclear = [], []
    for m in store.rows("SELECT * FROM members ORDER BY kv,COALESCE(NULLIF(last_name,''),name) COLLATE NOCASE,first_name COLLATE NOCASE,number"):
        if (kv and m['kv'] != kv) or (ov == 'none' and m['ov_id']) or (ov_id and m['ov_id'] != ov_id):
            continue
        acc = mitgliederkonto._account(store, m, ctx)
        if acc['issues']:                              # Soll nicht berechenbar (z. B. Austrittsjahr): klärt die Landesgeschäftsstelle
            unclear.append({'id': m['id'], 'number': m['number'], 'name': m['name'], 'first_name': m['first_name'], 'last_name': m['last_name'],
                            'kv': m['kv'], 'issues': acc['issues']})
        years = [{'year': y['year'], 'basis': y['basis'], 'soll': y['soll'], 'paid': y['paid'], 'open': y['open'], 'due': y['due'],
                  'reminders': reminded.get((m['id'], y['year']), {}).get('count', 0), 'last_reminder': reminded.get((m['id'], y['year']), {}).get('last')}
                 for y in acc['years'] if y['open'] and y['open'] > 0]
        if not years:
            continue
        out.append({'id': m['id'], 'number': m['number'], 'name': m['name'], 'first_name': m['first_name'], 'last_name': m['last_name'],
                    'kv': m['kv'], 'kv_name': names.get(m['kv'], m['kv']), 'ov': ovs.get(m['ov_id'], ''), 'email': m['email'] or m['email_work'] or '',
                    'phone': m['mobile'] or m['phone'] or m['mobile_work'] or m['phone_work'] or '', 'left_on': m['left_on'] or '',
                    'mandate': m['id'] in mandates, 'external_balance': m['external_balance'] or '', 'last_payment': acc['last_payment'],
                    'years': years, 'open': sum(y['open'] for y in years)})
    by_year, by_kv = {}, {}
    for x in out:
        for y in x['years']:
            by_year[y['year']] = by_year.get(y['year'], 0) + y['open']
        k = by_kv.setdefault(x['kv'], {'code': x['kv'], 'name': x['kv_name'], 'count': 0, 'open': 0})
        k['count'] += 1
        k['open'] += x['open']
    return {'today': today, 'kv': kv, 'kv_name': names.get(kv, '') if kv else 'alle Kreisverbände', 'ov': ov or '',
            'ov_name': 'ohne Ortsverband' if ov == 'none' else ovs.get(ov_id, ''), 'members': out, 'count': len(out),
            'total': sum(x['open'] for x in out), 'by_year': [{'year': y, 'open': v} for y, v in sorted(by_year.items())],
            'by_kv': sorted(by_kv.values(), key=lambda k: k['code']),
            'unassigned': len(__import__('beitraege').pending(store)), 'unclear': len(unclear), 'issues': unclear}


def _eur(cents):
    return '' if cents is None else f'{cents / 100:.2f}'.replace('.', ',')


def _sort_name(x):
    return f"{x['last_name'] or x['name']}, {x['first_name'] or ''}".strip(', ')


def _hint(x):
    out = []
    if x['mandate']:
        out.append('Lastschrift')
    if x['left_on']:
        out.append(f"ausgetreten {german_date(x['left_on'])}")
    n = max((y['reminders'] for y in x['years']), default=0)
    if n:
        last = max(y['last_reminder'] for y in x['years'] if y['last_reminder'])
        out.append(f"{n}× erinnert, zuletzt {german_date(last)}")
    return out


@exclusive
def pdf(store, kv=None, ov=None, today=None):
    """OP-Liste als PDF (A4 quer): je Kreisverband ein Abschnitt, je Mitglied eine Zeile mit offenen Jahren, Kontakt und Hinweis."""
    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import CondPageBreak, Paragraph, SimpleDocTemplate
    r = items(store, kv, ov, today)
    st, cfg = berichte._styles(), store.config()
    p = berichte._para(st)
    title = 'Offene Posten Mitgliedsbeiträge · ' + r['kv_name'] + (f" · {r['ov_name']}" if r['ov_name'] else '')
    story = [p(title, 'title'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · Stand {german_date(r['today'])} · {r['count']} "
               f"{'Mitglied' if r['count'] == 1 else 'Mitglieder'} mit offenen Beiträgen · zusammen {euro(r['total'])}", 'small')]
    if r['unassigned']:
        story.append(p(f"Hinweis: {r['unassigned']} Beitragszahlungen sind noch keinem Mitglied zugeordnet – die Liste kann deshalb zu hoch sein.", 'body'))
    if r['unclear']:
        story.append(p(f"Hinweis: Bei {r['unclear']} {'Mitglied' if r['unclear'] == 1 else 'Mitgliedern'} ist der Beitrag eines Jahres noch zu "
                       'klären (z. B. Austrittsjahr) – dieses Jahr fehlt hier, bis die Landesgeschäftsstelle das Soll festlegt.', 'body'))
    if r['by_year']:
        story.append(berichte._table([['Beitragsjahr'] + [str(y['year']) for y in r['by_year']] + ['Zusammen'],
                                      ['Offen'] + [euro(y['open']) for y in r['by_year']] + [euro(r['total'])]],
                                     [90] + [70] * len(r['by_year']) + [80], st, numeric=tuple(range(1, len(r['by_year']) + 2))))
    if not kv and len(r['by_kv']) > 1:
        story += [p('Je Kreisverband', 'h2'),
                  berichte._table([['Kreisverband', 'Mitglieder', 'Offen']] + [[k['name'], k['count'], euro(k['open'])] for k in r['by_kv']],
                                  [180, 80, 90], st, numeric=(1, 2))]
    cell = lambda *lines: Paragraph('<br/>'.join(escape(str(x)) for x in lines if x), st['cell'])   # noqa: E731
    for k in r['by_kv'] or [{'code': kv, 'name': r['kv_name']}]:
        rows = [x for x in r['members'] if x['kv'] == k['code']]
        if not kv:
            story += [CondPageBreak(90), p(f"{k['name']} · {len(rows)} Mitglieder · {euro(sum(x['open'] for x in rows))}", 'h2')]
        if not rows:
            story.append(p('Keine offenen Beiträge.', 'body'))
            continue
        body = [['Mitglied', 'Ortsverband', 'Kontakt', 'Offen je Jahr', 'Offen', 'Hinweis']]
        for x in rows:
            body.append([cell(_sort_name(x), x['number']), cell(x['ov'] or '—'), cell(x['email'], x['phone']) if x['email'] or x['phone'] else cell('—'),
                         cell(*[f"{y['year']}: {euro(y['open'])}" for y in x['years']]), euro(x['open']), cell(*_hint(x)) if _hint(x) else ''])
        story.append(berichte._table(body, [150, 105, 170, 95, 60, 160], st, numeric=(4,)))
    story += [p(NOTE, 'small'), p(CONFIDENTIAL, 'small')]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=40, rightMargin=40, topMargin=36, bottomMargin=36, title=title,
                      creator='JU Hamburg Finanzen').build(story)
    return buf.getvalue()


def csv(store, kv=None, ov=None, today=None):
    """OP-Liste als CSV: eine Zeile je Mitglied und offenem Beitragsjahr."""
    r = items(store, kv, ov, today)
    data = [[x['kv_name'], x['ov'], x['number'], x['last_name'] or x['name'], x['first_name'] or '', x['email'], x['phone'], x['left_on'],
             y['year'], y['basis'], _eur(y['soll']), _eur(y['paid']), _eur(y['open']), y['due'] or '',
             'ja' if x['mandate'] else '', y['reminders'] or '', y['last_reminder'] or '', x['last_payment'] or '', x['external_balance']]
            for x in r['members'] for y in x['years']]
    return berichte._csv(data, ['Kreisverband', 'Ortsverband', 'Mitgliedsnummer', 'Nachname', 'Vorname', 'E-Mail', 'Telefon', 'Austritt',
                                'Beitragsjahr', 'Grundlage', 'Soll', 'Gezahlt', 'Offen', 'Fällig seit', 'Lastschrift', 'Erinnerungen',
                                'Letzte Erinnerung', 'Letzte Zahlung', 'Kontostand Mitgliederverwaltung'])


def _slug(text):
    return re.sub(r'[^A-Za-z0-9ÄÖÜäöüß-]+', '-', str(text)).strip('-')


def zip_all(store, today=None):
    """Alle OP-Listen zum Verteilen: je Kreisverband (mit Mitgliedern) ein PDF und eine CSV."""
    today = today or date.today().isoformat()
    codes = [r[0] for r in store.db.execute('SELECT DISTINCT kv FROM members ORDER BY kv')]
    names = _names(store)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for code in codes:
            if code not in names:
                continue
            base = f'OP-Liste-{code}-{_slug(names[code])}-{today}'
            z.writestr(base + '.pdf', pdf(store, code, None, today))
            z.writestr(base + '.csv', csv(store, code, None, today))
    return buf.getvalue()
