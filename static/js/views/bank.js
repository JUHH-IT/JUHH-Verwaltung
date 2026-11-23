// Buchungsplatz (Richtung B): Arbeitsliste links, ausgewählter Umsatz mit Kontierung rechts – ab 0.25 für die Kasse (cash,
// views/cash.js): dieselbe Kontierung, nur das Gegenkonto ist 1400 statt 1420. Der Kontoauszug steht ab 0.58 auf eigenen Seiten
// (views/umsaetze.js Liste, views/verbuchen.js Maske) und nutzt von hier Parken, Ungültig und den Hinweis auf ein Storno.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { eur, signed, date, shortDate, findIban, bookingKind } from '../format.js';
import { store, refresh, ledgerRows, eligibleInvoices, kvName, accountName, titleLabel } from '../store.js';
import { pageHead, icon, empty, badge, confirmAction, dialog, safe, toast } from '../ui.js';
import { bookingEditor } from './booking.js';
import { importDialog, masterDialog, learningDialog } from './imports.js';
import { reconcileDialog } from './reconcile.js';

const FILTERS = [
  ['open', 'Offen'],
  ['parked', 'Parkkonto 1370'],
  ['booked', 'Verbucht'],
  ['all', 'Alle'],
];
const STATE_LABEL = { open: ['Offen', 'warn'], parked: ['Geparkt', 'warn'], booked: ['Verbucht', 'ok'], reversed: ['Storniert', ''], void: ['Ungültig', ''] };
const OWN = { bank: '1420', cash: '1400' };   // Gegenkonto des Umsatzes (nicht als Kontierung anzeigen)

/** Arbeitsbereich: Kontoauszug oder Kasse (setzt die jeweilige Seite vor dem Zeichnen). */
let L = { ledger: 'bank', state: store.bank, head: null, empty: '' };
export function useLedger(ctx) { L = ctx; }
/** Journal-Quellen eines Umsatzes in einer Zuordnungsrunde (wie core.bank_source: bank:7, nach Storno bank:7/1). */
export const roundSources = (b, round = b.reversals || 0) => new Set(['bank', 'resolve'].map((k) => `${k}:${b.id}${round ? '/' + round : ''}`));

let editor = null;

function rowsFor(filter) {
  if (filter === 'parked') return store.workflow.parked.filter((b) => (b.ledger || 'bank') === L.ledger);
  const rows = ledgerRows(L.ledger).filter((b) => filter === 'all' || b.state === filter);
  return filter === 'open' ? rows.sort((a, b) => a.booked.localeCompare(b.booked) || a.id - b.id) : rows;
}

function visibleRows() {
  const q = L.state.search.trim().toLowerCase();
  const rows = rowsFor(L.state.filter);
  if (!q) return rows;
  return rows.filter((b) => `${b.name} ${b.reference} ${eur(b.amount)} ${date(b.booked)}`.toLowerCase().includes(q));
}

function counts() {
  const year = ledgerRows(L.ledger);
  return {
    open: year.filter((b) => b.state === 'open').length,
    parked: store.workflow.parked.filter((b) => (b.ledger || 'bank') === L.ledger).length,
    booked: year.filter((b) => b.state === 'booked').length,
    all: year.length,
  };
}

function listItem(b, current) {
  const invoice = b.state === 'open' && eligibleInvoices(b).some((i) => `${b.reference} ${b.name}`.includes(i.number));
  const tag = L.state.filter === 'all' || L.state.filter === 'booked' ? STATE_LABEL[b.state] : invoice ? ['Rechnung erkannt', '']
    : b.reason ? [b.reason, 'warn'] : b.state === 'open' && b.reversals ? ['Storniert – neu zuordnen', 'warn'] : null;
  return html`<button type="button" class="bank-item" data-id="${b.id}" ${current ? html`aria-current="true"` : ''}>
    <span class="row1"><span class="name">${b.name || bookingKind(b.reference) || 'Ohne Namen'}</span><span class="amount ${b.amount > 0 ? 'income' : ''}">${signed(b.amount)}</span></span>
    <span class="row2"><span>${shortDate(b.booked)}${bookingKind(b.reference) ? ' · ' + bookingKind(b.reference) : ''}</span>${tag ? html`<span class="tag ${tag[1]}">${tag[0]}</span>` : ''}</span>
  </button>`;
}

