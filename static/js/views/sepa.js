// SEPA-Lastschrift: Mandat am Mitglied erfassen/widerrufen, Lastschriftdatei (pain.008.001.08) erzeugen und einreichen.
import { api, download } from '../api.js';
import { html } from '../html.js';
import { eur, date, dateTime, todayIso } from '../format.js';
import { store, refresh, go } from '../store.js';
import { dialog, overlay, table, badge, empty, safe, toast, icon } from '../ui.js';

/** Aktives Mandat eines Mitglieds (aus den Daten, IBAN gekürzt). */
export const activeMandate = (memberId) => store.data.mandates.find((m) => m.member_id === memberId && m.status === 'active');

/** Abschnitt im Mitgliedskonto: Mandat anzeigen, erfassen, widerrufen, Formular drucken. */
export function mandateSection(member) {
  const m = activeMandate(member.id);
  const old = store.data.mandates.filter((x) => x.member_id === member.id && x.status === 'revoked');
  const enabled = Boolean(store.data.settings.creditor_id);
  return html`<section class="approval-card ${m ? 'done' : ''}"><h3>${icon('bank', 16)} SEPA-Lastschrift</h3>
    ${m && member.iban_abweichend ? html`<p class="notice warn" data-iban-abweichend>Die Mitgliederverwaltung hat eine andere IBAN eingetragen als im Mandat –
      Kontowechsel? Dann das alte Mandat widerrufen und das neue (unterschriebene) erfassen.</p>` : ''}
    ${m ? html`<p>Mandat <strong style="font-weight:600">${m.reference}</strong> · ${m.holder} · ${m.iban} · unterschrieben am ${date(m.signed_on)}${m.last_used ? ` · zuletzt eingezogen ${date(m.last_used)}` : ''}</p>
        <p class="small muted">Beitragsrechnungen enthalten die Vorabankündigung; der Beitrag wird per Lastschrift eingezogen.
          ${m.expires < todayIso() ? html`<strong>Mandat verfallen</strong> (36 Monate ungenutzt) – neues Mandat einholen; zum Erfassen das alte zuerst widerrufen.` : `Gültig für Einzüge bis ${date(m.expires)}, wenn es bis dahin nicht genutzt wird.`}</p>`
      : html`<p class="small muted">${enabled ? 'Kein Mandat – der Beitrag wird überwiesen.' : 'Für Lastschriften zuerst die Gläubiger-ID in den Stammdaten eintragen.'}${
        member.iban ? ' Die Mitgliederverwaltung hat Bankdaten eingetragen – liegt das unterschriebene Formular vor, damit erfassen.' : ''}</p>`}
    ${old.length ? html`<p class="small muted">Frühere Mandate: ${old.map((x, i) => html`${i ? ', ' : ''}${x.reference} (widerrufen ${date(x.revoked_on)})`)}</p>` : ''}
    <div class="form-actions" style="justify-content:flex-start">
      <a class="btn small" href="/api/mandate.pdf?member=${member.id}" target="_blank" rel="noopener">${icon('print', 14)} Mandatsformular</a>
      ${m ? html`<button type="button" class="btn small ghost" data-revoke-mandate="${m.id}">Mandat widerrufen</button>`
        : enabled ? html`<button type="button" class="btn small primary" data-new-mandate="${member.id}">${member.iban ? 'Mandat aus den Bankdaten erfassen' : 'Unterschriebenes Mandat erfassen'}</button>` : ''}</div></section>`;
}

export function bindMandateSection(el, member, reopen) {
  const add = el.querySelector('[data-new-mandate]');
  if (add) add.onclick = () => mandateForm(member, reopen);
  const revoke = el.querySelector('[data-revoke-mandate]');
  if (revoke) revoke.onclick = () => {
    const o = overlay('Mandat widerrufen', html`<form id="revoke-form" style="display:flex;flex-direction:column;gap:12px">
      <p>Danach wird nichts mehr per Lastschrift eingezogen. Angekündigte Beiträge zahlt das Mitglied per Überweisung (GiroCode, Zahlungserinnerung). Steckt eine Lastschrift schon in einer noch nicht eingereichten Datei, dort herausnehmen.</p>
      <label class="field">Grund<input name="reason" required placeholder="z. B. Widerruf durch das Mitglied, Kontowechsel"></label>
      <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn danger">Widerrufen</button></div></form>`, { wide: false });
    o.querySelector('#revoke-form').onsubmit = (e) => {
      e.preventDefault();
      safe(async () => {
        await api('/api/mandate-revoke', { id: Number(revoke.dataset.revokeMandate), reason: e.target.reason.value });
        o.close();
        await refresh();
        toast('Mandat widerrufen.');
        reopen();
      });
    };
  };
}

