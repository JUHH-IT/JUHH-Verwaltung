// E-Mail-Versand von Rechnungen: Auswahl → Vorschau/Testmail → Bestätigung → Versand mit Fortschritt.
import { api } from '../api.js';
import { html } from '../html.js';
import { eur, shortDate, date, dateTime } from '../format.js';
import { store, refresh, go } from '../store.js';
import { dialog, table, badge, safe, toast, icon, mailPreview, empty } from '../ui.js';

/** Sinnvoll per E-Mail zu senden (Vorauswahl): offen, unbezahlt, nicht versendet, Adresse vorhanden. */
export const sendable = (i) => i.status === 'issued' && !i.pilot && i.total > 0 && i.paid === 0 && !!i.mail_to && !i.sent_at && i.last_status !== 'unknown';

export function mailBadge(i) {
  if (i.status !== 'issued') return '';
  if (i.pilot) return badge('Vorversion – nicht versenden');
  if (i.last_status === 'unknown') return html`<span class="badge danger" title="${i.last_error || ''}">Zustellung unklar</span>`;
  if (i.sent_at) return badge('Versendet ' + shortDate(i.sent_at.slice(0, 10)), 'ok');
  if (i.last_error) return html`<span class="badge danger" title="${i.last_error}">Fehler beim Versand</span>`;
  if (!i.mail_to) return badge('Keine E-Mail');
  if (i.total > 0 && i.paid >= i.total) return badge('Nicht versendet');   // schon bezahlt: kein Handlungsbedarf
  return badge('Nicht versendet', 'warn');
}

/** Stand der Zahlungserinnerungen einer Rechnung. */
export function reminderBadge(i) {
  if (i.status !== 'issued') return '';
  if (i.reminder_status === 'unknown') return badge('Erinnerung unklar zugestellt', 'danger');
  return i.reminders ? badge(`${i.reminders}× erinnert, zuletzt ${shortDate(i.reminded_at.slice(0, 10))}`, 'warn') : '';
}

/** Sammel-PDF zum Drucken (z. B. für Mitglieder ohne E-Mail). */
export const printLink = (ids, label = 'PDF zum Drucken') => html`<a class="btn" href="/api/invoices.pdf?ids=${ids.join(',')}" target="_blank" rel="noopener">${icon('print', 16)} ${label}</a>`;

/** Ergebnis nach dem Festschreiben: direkt versenden oder drucken. */
export function afterIssue(ids, what) {
  const invoices = store.data.invoices.filter((i) => ids.includes(i.id));
  const withMail = invoices.filter((i) => i.mail_to && i.total > 0).length;
  const free = invoices.filter((i) => i.total === 0).length;
  const el = dialog(`${ids.length} ${what} erstellt`, html`
    <p>Die Rechnungen sind festgeschrieben und gebucht. ${withMail} von ${ids.length} können per E-Mail versendet werden${ids.length - withMail - free ? '; die übrigen bitte drucken und per Post schicken' : ''}.${free ? ` ${free} beitragsfreie Rechnung(en) werden nicht verschickt.` : ''}</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-close-result>Später</button>${printLink(ids)}
      ${withMail ? html`<button type="button" class="btn primary" data-send>${icon('mail', 16)} Per E-Mail versenden …</button>` : ''}</div>`);
  el.querySelector('[data-close-result]').onclick = () => el.close();
  const send = el.querySelector('[data-send]');
  if (send) send.onclick = () => safe(() => sendDialog(ids));
}

