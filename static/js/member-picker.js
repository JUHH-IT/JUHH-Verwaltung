// Mitgliedersuche als Popup (Wunsch Lennert Gust 29.10.2026): zuerst Kreisverband wählen, dann suchen – nach Vor- oder
// Nachname, Anschrift (Straße, PLZ, Ort), Mitgliedsnummer oder E-Mail. Für Beitragszahlungen auf 8100: ein Mitglied
// (Enter/Klick übernimmt sofort) oder eine Sammelzahlung auf mehrere Mitglieder mit Beträgen.
// Ab 0.29 auch Personen, die nicht (mehr) in der Mitgliederliste stehen (z. B. ausgetreten, Daten gelöscht): Name, Nummer,
// Kreisverband, Grund – Server: beitraege.external, Zuordnung {external: {...}, amount}. Negative Beträge (Rückzahlung) erlaubt.
// Ab 0.31 „Unklar – Klärfall“: {case: {name, reason}, amount} geht als Aufgabe an die Mitgliederverwaltung (klaerfaelle.py) und bleibt
// bis zur Klärung auf 8100 ohne Mitglied und ohne Kreisverband.
import { html, mount } from './html.js';
import { eur, toInput, parseCents } from './format.js';
import { store, kvName } from './store.js';
import { overlay, icon } from './ui.js';
import { ovById, ovLabel } from './gliederung.js';

const LIMIT = 80;

/** Kleinbuchstaben ohne Akzente (ä → a, ß → ss) für die Suche. */
export const fold = (text) => String(text ?? '').toLowerCase().replace(/ß/g, 'ss').normalize('NFD').replace(/[̀-ͯ]/g, '');

const haystack = (m) => fold(`${m.last_name || ''} ${m.first_name || ''} ${m.name} ${m.number} ${m.street || ''} ${m.zip || ''} ${m.city || ''} ${m.country || ''} ${m.address || ''} ${m.email || ''} ${kvName(m.kv)} ${ovLabel(ovById(m.ov_id))}`);
export const sortName = (m) => (m.last_name ? `${m.last_name}, ${m.first_name || ''}`.replace(/, $/, '') : m.name);
export const addressLine = (m) => (m.street ? `${m.street}${m.zip || m.city ? ` · ${m.zip || ''} ${m.city || ''}`.trimEnd() : ''}${m.country ? ` · ${m.country}` : ''}`
  : String(m.address || '').split('\n').join(' · '));

/** Suche mit mehreren Wörtern: jedes Wort muss vorkommen (z. B. „muster hamburg“). */
export function matchMembers(list, query, kv = '') {
  const words = fold(query).split(/\s+/).filter(Boolean);
  return list.filter((m) => (!kv || m.kv === kv) && words.every((w) => haystack(m).includes(w)));
}

/** Mitglieder, deren Nachname (und Vorname) bzw. Mitgliedsnummer im Text (Zahlungspartner, Verwendungszweck) steht. */
export function suggestMembers(text) {
  const t = fold(text), words = new Set(t.split(/[^a-z0-9]+/).filter(Boolean));
  return (store.data?.members || []).map((m) => {
    const last = fold(m.last_name || String(m.name).split(' ').pop()).split(/[^a-z0-9]+/).filter(Boolean);
    const first = fold(m.first_name || '').split(/[^a-z0-9]+/).filter(Boolean);
    const number = fold(m.number);
    let score = number.length >= 3 && new RegExp(`(^|[^a-z0-9])${number.replace(/[^a-z0-9]/g, '\\$&')}($|[^a-z0-9])`).test(t) ? 5 : 0;   // ganze Nummer
    if (last.length && last.every((w) => words.has(w))) score += 2 + (first.some((w) => words.has(w)) ? 1 : 0);
    return { m, score };
  }).filter((x) => x.score >= 3).sort((a, b) => b.score - a.score).slice(0, 3).map((x) => x.m);
}

const memberById = (id) => store.data.members.find((m) => m.id === Number(id));

