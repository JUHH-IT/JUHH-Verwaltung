// Kasse (ab 0.25, Wunsch Lennert Gust 08.11.2026): Kassenbelege erfassen und im selben Buchungsplatz wie den Kontoauszug verbuchen
// (views/bank.js), Kassenbuch mit Bestand, Anfangsbestand, Geldtransit Bank ↔ Kasse über 2380. Verbindlich prüft der Server (kasse.py).
import { api, fileToBase64, download } from '../api.js';
import { html, mount } from '../html.js';
import { eur, signed, date, toInput, todayIso } from '../format.js';
import { store, refresh, ledgerRows, kvName, titleLabel } from '../store.js';
import { pageHead, icon, empty, dialog, safe, toast, table, badge } from '../ui.js';
import { renderLedger } from './bank.js';

const KINDS = [['out', 'Ausgabe'], ['in', 'Einnahme'], ['withdraw', 'Abhebung von der Bank'], ['deposit', 'Einzahlung auf die Bank']];
const today = () => todayIso();   // Ortszeit (nicht UTC)

function head() {
  const c = store.data.cash;
  const year = ledgerRows('cash');
  return html`${pageHead(`Haushaltsjahr ${store.year}`, 'Kasse', html`
      <button type="button" class="btn" data-opening>${c.start ? 'Anfangsbestand' : 'Anfangsbestand festlegen'}</button>
      ${c.start ? html`<button type="button" class="btn primary" data-new-cash>${icon('plus', 16)} Kassenbeleg erfassen</button>` : ''}`)}
    ${c.start ? html`<section class="cards" aria-label="Kasse">
      <div class="card"><span class="label">Kassenbestand heute</span><span class="value">${eur(c.balance)}</span>
        <span class="note">Anfangsbestand ${eur(c.opening?.amount || 0)} am ${date(c.start)}</span></div>
      <div class="card"><span class="label">Einnahmen ${store.year}</span><span class="value">${eur(year.filter((b) => b.amount > 0 && b.state !== 'void').reduce((s, b) => s + b.amount, 0))}</span>
        <span class="note">Kassenbelege inkl. Abhebungen</span></div>
      <div class="card"><span class="label">Ausgaben ${store.year}</span><span class="value">${eur(-year.filter((b) => b.amount < 0 && b.state !== 'void').reduce((s, b) => s + b.amount, 0))}</span>
        <span class="note">Kassenbelege inkl. Einzahlungen</span></div>
      <div class="card"><span class="label">Geldtransit 2380</span><span class="value ${c.transit ? 'over' : ''}">${eur(Math.abs(c.transit))}</span>
        <span class="note">${c.transit ? 'offen – Gegenseite fehlt noch (Kasse oder Kontoauszug) oder anderer Durchlaufposten' : 'ausgeglichen'}</span></div>
    </section>` : html`<p class="notice">Die Kasse ist noch nicht eingerichtet. Zuerst den <strong>Anfangsbestand</strong> festlegen (z. B. Bargeld am 01.01.2026) –
      danach lassen sich Kassenbelege erfassen und wie Kontoumsätze verbuchen.</p>`}`;
}

