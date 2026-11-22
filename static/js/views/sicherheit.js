// Verwaltung → Sicherheit (ab 0.35, nur Serverbetrieb). Ab 0.50 als Cockpit (Designvorschlag „Sicherheits-Cockpit“, freigegeben
// von Lennert Gust 22.11.2026): vier Bereiche Übersicht, Abwehr, Zugänge (nur „Sicherheit ändern“), Protokoll; Notfall und Wartung
// als Knöpfe im Kopf jeder Ansicht. Die Übersicht beantwortet zuerst „Ist alles in Ordnung?“ (Ring aus der Konfigurationsprüfung),
// zeigt nur Offenes als „Zu tun“ mit der Aktion daneben und jede Angabe genau einmal.
// Nur Anzeige und Auslöser – der Server prüft jede Anfrage selbst (rollen.py, sicherheit.py, schutz.py) und verlangt bei
// kritischen Aktionen das Passwort erneut (api.onConfirm).
// Diagramme: HTML/CSS ohne Bibliothek, eine Reihe in --teal, nur der Höchstwert beschriftet, jeder Wert als Hinweis am Balken und
// in der Tabelle darunter (lesbar ohne Zeigen).
import { api, download } from '../api.js';
import { html, mount } from '../html.js';
import { store, can, go } from '../store.js';
import { pageHead, icon, safe, toast, dialog, table, badge } from '../ui.js';
import { date, dateTime } from '../format.js';

let tab = 'uebersicht';
const filter = { art: '', tage: '30', suche: '' };   // Protokoll
let loads = 0;                                        // nur die zuletzt gestartete Ladung zeigen
let poll = 0;                                         // Live-Verkehr: Auffrischen während eines Mitschnitts

const TABS = [
  ['uebersicht', 'Übersicht'],
  ['abwehr', 'Abwehr'],
  ['zugaenge', 'Zugänge', true],   // true = nur mit „Sicherheit ändern“
  ['protokoll', 'Protokoll'],
];
const tabs = () => TABS.filter(([, , write]) => !write || can('sicherheit', 'schreiben'));

const when = (iso) => (iso ? dateTime(iso) : 'nie');
const n = (value) => Number(value || 0).toLocaleString('de-DE', { maximumFractionDigits: 1 });
const card = (label, value, note, warn = false, extra = '') => html`<div class="card ${warn ? 'sec-warn' : ''}"><span class="label">${label}</span>
  <span class="value">${value}</span>${extra}<span class="note">${note}</span></div>`;
const MODUS = { abweisen: ['Firewall weist ab', 'ok'], protokollieren: ['Firewall protokolliert nur', 'warn'], aus: ['Firewall aus', 'danger'] };
const STAND = { ok: ['in Ordnung', 'ok'], hinweis: ['Hinweis', 'warn'], kritisch: ['kritisch', 'danger'] };
const standBadge = (stand) => badge(...(STAND[stand] || STAND.hinweis));
const ORDER = ['kritisch', 'hinweis', 'ok'];
const mehrzahl = (zahl, eins, viele) => `${n(zahl)} ${zahl === 1 ? eins : viele}`;

function checkSummary(d) {
  if (!d?.verfuegbar) return ['nicht verfügbar', d?.zeit ? `Stand ${dateTime(d.zeit)}` : 'noch nicht geprüft', false];
  const count = (d.geaendert?.length || 0) + (d.fehlend?.length || 0) + (d.zusaetzlich?.length || 0);
  return [d.ok ? 'ohne Befund' : mehrzahl(count, 'Befund', 'Befunde'), `${n(d.dateien)} Dateien · ${dateTime(d.zeit)}`, !d.ok];
}

// ---- Kopf: Zustand, Wartung, Notfall – in jeder Ansicht ----
function kopf(sperre) {
  const write = can('sicherheit', 'schreiben');
  const notfall = sperre?.art === 'notfall';
  const wartung = sperre?.art === 'wartung';
  const zustand = notfall ? badge('Notfallmodus aktiv', 'danger') : wartung ? badge('Wartung läuft', 'warn') : badge('Normalbetrieb', 'ok');
  return html`<span class="sec-zustand">${zustand}</span>
    ${write && !notfall ? html`<button type="button" class="btn" data-wartung>${wartung ? 'Wartung beenden' : 'Wartung'}</button>` : ''}
    ${notfall ? (write ? html`<button type="button" class="btn dark" data-emergency-off>Notfall beenden</button>` : '')
      : html`<button type="button" class="btn sec-danger" data-emergency-on>${icon('shield', 16)} Notfall</button>`}`;
}

