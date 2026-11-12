"""Eingebettete PDF-Schrift: Namen mit Sonderzeichen erscheinen vollständig – nur erfundene Testdaten."""
import tempfile
import unittest
from pathlib import Path

import pdfschrift
import rechnung_pdf
from core import Store

NAMES = 'Şahin Yıldız · Łukasz Wójcik · Čeněk Dvořák · Ştefan Ţurcanu · Đorđe Kovačević · Ærø Øster · Müller-Lüdenscheidt €'


class FontTests(unittest.TestCase):
    def test_font_covers_common_names(self):
        self.assertTrue(pdfschrift.covers(NAMES))
        self.assertFalse(pdfschrift.covers('漢字'))                                   # bewusst nicht abgedeckt

    def test_invoice_embeds_font(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Store(Path(tmp.name) / 't.db')
        self.addCleanup(s.db.close)
        s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1', 'iban': 'DE89 3704 0044 0532 0130 00', 'age_basis': 'jan1'}, 'Test')
        m = s.member(dict(number='M1', name='Şahin Yıldız', address='Ağaçlı Sokak 5\n20000 Hamburg', email='', birth='2000-02-01',
                          joined='2024-01-01', kv='5100'), 'Test')
        pdf = rechnung_pdf.render([rechnung_pdf.invoice_data(s, s.issue_dues(2026, [m], 'Test')[0])])
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertTrue(b'LiberationSans' in pdf)                                    # Schrift eingebettet

    def test_license_shipped(self):
        self.assertIn('SIL OPEN FONT LICENSE', (pdfschrift.FOLDER / 'LICENSE-Liberation.txt').read_text())


if __name__ == '__main__':
    unittest.main()
