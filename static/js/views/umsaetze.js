// Kontoumsätze (0.58, REGELN Nr. 40, Wunsch Lennert Gust 23.11.2026): alle Umsätze des Kontoauszugs untereinander wie im
// Onlinebanking – nach Tagen, mit Suche, zugeordnetem Konto, Notiz und Belegen. Verbucht wird in der Maske (views/verbuchen.js,
// dieselbe Kontierung wie bisher); diese Seite zeigt den Bestand und führt dorthin. Die Kasse bleibt bei views/bank.js.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { eur, signed, date, toInput, findIban, todayIso } from '../format.js';
import { store, render, refresh, ledgerRows, kvName, accountName, titleLabel, can } from '../store.js';
import { pageHead, icon, safe, confirmAction } from '../ui.js';
import { importDialog, masterDialog, learningDialog } from './imports.js';
import { reconcileDialog } from './reconcile.js';
import { voidBankDialog, roundSources } from './bank.js';
import { akteFeld, umsatzName } from './umsatz-akte.js';
import { titleFor } from '../account-picker.js';
import { verbuchen, verbuchenKeys } from './verbuchen.js';

const STAENDE = [['alle', 'Alle'], ['open', 'Offen'], ['booked', 'Verbucht'], ['parked', 'Geparkt']];
const sicht = { jahr: null, importiert: null, monat: '', stand: 'alle', suche: '', id: null };

const tag = (iso, optionen) => new Date(iso + 'T12:00:00').toLocaleDateString('de-DE', optionen);
const monatName = (ym) => tag(ym + '-01', { month: 'long' });
const mehrzahl = (n, eins, viele) => `${n} ${n === 1 ? eins : viele}`;

const bereichName = (kv) => (/^5[1-7]00$/.test(kv) ? `Kreisverband ${kvName(kv)}` : kvName(kv));

/** Zur Buchungsmaske wechseln, auf Wunsch mit diesem Umsatz. */
function zurMaske(id = null) {
  Object.assign(store.bank, { modus: 'verbuchen', selected: id, search: '' });
  render();
  window.scrollTo(0, 0);
}

/** Zuordnung eines verbuchten Umsatzes für Liste und Detail: Titel, Zusatz und die gebuchten Zeilen (ohne Bank- und Parkkonto). */
function zuordnung(b, journal) {
  const zeilen = [...roundSources(b)].flatMap((q) => journal.get(q) || []).filter((j) => !['1420', '1370'].includes(j.account));
  const konto = (j) => `${j.account} ${accountName(j.account)}`;
  // der Titel ergibt sich aus dem Konto – genannt wird nur ein abweichend gewählter
  const bereich = (j) => `${bereichName(j.kv)}${j.title && j.title !== titleFor(j.account, j.kv, b.amount > 0 ? 'income' : 'expense') ? `, Titel ${titleLabel(j.title)}` : ''}`;
  const z = b.bezug;
  let titel = '', zusatz = '';
  if (z?.titel) [titel, zusatz] = [z.titel, z.zusatz];
  else if (z && zeilen.every((j) => j.account === '8100')) [titel, zusatz] = [konto(zeilen[0] || { account: '8100' }), zeilen.length === 1 ? `${z.zusatz}, ${kvName(zeilen[0].kv)}` : z.zusatz];
  else if (zeilen.length > 1) [titel, zusatz] = [`${zeilen.length} Teilbeträge`, [...new Set(zeilen.map(konto))].join(', ')];
  else if (zeilen.length) [titel, zusatz] = [konto(zeilen[0]), bereich(zeilen[0])];
  return { titel, zusatz, zeilen, konto, bereich };
}

/** Was in der Spalte „Konto“ steht – je Stand. punkt: offen oder geparkt (es ist etwas zu tun). */
function kontoSpalte(b, z, grund) {
  if (b.state === 'booked') return { titel: z.titel || 'Verbucht', zusatz: z.zusatz };
  if (b.state === 'parked') return { titel: 'Geparkt auf 1370', zusatz: grund || 'ohne Klärungsgrund', punkt: true };
  if (b.state === 'void') return { titel: 'Ungültig', zusatz: b.note || '' };
  return { titel: 'offen', zusatz: b.reversals ? 'frühere Zuordnung storniert' : '', punkt: true };
}

