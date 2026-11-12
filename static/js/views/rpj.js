// RPJ-Mittel (ab 0.26, Wunsch Lennert Gust 08.11.2026): Schattenhaushalt der Zuwendung des Rings Politischer Jugend –
// Bescheid (Kontingent), Mittelabrufe je Quartal, Projekte mit Plan je Kostenart a–k, Ist aus den zugeordneten Buchungen,
// Einzelübersicht je Projekt (PDF/CSV), Import des RPJ-Haushalts, „RPJ zuordnen“ für ältere Buchungen. Verbindlich: rpj.py.
import { api, fileToBase64, download } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date, toInput, parseCents, todayIso } from '../format.js';
import { store, refresh } from '../store.js';
import { pageHead, icon, empty, dialog, overlay, safe, toast, table, badge } from '../ui.js';
import { RPJ_CATEGORIES, targetOptions, categoryOptions } from '../rpj-field.js';

const STATUS = { planned: 'geplant', done: 'durchgeführt', cancelled: 'abgesagt' };
const STATUS_KIND = { planned: '', done: 'ok', cancelled: 'warn' };
const quarter = (q) => (q ? `Q${q}` : 'ganzjährig');
const money = (cents) => (cents ? eur(cents) : html`<span class="muted">—</span>`);
const diff = (ist, plan, income = false) => {   // Kosten über Plan rot; Mehreinnahmen (Teilnahmebeiträge) nicht
  const d = ist - plan;
  return d ? html`<span class="${d > 0 && !income ? 'over' : ''}">${d > 0 ? '+' : '−'}${eur(Math.abs(d))}</span>` : html`<span class="muted">±0</span>`;
};

// ---------------------------------------------------------------- Bescheid und Antrag

