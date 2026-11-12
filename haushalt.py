"""Haushaltsansicht nach der Excel-Vorlage; Sachkonten und Titel bleiben getrennt."""
from datetime import date
from pathlib import Path
from pdfschrift import SANS,SANS_BOLD,exclusive
import json, io
from decimal import Decimal
from xml.sax.saxutils import escape

GROUPS=[
 ('income','1. Beiträge / Spenden',[(4,'8100','Mitgliedsbeiträge'),(5,'8200','Spenden Landesverband'),(6,'1371','Spenden Kandidaten'),(7,'8202','Freundes- und Förderkreis')]),
 ('income','2. Sonstige Einnahmen',[(10,'8505','Werbeeinnahmen (Alsterblatt)'),(11,'8502','Hamburgtag – Ticketverkäufe'),(12,'8511','Hamburgtag – Sponsoring'),(13,'8501','Teilnahmebeiträge Deutschlandtag'),(14,'8504','Sommerfest Landesverband'),(15,'8507','Weihnachtsfeier Landesverband'),(16,'8590','Unterstützung Haspa Marathon'),(17,'8500','Sonstige Veranstaltungseinnahmen'),(18,'1592','Landesfördermittel RPJ – Mittelzufluss'),(19,'','Teilnahmebeiträge RPJ')]),
 ('expense','4. Ausgaben Beiträge',[(25,'4340','Abgabe Bundesverband')]),
 ('expense','5. Laufender Geschäftsbetrieb',[(27,'4210','Bürobedarf / Telefon / EDV'),(28,'4220','Porto Geschäftsstelle'),(29,'4310','Druckkosten CDU-Drucker'),(30,'4441','Website JU'),(31,'4249','Anschaffungen / Investitionen'),(32,'4251','Reisekosten Landesverband'),(33,'4246','Buchhaltungs- und Prüfkosten'),(34,'4225','Kosten des Geldverkehrs')]),
 ('expense','6. Allgemeine politische Arbeit',[(37,'4290','Öffentlichkeitsarbeit / Präsente'),(38,'4331','Alsterblatt'),(39,'4451','Hamburgtag – Veranstaltungskosten'),(40,'4360','Landesdelegiertenversammlung'),(41,'4472','Klausurtagungen / Gruppenreisen'),(42,'4452','Deutschlandtag'),(43,'4473','Sommerfest Landesverband'),(44,'4470','Sonstige Veranstaltungen'),(45,'4380','Landesfördermittel RPJ – Mittelverwendung'),(46,'4740','Steuern'),(47,'7200','Wahlkampf Kandidaten – Abruf Spenden')]),
 ('expense','7. Kreisverbände / Schüler Union',[(50,'5100','KV Altona / Elbvororte'),(51,'5200','KV Bergedorf'),(52,'5300','KV Eimsbüttel'),(53,'5400','KV Harburg'),(54,'5500','KV Mitte'),(55,'5600','KV Nord'),(56,'5700','KV Wandsbek'),(57,'5800','Schüler Union')]),
 ('expense','8. Wahlkampfkosten',[(61,'7200','Sonstige Wahlkampfausgaben')])]
TITLES={f'H{r}':{'id':f'H{r}','code':code,'label':label,'kind':kind,'group':name,'source_row':r} for kind,name,rows in GROUPS for r,code,label in rows}
KV_ROWS={str(5100+i*100):f'H{50+i}' for i in range(8)}
# Only unambiguous broad mappings. More specific titles require explicit selection.
AUTO={'8100':'H4','8500':'H17','4210':'H27','4232':'H27','4220':'H28','4225':'H34'}

# Sachkonto je Haushaltsposten der Excel-Vorlage (erster Titel je Kontonummer). Bereichskennungen
# 5100–5800 sind keine Sachkonten; KV-Ausgaben ohne Sachkonto laufen über das Sammelkonto KV.
CODE_TITLES={}
for _tid,_t in TITLES.items():
    if _t['code'] and _t['code'] not in KV_ROWS: CODE_TITLES.setdefault(_t['code'],_tid)
KV_ACCOUNT=('KV','Kreisverbandsausgaben (Sammelkonto, KV-Kennung je Buchung)','expense')
# Konten mit genau einem Haushaltstitel: der Titel ergibt sich aus dem Konto (eine Auswahl beim Verbuchen genügt).
UNIQUE_TITLE={code:tid for code,tid in CODE_TITLES.items() if sum(1 for t in TITLES.values() if t['code']==code)==1}

