// Klärfälle (Mitgliederverwaltung, ab 0.31 – Wunsch Lennert Gust 10.11.2026): alle Beitragszahlungen, bei denen die Person nicht (mehr)
// in der Mitgliederliste steht, als eine Aufgabe für die Mitgliederbeauftragten. Server: klaerfaelle.py.
// Zwei Arten: „ohne Zuordnung“ (von der Buchhaltung gemeldet – zählt noch für keinen Kreisverband) und „schon gebucht“ (als „nicht in
// der Mitgliederliste“ einem Kreisverband zugeordnet – zur Prüfung). Klären: Mitglied zuordnen, neues Mitglied anlegen, bestätigen
// (war Mitglied) oder zurück an die Buchhaltung (kein Mitgliedsbeitrag). Beträge einzelner Zahlungen ja, Buchungen ändert hier niemand.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date } from '../format.js';
import { store, refresh, kvName } from '../store.js';
import { pageHead, icon, table, empty, safe, toast, badge } from '../ui.js';
import { pickMembers, sortName, EXTERNAL_REASON } from '../member-picker.js';
import { memberDialog } from './members.js';

let data = null;          // letzte Antwort von /api/klaerfaelle (bleibt beim Neuzeichnen stehen, bis die neue da ist)
let selected = null;      // id des geöffneten Klärfalls
let statusFilter = 'active';
let kindFilter = '';      // '' alle, 'open' ohne Zuordnung, 'booked' schon gebucht
let how = 'member';       // gewählter Weg im geöffneten Klärfall
let note = null;          // ungespeicherte Notiz zum geöffneten Klärfall (null = Stand des Servers)
let seq = 0;

const memberById = (id) => store.data.members.find((m) => m.id === Number(id));
const STATUS = { offen: ['offen', 'warn'], rueckfrage: ['Rückfrage läuft', 'navy'], geklaert: ['geklärt', 'ok'], zurueck: ['zurück an Buchhaltung', ''] };

/** Vorgabe für „Neues Mitglied“ aus dem Namen der Zahlung („Nachname, Vorname“ oder „Vorname Nachname“). */
function namePreset(name) {
  const text = String(name || '').trim();
  if (text.includes(',')) { const [last, first] = text.split(',').map((x) => x.trim()); return { last_name: last, first_name: first || '' }; }
  const words = text.split(/\s+/);
  return { first_name: words.slice(0, -1).join(' '), last_name: words.slice(-1)[0] || '' };
}

