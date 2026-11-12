// Eingangsrechnungen (Verbindlichkeiten): erfassen, Zahlungsvorschlag mit Überweisungsdaten zum Kopieren, Storno.
import { api, fileToBase64 } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date, parseCents, todayIso, toInput } from '../format.js';
import { store, refresh, kvName, openBills, daysOverdue } from '../store.js';
import { pageHead, icon, table, empty, badge, dialog, overlay, confirmAction, safe, toast } from '../ui.js';
import { postenField, bindPosten, postenCodes, setPosten } from '../account-picker.js';
import { rpjField, bindRpj, rpjKind } from '../rpj-field.js';
import { reconcileDialog } from './reconcile.js';

let filter = 'open';
const option = (value, label, selected) => html`<option value="${value}" ${selected ? 'selected' : ''}>${label}</option>`;

/** „2026-11-30“ + 3 Monate → „2027-02-28“ (nur Anzeige; die Frist prüft der Server). */
function addMonths(iso, n) {
  const [y, m, d] = iso.split('-').map(Number);
  const first = new Date(Date.UTC(y, m - 1 + n, 1));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  return `${first.getUTCFullYear()}-${String(first.getUTCMonth() + 1).padStart(2, '0')}-${String(Math.min(d, last)).padStart(2, '0')}`;
}

export function billBadge(b) {
  if (b.status === 'cancelled') return badge('Storniert');
  if (b.payment_reversed && b.open > 0) return badge('Zahlung storniert – prüfen', 'danger');
  if (b.open === 0) return badge(b.written_off ? 'Bezahlt (Rest ausgebucht)' : 'Bezahlt', 'ok');
  if (b.in_bank) return badge('Abbuchung im Kontoauszug – abgleichen', 'warn');
  if (b.paid > 0) return badge('Teilweise bezahlt', 'warn');
  if (b.transferred) return badge('Angewiesen ' + date(b.transferred).slice(0, 6), 'ok');
  const days = daysOverdue(b.due);
  return days > 0 ? badge(`${days} Tage überfällig`, 'danger') : badge(days >= -7 ? 'Bald fällig' : 'Offen', 'warn');
}

/** Freigabestand nach § 7 Abs. 4 Finanzordnung (nur Fremdrechnungen über der Grenze). */
export function approvalBadge(b) {
  const a = b.approval;
  if (!a?.required || b.status !== 'open') return '';
  if (a.complete) return a.late ? badge(a.late === 'before' ? 'Zahlung vor Freigabe' : 'Freigabe nach Zahlung eingetragen', 'warn') : badge('Freigegeben', 'ok');
  if (a.unapproved_paid) return badge('Ohne Freigabe angewiesen/bezahlt', 'danger');
  return badge(`Freigabe ${2 - a.missing.length}/2`, 'warn');
}

const limitCents = () => Number(store.data.settings.approval_limit);

const FILTERS = [
  ['open', 'Offen', (b) => b.status === 'open' && b.open > 0 && !b.transferred],
  ['transferred', 'Angewiesen', (b) => b.status === 'open' && b.open > 0 && b.transferred],
  ['paid', 'Bezahlt', (b) => b.status === 'open' && b.open === 0],
  ['cancelled', 'Storniert', (b) => b.status === 'cancelled'],
  ['all', 'Alle', () => true],
];

// ---------------------------------------------------------------- Erfassen

