// Einstieg: Anmeldung, Gerüst (Seitenleiste) und Seitenwechsel.
import { api, setCsrf, onUnauthorized, onConfirm, onLocked } from './api.js';
import { html, mount } from './html.js';
import { store, onChange, refresh, go, can, yearBank } from './store.js';
import { dialog, overlay, icon, safe, toast, closeTop } from './ui.js';
import { dateTime } from './format.js';
import { overview } from './views/overview.js';
import { bank, bankKeys } from './views/bank.js';
import { cash } from './views/cash.js';
import { budgets } from './views/budgets.js';
import { rpj } from './views/rpj.js';
import { members } from './views/members.js';
import { cases } from './views/klaerfaelle.js';
import { karte, leaveMap } from './views/karte.js';
import { dues } from './views/dues.js';
import { auswertung } from './views/auswertung.js';
import { invoices } from './views/invoices.js';
import { bills } from './views/bills.js';
import { openItems } from './views/open-items.js';
import { journal } from './views/journal.js';
import { konten } from './views/konten.js';
import { settings } from './views/settings.js';
import { benutzer } from './views/benutzer.js';
import { sicherheit } from './views/sicherheit.js';

const root = document.getElementById('root');
const app = () => window.webkit?.messageHandlers?.ju;   // eigenes Programmfenster der Mac-App (mac/fenster.py)
const server = () => store.mode === 'server';           // Serverbetrieb (Ubuntu hinter Tailscale): Anmeldung mit Passwort

// Aufbau nach Aufgabenbereichen (0.31, Wunsch Lennert Gust 10.11.2026): Mitgliederverwaltung, Buchhaltung, Verband, Verwaltung –
// später sieht jede Rolle nur ihren Bereich. Start = Aufgaben je Bereich (ohne Gruppenüberschrift).
const PAGES = [
  // area: Bereich, für den die Person mindestens „lesen“ braucht (rollen.py); ohne Angabe für alle Angemeldeten
  { id: 'overview', label: 'Start', icon: 'home', view: overview, group: '' },
  { id: 'members', label: 'Mitglieder', icon: 'users', view: members, group: 'Mitgliederverwaltung', area: 'mitglieder' },
  { id: 'cases', label: 'Klärfälle', icon: 'alert', view: cases, group: 'Mitgliederverwaltung', area: 'mitglieder' },
  { id: 'map', label: 'Karte', icon: 'map', view: karte, group: 'Mitgliederverwaltung', area: 'mitglieder' },
  { id: 'bank', label: 'Kontoauszüge', icon: 'bank', view: bank, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'dues', label: 'Beitragseingänge', icon: 'coins', view: dues, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'cash', label: 'Kasse', icon: 'cash', view: cash, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'invoices', label: 'Rechnungen', icon: 'file', view: invoices, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'bills', label: 'Eingangsrechnungen', icon: 'inbox', view: bills, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'openitems', label: 'Offene Posten', icon: 'scale', view: openItems, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'journal', label: 'Buchungsjournal', icon: 'book', view: journal, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'konten', label: 'Konten', icon: 'list', view: konten, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'budgets', label: 'Haushalt & Budgets', icon: 'chart', view: budgets, group: 'Verband', area: 'verband' },
  { id: 'rpj', label: 'RPJ-Mittel', icon: 'layers', view: rpj, group: 'Verband', area: 'verband' },
  { id: 'auswertung', label: 'Auswertung', icon: 'trend', view: auswertung, group: 'Verband', area: 'auswertung' },
  { id: 'users', label: 'Benutzer & Rollen', icon: 'users', view: benutzer, group: 'Verwaltung', area: 'verwaltung', serverOnly: true },
  { id: 'security', label: 'Sicherheit', icon: 'shield', view: sicherheit, group: 'Verwaltung', area: 'sicherheit', serverOnly: true },
  // Einstellungen: im Serverbetrieb für alle (eigenes Passwort ändern), die Reiter richten sich nach dem Recht „Einstellungen“
  { id: 'settings', label: 'Einstellungen', icon: 'settings', view: settings, group: 'Verwaltung', area: 'einstellungen', always: 'server' },
];
const GROUPS = [...new Set(PAGES.map((p) => p.group))];
/** Seiten, die die angemeldete Person sieht (der Server prüft jede Anfrage unabhängig davon). */
const visiblePages = () => PAGES.filter((p) => (!p.serverOnly || server()) && (!p.area || can(p.area) || (p.always === 'server' && server())));

function years() {
  const now = new Date().getFullYear();
  const list = [];
  const first = store.data?.data_start ? Number(store.data.data_start.slice(0, 4)) : 2024;   // Datenbeginn (ältere Jahre gelöscht)
  for (let y = first; y <= Math.max(now + 1, store.year); y++) list.push(y);
  return list;
}

