// Haushalt (Richtung C): Plan/Ist mit Ausschöpfung und 10-%-Hinweis; Kreisverbände als Tabelle mit eigener Seite je KV;
// Spenden, Barspenden, KV-Budgetabschluss.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { eur, plain, date, toInput } from '../format.js';
import { store, refresh, render, kvName, go } from '../store.js';
import { pageHead, icon, table, safe, toast, dialog, overlay, badge, empty, confirmAction } from '../ui.js';
import { kvTable, bindKv, needsAttention, transferList, bindTransfers, kvPage, bindKvPage } from './kv.js';

const cell = (v) => (v === null || v === undefined ? html`<span class="missing">—</span>` : plain(v));

/** Ausschöpfung Ist/Plan, nur wenn Plan gesetzt und gleiches Vorzeichen. */
function usage(plan, ist) {
  if (plan === null || ist === null || plan === 0 || Math.sign(plan) !== Math.sign(ist || plan)) return null;
  return (ist || 0) / plan;
}

function deviates(row) {
  const [, , plan, ist] = row.values;
  if (plan === null) return ist !== null && ist !== 0;
  if (plan === 0) return (ist || 0) !== 0;
  return Math.abs((ist || 0) - plan) > Math.abs(plan) * 0.1;
}

function householdRow(r, h) {
  if (r.type === 'section') return html`<tr class="row-section"><th colspan="8" scope="rowgroup">${r.label}</th></tr>`;
  if (r.type === 'group') return html`<tr class="row-group"><td colspan="8">${r.label}</td></tr>`;
  const [planPrev, istPrev, plan, ist] = r.values;
  const expense = store.household.titles.find((t) => t.id === r.id)?.kind === 'expense' || (plan ?? ist ?? 0) < 0;
  const ratio = r.type === 'item' ? usage(plan, ist) : null;
  const delta = plan !== null && ist !== null ? ist - plan : null;
  const over = ratio !== null && expense && ratio > 1;
  const editable = (value, year) => r.type === 'item'
    ? html`<button type="button" class="plan-cell" data-plan="${r.id}" data-year="${year}" aria-label="Plan ${year} ${r.label} bearbeiten">${cell(value)}</button>` : cell(value);
  const flagged = r.type === 'item' && over && ratio > 1.1;
  return html`<tr class="row-${r.type} ${flagged ? 'row-flag' : ''}">
    <td class="code">${r.code || ''}</td><td>${r.label}</td>
    <td class="num prev">${editable(planPrev, h.previous)}</td><td class="num prev">${cell(istPrev)}</td>
    <td class="num">${editable(plan, h.year)}</td><td class="num" style="font-weight:600">${cell(ist)}</td>
    <td class="usage-col">${ratio === null ? '' : html`<div class="usage" role="img" aria-label="${Math.round(ratio * 100)} Prozent des Plans"><span class="${over ? 'warn' : expense ? 'expense' : ''}" style="width:${Math.min(118, Math.round(ratio * 100))}%"></span></div>`}</td>
    <td class="num ${over ? 'over' : 'muted'}" ${flagged ? html`style="font-weight:700"` : ''}>${delta === null ? '' : html`${plain(delta)}${ratio !== null ? ` · ${Math.round(ratio * 100)} %` : ''}`}</td>
  </tr>`;
}

