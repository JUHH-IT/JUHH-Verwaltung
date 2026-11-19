"""Mitgliederstatus und Wahlberechtigung (ab 0.37, Wunsch Lennert Gust 19.11.2026): wahlberechtigt / nicht wahlberechtigt / ausgetreten.

Nur lesend – der Status wird immer berechnet, nie von Hand gepflegt. Satzung der JU Hamburg (Stand 11.04.2026):
  § 37 Abs. 1 Nr. 1: Wahlberechtigt bei turnusmäßigen Wahlen ist, wer dem Verband zum Beitrittsstichtag (30. November vor den Wahlen)
      angehört und die bis zum Beitragsstichtag fälligen Beiträge des aktuellen und der vergangenen zwei Kalenderjahre bezahlt hat.
      Maßgeblich ist der Zahlungseingang – ein SEPA-Mandat allein reicht nicht.
  § 15 Abs. 3: Beitragsstichtag = letzter Werktag im Januar vor den Wahlen.
  § 17 Abs. 1 Nr. 2/3: Der Landesmitgliedschaftsausschuss (LMA) bestimmt binnen 7 Tagen nach dem Beitragsstichtag die Wahlberechtigten
      und berichtet, welche und wie viele Mitglieder je Ortsverband wahlberechtigt und nicht wahlberechtigt sind.
Entscheidungen Lennert Gust 19.11.2026: Jahre vor dem Datenbeginn gelten als bezahlt; beitragsfreie Mitglieder sind wahlberechtigt,
Gastmitglieder nicht; 2026 wird nicht ausgewertet, ab `wahlrecht_ab` (2027) gilt die Regel; Stichtage in den Einstellungen.

Bezugstag: der gewählte Stichtag (Liste des LMA) bzw. heute (laufender Status). Gerechnet wird mit dem Beitragskonto zum Bezugstag
(mitgliederkonto.overview: Zahlungen und Fälligkeit bis dahin) – keine eigene Beitragsrechnung. Ausgetreten = Austrittsdatum erreicht
(wie die Mitgliederliste). Nicht umgesetzt: außerordentliche Neuwahlen (§ 37 Abs. 1 Nr. 2), Delegiertenschlüssel (§ 15 Abs. 3).
"""
import io
from datetime import date, timedelta

from formate import german_date
from pdfschrift import exclusive

STATUS = {'wahlberechtigt': 'wahlberechtigt', 'nicht': 'nicht wahlberechtigt', 'ausgetreten': 'ausgetreten'}
NOTE = ('Wahlberechtigt nach Satzung § 37 Abs. 1 Nr. 1: Mitglied am Beitrittsstichtag, und die bis zum Stichtag fälligen Beiträge des Jahres '
        'und der zwei Jahre davor sind eingegangen (Zahlungseingang; ein SEPA-Mandat allein reicht nicht). Jahre vor dem Datenbeginn gelten '
        'als bezahlt, beitragsfreie Mitglieder sind wahlberechtigt, Gastmitglieder nicht. Kreis- und Ortsverband = heutige Zuordnung. '
        'Stand der Buchhaltung – nur so aktuell wie der zuletzt importierte und zugeordnete Kontoauszug.')
FRIST = timedelta(days=7)   # § 17 Abs. 1 Nr. 2: so lange nach dem Beitragsstichtag bestimmt der LMA die Wahlberechtigten
RUECK = 'Rücklastschrift nach dem Stichtag – bitte prüfen'
CONFIDENTIAL = 'Vertraulich: enthält personenbezogene Daten – nur für den Landesmitgliedschaftsausschuss und die zuständigen Gremien.'


def letzter_werktag_januar(year):
    """Beitragsstichtag (§ 15 Abs. 3): letzter Werktag im Januar. Werktag = Montag bis Samstag, kein Feiertag – Ende Januar gibt es
    in Hamburg keinen Feiertag, deshalb genügt der Sonntag."""
    day = date(year, 1, 31)
    return day - timedelta(days=1) if day.weekday() == 6 else day


def beitritt_vor(beitrag):
    """Beitrittsstichtag: der 30. November vor dem Beitragsstichtag."""
    november = date(beitrag.year, 11, 30)
    return november if november < beitrag else date(beitrag.year - 1, 11, 30)


def _tag(cfg, key):
    return date.fromisoformat(cfg[key]) if cfg.get(key) else None


def _beitritt(cfg, beitrag):
    """Beitrittsstichtag zu einem Beitragsstichtag: der eingestellte, wenn er im Jahr davor liegt (sonst ist er überholt), sonst der 30.11."""
    own = _tag(cfg, 'wahl_beitrittsstichtag')
    return own if own and beitrag - timedelta(days=366) < own < beitrag else beitritt_vor(beitrag)