function billDialog() {
  const codes = postenCodes('expense');
  const creditors = [...new Set(store.data.bills.map((b) => b.creditor))].sort();
  const today = todayIso();
  const lineRow = (l = {}) => html`<div class="line-row bill-line">
    <input data-key="description" placeholder="Leistung, z. B. Flyer Sommerfest" value="${l.description || ''}" aria-label="Beschreibung">
    <input data-key="amount" inputmode="decimal" placeholder="Betrag €" value="${l.amount || ''}" aria-label="Betrag">
    ${postenField({ kind: 'expense', account: l.account || '', codes, key: true })}
    <button type="button" class="btn ghost icon-btn" data-drop aria-label="Position entfernen">${icon('trash', 16)}</button>
    ${rpjField({ key: true })}</div>`;
  const members = [...store.data.members].sort((a, b) => a.name.localeCompare(b.name, 'de'));
  const el = dialog('Eingangsrechnung erfassen', html`<form id="bill-form" novalidate style="display:flex;flex-direction:column;gap:16px">
    <div class="einvoice-drop"><label class="field">E-Rechnung einlesen (XRechnung oder ZUGFeRD/Factur-X)<input type="file" id="einvoice-file" accept=".xml,.pdf"></label>
      <p class="small muted">Füllt das Formular aus der Rechnung aus und hängt sie als Beleg an – den Haushaltsposten wählst du selbst.</p>
      <div id="einvoice-result"></div></div>
    <div class="formgrid">
      <label class="field">Art<select name="kind">${option('external', 'Fremdrechnung (Finanzordnung § 7)', true)}${option('refund', 'Auslagenerstattung eines Mitglieds (§ 8)', false)}</select></label>
      <span></span>
    </div>
    <div class="formgrid refund-fields" id="refund-fields" hidden>
      <label class="field">Antragsteller (Mitglied)<select name="member_id">${option('', 'Mitglied wählen', true)}${members.map((m) => option(String(m.id), `${m.name} (${m.number})`, false))}</select></label>
      <label class="field">Antrag eingegangen am<input type="date" name="submitted_on" value="${today}" max="${today}"></label>
      <fieldset class="field span checks"><legend>Bestätigungen nach Finanzordnung § 8</legend>
        <label class="check"><input type="checkbox" name="signed"> Antrag vom Antragsteller unterschrieben</label>
        <label class="check"><input type="checkbox" name="complete"> Antrag vollständig, Nachweis liegt bei</label>
        <label class="check"><input type="checkbox" name="authorized"> Ausgabe für JU-Aufgaben, Mitglied war befugt</label>
        <label class="check"><input type="checkbox" name="no_other"> keine andere Erstattungsmöglichkeit</label></fieldset>
      <label class="field span" id="late-field" hidden>Begründung der Ausnahme – Antrag mehr als drei Monate nach der Ausgabe<input name="late_reason" placeholder="z. B. Beschluss des geschäftsführenden Landesvorstands vom …"></label>
      <p class="small muted span">Antragsformular als PDF: <a href="/api/refund-form.pdf" target="_blank" rel="noopener" id="refund-form-link">leer</a> – erstattet wird auf das Konto des Antragstellers (IBAN unten).</p>
    </div>
    <div class="formgrid">
      <label class="field">Rechnungssteller (Kreditor)<input name="creditor" list="creditors" required autocomplete="off">
        <datalist id="creditors">${creditors.map((c) => html`<option value="${c}"></option>`)}</datalist></label>
      <label class="field">Rechnungsnummer des Kreditors<input name="ext_number" autocomplete="off"></label>
      <label class="field">IBAN des Kreditors<input name="iban" autocomplete="off" placeholder="für den Zahlungsvorschlag"></label>
      <label class="field">BIC<input name="bic" autocomplete="off" placeholder="optional"></label>
      <label class="field"><span id="issued-label">Rechnungsdatum</span><input type="date" name="issued" value="${today}" required></label>
      <label class="field">Fällig am<input type="date" name="due"><span class="small muted" id="due-hint">leer = 14 Tage nach Rechnungsdatum</span></label>
      <label class="field">Bereich<select name="kv">${store.data.kv.map((k) => option(k.code, k.name, k.code === 'LV'))}</select></label>
      <label class="field">Verwendungszweck der Überweisung<input name="reference" placeholder="leer = Rechnungsnummer"></label>
    </div>
    <h3>Positionen</h3>
    <div id="bill-lines" style="display:flex;flex-direction:column;gap:12px">${lineRow()}</div>
    <div><button type="button" class="link" id="add-bill-line">${icon('plus', 14)} Position</button></div>
    <div class="formgrid">
      <label class="field"><span id="file-label">Beleg (PDF, PNG, JPG – optional)</span><input type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,.xml"></label>
      <label class="field">Notiz<input name="note" placeholder="optional"></label>
    </div>
    <p class="notice warn" id="bill-approval-hint" hidden>Fremdrechnung über ${eur(limitCents())}: Vor der Zahlung geben Landesvorsitz und Landesschatzmeister frei (§ 7 Abs. 4) – bis dahin ist sie im Zahlungsvorschlag gesperrt.</p>
    <div class="bulkbar"><strong id="bill-total">Summe ${eur(0)}</strong>
      <button type="submit" class="btn primary">Erfassen und buchen</button></div>
    <p class="small muted">Gebucht wird Aufwand an Verbindlichkeiten (${store.data.settings.liability_account}) im Haushaltsjahr des Rechnungsdatums. Die spätere Abbuchung wird im Buchungsplatz oder über den Abgleich zugeordnet – nicht noch einmal auf ein Sachkonto buchen.</p>
  </form>`, { wide: true });
  const form = el.querySelector('#bill-form');
  const linesEl = form.querySelector('#bill-lines');
  const total = () => [...linesEl.children].reduce((s, row) => s + (parseCents(row.querySelector('[data-key=amount]').value) || 0), 0);
  const showTotal = () => {
    form.querySelector('#bill-total').textContent = 'Summe ' + eur(total());
    form.querySelector('#bill-approval-hint').hidden = !(form.kind.value === 'external' && total() > limitCents());
  };
  const billYear = () => Number((form.issued.value || today).slice(0, 4));   // Haushaltsjahr = Jahr des Rechnungsdatums
  const bind = (row) => {
    const rpjBox = row.querySelector('[data-rpj]');
    bindPosten(row, { kv: () => form.kv.value, onChange: () => rpjBox.refresh() });
    bindRpj(rpjBox, { kind: () => rpjKind(row.querySelector('[data-key=account]').value, row.querySelector('[data-key=title]').value), year: billYear });
    const retitle = () => { row.querySelector('[data-posten]').refresh(); rpjBox.refresh(); };
    row.querySelector('[data-key=amount]').oninput = showTotal;
    row.querySelector('[data-drop]').onclick = () => { if (linesEl.children.length > 1) row.remove(); showTotal(); };
    row.retitle = retitle;
  };
  [...linesEl.children].forEach(bind);
  // Auslagenerstattung (§ 8): Antragsteller, Eingang, Bestätigungen; Datum = Datum der Ausgabe (Drei-Monats-Frist)
  const lateCheck = () => {
    const issued = form.issued.value, submitted = form.submitted_on.value;
    form.querySelector('#late-field').hidden = !(form.kind.value === 'refund' && issued && submitted && submitted > addMonths(issued, 3));
  };
  const kindChanged = (manual = false) => {
    const refund = form.kind.value === 'refund';
    if (manual) {  // Art gewechselt: Bankdaten gehören zum Kreditor bzw. Antragsteller – nicht übernehmen
      for (const key of ['iban', 'bic', 'reference', 'due']) form[key].value = '';
      form.creditor.value = refund ? (members.find((x) => String(x.id) === form.member_id.value)?.name || '') : '';
    }
    form.querySelector('#refund-fields').hidden = !refund;
    form.creditor.readOnly = refund;  // Erstattung: Kreditor ist der Antragsteller
    form.querySelector('#issued-label').textContent = refund ? 'Datum der Ausgabe (Beleg)' : 'Rechnungsdatum';
    form.querySelector('#due-hint').textContent = refund ? 'leer = 14 Tage nach Antragseingang' : 'leer = 14 Tage nach Rechnungsdatum';
    form.querySelector('#file-label').textContent = refund ? 'Nachweis (Kassenbeleg, Rechnung – Pflicht)' : 'Beleg (PDF, PNG, JPG, E-Rechnung – optional)';
    lateCheck();
    showTotal();
  };
  form.kind.onchange = () => kindChanged(true);
  form.issued.addEventListener('change', lateCheck);
  form.issued.addEventListener('change', () => [...linesEl.children].forEach((r) => r.querySelector('[data-rpj]').refresh()));   // anderes Jahr: andere Projekte
  form.submitted_on.onchange = lateCheck;
  form.member_id.onchange = () => {
    const m = members.find((x) => String(x.id) === form.member_id.value);
    form.querySelector('#refund-form-link').href = m ? `/api/refund-form.pdf?member=${m.id}` : '/api/refund-form.pdf';
    form.querySelector('#refund-form-link').textContent = m ? `für ${m.name}` : 'leer';
    if (m) form.creditor.value = m.name;
  };
  kindChanged();
  // E-Rechnung: Angaben vom Server lesen lassen, Formular füllen, Datei als Beleg merken
  let einvoice = null;
  form.querySelector('#einvoice-file').onchange = (e) => safe(async () => {
    const file = e.target.files[0];
    if (!file) return;
    const result = form.querySelector('#einvoice-result');
    let d;
    try {
      const content = await fileToBase64(file);
      d = await api('/api/einvoice', { file: { name: file.name, content } });
      einvoice = { name: file.name, content };
    } catch (err) {
      e.target.value = '';
      mount(result, html`<p class="notice warn">Nicht eingelesen: ${err.message}${einvoice ? ` – angehängt bleibt ${einvoice.name}.` : ''}</p>`);
      throw err;
    }
    form.ext_number.value = d.number;
    form.issued.value = d.issued;
    if (form.kind.value === 'refund') {
      // Erstattung: die Rechnung ist der Nachweis; Kreditor, IBAN und Fälligkeit bleiben die des Antragstellers
      lateCheck();
    } else {
      for (const key of ['creditor', 'iban', 'bic', 'reference']) form[key].value = d[key] || '';  // alles aus dieser Rechnung, nichts von der vorigen
      form.due.value = d.due || '';
    }
    linesEl.replaceChildren();
    for (const l of d.bill_lines) {
      const holder = document.createElement('div');
      mount(holder, lineRow({ description: l.description, amount: toInput(l.amount) }));
      linesEl.append(holder.firstElementChild);
      bind(linesEl.lastElementChild);
    }
    showTotal();
    mount(result, html`<p class="notice ${d.warnings.length || d.duplicates.length ? 'warn' : ''}">
      <strong>${d.format}</strong> · ${d.creditor} · ${d.number} vom ${date(d.issued)} · zu zahlen ${eur(d.total)}${d.tax ? ` (darin ${eur(d.tax)} USt)` : ''}. Wird als ${form.kind.value === 'refund' ? 'Nachweis' : 'Beleg'} angehängt.
      ${d.duplicates.length ? html`<br><strong>Möglicherweise schon erfasst: ${d.duplicates.join(', ')}</strong>` : ''}
      ${d.warnings.map((w) => html`<br>${w}`)}</p>`);
    toast('E-Rechnung eingelesen – bitte Konto und Haushaltstitel wählen und prüfen.');
  });
  let kvTouched = false;
  form.kv.onchange = () => { kvTouched = true; [...linesEl.children].forEach((r) => r.retitle()); };
  form.querySelector('#add-bill-line').onclick = () => {
    const holder = document.createElement('div');
    mount(holder, lineRow());
    linesEl.append(holder.firstElementChild);
    bind(linesEl.lastElementChild);
  };
  // Bekannter Kreditor: Bankverbindung und Kontierung der letzten Rechnung übernehmen (nur leere Felder)
  form.creditor.onchange = () => {
    const last = store.data.bills.find((b) => b.creditor === form.creditor.value.trim());
    if (!last) return;
    if (!form.iban.value && last.iban) form.iban.value = last.iban;
    if (!form.bic.value && last.bic) form.bic.value = last.bic;
    if (!kvTouched) form.kv.value = last.kv;
    const first = linesEl.firstElementChild, line = last.lines[0];
    if (line && !first.querySelector('[data-key=account]').value) { setPosten(first.querySelector('[data-posten]'), line.account, line.title); first.querySelector('[data-rpj]').refresh(); }
    toast('Bankverbindung und Kontierung der letzten Rechnung übernommen – bitte prüfen.');
  };
  const submit = form.querySelector('button[type=submit]');
  const send = async (data) => {
    submit.disabled = true;  // kein Doppelklick → keine doppelte Rechnung
    try {
      const r = await api('/api/bill', data);
      document.getElementById('preview').close();
      el.close();
      await refresh();
      toast(`${r.number} erfasst und gebucht.`);
    } catch (err) {
      if (!String(err.message).startsWith('Möglicherweise doppelt')) throw err;
      const o = overlay('Möglicherweise doppelt', html`<p>${err.message}</p>
        <p>Nur fortfahren, wenn es wirklich eine weitere Rechnung ist – sonst wird die Ausgabe doppelt gebucht und doppelt bezahlt.</p>
        <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button>
          <button type="button" class="btn warn" data-force>Trotzdem erfassen</button></div>`, { wide: false });
      o.querySelector('[data-force]').onclick = () => safe(() => send({ ...data, allow_duplicate: true }));
    } finally { submit.disabled = false; }
  };
  form.onsubmit = (e) => {
    e.preventDefault();
    if (submit.disabled) return;
    safe(async () => {
      const data = Object.fromEntries(['kind', 'creditor', 'ext_number', 'iban', 'bic', 'issued', 'due', 'kv', 'reference', 'note'].map((k) => [k, form[k].value]));
      if (data.kind === 'refund') {
        Object.assign(data, { member_id: form.member_id.value, submitted_on: form.submitted_on.value, late_reason: form.late_reason.value });
        for (const key of ['signed', 'complete', 'authorized', 'no_other']) data[key] = form[key].checked;
      }
      data.lines = [...linesEl.children].map((row) => Object.fromEntries([...row.querySelectorAll('[data-key]')].map((x) => [x.dataset.key, x.value])));
      const file = form.file.files[0];
      if (file) data.file = { name: file.name, content: await fileToBase64(file) };
      else if (einvoice) data.file = einvoice;
      await send(data);
    });
  };
}

