"""E-Mail-Versand von Rechnungen über SMTP (z. B. Strato) – Nachricht, Verbindung, Versandauftrag.

Ablauf: Vorschau → Testmail an die eigene Adresse → Versandauftrag im Hintergrund. Jede versendete
oder fehlgeschlagene Nachricht wird in der Tabelle mailings protokolliert (kind: Rechnung oder
Zahlungserinnerung); bereits versendete Rechnungen werden nur auf ausdrücklichen Wunsch erneut verschickt.
Zahlungserinnerungen nur für überfällige, offene Rechnungen ohne Zahlungseingang im Kontoauszug und mit
Abstand zur letzten Erinnerung (Stammdaten).
"""
import base64
import json
import smtplib
import socket
import ssl
import threading
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape

import geheimnis
import lastschrift
import rechnung_pdf
import stammdaten
import zugferd
from formate import email_address, euro, german_date, iban_pretty

STATES = ('idle', 'running', 'done', 'cancelled', 'aborted')
_job = {'state': 'idle'}
_job_lock = threading.Lock()
_cancel = threading.Event()
_thread = None


# ---------------------------------------------------------------- Nachricht

def _clean_header(text):
    return ' '.join(str(text or '').split())


def texts(cfg, inv, test=False):
    """Betreff und Text aus der Vorlage für die Rechnungsart (oder die Zahlungserinnerung, wenn inv['reminder'])."""
    kind = 'reminder' if inv.get('reminder') else 'dues' if inv['kind'] == 'dues' else 'normal'
    values = stammdaten.invoice_values(inv, sender=cfg.get('mail_from_name'))
    subject = _clean_header(stammdaten.fill(cfg[f'tpl_{kind}_subject'], values))
    debit = kind == 'dues' and rechnung_pdf.debit_of(inv)  # Beitrag per Lastschrift: Text mit Vorabankündigung
    body = stammdaten.fill(cfg['tpl_debit_body' if debit else f'tpl_{kind}_body'], values).replace('\r\n', '\n').strip()
    if test:
        subject = '[TEST] ' + subject
        body = ('Testnachricht – so sieht die E-Mail für den Empfänger aus. Sie wurde nur an dich gesendet; '
                'die Rechnung gilt dadurch nicht als versendet.\n\n') + body
    return subject, body


def html_body(cfg, inv, body):
    """Schlichte HTML-Fassung (Inline-Stile, ohne externe Bilder oder Skripte)."""
    snap = inv['snap']
    paragraphs = ''.join(
        f'<p style="margin:0 0 14px">{"<br>".join(escape(line) for line in block.splitlines())}</p>'
        for block in body.split('\n\n') if block.strip())
    reminder = inv.get('reminder')
    rows = [('Rechnungsnummer', inv['number'])] + ([('Rechnungsdatum', german_date(inv['issued']))] if reminder else []) + [('Betrag', euro(inv['total']))]
    if inv.get('paid'):
        rows += [('Bereits bezahlt', euro(inv['paid'])), ('Noch offen', euro(inv['total'] - inv['paid']))]
    elif reminder:
        rows += [('Noch offen', euro(inv['total']))]
    debit = not reminder and rechnung_pdf.debit_of(inv)
    if inv['total'] > 0 and inv['status'] == 'issued' and debit:
        rows += [('Zahlungsart', 'SEPA-Lastschrift'), ('Einzug am', german_date(debit['date'])), ('Von Konto', debit['iban']),
                 ('Mandatsreferenz', debit['mandate']), ('Gläubiger-ID', debit['creditor_id'])]
    elif inv['total'] > 0 and inv['status'] == 'issued':
        rows += [('Bitte zahlen bis' if reminder else 'Zahlbar bis', german_date(reminder['deadline'] if reminder else lastschrift.payable_by(inv))),
                 ('IBAN', iban_pretty(snap.get('iban', ''))), ('Verwendungszweck', inv['number'])]
    facts = ''.join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#5b6b7f;font-size:13px">{escape(k)}</td>'
        f'<td style="padding:4px 0;font-size:14px;font-weight:bold;color:#1c2a3a">{escape(v)}</td></tr>' for k, v in rows)
    address = ' · '.join([snap.get('issuer', '')] + [l.strip() for l in snap.get('issuer_address', '').splitlines() if l.strip()])
    contact = ' · '.join(v for v in (snap.get('contact_email'), snap.get('website')) if v)
    head = escape(('Zahlungserinnerung · ' if reminder else '') + (cfg.get('mail_from_name') or snap.get('issuer') or 'Rechnung'))
    return f'''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:#f4f6f9">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f6f9"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;font-family:Helvetica,Arial,sans-serif;color:#1c2a3a">
<tr><td style="background:#043770;border-radius:12px 12px 0 0;padding:20px 28px;color:#ffffff;font-size:18px;font-weight:bold">{head}</td></tr>
<tr><td style="padding:28px 28px 8px;font-size:15px;line-height:1.55">{paragraphs}</td></tr>
<tr><td style="padding:0 28px 24px"><table role="presentation" cellspacing="0" cellpadding="0" width="100%" style="background:#eef3f9;border-radius:10px"><tr><td style="padding:14px 18px">
<table role="presentation" cellspacing="0" cellpadding="0">{facts}</table></td></tr></table>
<p style="margin:12px 0 0;font-size:12px;color:#5b6b7f">{'Die Rechnung hängt als PDF an; der Betrag wird per Lastschrift eingezogen.' if debit else ('Die ursprüngliche Rechnung hängt als PDF an.' if reminder else 'Die Rechnung hängt als PDF an.') + ' Mit dem QR-Code (GiroCode) auf der Rechnung kannst du direkt in deiner Banking-App überweisen.'}</p></td></tr>
<tr><td style="padding:16px 28px;background:#f4f6f9;border-radius:0 0 12px 12px;font-size:12px;line-height:1.5;color:#5b6b7f">{escape(address)}{'<br>' + escape(contact) if contact else ''}</td></tr>
</table></td></tr></table></body></html>'''