// Kompakte Ansicht (Seitenleiste als Menü): im Mac-Fenster bis 900 px wie bisher, im Browser (Serverbetrieb) bis 1100 px
const NARROW = { mac: window.matchMedia('(max-width: 900px)'), server: window.matchMedia('(max-width: 1100px)') };
function layout() {
  const compact = NARROW[server() ? 'server' : 'mac'].matches;
  document.documentElement.classList.toggle('compact', compact);
  if (!compact) setMenu(false);
}
Object.values(NARROW).forEach((q) => q.addEventListener('change', layout));

// Seitenleiste auf kleinen Bildschirmen als Menü (öffnet über den Knopf in der Kopfzeile)
function setMenu(open) {
  const shell = root.querySelector('.shell');
  if (!shell) return;
  shell.classList.toggle('menu-open', open);
  root.querySelector('#menu-toggle')?.setAttribute('aria-expanded', String(open));
}
const menuOpen = () => !!root.querySelector('.shell.menu-open');

function renderShell() {
  leaveMap();   // die Seite wird neu aufgebaut: eine offene Karte vorher freigeben (sonst bleibt sie über das Fenster erreichbar)
  const pages = visiblePages();
  const page = pages.find((p) => p.id === store.page) || pages[0];
  const openCount = yearBank().filter((b) => b.state === 'open').length;
  const cashOpen = store.data?.cash?.open || 0;
  const rpjOpen = store.data?.rpj?.open || 0;
  const counts = { bank: [openCount, 'offen'], cash: [cashOpen, 'offen'], rpj: [rpjOpen, 'ohne Zuordnung'], cases: [store.data?.cases?.active || 0, 'zu klären'],
    dues: [store.data?.dues_open_all || 0, 'ohne Mitglied'] };
  mount(root, html`<div class="shell">
    <header class="topbar">
      <button type="button" class="btn ghost icon-btn" id="menu-toggle" aria-label="Menü" aria-controls="sidebar" aria-expanded="false">${icon('menu', 22)}</button>
      <span class="topbar-title">${page.label}</span>
      <span class="topbar-year">${store.year}</span>
    </header>
    <div class="scrim" data-menu-close></div>
    <aside class="sidebar" id="sidebar" aria-label="Seitenleiste">
      <img class="logo" src="/JU-Hamburg-Finanzen.svg" alt="Junge Union Hamburg – Finanzen">
      <label class="year-select">Haushaltsjahr
        <select id="year" aria-label="Haushaltsjahr">${years().map((y) => html`<option ${y === store.year ? 'selected' : ''}>${y}</option>`)}</select>
      </label>
      <nav class="nav" aria-label="Hauptnavigation">${GROUPS.filter((g) => pages.some((p) => p.group === g)).map((g) => html`
        ${g ? html`<p class="nav-group">${g}</p>` : ''}
        ${pages.filter((p) => p.group === g).map((p) => html`
        <button type="button" data-page="${p.id}" ${p.id === page.id ? html`aria-current="page"` : ''}>${icon(p.icon)} ${p.label}
          ${counts[p.id]?.[0] ? html`<span class="count" aria-label="${counts[p.id][0]} ${counts[p.id][1]}">${counts[p.id][0]}</span>` : ''}</button>`)}`)}
      </nav>
      <div class="sidebar-foot">
        <div class="user"><span class="avatar" aria-hidden="true">${(store.user || '?').slice(0, 1).toUpperCase()}</span><span class="who">${store.user}</span>
          <button type="button" class="link-button" id="logout">Abmelden</button></div>
        ${server() ? '' : html`<button type="button" class="btn small" id="shutdown">${icon('power', 16)} Programm beenden</button>`}
        <span class="where">${server() ? 'Server' : 'Lokal auf diesem Mac'} · Version ${store.version}</span>
      </div>
    </aside>
    <main class="main" id="main"></main>
  </div>`);
  root.querySelectorAll('[data-page]').forEach((b) => (b.onclick = () => go(b.dataset.page)));
  root.querySelector('#menu-toggle').onclick = () => setMenu(!menuOpen());
  root.querySelector('[data-menu-close]').onclick = () => setMenu(false);
  root.querySelector('#year').onchange = (e) => safe(async () => {
    const before = store.year;
    store.year = Number(e.target.value);
    store.bank.selected = null;
    try {
      await refresh();
    } catch (err) {
      store.year = before;        // Anzeige und Daten bleiben beim bisherigen Jahr
      e.target.value = String(before);
      throw err;
    }
  });
  root.querySelector('#logout').onclick = () => safe(async () => {
    await api('/api/logout', {});
    location.href = '/';
  });
  const stop = root.querySelector('#shutdown');
  if (stop) stop.onclick = shutdown;
  Promise.resolve(page.view(root.querySelector('#main'))).catch((e) => toast(e.message || String(e), true));
}

