// Kreisverbände und Schüler Union (0.22; Regel wie Masterdatei seit 08.11.2026, Wunsch Lennert Gust).
// Je Jahr: Plan (Zeile im Gesamthaushalt), Freundes- und Förderkreis (eigener Punkt), Beitragsanteil (festgestellt oder 25 % der
// gezahlten Beiträge), Übertrag aus dem Vorjahr (Rest bis 31.01., dann Verfall; Minus wird vorgetragen). Budgetkonto und
// kompaktes PDF für den Kreisvorsitz vom Server (kvbudget.py, berichte.kv_pdf); Abgleich mit der Masterdatei (kvabgleich.py).
import { api, fileToBase64 } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date, toInput, todayIso, parseCents } from '../format.js';
import { store, refresh, render, kvName } from '../store.js';
import { dialog, table, empty, badge, safe, toast, icon } from '../ui.js';
import { postenField, bindPosten, postenCodes, setPosten } from '../account-picker.js';
import { sortName } from '../member-picker.js';

const option = (value, label, selected) => html`<option value="${value}" ${selected ? 'selected' : ''}>${label}</option>`;
const area = (code) => (code === 'LV' || code === '5800' ? kvName(code) : 'KV ' + kvName(code));
const money = (v) => (v === null || v === undefined ? html`<span class="missing">—</span>` : eur(v));
const startYear = () => Number(String(store.data.settings.fo_start || '2026').slice(0, 4));
const currentYear = () => Number(todayIso().slice(0, 4));
const endLabel = () => (store.year === currentYear() ? 'Stand heute' : store.year < currentYear() ? `Stand 31.12.${store.year}` : 'Stand');
const KINDS = { anfang: 'Anfangsbestand', uebertrag: 'Übertrag', ffk: 'Freundes- und Förderkreis', beitrag: 'Beitragsanteil', spende: 'Spende',
  gutschrift: 'Gutschrift', ausgabe: 'Ausgabe', umbuchung: 'Umbuchung', verfall: 'Verfall' };

/** Plan minus Ausgaben (netto, null ohne Plan). */
export const planRest = (b) => (b.plan === null || b.plan === undefined ? null : b.plan - (b.plan_spent ?? b.spent));
export const needsAttention = (b) => b.plan === null || (planRest(b) ?? 0) < 0 || (b.fo_active && b.end < 0);

/** Kreisverbandsseite öffnen (Haushalt & Budgets → Kreisverbände). */
export function openKv(code) {
  store.budgets.mode = 'kv';
  store.budgets.kvSelected = code;
  store.page = 'budgets';
  render();
  window.scrollTo(0, 0);
}

function reloadKv() {
  store.budgets.kvKey = null;
}