def message(cfg, inv, pdf, to, test=False):
    if not cfg.get('mail_from'):
        raise ValueError('Absenderadresse in den Stammdaten (E-Mail-Versand) hinterlegen.')
    subject, body = texts(cfg, inv, test)
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr((_clean_header(cfg.get('mail_from_name')), cfg['mail_from']))
    msg['To'] = formataddr((_clean_header(inv['recipient']) if not test else '', to))
    if cfg.get('mail_reply_to'):
        msg['Reply-To'] = cfg['mail_reply_to']
    if cfg.get('mail_bcc') and not test:
        msg['Bcc'] = cfg['mail_bcc']
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=cfg['mail_from'].rsplit('@', 1)[1])
    msg.set_content(body + '\n')
    msg.add_alternative(html_body(cfg, inv, body), subtype='html')
    msg.add_attachment(pdf, maintype='application', subtype='pdf', filename=rechnung_pdf.filename(inv))
    return msg


def recipient_address(store, inv):
    """Versandadresse: an der Rechnung hinterlegt, sonst die aktuelle E-Mail des Mitglieds."""
    if inv.get('email'):
        return inv['email']
    if inv.get('member_id'):
        row = store.db.execute('SELECT email FROM members WHERE id=?', (inv['member_id'],)).fetchone()
        return (row['email'] or '') if row else ''
    return ''


# ---------------------------------------------------------------- Verbindung

def tls_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class _Tracking:
    """Merkt sich, ob die Nachricht schon übertragen wurde (DATA begonnen)."""
    data_started = False

    def data(self, msg):
        self.data_started = True
        return super().data(msg)


class _SMTP(_Tracking, smtplib.SMTP):
    pass


class _SMTP_SSL(_Tracking, smtplib.SMTP_SSL):
    pass


class RecipientRefused(Exception):
    """Der Hauptempfänger wurde abgelehnt (eine angenommene Bcc-Kopie zählt nicht als Zustellung)."""


class Uncertain(Exception):
    """Verbindung brach nach der Übertragung ab – ob die E-Mail angekommen ist, ist unklar."""


class ConnectionLost(Exception):
    """Verbindung vor der Übertragung verloren – die E-Mail wurde nicht versendet."""