export async function sendDialog(preselect = null) {
  const status = await api('/api/mail-status');
  if (status.job.state === 'running') return progress();
  const cfg = store.data.settings;
  const issued = store.data.invoices.filter((i) => i.status === 'issued');
  let view = preselect ? 'chosen' : 'unsent';
  const chosen = new Set(preselect ? issued.filter((i) => preselect.includes(i.id) && (sendable(i) || i.sent_at)).map((i) => i.id) : issued.filter(sendable).map((i) => i.id));
  let resend = false;

  const render = () => {
    const list = view === 'chosen' ? issued.filter((i) => preselect.includes(i.id)) : view === 'unsent' ? issued.filter((i) => !i.sent_at) : issued;
    const selected = issued.filter((i) => chosen.has(i.id));
    const mailable = selected.filter((i) => !i.pilot && i.total > i.paid && i.mail_to && (resend || (!i.sent_at && i.last_status !== 'unknown')));
    const already = selected.filter((i) => i.sent_at || i.last_status === 'unknown').length;
    const missing = [!cfg.mail_from || !cfg.smtp_user ? 'Absenderadresse und Benutzername' : '', !status.password ? 'Passwort' : ''].filter(Boolean);
    const el = dialog('Rechnungen per E-Mail versenden', html`
      ${missing.length ? html`<p class="notice warn">Für den Versand fehlt in den Stammdaten: ${missing.join(' und ')}.
        <button type="button" class="link" data-settings>Zu den Einstellungen</button></p>` : ''}
      <div class="toolbar"><div class="segmented" role="group" aria-label="Anzeige">
        ${preselect ? html`<button type="button" data-view="chosen" aria-pressed="${view === 'chosen'}">Neu erstellt</button>` : ''}
        <button type="button" data-view="unsent" aria-pressed="${view === 'unsent'}">Nicht versendet</button>
        <button type="button" data-view="all" aria-pressed="${view === 'all'}">Alle</button></div>
        <span class="small muted">Absender: ${cfg.mail_from_name} &lt;${cfg.mail_from || '—'}&gt;${cfg.mail_bcc ? ` · Kopie an ${cfg.mail_bcc}` : ''}</span></div>
      ${list.length ? table([html`<input type="checkbox" data-all aria-label="Alle auswählen" ${list.every((i) => chosen.has(i.id)) ? 'checked' : ''}>`, 'Rechnung', 'Empfänger', { label: 'Betrag', className: 'num' }, 'Versand', ''],
        list.map((i) => html`<tr><td><input type="checkbox" data-pick="${i.id}" ${chosen.has(i.id) ? 'checked' : ''} aria-label="${i.number} auswählen"></td>
          <td><strong style="font-weight:600">${i.number}</strong><span class="sub">${i.kind === 'dues' ? 'Beitrag ' + i.year : 'Rechnung'}</span></td>
          <td>${i.recipient}<span class="sub">${i.mail_to || 'keine E-Mail – nur Druck'}</span></td><td class="num">${eur(i.total)}</td><td>${mailBadge(i)}</td>
          <td><button type="button" class="btn small ghost" data-look="${i.id}">${icon('eye', 14)} Vorschau</button></td></tr>`))
        : empty('Nichts zu versenden', 'Alle offenen Rechnungen wurden bereits versendet.')}
      ${already ? html`<label class="check"><input type="checkbox" data-resend ${resend ? 'checked' : ''}> ${already} bereits versendete oder unklar zugestellte Rechnung(en) erneut senden</label>` : ''}
      ${selected.some((i) => i.paid > 0 && i.total > i.paid) ? html`<p class="small muted">Teilweise bezahlte Rechnungen: Die E-Mail nennt den bereits bezahlten und den offenen Betrag.</p>` : ''}
      <div class="bulkbar"><strong>${selected.length} ausgewählt · ${mailable.length} per E-Mail</strong>
        <button type="button" class="btn" data-test ${selected.length && !missing.length ? '' : 'disabled'}>Testmail an mich</button>
        ${selected.length ? printLink(selected.map((i) => i.id), `Drucken (${selected.length})`) : ''}
        <button type="button" class="btn primary" data-go ${mailable.length && !missing.length ? '' : 'disabled'}>${icon('mail', 16)} ${mailable.length} E-Mail${mailable.length === 1 ? '' : 's'} senden</button></div>`, { wide: true });
    el.querySelectorAll('[data-view]').forEach((b) => (b.onclick = () => { view = b.dataset.view; render(); }));
    el.querySelectorAll('[data-pick]').forEach((c) => (c.onchange = () => { c.checked ? chosen.add(Number(c.dataset.pick)) : chosen.delete(Number(c.dataset.pick)); render(); }));
    const all = el.querySelector('[data-all]');
    if (all) all.onchange = () => { list.forEach((i) => (all.checked ? chosen.add(i.id) : chosen.delete(i.id))); render(); };
    const again = el.querySelector('[data-resend]');
    if (again) again.onchange = () => { resend = again.checked; render(); };
    el.querySelectorAll('[data-look]').forEach((b) => (b.onclick = () => mailPreview('id=' + b.dataset.look, 'Vorschau ' + issued.find((i) => i.id === Number(b.dataset.look)).number)));
    const toSettings = el.querySelector('[data-settings]');
    if (toSettings) toSettings.onclick = () => { el.close(); go('settings'); };
    el.querySelector('[data-test]').onclick = (e) => safe(async () => {
      e.target.disabled = true;
      try {
        const r = await api('/api/mail-test', { id: selected[0].id });
        toast(`Testmail zu ${selected[0].number} an ${r.to} gesendet.`);
      } finally { e.target.disabled = false; }
    });
    el.querySelector('[data-go]').onclick = () => confirm(mailable, resend, render);
  };
  render();
}