/** Mitgliedsnummer zum Vergleichen wie beitraege.number_key: nur Ziffernfolgen ohne führende Nullen, sonst die Buchstaben. */
const numberKey = (n) => { const d = String(n ?? '').match(/[0-9]+/g); return d ? d.map((x) => x.replace(/^0+(?=.)/, '')).join('-') : fold(n).replace(/[^0-9a-z]/g, ''); };

/** Vorgabe für den Grund (wie beitraege.EXTERNAL_REASON). */
export const EXTERNAL_REASON = 'ausgetreten, nicht mehr in der Mitgliederliste';
/** Vorgabe für den Grund eines Klärfalls (wie klaerfaelle.REASON). */
export const CASE_REASON = 'nicht in der Mitgliederliste gefunden';
const sameKey = (a, b) => (a.case || b.case ? false : a.external || b.external ? !!(a.external && b.external) && fold(a.external.name) === fold(b.external.name)
  && (a.external.number || '') === (b.external.number || '') : a.member_id === b.member_id);

/** Anzeige einer Person ohne Eintrag in der Mitgliederliste. */
export const externalLabel = (x) => `${x.name} (nicht in der Mitgliederliste${x.number ? `, ${x.number}` : ''}, ${kvName(x.kv)})`;
/** Anzeige eines Klärfalls (unklar, wer gezahlt hat – Aufgabe der Mitgliederverwaltung). */
export const caseLabel = (x) => `${x.name} (Klärfall für die Mitgliederverwaltung)`;

/**
 * Popup öffnen. amount: Betrag der Zahlung in Cent (null = nur ein Mitglied auswählen, ohne Betrag; negativ = Rückzahlung);
 * selected: [{member_id, amount}] bzw. [{external, amount}] (Cent); hint: Text für Vorschläge; payer: Name laut Kontoauszug
 * (Vorgabe für eine Person außerhalb der Liste); external: Person außerhalb der Mitgliederliste zulassen (nur bei Zahlungen);
 * entryKv: Kreisverband, für den die Zahlung schon gebucht ist (Vorgabe); split: Sammelzahlung aufteilen erlaubt (Standard bei Zahlungen);
 * unclear: „Unklar – Klärfall“ zulassen (Standard bei Zahlungen);
 * onDone(list) mit [{member_id, amount}], [{external, amount}] bzw. [{case, amount}].
 */
