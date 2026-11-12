// Offene Posten (Buchhaltung): offene Mitgliedsbeiträge je Mitglied mit den OP-Listen der Kreisverbände (ab 0.31 hier statt unter
// Mitglieder; Server: opliste.py), Forderungen (Ausgangsrechnungen) und Verbindlichkeiten (Eingangsrechnungen) mit Fälligkeit und Alter.
import { api, download } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date } from '../format.js';
import { store, openBills, daysOverdue, go, kvName } from '../store.js';
import { pageHead, table, empty, badge, safe, icon } from '../ui.js';
import { mailBadge, reminderBadge, reminderDialog } from './mailing.js';
import { paymentDialog, approvalBadge } from './bills.js';
import { reconcileDialog } from './reconcile.js';
import { account } from './members.js';
import { sortName } from '../member-picker.js';
import { ovLabel } from '../gliederung.js';

let side = 'dues';
let opKv = '', opOv = '';   // OP-Liste: gewählter Kreis- und Ortsverband ('' = alle)
let opSeq = 0;

/** Offene Mitgliedsbeiträge je Mitglied – auf Kreis- und Ortsverband eingrenzbar, als PDF/CSV für den Kreisverband und als Paket für
 *  alle Kreisverbände (ZIP). Gleiche Zahlen wie Beitragskonto und Rückstände. */
