// Doppelte Mitglieder (0.45, Wunsch Lennert Gust 21.11.2026): Gruppen möglicher Doppelter mit Grund (Server: doppelte.py), je Mitglied
// die Angaben zum Vergleichen und die Akte; „Kein Duplikat“ blendet eine Gruppe dauerhaft aus. Nichts wird zusammengeführt.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { date } from '../format.js';
import { store, kvName, can, render } from '../store.js';
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
      gleiche E-Mail-Adresse mit gleichem Geburtsdatum oder ähnlicher Vorname bei gleichem Nachnamen und Geburtsdatum. Wirklich doppelt? Angaben im richtigen Mitglied ergänzen und das
      überzählige löschen (nur ohne Rechnungen, Beitragszahlungen und Mandate) bzw. in der CDU-Mitgliederverwaltung bereinigen.</p>
    ${gruppen.map((g, i) => html`<section class="panel dp-group">
      <div class="panel-head"><p class="dp-why">${g.gruende.map((r) => badge(r, 'warn'))}</p>
        ${write ? html`<button type="button" class="btn small" data-nein="${i}">Kein Duplikat</button>` : ''}</div>
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
