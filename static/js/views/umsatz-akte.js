// Notiz und Belege am Kontoumsatz (0.60, REGELN Nr. 40, Wunsch Lennert Gust 23.11.2026): ein Baustein für die Liste der
// Kontoumsätze (views/umsaetze.js) und für die Buchungsmaske (views/verbuchen.js). Server: umsatzakte.py – lesen mit
// „Buchhaltung lesen“ (/api/umsatz-akte, /api/umsatz-beleg), ändern mit „Buchhaltung ändern“.
import { api, fileToBase64 } from '../api.js';
import { html, mount } from '../html.js';
import { can } from '../store.js';
import { icon, safe, toast, dialog } from '../ui.js';
import { wann } from './kenntnis.js';
import { bookingKind } from '../format.js';

/** Name des Zahlungspartners, wie ihn Liste und Maske zeigen. */
export const umsatzName = (b) => b.name || bookingKind(b.reference) || 'Ohne Zahlungspartner';
const groesse = (bytes) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1).replace('.', ',')} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);
const notizStand = (n) => (n ? `Gespeichert von ${n.von}, ${wann(n.am)}` : 'Die Notiz speichert sich beim Verlassen des Feldes.');

/** Notiz und Belege des Umsatzes b in box zeigen und pflegen. Der Baustein lädt und aktualisiert sich selbst; die Seite
 *  erfährt Änderungen über geaendert({ hat_notiz, belege }) und zieht nach nachBeleg() ihre Zahlen nach (vor der Meldung). */
export function akteFeld(box, b, { geaendert = () => {}, nachBeleg = async () => {} } = {}) {
  const darf = can('buchhaltung', 'schreiben');
  const lebt = () => box.isConnected && box.dataset.umsatz === String(b.id);
  box.dataset.umsatz = String(b.id);
  mount(box, html`<p class="small muted">Notiz und Belege werden geladen …</p>`);

  const laden = async () => {
    const a = await api(`/api/umsatz-akte?id=${b.id}`);
    if (lebt()) zeichne(a);
    return a;
  };

  function zeichne(a) {
    mount(box, html`<div class="ua-teil">
        <label class="ua-titel" for="ua-notiz-${b.id}">Notiz</label>
        <textarea id="ua-notiz-${b.id}" data-notiz rows="3" maxlength="1000" placeholder="z. B. Rückfrage beim Kreisverband, Beleg folgt" ${darf ? '' : 'readonly'}>${a.notiz?.text || ''}</textarea>
        <p class="ua-stand" data-notiz-stand role="status">${darf || a.notiz ? notizStand(a.notiz) : 'Keine Notiz.'}</p>
      </div>
      <div class="ua-teil">
        <h3 class="ua-titel">Belege</h3>
        ${a.belege.length ? html`<ul class="ua-belege">${a.belege.map((x) => html`<li>${icon('file', 16)}
          <span class="ua-name" title="${x.name}, angeheftet von ${x.von}, ${wann(x.am)}">${x.name}</span><span class="ua-gr">${groesse(x.size)}</span>
          <a class="blatt-link" href="/api/umsatz-beleg?id=${x.id}" target="_blank" rel="noopener" aria-label="${x.name} ansehen">Ansehen</a>
          ${darf ? html`<button type="button" class="ua-weg" data-weg="${x.id}" aria-label="${x.name} entfernen">${icon('trash', 16)}</button>` : ''}</li>`)}</ul>`
          : darf ? '' : html`<p class="ua-stand">Kein Beleg angeheftet.</p>`}
        ${darf ? html`<button type="button" class="ua-ablage" data-anheften>${icon('clip', 16)}<span>Beleg anheften: PDF, PNG oder JPG hierher ziehen oder auswählen</span></button>
          <input type="file" data-datei accept=".pdf,.png,.jpg,.jpeg" multiple hidden>` : ''}
      </div>`);
    if (!darf) return;

    const feld = box.querySelector('[data-notiz]'), stand = box.querySelector('[data-notiz-stand]');
    let gespeichert = a.notiz?.text || '';
    feld.onchange = () => safe(async () => {
      if (feld.value.trim() === gespeichert) return;
      stand.textContent = 'Wird gespeichert …';
      try {
        const r = await api('/api/umsatz-notiz', { id: b.id, text: feld.value });
        gespeichert = r.notiz?.text || '';
        geaendert({ hat_notiz: Boolean(r.notiz) });
        if (lebt()) stand.textContent = r.notiz ? notizStand(r.notiz) : 'Notiz gelöscht.';
      } catch (e) {
        if (lebt()) stand.textContent = 'Nicht gespeichert.';
        throw e;
      }
    });

    const anheften = (dateien) => safe(async () => {
      if (!dateien.length) return;
      const ablage = box.querySelector('[data-anheften]');
      ablage.disabled = true;
      let n = 0;
      try {
        for (const f of dateien) {
          await api('/api/umsatz-beleg', { id: b.id, file: { name: f.name, content: await fileToBase64(f) } });
          n += 1;
        }
      } finally {
        if (n) {
          const neu = await laden();
          geaendert({ belege: neu.belege.length });
          await nachBeleg();
          toast(n === 1 ? 'Beleg angeheftet.' : `${n} Belege angeheftet.`);
        } else if (lebt()) ablage.disabled = false;
      }
    });
    const datei = box.querySelector('[data-datei]'), ablage = box.querySelector('[data-anheften]');
    ablage.onclick = () => datei.click();
    datei.onchange = () => anheften([...datei.files]);
    ablage.ondragover = (e) => { e.preventDefault(); ablage.classList.add('ist-ziel'); };
    ablage.ondragleave = () => ablage.classList.remove('ist-ziel');
    ablage.ondrop = (e) => { e.preventDefault(); ablage.classList.remove('ist-ziel'); anheften([...e.dataTransfer.files]); };

    box.querySelectorAll('[data-weg]').forEach((knopf) => (knopf.onclick = () => {
      const x = a.belege.find((y) => String(y.id) === knopf.dataset.weg);
      const el = dialog('Beleg entfernen', html`<p>„${x.name}“ wird vom Umsatz gelöst und die Datei endgültig gelöscht. Das Entfernen steht im Protokoll.</p>
        <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button>
          <button type="button" class="btn danger" data-ok>Beleg entfernen</button></div>`);
      el.querySelector('[data-cancel]').onclick = () => el.close();
      el.querySelector('[data-ok]').onclick = () => safe(async () => {
        await api('/api/umsatz-beleg-entfernen', { id: x.id });
        el.close();
        const neu = await laden();
        geaendert({ belege: neu.belege.length });
        await nachBeleg();
        toast('Beleg entfernt.');
      });
    }));
  }

  safe(laden);
}