const zeichen = (b) => html`${b.hat_notiz ? html`<span role="img" aria-label="mit Notiz" title="mit Notiz">${icon('note', 16)}</span>` : ''}
  ${b.belege ? html`<span role="img" aria-label="${mehrzahl(b.belege, 'Beleg', 'Belege')}" title="${mehrzahl(b.belege, 'Beleg', 'Belege')}">${icon('clip', 16)}</span>` : ''}`;

function zeile(b, s, gewaehlt) {
  const ein = b.amount > 0, weg = b.state === 'void';
  return html`<button type="button" class="ku-zeile${weg ? ' ungueltig' : ''}" data-id="${b.id}" aria-pressed="${gewaehlt ? 'true' : 'false'}">
    <span class="ku-wer"><strong>${umsatzName(b)}</strong><span>${b.reference || ''}</span></span>
    <span class="ku-konto${s.punkt ? ' offen' : ''}">${s.punkt ? html`<span class="punkt" aria-hidden="true"></span>` : ''}
      <span><span class="t">${s.titel}</span>${s.zusatz ? html`<span class="z">${s.zusatz}</span>` : ''}</span></span>
    <span class="ku-zeichen">${zeichen(b)}</span>
    <span class="ku-betrag${ein && !weg ? ' ein' : ''}">${signed(b.amount)}</span>
  </button>`;
}

function detail(b, z, grund, darf) {
  const iban = findIban(b.reference);
  const s = kontoSpalte(b, z, grund);
  const stand = b.state === 'booked' ? 'Verbucht' : b.state === 'open' ? 'Offen' : s.titel;
  return html`<header class="ku-kopf">
      <p class="ku-stand${s.punkt ? ' offen' : ''}">${s.punkt ? html`<span class="punkt" aria-hidden="true"></span>` : ''}${stand}</p>
      <div class="ku-titel"><h2>${umsatzName(b)}</h2><span class="ku-betrag${b.amount > 0 && b.state !== 'void' ? ' ein' : ''}">${signed(b.amount)}</span></div>
      <p class="ku-datum">${tag(b.booked, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}</p>
    </header>
    <dl class="ku-fakten">
      <dt>Zweck</dt><dd>${b.reference || 'ohne Angabe'}</dd>
      ${iban ? html`<dt>IBAN</dt><dd class="zahl">${iban}</dd>` : ''}
      <dt>Quelle</dt><dd>${b.source || 'unbekannt'}${b.row_no ? `, Zeile ${b.row_no}` : ''}</dd>
    </dl>
    <section class="ku-abschnitt">
      ${b.state === 'booked' ? html`<h3>Gebucht auf</h3>
          ${b.bezug?.titel ? html`<p class="ku-bezug">${b.bezug.titel}<span>${b.bezug.zusatz}</span></p>` : ''}
          ${b.bezug?.art === 'beitrag' ? html`<p class="ku-bezug">${b.bezug.zusatz}</p>` : ''}
          ${z.zeilen.map((j) => html`<div class="ku-gebucht"><span><strong>${z.konto(j)}</strong><span>${z.bereich(j)}</span></span>
            <span class="zahl">${signed(j.credit - j.debit)}</span></div>`)}`
        : b.state === 'parked' ? html`<h3>Geparkt auf 1370</h3><p>${grund || 'Ohne Klärungsgrund'}. Beim Verbuchen wird das Parkkonto aufgelöst.</p>`
        : b.state === 'void' ? html`<h3>Ungültig</h3><p>${b.note || 'Ohne Grund'}. Der Umsatz zählt nicht mehr, weder als offen noch im Monatsbericht oder im Abgleich.</p>`
        : html`<h3>Noch nicht verbucht</h3><p>${b.reversals ? 'Die frühere Zuordnung wurde storniert. Der Umsatz wird neu zugeordnet.' : 'Mit „Jetzt verbuchen“ öffnet sich die Maske mit diesem Umsatz.'}</p>`}
    </section>
    <div class="ku-akte" data-akte></div>
    ${darf ? html`<div class="ku-aktionen">
      ${['open', 'parked'].includes(b.state) ? html`<button type="button" class="btn dark" data-jetzt>Jetzt verbuchen</button>` : ''}
      ${b.state === 'open' ? html`<button type="button" class="btn" data-void>Umsatz ungültig</button>` : ''}
      ${b.state === 'booked' ? html`<button type="button" class="btn" data-reverse>Zuordnung stornieren</button>` : ''}
      ${b.state === 'void' ? html`<button type="button" class="btn" data-unvoid>Wieder gültig</button>` : ''}
    </div>` : ''}`;
}

