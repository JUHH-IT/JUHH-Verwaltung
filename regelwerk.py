"""Regelwerk (ab 0.43, Wunsch Lennert Gust 21.11.2026): die Regeln aus Satzung und Finanzordnung, die das Programm anwendet.

Nur lesend, für alle Angemeldeten. Je Regel: Quelle (Paragraf oder Entscheidung), kurze Fassung, Stand der Umsetzung und der Wert, der heute
im Programm gilt – gelesen aus den Stammdaten, damit Seite und Rechnung nie auseinanderlaufen. Die Texte folgen der Tabelle „Quellen und
Entscheidungen“ in REGELN-UND-OFFENE-PUNKTE.md; ändert sich dort ein Stand, hier mitziehen. Die Termine (Stichtage, Rechnungsausgang)
nutzt auch der Überblick der Mitgliederverwaltung.
"""
from datetime import date, timedelta

import wahlrecht
from formate import euro

QUELLEN = ('Satzung vom 11.04.2026', 'Finanzordnung vom 13.03.2024')
BEREICHE = (('mitglieder', 'Mitglieder und Beiträge'), ('wahlen', 'Wahlen'), ('finanzen', 'Finanzen'), ('verband', 'Verband'))
STAND = ('umgesetzt', 'teilweise', 'noch nicht')
ALTER = {'jan1': 'Alter am 01.01. des Beitragsjahres', 'dec31': 'Alter am 31.12. des Beitragsjahres'}


def zweiter_freitag_dezember(year):
    """Rechnungsausgang (Satzung § 49 Abs. 4): bis zum zweiten Freitag im Dezember."""
    first = date(year, 12, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 7)


def _eur(cents):
    text = euro(int(cents))
    return text[:-5] + ' €' if text.endswith(',00 €') else text


def termine(cfg, today=None):
    """Nächste Termine der Mitgliederverwaltung: Beitrittsstichtag, Rechnungsausgang, Beitragsstichtag, Liste an den LMA (§ 17)."""
    today = today or date.today()
    s = wahlrecht.stichtage(cfg, today)
    beitrag = date.fromisoformat(s['beitrag'])
    rechnung = zweiter_freitag_dezember(today.year)
    if rechnung < today:
        rechnung = zweiter_freitag_dezember(today.year + 1)
    out = [('beitritt', 'Beitrittsstichtag', s['beitritt'], 'Satzung § 37 Abs. 1 Nr. 1'),
           ('rechnung', 'Beitragsrechnungen versenden', rechnung.isoformat(), 'Satzung § 49 Abs. 4'),
           ('beitrag', 'Beitragsstichtag', s['beitrag'], 'Satzung § 15 Abs. 3'),
           ('lma', 'Liste an den Landesmitgliedschaftsausschuss', (beitrag + wahlrecht.FRIST).isoformat(), 'Satzung § 17 Abs. 1 Nr. 2')]
    return [{'key': k, 'label': label, 'tag': tag, 'quelle': quelle, 'vorbei': tag < today.isoformat()}
            for k, label, tag, quelle in sorted(out, key=lambda x: x[2])]


def _staffel(cfg, t):
    low, young, mid = int(cfg['dues_min_age']), int(cfg['dues_young_max']), int(cfg['dues_mid_max'])
    return (f"{low}–{young} Jahre {_eur(cfg['dues_young'])} · {young + 1}–{mid} Jahre {_eur(cfg['dues_mid'])} · "
            f"ab {mid + 1} Jahren {_eur(cfg['dues_old'])}")


def _wahl(cfg, t):
    return (f"Beitrittsstichtag {_de(t['beitritt'])} · Beitragsstichtag {_de(t['beitrag'])} · ausgewertet ab {cfg['wahlrecht_ab']}"
            + (' (Stichtage eingestellt)' if cfg.get('wahl_beitragsstichtag') or cfg.get('wahl_beitrittsstichtag') else ''))


