"""Kontenrahmen der CDU Deutschlands (gültig ab 1.1.2016) mit den zusätzlichen Konten der JU Hamburg; Kontenblätter.

Wunsch Lennert Gust 29.10.2026: „wirklich alle Konten in dem Kontenrahmen“ und „jedes Konto anschauen“.
Konten werden nur ergänzt (INSERT OR IGNORE): vorhandene Bezeichnungen bleiben – Konten mit Haushaltstitel tragen die
Namen aus der Excel-Vorlage (Entscheidung vom 29.10.2026), alle übrigen die Bezeichnung des Kontenrahmens. Bereiche
eines Kontenrahmens (z. B. 1100–1149, je Gliederung ein Konto) stehen mit ihrem ersten Konto in der Liste.

Kontoarten: asset (Aktiva), liability (Passiva), equity (Reinvermögen), income (Einnahmen), expense (Ausgaben),
closing (Eröffnungs- und Abschlusskonten). Einnahmen und Ausgaben zählen im Haushalt; Bestandskonten nicht.

Kontenblatt: Bestandskonten nach Buchungsdatum mit Anfangsbestand (wie der Kontoauszug), Einnahmen- und Ausgabenkonten
nach Haushaltsjahr (wie der Haushalt). Saldo immer Soll − Haben (S = Sollsaldo, H = Habensaldo).
"""
KINDS = {'asset': 'Aktivkonto', 'liability': 'Passivkonto', 'equity': 'Reinvermögen', 'income': 'Einnahmen', 'expense': 'Ausgaben',
         'closing': 'Abschlusskonto'}
BALANCE_KINDS = ('asset', 'liability', 'equity', 'closing')

CLASSES = {'0': 'Klasse 0 · Anlagevermögen', '1': 'Klasse 1 · Umlaufvermögen', '2': 'Klasse 2 · Schuldposten und Reinvermögen',
           '4': 'Klasse 4 · Personal- und Sachausgaben, Zuschüsse an Gliederungen', '6': 'Klasse 6 · Ausgaben für die Bundestagswahl',
           '7': 'Klasse 7 · Ausgaben für Europa-, Landtags-, Kommunal- und sonstige Wahlen', '8': 'Klasse 8 · Einnahmen',
           '9': 'Klasse 9 · Eröffnungs- und Abschlusskonten'}

GROUPS = [('0100', '0199', 'Haus- und Grundvermögen'), ('0200', '0299', 'Geschäftsstellenausstattung'), ('0300', '0399', 'Finanzanlagen'),
          ('1100', '1199', 'Forderungen an Gliederungen'), ('1200', '1299', 'Forderungen aus der staatlichen Teilfinanzierung'),
          ('1300', '1399', 'Klärung und Kandidatenspenden (JU Hamburg)'), ('1400', '1499', 'Geldbestände'),
          ('1500', '1599', 'Sonstige Vermögensgegenstände'), ('1600', '1699', 'Eingangsrechnungen (JU Hamburg)'),
          ('2000', '2099', 'Rückstellungen'), ('2100', '2199', 'Verbindlichkeiten gegenüber Gliederungen und Landesparlament'),
          ('2200', '2249', 'Verbindlichkeiten gegenüber Kreditinstituten'), ('2250', '2299', 'Verbindlichkeiten gegenüber sonstigen Darlehensgebern'),
          ('2300', '2399', 'Sonstige Verbindlichkeiten'), ('2400', '2499', 'Zweckgebundene öffentliche Zuschüsse an politische Jugendorganisationen'),
          ('2600', '2699', 'Reinvermögen'), ('4100', '4199', 'Personalausgaben'), ('4200', '4299', 'Ausgaben des laufenden Geschäftsbetriebes'),
          ('4300', '4499', 'Ausgaben für allgemeine politische Arbeit'), ('4600', '4699', 'Ausgaben für die Vermögensverwaltung'),
          ('4700', '4799', 'Sonstige Ausgaben'), ('4800', '4899', 'Zuschüsse an Gliederungen'), ('6000', '6999', 'Bundestagswahl'),
          ('7000', '7099', 'Europawahl'), ('7100', '7199', 'Landtagswahl'), ('7200', '7299', 'Kommunalwahl und sonstige Wahlen'),
          ('8100', '8199', 'Mitglieds- und Mandatsträgerbeiträge'), ('8200', '8299', 'Spenden von natürlichen Personen'),
          ('8300', '8399', 'Spenden von juristischen Personen, Unternehmenstätigkeit'),
          ('8400', '8499', 'Einnahmen aus Beteiligungen und sonstigem Vermögen'),
          ('8500', '8599', 'Einnahmen aus Veranstaltungen, Druckschriften und sonstiger Tätigkeit'), ('8600', '8699', 'Staatliche Mittel'),
          ('8700', '8799', 'Sonstige Einnahmen'), ('8800', '8899', 'Zuschüsse von Gliederungen'), ('9000', '9999', 'Eröffnungs- und Abschlusskonten')]