def friendly(error):
    if isinstance(error, ValueError) and not isinstance(error, UnicodeError):
        return str(error)
    if isinstance(error, Uncertain):
        return 'Verbindung brach während der Übertragung ab – Zustellung unklar. Bitte im Postfach (Bcc/Gesendet) prüfen, bevor erneut gesendet wird.'
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return 'Anmeldung beim Mailserver fehlgeschlagen – Benutzername und Passwort prüfen.'
    if isinstance(error, (smtplib.SMTPRecipientsRefused, RecipientRefused)):
        return 'Empfängeradresse wurde vom Mailserver abgelehnt.'
    if isinstance(error, smtplib.SMTPSenderRefused):
        return 'Absenderadresse wurde vom Mailserver abgelehnt (muss zum E-Mail-Konto passen).'
    if isinstance(error, ssl.SSLError):
        return 'Sichere Verbindung zum Mailserver fehlgeschlagen – Port und Verschlüsselung prüfen.'
    if isinstance(error, (socket.timeout, TimeoutError)):
        return 'Zeitüberschreitung – Mailserver antwortet nicht.'
    if isinstance(error, (ConnectionLost, smtplib.SMTPServerDisconnected)):
        return 'Verbindung zum Mailserver verloren – E-Mail nicht versendet.'
    if isinstance(error, smtplib.SMTPException):
        return 'Mailserver meldet einen Fehler: ' + str(error)[:200]
    if isinstance(error, OSError):
        return 'Mailserver nicht erreichbar – Servername, Port und Internetverbindung prüfen.'
    if isinstance(error, UnicodeError):
        return 'Ungültige Zeichen in Adresse oder Text.'
    return 'Unerwarteter Fehler beim Versand.'


def _login(smtp, user, password):
    """AUTH PLAIN mit UTF-8 (smtplib selbst kann nur ASCII-Passwörter); sonst Standardverfahren."""
    smtp.ehlo_or_helo_if_needed()
    if 'PLAIN' in smtp.esmtp_features.get('auth', '').upper().split():
        token = base64.b64encode(b'\0' + user.encode('utf-8') + b'\0' + password.encode('utf-8')).decode('ascii')
        code, resp = smtp.docmd('AUTH', 'PLAIN ' + token)
        if code != 235:
            raise smtplib.SMTPAuthenticationError(code, resp)
        return
    smtp.login(user, password)


def connect(cfg, password, timeout=30):
    host, port, security = cfg.get('smtp_host'), int(cfg.get('smtp_port') or 0), cfg.get('smtp_security')
    if not host or not port or not cfg.get('smtp_user'):
        raise ValueError('E-Mail-Konto in den Stammdaten vervollständigen (Server, Port, Benutzername).')
    if not password:
        raise ValueError('Passwort des E-Mail-Kontos fehlt – in den Stammdaten unter „E-Mail-Versand“ eingeben.')
    if security == 'ssl':
        smtp = _SMTP_SSL(host, port, timeout=timeout, context=tls_context())
    elif security == 'starttls':
        smtp = _SMTP(host, port, timeout=timeout)
        smtp.ehlo()
        smtp.starttls(context=tls_context())
        smtp.ehlo()
    elif security == 'none' and host in stammdaten.LOCAL_HOSTS:
        smtp = _SMTP(host, port, timeout=timeout)
    else:
        raise ValueError('Unverschlüsselte Verbindung ist nicht erlaubt.')
    try:
        _login(smtp, cfg['smtp_user'], password)
    except Exception:
        smtp.close()
        raise
    return smtp


def password_for(cfg):
    return geheimnis.load(cfg.get('smtp_user'))


def check(cfg):
    """Nur anmelden und wieder abmelden – es wird nichts versendet."""
    try:
        smtp = connect(cfg, password_for(cfg), timeout=20)
        smtp.quit()
    except Exception as e:
        raise ValueError(friendly(e))


def deliver(smtp, msg, to):
    """Genau ein Übertragungsversuch – nie automatisch wiederholen (sonst Gefahr doppelter E-Mails)."""
    smtp.data_started = False
    try:
        refused = smtp.send_message(msg) or {}
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise  # vom Server eindeutig abgelehnt → nicht zugestellt
    except (smtplib.SMTPServerDisconnected, OSError) as e:
        if getattr(smtp, 'data_started', False):
            raise Uncertain(str(e)) from e
        raise ConnectionLost(str(e)) from e
    if to.lower() in {r.lower() for r in refused}:
        raise RecipientRefused(to)


def alive(smtp):
    try:
        return smtp is not None and smtp.noop()[0] == 250
    except (smtplib.SMTPException, OSError):
        return False