// ---------------------------------------------------------------- Zahlungsvorschlag

const copyButton = (value, label) => html`<button type="button" class="btn small ghost" data-copy="${value}" aria-label="${label} kopieren">${icon('copy', 14)} Kopieren</button>`;

export async function paymentDialog() {
  const list = openBills().sort((a, b) => a.due.localeCompare(b.due));
  const payable = (b) => b.iban && !b.payment_reversed && !b.approval.missing.length;
  const chosen = new Set(list.filter((b) => payable(b) && !b.transferred && !b.in_bank && daysOverdue(b.due) >= -14).map((b) => b.id));
  const render = () => {
    const picked = list.filter((b) => chosen.has(b.id));
    const el = dialog('Zahlungsvorschlag', html`
      <p class="small muted">Vorausgewählt: offene Eingangsrechnungen mit IBAN, die in den nächsten 14 Tagen fällig sind oder überfällig, noch nicht angewiesen und nicht schon im Kontoauszug abgebucht. Fremdrechnungen über ${eur(limitCents())} erst nach Freigabe durch Landesvorsitz und Landesschatzmeister (§ 7 Abs. 4).</p>
      ${list.length ? table(['', 'Beleg', 'Kreditor', 'Fällig', { label: 'Offen', className: 'num' }, 'Status'], list.map((b) => html`<tr>
        <td><input type="checkbox" data-pick="${b.id}" ${chosen.has(b.id) ? 'checked' : ''} ${payable(b) ? '' : 'disabled'} aria-label="${b.number} auswählen"></td>
        <td>${b.number}<span class="sub">${b.ext_number || ''}</span></td><td>${b.creditor}<span class="sub">${!b.iban ? 'IBAN fehlt' : b.payment_reversed ? 'erst klären' : b.approval.missing.length ? 'Freigabe fehlt' : ''}</span></td>
        <td>${date(b.due)}</td><td class="num">${eur(b.open)}</td><td>${billBadge(b)} ${approvalBadge(b)}</td></tr>`))
        : empty('Keine offenen Eingangsrechnungen', 'Alles bezahlt.')}
      <div class="bulkbar"><strong>${picked.length} Überweisung${picked.length === 1 ? '' : 'en'} · ${eur(picked.reduce((s, b) => s + b.open, 0))}</strong>
        <button type="button" class="btn primary" data-show ${picked.length ? '' : 'disabled'}>Überweisungsdaten anzeigen</button></div>`, { wide: true });
    el.querySelectorAll('[data-pick]').forEach((c) => (c.onchange = () => { c.checked ? chosen.add(Number(c.dataset.pick)) : chosen.delete(Number(c.dataset.pick)); render(); }));
    el.querySelector('[data-show]').onclick = () => {
      const risky = picked.filter((b) => b.transferred || b.in_bank);
      if (!risky.length) return safe(() => transfers([...chosen]));
      const o = overlay('Wirklich noch einmal überweisen?', html`<p>${risky.map((b, i) => html`${i ? ', ' : ''}<strong>${b.number}</strong> (${b.creditor}: ${b.transferred ? `angewiesen am ${date(b.transferred)}` : `Abbuchung vom ${date(b.in_bank)} im Kontoauszug`})`)}.</p>
        <p>Diese Rechnungen wurden vermutlich schon bezahlt. Erst den Abgleich mit dem Kontoauszug durchführen.</p>
        <div class="form-actions"><button type="button" class="btn ghost" data-close>Zurück</button>
          <button type="button" class="btn warn" data-anyway>Trotzdem anzeigen</button></div>`, { wide: false });
      o.querySelector('[data-anyway]').onclick = () => { o.close(); safe(() => transfers([...chosen])); };
    };
  };
  render();
}

