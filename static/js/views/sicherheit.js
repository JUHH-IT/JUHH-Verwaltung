// Verwaltung → Sicherheit (ab 0.35, nur Serverbetrieb): Lagebild, Ereignisse, Notfall & Wartung, Sitzungen & Konten, System.
// Nur Anzeige und Auslöser – der Server prüft jede Anfrage selbst (rollen.py, sicherheit.py) und verlangt bei kritischen
// Aktionen das Passwort erneut (api.onConfirm).
import { api, download } from '../api.js';
import { html, mount } from '../html.js';
import { store, can, go, daysOverdue } from '../store.js';
import { pageHead, icon, safe, toast, dialog, table, badge } from '../ui.js';
import { date, dateTime } from '../format.js';

let tab = 'lagebild';
const filter = { art: '', tage: '30', suche: '' };   // Ereignisse
let loads = 0;                                        // nur die zuletzt gestartete Ladung zeigen

const TABS = [
  ['lagebild', 'Lagebild'],
  ['ereignisse', 'Ereignisse'],
  ['notfall', 'Notfall & Wartung'],
  ['sitzungen', 'Sitzungen & Konten', true],   // true = nur mit „Sicherheit ändern“
  ['system', 'System', true],
];
const tabs = () => TABS.filter(([, , write]) => !write || can('sicherheit', 'schreiben'));

const when = (iso) => (iso ? dateTime(iso) : 'nie');
const n = (value) => Number(value || 0).toLocaleString('de-DE', { maximumFractionDigits: 1 });
const card = (label, value, note, warn = false) => html`<div class="card ${warn ? 'sec-warn' : ''}"><span class="label">${label}</span>
  <span class="value">${value}</span><span class="note">${note}</span></div>`;

// ---- Lagebild ----
function stateText(sperre) {
  if (!sperre) return html`<p>${badge('Normalbetrieb', 'ok')} Kein Notfallmodus, keine Wartung.</p>`;
  const notfall = sperre.art === 'notfall';
  return html`<p>${badge(notfall ? 'Notfallmodus aktiv' : 'Wartung', notfall ? 'danger' : 'warn')}
    seit ${dateTime(sperre.seit)}${sperre.von ? html` · ausgelöst von ${sperre.von}` : ''}</p>
    ${sperre.grund ? html`<p class="small">Grund: ${sperre.grund}</p>` : ''}
    ${sperre.text ? html`<p class="small">Hinweis: ${sperre.text}</p>` : ''}`;
}

function checkSummary(d) {
  if (!d?.verfuegbar) return ['nicht verfügbar', d?.zeit ? `Stand ${dateTime(d.zeit)}` : 'noch nicht geprüft', false];
  const count = (d.geaendert?.length || 0) + (d.fehlend?.length || 0) + (d.zusaetzlich?.length || 0);
  return [d.ok ? 'in Ordnung' : `${count} Befund${count === 1 ? '' : 'e'}`, `${n(d.dateien)} Dateien · ${dateTime(d.zeit)}`, !d.ok];
}

function lagebild(l) {
  const old = !l.letzte_sicherung || daysOverdue(l.letzte_sicherung) > 2;
  const [checkValue, checkNote, checkWarn] = checkSummary(l.dateipruefung);
  return html`<section class="panel sec-state ${l.sperre ? 'sec-alert' : ''}"><h2>Zustand</h2>${stateText(l.sperre)}</section>
    <section class="cards" aria-label="Kennzahlen">
      ${card('Fehlanmeldungen', n(l.fehlanmeldungen_24h), `in 24 Stunden · ${n(l.fehlanmeldungen_7t)} in 7 Tagen`)}
      ${card('Verweigerte Zugriffe', n(l.verweigert_7t), `in 7 Tagen${l.bestaetigung_fehl_7t ? ` · ${n(l.bestaetigung_fehl_7t)} falsche Bestätigungen` : ''}`)}
      ${card('Aktive Sitzungen', n(l.sitzungen), 'angemeldet')}
      ${card('Gesperrte Zugänge', n(l.gesperrt), 'nach Fehlversuchen', l.gesperrt > 0)}
      ${card('Ungenutzte Konten', n(l.konten_ungenutzt), `länger als 90 Tage nicht angemeldet · ${n(l.konten_aktiv)} aktiv`, l.konten_ungenutzt > 0)}
      ${card('Verwaltungsrechte', n(l.konten_privilegiert), 'Konten mit Rechten zur Verwaltung')}
      ${card('Letzte Sicherung', l.letzte_sicherung ? date(l.letzte_sicherung) : 'keine', old ? 'älter als 2 Tage – bitte prüfen' : 'aktuell', old)}
      ${card('Dateiprüfung', checkValue, checkNote, checkWarn)}
    </section>`;
}

