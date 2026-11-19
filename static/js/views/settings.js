// Stammdaten: Verein & Bank, Beiträge, Gliederung (Kreisverbände mit Nummer der Mitgliederverwaltung, Ortsverbände, Import),
// E-Mail-Versand (Strato), Vorlagen, Sicherung; im Serverbetrieb zusätzlich Anmeldung (Passwort ändern).
import { api, download } from '../api.js';
import { html, mount, raw } from '../html.js';
import { toInput } from '../format.js';
import { store, refresh, can } from '../store.js';
import { pageHead, safe, toast, icon, mailPreview, dialog, table, badge, empty, confirmAction, uploadProtected } from '../ui.js';
import { ovLabel } from '../gliederung.js';
import { accountField, bindAccountPickers } from '../account-picker.js';
import { addressState, addressSummary, addressCredit, loadAddressData, loadedText } from '../adressdaten.js';

const TABS = [['schalter', 'Schalter'], ['verein', 'Verein & Bank'], ['beitraege', 'Beiträge'], ['gliederung', 'Gliederung'], ['versand', 'E-Mail-Versand'], ['vorlagen', 'Vorlagen'], ['sicherung', 'Sicherung']];
// ohne das Recht „Einstellungen“ bleibt im Serverbetrieb nur die eigene Anmeldung (Passwort ändern)
const tabs = () => (store.mode !== 'server' ? TABS : can('einstellungen') ? [...TABS, ['zugang', 'Anmeldung']] : [['zugang', 'Anmeldung']]);
const PLACEHOLDERS = [['vorname', 'Vorname'], ['name', 'Name'], ['nummer', 'Rechnungsnummer'], ['betrag', 'Betrag'], ['faellig', 'Fällig am'],
  ['datum', 'Rechnungsdatum'], ['jahr', 'Beitragsjahr'], ['leistung', 'Leistungen'], ['mitgliedsnummer', 'Mitgliedsnummer'], ['iban', 'IBAN'], ['absender', 'Absendername'],
  ['offen', 'offener Betrag'], ['neue_frist', 'neue Frist (Erinnerung)'], ['stufe', 'Nummer der Erinnerung'],
  ['einzug', 'Einzugsdatum (Lastschrift)'], ['konto', 'Konto gekürzt (Lastschrift)'], ['mandat', 'Mandatsreferenz'], ['glaeubiger', 'Gläubiger-ID']];
let tab = 'schalter';
let ovKv = '';   // Gliederung: Ortsverbände eines Kreisverbands zeigen ('' = alle)

// attrs: nur feste Attribut-Texte aus diesem Modul (nie Nutzerdaten) – deshalb raw(), sonst würden die Anführungszeichen maskiert
const field = (label, name, value, attrs = '', hint = '') => html`<label class="field">${label}
  <input name="${name}" value="${value ?? ''}" ${raw(attrs)}>${hint ? html`<span class="small muted">${hint}</span>` : ''}</label>`;
const area = (label, name, value, rows = 3, hint = '') => html`<label class="field span">${label}
  <textarea name="${name}" rows="${rows}">${value ?? ''}</textarea>${hint ? html`<span class="small muted">${hint}</span>` : ''}</label>`;
const save = html`<div class="form-actions"><button type="submit" class="btn primary">Speichern</button></div>`;

/** Schalter (Verwaltung, ab 0.31): Abläufe, die das Programm selbst erledigen kann – jede Änderung steht im Änderungsprotokoll. */
function schalter(s) {
  const now = new Date().getFullYear(), first = Number((s.fo_start || '2026').slice(0, 4));
  const years = [];
  for (let y = first; y <= now + 1; y++) years.push(y);
  const on = s.kv_share_auto === 'yes', from = s.kv_share_auto_from || String(now + 1);
  const open = store.data.dues_open_all, waiting = store.data.cases?.unassigned || 0;
  return html`<form data-save class="panel switch-list">
    <div class="switch-row">
      <div class="switch-text"><h2>Beitragsanteil der Kreisverbände automatisch aus den Mitgliedsbeiträgen</h2>
        <p class="muted"><strong>An:</strong> Der Beitragsanteil jedes Kreisverbands ergibt sich laufend aus den gezahlten und zugeordneten Beiträgen seiner Mitglieder –
          25 % nach Finanzordnung § 6 Abs. 1, ohne Eingabe. Ein unter „Haushalt &amp; Budgets“ festgestellter Betrag zählt ab dem gewählten Jahr nicht mehr.
          <strong>Aus:</strong> wie bisher der festgestellte Betrag (z. B. aus der Masterdatei); nur ohne Eingabe wird gerechnet.</p>
        <label class="field inline">Gilt ab Haushaltsjahr<select name="kv_share_auto_from">${years.map((y) => html`<option ${String(y) === from ? 'selected' : ''}>${y}</option>`)}</select></label>
        <p class="small muted">Die Schüler Union behält ihr festes Budget. Abgeschlossene Budgetjahre bleiben, wie sie sind.</p>
        ${open || waiting ? html`<p class="notice warn">Vorher klären: ${[open && `${open} Beitragszahlungen ohne Mitglied`, waiting && `${waiting} Klärfälle ohne Zuordnung`].filter(Boolean).join(' und ')} –
          sonst fehlen sie im Anteil der Kreisverbände.</p>` : ''}</div>
      <label class="switch"><input type="checkbox" name="kv_share_auto" role="switch" aria-label="Beitragsanteil der Kreisverbände automatisch" ${on ? 'checked' : ''}><span class="switch-track" aria-hidden="true"></span>
        <span class="switch-label" data-switch-label>${on ? 'An' : 'Aus'}</span></label>
    </div>
    <div class="switch-row">
      <div class="switch-text"><h2>Karte: Hintergrund von OpenStreetMap laden</h2>
        <p class="muted"><strong>An:</strong> Die Karte (Mitgliederverwaltung → Karte) zeigt Straßen und Gebäude von OpenStreetMap. Dafür lädt das Programm nur die
          Kartenbilder des gezeigten Ausschnitts aus dem Internet – keine Namen, keine Anschriften. <strong>Aus:</strong> nur Bezirke, Stadtteile und Punkte, ganz ohne Internet.</p></div>
      <label class="switch"><input type="checkbox" name="map_tiles" role="switch" aria-label="Karte: Hintergrund von OpenStreetMap laden" ${s.map_tiles === 'yes' ? 'checked' : ''}><span class="switch-track" aria-hidden="true"></span>
        <span class="switch-label" data-switch-label>${s.map_tiles === 'yes' ? 'An' : 'Aus'}</span></label>
    </div>
    <div class="switch-row">
      <div class="switch-text"><h2>Amtliche Adressdaten für Karte und Adressprüfung</h2>
        <p class="muted">Öffentlicher Adressbestand der Stadt Hamburg (alle Straßen und Hausnummern mit Lage). Er wird auf diesem Rechner gespeichert;
          geprüft und verortet wird nur hier. Beim Laden wird immer der ganze Bestand abgerufen – keine Mitgliederadresse geht ins Internet.
          Neue Straßen kommen dazu, wenn man den Bestand gelegentlich neu lädt (etwa eine Minute).</p>
        <p class="small" data-address-state aria-live="polite">Stand wird geladen …</p>
        <p class="small muted">${addressCredit()}</p></div>
      <button type="button" class="btn" data-address-load>${icon('download', 16)} Adressdaten laden</button>
    </div>
    ${save}</form>`;
}