ZMD = 'Am Jahresende über die ZMD an den UBG zu melden.'

# (Konto, Bezeichnung laut Kontenrahmen, Art, Hinweis)
ACCOUNTS = [
    ('0100', 'Unbebaute Grundstücke', 'asset', ''), ('0120', 'Bebaute Grundstücke', 'asset', 'Ab 2003 keine planmäßigen Abschreibungen.'),
    ('0130', 'Im Bau befindliche Gebäude', 'asset', ''),
    ('0200', 'Kraftfahrzeuge', 'asset', 'Geschäftsstellenausstattung über 5.000 € inkl. MwSt.; darunter Konto 4248.'),
    ('0210', 'Mobiliar', 'asset', ''), ('0220', 'Büromaschinen, Computer', 'asset', ''), ('0230', 'Geräte für Technik, Ton und Bild', 'asset', ''),
    ('0300', 'Beteiligungen an Unternehmen', 'asset', ''), ('0310', 'Langfristige Forderungen (Darlehen)', 'asset', ''),
    ('0320', 'Wertpapiere / Fonds', 'asset', ''),
    ('1100', 'Beitragsforderungen an Gliederungen', 'asset', 'Kontenrahmen 1100–1149, je Gliederung (inkl. Vereinigungen) ein Konto.'),
    ('1150', 'Sonstige Forderungen an Gliederungen', 'asset', 'Kontenrahmen 1150–1199, je Gliederung ein Konto.'),
    ('1200', 'Forderung auf staatliche Mittel', 'asset', ''), ('1400', 'Kasse', 'asset', ''), ('1410', 'Portokasse', 'asset', ''),
    ('1420', 'Banken und Sparkassen', 'asset', 'Je Konto-Nr. ein Konto.'),
    ('1500', 'Forderungen aus Lieferungen und Leistungen', 'asset', ''), ('1510', 'Aktive Posten der Rechnungsabgrenzung', 'asset', ''),
    ('1540', 'Forderungen an befreundete Organisationen', 'asset', ''), ('1550', 'Gehalts- und Lohnvorschüsse', 'asset', ''),
    ('1560', 'Andere kurzfristige Forderungen an Beschäftigte', 'asset', ''), ('1570', 'Verrechnungsvorschüsse (Reisekosten u. a.)', 'asset', ''),
    ('1580', 'Voraus- und Abschlagszahlungen', 'asset', ''), ('1590', 'Sonstige Forderungen', 'asset', ''),
    ('2010', 'Pensionsverpflichtungen', 'liability', ''), ('2020', 'Sonstige Rückstellungen', 'liability', ''),
    ('2100', 'Beitragsverbindlichkeiten gegenüber Gliederungen', 'liability', 'Kontenrahmen 2100–2149, je Gliederung ein Konto.'),
    ('2150', 'Sonstige Verbindlichkeiten gegenüber Gliederungen', 'liability', 'Kontenrahmen 2150–2198, je Gliederung ein Konto.'),
    ('2199', 'Verbindlichkeit gegenüber dem Landesparlament', 'liability', 'Rückzahlungsverpflichtung aus der staatlichen Teilfinanzierung.'),
    ('2202', 'Verbindlichkeiten gegenüber Kreditinstituten', 'liability', 'Kontenrahmen 2202–2249, je Konto-Nr. ein Konto.'),
    ('2250', 'Darlehen', 'liability', 'Verbindlichkeiten gegenüber sonstigen Darlehensgebern.'),
    ('2260', 'Anzahlungen (z. B. als Wahlkampffinanzierung)', 'liability', ''),
    ('2300', 'Verbindlichkeiten aus Lieferungen und Leistungen', 'liability', 'Eingangsrechnungen im Programm über das eigene Verbindlichkeitenkonto.'),
    ('2310', 'Langfristige Verbindlichkeiten (Darlehen)', 'liability', ''), ('2320', 'Wertberichtigungen auf Forderungen', 'liability', ''),
    ('2325', 'Passive Posten der Rechnungsabgrenzung', 'liability', ''), ('2350', 'Lohn- und Kirchensteuer', 'liability', ''),
    ('2351', 'Umsatzsteuer', 'liability', ''), ('2355', 'Krankenkassen', 'liability', 'Je Krankenkasse ein Konto.'),
    ('2370', 'Andere Versicherungen', 'liability', ''), ('2375', 'Gehalts- und Lohnpfändungen', 'liability', ''),
    ('2379', 'Abzuführende vermögenswirksame Leistungen', 'liability', ''),
    ('2380', 'Durchlauf- und Übergangskonto (z. B. Kasse/Bank)', 'liability', ''), ('2390', 'Andere sonstige Verbindlichkeiten', 'liability', ''),
    ('2400', 'Zweckgebundene öffentliche Zuschüsse – Einnahmen', 'liability',
     'Kontenrahmen 2400–2449. Am Jahresende ergebnisneutral verrechnen (Saldo 0); Einnahmen nachrichtlich angeben.'),
    ('2450', 'Zweckgebundene öffentliche Zuschüsse – Ausgaben', 'liability',
     'Kontenrahmen 2450–2499. Mehrausgaben am Jahresende auf ein sachgerechtes Ausgabenkonto umbuchen.'),
    ('2600', 'Reinvermögen (positiv oder negativ)', 'equity', ''),
    ('4100', 'Bruttogehälter und -löhne', 'expense', 'Personalausgaben einschließlich Wahlen, Veranstaltungen usw.'),
    ('4110', 'Gesetzliche soziale Abgaben', 'expense', ''), ('4120', 'Freiwillige soziale Aufwendungen', 'expense', ''),
    ('4130', 'Zusätzliche Personalaufwendungen', 'expense', 'Fahrkosten, Trennungsentschädigung, Betriebsratskosten u. ä.'),
    ('4150', 'Aufwendungen für zusätzliche Altersversorgung', 'expense', ''),
    ('4200', 'Mieten für Geschäftsräume', 'expense', ''), ('4201', 'Grundbesitzabgaben und Gebäudeversicherungen', 'expense', ''),
    ('4202', 'Heizung, Strom, Wasser und sonstige Abgaben', 'expense', ''), ('4203', 'Instandsetzung und Instandhaltung', 'expense', ''),
    ('4204', 'Reinigung und Sonstiges', 'expense', ''), ('4205', 'Alarmanlagen / Notruf', 'expense', 'Mieten, Versicherungen, Wartung, Veränderungen.'),
    ('4210', 'Büromaterial, Vervielfältigung, Kopier- und Fotomaterial', 'expense', 'Inkl. Mieten / Leasing.'),
    ('4211', 'Wartung und Instandsetzung von Büromaschinen und Inventar', 'expense', ''),
    ('4220', 'Porto- und Versandkosten', 'expense', ''), ('4225', 'Kosten des Geldverkehrs', 'expense', ''),
    ('4230', 'Telefon', 'expense', 'Gebühren, Anlagemieten, Versicherungen, Wartung und Veränderungen.'),
    ('4231', 'Telekopierer / Fax', 'expense', ''), ('4232', 'EDV-Kosten', 'expense', 'Mieten, Versicherungen, Wartung und Veränderungen.'),
    ('4240', 'Zeitungen, Zeitschriften und Informationsdienste', 'expense', ''), ('4241', 'Bücher und Loseblattsammlungen', 'expense', ''),
    ('4242', 'Versicherungen', 'expense', 'Z. B. Haftpflicht, Unfall, Künstlersozialkasse.'), ('4243', 'Beiträge', 'expense', ''),
    ('4244', 'Rechtsberatungs-, Beratungs- und Prüfungskosten', 'expense', ''), ('4247', 'Abschreibungen auf Anlagevermögen', 'expense', ''),
    ('4248', 'Anschaffungen bis 5.000 € inkl. MwSt.', 'expense', ''), ('4250', 'Reisekosten', 'expense', ''),
    ('4260', 'Bewirtungen', 'expense', ''), ('4270', 'Autokosten', 'expense', 'Je eigenes Fahrzeug ein Konto.'),
    ('4290', 'Sonstige Kosten', 'expense', 'Repräsentationskosten, Geschenke, Stellen- und Todesanzeigen u. ä.'),
    ('4300', 'Publikationen', 'expense', 'Je Publikation ein Konto.'), ('4320', 'Umfragen', 'expense', ''),
    ('4330', 'Information, Dokumentation', 'expense', ''), ('4340', 'Kosten der Mitgliederkartei, Beitragseinzug EDV', 'expense', ''),
    ('4350', 'Aus- und Weiterbildung, Studienfahrten', 'expense', ''),
    ('4360', 'Innerparteiliche Gremien', 'expense', 'Parteiausschuss, Präsidium, Vorstand, Konferenzen, Parteigericht – bei Bedarf je ein Konto.'),
    ('4370', 'Arbeitskreise und Fachausschüsse', 'expense', ''), ('4400', 'Broschüren', 'expense', ''), ('4410', 'Rednerdienste', 'expense', ''),
    ('4420', 'Mitgliederwerbung', 'expense', ''), ('4430', 'Werbematerialien', 'expense', ''),
    ('4440', 'Sonstige Informations- und Werbekosten', 'expense', ''), ('4450', 'Parteitage und vergleichbare Veranstaltungen', 'expense', ''),
    ('4460', 'Kongresse, Tagungen', 'expense', ''), ('4480', 'Pressekonferenzen und Kontaktgespräche', 'expense', ''),
    ('4600', 'Hypothekenzinsen', 'expense', ''), ('4601', 'Abgang Anlagevermögen (brutto)', 'expense', ''),
    ('4605', 'Aufwendungen für fremdvermietete Objekte', 'expense', ''), ('4610', 'Bankzinsen / Darlehenszinsen', 'expense', ''),
    ('4630', 'Ausgaben im Rahmen einer Unternehmenstätigkeit', 'expense', ''),
    ('4700', 'Zuschüsse an befreundete Organisationen (z. B. RCDS)', 'expense', ''), ('4710', 'Internationale Beiträge', 'expense', ''),
    ('4720', 'Beteiligungsaufwand', 'expense', ''), ('4740', 'Steuern', 'expense', ''),
    ('4790', 'Außerplanmäßige Abschreibungen auf Finanzanlagen', 'expense', ''),
    ('4795', 'Außerplanmäßige Abschreibungen auf das Sachanlagevermögen', 'expense', ''),
    ('4800', 'Zuschüsse an Gliederungen', 'expense', 'Je Gliederung ein Konto.'), ('4820', 'Zuschüsse an Junge Union (JU)', 'expense', ''),
    ('4830', 'Zuschüsse an Frauen-Union', 'expense', ''), ('4840', 'Zuschüsse an Sozialausschüsse (CDA)', 'expense', ''),
    ('4850', 'Zuschüsse an Kommunalpolitische Vereinigung (KPV)', 'expense', ''),
    ('4860', 'Zuschüsse an Mittelstandsvereinigung (MIT)', 'expense', ''),
    ('4870', 'Zuschüsse an Ost- und Mitteldeutsche Vereinigung (OMV)', 'expense', ''), ('4880', 'Zuschüsse an Senioren-Union', 'expense', ''),
    ('4890', 'Zuschüsse an Evangelischen Arbeitskreis (EAK)', 'expense', ''),
    ('6100', 'Bundestagswahl: Ausgaben des laufenden Geschäftsbetriebes', 'expense', 'Direkt zurechenbare Ausgaben.'),
    ('6200', 'Bundestagswahl: Ausgaben für allgemeine politische Arbeit', 'expense', 'Kontenrahmen 6200–6500, direkt zurechenbar.'),
    ('6600', 'Bundestagswahl: Zinsen', 'expense', 'Direkt zurechenbare Zinsen.'),
    ('7010', 'Europawahl: Ausgaben des laufenden Geschäftsbetriebes', 'expense', ''),
    ('7020', 'Europawahl: Ausgaben für allgemeine politische Arbeit', 'expense', 'Kontenrahmen 7020–7050.'),
    ('7060', 'Europawahl: Zinsen', 'expense', ''),
    ('7110', 'Landtagswahl: Ausgaben des laufenden Geschäftsbetriebes', 'expense', ''),
    ('7120', 'Landtagswahl: Ausgaben für allgemeine politische Arbeit', 'expense', 'Kontenrahmen 7120–7150.'),
    ('7160', 'Landtagswahl: Zinsen', 'expense', ''),
    ('7210', 'Kommunal- und sonstige Wahlen: Ausgaben des laufenden Geschäftsbetriebes', 'expense', ''),
    ('7220', 'Kommunal- und sonstige Wahlen: Ausgaben für allgemeine politische Arbeit', 'expense', 'Kontenrahmen 7220–7250.'),
    ('7260', 'Kommunal- und sonstige Wahlen: Zinsen', 'expense', ''),
    ('8100', 'Mitgliedsbeiträge (Zahlungen der Mitglieder)', 'income', ZMD),
    ('8110', 'Mitgliedsbeiträge: Beitragsanteile von Gliederungen', 'income', 'Je Gliederung ein Konto.'),
    ('8120', 'Mitgliedsbeiträge: Beitragsanteile an Gliederungen weitergeleitet', 'income', 'Soll-Buchungen.'),
    ('8150', 'Mandatsträgerbeiträge und ähnliche regelmäßige Beiträge', 'income', ZMD),
    ('8160', 'Mandatsträgerbeiträge: Beitragsanteile von Gliederungen', 'income', 'Je Gliederung ein Konto.'),
    ('8170', 'Mandatsträgerbeiträge: Beitragsanteile an Gliederungen weitergeleitet', 'income', 'Soll-Buchungen.'),
    ('8200', 'Spenden von natürlichen Personen bis 10.000 €', 'income', ZMD),
    ('8210', 'Spenden von natürlichen Personen über 10.000 €', 'income', ZMD),
    ('8300', 'Spenden von juristischen Personen bis 10.000 €', 'income', ZMD),
    ('8310', 'Spenden von juristischen Personen über 10.000 €', 'income', ZMD),
    ('8350', 'Einnahmen aus Unternehmenstätigkeit', 'income', ''), ('8400', 'Beteiligungserträge u. ä.', 'income', ''),
    ('8401', 'Veräußerungserlös von Beteiligungen (brutto)', 'income', ''),
    ('8410', 'Zinserträge', 'income', 'Nicht mit Ausgaben saldieren.'), ('8420', 'Mieterträge', 'income', ''),
    ('8430', 'Erlöse aus Anlageverkäufen (brutto)', 'income', ''),
    ('8500', 'Einnahmen aus Veranstaltungen', 'income', 'Nicht mit Ausgaben saldieren.'),
    ('8510', 'Einnahmen aus Vertrieb von Druckschriften und Veröffentlichungen', 'income', ''),
    ('8520', 'Einnahmen aus Untervermietung', 'income', ''), ('8590', 'Sonstige Einnahmen aus Veranstaltungen und Tätigkeit', 'income', ''),
    ('8600', 'Staatszuschuss vom Bund', 'income', ''), ('8610', 'Staatszuschuss vom Land', 'income', ''),
    ('8710', 'Schadensersatzleistungen', 'income', ''), ('8720', 'Erbschaften', 'income', ''), ('8730', 'Skonti, Boni', 'income', ''),
    ('8790', 'Sonstige Einnahmen', 'income', ''), ('8800', 'Zuschüsse von Gliederungen', 'income', 'Je Gliederung ein Konto.'),
    ('9100', 'Eröffnungsbilanz', 'closing', ''), ('9200', 'Schlussbilanz', 'closing', ''),
    ('9900', 'Einnahmen- und Ausgabenrechnung', 'closing', ''),
]
KR = {code: {'name': name, 'kind': kind, 'note': note} for code, name, kind, note in ACCOUNTS}

