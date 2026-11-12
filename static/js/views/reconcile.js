// Abgleich: offene Kontoumsätze eindeutig passenden Eingangs- und Ausgangsrechnungen zuordnen (nach Bestätigung).
import { api } from '../api.js';
import { html } from '../html.js';
import { eur, date } from '../format.js';
import { refresh } from '../store.js';
import { dialog, table, badge, empty, safe, toast } from '../ui.js';

export async function reconcileDialog() {
  const proposals = await api('/api/abgleich');
  // vorausgewählt nur, was mindestens zweifach belegt ist (z. B. IBAN und Rechnungsnummer); der Rest bitte selbst prüfen
  const chosen = new Set(proposals.filter((p) => p.confident).map((p) => p.bank_id));
  const render = () => {
    const el = dialog('Abgleich mit Kontoumsätzen', html`
      <p class="small muted">Vorgeschlagen werden nur eindeutige Fälle: Der Betrag passt genau zum offenen Betrag, und IBAN oder Rechnungsnummer stimmen. Vorausgewählt ist nur, was doppelt belegt ist – nie ein Umsatz, dessen Zuordnung schon einmal storniert wurde, und keine Fremdrechnung ohne Freigabe nach § 7 Abs. 4. Eine verbuchte Zuordnung lässt sich im Buchungsplatz stornieren und neu zuordnen.</p>
      ${proposals.length ? table(['', 'Kontoumsatz', { label: 'Betrag', className: 'num' }, 'Zuordnung', 'Grund'], proposals.map((p) => html`<tr>
        <td><input type="checkbox" data-pick="${p.bank_id}" ${chosen.has(p.bank_id) ? 'checked' : ''} aria-label="Zuordnung ${p.number} auswählen"></td>
        <td>${date(p.booked)} · ${p.bank_name || '—'}<span class="sub">${p.reference || ''}</span></td>
        <td class="num">${eur(p.amount)}</td>
        <td><strong style="font-weight:600">${p.number}</strong><span class="sub">${p.kind === 'bill' ? 'Eingangsrechnung' : 'Rechnung'} · ${p.name}</span></td>
        <td>${p.evidence.map((e) => badge(e, 'ok'))}${p.reversed ? html` ${badge('zuvor storniert', 'warn')}` : ''}${p.unapproved ? html` ${badge('Freigabe fehlt (§ 7 Abs. 4)', 'danger')}` : ''}${p.confident ? '' : html` ${badge('bitte prüfen', 'warn')}`}</td></tr>`))
        : empty('Keine eindeutigen Zuordnungen', 'Erst den Kontoauszug importieren; nicht eindeutige Umsätze im Buchungsplatz zuordnen.')}
      <div class="bulkbar"><strong>${chosen.size} von ${proposals.length} ausgewählt</strong>
        <button type="button" class="btn primary" data-apply ${chosen.size ? '' : 'disabled'}>${chosen.size} Zuordnung${chosen.size === 1 ? '' : 'en'} verbuchen</button></div>`, { wide: true });
    el.querySelectorAll('[data-pick]').forEach((c) => (c.onchange = () => { c.checked ? chosen.add(Number(c.dataset.pick)) : chosen.delete(Number(c.dataset.pick)); render(); }));
    el.querySelector('[data-apply]').onclick = (e) => safe(async () => {
      e.target.disabled = true;
      try {
        const items = proposals.filter((p) => chosen.has(p.bank_id)).map((p) => ({ bank_id: p.bank_id, kind: p.kind, id: p.id }));
        const r = await api('/api/abgleich', { items });
        el.close();
        await refresh();
        toast(r.error || `${r.count} Kontoumsätze zugeordnet und verbucht.`, Boolean(r.error));
      } finally { e.target.disabled = false; }
    });
  };
  render();
}
