"""Stammdaten (Einstellungen): erlaubte Felder, Standardwerte und Prüfung an einer Stelle.

Gespeichert wird in der Tabelle settings (Schlüssel/Wert). Fehlende Schlüssel liefern den
Standardwert aus DEFAULTS. Passwörter werden hier nie gespeichert (siehe geheimnis.py).
"""
import re

from datetime import date

from formate import money, iban_valid, iban_pretty, email_address, euro, german_date
import lastschrift

PLACEHOLDERS = {
    'vorname': 'Vorname des Empfängers',
    'name': 'vollständiger Name',
    'nummer': 'Rechnungsnummer',
    'betrag': 'Rechnungsbetrag',
    'faellig': 'Fälligkeitsdatum',
    'datum': 'Rechnungsdatum',
    'jahr': 'Beitragsjahr',
    'leistung': 'Leistungen (Positionen)',
    'mitgliedsnummer': 'Mitgliedsnummer',
    'iban': 'IBAN des Ausstellers',
    'absender': 'Absendername',
    'offen': 'offener Betrag',
    'neue_frist': 'neue Zahlungsfrist (Erinnerung)',
    'stufe': 'Nummer der Erinnerung (1, 2, …)',
    'einzug': 'Einzugsdatum (Lastschrift)',
    'konto': 'Konto des Mitglieds, gekürzt (Lastschrift)',
    'mandat': 'Mandatsreferenz (Lastschrift)',
    'glaeubiger': 'Gläubiger-ID (Lastschrift)',
}