def _de(iso):
    return f'{iso[8:10]}.{iso[5:7]}.{iso[:4]}'


# (Kennung, Bereich, Quelle, Titel, kurze Fassung, Stand, Wert im Programm: Text oder Funktion(cfg, Termine), aus den Stammdaten?)
REGELN = (
    ('beitrag-alter', 'mitglieder', 'Satzung § 49 Abs. 1', 'Beitrag nach Alter', 'Der Jahresbeitrag hat drei Altersstufen.',
     'umgesetzt', _staffel, True),
    ('altersstichtag', 'mitglieder', 'Entscheidung 08.11.2026', 'Altersstichtag',
     'Die Satzung nennt keinen Stichtag. Es zählt das Alter am 1. Januar des Beitragsjahres, im Eintrittsjahr frühestens am Tag des Beitritts.',
     'umgesetzt', lambda cfg, t: ALTER.get(cfg['age_basis'], 'noch nicht festgelegt'), True),
    ('eintritt', 'mitglieder', 'Satzung § 49 Abs. 2', 'Eintritt im laufenden Jahr',
     'Wer später im Jahr eintritt, zahlt anteilig nach dem Quartal des Eintritts; fällig mit dem Beitritt.',
     'umgesetzt', lambda cfg, t: ('Eintritt im 1. bis 4. Quartal: 75 % · 50 % · 25 % · 0 %' if cfg['dues_prorate'] == 'yes'
                                  else 'ausgeschaltet – voller Beitrag im Eintrittsjahr'), True),
    ('faelligkeit', 'mitglieder', 'Satzung § 49 Abs. 4', 'Fälligkeit und Rechnungsausgang',
     'Fällig am 1. Januar. Die Beitragsrechnungen gehen bis zum zweiten Freitag im Dezember hinaus.',
     'teilweise', lambda cfg, t: (f"Fällig 01.01., Zahlungsziel {cfg['dues_terms']} Tage. Rechnungsausgang bis {_de(t['rechnung'])} "
                                  'steht nur als Termin im Überblick, nicht als Frist.'), True),
    ('sepa', 'mitglieder', 'Satzung § 49 Abs. 5', 'SEPA-Mandat', 'Ein gültiges Mandat gilt als gezahlt.',
     'teilweise', lambda cfg, t: (f"Vorabankündigung {cfg['sepa_prenotice']} Tage; Mandat verfällt nach 36 Monaten ohne Einzug. "
                                  'Für die Wahlberechtigung zählt nur der Zahlungseingang (Entscheidung 19.11.2026).'), True),
    ('altersgrenze', 'mitglieder', 'Satzung § 2 Abs. 1 Nr. 4', 'Altersgrenze',
     'Die Mitgliedschaft endet mit Vollendung des 35. Lebensjahres; Ausnahme für Amtsträger nach Abs. 2.',
     'teilweise', 'Liste „Werden 35“ und Auswertung. Kein automatischer Austritt, die Ausnahme kennt das Programm nicht.', False),
    ('notiz-austritt', 'mitglieder', 'DSGVO Art. 5 Abs. 1 lit. e, Art. 17', 'Notizen nach dem Austritt',
     'Notizen der Mitgliederverwaltung braucht es nur während der Mitgliedschaft; eine Aufbewahrungspflicht gibt es für sie nicht.',
     'teilweise', 'In der Datenbank gelöscht am Austrittstag (beim Eintragen, Bearbeiten, Import und stündlich); für Ausgetretene keine '
     'neuen Notizen. Sicherungen enthalten die Notiz weiter: Tagessicherungen 30 Tage, Sicherungen „Vor Änderungen“ ohne Frist.', False),
    ('wahlrecht', 'wahlen', 'Satzung § 37 Abs. 1 Nr. 1', 'Wahlberechtigung',
     'Mitglied am Beitrittsstichtag und die bis zum Beitragsstichtag fälligen Beiträge dieses und der zwei vorigen Jahre bezahlt.',
     'umgesetzt', _wahl, True),
    ('lma-liste', 'wahlen', 'Satzung § 17 Abs. 1 Nr. 3', 'Liste für den Landesmitgliedschaftsausschuss',
     'Binnen 7 Tagen nach dem Beitragsstichtag: wahlberechtigte und nicht wahlberechtigte Mitglieder je Ortsverband.',
     'umgesetzt', lambda cfg, t: f"PDF und CSV unter Wahlberechtigung · Frist {_de(t['lma'])}", False),
    ('kv-anteil', 'finanzen', 'Satzung § 50 · FO § 6', 'Anteil der Kreisverbände',
     'Ein Viertel der gezahlten Beiträge steht dem Kreisverband zu.', 'umgesetzt',
     lambda cfg, t: '25 % der tatsächlich gezahlten Beiträge' + (f" (automatisch ab {cfg['kv_share_auto_from']})"
                                                                  if cfg['kv_share_auto'] == 'yes' else ''), True),
    ('freigabe', 'finanzen', 'FO § 7 Abs. 4', 'Rechnungen über der Freigabegrenze',
     'Landesvorsitz und Landesschatzmeister geben jede Rechnung über der Grenze frei.', 'umgesetzt',
     lambda cfg, t: f"Grenze {euro(int(cfg['approval_limit']))} · ohne beide Freigaben keine Zahlung", True),
    ('barspenden', 'finanzen', 'FO § 2 Abs. 5', 'Barspenden', 'Zwei Vertreter, Quittung, Einzahlung binnen 30 Tagen.',
     'umgesetzt', 'Register mit Frist in der Übersicht', False),
    ('erstattung', 'finanzen', 'FO § 8', 'Erstattung von Auslagen', 'Mit Nachweis; Antrag binnen drei Monaten nach der Ausgabe.',
     'umgesetzt', 'Frist 3 Monate · fällig 14 Tage nach Antragseingang', False),
    ('haushalt', 'finanzen', 'Satzung § 50 · FO §§ 3, 5', 'Haushalt und Nachtrag', 'Haushalt, Nachtragshaushalt und Nothaushalt.',
     'teilweise', 'Hinweis bei über 10 % je Ausgabetitel. Beschlüsse sind nicht abgebildet.', False),
    ('reisekosten', 'finanzen', 'FO §§ 9–10', 'Reisekosten und Geschenke', 'Sätze und Grenzen der Finanzordnung.',
     'noch nicht', 'Nicht abgebildet', False),
    ('kassenpruefung', 'verband', 'Satzung § 19 Abs. 2', 'Kassenprüfung', 'Die Kassenprüfer prüfen jedes Haushaltsjahr.',
     'umgesetzt', 'Prüferpaket als ZIP mit Prüfhinweisen', False),
)
OFFEN = ('Welcher Beitrag gilt im Austrittsjahr: voll, anteilig oder keiner? Bis dahin verlangt das Programm eine Festlegung mit Grund.',
         'Amtsträger über 35 (Satzung § 2 Abs. 2): Die Ausnahme kennt das Programm noch nicht.',
         'Zählt eine Zahlung im Dezember schon für die Beitragsrechnung des Folgejahres?')


def regeln(cfg, today=None):
    """Regelwerk für die Seite: Quellen, Bereiche, Regeln mit heutigem Wert und Stand, Termine, offene Fragen."""
    t = termine(cfg, today)
    tage = {x['key']: x['tag'] for x in t}
    rows = [{'id': key, 'bereich': bereich, 'quelle': quelle, 'titel': titel, 'text': text, 'stand': stand,
             'wert': wert(cfg, tage) if callable(wert) else wert, 'einstellung': einstellung}
            for key, bereich, quelle, titel, text, stand, wert, einstellung in REGELN]
    return {'quellen': list(QUELLEN), 'bereiche': [{'id': k, 'name': n} for k, n in BEREICHE], 'stand': list(STAND),
            'regeln': rows, 'termine': t, 'offen': list(OFFEN)}
