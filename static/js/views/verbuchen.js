// Verbuchen (0.58, REGELN Nr. 40): Arbeitsmodus der Kontoumsätze für offene und geparkte Umsätze. Links die Arbeitsliste, rechts
// die bestehende Buchungsmaske (views/booking.js: Vorschläge, Teilbeträge, Lernen), dazu Parken und „ungültig“ aus views/bank.js
// sowie Notiz und Belege (views/umsatz-akte.js). Gebucht wird wie bisher allein über /api/book und /api/park.
import { html, mount } from '../html.js';
import { signed, shortDate, findIban } from '../format.js';
import { store, render, refresh, ledgerRows, eligibleInvoices } from '../store.js';
import { icon } from '../ui.js';
import { bookingEditor } from './booking.js';
import { parkForm, reversedNote, voidBankDialog } from './bank.js';
import { akteFeld, umsatzName } from './umsatz-akte.js';

let editor = null;
const tag = (iso, optionen) => new Date(iso + 'T12:00:00').toLocaleDateString('de-DE', optionen);

/** Arbeitsliste: offene Umsätze des Jahres (älteste zuerst), danach die geparkten aus allen Jahren – gefiltert nach der Suche. */
function arbeit() {
  const q = store.bank.search.trim().toLowerCase();
  const passt = (b) => !q || `${umsatzName(b)} ${b.reference || ''} ${signed(b.amount)} ${tag(b.booked)} ${b.reason || ''}`.toLowerCase().includes(q);
  const offen = ledgerRows('bank').filter((b) => b.state === 'open').sort((a, b) => a.booked.localeCompare(b.booked) || a.id - b.id);
  const geparkt = store.workflow.parked.filter((b) => (b.ledger || 'bank') === 'bank');
  return { offen, geparkt, zeigen: [...offen, ...geparkt].filter(passt) };
}

function zurListe() {
  store.bank.modus = 'liste';
  render();
  window.scrollTo(0, 0);
}

/** Nach dem Verbuchen oder Parken den nächsten Umsatz der Liste wählen. */
async function weiter(b) {
  const { zeigen } = arbeit();
  const i = zeigen.findIndex((x) => x.id === b.id);
  store.bank.selected = (zeigen[i + 1] || zeigen[i - 1])?.id ?? null;
  await refresh();
}

function gehe(schritt) {
  const { zeigen } = arbeit();
  if (!zeigen.length) return;
  const i = Math.max(0, zeigen.findIndex((b) => b.id === store.bank.selected));
  store.bank.selected = zeigen[Math.min(zeigen.length - 1, Math.max(0, i + schritt))].id;
  const main = document.getElementById('main');
  verbuchen(main);
  const zeile = main.querySelector(`.vb-zeile[data-id="${store.bank.selected}"]`);
  zeile?.focus();
  zeile?.scrollIntoView({ block: 'nearest' });
}

function zeile(b, gewaehlt) {
  const hinweis = b.state === 'parked' ? b.reason || 'ohne Klärungsgrund' : b.reversals ? 'storniert, neu zuordnen'
    : eligibleInvoices(b).some((i) => `${b.reference} ${b.name}`.includes(i.number)) ? 'Rechnung erkannt' : '';
  return html`<button type="button" class="vb-zeile" data-id="${b.id}" ${gewaehlt ? html`aria-current="true"` : ''}>
    <span><strong>${umsatzName(b)}</strong><span class="betrag${b.amount > 0 ? ' ein' : ''}">${signed(b.amount)}</span></span>
    <span class="meta">${shortDate(b.booked)}${hinweis ? `, ${hinweis}` : ''}</span></button>`;
}

function maske(b) {
  const iban = findIban(b.reference);
  return html`<header class="vb-kopf">
      <div><p class="vb-datum">${tag(b.booked, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</p>
        <h2>${umsatzName(b)}</h2>
        <p class="vb-zweck">${b.reference || 'ohne Verwendungszweck'}</p>
        <p class="vb-quelle">${iban ? html`<span class="zahl">${iban}</span>, ` : ''}${b.source || 'Quelle unbekannt'}${b.row_no ? `, Zeile ${b.row_no}` : ''}</p></div>
      <p class="vb-betrag${b.amount > 0 ? ' ein' : ''}">${signed(b.amount)}</p>
    </header>
    ${b.state === 'parked' ? html`<p class="vb-hinweis"><span class="punkt" aria-hidden="true"></span><span><strong>Geparkt auf 1370:</strong> ${b.reason || 'ohne Klärungsgrund'}.
      Beim Verbuchen wird das Parkkonto aufgelöst, die Bank wird nicht erneut bebucht.</span></p>` : ''}
    ${reversedNote(b)}
    <div id="editor"></div><div id="park-box" hidden></div>
    ${b.state === 'open' ? html`<div class="vb-ungueltig"><p class="small muted">Doppelt importiert? Der Umsatz bleibt mit Grund sichtbar, zählt aber nicht mehr. Gelöscht wird nichts.</p>
      <button type="button" class="btn ghost" data-void>Umsatz ungültig</button></div>` : ''}`;
}

