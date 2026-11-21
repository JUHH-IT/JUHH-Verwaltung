// Überblick der Mitgliederverwaltung (0.43, Wunsch Lennert Gust 21.11.2026): Arbeitsplatz des Landesmitgliederbeauftragten –
// Kennzahlen, was zu tun ist (jede Zeile öffnet die passende Liste der Seite „Mitglieder“, mitglieder-listen.js), Termine aus dem
// Regelwerk (regelwerk.py) und die Kreisverbände mit Vollständigkeit der Daten. Nur lesend, ohne Beträge.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { date, weekdayDate, todayIso } from '../format.js';
import { store, go, can } from '../store.js';
import { pageHead, icon, safe, table, meter } from '../ui.js';
import { listen, isActive, fehlt } from '../mitglieder-listen.js';
import { memberDialog, importMembersDialog, openList } from './members.js';
import { doppelteDialog } from './doppelte.js';

const TODO = ['ohne-mail', 'anschrift', 'werden-35', 'beitrag-offen', 'nicht-wahlberechtigt', 'austritt-bald'];
const pct = (part, all) => (all ? Math.round((part / all) * 100) : 100);

export function mitgliederUeberblick(main) {
  mount(main, html`<div class="page wide"><p class="small muted">Wird geladen …</p></div>`);
  safe(async () => {
    const [regeln, doppelte] = await Promise.all([api('/api/regelwerk'), api('/api/doppelte').catch(() => ({ gruppen: [] }))]);   // Hinweis darf die Seite nie blockieren
    draw(main, regeln.termine, doppelte.gruppen.length);
  });
}