function household(main) {
  const h = store.household;
  const over = h.overruns; // Regel in haushalt.overruns (Satzung § 50)
  const rows = store.budgets.onlyDeviations
    ? h.rows.filter((r) => (r.type === 'item' && deviates(r)) || ['total', 'result'].includes(r.type))
    : h.rows;
  return html`
    <div class="print-head"><div><p class="small">Junge Union Hamburg</p><h1>Haushalt ${h.year}</h1></div>
      <div class="meta">Arbeitsfassung · Stand ${date(h.date)}<br>Vergleich ${h.previous} / ${h.year} · Beträge in Euro</div></div>
    <div class="toolbar no-print">
      <div class="segmented" role="group" aria-label="Ansicht">
        <button type="button" data-dev="0" aria-pressed="${!store.budgets.onlyDeviations}">Alle Titel</button>
        <button type="button" data-dev="1" aria-pressed="${store.budgets.onlyDeviations}">Nur Abweichungen über 10 %</button>
      </div>
      <p class="small muted">Einnahmen positiv, Ausgaben negativ – wie in der Excel-Vorlage. Planwerte anklicken zum Bearbeiten.</p>
    </div>
    ${over.length ? html`<p class="notice warn"><strong>Titelüberschreitung über 10 %:</strong> ${over.map((o, i) => html`${i ? ', ' : ''}${o.code} ${o.label} (+${o.pct} %)`)}.
      Laut Satzung § 50 ist vor weiteren Verpflichtungen ein Nachtragshaushalt zu beschließen.</p>` : ''}
    ${h.missing_plan || h.unmapped_entries ? html`<p class="notice no-print">${h.missing_plan ? `${h.missing_plan} Planwerte fehlen für ${h.year}. ` : ''}${h.unmapped_entries ? `${h.unmapped_entries} Ergebnisbuchungen sind noch keinem Titel zugeordnet.` : ''}</p>` : ''}
    <p class="small muted swipe-hint">Tabelle seitlich wischen für Plan und Ist.</p>
    <section class="panel" style="padding:0">
      ${table(['Titel', 'Bezeichnung', { label: `Plan ${h.previous}`, className: 'num' }, { label: `Ist ${h.previous}`, className: 'num' },
        { label: `Plan ${h.year}`, className: 'num' }, { label: `Ist ${h.year}`, className: 'num' }, { label: 'Ausschöpfung', className: 'usage-col' }, { label: 'Ist − Plan', className: 'num' }],
        rows.map((r) => householdRow(r, h)), { className: 'hh-table' })}
    </section>
    <div class="hh-notes">${h.notes.map((n) => html`<p>${n}</p>`)}</div>`;
}

function kvs() {
  if (store.budgets.kvSelected) return kvPage(store.budgets.kvSelected);
  const all = store.workflow.budgets.filter((b) => b.code !== 'LV');
  const q = store.budgets.kvSearch.toLowerCase();
  const shown = all.filter((b) => (!q || `${b.name} ${b.code}`.toLowerCase().includes(q)) && (!store.budgets.kvAttention || needsAttention(b)));
  const unallocated = store.data.unallocated_dues;
  const missing = all.filter((b) => b.plan === null).length;
  return html`
    <div class="toolbar">
      <label class="search">${icon('search', 16)}<span class="sr-only">Kreisverband suchen</span><input id="kv-search" type="search" placeholder="Kreisverband suchen" value="${store.budgets.kvSearch}"></label>
      <label class="check"><input type="checkbox" id="kv-attention" ${store.budgets.kvAttention ? 'checked' : ''}> Nur mit Klärungsbedarf</label>
      <span style="display:flex;gap:8px;flex-wrap:wrap">${store.data.dues_open_all ? html`<button type="button" class="btn" data-dues-open>Beiträge zuordnen <span class="count">${store.data.dues_open_all}</span></button>` : ''}
        <button type="button" class="btn" data-kv-master>Abgleich mit Masterdatei</button>
        <button type="button" class="btn ${missing ? 'primary' : ''}" data-kv-values>Werte ${store.year} festlegen</button></span>
    </div>
    <p class="small muted swipe-hint">Tabelle seitlich wischen.</p>
    <section class="panel" style="padding:6px 12px">${shown.length ? kvTable(shown) : html`<p class="muted" style="padding:12px">Keine passenden Kreisverbände.</p>`}</section>
    ${transferList()}
    ${unallocated.length ? html`<section class="panel"><div class="panel-head"><h2>Jahreszuordnung klären</h2><span class="small muted">zählt nicht in den 25-%-Anteil</span></div>
      ${table(['Rechnung', 'Zahlungsdatum', { label: 'Betrag', className: 'num' }], unallocated.map((x) => html`<tr><td>${x.number}</td><td>${date(x.booked)}</td><td class="num">${eur(x.amount)}</td></tr>`))}</section>` : ''}
    <p class="notice">Plan = Zeile im Gesamthaushalt. Budget je Jahr = Freundes- und Förderkreis (FFK, eigener Punkt) + Beitragsanteil (festgelegt oder
      25 % der tatsächlich gezahlten Beiträge der Mitglieder; Schüler Union: Budget), ab 01.01. Dazu kommen Spenden, Gutschriften und Umbuchungen;
      Ausgaben mindern es. Ein Rest des Vorjahres steht bis 31.01. zur Verfügung – Ausgaben im Januar verbrauchen ihn zuerst – und verfällt dann
      (FO § 6 Abs. 3); ein Minus wird vorgetragen. Schüler Union: der Rest wird vorgetragen. Zeile anklicken für Budgetkonto und PDF des Bereichs.</p>`;
}

// ---------------------------------------------------------------- Spenden (Finanzordnung § 2) und KV-Budgetabschluss (§ 6)