/** Sichtbare Umsätze nach Suche, Monat und Stand. Mit Suchbegriff gilt das ganze Jahr – sonst fände man nichts aus anderen Monaten. */
function auswahl(jahr, journal, grund) {
  const q = sicht.suche.trim().toLowerCase();
  const bereich = (q ? jahr : jahr.filter((b) => b.booked.startsWith(sicht.monat)))
    .map((b) => { const z = zuordnung(b, journal); return { b, z, s: kontoSpalte(b, z, grund.get(b.id)) }; })
    .filter(({ b, s }) => !q || `${umsatzName(b)} ${b.reference || ''} ${eur(Math.abs(b.amount))} ${toInput(b.amount)} ${date(b.booked)} ${s.titel} ${s.zusatz}`.toLowerCase().includes(q));
  const zahl = { alle: bereich.length };
  for (const [id] of STAENDE.slice(1)) zahl[id] = bereich.filter(({ b }) => b.state === id).length;
  const liste = sicht.stand === 'alle' ? bereich : sicht.stand === 'ohnebeleg' ? bereich.filter(({ b }) => b.ohne_beleg || b.id === sicht.id)   /* der gewählte bleibt stehen, auch wenn er gerade seinen Beleg bekommen hat – sonst landet der zweite Beleg am falschen Umsatz */ : bereich.filter(({ b }) => b.state === sicht.stand);
  return { q, zahl, liste };
}

