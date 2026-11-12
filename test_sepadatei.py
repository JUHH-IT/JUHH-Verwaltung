"""SEPA-Dateien einlesen und Sammelbuchungen aufteilen (pain.008, camt.054, ZIP) – nur erfundene Testdaten."""
import io
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

import beitraege
import sepadatei
from core import Store, bank_source

IBAN_A = 'DE89370400440532013000'   # Prüfziffer gültig, erfundene Testkonten
IBAN_B = 'DE02120300000000202051'
NS8 = 'urn:iso:std:iso:20022:tech:xsd:pain.008.001.08'
NS54 = 'urn:iso:std:iso:20022:tech:xsd:camt.054.001.02'
BASE = dict(street='Musterweg 1', zip='20095', city='Hamburg', birth='1995-03-01', joined='2020-01-01')


def pain008(day, txs, msg='TEST-MSG-1', blocks=None):
    """txs: [(Betrag '30.00', Name, IBAN, Mandat, EndToEnd, Verwendungszweck)]"""
    blocks = blocks or [(f'{msg}-1', day, txs)]
    out = [f'<?xml version="1.0" encoding="UTF-8"?><Document xmlns="{NS8}"><CstmrDrctDbtInitn><GrpHdr><MsgId>{msg}</MsgId>'
           f'<NbOfTxs>{sum(len(b[2]) for b in blocks)}</NbOfTxs></GrpHdr>']
    for ref, d, items in blocks:
        out.append(f'<PmtInf><PmtInfId>{ref}</PmtInfId><PmtMtd>DD</PmtMtd><NbOfTxs>{len(items)}</NbOfTxs><ReqdColltnDt>{d}</ReqdColltnDt>')
        for amount, name, iban, mandate, e2e, text in items:
            out.append(f'<DrctDbtTxInf><PmtId><EndToEndId>{e2e}</EndToEndId></PmtId><InstdAmt Ccy="EUR">{amount}</InstdAmt>'
                       f'<DrctDbtTx><MndtRltdInf><MndtId>{mandate}</MndtId></MndtRltdInf></DrctDbtTx><Dbtr><Nm>{name}</Nm></Dbtr>'
                       f'<DbtrAcct><Id><IBAN>{iban}</IBAN></Id></DbtrAcct><RmtInf><Ustrd>{text}</Ustrd></RmtInf></DrctDbtTxInf>')
        out.append('</PmtInf>')
    out.append('</CstmrDrctDbtInitn></Document>')
    return ''.join(out).encode()


def camt054(day, total, txs):
    items = ''.join(f'<TxDtls><Refs><EndToEndId>{e2e}</EndToEndId><MndtId>{mandate}</MndtId></Refs><AmtDtls><TxAmt><Amt Ccy="EUR">{amount}</Amt></TxAmt></AmtDtls>'
                    f'<RltdPties><Dbtr><Nm>{name}</Nm></Dbtr><DbtrAcct><Id><IBAN>{iban}</IBAN></Id></DbtrAcct></RltdPties>'
                    f'<RmtInf><Ustrd>{text}</Ustrd></RmtInf></TxDtls>' for amount, name, iban, mandate, e2e, text in txs)
    return (f'<?xml version="1.0"?><Document xmlns="{NS54}"><BkToCstmrDbtCdtNtfctn><GrpHdr><MsgId>CAMT-1</MsgId></GrpHdr><Ntfctn>'
            f'<Ntry><Amt Ccy="EUR">{total}</Amt><CdtDbtInd>CRDT</CdtDbtInd><BookgDt><Dt>{day}</Dt></BookgDt><AcctSvcrRef>SAMMLER-1</AcctSvcrRef>'
            f'<NtryDtls>{items}</NtryDtls></Ntry>'
            f'<Ntry><Amt Ccy="EUR">5.00</Amt><CdtDbtInd>DBIT</CdtDbtInd><BookgDt><Dt>{day}</Dt></BookgDt></Ntry>'
            f'</Ntfctn></BkToCstmrDbtCdtNtfctn></Document>').encode()


