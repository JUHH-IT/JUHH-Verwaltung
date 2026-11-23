"""Local, opt-in booking examples and auditable suspense-account transfers."""
import json,re
from core import money, bank_source, BANK_SOURCE_SQL

def init(db):
    from core import ensure_rows
    ensure_rows(db,'accounts',[('1370','Parkkonto / ungeklärte Bankumsätze','asset')])
    db.execute('CREATE TABLE IF NOT EXISTS booking_examples(bank_id INTEGER PRIMARY KEY REFERENCES bank(id),partner TEXT,pattern TEXT,direction INTEGER,parts TEXT)')
    db.commit()

def normalized(value):
    """Verwendungszweck ohne wechselnde Teile: Wörter mit Ziffern (Daten, Referenzen, IBAN) werden zu #."""
    return ' '.join(re.sub(r'\S*\d\S*', '#',str(value or '').casefold()).split())

def partner(b):
    return (b['name'] or '').strip().casefold()

def remember(store,b,parts,actor):
    if not (partner(b) or normalized(b['reference'])) or any(p.get('invoice_id') or p.get('bill_id') for p in parts):
        raise ValueError('Lernen benötigt einen Zahlungspartner oder Verwendungszweck und ausschließlich direkte Sachkontobuchungen. Rechnungsausgleiche werden nicht gelernt.')
    from haushalt import default_title
    def members(p):   # Mitgliedsbeitrag (8100): Mitglieder mit Betrag merken, damit der Vorschlag sie wieder einsetzt
        items=p.get('members') or []
        cents=lambda m: money(p['amount']) if len(items)==1 and not str(m.get('amount') or '').strip() else money(m['amount'])
        return [{'external':{k:str(m['external'].get(k) or '') for k in ('name','number','kv','reason')},'cents':cents(m)} if m.get('external') is not None   # nicht in der Mitgliederliste
                else {'member_id':int(m['member_id']),'cents':cents(m)} for m in items]
    clean=[{'account':p['account'],'kv':p['kv'],'title':(default_title(p['account'],p['kv']) if p['account']=='KV' else p.get('title')) or default_title(p['account'],p['kv']) or '','cents':money(p['amount']),
            **({'members':members(p)} if p.get('members') and not any(m.get('case') is not None for m in p['members']) else {})} for p in parts]   # mit Klärfall: Mitglieder nicht merken
    store.db.execute('INSERT OR REPLACE INTO booking_examples VALUES(?,?,?,?,?)',(b['id'],partner(b),normalized(b['reference']),1 if b['amount']>0 else -1,json.dumps(clean)))
    store.audit(actor,'Buchung als Lernbeispiel gespeichert',{'bank_id':b['id']})

def suggest(store,bid):
    b=store.db.execute('SELECT * FROM bank WHERE id=?',(bid,)).fetchone()
    if not b: raise ValueError('Bankumsatz fehlt.')
    matches=store.rows("SELECT e.*,b.booked FROM booking_examples e JOIN bank b ON b.id=e.bank_id WHERE b.state='booked' AND e.partner=? AND e.pattern=? AND e.direction=? ORDER BY b.booked DESC,e.bank_id DESC",(partner(b),normalized(b['reference']),1 if b['amount']>0 else -1))
    grouped={}
    for m in matches:
        parts=json.loads(m['parts'])
        # Only suggest exact amounts. No silent scaling of split allocations.
        if sum(p['cents'] for p in parts)!=b['amount']: continue
        key=json.dumps(parts,sort_keys=True)
        if key not in grouped: grouped[key]={'example':m['bank_id'],'count':0,'parts':parts,'last':m['booked']}
        grouped[key]['count']+=1
    return list(grouped.values())

def park(store,bid,reason,actor):
    reason=str(reason).strip()
    if not reason: raise ValueError('Bitte einen Klärungsgrund angeben.')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b=store.db.execute('SELECT * FROM bank WHERE id=?',(bid,)).fetchone()
        if not b or b['state']!='open': raise ValueError('Nur offene Umsätze können geparkt werden.')
        a=b['amount']
        import kasse
        store.journal(b['booked'],'Geparkt: '+reason,bank_source('bank',b),[
            {'account':kasse.ledger_account(store,b),'kv':'LV','debit':max(a,0),'credit':max(-a,0)},   # Bank 1420 bzw. Konto der Kasse
            {'account':'1370','kv':'LV','debit':max(-a,0),'credit':max(a,0),'title':reason}],year=store._previous_year(b))
        store.db.execute("UPDATE bank SET state='parked' WHERE id=?",(bid,))
        store.audit(actor,'Bankumsatz auf 1370 geparkt',{'bank_id':bid,'reason':reason})