# Konten, die das Programm selbst führt: nicht direkt aus dem Kontoauszug bebuchbar (dafür gibt es eigene Wege).
SYSTEM = {'1420': 'Bankkonto selbst', '1370': 'Parkkonto – über „Parken“', '1500': 'Forderungen – über die Zuordnung zur Rechnung',
          '1600': 'Eingangsrechnungen – über die Zuordnung zur Eingangsrechnung'}


def init(db):
    """Fehlende Konten des Kontenrahmens anlegen; vorhandene Konten (Namen, Art) bleiben unverändert.
    Läuft bei jedem Öffnen der Datenbank (je Anfrage): erst lesen, nur bei fehlenden Konten schreiben."""
    from core import ensure_rows
    ensure_rows(db, 'accounts', [(c, a['name'], a['kind']) for c, a in KR.items()])


def group_of(code):
    if code == 'KV':
        return '4', 'Kreisverbände (Sammelkonto, Bereich je Buchung)'
    for low, high, label in GROUPS:
        if len(code) == 4 and low <= code <= high:
            return code[0], label
    return (code[:1] if code[:1] in CLASSES else '9'), 'Weitere Konten'


def liability_account(store):
    return store.config().get('liability_account') or '1600'


def system_reason(code, liability='1600'):
    """Warum ein Konto nicht direkt aus dem Kontoauszug gebucht wird – None, wenn es bebuchbar ist."""
    if code == liability and code not in SYSTEM:
        return 'Eingangsrechnungen – über die Zuordnung zur Eingangsrechnung'
    return SYSTEM.get(code)