function mandateForm(member, reopen) {
  const o = overlay('Unterschriebenes Mandat erfassen', html`<form id="mandate-form" style="display:flex;flex-direction:column;gap:14px">
    <p class="small muted">Nur erfassen, wenn das unterschriebene Mandat vorliegt (Formular über „Mandatsformular“). Bei Kontowechsel das alte Mandat widerrufen und ein neues erfassen.</p>
    <div class="formgrid">
      <label class="field">Kontoinhaber<input name="holder" value="${member.kontoinhaber || member.name}" required></label>
      <label class="field">IBAN<input name="iban" required autocomplete="off" value="${member.iban || ''}"></label>
      <label class="field">BIC<input name="bic" autocomplete="off" placeholder="optional"></label>
      <label class="field">Unterschrieben am<input type="date" name="signed_on" value="${todayIso()}" required></label>
      <label class="field">Mandatsreferenz<input name="reference" autocomplete="off" placeholder="leer = automatisch (${store.data.settings.sepa_prefix}-${member.number}-1)">
        <span class="small muted">Nur bei einem schon vorhandenen Papiermandat dessen Referenz eintragen.</span></label>
      <label class="field">Zuletzt eingezogen<input type="date" name="used_before">
        <span class="small muted">Nur bei einem übernommenen Papiermandat, falls bekannt – ein Mandat verfällt nach 36 Monaten ohne Einzug.</span></label>
    </div>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Mandat speichern</button></div></form>`, { wide: true });
  o.querySelector('#mandate-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const r = await api('/api/mandate', { member: member.id, ...Object.fromEntries(new FormData(e.target)) });
      o.close();
      await refresh();
      toast(`Mandat ${r.reference} gespeichert.`);
      reopen();
    });
  };
}

// ---------------------------------------------------------------- Lastschriftdatei

const BATCH_LABEL = { created: ['Erstellt – noch nicht eingereicht', 'warn'], submitted: ['Eingereicht', 'ok'], cancelled: ['Verworfen', ''] };
const plural = (n) => `${n} Lastschrift${n === 1 ? '' : 'en'}`;

function noticeForm(item) {
  const o = overlay('Vorabankündigung per Post', html`<form id="notice-form" style="display:flex;flex-direction:column;gap:12px">
    <p>Rechnung ${item.number} an ${item.recipient} mit Post versendet? Eingezogen wird frühestens ${store.data.settings.sepa_prenotice} Tage nach dem Versand.</p>
    <label class="field">Versendet am<input type="date" name="sent_on" value="${todayIso()}" max="${todayIso()}" required></label>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn primary">Eintragen</button></div></form>`, { wide: false });
  o.querySelector('#notice-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/debit-notice', { invoice: item.id, sent_on: e.target.sent_on.value });
      o.close();
      await refresh();
      toast('Postversand eingetragen.');
      await sepaDialog();
    });
  };
}

function cancelForm(batch) {
  const needsReason = batch.downloaded_on || batch.status === 'submitted';
  const o = overlay('Lastschriftdatei verwerfen', html`<form id="cancel-form" style="display:flex;flex-direction:column;gap:12px">
    <p>${needsReason ? 'Die Datei wurde schon heruntergeladen oder eingereicht. Nur verwerfen, wenn die Bank sie nicht ausgeführt hat (abgelehnt oder nie hochgeladen) – sonst wird doppelt eingezogen.'
      : 'Die Rechnungen können danach erneut eingezogen werden.'}</p>
    ${needsReason ? html`<label class="field">Grund<input name="reason" required placeholder="z. B. von der Bank abgelehnt, nie hochgeladen"></label>` : ''}
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn danger">Verwerfen</button></div></form>`, { wide: false });
  o.querySelector('#cancel-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/sepa-batch-status', { id: batch.id, status: 'cancelled', reason: e.target.reason?.value || '' });
      o.close();
      await refresh();
      toast('Lastschriftdatei verworfen.');
      await sepaDialog();
    });
  };
}

