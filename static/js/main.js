// Einstieg: Anmeldung, Gerüst (Seitenleiste) und Seitenwechsel.
import { api, setCsrf, onUnauthorized, onConfirm, onLocked } from './api.js';
import { html, mount } from './html.js';
import { store, onChange, refresh, go, can, yearBank } from './store.js';
import { overlay, icon, safe, toast, closeTop } from './ui.js';
import { dateTime } from './format.js';
import { overview } from './views/overview.js';
import { bankKeys } from './views/bank.js';
import { kontoumsaetze, umsatzKeys } from './views/umsaetze.js';
import { cash } from './views/cash.js';
import { budgets } from './views/budgets.js';
import { rpj } from './views/rpj.js';
import { members } from './views/members.js';
import { mitgliederUeberblick } from './views/mitglieder-ueberblick.js';
import { kenntnis } from './views/kenntnis.js';
import { cases } from './views/klaerfaelle.js';
import { karte, leaveMap } from './views/karte.js';
import { wahlrecht } from './views/wahlrecht.js';
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
import { regelwerk } from './views/regelwerk.js';
import { darstellungWahl, bindDarstellung, darstellungDialog } from './darstellung.js';

const root = document.getElementById('root');

// Aufbau nach Aufgabenbereichen (0.31, Wunsch Lennert Gust 10.11.2026): Mitgliederverwaltung, Buchhaltung, Verband, Verwaltung –
// später sieht jede Rolle nur ihren Bereich. Start = Aufgaben je Bereich (ohne Gruppenüberschrift).
const PAGES = [
  // area: Bereich, für den die Person mindestens „lesen“ braucht (rollen.py); ohne Angabe für alle Angemeldeten
  { id: 'overview', label: 'Start', icon: 'home', view: overview, group: '' },
  { id: 'mv-overview', label: 'Cockpit', icon: 'layers', view: mitgliederUeberblick, group: 'Mitgliederverwaltung', area: 'mitglieder' },
  { id: 'kenntnis', label: 'Zur Kenntnis', icon: 'inbox', view: kenntnis, group: 'Mitgliederverwaltung', area: 'mitglieder' },   // ab 0.54
  { id: 'members', label: 'Mitglieder', icon: 'users', view: members, group: 'Mitgliederverwaltung', area: 'mitglieder' },
  { id: 'auswertung', label: 'Auswertung', icon: 'trend', view: auswertung, group: 'Mitgliederverwaltung', area: 'auswertung' },   // Auswertung der Mitgliederdaten: ab 0.54 bei der Mitgliederverwaltung
  { id: 'cases', label: 'Klärfälle', icon: 'alert', view: cases, group: 'Mitgliederverwaltung', area: 'mitglieder' },
  { id: 'map', label: 'Karte', icon: 'map', view: karte, group: 'Mitgliederverwaltung', area: 'mitglieder' },
  { id: 'wahl', label: 'Wahlberechtigung', icon: 'check', view: wahlrecht, group: 'Mitgliederverwaltung', area: 'mitglieder' },
  { id: 'bank', label: 'Kontoumsätze', icon: 'bank', view: kontoumsaetze, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'dues', label: 'Beitragseingänge', icon: 'coins', view: dues, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'cash', label: 'Kasse', icon: 'cash', view: cash, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'invoices', label: 'Rechnungen', icon: 'file', view: invoices, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'bills', label: 'Eingangsrechnungen', icon: 'inbox', view: bills, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'openitems', label: 'Offene Posten', icon: 'scale', view: openItems, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'journal', label: 'Buchungsjournal', icon: 'book', view: journal, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'konten', label: 'Konten', icon: 'list', view: konten, group: 'Buchhaltung', area: 'buchhaltung' },
  { id: 'budgets', label: 'Haushalt & Budgets', icon: 'chart', view: budgets, group: 'Verband', area: 'verband' },
  { id: 'rpj', label: 'RPJ-Mittel', icon: 'layers', view: rpj, group: 'Verband', area: 'verband' },
  { id: 'regelwerk', label: 'Regelwerk', icon: 'book', view: regelwerk, group: 'Verband' },   // für alle Angemeldeten (0.43)
  { id: 'users', label: 'Benutzer & Rollen', icon: 'users', view: benutzer, group: 'Verwaltung', area: 'verwaltung' },
  { id: 'security', label: 'Sicherheit', icon: 'shield', view: sicherheit, group: 'Verwaltung', area: 'sicherheit' },
  // Einstellungen: für alle (eigenes Passwort ändern), die Reiter richten sich nach dem Recht „Einstellungen“
  { id: 'settings', label: 'Einstellungen', icon: 'settings', view: settings, group: 'Verwaltung', area: 'einstellungen', always: true },
];
const GROUPS = [...new Set(PAGES.map((p) => p.group))];
/** Seiten, die die angemeldete Person sieht (der Server prüft jede Anfrage unabhängig davon). */
const visiblePages = () => PAGES.filter((p) => (!p.area || can(p.area) || p.always) && !(p.id === 'kenntnis' && store.data?.kenntnis?.an === false));   // „Zur Kenntnis“ nur, wenn eingeschaltet

