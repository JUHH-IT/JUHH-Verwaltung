// Kasse (ab 0.25, Wunsch Lennert Gust 08.11.2026): Kassenbelege erfassen und im selben Buchungsplatz wie den Kontoauszug verbuchen
// (views/bank.js), Kassenbuch mit Bestand, Anfangsbestand, Geldtransit Bank ↔ Kasse über 2380. Verbindlich prüft der Server (kasse.py).
// Ab 0.59 mehrere Kassen (Bargeld, PayPal …): Umschalter, „Weitere Kasse einrichten“; alles gilt für die gewählte Kasse (currentKasse).
import { api, fileToBase64, download } from '../api.js';
import { html, mount } from '../html.js';
import { eur, signed, date, toInput, todayIso } from '../format.js';
import { store, refresh, ledgerRows, currentKasse, setKasse, kvName, titleLabel } from '../store.js';
import { pageHead, icon, empty, dialog, safe, toast, table, badge } from '../ui.js';
import { renderLedger } from './bank.js';

const today = () => todayIso();   // Ortszeit (nicht UTC)
/** Wörter je Kasse: die Bargeldkasse (Kennung 0) spricht von Bargeld, Abhebung und Einzahlung – jede weitere (PayPal) von Umbuchungen. */
const words = (k) => (k.id ? {
  beleg: 'Beleg', bestand: `Bestand ${k.name}`, von: 'Umbuchung von der Bank', auf: 'Umbuchung auf die Bank', inNote: 'Belege inkl. Umbuchungen von der Bank',
  outNote: 'Belege inkl. Umbuchungen auf die Bank', text: 'z. B. Teilnahmebeitrag, Gebühr', file: 'Beleg (z. B. Zahlungsbestätigung, Rechnung)', buch: `Kassenbuch ${k.name}`,
} : {
  beleg: 'Kassenbeleg', bestand: 'Kassenbestand', von: 'Abhebung von der Bank', auf: 'Einzahlung auf die Bank', inNote: 'Kassenbelege inkl. Abhebungen',
  outNote: 'Kassenbelege inkl. Einzahlungen', text: 'z. B. Getränke Stammtisch, Teilnahmebeitrag bar', file: 'Beleg (Quittung, Kassenbon)', buch: 'Kassenbuch',
});

function head() {
  const c = store.data.cash;
  const k = currentKasse();
  const w = words(k);
  const year = ledgerRows('cash');
  const offen = k.transit || [];   // Belege DIESER Kasse auf 2380, zu denen der Kontoumsatz fehlt (kasse.transit)
  const ohneBeleg = c.transit.bank.length;
  return html`${pageHead(`Haushaltsjahr ${store.year}`, 'Kasse', html`
      <button type="button" class="btn" data-add-kasse>Weitere Kasse einrichten</button>
      <button type="button" class="btn" data-opening>${k.start ? 'Anfangsbestand' : 'Anfangsbestand festlegen'}</button>
      ${k.start ? html`<button type="button" class="btn primary" data-new-cash>${icon('plus', 16)} ${w.beleg} erfassen</button>` : ''}`)}
    ${c.kassen.length > 1 ? html`<div class="segmented" role="group" aria-label="Kasse wählen" style="align-self:flex-start">${c.kassen.map((x) => html`
      <button type="button" data-kasse="${x.id}" aria-pressed="${x.id === k.id}">${x.name}<span class="zahl">${x.start ? eur(x.balance) : 'nicht eingerichtet'}</span></button>`)}</div>` : ''}
    ${k.start ? html`<section class="cards" aria-label="Kasse">
      <div class="card"><span class="label">${w.bestand} heute</span><span class="value">${eur(k.balance)}</span>
        <span class="note">Anfangsbestand ${eur(k.opening?.amount || 0)} am ${date(k.start)} · Konto ${k.account}</span></div>
      <div class="card"><span class="label">Einnahmen ${store.year}</span><span class="value">${eur(year.filter((b) => b.amount > 0 && b.state !== 'void').reduce((s, b) => s + b.amount, 0))}</span>
        <span class="note">${w.inNote}</span></div>
      <div class="card"><span class="label">Ausgaben ${store.year}</span><span class="value">${eur(-year.filter((b) => b.amount < 0 && b.state !== 'void').reduce((s, b) => s + b.amount, 0))}</span>
        <span class="note">${w.outNote}</span></div>
      <div class="card" data-transit><span class="label">Geldtransit 2380 offen</span><span class="value ${offen.length ? 'over' : ''}">${eur(offen.reduce((s, x) => s + Math.abs(x.amount), 0))}</span>
        <span class="note">${offen.length ? `${offen.length} ${offen.length === 1 ? 'Beleg' : 'Belege'} ohne Gegenseite im Kontoauszug: ${offen.slice(0, 3).map((x) => `${x.number} vom ${date(x.day)}`).join(', ')}${offen.length > 3 ? ' …' : ''} – den Kontoumsatz auf 2380 buchen`
          : 'kein Beleg dieser Kasse offen'}${ohneBeleg ? ` · Kontoauszug: ${ohneBeleg} ${ohneBeleg === 1 ? 'Buchung' : 'Buchungen'} auf 2380 ohne Kassenbeleg` : ''}${c.transit.saldo ? ` · Saldo 2380: ${signed(c.transit.saldo)}` : ''}</span></div>
    </section>` : html`<p class="notice">Die Kasse ist noch nicht eingerichtet. Zuerst den <strong>Anfangsbestand</strong> festlegen (z. B. Bargeld am 01.01.2026) –
      danach lassen sich Kassenbelege erfassen und wie Kontoumsätze verbuchen.</p>`}`;
}

