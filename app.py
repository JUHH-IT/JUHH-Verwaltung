"""JU Hamburg Finanzen – local authenticated pilot server."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from http.cookies import SimpleCookie
import json, re, secrets, time, sqlite3, io, csv, base64, zipfile, tempfile, webbrowser, sys, argparse, threading, os, shutil, getpass, signal, traceback, ipaddress, gzip
from datetime import date
from html import escape as html_escape
from core import Store, money, KVS
import socket
import haushalt
import workflow
import sicherung
import rollen
import uebernahme
import rechnung_pdf
import versand
import verbindlichkeiten
import freigabe
import lastschrift
import kvbudget
import kvabgleich
import kontenrahmen
import mitglieder
import gliederung
import sepadatei
import auswertung
import opliste
import beitraege
import mitgliederkonto
import kasse
import rpj
import berichte
import erechnung
import erstattung
import geheimnis
import zugang

VERSION='0.34'
APP_NAME='JU Hamburg Finanzen'
ROOT=Path(__file__).resolve().parent
# Datenordner: --data, Umgebungsvariable JU_FINANZEN_DATEN oder (Altbestand) Daten neben dem Programm.
DATA=Path(os.environ.get('JU_FINANZEN_DATEN') or ROOT/'Daten'); DB=DATA/'finanzen.sqlite3'
BACKUPS=None  # Standard: Sicherungen neben dem Datenordner
# Betriebsart: 'mac' = Mac-App (Anmeldung über macOS, nur 127.0.0.1) oder 'server' = Ubuntu-Server hinter Tailscale
# (Anmeldung mit Benutzername und Passwort, Adressen aus ADDRESSES, Dienst über systemd, kein „Programm beenden“).
MODE='mac'
ADDRESSES=[]           # Servermodus: Adressen, unter denen die Oberfläche aufgerufen wird, z. B. https://ju-finanzen.beispiel.ts.net
THROTTLE=zugang.Throttle()   # im Serverbetrieb mit Datei im Datenordner (main)
MAC_PATHS=('/api/stop','/api/login-code','/api/login-device','/api/shutdown')
NO_RIGHT='Dafür fehlt die Berechtigung. Rollen und Rechte vergibt die Verwaltung (Benutzer und Rollen).'
PUBLIC_PATHS=('/api/login','/api/stop','/api/login-code','/api/login-device')   # ohne Sitzung erreichbar
UPLOAD_PATHS=('/api/members-preview','/api/gliederung-vorschau','/api/sepa-vorschau','/api/sepa-begleitzettel','/api/rpj-import-vorschau','/api/einvoice','/api/bill','/api/kasse-beleg','/api/preview-bank','/api/preview-master','/api/preview-kv-master')
TAILNET=[ipaddress.ip_network('100.64.0.0/10'),ipaddress.ip_network('fd7a:115c:a1e0::/48')]
SESSION_HOURS=8        # höchstens so lange gilt eine Anmeldung
IDLE_MINUTES=120       # Servermodus: Abmeldung nach so langer Untätigkeit
SESSIONS={}; PREVIEWS={}; LOGIN_CODES={}; LOGIN_LOCK=threading.Lock(); STOP_TOKEN=secrets.token_urlsafe(32)  # STOP_TOKEN: Steuerschlüssel für Starter und Fenster (Daten/.steuerung)
LOGIN_CODE_SECONDS=60
INSTANCE=secrets.token_hex(8)  # Kennung dieses Serverstarts (kein Geheimnis): Programmfenster beendet nur „seinen“ Server
IMPORT_SUFFIXES={'.csv','.txt','.xls','.xlsx'}
MASTER_ROW_FIELDS=('sheet','row_no','booked','amount','name','reference','code','kst','status','note')

def backups_dir(): return BACKUPS or DATA.parent/'Sicherungen'

def save_receipt(store,table,rid,number,filename,suffix,raw):
    """Beleg (Eingangsrechnung oder Kassenbeleg) in Daten/Belege speichern – nie einen vorhandenen überschreiben."""
    try:
        folder=DATA/'Belege';folder.mkdir(exist_ok=True)
        target=folder/filename;n=1
        while target.exists(): n+=1;target=folder/f'{number}-{n}{suffix}'
        with open(target,'xb') as f: f.write(raw)
        if target.name!=filename:
            with store.db: store.db.execute(f'UPDATE {table} SET attachment=? WHERE id=?',(target.name,rid))
    except OSError:
        with store.db: store.db.execute(f'UPDATE {table} SET attachment=NULL WHERE id=?',(rid,))
        raise ValueError(f'{number} ist erfasst, der Beleg konnte aber nicht gespeichert werden.')

def server_mode(): return MODE=='server'

def clean_address(text):
    """Adresse wie https://ju-finanzen.beispiel.ts.net oder http://100.64.0.1:8769 → 'schema://host[:port]'."""
    u=urlparse(str(text or '').strip().rstrip('/'))
    if u.scheme not in ('http','https') or not u.hostname or u.path or u.query or u.fragment or u.username or u.password:
        raise ValueError(f'Ungültige Adresse: {text!r} (Beispiel: https://ju-finanzen.beispiel.ts.net)')
    return f'{u.scheme}://{u.netloc.lower()}'

def allowed_hosts(port):
    hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
    for a in ADDRESSES:
        u=urlparse(a);hosts.add(u.netloc)
        if u.port is None: hosts.add(f'{u.hostname}:{443 if u.scheme=="https" else 80}')
    return hosts

def allowed_origins(port): return {f'http://127.0.0.1:{port}',f'http://localhost:{port}',*ADDRESSES}