async function transfers(ids) {
  const items = await api('/api/bill-transfers?ids=' + ids.join(','));
  const marked = new Set();
  const render = () => {
    const el = dialog('Überweisungen im Online-Banking', html`
      <p class="small muted">Je Überweisung die Felder kopieren und im Online-Banking einfügen – oder den GiroCode mit der Banking-App scannen. Danach „Als überwiesen markieren“; die Zuordnung zur Abbuchung erfolgt beim Abgleich mit dem Kontoauszug.</p>
      <div class="transfer-list">${items.map((t) => html`<section class="transfer-card ${marked.has(t.id) ? 'done' : ''}">
        <div class="transfer-head"><strong>${t.number}</strong><span class="small muted">fällig ${date(t.due)}</span>${marked.has(t.id) ? badge('Angewiesen', 'ok') : ''}
          ${!marked.has(t.id) && t.transferred ? badge(`schon angewiesen am ${date(t.transferred)}`, 'danger') : ''}${t.in_bank ? badge(`Abbuchung vom ${date(t.in_bank)} im Kontoauszug`, 'danger') : ''}</div>
        <div class="transfer-body">
          <dl class="transfer-fields">
            <dt>Empfänger</dt><dd><span>${t.name}</span>${copyButton(t.name, 'Empfänger')}</dd>
            <dt>IBAN</dt><dd><span class="mono">${t.iban}</span>${copyButton(t.iban_compact, 'IBAN')}</dd>
            ${t.bic ? html`<dt>BIC</dt><dd><span class="mono">${t.bic}</span>${copyButton(t.bic, 'BIC')}</dd>` : ''}
            <dt>Betrag</dt><dd><span><strong>${eur(t.amount)}</strong></span>${copyButton((t.amount / 100).toFixed(2).replace('.', ','), 'Betrag')}</dd>
            <dt>Verwendungszweck</dt><dd><span>${t.reference}</span>${copyButton(t.reference, 'Verwendungszweck')}</dd>
          </dl>
          <figure class="transfer-qr"><img src="/api/bill-qr.svg?id=${t.id}" alt="GiroCode für ${t.number}" width="150" height="150"><figcaption>GiroCode</figcaption></figure>
        </div>
        <div class="form-actions">${marked.has(t.id) ? '' : html`<button type="button" class="btn" data-mark="${t.id}">${icon('check', 16)} Als überwiesen markieren</button>`}</div>
      </section>`)}</div>
      <div class="bulkbar"><strong>${items.length} Überweisung${items.length === 1 ? '' : 'en'} · ${eur(items.reduce((s, t) => s + t.amount, 0))}</strong>
        <button type="button" class="btn primary" data-mark-all ${marked.size === items.length ? 'disabled' : ''}>Alle als überwiesen markieren</button></div>`, { wide: true });
    el.querySelectorAll('[data-copy]').forEach((b) => (b.onclick = () => safe(async () => {
      await navigator.clipboard.writeText(b.dataset.copy);
      toast(`${b.getAttribute('aria-label').replace(' kopieren', '')} kopiert.`);
    })));
    const mark = async (list) => {
      await api('/api/bill-transferred', { ids: list });
      list.forEach((id) => marked.add(id));
      await refresh();
      toast(list.length === 1 ? 'Als überwiesen markiert.' : `${list.length} Überweisungen als angewiesen markiert.`);
      render();
    };
    el.querySelectorAll('[data-mark]').forEach((b) => (b.onclick = () => safe(() => mark([Number(b.dataset.mark)]))));
    el.querySelector('[data-mark-all]').onclick = () => safe(() => mark(items.filter((t) => !marked.has(t.id)).map((t) => t.id)));
  };
  render();
}