function zeichne(main) {
  const jahr = ledgerRows('bank');   // neueste zuerst (Reihenfolge des Servers)
  const st = store.data.bank_stand || {};
  if (sicht.jahr !== store.year || sicht.importiert !== st.am) {   // anderes Jahr oder neuer Import: beim jüngsten Monat beginnen
    const heute = todayIso();
    Object.assign(sicht, { jahr: store.year, importiert: st.am, monat: jahr[0]?.booked.slice(0, 7) || (heute.startsWith(String(store.year)) ? heute.slice(0, 7) : `${store.year}-01`) });
  }
  const darf = can('buchhaltung', 'schreiben');
  const journal = new Map();
  for (const j of store.data.journal) journal.set(j.source, [...(journal.get(j.source) || []), j]);
  const geparkt = store.workflow.parked.filter((b) => (b.ledger || 'bank') === 'bank');   // aus allen Jahren, wie in der Maske
  const grund = new Map(geparkt.map((b) => [b.id, b.reason]));
  const zuTun = jahr.filter((b) => b.state === 'open').length + geparkt.length;

  const { q, zahl, liste } = auswahl(jahr, journal, grund);
  if (!liste.some(({ b }) => b.id === sicht.id)) sicht.id = liste[0]?.b.id ?? null;
  const sel = liste.find(({ b }) => b.id === sicht.id);

  const monat = jahr.filter((b) => b.booked.startsWith(sicht.monat) && b.state !== 'void');
  const ein = monat.filter((b) => b.amount > 0), aus = monat.filter((b) => b.amount < 0);
  const offenMonat = monat.filter((b) => ['open', 'parked'].includes(b.state)), parkMonat = offenMonat.filter((b) => b.state === 'parked').length;
  const ohne = monat.filter((b) => b.ohne_beleg).length;
  const im = `im ${monatName(sicht.monat)}`;
  const summe = (rows) => eur(Math.abs(rows.reduce((s2, b) => s2 + b.amount, 0)));
  const nr = Number(sicht.monat.slice(5));
  const tage = new Map();
  for (const x of liste) tage.set(x.b.booked, [...(tage.get(x.b.booked) || []), x]);

  mount(main, html`<div class="page wide ku-page">
    ${pageHead(`Buchhaltung, Haushaltsjahr ${store.year}`, 'Kontoumsätze', html`
      <details class="ku-mehr"><summary class="btn">Mehr ${icon('chevron', 16)}</summary><div class="ku-menu">
        <button type="button" data-learning>Lernbeispiele (${store.workflow.examples.length})</button>
        <button type="button" data-master>Excel übernehmen</button></div></details>
      <button type="button" class="btn" data-reconcile>Abgleich</button>
      <button type="button" class="btn" data-import>Kontoauszug importieren</button>`)}
    <div class="blatt">
      <section class="blatt-teil ku-lage" aria-label="Lage">
        <div><h2 class="blatt-aussage gross">${!jahr.length && !zuTun ? `Für ${store.year} gibt es noch keine Kontoumsätze.` : !zuTun ? 'Alles verbucht.'
          : zuTun === 1 ? '1 Umsatz ist noch nicht verbucht.' : `${zuTun} Umsätze sind noch nicht verbucht.`}</h2>
          <p class="blatt-text">${st.am ? `Zuletzt importiert am ${tag(st.am.slice(0, 10), { weekday: 'long', day: 'numeric', month: 'long' })}.${st.bis ? ` Der Auszug reicht bis zum ${tag(st.bis, { day: 'numeric', month: 'long', year: 'numeric' })}.` : ''}`
            : 'Mit „Kontoauszug importieren“ kommen die Umsätze der Bank hierher.'}</p></div>
        ${zuTun && darf ? html`<button type="button" class="btn dark large" data-verbuchen>Offene verbuchen ${icon('right', 16)}</button>` : ''}
      </section>
      <section class="kz-band" aria-label="Kennzahlen ${monatName(sicht.monat)} ${store.year}">
        <div class="kz"><span class="label">Eingänge ${im}</span><span class="wert">${summe(ein)}</span><span class="note">${mehrzahl(ein.length, 'Umsatz', 'Umsätze')}</span></div>
        <div class="kz"><span class="label">Ausgänge ${im}</span><span class="wert">${summe(aus)}</span><span class="note">${mehrzahl(aus.length, 'Umsatz', 'Umsätze')}</span></div>
        <div class="kz"><span class="label">Noch offen</span><span class="wert">${offenMonat.length}</span>
          <span class="note">${!offenMonat.length ? `${im} ist alles verbucht` : parkMonat ? `davon ${parkMonat} geparkt auf 1370` : 'noch zu verbuchen'}</span></div>
        <div class="kz"><span class="label">Ausgaben ohne Beleg</span><span class="wert">${ohne}</span>
          <span class="note">${!ohne ? 'jede verbuchte Ausgabe hat einen Beleg' : 'Beleg direkt am Umsatz anheften.'}
            ${ohne || sicht.stand === 'ohnebeleg' ? html`<button type="button" class="blatt-link" data-ohne aria-pressed="${sicht.stand === 'ohnebeleg' ? 'true' : 'false'}">${sicht.stand === 'ohnebeleg' ? 'Wieder alle zeigen' : 'Nur diese zeigen'}</button>` : ''}</span></div>
      </section>
      <section class="ku-leiste" aria-label="Suche und Filter">
        <label class="search">${icon('search', 16)}<span class="sr-only">Umsätze durchsuchen</span>
          <input id="ku-suche" type="search" placeholder="Name, Zweck, Betrag oder Konto" value="${sicht.suche}" autocomplete="off"></label>
        ${q ? html`<p class="ku-monat"><strong>Ganzes Jahr ${store.year}</strong></p>` : html`<div class="ku-monat">
          <button type="button" data-monat="-1" aria-label="Vorheriger Monat" ${nr === 1 ? 'disabled' : ''}>${icon('left', 16)}</button>
          <strong>${monatName(sicht.monat)} ${store.year}</strong>
          <button type="button" data-monat="1" aria-label="Nächster Monat" ${nr === 12 ? 'disabled' : ''}>${icon('right', 16)}</button></div>`}
        <div class="segmented" role="group" aria-label="Stand">${STAENDE.map(([id, text]) => html`
          <button type="button" data-stand="${id}" aria-pressed="${sicht.stand === id ? 'true' : 'false'}">${text} <span class="zahl">${zahl[id]}</span></button>`)}</div>
      </section>
      <div class="blatt-reihe ku-reihe">
        <section class="blatt-teil ku-liste" aria-label="Umsätze">
          ${liste.length ? '' : html`<p class="ku-leer">${q ? `Kein Umsatz in ${store.year} passt zur Suche.` : sicht.stand === 'alle' ? `Im ${monatName(sicht.monat)} ${store.year} gibt es keine Umsätze.`
            : `Im ${monatName(sicht.monat)} gibt es keinen Umsatz mit diesem Stand.`}</p>`}
          ${[...tage.entries()].map(([iso, zeilen]) => html`<div class="ku-tag"><h3>${tag(iso, { weekday: 'long', day: 'numeric', month: 'long' })}</h3>
              <span class="zahl">${signed(zeilen.filter(({ b }) => b.state !== 'void').reduce((s2, { b }) => s2 + b.amount, 0))}</span></div>
            ${zeilen.map(({ b, s }) => zeile(b, s, b.id === sicht.id))}`)}
        </section>
        <aside class="blatt-teil ku-detail" aria-label="Gewählter Umsatz" data-detail>
          ${sel ? detail(sel.b, sel.z, grund.get(sel.b.id), darf) : html`<p class="muted">Kein Umsatz gewählt.</p>`}
        </aside>
      </div>
    </div></div>`);

  const neu = () => zeichne(main);
  main.querySelector('[data-import]').onclick = importDialog;
  main.querySelector('[data-master]').onclick = masterDialog;
  main.querySelector('[data-learning]').onclick = learningDialog;
  main.querySelector('[data-reconcile]').onclick = () => safe(reconcileDialog);
  const mehr = main.querySelector('.ku-mehr');
  mehr.onkeydown = (e) => { if (e.key === 'Escape' && mehr.open) { e.stopPropagation(); mehr.open = false; mehr.querySelector('summary').focus(); } };
  mehr.querySelectorAll('.ku-menu button').forEach((k) => k.addEventListener('click', () => { mehr.open = false; }));
  main.querySelector('[data-verbuchen]')?.addEventListener('click', () => zurMaske());
  main.querySelector('[data-ohne]')?.addEventListener('click', () => {
    sicht.stand = sicht.stand === 'ohnebeleg' ? 'alle' : 'ohnebeleg';
    if (sicht.stand === 'ohnebeleg') sicht.id = null;   // beim Einschalten zählt nur die Regel; gehalten wird erst, was im Filter gewählt wurde
    neu();
  });
  main.querySelectorAll('[data-stand]').forEach((k) => (k.onclick = () => { sicht.stand = k.dataset.stand; neu(); }));
  main.querySelectorAll('[data-monat]').forEach((k) => (k.onclick = () => { sicht.monat = `${store.year}-${String(nr + Number(k.dataset.monat)).padStart(2, '0')}`; neu(); }));
  const suche = main.querySelector('#ku-suche');
  suche.oninput = () => {
    sicht.suche = suche.value;
    const pos = suche.selectionStart;
    neu();
    const wieder = main.querySelector('#ku-suche');
    wieder.focus();
    wieder.setSelectionRange(pos, pos);
  };
  main.querySelectorAll('.ku-zeile').forEach((k) => (k.onclick = () => {
    sicht.id = Number(k.dataset.id);
    const oben = window.scrollY;
    neu();
    window.scrollTo(0, oben);
    // Handy/Tablet: Liste und Umsatz stehen untereinander – nach der Wahl zum Umsatz springen
    if (window.matchMedia('(max-width: 900px)').matches) main.querySelector('[data-detail]').scrollIntoView({ block: 'start' });
    else main.querySelector(`.ku-zeile[data-id="${sicht.id}"]`)?.focus({ preventScroll: true });
  }));
  if (!sel) return;

  const b = sel.b;
  akteFeld(main.querySelector('[data-akte]'), b, {
    geaendert: (neues) => {   // Zeichen in der Liste stimmen sofort, die Zahlen nach dem Neuladen
      Object.assign(b, neues);
      const platz = main.querySelector(`.ku-zeile[data-id="${b.id}"] .ku-zeichen`);
      if (platz) mount(platz, zeichen(b));
    },
    nachBeleg: refresh,
  });
  main.querySelector('[data-jetzt]')?.addEventListener('click', () => zurMaske(b.id));
  main.querySelector('[data-void]')?.addEventListener('click', () => voidBankDialog(b));
  main.querySelector('[data-unvoid]')?.addEventListener('click', () => confirmAction('Umsatz wieder gültig',
    'Der Umsatz ist danach wieder offen und kann verbucht werden.', () => api('/api/umsatz-gueltig', { id: b.id }),
    { label: 'Wieder gültig', done: 'Umsatz ist wieder offen.' }));
  main.querySelector('[data-reverse]')?.addEventListener('click', () => confirmAction('Zuordnung stornieren',
    'Die Buchung wird gegengebucht, eine zugeordnete Rechnung wieder geöffnet. Eine zugeordnete Eingangsrechnung wird als „Zahlung storniert – prüfen“ gesperrt, bis die Abbuchung ihr wieder zugeordnet ist – so wird sie nicht ein zweites Mal überwiesen. Der Umsatz ist danach wieder offen und kann sofort neu zugeordnet werden.',
    async () => {
      await api('/api/reverse-bank', { id: b.id });
      Object.assign(store.bank, { modus: 'verbuchen', selected: b.id, search: '' });   // gleich weiter zum Neuzuordnen
    }, { label: 'Stornieren', done: 'Zuordnung storniert. Der Umsatz ist wieder offen.' }));
}