function yearDialog(v) {
  const i = v.info || {};
  const el = dialog(`RPJ ${v.year}: Antrag und Bescheid`, html`<form id="rpj-year" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Der Bescheid der Stadt legt das Kontingent für das Jahr fest; die Zuwendung wird danach quartalsweise abgerufen (Mittelabruf).
      Abrufe sind nur bis zum Kontingent möglich.</p>
    <div class="formgrid">
      <label class="field">Kontingent laut Bescheid (€)<input name="contingent" inputmode="decimal" autocomplete="off" value="${toInput(i.contingent)}" placeholder="leer, solange kein Bescheid vorliegt"></label>
      <label class="field">Bescheid vom<input name="notice_on" type="date" value="${i.notice_on || ''}"></label>
      <label class="field">Aktenzeichen<input name="reference" maxlength="80" autocomplete="off" value="${i.reference || ''}"></label>
      <label class="field">Antrag vom<input name="applied_on" type="date" value="${i.applied_on || ''}"></label>
      <label class="field span">Notiz<input name="note" maxlength="500" autocomplete="off" value="${i.note || ''}"></label>
    </div>
    <p class="small muted">Beantragt laut RPJ-Haushalt: ${eur(v.cards.applied)} (Summe „Mittel RPJ“ der Projekte).</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  el.querySelector('form [data-close]').onclick = () => el.close();
  el.querySelector('#rpj-year').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/rpj-jahr', { year: v.year, ...Object.fromEntries(new FormData(e.target)) });
      el.close();
      await refresh();
      toast('Antrag und Bescheid gespeichert.');
    });
  };
}

// ---------------------------------------------------------------- Projekt anlegen / ändern

function projectForm(v, p = null) {
  const next = Math.max(0, ...v.projects.map((x) => x.no)) + 1;
  const x = p || { year: v.year, no: next, kind: '', name: '', quarter: null, participants: '', status: 'planned', plan: {} };
  const el = dialog(p ? `RPJ-Projekt Nr. ${p.no} bearbeiten` : `RPJ-Projekt ${v.year} anlegen`, html`<form id="rpj-project" style="display:flex;flex-direction:column;gap:16px">
    <div class="formgrid">
      <label class="field">Jahr<input name="year" inputmode="numeric" required value="${x.year}"></label>
      <label class="field">Lfd. Nr. (wie im Antrag)<input name="no" inputmode="numeric" required value="${x.no}"></label>
      <label class="field">Maßnahme<input name="kind" maxlength="60" autocomplete="off" value="${x.kind || ''}" placeholder="z. B. Podiumsdiskussion, Bildungsreise"></label>
      <label class="field">Quartal<select name="quarter">${['', 1, 2, 3, 4].map((q) => html`<option value="${q}" ${(x.quarter || '') === q ? 'selected' : ''}>${q ? `Q${q}` : 'ganzjährig / offen'}</option>`)}</select></label>
      <label class="field span">Name der Veranstaltung<input name="name" required maxlength="200" autocomplete="off" value="${x.name}"></label>
      <label class="field">Teilnehmer (geplant)<input name="participants" maxlength="120" autocomplete="off" value="${x.participants || ''}" placeholder="z. B. 30–35"></label>
      <label class="field">Ansprechpartner<input name="contact" maxlength="120" autocomplete="off" value="${x.contact || ''}"></label>
      <label class="field">Status<select name="status">${Object.entries(STATUS).map(([k, l]) => html`<option value="${k}" ${x.status === k ? 'selected' : ''}>${l}</option>`)}</select></label>
      <label class="field">Durchgeführt am<input name="held_on" type="date" value="${x.held_on || ''}"></label>
      <label class="field">Teilnehmer (tatsächlich)<input name="attended" inputmode="numeric" autocomplete="off" value="${x.attended ?? ''}"></label>
      <label class="field span">Notiz<input name="note" maxlength="500" autocomplete="off" value="${x.note || ''}"></label>
    </div>
    <h3>Plan je Kostenart (wie im RPJ-Antrag)</h3>
    <div class="rpj-plan">${RPJ_CATEGORIES.map(([c, l]) => html`<label class="field">${c}) ${l}${c === 'i' ? ' – Einnahme' : ''}
      <input data-plan="${c}" inputmode="decimal" autocomplete="off" value="${x.plan[c] ? toInput(x.plan[c]) : ''}" placeholder="0,00"></label>`)}</div>
    <p class="rpj-sum" data-sum></p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`, { wide: true });
  const form = el.querySelector('#rpj-project');
  const sum = () => {
    const plan = Object.fromEntries([...form.querySelectorAll('[data-plan]')].map((i) => [i.dataset.plan, parseCents(i.value || '0') || 0]));
    const cost = Object.entries(plan).filter(([c]) => c !== 'i').reduce((s, [, a]) => s + a, 0);
    form.querySelector('[data-sum]').textContent = `Kosten (a–h, j, k) ${eur(cost)} − Teilnahmebeiträge (i) ${eur(plan.i)} = Mittel RPJ ${eur(cost - plan.i)}`;
  };
  form.querySelectorAll('[data-plan]').forEach((i) => (i.oninput = sum));
  sum();
  form.querySelector('[data-close]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const data = Object.fromEntries(new FormData(form));
      data.plan = Object.fromEntries([...form.querySelectorAll('[data-plan]')].map((i) => [i.dataset.plan, i.value]));
      if (p) data.id = p.id;
      const r = await api('/api/rpj-projekt', data);
      await refresh();
      toast(p ? 'Projekt gespeichert.' : 'Projekt angelegt.');
      projectDialog(r.id);
    });
  };
}

// ---------------------------------------------------------------- Einzelübersicht

export function projectDialog(id) {
  const el = dialog('RPJ-Projekt', html`<div data-rpj-project class="rpj-detail"><p class="small muted">Wird geladen …</p></div>`, { wide: true });
  safe(async () => {
    const d = await api('/api/rpj-projekt?id=' + id);
    const p = d.project;
    el.querySelector('#modal-title').textContent = `RPJ ${p.year} · Nr. ${p.no} ${p.name}`;
    const rows = d.categories.filter((c) => c.plan || c.ist);
    mount(el.querySelector('[data-rpj-project]'), html`
      <p class="muted">${[p.kind, `${quarter(p.quarter)} ${p.year}`, p.held_on ? `durchgeführt am ${date(p.held_on)}` : '', p.participants ? `Teilnehmer geplant ${p.participants}` : '',
        p.attended !== null ? `tatsächlich ${p.attended}` : '', p.contact ? `Ansprechpartner ${p.contact}` : ''].filter(Boolean).join(' · ')} ${badge(STATUS[p.status], STATUS_KIND[p.status])}</p>
      <div class="toolbar">
        <span style="display:flex;gap:8px;flex-wrap:wrap">
          <button type="button" class="btn" data-edit>${icon('edit', 16)} Bearbeiten</button>
          <a class="btn" href="/api/rpj-projekt.pdf?id=${p.id}" target="_blank" rel="noopener">${icon('file', 16)} Einzelübersicht PDF</a>
          <button type="button" class="btn" data-csv>${icon('download', 16)} Buchungen CSV</button>
          ${p.bookings ? '' : html`<button type="button" class="btn ghost" data-delete>${icon('trash', 16)} Löschen</button>`}
        </span></div>
      <section class="cards" aria-label="Projekt">
        <div class="card"><span class="label">Kosten (a–h, j, k)</span><span class="value">${eur(p.cost_ist)}</span><span class="note">Plan ${eur(p.cost_plan)}</span></div>
        <div class="card"><span class="label">Teilnahmebeiträge (i)</span><span class="value">${eur(p.fee_ist)}</span><span class="note">Plan ${eur(p.fee_plan)}</span></div>
        <div class="card"><span class="label">Mittel RPJ</span><span class="value ${p.rpj_ist > p.rpj_plan ? 'over' : ''}">${eur(p.rpj_ist)}</span><span class="note">Plan ${eur(p.rpj_plan)} · ${diff(p.rpj_ist, p.rpj_plan)}</span></div>
      </section>
      <h3>Plan und Ist je Kostenart</h3>
      ${rows.length ? table(['Kostenart', { label: 'Plan', className: 'num' }, { label: 'Ist', className: 'num' }, { label: 'Abweichung', className: 'num' }],
        rows.map((c) => html`<tr><td>${c.code}) ${c.label}${c.income ? html`<span class="sub">Einnahme</span>` : ''}</td><td class="num">${money(c.plan)}</td>
          <td class="num">${money(c.ist)}</td><td class="num">${diff(c.ist, c.plan, c.income)}</td></tr>`)) : empty('Kein Plan', 'Beim Bearbeiten den Plan je Kostenart eintragen.')}
      <h3>Buchungen</h3>
      ${d.bookings.length ? table(['Datum', 'Herkunft', 'Text', 'Kostenart', { label: 'Ausgabe', className: 'num' }, { label: 'Einnahme', className: 'num' }, ''],
        d.bookings.map((b, n) => html`<tr><td class="nowrap">${date(b.day)}${b.fy !== p.year ? html`<span class="sub">Haushaltsjahr ${b.fy}</span>` : ''}</td>
          <td class="nowrap">${b.origin}${b.ref ? html`<span class="sub">${b.ref}</span>` : ''}</td>
          <td>${b.text}${b.name ? html`<span class="sub">${b.name}</span>` : ''}${b.split ? html`<span class="sub">Teilbetrag einer aufgeteilten Buchung</span>` : ''}</td>
          <td>${b.category}) ${b.label}</td><td class="num">${b.cost ? eur(b.cost) : ''}</td><td class="num">${b.income ? eur(b.income) : ''}</td>
          <td><button type="button" class="btn small ghost" data-reassign="${n}">Ändern</button></td></tr>`))
        : empty('Noch keine Buchungen', 'Beim Verbuchen auf 4380 (bzw. Teilnahmebeiträgen H19) dieses Projekt wählen – ältere Buchungen über „RPJ zuordnen“.')}`);
    const box = el.querySelector('[data-rpj-project]');
    box.querySelector('[data-edit]').onclick = () => safe(async () => projectForm(await api('/api/rpj?year=' + p.year), p));
    box.querySelector('[data-csv]').onclick = () => safe(() => download(`/api/rpj-buchungen.csv?project=${p.id}`, `RPJ-${p.year}-Projekt-${String(p.no).padStart(2, '0')}-Buchungen.csv`));
    box.querySelector('[data-delete]')?.addEventListener('click', () => safe(async () => {
      await api('/api/rpj-projekt-loeschen', { id: p.id });
      el.close();
      await refresh();
      toast('Projekt gelöscht.');
    }));
    box.querySelectorAll('[data-reassign]').forEach((b) => (b.onclick = () => safe(async () => {
      const entry = (await api('/api/rpj-offen?all=1')).find((x) => x.id === d.bookings[Number(b.dataset.reassign)].entry_id);
      if (entry) assignEditor(entry, () => projectDialog(id));
    })));
  });
}

// ---------------------------------------------------------------- Mittelabruf

function requestDialog(v, r = null) {
  const planned = v.plan_quarter.map((q) => `${quarter(q.quarter)} ${eur(q.amount)}`).join(' · ');
  const el = dialog(r ? `${r.label} bearbeiten` : `Mittelabruf ${v.year} erfassen`, html`<form id="rpj-request" style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">Kontingent ${v.cards.contingent === null ? 'noch nicht eingetragen' : eur(v.cards.contingent)} · bisher abgerufen ${eur(v.cards.requested)}
      ${v.cards.available !== null ? ` · noch abrufbar ${eur(v.cards.available + (r ? r.amount : 0))}` : ''}</p>
    <div class="formgrid">
      <label class="field">Quartal<select name="quarter">${[1, 2, 3, 4].map((q) => html`<option value="${q}" ${(r ? r.quarter : Math.floor(new Date().getMonth() / 3) + 1) === q ? 'selected' : ''}>Q${q}</option>`)}</select></label>
      <label class="field">Betrag (€)<input name="amount" inputmode="decimal" required autocomplete="off" value="${r ? toInput(r.amount) : ''}"></label>
      <label class="field">Abgerufen am<input name="requested_on" type="date" required value="${r ? r.requested_on : todayIso()}"></label>
      <label class="field">Notiz<input name="note" maxlength="300" autocomplete="off" value="${r?.note || ''}"></label>
    </div>
    ${planned ? html`<p class="small muted">Plan „Mittel RPJ“ nach Quartal der Projekte: ${planned}</p>` : ''}
    ${r?.received ? html`<p class="small muted">Eingegangen ${eur(r.received)} – der Betrag kann nicht kleiner werden.</p>` : ''}
    <div class="form-actions">${r && !r.received ? html`<button type="button" class="btn ghost" data-delete>${icon('trash', 16)} Löschen</button>` : ''}
      <button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  const form = el.querySelector('#rpj-request');
  form.querySelector('[data-close]').onclick = () => el.close();
  form.querySelector('[data-delete]')?.addEventListener('click', () => safe(async () => {
    await api('/api/rpj-abruf-loeschen', { id: r.id });
    el.close();
    await refresh();
    toast('Mittelabruf gelöscht.');
  }));
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/rpj-abruf', { year: v.year, ...(r ? { id: r.id } : {}), ...Object.fromEntries(new FormData(form)) });
      el.close();
      await refresh();
      toast('Mittelabruf gespeichert – den Zahlungseingang beim Verbuchen auf 1592 diesem Abruf zuordnen.');
    });
  };
}

