// Start (ab 0.31, Wunsch Lennert Gust 10.11.2026; ab 0.57 als Blatt, Wunsch 23.11.2026): Lage in einem Satz, Kennzahlen des Haushalts,
// Aufgaben je Bereich (jede Zeile führt in die Liste dazu), Termine und Zustand der Verwaltung. Jede Person sieht nur ihre Bereiche.
import { download } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date, shortDate, todayIso } from '../format.js';
import { store, go, can, yearBank, openBills, daysOverdue } from '../store.js';
import { pageHead, icon, safe, toast, dialog } from '../ui.js';
import { reviewDialog } from './bills.js';
import { anschriftVollstaendig } from '../mitglieder-listen.js';
import { reminderDialog } from './mailing.js';
import { sepaDialog } from './sepa.js';

const MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];

/** Monatsbericht: Monat des gewählten Haushaltsjahres wählen; Stand ist das Monatsende (laufender Monat: heute). */
function monthlyDialog() {
  const now = todayIso().slice(0, 7), first = (store.data.data_start || '2000-01').slice(0, 7);
  const months = MONTHS.map((name, i) => ({ key: `${store.year}-${String(i + 1).padStart(2, '0')}`, name }))
    .filter((m) => m.key <= now && m.key >= first);
  const current = months.length ? months[months.length - 1].key : '';
  const el = dialog('Monatsbericht', months.length ? html`
    <p class="muted">Für den Landesvorstand: Bankkonto, Einnahmen und Ausgaben des Monats, Haushalt bis zum Stichtag, offene Posten,
      Kreisverbände und Aufgaben. Stichtag ist das Monatsende, im laufenden Monat heute.</p>
    <label class="field">Monat<select name="month">${months.map((m) => html`<option value="${m.key}" ${m.key === current ? 'selected' : ''}>${m.name} ${store.year}</option>`)}</select></label>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button>
      <a class="btn primary" data-open target="_blank" rel="noopener" href="/api/monatsbericht.pdf?month=${current}">${icon('file', 16)} PDF öffnen</a></div>`
    : html`<p class="muted">Für ${store.year} gibt es noch keinen abgeschlossenen oder laufenden Monat.</p>
    <div class="form-actions"><button type="button" class="btn primary" data-close>Schließen</button></div>`);
  el.querySelectorAll('[data-close]').forEach((b) => (b.onclick = () => el.close()));
  const select = el.querySelector('select[name=month]'), link = el.querySelector('[data-open]');
  if (!select) return;
  select.onchange = () => link.setAttribute('href', `/api/monatsbericht.pdf?month=${select.value}`);
  link.addEventListener('click', () => setTimeout(() => el.close(), 0));
}

function secondFridayOfDecember(year) {
  const d = new Date(year, 11, 1, 12);
  while (d.getDay() !== 5) d.setDate(d.getDate() + 1);
  d.setDate(d.getDate() + 7);
  return d;
}
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** Summenzeilen des Gesamthaushalts: [Plan Vorjahr, Ist Vorjahr, Plan, Ist] */
function totals() {
  const rows = store.household.rows.filter((r) => r.type === 'total');
  return { income: rows[0]?.values || [], expense: rows[1]?.values || [] };
}

const mehrzahl = (n, eins, viele) => (n === 1 ? eins : `${n} ${viele}`);
const tag = (isoDay) => new Date(`${isoDay}T12:00:00`);
const wochentag = (isoDay) => `${tag(isoDay).toLocaleDateString('de-DE', { weekday: 'short' })} ${shortDate(isoDay)}`;

/** Kennzahl im Band; ratio (Ist : Plan) zeichnet den dünnen Anteilsbalken. */
function kz(label, value, note, ratio = null) {
  return html`<div class="kz"><span class="label">${label}</span><span class="wert">${value}</span>${ratio === null ? ''
    : html`<span class="strich" aria-hidden="true"><span style="width:${Math.min(100, Math.round(ratio * 100))}%"></span></span>`}<span class="note">${note}</span></div>`;
}

/** Hinweis zum Verfall am 31.01.: Summe der KV-Budgets, die bis dahin nicht verbraucht sind (Budgetkonto vom Server). */
function lapseText() {
  const until = `${store.year + 1}-01-31`;
  const sum = store.workflow.budgets.reduce((s, b) => s + (b.fo?.next || []).filter((n) => n.until === until).reduce((t, n) => t + n.amount, 0), 0);
  return sum ? `Nach heutigem Stand verfallen ${eur(sum)} nicht genutzte Mittel der Kreisverbände an den Landesverband (Finanzordnung § 6)`
    : 'Nicht genutzte Mittel gehen an den Landesverband (Finanzordnung § 6)';
}

