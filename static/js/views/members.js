// Mitglieder (Mitgliederverwaltung): Liste mit Suche (Name, Anschrift, Nummer, E-Mail), Kreis- und Ortsverband, Status, Anlegen/Bearbeiten
// mit allen Angaben der Mitgliederverwaltung (Kreisverband als Auswahl, Ortsverband nur aus dem gewählten Kreisverband – gliederung.js) und
// Import einer Mitgliederliste (Excel/CSV, auch kennwortgeschützt). Ab 0.31 zeigt die Liste nur noch den Stand des Beitrags; Beträge,
// Beitragseingänge (views/dues.js) und OP-Listen (views/open-items.js) gehören zur Buchhaltung, Klärfälle zu views/klaerfaelle.js.
// Das geöffnete Mitglied zeigt weiter sein Beitragskonto (mitgliederkonto.py): Jahre, Buchungen, Soll von Hand festlegen.
import { api } from '../api.js';
import { html, mount, raw } from '../html.js';
import { eur, signed, date, toInput } from '../format.js';
import { store, refresh, kvName, go } from '../store.js';
import { pageHead, icon, table, empty, dialog, overlay, safe, toast, badge, uploadProtected } from '../ui.js';
import { kvOvFields, bindKvOv, ovById, ovLabel, ovsOf } from '../gliederung.js';
import { invoiceBadge } from './invoices.js';
import { mandateSection, bindMandateSection, activeMandate } from './sepa.js';
import { matchMembers, sortName, addressLine } from '../member-picker.js';

let search = '';
let kvFilter = '';
let ovFilter = '';   // '' = alle, 'none' = ohne Ortsverband, sonst id
let stateFilter = '';
let memberFilter = 'active';   // aktiv | ausgetreten | alle
const LIMIT = 500;   // Zeilen in der Liste (Suche grenzt ein)
const STATES = { arrears: 'mit Rückstand', credit: 'mit Guthaben', issue: 'Beitrag zu klären', guest: 'Gastmitglieder', free: 'beitragsfrei' };
const inState = (m) => !stateFilter || ({ arrears: m.balance < 0, credit: m.balance > 0, issue: m.dues_issues.length > 0, guest: !!m.guest,
  free: !!m.dues_free })[stateFilter];
/** Kennzeichen Gastmitglied / beitragsfrei (Mitgliederverwaltung). */
const statusBadges = (m) => html`${m.guest ? html` ${badge('Gast', 'navy')}` : ''}${m.dues_free ? html` ${badge('beitragsfrei', '')}` : ''}${
  m.no_mail ? html` ${badge('Postausschluss', 'warn')}` : ''}`;
/** Volle Jahre zwischen zwei Daten (JJJJ-MM-TT) – Alter bzw. Jubiläumsjahre wie in der Mitgliederverwaltung. */
const years = (from, to = new Date().toISOString().slice(0, 10)) => (from ? Number(to.slice(0, 4)) - Number(from.slice(0, 4)) - (to.slice(5) < from.slice(5) ? 1 : 0) : null);
const text = (name, label, v, attrs = {}) => html`<label class="field">${label}<input name="${name}" autocomplete="off" maxlength="${attrs.max || 200}"
  ${attrs.type ? html`type="${attrs.type}"` : ''} ${attrs.required ? 'required' : ''} placeholder="${attrs.hint || ''}" value="${v[name] || ''}">${
  attrs.note ? html`<span class="small muted">${attrs.note}</span>` : ''}</label>`;
const NOAUTO = 'autocapitalize="off" autocorrect="off" spellcheck="false"';

/** Saldo des Beitragskontos: − Rückstand (rot), + Guthaben. */
export const balanceCell = (cents) => (cents < 0 ? html`<span class="over">${signed(cents)}</span>` : cents > 0 ? signed(cents) : html`<span class="muted">${eur(0)}</span>`);

/** Jahresbeitrag: fester Betrag oder automatisch nach Alter (Beitrag des gewählten Haushaltsjahres laut Beitragskonto). */
const duesLabel = (m) => {
  if (m.dues !== null && m.dues !== undefined) return html`${eur(m.dues)} <span class="muted">(fester Betrag)</span>`;
  const byAge = String(m.dues_basis || '').startsWith('Staffel');   // „automatisch nach Alter“ nur, wenn das Soll wirklich aus der Staffel kommt
  if (m.dues_soll) return html`${eur(m.dues_soll)} <span class="muted">(${store.year}${byAge ? ', automatisch nach Alter' : ''}: ${m.dues_basis})</span>`;
  return html`<span class="muted">${m.dues_basis ? `${store.year}: ${m.dues_basis}` : 'automatisch nach Alter'}</span>`;
};

/** Anschrift im Mitgliedsdialog prüfen (ab 0.32): mit den amtlichen Hamburger Adressdaten auf diesem Rechner (adressen.py) – gibt es die
 * Straße, passt die PLZ? Nur ein Hinweis mit Vorschlägen; gespeichert werden kann immer (Mitglieder außerhalb Hamburgs, neue Straßen). */
