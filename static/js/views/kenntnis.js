// Zur Kenntnis (0.54, REGELN Nr. 35, Wunsch Lennert Gust 22.11.2026): jede Änderung an den Stammdaten gilt sofort; hier steht je
// Mitglied ein Punkt mit Vorher und Jetzt, den die Mitgliederverwaltung abhakt. Importe und Abgleiche stehen als Sammelpunkt
// zusammen. Server: kenntnis.py (/api/kenntnis mit „Mitglieder lesen“, /api/kenntnis-abhaken mit „Mitglieder ändern“).
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { shortDate, date } from '../format.js';
import { refresh, can, kvName } from '../store.js';
import { pageHead, icon, safe, toast } from '../ui.js';
import { account } from './members.js';

const sicht = { ansicht: 'offen', key: null };
const schluessel = (p) => `${p.id}|${p.sammel || ''}|${p.gesehen_am || ''}|${p.bis}`;
const punkt = (p) => ({ id: p.id, sammel: p.sammel });   // genau dieser Ausschnitt wird abgehakt (kenntnis.abhaken)
const mehrzahl = (n, eins, viele) => `${n} ${n === 1 ? eins : viele}`;

/** Zeitpunkt kurz: „heute 14:20“, „gestern 17:48“, sonst „20.11. 09:12“. */
export function wann(stamp) {
  const d = new Date(stamp), heute = new Date(), tage = Math.round((new Date(heute.getFullYear(), heute.getMonth(), heute.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  const uhr = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  return `${tage === 0 ? 'heute' : tage === 1 ? 'gestern' : shortDate(stamp.slice(0, 10))} ${uhr}`;
}

/** Kurz, was sich geändert hat: „Straße, PLZ und 2 weitere“ bzw. „Neu angelegt“. */
export function wasGeaendert(p) {
  if (p.neu) return 'Neu angelegt';
  const titel = p.felder.map((f) => f.titel);
  return titel.length > 3 ? `${titel.slice(0, 2).join(', ')} und ${titel.length - 2} weitere` : titel.join(', ');
}

export function kenntnis(main) {
  mount(main, html`<div class="page wide"><p class="small muted">Wird geladen …</p></div>`);
  safe(async () => draw(main, await api('/api/kenntnis')));
}

function draw(main, d) {
  if (!d.an) {
    mount(main, html`<div class="page wide">${pageHead('Mitgliederverwaltung', 'Zur Kenntnis')}
      <p class="notice">Die Kenntnisnahme ist ausgeschaltet. Einschalten unter Einstellungen, Reiter „Schalter“.</p></div>`);
    return;
  }
  const schreiben = can('mitglieder', 'schreiben');
  const offen = sicht.ansicht === 'offen';
  const liste = offen ? d.offen : d.abgehakt;
  const einzeln = liste.filter((p) => !p.sammel);
  const sammel = new Map();
  for (const p of liste) if (p.sammel) sammel.set(p.sammel, [...(sammel.get(p.sammel) || []), p]);
  const sel = liste.find((p) => schluessel(p) === sicht.key) || einzeln[0] || liste[0] || null;   // ohne Wahl: der erste gezeigte Punkt

  const zeile = (p) => html`<div class="kn-zeile ${sel && schluessel(p) === schluessel(sel) ? 'is-sel' : ''}">
    <button type="button" class="kn-wahl" data-key="${schluessel(p)}" aria-pressed="${sel && schluessel(p) === schluessel(sel) ? 'true' : 'false'}">
      <span class="${offen ? 'punkt' : 'punkt aus'}" aria-hidden="true"></span>
      <span><strong>${p.name}</strong><span class="was">${wasGeaendert(p)}</span>
        <span class="meta">${p.actor}, ${wann(p.zeit)}</span></span></button>
    ${offen && schreiben ? html`<button type="button" class="kn-haken" data-ab="${schluessel(p)}" aria-label="Änderung bei ${p.name} zur Kenntnis nehmen">${icon('check', 18)}</button>` : ''}
  </div>`;

  mount(main, html`<div class="page wide kn-page">
    ${pageHead('Mitgliederverwaltung', 'Zur Kenntnis', html`<div class="segmented" role="group" aria-label="Ansicht">
      <button type="button" data-ansicht="offen" aria-pressed="${offen ? 'true' : 'false'}">Offen <span class="zahl">${d.offen.length}</span></button>
      <button type="button" data-ansicht="abgehakt" aria-pressed="${offen ? 'false' : 'true'}">Abgehakt <span class="zahl">${d.abgehakt.length}</span></button></div>`)}
    <p class="kn-einleitung">Jede Änderung an den Stammdaten gilt sofort. Hier steht je Mitglied, was sich geändert hat.
      ${schreiben ? 'Du hakst es ab, wenn du es gesehen hast.' : 'Abhaken kann, wer Mitglieder ändern darf.'} Abgehaktes bleibt ${d.frist} Tage sichtbar.</p>
    <div class="blatt"><div class="blatt-reihe kn-reihe">
      <section class="blatt-teil kn-liste" aria-label="Mitglieder mit Änderungen">
        ${liste.length ? '' : html`<p class="muted kn-leer">${offen ? 'Keine offenen Änderungen.' : `In den letzten ${d.frist} Tagen wurde nichts abgehakt.`}</p>`}
        ${einzeln.length && sammel.size ? html`<div class="kn-gruppe"><span><strong>Einzeln geändert</strong>
          <span class="meta">${mehrzahl(einzeln.length, 'Mitglied', 'Mitglieder')}</span></span></div>` : ''}
        ${einzeln.map(zeile)}
        ${[...sammel.entries()].map(([key, punkte]) => html`<div class="kn-gruppe"><span><strong>${punkte[0].sammel_titel}</strong>
            <span class="meta">${punkte[0].actor}, ${date(punkte[0].zeit.slice(0, 10))}, ${mehrzahl(punkte.length, 'Mitglied', 'Mitglieder')}</span></span>
            ${offen && schreiben ? html`<button type="button" class="btn small" data-alle="${key}">${punkte.length === 1 ? 'Abhaken' : `Alle ${punkte.length} abhaken`}</button>` : ''}</div>
          ${punkte.map(zeile)}`)}
      </section>
      <section class="blatt-teil kn-detail" aria-label="Änderungen des gewählten Mitglieds" data-detail>
        ${sel ? detail(sel, offen, schreiben) : html`<div class="kn-fertig">${icon('check', 28)}
          <h2>${offen ? 'Alles zur Kenntnis genommen.' : 'Nichts abgehakt.'}</h2>
          <p class="muted">Neue Änderungen erscheinen hier und als Zahl im Menü.</p></div>`}
      </section>
    </div></div></div>`);

  const abhaken = (punkte, text) => safe(async () => {
    const r = await api('/api/kenntnis-abhaken', { punkte: punkte.map(punkt), bis: d.stand });
    sicht.key = null;
    toast(r.mitglieder ? text : 'Diese Änderungen waren schon abgehakt.');
    await refresh();
  });
  main.querySelectorAll('[data-ansicht]').forEach((b) => (b.onclick = () => { sicht.ansicht = b.dataset.ansicht; sicht.key = null; draw(main, d); }));
  main.querySelectorAll('[data-key]').forEach((b) => (b.onclick = () => {
    sicht.key = b.dataset.key;
    draw(main, d);
    if (window.matchMedia('(max-width: 900px)').matches) main.querySelector('[data-detail]')?.scrollIntoView({ block: 'start' });
  }));
  main.querySelectorAll('[data-ab]').forEach((b) => (b.onclick = () => abhaken(liste.filter((p) => schluessel(p) === b.dataset.ab), 'Zur Kenntnis genommen.')));
  main.querySelectorAll('[data-alle]').forEach((b) => (b.onclick = () => {
    const punkte = sammel.get(b.dataset.alle);
    abhaken(punkte, `${mehrzahl(punkte.length, 'Mitglied', 'Mitglieder')} zur Kenntnis genommen.`);
  }));
  main.querySelector('[data-akte]')?.addEventListener('click', () => account(sel.id));
  if (sel) alleAngaben(main, sel);
}

function detail(p, offen, schreiben) {
  const leer = html`<span class="muted">leer</span>`;
  return html`<header class="kn-kopf">
      <div><h2>${p.name}</h2><p class="muted">Mitglied ${p.number}, ${kvName(p.kv)}${p.ov ? `, ${p.ov}` : ''}</p></div>
      ${offen ? html`<p class="kn-status"><span class="punkt" aria-hidden="true"></span>Wartet auf Kenntnisnahme</p>`
        : html`<p class="kn-status aus">Abgehakt von ${p.gesehen_von}, ${wann(p.gesehen_am)}</p>`}
    </header>
    <p class="kn-herkunft">${p.neu ? 'Neu angelegt' : mehrzahl(p.felder.length, 'Angabe geändert', 'Angaben geändert')}, ${p.herkunft}, ${p.actor}, ${wann(p.zeit)}</p>
    ${p.neu ? html`<p class="notice">Das Mitglied wurde neu angelegt. Alle Angaben stehen unten.</p>` : html`<div class="zeilen kn-felder">
      <div class="zeilen-kopf"><span>Angabe</span><span>Vorher</span><span>Jetzt</span></div>
      ${p.felder.map((f) => html`<div><strong>${f.titel}</strong>
        <span class="kn-alt">${f.alt === '' ? leer : html`<s>${f.alt}</s>`}</span>
        <span class="kn-neu">${f.neu === '' ? leer : f.neu}</span></div>`)}</div>`}
    <div class="kn-aktionen">
      ${offen && schreiben ? html`<button type="button" class="btn primary" data-ab="${schluessel(p)}">${icon('check', 16)} Zur Kenntnis genommen</button>` : ''}
      <button type="button" class="btn" data-akte>Mitglied öffnen</button>
    </div>
    <div class="kn-alle"><h3>Alle Angaben</h3><div data-angaben><p class="small muted">Wird geladen …</p></div></div>`;
}

/** Alle Stammdaten des gewählten Mitglieds nachladen (wie in der Akte); geänderte Angaben sind markiert. */
function alleAngaben(main, p) {
  const key = schluessel(p);
  safe(async () => {
    const a = await api(`/api/member-account?id=${p.id}`);
    const box = main.querySelector('[data-angaben]');
    if (!box || sicht.key !== null && sicht.key !== key) return;   // inzwischen ein anderes Mitglied gewählt
    const neu = new Set(p.felder.map((f) => f.titel));
    mount(box, html`<dl class="akte-all kn-stamm">${a.angaben.map(([k, v]) => html`<dt class="${neu.has(k) ? 'kn-geaendert' : ''}">${k}</dt>
      <dd class="${neu.has(k) ? 'kn-geaendert' : ''}">${v}</dd>`)}</dl>`);
  });
}