function stateText(sperre) {
  const notfall = sperre.art === 'notfall';
  return html`<p class="sub">seit ${dateTime(sperre.seit)}${sperre.von ? html` · ausgelöst von ${sperre.von}` : ''}</p>
    ${sperre.grund ? html`<p class="small">Grund: ${sperre.grund}</p>` : ''}
    ${sperre.text ? html`<p class="small">Hinweis für alle: ${sperre.text}</p>` : ''}
    <p class="small muted">${notfall ? html`Alle ohne „Sicherheit ändern“ sind abgemeldet, der E-Mail-Versand ruht, die Firewall ist scharf. Beenden: hier mit „Sicherheit ändern“ oder auf dem Server mit <code>sudo ju-finanzen notfall-aus</code>.`
      : 'Alle ohne „Sicherheit ändern“ sehen statt des Programms einen Hinweis. Die Daten bleiben unverändert.'}</p>`;
}

// ---- Übersicht ----
/** Ring aus allen Prüfungen: je Stand ein Bogen (Reihenfolge in Ordnung, Hinweis, kritisch), in der Mitte die Zahl offener Punkte. */
function ring(zahl) {
  const r = 54;
  const umfang = 2 * Math.PI * r;
  const gesamt = zahl.ok + zahl.hinweis + zahl.kritisch || 1;
  const teile = ['ok', 'hinweis', 'kritisch'].filter((s) => zahl[s]);
  const luecke = teile.length > 1 ? 6 : 0;
  let start = 0;
  const boegen = teile.map((s) => {
    const laenge = (zahl[s] / gesamt) * umfang;
    const bogen = html`<circle class="${s}" cx="66" cy="66" r="${r}" stroke-dasharray="${Math.max(0.1, laenge - luecke).toFixed(1)} ${umfang.toFixed(1)}"
      stroke-dashoffset="${(-start).toFixed(1)}"></circle>`;
    start += laenge;
    return bogen;
  });
  const offen = zahl.hinweis + zahl.kritisch;
  return html`<svg class="sec-ring" viewBox="0 0 132 132" role="img"
      aria-label="${`${gesamt} Prüfungen: ${zahl.ok} in Ordnung, ${zahl.hinweis} Hinweise, ${zahl.kritisch} kritisch`}">
    <circle class="spur" cx="66" cy="66" r="${r}"></circle><g transform="rotate(-87 66 66)">${boegen}</g>
    ${offen ? html`<text x="66" y="68" text-anchor="middle">${offen}</text><text class="klein" x="66" y="90" text-anchor="middle">offen</text>`
      : html`<path class="haken" d="M48 67l12 12 24-26"></path>`}</svg>`;
}

const CHIPS = { firewall: 'Firewall weist ab', authenticator: 'Anmeldung mit Authenticator-App', sicherung: 'Sicherung aktuell' };

function lage(l, pruefung) {
  const zahl = { ok: 0, hinweis: 0, kritisch: 0 };
  pruefung.forEach((p) => { zahl[p.stand] = (zahl[p.stand] || 0) + 1; });
  const s = l.sperre;
  const stand = new Date().toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  const [titel, text] = s ? [s.art === 'notfall' ? 'Notfallmodus aktiv' : 'Wartung läuft', null]
    : zahl.kritisch ? [`${mehrzahl(zahl.kritisch, 'kritischer Punkt', 'kritische Punkte')}`, `Bitte sofort ansehen. ${zahl.ok} von ${pruefung.length} Prüfungen sind in Ordnung.`]
    : zahl.hinweis ? ['Keine kritischen Punkte.', `${zahl.hinweis === 1 ? 'Einen Punkt solltest du dir ansehen' : `${n(zahl.hinweis)} Punkte solltest du dir ansehen`}. ${zahl.ok} von ${pruefung.length} Prüfungen sind in Ordnung.`]
    : ['Alles in Ordnung.', `Alle ${pruefung.length} Prüfungen ohne Befund.`];
  const chips = pruefung.filter((p) => p.stand === 'ok' && CHIPS[p.id]).map((p) => badge(CHIPS[p.id], 'ok'));
  const alarm = s?.art === 'notfall' || (!s && zahl.kritisch);
  return html`<section class="panel sec-lage ${alarm ? 'sec-alert' : s ? 'sec-hinweis' : ''}" aria-labelledby="sec-lage">
    ${ring(zahl)}
    <div class="sec-lage-text"><span class="eyebrow" id="sec-lage">Lage · Stand ${stand}</span>
      <h2>${titel}</h2>${s ? stateText(s) : html`<p class="sub">${text}</p>`}
      ${chips.length && !s ? html`<p class="sec-chips">${chips}</p>` : ''}</div></section>`;
}