DEFAULTS = {
    # Verein und Bank
    'issuer': '', 'issuer_address': '', 'iban': '', 'bic': '', 'bank_name': '',
    'contact_email': '', 'contact_phone': '', 'website': '',
    'tax_note': '', 'invoice_ready': 'no',
    # Buchhaltung: Konto für Eingangsrechnungen (fachlich zu bestätigen, Standard 1600)
    'liability_account': '1600', 'liability_name': 'Verbindlichkeiten (Eingangsrechnungen)',
    # SEPA-Lastschrift: Gläubiger-ID (Bundesbank), Präfix der Mandatsreferenz, Vorabankündigung in Tagen
    'creditor_id': '', 'sepa_prefix': 'JUHH', 'sepa_prenotice': '14',
    # Freigabe von Fremdrechnungen (Finanzordnung § 7 Abs. 4): über dieser Grenze Landesvorsitz + Landesschatzmeister
    'chair_name': '', 'treasurer_name': '', 'approval_limit': '50000',
    # Finanzrahmen der Kreisverbände (Finanzordnung §§ 2, 6): Buchungen ab diesem Tag zählen (ältere Jahre ohne Anteile)
    'fo_start': '2026-01-01',
    # Schalter (Verwaltung, ab 0.31): Beitragsanteil der Kreisverbände automatisch aus den gezahlten Beiträgen ab diesem Haushaltsjahr
    'kv_share_auto': 'no', 'kv_share_auto_from': '',
    # Karte (ab 0.32): Kartenhintergrund von OpenStreetMap laden (es gehen nur Kartenausschnitte ins Netz, keine Mitgliederdaten)
    'map_tiles': 'yes',
    # Beiträge (Satzung § 49): Staffel nach Alter, anteilig im Eintrittsjahr
    'age_basis': 'jan1', 'dues_min_age': '14',   # Altersstichtag 01.01. (Entscheidung Lennert Gust 08.11.2026)
    'dues_young_max': '18', 'dues_young': '1000',
    'dues_mid_max': '21', 'dues_mid': '2000',
    'dues_old': '3000', 'dues_prorate': 'yes',
    # Zahlungsziele in Tagen ab Rechnungsdatum
    'dues_terms': '14', 'normal_terms': '14',
    # Zahlungserinnerung: frühestens so viele Tage nach Fälligkeit, Abstand zwischen Erinnerungen, neue Frist
    'reminder_after': '14', 'reminder_gap': '21', 'reminder_terms': '14',
    # E-Mail-Versand (Strato: SSL auf Port 465 oder STARTTLS auf 587)
    'mail_from_name': 'Junge Union Hamburg', 'mail_from': '', 'smtp_host': 'smtp.strato.de',
    'smtp_port': '465', 'smtp_security': 'ssl', 'smtp_user': '', 'mail_reply_to': '', 'mail_bcc': '',
    'mail_pause': '3',
    # Vorlagen
    'tpl_dues_subject': 'Deine Beitragsrechnung {jahr} – Junge Union Hamburg',
    'tpl_dues_body': (
        'Hallo {vorname},\n\n'
        'vielen Dank, dass du Mitglied der Jungen Union Hamburg bist! Im Anhang findest du deine '
        'Beitragsrechnung {nummer} für das Jahr {jahr} über {betrag}.\n\n'
        'Bitte überweise den Betrag bis zum {faellig} und gib als Verwendungszweck {nummer} an. '
        'Am schnellsten geht es mit dem QR-Code (GiroCode) auf der Rechnung.\n\n'
        'Bei Fragen antworte einfach auf diese E-Mail.\n\n'
        'Viele Grüße\n{absender}'),
    'tpl_normal_subject': 'Rechnung {nummer} – Junge Union Hamburg',
    'tpl_normal_body': (
        'Hallo {vorname},\n\n'
        'im Anhang findest du die Rechnung {nummer} über {betrag} für: {leistung}.\n\n'
        'Bitte überweise den Betrag bis zum {faellig} und gib als Verwendungszweck {nummer} an. '
        'Am schnellsten geht es mit dem QR-Code (GiroCode) auf der Rechnung.\n\n'
        'Viele Grüße\n{absender}'),
    'tpl_debit_body': (
        'Hallo {vorname},\n\n'
        'vielen Dank, dass du Mitglied der Jungen Union Hamburg bist! Im Anhang findest du deine '
        'Beitragsrechnung {nummer} für das Jahr {jahr} über {betrag}.\n\n'
        'Du musst nichts tun: Wir ziehen den Betrag am {einzug} per SEPA-Lastschrift von deinem Konto {konto} ein '
        '(Mandatsreferenz {mandat}, Gläubiger-ID {glaeubiger}). Bitte sorge für ausreichende Deckung.\n\n'
        'Bei Fragen antworte einfach auf diese E-Mail.\n\n'
        'Viele Grüße\n{absender}'),
    'tpl_reminder_subject': 'Zahlungserinnerung zur Rechnung {nummer} – Junge Union Hamburg',
    'tpl_reminder_body': (
        'Hallo {vorname},\n\n'
        'sicher ist es im Alltag untergegangen: Für die Rechnung {nummer} vom {datum} über {betrag} ist bei uns '
        'bisher kein vollständiger Zahlungseingang verbucht. Offen sind noch {offen}.\n\n'
        'Bitte überweise den offenen Betrag bis zum {neue_frist} und gib als Verwendungszweck {nummer} an. '
        'Die Rechnung hängt zur Sicherheit noch einmal an.\n\n'
        'Falls du in den letzten Tagen schon bezahlt hast, betrachte diese Erinnerung bitte als gegenstandslos – vielen Dank! '
        'Bei Fragen antworte einfach auf diese E-Mail.\n\n'
        'Viele Grüße\n{absender}'),
    'pdf_dues_text': ('vielen Dank für deine Mitgliedschaft in der Jungen Union Hamburg. '
                      'Für das Jahr {jahr} stellen wir dir deinen Mitgliedsbeitrag in Rechnung:'),
    'pdf_normal_text': 'wir stellen dir folgende Leistungen in Rechnung:',
}

TEXT_KEYS = {'issuer', 'issuer_address', 'bank_name', 'liability_name', 'chair_name', 'treasurer_name', 'contact_phone', 'website', 'tax_note', 'mail_from_name',
             'tpl_dues_subject', 'tpl_dues_body', 'tpl_normal_subject', 'tpl_normal_body',
             'tpl_reminder_subject', 'tpl_reminder_body', 'tpl_debit_body', 'pdf_dues_text', 'pdf_normal_text'}
TEMPLATE_KEYS = {'tpl_dues_subject', 'tpl_dues_body', 'tpl_normal_subject', 'tpl_normal_body',
                 'tpl_reminder_subject', 'tpl_reminder_body', 'tpl_debit_body', 'pdf_dues_text', 'pdf_normal_text'}
LOCAL_HOSTS = {'127.0.0.1', 'localhost'}


def unknown_placeholders(text):
    return sorted(set(re.findall(r'\{(\w+)\}', text)) - set(PLACEHOLDERS))


def _days(value, label):
    s = str(value).strip()
    if not s.isdigit() or not 0 <= int(s) <= 365:
        raise ValueError(f'{label}: bitte eine Zahl von 0 bis 365 Tagen.')
    return str(int(s))


def _age(value, label):
    s = str(value).strip()
    if not s.isdigit() or not 0 < int(s) < 40:
        raise ValueError(f'{label}: bitte ein Alter in Jahren.')
    return str(int(s))


