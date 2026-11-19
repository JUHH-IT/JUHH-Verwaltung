// Wahlberechtigung (0.37, Wunsch Lennert Gust 19.11.2026). Server: wahlrecht.py (nur lesend, Satzung § 37 und § 17).
// Stichtag wählen (Beitragsstichtag = Liste des Landesmitgliedschaftsausschusses, heute = laufender Status, oder ein anderes Datum), Kennzahlen,
// Zahlen je Kreis- und Ortsverband, Mitglieder mit Status und Grund (ohne Beträge), PDF und CSV. Stichtage: Einstellungen → Schalter.
import { api, download } from '../api.js';
import { html, mount } from '../html.js';
import { date, weekdayDate } from '../format.js';
import { store, kvName, go, can } from '../store.js';
import { pageHead, table, empty, safe, icon, badge } from '../ui.js';
import { account, wahlBadge } from './members.js';

let stichtag = null;   // null = eingestellter Beitragsstichtag, '' = heute (laufender Status), sonst JJJJ-MM-TT
let custom = false;    // „anderes Datum“ gewählt: Datumsfeld zeigen (z. B. der wirklich benutzte Stichtag eines früheren Jahres)
let kvFilter = '';
let ovFilter = '';     // '' = alle, 'none' = ohne Ortsverband, sonst id
let statusFilter = ''; // '' = Mitglieder am Stichtag (ohne Ausgetretene)
const LIMIT = 500;

export function wahlrecht(main) {
  if (!main.querySelector('.wr-page')) mount(main, html`<div class="page wide"><p class="small muted">Wird geladen …</p></div>`);
  safe(async () => {
    const q = `stichtag=${stichtag ?? store.data.wahl.beitrag}`;
    try {
      draw(main, await api(`/api/wahlrecht?${q}`), q);
    } catch (error) {   // z. B. Datum außerhalb des erlaubten Bereichs: zurück zum Beitragsstichtag, Meldung zeigt safe()
      const failed = stichtag;
      stichtag = null;
      if (failed) wahlrecht(main);
      throw error;
    }
  });
}