def stichtage(cfg, today=None):
    """Stichtage der nächsten turnusmäßigen Wahlen: aus den Einstellungen, leer = Vorschlag (letzter Werktag im Januar, 30.11. davor).
    Ein eingestellter Beitragsstichtag ersetzt den Vorschlag seines Kalenderjahres (in späteren Jahren ist er überholt). Ein Stichtag
    bleibt bis zum Ende der Frist des LMA (7 Tage danach) der aktuelle; dann gilt der des nächsten Jahres und er ist `letzter`."""
    today = date.fromisoformat(today) if isinstance(today, str) else today or date.today()
    own = _tag(cfg, 'wahl_beitragsstichtag')

    def tag(year):
        return own if own and own.year == year else letzter_werktag_januar(year)
    year = today.year + (tag(today.year) + FRIST < today)
    beitrag = tag(year)
    vorschlag = next(d for d in map(letzter_werktag_januar, (today.year, today.year + 1)) if d >= today)   # nie in der Vergangenheit
    return {'ab': int(cfg.get('wahlrecht_ab') or 2027), 'beitrag': beitrag.isoformat(), 'beitritt': _beitritt(cfg, beitrag).isoformat(),
            'vorschlag': {'beitrag': vorschlag.isoformat(), 'beitritt': beitritt_vor(beitrag).isoformat()},
            'letzter': tag(year - 1).isoformat()}


def pruefe_einstellungen(cfg, today=None):
    """Eingetragener Beitrittsstichtag muss vor dem geltenden Beitragsstichtag liegen (§ 37 Abs. 1 Nr. 1). Verglichen wird mit dem
    Stichtag, der heute gilt – ein überholter gespeicherter Beitragsstichtag sperrt nichts; ein überholter Beitrittsstichtag zählt ohnehin nicht."""
    own = _tag(cfg, 'wahl_beitrittsstichtag')
    if own and own.isoformat() >= stichtage(cfg, today)['beitrag']:
        raise ValueError('Der Beitrittsstichtag muss vor dem Beitragsstichtag liegen (Satzung § 37 Abs. 1 Nr. 1).')


def _fenster(day):
    """Beitragsjahre, die zählen (§ 37 Abs. 1 Nr. 1): das Jahr des Bezugstags und die zwei Jahre davor."""
    return range(day.year - 2, day.year + 1)


def _status(m, konto, day, beitritt):
    """(Status, Gründe ohne Beträge) eines Mitglieds am Tag `day`; beitritt = Beitrittsstichtag oder None (zählt noch nicht)."""
    if m['left_on'] and m['left_on'] <= day.isoformat():
        return 'ausgetreten', [f"Austritt {german_date(m['left_on'])}"]
    if m['joined'] > day.isoformat():   # am Bezugstag noch kein Mitglied – steht trotzdem in der Liste (heutige Mitglieder), nie wahlberechtigt
        return 'nicht', [f"Eintritt erst am {german_date(m['joined'])}"]
    reasons = ['Gastmitglied'] if m['guest'] else []
    if beitritt and m['joined'] > beitritt.isoformat():
        reasons.append('nach dem Beitrittsstichtag eingetreten')
    # shortcut: „offen“ folgt der Verrechnung des Beitragskontos (Zahlungen decken die ältesten Beiträge zuerst, auch Jahre vor dem
    # Dreijahresfenster) – weicht erst ab 2028 vom Wortlaut des § 37 ab (Datenbeginn 2025); vor dem Stichtag Januar 2028 je Jahr des
    # Fensters rechnen (REGELN Nr. 25, Test test_verrechnung_aelteste_zuerst)
    window = _fenster(day)
    left = (m['left_on'] or '')[:4]
    for label, years in (('Beitrag offen', konto['arrear_years']), ('Soll zu klären', konto['unclear_years'])):
        years = [f'{y} (Austrittsjahr)' if label == 'Soll zu klären' and str(y) == left else str(y) for y in years if y in window]
        if years:
            reasons.append(f"{label}: {', '.join(years)}")
    return ('nicht' if reasons else 'wahlberechtigt'), reasons