function openingDialog() {
  const c = store.data.cash;
  const el = dialog('Anfangsbestand der Kasse', html`<form id="cash-opening" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Bargeld in der Kasse zu Beginn des Kassenbuchs. Gebucht wird Kasse 1400 an 9100 Eröffnungsbilanz – das zählt nicht im Haushalt.
      ${c.start ? 'Eine Änderung bucht den bisherigen Anfangsbestand gegen.' : ''}</p>
    <div class="formgrid"><label class="field">Tag<input name="day" type="date" required value="${c.start || `${store.year >= 2026 ? store.year : 2026}-01-01`}"></label>
      <label class="field">Betrag in Euro<input name="amount" inputmode="decimal" required value="${c.opening ? toInput(c.opening.amount) : ''}" placeholder="z. B. 250,00"></label></div>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  el.querySelector('form [data-close]').onclick = () => el.close();
  el.querySelector('#cash-opening').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/kasse-anfang', Object.fromEntries(new FormData(e.target)));
      el.close();
      await refresh();
      toast('Anfangsbestand der Kasse gespeichert.');
    });
  };
}

function newDialog() {
  const el = dialog('Kassenbeleg erfassen', html`<form id="cash-form" style="display:flex;flex-direction:column;gap:14px">
    <div class="kv-chips" role="radiogroup" aria-label="Art">${KINDS.map(([k, label], i) => html`<label class="check"><input type="radio" name="kind" value="${k}" ${i ? '' : 'checked'}> ${label}</label>`)}</div>
    <div class="formgrid">
      <label class="field">Datum<input name="day" type="date" required min="${store.data.cash.start}" max="${today()}" value="${today()}"></label>
      <label class="field">Betrag in Euro<input name="amount" inputmode="decimal" required placeholder="z. B. 12,50" autocomplete="off"></label>
      <label class="field span" data-text>Wofür (Text des Belegs)<input name="text" maxlength="200" autocomplete="off" placeholder="z. B. Getränke Stammtisch, Teilnahmebeitrag bar"></label>
      <label class="field span">Name (Empfänger bzw. Einzahler)<input name="name" maxlength="200" autocomplete="off" placeholder="optional"></label>
      <label class="field span">Beleg (Quittung, Kassenbon) als PDF, PNG oder JPG<input name="file" type="file" accept=".pdf,.png,.jpg,.jpeg"></label>
    </div>
    <p class="small muted" data-hint></p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Erfassen</button></div></form>`, { wide: true });
  const form = el.querySelector('#cash-form');
  const hint = () => {
    const kind = form.kind.value;
    mount(el.querySelector('[data-hint]'), ['withdraw', 'deposit'].includes(kind)
      ? 'Geldtransit: wird sofort gegen 2380 gebucht. Im Kontoauszug den Gegenumsatz ebenfalls auf 2380 buchen – dann ist der Transit ausgeglichen.'
      : 'Danach im Buchungsplatz der Kasse verbuchen – genau wie einen Kontoumsatz (Haushaltsposten, Mitgliedsbeitrag mit Mitglied, Rechnung, Eingangsrechnung …).');
  };
  form.querySelectorAll('[name=kind]').forEach((r) => (r.onchange = hint));
  hint();
  form.querySelector('[data-close]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const data = Object.fromEntries(new FormData(form));
      const file = form.file.files[0];
      delete data.file;
      if (file) data.file = { name: file.name, content: await fileToBase64(file) };
      const r = await api('/api/kasse-beleg', data);
      el.close();
      const transit = ['withdraw', 'deposit'].includes(data.kind);
      store.cash.filter = transit ? 'book' : 'open';
      store.cash.selected = r.id;
      await refresh();
      toast(transit ? `${r.number} erfasst und auf Geldtransit 2380 gebucht.` : `${r.number} erfasst – jetzt verbuchen.`);
    });
  };
}

function voidDialog(b) {
  const el = dialog(`Kassenbeleg ${b.bank_ref} ungültig`, html`<form id="cash-void" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">${date(b.booked)} · ${signed(b.amount)} · ${b.reference || ''}. Der Beleg bleibt im Kassenbuch sichtbar, zählt aber nicht mehr.</p>
    <label class="field">Grund<input name="reason" required maxlength="200" autocomplete="off" placeholder="z. B. Tippfehler im Betrag, doppelt erfasst"></label>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn danger">Ungültig</button></div></form>`);
  el.querySelector('form [data-close]').onclick = () => el.close();
  el.querySelector('#cash-void').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/kasse-ungueltig', { id: b.id, reason: e.target.reason.value });
      el.close();
      store.cash.selected = null;
      await refresh();
      toast(`${b.bank_ref} ist ungültig.`);
    });
  };
}

const KIND_LABEL = { anfang: 'Anfangsbestand', bank: 'aus Kontoauszug' };
const STATE = { open: ['offen', 'warn'], parked: ['geparkt', 'warn'], reversed: ['später storniert', ''], storno: ['Storno', ''] };