function bindAddressCheck(form) {
  const box = form.querySelector('[data-address-check]');
  let round = 0;
  // Vorschlag übernehmen: nur den Straßennamen ersetzen, alles ab der Hausnummer bleibt, wie es eingegeben wurde („12 Haus 3“, „3, 2. OG“);
  // eine vorangestellte Hausnummer („7 Musterweg“) rückt nach hinten (Prüfbefund 0.32).
  const withStreet = (street, typed = '') => {
    const value = form.street.value.replace(/\s+/g, ' ').trim();
    if (typed && value.includes(typed)) return value.replace(typed, () => street);   // genau den Teil ersetzen, den der Server als Straße gelesen hat
    const lead = value.match(/^(\d+\s*[a-zA-Z]?(?:\s*[-–/]\s*\d+\s*[a-zA-Z]?)?)\.?\s+\D/);   // „7 Musterweg“, „12-14 Musterweg“
    if (lead) return `${street} ${lead[1].trim()}`;
    const tail = value.match(/^\D+?(\d.*)$/) || value.match(/^[^,]*(,.*)$/);   // ab der ersten Zahl, sonst ab dem Komma
    return tail ? (tail[1].startsWith(',') ? street + tail[1] : `${street} ${tail[1].trim()}`) : street;
  };
  const run = async () => {
    const mine = ++round;
    const street = form.street.value.trim(), zip = form.zip.value.trim(), city = form.city.value.trim(), abroad = !/^(|d|de|deu|ger|brd|deutschland|germany|bundesrepublik( deutschland)?)$/i.test(form.country.value.trim());   // wie mitglieder.DOMESTIC
    if (!street || abroad) return mount(box, '');
    let r;
    try { r = await api('/api/adresse-pruefen', { street, zip, city }); } catch { return mount(box, ''); }   // die Prüfung darf das Speichern nie stören
    if (mine !== round || !box.isConnected) return;
    const kv = r.kv && form.kv && form.kv.value && form.kv.value !== r.kv && !['LV', '5800'].includes(form.kv.value)
      ? html` Der Wohnort liegt im Bezirk ${r.bezirk} (Kreisverband ${kvName(r.kv)}).` : '';
    const where = [r.stadtteil, r.bezirk].filter(Boolean).join(', ');
    const view = {
      haus: () => html`<span class="ok">${icon('check', 14)} Adresse gefunden</span> – ${where}.${kv}`,
      strasse: () => html`<span class="warn">Straße gefunden (${where}), aber diese Hausnummer steht nicht im amtlichen Bestand – bitte prüfen.</span>${kv}`,
      plz: () => html`<span class="warn">Die Straße gibt es in Hamburg, aber mit der PLZ ${r.zips.join(' oder ')}.</span>
        ${r.zips.length === 1 ? html`<button type="button" class="btn small" data-zip="${r.zips[0]}">PLZ ${r.zips[0]} übernehmen</button>` : ''}`,
      aehnlich: () => html`<span class="warn">So gibt es die Straße in Hamburg nicht – gemeint ist wohl:</span>
        ${r.suggestions.map((s, i) => html`<button type="button" class="btn small" data-suggestion="${i}">${s.street}${s.zip ? ` (${s.zip})` : ''}</button> `)}`,
      unbekannt: () => html`<span class="warn">Diese Straße gibt es in Hamburg nicht.</span>
        ${r.suggestions.length ? html` Gemeint? ${r.suggestions.map((s, i) => html`<button type="button" class="btn small" data-suggestion="${i}">${s.street}${s.zip ? ` (${s.zip})` : ''}</button> `)}` : ''}`,
      ausserhalb: () => html`<span class="muted">Außerhalb Hamburgs – die Adresse wird nicht geprüft.</span>`,
      keine_daten: () => html`<span class="muted">Adressprüfung: Die amtlichen Adressdaten sind noch nicht geladen (Verwaltung → Einstellungen → Schalter).</span>`,
    }[r.status];
    mount(box, view ? view() : '');
    box.querySelector('[data-zip]')?.addEventListener('click', (e) => { form.zip.value = e.target.dataset.zip; run(); });
    box.querySelectorAll('[data-suggestion]').forEach((b) => (b.onclick = () => {
      const s = r.suggestions[Number(b.dataset.suggestion)];
      form.street.value = withStreet(s.street, r.typed);
      if (s.zip) form.zip.value = s.zip;
      run();
    }));
  };
  for (const name of ['street', 'zip', 'city', 'country', 'kv']) form[name]?.addEventListener('change', () => safe(run));
  if (form.street.value.trim()) safe(run);
}

