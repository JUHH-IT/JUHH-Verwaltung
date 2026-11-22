// Ortsverbände nach Adresse abgleichen (0.52, Wunsch Lennert Gust 22.11.2026): der Server (ovabgleich.py) vergleicht jedes aktive Mitglied
// mit dem Ortsverband, zu dessen Gebiet (Stadtteile, Einstellungen → Gliederung) die Adresse gehört. Vorschläge werden nur übernommen,
// wenn sie ausgewählt sind; ein Vorschlag in einen anderen Kreisverband ist nicht vorausgewählt (die Satzung erlaubt die Mitgliedschaft
// in einem anderen Kreisverband). Adressen außerhalb Hamburgs oder nicht gefunden stehen unter „Adresse prüfen“; „Ortsverband fest“
// nimmt ein Mitglied dauerhaft aus dem Abgleich.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { store, kvName, can, refresh } from '../store.js';
import { dialog, badge, safe, toast, table } from '../ui.js';
import { ovById, ovLabel } from '../gliederung.js';
import { sortName, addressLine } from '../member-picker.js';
import { memberDialog } from './members.js';
import { gebieteDialog } from '../ov-gebiete.js';

const ART = { ausserhalb: 'außerhalb Hamburgs', unbekannt: 'nicht gefunden', leer: 'ohne Anschrift' };

export function ovAbgleichDialog() {
  const el = dialog('Ortsverbände nach Adresse abgleichen', html`<div data-ova><p class="small muted">Wird geprüft …</p></div>`, { wide: true });
  safe(async () => draw(el, await api('/api/ov-abgleich')));
}

const bisher = (e) => html`${kvName(e.kv_alt)}${e.ov_alt ? html`<span class="sub">${ovLabel(ovById(e.ov_alt))}</span>` : html`<span class="sub muted">ohne Ortsverband</span>`}`;