// ---------------------------------------------------------------- RPJ-Haushalt importieren

function importDialog() {
  const el = dialog('RPJ-Haushalt importieren', html`<div style="display:flex;flex-direction:column;gap:14px">
    <p class="muted">CSV- oder Excel-Datei mit einer Zeile je Projekt: Jahr, Nr., Maßnahme, Veranstaltung, Quartal, Teilnehmer, Antrag vom und die Beträge
      der Kostenarten a) bis k) wie im RPJ-Antrag. Vorhandene Projekte (gleiches Jahr und gleiche Nr.) werden aktualisiert; Status, Durchführung
      und Buchungen bleiben.</p>
    <div class="toolbar"><label class="field">Datei<input type="file" id="rpj-file" accept=".csv,.txt,.xlsx"></label>
      <button type="button" class="link" data-template>Vorlage herunterladen</button></div>
    <div data-preview></div></div>`, { wide: true });
  el.querySelector('[data-template]').onclick = () => safe(() => download('/api/rpj-vorlage.csv', 'RPJ-Haushalt-Vorlage.csv'));
  el.querySelector('#rpj-file').onchange = (e) => safe(async () => {
    const file = e.target.files[0];
    if (!file) return;
    const p = await api('/api/rpj-import-vorschau', { filename: file.name, content: await fileToBase64(file) });
    const ACTION = { new: ['neu', 'ok'], update: ['ändern', 'warn'], same: ['unverändert', ''], error: ['Fehler', 'danger'] };
    mount(el.querySelector('[data-preview]'), html`
      <p><strong>${p.filename}</strong> · ${p.counts.new} neu · ${p.counts.update} geändert · ${p.counts.same} unverändert${p.counts.error ? html` · <span class="over">${p.counts.error} mit Fehler</span>` : ''}</p>
      ${p.years.map((y) => html`<p class="small muted">${y}: Mittel RPJ zusammen ${eur(p.totals[y].rpj)} · ${RPJ_CATEGORIES.filter(([c]) => p.totals[y].categories[c]).map(([c]) => `${c}) ${eur(p.totals[y].categories[c])}`).join(' · ')}</p>`)}
      ${table(['', 'Zeile', 'Jahr', 'Nr.', 'Veranstaltung', 'Quartal', { label: 'Kosten', className: 'num' }, { label: 'TB', className: 'num' }, { label: 'RPJ', className: 'num' }, ''],
        p.rows.map((r) => html`<tr><td>${['new', 'update'].includes(r.action) ? html`<input type="checkbox" data-row="${r.row_no}" checked aria-label="Zeile ${r.row_no} übernehmen">` : ''}</td>
          <td>${r.row_no}</td><td>${r.values?.year || ''}</td><td>${r.values?.no || ''}</td>
          <td>${r.values ? html`${r.values.name}<span class="sub">${r.values.kind || ''}</span>` : html`<span class="over">${r.error}</span>`}</td>
          <td>${r.values ? quarter(r.values.quarter) : ''}</td><td class="num">${r.values ? eur(r.cost_plan) : ''}</td><td class="num">${r.values ? eur(r.fee_plan) : ''}</td>
          <td class="num">${r.values ? eur(r.rpj_plan) : ''}</td><td>${badge(...ACTION[r.action])}${r.changes?.length ? html`<span class="sub">${r.changes.map((c) => ({ plan: 'Plan', name: 'Name', kind: 'Maßnahme', quarter: 'Quartal', participants: 'Teilnehmer', contact: 'Ansprechpartner' })[c]).join(', ')}</span>` : ''}</td></tr>`))}
      ${p.missing.length ? html`<p class="notice">Im Programm, aber nicht in der Datei (bleiben unverändert): ${p.missing.map((m) => `${m.year} Nr. ${m.no} ${m.name}`).join(' · ')}</p>` : ''}
      <div class="form-actions"><button type="button" class="btn ghost" data-close-preview>Abbrechen</button>
        <button type="button" class="btn primary" data-apply ${p.counts.new + p.counts.update ? '' : 'disabled'}>Ausgewählte übernehmen</button></div>`);
    el.querySelector('[data-close-preview]').onclick = () => el.close();
    el.querySelector('[data-apply]').onclick = (ev) => safe(async () => {
      if (ev.target.disabled) return;
      ev.target.disabled = true;
      try {
        const selected = [...el.querySelectorAll('[data-row]:checked')].map((x) => Number(x.dataset.row));
        const r = await api('/api/rpj-import', { token: p.token, selected });
        el.close();
        await refresh();
        toast(`RPJ-Haushalt übernommen: ${r.new} neu, ${r.update} geändert. Sicherung vorher: ${r.backup}`);
      } finally { ev.target.disabled = false; }
    });
  });
}

