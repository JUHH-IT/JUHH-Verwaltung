// Doppelte Mitglieder (0.45, Wunsch Lennert Gust 21.11.2026): Gruppen möglicher Doppelter mit Grund (Server: doppelte.py), je Mitglied
// die Angaben zum Vergleichen und die Akte; „Kein Duplikat“ blendet eine Gruppe dauerhaft aus, „Zusammenführen“ (0.47) lässt ein
// Mitglied nach Vorschau im anderen aufgehen.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { date } from '../format.js';
import { store, kvName, can, render, refresh } from '../store.js';
import { dialog, overlay, badge, safe, toast, table } from '../ui.js';
import { ovById, ovLabel } from '../gliederung.js';
import { sortName, addressLine } from '../member-picker.js';
import { account } from './members.js';
import { isActive } from '../mitglieder-listen.js';

/** Dialog mit allen Gruppen; nach „Kein Duplikat“ wird die Seite dahinter neu gezeichnet (Zahl im Überblick). */
export function doppelteDialog() {
  const el = dialog('Mögliche doppelte Mitglieder', html`<div data-doppelte><p class="small muted">Wird gesucht …</p></div>`, { wide: true });
  safe(async () => draw(el, (await api('/api/doppelte')).gruppen, doppelteDialog));
}

function draw(el, gruppen, reopen) {
  const byId = new Map(store.data.members.map((m) => [m.id, m]));
  const write = can('mitglieder', 'schreiben');
  mount(el.querySelector('[data-doppelte]'), gruppen.length ? html`
    <p class="muted">${gruppen.length} ${gruppen.length === 1 ? 'Gruppe' : 'Gruppen'} – gleicher Name mit gleichem Geburtsdatum oder gleicher Anschrift,
      gleiche E-Mail-Adresse mit gleichem Geburtsdatum oder ähnlicher Vorname bei gleichem Nachnamen und Geburtsdatum. Wirklich doppelt?
      „Zusammenführen“ übernimmt fehlende Angaben, Rechnungen, Zahlungen und Mandate in ein Mitglied – bitte auch in der CDU-Mitgliederverwaltung
      bereinigen. Verschiedene Personen: „Kein Duplikat“.</p>
    ${gruppen.map((g, i) => html`<section class="panel dp-group">
      <div class="panel-head"><p class="dp-why">${g.gruende.map((r) => badge(r, 'warn'))}</p>
        ${write ? html`<span class="dp-actions"><button type="button" class="btn small" data-merge="${i}">Zusammenführen</button>
          <button type="button" class="btn small ghost" data-nein="${i}">Kein Duplikat</button></span>` : ''}</div>
      ${table(['Mitglied', 'Geburtsdatum', 'Anschrift', 'E-Mail', 'Kreis- und Ortsverband', 'Mitgliedschaft'], g.ids.map((id) => byId.get(id)).filter(Boolean)
        .map((m) => html`<tr class="clickable" data-akte="${m.id}" tabindex="0"><td><strong style="font-weight:600">${sortName(m)}</strong><span class="sub">${m.number}</span></td>
          <td>${date(m.birth)}</td><td class="small">${addressLine(m)}</td><td class="small">${m.email || '—'}</td>
          <td>${kvName(m.kv)}${m.ov_id ? html`<span class="sub">${ovLabel(ovById(m.ov_id))}</span>` : ''}</td>
          <td class="small">seit ${date(m.joined)}${m.left_on ? html`<span class="sub">${isActive(m) ? 'Austritt zum' : 'ausgetreten'} ${date(m.left_on)}</span>` : ''}</td></tr>`))}
    </section>`)}` : html`<p class="muted">Keine möglichen Doppelten gefunden.</p>`);
  el.querySelectorAll('[data-akte]').forEach((row) => {
    const open = () => account(Number(row.dataset.akte), { back: reopen });
    row.onclick = open;
    row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
  });
  el.querySelectorAll('[data-nein]').forEach((b) => (b.onclick = () => confirmNein(gruppen[Number(b.dataset.nein)].ids, reopen)));
  el.querySelectorAll('[data-merge]').forEach((b) => (b.onclick = () => mergeDialog(gruppen[Number(b.dataset.merge)].ids, reopen)));
}

/** Vorschlag, wer bleibt: aktives Mitglied vor ausgetretenem, dann der frühere Eintritt, dann der ältere Datensatz. */
const rank = (m) => [isActive(m) ? 0 : 1, m.joined || '9', m.id];
const better = (a, b) => { const [x, y] = [rank(a), rank(b)]; return x[0] - y[0] || x[1].localeCompare(y[1]) || x[2] - y[2]; };

/** Zusammenführen (0.47): wählen, wer bleibt und wer darin aufgeht; der Server zeigt vorher, was ergänzt, umgehängt und übergangen
 * wird, und sperrt bei Hindernissen. Vorher legt er eine Sicherung an (Passwortbestätigung im Serverbetrieb). */
