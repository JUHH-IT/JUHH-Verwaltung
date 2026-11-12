"""Rechnungs-PDF (A4, Briefaufbau nach DIN 5008 Form B) – einzeln, als Sammel-PDF oder als Vorschau.

Eine Rechnung wird als dict beschrieben (invoice_data für festgeschriebene, draft für Vorschauen),
damit PDF und E-Mail dieselben Angaben verwenden. Die Angaben des Ausstellers stammen aus dem
mit der Rechnung festgeschriebenen Abbild (snapshot), nicht aus den aktuellen Stammdaten.
"""
import io
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

import lastschrift
import stammdaten
from pdfschrift import SANS, SANS_BOLD, exclusive
from formate import euro, german_date, iban_compact, iban_valid, iban_pretty

PAGE_W, PAGE_H = 595.28, 841.89
LEFT, RIGHT, TOP, BOTTOM = 25 * mm, 20 * mm, 15 * mm, 32 * mm
LOGO = Path(__file__).resolve().parent / 'static' / 'img' / 'logo-rechnung.png'
NAVY, TEAL, INK, MUTED, LINE, TINT = (colors.HexColor(c) for c in
                                      ('#043770', '#087f8c', '#1c2a3a', '#5b6b7f', '#d5dde8', '#eef3f9'))

S = {
    'title': ParagraphStyle('title', fontName=SANS_BOLD, fontSize=16, leading=20, textColor=NAVY),
    'body': ParagraphStyle('body', fontName=SANS, fontSize=10, leading=14.5, textColor=INK),
    'small': ParagraphStyle('small', fontName=SANS, fontSize=8, leading=11, textColor=MUTED),
    'th': ParagraphStyle('th', fontName=SANS_BOLD, fontSize=8, leading=10, textColor=MUTED),
    'cell': ParagraphStyle('cell', fontName=SANS, fontSize=10, leading=13, textColor=INK),
    'num': ParagraphStyle('num', fontName=SANS, fontSize=10, leading=13, textColor=INK, alignment=2),
    'total': ParagraphStyle('total', fontName=SANS_BOLD, fontSize=11, leading=14, textColor=NAVY),
    'totalnum': ParagraphStyle('totalnum', fontName=SANS_BOLD, fontSize=11, leading=14, textColor=NAVY, alignment=2),
    'pay': ParagraphStyle('pay', fontName=SANS, fontSize=9.5, leading=13.5, textColor=INK),
}


def _p(text, style='body'):
    return Paragraph(escape(str(text)).replace('\n', '<br/>'), S[style])


def invoice_data(store, iid):
    """Festgeschriebene Rechnung aus der Datenbank."""
    inv = store.db.execute('SELECT * FROM invoices WHERE id=?', (iid,)).fetchone()
    if not inv:
        raise ValueError('Rechnung nicht gefunden.')
    data = dict(inv)
    data['snap'] = json.loads(inv['snapshot'])
    data['lines'] = store.rows('SELECT description,amount FROM lines WHERE invoice_id=? ORDER BY id', (iid,))
    data['preview'] = False
    debit = lastschrift.info(store, data)
    data['debit_active'] = bool(debit and debit['active'])
    data['pay_by'] = debit.get('pay_by') if debit else None
    return data


def debit_of(inv):
    """Vorabankündigung der Lastschrift, solange sie gilt (lastschrift.info: nicht zurückgegeben, Mandat gültig, Datum nicht
    verpasst), sonst None – dann zahlt das Mitglied per Überweisung (GiroCode)."""
    d = inv['snap'].get('debit')
    return d if d and inv.get('debit_active', True) else None


def draft(kind, cfg, recipient, address, lines, due, issued, year=None, member_number=None, number=None, basis=None, debit=None):
    """Noch nicht festgeschriebene Rechnung für Vorschau (PDF und E-Mail)."""
    from core import Store
    snap = Store.snapshot_for(kind, cfg, member_number)
    if basis:
        snap['dues_basis'] = basis
    if debit:
        snap['debit'] = debit
        due = debit['date']
    return {'id': None, 'number': number or 'Vorschau', 'kind': kind, 'year': year, 'recipient': recipient,
            'address': address, 'issued': issued, 'due': due, 'total': sum(l['amount'] for l in lines),
            'status': 'issued', 'snap': snap, 'lines': [{'description': l['description'], 'amount': l['amount']} for l in lines],
            'preview': True}