async function loadDonations() {
  const year = store.year;
  store.budgets.donations = { year, loading: true };
  try {
    store.budgets.donations = { year, data: await api('/api/donations?year=' + year) };
  } catch (err) {
    store.budgets.donations = { year, error: err.message };
  }
  render();
}

function donations() {
  const cache = store.budgets.donations;
  if (!cache || cache.year !== store.year) {
    loadDonations();
    return html`<p class="muted" aria-live="polite">Spenden werden geladen …</p>`;
  }
  if (cache.loading) return html`<p class="muted" aria-live="polite">Spenden werden geladen …</p>`;
  if (cache.error) return html`<p class="notice warn" aria-live="polite">${cache.error} <button type="button" class="btn small" data-donations-retry>Erneut laden</button></p>`;
  const d = cache.data;
  const sum = d.donations.reduce((s, x) => s + x.amount, 0);
  const share = d.donations.reduce((s, x) => s + x.kv_share, 0);
  const cash = d.cash.filter((c) => c.received_on.startsWith(String(store.year)) || !c.deposited_on || !c.cdu_informed_on);
  return html`
    ${d.closing_due.length ? html`<p class="notice warn">${d.closing_due.map((y) => html`Der Rest der KV-Budgets ${y} ist am 31.01.${y + 1} verfallen, das Budgetjahr ist noch nicht abgeschlossen.
      <button type="button" class="btn small primary" data-close-year="${y}">Budgetjahr ${y} abschließen</button> `)}</p>` : ''}
    <section class="panel"><div class="panel-head"><h2>Spenden ${store.year}</h2><span class="small muted">${d.donations.length} Eingänge · ${eur(sum)} · davon für Kreisverbände ${eur(share)}</span></div>
      ${d.donations.length ? table(['Datum', 'Text', 'Konto', 'Bereich', { label: 'Betrag', className: 'num' }, { label: 'KV-Anteil', className: 'num' }, 'Verfügbar bis'],
        d.donations.map((x) => html`<tr><td>${date(x.day)}</td><td>${x.description}</td><td>${x.account} · ${x.account_name}</td><td>${kvName(x.kv)}</td>
          <td class="num">${eur(x.amount)}</td><td class="num">${x.kv_share ? eur(x.kv_share) : html`<span class="muted">—</span>`}</td><td>${x.until ? date(x.until) : ''}</td></tr>`))
        : empty('Keine Spenden gebucht', 'Spenden werden im Buchungsplatz auf 8200 (Spenden), 8202 (Freundes- und Förderkreis) oder 1371 (Kandidaten) gebucht.')}
      <p class="small muted">Spende auf 8200 mit Bereich = Kreisverband bzw. Schüler Union geht ganz in dessen Budget und verfällt mit dem Jahresrest am 31.01. des Folgejahres
        (Schüler Union: wird vorgetragen). Der Freundes- und Förderkreis zählt je Kreisverband über den Jahresbetrag („Werte festlegen“), nicht über einzelne Buchungen auf 8202.</p></section>
    <section class="panel"><div class="panel-head"><h2>Barspenden</h2><button type="button" class="btn small primary" data-cash>Barspende erfassen</button></div>
      ${cash.length ? table(['Datum', 'Spender', { label: 'Betrag', className: 'num' }, 'Entgegengenommen', 'Quittung', 'Stand', ''], cash.map((c) => html`<tr>
          <td>${date(c.received_on)}</td><td>${c.donor}<span class="sub">${kvName(c.kv)}</span></td><td class="num">${eur(c.amount)}</td>
          <td>${c.receiver1}<span class="sub">${c.receiver2}</span></td><td>${c.receipt}</td>
          <td>${c.issues.length ? c.issues.map((i) => html`<span class="small ${c.overdue ? 'over' : 'muted'}" style="display:block">${i}</span>`) : badge('erledigt', 'ok')}</td>
          <td><button type="button" class="link" data-cash-edit="${c.id}">Bearbeiten</button></td></tr>`))
        : empty('Keine Barspenden', 'Barspenden nehmen zwei Vertreter des geschäftsführenden Landesvorstands gegen Quittung entgegen.')}
      <p class="small muted">Finanzordnung § 2 Abs. 5: zwei Vertreter, Quittung, Einzahlung aufs Konto binnen 30 Tagen, CDU-Buchhaltung für die Spendenquittung informieren. Das Register dokumentiert; gebucht wird die Einzahlung im Buchungsplatz.</p></section>
    <section class="panel"><div class="panel-head"><h2>Budgetabschlüsse der Kreisverbände</h2><span class="small muted">Finanzordnung § 6</span></div>
      ${d.closings.length ? table(['Jahr', 'Bereich', { label: 'Zugänge', className: 'num' }, { label: 'Ausgaben', className: 'num' }, { label: 'Verfallen', className: 'num' },
        { label: 'Vorgetragen', className: 'num' }, 'Abgeschlossen'],
        d.closings.map((c) => html`<tr><td>${c.year}</td><td>${kvName(c.kv)}</td><td class="num">${eur(c.share)}</td><td class="num">${eur(c.used)}</td><td class="num">${eur(c.lapsed)}</td>
          <td class="num">${eur(c.carried)}</td><td>${date(c.closed_on)}${c.changed ? html` ${badge('seither geändert', 'warn')}` : ''}${c.old_rule ? html` ${badge('Regel bis 0.22')}` : ''}</td></tr>`))
        : empty('Noch kein Budgetjahr abgeschlossen', 'Nach dem 31.01. hält der Abschluss je Bereich fest, was vom Vorjahr verfallen ist und was als Minus vorgetragen wird.')}</section>`;
}