/** Werte eines Jahres für alle Bereiche: Plan, Freundes- und Förderkreis, Beitragsanteil (Schüler Union: Budget), im ersten Jahr Anfangsbestand. */
export function valuesDialog() {
  const rows = store.workflow.budgets.filter((b) => b.code !== 'LV');
  const year = store.year, first = startYear();
  if (year < first) {
    dialog(`Werte ${year}`, html`<p>Die KV-Budgets beginnen ${first} (Einstellungen → „KV-Finanzrahmen ab Haushaltsjahr“). Für ${year} gibt es nur Planwerte im Gesamthaushalt.</p>
      <div class="form-actions"><button type="button" class="btn primary" data-close>Schließen</button></div>`).querySelector('[data-close]').onclick = closeTopDialog;
    return;
  }
  const opening = year === first;
  const el = dialog(`Werte ${year} festlegen`, html`<form id="kv-values-form" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Plan = Zeile im Gesamthaushalt. Budget ${year} = Freundes- und Förderkreis + Beitragsanteil (ab 01.01.). Beitragsanteil leer =
      25 % der tatsächlich gezahlten Beiträge der Mitglieder (Wert in grau).${opening ? ` Anfangsbestand = Stand 31.12.${year - 1} (z. B. „SB-Wert“ der Masterdatei), auch negativ.` : ''}</p>
    <p class="small muted swipe-hint">Tabelle seitlich wischen.</p>
    ${table(['Bereich', { label: 'Plan', className: 'num' }, { label: 'Freundes- und Förderkreis', className: 'num' }, { label: 'Beitragsanteil / Budget', className: 'num' },
      ...(opening ? [{ label: `Anfangsbestand 01.01.${year}`, className: 'num' }] : [])], rows.map((b) => html`<tr>
      <td>${area(b.code)}</td>
      <td class="num"><input class="amount-input" name="plan-${b.code}" inputmode="decimal" value="${toInput(b.plan)}" placeholder="—" aria-label="Plan ${b.name}"></td>
      <td class="num">${b.code === '5800' ? html`<span class="muted">—</span>` : html`<input class="amount-input" name="ffk-${b.code}" inputmode="decimal" value="${toInput(b.set.ffk)}" placeholder="0,00" aria-label="Freundes- und Förderkreis ${b.name}">`}</td>
      <td class="num">${b.share_auto ? html`<span class="small muted" title="Schalter „Beitragsanteil automatisch“ (Verwaltung → Einstellungen)">automatisch</span>` : ''}<input class="amount-input" name="dues-${b.code}" inputmode="decimal" ${b.share_auto ? 'disabled' : ''} value="${toInput(b.set.dues_share)}" placeholder="${b.code === '5800' ? '0,00' : toInput(b.share_computed)}" aria-label="Beitragsanteil ${b.name}"></td>
      ${opening ? html`<td class="num"><input class="amount-input" name="open-${b.code}" inputmode="decimal" value="${toInput(b.set.opening)}" placeholder="0,00" aria-label="Anfangsbestand ${b.name}"></td>` : ''}
      </tr>`), { className: 'kv-values' })}
    <p class="small muted">Leer = nicht festgelegt. Änderungen stehen im Änderungsprotokoll.</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Werte speichern</button></div></form>`, { wide: true });
  el.querySelector('form [data-close]').onclick = () => el.close();
  el.querySelector('#kv-values-form').onsubmit = (e) => {
    e.preventDefault();
    const submit = e.target.querySelector('[type=submit]');
    if (submit.disabled) return;
    submit.disabled = true;
    const f = e.target.elements;
    const read = (name) => { const input = f.namedItem(name); return input ? input.value.trim() : null; };
    safe(async () => {
      try {
        let changed = 0;
        for (const b of rows) {
          for (const [name, label] of [[`plan-${b.code}`, 'Plan'], [`ffk-${b.code}`, 'Freundes- und Förderkreis'], [`dues-${b.code}`, 'Beitragsanteil'], [`open-${b.code}`, 'Anfangsbestand']]) {
            const v = read(name);
            if (v && parseCents(v) === null) throw new Error(`${b.name}, ${label}: Betrag prüfen.`);
          }
          const plan = read(`plan-${b.code}`);
          if ((plan === '' ? null : parseCents(plan)) !== (b.plan ?? null)) {
            await api('/api/kv-plan', { year, kv: b.code, amount: plan });
            changed += 1;
          }
          const values = {};
          const same = (v, now) => (v === '' ? null : parseCents(v)) === (now ?? null);
          for (const [key, name] of [['ffk', `ffk-${b.code}`], ['dues_share', `dues-${b.code}`], ['opening', `open-${b.code}`]]) {
            const v = read(name);
            if (v !== null && !same(v, b.set[key])) values[key] = v;
          }
          if (Object.keys(values).length) {
            await api('/api/kv-values', { year, kv: b.code, ...values });
            changed += 1;
          }
        }
        el.close();
        reloadKv();
        await refresh();
        toast(changed ? 'Werte gespeichert.' : 'Keine Änderung.');
      } finally { submit.disabled = false; }
    });
  };
}

function closeTopDialog(e) {
  e.target.closest('dialog')?.close();
}