def _cents(value, label):
    cents = money(value)
    if cents < 0:
        raise ValueError(f'{label}: Betrag darf nicht negativ sein.')
    return str(cents)


def clean(data, current):
    """Prüft geänderte Felder; liefert {Schlüssel: Text}. current = bisherige Stammdaten (mit Standards)."""
    out = {}
    for key, value in data.items():
        if key not in DEFAULTS:
            continue
        v = str(value if value is not None else '').strip()
        if key in TEXT_KEYS:
            if key in TEMPLATE_KEYS:
                bad = unknown_placeholders(v)
                if bad:
                    raise ValueError('Unbekannte Platzhalter: ' + ', '.join('{' + b + '}' for b in bad))
                if key.endswith('_subject') and ('\n' in v or not v):
                    raise ValueError('Der Betreff muss einzeilig sein und darf nicht leer sein.')
            out[key] = v
        elif key == 'iban':
            if v and not iban_valid(v):
                raise ValueError('IBAN ist ungültig (Prüfziffer stimmt nicht).')
            out[key] = iban_pretty(v) if v else ''
        elif key == 'bic':
            if v and not re.fullmatch(r'[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?', v.upper().replace(' ', '')):
                raise ValueError('BIC ist ungültig (8 oder 11 Zeichen).')
            out[key] = v.upper().replace(' ', '')
        elif key in ('contact_email', 'mail_from', 'mail_reply_to', 'mail_bcc'):
            out[key] = email_address(v)
        elif key == 'creditor_id':
            v = re.sub(r'\s+', '', v).upper()
            if v and not lastschrift.creditor_id_valid(v):
                raise ValueError('Gläubiger-Identifikationsnummer ist ungültig (Prüfziffer oder Aufbau, z. B. DE98ZZZ09999999999).')
            out[key] = v
        elif key == 'sepa_prefix':
            if not re.fullmatch(r'[A-Za-z0-9]{1,10}', v):
                raise ValueError('Präfix der Mandatsreferenz: 1 bis 10 Buchstaben oder Ziffern.')
            out[key] = v
        elif key == 'sepa_prenotice':
            out[key] = _days(v, 'Vorabankündigung')
            if not 1 <= int(out[key]) <= 30:
                raise ValueError('Vorabankündigung: 1 bis 30 Tage (ohne Vereinbarung im Mandat 14 Tage).')
        elif key == 'liability_account':
            if not re.fullmatch(r'\d{3,6}', v):
                raise ValueError('Verbindlichkeitenkonto: bitte eine Kontonummer mit 3 bis 6 Ziffern.')
            out[key] = v
        elif key == 'smtp_user':
            if any(c in v for c in '\r\n'):
                raise ValueError('Ungültiger Benutzername.')
            out[key] = v
        elif key == 'smtp_host':
            if v and not re.fullmatch(r'[A-Za-z0-9.-]+', v):
                raise ValueError('Ungültiger Servername.')
            out[key] = v.lower()
        elif key == 'smtp_port':
            if not v.isdigit() or not 0 < int(v) < 65536:
                raise ValueError('Ungültiger Port.')
            out[key] = str(int(v))
        elif key == 'smtp_security':
            if v not in ('ssl', 'starttls', 'none'):
                raise ValueError('Verschlüsselung: SSL/TLS oder STARTTLS wählen.')
            out[key] = v
        elif key == 'mail_pause':
            if not v.isdigit() or int(v) > 60:
                raise ValueError('Pause zwischen E-Mails: 0 bis 60 Sekunden.')
            out[key] = str(int(v))
        elif key in ('dues_terms', 'normal_terms'):
            out[key] = _days(v, 'Zahlungsziel')
        elif key in ('reminder_after', 'reminder_gap', 'reminder_terms'):
            out[key] = _days(v, 'Zahlungserinnerung')
            if key == 'reminder_after' and int(out[key]) < 1:
                raise ValueError('Zahlungserinnerung: frühestens 1 Tag nach Fälligkeit.')
            if key != 'reminder_after' and int(out[key]) < 7:
                raise ValueError('Zahlungserinnerung: Abstand und neue Frist mindestens 7 Tage.')
        elif key in ('dues_min_age', 'dues_young_max', 'dues_mid_max'):
            out[key] = _age(v, 'Altersgrenze')
        elif key in ('dues_young', 'dues_mid', 'dues_old'):
            out[key] = _cents(v, 'Beitrag')
        elif key == 'approval_limit':
            out[key] = _cents(v, 'Freigabegrenze')
            if not 0 < int(out[key]) <= 50000:  # Finanzordnung § 7 Abs. 4: höher ist nicht zulässig, niedriger strenger
                raise ValueError('Freigabegrenze: über 0 € und höchstens 500,00 € (Finanzordnung § 7 Abs. 4).')
        elif key == 'fo_start':  # Haushaltsjahr (z. B. 2026) → 1. Januar dieses Jahres
            year = v[:4] if v[:4].isdigit() else ''
            if not year or not 2020 <= int(year) <= date.today().year + 1:
                raise ValueError('Beginn des KV-Finanzrahmens: ein Haushaltsjahr zwischen 2020 und dem nächsten Jahr.')
            out[key] = f'{year}-01-01'
        elif key == 'age_basis':
            if v not in ('', 'jan1', 'dec31'):
                raise ValueError('Ungültiger Altersstichtag.')
            out[key] = v
        elif key in ('invoice_ready', 'dues_prorate', 'kv_share_auto', 'map_tiles'):
            out[key] = 'yes' if v in ('yes', 'true', 'on', '1') else 'no'
        elif key == 'kv_share_auto_from':
            if v and not re.fullmatch(r'20\d\d', v):
                raise ValueError('Beitragsanteil automatisch: bitte das Haushaltsjahr wählen, ab dem der Schalter gilt.')
            out[key] = v
    merged = {**current, **out}
    if not int(merged['dues_min_age']) <= int(merged['dues_young_max']) < int(merged['dues_mid_max']):
        raise ValueError('Altersgrenzen der Beitragsstaffel: Mindestalter ≤ erste Grenze < zweite Grenze.')
    if merged['kv_share_auto'] == 'yes' and not merged['kv_share_auto_from']:
        raise ValueError('Beitragsanteil automatisch: bitte das Haushaltsjahr wählen, ab dem der Schalter gilt.')
    if merged['smtp_security'] == 'none' and merged['smtp_host'] not in LOCAL_HOSTS:
        raise ValueError('Unverschlüsselter Versand ist nur für Tests auf diesem Rechner erlaubt.')
    return out