def auswertung(store, stichtag=None, today=None, book=None):
    """Status je Mitglied mit Gründen, Zahlen je Kreis- und Ortsverband. stichtag (JJJJ-MM-TT) = Liste des LMA zu diesem Tag, der
    Beitrittsstichtag zählt immer; ohne stichtag = laufender Status heute, der Beitrittsstichtag zählt erst, wenn er erreicht ist.
    book: schon berechnete Beitragskonten zum selben Tag (mitgliederkonto.overview), sonst werden sie hier berechnet."""
    import gliederung
    import mitgliederkonto
    from core import KVS
    today = date.fromisoformat(today) if isinstance(today, str) else today or date.today()
    try:
        day = date.fromisoformat(str(stichtag)) if stichtag else today
    except ValueError:
        raise ValueError('Stichtag ungültig.') from None
    if not 2000 <= day.year <= today.year + 2:
        raise ValueError('Stichtag ungültig.')
    cfg = store.config()
    s = stichtage(cfg, today)
    beitritt = _beitritt(cfg, day) if stichtag else date.fromisoformat(s['beitritt'])
    counts = bool(stichtag) or beitritt <= day
    book = book if book is not None else mitgliederkonto.overview(store, today=day.isoformat())
    names, ovs = dict(KVS), {o['id']: gliederung.label(o) for o in gliederung.ovs(store)}
    # W1: Die Liste zum Stichtag zählt nach dem Wortlaut nur Buchungen bis dahin. Wird eine Zahlung danach zurückgebucht, bleibt der
    # Status stehen – die Liste weist aber darauf hin (Entscheidung des LMA, REGELN Nr. 25)
    zurueck = mitgliederkonto.returned_after(store, day.isoformat(), _fenster(day), today.isoformat()) if stichtag else set()
    members, kvs, total = [], {}, {key: 0 for key in STATUS}
    for m in store.rows('SELECT id,number,name,first_name,last_name,kv,ov_id,joined,left_on,guest FROM members'):
        status, reasons = _status(m, book[m['id']], day, beitritt if counts else None)
        members.append({'id': m['id'], 'number': m['number'], 'kv': m['kv'], 'ov_id': m['ov_id'], 'ov': ovs.get(m['ov_id'], ''),
                        'name': f"{m['last_name']}, {m['first_name'] or ''}".strip(', ') if m['last_name'] else m['name'],
                        'status': status, 'gruende': reasons, 'hinweis': RUECK if status == 'wahlberechtigt' and m['id'] in zurueck else ''})
        total[status] += 1
        if status != 'ausgetreten':
            kv = kvs.setdefault(m['kv'], {'code': m['kv'], 'name': names.get(m['kv'], m['kv']), 'wahlberechtigt': 0, 'nicht': 0, 'ovs': {}})
            ov = kv['ovs'].setdefault(m['ov_id'], {'id': m['ov_id'], 'label': ovs.get(m['ov_id'], 'ohne Ortsverband'), 'wahlberechtigt': 0, 'nicht': 0})
            kv[status] += 1
            ov[status] += 1
    members.sort(key=lambda x: (x['kv'], not x['ov'], x['ov'], gliederung.fold(x['name']), x['number']))   # ohne Ortsverband zuletzt; Ö wie O
    return {'stichtag': day.isoformat(), 'laufend': not stichtag, 'today': today.isoformat(), 'beitrittsstichtag': beitritt.isoformat(),
            'beitritt_zaehlt': counts, 'vorschau': day > today, 'gilt': day.year >= s['ab'], 'stichtage': s, 'gesamt': total,
            'hinweise': sum(1 for x in members if x['hinweis']),
            'kv': [{**k, 'ovs': sorted(k['ovs'].values(), key=lambda o: (o['id'] is None, o['label']))} for _, k in sorted(kvs.items())],
            'mitglieder': members}


def laufend(store, book=None, today=None):
    """Laufender Status für die Mitgliederliste: {Mitglied: {'status', 'gruende'}} – leer, solange das Jahr vor `wahlrecht_ab` liegt
    (dann zeigt die Liste wie bisher nur aktiv/ausgetreten)."""
    today = date.fromisoformat(today) if isinstance(today, str) else today or date.today()
    if today.year < stichtage(store.config(), today)['ab']:
        return {}
    return {x['id']: {'status': x['status'], 'gruende': x['gruende']} for x in auswertung(store, today=today, book=book)['mitglieder']}


# ---------------------------------------------------------------- PDF und CSV (mit Namen – vertraulich)

def _head(r):
    if r['laufend']:
        return f"laufender Status am {german_date(r['stichtag'])}"
    return f"Stichtag {german_date(r['stichtag'])} · Beitrittsstichtag {german_date(r['beitrittsstichtag'])}"


def _preview(r):
    if r['vorschau']:
        return f"Vorschau: Der Stichtag liegt in der Zukunft – Stand der Zahlungen am {german_date(r['today'])}."
    if not r['gilt']:
        return f"Vorschau: Die Wahlberechtigung wird erst ab {r['stichtage']['ab']} ausgewertet (Einstellungen)."
    return ''