function cashForm(item) {
  const c = item || { received_on: '', donor: '', amount: 0, kv: 'LV', receiver1: '', receiver2: '', receipt: '', deposited_on: '', cdu_informed_on: '', note: '' };
  const o = overlay(item ? 'Barspende bearbeiten' : 'Barspende erfassen', html`<form id="cash-form" style="display:flex;flex-direction:column;gap:14px">
    <div class="formgrid">
      <label class="field">Erhalten am<input type="date" name="received_on" value="${c.received_on}" required></label>
      <label class="field">Betrag in Euro<input name="amount" inputmode="decimal" value="${item ? toInput(c.amount) : ''}" required></label>
      <label class="field">Spender<input name="donor" value="${c.donor}" required></label>
      <label class="field">Bereich<select name="kv">${store.data.kv.map((k) => html`<option value="${k.code}" ${k.code === c.kv ? 'selected' : ''}>${k.name}</option>`)}</select></label>
      <label class="field">Entgegengenommen von<input name="receiver1" value="${c.receiver1}" required></label>
      <label class="field">und von<input name="receiver2" value="${c.receiver2}" required></label>
      <label class="field">Quittungsnummer<input name="receipt" value="${c.receipt}" required></label>
      <label class="field">Eingezahlt am<input type="date" name="deposited_on" value="${c.deposited_on || ''}"></label>
      <label class="field">CDU-Buchhaltung informiert am<input type="date" name="cdu_informed_on" value="${c.cdu_informed_on || ''}"></label>
      <label class="field span">Bemerkung<input name="note" value="${c.note || ''}"></label>
    </div>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`, { wide: true });
  o.querySelector('#cash-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/cash-donation', { ...Object.fromEntries(new FormData(e.target)), id: item?.id || null });
      o.close();
      await refresh();
      toast('Barspende gespeichert.');
    });
  };
}

function planDialog(id, year) {
  const h = store.household;
  const row = h.rows.find((r) => r.id === id);
  const value = row.values[year === h.previous ? 0 : 2];
  const el = dialog(`Plan ${year} · ${row.label}`, html`<form id="plan-form" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Titel ${row.code || 'ohne Nummer'} · Arbeitsplan, kein Haushaltsbeschluss.</p>
    <label class="field">Planbetrag in Euro<input name="amount" inputmode="decimal" value="${toInput(value)}" placeholder="leer = nicht erfasst"></label>
    <p class="small muted">Ausgaben negativ eingeben. Leer entfernt den Planwert; 0,00 ist ein ausdrücklich geplanter Nullbetrag.</p>
    <div class="form-actions"><button type="submit" class="btn primary">Speichern</button></div></form>`);
  el.querySelector('input').select();
  el.querySelector('#plan-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/haushalt-plan', { year, title: id, amount: e.target.amount.value });
      el.close();
      await refresh();
      toast('Planwert gespeichert.');
    });
  };
}