/** Gebuchte Zeilen eines Bankumsatzes aus dem Journal (ohne Bank- und Parkkonto); round: Zuordnungsrunde. */
function bookedLines(b, round) {
  const sources = roundSources(b, round);
  return store.data.journal.filter((j) => sources.has(j.source) && ![OWN[b.ledger || 'bank'], '1370'].includes(j.account));
}

const lineList = (lines) => html`<div class="booked-lines">${lines.map((j) => html`<div>
  <strong style="font-weight:600">${j.account}</strong> ${accountName(j.account)} · ${kvName(j.kv)}${j.title ? ` · ${titleLabel(j.title)}` : ''}
  <span class="muted"> · ${eur(j.credit - j.debit)}</span></div>`)}</div>`;

/** Hinweis auf eine stornierte frühere Zuordnung mit deren Kontierung (zum Vergleich beim Neuzuordnen). */
export function reversedNote(b) {
  if (!b.reversals || b.state === 'booked') return '';
  const before = bookedLines(b, b.reversals - 1);
  return html`<div class="notice"><strong>Frühere Zuordnung storniert${b.reversals > 1 ? ` (${b.reversals}×)` : ''}.</strong>
    Sie wurde gegengebucht; der Umsatz ist wieder offen und wird jetzt neu zugeordnet.
    ${before.length ? html`<p class="small muted" style="margin:6px 0 2px">Zuletzt gebucht auf:</p>${lineList(before)}` : ''}</div>`;
}