// ---- Ereignisse ----
const LABELS = { grund: 'Grund', anzahl: 'Anzahl', beendet: 'beendet', versuche: 'Versuche', name: 'Name', adresse: 'Adresse', pfad: 'Adresse',
  methode: 'Methode', recht: 'Recht', bereich: 'Bereich', stufe: 'Stufe', benutzer: 'Person', person: 'Person', sperre: 'Sperre', rolle: 'Rolle', alle_abmelden: 'alle abgemeldet', text: 'Hinweis' };
/** Einzelheiten kurz in Worten (nie rohes JSON): einfache Werte mit Bezeichnung, Listen als Anzahl bzw. kurze Aufzählung. */
function details(detail) {
  return Object.entries(detail || {}).map(([key, value]) => {
    const label = LABELS[key] || key.replace(/_/g, ' ');
    if (typeof value === 'boolean') return `${label}: ${value ? 'ja' : 'nein'}`;
    if (typeof value === 'number' || typeof value === 'string') return value === '' ? '' : `${label}: ${value}`;
    if (Array.isArray(value)) return value.every((v) => typeof v === 'string') && value.length <= 5 ? `${label}: ${value.join(', ') || '–'}` : `${label}: ${value.length}`;
    return '';
  }).filter(Boolean).join(' · ');
}

const query = () => new URLSearchParams(filter).toString();

function ereignisse(r) {
  const arten = { ...r.arten };
  if (!arten.verwaltung) arten.verwaltung = 'Personen und Rollen';
  return html`<form class="toolbar sec-filter" id="sec-filter">
      <label class="field inline">Art<select name="art"><option value="">alle</option>
        ${Object.entries(arten).map(([key, label]) => html`<option value="${key}" ${filter.art === key ? 'selected' : ''}>${label}</option>`)}</select></label>
      <label class="field inline">Zeitraum<select name="tage">${['7', '30', '90', '365'].map((d) => html`<option value="${d}" ${filter.tage === d ? 'selected' : ''}>${d} Tage</option>`)}</select></label>
      <label class="search">${icon('search', 16)}<span class="sr-only">Ereignisse durchsuchen</span><input name="suche" type="search" placeholder="Person, Gerät, Text" value="${filter.suche}"></label>
      <button type="button" class="btn small" data-csv>${icon('download', 16)} CSV</button>
    </form>
    <section class="panel">${r.ereignisse.length ? table(['Zeit', 'Ereignis', 'Person', 'Gerät', 'Einzelheiten'], r.ereignisse.map((e) => html`<tr>
      <td class="nowrap">${dateTime(e.zeit)}</td><td>${e.text || arten[e.art] || e.art}</td><td>${e.person || '–'}</td>
      <td class="small">${e.geraet || '–'}</td><td class="small">${details(e.detail)}</td></tr>`))
      : html`<p class="muted">Keine Ereignisse in diesem Zeitraum.</p>`}</section>`;
}

// ---- Notfall & Wartung ----
const EFFECTS = html`<ul class="sec-effects">
  <li>Alle außer Personen mit „Sicherheit ändern“ werden sofort abgemeldet und können sich nicht mehr anmelden.</li>
  <li>Der E-Mail-Versand wird angehalten.</li>
  <li>Die Daten bleiben unverändert – es wird nichts gelöscht.</li>
  <li>Beenden kann ihn nur eine Person mit „Sicherheit ändern“ und ihrem Passwort, auf dem Server auch mit <code>sudo ju-finanzen notfall-aus</code>.</li>
</ul>`;

