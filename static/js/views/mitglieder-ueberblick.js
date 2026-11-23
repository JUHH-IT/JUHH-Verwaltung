// Cockpit der Mitgliederverwaltung (0.54, Wunsch Lennert Gust 22.11.2026; löst den Überblick aus 0.43 ab): Arbeitsplatz des
// Landesmitgliederbeauftragten auf einem Blatt – Lage in einem Satz, Kennzahlen, Änderungen zur Kenntnis (kenntnis.js), Anlässe der
// Woche, Arbeitsvorrat nach Themen (jede Zeile öffnet die passende Liste, mitglieder-listen.js), nächste Termine (regelwerk.py),
// Kreisverbände und Bewegung des Jahres. Nur lesend, ohne Beträge; Zahlen kommen aus den Gesamtdaten, nur Zusätze werden nachgeladen.
import { api } from '../api.js';
import { html, mount } from '../html.js';
import { date, shortDate, todayIso } from '../format.js';
import { store, go, can, refresh } from '../store.js';
import { pageHead, icon, safe, toast } from '../ui.js';
import { listen, isActive, fehlt } from '../mitglieder-listen.js';
import { memberDialog, importMembersDialog, openList } from './members.js';
import { doppelteDialog } from './doppelte.js';
import { ovAbgleichDialog } from './ov-abgleich.js';
import { wasGeaendert, wann } from './kenntnis.js';

const pct = (part, all) => (all ? Math.round((part / all) * 100) : 100);
const mehrzahl = (n, eins, viele) => `${n === 1 ? eins : `${n} ${viele}`}`;
const tag = (iso) => new Date(iso + 'T12:00:00');
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const plus = (isoDay, days) => { const d = tag(isoDay); d.setDate(d.getDate() + days); return iso(d); };
const wochentag = (isoDay) => `${tag(isoDay).toLocaleDateString('de-DE', { weekday: 'short' })} ${shortDate(isoDay)}`;
const MONATE = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
const JUBILAEEN = [5, 10, 15, 20];   // wie auswertung.JUBILEES
const WICHTIG = new Set(['anschrift', 'beitrag-offen']);
const VORSCHAU = 4;

export function mitgliederUeberblick(main) {
  mount(main, html`<div class="page wide"><p class="small muted">Wird geladen …</p></div>`);
  const still = (p) => p.catch(() => null);   // Zusätze dürfen die Seite nie blockieren
  safe(async () => {
    const [regeln, doppelte, kenntnis, abgleich, auswertung] = await Promise.all([api('/api/regelwerk'), still(api('/api/doppelte')),
      still(api('/api/kenntnis')), still(api('/api/ov-abgleich')), can('auswertung') ? still(api('/api/auswertung')) : null]);
    draw(main, { termine: regeln.termine, doppelte: doppelte?.gruppen.length || 0, kenntnis, abgleich, auswertung });
  });
}

/** Geburtstage und Jubiläen der nächsten 7 Tage und Eintritte der letzten 14 Tage – aus den Gesamtdaten, ohne weitere Abfrage. */
export function anlaesse(members, today) {
  const bis = plus(today, 7), seit = plus(today, -14), jahr = Number(today.slice(0, 4));
  const schalt = (y) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  const tagIm = (y, md) => (md === '02-29' && !schalt(y) ? `${y}-03-01` : `${y}-${md}`);   // 29.2. ohne Schaltjahr: 1.3. (wie auswertung._birthday)
  const naechster = (md) => (tagIm(jahr, md) >= today ? tagIm(jahr, md) : tagIm(jahr + 1, md));
  const out = [];
  for (const m of members.filter((x) => isActive(x, today))) {
    if (m.birth) {
      const am = naechster(m.birth.slice(5));
      if (am <= bis) out.push({ am, name: m.name, text: `wird ${Number(am.slice(0, 4)) - Number(m.birth.slice(0, 4))}`, id: m.id });
    }
    if (m.joined) {
      const am = naechster(m.joined.slice(5)), jahre = Number(am.slice(0, 4)) - Number(m.joined.slice(0, 4));
      if (am <= bis && JUBILAEEN.includes(jahre)) out.push({ am, name: m.name, text: `${jahre} Jahre Mitglied`, id: m.id });
      if (m.joined >= seit && m.joined <= today) out.push({ am: m.joined, neu: true, name: m.name, text: `eingetreten am ${shortDate(m.joined)}${m.ov_id ? '' : ', noch ohne Ortsverband'}`, id: m.id });
    }
  }
  return out.sort((a, b) => Number(!!a.neu) - Number(!!b.neu) || a.am.localeCompare(b.am) || a.name.localeCompare(b.name, 'de'));
}

