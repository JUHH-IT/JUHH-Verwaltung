"""Rechnungen, Stammdaten und E-Mail-Versand – nur erfundene Testdaten, Versand nur an einen lokalen Testserver."""
import email
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from email import policy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'tests'))

import geheimnis
import rechnung_pdf
import stammdaten
import versand
from core import Store
from fake_smtp import FakeSMTP

IBAN = 'DE89 3704 0044 0532 0130 00'
TODAY = date.today().isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'test.db'
        self.s = Store(self.db)
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1\n20000 Hamburg', 'iban': IBAN,
                              'age_basis': 'jan1', 'invoice_ready': 'yes', 'tax_note': 'Fiktiver Hinweis',
                              'contact_email': 'kasse@example.org'}, 'Test')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def member(self, number='M1', name='Erika Musterfrau', email='erika@example.org', dues='', joined='2024-01-01', kv='5100'):
        return self.s.member(dict(number=number, name=name, address='Musterweg 1\n20000 Hamburg', email=email,
                                  birth='2000-02-01', joined=joined, kv=kv, dues=dues), 'Test')

    def series(self, member_ids=(), recipients=(), lines=None, due=''):
        return dict(kv='LV', due=due, member_ids=list(member_ids), recipients=list(recipients),
                    lines=lines or [dict(description='Teilnahme Sommerfest', amount='25', account='8504')])


