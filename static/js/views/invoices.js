// Rechnungen: Liste mit Versandstatus, Beitragslauf mit Vorschau, neue (Serien-)Rechnung, E-Mail-Versand, Druck, Storno.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date } from '../format.js';
import { store, refresh } from '../store.js';
import { pageHead, icon, table, empty, badge, dialog, confirmAction, safe, mailPreview } from '../ui.js';
import { createDialog } from './invoice-create.js';
import { sendDialog, mailBadge, printLink, afterIssue, erechnungHinweis } from './mailing.js';
import { sepaDialog } from './sepa.js';

let filter = 'all';
const selected = new Set();

export function invoiceBadge(i) {
  if (i.status === 'cancelled') return badge('Storniert');
  if (i.total === i.paid) return badge(i.total ? 'Bezahlt' : 'Beitragsfrei', 'ok');
  return i.paid ? badge('Teilzahlung', 'warn') : badge('Offen', 'warn');
}

const FILTERS = [
  ['all', 'Alle', () => true],
  ['open', 'Offen', (i) => i.status === 'issued' && i.total > i.paid],
  ['unsent', 'Nicht versendet', (i) => i.status === 'issued' && !i.sent_at && i.total > i.paid && !i.pilot],
  ['paid', 'Bezahlt', (i) => i.status === 'issued' && i.total === i.paid],
  ['cancelled', 'Storniert', (i) => i.status === 'cancelled'],
];

async function duesDialog(year = store.year) {
  const rows = await api('/api/dues-preview?year=' + year);
  const ready = (r) => !r.error && !r.existing && r.amount !== null;
  const chosen = new Set(rows.filter(ready).map((r) => r.id));
  const render = () => {
    const sum = rows.filter((r) => chosen.has(r.id)).reduce((s, r) => s + (r.amount || 0), 0);
    const noMail = rows.filter((r) => chosen.has(r.id) && !r.email).length;
    const el = dialog('Beitragslauf', html`
      <div class="toolbar"><label class="field" style="flex-direction:row;align-items:center;gap:10px">Beitragsjahr
        <select id="dues-year">${[store.year - 1, store.year, store.year + 1].map((y) => html`<option ${y === year ? 'selected' : ''}>${y}</option>`)}</select></label>
        <span class="small muted">Staffel und Zahlungsziel aus den Stammdaten; individuelle Beiträge am Mitglied haben Vorrang.</span></div>
      ${erechnungHinweis('dues')}
      ${rows.length ? table(['', 'Mitglied', { label: 'Beitrag', className: 'num' }, 'Fällig', 'Prüfung', 'Vorschau'], rows.map((r) => html`<tr>
        <td><input class="dues-check" type="checkbox" value="${r.id}" ${chosen.has(r.id) ? 'checked' : ''} ${ready(r) ? '' : 'disabled'} aria-label="${r.name} auswählen"></td>
        <td>${r.name}<span class="sub">${r.number} · ${r.email || 'keine E-Mail'}</span></td>
        <td class="num">${r.amount === null ? '—' : eur(r.amount)}${r.basis ? html`<span class="sub">${r.basis}</span>` : ''}</td>
        <td>${r.due ? date(r.due) : '—'}${r.debit && ready(r) ? html`<span class="sub">per Lastschrift</span>` : ''}</td>
        <td>${r.error ? badge(r.error, 'danger') : r.existing ? badge(r.existing) : r.amount === null ? badge('kein Beitrag') : badge('Bereit', 'ok')}</td>
        <td style="white-space:nowrap">${ready(r) ? html`<a class="btn small" href="/api/preview.pdf?dues=${r.id}&year=${year}" target="_blank" rel="noopener">PDF</a>
          <button type="button" class="btn small ghost" data-mail="${r.id}">E-Mail</button>` : ''}</td></tr>`))
        : empty('Keine Mitglieder', 'Zuerst Mitglieder anlegen.')}
      <div class="bulkbar"><strong>${chosen.size} ausgewählt · ${eur(sum)}${noMail ? ` · ${noMail} ohne E-Mail` : ''}</strong>
        <button type="button" class="btn primary" id="issue-dues" ${chosen.size ? '' : 'disabled'}>Beitragsrechnungen ${year} festschreiben</button></div>
      <p class="small muted">Festgeschriebene Rechnungen sind unveränderlich; bereits erstellte werden nicht erneut angelegt. Der Versand folgt im nächsten Schritt.</p>`, { wide: true });
    el.querySelector('#dues-year').onchange = (e) => safe(() => duesDialog(Number(e.target.value)));
    el.querySelectorAll('.dues-check').forEach((c) => (c.onchange = () => { c.checked ? chosen.add(Number(c.value)) : chosen.delete(Number(c.value)); render(); }));
    el.querySelectorAll('[data-mail]').forEach((b) => (b.onclick = () => mailPreview(`dues=${b.dataset.mail}&year=${year}`, 'Vorschau Beitragsrechnung')));
    el.querySelector('#issue-dues').onclick = (e) => safe(async () => {
      e.target.disabled = true;
      try {
        const result = await api('/api/dues', { year, ids: [...chosen] });
        await refresh();
        afterIssue(result.ids, result.ids.length === 1 ? 'Beitragsrechnung' : 'Beitragsrechnungen');
      } finally { e.target.disabled = false; }
    });
  };
  render();
}

