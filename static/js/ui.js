// Gemeinsame Bausteine: Dialog, Meldungen, Kennzeichen, Tabellen, Symbole.
import { html, raw, mount } from './html.js';
import { refresh } from './store.js';
import { api, fileToBase64 } from './api.js';

const modal = () => document.getElementById('modal');

/** Meldungsfeld vor dem Neuaufbau eines Dialogs zurück in die Seite holen. */
function rescueToast(el) {
  const t = document.getElementById('toast');
  if (t && el.contains(t)) document.body.append(t);
}

/** Dialog öffnen. content: html`…`; returns das Dialog-Element. */
export function dialog(title, content, { wide = false } = {}) {
  const el = modal();
  rescueToast(el);
  el.className = wide ? 'wide' : '';
  mount(el, html`<div class="dialog-head"><h2 id="modal-title">${title}</h2>
    <button type="button" class="btn ghost icon-btn" data-close aria-label="Schließen">${icon('close')}</button></div>
    <div class="dialog-body">${content}</div>`);
  el.querySelector('[data-close]').onclick = () => el.close();
  if (!el.open) el.showModal();
  return el;
}

export function closeDialog() {
  const el = modal();
  if (el.open) el.close();
}

/** Oberste offene Ebene schließen (erst Liste/Vorschau, dann Dialog). Esc; true = geschlossen. */
export function closeTop() {
  const top = [document.getElementById('preview'), modal()].find((d) => d && d.open);
  if (!top) return false;
  top.close();
  return true;
}

/** Zweite Ebene über einem offenen Dialog (z. B. Vorschau). */
export function overlay(title, content, { wide = true } = {}) {
  const el = document.getElementById('preview');
  rescueToast(el);
  el.className = wide ? 'wide' : '';
  mount(el, html`<div class="dialog-head"><h2 id="preview-title">${title}</h2>
    <button type="button" class="btn ghost icon-btn" data-close aria-label="Schließen">${icon('close')}</button></div>
    <div class="dialog-body">${content}</div>`);
  el.querySelectorAll('[data-close]').forEach((b) => (b.onclick = () => el.close()));
  if (!el.open) el.showModal();
  return el;
}

/** E-Mail so, wie sie verschickt wird, plus Link zum PDF-Anhang. query z. B. „id=3“ oder „dues=5&year=2027“. */
export function mailPreview(query, title = 'Vorschau E-Mail') {
  return overlay(title, html`<iframe class="mail-frame" sandbox title="E-Mail-Vorschau" src="/api/mail-preview?${query}"></iframe>
    <div class="form-actions"><a class="btn" href="/api/preview.pdf?${query}" target="_blank" rel="noopener">${icon('file', 16)} PDF-Anhang öffnen</a>
      <button type="button" class="btn primary" data-close>Schließen</button></div>`);
}

let toastTimer = null;
export function toast(message, error = false) {
  const el = document.getElementById('toast');
  // Über offenen Dialogen anzeigen (sonst verdeckt die oberste Ebene die Meldung)
  const host = [document.getElementById('preview'), modal()].find((d) => d && d.open) || document.body;
  if (el.parentElement !== host) host.append(el);
  el.textContent = message;
  el.className = 'show' + (error ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = ''), error ? 9000 : 6000);
}

/** Aktion ausführen und Fehler als Meldung zeigen. */
export async function safe(fn) {
  try {
    return await fn();
  } catch (e) {
    toast(e.message || String(e), true);
    return undefined;
  }
}

let uploads = 0;

/** Importdatei an den Server schicken (Vorschau). Ist sie mit einem Kennwort geschützt (Excel „Kennwort zum Öffnen“), fragt ein Feld
 * in box danach und schickt die Datei mit dem Kennwort erneut. Das Kennwort gilt nur für diese Anfrage: nie gespeichert, nie im Protokoll.
 * Liefert null, wenn inzwischen eine andere Datei gewählt wurde (die ältere Antwort wird verworfen – sonst gehörte die Vorschau zur falschen Datei). */