function confirm(items, resend, back, kind = 'invoice') {
  const cfg = store.data.settings;
  const reminder = kind === 'reminder';
  const el = dialog(reminder ? 'Zahlungserinnerungen bestätigen' : 'Versand bestätigen', html`
    <p><strong>${items.length} ${reminder ? (items.length === 1 ? 'Zahlungserinnerung' : 'Zahlungserinnerungen') : `E-Mail${items.length === 1 ? '' : 's'}`}</strong> mit ${reminder ? 'der ursprünglichen Rechnung' : 'Rechnung'} als PDF an ${items.length} Empfänger senden?</p>
    <ul class="small" style="margin:0;padding-left:18px">
      <li>Absender: ${cfg.mail_from_name} &lt;${cfg.mail_from}&gt;</li>
      ${cfg.mail_bcc ? html`<li>Kopie jeder E-Mail an ${cfg.mail_bcc}</li>` : ''}
      <li>Pause zwischen den E-Mails: ${cfg.mail_pause} Sekunden (Dauer etwa ${Math.max(1, Math.ceil((items.length * Number(cfg.mail_pause || 0)) / 60))} Minute(n))</li>
      ${resend ? html`<li>${reminder ? 'Auch Rechnungen mit kürzlich gesendeter oder unklar zugestellter Erinnerung werden erinnert.' : 'Bereits versendete Rechnungen werden erneut verschickt.'}</li>` : ''}
      ${reminder ? html`<li>Neue Zahlungsfrist: ${cfg.reminder_terms} Tage ab heute.</li>` : ''}</ul>
    <p class="small muted">Der Versand läuft im Hintergrund weiter, auch wenn dieses Fenster geschlossen wird. Jede E-Mail wird protokolliert.</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-back>Zurück</button>
      <button type="button" class="btn primary" data-start>${icon('mail', 16)} Jetzt senden</button></div>`);
  el.querySelector('[data-back]').onclick = back;
  el.querySelector('[data-start]').onclick = () => safe(async () => {
    await api('/api/mail-send', { ids: items.map((i) => i.id), resend, kind });
    progress();
  });
}

// ---------------------------------------------------------------- Zahlungserinnerungen