const ZIEL = { abwehr: 'Zur Abwehr', zugaenge: 'Zugänge ansehen', server: 'Ansehen', 'seite:users': 'Benutzer & Rollen' };
const AKTION = { bedrohungsliste: ['Jetzt laden', 'data-fw-list'], dateipruefung: ['Jetzt prüfen', 'data-check'] };   // direkt hier, mit „ändern“

function erreichbar(ziel) {
  if (!ziel) return false;
  if (ziel.startsWith('seite:')) return can('verwaltung');
  return ziel === 'server' || tabs().some(([id]) => id === ziel);
}

function zuTun(pruefung) {
  const offen = pruefung.filter((p) => p.stand !== 'ok').sort((a, b) => ORDER.indexOf(a.stand) - ORDER.indexOf(b.stand));
  const write = can('sicherheit', 'schreiben');
  const knopf = (p) => {
    const ausgeschaltet = p.id === 'bedrohungsliste' && p.detail.includes('ausgeschaltet');
    if (write && AKTION[p.id] && !ausgeschaltet) return html`<button type="button" class="btn small" ${AKTION[p.id][1]}>${AKTION[p.id][0]}</button>`;
    return erreichbar(p.ziel) ? html`<button type="button" class="btn small" data-ziel="${p.ziel}" aria-label="${p.titel}: ${ZIEL[p.ziel] || 'Öffnen'}">${ZIEL[p.ziel] || 'Öffnen'}</button>` : '';
  };
  return html`<section class="panel sec-todo" aria-labelledby="sec-todo">
    <div class="panel-head"><h2 id="sec-todo">Zu tun</h2>${offen.length ? badge(n(offen.length), offen.some((p) => p.stand === 'kritisch') ? 'danger' : 'warn') : ''}</div>
    ${offen.length ? html`<ul>${offen.map((p) => html`<li class="${p.stand}">
        <span class="sym">${icon('alert', 18)}</span>
        <div><strong>${p.titel}</strong><span class="small">${p.detail}</span><span class="stand">${STAND[p.stand][0]}</span></div>
        ${knopf(p)}</li>`)}</ul>`
      : html`<p class="sec-leer">${icon('check', 18)} Nichts zu tun – alle Prüfungen sind in Ordnung.</p>`}
  </section>`;
}

function kennzahlen(l, a, v) {
  const stunden = a.stunden.map((h) => h.treffer + h.abgewiesen);
  const max = Math.max(1, ...stunden);
  const linie = html`<span class="sec-spark" aria-hidden="true">${stunden.map((x) => html`<i style="height:${Math.max(x ? 2 : 0, Math.round((x / max) * 28))}px"></i>`)}</span>`;
  const personen = v ? new Set(v.sitzungen.map((s) => s.person)).size : 0;
  const fehl = [`${n(l.fehlanmeldungen_24h)} in 24 Stunden`];
  if (l.verweigert_7t) fehl.push(mehrzahl(l.verweigert_7t, 'verweigerter Zugriff', 'verweigerte Zugriffe'));
  if (l.bestaetigung_fehl_7t) fehl.push(mehrzahl(l.bestaetigung_fehl_7t, 'falsche Bestätigung', 'falsche Bestätigungen'));
  if (l.gesperrt) fehl.push(mehrzahl(l.gesperrt, 'Zugang gesperrt', 'Zugänge gesperrt'));
  return html`<section class="cards sec-kpis" aria-label="Kennzahlen">
    ${card('Auffällige Anfragen', n(stunden.reduce((x, y) => x + y, 0)), `in 24 Stunden · ${n(a.treffer_gesamt)} Treffer, ${n(a.abgewiesen)} abgewiesen`, false, linie)}
    ${card('Fehlanmeldungen', n(l.fehlanmeldungen_7t), `in 7 Tagen · ${fehl.join(' · ')}`, l.gesperrt > 0)}
    ${card('Gerade angemeldet', n(l.sitzungen), v ? `${mehrzahl(personen, 'Person', 'Personen')} · aktive Sitzungen` : 'aktive Sitzungen')}
    ${card('Gesperrte Adressen', n(a.gesperrt), `auf der Sperrliste · ${n(a.sperren)} neu in 24 Stunden`)}
  </section>`;
}

