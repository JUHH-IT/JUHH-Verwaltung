// Konten (Wunsch Lennert Gust 29.10.2026: „jedes Konto anschauen“ und „wirklich alle Konten in dem Kontenrahmen“):
// Kontenliste nach Klassen und Gruppen des CDU-Kontenrahmens mit Bewegungen im Haushaltsjahr, Kontenblatt je Konto
// (Buchungen mit laufendem Saldo, Filter nach Bereich, CSV, Drucken). Rechnet nichts selbst – alles vom Server.
import { api, download } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date } from '../format.js';
import { store, render, kvName, titleLabel } from '../store.js';
import { pageHead, icon, table, empty, badge, safe } from '../ui.js';

/** Saldo Soll − Haben mit S/H wie auf einem Kontenblatt. */
export const saldo = (c) => (c ? html`<span class="${c > 0 ? 'saldo-s' : 'saldo-h'}">${eur(Math.abs(c))} ${c > 0 ? 'S' : 'H'}</span>` : eur(0));

/** Daten einmal je Adresse laden und danach neu zeichnen; false = Fehler (kein erneuter Versuch bis „Erneut laden“). */
function load(url, key) {
  const k = store.konten;
  if (k[key + 'Key'] === url) return k[key];
  k[key + 'Key'] = url;
  k[key] = null;
  api(url).then((data) => { k[key] = data; }, (e) => { k[key] = false; k.error = e.message || String(e); })
    .finally(() => { if (store.page === 'konten' && k[key + 'Key'] === url) render(); });
  return null;
}

const failed = () => html`<p class="notice warn">Konnte nicht geladen werden: ${store.konten.error} <button type="button" class="link" data-retry>Erneut laden</button></p>`;
const bindRetry = (main) => main.querySelector('[data-retry]')?.addEventListener('click', () => { store.konten.listKey = store.konten.sheetKey = null; render(); });

function list(main) {
  const k = store.konten;
  const rows = load(`/api/konten?year=${store.year}`, 'list');
  const q = k.search.toLowerCase().trim();
  const shown = (rows || []).filter((a) => (!k.onlyUsed || a.count || a.opening) && (!q || `${a.code} ${a.name} ${a.kr_name} ${a.group} ${a.kind_label}`.toLowerCase().includes(q)));
  let lastClass = '', lastGroup = '';
  const body = [];
  for (const a of shown) {
    if (a.class !== lastClass) { body.push(html`<tr class="konten-class"><td colspan="7">${a.class_label}</td></tr>`); lastClass = a.class; lastGroup = ''; }
    if (a.group !== lastGroup) { body.push(html`<tr class="konten-group"><td colspan="7">${a.group}</td></tr>`); lastGroup = a.group; }
    body.push(html`<tr class="clickable" data-konto="${a.code}" tabindex="0">
      <td class="mono">${a.code}</td>
      <td><strong style="font-weight:500">${a.name}</strong>${a.kr_name && a.kr_name !== a.name ? html`<span class="sub">Kontenrahmen: ${a.kr_name}</span>` : ''}
        ${a.ju_only ? html`<span class="sub">Konto der JU Hamburg (nicht im Kontenrahmen)</span>` : ''}</td>
      <td class="small">${a.kind_label}${a.system ? html`<span class="sub">${a.system}</span>` : ''}</td>
      <td class="small">${a.titles.length ? a.titles.map((t, i) => html`${i ? html`<br>` : ''}${titleLabel(t)}`) : a.code === 'KV' ? 'Zeile des Kreisverbands' : html`<span class="muted">—</span>`}</td>
      <td class="num">${a.debit ? eur(a.debit) : ''}</td><td class="num">${a.credit ? eur(a.credit) : ''}</td>
      <td class="num">${a.count || a.opening ? saldo(a.balance) : ''}</td></tr>`);
  }
  mount(main, html`<div class="page wide">
    ${pageHead(`Kontenrahmen der CDU (gültig ab 1.1.2016) · ${store.year}`, 'Konten')}
    <div class="toolbar"><label class="search grow">${icon('search', 16)}<span class="sr-only">Konten suchen</span>
        <input id="konten-search" type="search" autocomplete="off" placeholder="Kontonummer, Bezeichnung oder Gruppe" value="${k.search}"></label>
      <label class="check"><input type="checkbox" id="konten-used" ${k.onlyUsed ? 'checked' : ''}> Nur Konten mit Buchungen ${store.year}</label></div>
    <section class="panel" style="padding:6px 12px">${rows === false ? failed() : !rows ? html`<p class="muted" style="padding:12px">Konten werden geladen …</p>`
      : shown.length ? table(['Konto', 'Bezeichnung', 'Art', 'Haushaltstitel', { label: `Soll ${store.year}`, className: 'num' }, { label: `Haben ${store.year}`, className: 'num' },
        { label: 'Saldo', className: 'num' }], body, { className: 'konten-table' })
        : empty('Kein Konto gefunden', k.onlyUsed ? `Im Jahr ${store.year} gibt es dazu keine Buchungen – Haken „Nur Konten mit Buchungen“ entfernen.` : 'Suche ändern.')}</section>
    <p class="small muted">Einnahmen- und Ausgabenkonten nach Haushaltsjahr (wie der Haushalt), Bestandskonten nach Buchungsdatum mit Anfangsbestand
      (wie der Kontoauszug). Saldo = Soll − Haben (S = Soll, H = Haben). Konto anklicken für das Kontenblatt.</p>
  </div>`);
  const input = main.querySelector('#konten-search');
  input.oninput = () => {
    k.search = input.value;
    const pos = input.selectionStart;
    list(main);
    const again = main.querySelector('#konten-search');
    again.focus();
    again.setSelectionRange(pos, pos);
  };
  main.querySelector('#konten-used').onchange = (e) => { k.onlyUsed = e.target.checked; list(main); };
  bindRetry(main);
  main.querySelectorAll('[data-konto]').forEach((row) => {
    const open = () => { k.selected = row.dataset.konto; k.kv = ''; render(); window.scrollTo(0, 0); };
    row.onclick = open;
    row.onkeydown = (e) => { if (e.key === 'Enter') open(); };
  });
}