/** Übersicht: alle Kreisverbände und die Schüler Union in einer Tabelle. */
export function kvTable(list) {
  const sum = (key) => list.reduce((s, b) => s + (b[key] || 0), 0);
  const plans = list.filter((b) => b.plan !== null);
  return table(['Bereich', { label: `Plan ${store.year}`, className: 'num' }, { label: 'Übertrag', className: 'num' }, { label: 'FFK', className: 'num' },
    { label: 'Beitragsanteil', className: 'num' }, { label: 'Zugänge', className: 'num' }, { label: 'Ausgaben', className: 'num' },
    { label: endLabel(), className: 'num' }, ''], [
    ...list.map((b) => html`<tr class="clickable" data-kv-open="${b.code}" tabindex="0" aria-label="${b.name}: Seite öffnen">
        <td>${b.name}<span class="sub">${b.members} ${b.members === 1 ? 'Mitglied' : 'Mitglieder'}</span></td>
        <td class="num">${money(b.plan)}</td>
        <td class="num ${b.carry < 0 ? 'over' : ''}">${eur(b.carry)}${b.old_lapsed ? html`<span class="sub">verfallen ${eur(b.old_lapsed)}</span>` : ''}</td>
        <td class="num">${b.code === '5800' ? html`<span class="muted">—</span>` : b.set.ffk === null ? html`<span class="missing">—</span>` : eur(b.ffk)}</td>
        <td class="num">${eur(b.share)}${b.share_fixed || b.code === '5800' ? '' : html`<span class="sub">${b.share_auto ? 'automatisch: ' : ''}25 % von ${eur(b.paid)}</span>`}</td>
        <td class="num">${eur(b.gains)}</td>
        <td class="num">${eur(b.spent)}</td>
        <td class="num ${b.end < 0 ? 'over' : ''}" style="font-weight:600">${b.fo_active ? eur(b.end) : html`<span class="muted">—</span>`}</td>
        <td>${needsAttention(b) ? badge(b.plan === null ? 'Plan fehlt' : 'prüfen', 'warn') : ''}</td></tr>`),
    html`<tr class="row-total"><td><strong>Summe</strong></td><td class="num">${plans.length ? eur(plans.reduce((s, b) => s + b.plan, 0)) : '—'}</td>
      <td class="num">${eur(sum('carry'))}</td><td class="num">${eur(sum('ffk'))}</td><td class="num">${eur(sum('share'))}</td><td class="num">${eur(sum('gains'))}</td>
      <td class="num">${eur(sum('spent'))}</td><td class="num">${eur(sum('end'))}</td><td></td></tr>`,
  ], { className: 'kv-table' });
}

export function bindKv(container) {
  container.querySelectorAll('[data-kv-open]').forEach((row) => {
    row.onclick = () => openKv(row.dataset.kvOpen);
    row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openKv(row.dataset.kvOpen); } };
  });
  container.querySelector('[data-kv-values]')?.addEventListener('click', valuesDialog);
  container.querySelector('[data-kv-master]')?.addEventListener('click', masterDialog);
}

function nextLapse(d) {
  const n = (d.next || [])[0];
  return n ? `${eur(n.amount)} verfällt am ${date(n.until)}` : d.lapse ? 'nichts verfällt' : 'Rest wird vorgetragen';
}