function duesPanel(box) {
  async function load() {
    const mine = ++opSeq;
    const r = await api(`/api/op-liste?kv=${opKv}&ov=${opOv}`);
    if (mine !== opSeq || !box.isConnected) return;
    const kvs = store.data.kv.filter((k) => store.data.members.some((m) => m.kv === k.code));
    const ovChoices = opKv ? (store.data.ovs || []).filter((o) => o.kv === opKv && (o.members || o.active)) : [];
    const query = `kv=${opKv}&ov=${opOv}`, stamp = new Date().toISOString().slice(0, 10);
    mount(box, html`<div class="toolbar"><label class="field inline">Kreisverband<select data-kv><option value="">alle</option>
        ${kvs.map((k) => html`<option value="${k.code}" ${k.code === opKv ? 'selected' : ''}>${k.name}</option>`)}</select></label>
      ${opKv ? html`<label class="field inline">Ortsverband<select data-ov><option value="">alle</option><option value="none" ${opOv === 'none' ? 'selected' : ''}>ohne Ortsverband</option>
        ${ovChoices.map((o) => html`<option value="${o.id}" ${String(o.id) === opOv ? 'selected' : ''}>${ovLabel(o)}</option>`)}</select></label>` : ''}
      <a class="btn primary" href="/api/op-liste.pdf?${query}" target="_blank" rel="noopener">${icon('file', 16)} OP-Liste (PDF)</a>
      <button type="button" class="btn" data-csv>${icon('download', 16)} CSV</button>
      <button type="button" class="btn" data-zip title="Je Kreisverband eine OP-Liste als PDF und CSV">${icon('download', 16)} Alle Kreisverbände (ZIP)</button></div>
      ${r.unassigned ? html`<p class="notice warn">${r.unassigned} Beitragszahlungen sind noch keinem Mitglied zugeordnet (Beitragseingänge bzw. Klärfälle) – bis dahin kann die Liste zu hoch sein.</p>` : ''}
      <section class="cards"><div class="card"><span class="label">Offene Beiträge (Stand heute)</span><span class="value ${r.total ? 'over' : ''}">${eur(r.total)}</span>
          <span class="note">${r.count} ${r.count === 1 ? 'Mitglied' : 'Mitglieder'} · ${r.kv_name}${r.ov_name ? ` · ${r.ov_name}` : ''}</span></div>
        ${r.by_year.map((y) => html`<div class="card"><span class="label">Beitragsjahr ${y.year}</span><span class="value">${eur(y.open)}</span><span class="note">offen</span></div>`)}</section>
      ${!opKv && r.by_kv.length ? html`<section class="panel"><h2 style="margin-bottom:4px">OP-Listen je Kreisverband</h2>
        <p class="small muted" style="margin-bottom:8px">Mit Kontaktdaten, damit der Kreisverband nachfassen kann – zum Weitergeben als PDF oder CSV.</p>
        ${table(['Kreisverband', { label: 'Mitglieder', className: 'num' }, { label: 'Offen', className: 'num' }, 'OP-Liste'], r.by_kv.map((k) => html`<tr>
        <td><button type="button" class="link" data-pick-kv="${k.code}">${k.name}</button></td><td class="num">${k.count}</td><td class="num">${eur(k.open)}</td>
        <td class="nowrap"><a class="btn small" href="/api/op-liste.pdf?kv=${k.code}" target="_blank" rel="noopener">PDF</a>
          <button type="button" class="btn small" data-kv-csv="${k.code}">CSV</button></td></tr>`))}</section>` : ''}
      <section class="panel" style="padding:6px 12px">${r.members.length ? html`<h2 style="padding:12px 8px 4px">${opKv ? `${r.kv_name}${r.ov_name ? ` · ${r.ov_name}` : ''} · ` : ''}${r.members.length} ${r.members.length === 1 ? 'Mitglied' : 'Mitglieder'} mit offenen Beiträgen</h2>` : ''}
      ${r.members.length ? table(['Mitglied', 'Kreis-/Ortsverband', 'Offen je Jahr', { label: 'Offen', className: 'num' }, 'Hinweis'], r.members.map((x) => html`<tr class="clickable" data-member="${x.id}" tabindex="0">
        <td><strong style="font-weight:500">${sortName(x)}</strong><span class="sub">${x.number}${x.email ? ` · ${x.email}` : ' · keine E-Mail'}</span></td>
        <td>${x.kv_name}${x.ov ? html`<span class="sub">${x.ov}</span>` : ''}</td>
        <td class="small">${x.years.map((y, i) => html`${i ? html`<br>` : ''}${y.year}: ${eur(y.open)}`)}</td><td class="num over">${eur(x.open)}</td>
        <td class="small">${x.mandate ? html`${badge('Lastschrift', 'ok')} ` : ''}${x.left_on ? `ausgetreten ${date(x.left_on)} ` : ''}${
          Math.max(0, ...x.years.map((y) => y.reminders)) ? `${Math.max(...x.years.map((y) => y.reminders))}× erinnert` : ''}</td></tr>`))
        : empty('Keine offenen Beiträge', 'Alle fälligen Beiträge sind bezahlt.')}</section>
      <p class="small muted">Offen = fälliger Beitrag (Satzung § 49) minus Zahlungen seit Datenbeginn; Zahlungen decken die ältesten Beiträge zuerst. Kreis- und
        Ortsverband = heutige Zuordnung. „Lastschrift“: gültiges Mandat – gilt nach § 49 Abs. 5 als gezahlt, solange der Einzug nicht scheitert. Die Listen
        enthalten Kontaktdaten: nur an die Mitgliederbetreuung des Kreisverbands weitergeben.</p>
      ${r.issues.length ? html`<section class="panel"><h2>Beitrag zu klären · ${r.issues.length}</h2><p class="small muted">Beitrag lässt sich nicht berechnen – im Beitragskonto „Soll festlegen“.
        Dieses Jahr fehlt in der OP-Liste, bis es festgelegt ist.</p>
        ${table(['Mitglied', 'Kreisverband', 'Grund'], r.issues.map((x) => html`<tr class="clickable" data-member="${x.id}" tabindex="0"><td>${sortName(x)}<span class="sub">${x.number}</span></td>
          <td>${kvName(x.kv)}</td><td class="small">${x.issues.join('; ')}</td></tr>`))}</section>` : ''}`);
    box.querySelector('[data-kv]').onchange = (e) => { opKv = e.target.value; opOv = ''; safe(load); };
    box.querySelector('[data-ov]')?.addEventListener('change', (e) => { opOv = e.target.value; safe(load); });
    box.querySelector('[data-csv]').onclick = () => safe(() => download(`/api/op-liste.csv?${query}`, `OP-Liste${opKv ? '-' + opKv : ''}-${stamp}.csv`));
    box.querySelector('[data-zip]').onclick = () => safe(() => download('/api/op-listen.zip', `OP-Listen-${stamp}.zip`));
    box.querySelectorAll('[data-kv-csv]').forEach((b) => (b.onclick = () => safe(() => download(`/api/op-liste.csv?kv=${b.dataset.kvCsv}&ov=`, `OP-Liste-${b.dataset.kvCsv}-${stamp}.csv`))));
    box.querySelectorAll('[data-pick-kv]').forEach((b) => (b.onclick = () => { opKv = b.dataset.pickKv; opOv = ''; safe(load); }));
    box.querySelectorAll('[data-member]').forEach((row) => {
      row.onclick = () => account(Number(row.dataset.member));
      row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); account(Number(row.dataset.member)); } };
    });
  }
  safe(load);
}

