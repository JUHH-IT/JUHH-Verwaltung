"""Zahlungserinnerungen für überfällige Rechnungen – nur erfundene Testdaten, Versand nur an einen lokalen Testserver."""
import email
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from email import policy
from pathlib import Path

os.environ['JU_FINANZEN_OHNE_SCHLUESSELBUND'] = '1'  # Tests berühren nie den echten Schlüsselbund
sys.path.insert(0, str(Path(__file__).parent / 'tests'))

import geheimnis
import rechnung_pdf
import versand
from core import Store
from fake_smtp import FakeSMTP

IBAN = 'DE89 3704 0044 0532 0130 00'
ago = lambda days: (date.today() - timedelta(days=days)).isoformat()


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'test.db'
        self.s = Store(self.db)
        self.smtp = FakeSMTP(user='kasse@example.org', password='geheim').start()
        self.s.save_settings({'issuer': 'JU Test', 'issuer_address': 'Teststraße 1\n20000 Hamburg', 'iban': IBAN, 'age_basis': 'jan1',
                              'mail_from': 'kasse@example.org', 'smtp_user': 'kasse@example.org', 'smtp_host': '127.0.0.1',
                              'smtp_port': str(self.smtp.port), 'smtp_security': 'none', 'mail_pause': '0'}, 'Test')
        geheimnis.save('kasse@example.org', 'geheim')

    def tearDown(self):
        geheimnis.forget('kasse@example.org')
        self.smtp.stop()
        self.s.db.close()
        self.tmp.cleanup()

    def invoice(self, number='M1', email='erika@example.org', due_days_ago=30, name='Erika Musterfrau', mailed_days_ago=45):
        mid = self.s.member(dict(number=number, name=name, address='Musterweg 1\n20000 Hamburg', email=email, birth='2000-02-01',
                                 joined='2024-01-01', kv='5100'), 'Test')
        iid = self.s.issue_dues(2026, [mid], 'Test')[0]
        with self.s.db:
            self.s.db.execute('UPDATE invoices SET due=? WHERE id=?', (ago(due_days_ago), iid))   # simuliert ältere Rechnung
            if mailed_days_ago is not None:                                                     # Rechnung damals per E-Mail versendet
                self.s.db.execute("INSERT INTO mailings(invoice_id,time,recipient,subject,status,kind) VALUES(?,?,?,?,'sent','invoice')",
                                  (iid, ago(mailed_days_ago) + 'T10:00:00', email or '-', 'Rechnung'))
        return iid

    def bank(self, amount, ref, key, name='Erika Musterfrau'):
        raw = f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{ago(2)};{amount};{name};{ref};{key}\n'.encode()
        self.s.import_bank(self.s.preview_bank(raw, key + '.csv'), [2], 'Test')
        return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]

    def number(self, iid):
        return self.s.db.execute('SELECT number FROM invoices WHERE id=?', (iid,)).fetchone()[0]

    def wait(self):
        for _ in range(200):
            if versand.status()['state'] != 'running':
                return versand.status()
            time.sleep(0.05)
        self.fail('Versand endet nicht')

    def reasons(self, ids=None, resend=False):
        return {r['id']: r['reason'] for r in versand.reminders(self.s, ids, resend)}

    def test_eligibility(self):
        due = self.invoice('A')
        fresh = self.invoice('B', email='b@example.org', due_days_ago=5, name='Berta Beispiel')
        nomail = self.invoice('C', email='', name='Carla Clausen')
        paid = self.invoice('D', email='d@example.org', name='Dora Dietz')
        self.s.book(self.bank('30', self.number(paid), 'p1', name='Dora Dietz'), [{'invoice_id': paid, 'amount': '30'}], 'Test')
        in_bank = self.invoice('E', email='e@example.org', name='Emil Engel')
        self.bank('30', f'Beitrag {self.number(in_bank).lower().replace("-", " ")}', 'p2', name='Emil Engel')   # andere Schreibweise
        similar = self.invoice('F', email='f@example.org', name='Frieda Fuchs')
        self.bank('30', 'Mitgliedsbeitrag', 'p3', name='FRIEDA FUCHS')                          # ohne Nummer, Betrag + Name passen
        never = self.invoice('G', email='g@example.org', name='Gustav Gans', mailed_days_ago=None)
        late = self.invoice('H', email='h@example.org', name='Hanna Hahn', mailed_days_ago=3)   # Rechnung erst kürzlich versendet
        r = self.reasons()
        self.assertNotIn(paid, r)                                                                 # bezahlt: nicht in der Liste
        self.assertIsNone(r[due])
        self.assertIn('noch nicht lange genug überfällig', r[fresh])
        self.assertEqual(r[nomail], 'keine E-Mail-Adresse – Erinnerung per Post')
        self.assertIn('(Kontoauszug oder Kasse) – erst zuordnen', r[in_bank])
        self.assertIn('Betrag und Name passen', r[similar])
        self.assertEqual(r[never], 'Rechnung wurde nie per E-Mail versendet – erst die Rechnung senden')
        self.assertIsNone(self.reasons([never], resend=True)[never])                              # ausdrücklich übergehbar
        self.assertIn('noch nicht lange genug', r[late])
        self.assertEqual(self.reasons([paid])[paid], 'bereits bezahlt')
        self.assertIn('erst zuordnen', self.reasons([in_bank], resend=True)[in_bank])            # nie übergehbar

    def test_send_reminder_and_gap(self):
        iid = self.invoice()
        bank = self.bank('10', 'Teilzahlung', 'p1')
        self.s.book(bank, [{'invoice_id': iid, 'amount': '10'}], 'Test')
        job = versand.start(self.db, [iid], 'Test', kind='reminder')
        self.assertEqual((job['kind'], job['total']), ('reminder', 1))
        self.assertEqual(self.wait()['sent'], 1)
        msg = email.message_from_bytes(self.smtp.messages[0]['data'], policy=policy.default)
        self.assertEqual(msg['Subject'], f'Zahlungserinnerung zur Rechnung {self.number(iid)} – Junge Union Hamburg')
        text = msg.get_body(('plain',)).get_content()
        deadline = (date.today() + timedelta(days=14)).strftime('%d.%m.%Y')
        self.assertIn('Offen sind noch 20,00 €', text)
        pdf_inv = rechnung_pdf.invoice_data(self.s, iid)
        pdf_inv['paid'] = 1000
        self.assertIn('20.00', rechnung_pdf.epc_payload(pdf_inv))                                # GiroCode über den offenen Betrag
        self.assertIn(deadline, text)
        self.assertIn('Bitte zahlen bis', msg.get_body(('html',)).get_content())
        self.assertEqual(next(msg.iter_attachments()).get_filename(), self.number(iid) + '.pdf')       # Originalrechnung
        inv = [i for i in self.s.invoices() if i['id'] == iid][0]
        self.assertEqual((inv['reminders'], inv['sent_at'][:10], inv['reminder_status']), (1, ago(45), 'sent'))   # Rechnungsversand unverändert
        self.assertIn('letzte Erinnerung am', self.reasons([iid])[iid])
        with self.assertRaises(ValueError):
            versand.start(self.db, [iid], 'Test', kind='reminder')                                # Abstand eingehalten
        versand.start(self.db, [iid], 'Test', resend=True, kind='reminder')                     # ausdrücklich erneut
        self.wait()
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM mailings WHERE kind='reminder' AND status='sent'").fetchone()[0], 2)
        self.assertEqual(versand.reminders(self.s, [iid], resend=True)[0]['level'], 3)

    def test_unclear_counts_as_attempt(self):
        iid = self.invoice()
        with self.s.db:
            self.s.db.execute("INSERT INTO mailings(invoice_id,time,recipient,subject,status,kind) VALUES(?,?,?,?,'unknown','reminder')",
                              (iid, ago(1) + 'T10:00:00', 'erika@example.org', 'Erinnerung'))
            self.s.db.execute("INSERT INTO mailings(invoice_id,time,recipient,subject,status,kind) VALUES(?,?,?,?,'failed','reminder')",
                              (iid, ago(0) + 'T09:00:00', 'erika@example.org', 'Erinnerung'))
        r = versand.reminders(self.s, [iid])[0]
        self.assertIn('Zustellung der letzten Erinnerung unklar', r['reason'])                   # fehlgeschlagener Neuversuch ändert nichts
        r = versand.reminders(self.s, [iid], resend=True)[0]
        self.assertEqual((r['reason'], r['level']), (None, 2))

    def test_job_rechecks_before_each_mail(self):
        first, second = self.invoice('A'), self.invoice('B', email='b@example.org', name='Berta Beispiel')
        self.s.save_settings({'mail_pause': '1'}, 'Test')
        versand.start(self.db, [first, second], 'Test', kind='reminder')
        bank = self.bank('30', self.number(second), 'p9', name='Berta Beispiel')
        self.s.book(bank, [{'invoice_id': second, 'amount': '30'}], 'Test')                    # während des Versands bezahlt
        job = self.wait()
        self.assertEqual((job['sent'], [x['reason'] for x in job['skipped']]), (1, ['bereits bezahlt']))

    def test_invoice_send_unaffected(self):
        iid = self.invoice(mailed_days_ago=None)                                                # z. B. Rechnung persönlich übergeben
        versand.start(self.db, [iid], 'Test', resend=True, kind='reminder')
        self.wait()
        send, skipped = versand.plan(self.s, [iid])
        self.assertEqual(([s['id'] for s in send], skipped), ([iid], []))                    # Rechnung selbst nie versendet
        with self.assertRaises(ValueError):
            versand.start(self.db, [iid], 'Test', kind='mahnung')

    def test_templates_and_settings(self):
        cfg = self.s.config()
        inv = versand.sample_invoice(cfg, 'reminder')
        subject, body = versand.texts(cfg, inv)
        self.assertTrue(subject.startswith('Zahlungserinnerung'))
        self.assertIn('25,00 €', body)
        self.s.save_settings({'tpl_reminder_subject': '{stufe}. Erinnerung {nummer}'}, 'Test')
        iid = self.invoice()
        inv = rechnung_pdf.invoice_data(self.s, iid)
        inv['reminder'] = versand.reminder_terms(self.s.config(), 2)
        self.assertEqual(versand.texts(self.s.config(), inv)[0], f'2. Erinnerung {self.number(iid)}')
        for bad in ({'reminder_gap': '3'}, {'reminder_terms': '400'}, {'reminder_after': '0'}, {'tpl_reminder_body': '{mahngebuehr}'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.s.save_settings(bad, 'Test')
        versand.send_test(self.s.config(), inv)
        self.assertIn(b'[TEST]', self.smtp.messages[-1]['data'])


if __name__ == '__main__':
    unittest.main()