function shutdown() {
  const el = dialog('Programm beenden', html`<p>Alle Daten bleiben gespeichert. Zum erneuten Öffnen die App „JU Hamburg Finanzen“ unter Programme starten.</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button><button type="button" class="btn dark" data-stop>Jetzt beenden</button></div>`);
  el.querySelector('[data-cancel]').onclick = () => el.close();
  el.querySelector('[data-stop]').onclick = () => safe(async () => {
    await api('/api/shutdown', {});
    el.close();
    if (app()) return app().postMessage('quit');
    mount(root, html`<div class="login"><img src="/JU-Hamburg-Finanzen.svg" alt="Junge Union Hamburg – Finanzen">
      <div class="login-card"><h1>Programm beendet</h1><p class="muted">Deine Daten sind gespeichert. Du kannst dieses Fenster schließen.</p></div></div>`);
  });
}

async function start(user) {
  store.user = user;
  onChange(renderShell);
  await refresh();
}

const LAST_USER = 'ju-finanzen-benutzer';
function lastUser() {
  try { return localStorage.getItem(LAST_USER) || ''; } catch { return ''; }
}

/** Anmeldung im Serverbetrieb: Benutzername und Passwort. */
function passwordLogin(status, notice) {
  mount(root, html`<div class="login">
    <img src="/JU-Hamburg-Finanzen.svg" alt="Junge Union Hamburg – Finanzen">
    <form class="login-card" id="login-form" autocomplete="on">
      <h1>Anmelden</h1>
      ${notice ? html`<p class="notice">${notice}</p>` : ''}
      ${status.configured ? '' : html`<p class="notice warn">Noch kein Passwort eingerichtet. Auf dem Server einmal ausführen:<br><code>sudo ju-finanzen passwort "Vorname Nachname"</code></p>`}
      <label class="field">Benutzername<input name="name" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false" required value="${lastUser()}"></label>
      <label class="field">Passwort<input name="password" type="password" autocomplete="current-password" required></label>
      <p class="login-error" role="alert" hidden></p>
      <button class="btn primary large" type="submit">Anmelden</button>
    </form>
    <p class="small muted">Nur über Tailscale erreichbar · Abmeldung nach 2 Stunden ohne Aktivität</p>
  </div>`);
  const form = root.querySelector('#login-form');
  const error = form.querySelector('.login-error');
  (form.name.value ? form.password : form.name).focus();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    error.hidden = true;
    try {
      const r = await api('/api/login', { name: form.name.value, password: form.password.value });
      try { localStorage.setItem(LAST_USER, r.user); } catch { /* nur Komfort */ }
      setCsrf(r.csrf);
      await boot();   // über den Status: zeigt bei Notfallmodus oder Wartung den Sperrbildschirm
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      form.password.value = '';
      form.password.focus();
      button.disabled = false;
    }
  };
}

async function boot(notice = '') {
  const code = new URLSearchParams(location.search).get('login');
  if (code) {
    history.replaceState({}, '', '/');                     // Code ist einmalig – nicht in Verlauf oder Neuladen
    try {
      setCsrf((await api('/api/login-device', { code })).csrf);
    } catch (e) {
      toast(e.message, true);
    }
  }
  const status = await api('/api/status');
  store.version = status.version || '';
  store.mode = status.mode || 'mac';
  layout();
  setCsrf(status.csrf);
  if (status.user && !status.sperre?.fuer_mich) return start(status.user);
  onChange(() => {});
  if (status.user) return lockScreen(status.sperre);
  if (status.auth === 'password') return passwordLogin(status, notice);
  mount(root, html`<div class="login">
    <img src="/JU-Hamburg-Finanzen.svg" alt="Junge Union Hamburg – Finanzen">
    <div class="login-card">
      <div><h1>Anmelden</h1>
        <p class="muted" style="margin-top:4px">Mit Touch ID, Apple Watch oder dem Passwort deines Mac.</p></div>
      ${app() ? html`<button class="btn primary large" type="button" id="login-mac">Anmelden</button>`
        : html`<p class="notice">Bitte die App „JU Hamburg Finanzen“ öffnen. Für die Anzeige im Browser die App mit gedrückter Wahltaste (⌥) öffnen.</p>`}
    </div>
    <p class="small muted">Nur auf diesem Mac erreichbar · keine Cloud-Verbindung</p>
  </div>`);
  root.querySelector('#login-mac')?.addEventListener('click', () => app().postMessage('login'));
}