function notfall(l) {
  const s = l.sperre;
  const write = can('sicherheit', 'schreiben');
  const active = s?.art === 'notfall';
  const maintenance = s?.art === 'wartung';
  return html`<section class="panel sec-state ${active ? 'sec-alert' : ''}"><h2>Notfallmodus</h2>
      ${active ? stateText(s) : html`<p class="muted">Bei Verdacht auf Missbrauch (z. B. ein fremder Zugriff oder ein verlorenes Gerät) sperrt der Notfallmodus das Programm sofort.</p>`}
      ${EFFECTS}
      <div class="form-actions">${active
        ? (write ? html`<button type="button" class="btn dark" data-emergency-off>Notfall beenden</button>` : html`<span class="small muted">Beenden nur mit „Sicherheit ändern“.</span>`)
        : html`<button type="button" class="btn sec-danger" data-emergency-on>${icon('alert', 16)} Notfall auslösen</button>`}</div>
    </section>
    ${write && !active ? html`<form class="panel switch-list" id="sec-maintenance">
      <div class="switch-row">
        <div class="switch-text"><h2>Wartung</h2>
          <p class="small muted">Während der Wartung sehen alle ohne „Sicherheit ändern“ statt des Programms einen Hinweis. Die Daten bleiben unverändert.</p>
          <label class="field">Hinweis für alle (freiwillig)<input name="text" maxlength="300" value="${maintenance ? s.text || '' : ''}" placeholder="z. B. Update – ab 18 Uhr wieder erreichbar"></label></div>
        <label class="switch"><input type="checkbox" name="an" role="switch" aria-label="Wartung" ${maintenance ? 'checked' : ''}><span class="switch-track" aria-hidden="true"></span>
          <span class="switch-label" data-switch-label>${maintenance ? 'An' : 'Aus'}</span></label>
      </div>
      <div class="form-actions"><button type="submit" class="btn primary">Speichern</button></div>
    </form>` : ''}`;
}

function emergencyDialog(reload) {
  const el = dialog('Notfall auslösen', html`<form id="sec-emergency" style="display:flex;flex-direction:column;gap:14px">
    ${EFFECTS}
    <label class="field">Grund<textarea name="grund" required maxlength="500" placeholder="Was ist passiert?"></textarea></label>
    ${can('sicherheit', 'schreiben') ? html`<label class="check"><input type="checkbox" name="alle"> Auch alle anderen Personen mit Sicherheitsrechten abmelden</label>` : ''}
    <label class="check"><input type="checkbox" name="gelesen"> Ich habe die Wirkungen gelesen</label>
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button>
      <button type="submit" class="btn sec-danger" disabled>Notfall auslösen</button></div></form>`);
  const form = el.querySelector('#sec-emergency');
  const submit = form.querySelector('button[type=submit]');
  form.elements.gelesen.onchange = () => { submit.disabled = !form.elements.gelesen.checked; };
  el.querySelector('[data-cancel]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const r = await api('/api/notfall-an', { grund: form.elements.grund.value.trim(), alle_abmelden: !!form.elements.alle?.checked });
      el.close();
      toast(`Notfallmodus ausgelöst – ${r.beendet === 1 ? '1 Sitzung' : `${n(r.beendet)} Sitzungen`} beendet.`);
      await reload();
    });
  };
}

// ---- Sitzungen & Konten ----
const waitText = (s) => (s > 90 ? `noch ${Math.ceil(s / 60)} Min.` : s > 0 ? `noch ${s} s` : '–');