function mergeDialog(ids, back) {
  const members = ids.map((id) => store.data.members.find((m) => m.id === id)).filter(Boolean).sort(better);
  if (members.length < 2) return;
  const option = (m, sel) => html`<option value="${m.id}" ${m.id === sel ? 'selected' : ''}>${sortName(m)} · ${m.number}${isActive(m) ? '' : ' · ausgetreten'}</option>`;
  const el = dialog('Mitglieder zusammenführen', html`<form data-merge-form style="display:flex;flex-direction:column;gap:14px">
    <div class="formgrid"><label class="field">Bleibt<select name="keep">${members.map((m) => option(m, members[0].id))}</select></label>
      <label class="field">Geht darin auf und wird gelöscht<select name="drop">${members.map((m) => option(m, members[1].id))}</select></label></div>
    <div data-merge-preview><p class="small muted">Vorschau wird geladen …</p></div>
    <div class="form-actions"><button type="button" class="btn ghost" data-back>Zurück</button>
      <button type="submit" class="btn primary" data-merge-ok disabled>Zusammenführen</button></div></form>`, { wide: true });
  const form = el.querySelector('[data-merge-form]'), box = el.querySelector('[data-merge-preview]'), ok = el.querySelector('[data-merge-ok]');
  let shown = 0;
  const load = () => {
    const keep = Number(form.keep.value), drop = Number(form.drop.value), call = ++shown;
    ok.disabled = true;
    if (keep === drop) return mount(box, html`<p class="notice warn">Bitte zwei verschiedene Mitglieder wählen.</p>`);
    safe(async () => {
      const v = await api('/api/zusammenfuehren-vorschau', { keep, drop });
      if (call !== shown) return;                                    // inzwischen anders gewählt
      const [a, b] = [keep, drop].map((id) => members.find((m) => m.id === id));
      const left = !isActive(a) || !isActive(b);
      mount(box, html`
        <p><strong>${v.bleibt.name}</strong> (${v.bleibt.number}) bleibt, <strong>${v.entfaellt.name}</strong> (${v.entfaellt.number}) wird gelöscht.</p>
        ${v.hinweise.map((h) => html`<p class="small" data-merge-hint>${h}</p>`)}
        ${left ? html`<p class="notice warn" data-merge-left>Eines der Mitglieder ist ausgetreten – vielleicht ein Wiedereintritt mit neuer Mitgliedsnummer.
          Dann besser nicht zusammenführen, sondern „Kein Duplikat“.</p>` : ''}
        <p class="small">${v.ergaenzt.length ? html`Wird beim bleibenden Mitglied ergänzt: ${v.ergaenzt.join(', ')}.` : 'Keine fehlenden Angaben zu ergänzen.'}
          ${v.umhaengen.length ? html` Geht über: ${v.umhaengen.map(([label, n]) => `${n} ${label}`).join(', ')}.` : ''}</p>
        ${v.unterschiede.length ? html`<p class="small muted">Abweichende Angaben – es gelten die des bleibenden Mitglieds:</p>
          ${table(['Angabe', 'Bleibt', 'Wird gelöscht'], v.unterschiede.map(([k, x, y]) => html`<tr><td>${k}</td><td>${x}</td><td class="muted">${y}</td></tr>`))}` : ''}
        ${v.konflikte.length ? html`<div class="notice warn" data-merge-blocked><p>Zusammenführen geht erst, wenn das geklärt ist:</p>
          <ul>${v.konflikte.map((k) => html`<li>${k}</li>`)}</ul></div>`
        : html`<p class="small muted">Vorher wird automatisch eine Sicherung angelegt. Das Protokoll nennt beide Mitgliedsnummern.</p>`}`);
      ok.disabled = v.konflikte.length > 0;
    });
  };
  form.keep.onchange = form.drop.onchange = load;
  el.querySelector('[data-back]').onclick = back;
  form.onsubmit = (e) => {
    e.preventDefault();
    const keep = Number(form.keep.value), drop = Number(form.drop.value);
    safe(async () => {
      const r = await api('/api/zusammenfuehren', { keep, drop });
      await refresh();
      toast(`Zusammengeführt. Sicherung vorher: ${r.backup}`);
      back();
    });
  };
  load();
}

/** „Kein Duplikat“ erst nach Rückfrage: gilt dauerhaft für alle Mitglieder der Gruppe untereinander. */
function confirmNein(ids, reopen) {
  const o = overlay('Kein Duplikat?', html`<p>Diese ${ids.length} Mitglieder sind verschiedene Personen. Die Gruppe wird dauerhaft ausgeblendet –
    auch, wenn zwei von ihnen doch doppelt sind. Sind nur einige verschieden, das doppelte Mitglied zuerst bereinigen.</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button>
      <button type="button" class="btn primary" data-ok>Ja, kein Duplikat</button></div>`, { wide: false });
  o.querySelector('[data-ok]').onclick = () => safe(async () => {
    await api('/api/kein-duplikat', { ids });
    o.close();
    toast('Als „kein Duplikat“ bestätigt.');
    render();
    reopen();
  });
}
