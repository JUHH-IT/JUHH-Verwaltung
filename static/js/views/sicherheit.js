// Verwaltung → Sicherheit (ab 0.35, nur Serverbetrieb): Lagebild, Ereignisse, Notfall & Wartung, Firewall & Sperren (0.39),
// Sitzungen & Konten, System. Nur Anzeige und Auslöser – der Server prüft jede Anfrage selbst (rollen.py, sicherheit.py,
// schutz.py) und verlangt bei kritischen Aktionen das Passwort erneut (api.onConfirm).
// Diagramm „Abwehr je Stunde“: HTML/CSS ohne Bibliothek, eine Reihe in --teal, nur der Höchstwert beschriftet, jeder Wert
// als Hinweis am Balken und in der Tabelle darunter (lesbar ohne Zeigen).
import { api, download } from '../api.js';
import { html, mount } from '../html.js';
import { store, can, go, daysOverdue } from '../store.js';
import { pageHead, icon, safe, toast, dialog, table, badge } from '../ui.js';
import { date, dateTime } from '../format.js';

let tab = 'lagebild';
const filter = { art: '', tage: '30', suche: '' };   // Ereignisse
let loads = 0;                                        // nur die zuletzt gestartete Ladung zeigen
let poll = 0;                                         // Live-Verkehr: Auffrischen während eines Mitschnitts