function years() {
  const now = new Date().getFullYear();
  const list = [];
  const first = store.data?.data_start ? Number(store.data.data_start.slice(0, 4)) : 2024;   // Datenbeginn (ältere Jahre gelöscht)
  for (let y = first; y <= Math.max(now + 1, store.year); y++) list.push(y);
  return list;
}

// Kompakte Ansicht (Seitenleiste als Menü) bis 1100 px
const NARROW = window.matchMedia('(max-width: 1100px)');
function layout() {
  document.documentElement.classList.toggle('compact', NARROW.matches);
  if (!NARROW.matches) setMenu(false);
}
NARROW.addEventListener('change', layout);

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
  // wie der Satz über den Kontoumsätzen: offen im Jahr und geparkt (aus allen Jahren)
  const openCount = yearBank().filter((b) => b.state === 'open').length + (store.workflow?.parked || []).filter((b) => (b.ledger || 'bank') === 'bank').length;
  const cashOpen = store.data?.cash?.open || 0;
  const rpjOpen = store.data?.rpj?.open || 0;
  const counts = { bank: [openCount, 'nicht verbucht'], cash: [cashOpen, 'offen'], rpj: [rpjOpen, 'ohne Zuordnung'], cases: [store.data?.cases?.active || 0, 'zu klären'], kenntnis: [store.data?.kenntnis?.punkte || 0, 'zur Kenntnis'],
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
        <button type="button" class="link-button ds-open" id="darstellung">${icon('eye', 16)} Darstellung</button>
        <span class="where">Server · Version ${store.version}</span>
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
  root.querySelector('#darstellung').onclick = () => { setMenu(false); darstellungDialog(); };
  Promise.resolve(page.view(root.querySelector('#main'))).catch((e) => toast(e.message || String(e), true));
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

// Rückkehr von Keycloak mit Fehler (/?anmeldung=<Kürzel>, app.py keycloak_rueckkehr) – nur feste Texte, nie der Wert aus der Adresse
const KEYCLOAK_ERRORS = {
  abgelaufen: 'Die Anmeldung ist abgelaufen oder wurde schon verwendet. Bitte erneut anmelden.',
  gesperrt: 'Zu viele Fehlversuche von diesem Gerät. Bitte einige Minuten warten.',
  ueberlastet: 'Gerade laufen zu viele Anmeldungen gleichzeitig. Bitte in einigen Minuten erneut versuchen.',
  abgebrochen: 'Die Anmeldung wurde abgebrochen.',
  fehler: 'Die Anmeldung über das JU-Konto hat nicht geklappt. Bitte erneut versuchen.',
  'kein-zugang': 'Dein JU-Konto hat keinen gültigen Zugang zu diesem Programm. Bitte wende dich an die Verwaltung.',
  app: 'Für dein Konto ist die Authenticator-App Pflicht, aber noch nicht eingerichtet. Bitte erneut anmelden und die App einrichten oder die Verwaltung fragen.',
};

/** Anmeldung im Serverbetrieb: Benutzername und Passwort – im Keycloak-Betrieb „Mit JU-Konto anmelden“ und das Passwort nur als Notzugang. */
function passwordLogin(status, notice) {
  const keycloak = status.auth === 'keycloak';
  const fields = html`<label class="field">Benutzername<input name="name" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false" required value="${lastUser()}"></label>
      <label class="field">Passwort<input name="password" type="password" autocomplete="current-password" required></label>
      <p class="login-error" role="alert" hidden></p>
      <button class="btn ${keycloak ? '' : 'primary'} large" type="submit">${keycloak ? 'Mit Notzugang anmelden' : 'Anmelden'}</button>`;
  mount(root, html`<div class="login">
    <img src="/JU-Hamburg-Finanzen.svg" alt="Junge Union Hamburg – Finanzen">
    ${keycloak ? html`<div class="login-card">
      <h1>Anmelden</h1>
      ${notice ? html`<p class="notice warn">${notice}</p>` : ''}
      <a class="btn primary large" href="/api/keycloak/start" id="login-keycloak">Mit JU-Konto anmelden</a>
      <details><summary class="small muted">Notzugang (Administrator)</summary>
        <form id="login-form" autocomplete="on" style="display:flex;flex-direction:column;gap:16px;margin-top:12px">
          <p class="small muted">Nur nach Freischaltung auf dem Server (<code>sudo ju-finanzen notzugang</code>) – wenn die Anmeldung mit dem JU-Konto nicht funktioniert, mit dem Passwort der Verwaltung.</p>
          ${fields}</form></details>
      ${darstellungWahl()}</div>`
    : html`<form class="login-card" id="login-form" autocomplete="on">
      <h1>Anmelden</h1>
      ${notice ? html`<p class="notice">${notice}</p>` : ''}
      ${status.configured ? '' : html`<p class="notice warn">Noch kein Passwort eingerichtet. Auf dem Server einmal ausführen:<br><code>sudo ju-finanzen passwort "Vorname Nachname"</code></p>`}
      ${fields}
      ${darstellungWahl()}
    </form>`}
    <p class="small muted">Nur über Tailscale erreichbar · Abmeldung nach 2 Stunden ohne Aktivität</p>
  </div>`);
  bindDarstellung(root);   // Darstellung schon vor der Anmeldung wählen (0.48) – gilt sofort, auch für die Seite danach
  const form = root.querySelector('#login-form');
  const error = form.querySelector('.login-error');
  if (!keycloak) (form.name.value ? form.password : form.name).focus();
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
  const params = new URLSearchParams(location.search);
  // Rückkehr von Keycloak: Fehlerkürzel bzw. Bestätigung einmal auswerten, dann aus der Adresse nehmen
  const kcError = params.get('anmeldung');
  const confirmed = params.has('bestaetigt');
  if (kcError || confirmed) history.replaceState({}, '', '/');
  if (kcError) notice = KEYCLOAK_ERRORS[kcError] || KEYCLOAK_ERRORS.fehler;
  const status = await api('/api/status');
  store.version = status.version || '';
  store.auth = status.auth || '';
  store.passwortLokal = !!status.passwort_lokal;
  layout();
  setCsrf(status.csrf);
  if (confirmed && status.user) toast('Bestätigt. Bitte die Aktion jetzt innerhalb von 5 Minuten erneut ausführen.');
  if (status.user && !status.sperre?.fuer_mich) return start(status.user);
  onChange(() => {});
  if (status.user) return lockScreen(status.sperre);
  return passwordLogin(status, notice);
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
/** Kritische Aktion im Keycloak-Betrieb (ohne Notzugang): zur Bestätigung erneut mit dem JU-Konto anmelden. Die Seite wird dabei
 *  verlassen; nach der Rückkehr gilt die Bestätigung 5 Minuten, die Aktion wird dann erneut ausgeführt. */
function askKeycloak() {
  return new Promise((resolve, reject) => {
    const el = overlay('Bestätigen', html`<div style="display:flex;flex-direction:column;gap:14px">
      <p class="small muted">Für diese Aktion meldest du dich zur Bestätigung noch einmal mit deinem JU-Konto an. Danach bist du wieder hier und
        führst die Aktion innerhalb von 5 Minuten erneut aus. Nicht gespeicherte Eingaben gehen dabei verloren.</p>
      <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button>
        <a class="btn primary" href="/api/keycloak/start?aktion=bestaetigen">Mit JU-Konto bestätigen</a></div></div>`, { wide: false });
    el.addEventListener('close', () => reject(new Error('Abgebrochen.')), { once: true });
  });
}
let asking = null;   // mehrere Anfragen zugleich: nur einmal fragen
onConfirm(() => (asking ||= (store.auth === 'keycloak' && !store.passwortLokal ? askKeycloak() : askPassword()).finally(() => { asking = null; })));

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

// Esc schließt immer die oberste Ebene (Liste/Vorschau vor Dialog)
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
  if (store.page === 'bank') umsatzKeys(e); else bankKeys(e);
});

boot().catch((e) => {
  root.textContent = 'Programm konnte nicht geladen werden: ' + e.message;
  toast(e.message, true);
});
