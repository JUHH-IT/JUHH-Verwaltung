// Dialoge rund um Kontoauszüge: Import, Excel-Übernahme, Lernbeispiele.
import { api, fileToBase64 } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date } from '../format.js';
import { store, refresh } from '../store.js';
import { dialog, table, empty, safe, toast } from '../ui.js';

export function importDialog() {
  const el = dialog('Kontoauszug importieren', html`
    <div class="filebox"><label class="field">Excel-Export der Bank (.xls) oder CSV
      <input id="bank-file" type="file" accept=".xls,.csv,.txt,application/vnd.ms-excel,text/csv"></label>
      <p class="small muted">Erkannt werden Valuta bzw. Buchungsdatum, Verwendungszweck und Betrag. Zahlungspartner und IBAN werden aus dem Verwendungszweck abgeleitet, wenn die Bank keine eigene Spalte liefert. Bereits vorhandene Umsätze werden erkannt.</p></div>
    <div id="import-preview"></div>`, { wide: true });
  el.querySelector('#bank-file').onchange = (e) => safe(async () => {
    const file = e.target.files[0];
    if (!file) return;
    const preview = await api('/api/preview-bank', { filename: file.name, content: await fileToBase64(file) });
    const fresh = preview.rows.filter((r) => r.selected).length;
    const vorhanden = preview.rows.filter((r) => r.duplicate).length, aehnlich = preview.rows.filter((r) => !r.selected && !r.error && !r.duplicate).length;
    mount(el.querySelector('#import-preview'), html`<h3>Vorschau · ${preview.rows.length} Zeilen, ${fresh} neu</h3>
      ${preview.before_start ? html`<p class="notice">${preview.before_start} Umsätze vor dem Datenbeginn ${date(preview.data_start)} werden nicht übernommen.</p>` : ''}
      ${vorhanden ? html`<p class="notice">${vorhanden === 1 ? '1 Umsatz ist' : `${vorhanden} Umsätze sind`} schon vorhanden (gleiche Bankreferenz) und ${vorhanden === 1 ? 'wird' : 'werden'} nicht noch einmal übernommen.</p>` : ''}
      ${aehnlich ? html`<p class="notice warn" data-aehnlich><strong>${aehnlich === 1 ? '1 Zeile sieht' : `${aehnlich} Zeilen sehen`} aus wie ein schon vorhandener Umsatz</strong> und ${aehnlich === 1 ? 'ist' : 'sind'} deshalb abgewählt.
        Nur ankreuzen, wenn es wirklich eine weitere Zahlung ist – sonst steht der Umsatz doppelt da.</p>` : ''}
      ${table(['', 'Datum', 'Zahlungspartner / Zweck', { label: 'Betrag', className: 'num' }, 'Prüfung'], preview.rows.map((r) => html`<tr>
        <td><input class="import-check" type="checkbox" value="${r.row_no}" ${r.selected ? 'checked' : ''} ${r.error || r.duplicate ? 'disabled' : ''} aria-label="Zeile ${r.row_no} übernehmen"></td>
        <td class="nowrap">${r.booked ? date(r.booked) : '—'}</td>
        <td><strong style="font-weight:500">${r.name || '—'}</strong><span class="sub">${r.reference}</span></td>
        <td class="num">${r.amount ? eur(r.amount) : '—'}</td>
        <td>${r.error ? html`<span class="badge danger">${r.error}</span>` : r.warning ? html`<span class="badge warn">${r.warning}</span>` : html`<span class="badge ok">Neu</span>`}</td></tr>`))}
      <div class="form-actions"><button type="button" class="btn primary" id="confirm-import">Auswahl importieren</button></div>`);
    const confirm = el.querySelector('#confirm-import');
    const count = () => {   // ohne neue Zeilen gibt es nichts zu importieren
      const n = el.querySelectorAll('.import-check:checked').length;
      confirm.disabled = !n;
      confirm.textContent = n ? `${n} ${n === 1 ? 'Umsatz' : 'Umsätze'} importieren` : 'Keine neuen Umsätze';
    };
    el.querySelectorAll('.import-check').forEach((c) => c.addEventListener('change', count));
    count();
    confirm.onclick = () => safe(async () => {
      const selected = [...el.querySelectorAll('.import-check:checked')].map((c) => Number(c.value));
      const result = await api('/api/import-bank', { token: preview.token, selected });
      el.close();
      store.bank.filter = 'open';
      await refresh();
      toast(`${result.count} Umsätze importiert.`);
    });
  });
}

const STATUS = {
  neu: 'wird verbucht', zuordnen: 'vorhandenen Umsatz verbuchen', parken: 'wird geparkt (1370)',
  offen: 'bleibt offen zum Verbuchen', vorhanden: 'bereits vorhanden', fehlt: 'nicht zugeordnet – wird übersprungen', fehler: 'Fehler – wird übersprungen',
};