class StammdatenTests(Base):
    def test_defaults_and_overrides(self):
        cfg = self.s.config()
        self.assertEqual(cfg['smtp_host'], 'smtp.strato.de')
        self.assertEqual(cfg['iban'], IBAN)
        self.assertIn('{nummer}', cfg['tpl_dues_body'])

    def test_validation(self):
        for data in [{'iban': 'DE00 1234'}, {'tpl_dues_body': 'Hallo {vornahme}'}, {'tpl_dues_subject': 'a\nb'},
                     {'mail_from': 'kein-mail'}, {'smtp_security': 'none'}, {'dues_young_max': '25'},
                     {'mail_pause': '99'}, {'bic': '123'}, {'dues_terms': '-1'}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.s.save_settings(data, 'Test')
        self.s.save_settings({'smtp_host': '127.0.0.1', 'smtp_security': 'none'}, 'Test')  # nur lokal erlaubt

    def test_amounts_stored_as_cents(self):
        self.s.save_settings({'dues_young': '12,50', 'dues_old': '40'}, 'Test')
        cfg = self.s.config()
        self.assertEqual((cfg['dues_young'], cfg['dues_old']), ('1250', '4000'))

    def test_iban_normalized(self):
        self.s.save_settings({'iban': 'de89370400440532013000'}, 'Test')
        self.assertEqual(self.s.config()['iban'], IBAN)


class MemberAndDuesTests(Base):
    def test_update_member(self):
        mid = self.member()
        self.s.update_member(mid, dict(number='M1', name='Erika Neu', address='Neuer Weg 2', email='neu@example.org',
                                       birth='2000-02-01', joined='2024-01-01', kv='5200', dues='45'), 'Test')
        m = self.s.db.execute('SELECT * FROM members WHERE id=?', (mid,)).fetchone()
        self.assertEqual((m['name'], m['kv'], m['email'], m['dues']), ('Erika Neu', '5200', 'neu@example.org', 4500))
        self.assertIn('Mitglied geändert', [a['action'] for a in self.s.rows('SELECT action FROM audit')])
        with self.assertRaises(ValueError):
            self.s.update_member(mid, dict(number='M1', name='X', address='Y', email='falsch', birth='2000-02-01',
                                           joined='2024-01-01', kv='5200'), 'Test')

    def test_individual_dues_and_scale(self):
        individual = self.member(number='I', dues='55')
        staffel = self.member(number='S')
        rows = {r['number']: r for r in self.s.dues_preview(2026)}
        self.assertEqual((rows['I']['amount'], rows['I']['basis']), (5500, 'individueller Jahresbeitrag'))
        self.assertEqual(rows['S']['amount'], 3000)
        self.s.save_settings({'dues_old': '35'}, 'Test')
        self.assertEqual({r['number']: r['amount'] for r in self.s.dues_preview(2026)}['S'], 3500)
        ids = self.s.issue_dues(2026, [individual, staffel], 'Test')
        self.assertEqual(sorted(i['total'] for i in self.s.invoices() if i['id'] in ids), [3500, 5500])

    def test_individual_dues_without_age_basis(self):
        self.s.save_settings({'age_basis': ''}, 'Test')
        mid = self.member(dues='20')
        self.assertEqual(self.s.dues_amount(self.s.db.execute('SELECT * FROM members WHERE id=?', (mid,)).fetchone(), 2026), 2000)

    def test_proration_switch(self):
        m = {'birth': '2000-01-01', 'joined': '2026-04-01', 'left_on': None, 'dues': 4000}
        self.assertEqual(self.s.dues_amount(m, 2026), 2000)
        self.s.save_settings({'dues_prorate': 'no'}, 'Test')
        self.assertEqual(self.s.dues_amount(m, 2026), 4000)

    def test_due_date_not_in_past(self):
        mid = self.member()
        iid = self.s.issue_dues(2026, [mid], 'Test')[0]
        inv = [i for i in self.s.invoices() if i['id'] == iid][0]
        self.assertEqual(inv['due'], (date.today() + timedelta(days=14)).isoformat())


class SeriesTests(Base):
    def test_series_preview_and_issue(self):
        a, b = self.member(number='A', name='Anna Alpha'), self.member(number='B', name='Ben Beta', email='')
        data = self.series([a, b], [dict(name='Gast GmbH', address='Gastweg 3', email='gast@example.org')])
        p = self.s.series_preview(data)
        self.assertEqual((len(p['recipients']), p['total'], p['sum']), (3, 2500, 7500))
        self.assertEqual(p['numbers'], [f'R-{TODAY[:4]}-00001', f'R-{TODAY[:4]}-00003'])
        self.assertEqual(self.s.rows('SELECT * FROM invoices'), [])  # Vorschau schreibt nichts
        ids = self.s.issue_series(data, 'Test')
        inv = {i['recipient']: i for i in self.s.invoices()}
        self.assertEqual([inv[n]['number'][-5:] for n in ('Anna Alpha', 'Ben Beta', 'Gast GmbH')], ['00001', '00002', '00003'])
        self.assertEqual((inv['Anna Alpha']['mail_to'], inv['Ben Beta']['mail_to'], inv['Gast GmbH']['mail_to']),
                         ('erika@example.org', '', 'gast@example.org'))
        self.assertEqual(inv['Anna Alpha']['member_id'], a)
        self.assertEqual(len(ids), 3)
        self.assertEqual(self.s.next_numbers('normal', 1), [f'R-{TODAY[:4]}-00004'])

    def test_series_validation(self):
        mid = self.member()
        for data in [self.series(), self.series([mid], lines=[dict(description='X', amount='5', account='8100')]),
                     self.series([mid], lines=[dict(description='', amount='5', account='8500')]),
                     self.series(recipients=[dict(name='Ohne Anschrift', address='', email='')]),
                     self.series([mid], due='2020-01-01')]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.s.series_preview(data)

    def test_member_email_change_applies_to_unsent_invoice(self):
        mid = self.member()
        iid = self.s.issue_series(self.series([mid]), 'Test')[0]
        self.s.update_member(mid, dict(number='M1', name='Erika Musterfrau', address='Musterweg 1', email='neu@example.org',
                                       birth='2000-02-01', joined='2024-01-01', kv='5100'), 'Test')
        self.assertEqual([i['mail_to'] for i in self.s.invoices() if i['id'] == iid], ['neu@example.org'])


class PdfTests(Base):
    def test_render_many_and_qr(self):
        ids = self.s.issue_series(self.series([self.member(), self.member(number='M2')]), 'Test')
        invs = [rechnung_pdf.invoice_data(self.s, i) for i in ids]
        pdf = rechnung_pdf.render(invs)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(pdf.count(b'/Type /Page\n') + pdf.count(b'/Type /Page>>') + pdf.count(b'/Type /Page '), 2)
        payload = rechnung_pdf.epc_payload(invs[0]).split('\n')
        self.assertEqual(payload[:4] + payload[6:8], ['BCD', '002', '1', 'SCT', 'DE89370400440532013000', 'EUR25.00'])

    def test_no_qr_when_cancelled_or_free(self):
        iid = self.s.issue_series(self.series([self.member()]), 'Test')[0]
        self.s.reverse_invoice(iid, 'Test')
        inv = rechnung_pdf.invoice_data(self.s, iid)
        self.assertIsNone(rechnung_pdf.epc_payload(inv))
        self.assertTrue(rechnung_pdf.render([inv]).startswith(b'%PDF'))

    def test_snapshot_keeps_old_issuer(self):
        iid = self.s.issue_series(self.series([self.member()]), 'Test')[0]
        self.s.save_settings({'issuer': 'Neuer Name'}, 'Test')
        self.assertEqual(rechnung_pdf.invoice_data(self.s, iid)['snap']['issuer'], 'JU Test')


class MailTests(Base):
    def setUp(self):
        super().setUp()
        self.smtp = FakeSMTP(user='kasse@example.org', password='geheim').start()
        self.s.save_settings({'mail_from': 'kasse@example.org', 'smtp_user': 'kasse@example.org', 'smtp_host': '127.0.0.1',
                              'smtp_port': str(self.smtp.port), 'smtp_security': 'none', 'mail_pause': '0',
                              'mail_bcc': 'archiv@example.org'}, 'Test')
        geheimnis.save('kasse@example.org', 'geheim')

    def tearDown(self):
        geheimnis.forget('kasse@example.org')
        self.smtp.stop()
        super().tearDown()

    def wait(self):
        for _ in range(200):
            if versand.status()['state'] != 'running':
                return versand.status()
            time.sleep(0.05)
        self.fail('Versand endet nicht')

    def test_message_structure(self):
        mid = self.member()
        iid = self.s.issue_dues(2026, [mid], 'Test')[0]
        inv = rechnung_pdf.invoice_data(self.s, iid)
        msg = versand.message(self.s.config(), inv, rechnung_pdf.render([inv]), 'erika@example.org')
        self.assertEqual(msg['Subject'], 'Deine Beitragsrechnung 2026 – Junge Union Hamburg')
        self.assertEqual(msg['Bcc'], 'archiv@example.org')
        self.assertEqual(msg.get_content_type(), 'multipart/mixed')
        text = msg.get_body(('plain',)).get_content()
        self.assertIn('Hallo Erika,', text)
        self.assertIn(inv['number'], text)
        self.assertIn('30,00 €', text)
        self.assertIn('<b', msg.get_body(('html',)).get_content().lower())
        attachment = next(msg.iter_attachments())
        self.assertEqual(attachment.get_filename(), inv['number'] + '.pdf')

    def test_html_escapes_values(self):
        mid = self.member(name='<script>alert(1)</script>')
        iid = self.s.issue_dues(2026, [mid], 'Test')[0]
        inv = rechnung_pdf.invoice_data(self.s, iid)
        _, body = versand.texts(self.s.config(), inv)
        self.assertNotIn('<script>', versand.html_body(self.s.config(), inv, body))

    def test_check_and_test_mail(self):
        versand.check(self.s.config())
        self.assertEqual(self.smtp.messages, [])
        versand.send_test(self.s.config(), versand.sample_invoice(self.s.config(), 'dues'))
        self.assertEqual(self.smtp.messages[0]['to'], ['kasse@example.org'])  # nur an sich selbst, ohne Bcc
        self.assertIn(b'[TEST]', self.smtp.messages[0]['data'])

    def test_batch_send_and_no_double_send(self):
        a, b, c = self.member(number='A'), self.member(number='B', email=''), self.member(number='C', email='c@example.org')
        ids = self.s.issue_dues(2026, [a, b, c], 'Test')
        job = versand.start(self.db, ids, 'Test')
        self.assertEqual((job['total'], [x['reason'] for x in job['skipped']]), (2, ['keine E-Mail-Adresse']))
        job = self.wait()
        self.assertEqual((job['state'], job['sent'], job['failed']), ('done', 2, 0))
        self.assertEqual(sorted(m['to'][0] for m in self.smtp.messages), ['c@example.org', 'erika@example.org'])
        self.assertTrue(all('archiv@example.org' in m['to'] for m in self.smtp.messages))  # Bcc als Kopie
        parsed = email.message_from_bytes(self.smtp.messages[0]['data'], policy=policy.default)
        self.assertNotIn('Bcc', parsed)
        sent = [i for i in self.s.invoices() if i['sent_at']]
        self.assertEqual(len(sent), 2)
        with self.assertRaises(ValueError):  # alles schon versendet bzw. ohne Adresse
            versand.start(self.db, ids, 'Test')
        versand.start(self.db, ids, 'Test', resend=True)
        self.assertEqual(self.wait()['sent'], 2)

    def test_invalid_stored_address_is_skipped(self):
        ids = self.s.issue_dues(2026, [self.member()], 'Test')
        self.s.db.execute("UPDATE members SET email='kaputt\nBcc: x@example.org'"); self.s.db.commit()  # Altbestand ohne Prüfung
        send, skipped = versand.plan(self.s, ids)
        self.assertEqual((send, [x['reason'] for x in skipped]), ([], ['ungültige E-Mail-Adresse']))

    def test_qr_fields_stay_on_one_line(self):
        iid = self.s.issue_series(self.series([self.member()]), 'Test')[0]
        inv = rechnung_pdf.invoice_data(self.s, iid)
        inv['snap']['issuer'] = 'JU\nHamburg'
        self.assertEqual(len(rechnung_pdf.epc_payload(inv).split('\n')), 11)

    def test_rejected_recipient_is_logged(self):
        ids = self.s.issue_dues(2026, [self.member()], 'Test')
        self.smtp.reject.add('erika@example.org')
        job = versand.start(self.db, ids, 'Test')
        job = self.wait()
        self.assertEqual((job['sent'], job['failed']), (0, 1))
        self.assertIn('abgelehnt', job['errors'][0]['error'])
        self.assertIsNone(self.s.invoices()[0]['sent_at'])
        self.assertIn('abgelehnt', self.s.invoices()[0]['last_error'])

    def test_wrong_password_aborts(self):
        ids = self.s.issue_dues(2026, [self.member(), self.member(number='M2')], 'Test')
        geheimnis.save('kasse@example.org', 'falsch')
        with self.assertRaises(ValueError):
            versand.check(self.s.config())
        versand.start(self.db, ids, 'Test')
        job = self.wait()
        self.assertEqual((job['state'], job['sent'], job['failed']), ('aborted', 0, 1))
        self.assertIn('Anmeldung', job['errors'][0]['error'])

    def test_missing_password(self):
        geheimnis.forget('kasse@example.org')
        ids = self.s.issue_dues(2026, [self.member()], 'Test')
        with self.assertRaises(ValueError):
            versand.start(self.db, ids, 'Test')

    def test_cancel(self):
        self.s.save_settings({'mail_pause': '5'}, 'Test')
        ids = self.s.issue_dues(2026, [self.member(number=f'M{i}') for i in range(3)], 'Test')
        versand.start(self.db, ids, 'Test')
        for _ in range(100):
            if versand.status()['done'] >= 1:
                break
            time.sleep(0.05)
        versand.cancel()
        job = self.wait()
        self.assertEqual((job['state'], job['sent']), ('cancelled', 1))


class ReviewFindingTests(MailTests):
    """Fälle aus der unabhängigen Prüfung: keine Doppelversendung, sichere Abbrüche, saubere Auswahl."""

    def test_drop_after_data_is_unknown_not_resent(self):
        a, b = self.member(number='A', email='a@example.org'), self.member(number='B', email='b@example.org')
        ids = self.s.issue_dues(2026, [a, b], 'Test')
        self.smtp.drop_after_data.add('a@example.org')
        versand.start(self.db, ids, 'Test')
        job = self.wait()
        self.assertEqual((job['sent'], job['unknown'], job['failed']), (1, 1, 0))
        to_a = [m for m in self.smtp.messages if 'a@example.org' in m['to']]
        self.assertEqual(len(to_a), 1)  # genau einmal übertragen, nicht automatisch wiederholt
        inv = {i['recipient'] and i['mail_to']: i for i in self.s.invoices()}
        self.assertEqual(inv['a@example.org']['last_status'], 'unknown')
        _, skipped = versand.plan(self.s, ids)
        self.assertIn('Zustellung unklar – erst im Postfach prüfen', [x['reason'] for x in skipped])
        send, _ = versand.plan(self.s, ids, resend=True)
        self.assertEqual(len(send), 2)

    def test_paid_free_and_pilot_are_never_sent(self):
        paid, free, pilot = self.member(number='P'), self.member(number='F', dues='0'), self.member(number='L')
        ids = self.s.issue_dues(2026, [paid, free, pilot], 'Test')
        by_member = {i['member_id']: i['id'] for i in self.s.invoices()}
        raw = 'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-06-01;30;Test;Beitrag;r1\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, 'r1.csv'), [2], 'Test')
        bid = self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
        self.s.book(bid, [{'invoice_id': by_member[paid], 'amount': '30'}], 'Test')
        self.s.db.execute("UPDATE invoices SET snapshot=json_set(snapshot,'$.pilot',json('true')) WHERE id=?", (by_member[pilot],))
        self.s.db.commit()
        send, skipped = versand.plan(self.s, ids, resend=True)
        self.assertEqual(send, [])
        self.assertEqual(sorted(x['reason'] for x in skipped), ['Betrag 0 €', 'Pilotrechnung aus einer Vorversion', 'bereits bezahlt'])

    def test_umlaut_address_rejected_and_password_with_umlaut_works(self):
        with self.assertRaises(ValueError):
            self.member(number='U', email='jürgen@example.org')
        self.smtp.password = 'gehe§m-ä€'
        geheimnis.save('kasse@example.org', 'gehe§m-ä€')
        self.smtp.user = 'kasse@example.org'
        versand.check(self.s.config())

    def test_two_connection_failures_stop_batch(self):
        ids = self.s.issue_dues(2026, [self.member(number=f'M{i}') for i in range(4)], 'Test')
        self.smtp.stop()
        versand.start(self.db, ids, 'Test')
        job = self.wait()
        self.assertEqual((job['state'], job['done'], job['failed']), ('aborted', 2, 2))
        self.smtp = FakeSMTP(user='kasse@example.org', password='geheim').start()  # für tearDown

    def test_log_failure_aborts(self):
        ids = self.s.issue_dues(2026, [self.member(number='A'), self.member(number='B')], 'Test')
        original = versand._log
        versand._log = lambda *a, **k: False
        try:
            versand.start(self.db, ids, 'Test')
            job = self.wait()
        finally:
            versand._log = original
        self.assertEqual((job['state'], job['done']), ('aborted', 1))
        self.assertIn('nicht protokolliert', job['errors'][-1]['error'])


class FakeAblage:
    label = 'datei'

    def __init__(self):
        self.store, self.reads, self.deleted = {}, 0, 0

    def get_password(self, service, account):
        self.reads += 1
        return self.store.get((service, account))

    def set_password(self, service, account, password):
        self.store[(service, account)] = password

    def delete_password(self, service, account):
        self.deleted += 1
        self.store.pop((service, account), None)


class AblageTests(unittest.TestCase):
    """Passwort in der Ablage (auf dem Server: Datei aus JU_FINANZEN_GEHEIMNISSE, geprüft in test_server.py)."""
    def setUp(self):
        self.fake = FakeAblage()
        self.original = geheimnis._ablage
        geheimnis._ablage = lambda: self.fake
        geheimnis._cache.clear()

    def tearDown(self):
        geheimnis._ablage = self.original
        geheimnis._cache.clear()

    def test_read_once(self):
        self.fake.store[(geheimnis.SERVICE, 'k@example.org')] = 'pw'
        for _ in range(5):
            self.assertEqual(geheimnis.where('k@example.org'), 'datei')
        self.assertEqual(geheimnis.load('k@example.org'), 'pw')
        self.assertEqual(self.fake.reads, 1)

    def test_session_only_removes_old_entry(self):
        self.assertEqual(geheimnis.save('k@example.org', 'alt'), 'datei')
        self.assertEqual(geheimnis.save('k@example.org', 'neu', remember=False), 'sitzung')
        self.assertNotIn((geheimnis.SERVICE, 'k@example.org'), self.fake.store)
        self.assertEqual((geheimnis.load('k@example.org'), geheimnis.where('k@example.org')), ('neu', 'sitzung'))


class SecretTests(unittest.TestCase):
    def test_session_only_without_keychain(self):
        self.assertEqual(geheimnis.save('a@example.org', 'pw'), 'sitzung')
        self.assertEqual((geheimnis.load('a@example.org'), geheimnis.where('a@example.org')), ('pw', 'sitzung'))
        geheimnis.forget('a@example.org')
        self.assertIsNone(geheimnis.load('a@example.org'))
        for bad in ['', 'a\nb']:
            with self.assertRaises(ValueError):
                geheimnis.save('a@example.org', bad)


class LetterLayoutTests(unittest.TestCase):
    """Absenderzeile und Fußzeile: nie in den Informationsblock bzw. die Nachbarspalte, PLZ/Ort fehlt nie (Befund Lennert 10.11.2026)."""
    ADDRESS = 'Landesgeschäftsstelle\nTesthaus am Beispielpark\nBeispielweg 74\n22299 Hamburg'

    def test_sender_line_fits_address_field(self):
        import rechnung_pdf as r
        from reportlab.pdfbase.pdfmetrics import stringWidth
        for issuer, address in (('Junge Union Hamburg', self.ADDRESS), ('Junge Union Hamburg', 'Weg 1\n20000 Hamburg'), ('X' * 120, 'Weg 1\n20000 Hamburg'),
                                ('', '')):
            text, size = r.sender_line({'issuer': issuer, 'issuer_address': address})
            self.assertLessEqual(stringWidth(text, r.SANS, size), r.SENDER_WIDTH + 0.01)
            self.assertGreaterEqual(size, 6)
        text, size = r.sender_line({'issuer': 'Junge Union Hamburg', 'issuer_address': self.ADDRESS})
        self.assertTrue(text.startswith('Junge Union Hamburg') and text.endswith('Beispielweg 74 · 22299 Hamburg'), text)
        self.assertEqual(r.sender_line({'issuer': 'Junge Union Hamburg', 'issuer_address': 'Weg 1\n20000 Hamburg'}),
                         ('Junge Union Hamburg · Weg 1 · 20000 Hamburg', 7))

    def test_footer_keeps_zip_and_city(self):
        import rechnung_pdf as r
        lines = r.footer_address({'issuer_address': self.ADDRESS})
        self.assertEqual(len(lines), 3)
        self.assertIn('22299 Hamburg', lines[-1])
        self.assertEqual(r.footer_address({'issuer_address': 'Weg 1\n20000 Hamburg'}), ['Weg 1', '20000 Hamburg'])
        long = r.footer_address({'issuer_address': 'A\nB\nC\nD\nE\nSehr lange Straßenbezeichnung 123\n22299 Hamburg-Groß Flottbek'})
        self.assertEqual(long[-1], '22299 Hamburg-Groß Flottbek')
        self.assertEqual(r.footer_address({'issuer_address': 'A\nB\nC\nWeg 1\n20000 Hamburg'}), ['B', 'C', 'Weg 1 · 20000 Hamburg'])   # nur einmal zusammenfassen
        self.assertEqual([round(w / r.mm) for w in r.FOOTER_WIDTHS], [56, 51, 50])         # Spalte 3 endet am rechten Rand (190 mm)

    def test_long_issuer_keeps_street_and_city(self):
        import rechnung_pdf as r
        text, size = r.sender_line({'issuer': 'Junge Union Deutschlands Landesverband Hamburg e. V.', 'issuer_address': self.ADDRESS})
        self.assertTrue(text.endswith('Beispielweg 74 · 22299 Hamburg'), text)
        self.assertEqual(size, 6)

    def test_long_recipient_address_stays_in_window(self):
        import rechnung_pdf as r
        from reportlab.pdfbase.pdfmetrics import stringWidth
        line = r.fit('Wohngemeinschaft Hinterhaus, 3. Obergeschoss, Aufgang Nordseite', 10, r.SENDER_WIDTH)
        self.assertLessEqual(stringWidth(line, r.SANS, 10), r.SENDER_WIDTH + 0.01)
        self.assertTrue(line.endswith('…'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