const hourText = (h) => `${h.stunde}–${String((Number(h.stunde) + 1) % 24).padStart(2, '0')} Uhr: ${n(h.treffer)} Treffer, ${n(h.abgewiesen)} abgewiesen${h.sperren ? `, ${n(h.sperren)} gesperrt` : ''}`;

function verlauf(a) {
  const t = a.treffer;
  const hours = a.stunden.map((h) => ({ ...h, sum: h.treffer + h.abgewiesen }));
  const max = Math.max(...hours.map((h) => h.sum));
  const peak = hours.findIndex((h) => h.sum === max);
  const [modusText, modusKind] = MODUS[a.modus] || MODUS.aus;
  const busy = hours.filter((h) => h.sum || h.sperren);
  return html`<section class="panel sec-verlauf" aria-labelledby="sec-verlauf"><div class="panel-head"><h2 id="sec-verlauf">Abwehr der letzten 24 Stunden</h2>${badge(modusText, modusKind)}</div>
    ${max ? html`<ol class="sec-cols" aria-label="Auffällige Anfragen je Stunde, letzte 24 Stunden">${hours.map((h, i) => html`<li title="${hourText(h)}">
        <span class="sr-only">${hourText(h)}</span>${i === peak ? html`<span class="sec-peak" aria-hidden="true">${n(h.sum)}</span>` : ''}
        ${h.sum ? html`<span class="sec-bar" aria-hidden="true" style="height:${Math.max(2, Math.round((h.sum / max) * 104))}px"></span>` : ''}</li>`)}</ol>
      <div class="sec-axis" aria-hidden="true">${[0, 6, 12, 18].map((i) => html`<span style="grid-column:${i + 1} / span 5">${hours[i].stunde} Uhr</span>`)}<span class="now">jetzt</span></div>`
      : html`<p class="muted">Keine auffälligen Anfragen in den letzten 24 Stunden.</p>`}
    <p class="small sec-arten">${n(a.treffer_gesamt)} Treffer: ${n(t.falle)} Fallen · ${n(t.muster)} Angriffsmuster · ${n(t.werkzeug)} Werkzeuge · ${n(t.fehler)} Fehlerbremse.
      ${mehrzahl(a.sperren, 'Adresse', 'Adressen')} automatisch gesperrt. <button type="button" class="link-button" data-ziel="abwehr">Zur Abwehr</button></p>
    ${busy.length ? html`<details class="sec-details"><summary>Als Tabelle</summary>
      ${table(['Stunde', { label: 'Treffer', className: 'num' }, { label: 'Abgewiesen', className: 'num' }, { label: 'Gesperrt', className: 'num' }], busy.map((h) => html`<tr>
        <td>${h.stunde} Uhr</td><td class="num">${n(h.treffer)}</td><td class="num">${n(h.abgewiesen)}</td><td class="num">${n(h.sperren)}</td></tr>`))}</details>` : ''}
  </section>`;
}

function serverUndDaten(l, pruefung, b, v) {
  const stand = Object.fromEntries(pruefung.map((p) => [p.id, p.stand]));
  const d = l.dateipruefung || {};
  const [pruefWert, pruefNotiz] = checkSummary(d);
  const s = v?.system;
  const zeile = (titel, wert, st = '') => html`<li><span class="sec-punkt ${st}" aria-hidden="true"></span>
    <span>${titel}${st && st !== 'ok' ? html` <span class="sr-only">(${STAND[st]?.[0]})</span>` : ''}</span><span class="wert">${wert}</span></li>`;
  const befunde = (d.geaendert?.length || 0) + (d.fehlend?.length || 0) + (d.zusaetzlich?.length || 0);
  return html`<section class="panel sec-server" id="sec-server" tabindex="-1" aria-labelledby="sec-server-titel">
    <div class="panel-head"><h2 id="sec-server-titel">Server &amp; Daten</h2>
      ${can('sicherheit', 'schreiben') ? html`<button type="button" class="btn small" data-check>Dateiprüfung jetzt</button>` : ''}</div>
    <ul class="sec-rows">
      ${zeile('Sicherung', l.letzte_sicherung ? `zuletzt ${date(l.letzte_sicherung)}` : 'keine', stand.sicherung)}
      ${zeile('Dateiprüfung', `${pruefWert} · ${pruefNotiz}`, stand.dateipruefung)}
      ${zeile('Serverschutz', b.zeit ? `${mehrzahl(b.pruefungen.length, 'Prüfung', 'Prüfungen')} · Stand ${dateTime(b.zeit)}` : 'kein Bericht', stand.serverschutz)}
      ${s ? zeile('Version', `${s.version}${s.stand ? ` · Stand ${/^\d{4}-\d{2}-\d{2}/.test(s.stand) ? dateTime(s.stand) : s.stand}` : ''}`) : ''}
      ${s ? zeile('Speicher', `${n(s.daten_frei_gb)} GB frei von ${n(s.daten_gesamt_gb)} GB`) : ''}
    </ul>
    ${befunde ? html`<details class="sec-details" open><summary>Befunde der Dateiprüfung (${n(befunde)})</summary>
      ${findings('Geändert', d.geaendert)}${findings('Fehlend', d.fehlend)}${findings('Zusätzlich', d.zusaetzlich)}</details>` : ''}
    <details class="sec-details"><summary>Bericht des Serverschutzes${s ? ' und Systemangaben' : ''}</summary>${serverschutz(b)}
      ${s ? html`<dl class="sec-facts"><dt>Python</dt><dd>${s.python}</dd><dt>Datenbank</dt><dd>${n(s.datenbank_mb)} MB</dd>
        <dt>Sicherungen</dt><dd>${n(s.sicherungen)} vorhanden</dd></dl>` : ''}</details>
    <p class="small muted">Die Dateiprüfung vergleicht die Programmdateien mit dem Stand der Installation. Firewall, Sperrlisten und Updates des
      Betriebssystems prüft der Server selbst und legt regelmäßig einen Bericht ab; das Programm zeigt ihn nur an.</p>
  </section>`;
}