export async function reminderDialog(resend = false, keep = null) {
  const status = await api('/api/mail-status');
  if (status.job.state === 'running') return progress();
  const data = await api('/api/reminders' + (resend ? '?resend=1' : ''));
  const cfg = store.data.settings;
  const items = data.items;
  // beim Umschalten die bisherige Auswahl behalten – neu freigegebene Zeilen werden nicht automatisch gewählt
  const chosen = keep ? new Set([...keep].filter((id) => items.some((r) => r.id === id && !r.reason)))
    : new Set(items.filter((r) => !r.reason).map((r) => r.id));
  const stale = !data.last_import || (Date.now() - new Date(data.last_import).getTime()) / 86400000 > 7;
  const render = () => {
    const selected = items.filter((r) => chosen.has(r.id) && !r.reason);
    const missing = [!cfg.mail_from || !cfg.smtp_user ? 'Absenderadresse und Benutzername' : '', !status.password ? 'Passwort' : ''].filter(Boolean);
    const el = dialog('Zahlungserinnerungen', html`
      ${missing.length ? html`<p class="notice warn">Für den Versand fehlt in den Stammdaten: ${missing.join(' und ')}.
        <button type="button" class="link" data-settings>Zu den Einstellungen</button></p>` : ''}
      <p class="notice ${stale ? 'warn' : ''}">${data.last_import ? html`Letzter Kontoauszug importiert am <strong>${dateTime(data.last_import)}</strong>${data.last_booked ? ` (Umsätze bis ${date(data.last_booked)})` : ''}.` : 'Noch kein Kontoauszug importiert.'}
        Vor dem Erinnern den aktuellen Kontoauszug importieren und zuordnen – sonst bekommen Mitglieder eine Erinnerung, die schon bezahlt haben.</p>
      ${data.open_incoming ? html`<p class="notice warn"><strong>${data.open_incoming} Zahlungseingang${data.open_incoming === 1 ? ' ist' : 'e sind'} noch nicht zugeordnet.</strong> Erinnerungen mit passender Rechnungsnummer oder passendem Betrag und Namen sind gesperrt – trotzdem erst im Buchungsplatz zuordnen.</p>` : ''}
      <p class="small muted">Erinnert werden offene Rechnungen frühestens ${cfg.reminder_after} Tage nach Fälligkeit, danach höchstens alle ${cfg.reminder_gap} Tage; neue Frist ${cfg.reminder_terms} Tage. Zahlungseingänge, die schon im Kontoauszug stehen, sperren die Erinnerung.</p>
      ${items.length ? table(['', 'Rechnung', 'Empfänger', 'Fällig', { label: 'Offen', className: 'num' }, 'Bisher', 'Stand', ''],
        items.map((r) => html`<tr><td><input type="checkbox" data-pick="${r.id}" ${chosen.has(r.id) && !r.reason ? 'checked' : ''} ${r.reason ? 'disabled' : ''} aria-label="${r.number} auswählen"></td>
          <td><strong style="font-weight:600">${r.number}</strong><span class="sub">${r.kind === 'dues' ? 'Beitrag' : 'Rechnung'}</span></td>
          <td>${r.recipient}<span class="sub">${r.to || 'keine E-Mail'}</span></td>
          <td>${date(r.due)}<span class="sub">seit ${r.overdue} Tagen</span></td><td class="num">${eur(r.open)}</td>
          <td>${r.level > 1 ? `${r.level - 1}× erinnert · ${shortDate(r.last_reminder)}` : '—'}<span class="sub">${r.mailed ? `Rechnung per E-Mail ${shortDate(r.mailed)}` : 'Rechnung nicht per E-Mail'}</span></td>
          <td>${r.reason ? html`<span class="small muted">${r.reason}</span>` : badge(`${r.level}. Erinnerung bereit`, 'ok')}</td>
          <td><button type="button" class="btn small ghost" data-look="${r.id}">${icon('eye', 14)} Vorschau</button></td></tr>`))
        : empty('Keine überfälligen Rechnungen', 'Alle fälligen Rechnungen sind bezahlt.')}
      <label class="check"><input type="checkbox" data-resend ${resend ? 'checked' : ''}> Sperren lockern: auch erinnern, wenn die letzte Erinnerung kürzlich war, unklar zugestellt wurde oder die Rechnung nicht per E-Mail ging (z. B. persönlich übergeben) – einzeln auswählen</label>
      <div class="bulkbar"><strong>${selected.length} Erinnerung${selected.length === 1 ? '' : 'en'} · ${eur(selected.reduce((s, r) => s + r.open, 0))} offen</strong>
        <button type="button" class="btn" data-test ${selected.length && !missing.length ? '' : 'disabled'}>Testmail an mich</button>
        <button type="button" class="btn primary" data-go ${selected.length && !missing.length ? '' : 'disabled'}>${icon('mail', 16)} ${selected.length} Erinnerung${selected.length === 1 ? '' : 'en'} senden</button></div>`, { wide: true });
    el.querySelectorAll('[data-pick]').forEach((c) => (c.onchange = () => { c.checked ? chosen.add(Number(c.dataset.pick)) : chosen.delete(Number(c.dataset.pick)); render(); }));
    el.querySelector('[data-resend]').onchange = (e) => safe(() => reminderDialog(e.target.checked, chosen));
    el.querySelectorAll('[data-look]').forEach((b) => (b.onclick = () => mailPreview(`id=${b.dataset.look}&reminder=1`, 'Vorschau Erinnerung ' + items.find((r) => r.id === Number(b.dataset.look)).number)));
    const toSettings = el.querySelector('[data-settings]');
    if (toSettings) toSettings.onclick = () => { el.close(); go('settings'); };
    el.querySelector('[data-test]').onclick = (e) => safe(async () => {
      e.target.disabled = true;
      try {
        const r = await api('/api/mail-test', { id: selected[0].id, reminder: '1' });
        toast(`Test-Erinnerung zu ${selected[0].number} an ${r.to} gesendet.`);
      } finally { e.target.disabled = false; }
    });
    el.querySelector('[data-go]').onclick = () => confirm(selected, resend, render, 'reminder');
  };
  render();
}