/** Kassenbuch des Jahres: alle Bewegungen mit Beleg, Kontierung und Bestand. */
function bookView(box) {
  mount(box, html`<p class="small muted">Wird geladen …</p>`);
  safe(async () => {
    const k = await api('/api/kasse?year=' + store.year);
    mount(box, html`<section class="panel">
      <div class="panel-head"><h2>Kassenbuch ${store.year}</h2>
        <span style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn" href="/api/kasse.pdf?year=${store.year}" target="_blank" rel="noopener">${icon('file', 16)} PDF</a>
          <button type="button" class="btn" data-cash-csv>CSV</button></span></div>
      <p class="small muted">Übertrag 01.01.: ${eur(k.carry)}${k.opening_in_year ? ` · Anfangsbestand ${eur(k.opening_in_year)}` : ''} · Einnahmen ${eur(k.income)} · Ausgaben ${eur(k.expense)} · Bestand ${eur(k.end)}${k.missing_receipt ? ` · ${k.missing_receipt} Ausgaben ohne Belegdatei` : ''}</p>
      ${k.lines.length ? table(['Datum', 'Beleg', 'Text', 'Kontierung', { label: 'Einnahme', className: 'num' }, { label: 'Ausgabe', className: 'num' }, { label: 'Bestand', className: 'num' }],
        k.lines.map((x) => html`<tr class="${x.bank_id ? 'clickable' : ''}" ${x.bank_id ? html`data-cash-row="${x.bank_id}" tabindex="0"` : ''}>
          <td class="nowrap">${date(x.day)}</td><td class="nowrap">${x.number || html`<span class="muted">${KIND_LABEL[x.kind] || ''}</span>`}${x.attachment ? html` <a href="/api/kasse-datei?id=${x.bank_id}" target="_blank" rel="noopener" title="Beleg ansehen">${icon('eye', 14)}</a>` : ''}</td>
          <td>${x.text || ''}${x.name ? html`<span class="sub">${x.name}</span>` : ''}${STATE[x.state] ? html` ${badge(...STATE[x.state])}` : ''}</td>
          <td class="small">${x.posted.map((p, i) => html`${i ? html`<br>` : ''}${p.account} · ${kvName(p.kv)}${p.title ? ` · ${titleLabel(p.title)}` : ''}`)}</td>
          <td class="num">${x.amount > 0 ? eur(x.amount) : ''}</td><td class="num">${x.amount < 0 ? eur(-x.amount) : ''}</td><td class="num">${eur(x.balance)}</td></tr>`))
        : empty('Keine Bewegungen', `Für ${store.year} gibt es noch keine Kassenbewegungen.`)}
      ${k.void.length ? html`<h3>Ungültige Belege</h3>${table(['Datum', 'Beleg', 'Text', { label: 'Betrag', className: 'num' }, 'Grund'],
        k.void.map((v) => html`<tr><td>${date(v.booked)}</td><td>${v.bank_ref}</td><td>${v.reference || ''}</td><td class="num">${signed(v.amount)}</td><td class="small">${v.note || ''}</td></tr>`))}` : ''}
      <p class="small muted">Bestand = Anfangsbestand + Kassenbelege + Buchungen auf 1400 (auch aus dem Kontoauszug). Er darf an keinem Tag negativ sein.
        Abhebung/Einzahlung: Kassenbeleg und Kontoumsatz jeweils auf 2380 buchen.</p></section>`);
    box.querySelector('[data-cash-csv]').onclick = () => safe(() => download(`/api/kasse.csv?year=${store.year}`, `Kassenbuch-${store.year}.csv`));
    box.querySelectorAll('[data-cash-row]').forEach((row) => {
      const open = () => { store.cash.filter = 'all'; store.cash.selected = Number(row.dataset.cashRow); cash(document.getElementById('main')); };
      row.onclick = open;
      row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    });
  });
}

export function cash(main) {
  if (!store.data.cash.start) {
    mount(main, html`<div class="page wide">${head()}</div>`);
    main.querySelector('[data-opening]').onclick = openingDialog;
    return;
  }
  renderLedger(main, {
    ledger: 'cash', state: store.cash, head, voidDialog,
    empty: `Für ${store.year} sind keine offenen Kassenbelege vorhanden. Neue Belege über „Kassenbeleg erfassen“.`,
    extraFilters: [['book', 'Kassenbuch']],
    custom: { book: bookView },
    bind: (el) => {
      el.querySelector('[data-opening]').onclick = openingDialog;
      el.querySelector('[data-new-cash]').onclick = newDialog;
    },
  });
}
