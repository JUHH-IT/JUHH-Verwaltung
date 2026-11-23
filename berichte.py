"""Berichte: Monatsbericht für den Landesvorstand (PDF) und Kassenprüfer-Paket (ZIP, Satzung § 19 Abs. 2).

Monatsbericht: Stand eines Tages – Saldo der gebuchten Bankbewegungen, offene Umsätze, Haushalt Plan/Ist mit 10-%-Hinweis,
offene Forderungen und Verbindlichkeiten, Budgets der Kreisverbände, Spenden, anstehende Aufgaben.
KV-Auszug: kompaktes Budget-PDF je Kreisverband für den Kreisvorsitz (kv_pdf).
Kassenprüfer-Paket: was die Rechnungsprüfer für ein Haushaltsjahr einsehen (Kassenunterlagen, Kontoumsätze und
Originaldateien, Rechnungen, Belege, Freigaben, Prüfprotokolle, Lastschriften, Spenden, Änderungsprotokoll) als ZIP
mit Übersicht und automatischen Prüfhinweisen. Nur lesen – erzeugt keine Buchungen.
"""
import calendar
import csv
import io
import re
import zipfile
from datetime import date
from pathlib import Path

from formate import euro, german_date
from pdfschrift import SANS, SANS_BOLD, exclusive

MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember']
BANK = '1420'
FISCAL_YEAR = 'COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER))'


def _money(v):
    return '—' if v is None else euro(v)


def balance(store, account, until=None):
    """Saldo Soll − Haben eines Kontos bis einschließlich until (Buchungsdatum)."""
    sql = 'SELECT COALESCE(SUM(e.debit-e.credit),0) FROM entries e JOIN journals j ON j.id=e.journal_id WHERE e.account=?'
    return store.db.execute(sql + ' AND j.day<=?', (account, until)).fetchone()[0] if until else store.db.execute(sql, (account,)).fetchone()[0]


def _totals(report):
    totals = [r['values'] for r in report['rows'] if r['type'] == 'total']
    result = next((r['values'] for r in report['rows'] if r['type'] == 'result'), [None] * 4)
    return {'income': totals[0], 'expense': totals[1], 'result': result}


def _ratio(plan, ist):
    return f'{round((ist or 0) / plan * 100)} %' if plan else '—'


# ---------------------------------------------------------------- Monatsbericht

def report_day(month=None, data_start=None, today=None):
    """Stichtag des Monatsberichts: Monatsende des Monats JJJJ-MM, im laufenden Monat heute; ohne Monat heute."""
    today = today or date.today().isoformat()
    if not month:
        return today
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', str(month)):
        raise ValueError('Ungültiger Monat.')
    if month > today[:7]:
        raise ValueError('Der Monat liegt in der Zukunft.')
    if data_start and month < data_start[:7]:
        raise ValueError(f'Vor dem Datenbeginn {german_date(data_start)} gibt es keine Daten.')
    last = f'{month}-{calendar.monthrange(int(month[:4]), int(month[5:]))[1]:02d}'
    return min(last, today)


def _cash(store, day):
    """Kassen zum Stichtag: je eingerichteter Kasse Bestand, offene Belege und offene Posten im Geldtransit (`kasse.transit`),
    dazu die Kontoumsätze auf 2380 ohne Kassenbeleg und der Saldo 2380 – None, solange keine Kasse eingerichtet ist. Eine
    stillgelegte Kasse steht nur in den Jahren, in denen sie Bewegungen hatte."""
    import kasse
    t = kasse.transit(store, day)
    kassen = [{'id': k['id'], 'name': k['name'], 'account': k['account'], 'balance': kasse.balance(store, day, k), 'transit': t['kassen'].get(k['id'], []),
               'open': store.db.execute("SELECT COUNT(*) FROM bank WHERE ledger='cash' AND kasse=? AND state IN ('open','parked') AND booked<=?",
                                        (k['id'], day)).fetchone()[0]} for k in kasse.liste(store) if k['start'] and (k['aktiv'] or kasse.bewegt(store, k, day[:4]))]
    return {'kassen': kassen, 'transit': t['saldo'], 'transit_bank': t['bank']} if kassen else None


def _minus_notes(cash, only=None):
    """Hinweis aus `_cash` je Kasse mit Bestand im Minus – das gibt es nur bei einem Zahlungsdienst (PayPal): der Einzug vom
    Bankkonto ist noch nicht gebucht. Kein Fehler. only: nur diese Kassen (Kennungen)."""
    return [f"{k['name']}: Bestand im Minus ({euro(k['balance'])}) – der Einzug vom Bankkonto über {euro(-k['balance'])} fehlt noch "
            f"(im Kontoauszug auf Konto {k['account']} buchen)" for k in cash['kassen'] if k['balance'] < 0 and (only is None or k['id'] in only)]


def _transit_sum(posten):
    return euro(sum(abs(p['amount']) for p in posten))


def _transit_notes(cash, only=None):
    """Hinweise zum Geldtransit 2380 aus `_cash`: je Kasse die Belege ohne Kontoumsatz, die Kontoumsätze ohne Kassenbeleg – und
    der Saldo nur, wenn er sich aus keinem dieser Posten erklärt. only: nur diese Kassen (Kennungen)."""
    notes = [f"Geldtransit 2380, {k['name']}: {len(k['transit'])} Beleg(e) ohne Gegenseite im Kontoauszug ({_transit_sum(k['transit'])}) – "
             + ', '.join(f"{x['number']} vom {german_date(x['day'])}" for x in k['transit'][:5]) + (' …' if len(k['transit']) > 5 else '')
             + ' – den Kontoumsatz auf 2380 buchen' for k in cash['kassen'] if k['transit'] and (only is None or k['id'] in only)]
    if cash['transit_bank']:
        notes.append(f"Geldtransit 2380: {len(cash['transit_bank'])} Buchung(en) aus dem Kontoauszug ohne Kassenbeleg ({_transit_sum(cash['transit_bank'])}) – "
                     + ', '.join(f"{german_date(x['day'])} {euro(x['amount'])}" for x in cash['transit_bank'][:5]) + (' …' if len(cash['transit_bank']) > 5 else '')
                     + ' – anderer Durchlaufposten oder Kassenbeleg fehlt')
    if cash['transit'] and not notes:
        notes.append(f"Saldo des Kontos 2380 nicht ausgeglichen ({euro(cash['transit'])}) – Kontenblatt 2380 prüfen")
    return notes


def _month_flows(store, first, day):
    """Bewegungen eines Monats: Kontoauszug (Ein-/Ausgänge) und gebuchte Einnahmen/Ausgaben je Haushaltstitel."""
    import haushalt
    bank_in, bank_out, count = store.db.execute(
        'SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount END),0),COALESCE(SUM(CASE WHEN amount<0 THEN amount END),0),COUNT(*) '
        "FROM bank WHERE ledger='bank' AND state!='void' AND booked BETWEEN ? AND ?", (first, day)).fetchone()   # ungültig (doppelt importiert) zählt nicht
    titles = {}
    totals = {'income': 0, 'expense': 0}
    for e in store.rows("SELECT e.account,e.kv,e.title,e.debit,e.credit,a.kind FROM entries e JOIN journals j ON j.id=e.journal_id "
                        "JOIN accounts a ON a.code=e.account WHERE a.kind IN ('income','expense') AND j.day BETWEEN ? AND ?", (first, day)):
        value = e['credit'] - e['debit']
        totals[e['kind']] += value
        title = haushalt.KV_ROWS[e['kv']] if e['kind'] == 'expense' and e['kv'] in haushalt.KV_ROWS else e['title'] if e['title'] in haushalt.TITLES \
            else haushalt.AUTO.get(e['account'])
        label = (lambda t: f"{t['code'] + ' ' if t['code'] else ''}{t['label']}")(haushalt.TITLES[title]) if title in haushalt.TITLES else 'Noch nicht zugeordnet'
        key = (e['kind'], label)
        titles[key] = titles.get(key, 0) + value
    biggest = sorted(((k[1], v) for k, v in titles.items() if k[0] == 'expense' and v), key=lambda x: x[1])[:8]
    return {'bank_in': bank_in, 'bank_out': bank_out, 'bank_count': count, 'income': totals['income'], 'expense': totals['expense'],
            'expense_titles': biggest}