def ungueltig(store,bid,reason,actor):
    """Offenen Kontoumsatz mit Grund für ungültig erklären (ab 0.55, Wunsch Lennert Gust 23.11.2026: ein Umsatz war nach dem Import
    doppelt). Gelöscht wird nichts: der Umsatz bleibt mit Grund sichtbar und steht im Protokoll, zählt aber nirgends mehr (nicht als
    offen, nicht in Monatsbericht, Abgleich und Sammelbuchungen). Nur offene Umsätze – ein verbuchter wird zuerst storniert, ein
    geparkter zuerst aufgelöst. Kassenbelege: kasse.void (prüft zusätzlich den Kassenbestand)."""
    reason=' '.join(str(reason or '').split())[:200]
    if not reason: raise ValueError('Bitte einen Grund angeben (z. B. doppelt importiert).')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b=store.db.execute('SELECT * FROM bank WHERE id=?',(int(bid),)).fetchone()
        if not b or b['ledger']!='bank': raise ValueError('Kontoumsatz nicht gefunden.')
        if b['state']!='open': raise ValueError('Nur offene Umsätze lassen sich für ungültig erklären – einen verbuchten zuerst stornieren, einen geparkten zuerst auflösen.')
        store.db.execute("UPDATE bank SET state='void',note=? WHERE id=?",(reason,b['id']))
        store.audit(actor,'Kontoumsatz ungültig',{'bank_id':b['id'],'grund':reason})

def gueltig(store,bid,actor):
    """Einen für ungültig erklärten Kontoumsatz wieder öffnen (Irrtum) – dieselbe Datei lässt sich nicht noch einmal importieren."""
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        b=store.db.execute('SELECT * FROM bank WHERE id=?',(int(bid),)).fetchone()
        if not b or b['ledger']!='bank' or b['state']!='void': raise ValueError('Dieser Umsatz ist nicht ungültig.')
        store.db.execute("UPDATE bank SET state='open',note=NULL WHERE id=?",(b['id'],))
        store.audit(actor,'Kontoumsatz wieder gültig',{'bank_id':b['id'],'grund_vorher':b['note']})

def forget(store,bid,actor):
    with store.db:
        store.db.execute('DELETE FROM booking_examples WHERE bank_id=?',(bid,))
        store.audit(actor,'Lernbeispiel entfernt',{'bank_id':bid})

def details(store,year):
    import kvbudget
    result=store.budget_view(year)
    fo=kvbudget.overview(store)
    for b in result:
        code=b['code']
        b['members']=store.db.execute("SELECT count(*) FROM members WHERE kv=? AND joined<=? AND (left_on IS NULL OR left_on='' OR left_on>=?)",(code,f'{year}-12-31',f'{year}-01-01')).fetchone()[0]
        led=fo.get(code)
        b['fo']={k:led[k] for k in ('available','uncovered','lapsed','next')} if led else None
        b['expenses']=store.rows("SELECT j.id,j.day,j.description,e.account,e.title,e.debit-e.credit amount FROM entries e JOIN journals j ON j.id=e.journal_id JOIN accounts a ON a.code=e.account WHERE e.kv=? AND a.kind='expense' AND COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER))=? ORDER BY j.day DESC,j.id DESC",(code,int(year)))
    parked=store.rows("SELECT b.*,e.title reason FROM bank b JOIN journals j ON j.source="+BANK_SOURCE_SQL+" JOIN entries e ON e.journal_id=j.id AND e.account='1370' WHERE b.state='parked' ORDER BY b.booked,b.id")
    examples=store.rows("SELECT e.bank_id,b.name,b.reference,b.amount,b.booked FROM booking_examples e JOIN bank b ON b.id=e.bank_id WHERE b.state='booked' ORDER BY e.bank_id DESC")
    return {'budgets':result,'parked':parked,'examples':examples}