def bank_bookable(code, kind, liability='1600'):
    """Bestandskonto direkt aus dem Kontoauszug buchen (z. B. Kasse, Durchlaufkonto, Darlehen) – ohne Haushaltstitel."""
    return kind in ('asset', 'liability') and not system_reason(code, liability)


def catalog(store):
    """Konten für Auswahllisten: Gruppe, Haushalt (mit Titel) ja/nein, direkt aus dem Kontoauszug bebuchbar, Hinweis."""
    import haushalt
    titled = {t['code'] for t in haushalt.TITLES.values() if t['code']} - set(haushalt.KV_ROWS)
    liability = liability_account(store)
    out = []
    for a in store.rows('SELECT code,name,kind FROM accounts ORDER BY code'):
        cls, group = group_of(a['code'])
        out.append({**a, 'class': cls, 'group': group, 'household': a['code'] in titled or a['code'] == 'KV',
                    'bank': bank_bookable(a['code'], a['kind'], liability), 'note': KR.get(a['code'], {}).get('note', '')})
    return out


def _fiscal(kind):
    return kind in ('income', 'expense')


def _where(kind):
    """Zeitraum eines Kontenblatts: Ergebniskonten nach Haushaltsjahr, Bestandskonten nach Buchungsdatum."""
    return ('COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER))=?', 'j.day<?') if _fiscal(kind) else ('substr(j.day,1,4)=?', 'j.day<?')