const WICHTIG = new Set(['anmeldung_fehl', 'verweigert', 'bestaetigung_fehl', 'notfall_an', 'notfall_aus', 'wartung_an', 'wartung_aus', 'notzugang',
  'notzugang_auf', 'schutz_sperre', 'schutz_einstellung', 'schutz_aus']);

function zuletzt(rows, arten) {
  return html`<section class="panel sec-zuletzt" aria-labelledby="sec-zuletzt">
    <div class="panel-head"><h2 id="sec-zuletzt">Zuletzt passiert</h2><button type="button" class="link-button" data-ziel="protokoll">Ganzes Protokoll</button></div>
    ${rows.length ? html`<ul class="sec-rows sec-ereignisse">${rows.slice(0, 6).map((e) => html`<li>
        <span class="sec-punkt ${WICHTIG.has(e.art) ? 'hinweis' : 'leer'}" aria-hidden="true"></span>
        <span><strong>${e.text || arten[e.art] || e.art}</strong>${WICHTIG.has(e.art) ? html`<span class="sr-only"> (wichtig)</span>` : ''}
          <span class="small muted">${[e.person, e.geraet, details(e.detail)].filter(Boolean).join(' · ') || '–'}</span></span>
        <span class="wert">${dateTime(e.zeit)}</span></li>`)}</ul>`
      : html`<p class="muted">Keine Ereignisse in den letzten 7 Tagen.</p>`}
  </section>`;
}

function inOrdnung(pruefung) {
  const ok = pruefung.filter((p) => p.stand === 'ok');
  return html`<section class="panel sec-ok" aria-labelledby="sec-ok">
    <div class="panel-head"><h2 id="sec-ok">In Ordnung</h2>${badge(n(ok.length), 'ok')}</div>
    ${ok.length ? html`<ul>${ok.map((p) => html`<li title="${p.detail}">${icon('check', 15)} ${p.titel}</li>`)}</ul>` : html`<p class="muted">Noch keine Prüfung in Ordnung.</p>`}
  </section>`;
}

/** Zwei Spalten ohne Lücken (breit); schmal eine Spalte in der Reihenfolge Lage, Zu tun, Kennzahlen, … (CSS `order`). */
function uebersicht(r) {
  return html`<div class="sec-grid">
    <div class="sec-spalte">${lage(r.lage, r.pruefung)}${kennzahlen(r.lage, r.abwehr, r.verwaltung)}${verlauf(r.abwehr)}${r.zuletzt ? zuletzt(r.zuletzt.ereignisse, r.zuletzt.arten)
      : html`<p class="small muted sec-gesperrt">Während der Sperre zeigt das Cockpit nur die Lage.</p>`}</div>
    <div class="sec-spalte">${zuTun(r.pruefung)}${serverUndDaten(r.lage, r.pruefung, r.serverschutz, r.verwaltung)}${inOrdnung(r.pruefung)}</div>
  </div>`;
}

