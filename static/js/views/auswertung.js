// Auswertung der Mitgliederdaten (0.29, Wunsch Lennert Gust 10.11.2026). Server: auswertung.py (nur lesend).
// Stichtag und Auswahl Kreis-/Ortsverband; Kennzahlen, Altersstruktur (Altersgruppen, Pyramide), Geschlecht (gesamt, Eintritte, je
// Kreisverband), Kreisverbände im Vergleich, Entwicklung (Bestand, Ein-/Austritte je Jahr, Eintritte je Monat), Eintrittsalter,
// Mitgliedsdauer, Zahlweg, Vereinigungen und Listen zum Handeln. Diagramme (ab 0.38) sind Listen/Tabellen aus HTML und CSS: jeder Wert
// steht als Text am Balken (lesbar ohne Zeigen, auch für Vorleseprogramme). Farben: einfarbig Petrol; Geschlecht weiblich/männlich/
// divers-ohne-Angabe (--aw-w/-m/-x, geprüfte Farbreihe) immer mit Beschriftung. Das PDF enthält nur Zahlen.
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

const MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
const SEXES = [['w', 'weiblich'], ['m', 'männlich'], ['x', 'divers/ohne Angabe']];
const few = (v) => (v === null ? '< 3' : v);   // Rollen ohne Mitgliederrecht: der Server nennt Gruppen unter 3 Personen nicht (auswertung.redact)
const signed = (v) => `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v)}`;
const swatch = (k) => html`<i class="aw-sw ${k}" aria-hidden="true"></i>`;
const legend = (items) => html`<ul class="aw-legend">${items.map(([k, label]) => html`<li>${swatch(k)}${label}</li>`)}</ul>`;

/** Einfarbige Balkenliste mit beschrifteten Werten. Zeile: label, count, optional text (statt Anzahl), sub (statt Anteil), pick (Kreisverband wählen). */
function bars(title, rows, total, names = false) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return html`<div class="aw-bars ${names ? 'names' : ''}"><h3>${title}</h3>${rows.length ? html`<ul>${rows.map((r) => {
    const sub = r.sub ?? (total ? pct(r.count / total) : '');
    return html`<li title="${r.label}: ${r.text ?? r.count}${sub ? ` (${sub})` : ''}">
      ${r.pick ? html`<button type="button" class="aw-label aw-pick" data-kv-pick="${r.pick}" title="Nur ${r.label} auswerten">${r.label}</button>` : html`<span class="aw-label">${r.label}</span>`}
      <span class="aw-track"><span style="width:${Math.round((r.count / max) * 100)}%"></span></span>
      <span class="aw-value">${r.text ?? r.count}${sub ? html`<small>${sub}</small>` : ''}</span></li>`;
  })}</ul>` : html`<p class="small muted">Keine Angaben.</p>`}</div>`;
}

/** Zweiseitige Balken an einer Mittelachse (Alterspyramide, Aus-/Eintritte je Kreisverband) als Tabelle: Wert steht am Balken. */
function twoSided(caption, mid, left, right, rows, extra = null) {
  const max = Math.max(1, ...rows.flatMap((r) => [r.l ?? 1, r.r ?? 1]));
  const side = (v, k) => html`<span class="aw-side"><span class="aw-value">${few(v)}</span><span class="aw-bar"><span class="${k}" style="width:${Math.round(((v ?? 1) / max) * 100)}%"></span></span></span>`;
  return html`<table class="aw-two ${extra ? 'wide' : ''}"><caption class="sr-only">${caption}</caption>
    <thead><tr><th scope="col" class="l">${swatch(left[0])}${left[1]}</th><th scope="col">${mid}</th><th scope="col" class="r">${swatch(right[0])}${right[1]}</th>${extra ? html`<th scope="col" class="num">${extra}</th>` : ''}</tr></thead>
    <tbody>${rows.map((r) => html`<tr><td class="l">${side(r.l, left[0])}</td><th scope="row">${r.label}</th><td class="r">${side(r.r, right[0])}</td>${extra ? html`<td class="num">${r.extra}</td>` : ''}</tr>`)}</tbody></table>`;
}