function verein(s) {
  const n = store.data.next_numbers;
  return html`<form data-save class="panel" style="display:flex;flex-direction:column;gap:16px">
    <div class="formgrid">
      ${field('Rechtlicher Rechnungsaussteller', 'issuer', s.issuer, 'placeholder="z. B. Junge Union Hamburg"')}
      ${field('Kontakt-E-Mail (auf der Rechnung)', 'contact_email', s.contact_email, 'type="email"')}
      ${area('Anschrift des Ausstellers', 'issuer_address', s.issuer_address, 3)}
      ${field('IBAN', 'iban', s.iban, 'autocomplete="off"', 'Wird geprüft; der QR-Code auf der Rechnung nutzt sie.')}
      ${field('BIC', 'bic', s.bic, 'autocomplete="off"', 'optional')}
      ${field('Bank', 'bank_name', s.bank_name, '', 'optional')}
      ${field('Telefon', 'contact_phone', s.contact_phone, '', 'optional')}
      ${field('Website', 'website', s.website, '', 'optional')}
      ${field('Zahlungsziel normale Rechnungen (Tage)', 'normal_terms', s.normal_terms, 'inputmode="numeric"')}
    </div>
    <h3>Buchhaltung</h3>
    <div class="formgrid">
      <label class="field">Konto für Verbindlichkeiten (Eingangsrechnungen)${accountField(html`<input name="liability_account" value="${s.liability_account}" inputmode="numeric" data-kinds="liability" autocomplete="off">`)}
        <span class="small muted">Vorauswahl 1600 – fachlich zu bestätigen. Gilt für neu erfasste Eingangsrechnungen; erfasste behalten ihr Konto.</span></label>
      ${field('Bezeichnung des Kontos', 'liability_name', s.liability_name)}
    </div>
    <h3>SEPA-Lastschrift (Mitgliedsbeiträge)</h3>
    <div class="formgrid">
      ${field('Gläubiger-Identifikationsnummer', 'creditor_id', s.creditor_id, 'autocomplete="off" placeholder="z. B. DE98ZZZ09999999999"', 'Kostenlos bei der Deutschen Bundesbank; zusätzlich ist eine Lastschriftvereinbarung mit der Bank nötig.')}
      ${field('Präfix der Mandatsreferenz', 'sepa_prefix', s.sepa_prefix, 'autocomplete="off"', 'Referenz: Präfix-Mitgliedsnummer-laufende Nummer')}
      ${field('Vorabankündigung (Tage vor dem Einzug)', 'sepa_prenotice', s.sepa_prenotice, 'inputmode="numeric"', 'Ohne andere Vereinbarung im Mandat 14 Tage; die Ankündigung steht in der Beitragsrechnung.')}
      <span class="field"><span>Mandatsformular</span><a class="btn small" href="/api/mandate.pdf" target="_blank" rel="noopener">Leeres Formular (PDF)</a></span>
    </div>
    <h3>Vorstand und Freigaben (Finanzordnung § 7)</h3>
    <div class="formgrid">
      ${field('Landesvorsitz', 'chair_name', s.chair_name, 'autocomplete="off"', 'Vorbelegung bei Freigaben')}
      ${field('Landesschatzmeister', 'treasurer_name', s.treasurer_name, 'autocomplete="off"', 'Vorbelegung bei Freigaben und Quartalsprüfung')}
      ${field('Freigabegrenze für Fremdrechnungen (€)', 'approval_limit', toInput(Number(s.approval_limit)), 'inputmode="decimal"', 'Über diesem Betrag geben Landesvorsitz und Landesschatzmeister frei – höchstens 500,00 € (§ 7 Abs. 4); gilt für neu erfasste Rechnungen.')}
      ${field('KV-Finanzrahmen ab Haushaltsjahr', 'fo_start', s.fo_start.slice(0, 4), 'inputmode="numeric" maxlength="4"', 'Budgetkonten der Kreisverbände (Finanzordnung §§ 2, 6) zählen Beitragsanteile, Spenden und Ausgaben ab dem 1. Januar dieses Jahres – frühere Jahre (z. B. aus der Excel-Übernahme) bleiben außen vor.')}
    </div>
    <h3>Normale Rechnungen (z. B. Veranstaltungen)</h3>
    ${area('Steuerhinweis', 'tax_note', s.tax_note, 2, 'Nur den fachlich bestätigten Text eintragen.')}
    <label class="check"><input type="checkbox" name="invoice_ready" ${s.invoice_ready === 'yes' ? 'checked' : ''}> Rechnungen ohne Umsatzsteuerausweis sind für den Aussteller fachlich bestätigt.</label>
    <p class="notice">Rechnungsnummern werden automatisch fortlaufend je Kalenderjahr vergeben und können nicht geändert werden.
      Nächste Nummern: <strong>${n.dues}</strong> (Beitrag) · <strong>${n.normal}</strong> (Rechnung).</p>
    ${save}</form>`;
}

