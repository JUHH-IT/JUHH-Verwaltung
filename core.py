"""Local JU Hamburg finance pilot. All currency values are integer cents."""
import sqlite3, json, hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

KVS = [("LV", "Landesverband"), ("5100", "Altona / Elbvororte"), ("5200", "Bergedorf"), ("5300", "Eimsbüttel"), ("5400", "Harburg"), ("5500", "Mitte"), ("5600", "Nord"), ("5700", "Wandsbek"), ("5800", "Schüler Union")]
ACCOUNTS = [("1420","Bank","asset"),("1500","Forderungen","asset"),("8100","Mitgliedsbeiträge","income"),("8500","Veranstaltungseinnahmen","income"),("8590","Sonstige Veranstaltungseinnahmen","income"),("4210","Büromaterial","expense"),("4220","Porto","expense"),("4225","Kosten des Geldverkehrs","expense"),("4232","EDV-Kosten","expense"),("4350","Aus- und Weiterbildung","expense"),("4360","Innerparteiliche Gremien","expense"),("4430","Werbematerialien","expense"),("4450","Parteitage und vergleichbare Veranstaltungen","expense")]

from formate import money, iso, email_address  # zentrale Geld-/Datumsformate (weiterhin über core importierbar)
import stammdaten
from bankimport import parse_statement

def bank_source(kind,b):
    """Journal-Quelle eines Bankumsatzes je Zuordnungsrunde: bank:7, nach dem ersten Storno bank:7/1 usw.
    kind: 'bank' (Buchung oder Parken), 'resolve' (Parkkonto aufgelöst), 'reverse-bank' (Storno)."""
    return f"{kind}:{b['id']}"+(f"/{b['reversals']}" if b['reversals'] else '')

BANK_SOURCE_SQL="'bank:'||b.id||CASE WHEN b.reversals>0 THEN '/'||b.reversals ELSE '' END"

def ensure_rows(db,table,rows):
    """Grunddaten (Bereiche, Konten) anlegen, falls sie fehlen. Erst lesen: Store() entsteht je Anfrage, ein INSERT OR IGNORE
    würde jedes Mal die Schreibsperre nehmen. Vorhandene Zeilen bleiben unverändert."""
    have={r[0] for r in db.execute(f'SELECT code FROM {table}')}
    missing=[r for r in rows if r[0] not in have]
    if missing:
        db.executemany(f'INSERT OR IGNORE INTO {table} VALUES({",".join("?"*len(missing[0]))})',missing);db.commit()

MAX_LINE_TEXT=300   # Zeichen je Rechnungsposition (längere Texte sprengen die PDF-Tabelle)

def zeilen(text):
    """Leerraum je Zeile zusammenfassen, leere Zeilen entfallen (wie bei Mitgliedern, mitglieder._clean) – ein Tabulator oder
    U+3000 stünde sonst als Kästchen in der Rechnungs-PDF. Nur beim Festschreiben neuer Rechnungen, bestehende bleiben, wie sie sind."""
    return '\n'.join(z for z in (' '.join(l.split()) for l in str(text or '').splitlines()) if z)

