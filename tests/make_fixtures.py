"""Erzeugt die Test-Kontoauszüge (ausschließlich erfundene Daten, öffentliche Beispiel-IBANs).

Nachbildung des Bankexports: Blatt mit Spalten Valuta | Verwendungszweck | (leer) | Betrag,
ohne Namensspalte. Variante A mit Kopfzeile oben (absteigend sortiert), Variante B mit
Kopfzeile unten (aufsteigend sortiert, wie nach Sortierung in Excel).
Aufruf (nur bei Bedarf): pip install xlwt && python3 tests/make_fixtures.py
"""
from datetime import date
from pathlib import Path
import xlwt

ROWS = [
    (date(2026, 6, 2), 'ECHTZEITÜBERWEISUNG Erika Musterfrau DE02120300000000202051 Mitgliedsbeitrag 2026 Erika Musterfrau', 30.0),
    (date(2026, 6, 3), 'LASTSCHRIFT Muster Telekom GmbH 12345678 Rechnung 06/2026 MREF ABC123', -39.99),
    (date(2026, 6, 5), 'ÜBERWEISUNG Druckerei Beispiel KG DE89370400440532013000 Flyer Sommerfest', -1234.56),
    (date(2026, 6, 5), 'ENTGELT Kontofuehrung 05/2026', -7.5),
    (date(2026, 6, 9), 'ECHTZEITÜBERWEISUNG Max Mustermann DE02120300000000202051 Beitrag 2026', 20.0),
    (date(2026, 6, 9), 'ECHTZEITÜBERWEISUNG Max Mustermann DE02120300000000202051 Beitrag 2026', 20.0),
]
HEADER = ['Valuta', 'Verwendungszweck', '', 'Betrag']


def write(path, rows, header_first):
    book = xlwt.Workbook()
    sheet = book.add_sheet('20260826_020543')
    datefmt = xlwt.easyxf(num_format_str='DD.MM.YYYY')
    numfmt = xlwt.easyxf(num_format_str='#,##0.00')
    lines = ([HEADER] + rows) if header_first else (rows + [HEADER])
    for r, line in enumerate(lines):
        if line is HEADER:
            for c, value in enumerate(HEADER):
                sheet.write(r, c, value)
        else:
            day, text, amount = line
            sheet.write(r, 0, day, datefmt)
            sheet.write(r, 1, text)
            sheet.write(r, 3, amount, numfmt)
    book.save(str(path))


if __name__ == '__main__':
    here = Path(__file__).parent / 'fixtures'
    write(here / 'umsaetze-kopf-oben.xls', sorted(ROWS, key=lambda r: r[0], reverse=True), True)
    write(here / 'umsaetze-kopf-unten.xls', sorted(ROWS, key=lambda r: r[0]), False)
    print('Fixtures erzeugt.')