/** Seite zeichnen; Filter der Liste ohne neue Anfrage. */
function draw(main, r, q) {
  const s = r.stichtage, g = r.gesamt;
  const days = [[s.beitrag, `${weekdayDate(s.beitrag)} · Beitragsstichtag`], ...(s.letzter !== s.beitrag ? [[s.letzter, `${weekdayDate(s.letzter)} · letzter Beitragsstichtag`]] : []),
    ['', 'heute · laufender Status'], ['custom', 'anderes Datum …']];   // Datum vorn: auf dem Handy schneidet die Auswahl hinten ab
  const chosen = custom ? 'custom' : r.laufend ? '' : [s.beitrag, s.letzter].includes(r.stichtag) ? r.stichtag : 'custom';
  const ovs = kvFilter ? (r.kv.find((k) => k.code === kvFilter)?.ovs || []) : [];
  if (statusFilter === 'hinweis' && !r.hinweise) statusFilter = '';
  const list = r.mitglieder.filter((m) => (statusFilter === 'hinweis' ? m.hinweis : statusFilter ? m.status === statusFilter : m.status !== 'ausgetreten') && (!kvFilter || m.kv === kvFilter)
    && (!ovFilter || (ovFilter === 'none' ? !m.ov_id : String(m.ov_id) === ovFilter)));
  const preview = r.vorschau ? `Vorschau: Der Stichtag liegt in der Zukunft – gezeigt wird der Stand der Zahlungen von heute (${date(r.today)}). Was bis zum Stichtag noch eingeht, zählt mit.`
    : !r.gilt ? `Vorschau: Die Wahlberechtigung wird erst ab ${s.ab} ausgewertet – für ${r.stichtag.slice(0, 4)} gilt sie noch nicht.` : '';
  mount(main, html`<div class="page wide wr-page">
    ${pageHead('Mitgliederverwaltung · Satzung § 37 und § 17', 'Wahlberechtigung', html`
      <a class="btn" href="/api/wahlrecht.pdf?${q}" target="_blank" rel="noopener" title="Bericht mit Namen – vertraulich">${icon('file', 16)} PDF</a>
      <button type="button" class="btn" data-csv>${icon('download', 16)} CSV</button>`)}
    ${preview ? html`<p class="notice warn" data-preview>${preview}</p>` : ''}
    <div class="toolbar"><label class="field inline">Stichtag<select id="wr-day">${days.map(([k, label]) => html`<option value="${k}" ${k === chosen ? 'selected' : ''}>${label}</option>`)}</select></label>
      ${chosen === 'custom' ? html`<label class="field inline">Datum<input type="date" id="wr-custom" value="${r.laufend ? '' : r.stichtag}"></label>` : ''}
      <span class="small muted">Beitrittsstichtag ${weekdayDate(r.beitrittsstichtag)}${r.beitritt_zaehlt ? '' : ' (noch nicht erreicht – zählt heute noch nicht)'}
        ${can('einstellungen') ? html` · <button type="button" class="link" data-settings>Stichtage ändern</button>` : ''}</span></div>
    <section class="cards" aria-label="Kennzahlen">
      <div class="card"><span class="label">Wahlberechtigt</span><span class="value">${g.wahlberechtigt}</span>
        <span class="note">von ${g.wahlberechtigt + g.nicht} Mitgliedern am ${date(r.stichtag)}</span></div>
      <div class="card"><span class="label">Nicht wahlberechtigt</span><span class="value ${g.nicht ? 'over' : ''}">${g.nicht}</span>
        <span class="note">Grund steht in der Liste</span></div>
    </section>
    ${r.hinweise ? html`<p class="notice warn" data-hints><strong>${r.hinweise} mit Hinweis:</strong> Rücklastschrift nach dem Stichtag – bitte prüfen. Die Zahlung ging bis zum
      Stichtag ein und wurde danach zurückgebucht. Der Status folgt dem Stand am Stichtag; über diese Fälle entscheidet der Landesmitgliedschaftsausschuss.
      <button type="button" class="link" data-show-hints>Nur diese zeigen</button></p>` : ''}
    <section class="panel"><h2>Kreis- und Ortsverbände</h2>
      ${r.kv.length ? table(['Kreisverband / Ortsverband', { label: 'wahlberechtigt', className: 'num' }, { label: 'nicht', className: 'num' }],
        r.kv.flatMap((k) => [html`<tr><td><strong style="font-weight:600">${k.name}</strong></td><td class="num"><strong style="font-weight:600">${k.wahlberechtigt}</strong></td>
            <td class="num"><strong style="font-weight:600">${k.nicht}</strong></td></tr>`,
          ...k.ovs.map((o) => html`<tr><td class="small wr-ov">${o.label}</td><td class="num">${o.wahlberechtigt}</td><td class="num">${o.nicht}</td></tr>`)]))
        : empty('Keine Mitglieder', 'Am Stichtag gibt es keine Mitglieder.')}</section>
    <section class="panel"><div class="panel-head"><h2>Mitglieder</h2></div>
      <div class="toolbar" style="justify-content:flex-start">
        <label class="field inline">Kreisverband<select id="wr-kv"><option value="">alle</option>${r.kv.map((k) => html`<option value="${k.code}" ${k.code === kvFilter ? 'selected' : ''}>${k.name}</option>`)}</select></label>
        ${kvFilter ? html`<label class="field inline">Ortsverband<select id="wr-ov"><option value="">alle</option>${ovs.map((o) => html`<option value="${o.id ?? 'none'}" ${String(o.id ?? 'none') === ovFilter ? 'selected' : ''}>${o.label}</option>`)}</select></label>` : ''}
        <label class="field inline">Status<select id="wr-status">${[['', 'alle Mitglieder'], ['wahlberechtigt', 'wahlberechtigt'], ['nicht', 'nicht wahlberechtigt'], ['ausgetreten', 'ausgetreten'],
          ...(r.hinweise ? [['hinweis', 'mit Hinweis']] : [])]
          .map(([k, label]) => html`<option value="${k}" ${k === statusFilter ? 'selected' : ''}>${label}</option>`)}</select></label></div>
      ${list.length ? table(['Mitglied', 'Status und Grund', { label: 'Kreis- und Ortsverband', className: 'wr-wide' }], list.slice(0, LIMIT).map((m) => html`<tr class="clickable" data-member="${m.id}" tabindex="0">
        <td><strong style="font-weight:500">${m.name}</strong><span class="sub">${m.number}</span><span class="sub wr-narrow">${kvName(m.kv)}${m.ov ? ` · ${m.ov}` : ''}</span></td>
        <td>${wahlBadge(m.status)}${m.hinweis ? html` ${badge('bitte prüfen', 'warn')}` : ''}${m.gruende.length || m.hinweis
          ? html`<span class="sub">${[...m.gruende, m.hinweis].filter(Boolean).join(' · ')}</span>` : ''}</td>
        <td class="wr-wide">${kvName(m.kv)}${m.ov ? html`<span class="sub">${m.ov}</span>` : ''}</td></tr>`))
        : empty('Niemand in dieser Auswahl', 'Für Stichtag und Filter gibt es keine Mitglieder.')}
      <p class="small muted">${list.length > LIMIT ? `Die ersten ${LIMIT} von ${list.length} – Kreisverband oder Status eingrenzen. ` : `${list.length} Mitglieder. `}Der Status wird berechnet,
        nie von Hand gesetzt: Mitglied am Beitrittsstichtag, und die bis zum Stichtag fälligen Beiträge des Jahres und der zwei Jahre davor sind eingegangen
        (ein SEPA-Mandat allein reicht nicht). Beitragsfreie Mitglieder sind wahlberechtigt, Gastmitglieder nicht.</p>
    </section>
  </div>`);
  const redraw = () => draw(main, r, q);
  main.querySelector('#wr-day').onchange = (e) => {
    custom = e.target.value === 'custom';
    if (custom) return redraw();   // erst das Datum wählen
    stichtag = e.target.value;
    wahlrecht(main);
  };
  main.querySelector('#wr-custom')?.addEventListener('change', (e) => { if (e.target.value) { stichtag = e.target.value; wahlrecht(main); } });
  main.querySelector('[data-show-hints]')?.addEventListener('click', () => { statusFilter = 'hinweis'; redraw(); });
  main.querySelector('#wr-kv').onchange = (e) => { kvFilter = e.target.value; ovFilter = ''; redraw(); };
  main.querySelector('#wr-ov')?.addEventListener('change', (e) => { ovFilter = e.target.value; redraw(); });
  main.querySelector('#wr-status').onchange = (e) => { statusFilter = e.target.value; redraw(); };
  main.querySelector('[data-settings]')?.addEventListener('click', () => go('settings', { settingsTab: 'schalter' }));
  main.querySelector('[data-csv]').onclick = () => safe(() => download(`/api/wahlrecht.csv?${q}`, `Wahlberechtigung-${r.stichtag}.csv`));
  main.querySelectorAll('[data-member]').forEach((row) => {
    const open = () => account(Number(row.dataset.member));
    row.onclick = open;
    row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
  });
}