// ---------------------------------------------------------------- Freigabe (§ 7 Abs. 4)

const ROLES = [['chair', 'Landesvorsitz', 'chair_name'], ['treasurer', 'Landesschatzmeister', 'treasurer_name']];
const HOW = ['E-Mail', 'schriftlich', 'Vorstandssitzung', 'mündlich'];

function approvalDialog(id) {
  const b = store.data.bills.find((x) => x.id === id);
  if (!b) return;
  const s = store.data.settings;
  const locked = Boolean(b.transferred) || b.paid > 0;
  const el = dialog(`Freigabe ${b.number}`, html`<div style="display:flex;flex-direction:column;gap:14px">
    <p><strong style="font-weight:600">${b.creditor}</strong> · ${eur(b.total)} · Rechnung vom ${date(b.issued)}${b.ext_number ? ` · ${b.ext_number}` : ''}</p>
    <p class="small muted">Finanzordnung § 7 Abs. 4: Fremdrechnungen über ${eur(b.approval.limit)} werden erst beglichen, wenn Landesvorsitz und Landesschatzmeister sie freigegeben haben (zwei verschiedene Personen). Hier wird festgehalten, wer wann auf welchem Weg freigegeben hat – bis dahin ist die Rechnung im Zahlungsvorschlag gesperrt.</p>
    ${ROLES.map(([role, label, key]) => {
      const g = b.approval.given[role];
      return g ? html`<section class="approval-card done"><h3>${icon('check', 16)} ${label}</h3>
          <p>${g.name} · ${date(g.approved_on)} · ${g.how}${g.note ? ` · ${g.note}` : ''}</p>
          ${locked ? '' : html`<div><button type="button" class="btn small ghost" data-revoke="${g.id}">Freigabe zurücknehmen</button></div>`}</section>`
        : html`<form class="approval-card" data-role="${role}"><h3>${label}</h3>
          <div class="formgrid">
            <label class="field">Name<input name="name" value="${s[key] || ''}" required autocomplete="off"></label>
            <label class="field">Freigegeben am<input type="date" name="approved_on" value="${todayIso()}" required></label>
            <label class="field">Auf welchem Weg<select name="how">${HOW.map((h, i) => option(h, h, i === 0))}</select></label>
            <label class="field">Bemerkung<input name="note" placeholder="optional, z. B. Betreff der E-Mail"></label>
          </div>
          <div class="form-actions"><button type="submit" class="btn primary">Freigabe eintragen</button></div></form>`;
    })}
    ${locked && b.approval.missing.length ? html`<p class="notice warn">Bereits angewiesen oder bezahlt – die Freigabe wird nachträglich dokumentiert und in der Quartalsprüfung ausgewiesen.</p>` : ''}
  </div>`, { wide: true });
  el.querySelectorAll('form[data-role]').forEach((form) => (form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/bill-approve', { id, role: form.dataset.role, ...Object.fromEntries(new FormData(form)) });
      await refresh();
      approvalDialog(id);
      toast('Freigabe eingetragen.');
    });
  }));
  el.querySelectorAll('[data-revoke]').forEach((btn) => (btn.onclick = () => safe(async () => {
    await api('/api/bill-approval-revoke', { id: Number(btn.dataset.revoke) });
    await refresh();
    approvalDialog(id);
    toast('Freigabe zurückgenommen.');
  })));
}

