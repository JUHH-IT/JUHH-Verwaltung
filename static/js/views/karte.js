// Karte (Mitgliederverwaltung, ab 0.32 – Wunsch Lennert Gust 10.11.2026): jedes aktive Mitglied an seiner Anschrift, die Kreisverbände
// als farbige Gebiete (Bezirke), dazu die Listen „zu prüfen“. Server: karte.py (Lage je Mitglied) und adressen.py (amtliche Adressdaten,
// auf diesem Rechner gespeichert). Namen kommen aus der Mitgliederliste der Oberfläche – die Karte schickt nichts davon ins Netz.
// Kartenhintergrund: OpenStreetMap (abschaltbar unter Einstellungen → Schalter); dafür lädt der Browser nur Kartenkacheln des Ausschnitts.
// Bibliothek: Leaflet (static/vendor/leaflet), erst beim Öffnen der Karte geladen. Texte auf der Karte immer als DOM-Text, nie als HTML.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { store, go, refresh, kvName } from '../store.js';
import { pageHead, icon, dialog, safe, toast, table, empty } from '../ui.js';
import { addressSummary, addressCredit, loadAddressData, loadedText } from '../adressdaten.js';
import { memberDialog } from './members.js';

const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const START = { center: [53.55, 10.0], zoom: 10.5 };
const CHECKS = [
  ['unbekannt', 'Adresse nicht gefunden', 'nicht auf der Karte'],
  ['schreibweise', 'Schreibweise der Straße prüfen', 'Tippfehler – Lage vermutet'],
  ['plz', 'PLZ passt nicht zur Straße', 'bitte prüfen'],
  ['hausnummer', 'Hausnummer nicht gefunden', 'Lage ungefähr'],
  ['fremd', 'Wohnort passt nicht zum Kreisverband', 'orange umrandet'],
  ['ausserhalb', 'Wohnt außerhalb Hamburgs', 'Adresse nicht geprüft'],
];

let L = null;             // Leaflet (einmal geladen)
let geo = null;           // Bezirke und Stadtteile (einmal geladen)
let data = null;          // letzte Antwort von /api/karte
let mode = 'members';     // 'members' | 'density'
let kvFilter = '';        // nur Mitglieder dieses Kreisverbands
let view = null;          // letzter Kartenausschnitt {center, zoom} – bleibt beim Neuzeichnen
let live = null;          // die gerade gezeigte Leaflet-Karte (genau eine; wird beim Neuzeichnen und Verlassen freigegeben)
let seq = 0;

/** Karte freigeben (Ereignisse am Fenster, Zeichenfläche, Kachelbilder) – main.js ruft das bei jedem Seitenwechsel (Prüfbefund 0.32). */
export function leaveMap() {
  if (!live) return;
  const old = live;
  live = null;
  try {
    old.stop();
    old._animatingZoom = false;   // Leaflet 1.9.4: ein noch laufendes Zoomen griffe nach dem Abbau ins Leere (Feld der eingebundenen Version)
    old.off();
    old.remove();
  } catch { /* Karte war schon abgebaut */ }
}

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const memberById = (id) => store.data.members.find((m) => m.id === id);
const fullName = (m) => m.name || [m.first_name, m.last_name].filter(Boolean).join(' ');
const latlngs = (flaechen) => flaechen.map((polygon) => polygon.map((ring) => ring.map(([lon, lat]) => [lat, lon])));