def monthly(store, day=None):
    """Kennzahlen für den Monatsbericht (Stand day; Haushaltsjahr = Jahr von day)."""
    import freigabe
    import haushalt
    import kvbudget
    import lastschrift
    import verbindlichkeiten
    import versand
    from core import KVS
    day = day or date.today().isoformat()
    year = int(day[:4])
    report = haushalt.report(store, year, until=day)                   # Ist bis zum Stichtag
    invoices = [i for i in store.invoices() if i['status'] == 'issued' and i['total'] > i['paid'] and not i['pilot']]
    bills = [b for b in verbindlichkeiten.bills(store) if b['status'] == 'open' and b['open'] > 0]
    names = dict(KVS)
    kv = []
    past = day < date.today().isoformat()
    year_rows = {b['code']: b for b in kvbudget.year_overview(store, year, until=day if past else None)}
    for code, led in kvbudget.overview(store, day, until=day if past else None).items():
        nxt = led['next'][0] if led['next'] else None
        y = year_rows[code]
        kv.append({'code': code, 'name': names[code], 'available': led['available'], 'uncovered': led['uncovered'], 'spent': led['spent'],
                   'next_until': nxt and nxt['until'], 'next_amount': nxt and nxt['amount'], 'plan': y['plan'], 'paid': y['paid'],
                   'carry': y['carry'], 'ffk': y['ffk'], 'share': y['share'], 'gains': y['gains'],
                   'ist_budget': y['ist_budget'], 'year_spent': y['spent']})
    donations = kvbudget.donations(store, year)
    waiting = freigabe.pending(store)
    tasks = [f"{'Nachprüfung' if r['recheck'] else 'Quartalsprüfung'} Eingangsrechnungen {r['label']} bis {german_date(r['deadline'])}"
             for r in freigabe.due(store, day)]
    if waiting:
        tasks.append(f"Freigabe nach FO § 7 Abs. 4 fehlt: {', '.join(waiting)}")
    ready = sum(1 for r in versand.reminders(store, today=day) if not r['reason'])
    if ready:
        tasks.append(f'{ready} Zahlungserinnerung(en) können versendet werden')
    sepa = sum(1 for c in lastschrift.collectable(store, day) if not c['reason'] and c['soon'])
    if sepa:
        tasks.append(f'{sepa} Lastschrift(en) einzureichen')
    check = sum(1 for b in lastschrift.batches(store, day) if b['issues'])
    if check:
        tasks.append(f'{check} Lastschriftdatei(en) prüfen – enthält Lastschriften, die nicht mehr eingezogen werden dürfen')
    import beitraege
    pending = [x for x in beitraege.pending(store) if x['day'] <= day]
    unassigned = [x for x in pending if x['open']]
    if unassigned:
        tasks.append(f"{len(unassigned)} Beitragszahlung(en) auf 8100 ({euro(sum(x['open'] for x in unassigned))}) einem Mitglied zuordnen "
                     '(Pflicht – Grundlage für Mitgliederkonten und KV-Beitragsanteil)')
    in_case = [x for x in pending if x['in_case']]
    if in_case:
        tasks.append(f"{sum(len(x['cases']) for x in in_case)} Klärfall/Klärfälle ({euro(sum(x['in_case'] for x in in_case))}) bei der Mitgliederverwaltung – "
                     'Zahlungen auf 8100, deren Mitglied noch zu klären ist')
    import rpj
    rpj_open = [x for x in rpj.open_entries(store) if x['day'] <= day]
    if rpj_open:
        tasks.append(f"{len(rpj_open)} RPJ-Buchung(en) einem Projekt bzw. Mittelabruf zuordnen (Seite RPJ-Mittel → RPJ zuordnen)")
    cash = _cash(store, day)
    tasks += [f"{k['open']} Kassenbeleg(e) verbuchen (Kasse → {k['name']} → Buchungsplatz)" for k in (cash or {}).get('kassen', []) if k['open']]
    tasks += _transit_notes(cash) if cash else []
    tasks += [f'KV-Budgetjahr {y} abschließen (FO § 6)' + (' – vorher alle Beitragszahlungen des Jahres zuordnen'
                                                            if any(x['year'] == y for x in unassigned) else '') for y in kvbudget.closing_due(store, day)]
    tasks += [f"Barspende {c['receipt']}: {', '.join(c['issues'])}" for c in kvbudget.cash_list(store, day) if c['overdue'] or not c['cdu_informed_on']]
    open_bank = store.db.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM bank WHERE ledger='bank' AND state='open' AND booked<=?", (day,)).fetchone()
    return {'day': day, 'year': year, 'month': f'{MONTHS[int(day[5:7]) - 1]} {year}', 'today': date.today().isoformat(),
            'flows': _month_flows(store, day[:8] + '01', day),
            'bank': balance(store, BANK, day),
            'parked': dict(zip(('count', 'sum'), store.db.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM bank WHERE ledger='bank' AND state='parked' AND booked<=?",
                                                                    (day,)).fetchone())),
            'open_bank': {'count': open_bank[0], 'sum': open_bank[1]},
            'last_booked': store.db.execute("SELECT MAX(booked) FROM bank WHERE ledger='bank' AND booked<=?", (day,)).fetchone()[0],
            'cash': _cash(store, day),
            'totals': _totals(report), 'overruns': report['overruns'],
            'receivables': {'count': len(invoices), 'sum': sum(i['total'] - i['paid'] for i in invoices),
                            'overdue': sum(i['total'] - i['paid'] for i in invoices if i['due'] < day)},
            'payables': {'count': len(bills), 'sum': sum(b['open'] for b in bills), 'waiting': len(waiting)},
            'kv': kv, 'dues': __import__('mitgliederkonto').summary(store, year, today=day),
            'rpj': _rpj_month(store, year),
            'donations': {'count': len(donations), 'sum': sum(d['amount'] for d in donations),
                                    'kv_share': sum(d['kv_share'] for d in donations)},
            'tasks': tasks}


def _rpj_month(store, year):
    """RPJ-Kennzahlen des Jahres für den Monatsbericht (Stand heute), None ohne Bescheid und Projekte."""
    import rpj
    if not store.db.execute('SELECT 1 FROM rpj_years WHERE year=? UNION SELECT 1 FROM rpj_projects WHERE year=?', (year, year)).fetchone():
        return None
    v = rpj.year_view(store, year)
    return {**v['cards'], 'open': v['open']}


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    navy, muted = colors.HexColor('#043770'), colors.HexColor('#5b6b7f')
    return {'title': ParagraphStyle('t', fontName=SANS_BOLD, fontSize=15, leading=19, textColor=navy),
            'h2': ParagraphStyle('h2', fontName=SANS_BOLD, fontSize=10.5, leading=14, textColor=navy, spaceBefore=10, spaceAfter=4),
            'body': ParagraphStyle('b', fontName=SANS, fontSize=9, leading=12.5),
            'small': ParagraphStyle('s', fontName=SANS, fontSize=7.5, leading=10, textColor=muted, spaceBefore=3),
            'cell': ParagraphStyle('c', fontName=SANS, fontSize=8, leading=10),
            'num': ParagraphStyle('n', fontName=SANS, fontSize=8, leading=10, alignment=2),
            'head': ParagraphStyle('h', fontName=SANS_BOLD, fontSize=7.5, leading=10, textColor=muted)}


def _table(rows, widths, st, numeric=(), pad=None):
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle
    data = [[c if hasattr(c, 'wrapOn') else Paragraph(escape(str(c)), st['head' if i == 0 else 'num' if j in numeric else 'cell'])   # fertige Absätze (z. B. mehrzeilig) unverändert
             for j, c in enumerate(r)] for i, r in enumerate(rows)]
    t = Table(data, colWidths=widths, hAlign='LEFT', repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef3f9')), ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#d5dde8')),
                           ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3), ('VALIGN', (0, 0), (-1, -1), 'TOP')]
                          + ([('LEFTPADDING', (0, 0), (-1, -1), pad), ('RIGHTPADDING', (0, 0), (-1, -1), pad)] if pad is not None else [])))
    return t


def _pdf(story, title):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=45, rightMargin=45, topMargin=38, bottomMargin=38, title=title,
                      creator='JU Hamburg Finanzen').build(story)
    return buf.getvalue()


def _para(st):
    from xml.sax.saxutils import escape
    from reportlab.platypus import Paragraph
    return lambda text, style='body': Paragraph(escape(str(text)), st[style])