def accounts(store, year):
    """Alle Konten mit Kontenrahmen-Gruppe, Haushaltstitel und Bewegungen im Jahr (eine Abfrage für alle Konten)."""
    import haushalt
    year = int(year)
    sums = {}
    for r in store.rows("SELECT e.account,a.kind,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) fy,substr(j.day,1,4) dy,"
                        "SUM(e.debit) debit,SUM(e.credit) credit,COUNT(*) n,MIN(j.day) first FROM entries e JOIN journals j ON j.id=e.journal_id "
                        "JOIN accounts a ON a.code=e.account GROUP BY e.account,fy,dy"):
        s = sums.setdefault(r['account'], {'debit': 0, 'credit': 0, 'count': 0, 'opening': 0})
        in_year = (r['fy'] == year) if _fiscal(r['kind']) else (int(r['dy']) == year)
        if in_year:
            s['debit'] += r['debit']
            s['credit'] += r['credit']
            s['count'] += r['n']
        elif not _fiscal(r['kind']) and int(r['dy']) < year:
            s['opening'] += r['debit'] - r['credit']
    out, liability = [], liability_account(store)
    for a in store.rows('SELECT code,name,kind FROM accounts'):
        cls, group = group_of(a['code'])
        s = sums.get(a['code'], {'debit': 0, 'credit': 0, 'count': 0, 'opening': 0})
        titles = [t for t in haushalt.TITLES.values() if t['code'] == a['code'] and t['code'] not in haushalt.KV_ROWS]
        kr = KR.get(a['code'], {})
        out.append({**a, 'class': cls, 'class_label': CLASSES.get(cls, ''), 'group': group, 'kind_label': KINDS.get(a['kind'], a['kind']),
                    'kr_name': kr.get('name', ''), 'note': kr.get('note', ''), 'ju_only': a['code'] not in KR,   # nur bei der JU Hamburg
                    'titles': [t['id'] for t in titles], 'household': bool(titles) or a['code'] == 'KV',
                    'debit': s['debit'], 'credit': s['credit'], 'count': s['count'], 'opening': s['opening'],
                    'balance': s['opening'] + s['debit'] - s['credit'], 'system': system_reason(a['code'], liability)})
    return sorted(out, key=lambda a: (a['class'], a['code'] if a['code'] != 'KV' else '4999'))


