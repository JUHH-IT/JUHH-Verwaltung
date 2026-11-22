// Gebiete der Ortsverbände (0.52, REGELN Nr. 33): Stadtteile je Ortsverband ankreuzen. Eine Stelle für Einstellungen → Gliederung und
// den Abgleich der Mitgliederverwaltung. Lesen braucht „Mitglieder lesen“, Speichern „Mitglieder ändern“ (so pflegt auch der
// Landesmitgliederbeauftragte die Gebiete). Server: ovabgleich.py (/api/ov-gebiete, /api/ov-gebiet).
import { api } from './api.js';
import { html, mount } from './html.js';
import { store, kvName } from './store.js';
import { dialog, overlay, safe, toast, table } from './ui.js';
import { ovLabel } from './gliederung.js';

/** Stadtteile eines Ortsverbands laut Antwort von /api/ov-gebiete. */
export const gebietVon = (geb, id) => geb?.gebiete?.[id] || [];

/** Kurz für Tabellen: zugeordnete Stadtteile, sonst der Vorschlag aus dem Namen. */
export function gebietText(geb, o) {
  const teile = gebietVon(geb, o.id);
  if (teile.length) return html`<span class="small">${teile.length > 4 ? `${teile.slice(0, 3).join(', ')} + ${teile.length - 3} weitere` : teile.join(', ')}</span>`;
  const vor = geb?.vorschlag?.[o.id];
  return vor ? html`<span class="small muted">Vorschlag: ${vor.join(', ')}</span>` : html`<span class="muted">—</span>`;
}

/** Stadtteile eines Kreisverbands zum Ankreuzen; daneben, welche anderen Ortsverbände den Stadtteil schon haben. */
export function gebietFelder(geb, kv, ovId, gewaehlt) {
  const teile = (geb?.stadtteile || []).filter((t) => t.kv === kv);
  if (!kv) return html`<p class="small muted">Erst den Kreisverband wählen.</p>`;
  if (!teile.length) return html`<p class="small muted">Keine Stadtteile – die amtlichen Adressdaten fehlen (Einstellungen → Schalter).</p>`;
  const andere = (name) => (store.data.ovs || []).filter((x) => x.id !== ovId && gebietVon(geb, x.id).includes(name)).map(ovLabel);
  return html`<div class="ov-gebiet-liste">${teile.map((t) => {
    const auch = andere(t.name);
    return html`<label class="check"><input type="checkbox" name="stadtteil" value="${t.name}" ${gewaehlt.has(t.name) ? 'checked' : ''}> ${t.name}${
      auch.length ? html` <span class="small muted">(auch ${auch.join(', ')})</span>` : ''}</label>`;
  })}</div>`;
}

/** Gebiet eines Ortsverbands bearbeiten (Popup über der Liste). */
function gebietDialog(o, geb, done) {
  const vorher = gebietVon(geb, o.id);
  const vorschlag = geb?.vorschlag?.[o.id] || [];
  const el = overlay(`Gebiet · ${ovLabel(o)}`, html`<form data-gebiet-form style="display:flex;flex-direction:column;gap:12px">
    <p class="small muted">${kvName(o.kv)} – Mitglieder mit Adresse in diesen Stadtteilen bekommen diesen Ortsverband vorgeschlagen. Ein Stadtteil kann zu
      mehreren Ortsverbänden gehören; dann wird im Mitglied gewählt.</p>
    ${vorschlag.length ? html`<p><button type="button" class="btn small" data-gebiet-vorschlag>Vorschlag aus dem Namen: ${vorschlag.join(', ')}</button></p>` : ''}
    ${gebietFelder(geb, o.kv, o.id, new Set(vorher))}
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button><button type="submit" class="btn primary">Gebiet speichern</button></div></form>`);
  const form = el.querySelector('[data-gebiet-form]');
  el.querySelector('[data-cancel]').onclick = () => el.close();
  const vor = el.querySelector('[data-gebiet-vorschlag]');
  if (vor) vor.onclick = () => form.querySelectorAll('input[name="stadtteil"]').forEach((x) => { if (vorschlag.includes(x.value)) x.checked = true; });
  form.onsubmit = (e) => {
    e.preventDefault();
    const stadtteile = [...form.querySelectorAll('input[name="stadtteil"]:checked')].map((x) => x.value);
    safe(async () => {
      await api('/api/ov-gebiet', { id: o.id, stadtteile });
      el.close();
      toast(`Gebiet von „${ovLabel(o)}“ gespeichert.`);
      done();
    });
  };
}