function draw(main, extra) {
  const today = todayIso(), jahr = Number(today.slice(0, 4)), y = String(jahr), vorjahr = String(jahr - 1), md = today.slice(4);
  const all = store.data.members, active = all.filter((m) => isActive(m, today));
  const schreiben = can('mitglieder', 'schreiben');
  const im = (feld, von, bis) => all.filter((m) => m[feld] && m[feld] >= von && m[feld] <= bis).length;
  const ein = im('joined', `${y}-01-01`, today), aus = im('left_on', `${y}-01-01`, today);
  const einVor = im('joined', `${vorjahr}-01-01`, vorjahr + md), ausVor = im('left_on', `${vorjahr}-01-01`, vorjahr + md);
  const complete = active.filter((m) => !fehlt(m).length).length, luecken = active.length - complete;
  const wahl = all.some((m) => m.wahl), allowed = active.filter((m) => m.wahl?.status === 'wahlberechtigt').length;
  const neu30 = im('joined', plus(today, -30), today);

  // Zur Kenntnis: einzelne Punkte zuerst, Sammelpunkte je Vorgang als eine Zeile
  const k = extra.kenntnis, kAn = !!k?.an, offen = kAn ? k.offen : [];
  const geaendert = store.data.kenntnis?.offen?.length || 0;   // Mitglieder mit offener Änderung (aus den Gesamtdaten: stimmt auch, wenn die Vorschau nicht lädt)
  const kPunkte = store.data.kenntnis?.punkte || 0;            // offene Punkte – dieselbe Zahl wie im Menü und auf der Seite „Zur Kenntnis“
  const einzeln = offen.filter((p) => !p.sammel), sammel = new Map();
  for (const p of offen) if (p.sammel) sammel.set(p.sammel, [...(sammel.get(p.sammel) || []), p]);
  const zeilen = [...einzeln.map((p) => ({ p })), ...[...sammel.values()].map((punkte) => ({ punkte }))];

  // Arbeitsvorrat nach Themen – nur Zeilen mit Treffern
  const lists = listen(jahr, today), liste = (id) => {
    const l = lists.find((x) => x.id === id);
    const n = l ? all.filter(l.test).length : 0, bald = l?.soon ? all.filter((m) => l.test(m) && l.soon(m)).length : 0;
    return n ? { list: id, n, titel: l.label, text: `${l.hint || ''}${bald ? `, ${bald} zum Monatsende` : ''}`, wichtig: WICHTIG.has(id) } : null;
  };
  const a = extra.abgleich?.verfuegbar ? extra.abgleich : null, cases = store.data.cases?.active || 0;
  const vorrat = [
    ['Daten pflegen', [liste('anschrift'), liste('ohne-mail'), liste('ohne-ov')]],
    ['Abgleichen', [a?.aendern.length ? { tun: 'abgleich', n: a.aendern.length, titel: 'Ortsverband passt nicht', text: 'Adresse liegt in anderem Gebiet', wichtig: true } : null,
      extra.doppelte ? { tun: 'doppelte', n: extra.doppelte, titel: 'Mögliche Doppelte', text: 'Gleicher Name, gleiche E-Mail oder ähnlicher Vorname', wichtig: true } : null,
      a?.fehler.length ? { tun: 'abgleich', n: a.fehler.length, titel: 'Adresse prüfen', text: 'Außerhalb Hamburgs, nicht gefunden oder leer' } : null]],
    ['Fristen', [liste('werden-35'), liste('austritt-bald')]],
    ['Beitrag und SEPA', [liste('beitrag-offen'), liste('iban-ohne-mandat'),
      cases ? { tun: 'klaerfaelle', n: cases, titel: 'Klärfälle', text: 'Beitragszahlungen ohne Mitglied', wichtig: true } : null]],
  ].map(([titel, rows]) => [titel, rows.filter(Boolean)]).filter(([, rows]) => rows.length);
  const punkte = vorrat.reduce((n, [, rows]) => n + rows.length, 0);

  // Nächste Wochen: Termine des Regelwerks und Austritte, die wirksam werden (60 Tage)
  const wirksam = new Map();
  for (const m of all) if (m.left_on && m.left_on > today && m.left_on <= plus(today, 60)) wirksam.set(m.left_on, (wirksam.get(m.left_on) || 0) + 1);
  const wochen = [...extra.termine.filter((t) => !t.vorbei).map((t) => ({ am: t.tag, titel: t.label, text: t.quelle })),
    ...[...wirksam.entries()].map(([am, n]) => ({ am, titel: `${mehrzahl(n, 'Ein Austritt wird', 'Austritte werden')} wirksam`, text: 'Die Mitgliedschaft endet an diesem Tag', liste: 'austritt-bald' }))]
    .sort((x, z) => x.am.localeCompare(z.am)).slice(0, 6);

  const feste = anlaesse(all, today).slice(0, 6);

  // Kreisverbände; Frauenanteil nur aus der Auswertung (Geschlecht steht nicht in den Gesamtdaten)
  const frauen = new Map((extra.auswertung?.kv_rows || []).map((r) => [r.code, r.women_share]));
  const kvs = store.data.kv.map((kv) => {
    const mine = all.filter((m) => m.kv === kv.code), act = mine.filter((m) => isActive(m, today));
    const saldo = mine.filter((m) => (m.joined || '').startsWith(y) && m.joined <= today).length - mine.filter((m) => (m.left_on || '').startsWith(y) && m.left_on <= today).length;
    return { ...kv, active: act.length, saldo, complete: pct(act.filter((m) => !fehlt(m).length).length, act.length), frauen: frauen.get(kv.code) };
  }).filter((kv) => kv.active);
  const groesster = Math.max(1, ...kvs.map((kv) => kv.active));

  // Bewegung je Monat
  const monat = today.slice(0, 7), monate = MONATE.map((name, i) => {
    const m = `${y}-${String(i + 1).padStart(2, '0')}`;
    return { name, kurz: name[0], zukunft: m > monat, ein: all.filter((x) => (x.joined || '').startsWith(m) && x.joined <= today).length,
      aus: all.filter((x) => (x.left_on || '').startsWith(m) && x.left_on <= today).length };
  });
  const hoechst = Math.max(1, ...monate.map((m) => Math.max(m.ein, m.aus)));
  const maxEin = Math.max(...monate.map((m) => m.ein)), maxAus = Math.max(...monate.map((m) => m.aus));

  const kopf = geaendert ? `${mehrzahl(geaendert, 'Ein Mitglied wurde', 'Mitglieder wurden')} geändert${punkte ? `, ${mehrzahl(punkte, 'ein Thema im Arbeitsvorrat ist', 'Themen im Arbeitsvorrat sind')} offen` : ''}.`
    : punkte ? `${mehrzahl(punkte, 'Ein Thema im Arbeitsvorrat ist', 'Themen im Arbeitsvorrat sind')} offen.` : 'Alles erledigt.';
  const untertitel = geaendert ? (schreiben ? 'Jede Änderung gilt bereits, du hakst sie nur ab.' : 'Jede Änderung gilt bereits. Abhaken kann, wer Mitglieder ändern darf.')
    : punkte ? 'Der Arbeitsvorrat zeigt, wo Daten fehlen, etwas abzugleichen ist oder eine Frist ansteht.' : 'Keine offenen Änderungen, der Arbeitsvorrat ist leer.';
  const vorzeichen = (n) => (n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : '0');

  mount(main, html`<div class="page wide mu-page ck-page">
    ${pageHead(tag(today).toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }), 'Cockpit', schreiben ? html`
      <button type="button" class="btn" data-import>Liste importieren</button>
      <button type="button" class="btn primary" data-new>${icon('plus', 16)} Mitglied anlegen</button>` : '')}
    <div class="blatt">
      <section class="blatt-teil ck-lage" aria-label="Lage">
        <div><h2 class="blatt-aussage gross">${kopf}</h2><p class="blatt-text">${untertitel}</p></div>
        ${geaendert ? html`<button type="button" class="btn" data-kenntnis>Änderungen ansehen</button>` : ''}
      </section>

      <section class="kz-band" aria-label="Kennzahlen">
        <div class="kz"><span class="label">Aktive Mitglieder</span><span class="wert">${active.length.toLocaleString('de-DE')}</span>
          <span class="note">${ein - aus === 0 ? `so viele wie Ende ${vorjahr}` : `${Math.abs(ein - aus)} ${ein > aus ? 'mehr' : 'weniger'} als Ende ${vorjahr}`}</span></div>
        <div class="kz"><span class="label">Eintritte ${jahr}</span><span class="wert">${ein}</span><span class="note">Vorjahr bis heute ${einVor}</span></div>
        <div class="kz"><span class="label">Austritte ${jahr}</span><span class="wert">${aus}</span><span class="note">Vorjahr bis heute ${ausVor}</span></div>
        ${wahl ? html`<div class="kz"><span class="label">Wahlberechtigt</span><span class="wert">${pct(allowed, active.length)} %</span>
          <span class="note">${allowed.toLocaleString('de-DE')} von ${active.length.toLocaleString('de-DE')}</span></div>`
          : html`<div class="kz"><span class="label">Neu in 30 Tagen</span><span class="wert">${neu30}</span>
            <span class="note">Wahlberechtigung ab ${store.data.wahl?.ab || 2027}</span></div>`}
        <div class="kz"><span class="label">Daten vollständig</span>
          <span class="wert">${pct(complete, active.length)} %<span class="strich ${pct(complete, active.length) < 80 ? 'warn' : ''}" aria-hidden="true"><span style="width:${pct(complete, active.length)}%"></span></span></span>
          <span class="note">${luecken ? `${mehrzahl(luecken, 'ein Mitglied', 'Mitglieder')} mit Lücken` : 'keine Lücken'}</span></div>
      </section>

      <div class="blatt-reihe">
        <section class="blatt-teil" aria-labelledby="ck-kenntnis">
          <div class="blatt-kopf"><h2 id="ck-kenntnis">Zur Kenntnis</h2>${kPunkte ? html`<span class="ck-offen">${kPunkte} offen</span>` : ''}</div>
          ${!k ? html`<p class="muted">Die Änderungen konnten nicht geladen werden.</p>`
            : !kAn ? html`<p class="muted">Die Kenntnisnahme von Änderungen ist ausgeschaltet (Einstellungen, Reiter „Schalter“).</p>`
            : !zeilen.length ? html`<p class="muted">Keine offenen Änderungen.</p>`
            : html`<div class="zeilen ck-kenntnis">${zeilen.slice(0, VORSCHAU).map(({ p, punkte: sp }) => (p ? html`<div>
                <button type="button" class="ck-name" data-kenntnis><span class="punkt" aria-hidden="true"></span>
                  <span><strong>${p.name}</strong><span class="meta">${p.actor}, ${wann(p.zeit)}</span></span></button>
                <span class="ck-was"><span>${wasGeaendert(p)}</span>
                  ${p.felder[0] ? html`<span class="ck-wert"><s>${p.felder[0].alt || 'leer'}</s>${icon('right', 13)}<strong>${p.felder[0].neu || 'leer'}</strong></span>` : ''}</span>
                ${schreiben ? html`<button type="button" class="kn-haken" data-ab="${p.id}" aria-label="Änderung bei ${p.name} zur Kenntnis nehmen">${icon('check', 18)}</button>` : html`<span></span>`}
              </div>` : html`<div>
                <button type="button" class="ck-name" data-kenntnis><span class="punkt" aria-hidden="true"></span>
                  <span><strong>${sp[0].sammel_titel}</strong><span class="meta">${sp[0].actor}, ${date(sp[0].zeit.slice(0, 10))}</span></span></button>
                <span class="ck-was"><span>${mehrzahl(sp.length, 'Ein Mitglied', 'Mitglieder')} geändert</span></span>
                <button type="button" class="btn small" data-kenntnis>Ansehen</button></div>`))}</div>`}
          ${kAn ? html`<p class="blatt-fuss ck-fuss"><span>${zeilen.length > VORSCHAU ? `${zeilen.length - VORSCHAU} weitere offen. ` : ''}In den letzten ${k.frist} Tagen abgehakt: ${k.abgehakt.length}.</span>
            <button type="button" class="blatt-link" data-kenntnis>Alle mit Vorher und Jetzt</button></p>` : ''}
        </section>
        <section class="blatt-teil schmal" aria-labelledby="ck-anlaesse">
          <div class="blatt-kopf"><h2 id="ck-anlaesse">Anlässe diese Woche</h2></div>
          ${feste.length ? html`<div class="zeilen ck-anlaesse">${feste.map((f) => html`<div><span class="zahl ck-tag">${f.neu ? 'neu' : wochentag(f.am)}</span>
            <span><strong>${f.name}</strong><span class="meta">${f.text}</span></span></div>`)}</div>`
            : html`<p class="muted">Keine Geburtstage, Jubiläen oder neuen Mitglieder in dieser Woche.</p>`}
        </section>
      </div>

      <div class="blatt-reihe">
        <section class="blatt-teil" aria-labelledby="ck-vorrat">
          <div class="blatt-kopf"><h2 id="ck-vorrat">Arbeitsvorrat</h2><span class="small">${punkte ? 'Jede Zeile öffnet die Liste' : ''}</span></div>
          ${vorrat.length ? html`<div class="ck-vorrat">${vorrat.map(([titel, rows]) => html`<div><h3>${titel}</h3>
            <div class="zeilen">${rows.map((r) => {
              const inhalt = html`<span><strong>${r.titel}</strong><span class="meta">${r.text}</span></span>
                <span class="ck-zahl">${r.wichtig ? html`<span class="punkt" role="img" aria-label="dringend"></span>` : ''}<span class="zahl">${r.n}</span></span>${icon('right', 14)}`;
              return r.list ? html`<button type="button" data-list="${r.list}">${inhalt}</button>`
                : r.tun === 'doppelte' ? html`<button type="button" data-tun="doppelte" data-doubles>${inhalt}</button>` : html`<button type="button" data-tun="${r.tun}">${inhalt}</button>`;
            })}</div></div>`)}</div>` : html`<p class="muted">Nichts offen.</p>`}
        </section>
        <section class="blatt-teil schmal" aria-labelledby="ck-wochen">
          <div class="blatt-kopf"><h2 id="ck-wochen">Nächste Wochen</h2><button type="button" class="blatt-link" data-rules>Regelwerk</button></div>
          ${wochen.length ? html`<ol class="ck-wochen">${wochen.map((w, i) => html`<li class="${i === 0 ? 'ist-naechster' : ''}">
            <span class="zahl ck-tag">${wochentag(w.am)}</span><span class="ck-schiene" aria-hidden="true"><i></i><b></b></span>
            <span><strong>${w.titel}</strong><span class="meta">${w.text}</span></span></li>`)}</ol>` : html`<p class="muted">Keine Termine.</p>`}
        </section>
      </div>

      <div class="blatt-reihe">
        <section class="blatt-teil" aria-labelledby="ck-kv">
          <div class="blatt-kopf"><h2 id="ck-kv">Kreisverbände</h2>${can('auswertung') ? html`<button type="button" class="blatt-link" data-auswertung>Zur Auswertung</button>` : ''}</div>
          ${kvs.length ? html`<div class="ck-tabelle"><div class="zeilen ck-kvs ${frauen.size ? 'mit-frauen' : ''}">
            <div class="zeilen-kopf"><span>Kreisverband</span><span>Aktiv</span><span class="num">Saldo ${jahr}</span>${frauen.size ? html`<span class="num">Frauen</span>` : ''}<span>Daten vollständig</span></div>
            ${kvs.map((kv) => html`<button type="button" data-kv="${kv.code}"><strong>${kv.name}</strong>
              <span class="ck-balken"><span class="zahl">${kv.active}</span><span class="ck-laenge" aria-hidden="true"><span style="width:${(kv.active / groesster) * 100}%"></span></span></span>
              <span class="num zahl">${vorzeichen(kv.saldo)}</span>
              ${frauen.size ? html`<span class="num zahl">${kv.frauen === null || kv.frauen === undefined ? '—' : `${Math.round(kv.frauen * 100)} %`}</span>` : ''}
              <span class="ck-balken"><span class="strich ${kv.complete < 80 ? 'warn' : ''}" aria-hidden="true"><span style="width:${kv.complete}%"></span></span>
                <span class="zahl ${kv.complete < 80 ? 'ck-warn' : ''}">${kv.complete} %</span></span></button>`)}</div></div>
            <p class="small muted ck-hinweis">Vollständig heißt: Anschrift, E-Mail, Geburtsdatum und Ortsverband sind eingetragen.</p>`
            : html`<p class="muted">Noch keine Mitglieder.</p>`}
        </section>
        <section class="blatt-teil schmal" aria-labelledby="ck-bewegung">
          <div class="blatt-kopf"><h2 id="ck-bewegung">Bewegung ${jahr}</h2><span class="small">je Monat</span></div>
          <p class="ck-legende"><span><i class="ck-ein"></i>Eintritte</span><span><i class="ck-aus"></i>Austritte</span></p>
          <div class="ck-monate" role="img" aria-label="${`Eintritte und Austritte je Monat ${jahr}: ${monate.filter((m) => !m.zukunft).map((m) => `${m.name} ${m.ein} und ${m.aus}`).join(', ')}.`}">
            ${monate.map((m) => html`<div title="${m.zukunft ? `${m.name}: noch keine Zahlen` : `${m.name}: ${mehrzahl(m.ein, 'ein Eintritt', 'Eintritte')}, ${mehrzahl(m.aus, 'ein Austritt', 'Austritte')}`}">
              <div class="ck-hoch"><span class="zahl">${m.ein && m.ein === maxEin ? m.ein : ''}</span><i class="ck-ein" style="height:${(m.ein / hoechst) * 100}px"></i></div>
              <div class="ck-runter" style="height:${Math.round((maxAus / hoechst) * 100) + 22}px"><i class="ck-aus" style="height:${(m.aus / hoechst) * 100}px"></i><span class="zahl">${m.aus && m.aus === maxAus ? m.aus : ''}</span></div>
              <span class="ck-monat">${m.kurz}</span></div>`)}
          </div>
          <p class="small muted ck-hinweis">${mehrzahl(ein, 'Ein Eintritt', 'Eintritte')} und ${mehrzahl(aus, 'ein Austritt', 'Austritte')} bis heute. Angekündigte Austritte zählen ab ihrem Datum.</p>
        </section>
      </div>
    </div>
  </div>`);

  main.querySelector('[data-new]')?.addEventListener('click', () => memberDialog());
  main.querySelector('[data-import]')?.addEventListener('click', importMembersDialog);
  main.querySelectorAll('[data-kenntnis]').forEach((b) => (b.onclick = () => go('kenntnis')));
  main.querySelectorAll('[data-ab]').forEach((b) => (b.onclick = () => safe(async () => {
    await api('/api/kenntnis-abhaken', { punkte: [{ id: Number(b.dataset.ab), sammel: null }], bis: k.stand });
    await refresh();
    toast('Zur Kenntnis genommen.');
  })));
  main.querySelectorAll('[data-list]').forEach((b) => (b.onclick = () => openList(b.dataset.list)));
  main.querySelectorAll('[data-tun]').forEach((b) => (b.onclick = () => ({ abgleich: ovAbgleichDialog, doppelte: doppelteDialog, klaerfaelle: () => go('cases') })[b.dataset.tun]()));
  main.querySelector('[data-rules]').onclick = () => go('regelwerk');
  main.querySelector('[data-auswertung]')?.addEventListener('click', () => go('auswertung'));
  main.querySelectorAll('[data-kv]').forEach((b) => (b.onclick = () => openList('aktiv', { kv: b.dataset.kv })));
}