/** Notfallmodus oder Wartung (ab 0.35): statt der Oberfläche nur Hinweis, Abmelden und erneut prüfen. */
function lockScreen(sperre) {
  const emergency = sperre.art === 'notfall';
  mount(root, html`<div class="login">
    <img src="/JU-Hamburg-Finanzen.svg" alt="Junge Union Hamburg – Finanzen">
    <div class="login-card">
      <div><h1>${emergency ? 'Notfallmodus aktiv' : 'Wartung'}</h1>
        <p class="muted" style="margin-top:4px">${emergency ? 'Das Programm ist vorübergehend gesperrt. Deine Daten bleiben unverändert. Bitte wende dich an die Verwaltung.'
          : sperre.text || 'Das Programm wird gerade gewartet und ist bald wieder für dich erreichbar.'}</p></div>
      ${sperre.grund || sperre.von ? html`<p class="notice warn">${sperre.grund ? html`Grund: ${sperre.grund}` : ''}${sperre.grund && sperre.von ? html`<br>` : ''}${sperre.von ? html`Ausgelöst von ${sperre.von}` : ''}</p>` : ''}
      ${sperre.seit ? html`<p class="small muted">Seit ${dateTime(sperre.seit)}</p>` : ''}
      <button class="btn primary large" type="button" id="lock-retry">Erneut prüfen</button>
      <button class="btn large" type="button" id="lock-logout">Abmelden</button>
    </div>
  </div>`);
  root.querySelector('#lock-retry').onclick = () => safe(() => boot());
  root.querySelector('#lock-logout').onclick = () => safe(async () => {
    await api('/api/logout', {});
    await boot();
  });
}

/** Kritische Aktion (Serverbetrieb): Passwort erneut abfragen. Liegt als zweite Ebene über einem offenen Dialog, damit dessen Eingaben bleiben.
 *  Das Passwort geht nur an /api/bestaetigen und wird sofort aus dem Feld gelöscht. */
function askPassword() {
  return new Promise((resolve, reject) => {
    const el = overlay('Zur Bestätigung dein Passwort', html`<form id="confirm-form" style="display:flex;flex-direction:column;gap:14px">
      <p class="small muted">Für diese Aktion fragt das Programm noch einmal nach deinem Passwort.</p>
      <label class="field">Passwort<input name="password" type="password" autocomplete="current-password" required></label>
      <p class="login-error" role="alert" hidden></p>
      <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Bestätigen</button></div>
    </form>`, { wide: false });
    const form = el.querySelector('#confirm-form');
    const error = form.querySelector('.login-error');
    let done = false;
    el.addEventListener('close', () => {
      form.password.value = '';
      if (done) resolve(); else reject(new Error('Abgebrochen.'));
    }, { once: true });
    form.password.focus();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const password = form.password.value;
      form.password.value = '';
      const button = form.querySelector('button[type=submit]');
      button.disabled = true;
      try {
        await api('/api/bestaetigen', { password });
        done = true;
        el.close();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        form.password.focus();
      } finally {
        button.disabled = false;
      }
    };
  });
}
let asking = null;   // mehrere Anfragen zugleich: nur einmal fragen
onConfirm(() => (asking ||= askPassword().finally(() => { asking = null; })));

// Sitzung abgelaufen (Serverbetrieb: nach Untätigkeit oder Passwortwechsel) oder Sperre (Notfall/Wartung):
// offene Dialoge schließen, neu starten (Anmeldung bzw. Sperrbildschirm).
let relogin = false;
function restart(notice = '') {
  if (relogin) return;
  relogin = true;
  while (closeTop()) { /* alle Ebenen schließen */ }
  boot(notice).finally(() => { relogin = false; });
}
onUnauthorized(() => restart('Die Sitzung ist abgelaufen. Bitte erneut anmelden.'));
onLocked(() => restart());

// Esc schließt immer die oberste Ebene (Liste/Vorschau vor Dialog) – auch im Programmfenster, wo WebKit Esc nicht
// zuverlässig an <dialog> weitergibt. ⌘W ruft im Fenster dieselbe Funktion auf (mac/fenster.py) und schließt nie das Programm.
window.juCloseTop = closeTop;
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || e.isComposing) return;
  if (closeTop() || (menuOpen() && (setMenu(false), true))) {
    e.preventDefault();
    e.stopPropagation();
  }
}, true);

// Tastenkürzel im Buchungsplatz (nur ohne offenen Dialog und außerhalb von Eingabefeldern).
document.addEventListener('keydown', (e) => {
  if (!['bank', 'cash'].includes(store.page) || document.getElementById('modal').open || document.getElementById('preview').open) return;
  if (store.page === 'cash' && (store.cash.filter === 'book' || !store.data?.cash?.start)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (['input', 'select', 'textarea'].includes(tag)) {
    if (e.key === 'Escape') e.target.blur();
    return;
  }
  bankKeys(e);
});

boot().catch((e) => {
  root.textContent = 'Programm konnte nicht geladen werden: ' + e.message;
  toast(e.message, true);
});