// ---------------------------------------------------------------- Quartalsprüfung (§ 7 Abs. 3, § 8 Abs. 8)

const quarterOf = (iso) => [Number(iso.slice(0, 4)), Math.floor((Number(iso.slice(5, 7)) - 1) / 3) + 1];

/** Quartale vom ersten Rechnungsdatum bis heute, neueste zuerst. */
function quarterOptions() {
  const today = quarterOf(todayIso());
  const first = store.data.bills.map((b) => b.issued).sort()[0];
  let [y, q] = first ? quarterOf(first) : [today[0], 1];
  const out = [];
  while (y < today[0] || (y === today[0] && q <= today[1])) {
    out.unshift([y, q]);
    [y, q] = q === 4 ? [y + 1, 1] : [y, q + 1];
  }
  return out;
}

/** Zuletzt abgeschlossenes Quartal (oder das älteste mit fälliger Prüfung). */
function defaultQuarter() {
  const due = store.data.reviews_due[0];
  if (due) return [due.year, due.quarter];
  const [y, q] = quarterOf(todayIso());
  return q === 1 ? [y - 1, 4] : [y, q - 1];
}

export async function reviewDialog(year, quarter) {
  if (!year) [year, quarter] = defaultQuarter();
  const rep = await api(`/api/quarter-review?year=${year}&quarter=${quarter}`);
  const s = store.data.settings;
  const options = quarterOptions();
  if (!options.some(([y, q]) => y === year && q === quarter)) options.push([year, quarter]);
  const approvalText = (a) => !a.required ? 'nicht nötig' : a.complete ? 'freigegeben' : `fehlt: ${a.missing.map((r) => ROLES.find((x) => x[0] === r)[1]).join(', ')}`;
  const el = dialog(`Quartalsprüfung ${rep.label}`, html`<div style="display:flex;flex-direction:column;gap:16px">
    <div class="review-head">
      <label class="field">Quartal<select id="review-quarter">${options.map(([y, q]) => option(`${y}-${q}`, `Q${q}/${y}`, y === year && q === quarter))}</select></label>
      <a class="btn" href="/api/quarter-review.pdf?year=${year}&quarter=${quarter}" target="_blank" rel="noopener">${icon('file', 16)} Prüfprotokoll (PDF)</a>
    </div>
    <p class="small muted">Finanzordnung § 7 Abs. 3 und § 8 Abs. 8: Der Landesschatzmeister prüft alle Rechnungen eines Quartals innerhalb von vier Wochen nach Quartalsende – für ${rep.label} bis <strong>${date(rep.deadline)}</strong> – und reicht das Protokoll beim Landesgeschäftsführer ein. Zugeordnet nach Rechnungsdatum (${date(rep.start)} – ${date(rep.end)}).</p>
    <div class="cards">
      <div class="card"><span class="label">Rechnungen</span><span class="value">${rep.count}</span><span class="note">${eur(rep.total)}</span></div>
      <div class="card"><span class="label">Freigabepflichtig</span><span class="value">${rep.required}</span><span class="note">${rep.approved} freigegeben</span></div>
      <div class="card"><span class="label">Auffälligkeiten</span><span class="value">${rep.findings}</span><span class="note">${!rep.reviews.length ? 'noch nicht protokolliert' : rep.unreviewed ? `${rep.unreviewed} nach der Prüfung erfasst – Nachprüfung` : `${rep.reviews.length}× protokolliert`}</span></div>
    </div>
    ${rep.bills.length ? table(['Beleg', 'Datum', 'Kreditor', { label: 'Betrag', className: 'num' }, 'Freigabe', 'Stand', 'Auffälligkeiten'], rep.bills.map((i) => html`<tr>
        <td><strong style="font-weight:600">${i.number}</strong><span class="sub">${i.ext_number || ''}</span></td><td>${date(i.issued)}</td>
        <td>${i.creditor}<span class="sub">${i.kind === 'refund' ? 'Auslagenerstattung' : 'Fremdrechnung'}</span></td><td class="num">${eur(i.total)}</td>
        <td>${approvalText(i.approval)}</td><td>${i.state}</td>
        <td>${i.findings.length ? i.findings.map((x) => html`${badge(x, 'warn')} `) : badge('keine', 'ok')}${rep.reviews.length && !i.reviewed && !i.cancelled ? html` ${badge('nach Prüfung erfasst', 'danger')}` : ''}</td></tr>`))
      : empty('Keine Eingangsrechnungen', 'In diesem Quartal ist keine Rechnung datiert.')}
    ${rep.reviews.length ? html`<div><h3 style="margin-bottom:8px">Protokollierte Prüfungen</h3><ol class="review-list">${rep.reviews.map((r) => html`<li>
        ${date(r.reviewed_on)} · ${r.reviewer} · <strong style="font-weight:600">${r.result === 'ok' ? 'ohne Beanstandungen' : 'mit Bemerkungen'}</strong>
        <span class="muted"> · ${r.bills} Rechnungen, ${eur(r.total)}${r.late ? ' · nach Fristablauf' : ''}</span>${r.remarks ? html`<br><span class="muted" style="white-space:pre-line">${r.remarks}</span>` : ''}</li>`)}</ol></div>` : ''}
    ${rep.ended ? html`<form id="review-form" class="approval-card"><h3>Prüfung protokollieren</h3>
        <div class="formgrid">
          <label class="field">Geprüft von<input name="reviewer" value="${s.treasurer_name || ''}" required autocomplete="off"></label>
          <label class="field">Geprüft am<input type="date" name="reviewed_on" value="${todayIso()}" required></label>
          <label class="field">Ergebnis<select name="result">${option('ok', 'ohne Beanstandungen', !rep.findings)}${option('remarks', 'mit Bemerkungen', Boolean(rep.findings))}</select></label>
          <label class="field span">Bemerkungen<textarea name="remarks" rows="2" placeholder="bei „mit Bemerkungen“ erforderlich"></textarea></label>
        </div>
        <div class="form-actions"><button type="submit" class="btn primary">Prüfung protokollieren</button></div></form>`
      : html`<p class="notice">${rep.label} läuft noch – geprüft und protokolliert wird nach Quartalsende.</p>`}
  </div>`, { wide: true });
  el.querySelector('#review-quarter').onchange = (e) => {
    const [y, q] = e.target.value.split('-').map(Number);
    safe(() => reviewDialog(y, q));
  };
  const form = el.querySelector('#review-form');
  if (form) form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/quarter-review', { year, quarter, ...Object.fromEntries(new FormData(form)) });
      await refresh();
      await reviewDialog(year, quarter);
      toast(`Prüfung ${rep.label} protokolliert – Protokoll als PDF einreichen.`);
    });
  };
}