function sheet(main) {
  const k = store.konten;
  const query = `code=${encodeURIComponent(k.selected)}&year=${store.year}${k.kv ? `&kv=${encodeURIComponent(k.kv)}` : ''}`;
  const d = load(`/api/konto?${query}`, 'sheet');
  mount(main, html`<div class="page wide">
    <div><button type="button" class="link back-link" data-back>← Alle Konten</button></div>
    ${d === false ? failed() : !d ? html`<p class="muted">Kontenblatt wird geladen …</p>` : html`
    <header class="pagehead"><div><p class="eyebrow">${d.class_label} · ${d.group}</p><h1>${d.code} · ${d.name}</h1>
      <p class="konto-head meta">${d.kind_label} · ${store.year}${d.kv ? ` · Bereich ${kvName(d.kv)}` : ''}${d.note ? ` · ${d.note}` : ''}</p></div>
      <div class="actions konto-actions"><button type="button" class="btn" data-csv>CSV</button>
        <button type="button" class="btn" data-print>${icon('print', 16)} Drucken</button></div></header>
    <div class="toolbar"><label class="field inline">Bereich<select id="konto-kv"><option value="">alle</option>${store.data.kv.map((x) => html`<option value="${x.code}" ${x.code === k.kv ? 'selected' : ''}>${x.name}</option>`)}</select></label></div>
    <section class="cards" aria-label="Summen">
      ${d.by_date ? html`<div class="card"><span class="label">Anfangsbestand 01.01.${store.year}</span><span class="value">${saldo(d.opening)}</span></div>` : ''}
      <div class="card"><span class="label">Soll ${store.year}</span><span class="value">${eur(d.debit)}</span></div>
      <div class="card"><span class="label">Haben ${store.year}</span><span class="value">${eur(d.credit)}</span></div>
      <div class="card"><span class="label">${d.by_date ? 'Endbestand' : 'Saldo'}</span><span class="value">${saldo(d.closing)}</span><span class="note">${d.rows.length} Buchungen</span></div>
    </section>
    <section class="panel" style="padding:6px 12px">${d.rows.length ? table(['Datum', 'Buchung', 'Text', 'Art', 'Bereich', 'Titel', { label: 'Soll', className: 'num' },
      { label: 'Haben', className: 'num' }, { label: 'Saldo', className: 'num' }], d.rows.map((r) => html`<tr class="${r.reversed || r.reversal_of ? 'muted' : ''}">
        <td class="nowrap">${date(r.day)}${!d.by_date && r.year !== Number(r.day.slice(0, 4)) ? html`<span class="sub">Haushaltsjahr ${r.year}</span>` : ''}</td>
        <td class="muted">#${r.id}</td><td>${r.description}${r.reversed ? html` ${badge('storniert')}` : ''}${r.reversal_of ? html` ${badge('Storno')}` : ''}</td>
        <td class="small">${r.kind}</td><td>${kvName(r.kv)}</td><td class="small">${r.title ? titleLabel(r.title) || r.title : ''}</td>
        <td class="num">${r.debit ? eur(r.debit) : ''}</td><td class="num">${r.credit ? eur(r.credit) : ''}</td><td class="num">${saldo(r.balance)}</td></tr>`))
      : empty('Keine Buchungen', `Auf diesem Konto gibt es ${store.year}${d.kv ? ' für diesen Bereich' : ''} keine Buchungen.`)}</section>
    <p class="small muted">${d.by_date ? 'Bestandskonto: Buchungen nach Buchungsdatum, Anfangsbestand aus allen früheren Buchungen.' : 'Einnahmen-/Ausgabenkonto: Buchungen des Haushaltsjahres (bei Excel-Übernahme laut Blatt).'}
      Stornierte Buchungen und ihre Stornos bleiben sichtbar und heben sich auf.</p>`}
  </div>`);
  main.querySelector('[data-back]').onclick = () => { k.selected = null; render(); };
  bindRetry(main);
  main.querySelector('[data-print]')?.addEventListener('click', () => window.print());
  main.querySelector('[data-csv]')?.addEventListener('click', () => safe(() => download(`/api/konto.csv?${query}`, `Konto-${k.selected}-${store.year}${k.kv ? '-' + k.kv : ''}.csv`)));
  main.querySelector('#konto-kv')?.addEventListener('change', (e) => { k.kv = e.target.value; render(); });
}

export function konten(main) {
  if (store.konten.selected) sheet(main);
  else list(main);
}