def wait_text(seconds):
    if seconds<60: return f'{seconds} Sekunden'
    minutes=-(-seconds//60)
    return '1 Minute' if minutes==1 else f'{minutes} Minuten'

def user_fingerprint(name):
    """Fingerabdruck des gespeicherten Passwort-Hashes (Servermodus): ändert sich das Passwort – auch über verwaltung.py –,
    enden bestehende Sitzungen bei der nächsten Anfrage."""
    try:
        con=sqlite3.connect(f'file:{DB}?mode=ro',uri=True,timeout=5)
        try: row=con.execute('SELECT password,active FROM users WHERE name=?',(name,)).fetchone()
        finally: con.close()
    except sqlite3.Error: return None
    return zugang.fingerprint(row[0]) if row and row[1] else None   # deaktiviert: keine gültige Sitzung mehr

def log(text): print(time.strftime('%Y-%m-%d %H:%M:%S')+' '+text,flush=True)

def log_error(where):
    """Unerwarteten Fehler ohne Inhalte protokollieren: Art und Programmstelle, keine Werte (keine Daten im Protokoll)."""
    kind,_,tb=sys.exc_info()
    frames=[f'{Path(f.filename).name}:{f.lineno}' for f in traceback.extract_tb(tb)[-4:]]
    log(f'Interner Fehler bei {where}: {kind.__name__ if kind else "?"} ({" > ".join(frames)})')

STATIC=ROOT/'static'
STATIC_TYPES={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml'}
COMPRESSIBLE={'application/json','text/html','text/javascript','text/css','image/svg+xml'}   # gzip für Browser über Tailscale/Mobilfunk

def static_file(path):
    """Datei der Oberfläche zu einem URL-Pfad – nur innerhalb von static/, ohne versteckte Dateien."""
    rel='index.html' if path=='/' else path.lstrip('/')
    if not rel or any(part in ('','.','..') or part.startswith('.') for part in rel.split('/')): return None
    target=(STATIC/rel).resolve()
    if STATIC.resolve() not in target.parents or target.suffix not in STATIC_TYPES or not target.is_file(): return None
    return target

def keep_original(preview,raw):
    (DATA/'Importe').mkdir(exist_ok=True)
    suffix=Path(preview['filename']).suffix.lower()
    (DATA/'Importe'/(preview['hash']+(suffix if suffix in IMPORT_SUFFIXES else '.csv'))).write_bytes(raw)

class LocalServer(ThreadingHTTPServer):
    # Windows: SO_REUSEADDR erlaubt dort mehrere Listener auf einem Port, daher nur auf macOS/Linux
    # (verhindert dort „Port belegt“ direkt nach dem Beenden).
    allow_reuse_address = os.name != 'nt'
    daemon_threads = True
    MAX_CONNECTIONS = 48   # gleichzeitige Verbindungen; weitere werden sofort geschlossen (Schutz vor Überlastung)
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(self.MAX_CONNECTIONS)
        super().__init__(*args,**kwargs)
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request);return
        try: super().process_request(request,client_address)
        except Exception:
            self.slots.release();raise
    def process_request_thread(self,request,client_address):
        try: super().process_request_thread(request,client_address)
        finally: self.slots.release()
    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

class LocalServer6(LocalServer):
    address_family=socket.AF_INET6   # Servermodus mit IPv6-Adresse (z. B. ::1 oder Tailscale fd7a:…)

def preview_invoice(store,query,actor):
    """Rechnung für Vorschau oder Testmail: festgeschrieben (id), Beitragsentwurf (dues+year),
    Serienrechnungsentwurf (draft+i) oder erfundenes Beispiel (sample)."""
    one=lambda k:str(query[k][0] if isinstance(query[k],list) else query[k])
    cfg=store.config();today=date.today().isoformat()
    if 'id' in query:
        inv=rechnung_pdf.invoice_data(store,int(one('id')));inv['email']=versand.recipient_address(store,inv)
        inv['paid']=versand.paid_amount(store,inv['id'])  # wie beim Versand: offener Betrag in E-Mail und GiroCode
        if 'reminder' in query and one('reminder') in ('1','true'):  # Zahlungserinnerung (nächste Stufe, neue Frist ab heute)
            r=versand.reminders(store,[inv['id']],resend=True)[0];inv['reminder']=versand.reminder_terms(cfg,r['level'])
        return inv
    if 'sample' in query:
        if one('sample') not in ('dues','normal','reminder'): raise ValueError('Unbekannte Vorlage.')
        inv=versand.sample_invoice(cfg,one('sample'));inv['email']=cfg.get('mail_from','');return inv
    if 'dues' in query:
        year=int(one('year'));m=store.db.execute('SELECT * FROM members WHERE id=?',(int(one('dues')),)).fetchone()
        if not m: raise ValueError('Mitglied nicht gefunden.')
        store.check_issuer('dues',cfg);r=store.dues_for_invoice(m,year,cfg)
        if r is None: raise ValueError('Für dieses Mitglied fällt in diesem Jahr kein Beitrag an.')
        lines=[{'description':f'Mitgliedsbeitrag {year}','amount':r['amount']}] if r['amount'] else []
        due=store.dues_due(m,year,today,cfg);debit=lastschrift.debit_snapshot(store,cfg,m,due,today) if r['amount'] else None
        inv=rechnung_pdf.draft('dues',cfg,m['name'],m['address'],lines,due,today,year,m['number'],basis=r['basis'],debit=debit)
        inv['email']=m['email'] or '';return inv
    if 'draft' in query:
        p=PREVIEWS.get(one('draft'))
        if not p or p['kind']!='series' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen. Bitte erneut auf „Vorschau“ klicken.')
        pv=p['preview'];r=pv['recipients'][int(one('i'))]
        inv=rechnung_pdf.draft('normal',cfg,r['name'],r['address'],pv['lines'],pv['due'],today,member_number=r['member_number'],number=r['number'])
        inv['email']=r['email'];return inv
    raise ValueError('Keine Rechnung angegeben.')

def mail_preview_page(cfg,inv):
    """HTML-Seite für die Vorschau im Programm: Kopfangaben und die E-Mail, wie sie verschickt wird."""
    subject,body=versand.texts(cfg,inv)
    page=versand.html_body(cfg,inv,body)
    meta=''.join(f'<tr><td style="color:#5b6b7f;padding:2px 12px 2px 0">{k}</td><td>{html_escape(v)}</td></tr>' for k,v in (
        ('Von',f"{cfg.get('mail_from_name','')} <{cfg.get('mail_from') or 'Absender fehlt'}>"),('An',f"{inv['recipient']} <{inv.get('email') or 'keine E-Mail-Adresse'}>"),
        ('Betreff',subject),('Anhang',rechnung_pdf.filename(inv))))
    head=f'<div style="font:13px Helvetica,Arial,sans-serif;background:#fff;padding:12px 16px;border-bottom:1px solid #d5dde8"><table>{meta}</table></div>'
    return page.replace('<body style="margin:0;padding:0;background:#f4f6f9">','<body style="margin:0;padding:0;background:#f4f6f9">'+head,1)

def prune_previews():
    for k,v in list(PREVIEWS.items()):
        if time.time()-v['created']>1800: PREVIEWS.pop(k,None)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,status,data,ctype='application/json; charset=utf-8',cookie=None,attachment=None,inline=None,frame=False):
        if isinstance(data,(dict,list)): data=json.dumps(data,ensure_ascii=False).encode()
        if isinstance(data,str): data=data.encode('utf-8')
        encoding=None
        if len(data)>1400 and not attachment and ctype.split(';')[0] in COMPRESSIBLE and 'gzip' in self.headers.get('Accept-Encoding',''):
            data=gzip.compress(data,5);encoding='gzip'
        self.head(status,ctype,len(data),cookie,attachment,inline,frame,encoding);self.wfile.write(data)
    def send_file(self,fileobj,size,ctype,attachment):
        """Große Antwort (z. B. Kassenprüfer-Paket) stückweise aus einer Datei senden."""
        self.head(200,ctype,size,attachment=attachment);shutil.copyfileobj(fileobj,self.wfile,1024*1024)
    def head(self,status,ctype,length,cookie=None,attachment=None,inline=None,frame=False,encoding=None):
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(length))
        if encoding: self.send_header('Content-Encoding',encoding)
        if ctype.split(';')[0] in COMPRESSIBLE: self.send_header('Vary','Accept-Encoding')
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        if frame:  # E-Mail-Vorschau im Programm: nur einbettbar von hier, ohne Skripte
            self.send_header('X-Frame-Options','SAMEORIGIN')
            self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
        else:
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie: self.send_header('Set-Cookie',cookie)
        if attachment: self.send_header('Content-Disposition',f'attachment; filename="{attachment}"')
        if inline: self.send_header('Content-Disposition',f'inline; filename="{inline}"')
        self.end_headers()
    timeout=30    # Sekunden je Lese-/Schreibvorgang: hängende Verbindungen belegen keinen Thread auf Dauer
    server_version='JU-Finanzen';sys_version=''   # keine Python-Version im Kopf der Antworten
    def valid_host(self): return (self.headers.get('Host') or '').lower() in allowed_hosts(self.server.server_port)
    def via_https(self):
        """Kam die Anfrage über HTTPS (Tailscale Serve auf demselben Rechner bzw. eingetragene https-Adresse)?"""
        if self.headers.get('X-Forwarded-Proto','').lower()=='https' and self.client_address[0] in ('127.0.0.1','::1'): return True
        host=(self.headers.get('Host') or '').lower()
        return any(a.startswith('https://') and urlparse(a).netloc in (host,host.removesuffix(':443')) for a in ADDRESSES)
    def cookie_name(self):
        # über HTTPS mit __Host-Präfix: kein anderer Rechner im Tailnet (*.ts.net) kann den Cookie setzen oder überschreiben
        return '__Host-ju_session' if self.via_https() else 'ju_session'
    def cookie(self,sid=''):
        flags='; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if self.via_https() else '')
        name=self.cookie_name()
        return f'{name}={sid}{flags}' if sid else f'{name}=; Max-Age=0{flags}'
    def session_id(self):
        """Sitzungsschlüssel aus dem Cookie-Kopf – selbst gelesen, damit fremde Cookies (z. B. von anderen Rechnern im Tailnet)
        das Lesen nicht verhindern; nur der Name zur aktuellen Verbindung (HTTPS: __Host-) zählt."""
        wanted=self.cookie_name()
        for part in self.headers.get('Cookie','').split(';'):
            name,_,value=part.strip().partition('=')
            if name==wanted and value: return value.strip()
        return None
    def session(self):
        if hasattr(self,'_session'): return self._session
        sid=self.session_id();s=SESSIONS.get(sid) if sid else None;now=time.time()
        if s and (s['expires']<=now or (server_mode() and now-s['seen']>IDLE_MINUTES*60) or (server_mode() and (s.get('fp') is None or s.get('fp')!=user_fingerprint(s['actor'])))):
            SESSIONS.pop(sid,None);s=None   # abgelaufen, untätig oder Passwort inzwischen neu gesetzt
        if s: s['seen']=now
        self._session=s
        return s
    def new_session(self,actor):
        now=time.time()
        for k,v in list(SESSIONS.items()):   # abgelaufene Sitzungen aufräumen
            if v['expires']<=now or (server_mode() and now-v['seen']>IDLE_MINUTES*60): SESSIONS.pop(k,None)
        sid=secrets.token_urlsafe(32);SESSIONS[sid]={'actor':actor,'csrf':secrets.token_urlsafe(32),'expires':now+SESSION_HOURS*3600,'seen':now,
                                                     'fp':user_fingerprint(actor) if server_mode() else None}
        self._session=SESSIONS[sid]
        return sid,SESSIONS[sid]
    def rights(self,store):
        """Rechte der angemeldeten Person (rollen.py); die Mac-App hat eine Person und prüft keine Rechte."""
        s=self.session()
        if not s: return {}
        return rollen.rights_of(store.db,s['actor']) if server_mode() else dict(rollen.ALL)
    def from_proxy(self): return self.client_address[0] in ('127.0.0.1','::1')
    def device(self):
        """Gerät für Sperre und Protokoll: hinter Tailscale Serve (Anfrage von 127.0.0.1) Tailscale-Login bzw. Adresse aus
        X-Forwarded-For, sonst die Adresse der Verbindung."""
        if self.from_proxy():
            login=self.headers.get('Tailscale-User-Login','').strip()[:80]
            ip=self.headers.get('X-Forwarded-For','').split(',')[-1].strip()[:45]
            return login or ip or self.client_address[0]
        return self.client_address[0]
    def who(self):
        """Herkunft für das Protokoll (keine Inhalte)."""
        return self.device()
    def same_site(self):
        """Browser melden mit Sec-Fetch-Site, woher eine Anfrage kommt: nur eigene Seite oder direkte Eingabe zulassen
        (Schutz vor Seiten auf anderen Rechnern im Tailnet, die als „same-site“ gelten)."""
        return self.headers.get('Sec-Fetch-Site','') in ('','same-origin','none')
    def do_GET(self):
        if not self.valid_host(): return self.send(403,{'error':'Ungültiger Host.'})
        parsed=urlparse(self.path);path=parsed.path;query=parse_qs(parsed.query)
        if path.startswith('/api/') and not self.same_site(): return self.send(403,{'error':'Ungültige Anfrageherkunft.'})
        store=Store(DB)
        try:
            if path=='/api/status':
                s=self.session()
                configured=any(zugang.is_set(u['password']) for u in store.rows('SELECT password FROM users')) if server_mode() else bool(store.rows('SELECT id FROM users'))
                return self.send(200,{'app':APP_NAME,'version':VERSION,'instance':INSTANCE,'mode':MODE,'auth':'password' if server_mode() else 'macos','configured':configured,
                                      'mail_busy':versand.busy(),'user':s['actor'] if s else None,'csrf':s['csrf'] if s else None,
                                      'rights':self.rights(store) if s else {}})
            if path.startswith('/api/'):
                if not self.session(): return self.send(401,{'error':'Bitte anmelden.'})
                rights=self.rights(store)
                if not rollen.allowed(rights,rollen.required('GET',path)): return self.send(403,{'error':NO_RIGHT})
                if path=='/api/data': return self.send(200,rollen.filter_snapshot({**store.snapshot(int(query.get('year',[time.localtime().tm_year])[0])),'last_backup':sicherung.last_daily(backups_dir()/'Automatisch'),'address_data':__import__('adressen').status(DATA)['available']},rights))
                if path=='/api/benutzer':   # Personen und Rollen (ohne Passwörter) – die Mac-App hat nur eine Person
                    return self.send(200,rollen.overview(store.db)) if server_mode() else self.send(404,{'error':'Nur im Serverbetrieb.'})
                if path=='/api/workflow':
                    # die Oberfläche lädt das immer: ohne Recht gleiche Form ohne Inhalt (Parkkonto/Lernbeispiele: Buchhaltung; KV-Budgets: Verband)
                    details=workflow.details(store,int(query['year'][0]))
                    return self.send(200,{k:(v if rollen.can(rights,'buchhaltung') or (k=='budgets' and rollen.can(rights,'verband')) else rollen.blank(v)) for k,v in details.items()})
                if path=='/api/suggestions': return self.send(200,workflow.suggest(store,int(query['id'][0])))
                if path=='/api/dues-preview': return self.send(200,store.dues_preview(int(query['year'][0])))
                if path=='/api/haushalt':
                    report=haushalt.report(store,int(query['year'][0]))
                    return self.send(200,report if rollen.can(rights,'verband') or rollen.can(rights,'buchhaltung') else rollen.blank(report))
                if path=='/api/haushalt-reference': return self.send(200,haushalt.reference())
                if path=='/api/haushalt.pdf':
                    year=int(query['year'][0]);return self.send(200,haushalt.pdf_report(store,year),'application/pdf',inline=f'Haushalt-{year}.pdf')
                if path=='/api/invoice.pdf':
                    inv=rechnung_pdf.invoice_data(store,int(query['id'][0]));return self.send(200,rechnung_pdf.render([inv]),'application/pdf',inline=rechnung_pdf.filename(inv))
                if path=='/api/invoices.pdf':
                    ids=[int(x) for x in query['ids'][0].split(',') if x.strip()]
                    if not 0<len(ids)<=2000: raise ValueError('Bitte 1 bis 2000 Rechnungen auswählen.')
                    return self.send(200,rechnung_pdf.render([rechnung_pdf.invoice_data(store,i) for i in ids]),'application/pdf',inline='Rechnungen.pdf')
                if path=='/api/preview.pdf':
                    inv=preview_invoice(store,query,self.session()['actor']);return self.send(200,rechnung_pdf.render([inv]),'application/pdf',inline=rechnung_pdf.filename(inv))
                if path=='/api/mail-preview':
                    inv=preview_invoice(store,query,self.session()['actor']);return self.send(200,mail_preview_page(store.config(),inv),'text/html; charset=utf-8',frame=True)
                if path=='/api/rpj': return self.send(200,rpj.year_view(store,int(query['year'][0])))
                if path=='/api/rpj-projekt': return self.send(200,rpj.project_view(store,int(query['id'][0])))
                if path=='/api/rpj-offen':
                    year=int(query['year'][0]) if query.get('year',[''])[0] else None
                    return self.send(200,rpj.entries(store,year) if query.get('all',[''])[0]=='1' else rpj.open_entries(store,year))
                if path=='/api/rpj.pdf':
                    year=int(query['year'][0]);return self.send(200,berichte.rpj_pdf(store,year),'application/pdf',inline=f'RPJ-{year}.pdf')
                if path=='/api/rpj-projekt.pdf':
                    pid=int(query['id'][0]);x=rpj.project_view(store,pid)['project']
                    return self.send(200,berichte.rpj_project_pdf(store,pid),'application/pdf',inline=f"RPJ-{x['year']}-Projekt-{x['no']:02d}.pdf")
                if path=='/api/rpj.csv':
                    year=int(query['year'][0]);return self.send(200,rpj.year_csv(store,year),'text/csv; charset=utf-8',attachment=f'RPJ-{year}.csv')
                if path=='/api/rpj-buchungen.csv':
                    if query.get('project',[''])[0]:
                        pid=int(query['project'][0]);x=rpj.project_view(store,pid)['project']
                        return self.send(200,rpj.bookings_csv(store,project_id=pid),'text/csv; charset=utf-8',attachment=f"RPJ-{x['year']}-Projekt-{x['no']:02d}-Buchungen.csv")
                    year=int(query['year'][0]);return self.send(200,rpj.bookings_csv(store,year),'text/csv; charset=utf-8',attachment=f'RPJ-Buchungen-{year}.csv')
                if path=='/api/rpj-vorlage.csv': return self.send(200,rpj.template_csv(),'text/csv; charset=utf-8',attachment='RPJ-Haushalt-Vorlage.csv')
                if path=='/api/kasse': return self.send(200,kasse.book(store,int(query['year'][0])))
                if path=='/api/kasse.pdf':
                    year=int(query['year'][0]);return self.send(200,berichte.cash_pdf(store,year),'application/pdf',inline=f'Kassenbuch-{year}.pdf')
                if path=='/api/kasse.csv':
                    year=int(query['year'][0]);return self.send(200,kasse.csv(store,year),'text/csv; charset=utf-8',attachment=f'Kassenbuch-{year}.csv')
                if path=='/api/kasse-datei':
                    b=store.db.execute("SELECT attachment FROM bank WHERE id=? AND ledger='cash'",(int(query['id'][0]),)).fetchone()
                    if not b or not b['attachment']: raise ValueError('Kein Beleg hinterlegt.')
                    folder=(DATA/'Belege').resolve();target=(folder/b['attachment']).resolve()
                    if folder not in target.parents or not target.is_file() or target.suffix not in verbindlichkeiten.FILE_TYPES or target.suffix=='.xml':
                        raise ValueError('Beleg nicht gefunden.')
                    return self.send(200,target.read_bytes(),verbindlichkeiten.FILE_TYPES[target.suffix][1],inline=target.name)
                if path=='/api/bill-file':
                    b=store.db.execute('SELECT attachment FROM bills WHERE id=?',(int(query['id'][0]),)).fetchone()
                    if not b or not b['attachment']: raise ValueError('Kein Beleg hinterlegt.')
                    folder=(DATA/'Belege').resolve();target=(folder/b['attachment']).resolve()
                    if folder not in target.parents or not target.is_file(): raise ValueError('Beleg nicht gefunden.')
                    if target.suffix=='.xml':  # E-Rechnung: Sichtfassung als PDF oder die XML als Download (nie im Browser darstellen)
                        if query.get('view'): return self.send(200,erechnung.view_pdf(erechnung.read(target.read_bytes())),'application/pdf',inline=target.stem+'-Sichtfassung.pdf')
                        return self.send(200,target.read_bytes(),'application/xml',attachment=target.name)
                    return self.send(200,target.read_bytes(),verbindlichkeiten.FILE_TYPES[target.suffix][1],inline=target.name)
                if path=='/api/refund-form.pdf':
                    mid=int(query['member'][0]) if query.get('member') else None
                    return self.send(200,erstattung.form_pdf(store,mid),'application/pdf',inline='Antrag-Erstattung.pdf')
                if path=='/api/bill-transfers':
                    ids=[int(x) for x in query['ids'][0].split(',') if x.strip()]
                    return self.send(200,[verbindlichkeiten.transfer(store,i) for i in ids])
                if path=='/api/bill-qr.svg':
                    t=verbindlichkeiten.transfer(store,int(query['id'][0]))
                    return self.send(200,rechnung_pdf.qr_svg(rechnung_pdf.epc(t['name'],t['iban_compact'],t['bic'],t['amount'],t['reference'])),'image/svg+xml')
                if path=='/api/kv-ledger':
                    kv=query['kv'][0]
                    if kv not in dict(kvbudget.areas()): raise ValueError('Kein Kreisverband.')
                    return self.send(200,kvbudget.ledger(store,kv))
                if path=='/api/kv.pdf':
                    kv=query['kv'][0]
                    if kv not in dict(kvbudget.areas()): raise ValueError('Kein Kreisverband.')
                    year=int(query['year'][0])
                    return self.send(200,berichte.kv_pdf(store,kv,year),'application/pdf',inline=f'KV-Budget-{kv}-{year}.pdf')
                if path=='/api/monatsbericht.pdf':
                    day=berichte.report_day(query.get('month',[''])[0],store.data_start())  # Monatsende bzw. heute
                    return self.send(200,berichte.monthly_pdf(store,day),'application/pdf',inline=f'Monatsbericht-{day[:7]}.pdf')
                if path=='/api/pruefpaket.zip':
                    year=int(query['year'][0])
                    with tempfile.TemporaryFile(dir=DATA) as tmp:  # Datei für Datei auf die Platte, nicht alles im Speicher
                        berichte.audit_package(store,year,DATA,tmp)
                        size=tmp.tell();tmp.seek(0)
                        with store.db: store.audit(self.session()['actor'],'Kassenprüfer-Paket erstellt',{'year':year,'bytes':size})
                        return self.send_file(tmp,size,'application/zip',f'Kassenpruefung-{year}.zip')
                if path=='/api/konten': return self.send(200,kontenrahmen.accounts(store,int(query['year'][0])))
                if path=='/api/konto':
                    return self.send(200,kontenrahmen.ledger(store,query['code'][0],int(query['year'][0]),query.get('kv',[''])[0] or None))
                if path=='/api/konto.csv':
                    code,year=query['code'][0],int(query['year'][0]);kv=query.get('kv',[''])[0] or None
                    data=kontenrahmen.ledger_csv(store,code,year,kv)
                    return self.send(200,data,'text/csv; charset=utf-8',attachment=re.sub(r'[^A-Za-z0-9-]','',f'Konto-{code}-{year}'+(f'-{kv}' if kv else ''))+'.csv')
                if path=='/api/beitraege-offen':
                    year=int(query['year'][0]) if query.get('year',[''])[0] else None
                    return self.send(200,beitraege.entries(store,year) if query.get('all',[''])[0]=='1' else beitraege.open_list(store,year))
                if path=='/api/karte': return self.send(200,__import__('karte').data(store,DATA))   # Lage der Mitglieder (karte.py), nur Kennung und Koordinate
                if path=='/api/karte-gebiete': return self.send(200,__import__('karte').gebiete())   # Bezirke/Stadtteile (öffentliche Daten)
                if path=='/api/adressen-status': return self.send(200,__import__('adressen').status(DATA))
                if path=='/api/klaerfaelle':   # Klärfälle der Mitgliederverwaltung (klaerfaelle.py)
                    import klaerfaelle
                    return self.send(200,{'items':klaerfaelle.items(store),'counts':klaerfaelle.counts(store)})
                if path=='/api/member-dues': return self.send(200,beitraege.by_member(store,int(query['id'][0])))
                if path=='/api/member-account':
                    account={**mitgliederkonto.account(store,int(query['id'][0])),'sepa_refs':sepadatei.learned_for(store,int(query['id'][0]))}
                    if not rollen.can(rights,'buchhaltung'):   # Mitgliederverwaltung ohne Buchhaltung: Stammdaten ja, Beitragskonto und Beträge nein
                        account={k:(v if k in ('member','first','today') else rollen.blank(v)) for k,v in account.items()}
                    return self.send(200,account)
                if path in ('/api/auswertung','/api/auswertung.pdf','/api/auswertung.csv'):
                    q=lambda k:query.get(k,[''])[0] or None
                    args=(q('stichtag'),q('kv'),q('ov'))
                    amounts=rollen.can(rights,'buchhaltung')   # Beitragsbeträge nur mit Buchhaltung, Listen mit Namen nur mit Mitgliederverwaltung
                    if path=='/api/auswertung': return self.send(200,auswertung.redact(auswertung.report(store,*args),amounts,rollen.can(rights,'mitglieder')))
                    stamp=re.sub(r'[^0-9-]','',args[0] or date.today().isoformat())[:10]
                    if path=='/api/auswertung.pdf': return self.send(200,auswertung.pdf(store,*args,amounts=amounts),'application/pdf',inline=f'Mitgliederstatistik-{stamp}.pdf')
                    return self.send(200,auswertung.csv(store,*args,amounts=amounts),'text/csv; charset=utf-8',attachment=f'Mitgliederstatistik-{stamp}.csv')
                if path=='/api/beitrag-nach-alter':
                    q=lambda k:query.get(k,[''])[0]
                    if not re.fullmatch(r'\d{4}',q('year')): raise ValueError('Ungültiges Jahr.')
                    return self.send(200,store.dues_by_age(q('birth'),q('joined'),int(q('year')),q('guest'),q('dues_free'),q('left_on') or None))
                if path=='/api/member-account.pdf':
                    mid=int(query['id'][0]);number=store.db.execute('SELECT number FROM members WHERE id=?',(mid,)).fetchone()
                    if not number: raise ValueError('Mitglied nicht gefunden.')
                    return self.send(200,berichte.member_pdf(store,mid),'application/pdf',inline=re.sub(r'[^A-Za-z0-9-]','',f'Beitragskonto-{number[0]}')+'.pdf')
                if path in ('/api/op-liste','/api/op-liste.pdf','/api/op-liste.csv','/api/op-listen.zip'):   # offene Posten je Kreisverband (opliste.py)
                    kv=query.get('kv',[''])[0] or None;ov=query.get('ov',[''])[0] or None;stamp=date.today().isoformat()
                    name=f"OP-Liste{'-'+kv if kv else ''}-{stamp}"
                    if path=='/api/op-liste': return self.send(200,opliste.items(store,kv,ov))
                    if path=='/api/op-liste.pdf': return self.send(200,opliste.pdf(store,kv,ov),'application/pdf',inline=name+'.pdf')
                    if path=='/api/op-liste.csv': return self.send(200,opliste.csv(store,kv,ov),'text/csv; charset=utf-8',attachment=name+'.csv')
                    return self.send(200,opliste.zip_all(store),'application/zip',attachment=f'OP-Listen-{stamp}.zip')
                if path=='/api/mitgliederkonten.csv':
                    year=int(query['year'][0]);kv=query.get('kv',[''])[0] or None
                    if kv and kv not in dict(KVS): raise ValueError('Unbekannter Kreisverband.')
                    return self.send(200,mitgliederkonto.overview_csv(store,year,kv),'text/csv; charset=utf-8',attachment=f"Mitgliederkonten-{year}{'-'+kv if kv else ''}.csv")
                if path=='/api/kv-detail': return self.send(200,kvbudget.detail(store,query['kv'][0],int(query['year'][0])))
                if path=='/api/donations':
                    year=int(query['year'][0])
                    return self.send(200,{'donations':kvbudget.donations(store,year),'cash':kvbudget.cash_list(store),'closings':kvbudget.closings(store),'closing_due':kvbudget.closing_due(store)})
                if path=='/api/sepa':
                    return self.send(200,{'collectable':lastschrift.collectable(store),'batches':lastschrift.batches(store),'enabled':lastschrift.enabled(store.config())})
                if path=='/api/sepa.xml':
                    bid=int(query['batch'][0]);return self.send(200,lastschrift.download(store,bid,self.session()['actor']),'application/xml',attachment=lastschrift.filename(store,bid))
                if path=='/api/mandate.pdf':
                    mid=int(query['member'][0]) if query.get('member') else None
                    return self.send(200,lastschrift.mandate_pdf(store,mid),'application/pdf',inline='SEPA-Lastschriftmandat.pdf')
                if path=='/api/reminders':
                    last=store.db.execute('SELECT MAX(created) FROM imports').fetchone()[0]
                    return self.send(200,{'items':versand.reminders(store,resend=query.get('resend',[''])[0]=='1'),'last_import':last,'last_booked':store.db.execute("SELECT MAX(booked) FROM bank WHERE ledger='bank'").fetchone()[0],
                                          'open_incoming':store.db.execute("SELECT COUNT(*) FROM bank WHERE state IN ('open','parked') AND amount>0").fetchone()[0]})
                if path=='/api/quarter-review': return self.send(200,freigabe.report(store,int(query['year'][0]),int(query['quarter'][0])))
                if path=='/api/quarter-review.pdf':
                    y,q=int(query['year'][0]),int(query['quarter'][0])
                    return self.send(200,freigabe.protocol_pdf(store,y,q),'application/pdf',inline=f'Pruefprotokoll-Q{q}-{y}.pdf')
                if path=='/api/matches': return self.send(200,verbindlichkeiten.matches(store,int(query['id'][0])))
                if path=='/api/abgleich': return self.send(200,verbindlichkeiten.proposals(store,int(query['year'][0]) if query.get('year') else None))
                if path=='/api/mail-status':
                    cfg=store.config();return self.send(200,{'job':versand.status(),'password':geheimnis.where(cfg.get('smtp_user')),'keychain':geheimnis.available()})
                if path=='/api/journal.csv':
                    rows=store.snapshot(time.localtime().tm_year)['journal'];buf=io.StringIO();writer=csv.writer(buf,delimiter=';')
                    keys=['id','day','year','description','account','kv','debit','credit','title'];writer.writerow(keys)
                    for r in rows:
                        vals=[r[k] for k in keys]
                        writer.writerow([("'"+v if v.startswith(('=','+','-','@')) else v) if isinstance(v,str) else v for v in vals])
                    return self.send(200,b'\xef\xbb\xbf'+buf.getvalue().encode(),'text/csv; charset=utf-8',attachment='Buchungsjournal-Cent.csv')
                if path=='/api/backup':
                    # ZIP über eine temporäre Datei (nicht im Arbeitsspeicher, auch bei vielen Belegen)
                    with tempfile.TemporaryDirectory(dir=DATA) as td, tempfile.TemporaryFile(dir=DATA) as out:
                        target=Path(td)/'finanzen.sqlite3'; dest=sqlite3.connect(target);store.db.backup(dest);dest.close()
                        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                            z.write(target,'Daten/finanzen.sqlite3')
                            for f in (DATA/'Importe').glob('*'): z.write(f,'Daten/Importe/'+f.name)
                            for f in (DATA/'Belege').glob('*'): z.write(f,'Daten/Belege/'+f.name)
                        size=out.tell();out.seek(0)
                        return self.send_file(out,size,'application/zip','JU-Sicherung-'+time.strftime('%Y-%m-%d')+'.zip')
                return self.send(404,{'error':'Nicht gefunden.'})
            file=static_file(path)
            if file: return self.send(200,file.read_bytes(),STATIC_TYPES[file.suffix])
            return self.send(404,'Nicht gefunden.','text/plain')
        except (ValueError,KeyError,IndexError) as e:
            if path=='/api/mail-preview':  # Fehler sichtbar in der eingebetteten Vorschau statt blockiertem Rahmen
                return self.send(400,f'<p style="font:14px Helvetica,Arial,sans-serif;padding:16px;color:#a32020">{html_escape(str(e) if not isinstance(e,IndexError) else "Vorschau nicht gefunden.")}</p>','text/html; charset=utf-8',frame=True)
            self.send(400,{'error':str(e) if not isinstance(e,IndexError) else 'Nicht gefunden.'})
        except Exception:
            log_error('GET '+path);self.send(500,{'error':'Interner Fehler. Die Aktion wurde nicht abgeschlossen.'})
        finally: store.db.close()
    def do_POST(self):
        if not self.valid_host(): return self.send(403,{'error':'Ungültiger Host.'})
        if self.headers.get('Origin') not in allowed_origins(self.server.server_port) or self.headers.get('Content-Type','').split(';')[0]!='application/json' or not self.same_site():
            return self.send(403,{'error':'Ungültige Anfrageherkunft.'})
        path=urlparse(self.path).path
        if server_mode() and path in MAC_PATHS: return self.send(404,{'error':'Im Serverbetrieb nicht verfügbar.'})
        s=None
        if path not in PUBLIC_PATHS:   # Sitzung und CSRF-Token prüfen, bevor größere Anfragedaten gelesen werden
            s=self.session()
            if not s: return self.send(401,{'error':'Bitte anmelden.'})
            if not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']): return self.send(403,{'error':'Sitzung ungültig. Bitte neu anmelden.'})
        limit=15_000_000 if path in UPLOAD_PATHS else 8_192 if path in PUBLIC_PATHS else 2_000_000
        try: size=int(self.headers.get('Content-Length','0'))
        except ValueError: size=-1
        if not 0<=size<=limit:
            self.close_connection=True
            return self.send(413 if size>limit else 400,{'error':'Datei zu groß (maximal 10 MB Rohdaten).' if size>limit and path in UPLOAD_PATHS else 'Ungültige Anfragedaten.'})
        try: data=json.loads(self.rfile.read(size))
        except Exception: return self.send(400,{'error':'Ungültige Anfragedaten.'})
        if not isinstance(data,dict): return self.send(400,{'error':'Ungültige Anfragedaten.'})
        store=Store(DB)
        try:
            if path=='/api/login':
                # Servermodus: Benutzername und Passwort; Sperre je Gerät und Benutzername (Versuch wird vorgemerkt), neue Sitzung
                if not server_mode(): return self.send(404,{'error':'Nicht gefunden.'})
                keys=('geraet:'+self.device(),'name:'+' '.join(str(data.get('name') or '').split()).lower()[:80])
                wait=THROTTLE.attempt(keys)
                if wait: return self.send(429,{'error':f'Zu viele Fehlversuche. Bitte {wait_text(wait)} warten.','wait':wait})
                actor=zugang.check_login(store.db,data.get('name'),data.get('password'))
                if not actor:
                    log('Anmeldung fehlgeschlagen ('+self.who()+')')
                    return self.send(401,{'error':'Benutzername oder Passwort ist falsch.'})
                THROTTLE.succeeded(keys);log(f'Angemeldet: {actor} ({self.who()})')
                sid,s=self.new_session(actor);return self.send(200,{'csrf':s['csrf'],'user':actor},cookie=self.cookie(sid))
            if path=='/api/stop':
                # Beenden durch den Mac-Starter bei Updates; Schlüssel liegt nur lesbar für den Benutzer in Daten/.steuerung
                if not secrets.compare_digest(str(data.get('token','')),STOP_TOKEN): return self.send(403,{'error':'Nicht erlaubt.'})
                if versand.busy(): return self.send(409,{'error':'Gerade läuft ein E-Mail-Versand.'})
                self.send(200,{'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if path=='/api/login-code':
                # Anmeldung über macOS: Starter/Fenster haben Touch ID bzw. das Mac-Passwort geprüft und holen mit dem
                # Steuerschlüssel (nur für den Benutzer lesbar) einen einmaligen, kurzlebigen Anmeldecode
                if not secrets.compare_digest(str(data.get('token','')),STOP_TOKEN): return self.send(403,{'error':'Nicht erlaubt.'})
                return self.send(200,{'code':new_login_code()})
            if path=='/api/login-device':
                with LOGIN_LOCK: entry=LOGIN_CODES.pop(str(data.get('code','')),None)
                if not entry or time.time()-entry['created']>LOGIN_CODE_SECONDS:
                    return self.send(401,{'error':'Anmeldung abgelaufen. Bitte in der App erneut mit Touch ID oder Mac-Passwort anmelden.'})
                with store.db:
                    store.db.execute('BEGIN IMMEDIATE')
                    user=store.db.execute('SELECT name FROM users ORDER BY id LIMIT 1').fetchone()
                    if not user:   # erster Start: Benutzer mit dem Namen des Mac-Benutzers anlegen (kein Passwort)
                        name=mac_user_name()
                        created=store.db.execute("INSERT INTO users(name,password) VALUES(?,'')",(name,)).lastrowid
                        rollen._grant_owner(store.db,created)   # die eine Person der Mac-App darf alles
                        store.audit(name,'Benutzer eingerichtet (Anmeldung über macOS)',{})
                    actor=user['name'] if user else name
                sid,s=self.new_session(actor);return self.send(200,{'csrf':s['csrf']},cookie=self.cookie(sid))
            actor=s['actor'];result={'ok':True}
            if not rollen.allowed(self.rights(store),rollen.required('POST',path)): return self.send(403,{'error':NO_RIGHT})
            if path=='/api/logout':
                for k,v in list(SESSIONS.items()):
                    if v is s: del SESSIONS[k]
                return self.send(200,result,cookie=self.cookie())
            elif path=='/api/password':
                # eigenes Passwort ändern (Servermodus): altes Passwort nötig; andere Sitzungen dieses Benutzers enden
                if not server_mode(): return self.send(404,{'error':'Nicht gefunden.'})
                keys=('geraet:'+self.device(),'name:'+actor.lower())
                wait=THROTTLE.attempt(keys)
                if wait: return self.send(429,{'error':f'Zu viele Fehlversuche. Bitte {wait_text(wait)} warten.','wait':wait})
                if not zugang.check_login(store.db,actor,data.get('current')): raise ValueError('Das bisherige Passwort ist falsch.')
                THROTTLE.succeeded(keys)
                if data.get('new')!=data.get('repeat'): raise ValueError('Die beiden neuen Passwörter stimmen nicht überein.')
                if data.get('new')==data.get('current'): raise ValueError('Bitte ein anderes Passwort als bisher wählen.')
                zugang.set_password(store.db,actor,data.get('new'),actor)
                for k,v in list(SESSIONS.items()):
                    if v['actor']==actor and v is not s: SESSIONS.pop(k,None)
                s['fp']=user_fingerprint(actor)   # eigene Sitzung bleibt gültig
                log(f'Passwort geändert: {actor}')
            elif path in ('/api/benutzer','/api/rolle','/api/rolle-loeschen') and not server_mode(): return self.send(404,{'error':'Nur im Serverbetrieb.'})
            elif path=='/api/benutzer':
                # Person anlegen/ändern (Verwaltung): ein neues Passwort oder das Deaktivieren beendet deren Sitzungen (Fingerabdruck)
                result['id']=rollen.save_user(store.db,data,actor);log(f'Person gespeichert durch {actor}')
            elif path=='/api/rolle': result['id']=rollen.save_role(store.db,data,actor)
            elif path=='/api/rolle-loeschen': rollen.delete_role(store.db,data.get('id') or 0,actor)
            elif path=='/api/shutdown':
                if versand.busy(): raise ValueError('Gerade läuft ein E-Mail-Versand. Bitte abwarten oder den Versand abbrechen, dann beenden.')
                self.send(200,result)
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            elif path=='/api/settings': store.save_settings(data,actor)
            elif path=='/api/haushalt-plan': haushalt.save_plan(store,int(data['year']),data['title'],data.get('amount'),actor)
            elif path=='/api/haushalt-import': result={'count':haushalt.import_reference(store,int(data['year']),actor)}
            elif path=='/api/member': result={'id':store.member(data,actor)}
            elif path=='/api/members-preview':
                raw=base64.b64decode(data['content'],validate=True)
                if len(raw)>10_000_000: raise ValueError('Datei zu groß.')
                # Kennwort einer geschützten Excel-Datei: nur zum Entschlüsseln im Arbeitsspeicher, nie gespeichert oder protokolliert
                preview=mitglieder.preview(store,raw,Path(data['filename']).name,data.get('password') or None);token=secrets.token_urlsafe(20)
                PREVIEWS[token]={'kind':'members','actor':actor,'created':time.time(),'preview':preview}
                result={'token':token,'filename':preview['filename'],'columns':preview['columns'],'counts':preview['counts'],'guests':preview['guests'],'missing':preview['missing'],
                        'new_ovs':preview['new_ovs'],
                        'rows':[{k:r.get(k) for k in ('row_no','action','error','warning','changes','name','number','kv','ov')}|{k:(r.get('values') or {}).get(k) for k in ('city','guest','dues_free','dues')} for r in preview['rows']]}
            elif path=='/api/members-import':
                p=PREVIEWS.get(str(data.get('token','')))
                if not p or p['kind']!='members' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen. Datei erneut auswählen.')
                saved=sicherung.named_backup(DB,backups_dir()/'Vor Änderungen','vor-mitgliederimport')
                result={**mitglieder.apply(store,p['preview'],data.get('selected') or [],actor),'backup':saved.name};del PREVIEWS[str(data['token'])]
            elif path=='/api/gliederung-vorschau':
                raw=base64.b64decode(data['content'],validate=True)
                if len(raw)>10_000_000: raise ValueError('Datei zu groß.')
                preview=gliederung.preview(store,raw,Path(data['filename']).name,data.get('password') or None);token=secrets.token_urlsafe(20)
                PREVIEWS[token]={'kind':'gliederung','actor':actor,'created':time.time(),'preview':preview}
                result={**preview,'token':token}
            elif path=='/api/gliederung-import':
                p=PREVIEWS.get(str(data.get('token','')))
                if not p or p['kind']!='gliederung' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen. Datei erneut auswählen.')
                if not any(x['action']=='new' for x in p['preview']['items']): raise ValueError('Keine neuen Ortsverbände in der Datei.')
                saved=sicherung.named_backup(DB,backups_dir()/'Vor Änderungen','vor-gliederung-import')
                result={**gliederung.apply_import(store,p['preview'],actor),'backup':saved.name};del PREVIEWS[str(data['token'])]
            elif path=='/api/ortsverband': result={'id':gliederung.save_ov(store,data,actor,int(data['id']) if data.get('id') else None)}
            elif path=='/api/ortsverband-status': gliederung.set_active(store,int(data['id']),data.get('active'),actor)
            elif path=='/api/ortsverband-loeschen': gliederung.delete_ov(store,int(data['id']),actor)
            elif path=='/api/kv-nummern': result={'changed':gliederung.set_cdu_numbers(store,data.get('numbers'),actor)}
            elif path=='/api/sepa-vorschau':
                prune_previews()   # alte Vorschauen (mit Datei) nicht im Speicher halten
                raw=base64.b64decode(data['content'],validate=True)
                if len(raw)>10_000_000: raise ValueError('Datei zu groß.')
                preview=sepadatei.preview(store,raw,Path(str(data.get('filename') or 'SEPA.xml')).name);token=secrets.token_urlsafe(20)
                PREVIEWS[token]={'kind':'sepa','actor':actor,'created':time.time(),'preview':preview,'raw':raw}
                result={**sepadatei.public(preview),'token':token}
            elif path=='/api/sepa-begleitzettel':   # PDF zur Lastschriftdatei: Spalte „Zahler“ (sepadatei.add_payers)
                p=PREVIEWS.get(str(data.get('token','')))
                if not p or p['kind']!='sepa' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen. Datei erneut auswählen.')
                found=sepadatei.add_payers(store,p['preview'],base64.b64decode(data['content'],validate=True))
                result={**sepadatei.public(p['preview']),'token':str(data['token']),'found':found}
            elif path=='/api/sepa-mandat-vergessen': sepadatei.forget(store,data.get('creditor',''),data.get('mandate',''),actor)
            elif path=='/api/sepa-aufteilen':
                p=PREVIEWS.get(str(data.get('token','')))
                if not p or p['kind']!='sepa' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen. Datei erneut auswählen.')
                sepadatei.validate(store,p['preview'],data.get('batch'),int(data['bank_id']),data.get('choices') or {})   # Fehler melden, bevor gesichert wird
                saved=sicherung.named_backup(DB,backups_dir()/'Vor Änderungen','vor-sepa-aufteilung')
                result={**sepadatei.apply(store,p['preview'],data.get('batch'),int(data['bank_id']),data.get('choices') or {},actor),'backup':saved.name}
                # Datei als Beleg der Sammelbuchung aufbewahren (eine Datei kann mehrere Sammler enthalten: nur einmal ablegen)
                prev=p['preview'];suffix='.zip' if p['raw'][:2]==b'PK' else '.xml'
                name=f"SEPA-{prev['batches'][data['batch']]['date']}-{prev['hash'][:12]}{suffix}"
                try:
                    folder=DATA/'Belege';folder.mkdir(exist_ok=True);target=folder/name
                    if not target.exists():
                        with open(target,'xb') as f: f.write(p['raw'])
                    sepadatei.set_stored(store,result['id'],name)
                except (OSError,sqlite3.Error): result['warning']='Aufgeteilt, aber die SEPA-Datei konnte nicht als Beleg gespeichert werden – Datei aufbewahren.'
            elif path=='/api/beitrag-zuordnen': beitraege.assign(store,int(data['entry_id']),data.get('members'),actor)
            elif path=='/api/klaerfall-melden':   # Buchhaltung → Mitgliederverwaltung: eine Zahlung oder alle offenen (des Jahres)
                import klaerfaelle
                if data.get('all') is True: result={'count':klaerfaelle.report_open(store,data.get('year'),actor)}   # Kennungen prüft klaerfaelle._int
                else: result={'id':klaerfaelle.report(store,data.get('entry_id'),data,actor)}
            elif path=='/api/klaerfall-klaeren': result={'result':__import__('klaerfaelle').resolve(store,data.get('id'),data,actor)}
            elif path=='/api/klaerfall-notiz': __import__('klaerfaelle').update(store,data.get('id'),data,actor)
            elif path=='/api/adresse-pruefen':   # Anschrift im amtlichen Bestand auf diesem Rechner prüfen (POST: keine Anschrift in der Adresszeile); speichert nichts
                text=lambda k,n: data.get(k)[:n] if isinstance(data.get(k),str) else ''
                result=__import__('adressen').check(DATA,text('street',200),text('zip',10),text('city',100))
            elif path=='/api/strassen-suche':    # Straßensuche der Karte im amtlichen Bestand – POST, weil der Suchtext auch ein Name sein kann (nie in der Adresszeile)
                ix=__import__('adressen').index(DATA)
                try: result=ix.streets(data.get('q')[:80] if isinstance(data.get('q'),str) else '') if ix else []
                finally:
                    if ix: ix.close()
            elif path=='/api/adressen-laden':    # ganzen öffentlichen Adressbestand der Stadt abrufen (ohne Suchbegriff) – läuft im Hintergrund
                import adressen
                if adressen.start(DATA,version=VERSION):
                    with store.db: store.audit(actor,'Adressdaten: Abruf gestartet',{'quelle':adressen.HOST})
                result=adressen.status(DATA)
            elif path=='/api/member-target':
                if data.get('remove') is True: mitgliederkonto.remove_target(store,int(data['member_id']),int(data['year']),actor)
                else: mitgliederkonto.set_target(store,int(data['member_id']),int(data['year']),data.get('amount',''),data.get('reason',''),actor)
            elif path=='/api/beitraege-vorschlaege': result={'count':beitraege.assign_suggested(store,int(data['year']) if data.get('year') else None,actor)}
            elif path=='/api/kv-plan': kvbudget.set_plan(store,int(data['year']),data.get('kv'),data.get('amount'),actor)
            elif path=='/api/kv-values': result={'changed':kvbudget.set_values(store,int(data['year']),data.get('kv'),{k:data[k] for k in kvbudget.VALUE_FIELDS if k in data},actor)}
            elif path=='/api/kv-label': kvbudget.set_label(store,int(data['journal']),data.get('label'),data.get('recipient'),actor)
            elif path=='/api/preview-kv-master':
                prune_previews()
                raw=base64.b64decode(data['content'],validate=True)
                if len(raw)>10_000_000: raise ValueError('Datei zu groß.')
                preview=kvabgleich.preview(store,raw,Path(str(data.get('filename') or 'Masterdatei.xlsx')).name);token=secrets.token_urlsafe(20)
                PREVIEWS[token]={'kind':'kvmaster','actor':actor,'created':time.time(),'raw':raw}
                result={'token':token,**preview}
            elif path=='/api/import-kv-master':
                p=PREVIEWS.get(str(data.get('token','')))
                if not p or p['kind']!='kvmaster' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen. Datei erneut auswählen.')
                saved=sicherung.named_backup(DB,backups_dir()/'Vor Änderungen','vor-kv-abgleich')
                result={'counts':kvabgleich.execute(store,p['raw'],actor),'backup':saved.name};del PREVIEWS[str(data['token'])]
            elif path=='/api/member-update': result={'id':store.update_member(int(data['id']),data,actor)}
            elif path=='/api/member-delete':
                mid=int(data['id']);ok=data.get('arrears_ok') is True
                mitglieder.check_delete(store,mid,ok)   # erst prüfen, dann sichern, dann löschen (erneut geprüft)
                saved=sicherung.named_backup(DB,backups_dir()/'Vor Änderungen','vor-mitglied-loeschen')
                mitglieder.delete(store,mid,actor,ok);result={'backup':saved.name}
            elif path=='/api/einvoice':
                f=data.get('file') or {}
                raw=base64.b64decode(f.get('content',''),validate=True)
                result=erechnung.read(raw)
                result['duplicates']=verbindlichkeiten.duplicates(store,{'creditor':result['creditor'],'iban':result['iban'] or None,'ext_number':result['number'],'issued':result['issued']},result['total'])
            elif path=='/api/bill':
                suffix=raw=None
                if data.get('file'): suffix,raw=verbindlichkeiten.check_file(data['file'].get('name',''),data['file'].get('content',''))
                bid,number,filename=verbindlichkeiten.create(store,data,actor,suffix)
                if raw is not None: save_receipt(store,'bills',bid,number,filename,suffix,raw)
                result={'id':bid,'number':number}
            elif path=='/api/kasse-beleg':
                suffix=raw=None
                if data.get('file'):
                    if not str(data['file'].get('name','')).lower().endswith(('.pdf','.png','.jpg','.jpeg')): raise ValueError('Kassenbeleg bitte als PDF, PNG oder JPG anhängen.')
                    suffix,raw=verbindlichkeiten.check_file(data['file'].get('name',''),data['file'].get('content',''))
                created=kasse.create(store,data,actor,suffix)
                if raw is not None: save_receipt(store,'bank',created['id'],created['number'],created['filename'],suffix,raw)
                result={'id':created['id'],'number':created['number']}
            elif path=='/api/rpj-jahr': rpj.save_year(store,data,actor)
            elif path=='/api/rpj-projekt': result={'id':rpj.save_project(store,data,actor)}
            elif path=='/api/rpj-projekt-loeschen': rpj.delete_project(store,int(data['id']),actor)
            elif path=='/api/rpj-abruf': result={'id':rpj.save_request(store,data,actor)}
            elif path=='/api/rpj-abruf-loeschen': rpj.delete_request(store,int(data['id']),actor)
            elif path=='/api/rpj-zuordnen': rpj.assign(store,int(data['entry_id']),data.get('items'),actor)
            elif path=='/api/rpj-zuordnen-mehrere': result={'count':rpj.assign_many(store,data.get('entry_ids') or [],data.get('target'),data.get('category'),actor)}
            elif path=='/api/rpj-import-vorschau':
                raw=base64.b64decode(data['content'],validate=True)
                if len(raw)>5_000_000: raise ValueError('Datei zu groß.')
                preview=rpj.preview_import(store,raw,Path(data['filename']).name);token=secrets.token_urlsafe(20)
                PREVIEWS[token]={'kind':'rpj','actor':actor,'created':time.time(),'preview':preview}
                result={'token':token,**preview}
            elif path=='/api/rpj-import':
                p=PREVIEWS.get(str(data.get('token','')))
                if not p or p['kind']!='rpj' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen. Datei erneut auswählen.')
                saved=sicherung.named_backup(DB,backups_dir()/'Vor Änderungen','vor-rpj-import')
                result={**rpj.apply_import(store,p['preview'],data.get('selected') or [],actor),'backup':saved.name};del PREVIEWS[str(data['token'])]
            elif path=='/api/kasse-anfang': kasse.set_opening(store,data.get('day',''),data.get('amount',''),actor)
            elif path=='/api/kasse-ungueltig': kasse.void(store,int(data['id']),data.get('reason',''),actor)
            elif path=='/api/bill-cancel': verbindlichkeiten.cancel(store,int(data['id']),actor)
            elif path=='/api/bill-transferred': verbindlichkeiten.mark_transferred(store,data['ids'],actor,data.get('undo') is True)
            elif path=='/api/abgleich':
                count,error=verbindlichkeiten.apply(store,data['items'],actor);result={'count':count,'error':error}
            elif path=='/api/bill-release': verbindlichkeiten.release(store,int(data['id']),actor)
            elif path=='/api/cash-donation': result={'id':kvbudget.save_cash(store,data,actor,int(data['id']) if data.get('id') else None)}
            elif path=='/api/kv-close': kvbudget.close_year(store,int(data['year']),actor)
            elif path=='/api/kv-transfer': result={'id':kvbudget.transfer(store,data,actor)}
            elif path=='/api/kv-transfer-reverse': kvbudget.reverse_transfer(store,int(data['id']),data.get('reason'),actor)
            elif path=='/api/mandate': result={'reference':lastschrift.create_mandate(store,int(data['member']),data,actor)}
            elif path=='/api/mandate-revoke': lastschrift.revoke_mandate(store,int(data['id']),data.get('reason'),actor)
            elif path=='/api/sepa-batch': result={'id':lastschrift.create_batch(store,data.get('ids') or [],actor)}
            elif path=='/api/sepa-batch-status': lastschrift.set_batch(store,int(data['id']),data.get('status'),actor,data.get('reason'))
            elif path=='/api/sepa-debit-remove': lastschrift.remove_debit(store,int(data['id']),actor)
            elif path=='/api/sepa-debit-reject': lastschrift.reject_debit(store,int(data['id']),data.get('reason'),actor)
            elif path=='/api/debit-notice': lastschrift.record_notice(store,int(data['invoice']),data.get('sent_on'),actor)
            elif path=='/api/bill-approve': freigabe.approve(store,int(data['id']),data.get('role'),data,actor)
            elif path=='/api/bill-approval-revoke': freigabe.revoke(store,int(data['id']),actor)
            elif path=='/api/quarter-review': freigabe.record_review(store,int(data['year']),int(data['quarter']),data,actor)
            elif path=='/api/bill-writeoff': verbindlichkeiten.write_off(store,int(data['id']),data.get('reason',''),actor)
            elif path=='/api/series-preview':
                prune_previews();preview=store.series_preview(data);token=secrets.token_urlsafe(20)
                PREVIEWS[token]={'kind':'series','actor':actor,'created':time.time(),'preview':preview,'request':data}
                result={'token':token,**preview}
            elif path=='/api/series':
                # Festgeschrieben wird genau die geprüfte Vorschau; der Schlüssel gilt nur einmal (kein Doppelklick-Risiko)
                p=PREVIEWS.pop(str(data.get('token','')),None)
                if not p or p['kind']!='series' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen oder bereits festgeschrieben. Bitte die Rechnungsliste prüfen und ggf. neu beginnen.')
                try: result={'ids':store.issue_series(p['request'],actor)}
                except Exception:
                    PREVIEWS[str(data['token'])]=p  # nichts festgeschrieben → Vorschau bleibt gültig
                    raise
            elif path=='/api/mail-password':
                cfg=store.config();where=geheimnis.save(cfg.get('smtp_user'),data.get('password',''),data.get('remember') is not False)
                with store.db: store.audit(actor,'Passwort für E-Mail-Versand hinterlegt',{'ort':where})
                result={'where':where}
            elif path=='/api/mail-forget':
                geheimnis.forget(store.config().get('smtp_user'))
                with store.db: store.audit(actor,'Passwort für E-Mail-Versand entfernt',{})
            elif path=='/api/mail-check': versand.check(store.config())
            elif path=='/api/mail-test':
                cfg=store.config();inv=preview_invoice(store,data,actor);to=versand.send_test(cfg,inv)
                with store.db: store.audit(actor,'Testmail gesendet',{'an':to,'rechnung':inv['number']})
                result={'to':to}
            elif path=='/api/mail-send': result=versand.start(DB,data['ids'],actor,data.get('resend') is True,data.get('kind','invoice'))
            elif path=='/api/mail-cancel': versand.cancel();result=versand.status()
            elif path=='/api/dues': result={'ids':store.issue_dues(int(data['year']),data['ids'],actor)}
            elif path=='/api/invoice': result={'id':store.issue_normal(data,actor)}
            elif path=='/api/cancel-invoice': store.reverse_invoice(int(data['id']),actor)
            elif path=='/api/preview-bank':
                raw=base64.b64decode(data['content'],validate=True)
                if len(raw)>10_000_000: raise ValueError('Datei zu groß.')
                preview=store.preview_bank(raw,Path(data['filename']).name);token=secrets.token_urlsafe(20)
                PREVIEWS[token]={'kind':'bank','actor':actor,'created':time.time(),'preview':preview,'raw':raw}
                result={'token':token,**preview}
            elif path=='/api/preview-master':
                raw=base64.b64decode(data['content'],validate=True)
                if len(raw)>10_000_000: raise ValueError('Datei zu groß.')
                preview=uebernahme.preview(store,raw,Path(data['filename']).name);token=secrets.token_urlsafe(20)
                PREVIEWS[token]={'kind':'master','actor':actor,'created':time.time(),'preview':preview,'raw':raw}
                result={'token':token,'filename':preview['filename'],'sheets':preview['sheets'],'summary':preview['summary'],'notes':preview['notes'],'rows':[{k:r.get(k) for k in MASTER_ROW_FIELDS} for r in preview['rows']]}
            elif path=='/api/import-master':
                p=PREVIEWS.get(data['token'])
                if not p or p['kind']!='master' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Vorschau abgelaufen. Datei erneut auswählen.')
                saved=sicherung.named_backup(DB,backups_dir()/'Vor Änderungen','vor-excel-uebernahme')
                keep_original(p['preview'],p['raw'])
                result={'counts':uebernahme.execute(store,p['preview'],actor),'backup':saved.name};del PREVIEWS[data['token']]
            elif path=='/api/import-bank':
                p=PREVIEWS.get(data['token'])
                if not p or p['kind']!='bank' or p['actor']!=actor or time.time()-p['created']>1800: raise ValueError('Importvorschau abgelaufen. Datei erneut auswählen.')
                keep_original(p['preview'],p['raw'])
                result={'count':store.import_bank(p['preview'],data['selected'],actor)};del PREVIEWS[data['token']]
            elif path=='/api/book': store.book(int(data['id']),data['parts'],actor,data.get('learn') is True,data.get('confirm_no_bill') is True)
            elif path=='/api/park': workflow.park(store,int(data['id']),data.get('reason',''),actor)
            elif path=='/api/forget': workflow.forget(store,int(data['id']),actor)
            elif path=='/api/reverse-bank': store.reverse_bank(int(data['id']),actor)
            elif path=='/api/budget': kvbudget.set_plan(store,int(data['year']),data.get('kv'),data.get('amount'),actor)   # ältere Schnittstelle: ein Planwert je KV
            else: return self.send(404,{'error':'Nicht gefunden.'})
            self.send(200,result)
        except sqlite3.IntegrityError: self.send(400,{'error':'Datensatz bereits vorhanden oder Zuordnung ungültig. Es wurde nichts gebucht.'})
        except (csv.Error,OverflowError,zipfile.BadZipFile,EOFError):   # beschädigte oder ungewöhnliche Importdatei
            self.send(400,{'error':'Die Datei ist nicht lesbar oder beschädigt. Bitte das Original erneut exportieren.'})
        except mitglieder.PasswordNeeded as e: self.send(400,{'error':str(e),'password':e.state})   # Oberfläche fragt das Kennwort ab
        except (ValueError,KeyError,TypeError) as e: self.send(400,{'error':str(e)})
        except Exception:
            log_error('POST '+path);self.send(500,{'error':'Interner Fehler. Die Aktion konnte nicht abgeschlossen werden.'})
        finally: store.db.close()

def new_login_code():
    """Einmaliger Anmeldecode (60 Sekunden gültig) – nur nach Prüfung durch macOS bzw. beim Start im Terminal."""
    now=time.time();code=secrets.token_urlsafe(32)
    with LOGIN_LOCK:
        for old in [c for c,v in LOGIN_CODES.items() if now-v['created']>LOGIN_CODE_SECONDS]: LOGIN_CODES.pop(old,None)
        LOGIN_CODES[code]={'created':now}
    return code

def mac_user_name():
    """Name des Mac-Benutzers, unter dem der Server läuft (vollständiger Name, sonst Kurzname) –
    wird beim ersten Start als Name im Änderungsprotokoll übernommen."""
    name=''
    try:
        import pwd
        name=pwd.getpwuid(os.getuid()).pw_gecos.split(',')[0].strip()
    except Exception: pass
    if not name:
        try: name=getpass.getuser()
        except Exception: pass
    return ' '.join(str(name or '').split())[:80] or 'Schatzmeister'

def write_private(name,text):
    """Datei im Datenordner anlegen, die nur der Benutzer lesen kann."""
    target=DATA/name
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(text)
    os.chmod(target,0o600)

def write_start_url(url):
    """Adresse und Steuerschlüssel (Beenden, Anmeldecode) für den Mac-Starter hinterlegen."""
    write_private('.start-url',url)
    write_private('.steuerung',STOP_TOKEN)

def env_list(name): return [x.strip() for x in os.environ.get(name,'').replace(';',',').split(',') if x.strip()]

def run_backups(backups):
    """Tägliche Sicherung der Datenbank und neue Belege spiegeln (beim Start und im Servermodus stündlich geprüft)."""
    try:
        saved=sicherung.daily_backup(DB,backups/'Automatisch')
        if saved: log('Automatische Sicherung: '+str(saved))
        copied=sicherung.mirror_files(DATA/'Belege',backups/'Belege')
        if copied: log(f'{copied} neue Belege gesichert.')
    except Exception as e:
        log('Automatische Sicherung fehlgeschlagen: '+str(e))

def check_bind(host):
    """Servermodus: nur eine bestimmte Adresse (127.0.0.1 hinter Tailscale Serve oder die Tailscale-Adresse 100.x),
    nie alle Netzwerkkarten – so ist das Programm aus dem Internet nicht erreichbar."""
    try: ip=ipaddress.ip_address(host)
    except ValueError: raise SystemExit(f'Ungültige Adresse für --host: {host!r} (z. B. 127.0.0.1)')
    if ip.is_unspecified: raise SystemExit('--host 0.0.0.0 bzw. :: ist nicht erlaubt: Bitte 127.0.0.1 (mit Tailscale Serve) oder die Tailscale-Adresse angeben.')
    if not (ip.is_loopback or any(ip in net for net in TAILNET)):
        raise SystemExit(f'--host {ip} ist weder 127.0.0.1 noch eine Tailscale-Adresse (100.64.0.0/10, fd7a:115c:a1e0::/48) – '
                         'so wäre das Programm ohne Verschlüsselung im lokalen Netz oder Internet erreichbar.')
    return str(ip)

def main(argv=None):
    global DATA,DB,BACKUPS,MODE,ADDRESSES,THROTTLE
    parser=argparse.ArgumentParser(description=f'{APP_NAME} {VERSION}')
    parser.add_argument('--no-browser',action='store_true');parser.add_argument('--port',type=int,default=int(os.environ.get('JU_FINANZEN_PORT') or 8769))
    parser.add_argument('--data',help='Ordner für Datenbank und importierte Kontoauszüge')
    parser.add_argument('--backups',help='Ordner für automatische Sicherungen (Standard: Sicherungen neben dem Datenordner)')
    parser.add_argument('--modus',choices=('mac','server'),default=os.environ.get('JU_FINANZEN_MODUS') or 'mac',help='server = Anmeldung mit Passwort, Betrieb als Dienst')
    parser.add_argument('--host',default=os.environ.get('JU_FINANZEN_HOST') or '127.0.0.1',help='Servermodus: Adresse, an die der Server bindet (Standard 127.0.0.1)')
    parser.add_argument('--adresse',action='append',default=None,help='Servermodus: Adresse der Oberfläche, z. B. https://ju-finanzen.beispiel.ts.net (mehrfach möglich)')
    args=parser.parse_args(argv)
    MODE=args.modus
    if args.data: DATA=Path(args.data); DB=DATA/'finanzen.sqlite3'
    BACKUPS=Path(args.backups) if args.backups else (Path(os.environ['JU_FINANZEN_SICHERUNGEN']) if os.environ.get('JU_FINANZEN_SICHERUNGEN') else None)
    host='127.0.0.1'
    if server_mode():
        THROTTLE=zugang.Throttle(path=DATA/'.anmeldesperre')   # Fehlversuche überstehen einen Neustart
        try: ADDRESSES=sorted({clean_address(a) for a in (args.adresse if args.adresse is not None else env_list('JU_FINANZEN_ADRESSEN'))})
        except ValueError as e: print(str(e),flush=True);return 4
        host=check_bind(args.host)
    backups=backups_dir()
    DATA.mkdir(parents=True,exist_ok=True);(DATA/'Importe').mkdir(exist_ok=True)
    print(f'{APP_NAME} {VERSION} – Start {time.strftime("%Y-%m-%d %H:%M:%S")} – Daten: {DATA}'+(' – Serverbetrieb' if server_mode() else ''),flush=True)
    run_backups(backups)
    marker=DATA/'.version'
    try: last=marker.read_text(encoding='utf-8').strip()
    except OSError: last=''
    if DB.exists() and last!=VERSION:
        # Neue Programmversion: vor möglichen (additiven) Datenbankänderungen immer sichern
        try: print('Sicherung vor Version '+VERSION+': '+str(sicherung.named_backup(DB,backups/'Vor Änderungen',f'vor-version-{VERSION}')),flush=True)
        except Exception as e:
            print('Sicherung vor dem Update fehlgeschlagen – Start abgebrochen, um die Daten zu schützen: '+str(e),flush=True)
            return 3
    Store(DB).db.close()
    write_private('.version',VERSION)
    try:
        server=(LocalServer6 if ':' in host else LocalServer)((host,args.port),Handler)
    except OSError:
        print('Das Programm läuft bereits oder der Port ist belegt.',flush=True)
        return 2
    if server_mode():
        stop=threading.Event()
        def every_hour():
            while not stop.wait(3600): run_backups(backups)
        threading.Thread(target=every_hour,daemon=True,name='sicherung').start()
        # systemd beendet mit SIGTERM: laufenden E-Mail-Versand nach der aktuellen Nachricht anhalten (wird protokolliert),
        # höchstens 25 s warten (TimeoutStopSec=30), dann den Server herunterfahren
        def terminate(*_):
            def run():
                if versand.busy():
                    log('Beenden: E-Mail-Versand wird nach der aktuellen Nachricht angehalten.')
                    versand.cancel()
                    for _ in range(250):
                        if not versand.busy(): break
                        time.sleep(0.1)
                server.shutdown()
            threading.Thread(target=run,daemon=True).start()
        signal.signal(signal.SIGTERM,terminate)
        store=Store(DB)
        try: ready=any(zugang.is_set(u['password']) for u in store.rows('SELECT password FROM users'))
        finally: store.db.close()
        print(f'Serverbetrieb auf {host}:{args.port} – Adressen: {", ".join(ADDRESSES) or "nur lokal (127.0.0.1)"}',flush=True)
        if not ready: print('Noch kein Passwort gesetzt: auf dem Server „sudo ju-finanzen passwort <Name>“ ausführen.',flush=True)
    else:
        url=f'http://127.0.0.1:{args.port}/'
        write_start_url(url)
        print('Läuft unter http://127.0.0.1:'+str(args.port)+'/ – Beenden über die Schaltfläche „Beenden“ oder Strg+C.',flush=True)
        if not args.no_browser: webbrowser.open(url+'?login='+new_login_code())   # Start im Terminal (Entwicklung)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        if server_mode(): stop.set()
        server.server_close()
    print('Beendet '+time.strftime('%Y-%m-%d %H:%M:%S'),flush=True)
    return 0

if __name__=='__main__': sys.exit(main())
