// Verwaltung → Benutzer & Rollen (ab 0.34, nur Serverbetrieb): Personen anlegen, Rollen vergeben, Rechte je Rolle einstellen.
// Der Server prüft jede Anfrage selbst (rollen.py); diese Seite zeigt und ändert nur die Zuordnung. Passwörter werden nie angezeigt –
// einzige Ausnahme im Keycloak-Betrieb (ab 0.36): das Startpasswort einer Person genau einmal direkt aus der Antwort des Servers.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { store, refresh } from '../store.js';
import { pageHead, icon, safe, toast, dialog, closeDialog, table } from '../ui.js';

let data = null;   // /api/benutzer: users, roles, kreisverbaende, areas, levels
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
  if (d.verknuepft) parts.push('mit vorhandener Keycloak-Person verknüpft');
  return parts.join(' · ');
}

const fullName = (u) => [u.vorname, u.nachname].filter(Boolean).join(' ');
const kvName = (code) => (code ? data.kreisverbaende.find((k) => k.code === code)?.name || code : 'Landesebene');
// Status wie der Server ihn prüft (rollen.GILT): inaktiv, abgelaufen („gültig bis“ vorbei) oder aktiv
const status = (u) => (!u.active ? html`<span class="badge warn">inaktiv</span>` : u.abgelaufen ? html`<span class="badge danger">abgelaufen</span>` : html`<span class="badge ok">aktiv</span>`);

const kc = () => store.auth === 'keycloak';
const kcStatus = (u) => (u.keycloak_verbunden ? html`<span class="badge ok">verbunden</span>` : html`<span class="badge warn">nicht mit Keycloak verbunden</span>`);

/** Startpasswort groß anzeigen – es steht nur in dieser einen Antwort des Servers und wird nirgends gespeichert. */
function startPassword(name, password) {
  const el = dialog(`Startpasswort · ${name}`, html`<div style="display:flex;flex-direction:column;gap:14px">
    <p class="kc-startpasswort">${password}</p>
    <p class="notice warn">Wird nicht gespeichert und nur jetzt angezeigt – bitte der Person persönlich weitergeben. Beim ersten Anmelden mit dem
      JU-Konto ändert sie es und richtet die Authenticator-App ein.</p>
    <div class="form-actions"><button type="button" class="btn" data-copy>Kopieren</button><button type="button" class="btn primary" data-done>Fertig</button></div></div>`);
  el.querySelector('[data-done]').onclick = () => el.close();
  el.addEventListener('close', () => el.querySelector('.kc-startpasswort')?.remove(), { once: true });   // nicht im geschlossenen Dialog stehen lassen
  el.querySelector('[data-copy]').onclick = () => navigator.clipboard.writeText(password)
    .then(() => toast('Kopiert.'), () => toast('Kopieren nicht möglich – bitte abschreiben.', true));
}

/** Antwort des Servers nach Speichern/Abgleichen: Startpasswort zeigen bzw. Keycloak-Fehler melden. */
async function afterSave(name, r, text) {
  data = await api('/api/benutzer');   // vor dem Neuzeichnen laden: die Liste steht sofort mit dem neuen Stand da
  await refresh();
  if (r.startpasswort) startPassword(name, r.startpasswort);
  if (r.keycloak_fehler) toast(`Gespeichert, aber nicht mit Keycloak verbunden: ${r.keycloak_fehler}`, true);
  else if (text && !r.startpasswort) toast(text);   // der Startpasswort-Dialog sagt genug; ein Hinweis würde das Passwort verdecken
}

/** Keycloak-Aktionen zu einer Person (Abgleichen, neues Startpasswort, App zurücksetzen) – jeweils mit Bestätigung im Server. */
function kcAction(u, path, text) {
  return safe(async () => {
    const r = await api(path, { id: u.id });
    closeDialog();
    await afterSave(u.name, r, text);
  });
}

