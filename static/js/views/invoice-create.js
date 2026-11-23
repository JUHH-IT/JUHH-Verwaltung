// Rechnung erstellen – für einen oder viele Empfänger (z. B. Teilnahmebeiträge einer Veranstaltung).
// Schritt 1: Positionen und Empfänger, Schritt 2: Vorschau (PDF/E-Mail je Empfänger), dann Festschreiben.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date, parseCents } from '../format.js';
import { store, refresh, kvName } from '../store.js';
import { dialog, table, badge, safe, icon, mailPreview } from '../ui.js';
import { afterIssue, erechnungHinweis } from './mailing.js';
import { accountField, bindAccountPickers } from '../account-picker.js';

let state = null;

function fresh() {
  const accounts = store.data.invoice_accounts;
  return {
    lines: [{ description: '', amount: '', account: accounts.find((a) => a.code === '8500')?.code || accounts[0]?.code }],
    due: '', kv: 'LV', members: new Set(), others: [], tab: 'members', search: '', kvFilter: '', mailOnly: false,
  };
}

const payload = () => ({
  lines: state.lines, due: state.due, kv: state.kv, member_ids: [...state.members],
  recipients: state.others.filter((r) => r.name || r.address || r.email),
});

function lineRow(l, i) {
  return html`<div class="line-row">
    <input data-line="${i}" data-key="description" value="${l.description}" placeholder="Leistung, z. B. Teilnahme Sommerfest 01.08.2026" aria-label="Beschreibung Position ${i + 1}">
    <input data-line="${i}" data-key="amount" value="${l.amount}" inputmode="decimal" placeholder="Betrag €" aria-label="Betrag Position ${i + 1}">
    ${accountField(html`<select data-line="${i}" data-key="account" aria-label="Einnahmenkonto Position ${i + 1}">${store.data.invoice_accounts.map((a) => html`<option value="${a.code}" ${a.code === l.account ? 'selected' : ''}>${a.code} ${a.name}</option>`)}</select>`)}
    <button type="button" class="btn ghost icon-btn" data-drop-line="${i}" aria-label="Position entfernen" ${state.lines.length === 1 ? 'disabled' : ''}>${icon('trash', 16)}</button></div>`;
}

function memberList() {
  const q = state.search.toLowerCase();
  const list = store.data.members.filter((m) => !m.left_on && (!state.kvFilter || m.kv === state.kvFilter) && (!state.mailOnly || m.email)
    && (!q || `${m.name} ${m.number} ${m.email || ''}`.toLowerCase().includes(q)));
  return { list, markup: html`<div class="checklist">${list.length ? list.map((m) => html`<label>
      <input type="checkbox" data-member="${m.id}" ${state.members.has(m.id) ? 'checked' : ''}>
      <span class="who">${m.name}<small>${m.number} · ${kvName(m.kv)}</small></span>${m.email ? html`<span class="small muted">${m.email}</span>` : badge('keine E-Mail')}</label>`)
    : html`<p class="muted" style="padding:12px">Keine passenden Mitglieder.</p>`}</div>` };
}

function otherRow(r, i) {
  return html`<div class="recipient-row">
    <input data-other="${i}" data-key="name" value="${r.name}" placeholder="Name / Firma" aria-label="Name Empfänger ${i + 1}">
    <input data-other="${i}" data-key="email" value="${r.email}" type="email" placeholder="E-Mail (optional)" aria-label="E-Mail Empfänger ${i + 1}">
    <textarea data-other="${i}" data-key="address" rows="2" placeholder="Straße&#10;PLZ Ort" aria-label="Anschrift Empfänger ${i + 1}">${r.address}</textarea>
    <button type="button" class="btn ghost icon-btn" data-drop-other="${i}" aria-label="Empfänger entfernen">${icon('trash', 16)}</button></div>`;
}