const TABS = [
  ['lagebild', 'Lagebild'],
  ['ereignisse', 'Ereignisse'],
  ['notfall', 'Notfall & Wartung'],
  ['firewall', 'Firewall & Sperren'],
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

// Abwehr der letzten 24 Stunden: vier Zahlen und ein kleiner Verlauf je Stunde (auffällig = Treffer + abgewiesene Anfragen)
const MODUS = { abweisen: ['Firewall weist ab', 'ok'], protokollieren: ['Firewall protokolliert nur', 'warn'], aus: ['Firewall aus', 'danger'] };
const hourText = (h) => `${h.stunde}–${String((Number(h.stunde) + 1) % 24).padStart(2, '0')} Uhr: ${n(h.treffer)} Treffer, ${n(h.abgewiesen)} abgewiesen${h.sperren ? `, ${n(h.sperren)} gesperrt` : ''}`;

function abwehr(a) {
  const t = a.treffer;
  const hours = a.stunden.map((h) => ({ ...h, sum: h.treffer + h.abgewiesen }));
  const max = Math.max(...hours.map((h) => h.sum));
  const peak = hours.findIndex((h) => h.sum === max);
  const [modusText, modusKind] = MODUS[a.modus] || MODUS.aus;
  const busy = hours.filter((h) => h.sum || h.sperren);
  return html`<section class="panel"><div class="panel-head"><h2>Abwehr der letzten 24 Stunden</h2>${badge(modusText, modusKind)}</div>
    <dl class="sec-stats">
      <div><dt>Treffer</dt><dd>${n(a.treffer_gesamt)}<small>${n(t.falle)} Fallen · ${n(t.muster)} Angriffsmuster · ${n(t.werkzeug)} Werkzeuge · ${n(t.fehler)} Fehlerbremse</small></dd></div>
      <div><dt>Abgewiesene Anfragen</dt><dd>${n(a.abgewiesen)}<small>von gesperrten Adressen</small></dd></div>
      <div><dt>Automatisch gesperrt</dt><dd>${n(a.sperren)}<small>neue Sperren</small></dd></div>
      <div><dt>Gesperrte Adressen</dt><dd>${n(a.gesperrt)}<small>derzeit auf der Sperrliste</small></dd></div>
    </dl>
    <div class="sec-hours"><h3>Auffällige Anfragen je Stunde</h3>
      ${max ? html`<ol class="sec-cols" aria-label="Auffällige Anfragen je Stunde, letzte 24 Stunden">${hours.map((h, i) => html`<li title="${hourText(h)}">
          <span class="sr-only">${hourText(h)}</span>${i === peak ? html`<span class="sec-peak" aria-hidden="true">${n(h.sum)}</span>` : ''}
          ${h.sum ? html`<span class="sec-bar" aria-hidden="true" style="height:${Math.max(2, Math.round((h.sum / max) * 72))}px"></span>` : ''}</li>`)}</ol>
        <div class="sec-axis" aria-hidden="true">${[0, 6, 12, 18].map((i) => html`<span style="grid-column:${i + 1} / span 5">${hours[i].stunde} Uhr</span>`)}<span class="now">jetzt</span></div>
        <details class="sec-details"><summary>Als Tabelle</summary>
          ${table(['Stunde', { label: 'Treffer', className: 'num' }, { label: 'Abgewiesen', className: 'num' }, { label: 'Gesperrt', className: 'num' }], busy.map((h) => html`<tr>
            <td>${h.stunde} Uhr</td><td class="num">${n(h.treffer)}</td><td class="num">${n(h.abgewiesen)}</td><td class="num">${n(h.sperren)}</td></tr>`))}</details>`
        : html`<p class="muted">Keine auffälligen Anfragen in den letzten 24 Stunden.</p>`}
    </div></section>`;
}

const STAND = { ok: ['in Ordnung', 'ok'], hinweis: ['Hinweis', 'warn'], kritisch: ['kritisch', 'danger'] };
const standBadge = (stand) => badge(...(STAND[stand] || STAND.hinweis));
const ORDER = ['kritisch', 'hinweis', 'ok'];

function pruefung(list) {
  const count = (stand) => list.filter((p) => p.stand === stand).length;
  const reachable = (ziel) => (ziel.startsWith('seite:') ? can('verwaltung') : tabs().some(([id]) => id === ziel));
  return html`<section class="panel"><div class="panel-head"><h2>Konfigurationsprüfung</h2>
      <span class="small muted">${count('ok')} in Ordnung · ${count('hinweis')} Hinweise · ${count('kritisch')} kritisch</span></div>
    <ul class="sec-checks">${[...list].sort((a, b) => ORDER.indexOf(a.stand) - ORDER.indexOf(b.stand)).map((p) => html`<li>
      ${standBadge(p.stand)}<div><strong>${p.titel}</strong><span class="small muted">${p.detail}</span></div>
      ${p.ziel && reachable(p.ziel) ? html`<button type="button" class="link-button small" data-ziel="${p.ziel}" aria-label="${p.titel}: öffnen">Öffnen</button>` : ''}</li>`)}</ul></section>`;
}

function lagebild(l, r) {
  const old = !l.letzte_sicherung || daysOverdue(l.letzte_sicherung) > 2;
  const [checkValue, checkNote, checkWarn] = checkSummary(l.dateipruefung);
  return html`<section class="panel sec-state ${l.sperre ? 'sec-alert' : ''}"><h2>Zustand</h2>${stateText(l.sperre)}</section>
    ${abwehr(r.abwehr)}
    <section class="cards" aria-label="Kennzahlen">
      ${card('Fehlanmeldungen', n(l.fehlanmeldungen_24h), `in 24 Stunden · ${n(l.fehlanmeldungen_7t)} in 7 Tagen`)}
      ${card('Verweigerte Zugriffe', n(l.verweigert_7t), `in 7 Tagen${l.bestaetigung_fehl_7t ? ` · ${n(l.bestaetigung_fehl_7t)} falsche Bestätigungen` : ''}`)}
      ${card('Aktive Sitzungen', n(l.sitzungen), 'angemeldet')}
      ${card('Gesperrte Zugänge', n(l.gesperrt), 'nach Fehlversuchen', l.gesperrt > 0)}
      ${card('Ungenutzte Konten', n(l.konten_ungenutzt), `länger als 90 Tage nicht angemeldet · ${n(l.konten_aktiv)} aktiv`, l.konten_ungenutzt > 0)}
      ${card('Verwaltungsrechte', n(l.konten_privilegiert), 'Konten mit Rechten zur Verwaltung')}
      ${card('Letzte Sicherung', l.letzte_sicherung ? date(l.letzte_sicherung) : 'keine', old ? 'älter als 2 Tage – bitte prüfen' : 'aktuell', old)}
      ${card('Dateiprüfung', checkValue, checkNote, checkWarn)}
      ${store.auth === 'keycloak' ? card('Notzugang', l.notzugang ? 'offen' : 'geschlossen',
        l.notzugang ? `Passwort-Anmeldung bis ${dateTime(l.notzugang.bis)}` : 'öffnen nur auf dem Server: sudo ju-finanzen notzugang', !!l.notzugang) : ''}
    </section>
    ${pruefung(r.pruefung)}`;
}

// ---- Ereignisse ----
const LABELS = { netz: 'Adresse', minuten: 'Minuten', weitere: 'weitere Sperren', geaendert: 'geändert', modus: 'Firewall', sperren: 'Sperren', art: 'Art',
  grund: 'Grund', anzahl: 'Anzahl', beendet: 'beendet', versuche: 'Versuche', name: 'Name', adresse: 'Adresse', pfad: 'Adresse',
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
  <li>Die Firewall wird scharf gestellt: abweisen, Sperre ab dem ersten Treffer für 7 Tage, Bedrohungsliste an. Beim Beenden gelten wieder die vorherigen Einstellungen.</li>
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

// ---- Firewall & Sperren ----
const DAUER = { 10: '10 Minuten', 60: '1 Stunde', 360: '6 Stunden', 1440: '1 Tag', 10080: '7 Tage' };
const dauer = (m) => DAUER[m] || `${n(m)} Minuten`;

function firewall(s) {
  const e = s.einstellungen;
  const write = can('sicherheit', 'schreiben');
  const [modusText, modusKind] = MODUS[e.modus] || MODUS.aus;
  const auto = s.sperren.filter((x) => x.art === 'auto').length;
  return html`<section class="panel"><div class="panel-head"><h2>Firewall</h2>
        ${write ? html`<button type="button" class="btn small" data-fw-edit>Einstellungen</button>` : ''}</div>
      <p>${badge(modusText, modusKind)} ${s.notfall_vorlage ? badge('Vorlage Notfall', 'danger') : ''}</p>
      <dl class="sec-facts">
        <dt>Automatisch sperren</dt><dd>${e.auto ? `nach ${n(e.schwelle)} Treffern in 10 Minuten für ${dauer(e.sperrminuten)}` : 'aus'}</dd>
        <dt>Fehlerbremse</dt><dd>${e.fehler ? `ab ${n(e.fehler)} Fehlerantworten einer Adresse in 5 Minuten` : 'aus'}</dd>
        <dt>Nie sperren</dt><dd>${e.nie.length ? e.nie.join(', ') : 'keine Einträge'}</dd>
        <dt>Deine Adresse</dt><dd>${s.meine_adresse || 'unbekannt'} ${s.geschuetzt ? badge('nie gesperrt', 'ok') : badge('nicht unter „nie sperren“')}</dd>
      </dl>
      ${s.notfall_vorlage ? html`<p class="small muted">Der Notfallmodus hat die Firewall scharf gestellt. Beim Beenden gelten wieder die vorherigen Einstellungen.</p>` : ''}
      <p class="small muted">Selbst ausgesperrt? Auf dem Server: <code>sudo ju-finanzen schutz-aus</code> löscht alle Sperren und stellt auf „nur protokollieren“.</p>
    </section>
    <section class="panel"><div class="panel-head"><h2>Sperrliste</h2>
        ${write ? html`<span class="sec-actions">${auto ? html`<button type="button" class="btn small" data-fw-free="auto">Alle automatischen freigeben</button>` : ''}
          <button type="button" class="btn small" data-fw-block>Adresse sperren</button></span>` : ''}</div>
      ${s.sperren.length ? table(['Adresse / Netz', 'Art', 'Grund', 'Seit', 'Bis', ''], s.sperren.map((x) => html`<tr>
        <td class="sec-mono nowrap">${x.netz}</td><td>${x.art === 'auto' ? badge('automatisch', 'warn') : badge('von Hand', 'navy')}</td>
        <td class="small">${x.grund || '–'}${x.art === 'hand' && x.von ? html` <span class="muted">(${x.von})</span>` : ''}</td>
        <td class="nowrap small" data-label="seit">${when(x.seit)}</td><td class="nowrap small" data-label="bis">${x.bis ? dateTime(x.bis) : 'unbefristet'}</td>
        <td class="num">${write ? html`<button type="button" class="btn small" data-fw-free="${x.id}" data-netz="${x.netz}">Freigeben</button>` : ''}</td></tr>`), { className: 'sec-stack sec-blocks' })
        : html`<p class="muted">Keine gesperrten Adressen.</p>`}
    </section>
    <section class="panel"><div class="panel-head"><h2>Bedrohungsliste</h2>
        ${write ? html`<button type="button" class="btn small" data-fw-list>Jetzt laden</button>` : ''}</div>
      <p>${e.liste ? badge('eingeschaltet', 'ok') : badge('ausgeschaltet', 'warn')}
        <span class="small muted">${s.liste.stand ? `${n(s.liste.anzahl)} Netze · Stand ${dateTime(s.liste.stand)}` : 'noch nicht geladen'}${s.liste.stand && s.liste.veraltet ? ' · älter als 3 Tage' : ''}</span></p>
      <p class="small muted">Spamhaus DROP: Netze, die von Kriminellen betrieben oder gekapert sind. Anfragen von dort werden abgewiesen. Der Server lädt die Liste täglich neu.</p>
      ${s.eigene_gelistet ? html`<p class="small">${badge('Achtung', 'danger')} Deine eigene Adresse steht auf der Liste – bitte unter „nie sperren“ eintragen.</p>` : ''}
    </section>
    <section class="panel"><div class="panel-head"><h2>Live-Verkehr</h2>
        ${write ? html`<button type="button" class="btn small" data-fw-capture="${s.mitschnitt_bis ? '' : '1'}">${s.mitschnitt_bis ? 'Mitschnitt beenden' : 'Mitschnitt starten (15 Minuten)'}</button>` : ''}</div>
      <p class="small muted">${s.mitschnitt_bis ? html`${badge('Mitschnitt läuft', 'navy')} bis ${dateTime(s.mitschnitt_bis)} – alle Anfragen. ` : 'Ohne Mitschnitt nur auffällige Anfragen. '}
        Festgehalten werden Zeit, Adresse, Methode, Pfad und Status – nie Inhalte oder Personen; die Liste liegt nur im Arbeitsspeicher.</p>
      ${s.verkehr.length ? table(['Zeit', 'Adresse', 'Anfrage', { label: 'Status', className: 'num' }, 'Auffälligkeit'], s.verkehr.map((v) => html`<tr>
        <td class="nowrap small">${dateTime(v.zeit)}</td><td class="sec-mono nowrap">${v.adresse}</td><td class="sec-mono">${v.methode} ${v.pfad}</td>
        <td class="num">${v.status}</td><td class="small">${v.art || '–'}${v.n > 1 ? html` <span class="muted">× ${n(v.n)}</span>` : ''}</td></tr>`), { className: 'sec-stack sec-traffic' })
        : html`<p class="muted">Keine Einträge.</p>`}
    </section>`;
}

/** Rückfrage im Dialog; die Aktion selbst bestätigt der Server zusätzlich mit dem Passwort (app.CONFIRM). */
function ask(title, text, label, run) {
  const el = dialog(title, html`<p>${text}</p><div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button>
    <button type="button" class="btn primary" data-ok>${label}</button></div>`);
  el.querySelector('[data-cancel]').onclick = () => el.close();
  el.querySelector('[data-ok]').onclick = () => { el.close(); run(); };
}

function settingsDialog(s, reload) {
  const e = s.einstellungen;
  const el = dialog('Firewall-Einstellungen', html`<form id="sec-fw-form" style="display:flex;flex-direction:column;gap:14px">
    <label class="field">Firewall<select name="modus">${Object.entries(s.modi).map(([key, label]) => html`<option value="${key}" ${e.modus === key ? 'selected' : ''}>${label}</option>`)}</select></label>
    <p class="small muted">„Nur protokollieren“ weist nie ab – auch gesperrte Adressen kommen durch. Zum Ausprobieren neuer Einstellungen. Beim Umschalten auf „abweisen“ werden die automatischen Sperren aus der Zeit davor aufgehoben; Sperren von Hand bleiben.</p>
    <label class="check"><input type="checkbox" name="auto" ${e.auto ? 'checked' : ''}> Auffällige Adressen automatisch sperren</label>
    <label class="field">Treffer bis zur Sperre (in 10 Minuten)<input name="schwelle" type="number" min="1" max="50" required value="${e.schwelle}"></label>
    <label class="field">Sperrdauer<select name="sperrminuten">${s.sperrminuten.map((m) => html`<option value="${m}" ${e.sperrminuten === m ? 'selected' : ''}>${dauer(m)}</option>`)}</select></label>
    <label class="field">Fehlerbremse: Fehlerantworten je Adresse in 5 Minuten (0 = aus)<input name="fehler" type="number" min="0" max="500" required value="${e.fehler}"></label>
    <label class="check"><input type="checkbox" name="liste" ${e.liste ? 'checked' : ''}> Bedrohungsliste Spamhaus DROP anwenden</label>
    <label class="field">Nie sperren – eine Adresse oder ein Netz je Zeile
      <textarea name="nie" rows="3" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="z. B. 203.0.113.7 oder 203.0.113.0/24">${e.nie.join('\n')}</textarea></label>
    ${s.meine_adresse && !s.geschuetzt ? html`<button type="button" class="link-button small" data-mine>Meine Adresse ${s.meine_adresse} eintragen</button>` : ''}
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  const form = el.querySelector('#sec-fw-form');
  const f = form.elements;
  el.querySelector('[data-cancel]').onclick = () => el.close();
  el.querySelector('[data-mine]')?.addEventListener('click', () => { f.nie.value = [f.nie.value.trim(), s.meine_adresse].filter(Boolean).join('\n'); });
  form.onsubmit = (ev) => {
    ev.preventDefault();
    safe(async () => {
      await api('/api/schutz-einstellungen', { modus: f.modus.value, auto: f.auto.checked, schwelle: Number(f.schwelle.value), sperrminuten: Number(f.sperrminuten.value),
        fehler: Number(f.fehler.value), liste: f.liste.checked, nie: f.nie.value.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean) });
      el.close();
      toast('Firewall-Einstellungen gespeichert.');
      await reload();
    });
  };
}

function blockDialog(s, reload) {
  const el = dialog('Adresse sperren', html`<form id="sec-block-form" style="display:flex;flex-direction:column;gap:14px">
    <label class="field">Adresse oder Netz<input name="netz" required maxlength="60" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="z. B. 198.51.100.7 oder 198.51.100.0/24"></label>
    <label class="field">Grund<input name="grund" maxlength="120" placeholder="z. B. Scan am Wochenende"></label>
    <label class="field">Dauer<select name="minuten"><option value="0">unbefristet</option>${s.sperrminuten.map((m) => html`<option value="${m}">${dauer(m)}</option>`)}</select></label>
    <p class="small muted">Gilt sofort und auch für angemeldete Personen. Deine eigene Adresse${s.meine_adresse ? ` (${s.meine_adresse})` : ''} und „nie sperren“ lassen sich nicht sperren.</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button><button type="submit" class="btn primary">Sperren</button></div></form>`);
  const form = el.querySelector('#sec-block-form');
  el.querySelector('[data-cancel]').onclick = () => el.close();
  form.onsubmit = (ev) => {
    ev.preventDefault();
    safe(async () => {
      const r = await api('/api/schutz-sperren', { netz: form.elements.netz.value.trim(), grund: form.elements.grund.value.trim(), minuten: Number(form.elements.minuten.value) });
      el.close();
      toast(`${r.netz} gesperrt.`);
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
        <td>${!k.aktiv ? badge('inaktiv', 'warn') : k.passwort || store.auth === 'keycloak' ? badge('aktiv', 'ok') : badge('kein Passwort', 'warn')}</td>
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

function system(v, l, b) {
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
    </section>
    ${serverschutz(b)}`;
}

/** Bericht der Betriebssystem-Härtung (Datei vom Server, nur gelesen): Texte kommen als fremde Eingabe – nur über html`` (maskiert). */
function serverschutz(b) {
  const z = b.zahlen || {};
  const fact = (key, label, text) => (key in z ? html`<dt>${label}</dt><dd>${text(z[key])}</dd>` : '');
  return html`<section class="panel"><div class="panel-head"><h2>Serverschutz (Betriebssystem)</h2>
      <span class="small muted">${b.zeit ? `Stand ${dateTime(b.zeit)}` : ''}</span></div>
    ${b.hinweis ? html`<p>${badge('Hinweis', 'warn')} <span class="small">${b.hinweis}</span></p>` : ''}
    ${b.pruefungen.length ? html`<ul class="sec-checks">${b.pruefungen.map((p) => html`<li>${standBadge(p.stand)}
      <div><strong>${p.titel}</strong><span class="small muted">${p.detail}</span></div></li>`)}</ul>` : ''}
    ${Object.keys(z).length ? html`<dl class="sec-facts">
      ${fact('sperrliste_netze', 'Sperrliste der Server-Firewall', (v) => `${n(v)} Netze${z.sperrliste_stand ? ` · Stand ${dateTime(z.sperrliste_stand)}` : ''}`)}
      ${fact('gebannt', 'Gebannte Adressen', n)}${fact('abgewiesen_24h', 'Abgewiesen in 24 Stunden', n)}
      ${fact('updates_offen', 'Offene Sicherheitsupdates', n)}${fact('neustart_noetig', 'Neustart nötig', (v) => (v ? 'ja' : 'nein'))}</dl>` : ''}
    <p class="small muted">Firewall, Sperrlisten und Updates des Betriebssystems prüft der Server selbst und legt regelmäßig einen Bericht ab; das Programm zeigt ihn nur an.</p>
  </section>`;
}

// ---- Seite ----
async function load() {
  if (tab === 'ereignisse') return { ereignisse: await api('/api/sicherheit-ereignisse?' + query()) };
  if (tab === 'firewall') return { schutz: await api('/api/schutz') };
  const [lage, verwaltung] = await Promise.all([api('/api/sicherheit'),
    ['sitzungen', 'system'].includes(tab) ? api('/api/sicherheit-verwaltung') : null]);
  return { lage: lage.lagebild, abwehr: lage.abwehr, pruefung: lage.pruefung, serverschutz: lage.serverschutz, verwaltung };
}

function bind(main, reload, r) {
  main.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => {
    tab = b.dataset.tab;
    main.querySelectorAll('[data-tab]').forEach((x) => x.setAttribute('aria-selected', String(x === b)));   // sofort, auch während des Ladens
    reload();
  }));
  const action = (fn, text) => safe(async () => { await fn(); toast(text); await reload(); });
  main.querySelectorAll('[data-ziel]').forEach((b) => (b.onclick = () => {   // Konfigurationsprüfung: Sprung zur passenden Stelle
    if (b.dataset.ziel.startsWith('seite:')) return go(b.dataset.ziel.slice(6));
    tab = b.dataset.ziel;
    return reload();
  }));
  main.querySelector('[data-fw-edit]')?.addEventListener('click', () => settingsDialog(r.schutz, reload));
  main.querySelector('[data-fw-block]')?.addEventListener('click', () => blockDialog(r.schutz, reload));
  main.querySelectorAll('[data-fw-free]').forEach((b) => (b.onclick = () => {
    const all = b.dataset.fwFree === 'auto';
    ask(all ? 'Automatische Sperren freigeben' : 'Adresse freigeben', all ? 'Alle automatisch gesperrten Adressen wieder zulassen?' : `${b.dataset.netz} wieder zulassen?`, 'Freigeben',
      () => action(() => api('/api/schutz-freigeben', { id: all ? 'auto' : Number(b.dataset.fwFree) }), all ? 'Automatische Sperren freigegeben.' : 'Adresse freigegeben.'));
  }));
  main.querySelector('[data-fw-list]')?.addEventListener('click', () => ask('Bedrohungsliste laden', 'Die Liste Spamhaus DROP jetzt von www.spamhaus.org laden? Dabei werden keine Daten des Programms übertragen.', 'Jetzt laden',
    () => safe(async () => { const x = await api('/api/schutz-liste-laden', {}); toast(`Bedrohungsliste geladen: ${n(x.liste.anzahl)} Netze.`); await reload(); })));
  main.querySelector('[data-fw-capture]')?.addEventListener('click', (ev) => {
    const on = ev.currentTarget.dataset.fwCapture === '1';
    action(() => api('/api/schutz-mitschnitt', { an: on }), on ? 'Mitschnitt gestartet – endet nach 15 Minuten von selbst.' : 'Mitschnitt beendet.');
  });
  clearTimeout(poll);
  const tick = () => {   // während des Mitschnitts alle 5 Sekunden auffrischen (nicht hinter einem offenen Dialog)
    if (store.page !== 'security' || tab !== 'firewall' || !main.isConnected) return;
    if (document.querySelector('dialog[open]')) poll = setTimeout(tick, 5000);
    else reload();
  };
  if (r.schutz?.mitschnitt_bis) poll = setTimeout(tick, 5000);
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
    const views = { lagebild: () => lagebild(r.lage, r), ereignisse: () => ereignisse(r.ereignisse), notfall: () => notfall(r.lage),
      firewall: () => firewall(r.schutz), sitzungen: () => sitzungen(r.verwaltung), system: () => system(r.verwaltung, r.lage, r.serverschutz) };
    mount(main, html`<div class="page sec-page">${head}${views[tab]()}</div>`);
    bind(main, reload, r);
  });
}