export function verbuchen(main) {
  const oben = main.querySelector('.vb-liste')?.scrollTop || 0;
  const { offen, geparkt, zeigen } = arbeit();
  if (store.bank.filter === 'parked' && !store.bank.selected) store.bank.selected = geparkt[0]?.id ?? null;   // Aufgabe „Klärungsfälle im Parkkonto“ vom Start
  store.bank.filter = 'open';
  if (!zeigen.some((b) => b.id === store.bank.selected)) store.bank.selected = zeigen[0]?.id ?? null;
  const b = zeigen.find((x) => x.id === store.bank.selected);
  const jahr = ledgerRows('bank').filter((x) => x.state !== 'void');
  const fertig = jahr.filter((x) => x.state === 'booked').length;
  const monate = [...new Set([...offen, ...geparkt].map((x) => x.booked.slice(0, 7)))];
  const nr = b ? (b.state === 'parked' ? geparkt : offen).findIndex((x) => x.id === b.id) + 1 : 0;
  const gruppe = (titel, rows) => (rows.length ? html`<h3>${titel}</h3>${rows.map((x) => zeile(x, x.id === store.bank.selected))}` : '');

  mount(main, html`<div class="page wide vb-page">
    <header class="pagehead"><div><button type="button" class="vb-zurueck" data-zurueck>${icon('left', 16)} Kontoumsätze</button><h1>Verbuchen</h1></div>
      ${b ? html`<p class="vb-tasten">Enter verbucht, J und K wechseln den Umsatz${b.state === 'open' ? ', P parkt' : ''}</p>` : ''}</header>
    <div class="blatt">${!offen.length && !geparkt.length ? html`<section class="blatt-teil vb-fertig">
        <h2 class="blatt-aussage gross">Alles verbucht.</h2>
        <p class="blatt-text">Für ${store.year} ist kein Kontoumsatz offen und keiner geparkt. Neue Umsätze kommen über „Kontoauszug importieren“.</p>
        <p><button type="button" class="btn dark" data-zurueck>Zu den Kontoumsätzen</button></p></section>` : html`
      <section class="blatt-teil vb-stand" aria-label="Fortschritt">
        <h2>${!b ? 'Kein Umsatz passt zur Suche' : b.state === 'parked' ? `Geparkter Umsatz ${nr} von ${geparkt.length}` : `Umsatz ${nr} von ${offen.length} offenen`}</h2>
        <div class="strich" role="img" aria-label="${fertig} von ${jahr.length} Umsätzen in ${store.year} sind verbucht"><span style="width:${jahr.length ? Math.round((fertig / jahr.length) * 100) : 0}%"></span></div>
        <p>${monate.length === 1 ? `Danach ist der ${tag(monate[0] + '-01', { month: 'long' })} vollständig verbucht.` : `${fertig} von ${jahr.length} Umsätzen in ${store.year} sind verbucht.`}</p>
      </section>
      <div class="blatt-reihe vb-reihe">
        <section class="blatt-teil vb-liste" aria-label="Offene und geparkte Umsätze">
          <label class="search">${icon('search', 16)}<span class="sr-only">Offene Umsätze durchsuchen</span>
            <input id="bank-search" type="search" placeholder="Name, Zweck, Betrag (Taste /)" value="${store.bank.search}" autocomplete="off"></label>
          ${gruppe('Offen', zeigen.filter((x) => x.state === 'open'))}
          ${gruppe('Geparkt auf 1370', zeigen.filter((x) => x.state === 'parked'))}
        </section>
        <section class="blatt-teil vb-maske" aria-label="Ausgewählter Umsatz" data-maske>${b ? maske(b) : html`<p class="muted">Suchbegriff anpassen, dann erscheinen die offenen Umsätze wieder.</p>`}</section>
      </div>`}
    </div></div>`);

  main.querySelectorAll('[data-zurueck]').forEach((k) => (k.onclick = zurListe));
  const liste = main.querySelector('.vb-liste');
  if (liste) liste.scrollTop = oben;
  const suche = main.querySelector('#bank-search');
  if (suche) suche.oninput = () => {
    store.bank.search = suche.value;
    const pos = suche.selectionStart;
    verbuchen(main);
    const wieder = main.querySelector('#bank-search');
    wieder.focus();
    wieder.setSelectionRange(pos, pos);
  };
  main.querySelectorAll('.vb-zeile').forEach((k) => (k.onclick = () => {
    store.bank.selected = Number(k.dataset.id);
    verbuchen(main);
    // Handy/Tablet: Liste und Maske stehen untereinander – nach der Wahl zur Maske springen
    if (window.matchMedia('(max-width: 900px)').matches) main.querySelector('[data-maske]').scrollIntoView({ block: 'start' });
  }));

  editor = null;
  if (!b) return;
  editor = bookingEditor(main.querySelector('#editor'), b, {
    onBooked: () => weiter(b),
    onPark: () => parkForm(b, main.querySelector('#park-box'), weiter),
    zusatz: html`<div class="vb-akte" data-akte></div>`,
  });
  // in der Maske nie neu laden: ein Beleg oder eine Notiz darf die begonnene Kontierung nicht zurücksetzen
  akteFeld(main.querySelector('[data-akte]'), b, { geaendert: (neues) => Object.assign(b, neues) });
  main.querySelector('[data-void]')?.addEventListener('click', () => voidBankDialog(b));
}

/** Tastenkürzel der Maske (aus main.js über umsatzKeys, nur außerhalb von Eingabefeldern). */
export function verbuchenKeys(e) {
  const key = e.key.toLowerCase();
  if (key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); gehe(1); }
  else if (key === 'k' || e.key === 'ArrowUp') { e.preventDefault(); gehe(-1); }
  else if (e.key === '/') { e.preventDefault(); document.getElementById('bank-search')?.focus(); }
  else if (key === 'p') { const park = document.getElementById('park'); if (park) { e.preventDefault(); park.click(); } }
  else if (e.key === 'Enter' && editor) {
    const zeileEl = e.target.closest('.vb-zeile');
    // Enter auf einem anderen Umsatz in der Liste wählt ihn aus – gebucht wird nur der gerade angezeigte
    if (zeileEl && zeileEl.getAttribute('aria-current') !== 'true') { e.preventDefault(); zeileEl.click(); return; }
    if (!e.target.closest('button, a') || zeileEl) { e.preventDefault(); editor.submit(); }
  }
}