def send_one(cfg, msg):
    smtp = connect(cfg, password_for(cfg))
    try:
        deliver(smtp, msg, msg['To'].addresses[0].addr_spec)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def sample_invoice(cfg, kind):
    """Erfundene Beispielrechnung für die Vorschau der Vorlagen (kind 'reminder': Erinnerung zur Beispielrechnung)."""
    today = date.today().isoformat()
    if kind == 'reminder':
        inv = rechnung_pdf.draft('normal', cfg, 'Max Mustermann', 'Musterweg 1\n20000 Hamburg',
                                 [{'description': 'Teilnahme Sommerfest', 'amount': 2500}],
                                 (date.today() - timedelta(days=30)).isoformat(), (date.today() - timedelta(days=44)).isoformat(),
                                 member_number='M-0001', number='R-0000-00000')
        inv['reminder'] = reminder_terms(cfg, 1)
        return inv
    if kind == 'dues':
        return rechnung_pdf.draft('dues', cfg, 'Max Mustermann', 'Musterweg 1\n20000 Hamburg',
                                  [{'description': f'Mitgliedsbeitrag {date.today().year + 1}', 'amount': 3000}],
                                  f'{date.today().year + 1}-01-15', today, year=date.today().year + 1,
                                  member_number='M-0001', number='B-0000-00000')
    return rechnung_pdf.draft('normal', cfg, 'Max Mustermann', 'Musterweg 1\n20000 Hamburg',
                              [{'description': 'Teilnahme Sommerfest', 'amount': 2500}], today, today,
                              member_number='M-0001', number='R-0000-00000')


def send_test(cfg, inv):
    """Schickt die Nachricht zu einer Rechnung als Test an die eigene Absenderadresse."""
    try:
        msg = message(cfg, inv, zugferd.pdf(inv)[0], cfg['mail_from'], test=True)   # wie im Versand: E-Rechnung, wenn möglich
        send_one(cfg, msg)
    except Exception as e:
        raise ValueError(friendly(e))
    return cfg['mail_from']


# ---------------------------------------------------------------- Versandauftrag

def status():
    with _job_lock:
        job = dict(_job)
        job['errors'] = list(_job.get('errors', []))
        job['einfach'] = dict(_job.get('einfach', {}))
        return job


def busy():
    with _job_lock:
        return _job.get('state') == 'running'


def paid_amount(store, iid):
    return store.db.execute("SELECT COALESCE(SUM(p.amount),0) FROM payments p JOIN bank b ON b.id=p.bank_id "
                            "WHERE p.invoice_id=? AND b.state='booked'", (iid,)).fetchone()[0]


def plan(store, ids, resend=False):
    """Welche Rechnungen werden versendet, welche übersprungen (mit Grund).

    resend=True erlaubt nur erneutes Senden bereits versendeter (oder unklar zugestellter) Rechnungen;
    stornierte, bezahlte, beitragsfreie und Pilotrechnungen werden nie per E-Mail verschickt."""
    send, skipped = [], []
    for iid in dict.fromkeys(int(i) for i in ids):
        inv = store.db.execute('SELECT * FROM invoices WHERE id=?', (iid,)).fetchone()
        if not inv:
            raise ValueError('Rechnung nicht gefunden.')
        inv = dict(inv)
        try:
            to = email_address(recipient_address(store, inv))
            invalid = False
        except ValueError:
            to, invalid = '', True
        last = store.db.execute("SELECT status FROM mailings WHERE invoice_id=? AND kind='invoice' ORDER BY id DESC LIMIT 1", (iid,)).fetchone()
        sent = store.db.execute("SELECT 1 FROM mailings WHERE invoice_id=? AND kind='invoice' AND status='sent'", (iid,)).fetchone()
        pilot = json.loads(inv['snapshot']).get('pilot')
        paid = paid_amount(store, iid)
        reason = ('storniert' if inv['status'] != 'issued'
                  else 'Pilotrechnung aus einer Vorversion' if pilot
                  else 'Betrag 0 €' if inv['total'] <= 0
                  else 'bereits bezahlt' if paid >= inv['total']
                  else 'ungültige E-Mail-Adresse' if invalid
                  else 'keine E-Mail-Adresse' if not to
                  else 'Zustellung unklar – erst im Postfach prüfen' if last and last['status'] == 'unknown' and not resend
                  else 'bereits versendet' if sent and not resend
                  else None)
        if reason:
            skipped.append({'id': iid, 'number': inv['number'], 'reason': reason})
        else:
            send.append({'id': iid, 'number': inv['number'], 'to': to, 'paid': paid})
    return send, skipped