function beitraege(s) {
  const young = Number(s.dues_young_max);
  const mid = Number(s.dues_mid_max);
  return html`<form data-save class="panel" style="display:flex;flex-direction:column;gap:16px">
    <label class="field">Altersstichtag für die Beitragsstaffel<select name="age_basis">
      <option value="">Noch ungeklärt – Staffel gesperrt</option>
      <option value="jan1" ${s.age_basis === 'jan1' ? 'selected' : ''}>1. Januar (im Eintrittsjahr frühestens Eintritt)</option>
      <option value="dec31" ${s.age_basis === 'dec31' ? 'selected' : ''}>31. Dezember</option></select></label>
    <p class="notice warn">Die Satzung nennt keinen eindeutigen Altersstichtag – die Auswahl ist fachlich zu bestätigen.</p>
    <h3>Beitragsstaffel (Satzung § 49)</h3>
    <div class="staffel">
      <span>Mindestalter</span><input name="dues_min_age" value="${s.dues_min_age}" inputmode="numeric" aria-label="Mindestalter"><span class="small muted">Jahre</span>
      <span>bis einschließlich</span><input name="dues_young_max" value="${s.dues_young_max}" inputmode="numeric" aria-label="Erste Altersgrenze"><input name="dues_young" value="${toInput(Number(s.dues_young))}" inputmode="decimal" aria-label="Beitrag bis ${young} Jahre in Euro">
      <span>bis einschließlich</span><input name="dues_mid_max" value="${s.dues_mid_max}" inputmode="numeric" aria-label="Zweite Altersgrenze"><input name="dues_mid" value="${toInput(Number(s.dues_mid))}" inputmode="decimal" aria-label="Beitrag bis ${mid} Jahre in Euro">
      <span>ab ${mid + 1} Jahren</span><span></span><input name="dues_old" value="${toInput(Number(s.dues_old))}" inputmode="decimal" aria-label="Beitrag ab ${mid + 1} Jahren in Euro">
    </div>
    <p class="small muted">Beträge in Euro pro Jahr. Ein individueller Jahresbeitrag am Mitglied (Mitglieder → Bearbeiten) hat Vorrang vor der Staffel.</p>
    <label class="check"><input type="checkbox" name="dues_prorate" ${s.dues_prorate === 'yes' ? 'checked' : ''}> Im Eintrittsjahr anteilig nach Quartal (75 / 50 / 25 / 0 %)</label>
    <div class="formgrid">${field('Zahlungsziel Beitragsrechnungen (Tage)', 'dues_terms', s.dues_terms, 'inputmode="numeric"', 'Fällig frühestens zum 1. Januar des Beitragsjahres.')}</div>
    ${save}</form>`;
}

// ---------------------------------------------------------------- Gliederung (gliederung.py)

const kvList = () => store.data.kv.filter((k) => k.code !== 'LV');
const kvShort = (code) => store.data.kv.find((k) => k.code === code)?.name || code;