// Menü „Mehr“ schließt beim Klick daneben (ein Hörer für die ganze Laufzeit)
document.addEventListener('click', (e) => {
  const offen = document.querySelector('.ku-mehr[open]');
  if (offen && !offen.contains(e.target)) offen.open = false;
});

/** Seite „Kontoumsätze“: die Liste oder, im Arbeitsmodus, die Buchungsmaske. */
export function kontoumsaetze(main) {
  if (store.bank.modus === 'verbuchen') verbuchen(main);
  else zeichne(main);
}

/** Tastenkürzel (aus main.js, nur außerhalb von Eingabefeldern): in der Maske wie bisher, in der Liste J/K und „/“. */
export function umsatzKeys(e) {
  if (store.bank.modus === 'verbuchen') return verbuchenKeys(e);
  const key = e.key.toLowerCase();
  if (e.key === '/') { e.preventDefault(); document.getElementById('ku-suche')?.focus(); return; }
  const schritt = key === 'j' ? 1 : key === 'k' ? -1 : 0;   // Pfeiltasten bleiben beim Blättern der Seite
  if (!schritt) return;
  const zeilen = [...document.querySelectorAll('.ku-zeile')];
  const ziel = zeilen[Math.min(zeilen.length - 1, Math.max(0, zeilen.findIndex((k) => k.getAttribute('aria-pressed') === 'true') + schritt))];
  if (!ziel) return;
  e.preventDefault();
  ziel.click();
  document.querySelector(`.ku-zeile[data-id="${ziel.dataset.id}"]`)?.scrollIntoView({ block: 'nearest' });
}
