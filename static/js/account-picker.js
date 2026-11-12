// Kontenauswahl als Popup: zu jedem Kontofeld eine durchsuchbare Liste (Nummer, Bezeichnung, Haushaltstitel), gegliedert in
// Haushalt, weitere Konten des Kontenrahmens und Bestandskonten (0.22: alle Konten des CDU-Kontenrahmens).
// Angeboten werden genau die Konten, die das Feld erlaubt (Optionen des Auswahlfelds, data-codes beim Haushaltsposten
// bzw. data-kinds bei Eingabefeldern).
//
// Haushaltsposten (Entscheidung Lennert Gust 29.10.2026): beim Verbuchen und bei Eingangsrechnungen nur noch EIN Feld.
// Der Knopf öffnet die Liste; der Haushaltstitel ergibt sich aus dem Konto (bei einem Kreisverband zählt die KV-Zeile).
// Eine Titelauswahl erscheint nur, wenn das Konto keinen eindeutigen Titel hat oder „anderer Titel“ gewählt wird.
import { html, mount } from './html.js';
import { store, accountName, kvName } from './store.js';
import { overlay, icon } from './ui.js';

const option = (value, label, selected) => html`<option value="${value}" ${selected ? 'selected' : ''}>${label}</option>`;
const titlesOf = (kind) => (store.household?.titles || []).filter((t) => t.kind === kind);
const isKvRow = (kv) => titlesOf('expense').some((t) => t.code === kv);

/** Kontofeld mit Listen-Knopf. field: html`<select …>` oder html`<input …>`. */
export const accountField = (field, label = 'Kontenliste öffnen') => html`<div class="account-field">${field}
  <button type="button" class="btn ghost icon-btn" data-account-picker aria-label="${label}" title="Kontenliste">${icon('list', 16)}</button></div>`;

/** Alle Listen-Knöpfe innerhalb von root aktivieren (nach jedem Neuaufbau aufrufen). */
export function bindAccountPickers(root) {
  root.querySelectorAll('[data-account-picker]').forEach((b) => {
    b.onclick = () => openPicker(b.closest('.account-field').querySelector('select, input'));
  });
}

/** Haushaltstitel, der sich aus Konto und Bereich ergibt – wie haushalt.default_title auf dem Server. */
export function titleFor(account, kv, kind) {
  const own = accountOf(account)?.kind;   // z. B. 8100 bei einer Rückzahlung (Ausgang): Titel des Einnahmenkontos
  const titles = titlesOf(['income', 'expense'].includes(own) ? own : kind);
  if (account === 'KV') return titles.find((t) => t.code === kv)?.id || '';
  const matching = titles.filter((t) => t.code && t.code === account);
  return matching.length === 1 ? matching[0].id : '';
}

const accountOf = (code) => (store.data?.accounts || []).find((a) => a.code === code);
/** Bestandskonto (Aktiv/Passiv): zählt nicht im Haushalt, braucht keinen Haushaltstitel. */
export const isBalance = (code) => ['asset', 'liability'].includes(accountOf(code)?.kind);

/** Konten für die Kontierung: alle Einnahmen- bzw. Ausgabenkonten des Kontenrahmens (Ausgaben mit Sammelkonto KV);
 *  balance=true zusätzlich die Bestandskonten, die direkt aus dem Kontoauszug gebucht werden dürfen (z. B. Kasse, Darlehen). */
export function postenCodes(kind, { exclude = [], balance = false } = {}) {
  return (store.data?.accounts || []).filter((a) => !exclude.includes(a.code) && (a.kind === kind || (balance && a.bank))).map((a) => a.code);
}

/** Abschnitt in der Kontenliste: Haushalt, weitere Konten des Kontenrahmens, Bestandskonten. */
function sectionOf(a) {
  if (a.household) return 'Haushalt';
  if (['asset', 'liability'].includes(a.kind)) return 'Bestandskonten – zählen nicht im Haushalt';
  return 'Weitere Konten des Kontenrahmens – Haushaltstitel wählen';
}
const SECTION_ORDER = ['Haushalt', 'Weitere Konten des Kontenrahmens – Haushaltstitel wählen', 'Bestandskonten – zählen nicht im Haushalt'];