function gliederung() {
  const ovs = store.data.ovs || [];
  const members = (code) => store.data.members.filter((m) => m.kv === code).length;
  const shown = ovs.filter((o) => !ovKv || o.kv === ovKv);
  return html`<div style="display:flex;flex-direction:column;gap:16px">
    <form class="panel" id="kv-numbers" style="display:flex;flex-direction:column;gap:12px">
      <h2>Kreisverbände</h2>
      <p class="small muted">Die Kreisverbände sind fest (Kostenstellen für Buchungen und Budgets). Die Nummer der CDU-Mitgliederverwaltung
        (Spalte LVKV_Nummer, z. B. 701) ordnet beim Import einer Mitgliederliste den Kreisverband zu.</p>
      ${table(['Kreisverband', 'Kostenstelle', 'Nr. Mitgliederverwaltung', { label: 'Ortsverbände', className: 'num' }, { label: 'Mitglieder', className: 'num' }],
        kvList().map((k) => html`<tr><td>${k.name}</td><td>${k.code}</td>
          <td><input name="${k.code}" value="${k.cdu_number || ''}" inputmode="numeric" maxlength="6" autocomplete="off" aria-label="Nummer der Mitgliederverwaltung für ${k.name}" style="max-width:110px"></td>
          <td class="num">${ovs.filter((o) => o.kv === k.code).length}</td><td class="num">${members(k.code)}</td></tr>`))}
      <div class="form-actions"><button type="submit" class="btn primary">Nummern speichern</button></div>
    </form>
    <section class="panel" style="display:flex;flex-direction:column;gap:12px">
      <div class="panel-head"><h2>Ortsverbände · ${ovs.length}</h2>
        <span style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
          <label class="field inline">Kreisverband<select id="ov-kv"><option value="">alle</option>
            ${kvList().map((k) => html`<option value="${k.code}" ${k.code === ovKv ? 'selected' : ''}>${k.name}</option>`)}</select></label>
          <button type="button" class="btn" id="ov-import">${icon('upload', 16)} Gliederung importieren</button>
          <button type="button" class="btn primary" id="ov-new">${icon('plus', 16)} Ortsverband anlegen</button></span></div>
      <p class="small muted">Im Mitglied ist nur ein Ortsverband des gewählten Kreisverbands auswählbar. Nummer 0 = ohne Nummer (z. B. „Kreisverband“
        oder „OV Schüler Union“ aus der Mitgliederverwaltung). Ein Ortsverband mit Mitgliedern lässt sich nicht löschen, nur deaktivieren – er ist dann
        nicht mehr auswählbar, vorhandene Zuordnungen bleiben.</p>
      ${shown.length ? table(['Kreisverband', { label: 'Nr.', className: 'num' }, 'Ortsverband', { label: 'Mitglieder', className: 'num' }, 'Status', ''],
        shown.map((o) => html`<tr class="${o.active ? '' : 'muted'}"><td>${kvShort(o.kv)}</td><td class="num">${o.number || '—'}</td><td>${o.name}</td>
          <td class="num">${o.members}</td><td>${o.active ? badge('aktiv', 'ok') : badge('deaktiviert', '')}</td>
          <td class="nowrap"><button type="button" class="btn small" data-ov-edit="${o.id}">Bearbeiten</button>
            <button type="button" class="btn small" data-ov-toggle="${o.id}">${o.active ? 'Deaktivieren' : 'Aktivieren'}</button>
            ${o.members ? '' : html`<button type="button" class="btn small ghost" data-ov-delete="${o.id}">Löschen</button>`}</td></tr>`))
        : empty('Keine Ortsverbände', 'Einzeln anlegen oder die Gliederung aus dem Export der Mitgliederverwaltung importieren.')}
    </section></div>`;
}