function batchRow(b) {
  const open = b.items.filter((d) => d.status === 'pending');
  const flagged = open.filter((d) => d.issue);
  const fresh = b.status === 'created' && !b.downloaded_on;
  return html`<tr>
    <td><strong style="font-weight:600">${b.msg_id}</strong><span class="sub">${dateTime(b.created)} · ${plural(b.count)}${b.downloaded_on ? ` · heruntergeladen ${dateTime(b.downloaded_on)}` : ''}</span></td>
    <td>${b.dates.map(date).join(', ')}</td><td class="num">${eur(b.total)}</td>
    <td>${badge(...BATCH_LABEL[b.status])}${b.submitted_on ? html`<span class="sub">am ${date(b.submitted_on)}</span>` : ''}${b.cancel_reason ? html`<span class="sub">${b.cancel_reason}</span>` : ''}</td>
    <td style="white-space:nowrap">${b.status === 'created' && !b.issues ? html`<button type="button" class="btn small" data-download="${b.id}">${icon('file', 14)} Datei</button> ` : ''}
      ${b.status === 'created' && b.downloaded_on ? html`<button type="button" class="btn small primary" data-submitted="${b.id}">Als eingereicht markieren</button> ` : ''}
      ${b.status !== 'cancelled' ? html`<button type="button" class="btn small ghost" data-cancel-batch="${b.id}">Verwerfen</button>` : ''}</td></tr>
    ${flagged.length ? html`<tr class="row-flag"><td colspan="5"><p class="small" style="margin:0 0 6px"><strong>Erneut geprüft – nicht mehr einziehen:</strong></p>
      ${flagged.map((d) => html`<p class="small" style="margin:0 0 4px">${d.number} · ${d.recipient} · ${eur(d.amount)}: ${d.issue}
        ${fresh ? html`<button type="button" class="link" data-remove-debit="${d.id}">Herausnehmen</button>` : ''}</p>`)}
      <p class="small muted" style="margin:6px 0 0">${fresh ? 'Danach die Datei herunterladen.'
        : 'Die Datei wurde schon heruntergeladen und bleibt, wie sie ist. Schon hochgeladen: als eingereicht markieren und betroffene Lastschriften danach als nicht ausgeführt markieren bzw. die Rücklastschrift zuordnen. Nicht hochgeladen: verwerfen und neu erstellen.'}</p></td></tr>` : ''}
    ${b.status === 'submitted' && open.length ? html`<tr><td colspan="5"><details><summary class="small">Lastschriften der Datei (${open.length} ausstehend)</summary>
      ${open.map((d) => html`<p class="small" style="margin:4px 0">${d.number} · ${d.recipient} · ${eur(d.amount)} · Einzug ${date(d.collection_date)}
        <button type="button" class="link" data-reject-debit="${d.id}">Nicht ausgeführt</button></p>`)}
      <p class="small muted">„Nicht ausgeführt“ nur, wenn die Bank diese Lastschrift abgelehnt hat und kein Geld geflossen ist – eine Rücklastschrift wird im Buchungsplatz zugeordnet.</p></details></td></tr>` : ''}`;
}

function rejectForm(debit) {
  const o = overlay('Lastschrift nicht ausgeführt', html`<form id="reject-form" style="display:flex;flex-direction:column;gap:12px">
    <p>${debit.number} · ${debit.recipient} · ${eur(debit.amount)}: Die Rechnung fällt auf Überweisung zurück (GiroCode, Zahlungserinnerung möglich).</p>
    <label class="field">Grund<input name="reason" required placeholder="z. B. von der Bank abgelehnt: Konto erloschen"></label>
    <div class="form-actions"><button type="button" class="btn ghost" data-close>Abbrechen</button><button type="submit" class="btn danger">Als nicht ausgeführt markieren</button></div></form>`, { wide: false });
  o.querySelector('#reject-form').onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      await api('/api/sepa-debit-reject', { id: debit.id, reason: e.target.reason.value });
      o.close();
      await refresh();
      toast('Als nicht ausgeführt markiert.');
      await sepaDialog();
    });
  };
}