function userDialog(u = null) {
  const v = (key) => (u ? u[key] ?? '' : '');
  const el = dialog(u ? `Person bearbeiten · ${u.name}` : 'Person anlegen', html`<form id="user-form" style="display:flex;flex-direction:column;gap:14px" autocomplete="off">
    <div class="formgrid">
      <label class="field">Vorname<input name="vorname" required maxlength="60" value="${v('vorname')}"></label>
      <label class="field">Nachname<input name="nachname" required maxlength="60" value="${v('nachname')}"></label>
      ${u ? html`<div class="field">Benutzername<span><strong>${u.name}</strong> <span class="small muted">(Anmeldename, nicht änderbar)</span></span></div>`
        : html`<label class="field">Benutzername (Anmeldename)<input name="name" required minlength="2" maxlength="40" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="z. B. erika.muster">
          <span class="small muted">Buchstaben ohne Umlaute, Ziffern, Punkt, - und _. Später nicht änderbar.</span></label>`}
      <label class="field">E-Mail<input name="email" type="email" required maxlength="254" autocapitalize="off" autocorrect="off" spellcheck="false" value="${v('email')}"></label>
      <label class="field">Telefon (optional)<input name="telefon" type="tel" maxlength="30" value="${v('telefon')}"></label>
      <label class="field">Funktion / Amt (optional)<input name="funktion" maxlength="80" value="${v('funktion')}" placeholder="z. B. Landesschatzmeister"></label>
      <label class="field">Kreisverband<select name="kreisverband"><option value="">Landesebene</option>
        ${data.kreisverbaende.map((k) => html`<option value="${k.code}" ${v('kreisverband') === k.code ? 'selected' : ''}>${k.name}</option>`)}</select></label>
      <label class="field">Zugang gültig bis (optional)<input name="gueltig_bis" type="date" value="${v('gueltig_bis')}">
        <span class="small muted">Leer = unbefristet. Nach diesem Tag gilt die Person als inaktiv.</span></label>
    </div>
    <fieldset class="field"><legend>Rollen</legend>
      ${data.roles.map((r) => html`<label class="check"><input type="checkbox" name="role" value="${r.id}" ${u?.roles.includes(r.id) ? 'checked' : ''}> ${r.name}</label>`)}
      <span class="small muted">Mehrere Rollen sind möglich – es gilt je Bereich das höchste Recht. Ohne Rolle sieht die Person nur die Startseite.</span></fieldset>
    ${kc() ? (u ? '' : html`<p class="small muted">Das Programm legt die Person auch im JU-Konto (Keycloak) an und zeigt danach einmal ein Startpasswort.</p>`)
      : html`<label class="field">${u ? 'Neues Passwort setzen (leer lassen = unverändert)' : 'Passwort für die erste Anmeldung'}
      <input name="password" type="password" autocomplete="new-password" minlength="12" maxlength="256" ${u ? '' : 'required'}>
      <span class="small muted">Mindestens 12 Zeichen. Die Person ändert es danach selbst unter Einstellungen → Anmeldung.${u ? ' Ein neues Passwort meldet sie überall ab.' : ''}</span></label>`}
    <label class="check"><input type="checkbox" name="totp_pflicht" ${!u || u.totp_pflicht ? 'checked' : ''}> Authenticator-App Pflicht</label>
    <label class="check"><input type="checkbox" name="active" ${!u || u.active ? 'checked' : ''}> Aktiv (darf sich anmelden)</label>
    ${kc() && u ? html`<fieldset class="field"><legend>JU-Konto (Keycloak)</legend><span>${kcStatus(u)}</span>
      <div class="form-actions" style="justify-content:flex-start">
        <button type="button" class="btn small" data-kc-sync>Mit Keycloak abgleichen</button>
        ${u.keycloak_verbunden ? html`<button type="button" class="btn small" data-kc-password>Neues Startpasswort</button>
          <button type="button" class="btn small" data-kc-app>App zurücksetzen</button>` : ''}</div>
      <span class="small muted">Neues Startpasswort und App zurücksetzen melden die Person überall ab.</span></fieldset>` : ''}
    <div class="form-actions"><button type="button" class="btn ghost" data-cancel>Abbrechen</button><button type="submit" class="btn primary">Speichern</button></div></form>`, { wide: true });
  const form = el.querySelector('#user-form');
  el.querySelector('[data-cancel]').onclick = () => el.close();
  el.querySelector('[data-kc-sync]')?.addEventListener('click', () => kcAction(u, '/api/benutzer-abgleichen', 'Mit Keycloak abgeglichen.'));
  el.querySelector('[data-kc-password]')?.addEventListener('click', () => kcAction(u, '/api/benutzer-startpasswort'));
  el.querySelector('[data-kc-app]')?.addEventListener('click', () => kcAction(u, '/api/benutzer-app-zuruecksetzen', 'Authenticator-App zurückgesetzt – beim nächsten Anmelden neu einrichten.'));
  form.onsubmit = (e) => {
    e.preventDefault();
    safe(async () => {
      const f = form.elements;
      const value = (key) => f.namedItem(key)?.value ?? '';
      const name = u ? u.name : value('name');
      const r = await api('/api/benutzer', { id: u ? u.id : null, name,
        vorname: value('vorname'), nachname: value('nachname'), email: value('email'), telefon: value('telefon'), funktion: value('funktion'),
        kreisverband: value('kreisverband'), gueltig_bis: value('gueltig_bis'), totp_pflicht: f.namedItem('totp_pflicht').checked,
        roles: [...form.querySelectorAll('input[name="role"]:checked')].map((c) => Number(c.value)),
        password: kc() ? '' : value('password'), active: f.namedItem('active').checked });
      el.close();
      await afterSave(name, r, u ? 'Person gespeichert.' : 'Person angelegt.');
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
      ${table(['Name', 'Benutzername', 'Funktion', 'Kreisverband', 'Rollen', 'Status', 'App-Pflicht', ...(kc() ? ['Keycloak'] : []), ''], data.users.map((u) => html`<tr>
        <td><strong>${fullName(u) || '–'}</strong>${u.name === store.user ? html` <span class="small muted">(du)</span>` : ''}</td>
        <td>${u.name}</td><td>${u.funktion || ''}</td><td>${kvName(u.kreisverband)}</td>
        <td>${u.roles.length ? u.roles.map((id) => html`<span class="badge navy">${roleName(id)}</span> `) : html`<span class="small muted">keine Rolle</span>`}</td>
        <td>${status(u)}</td><td>${u.totp_pflicht ? 'ja' : 'nein'}</td>
        ${kc() ? html`<td>${kcStatus(u)}${u.keycloak_verbunden ? '' : html` <button type="button" class="btn small" data-kc-sync="${u.id}">Mit Keycloak abgleichen</button>`}</td>` : ''}
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
  main.querySelectorAll('[data-kc-sync]').forEach((b) => (b.onclick = () => kcAction(data.users.find((u) => u.id === Number(b.dataset.kcSync)), '/api/benutzer-abgleichen', 'Mit Keycloak abgeglichen.')));
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