function sitzungen(v) {
  return html`<section class="panel"><h2>Aktive Sitzungen</h2>
      ${v.sitzungen.length ? table(['Person', 'Angemeldet', 'Zuletzt aktiv', 'Gerät', ''], v.sitzungen.map((s) => html`<tr>
        <td><strong>${s.person}</strong>${s.eigene ? html` ${badge('eigene Sitzung', 'navy')}` : ''}</td>
        <td class="nowrap">${when(s.seit)}</td><td class="nowrap">${when(s.zuletzt)}</td><td class="small">${s.geraet || '–'}</td>
        <td class="num">${s.eigene ? '' : html`<button type="button" class="btn small" data-end="${s.id}">Beenden</button>`}</td></tr>`))
        : html`<p class="muted">Keine aktiven Sitzungen.</p>`}
    </section>
    <section class="panel"><h2>Gesperrte Zugänge</h2>
      <p class="small muted">Nach zu vielen falschen Passwörtern sperrt der Server ein Gerät oder einen Namen für eine Weile.</p>
      ${v.sperren.length ? table(['Art', 'Wert', { label: 'Versuche', className: 'num' }, 'Gesperrt', ''], v.sperren.map((s) => html`<tr>
        <td>${s.art}</td><td>${s.wert || '–'}</td><td class="num">${n(s.versuche)}</td><td class="nowrap">${waitText(s.wartet_sekunden)}</td>
        <td class="num"><button type="button" class="btn small" data-unlock="${s.key}">Freigeben</button></td></tr>`))
        : html`<p class="muted">Keine gesperrten Zugänge.</p>`}
    </section>
    <section class="panel"><div class="panel-head"><h2>Konten</h2>
        ${can('verwaltung') ? html`<button type="button" class="link-button" data-users>Bearbeiten unter Benutzer & Rollen</button>` : ''}</div>
      ${table(['Name', 'Stand', 'Zuletzt angemeldet', 'Hinweise'], v.konten.map((k) => html`<tr>
        <td><strong>${k.name}</strong>${k.name === store.user ? html` <span class="small muted">(du)</span>` : ''}</td>
        <td>${!k.aktiv ? badge('deaktiviert', 'warn') : k.passwort ? badge('aktiv', 'ok') : badge('kein Passwort', 'warn')}</td>
        <td class="nowrap">${when(k.zuletzt)}</td>
        <td>${k.ungenutzt ? badge('ungenutzt', 'warn') : ''} ${k.privilegiert ? badge('Verwaltungsrechte', 'navy') : ''}</td></tr>`))}
    </section>`;
}

// ---- System ----
function findings(label, list = []) {
  if (!list.length) return '';
  return html`<h3>${label} (${list.length})</h3><ul class="sec-files">${list.slice(0, 20).map((f) => html`<li>${f}</li>`)}
    ${list.length > 20 ? html`<li class="muted">und ${list.length - 20} weitere</li>` : ''}</ul>`;
}

function system(v, l) {
  const s = v.system;
  const d = l.dateipruefung || {};
  const [checkValue, checkNote] = checkSummary(d);
  return html`<section class="panel"><h2>System</h2>
      <dl class="sec-facts">
        <dt>Version</dt><dd>${s.version}${s.stand ? html` · Stand ${/^\d{4}-\d{2}-\d{2}/.test(s.stand) ? dateTime(s.stand) : s.stand}` : ''}</dd>
        <dt>Python</dt><dd>${s.python}</dd>
        <dt>Datenbank</dt><dd>${n(s.datenbank_mb)} MB</dd>
        <dt>Speicher</dt><dd>${n(s.daten_frei_gb)} GB frei von ${n(s.daten_gesamt_gb)} GB</dd>
        <dt>Sicherungen</dt><dd>${s.letzte_sicherung ? `zuletzt ${date(s.letzte_sicherung)}` : 'keine'} · ${n(s.sicherungen)} vorhanden</dd>
      </dl>
    </section>
    <section class="panel"><div class="panel-head"><h2>Dateiprüfung</h2>
        <button type="button" class="btn small" data-check>Jetzt prüfen</button></div>
      <p>${d.verfuegbar ? badge(checkValue, d.ok ? 'ok' : 'danger') : badge(checkValue)} <span class="small muted">${checkNote}</span></p>
      <p class="small muted">Vergleicht die Programmdateien mit dem Stand der Installation.</p>
      ${findings('Geändert', d.geaendert)}${findings('Fehlend', d.fehlend)}${findings('Zusätzlich', d.zusaetzlich)}
    </section>`;
}

