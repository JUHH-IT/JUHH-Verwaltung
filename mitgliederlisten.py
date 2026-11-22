"""Listen der Mitgliederverwaltung (ab 0.43, Wunsch Lennert Gust 21.11.2026): eine Auswahl aus der Mitgliederliste als CSV, als
Excel mit einer Datei je Kreisverband (ab 0.45, zum Weiterleiten; mehrere als ZIP), als Adressetiketten (Zweckform 3474, 70 × 37 mm, 24 je Blatt) oder als
Anschreiben (PDF, ein Brief je Mitglied).

Enthält Kontaktdaten – vertraulich: Abruf mit „Mitglieder lesen“ und Passwortbestätigung (app.CONFIRM), im Änderungsprotokoll nur Art und
Anzahl. Post (Etiketten, Anschreiben) bekommt nur, wer eine vollständige Anschrift und keinen Postausschluss hat; die Oberfläche nennt die
übrigen vorher. Welche Mitglieder in einer Liste stehen, entscheidet die Oberfläche (static/js/mitglieder-listen.js).
"""
import io
import re
from datetime import date
from xml.sax.saxutils import escape

from formate import german_date
from pdfschrift import exclusive

MAX = 5000
ARTEN = {'csv': ('text/csv; charset=utf-8', 'csv'), 'excel': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'),
         'etiketten': ('application/pdf', 'pdf'), 'anschreiben': ('application/pdf', 'pdf'), 'mandate': ('application/pdf', 'pdf')}
NAMEN = {'csv': 'Mitgliederliste', 'excel': 'Mitgliederliste', 'etiketten': 'Adressetiketten', 'anschreiben': 'Anschreiben', 'mandate': 'SEPA-Mandate'}
MAX_BETREFF, MAX_TEXT = 200, 5000


def auswahl(store, ids):
    """Mitglieder zu den Kennungen der Auswahl (sortiert nach Name) – prüft die Eingabe: Liste ganzer Zahlen, höchstens MAX, alle vorhanden."""
    if not isinstance(ids, list) or not ids or len(ids) > MAX or not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < 2 ** 62 for i in ids):
        raise ValueError('Bitte Mitglieder auswählen.')
    ids = sorted(set(ids))
    rows = []
    for start in range(0, len(ids), 500):   # SQLite: höchstens 999 Platzhalter je Abfrage
        part = ids[start:start + 500]
        rows += store.rows(f"SELECT * FROM members WHERE id IN ({','.join('?' * len(part))})", part)
    if len(rows) != len(ids):
        raise ValueError('Mitglied nicht gefunden.')
    return sorted(rows, key=lambda m: ((m['last_name'] or m['name'] or '').casefold(), (m['first_name'] or '').casefold(), m['id']))


def postfaehig(m):
    """Bekommt Post: vollständige Anschrift (Straße, PLZ, Ort) und kein Postausschluss."""
    return bool(m['street'] and m['zip'] and m['city']) and not m['no_mail']


def anschrift(m):
    """Zeilen für Etikett und Anschriftfeld: Anrede, Titel und Name, Adresszusätze, Straße, PLZ Ort, Land."""
    import mitglieder
    name, address = mitglieder.compose(dict(m))
    first = ' '.join(x for x in (m['title'], name) if x)
    return [x for x in [m['salutation'], first, *address.splitlines()] if x and x.strip()]


KOPF = ['Mitgliedsnummer', 'Anrede', 'Titel', 'Vorname', 'Nachname', 'Briefanrede', 'Adresszusatz', 'Straße', 'PLZ', 'Ort', 'Land',
        'E-Mail', 'Telefon', 'Handy', 'Kreisverband', 'Ortsverband', 'Geburtsdatum', 'Eintritt', 'Austritt', 'Postausschluss']


def _zeilen(store, members):
    """Zeilen für CSV und Excel in der Spaltenfolge von KOPF."""
    import gliederung
    from core import KVS
    names, ovs = dict(KVS), {o['id']: gliederung.label(o) for o in gliederung.ovs(store)}
    return [[m['number'], m['salutation'], m['title'], m['first_name'], m['last_name'], m['letter_salutation'], m['address_extra'],
             m['street'], m['zip'], m['city'], m['country'], m['email'], m['phone'], m['mobile'], names.get(m['kv'], m['kv']),
             ovs.get(m['ov_id'], ''), german_date(m['birth']), german_date(m['joined']), german_date(m['left_on']),
             'ja' if m['no_mail'] else ''] for m in members]


