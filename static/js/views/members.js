// Mitglieder (Mitgliederverwaltung): Liste mit Suche (Name, Anschrift, Nummer, E-Mail), Kreis- und Ortsverband, Status, Anlegen/Bearbeiten
// mit allen Angaben der Mitgliederverwaltung (Kreisverband als Auswahl, Ortsverband nur aus dem gewählten Kreisverband – gliederung.js) und
// Import einer Mitgliederliste (Excel/CSV, auch kennwortgeschützt). Ab 0.37 Status (wahlberechtigt / nicht wahlberechtigt / ausgetreten,
// vom Server berechnet) und „Austritt eintragen“ im geöffneten Mitglied. Ab 0.31 zeigt die Liste nur noch den Stand des Beitrags; Beträge,
// Beitragseingänge (views/dues.js) und OP-Listen (views/open-items.js) gehören zur Buchhaltung, Klärfälle zu views/klaerfaelle.js.
// Das geöffnete Mitglied zeigt weiter sein Beitragskonto (mitgliederkonto.py): Jahre, Buchungen, Soll von Hand festlegen.
import { api, download } from '../api.js';
import { html, mount, raw } from '../html.js';
import { eur, signed, date, dateTime, weekdayDate, toInput, todayIso } from '../format.js';
import { store, refresh, kvName, go, can } from '../store.js';
import { pageHead, icon, table, empty, dialog, overlay, safe, toast, badge, uploadProtected } from '../ui.js';
import { kvOvFields, bindKvOv, setKvOv, ovById, ovLabel, ovsOf } from '../gliederung.js';
import { invoiceBadge } from './invoices.js';
import { mandateSection, bindMandateSection, activeMandate } from './sepa.js';
import { matchMembers, sortName, addressLine, pickMembers } from '../member-picker.js';
import { listen, isActive, fehlt, anschriftVollstaendig, gespeichert, speichern } from '../mitglieder-listen.js';
import { doppelteDialog } from './doppelte.js';
import { ovAbgleichDialog } from './ov-abgleich.js';

let search = '';
let kvFilter = '';
let ovFilter = '';   // '' = alle, 'none' = ohne Ortsverband, sonst id
let stateFilter = '';
const LIMIT = 500;   // Zeilen in der Liste (Suche grenzt ein)
const STATES = { arrears: 'mit Rückstand', credit: 'mit Guthaben', issue: 'Beitrag zu klären', guest: 'Gastmitglieder', free: 'beitragsfrei' };
const inState = (m) => !stateFilter || ({ arrears: m.balance < 0, credit: m.balance > 0, issue: m.dues_issues.length > 0, guest: !!m.guest,
  free: !!m.dues_free })[stateFilter];
/** Kennzeichen Gastmitglied / beitragsfrei (Mitgliederverwaltung). */
const statusBadges = (m) => html`${m.guest ? html` ${badge('Gast', 'navy')}` : ''}${m.dues_free ? html` ${badge('beitragsfrei', '')}` : ''}${
  m.no_mail ? html` ${badge('Postausschluss', 'warn')}` : ''}`;
/** Mitgliederstatus (0.37): der Server rechnet (wahlrecht.py) – wahlberechtigt / nicht wahlberechtigt / ausgetreten mit Gründen ohne Beträge.
 * Vor „Wahlberechtigung auswerten ab“ liefert er keinen Status; dann wie bisher nur aktiv/ausgetreten. */
const WAHL = { wahlberechtigt: ['wahlberechtigt', 'ok'], nicht: ['nicht wahlberechtigt', 'warn'], ausgetreten: ['ausgetreten', ''] };
export const wahlBadge = (status) => badge(...WAHL[status]);
const memberStatus = (m) => (m.wahl ? wahlBadge(m.wahl.status) : isActive(m) ? badge('aktiv', 'ok') : badge('ausgetreten', ''));
/** Volle Jahre zwischen zwei Daten (JJJJ-MM-TT) – Alter bzw. Jubiläumsjahre wie in der Mitgliederverwaltung. */
const years = (from, to = new Date().toISOString().slice(0, 10)) => (from ? Number(to.slice(0, 4)) - Number(from.slice(0, 4)) - (to.slice(5) < from.slice(5) ? 1 : 0) : null);
const text = (name, label, v, attrs = {}) => html`<label class="field">${label}<input name="${name}" autocomplete="off" maxlength="${attrs.max || 200}"
  ${attrs.type ? html`type="${attrs.type}"` : ''} ${attrs.required ? 'required' : ''} placeholder="${attrs.hint || ''}" value="${v[name] || ''}">${
  attrs.note ? html`<span class="small muted">${attrs.note}</span>` : ''}</label>`;
const NOAUTO = 'autocapitalize="off" autocorrect="off" spellcheck="false"';

/** Saldo des Beitragskontos: − Rückstand (rot), + Guthaben. */
export const balanceCell = (cents) => (cents < 0 ? html`<span class="over">${signed(cents)}</span>` : cents > 0 ? signed(cents) : html`<span class="muted">${eur(0)}</span>`);

/** Anschrift im Mitgliedsdialog prüfen (ab 0.32): mit den amtlichen Hamburger Adressdaten auf diesem Rechner (adressen.py) – gibt es die
 * Straße, passt die PLZ? Nur ein Hinweis mit Vorschlägen; gespeichert werden kann immer (Mitglieder außerhalb Hamburgs, neue Straßen).
 * Ab 0.52 dazu der Ortsverband nach der Adresse (Gebiete unter Einstellungen → Gliederung): bei einem neuen Mitglied vorausgewählt, solange
 * Kreis- und Ortsverband nicht von Hand gewählt sind; sonst als Knopf zum Übernehmen. „Ortsverband fest“ schaltet den Vorschlag ab. */
