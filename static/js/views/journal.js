// Buchungsjournal und Änderungsprotokoll.
import { html, mount } from '../html.js';
import { eur, date, dateTime } from '../format.js';
import { store, kvName, titleLabel } from '../store.js';
import { pageHead, icon, table, empty } from '../ui.js';

export function journal(main) {
  const q = store.journal.search.toLowerCase();
  const entries = store.data.journal.filter((j) => j.year === store.year)
    .filter((j) => !q || `${j.description} ${j.account} ${j.kv} ${kvName(j.kv)} ${j.title} ${titleLabel(j.title)}`.toLowerCase().includes(q));
  const shown = entries.slice(0, store.journal.limit);
  mount(main, html`<div class="page wide">
    ${pageHead(`Haushaltsjahr ${store.year} · ${entries.length} Buchungszeilen`, 'Buchungsjournal', html`<a class="btn" href="/api/journal.csv">CSV exportieren</a>`)}
    <div class="toolbar"><label class="search">${icon('search', 16)}<span class="sr-only">Journal durchsuchen</span><input id="journal-search" type="search" placeholder="Beschreibung, Konto, Titel" value="${store.journal.search}"></label></div>
    <section class="panel" style="padding:6px 12px">${shown.length ? table(['Nr.', 'Datum', 'Beschreibung', 'Konto', 'Bereich', 'Titel', { label: 'Soll', className: 'num' }, { label: 'Haben', className: 'num' }],
      shown.map((j) => html`<tr><td class="muted">#${j.id}</td><td>${date(j.day)}</td><td>${j.description}</td><td>${j.account}</td><td>${kvName(j.kv)}</td>
        <td class="small">${j.title ? titleLabel(j.title) : ''}</td><td class="num">${j.debit ? eur(j.debit) : ''}</td><td class="num">${j.credit ? eur(j.credit) : ''}</td></tr>`))
      : empty('Keine Buchungen', 'Rechnungen und verbuchte Bankumsätze erscheinen hier automatisch.')}
      ${entries.length > shown.length ? html`<div style="padding:12px;text-align:center"><button type="button" class="btn" data-more>Weitere ${Math.min(300, entries.length - shown.length)} anzeigen</button></div>` : ''}</section>
    <section class="panel"><h2 style="margin-bottom:10px">Änderungsprotokoll</h2>
      ${table(['Zeitpunkt', 'Person', 'Aktion'], store.data.audit.map((a) => html`<tr><td class="nowrap">${dateTime(a.time)}</td><td>${a.actor}</td><td>${a.action}</td></tr>`))}</section>
    <p class="small muted">Der CSV-Export enthält alle Jahre, Beträge in Cent. Das Protokoll ist nachvollziehbar, aber keine zertifizierte revisionssichere Archivierung.</p>
  </div>`);
  const input = main.querySelector('#journal-search');
  input.oninput = () => {
    store.journal.search = input.value;
    const pos = input.selectionStart;
    journal(main);
    const again = main.querySelector('#journal-search');
    again.focus();
    again.setSelectionRange(pos, pos);
  };
  main.querySelector('[data-more]')?.addEventListener('click', () => { store.journal.limit += 300; journal(main); });
}