def csv(store, members):
    """Mitglieder der Auswahl mit Anschrift und Kontakt als CSV (Semikolon, Formelschutz wie die anderen Listen)."""
    import berichte
    return berichte._csv(_zeilen(store, members), KOPF)


def _xlsx(rows):
    """Eine Excel-Datei (ein Blatt) mit Kopfzeile, Filter und Spaltenbreiten; jede Zelle Text, Steuerzeichen entfallen."""
    import openpyxl
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Font
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = 'Mitglieder'
    sheet.append(KOPF)
    for row in rows:
        sheet.append([ILLEGAL_CHARACTERS_RE.sub('', str(v)) if v else '' for v in row])
    for cells in sheet.iter_rows():
        for cell in cells:
            cell.data_type = 's'
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    for i, title in enumerate(KOPF, 1):
        width = max(len(str(sheet.cell(r, i).value or '')) for r in range(1, sheet.max_row + 1))
        sheet.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(max(width, len(title)) + 2, 40)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def excel(store, members):
    """Auswahl als Excel zum Weiterleiten an die Kreisverbände (ab 0.45): je Kreisverband eine eigene Datei – so bekommt kein
    Kreisverband die Daten eines anderen; mehrere Kreisverbände als ZIP. → (Inhalt, Endung 'xlsx' oder 'zip')."""
    import zipfile
    kv = KOPF.index('Kreisverband')
    groups = {}
    for row in _zeilen(store, members):
        groups.setdefault(row[kv], []).append(row)
    if len(groups) == 1:
        return _xlsx(next(iter(groups.values()))), 'xlsx'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, rows in sorted(groups.items()):
            teil = re.sub(r'\W+', '-', name).strip('-')   # „Altona / Elbvororte“ → „Altona-Elbvororte“
            z.writestr(f'Mitgliederliste-{teil}.xlsx', _xlsx(rows))
    return out.getvalue(), 'zip'


