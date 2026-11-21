// Regelwerk (0.43, Wunsch Lennert Gust 21.11.2026): die Regeln aus Satzung und Finanzordnung, die das Programm anwendet – für alle
// Angemeldeten. Server: regelwerk.py (Texte, Stand, heutiger Wert aus den Stammdaten). Filter nach Bereich und Suche ohne neue Anfrage.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { go, can } from '../store.js';
import { pageHead, badge, empty, safe, icon } from '../ui.js';
import { fold } from '../member-picker.js';

let bereich = '';
let suche = '';
const STAND = { umgesetzt: 'ok', teilweise: 'warn', 'noch nicht': '' };

export function regelwerk(main) {
  mount(main, html`<div class="page wide"><p class="small muted">Wird geladen …</p></div>`);
  safe(async () => draw(main, await api('/api/regelwerk')));
}

function draw(main, r) {
  const words = fold(suche).split(/\s+/).filter(Boolean);
  const hit = (x) => words.every((w) => fold(`${x.quelle} ${x.titel} ${x.text} ${x.wert}`).includes(w));
  const list = r.regeln.filter((x) => (!bereich || x.bereich === bereich) && hit(x));
  const count = (id) => r.regeln.filter((x) => x.bereich === id).length;
  const stand = r.stand.map((s) => [s, r.regeln.filter((x) => x.stand === s).length]);
  const settings = can('einstellungen', 'schreiben');
  mount(main, html`<div class="page wide rw-page">
    ${pageHead('Verband', 'Regelwerk')}
    <p class="muted rw-intro">Diese Regeln wendet das Programm an. Grundlage: ${r.quellen.join(' und ')}. „Im Programm“ zeigt den Wert, der heute gilt.</p>
    <div class="toolbar">
      <div class="tabs" role="tablist" aria-label="Bereich">
        <button type="button" role="tab" data-bereich="" aria-selected="${!bereich}">Alle<span class="n">${r.regeln.length}</span></button>
        ${r.bereiche.map((b) => html`<button type="button" role="tab" data-bereich="${b.id}" aria-selected="${bereich === b.id}">${b.name}<span class="n">${count(b.id)}</span></button>`)}
      </div>
      <label class="search">${icon('search', 16)}<span class="sr-only">Regel oder Paragraf suchen</span>
        <input id="rw-search" type="search" autocomplete="off" placeholder="Regel oder § suchen" value="${suche}"></label>
    </div>
    <p class="small muted">${stand.map(([s, n], i) => html`${i ? ' · ' : ''}${n} ${s}`)}</p>
    ${r.bereiche.filter((b) => list.some((x) => x.bereich === b.id)).map((b) => html`<section class="panel rw-group" aria-labelledby="rw-${b.id}">
      <h2 id="rw-${b.id}">${b.name}</h2>
      <div class="rw-head" aria-hidden="true"><span>Quelle</span><span>Regel</span><span>Im Programm</span><span>Stand</span></div>
      ${list.filter((x) => x.bereich === b.id).map((x) => html`<div class="rw-row" data-regel="${x.id}">
        <span class="rw-source">${x.quelle}</span>
        <div><strong>${x.titel}</strong><p class="small muted">${x.text}</p></div>
        <div class="rw-value"><span class="rw-label">Im Programm</span>${x.wert}${x.einstellung && settings
          ? html`<button type="button" class="link small" data-settings>Einstellung ändern</button>` : ''}</div>
        <span>${badge(x.stand, STAND[x.stand])}</span></div>`)}
    </section>`)}
    ${list.length ? '' : empty('Keine Regel gefunden', 'Suche oder Bereich ändern.')}
    <section class="panel" aria-labelledby="rw-offen"><h2 id="rw-offen">Offene Fragen</h2>
      <ul class="rw-open">${r.offen.map((x) => html`<li>${x}</li>`)}</ul></section>
  </div>`);
  main.querySelectorAll('[data-bereich]').forEach((b) => (b.onclick = () => { bereich = b.dataset.bereich; draw(main, r); }));
  main.querySelectorAll('[data-settings]').forEach((b) => (b.onclick = () => go('settings')));
  const input = main.querySelector('#rw-search');
  input.oninput = () => {
    suche = input.value;
    const pos = input.selectionStart;
    draw(main, r);
    const again = main.querySelector('#rw-search');
    again.focus();
    again.setSelectionRange(pos, pos);
  };
}