const postenLabel = (code) => (code ? `${code} · ${accountName(code)}` : 'Konto wählen');

/** Ein Feld für den Haushaltsposten. key=true: zusätzlich data-key (Formulare, die [data-key] einsammeln). */
export function postenField({ kind, account = '', title = '', codes, key = false, label = 'Haushaltsposten' }) {
  return html`<div class="posten" data-posten data-kind="${kind}">
    <button type="button" class="account-button ${account ? '' : 'unset'}" data-account ${key ? html`data-key="account"` : ''} value="${account}"
      data-codes="${codes.join(',')}" aria-haspopup="dialog" aria-label="${label}: ${postenLabel(account)}"><span data-account-label>${postenLabel(account)}</span>${icon('chevron', 16)}</button>
    <p class="posten-title" data-title-hint></p>
    <select data-title ${key ? html`data-key="title"` : ''} data-chosen="${title ? '1' : '0'}" aria-label="Haushaltstitel" hidden>${option('', 'Haushaltstitel wählen', !title)}${titlesOf(kind)
      .map((t) => option(t.id, `${t.code ? t.code + ' · ' : ''}${t.label}`, t.id === title))}</select>
  </div>`;
}

/** Haushaltsposten-Felder in root aktivieren. kv(): aktueller Bereich; onChange: nach jeder Änderung. */
export function bindPosten(root, { kv, onChange = () => {} }) {
  root.querySelectorAll('[data-posten]').forEach((box) => {
    const button = box.querySelector('[data-account]'), select = box.querySelector('[data-title]'), hint = box.querySelector('[data-title-hint]');
    const kind = box.dataset.kind;
    const refresh = () => {
      const account = button.value, area = kv(), derived = titleFor(account, area, kind);
      if (derived && ![...select.options].some((o) => o.value === derived)) {   // Titel der anderen Seite (Rückzahlung auf 8100)
        const t = (store.household?.titles || []).find((x) => x.id === derived);
        select.append(new Option(`${t?.code ? t.code + ' · ' : ''}${t?.label || derived}`, derived));
      }
      if (select.dataset.chosen === '1' && (select.value === derived || account === 'KV')) select.dataset.chosen = '0';   // Standard bzw. KV-Zeile
      const chosen = select.dataset.chosen === '1' && select.value;
      button.classList.toggle('unset', !account);
      mount(button.querySelector('[data-account-label]'), postenLabel(account));
      button.setAttribute('aria-label', `Konto: ${postenLabel(account)}`);
      const balance = isBalance(account);
      if (balance) { select.value = ''; select.dataset.chosen = '0'; }
      else if (!chosen) select.value = derived;
      select.hidden = balance || !(account && (chosen || !derived)) || (account === 'KV' && !derived);
      const kvRow = kind === 'expense' && isKvRow(area);
      const current = (store.household?.titles || []).find((t) => t.id === select.value);
      let text = '';
      if (balance) text = 'Bestandskonto – zählt nicht im Haushalt, kein Haushaltstitel.';
      else if (account === 'KV' && !derived) text = html`<span class="over">Sammelkonto KV nur mit einem Kreisverband als Bereich.</span>`;
      else if (kvRow && account) text = html`Haushalt: Zeile ${kvName(area)}`;
      else if (current && select.hidden) text = current.label === accountName(account)   // gleicher Name wie das Konto: nicht doppelt anzeigen
        ? html`<button type="button" class="link" data-title-other>anderer Haushaltstitel</button>`
        : html`Haushaltstitel: ${current.label} <button type="button" class="link" data-title-other>ändern</button>`;
      else if (account && !derived && !chosen) text = 'Für dieses Konto bitte den Haushaltstitel wählen.';
      mount(hint, text);
      hint.querySelector('[data-title-other]')?.addEventListener('click', () => { select.dataset.chosen = '1'; select.hidden = false; select.focus(); });
    };
    button.onclick = () => openPicker(button);
    button.onchange = () => { select.dataset.chosen = '0'; refresh(); onChange(); };   // neues Konto: Titel neu ableiten
    select.onchange = () => { select.dataset.chosen = select.value ? '1' : '0'; refresh(); onChange(); };
    box.refresh = refresh;
    refresh();
  });
}