/** Eine Zeile „100-%-Balken“ nach Geschlecht; die Zahlen stehen daneben (Anzahl und Anteil). */
function sexRow(label, total, counts, strong = false) {
  return html`<li class="${strong ? 'strong' : ''}"><span class="aw-label">${label}</span>
    <span class="aw-seg" aria-hidden="true">${SEXES.map(([k]) => (counts[k] === 0 ? '' : html`<span class="${k}" style="flex-grow:${counts[k] ?? 1}"></span>`))}</span>
    ${SEXES.map(([k, name]) => html`<span class="aw-n">${swatch(k)}<span class="sr-only">${name}: </span>${few(counts[k])}${counts[k] && total ? html`<small>${pct(counts[k] / total)}</small>` : ''}</span>`)}</li>`;
}

/** Säulen mit Wert über der Säule; `down` zeichnet eine zweite Reihe nach unten (Austritte). `slots` hält Platz für fehlende Monate frei. */
function columns(title, items, { down = false, slots = 0 } = {}) {
  const H = 96, max = Math.max(1, ...items.flatMap((i) => [i.value, i.down || 0]));
  const px = (v) => (v ? Math.max(2, Math.round((v / max) * H)) : 0);
  const low = px(Math.max(0, ...items.map((i) => i.down || 0))) + 18;   // Platz für die längste Säule nach unten und ihren Wert
  return html`<div class="aw-chart"><h3>${title}</h3>
    <ol class="aw-cols ${down ? 'both' : ''}">${items.map((i) => html`<li title="${i.title}"><span class="sr-only">${i.title}</span>
      <span class="aw-up" aria-hidden="true"><span class="aw-value">${i.value}</span><span class="aw-col t" style="height:${px(i.value)}px"></span></span>
      ${down ? html`<span class="aw-down" aria-hidden="true" style="height:${low}px"><span class="aw-col x" style="height:${px(i.down)}px"></span><span class="aw-value">${i.down}</span></span>` : ''}
      <span class="aw-x" aria-hidden="true">${i.label}</span></li>`)}${Array.from({ length: Math.max(0, slots - items.length) }, (_, k) => html`<li aria-hidden="true"><span class="aw-up"></span><span class="aw-x muted">${MONTHS[items.length + k]}</span></li>`)}</ol>
    ${down ? legend([['t', 'Eintritte (nach oben)'], ['x', 'Austritte (nach unten)']]) : ''}</div>`;
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
  const money = (value) => (r.amounts === false ? '—' : eur(value));   // ohne Buchhaltungsrecht liefert der Server keine Beträge
  const c = r.cards;
  const kvs = store.data.kv.filter((k) => k.code !== 'LV' && store.data.members.some((m) => m.kv === k.code));
  const ovChoices = kvFilter ? (store.data.ovs || []).filter((o) => o.kv === kvFilter && (o.members || o.active)) : [];
  const partial = r.development.find((d) => d.until.slice(5) !== '12-31');
  const others = r.gender.filter((g) => !['weiblich', 'männlich'].includes(g.label));
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
    <section class="cards aw-cards" aria-label="Kennzahlen">
      <div class="card"><span class="label">Mitglieder am Stichtag</span><span class="value">${c.active}</span>
        <span class="note">${c.guests} Gast · ${c.dues_free} beitragsfrei</span></div>
      <div class="card"><span class="label">Entwicklung ${r.year}</span><span class="value ${c.net_year < 0 ? 'over' : ''}">${c.net_year > 0 ? '+' : ''}${c.net_year}</span>
        <span class="note">${c.joined_year} Eintritte · ${c.left_year} Austritte bis zum Stichtag</span></div>
      <div class="card"><span class="label">Durchschnittsalter</span><span class="value">${num(c.avg_age)}</span><span class="note">Median ${num(c.median_age)} Jahre</span></div>
      <div class="card"><span class="label">Frauenanteil</span><span class="value">${pct(c.women_share)}</span>
        <span class="note">${c.women} weiblich · ${c.men} männlich · ${c.active - c.women - c.men} divers/ohne Angabe</span></div>
      <div class="card"><span class="label">Erreichbarkeit</span><span class="value">${pct(c.with_email)}</span>
        <span class="note">mit E-Mail · ${pct(c.with_mandate)} mit SEPA-Mandat · ${pct(c.with_ov)} mit Ortsverband</span></div>
    </section>
    <section class="panel"><h2>Altersstruktur</h2>
      <div class="aw-grid">
        ${bars('Altersgruppen', r.ages, c.active)}
        <div class="aw-chart"><h3>Alterspyramide</h3>
          ${twoSided('Mitglieder je Altersgruppe, männlich und weiblich', 'Alter', ['m', 'männlich'], ['w', 'weiblich'], r.ages.map((a) => ({ label: a.label, l: a.m, r: a.w })))}
          <p class="small muted">Nur männlich und weiblich; ${r.age_stats[2].count} Mitglieder divers/ohne Angabe${c.no_birth ? html`, ${c.no_birth} ohne Geburtsdatum` : ''}.</p>
          ${table(['Geschlecht', { label: 'Mitglieder', className: 'num' }, { label: 'Ø Alter', className: 'num' }, { label: 'Median', className: 'num' }],
            [...r.age_stats.map((a) => html`<tr><td>${swatch(a.key)}${a.label}</td><td class="num">${a.count}</td><td class="num">${num(a.avg_age)}</td><td class="num">${num(a.median_age)}</td></tr>`),
              html`<tr><td>gesamt</td><td class="num">${c.active}</td><td class="num">${num(c.avg_age)}</td><td class="num">${num(c.median_age)}</td></tr>`], { className: 'aw-small' })}
        </div>
      </div></section>
    <section class="panel"><div class="panel-head"><h2>Geschlecht</h2>${legend(SEXES)}</div>
      <ul class="aw-sex">${sexRow('Mitglieder gesamt', c.active, { w: c.women, m: c.men, x: c.active - c.women - c.men }, true)}
        ${sexRow(`Eintritte ${r.year}`, c.joined_year, r.joined_gender)}
        ${r.kv_rows.length > 1 ? r.kv_rows.map((k) => sexRow(k.name, k.active, k)) : ''}</ul>
      <p class="small muted">Anteil an allen Mitgliedern der Zeile.${others.length ? html` Divers/ohne Angabe gesamt: ${others.map((g) => `${g.count} ${g.label}`).join(', ')}.` : ''}</p></section>
    <section class="panel"><div class="panel-head"><h2>Kreisverbände ${r.year}</h2><span class="small muted">Beitrag: Soll und gezahlt im Jahr ${r.year}; Rückstand Stand heute</span></div>
      ${r.kv_rows.length > 1 ? html`<div class="aw-grid three">
        ${bars('Mitglieder', r.kv_rows.map((k) => ({ label: k.name, count: k.active, pick: k.code })), c.active, true)}
        ${bars('Ø Alter', r.kv_rows.map((k) => ({ label: k.name, count: k.avg_age || 0, text: num(k.avg_age), sub: 'Jahre' })), 0, true)}
        <div class="aw-chart"><h3>Aus- und Eintritte ${r.year}</h3>
          ${twoSided(`Austritte, Eintritte und Saldo ${r.year} je Kreisverband`, 'Kreisverband', ['x', 'Austritte'], ['t', 'Eintritte'],
            r.kv_rows.map((k) => ({ label: k.name, l: k.left_year, r: k.joined_year, extra: signed(k.net_year) })), 'Saldo')}</div>
      </div>` : ''}
      ${r.kv_rows.length ? table(['Kreisverband', { label: 'Mitglieder', className: 'num' }, { label: 'Ø Alter', className: 'num' }, { label: 'Frauenanteil', className: 'num' },
        { label: `Saldo ${r.year}`, className: 'num' }, { label: 'Beitrag Soll', className: 'num' }, { label: 'Gezahlt', className: 'num' }, { label: 'Rückstand', className: 'num' }, ''],
        r.kv_rows.map((k) => html`<tr><td>${k.name}</td><td class="num">${k.active}<span class="sub">${pct(k.share)}</span></td>
          <td class="num">${num(k.avg_age)}</td><td class="num">${k.women_share === null ? '—' : pct(k.women_share)}</td>
          <td class="num"><span class="${k.net_year < 0 ? 'over' : ''}">${signed(k.net_year)}</span><span class="sub">${k.joined_year} ein · ${k.left_year} aus</span></td><td class="num">${money(k.soll)}</td>
          <td class="num">${money(k.paid)}${k.paid_share !== null ? html`<span class="sub">${pct(k.paid_share)}</span>` : ''}</td>
          <td class="num ${k.arrears ? 'over' : ''}">${money(k.arrears)}${k.arrears_count ? html`<span class="sub">${k.arrears_count} Mitglieder</span>` : ''}</td>
          <td><button type="button" class="btn small" data-op-kv="${k.code}">OP-Liste</button></td></tr>`), { className: 'kv-table aw-kv' })
        : empty('Keine Mitglieder', 'Für die Auswahl gibt es keine Mitglieder.')}</section>
    ${r.ov_rows.length ? html`<section class="panel"><h2>Ortsverbände</h2>${table(['Ortsverband', 'Kreisverband', { label: 'Mitglieder', className: 'num' }, { label: 'Ø Alter', className: 'num' }],
      r.ov_rows.map((o) => html`<tr><td>${o.label}</td><td>${kvName(o.kv)}</td><td class="num">${o.active}</td><td class="num">${num(o.avg_age)}</td></tr>`))}</section>` : ''}
    <section class="panel"><h2>Entwicklung</h2>
      <div class="aw-grid three">
        ${columns('Bestand am 31.12.', r.development.map((d) => ({ label: d.year, value: d.end, title: `${d.year}: ${d.end} Mitglieder` })))}
        ${columns('Ein- und Austritte je Jahr', r.development.map((d) => ({ label: d.year, value: d.joined, down: d.left, title: `${d.year}: ${d.joined} Eintritte, ${d.left} Austritte` })), { down: true })}
        ${columns(`Eintritte ${r.year} je Monat`, r.months.map((n, i) => ({ label: MONTHS[i], value: n, title: `${MONTHS[i]} ${r.year}: ${n} Eintritte` })), { slots: 12 })}
      </div>
      <p class="small muted">${partial ? `${partial.year}: Stand ${date(partial.until)}. ` : ''}Austritt = letzter Tag der Mitgliedschaft.</p>
      <details class="aw-details"><summary>Zahlen als Tabelle</summary>${table(['Jahr', { label: 'Eintritte', className: 'num' }, { label: 'Austritte', className: 'num' }, { label: 'Saldo', className: 'num' }, { label: 'Bestand', className: 'num' }],
        r.development.map((d) => html`<tr><td>${d.year}${d.until.slice(5) !== '12-31' ? html`<span class="sub">bis ${date(d.until)}</span>` : ''}</td>
          <td class="num">${d.joined}</td><td class="num">${d.left}</td><td class="num">${signed(d.joined - d.left)}</td><td class="num">${d.end}</td></tr>`), { className: 'aw-small' })}</details></section>
    <section class="panel"><h2>Eintritt, Mitgliedsdauer und Weiteres</h2>
      <div class="aw-grid three">
        ${bars('Alter beim Eintritt', r.entry_ages, c.active)}
        ${bars('Mitgliedsdauer', r.tenure, c.active)}
        ${bars('Zahlweg', r.payment, c.active)}
        ${bars('Vereinigungen', r.clubs, c.active)}
      </div></section>
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
  main.querySelectorAll('[data-kv-pick]').forEach((b) => (b.onclick = () => { kvFilter = b.dataset.kvPick; ovFilter = ''; auswertung(main); }));
  main.querySelectorAll('[data-op-kv]').forEach((b) => (b.onclick = () => go('openitems', { openSide: 'dues', opKv: b.dataset.opKv })));
  main.querySelectorAll('[data-list]').forEach((b) => (b.onclick = () => { listTab = b.dataset.list; draw(main, r, q); }));
  main.querySelectorAll('[data-gap]').forEach((b) => (b.onclick = () => { gapTab = b.dataset.gap; draw(main, r, q); }));
  main.querySelectorAll('[data-member]').forEach((row) => {
    const open = () => account(Number(row.dataset.member));
    row.onclick = open;
    row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
  });
}
