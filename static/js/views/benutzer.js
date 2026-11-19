// Verwaltung → Benutzer & Rollen (ab 0.34, nur Serverbetrieb): Personen anlegen, Rollen vergeben, Rechte je Rolle einstellen.
// Der Server prüft jede Anfrage selbst (rollen.py); diese Seite zeigt und ändert nur die Zuordnung. Passwörter werden nie angezeigt.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { store, refresh } from '../store.js';
import { pageHead, icon, safe, toast, dialog, table } from '../ui.js';

let data = null;   // /api/benutzer: users, roles, areas, levels
const LEVEL = { '': 'kein Zugriff', lesen: 'lesen', schreiben: 'lesen und ändern' };
const SHORT = { '': '–', lesen: 'lesen', schreiben: 'ändern' };
const COLUMN = { mitglieder: 'Mitglieder', verwaltung: 'Benutzer' };   // kurze Spaltenköpfe, damit die Tabelle auch auf dem Laptop passt

const roleName = (id) => data.roles.find((r) => r.id === id)?.name || '?';

// Protokoll der Verwaltung in Worten: wer hat wem welche Rolle gegeben, welche Rechte geändert
const when = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)} ${iso.slice(11, 16)}`;
const rightsText = (rights) => data.areas.filter((a) => rights?.[a.id]).map((a) => `${COLUMN[a.id] || a.label} ${SHORT[rights[a.id]]}`).join(', ') || 'keine Rechte';
const rolesText = (roles) => (roles?.length ? roles.join(', ') : 'keine Rolle');
function describe(entry) {
  const d = entry.detail || {};
  if (d.rolle) return `${d.rolle}${d.rechte ? ': ' + rightsText(d.rechte) : ''}${d.vorher ? ` (vorher: ${rightsText(d.vorher.rechte)})` : ''}`;
  if (!d.benutzer) return '';
  const parts = [d.benutzer];
  if (Array.isArray(d.rollen)) parts.push(rolesText(d.rollen) + (d.vorher ? ` (vorher: ${rolesText(d.vorher.rollen)})` : ''));
  if (d.aktiv === false) parts.push('deaktiviert');
  if (d.passwort_neu) parts.push('neues Passwort');
  return parts.join(' · ');
}

function userDialog(u = null) {
  const el = dialog(u ? `Person bearbeiten · ${u.name}` : 'Person anlegen', html`<form id="user-form" style="display:flex;flex-direction:column;gap:14px" autocomplete="off">
    <label class="field">Name (Anmeldename)<input name="name" required maxlength="80" autocapitalize="off" autocorrect="off" spellcheck="false"
      value="${u ? u.name : ''}" ${u ? 'readonly' : ''} placeholder="Vorname Nachname"></label>
    <fieldset class="field"><legend>Rollen</legend>
      ${data.roles.map((r) => html`<label class="check"><input type="checkbox" name="role" value="${r.id}" ${u?.roles.includes(r.id) ? 'checked' : ''}> ${r.name}</label>`)}
      <span class="small muted">Mehrere Rollen sind möglich – es gilt je Bereich das höchste Recht. Ohne Rolle sieht die Person nur die Startseite.</span></fieldset>
    <label class="field">${u ? 'Neues Passwort setzen (leer lassen = unverändert)' : 'Passwort für die erste Anmeldung'}
      <input name="password" type="password" autocomplete="new-password" minlength="12" maxlength="256" ${u ? '' : 'required'}>
      <span class="small muted">Mindestens 12 Zeichen. Die Person ändert es danach selbst unter Einstellungen → Anmeldung.${u ? ' Ein neues Passwort meldet sie überall ab.' : ''}</span></label>
    <label class="check"><input type="checkbox" name="active" ${!u || u.active ? 'checked' : ''}> Aktiv (darf sich anmelden)</label>
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`);
  const form = el.querySelector('#user-form');
  el.querySelector('[data-cancel]').onclick = () => el.close();
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const f = form.elements;
      await api('/api/benutzer', { id: u ? u.id : null, name: f.namedItem('name').value,
        roles: [...form.querySelectorAll('input[name="role"]:checked')].map((c) => Number(c.value)),
        password: f.namedItem('password').value, active: f.namedItem('active').checked });
      el.close();
      data = null;
      await refresh();
      toast(u ? 'Person gespeichert.' : 'Person angelegt.');
    });
  };
}

function roleDialog(r = null) {
  const el = dialog(r ? `Rolle bearbeiten · ${r.name}` : 'Rolle anlegen', html`<form id="role-form" style="display:flex;flex-direction:column;gap:14px">
    <label class="field">Bezeichnung<input name="name" required maxlength="40" autocomplete="off" value="${r ? r.name : ''}" placeholder="z. B. Kassenprüfer"></label>
    ${table(['Bereich', 'Recht'], data.areas.map((a) => html`<tr><td><strong>${a.label}</strong><br><span class="small muted">${a.hint}</span></td>
      <td><select name="area-${a.id}" aria-label="Recht für ${a.label}">${data.levels.map((l) => html`<option value="${l}" ${(r?.rights[a.id] || '') === l ? 'selected' : ''}>${LEVEL[l]}</option>`)}</select></td></tr>`))}
    <div class="form-actions">
      ${r && !r.users ? html`<button type="button" class="btn ghost danger" data-delete>Rolle löschen</button>` : ''}
      <button type="button" class="btn ghost" data-cancel>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`, { wide: true });
  const form = el.querySelector('#role-form');
  el.querySelector('[data-cancel]').onclick = () => el.close();
  const done = async (text) => { el.close(); data = null; await refresh(); toast(text); };
  el.querySelector('[data-delete]')?.addEventListener('click', () => safe(async () => {
    await api('/api/rolle-loeschen', { id: r.id });
    await done('Rolle gelöscht.');
  }));
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const rights = {};
      for (const a of data.areas) rights[a.id] = form.elements.namedItem('area-' + a.id).value;
      await api('/api/rolle', { id: r ? r.id : null, name: form.elements.namedItem('name').value, rights });
      await done(r ? 'Rolle gespeichert – gilt ab der nächsten Anfrage der betroffenen Personen.' : 'Rolle angelegt.');
    });
  };
}

function show(main) {
  mount(main, html`<div class="page">
    ${pageHead('Verwaltung', 'Benutzer & Rollen', html`
      <button type="button" class="btn" data-new-role>Rolle anlegen</button>
      <button type="button" class="btn primary" data-new-user>${icon('users', 16)} Person anlegen</button>`)}
    <section class="panel"><h2>Personen</h2>
      ${table(['Name', 'Rollen', 'Stand', ''], data.users.map((u) => html`<tr>
        <td><strong>${u.name}</strong>${u.name === store.user ? html` <span class="small muted">(du)</span>` : ''}</td>
        <td>${u.roles.length ? u.roles.map((id) => html`<span class="badge navy">${roleName(id)}</span> `) : html`<span class="small muted">keine Rolle</span>`}</td>
        <td>${!u.active ? html`<span class="badge warn">deaktiviert</span>` : u.has_password ? html`<span class="badge ok">aktiv</span>` : html`<span class="badge warn">kein Passwort</span>`}</td>
        <td class="num"><button type="button" class="btn small" data-user="${u.id}">Bearbeiten</button></td></tr>`))}
    </section>
    <section class="panel"><h2>Rollen und Rechte</h2>
      <p class="small muted">Jede Rolle hat je Bereich ein Recht – zum Ändern auf den Namen der Rolle klicken. „ändern“ schließt „lesen“ ein. Mindestens eine aktive Person muss „Benutzer“ ändern dürfen.</p>
      ${table(['Rolle', ...data.areas.map((a) => COLUMN[a.id] || a.label), { label: 'Personen', className: 'num' }], data.roles.map((r) => html`<tr>
        <td><button type="button" class="link-button" data-role="${r.id}" title="Rolle bearbeiten"><strong>${r.name}</strong></button></td>
        ${data.areas.map((a) => html`<td><span class="${r.rights[a.id] ? '' : 'muted'}">${SHORT[r.rights[a.id] || '']}</span></td>`)}
        <td class="num">${r.users}</td></tr>`))}
    </section>
    <section class="panel"><h2>Letzte Änderungen</h2>
      <p class="small muted">Wer hat wem welche Rolle gegeben und welche Rechte geändert – die letzten 50 Einträge, ohne Passwörter.</p>
      ${data.audit.length ? table(['Wann', 'Wer', 'Was', 'Einzelheiten'], data.audit.map((a) => html`<tr>
        <td class="nowrap">${when(a.time)}</td><td>${a.actor}</td><td>${a.action}</td><td class="small">${describe(a)}</td></tr>`))
        : html`<p class="muted">Noch keine Änderungen.</p>`}
    </section>
  </div>`);
  main.querySelector('[data-new-user]').onclick = () => userDialog();
  main.querySelector('[data-new-role]').onclick = () => roleDialog();
  main.querySelectorAll('[data-user]').forEach((b) => (b.onclick = () => userDialog(data.users.find((u) => u.id === Number(b.dataset.user)))));
  main.querySelectorAll('[data-role]').forEach((b) => (b.onclick = () => roleDialog(data.roles.find((r) => r.id === Number(b.dataset.role)))));
}

export function benutzer(main) {
  if (data) return show(main);
  mount(main, html`<div class="page">${pageHead('Verwaltung', 'Benutzer & Rollen')}<p class="muted">Lädt …</p></div>`);
  safe(async () => {
    data = await api('/api/benutzer');
    if (store.page === 'users') show(main);
  });
}