function referenceDialog() {
  safe(async () => {
    const year = store.year;
    const ref = await api('/api/haushalt-reference');
    const plan = ref.plans[String(year)];
    const warnings = (ref.warnings || []).filter((w) => w.startsWith(String(year)));
    const el = dialog(`Excel-Plan ${year} übernehmen`, html`
      <p>Planwerte aus <strong>${ref.source}</strong>, Blatt „${ref.sheet}“, Spalte ${ref.columns[String(year)]}. Nur fehlende Planwerte werden ergänzt, vorhandene bleiben. Keine Buchungen.</p>
      ${warnings.length ? html`<p class="notice warn">${warnings.map((w, i) => html`${i ? html`<br>` : ''}${w}`)}</p>` : ''}
      ${table(['Titel', 'Bezeichnung', { label: `Plan ${year}`, className: 'num' }], store.household.rows.filter((r) => r.type === 'item').map((r) => html`<tr>
        <td>${r.code}</td><td>${r.label}</td><td class="num">${cell(plan[r.id] ?? null)}${r.values[2] !== null ? html` <span class="small muted">(bereits erfasst)</span>` : ''}</td></tr>`))}
      <div class="form-actions"><button type="button" class="btn primary" id="confirm-reference">Fehlende Planwerte übernehmen</button></div>`, { wide: true });
    el.querySelector('#confirm-reference').onclick = () => safe(async () => {
      const result = await api('/api/haushalt-import', { year });
      el.close();
      await refresh();
      toast(`${result.count} Planwerte aus Excel ergänzt.`);
    });
  });
}

export function budgets(main) {
  const mode = store.budgets.mode;
  const h = store.household;
  mount(main, html`<div class="page wide">
    ${pageHead(`Haushaltsjahr ${store.year} · Arbeitsfassung`, mode === 'kv' ? 'Kreisverbände & Schüler Union' : mode === 'donations' ? 'Spenden und KV-Budgets' : 'Haushalt Plan und Ist', mode !== 'household' ? '' : html`
      ${h.reference_available ? html`<button type="button" class="btn" data-reference>Excel-Plan übernehmen</button>` : ''}
      <button type="button" class="btn" data-print>${icon('print', 16)} Drucken</button>
      <a class="btn" href="/api/haushalt.pdf?year=${store.year}" target="_blank" rel="noopener">PDF</a>`)}
    <div class="tabs" role="tablist" aria-label="Bereich">
      <button type="button" role="tab" data-mode="household" aria-selected="${mode === 'household'}">Gesamthaushalt</button>
      <button type="button" role="tab" data-mode="kv" aria-selected="${mode === 'kv'}">Kreisverbände</button>
      <button type="button" role="tab" data-mode="donations" aria-selected="${mode === 'donations'}">Spenden</button>
    </div>
    ${mode === 'kv' ? kvs() : mode === 'donations' ? donations() : household(main)}
  </div>`);

  main.querySelectorAll('[data-mode]').forEach((b) => (b.onclick = () => { store.budgets.mode = b.dataset.mode; store.budgets.kvSelected = null; render(); }));
  main.querySelectorAll('[data-dev]').forEach((b) => (b.onclick = () => { store.budgets.onlyDeviations = b.dataset.dev === '1'; render(); }));
  main.querySelectorAll('[data-plan]').forEach((b) => (b.onclick = () => planDialog(b.dataset.plan, Number(b.dataset.year))));
  main.querySelector('[data-reference]')?.addEventListener('click', referenceDialog);
  main.querySelector('[data-print]')?.addEventListener('click', () => window.print());
  main.querySelector('[data-cash]')?.addEventListener('click', () => cashForm());
  main.querySelector('[data-donations-retry]')?.addEventListener('click', () => { store.budgets.donations = null; render(); });
  main.querySelectorAll('[data-cash-edit]').forEach((b) => (b.onclick = () => cashForm(store.budgets.donations.data.cash.find((c) => c.id === Number(b.dataset.cashEdit)))));
  main.querySelectorAll('[data-close-year]').forEach((b) => (b.onclick = () => confirmAction(`KV-Budgetjahr ${b.dataset.closeYear} abschließen`,
    `Hält je Bereich fest, welche Zugänge und Ausgaben ${b.dataset.closeYear} hatte, was am 31.01. an den Landesverband verfallen ist und was als Minus vorgetragen wird (Finanzordnung § 6 Abs. 3). Spätere Buchungen ändern den Abschluss nicht, sie werden markiert.`,
    async () => { await api('/api/kv-close', { year: Number(b.dataset.closeYear) }); store.budgets.donations = null; },
    { label: 'Abschließen', done: 'Budgetjahr abgeschlossen.' })));
  if (mode === 'kv' && store.budgets.kvSelected) bindKvPage(main, store.budgets.kvSelected);
  main.querySelector('[data-dues-open]')?.addEventListener('click', () => go('dues'));
  const search = main.querySelector('#kv-search');
  if (search) {
    search.oninput = () => {
      store.budgets.kvSearch = search.value;
      const pos = search.selectionStart;
      render();
      const again = document.getElementById('kv-search');
      again.focus();
      again.setSelectionRange(pos, pos);
    };
    main.querySelector('#kv-attention').onchange = (e) => { store.budgets.kvAttention = e.target.checked; render(); };
    bindKv(main);
    bindTransfers(main);
  }
}