export async function uploadProtected(url, file, box, extra = {}) {
  const id = String(++uploads);
  box.dataset.upload = id;
  const current = () => box.dataset.upload === id;
  const content = await fileToBase64(file);
  const send = (password) => api(url, { ...extra, filename: file.name, content, ...(password ? { password } : {}) });
  try {
    const result = await send();
    return current() ? result : null;
  } catch (e) {
    if (!current()) return null;
    if (!e.password) throw e;
    return new Promise((resolve, reject) => {
      const ask = (message) => {
        mount(box, html`<form class="notice warn pw-box" autocomplete="off"><p>${message}</p>
          <label class="field">Kennwort der Datei „${file.name}“<input type="password" name="pw" required autocomplete="off" autocapitalize="off"
            autocorrect="off" spellcheck="false"></label>
          <div class="form-actions"><button type="submit" class="btn primary">Datei öffnen</button></div>
          <p class="small muted">Das Kennwort wird nur zum Öffnen der Datei verwendet und nicht gespeichert.</p></form>`);
        const form = box.querySelector('form');
        form.pw.focus();
        form.onsubmit = (ev) => {
          ev.preventDefault();
          const password = form.pw.value;
          form.pw.value = '';
          send(password).then((r) => {
            if (!current()) return resolve(null);
            mount(box, '');
            resolve(r);
          }).catch((err) => {
            if (!current()) return resolve(null);
            if (err.password) return ask(err.message);
            mount(box, '');   // anderer Fehler (z. B. Kopfzeile nicht erkannt): Kennwortfeld weg, Meldung zeigt safe()
            reject(err);
          });
        };
      };
      ask(e.message);
    });
  }
}

/** Sicherheitsabfrage; nach Bestätigung Aktion, Neuladen und Meldung. */
export function confirmAction(title, text, action, { label = 'Bestätigen', done = 'Änderung gespeichert.' } = {}) {
  const el = dialog(title, html`<p>${text}</p>
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button>
    <button type="button" class="btn primary" data-ok>${label}</button></div>`);
  el.querySelector('[data-cancel]').onclick = () => el.close();
  el.querySelector('[data-ok]').onclick = () => safe(async () => {
    await action();
    el.close();
    await refresh();
    toast(done);
  });
}

export const badge = (label, kind = '') => html`<span class="badge ${kind}">${label}</span>`;

export const empty = (title, text) => html`<div class="empty"><strong>${title}</strong>${text}</div>`;

export function table(headers, rows, { className = '' } = {}) {
  return html`<div class="tablewrap"><table class="${className}"><thead><tr>${headers.map((h) =>
    h && typeof h === 'object' && 'label' in h ? html`<th scope="col" class="${h.className || ''}">${h.label}</th>` : html`<th scope="col">${h}</th>`)}</tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

export function pageHead(eyebrow, title, actions = '') {
  return html`<header class="pagehead"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1></div>
    ${actions ? html`<div class="actions">${actions}</div>` : ''}</header>`;
}

export function meter(ratio, { warn = false, navy = false } = {}) {
  const pct = Math.max(0, Math.min(100, Math.round((ratio || 0) * 100)));
  return html`<div class="meter ${navy ? 'navy' : ''}"><span class="${warn ? 'warn' : ''}" style="width:${pct}%"></span></div>`;
}

// ---- Symbole (Linien, 24er Raster) ----
const ICONS = {
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
  bank: '<path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h10"/>',
  chart: '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M21 20H3"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M18 14.6c1.8.7 3 2.5 3.5 5.4"/>',
  file: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6"/><path d="M10 17h6"/>',
  book: '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12l4 4 10-10"/>',
  arrow: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
  upload: '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M5 20h14"/>',
  download: '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/><path d="M5 20h14"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
  print: '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v7H7z"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13 7l4 4"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  inbox: '<path d="M3 13h5l2 3h4l2-3h5"/><path d="M5 5h14l2 8v6H3v-6z"/>',
  scale: '<path d="M12 4v16"/><path d="M5 8h14"/><path d="M5 8l-3 7h6z"/><path d="M19 8l-3 7h6z"/>',
  cash: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9.5v5M18 9.5v5"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17.5l9 5 9-5"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.5v.01"/>',
  coins: '<ellipse cx="12" cy="7" rx="7" ry="3"/><path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7"/><path d="M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5"/>',
  trend: '<path d="M4 4v16h16"/><path d="M7.5 14.5l3.5-3.5 3 2.5 5-6.5"/>',
  right: '<path d="M9 6l6 6-6 6"/>',
  map: '<path d="M9 4l6 2 5-2v14l-5 2-6-2-5 2V6z"/><path d="M9 4v14M15 6v14"/>',
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/>',
};

export function icon(name, size = 18) {
  return raw(`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`);
}
