// Kontierung eines Bankumsatzes: Teilbeträge auf ein Konto (Haushaltsposten mit Bereich, Titel ergibt sich; Bestandskonto ohne Titel;
// Mitgliedsbeitrag 8100 nur mit Mitglied; RPJ-Posten nur mit Projekt bzw. Mittelabruf), Rechnung oder Eingangsrechnung.
// Verbindlich prüft der Server (Summe, Richtung, Konten); hier nur Komfort und sofortige Rückmeldung.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { eur, signed, toInput, parseCents } from '../format.js';
import { store, eligibleInvoices, openBills, kvName, accountName, titleLabel } from '../store.js';
import { icon, safe, toast, overlay } from '../ui.js';
import { postenField, bindPosten, postenCodes, titleFor, isBalance } from '../account-picker.js';
import { pickMembers, membersLabel, membersPayload } from '../member-picker.js';
import { rpjField, bindRpj, rpjKind, rpjValue, rpjMissing } from '../rpj-field.js';

const option = (value, label, selected) => html`<option value="${value}" ${selected ? 'selected' : ''}>${label}</option>`;

export function bookingEditor(container, b, { onBooked, onPark }) {
  const kind = b.amount > 0 ? 'income' : 'expense';
  // alle Konten des Kontenrahmens; 8100 nur mit Mitglied – bei Ausgängen als Rückzahlung bzw. Rücklastschrift eines Beitrags;
  // 1592 bei Ausgängen als Rückzahlung von RPJ-Mitteln (mit Mittelabruf)
  const codes = [...postenCodes(kind, { balance: true }), ...(kind === 'expense' ? ['8100', '1592'] : [])].filter((c) => !(b.ledger === 'cash' && store.data.cash.kassen.some((k) => k.account === c)));   // Kassenbeleg nie direkt auf eine Kasse (Transit über 2380)
  const invoices = eligibleInvoices(b);
  const invoiceOpen = (i) => (b.amount > 0 ? i.total - i.paid : i.paid);
  const matched = invoices.find((i) => `${b.reference} ${b.name}`.includes(i.number));
  const bills = b.amount < 0 ? openBills() : [];
  const billLabel = (x) => `${x.number} · ${x.creditor}${x.ext_number ? ' · ' + x.ext_number : ''} · ${eur(x.open)}${x.approval?.missing.length ? ' · Freigabe fehlt' : ''}`;

  mount(container, html`
    <div id="suggestions" class="suggestions" aria-live="polite">
      ${matched ? html`<div class="suggestion"><div><strong>Rechnung erkannt</strong><br><span class="what">${matched.number} · ${matched.recipient} · offen ${eur(invoiceOpen(matched))}</span></div>
        <button type="button" class="btn primary" data-use-invoice>Übernehmen</button></div>` : ''}
      <p class="small muted" data-loading>Suche passende Lernbeispiele …</p>
    </div>
    <form id="book-form" novalidate>
      <h3 style="margin-bottom:10px">Kontierung</h3>
      <div class="parts" id="parts"></div>
      <div class="parts-foot" style="margin-top:10px">
        <button type="button" class="link" id="add-part">${icon('plus', 14)} Teilbetrag</button>
        <span class="difference" id="difference" role="status"></span>
      </div>
      <label class="check" style="margin-top:12px"><input type="checkbox" id="learn"> Als Lernbeispiel merken (nur Konten, keine Rechnungen)</label>
      <div class="detail-actions" style="margin-top:14px">
        <p class="keys"><kbd>J</kbd>/<kbd>K</kbd> nächster/voriger · <kbd>↵</kbd> verbuchen${b.state === 'open' ? html` · <kbd>P</kbd> parken` : ''}</p>
        <div class="buttons">
          ${b.state === 'open' ? html`<button type="button" class="btn warn" id="park">Parken auf 1370</button>` : ''}
          <button type="submit" class="btn dark" id="book-submit">${b.state === 'parked' ? 'Auflösen & verbuchen' : 'Verbuchen'}</button>
        </div>
      </div>
    </form>`);

  const partsEl = container.querySelector('#parts');

  function partMarkup(values) {
    const mode = values.bill_id ? 'bill' : values.invoice_id ? 'invoice' : 'account';
    const account = values.account || '';
    const kv = values.kv || 'LV';
    const title = values.title && values.title !== titleFor(account, kv, kind) ? values.title : '';   // nur ein abweichender Titel gilt als gewählt
    return html`<div class="part" data-mode="${mode}">
      <label class="field">Betrag<input class="amount-input" data-amount inputmode="decimal" value="${toInput(values.cents)}" aria-label="Betrag in Euro"></label>
      <div class="assign">
        <div class="kind segmented" role="group" aria-label="Zuordnung">
          <button type="button" data-mode-btn="account" aria-pressed="${mode === 'account'}">Konto</button>
          <button type="button" data-mode-btn="invoice" aria-pressed="${mode === 'invoice'}" ${invoices.length ? '' : 'disabled'}>Rechnung${invoices.length ? '' : ' (keine offen)'}</button>
          ${b.amount < 0 ? html`<button type="button" data-mode-btn="bill" aria-pressed="${mode === 'bill'}" ${bills.length ? '' : 'disabled'}>Eingangsrechnung${bills.length ? '' : ' (keine offen)'}</button>` : ''}
        </div>
        <div class="field" data-account-field>Konto / Haushaltsposten${postenField({ kind, account, title, codes, label: 'Konto' })}</div>
        <label class="field" data-account-field data-kv-field>Bereich<select data-kv>${store.data.kv.map((k) => option(k.code, k.name, k.code === kv))}</select></label>
        ${rpjField()}
        <div class="field" data-members-field hidden>Mitglied (Pflicht bei 8100)<div class="members-box"><span data-members-label class="muted">noch nicht zugeordnet</span>
          <button type="button" class="btn small" data-members-edit>${icon('users', 14)} Mitglied suchen</button></div></div>
        <label class="field" data-invoice-field>Rechnung suchen<input data-invoice-search placeholder="Nummer oder Name"></label>
        <label class="field" data-invoice-field>Rechnung<select data-invoice>${option('', 'Bitte wählen', !values.invoice_id)}${invoices.map((i) => option(i.id, `${i.number} · ${i.recipient} · ${eur(invoiceOpen(i))}`, String(i.id) === String(values.invoice_id)))}</select></label>
        <label class="field" data-bill-field>Eingangsrechnung suchen<input data-bill-search placeholder="Beleg, Kreditor, Rechnungsnr."></label>
        <label class="field" data-bill-field>Eingangsrechnung<select data-bill>${option('', 'Bitte wählen', !values.bill_id)}${bills.map((x) => option(x.id, billLabel(x), String(x.id) === String(values.bill_id)))}</select></label>
      </div>
      <button type="button" class="btn ghost icon-btn remove" data-remove aria-label="Teilbetrag entfernen">${icon('trash', 16)}</button>
    </div>`;
  }

  const partCents = (part) => parseCents(part.querySelector('[data-amount]').value);
  const isDues = (part) => part.dataset.mode === 'account' && part.querySelector('[data-account]').value === '8100';
  const fiscal = b.fy || Number(b.booked.slice(0, 4));   // Haushaltsjahr wie auf dem Server (RPJ-Projekt bzw. Mittelabruf: dieses Jahr, höchstens eins daneben)
  const rpjOf = (part) => (part.dataset.mode === 'account' ? rpjKind(part.querySelector('[data-account]').value, part.querySelector('[data-title]').value) : null);

  /** Mitgliedsbeitrag (8100): Mitglied wählen; bei einer Sammelzahlung mehrere Mitglieder mit Beträgen. Negativer Betrag = Rückzahlung bzw.
   *  Rücklastschrift; auch Personen, die nicht (mehr) in der Mitgliederliste stehen (z. B. ausgetreten). */
  function editMembers(part) {
    const cents = partCents(part);
    if (!cents || Math.sign(cents) !== Math.sign(b.amount)) return toast('Bitte zuerst den Betrag eintragen.', true);
    pickMembers({ amount: cents, selected: part.members || [], hint: `${b.name || ''} ${b.reference || ''}`, payer: b.name || '',
      title: `${cents < 0 ? 'Rückzahlung' : 'Mitgliedsbeitrag'} ${eur(cents)} zuordnen`,
      onDone: (items) => { part.members = items; syncMembers(part); update(); part.querySelector('[data-members-edit]').focus(); } });
  }

  function syncMembers(part, openIfEmpty = false) {
    const dues = isDues(part);
    part.querySelector('[data-members-field]').hidden = !dues;
    part.querySelector('[data-kv-field]').hidden = dues || part.dataset.mode !== 'account';   // Bereich = Kreisverband des Mitglieds
    if (!dues) return;
    const items = part.members || [];
    const label = part.querySelector('[data-members-label]');
    label.classList.toggle('muted', !items.length);
    label.textContent = items.length ? membersLabel(items) : 'noch nicht zugeordnet';
    part.querySelector('[data-members-edit]').textContent = items.length ? 'Ändern' : 'Mitglied suchen';
    if (openIfEmpty && !items.length) editMembers(part);
  }

  /** Mitglieder vollständig: eines (Betrag = Teilbetrag) oder mehrere, deren Beträge den Teilbetrag ergeben. */
  const membersComplete = (part) => {
    const items = part.members || [];
    return items.length === 1 || (items.length > 1 && items.reduce((s, x) => s + (x.amount || 0), 0) === partCents(part));
  };

  function applyMode(part) {
    const mode = part.dataset.mode;
    part.querySelectorAll('[data-account-field]').forEach((x) => (x.hidden = mode !== 'account'));
    part.querySelectorAll('[data-invoice-field]').forEach((x) => (x.hidden = mode !== 'invoice'));
    part.querySelectorAll('[data-bill-field]').forEach((x) => (x.hidden = mode !== 'bill'));
    part.querySelectorAll('[data-mode-btn]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.modeBtn === mode)));
    syncMembers(part);
    part.querySelector('[data-rpj]').refresh?.();
  }

  function bindPart(part) {
    applyMode(part);
    part.querySelector('[data-amount]').oninput = update;
    part.querySelectorAll('[data-mode-btn]').forEach((btn) => (btn.onclick = () => { part.dataset.mode = btn.dataset.modeBtn; applyMode(part); update(); }));
    const kvSel = part.querySelector('[data-kv]');
    bindPosten(part, { kv: () => kvSel.value, onChange: () => { syncMembers(part, true); part.querySelector('[data-rpj]').refresh(); } });
    bindRpj(part.querySelector('[data-rpj]'), { kind: () => rpjOf(part), year: () => fiscal, onChange: () => update() });
    part.querySelector('[data-members-edit]').onclick = () => editMembers(part);
    kvSel.onchange = () => { part.querySelector('[data-posten]').refresh(); update(); };
    part.querySelector('[data-invoice]').onchange = update;
    part.querySelector('[data-invoice-search]').oninput = (e) => {
      const q = e.target.value.toLowerCase(), sel = part.querySelector('[data-invoice]'), current = sel.value;
      mount(sel, html`${option('', 'Bitte wählen', !current)}${invoices.filter((i) => String(i.id) === current || `${i.number} ${i.recipient}`.toLowerCase().includes(q))
        .map((i) => option(i.id, `${i.number} · ${i.recipient} · ${eur(invoiceOpen(i))}`, String(i.id) === current))}`);
    };
    part.querySelector('[data-bill]').onchange = update;
    part.querySelector('[data-bill-search]').oninput = (e) => {
      const q = e.target.value.toLowerCase(), sel = part.querySelector('[data-bill]'), current = sel.value;
      mount(sel, html`${option('', 'Bitte wählen', !current)}${bills.filter((x) => String(x.id) === current || `${x.number} ${x.creditor} ${x.ext_number || ''}`.toLowerCase().includes(q))
        .map((x) => option(x.id, billLabel(x), String(x.id) === current))}`);
    };
    part.querySelector('[data-remove]').onclick = () => { part.remove(); update(); };
  }

  function addPart(values = {}) {
    const holder = document.createElement('div');
    mount(holder, partMarkup(values));
    const part = holder.firstElementChild;
    part.members = (values.members || []).map((m) => (m.external ? { external: m.external, amount: m.amount ?? m.cents } : { member_id: m.member_id, amount: m.amount ?? m.cents }));
    partsEl.append(part);
    bindPart(part);
    update();
    return part;
  }

  function collect() {
    return [...partsEl.children].map((p) => {
      const mode = p.dataset.mode;
      const items = p.members || [];
      return {
        amount: p.querySelector('[data-amount]').value,
        invoice_id: mode === 'invoice' ? p.querySelector('[data-invoice]').value : '',
        bill_id: mode === 'bill' ? p.querySelector('[data-bill]').value : '',
        account: mode === 'account' ? p.querySelector('[data-account]').value : '',
        kv: p.querySelector('[data-kv]').value,
        title: mode === 'account' ? p.querySelector('[data-title]').value : '',
        ...(isDues(p) ? { members: membersPayload(items) } : {}),
        ...(rpjOf(p) ? { rpj: rpjValue(p.querySelector('[data-rpj]')) } : {}),
      };
    });
  }

  function update() {
    const parts = collect();
    let total = 0, valid = parts.length > 0;
    parts.forEach((p) => { const c = parseCents(p.amount); if (c === null || c === 0) valid = false; else total += c; });
    const rest = b.amount - total;
    const diff = container.querySelector('#difference');
    // was noch fehlt, steht neben dem Knopf (statt eines kommentarlos ausgegrauten „Verbuchen“)
    const missing = parts.map((p, i) => (p.invoice_id || p.bill_id ? ''
      : !p.account ? 'Konto wählen' : !(p.title || isBalance(p.account)) ? 'Haushaltstitel wählen'
        : p.account === '8100' && !membersComplete(partsEl.children[i]) ? 'Mitglied zuordnen'
          : rpjMissing(partsEl.children[i].querySelector('[data-rpj]')))).find(Boolean) || '';
    const complete = !missing;
    diff.textContent = !valid ? 'Beträge prüfen' : rest !== 0 ? 'Noch zu verteilen: ' + signed(rest) : missing ? `Vollständig aufgeteilt · noch: ${missing}` : 'Vollständig aufgeteilt';
    diff.classList.toggle('ok', valid && rest === 0 && complete);
    container.querySelector('#book-submit').disabled = !valid || rest !== 0 || !complete;
    partsEl.querySelectorAll('[data-remove]').forEach((x) => (x.hidden = partsEl.children.length < 2));
    const learn = container.querySelector('#learn');
    learn.disabled = [...partsEl.children].some((x) => x.dataset.mode !== 'account'); // Rechnungsausgleiche werden nicht gelernt
    if (learn.disabled) learn.checked = false;
    return { parts, valid: valid && rest === 0 && complete };
  }

  function replaceParts(list) {
    partsEl.replaceChildren();
    list.forEach(addPart);
  }

  addPart(matched ? { cents: b.amount, invoice_id: matched.id } : { cents: b.amount });

  container.querySelector('[data-use-invoice]')?.addEventListener('click', () => replaceParts([{ cents: b.amount, invoice_id: matched.id }]));
  container.querySelector('#add-part').onclick = () => {
    const total = collect().reduce((s, p) => s + (parseCents(p.amount) || 0), 0);
    const rest = b.amount - total;
    const part = addPart(rest && Math.sign(rest) === Math.sign(b.amount) ? { cents: rest } : {});
    part.querySelector('[data-amount]').focus();
  };
  container.querySelector('#park')?.addEventListener('click', onPark);
  container.querySelector('#book-form').onsubmit = (e) => {
    e.preventDefault();
    const state = update();
    if (!state.valid) return toast('Bitte Beträge und Zuordnung (Konto mit Haushaltstitel, bei 8100 das Mitglied, bei RPJ das Projekt, Rechnung oder Eingangsrechnung) vollständig angeben.', true);
    const send = async (confirmNoBill = false) => {
      await api('/api/book', { id: b.id, parts: state.parts, learn: container.querySelector('#learn').checked, confirm_no_bill: confirmNoBill });
      await onBooked();
      toast(b.state === 'parked' ? 'Parkkonto aufgelöst und verbucht.' : 'Umsatz verbucht.');
    };
    safe(async () => {
      try {
        await send();
      } catch (err) {
        const bill = String(err.message).startsWith('Diese Abbuchung gehört offenbar');
        if (!bill && !String(err.message).startsWith('Dieser Zahlungseingang gehört offenbar')) throw err;
        const o = overlay(bill ? 'Gehört zu einer Eingangsrechnung?' : 'Gehört zu einer Rechnung?', html`<p>${err.message}</p>
          <p>${bill ? 'Wird sie trotzdem auf ein Sachkonto gebucht, steht die Ausgabe doppelt im Haushalt und die Eingangsrechnung bleibt offen.'
            : 'Wird er trotzdem auf ein Sachkonto gebucht, steht die Einnahme doppelt im Haushalt und die Rechnung bleibt offen (Zahlungserinnerung).'}</p>
          <div class="form-actions"><button type="button" class="btn ghost" data-close>Zurück</button>
            <button type="button" class="btn warn" data-anyway>Gehört nicht dazu – so buchen</button></div>`, { wide: false });
        o.querySelector('[data-anyway]').onclick = () => { o.close(); safe(() => send(true)); };
      }
    });
  };

  const showSuggestions = (suggestions) => {
    const box = container.querySelector('#suggestions');
    if (!box) return;
    box.querySelector('[data-loading]')?.remove();
    suggestions.forEach((sug, i) => {
      const holder = document.createElement('div');
      mount(holder, html`<div class="suggestion"><div><strong>${suggestions.length > 1 ? `Vorschlag ${i + 1} von ${suggestions.length}` : 'Vorschlag'} · ${sug.count === 1 ? 'wie am' : `${sug.count}× gebucht, zuletzt`} ${new Date(sug.last + 'T12:00:00').toLocaleDateString('de-DE')}</strong><br>
        <span class="what">${sug.parts.map((p, j) => html`${j ? html`<br>` : ''}${p.account} · ${accountName(p.account)} · ${p.members ? membersLabel(p.members.map((m) => ({ ...m, amount: m.cents }))) : kvName(p.kv)}${p.title && p.title !== titleFor(p.account, p.kv, kind) ? ` · Titel ${titleLabel(p.title)}` : ''}${sug.parts.length > 1 ? ` · ${eur(p.cents)}` : ''}`)}</span></div>
        <button type="button" class="btn primary">Übernehmen</button></div>`);
      const card = holder.firstElementChild;
      card.querySelector('button').onclick = () => { replaceParts(sug.parts.map((p) => ({ ...p }))); toast('Vorschlag eingesetzt – bitte prüfen und verbuchen.'); };
      box.append(card);
    });
  };

  // Erst passende Eingangsrechnung prüfen (Betrag + IBAN/Rechnungsnummer); nur ohne Treffer Lernbeispiele anbieten
  const billCheck = b.amount < 0 && bills.length ? api('/api/matches?id=' + b.id).catch(() => []) : Promise.resolve([]);
  billCheck.then((found) => {
    const strong = found.filter((c) => c.kind === 'bill' && c.strong);
    const box = container.querySelector('#suggestions');
    if (!box) return null;
    if (strong.length === 1) {
      const hit = strong[0];
      replaceParts([{ cents: b.amount, bill_id: hit.id }]);  // wie bei erkannter Rechnung: direkt vorbelegt
      const holder = document.createElement('div');
      mount(holder, html`<div class="suggestion"><div><strong>Eingangsrechnung erkannt</strong> · ${hit.evidence.join(', ')}<br>
        <span class="what">${hit.number} · ${hit.name} · offen ${eur(hit.open)} – als Zuordnung eingesetzt</span></div></div>`);
      box.prepend(holder.firstElementChild);
      box.querySelector('[data-loading]')?.remove();
      return null;
    }
    if (strong.length > 1) {
      const holder = document.createElement('div');
      mount(holder, html`<div class="suggestion"><div><strong>Mehrere passende Eingangsrechnungen</strong><br>
        <span class="what">${strong.map((c, i) => html`${i ? ', ' : ''}${c.number} (${c.name})`)} – bitte unter „Eingangsrechnung“ die richtige wählen.</span></div></div>`);
      box.prepend(holder.firstElementChild);
    }
    return api('/api/suggestions?id=' + b.id).then(showSuggestions);
  }).catch(() => container.querySelector('[data-loading]')?.remove());

  return { submit: () => container.querySelector('#book-submit').click() };
}