export function invoices(main) {
  const all = store.data.invoices;
  const test = FILTERS.find((f) => f[0] === filter)[2];
  const shown = all.filter(test);
  for (const id of [...selected]) if (!all.some((i) => i.id === id)) selected.delete(id);
  const picked = all.filter((i) => selected.has(i.id));
  mount(main, html`<div class="page">
    ${pageHead('Alle Jahre · offene Rechnungen bleiben sichtbar', 'Rechnungen', html`<button type="button" class="btn" data-dues>Beitragslauf</button>
      <button type="button" class="btn" data-send>${icon('mail', 16)} E-Mail-Versand</button>
      <button type="button" class="btn" data-sepa>Lastschriften${store.data.sepa_ready ? ` (${store.data.sepa_ready})` : ''}</button>
      <button type="button" class="btn primary" data-normal>${icon('plus', 16)} Rechnung erstellen</button>`)}
    <div class="tabs" role="tablist" aria-label="Rechnungen filtern">${FILTERS.map(([id, label, f]) => html`
      <button type="button" role="tab" data-filter="${id}" aria-selected="${filter === id}">${label}<span class="n">${all.filter(f).length}</span></button>`)}</div>
    ${picked.length ? html`<div class="bulkbar"><strong>${picked.length} ausgewählt</strong>
      <button type="button" class="link" data-clear>Auswahl aufheben</button>${printLink(picked.map((i) => i.id), 'Drucken')}
      <button type="button" class="btn primary" data-send-picked>${icon('mail', 16)} Per E-Mail senden …</button></div>` : ''}
    <section class="panel" style="padding:6px 12px">${shown.length ? table([html`<input type="checkbox" data-all aria-label="Alle angezeigten auswählen" ${shown.every((i) => selected.has(i.id)) ? 'checked' : ''}>`, 'Nummer', 'Empfänger', 'Fällig', { label: 'Betrag', className: 'num' }, { label: 'Offen', className: 'num' }, 'Status', 'Versand', ''],
      shown.map((i) => html`<tr><td><input type="checkbox" data-pick="${i.id}" ${selected.has(i.id) ? 'checked' : ''} aria-label="${i.number} auswählen"></td>
        <td class="nowrap"><strong style="font-weight:600">${i.number}</strong><span class="sub">${i.kind === 'dues' ? 'Beitrag ' + i.year : 'Rechnung'}</span></td>
        <td>${i.recipient}<span class="sub">${i.mail_to || 'keine E-Mail'}</span></td><td>${date(i.due)}</td><td class="num">${eur(i.total)}</td>
        <td class="num">${i.status === 'cancelled' ? '—' : eur(i.total - i.paid)}</td><td>${invoiceBadge(i)}</td><td>${mailBadge(i)}</td>
        <td style="white-space:nowrap"><a class="btn small" href="/api/invoice.pdf?id=${i.id}" target="_blank" rel="noopener">PDF</a>
          <button type="button" class="btn small ghost" data-look="${i.id}" aria-label="E-Mail-Vorschau ${i.number}">${icon('eye', 14)}</button>
          ${i.status === 'issued' && i.paid === 0 ? html` <button type="button" class="btn small ghost danger" data-cancel="${i.id}">Stornieren</button>` : ''}</td></tr>`))
      : empty('Keine Rechnungen', 'Beitragslauf starten oder eine Rechnung erstellen.')}</section>
    <p class="small muted">Rechnungsnummern werden automatisch fortlaufend vergeben (B = Beitrag, R = Rechnung). Jede versendete E-Mail wird protokolliert; versendete Rechnungen werden nicht versehentlich doppelt verschickt.</p>
  </div>`);
  const again = () => invoices(main);
  main.querySelector('[data-dues]').onclick = () => safe(() => duesDialog());
  main.querySelector('[data-sepa]').onclick = () => safe(sepaDialog);
  main.querySelector('[data-normal]').onclick = createDialog;
  main.querySelector('[data-send]').onclick = () => safe(() => sendDialog());
  const sendPicked = main.querySelector('[data-send-picked]');
  if (sendPicked) sendPicked.onclick = () => safe(() => sendDialog([...selected]));
  const clear = main.querySelector('[data-clear]');
  if (clear) clear.onclick = () => { selected.clear(); again(); };
  main.querySelectorAll('[data-filter]').forEach((b) => (b.onclick = () => { filter = b.dataset.filter; again(); }));
  main.querySelectorAll('[data-pick]').forEach((c) => (c.onchange = () => { c.checked ? selected.add(Number(c.dataset.pick)) : selected.delete(Number(c.dataset.pick)); again(); }));
  const allBox = main.querySelector('[data-all]');
  if (allBox) allBox.onchange = () => { shown.forEach((i) => (allBox.checked ? selected.add(i.id) : selected.delete(i.id))); again(); };
  main.querySelectorAll('[data-look]').forEach((b) => (b.onclick = () => mailPreview('id=' + b.dataset.look, 'Vorschau ' + all.find((i) => i.id === Number(b.dataset.look)).number)));
  main.querySelectorAll('[data-cancel]').forEach((b) => (b.onclick = () => confirmAction('Rechnung stornieren',
    'Der Beleg bleibt erhalten, eine Gegenbuchung hebt die Forderung auf. Ein erneuter Beitragslauf für denselben Zeitraum bleibt zum Schutz vor Doppelrechnungen gesperrt.',
    () => api('/api/cancel-invoice', { id: Number(b.dataset.cancel) }), { label: 'Stornieren', done: 'Rechnung storniert.' })));
}