/** Alle Vorschläge aus den Namen auf einmal prüfen (aktive Ortsverbände ohne Gebiet); übernommen wird nur, was angehakt bleibt. */
export function gebietVorschlaege(geb, done) {
  const liste = Object.entries(geb.vorschlag).map(([id, teile]) => ({ o: (store.data.ovs || []).find((x) => x.id === Number(id)), teile }))
    .filter((x) => x.o).sort((a, b) => a.o.kv.localeCompare(b.o.kv) || ovLabel(a.o).localeCompare(ovLabel(b.o), 'de'));
  const el = overlay('Gebiete aus den Namen', html`<form data-vorschlag-form style="display:flex;flex-direction:column;gap:14px">
    <p class="small muted">Für Ortsverbände ohne Gebiet: Stadtteile, deren Name im Namen des Ortsverbands vorkommt. Bitte prüfen – nicht jeder Treffer
      stimmt, und Stadtteile ohne Treffer fehlen. Falsche Treffer abhaken; einzelne Ortsverbände lassen sich danach anpassen.</p>
    ${table(['', 'Kreisverband', 'Ortsverband', 'Stadtteile'], liste.map(({ o, teile }) => html`<tr>
      <td><input type="checkbox" name="ov" value="${o.id}" checked aria-label="${ovLabel(o)} übernehmen"></td><td>${kvName(o.kv)}</td><td>${ovLabel(o)}</td>
      <td class="small">${teile.join(', ')}</td></tr>`))}
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button>
      <button type="submit" class="btn primary">Angehakte übernehmen</button></div></form>`);
  const form = el.querySelector('[data-vorschlag-form]');
  el.querySelector('[data-cancel]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    const ids = [...form.querySelectorAll('input[name="ov"]:checked')].map((x) => Number(x.value));
    if (!ids.length) return toast('Nichts angehakt.', true);
    safe(async () => {
      for (const id of ids) await api('/api/ov-gebiet', { id, stadtteile: geb.vorschlag[id] });
      el.close();
      toast(`Gebiet für ${ids.length} ${ids.length === 1 ? 'Ortsverband' : 'Ortsverbände'} gespeichert.`);
      done();
    });
  };
}

/** Gebiete aller aktiven Ortsverbände (Mitgliederverwaltung, „Mitglieder ändern“); back: zurück (z. B. zum Abgleich). */
export function gebieteDialog(back) {
  const el = dialog('Gebiete der Ortsverbände', html`<div data-gebiete><p class="small muted">Wird geladen …</p></div>`, { wide: true });
  const zeichnen = (geb) => {
    const box = el.querySelector('[data-gebiete]');
    if (!box) return;
    const ovs = (store.data.ovs || []).filter((o) => o.active && !['LV', '5800'].includes(o.kv))
      .sort((a, b) => a.kv.localeCompare(b.kv) || ovLabel(a).localeCompare(ovLabel(b), 'de'));
    const neu = () => safe(async () => zeichnen(await api('/api/ov-gebiete')));
    mount(box, html`${geb.stadtteile.length ? '' : html`<p class="notice warn">Die amtlichen Adressdaten fehlen – ohne sie gibt es keine Stadtteile
        (Einstellungen → Schalter).</p>`}
      <div class="ova-head"><p class="small muted">Mitglieder mit Adresse im Gebiet bekommen den Ortsverband vorgeschlagen. Name und Nummer der
        Ortsverbände pflegt die Verwaltung unter Einstellungen → Gliederung.</p>
        ${geb.stadtteile.length && Object.keys(geb.vorschlag || {}).length ? html`<button type="button" class="btn small" data-gebiete-vorschlaege>
          Aus den Namen vorschlagen (${Object.keys(geb.vorschlag).length})</button>` : ''}</div>
      ${table(['Ortsverband', 'Gebiet', ''], ovs.map((o) => html`<tr><td>${ovLabel(o)}<span class="sub">${kvName(o.kv)}</span></td>
        <td data-gebiet-zelle="${o.id}">${gebietText(geb, o)}</td>
        <td>${geb.stadtteile.length ? html`<button type="button" class="btn small" data-gebiet-edit="${o.id}">Bearbeiten</button>` : ''}</td></tr>`))}
      <div class="form-actions">${back ? html`<button type="button" class="btn" data-gebiete-zurueck>Zurück zum Abgleich</button>` : ''}
        <button type="button" class="btn ghost" data-gebiete-schliessen>Schließen</button></div>`);
    box.querySelectorAll('[data-gebiet-edit]').forEach((b) => (b.onclick = () => gebietDialog(ovs.find((o) => o.id === Number(b.dataset.gebietEdit)), geb, neu)));
    box.querySelector('[data-gebiete-vorschlaege]')?.addEventListener('click', () => gebietVorschlaege(geb, neu));
    box.querySelector('[data-gebiete-zurueck]')?.addEventListener('click', back);
    box.querySelector('[data-gebiete-schliessen]').onclick = () => el.close();
  };
  safe(async () => zeichnen(await api('/api/ov-gebiete')));
}