/** Mitglied anlegen (m = null; preset: Vorgaben, z. B. Name aus einem Klärfall) oder bearbeiten. onSaved(id) nach dem Speichern. */
export function memberDialog(m = null, { preset = null, onSaved = null } = {}) {
  const v = m || preset || {};
  const fixed = v.dues !== null && v.dues !== undefined;   // fester Jahresbeitrag; sonst automatisch nach Alter
  const legacy = m && !v.zip;   // vor 0.22 angelegt und Anschrift ohne erkennbare PLZ: bitte aufteilen
  const el = dialog(m ? `Mitglied bearbeiten · ${m.name}` : 'Mitglied anlegen', html`<form id="member-form" class="member-form" style="display:flex;flex-direction:column;gap:16px">
    ${legacy ? html`<p class="notice warn">Bisherige Anschrift: „${v.address}“ – bitte auf Straße, PLZ und Ort aufteilen.</p>` : ''}
    <h3>Person</h3>
    <div class="formgrid">
      ${text('salutation', 'Anrede', v, { hint: 'z. B. Herrn, Frau' })}
      ${text('title', 'Titel', v, { hint: 'z. B. Dr.' })}
      ${text('first_name', 'Vorname', v, { required: true })}
      ${text('name_prefix', 'Adelsname / Namenszusatz', v, { hint: 'z. B. von' })}
      ${text('last_name', 'Nachname', v, { required: true })}
      ${text('name_suffix', 'Zusatzname', v, { hint: 'optional' })}
      <label class="field">Geburtsdatum<input name="birth" type="date" required value="${v.birth || ''}"></label>
      ${text('gender', 'Geschlecht', v, { hint: 'M, W, D', max: 20 })}
      ${text('nationality', 'Nationalität', v, { hint: 'z. B. D' })}
      ${text('letter_salutation', 'Briefanrede 1', v, { hint: 'z. B. Lieber Max' })}
      ${text('letter_salutation2', 'Briefanrede 2', v, { hint: 'z. B. Hallo Max' })}
    </div>
    <h3>Mitgliedschaft</h3>
    <div class="formgrid">
      <label class="field">Mitgliedsnummer<input name="number" required autocomplete="off" ${raw(NOAUTO)} placeholder="z. B. A0000000001" value="${v.number || ''}"></label>
      ${kvOvFields(v.kv || '', v.ov_id)}
      <label class="field">Eintritt (Verein)<input name="joined" type="date" required value="${v.joined || ''}"></label>
      <label class="field">Eintritt Kreisverband<input name="kv_joined" type="date" value="${v.kv_joined || ''}"></label>
      <label class="field">Austritt<input name="left_on" type="date" value="${v.left_on || ''}"><span class="small muted">optional</span></label>
      ${text('clubs', 'Vereinigungen (Vereine)', v, { hint: 'z. B. JU,CDU' })}
      ${text('club', 'Verein', v, { hint: 'z. B. JU' })}
    </div>
    <h3>Anschrift</h3>
    <div class="formgrid">
      ${text('address_extra', 'Adresszusatz (Name 2)', v, { hint: 'optional, z. B. c/o' })}
      ${text('address_extra2', 'Adresszusatz 2 (Name 3)', v, { hint: 'optional' })}
      <label class="field span">Straße und Hausnummer<input name="street" required autocomplete="off" value="${legacy ? '' : v.street || ''}"></label>
      <label class="field">PLZ<input name="zip" required inputmode="numeric" autocomplete="off" value="${v.zip || ''}"></label>
      <label class="field">Ort<input name="city" required autocomplete="off" value="${v.city || (m ? '' : 'Hamburg')}"></label>
      <label class="field">Land<input name="country" autocomplete="off" placeholder="leer = Deutschland" value="${v.country || ''}"></label>
      <div class="span address-check small" data-address-check aria-live="polite"></div>
      <label class="check"><input type="checkbox" name="no_mail" value="1" ${v.no_mail ? 'checked' : ''}> Postausschluss (keine Post an diese Anschrift)</label>
    </div>
    <h3>Kontakt</h3>
    <div class="formgrid">
      <label class="field">E-Mail privat (für den Rechnungsversand)<input name="email" type="email" ${raw(NOAUTO)} value="${v.email || ''}"></label>
      <label class="field">E-Mail dienstlich<input name="email_work" type="email" ${raw(NOAUTO)} value="${v.email_work || ''}"></label>
      ${text('phone', 'Telefon privat', v, { type: 'tel' })}
      ${text('phone_work', 'Telefon dienstlich', v, { type: 'tel' })}
      ${text('mobile', 'Handy privat', v, { type: 'tel' })}
      ${text('mobile_work', 'Handy dienstlich', v, { type: 'tel' })}
    </div>
    <h3>Beitrag</h3>
    <div class="formgrid">
      <fieldset class="span member-dues"><legend>Jahresbeitrag</legend>
        <label class="check"><input type="radio" name="dues_mode" value="auto" ${fixed ? '' : 'checked'}> automatisch nach Alter (Beitragsstaffel, Einstellungen → Beiträge)</label>
        <p class="small" data-dues-auto aria-live="polite"></p>
        <label class="check"><input type="radio" name="dues_mode" value="fixed" ${fixed ? 'checked' : ''}> fester Betrag in Euro
          <input name="dues" inputmode="decimal" autocomplete="off" aria-label="Fester Jahresbeitrag in Euro" value="${toInput(v.dues)}" class="${fixed ? '' : 'is-off'}"></label>
        <span class="small muted">Ein fester Betrag hat Vorrang vor der Staffel. Eine Änderung gilt ab dem laufenden Jahr – frühere Jahre behalten ihren Beitrag.</span></fieldset>
      ${text('payment_method', 'Zahlungsart (Mitgliederverwaltung)', v, { hint: 'z. B. R, L' })}
      <label class="field">Kontostand laut Mitgliederverwaltung<input name="external_balance" inputmode="decimal" value="${toInput(v.external_balance)}"
        placeholder="nur zur Information"></label>
      <label class="check"><input type="checkbox" name="dues_free" value="1" ${v.dues_free ? 'checked' : ''}> beitragsfrei</label>
      <label class="check"><input type="checkbox" name="guest" value="1" ${v.guest ? 'checked' : ''}> Gastmitglied (beitragsfrei, keine Beitragsrechnung)</label>
    </div>
    ${m ? html`<p class="small muted">Bereits erstellte Rechnungen bleiben unverändert. Eine neue E-Mail-Adresse gilt auch für noch nicht versendete Rechnungen.</p>` : ''}
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Mitglied speichern</button></div></form>`, { wide: true });
  const form = el.querySelector('#member-form');
  bindKvOv(form);
  bindDues(form);
  bindAddressCheck(form);
  el.querySelector('form [data-close]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const flags = Object.fromEntries(['dues_free', 'guest', 'no_mail'].map((k) => [k, form[k].checked ? '1' : '0']));
      const { dues_mode: mode, ...fields } = Object.fromEntries(new FormData(form));
      const data = { ...fields, ...flags, dues: mode === 'fixed' ? form.dues.value : '' };   // automatisch = kein fester Betrag
      if (mode === 'fixed' && !data.dues.trim()) throw new Error('Bitte den festen Jahresbeitrag eintragen oder „automatisch nach Alter“ wählen.');
      const saved = m ? await api('/api/member-update', { ...data, id: m.id }) : await api('/api/member', data);
      el.close();
      if (onSaved) { await refresh(); return onSaved(saved.id); }   // z. B. Klärfall: Mitglied ist angelegt, jetzt gleich zuordnen
      await refresh();
      toast(m ? 'Mitglied gespeichert.' : 'Mitglied angelegt.');
    });
  };
}

