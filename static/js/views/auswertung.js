// Auswertung der Mitgliederdaten (0.29, Wunsch Lennert Gust 10.11.2026). Server: auswertung.py (nur lesend).
// Stichtag und Auswahl Kreis-/Ortsverband; Kennzahlen, Entwicklung, Kreis- und Ortsverbände, Alter, Mitgliedsdauer, Geschlecht,
// Vereinigungen, Zahlweg, Beitrag je Kreisverband und Listen zum Handeln (Altersgrenze, Jubiläen, Geburtstage, neue Mitglieder,
// Datenlücken). Balken einfarbig mit beschrifteten Werten; das PDF enthält nur Zahlen.
import { api, download } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date } from '../format.js';
import { store, kvName, go } from '../store.js';
import { pageHead, table, empty, safe, icon, badge } from '../ui.js';
import { ovLabel, ovById } from '../gliederung.js';
import { account } from './members.js';

let stichtag = '';
let kvFilter = '';
let ovFilter = '';
let listTab = 'age_limit';

const pct = (v) => `${Math.round((v || 0) * 1000) / 10} %`.replace('.', ',');
const num = (v) => (v === null || v === undefined ? '—' : String(v).replace('.', ','));
const LISTS = {
  age_limit: ['Altersgrenze 35', 'Mitgliedschaft endet mit Vollendung des 35. Lebensjahres (Satzung § 2 Abs. 1 Nr. 4) – erreicht oder in den nächsten 12 Monaten. Ausnahme: Amtsträger (§ 2 Abs. 2).'],
  jubilees: ['Jubiläen', 'Mitglieder mit 5, 10, 15 oder 20 Jahren Mitgliedschaft im Jahr des Stichtags.'],
  birthdays: ['Geburtstage', 'Geburtstage in den nächsten 30 Tagen ab Stichtag.'],
  new: ['Neue Mitglieder', 'Eintritt in den letzten 90 Tagen vor dem Stichtag.'],
  gaps: ['Datenlücken', 'Fehlende Angaben bei Mitgliedern am Stichtag – in der Mitgliederverwaltung bzw. per Liste ergänzen.'],
};
const GAPS = { no_email: 'ohne E-Mail', no_phone: 'ohne Telefon', no_ov: 'ohne Ortsverband', no_zip: 'ohne PLZ (alte Anschrift)', no_mail: 'Postausschluss' };
let gapTab = 'no_email';

/** Einfarbige Balkenliste mit beschrifteten Werten (Anzahl und Anteil). */
function bars(title, rows, total) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return html`<section class="panel aw-bars"><h2>${title}</h2>${rows.length ? html`<ul>${rows.map((r) => html`<li title="${r.label}: ${r.count}${total ? ` (${pct(r.count / total)})` : ''}">
      <span class="aw-label">${r.label}</span><span class="aw-track"><span style="width:${Math.round((r.count / max) * 100)}%"></span></span>
      <span class="aw-value">${r.count}${total ? html`<small>${pct(r.count / total)}</small>` : ''}</span></li>`)}</ul>`
    : html`<p class="small muted">Keine Angaben.</p>`}</section>`;
}

let good = null;   // letzte gültige Auswahl: nach einem Fehler (z. B. Stichtag ungültig) dorthin zurück

export function auswertung(main) {
  if (!main.querySelector('.aw-page')) mount(main, html`<div class="page wide"><p class="small muted">Wird geladen …</p></div>`);
  safe(async () => {
    const q = `stichtag=${stichtag}&kv=${kvFilter}&ov=${ovFilter}`;
    try {
      const r = await api(`/api/auswertung?${q}`);
      stichtag = r.stichtag;
      good = { stichtag, kvFilter, ovFilter };
      draw(main, r, q);
    } catch (err) {
      ({ stichtag, kvFilter, ovFilter } = good || { stichtag: '', kvFilter: '', ovFilter: '' });
      if (!main.querySelector('.aw-page')) {
        mount(main, html`<div class="page wide"><p class="notice warn">Auswertung nicht möglich.</p><button type="button" class="btn" data-retry>Erneut laden</button></div>`);
        main.querySelector('[data-retry]').onclick = () => auswertung(main);
      } else {
        const day = main.querySelector('#aw-day');
        if (day) day.value = stichtag;
      }
      throw err;
    }
  });
}

