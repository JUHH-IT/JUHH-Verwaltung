"""Erstattung finanzieller Aufwendungen (Finanzordnung § 8) – Antragsdaten zur Eingangsrechnung der Art „refund“.

§ 8: Antrag eines Mitglieds (Abs. 2) über das Formular mit Nachweis (Abs. 3); erstattet wird nur, was unmittelbar
mit der Wahrnehmung von JU-Aufgaben entstanden ist und wozu das Mitglied befugt war (Abs. 4), wenn der Antrag
vollständig, unterschrieben und binnen drei Monaten nach Entstehung der Kosten eingereicht ist (Abs. 5) und keine
andere Erstattungsmöglichkeit besteht (Abs. 6). Überwiesen wird an den Antragsteller (Abs. 7) – über den normalen
Zahlungsvorschlag. Die Quartalsprüfung (Abs. 8) läuft über freigabe.report; findings() liefert die Hinweise dazu.

Das Rechnungsdatum der Eingangsrechnung ist bei Erstattungen das Datum der (frühesten) Ausgabe – davon hängen
Haushaltsjahr und Drei-Monats-Frist ab. Die offizielle Vorlage (Anhang I) fehlt in der Finanzordnung; form_pdf()
erzeugt ein Antragsformular mit denselben Angaben.
"""
from datetime import date, datetime, timedelta

from formate import add_months, german_date, iso