def reminder_terms(cfg, level, today=None):
    """Angaben einer Zahlungserinnerung: laufende Nummer und neue Frist (heute + Frist laut Stammdaten)."""
    today = date.fromisoformat(today) if today else date.today()
    return {'level': level, 'deadline': (today + timedelta(days=int(cfg['reminder_terms']))).isoformat()}


def reminders(store, ids=None, resend=False, today=None):
    """Überfällige offene Rechnungen mit Erinnerungsstand; reason None = Erinnerung kann jetzt raus.

    ids=None: alle überfälligen offenen Rechnungen (Übersicht). resend=True übergeht nur „letzte Erinnerung zu
    kurz her“, „Zustellung unklar“ und „Rechnung nie per E-Mail versendet“ – nie bezahlte, stornierte oder im
    Kontoauszug vermutlich schon bezahlte Rechnungen. Als Versuch zählt auch eine unklar zugestellte Nachricht."""
    import lastschrift
    import verbindlichkeiten
    cfg, today = store.config(), today or date.today().isoformat()
    after, gap = int(cfg['reminder_after']), int(cfg['reminder_gap'])
    incoming = verbindlichkeiten.incoming_hint(store)
    if ids is None:
        rows = store.rows("SELECT * FROM invoices WHERE status='issued' AND total>0 AND due<? ORDER BY due,id", (today,))
    else:
        rows = []
        for iid in dict.fromkeys(int(i) for i in ids):
            row = store.db.execute('SELECT * FROM invoices WHERE id=?', (iid,)).fetchone()
            if not row:
                raise ValueError('Rechnung nicht gefunden.')
            rows.append(dict(row))
    out = []
    for inv in rows:
        paid = paid_amount(store, inv['id'])
        if ids is None and paid >= inv['total']:
            continue
        try:
            to, invalid = email_address(recipient_address(store, inv)), False
        except ValueError:
            to, invalid = '', True
        tried = store.rows("SELECT time,status FROM mailings WHERE invoice_id=? AND kind='reminder' AND status IN ('sent','unknown') ORDER BY id", (inv['id'],))
        last_sent = tried[-1]['time'][:10] if tried else None
        mailed = store.db.execute("SELECT MAX(time) FROM mailings WHERE invoice_id=? AND kind='invoice' AND status IN ('sent','unknown')",
                                  (inv['id'],)).fetchone()[0]
        debit = lastschrift.info(store, inv, today)  # gilt die Lastschrift nicht mehr, wird normal erinnert (Überweisung, neue Frist)
        due = (debit or {}).get('pay_by') or inv['due']
        # frühestens nach Fälligkeit bzw. nach (verspätetem) Rechnungsversand + Wartezeit
        start = max(due, mailed[:10]) if mailed else due
        earliest = (date.fromisoformat(start) + timedelta(days=after)).isoformat()
        next_after_gap = (date.fromisoformat(last_sent) + timedelta(days=gap)).isoformat() if last_sent else None
        open_ = inv['total'] - paid
        reason = ('storniert' if inv['status'] != 'issued'
                  else 'Pilotrechnung aus einer Vorversion' if json.loads(inv['snapshot']).get('pilot')
                  else 'Betrag 0 €' if inv['total'] <= 0
                  else 'bereits bezahlt' if paid >= inv['total']
                  else f"wird per SEPA-Lastschrift eingezogen (angekündigt zum {german_date(debit['announced'])})" if debit and debit['state'] == 'pending'
                  else ('Lastschrift noch nicht eingereicht – erst die Lastschriftdatei erstellen' if debit['state'] == 'ready' else debit['reason'])
                  if debit and debit['active']
                  else incoming(inv, open_) if incoming(inv, open_)
                  else f'noch nicht lange genug überfällig (Erinnerung ab {german_date(earliest)})' if today < earliest
                  else 'ungültige E-Mail-Adresse' if invalid
                  else 'keine E-Mail-Adresse – Erinnerung per Post' if not to
                  else 'Rechnung wurde nie per E-Mail versendet – erst die Rechnung senden' if not mailed and not resend
                  else 'Zustellung der letzten Erinnerung unklar – erst im Postfach prüfen' if tried and tried[-1]['status'] == 'unknown' and not resend
                  else f'letzte Erinnerung am {german_date(last_sent)} (nächste ab {german_date(next_after_gap)})' if next_after_gap and today < next_after_gap and not resend
                  else None)
        out.append({'id': inv['id'], 'number': inv['number'], 'recipient': inv['recipient'], 'to': to, 'kind': inv['kind'],
                    'total': inv['total'], 'paid': paid, 'open': open_, 'due': due, 'mailed': mailed[:10] if mailed else None,
                    'overdue': (date.fromisoformat(today) - date.fromisoformat(due)).days,
                    'level': len(tried) + 1, 'last_reminder': last_sent, 'reason': reason})
    return out