@exclusive
def monthly_pdf(store, day=None):
    m, cfg, st = monthly(store, day), store.config(), _styles()
    p, t, f = _para(st), m['totals'], m['flows']
    story = [p(f"Monatsbericht Finanzen – {m['month']}", 'title'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · Stand {german_date(m['day'])} · Haushaltsjahr {m['year']} · Arbeitsfassung aus der Buchhaltung", 'small'),
             p('Bankkonto', 'h2'),
             _table([['Saldo gebuchter Bankbewegungen', 'Im Parkkonto (ungeklärt)', 'Nicht zugeordnete Umsätze', 'Kontoauszug bis'],
                     [euro(m['bank']), f"{m['parked']['count']} · {euro(m['parked']['sum'])}", f"{m['open_bank']['count']} · {euro(m['open_bank']['sum'])}",
                      german_date(m['last_booked']) or '—']], [130, 115, 130, 105], st),
             p('Saldo = Summe der gebuchten Kontobewegungen (ein Anfangsbestand nur, wenn er gebucht ist) – mit dem Kontoauszug abgleichen. '
               'Umsätze in Parkkonto und offen: Anzahl und Betrag laut Kontoauszug.', 'small')]
    if m['cash']:
        bank_open = m['cash']['transit_bank']
        story += [p('Kasse', 'h2'), _table([['Kasse', 'Bestand am Stichtag', 'Offene Kassenbelege', 'Geldtransit offen']]
                                            + [[f"{k['name']} ({k['account']})", euro(k['balance']), k['open'],
                                                f"{len(k['transit'])} · {_transit_sum(k['transit'])}" if k['transit'] else '–'] for k in m['cash']['kassen']],
                                            [170, 110, 100, 100], st, numeric=(1, 2, 3)),
                  p('Geldtransit offen = Belege der Kasse auf 2380, zu denen der Kontoumsatz auf 2380 noch fehlt (Anzahl · Betrag). '
                    + (f'Im Kontoauszug stehen {len(bank_open)} Buchung(en) auf 2380 ohne Kassenbeleg ({_transit_sum(bank_open)}). ' if bank_open else '')
                    + f"Saldo des Kontos 2380: {euro(m['cash']['transit'])}. Bestand laut Kassenbuch (Anfangsbestand + Kassenbelege + Buchungen auf "
                    'dem Konto der Kasse) – mit der Zählung der Kasse bzw. dem Kontostand (z. B. PayPal) abgleichen.', 'small')]
        story += [p(f'Hinweis – {n}.', 'small') for n in _minus_notes(m['cash'])]
    story += [
             p(f"Im {m['month']}", 'h2'),
             _table([['Eingänge Konto', 'Ausgänge Konto', 'Einnahmen gebucht', 'Ausgaben gebucht'],
                     [euro(f['bank_in']), euro(f['bank_out']), euro(f['income']), euro(f['expense'])]], [120, 120, 120, 120], st, numeric=(0, 1, 2, 3)),
             p(f"Kontoauszug: {f['bank_count']} Umsätze im Monat. Gebucht = Einnahmen und Ausgaben mit Buchungsdatum im Monat "
               '(auch offene Rechnungen und Eingangsrechnungen).', 'small')]
    if f['expense_titles']:
        story += [_table([['Größte Ausgaben im Monat nach Haushaltstitel', 'Betrag']] + [[label, euro(v)] for label, v in f['expense_titles']],
                         [360, 120], st, numeric=(1,))]
    story += [p(f"Haushalt {m['year']} bis {german_date(m['day'])}", 'h2'),
             _table([['', f"Plan {m['year']}", f"Ist bis {german_date(m['day'])}", 'Ausschöpfung'],
                     ['Einnahmen', _money(t['income'][2]), _money(t['income'][3]), _ratio(t['income'][2], t['income'][3])],
                     ['Ausgaben', _money(t['expense'][2]), _money(t['expense'][3]), _ratio(t['expense'][2], t['expense'][3])],
                     ['Ergebnis', _money(t['result'][2]), _money(t['result'][3]), '']], [170, 105, 105, 100], st, numeric=(1, 2, 3))]
    if m['overruns']:
        story.append(p('Titelüberschreitung über 10 % – laut Satzung § 50 vor weiteren Verpflichtungen Nachtragshaushalt beschließen: '
                       + ', '.join(f"{o['code']} {o['label']} (+{o['pct']} %)" for o in m['overruns']), 'body'))
    r, b, d = m['receivables'], m['payables'], m['dues']
    story += [p('Offene Posten' + (f" (Stand heute, {german_date(m['today'])})" if m['day'] < m['today'] else ''), 'h2'),
              _table([['', 'Anzahl', 'Offen', 'Hinweis'],
                      ['Forderungen (Beiträge, Rechnungen)', r['count'], euro(r['sum']), f"davon überfällig {euro(r['overdue'])}"],
                      ['Verbindlichkeiten (Eingangsrechnungen)', b['count'], euro(b['sum']), f"{b['waiting']} ohne Freigabe" if b['waiting'] else '']],
                     [190, 55, 95, 140], st, numeric=(1, 2)),
              p(f"Mitgliedsbeiträge {m['year']}", 'h2'),
              _table([['Beiträge (Soll)', 'Gezahlt', 'Rückstände gesamt', 'Zahlungen ohne Mitglied'],
                      [euro(d['soll']), euro(d['paid']), f"{d['arrears_count']} · {euro(d['arrears'])}",
                       f"{d['reconciliation']['open_count']} · {euro(d['reconciliation']['open'])}"]], [120, 110, 125, 125], st, numeric=(0, 1)),
              p('Soll laut Beitragsstaffel (Satzung § 49) bzw. Beitragsrechnung, gezahlt = zugeordnete Zahlungen des Beitragsjahres. '
                'Rückstände: alle fälligen Beiträge seit Datenbeginn minus Zahlungen.' + (' Mitgliederkonten unvollständig, solange '
                'Zahlungen ohne Mitglied offen sind.' if d['reconciliation']['open_count'] else '') +
                (f" {d['issues']} Mitglieder mit ungeklärtem Soll." if d['issues'] else ''), 'small'),
              *([p(f"RPJ-Mittel {m['year']}" + (f" (Stand heute, {german_date(m['today'])})" if m['day'] < m['today'] else ''), 'h2'),
                 _table([['Kontingent', 'Abgerufen', 'Eingegangen', 'Zurückgezahlt', 'Verwendet', 'Saldo', 'Nicht zugeordnet'],
                         [_money(m['rpj']['contingent']), euro(m['rpj']['requested']), euro(m['rpj']['received']), euro(m['rpj']['refunded']),
                          euro(m['rpj']['used']), euro(m['rpj']['balance']), m['rpj']['open']]], [74, 72, 72, 74, 72, 72, 69], st, numeric=(0, 1, 2, 3, 4, 5, 6)),
                 p('Saldo = eingegangen − zurückgezahlt − verwendet (Kosten der Projekte minus Teilnahmebeiträge). Nicht zugeordnet: RPJ-Buchungen '
                   'ohne Projekt bzw. Mittelabruf.', 'small')] if m['rpj'] else []),
              p(f"Kreisverbände und Schüler Union {m['year']}", 'h2'),
              _table([['Bereich', 'Plan', 'Übertrag', 'FFK', 'Beitragsanteil', 'Zugänge', 'Ausgaben', 'Stand am Stichtag', 'Nächster Verfall']] +
                     [[k['name'], _money(k['plan']), euro(k['carry']), euro(k['ffk']), euro(k['share']), euro(k['gains']), euro(k['year_spent']),
                       euro(k['available']), f"{euro(k['next_amount'])} am {german_date(k['next_until'])}" if k['next_until'] else '—'] for k in m['kv']],
                     [70, 48, 50, 44, 54, 48, 50, 56, 85], st, numeric=(1, 2, 3, 4, 5, 6, 7)),
              p('Plan = Zeile im Gesamthaushalt. Budget = Freundes- und Förderkreis (FFK) + Beitragsanteil (Schüler Union: Budget) ab 01.01., '
                'dazu Spenden, Gutschriften und Umbuchungen; Ausgaben mindern es. Ein positiver Rest verfällt am 31.01. des Folgejahres, '
                'ein Minus wird vorgetragen (Schüler Union: Rest wird vorgetragen).', 'small'),
              p(f"Spenden {m['year']}: {m['donations']['count']} Eingänge · {euro(m['donations']['sum'])}, davon für Kreisverbände {euro(m['donations']['kv_share'])}", 'body'),
              p('Aufgaben', 'h2')]
    story += [p('• ' + x) for x in m['tasks']] or [p('Keine offenen Aufgaben.')]
    story.append(p('Alle Werte aus der lokalen Buchhaltung – nur so aktuell wie der zuletzt importierte Kontoauszug.', 'small'))
    return _pdf(story, f"Monatsbericht {m['month']}")


# ---------------------------------------------------------------- Kassenprüfer-Paket

_NUMBER = re.compile(r'-?\d+(,\d+)?')


def _csv(rows, header):
    """CSV für Excel (Semikolon, UTF-8 mit BOM); Texte, die als Formel gelesen würden, bekommen ein Hochkomma."""
    def safe(v):
        if v is None:
            return ''
        if isinstance(v, str) and v[:1] in ('=', '+', '-', '@', '\t', '\r') and not _NUMBER.fullmatch(v):
            return "'" + v
        return v
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    w.writerow(header)
    w.writerows([safe(v) for v in r] for r in rows)
    return b'\xef\xbb\xbf' + buf.getvalue().encode()


KV_KINDS = {'anfang': 'Anfangsbestand', 'uebertrag': 'Übertrag', 'ffk': 'Freundes- und Förderkreis', 'beitrag': 'Beitragsanteil/Budget',
            'spende': 'Spende', 'gutschrift': 'Gutschrift', 'ausgabe': 'Ausgabe', 'umbuchung': 'Umbuchung', 'verfall': 'Verfall'}


@exclusive
def kv_pdf(store, kv, year, today=None):
    """Kompakter Auszug für den Kreisvorsitz: Zugänge (FFK als eigener Punkt), Ausgaben mit kurzer Bezeichnung, Stand, Verfall."""
    import kvbudget
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle
    today = today or date.today().isoformat()
    if kv not in dict(kvbudget.areas()):
        raise ValueError('Kein Kreisverband.')
    year = int(year)
    led = kvbudget.ledger(store, kv, today)
    s = kvbudget.statement_for(led, year)
    name, st, cfg = kvbudget.area_name(kv), _styles(), store.config()
    p = _para(st)
    story = [p(f'{name} · Budget {year}', 'title'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · Stand {german_date(today)} · Auszug für den Kreisvorsitz", 'small')]
    if not s['active']:
        story.append(p(f"Für {year} gibt es kein Budgetkonto (KV-Budgets ab {led['start']}).", 'body'))
        return _pdf(story, f'{name} Budget {year}')
    su = kv == '5800'
    current = int(today[:4]) == year
    end_label = f'Stand heute ({german_date(today)})' if current else f'Stand 31.12.{year}'
    rows = [['Überblick', 'Betrag']]
    if s['carry'] or year != led['start']:
        rows.append([f'Übertrag aus {year - 1}' if year != led['start'] else f'Anfangsbestand 01.01.{year}', euro(s['carry'])])
    if not su:
        rows.append([f'Freundes- und Förderkreis {year}', euro(s['ffk'])])
    rows.append([(f'Budget {year}' if su else f'Beitragsanteil {year}') + ('' if s['dues_fixed'] or su else ' (25 % der gezahlten Beiträge)'), euro(s['dues_share'])])
    rows.append(['Spenden, Gutschriften, Umbuchungen', euro(s['gains'])])
    rows.append(['Ausgaben und Umbuchungen', euro(-s['spent'])])
    if s['lapsed']:
        rows.append([f'Verfallen am 31.01.{year} (Rest {year - 1})', euro(-s['lapsed'])])
    end_row = len(rows)
    rows.append([end_label, euro(s['end'])])
    if s['open_old'] > 0:
        rows.append([f'davon Rest {year - 1}, verfällt am 31.01.{year}', euro(s['open_old'])])
    if not su and s['balance'] > 0:
        done = s['rest_lapsed'] is not None
        still = s['open_next'] if s.get('open_next') is not None else s['balance']   # im Januar des Folgejahres: was noch übrig ist
        rows.append([f"{'Verfallen' if done else 'Verfällt'} am 31.01.{year + 1}" + (' (nach Ausgaben im Januar)' if (done and s.get('jan_used')) or
                     (not done and still != s['balance']) else ''), euro(s['rest_lapsed'] if done else still)])
    elif s['end'] < 0:
        rows.append([f'Minus wird ins Jahr {year + 1} vorgetragen', euro(s['end'])])
    summary = _table(rows, [330, 110], st, numeric=(1,))
    summary.setStyle(TableStyle([('BACKGROUND', (0, end_row), (-1, end_row), colors.HexColor('#e6eef8'))]))
    story += [summary, p(f'Bewegungen {year}', 'h2')]
    moves = [line for line in s['lines'] if line['kind'] not in ('anfang', 'uebertrag', 'ffk', 'beitrag') or line['amount']]
    body = [['Datum', 'Bezeichnung', 'Empfänger', 'Betrag', 'Stand']] + [
        [german_date(line['day']), line['label'] or KV_KINDS.get(line['kind'], ''), line.get('recipient') or '', euro(line['amount']), euro(line['balance'])]
        for line in moves]
    story.append(_table(body, [60, 205, 108, 64, 68], st, numeric=(3, 4)) if len(body) > 1 else p('Keine Bewegungen.', 'body'))
    rule = ('Budget der Schüler Union: Jahresbudget ab 01.01., dazu Spenden und Gutschriften; Ausgaben mindern es; der Rest wird ins Folgejahr vorgetragen.'
            if su else 'Budget: Freundes- und Förderkreis und Beitragsanteil ab 01.01., dazu Spenden, Gutschriften und Umbuchungen; Ausgaben mindern es. '
            'Ein Rest steht bis 31.01. des Folgejahres zur Verfügung und verfällt dann an den Landesverband (FO § 6 Abs. 3); ein Minus wird vorgetragen.')
    story.append(p(rule + ' Stand der Buchhaltung – nur so aktuell wie der zuletzt importierte Kontoauszug.', 'small'))
    return _pdf(story, f'{name} Budget {year}')


@exclusive
def cash_pdf(store, year, today=None, kasse_id=0):
    """Kassenbuch einer Kasse für ein Jahr: Übertrag, Einnahmen, Ausgaben, Bestand, alle Bewegungen mit Beleg und Bestand."""
    import kasse
    today = today or date.today().isoformat()
    b = kasse.book(store, year, kasse_id)
    k = b['kasse']
    title = f"Kassenbuch {k['name']} {year}" if k['id'] else f'Kassenbuch {year}'
    st, cfg = _styles(), store.config()
    p = _para(st)
    end_label = f'Bestand heute ({german_date(today)})' if int(today[:4]) == int(year) else f'Bestand 31.12.{year}'
    story = [p(title, 'title'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · {k['name'] if k['id'] else 'Kasse'} (Konto {k['account']}) · Kassenbuch ab {german_date(b['start']) or '—'} · erstellt am {german_date(today)}", 'small'),
             _table([['Übertrag 01.01.', 'Anfangsbestand', 'Einnahmen', 'Ausgaben', end_label],
                     [euro(b['carry']), euro(b['opening_in_year']), euro(b['income']), euro(b['expense']), euro(b['end'])]],
                    [90, 90, 90, 90, 120], st, numeric=(0, 1, 2, 3, 4)),
             p('Bewegungen', 'h2')]
    body = [['Datum', 'Beleg', 'Text', 'Einnahme', 'Ausgabe', 'Bestand']] + [
        [german_date(x['day']), x['number'] or x['label'],
         ' · '.join(v for v in (x['text'], x['name'], {'storno': 'Storno', 'open': 'offen', 'parked': 'geparkt',
                                                        'reversed': 'später storniert'}.get(x['state'], '')) if v),
         euro(x['amount']) if x['amount'] > 0 else '', euro(-x['amount']) if x['amount'] < 0 else '', euro(x['balance'])] for x in b['lines']]
    story.append(_table(body, [55, 65, 190, 60, 60, 65], st, numeric=(3, 4, 5)) if len(body) > 1 else p('Keine Bewegungen.', 'body'))
    if b['void']:
        story += [p('Ungültige Belege (zählen nicht)', 'h2'),
                  _table([['Datum', 'Beleg', 'Text', 'Betrag', 'Grund']] + [[german_date(v['booked']), v['bank_ref'], v['reference'] or '', euro(v['amount']), v['note'] or '']
                                                                           for v in b['void']], [55, 65, 170, 60, 140], st, numeric=(3,))]
    offen = b['transit']                                      # nur Belege DIESER Kasse, zu denen der Kontoumsatz auf 2380 fehlt
    dienst = k['art'] == 'dienst' and not offen                # Zahlungsdienst (PayPal): kein Geldtransit, Bestand darf im Minus stehen
    story.append(p(f"Bestand = Anfangsbestand + Kassenbelege + Buchungen auf {k['account']} (auch aus dem Kontoauszug). "
                   + (f"{k['name']} zieht jede Zahlung vom Bankkonto ein: Die Abbuchung steht im Kontoauszug und wird dort direkt auf Konto "
                      f"{k['account']} gebucht. Bis dahin steht der Bestand im Minus." if dienst else 'Geldtransit über 2380: '
                      + (f"{len(offen)} Beleg(e) dieser Kasse ohne Gegenseite im Kontoauszug ({_transit_sum(offen)}): "
                      + ', '.join(f"{x['number']} vom {german_date(x['day'])}" for x in offen) + '.' if offen else
                         'bei dieser Kasse ausgeglichen – zu jedem Beleg auf 2380 gibt es den Kontoumsatz.'))
                   + ('' if k['aktiv'] else ' Die Kasse ist stillgelegt.'), 'small'))
    return _pdf(story, title)


@exclusive
def member_pdf(store, member_id, today=None):
    """Beitragskonto eines Mitglieds: je Jahr Soll, gezahlt, Stand und die Buchungen mit laufendem Saldo."""
    import mitgliederkonto
    from core import KVS
    today = today or date.today().isoformat()
    acc = mitgliederkonto.account(store, member_id, today)
    m = store.db.execute('SELECT * FROM members WHERE id=?', (int(member_id),)).fetchone()
    st, cfg = _styles(), store.config()
    p = _para(st)
    name = f"{m['first_name'] or ''} {m['last_name'] or m['name']}".strip()
    story = [p(f'Beitragskonto · {name}', 'title'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · Mitgliedsnummer {m['number']} · {dict(KVS).get(m['kv'], m['kv'])} · "
               f"Stand {german_date(today)}", 'small'),
             p('Beiträge je Jahr', 'h2'),
             _table([['Jahr', 'Beitrag (Soll)', 'Grundlage', 'Gezahlt für das Jahr', 'Offen', 'Stand 31.12.']] +
                    [[r['year'], _money(r['soll']), r['issue'] or r['basis'] + ('' if r['due_now'] or not r['soll'] else f" (fällig {german_date(r['due'])})"),
                      euro(r['paid']), _money(r['open']), euro(r['end_balance'])] for r in acc['years']],
                    [40, 70, 170, 70, 55, 75], st, numeric=(1, 3, 4, 5)),
             p(('Rückstand' if acc['balance'] < 0 else 'Guthaben' if acc['balance'] > 0 else 'Ausgeglichen') +
               (f": {euro(abs(acc['balance']))}" if acc['balance'] else ''), 'h2')]
    if acc['issues']:
        story.append(p('Zu klären: ' + '; '.join(acc['issues']), 'body'))
    moves = [x for x in acc['postings'] if x['ist'] or x['counts']]
    story += [p('Buchungen', 'h2'),
              _table([['Datum', 'Buchung', 'Beitrag', 'Zahlung', 'Saldo']] +
                     [[german_date(x['day']), x['text'], euro(x['soll']) if x['soll'] else '', euro(x['ist']) if x['ist'] else '', euro(x['balance'])]
                      for x in moves], [60, 250, 60, 60, 65], st, numeric=(2, 3, 4)) if moves else p('Keine Buchungen.', 'body'),
              p(f"Beitrag laut Satzung § 49 (Staffel nach Alter, im Eintrittsjahr anteilig), fällig am 01.01. bzw. am Tag des Beitritts; "
                f"Beitragskonto ab {acc['first']} (Datenbeginn). Saldo: Zahlungen minus fällige Beiträge (− Rückstand, + Guthaben); „Offen“: "
                'Zahlungen decken die ältesten fälligen Beiträge zuerst. '
                'Stand der Buchhaltung – nur so aktuell wie der zuletzt importierte Kontoauszug.', 'small')]
    return _pdf(story, f'Beitragskonto {m["number"]}')


def _quarter(q):
    return f'Q{q}' if q else 'ganzjährig'


@exclusive
def rpj_pdf(store, year, today=None):
    """RPJ-Jahresübersicht (Schattenhaushalt): Bescheid, Mittelabrufe, Projekte mit Plan und Ist, Kostenarten, Abstimmung."""
    import rpj
    today = today or date.today().isoformat()
    v = rpj.year_view(store, year)
    st, cfg, c, info = _styles(), store.config(), v['cards'], v['info'] or {}
    p = _para(st)
    head = ' · '.join(x for x in (f"Antrag vom {german_date(info.get('applied_on'))}" if info.get('applied_on') else '',
                                  f"Bescheid vom {german_date(info.get('notice_on'))}" if info.get('notice_on') else '',
                                  f"Az. {info['reference']}" if info.get('reference') else '') if x)
    story = [p(f'RPJ-Mittel {year}', 'title'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · Ring Politischer Jugend Hamburg · {head + ' · ' if head else ''}Stand {german_date(today)}", 'small'),
             _table([['Kontingent (Bescheid)', 'Beantragt (Antrag)', 'Abgerufen', 'Eingegangen', 'Verwendet', 'Saldo'],
                     [_money(c['contingent']), euro(c['applied']), euro(c['requested']), euro(c['received']), euro(c['used']), euro(c['balance'])]],
                    [90, 85, 80, 80, 80, 90], st, numeric=(0, 1, 2, 3, 4, 5)),
             p('Verwendet = Kosten der Projekte (a–h, j, k) minus Teilnahmebeiträge (i). Saldo = eingegangene Mittel minus Rückzahlungen '
               f"{'(' + euro(c['refunded']) + ') ' if c['refunded'] else ''}minus verwendete Mittel (positiv: noch nicht verwendet, negativ: vorfinanziert).", 'small'),
             p('Projekte', 'h2')]
    num = lambda cents: euro(cents).replace('\xa0€', '').replace(' €', '')   # Beträge in Euro ohne Zeichen (Spaltenkopf), damit sechs Spalten passen
    body = [['Nr.', 'Maßnahme · Veranstaltung', 'Q.', 'Kosten Plan', 'Kosten Ist', 'TB Plan', 'TB Ist', 'RPJ Plan', 'RPJ Ist']] + [
        [x['no'], ' · '.join(t for t in (x['kind'], x['name'], x['status_label'] if x['status'] != 'planned' else '') if t),
         f"Q{x['quarter']}" if x['quarter'] else '–', num(x['cost_plan']), num(x['cost_ist']),
         num(x['fee_plan']), num(x['fee_ist']), num(x['rpj_plan']), num(x['rpj_ist'])] for x in v['projects']]
    if len(body) > 1:
        body.append(['', 'Summe', '', num(sum(x['cost_plan'] for x in v['projects'])), num(c['cost']),
                     num(sum(x['fee_plan'] for x in v['projects'])), num(c['fees']), num(c['applied']), num(c['used'])])
    story.append(_table(body, [26, 137, 28, 52, 52, 52, 52, 52, 52], st, numeric=(3, 4, 5, 6, 7, 8), pad=3) if len(body) > 1 else p('Noch keine Projekte.', 'body'))
    story += [p('Zu klären: ' + '; '.join(v['issues']), 'body')] if v['issues'] else []
    story += [p('Beträge in Euro. TB = Teilnahmebeiträge (Kostenart i, Einnahme). RPJ = Kosten minus Teilnahmebeiträge („Mittel RPJ“ im Antrag). '
                'Q. = Quartal (– ganzjährig).', 'small'),
              p('Kostenarten', 'h2'),
              _table([['Kostenart', 'Plan', 'Ist', 'Abweichung']] +
                     [[f"{k['code']}) {k['label']}" + (' (Einnahme)' if k['income'] else ''), euro(k['plan']), euro(k['ist']), euro(k['ist'] - k['plan'])]
                      for k in v['categories']], [220, 90, 90, 90], st, numeric=(1, 2, 3)),
              p('Mittelabrufe', 'h2')]
    story.append(_table([['Mittelabruf', 'Abgerufen am', 'Betrag', 'Eingegangen', 'Noch offen', 'Zurückgezahlt']] +
                        [[r['label'], german_date(r['requested_on']), euro(r['amount']), euro(r['received']), euro(r['open']),
                          euro(r['refunded']) if r['refunded'] else ''] for r in v['requests']],
                        [125, 70, 75, 80, 75, 80], st, numeric=(2, 3, 4, 5)) if v['requests'] else p('Noch keine Mittelabrufe.', 'body'))
    story += [p(f'Abstimmung mit dem Haushalt {year}', 'h2'),
              _table([['RPJ-Posten', 'Buchungen', f'Im Haushalt {year}', f'Projekten/Abrufen {year} zugeordnet', 'anderen Jahren zugeordnet', 'Nicht zugeordnet']] +
                     [[r['label'], r['count'], euro(r['household']), euro(r['linked']), euro(r['other_years']),
                       euro(r['open']) + (f" ({r['open_count']})" if r['open_count'] else '')] for r in v['reconciliation']],
                     [88, 57, 80, 100, 90, 90], st, numeric=(1, 2, 3, 4, 5)),
              p('Mittelverwendung = Konto 4380 bzw. Titel H45 (Kosten), Mittelzufluss = 1592 bzw. H18, Teilnahmebeiträge = Titel H19 – aus Kontoauszug, '
                'Kasse und Eingangsrechnungen, ohne Stornos. Projekte enthalten auch Buchungen benachbarter Haushaltsjahre (z. B. Rechnung im Januar). '
                'Stand der Buchhaltung – nur so aktuell wie der zuletzt importierte Kontoauszug.', 'small')]
    return _pdf(story, f'RPJ-Mittel {year}')


@exclusive
def rpj_project_pdf(store, project_id, today=None):
    """Einzelübersicht eines RPJ-Projekts: Plan und Ist je Kostenart und alle zugeordneten Buchungen."""
    import rpj
    today = today or date.today().isoformat()
    v = rpj.project_view(store, project_id)
    x, st, cfg = v['project'], _styles(), store.config()
    p = _para(st)
    facts = ' · '.join(t for t in (x['kind'], _quarter(x['quarter']) + f" {x['year']}",
                                   f"durchgeführt am {german_date(x['held_on'])}" if x['held_on'] else '',
                                   f"Teilnehmer geplant {x['participants']}" if x['participants'] else '',
                                   f"tatsächlich {x['attended']}" if x['attended'] is not None else '',
                                   f"Ansprechpartner {x['contact']}" if x['contact'] else '', x['status_label']) if t)
    rows = [k for k in v['categories'] if k['plan'] or k['ist']]
    story = [p(f"RPJ-Projekt {x['year']} · Nr. {x['no']}", 'title'), p(x['name'], 'h2'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · {facts} · Stand {german_date(today)}", 'small'),
             p('Plan und Ist je Kostenart', 'h2'),
             _table([['Kostenart', 'Plan', 'Ist', 'Abweichung']] +
                    [[f"{k['code']}) {k['label']}" + (' (Einnahme)' if k['income'] else ''), euro(k['plan']), euro(k['ist']), euro(k['diff'])] for k in rows] +
                    [['Kosten gesamt (a–h, j, k)', euro(x['cost_plan']), euro(x['cost_ist']), euro(x['cost_ist'] - x['cost_plan'])],
                     ['Teilnahmebeiträge (i)', euro(x['fee_plan']), euro(x['fee_ist']), euro(x['fee_ist'] - x['fee_plan'])],
                     ['Mittel RPJ (Kosten − Teilnahmebeiträge)', euro(x['rpj_plan']), euro(x['rpj_ist']), euro(x['rpj_ist'] - x['rpj_plan'])]],
                    [220, 90, 90, 90], st, numeric=(1, 2, 3)),
             p('Buchungen', 'h2')]
    body = [['Datum', 'Herkunft · Beleg', 'Text', 'Kostenart', 'Ausgabe', 'Einnahme']] + [
        [german_date(b['day']) + (f" (HJ {b['fy']})" if b['fy'] != x['year'] else ''), ' '.join(t for t in (b['origin'], b['ref']) if t),
         ' · '.join(t for t in (b['text'], b['name']) if t) + (' (Teilbetrag)' if b['split'] else ''), f"{b['category']}) {b['label']}",
         euro(b['cost']) if b['cost'] else '', euro(b['income']) if b['income'] else ''] for b in v['bookings']]
    story.append(_table(body, [62, 85, 150, 88, 60, 60], st, numeric=(4, 5), pad=4) if len(body) > 1 else p('Noch keine Buchungen zugeordnet.', 'body'))
    if x['note']:
        story.append(p('Notiz: ' + x['note'], 'body'))
    story.append(p('Ist = zugeordnete Buchungen aus Kontoauszug, Kasse und Eingangsrechnungen (ohne Stornos); Ausgaben mindernde Gutschriften '
                   'stehen als negative Ausgabe. HJ = abweichendes Haushaltsjahr der Buchung. Stand der Buchhaltung.', 'small'))
    return _pdf(story, f"RPJ-Projekt {x['year']} Nr. {x['no']}")


def _eur(cents):
    """Cent → „-12,50“ (Zahl für Excel, ohne Tausenderpunkt)."""
    return '' if cents is None else f'{cents / 100:.2f}'.replace('.', ',')


def _safe_name(name):
    return re.sub(r'[^A-Za-z0-9ÄÖÜäöüß._ -]+', '_', Path(str(name)).name)[:80] or 'Datei'


def cash_name(k, year):
    """Dateiname eines Kassenbuchs ohne Endung: „Kassenbuch-2026“ (Bargeldkasse, wie seit 0.25), sonst mit Name und Konto
    („Kassenbuch-PayPal-1430-2026“) – das Konto macht ihn eindeutig, auch wenn zwei Namen dieselben Zeichen ergeben."""
    return f"Kassenbuch-{_safe_name(k['name'])}-{k['account']}-{year}" if k['id'] else f'Kassenbuch-{year}'


STORED = ('.pdf', '.png', '.jpg', '.jpeg', '.zip', '.xls', '.xlsx')  # schon komprimiert – nicht noch einmal packen
INVOICES_PER_PDF = 200  # Sammel-PDF in Teilen: die PDF-Sperre wird zwischendurch frei


def audit_package(store, year, data_dir, out):
    """Kassenprüfer-Paket als ZIP in das Dateiobjekt out schreiben (Ordner Kassenpruefung-JJJJ) – Datei für Datei,
    ohne alles im Speicher zu halten. Die Übersicht mit den Prüfhinweisen entsteht zuletzt."""
    if not 2000 <= year <= 2100:
        raise ValueError('Ungültiges Haushaltsjahr.')
    names, notes = [], []
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in _audit_entries(store, year, Path(data_dir), notes):
            names.append(name)
            z.writestr(f'Kassenpruefung-{year}/{name}', content,
                       compress_type=zipfile.ZIP_STORED if name.lower().endswith(STORED) else zipfile.ZIP_DEFLATED)
        z.writestr(f'Kassenpruefung-{year}/00-Uebersicht.pdf', _overview_pdf(store, year, sorted(names), notes), compress_type=zipfile.ZIP_STORED)


def audit_files(store, year, data_dir):
    """Inhalt des Pakets als (Dateiname → Bytes, Prüfhinweise) – für Tests und kleine Bestände."""
    if not 2000 <= year <= 2100:
        raise ValueError('Ungültiges Haushaltsjahr.')
    notes = []
    files = dict(_audit_entries(store, year, Path(data_dir), notes))
    files['00-Uebersicht.pdf'] = _overview_pdf(store, year, sorted(files), notes)
    return files, notes


def _inside(folder, name):
    """Datei name im Ordner folder – nur echte Dateien darin (keine Pfade oder Verweise nach außen), sonst None."""
    root = folder.resolve()
    target = (folder / Path(str(name)).name).resolve()
    return target if root in target.parents and target.is_file() else None


def _audit_entries(store, year, data_dir, notes):
    """Erzeugt (Dateiname, Bytes) für das Paket und ergänzt notes um automatische Prüfhinweise."""
    import erechnung
    import erstattung
    import freigabe
    import haushalt
    import kvbudget
    import lastschrift
    import rechnung_pdf
    import verbindlichkeiten
    from core import KVS
    y, names = str(year), dict(KVS)

    journal = store.rows(f'SELECT j.id,j.day,{FISCAL_YEAR} fy,j.source,j.description,j.reversal_of,e.account,e.kv,e.debit,e.credit,e.title '
                         f'FROM journals j JOIN entries e ON e.journal_id=j.id WHERE {FISCAL_YEAR}=? ORDER BY j.day,j.id,e.id', (year,))
    yield f'Buchungsjournal-{y}.csv', _csv([[r['id'], r['day'], r['fy'], r['source'], r['description'], r['reversal_of'], r['account'], r['kv'],
                                             _eur(r['debit']), _eur(r['credit']), r['title']] for r in journal],
                                            ['Buchung', 'Datum', 'Haushaltsjahr', 'Quelle', 'Text', 'Storno von Buchung', 'Konto', 'Bereich', 'Soll', 'Haben', 'Titel'])
    yield f'Haushalt-{y}.pdf', haushalt.pdf_report(store, year)

    bank = store.rows("SELECT * FROM bank WHERE ledger='bank' AND substr(booked,1,4)=? ORDER BY booked,id", (y,))
    yield f'Kontoumsaetze-{y}.csv', _csv([[b['id'], b['booked'], _eur(b['amount']), b['name'], b['reference'], b['state'], b['reversals'], b['source'], b['note'] if b['state'] == 'void' else ''] for b in bank],
                                         ['Umsatz', 'Datum', 'Betrag', 'Name', 'Verwendungszweck', 'Stand', 'Stornos', 'Datei', 'Grund (ungültig)'])
    open_bank = [b for b in bank if b['state'] in ('open', 'parked')]
    if open_bank:
        notes.append(f'{len(open_bank)} Bankumsätze nicht endgültig zugeordnet (offen oder Parkkonto)')
    hashes = {b['import_hash'] for b in bank if b['import_hash']}
    legacy = {b['source'] for b in bank if not b['import_hash']}  # vor 0.16 importiert: nur über den Dateinamen
    for imp in store.rows('SELECT * FROM imports ORDER BY created'):
        if imp['hash'] not in hashes and imp['filename'] not in legacy:
            continue
        found = [f for f in sorted((data_dir / 'Importe').glob(imp['hash'] + '.*')) if _inside(data_dir / 'Importe', f.name)]
        if not found:
            notes.append(f"Originaldatei des Kontoauszugs fehlt: {imp['filename']} (importiert {german_date(imp['created'][:10])})")
        for f in found:
            yield f"Kontoauszuege-Originale/{imp['created'][:10]}-{imp['hash'][:8]}-{_safe_name(imp['filename'])}", f.read_bytes()
    import sepadatei   # Belege zu aufgeteilten Sammelbuchungen (SEPA-Dateien), je Datei einmal
    files = sepadatei.files_for_year(store, year)
    notes += [f"Beleg zur SEPA-Sammelbuchung vom {f['booked']} wurde nicht gespeichert ({_safe_name(f['filename'])})" for f in files if not f['stored']]
    for name in dict.fromkeys(f['stored'] for f in files if f['stored']):
        source = _inside(data_dir / 'Belege', name)
        if source:
            yield f'SEPA-Sammler/{_safe_name(name)}', source.read_bytes()
        else:
            notes.append(f'Beleg zur SEPA-Sammelbuchung fehlt: {name}')

    import kasse
    shown = set()
    for k in kasse.liste(store):                              # je Kasse ein Kassenbuch; die Bargeldkasse heißt wie seit 0.25
        if not k['start'] or k['start'][:4] > y or not (k['aktiv'] or kasse.bewegt(store, k, y)):   # stillgelegt: nur Jahre mit Bewegungen
            continue
        shown.add(k['id'])
        cash = kasse.book(store, year, k)
        who = f" ({k['name']})" if k['id'] else ''
        yield f'{cash_name(k, y)}.csv', kasse.csv(store, year, k)
        yield f'{cash_name(k, y)}.pdf', cash_pdf(store, year, kasse_id=k['id'])
        for c in store.rows("SELECT bank_ref,attachment FROM bank WHERE ledger='cash' AND kasse=? AND attachment IS NOT NULL AND substr(booked,1,4)=? "
                            'ORDER BY booked,id', (k['id'], y)):
            source = _inside(data_dir / 'Belege', c['attachment'])
            if source:
                yield f"Kassenbelege/{_safe_name(c['attachment'])}", source.read_bytes()
            else:
                notes.append(f"Kassenbeleg {c['bank_ref']}: Belegdatei fehlt")
        if cash['open'] or cash['parked']:
            notes.append(f"{cash['open'] + cash['parked']} Kassenbelege{who} nicht endgültig verbucht (offen oder Parkkonto)")
        if cash['missing_receipt']:
            notes.append(f"{cash['missing_receipt']} Kassenausgaben{who} ohne hinterlegten Beleg (Quittung/Kassenbon prüfen)")
    if shown:                                                 # Geldtransit zum Jahresende (laufendes Jahr: heute), wie im Kassenbuch
        cash = _cash(store, min(f'{y}-12-31', date.today().isoformat()))
        notes += _transit_notes(cash, shown) + [f'Hinweis – {n}' for n in _minus_notes(cash, shown)]

    invoices = sorted((i for i in store.invoices() if i['issued'][:4] == y), key=lambda i: i['number'])
    yield f'Ausgangsrechnungen-{y}.csv', _csv([[i['number'], i['issued'], 'Beitrag' if i['kind'] == 'dues' else 'Rechnung', i['year'], i['recipient'],
                                                i['kv'], _eur(i['total']), _eur(i['paid']), 'storniert' if i['status'] == 'cancelled' else 'gestellt',
                                                i['due'], (i['sent_at'] or '')[:10], i['reminders']] for i in invoices],
                                              ['Nummer', 'Datum', 'Art', 'Beitragsjahr', 'Empfänger', 'Bereich', 'Betrag', 'Bezahlt', 'Status', 'Fällig',
                                               'Per E-Mail versendet', 'Erinnerungen'])
    parts = [invoices[i:i + INVOICES_PER_PDF] for i in range(0, len(invoices), INVOICES_PER_PDF)]
    for n, part in enumerate(parts, 1):
        yield (f'Ausgangsrechnungen-{y}.pdf' if len(parts) == 1 else f'Ausgangsrechnungen-{y}-Teil{n}.pdf'), \
            rechnung_pdf.render([rechnung_pdf.invoice_data(store, i['id']) for i in part])

    bills = sorted((b for b in verbindlichkeiten.bills(store) if b['issued'][:4] == y), key=lambda b: b['number'])
    yield f'Eingangsrechnungen-{y}.csv', _csv([[b['number'], b['issued'], freigabe.KINDS.get(b['kind'], b['kind']), b['creditor'], b['ext_number'],
                                                b['kv'], _eur(b['total']), _eur(b['paid']), _eur(b['open']), 'storniert' if b['status'] == 'cancelled' else 'erfasst',
                                                b['attachment'] or 'fehlt',
                                                'nicht nötig' if not b['approval']['required'] else 'vollständig' if b['approval']['complete'] else 'fehlt',
                                                (b['refund'] or {}).get('member', 'Antragsdaten fehlen' if b['kind'] == 'refund' else ''),
                                                (b['refund'] or {}).get('submitted_on', ''),
                                                ('Ausnahme: ' + b['refund']['late_reason'] if b['refund']['late'] else 'eingehalten') if b['refund'] else '']
                                               for b in bills],
                                              ['Beleg', 'Datum', 'Art', 'Kreditor', 'Rechnungsnr. Kreditor', 'Bereich', 'Betrag', 'Bezahlt', 'Offen', 'Status',
                                               'Belegdatei', 'Freigabe FO § 7 Abs. 4', 'Antragsteller (§ 8)', 'Antrag eingegangen', 'Drei-Monats-Frist'])
    for b in bills:
        if b['status'] == 'cancelled':
            continue
        source = _inside(data_dir / 'Belege', b['attachment']) if b['attachment'] else None
        if source:
            content = source.read_bytes()
            yield f"Belege/{b['number']}-{_safe_name(b['attachment'])}", content
            if source.suffix == '.xml':  # E-Rechnung: lesbare Fassung daneben
                try:
                    yield f"Belege/{b['number']}-Sichtfassung.pdf", erechnung.view_pdf(erechnung.read(content))
                except ValueError as e:
                    notes.append(f"E-Rechnung {b['number']} nicht lesbar: {e}")
        else:
            notes.append(f"Beleg fehlt: {b['number']} ({b['creditor']})")
        if b['approval']['unapproved_paid']:
            notes.append(f"{b['number']}: ohne vollständige Freigabe angewiesen/bezahlt")
        notes += [f"{b['number']}: {n}" for n in erstattung.findings(b)]
    approvals = store.rows('SELECT a.*,b.number FROM bill_approvals a JOIN bills b ON b.id=a.bill_id WHERE substr(b.issued,1,4)=? ORDER BY b.number,a.id', (y,))
    yield f'Freigaben-{y}.csv', _csv([[a['number'], freigabe.ROLES[a['role']], a['name'], a['approved_on'], a['how'], a['note'], a['created'], a['revoked']]
                                      for a in approvals],
                                     ['Beleg', 'Rolle', 'Name', 'Freigegeben am', 'Weg', 'Bemerkung', 'Eingetragen', 'Zurückgenommen'])
    for q in (1, 2, 3, 4):
        rep = freigabe.report(store, year, q)
        if not rep['bills'] and not rep['reviews']:
            continue
        yield f'Pruefprotokolle/Pruefprotokoll-Q{q}-{y}.pdf', freigabe.protocol_pdf(store, year, q)
        if rep['ended'] and rep['count'] and not rep['reviews']:
            notes.append(f"Quartalsprüfung {rep['label']} nicht protokolliert (FO § 7 Abs. 3)")

    mailings = store.rows('SELECT m.*,i.number FROM mailings m JOIN invoices i ON i.id=m.invoice_id WHERE substr(m.time,1,4)=? ORDER BY m.id', (y,))
    yield f'E-Mail-Protokoll-{y}.csv', _csv([[m['number'], 'Erinnerung' if m['kind'] == 'reminder' else 'Rechnung', m['time'], m['recipient'], m['subject'],
                                              m['status'], m['error']] for m in mailings],
                                            ['Rechnung', 'Art', 'Zeit', 'Empfänger', 'Betreff', 'Status', 'Fehler'])

    # Lastschriften nur als Liste mit gekürzter IBAN – keine hochladbare Datei im Paket (Festschreibung aus 0.15)
    batches = [b for b in lastschrift.batches(store) if b['created'][:4] == y]
    if batches:
        ibans = {m['id']: lastschrift.masked(m['iban']) for m in lastschrift.mandates(store)}
        yield f'Lastschriften-{y}.csv', _csv([[b['msg_id'], b['status'], b['submitted_on'], d['number'], d['recipient'], ibans.get(d['mandate_id'], ''),
                                               d['collection_date'], _eur(d['amount']), d['status'], d['note']]
                                              for b in batches for d in b['items']],
                                             ['Datei', 'Datei-Status', 'Eingereicht am', 'Rechnung', 'Mitglied', 'IBAN', 'Einzug', 'Betrag', 'Lastschrift', 'Hinweis'])
    mandates = [m for m in lastschrift.mandates(store) if m['created'][:4] <= y]
    if mandates:
        yield 'SEPA-Mandate.csv', _csv([[m['reference'], m['holder'], lastschrift.masked(m['iban']), m['signed_on'],
                                         'aktiv' if m['status'] == 'active' else 'widerrufen', m['revoked_on']] for m in mandates],
                                       ['Mandatsreferenz', 'Kontoinhaber', 'IBAN', 'Unterschrieben am', 'Status', 'Widerrufen am'])

    import beitraege
    import mitgliederkonto
    open_dues = beitraege.pending(store, year)
    if open_dues:
        notes.append(f"{len(open_dues)} Beitragszahlungen auf 8100 ({euro(sum(x['open'] + x['in_case'] for x in open_dues))}) keinem Mitglied zugeordnet"
                     f"{' (davon ' + str(sum(1 for x in open_dues if x['in_case'])) + ' als Klärfall bei der Mitgliederverwaltung)' if any(x['in_case'] for x in open_dues) else ''}"
                     ' – Mitgliederkonten und KV-Beitragsanteil unvollständig')
    if store.db.execute('SELECT 1 FROM members LIMIT 1').fetchone():
        yield f'Mitgliederkonten-{y}.csv', mitgliederkonto.overview_csv(store, year, today=min(f'{year}-12-31', date.today().isoformat()))

    import rpj
    if store.db.execute('SELECT 1 FROM rpj_years WHERE year=? UNION SELECT 1 FROM rpj_projects WHERE year=?', (year, year)).fetchone() \
            or rpj.entries(store, year):
        view = rpj.year_view(store, year)
        yield f'RPJ-{y}.pdf', rpj_pdf(store, year)
        yield f'RPJ-{y}.csv', rpj.year_csv(store, year)
        yield f'RPJ-Buchungen-{y}.csv', rpj.bookings_csv(store, year)
        for x in view['projects']:
            yield f"RPJ-Projekte/RPJ-{y}-Projekt-{x['no']:02d}.pdf", rpj_project_pdf(store, x['id'])
        if view['open']:
            notes.append(f"{view['open']} RPJ-Buchungen {year} keinem Projekt bzw. Mittelabruf zugeordnet – RPJ-Übersicht unvollständig")
        c = view['cards']
        if c['contingent'] is None:
            notes.append(f'RPJ {year}: Bescheid (Kontingent) nicht eingetragen')
        elif c['requested'] > c['contingent']:
            notes.append(f"RPJ {year}: Mittelabrufe über dem Kontingent")
        notes += [f"RPJ {year}: {r['label']} – {euro(r['open'])} noch nicht eingegangen" for r in view['requests'] if r['open'] > 0]
        notes += [f'RPJ {year}: {x}' for x in view['issues']]
        if c['balance'] and f'{year}-12-31' < date.today().isoformat():
            notes.append(f"RPJ {year}: Saldo {euro(c['balance'])} (eingegangen minus zurückgezahlt minus verwendet) – "
                         + ('nicht verwendete Mittel: Rückzahlung bzw. Verwendungsnachweis prüfen' if c['balance'] > 0 else 'mehr verwendet als eingegangen'))

    rows = []
    for code, led in kvbudget.overview(store).items():
        for line in led['lines']:
            if line['year'] == year:
                rows.append([names[code], line['day'], KV_KINDS.get(line['kind'], line['kind']), line['label'], line.get('recipient') or '',
                             _eur(line['amount']), _eur(line['balance']), line.get('journal') or ''])
    yield f'KV-Budgets-{y}.csv', _csv(rows, ['Bereich', 'Datum', 'Art', 'Bezeichnung', 'Empfänger', 'Betrag', 'Stand', 'Buchung'])
    closings = kvbudget.closings(store)
    if closings:
        yield 'KV-Budgetabschluesse.csv', _csv([[c['year'], names.get(c['kv'], c['kv']), _eur(c['carry_in']), _eur(c['share']), _eur(c['used']),
                                                 _eur(c['jan_used']), _eur(c['lapsed']), _eur(c['carried']), c['closed_on'], c['rule'],
                                                 'ja' if c['changed'] else 'nein'] for c in closings],
                                               ['Haushaltsjahr', 'Bereich', 'Übertrag ins Jahr', 'Zugänge', 'Ausgaben', 'Im Januar aus dem Rest verbraucht',
                                                'Verfallen', 'Vorgetragen', 'Abgeschlossen am', 'Regel', 'Seither geändert'])
        notes += [f"KV-Budgetabschluss {c['year']} {names.get(c['kv'], c['kv'])}: seither geänderte Buchungen" for c in closings if c['changed']]
    if year in kvbudget.closing_due(store):
        notes.append(f'KV-Budgetjahr {year} noch nicht abgeschlossen (FO § 6)')

    donations = kvbudget.donations(store, year)
    yield f'Spenden-{y}.csv', _csv([[d['day'], d['description'], d['account'], d['account_name'], names.get(d['kv'], d['kv']), _eur(d['amount']),
                                     _eur(d['kv_share']), d['until']] for d in donations],
                                   ['Datum', 'Text', 'Konto', 'Kontoname', 'Bereich', 'Betrag', 'Anteil Kreisverband', 'Anteil verfügbar bis'])
    cash = [c for c in kvbudget.cash_list(store) if c['received_on'][:4] == y]
    if cash:
        yield f'Barspenden-{y}.csv', _csv([[c['received_on'], c['donor'], _eur(c['amount']), names.get(c['kv'], c['kv']), c['receiver1'], c['receiver2'],
                                            c['receipt'], c['deposited_on'], c['cdu_informed_on'], c['note'], '; '.join(c['issues'])] for c in cash],
                                          ['Datum', 'Spender', 'Betrag', 'Bereich', 'Entgegengenommen von', 'und von', 'Quittung', 'Eingezahlt am',
                                           'CDU informiert am', 'Bemerkung', 'Hinweise'])
        notes += [f"Barspende {c['receipt']}: {', '.join(c['issues'])}" for c in cash if c['overdue'] or not c['cdu_informed_on']
                  or (c['deposited_on'] and c['deposited_on'] > c['deadline'])]

    audit = store.rows('SELECT * FROM audit WHERE substr(time,1,4)=? ORDER BY id', (y,))
    yield f'Aenderungsprotokoll-{y}.csv', _csv([[a['id'], a['time'], a['actor'], a['action'], a['detail']] for a in audit],
                                               ['Nr.', 'Zeit', 'Benutzer', 'Aktion', 'Einzelheiten'])


@exclusive
def _overview_pdf(store, year, names, notes):
    import haushalt
    cfg, st = store.config(), _styles()
    p = _para(st)
    t = _totals(haushalt.report(store, year))
    end_day = min(f'{year}-12-31', date.today().isoformat())
    story = [p(f'Kassenprüfung Haushaltsjahr {year}', 'title'),
             p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · erstellt am {german_date(date.today().isoformat())} · Unterlagen nach Satzung § 19 Abs. 2", 'small'),
             p('Überblick', 'h2'),
             _table([['', 'Betrag'],
                     [f'Saldo gebuchter Bankbewegungen am {german_date(f"{year - 1}-12-31")}', euro(balance(store, BANK, f'{year - 1}-12-31'))],
                     ['Einnahmen (Ist, Haushalt)', _money(t['income'][3])], ['Ausgaben (Ist, Haushalt)', _money(t['expense'][3])],
                     ['Ergebnis (Ist, Haushalt)', _money(t['result'][3])],
                     [f'Saldo gebuchter Bankbewegungen am {german_date(end_day)}', euro(balance(store, BANK, end_day))]], [300, 120], st, numeric=(1,)),
             p('Ergebnis vor CDU-Verrechnung, Rücklagen und Abgrenzungen – kein endgültiger Jahresabschluss. Der Saldo enthält nur gebuchte '
               'Kontobewegungen (ein Anfangsbestand nur, wenn er gebucht ist) und ist mit dem Kontoauszug der Bank abzugleichen. Buchungsjournal '
               'und Haushalt folgen dem Haushaltsjahr der Buchung, Kontoumsätze dem Buchungsdatum der Bank – bei Buchungen mit abweichendem '
               'Haushaltsjahr (z. B. aus der Excel-Übernahme) ergibt Anfangssaldo plus Ergebnis daher nicht genau den Endsaldo.', 'small'),
             p('Automatische Hinweise', 'h2')]
    story += [p('• ' + n) for n in notes] or [p('Keine automatischen Hinweise.')]
    story += [p('Inhalt', 'h2')] + [p('• ' + n, 'small') for n in names + ['00-Uebersicht.pdf']]
    story.append(p('CSV-Dateien: Semikolon, Beträge in Euro mit Komma (Ausgaben negativ, Soll/Haben je Buchungszeile). '
                   'Belegdateien beginnen mit der Belegnummer der Eingangsrechnung.', 'small'))
    return _pdf(story, f'Kassenprüfung {year}')