export function masterDialog() {
  const el = dialog('Vorkontierte Excel übernehmen', html`
    <div class="filebox"><label class="field">Masterdatei (.xlsx)
      <input id="master-file" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"></label>
      <p class="small muted">Übernommen werden alle Blätter „Umsatz JJJJ“ (Valuta/Buchungsdatum, Verwendungszweck, Haushaltposten, Betrag, Kostenstelle). Im Zeitraum bereits importierter Kontoauszüge werden die vorhandenen Umsätze verbucht (auch bei abweichendem Text oder Datum), außerhalb wird jede Zeile als Bankumsatz angelegt – darum zuerst alle Kontoauszüge importieren. Haushaltsjahr ist das Jahr des Blatts. Vorher wird automatisch gesichert.</p></div>
    <div id="master-preview" class="stack"></div>`, { wide: true });
  el.querySelector('#master-file').onchange = (e) => safe(async () => {
    const file = e.target.files[0];
    if (!file) return;
    const p = await api('/api/preview-master', { filename: file.name, content: await fileToBase64(file) });
    const todo = p.summary.reduce((s, x) => s + x.neu + x.zuordnen + x.parken + x.offen, 0);
    const order = ['fehler', 'fehlt', 'vorhanden', 'offen'];
    const rank = (r) => (order.includes(r.status) ? order.indexOf(r.status) : order.length);
    const issues = p.rows.filter((r) => r.note || order.includes(r.status)).sort((a, b) => rank(a) - rank(b));
    mount(el.querySelector('#master-preview'), html`<h3>Vorschau · ${p.sheets.join(', ')}</h3>
      ${table(['Haushaltsjahr', { label: 'Umsätze', className: 'num' }, { label: 'Summe', className: 'num' }, { label: 'Verbuchen', className: 'num' }, { label: 'Vorhandene verbuchen', className: 'num' }, { label: 'Parken', className: 'num' }, { label: 'Offen', className: 'num' }, { label: 'Schon da', className: 'num' }, { label: 'Nicht gefunden', className: 'num' }, { label: 'Fehler', className: 'num' }],
        p.summary.map((s) => html`<tr><td><strong>${s.year}</strong></td><td class="num">${s.count}</td><td class="num">${eur(s.cents)}</td><td class="num">${s.neu}</td><td class="num">${s.zuordnen}</td><td class="num">${s.parken}</td><td class="num">${s.offen}</td><td class="num">${s.vorhanden}</td><td class="num">${s.fehlt}</td><td class="num">${s.fehler}</td></tr>`))}
      ${p.notes.map((n) => html`<p class="notice">${n}</p>`)}
      ${issues.length ? html`<h3>Auffälligkeiten · ${issues.length}</h3>${table(['Blatt · Zeile', 'Datum', 'Zahlungspartner', { label: 'Betrag', className: 'num' }, 'Posten', 'Status / Hinweis'],
        issues.map((r) => html`<tr><td class="nowrap">${r.sheet} · ${r.row_no}</td><td class="nowrap">${r.booked ? date(r.booked) : '—'}</td><td>${r.name || '—'}</td><td class="num">${r.amount ? eur(r.amount) : '—'}</td><td>${r.code || '—'}</td>
          <td>${STATUS[r.status] || r.status}${r.note ? html`<span class="sub">${r.note}</span>` : ''}</td></tr>`))}` : html`<p class="muted">Keine Auffälligkeiten.</p>`}
      <p class="notice">Regeln: Mitgliedsbeiträge direkt auf 8100 (Ausgänge = Rücklastschriften), KV-Posten 5100–5800 auf das Sammelkonto „KV“, Posten 1370 sowie Zeilen ohne oder mit unbekanntem Posten werden auf das Parkkonto 1370 gebucht. Fehlerhafte, bereits vorhandene und im Kontoauszug nicht eindeutig zugeordnete Zeilen werden übersprungen. Stornierte Umsätze und Zahlungen zu erfassten Rechnungen bleiben für den Buchungsplatz offen.</p>
      <div class="form-actions"><button type="button" class="btn primary" id="confirm-master" ${todo ? '' : 'disabled'}>${todo} Umsätze übernehmen</button></div>`);
    el.querySelector('#confirm-master').onclick = (ev) => safe(async () => {
      ev.target.disabled = true;
      ev.target.textContent = 'Übernahme läuft …';
      try {
        const r = await api('/api/import-master', { token: p.token });
        el.close();
        await refresh();
        const c = r.counts;
        toast(`Übernommen: ${c.neu + c.zuordnen} verbucht, ${c.parken} geparkt, ${c.offen} offen, ${c.fehlt} nicht gefunden. Sicherung: ${r.backup}`);
      } finally {
        ev.target.disabled = false;
      }
    });
  });
}

export function learningDialog() {
  const examples = store.workflow.examples;
  const el = dialog('Lernbeispiele', html`<p>Vorschläge entstehen nur aus Buchungen, die du ausdrücklich als Lernbeispiel gespeichert hast. Zahlungspartner, Richtung, Betrag und Verwendungszweck müssen passen; Ziffern und Referenzen im Zweck dürfen abweichen. Es wird nie automatisch gebucht.</p>
    ${examples.length ? table(['Datum', 'Zahlungspartner / Zweck', { label: 'Betrag', className: 'num' }, ''], examples.map((e) => html`<tr>
      <td class="nowrap">${date(e.booked)}</td><td><strong style="font-weight:500">${e.name || '—'}</strong><span class="sub">${e.reference}</span></td><td class="num">${eur(e.amount)}</td>
      <td><button type="button" class="btn small" data-forget="${e.bank_id}">Nicht mehr vorschlagen</button></td></tr>`))
      : empty('Noch keine Lernbeispiele', 'Beim Verbuchen „Als Lernbeispiel merken“ anhaken.')}`, { wide: true });
  el.querySelectorAll('[data-forget]').forEach((b) => (b.onclick = () => safe(async () => {
    await api('/api/forget', { id: Number(b.dataset.forget) });
    el.close();
    await refresh();
    toast('Lernbeispiel entfernt. Die Buchung bleibt erhalten.');
  })));
}
