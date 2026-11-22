"""SEPA-Basislastschrift (CORE) für Mitgliedsbeiträge – Mandate, Vorabankündigung, Lastschriftdatei pain.008.001.08.

Ablauf: Mandat am Mitglied erfassen (unterschriebenes Formular, Referenz, Datum) → Beitragslauf: Rechnungen an
Mitglieder mit Mandat enthalten die Vorabankündigung (Einzugsdatum, Mandatsreferenz, Gläubiger-ID, Konto) →
„Lastschriften“: Datei pain.008.001.08 erzeugen und im Online-Banking hochladen → als eingereicht markieren →
die Gutschriften erscheinen einzeln im Kontoauszug (keine Sammelbuchung) und werden über die Rechnungsnummer
abgeglichen. Rücklastschriften werden im Buchungsplatz der Rechnung (negativ) zugeordnet; danach ist die
Rechnung wieder offen und wird per Überweisung bezahlt.

Regeln: seit November 2016 genügt für jede Lastschrift der Sequenztyp RCUR und eine Vorlage bis einen
Bankarbeitstag vor Fälligkeit; ab November 2026 nehmen Banken nur noch pain.008.001.08 an. Ändert sich das
Konto, wird ein neues Mandat erfasst (keine Mandatsänderung in der Datei). Nur Konten im EWR (dort ist die
Anschrift des Zahlungspflichtigen nicht erforderlich). Ein Mandat verfällt, wenn 36 Monate keine Lastschrift
eingereicht wurde. Eingezogen wird nur, wenn die Vorabankündigung nachweislich versendet wurde (E-Mail oder
eingetragener Postversand) und die Frist ab Versand eingehalten ist – nie vor dem angekündigten Datum und höchstens
GRACE_DAYS danach; sonst fällt die Rechnung auf Überweisung zurück (GiroCode, Zahlungserinnerung).

Einstieg für alle anderen Module: info() – der Lastschriftstand einer Rechnung an einer Stelle.
"""
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from formate import add_months, euro, german_date, iban_compact, iban_pretty, iban_valid, iso

