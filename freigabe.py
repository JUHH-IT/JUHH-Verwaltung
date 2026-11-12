"""Freigabe und Prüfung von Eingangsrechnungen nach der Finanzordnung der JU Hamburg (Stand 13.03.2024).

§ 7 Abs. 4: Fremdrechnungen über 500,00 € (Grenze in den Stammdaten) werden erst beglichen, wenn Landesvorsitz und
Landesschatzmeister sie freigegeben haben. Das Programm dokumentiert beide Freigaben (wer, wann, auf welchem Weg) und
sperrt bis dahin Zahlungsvorschlag und „als überwiesen markieren“. Eine schon erfolgte Abbuchung wird trotzdem
zugeordnet (die Bank muss stimmen) und in der Quartalsprüfung als Auffälligkeit ausgewiesen.
§ 7 Abs. 3 / § 8 Abs. 8: Der Landesschatzmeister prüft alle Rechnungen eines Quartals binnen vier Wochen nach
Quartalsende und protokolliert die Prüfung schriftlich – hier als Prüfprotokoll (PDF) zum Einreichen beim LGF.
Auslagenerstattungen (§ 8) fallen nicht unter die Freigabegrenze, wohl aber unter die Quartalsprüfung.
"""
import io
import json
import sqlite3
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from formate import euro, german_date, iso
from pdfschrift import SANS, SANS_BOLD, exclusive

ROLES = {'chair': 'Landesvorsitz', 'treasurer': 'Landesschatzmeister'}
HOW = ('E-Mail', 'schriftlich', 'Vorstandssitzung', 'mündlich')
KINDS = {'external': 'Fremdrechnung', 'refund': 'Auslagenerstattung'}
REVIEW_DAYS = 28  # „innerhalb von vier Wochen nach dem jeweiligen Quartalsabschluss“
MAX_LIMIT = 50000  # § 7 Abs. 4: über EUR 500,00 – die Stammdaten dürfen die Grenze nur senken