function bindAddressCheck(form, neu = false) {
  const box = form.querySelector('[data-address-check]');
  const ovBox = form.querySelector('[data-ov-vorschlag]');
  let round = 0;
  let manuell = false;   // Kreis- oder Ortsverband von Hand gewählt: nichts mehr vorauswählen
  let auto = null;       // vom Programm vorausgewählter Ortsverband – solange er gewählt ist, sagt der Hinweis das
  let letzte = null;
  for (const name of ['kv', 'ov_id']) form[name]?.addEventListener('change', (e) => { if (e.isTrusted) manuell = true; });
  const zeigeOv = (r) => {
    letzte = r;
    if (!ovBox) return;
    const ovs = (r?.ovs || []).map(ovById).filter(Boolean);
    if (!r?.kv || !r.stadtteil || form.ov_fest?.checked || ['LV', '5800'].includes(form.kv.value)) return mount(ovBox, '');
    if (!ovs.length) return mount(ovBox, html`<span class="muted">Für ${r.stadtteil} ist noch kein Ortsverband hinterlegt (Einstellungen → Gliederung).</span>`);
    const gewaehlt = Number(form.ov_id.value);
    if (neu && !manuell && ovs.length === 1 && (ovs[0].id !== gewaehlt || auto === gewaehlt) && setKvOv(form, ovs[0].kv, ovs[0].id)) {
      auto = ovs[0].id;
      return mount(ovBox, html`<span class="ok">${icon('check', 14)} Nach der Adresse (${r.stadtteil}) vorausgewählt: ${ovLabel(ovs[0])} – änderbar.</span>`);
    }
    if (form.kv.value === r.kv && ovs.some((o) => o.id === gewaehlt)) {
      return mount(ovBox, html`<span class="ok">${icon('check', 14)} Ortsverband passt zur Adresse (${r.stadtteil}).</span>`);
    }
    mount(ovBox, html`<span class="warn">Nach der Adresse (${r.stadtteil}) gehört das Mitglied zu:</span>
      ${ovs.map((o) => html`<button type="button" class="btn small" data-ov-uebernehmen="${o.id}">${ovLabel(o)}${o.kv !== form.kv.value ? ` (${kvName(o.kv)})` : ''} übernehmen</button> `)}`);
    ovBox.querySelectorAll('[data-ov-uebernehmen]').forEach((b) => (b.onclick = () => {
      const o = ovById(b.dataset.ovUebernehmen);
      manuell = true;
      setKvOv(form, o.kv, o.id);
      zeigeOv(r);
    }));
  };
  form.ov_id?.addEventListener('change', () => zeigeOv(letzte));
  form.ov_fest?.addEventListener('change', () => zeigeOv(letzte));
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
    if (!street || abroad) { zeigeOv(null); return mount(box, ''); }
    let r;
    try { r = await api('/api/adresse-pruefen', { street, zip, city }); } catch { return mount(box, ''); }   // die Prüfung darf das Speichern nie stören
    if (mine !== round || !box.isConnected) return;
    zeigeOv(r);
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

/** „Geworben von“ (0.52): Name und Nummer des werbenden Mitglieds. */
const werberText = (id) => {
  if (!id) return 'kein Mitglied gewählt';
  const w = store.data.members.find((x) => x.id === Number(id));
  return w ? `${w.name} (${w.number})` : 'Mitglied nicht in der Liste';
};

/** Auswahl „Geworben von“ über die Mitgliedersuche (member-picker.js); gespeichert wird nur die Kennung. */
function bindWerber(form, m) {
  const input = form.geworben_von;
  const name = form.querySelector('[data-werber-name]');
  const pick = form.querySelector('[data-werber-pick]');
  const clear = form.querySelector('[data-werber-clear]');
  const text = form.geworben_text;
  const set = (id) => {
    input.value = id ? String(id) : '';
    text.hidden = !!id;   // entweder ein Mitglied oder ein Text (der Server leert den Text, sobald ein Mitglied gewählt ist)
    if (id) text.value = '';
    name.textContent = werberText(id);
    pick.textContent = id ? 'Ändern' : 'Auswählen';
    clear.hidden = !id;
  };
  pick.onclick = () => pickMembers({ title: 'Geworben von', selected: input.value ? [{ member_id: input.value }] : [], onDone: (list) => {
    const id = list[0]?.member_id;
    if (m && Number(id) === m.id) return toast('Ein Mitglied kann sich nicht selbst geworben haben.', true);
    return set(id);
  } });
  clear.onclick = () => set(null);
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
      <div class="span address-check small" data-ov-vorschlag aria-live="polite"></div>
      <label class="check span"><input type="checkbox" name="ov_fest" value="1" ${v.ov_fest ? 'checked' : ''}> Ortsverband fest – nicht nach der Adresse
        vorschlagen (Mitgliedschaft auf Wunsch in einem anderen Gebiet)</label>
      <label class="field">Eintritt (Verein)<input name="joined" type="date" required value="${v.joined || ''}"></label>
      <label class="field">Eintritt Kreisverband<input name="kv_joined" type="date" value="${v.kv_joined || ''}"></label>
      <label class="field">Austritt<input name="left_on" type="date" value="${v.left_on || ''}"><span class="small muted">optional</span></label>
      ${text('clubs', 'Vereinigungen (Vereine)', v, { hint: 'z. B. JU,CDU' })}
      ${text('club', 'Verein', v, { hint: 'z. B. JU' })}
      <div class="span member-werber"><span>Geworben von</span>
        <input type="hidden" name="geworben_von" value="${v.geworben_von || ''}">
        <span class="member-werber-wahl"><span data-werber-name>${werberText(v.geworben_von)}</span>
          <button type="button" class="btn small" data-werber-pick>${v.geworben_von ? 'Ändern' : 'Auswählen'}</button>
          <button type="button" class="btn small ghost" data-werber-clear ${v.geworben_von ? '' : 'hidden'}>Entfernen</button></span>
        <input name="geworben_text" maxlength="200" autocomplete="off" value="${v.geworben_text || ''}" ${v.geworben_von ? 'hidden' : ''}
          placeholder="oder ohne Mitglied als Text, z. B. Infostand Uni" aria-label="Geworben von (kein Mitglied)"></div>
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
  bindAddressCheck(form, !m);
  bindWerber(form, m);
  el.querySelector('form [data-close]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const flags = Object.fromEntries(['dues_free', 'guest', 'no_mail', 'ov_fest'].map((k) => [k, form[k].checked ? '1' : '0']));
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

const YEAR = { klaeren: ['klären', 'warn'], frei: ['beitragsfrei', ''], spaeter: ['noch nicht fällig', ''], offen: ['offen', 'warn'], bezahlt: ['bezahlt', 'ok'] };
const initials = (m) => `${(m.first_name || '').slice(0, 1)}${(m.last_name || m.name || '').slice(0, 1)}`.toUpperCase();
const telLink = (n) => `tel:${String(n).replace(/[^+0-9]/g, '')}`;

/** Hinweis zur Wahlberechtigung: ausgewertet (ab „Wahlberechtigung auswerten ab“) die Gründe des Servers, vorher eine Vorschau aus den
 * offenen Jahren im Fenster des nächsten Beitragsstichtags (§ 37 Abs. 1 Nr. 1) – ohne Beträge. */
function wahlHint(m, a) {
  const day = store.data.wahl?.beitrag;
  if (!day || !isActive(m) || m.guest) return '';
  if (m.wahl) return m.wahl.status === 'nicht' ? html`<p class="notice warn" data-wahl-hint><strong>Nicht wahlberechtigt:</strong> ${m.wahl.gruende.join(' · ')}.
    Was bis zum Beitragsstichtag ${weekdayDate(day)} eingeht, zählt (Satzung § 37 Abs. 1 Nr. 1).</p>` : '';
  const y = Number(day.slice(0, 4)), open = a.jahre.filter((j) => j.stand === 'offen' && j.year >= y - 2 && j.year <= y).map((j) => j.year);
  return open.length ? html`<p class="notice warn" data-wahl-hint><strong>Für die Wahlberechtigung fehlt der Beitrag ${open.join(', ')}.</strong>
    Er muss bis zum Beitragsstichtag ${weekdayDate(day)} eingegangen sein (Satzung § 37 Abs. 1 Nr. 1).</p>` : '';
}

/** Mitgliederakte (0.43): Kopf mit Status und Aktionen, Reiter Überblick / Alle Angaben / Beitragskonto (nur mit Buchhaltung) / Verlauf.
 * Ohne Buchhaltung: Stand je Beitragsjahr ohne Beträge (Server: /api/member-account). */
export function account(id, { back = null, tab = null } = {}) {
  const m = store.data.members.find((x) => x.id === id);
  const money = can('buchhaltung'), write = can('mitglieder', 'schreiben');
  tab = tab || (money ? 'konto' : 'ueberblick');   // Schatzmeister zuerst das Beitragskonto, Mitgliederverwaltung den Überblick
  const el = dialog('Mitglied · ' + sortName(m), html`${back ? html`<button type="button" class="link back-link" data-back>← Zurück zur Liste</button>` : ''}
    <div data-akte><p class="small muted">Wird geladen …</p></div>`, { wide: true });
  el.querySelector('[data-back]')?.addEventListener('click', back);
  const reopen = (t = tab) => account(id, { back, tab: t });
  safe(async () => draw(await api('/api/member-account?id=' + id)));

  function draw(a) {
    const f = a.member;
    const tabs = [['ueberblick', 'Überblick'], ['angaben', 'Alle Angaben'], ...(money ? [['konto', 'Beitragskonto']] : []), ['verlauf', 'Verlauf']];
    if (!tabs.some(([k]) => k === tab)) tab = 'ueberblick';
    const phone = f.mobile || f.phone || f.mobile_work || f.phone_work;
    const [dl, dk] = DUES[m.dues_state] || DUES.frei;
    const box = el.querySelector('[data-akte]');
    mount(box, html`<div class="akte">
      <header class="akte-head panel-head"><span class="akte-avatar" aria-hidden="true">${initials(m)}</span>
        <div class="akte-who"><p class="muted">${m.number} · ${kvName(m.kv)}${m.ov_id ? ` · OV ${ovLabel(ovById(m.ov_id))}` : ''}${m.birth ? ` · ${years(m.birth)} Jahre` : ''}${
          m.left_on ? ` · Austritt ${isActive(m) ? 'zum ' : ''}${date(m.left_on)}${f.left_reason ? ` (${f.left_reason})` : ''}` : ''}</p>
          <p class="akte-chips" data-status>${memberStatus(m)}${m.joined ? html` <span class="small muted">seit ${m.joined.slice(0, 4)}</span>` : ''}
            ${badge(`Beitrag ${store.year}: ${dl}`, dk)}${statusBadges(m)}</p></div>
        <div class="akte-actions">
          ${m.email ? html`<a class="btn" href="mailto:${m.email}">${icon('mail', 16)} E-Mail</a>` : ''}
          ${phone ? html`<a class="btn" href="${telLink(phone)}">Anrufen</a>` : ''}
          ${write ? html`<button type="button" class="btn" data-edit>${icon('edit', 16)} Bearbeiten</button>
            <button type="button" class="btn" data-leave>${m.left_on ? 'Austritt ändern' : 'Austritt eintragen'}</button>
            <button type="button" class="btn ghost" data-delete>Löschen</button>` : ''}</div></header>
      ${wahlHint(m, a)}
      <div class="tabs" role="tablist" aria-label="Mitgliederakte">${tabs.map(([k, label]) => html`<button type="button" role="tab" data-tab="${k}" aria-selected="${k === tab}">${label}</button>`)}</div>
      <div data-tab-body></div></div>`);
    const body = box.querySelector('[data-tab-body]');
    const show = {
      ueberblick: () => html`<div class="akte-grid">
        <section class="akte-card"><h4>Kontakt</h4><dl>
          <dt>E-Mail</dt><dd>${m.email ? html`<a href="mailto:${m.email}">${m.email}</a>` : badge('fehlt', 'warn')}</dd>
          ${f.email_work ? html`<dt>E-Mail dienstl.</dt><dd>${f.email_work}</dd>` : ''}
          ${[['Handy', f.mobile], ['Telefon', f.phone], ['Handy dienstl.', f.mobile_work], ['Telefon dienstl.', f.phone_work]].filter(([, v]) => v)
            .map(([k, v]) => html`<dt>${k}</dt><dd><a href="${telLink(v)}">${v}</a></dd>`)}
          <dt>Anschrift</dt><dd>${m.address.split('\n').map((l, i) => html`${i ? html`<br>` : ''}${l}`)}${anschriftVollstaendig(m) ? '' : html` ${badge('prüfen', 'warn')}`}</dd></dl></section>
        <section class="akte-card"><h4>Mitgliedschaft</h4><dl>
          <dt>Eintritt</dt><dd>${date(m.joined)}${m.left_on ? '' : ` (${years(m.joined)} Jahre)`}</dd>
          ${m.kv_joined ? html`<dt>Eintritt KV</dt><dd>${date(m.kv_joined)}</dd>` : ''}
          ${m.left_on ? html`<dt>Austritt</dt><dd>${date(m.left_on)}${f.left_reason ? ` · ${f.left_reason}` : ''}</dd>` : ''}
          <dt>Geburtsdatum</dt><dd>${date(m.birth)}</dd>
          <dt>Jahresbeitrag</dt><dd>${m.dues !== null && m.dues !== undefined ? html`${eur(m.dues)} <span class="muted">(fester Betrag)</span>` : 'automatisch nach Alter'}</dd>
          ${m.wahl ? html`<dt>Status</dt><dd>${wahlBadge(m.wahl.status)}</dd>` : ''}</dl></section>
        <section class="akte-card"><h4>Beiträge</h4>${a.jahre.length ? html`<ul class="akte-years">${a.jahre.slice(-3).reverse().map((j) => html`
          <li><span>${j.year}</span>${badge(YEAR[j.stand][0], YEAR[j.stand][1])}</li>`)}</ul>` : html`<p class="small muted">Kein Beitragsjahr seit Datenbeginn.</p>`}
          ${money ? '' : html`<p class="small muted">Beträge und Zahlungen stehen in der Buchhaltung.</p>`}</section>
        <section class="akte-card"><h4>Notiz <span class="small muted">nur Mitgliederverwaltung</span></h4>
          ${!isActive(m) ? html`<p class="small muted" data-note-gone>Für ausgetretene Mitglieder werden keine Notizen gespeichert – eine Notiz wird mit dem Austritt gelöscht (Datenschutz).</p>`
          : write ? html`<form data-note><textarea name="text" rows="3" maxlength="1000" placeholder="z. B. Rückruf vereinbart">${f.note || ''}</textarea>
            ${m.left_on ? html`<p class="small muted">Wird am ${date(m.left_on)} mit dem Austritt gelöscht.</p>` : ''}
            <div class="form-actions"><button type="submit" class="btn small">Notiz speichern</button></div></form>` : html`<p>${f.note || html`<span class="muted">Keine Notiz.</span>`}</p>`}</section>
        <section class="akte-card akte-wide"><h4>Letzte Änderungen</h4>${history(a.verlauf.slice(0, 4))}
          ${a.verlauf.length > 4 ? html`<button type="button" class="link small" data-tab="verlauf">Ganzer Verlauf</button>` : ''}</section></div>`,
      angaben: () => html`<dl class="akte-all">${a.angaben.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>`,
      konto: () => html`<div data-account></div>${mandateSection(m)}
        <h3>Rechnungen</h3>${(() => { const inv = store.data.invoices.filter((i) => i.member_id === id);
          return inv.length ? table(['Jahr', 'Rechnung', { label: 'Forderung', className: 'num' }, { label: 'Bezahlt', className: 'num' }, 'Status'],
            inv.map((i) => html`<tr><td>${i.year || date(i.issued)}</td><td>${i.number}</td><td class="num">${eur(i.total)}</td><td class="num">${eur(i.paid)}</td><td>${invoiceBadge(i)}</td></tr>`))
            : empty('Keine Rechnungen', 'Beitragslauf unter „Rechnungen“ starten.'); })()}`,
      verlauf: () => history(a.verlauf),
    };
    mount(body, show[tab]());
    box.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { tab = b.dataset.tab; draw(a); }));
    box.querySelector('[data-edit]')?.addEventListener('click', () => memberDialog(f));
    box.querySelector('[data-delete]')?.addEventListener('click', () => deleteMember(m, () => el.close()));
    box.querySelector('[data-leave]')?.addEventListener('click', () => leaveDialog(m, a, () => reopen()));
    box.querySelector('[data-note]')?.addEventListener('submit', (e) => {
      e.preventDefault();
      safe(async () => {
        await api('/api/member-notiz', { id, text: e.target.text.value });
        toast('Notiz gespeichert.');
        reopen();
      });
    });
    if (tab === 'konto') accountBox(m, a, box, () => reopen('konto'));
  }
}

/** Verlauf aus dem Protokoll: Datum, Änderung, wer. */
const history = (rows) => (rows.length ? html`<ol class="akte-history">${rows.map((r) => html`<li><span class="muted">${dateTime(r.time)}</span>
  <span>${r.text}<span class="sub">${r.actor}</span></span></li>`)}</ol>` : html`<p class="small muted">Noch keine Änderungen im Programm protokolliert.</p>`);

/** Beitragskonto (nur mit Buchhaltung): Saldo, Jahre mit Soll von Hand, Buchungen, gemerkte Mandatsreferenzen, SEPA-Mandat. */
function accountBox(m, a, root, reload) {
  bindMandateSection(root, m, reload);
  const box = root.querySelector('[data-account]');
  mount(box, html`<p class="${a.balance < 0 ? 'notice warn' : 'notice'}">${a.balance < 0 ? `Rückstand ${eur(-a.balance)}` : a.balance > 0 ? `Guthaben ${eur(a.balance)}` : 'Ausgeglichen'}
      – fällige Beiträge seit ${a.first} minus Zahlungen.${a.issues.length ? ` Zu klären: ${a.issues.join('; ')}` : ''}
      <a class="link small" href="/api/member-account.pdf?id=${m.id}" target="_blank" rel="noopener">Beitragskonto als PDF</a></p>
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
      : html`<p class="small muted">Keine Buchungen.</p>`}
    ${a.member.external_balance !== null && a.member.external_balance !== undefined ? html`<p class="small muted">Kontostand laut Mitgliederverwaltung: ${eur(a.member.external_balance)}</p>` : ''}
    ${a.sepa_refs.length ? html`<p class="small">Gemerkte Mandatsreferenzen (SEPA-Datei): ${a.sepa_refs.map((r, n) => html`${n ? ', ' : ''}${r.mandate}
      <button type="button" class="link" data-forget="${n}" title="Nicht mehr automatisch diesem Mitglied zuordnen">entfernen</button>`)}</p>` : ''}`);
  box.querySelectorAll('[data-target]').forEach((b) => (b.onclick = () => targetDialog(m, a.years.find((r) => r.year === Number(b.dataset.target)), reload)));
  box.querySelectorAll('[data-forget]').forEach((b) => (b.onclick = () => safe(async () => {
    const r = a.sepa_refs[Number(b.dataset.forget)];
    await api('/api/sepa-mandat-vergessen', { creditor: r.creditor, mandate: r.mandate });
    toast('Gemerkte Mandatsreferenz entfernt.');
    reload();
  })));
}

/** Austritt eintragen, ändern oder zurücknehmen (0.37): Datum (auch künftig), Grund optional; a = /api/member-account mit den Hinweisen
 * des Servers (Ja/Nein, ohne Beträge). Die Hinweise sperren nichts. Der Status ergibt sich danach von selbst. */
function leaveDialog(m, a, reopen) {
  const hints = [a.austritt.offen && 'offene Beiträge', a.austritt.mandat && 'ein aktives SEPA-Mandat'].filter(Boolean);
  const el = overlay(`Austritt · ${sortName(m)}`, html`<form id="leave-form" style="display:flex;flex-direction:column;gap:14px">
    <div class="formgrid"><label class="field">Austrittsdatum<input name="left_on" type="date" required min="${m.joined}" max="${Number(todayIso().slice(0, 4)) + 2}-12-31" value="${m.left_on || todayIso()}">
        <span class="small muted">Auch ein künftiges Datum ist möglich – bis dahin bleibt das Mitglied aktiv.</span></label>
      <label class="field span">Grund<input name="reason" maxlength="200" autocomplete="off" value="${a.member.left_reason || ''}" placeholder="optional, z. B. Kündigung, Umzug, Altersgrenze"></label></div>
    ${a.member.note ? html`<p class="small muted" data-leave-note>Die Notiz zum Mitglied wird am Austrittstag gelöscht (Datenschutz).</p>` : ''}
    ${hints.length ? html`<p class="notice warn" data-leave-hints>Bitte prüfen: Das Mitglied hat ${hints.join(' und ')}. Der Austritt wird trotzdem gespeichert.</p>` : ''}
    <p class="notice warn" data-leave-year hidden></p>
    <div class="form-actions">${m.left_on ? html`<button type="button" class="btn ghost" data-undo>Austritt zurücknehmen</button>` : ''}
      <button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Austritt speichern</button></div></form>`, { wide: false });
  // Austrittsjahr ohne festes Soll (Festlegung oder Beitragsrechnung): der Beitrag ist danach „zu klären“ und das Mitglied bis zur
  // Festlegung nicht wahlberechtigt (wahlrecht.py) – vor dem Speichern deutlich sagen
  const dayInput = el.querySelector('[name=left_on]'), box = el.querySelector('[data-leave-year]');
  const warn = () => {
    const year = Number(dayInput.value.slice(0, 4));
    box.hidden = !year || !!m.guest || !!m.dues_free || a.austritt.festgelegt.includes(year);
    box.textContent = `Der Beitrag des Austrittsjahres ${year} muss danach in der Buchhaltung festgelegt werden (Beitragskonto → Soll festlegen)${
      dayInput.value > todayIso() ? ' – bis dahin gilt das Mitglied als nicht wahlberechtigt' : ''}.`;
  };
  dayInput.oninput = warn;
  warn();
  const save = (data, message) => safe(async () => {
    await api('/api/member-austritt', { id: m.id, ...data });
    el.close();
    await refresh();
    toast(message);
    reopen();
  });
  el.querySelector('[data-undo]')?.addEventListener('click', () => save({ left_on: null }, 'Austritt zurückgenommen.'));
  el.querySelector('#leave-form').onsubmit = (e) => {
    e.preventDefault();
    save(Object.fromEntries(new FormData(e.target)), 'Austritt eingetragen.');
  };
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

/** Stand des Beitrags im gewählten Haushaltsjahr – nur als Kennzeichen (Server: mitgliederkonto.stand, auch ohne Buchhaltung);
 * Beträge und Konten stehen in der Buchhaltung. */
const DUES = { klaeren: ['klären', 'warn'], frei: ['beitragsfrei', ''], spaeter: ['noch nicht fällig', ''], bezahlt: ['bezahlt', 'ok'], offen: ['offen', 'warn'] };
function duesState(m) {
  const [label, kind] = DUES[m.dues_state] || DUES.frei;
  const older = (m.dues_open_years || []).filter((y) => y !== store.year);
  const hint = m.dues_state === 'klaeren' ? 'Beitrag nicht berechenbar' : m.dues_state === 'frei' ? (m.guest ? 'Gastmitglied' : m.dues_basis || '')
    : older.length ? `offen: ${older.join(', ')}` : m.dues_state === 'offen' && (m.paid || 0) > 0
      ? ((m.paid || 0) >= (m.dues_soll || 0) ? 'Zahlung deckt ältere Jahre' : 'teilweise bezahlt') : '';
  return [badge(label, kind), hint];
}
const postfaehig = (m) => anschriftVollstaendig(m) && !m.no_mail;   // wie mitgliederlisten.postfaehig

/** Gespeicherte Ansicht anlegen (Liste, Kreis-/Ortsverband, Suche) – nur in diesem Browser. */
function saveView(redraw) {
  const el = overlay('Ansicht speichern', html`<form id="view-form" style="display:flex;flex-direction:column;gap:14px">
    <label class="field">Name der Ansicht<input name="name" required maxlength="40" autocomplete="off" placeholder="z. B. Eimsbüttel ohne E-Mail"></label>
    <p class="small muted">Gespeichert werden nur die Filter, keine Mitgliederdaten – und nur in diesem Browser.</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`, { wide: false });
  el.querySelector('#view-form').onsubmit = (e) => {
    e.preventDefault();
    const name = e.target.name.value.trim();
    const list = gespeichert().filter((v) => v.name !== name);
    if (!speichern([...list, { name, liste, kv: kvFilter, ov: ovFilter, suche: search }])) return toast('Dieser Browser speichert keine Ansichten.');
    ansicht = name;
    el.close();
    redraw();
  };
}

/** Anschreiben an die Auswahl: Betreff und Text; Anschrift, Datum und Briefanrede setzt der Server je Mitglied. */
function letterDialog(ids, post) {
  const el = overlay(`Anschreiben an ${post} ${post === 1 ? 'Mitglied' : 'Mitglieder'}`, html`<form id="letter-form" style="display:flex;flex-direction:column;gap:14px">
    <label class="field">Betreff<input name="betreff" required maxlength="200" autocomplete="off"></label>
    <label class="field">Text<textarea name="text" required maxlength="5000" rows="10" placeholder="Absätze mit einer Leerzeile trennen"></textarea>
      <span class="small muted">Anschrift, Datum und Briefanrede („Briefanrede 1“, sonst „Hallo Vorname,“) setzt das Programm je Mitglied, darunter „Viele Grüße“ und der Name des Verbands.</span></label>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">PDF erstellen</button></div></form>`, { wide: false });
  el.querySelector('#letter-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const f = e.target;
      if (await download('/api/mitglieder-liste', `Anschreiben-${todayIso()}.pdf`, { art: 'anschreiben', ids, betreff: f.betreff.value, text: f.text.value })) el.close();
    });
  };
}