def epc(name, iban, bic, cents, purpose):
    """GiroCode-Inhalt (EPC069-12, Version 002, UTF-8) für eine SEPA-Überweisung."""
    one_line = lambda text: ' '.join(str(text or '').split())  # Zeilenumbrüche würden die Feldfolge zerstören
    fields = ['BCD', '002', '1', 'SCT', one_line(bic), one_line(name)[:70], iban_compact(iban),
              f"EUR{cents / 100:.2f}", '', '', one_line(purpose)[:140]]
    while len('\n'.join(fields).encode('utf-8')) > 331 and fields[-1]:  # Höchstlänge laut EPC069-12
        fields[-1] = fields[-1][:-1]
    return '\n'.join(fields)


def amount_due(inv):
    """Zu zahlender Betrag: Rechnungsbetrag abzüglich bereits zugeordneter Zahlungen (beim Versand bekannt)."""
    return inv['total'] - (inv.get('paid') or 0)


def epc_payload(inv):
    """GiroCode über den offenen Betrag oder None, wenn nicht möglich (bezahlt, storniert, IBAN ungültig)."""
    snap = inv['snap']
    if amount_due(inv) <= 0 or inv['status'] != 'issued' or not iban_valid(snap.get('iban')) or debit_of(inv):
        return None
    return epc(snap.get('issuer'), snap['iban'], snap.get('bic'), amount_due(inv), inv['number'] + ' ' + (inv['recipient'] or ''))


def qr_svg(payload, size=180):
    """GiroCode als SVG (für die Anzeige im Programm)."""
    from reportlab.graphics import renderSVG
    return renderSVG.drawToString(_qr(payload, size=size))


def _qr(payload, size=30 * mm):
    widget = QrCodeWidget(payload, barLevel='M')
    x1, y1, x2, y2 = widget.getBounds()
    d = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    d.add(widget)
    return d


def title_of(inv):
    title = f"Beitragsrechnung {inv['year']}" if inv['kind'] == 'dues' else 'Rechnung'
    return ('Storniert: ' + title) if inv['status'] == 'cancelled' else title


class Start(Flowable):
    """Unsichtbare Marke: ab hier gilt diese Rechnung für Fußzeile und Seitenzählung."""

    def __init__(self, inv):
        super().__init__()
        self.inv = inv

    def wrap(self, *_):
        return 0, 0

    def draw(self):
        doc = self.canv._doctemplate
        doc.current, doc.first_page = self.inv, self.canv.getPageNumber()


SENDER_WIDTH = 85 * mm       # Rücksendeangabe nur über dem Anschriftfeld (DIN 5008: 85 mm breit), nie in den Informationsblock
SENDER_SIZES = (7, 6.5, 6)   # Schriftgrößen, bevor Teile der Anschrift entfallen


def sender_line(snap, width=SENDER_WIDTH):
    """Absenderzeile (Rücksendeangabe) passend zur Breite des Anschriftfelds: (Text, Schriftgröße).
    Erst kleiner (bis 6 pt), dann entfallen Zwischenzeilen der Anschrift (z. B. „Landesgeschäftsstelle“, Gebäudename) –
    Name, Straße und PLZ/Ort bleiben; reicht das nicht, wird gekürzt (…)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines = [l.strip() for l in (snap.get('issuer_address') or '').splitlines() if l.strip()]
    head = [x for x in [(snap.get('issuer') or '').strip()] if x]
    candidates = [head + lines]
    for drop in range(1, max(len(lines) - 1, 1)):           # Zeilen vor Straße und PLZ/Ort nach und nach weglassen
        candidates.append(head + lines[drop:])
    for parts in candidates:
        text = ' · '.join(parts)
        for size in SENDER_SIZES:
            if stringWidth(text, SANS, size) <= width:
                return text, size
    size = SENDER_SIZES[-1]                                   # zuletzt den Namen kürzen, Straße und PLZ/Ort bleiben ganz
    tail = ' · '.join(lines[-2:])
    room = width - stringWidth(' · ' + tail, SANS, size)
    if head and tail and room >= 15 * mm:
        return fit(head[0], size, room) + ' · ' + tail, size
    return fit(' · '.join(candidates[-1]), size, width), size


# Spalten der Fußzeile: 25–85 mm, 85–140 mm, 140–190 mm (rechter Rand) – je Spalte die nutzbare Breite
FOOTER_WIDTHS = (56 * mm, 51 * mm, 50 * mm)
FOOTER_WIDTH = FOOTER_WIDTHS[0]


def fit(text, size, width, font=SANS):
    """Text auf eine Breite kürzen (…), statt in die Nachbarspalte zu laufen."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + ' …', font, size) > width:
        text = text[:-1]
    return text.rstrip(' ·,') + ' …'


