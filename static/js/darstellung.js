// Darstellung (0.48, Wunsch Lennert Gust 22.11.2026): „Klassisch“ (das bisherige Design, unverändert) oder „Glas“ (css/glas.css)
// in hell, dunkel oder automatisch nach dem Gerät. Die Wahl gilt je Browser (localStorage) – keine Datenbank, nichts auf dem Server.
// Zur Wahl beim Anmelden (main.js) und jederzeit über „Darstellung“ in der Seitenleiste. Erstes Bild: darstellung-start.js.
import { html } from './html.js';
import { dialog } from './ui.js';

const KEY = 'ju-finanzen-darstellung';
const DUNKEL = window.matchMedia('(prefers-color-scheme: dark)');

/** Gespeicherte Wahl {design: 'klassisch'|'glas', farbe: 'hell'|'dunkel'|'auto'} – ohne Wahl „klassisch“. */
export function darstellung() {
  let wahl = null;
  try { wahl = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch { wahl = null; }
  const design = wahl?.design === 'glas' ? 'glas' : 'klassisch';
  const farbe = ['hell', 'dunkel', 'auto'].includes(wahl?.farbe) ? wahl.farbe : 'auto';
  return { design, farbe };
}

/** Wahl auf die Seite anwenden: html[data-design="glas"][data-farbe="hell|dunkel"]; klassisch = keine Attribute. */
function anwenden({ design, farbe }) {
  const root = document.documentElement;
  if (design !== 'glas') {
    root.removeAttribute('data-design');
    root.removeAttribute('data-farbe');
    return;
  }
  root.setAttribute('data-design', 'glas');
  root.setAttribute('data-farbe', farbe === 'auto' ? (DUNKEL.matches ? 'dunkel' : 'hell') : farbe);
}
DUNKEL.addEventListener('change', () => anwenden(darstellung()));   // „automatisch“ folgt dem Gerät
anwenden(darstellung());

/** Wahl speichern und sofort anwenden (ohne Speicher im Browser gilt sie nur bis zum Neuladen). */
export function setDarstellung(wahl) {
  const neu = { ...darstellung(), ...wahl };
  try { localStorage.setItem(KEY, JSON.stringify(neu)); } catch { /* privates Fenster: nur für diese Seite */ }
  anwenden(neu);
  return neu;
}

const thumb = (art) => html`<span class="ds-thumb ds-${art}" aria-hidden="true"><i></i><b></b><b></b></span>`;

/** Auswahl als Formularteil: Design (Klassisch/Glas) und – nur bei Glas – Helligkeit. */
export function darstellungWahl() {
  const { design, farbe } = darstellung();
  return html`<div class="ds-pick" data-darstellung>
    <fieldset><legend>Darstellung</legend><div class="ds-designs">
      <label class="ds-option"><input type="radio" name="ds-design" value="klassisch" ${design === 'klassisch' ? 'checked' : ''}>${thumb('klassisch')}
        <span><strong>Klassisch</strong><span class="sub">das bisherige Design</span></span></label>
      <label class="ds-option"><input type="radio" name="ds-design" value="glas" ${design === 'glas' ? 'checked' : ''}>${thumb('glas')}
        <span><strong>Glas <span class="badge ok">neu</span></strong><span class="sub">hell oder dunkel</span></span></label>
    </div></fieldset>
    <fieldset class="ds-farben" ${design === 'glas' ? '' : 'hidden'}><legend>Helligkeit</legend>
      <span class="ds-seg">${[['hell', 'Hell'], ['dunkel', 'Dunkel'], ['auto', 'Automatisch']].map(([wert, text]) => html`
        <label><input type="radio" name="ds-farbe" value="${wert}" ${farbe === wert ? 'checked' : ''}><span>${text}</span></label>`)}</span>
    </fieldset>
  </div>`;
}

/** Auswahl in `el` lebendig machen: jede Änderung gilt sofort und wird gespeichert. */
export function bindDarstellung(el) {
  const box = el.querySelector('[data-darstellung]');
  if (!box) return;
  const farben = box.querySelector('.ds-farben');
  box.addEventListener('change', (e) => {
    if (e.target.name === 'ds-design') {
      setDarstellung({ design: e.target.value });
      farben.hidden = e.target.value !== 'glas';
    } else if (e.target.name === 'ds-farbe') setDarstellung({ farbe: e.target.value });
  });
}

/** Dialog „Darstellung“ (Seitenleiste). */
export function darstellungDialog() {
  const el = dialog('Darstellung', html`${darstellungWahl()}
    <p class="small muted">Gilt für diesen Browser. Klassisch ist das bisherige Design, Glas das neue – hell, dunkel oder automatisch wie das Gerät.</p>
    <div class="form-actions"><button type="button" class="btn primary" data-close>Fertig</button></div>`);
  el.querySelectorAll('[data-close]').forEach((b) => (b.onclick = () => el.close()));
  bindDarstellung(el);
  return el;
}