/** Seite eines Bereichs: Kennzahlen, Budgetkonto mit Stand, Beitragseingänge. */
export function kvPage(code) {
  const url = `/api/kv-detail?kv=${encodeURIComponent(code)}&year=${store.year}`;
  const b = store.budgets;
  if (b.kvKey !== url) {
    b.kvKey = url;
    b.kvData = null;
    api(url).then((d) => { b.kvData = d; }, (e) => { b.kvData = { error: e.message || String(e) }; })
      .finally(() => { if (store.page === 'budgets' && b.kvKey === url) render(); });
  }
  const d = b.kvData;
  if (!d) return html`<p class="muted">Seite wird geladen …</p>`;
  if (d.error) return html`<p class="notice warn">Konnte nicht geladen werden: ${d.error}</p>`;
  const s = d.statement || { active: false, lines: [] };
  const su = code === '5800';
  const rest = planRest(d);
  return html`
    <div class="konto-head"><div><button type="button" class="link back-link" data-kv-back>← Alle Kreisverbände</button>
      <h2 style="margin-top:6px">${area(code)} · ${store.year}</h2>
      <p class="muted">${d.members} ${d.members === 1 ? 'Mitglied' : 'Mitglieder'} · ${d.paying_members} mit Beitragszahlung ${store.year}</p></div>
      <div class="actions konto-actions" style="display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" class="btn" data-kv-values>Werte ${store.year}</button>
        <button type="button" class="btn" data-kv-transfer>Budget umbuchen</button>
        <a class="btn" href="/api/op-liste.pdf?kv=${code}" target="_blank" rel="noopener" title="Offene Beiträge je Mitglied (Stand heute)">${icon('file', 16)} OP-Liste</a>
        ${s.active ? html`<a class="btn primary" href="/api/kv.pdf?kv=${code}&year=${store.year}" target="_blank" rel="noopener">${icon('file', 16)} PDF für den Kreisvorsitz</a>` : ''}</div></div>
    ${s.active ? html`<section class="cards" aria-label="Kennzahlen">
      <div class="card"><span class="label">${store.year === d.start ? `Anfangsbestand 01.01.` : `Übertrag aus ${store.year - 1}`}</span>
        <span class="value ${s.carry < 0 ? 'over' : ''}">${eur(s.carry)}</span>
        <span class="note">${s.lapsed ? `verfallen am 31.01.: ${eur(s.lapsed)}` : s.open_old > 0 ? `verfügbar bis 31.01.${store.year}` : s.carry < 0 ? 'Minus aus dem Vorjahr' : su ? 'wird vorgetragen' : '—'}</span></div>
      ${su ? '' : html`<div class="card"><span class="label">Freundes- und Förderkreis</span><span class="value">${d.set.ffk === null ? html`<span class="missing">—</span>` : eur(s.ffk)}</span>
        <span class="note">${d.set.ffk === null ? 'noch nicht festgelegt' : `Jahresbetrag ${store.year}`}</span></div>`}
      <div class="card"><span class="label">${su ? 'Budget' : 'Beitragsanteil'} ${store.year}</span><span class="value">${eur(s.dues_share)}</span>
        <span class="note">${s.dues_fixed ? 'festgelegt' : su ? 'nicht festgelegt' : `25 % von ${eur(d.paid)} gezahlten Beiträgen`}</span></div>
      <div class="card"><span class="label">Zugänge</span><span class="value">${eur(s.gains)}</span><span class="note">Spenden, Gutschriften, Umbuchungen</span></div>
      <div class="card"><span class="label">Ausgaben</span><span class="value">${eur(s.spent)}</span>
        <span class="note ${rest !== null && rest < 0 ? 'over' : ''}">${rest === null ? 'kein Plan' : `Plan ${eur(d.plan)}${rest < 0 ? ' · überschritten' : ''}`}</span></div>
      <div class="card"><span class="label">${endLabel()}</span><span class="value ${s.end < 0 ? 'over' : ''}">${eur(s.end)}</span>
        <span class="note">${store.year === currentYear() ? nextLapse(d) : s.rest_lapsed ? `verfallen am 31.01.${store.year + 1}: ${eur(s.rest_lapsed)}` : s.end < 0 ? 'Minus wird vorgetragen' : ''}</span></div>
    </section>
    <section class="panel"><div class="panel-head"><h2>Budgetkonto ${store.year}</h2><span class="small muted">Bezeichnung anklicken zum Ändern (erscheint so im PDF)</span></div>
      <p class="small muted swipe-hint">Tabelle seitlich wischen.</p>
      ${table(['Datum', 'Bezeichnung', 'Empfänger', { label: 'Betrag', className: 'num' }, { label: 'Stand', className: 'num' }], s.lines.map((x) => html`<tr>
        <td class="nowrap">${date(x.day)}</td>
        <td>${x.journal && x.kind !== 'umbuchung' ? html`<button type="button" class="link kv-label" data-kv-label="${x.journal}">${x.label || KINDS[x.kind]}</button>` : x.label}
          <span class="sub">${KINDS[x.kind] || ''}${x.journal ? ` · Buchung #${x.journal}` : ''}</span></td>
        <td>${x.recipient || ''}</td><td class="num ${x.amount < 0 ? 'over' : ''}">${eur(x.amount)}</td><td class="num">${eur(x.balance)}</td></tr>`), { className: 'kv-statement' })}
      </section>`
    : store.year > currentYear() ? html`<p class="notice">Das Budget ${store.year} beginnt am 01.01.${store.year}. Freundes- und Förderkreis und Beitragsanteil lassen sich schon festlegen („Werte ${store.year}“).</p>`
    : html`<p class="notice">Die KV-Budgets beginnen ${d.start || startYear()} (Einstellungen → „KV-Finanzrahmen ab Haushaltsjahr“).</p>`}
    ${su ? '' : html`<section class="panel"><div class="panel-head"><h2>Beitragseingänge ${store.year}</h2><span class="small muted">Grundlage des 25-%-Anteils, wenn kein Betrag festgelegt ist</span></div>
      ${d.payments.length ? table(['Datum', 'Mitglied', 'Weg', { label: 'Betrag', className: 'num' }], d.payments.map((x) => html`<tr>
        <td class="nowrap">${date(x.day)}</td><td><strong style="font-weight:500">${x.last_name ? sortName(x) : x.name || '—'}</strong><span class="sub">${x.external ? `nicht in der Mitgliederliste${x.number ? ` · ${x.number}` : ''}` : x.number || ''}</span></td>
        <td class="small">${x.invoice ? `Beitragsrechnung ${x.invoice_number}` : `Zahlung auf 8100 (Buchung #${x.journal_id})`}</td><td class="num">${eur(x.amount)}</td></tr>`))
        : empty('Noch keine Beitragseingänge', 'Zahlungen auf 8100 unter „Buchhaltung → Beitragseingänge“ einem Mitglied zuordnen.')}</section>`}`;
}

function labelDialog(line) {
  const el = dialog('Bezeichnung im KV-Budget', html`<form id="kv-label-form" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Kurze Bezeichnung und Empfänger für Budgetkonto und PDF des Kreisvorsitzes. Die Buchung selbst bleibt unverändert (#${line.journal}, ${date(line.day)}, ${eur(line.amount)}).</p>
    <label class="field">Bezeichnung<input name="label" maxlength="80" value="${line.auto ? '' : line.label}" placeholder="${line.label}"></label>
    <label class="field">Empfänger<input name="recipient" maxlength="60" value="${line.auto ? '' : line.recipient || ''}" placeholder="${line.recipient || ''}"></label>
    <p class="small muted">Beide Felder leer = Text aus der Buchung.</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  el.querySelector('[data-close]').onclick = () => el.close();
  el.querySelector('#kv-label-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/kv-label', { journal: line.journal, label: e.target.label.value, recipient: e.target.recipient.value });
      el.close();
      reloadKv();
      render();
      toast('Bezeichnung gespeichert.');
    });
  };
}