function renderForm() {
  const total = state.lines.reduce((s, l) => s + (parseCents(l.amount) || 0), 0);
  const count = state.members.size + state.others.filter((r) => r.name).length;
  const { markup } = memberList();
  const el = dialog('Rechnung erstellen', html`<form id="series-form" novalidate style="display:flex;flex-direction:column;gap:16px">
    <p class="small muted">Eine Rechnung pro Empfänger mit denselben Positionen – für eine einzelne Rechnung einfach einen Empfänger wählen. Nummern werden beim Festschreiben fortlaufend vergeben.
      <button type="button" class="link" id="reset">Neu beginnen</button></p>
    <h3>Positionen</h3>
    <div style="display:flex;flex-direction:column;gap:8px" id="lines">${state.lines.map(lineRow)}</div>
    <div><button type="button" class="link" id="add-line">${icon('plus', 14)} Position</button></div>
    <div class="formgrid">
      <label class="field">Fällig am<input type="date" name="due" value="${state.due}"><span class="small muted">leer = in ${store.data.settings.normal_terms} Tagen</span></label>
      <label class="field">Bereich (Buchung)<select name="kv">${store.data.kv.map((k) => html`<option value="${k.code}" ${k.code === state.kv ? 'selected' : ''}>${k.name}</option>`)}</select></label>
    </div>
    <h3>Empfänger</h3>
    <div class="tabs" role="tablist" aria-label="Empfänger">
      <button type="button" role="tab" data-rtab="members" aria-selected="${state.tab === 'members'}">Mitglieder<span class="n">${state.members.size}</span></button>
      <button type="button" role="tab" data-rtab="others" aria-selected="${state.tab === 'others'}">Weitere Empfänger<span class="n">${state.others.filter((r) => r.name).length}</span></button></div>
    ${state.tab === 'members' ? html`
      <div class="toolbar"><label class="search">${icon('search', 16)}<span class="sr-only">Mitglieder suchen</span><input id="m-search" type="search" placeholder="Name, Nummer, E-Mail" value="${state.search}"></label>
        <select id="m-kv" aria-label="Kreisverband">${[['', 'Alle Kreisverbände'], ...store.data.kv.filter((k) => k.code !== 'LV').map((k) => [k.code, k.name])].map(([c, n]) => html`<option value="${c}" ${c === state.kvFilter ? 'selected' : ''}>${n}</option>`)}</select>
        <label class="check"><input type="checkbox" id="m-mail" ${state.mailOnly ? 'checked' : ''}> nur mit E-Mail</label>
        <button type="button" class="link" id="m-all">Alle angezeigten wählen</button><button type="button" class="link" id="m-none">Keine</button></div>
      <div id="m-list">${markup}</div>`
    : html`<div style="display:flex;flex-direction:column;gap:8px" id="others">${state.others.map(otherRow)}</div>
      <p class="small muted">Anschrift im Ausland: das Land in die letzte Zeile schreiben.</p>
      <div><button type="button" class="link" id="add-other">${icon('plus', 14)} Empfänger</button></div>`}
    <div class="bulkbar"><strong>${count} Empfänger · je ${eur(total)} · gesamt ${eur(total * count)}</strong>
      <button type="submit" class="btn primary">${icon('eye', 16)} Vorschau</button></div>
  </form>`, { wide: true });

  const form = el.querySelector('#series-form');
  bindAccountPickers(form);
  form.querySelector('#reset').onclick = () => { state = fresh(); renderForm(); };
  form.querySelectorAll('[data-line]').forEach((input) => (input.oninput = input.onchange = () => {
    state.lines[Number(input.dataset.line)][input.dataset.key] = input.value;
    if (input.dataset.key === 'amount') updateBar(el);
  }));
  form.querySelectorAll('[data-drop-line]').forEach((b) => (b.onclick = () => { state.lines.splice(Number(b.dataset.dropLine), 1); renderForm(); }));
  form.querySelector('#add-line').onclick = () => { state.lines.push({ description: '', amount: '', account: state.lines.at(-1).account }); renderForm(); };
  form.due.onchange = () => (state.due = form.due.value);
  form.kv.onchange = () => (state.kv = form.kv.value);
  form.querySelectorAll('[data-rtab]').forEach((b) => (b.onclick = () => { state.tab = b.dataset.rtab; if (state.tab === 'others' && !state.others.length) state.others.push({ name: '', email: '', address: '' }); renderForm(); }));
  if (state.tab === 'members') {
    const bindList = () => form.querySelectorAll('[data-member]').forEach((c) => (c.onchange = () => {
      c.checked ? state.members.add(Number(c.dataset.member)) : state.members.delete(Number(c.dataset.member));
      updateBar(el);
    }));
    const redrawList = () => { mount(form.querySelector('#m-list'), memberList().markup); bindList(); };
    bindList();
    form.querySelector('#m-search').oninput = (e) => { state.search = e.target.value; redrawList(); };
    form.querySelector('#m-kv').onchange = (e) => { state.kvFilter = e.target.value; redrawList(); };
    form.querySelector('#m-mail').onchange = (e) => { state.mailOnly = e.target.checked; redrawList(); };
    form.querySelector('#m-all').onclick = () => { memberList().list.forEach((m) => state.members.add(m.id)); redrawList(); updateBar(el); };
    form.querySelector('#m-none').onclick = () => { state.members.clear(); redrawList(); updateBar(el); };
  } else {
    form.querySelectorAll('[data-other]').forEach((input) => (input.oninput = () => {
      state.others[Number(input.dataset.other)][input.dataset.key] = input.value;
      if (input.dataset.key === 'name') updateBar(el);
    }));
    form.querySelectorAll('[data-drop-other]').forEach((b) => (b.onclick = () => { state.others.splice(Number(b.dataset.dropOther), 1); renderForm(); }));
    form.querySelector('#add-other').onclick = () => { state.others.push({ name: '', email: '', address: '' }); renderForm(); };
  }
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => renderPreview(await api('/api/series-preview', payload())));
  };
}