function openingDialog() {
  const k = currentKasse();
  const el = dialog(`Anfangsbestand ${k.name}`, html`<form id="cash-opening" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">${k.id ? `Bestand von ${k.name} zu Beginn des Kassenbuchs. Gebucht wird ${k.name} ${k.account}` : 'Bargeld in der Kasse zu Beginn des Kassenbuchs. Gebucht wird Kasse 1400'} an 9100 Eröffnungsbilanz – das zählt nicht im Haushalt.
      ${k.start ? 'Eine Änderung bucht den bisherigen Anfangsbestand gegen.' : ''}</p>
    <div class="formgrid"><label class="field">Tag<input name="day" type="date" required value="${k.start || `${store.year >= 2026 ? store.year : 2026}-01-01`}"></label>
      <label class="field">Betrag in Euro<input name="amount" inputmode="decimal" required value="${k.opening ? toInput(k.opening.amount) : ''}" placeholder="z. B. 250,00"></label></div>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  el.querySelector('form [data-close]').onclick = () => el.close();
  el.querySelector('#cash-opening').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/kasse-anfang', { ...Object.fromEntries(new FormData(e.target)), kasse: k.id });
      el.close();
      await refresh();
      toast(`Anfangsbestand ${k.name} gespeichert.`);
    });
  };
}

/** Weitere Kasse einrichten (ab 0.59), z. B. PayPal: eigenes Bestandskonto, eigene Belegnummern, eigenes Kassenbuch. */
function addDialog() {
  const first = `${store.year}-01-01`;
  const el = dialog('Weitere Kasse einrichten', html`<form id="cash-add" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Für einen weiteren Geldbestand neben Bargeld und Bankkonto, z. B. ein PayPal-Konto. Er wird geführt wie die Kasse: Belege erfassen, verbuchen wie Kontoumsätze,
      eigenes Kassenbuch. Geld zwischen dieser Kasse und der Bank läuft über 2380 (Geldtransit).</p>
    <div class="formgrid">
      <label class="field span">Name<input name="name" required maxlength="40" autocomplete="off" placeholder="z. B. PayPal"></label>
      <label class="field">Konto (1401–1499, Geldbestände)<input name="account" required inputmode="numeric" pattern="14[0-9][0-9]" maxlength="4" autocomplete="off" value="${store.data.cash.next_account}"></label>
      <label class="field">Belegkürzel<input name="prefix" required maxlength="4" pattern="[A-Za-z]{1,4}" autocomplete="off" autocapitalize="characters" autocorrect="off" spellcheck="false" placeholder="z. B. PP"></label>
      <label class="field">Beginn (Stichtag)<input name="day" type="date" required max="${today()}" value="${first > today() ? today() : first}"></label>
      <label class="field">Anfangsbestand in Euro<input name="amount" inputmode="decimal" required placeholder="z. B. 0,00"></label>
    </div>
    <p class="small muted">Das vorgeschlagene Konto ist frei und wird im Kontenrahmen angelegt. Die Belege heißen dann z. B. PP-${store.year}-0001.
      Name, Konto und Belegkürzel lassen sich später nicht ändern. Der Anfangsbestand ist der Kontostand am Stichtag (Buchung an 9100 Eröffnungsbilanz, zählt nicht im Haushalt).</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Einrichten</button></div></form>`, { wide: true });
  el.querySelector('form [data-close]').onclick = () => el.close();
  el.querySelector('#cash-add').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const data = Object.fromEntries(new FormData(e.target));
      const r = await api('/api/kasse-neu', data);
      el.close();
      Object.assign(store.cash, { filter: 'open', selected: null });
      setKasse(r.id);
      await refresh();
      toast(`${data.name.trim()} ist eingerichtet.`);
    });
  };
}

function newDialog() {
  const k = currentKasse();
  const w = words(k);
  const kinds = [['out', 'Ausgabe'], ['in', 'Einnahme'], ['withdraw', w.von], ['deposit', w.auf]];
  const el = dialog(`${w.beleg} erfassen – ${k.name}`, html`<form id="cash-form" style="display:flex;flex-direction:column;gap:14px">
    <div class="kv-chips" role="radiogroup" aria-label="Art">${kinds.map(([id, label], i) => html`<label class="check"><input type="radio" name="kind" value="${id}" ${i ? '' : 'checked'}> ${label}</label>`)}</div>
    <div class="formgrid">
      <label class="field">Datum<input name="day" type="date" required min="${k.start}" max="${today()}" value="${today()}"></label>
      <label class="field">Betrag in Euro<input name="amount" inputmode="decimal" required placeholder="z. B. 12,50" autocomplete="off"></label>
      <label class="field span" data-text>Wofür (Text des Belegs)<input name="text" maxlength="200" autocomplete="off" placeholder="${w.text}"></label>
      <label class="field span">Name (Empfänger bzw. Einzahler)<input name="name" maxlength="200" autocomplete="off" placeholder="optional"></label>
      <label class="field span">${w.file} als PDF, PNG oder JPG<input name="file" type="file" accept=".pdf,.png,.jpg,.jpeg"></label>
    </div>
    <p class="small muted" data-hint></p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Erfassen</button></div></form>`, { wide: true });
  const form = el.querySelector('#cash-form');
  const hint = () => {
    const kind = form.kind.value;
    mount(el.querySelector('[data-hint]'), ['withdraw', 'deposit'].includes(kind)
      ? 'Geldtransit: wird sofort gegen 2380 gebucht. Im Kontoauszug den Gegenumsatz ebenfalls auf 2380 buchen – dann ist der Transit ausgeglichen.'
      : `Danach im Buchungsplatz der Kasse verbuchen – genau wie einen Kontoumsatz (Haushaltsposten, Mitgliedsbeitrag mit Mitglied, Rechnung, Eingangsrechnung …).${k.id ? ' Gebühren sind eine Ausgabe.' : ''}`);
  };
  form.querySelectorAll('[name=kind]').forEach((r) => (r.onchange = hint));
  hint();
  form.querySelector('[data-close]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const data = { ...Object.fromEntries(new FormData(form)), kasse: k.id };
      const file = form.file.files[0];
      delete data.file;
      if (file) data.file = { name: file.name, content: await fileToBase64(file) };
      const r = await api('/api/kasse-beleg', data);
      el.close();
      const transit = ['withdraw', 'deposit'].includes(data.kind);
      store.cash.filter = transit ? 'book' : 'open';
      store.cash.selected = r.id;
      await refresh();
      toast(`${k.name}: ${transit ? `${r.number} erfasst und auf Geldtransit 2380 gebucht.` : `${r.number} erfasst – jetzt verbuchen.`}`);
    });
  };
}

function voidDialog(b) {
  const el = dialog(`${b.kasse ? 'Beleg' : 'Kassenbeleg'} ${b.bank_ref} ungültig`, html`<form id="cash-void" style="display:flex;flex-direction:column;gap:14px">
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
    const cur = currentKasse();
    const query = `year=${store.year}&kasse=${cur.id}`;
    const k = await api('/api/kasse?' + query);
    mount(box, html`<section class="panel">
      <div class="panel-head"><h2>${words(cur).buch} ${store.year}</h2>
        <span style="display:flex;gap:8px;flex-wrap:wrap"><a class="btn" href="/api/kasse.pdf?${query}" target="_blank" rel="noopener">${icon('file', 16)} PDF</a>
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
      <p class="small muted">Bestand = Anfangsbestand + Kassenbelege + Buchungen auf ${cur.account} (auch aus dem Kontoauszug). Er darf an keinem Tag negativ sein.
        ${cur.id ? 'Umbuchung von der bzw. auf die Bank' : 'Abhebung/Einzahlung'}: Kassenbeleg und Kontoumsatz jeweils auf 2380 buchen.</p></section>`);
    box.querySelector('[data-cash-csv]').onclick = () => safe(() => download('/api/kasse.csv?' + query, `${words(cur).buch.replaceAll(' ', '-')}${cur.id ? `-${cur.account}` : ''}-${store.year}.csv`));
    box.querySelectorAll('[data-cash-row]').forEach((row) => {
      const open = () => { store.cash.filter = 'all'; store.cash.selected = Number(row.dataset.cashRow); cash(document.getElementById('main')); };
      row.onclick = open;
      row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    });
  });
}

function bind(el) {
  el.querySelector('[data-add-kasse]').onclick = addDialog;
  el.querySelector('[data-opening]').onclick = openingDialog;
  el.querySelector('[data-new-cash]')?.addEventListener('click', newDialog);
  el.querySelectorAll('[data-kasse]').forEach((b) => (b.onclick = () => {
    store.cash.selected = null;
    setKasse(Number(b.dataset.kasse));
    cash(document.getElementById('main'));
  }));
}

export function cash(main) {
  const k = currentKasse();
  if (!k.start) {
    mount(main, html`<div class="page wide">${head()}</div>`);
    bind(main);
    return;
  }
  renderLedger(main, {
    ledger: 'cash', state: store.cash, head, voidDialog, bind,
    empty: `Für ${store.year} sind keine offenen ${k.id ? 'Belege' : 'Kassenbelege'} vorhanden. Neue Belege über „${words(k).beleg} erfassen“.`,
    extraFilters: [['book', 'Kassenbuch']],
    custom: { book: bookView },
  });
}