const AREAS = { mv: 'Mitgliederverwaltung', bh: 'Buchhaltung', vb: 'Kreisverbände und Haushalt', vw: 'Verwaltung' };

/** Aufgaben je Bereich: {area, title, text, count (Zahl bzw. kurzer Text), kind ('warn' | 'danger' = offen bzw. dringend, 'ok' | ''), go, then,
 * am (Tag eines festen Termins – steht dann unter „Termine“ statt bei den Aufgaben)}. */
function tasks() {
  const d = store.data, y = store.year, out = [];
  const add = (area, title, text, count, kind, go, then, am = '') => out.push({ area, title, text, count, kind, go, then, am });
  const today = todayIso();
  const active = d.members.filter((m) => !m.left_on || m.left_on > today);

  // ---- Mitgliederverwaltung
  const c = d.cases;
  if (c.active) add('mv', 'Klärfälle Beitragszahlungen', `Zahlungen von Personen, die nicht (mehr) in der Mitgliederliste stehen${c.unassigned ? ` – ${c.unassigned} ohne Zuordnung` : ''}${c.rueckfrage ? `, ${c.rueckfrage} in Rückfrage` : ''}`, c.active, 'warn', ['cases']);
  const noMail = active.filter((m) => !m.email).length, noZip = active.filter((m) => !anschriftVollstaendig(m)).length;
  if (noZip) add('mv', 'Anschrift unvollständig', 'Straße, PLZ und Ort beim Mitglied ergänzen', noZip, 'warn', ['members']);
  if (noMail) add('mv', 'Ohne E-Mail-Adresse', 'Rechnungen gehen an sie nur per Post', noMail, '', ['members']);

  // ---- Buchhaltung
  const open = yearBank().filter((b) => b.state === 'open').sort((a, b) => a.booked.localeCompare(b.booked));
  const parked = store.workflow.parked.filter((b) => (b.ledger || 'bank') === 'bank');   // Kasse: eigene Zeile
  if (open.length) add('bh', 'Kontoauszug: offene Umsätze', `noch nicht verbucht – ältester vom ${date(open[0].booked)}`, open.length, 'warn', ['bank', { bankFilter: 'open' }]);
  if (parked.length) add('bh', 'Klärungsfälle im Parkkonto', parked[0].reason || 'Klärungsgrund fehlt', parked.length, '', ['bank', { bankFilter: 'parked' }]);
  const duesOther = d.dues_open_all - d.dues_open;   // z. B. Vorjahr: im Januar vor dem Verfall am 31.01. wichtig
  if (d.dues_open_all) add('bh', 'Beitragszahlungen ohne Mitglied', `zuordnen oder als Klärfall melden${duesOther ? ` (${d.dues_open} aus ${y}, ${duesOther} aus anderen Jahren)` : ''} – erst dann stimmen Beitragskonten und der Anteil der Kreisverbände`,
    d.dues_open_all, 'warn', ['dues']);
  const cashInfo = d.cash;
  if (cashInfo?.open) add('bh', 'Kassenbelege verbuchen', `Kassenbestand ${eur(cashInfo.balance)} – offene Belege wie Kontoumsätze kontieren`, cashInfo.open, 'warn', ['cash', { cashFilter: 'open' }]);
  const cashParked = store.workflow.parked.filter((b) => b.ledger === 'cash');
  if (cashParked.length) add('bh', 'Kassenbelege im Parkkonto', cashParked[0].reason || 'Klärungsgrund fehlt', cashParked.length, '', ['cash', { cashFilter: 'parked' }]);
  if (cashInfo?.transit) add('bh', 'Geldtransit 2380 offen', 'Abhebung oder Einzahlung fehlt auf einer Seite – Kassenbeleg bzw. Kontoumsatz auf 2380 buchen', eur(cashInfo.transit), '', ['cash', { cashFilter: 'book' }]);
  const payables = openBills();
  const payDue = payables.filter((b) => !b.transferred && !b.approval.missing.length && daysOverdue(b.due) >= -7);
  if (payDue.length) add('bh', 'Eingangsrechnungen bezahlen', `${eur(payDue.reduce((s, b) => s + b.open, 0))} fällig in den nächsten 7 Tagen oder überfällig – Zahlungsvorschlag`, payDue.length, 'warn', ['bills']);
  const waiting = payables.filter((b) => b.approval.missing.length);
  if (waiting.length) add('bh', 'Freigaben einholen', `${waiting.map((b) => b.number).join(', ')} – über ${eur(Number(d.settings.approval_limit))}: Landesvorsitz und Landesschatzmeister (§ 7 Abs. 4)`, waiting.length, 'warn', ['bills']);
  for (const r of d.reviews_due) add('bh', `${r.recheck ? 'Nachprüfung' : 'Quartalsprüfung'} ${r.label}`,
    `${r.count} ${r.recheck ? 'nach der Prüfung erfasste ' : ''}Eingangsrechnung${r.count === 1 ? '' : 'en'} prüfen und protokollieren (Finanzordnung § 7 Abs. 3) – Frist ${shortDate(r.deadline)}`,
    r.overdue ? 'überfällig' : 'fällig', r.overdue ? 'danger' : 'warn', ['bills'], () => safe(() => reviewDialog(r.year, r.quarter)));
  if (d.sepa_ready) add('bh', 'Lastschriftdatei erstellen', 'Beiträge per Lastschrift – Datei erstellen und im Online-Banking einreichen', d.sepa_ready, 'warn', ['invoices'], () => safe(sepaDialog));
  if (d.sepa_check) add('bh', 'Lastschriftdatei prüfen', 'Eine noch nicht eingereichte Datei enthält Lastschriften, die nicht mehr eingezogen werden dürfen', d.sepa_check, 'danger', ['invoices'], () => safe(sepaDialog));
  if (d.reminders_ready) add('bh', 'Zahlungserinnerungen', 'überfällige Rechnungen – vorher Kontoauszug importieren und zuordnen', d.reminders_ready, 'warn', ['openitems', { openSide: 'receivables' }], () => safe(() => reminderDialog()));
  if (d.dues_book.arrears_count) add('bh', 'Offene Mitgliedsbeiträge', `${eur(d.dues_book.arrears)} Rückstand – OP-Listen für die Kreisverbände`, d.dues_book.arrears_count, '', ['openitems', { openSide: 'dues' }]);
  if (d.unallocated_dues.length) add('bh', 'Beitragszahlungen mit abweichendem Jahr', 'zählen nicht in den Anteil der Kreisverbände – unter Kreisverbände klären', d.unallocated_dues.length, '', ['budgets', { budgetMode: 'kv' }]);
  const friday = secondFridayOfDecember(y);
  add('bh', `Beitragsrechnungen ${y + 1} versenden`, 'Rechnungsausgang bis zum zweiten Freitag im Dezember (Satzung § 49)', '', '', ['invoices'], null, iso(friday));

  // ---- Kreisverbände und Haushalt
  const rpjOpen = d.rpj?.open || 0;
  if (rpjOpen) add('vb', 'RPJ-Buchungen ohne Zuordnung', 'je Buchungszeile Projekt oder Mittelabruf wählen – erst danach stimmen die RPJ-Übersichten', rpjOpen, 'warn', ['rpj', { rpjAssign: true }]);
  for (const year of d.kv_closing_due) add('vb', `KV-Budgetjahr ${year} abschließen`, 'verfallene Beitragsanteile festhalten (Finanzordnung § 6) – erst möglich, wenn alle Beitragszahlungen des Jahres zugeordnet sind', 'fällig', 'warn', ['budgets', { budgetMode: 'donations' }]);
  if (d.cash_overdue) add('vb', 'Barspenden einzahlen', 'länger als 30 Tage nicht aufs Konto eingezahlt (Finanzordnung § 2 Abs. 5)', d.cash_overdue, 'danger', ['budgets', { budgetMode: 'donations' }]);
  if (store.household.missing_plan) add('vb', `Planwerte ${y} unvollständig`, 'im Gesamthaushalt direkt in der Planspalte ergänzen', store.household.missing_plan, '', ['budgets', { budgetMode: 'household' }]);
  add('vb', `KV-Budgets ${y} verfallen`, lapseText(), '', '', ['budgets', { budgetMode: 'kv' }], null, `${y + 1}-01-31`);

  // ---- Verwaltung
  const backup = d.last_backup;
  add('vw', 'Letzte Sicherung', backup ? `${backup === today ? 'heute' : `am ${date(backup)}`}, automatisch, 30 Tage aufbewahrt` : 'noch keine automatische Sicherung gefunden',
    backup && backup >= isoDaysAgo(2) ? 'in Ordnung' : 'prüfen', backup && backup >= isoDaysAgo(2) ? 'ok' : 'warn', ['settings', { settingsTab: 'sicherung' }]);
  if (!d.address_data) add('vw', 'Amtliche Adressdaten laden', 'für die Karte der Mitglieder und die Adressprüfung – einmal laden, etwa eine Minute', 'fehlt', 'warn', ['map']);
  const auto = d.settings.kv_share_auto === 'yes' && d.settings.kv_share_auto_from;
  add('vw', 'Beitragsanteil der Kreisverbände automatisch', auto ? `aus den Mitgliedsbeiträgen ab Haushaltsjahr ${d.settings.kv_share_auto_from}` : 'Schalter in den Einstellungen – derzeit gilt der festgestellte Betrag',
    auto ? 'an' : 'aus', auto ? 'ok' : '', ['settings', { settingsTab: 'schalter' }]);
  return out;
}