export function cases(main) {
  mount(main, html`<div class="page wide">
    ${pageHead('Mitgliederverwaltung · Aufgabe für die Mitgliederbeauftragten', 'Klärfälle')}
    <p class="muted" style="max-width:820px">Beitragszahlungen, bei denen die Person nicht (mehr) in der Mitgliederliste steht. Die Buchhaltung meldet sie aus dem
      Kontoauszug oder der SEPA-Datei hierher. „Ohne Zuordnung“ heißt: Der Betrag steht im Haushalt, zählt aber noch für kein Mitglied und keinen Kreisverband.
      „Schon gebucht“ zählt bereits für einen Kreisverband und wartet nur auf die Prüfung.</p>
    <div data-cases><p class="small muted">Wird geladen …</p></div>
  </div>`);
  const box = main.querySelector('[data-cases]');

  const act = (fn, done) => safe(async () => {
    await fn();
    toast(done);
    note = null;
    await refresh();      // zeichnet die Seite neu und lädt die Liste
  });

  function draw() {
    const c = data.counts;
    const list = data.items.filter((x) => (statusFilter === 'active' ? ['offen', 'rueckfrage'].includes(x.status) : !['offen', 'rueckfrage'].includes(x.status))
      && (!kindFilter || (kindFilter === 'booked') === x.booked));
    if (!list.some((x) => x.id === selected)) { selected = list[0]?.id ?? null; note = null; how = 'member'; }
    const cur = list.find((x) => x.id === selected);
    const solved = Object.entries(c.by_result).map(([k, n]) => `${n} ${({ mitglied: 'Mitglied gefunden', neu: 'neu angelegt', ehemalig: 'nicht mehr in der Liste',
      kein_beitrag: 'kein Beitrag', neu_zugeordnet: 'von der Buchhaltung zugeordnet' })[k] || k}`).join(' · ');
    mount(box, html`
      <section class="cards" aria-label="Stand">
        <div class="card"><span class="label">Zu klären</span><span class="value ${c.active ? 'over' : ''}">${c.active}</span><span class="note">zusammen ${eur(c.sum)}</span></div>
        <div class="card"><span class="label">davon ohne Zuordnung</span><span class="value">${c.unassigned}</span><span class="note">Kreisverbandsanteil wartet auf die Klärung</span></div>
        <div class="card"><span class="label">In Rückfrage</span><span class="value">${c.rueckfrage}</span><span class="note">beim Kreisverband nachgefragt</span></div>
        <div class="card"><span class="label">Geklärt in den letzten 30 Tagen</span><span class="value">${c.solved_30}</span><span class="note">${solved || '—'}</span></div>
      </section>
      <div class="split-row case-split">
        <section class="panel grow2" aria-label="Liste der Klärfälle">
          <div class="toolbar"><label class="field inline">Status<select data-status><option value="active" ${statusFilter === 'active' ? 'selected' : ''}>zu klären</option>
              <option value="done" ${statusFilter === 'done' ? 'selected' : ''}>erledigt</option></select></label>
            <label class="field inline">Art<select data-kind><option value="">alle</option><option value="open" ${kindFilter === 'open' ? 'selected' : ''}>ohne Zuordnung</option>
              <option value="booked" ${kindFilter === 'booked' ? 'selected' : ''}>schon gebucht</option></select></label>
            <span class="small muted">${list.length} ${list.length === 1 ? 'Fall' : 'Fälle'} · älteste Zahlung zuerst</span></div>
          ${list.length ? table(['Zahlung', 'Zahler', { label: 'Betrag', className: 'num' }, 'Stand'], list.map((x) => html`
            <tr class="clickable ${x.id === selected ? 'case-current' : ''}" data-case="${x.id}" tabindex="0" aria-current="${x.id === selected ? 'true' : 'false'}">
              <td class="nowrap">${date(x.day)}<span class="sub">${x.origin}</span></td>
              <td><strong style="font-weight:600">${x.name}</strong><span class="sub">${x.info || x.reference}</span></td>
              <td class="num ${x.amount < 0 ? 'over' : ''}">${eur(x.amount)}</td>
              <td><span class="case-state">${badge(...STATUS[x.status])} <span class="small">${x.booked ? `gebucht: ${kvName(x.kv)}` : ['offen', 'rueckfrage'].includes(x.status) ? 'ohne Zuordnung' : ''}</span></span>
                <span class="sub">${x.status === 'rueckfrage' && x.inquiry_kv ? `Rückfrage ${kvName(x.inquiry_kv)} · ` : ''}${['offen', 'rueckfrage'].includes(x.status) ? x.reason : x.result_text}</span></td></tr>`))
            : empty(statusFilter === 'active' ? 'Nichts zu klären' : 'Noch nichts erledigt', statusFilter === 'active' ? 'Alle gemeldeten Zahlungen sind geklärt.' : 'Geklärte Fälle erscheinen hier.')}
        </section>
        ${cur ? html`<section class="panel grow1 case-detail" aria-labelledby="case-title">${detail(cur)}</section>` : ''}
      </div>`);
    box.querySelector('[data-status]').onchange = (e) => { statusFilter = e.target.value; selected = null; draw(); };
    box.querySelector('[data-kind]').onchange = (e) => { kindFilter = e.target.value; selected = null; draw(); };
    box.querySelectorAll('[data-case]').forEach((row) => {
      const open = () => { selected = Number(row.dataset.case); note = null; how = 'member'; draw(); box.querySelector('.case-detail')?.scrollIntoView({ block: 'nearest' }); };
      row.onclick = open;
      row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    });
    if (cur) bindDetail(cur);
  }

  function detail(x) {
    const active = ['offen', 'rueckfrage'].includes(x.status);
    const hints = x.suggestions.map(memberById).filter(Boolean);
    const kvs = store.data.kv.filter((k) => k.code !== 'LV');
    const text = note ?? x.note;
    const option = (value, title, sub) => html`<label class="case-option ${how === value ? 'on' : ''}"><input type="radio" name="case-how" value="${value}" ${how === value ? 'checked' : ''}>
      <span><strong>${title}</strong><span class="sub">${sub}</span></span></label>`;
    return html`<div class="stack">
      <div><p class="small muted">Klärfall ${x.number} · gemeldet am ${date(x.created.slice(0, 10))}</p>
        <h2 id="case-title">${x.name} · ${eur(x.amount)}</h2>
        <p class="small">Zahlung vom ${date(x.day)} · ${x.origin}${x.info ? ` · ${x.info}` : ''}</p>
        ${(x.payer || x.reference) && (x.reference !== x.info || x.payer !== x.name) ? html`<p class="small muted">Im Kontoauszug: ${[x.payer, x.reference].filter(Boolean).join(' · ')}${
          x.entry_amount !== x.amount ? ` · Sammelbuchung ${eur(x.entry_amount)}` : ''}</p>` : ''}</div>
      ${!x.booked && !active ? '' : html`<p class="notice ${x.booked ? '' : 'warn'}">${x.booked ? html`Schon gebucht als „nicht in der Mitgliederliste“ für <strong>${kvName(x.kv)}</strong>${x.ext_number ? ` (Nr. ${x.ext_number})` : ''} –
        zählt im Haushalt und für den Anteil dieses Kreisverbands. Grund: ${x.reason}.` : html`Noch <strong>ohne Zuordnung</strong>: zählt im Haushalt, aber für kein Mitglied und keinen Kreisverband.
        Warum unklar: ${x.reason}.`}</p>`}
      ${active ? html`
        ${hints.length ? html`<div class="case-hints"><span class="small muted">Passt vielleicht:</span>
          ${hints.map((m) => html`<button type="button" class="btn small" data-hint="${m.id}">${sortName(m)} (${m.number}, ${kvName(m.kv)})</button>`)}</div>` : ''}
        <fieldset class="case-options"><legend>Was ist richtig?</legend>
          ${option('member', 'Ist Mitglied', 'unter anderem Namen oder anderer Schreibweise – Mitglied suchen und Zahlung zuordnen')}
          ${option('new', 'Neues Mitglied', 'Mitglied anlegen, die Zahlung wird gleich zugeordnet')}
          ${x.booked ? option('former', 'Stimmt so: nicht (mehr) in der Mitgliederliste', `bleibt für ${kvName(x.kv)} gebucht, der Klärfall ist erledigt`)
            : option('former', 'War Mitglied, Daten gelöscht', 'als Person außerhalb der Mitgliederliste buchen – zählt für Haushalt und Kreisverbandsanteil')}
          ${x.booked ? '' : option('none', 'Kein Mitgliedsbeitrag', 'zurück an die Buchhaltung, z. B. zur Rückzahlung oder als Spende')}
        </fieldset>
        ${how === 'former' && !x.booked ? html`<div class="formgrid">
          <label class="field">Name<input data-ext="name" autocomplete="off" maxlength="120" value="${x.name}"></label>
          <label class="field">Mitgliedsnummer<input data-ext="number" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" maxlength="30" placeholder="falls bekannt"></label>
          <label class="field">Kreisverband<select data-ext="kv">${store.data.kv.map((k) => html`<option value="${k.code}" ${k.code === (x.entry_kv || 'LV') ? 'selected' : ''}>${k.code === 'LV' ? 'kein Kreisverband (Landesverband)' : k.name}</option>`)}</select></label>
          <label class="field">Grund<input data-ext="reason" autocomplete="off" maxlength="200" value="${EXTERNAL_REASON}"></label></div>` : ''}
        <label class="field">Notiz<textarea data-note rows="2" maxlength="500" placeholder="z. B. mit wem gesprochen, was fehlt">${text}</textarea></label>
        <div class="form-actions case-actions">
          <button type="button" class="btn primary" data-resolve>${{ member: 'Mitglied suchen und zuordnen', new: 'Mitglied anlegen und zuordnen',
            former: x.booked ? 'Bestätigen' : 'So buchen', none: 'Zurück an die Buchhaltung' }[how]}</button>
          <button type="button" class="btn ghost" data-save-note>Notiz speichern</button></div>
        <div class="case-inquiry">${x.status === 'rueckfrage' ? html`<span class="small">Rückfrage läuft${x.inquiry_kv ? ` bei ${kvName(x.inquiry_kv)}` : ''}.</span>
            <button type="button" class="btn small" data-inquiry-off>Rückfrage beendet</button>`
          : html`<label class="field inline">Rückfrage beim Kreisverband<select data-inquiry-kv><option value="">ohne Angabe</option>
              ${kvs.map((k) => html`<option value="${k.code}" ${k.code === x.kv ? 'selected' : ''}>${k.name}</option>`)}</select></label>
            <button type="button" class="btn small" data-inquiry-on>Vermerken</button>`}</div>
        <p class="small muted">Geklärt wird nur die Zuordnung – die Buchung im Kontoauszug bleibt, wie sie ist. Wer wann geklärt hat, steht im Änderungsprotokoll.</p>`
      : html`<p>${badge(...STATUS[x.status])} ${x.result_text}${x.resolved ? ` · ${date(x.resolved.slice(0, 10))}` : ''}</p>
        ${x.note ? html`<p class="small">Notiz: ${x.note}</p>` : ''}
        <p class="small muted">${x.status === 'zurueck' ? 'Die Zahlung liegt wieder bei der Buchhaltung (Beitragseingänge).' : 'Die Zuordnung lässt sich in der Buchhaltung unter „Beitragseingänge“ ändern.'}</p>`}
    </div>`;
  }

  function bindDetail(x) {
    const area = box.querySelector('[data-note]');
    if (!area) return;
    area.oninput = () => { note = area.value; };
    const body = () => ({ id: x.id, note: area.value });
    const toMember = (id) => act(() => api('/api/klaerfall-klaeren', { ...body(), member_id: id }), 'Zahlung zugeordnet – Klärfall erledigt.');
    // Neues Mitglied: Es ist schon gespeichert, wenn die Zuordnung scheitert – dann nicht noch einmal anlegen (Prüfbefund 0.31),
    // sondern auf „Ist Mitglied“ stellen und sagen, was zu tun ist. Die Notiz steht in `note`, body() liest sie nach dem Neuzeichnen von dort.
    const toNewMember = async (id) => {
      how = 'member';
      const payload = { id: x.id, note: note ?? x.note, member_id: id, new: true };
      try {
        await api('/api/klaerfall-klaeren', payload);
        toast('Mitglied angelegt und Zahlung zugeordnet.');
        note = null;
      } catch (error) {
        toast(`Das Mitglied ist angelegt, die Zahlung aber noch nicht zugeordnet: ${error.message} Bitte über „Ist Mitglied“ zuordnen – nicht noch einmal anlegen.`, 'error');
      }
      await refresh();
    };
    // Auswahl wechseln: neu zeichnen, den Tastaturfokus aber auf der gewählten Möglichkeit lassen (Pfeiltasten in der Gruppe)
    box.querySelectorAll('input[name="case-how"]').forEach((r) => (r.onchange = () => { how = r.value; draw(); box.querySelector('input[name="case-how"]:checked')?.focus(); }));
    box.querySelectorAll('[data-hint]').forEach((b) => (b.onclick = () => toMember(Number(b.dataset.hint))));
    box.querySelector('[data-save-note]').onclick = () => act(() => api('/api/klaerfall-notiz', body()), 'Notiz gespeichert.');
    box.querySelector('[data-inquiry-on]')?.addEventListener('click', () => act(() => api('/api/klaerfall-notiz',
      { ...body(), status: 'rueckfrage', kv: box.querySelector('[data-inquiry-kv]').value }), 'Rückfrage vermerkt.'));
    box.querySelector('[data-inquiry-off]')?.addEventListener('click', () => act(() => api('/api/klaerfall-notiz', { ...body(), status: 'offen' }), 'Rückfrage beendet.'));
    box.querySelector('[data-resolve]').onclick = () => {
      if (how === 'member') return pickMembers({ hint: `${x.name} ${x.info}`, title: `Mitglied zu „${x.name}“ suchen`, onDone: ([m]) => toMember(m.member_id) });
      if (how === 'new') return memberDialog(null, { preset: { ...namePreset(x.name), kv: [x.kv, x.entry_kv].find((k) => k && k !== 'LV') || '' }, onSaved: (id) => safe(() => toNewMember(id)) });
      if (how === 'none') return act(() => api('/api/klaerfall-klaeren', { ...body(), no_dues: true }), 'Zurück an die Buchhaltung gegeben.');
      if (x.booked) return act(() => api('/api/klaerfall-klaeren', { ...body(), confirm: true }), 'Bestätigt – Klärfall erledigt.');
      const ext = Object.fromEntries([...box.querySelectorAll('[data-ext]')].map((f) => [f.dataset.ext, f.value.trim()]));
      return act(() => api('/api/klaerfall-klaeren', { ...body(), external: ext }), 'Als „nicht in der Mitgliederliste“ gebucht – Klärfall erledigt.');
    };
  }

  // Sprung aus „Beitragseingänge → Zum Klärfall“ (store.caseId): erst mit der frisch geladenen Liste auswerten – in der alten fehlt ein
  // eben gemeldeter Fall, und dann bliebe ein anderer gewählt (Nachprüfung 0.31). Bis dahin nicht mit dem alten Stand zeichnen.
  const jump = store.caseId;   // gleich verbrauchen: bleibt nicht hängen, wenn das Laden scheitert oder die Seite vorher verlassen wird
  store.caseId = null;
  if (data && !jump) draw();
  safe(async () => {
    const mine = ++seq;
    let next;
    try { next = await api('/api/klaerfaelle'); } catch (error) {
      if (data && mine === seq && box.isConnected) draw();   // Laden gescheitert: wenigstens den letzten Stand zeigen
      throw error;
    }
    if (mine !== seq || !box.isConnected) return;
    data = next;
    if (jump) {
      const wanted = data.items.find((x) => x.id === jump);
      if (wanted) { statusFilter = ['offen', 'rueckfrage'].includes(wanted.status) ? 'active' : 'done'; kindFilter = ''; selected = wanted.id; note = null; how = 'member'; }
    }
    draw();
  });
}