// ---- Notfall & Wartung (Dialoge aus dem Kopf) ----
const WIRKUNGEN = [
  ['Alle werden abgemeldet', 'Alle außer Personen mit „Sicherheit ändern“ – sofort; neue Anmeldungen sind gesperrt.', 'danger'],
  ['E-Mail-Versand hält an', 'Bis der Notfall endet, verschickt das Programm keine E-Mails.', 'danger'],
  ['Firewall wird scharf', 'Abweisen, Sperre ab dem ersten Treffer für 7 Tage, Bedrohungsliste an. Beim Beenden gelten wieder die vorherigen Einstellungen.', 'danger'],
  ['Daten bleiben unverändert', 'Es wird nichts gelöscht.', 'ok'],
];
const EFFECTS = html`<ul class="sec-wirkungen">${WIRKUNGEN.map(([titel, text, art]) => html`<li class="${art}"><strong>${titel}</strong><span>${text}</span></li>`)}</ul>`;

function emergencyDialog(reload) {
  const el = dialog('Notfall auslösen', html`<form id="sec-emergency" style="display:flex;flex-direction:column;gap:14px">
    <p>Bei Verdacht auf Missbrauch – etwa einem fremden Zugriff oder einem verlorenen Gerät – sperrt der Notfallmodus das Programm sofort.</p>
    ${EFFECTS}
    <label class="field">Was ist passiert?<textarea name="grund" required maxlength="500" placeholder="z. B. Laptop mit angemeldeter Sitzung verloren"></textarea></label>
    ${can('sicherheit', 'schreiben') ? html`<label class="check"><input type="checkbox" name="alle"> Auch alle anderen Personen mit Sicherheitsrechten abmelden</label>` : ''}
    <label class="check"><input type="checkbox" name="gelesen"> Ich habe die Wirkungen gelesen</label>
    <p class="small muted">Beenden kann ihn nur eine Person mit „Sicherheit ändern“ und ihrem Passwort, auf dem Server auch <code>sudo ju-finanzen notfall-aus</code>.</p>
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

function wartungDialog(sperre, reload) {
  const an = sperre?.art === 'wartung';
  const el = dialog(an ? 'Wartung' : 'Wartung einschalten', html`<form id="sec-maintenance" style="display:flex;flex-direction:column;gap:14px">
    <p class="small muted">Während der Wartung sehen alle ohne „Sicherheit ändern“ statt des Programms einen Hinweis. Niemand wird abgemeldet,
      die Daten bleiben unverändert.</p>
    <label class="field">Hinweis für alle (freiwillig)<input name="text" maxlength="300" value="${an ? sperre.text || '' : ''}" placeholder="z. B. Update – ab 18 Uhr wieder erreichbar"></label>
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button>
      ${an ? html`<button type="button" class="btn primary" data-end-wartung>Wartung beenden</button><button type="submit" class="btn">Hinweis speichern</button>`
        : html`<button type="submit" class="btn primary">Wartung einschalten</button>`}</div></form>`);
  const form = el.querySelector('#sec-maintenance');
  const senden = (einschalten, meldung) => safe(async () => {
    await api('/api/wartung', { an: einschalten, text: form.elements.text.value.trim() });
    el.close();
    toast(meldung);
    await reload();
  });
  el.querySelector('[data-cancel]').onclick = () => el.close();
  el.querySelector('[data-end-wartung]')?.addEventListener('click', () => senden(false, 'Wartung ausgeschaltet.'));
  form.onsubmit = (e) => { e.preventDefault(); senden(true, an ? 'Hinweis gespeichert.' : 'Wartung eingeschaltet.'); };   // Enter beendet nie die Wartung
}

// ---- Protokoll ----
const LABELS = { netz: 'Adresse', minuten: 'Minuten', weitere: 'weitere Sperren', geaendert: 'geändert', modus: 'Firewall', sperren: 'Sperren', art: 'Art',
  grund: 'Grund', anzahl: 'Anzahl', beendet: 'beendet', versuche: 'Versuche', name: 'Name', adresse: 'Adresse', pfad: 'Adresse',
  methode: 'Methode', recht: 'Recht', bereich: 'Bereich', stufe: 'Stufe', benutzer: 'Person', person: 'Person', sperre: 'Sperre', rolle: 'Rolle', alle_abmelden: 'alle abgemeldet', text: 'Hinweis' };
/** Einzelheiten kurz in Worten (nie rohes JSON): einfache Werte mit Bezeichnung, Listen als Anzahl bzw. kurze Aufzählung. */
function details(detail) {
  return Object.entries(detail || {}).map(([key, value]) => {
    const label = LABELS[key] || key.replace(/_/g, ' ');
    if (typeof value === 'boolean') return `${label}: ${value ? 'ja' : 'nein'}`;
    if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)) return `${label}: ${dateTime(value)}`;
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

// ---- Abwehr ----
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

// ---- Zugänge ----
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

// ---- Server & Daten ----
function findings(label, list = []) {
  if (!list.length) return '';
  return html`<h3>${label} (${list.length})</h3><ul class="sec-files">${list.slice(0, 20).map((f) => html`<li>${f}</li>`)}
    ${list.length > 20 ? html`<li class="muted">und ${list.length - 20} weitere</li>` : ''}</ul>`;
}

/** Bericht der Betriebssystem-Härtung (Datei vom Server, nur gelesen): Texte kommen als fremde Eingabe – nur über html`` (maskiert). */
function serverschutz(b) {
  const z = b.zahlen || {};
  const fact = (key, label, text) => (key in z ? html`<dt>${label}</dt><dd>${text(z[key])}</dd>` : '');
  return html`${b.hinweis ? html`<p>${badge('Hinweis', 'warn')} <span class="small">${b.hinweis}</span></p>` : ''}
    ${b.pruefungen.length ? html`<ul class="sec-checks">${b.pruefungen.map((p) => html`<li>${standBadge(p.stand)}
      <div><strong>${p.titel}</strong><span class="small muted">${p.detail}</span></div></li>`)}</ul>` : ''}
    ${Object.keys(z).length ? html`<dl class="sec-facts">
      ${fact('sperrliste_netze', 'Sperrliste der Server-Firewall', (v) => `${n(v)} Netze${z.sperrliste_stand ? ` · Stand ${dateTime(z.sperrliste_stand)}` : ''}`)}
      ${fact('gebannt', 'Gebannte Adressen', n)}${fact('abgewiesen_24h', 'Abgewiesen in 24 Stunden', n)}
      ${fact('updates_offen', 'Offene Sicherheitsupdates', n)}${fact('neustart_noetig', 'Neustart nötig', (v) => (v ? 'ja' : 'nein'))}</dl>` : ''}`;
}

// ---- Seite ----
async function load() {
  const extra = {
    uebersicht: () => Promise.all([can('sicherheit', 'schreiben') ? api('/api/sicherheit-verwaltung') : null, api('/api/sicherheit-ereignisse?tage=7')]),
    abwehr: () => api('/api/schutz'),
    zugaenge: () => api('/api/sicherheit-verwaltung'),
    protokoll: () => api('/api/sicherheit-ereignisse?' + query()),
  }[tab];
  // Erst die Lage: Während Notfall oder Wartung beantwortet der Server ohne „Sicherheit ändern“ nur GET /api/sicherheit (sonst 503 → Sperrbildschirm)
  const lage = await api('/api/sicherheit');
  const gesperrt = Boolean(lage.lagebild.sperre) && !can('sicherheit', 'schreiben');
  const daten = gesperrt ? null : await extra();
  const r = { lage: lage.lagebild, abwehr: lage.abwehr, pruefung: lage.pruefung, serverschutz: lage.serverschutz };
  if (gesperrt) { tab = 'uebersicht'; return r; }
  if (tab === 'uebersicht') [r.verwaltung, r.zuletzt] = daten;
  if (tab === 'abwehr') r.schutz = daten;
  if (tab === 'zugaenge') r.verwaltung = daten;
  if (tab === 'protokoll') r.ereignisse = daten;
  return r;
}

function bind(main, reload, r) {
  const zeigen = (id) => {
    tab = id;
    main.querySelectorAll('[data-tab]').forEach((x) => x.setAttribute('aria-selected', String(x.dataset.tab === id)));   // sofort, auch während des Ladens
    return reload();
  };
  main.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => zeigen(b.dataset.tab)));
  const action = (fn, text) => safe(async () => { await fn(); toast(text); await reload(); });
  main.querySelectorAll('[data-ziel]').forEach((b) => (b.onclick = () => {   // aus „Zu tun“ und den Übersichtskarten zur passenden Stelle
    const ziel = b.dataset.ziel;
    if (ziel.startsWith('seite:')) return go(ziel.slice(6));
    if (ziel === 'server') {
      const ziel = main.querySelector('#sec-server');
      ziel?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
      return ziel?.focus({ preventScroll: true });
    }
    return zeigen(ziel);
  }));
  main.querySelector('[data-wartung]')?.addEventListener('click', () => wartungDialog(r.lage.sperre, reload));
  main.querySelector('[data-emergency-on]')?.addEventListener('click', () => emergencyDialog(reload));
  main.querySelector('[data-emergency-off]')?.addEventListener('click', () => action(() => api('/api/notfall-aus', {}), 'Notfallmodus beendet.'));
  main.querySelector('[data-fw-edit]')?.addEventListener('click', () => settingsDialog(r.schutz, reload));
  main.querySelector('[data-fw-block]')?.addEventListener('click', () => blockDialog(r.schutz, reload));
  main.querySelectorAll('[data-fw-free]').forEach((b) => (b.onclick = () => {
    const all = b.dataset.fwFree === 'auto';
    ask(all ? 'Automatische Sperren freigeben' : 'Adresse freigeben', all ? 'Alle automatisch gesperrten Adressen wieder zulassen?' : `${b.dataset.netz} wieder zulassen?`, 'Freigeben',
      () => action(() => api('/api/schutz-freigeben', { id: all ? 'auto' : Number(b.dataset.fwFree) }), all ? 'Automatische Sperren freigegeben.' : 'Adresse freigegeben.'));
  }));
  main.querySelectorAll('[data-fw-list]').forEach((b) => b.addEventListener('click', () => ask('Bedrohungsliste laden', 'Die Liste Spamhaus DROP jetzt von www.spamhaus.org laden? Dabei werden keine Daten des Programms übertragen.', 'Jetzt laden',
    () => safe(async () => { const x = await api('/api/schutz-liste-laden', {}); toast(`Bedrohungsliste geladen: ${n(x.liste.anzahl)} Netze.`); await reload(); }))));
  main.querySelector('[data-fw-capture]')?.addEventListener('click', (ev) => {
    const on = ev.currentTarget.dataset.fwCapture === '1';
    action(() => api('/api/schutz-mitschnitt', { an: on }), on ? 'Mitschnitt gestartet – endet nach 15 Minuten von selbst.' : 'Mitschnitt beendet.');
  });
  clearTimeout(poll);
  const tick = () => {   // während des Mitschnitts alle 5 Sekunden auffrischen (nicht hinter einem offenen Dialog)
    if (store.page !== 'security' || tab !== 'abwehr' || !main.isConnected) return;
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
  main.querySelectorAll('[data-end]').forEach((b) => (b.onclick = () => action(() => api('/api/sitzung-beenden', { id: b.dataset.end }), 'Sitzung beendet.')));
  main.querySelectorAll('[data-unlock]').forEach((b) => (b.onclick = () => action(() => api('/api/sperre-aufheben', { key: b.dataset.unlock }), 'Zugang freigegeben.')));
  main.querySelector('[data-users]')?.addEventListener('click', () => go('users'));
  main.querySelectorAll('[data-check]').forEach((b) => (b.onclick = () => safe(async () => {   // „Zu tun“ und „Server & Daten“
    const x = await api('/api/dateipruefung', {});
    const e = x.ergebnis || {};
    toast(!e.verfuegbar ? 'Dateiprüfung: keine Prüfliste vorhanden (nur nach Installation auf dem Server).'
      : e.ok ? 'Dateiprüfung: alles in Ordnung.' : 'Dateiprüfung: Befund – bitte die Liste ansehen.', e.verfuegbar && !e.ok);
    await reload();
  })));
}

export function sicherheit(main) {
  if (!tabs().some(([id]) => id === tab)) tab = 'uebersicht';
  const reiter = html`<div class="tabs" role="tablist" aria-label="Bereich">${tabs().map(([id, label]) => html`
      <button type="button" role="tab" data-tab="${id}" aria-selected="${tab === id}">${label}</button>`)}</div>`;
  const reload = () => sicherheit(main);
  const id = ++loads;
  if (!main.querySelector('.sec-page')) mount(main, html`<div class="page sec-page">${pageHead('Verwaltung', 'Sicherheit')}${reiter}<p class="muted">Lädt …</p></div>`);
  return safe(async () => {
    const r = await load();
    if (id !== loads || store.page !== 'security' || !main.isConnected) return;   // inzwischen anderer Reiter oder andere Seite
    const views = { uebersicht: () => uebersicht(r), abwehr: () => firewall(r.schutz), zugaenge: () => sitzungen(r.verwaltung), protokoll: () => ereignisse(r.ereignisse) };
    mount(main, html`<div class="page sec-page">${pageHead('Verwaltung', 'Sicherheit', kopf(r.lage.sperre))}${reiter}${views[tab]()}</div>`);
    bind(main, reload, r);
  });
}