def default_title(account,kv=None):
    """Haushaltstitel, der sich aus Konto und Bereich ergibt (Sammelkonto KV: Zeile des Kreisverbands), sonst None."""
    if account=='KV': return KV_ROWS.get(kv)
    return UNIQUE_TITLE.get(account)

def init(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS household_plan(year INTEGER NOT NULL,title TEXT NOT NULL,amount INTEGER NOT NULL,source TEXT NOT NULL,PRIMARY KEY(year,title));''')
    # Kontenrahmen-Ausschnitt der Excel-Vorlage ergänzen (bestehende Konten bleiben unverändert).
    from core import ensure_rows
    ensure_rows(db,'accounts',[(code,TITLES[tid]['label'],TITLES[tid]['kind']) for code,tid in CODE_TITLES.items()]+[KV_ACCOUNT])
    sync_account_names(db)

def sync_account_names(db):
    """Einmalig (Entscheidung Lennert Gust 29.10.2026): Kontenbezeichnungen wie im Haushalt bzw. in der Excel-Vorlage,
    damit Konto und Haushaltstitel nicht zwei verschiedene Namen tragen (z. B. 8590 „Unterstützung Haspa Marathon“).
    Später in den Stammdaten geänderte Namen bleiben unangetastet."""
    if db.execute("SELECT 1 FROM settings WHERE key='account_names_household'").fetchone(): return []
    changed=[]
    for code,tid in sorted(UNIQUE_TITLE.items()):
        row=db.execute('SELECT name FROM accounts WHERE code=?',(code,)).fetchone()
        if row and row[0]!=TITLES[tid]['label']:
            db.execute('UPDATE accounts SET name=? WHERE code=?',(TITLES[tid]['label'],code))
            changed.append({'konto':code,'vorher':row[0],'jetzt':TITLES[tid]['label']})
    db.execute("INSERT INTO settings(key,value) VALUES('account_names_household',?)",(date.today().isoformat(),))
    if changed:
        from datetime import datetime
        db.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)',(datetime.now().isoformat(timespec='seconds'),'Programm',
                   'Kontenbezeichnungen an den Haushalt angepasst',json.dumps({'konten':changed},ensure_ascii=False)))
    db.commit()
    return changed

def reference():
    return json.loads((Path(__file__).parent/'haushalt-vorlage.json').read_text(encoding='utf-8'))

def save_plan(store,year,title,amount,actor):
    from core import money
    if not 2000<=year<=2100 or title not in TITLES: raise ValueError('Unbekannter Haushaltstitel oder ungültiges Jahr.')
    value=None if amount is None or str(amount).strip()=='' else money(amount)
    if value is not None and ((TITLES[title]['kind']=='expense' and value>0) or (TITLES[title]['kind']=='income' and value<0)):
        raise ValueError('Einnahmen positiv, Ausgaben negativ eingeben (wie in Excel).')
    with store.db:
        if value is None: store.db.execute('DELETE FROM household_plan WHERE year=? AND title=?',(year,title))
        else: store.db.execute('INSERT INTO household_plan VALUES(?,?,?,?) ON CONFLICT(year,title) DO UPDATE SET amount=excluded.amount,source=excluded.source',(year,title,value,'Manuell erfasster Arbeitsplan'))
        store.audit(actor,'Haushaltsplan geändert',{'year':year,'title':title,'cents':value})

def import_reference(store,year,actor):
    ref=reference();values=ref['plans'].get(str(year))
    if not values: raise ValueError('Keine Excel-Planwerte für dieses Jahr vorhanden.')
    count=0
    with store.db:
        for title,value in values.items():
            if title not in TITLES or value is None: continue
            c=store.db.execute('INSERT OR IGNORE INTO household_plan VALUES(?,?,?,?)',(year,title,value,f"Excel-Arbeitsplan · {ref['sheet']} · {ref['columns'][str(year)]}{TITLES[title]['source_row']}"))
            count+=c.rowcount
        store.audit(actor,'Excel-Planvorlage übernommen',{'year':year,'new_values':count,'note':'Nur fehlende Planwerte, keine Buchungen oder Beschlüsse.'})
    return count

def actuals(store,year,until=None):
    """Ist je Haushaltstitel; until (JJJJ-MM-TT): nur Buchungen bis einschließlich diesem Tag (Monatsbericht)."""
    entries=store.rows("SELECT e.*,a.kind FROM entries e JOIN journals j ON j.id=e.journal_id JOIN accounts a ON a.code=e.account WHERE COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER))=? AND a.kind IN ('income','expense')"+(" AND j.day<=?" if until else ''),(int(year),until) if until else (int(year),))
    values={key:0 for key in TITLES};unmatched={'income':0,'expense':0};count={'income':0,'expense':0}
    for e in entries:
        title=e['title']
        if e['kind']=='expense' and e['kv'] in KV_ROWS: title=KV_ROWS[e['kv']]
        elif title not in TITLES: title=AUTO.get(e['account']) or default_title(e['account'],e['kv'])   # z. B. Rechnungen auf 85xx ohne Titel (bis 0.22)
        if title not in TITLES or TITLES[title]['kind']!=e['kind']:
            unmatched[e['kind']]+=e['credit']-e['debit'];count[e['kind']]+=1
        else: values[title]+=e['credit']-e['debit']
    return values,unmatched,count,bool(entries)

def report(store,year,until=None):
    """Haushalt Plan/Ist für year und Vorjahr; until begrenzt das Ist des Jahres auf Buchungen bis zu diesem Tag."""
    if not 2000<=year<=2100: raise ValueError('Ungültiges Haushaltsjahr.')
    years=[year-1,year];plans={};acts={};unknown={};counts={};has={}
    for y in years:
        plans[y]={r['title']:r['amount'] for r in store.rows('SELECT * FROM household_plan WHERE year=?',(y,))}
        acts[y],unknown[y],counts[y],has[y]=actuals(store,y,until if y==year else None)
    def sum_or_none(vals): return sum(vals) if vals and all(v is not None for v in vals) else None
    rows=[];section=None;totals={}
    def totals_for(kind):
        keys=[k for k,v in TITLES.items() if v['kind']==kind]
        return [sum_or_none([plans[year-1].get(k) for k in keys]),sum(acts[year-1][k] for k in keys)+unknown[year-1][kind] if has[year-1] else None,sum_or_none([plans[year].get(k) for k in keys]),sum(acts[year][k] for k in keys)+unknown[year][kind] if has[year] else None]
    for kind,name,items in GROUPS:
        if section!=kind:
            if section=='income':
                if any(counts[y]['income'] for y in years): rows.append({'type':'unmapped','label':'Noch nicht zugeordnete Einnahmen','code':'?','values':[0,unknown[year-1]['income'] if has[year-1] else None,0,unknown[year]['income'] if has[year] else None]})
                rows.append({'type':'total','label':'3. Einnahmen insgesamt','values':totals_for('income')})
            rows.append({'type':'section','label':'Einnahmen' if kind=='income' else 'Ausgaben'});section=kind
        rows.append({'type':'group','label':name})
        groupvals=[]
        for r,code,label in items:
            tid=f'H{r}';v=[plans[year-1].get(tid),acts[year-1][tid] if has[year-1] else None,plans[year].get(tid),acts[year][tid] if has[year] else None]
            rows.append({'type':'item','id':tid,'code':code,'label':label,'values':v});groupvals.append(v)
        rows.append({'type':'subtotal','label':'Summe '+name.split('. ',1)[-1],'values':[sum_or_none([v[i] for v in groupvals]) for i in range(4)]})
    if any(counts[y]['expense'] for y in years): rows.append({'type':'unmapped','label':'Noch nicht zugeordnete Ausgaben','code':'?','values':[0,unknown[year-1]['expense'] if has[year-1] else None,0,unknown[year]['expense'] if has[year] else None]})
    rows.append({'type':'total','label':'9. Ausgaben insgesamt','values':totals_for('expense')})
    inc=totals_for('income');exp=totals_for('expense')
    result=[inc[i]+exp[i] if inc[i] is not None and exp[i] is not None else None for i in range(4)]
    rows.append({'type':'result','label':'Ergebnis aus Einnahmen und Ausgaben¹','values':result})
    ref=reference()
    return {'overruns':overruns(rows),'year':year,'previous':year-1,'date':date.today().isoformat(),'rows':rows,'titles':list(TITLES.values()),'reference_available':str(year) in ref['plans'],'missing_plan':len(TITLES)-len(plans[year]),'unmapped_entries':sum(counts[year].values()),'has_actuals':has,'reference':ref['source'],'notes':[
        'Arbeitsstand / Pilotversion – kein genehmigter Haushalt. Einnahmen positiv, Ausgaben negativ. Werte in EUR.',
        'Ist = erfasste Ergebnisbuchungen (einschließlich offener Rechnungen) nach Haushaltsjahr (bei Excel-Übernahme laut Blatt, sonst Buchungsdatum), nicht Bankkontostand. Fehlende Jahresdaten erscheinen als —; erfasste Daten sind nicht automatisch vollständig.',
        'KV-Ausgaben werden ausschließlich im jeweiligen KV-Titel gezeigt und nicht nochmals unter Sachausgaben addiert. 25-%-Budgetansprüche sind keine zusätzlichen Ausgaben.',
        '¹ Vor CDU-Verrechnung, Rücklagen, Rechnungsabgrenzung und Vermögensfortschreibung. Diese Bereiche sind noch nicht vollständig erfasst; es wird kein endgültiges Jahresergebnis ausgewiesen.',
        '— = Planwert fehlt oder keine Ergebnisbuchungen im Jahr. Summen unvollständiger Pläne bleiben leer. Excel-Planwerte sind ausdrücklich als Arbeitsvorlage zu übernehmen.'
    ]}

def overruns(rows):
    """Ausgabentitel mehr als 10 % über Plan (Satzung § 50: Nachtragshaushalt vor weiteren Verpflichtungen). Ausgaben sind negativ."""
    out=[]
    for r in rows:
        if r['type']!='item' or TITLES[r['id']]['kind']!='expense': continue
        plan,ist=r['values'][2],r['values'][3]
        if plan is not None and plan<0 and ist is not None and ist<plan*1.1:
            out.append({'id':r['id'],'code':r['code'],'label':r['label'],'plan':plan,'ist':ist,'pct':round((ist/plan-1)*100)})
    return out

@exclusive
def pdf_report(store,year):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    data=report(store,year);buf=io.BytesIO();styles=getSampleStyleSheet();styles['Title'].fontName=SANS_BOLD
    styles.add(ParagraphStyle(name='Cell',fontName=SANS,fontSize=8,leading=10))
    styles.add(ParagraphStyle(name='Note',fontName=SANS,fontSize=7,leading=9,textColor=colors.HexColor('#49616b')))
    p=lambda text,style='Cell':Paragraph(escape(str(text)),styles[style])
    fmt=lambda v:'—' if v is None else f'{v/100:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
    header=['Titel','Haushalt Junge Union Hamburg',f'Plan {year-1}',f'Ist {year-1}',f'Plan {year}',f'Ist {year}','Ist - Plan']
    tabledata=[[p(h) for h in header]]
    commands=[('VALIGN',(0,0),(-1,-1),'MIDDLE'),('FONTNAME',(0,0),(-1,-1),SANS),('FONTSIZE',(0,0),(-1,-1),8),('LEADING',(0,0),(-1,-1),10),('ALIGN',(2,0),(-1,-1),'RIGHT'),('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dce9e6')),('LINEBELOW',(0,0),(-1,0),1,colors.HexColor('#40766b'))]
    for r in data['rows']:
        n=len(tabledata);typ=r['type']
        if typ in ('section','group'):
            commands.append(('NOSPLIT',(0,n),(-1,n+1)))
            tabledata.append([p(r['label'])]+['']*6);commands.extend([('SPAN',(0,n),(-1,n)),('BACKGROUND',(0,n),(-1,n),colors.HexColor('#c7ded6' if typ=='section' else '#eef4f1'))])
        else:
            v=r['values'];delta=v[3]-v[2] if v[3] is not None and v[2] is not None else None
            tabledata.append([r.get('code',''),p(r['label'])]+[fmt(x) for x in v]+[fmt(delta)])
            commands.append(('LINEBELOW',(0,n),(-1,n),.2,colors.HexColor('#dce5e2')))
            if typ in ('subtotal','total','result'):
                commands.extend([('BACKGROUND',(0,n),(-1,n),colors.HexColor('#eef4f1' if typ=='subtotal' else '#dce9e6')),('FONTNAME',(2,n),(-1,n),SANS_BOLD)])
    t=LongTable(tabledata,colWidths=[38,275,82,82,82,82,95],repeatRows=1,hAlign='LEFT',splitByRow=1);t.setStyle(TableStyle(commands))
    story=[p(f'Haushalt {year}','Title'),p(f'Junge Union Hamburg · Vergleich {year-1} / {year} · Stand {data["date"]} · Arbeitsfassung','Note'),Spacer(1,10),t,Spacer(1,10)]+[p(note,'Note') for note in data['notes']]
    def footer(canvas,doc):
        canvas.setFont(SANS,7);canvas.setFillColor(colors.HexColor('#49616b'));canvas.drawString(32,18,'JU Hamburg · Arbeitsfassung · EUR');canvas.drawRightString(810,18,f'Seite {doc.page}')
    SimpleDocTemplate(buf,pagesize=landscape(A4),leftMargin=32,rightMargin=32,topMargin=28,bottomMargin=32).build(story,onFirstPage=footer,onLaterPages=footer)
    return buf.getvalue()