def dues_scale(cfg):
    """[(bis Alter, Cent), …, (None, Cent)] – letzte Stufe ohne Obergrenze."""
    return [(int(cfg['dues_young_max']), int(cfg['dues_young'])),
            (int(cfg['dues_mid_max']), int(cfg['dues_mid'])),
            (None, int(cfg['dues_old']))]


def first_name(inv):
    """Anrede: bei Mitgliedern der Vorname, sonst der vollständige Empfängername."""
    name = (inv.get('recipient') or '').strip()
    if inv.get('snap', {}).get('member_number') and name:
        return name.split()[0]
    return name


def invoice_values(inv, sender=None):
    """Werte für die Platzhalter aus einer Rechnung (siehe rechnung_pdf.invoice_data)."""
    snap = inv.get('snap', {})
    return {
        'vorname': first_name(inv),
        'name': inv.get('recipient', ''),
        'nummer': inv.get('number', ''),
        'betrag': euro(inv.get('total', 0)),
        'faellig': german_date(lastschrift.payable_by(inv)),  # entfallene Lastschrift: neue Frist statt vergangenem Einzugsdatum
        'datum': german_date(inv.get('issued')),
        'jahr': str(inv.get('year') or (inv.get('issued') or '')[:4]),
        'leistung': ', '.join(l['description'] for l in inv.get('lines', [])) or 'Mitgliedsbeitrag',
        'mitgliedsnummer': snap.get('member_number') or '',
        'iban': snap.get('iban', ''),
        'absender': sender or snap.get('mail_from_name') or snap.get('issuer', ''),
        'offen': euro(inv.get('total', 0) - (inv.get('paid') or 0)),
        'neue_frist': german_date((inv.get('reminder') or {}).get('deadline') or inv.get('due')),
        'stufe': str((inv.get('reminder') or {}).get('level', '')),
        'einzug': german_date((snap.get('debit') or {}).get('date')),
        'konto': (snap.get('debit') or {}).get('iban', ''),
        'mandat': (snap.get('debit') or {}).get('mandate', ''),
        'glaeubiger': (snap.get('debit') or {}).get('creditor_id', ''),
    }


def fill(text, values):
    """Ersetzt {platzhalter}; Unbekanntes bleibt sichtbar stehen."""
    return re.sub(r'\{(\w+)\}', lambda m: str(values.get(m.group(1), m.group(0))), text or '')