const STATE_LABEL = { running: 'Versand läuft …', done: 'Versand abgeschlossen', cancelled: 'Versand abgebrochen', aborted: 'Versand gestoppt' };

async function progress() {
  const { job } = await api('/api/mail-status');
  const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
  const running = job.state === 'running';
  const el = dialog(STATE_LABEL[job.state] || 'Versand', html`
    <div class="progress" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></div>
    <p><strong>${job.done} von ${job.total}</strong> · ${job.sent} versendet · ${job.failed} fehlgeschlagen${job.unknown ? ` · ${job.unknown} Zustellung unklar` : ''}${job.skipped?.length ? ` · ${job.skipped.length} übersprungen` : ''}</p>
    ${job.unknown ? html`<p class="notice warn">Bei „Zustellung unklar“ brach die Verbindung während der Übertragung ab. Bitte im Postfach (Kopie/Gesendet) prüfen, ob die E-Mail angekommen ist, bevor sie erneut gesendet wird.</p>` : ''}
    ${running && job.current ? html`<p class="small muted">Gerade: ${job.current}</p>` : ''}
    ${job.state === 'aborted' ? html`<p class="notice warn">Der Versand wurde nach einem Verbindungs- oder Anmeldefehler gestoppt. Bitte die Stammdaten prüfen und die übrigen Rechnungen erneut senden.</p>` : ''}
    ${job.errors?.length ? table(['Rechnung', 'Empfänger', 'Fehler'], job.errors.map((e) => html`<tr><td>${e.number}</td><td>${e.to}</td><td>${e.error}</td></tr>`)) : ''}
    ${!running && job.skipped?.length ? html`<p class="small muted">Übersprungen: ${job.skipped.map((s, i) => html`${i ? ', ' : ''}${s.number} (${s.reason})`)}</p>` : ''}
    <div class="form-actions">${running ? html`<button type="button" class="btn warn" data-cancel>Versand abbrechen</button>` : ''}
      <button type="button" class="btn ${running ? 'ghost' : 'primary'}" data-done>${running ? 'Im Hintergrund weiterlaufen lassen' : 'Fertig'}</button></div>`);
  el.querySelector('[data-done]').onclick = () => { el.close(); safe(refresh); };
  const cancel = el.querySelector('[data-cancel]');
  if (cancel) cancel.onclick = () => safe(async () => { await api('/api/mail-cancel', {}); });
  if (running) setTimeout(() => { if (el.open && el.querySelector('.progress')) safe(progress); }, 900);
  else safe(refresh);
}
