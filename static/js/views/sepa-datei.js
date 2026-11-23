// SEPA-Datei einlesen (0.29, Wunsch Lennert Gust 10.11.2026): Lastschriftdatei (pain.008) oder Bankdatei mit Einzelposten
// (camt.052/053/054, auch ZIP) lesen und eine Sammelgutschrift aus dem Kontoauszug auf die einzelnen Mitglieder aufteilen.
// Server: sepadatei.py (Vorschau ohne Speichern, Zuordnung nach Rechnungsnummer, Mandat, Mitgliedsnummer, IBAN, Name; Aufteilen
// verbucht einen offenen Umsatz bzw. ordnet eine schon gebuchte 8100-Zeile zu; die Datei bleibt als Beleg).
import { api, fileToBase64 } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date } from '../format.js';
import { store, refresh, kvName } from '../store.js';
import { dialog, safe, toast, badge, icon, table } from '../ui.js';
import { pickMembers, sortName, externalLabel, caseLabel } from '../member-picker.js';

const memberById = (id) => store.data.members.find((m) => m.id === Number(id));
const invoiceById = (id) => store.data.invoices.find((i) => i.id === Number(id));

/** Ziel einer Einzelzahlung als Text: Mitglied, offene Beitragsrechnung oder Person außerhalb der Mitgliederliste. */
function targetLabel(t) {
  if (!t || (!t.member_id && !t.invoice_id && !t.external && !t.case)) return null;
  if (t.case) return caseLabel(t.case);
  if (t.external) return externalLabel(t.external);
  const m = memberById(t.member_id) || (t.invoice_id ? memberById(invoiceById(t.invoice_id)?.member_id) : null);
  const who = m ? `${sortName(m)} (${m.number}, ${kvName(m.kv)})` : t.invoice ? `Rechnung ${t.invoice}` : `Mitglied ${t.member_id}`;
  return t.invoice ? `${who} · ${t.invoice_id ? 'Rechnung' : 'zuerst auf'} ${t.invoice}` : who;
}

/** Hinweis bei einem gewählten Mitglied mit offener Beitragsrechnung bis zum Zahlungsjahr: die Zahlung geht zuerst dorthin (wie sepadatei._current). */
function duesNote(choice, year) {
  if (!choice?.member_id) return '';
  const open = store.data.invoices.filter((i) => i.member_id === Number(choice.member_id) && i.kind === 'dues' && i.status === 'issued' && i.total > i.paid
    && (!i.year || i.year <= year));
  return open.length ? ` · geht zuerst auf ${open.map((i) => i.number).join(', ')}` : '';
}