function draw(main, termine, doubles) {
  const today = todayIso(), year = store.year, y = String(year);
  const all = store.data.members, active = all.filter((m) => isActive(m, today));
  const lists = listen(year, today);
  const complete = active.filter((m) => !fehlt(m).length).length;
  const joined = all.filter((m) => (m.joined || '').startsWith(y)).length;
  const left = all.filter((m) => (m.left_on || '').startsWith(y) && m.left_on <= today).length;
  const wahl = all.some((m) => m.wahl), allowed = active.filter((m) => m.wahl?.status === 'wahlberechtigt').length;
  const cases = store.data.cases?.active || 0;
  const todo = TODO.map((id) => lists.find((l) => l.id === id)).filter(Boolean)
    .map((l) => ({ ...l, n: all.filter(l.test).length, soon: l.soon ? all.filter((m) => l.test(m) && l.soon(m)).length : 0 })).filter((l) => l.n);
  const kvs = store.data.kv.map((k) => {
    const mine = all.filter((m) => m.kv === k.code), act = mine.filter((m) => isActive(m, today));
    return { ...k, active: act.length, delta: mine.filter((m) => (m.joined || '').startsWith(y)).length - mine.filter((m) => (m.left_on || '').startsWith(y) && m.left_on <= today).length,
      allowed: act.filter((m) => m.wahl?.status === 'wahlberechtigt').length, complete: act.filter((m) => !fehlt(m).length).length };
  }).filter((k) => k.active);
  mount(main, html`<div class="page wide mu-page">
    ${pageHead(`Mitgliederverwaltung · Stand ${date(today)}`, 'Überblick', can('mitglieder', 'schreiben') ? html`
      <button type="button" class="btn" data-import>${icon('upload', 16)} Liste importieren</button>
      <button type="button" class="btn primary" data-new>${icon('plus', 16)} Mitglied anlegen</button>` : '')}
    <section class="cards" aria-label="Kennzahlen">
      <div class="card"><span class="label">Aktive Mitglieder</span><span class="value">${active.length}</span>
        <span class="note">${kvs.length} ${kvs.length === 1 ? 'Kreisverband' : 'Kreisverbände'}</span></div>
      <div class="card"><span class="label">Eintritte ${year}</span><span class="value">${joined}</span>
        <span class="note">Austritte bis heute ${left} · Saldo ${joined - left >= 0 ? '+' : ''}${joined - left}</span></div>
      <div class="card"><span class="label">Wahlberechtigt</span><span class="value">${wahl ? allowed : '—'}</span>
        <span class="note">${wahl ? `von ${active.length} aktiven Mitgliedern` : `wird ab ${store.data.wahl?.ab || 2027} berechnet`}</span></div>
      <div class="card"><span class="label">Daten vollständig</span><span class="value">${pct(complete, active.length)} %</span>
        <span class="note">${active.length - complete} mit Lücken</span></div>
    </section>
    <div class="mu-grid">
      <section class="panel mu-todo" aria-labelledby="mu-todo"><div class="panel-head"><h2 id="mu-todo">Zu tun</h2>
        <span class="small muted">Jede Zeile öffnet die Liste</span></div>
        ${cases || doubles || todo.length ? html`<ul class="mu-list">
          ${cases ? html`<li><button type="button" data-go-cases><span class="mu-count warn">${cases}</span>
            <span><strong>Klärfälle</strong><span class="sub">Beitragszahlungen, die keinem Mitglied zugeordnet sind</span></span>${icon('right', 16)}</button></li>` : ''}
          ${doubles ? html`<li><button type="button" data-doubles><span class="mu-count warn">${doubles}</span>
            <span><strong>Mögliche Doppelte</strong><span class="sub">Gleicher Name, gleiche E-Mail oder ähnlicher Vorname – prüfen</span></span>${icon('right', 16)}</button></li>` : ''}
          ${todo.map((l) => html`<li><button type="button" data-list="${l.id}"><span class="mu-count ${['anschrift', 'beitrag-offen', 'nicht-wahlberechtigt'].includes(l.id) ? 'warn' : ''}">${l.n}</span>
            <span><strong>${l.label}</strong><span class="sub">${l.hint || ''}${l.soon ? ` · ${l.soon} zum Monatsende` : ''}</span></span>${icon('right', 16)}</button></li>`)}
        </ul>` : html`<p class="muted">Nichts offen.</p>`}
      </section>
      <section class="panel" aria-labelledby="mu-termine"><div class="panel-head"><h2 id="mu-termine">Termine</h2>
        <button type="button" class="link" data-rules>Regelwerk</button></div>
        <ol class="mu-dates">${termine.map((t) => html`<li class="${t.vorbei ? 'is-past' : ''}"><span class="mu-day">${weekdayDate(t.tag)}</span>
          <span><strong>${t.label}</strong><span class="sub">${t.quelle}${t.vorbei ? ' · vorbei' : ''}</span></span></li>`)}</ol>
      </section>
    </div>
    <section class="panel" aria-labelledby="mu-kv"><div class="panel-head"><h2 id="mu-kv">Kreisverbände</h2>
      <span class="small muted">Vollständig: Anschrift, E-Mail, Geburtsdatum und Ortsverband</span></div>
      ${kvs.length ? table(['Kreisverband', { label: 'Aktiv', className: 'num' }, { label: `Saldo ${year}`, className: 'num' },
        ...(wahl ? [{ label: 'Wahlberechtigt', className: 'num' }] : []), 'Daten vollständig'],
        kvs.map((k) => html`<tr class="clickable" data-kv="${k.code}" tabindex="0"><td>${k.name}</td><td class="num">${k.active}</td>
          <td class="num">${k.delta > 0 ? '+' : ''}${k.delta}</td>${wahl ? html`<td class="num">${k.allowed}</td>` : ''}
          <td><span class="mu-meter">${meter(k.complete / k.active, { warn: pct(k.complete, k.active) < 80 })}<span class="small">${pct(k.complete, k.active)} %</span></span></td></tr>`))
        : html`<p class="muted">Noch keine Mitglieder.</p>`}
    </section>
  </div>`);
  main.querySelector('[data-new]')?.addEventListener('click', () => memberDialog());
  main.querySelector('[data-import]')?.addEventListener('click', importMembersDialog);
  main.querySelector('[data-go-cases]')?.addEventListener('click', () => go('cases'));
  main.querySelector('[data-doubles]')?.addEventListener('click', () => doppelteDialog());
  main.querySelector('[data-rules]').onclick = () => go('regelwerk');
  main.querySelectorAll('[data-list]').forEach((b) => (b.onclick = () => openList(b.dataset.list)));
  main.querySelectorAll('[data-kv]').forEach((row) => {
    const open = () => openList('aktiv', { kv: row.dataset.kv });
    row.onclick = open;
    row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
  });
}