NS = 'urn:iso:std:iso:20022:tech:xsd:pain.008.001.08'
EEA = set('AT BE BG HR CY CZ DK EE FI FR DE GR HU IS IE IT LV LI LT LU MT NL NO PL PT RO SK SI ES SE'.split())
# EPC-Zeichensatz für Kennungen; „/“ nicht am Anfang oder Ende und nie doppelt
MANDATE_CHARS = re.compile(r"(?!/)(?!.*//)[A-Za-z0-9+?/\-:().,']{1,35}(?<!/)")
DEBIT_STATES = ('pending', 'returned', 'cancelled')
GRACE_DAYS = 7          # Einzug höchstens so viele Tage nach dem angekündigten Datum, sonst Überweisung
MANDATE_MONTHS = 36     # Verfall eines ungenutzten Mandats
SOON_DAYS = 14          # Datei erst so kurz vor dem Einzug erstellen (viele Banken nehmen sie nicht früher an)
REPAY_DAYS = 14         # neue Zahlungsfrist ab dem Tag, an dem die Lastschrift entfiel


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS mandates(id INTEGER PRIMARY KEY,member_id INTEGER NOT NULL REFERENCES members(id),
        reference TEXT UNIQUE NOT NULL,holder TEXT NOT NULL,iban TEXT NOT NULL,bic TEXT,signed_on TEXT NOT NULL,
        used_before TEXT,status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),revoked_on TEXT,revoke_reason TEXT,
        created TEXT NOT NULL,actor TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS mandates_active ON mandates(member_id) WHERE status='active';
    CREATE TABLE IF NOT EXISTS debit_batches(id INTEGER PRIMARY KEY,msg_id TEXT UNIQUE NOT NULL,created TEXT NOT NULL,
        count INTEGER NOT NULL,total INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'created' CHECK(status IN ('created','submitted','cancelled')),
        submitted_on TEXT,downloaded_on TEXT,cancel_reason TEXT,actor TEXT);
    CREATE TABLE IF NOT EXISTS debit_notices(invoice_id INTEGER PRIMARY KEY REFERENCES invoices(id),sent_on TEXT NOT NULL,
        actor TEXT,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS debits(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL REFERENCES debit_batches(id),
        invoice_id INTEGER NOT NULL REFERENCES invoices(id),mandate_id INTEGER NOT NULL REFERENCES mandates(id),
        amount INTEGER NOT NULL CHECK(amount>0),collection_date TEXT NOT NULL,end_to_end TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','returned','cancelled')),returned_on TEXT,note TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS debits_pending ON debits(invoice_id) WHERE status='pending';
    ''')
    for table, column in (('mandates', 'used_before'), ('debit_batches', 'downloaded_on'), ('debit_batches', 'cancel_reason'), ('debits', 'note')):
        if column not in [c[1] for c in db.execute(f'PRAGMA table_info({table})')]:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {column} TEXT')  # additiv für Datenbanken aus dem ersten 0.15-Stand
    db.commit()


# ---------------------------------------------------------------- Gläubiger-ID, Zeichen, Bankarbeitstage

def creditor_id_valid(value):
    """Gläubiger-Identifikationsnummer: Land, Prüfziffer (ISO 7064 Mod 97, ohne Geschäftsbereich), Geschäftsbereich, Kennung."""
    s = re.sub(r'\s+', '', str(value or '')).upper()
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{3}[A-Z0-9]{1,28}', s):
        return False
    digits = ''.join(str(int(c, 36)) for c in s[7:] + s[:2] + '00')
    return 98 - int(digits) % 97 == int(s[2:4])


def masked(iban):
    s = iban_compact(iban)
    return f'{s[:4]} •••• {s[-4:]}' if len(s) > 8 else s


def sepa_text(value, limit):
    """Text im SEPA-Zeichensatz: Umlaute umschreiben, Akzente entfernen, Unzulässiges durch Leerzeichen ersetzen."""
    s = str(value or '')
    for a, b in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('Ä', 'Ae'), ('Ö', 'Oe'), ('Ü', 'Ue'), ('ß', 'ss'),
                 ('ł', 'l'), ('Ł', 'L'), ('ø', 'o'), ('Ø', 'O'), ('æ', 'ae'), ('Æ', 'Ae'), ('đ', 'd'), ('Đ', 'D'), ('ı', 'i'), ('&', '+')):
        s = s.replace(a, b)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    s = re.sub(r"[^A-Za-z0-9/\-?:().,'+ ]", ' ', s)
    return ' '.join(s.split())[:limit] or '-'


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def business_day(day):
    """Nächster TARGET-Geschäftstag ab day (Wochenende, Neujahr, Karfreitag, Ostermontag, 1. Mai, 25./26. Dezember)."""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    while True:
        e = _easter(d.year)
        if d.weekday() < 5 and d not in {date(d.year, 1, 1), e - timedelta(days=2), e + timedelta(days=1), date(d.year, 5, 1),
                                         date(d.year, 12, 25), date(d.year, 12, 26)}:
            return d.isoformat()
        d += timedelta(days=1)


def collection_date(cfg, due, issued=None):
    """Einzugsdatum für eine neue Rechnung: frühestens Fälligkeit und Rechnungsdatum + Vorabankündigungsfrist."""
    issued = date.fromisoformat(issued) if issued else date.today()
    earliest = (issued + timedelta(days=int(cfg['sepa_prenotice']))).isoformat()
    return business_day(max(due, earliest))


def enabled(cfg):
    return creditor_id_valid(cfg.get('creditor_id'))


# ---------------------------------------------------------------- Mandate

def active_mandate(store, member_id):
    return store.db.execute("SELECT * FROM mandates WHERE member_id=? AND status='active'", (member_id,)).fetchone()


def last_use(store, mandate, exclude=None):
    """Letzte Nutzung (eingereichte oder geplante Lastschrift, sonst Unterschrift bzw. bekannte Nutzung außerhalb)."""
    used = store.db.execute("SELECT MAX(collection_date) FROM debits WHERE mandate_id=? AND status!='cancelled' AND id!=?",
                            (mandate['id'], exclude or 0)).fetchone()[0]
    return max(d for d in (mandate['signed_on'], mandate['used_before'], used) if d)


def expires(store, mandate, exclude=None):
    """Letzter möglicher Einzugstag: 36 Monate nach der letzten Nutzung (exclude: diese Lastschrift nicht mitzählen)."""
    return add_months(last_use(store, mandate, exclude), MANDATE_MONTHS)


def mandat_gueltig(store, today=None, rows=None):
    """{Mitglied: IBAN} der aktiven, nicht verfallenen Mandate (ab 0.53, Liste „IBAN ohne Mandat“); rows = schon gelesene mandates()."""
    today = today or date.today().isoformat()
    return {m['member_id']: m['iban'] for m in (rows if rows is not None else mandates(store)) if m['status'] == 'active' and m['expires'] >= today}


def mandates(store):
    rows = store.rows('SELECT m.*,(SELECT MAX(d.collection_date) FROM debits d WHERE d.mandate_id=m.id AND d.status!=\'cancelled\') last_used '
                      'FROM mandates m ORDER BY m.id DESC')
    for m in rows:
        m['expires'] = expires(store, m)
    return rows


def _next_reference(store, cfg, member):
    base = sepa_text(f"{cfg.get('sepa_prefix') or 'JUHH'}-{member['number']}", 30).replace(' ', '')
    n = 1
    while store.db.execute('SELECT 1 FROM mandates WHERE reference=?', (f'{base}-{n}',)).fetchone():
        n += 1
    return f'{base}-{n}'


def create_mandate(store, member_id, data, actor):
    """Unterschriebenes Mandat erfassen. Ein aktives Mandat je Mitglied; Kontowechsel = neues Mandat."""
    cfg = store.config()
    if not enabled(cfg):
        raise ValueError('Zuerst die Gläubiger-Identifikationsnummer in den Stammdaten (Verein & Bank) eintragen.')
    m = store.db.execute('SELECT * FROM members WHERE id=?', (member_id,)).fetchone()
    if not m:
        raise ValueError('Mitglied nicht gefunden.')
    holder = ' '.join(str(data.get('holder') or m['name']).split())[:70]
    if not holder:
        raise ValueError('Bitte den Kontoinhaber angeben.')
    if not re.search(r'[A-Za-z]', sepa_text(holder, 70)):
        raise ValueError('Kontoinhaber bitte in lateinischer Schrift angeben – so, wie er bei der Bank geführt wird.')
    iban = iban_compact(data.get('iban'))
    if not iban_valid(iban):
        raise ValueError('IBAN ist ungültig (Prüfziffer stimmt nicht).')
    if iban[:2] not in EEA:
        raise ValueError('Lastschrift nur für Konten im Europäischen Wirtschaftsraum – bitte Überweisung nutzen.')
    bic = str(data.get('bic') or '').upper().replace(' ', '') or None
    if bic and not re.fullmatch(r'[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?', bic):
        raise ValueError('BIC ist ungültig (8 oder 11 Zeichen).')
    signed = iso(data.get('signed_on') or '')
    if signed > date.today().isoformat():
        raise ValueError('Datum der Unterschrift liegt in der Zukunft.')
    used_before = str(data.get('used_before') or '').strip()
    used_before = iso(used_before) if used_before else None
    if used_before and not signed <= used_before <= date.today().isoformat():
        raise ValueError('Letzte Nutzung: nach der Unterschrift und nicht in der Zukunft.')
    reference = str(data.get('reference') or '').strip()
    if reference and not MANDATE_CHARS.fullmatch(reference):
        raise ValueError("Mandatsreferenz: höchstens 35 Zeichen, nur Buchstaben, Ziffern und + ? / - : ( ) . , ' – keine Leerzeichen, "
                         "„/“ nicht am Anfang, am Ende oder doppelt.")
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        if active_mandate(store, member_id):
            raise ValueError('Für dieses Mitglied besteht bereits ein aktives Mandat – bei Kontowechsel das alte zuerst widerrufen.')
        reference = reference or _next_reference(store, cfg, m)
        try:
            store.db.execute('INSERT INTO mandates(member_id,reference,holder,iban,bic,signed_on,used_before,created,actor) VALUES(?,?,?,?,?,?,?,?,?)',
                             (member_id, reference, holder, iban, bic, signed, used_before, datetime.now().isoformat(timespec='seconds'), actor))
        except sqlite3.IntegrityError:
            raise ValueError(f'Mandatsreferenz {reference} ist bereits vergeben.') from None
        store.audit(actor, 'SEPA-Mandat erfasst', {'member': m['number'], 'reference': reference, 'iban': masked(iban)})
    return reference


def revoke_mandate(store, mandate_id, reason, actor):
    reason = ' '.join(str(reason or '').split())
    if not reason:
        raise ValueError('Bitte den Grund angeben (z. B. Widerruf durch das Mitglied, Kontowechsel).')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        m = store.db.execute("SELECT * FROM mandates WHERE id=? AND status='active'", (mandate_id,)).fetchone()
        if not m:
            raise ValueError('Kein aktives Mandat.')
        store.db.execute("UPDATE mandates SET status='revoked',revoked_on=?,revoke_reason=? WHERE id=?",
                         (date.today().isoformat(), reason[:200], mandate_id))
        store.audit(actor, 'SEPA-Mandat widerrufen', {'reference': m['reference'], 'reason': reason[:200]})


def debit_snapshot(store, cfg, member, due, issued):
    """Vorabankündigung für die Rechnung (wird mit der Rechnung festgeschrieben) oder None ohne aktives Mandat."""
    if not member or not enabled(cfg):
        return None
    m = active_mandate(store, member['id'])
    if not m or collection_date(cfg, due, issued) > expires(store, m):  # verfallenes Mandat → Überweisung
        return None
    return {'mandate': m['reference'], 'creditor_id': re.sub(r'\s+', '', cfg['creditor_id']).upper(), 'iban': masked(m['iban']),
            'holder': m['holder'], 'date': collection_date(cfg, due, issued), 'prenotice': int(cfg['sepa_prenotice'])}


# ---------------------------------------------------------------- Einzug

def debit_state(store, invoice_id):
    """'pending' (in einer Lastschriftdatei), 'returned' (zuletzt zurückgegeben) oder None."""
    row = store.db.execute("SELECT d.status FROM debits d WHERE d.invoice_id=? AND d.status!='cancelled' ORDER BY d.id DESC LIMIT 1",
                           (invoice_id,)).fetchone()
    return row[0] if row else None


def earliest(today=None):
    """Frühestes Einzugsdatum für eine heute erstellte Datei (Vorlage bis einen Bankarbeitstag vorher)."""
    today = today or date.today().isoformat()
    return business_day((date.fromisoformat(today) + timedelta(days=1)).isoformat())


def notified_on(store, invoice_id):
    """Versand der Vorabankündigung: erste erfolgreich versendete Rechnungs-E-Mail oder eingetragener Postversand."""
    mail = store.db.execute("SELECT MIN(substr(time,1,10)) FROM mailings WHERE invoice_id=? AND kind='invoice' AND status='sent'",
                            (invoice_id,)).fetchone()[0]
    post = store.db.execute('SELECT sent_on FROM debit_notices WHERE invoice_id=?', (invoice_id,)).fetchone()
    return min(d for d in (mail, post and post[0]) if d) if mail or post else None


def info(store, inv, today=None):
    """Lastschriftstand einer Rechnung mit Vorabankündigung – oder None ohne Lastschrift.

    state: pending (in einer Datei), ready (einziehbar), unnotified (Vorabankündigung nicht versendet), returned,
    revoked (Mandat widerrufen), expired (Mandat verfallen), missed (angekündigtes Datum zu lange vorbei).
    active: Die Lastschrift gilt noch (pending, ready, unnotified) – sonst zahlt das Mitglied per Überweisung.
    date: Einzugsdatum, wenn heute eine Datei erstellt würde; reason: warum nicht einziehbar (None = bereit)."""
    import json
    inv = dict(inv)
    snap = inv['snap'] if 'snap' in inv else json.loads(inv['snapshot'])
    debit = snap.get('debit')
    if not debit:
        return None
    today = today or date.today().isoformat()
    out = {'mandate': debit['mandate'], 'iban': debit['iban'], 'announced': debit['date'], 'date': debit['date'], 'late': False}
    last = store.db.execute("SELECT * FROM debits WHERE invoice_id=? AND status!='cancelled' ORDER BY id DESC LIMIT 1",
                            (inv['id'],)).fetchone() if inv.get('id') else None
    if last and last['status'] == 'pending':
        return {**out, 'state': 'pending', 'active': True, 'reason': 'bereits in einer Lastschriftdatei'}
    if last and last['status'] == 'returned':
        why = f"von der Bank nicht ausgeführt ({last['note']})" if last['note'] else 'Rücklastschrift'
        return {**out, 'state': 'returned', 'active': False, 'pay_by': _pay_by(inv, last['returned_on']),
                'reason': why + ' – bitte per Überweisung zahlen lassen oder Mitglied klären'}
    mandate = store.db.execute('SELECT * FROM mandates WHERE reference=?', (debit['mandate'],)).fetchone()
    if not mandate or mandate['status'] != 'active':
        return {**out, 'state': 'revoked', 'active': False, 'pay_by': _pay_by(inv, mandate and mandate['revoked_on']),
                'reason': 'Mandat widerrufen – per Überweisung zahlen lassen'}
    notified = notified_on(store, inv['id']) if inv.get('id') else None
    days = int(debit.get('prenotice') or store.config()['sepa_prenotice'])
    # volle Frist ab Versand (noch nicht versendet: als ginge sie heute raus)
    notice_ready = business_day((date.fromisoformat(notified or today) + timedelta(days=days)).isoformat())
    day = max(debit['date'], notice_ready, earliest(today))
    out.update(date=day, late=day > debit['date'], notified=notified)
    if day > (date.fromisoformat(debit['date']) + timedelta(days=GRACE_DAYS)).isoformat():
        why = (f"angekündigter Einzug am {german_date(debit['date'])} verpasst" if notified and notice_ready <= debit['date']
               else f'Vorabankündigung zu spät versendet (am {german_date(notified)})' if notified
               else 'Vorabankündigung nicht rechtzeitig versendet')
        return {**out, 'state': 'missed', 'active': False, 'reason': why + ' – per Überweisung zahlen lassen',
                'pay_by': _pay_by(inv, (date.fromisoformat(debit['date']) + timedelta(days=GRACE_DAYS)).isoformat())}
    if day > expires(store, mandate):
        return {**out, 'state': 'expired', 'active': False, 'pay_by': _pay_by(inv, expires(store, mandate)),
                'reason': f'Mandat seit {MANDATE_MONTHS} Monaten ungenutzt und verfallen – diese Rechnung per Überweisung, für künftige Einzüge neues Mandat einholen'}
    if not notified:
        return {**out, 'state': 'unnotified', 'active': True,
                'reason': 'Vorabankündigung nicht versendet – Rechnung per E-Mail senden oder den Postversand eintragen'}
    return {**out, 'state': 'ready', 'active': True, 'reason': None}


def _pay_by(inv, since):
    """Feste Zahlungsfrist, wenn die Lastschrift entfällt: REPAY_DAYS ab dem Tag, an dem sie entfiel (nie vor der Fälligkeit)."""
    return max(inv['due'], (date.fromisoformat(since or inv['due']) + timedelta(days=REPAY_DAYS)).isoformat())


def payable_by(inv):
    """Zahlungsfrist für PDF und E-Mail: Fälligkeit – oder die feste neue Frist, wenn die Lastschrift entfallen ist
    (rechnung_pdf.invoice_data setzt pay_by aus info())."""
    return inv.get('pay_by') or inv.get('due')


def record_notice(store, invoice_id, sent_on, actor):
    """Postversand der Rechnung mit Vorabankündigung eintragen (für Mitglieder ohne E-Mail) – nur solange sie noch
    nicht versendet ist; eine entfallene Lastschrift wird dadurch nie wieder einziehbar."""
    inv = store.db.execute('SELECT * FROM invoices WHERE id=?', (invoice_id,)).fetchone()
    d = info(store, inv) if inv and inv['status'] == 'issued' else None
    if not d:
        raise ValueError('Rechnung ohne Vorabankündigung.')
    if d['state'] != 'unnotified':
        raise ValueError('Die Vorabankündigung ist schon versendet oder die Lastschrift entfallen – Postversand nicht mehr eintragbar.')
    day = iso(sent_on or '')
    if not inv['issued'] <= day <= date.today().isoformat():
        raise ValueError('Versanddatum: ab Rechnungsdatum und nicht in der Zukunft.')
    with store.db:
        store.db.execute('INSERT INTO debit_notices(invoice_id,sent_on,actor,created) VALUES(?,?,?,?)',
                         (invoice_id, day, actor, datetime.now().isoformat(timespec='seconds')))
        store.audit(actor, 'Vorabankündigung per Post eingetragen', {'invoice': inv['number'], 'sent_on': day})


def _incoming(store):
    """Nicht zugeordnete Zahlungseingänge zu Rechnungen (gemeinsame Regel mit den Zahlungserinnerungen)."""
    import verbindlichkeiten
    return verbindlichkeiten.incoming_hint(store)


def collectable(store, today=None):
    """Rechnungen mit Vorabankündigung, die eingezogen werden können (reason None) – sonst mit Grund.
    soon: Einzugsdatum innerhalb von SOON_DAYS (erst dann die Datei erstellen)."""
    from versand import paid_amount
    today = today or date.today().isoformat()
    hint = _incoming(store)
    out = []
    for inv in store.rows("SELECT * FROM invoices WHERE status='issued' AND total>0 ORDER BY due,id"):
        d = info(store, inv, today)
        if not d:
            continue
        paid = paid_amount(store, inv['id'])
        if paid >= inv['total'] or d['state'] == 'pending' and paid > 0:
            continue
        reason = d['reason'] or hint(inv, inv['total'] - paid)
        out.append({'id': inv['id'], 'number': inv['number'], 'recipient': inv['recipient'], 'open': inv['total'] - paid,
                    'mandate': d['mandate'], 'iban': d['iban'], 'announced': d['announced'], 'date': d['date'], 'late': d['late'],
                    'state': d['state'], 'reason': reason,
                    'soon': d['date'] <= (date.fromisoformat(today) + timedelta(days=SOON_DAYS)).isoformat()})
    return out


def create_batch(store, ids, actor):
    """Lastschriftdatei für die gewählten Rechnungen anlegen (Status „erstellt“). Liefert die Batch-ID."""
    cfg = store.config()
    if not enabled(cfg):
        raise ValueError('Gläubiger-Identifikationsnummer fehlt oder ist ungültig (Stammdaten).')
    if not iban_valid(cfg.get('iban')):
        raise ValueError('Eigene IBAN in den Stammdaten fehlt oder ist ungültig.')
    wanted = {int(i) for i in ids}
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        items = [c for c in collectable(store) if c['id'] in wanted]
        bad = [c for c in items if c['reason']]
        if bad or len(items) != len(wanted):
            raise ValueError('Mindestens eine Rechnung kann nicht eingezogen werden – bitte die Liste neu öffnen.')
        if not items:
            raise ValueError('Bitte Rechnungen auswählen.')
        now = datetime.now()
        msg_id = f"JUHH-{now:%Y%m%d%H%M%S}-{store.db.execute('SELECT COALESCE(MAX(id),0)+1 FROM debit_batches').fetchone()[0]}"
        c = store.db.execute('INSERT INTO debit_batches(msg_id,created,count,total,actor) VALUES(?,?,?,?,?)',
                             (msg_id, now.isoformat(timespec='seconds'), len(items), sum(i['open'] for i in items), actor))
        batch = c.lastrowid
        for i in items:
            mandate = store.db.execute('SELECT id FROM mandates WHERE reference=?', (i['mandate'],)).fetchone()[0]
            store.db.execute('INSERT INTO debits(batch_id,invoice_id,mandate_id,amount,collection_date,end_to_end) VALUES(?,?,?,?,?,?)',
                             (batch, i['id'], mandate, i['open'], i['date'], i['number']))
        store.audit(actor, 'Lastschriftdatei erstellt', {'msg_id': msg_id, 'count': len(items), 'cents': sum(i['open'] for i in items)})
    return batch


def issues(store, batch_id, today=None):
    """Erneute Prüfung einer noch nicht eingereichten Datei: debit_id → Grund, warum diese Lastschrift nicht mehr raus darf."""
    from versand import paid_amount
    today = today or date.today().isoformat()
    hint = _incoming(store)
    downloaded = store.db.execute('SELECT downloaded_on FROM debit_batches WHERE id=?', (batch_id,)).fetchone()[0]
    found = {}
    for d in store.rows("SELECT d.*,i.status inv_status,i.total,i.number,i.recipient,m.status m_status FROM debits d JOIN invoices i ON i.id=d.invoice_id "
                        "JOIN mandates m ON m.id=d.mandate_id WHERE d.batch_id=? AND d.status='pending' ORDER BY d.id", (batch_id,)):
        mandate = store.db.execute('SELECT * FROM mandates WHERE id=?', (d['mandate_id'],)).fetchone()
        paid = paid_amount(store, d['invoice_id'])
        reason = ('Rechnung storniert' if d['inv_status'] != 'issued'
                  else 'Mandat widerrufen' if d['m_status'] != 'active'
                  else f'Mandat verfallen ({MANDATE_MONTHS} Monate ungenutzt)' if d['collection_date'] > expires(store, mandate, d['id'])
                  else 'Rechnung inzwischen bezahlt' if paid >= d['total']
                  else f"Betrag geändert (offen {euro(d['total'] - paid)})" if d['total'] - paid != d['amount']
                  else hint(dict(d, id=d['invoice_id']), d['amount'])
                  or ((f"Einzugsdatum {german_date(d['collection_date'])} vorbei oder zu nah – wurde die Datei schon hochgeladen?" if downloaded
                       else f"Einzugsdatum {german_date(d['collection_date'])} zu nah oder vorbei – herausnehmen und neu einziehen")
                      if d['collection_date'] < earliest(today) else None))
        if reason:
            found[d['id']] = reason
    return found


def batches(store, today=None):
    rows = store.rows('SELECT * FROM debit_batches ORDER BY id DESC')
    for b in rows:
        b['items'] = store.rows('SELECT d.*,i.number,i.recipient FROM debits d JOIN invoices i ON i.id=d.invoice_id WHERE d.batch_id=? ORDER BY d.id',
                                (b['id'],))
        b['dates'] = sorted({d['collection_date'] for d in b['items'] if d['status'] != 'cancelled'})
        found = issues(store, b['id'], today) if b['status'] == 'created' else {}
        for d in b['items']:
            d['issue'] = found.get(d['id'])
        b['issues'] = len(found)
    return rows


DOWNLOADED = ('Die Datei wurde schon heruntergeladen und liegt vielleicht bei der Bank. Hochgeladen: als eingereicht markieren; '
              'sonst ganz verwerfen (mit Grund) und neu erstellen.')


def remove_debit(store, debit_id, actor):
    """Eine Lastschrift aus einer noch nie heruntergeladenen Datei nehmen – eine heruntergeladene Datei bleibt, wie sie ist."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        d = store.db.execute("SELECT d.*,b.status b_status,b.downloaded_on,b.msg_id,i.number FROM debits d JOIN debit_batches b ON b.id=d.batch_id "
                             "JOIN invoices i ON i.id=d.invoice_id WHERE d.id=?", (debit_id,)).fetchone()
        if not d or d['status'] != 'pending' or d['b_status'] != 'created':
            raise ValueError('Nur Lastschriften aus einer noch nicht eingereichten Datei lassen sich herausnehmen.')
        if d['downloaded_on']:
            raise ValueError(DOWNLOADED)
        store.db.execute("UPDATE debits SET status='cancelled' WHERE id=?", (debit_id,))
        count, total = store.db.execute("SELECT COUNT(*),COALESCE(SUM(amount),0) FROM debits WHERE batch_id=? AND status='pending'",
                                        (d['batch_id'],)).fetchone()
        store.db.execute("UPDATE debit_batches SET count=?,total=?,status=CASE WHEN ?=0 THEN 'cancelled' ELSE status END,"
                         "cancel_reason=CASE WHEN ?=0 THEN 'letzte Lastschrift herausgenommen' ELSE cancel_reason END WHERE id=?",
                         (count, total, count, count, d['batch_id']))
        store.audit(actor, 'Lastschrift aus Datei herausgenommen', {'msg_id': d['msg_id'], 'invoice': d['number'], 'left': count})


def _received(store, batch_id, since, debit_id=None):
    """Rechnungen der Datei mit Zahlungseingang ab since (verbucht oder im Kontoauszug mit Rechnungsnummer)."""
    import verbindlichkeiten
    in_bank = verbindlichkeiten.invoices_in_bank(store)
    out = []
    for d in store.rows("SELECT d.*,i.number FROM debits d JOIN invoices i ON i.id=d.invoice_id WHERE d.batch_id=? AND d.status='pending'"
                        + (' AND d.id=?' if debit_id else ''), (batch_id, debit_id) if debit_id else (batch_id,)):
        booked = store.db.execute("SELECT 1 FROM payments p JOIN bank k ON k.id=p.bank_id WHERE p.invoice_id=? AND p.amount>0 AND k.state='booked' "
                                  "AND k.booked>=?", (d['invoice_id'], since)).fetchone()
        if booked or in_bank.get(d['number'], '') >= since:
            out.append(d['number'])
    return out


def set_batch(store, batch_id, status, actor, reason=None):
    """Datei als eingereicht markieren oder verwerfen.

    Eingereicht: nur nach dem Herunterladen; dokumentiert, was bei der Bank liegt (Befunde der erneuten Prüfung sperren
    dann nicht mehr – betroffene Lastschriften einzeln als nicht ausgeführt markieren oder die Rücklastschrift abwarten).
    Verwerfen: eine heruntergeladene oder eingereichte Datei nur mit Grund und nie, wenn schon Geld für eine ihrer
    Rechnungen eingegangen ist (eingereicht: seit Erstellung; heruntergeladen: ab dem Einzugsdatum) – sonst würde nach
    dem Verwerfen ein zweites Mal eingezogen."""
    if status not in ('submitted', 'cancelled'):
        raise ValueError('Unbekannter Status.')
    reason = ' '.join(str(reason or '').split())[:200]
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b = store.db.execute('SELECT * FROM debit_batches WHERE id=?', (batch_id,)).fetchone()
        if not b or b['status'] == 'cancelled' or (status == 'submitted' and b['status'] != 'created'):
            raise ValueError('Diese Lastschriftdatei kann nicht mehr geändert werden.')
        if status == 'submitted' and not b['downloaded_on']:
            raise ValueError('Erst die Datei herunterladen und im Online-Banking hochladen.')
        if status == 'cancelled':
            if (b['downloaded_on'] or b['status'] == 'submitted') and not reason:
                raise ValueError('Bitte den Grund angeben – nur verwerfen, wenn die Bank die Datei nicht ausgeführt hat.')
            if b['downloaded_on'] or b['status'] == 'submitted':
                since = b['created'][:10] if b['status'] == 'submitted' else store.db.execute(
                    "SELECT MIN(collection_date) FROM debits WHERE batch_id=? AND status='pending'", (batch_id,)).fetchone()[0]
                paid = _received(store, batch_id, since) if since else []
                if paid:
                    raise ValueError(f"Für {', '.join(paid)} ist schon ein Zahlungseingang da – die Datei wurde offenbar ausgeführt und darf nicht "
                                     'verworfen werden. Als eingereicht markieren; nicht ausgeführte Lastschriften einzeln markieren, '
                                     'Rücklastschriften im Buchungsplatz zuordnen.')
            store.db.execute("UPDATE debits SET status='cancelled' WHERE batch_id=? AND status='pending'", (batch_id,))
        store.db.execute('UPDATE debit_batches SET status=?,submitted_on=?,cancel_reason=? WHERE id=?',
                         (status, date.today().isoformat() if status == 'submitted' else b['submitted_on'],
                          reason or None if status == 'cancelled' else None, batch_id))
        store.audit(actor, 'Lastschriftdatei eingereicht' if status == 'submitted' else 'Lastschriftdatei verworfen',
                    {'msg_id': b['msg_id'], **({'reason': reason} if reason else {})})


def reject_debit(store, debit_id, reason, actor):
    """Einzelne Lastschrift einer eingereichten Datei, die die Bank nicht ausgeführt hat (ohne Geldfluss): die Rechnung
    fällt auf Überweisung zurück. Mit Zahlungseingang ist es eine Rücklastschrift – die wird im Buchungsplatz zugeordnet."""
    reason = ' '.join(str(reason or '').split())[:200]
    if not reason:
        raise ValueError('Bitte den Grund angeben (z. B. Ablehnung durch die Bank).')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        d = store.db.execute("SELECT d.*,b.status b_status,b.created b_created,b.msg_id,i.number FROM debits d JOIN debit_batches b ON b.id=d.batch_id "
                             "JOIN invoices i ON i.id=d.invoice_id WHERE d.id=?", (debit_id,)).fetchone()
        if not d or d['status'] != 'pending' or d['b_status'] != 'submitted':
            raise ValueError('Nur ausstehende Lastschriften einer eingereichten Datei lassen sich als nicht ausgeführt markieren.')
        if _received(store, d['batch_id'], d['b_created'][:10], debit_id):
            raise ValueError(f"Für {d['number']} ist schon ein Zahlungseingang da – die Lastschrift wurde ausgeführt. "
                             'Eine Rückgabe als Rücklastschrift im Buchungsplatz zuordnen.')
        store.db.execute("UPDATE debits SET status='returned',returned_on=?,note=? WHERE id=?", (date.today().isoformat(), reason, debit_id))
        store.audit(actor, 'Lastschrift nicht ausgeführt', {'msg_id': d['msg_id'], 'invoice': d['number'], 'reason': reason})


def on_invoice_payment(store, invoice_id, amount, day):
    """Aus core.book: Rückbuchung genau des eingezogenen Betrags = Rücklastschrift (eine andere Erstattung nicht)."""
    if amount < 0:
        store.db.execute("UPDATE debits SET status='returned',returned_on=? WHERE id=(SELECT MAX(id) FROM debits WHERE invoice_id=? "
                         "AND status='pending' AND amount=?)", (day, invoice_id, -amount))


def on_payment_reversed(store, invoice_id, amount, day):
    """Aus core.reverse_bank: Zuordnung einer Rücklastschrift storniert → Lastschrift gilt wieder als eingezogen."""
    if amount < 0:
        store.db.execute("UPDATE debits SET status='pending',returned_on=NULL WHERE id=(SELECT MAX(id) FROM debits WHERE invoice_id=? "
                         "AND status='returned' AND returned_on=? AND amount=?)", (invoice_id, day, -amount))


# ---------------------------------------------------------------- Datei pain.008.001.08

def _el(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, f'{{{NS}}}{tag}', attrs)
    if text is not None:
        e.text = str(text)
    return e


def _agent(parent, tag, bic):
    fin = _el(_el(parent, tag), 'FinInstnId')
    if bic:
        _el(fin, 'BICFI', bic)
    else:
        _el(_el(fin, 'Othr'), 'Id', 'NOTPROVIDED')


def _amount(cents):
    return f'{cents // 100}.{cents % 100:02d}'


def download(store, batch_id, actor):
    """Datei zum Hochladen: nur solange nicht eingereicht und ohne Befund der erneuten Prüfung; Download wird vermerkt."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b = store.db.execute('SELECT * FROM debit_batches WHERE id=?', (batch_id,)).fetchone()
        if not b or b['status'] != 'created':
            raise ValueError('Nur eine noch nicht eingereichte Datei lässt sich herunterladen – eine eingereichte nie ein zweites Mal hochladen.')
        found = issues(store, batch_id)
        if found:
            raise ValueError(f'{len(found)} Lastschrift(en) der Datei dürfen nicht mehr eingezogen werden – '
                             + (DOWNLOADED if b['downloaded_on'] else 'erst herausnehmen.'))
        xml = xml_file(store, batch_id)
        store.db.execute('UPDATE debit_batches SET downloaded_on=COALESCE(downloaded_on,?) WHERE id=?', (datetime.now().isoformat(timespec='seconds'), batch_id))
        store.audit(actor, 'Lastschriftdatei heruntergeladen', {'msg_id': b['msg_id']})
    return xml


def xml_file(store, batch_id):
    """pain.008.001.08 für eine Lastschriftdatei (je Einzugsdatum und Gläubiger-ID ein Zahlungsblock, Einzelbuchung gewünscht).
    Ohne Prüfung – zum Hochladen nur über download(); sonst als Archivkopie (Kassenprüfung)."""
    import json
    cfg = store.config()
    b = store.db.execute('SELECT * FROM debit_batches WHERE id=?', (batch_id,)).fetchone()
    if not b or b['status'] == 'cancelled':
        raise ValueError('Lastschriftdatei nicht gefunden oder verworfen.')
    items = store.rows("SELECT d.*,i.number,i.year,i.kind,i.snapshot,m.reference,m.signed_on,m.holder,m.iban,m.bic FROM debits d "
                       "JOIN invoices i ON i.id=d.invoice_id JOIN mandates m ON m.id=d.mandate_id WHERE d.batch_id=? AND d.status!='cancelled' ORDER BY d.id",
                       (batch_id,))
    ET.register_namespace('', NS)
    doc = ET.Element(f'{{{NS}}}Document')
    root = _el(doc, 'CstmrDrctDbtInitn')
    head = _el(root, 'GrpHdr')
    _el(head, 'MsgId', b['msg_id'])
    _el(head, 'CreDtTm', b['created'][:19])
    _el(head, 'NbOfTxs', len(items))
    _el(head, 'CtrlSum', _amount(sum(i['amount'] for i in items)))
    _el(_el(head, 'InitgPty'), 'Nm', sepa_text(cfg['issuer'], 70))
    for i in items:  # Gläubiger-ID wie in der Vorabankündigung festgeschrieben
        i['creditor_id'] = json.loads(i['snapshot'])['debit']['creditor_id']
    for n, (day, creditor_id) in enumerate(sorted({(i['collection_date'], i['creditor_id']) for i in items}), 1):
        group = [i for i in items if (i['collection_date'], i['creditor_id']) == (day, creditor_id)]
        pmt = _el(root, 'PmtInf')
        _el(pmt, 'PmtInfId', f"{b['msg_id']}-{n}"[:35])
        _el(pmt, 'PmtMtd', 'DD')
        _el(pmt, 'BtchBookg', 'false')  # Einzelbuchung gewünscht (Abgleich je Rechnung) – gilt nur, wenn die Bank sie zulässt
        _el(pmt, 'NbOfTxs', len(group))
        _el(pmt, 'CtrlSum', _amount(sum(i['amount'] for i in group)))
        tp = _el(pmt, 'PmtTpInf')
        _el(_el(tp, 'SvcLvl'), 'Cd', 'SEPA')
        _el(_el(tp, 'LclInstrm'), 'Cd', 'CORE')
        _el(tp, 'SeqTp', 'RCUR')
        _el(pmt, 'ReqdColltnDt', day)
        _el(_el(pmt, 'Cdtr'), 'Nm', sepa_text(cfg['issuer'], 70))
        _el(_el(_el(pmt, 'CdtrAcct'), 'Id'), 'IBAN', iban_compact(cfg['iban']))
        _agent(pmt, 'CdtrAgt', (cfg.get('bic') or '').upper().replace(' ', '') or None)
        _el(pmt, 'ChrgBr', 'SLEV')
        other = _el(_el(_el(_el(pmt, 'CdtrSchmeId'), 'Id'), 'PrvtId'), 'Othr')
        _el(other, 'Id', creditor_id)
        _el(_el(other, 'SchmeNm'), 'Prtry', 'SEPA')
        for i in group:
            tx = _el(pmt, 'DrctDbtTxInf')
            _el(_el(tx, 'PmtId'), 'EndToEndId', sepa_text(i['end_to_end'], 35).replace(' ', ''))
            _el(tx, 'InstdAmt', _amount(i['amount']), Ccy='EUR')
            rel = _el(_el(tx, 'DrctDbtTx'), 'MndtRltdInf')
            _el(rel, 'MndtId', i['reference'])
            _el(rel, 'DtOfSgntr', i['signed_on'])
            _agent(tx, 'DbtrAgt', i['bic'])
            _el(_el(tx, 'Dbtr'), 'Nm', sepa_text(i['holder'], 70))
            _el(_el(_el(tx, 'DbtrAcct'), 'Id'), 'IBAN', i['iban'])
            purpose = (f"Mitgliedsbeitrag {i['year']} " if i['kind'] == 'dues' else '') + i['number']
            _el(_el(tx, 'RmtInf'), 'Ustrd', sepa_text(purpose, 140))
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(doc, encoding='utf-8')


def filename(store, batch_id):
    b = store.db.execute('SELECT msg_id FROM debit_batches WHERE id=?', (batch_id,)).fetchone()
    return f"Lastschrift-{b['msg_id']}.xml"


# ---------------------------------------------------------------- Mandatsformular

def mandate_pdf(store, member_id=None, member_ids=None, mit_mandat=True):
    """SEPA-Lastschriftmandat zum Unterschreiben (vorausgefüllt mit Mitglied, Bankdaten des Mitglieds und ggf. Mandat);
    mit member_ids eine Seite je Mitglied (ab 0.53, Serie aus der Mitgliederverwaltung – dort mit_mandat=False: nie Daten eines
    bestehenden Mandats, die sieht nur die Buchhaltung)."""
    from pdfschrift import exclusive
    return exclusive(_mandate_pdf)(store, member_ids or [member_id], mit_mandat)


def _mandate_pdf(store, member_ids, mit_mandat=True):
    import io
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate
    from pdfschrift import SANS, SANS_BOLD
    cfg = store.config()
    navy, muted = colors.HexColor('#043770'), colors.HexColor('#5b6b7f')
    st = {'t': ParagraphStyle('t', fontName=SANS_BOLD, fontSize=15, leading=19, textColor=navy),
          'b': ParagraphStyle('b', fontName=SANS, fontSize=10, leading=14.5),
          's': ParagraphStyle('s', fontName=SANS, fontSize=8, leading=11, textColor=muted),
          'h': ParagraphStyle('h', fontName=SANS_BOLD, fontSize=10, leading=14, spaceBefore=8)}
    p = lambda text, style='b': Paragraph(escape(str(text)).replace('\n', '<br/>'), st[style])
    issuer = cfg.get('issuer') or 'Junge Union Hamburg'
    line = lambda label, value='': [p(label, 's'), p(value or ' ')]
    story = []
    for member_id in member_ids:
        if story:
            story.append(PageBreak())
        story += _mandat_seite(store, cfg, member_id, p, line, issuer, mit_mandat)
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=55, rightMargin=55, topMargin=50, bottomMargin=50,
                      title='SEPA-Lastschriftmandat').build(story)
    return buf.getvalue()


def _mandat_seite(store, cfg, member_id, p, line, issuer, mit_mandat=True):
    from reportlab.lib import colors
    from reportlab.platypus import Spacer, Table
    m = store.db.execute('SELECT * FROM members WHERE id=?', (member_id,)).fetchone() if member_id else None
    mandate = active_mandate(store, member_id) if member_id and mit_mandat else None
    if mandate and (expires(store, mandate) < date.today().isoformat() or (m and m['iban'] and m['iban'] != mandate['iban'])):
        mandate = None   # verfallen oder Kontowechsel: Formular für ein neues Mandat mit den Bankdaten des Mitglieds (ab 0.53)
    bank = {'holder': (m['kontoinhaber'] or m['name']) if m else '', 'iban': m['iban'] if m and m['iban'] else ''}   # Bankdaten am Mitglied (ab 0.53)
    rows = [line('Zahlungsempfänger', f"{issuer}\n{cfg.get('issuer_address', '')}"),
            line('Gläubiger-Identifikationsnummer', re.sub(r'\s+', '', cfg.get('creditor_id') or '').upper()),
            line('Mandatsreferenz', mandate['reference'] if mandate else 'wird mit der ersten Beitragsrechnung mitgeteilt'),
            line('Kontoinhaber (Vor- und Nachname)', mandate['holder'] if mandate else bank['holder']),
            line('Anschrift', m['address'] if m else ''),
            line('Mitgliedsnummer', m['number'] if m else ''),
            line('IBAN', iban_pretty(mandate['iban']) if mandate else iban_pretty(bank['iban']) if bank['iban'] else ''),
            line('BIC (optional)', (mandate['bic'] or '') if mandate else '')]
    table = Table(rows, colWidths=[150, 330], style=[('LINEBELOW', (1, 0), (1, -1), 0.5, colors.HexColor('#d5dde8')),
                                                      ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 7),
                                                      ('BOTTOMPADDING', (0, 0), (-1, -1), 7)])
    return [p('SEPA-Lastschriftmandat', 't'), p('für wiederkehrende Zahlungen (Mitgliedsbeitrag)', 's'), Spacer(1, 14), table, Spacer(1, 14),
             p(f'Ich ermächtige den Zahlungsempfänger {issuer}, Zahlungen von meinem Konto mittels Lastschrift einzuziehen. Zugleich weise ich '
               f'mein Kreditinstitut an, die vom Zahlungsempfänger {issuer} auf mein Konto gezogenen Lastschriften einzulösen.'),
             Spacer(1, 6),
             p('Hinweis: Ich kann innerhalb von acht Wochen, beginnend mit dem Belastungsdatum, die Erstattung des belasteten Betrages '
               'verlangen. Es gelten dabei die mit meinem Kreditinstitut vereinbarten Bedingungen.'),
             Spacer(1, 6),
             p(f"Die Vorabankündigung (Betrag und Einzugsdatum) erfolgt mit der Beitragsrechnung mindestens {cfg.get('sepa_prenotice', '14')} "
               'Tage vor dem Einzug.', 's'),
             Spacer(1, 40),
             Table([['', '', ''], [p('Ort, Datum', 's'), '', p('Unterschrift Kontoinhaber', 's')]], colWidths=[180, 40, 260],
                   style=[('LINEABOVE', (0, 1), (0, 1), 0.6, colors.black), ('LINEABOVE', (2, 1), (2, 1), 0.6, colors.black)])]