export function sepaFileDialog() {
  const el = dialog('SEPA-Datei einlesen', html`
    <div class="filebox"><label class="field">Lastschriftdatei (pain.008) oder Bankdatei mit Einzelposten (camt.052/053/054, auch als ZIP)
      <input id="sepa-file" type="file" accept=".xml,.zip"></label>
      <p class="small muted">Die Bank bucht eine Sammellastschrift bzw. Sammelgutschrift als <em>eine</em> Zeile im Kontoauszug. Die Datei enthält die
        Einzelzahlungen: Das Programm ordnet sie den Mitgliedern zu (Rechnungsnummer, Mandatsreferenz, Mitgliedsnummer, IBAN des Mandats, Name) und
        teilt die Sammelbuchung auf – offene Umsätze werden dabei verbucht, schon auf 8100 gebuchte Zeilen nur zugeordnet. Zahlungen von Personen,
        die nicht mehr in der Mitgliederliste stehen, lassen sich als „nicht in der Mitgliederliste“ zuordnen; ist unklar, zu wem eine Zahlung gehört,
        geht sie als Klärfall an die Mitgliederverwaltung. Die Datei wird beim Aufteilen als
        Beleg gespeichert (auch im Kassenprüfer-Paket); vorher wird automatisch gesichert.</p></div>
    <div id="sepa-preview"></div>`, { wide: true });
  const box = el.querySelector('#sepa-preview');
  el.querySelector('#sepa-file').onchange = (e) => safe(async () => {
    const file = e.target.files[0];
    if (!file) return;
    mount(box, html`<p class="small muted">Wird gelesen …</p>`);
    try {
      const prev = await api('/api/sepa-vorschau', { filename: file.name, content: await fileToBase64(file) });
      draw(prev);
    } catch (err) {
      mount(box, '');
      throw err;
    }
  });

  function draw(prev) {
    // Vorauswahl der Sammelbuchung nur, wenn genau eine passt und Name/Verwendungszweck nach Sammler aussehen (Prüfbefund 0.29)
    const state = prev.batches.map((b) => ({ bank: b.candidates.length === 1 && b.candidates[0].hint ? b.candidates[0].bank_id : null, choices: {}, done: null }));
    // Ziel einer Einzelzahlung: eigene Wahl, sonst sicherer Vorschlag; Vorschläge „prüfen“ zählen erst nach Bestätigung
    const target = (bi, item) => state[bi].choices[item.no] || (item.match.sure ? item.match : null);
    const assigned = (t) => !!(t && (t.member_id || t.invoice_id || t.external || t.case));
    // Einzelzahlungen ganz ohne Treffer: lassen sich gesammelt als Klärfälle an die Mitgliederverwaltung melden
    const unmatched = (bi) => prev.batches[bi].items.filter((i) => !state[bi].choices[i.no] && !i.match.member_id && !i.match.invoice_id);
    const caseOf = (item) => ({ case: { name: item.payer || item.name || 'unbekannt' } });
    const candidate = (bi) => prev.batches[bi].candidates.find((c) => c.bank_id === state[bi].bank);
    // Sammelbestätigung nur für einfache Namensvorschläge ohne Widerspruch (match.bulk); sie wird nicht gemerkt (confirmed: 'bulk')
    const unconfirmed = (bi) => prev.batches[bi].items.filter((i) => !state[bi].choices[i.no] && i.match && !i.match.sure && i.match.bulk);
    const confirmItem = (bi, item, how = true) => {
      state[bi].choices[item.no] = { ...(item.match.invoice_id ? { invoice_id: item.match.invoice_id } : { member_id: item.match.member_id }),
        confirmed: how, reason: item.match.reason.replaceAll(' – bitte prüfen', '') };
    };

    function render() {
      mount(box, html`<p class="muted">${prev.filename} · ${prev.kind} · ${prev.batches.length} ${prev.batches.length === 1 ? 'Sammelbuchung' : 'Sammelbuchungen'}</p>
        ${prev.kind.includes('pain.008') ? html`<div class="filebox sepa-pdf"><label class="field">Begleitzettel zur Lastschriftdatei (PDF, optional)
          <input type="file" accept=".pdf" data-pdf></label>
          <p class="small muted">${prev.payers ? `Zahler für ${prev.payers} Einzelzahlungen aus dem Begleitzettel übernommen.`
            : 'Die Mitgliederverwaltung druckt zur Datei eine Liste mit der Spalte „Zahler“ (das Mitglied – auch wenn z. B. Eltern zahlen). Die PDF hilft beim Zuordnen; gespeichert wird sie nicht.'}</p></div>` : ''}
        ${prev.batches.map((b, bi) => {
          const s = state[bi];
          const open = b.items.filter((i) => !assigned(target(bi, i))).length;
          const blocked = b.note && b.note.includes('weicht');
          return html`<section class="panel sepa-batch" data-batch="${bi}">
            <div class="sepa-head"><div><h3>${b.ref || 'Sammelbuchung'} · ${date(b.date)}</h3>
              <p class="small muted">${b.items.length} Einzelzahlungen · zusammen ${eur(b.total)}${b.file ? ` · ${b.file}` : ''}</p></div>
              <div class="sepa-counts">${badge(`${b.counts.sure} sicher`, 'ok')} ${b.counts.check ? badge(`${b.counts.check} prüfen`, 'warn') : ''}
                ${open ? badge(`${open} offen`, 'danger') : ''}</div></div>
            ${b.note ? html`<p class="notice ${blocked ? 'warn' : ''}">${b.note}</p>` : ''}
            ${s.done ? html`<p class="notice">${icon('check', 16)} Aufgeteilt: ${s.done}</p>` : html`
              ${b.done ? html`<p class="notice">Dieser Sammler der Datei wurde am ${date(b.done)} schon aufgeteilt.</p>` : ''}
              <h4>Sammelbuchung im Kontoauszug</h4>
              ${b.candidates.length ? html`<div class="sepa-candidates" role="radiogroup" aria-label="Sammelbuchung">${b.candidates.map((c) => html`
                <label class="check"><input type="radio" name="sepa-bank-${bi}" value="${c.bank_id}" ${s.bank === c.bank_id ? 'checked' : ''} data-bank="${bi}">
                  <span>${date(c.booked)} · ${eur(c.amount)} · ${c.name || '—'}<span class="sub">${c.reference}</span>
                  <span class="sub">${c.mode === 'book' ? (c.state === 'parked' ? 'im Parkkonto – wird jetzt je Mitglied verbucht' : 'noch nicht verbucht – wird jetzt je Mitglied verbucht')
                    : `schon auf 8100 gebucht – wird den Mitgliedern zugeordnet${c.assigned ? ' (ersetzt die bisherige Zuordnung)' : ''}`}</span></span></label>`)}</div>
                ${!s.bank ? html`<p class="small muted">Bitte die Sammelbuchung auswählen – nur Betrag und Datum stimmen sicher überein.</p>` : ''}`
                : html`<p class="notice warn">Keine passende Sammelbuchung gefunden (gleicher Betrag, gebucht 3 Tage vor bis 10 Tage nach dem Datum der Datei,
                  noch nicht mit einer SEPA-Datei aufgeteilt). Kontoauszug importieren und die Datei danach erneut einlesen.</p>`}
              <h4>Einzelzahlungen</h4>
              ${table(['Einzelzahlung laut Datei', 'Zuordnung'], b.items.map((i) => {
                const own = s.choices[i.no], t = own || i.match, label = targetLabel(t), unsure = !own && label && !i.match.sure;
                return html`<tr><td><strong style="font-weight:500">${i.name || '—'}</strong> · <span class="nowrap">${eur(i.amount)}</span>
                    ${i.payer ? html`<span class="sub">Zahler: ${i.payer}</span>` : ''}
                    <span class="sub">${[i.text, i.mandate && `Mandat ${i.mandate}`, i.iban_masked].filter(Boolean).join(' · ')}</span></td>
                  <td>${label ? html`${label}${unsure ? html` ${badge('prüfen', 'warn')}` : ''}<span class="sub">${own ? `${own.case ? 'bleibt ohne Mitglied, bis die Mitgliederverwaltung geklärt hat' : own.confirmed ? `${own.confirmed === 'bulk' ? 'mit allen bestätigt' : 'bestätigt'} (${own.reason})` : 'von Hand gewählt'}${duesNote(own, Number(b.date.slice(0, 4)))}` : i.match.reason}</span>`
                    : html`<span class="over">noch nicht zugeordnet</span>`}
                    <span class="sepa-actions">${unsure ? html`<button type="button" class="btn small primary" data-confirm="${bi}:${i.no}">Bestätigen</button> ` : ''}
                      <button type="button" class="btn small" data-choose="${bi}:${i.no}">${label ? 'Ändern' : 'Zuordnen'}</button>
                      ${own?.case ? '' : html`<button type="button" class="btn small" data-unclear="${bi}:${i.no}" title="Unklar, zu wem die Zahlung gehört: Aufgabe für die Mitgliederverwaltung">Klärfall</button>`}</span></td></tr>`;
              }))}
              <div class="form-actions">${unconfirmed(bi).length ? html`<button type="button" class="btn" data-confirm-all="${bi}"
                  title="Nur einfache Namensvorschläge ohne Widerspruch; Hinweise wie „weitere Zahlung“ oder „passt zu einem anderen Mitglied“ bitte einzeln prüfen">
                  ${unconfirmed(bi).length} Namensvorschläge bestätigen</button>` : ''}
                ${unmatched(bi).length ? html`<button type="button" class="btn" data-unclear-all="${bi}"
                  title="Alle Einzelzahlungen ohne Treffer gehen als Klärfälle an die Mitgliederverwaltung">${unmatched(bi).length} ohne Treffer als Klärfälle melden</button>` : ''}
                <button type="button" class="btn primary" data-apply="${bi}" ${!s.bank || open || blocked ? 'disabled' : ''}>
                Sammelbuchung aufteilen (${b.items.length} Zahlungen)</button></div>
              ${open ? html`<p class="small muted">Erst wenn jede Einzelzahlung zugeordnet bzw. jeder Vorschlag „prüfen“ bestätigt ist, lässt sich die Sammelbuchung aufteilen.
                Einzeln bestätigte oder gewählte Zuordnungen merkt sich das Programm je Mandatsreferenz – in der nächsten Datei sind sie dann sicher,
                solange nichts widerspricht (Begleitzettel, Mitgliedsnummer, Austritt). Gemerktes steht im Mitglied und lässt sich dort entfernen.
                Zahlungen eines Mitglieds gehen zuerst auf seine offenen Beitragsrechnungen (älteste zuerst), nur der Rest als Beitrag auf 8100.
                „Klärfall“: Die Zahlung wird als Beitrag ohne Mitglied gebucht und erscheint bei der Mitgliederverwaltung unter „Klärfälle“.</p>` : ''}`}
          </section>`;
        })}`);
      box.querySelectorAll('[data-bank]').forEach((r) => (r.onchange = () => { state[Number(r.dataset.bank)].bank = Number(r.value); render(); }));
      const itemOf = (key) => { const [bi, no] = key.split(':').map(Number); return [bi, no, prev.batches[bi].items.find((x) => x.no === no)]; };
      box.querySelectorAll('[data-confirm]').forEach((btn) => (btn.onclick = () => {
        const [bi, , item] = itemOf(btn.dataset.confirm);
        confirmItem(bi, item);
        render();
      }));
      box.querySelectorAll('[data-unclear]').forEach((btn) => (btn.onclick = () => {
        const [bi, no, item] = itemOf(btn.dataset.unclear);
        state[bi].choices[no] = caseOf(item);
        render();
      }));
      box.querySelectorAll('[data-unclear-all]').forEach((btn) => (btn.onclick = () => {
        const bi = Number(btn.dataset.unclearAll);
        unmatched(bi).forEach((item) => { state[bi].choices[item.no] = caseOf(item); });   // jede Zahlung bleibt einzeln änderbar
        render();
      }));
      box.querySelectorAll('[data-confirm-all]').forEach((btn) => (btn.onclick = () => {
        const bi = Number(btn.dataset.confirmAll);
        unconfirmed(bi).forEach((item) => confirmItem(bi, item, 'bulk'));   // jeder Vorschlag bleibt einzeln änderbar; nicht gemerkt
        render();
      }));
      box.querySelector('[data-pdf]')?.addEventListener('change', (e) => safe(async () => {
        const file = e.target.files[0];
        if (!file) return;
        const next = await api('/api/sepa-begleitzettel', { token: prev.token, filename: file.name, content: await fileToBase64(file) });
        Object.assign(prev, next);                       // eigene Auswahl bleibt; Bestätigungen nur, wenn der Vorschlag gleich geblieben ist
        let dropped = 0;
        prev.batches.forEach((b, bi) => b.items.forEach((i) => {
          const own = state[bi].choices[i.no];
          if (own?.confirmed && (Number(own.member_id ?? 0) !== Number(i.match.member_id ?? 0) || (own.invoice_id ?? null) !== (i.match.invoice_id ?? null) || !i.match.member_id)) {
            delete state[bi].choices[i.no];
            dropped += 1;
          }
        }));
        toast(`Zahler für ${next.found} Einzelzahlungen übernommen.${dropped ? ` ${dropped} Bestätigungen aufgehoben – der Vorschlag hat sich geändert.` : ''}`);
        render();
      }));
      box.querySelectorAll('[data-choose]').forEach((btn) => (btn.onclick = () => {
        const [bi, no, item] = itemOf(btn.dataset.choose);
        const current = state[bi].choices[no];
        const c = candidate(bi);
        pickMembers({ amount: item.amount, split: false, payer: item.payer || item.name, hint: `${item.payer || ''} ${item.name} ${item.text}`,
          entryKv: c?.mode === 'assign' && c.kv !== 'LV' ? c.kv : '',
          selected: current?.external ? [{ external: current.external }] : [], title: `Einzelzahlung ${eur(item.amount)} zuordnen`,
          onDone: ([x]) => { state[bi].choices[no] = x.case ? { case: x.case } : x.external ? { external: x.external } : { member_id: x.member_id }; render(); } });
      }));
      box.querySelectorAll('[data-apply]').forEach((btn) => (btn.onclick = () => safe(async () => {
        if (btn.disabled) return;
        btn.disabled = true;
        const bi = Number(btn.dataset.apply);
        try {
          const r = await api('/api/sepa-aufteilen', { token: prev.token, batch: bi, bank_id: state[bi].bank, choices: state[bi].choices });
          const parts = [r.members && `${r.members} Mitglieder auf 8100`, r.invoices && `${r.invoices} Rechnungen`, r.external && `${r.external} außerhalb der Mitgliederliste`,
            r.cases && `${r.cases} als Klärfall an die Mitgliederverwaltung`].filter(Boolean);
          state[bi].done = `${r.count} Zahlungen ${r.mode === 'book' ? 'verbucht' : 'zugeordnet'} (${parts.join(', ')})${r.learned ? ` · ${r.learned} Mandatsreferenzen gemerkt` : ''}. Sicherung: ${r.backup}${r.warning ? ` · ${r.warning}` : ''}`;
          await refresh();
          toast(r.warning || 'Sammelbuchung aufgeteilt.', !!r.warning);
          render();
        } finally { btn.disabled = false; }
      })));
    }
    render();
  }
  return el;
}