// ---------------------------------------------------------------- Seite

export function bills(main) {
  const all = store.data.bills;
  const test = FILTERS.find((f) => f[0] === filter)[2];
  const shown = all.filter(test);
  const open = openBills();
  mount(main, html`<div class="page">
    ${pageHead(`${open.length} offen · ${eur(open.reduce((s, b) => s + b.open, 0))}`, 'Eingangsrechnungen', html`
      <button type="button" class="btn" data-reconcile>Abgleich mit Kontoumsätzen</button>
      <button type="button" class="btn" data-review>Quartalsprüfung${store.data.reviews_due.length ? ` (${store.data.reviews_due.length})` : ''}</button>
      <button type="button" class="btn" data-pay>Zahlungsvorschlag</button>
      <button type="button" class="btn primary" data-new-bill>${icon('plus', 16)} Eingangsrechnung erfassen</button>`)}
    <div class="tabs" role="tablist" aria-label="Eingangsrechnungen filtern">${FILTERS.map(([id, label, f]) => html`
      <button type="button" role="tab" data-filter="${id}" aria-selected="${filter === id}">${label}<span class="n">${all.filter(f).length}</span></button>`)}</div>
    <section class="panel" style="padding:6px 12px">${shown.length ? table(['Beleg', 'Kreditor', 'Rechnungsdatum', 'Fällig', { label: 'Betrag', className: 'num' }, { label: 'Offen', className: 'num' }, 'Status', ''],
      shown.map((b) => html`<tr><td><strong style="font-weight:600">${b.number}</strong><span class="sub">${b.ext_number || '—'}</span></td>
        <td>${b.creditor}<span class="sub">${b.kind === 'refund' ? `Erstattung${b.refund ? ` · Antrag vom ${date(b.refund.submitted_on)}` : ''} · ` : ''}${kvName(b.kv)} · ${b.lines.map((l) => l.account).join(', ')}</span>
          ${b.refund?.late ? badge('nach drei Monaten', 'warn') : ''}</td><td>${date(b.issued)}</td><td>${date(b.due)}</td>
        <td class="num">${eur(b.total)}</td><td class="num">${b.status === 'cancelled' ? '—' : eur(b.open)}</td><td>${billBadge(b)} ${approvalBadge(b)}</td>
        <td style="white-space:nowrap">${b.attachment?.endsWith('.xml') ? html`<a class="btn small" href="/api/bill-file?id=${b.id}&view=1" target="_blank" rel="noopener">E-Rechnung</a>
            <a class="btn small ghost" href="/api/bill-file?id=${b.id}" download>XML</a>`
          : b.attachment ? html`<a class="btn small" href="/api/bill-file?id=${b.id}" target="_blank" rel="noopener">Beleg</a>` : ''}
          ${b.status === 'open' && b.approval.required ? html` <button type="button" class="btn small" data-approve="${b.id}">Freigabe</button>` : ''}
          ${b.transferred && b.open > 0 && !b.payment_reversed ? html` <button type="button" class="btn small ghost" data-untransfer="${b.id}">Anweisung zurücknehmen</button>` : ''}
          ${b.payment_reversed && b.open > 0 ? html` <button type="button" class="btn small ghost" data-release="${b.id}">Wieder zur Zahlung freigeben</button>` : ''}
          ${b.status === 'open' && b.paid > 0 && b.open > 0 ? html` <button type="button" class="btn small ghost" data-writeoff="${b.id}">Restbetrag ausbuchen</button>` : ''}
          ${b.status === 'open' && b.paid === 0 ? html` <button type="button" class="btn small danger" data-cancel-bill="${b.id}">Stornieren</button>` : ''}</td></tr>`))
      : empty('Keine Eingangsrechnungen', 'Eingangsrechnung erfassen – sie erscheint dann in den offenen Posten und im Zahlungsvorschlag.')}</section>
    <p class="small muted">Belegnummern E-JJJJ-nnnnn werden automatisch vergeben. Bezahlt ist eine Eingangsrechnung, sobald die Abbuchung aus dem Kontoauszug zugeordnet ist.</p>
  </div>`);
  main.querySelector('[data-new-bill]').onclick = billDialog;
  main.querySelector('[data-pay]').onclick = () => safe(paymentDialog);
  main.querySelector('[data-reconcile]').onclick = () => safe(reconcileDialog);
  main.querySelector('[data-review]').onclick = () => safe(() => reviewDialog());
  main.querySelectorAll('[data-approve]').forEach((b) => (b.onclick = () => approvalDialog(Number(b.dataset.approve))));
  main.querySelectorAll('[data-filter]').forEach((b) => (b.onclick = () => { filter = b.dataset.filter; bills(main); }));
  main.querySelectorAll('[data-untransfer]').forEach((b) => (b.onclick = () => safe(async () => {
    await api('/api/bill-transferred', { ids: [Number(b.dataset.untransfer)], undo: true });
    await refresh();
    toast('Anweisung zurückgenommen.');
  })));
  main.querySelectorAll('[data-release]').forEach((b) => (b.onclick = () => confirmAction('Wieder zur Zahlung freigeben',
    'Nur freigeben, wenn geklärt ist, dass diese Rechnung noch nicht bezahlt wurde (Kontoauszug prüfen). Sie erscheint dann wieder im Zahlungsvorschlag.',
    () => api('/api/bill-release', { id: Number(b.dataset.release) }), { label: 'Freigeben', done: 'Wieder zur Zahlung freigegeben.' })));
  main.querySelectorAll('[data-writeoff]').forEach((b) => (b.onclick = () => {
    const bill = all.find((x) => x.id === Number(b.dataset.writeoff));
    const el = dialog('Restbetrag ausbuchen', html`<form id="writeoff-form" style="display:flex;flex-direction:column;gap:14px">
      <p>${bill.number} · ${bill.creditor}: bezahlt ${eur(bill.paid)} von ${eur(bill.total)}. Der Rest von <strong>${eur(bill.open)}</strong> wird ausgebucht und mindert die Ausgabe.</p>
      <label class="field">Grund<input name="reason" required placeholder="z. B. Skonto, Kürzung, Gebühr"></label>
      <div class="form-actions"><button type="submit" class="btn primary">Ausbuchen</button></div></form>`);
    el.querySelector('#writeoff-form').onsubmit = (e) => {
      e.preventDefault();
      safe(async () => {
        await api('/api/bill-writeoff', { id: bill.id, reason: e.target.reason.value });
        el.close();
        await refresh();
        toast('Restbetrag ausgebucht.');
      });
    };
  }));
  main.querySelectorAll('[data-cancel-bill]').forEach((b) => (b.onclick = () => confirmAction('Eingangsrechnung stornieren',
    (all.find((x) => x.id === Number(b.dataset.cancelBill)).transferred ? 'Achtung: Diese Rechnung ist als überwiesen markiert – bitte prüfen, ob das Geld schon abgebucht wurde. ' : '')
      + 'Die Buchung wird durch eine Gegenbuchung aufgehoben; der Beleg bleibt mit Nummer erhalten.',
    () => api('/api/bill-cancel', { id: Number(b.dataset.cancelBill) }), { label: 'Stornieren', done: 'Eingangsrechnung storniert.' })));
}