function updateBar(el) {
  const total = state.lines.reduce((s, l) => s + (parseCents(l.amount) || 0), 0);
  const count = state.members.size + state.others.filter((r) => r.name).length;
  const bar = el.querySelector('.bulkbar strong');
  if (bar) bar.textContent = `${count} Empfänger · je ${eur(total)} · gesamt ${eur(total * count)}`;
  const counts = el.querySelectorAll('[data-rtab] .n');
  if (counts.length === 2) { counts[0].textContent = state.members.size; counts[1].textContent = state.others.filter((r) => r.name).length; }
}

function renderPreview(pv) {
  const noMail = pv.recipients.filter((r) => !r.email).length;
  const el = dialog('Vorschau – noch nichts festgeschrieben', html`
    <div class="cards">
      <div class="card"><span class="label">Rechnungen</span><span class="value">${pv.recipients.length}</span><span class="note">je ${eur(pv.total)} · gesamt ${eur(pv.sum)}</span></div>
      <div class="card"><span class="label">Nummern</span><span class="value" style="font-size:19px">${pv.numbers[0]}${pv.recipients.length > 1 ? html`<br>bis ${pv.numbers[1]}` : ''}</span><span class="note">vorläufig, endgültig beim Festschreiben</span></div>
      <div class="card"><span class="label">Fällig am</span><span class="value" style="font-size:22px">${date(pv.due)}</span><span class="note">Buchung: ${kvName(pv.kv)}</span></div></div>
    <p class="small muted">Positionen: ${pv.lines.map((l, i) => html`${i ? ' · ' : ''}${l.description} (${eur(l.amount)}, Konto ${l.account})`)}</p>
    ${noMail ? html`<p class="notice warn">${noMail} Empfänger ohne E-Mail-Adresse – diese Rechnungen bitte drucken und per Post schicken.</p>` : ''}
    ${erechnungHinweis('normal')}
    ${table(['Nummer', 'Empfänger', 'E-Mail', 'Vorschau'], pv.recipients.map((r, i) => html`<tr>
      <td>${r.number}</td><td>${r.name}<span class="sub">${r.member_number || 'kein Mitglied'}</span></td><td>${r.email || badge('keine – Post')}</td>
      <td style="white-space:nowrap"><a class="btn small" href="/api/preview.pdf?draft=${pv.token}&i=${i}" target="_blank" rel="noopener">PDF</a>
        <button type="button" class="btn small ghost" data-mail="${i}">E-Mail</button></td></tr>`))}
    <div class="form-actions"><button type="button" class="btn ghost" data-back>Zurück</button>
      <button type="button" class="btn primary" data-issue>${pv.recipients.length} Rechnung${pv.recipients.length === 1 ? '' : 'en'} festschreiben</button></div>`, { wide: true });
  el.querySelector('[data-back]').onclick = renderForm;
  el.querySelectorAll('[data-mail]').forEach((b) => (b.onclick = () => mailPreview(`draft=${pv.token}&i=${b.dataset.mail}`, 'Vorschau ' + pv.recipients[Number(b.dataset.mail)].name)));
  el.querySelector('[data-issue]').onclick = (e) => safe(async () => {
    e.target.disabled = true;
    try {
      const { ids } = await api('/api/series', { token: pv.token });
      state = null;
      await refresh();
      afterIssue(ids, ids.length === 1 ? 'Rechnung' : 'Rechnungen');
    } finally { e.target.disabled = false; }
  });
}

export function createDialog() {
  if (!state) state = fresh();
  renderForm();
}