function detail(b) {
  const iban = findIban(b.reference);
  const kind = bookingKind(b.reference);
  const [label, tone] = STATE_LABEL[b.state] || ['', ''];
  return html`<div class="detail-card">
    <div class="detail-head">
      <div><p class="small muted">${kind ? kind + ' · ' : ''}${date(b.booked)} · ${badge(label, tone)}</p>
        <h2 style="font-size:21px;margin-top:4px">${b.name || 'Ohne Zahlungspartner'}</h2></div>
      <p class="amount ${b.amount > 0 ? 'income' : ''}">${signed(b.amount)}</p>
    </div>
    <dl class="facts">
      ${iban ? html`<div><dt>IBAN</dt><dd class="mono">${iban}</dd></div>` : ''}
      <div><dt>Quelle</dt><dd>${b.source || '—'}${b.row_no ? ` · Zeile ${b.row_no}` : ''}</dd></div>
      ${b.ledger === 'cash' ? html`<div><dt>Kassenbeleg</dt><dd>${b.bank_ref}${b.attachment ? html` · <a href="/api/kasse-datei?id=${b.id}" target="_blank" rel="noopener">Beleg ansehen</a>` : html` · <span class="muted">ohne Belegdatei</span>`}</dd></div>`
        : html`<div><dt>Bankumsatz</dt><dd>#${b.id}</dd></div>`}
    </dl>
    <p class="reference">${b.reference || '—'}</p>
    ${b.state === 'parked' ? html`<p class="notice warn"><strong>Geparkt auf 1370:</strong> ${b.reason || 'ohne Klärungsgrund'}. Beim Verbuchen wird das Parkkonto aufgelöst; ${b.ledger === 'cash' ? 'die Kasse' : 'die Bank'} wird nicht erneut bebucht.</p>` : ''}
    ${b.state === 'void' ? html`<p class="notice">Ungültig: ${b.note || ''}. ${b.ledger === 'cash' ? 'Der Beleg zählt nicht im Kassenbestand.' : 'Der Umsatz zählt nicht mehr – nicht als offen, nicht im Monatsbericht, nicht im Abgleich.'}</p>` : ''}
    ${reversedNote(b)}
    ${['open', 'parked'].includes(b.state) ? html`<div id="editor"></div><div id="park-box" hidden></div>` : ''}
    ${b.ledger === 'cash' && b.state === 'open' ? html`<div class="detail-actions"><p class="small muted">Falsch erfasst (Tippfehler, doppelt)? Der Beleg bleibt sichtbar, zählt aber nicht mehr.</p>
      <button type="button" class="btn ghost" data-void>Beleg ungültig</button></div>` : ''}
    ${b.ledger !== 'cash' && b.state === 'open' ? html`<div class="detail-actions"><p class="small muted">Doppelt importiert? Der Umsatz bleibt mit Grund sichtbar, zählt aber nicht mehr. Gelöscht wird nichts.</p>
      <button type="button" class="btn ghost" data-void>Umsatz ungültig</button></div>` : ''}
    ${b.ledger !== 'cash' && b.state === 'void' ? html`<div class="detail-actions"><p class="small muted">Irrtum? Danach ist der Umsatz wieder offen.</p>
      <button type="button" class="btn ghost" data-unvoid>Wieder gültig</button></div>` : ''}
    ${b.state === 'booked' ? html`<div><h3 style="margin-bottom:8px">Gebucht auf</h3>${lineList(bookedLines(b))}</div>
      <div class="detail-actions"><p class="small muted">Ein Storno bucht gegen; danach ist der Umsatz wieder offen und kann neu zugeordnet werden.</p>
        <button type="button" class="btn danger" data-reverse>Zuordnung stornieren</button></div>` : ''}
    ${b.state === 'reversed' ? html`<p class="notice">Die Zuordnung wurde storniert.</p>` : ''}
  </div>`;
}

function select(id, { focus = false } = {}) {
  L.state.selected = id;
  const main = document.getElementById('main');
  if (main) renderBank(main, { keepScroll: true });
  if (focus) document.querySelector(`.bank-item[data-id="${id}"]`)?.focus();
}

function move(step) {
  const rows = visibleRows();
  if (!rows.length) return;
  const index = Math.max(0, rows.findIndex((b) => b.id === L.state.selected));
  const next = rows[Math.min(rows.length - 1, Math.max(0, index + step))];
  select(next.id, { focus: true });
  document.querySelector(`.bank-item[data-id="${next.id}"]`)?.scrollIntoView({ block: 'nearest' });
}

export function parkForm(b, box, done = afterChange) {
  box.hidden = false;
  mount(box, html`<form class="park" id="park-form">
    <label class="field">Was muss geklärt werden?<input name="reason" required placeholder="z. B. Beleg fehlt, Kreisverband unklar"></label>
    <div class="buttons"><button type="button" class="btn ghost" data-park-cancel>Abbrechen</button>
      <button type="submit" class="btn warn">Auf 1370 parken</button></div></form>`);
  box.querySelector('input').focus();
  const cancel = () => { box.hidden = true; mount(box, ''); };
  box.querySelector('[data-park-cancel]').onclick = cancel;
  box.querySelector('input').addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel(); } });
  box.querySelector('#park-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/park', { id: b.id, reason: e.target.reason.value });
      await done(b);
      toast('Auf Parkkonto 1370 gebucht – bleibt als Klärungsfall sichtbar.');
    });
  };
}

/** Nach dem Verbuchen oder Parken den nächsten Umsatz der Liste wählen. */
async function afterChange(b) {
  const rows = visibleRows();
  const index = rows.findIndex((x) => x.id === b.id);
  const next = rows[index + 1] || rows[index - 1];
  L.state.selected = next ? next.id : null;
  await refresh();
}

function renderBank(main, { keepScroll = false } = {}) {
  const listScroll = keepScroll ? main.querySelector('.worklist .list')?.scrollTop : 0;
  const c = counts();
  const rows = visibleRows();
  if (!rows.some((b) => b.id === L.state.selected)) L.state.selected = rows[0]?.id ?? null;
  const current = rows.find((b) => b.id === L.state.selected);
  const allBooked = L.state.filter === 'open' && !c.open;

  const filters = L.extraFilters ? [...FILTERS, ...L.extraFilters] : FILTERS;
  mount(main, html`<div class="page wide">
    ${L.head ? L.head() : pageHead(`Haushaltsjahr ${store.year}`, 'Buchungsplatz', html`
      <button type="button" class="btn" data-reconcile>Abgleich</button>
      <button type="button" class="btn" data-learning>Lernbeispiele (${store.workflow.examples.length})</button>
      <button type="button" class="btn" data-master>Excel übernehmen</button>
      <button type="button" class="btn primary" data-import>${icon('upload', 16)} Kontoauszug importieren</button>`)}
    <div class="toolbar">
      <div class="tabs" role="tablist" aria-label="Umsätze filtern">${filters.map(([id, label]) => html`
        <button type="button" role="tab" data-filter="${id}" aria-selected="${L.state.filter === id}">${label}${id in c ? html`<span class="n">${c[id]}</span>` : ''}</button>`)}</div>
      <label class="search">${icon('search', 16)}<span class="sr-only">Umsätze durchsuchen</span>
        <input id="bank-search" type="search" placeholder="Name, Zweck, Betrag (Taste /)" value="${L.state.search}"></label>
    </div>
    ${L.state.filter === 'parked' ? html`<p class="small muted">Geparkte Umsätze aus allen Jahren, älteste zuerst. Parken ist eine vorläufige Buchung auf 1370 und ändert noch keine Einnahmen oder Ausgaben.</p>` : ''}
    ${L.custom && L.custom[L.state.filter] ? html`<div data-custom></div>` : rows.length ? html`<div class="worklist">
        <div class="list" role="list" aria-label="Umsätze">${rows.map((b) => listItem(b, b.id === L.state.selected))}</div>
        <section class="detail" aria-label="Ausgewählter Umsatz">${current ? detail(current) : ''}</section>
      </div>`
      : html`<section class="panel">${allBooked
        ? empty('Alles verbucht', L.empty || `Für ${store.year} sind keine offenen Bankumsätze vorhanden. Neue Umsätze kommen über „Kontoauszug importieren“.`)
        : empty('Keine Umsätze in dieser Ansicht', L.state.search ? 'Suchbegriff anpassen.' : 'Andere Ansicht oder anderes Haushaltsjahr wählen.')}</section>`}
  </div>`);

  main.querySelector('[data-import]')?.addEventListener('click', importDialog);
  main.querySelector('[data-master]')?.addEventListener('click', masterDialog);
  main.querySelector('[data-learning]')?.addEventListener('click', learningDialog);
  main.querySelector('[data-reconcile]')?.addEventListener('click', () => safe(reconcileDialog));
  if (L.bind) L.bind(main);
  if (L.custom && L.custom[L.state.filter]) L.custom[L.state.filter](main.querySelector('[data-custom]'));
  main.querySelectorAll('[data-filter]').forEach((t) => (t.onclick = () => {
    L.state.filter = t.dataset.filter;
    L.state.selected = null;
    renderBank(main);
  }));
  const search = main.querySelector('#bank-search');
  search.oninput = () => {
    L.state.search = search.value;
    const pos = search.selectionStart;
    renderBank(main);
    const again = main.querySelector('#bank-search');
    again.focus();
    again.setSelectionRange(pos, pos);
  };
  main.querySelectorAll('.bank-item').forEach((item) => (item.onclick = () => {
    select(Number(item.dataset.id));
    // Handy/Tablet: Liste und Kontierung stehen untereinander – nach der Auswahl zur Kontierung springen
    if (window.matchMedia('(max-width: 900px)').matches) document.querySelector('.worklist .detail')?.scrollIntoView({ block: 'start' });
  }));
  const list = main.querySelector('.worklist .list');
  if (list && keepScroll) list.scrollTop = listScroll;

  editor = null;
  if (current && ['open', 'parked'].includes(current.state) && main.querySelector('#editor')) {
    editor = bookingEditor(main.querySelector('#editor'), current, {
      onBooked: () => afterChange(current),
      onPark: () => parkForm(current, main.querySelector('#park-box')),
    });
  }
  main.querySelector('[data-void]')?.addEventListener('click', () => L.voidDialog?.(current));
  main.querySelector('[data-unvoid]')?.addEventListener('click', () => confirmAction('Umsatz wieder gültig',
    'Der Umsatz ist danach wieder offen und kann verbucht werden.', async () => { await api('/api/umsatz-gueltig', { id: current.id }); L.state.filter = 'open'; L.state.selected = current.id; },
    { label: 'Wieder gültig', done: 'Umsatz ist wieder offen.' }));
  main.querySelector('[data-reverse]')?.addEventListener('click', () => confirmAction('Zuordnung stornieren',
    'Die Buchung wird gegengebucht, eine zugeordnete Rechnung wieder geöffnet. Eine zugeordnete Eingangsrechnung wird als „Zahlung storniert – prüfen“ gesperrt, bis die Abbuchung ihr wieder zugeordnet ist – so wird sie nicht ein zweites Mal überwiesen. Der Umsatz ist danach wieder offen und kann sofort neu zugeordnet werden.',
    async () => {
      await api('/api/reverse-bank', { id: current.id });
      if (L.state.filter !== 'all') L.state.filter = 'open';  // gleich weiter zum Neuzuordnen
      L.state.selected = current.id;
    }, { label: 'Stornieren', done: 'Zuordnung storniert – Umsatz ist wieder offen.' }));
}

/** Kontoumsatz für ungültig erklären (0.55, z. B. doppelt importiert) – workflow.ungueltig; Kassenbelege: views/cash.js. */
export function voidBankDialog(b) {
  const el = dialog('Umsatz ungültig', html`<form id="bank-void" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">${date(b.booked)}, ${signed(b.amount)}, ${b.name || 'ohne Zahlungspartner'}. Der Umsatz bleibt unter „Alle“ mit dem Grund sichtbar und steht im Protokoll,
      zählt aber nicht mehr. Mit „Wieder gültig“ lässt sich das zurücknehmen.</p>
    <label class="field">Grund<input name="reason" required maxlength="200" autocomplete="off" value="doppelt importiert"></label>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn danger">Ungültig</button></div></form>`);
  el.querySelector('form [data-close]').onclick = () => el.close();
  el.querySelector('#bank-void').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/umsatz-ungueltig', { id: b.id, reason: e.target.reason.value });
      el.close();
      store.bank.selected = null;
      await refresh();
      toast('Umsatz ist ungültig und zählt nicht mehr.');
    });
  };
}

/** Arbeitsbereich mit eigener Kopfzeile, z. B. die Kasse (views/cash.js). */
export function renderLedger(main, ctx) {
  useLedger(ctx);
  renderBank(main);
}

/** Tastenkürzel (aus main.js, nur außerhalb von Eingabefeldern). */
export function bankKeys(e) {
  const key = e.key.toLowerCase();
  if (key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); move(1); }
  else if (key === 'k' || e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
  else if (e.key === '/') { e.preventDefault(); document.getElementById('bank-search')?.focus(); }
  else if (key === 'p') { const park = document.getElementById('park'); if (park) { e.preventDefault(); park.click(); } }
  else if (e.key === 'Enter' && editor) {
    const item = e.target.closest('.bank-item');
    // Enter auf einem anderen Umsatz in der Liste wählt ihn aus – gebucht wird nur der gerade angezeigte
    if (item && item.getAttribute('aria-current') !== 'true') { e.preventDefault(); item.click(); return; }
    if (!e.target.closest('button') || item) { e.preventDefault(); editor.submit(); }
  }
}