function draw(el, a) {
  const box = el.querySelector('[data-ova]');
  if (!box) return;
  const write = can('mitglieder', 'schreiben');
  const byId = new Map(store.data.members.map((m) => [m.id, m]));
  const wer = (id) => byId.get(id);
  const person = (m) => html`<strong style="font-weight:600">${sortName(m)}</strong><span class="sub">${m.number}</span>`;
  const toGliederung = html`<button type="button" class="btn small" data-ova-gliederung>Gebiete festlegen</button>`;
  if (!a.verfuegbar) {
    return mount(box, html`<p class="notice warn">Die amtlichen Hamburger Adressdaten sind noch nicht geladen – ohne sie lässt sich der Stadtteil
      einer Adresse nicht bestimmen. Laden unter Einstellungen → Schalter.</p>`);
  }
  const vorschlaege = [...a.aendern, ...a.mehrdeutig].filter((e) => wer(e.id));
  const wechsel = vorschlaege.filter((e) => !e.ovs && e.kv_anders).length;
  const eintrag = new Map(vorschlaege.map((e) => [e.id, e]));
  const fehler = a.fehler.filter((e) => wer(e.id));
  const ohne = new Map();
  for (const e of a.ohne_gebiet) {
    if (!wer(e.id)) continue;
    const key = `${e.kv}|${e.stadtteil}`;
    if (!ohne.has(key)) ohne.set(key, { kv: e.kv, stadtteil: e.stadtteil, ids: [] });
    ohne.get(key).ids.push(e.id);
  }
  const wahl = (e) => {
    const ovs = e.ovs || [e.ov];
    const eindeutig = !e.ovs && !e.kv_anders;                    // ein Ortsverband im selben Kreisverband: „Alle übernehmen“ darf ihn wählen
    const vor = eindeutig && !e.hinweis ? e.ov : null;              // vorausgewählt nur bei sicherer Lage (Hausnummer im amtlichen Bestand)
    return html`<select name="ova-${e.id}" data-ova-wahl="${e.id}" ${eindeutig ? 'data-ova-eindeutig' : ''} aria-label="Aktion für ${sortName(wer(e.id))}">
      ${ovs.map((id) => html`<option value="ov:${id}" ${id === vor ? 'selected' : ''}>${e.ovs ? `→ ${ovLabel(ovById(id))}` : 'übernehmen'}</option>`)}
      <option value="" ${vor ? '' : 'selected'}>nicht ändern</option>
      <option value="fest">Ortsverband fest</option></select>`;
  };
  const ziel = (e) => (e.ovs
    ? html`<span class="muted">mehrere: ${e.ovs.map((id, i) => html`${i ? ' · ' : ''}${ovLabel(ovById(id))}`)}</span>`
    : html`${ovLabel(ovById(e.ov))}${e.kv_anders ? html`<span class="sub">${badge(`anderer Kreisverband: ${kvName(e.kv)}`, 'warn')}</span>` : ''}`);
  mount(box, html`
    ${a.gebiete ? '' : html`<p class="notice warn">Noch keinem Ortsverband sind Stadtteile zugeordnet. Erst die Gebiete festlegen, dann abgleichen. ${write ? toGliederung : ''}</p>`}
    ${write && a.gebiete ? html`<p class="ova-gebiete-link">${toGliederung}</p>` : ''}
    <p class="muted" data-ova-summe>${a.passt} passen · ${vorschlaege.length} ${vorschlaege.length === 1 ? 'Vorschlag' : 'Vorschläge'} ·
      ${fehler.length} Adresse${fehler.length === 1 ? '' : 'n'} prüfen · ${a.ohne_gebiet.length} ohne Gebiet${a.fest ? ` · ${a.fest} mit festem Ortsverband` : ''}${
      a.sonder ? ` · ${a.sonder} im Landesverband oder in der Schüler Union (ohne Abgleich)` : ''}</p>
    <form data-ova-form style="display:flex;flex-direction:column;gap:16px">
      ${vorschlaege.length ? html`<section class="ova-part" data-ova-vorschlaege><div class="ova-head"><h3>Vorschläge</h3>
        ${write ? html`<span class="ova-actions"><button type="button" class="btn small ghost" data-ova-alle title="Alle eindeutigen Vorschläge im selben Kreisverband">Alle übernehmen</button>
          <button type="button" class="btn small ghost" data-ova-keine>Keine</button></span>` : ''}</div>
        <p class="small muted">Vorausgewählt sind eindeutige Vorschläge im selben Kreisverband, deren Adresse sicher gefunden wurde. Ein Wechsel des
          Kreisverbands nur einzeln und bewusst wählen – die Satzung erlaubt die Mitgliedschaft in einem anderen Kreisverband („Ortsverband fest“);
          „Eintritt Kreisverband“ bleibt dabei stehen.${wechsel ? ` ${wechsel} ${wechsel === 1 ? 'Vorschlag wechselt' : 'Vorschläge wechseln'} den Kreisverband.` : ''}</p>
        ${table(['Mitglied', 'Wohnort', 'Bisher', 'Nach der Adresse', ...(write ? ['Aktion'] : [])], vorschlaege.map((e) => html`<tr data-ova-row="${e.id}">
          <td>${person(wer(e.id))}</td><td>${e.stadtteil}${e.hinweis ? html`<span class="sub">${e.hinweis}</span>` : ''}</td><td>${bisher(e)}</td><td>${ziel(e)}</td>
          ${write ? html`<td>${wahl(e)}</td>` : ''}</tr>`))}</section>` : ''}
      ${fehler.length ? html`<section class="ova-part" data-ova-fehler><div class="ova-head"><h3>Adresse prüfen</h3></div>
        <p class="small muted">Diese Adressen liegen außerhalb Hamburgs, fehlen oder wurden nicht gefunden. Adresse korrigieren – oder, wenn sie
          stimmt, den Ortsverband fest lassen.</p>
        ${table(['Mitglied', 'Anschrift', 'Problem', 'Bisher', ...(write ? ['Aktion'] : [])], fehler.map((e) => {
          const m = wer(e.id);
          return html`<tr data-ova-row="${e.id}"><td>${person(m)}</td><td class="small">${addressLine(m) || '—'}</td>
            <td>${badge(ART[e.art] || e.art, 'warn')}<span class="sub">${e.hinweis}</span></td><td>${bisher({ kv_alt: m.kv, ov_alt: m.ov_id })}</td>
            ${write ? html`<td><span class="ova-cell"><button type="button" class="btn small" data-ova-edit="${e.id}">Bearbeiten</button>
              <select name="ova-${e.id}" data-ova-wahl="${e.id}" aria-label="Aktion für ${sortName(m)}"><option value="">offen lassen</option>
                <option value="fest">Ortsverband fest</option></select></span></td>` : ''}</tr>`;
        }))}</section>` : ''}
      ${ohne.size ? html`<section class="ova-part" data-ova-ohne><div class="ova-head"><h3>Stadtteile ohne Ortsverband</h3>${write ? toGliederung : ''}</div>
        <p class="small muted">Für diese Stadtteile ist noch kein Ortsverband hinterlegt – die Mitglieder bleiben, wie sie sind.</p>
        <ul class="ova-ohne">${[...ohne.values()].map((g) => html`<li><details><summary>${g.stadtteil} <span class="muted">(${kvName(g.kv)})</span> –
          ${g.ids.length} ${g.ids.length === 1 ? 'Mitglied' : 'Mitglieder'}</summary>
          <p class="small">${g.ids.map((id, i) => html`${i ? ' · ' : ''}${sortName(wer(id))}`)}</p></details></li>`)}</ul></section>` : ''}
      ${!vorschlaege.length && !fehler.length && !ohne.size ? html`<p data-ova-fertig>${a.passt ? 'Alle Ortsverbände passen zur Adresse.' : 'Keine Mitglieder zum Abgleichen.'}</p>` : ''}
      ${write && (vorschlaege.length || fehler.length) ? html`<div class="form-actions"><span class="small muted">Vorher wird automatisch eine Sicherung angelegt.</span>
        <button type="button" class="btn ghost" data-close-ova>Schließen</button>
        <button type="submit" class="btn primary" data-ova-ok>Auswahl übernehmen</button></div>` : ''}
    </form>`);
  const form = box.querySelector('[data-ova-form]');
  const selects = [...form.querySelectorAll('[data-ova-wahl]')];
  const ok = form.querySelector('[data-ova-ok]');
  const zaehlen = () => {
    const n = selects.filter((s) => s.value).length;
    if (ok) { ok.textContent = n ? `Auswahl übernehmen (${n})` : 'Auswahl übernehmen'; ok.disabled = !n; }
  };
  selects.forEach((s) => (s.onchange = zaehlen));
  const setzen = (alle) => {
    // „Alle“ nur für eindeutige Vorschläge im selben Kreisverband; Wechsel des Kreisverbands und mehrere Ortsverbände bleiben Handwahl
    for (const s of form.querySelectorAll('[data-ova-vorschlaege] [data-ova-wahl]')) s.value = alle && s.hasAttribute('data-ova-eindeutig') ? s.options[0].value : (alle ? s.value : '');
    zaehlen();
  };
  const alle = form.querySelector('[data-ova-alle]');
  if (alle) alle.onclick = () => setzen(true);
  const keine = form.querySelector('[data-ova-keine]');
  if (keine) keine.onclick = () => setzen(false);
  box.querySelectorAll('[data-ova-gliederung]').forEach((b) => (b.onclick = () => gebieteDialog(ovAbgleichDialog)));   // Gebiete mit „Mitglieder ändern“
  box.querySelectorAll('[data-ova-edit]').forEach((b) => (b.onclick = () => memberDialog(wer(Number(b.dataset.ovaEdit)), { onSaved: ovAbgleichDialog })));
  const close = form.querySelector('[data-close-ova]');
  if (close) close.onclick = () => el.close();
  form.onsubmit = (ev) => {
    ev.preventDefault();
    const aendern = [], fest = [];
    for (const s of selects) {
      const id = Number(s.dataset.ovaWahl);
      if (s.value === 'fest') fest.push(id);
      else if (s.value.startsWith('ov:')) aendern.push({ id, ov: Number(s.value.slice(3)), ov_alt: eintrag.get(id).ov_alt ?? null, kv_alt: eintrag.get(id).kv_alt });   // Stand beim Anzeigen: inzwischen von Hand geändert = übersprungen
    }
    if (!aendern.length && !fest.length) return toast('Nichts ausgewählt.', true);
    safe(async () => {
      const r = await api('/api/ov-abgleich', { aendern, fest });
      await refresh();
      toast(`${r.geaendert} ${r.geaendert === 1 ? 'Mitglied' : 'Mitglieder'} geändert${r.fest ? `, ${r.fest} mit festem Ortsverband` : ''}${
        r.uebersprungen ? `, ${r.uebersprungen} übersprungen (inzwischen geändert)` : ''}. Sicherung vorher: ${r.backup}`);
      draw(el, await api('/api/ov-abgleich'));
    });
  };
  zaehlen();
}