def start(db_path, ids, actor, resend=False, kind='invoice'):
    from core import Store
    if kind not in ('invoice', 'reminder'):
        raise ValueError('Unbekannte Versandart.')
    store = Store(db_path)
    try:
        cfg = store.config()
        if not cfg.get('mail_from'):
            raise ValueError('Absenderadresse in den Stammdaten (E-Mail-Versand) hinterlegen.')
        password = password_for(cfg)
        if not password:
            raise ValueError('Passwort des E-Mail-Kontos fehlt – in den Stammdaten unter „E-Mail-Versand“ eingeben.')
        if kind == 'reminder':
            checked = reminders(store, ids, resend)
            items = [{'id': r['id'], 'number': r['number'], 'to': r['to'], 'paid': r['paid'], 'level': r['level']} for r in checked if not r['reason']]
            skipped = [{'id': r['id'], 'number': r['number'], 'reason': r['reason']} for r in checked if r['reason']]
        else:
            items, skipped = plan(store, ids, resend)
    finally:
        store.db.close()
    if not items:
        reasons = sorted({s['reason'] for s in skipped})
        what = 'Zahlungserinnerungen' if kind == 'reminder' else 'Rechnungen'
        raise ValueError(f'Keine versendbaren {what} ausgewählt' + (' (' + ', '.join(reasons) + ').' if reasons else '.'))
    global _thread
    with _job_lock:
        if _job.get('state') == 'running':
            raise ValueError('Es läuft bereits ein Versand.')
        _cancel.clear()
        _job.clear()
        _job.update(state='running', kind=kind, total=len(items), done=0, sent=0, failed=0, unknown=0, current='', errors=[], einfach={},
                    skipped=skipped, started=datetime.now().isoformat(timespec='seconds'), finished=None, actor=actor)
        _thread = threading.Thread(target=_run, args=(db_path, items, cfg, password, actor, kind, resend), daemon=True)
        _thread.start()
    return status()


def cancel():
    _cancel.set()


def _log(store, iid, to, subject, outcome, error, message_id, actor, kind='invoice'):
    """Protokolliert eine Nachricht; bei gesperrter Datenbank einige Male erneut versuchen."""
    for attempt in range(6):
        try:
            with store.db:
                store.db.execute('INSERT INTO mailings(invoice_id,time,recipient,subject,status,error,message_id,actor,kind) VALUES(?,?,?,?,?,?,?,?,?)',
                                 (iid, datetime.now().isoformat(timespec='seconds'), to, subject, outcome, error, message_id, actor, kind))
            return True
        except Exception:
            time.sleep(1 + attempt)
    return False


def _recheck(store, item, kind, resend):
    """Unmittelbar vor dem Senden erneut prüfen (Zahlung verbucht, storniert, Zahlungseingang importiert …).
    Liefert den Grund zum Überspringen oder None; bei Erinnerungen wird die Stufe aktualisiert."""
    if kind == 'reminder':
        r = reminders(store, [item['id']], resend)[0]
        item.update(level=r['level'], paid=r['paid'])
        return r['reason']
    send, skipped = plan(store, [item['id']], resend)
    if send:
        item['paid'] = send[0]['paid']
    return skipped[0]['reason'] if skipped else None


