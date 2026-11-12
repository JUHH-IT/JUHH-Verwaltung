import unittest, tempfile, sqlite3
from pathlib import Path
from core import Store,money

class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'test.db')
        self.s.save_settings({'issuer':'JU Test','issuer_address':'Teststraße 1\n20000 Hamburg','iban':'DE89 3704 0044 0532 0130 00','age_basis':'jan1','invoice_ready':'yes','tax_note':'Nur fiktive Testrechnung'},'Test')
    def tearDown(self): self.s.db.close();self.tmp.cleanup()
    def member(self,birth='2000-02-01',joined='2024-01-01',number='M1'):
        return self.s.member(dict(number=number,name='Testmitglied',address='Teststraße 1',email='',birth=birth,joined=joined,kv='5100'),'Test')
    def dues(self): return self.s.issue_dues(2026,[self.member()],'Test')[0]
    def bank(self,amount,day='2026-06-01',ref='x'):
        raw=f'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n{day};{amount};Test;Beitrag;{ref}\n'.encode()
        p=self.s.preview_bank(raw,ref+'.csv');self.s.import_bank(p,[2],'Test');return self.s.db.execute('SELECT MAX(id) FROM bank').fetchone()[0]
    def pay(self,bid,iid,amount): self.s.book(bid,[{'invoice_id':iid,'amount':amount}],'Test')
    def test_money(self):
        self.assertEqual(money('1.234,56'),123456);self.assertEqual(money('1.001'),100100)  # Tausenderpunkt
        for v in ['nan','inf','1.0001','2,001']:
            with self.assertRaises(ValueError): money(v)
    def test_dues_and_idempotent(self):
        m=self.member();ids=self.s.issue_dues(2026,[m],'Test');self.assertEqual(len(ids),1);self.assertEqual(self.s.invoices()[0]['total'],3000)
        self.assertEqual(self.s.issue_dues(2026,[m],'Test'),[])
    def test_age_boundaries(self):
        for birth,expected in [('2007-01-02',1000),('2007-01-01',2000),('2004-01-02',2000),('2004-01-01',3000)]:
            m={'birth':birth,'joined':'2024-01-01','left_on':None};self.assertEqual(self.s.dues_amount(m,2026),expected)
    def test_quarters(self):
        for joined,amount in [('2026-03-31',2250),('2026-04-01',1500),('2026-06-30',1500),('2026-07-01',750),('2026-09-30',750),('2026-10-01',0)]:
            self.assertEqual(self.s.dues_amount({'birth':'2000-01-01','joined':joined,'left_on':None},2026),amount)
    def test_unconfirmed_age_blocked(self):
        self.s.save_settings({'age_basis':''},'Test');m=self.member()
        with self.assertRaises(ValueError):self.s.issue_dues(2026,[m],'Test')
        self.assertEqual(self.s.invoices(),[])
    def test_zero_dues(self):
        iid=self.s.issue_dues(2026,[self.member(joined='2026-10-01')],'Test')[0]
        self.assertEqual(self.s.invoices()[0]['total'],0);self.assertEqual(self.s.rows('SELECT * FROM journals'),[])
    def test_partial_payment_and_budget(self):
        iid=self.dues();self.pay(self.bank('10',ref='a'),iid,'10');self.pay(self.bank('20',ref='b'),iid,'20')
        self.assertEqual(self.s.invoices()[0]['paid'],3000);self.assertEqual(self.s.budget_view(2026)[1]['share'],750)
        income=self.s.db.execute('SELECT SUM(credit-debit) FROM entries WHERE account="8100"').fetchone()[0]
        self.assertEqual(income,3000)
    def test_refund_reopens_and_budget(self):
        iid=self.dues();self.pay(self.bank('30'),iid,'30');self.pay(self.bank('-10',ref='refund'),iid,'-10')
        self.assertEqual(self.s.invoices()[0]['paid'],2000);self.assertEqual(self.s.budget_view(2026)[1]['share'],500)
    def test_overpayment_rollback(self):
        iid=self.dues();bid=self.bank('31')
        with self.assertRaises(ValueError): self.pay(bid,iid,'31')
        self.assertEqual(self.s.invoices()[0]['paid'],0);self.assertEqual(self.s.rows('SELECT state FROM bank')[0]['state'],'open')
    def test_split_cent_mismatch(self):
        bid=self.bank('-300')
        with self.assertRaises(ValueError):self.s.book(bid,[{'amount':'-299.99','account':'4210','kv':'5100','title':'H27'}],'Test')
        self.assertEqual(self.s.rows('SELECT * FROM journals'),[])
    def test_split_expense(self):
        bid=self.bank('-300')
        with self.assertRaises(ValueError) as cm: self.s.book(bid,[{'amount':'-300','account':'4210','kv':'LV','title':'Büro'}],'Test')   # kein Haushaltstitel
        self.assertIn('Haushaltstitel passt nicht',str(cm.exception))
        self.s.book(bid,[{'amount':a,'account':'4210','kv':kv,'title':'H27'} for a,kv in [('-120','LV'),('-100','5100'),('-80','5600')]],'Test')
        self.assertEqual(sum(b['spent'] for b in self.s.budget_view(2026)),30000)
        self.assertEqual(self.s.db.execute('SELECT SUM(debit-credit) FROM entries').fetchone()[0],0)
    def test_duplicate_import(self):
        self.bank('30');raw=b'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-06-01;30;Test;Beitrag;x\n'
        with self.assertRaises(ValueError): self.s.preview_bank(raw,'again.csv')
    def test_same_amount_distinct_reference(self):
        self.bank('30',ref='a');self.bank('30',ref='b');self.assertEqual(len(self.s.rows('SELECT * FROM bank')),2)
    def test_duplicate_reference_and_ambiguous(self):
        self.bank('30',ref='a')
        p=self.s.preview_bank(b'Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-06-02;30;Test;Beitrag;a\n','again.csv')
        self.assertTrue(p['rows'][0]['duplicate']);self.assertFalse(p['rows'][0]['selected'])
        p=self.s.preview_bank(b'Datum;Betrag;Name;Verwendungszweck\n2026-06-01;30;Test;Beitrag\n','ambiguous.csv')
        self.assertFalse(p['rows'][0]['selected']);self.assertTrue(p['rows'][0]['warning'])
    def test_wrong_year_not_allocated(self):
        iid=self.dues();self.pay(self.bank('30',day='2027-01-02'),iid,'30')
        self.assertEqual(self.s.budget_view(2026)[1]['share'],0);self.assertEqual(self.s.budget_view(2027)[1]['share'],0);self.assertEqual(len(self.s.snapshot(2026)['unallocated_dues']),1)
    def test_reversal(self):
        iid=self.dues();bid=self.bank('30');self.pay(bid,iid,'30');self.s.reverse_bank(bid,'Test')
        self.assertEqual(self.s.invoices()[0]['paid'],0);self.assertEqual(self.s.budget_view(2026)[1]['share'],0)
        with self.assertRaises(ValueError): self.s.reverse_bank(bid,'Test')
    def test_double_booking_blocked(self):
        iid=self.dues();bid=self.bank('15');self.pay(bid,iid,'15')
        self.assertEqual(self.s.rows('SELECT state FROM bank')[0]['state'],'booked')
        with self.assertRaises(ValueError):self.pay(bid,iid,'15')
        self.assertEqual(self.s.invoices()[0]['paid'],1500)
    def test_invoice_cancellation(self):
        iid=self.dues();self.s.reverse_invoice(iid,'Test');self.assertEqual(self.s.invoices()[0]['status'],'cancelled')
        self.assertEqual(self.s.db.execute('SELECT SUM(debit-credit) FROM entries WHERE account="1500"').fetchone()[0],0)
    def test_normal_invoice_and_snapshot(self):
        iid=self.s.issue_normal(dict(recipient='Test GmbH',address='Adresse 1',kv='LV',due='2026-12-01',lines=[dict(description='Teilnahme 01.11.2026',amount='100',account='8500')]),'Test')
        self.s.save_settings({'issuer':'Geändert'},'Test');self.assertIn('JU Test',self.s.invoices()[0]['snapshot'])
        import rechnung_pdf
        inv=rechnung_pdf.invoice_data(self.s,iid);pdf=rechnung_pdf.render([inv])
        self.assertTrue(pdf.startswith(b'%PDF'));self.assertEqual(rechnung_pdf.filename(inv),inv['number']+'.pdf')
    def test_backup_restore(self):
        iid=self.dues();self.pay(self.bank('30'),iid,'30')
        dest=sqlite3.connect(Path(self.tmp.name)/'backup.db');self.s.db.backup(dest);dest.close()
        other=Store(Path(self.tmp.name)/'backup.db');self.assertEqual(other.invoices(),self.s.invoices());self.assertEqual(other.budget_view(2026),self.s.budget_view(2026));other.db.close()

if __name__=='__main__':unittest.main(verbosity=2)