// ---------------------------------------------------------------- RPJ zuordnen (ältere Buchungen, Änderungen)

/** Zuordnung einer Buchungszeile bearbeiten – mehrere Teilbeträge möglich (z. B. eine Zahlung für zwei Projekte). */
function assignEditor(x, done) {
  const kind = x.kind, fy = x.fy;
  const row = (l = {}) => html`<div class="rpj-split" data-split>
    <select data-target aria-label="Projekt bzw. Mittelabruf">${targetOptions(kind, fy, l.request_id ? `r:${l.request_id}` : l.project_id ? `p:${l.project_id}` : '')}</select>
    <select data-category aria-label="Kostenart">${categoryOptions(l.category || '', kind)}</select>
    <input data-amount inputmode="decimal" aria-label="Betrag" value="${l.amount !== undefined ? toInput(l.amount) : ''}">
    <button type="button" class="btn ghost icon-btn" data-drop aria-label="Teilbetrag entfernen">${icon('trash', 16)}</button></div>`;
  const o = overlay('RPJ-Zuordnung', html`<form id="rpj-assign" style="display:flex;flex-direction:column;gap:12px">
    <p class="muted">${date(x.day)} · ${x.origin}${x.ref ? ` ${x.ref}` : ''} · ${x.text}${x.name ? ` · ${x.name}` : ''}<br>
      Konto ${x.account}${x.title ? ` (${x.title})` : ''} · Betrag ${eur(x.amount)} · Haushaltsjahr ${fy}</p>
    <p class="small muted">${kind === 'cost' ? 'Kosten: Projekt und Kostenart wählen.' : kind === 'fee' ? 'Teilnahmebeitrag: Projekt wählen (Kostenart i).'
      : 'Mittelzufluss: Mittelabruf wählen – oder ein Projekt, wenn es ein Teilnahmebeitrag ist (bzw. bei netto auf 1592 gebuchten Kosten die Kostenart).'}
      Beträge wie gebucht: Ausgaben negativ.</p>
    <div data-rows style="display:flex;flex-direction:column;gap:8px">${(x.links.length ? x.links : [{ amount: x.amount }]).map(row)}</div>
    <div class="parts-foot"><button type="button" class="link" data-add>${icon('plus', 14)} Teilbetrag</button><span data-rest role="status"></span></div>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  const rows = o.querySelector('[data-rows]');
  const sync = () => {
    const sum = [...rows.children].reduce((s, r) => s + (parseCents(r.querySelector('[data-amount]').value) || 0), 0);
    rows.querySelectorAll('[data-split]').forEach((r) => {
      const t = r.querySelector('[data-target]').value;
      r.querySelector('[data-category]').hidden = kind === 'fee' || t.startsWith('r:') || (kind === 'grant' && !t);
    });
    rows.querySelectorAll('[data-drop]').forEach((b) => (b.hidden = rows.children.length < 2));
    o.querySelector('[data-rest]').textContent = sum === x.amount ? 'Vollständig aufgeteilt' : `Noch zu verteilen: ${eur(x.amount - sum)}`;
  };
  const bind = (r) => {
    r.querySelector('[data-target]').onchange = sync;
    r.querySelector('[data-amount]').oninput = sync;
    r.querySelector('[data-drop]').onclick = () => { r.remove(); sync(); };
  };
  [...rows.children].forEach(bind);
  o.querySelector('[data-add]').onclick = () => {
    const holder = document.createElement('div');
    const used = [...rows.children].reduce((s, r) => s + (parseCents(r.querySelector('[data-amount]').value) || 0), 0);
    mount(holder, row({ amount: x.amount - used }));
    rows.append(holder.firstElementChild);
    bind(rows.lastElementChild);
    sync();
  };
  sync();
  o.querySelector('#rpj-assign').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const items = [...rows.children].map((r) => ({ target: r.querySelector('[data-target]').value, amount: r.querySelector('[data-amount]').value,
        category: r.querySelector('[data-category]').hidden ? '' : r.querySelector('[data-category]').value }));
      await api('/api/rpj-zuordnen', { entry_id: x.id, items });
      o.close();
      await refresh();
      toast('RPJ-Zuordnung gespeichert.');
      done?.();
    });
  };
}

export function assignDialog({ all = false } = {}) {
  let scope = store.data.rpj.open_year || !store.data.rpj.open ? String(store.year) : '';
  const el = dialog('RPJ zuordnen', html`<div data-rpj-open><p class="small muted">Wird geladen …</p></div>`, { wide: true });
  const box = el.querySelector('[data-rpj-open]');
  async function load() {
    const items = await api(`/api/rpj-offen?year=${scope}${all ? '&all=1' : ''}`);
    mount(box, html`<p class="muted">Buchungen auf den RPJ-Posten (4380 Mittelverwendung, 1592 Mittelzufluss, Titel H19 Teilnahmebeiträge) aus Kontoauszug,
        Kasse und Eingangsrechnungen, die noch keinem Projekt bzw. Mittelabruf zugeordnet sind – z. B. aus der Excel-Übernahme. Neue Buchungen
        werden schon beim Verbuchen zugeordnet (Pflicht).</p>
      <div class="toolbar"><label class="field inline">Haushaltsjahr<select data-scope><option value="${store.year}" ${scope ? 'selected' : ''}>${store.year}</option>
          <option value="" ${scope ? '' : 'selected'}>alle Jahre</option></select></label>
        <label class="check"><input type="checkbox" data-all ${all ? 'checked' : ''}> Auch bereits zugeordnete Buchungen zeigen (zum Ändern)</label></div>
      <div class="bulkbar" data-bulk hidden><strong data-bulk-count></strong>
        <select data-bulk-target aria-label="Projekt bzw. Mittelabruf für die ausgewählten Buchungen"></select>
        <select data-bulk-category aria-label="Kostenart für die ausgewählten Buchungen"></select>
        <button type="button" class="btn primary" data-bulk-apply>Ausgewählte zuordnen</button></div>
      ${items.length ? table(['', 'Datum', 'Herkunft', 'Text', 'Konto', { label: 'Betrag', className: 'num' }, 'Zuordnung', ''], items.map((x, i) => html`<tr>
          <td>${x.links.length ? '' : html`<input type="checkbox" data-select="${i}" aria-label="Buchung vom ${date(x.day)} auswählen">`}</td>
          <td class="nowrap">${date(x.day)}${x.fy !== Number(x.day.slice(0, 4)) ? html`<span class="sub">Haushaltsjahr ${x.fy}</span>` : ''}</td>
          <td class="nowrap">${x.origin}${x.ref ? html`<span class="sub">${x.ref}</span>` : ''}</td>
          <td>${x.text}${x.name ? html`<span class="sub">${x.name}</span>` : ''}</td>
          <td class="nowrap">${x.account}${x.title ? html`<span class="sub">${x.title}</span>` : ''}</td>
          <td class="num ${x.amount < 0 ? '' : ''}">${eur(x.amount)}</td>
          <td class="small">${x.links.length ? x.links.map((l, j) => html`${j ? html`<br>` : ''}${l.label}${x.links.length > 1 ? ` · ${eur(l.amount)}` : ''}`) : html`<span class="muted">—</span>`}
            ${x.open && x.links.length ? html`<span class="sub over">offen ${eur(x.open)}</span>` : ''}</td>
          <td><button type="button" class="btn small ${x.links.length ? '' : 'primary'}" data-pick="${i}">${x.links.length ? 'Ändern' : 'Zuordnen'}</button></td></tr>`))
        : empty(all ? 'Keine RPJ-Buchungen' : 'Alles zugeordnet', `${scope ? `Für ${scope}` : 'Insgesamt'} gibt es ${all ? 'keine Buchungen auf den RPJ-Posten' : 'keine offenen RPJ-Buchungen'}.`)}`);
    box.querySelector('[data-all]').onchange = (e) => { all = e.target.checked; safe(load); };
    box.querySelector('[data-scope]').onchange = (e) => { scope = e.target.value; safe(load); };
    box.querySelectorAll('[data-pick]').forEach((b) => (b.onclick = () => assignEditor(items[Number(b.dataset.pick)], () => safe(load))));
    // Mehrere gleichartige Buchungen (z. B. alle Teilnahmebeiträge einer Reise) mit dem ganzen Betrag demselben Ziel zuordnen
    const bulk = box.querySelector('[data-bulk]'), bulkTarget = box.querySelector('[data-bulk-target]'), bulkCategory = box.querySelector('[data-bulk-category]');
    const chosen = () => [...box.querySelectorAll('[data-select]:checked')].map((c) => items[Number(c.dataset.select)]);
    let drawn = '';
    const sync = () => {
      const list = chosen(), kinds = [...new Set(list.map((x) => x.kind))], years = [...new Set(list.map((x) => x.fy))];
      bulk.hidden = !list.length;
      if (!list.length) return;
      const same = kinds.length === 1;
      box.querySelector('[data-bulk-count]').textContent = same
        ? `${list.length} ausgewählt · ${eur(list.reduce((s, x) => s + x.amount, 0))}`
        : 'Bitte nur Buchungen derselben Art auswählen (Kosten, Teilnahmebeiträge oder Mittelzufluss)';
      bulkTarget.hidden = bulkCategory.hidden = box.querySelector('[data-bulk-apply]').hidden = !same;
      if (!same) return;
      const key = `${kinds[0]}|${years[0]}`;
      if (drawn !== key) { mount(bulkTarget, targetOptions(kinds[0], years[0], bulkTarget.value)); mount(bulkCategory, categoryOptions(bulkCategory.value, kinds[0])); drawn = key; }
      bulkCategory.hidden = kinds[0] === 'fee' || (kinds[0] === 'grant' && !bulkTarget.value.startsWith('p:'));
    };
    box.querySelectorAll('[data-select]').forEach((c) => (c.onchange = sync));
    bulkTarget.onchange = sync;
    box.querySelector('[data-bulk-apply]').onclick = (e) => safe(async () => {
      if (e.target.disabled) return;
      e.target.disabled = true;
      try {
        const r = await api('/api/rpj-zuordnen-mehrere', { entry_ids: chosen().map((x) => x.id), target: bulkTarget.value, category: bulkCategory.hidden ? '' : bulkCategory.value });
        toast(`${r.count} RPJ-${r.count === 1 ? 'Buchung' : 'Buchungen'} zugeordnet.`);
        await refresh();
        await load();
      } finally { e.target.disabled = false; }
    });
  }
  safe(load);
}

// ---------------------------------------------------------------- Seite

export function rpj(main) {
  mount(main, html`<div class="page wide"><p class="small muted">Wird geladen …</p></div>`);
  safe(async () => {
    const v = await api('/api/rpj?year=' + store.year);
    if (store.page !== 'rpj') return;
    const c = v.cards, i = v.info || {}, open = store.data.rpj.open;
    mount(main, html`<div class="page wide">
      ${pageHead(`Haushaltsjahr ${v.year} · Ring Politischer Jugend`, 'RPJ-Mittel', html`
        <button type="button" class="btn" data-assign>RPJ zuordnen${open ? html` <span class="count">${open}</span>` : ''}</button>
        <button type="button" class="btn" data-year>Antrag & Bescheid</button>
        <button type="button" class="btn" data-import>${icon('upload', 16)} Haushalt importieren</button>
        <a class="btn" href="/api/rpj.pdf?year=${v.year}" target="_blank" rel="noopener">${icon('file', 16)} PDF</a>
        <button type="button" class="btn" data-csv>${icon('download', 16)} CSV</button>
        <button type="button" class="btn" data-request>${icon('plus', 16)} Mittelabruf</button>
        <button type="button" class="btn primary" data-new>${icon('plus', 16)} Projekt anlegen</button>`)}
      <section class="cards rpj-cards" aria-label="RPJ ${v.year}">
        <div class="card"><span class="label">Kontingent laut Bescheid</span><span class="value">${c.contingent === null ? '—' : eur(c.contingent)}</span>
          <span class="note">${c.contingent === null ? html`<span class="over">Bescheid noch nicht eingetragen</span>` : [i.notice_on ? `Bescheid vom ${date(i.notice_on)}` : 'Datum des Bescheids fehlt', i.reference ? `Az. ${i.reference}` : ''].filter(Boolean).join(' · ')}</span></div>
        <div class="card"><span class="label">Beantragt (RPJ-Haushalt)</span><span class="value">${eur(c.applied)}</span>
          <span class="note">${v.projects.length} Projekte${i.applied_on ? ` · Antrag vom ${date(i.applied_on)}` : ''}</span></div>
        <div class="card"><span class="label">Abgerufen</span><span class="value">${eur(c.requested)}</span>
          <span class="note">${c.available === null ? `${v.requests.length} Mittelabrufe` : `noch abrufbar ${eur(c.available)}`}</span></div>
        <div class="card"><span class="label">Eingegangen</span><span class="value">${eur(c.received)}</span>
          <span class="note">${c.outstanding ? html`<span class="over">offen ${eur(c.outstanding)}</span>` : 'alle Abrufe eingegangen'}${c.refunded ? ` · zurückgezahlt ${eur(c.refunded)}` : ''}</span></div>
        <div class="card"><span class="label">Verwendet</span><span class="value">${eur(c.used)}</span>
          <span class="note">Kosten ${eur(c.cost)} − Teilnahmebeiträge ${eur(c.fees)}</span></div>
        <div class="card"><span class="label">Saldo</span><span class="value ${c.balance < 0 ? 'over' : ''}">${eur(c.balance)}</span>
          <span class="note">${c.balance > 0 ? 'eingegangen, noch nicht verwendet' : c.balance < 0 ? 'mehr verwendet als eingegangen (vorfinanziert)' : 'ausgeglichen'}${c.refunded ? ' · nach Rückzahlung' : ''}</span></div>
      </section>
      ${v.issues.map((x) => html`<p class="notice warn">${x}</p>`)}
      ${v.open ? html`<p class="notice warn">${v.open} RPJ-${v.open === 1 ? 'Buchung' : 'Buchungen'} aus ${v.year} ohne Projekt bzw. Mittelabruf – die Übersicht ist erst danach vollständig.
        <button type="button" class="link" data-assign>Jetzt zuordnen</button></p>` : ''}
      <section class="panel">
        <div class="panel-head"><h2>Projekte ${v.year}</h2><span class="small muted">Zeile öffnen: Einzelübersicht mit Buchungen, PDF und CSV</span></div>
        ${v.projects.length ? table([{ label: 'Nr.', className: 'num' }, 'Veranstaltung', 'Quartal', { label: 'Kosten Plan', className: 'num' }, { label: 'Kosten Ist', className: 'num' },
          { label: 'TB Ist', className: 'num' }, { label: 'RPJ Plan', className: 'num' }, { label: 'RPJ Ist', className: 'num' }, 'Status'],
          v.projects.map((p) => html`<tr class="clickable" data-project="${p.id}" tabindex="0"><td class="num">${p.no}</td>
            <td><strong style="font-weight:600">${p.name}</strong><span class="sub">${[p.kind, p.participants ? `${p.participants} Teilnehmer` : '', p.bookings ? `${p.bookings} ${p.bookings === 1 ? 'Buchung' : 'Buchungen'}` : ''].filter(Boolean).join(' · ')}</span></td>
            <td>${quarter(p.quarter)}</td><td class="num">${eur(p.cost_plan)}</td><td class="num ${p.cost_ist > p.cost_plan ? 'over' : ''}">${money(p.cost_ist)}</td>
            <td class="num">${money(p.fee_ist)}${p.fee_plan ? html`<span class="sub">Plan ${eur(p.fee_plan)}</span>` : ''}</td>
            <td class="num">${eur(p.rpj_plan)}</td><td class="num ${p.rpj_ist > p.rpj_plan ? 'over' : ''}">${money(p.rpj_ist)}</td>
            <td>${badge(STATUS[p.status], STATUS_KIND[p.status])}</td></tr>`))
          : empty('Noch keine Projekte', `„Projekt anlegen“ oder den RPJ-Haushalt ${v.year} als Datei importieren.`)}
      </section>
      <div class="rpj-grid">
        <section class="panel"><div class="panel-head"><h2>Kostenarten</h2></div>
          ${table(['Kostenart', { label: 'Plan', className: 'num' }, { label: 'Ist', className: 'num' }, { label: 'Abweichung', className: 'num' }],
            v.categories.map((k) => html`<tr><td>${k.code}) ${k.label}${k.income ? html`<span class="sub">Einnahme</span>` : ''}</td><td class="num">${money(k.plan)}</td>
              <td class="num">${money(k.ist)}</td><td class="num">${k.plan || k.ist ? diff(k.ist, k.plan, k.income) : ''}</td></tr>`))}</section>
        <section class="panel"><div class="panel-head"><h2>Mittelabrufe</h2><button type="button" class="link no-print" data-request>${icon('plus', 14)} Mittelabruf</button></div>
          ${v.requests.length ? table(['Abruf', 'am', { label: 'Betrag', className: 'num' }, { label: 'Eingegangen', className: 'num' }, { label: 'Offen', className: 'num' }],
            v.requests.map((r, n) => html`<tr class="clickable" data-edit-request="${n}" tabindex="0"><td>Q${r.quarter}/${r.year}${r.note ? html`<span class="sub">${r.note}</span>` : ''}</td>
              <td>${date(r.requested_on)}</td><td class="num">${eur(r.amount)}</td><td class="num">${money(r.received)}${r.refunded ? html`<span class="sub">zurückgezahlt ${eur(r.refunded)}</span>` : ''}</td>
              <td class="num ${r.open > 0 ? 'over' : ''}">${money(r.open)}</td></tr>`))
            : empty('Noch keine Mittelabrufe', c.contingent === null ? 'Zuerst unter „Antrag & Bescheid“ das Kontingent eintragen.' : 'Je Quartal den abgerufenen Betrag erfassen.')}
          <h3 style="margin-top:18px">Abstimmung mit dem Haushalt ${v.year}</h3>
          ${table(['RPJ-Posten', { label: `Haushalt ${v.year}`, className: 'num' }, { label: 'zugeordnet', className: 'num' }, { label: 'offen', className: 'num' }],
            v.reconciliation.map((r) => html`<tr><td>${r.label}<span class="sub">${r.count} ${r.count === 1 ? 'Buchung' : 'Buchungen'}${r.other_years ? ` · ${eur(r.other_years)} zu Projekten/Abrufen anderer Jahre` : ''}</span></td>
              <td class="num">${eur(r.household)}</td><td class="num">${eur(r.linked + r.other_years)}</td><td class="num ${r.open ? 'over' : ''}">${money(r.open)}</td></tr>`))}
          <p class="small muted">Mittelverwendung = 4380/H45, Mittelzufluss = 1592/H18, Teilnahmebeiträge = H19 – aus Kontoauszug, Kasse und Eingangsrechnungen, ohne Stornos.</p></section>
      </div>
      <p class="small muted">Verwendet = Kosten der Projekte (a–h, j, k) minus Teilnahmebeiträge (i), wie „Mittel RPJ“ im Antrag. Saldo = eingegangene Mittel minus
        verwendete Mittel. Zuordnung beim Verbuchen ist Pflicht; Projekte dürfen Buchungen des Nachbarjahres enthalten (z. B. Rechnung im Januar).</p>
    </div>`);
    main.querySelectorAll('[data-assign]').forEach((b) => (b.onclick = () => assignDialog()));
    main.querySelector('[data-year]').onclick = () => yearDialog(v);
    main.querySelector('[data-import]').onclick = importDialog;
    main.querySelector('[data-csv]').onclick = () => safe(() => download(`/api/rpj.csv?year=${v.year}`, `RPJ-${v.year}.csv`));
    main.querySelectorAll('[data-request]').forEach((b) => (b.onclick = () => requestDialog(v)));
    main.querySelector('[data-new]').onclick = () => projectForm(v);
    main.querySelectorAll('[data-project]').forEach((row) => {
      row.onclick = () => projectDialog(Number(row.dataset.project));
      row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); projectDialog(Number(row.dataset.project)); } };
    });
    main.querySelectorAll('[data-edit-request]').forEach((row) => {
      const open = () => requestDialog(v, v.requests[Number(row.dataset.editRequest)]);
      row.onclick = open;
      row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    });
    if (store.rpjAssign) { store.rpjAssign = false; assignDialog(); }
  });
}