/** Beitrag nach Alter live anzeigen (Berechnung auf dem Server wie im Beitragslauf); fester Betrag nur bei Auswahl änderbar. */
function bindDues(form) {
  const box = form.querySelector('[data-dues-auto]');
  let timer = null, seq = 0;
  const show = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const birth = form.birth.value, joined = form.joined.value, mine = ++seq;
      if (!birth || !joined) { mount(box, html`<span class="muted">Geburtsdatum und Eintritt eintragen – dann erscheint der Beitrag.</span>`); return; }
      const q = new URLSearchParams({ birth, joined, year: store.year, left_on: form.left_on.value,
        guest: form.guest.checked ? '1' : '0', dues_free: form.dues_free.checked ? '1' : '0' });   // wie im Beitragslauf
      let rows;
      try { rows = await api(`/api/beitrag-nach-alter?${q}`); }
      catch (e) { if (mine === seq) mount(box, html`<span class="over">${e.message}</span>`); return; }
      if (mine !== seq) return;
      mount(box, html`${rows.map((r, i) => html`${i ? html`<br>` : ''}<strong style="font-weight:500">${r.year}:</strong> ${r.amount === null ? html`<span class="muted">${r.basis}</span>` : html`${eur(r.amount)} <span class="muted">(${r.basis})</span>`}`)}`);
    }, 250);
  };
  // Fester Betrag: Klick ins Feld wählt „fester Betrag“; bei „automatisch“ bleibt der Wert stehen, zählt aber nicht (grau)
  const mode = () => form.dues.classList.toggle('is-off', form.dues_mode.value !== 'fixed');
  form.querySelectorAll('input[name=dues_mode]').forEach((r) => r.addEventListener('change', () => { mode(); if (form.dues_mode.value === 'fixed') form.dues.focus(); }));
  form.dues.addEventListener('focus', () => { form.querySelector('input[name=dues_mode][value=fixed]').checked = true; mode(); });
  ['birth', 'joined', 'left_on', 'guest', 'dues_free'].forEach((n) => form[n].addEventListener('change', show));
  show();
}

/** Mitglied löschen – nur ohne Rechnungen, Beitragszahlungen, Mandate und Erstattungen (das prüft der Server); vorher wird gesichert. */
function deleteMember(m, done) {
  // bekannte Sperrgründe gleich zeigen (der Server prüft trotzdem alles)
  const invoices = store.data.invoices.filter((i) => i.member_id === m.id).length;
  const known = [invoices && `${invoices} ${invoices === 1 ? 'Rechnung' : 'Rechnungen'}`, activeMandate(m.id) && 'ein SEPA-Mandat',
    m.paid && 'Beitragszahlungen'].filter(Boolean);
  const arrears = m.balance < 0 ? -m.balance : 0;
  const el = overlay(`Mitglied löschen · ${sortName(m)}`, known.length ? html`<p class="notice warn">${sortName(m)} hat ${known.join(', ')} – Löschen ist
      deshalb gesperrt (Aufbewahrung der Buchhaltung). Stattdessen im Mitglied den Austritt eintragen.</p>
    <div class="form-actions"><button type="button" class="btn primary" data-close>Verstanden</button></div>`
    : html`<p>${sortName(m)} (${m.number}) wird endgültig gelöscht – mit Beitragsverlauf und Soll-Festlegungen. Vorher sichert das Programm die
      Datenbank (Sicherungen/Vor Änderungen).</p>
    ${arrears ? html`<label class="check notice warn"><input type="checkbox" data-arrears> Offener Beitragsrückstand von ${eur(arrears)} – er entfällt mit
      dem Löschen. Ich bestätige das.</label>` : ''}
    <p class="small muted">Hat das Mitglied Rechnungen, zugeordnete Beitragszahlungen, ein SEPA-Mandat oder eine Auslagenerstattung, ist Löschen gesperrt
      (Aufbewahrung der Buchhaltung) – dann den Austritt eintragen.</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button>
      <button type="button" class="btn danger" data-confirm>Endgültig löschen</button></div>`, { wide: false });
  const confirm = el.querySelector('[data-confirm]');
  if (!confirm) return;
  confirm.onclick = () => safe(async () => {
    if (confirm.disabled) return;
    const box = el.querySelector('[data-arrears]');
    if (box && !box.checked) throw new Error('Bitte den Wegfall des Rückstands bestätigen – oder abbrechen und den Austritt eintragen.');
    confirm.disabled = true;
    try {
      const r = await api('/api/member-delete', { id: m.id, arrears_ok: !!box?.checked });
      el.close();
      done();
      await refresh();
      toast(`Mitglied gelöscht. Sicherung: ${r.backup}`);
    } finally { confirm.disabled = false; }
  });
}