export function bindKvPage(container, code) {
  container.querySelector('[data-kv-back]')?.addEventListener('click', () => { store.budgets.kvSelected = null; render(); });
  container.querySelector('[data-kv-transfer]')?.addEventListener('click', () => transferDialog({ to: code }));
  container.querySelector('[data-kv-values]')?.addEventListener('click', valuesDialog);
  const lines = store.budgets.kvData?.statement?.lines || [];
  container.querySelectorAll('[data-kv-label]').forEach((b) => (b.onclick = () => {
    const line = lines.find((x) => String(x.journal) === b.dataset.kvLabel);
    if (line) labelDialog(line);
  }));
}

// ---------------------------------------------------------------- Abgleich mit der Masterdatei

/** Masterdatei (.xlsx) einlesen, Unterschiede zeigen, Jahreswerte und Bezeichnungen übernehmen (keine Buchungen). */
export function masterDialog() {
  const el = dialog('Abgleich mit der Masterdatei', html`
    <div class="filebox"><label class="field">Masterdatei (.xlsx)
      <input id="kv-master-file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"></label>
      <p class="small muted">Gelesen werden die Blätter „KV … 5100“ bis „Schüler Union 5800“: Beitrag FFA, Budget MB, EB-/SB-Wert, Bewegungen und Verfall.
        Die Bewegungen werden mit dem Budgetkonto verglichen und die Masterdatei mit derselben Regel nachgerechnet. Übernommen werden nur
        Jahreswerte und kurze Bezeichnungen – keine Buchungen. Vorher wird automatisch gesichert.</p></div>
    <div id="kv-master-preview" class="stack"></div>`, { wide: true });
  el.querySelector('#kv-master-file').onchange = (e) => safe(async () => {
    const file = e.target.files[0];
    if (!file) return;
    const p = await api('/api/preview-kv-master', { filename: file.name, content: await fileToBase64(file) });
    const has = (x) => x !== null && x !== undefined;
    const values = (a) => a.values.map((v) => html`<span style="display:block">${v.year}: ${[has(v.ffk) && a.code !== '5800' ? `FFK ${eur(v.ffk)}` : '',
      has(v.dues_share) ? `${a.code === '5800' ? 'Budget' : 'Beitrag'} ${eur(v.dues_share)}` : '', has(v.opening) ? `Anfang ${eur(v.opening)}` : ''].filter(Boolean).join(' · ')}</span>`);
    const rowsTable = (rows) => table(['Datum', 'Bezeichnung', 'Empfänger', { label: 'Betrag', className: 'num' }], rows.map((r) => html`<tr>
      <td class="nowrap">${date(r.day)}</td><td>${r.label || '—'}${r.row ? html`<span class="sub">Zeile ${r.row}</span>` : r.journal ? html`<span class="sub">Buchung #${r.journal}</span>` : ''}</td>
      <td>${r.recipient || ''}</td><td class="num">${eur(r.amount)}</td></tr>`));
    const todo = p.areas.reduce((s, a) => s + a.values_changed + a.labels, 0);   // nicht eindeutige Treffer zählen nicht (a.unsure)
    mount(el.querySelector('#kv-master-preview'), html`<h3>Vorschau · ${p.filename} · KV-Budgets ab ${p.first}</h3>
      ${p.notes.map((n) => html`<p class="notice warn">${n}</p>`)}
      <p class="small muted swipe-hint">Tabelle seitlich wischen.</p>
      ${table(['Bereich', 'Jahreswerte (Masterdatei)', { label: 'Gefunden', className: 'num' }, { label: 'Nur Masterdatei', className: 'num' }, { label: 'Nur Programm', className: 'num' },
        { label: 'Stand Programm', className: 'num' }, { label: 'Stand Masterdatei', className: 'num' }, { label: 'Differenz', className: 'num' }],
        p.areas.map((a) => html`<tr><td><strong>${area(a.code)}</strong><span class="sub">${a.sheet}</span></td><td class="small">${values(a)}</td>
          <td class="num">${a.matched}</td><td class="num ${a.master_only.length ? 'over' : ''}">${a.master_only.length}</td><td class="num ${a.program_only.length ? 'over' : ''}">${a.program_only.length}</td>
          <td class="num">${eur(a.program_balance)}</td><td class="num">${eur(a.master_balance)}</td>
          <td class="num ${a.program_balance !== a.master_balance ? 'over' : ''}">${eur(a.program_balance - a.master_balance)}</td></tr>`), { className: 'kv-table' })}
      <p class="small muted">Stand Programm = mit den Jahreswerten aus der Masterdatei. Stand Masterdatei = Bewegungen der Masterdatei, nach derselben Regel nachgerechnet.
        Eine Differenz erklärt sich aus den Zeilen „nur Masterdatei“ und „nur Programm“.</p>
      ${p.areas.filter((a) => a.master_only.length || a.program_only.length || a.notes.length).map((a) => html`<details class="panel"><summary><strong>${area(a.code)}</strong> ·
          ${a.master_only.length} nur Masterdatei · ${a.program_only.length} nur Programm${a.notes.length ? ` · ${a.notes.length} Hinweis${a.notes.length === 1 ? '' : 'e'}` : ''}</summary>
        ${a.notes.map((n) => html`<p class="notice warn">${n}</p>`)}
        ${a.lapses.length ? table(['Verfall', { label: 'Programm', className: 'num' }, { label: 'Masterdatei nachgerechnet', className: 'num' }, { label: 'Masterdatei steht', className: 'num' }],
          a.lapses.map((l) => html`<tr><td>31.01.${l.year}</td><td class="num">${eur(l.program)}</td><td class="num">${l.master === null ? '—' : eur(l.master)}</td><td class="num">${l.stated === null ? '—' : eur(l.stated)}</td></tr>`)) : ''}
        ${a.master_only.length ? html`<h4>Nur in der Masterdatei</h4>${rowsTable(a.master_only)}` : ''}
        ${a.program_only.length ? html`<h4>Nur im Programm</h4>${rowsTable(a.program_only)}` : ''}</details>`)}
      <div class="form-actions"><button type="button" class="btn primary" id="confirm-kv-master" ${todo ? '' : 'disabled'}>${todo ? 'Jahreswerte und Bezeichnungen übernehmen' : 'Nichts zu übernehmen'}</button></div>`);
    el.querySelector('#confirm-kv-master').onclick = (ev) => safe(async () => {
      ev.target.disabled = true;
      try {
        const r = await api('/api/import-kv-master', { token: p.token });
        el.close();
        reloadKv();
        await refresh();
        toast(`Übernommen: ${r.counts.jahreswerte} Jahreswerte, ${r.counts.bezeichnungen} Bezeichnungen. Sicherung: ${r.backup}`);
      } finally { ev.target.disabled = false; }
    });
  });
}

// ---------------------------------------------------------------- Budget-Umbuchung zwischen Bereichen

/** Ein Bereich beteiligt sich an Kosten eines anderen (z. B. gemeinsame Feier): Umbuchung auf demselben Haushaltsposten. */
export function transferDialog({ to = '', from = '' } = {}) {
  const areas = store.data.kv;
  const bills = store.data.bills.filter((x) => x.status === 'open');
  const el = dialog('Budget umbuchen', html`<form id="transfer-form" novalidate style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Ein Bereich beteiligt sich an Kosten eines anderen – z. B. an der Rechnung für eine gemeinsame Feier. Das Budget des
      beteiligten Bereichs wird belastet, der andere entlastet. Der Gesamthaushalt bleibt gleich.</p>
    <div class="formgrid">
      <label class="field">Wer beteiligt sich?<select name="to">${option('', 'Bereich wählen', !to)}${areas.map((k) => option(k.code, k.name, k.code === to))}</select></label>
      <label class="field">An den Kosten von<select name="from">${option('', 'Bereich wählen', !from)}${areas.map((k) => option(k.code, k.name, k.code === from))}</select></label>
      <label class="field">Betrag in Euro<input name="amount" inputmode="decimal" required></label>
      <label class="field">Datum<input type="date" name="day" value="${todayIso()}" max="${todayIso()}" ${store.data.data_start ? html`min="${store.data.data_start}"` : ''} required></label>
      <label class="field span">Eingangsrechnung (optional)<select name="bill_id">${option('', 'ohne Bezug', true)}${bills.map((x) => option(String(x.id), `${x.number} · ${x.creditor} · ${eur(x.total)} · ${kvName(x.kv)}`, false))}</select></label>
      <div class="field span">Haushaltsposten der Kosten${postenField({ kind: 'expense', account: 'KV', codes: postenCodes('expense') })}</div>
      <label class="field span">Grund<input name="reason" placeholder="z. B. Beteiligung gemeinsames Sommerfest" required></label>
    </div>
    <p class="small muted" id="transfer-effect" aria-live="polite"></p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Umbuchen</button></div>
  </form>`, { wide: true });
  const form = el.querySelector('#transfer-form');
  const posten = form.querySelector('[data-posten]');
  form.querySelector('[data-close]').onclick = () => el.close();
  const effect = () => {
    const a = form.to.value, b = form.from.value, cents = parseCents(form.amount.value) || 0;
    const fo = store.workflow.budgets.find((x) => x.code === a)?.fo;   // Finanzrahmen des belasteten Kreisverbands
    const short = fo && cents > fo.available ? ` Achtung: ${area(a)} hat derzeit nur ${eur(Math.max(0, fo.available))} verfügbar – das Budget würde negativ (Minus wird vorgetragen).` : '';
    form.querySelector('#transfer-effect').textContent = a && b && a !== b
      ? `${area(a)} wird belastet, ${area(b)} entlastet.${short}` : a && a === b ? 'Bitte zwei verschiedene Bereiche wählen.' : '';
  };
  bindPosten(form, { kv: () => form.to.value || 'LV' });
  form.to.onchange = () => { posten.refresh(); effect(); };
  form.from.onchange = effect;
  form.amount.oninput = effect;
  form.bill_id.onchange = () => {
    const bill = bills.find((x) => String(x.id) === form.bill_id.value);
    if (!bill) return;
    form.from.value = bill.kv;
    if (bill.issued <= todayIso() && (!store.data.data_start || bill.issued >= store.data.data_start)) form.day.value = bill.issued;   // Haushaltsjahr der Rechnung
    const line = bill.lines[0];
    if (line) setPosten(posten, line.account, line.title);
    effect();
  };
  effect();
  const submit = form.querySelector('button[type=submit]');
  form.onsubmit = (e) => {
    e.preventDefault();
    if (submit.disabled) return;   // kein Doppelklick → keine doppelte Umbuchung
    submit.disabled = true;
    safe(async () => {
      const data = Object.fromEntries(['to', 'from', 'amount', 'day', 'bill_id', 'reason'].map((k) => [k, form[k].value]));
      data.account = posten.querySelector('[data-account]').value;
      const select = posten.querySelector('[data-title]');
      if (select.dataset.chosen === '1') data.title = select.value;
      try {
        await api('/api/kv-transfer', data);
      } finally { submit.disabled = false; }
      el.close();
      await refresh();
      toast('Budget umgebucht.');
    });
  };
}

/** Liste der Umbuchungen eines Haushaltsjahres mit Storno. */
export function transferList() {
  const list = store.data.kv_transfers || [];
  return html`<section class="panel"><div class="panel-head"><h2>Budget-Umbuchungen ${store.year}</h2>
      <button type="button" class="btn small primary" data-transfer-new>Budget umbuchen</button></div>
    ${list.length ? table(['Datum', 'Beteiligt sich', 'An den Kosten von', 'Text', { label: 'Betrag', className: 'num' }, ''], list.map((t) => html`<tr class="${t.reversed ? 'muted' : ''}">
        <td>${date(t.day)}</td><td>${area(t.to)}</td><td>${area(t.from)}</td><td>${t.description}</td><td class="num">${eur(t.amount)}</td>
        <td>${t.reversed ? badge('storniert') : html`<button type="button" class="link" data-transfer-reverse="${t.id}">Stornieren</button>`}</td></tr>`))
      : empty('Keine Umbuchungen', 'Beteiligt sich ein Kreisverband an Kosten eines anderen (z. B. gemeinsame Feier), hier umbuchen.')}</section>`;
}

export function bindTransfers(container) {
  container.querySelector('[data-transfer-new]')?.addEventListener('click', () => transferDialog());
  container.querySelectorAll('[data-transfer-reverse]').forEach((b) => (b.onclick = () => {
    const el = dialog('Umbuchung stornieren', html`<form id="transfer-reverse" style="display:flex;flex-direction:column;gap:14px">
      <p class="muted">Bucht die Umbuchung zurück. Beide Buchungen bleiben im Journal sichtbar und zählen nicht mehr im Budget.</p>
      <label class="field">Grund<input name="reason" required></label>
      <div class="form-actions"><button type="submit" class="btn warn">Stornieren</button></div></form>`);
    el.querySelector('#transfer-reverse').onsubmit = (e) => {
      e.preventDefault();
      safe(async () => {
        await api('/api/kv-transfer-reverse', { id: Number(b.dataset.transferReverse), reason: e.target.reason.value });
        el.close();
        await refresh();
        toast('Umbuchung storniert.');
      });
    };
  }));
}