const isoDaysAgo = (n) => { const t = new Date(); t.setDate(t.getDate() - n); return iso(t); };

export function overview(main) {
  const today = todayIso();
  const all = tasks().map((x, i) => ({ ...x, i }));
  const dues = store.data.invoices.filter((i) => i.status === 'issued' && i.total > i.paid);
  const dueSum = dues.reduce((s, i) => s + i.total - i.paid, 0);
  const t = totals();
  const incomeIst = t.income[3], incomePlan = t.income[2];
  const expenseIst = t.expense[3] === null || t.expense[3] === undefined ? null : -t.expense[3];
  const expensePlan = t.expense[2] === null || t.expense[2] === undefined ? null : -t.expense[2];
  const payables = openBills();
  const payableSum = payables.reduce((s, b) => s + b.open, 0);
  const plan = (ist, soll) => (soll ? `${Math.round((ist || 0) / soll * 100)} % von ${eur(soll)} Plan` : 'Plan unvollständig');

  // mit Rollen (ab 0.34) sieht jede Person nur die Bereiche, für die sie ein Recht hat
  const SHOWN = { mv: can('mitglieder'), bh: can('buchhaltung'), vb: can('verband'), vw: can('einstellungen') || can('sicherung') || can('verwaltung') };
  const mine = all.filter((x) => SHOWN[x.area]);
  const offen = mine.filter((x) => x.kind === 'warn' || x.kind === 'danger').length, dringend = mine.filter((x) => x.kind === 'danger').length;
  const termine = mine.filter((x) => x.am).sort((a, b) => a.am.localeCompare(b.am)), naechster = termine.find((x) => x.am >= today);
  const arbeit = ['mv', 'bh', 'vb'].filter((id) => SHOWN[id]), rolle = Object.values(SHOWN).some(Boolean);   // Reihenfolge wie im Menü

  const lage = !rolle ? 'Für dich ist noch keine Rolle eingetragen.'
    : !offen ? 'Nichts ist offen.'
    : `${mehrzahl(offen, 'Eine Aufgabe ist', 'Aufgaben sind')} offen${!dringend ? '' : dringend === offen ? (offen === 1 ? ' und dringend' : ', alle sind dringend')
      : `, ${dringend === 1 ? 'eine davon ist' : `${dringend} davon sind`} dringend`}.`;
  const lageText = !rolle ? 'Rollen vergibt die Verwaltung (Benutzer und Rollen).'
    : offen ? 'Der Punkt zeigt, was zu tun ist. Jede Zeile öffnet die Liste dazu.' : 'Die Zeilen ohne Punkt sind zur Übersicht da. Jede öffnet die Liste dazu.';

  const zeile = (x) => html`<button type="button" class="task-row" data-task="${x.i}">
    <span><strong>${x.title}</strong><span class="meta">${x.text}</span></span>
    <span class="task-count ${x.kind} ${typeof x.count === 'number' ? 'n' : ''}">${x.kind === 'warn' || x.kind === 'danger'
      ? html`<span class="punkt" role="img" aria-label="${x.kind === 'danger' ? 'dringend' : 'offen'}"></span>` : ''}<span class="zahl">${x.count}</span></span>${icon('right', 14)}</button>`;
  const bereich = (id, kopf) => {
    const rows = mine.filter((x) => x.area === id && !x.am);
    return html`<div class="task-bereich" data-bereich="${id}">${kopf}
      ${rows.length ? html`<div class="zeilen">${rows.map(zeile)}</div>` : html`<p class="muted">Nichts offen.</p>`}</div>`;
  };
  const seite = termine.length || SHOWN.vw;   // rechte Spalte: Termine und der Zustand der Verwaltung

  mount(main, html`<div class="page wide task-page">
    ${pageHead(`Haushaltsjahr ${store.year}, Stand ${date(today)}`, 'Start', can('buchhaltung') ? html`
      <button type="button" class="btn" data-monthly>${icon('file', 16)} Monatsbericht</button>
      <button type="button" class="btn" data-audit-package>Kassenprüfer-Paket ${store.year}</button>` : '')}
    <div class="blatt">
      <section class="blatt-teil task-lage" aria-label="Lage">
        <h2 class="blatt-aussage gross">${lage}</h2><p class="blatt-text">${lageText}</p>
      </section>

      ${can('buchhaltung') ? html`<section class="kz-band" aria-label="Haushalt ${store.year}">
        ${kz(`Einnahmen ${store.year}`, incomeIst === null || incomeIst === undefined ? '—' : eur(incomeIst), plan(incomeIst, incomePlan), incomePlan ? (incomeIst || 0) / incomePlan : null)}
        ${kz(`Ausgaben ${store.year}`, expenseIst === null ? '—' : eur(expenseIst), plan(expenseIst, expensePlan), expensePlan ? (expenseIst || 0) / expensePlan : null)}
        ${kz('Offene Forderungen', eur(dueSum), dues.length === 1 ? '1 Rechnung offen' : `${dues.length} Rechnungen offen`)}
        ${kz('Offene Verbindlichkeiten', eur(payableSum), payables.length === 1 ? '1 Eingangsrechnung offen' : `${payables.length} Eingangsrechnungen offen`)}
      </section>` : ''}

      ${rolle ? html`<div class="blatt-reihe">
        ${arbeit.length ? html`<section class="blatt-teil" aria-labelledby="tasks-title">
          <div class="blatt-kopf"><h2 id="tasks-title">Aufgaben</h2></div>
          ${arbeit.map((id) => bereich(id, html`<h3>${AREAS[id]}</h3>`))}
        </section>` : ''}
        ${seite ? html`<div class="blatt-stapel ${arbeit.length ? '' : 'allein'}">
          ${termine.length ? html`<section class="blatt-teil" aria-labelledby="task-termine">
            <div class="blatt-kopf"><h2 id="task-termine">Termine</h2></div>
            <ol class="ck-wochen task-termine">${termine.map((x) => html`<li class="${x === naechster ? 'ist-naechster' : ''}"><button type="button" class="task-row" data-task="${x.i}">
              <span class="zahl ck-tag">${wochentag(x.am)}</span><span class="ck-schiene" aria-hidden="true"><i></i><b></b></span>
              <span><strong>${x.title}</strong><span class="meta">${x.text}</span></span></button></li>`)}</ol>
          </section>` : ''}
          ${SHOWN.vw ? html`<section class="blatt-teil" aria-labelledby="task-vw">${bereich('vw', html`<div class="blatt-kopf"><h2 id="task-vw">${AREAS.vw}</h2></div>`)}</section>` : ''}
        </div>` : ''}
      </div>` : ''}
    </div>
  </div>`);

  bindTasks(main, all);
  if (!can('buchhaltung')) return;
  main.querySelector('[data-monthly]').onclick = monthlyDialog;
  main.querySelector('[data-audit-package]').onclick = (e) => safe(async () => {
    e.target.disabled = true;
    try {
      const saved = await download(`/api/pruefpaket.zip?year=${store.year}`, `Kassenpruefung-${store.year}.zip`);
      toast(saved ? `Kassenprüfer-Paket ${store.year} heruntergeladen – enthält personenbezogene Daten, nur an die Rechnungsprüfer weitergeben.`
        : `Kassenprüfer-Paket ${store.year} wird erstellt – gleich erscheint „Sichern unter“. Enthält personenbezogene Daten, nur an die Rechnungsprüfer weitergeben.`);
    } finally { e.target.disabled = false; }
  });
}

function bindTasks(main, all) {
  main.querySelectorAll('[data-task]').forEach((b) => (b.onclick = () => {
    const x = all[Number(b.dataset.task)];
    go(...x.go);
    x.then?.();
  }));
}