def ledger(store, code, year, kv=None):
    """Kontenblatt eines Kontos für ein Jahr; kv filtert auf einen Bereich."""
    year = int(year)
    if not 2000 <= year <= 2100:
        raise ValueError('Ungültiges Jahr.')
    acc = store.db.execute('SELECT code,name,kind FROM accounts WHERE code=?', (code,)).fetchone()
    if not acc:
        raise ValueError('Konto nicht gefunden.')
    if kv and not store.db.execute('SELECT 1 FROM kv WHERE code=?', (kv,)).fetchone():
        raise ValueError('Unbekannter Bereich.')
    period, before = _where(acc['kind'])
    flt, args = (' AND e.kv=?', (kv,)) if kv else ('', ())
    opening = 0
    if not _fiscal(acc['kind']):
        opening = store.db.execute(f'SELECT COALESCE(SUM(e.debit-e.credit),0) FROM entries e JOIN journals j ON j.id=e.journal_id '
                                   f'WHERE e.account=? AND {before}{flt}', (code, f'{year}-01-01', *args)).fetchone()[0]
    rows = store.rows(f'SELECT j.id,j.day,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) year,j.source,j.description,j.reversal_of,'
                      f'e.id entry,e.kv,e.title,e.debit,e.credit FROM entries e JOIN journals j ON j.id=e.journal_id '
                      f'WHERE e.account=? AND {period}{flt} ORDER BY j.day,j.id,e.id', (code, str(year) if not _fiscal(acc['kind']) else year, *args))
    reversed_ids = {r[0] for r in store.db.execute('SELECT reversal_of FROM journals WHERE reversal_of IS NOT NULL')}
    running = opening
    for r in rows:
        running += r['debit'] - r['credit']
        r['balance'] = running
        r['reversed'] = r['id'] in reversed_ids
        r['kind'] = source_label(r['source'])
    debit, credit = sum(r['debit'] for r in rows), sum(r['credit'] for r in rows)
    cls, group = group_of(code)
    return {'code': acc['code'], 'name': acc['name'], 'kind': acc['kind'], 'kind_label': KINDS.get(acc['kind'], acc['kind']),
            'class_label': CLASSES.get(cls, ''), 'group': group, 'note': KR.get(code, {}).get('note', ''), 'year': year, 'kv': kv or None,
            'by_date': not _fiscal(acc['kind']), 'opening': opening, 'debit': debit, 'credit': credit, 'closing': opening + debit - credit,
            'rows': rows}