def footer_address(snap, width=FOOTER_WIDTH, size=7):
    """Anschrift für die Fußzeile in höchstens drei Zeilen: bei mehr Zeilen erst Straße und PLZ/Ort zusammenfassen,
    dann Zwischenzeilen (z. B. „Landesgeschäftsstelle“) weglassen – PLZ/Ort fehlt nie."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines = [l.strip() for l in (snap.get('issuer_address') or '').splitlines() if l.strip()]
    merged = False                                            # Straße und PLZ/Ort höchstens einmal zusammenfassen
    while len(lines) > 3:
        joined = f'{lines[-2]} · {lines[-1]}'
        if not merged and stringWidth(joined, SANS, size) <= width:
            lines[-2:] = [joined]
            merged = True
        else:
            del lines[0]
    return [fit(l, size, width) for l in lines]


class Letterhead(Flowable):
    """Logo, Absenderzeile, Anschriftfeld und Informationsblock (oberer Briefbereich bis 100 mm)."""
    HEIGHT = 85 * mm

    def __init__(self, inv):
        super().__init__()
        self.inv = inv

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, self.HEIGHT

    def draw(self):
        c, inv, snap = self.canv, self.inv, self.inv['snap']
        y = lambda from_top: self.HEIGHT - (from_top * mm - TOP)  # Abstand vom Blattrand oben → lokale Koordinate
        x = lambda from_left: from_left * mm - LEFT
        if LOGO.exists():
            w = 62 * mm
            h = w * 171 / 900
            c.drawImage(str(LOGO), x(190) - w, y(15) - h, width=w, height=h, mask='auto')
        # Absenderzeile im Anschriftfeld (Rücksendeangabe)
        sender, size = sender_line(snap)
        c.setFont(SANS, size)
        c.setFillColor(MUTED)
        c.drawString(x(25), y(50), sender)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.4)
        c.line(x(25), y(50) - 2, x(25) + min(c.stringWidth(sender, SANS, size), SENDER_WIDTH), y(50) - 2)
        # Anschrift
        c.setFillColor(INK)
        lines = [inv['recipient']] + [l for l in inv['address'].splitlines() if l.strip()]
        text = c.beginText(x(25), y(58))
        text.setFont(SANS, 10)
        text.setLeading(13)
        for line in lines[:8]:
            text.textLine(fit(line.strip(), 10, SENDER_WIDTH))   # Anschriftfeld 85 mm – nie in den Informationsblock
        c.drawText(text)
        # Informationsblock
        rows = [('Rechnungsnummer', inv['number']), ('Rechnungsdatum', german_date(inv['issued']))]
        if inv['status'] == 'issued' and inv['total'] > 0:
            rows.append(('Einzug am', german_date(debit_of(inv)['date'])) if debit_of(inv) else ('Zahlbar bis', german_date(lastschrift.payable_by(inv))))
        if snap.get('member_number'):
            rows.append(('Mitgliedsnummer', snap['member_number']))
        if inv['kind'] == 'dues':
            rows.append(('Beitragsjahr', str(inv['year'])))
        if snap.get('contact_email'):
            rows.append(('Kontakt', snap['contact_email']))
        top = 50
        for label, value in rows:
            c.setFont(SANS, 8)
            c.setFillColor(MUTED)
            c.drawString(x(125), y(top), label)
            c.setFont(SANS_BOLD if label == 'Rechnungsnummer' else SANS, 9)
            c.setFillColor(INK)
            c.drawRightString(x(190), y(top), str(value))
            top += 5.2


def _lines_table(inv, width):
    rows = [[_p('Pos.', 'th'), _p('Beschreibung', 'th'), Paragraph('Betrag', ParagraphStyle('thr', parent=S['th'], alignment=2))]]
    for i, l in enumerate(inv['lines'], 1):
        text = str(l['description'] or '')
        rows.append([_p(str(i), 'cell'), _p(text if len(text) <= 600 else text[:600] + ' …', 'cell'), _p(euro(l['amount']), 'num')])   # sehr lange Altbestände kürzen
    if not inv['lines']:
        rows.append([_p('1', 'cell'), _p(f"Mitgliedsbeitrag {inv['year']} – beitragsfrei" if inv['kind'] == 'dues' else 'Keine Positionen', 'cell'), _p(euro(0), 'num')])
    rows.append(['', _p('Gesamtbetrag', 'total'), _p(euro(inv['total']), 'totalnum')])
    t = Table(rows, colWidths=[12 * mm, width - 12 * mm - 32 * mm, 32 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), TINT),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, NAVY),
        ('LINEBELOW', (0, 1), (-1, -3), 0.3, LINE),
        ('LINEABOVE', (0, -1), (-1, -1), 0.9, NAVY),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, -1), (-1, -1), 8),
    ]))
    return t


def _debit_box(inv, width, debit):
    text = (f"Du musst nichts tun: Wir ziehen <b>{escape(euro(amount_due(inv)))}</b> am <b>{escape(german_date(debit['date']))}</b> "
            f"per SEPA-Lastschrift von deinem Konto <b>{escape(debit['iban'])}</b> ein. Bitte sorge für ausreichende Deckung.<br/><br/>"
            + '<br/>'.join(f'<font color="#5b6b7f">{escape(k)}:</font> {escape(v)}' for k, v in (
                ('Kontoinhaber', debit['holder']), ('Mandatsreferenz', debit['mandate']), ('Gläubiger-ID', debit['creditor_id']),
                ('Verwendungszweck', inv['number']))))
    t = Table([[Paragraph(text, S['pay'])]], colWidths=[width])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), TINT), ('LEFTPADDING', (0, 0), (-1, -1), 10),
                           ('RIGHTPADDING', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10)]))
    return t


def _payment_box(inv, width):
    snap = inv['snap']
    if debit_of(inv) and not inv.get('reminder'):
        return _debit_box(inv, width, debit_of(inv))
    facts = [('Empfänger', snap.get('issuer', '')), ('IBAN', iban_pretty(snap.get('iban', '')))]
    if snap.get('bic'):
        facts.append(('BIC', snap['bic']))
    if snap.get('bank_name'):
        facts.append(('Bank', snap['bank_name']))
    due, deadline = amount_due(inv), (inv.get('reminder') or {}).get('deadline') or lastschrift.payable_by(inv)
    if inv.get('paid'):
        facts += [('Rechnungsbetrag', euro(inv['total'])), ('Bereits bezahlt', euro(inv['paid']))]
    facts += [('Offener Betrag' if inv.get('paid') else 'Betrag', euro(due)), ('Verwendungszweck', inv['number'])]
    text = (f"Bitte überweise <b>{escape(euro(due))}</b> bis zum <b>{escape(german_date(deadline))}</b> "
            f"und gib als Verwendungszweck <b>{escape(inv['number'])}</b> an.<br/><br/>"
            + '<br/>'.join(f'<font color="#5b6b7f">{escape(k)}:</font> {escape(v)}' for k, v in facts))
    left = Paragraph(text, S['pay'])
    payload = epc_payload(inv)
    if payload:
        right = [_qr(payload), Paragraph('GiroCode – mit der Banking-App scannen',
                                         ParagraphStyle('qrcap', parent=S['small'], fontSize=6.5, leading=8, alignment=1))]
        t = Table([[left, right]], colWidths=[width - 40 * mm, 40 * mm])
    else:
        t = Table([[left]], colWidths=[width])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), TINT), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('ALIGN', (-1, 0), (-1, 0), 'CENTER'),
    ]))
    return t


def _story(inv, width):
    snap = inv['snap']
    values = stammdaten.invoice_values(inv)
    story = [Start(inv), Letterhead(inv), _p(title_of(inv), 'title'), Spacer(1, 6 * mm)]
    greeting = values['vorname']
    story.append(_p(f'Hallo {greeting},' if snap.get('member_number') else (f'Guten Tag {greeting},' if greeting else 'Guten Tag,')))
    story.append(Spacer(1, 2 * mm))
    if snap.get('pdf_text'):
        story += [_p(stammdaten.fill(snap['pdf_text'], values)), Spacer(1, 5 * mm)]
    story += [_lines_table(inv, width), Spacer(1, 7 * mm)]
    if inv['status'] == 'cancelled':
        story.append(_p('Diese Rechnung wurde storniert. Es ist keine Zahlung erforderlich.'))
    elif inv['total'] > 0:
        story.append(KeepTogether([_payment_box(inv, width)]))
    else:
        story.append(_p('Es fällt kein Betrag an. Es ist keine Zahlung erforderlich.'))
    story.append(Spacer(1, 6 * mm))
    if snap.get('dues_basis'):
        story.append(_p('Beitragsberechnung: ' + snap['dues_basis'] + '.', 'small'))
    if snap.get('tax_note'):
        story.append(_p(snap['tax_note'], 'small'))
    if snap.get('pilot'):
        story += [Spacer(1, 3 * mm), _p('Pilotbetrieb: Vor Versand fachlich prüfen.', 'small')]
    story += [Spacer(1, 8 * mm), _p('Vielen Dank!\n' + (snap.get('issuer') or ''))]
    return story


def _page_end(canvas, doc):
    inv = getattr(doc, 'current', None)
    if not inv:
        return
    snap = inv['snap']
    canvas.saveState()
    if inv.get('preview') or inv['status'] == 'cancelled':
        canvas.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.10) if inv['status'] == 'cancelled' else colors.Color(0.02, 0.2, 0.45, alpha=0.08))
        canvas.setFont(SANS_BOLD, 84)
        canvas.translate(PAGE_W / 2, PAGE_H / 2)
        canvas.rotate(35)
        canvas.drawCentredString(0, 0, 'STORNIERT' if inv['status'] == 'cancelled' else 'VORSCHAU')
        canvas.restoreState()
        canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.5)
    canvas.line(LEFT, 26 * mm, PAGE_W - RIGHT, 26 * mm)
    columns = [
        [snap.get('issuer', '')] + footer_address(snap),
        ['Bankverbindung'] + [v for v in (snap.get('bank_name'), 'IBAN ' + iban_pretty(snap.get('iban', '')), 'BIC ' + snap['bic'] if snap.get('bic') else '') if v],
        ['Kontakt'] + [v for v in (snap.get('contact_email'), snap.get('contact_phone'), snap.get('website')) if v],
    ]
    for i, (col_x, lines, col_w) in enumerate(zip((LEFT, 85 * mm, 140 * mm), columns, FOOTER_WIDTHS)):
        if i == 2 and len(lines) == 1:
            continue
        for j, line in enumerate(lines[:4]):
            canvas.setFont(SANS_BOLD if j == 0 else SANS, 7)
            canvas.setFillColor(INK if j == 0 else MUTED)
            canvas.drawString(col_x, 22 * mm - j * 3.4 * mm, fit(line, 7, col_w, SANS_BOLD if j == 0 else SANS))
    canvas.setFont(SANS, 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(LEFT, 8 * mm, inv['number'])
    canvas.drawRightString(PAGE_W - RIGHT, 8 * mm, f'Seite {canvas.getPageNumber() - doc.first_page + 1}')
    canvas.restoreState()


@exclusive
def render(invoices):
    """Eine oder mehrere Rechnungen (dicts) als ein PDF; jede beginnt auf einer neuen Seite."""
    if not invoices:
        raise ValueError('Keine Rechnungen ausgewählt.')
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=(PAGE_W, PAGE_H), leftMargin=LEFT, rightMargin=RIGHT, topMargin=TOP,
                          bottomMargin=BOTTOM, title=invoices[0]['number'] if len(invoices) == 1 else 'Rechnungen',
                          author=invoices[0]['snap'].get('issuer', ''), creator='JU Hamburg Finanzen')
    frame = Frame(LEFT, BOTTOM, PAGE_W - LEFT - RIGHT, PAGE_H - TOP - BOTTOM, id='body', leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='brief', frames=[frame], onPageEnd=_page_end)])
    story = []
    for i, inv in enumerate(invoices):
        if i:
            story.append(PageBreak())
        story += _story(inv, frame._width)
    doc.build(story)
    return buf.getvalue()


def filename(inv):
    return f"{inv['number']}.pdf" if not inv.get('preview') else 'Vorschau.pdf'