// ---- Seite ----
async function load() {
  if (tab === 'ereignisse') return { ereignisse: await api('/api/sicherheit-ereignisse?' + query()) };
  const [lage, verwaltung] = await Promise.all([api('/api/sicherheit'),
    ['sitzungen', 'system'].includes(tab) ? api('/api/sicherheit-verwaltung') : null]);
  return { lage: lage.lagebild, verwaltung };
}

function bind(main, reload) {
  main.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => {
    tab = b.dataset.tab;
    main.querySelectorAll('[data-tab]').forEach((x) => x.setAttribute('aria-selected', String(x === b)));   // sofort, auch während des Ladens
    reload();
  }));
  const action = (fn, text) => safe(async () => { await fn(); toast(text); await reload(); });
  const filterForm = main.querySelector('#sec-filter');
  if (filterForm) {
    const f = filterForm.elements;
    const read = () => Object.assign(filter, { art: f.art.value, tage: f.tage.value, suche: f.suche.value.trim() });
    const apply = () => { read(); reload(); };
    f.art.onchange = apply;
    f.tage.onchange = apply;
    filterForm.onsubmit = (e) => { e.preventDefault(); apply(); };
    filterForm.querySelector('[data-csv]').onclick = () => safe(() => read() && download('/api/sicherheit-ereignisse.csv?' + query(), 'sicherheitsereignisse.csv'));
  }
  main.querySelector('[data-emergency-on]')?.addEventListener('click', () => emergencyDialog(reload));
  main.querySelector('[data-emergency-off]')?.addEventListener('click', () => action(() => api('/api/notfall-aus', {}), 'Notfallmodus beendet.'));
  const maintenance = main.querySelector('#sec-maintenance');
  if (maintenance) {
    const f = maintenance.elements;
    f.an.onchange = () => { maintenance.querySelector('[data-switch-label]').textContent = f.an.checked ? 'An' : 'Aus'; };
    maintenance.onsubmit = (e) => {
      e.preventDefault();
      const on = f.an.checked;
      action(() => api('/api/wartung', { an: on, text: f.text.value.trim() }), on ? 'Wartung eingeschaltet.' : 'Wartung ausgeschaltet.');
    };
  }
  main.querySelectorAll('[data-end]').forEach((b) => (b.onclick = () => action(() => api('/api/sitzung-beenden', { id: b.dataset.end }), 'Sitzung beendet.')));
  main.querySelectorAll('[data-unlock]').forEach((b) => (b.onclick = () => action(() => api('/api/sperre-aufheben', { key: b.dataset.unlock }), 'Zugang freigegeben.')));
  main.querySelector('[data-users]')?.addEventListener('click', () => go('users'));
  main.querySelector('[data-check]')?.addEventListener('click', () => safe(async () => {
    const r = await api('/api/dateipruefung', {});
    toast(r.ergebnis?.ok ? 'Dateiprüfung: alles in Ordnung.' : 'Dateiprüfung: Befund – bitte die Liste ansehen.', !r.ergebnis?.ok);
    await reload();
  }));
}

export function sicherheit(main) {
  if (!tabs().some(([id]) => id === tab)) tab = 'lagebild';
  const head = html`${pageHead('Verwaltung', 'Sicherheit')}
    <div class="tabs" role="tablist" aria-label="Bereich">${tabs().map(([id, label]) => html`
      <button type="button" role="tab" data-tab="${id}" aria-selected="${tab === id}">${label}</button>`)}</div>`;
  const reload = () => sicherheit(main);
  const id = ++loads;
  if (!main.querySelector('.sec-page')) mount(main, html`<div class="page sec-page">${head}<p class="muted">Lädt …</p></div>`);
  return safe(async () => {
    const r = await load();
    if (id !== loads || store.page !== 'security' || !main.isConnected) return;   // inzwischen anderer Reiter oder andere Seite
    const views = { lagebild: () => lagebild(r.lage), ereignisse: () => ereignisse(r.ereignisse), notfall: () => notfall(r.lage),
      sitzungen: () => sitzungen(r.verwaltung), system: () => system(r.verwaltung, r.lage) };
    mount(main, html`<div class="page sec-page">${head}${views[tab]()}</div>`);
    bind(main, reload);
  });
}