const ageBadge = (due) => {
  const d = daysOverdue(due);
  if (d > 60) return badge(`${d} Tage überfällig`, 'danger');
  if (d > 0) return badge(`${d} Tage überfällig`, 'warn');
  return d >= -14 ? badge(d === 0 ? 'heute fällig' : `fällig in ${-d} Tagen`) : html`<span class="muted small">fällig ${date(due)}</span>`;
};

function summary(items, amountOf) {
  const sum = (list) => list.reduce((s, x) => s + amountOf(x), 0);
  const overdue = items.filter((x) => daysOverdue(x.due) > 0);
  const soon = items.filter((x) => daysOverdue(x.due) <= 0 && daysOverdue(x.due) >= -14);
  return html`<div class="cards">
    <div class="card"><span class="label">Offen gesamt</span><span class="value">${eur(sum(items))}</span><span class="note">${items.length} Posten</span></div>
    <div class="card"><span class="label">Davon überfällig</span><span class="value">${eur(sum(overdue))}</span><span class="note">${overdue.length} Posten</span></div>
    <div class="card"><span class="label">Fällig in 14 Tagen</span><span class="value">${eur(sum(soon))}</span><span class="note">${soon.length} Posten</span></div></div>`;
}

export function openItems(main) {
  if (store.openSide) { side = store.openSide; store.openSide = null; }   // Sprung von Start, Beitragseingängen oder Auswertung
  if (store.opKv !== undefined && store.opKv !== null) { opKv = store.opKv; opOv = ''; store.opKv = null; }
  const receivables = store.data.invoices.filter((i) => i.status === 'issued' && i.total > i.paid).sort((a, b) => a.due.localeCompare(b.due));
  const payables = openBills().sort((a, b) => a.due.localeCompare(b.due));
  mount(main, html`<div class="page">
    ${pageHead('Buchhaltung · Stichtag heute · alle Jahre', 'Offene Posten', side === 'dues' ? '' : html`<button type="button" class="btn" data-reconcile>Abgleich mit Kontoumsätzen</button>
      ${side === 'payables' ? html`<button type="button" class="btn primary" data-pay>Zahlungsvorschlag</button>`
        : html`<button type="button" class="btn primary" data-remind>Zahlungserinnerungen</button>`}`)}
    <div class="tabs" role="tablist" aria-label="Seite">
      <button type="button" role="tab" data-side="dues" aria-selected="${side === 'dues'}">Mitgliedsbeiträge<span class="n">${store.data.dues_book.arrears_count}</span></button>
      <button type="button" role="tab" data-side="receivables" aria-selected="${side === 'receivables'}">Rechnungen<span class="n">${receivables.length}</span></button>
      <button type="button" role="tab" data-side="payables" aria-selected="${side === 'payables'}">Eingangsrechnungen<span class="n">${payables.length}</span></button></div>
    ${side === 'dues' ? html`<div class="stack" style="gap:24px" data-op><p class="small muted">Wird geladen …</p></div>` : side === 'receivables' ? html`
      ${summary(receivables, (i) => i.total - i.paid)}
      <section class="panel" style="padding:6px 12px">${receivables.length ? table(['Rechnung', 'Schuldner', 'Fällig', { label: 'Betrag', className: 'num' }, { label: 'Offen', className: 'num' }, 'Alter', 'Versand', ''],
        receivables.map((i) => html`<tr><td><strong style="font-weight:600">${i.number}</strong><span class="sub">${i.kind === 'dues' ? 'Beitrag ' + i.year : 'Rechnung'}</span></td>
          <td>${i.recipient}<span class="sub">${i.mail_to || 'keine E-Mail'}</span></td><td>${date(i.due)}</td><td class="num">${eur(i.total)}</td>
          <td class="num"><strong>${eur(i.total - i.paid)}</strong></td><td>${ageBadge(i.due)}</td><td>${mailBadge(i)} ${reminderBadge(i)}</td>
          <td><a class="btn small" href="/api/invoice.pdf?id=${i.id}" target="_blank" rel="noopener">PDF</a></td></tr>`))
        : empty('Keine offenen Forderungen', 'Alle Rechnungen sind bezahlt.')}</section>
      <p class="small muted">Zahlungseingänge werden über den Abgleich oder im Buchungsplatz zugeordnet. Versand und Druck unter <button type="button" class="link" data-go="invoices">Rechnungen</button>.</p>`
    : html`
      ${summary(payables, (b) => b.open)}
      <section class="panel" style="padding:6px 12px">${payables.length ? table(['Beleg', 'Kreditor', 'Fällig', { label: 'Betrag', className: 'num' }, { label: 'Offen', className: 'num' }, 'Alter', 'Status'],
        payables.map((b) => html`<tr><td><strong style="font-weight:600">${b.number}</strong><span class="sub">${b.ext_number || '—'}</span></td>
          <td>${b.creditor}<span class="sub">${b.iban ? '' : 'IBAN fehlt'}</span></td><td>${date(b.due)}</td><td class="num">${eur(b.total)}</td>
          <td class="num"><strong>${eur(b.open)}</strong></td><td>${ageBadge(b.due)}</td>
          <td>${b.payment_reversed ? badge('Zahlung storniert – prüfen', 'danger') : b.in_bank ? badge('Abbuchung im Kontoauszug', 'warn') : b.transferred ? badge('Angewiesen ' + date(b.transferred).slice(0, 6), 'ok') : b.paid ? badge('Teilweise bezahlt', 'warn') : badge('Nicht angewiesen')} ${approvalBadge(b)}</td></tr>`))
        : empty('Keine offenen Verbindlichkeiten', 'Neue Eingangsrechnungen unter „Eingangsrechnungen“ erfassen.')}</section>
      <p class="small muted">„Angewiesen“ heißt: im Online-Banking überwiesen, die Abbuchung ist noch nicht zugeordnet. Erfassen unter <button type="button" class="link" data-go="bills">Eingangsrechnungen</button>.</p>`}
  </div>`);
  main.querySelectorAll('[data-side]').forEach((b) => (b.onclick = () => { side = b.dataset.side; openItems(main); }));
  main.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => go(b.dataset.go)));
  main.querySelector('[data-reconcile]')?.addEventListener('click', () => safe(reconcileDialog));
  const op = main.querySelector('[data-op]');
  if (op) duesPanel(op);
  const remind = main.querySelector('[data-remind]');
  if (remind) remind.onclick = () => safe(() => reminderDialog());
  const pay = main.querySelector('[data-pay]');
  if (pay) pay.onclick = () => safe(paymentDialog);
}