export function pickMembers({ amount = null, selected = [], hint = '', payer = '', external = amount !== null, entryKv = '', split = amount !== null,
  unclear = amount !== null, title = 'Mitglied zuordnen', onDone }) {
  const kvs = store.data.kv.filter((k) => k.code !== 'LV' && store.data.members.some((m) => m.kv === k.code));
  const suggestions = hint ? suggestMembers(hint) : [];
  let kv = '', query = '', active = 0;
  let chosen = selected.map((x) => (x.case ? { case: { ...x.case }, amount: x.amount ?? amount } : x.external ? { external: { ...x.external }, amount: x.amount ?? amount }
    : { member_id: Number(x.member_id), amount: x.amount ?? amount }));
  let multi = split && amount !== null && chosen.length > 1;
  const sign = (v) => v !== null && v !== undefined && v !== 0 && (amount < 0 ? v < 0 : v > 0);   // gleiche Richtung wie die Zahlung
  const preset = chosen.length === 1 && chosen[0].external ? chosen[0].external : { name: payer, number: '', kv: entryKv || 'LV', reason: EXTERNAL_REASON };   // Ändern: bisherige Angaben
  const el = overlay(title, html`
    ${amount !== null ? html`<p class="muted">${amount < 0 ? 'Rückzahlung bzw. Rücklastschrift' : 'Zahlung'} ${eur(amount)} · zählt für den 25-%-Anteil des Kreisverbands
      des Mitglieds (nur gezahlte Beiträge).</p>` : ''}
    <div class="kv-chips" role="group" aria-label="Kreisverband">
      <button type="button" class="kv-chip" data-kv="" aria-pressed="true">Alle <span>${store.data.members.length}</span></button>
      ${kvs.map((k) => html`<button type="button" class="kv-chip" data-kv="${k.code}" aria-pressed="false">${k.name} <span>${store.data.members.filter((m) => m.kv === k.code).length}</span></button>`)}
    </div>
    <label class="search">${icon('search', 16)}<span class="sr-only">Mitglied suchen</span><input id="member-query" type="search" autocomplete="off"
      placeholder="Name, Straße, PLZ, Ort, Mitgliedsnummer oder E-Mail"></label>
    <div id="member-list" class="account-list member-list" role="listbox" aria-label="Mitglieder"></div>
    ${external ? html`<details class="member-external" id="member-external"><summary>Person nicht in der Mitgliederliste (z. B. ausgetreten, Daten gelöscht)</summary>
      <div class="formgrid">
        <label class="field">Name<input id="ext-name" autocomplete="off" maxlength="120" value="${preset.name}"></label>
        <label class="field">Mitgliedsnummer<input id="ext-number" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" maxlength="30" placeholder="falls bekannt" value="${preset.number || ''}"></label>
        <label class="field">Kreisverband<select id="ext-kv">${store.data.kv.map((k) => html`<option value="${k.code}" ${k.code === preset.kv ? 'selected' : ''}>${k.code === 'LV' ? 'kein Kreisverband (Landesverband)' : k.name}</option>`)}</select></label>
        <label class="field">Grund<input id="ext-reason" autocomplete="off" maxlength="200" value="${preset.reason}"></label>
      </div>
      <p class="small muted">Zählt im Haushalt und für den Anteil des gewählten Kreisverbands wie ein Mitgliedsbeitrag, bekommt aber kein Beitragskonto.</p>
      <p class="small over" id="ext-error" role="alert" hidden></p>
      <button type="button" class="btn" id="ext-add">${icon('plus', 16)} Diese Person ${amount !== null ? 'zuordnen' : 'übernehmen'}</button></details>` : ''}
    ${unclear ? html`<details class="member-external" id="member-case"><summary>Unklar, zu wem die Zahlung gehört – als Klärfall an die Mitgliederverwaltung</summary>
      <div class="formgrid">
        <label class="field">Zahler laut Zahlung<input id="case-name" autocomplete="off" maxlength="120" value="${payer}"></label>
        <label class="field">Was ist unklar?<input id="case-reason" autocomplete="off" maxlength="200" value="${CASE_REASON}"></label>
      </div>
      <p class="small muted">Der Betrag bleibt als Mitgliedsbeitrag auf 8100, aber ohne Mitglied und ohne Kreisverband – bis die Mitgliederverwaltung
        den Fall unter „Klärfälle“ geklärt hat. Solange ist der KV-Budgetabschluss des Jahres gesperrt.</p>
      <p class="small over" id="case-error" role="alert" hidden></p>
      <button type="button" class="btn" id="case-add">${icon('plus', 16)} Als Klärfall melden</button></details>` : ''}
    ${amount !== null && split ? html`<label class="check"><input type="checkbox" id="member-multi" ${multi ? 'checked' : ''}> Sammelzahlung – auf mehrere Mitglieder aufteilen</label>
      <div id="member-chosen"></div>` : ''}
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button>
      ${amount !== null && split ? html`<button type="button" class="btn primary" id="member-ok">Übernehmen</button>` : ''}</div>
    <p class="small muted">↑/↓ wählen · Enter übernehmen · Esc schließen</p>`, { wide: true });
  const list = el.querySelector('#member-list'), input = el.querySelector('#member-query');
  const ok = el.querySelector('#member-ok'), box = el.querySelector('#member-chosen');
  el.querySelectorAll('[data-close]').forEach((b) => (b.onclick = () => el.close()));

  const shown = () => {
    const found = matchMembers(store.data.members, query, kv);
    if (!query && !kv && suggestions.length) return [...suggestions, ...found.filter((m) => !suggestions.includes(m))];
    return found;
  };
  const finish = (items) => { el.close(); onDone(items); };
  const rest = () => (amount ?? 0) - chosen.reduce((s, c) => s + (c.amount || 0), 0);

  function choose(m) {
    if (!m) return;
    add({ member_id: m.id });
  }

  function add(item) {
    if (amount === null || !multi) return finish([{ ...item, amount }]);
    if (!chosen.some((c) => sameKey(c, item))) chosen.push({ ...item, amount: sign(rest()) ? rest() : null });
    drawChosen();
    draw();
  }

  function addExternal() {
    const ext = { name: el.querySelector('#ext-name').value.trim().replace(/\s+/g, ' '), number: el.querySelector('#ext-number').value.trim(),
      kv: el.querySelector('#ext-kv').value, reason: el.querySelector('#ext-reason').value.trim() };
    const owner = ext.number && store.data.members.find((m) => numberKey(m.number) === numberKey(ext.number));
    const error = !ext.name ? 'Bitte den Namen angeben.' : !ext.reason ? 'Bitte den Grund angeben (z. B. ausgetreten).'
      : owner ? `Die Nummer ${ext.number} gehört zu ${sortName(owner)} – bitte das Mitglied in der Liste auswählen.` : '';
    const box = el.querySelector('#ext-error');
    box.hidden = !error;
    box.textContent = error;
    if (!error) add({ external: ext });
  }

  function addCase() {
    const item = { name: el.querySelector('#case-name').value.trim().replace(/\s+/g, ' '), reason: el.querySelector('#case-reason').value.trim() };
    const box = el.querySelector('#case-error');
    box.hidden = !!item.name;
    box.textContent = item.name ? '' : 'Bitte angeben, wer gezahlt hat (Name laut Zahlung).';
    if (item.name) add({ case: item });
  }

  function drawChosen() {
    if (!box) return;
    box.hidden = !multi;
    if (!multi) { ok.disabled = !chosen.length; return; }
    const r = rest();
    mount(box, chosen.length ? html`<table class="member-chosen"><tbody>${chosen.map((c, i) => {
      const m = c.external || c.case ? null : memberById(c.member_id);
      return html`<tr><td>${c.case ? html`<strong>${c.case.name}</strong> <span class="badge warn">Klärfall</span><span class="sub">${c.case.reason || CASE_REASON}</span>`
        : c.external ? html`<strong>${c.external.name}</strong> <span class="badge">nicht in der Liste</span><span class="sub">${c.external.number ? `${c.external.number} · ` : ''}${kvName(c.external.kv)} · ${c.external.reason}</span>`
        : html`<strong>${m ? sortName(m) : c.member_id}</strong><span class="sub">${m ? `${m.number} · ${kvName(m.kv)}` : ''}</span>`}</td>
        <td class="num"><input class="amount-input" data-chosen-amount="${i}" inputmode="decimal" value="${toInput(c.amount)}" aria-label="Betrag in Euro"></td>
        <td><button type="button" class="btn ghost icon-btn" data-chosen-remove="${i}" aria-label="Entfernen">${icon('trash', 16)}</button></td></tr>`;
    })}</tbody></table>
      <p class="small ${r === 0 ? 'muted' : 'over'}" role="status">${r === 0 ? 'Vollständig aufgeteilt.' : `Noch zu verteilen: ${eur(r)}`}</p>`
      : html`<p class="small muted">Mitglieder in der Liste anklicken (oder unten eine Person außerhalb der Liste angeben), um sie der Sammelzahlung hinzuzufügen.</p>`);
    box.querySelectorAll('[data-chosen-amount]').forEach((x) => (x.onchange = () => { chosen[Number(x.dataset.chosenAmount)].amount = parseCents(x.value); drawChosen(); }));
    box.querySelectorAll('[data-chosen-remove]').forEach((x) => (x.onclick = () => { chosen.splice(Number(x.dataset.chosenRemove), 1); drawChosen(); }));
    ok.disabled = !(chosen.length && r === 0 && chosen.every((c) => sign(c.amount)));
  }

  function draw() {
    const all = shown(), visible = all.slice(0, LIMIT);
    active = Math.min(active, Math.max(0, visible.length - 1));
    const paidLabel = (m) => (m.paid ? `Beitrag ${store.year}: ${eur(m.paid)}` : `Beitrag ${store.year}: noch nichts`);
    mount(list, visible.length ? html`${!query && !kv && suggestions.length ? html`<p class="account-section">Passend zur Zahlung</p>` : ''}
      ${visible.map((m, i) => html`${!query && !kv && suggestions.length && i === suggestions.length ? html`<p class="account-section">Alle Mitglieder</p>` : ''}
      <button type="button" role="option" aria-selected="${i === active}" class="member-option ${i === active ? 'active' : ''} ${chosen.some((c) => !c.external && !c.case && c.member_id === m.id) ? 'current' : ''}" data-member="${m.id}">
        <span><strong>${sortName(m)}</strong>${m.left_on ? html` <span class="badge">ausgetreten</span>` : ''}<small>${m.number} · ${addressLine(m)}</small></span>
        <span class="member-side">${kvName(m.kv)}<small>${paidLabel(m)}</small></span></button>`)}
      ${all.length > visible.length ? html`<p class="small muted" style="padding:10px 12px">${all.length - visible.length} weitere – Suche verfeinern.</p>` : ''}`
      : html`<p class="muted" style="padding:12px">${store.data.members.length ? 'Kein passendes Mitglied.' : 'Noch keine Mitglieder – unter „Mitglieder“ anlegen oder Liste importieren.'}</p>`);
    list.querySelectorAll('[data-member]').forEach((b) => (b.onclick = () => choose(memberById(b.dataset.member))));
    list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
  }

  el.querySelectorAll('[data-kv]').forEach((b) => (b.onclick = () => {
    kv = b.dataset.kv;
    el.querySelectorAll('[data-kv]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    active = 0;
    draw();
    input.focus();
  }));
  input.oninput = () => { query = input.value; active = 0; draw(); };
  input.onkeydown = (e) => {
    const visible = shown().slice(0, LIMIT);
    if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, visible.length - 1); draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(visible[active]); }
  };
  el.querySelector('#member-multi')?.addEventListener('change', (e) => { multi = e.target.checked; if (!multi) chosen = chosen.slice(0, 1); drawChosen(); draw(); });
  el.querySelector('#ext-add')?.addEventListener('click', addExternal);
  el.querySelector('#case-add')?.addEventListener('click', addCase);
  if (chosen.some((c) => c.external) && !multi) el.querySelector('#member-external')?.setAttribute('open', '');
  ok?.addEventListener('click', () => {
    if (!multi) return chosen.length && finish([{ ...chosen[0], amount }]);
    finish(chosen.map((c) => ({ ...c })));
  });
  draw();
  drawChosen();
  input.focus();
  return el;
}

/** Auswahl aus pickMembers für den Server: je Person Mitglied, Person außerhalb der Liste oder Klärfall; Beträge nur bei mehreren (always: immer). */
export const membersPayload = (items, always = false) => items.map((x) => ({ ...(x.case ? { case: x.case } : x.external ? { external: x.external } : { member_id: x.member_id }),
  ...(always || items.length > 1 ? { amount: toInput(x.amount) } : {}) }));

/** Kurzfassung einer Zuordnung für Buchungsplatz und Listen. */
export function membersLabel(items) {
  return items.map((c) => {
    if (c.case) return `${caseLabel(c.case)}${items.length > 1 ? ` ${eur(c.amount)}` : ''}`;
    if (c.external) return `${externalLabel(c.external)}${items.length > 1 ? ` ${eur(c.amount)}` : ''}`;
    const m = memberById(c.member_id);
    return `${m ? `${sortName(m)} (${m.number}, ${kvName(m.kv)})` : `Mitglied ${c.member_id}`}${items.length > 1 ? ` ${eur(c.amount)}` : ''}`;
  }).join(' · ');
}