def _run(db_path, items, cfg, password, actor, kind='invoice', resend=False):
    from core import Store
    store, smtp, final, lost = None, None, 'done', 0
    pause = int(cfg.get('mail_pause') or 0)
    try:
        store = Store(db_path)
        for n, item in enumerate(items):
            if _cancel.is_set():
                final = 'cancelled'
                break
            with _job_lock:
                _job['current'] = f"{item['number']} an {item['to']}"
            subject, outcome, error, msgid, stop, einfach = item['number'], 'failed', None, None, False, None
            try:
                reason = _recheck(store, item, kind, resend)
            except Exception:
                reason = 'konnte vor dem Senden nicht geprüft werden'
            if reason:
                with _job_lock:
                    _job['done'] += 1
                    _job['skipped'].append({'id': item['id'], 'number': item['number'], 'reason': reason})
                continue
            try:
                inv = rechnung_pdf.invoice_data(store, item['id'])
                inv['email'], inv['paid'] = item['to'], item['paid']
                if kind == 'reminder':
                    inv['reminder'] = reminder_terms(cfg, item['level'])
                # Rechnung mit offenem Betrag und Frist – als E-Rechnung, sonst die bisherige PDF mit Grund; Erinnerungen bleiben normale PDF
                datei, einfach = (rechnung_pdf.render([inv]), None) if kind == 'reminder' else zugferd.pdf(inv)
                msg = message(cfg, inv, datei, item['to'])
                subject, msgid = msg['Subject'], msg['Message-ID']
            except Exception as e:  # Fehler beim Erstellen betreffen nur diese Rechnung
                error = friendly(e)
            if error is None:
                try:
                    if not alive(smtp):
                        smtp = connect(cfg, password)
                    deliver(smtp, msg, item['to'])
                    outcome, lost = 'sent', 0
                except Uncertain as e:
                    outcome, error, smtp = 'unknown', friendly(e), None
                except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused, ssl.SSLError, ValueError) as e:
                    error, stop = friendly(e), True  # Konto- oder Einstellungsfehler: jede weitere E-Mail scheitert ebenso
                except (ConnectionLost, smtplib.SMTPServerDisconnected, OSError) as e:
                    error, smtp, lost = friendly(e), None, lost + 1
                    stop = lost >= 2  # zweimal hintereinander keine Verbindung → anhalten
                except Exception as e:
                    error = friendly(e)
            logged = _log(store, item['id'], item['to'], subject, outcome, error, msgid, actor, kind)
            with _job_lock:
                _job['done'] += 1
                _job[{'sent': 'sent', 'unknown': 'unknown'}.get(outcome, 'failed')] += 1
                if error:
                    _job['errors'].append({'id': item['id'], 'number': item['number'], 'to': item['to'], 'error': error})
                if einfach and outcome == 'sent' and cfg.get('erechnung') == 'yes':   # ungewollt ohne E-Rechnung versendet: Grund → Anzahl
                    _job['einfach'][einfach] = _job['einfach'].get(einfach, 0) + 1
                if not logged:
                    _job['errors'].append({'id': item['id'], 'number': item['number'], 'to': item['to'],
                                           'error': f"Ergebnis ({'versendet' if outcome == 'sent' else outcome}) konnte nicht protokolliert werden – nicht erneut senden, erst prüfen."})
            if stop or not logged:
                final = 'aborted'
                break
            if n < len(items) - 1 and pause:
                _cancel.wait(pause)
    except Exception:
        final = 'aborted'
        with _job_lock:
            _job['errors'].append({'id': None, 'number': '–', 'to': '', 'error': 'Unerwarteter Fehler – Versand angehalten.'})
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
        with _job_lock:
            _job.update(state=final, current='', finished=datetime.now().isoformat(timespec='seconds'))
            summary = {k: _job[k] for k in ('total', 'sent', 'failed', 'unknown')}
        if store is not None:
            try:
                with store.db:
                    store.audit(actor, 'Zahlungserinnerungen per E-Mail versendet' if kind == 'reminder' else 'Rechnungen per E-Mail versendet',
                                {**summary, 'state': final})
            except Exception:
                pass
            store.db.close()


def stop_and_wait(seconds=40):
    """Beim Beenden: laufenden Versand abbrechen und auf die aktuelle E-Mail warten."""
    cancel()
    thread = _thread
    if thread is not None:
        thread.join(seconds)
    return not busy()