export async function sepaDialog() {
  const data = await api('/api/sepa');
  if (!data.enabled) {
    const el = dialog('Lastschriften', html`<p class="notice warn">Für Lastschriften fehlt die Gläubiger-Identifikationsnummer (kostenlos bei der Deutschen Bundesbank) und eine Lastschriftvereinbarung mit der Bank.</p>
      <div class="form-actions"><button type="button" class="btn primary" data-settings>Zu den Einstellungen</button></div>`);
    el.querySelector('[data-settings]').onclick = () => { el.close(); go('settings'); };
    return;
  }
  const chosen = new Set(data.collectable.filter((c) => !c.reason && c.soon).map((c) => c.id));
  const render = () => {
    const picked = data.collectable.filter((c) => chosen.has(c.id) && !c.reason);
    const early = picked.filter((c) => !c.soon);
    const el = dialog('Lastschriften', html`<div style="display:flex;flex-direction:column;gap:16px">
      <p class="small muted">Eingezogen werden Beitragsrechnungen mit Vorabankündigung (Mitglied mit Mandat), sobald die Rechnung versendet ist. Die Datei im Online-Banking unter „SEPA-Lastschrift / Datei hochladen“ einreichen – frühestens etwa zwei Wochen und spätestens einen Bankarbeitstag vor dem Einzugsdatum. Die Gutschriften werden über den Abgleich zugeordnet.</p>
      ${data.collectable.length ? table(['', 'Rechnung', 'Mitglied', 'Mandat', 'Einzug', { label: 'Betrag', className: 'num' }, 'Stand'], data.collectable.map((c) => html`<tr>
          <td><input type="checkbox" data-pick="${c.id}" ${chosen.has(c.id) && !c.reason ? 'checked' : ''} ${c.reason ? 'disabled' : ''} aria-label="${c.number} auswählen"></td>
          <td><strong style="font-weight:600">${c.number}</strong></td><td>${c.recipient}<span class="sub">${c.iban}</span></td><td>${c.mandate}</td>
          <td>${date(c.date)}${c.date !== c.announced ? html`<span class="sub">angekündigt ${date(c.announced)}</span>` : ''}</td>
          <td class="num">${eur(c.open)}</td><td>${c.reason ? html`<span class="small muted">${c.reason}</span>${c.state === 'unnotified'
            ? html` <button type="button" class="link" data-notice="${c.id}">Postversand eintragen</button>` : ''}` : c.soon ? badge('bereit', 'ok') : badge('später', '')}</td></tr>`))
        : empty('Keine Lastschriften fällig', 'Beitragsrechnungen an Mitglieder mit Mandat erscheinen hier nach dem Beitragslauf.')}
      ${early.length ? html`<p class="notice warn">${plural(early.length)} mit Einzug in mehr als 14 Tagen ausgewählt – viele Banken nehmen die Datei so früh nicht an.</p>` : ''}
      <div class="bulkbar"><strong>${plural(picked.length)} · ${eur(picked.reduce((s, c) => s + c.open, 0))}</strong>
        <button type="button" class="btn primary" data-create ${picked.length ? '' : 'disabled'}>Lastschriftdatei erstellen</button></div>
      ${data.batches.length ? html`<div><h3 style="margin-bottom:8px">Lastschriftdateien</h3>${table(['Datei', 'Einzug', { label: 'Betrag', className: 'num' }, 'Stand', ''], data.batches.map(batchRow))}
        <p class="small muted">Eine eingereichte Datei lässt sich nicht erneut herunterladen – so wird nichts doppelt hochgeladen. Die Liste aller Lastschriften (IBAN gekürzt) enthält das Kassenprüfer-Paket.</p></div>` : ''}
    </div>`, { wide: true });
    el.querySelectorAll('[data-pick]').forEach((c) => (c.onchange = () => { c.checked ? chosen.add(Number(c.dataset.pick)) : chosen.delete(Number(c.dataset.pick)); render(); }));
    el.querySelectorAll('[data-notice]').forEach((b) => (b.onclick = () => noticeForm(data.collectable.find((c) => c.id === Number(b.dataset.notice)))));
    el.querySelector('[data-create]').onclick = (e) => safe(async () => {
      e.target.disabled = true;
      try {
        await api('/api/sepa-batch', { ids: picked.map((c) => c.id) });
        await refresh();
        toast('Lastschriftdatei erstellt – jetzt herunterladen und im Online-Banking hochladen.');
        await sepaDialog();
      } finally { e.target.disabled = false; }
    });
    el.querySelectorAll('[data-download]').forEach((b) => (b.onclick = () => safe(async () => {
      const batch = data.batches.find((x) => x.id === Number(b.dataset.download));
      const saved = await download(`/api/sepa.xml?batch=${batch.id}`, `Lastschrift-${batch.msg_id}.xml`);
      toast(saved ? 'Datei heruntergeladen – im Online-Banking hochladen und danach als eingereicht markieren.'
        : 'Datei sichern – danach im Online-Banking hochladen und als eingereicht markieren.');
      await sepaDialog();
    })));
    el.querySelectorAll('[data-submitted]').forEach((b) => (b.onclick = () => safe(async () => {
      await api('/api/sepa-batch-status', { id: Number(b.dataset.submitted), status: 'submitted' });
      await refresh();
      toast('Als eingereicht markiert.');
      await sepaDialog();
    })));
    el.querySelectorAll('[data-remove-debit]').forEach((b) => (b.onclick = () => safe(async () => {
      await api('/api/sepa-debit-remove', { id: Number(b.dataset.removeDebit) });
      await refresh();
      toast('Lastschrift herausgenommen – Datei neu herunterladen.');
      await sepaDialog();
    })));
    el.querySelectorAll('[data-cancel-batch]').forEach((b) => (b.onclick = () => cancelForm(data.batches.find((x) => x.id === Number(b.dataset.cancelBatch)))));
    el.querySelectorAll('[data-reject-debit]').forEach((b) => (b.onclick = () => rejectForm(data.batches.flatMap((x) => x.items).find((d) => d.id === Number(b.dataset.rejectDebit)))));
  };
  render();
}