def _build(story, pages=None):
    """PDF mit Fußzeile auf jeder Seite („Vertraulich“, Seite x von y) → (Inhalt, Seitenzahl)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate
    from pdfschrift import SANS

    def footer(canvas, doc):
        canvas.setFont(SANS, 7)
        canvas.setFillGray(0.35)
        canvas.drawString(45, 22, 'Vertraulich – enthält personenbezogene Daten')
        canvas.drawRightString(550, 22, f'Seite {doc.page}' + (f' von {pages}' if pages else ''))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=45, rightMargin=45, topMargin=38, bottomMargin=38, title='Wahlberechtigung',
                            creator='JU Hamburg Finanzen')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue(), doc.page


def _grund(x):
    return '; '.join(x['gruende'] + ([x['hinweis']] if x['hinweis'] else []))


@exclusive
def pdf(store, stichtag=None, today=None):
    """Bericht nach § 17 Abs. 1 Nr. 3: Zahlen je Kreis- und Ortsverband, dann je Ortsverband die Mitglieder mit Status und Grund."""
    import berichte
    from reportlab.platypus import CondPageBreak
    r = auswertung(store, stichtag, today)
    st, cfg, g = berichte._styles(), store.config(), r['gesamt']
    p = berichte._para(st)

    def story():
        out = [p('Wahlberechtigung der Mitglieder' + (' – Vorschau' if _preview(r) else ''), 'title'),
               p(f"{cfg.get('issuer') or 'Junge Union Hamburg'} · {_head(r)} · erstellt am {german_date(r['today'])} · Satzung § 37, § 17", 'small')]
        if _preview(r):
            out.append(p(_preview(r), 'body'))
        if r['hinweise']:
            out.append(p(f"{r['hinweise']} Mitglied(er) mit Hinweis: {RUECK}. Die Zahlung ging bis zum Stichtag ein und wurde danach "
                         'zurückgebucht; der Status folgt dem Stand am Stichtag – über diese Fälle entscheidet der Landesmitgliedschaftsausschuss.', 'body'))
        rows = [['Kreisverband / Ortsverband', 'wahlberechtigt', 'nicht wahlberechtigt', 'zusammen']]
        for k in r['kv']:
            rows.append([k['name'], k['wahlberechtigt'], k['nicht'], k['wahlberechtigt'] + k['nicht']])
            rows += [[f"– {o['label']}", o['wahlberechtigt'], o['nicht'], o['wahlberechtigt'] + o['nicht']] for o in k['ovs']]
        rows.append(['Gesamt', g['wahlberechtigt'], g['nicht'], g['wahlberechtigt'] + g['nicht']])
        out += [p('Zahlen je Kreis- und Ortsverband', 'h2'), berichte._table(rows, [245, 80, 100, 80], st, numeric=(1, 2, 3))]
        current = [x for x in r['mitglieder'] if x['status'] != 'ausgetreten']
        for k in r['kv']:
            for o in k['ovs']:
                mine = [x for x in current if x['kv'] == k['code'] and x['ov_id'] == o['id']]
                out += [CondPageBreak(70), p(f"{k['name']} · {o['label']} · {o['wahlberechtigt']} wahlberechtigt, {o['nicht']} nicht wahlberechtigt", 'h2'),
                        berichte._table([['Mitglied', 'Mitgliedsnummer', 'Status', 'Grund / Hinweis']] +
                                        [[x['name'], x['number'], STATUS[x['status']], _grund(x)] for x in mine], [170, 95, 95, 145], st)]
        return out + [p(NOTE, 'small'), p(CONFIDENTIAL, 'small')]
    return _build(story(), _build(story())[1])[0]   # zweimal gebaut: erst die Seitenzahl, dann „Seite x von y“


def csv(store, stichtag=None, today=None):
    """Mitglieder am Stichtag mit Status und Grund als CSV (Semikolon, Formelschutz wie die anderen Listen)."""
    import berichte
    r = auswertung(store, stichtag, today)
    names = {k['code']: k['name'] for k in r['kv']}
    return berichte._csv([[names[x['kv']], x['ov'], x['number'], x['name'], STATUS[x['status']], '; '.join(x['gruende']), x['hinweis']]
                          for x in r['mitglieder'] if x['status'] != 'ausgetreten'],
                         ['Kreisverband', 'Ortsverband', 'Mitgliedsnummer', 'Name', f"Status am {german_date(r['stichtag'])}", 'Grund', 'Hinweis'])