async function library() {
  if (!L) {
    if (!document.querySelector('link[data-leaflet]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = '/vendor/leaflet/leaflet.css';
      link.dataset.leaflet = '1';
      document.head.append(link);
    }
    L = await import('../../vendor/leaflet/leaflet.js');
  }
  return L;
}

/** Klassen der Dichte: fünf Stufen bis zum größten Wert (ganze Zahlen). */
function densitySteps(max) {
  const top = Math.max(max, 5);
  return [1, 2, 3, 4, 5].map((i) => Math.ceil(top * i / 5));
}

/** Text für den Hinweis an einem Punkt: ein Eintrag je Mitglied, als DOM-Knoten (keine Mitgliederdaten als HTML). */
function tipNode(lines, note = '') {
  const box = document.createElement('div');
  for (const line of lines) {
    const row = document.createElement('div');
    row.textContent = line;
    box.append(row);
  }
  if (note) {
    const small = document.createElement('div');
    small.className = 'map-tip-note';
    small.textContent = note;
    box.append(small);
  }
  return box;
}

export function karte(main) {
  mount(main, html`<div class="page wide">
    ${pageHead('Mitgliederverwaltung · geprüft mit den amtlichen Hamburger Adressdaten auf diesem Rechner', 'Karte')}
    <div data-map-body><p class="small muted">Wird geladen …</p></div>
  </div>`);
  leaveMap();               // jedes Neuzeichnen der Seite (z. B. nach dem Speichern eines Mitglieds) beginnt mit einer neuen Karte
  const body = main.querySelector('[data-map-body]');
  const mine = ++seq;
  const alive = () => mine === seq && body.isConnected;
  let map = null;
  let markers = new Map();   // Mitglied → Punkt

  const reload = () => safe(async () => {
    data = await api('/api/karte');
    if (alive()) draw();
  });

  // ---- noch keine Adressdaten: erklären und laden
  function drawMissing(state) {
    const running = state.job?.state === 'running';
    mount(body, html`<section class="panel map-missing">
      <h2>Amtliche Adressdaten laden</h2>
      <p class="muted">Für die Karte und die Adressprüfung braucht das Programm einmal den öffentlichen Adressbestand der Stadt Hamburg
        (alle Straßen und Hausnummern mit Lage, rund 300.000 Adressen). Er wird auf diesem Rechner gespeichert; danach prüft und verortet das Programm
        ausschließlich hier. <strong>Es wird keine Mitgliederadresse ins Internet geschickt</strong> – abgerufen wird immer der ganze Bestand.</p>
      <p class="small muted" data-load-state aria-live="polite">${running ? addressSummary(state) : 'Dauert etwa eine Minute (rund 8 MB).'}</p>
      ${state.job?.state === 'error' ? html`<p class="notice warn">${state.job.error}</p>` : ''}
      <div class="form-actions" style="justify-content:flex-start"><button type="button" class="btn primary" data-load ${running ? 'disabled' : ''}>${icon('download', 16)} Adressdaten jetzt laden</button></div>
      <p class="small muted">${addressCredit()}</p>
    </section>`);
    const button = body.querySelector('[data-load]');
    const start = () => safe(async () => {
      button.disabled = true;
      try {
        const state = await loadAddressData((s) => { const p = body.querySelector('[data-load-state]'); if (p && alive()) p.textContent = addressSummary(s); }, alive, { start: !running });
        if (!alive()) return;
        await refresh();        // Start und Einstellungen kennen den neuen Stand; zeichnet diese Seite neu
        toast(loadedText(state));
      } catch (error) {
        if (alive()) reload();   // zeigt die Fehlermeldung des Abrufs
        throw error;
      }
    });
    button.onclick = start;
    if (running) start();
  }

  // ---- Seite mit Karte
  function draw() {
    leaveMap();                                // alte Karte samt Ereignissen freigeben, bevor neu gezeichnet wird
    map = null;
    if (!data.available) return drawMissing(data.state);
    const check = data.check;
    const kvs = geo.bezirke.map((b) => ({ ...b, count: data.kv[b.kv] || 0 })).sort((a, b) => kvName(a.kv).localeCompare(kvName(b.kv), 'de'));
    const others = Object.entries(data.kv).filter(([code]) => !geo.bezirke.some((b) => b.kv === code)).reduce((s, [, n]) => s + n, 0);
    const shown = data.points.filter((p) => !kvFilter || p.kv === kvFilter).length;
    const steps = densitySteps(Math.max(0, ...Object.values(data.stadtteile)));
    mount(body, html`
      <div class="toolbar map-toolbar">
        <label class="field inline map-search">${icon('search', 16)}<span class="sr-only">Straße oder Mitglied suchen</span>
          <input type="search" data-search placeholder="Straße oder Mitglied suchen" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" aria-controls="map-results"></label>
        <div class="segmented" role="group" aria-label="Darstellung">
          <button type="button" data-mode="members" aria-pressed="${mode === 'members'}">Mitglieder</button>
          <button type="button" data-mode="density" aria-pressed="${mode === 'density'}">Dichte je Stadtteil</button></div>
        <span class="small muted">${shown} von ${data.members} aktiven Mitgliedern auf der Karte${kvFilter ? ` · ${kvName(kvFilter)}` : ''}</span>
      </div>
      <div class="map-results" id="map-results" data-results aria-live="polite" aria-label="Suchergebnisse" hidden></div>
      <div class="map-layout">
        <section class="map-frame" aria-label="Karte der Mitglieder"><div class="map-canvas" data-map></div></section>
        <aside class="map-side">
          <section class="panel"><h2>Kreisverbände</h2>
            <ul class="map-legend">${kvs.map((b) => html`<li><button type="button" class="map-kv ${kvFilter === b.kv ? 'on' : ''}" data-kv="${b.kv}" aria-pressed="${kvFilter === b.kv}">
              <span class="map-swatch" style="background:var(--kv-${b.kv})"></span><span class="map-kv-name">${kvName(b.kv)}</span><span class="map-kv-count">${b.count}</span></button></li>`)}
              ${others ? html`<li class="map-kv static"><span class="map-swatch none"></span><span class="map-kv-name">Landesverband, Schüler Union</span><span class="map-kv-count">${others}</span></li>` : ''}</ul>
            <p class="small muted">Fläche = Bezirk, Zahl = Mitglieder des Kreisverbands auf der Karte. Antippen zeigt nur diesen Kreisverband.</p>
            ${mode === 'density' ? html`<div class="map-scale" aria-label="Mitglieder je Stadtteil">${steps.map((s, i) => html`<span><i style="background:var(--map-seq-${i + 1})"></i>bis ${s}</span>`)}</div>` : ''}
          </section>
          <section class="panel"><h2>Zu prüfen</h2>
            <div class="map-checks">${CHECKS.map(([key, title, sub]) => {
              const n = (check[key] || []).length + (key === 'unbekannt' ? (check.leer || []).length : 0);
              return html`<button type="button" class="map-check" data-check="${key}" ${n ? '' : 'disabled'}>
                <span><strong>${title}</strong><span class="sub">${sub}</span></span><span class="map-check-count ${n && key !== 'ausserhalb' ? 'warn' : ''}">${n}</span></button>`;
            })}</div>
          </section>
          <p class="small muted">${addressSummary(data.state)} · <button type="button" class="linklike" data-address-settings>aktualisieren</button><br>
            ${addressCredit()}${store.data.settings.map_tiles === 'yes' ? ' Kartenhintergrund: © OpenStreetMap-Mitwirkende – geladen werden nur Kartenausschnitte, keine Mitgliederdaten.' : ' Kartenhintergrund ist ausgeschaltet.'}</p>
        </aside>
      </div>`);
    body.querySelectorAll('[data-mode]').forEach((b) => (b.onclick = () => { mode = b.dataset.mode; draw(); }));
    body.querySelectorAll('[data-kv]').forEach((b) => (b.onclick = () => { kvFilter = kvFilter === b.dataset.kv ? '' : b.dataset.kv; draw(); }));
    body.querySelectorAll('[data-check]').forEach((b) => (b.onclick = () => checkDialog(b.dataset.check)));
    body.querySelector('[data-address-settings]').onclick = () => go('settings', { settingsTab: 'schalter' });
    bindSearch();
    drawMap();
  }

  function drawMap() {
    const el = body.querySelector('[data-map]');
    // Leaflet 1.9.4: ein direktes Neuzeichnen (z. B. nach „moveend“) vergisst den schon angeforderten Frame – der liefe nach dem Abbau der Karte ins Leere (clearRect)
    const renderer = new (L.Canvas.extend({ _redraw() { L.Util.cancelAnimFrame(this._redrawRequest); L.Canvas.prototype._redraw.call(this); } }))();
    map = L.map(el, { renderer, zoomSnap: 0.5, attributionControl: true, zoomControl: false, center: (view || START).center, zoom: (view || START).zoom, minZoom: 9, maxZoom: 19 });
    live = map;
    L.control.zoom({ zoomInTitle: 'Vergrößern', zoomOutTitle: 'Verkleinern' }).addTo(map);
    map.attributionControl.setPrefix(false);
    map.on('moveend', () => { view = { center: [map.getCenter().lat, map.getCenter().lng], zoom: map.getZoom() }; });
    const areas = [];        // Flächen der Kreisverbände: in der Übersicht kräftig, auf Straßenebene nur noch ein Hauch (Straßen bleiben lesbar)
    const zoomClass = () => {
      const far = map.getZoom() < 13;
      el.classList.toggle('map-far', far);   // Namen der Kreisverbände nur in der Übersicht
      areas.forEach(({ layer, fill }) => layer.setStyle({ fillOpacity: far ? fill : fill / 3 }));
    };
    map.on('zoomend', zoomClass);
    if (store.data.settings.map_tiles === 'yes') {
      L.tileLayer(TILES, { maxZoom: 19, referrerPolicy: 'origin', attribution: '© OpenStreetMap-Mitwirkende' }).addTo(map);
    } else {
      el.classList.add('map-plain');
    }
    const muted = css('--muted'), navy = css('--navy'), orange = css('--orange');

    // Stadtteile: dünne Grenzen; in der Dichte-Ansicht gefüllt
    const steps = densitySteps(Math.max(0, ...Object.values(data.stadtteile)));
    for (const s of geo.stadtteile) {
      const n = data.stadtteile[s.name] || 0;
      const level = n ? steps.findIndex((limit) => n <= limit) + 1 : 0;
      const layer = L.polygon(latlngs(s.flaechen), mode === 'density'
        ? { color: '#fff', weight: 1, fillColor: level ? css(`--map-seq-${level}`) : '#fff', fillOpacity: level ? 0.78 : 0.15 }
        : { color: muted, weight: 1, opacity: 0.45, fill: true, fillOpacity: 0 }).addTo(map);
      layer.bindTooltip(tipNode([s.name], mode === 'density' ? `${n} ${n === 1 ? 'Mitglied' : 'Mitglieder'}` : `Bezirk ${s.bezirk}`), { sticky: true, className: 'map-tip' });
    }
    // Bezirke = Kreisverbände: farbige Fläche mit Namen
    for (const b of geo.bezirke) {
      const color = css(`--kv-${b.kv}`);
      const dim = kvFilter && kvFilter !== b.kv;
      const fill = mode === 'density' ? 0 : dim ? 0.04 : 0.16;
      const layer = L.polygon(latlngs(b.flaechen), { color, weight: mode === 'density' ? 2 : 2.5, opacity: dim ? 0.35 : 1, fillColor: color,
        fillOpacity: fill, interactive: false }).addTo(map);
      areas.push({ layer, fill });
      L.tooltip({ permanent: true, direction: 'center', className: 'map-area-label', interactive: false }).setLatLng(layer.getBounds().getCenter())
        .setContent(tipNode([kvName(b.kv)])).addTo(map);
    }
    // Mitglieder: ein Punkt je Anschrift (mehrere Mitglieder an einer Anschrift: größerer Punkt)
    markers = new Map();
    if (mode === 'members') {
      const places = new Map();
      for (const p of data.points) {
        if (kvFilter && p.kv !== kvFilter) continue;
        const key = `${p.lat.toFixed(5)},${p.lon.toFixed(5)}`;
        if (!places.has(key)) places.set(key, []);
        places.get(key).push(p);
      }
      for (const group of places.values()) {
        const people = group.map((p) => ({ p, m: memberById(p.id) })).filter((x) => x.m);
        if (!people.length) continue;
        const other = group.some((p) => p.other), exact = group.every((p) => p.exact);
        const marker = L.circleMarker([group[0].lat, group[0].lon], { radius: people.length > 1 ? 8 : 6, color: other ? orange : '#fff', weight: other ? 3 : 2,
          fillColor: navy, fillOpacity: exact ? 0.95 : 0.45 }).addTo(map);
        const lines = people.slice(0, 6).map(({ m }) => `${fullName(m)} · ${kvName(m.kv)}`);
        if (people.length > 6) lines.push(`… und ${people.length - 6} weitere`);
        marker.bindTooltip(tipNode(lines, [!exact && 'Lage ungefähr', other && 'wohnt in einem anderen Bezirk'].filter(Boolean).join(' · ')), { className: 'map-tip' });
        marker.on('click', () => placeDialog(people.map((x) => x.m)));
        people.forEach(({ m }) => markers.set(m.id, marker));
      }
    }
    if (!view) map.fitBounds(L.latLngBounds(geo.bezirke.flatMap((b) => latlngs(b.flaechen).flat(2))), { padding: [12, 12], animate: false });
    zoomClass();
  }

  const addressOf = (m) => [m.street, [m.zip, m.city].filter(Boolean).join(' ')].filter(Boolean).join(', ');
  const edit = (m) => memberDialog(m, { onSaved: () => { toast('Mitglied gespeichert.'); } });   // refresh zeichnet die Karte neu

  /** Mitglieder an einem Punkt. */
  function placeDialog(list) {
    const el = dialog(list.length === 1 ? fullName(list[0]) : `${list.length} Mitglieder an dieser Anschrift`, html`
      ${table(['Mitglied', 'Anschrift', 'Kreisverband', ''], list.map((m, i) => html`<tr><td><strong style="font-weight:600">${fullName(m)}</strong><span class="sub">${m.number}</span></td>
        <td class="small">${addressOf(m)}</td><td class="small">${kvName(m.kv)}</td>
        <td class="nowrap"><button type="button" class="btn small" data-edit="${i}">Bearbeiten</button></td></tr>`))}
      <div class="form-actions"><button type="button" class="btn primary" data-close>Schließen</button></div>`, { wide: true });
    el.querySelector('.form-actions [data-close]').onclick = () => el.close();
    el.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = () => edit(list[Number(b.dataset.edit)])));
  }

  /** Liste „zu prüfen“: Mitglied öffnen und die Anschrift berichtigen. */
  function checkDialog(key) {
    const [, title] = CHECKS.find((c) => c[0] === key);
    const rows = [...(data.check[key] || []), ...(key === 'unbekannt' ? data.check.leer || [] : [])].map((x) => ({ ...x, m: memberById(x.id) })).filter((x) => x.m)
      .sort((a, b) => fullName(a.m).localeCompare(fullName(b.m), 'de'));
    const el = dialog(`${title} (${rows.length})`, html`
      <p class="small muted">${key === 'fremd' ? 'Das ist kein Fehler: Mitglieder dürfen einem anderen Kreisverband angehören. Die Liste hilft, Umzüge und Zuordnungen zu prüfen.'
        : key === 'ausserhalb' ? 'Für Orte außerhalb Hamburgs gibt es keine Adressdaten im Programm.' : 'Mitglied öffnen und die Anschrift berichtigen – beim Eingeben prüft das Programm sofort.'}</p>
      ${rows.length ? table(['Mitglied', 'Anschrift', 'Hinweis', ''], rows.map((x, i) => html`<tr><td><strong style="font-weight:600">${fullName(x.m)}</strong><span class="sub">${x.m.number} · ${kvName(x.m.kv)}</span></td>
        <td class="small">${addressOf(x.m) || '—'}</td><td class="small">${x.hint}</td>
        <td class="nowrap">${markers.has(x.id) ? html`<button type="button" class="btn small" data-show="${i}">Zeigen</button> ` : ''}<button type="button" class="btn small" data-edit="${i}">Bearbeiten</button></td></tr>`))
        : empty('Nichts zu prüfen', 'Alle Anschriften passen.')}
      <div class="form-actions"><button type="button" class="btn primary" data-close>Schließen</button></div>`, { wide: true });
    el.querySelector('.form-actions [data-close]').onclick = () => el.close();
    el.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = () => edit(rows[Number(b.dataset.edit)].m)));
    el.querySelectorAll('[data-show]').forEach((b) => (b.onclick = () => { el.close(); showMember(rows[Number(b.dataset.show)].id); }));
  }

  function showMember(id) {
    const marker = markers.get(id);
    if (!marker) return;
    map.setView(marker.getLatLng(), Math.max(map.getZoom(), 16));
    marker.openTooltip();
  }

  // ---- Suche: Mitglieder auf der Karte (im Browser) und Straßen im amtlichen Bestand (Server, ohne Mitgliederbezug)
  function bindSearch() {
    const input = body.querySelector('[data-search]'), box = body.querySelector('[data-results]');
    let timer = null, round = 0;
    const close = () => { box.hidden = true; mount(box, ''); };
    const run = async () => {
      const q = input.value.trim().toLowerCase(), mineRound = ++round;
      if (q.length < 3) return close();
      const onMap = new Set(data.points.map((p) => p.id)), today = new Date().toISOString().slice(0, 10);
      const people = store.data.members.filter((m) => (!m.left_on || m.left_on > today) && `${fullName(m)} ${m.number} ${m.street || ''}`.toLowerCase().includes(q))
        .sort((a, b) => onMap.has(b.id) - onMap.has(a.id)).slice(0, 6);
      let streets = [];
      try { streets = await api('/api/strassen-suche', { q }); } catch { streets = []; }   // POST: der Suchtext kann ein Name sein
      if (mineRound !== round || !alive()) return;
      box.hidden = false;
      mount(box, people.length || streets.length ? html`
        ${people.map((m) => html`<button type="button" class="map-result" data-member="${m.id}">${icon('users', 16)}<span><strong>${fullName(m)}</strong><span class="sub">${addressOf(m)} · ${kvName(m.kv)}${onMap.has(m.id) ? '' : ' · nicht auf der Karte – Anschrift prüfen'}</span></span></button>`)}
        ${streets.slice(0, 8).map((s, i) => html`<button type="button" class="map-result" data-street="${i}">${icon('search', 16)}<span><strong>${s.street}</strong><span class="sub">${s.zip} · ${s.stadtteil}</span></span></button>`)}`
        : html`<p class="small muted" style="margin:0;padding:10px 14px">Nichts gefunden.</p>`);
      box.querySelectorAll('[data-member]').forEach((b) => (b.onclick = () => {
        const id = Number(b.dataset.member), p = data.points.find((x) => x.id === id);
        close();
        if (!p) return edit(memberById(id));   // steht nicht auf der Karte: Mitglied öffnen, um die Anschrift zu berichtigen
        if (mode !== 'members' || (kvFilter && p.kv !== kvFilter)) { mode = 'members'; kvFilter = ''; view = { center: [p.lat, p.lon], zoom: 16 }; draw(); }
        showMember(id);
      }));
      box.querySelectorAll('[data-street]').forEach((b) => (b.onclick = () => { const s = streets[Number(b.dataset.street)]; close(); map.setView([s.lat, s.lon], 16); }));
    };
    input.oninput = () => { clearTimeout(timer); timer = setTimeout(() => safe(run), 250); };
    input.onkeydown = (e) => { if (e.key === 'Escape') { input.value = ''; close(); } };
  }

  safe(async () => {
    const [areas, next] = await Promise.all([geo || api('/api/karte-gebiete'), api('/api/karte'), library()]);
    if (!alive()) return;
    geo = areas;
    data = next;
    draw();
  });
}