/** Seite zeichnen; Reiterwechsel ohne neue Anfrage. */
function draw(main, r, q) {
  const c = r.cards;
  const kvs = store.data.kv.filter((k) => k.code !== 'LV' && store.data.members.some((m) => m.kv === k.code));
  const ovChoices = kvFilter ? (store.data.ovs || []).filter((o) => o.kv === kvFilter && (o.members || o.active)) : [];
  const maxEnd = Math.max(1, ...r.development.map((d) => d.end));
  const scope = [kvFilter && kvName(kvFilter), ovFilter === 'none' ? 'ohne Ortsverband' : ovFilter && ovLabel(ovById(Number(ovFilter)))].filter(Boolean).join(' · ') || 'alle Kreisverbände';
  const list = r.lists[listTab];
  const rows = listTab === 'gaps' ? r.lists.gaps[gapTab].items : list;
  mount(main, html`<div class="page wide aw-page">
    ${pageHead(`Mitgliederdaten · Stand ${date(r.stichtag)} · ${scope}`, 'Auswertung', html`
      <a class="btn" href="/api/auswertung.pdf?${q}" target="_blank" rel="noopener" title="Nur Zahlen, keine Namen – z. B. für den Vorstand">${icon('file', 16)} PDF</a>
      <button type="button" class="btn" data-csv>${icon('download', 16)} CSV</button>
      <button type="button" class="btn" data-op title="Offene Beiträge je Mitglied für die Kreisverbände">OP-Listen</button>`)}
    <div class="toolbar"><label class="field inline">Stichtag<input type="date" id="aw-day" min="2000-01-01" value="${r.stichtag}"></label>
      <label class="field inline">Kreisverband<select id="aw-kv"><option value="">alle</option>${kvs.map((k) => html`<option value="${k.code}" ${k.code === kvFilter ? 'selected' : ''}>${k.name}</option>`)}</select></label>
      ${kvFilter ? html`<label class="field inline">Ortsverband<select id="aw-ov"><option value="">alle</option><option value="none" ${ovFilter === 'none' ? 'selected' : ''}>ohne Ortsverband</option>
        ${ovChoices.map((o) => html`<option value="${o.id}" ${String(o.id) === ovFilter ? 'selected' : ''}>${ovLabel(o)}</option>`)}</select></label>` : ''}</div>
    <section class="cards" aria-label="Kennzahlen">
      <div class="card"><span class="label">Mitglieder am Stichtag</span><span class="value">${c.active}</span>
        <span class="note">${c.guests} Gast · ${c.dues_free} beitragsfrei</span></div>
      <div class="card"><span class="label">Entwicklung ${r.year}</span><span class="value ${c.net_year < 0 ? 'over' : ''}">${c.net_year > 0 ? '+' : ''}${c.net_year}</span>
        <span class="note">${c.joined_year} Eintritte · ${c.left_year} Austritte bis zum Stichtag</span></div>
      <div class="card"><span class="label">Durchschnittsalter</span><span class="value">${num(c.avg_age)}</span><span class="note">Median ${num(c.median_age)} Jahre</span></div>
      <div class="card"><span class="label">Erreichbarkeit</span><span class="value">${pct(c.with_email)}</span>
        <span class="note">mit E-Mail · ${pct(c.with_mandate)} mit SEPA-Mandat · ${pct(c.with_ov)} mit Ortsverband</span></div>
    </section>
    <div class="aw-grid">
      <section class="panel"><h2>Entwicklung</h2>${table(['Jahr', { label: 'Eintritte', className: 'num' }, { label: 'Austritte', className: 'num' }, 'Bestand'],
        r.development.map((d) => html`<tr><td>${d.year}${d.until.slice(5) !== '12-31' ? html`<span class="sub">bis ${date(d.until)}</span>` : ''}</td>
          <td class="num">${d.joined}</td><td class="num">${d.left}</td>
          <td><span class="aw-inline"><span class="aw-track"><span style="width:${Math.round((d.end / maxEnd) * 100)}%"></span></span><span class="aw-value">${d.end}</span></span></td></tr>`))}
        <p class="small muted">Bestand am 31.12. (laufendes Jahr: am Stichtag). Austritt = letzter Tag der Mitgliedschaft.</p></section>
      ${bars('Alter', r.ages, c.active)}
    </div>
    <section class="panel"><div class="panel-head"><h2>Kreisverbände ${r.year}</h2><span class="small muted">Beitrag: Soll und gezahlt im Jahr ${r.year}; Rückstand Stand heute</span></div>
      ${r.kv_rows.length ? table(['Kreisverband', { label: 'Mitglieder', className: 'num' }, 'Anteil', { label: 'Ø Alter', className: 'num' }, { label: 'Ein-/Austritte', className: 'num' },
        { label: 'Beitrag Soll', className: 'num' }, { label: 'Gezahlt', className: 'num' }, { label: 'Rückstand', className: 'num' }, ''],
        r.kv_rows.map((k) => html`<tr><td>${k.name}</td><td class="num">${k.active}</td>
          <td><span class="aw-inline"><span class="aw-track"><span style="width:${Math.round(k.share * 100)}%"></span></span><span class="aw-value">${pct(k.share)}</span></span></td>
          <td class="num">${num(k.avg_age)}</td><td class="num">+${k.joined_year} / −${k.left_year}</td><td class="num">${eur(k.soll)}</td>
          <td class="num">${eur(k.paid)}${k.paid_share !== null ? html`<span class="sub">${pct(k.paid_share)}</span>` : ''}</td>
          <td class="num ${k.arrears ? 'over' : ''}">${eur(k.arrears)}${k.arrears_count ? html`<span class="sub">${k.arrears_count} Mitglieder</span>` : ''}</td>
          <td><button type="button" class="btn small" data-op-kv="${k.code}">OP-Liste</button></td></tr>`), { className: 'kv-table' })
        : empty('Keine Mitglieder', 'Für die Auswahl gibt es keine Mitglieder.')}</section>
    ${r.ov_rows.length ? html`<section class="panel"><h2>Ortsverbände</h2>${table(['Ortsverband', 'Kreisverband', { label: 'Mitglieder', className: 'num' }, { label: 'Ø Alter', className: 'num' }],
      r.ov_rows.map((o) => html`<tr><td>${o.label}</td><td>${kvName(o.kv)}</td><td class="num">${o.active}</td><td class="num">${num(o.avg_age)}</td></tr>`))}</section>` : ''}
    <div class="aw-grid three">
      ${bars('Mitgliedsdauer', r.tenure, c.active)}
      ${bars('Geschlecht', r.gender, c.active)}
      ${bars('Zahlweg', r.payment, c.active)}
      ${bars('Vereinigungen', r.clubs, c.active)}
    </div>
    <section class="panel"><div class="panel-head"><h2>Listen zum Handeln</h2></div>
      <div class="tabs" role="tablist" aria-label="Liste">${Object.entries(LISTS).map(([k, [label]]) => html`<button type="button" role="tab" data-list="${k}" aria-selected="${k === listTab}">${label}<span class="n">${
        k === 'gaps' ? Object.values(r.lists.gaps).reduce((s, g) => s + g.count, 0) : r.lists[k].length}</span></button>`)}</div>
      <p class="small muted">${LISTS[listTab][1]}</p>
      ${listTab === 'gaps' ? html`<div class="kv-chips" role="group" aria-label="Lücke">${Object.entries(GAPS).map(([k, label]) => html`<button type="button" class="kv-chip" data-gap="${k}" aria-pressed="${k === gapTab}">${label} <span>${r.lists.gaps[k].count}</span></button>`)}</div>` : ''}
      ${rows.length ? table(['Mitglied', 'Kreis-/Ortsverband', listTab === 'gaps' ? '' : 'Datum', ''], rows.map((x) => html`<tr class="clickable" data-member="${x.id}" tabindex="0">
        <td><strong style="font-weight:500">${x.name}</strong><span class="sub">${x.number}</span></td>
        <td>${kvName(x.kv)}${x.ov_id ? html`<span class="sub">${ovLabel(ovById(x.ov_id))}</span>` : ''}</td>
        <td>${x.on ? date(x.on) : ''}</td>
        <td>${listTab === 'age_limit' ? (x.reached ? badge('erreicht', 'warn') : badge('demnächst')) : listTab === 'jubilees' ? badge(`${x.years} Jahre`, 'ok')
          : listTab === 'birthdays' ? `wird ${x.age}` : ''}</td></tr>`)) : empty('Niemand in dieser Liste', 'Für Stichtag und Auswahl gibt es keine Einträge.')}
      ${listTab === 'gaps' && r.lists.gaps[gapTab].count > rows.length ? html`<p class="small muted">Die ersten ${rows.length} von ${r.lists.gaps[gapTab].count}.</p>` : ''}
    </section>
    <p class="small muted">Mitglied am Stichtag: eingetreten bis zum Stichtag, nicht vorher ausgetreten. Alter in vollendeten Jahren am Stichtag. Kreis- und Ortsverband = heutige Zuordnung.</p>
  </div>`);
  main.querySelector('#aw-day').onchange = (e) => { if (e.target.value) { stichtag = e.target.value; auswertung(main); } };
  main.querySelector('#aw-kv').onchange = (e) => { kvFilter = e.target.value; ovFilter = ''; auswertung(main); };
  main.querySelector('#aw-ov')?.addEventListener('change', (e) => { ovFilter = e.target.value; auswertung(main); });
  main.querySelector('[data-csv]').onclick = () => safe(() => download(`/api/auswertung.csv?${q}`, `Mitgliederstatistik-${r.stichtag}.csv`));
  main.querySelector('[data-op]').onclick = () => go('openitems', { openSide: 'dues', opKv: kvFilter });   // OP-Listen stehen in der Buchhaltung
  main.querySelectorAll('[data-op-kv]').forEach((b) => (b.onclick = () => go('openitems', { openSide: 'dues', opKv: b.dataset.opKv })));
  main.querySelectorAll('[data-list]').forEach((b) => (b.onclick = () => { listTab = b.dataset.list; draw(main, r, q); }));
  main.querySelectorAll('[data-gap]').forEach((b) => (b.onclick = () => { gapTab = b.dataset.gap; draw(main, r, q); }));
  main.querySelectorAll('[data-member]').forEach((row) => {
    const open = () => account(Number(row.dataset.member));
    row.onclick = open;
    row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
  });
}