export function account(id, { back = null } = {}) {
  const m = store.data.members.find((x) => x.id === id);
  const invoices = store.data.invoices.filter((i) => i.member_id === id);
  let full = null;   // alle Angaben des Mitglieds (Kontakt, Mitgliederverwaltung) – nicht in der Gesamtliste
  const el = dialog('Mitglied · ' + sortName(m), html`
    ${back ? html`<button type="button" class="link back-link" data-back>← Zurück zur Liste</button>` : ''}
    <div class="panel-head"><p class="muted">${m.number} · ${kvName(m.kv)}${m.ov_id ? ` · OV ${ovLabel(ovById(m.ov_id))}` : ''} · geboren ${date(m.birth)} (${years(m.birth)} Jahre)
      · Eintritt ${date(m.joined)}${m.left_on ? ` · Austritt ${date(m.left_on)}` : ` (${years(m.joined)} Jahre Mitglied)`}${statusBadges(m)}<br>
      ${m.title ? `${m.title} ` : ''}${m.address.split('\n').map((l, i) => html`${i ? html`<br>` : ''}${l}`)}<br>${m.email || 'keine E-Mail-Adresse'} · Jahresbeitrag: ${duesLabel(m)}
      <span data-contact></span></p>
      <span style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn" href="/api/member-account.pdf?id=${id}" target="_blank" rel="noopener">${icon('file', 16)} Beitragskonto (PDF)</a>
        <button type="button" class="btn" data-edit>${icon('edit', 16)} Bearbeiten</button>
        <button type="button" class="btn ghost" data-delete>Löschen</button></span></div>
    <div data-account><p class="small muted">Wird geladen …</p></div>
    ${mandateSection(m)}
    <h3>Rechnungen</h3>
    ${invoices.length ? table(['Jahr', 'Rechnung', { label: 'Forderung', className: 'num' }, { label: 'Bezahlt', className: 'num' }, 'Status'],
      invoices.map((i) => html`<tr><td>${i.year || date(i.issued)}</td><td>${i.number}</td><td class="num">${eur(i.total)}</td><td class="num">${eur(i.paid)}</td><td>${invoiceBadge(i)}</td></tr>`))
      : empty('Keine Rechnungen', 'Beitragslauf unter „Rechnungen“ starten.')}`, { wide: true });
  el.querySelector('[data-edit]').onclick = () => safe(async () => memberDialog(full || (await api('/api/member-account?id=' + id)).member));
  el.querySelector('[data-delete]').onclick = () => deleteMember(m, () => el.close());
  el.querySelector('[data-back]')?.addEventListener('click', back);
  bindMandateSection(el, m, () => account(id, { back }));
  const load = () => safe(async () => {
    const a = await api('/api/member-account?id=' + id);
    full = a.member;
    const contact = [full.phone && `Tel. ${full.phone}`, full.phone_work && `Tel. dienstl. ${full.phone_work}`, full.mobile && `Handy ${full.mobile}`,
      full.mobile_work && `Handy dienstl. ${full.mobile_work}`, full.email_work && `E-Mail dienstl. ${full.email_work}`].filter(Boolean).join(' · ');
    const known = full.external_balance !== null && full.external_balance !== undefined;
    const line = el.querySelector('[data-contact]');
    if (line) mount(line, html`${contact ? html`<br>${contact}` : ''}${known ? html`<br>Kontostand laut Mitgliederverwaltung: ${eur(full.external_balance)}` : ''}${
      a.sepa_refs.length ? html`<br>Gemerkte Mandatsreferenzen (SEPA-Datei): ${a.sepa_refs.map((r, n) => html`${n ? ', ' : ''}${r.mandate}
        <button type="button" class="link" data-forget="${n}" title="Nicht mehr automatisch diesem Mitglied zuordnen">entfernen</button>`)}` : ''}`);
    el.querySelectorAll('[data-forget]').forEach((b) => (b.onclick = () => safe(async () => {
      const r = a.sepa_refs[Number(b.dataset.forget)];
      await api('/api/sepa-mandat-vergessen', { creditor: r.creditor, mandate: r.mandate });
      toast('Gemerkte Mandatsreferenz entfernt.');
      load();
    })));
    const box = el.querySelector('[data-account]');
    mount(box, html`<h3>Beitragskonto</h3>
      <p class="${a.balance < 0 ? 'notice warn' : 'notice'}">${a.balance < 0 ? `Rückstand ${eur(-a.balance)}` : a.balance > 0 ? `Guthaben ${eur(a.balance)}` : 'Ausgeglichen'}
        – fällige Beiträge seit ${a.first} minus Zahlungen.${a.issues.length ? ` Zu klären: ${a.issues.join('; ')}` : ''}</p>
      ${a.years.length ? table(['Jahr', { label: 'Beitrag (Soll)', className: 'num' }, 'Grundlage', { label: 'Gezahlt für das Jahr', className: 'num' },
        { label: 'Offen', className: 'num' }, { label: 'Stand 31.12.', className: 'num' }, ''], a.years.map((r) => html`<tr>
        <td>${r.year}</td><td class="num">${r.soll === null ? badge('klären', 'warn') : eur(r.soll)}</td>
        <td class="small">${r.issue || r.basis}${r.soll && !r.due_now ? html`<span class="sub">fällig ${date(r.due)}</span>` : ''}</td>
        <td class="num">${eur(r.paid)}</td><td class="num">${r.open === null ? '—' : r.open ? html`<span class="over">${eur(r.open)}</span>` : eur(0)}</td>
        <td class="num">${balanceCell(r.end_balance)}</td>
        <td class="nowrap">${r.source === 'invoice' && r.soll !== null ? html`<span class="small muted">laut Rechnung</span>`
          : html`<button type="button" class="btn small" data-target="${r.year}">${r.source === 'set' ? 'Soll ändern' : 'Soll festlegen'}</button>`}</td></tr>`))
        : html`<p class="small muted">Kein Beitragsjahr seit Datenbeginn.</p>`}
      <p class="small muted">„Offen“: Zahlungen decken die ältesten fälligen Beiträge zuerst – auch eine Nachzahlung, die in einem späteren Haushaltsjahr gebucht ist.</p>
      <h3>Buchungen im Beitragskonto</h3>
      ${a.postings.length ? table(['Datum', 'Buchung', { label: 'Beitrag', className: 'num' }, { label: 'Zahlung', className: 'num' }, { label: 'Saldo', className: 'num' }],
        a.postings.map((x) => html`<tr class="${x.counts ? '' : 'muted'}"><td class="nowrap">${date(x.day)}</td>
          <td>${x.text}${x.journal_id ? html`<span class="sub">Buchung #${x.journal_id}</span>` : ''}${x.counts ? '' : html`<span class="sub">noch nicht fällig</span>`}</td>
          <td class="num">${x.soll ? eur(x.soll) : ''}</td><td class="num">${x.ist ? eur(x.ist) : ''}</td><td class="num">${balanceCell(x.balance)}</td></tr>`))
        : html`<p class="small muted">Keine Buchungen.</p>`}`);
    box.querySelectorAll('[data-target]').forEach((b) => (b.onclick = () => targetDialog(m, a.years.find((r) => r.year === Number(b.dataset.target)), load)));
  });
  load();
}

