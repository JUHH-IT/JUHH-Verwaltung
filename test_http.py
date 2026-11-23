import unittest, tempfile, threading, json, urllib.request, urllib.error, http.cookiejar, sqlite3, io, zipfile, time
from datetime import date
from pathlib import Path
import app
import test_server   # Passwort und schnelles Hashing der Tests (nicht als Name importiert, sonst liefen dessen Tests hier noch einmal)
import zugang
from core import Store

NAME='Schatzmeisterin Test'

def anmelden(request):
    """Anmeldung mit Passwort wie auf dem Server, danach die Passwortbestätigung für kritische Aktionen (5 Minuten); liefert das CSRF-Token."""
    csrf=json.load(request('/api/login',{'name':NAME,'password':test_server.PASSWORT}))['csrf']
    request('/api/bestaetigen',{'password':test_server.PASSWORT},csrf)
    return csrf

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        app.DATA=Path(cls.tmp.name);app.DB=app.DATA/'finanzen.sqlite3';(app.DATA/'Importe').mkdir()
        app.BACKUPS=app.DATA/'Sicherungen-Test'   # Sicherungen vor Importen bleiben im Testordner
        store=Store(app.DB)
        try: zugang.set_password(store.db,NAME,test_server.PASSWORT,create=True)   # erste Person: alle Rechte
        finally: store.db.close()
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    def test_end_to_end_security_and_backup(self):
        jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def request(path,data=None,csrf=None,origin=True):
            headers={}
            if data is not None:headers['Content-Type']='application/json'
            if origin:headers['Origin']=self.url
            if csrf:headers['X-CSRF-Token']=csrf
            req=urllib.request.Request(self.url+path,json.dumps(data).encode() if data is not None else None,headers)
            return client.open(req)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/data')
        self.assertEqual(cm.exception.code,401)
        for path in ('/api/op-liste.pdf','/api/op-listen.zip','/api/mitgliederkonten.csv?year=2026','/api/member-account?id=1'):   # 0.24: nur mit Sitzung
            with self.assertRaises(urllib.error.HTTPError) as cm: request(path)
            self.assertEqual(cm.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/setup',{'name':'test','password':'testpasswort12345'})
        self.assertEqual(cm.exception.code,401)
        csrf=anmelden(request)
        self.assertEqual(json.load(request('/api/status'))['user'],NAME)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/settings',{'issuer':'bad'})
        self.assertEqual(cm.exception.code,403)
        request('/api/settings',{'issuer':'Test','issuer_address':'Test 1','iban':'DE89 3704 0044 0532 0130 00','age_basis':'jan1'},csrf)
        m=json.load(request('/api/member',{'number':'T1','name':'Test Person','address':'Test 1','birth':'2000-01-01','joined':'2024-01-01','kv':'5100'},csrf))
        result=json.load(request('/api/dues',{'year':2026,'ids':[m['id']]},csrf))
        pdf=request('/api/invoice.pdf?id='+str(result['ids'][0])).read();self.assertTrue(pdf.startswith(b'%PDF'))
        data=json.load(request('/api/data?year=2026'));self.assertEqual(len(data['members']),1)
        # Rechnungen: Mitglied bearbeiten, Serienrechnung mit Vorschau (PDF + E-Mail), Sammel-PDF
        request('/api/settings',{'invoice_ready':'yes','tax_note':'Fiktiv','mail_from':'kasse@example.org'},csrf)
        request('/api/member-update',{'id':m['id'],'number':'T1','name':'Test Person','address':'Test 1','email':'t@example.org','birth':'2000-01-01','joined':'2024-01-01','kv':'5100','dues':'25'},csrf)
        series={'kv':'LV','due':'','member_ids':[m['id']],'recipients':[{'name':'Gast','address':'Weg 1','email':''}],'lines':[{'description':'Sommerfest','amount':'10','account':'8504'}]}
        pv=json.load(request('/api/series-preview',series,csrf));self.assertEqual(len(pv['recipients']),2)
        self.assertTrue(request(f"/api/preview.pdf?draft={pv['token']}&i=1").read().startswith(b'%PDF'))
        page=request(f"/api/mail-preview?draft={pv['token']}&i=0")
        self.assertIn("frame-ancestors 'self'",page.headers['Content-Security-Policy']);self.assertNotIn('script-src',page.headers['Content-Security-Policy'])
        self.assertIn('t@example.org',page.read().decode())
        ids=json.load(request('/api/series',{'token':pv['token']},csrf))['ids'];self.assertEqual(len(ids),2)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/series',{'token':pv['token']},csrf)
        self.assertEqual(cm.exception.code,400)  # Vorschau-Schlüssel nur einmal gültig
        busy=app.versand.busy;app.versand.busy=lambda:True
        try:
            self.assertTrue(json.load(request('/api/status'))['mail_busy'])
        finally: app.versand.busy=busy
        with self.assertRaises(urllib.error.HTTPError) as cm: request(f"/api/mail-preview?draft={pv['token']}&i=9")
        self.assertIn("frame-ancestors 'self'",cm.exception.headers['Content-Security-Policy'])
        both=request('/api/invoices.pdf?ids='+','.join(map(str,ids)));self.assertTrue(both.read().startswith(b'%PDF'));self.assertIn('inline',both.headers['Content-Disposition'])
        self.assertTrue(request(f"/api/preview.pdf?dues={m['id']}&year=2027").read().startswith(b'%PDF'))
        self.assertIn('Beitragsrechnung',request('/api/mail-preview?sample=dues').read().decode())
        self.assertIn('Zahlungserinnerung',request(f"/api/mail-preview?id={ids[0]}&reminder=1").read().decode())
        self.assertIn('Zahlungserinnerung',request('/api/mail-preview?sample=reminder').read().decode())
        self.assertEqual(set(json.load(request('/api/reminders'))),{'items','last_import','last_booked','open_incoming'})
        status=json.load(request('/api/mail-status'));self.assertEqual(status['job']['state'],'idle');self.assertIsNone(status['password'])
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/mail-send',{'ids':ids},csrf)
        self.assertEqual(cm.exception.code,400)  # ohne Passwort/Server kein Versand
        self.assertEqual(request('/').headers['X-Frame-Options'],'DENY')
        # Eingangsrechnung mit Beleg, Zahlungsvorschlag, GiroCode, Abgleich
        import base64
        bill={'creditor':'Druckerei Test','iban':'DE02120300000000202051','ext_number':'RE-1','issued':'2026-10-20','due':'','kv':'LV',
              'lines':[{'description':'Flyer','amount':'50','account':'4210','title':'H27'}],'file':{'name':'beleg.pdf','content':base64.b64encode(b'%PDF-1.4 x').decode()}}
        b=json.load(request('/api/bill',bill,csrf))
        self.assertTrue(request(f"/api/bill-file?id={b['id']}").read().startswith(b'%PDF'))
        t=json.load(request(f"/api/bill-transfers?ids={b['id']}"))[0];self.assertEqual((t['amount'],t['reference']),(5000,'RE-1'))
        qr=request(f"/api/bill-qr.svg?id={b['id']}");self.assertEqual(qr.headers['Content-Type'],'image/svg+xml');self.assertIn(b'<svg',qr.read())
        request('/api/bill-transferred',{'ids':[b['id']]},csrf)
        bad=dict(bill,ext_number='RE-2',file={'name':'x.pdf','content':base64.b64encode(b'MZ').decode()})
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/bill',bad,csrf)
        self.assertEqual(cm.exception.code,400)  # Inhalt passt nicht zur Endung
        csv_raw='Datum;Betrag;Name;Verwendungszweck\n27.10.2026;-50,00;Druckerei Test;RE-1\n'.encode()
        pv=json.load(request('/api/preview-bank',{'filename':'a.csv','content':base64.b64encode(csv_raw).decode()},csrf))
        request('/api/import-bank',{'token':pv['token'],'selected':[2]},csrf)
        props=json.load(request('/api/abgleich'));self.assertEqual([(p['kind'],p['id']) for p in props],[('bill',b['id'])])
        self.assertEqual(json.load(request('/api/abgleich',{'items':[{'bank_id':props[0]['bank_id'],'kind':'bill','id':b['id']}]},csrf)),{'count':1,'error':None})
        self.assertEqual([x['open'] for x in json.load(request('/api/data?year=2026'))['bills']],[0])
        # Freigabe § 7 Abs. 4 und Quartalsprüfung
        big=json.load(request('/api/bill',dict(bill,ext_number='RE-9',lines=[{'description':'Saal','amount':'750','account':'4210','title':'H27'}],file=None),csrf))
        with self.assertRaises(urllib.error.HTTPError) as cm: request(f"/api/bill-transfers?ids={big['id']}")
        self.assertIn('§ 7 Abs. 4',json.load(cm.exception)['error'])
        for role,name in (('chair','Vorsitz Test'),('treasurer','Schatz Test')):
            request('/api/bill-approve',{'id':big['id'],'role':role,'name':name,'approved_on':date.today().isoformat(),'how':'E-Mail'},csrf)
        self.assertEqual(json.load(request(f"/api/bill-transfers?ids={big['id']}"))[0]['amount'],75000)
        rep=json.load(request('/api/quarter-review?year=2026&quarter=4'));self.assertEqual(rep['label'],'Q4/2026')
        self.assertTrue(request('/api/quarter-review.pdf?year=2026&quarter=4').read().startswith(b'%PDF'))
        self.assertIn('reviews_due',json.load(request('/api/data?year=2026')))
        # Berichte (0.16): Monatsbericht, Kassenprüfer-Paket mit Beleg, Spenden, Budgetkonto
        month=date.today().isoformat()[:7]
        report=request('/api/monatsbericht.pdf?month='+month);self.assertTrue(report.read().startswith(b'%PDF'));self.assertIn('Monatsbericht-'+month,report.headers['Content-Disposition'])
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/monatsbericht.pdf?month=2999-01')
        self.assertIn('Zukunft',json.load(cm.exception)['error'])
        package=request('/api/pruefpaket.zip?year=2026');self.assertIn('attachment',package.headers['Content-Disposition'])
        with zipfile.ZipFile(io.BytesIO(package.read())) as z:
            names=z.namelist();self.assertIn('Kassenpruefung-2026/00-Uebersicht.pdf',names);self.assertTrue(any(n.startswith('Kassenpruefung-2026/Belege/E-') for n in names))
        self.assertTrue(any(a['action']=='Kassenprüfer-Paket erstellt' for a in json.load(request('/api/data?year=2026'))['audit']))
        self.assertIn('donations',json.load(request('/api/donations?year=2026')))
        self.assertIn('lines',json.load(request('/api/kv-ledger?kv=5100')))
        for bad in ('LV','XX'):
            with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kv-ledger?kv='+bad)
            self.assertEqual(cm.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kv-close',{'year':2026},csrf)
        self.assertEqual(cm.exception.code,400)  # erst nach dem 31.01.2027
        # E-Rechnung (0.17): einlesen, als Eingangsrechnung mit XML-Beleg erfassen, Sichtfassung, XML nur als Download
        import test_erechnung
        xml=test_erechnung.UBL.encode();einv={'name':'rechnung.xml','content':base64.b64encode(xml).decode()}
        d=json.load(request('/api/einvoice',{'file':einv},csrf));self.assertEqual((d['number'],d['total'],d['duplicates']),('2026-77',25000,[]))
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/einvoice',{'file':{'name':'x.pdf','content':base64.b64encode(b'%PDF-1.4').decode()}},csrf)
        self.assertEqual(cm.exception.code,400)
        e=json.load(request('/api/bill',{'creditor':d['creditor'],'iban':d['iban'],'ext_number':d['number'],'issued':d['issued'],'due':d['due'],'kv':'LV',
            'lines':[{'description':l['description'],'amount':str(l['amount']/100),'account':'4210','title':'H27'} for l in d['bill_lines']],'file':einv},csrf))
        xmlfile=request(f"/api/bill-file?id={e['id']}");self.assertIn('attachment',xmlfile.headers['Content-Disposition']);self.assertEqual(xmlfile.read(),xml)
        self.assertTrue(request(f"/api/bill-file?id={e['id']}&view=1").read().startswith(b'%PDF'))
        self.assertEqual(json.load(request('/api/einvoice',{'file':einv},csrf))['duplicates'],[e['number']])  # schon erfasst
        # Auslagenerstattung (0.17): Antragsformular und Erfassung mit Bestätigungen
        self.assertTrue(request(f"/api/refund-form.pdf?member={m['id']}").read().startswith(b'%PDF'))
        refund={'kind':'refund','member_id':m['id'],'creditor':'','iban':'DE02120300000000202051','issued':date.today().isoformat(),'due':'','kv':'5100',
                'submitted_on':date.today().isoformat(),'signed':True,'complete':True,'authorized':True,'no_other':True,
                'lines':[{'description':'Getränke','amount':'12,50','account':'4430','title':'H32'}],'file':{'name':'kassenbon.jpg','content':base64.b64encode(b'\xff\xd8\xff x').decode()}}
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/bill',dict(refund,signed=False),csrf)
        self.assertIn('unterschrieben',json.load(cm.exception)['error'])
        r=json.load(request('/api/bill',refund,csrf))
        row=[x for x in json.load(request('/api/data?year=2026'))['bills'] if x['id']==r['id']][0]
        self.assertEqual((row['creditor'],row['refund']['member']),('Test Person','Test Person'))
        # 0.22: Konten und Kontenblatt, Mitglieder-Import, Beitrag (8100) mit Mitglied, KV-Planwert und KV-Seite
        import base64 as b64
        konten={a['code']:a for a in json.load(request('/api/konten?year=2026'))};self.assertIn('2380',konten);self.assertEqual(konten['8100']['titles'],['H4'])
        sheet=json.load(request('/api/konto?code=1420&year=2026'));self.assertTrue(sheet['by_date'])
        kontocsv=request('/api/konto.csv?code=1420&year=2026&kv=LV');self.assertIn('Konto-1420-2026-LV.csv',kontocsv.headers['Content-Disposition']);self.assertIn('Saldo',kontocsv.read().decode('utf-8-sig'))
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/konto?code=9998&year=2026')
        self.assertEqual(cm.exception.code,400)
        liste='Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband\nT9;Ida;Import;Weg 9;22301;Hamburg;01.01.2001;01.01.2025;Nord\n'.encode()
        prev=json.load(request('/api/members-preview',{'filename':'liste.csv','content':b64.b64encode(liste).decode()},csrf))
        self.assertEqual((prev['counts']['new'],prev['rows'][0]['name']),(1,'Ida Import'));self.assertNotIn('values',prev['rows'][0])
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/members-import',{'token':'falsch','selected':[2]},csrf)
        self.assertEqual(cm.exception.code,400)
        res=json.load(request('/api/members-import',{'token':prev['token'],'selected':[2]},csrf));self.assertEqual(res['new'],1);self.assertTrue(res['backup'].startswith('vor-mitgliederimport'))
        ida=[x for x in json.load(request('/api/data?year=2026'))['members'] if x['number']=='T9'][0];self.assertEqual((ida['kv'],ida['zip']),('5600','22301'))
        raw='Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n2026-06-20;24,00;Ida Import;Beitrag 2026;ida1\n'.encode()
        pb=json.load(request('/api/preview-bank',{'filename':'ida.csv','content':b64.b64encode(raw).decode()},csrf));request('/api/import-bank',{'token':pb['token'],'selected':[2]},csrf)
        bid=[b for b in json.load(request('/api/data?year=2026'))['bank'] if b['name']=='Ida Import'][0]['id']
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/book',{'id':bid,'parts':[{'amount':'24','account':'8100','kv':'LV'}]},csrf)
        self.assertIn('Mitglied',json.load(cm.exception)['error'])
        request('/api/book',{'id':bid,'parts':[{'amount':'24','account':'8100','kv':'LV','members':[{'member_id':ida['id']}]}]},csrf)
        self.assertEqual(json.load(request(f"/api/member-dues?id={ida['id']}"))[0]['amount'],2400)
        self.assertEqual(json.load(request('/api/beitraege-offen?year=2026')),[])
        self.assertEqual(len(json.load(request('/api/beitraege-offen?year=2026&all=1'))),1)
        self.assertEqual(json.load(request('/api/beitraege-vorschlaege',{'year':2026},csrf))['count'],0)
        # 0.24: Mitgliederbuchhaltung – Beitragskonto, Soll festlegen, Rückstände, CSV/PDF
        acc=json.load(request(f"/api/member-account?id={ida['id']}"));y26=[r for r in acc['years'] if r['year']==2026][0]
        self.assertEqual(y26['paid'],2400)
        request('/api/member-target',{'member_id':ida['id'],'year':2026,'amount':'30','reason':'Testfestlegung'},csrf)
        acc=json.load(request(f"/api/member-account?id={ida['id']}"));y26=[r for r in acc['years'] if r['year']==2026][0]
        self.assertEqual((y26['soll'],y26['source'],y26['open']),(3000,'set',600))
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/member-target',{'member_id':ida['id'],'year':2026,'amount':'0','reason':'ohne CSRF'})
        self.assertEqual(cm.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/member-target',{'member_id':ida['id'],'year':2026,'amount':'30','reason':''},csrf)
        self.assertIn('Grund',json.load(cm.exception)['error'])
        op=json.load(request('/api/op-liste?kv=5600'));self.assertIn(ida['id'],[x['id'] for x in op['members']])   # 0.29: OP-Listen je Kreisverband
        for bad in ('/api/op-liste?kv=9999','/api/op-liste?ov=x','/api/op-liste.pdf?kv=9999'):
            with self.assertRaises(urllib.error.HTTPError) as cm: request(bad)
            self.assertEqual(cm.exception.code,400)
        pdf=request('/api/op-liste.pdf?kv=5600');self.assertTrue(pdf.read().startswith(b'%PDF'));self.assertIn('OP-Liste-5600-',pdf.headers['Content-Disposition'])
        op_csv=request('/api/op-liste.csv?kv=5600');self.assertIn('attachment',op_csv.headers['Content-Disposition']);self.assertIn('T9;Import;Ida;',op_csv.read().decode('utf-8-sig'))
        packed=request('/api/op-listen.zip');self.assertEqual(packed.headers['Content-Type'],'application/zip');self.assertTrue(packed.read().startswith(b'PK'))
        pdf=request(f"/api/member-account.pdf?id={ida['id']}");self.assertTrue(pdf.read().startswith(b'%PDF'));self.assertIn('Beitragskonto-T9.pdf',pdf.headers['Content-Disposition'])
        konten=request('/api/mitgliederkonten.csv?year=2026&kv=5600');self.assertIn('Mitgliederkonten-2026-5600.csv',konten.headers['Content-Disposition'])
        self.assertIn('T9;Import;Ida;',konten.read().decode('utf-8-sig'))
        request('/api/member-target',{'member_id':ida['id'],'year':2026,'remove':'false','amount':'30','reason':'kein Entfernen per Text'},csrf)   # nur true entfernt
        self.assertEqual([r for r in json.load(request(f"/api/member-account?id={ida['id']}"))['years'] if r['year']==2026][0]['source'],'set')
        request('/api/member-target',{'member_id':ida['id'],'year':2026,'remove':True},csrf)
        self.assertNotEqual([r for r in json.load(request(f"/api/member-account?id={ida['id']}"))['years'] if r['year']==2026][0]['source'],'set')
        self.assertIn('dues_book',json.load(request('/api/data?year=2026')))
        # 0.25: Kasse – Anfangsbestand, Kassenbeleg mit Datei, Kassenbuch, PDF/CSV, ungültig
        from datetime import date as _d
        y=_d.today().year;start=f'{y}-01-01'
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kasse-beleg',{'kind':'out','amount':'5','text':'x'},csrf)
        self.assertIn('Anfangsbestand',json.load(cm.exception)['error'])
        request('/api/kasse-anfang',{'day':start,'amount':'100'},csrf)
        pdf_bytes=b'%PDF-1.4 Testbeleg'
        k=json.load(request('/api/kasse-beleg',{'kind':'out','amount':'12,50','day':_d.today().isoformat(),'text':'Porto','file':{'name':'bon.pdf','content':b64.b64encode(pdf_bytes).decode()}},csrf))
        self.assertEqual(k['number'],f'K-{y}-0001')
        self.assertEqual(request(f"/api/kasse-datei?id={k['id']}").read(),pdf_bytes)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kasse-beleg',{'kind':'out','amount':'5','text':'x','file':{'name':'a.xml','content':b64.b64encode(b'<x/>').decode()}},csrf)
        self.assertEqual(cm.exception.code,400)
        data=json.load(request(f'/api/data?year={y}'))
        self.assertEqual((data['cash']['kassen'][0]['balance'],data['cash']['open']),(8750,1))
        self.assertEqual([b['ledger'] for b in data['bank'] if b['id']==k['id']],['cash'])
        book=json.load(request(f'/api/kasse?year={y}'));self.assertEqual(book['end'],8750)
        self.assertTrue(request(f'/api/kasse.pdf?year={y}').read().startswith(b'%PDF'))
        self.assertIn(f'K-{y}-0001',request(f'/api/kasse.csv?year={y}').read().decode('utf-8-sig'))
        request('/api/kasse-ungueltig',{'id':k['id'],'reason':'Testfall'},csrf)
        self.assertEqual(json.load(request(f'/api/data?year={y}'))['cash']['kassen'][0]['balance'],10000)
        # 0.59: weitere Kasse (PayPal) über HTTP – eigenes Konto, eigene Belegnummern, eigenes Kassenbuch (kasse=Kennung)
        konto=json.load(request(f'/api/data?year={y}'))['cash']['next_account']
        pp=json.load(request('/api/kasse-neu',{'name':'PayPal','account':konto,'prefix':'pp','day':start,'amount':'40'},csrf))['id']
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kasse-neu',{'name':'Zweite','account':konto,'prefix':'ZW','day':start,'amount':'0'},csrf)
        self.assertIn('gibt es schon',json.load(cm.exception)['error'])
        k=json.load(request('/api/kasse-beleg',{'kasse':pp,'kind':'out','amount':'1,50','day':_d.today().isoformat(),'text':'Gebühr'},csrf))
        self.assertEqual(k['number'],f'PP-{y}-0001')
        request('/api/book',{'id':k['id'],'parts':[{'amount':'-1,50','account':'4225','kv':'LV'}]},csrf)
        data=json.load(request(f'/api/data?year={y}'))
        self.assertEqual([(x['name'],x['account'],x['balance']) for x in data['cash']['kassen']],[('Bargeldkasse','1400',10000),('PayPal',konto,3850)])
        self.assertEqual([b['kasse'] for b in data['bank'] if b['id']==k['id']],[pp])
        book=json.load(request(f'/api/kasse?year={y}&kasse={pp}'));self.assertEqual((book['end'],book['kasse']['name']),(3850,'PayPal'))
        self.assertEqual(json.load(request(f'/api/kasse?year={y}'))['end'],10000)                              # ohne Angabe: Bargeldkasse wie bisher
        antwort=request(f'/api/kasse.csv?year={y}&kasse={pp}')
        self.assertIn(f'Kassenbuch-PayPal-{konto}-{y}.csv',antwort.headers['Content-Disposition']);self.assertIn(f'PP-{y}-0001',antwort.read().decode('utf-8-sig'))
        self.assertTrue(request(f'/api/kasse.pdf?year={y}&kasse={pp}').read().startswith(b'%PDF'))
        with self.assertRaises(urllib.error.HTTPError) as cm: request(f'/api/kasse?year={y}&kasse=99')
        self.assertEqual(cm.exception.code,400)
        # K1 (Prüfung 0.59): von außen zählt nur die Kennung einer vorhandenen Kasse – Objekt, Liste, unbekannte Zahl, Text: „Kasse nicht gefunden“, nichts geschrieben
        def stand():
            db=sqlite3.connect(app.DB)
            try: return ([db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('journals','entries','bank','kassen')],db.execute("SELECT value FROM settings WHERE key='cash_start'").fetchall(),
                         sorted(str(f.relative_to(app.DATA.parent)) for f in app.DATA.parent.rglob('*.pdf')))
            finally: db.close()
        vorher=stand();heute=_d.today().isoformat()
        fremd={'id':0,'name':'x','account':'1420','prefix':'../../ausbruch','start':'2000-01-01'}
        for wert in (fremd,{**fremd,'id':pp,'prefix':'R'},{'id':pp},[pp],[],{},99,-1,True,1.5,'abc',' 0 ','1;x'):
            for path,payload in (('/api/kasse-anfang',{'day':start,'amount':'5000','kasse':wert}),
                                 ('/api/kasse-beleg',{'kind':'in','amount':'1','day':heute,'text':'x','kasse':wert,'file':{'name':'a.pdf','content':b64.b64encode(pdf_bytes).decode()}}),
                                 ('/api/kasse-beleg',{'kind':'withdraw','amount':'1','day':'2001-05-05','kasse':wert})):
                with self.assertRaises(urllib.error.HTTPError) as cm: request(path,payload,csrf)
                self.assertEqual((cm.exception.code,json.load(cm.exception)['error']),(400,'Kasse nicht gefunden.'),(path,wert))
        for wert in ('99','-1','abc','1.5','0x1','%200','%7B%22id%22%3A0%7D'):
            for path in ('/api/kasse','/api/kasse.pdf','/api/kasse.csv'):
                with self.assertRaises(urllib.error.HTTPError) as cm: request(f'{path}?year={y}&kasse={wert}')
                self.assertEqual((cm.exception.code,json.load(cm.exception)['error']),(400,'Kasse nicht gefunden.'),(path,wert))
        probe=Store(app.DB)   # der Belegspeicher bildet nie einen Pfad aus einer ungeprüften Nummer
        try:
            for nummer,datei,endung in (('../../ausbruch-2026-0001','../../ausbruch-2026-0001.pdf','.pdf'),(f'K-{y}-0009','../x.pdf','.pdf'),(f'K-{y}-0009',f'K-{y}-0009.exe','.exe')):
                with self.assertRaises(ValueError): app.save_receipt(probe,'bank',k['id'],nummer,datei,endung,pdf_bytes)
        finally: probe.db.close()
        self.assertEqual(stand(),vorher)
        self.assertEqual(json.load(request(f'/api/kasse?year={y}&kasse={pp}'))['kasse']['name'],'PayPal')   # die Kennung als Text gilt weiter
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kasse-datei?id=999999')
        self.assertEqual(cm.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as cm: request(f'/api/kasse-datei?id={bid}')            # Kontoumsatz, kein Kassenbeleg
        self.assertEqual(cm.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kasse-beleg',{'kind':'out','amount':'1','text':'x','file':{'name':'a.pdf','content':b64.b64encode(b'kein pdf').decode()}},csrf)
        self.assertIn('passt nicht',json.load(cm.exception)['error'])
        for path,payload in (('/api/kasse-beleg',{'kind':'in','amount':'1','text':'x'}),('/api/kasse-anfang',{'day':start,'amount':'1'}),('/api/kasse-ungueltig',{'id':k['id'],'reason':'x'})):
            with self.assertRaises(urllib.error.HTTPError) as cm: request(path,payload)                       # ohne CSRF
            self.assertEqual(cm.exception.code,403)
        request('/api/kv-plan',{'year':2026,'kv':'5600','amount':'500'},csrf)
        nord=json.load(request('/api/kv-detail?kv=5600&year=2026'));self.assertEqual((nord['plan'],nord['paid'],nord['payments'][0]['number']),(50000,2400,'T9'))
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kv-plan',{'year':2026,'kv':'LV','amount':'1'},csrf)
        self.assertEqual(cm.exception.code,400)
        # KV-Budgets (0.23): Jahreswerte, PDF für den Kreisvorsitz, Bezeichnung, Abgleich mit der Masterdatei
        self.assertTrue(json.load(request('/api/kv-values',{'year':2026,'kv':'5600','ffk':'120,50'},csrf))['changed'])
        nord=json.load(request('/api/kv-detail?kv=5600&year=2026'));self.assertEqual((nord['ffk'],nord['set']['ffk']),(12050,12050))
        self.assertEqual(nord['statement']['lines'][1]['kind'],'ffk')
        pdf=request('/api/kv.pdf?kv=5600&year=2026');self.assertTrue(pdf.read().startswith(b'%PDF'));self.assertIn('KV-Budget-5600-2026.pdf',pdf.headers['Content-Disposition'])
        for bad in ('/api/kv.pdf?kv=LV&year=2026','/api/kv.pdf?kv=5600&year=x'):
            with self.assertRaises(urllib.error.HTTPError) as cm: request(bad)
            self.assertEqual(cm.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kv-values',{'year':2026,'kv':'5600','ffk':'-1'},csrf)
        self.assertEqual(cm.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kv-label',{'journal':999999,'label':'x'},csrf)
        self.assertEqual(cm.exception.code,400)
        import test_kvabgleich
        preview=json.load(request('/api/preview-kv-master',{'filename':'Master.xlsx','content':base64.b64encode(test_kvabgleich.workbook()).decode()},csrf))
        self.assertIn('token',preview);self.assertTrue(any(a['code']=='5100' for a in preview['areas']))
        done=json.load(request('/api/import-kv-master',{'token':preview['token']},csrf));self.assertIn('vor-kv-abgleich',done['backup'])
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/import-kv-master',{'token':preview['token']},csrf)
        self.assertIn('abgelaufen',json.load(cm.exception)['error'])
        with zipfile.ZipFile(io.BytesIO(request('/api/backup').read())) as z:
            self.assertTrue(any(n.startswith('Daten/Belege/E-') for n in z.namelist()))
        with zipfile.ZipFile(io.BytesIO(request('/api/backup').read())) as z:
            saved=Path(self.tmp.name)/'restored.sqlite3';saved.write_bytes(z.read('Daten/finanzen.sqlite3'))
            conn=sqlite3.connect(saved);self.assertEqual(conn.execute('SELECT count(*) FROM invoices').fetchone()[0],3);conn.close()
        request('/api/logout',{},csrf)
        with self.assertRaises(urllib.error.HTTPError) as cm:request('/api/data')
        self.assertEqual(cm.exception.code,401)
    def test_rpj_endpoints(self):
        # 0.26: RPJ-Mittel – Jahr, Projekt, Mittelabruf, Import mit Vorschau-Schlüssel, PDF/CSV, Zuordnung; ohne Sitzung gesperrt
        import base64
        jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def request(path,data=None,csrf=None):
            headers={'Origin':self.url,**({'Content-Type':'application/json'} if data is not None else {}),**({'X-CSRF-Token':csrf} if csrf else {})}
            return client.open(urllib.request.Request(self.url+path,json.dumps(data).encode() if data is not None else None,headers))
        y=date.today().year
        for path in (f'/api/rpj?year={y}',f'/api/rpj.pdf?year={y}','/api/rpj-offen'):
            with self.assertRaises(urllib.error.HTTPError) as cm: request(path)
            self.assertEqual(cm.exception.code,401)
        csrf=anmelden(request)
        request('/api/rpj-jahr',{'year':y,'contingent':'5000','notice_on':f'{y}-01-15'},csrf)
        pid=json.load(request('/api/rpj-projekt',{'year':y,'no':'7','name':'HTTP-Testprojekt','quarter':'2','plan':{'a':'900','i':'100'}},csrf))['id']
        rid=json.load(request('/api/rpj-abruf',{'year':y,'quarter':'1','amount':'800'},csrf))['id']
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/rpj-abruf',{'year':y,'quarter':'2','amount':'4200,01'},csrf)
        self.assertEqual(cm.exception.code,400)                                       # über dem Kontingent
        view=json.load(request(f'/api/rpj?year={y}'))
        self.assertEqual((view['cards']['applied'],view['cards']['requested'],view['projects'][0]['id']),(80000,80000,pid))
        self.assertEqual(json.load(request(f'/api/rpj-projekt?id={pid}'))['project']['rpj_plan'],80000)
        for path,start in ((f'/api/rpj.pdf?year={y}',b'%PDF'),(f'/api/rpj-projekt.pdf?id={pid}',b'%PDF')):
            r=request(path);self.assertTrue(r.read().startswith(start));self.assertIn('inline',r.headers['Content-Disposition'])
        for path,name in ((f'/api/rpj.csv?year={y}',f'RPJ-{y}.csv'),(f'/api/rpj-buchungen.csv?year={y}',f'RPJ-Buchungen-{y}.csv'),
                          (f'/api/rpj-buchungen.csv?project={pid}',f'RPJ-{y}-Projekt-07-Buchungen.csv'),('/api/rpj-vorlage.csv','RPJ-Haushalt-Vorlage.csv')):
            r=request(path);self.assertTrue(r.read().startswith(b'\xef\xbb\xbf'));self.assertIn(f'attachment; filename="{name}"',r.headers['Content-Disposition'])
        raw=f'Jahr;Nr.;Veranstaltung;Quartal;a) Raummiete;i) Teilnahmebeiträge\n{y};8;HTTP-Import;Q3;500;\n'.encode()
        prev=json.load(request('/api/rpj-import-vorschau',{'filename':'rpj.csv','content':base64.b64encode(raw).decode()},csrf))
        self.assertEqual(prev['counts']['new'],1)
        done=json.load(request('/api/rpj-import',{'token':prev['token'],'selected':[2]},csrf))
        self.assertEqual(done['new'],1);self.assertTrue(done['backup'])
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/rpj-import',{'token':prev['token'],'selected':[2]},csrf)
        self.assertEqual(cm.exception.code,400)                                       # Vorschau-Schlüssel nur einmal
        self.assertEqual(json.load(request('/api/rpj-offen?all=1')),[])
        request('/api/rpj-abruf-loeschen',{'id':rid},csrf)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/rpj-zuordnen',{'entry_id':999999,'items':[{'target':f'p:{pid}','category':'a'}]},csrf)
        self.assertEqual(cm.exception.code,400)
        self.assertEqual(len(json.load(request('/api/data?year='+str(y)))['rpj']['projects']),2)
    def test_map_and_address_endpoints(self):
        # 0.32: Karte und Adressprüfung – ohne Sitzung gesperrt, Abruf nur mit CSRF, Daten erfunden (kein Netz: fetch_page ersetzt)
        import adressen, test_karte
        jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def request(path,data=None,csrf=None):
            headers={'Origin':self.url,**({'Content-Type':'application/json'} if data is not None else {}),**({'X-CSRF-Token':csrf} if csrf else {})}
            return client.open(urllib.request.Request(self.url+path,json.dumps(data).encode() if data is not None else None,headers))
        for path in ('/api/karte','/api/karte-gebiete','/api/adressen-status'):
            with self.assertRaises(urllib.error.HTTPError) as cm: request(path)
            self.assertEqual(cm.exception.code,401)
        csrf=anmelden(request)
        for path in ('/api/adresse-pruefen','/api/adressen-laden','/api/strassen-suche'):
            with self.assertRaises(urllib.error.HTTPError) as cm: request(path,{'street':'Testallee 1'})
            self.assertEqual(cm.exception.code,403)                                   # ohne CSRF-Schlüssel
        adressen.path(app.DATA).unlink(missing_ok=True)
        self.assertEqual(json.load(request('/api/karte'))['available'],False)
        self.assertEqual(json.load(request('/api/adresse-pruefen',{'street':'Testallee 1','zip':'20001','city':'Hamburg'},csrf))['status'],'keine_daten')
        self.assertEqual(json.load(request('/api/strassen-suche',{'q':'testallee'},csrf)),[])
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/strassen?q=testallee')
        self.assertEqual(cm.exception.code,404)                                       # Suchtext nie in der Adresszeile
        self.assertFalse(json.load(request('/api/data?year=2026'))['address_data'])
        data=test_karte.fixtures();old=(adressen.fetch_page,adressen.MIN_ROWS)
        adressen.fetch_page=lambda offset,version='': {'numberMatched':len(data),'features':data[offset:offset+adressen.PAGE]};adressen.MIN_ROWS=5
        self.addCleanup(lambda: (setattr(adressen,'fetch_page',old[0]),setattr(adressen,'MIN_ROWS',old[1]),adressen._job.update(state='idle',error=''),adressen._job.pop('finished',None)))
        state=json.load(request('/api/adressen-laden',{},csrf))
        for _ in range(100):
            if state['job']['state']!='running': break
            time.sleep(0.05);state=json.load(request('/api/adressen-status'))
        self.assertEqual((state['job']['state'],state['available'],state['adressen']),('done',True,len(data)))
        self.assertTrue(json.load(request('/api/data?year=2026'))['address_data'])
        m=json.load(request('/api/member',{'number':'KA-HTTP-1','first_name':'Karla','last_name':'Karte','street':'Beispielstr. 3','zip':'20002','city':'Hamburg',
                                           'birth':'2001-01-01','joined':'2024-01-01','kv':'5100'},csrf))
        view=json.load(request('/api/karte'))
        point=next(p for p in view['points'] if p['id']==m['id'])
        self.assertEqual((point['area'],point['other'],point['exact']),('5300',True,True))
        self.assertNotIn('Karla',json.dumps(view))                                    # nur Kennung und Lage, keine Namen
        r=json.load(request('/api/adresse-pruefen',{'street':'Testalle 3','zip':'20001','city':'Hamburg'},csrf))
        self.assertEqual((r['status'],r['suggestions'][0]['street']),('aehnlich','Testallee'))
        self.assertEqual(json.load(request('/api/adresse-pruefen',{'street':['x'],'zip':5,'city':None},csrf))['status'],'leer')   # nur Text zählt
        self.assertEqual(json.load(request('/api/strassen-suche',{'q':'doppel'},csrf))[0]['street'],'Doppelweg')
        self.assertEqual(json.load(request('/api/strassen-suche',{'q':['x']},csrf)),[])
        again=json.load(request('/api/adressen-laden',{},csrf))                       # eben erst geladen: kein zweiter Abruf, kein zweiter Protokolleintrag
        self.assertEqual(again['job']['state'],'done')
        areas=json.load(request('/api/karte-gebiete'))
        self.assertEqual(len(areas['bezirke']),7)
        with sqlite3.connect(app.DB) as db:
            audit=[r[0] for r in db.execute("SELECT detail FROM audit WHERE action='Adressdaten: Abruf gestartet'")]
        self.assertEqual(len(audit),1);self.assertNotIn('Testallee',audit[0])
        self.assertEqual(json.load(request('/api/settings',{'map_tiles':'no'},csrf))['ok'],True)
        self.assertEqual(json.load(request('/api/data?year=2026'))['settings']['map_tiles'],'no')
        request('/api/settings',{'map_tiles':'yes'},csrf)
        adressen.path(app.DATA).unlink(missing_ok=True)

    def test_klaerfall_endpoints(self):
        # 0.31: Klärfälle – beim Verbuchen melden, Liste, Notiz/Rückfrage, klären; Schalter in den Einstellungen; ohne Sitzung gesperrt
        import base64
        jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def request(path,data=None,csrf=None):
            headers={'Origin':self.url,**({'Content-Type':'application/json'} if data is not None else {}),**({'X-CSRF-Token':csrf} if csrf else {})}
            return client.open(urllib.request.Request(self.url+path,json.dumps(data).encode() if data is not None else None,headers))
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/klaerfaelle')
        self.assertEqual(cm.exception.code,401)
        csrf=anmelden(request)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/klaerfall-klaeren',{'id':1,'no_dues':True})
        self.assertEqual(cm.exception.code,403)                                       # ohne CSRF-Schlüssel
        m=json.load(request('/api/member',{'number':'K-HTTP-1','first_name':'Kira','last_name':'Klar','street':'Testweg 1','zip':'20000','city':'Hamburg',
                                           'birth':'2001-01-01','joined':'2024-01-01','kv':'5300'},csrf))
        raw='Datum;Betrag;Name;Verwendungszweck;Bankreferenz\n05.05.2026;30,00;Unklarer Zahler;Beitrag;kf-http-1\n06.05.2026;20,00;Noch Unklar;Beitrag;kf-http-2\n'.encode()
        pv=json.load(request('/api/preview-bank',{'filename':'kf.csv','content':base64.b64encode(raw).decode()},csrf))
        request('/api/import-bank',{'token':pv['token'],'selected':[2,3]},csrf)
        bank=[b for b in json.load(request('/api/data?year=2026'))['bank'] if b['name'] in ('Unklarer Zahler','Noch Unklar')]
        for b in bank:
            request('/api/book',{'id':b['id'],'parts':[{'amount':str(b['amount']/100),'account':'8100','kv':'LV','members':[{'case':{'name':b['name'],'reason':'unbekannt'}}]}]},csrf)
        view=json.load(request('/api/klaerfaelle'))
        mine=[c for c in view['items'] if c['name'] in ('Unklarer Zahler','Noch Unklar')]
        self.assertEqual(sorted((c['amount'],c['status'],c['booked']) for c in mine),[(2000,'offen',False),(3000,'offen',False)])
        self.assertGreaterEqual(json.load(request('/api/data?year=2026'))['cases']['unassigned'],2)
        a,b=sorted(mine,key=lambda c:c['amount'])
        request('/api/klaerfall-notiz',{'id':a['id'],'status':'rueckfrage','kv':'5300','note':'KV gefragt'},csrf)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/klaerfall-notiz',{'id':a['id'],'status':'geklaert'},csrf)
        self.assertEqual(cm.exception.code,400)
        self.assertEqual(json.load(request('/api/klaerfall-klaeren',{'id':a['id'],'member_id':m['id']},csrf))['result'],'mitglied')
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/klaerfall-klaeren',{'id':a['id'],'member_id':m['id']},csrf)
        self.assertEqual(cm.exception.code,400)                                       # schon erledigt
        self.assertEqual(json.load(request('/api/klaerfall-klaeren',{'id':b['id'],'no_dues':True,'note':'Spende'},csrf))['result'],'kein_beitrag')
        entry=next(e for e in json.load(request('/api/beitraege-offen?year=2026')) if e['payer']=='Unklarer Zahler')
        self.assertEqual(entry['returned'],[{'name':'Unklarer Zahler','note':'Spende'}])
        self.assertTrue(json.load(request('/api/klaerfall-melden',{'entry_id':entry['id']},csrf))['id'])
        self.assertEqual(json.load(request('/api/klaerfall-melden',{'all':True,'year':2026},csrf))['count']>=0,True)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/klaerfall-melden',{'entry_id':entry['id']},csrf)
        self.assertEqual(cm.exception.code,400)                                       # schon gemeldet
        for bad in ({'entry_id':True},{'entry_id':1.9},{'entry_id':[1]},{},{'all':True,'year':'x'}):
            with self.assertRaises(urllib.error.HTTPError,msg=repr(bad)) as cm: request('/api/klaerfall-melden',bad,csrf)
            self.assertEqual(cm.exception.code,400)
        # Schalter „Beitragsanteil automatisch“: nur mit Jahr, Stand in den Daten
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/settings',{'kv_share_auto':'yes','kv_share_auto_from':''},csrf)
        self.assertEqual(cm.exception.code,400)
        request('/api/settings',{'kv_share_auto':'yes','kv_share_auto_from':str(date.today().year+1)},csrf)
        self.assertEqual(json.load(request('/api/data?year=2026'))['settings']['kv_share_auto'],'yes')
        request('/api/settings',{'kv_share_auto':'no'},csrf)
        self.assertIn('last_backup',json.load(request('/api/data?year=2026')))
    def test_gliederung_endpoints(self):
        # 0.27: Ortsverbände, Nummern der Mitgliederverwaltung, Gliederung-Import, kennwortgeschützte Mitgliederliste – erfundene Daten
        import base64, test_gliederung as t
        jar=http.cookiejar.CookieJar();client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def request(path,data=None,csrf=None):
            headers={'Origin':self.url,**({'Content-Type':'application/json'} if data is not None else {}),**({'X-CSRF-Token':csrf} if csrf else {})}
            return client.open(urllib.request.Request(self.url+path,json.dumps(data).encode() if data is not None else None,headers))
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/ortsverband',{'kv':'5100','number':'1','name':'X'})
        self.assertIn(cm.exception.code,(401,403))
        csrf=anmelden(request)
        oid=json.load(request('/api/ortsverband',{'kv':'5100','number':'14','name':'HTTP-Testort'},csrf))['id']
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/ortsverband',{'kv':'5100','number':'14','name':'Doppelt'},csrf)
        self.assertIn('schon',json.load(cm.exception)['error'])
        request('/api/ortsverband',{'id':oid,'kv':'5100','number':'14','name':'HTTP-Testort Nord'},csrf)
        request('/api/ortsverband-status',{'id':oid,'active':False},csrf)
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/kv-nummern',{'numbers':{'5100':'702'}},csrf)
        self.assertEqual(cm.exception.code,400)
        self.assertEqual(json.load(request('/api/kv-nummern',{'numbers':{'5100':'701'}},csrf))['changed'],0)
        data=json.load(request('/api/data?year=2026'))
        ov=[o for o in data['ovs'] if o['id']==oid][0]
        self.assertEqual((ov['name'],ov['active'],ov['members']),('HTTP-Testort Nord',0,0))
        self.assertEqual([k for k in data['kv'] if k['code']=='5700'][0]['cdu_number'],'707')
        request('/api/ortsverband-loeschen',{'id':oid},csrf)
        # 0.28: Beitrag nach Alter (Anzeige im Formular) und Mitglied löschen mit Sicherung
        y=date.today().year
        ages=json.load(request(f'/api/beitrag-nach-alter?birth={y-30}-06-01&joined={y-5}-01-01&year={y}'))
        self.assertEqual([(a['year'],a['amount']) for a in ages],[(y,3000),(y+1,3000)])
        for bad in (f'birth=kaputt&joined={y}-01-01&year={y}',f'birth={y-30}-06-01&joined={y-5}-01-01&year=x',f'joined={y-5}-01-01&year={y}'):
            with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/beitrag-nach-alter?'+bad)
            self.assertEqual(cm.exception.code,400);self.assertNotIn("'birth'",json.load(cm.exception)['error'])
        self.assertIsNone(json.load(request(f'/api/beitrag-nach-alter?birth={y-30}-06-01&joined={y-5}-01-01&year={y}&guest=1'))[0]['amount'])
        gone=json.load(request('/api/member',{'number':'DEL-1','first_name':'Lia','last_name':'Lösch','street':'Weg 1','zip':'20000','city':'Hamburg',
                                              'birth':'2000-01-01','joined':'2024-01-01','kv':'5100'},csrf))['id']
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/member-delete',{'id':gone},csrf)   # Rückstand nach Staffel
        self.assertIn('rückstand',json.load(cm.exception)['error'])
        done=json.load(request('/api/member-delete',{'id':gone,'arrears_ok':True},csrf));self.assertTrue(done['backup'].startswith('vor-mitglied-loeschen'))
        self.assertFalse([m for m in json.load(request(f'/api/data?year={y}'))['members'] if m['id']==gone])
        with self.assertRaises(urllib.error.HTTPError) as cm: request('/api/member-delete',{'id':gone},csrf)
        self.assertEqual(cm.exception.code,400)
        raw=t.xlsx([[704,'Hamburg-Harburg',41,'HTTP-Heide','JU']],t.GliederungImportTests.HEAD2)
        prev=json.load(request('/api/gliederung-vorschau',{'filename':'g.xlsx','content':base64.b64encode(raw).decode()},csrf))
        self.assertEqual(prev['counts']['new'],1)
        done=json.load(request('/api/gliederung-import',{'token':prev['token']},csrf))
        self.assertEqual(done['created'],1);self.assertTrue(done['backup'].startswith('vor-gliederung-import'))
        if t.OOXMLFile:
            locked=base64.b64encode(t.encrypt(t.xlsx([t.person(704,'Hamburg-Harburg',41,'HTTP-Heide','H-1','Hans','Heide')]))).decode()
            for password,state in ((None,'required'),('Irrtum-999','wrong')):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    request('/api/members-preview',{'filename':'g.xlsx','content':locked,**({'password':password} if password else {})},csrf)
                body=json.load(cm.exception)
                self.assertEqual((cm.exception.code,body['password']),(400,state))
            prev=json.load(request('/api/members-preview',{'filename':'g.xlsx','content':locked,'password':t.PASSWORD},csrf))
            self.assertEqual((prev['counts']['new'],prev['rows'][0]['ov'],prev['new_ovs']),(1,'41 HTTP-Heide',[]))
            self.assertNotIn(t.PASSWORD,json.dumps(prev))
            request('/api/members-import',{'token':prev['token'],'selected':[2]},csrf)
            hans=[m for m in json.load(request('/api/data?year=2026'))['members'] if m['number']=='H-1'][0]
            self.assertNotIn('email_work',hans)
            full=json.load(request(f"/api/member-account?id={hans['id']}"))['member']
            self.assertEqual((full['kv'],full['letter_salutation2']),('5400','Hallo Hans'))
            db=sqlite3.connect(app.DB)
            try:
                for (detail,) in db.execute('SELECT detail FROM audit'): self.assertNotIn(t.PASSWORD,detail or '')
            finally: db.close()

if __name__=='__main__':unittest.main(verbosity=2)
