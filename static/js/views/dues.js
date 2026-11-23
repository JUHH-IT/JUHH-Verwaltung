// Beitragseingänge (Buchhaltung, ab 0.31 eigene Seite – vorher Dialog „Beiträge zuordnen“ unter Mitglieder):
// Zahlungen auf 8100 aus dem Kontoauszug einem Mitglied zuordnen, SEPA-Datei einlesen, Unklares als Klärfall an die
// Mitgliederverwaltung melden (klaerfaelle.py). Kennzahlen der Mitgliederbuchhaltung (Soll, gezahlt, Rückstände) stehen hier,
// nicht mehr auf der Mitgliederseite.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date } from '../format.js';
import { store, refresh, kvName, go } from '../store.js';
import { pageHead, icon, table, empty, safe, toast, badge, confirmAction } from '../ui.js';
import { pickMembers, sortName, membersLabel, membersPayload } from '../member-picker.js';
import { sepaFileDialog } from './sepa-datei.js';

let all = false;       // auch bereits zugeordnete Zahlungen zeigen (zum Ändern)
let scope = null;      // Jahr der Zahlungen: null = noch nicht gewählt, '' = alle Jahre
let seq = 0;

export function dues(main) {
  const b = store.data.dues_book, rec = b.reconciliation, c = store.data.cases;
  // Vorgabe: gewähltes Haushaltsjahr; gibt es dort nichts Offenes, aber in anderen Jahren, alle Jahre (z. B. im Januar das Vorjahr)
  if (scope === null || (scope && scope !== String(store.year))) scope = store.data.dues_open || !store.data.dues_open_all ? String(store.year) : '';
  mount(main, html`<div class="page wide">
    ${pageHead(`Buchhaltung · Mitgliedsbeiträge ${store.year}`, 'Beitragseingänge', html`
      <button type="button" class="btn primary" data-sepa title="Lastschrift- oder Bankdatei mit Einzelzahlungen: Sammelbuchung auf Mitglieder aufteilen">${icon('upload', 16)} SEPA-Datei einlesen</button>`)}
    <section class="cards" aria-label="Mitgliederbuchhaltung ${store.year}">
      <div class="card"><span class="label">Beiträge ${store.year} (Soll)</span><span class="value">${eur(b.soll)}</span>
        <span class="note">${b.issues ? html`<span class="over">${b.issues} ${b.issues === 1 ? 'Mitglied' : 'Mitglieder'}: Soll ${store.year} klären</span>` : 'laut Mitgliederliste, Staffel, Rechnung oder Festlegung'}</span></div>
      <div class="card"><span class="label">Gezahlt ${store.year}</span><span class="value">${eur(b.paid)}</span>
        <span class="note">zugeordnete Zahlungen des Beitragsjahres</span></div>
      <div class="card"><span class="label">Zahlungen ohne Mitglied</span><span class="value ${store.data.dues_open_all ? 'over' : ''}">${store.data.dues_open_all}</span>
        <span class="note">${rec.total ? `${store.year}: ${eur(rec.assigned)} von ${eur(rec.total)} auf 8100 zugeordnet` : `keine Zahlungen auf 8100 in ${store.year}`}${
          store.data.dues_open_all > store.data.dues_open ? ` · ${store.data.dues_open_all - store.data.dues_open} offen aus anderen Jahren` : ''}</span></div>
      <button type="button" class="card card-link" data-go-cases><span class="label">Klärfälle</span>
        <span class="value ${c.unassigned ? 'over' : ''}">${c.active}</span>
        <span class="note">bei der Mitgliederverwaltung${c.unassigned ? ` · ${c.unassigned} ohne Zuordnung, Kreisverbandsanteil wartet` : c.active ? ' · alle schon gebucht, zur Prüfung' : ' · nichts offen'}</span></button>
    </section>
    ${store.data.dues_open_all || c.unassigned ? html`<p class="notice warn">Jede Beitragszahlung muss einem Mitglied zugeordnet sein – erst dann sind Mitgliederkonten,
      Rückstände und der Beitragsanteil der Kreisverbände vollständig. Der KV-Budgetabschluss eines Jahres ist erst danach möglich. Ist unklar, zu wem eine
      Zahlung gehört, als Klärfall an die Mitgliederverwaltung melden.</p>` : ''}
    <section class="panel" data-dues><p class="small muted">Wird geladen …</p></section>
    <p class="small muted">Zahlen Eltern oder Verwandte, das Mitglied wählen, für das gezahlt wurde (Satzung § 49 Abs. 3). Steht die Person nicht (mehr) in der
      Mitgliederliste und ist der Kreisverband bekannt, in der Suche „Person nicht in der Mitgliederliste“ wählen – die Zahlung zählt dann im Haushalt und für
      den Anteil des Kreisverbands und erscheint zur Prüfung unter „Klärfälle“. Offene Beiträge je Mitglied (OP-Listen) stehen unter
      <button type="button" class="link" data-go-op>Offene Posten</button>.</p>
  </div>`);
  main.querySelector('[data-sepa]').onclick = () => sepaFileDialog();
  main.querySelector('[data-go-cases]').onclick = () => go('cases');
  main.querySelector('[data-go-op]').onclick = () => go('openitems', { openSide: 'dues' });
  const box = main.querySelector('[data-dues]');

  const assign = (item, members) => safe(async () => {
    await api('/api/beitrag-zuordnen', { entry_id: item.id, members: membersPayload(members, true) });
    await refresh();
    toast(members.some((m) => m.case) ? 'Zugeordnet – der unklare Teil liegt als Klärfall bei der Mitgliederverwaltung.' : 'Beitrag zugeordnet.');
  });

  async function load() {
    const mine = ++seq;
    const items = await api(`/api/beitraege-offen?year=${scope}${all ? '&all=1' : ''}`);
    if (mine !== seq || !box.isConnected) return;   // inzwischen neu gezeichnet
    const name = (id) => store.data.members.find((m) => m.id === id);
    // wie beitraege.unique_suggestion: ein Mitglied vorn, Nachname in der Zahlung
    const unique = (x) => { const s = x.suggestions || []; return s.length && s[0].name && (s.length === 1 || s[1].score < s[0].score); };
    const proposals = all ? 0 : items.filter((x) => !x.members.length && unique(x)).length;
    const openCount = items.filter((x) => x.open && !(x.returned || []).length).length;   // „kein Mitgliedsbeitrag“ bleibt bei der Buchhaltung (klaerfaelle.report_open)
    mount(box, html`<div class="toolbar"><label class="field inline">Jahr<select data-scope><option value="${store.year}" ${scope ? 'selected' : ''}>${store.year}</option>
          <option value="" ${scope ? '' : 'selected'}>alle Jahre</option></select></label>
        <label class="check"><input type="checkbox" data-all ${all ? 'checked' : ''}> Auch zugeordnete und gemeldete Zahlungen zeigen (zum Ändern)</label>
        ${proposals ? html`<button type="button" class="btn primary" data-take-all>${proposals} eindeutige Vorschläge übernehmen</button>` : ''}
        ${openCount && !all ? html`<button type="button" class="btn" data-report-all title="Alle Zahlungen dieser Liste, zu denen sich kein Mitglied findet, an die Mitgliederverwaltung geben">
          ${openCount} als Klärfälle melden</button>` : ''}</div>
      ${items.length ? table(['Datum', 'Zahlung', { label: 'Betrag', className: 'num' }, 'Mitglied', ''], items.map((x, i) => {
        const hint = (x.suggestions || []).map((s) => name(s.member_id)).filter(Boolean)[0];
        const learned = x.suggestions?.[0]?.learned;   // gleicher Zahler wie bei einer früheren Zuordnung
        const whole = x.open === x.amount;              // noch gar nichts zugeordnet oder gemeldet
        return html`<tr><td class="nowrap">${date(x.day)}</td>
          <td><strong style="font-weight:500">${x.payer || '—'}</strong><span class="sub">${x.reference}</span></td>
          <td class="num ${x.amount < 0 ? 'over' : ''}">${eur(x.amount)}${x.amount < 0 ? html`<span class="sub">Rückzahlung / Rücklastschrift</span>` : ''}</td>
          <td>${x.members.length ? membersLabel(x.members) : ''}
            ${x.cases.map((k) => html`<span class="sub">${badge('Klärfall', 'warn')} ${k.number} · ${k.name} · ${eur(k.amount)}${k.status === 'rueckfrage' ? ' · Rückfrage läuft' : ''}</span>`)}
            ${x.open && !whole ? html`<span class="sub over">noch offen: ${eur(x.open)}</span>` : ''}
            ${(x.returned || []).map((r) => html`<span class="sub over">Mitgliederverwaltung: kein Mitgliedsbeitrag${r.note ? ` – ${r.note}` : ''}. Buchung im Buchungsplatz stornieren und richtig verbuchen.</span>`)}
            ${whole ? (hint ? html`<span class="muted">Vorschlag:</span> ${sortName(hint)} (${hint.number}, ${kvName(hint.kv)})${learned ? html`<span class="sub">gleicher Zahler wie bei einer früheren Zuordnung</span>` : ''}` : html`<span class="muted">—</span>`) : ''}</td>
          <td class="nowrap">${hint && whole ? html`<button type="button" class="btn small primary" data-take="${i}">Übernehmen</button> ` : ''}
            ${x.in_case === x.amount ? html`<button type="button" class="btn small" data-go-case="${x.cases[0]?.id ?? ''}">Zum Klärfall</button>`
              : html`<button type="button" class="btn small" data-pick="${i}">${x.members.length ? 'Ändern' : 'Mitglied suchen'}</button>`}
            ${x.open ? html` <button type="button" class="btn small" data-report="${i}" title="Unklar, zu wem die Zahlung gehört: an die Mitgliederverwaltung geben">Klärfall</button>` : ''}</td></tr>`;
      })) : empty(all ? 'Keine Zahlungen auf 8100' : 'Alles zugeordnet', `${scope ? `Für ${scope}` : 'Insgesamt'} gibt es ${all ? 'keine Beitragszahlungen aus dem Kontoauszug' : 'keine Beitragszahlungen ohne Mitglied'}.`)}`);
    box.querySelector('[data-all]').onchange = (e) => { all = e.target.checked; safe(load); };
    box.querySelector('[data-scope]').onchange = (e) => { scope = e.target.value; safe(load); };
    box.querySelector('[data-take-all]')?.addEventListener('click', (e) => safe(async () => {
      if (e.target.disabled) return;
      e.target.disabled = true;
      try {
        const result = await api('/api/beitraege-vorschlaege', { year: scope ? Number(scope) : null });
        await refresh();
        toast(`${result.count} Zahlungen nach Vorschlag zugeordnet – einzeln prüfbar unter „Auch zugeordnete … zeigen“.`);
      } finally { e.target.disabled = false; }
    }));
    box.querySelector('[data-report-all]')?.addEventListener('click', () => confirmAction('Als Klärfälle melden',
      `${openCount} ${openCount === 1 ? 'Zahlung' : 'Zahlungen'} ohne Mitglied ${scope ? `aus ${scope}` : 'aus allen Jahren'} gehen als Klärfälle an die Mitgliederverwaltung. `
      + 'Sie bleiben auf 8100 ohne Mitglied, bis sie dort geklärt sind. Vorher am besten die eindeutigen Vorschläge übernehmen.',
      () => api('/api/klaerfall-melden', { all: true, year: scope ? Number(scope) : null }), { label: 'Melden', done: 'Klärfälle gemeldet – zu finden unter Mitgliederverwaltung → Klärfälle.' }));
    box.querySelectorAll('[data-take]').forEach((btn) => (btn.onclick = () => {
      const x = items[Number(btn.dataset.take)];
      assign(x, [{ member_id: x.suggestions[0].member_id, amount: x.amount }]);
    }));
    box.querySelectorAll('[data-go-case]').forEach((btn) => (btn.onclick = () => go('cases', { caseId: Number(btn.dataset.goCase) || null })));
    box.querySelectorAll('[data-report]').forEach((btn) => (btn.onclick = () => safe(async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await api('/api/klaerfall-melden', { entry_id: items[Number(btn.dataset.report)].id });
        await refresh();
        toast('Als Klärfall an die Mitgliederverwaltung gemeldet.');
      } finally { btn.disabled = false; }
    })));
    box.querySelectorAll('[data-pick]').forEach((btn) => (btn.onclick = () => {
      const x = items[Number(btn.dataset.pick)];   // negativ = Rückzahlung bzw. Rücklastschrift; auch Personen außerhalb der Mitgliederliste
      const rest = x.amount - x.in_case;           // ein gemeldeter Klärfall bleibt, verteilt wird der Rest
      return pickMembers({ amount: rest, selected: x.members, hint: `${x.payer} ${x.reference}`, payer: x.payer, entryKv: x.kv !== 'LV' ? x.kv : '',
        title: `${x.amount < 0 ? 'Rückzahlung' : 'Mitgliedsbeitrag'} ${eur(rest)} zuordnen`, onDone: (list) => assign(x, list) });
    }));
  }
  safe(load);
}