/** Haushaltsposten von außen setzen (z. B. Vorschlag, E-Rechnung). */
export function setPosten(box, account, title = '') {
  box.querySelector('[data-account]').value = account || '';
  const select = box.querySelector('[data-title]');
  select.dataset.chosen = title ? '1' : '0';
  select.value = title || '';
  box.refresh?.();
}

/** Vollständig, wenn ein Konto gewählt ist und sich ein Haushaltstitel ergibt bzw. gewählt wurde (Bestandskonten ohne Titel). */
export const postenComplete = (box) => {
  const account = box.querySelector('[data-account]').value;
  return Boolean(account && (isBalance(account) || box.querySelector('[data-title]').value));
};

function rowsFor(field) {
  const codes = field.dataset.codes !== undefined
    ? field.dataset.codes.split(',').filter(Boolean)
    : field.tagName === 'SELECT'
      ? [...field.options].map((o) => o.value).filter(Boolean)
      : store.data.accounts.filter((a) => (field.dataset.kinds || '').split(',').includes(a.kind)).map((a) => a.code);
  const rows = codes.map((code) => {
    const account = accountOf(code) || { code, name: '', kind: '' };
    const title = store.household?.titles.find((t) => t.code === code);
    const note = code === 'KV' ? 'Kreisverbandsausgaben ohne eigenes Sachkonto' : title && title.label !== account.name ? `Haushaltstitel: ${title.label}` : account.note || '';
    return { code, name: account.name, title: note, section: field.dataset.codes !== undefined ? sectionOf(account) : '' };
  });
  return rows.sort((a, b) => SECTION_ORDER.indexOf(a.section) - SECTION_ORDER.indexOf(b.section) || (a.code === 'KV') - (b.code === 'KV') || a.code.localeCompare(b.code));
}

export function openPicker(field) {
  const rows = rowsFor(field);
  let query = '';
  let active = Math.max(0, rows.findIndex((r) => r.code === field.value));
  const el = overlay('Konto auswählen', html`
    <label class="search">${icon('search', 16)}<span class="sr-only">Konto suchen</span><input id="account-query" type="search" placeholder="Nummer oder Bezeichnung" autocomplete="off"></label>
    <div id="account-list" class="account-list" role="listbox" aria-label="Konten"></div>
    <p class="small muted">↑/↓ wählen · Enter übernehmen · Esc schließen</p>`, { wide: false });
  const list = el.querySelector('#account-list');
  const input = el.querySelector('#account-query');
  const shown = () => rows.filter((r) => !query || `${r.code} ${r.name} ${r.title}`.toLowerCase().includes(query));
  const choose = (code) => {
    el.close();                       // erst schließen: die Änderung kann eine neue Liste öffnen (8100 → Mitgliedersuche)
    field.value = code;
    field.dispatchEvent(new Event('change', { bubbles: true }));
    if (!el.open) field.focus();
  };
  const draw = () => {
    const visible = shown();
    active = Math.min(active, Math.max(0, visible.length - 1));
    mount(list, visible.length ? html`${visible.map((r, i) => html`${r.section && r.section !== visible[i - 1]?.section ? html`<p class="account-section" role="presentation">${r.section}</p>` : ''}
        <button type="button" role="option" aria-selected="${i === active}"
        class="account-option ${i === active ? 'active' : ''} ${r.code === field.value ? 'current' : ''}" data-code="${r.code}">
        <span class="mono">${r.code}</span><span>${r.name}${r.title ? html`<small>${r.title}</small>` : ''}</span></button>`)}`
      : html`<p class="muted" style="padding:12px">Kein passendes Konto.</p>`);
    list.querySelectorAll('[data-code]').forEach((b) => (b.onclick = () => choose(b.dataset.code)));
    list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
  };
  input.oninput = () => { query = input.value.toLowerCase().trim(); active = 0; draw(); };
  input.onkeydown = (e) => {
    const visible = shown();
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, visible.length - 1); draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (visible[active]) choose(visible[active].code); }
  };
  draw();
  input.focus();
}