SOURCES = {'bank': 'Bank', 'resolve': 'Bank (aus Parkkonto)', 'reverse-bank': 'Storno Bank', 'invoice': 'Rechnung', 'cancel': 'Storno Rechnung',
           'bill': 'Eingangsrechnung', 'bill-cancel': 'Storno Eingangsrechnung', 'bill-writeoff': 'Ausbuchung Eingangsrechnung',
           'kvumbuchung': 'Budget-Umbuchung'}


def source_label(source):
    prefix = str(source or '').split(':', 1)[0]
    if str(source or '').endswith('/storno'):
        return 'Storno ' + SOURCES.get(prefix, prefix)
    return SOURCES.get(prefix, prefix or '—')


def ledger_csv(store, code, year, kv=None):
    """Kontenblatt als CSV (Semikolon, Euro mit Komma, wie die übrigen Exporte)."""
    from berichte import _csv, _eur
    data = ledger(store, code, year, kv)
    rows = [['', f"{data['year']}-01-01", '', 'Anfangsbestand', '', '', '', '', '', _eur(data['opening'])]] if data['by_date'] else []
    rows += [[r['id'], r['day'], r['year'], r['description'], r['kind'], r['kv'], r['title'], _eur(r['debit']) if r['debit'] else '',
              _eur(r['credit']) if r['credit'] else '', _eur(r['balance'])] for r in data['rows']]
    return _csv(rows, ['Buchung', 'Datum', 'Haushaltsjahr', 'Text', 'Art', 'Bereich', 'Titel', 'Soll', 'Haben', 'Saldo (Soll − Haben)'])
