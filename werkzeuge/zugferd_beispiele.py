"""Beispiel-E-Rechnungen aus erfundenen Daten in einen Ordner schreiben – zum Prüfen mit einem Prüfwerkzeug von außen.

    python3 werkzeuge/zugferd_beispiele.py <Ordner>
    java -jar Mustang-CLI-<Version>.jar --action validate --source <Ordner>/<Datei>.pdf     (PDF/A-3, Schema, Schematron EN 16931)

Geht über den echten Programmweg (festschreiben, zugferd.pdf) in einer Wegwerf-Datenbank: Beitrag per Überweisung und per
Lastschrift, Rechnung „nicht steuerbar“ und „steuerbefreit“, mit Teilzahlung, bezahlt, mit Sonderzeichen und langer Anschrift,
nur mit USt-IdNr. und nur mit Steuernummer.
Nach jeder Änderung an zugferd.py oder am Aufbau der Rechnungs-PDF laufen lassen und alle Dateien prüfen (AGENTS.md).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

IBAN = 'DE89 3704 0044 0532 0130 00'   # bekannte Beispiel-IBAN


def beispiele(ordner):
    """Schreibt je Fall <Name>.pdf (E-Rechnung) und <Name>.xml (nur die Rechnungsdaten); liefert die Namen."""
    import lastschrift
    import rechnung_pdf
    import zugferd
    from core import Store
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    namen = []
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(Path(tmp) / 'beispiele.db')
        s.save_settings({'erechnung': 'yes', 'issuer': 'Junge Union Beispielstadt', 'issuer_address': 'Landesgeschäftsstelle\nBeispielweg 7\n20000 Hamburg', 'iban': IBAN,
                         'bic': 'COBADEFFXXX', 'bank_name': 'Beispielbank', 'age_basis': 'jan1', 'invoice_ready': 'yes',
                         'tax_note': 'Kein Ausweis von Umsatzsteuer (erfundener Hinweis).', 'contact_email': 'kasse@example.org',
                         'creditor_id': 'DE98ZZZ09999999999', 'tax_number': '22/123/45678', 'vat_id': 'DE123456789'}, 'Beispiel')

        def mitglied(nummer, name):
            return s.member(dict(number=nummer, name=name, address='Musterweg 1\n20000 Hamburg', email='erika@example.org',
                                 birth='2000-02-01', joined='2024-01-01', kv='5100'), 'Beispiel')

        def schreiben(name, iid, **zusatz):
            inv = {**rechnung_pdf.invoice_data(s, iid), **zusatz}
            datei, grund = zugferd.pdf(inv)
            if grund:
                raise SystemExit(f'{name}: keine E-Rechnung – {grund}')
            (ordner / f'{name}.pdf').write_bytes(datei)
            (ordner / f'{name}.xml').write_bytes(zugferd.xml(inv))
            namen.append(name)

        jahr = int(s.next_numbers('dues', 1)[0][2:6]) + 1
        erika, sahin = mitglied('M1', 'Erika Musterfrau'), mitglied('M2', 'Şahin Yıldız-Łukasz')
        schreiben('beitrag-ueberweisung', s.issue_dues(jahr, [erika], 'Beispiel')[0])
        lastschrift.create_mandate(s, sahin, {'iban': IBAN, 'holder': 'Sahin Yildiz', 'signed_on': f'{jahr - 1}-01-10'}, 'Beispiel')
        schreiben('beitrag-lastschrift', s.issue_dues(jahr, [sahin], 'Beispiel')[0])
        for kategorie in zugferd.KATEGORIEN:
            s.save_settings({'tax_category': kategorie}, 'Beispiel')
            ids = s.issue_series(dict(kv='LV', due='', member_ids=[erika], recipients=[dict(
                name='Muster & Söhne GmbH <Einkauf>', address='z. Hd. Frau Beispiel\nHinterhof\nAufgang B\nLange Reihe 5\n20099 Hamburg', email='')],
                lines=[dict(description='Teilnahme Sommerfest', amount='25', account='8504'),
                       dict(description='Getränkepauschale „groß“ & Pfand', amount='7,50', account='8504')]), 'Beispiel')
            schreiben(f'rechnung-{kategorie}-mitglied', ids[0])
            schreiben(f'rechnung-{kategorie}-firma-teilzahlung', ids[1], paid=1000)
        schreiben('rechnung-E-bezahlt', ids[0], paid=3250)
        s.save_settings({'creditor_id': '', 'tax_number': ''}, 'Beispiel')            # steuerbefreit, nur mit USt-IdNr.
        schreiben('rechnung-E-nur-ust-id', s.issue_series(dict(kv='LV', due='', member_ids=[erika], recipients=[], lines=[
            dict(description='Teilnahme Seminar', amount='40', account='8504')]), 'Beispiel')[0])
        s.save_settings({'tax_category': 'O', 'tax_number': '22/123/45678'}, 'Beispiel')   # nicht steuerbar, nur mit Steuernummer
        schreiben('rechnung-O-nur-steuernummer', s.issue_series(dict(kv='LV', due='', member_ids=[erika], recipients=[], lines=[
            dict(description='Teilnahme Seminar', amount='40', account='8504')]), 'Beispiel')[0])
        s.db.close()
    return namen


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    for name in beispiele(sys.argv[1]):
        print(name)