function ovDialog(o = null) {
  const locked = o && o.members > 0;   // Kreisverband nur ohne zugeordnete Mitglieder änderbar
  const el = dialog(o ? `Ortsverband bearbeiten · ${ovLabel(o)}` : 'Ortsverband anlegen', html`<form id="ov-form" style="display:flex;flex-direction:column;gap:14px">
    <div class="formgrid">
      <label class="field">Kreisverband<select name="kv" required ${locked ? 'disabled' : ''}>
        ${o ? '' : html`<option value="" ${ovKv ? '' : 'selected'} disabled>Bitte wählen</option>`}
        ${kvList().map((k) => html`<option value="${k.code}" ${k.code === (o ? o.kv : ovKv) ? 'selected' : ''}>${k.name}</option>`)}</select>
        ${locked ? html`<span class="small muted">${o.members} Mitglieder zugeordnet – Kreisverband nicht änderbar.</span>` : ''}</label>
      <label class="field">Nummer<input name="number" inputmode="numeric" maxlength="4" autocomplete="off" value="${o ? o.number : ''}" placeholder="0 = ohne Nummer"></label>
      <label class="field span">Name<input name="name" required maxlength="100" autocomplete="off" value="${o ? o.name : ''}" placeholder="z. B. Blankenese"></label>
    </div>
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  const form = el.querySelector('#ov-form');
  el.querySelector('[data-cancel]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const f = form.elements;   // form.name wäre der Name des Formulars, nicht das Feld
      await api('/api/ortsverband', { id: o ? o.id : null, kv: locked ? o.kv : f.namedItem('kv').value, number: f.namedItem('number').value, name: f.namedItem('name').value });
      el.close();
      await refresh();
      toast(o ? 'Ortsverband gespeichert.' : 'Ortsverband angelegt.');
    });
  };
}

const ACTION = { new: ['neu', 'ok'], same: ['vorhanden', ''] };

function importDialog() {
  const el = dialog('Gliederung importieren', html`<div class="filebox"><label class="field">Excel (.xlsx, .xls) oder CSV
      <input id="ov-file" type="file" accept=".xlsx,.xls,.csv,.txt"></label>
      <p class="small muted">Export der Mitgliederverwaltung mit den Spalten LVKV_Nummer bzw. LVKV_Name, Ortsverband und Ortsbezeichnung – eine Zeile je
        Mitglied oder je Ortsverband; auch kennwortgeschützt (das Kennwort wird abgefragt und nicht gespeichert). Angelegt werden nur fehlende
        Ortsverbände, vorhandene bleiben unverändert. Vorher wird automatisch gesichert.</p></div>
    <div id="ov-preview"></div>`, { wide: true });
  el.querySelector('#ov-file').onchange = (e) => safe(async () => {
    const file = e.target.files[0];
    if (!file) return;
    const box = el.querySelector('#ov-preview');
    mount(box, '');
    const p = await uploadProtected('/api/gliederung-vorschau', file, box);
    if (!p) return;   // inzwischen andere Datei gewählt
    mount(box, html`<h3>Vorschau „${p.filename}“ · ${p.counts.rows} Zeilen</h3>
      <p class="muted">${p.counts.new} Ortsverbände neu · ${p.counts.same} schon vorhanden${p.counts.errors ? ` · ${p.counts.errors} Zeilen mit Fehler` : ''}.
        Kreisverbände: ${p.kvs.map((k, i) => html`${i ? ' · ' : ''}${k.file_number || k.file_name} → ${kvShort(k.code)}`)}</p>
      ${p.kvs.some((k) => k.note) ? html`<p class="notice warn">${p.kvs.filter((k) => k.note).map((k) => k.note).join(' ')}</p>` : ''}
      ${p.errors.length ? html`<details class="notice warn"><summary>${p.errors.length} Zeilen nicht übernommen</summary>
        <p class="small">${p.errors.slice(0, 50).map((x, i) => html`${i ? ' · ' : ''}Zeile ${x.row_no}: ${x.error}`)}</p></details>` : ''}
      ${table(['Kreisverband', { label: 'Nr.', className: 'num' }, 'Ortsverband', { label: 'Zeilen', className: 'num' }, 'Prüfung'], p.items.map((x) => html`<tr>
        <td>${kvShort(x.kv)}</td><td class="num">${x.number || '—'}</td><td>${x.name}</td><td class="num">${x.rows}</td>
        <td>${badge(ACTION[x.action][0], ACTION[x.action][1])}${x.active ? '' : html` ${badge('deaktiviert', '')}`}${x.note ? html`<span class="sub over">${x.note}</span>` : ''}</td></tr>`))}
      <div class="form-actions"><button type="button" class="btn primary" id="ov-confirm" ${p.counts.new ? '' : 'disabled'}>${p.counts.new} Ortsverbände anlegen</button></div>`);
    const confirm = box.querySelector('#ov-confirm');
    confirm.onclick = () => safe(async () => {
      if (confirm.disabled) return;
      confirm.disabled = true;
      try {
        const r = await api('/api/gliederung-import', { token: p.token });
        el.close();
        await refresh();
        toast(`${r.created} Ortsverbände angelegt. Sicherung: ${r.backup}`);
      } finally { confirm.disabled = false; }
    });
  });
}

function versand(s, status) {
  const local = ['127.0.0.1', 'localhost'].includes(s.smtp_host);
  const server = store.mode === 'server';
  const pw = status?.password === 'schluesselbund' ? html`<span class="badge ok">Im Schlüsselbund gespeichert</span>`
    : status?.password === 'datei' ? html`<span class="badge ok">Auf dem Server gespeichert</span>`
      : status?.password === 'sitzung' ? html`<span class="badge warn">${server ? 'Nur bis zum Neustart des Servers gespeichert' : 'Nur bis zum Beenden gespeichert'}</span>` : html`<span class="badge danger">Kein Passwort hinterlegt</span>`;
  return html`<div class="split-row">
    <form data-save class="panel grow2" style="display:flex;flex-direction:column;gap:16px">
      <div class="formgrid">
        ${field('Absendername', 'mail_from_name', s.mail_from_name)}
        ${field('Absenderadresse', 'mail_from', s.mail_from, 'type="email" placeholder="rechnungen@…"')}
        ${field('Postausgangsserver (SMTP)', 'smtp_host', s.smtp_host)}
        <label class="field">Verschlüsselung<select name="smtp_security">
          <option value="ssl" ${s.smtp_security === 'ssl' ? 'selected' : ''}>SSL/TLS (Port 465)</option>
          <option value="starttls" ${s.smtp_security === 'starttls' ? 'selected' : ''}>STARTTLS (Port 587)</option>
          ${local || s.smtp_security === 'none' ? html`<option value="none" ${s.smtp_security === 'none' ? 'selected' : ''}>Ohne (nur Testserver auf diesem Rechner)</option>` : ''}</select></label>
        ${field('Port', 'smtp_port', s.smtp_port, 'inputmode="numeric"')}
        ${field('Benutzername', 'smtp_user', s.smtp_user, 'autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"', 'Bei Strato die vollständige E-Mail-Adresse.')}
        ${field('Antworten an', 'mail_reply_to', s.mail_reply_to, 'type="email"', 'optional')}
        ${field('Kopie jeder Rechnung an (Bcc)', 'mail_bcc', s.mail_bcc, 'type="email"', 'z. B. die Absenderadresse – so liegt jede versendete Rechnung im Postfach.')}
        ${field('Pause zwischen E-Mails (Sekunden)', 'mail_pause', s.mail_pause, 'inputmode="numeric"', 'Schont das Versandlimit des Postfachs.')}
      </div>
      ${save}</form>
    <div class="grow1" style="display:flex;flex-direction:column;gap:16px">
      <section class="panel" style="display:flex;flex-direction:column;gap:12px"><h2>Passwort</h2>
        <p>${pw}</p>
        <form id="pw-form" style="display:flex;flex-direction:column;gap:10px">
          <label class="field">Passwort des E-Mail-Kontos<input type="password" name="password" autocomplete="new-password" required></label>
          <label class="check"><input type="checkbox" name="remember" ${status?.keychain ? 'checked' : 'disabled'}> ${server ? 'Auf dem Server speichern (nur für das Programm lesbar)' : 'Im Schlüsselbund dieses Macs speichern'}</label>
          ${status?.keychain ? '' : html`<p class="small muted">Kein Schlüsselbund verfügbar – das Passwort gilt bis zum Beenden des Programms.</p>`}
          <div class="form-actions"><button type="submit" class="btn primary">Passwort speichern</button>
            ${status?.password ? html`<button type="button" class="btn ghost" id="pw-forget">Entfernen</button>` : ''}</div>
        </form>
        <p class="small muted">Das Passwort wird nie in der Datenbank oder in Sicherungen abgelegt.</p></section>
      <section class="panel" style="display:flex;flex-direction:column;gap:10px"><h2>Prüfen</h2>
        <button type="button" class="btn" id="mail-check">${icon('check', 16)} Verbindung prüfen</button>
        <button type="button" class="btn" id="mail-test">Testmail an mich senden</button>
        <p class="small muted">Strato: Server smtp.strato.de, SSL/TLS auf Port 465, Benutzername = vollständige E-Mail-Adresse. Die Testmail geht nur an die Absenderadresse.</p></section>
    </div></div>`;
}

function template(kind, s) {
  if (kind === 'reminder') return html`<section class="panel" style="display:flex;flex-direction:column;gap:12px">
    <div class="panel-head"><h2>Zahlungserinnerung</h2>
      <span><button type="button" class="btn small" data-preview-mail="reminder">Vorschau E-Mail</button></span></div>
    <div class="formgrid">
      ${field('Erste Erinnerung frühestens (Tage nach Fälligkeit)', 'reminder_after', s.reminder_after, 'inputmode="numeric"')}
      ${field('Abstand zwischen Erinnerungen (Tage)', 'reminder_gap', s.reminder_gap, 'inputmode="numeric"', 'mindestens 7')}
      ${field('Neue Zahlungsfrist (Tage ab Erinnerung)', 'reminder_terms', s.reminder_terms, 'inputmode="numeric"', 'mindestens 7')}
    </div>
    <label class="field">Betreff<input name="tpl_reminder_subject" value="${s.tpl_reminder_subject}"></label>
    ${area('E-Mail-Text', 'tpl_reminder_body', s.tpl_reminder_body, 10, 'Angehängt wird die ursprüngliche Rechnung als PDF; der Kasten in der E-Mail nennt offenen Betrag und neue Frist.')}
  </section>`;
  const label = kind === 'dues' ? 'Beitragsrechnung' : 'Rechnung (z. B. Veranstaltung)';
  return html`<section class="panel" style="display:flex;flex-direction:column;gap:12px">
    <div class="panel-head"><h2>${label}</h2>
      <span><button type="button" class="btn small" data-preview-mail="${kind}">Vorschau E-Mail</button>
      <a class="btn small" href="/api/preview.pdf?sample=${kind}" target="_blank" rel="noopener" data-preview-pdf>Vorschau PDF</a></span></div>
    <label class="field">Betreff<input name="tpl_${kind}_subject" value="${s[`tpl_${kind}_subject`]}"></label>
    ${area('E-Mail-Text', `tpl_${kind}_body`, s[`tpl_${kind}_body`], 10)}
    ${area('Text auf der Rechnung (nach „Hallo …,“)', `pdf_${kind}_text`, s[`pdf_${kind}_text`], 3)}
    ${kind === 'dues' ? area('E-Mail-Text bei SEPA-Lastschrift', 'tpl_debit_body', s.tpl_debit_body, 9, 'Für Mitglieder mit Mandat – enthält die Vorabankündigung ({einzug}, {konto}, {mandat}, {glaeubiger}).') : ''}
  </section>`;
}

function vorlagen(s) {
  return html`<form data-save style="display:flex;flex-direction:column;gap:16px">
    <section class="panel"><h2 style="margin-bottom:10px">Platzhalter</h2>
      <div class="placeholder-list">${PLACEHOLDERS.map(([k, l]) => html`<span><code>{${k}}</code> <span class="small muted">${l}</span></span>`)}</div>
      <p class="small muted" style="margin-top:10px">Die E-Mail erhält zusätzlich automatisch einen Kasten mit Rechnungsnummer, Betrag, Fälligkeit und IBAN sowie die Rechnung als PDF.</p></section>
    ${template('dues', s)}${template('normal', s)}${template('reminder', s)}
    ${save}</form>`;
}

function sicherung() {
  const server = store.mode === 'server';
  return html`<div class="split-row">
    <section class="panel grow1"><h2 style="margin-bottom:8px">Datensicherung</h2>
      <p class="small muted" style="margin-bottom:12px">${server
        ? 'Der Server sichert täglich automatisch (30 Tage), vor jedem Update und vor Importen. Zusätzlich hier regelmäßig eine ZIP-Datei mit Datenbank, Belegen und importierten Originaldateien herunterladen und getrennt vom Server aufbewahren.'
        : 'Das Programm sichert beim Start täglich automatisch und vor jedem Update (Ordner „Sicherungen“). Zusätzlich hier eine ZIP-Datei mit Datenbank, Belegen und importierten Originaldateien erstellen und getrennt aufbewahren.'}</p>
      <button type="button" class="btn" id="backup-download">${icon('download', 16)} Sicherung herunterladen</button></section>
    <section class="panel grow1"><h2 style="margin-bottom:8px">Wiederherstellen</h2>
      <p class="small muted">${server
        ? html`Auf dem Server mit <code>sudo ju-finanzen uebernehmen &lt;ZIP-Datei&gt; --ersetzen</code>. Die bisherigen Daten werden vorher vollständig zur Seite gelegt, nichts wird gelöscht.`
        : html`Die ZIP-Datei enthält den Ordner „Daten“. Für den Umzug auf den Server: <code>sudo ju-finanzen uebernehmen &lt;ZIP-Datei&gt;</code> (siehe SERVER.md).`}</p></section></div>`;
}

function zugang() {
  return html`<div class="split-row">
    <form class="panel grow1" id="password-form" style="display:flex;flex-direction:column;gap:12px">
      <h2>Passwort ändern</h2>
      <input type="text" name="username" autocomplete="username" value="${store.user}" hidden>
      <label class="field">Bisheriges Passwort<input type="password" name="current" autocomplete="current-password" required></label>
      <label class="field">Neues Passwort<input type="password" name="new" autocomplete="new-password" minlength="12" required>
        <span class="small muted">Mindestens 12 Zeichen. Ein Satz aus mehreren Wörtern ist gut zu merken.</span></label>
      <label class="field">Neues Passwort wiederholen<input type="password" name="repeat" autocomplete="new-password" minlength="12" required></label>
      <div class="form-actions"><button type="submit" class="btn primary">Passwort ändern</button></div>
    </form>
    <section class="panel grow1"><h2 style="margin-bottom:8px">Hinweise</h2>
      <p class="small muted">Angemeldet als <strong>${store.user}</strong>. Nach dem Ändern enden alle anderen Anmeldungen dieses Benutzers.
        Ohne Aktivität endet eine Anmeldung nach 2 Stunden, spätestens nach 8 Stunden. Passwort vergessen: auf dem Server
        <code>sudo ju-finanzen passwort "${store.user}"</code>.</p></section></div>`;
}

function collect(form) {
  const data = Object.fromEntries(new FormData(form));
  for (const name of ['invoice_ready', 'dues_prorate', 'kv_share_auto', 'map_tiles']) if (form.elements[name]) data[name] = form.elements[name].checked ? 'yes' : 'no';
  if (data.kv_share_auto === 'no') delete data.kv_share_auto_from;   // Schalter aus: kein Jahr speichern, das noch niemand entschieden hat
  return data;
}

export async function settings(main) {
  const s = store.data.settings;
  if (store.settingsTab) { tab = store.settingsTab; store.settingsTab = null; }   // Sprung vom Start
  if (!tabs().some(([id]) => id === tab)) tab = tabs()[0][0];   // Reiter ohne Recht: auf den ersten erlaubten
  const status = tab === 'versand' ? await safe(() => api('/api/mail-status')) : null;
  if (!tabs().some(([id]) => id === tab)) tab = 'schalter';
  const views = { schalter, verein, beitraege, gliederung, versand: (x) => versand(x, status), vorlagen, sicherung, zugang };
  mount(main, html`<div class="page">
    ${pageHead('Verwaltung · Schalter und Stammdaten für Rechnungen, Beiträge, Gliederung und Versand', 'Einstellungen')}
    <div class="tabs" role="tablist" aria-label="Bereich">${tabs().map(([id, label]) => html`
      <button type="button" role="tab" data-tab="${id}" aria-selected="${tab === id}">${label}</button>`)}</div>
    ${views[tab](s)}
  </div>`);
  const rerender = () => settings(main);
  bindAccountPickers(main);
  const liability = main.querySelector('input[name="liability_account"]');
  // vorhandenes Konto gewählt: dessen Bezeichnung übernehmen, damit Speichern es nicht umbenennt
  if (liability) liability.addEventListener('change', () => {
    const known = store.data.accounts.find((a) => a.code === liability.value.trim());
    if (known) main.querySelector('input[name="liability_name"]').value = known.name;
  });
  const form = main.querySelector('form[data-save]');
  main.querySelectorAll('.switch input').forEach((box) => box.addEventListener('change', () => {
    box.closest('.switch').querySelector('[data-switch-label]').textContent = box.checked ? 'An' : 'Aus';
  }));
  const addressLine = main.querySelector('[data-address-state]');   // Schalter: Stand der amtlichen Adressdaten, Laden auf Knopfdruck
  if (addressLine) {
    const button = main.querySelector('[data-address-load]');
    const show = (state) => { if (addressLine.isConnected) addressLine.textContent = addressSummary(state); };
    const load = (start = true) => safe(async () => {
      button.disabled = true;
      try {
        const state = await loadAddressData(show, () => addressLine.isConnected, { start });
        if (!addressLine.isConnected) return;           // Reiter inzwischen verlassen: nichts melden, nichts neu zeichnen
        toast(loadedText(state));
        store.data.address_data = true;                 // der Start kennt den neuen Stand auch, wenn hier nicht neu gezeichnet wird
        if (!dirty) await refresh();                    // Start und Karte kennen den neuen Stand; ungespeicherte Schalter bleiben stehen
      } finally { button.disabled = false; }
    });
    button.onclick = () => load();
    safe(async () => {
      const state = await addressState();
      show(state);
      if (state.job.state === 'running') load(false);   // läuft schon: nur verfolgen, nicht noch einmal starten
      else if (state.job.state === 'error' && addressLine.isConnected) addressLine.textContent = `${addressSummary(state)} Letzter Abruf: ${state.job.error}`;
    });
  }
  const numbers = main.querySelector('#kv-numbers');   // Gliederung: Nummern der Mitgliederverwaltung
  let dirty = false;   // ungespeicherte Eingaben: vor dem Reiterwechsel nachfragen
  (form || numbers)?.addEventListener('input', () => { dirty = true; });
  main.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => {
    if (b.dataset.tab === tab) return;
    if (!dirty) { tab = b.dataset.tab; rerender(); return; }
    const el = dialog('Änderungen nicht gespeichert', html`<p>In diesem Reiter gibt es ungespeicherte Eingaben.</p>
      <div class="form-actions"><button type="button" class="btn ghost" data-stay>Zurück</button>
        <button type="button" class="btn" data-drop>Verwerfen</button><button type="button" class="btn primary" data-keep>Speichern und wechseln</button></div>`);
    el.querySelector('[data-stay]').onclick = () => el.close();
    el.querySelector('[data-drop]').onclick = () => { el.close(); tab = b.dataset.tab; rerender(); };
    el.querySelector('[data-keep]').onclick = () => safe(async () => { await persist(); el.close(); toast('Einstellungen gespeichert.'); tab = b.dataset.tab; rerender(); });
  }));
  const persist = async () => {
    if (numbers) await api('/api/kv-nummern', { numbers: Object.fromEntries(new FormData(numbers)) });
    else await api('/api/settings', collect(form));
    dirty = false;
    await refresh();
  };
  if (form) form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await persist();
      toast('Einstellungen gespeichert.');
    });
  };
  main.querySelectorAll('[data-preview-mail]').forEach((b) => (b.onclick = () => safe(async () => {
    await persist();
    mailPreview('sample=' + b.dataset.previewMail, 'Vorschau mit Beispieldaten');
  })));
  main.querySelectorAll('[data-preview-pdf]').forEach((a) => (a.onclick = () => { safe(persist); }));
  if (numbers) numbers.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await persist();
      toast('Nummern gespeichert.');
    });
  };
  main.querySelector('#ov-kv')?.addEventListener('change', (e) => { ovKv = e.target.value; rerender(); });
  main.querySelector('#ov-new')?.addEventListener('click', () => ovDialog());
  main.querySelector('#ov-import')?.addEventListener('click', importDialog);
  const ovOf = (id) => store.data.ovs.find((o) => o.id === Number(id));
  main.querySelectorAll('[data-ov-edit]').forEach((b) => (b.onclick = () => ovDialog(ovOf(b.dataset.ovEdit))));
  main.querySelectorAll('[data-ov-toggle]').forEach((b) => (b.onclick = () => safe(async () => {
    const o = ovOf(b.dataset.ovToggle);
    await api('/api/ortsverband-status', { id: o.id, active: !o.active });
    await refresh();
    toast(o.active ? `„${ovLabel(o)}“ deaktiviert – nicht mehr auswählbar.` : `„${ovLabel(o)}“ wieder aktiv.`);
  })));
  main.querySelectorAll('[data-ov-delete]').forEach((b) => (b.onclick = () => {
    const o = ovOf(b.dataset.ovDelete);
    confirmAction('Ortsverband löschen', `„${ovLabel(o)}“ (${kvShort(o.kv)}) endgültig löschen? Ihm sind keine Mitglieder zugeordnet.`,
      () => api('/api/ortsverband-loeschen', { id: o.id }), { label: 'Löschen', done: 'Ortsverband gelöscht.' });
  }));
  const pwForm = main.querySelector('#pw-form');
  if (pwForm) pwForm.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const r = await api('/api/mail-password', { password: pwForm.password.value, remember: pwForm.remember.checked });
      toast(r.where === 'schluesselbund' ? 'Passwort im Schlüsselbund gespeichert.' : r.where === 'datei' ? 'Passwort auf dem Server gespeichert.' : 'Passwort gilt bis zum Beenden des Programms.');
      rerender();
    });
  };
  const backup = main.querySelector('#backup-download');
  if (backup) backup.onclick = () => safe(async () => {
    backup.disabled = true;
    try {
      const today = new Date().toISOString().slice(0, 10);
      if (await download('/api/backup', `JU-Sicherung-${today}.zip`)) toast('Sicherung heruntergeladen.');
    } finally { backup.disabled = false; }
  });
  const pass = main.querySelector('#password-form');
  if (pass) pass.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      if (pass.new.value !== pass.repeat.value) throw new Error('Die beiden neuen Passwörter stimmen nicht überein.');
      await api('/api/password', { current: pass.current.value, new: pass.new.value, repeat: pass.repeat.value });
      pass.reset();
      toast('Passwort geändert.');
    });
  };
  const forget = main.querySelector('#pw-forget');
  if (forget) forget.onclick = () => safe(async () => { await api('/api/mail-forget', {}); toast('Passwort entfernt.'); rerender(); });
  const check = main.querySelector('#mail-check');
  if (check) check.onclick = () => safe(async () => {
    check.disabled = true;
    try {
      await api('/api/mail-check', {});
      toast('Verbindung und Anmeldung erfolgreich.');
    } finally { check.disabled = false; }
  });
  const test = main.querySelector('#mail-test');
  if (test) test.onclick = () => safe(async () => {
    test.disabled = true;
    try {
      const r = await api('/api/mail-test', { sample: 'dues' });
      toast(`Testmail an ${r.to} gesendet.`);
    } finally { test.disabled = false; }
  });
}