/** Soll eines Jahres von Hand festlegen (z. B. Austrittsjahr, Befreiung, Erlass) – mit Grund, im Protokoll. */
function targetDialog(m, row, done) {
  const el = overlay(`Beitrag ${row.year} festlegen · ${sortName(m)}`, html`<form id="target-form" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Bisher: ${row.soll === null ? 'nicht berechenbar' : eur(row.soll)} (${row.issue || row.basis}). Ein festgelegter Betrag geht Beitragsrechnung und Staffel vor.</p>
    <div class="formgrid"><label class="field">Beitrag ${row.year} in Euro<input name="amount" inputmode="decimal" required value="${row.source === 'set' ? toInput(row.soll) : ''}" placeholder="z. B. 0 oder 30"></label>
      <label class="field span">Grund<input name="reason" required maxlength="200" autocomplete="off" value="${row.reason || ''}" placeholder="z. B. Austritt 31.08. – voller Jahresbeitrag, Befreiung, Erlass"></label></div>
    <div class="form-actions">${row.source === 'set' ? html`<button type="button" class="btn ghost" data-remove>Festlegung entfernen</button>` : ''}
      <button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`, { wide: false });
  el.querySelector('[data-remove]')?.addEventListener('click', () => safe(async () => {
    await api('/api/member-target', { member_id: m.id, year: row.year, remove: true });
    el.close();
    await refresh();
    toast('Festlegung entfernt – es gilt wieder Rechnung bzw. Staffel.');
    done();
  }));
  el.querySelector('#target-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const data = Object.fromEntries(new FormData(e.target));
      await api('/api/member-target', { member_id: m.id, year: row.year, ...data });
      el.close();
      await refresh();
      toast(`Beitrag ${row.year} festgelegt.`);
      done();
    });
  };
}

// ---------------------------------------------------------------- Import einer Mitgliederliste

const ACTION = { new: ['Neu', 'ok'], update: ['Änderung', 'navy'], same: ['Unverändert', ''], error: ['Fehler', 'danger'] };

export function importMembersDialog() {
  const el = dialog('Mitgliederliste importieren', html`
    <div class="filebox"><label class="field">Excel (.xlsx, .xls) oder CSV
      <input id="members-file" type="file" accept=".xlsx,.xls,.csv,.txt"></label>
      <p class="small muted">Der Export der CDU-Mitgliederverwaltung (z. B. „ultimate alles …“ oder „finanzen_mitglieder_gesamt.csv“) wird mit allen Spalten
        erkannt – auch kennwortgeschützt (das Kennwort wird abgefragt und nicht gespeichert). Kreisverband über die Nummer (LVKV_Nummer, siehe
        Einstellungen → Gliederung) oder den Namen; Ortsverband aus den Stammdaten, unbekannte Ortsverbände legt die Übernahme an.
        Sonst Spalten wie Mitgliedsnummer, Vorname, Nachname (oder Name), Straße, Hausnummer, PLZ, Ort,
        Geburtsdatum, Eintritt, Kreisverband (Name oder Kennziffer), E-Mail und Jahresbeitrag. Der Jahresbeitrag aus der Datei gilt als Beitrags-Soll
        (Vorrang vor der Staffel), „Beitragsfrei“ und „Gastmitglied“ (beitragsfrei) werden übernommen. Vorhandene Mitglieder werden über die
        Mitgliedsnummer erkannt; fehlt eine Spalte, bleibt der bisherige Wert, eine leere Zelle in einer vorhandenen Spalte leert ihn. Vor der Übernahme wird automatisch gesichert. Die Datei
        selbst wird nicht gespeichert.</p></div>
    <div id="members-preview"></div>`, { wide: true });
  el.querySelector('#members-file').onchange = (e) => safe(async () => {
    const file = e.target.files[0];
    if (!file) return;
    const box = el.querySelector('#members-preview');
    mount(box, '');
    const p = await uploadProtected('/api/members-preview', file, box);
    if (!p) return;   // inzwischen andere Datei gewählt
    const order = { error: 0, update: 1, new: 2, same: 3 };
    const rows = [...p.rows].sort((a, b) => order[a.action] - order[b.action] || a.row_no - b.row_no);
    mount(el.querySelector('#members-preview'), html`<h3>Vorschau „${p.filename}“ · ${p.rows.length} Zeilen</h3>
      <p class="muted">${p.counts.new} neu · ${p.counts.update} geändert · ${p.counts.same} unverändert · ${p.counts.error} mit Fehler.
        ${p.guests ? `Davon ${p.guests} Gastmitglieder (beitragsfrei). ` : ''}Erkannte Spalten: ${p.columns.join(', ')}. Zeilen mit Hinweis sind nicht vorausgewählt – erst prüfen.</p>
      ${p.new_ovs.length ? html`<p class="notice">${p.new_ovs.length} ${p.new_ovs.length === 1 ? 'Ortsverband ist' : 'Ortsverbände sind'} noch nicht in den Stammdaten und
        ${p.new_ovs.length === 1 ? 'wird' : 'werden'} mit der Übernahme angelegt: ${p.new_ovs.map((o, i) => html`${i ? ' · ' : ''}${kvName(o.kv)}: ${ovLabel(o)}`)}</p>` : ''}
      ${p.missing.length ? html`<details class="notice warn"><summary>${p.missing.length} ${p.missing.length === 1 ? 'Mitglied steht' : 'Mitglieder stehen'} im Programm, aber nicht in
        der Datei – Austritt prüfen und im Mitglied eintragen (der Import ändert sie nicht).</summary>
        <p class="small">${p.missing.map((m, i) => html`${i ? ' · ' : ''}${m.name} (${m.number}, ${kvName(m.kv)})`)}</p></details>` : ''}
      ${table(['', 'Zeile', 'Mitglied', 'Kreis-/Ortsverband', 'Ort', 'Prüfung'], rows.map((r) => html`<tr>
        <td><input class="members-check" type="checkbox" value="${r.row_no}" ${['new', 'update'].includes(r.action) ? (r.warning ? '' : 'checked') : 'disabled'} aria-label="Zeile ${r.row_no} übernehmen"></td>
        <td>${r.row_no}</td><td><strong style="font-weight:500">${r.name || '—'}</strong>${r.guest ? html` ${badge('Gast', 'navy')}` : ''}${r.dues_free ? html` ${badge('beitragsfrei', '')}` : ''}<span class="sub">${r.number}</span></td>
        <td>${r.kv ? kvName(r.kv) : '—'}${r.ov ? html`<span class="sub">${r.ov}</span>` : ''}</td><td>${r.city || ''}</td>
        <td>${badge(ACTION[r.action][0], ACTION[r.action][1])}${r.error ? html`<span class="sub over">${r.error}</span>` : ''}
          ${r.changes.length && r.action === 'update' ? html`<span class="sub">${r.changes.join(', ')}</span>` : ''}${r.warning ? html`<span class="sub over">${r.warning}</span>` : ''}</td></tr>`))}
      <div class="form-actions"><button type="button" class="btn primary" id="members-confirm" ${p.counts.new + p.counts.update ? '' : 'disabled'}>Auswahl übernehmen</button></div>`);
    const confirm = el.querySelector('#members-confirm');
    confirm.onclick = () => safe(async () => {
      if (confirm.disabled) return;
      confirm.disabled = true;
      try {
        const selected = [...el.querySelectorAll('.members-check:checked')].map((c) => Number(c.value));
        const result = await api('/api/members-import', { token: p.token, selected });
        el.close();
        await refresh();
        toast(`${result.new} Mitglieder angelegt, ${result.updated} aktualisiert${result.ov_created ? `, ${result.ov_created} Ortsverbände angelegt` : ''}. Sicherung: ${result.backup}`);
      } finally { confirm.disabled = false; }
    });
  });
}