def begleitzettel(rows, encrypt=None, offsets=None, per_page=40, split=False):
    """Begleitzettel wie aus der Mitgliederverwaltung (erfunden): ID, Kontoinhaber, Zahler, BIC, IBAN, Mandat, Verwendungszweck, Betrag.
    offsets: {Zeilennummer: Höhenversatz der Zahler-Zelle in pt}; Überschrift auf jeder Seite. split: wie die echte Liste – Nachname als eigenes
    Textstück, „, Vorname“ an Mandat und Betrag geklebt, ID/Kontoinhaber/BIC/IBAN/Betrag-Überschrift als ein Stück."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(A4), encrypt=encrypt)
    xs = (20, 110, 230, 350, 430, 590, 680, 790)
    for start in range(0, max(len(rows), 1), per_page):
        c.setFont('Helvetica', 7)
        c.drawString(20, 560, f'Erstellungsdatum: 02.03.2026   Message: TEST-MSG-1   Gesamtzahl Sätze: {len(rows)}')
        if split:
            c.drawString(20, 540, 'ID Kontoinhaber BIC IBAN Betrag (€)')
            for x, h in ((230, 'Zahler'), (590, 'Mandat'), (680, 'Verwendungszweck')):
                c.drawString(x, 540, h)
        else:
            for x, h in zip(xs, ('ID', 'Kontoinhaber', 'Zahler', 'BIC', 'IBAN', 'Mandat', 'Verwendungszweck', 'Betrag (€)')):
                c.drawString(x, 540, h)
        for n, row in enumerate(rows[start:start + per_page], start):
            y = 525 - (n - start) * 12
            if split:
                last, _, first = row[2].partition(', ')
                for x, v in ((20, row[0]), (110, row[1]), (230, last), (262, f', {first} {row[5]} {row[7]}' if first else f'{row[5]} {row[7]}'),
                             (350, row[3]), (430, row[4]), (680, row[6])):
                    if v.strip(', '):
                        c.drawString(x, y, v)
                continue
            for col, (x, v) in enumerate(zip(xs, row)):
                if v:
                    c.drawString(x, y + ((offsets or {}).get(n, 0) if col == 2 else 0), v)
        c.showPage()
    c.save()
    return buf.getvalue()


def raw_pdf(content, pages=1):
    """Minimale PDF mit einem (gepackten) Inhalt für alle Seiten – für die Grenzen beim Lesen (erfunden)."""
    import zlib
    data = zlib.compress(content, 9)
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [' + ' '.join(f'{5 + i} 0 R' for i in range(pages)).encode() + b'] /Count ' + str(pages).encode() + b' >>',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
            b'<< /Length ' + str(len(data)).encode() + b' /Filter /FlateDecode >>\nstream\n' + data + b'\nendstream']
    objs += [b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>'] * pages
    out, offs = bytearray(b'%PDF-1.4\n'), []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f'{n} 0 obj\n'.encode() + o + b'\nendobj\n'
    x = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode() + b''.join(f'{o:010d} 00000 n \n'.encode() for o in offs)
    out += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n'.encode()
    return bytes(out)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 't.db')
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1\n20000 Hamburg', 'iban': IBAN_B, 'age_basis': 'jan1',
                              'invoice_ready': 'yes', 'tax_note': 'Fiktiver Hinweis', 'contact_email': 'kasse@example.org', 'creditor_id': 'DE98ZZZ09999999999'},
                              'Test')
        self.anna = self.s.member({**BASE, 'number': 'A0000000001', 'first_name': 'Anna', 'last_name': 'Müller', 'kv': '5100'}, 'Test')
        self.ben = self.s.member({**BASE, 'number': 'A0000000002', 'first_name': 'Ben', 'last_name': 'Beispiel', 'kv': '5300'}, 'Test')
        self.cem = self.s.member({**BASE, 'number': 'A0000000003', 'first_name': 'Cem', 'last_name': 'Probe', 'kv': '5600'}, 'Test')
        self.dora = self.s.member({**BASE, 'number': 'A0000000004', 'first_name': 'Dora', 'last_name': 'Test', 'kv': '5700'}, 'Test')
        import lastschrift
        self.ref = lastschrift.create_mandate(self.s, self.anna, {'iban': IBAN_A, 'signed_on': '2025-01-10', 'holder': 'Anna Müller'}, 'Test')
        self.n = 0

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def bank(self, amount, day, name='Sparkasse', ref='SEPA-Sammler Lastschrift'):
        self.n += 1
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};{name};{ref};s{self.n}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, f's{self.n}.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def txs(self):
        return [('30.00', 'MUELLER ANNA', IBAN_A, self.ref, 'E2E-1', 'Mitgliedsbeitrag 2026'),         # Mandatsreferenz
                ('20.00', 'Peter Beispiel', 'DE44500105175407324931', 'X-99', 'E2E-2', 'Beitrag A0000000002'),   # Mitgliedsnummer
                ('30.00', 'CEM PROBE', 'DE27100777770209299700', 'X-98', 'NOTPROVIDED', 'Beitrag 2026'),  # nur Name
                ('10.00', 'Unbekannt GmbH', 'DE91100000000123456789', 'X-97', 'E2E-4', 'Spende')]       # nichts passt


class ParseTests(Base):
    def test_pain008_camt_zip_and_security(self):
        data = sepadatei.parse(pain008('2026-03-02', self.txs()), 'lastschrift.xml')
        self.assertEqual((data['kind'], len(data['batches']), data['batches'][0]['total'], data['batches'][0]['date']), ('pain.008', 1, 9000, '2026-03-02'))
        self.assertEqual(data['batches'][0]['items'][2]['e2e'], '')                       # NOTPROVIDED = leer
        camt = sepadatei.parse(camt054('2026-03-03', '90.00', self.txs()), 'auszug.xml')
        self.assertEqual((camt['kind'], len(camt['batches']), camt['batches'][0]['total']), ('camt.054', 1, 9000))   # Lastschrift-Ausgang (DBIT) ignoriert
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('a/eins.xml', pain008('2026-03-02', self.txs()[:2], 'M1'))
            z.writestr('zwei.xml', camt054('2026-03-03', '90.00', self.txs()))
            z.writestr('liesmich.txt', 'x')
        both = sepadatei.parse(buf.getvalue(), 'bank.zip')
        self.assertEqual((both['kind'], len(both['batches'])), ('camt.054, pain.008', 2))
        two = sepadatei.parse(pain008('', [], blocks=[('B1', '2026-03-02', self.txs()[:2]), ('B2', '2026-03-02', self.txs()[2:])]), 'x.xml')
        self.assertEqual([b['total'] for b in two['batches']], [5000, 4000, 9000])          # ganze Datei zusätzlich als ein Sammler
        for raw, message in ((b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Document/>', 'DOCTYPE'), (b'<x>', 'lesbare'),
                             (b'<Document xmlns="urn:x"><Other/></Document>', 'keine SEPA'),
                             (camt054('2026-03-03', '90.00', self.txs()[:1]), 'keine Sammelbuchung'),
                             (pain008('2026-03-02', [('30.0x', 'A', IBAN_A, 'M', 'E', 'T')]), 'nicht lesbar')):
            with self.assertRaises(ValueError, msg=message) as cm:
                sepadatei.parse(raw, 'x.xml')
            self.assertIn(message, str(cm.exception))
        self.assertEqual(sepadatei.fold('MÜLLER, Anna-Lena'), {'mueller', 'anna', 'lena'})


class ApplyTests(Base):
    def test_book_open_collective_credit(self):
        bid = self.bank('90,00', '2026-03-03')
        self.bank('90,00', '2026-04-30')                                                    # gleicher Betrag, zu spät: kein Kandidat
        inv = self.s.issue_dues(2026, [self.dora], 'Test')                                  # Dora: offene Beitragsrechnung über 30 €
        prev = sepadatei.preview(self.s, pain008('2026-03-02', self.txs()), 'lastschrift.xml')
        b = prev['batches'][0]
        self.assertEqual([c['bank_id'] for c in b['candidates']], [bid])
        m = [i['match'] for i in b['items']]
        self.assertEqual((m[0]['member_id'], m[0]['reason'], m[0]['sure']), (self.anna, 'Mandatsreferenz', True))
        self.assertEqual((m[1]['member_id'], m[1]['reason']), (self.ben, 'Mitgliedsnummer'))
        self.assertEqual((m[2]['member_id'], m[2]['sure']), (self.cem, False))                # nur Name: Vorschlag
        self.assertEqual(m[3], {})
        self.assertEqual(b['counts'], {'items': 4, 'sure': 2, 'check': 1, 'open': 1})
        public = sepadatei.public(prev)
        self.assertNotIn(IBAN_A, repr(public))                                              # IBAN nur gekürzt
        with self.assertRaises(ValueError) as cm:
            sepadatei.apply(self.s, prev, 0, bid, {'3': {'member_id': self.dora}}, 'Test')
        self.assertIn('bitte bestätigen', str(cm.exception))                                  # nur Name: erst bestätigen
        with self.assertRaises(ValueError) as cm:
            sepadatei.apply(self.s, prev, 0, bid, {'2': {'member_id': self.cem}}, 'Test')
        self.assertIn('noch keinem Mitglied', str(cm.exception))
        result = sepadatei.apply(self.s, prev, 0, bid, {'2': {'member_id': self.cem}, '3': {'member_id': self.dora}}, 'Test')   # Dora → offene Beitragsrechnung
        self.assertEqual((result['mode'], result['members'], result['invoices'], result['count']), ('book', 3, 1, 4))
        self.assertEqual(self.s.db.execute("SELECT state FROM bank WHERE id=?", (bid,)).fetchone()[0], 'booked')
        rows = {r['member_id']: r for r in self.s.rows('SELECT member_id,kv,amount FROM dues_assignments')}
        self.assertEqual({k: (v['kv'], v['amount']) for k, v in rows.items()}, {self.anna: ('5100', 3000), self.ben: ('5300', 2000), self.cem: ('5600', 3000)})
        paid = self.s.db.execute('SELECT SUM(amount) FROM payments WHERE invoice_id=?', (inv[0],)).fetchone()[0]
        self.assertEqual(paid, 1000)                                                         # Teilzahlung auf die Rechnung
        self.assertEqual(self.s.db.execute('SELECT count,total,kind FROM sepa_files').fetchone()[:], (4, 9000, 'pain.008'))
        audit = self.s.rows("SELECT detail FROM audit WHERE action='SEPA-Sammler aufgeteilt'")[0]['detail']
        self.assertNotIn('Müller', audit)
        self.assertNotIn(IBAN_A, audit)
        again = sepadatei.preview(self.s, pain008('2026-03-02', self.txs()), 'x.xml')['batches'][0]
        self.assertEqual((again['candidates'], again['done']), ([], date.today().isoformat()))       # schon aufgeteilt
        with self.assertRaises(ValueError):
            sepadatei.apply(self.s, prev, 0, bid, {'2': {'member_id': self.cem}, '3': {'member_id': self.dora}}, 'Test')
        # Storno: Sammelbuchung lässt sich wieder aufteilen (neue Zuordnungsrunde)
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual([c['bank_id'] for c in sepadatei.preview(self.s, pain008('2026-03-02', self.txs()), 'x.xml')['batches'][0]['candidates']], [bid])

    def test_assign_existing_8100_booking(self):
        bid = self.bank('50,00', '2026-03-05')
        with self.s.db:   # wie aus der Excel-Übernahme: eine 8100-Zeile ohne Mitglied
            self.s.journal('2026-03-05', 'Sammler', bank_source('bank', {'id': bid, 'reversals': 0}),
                           [{'account': '1420', 'kv': 'LV', 'debit': 5000, 'credit': 0}, {'account': '8100', 'kv': 'LV', 'debit': 0, 'credit': 5000, 'title': 'H4'}], year=2026)
            self.s.db.execute("UPDATE bank SET state='booked' WHERE id=?", (bid,))
        raw = camt054('2026-03-05', '50.00', self.txs()[:2])
        prev = sepadatei.preview(self.s, raw, 'camt.xml')
        cand = prev['batches'][0]['candidates']
        self.assertEqual((len(cand), cand[0]['mode']), (1, 'assign'))
        result = sepadatei.apply(self.s, prev, 0, bid, {}, 'Test')
        self.assertEqual((result['mode'], result['members']), ('assign', 2))
        self.assertEqual(beitraege.entries(self.s, only_open=True), [])
        self.assertEqual(sepadatei.candidates(self.s, prev['batches'][0]), [])                    # nicht noch einmal (fremde Datei überschreibt nicht)
        with self.assertRaises(ValueError):                                                  # Rechnung bei schon gebuchter 8100-Zeile: nicht möglich
            inv = self.s.issue_dues(2026, [self.dora], 'Test')[0]
            prev2 = sepadatei.preview(self.s, raw, 'camt.xml')
            sepadatei.apply(self.s, prev2, 0, bid, {'0': {'invoice_id': inv}}, 'Test')

    def test_former_member_in_collective_payment(self):
        bid = self.bank('90,00', '2026-03-03')
        prev = sepadatei.preview(self.s, pain008('2026-03-02', self.txs()), 'lastschrift.xml')
        ex = {'name': 'Unbekannt Ehemalig', 'number': '', 'kv': '5400', 'reason': beitraege.EXTERNAL_REASON}
        with self.assertRaises(ValueError) as cm:
            sepadatei.apply(self.s, prev, 0, bid, {'2': {'member_id': self.cem}, '3': {'external': {**ex, 'name': ''}}}, 'Test')
        self.assertIn('Namen', str(cm.exception))
        result = sepadatei.apply(self.s, prev, 0, bid, {'2': {'member_id': self.cem}, '3': {'external': ex}}, 'Test')
        self.assertEqual((result['members'], result['external'], result['count']), (3, 1, 4))
        self.assertEqual(self.s.rows('SELECT name,kv,amount FROM dues_external'), [{'name': 'Unbekannt Ehemalig', 'kv': '5400', 'amount': 1000}])
        self.assertEqual(self.s.rows('SELECT origin FROM dues_cases'), [{'origin': 'SEPA-Datei'}])   # Herkunft aus der gelesenen Datei
        self.assertEqual(beitraege.entries(self.s, only_open=True), [])
        self.assertNotIn('Ehemalig', self.s.rows("SELECT detail FROM audit WHERE action='SEPA-Sammler aufgeteilt'")[0]['detail'])

    def test_open_dues_invoices_first_never_twice(self):
        """Prüfbefund K1/W1: Zahlungen eines Mitglieds gehen zuerst auf seine offenen Beitragsrechnungen (älteste zuerst), nur der Rest auf 8100."""
        def total_8100():
            return self.s.db.execute("SELECT COALESCE(SUM(credit-debit),0) FROM entries WHERE account='8100'").fetchone()[0]
        def open_of(iid):
            return self.s.db.execute("SELECT i.total-COALESCE((SELECT SUM(amount) FROM payments WHERE invoice_id=i.id),0) FROM invoices i WHERE id=?", (iid,)).fetchone()[0]
        ben26 = self.s.issue_dues(2026, [self.ben], 'Test')[0]                                  # 30 €
        ben27 = self.s.issue_dues(2027, [self.ben], 'Test')[0]                                  # 30 €
        cem26 = self.s.issue_dues(2026, [self.cem], 'Test')[0]                                  # 30 €
        before = total_8100()                                                                    # Sollbuchungen der Rechnungen (90 €)
        txs = [('40.00', 'Peter Beispiel', 'DE44500105175407324931', 'X-1', 'E2E-A', 'Beitrag A0000000002'),   # Ben 40 € → 30 + 10
               ('30.00', 'Cem Probe', 'DE27100777770209299700', 'X-2', 'E2E-B', 'Beitrag A0000000003'),       # Cem zweimal 30 €
               ('30.00', 'Cem Probe', 'DE27100777770209299700', 'X-3', 'E2E-C', 'Beitrag A0000000003')]
        bid = self.bank('100,00', '2026-03-05')
        prev = sepadatei.preview(self.s, pain008('2026-03-04', txs, 'M-K1'), 'k1.xml')
        m = [i['match'] for i in prev['batches'][0]['items']]
        self.assertEqual([x['sure'] for x in m], [False, True, False])        # Ben: 40 € ≠ 30 € und Rechnung 2027 offen; Cem: zweite Zahlung prüfen
        self.assertIn('Folgejahr', m[0]['reason'])
        self.assertIn('weitere Zahlung', m[2]['reason'])
        with self.assertRaises(ValueError):
            sepadatei.apply(self.s, prev, 0, bid, {'0': {'member_id': self.ben}}, 'Test')
        sepadatei.apply(self.s, prev, 0, bid, {'0': {'member_id': self.ben}, '2': {'member_id': self.cem}}, 'Test')
        self.assertEqual((open_of(ben26), open_of(ben27), open_of(cem26)), (0, 3000, 0))         # Ben: 2026 voll; 2027 nicht automatisch
        self.assertEqual(total_8100() - before, 4000)                                          # Rest Ben 10 € + Cems zweite Zahlung, nichts doppelt
        self.assertEqual({r['member_id']: r['amount'] for r in self.s.rows('SELECT member_id,amount FROM dues_assignments')}, {self.ben: 1000, self.cem: 3000})

    def test_overpaid_other_invoice_is_no_membership_fee(self):
        """Nachprüfung N1/N4: Überschuss einer normalen Rechnung wird nicht zum Mitgliedsbeitrag; Betrag passt nicht → prüfen."""
        inv = self.s.issue_series({'member_ids': [self.dora], 'kv': '5700', 'due': '2099-12-31',
                                   'lines': [{'description': 'Seminar (Test)', 'amount': '25', 'account': '8500'}]}, 'Test')
        number = self.s.db.execute('SELECT number FROM invoices WHERE kind=?', ('normal',)).fetchone()[0]
        txs = [('25.00', 'Dora Test', IBAN_A, 'X-1', 'E-1', f'Seminar {number}'), ('25.00', 'Dora Test', IBAN_A, 'X-2', 'E-2', f'Seminar {number}')]
        bid = self.bank('50,00', '2026-03-05')
        prev = sepadatei.preview(self.s, pain008('2026-03-04', txs, 'M-N1'), 'n1.xml')
        m = [i['match'] for i in prev['batches'][0]['items']]
        self.assertEqual([x['sure'] for x in m], [True, False])                                 # zweite: Rechnung schon ausgeglichen
        with self.assertRaises(ValueError) as cm:
            sepadatei.apply(self.s, prev, 0, bid, {'1': {'invoice_id': m[1]['invoice_id']}}, 'Test')
        self.assertIn('übersteigen', str(cm.exception))
        self.assertEqual(self.s.db.execute("SELECT state FROM bank WHERE id=?", (bid,)).fetchone()[0], 'open')
        one = sepadatei.parse(pain008('2026-03-04', [('30.00', 'Dora Test', IBAN_A, 'X-3', 'E-3', f'Beitrag A0000000004 {number}')] * 2, 'M-N4'), 'n4.xml')
        hit = sepadatei.match(one['batches'][0]['items'][0], sepadatei._index(self.s), 2026)
        self.assertEqual((hit['member_id'], hit['sure'], 'Betrag passt nicht' in hit['reason']), (self.dora, False, True))
        self.assertTrue(inv)

    def test_safety_of_file_reading(self):
        utf16 = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY a "b">]><Document/>'.encode('utf-16')
        enc = io.BytesIO()
        with zipfile.ZipFile(enc, 'w') as z:
            z.writestr('a.xml', pain008('2026-03-02', self.txs()))
        raw = bytearray(enc.getvalue())
        central = raw.index(b'PK\x01\x02')
        raw[6] |= 1                                                                               # Kennzeichen „verschlüsselt“ (lokal und im Verzeichnis)
        raw[central + 8] |= 1
        raw = bytes(raw)
        for data, message in ((utf16, 'DOCTYPE'), (raw, 'ZIP-Datei nicht lesbar'),
                              (pain008('2026-03-02', [('1E+30', 'A', IBAN_A, 'M', 'E', 'T')] * 2), 'nicht lesbar'),
                              (pain008('2026-03-02', [('Infinity', 'A', IBAN_A, 'M', 'E', 'T')] * 2), 'nicht lesbar'),
                              (pain008('2026-03-02', [('999999999999.99', 'A', IBAN_A, 'M', 'E', 'T')] * 2), 'ungültig')):
            with self.assertRaises(ValueError, msg=message) as cm:
                sepadatei.parse(data, 'x.xml')
            self.assertIn(message, str(cm.exception))
        two = sepadatei.parse(pain008('2026-03-02', [('30.00', 'X', IBAN_A, 'M', 'E', 'Beitrag A0000000002 und A0000000003')] * 2), 'x.xml')
        ix = sepadatei._index(self.s)
        hit = sepadatei.match(two['batches'][0]['items'][0], ix)
        self.assertEqual((hit['sure'], 'mehrere' in hit['reason']), (False, True))             # Geschwister: nur Vorschlag

    ROW = staticmethod(lambda e2e, holder, payer, iban, mandate, amount: (e2e, holder, payer, 'INGDDEFFXXX', iban, mandate, 'MITGLIEDSBEITRAG', amount))

    def txs_mv(self):
        return [('30.00', 'ELTERN PROBE', 'DE27100777770209299700', 'MV-0001', 'EEID-1001', 'MITGLIEDSBEITRAG'),   # Kontoinhaber ≠ Mitglied
                ('20.00', 'BEN BEISPIEL', 'DE44500105175407324931', 'MV-0002', 'EEID-1002', 'MITGLIEDSBEITRAG'),
                ('10.00', 'NIEMAND BEKANNT', 'DE91100000000123456789', 'MV-0003', 'EEID-1003', 'MITGLIEDSBEITRAG')]

    def test_payer_from_pdf(self):
        """0.30: Spalte „Zahler“ aus dem Begleitzettel über die Position der Überschrift; jede Zelle gehört zu genau einer Zeile."""
        R = self.ROW
        rows = [R('EEID-1001', 'ELTERN PROBE', 'Probe, Cem', 'DE27100777770209299700', 'MV-0001', '30,00'),
                R('EEID-1002', 'BEN BEISPIEL', 'Beispiel, Ben', 'DE44500105175407324931', 'MV-0002', '20,00'),
                R('EEID-1003', 'NIEMAND BEKANNT', 'Bekannt, Niemand', 'DE91100000000123456789', 'MV-0003', '10,00'),
                R('EEID-9999', 'FREMD', 'Fremd, Zeile', 'DE89370400440532013000', 'MV-9999', '5,00')]
        prev = sepadatei.preview(self.s, pain008('2026-03-02', self.txs_mv(), 'M-PDF'), 'mv.xml')
        self.assertEqual([bool(i['match']) for i in prev['batches'][0]['items']], [False, True, False])   # ohne PDF: nur Ben über den Namen
        self.assertEqual(sepadatei.add_payers(self.s, prev, begleitzettel(rows)), 3)
        items = prev['batches'][0]['items']
        self.assertEqual([i['payer'] for i in items], ['Probe, Cem', 'Beispiel, Ben', 'Bekannt, Niemand'])
        self.assertEqual((items[0]['match']['member_id'], items[0]['match']['reason'], items[0]['match']['bulk']),
                         (self.cem, 'Zahler laut Begleitzettel – bitte prüfen', True))
        self.assertEqual((items[2]['match'], prev['batches'][0]['counts']), ({}, {'items': 3, 'sure': 0, 'check': 2, 'open': 1}))
        self.assertEqual(sepadatei.public(prev)['batches'][0]['items'][0]['payer'], 'Probe, Cem')
        items3 = [{'e2e': f'EEID-{n}', 'mandate': f'MV-{n}', 'name': 'X'} for n in (1, 2, 3)]
        R3 = [R(f'EEID-{n}', 'KONTO', p, 'DE89370400440532013000', f'MV-{n}', '1,00') for n, p in ((1, 'Erster, Eins'), (2, ''), (3, 'Langername-Mit-Viel-Text, Drei'))]
        self.assertEqual(sepadatei.payers_in_pdf(begleitzettel(R3), items3), {'EEID-1': 'Erster, Eins', 'EEID-3': 'Langername-Mit-Viel-Text, Drei'})   # leer bleibt leer
        shifted = [R(f'EEID-{n}', 'KONTO', f'Zahler, {v}', 'DE89370400440532013000', f'MV-{n}', '1,00') for n, v in ((1, 'Anna'), (2, 'Bert'), (3, 'Carl'))]
        self.assertEqual(sepadatei.payers_in_pdf(begleitzettel(shifted, offsets={1: 8}), items3),                 # Zelle 8 pt höher: nie zur Vorzeile
                         {'EEID-1': 'Zahler, Anna', 'EEID-3': 'Zahler, Carl'})
        self.assertEqual(sepadatei.payers_in_pdf(begleitzettel(shifted + [R('EEID-9', 'KONTO', '', 'DE1', 'MV-9', '1,00')], split=True), items3),
                         {'EEID-1': 'Zahler, Anna', 'EEID-2': 'Zahler, Bert', 'EEID-3': 'Zahler, Carl'})   # zerlegte Namen wie in der echten Liste
        many = [{'e2e': f'EEID-{n}', 'mandate': f'MV-{n}', 'name': 'X'} for n in range(90)]
        pages = begleitzettel([R(f'EEID-{n}', 'KONTO', f'Person, {chr(65 + n % 26)}{chr(65 + n // 26)}', 'DE89370400440532013000', f'MV-{n}', '1,00')
                               for n in range(90)])
        self.assertEqual(len(sepadatei.payers_in_pdf(pages, many)), 90)                                       # über mehrere Seiten
        for raw, message in ((b'kein pdf', 'keine PDF'), (b'%PDF-1.4 kaputt', 'nicht lesbar'), (begleitzettel([], encrypt='geheim'), 'verschlüsselt'),
                             (begleitzettel([R('EEID-7', 'A', 'B, C', 'DE1', 'M', '1,00')]), 'keine Zeile'),
                             (raw_pdf(b'BT /F1 7 Tf 20 20 Td (Erfunden, Max) Tj ET\n' * 30_000), 'nicht lesbar')):   # entpackt über 1 MB
            with self.assertRaises(ValueError, msg=message) as cm:
                sepadatei.add_payers(self.s, prev, raw)
            self.assertIn(message, str(cm.exception))
        import time
        t = time.monotonic()
        self.assertEqual(sepadatei.payers_in_pdf(raw_pdf(b'BT /F1 7 Tf 20 20 Td (Erfunden, Max) Tj ET\n' * 15_000, pages=300), items3), {})
        self.assertLess(time.monotonic() - t, 15)                                                                # ein Inhalt für 300 Seiten: einmal gelesen

    def test_pdf_time_limit_inside_page_and_old_table(self):
        """Nachprüfung 0.30: Frist auch innerhalb einer Seite (oft aufgerufenes Formular); altes Tabellenformat wird umgestellt."""
        import time
        import zlib
        text = b''.join(b'BT /F1 7 Tf 20 %d Td (Erfunden %d) Tj ET\n' % (10 + i % 570, i) for i in range(20_000))
        form = zlib.compress(text, 9)
        page = zlib.compress(b'/X1 Do\n' * 40, 9)
        objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [4 0 R] /Count 1 >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 3 0 R >> /XObject << /X1 6 0 R >> >> /Contents 5 0 R >>',
                b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(page) + page + b'\nendstream',
                b'<< /Length %d /Filter /FlateDecode /Type /XObject /Subtype /Form /BBox [0 0 842 595] /Resources << /Font << /F1 3 0 R >> >> >>\nstream\n'
                % len(form) + form + b'\nendstream']
        out, offs = bytearray(b'%PDF-1.4\n'), []
        for n, o in enumerate(objs, 1):
            offs.append(len(out))
            out += f'{n} 0 obj\n'.encode() + o + b'\nendobj\n'
        x = len(out)
        out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode() + b''.join(f'{o:010d} 00000 n \n'.encode() for o in offs)
        out += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n'.encode()
        t = time.monotonic()
        with self.assertRaises(ValueError) as cm:
            sepadatei.payers_in_pdf(bytes(out), [{'e2e': 'E-1', 'mandate': 'MV-1', 'name': 'X'}])
        self.assertIn('zu viel Text', str(cm.exception))
        self.assertLess(time.monotonic() - t, 5)
        with self.s.db:                                                                        # Zwischenstand 0.30 ohne Gläubiger-ID
            self.s.db.execute('DROP TABLE sepa_mandates')
            self.s.db.execute('CREATE TABLE sepa_mandates(mandate TEXT PRIMARY KEY,member_id INTEGER NOT NULL REFERENCES members(id),learned TEXT NOT NULL,actor TEXT)')
            self.s.db.execute("INSERT INTO sepa_mandates VALUES('MV-0001',?,'2026-01-01','Test')", (self.cem,))
        sepadatei.init(self.s.db)
        self.assertEqual([(r['creditor'], r['mandate']) for r in sepadatei.learned_for(self.s, self.cem)], [('', 'MV-0001')])

    def test_unclear_payments_become_cases(self):
        """0.31: unklare Einzelzahlungen als Klärfall melden – bleiben auf 8100 ohne Mitglied; nach dem Klären ist das Mandat gemerkt."""
        import klaerfaelle
        prev = sepadatei.preview(self.s, pain008('2026-03-02', self.txs_mv(), 'M-K'), 'mv.xml')
        bid = self.bank('60,00', '2026-03-03')
        r = sepadatei.apply(self.s, prev, 0, bid, {'0': {'case': {}}, '1': {'member_id': self.ben}, '2': {'case': {'reason': 'ausgetreten?'}}}, 'Test')
        self.assertEqual((r['mode'], r['members'], r['cases'], r['learned']), ('book', 1, 2, 1))
        cases = [c for c in klaerfaelle.items(self.s)]
        self.assertEqual([(c['amount'], c['origin'], c['status'], c['booked'], c['reason']) for c in cases],
                         [(3000, 'SEPA-Datei', 'offen', False, klaerfaelle.REASON), (1000, 'SEPA-Datei', 'offen', False, 'ausgetreten?')])
        self.assertIn('Mandat MV-0001', cases[0]['info'])
        self.assertEqual(len(beitraege.pending(self.s, 2026)), 2)
        self.assertEqual(beitraege.entries(self.s, only_open=True), [])
        audit = self.s.rows("SELECT detail FROM audit WHERE action='SEPA-Sammler aufgeteilt'")[0]['detail']
        self.assertIn('"klaerfaelle": 2', audit)
        self.assertNotIn('PROBE', audit.upper())
        klaerfaelle.resolve(self.s, cases[0]['id'], {'member_id': self.cem}, 'MV')
        self.assertIn({'creditor': '', 'mandate': 'MV-0001', 'member_id': self.cem}, self.s.rows('SELECT creditor,mandate,member_id FROM sepa_mandates'))
        nxt = sepadatei.preview(self.s, pain008('2027-03-02', self.txs_mv()[:1], 'M-K2'), 'next.xml')
        m = nxt['batches'][0]['items'][0]['match']
        self.assertEqual((m['member_id'], m['sure']), (self.cem, True))
        # schon auf 8100 gebuchter Sammler mit gemeldetem Klärfall: die Datei teilt neu auf, der alte Klärfall ist erledigt
        bid2 = self.bank('50,00', '2026-04-05')
        with self.s.db:
            self.s.journal('2026-04-05', 'Sammler', bank_source('bank', {'id': bid2, 'reversals': 0}),
                           [{'account': '1420', 'kv': 'LV', 'debit': 5000, 'credit': 0}, {'account': '8100', 'kv': 'LV', 'debit': 0, 'credit': 5000, 'title': 'H4'}], year=2026)
            self.s.db.execute("UPDATE bank SET state='booked' WHERE id=?", (bid2,))
        eid = self.s.db.execute("SELECT MAX(id) FROM entries WHERE account='8100'").fetchone()[0]
        old = klaerfaelle.report(self.s, eid, {}, 'Test')
        prev = sepadatei.preview(self.s, camt054('2026-04-05', '50.00', self.txs()[:2]), 'camt.xml')
        r = sepadatei.apply(self.s, prev, 0, bid2, {'1': {'case': {}}}, 'Test')
        self.assertEqual((r['mode'], r['members'], r['cases']), ('assign', 1, 1))
        mine = [c for c in klaerfaelle.items(self.s) if c['entry_id'] == eid]
        self.assertEqual(sorted((c['status'], c['amount']) for c in mine), [('geklaert', 5000), ('offen', 2000)])
        self.assertEqual(next(c for c in mine if c['id'] == old)['result'], 'neu_zugeordnet')

    def test_learned_mandates(self):
        """0.30 + Prüfung: nur Einzelbestätigungen werden gemerkt; Widersprüche, Mehrfachreferenz, Austritt → prüfen; Storno/extern löschen."""
        prev = sepadatei.preview(self.s, pain008('2026-03-02', self.txs_mv(), 'M-L'), 'mv.xml')
        bid = self.bank('60,00', '2026-03-03')
        ex = {'name': 'Niemand Bekannt', 'number': '', 'kv': '5100', 'reason': 'ausgetreten'}
        r = sepadatei.apply(self.s, prev, 0, bid, {'0': {'member_id': self.cem}, '1': {'member_id': self.ben, 'confirmed': 'bulk'},
                                                   '2': {'external': ex}}, 'Test')
        self.assertEqual(r['learned'], 1)                                                 # Sammelbestätigung und Externe nicht
        self.assertEqual(self.s.rows('SELECT creditor,mandate,member_id FROM sepa_mandates'), [{'creditor': '', 'mandate': 'MV-0001', 'member_id': self.cem}])
        audit = self.s.rows("SELECT detail FROM audit WHERE action='SEPA-Sammler aufgeteilt'")[0]['detail']
        self.assertIn('"referenz": "MV-0001"', audit)
        self.assertNotIn('Probe', audit)
        def nxt(txs, pdf=None, day='2027-03-02'):
            p = sepadatei.preview(self.s, pain008(day, txs, f'M-{day}-{len(txs)}'), 'next.xml')
            if pdf:
                sepadatei.add_payers(self.s, p, pdf)
            return [i['match'] for i in p['batches'][0]['items']]
        m = nxt(self.txs_mv()[:1])
        self.assertEqual((m[0]['member_id'], m[0]['reason'], m[0]['sure']), (self.cem, 'Mandatsreferenz (gemerkt)', True))
        R = self.ROW
        m = nxt(self.txs_mv()[:1], begleitzettel([R('EEID-1001', 'ELTERN PROBE', 'Test, Dora', 'DE27100777770209299700', 'MV-0001', '30,00')]))
        self.assertEqual((m[0]['member_id'], m[0]['sure'], m[0]['bulk']), (self.cem, False, False))           # Begleitzettel widerspricht
        self.assertIn('Zahler laut Begleitzettel passt zu einem anderen Mitglied', m[0]['reason'])
        tx = self.txs_mv()[0]
        m = nxt([(tx[0], tx[1], tx[2], tx[3], tx[4], 'Beitrag A0000000004')])
        self.assertEqual(m[0]['sure'], False)                                                                  # Mitgliedsnummer widerspricht
        m = nxt([(tx[0], tx[1], tx[2], tx[3], tx[4], 'Beitrag A0000000003 und A0000000004')])
        self.assertFalse(m[0]['sure'])                                                         # zwei Nummern, eine davon fremd: Widerspruch
        m = nxt([tx, ('30.00', tx[1], tx[2], tx[3], 'EEID-2001', 'MITGLIEDSBEITRAG')])
        self.assertTrue(all('mehrfach in der Datei' in x['reason'] and not x['sure'] for x in m))           # Geschwister mit Elternmandat
        prev2 = sepadatei.preview(self.s, pain008('2027-03-02', [tx, ('30.00', tx[1], tx[2], tx[3], 'EEID-2001', 'MITGLIEDSBEITRAG')], 'M-G'), 'g.xml')
        self.assertEqual(sepadatei._targets(self.s, prev2['batches'][0], {'0': {'member_id': self.cem}, '1': {'member_id': self.dora}},
                                            sepadatei._index(self.s))[3], {})                                    # nie merken
        with self.s.db:
            self.s.db.execute("UPDATE members SET left_on='2026-12-31' WHERE id=?", (self.cem,))
        m = nxt([tx])
        self.assertIn('ausgetreten', m[0]['reason'])
        self.assertFalse(m[0]['sure'])
        self.assertEqual([r['mandate'] for r in sepadatei.learned_for(self.s, self.cem)], ['MV-0001'])
        # Storno der Aufteilung: gemerkte Referenz gilt nicht mehr; Korrektur auf eine Person außerhalb der Liste löscht sie
        self.s.reverse_bank(bid, 'Test')
        self.assertEqual(sepadatei.learned_for(self.s, self.cem), [])
        with self.s.db:
            self.s.db.execute("INSERT INTO sepa_mandates VALUES('','MV-0001',?,NULL,'2026-01-01','Test')", (self.cem,))
        prev3 = sepadatei.preview(self.s, pain008('2026-03-02', self.txs_mv(), 'M-L'), 'mv.xml')
        r = sepadatei.apply(self.s, prev3, 0, bid, {'0': {'external': {**ex, 'name': 'Eltern Probe'}}, '1': {'member_id': self.ben},
                                                    '2': {'external': ex}}, 'Test')
        self.assertEqual(sepadatei.learned_for(self.s, self.cem), [])
        self.assertEqual([r['mandate'] for r in sepadatei.learned_for(self.s, self.ben)], ['MV-0002'])
        sepadatei.forget(self.s, '', 'MV-0002', 'Test')
        self.assertEqual(sepadatei.learned_for(self.s, self.ben), [])
        with self.assertRaises(ValueError):
            sepadatei.forget(self.s, '', 'MV-0002', 'Test')

    def test_mismatch_and_wrong_input(self):
        bid = self.bank('90,00', '2026-03-03')
        prev = sepadatei.preview(self.s, camt054('2026-03-03', '90.00', self.txs()[:3]), 'x.xml')   # Einzelposten 80 € ≠ 90 €
        self.assertIn('weicht', prev['batches'][0]['note'])
        for batch in (5, '0', True):
            with self.assertRaises(ValueError):
                sepadatei.apply(self.s, prev, batch, bid, {}, 'Test')
        with self.assertRaises(ValueError) as cm:
            sepadatei.apply(self.s, prev, 0, bid, {}, 'Test')
        self.assertIn('weicht', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