class Store:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kv(code TEXT PRIMARY KEY,name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS accounts(code TEXT PRIMARY KEY,name TEXT,kind TEXT);
        CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY,number TEXT UNIQUE NOT NULL,name TEXT NOT NULL,address TEXT NOT NULL,email TEXT,birth TEXT NOT NULL,joined TEXT NOT NULL,left_on TEXT,kv TEXT NOT NULL REFERENCES kv(code));
        CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY,number TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,member_id INTEGER REFERENCES members(id),year INTEGER,recipient TEXT NOT NULL,address TEXT NOT NULL,issued TEXT NOT NULL,due TEXT NOT NULL,total INTEGER NOT NULL CHECK(total>=0),kv TEXT NOT NULL REFERENCES kv(code),snapshot TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'issued',UNIQUE(member_id,year,kind));
        CREATE TABLE IF NOT EXISTS lines(id INTEGER PRIMARY KEY,invoice_id INTEGER REFERENCES invoices(id),description TEXT,amount INTEGER CHECK(amount>0),account TEXT REFERENCES accounts(code));
        CREATE TABLE IF NOT EXISTS bank(id INTEGER PRIMARY KEY,booked TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount!=0),name TEXT,reference TEXT,bank_ref TEXT,source TEXT,row_no INTEGER,fingerprint TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'open');
        CREATE UNIQUE INDEX IF NOT EXISTS bank_reference_unique ON bank(bank_ref) WHERE bank_ref IS NOT NULL;
        CREATE TABLE IF NOT EXISTS imports(hash TEXT PRIMARY KEY,filename TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,bank_id INTEGER REFERENCES bank(id),invoice_id INTEGER REFERENCES invoices(id),amount INTEGER NOT NULL,UNIQUE(bank_id,invoice_id));
        CREATE TABLE IF NOT EXISTS journals(id INTEGER PRIMARY KEY,day TEXT,description TEXT,source TEXT UNIQUE NOT NULL,reversal_of INTEGER UNIQUE REFERENCES journals(id));
        CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY,journal_id INTEGER REFERENCES journals(id),account TEXT REFERENCES accounts(code),kv TEXT REFERENCES kv(code),debit INTEGER NOT NULL DEFAULT 0 CHECK(debit>=0),credit INTEGER NOT NULL DEFAULT 0 CHECK(credit>=0),title TEXT, CHECK((debit>0 AND credit=0) OR (credit>0 AND debit=0)));
        CREATE TABLE IF NOT EXISTS budgets(year INTEGER,kv TEXT REFERENCES kv(code),amount INTEGER CHECK(amount>=0),PRIMARY KEY(year,kv));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,time TEXT,actor TEXT,action TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS mailings(id INTEGER PRIMARY KEY,invoice_id INTEGER NOT NULL REFERENCES invoices(id),time TEXT NOT NULL,recipient TEXT NOT NULL,subject TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('sent','failed','unknown')),error TEXT,message_id TEXT,actor TEXT);
        CREATE INDEX IF NOT EXISTS mailings_invoice ON mailings(invoice_id);
        ''')
        ensure_rows(self.db,'kv',KVS)                 # nur fehlende Zeilen schreiben: keine Schreibsperre je Anfrage
        ensure_rows(self.db,'accounts',ACCOUNTS)
        cols=lambda t:[c[1] for c in self.db.execute(f'PRAGMA table_info({t})')]
        if 'year' not in cols('journals'):
            # Haushaltsjahr, falls abweichend vom Buchungsdatum (z. B. Excel-Übernahme nach Blatt)
            self.db.execute('ALTER TABLE journals ADD COLUMN year INTEGER');self.db.commit()
        if 'dues' not in cols('members'):
            # individueller Jahresbeitrag in Cent; leer = Beitragsstaffel aus den Stammdaten
            self.db.execute('ALTER TABLE members ADD COLUMN dues INTEGER CHECK(dues IS NULL OR dues>=0)');self.db.commit()
        if 'kind' not in cols('mailings'):
            # Art der Nachricht: Rechnung oder Zahlungserinnerung
            self.db.execute("ALTER TABLE mailings ADD COLUMN kind TEXT NOT NULL DEFAULT 'invoice' CHECK(kind IN ('invoice','reminder'))");self.db.commit()
        if 'erechnung' not in cols('mailings'):
            # ab 0.59: leer ('') = die Rechnung ging als E-Rechnung hinaus, sonst der Grund für die normale PDF; NULL = nicht erfasst (vor 0.59, Erinnerung, keine PDF erstellt)
            self.db.execute('ALTER TABLE mailings ADD COLUMN erechnung TEXT');self.db.commit()
        import gliederung
        gliederung.init(self.db)   # Ortsverbände und Nummer der Mitgliederverwaltung je Kreisverband (0.27)
        import mitglieder
        mitglieder.init(self.db)   # Vorname/Nachname, Straße/PLZ/Ort (vorhandene Mitglieder einmalig aufgeteilt)
        __import__('doppelte').init(self.db)   # bestätigte „kein Duplikat“-Paare (0.45)
        __import__('ovabgleich').init(self.db)   # Gebiete der Ortsverbände (Stadtteile) für den Ortsverband nach Adresse (0.52)
        gliederung.link_legacy(self.db)   # Ortsverband-Texte aus 0.24 einmalig in Ortsverbände überführen
        __import__('kenntnis').init(self.db)   # Änderungen an Stammdaten festhalten (0.54) – nach allen Spalten und einmaligen Übernahmen
        import beitraege
        beitraege.init(self.db)    # Zuordnung von 8100-Zahlungen zu Mitgliedern
        import mitgliederkonto
        mitgliederkonto.init(self.db)   # Mitgliederbuchhaltung: von Hand festgelegtes Beitrags-Soll je Mitglied und Jahr
        if 'email' not in cols('invoices'):
            # Versandadresse der Rechnung; leer = E-Mail des Mitglieds
            self.db.execute('ALTER TABLE invoices ADD COLUMN email TEXT');self.db.commit()
        from haushalt import init
        init(self.db)
        import kontenrahmen
        kontenrahmen.init(self.db)   # alle Konten des CDU-Kontenrahmens (vorhandene Namen bleiben)
        from workflow import init as workflow_init
        workflow_init(self.db)
        import verbindlichkeiten
        verbindlichkeiten.init(self.db)
        import freigabe
        freigabe.init(self.db)
        import lastschrift
        lastschrift.init(self.db)
        import kvbudget
        kvbudget.init(self.db)
        import erstattung
        erstattung.init(self.db)
        if 'import_hash' not in cols('bank'):
            # Herkunftsdatei je Umsatz (Hash in imports/Importe) – für das Kassenprüfer-Paket; ältere Umsätze bleiben leer
            self.db.execute('ALTER TABLE bank ADD COLUMN import_hash TEXT');self.db.commit()
        if 'reversals' not in cols('bank'):
            # Anzahl Stornos je Umsatz: nach einem Storno ist der Umsatz wieder offen und wird neu zugeordnet
            self.db.execute('ALTER TABLE bank ADD COLUMN reversals INTEGER NOT NULL DEFAULT 0 CHECK(reversals>=0)');self.db.commit()
        # Zuordnungen stornierter Runden (Rechnung/Eingangsrechnung), damit dieselbe Zuordnung erneut möglich ist
        self.db.execute("CREATE TABLE IF NOT EXISTS reversed_allocations(id INTEGER PRIMARY KEY,bank_id INTEGER NOT NULL REFERENCES bank(id),kind TEXT NOT NULL CHECK(kind IN ('invoice','bill')),target_id INTEGER NOT NULL,amount INTEGER NOT NULL,reversal INTEGER NOT NULL,reversed_on TEXT)")
        self.db.commit()
        import kasse
        kasse.init(self.db)   # Kassenbuch: Kassenbelege als Umsätze mit ledger='cash' (0.25)
        import rpj
        rpj.init(self.db)     # RPJ-Schattenhaushalt: Projekte, Mittelabrufe, Zuordnung der RPJ-Buchungen (0.26)
        import sepadatei
        sepadatei.init(self.db)   # Belege zu aufgeteilten SEPA-Sammelbuchungen (0.29)
        import klaerfaelle
        klaerfaelle.init(self.db) # Klärfälle der Mitgliederverwaltung (0.31; nach beitraege und sepadatei: liest deren Tabellen)
        import rollen
        rollen.init(self.db)      # Rollen und Rechte im Serverbetrieb (0.34)
        import sicherheit
        sicherheit.init(self.db)  # Sicherheitsereignisse, Notfall/Wartung, Dateiprüfung (0.35)
        import schutz
        schutz.init(self.db)      # Abwehr: Sperrliste und Stundenstatistik (0.39)
        if self.db.execute("SELECT 1 FROM settings WHERE key='age_basis' AND value=''").fetchone() and \
                not self.db.execute("SELECT 1 FROM settings WHERE key='age_basis_decided'").fetchone():
            with self.db:   # einmalig: „noch ungeklärt“ → 01.01. (Entscheidung Lennert Gust 08.11.2026), danach in den Stammdaten änderbar
                self.db.execute("UPDATE settings SET value='jan1' WHERE key='age_basis'")
                self.db.execute("INSERT INTO settings VALUES('age_basis_decided','2026-11-08')")
                self.audit('Programm','Altersstichtag auf 01.01. gesetzt (Entscheidung 08.11.2026)',{'vorher':'ungeklärt'})
        legacy=[r[0] for r in self.db.execute("SELECT id FROM bank WHERE state='reversed' ORDER BY id")]
        if legacy:
            # bis 0.11 blieb ein stornierter Umsatz gesperrt; jetzt wieder offen, damit er neu zugeordnet werden kann
            with self.db:
                for bid in legacy:
                    self._archive_allocations(bid,1,None)
                    self.db.execute('DELETE FROM booking_examples WHERE bank_id=?',(bid,))  # stornierte Kontierung nicht mehr vorschlagen
                self.db.execute("UPDATE bank SET state='open',reversals=reversals+1 WHERE state='reversed'")
                self.audit('Programm','Stornierte Umsätze zur Neuzuordnung geöffnet',{'ids':legacy})

    def rows(self,sql,args=()): return [dict(r) for r in self.db.execute(sql,args)]
    def data_start(self):
        """Datenbeginn (JJJJ-MM-TT): ältere Umsätze werden nicht mehr importiert (gesetzt beim Löschen eines Jahres)."""
        row=self.db.execute("SELECT value FROM settings WHERE key='data_start'").fetchone()
        return row[0] if row else None
    def settings(self): return {r['key']:r['value'] for r in self.db.execute('SELECT * FROM settings')}
    def config(self):
        """Stammdaten mit Standardwerten für nicht gesetzte Felder."""
        return {**stammdaten.DEFAULTS,**{k:v for k,v in self.settings().items() if k in stammdaten.DEFAULTS}}
    def audit(self,actor,action,detail): self.db.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)',(datetime.now().isoformat(timespec='seconds'),actor,action,json.dumps(detail,ensure_ascii=False)));__import__('kenntnis').zuordnen(self.db,actor,action,detail)   # ab 0.54: Änderungen an Mitgliedern der Person zuordnen
    def save_settings(self,data,actor):
        values=stammdaten.clean(data,self.config())
        if 'kv_share_auto' in values or 'kv_share_auto_from' in values or 'fo_start' in values:
            import kvbudget
            kvbudget.check_auto_change(self,self.config(),{**self.config(),**values})   # abgeschlossene Budgetjahre bleiben, wie sie sind
        code=values.get('liability_account') or (self.config()['liability_account'] if 'liability_name' in values else None)
        if code:
            row=self.db.execute('SELECT kind FROM accounts WHERE code=?',(code,)).fetchone()
            if row and row['kind']!='liability': raise ValueError(f'Konto {code} ist bereits als {row["kind"]}-Konto belegt und kann nicht für Verbindlichkeiten genutzt werden.')
        with self.db:
            if code:
                name=values.get('liability_name') or self.config().get('liability_name') or 'Verbindlichkeiten'
                self.db.execute("INSERT INTO accounts VALUES(?,?,'liability') ON CONFLICT(code) DO UPDATE SET name=excluded.name",(code,name))
            for k,v in values.items():
                self.db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
            self.audit(actor,'Stammdaten geändert',{k:(v if k not in stammdaten.TEMPLATE_KEYS else '…') for k,v in values.items()})
        if any(k.startswith('kenntnis') for k in values): __import__('kenntnis').bereinigen(self)   # Schalter, ausgenommene Angaben, Höchstfrist gelten sofort

    def _member_values(self,data,current_ov=None,mid=None):
        import mitglieder,gliederung   # deaktivierter Ortsverband nur, wenn er schon der bisherige des Mitglieds ist
        v=mitglieder.values(data,{k for k,_ in KVS},gliederung.ov_map(self.db),inactive={current_ov} if current_ov else set())
        if v['geworben_von'] is not None and (v['geworben_von']==mid or not self.db.execute('SELECT 1 FROM members WHERE id=?',(v['geworben_von'],)).fetchone()):
            raise ValueError('„Geworben von“: Mitglied nicht gefunden – bitte neu auswählen.')
        if self.db.execute('SELECT 1 FROM members WHERE number=? AND id!=?',(v['number'],mid or 0)).fetchone():   # z. B. zwei bekamen denselben Vorschlag (0.53)
            raise ValueError(f"Mitgliedsnummer {v['number']} ist schon vergeben – bitte eine andere eintragen.")
        return v

    def member(self,data,actor):
        from mitglieder import STORED
        v=self._member_values(data)
        with self.db:
            mid=__import__('mitglieder').insert(self.db,v)   # id nie wiederverwenden (auch nicht die eines gelöschten Mitglieds)
            __import__('mitglieder').record_dues(self.db,mid,None,v['dues'])   # individueller Beitrag gilt ab dem laufenden Jahr
            self.audit(actor,'Mitglied angelegt',{'id':mid,'number':v['number']})
        return mid

    def update_member(self,mid,data,actor):
        old=self.db.execute('SELECT * FROM members WHERE id=?',(mid,)).fetchone()
        if not old: raise ValueError('Mitglied nicht gefunden.')
        from mitglieder import STORED,EXTRA
        v=self._member_values({**{k:old[k] for k in EXTRA if k not in data},**data},old['ov_id'],mid)   # nicht mitgeschickte Zusatzangaben bleiben
        changed={k:[old[k],v[k]] for k in STORED if old[k]!=v[k]}
        if not changed: return mid
        with self.db:
            self.db.execute(f'UPDATE members SET {",".join(k+"=?" for k in STORED)} WHERE id=?',[v[k] for k in STORED]+[mid])
            __import__('mitglieder').record_dues(self.db,mid,old['dues'],v['dues'])
            self.audit(actor,'Mitglied geändert',{'id':mid,'number':v['number'],'fields':sorted(changed)})
        if 'left_on' in changed: __import__('mitglieder').notizen_bereinigen(self)   # Notiz entfällt mit dem Austritt (DSGVO)
        return mid

    def individual_dues(self,m,year,rates=None):
        """Individueller Jahresbeitrag für ein Jahr laut Verlauf (mitglieder.record_dues); ohne Verlauf der Wert am Mitglied (vor 0.24)."""
        keys=m.keys()
        if 'id' not in keys: return m['dues'] if 'dues' in keys else None
        if rates is None: rates=[tuple(r) for r in self.db.execute('SELECT valid_from,amount FROM dues_rates WHERE member_id=? ORDER BY valid_from',(m['id'],))]
        if not rates: return m['dues'] if 'dues' in keys else None
        current=None
        for valid_from,amount in rates:
            if valid_from<=year: current=amount
        return current

    def dues_for_invoice(self,m,year,cfg=None):
        """Beitrag für den Beitragslauf: festgelegtes Soll (Mitgliederbuchhaltung) vor der Berechnung; 0 € → keine Rechnung."""
        t=self.db.execute('SELECT amount,reason FROM dues_targets WHERE member_id=? AND year=?',(m['id'],year)).fetchone()
        if t: return {'amount':t['amount'],'basis':f"festgelegt: {t['reason']}"} if t['amount'] else None
        return self.dues_calc(m,year,cfg)

    def dues_calc(self,m,year,cfg=None,rates=None):
        """Beitrag eines Mitglieds: {'amount': Cent, 'basis': Text} oder None (kein Beitrag in diesem Jahr).

        Individueller Jahresbeitrag (Stammdaten des Mitglieds) vor Beitragsstaffel nach Alter;
        im Eintrittsjahr anteilig je Quartal (75/50/25/0 %), sofern in den Stammdaten eingeschaltet."""
        if not 2000<=year<=2100: raise ValueError('Ungültiges Beitragsjahr.')
        cfg=cfg or self.config()
        joined=date.fromisoformat(m['joined']); birth=date.fromisoformat(m['birth'])
        if joined.year>year or (m['left_on'] and m['left_on']<f'{year}-01-01'): return None
        keys=m.keys()
        if ('guest' in keys and m['guest']) or ('dues_free' in keys and m['dues_free']):
            return None   # Gastmitglied bzw. beitragsfrei: kein Beitrag, keine Beitragsrechnung (Entscheidung Lennert Gust 08.11.2026)
        if m['left_on'] and m['left_on'][:4]==str(year): raise ValueError('Austrittsjahr muss fachlich geprüft werden.')
        individual=self.individual_dues(m,year,rates)
        if individual is not None:
            amount,basis=individual,'individueller Jahresbeitrag'
        else:
            if cfg.get('age_basis') not in ('jan1','dec31'): raise ValueError('Altersstichtag in den Stammdaten fachlich bestätigen.')
            cutoff=date(year,1,1) if cfg['age_basis']=='jan1' else date(year,12,31)
            if joined.year==year and cutoff<joined: cutoff=joined
            age=cutoff.year-birth.year-((cutoff.month,cutoff.day)<(birth.month,birth.day))
            if age<int(cfg['dues_min_age']): raise ValueError(f'Keine bestätigte Beitragsregel für Mitglieder unter {cfg["dues_min_age"]} Jahren.')
            lower=int(cfg['dues_min_age'])
            for upper,cents in stammdaten.dues_scale(cfg):
                if upper is None or age<=upper:
                    amount,basis=cents,(f'Staffel {lower}–{upper} Jahre' if upper is not None else f'Staffel ab {lower} Jahren')
                    break
                lower=upper+1
        if joined.year==year and cfg.get('dues_prorate')=='yes':
            quarters=3-(joined.month-1)//3
            amount=int((Decimal(amount)*quarters/4).quantize(Decimal(1),rounding=ROUND_HALF_UP)); basis+=f', Eintritt im {4-quarters}. Quartal ({quarters*25} %)'
        return {'amount':amount,'basis':basis}

    def dues_by_age(self,birth,joined,year,guest=False,dues_free=False,left_on=None):
        """Beitrag nach Alter (Beitragsstaffel, Satzung § 49) für ein Jahr und das folgende – für die Anzeige im Mitgliedsformular,
        mit derselben Berechnung wie Beitragslauf und Beitragskonto (dues_calc); Gast, beitragsfrei und Austritt wie dort.
        Festlegungen im Beitragskonto gehen im Beitragslauf vor (hier nicht berücksichtigt). Nichts wird gespeichert."""
        import mitglieder
        if not 2000<=year<=2100: raise ValueError('Ungültiges Jahr.')
        m={'birth':mitglieder._date(birth,'Geburtsdatum'),'joined':mitglieder._date(joined,'Eintrittsdatum'),'left_on':mitglieder._date(left_on,'Austritt',required=False),
           'dues':None,'guest':mitglieder.flag(guest,'Gastmitglied'),'dues_free':mitglieder.flag(dues_free,'Beitragsfrei')}
        if m['birth']>=m['joined']: raise ValueError('Geburtsdatum muss vor dem Eintritt liegen.')
        out=[]
        for y in (year,year+1):
            try:
                r=self.dues_calc(m,y)
            except ValueError as e:
                out.append({'year':y,'amount':None,'basis':str(e)});continue
            if r:
                out.append({'year':y,'amount':r['amount'],'basis':r['basis']});continue
            why=('Gastmitglied – kein Beitrag' if m['guest'] else 'beitragsfrei' if m['dues_free'] else
                 'ausgetreten' if m['left_on'] and m['left_on']<f'{y}-01-01' else 'noch kein Mitglied')
            out.append({'year':y,'amount':None,'basis':why})
        return out

    def dues_amount(self,m,year):
        r=self.dues_calc(m,year)
        return None if r is None else r['amount']

    def dues_due(self,m,year,today,cfg):
        """Fällig zum 1.1. (bzw. Eintritt), frühestens nach dem Zahlungsziel ab Rechnungsdatum."""
        start=m['joined'] if m['joined'][:4]==str(year) else f'{year}-01-01'
        return max(start,(date.fromisoformat(today)+timedelta(days=int(cfg['dues_terms']))).isoformat())

    def journal(self,day,description,source,entries,reversal=None,year=None):
        if sum(e['debit'] for e in entries)!=sum(e['credit'] for e in entries): raise ValueError('Buchung ist nicht ausgeglichen.')
        c=self.db.execute('INSERT INTO journals(day,description,source,reversal_of,year) VALUES(?,?,?,?,?)',(day,description,source,reversal,year))
        for e in entries:
            e['entry_id']=self.db.execute('INSERT INTO entries(journal_id,account,kv,debit,credit,title) VALUES(?,?,?,?,?,?)',(c.lastrowid,e['account'],e['kv'],e['debit'],e['credit'],e.get('title',''))).lastrowid
        return c.lastrowid

    def check_issuer(self,kind,cfg=None):
        cfg=cfg or self.config()
        if not cfg.get('issuer') or not cfg.get('issuer_address') or not cfg.get('iban'): raise ValueError('Aussteller, Anschrift und Bankverbindung zuerst in den Stammdaten hinterlegen.')
        if kind=='normal' and (cfg.get('invoice_ready')!='yes' or not cfg.get('tax_note')): raise ValueError('Steuerliche Behandlung für normale Rechnungen zunächst in den Stammdaten bestätigen. Unterstützt werden nur Rechnungen ohne Umsatzsteuerausweis.')
        return cfg

    def next_numbers(self,kind,count,issued=None):
        """Die nächsten fortlaufenden Rechnungsnummern (B = Beitrag, R = Rechnung, je Kalenderjahr)."""
        prefix='B' if kind=='dues' else 'R'; numyear=(issued or date.today().isoformat())[:4]
        seq=self.db.execute('SELECT COUNT(*) FROM invoices WHERE number LIKE ?',(f'{prefix}-{numyear}-%',)).fetchone()[0]
        return [f'{prefix}-{numyear}-{seq+i:05d}' for i in range(1,count+1)]

    @staticmethod
    def snapshot_for(kind,cfg,member_number=None):
        """Angaben, die mit der Rechnung festgeschrieben werden (spätere Stammdatenänderungen ändern alte Rechnungen nicht)."""
        keys=('issuer','issuer_address','iban','bic','bank_name','contact_email','contact_phone','website','mail_from_name')+stammdaten.ERECHNUNG   # ab 0.59 auch die Angaben für die E-Rechnung
        return {**{k:cfg.get(k,'') for k in keys},
                'tax_note':cfg.get('tax_note','') if kind=='normal' else 'Mitgliedsbeitrag gemäß Satzung der Jungen Union Hamburg.',
                'pdf_text':cfg['pdf_dues_text' if kind=='dues' else 'pdf_normal_text'],
                'member_number':member_number,'age_basis':cfg.get('age_basis'),'rule':'Satzung 11.04.2026 §49','pilot':False}

    def _issue(self,kind,m,year,recipient,address,kv,issued,due,lines,actor,email=None,basis=None):
        cfg=self.check_issuer(kind)
        if not recipient.strip() or not address.strip(): raise ValueError('Empfänger und Anschrift fehlen.')
        number=self.next_numbers(kind,1,issued)[0]
        total=sum(l['amount'] for l in lines)
        snapshot=self.snapshot_for(kind,cfg,m['number'] if m else None)
        if basis: snapshot['dues_basis']=basis
        if kind=='dues' and total:
            import lastschrift
            debit=lastschrift.debit_snapshot(self,cfg,m,due,issued)  # Mandat vorhanden → Vorabankündigung mit der Rechnung
            if debit: snapshot['debit']=debit;due=debit['date']
        c=self.db.execute('INSERT INTO invoices(number,kind,member_id,year,recipient,address,issued,due,total,kv,snapshot,email) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(number,kind,m['id'] if m else None,year,recipient,address,issued,due,total,kv,json.dumps(snapshot,ensure_ascii=False),email or None))
        iid=c.lastrowid
        for l in lines:
            self.db.execute('INSERT INTO lines(invoice_id,description,amount,account) VALUES(?,?,?,?)',(iid,l['description'],l['amount'],l['account']))
        if total:
            # Haushaltsjahr: Beitragsrechnungen im Beitragsjahr (Versand im Dezember für das Folgejahr), sonst Rechnungsdatum;
            # Haushaltstitel ergibt sich aus dem Konto (z. B. 8504 → Titel der Veranstaltung), damit die Einnahme zugeordnet ist
            import haushalt
            self.journal(issued,number,'invoice:'+str(iid),[{'account':'1500','kv':kv,'debit':total,'credit':0,'title':number}]+[{'account':l['account'],'kv':kv,'debit':0,'credit':l['amount'],'title':haushalt.default_title(l['account'],kv) or ''} for l in lines],
                         year=year if kind=='dues' else None)
        self.audit(actor,'Rechnung festgeschrieben',{'id':iid,'number':number,'cents':total})
        return iid

    def dues_preview(self,year):
        import mitglieder
        result=[];cfg=self.config()
        try: self.check_issuer('dues',cfg); blocked=None
        except ValueError as e: blocked=str(e)
        today=date.today().isoformat()
        for m in self.rows('SELECT * FROM members ORDER BY name'):
            existing=self.db.execute('SELECT number FROM invoices WHERE member_id=? AND year=? AND kind=?',(m['id'],year,'dues')).fetchone()
            item={k:v for k,v in dict(m).items() if k not in mitglieder.PRIVATE}   # wie die Gesamtliste: keine privaten Angaben und keine Notiz
            try:
                r=self.dues_for_invoice(m,year,cfg)
                item.update(amount=r and r['amount'],basis=r and r['basis'],due=r and self.dues_due(m,year,today,cfg),error=blocked)
            except ValueError as e: item.update(amount=None,basis=None,due=None,error=str(e))
            item['existing']=existing[0] if existing else None
            import lastschrift
            item['debit']=bool(item.get('due') and item.get('amount') and lastschrift.debit_snapshot(self,cfg,m,item['due'],today))  # wie beim Erstellen
            result.append(item)
        return result

    def issue_dues(self,year,ids,actor):
        if year<2026: raise ValueError('Die vorliegende Satzung wird nicht rückwirkend auf Beitragsjahre vor 2026 angewendet.')
        created=[]
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for mid in set(ids):
                m=self.db.execute('SELECT * FROM members WHERE id=?',(int(mid),)).fetchone()
                if not m: raise ValueError('Mitglied fehlt.')
                if self.db.execute('SELECT 1 FROM invoices WHERE member_id=? AND year=? AND kind=?',(mid,year,'dues')).fetchone(): continue
                r=self.dues_for_invoice(m,year)
                if r is None: continue
                today=date.today().isoformat();amount=r['amount']
                lines=[{'description':f'Mitgliedsbeitrag {year}','amount':amount,'account':'8100'}] if amount else []
                created.append(self._issue('dues',m,year,m['name'],m['address'],m['kv'],today,self.dues_due(m,year,today,self.config()),lines,actor,basis=r['basis']))
        return created

    def invoice_accounts(self):
        """Einnahmenkonten für normale Rechnungen (Veranstaltungen u. Ä., Kontenbereich 85xx)."""
        return self.rows("SELECT code,name FROM accounts WHERE kind='income' AND code LIKE '85%' ORDER BY code")

    def _series(self,data,cfg):
        """Prüft eine (Serien-)Rechnung: gleiche Positionen für einen oder mehrere Empfänger."""
        allowed={a['code'] for a in self.invoice_accounts()}
        lines=[]
        for l in data.get('lines') or []:
            amount=money(l.get('amount',''))
            text=zeilen(l.get('description'))
            if amount<=0 or not text: raise ValueError('Positionen benötigen einen positiven Betrag und eine Beschreibung.')
            if len(text)>MAX_LINE_TEXT: raise ValueError(f'Positionstext höchstens {MAX_LINE_TEXT} Zeichen.')
            if l.get('account') not in allowed: raise ValueError('Für diese Rechnung bitte ein Einnahmenkonto 85xx wählen.')
            lines.append({'description':text,'amount':amount,'account':l['account']})
        if not lines: raise ValueError('Mindestens eine Position erforderlich.')
        if data.get('kv') not in {k for k,_ in KVS}: raise ValueError('Bitte einen Bereich wählen.')
        today=date.today().isoformat()
        due=iso(data['due']) if str(data.get('due') or '').strip() else (date.today()+timedelta(days=int(cfg['normal_terms']))).isoformat()
        if due<today: raise ValueError('Das Fälligkeitsdatum liegt in der Vergangenheit.')
        recipients=[];seen=set()
        for mid in data.get('member_ids') or []:
            m=self.db.execute('SELECT * FROM members WHERE id=?',(int(mid),)).fetchone()
            if not m: raise ValueError('Mitglied nicht gefunden.')
            if m['id'] in seen: continue
            seen.add(m['id'])
            recipients.append({'member':m,'name':m['name'],'address':m['address'],'email':m['email'] or ''})
        for r in data.get('recipients') or []:
            name,address=' '.join(str(r.get('name') or '').split()),zeilen(r.get('address'))
            if not name and not address and not str(r.get('email','')).strip(): continue
            if not name or not address: raise ValueError('Weitere Empfänger benötigen Name und Anschrift.')
            recipients.append({'member':None,'name':name,'address':address,'email':email_address(r.get('email'))})
        if not recipients: raise ValueError('Bitte mindestens einen Empfänger wählen.')
        if len(recipients)>2000: raise ValueError('Höchstens 2000 Rechnungen auf einmal.')
        return lines,due,recipients

    def series_preview(self,data):
        """Vorschau ohne Schreiben. Festgeschrieben wird später genau dieser Stand (app: Vorschau-Schlüssel)."""
        cfg=self.check_issuer('normal')
        lines,due,recipients=self._series(data,cfg)
        numbers=self.next_numbers('normal',len(recipients))
        total=sum(l['amount'] for l in lines)
        return {'lines':lines,'due':due,'total':total,'sum':total*len(recipients),'kv':data['kv'],'numbers':[numbers[0],numbers[-1]],
                'recipients':[{'member_id':r['member']['id'] if r['member'] else None,'member_number':r['member']['number'] if r['member'] else None,
                               'name':r['name'],'address':r['address'],'email':r['email'],'number':n} for r,n in zip(recipients,numbers)]}

    def issue_series(self,data,actor):
        cfg=self.check_issuer('normal')
        lines,due,recipients=self._series(data,cfg)
        today=date.today().isoformat();ids=[]
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for r in recipients:
                ids.append(self._issue('normal',r['member'],None,r['name'],r['address'],data['kv'],today,due,lines,actor,email=r['email'] if not r['member'] else None))
        return ids

    def issue_normal(self,data,actor):
        """Einzelne Rechnung an einen frei eingegebenen Empfänger (ältere Schnittstelle)."""
        payload={**data,'recipients':[{'name':data.get('recipient',''),'address':data.get('address',''),'email':data.get('email','')}],'member_ids':[]}
        return self.issue_series(payload,actor)[0]

    def invoices(self):
        return self.rows("""SELECT i.*,COALESCE((SELECT SUM(p.amount) FROM payments p JOIN bank b ON b.id=p.bank_id WHERE p.invoice_id=i.id AND b.state='booked'),0) paid,
            COALESCE(NULLIF(i.email,''),(SELECT m.email FROM members m WHERE m.id=i.member_id),'') mail_to,
            (SELECT MAX(time) FROM mailings WHERE invoice_id=i.id AND kind='invoice' AND status='sent') sent_at,
            (SELECT erechnung FROM mailings WHERE invoice_id=i.id AND kind='invoice' AND status='sent' ORDER BY id DESC LIMIT 1) sent_erechnung,
            (SELECT error FROM mailings WHERE invoice_id=i.id AND kind='invoice' ORDER BY id DESC LIMIT 1) last_error,
            (SELECT status FROM mailings WHERE invoice_id=i.id AND kind='invoice' ORDER BY id DESC LIMIT 1) last_status,
            (SELECT COUNT(*) FROM mailings WHERE invoice_id=i.id AND kind='reminder' AND status='sent') reminders,
            (SELECT MAX(time) FROM mailings WHERE invoice_id=i.id AND kind='reminder' AND status='sent') reminded_at,
            (SELECT status FROM mailings WHERE invoice_id=i.id AND kind='reminder' ORDER BY id DESC LIMIT 1) reminder_status,
            COALESCE(json_extract(i.snapshot,'$.pilot'),0) pilot
            FROM invoices i ORDER BY i.id DESC""")

    def reverse_invoice(self,iid,actor):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            i=self.db.execute('SELECT * FROM invoices WHERE id=?',(iid,)).fetchone()
            if not i or i['status']!='issued': raise ValueError('Rechnung ist nicht stornierbar.')
            # Zahlungen, die sich aufheben (z. B. Zahlung und Rücklastschrift), sperren das Storno nicht
            if self.db.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=?',(iid,)).fetchone()[0]: raise ValueError('Rechnung mit Zahlungszuordnung benötigt eine gesonderte Erstattungsprüfung; Storno hier gesperrt.')
            batch=self.db.execute("SELECT b.msg_id,b.status FROM debits d JOIN debit_batches b ON b.id=d.batch_id WHERE d.invoice_id=? AND d.status='pending'",(iid,)).fetchone()
            if batch: raise ValueError(f"Die Rechnung steckt in der Lastschriftdatei {batch['msg_id']}. "+('Zuerst die Lastschrift dort herausnehmen.' if batch['status']=='created' else 'Die Datei ist eingereicht – erst Gutschrift oder Rücklastschrift abwarten und zuordnen.'))
            j=self.db.execute('SELECT id,year,day FROM journals WHERE source=?',('invoice:'+str(iid),)).fetchone()
            if j:
                entries=self.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?',(j['id'],))
                # Storno im Haushaltsjahr der Rechnung (wie bei Bankumsätzen und Eingangsrechnungen)
                self.journal(date.today().isoformat(),'Storno '+i['number'],'cancel:'+str(iid),entries,j['id'],year=j['year'] or int(j['day'][:4]))
            self.db.execute("UPDATE invoices SET status='cancelled' WHERE id=?",(iid,))
            self.audit(actor,'Rechnung storniert',{'id':iid})

    def preview_bank(self,raw,filename):
        h=hashlib.sha256(raw).hexdigest()
        if self.db.execute('SELECT 1 FROM imports WHERE hash=?',(h,)).fetchone(): raise ValueError('Diese Datei wurde bereits importiert.')
        out=[]; local=set(); local_refs=set(); start=self.data_start(); before=0
        # ab 0.55 (ein Umsatz war nach dem Import doppelt): dieselbe Zahlung aus einem zweiten Abruf weicht oft nur in Leerzeichen, Schreibweise oder
        # um einen Tag ab – der exakte Fingerabdruck erkennt das nicht. Ähnlich = gleicher Betrag, höchstens 3 Tage auseinander und gleicher
        # Verwendungszweck (ohne Leer- und Satzzeichen, mindestens 8 Zeichen), oder gleicher Tag und gleicher Name. Abgewählt, nicht gesperrt.
        kern=lambda t: __import__('re').sub(r'[^0-9a-zäöüß]','',str(t or '').casefold())
        tage=lambda a,b: abs((date.fromisoformat(a)-date.fromisoformat(b)).days)
        def aehnlich(booked,amount,name,ref,andere):
            return any(tage(booked,b)<=3 and ((len(kern(ref))>=8 and kern(ref)==kern(r)) or (b==booked and kern(name) and kern(name)==kern(n))) for b,n,r in andere)
        # zwei Zeilen mit je eigener, verschiedener Bankreferenz sind laut Bank zwei Umsätze – dann keine Ähnlichkeitswarnung
        gesehen=[]   # Zeilen dieser Datei: (Tag, Betrag, Name, Zweck, Bankreferenz)
        for parsed in parse_statement(raw,filename):
            item={'row_no':parsed['row_no'],'selected':True,'warning':'','error':None}
            if parsed.get('error'):
                item.update(error=parsed['error'],selected=False); out.append(item); continue
            booked,amount,name,ref,bid=(parsed[k] for k in ('booked','amount','name','reference','bank_ref'))
            if start and booked<start:
                before+=1; continue   # vor dem Datenbeginn: gar nicht erst anzeigen oder übernehmen
            fp=hashlib.sha256(json.dumps([booked,amount,name,ref],ensure_ascii=False).encode()).hexdigest()
            duplicate=bool(bid and (bid in local_refs or self.db.execute("SELECT 1 FROM bank WHERE bank_ref=? AND ledger='bank'",(bid,)).fetchone()))
            matches=self.db.execute("SELECT COUNT(*) FROM bank WHERE fingerprint=? AND ledger='bank'",(fp,)).fetchone()[0]
            if duplicate: item.update(selected=False,warning='Bankreferenz bereits vorhanden',duplicate=True)
            elif matches or fp in local: item.update(selected=False,warning='Ähnlicher Umsatz vorhanden – echte zweite Zahlung ausdrücklich auswählen')
            elif aehnlich(booked,amount,name,ref,[(b['booked'],b['name'],b['reference']) for b in self.db.execute("SELECT booked,name,reference,bank_ref FROM bank WHERE ledger='bank' AND amount=? AND ABS(julianday(booked)-julianday(?))<=3",(amount,booked)) if not (bid and b['bank_ref'])]+[(b,n,r) for b,a,n,r,x in gesehen if a==amount and not (bid and x)]):
                item.update(selected=False,warning='Sehr ähnlicher Umsatz vorhanden (gleicher Betrag, gleicher Name oder Zweck, höchstens 3 Tage auseinander) – nur auswählen, wenn es wirklich eine zweite Zahlung ist')
            elif self.db.execute("SELECT 1 FROM bank WHERE import_hash IS NULL AND source LIKE '% · Umsatz %' AND amount=? AND ABS(julianday(booked)-julianday(?))<=5",(amount,booked)).fetchone():
                item.update(selected=False,warning='Möglicherweise schon aus der Excel-Übernahme vorhanden – nur auswählen, wenn es ein weiterer Umsatz ist')
            item.update(booked=booked,amount=amount,name=name,reference=ref,bank_ref=bid,fingerprint=fp)
            local.add(fp); gesehen.append((booked,amount,name,ref,bid))
            if bid: local_refs.add(bid)
            out.append(item)
        return {'hash':h,'filename':filename,'rows':out,'before_start':before,'data_start':start}

    def import_bank(self,preview,selected,actor):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM imports WHERE hash=?',(preview['hash'],)).fetchone(): raise ValueError('Datei bereits importiert.')
            count=0
            for r in preview['rows']:
                if r['row_no'] not in selected: continue
                if r.get('error') or r.get('duplicate'): raise ValueError('Fehlerhafte oder bereits importierte Zeile gewählt.')
                self.db.execute('INSERT INTO bank(booked,amount,name,reference,bank_ref,source,row_no,fingerprint,import_hash) VALUES(?,?,?,?,?,?,?,?,?)',tuple(r[k] for k in ('booked','amount','name','reference','bank_ref'))+(preview['filename'],r['row_no'],r['fingerprint'],preview['hash']))
                count+=1
            if not count: raise ValueError('Keine Zeilen ausgewählt.')
            self.db.execute('INSERT INTO imports VALUES(?,?,?)',(preview['hash'],preview['filename'],datetime.now().isoformat()))
            self.audit(actor,'Kontoauszug importiert',{'filename':preview['filename'],'rows':count,'sha256':preview['hash']})
        return count

    BILL_CONFLICT='Diese Abbuchung gehört offenbar zur Eingangsrechnung'
    INVOICE_CONFLICT='Dieser Zahlungseingang gehört offenbar zur Rechnung'

    def book(self,bid,parts,actor,learn=False,confirm_no_bill=False):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._book(bid,parts,actor,learn,confirm_no_bill)

    def _book(self,bid,parts,actor,learn=False,confirm_no_bill=False):
        """Umsatz bzw. Kassenbeleg verbuchen – innerhalb einer schon offenen Transaktion (book, kasse.create)."""
        import verbindlichkeiten
        b=self.db.execute('SELECT * FROM bank WHERE id=?',(bid,)).fetchone()
        if not b or b['state'] not in ('open','parked'): raise ValueError('Umsatz ist nicht offen oder geparkt.')
        if b['amount']<0 and not confirm_no_bill and not any(p.get('bill_id') for p in parts):
            hit=verbindlichkeiten.unique_bill(self,b)  # sonst stünde die Ausgabe doppelt im Haushalt
            if hit: raise ValueError(f"{self.BILL_CONFLICT} {hit['number']} ({hit['name']}). Bitte dort zuordnen – oder bestätigen, dass sie nicht dazugehört.")
        if b['amount']>0 and not confirm_no_bill and not any(p.get('invoice_id') for p in parts):
            hits=verbindlichkeiten.invoice_candidates(self,b)  # sonst stünde die Einnahme doppelt im Haushalt und die Rechnung bliebe offen
            if len(hits)==1: raise ValueError(f"{self.INVOICE_CONFLICT} {hits[0]['number']} ({hits[0]['name']}). Bitte die Rechnung zuordnen – oder bestätigen, dass er nicht dazugehört.")
        total=sum(money(p['amount']) for p in parts)
        if total!=b['amount']: raise ValueError('Splitbeträge müssen exakt dem Bankbetrag entsprechen.')
        import kontenrahmen, beitraege as dues, kasse, rpj
        if b['state']=='parked':
            parked=self.db.execute('SELECT year FROM journals WHERE source=?',(bank_source('bank',b),)).fetchone()
            if not parked: raise ValueError('Zu diesem geparkten Umsatz fehlt die Parkbuchung – bitte prüfen.')
            year=parked[0]
        else: year=self._previous_year(b)
        fiscal=year or int(b['booked'][:4])   # Haushaltsjahr der Buchung (RPJ-Projekt bzw. Mittelabruf muss dazu passen)
        liability=kontenrahmen.liability_account(self)
        own=kasse.ledger_account(b)   # Bank 1420 bzw. Kasse 1400 (Kassenbeleg)
        entries=[{'account':'1370' if b['state']=='parked' else own,'kv':'LV','debit':max(b['amount'],0),'credit':max(-b['amount'],0)}]
        if any(p.get('account')==own and not p.get('bill_id') and not p.get('invoice_id') for p in parts):
            raise ValueError('Ein Umsatz kann nicht auf sein eigenes Konto gebucht werden.' if own=='1400' else 'Konto 1420 wird nicht direkt gebucht (Bankkonto selbst).')
        if own!=kasse.ACCOUNT and any(p.get('account')==kasse.ACCOUNT and not p.get('bill_id') and not p.get('invoice_id') for p in parts):
            begin=kasse.start(self)   # Kontoumsatz auf die Kasse: nur im Kassenbuch-Zeitraum, sonst zählte Bargeld doppelt
            if not begin: raise ValueError('Zuerst die Kasse einrichten (Seite Kasse → Anfangsbestand), dann auf 1400 buchen.')
            if b['booked']<begin: raise ValueError(f'Buchungen auf die Kasse erst ab Beginn des Kassenbuchs ({begin}).')
        for p in parts:
            a=money(p['amount'])
            if not a or (a>0)!=(b['amount']>0): raise ValueError('Teilbeträge müssen dieselbe Richtung wie der Bankumsatz haben.')
            if p.get('bill_id'):
                entries.append(verbindlichkeiten.book_part(self,bid,p['bill_id'],a))
                continue
            if p.get('invoice_id'):
                inv=self.db.execute('SELECT * FROM invoices WHERE id=?',(int(p['invoice_id']),)).fetchone()
                if not inv or inv['status']!='issued': raise ValueError('Rechnung ist nicht offen.')
                paid=self.db.execute("SELECT COALESCE(SUM(p.amount),0) FROM payments p JOIN bank b ON b.id=p.bank_id WHERE p.invoice_id=? AND b.state='booked'",(inv['id'],)).fetchone()[0]
                if a>0 and a>inv['total']-paid: raise ValueError('Zahlung übersteigt den offenen Rechnungsbetrag. Überschuss separat klären.')
                if a<0 and -a>paid: raise ValueError('Rückzahlung ist höher als bisher zugeordnet.')
                self.db.execute('INSERT INTO payments(bank_id,invoice_id,amount) VALUES(?,?,?)',(bid,inv['id'],a))
                import lastschrift
                if b['ledger']!='cash' or a>0: lastschrift.on_invoice_payment(self,inv['id'],a,b['booked'])  # negativ = Rücklastschrift (nie bei Barauszahlung)
                account='1500'; kv=inv['kv']; title=inv['number']
            else:
                import haushalt, kontenrahmen
                account=p.get('account'); kv=p.get('kv') or 'LV'
                ac=self.db.execute('SELECT kind FROM accounts WHERE code=?',(account,)).fetchone()
                if not ac: raise ValueError('Bitte ein Konto wählen.')
                if kontenrahmen.bank_bookable(account,ac['kind'],liability):
                    # Bestandskonto (z. B. Kasse, Durchlaufkonto, Darlehen): ohne Haushaltstitel, zählt nicht im Haushalt
                    entries.append({'account':account,'kv':kv,'debit':max(-a,0),'credit':max(a,0),'title':''})
                    continue
                reason=kontenrahmen.system_reason(account,liability)
                if reason: raise ValueError(f'Konto {account} wird nicht direkt gebucht ({reason}).')
                refund=account in ('8100',rpj.GRANT_ACCOUNT) and a<0   # Rückzahlung bzw. Rücklastschrift eines Mitgliedsbeitrags; Rückzahlung von RPJ-Mitteln (mindert den Zufluss)
                if ac['kind']!=('income' if a>0 else 'expense') and not refund: raise ValueError('Bitte passendes Konto wählen: Eingänge auf Einnahmen- oder Bestandskonten, Ausgänge auf Ausgaben- oder Bestandskonten.')
                if account=='KV' and kv not in haushalt.KV_ROWS: raise ValueError('Das Sammelkonto KV nur mit einem Kreisverband als Bereich verwenden.')
                title=haushalt.default_title(account,kv) if account=='KV' else (p.get('title') or '').strip() or haushalt.default_title(account,kv)   # Titel ergibt sich aus dem Konto
                if not title: raise ValueError('Bitte den Haushaltstitel angeben.')
                if title not in haushalt.TITLES or haushalt.TITLES[title]['kind']!=ac['kind']: raise ValueError('Der Haushaltstitel passt nicht zum Konto.')
                if account=='8100':
                    entries+=dues.split_entries(self,p,a,title)    # Mitgliedsbeitrag: je Mitglied eine Buchungszeile
                    continue
                link=rpj.check_part(self,account,title,p.get('rpj'),fiscal)   # RPJ-Posten: Projekt/Kostenart bzw. Mittelabruf (Pflicht)
                if link:
                    entries.append({'account':account,'kv':kv,'debit':max(-a,0),'credit':max(a,0),'title':title,'rpj':link})
                    continue
            entries.append({'account':account,'kv':kv,'debit':max(-a,0),'credit':max(a,0),'title':title})
        self.journal(b['booked'],b['reference'] or b['name'],bank_source('resolve' if b['state']=='parked' else 'bank',b),entries,year=year)
        dues.attach(self,entries,fiscal,actor)   # Mitgliedsbeiträge: Zuordnung je Mitglied festhalten
        rpj.attach(self,entries,actor)           # RPJ-Schattenhaushalt: Zuordnung je Buchungszeile
        self.db.execute('UPDATE bank SET state=? WHERE id=?',('booked',bid))
        if any(e['account']==kasse.ACCOUNT for e in entries): kasse.check_after_booking(self)   # Kassenbestand nie negativ
        self.audit(actor,'Bankumsatz verbucht',{'id':bid,'parts':dues.audit_parts(parts)})   # ohne Namen außerhalb der Mitgliederliste
        if learn:
            from workflow import remember
            remember(self,b,parts,actor)

    def reverse_bank(self,bid,actor):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            b=self.db.execute('SELECT * FROM bank WHERE id=?',(bid,)).fetchone()
            if not b or b['state'] not in ('booked','parked'): raise ValueError('Nur gebuchte oder geparkte Umsätze sind stornierbar.')
            j=self.db.execute('SELECT id,year FROM journals WHERE source=?',(bank_source('bank',b),)).fetchone()
            if not j: raise ValueError('Zu diesem Umsatz fehlt die Buchung – Storno nicht möglich.')
            entries=self.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?',(j['id'],))
            resolved=self.db.execute('SELECT id FROM journals WHERE source=?',(bank_source('resolve',b),)).fetchone()
            if resolved:
                entries+=self.rows('SELECT account,kv,credit debit,debit credit,title FROM entries WHERE journal_id=?',(resolved[0],))
            self.journal(b['booked'],'Storno Zuordnung: '+(b['reference'] or b['name'] or f'Umsatz {bid}'),bank_source('reverse-bank',b),entries,j['id'],year=j['year'])
            import verbindlichkeiten
            verbindlichkeiten.on_bank_reversed(self,bid)  # vor dem Archivieren: betroffene Eingangsrechnungen sperren
            __import__('sepadatei').on_bank_reversed(self,bid)   # dabei gemerkte Mandatsreferenzen (SEPA-Datei) gelten nicht mehr
            import lastschrift
            for p in self.rows('SELECT invoice_id,amount FROM payments WHERE bank_id=? AND amount<0',(bid,)) if b['ledger']!='cash' else []:  # stornierte Rücklastschrift
                lastschrift.on_payment_reversed(self,p['invoice_id'],p['amount'],b['booked'])
            self._archive_allocations(bid,b['reversals']+1,date.today().isoformat())
            learned=self.db.execute('DELETE FROM booking_examples WHERE bank_id=?',(bid,)).rowcount  # stornierte Kontierung nicht mehr vorschlagen
            self.db.execute("UPDATE bank SET state='open',reversals=reversals+1 WHERE id=?",(bid,))
            import kasse
            if any(e['account']==kasse.ACCOUNT for e in entries): kasse.check_after_booking(self,storno=True)
            self.audit(actor,'Bankzuordnung storniert',{'id':bid,'round':b['reversals']+1,'learned_removed':bool(learned),'note':'Gegengebucht; Umsatz ist wieder offen und wird neu zugeordnet.'})

    def _archive_allocations(self,bid,reversal,day):
        """Rechnungs- und Eingangsrechnungs-Zuordnungen eines stornierten Umsatzes ins Archiv verschieben."""
        self.db.execute("INSERT INTO reversed_allocations(bank_id,kind,target_id,amount,reversal,reversed_on) SELECT bank_id,'invoice',invoice_id,amount,?,? FROM payments WHERE bank_id=? ORDER BY id",(reversal,day,bid))
        self.db.execute("INSERT INTO reversed_allocations(bank_id,kind,target_id,amount,reversal,reversed_on) SELECT bank_id,'bill',bill_id,amount,?,? FROM bill_payments WHERE bank_id=? ORDER BY id",(reversal,day,bid))
        self.db.execute('DELETE FROM payments WHERE bank_id=?',(bid,))
        self.db.execute('DELETE FROM bill_payments WHERE bank_id=?',(bid,))

    def _previous_year(self,b):
        """Haushaltsjahr der zuletzt stornierten Zuordnung (z. B. Excel-Blatt), damit ein Neuzuordnen es nicht verschiebt."""
        if not b['reversals']: return None
        row=self.db.execute('SELECT year FROM journals WHERE source=?',(bank_source('bank',{'id':b['id'],'reversals':b['reversals']-1}),)).fetchone()
        return row[0] if row else None

    def bank_rows(self):
        """Umsätze und Kassenbelege für die Oberfläche; 'fy' = Haushaltsjahr, in das eine neue Zuordnung fällt, wenn es vom Buchungsdatum
        abweicht (geparkt bzw. nach Storno: Jahr der früheren Buchung, z. B. Excel-Blatt) – wie in _book, für die Auswahl der RPJ-Projekte."""
        rows=self.rows('SELECT * FROM bank ORDER BY booked DESC,id DESC')
        for b in rows:
            if b['state']=='parked':
                j=self.db.execute('SELECT year FROM journals WHERE source=?',(bank_source('bank',b),)).fetchone()
                year=j and j[0]
            elif b['state']=='open' and b['reversals']:
                year=self._previous_year(b)
            else: continue
            if year and year!=int(b['booked'][:4]): b['fy']=year
        return rows

    def budget_view(self,year):
        """Kreisverbände eines Jahres: Plan und Ist-Budget (kvbudget.year_overview)."""
        import kvbudget
        return kvbudget.year_overview(self,year)

    def snapshot(self,year):
        inv=self.invoices()
        import kontenrahmen, beitraege, mitgliederkonto
        book=mitgliederkonto.overview(self,year)   # Beitragskonten: Soll und gezahlt im Jahr, Saldo heute
        private=__import__('mitglieder').PRIVATE   # Kontakt- und Personenangaben erst beim Öffnen eines Mitglieds (/api/member-account)
        import wahlrecht
        wahl=wahlrecht.laufend(self,book)   # Status je Mitglied (ab 0.37); leer vor „Wahlberechtigung auswerten ab“
        alle_mandate=__import__('lastschrift').mandates(self)
        mandat=__import__('lastschrift').mandat_gueltig(self,rows=alle_mandate)   # ab 0.53: Liste „IBAN ohne Mandat“ – nur Ja/Nein, keine Bankdaten in der Gesamtliste
        return {'wahl':wahlrecht.stichtage(self.config()),'rpj':__import__('rpj').summary(self,year),'cash':__import__('kasse').summary(self),'dues_book':mitgliederkonto.summary(self,year,rows=book),'members':[{**{k:v for k,v in m.items() if k not in private},'paid':book[m['id']]['paid'],'dues_soll':book[m['id']]['soll'],'dues_basis':book[m['id']]['basis'],'balance':book[m['id']]['balance'],'dues_issues':book[m['id']]['issues'],'dues_state':mitgliederkonto.jahr_stand(book[m['id']]),'dues_open_years':book[m['id']]['arrear_years'],'wahl':wahl.get(m['id']),'hat_iban':bool(m['iban']),'sepa_mandat':m['id'] in mandat,'iban_abweichend':bool(m['iban']) and m['id'] in mandat and mandat[m['id']]!=m['iban']} for m in self.rows("SELECT * FROM members ORDER BY COALESCE(NULLIF(last_name,''),name) COLLATE NOCASE,first_name COLLATE NOCASE")],'invoices':inv,'bank':self.bank_rows(),'kv':__import__('gliederung').kvs(self),'ovs':__import__('gliederung').ovs(self),'accounts':kontenrahmen.catalog(self),'dues_open':len(beitraege.entries(self,year,only_open=True)),'dues_open_all':len(beitraege.entries(self,only_open=True)),'cases':__import__('klaerfaelle').counts(self),'kenntnis':__import__('kenntnis').kurz(self.db),'kenntnis_felder':__import__('kenntnis').felder(),'invoice_accounts':self.invoice_accounts(),'bills':__import__('verbindlichkeiten').bills(self),'reviews_due':__import__('freigabe').due(self),'mandates':[{**m,'iban':__import__('lastschrift').masked(m['iban'])} for m in alle_mandate],'sepa_ready':sum(1 for c in __import__('lastschrift').collectable(self) if not c['reason'] and c['soon']),'sepa_check':sum(1 for b in __import__('lastschrift').batches(self) if b['issues']),'kv_closing_due':__import__('kvbudget').closing_due(self),'kv_transfers':__import__('kvbudget').transfers(self,year),'data_start':self.data_start(),'cash_overdue':sum(1 for c in __import__('kvbudget').cash_list(self) if c['overdue']),'reminders_ready':sum(1 for r in __import__('versand').reminders(self) if not r['reason']),'settings':self.config(),'next_numbers':{'dues':self.next_numbers('dues',1)[0],'normal':self.next_numbers('normal',1)[0]},'erechnung':__import__('zugferd').stand(self.config()),'budgets':self.budget_view(year),'audit':self.rows('SELECT * FROM audit ORDER BY id DESC LIMIT 100'),'year':year,'journal':self.rows('SELECT j.id,j.day,COALESCE(j.year,CAST(substr(j.day,1,4) AS INTEGER)) year,j.source,j.description,e.account,e.kv,e.debit,e.credit,e.title FROM journals j JOIN entries e ON e.journal_id=j.id ORDER BY j.id DESC'),'unallocated_dues':self.rows("SELECT i.number,b.booked,p.amount FROM payments p JOIN invoices i ON i.id=p.invoice_id JOIN bank b ON b.id=p.bank_id WHERE i.kind='dues' AND b.state='booked' AND substr(b.booked,1,4)!=CAST(i.year AS TEXT)")
            +[{'number':f"Zahlung auf 8100 (Mitglied {r['number']}, Beitragsjahr {r['year']})",'booked':r['booked'],'amount':r['amount']} for r in beitraege.year_mismatch(self)]}