// ---------------------------------------------------------------- Seite

/** Stand des Beitrags im gewählten Haushaltsjahr – nur als Kennzeichen; Beträge und Konten stehen in der Buchhaltung. */
function duesState(m) {
  if (m.dues_soll === null) return [badge('klären', 'warn'), 'Beitrag nicht berechenbar'];
  if (m.dues_free || m.guest || !m.dues_soll) return [badge('beitragsfrei', ''), m.guest ? 'Gastmitglied' : (m.dues_basis || '')];
  if ((m.paid || 0) >= m.dues_soll) return [badge('bezahlt', 'ok'), m.balance < 0 ? 'Rückstand aus Vorjahren' : ''];
  return [badge('offen', 'warn'), m.balance < 0 && (m.paid || 0) > 0 ? 'teilweise bezahlt' : ''];
}

export function members(main) {
  if (ovFilter && ovFilter !== 'none' && !ovsOf(kvFilter, ovFilter).some((o) => String(o.id) === ovFilter)) ovFilter = '';
  const today = new Date().toISOString().slice(0, 10), y = String(store.year);
  const all = store.data.members;
  const isActive = (m) => !m.left_on || m.left_on > today;
  const active = all.filter(isActive);
  const list = matchMembers(all, search, kvFilter).filter(inState).filter((m) => memberFilter === 'all' || (memberFilter === 'left') !== isActive(m))
    .filter((m) => !ovFilter || (ovFilter === 'none' ? !m.ov_id : String(m.ov_id) === ovFilter));
  const ovChoices = kvFilter ? (store.data.ovs || []).filter((o) => o.kv === kvFilter && (o.members || o.active)) : [];
  const withoutMail = active.filter((m) => !m.email).length;
  const legacy = active.filter((m) => !m.zip).length;
  const joined = all.filter((m) => (m.joined || '').startsWith(y)).length, left = all.filter((m) => (m.left_on || '').startsWith(y)).length;
  const turning = active.filter((m) => m.birth && Number(m.birth.slice(0, 4)) === store.year - 35).length;   // Altersgrenze Satzung § 2
  const kvs = store.data.kv.filter((k) => all.some((m) => m.kv === k.code));
  const cases = store.data.cases?.active || 0;
  mount(main, html`<div class="page wide">
    ${pageHead('Mitgliederverwaltung', 'Mitglieder', html`
      <button type="button" class="btn" data-import>${icon('upload', 16)} Liste importieren</button>
      <button type="button" class="btn primary" data-new>${icon('plus', 16)} Mitglied anlegen</button>`)}
    <section class="cards" aria-label="Mitgliederbestand">
      <div class="card"><span class="label">Aktive Mitglieder</span><span class="value">${active.length}</span>
        <span class="note">${kvs.length} ${kvs.length === 1 ? 'Kreisverband' : 'Kreisverbände'}${all.length > active.length ? ` · ${all.length - active.length} ausgetreten` : ''}</span></div>
      <div class="card"><span class="label">Eintritte ${store.year}</span><span class="value">${joined}</span><span class="note">Austritte ${store.year}: ${left}</span></div>
      <div class="card"><span class="label">Werden ${store.year} 35 Jahre</span><span class="value">${turning}</span><span class="note">Altersgrenze (Satzung § 2)</span></div>
      <div class="card"><span class="label">Daten vervollständigen</span><span class="value ${withoutMail + legacy ? 'over' : ''}">${withoutMail + legacy}</span>
        <span class="note">${withoutMail} ohne E-Mail${legacy ? ` · ${legacy} Anschrift unvollständig` : ''}</span></div>
    </section>
    ${cases ? html`<p class="notice warn">${cases} ${cases === 1 ? 'Beitragszahlung wartet' : 'Beitragszahlungen warten'} auf Klärung, zu wem sie gehören:
      <button type="button" class="link" data-go-cases>Klärfälle öffnen</button></p>` : ''}
    <div class="toolbar"><label class="search grow">${icon('search', 16)}<span class="sr-only">Mitglieder suchen</span><input id="member-search" type="search" autocomplete="off"
        placeholder="Name, Straße, PLZ, Ort, Mitgliedsnummer oder E-Mail" value="${search}"></label>
      <label class="field inline">Kreisverband<select id="member-kv"><option value="">alle</option>${kvs.map((k) => html`<option value="${k.code}" ${k.code === kvFilter ? 'selected' : ''}>${k.name}</option>`)}</select></label>
      ${kvFilter ? html`<label class="field inline">Ortsverband<select id="member-ov"><option value="">alle</option><option value="none" ${ovFilter === 'none' ? 'selected' : ''}>ohne Ortsverband</option>
        ${ovChoices.map((o) => html`<option value="${o.id}" ${String(o.id) === ovFilter ? 'selected' : ''}>${ovLabel(o)} (${o.members})</option>`)}</select></label>` : ''}
      <label class="field inline">Status<select id="member-active">${[['active', 'aktiv'], ['left', 'ausgetreten'], ['all', 'alle']].map(([k, v]) => html`<option value="${k}" ${k === memberFilter ? 'selected' : ''}>${v}</option>`)}</select></label>
      <label class="field inline">Beitrag<select id="member-state"><option value="">alle</option>${Object.entries(STATES).map(([k, v]) => html`<option value="${k}" ${k === stateFilter ? 'selected' : ''}>${v}</option>`)}</select></label></div>
    <section class="panel" style="padding:6px 12px">${all.length ? table(['Mitglied', 'Anschrift', 'Kreis- und Ortsverband', 'Kontakt', { label: 'Alter', className: 'num' }, `Beitrag ${store.year}`],
      list.slice(0, LIMIT).map((m) => {
        const [state, hint] = duesState(m), age = years(m.birth);
        return html`<tr class="clickable" data-member="${m.id}" tabindex="0"><td><strong style="font-weight:600">${sortName(m)}</strong>${statusBadges(m)}<span class="sub">${m.number}${
            m.left_on ? ` · ${m.left_on > today ? 'Austritt zum' : 'ausgetreten'} ${date(m.left_on)}` : m.joined ? ` · seit ${m.joined.slice(0, 4)}` : ''}</span></td>
          <td class="small">${m.zip ? addressLine(m) : html`${addressLine(m)} ${badge('Anschrift prüfen', 'warn')}`}</td>
          <td>${kvName(m.kv)}${m.ov_id ? html`<span class="sub">${ovLabel(ovById(m.ov_id))}</span>` : ''}</td><td class="small">${m.email || badge('E-Mail fehlt', 'warn')}</td>
          <td class="num">${age ?? '—'}${age !== null && m.birth && Number(m.birth.slice(0, 4)) === store.year - 35 ? html`<span class="sub">wird 35</span>` : ''}</td>
          <td title="${m.dues_basis || ''}">${state}${activeMandate(m.id) ? html` ${badge('Lastschrift', 'navy')}` : ''}${hint ? html`<span class="sub">${hint}</span>` : ''}</td></tr>`;
      }))
      : empty('Noch keine Mitglieder', 'Einzeln anlegen oder eine Mitgliederliste (Excel/CSV) importieren.')}
      ${all.length && !list.length ? html`<p class="muted" style="padding:12px">Kein Mitglied passt zur Auswahl.</p>` : ''}</section>
    <p class="small muted">${list.length > LIMIT ? `Die ersten ${LIMIT} von ${list.length} Treffern – Suche oder Kreisverband eingrenzen.` : `${list.length} von ${all.length} angezeigt`} · sortiert nach Nachname.
      Hier steht nur der Stand des Beitrags ${store.year}. Beträge, Beitragseingänge und die OP-Listen der Kreisverbände stehen in der Buchhaltung; das Beitragskonto öffnet sich mit dem Mitglied.</p>
  </div>`);
  main.querySelector('[data-new]').onclick = () => memberDialog();
  main.querySelector('[data-import]').onclick = importMembersDialog;
  main.querySelector('[data-go-cases]')?.addEventListener('click', () => go('cases'));
  main.querySelector('#member-kv').onchange = (e) => { kvFilter = e.target.value; ovFilter = ''; members(main); };
  main.querySelector('#member-ov')?.addEventListener('change', (e) => { ovFilter = e.target.value; members(main); });
  main.querySelector('#member-state').onchange = (e) => { stateFilter = e.target.value; members(main); };
  main.querySelector('#member-active').onchange = (e) => { memberFilter = e.target.value; members(main); };
  const input = main.querySelector('#member-search');
  input.oninput = () => {
    search = input.value;
    const pos = input.selectionStart;
    members(main);
    const again = main.querySelector('#member-search');
    again.focus();
    again.setSelectionRange(pos, pos);
  };
  main.querySelectorAll('[data-member]').forEach((row) => {
    row.onclick = () => account(Number(row.dataset.member));
    row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); account(Number(row.dataset.member)); } };
  });
}