MONTHS = 3
PAY_DAYS = 14
CHECKS = {'signed': 'Antrag vom Antragsteller unterschrieben (§ 8 Abs. 5)',
          'complete': 'Antrag vollständig ausgefüllt, Nachweis liegt bei (§ 8 Abs. 3, 5)',
          'authorized': 'Ausgabe für JU-Aufgaben entstanden und befugt (§ 8 Abs. 4)',
          'no_other': 'keine andere Erstattungsmöglichkeit (§ 8 Abs. 6)'}


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS refunds(bill_id INTEGER PRIMARY KEY REFERENCES bills(id),member_id INTEGER NOT NULL REFERENCES members(id),
        submitted_on TEXT NOT NULL,signed INTEGER NOT NULL,complete INTEGER NOT NULL,authorized INTEGER NOT NULL,no_other INTEGER NOT NULL,
        late_reason TEXT,created TEXT NOT NULL,actor TEXT);
    ''')
    db.commit()


def _yes(value):
    return value is True or str(value).lower() in ('1', 'true', 'on', 'yes', 'ja')


def deadline(cost_date):
    return add_months(cost_date, MONTHS)


def prepare(store, data):
    """Vor der Prüfung der Eingangsrechnung: bei Erstattungen ist der Kreditor immer der Antragsteller (§ 8 Abs. 7) –
    ein anderer Name (etwa der Rechnungssteller des Belegs) wird nicht übernommen."""
    if data.get('kind') == 'refund':
        try:
            m = store.db.execute('SELECT name FROM members WHERE id=?', (int(data.get('member_id') or 0),)).fetchone()
        except (TypeError, ValueError):
            m = None
        data = {**data, 'creditor': m['name'] if m else ''}
    return data


def clean(store, data, bill, has_file):
    """Antragsdaten prüfen (bill: geprüfte Eingangsrechnung aus verbindlichkeiten._clean). Liefert die Werte für save()."""
    try:
        member_id = int(data.get('member_id') or 0)
    except (TypeError, ValueError):
        member_id = 0
    m = store.db.execute('SELECT * FROM members WHERE id=?', (member_id,)).fetchone() if member_id else None
    if not m:
        raise ValueError('§ 8 Abs. 2: Erstattet wird nur auf Antrag eines Mitglieds – bitte den Antragsteller wählen.')
    cost = bill['issued']
    if m['joined'] > cost or (m['left_on'] and m['left_on'] < cost):
        raise ValueError(f"{m['name']} war am {german_date(cost)} kein Mitglied (§ 8 Abs. 2).")
    submitted = iso(str(data.get('submitted_on') or ''))
    if not cost <= submitted <= date.today().isoformat():
        raise ValueError('Antrag eingegangen am: nicht vor der Ausgabe und nicht in der Zukunft.')
    missing = [label for key, label in CHECKS.items() if not _yes(data.get(key))]
    if missing:
        raise ValueError('Erstattet wird nur, wenn bestätigt ist: ' + '; '.join(missing) + '.')
    if not has_file:
        raise ValueError('§ 8 Abs. 3: Bitte den Nachweis (Kassenbeleg, Rechnung) als PDF oder Foto anhängen.')
    if not bill['iban']:
        raise ValueError('§ 8 Abs. 7: Erstattet wird per Überweisung an den Antragsteller – bitte seine IBAN angeben.')
    late_reason = ' '.join(str(data.get('late_reason') or '').split())[:300] or None
    late = submitted > deadline(cost)
    if late and not late_reason:
        raise ValueError(f'§ 8 Abs. 5: Antrag mehr als drei Monate nach der Ausgabe eingereicht (Frist bis {german_date(deadline(cost))}). '
                         'Nur mit dokumentierter Ausnahme erfassen – Begründung angeben.')
    return {'member_id': member_id, 'submitted_on': submitted, 'late_reason': late_reason if late else None,
            'due': (date.fromisoformat(submitted) + timedelta(days=PAY_DAYS)).isoformat()}


def save(store, bill_id, values, actor):
    """Innerhalb der Transaktion von verbindlichkeiten.create."""
    store.db.execute('INSERT INTO refunds(bill_id,member_id,submitted_on,signed,complete,authorized,no_other,late_reason,created,actor) '
                     'VALUES(?,?,?,1,1,1,1,?,?,?)', (bill_id, values['member_id'], values['submitted_on'], values['late_reason'],
                                                      datetime.now().isoformat(timespec='seconds'), actor))


def annotate(store, rows):
    """Ergänzt Eingangsrechnungen der Art „refund“ um die Antragsdaten (refund: dict oder None, wenn vor 0.17 erfasst)."""
    found = {r['bill_id']: r for r in store.rows('SELECT r.*,m.name member,m.number member_number FROM refunds r JOIN members m ON m.id=r.member_id')}
    for b in rows:
        r = found.get(b['id']) if b['kind'] == 'refund' else None
        if r:
            r['deadline'] = deadline(b['issued'])
            r['late'] = r['submitted_on'] > r['deadline']
        b['refund'] = r
    return rows


def findings(b):
    """Hinweise für die Quartalsprüfung (§ 8 Abs. 8)."""
    if b['kind'] != 'refund' or b['status'] == 'cancelled':
        return []
    r = b.get('refund')
    if not r:
        return ['Antragsdaten nach § 8 nicht erfasst (vor Version 0.17)']
    return [f"Antrag nach Ablauf von drei Monaten – Ausnahme: {r['late_reason']}"] if r['late'] else []


# ---------------------------------------------------------------- Antragsformular

def form_pdf(store, member_id=None):
    """Antrag auf Erstattung finanzieller Aufwendungen – vorausgefüllt mit dem Mitglied oder leer."""
    from pdfschrift import exclusive
    return exclusive(_form_pdf)(store, member_id)


def _form_pdf(store, member_id):
    import io
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from pdfschrift import SANS, SANS_BOLD
    cfg = store.config()
    m = store.db.execute('SELECT * FROM members WHERE id=?', (member_id,)).fetchone() if member_id else None
    navy, muted, line = colors.HexColor('#043770'), colors.HexColor('#5b6b7f'), colors.HexColor('#9aa8b8')
    st = {'t': ParagraphStyle('t', fontName=SANS_BOLD, fontSize=14, leading=18, textColor=navy),
          'h': ParagraphStyle('h', fontName=SANS_BOLD, fontSize=10, leading=14, spaceBefore=10, spaceAfter=4),
          'b': ParagraphStyle('b', fontName=SANS, fontSize=9.5, leading=13),
          's': ParagraphStyle('s', fontName=SANS, fontSize=7.5, leading=10, textColor=muted)}
    p = lambda text, style='b': Paragraph(escape(str(text)).replace('\n', '<br/>'), st[style])
    field = lambda label, value='': [p(label, 's'), p(value or ' ')]
    grid = TableStyle([('LINEBELOW', (1, 0), (1, -1), 0.5, line), ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
                       ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)])
    person = Table([field('Name, Vorname', m['name'] if m else ''), field('Mitgliedsnummer', m['number'] if m else ''),
                    field('Anschrift', m['address'] if m else ''), field('E-Mail', (m['email'] or '') if m else ''),
                    field('Kontoinhaber', ''), field('IBAN', '')], colWidths=[130, 350], style=grid)
    costs = Table([[p('Datum', 's'), p('Anlass / Zweck der Ausgabe', 's'), p('Betrag in €', 's')]] + [['', '', '']] * 6,
                  colWidths=[80, 300, 100], rowHeights=[16] + [22] * 6,
                  style=TableStyle([('GRID', (0, 0), (-1, -1), 0.5, line), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef3f9'))]))
    box = '□'  # in Liberation Sans enthalten
    confirm = [p(f'{box}  {text}') for text in (
        'Die Ausgaben sind unmittelbar bei der Wahrnehmung meiner Aufgaben als Mitglied bzw. im Rahmen der Tätigkeit der Jungen Union '
        'Hamburg entstanden; ich war zur Ausgabe befugt (§ 8 Abs. 4).',
        'Die Nachweise (Kassenbelege, Rechnungen) liegen im Original bei (§ 8 Abs. 3).',
        'Es besteht keine andere Erstattungsmöglichkeit, und ich habe für diese Ausgaben keine andere Erstattung beantragt (§ 8 Abs. 6).')]
    office = Table([field('Eingegangen am', ''), field('Sachlich geprüft (Name)', ''), field('Belegnummer im Programm', ''), field('Überwiesen am', '')],
                   colWidths=[160, 320], style=grid)
    story = [p('Antrag auf Erstattung finanzieller Aufwendungen', 't'),
             p(f"an die Landesgeschäftsführung der {cfg.get('issuer') or 'Jungen Union Hamburg'} · Finanzordnung § 8", 's'),
             p('Antragsteller', 'h'), person, p('Ausgaben', 'h'), costs, Spacer(1, 4),
             p('Summe: ____________ €', 'b'), p('Bestätigungen', 'h')] + confirm + [
             Spacer(1, 6),
             p('Erstattet wird nur ein vollständig ausgefüllter und unterschriebener Antrag, der innerhalb von drei Monaten nach Entstehung der '
               'Kosten eingereicht wird (§ 8 Abs. 5). Ein Rechtsanspruch auf Erstattung besteht nicht (§ 8 Abs. 9).', 's'),
             Spacer(1, 30),
             Table([['', '', ''], [p('Ort, Datum', 's'), '', p('Unterschrift Antragsteller', 's')]], colWidths=[180, 40, 260],
                   style=TableStyle([('LINEABOVE', (0, 1), (0, 1), 0.6, colors.black), ('LINEABOVE', (2, 1), (2, 1), 0.6, colors.black)])),
             p('Vermerk Landesgeschäftsführung', 'h'), office,
             Spacer(1, 8), p('Vorlage aus dem Finanzprogramm – ersetzt das Formular in Anhang I der Finanzordnung, solange dieses nicht vorliegt.', 's')]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=55, rightMargin=55, topMargin=45, bottomMargin=40,
                      title='Antrag auf Erstattung finanzieller Aufwendungen').build(story)
    return buf.getvalue()