let liste = 'aktiv';      // feste Liste (mitglieder-listen.js)
let ansicht = '';         // Name der gewählten gespeicherten Ansicht
let selected = new Set(); // ausgewählte Mitglieder (Kennungen) für die Serienaktionen

/** Liste von außen öffnen (Überblick): feste Liste, optional für einen Kreisverband, sonst ohne weitere Filter. */
export function openList(id, { kv = '' } = {}) {
  liste = id;
  ansicht = '';
  ovFilter = search = stateFilter = '';
  kvFilter = kv;
  selected = new Set();
  go('members');
}

export function members(main) {
  if (ovFilter && ovFilter !== 'none' && !ovsOf(kvFilter, ovFilter).some((o) => String(o.id) === ovFilter)) ovFilter = '';
  const today = todayIso(), y = String(store.year);
  const all = store.data.members;
  const lists = listen(store.year, today);
  if (!lists.some((l) => l.id === liste)) liste = 'aktiv';
  const current = lists.find((l) => l.id === liste);
  const saved = gespeichert();
  const money = can('buchhaltung');   // Filter nach Beträgen nur mit Buchhaltung
  if (!money) stateFilter = '';
  const list = matchMembers(all, search, kvFilter).filter(current.test).filter(inState)
    .filter((m) => !ovFilter || (ovFilter === 'none' ? !m.ov_id : String(m.ov_id) === ovFilter));
  const shown = new Set(list.map((m) => m.id));
  selected = new Set([...selected].filter((id) => shown.has(id)));   // Auswahl folgt der Liste
  const picked = list.filter((m) => selected.has(m.id)), post = picked.filter(postfaehig).length;
  const ovChoices = kvFilter ? (store.data.ovs || []).filter((o) => o.kv === kvFilter && (o.members || o.active)) : [];
  const active = all.filter((m) => isActive(m, today));
  const joined = all.filter((m) => (m.joined || '').startsWith(y)).length, left = all.filter((m) => (m.left_on || '').startsWith(y)).length;
  const turning = active.filter((m) => m.birth && Number(m.birth.slice(0, 4)) === store.year - 35).length;   // Altersgrenze Satzung § 2
  const gaps = active.filter((m) => fehlt(m).length).length;
  const kvs = store.data.kv.filter((k) => all.some((m) => m.kv === k.code));
  const cases = store.data.cases?.active || 0;
  const wahl = all.some((m) => m.wahl);
  const ids = list.map((m) => m.id);   // „alle auswählen“: die ganze Liste, auch über die angezeigten LIMIT Zeilen hinaus
  const allOn = ids.length > 0 && ids.every((id) => selected.has(id));
  const write = can('mitglieder', 'schreiben');
  mount(main, html`<div class="page wide">
    ${pageHead('Mitgliederverwaltung', 'Mitglieder', html`<button type="button" class="btn" data-doubles>Doppelte prüfen</button>
      <button type="button" class="btn" data-ov-abgleich>Ortsverbände abgleichen</button>${write ? html`
      <button type="button" class="btn" data-import>${icon('upload', 16)} Liste importieren</button>
      <button type="button" class="btn primary" data-new>${icon('plus', 16)} Mitglied anlegen</button>` : ''}`)}
    <section class="cards" aria-label="Mitgliederbestand">
      <div class="card"><span class="label">Aktive Mitglieder</span><span class="value">${active.length}</span>
        <span class="note">${kvs.length} ${kvs.length === 1 ? 'Kreisverband' : 'Kreisverbände'}${all.length > active.length ? ` · ${all.length - active.length} ausgetreten` : ''}</span></div>
      <div class="card"><span class="label">Eintritte ${store.year}</span><span class="value">${joined}</span><span class="note">Austritte ${store.year}: ${left}</span></div>
      <div class="card"><span class="label">Werden ${store.year} 35 Jahre</span><span class="value">${turning}</span><span class="note">Altersgrenze (Satzung § 2)</span></div>
      <div class="card"><span class="label">Daten vervollständigen</span><span class="value ${gaps ? 'over' : ''}">${gaps}</span>
        <span class="note">Anschrift, E-Mail, Geburtsdatum oder Ortsverband fehlt</span></div>
    </section>
    ${cases ? html`<p class="notice warn">${cases} ${cases === 1 ? 'Beitragszahlung wartet' : 'Beitragszahlungen warten'} auf Klärung, zu wem sie gehören:
      <button type="button" class="link" data-go-cases>Klärfälle öffnen</button></p>` : ''}
    <div class="tabs ml-tabs" role="tablist" aria-label="Listen">${lists.map((l) => html`
      <button type="button" role="tab" data-liste="${l.id}" aria-selected="${!ansicht && l.id === liste}">${l.label}<span class="n">${all.filter(l.test).length}</span></button>`)}${
      saved.map((v, i) => html`<button type="button" role="tab" data-saved="${i}" aria-selected="${ansicht === v.name}">${v.name}</button>`)}</div>
    <div class="toolbar"><label class="search grow">${icon('search', 16)}<span class="sr-only">Mitglieder suchen</span><input id="member-search" type="search" autocomplete="off"
        placeholder="Name, Straße, PLZ, Ort, Mitgliedsnummer oder E-Mail" value="${search}"></label>
      <label class="field inline">Kreisverband<select id="member-kv"><option value="">alle</option>${kvs.map((k) => html`<option value="${k.code}" ${k.code === kvFilter ? 'selected' : ''}>${k.name}</option>`)}</select></label>
      ${kvFilter ? html`<label class="field inline">Ortsverband<select id="member-ov"><option value="">alle</option><option value="none" ${ovFilter === 'none' ? 'selected' : ''}>ohne Ortsverband</option>
        ${ovChoices.map((o) => html`<option value="${o.id}" ${String(o.id) === ovFilter ? 'selected' : ''}>${ovLabel(o)} (${o.members})</option>`)}</select></label>` : ''}
      ${money ? html`<label class="field inline">Beitrag<select id="member-state"><option value="">alle</option>${Object.entries(STATES).map(([k, v]) => html`<option value="${k}" ${k === stateFilter ? 'selected' : ''}>${v}</option>`)}</select></label>` : ''}
      ${ansicht ? html`<button type="button" class="link" data-delete-view>Ansicht „${ansicht}“ löschen</button>`
        : html`<button type="button" class="ml-save" data-save-view>+ Ansicht speichern</button>`}</div>
    ${current.hint && !ansicht ? html`<p class="small muted">${current.label}: ${current.hint}.</p>` : ''}
    <section class="panel" style="padding:6px 12px">${all.length ? table([
      { label: html`<input type="checkbox" class="ml-check" data-check-all ${allOn ? 'checked' : ''} ${ids.length ? '' : 'disabled'} aria-label="Alle ${ids.length} auswählen">`, className: 'ml-pick' },
      'Mitglied', 'Anschrift', 'Kreis- und Ortsverband', 'Kontakt', { label: 'Alter', className: 'num' }, `Beitrag ${store.year}`, ...(wahl ? ['Status'] : [])],
      list.slice(0, LIMIT).map((m) => {
        const [state, hint] = duesState(m), age = years(m.birth);
        return html`<tr class="clickable ${selected.has(m.id) ? 'is-picked' : ''}" data-member="${m.id}" tabindex="0">
          <td class="ml-pick"><input type="checkbox" class="ml-check" data-pick="${m.id}" ${selected.has(m.id) ? 'checked' : ''} aria-label="${sortName(m)} auswählen"></td>
          <td><strong style="font-weight:600">${sortName(m)}</strong>${statusBadges(m)}<span class="sub">${m.number}${
            m.left_on ? ` · ${m.left_on > today ? 'Austritt zum' : 'ausgetreten'} ${date(m.left_on)}` : m.joined ? ` · seit ${m.joined.slice(0, 4)}` : ''}</span></td>
          <td class="small">${anschriftVollstaendig(m) ? addressLine(m) : html`${addressLine(m)} ${badge('Anschrift prüfen', 'warn')}`}</td>
          <td>${kvName(m.kv)}${m.ov_id ? html`<span class="sub">${ovLabel(ovById(m.ov_id))}</span>` : ''}</td><td class="small">${m.email || badge('E-Mail fehlt', 'warn')}</td>
          <td class="num">${age ?? '—'}${age !== null && m.birth && Number(m.birth.slice(0, 4)) === store.year - 35 ? html`<span class="sub">wird 35</span>` : ''}</td>
          <td title="${m.dues_basis || ''}">${state}${activeMandate(m.id) ? html` ${badge('Lastschrift', 'navy')}` : ''}${hint ? html`<span class="sub">${hint}</span>` : ''}</td>
          ${wahl ? html`<td>${m.wahl ? html`${wahlBadge(m.wahl.status)}${m.wahl.status === 'nicht' ? html`<span class="sub">${m.wahl.gruende.join(' · ')}</span>` : ''}` : ''}</td>` : ''}</tr>`;
      }))
      : empty('Noch keine Mitglieder', 'Einzeln anlegen oder eine Mitgliederliste (Excel/CSV) importieren.')}
      ${all.length && !list.length ? html`<p class="muted" style="padding:12px">Kein Mitglied passt zur Auswahl.</p>` : ''}</section>
    <p class="small muted">${list.length > LIMIT ? `Die ersten ${LIMIT} von ${list.length} Treffern – Suche oder Kreisverband eingrenzen.` : `${list.length} von ${all.length} angezeigt`} · sortiert nach Nachname.
      Hier steht nur der Stand des Beitrags ${store.year}. Beträge, Beitragseingänge und die OP-Listen der Kreisverbände stehen in der Buchhaltung.</p>
    ${picked.length ? html`<div class="ml-bar" role="region" aria-label="Auswahl">
      <strong>${picked.length} ausgewählt</strong>${picked.length > post ? html`<span class="small">${picked.length - post} ohne Post (Anschrift unvollständig oder Postausschluss)</span>` : ''}
      <span class="ml-actions"><button type="button" class="btn small" data-letter ${post ? '' : 'disabled'}>${icon('file', 14)} Anschreiben</button>
        <button type="button" class="btn small" data-labels ${post ? '' : 'disabled'}>Adressetiketten</button>
        <button type="button" class="btn small" data-excel>${icon('download', 14)} Excel je Kreisverband</button>
        <button type="button" class="btn small" data-csv>CSV</button>
        <button type="button" class="btn small ghost" data-unpick>Auswahl aufheben</button></span></div>` : ''}
  </div>`);
  const redraw = () => members(main);
  main.querySelector('[data-new]')?.addEventListener('click', () => memberDialog());
  main.querySelector('[data-import]')?.addEventListener('click', importMembersDialog);
  main.querySelector('[data-doubles]').onclick = () => doppelteDialog();
  main.querySelector('[data-ov-abgleich]').onclick = () => ovAbgleichDialog();
  main.querySelector('[data-go-cases]')?.addEventListener('click', () => go('cases'));
  main.querySelectorAll('[data-liste]').forEach((b) => (b.onclick = () => { liste = b.dataset.liste; ansicht = ''; redraw(); }));
  main.querySelectorAll('[data-saved]').forEach((b) => (b.onclick = () => {
    const v = saved[Number(b.dataset.saved)];
    ({ liste, kv: kvFilter, ov: ovFilter, suche: search } = { liste: 'aktiv', kv: '', ov: '', suche: '', ...v });
    ansicht = v.name;
    redraw();
  }));
  main.querySelector('[data-save-view]')?.addEventListener('click', () => saveView(redraw));
  main.querySelector('[data-delete-view]')?.addEventListener('click', () => {
    speichern(saved.filter((v) => v.name !== ansicht));
    ansicht = '';
    redraw();
  });
  main.querySelector('#member-kv').onchange = (e) => { kvFilter = e.target.value; ovFilter = ''; ansicht = ''; redraw(); };
  main.querySelector('#member-ov')?.addEventListener('change', (e) => { ovFilter = e.target.value; ansicht = ''; redraw(); });
  main.querySelector('#member-state')?.addEventListener('change', (e) => { stateFilter = e.target.value; redraw(); });
  const input = main.querySelector('#member-search');
  input.oninput = () => {
    search = input.value;
    ansicht = '';
    const pos = input.selectionStart;
    redraw();
    const again = main.querySelector('#member-search');
    again.focus();
    again.setSelectionRange(pos, pos);
  };
  main.querySelector('[data-check-all]')?.addEventListener('change', (e) => {
    ids.forEach((id) => (e.target.checked ? selected.add(id) : selected.delete(id)));
    redraw();
  });
  main.querySelectorAll('[data-pick]').forEach((c) => {
    c.onclick = (e) => e.stopPropagation();   // Auswahl, nicht das Mitglied öffnen
    c.onchange = () => { const id = Number(c.dataset.pick); c.checked ? selected.add(id) : selected.delete(id); redraw(); };
  });
  const pickedIds = picked.map((m) => m.id);
  main.querySelector('[data-csv]')?.addEventListener('click', () => safe(() => download('/api/mitglieder-liste', `Mitgliederliste-${todayIso()}.csv`, { art: 'csv', ids: pickedIds })));
  const zipped = new Set(picked.map((m) => m.kv)).size > 1;   // je Kreisverband eine Datei, mehrere als ZIP (mitgliederlisten.excel)
  main.querySelector('[data-excel]')?.addEventListener('click', () => safe(() => download('/api/mitglieder-liste', `Mitgliederliste-${todayIso()}.${zipped ? 'zip' : 'xlsx'}`, { art: 'excel', ids: pickedIds })));
  main.querySelector('[data-labels]')?.addEventListener('click', () => safe(() => download('/api/mitglieder-liste', `Adressetiketten-${todayIso()}.pdf`, { art: 'etiketten', ids: pickedIds })));
  main.querySelector('[data-letter]')?.addEventListener('click', () => letterDialog(pickedIds, post));
  main.querySelector('[data-unpick]')?.addEventListener('click', () => { selected = new Set(); redraw(); });
  main.querySelectorAll('[data-member]').forEach((row) => {
    row.onclick = () => account(Number(row.dataset.member));
    row.onkeydown = (e) => { if (e.target === row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); account(Number(row.dataset.member)); } };
  });
}