def init(db):
    cols = [c[1] for c in db.execute('PRAGMA table_info(bills)')]
    if 'kind' not in cols:
        db.execute("ALTER TABLE bills ADD COLUMN kind TEXT NOT NULL DEFAULT 'external' CHECK(kind IN ('external','refund'))")
    if 'approval_limit' not in cols:
        # Freigabegrenze bei Erfassung festgeschrieben – spätere Änderungen der Stammdaten wirken nicht zurück
        db.execute(f'ALTER TABLE bills ADD COLUMN approval_limit INTEGER NOT NULL DEFAULT {MAX_LIMIT} CHECK(approval_limit>0)')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS bill_approvals(id INTEGER PRIMARY KEY,bill_id INTEGER NOT NULL REFERENCES bills(id),
        role TEXT NOT NULL CHECK(role IN ('chair','treasurer')),name TEXT NOT NULL,approved_on TEXT NOT NULL,how TEXT NOT NULL,
        note TEXT,actor TEXT,created TEXT NOT NULL,revoked TEXT,revoked_by TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS bill_approvals_active ON bill_approvals(bill_id,role) WHERE revoked IS NULL;
    CREATE TABLE IF NOT EXISTS bill_reviews(id INTEGER PRIMARY KEY,year INTEGER NOT NULL,quarter INTEGER NOT NULL CHECK(quarter BETWEEN 1 AND 4),
        reviewed_on TEXT NOT NULL,reviewer TEXT NOT NULL,result TEXT NOT NULL CHECK(result IN ('ok','remarks')),remarks TEXT,
        bills INTEGER NOT NULL,total INTEGER NOT NULL,findings INTEGER NOT NULL,snapshot TEXT NOT NULL,actor TEXT,created TEXT NOT NULL);
    ''')
    db.commit()


# ---------------------------------------------------------------- Freigabe (§ 7 Abs. 4)

def limit(store):
    """Grenze für neu erfasste Eingangsrechnungen (Stammdaten)."""
    return int(store.config()['approval_limit'])


def _active(store, bill_id=None):
    sql = 'SELECT * FROM bill_approvals WHERE revoked IS NULL' + (' AND bill_id=?' if bill_id else '') + ' ORDER BY id'
    found = {}
    for r in store.rows(sql, (bill_id,) if bill_id else ()):
        found.setdefault(r['bill_id'], {})[r['role']] = r
    return found


def status(bill, given, paid_on=None):
    """Freigabestand einer Eingangsrechnung (given: aktive Freigaben je Rolle; paid_on: erste Zahlung/Anweisung).

    Bleibt nachvollziehbar: Wurde vor der Freigabe gezahlt oder die Freigabe erst nach der Zahlung eingetragen,
    steht das dauerhaft in late ('before' bzw. 'recorded_after') – eine nachgetragene Freigabe löscht es nicht."""
    lim = bill['approval_limit']
    required = bill['kind'] == 'external' and bill['total'] > lim
    missing = [r for r in ROLES if r not in given] if required else []
    paid = paid_on or bill['transferred']
    if bill['transferred'] and paid_on:
        paid = min(paid_on, bill['transferred'])
    late = None
    if required and not missing and paid:
        if max(a['approved_on'] for a in given.values()) > paid:
            late = 'before'
        elif max(a['created'][:10] for a in given.values()) > paid:
            late = 'recorded_after'
    return {'required': required, 'missing': missing, 'complete': required and not missing, 'limit': lim, 'paid_on': paid,
            'unapproved_paid': bool(missing and paid), 'late': late,
            'given': {r: {k: a[k] for k in ('id', 'name', 'approved_on', 'how', 'note', 'created')} for r, a in given.items()}}


def _paid_on(store, bill_id=None):
    """Datum der ersten zugeordneten (nicht stornierten) Abbuchung je Eingangsrechnung."""
    sql = ("SELECT p.bill_id,MIN(k.booked) day FROM bill_payments p JOIN bank k ON k.id=p.bank_id WHERE k.state='booked'"
           + (' AND p.bill_id=?' if bill_id else '') + ' GROUP BY p.bill_id')
    return {r['bill_id']: r['day'] for r in store.rows(sql, (bill_id,) if bill_id else ())}


def annotate(store, rows):
    """Ergänzt die Zeilen aus verbindlichkeiten.bills um den Freigabestand."""
    active, paid = _active(store), _paid_on(store)
    for r in rows:
        r['approval'] = status(r, active.get(r['id'], {}), paid.get(r['id']))
    return rows


def bill_status(store, bill_id):
    b = store.db.execute('SELECT * FROM bills WHERE id=?', (bill_id,)).fetchone()
    return status(b, _active(store, bill_id).get(bill_id, {}), _paid_on(store, bill_id).get(bill_id)) if b else None


def check_payable(store, bill):
    """Vor Zahlungsvorschlag und Anweisung: fehlende Freigabe nach § 7 Abs. 4 → ValueError."""
    st = bill_status(store, bill['id'])
    if st['missing']:
        raise ValueError(f"{bill['number']}: Fremdrechnung über {euro(st['limit'])} – Freigabe nach § 7 Abs. 4 Finanzordnung fehlt "
                         f"({', '.join(ROLES[r] for r in st['missing'])}).")


def _text(value, label, limit_=120, required=True, lines=False):
    v = ('\n'.join(' '.join(l.split()) for l in str(value or '').strip().splitlines()) if lines
         else ' '.join(str(value or '').split()))
    if required and not v:
        raise ValueError(f'Bitte {label} angeben.')
    if len(v) > limit_:
        raise ValueError(f'{label}: höchstens {limit_} Zeichen.')
    return v or None


def approve(store, bill_id, role, data, actor):
    if role not in ROLES:
        raise ValueError('Unbekannte Rolle.')
    name = _text(data.get('name'), 'den Namen')
    day = iso(data.get('approved_on') or '')
    today = date.today()
    if not (today - timedelta(days=730)).isoformat() <= day <= today.isoformat():
        raise ValueError('Datum der Freigabe: nicht in der Zukunft und höchstens zwei Jahre zurück.')
    how = str(data.get('how') or '')
    if how not in HOW:
        raise ValueError('Bitte angeben, wie freigegeben wurde (' + ', '.join(HOW) + ').')
    note = _text(data.get('note'), 'Bemerkung', 300, required=False)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b = store.db.execute('SELECT * FROM bills WHERE id=?', (bill_id,)).fetchone()
        if not b or b['status'] != 'open':
            raise ValueError('Eingangsrechnung ist nicht offen.')
        given = _active(store, bill_id).get(bill_id, {})
        if not status(b, given)['required']:
            raise ValueError(f"{b['number']} braucht keine Freigabe (nur Fremdrechnungen über {euro(b['approval_limit'])}).")
        if role in given:
            raise ValueError(f"{ROLES[role]} hat {b['number']} bereits freigegeben.")
        other = [a for r, a in given.items() if r != role]
        if other and other[0]['name'].casefold() == name.casefold():
            raise ValueError('Beide Freigaben müssen von verschiedenen Personen kommen (Landesvorsitz und Landesschatzmeister).')
        try:
            store.db.execute('INSERT INTO bill_approvals(bill_id,role,name,approved_on,how,note,actor,created) VALUES(?,?,?,?,?,?,?,?)',
                             (bill_id, role, name, day, how, note, actor, datetime.now().isoformat(timespec='seconds')))
        except sqlite3.IntegrityError:
            raise ValueError(f"{ROLES[role]} hat {b['number']} bereits freigegeben.") from None
        store.audit(actor, 'Freigabe Eingangsrechnung dokumentiert', {'bill': b['number'], 'role': ROLES[role], 'name': name,
                                                                       'approved_on': day, 'how': how})


def revoke(store, approval_id, actor):
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        a = store.db.execute('SELECT * FROM bill_approvals WHERE id=? AND revoked IS NULL', (approval_id,)).fetchone()
        if not a:
            raise ValueError('Freigabe nicht gefunden.')
        import verbindlichkeiten
        b = store.db.execute('SELECT * FROM bills WHERE id=?', (a['bill_id'],)).fetchone()
        if b['transferred'] or verbindlichkeiten.paid(store, b['id']) > 0:
            raise ValueError('Nach Anweisung oder Zahlung lässt sich die Freigabe nicht mehr zurücknehmen.')
        store.db.execute('UPDATE bill_approvals SET revoked=?,revoked_by=? WHERE id=?', (date.today().isoformat(), actor, approval_id))
        store.audit(actor, 'Freigabe Eingangsrechnung zurückgenommen', {'bill': b['number'], 'role': ROLES[a['role']], 'name': a['name']})


# ---------------------------------------------------------------- Quartalsprüfung (§ 7 Abs. 3, § 8 Abs. 8)

def quarter_of(day):
    d = date.fromisoformat(day)
    return d.year, (d.month - 1) // 3 + 1


def bounds(year, quarter):
    if quarter not in (1, 2, 3, 4):
        raise ValueError('Quartal 1 bis 4 wählen.')
    start = date(year, 3 * quarter - 2, 1)
    end = (date(year + 1, 1, 1) if quarter == 4 else date(year, 3 * quarter + 1, 1)) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def deadline(year, quarter):
    return (date.fromisoformat(bounds(year, quarter)[1]) + timedelta(days=REVIEW_DAYS)).isoformat()


def _state(b):
    if b['status'] == 'cancelled':
        return 'storniert'
    if b['open'] == 0:
        return 'bezahlt'
    if b['paid'] > 0:
        return 'teilweise bezahlt'
    return 'angewiesen' if b['transferred'] else 'offen'


def _findings(b):
    if b['status'] == 'cancelled':
        return []
    a, found = b['approval'], []
    if not b['attachment']:
        found.append('Beleg fehlt')
    if a['unapproved_paid']:
        found.append('ohne vollständige Freigabe angewiesen/bezahlt')
    elif a['missing']:
        found.append('Freigabe fehlt')
    elif a['late'] == 'before':
        found.append('Zahlung vor Freigabe')
    elif a['late'] == 'recorded_after':
        found.append('Freigabe erst nach Zahlung eingetragen')
    if b['kind'] == 'refund' and b['total'] > b['approval_limit']:
        found.append(f"Auslagenerstattung über {euro(b['approval_limit'])} – Art prüfen")
    if b['payment_reversed'] and b['open'] > 0:
        found.append('Zahlung storniert – klären')
    import erstattung
    return found + erstattung.findings(b)


def report(store, year, quarter):
    import verbindlichkeiten
    start, end = bounds(year, quarter)
    rows = sorted((b for b in verbindlichkeiten.bills(store) if start <= b['issued'] <= end), key=lambda b: (b['issued'], b['number']))
    items = []
    for b in rows:
        items.append({'id': b['id'], 'number': b['number'], 'issued': b['issued'], 'creditor': b['creditor'], 'ext_number': b['ext_number'],
                      'kind': b['kind'], 'total': b['total'], 'kv': b['kv'], 'titles': sorted({l['title'] for l in b['lines']}),
                      'state': _state(b), 'cancelled': b['status'] == 'cancelled', 'attachment': bool(b['attachment']),
                      'approval': b['approval'], 'findings': _findings(b)})
    live = [i for i in items if not i['cancelled']]
    reviews = _reviews(store, year, quarter)
    seen = {i for r in reviews for i in r['ids']}
    for i in items:
        i['reviewed'] = i['id'] in seen
    return {'year': year, 'quarter': quarter, 'label': f'Q{quarter}/{year}', 'start': start, 'end': end,
            'deadline': deadline(year, quarter), 'ended': end < date.today().isoformat(), 'limit': limit(store),
            'bills': items, 'count': len(live), 'total': sum(i['total'] for i in live),
            'required': sum(1 for i in live if i['approval']['required']),
            'approved': sum(1 for i in live if i['approval']['complete']),
            'findings': sum(len(i['findings']) for i in items),
            'unreviewed': sum(1 for i in live if not i['reviewed']) if reviews else 0,
            'reviews': [{k: v for k, v in r.items() if k not in ('snapshot', 'ids')} for r in reviews]}


def _reviews(store, year, quarter):
    """Protokollierte Prüfungen mit dem festgeschriebenen Stand (snapshot) und den geprüften Rechnungs-IDs."""
    out = []
    for r in store.rows('SELECT * FROM bill_reviews WHERE year=? AND quarter=? ORDER BY id', (year, quarter)):
        r['snapshot'] = json.loads(r['snapshot'])
        r['ids'] = {i['id'] for i in r['snapshot']['bills']}
        r['late'] = r['reviewed_on'] > deadline(year, quarter)
        out.append(r)
    return out


def record_review(store, year, quarter, data, actor):
    rep = report(store, year, quarter)
    if not rep['ended']:
        raise ValueError(f"{rep['label']} ist noch nicht abgeschlossen – geprüft wird nach Quartalsende.")
    reviewer = _text(data.get('reviewer'), 'den Namen des Prüfers')
    day = iso(data.get('reviewed_on') or '')
    if not rep['end'] < day <= date.today().isoformat():
        raise ValueError('Prüfdatum: nach Quartalsende und nicht in der Zukunft.')
    result = data.get('result')
    if result not in ('ok', 'remarks'):
        raise ValueError('Ergebnis wählen: ohne Beanstandungen oder mit Bemerkungen.')
    remarks = _text(data.get('remarks'), 'die Bemerkungen', 2000, required=result == 'remarks', lines=True)
    # Stand der Prüfung festschreiben: das Protokoll zeigt später genau das, was geprüft wurde
    snapshot = json.dumps({k: rep[k] for k in ('bills', 'count', 'total', 'required', 'approved', 'findings')}, ensure_ascii=False)
    with store.db:
        store.db.execute('INSERT INTO bill_reviews(year,quarter,reviewed_on,reviewer,result,remarks,bills,total,findings,snapshot,actor,created) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (year, quarter, day, reviewer, result, remarks, rep['count'], rep['total'],
                                                             rep['findings'], snapshot, actor, datetime.now().isoformat(timespec='seconds')))
        store.audit(actor, 'Quartalsprüfung Eingangsrechnungen protokolliert', {'quarter': rep['label'], 'reviewer': reviewer,
                                                                                 'result': result, 'bills': rep['count']})


def due(store, today=None):
    """Abgeschlossene Quartale mit Eingangsrechnungen, deren Prüfung noch nicht protokolliert ist (älteste zuerst)."""
    today = today or date.today().isoformat()
    quarters = {}
    for r in store.rows("SELECT id,issued FROM bills WHERE status='open'"):
        quarters.setdefault(quarter_of(r['issued']), set()).add(r['id'])
    seen = {}
    for r in store.rows('SELECT year,quarter,snapshot FROM bill_reviews'):
        seen.setdefault((r['year'], r['quarter']), set()).update(i['id'] for i in json.loads(r['snapshot'])['bills'])
    out = []
    for (y, q), ids in sorted(quarters.items()):
        todo = ids - seen.get((y, q), set())  # nach der Prüfung erfasste Rechnungen → Nachprüfung
        if not todo or bounds(y, q)[1] >= today:
            continue
        out.append({'year': y, 'quarter': q, 'label': f'Q{q}/{y}', 'count': len(todo), 'recheck': (y, q) in seen,
                    'deadline': deadline(y, q), 'overdue': deadline(y, q) < today})
    return out


def pending(store):
    """Offene Fremdrechnungen, denen noch eine Freigabe fehlt."""
    import verbindlichkeiten
    return [b['number'] for b in verbindlichkeiten.bills(store) if b['status'] == 'open' and b['open'] > 0 and b['approval']['missing']]


# ---------------------------------------------------------------- Prüfprotokoll (PDF)

def protocol_basis(store, year, quarter, rep=None):
    """Grundlage des Protokolls: festgeschriebener Stand der letzten Prüfung (sonst aktueller Stand als Entwurf)
    und die danach erfassten, noch nicht geprüften Rechnungen."""
    rep = rep or report(store, year, quarter)
    reviews = _reviews(store, year, quarter)
    review = reviews[-1] if reviews else None
    basis = review['snapshot'] if review else rep
    later = [i for i in rep['bills'] if not i['cancelled'] and i['id'] not in review['ids']] if review else []
    return basis, review, later


@exclusive
def protocol_pdf(store, year, quarter):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepTogether, LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    rep, cfg = report(store, year, quarter), store.config()
    basis, review, later = protocol_basis(store, year, quarter, rep)
    navy, muted, line, tint = (colors.HexColor(c) for c in ('#043770', '#5b6b7f', '#d5dde8', '#eef3f9'))
    st = {
        'title': ParagraphStyle('t', fontName=SANS_BOLD, fontSize=15, leading=19, textColor=navy),
        'body': ParagraphStyle('b', fontName=SANS, fontSize=9.5, leading=13.5),
        'small': ParagraphStyle('s', fontName=SANS, fontSize=8, leading=10.5, textColor=muted),
        'cell': ParagraphStyle('c', fontName=SANS, fontSize=8, leading=10),
        'num': ParagraphStyle('n', fontName=SANS, fontSize=8, leading=10, alignment=2),
        'head': ParagraphStyle('h', fontName=SANS_BOLD, fontSize=8, leading=10, textColor=muted),
        'h2': ParagraphStyle('h2', fontName=SANS_BOLD, fontSize=10.5, leading=14, textColor=navy, spaceBefore=6),
    }
    p = lambda text, style='cell': Paragraph(escape(str(text)).replace('\n', '<br/>'), st[style])

    def approval_text(a):
        if not a['required']:
            return 'nicht nötig'
        parts = [f"{ROLES[r]}: {g['name']}, {german_date(g['approved_on'])} ({g['how']})" for r, g in a['given'].items()]
        parts += [f'{ROLES[r]}: fehlt' for r in a['missing']]
        return '\n'.join(parts)

    head = ['Beleg', 'Datum', 'Kreditor · Art', 'Betrag', 'Freigabe (§ 7 Abs. 4)', 'Stand', 'Auffälligkeiten']
    data = [[p(h, 'head') for h in head]]
    for i in basis['bills']:
        data.append([p(i['number'] + (f"\n{i['ext_number']}" if i['ext_number'] else '')), p(german_date(i['issued'])),
                     p(f"{i['creditor']}\n{KINDS[i['kind']]}"), p(euro(i['total']), 'num'), p(approval_text(i['approval'])),
                     p(i['state']), p(', '.join(i['findings']) or '—')])
    if len(data) == 1:
        data.append([p('Keine Eingangsrechnungen mit Rechnungsdatum in diesem Quartal.')] + [''] * 6)
    table = LongTable(data, colWidths=[64, 48, 112, 56, 112, 52, 71], repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BACKGROUND', (0, 0), (-1, 0), tint),
                               ('LINEBELOW', (0, 0), (-1, -1), 0.4, line), ('TOPPADDING', (0, 0), (-1, -1), 4),
                               ('BOTTOMPADDING', (0, 0), (-1, -1), 4), ('LEFTPADDING', (0, 0), (-1, -1), 3),
                               ('RIGHTPADDING', (0, 0), (-1, -1), 3)]))
    issuer = cfg.get('issuer') or 'Junge Union Hamburg'
    state = f"Stand der Prüfung vom {german_date(review['reviewed_on'])}" if review else f"ENTWURF · Stand {german_date(date.today().isoformat())}"
    story = [p(f"Prüfprotokoll Eingangsrechnungen {rep['label']}", 'title'), Spacer(1, 4),
             p(f"{issuer} · Rechnungsdatum {german_date(rep['start'])} – {german_date(rep['end'])} · Prüffrist {german_date(rep['deadline'])} · {state}", 'small'),
             p('Grundlage: Finanzordnung § 7 Abs. 3 (Fremdrechnungen) und § 8 Abs. 8 (Auslagenerstattungen); '
               f"Freigabe von Fremdrechnungen über {euro(MAX_LIMIT)} nach § 7 Abs. 4 (Grenze je Rechnung bei Erfassung festgeschrieben).", 'small'), Spacer(1, 10),
             Table([[p('Rechnungen', 'head'), p('Summe', 'head'), p('freigabepflichtig', 'head'), p('davon freigegeben', 'head'), p('Auffälligkeiten', 'head')],
                    [p(basis['count'], 'body'), p(euro(basis['total']), 'body'), p(basis['required'], 'body'), p(basis['approved'], 'body'), p(basis['findings'], 'body')]],
                   colWidths=[103] * 5, hAlign='LEFT',
                   style=[('BACKGROUND', (0, 0), (-1, -1), tint), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]),
             Spacer(1, 12), table, Spacer(1, 14)]
    if later:
        story += [p('Nach dieser Prüfung erfasst – nicht Gegenstand dieses Protokolls (Nachprüfung nötig)', 'h2'),
                  p('\n'.join(f"{i['number']} · {german_date(i['issued'])} · {i['creditor']} · {euro(i['total'])}" for i in later), 'body'), Spacer(1, 10)]
    if review:
        verdict = 'ohne Beanstandungen' if review['result'] == 'ok' else 'mit Bemerkungen'
        block = [p('Ergebnis der Prüfung', 'h2'),
                 p(f"Geprüft am {german_date(review['reviewed_on'])} von {review['reviewer']}: {verdict}."
                   + (' Die Prüffrist war zu diesem Zeitpunkt abgelaufen.' if review['late'] else ''), 'body')]
        if review['remarks']:
            block.append(p('Bemerkungen: ' + review['remarks'], 'body'))
    else:
        block = [p('Ergebnis der Prüfung', 'h2'), p('ENTWURF – die Prüfung ist noch nicht protokolliert.', 'body')]
    block += [Spacer(1, 30), Table([['', '', ''], [p('Ort, Datum', 'small'), '', p(f"Unterschrift Landesschatzmeister{' · ' + review['reviewer'] if review else ''}", 'small')]],
                                   colWidths=[180, 40, 255], hAlign='LEFT',
                                   style=[('LINEABOVE', (0, 1), (0, 1), 0.6, colors.black), ('LINEABOVE', (2, 1), (2, 1), 0.6, colors.black)]),
              Spacer(1, 6), p('Einzureichen beim Landesgeschäftsführer (§ 7 Abs. 3 Satz 2 Finanzordnung).', 'small')]
    story.append(KeepTogether(block))

    def footer(canvas, doc):
        canvas.setFont(SANS, 7)
        canvas.setFillColor(muted)
        canvas.drawString(40, 22, f"{issuer} · Prüfprotokoll {rep['label']} · erstellt {german_date(date.today().isoformat())}")
        canvas.drawRightString(555, 22, f'Seite {doc.page}')

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=36, bottomMargin=40,
                      title=f"Prüfprotokoll {rep['label']}").build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