@exclusive
def etiketten(members):
    """Adressetiketten (Zweckform 3474: 3 × 8 Etiketten à 70 × 37 mm auf A4) – nur Mitglieder, die Post bekommen."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from pdfschrift import SANS
    from rechnung_pdf import fit
    post = [m for m in members if postfaehig(m)]
    if not post:
        raise ValueError('Keines der ausgewählten Mitglieder bekommt Post (Anschrift unvollständig oder Postausschluss).')
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle('Adressetiketten')
    c.setCreator('JU Hamburg Finanzen')
    w, h, top = 70 * mm, 37 * mm, (A4[1] - 8 * 37 * mm) / 2
    for n, m in enumerate(post):
        if n and n % 24 == 0:
            c.showPage()
        col, row = n % 3, n % 24 // 3
        x, y = col * w + 6 * mm, A4[1] - top - row * h - 9 * mm
        lines = anschrift(m)[:6]
        size = 10 if len(lines) <= 4 else 9
        c.setFont(SANS, size)
        for i, line in enumerate(lines):
            c.drawString(x, y - i * (size + 2), fit(line, size, w - 10 * mm))
    c.save()
    return buf.getvalue()


def _text(value, label, limit):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} fehlt.')
    value = value.replace('\r\n', '\n').strip()
    if len(value) > limit:
        raise ValueError(f'{label}: höchstens {limit} Zeichen.')
    return value


@exclusive
def anschreiben(cfg, members, betreff, text, today=None):
    """Ein Brief je Mitglied (DIN 5008): Absender, Anschriftfeld, Datum, Betreff, Briefanrede (sonst „Hallo Vorname,“), Text, Gruß."""
    from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer
    from reportlab.lib.units import mm
    import rechnung_pdf as r
    from pdfschrift import SANS
    betreff, text = _text(betreff, 'Betreff', MAX_BETREFF), _text(text, 'Text', MAX_TEXT)
    post = [m for m in members if postfaehig(m)]
    if not post:
        raise ValueError('Keines der ausgewählten Mitglieder bekommt Post (Anschrift unvollständig oder Postausschluss).')
    day = german_date((today or date.today()).isoformat())

    class Kopf(r.Flowable):
        HEIGHT = 85 * mm

        def __init__(self, m):
            super().__init__()
            self.m = m

        def wrap(self, avail_width, avail_height):
            return avail_width, self.HEIGHT

        def draw(self):
            c = self.canv
            y = lambda from_top: self.HEIGHT - (from_top * mm - r.TOP)
            x = lambda from_left: from_left * mm - r.LEFT
            if r.LOGO.exists():
                lw = 62 * mm
                c.drawImage(str(r.LOGO), x(190) - lw, y(15) - lw * 171 / 900, width=lw, height=lw * 171 / 900, mask='auto')
            sender, size = r.sender_line(cfg)
            c.setFont(SANS, size)
            c.setFillColor(r.MUTED)
            c.drawString(x(25), y(50), sender)
            c.setFillColor(r.INK)
            lines = c.beginText(x(25), y(58))
            lines.setFont(SANS, 10)
            lines.setLeading(13)
            for line in anschrift(self.m)[:8]:
                lines.textLine(r.fit(line, 10, r.SENDER_WIDTH))
            c.drawText(lines)
            c.setFont(SANS, 9)
            c.drawRightString(x(190), y(50), f'Hamburg, {day}')

    def fuss(canvas, doc):
        canvas.saveState()
        canvas.setFont(SANS, 7)
        canvas.setFillColor(r.MUTED)
        parts = [cfg.get('issuer') or ''] + r.footer_address(cfg, width=120 * mm) + [cfg.get('contact_email') or '']
        canvas.drawString(r.LEFT, 14 * mm, r.fit(' · '.join(p for p in parts if p), 7, r.PAGE_W - r.LEFT - r.RIGHT))
        canvas.restoreState()

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=(r.PAGE_W, r.PAGE_H), leftMargin=r.LEFT, rightMargin=r.RIGHT, topMargin=r.TOP, bottomMargin=24 * mm,
                          title=betreff, author=cfg.get('issuer') or '', creator='JU Hamburg Finanzen')
    frame = Frame(r.LEFT, 24 * mm, r.PAGE_W - r.LEFT - r.RIGHT, r.PAGE_H - r.TOP - 24 * mm, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='brief', frames=[frame], onPage=fuss)])
    body = [Paragraph(escape(block).replace('\n', '<br/>'), r.S['body']) for block in text.split('\n\n') if block.strip()]
    story = []
    for n, m in enumerate(post):
        greeting = (m['letter_salutation'] or '').strip() or f"Hallo {m['first_name'] or m['name']}"
        story += [Kopf(m), Paragraph(f'<b>{escape(betreff)}</b>', r.S['body']), Spacer(1, 8 * mm),
                  Paragraph(escape(greeting.rstrip(',')) + ',', r.S['body']), Spacer(1, 3 * mm)]
        for p in body:
            story += [p, Spacer(1, 3 * mm)]
        story += [Spacer(1, 4 * mm), Paragraph('Viele Grüße', r.S['body']), Paragraph(escape(cfg.get('issuer') or ''), r.S['body'])]
        if n < len(post) - 1:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


def datei(store, data, actor):
    """Abruf der Oberfläche: {art, ids, betreff?, text?} → (Inhalt, Medientyp, Dateiname); im Protokoll nur Art und Anzahl."""
    art = data.get('art')
    if art not in ARTEN:
        raise ValueError('Unbekannte Liste.')
    members = auswahl(store, data.get('ids'))
    zipped = False
    if art == 'csv':
        body = csv(store, members)
    elif art == 'excel':
        body, ext = excel(store, members)
        zipped = ext == 'zip'
    elif art == 'etiketten':
        body = etiketten(members)
    elif art == 'mandate':   # ab 0.53: Mandatsformulare zum Unterschreiben, vorausgefüllt mit den Bankdaten des Mitglieds
        import lastschrift
        if not lastschrift.enabled(store.config()):
            raise ValueError('Für Mandatsformulare zuerst die Gläubiger-ID in den Stammdaten (Verein & Bank) eintragen.')
        body = lastschrift.mandate_pdf(store, member_ids=[m['id'] for m in members])
    else:
        body = anschreiben(store.config(), members, data.get('betreff'), data.get('text'))
    post = len(members) if art in ('csv', 'excel', 'mandate') else sum(1 for m in members if postfaehig(m))
    with store.db:
        store.audit(actor, 'Mitgliederliste ausgegeben', {'art': art, 'anzahl': post})
    kind, ext = ('application/zip', 'zip') if zipped else ARTEN[art]
    return body, kind, f'{NAMEN[art]}-{date.today().isoformat()}.{ext}'
