// Auswertung der Mitgliederdaten (0.29, Wunsch Lennert Gust 10.11.2026; ab 0.54 als ein Blatt). Server: auswertung.py (nur lesend).
// Ein Blatt mit Abschnitten, getrennt durch Haarlinien: Bestand (Verlauf, Vergleich mit dem Vorjahr), Struktur (Alterspyramide,
// Frauenanteil, Mitgliedsdauer), Gliederung (Kreis- und Ortsverbände in mehreren Sichten), Dichte (Stadtteile als Flächen), Bindung
// (Kohorten, Austritte, Altersgrenze, geworben) und die Listen zum Handeln. Jeder Abschnitt beginnt mit einer Aussage aus den Zahlen
// (`aussage…`), die für jede Datenlage stimmen muss. Diagramme sind HTML/CSS (Text bleibt auf jedem Bildschirm gleich groß), nur die
// Bestandslinie und die Karte sind SVG; jeder Wert steht auch als Text da (am Diagramm oder in „Zahlen als Tabelle“).
// Farben: --aw-1 Hauptreihe, --aw-2 zweite Reihe, --aw-x neutral, --aw-seq-1…5 Stufen der Karte; Orange nur für einen auffälligen Wert.
import { api, download } from '../api.js';
import { html, mount } from '../html.js';
import { eur, date, shortDate, todayIso } from '../format.js';
import { store, kvName, go, can } from '../store.js';
import { pageHead, empty, safe, icon } from '../ui.js';
import { ovLabel, ovById } from '../gliederung.js';
import { account } from './members.js';

let stichtag = '';
let kvFilter = '';
let ovFilter = '';
let listTab = 'age_limit';
let gapTab = 'no_email';
let listeGanz = false;       // Listen zum Handeln: zuerst nur die ersten KURZ Einträge
let sicht = 'struktur';      // Gliederung: struktur | erreichbar | ov | beitrag
let dichteArt = 'anzahl';    // Dichte: anzahl | flaeche
let good = null;             // letzte gültige Auswahl: nach einem Fehler (z. B. Stichtag ungültig) dorthin zurück
let gebiete = null;          // Flächen der Stadtteile (öffentliche Daten): einmal geladen
let lage = null;             // Lage der Mitglieder (/api/karte): je Aufruf der Seite einmal geladen

const LISTS = {
  age_limit: ['Altersgrenze 35', 'Mitgliedschaft endet mit Vollendung des 35. Lebensjahres (Satzung § 2 Abs. 1 Nr. 4): erreicht oder in den nächsten 12 Monaten. Ausnahme: Amtsträger (§ 2 Abs. 2).'],
  jubilees: ['Jubiläen', 'Mitglieder mit 5, 10, 15 oder 20 Jahren Mitgliedschaft im Jahr des Stichtags.'],
  birthdays: ['Geburtstage', 'Geburtstage in den nächsten 30 Tagen ab Stichtag.'],
  new: ['Neue Mitglieder', 'Eintritt in den letzten 90 Tagen vor dem Stichtag.'],
  gaps: ['Datenlücken', 'Fehlende Angaben bei Mitgliedern am Stichtag. In der Mitgliederverwaltung oder per Liste ergänzen.'],
};
const GAPS = { no_email: 'ohne E-Mail', no_phone: 'ohne Telefon', no_ov: 'ohne Ortsverband', no_zip: 'ohne PLZ (alte Anschrift)', no_mail: 'Postausschluss' };
const MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];
const BANDS = ['unter 18', '18–24', 'ab 25'];   // Altersschwerpunkt je Ortsverband (auswertung.BANDS)
const SICHTEN = [['struktur', 'Struktur'], ['erreichbar', 'Erreichbarkeit'], ['ov', 'Ortsverbände'], ['beitrag', 'Beitrag']];
const KURZ = 20;
const WARN = 0.8;                               // Daten vollständig unter 80 %: auffälliger Wert

const zahl = (v) => Number(v).toLocaleString('de-DE');
const komma = (v, stellen = 1) => Number(v).toLocaleString('de-DE', { minimumFractionDigits: stellen, maximumFractionDigits: stellen });
const pct = (v) => `${Math.round((v || 0) * 100)} %`;
const part = (v) => (v === null || v === undefined ? '—' : pct(v));   // Anteil, den es nicht gibt (zu kleine Gruppe, keine Angaben)
const num = (v) => (v === null || v === undefined ? '—' : komma(v));
const few = (v) => (v === null ? '< 3' : zahl(v));   // Rollen ohne Mitgliederrecht: der Server nennt Gruppen unter 3 Personen nicht (auswertung.redact)
const signed = (v) => `${v > 0 ? '+' : v < 0 ? '−' : '±'}${zahl(Math.abs(v))}`;
const mz = (n, eins, viele) => `${zahl(n)} ${n === 1 ? eins : viele}`;
const mitglieder = (n) => mz(n, 'Mitglied', 'Mitglieder');
const kopf = (marke, aussage, id) => html`<div><p class="blatt-marke">${marke}</p><h2 class="blatt-aussage" id="${id}">${aussage}</h2></div>`;
const wahl = (name, items, current, label) => html`<div class="segmented aw-wahl" role="group" aria-label="${label}">${items.map(([k, text]) =>
  html`<button type="button" data-${name}="${k}" aria-pressed="${k === current}">${text}</button>`)}</div>`;
/** Große Zahl mit Beitext; ohne Wert steht nur der Grund da (kein Strich in großer Schrift). */
const gross = (wert, neben, ohne) => (wert === null || wert === undefined ? html`<p class="aw-notiz eng">${ohne}</p>`
  : html`<p class="aw-gross"><span class="wert">${pct(wert)}</span><span class="neben">${neben}</span></p>`);
const legende = (items) => html`<ul class="aw-legende">${items.map(([k, label]) => html`<li><i class="aw-sw ${k}" aria-hidden="true"></i>${label}</li>`)}</ul>`;
/** Dünner Balken (Anteil 0…1 der Spur); `warn` färbt ihn als auffälligen Wert. */
const strich = (anteil, klasse = '') => html`<span class="aw-strich ${klasse}" aria-hidden="true"><span style="width:${Math.round(Math.max(0, Math.min(1, anteil || 0)) * 100)}%"></span></span>`;

// ---------------------------------------------------------------- Aussagen: aus den Zahlen, für jede Datenlage richtig

export function aussageBestand(r) {
  const n = r.cards.active, vor = r.development.length > 1 ? r.development[r.development.length - 2] : null;
  if (!n) return 'Keine Mitglieder in dieser Auswahl.';
  if (!vor) return `${mitglieder(n)} am ${date(r.stichtag)}.`;
  const d = n - vor.end;
  return `${mitglieder(n)}, ${d ? `${zahl(Math.abs(d))} ${d > 0 ? 'mehr' : 'weniger'} als` : 'so viele wie'} Ende ${vor.year}.`;
}

export function aussageStruktur(r) {
  const mit = r.ages.filter((a) => a.count);
  if (!r.cards.active) return 'Keine Mitglieder in dieser Auswahl.';
  if (!mit.length) return 'Für diese Auswahl sind keine Geburtsdaten eingetragen.';
  const max = Math.max(...mit.map((a) => a.count)), top = mit.filter((a) => a.count === max);
  if (top.length > 1) return 'Mehrere Altersgruppen sind gleich stark besetzt.';
  return `Am stärksten ist die Altersgruppe ${top[0].label.replace('–', '\u2060–\u2060')} mit ${zahl(max)} von ${zahl(r.cards.active)} ${r.cards.active === 1 ? 'Mitglied' : 'Mitgliedern'}.`;
}

/** Kreisverbände, die eindeutig vorn liegen (höchstens zwei): nur sie nennt die Aussage und nur sie hebt die Tabelle hervor. */
export function vorn(rows) {
  const s = [...rows].sort((a, b) => b.active - a.active);
  if (s.length < 3 || s[0].active === s[1].active) return [];
  return s[1].active > s[2].active ? s.slice(0, 2) : s.slice(0, 1);
}

export function aussageGliederung(r) {
  const rows = r.kv_rows, n = r.cards.active, top = vorn(rows);
  if (!n) return 'Keine Mitglieder in dieser Auswahl.';
  if (top.length) return `${top.map((k) => k.name).join(' und ')} ${top.length > 1 ? 'stellen' : 'stellt'} ${pct(top.reduce((t, k) => t + k.active, 0) / n)} der Mitglieder.`;
  if (rows.length === 1) {
    const mitOv = r.ov_rows.filter((o) => o.id).reduce((s, o) => s + o.active, 0);
    return `${rows[0].name} hat ${mitglieder(n)}${mitOv ? `, ${mitOv === n ? 'alle' : `${zahl(mitOv)} davon`} mit Ortsverband` : ''}.`;
  }
  return 'Die Kreisverbände im Vergleich.';
}

export function aussageBindung(r) {
  const k = [...r.cohorts].reverse().find((c) => c.kept.length && c.kept[0] !== null);
  return k ? `Von ${zahl(k.joined)} Eintritten ${k.year} waren nach einem Jahr noch ${pct(k.kept[0])} Mitglied.`
    : 'Für eine Aussage zur Bindung gibt es noch zu wenige Eintritte.';
}

export function aussageDichte(orte) {
  const mit = orte.filter((o) => o.n);
  if (!mit.length) return 'Kein Mitglied dieser Auswahl ist einem Stadtteil zugeordnet.';
  const max = Math.max(...mit.map((o) => o.n)), top = mit.filter((o) => o.n === max);
  return `In ${zahl(mit.length)} von ${zahl(orte.length)} Stadtteilen wohnen Mitglieder${top.length === 1 ? `, die meisten in ${top[0].name}` : ''}.`;
}

// ---------------------------------------------------------------- Diagramme

/** Achse mit runden Schritten für die Bestandslinie (nicht ab null: es geht um die Veränderung). */
export function skala(values) {
  const lo0 = Math.min(...values), hi0 = Math.max(...values);
  const roh = Math.max(1, (hi0 - lo0) / 3), p = 10 ** Math.floor(Math.log10(roh)), f = roh / p;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  const lo = Math.floor(lo0 / step) * step, hi = Math.max(lo + step, Math.ceil(hi0 / step) * step);
  const ticks = [];
  for (let t = lo; t <= hi + step / 2; t += step) ticks.push(t);
  return { lo, hi, ticks };
}

/** Gespiegelte Säulen: Eintritte nach oben, Austritte nach unten, jeder Wert als Text. `slots` hält Platz für fehlende Monate frei. */
function saeulen(items, slots = 0) {
  const H = 56, max = Math.max(1, ...items.flatMap((i) => [i.auf, i.ab]));
  const px = (v) => (v ? Math.max(2, Math.round((v / max) * H)) : 0);
  const tief = px(Math.max(0, ...items.map((i) => i.ab))) + 18;   // Platz für die längste Säule nach unten und ihren Wert
  const jahr = (l) => (/^\d{4}$/.test(l) ? html`<span class="aw-cc">${String(l).slice(0, 2)}</span>${String(l).slice(2)}` : l);   // schmal: ’26 statt 2026 (CSS)
  return html`<ol class="aw-saeulen">${items.map((i) => html`<li class="${i.jetzt ? 'jetzt' : ''}" title="${i.title}">
      <span class="aw-auf"><span class="aw-wert">${zahl(i.auf)}</span><span class="aw-balken" style="height:${px(i.auf)}px"></span></span>
      <span class="aw-ab" style="height:${tief}px"><span class="aw-balken" style="height:${px(i.ab)}px"></span><span class="aw-wert">${zahl(i.ab)}</span></span>
      <span class="aw-achse">${jahr(i.label)}</span></li>`)}${Array.from({ length: Math.max(0, slots - items.length) }, (_, k) =>
    html`<li class="leer"><span class="aw-auf"></span><span class="aw-ab" style="height:${tief}px"></span><span class="aw-achse">${MONTHS[items.length + k]}</span></li>`)}</ol>`;
}

/** Bestand je Jahresende als Linie, darunter Ein- und Austritte je Jahr. */
function verlauf(r) {
  const dev = r.development, ends = dev.map((d) => d.end), { lo, hi, ticks } = skala(ends);
  const y = (v) => Math.round(((v - lo) / (hi - lo)) * 1000) / 10;
  const wichtig = [0, dev.length - 1];   // schmal: nur Anfang, Ende und Ausschläge beschriftet, wenn sie nicht direkt nebeneinander liegen
  for (const i of [ends.indexOf(Math.min(...ends)), ends.indexOf(Math.max(...ends))]) if (wichtig.every((k) => Math.abs(k - i) > 1)) wichtig.push(i);
  const erster = dev[0], letzter = dev[dev.length - 1];
  return html`<div class="aw-verlauf" role="img" aria-label="Bestand ${erster.year} bis ${letzter.year}: von ${zahl(erster.end)} auf ${zahl(letzter.end)} Mitglieder. Darunter Eintritte und Austritte je Jahr. Alle Werte stehen in der Tabelle darunter.">
    <div class="aw-plot">
      <div class="aw-raster">${ticks.map((t) => html`<span style="bottom:${y(t)}%"><i>${zahl(t)}</i></span>`)}</div>
      ${dev.length > 1 ? html`<svg viewBox="0 0 ${dev.length * 100} 100" preserveAspectRatio="none" aria-hidden="true"><polyline points="${dev.map((d, i) => `${i * 100 + 50},${100 - y(d.end)}`).join(' ')}"></polyline></svg>` : ''}
      <ol class="aw-punkte">${dev.map((d, i) => html`<li title="${d.year}: ${mitglieder(d.end)}"><span class="aw-pkt ${wichtig.includes(i) ? 'wichtig' : ''} ${i === dev.length - 1 ? 'jetzt' : ''}" style="bottom:${y(d.end)}%"><b>${zahl(d.end)}</b></span></li>`)}</ol>
    </div>
    ${saeulen(dev.map((d, i) => ({ label: d.year, auf: d.joined, ab: d.left, jetzt: i === dev.length - 1, title: `${d.year}: ${mz(d.joined, 'Eintritt', 'Eintritte')}, ${mz(d.left, 'Austritt', 'Austritte')}` })))}
  </div>`;
}

/** Tabelle im Blatt: Haarlinien, Zahlen rechts. `heads`: Text oder { label, num }. */
function tab(heads, rows, { klasse = '', fuss = '' } = {}) {
  return html`<div class="tablewrap"><table class="aw-tab ${klasse}"><thead><tr>${heads.map((h) => (h && typeof h === 'object'
    ? html`<th scope="col" class="${h.num ? 'num' : ''}">${h.label}</th>` : html`<th scope="col">${h}</th>`))}</tr></thead>
    <tbody>${rows}</tbody>${fuss ? html`<tfoot>${fuss}</tfoot>` : ''}</table></div>`;
}
const N = (label) => ({ label, num: true });

// ---------------------------------------------------------------- Abschnitte

function bestand(r) {
  const c = r.cards, v = r.vergleich, year = r.year, vorjahr = Number(v.stichtag.slice(0, 4));
  const teil = r.development.find((d) => d.until.slice(5) !== '12-31');
  const tag = v.stichtag.slice(5) === r.stichtag.slice(5) ? `jeweils bis ${shortDate(r.stichtag)}` : `bis ${shortDate(r.stichtag)} und ${shortDate(v.stichtag)}`;
  const quote = (heute, frueher) => [c.active ? heute : null, v.active ? frueher : null];
  const zeilen = [['Mitglieder', c.active, v.active, 'n'], ['Eintritte', c.joined_year, v.joined_year, 'n'], ['Austritte', c.left_year, v.left_year, 'n'],
    ['Frauenanteil', c.women_share, v.women_share, 'p'], ['Durchschnitts­alter', c.avg_age, v.avg_age, 'a'],
    ['Mit E\u2011Mail', ...quote(c.with_email, v.with_email), 'p'], ['Mit SEPA-Mandat', ...quote(c.with_mandate, v.with_mandate), 'p'], ['Mit Ortsverband', ...quote(c.with_ov, v.with_ov), 'p']];
  const zeige = { n: (x) => zahl(x), p: (x) => `${komma(x * 100)} %`, a: (x) => komma(x) };
  const aenderung = { n: (a, b) => signed(a - b),
    p: (a, b) => { const d = Math.round(a * 1000) - Math.round(b * 1000); return html`${d > 0 ? '+' : d < 0 ? '−' : '±'}${komma(Math.abs(d) / 10)}<span class="aw-lang"> Punkte</span>`; },
    a: (a, b) => { const d = Math.round(a * 10) - Math.round(b * 10); return `${d > 0 ? '+' : d < 0 ? '−' : '±'}${komma(Math.abs(d) / 10)}`; } };
  const leer = (x) => x === null || x === undefined;
  return html`<div class="blatt-teil" aria-labelledby="aw-t-bestand">
      ${kopf('Bestand', aussageBestand(r), 'aw-t-bestand')}
      ${legende([['linie', 'Bestand zum Jahresende'], ['zwei', 'Eintritte'], ['x', 'Austritte']])}
      ${verlauf(r)}
      <p class="aw-notiz">${teil ? `${teil.year} bis zum ${date(teil.until)}. ` : ''}Im Jahr ${year}: ${mz(c.joined_year, 'Eintritt', 'Eintritte')} und ${mz(c.left_year, 'Austritt', 'Austritte')}, Saldo ${signed(c.net_year)}. Unter den Mitgliedern ${c.guests === 1 ? 'ist' : 'sind'} ${mz(c.guests, 'Gast', 'Gäste')}, beitragsfrei ${c.dues_free === 1 ? 'ist' : 'sind'} ${zahl(c.dues_free)}. Austritt ist der letzte Tag der Mitgliedschaft.</p>
      <details class="aw-details"><summary>Zahlen als Tabelle</summary>${tab(['Jahr', N('Eintritte'), N('Austritte'), N('Saldo'), N('Bestand')],
        r.development.map((d) => html`<tr><th scope="row">${d.year}${d.until.slice(5) !== '12-31' ? html`<span class="sub">bis ${date(d.until)}</span>` : ''}</th>
          <td class="num">${zahl(d.joined)}</td><td class="num">${zahl(d.left)}</td><td class="num">${signed(d.joined - d.left)}</td><td class="num">${zahl(d.end)}</td></tr>`), { klasse: 'aw-eng' })}</details>
    </div>
    <div class="blatt-teil schmal" aria-labelledby="aw-t-vergleich">
      <div class="blatt-kopf"><h3 id="aw-t-vergleich">Im Vergleich zum Vorjahr</h3></div>
      <div class="aw-schmal">${tab([tag, N(year), N(vorjahr), N('Änderung')], zeilen.map(([titel, a, b, art]) => html`<tr><th scope="row">${titel}</th>
        <td class="num stark">${leer(a) ? '—' : zeige[art](a)}</td><td class="num alt">${leer(b) ? '—' : zeige[art](b)}</td>
        <td class="num">${leer(a) || leer(b) ? '—' : aenderung[art](a, b)}</td></tr>`), { klasse: 'aw-vgl' })}</div>
      <div class="blatt-kopf aw-zwischen"><h3>Eintritte und Austritte ${year} je Monat</h3></div>
      <div class="aw-verlauf" role="img" aria-label="Eintritte und Austritte ${year} je Monat: ${r.months.map((n, i) => `${MONTHS[i]} ${n} und ${r.months_left[i]}`).join(', ')}.">
        ${saeulen(r.months.map((n, i) => ({ label: MONTHS[i], auf: n, ab: r.months_left[i], title: `${MONTHS[i]} ${year}: ${mz(n, 'Eintritt', 'Eintritte')}, ${mz(r.months_left[i], 'Austritt', 'Austritte')}` })), 12)}</div>
      <p class="blatt-fuss">Quoten beziehen sich auf die Mitglieder am jeweiligen Tag, Änderungen von Anteilen sind Prozentpunkte. E‑Mail, Mandat und Ortsverband kennt das Programm nur im heutigen Stand.</p>
    </div>`;
}

function struktur(r) {
  const c = r.cards, x = c.active - c.women - c.men, max = Math.max(1, ...r.ages.flatMap((a) => [a.w ?? 1, a.m ?? 1]));
  const top = Math.max(...r.ages.map((a) => a.count));
  // Breite als Anteil der Spalte ohne den Platz für die Zahl; unter 3 Personen (null) nur ein Stummel
  const balken = (v) => (v === 0 ? '' : html`<span class="aw-bar" style="width:calc((100% - 5.5ch) * ${Math.round(((v ?? 1) / max) * 1000) / 1000})"></span>`);
  const dauerMax = Math.max(1, ...r.tenure.map((t) => t.count));
  const andere = r.gender.filter((g) => !['weiblich', 'männlich'].includes(g.label));
  const geschlecht = [['Mitglieder gesamt', { w: c.women, m: c.men, x }, c.women_share], [`Eintritte ${r.year}`, r.joined_gender, c.joined_women_share],
    ...r.women_age.map((a) => [`${a.label} Jahre`, a, a.share])];
  return html`<div class="blatt-teil" aria-labelledby="aw-t-struktur">
      ${kopf('Struktur', aussageStruktur(r), 'aw-t-struktur')}
      <div class="aw-pyramide" role="img" aria-label="Alterspyramide: je Altersgruppe weiblich links und männlich rechts. ${r.ages.map((a) => `${a.label}: ${few(a.w)} weiblich, ${few(a.m)} männlich`).join('; ')}.">
        <div class="aw-pyr kopf"><span class="l"><i class="aw-sw eins" aria-hidden="true"></i>weiblich, ${zahl(c.women)}</span><span class="mitte">Alter</span><span class="r"><i class="aw-sw zwei" aria-hidden="true"></i>männlich, ${zahl(c.men)}</span></div>
        ${r.ages.map((a) => html`<div class="aw-pyr ${a.count && a.count === top ? 'top' : ''}" title="${a.label}: ${few(a.w)} weiblich, ${few(a.m)} männlich, ${zahl(a.count)} gesamt">
          <span class="l"><span class="aw-wert">${few(a.w)}</span>${balken(a.w)}</span>
          <span class="mitte">${a.label}</span>
          <span class="r">${balken(a.m)}<span class="aw-wert">${few(a.m)}</span></span></div>`)}
      </div>
      <p class="aw-notiz">${mz(x, 'Mitglied', 'Mitglieder')} divers oder ohne Angabe ${x === 1 ? 'ist' : 'sind'} hier nicht dargestellt${c.no_birth ? `, ${zahl(c.no_birth)} ohne Geburtsdatum` : ''}. Mit 35 Jahren endet die Mitgliedschaft nach Satzung § 2, Amtsträger ausgenommen.</p>
      <details class="aw-details"><summary>Zahlen als Tabelle</summary>
        ${tab(['Altersgruppe', N('Mitglieder'), N('Anteil'), N('weiblich'), N('männlich'), N('beim Eintritt')], r.ages.map((a, i) => html`<tr><th scope="row">${a.label}</th>
          <td class="num">${zahl(a.count)}</td><td class="num">${c.active ? pct(a.count / c.active) : '—'}</td><td class="num">${few(a.w)}</td><td class="num">${few(a.m)}</td><td class="num">${zahl(r.entry_ages[i].count)}</td></tr>`), { klasse: 'aw-eng' })}
        <p class="aw-notiz">Beim Eintritt: so viele der heutigen Mitglieder waren bei ihrem Eintritt in dieser Altersgruppe.</p>
        ${tab(['Geschlecht', N('Mitglieder'), N('Ø Alter'), N('Median')], [...r.age_stats.map((a) => html`<tr><th scope="row">${a.label}</th><td class="num">${zahl(a.count)}</td><td class="num">${num(a.avg_age)}</td><td class="num">${num(a.median_age)}</td></tr>`),
          html`<tr><th scope="row">gesamt</th><td class="num">${zahl(c.active)}</td><td class="num">${num(c.avg_age)}</td><td class="num">${num(c.median_age)}</td></tr>`], { klasse: 'aw-eng' })}
        ${tab(['Frauenanteil', N('weiblich'), N('männlich'), N('divers/ohne Angabe'), N('Anteil')], geschlecht.map(([titel, n, anteil]) => html`<tr><th scope="row">${titel}</th>
          <td class="num">${few(n.w)}</td><td class="num">${few(n.m)}</td><td class="num">${few(n.x)}</td><td class="num">${part(anteil)}</td></tr>`), { klasse: 'aw-eng' })}
        ${andere.length ? html`<p class="aw-notiz">Divers oder ohne Angabe im Einzelnen: ${andere.map((g) => `${few(g.count)} ${g.label}`).join(', ')}.</p>` : ''}
      </details>
    </div>
    <div class="blatt-teil schmal" aria-label="Geschlecht, Alter und Mitgliedsdauer">
      <div class="blatt-kopf"><h3>Frauenanteil</h3></div>
      ${gross(c.women_share, r.vergleich.women_share === null ? '' : `Vorjahr ${pct(r.vergleich.women_share)}`, 'Kein Anteil: weniger als 3 Mitglieder mit der Angabe weiblich oder männlich.')}
      ${c.women + c.men + x ? html`<div class="aw-stapel" role="img" aria-label="${zahl(c.women)} weiblich, ${zahl(c.men)} männlich, ${zahl(x)} divers oder ohne Angabe">${[['eins', c.women], ['zwei', c.men], ['x', x]].map(([k, n]) =>
        (n ? html`<span class="${k}" style="flex-grow:${n}"></span>` : ''))}</div>` : ''}
      <p class="aw-notiz eng">${zahl(c.women)} weiblich, ${zahl(c.men)} männlich, ${zahl(x)} divers oder ohne Angabe</p>
      <dl class="aw-liste">
        <div><dt>bei den Eintritten ${r.year}</dt><dd>${part(c.joined_women_share)}</dd></div>
        ${r.women_age.map((a) => html`<div><dt>${a.label} Jahren</dt><dd>${part(a.share)}</dd></div>`)}
      </dl>
      <div class="blatt-kopf aw-zwischen"><h3>Mitgliedsdauer</h3></div>
      <ul class="aw-reihen">${r.tenure.map((t) => html`<li title="${t.label}: ${mitglieder(t.count)}"><span class="name">${t.label}</span>${strich(t.count / dauerMax)}
        <span class="n">${zahl(t.count)}</span><span class="p">${c.active ? pct(t.count / c.active) : ''}</span></li>`)}</ul>
      <p class="blatt-fuss">${c.avg_age === null ? 'Kein Durchschnittsalter: es sind keine Geburtsdaten eingetragen.' : `Durchschnittsalter ${komma(c.avg_age)} Jahre, Median ${String(c.median_age).replace('.', ',')}.`}${c.avg_entry_age === null ? '' : ` Beim Eintritt waren die Mitglieder im Schnitt ${komma(c.avg_entry_age)} Jahre alt.`} Frauenanteil: weiblich im Verhältnis zu weiblich und männlich, kein Anteil unter 3 Personen.</p>
    </div>`;
}

function gliederung(r) {
  const c = r.cards, rows = [...r.kv_rows].sort((a, b) => b.active - a.active), max = Math.max(1, ...rows.map((k) => k.active));
  const stark = new Set(vorn(rows).map((k) => k.code));   // die Kreisverbände aus der Aussage
  const sichten = SICHTEN.filter(([k]) => (k !== 'beitrag' || r.amounts !== false) && (k !== 'ov' || r.ov_rows.length));
  const art = sichten.some(([k]) => k === sicht) ? sicht : 'struktur';
  const summe = rows.length > 1;
  const name = (k) => (rows.length > 1 ? html`<button type="button" class="aw-pick" data-kv-pick="${k.code}" title="Nur ${k.name} auswerten">${k.name}</button>` : k.name);
  const z = (label, inhalt, klasse = '') => html`<td class="num ${klasse}" data-label="${label}">${inhalt}</td>`;
  const balken = (label, text, anteil, klasse = '') => z(label, html`<span>${text}</span>${strich(anteil, klasse)}`, 'aw-mitbalken');
  const daten = (v) => z('Daten vollständig', part(v), v !== null && v < WARN ? 'aw-warn' : '');
  const frauen = (v) => html`<td class="aw-skala" data-label="Frauenanteil"><span class="aw-spur" aria-hidden="true">${c.women_share === null ? '' : html`<i class="aw-mk" style="left:${Math.min(100, c.women_share * 200)}%"></i>`}${v === null ? '' : html`<i class="aw-pt" style="left:${Math.min(100, v * 200)}%"></i>`}</span><span class="wert">${part(v)}</span></td>`;
  const leer = empty('Keine Mitglieder', 'Für die Auswahl gibt es keine Mitglieder.');
  const ganz = (v) => (c.active ? v : null);   // leere Auswahl: kein Anteil statt 0 %
  let inhalt, notiz;
  if (art === 'struktur') {
    const zeile = (titel, k, aktivBalken) => html`<tr><th scope="row">${titel}</th>${aktivBalken}${z('Anteil', k.share === undefined ? '100 %' : pct(k.share))}
      <td class="num" data-label="Saldo ${r.year}" title="${mz(k.joined_year, 'Eintritt', 'Eintritte')}, ${mz(k.left_year, 'Austritt', 'Austritte')}">${signed(k.net_year)}</td>${frauen(k.women_share)}${z('Ø Alter', num(k.avg_age))}${daten(k.complete)}</tr>`;
    inhalt = rows.length ? html`${tab(['Kreisverband', 'Aktiv', N('Anteil'), N(`Saldo ${r.year}`), 'Frauenanteil', N('Ø Alter'), N('Daten vollständig')],
      rows.map((k) => zeile(name(k), k, balken('Aktiv', zahl(k.active), k.active / max, stark.has(k.code) ? 'eins' : ''))),
      { klasse: 'aw-kv aw-karten', fuss: summe ? zeile('Gesamt', { ...c, share: undefined, complete: ganz(c.complete) }, balken('Aktiv', zahl(c.active), 0)) : '' })}
      <details class="aw-details"><summary>Zahlen als Tabelle</summary>${tab(['Kreisverband', N('Mitglieder'), N('weiblich'), N('männlich'), N('divers/ohne Angabe'), N('Eintritte'), N('Austritte'), N('Gäste'), N('SEPA-Mandate')],
        rows.map((k) => html`<tr><th scope="row">${k.name}</th><td class="num">${zahl(k.active)}</td><td class="num">${few(k.w)}</td><td class="num">${few(k.m)}</td><td class="num">${few(k.x)}</td>
          <td class="num">${zahl(k.joined_year)}</td><td class="num">${zahl(k.left_year)}</td><td class="num">${zahl(k.guests)}</td><td class="num">${zahl(k.mandates)}</td></tr>`), { klasse: 'aw-eng' })}</details>` : leer;
    notiz = `Frauenanteil auf einer Skala von 0 bis 50 %${c.women_share === null ? '' : `, der Strich zeigt den Wert der ganzen Auswahl von ${pct(c.women_share)}`}. Daten vollständig: Anschrift, E‑Mail, Geburtsdatum und Ortsverband sind eingetragen. Saldo: Eintritte minus Austritte ${r.year} bis zum Stichtag.`;
  } else if (art === 'erreichbar') {
    const zeile = (titel, k) => html`<tr><th scope="row">${titel}</th>${z('Aktiv', zahl(k.active))}${balken('Mit E‑Mail', part(k.with_email), k.with_email)}${balken('Mit SEPA-Mandat', part(k.with_mandate), k.with_mandate)}${balken('Mit Ortsverband', part(k.with_ov), k.with_ov)}${daten(k.complete)}</tr>`;
    inhalt = rows.length ? tab(['Kreisverband', N('Aktiv'), 'Mit E‑Mail', 'Mit SEPA-Mandat', 'Mit Ortsverband', N('Daten vollständig')], rows.map((k) => zeile(name(k), k)),
      { klasse: 'aw-kv aw-karten', fuss: summe ? zeile('Gesamt', { active: c.active, with_email: ganz(c.with_email), with_mandate: ganz(c.with_mandate), with_ov: ganz(c.with_ov), complete: ganz(c.complete) }) : '' }) : leer;
    notiz = 'Anteile an den Mitgliedern des Kreisverbands am Stichtag, nach dem heutigen Stand der Daten. Daten vollständig: Anschrift, E‑Mail, Geburtsdatum und Ortsverband sind eingetragen.';
  } else if (art === 'ov') {
    inhalt = tab(['Ortsverband', 'Kreisverband', N('Mitglieder'), N('Frauenanteil'), N('Ø Alter'), ...BANDS.map(N)],
      r.ov_rows.map((o) => html`<tr><th scope="row">${o.label}</th><td>${kvName(o.kv)}</td>${z('Mitglieder', zahl(o.active))}${z('Frauenanteil', part(o.women_share))}${z('Ø Alter', num(o.avg_age))}
        ${BANDS.map((label, i) => z(label, o.band_n[i] === null ? '< 3' : part(o.bands[i])))}</tr>`), { klasse: 'aw-ov aw-karten' });
    notiz = 'Unter 18, 18–24 und ab 25: Anteil an den Mitgliedern des Ortsverbands nach dem Alter am Stichtag.';
  } else {
    const sum = (key) => rows.reduce((t, k) => t + k[key], 0);
    const zeile = (titel, k, knopf) => html`<tr><th scope="row">${titel}</th>${z('Aktiv', zahl(k.active))}${z('Beitrag Soll', eur(k.soll))}${z('Gezahlt', eur(k.paid))}${z('Quote', part(k.paid_share))}
      ${z('Rückstand', eur(k.arrears))}${z('Mitglieder im Rückstand', zahl(k.arrears_count))}<td class="num aw-knopf">${knopf}</td></tr>`;
    inhalt = rows.length ? tab(['Kreisverband', N('Aktiv'), N('Beitrag Soll'), N('Gezahlt'), N('Quote'), N('Rückstand'), N('Mitglieder im Rückstand'), ''],
      rows.map((k) => zeile(name(k), k, html`<button type="button" class="blatt-link" data-op-kv="${k.code}">OP-Liste</button>`)),
      { klasse: 'aw-kv aw-karten', fuss: summe ? zeile('Gesamt', { active: c.active, soll: sum('soll'), paid: sum('paid'), paid_share: sum('soll') ? sum('paid') / sum('soll') : null, arrears: sum('arrears'), arrears_count: sum('arrears_count') }, '') : '' }) : leer;
    notiz = `Beitrag laut Mitgliederbuchhaltung: Soll und gezahlt im Jahr ${r.year}, Rückstand Stand heute.`;
  }
  return html`<div class="aw-kopfzeile">${kopf('Gliederung', aussageGliederung(r), 'aw-t-gliederung')}${wahl('sicht', sichten, art, 'Sicht auf die Gliederung')}</div>
    ${inhalt}<p class="aw-notiz">${notiz} Kreis- und Ortsverband nach der heutigen Zuordnung.</p>`;
}

function bindung(r) {
  const alle = Array.from({ length: 5 }, (_, i) => i + 1);
  const ton = (v) => Math.round((0.05 + 0.45 * Math.max(0, Math.min(1, (v - 0.5) / 0.5))) * 100) / 100;   // 50 % und weniger blass, 100 % kräftig
  const zelle = (k, i) => (i >= k.kept.length ? html`<td class="num leer"></td>`
    : k.kept[i] === null ? html`<td class="num alt" title="${k.joined ? 'unter 3 Eintritte: kein Anteil' : 'keine Eintritte'}">${k.joined ? '< 3' : '—'}</td>`
      : html`<td class="num aw-ton" style="--a:${ton(k.kept[i])}" title="Eintritte ${k.year}: nach ${mz(i + 1, 'Jahr', 'Jahren')} noch ${pct(k.kept[i])} Mitglied"><span>${pct(k.kept[i])}</span></td>`);
  const l = r.left_tenure, lMax = Math.max(1, ...l.groups.map((g) => g.count)), ohne = l.total - l.groups.reduce((s, g) => s + g.count, 0);
  const aMax = Math.max(1, ...r.age_limit_ahead.map((a) => a.count)), w = r.recruited;
  return html`<div class="blatt-teil" aria-labelledby="aw-t-bindung">
      ${kopf('Bindung', aussageBindung(r), 'aw-t-bindung')}
      ${tab(['Eintrittsjahr', N('Eintritte'), ...alle.map((j) => N(j === 1 ? 'nach 1 Jahr' : `${j} Jahren`))],
        [...r.cohorts].reverse().map((k) => html`<tr><th scope="row">${k.year}</th><td class="num">${zahl(k.joined)}</td>${alle.map((_, i) => zelle(k, i))}</tr>`), { klasse: 'aw-kohorten' })}
      <p class="aw-notiz">Anteil der Eintritte eines Jahres, die so viele Jahre nach ihrem Eintritt noch Mitglied waren; je kräftiger die Fläche, desto mehr sind geblieben. Eine Zelle füllt sich, sobald alle Eintritte des Jahres so lange zurückliegen. Unter 3 Eintritten steht kein Anteil.</p>
    </div>
    <div class="blatt-teil schmal" aria-label="Austritte, Altersgrenze und Werbung">
      <div class="blatt-kopf"><h3>Wann Mitglieder gehen</h3><span class="small">${mz(l.total, 'Austritt', 'Austritte')} ${r.year}</span></div>
      <ul class="aw-reihen">${l.groups.map((g) => html`<li title="${g.label}: ${mz(g.count, 'Austritt', 'Austritte')}"><span class="name">${g.label}</span>${strich(g.count / lMax)}
        <span class="n">${zahl(g.count)}</span><span class="p">${l.total ? pct(g.count / l.total) : ''}</span></li>`)}</ul>
      <p class="aw-notiz eng">Mitgliedsdauer am Austrittstag.${l.total ? ` ${l.age_limit === 0 ? 'Kein Austritt fiel' : l.age_limit === 1 ? '1 Austritt fiel' : `${zahl(l.age_limit)} Austritte fielen`} auf die Altersgrenze von 35 Jahren${ohne > 0 ? `, bei ${zahl(ohne)} fehlt das Eintrittsdatum` : ''}.` : ''}</p>
      <div class="blatt-kopf aw-zwischen"><h3>Altersgrenze in den nächsten Jahren</h3></div>
      <ul class="aw-reihen kurz">${r.age_limit_ahead.map((a, i) => html`<li title="${a.year}: ${mitglieder(a.count)} werden 35"><span class="name">${i ? a.year : `noch ${a.year}`}</span>${strich(a.count / aMax)}
        <span class="n">${zahl(a.count)}</span></li>`)}</ul>
      <p class="aw-notiz eng">So viele der heutigen Mitglieder werden im jeweiligen Jahr 35. Wer die Grenze schon erreicht hat, steht in der Liste „Altersgrenze 35“.</p>
      <div class="blatt-kopf aw-zwischen"><h3>Geworben</h3></div>
      ${gross(w.share, `${zahl(w.count)} von ${mz(w.joined, 'Eintritt', 'Eintritten')} ${r.year} mit eingetragenem Werber`, `${r.year} gab es bis zum Stichtag keinen Eintritt, also auch keinen Anteil mit eingetragenem Werber.`)}
    </div>`;
}

function listen(r) {
  const darf = can('mitglieder');
  const alle = listTab === 'gaps' ? r.lists.gaps[gapTab].items : r.lists[listTab], rows = listeGanz ? alle : alle.slice(0, KURZ);
  const anzahl = (k) => (k === 'gaps' ? Object.values(r.lists.gaps).reduce((s, g) => s + g.count, 0) : r.lists[k].length);
  const stand = (x) => (listTab === 'age_limit' ? html`<span class="${x.reached ? 'aw-warn' : 'alt'}">${x.reached ? 'erreicht' : 'demnächst'}</span>`
    : listTab === 'jubilees' ? `${x.years} Jahre` : listTab === 'birthdays' ? `wird ${x.age}` : '');
  const reihen = (titel, items) => {
    const max = Math.max(1, ...items.map((i) => i.count || 0));
    return html`<div class="blatt-kopf ${titel === 'Vereinigungen' ? '' : 'aw-zwischen'}"><h3>${titel}</h3></div>
      ${items.length ? html`<ul class="aw-reihen">${items.map((i) => html`<li title="${i.label}: ${few(i.count)}"><span class="name">${i.label}</span>${strich((i.count ?? 1) / max)}
        <span class="n">${few(i.count)}</span><span class="p">${i.count !== null && r.cards.active ? pct(i.count / r.cards.active) : ''}</span></li>`)}</ul>` : html`<p class="aw-notiz eng">Keine Angaben.</p>`}`;
  };
  return html`<div class="blatt-teil" aria-labelledby="aw-t-listen">
      <div class="blatt-kopf"><h2 id="aw-t-listen">Listen zum Handeln</h2></div>
      ${darf ? html`${wahl('list', Object.entries(LISTS).map(([k, [label]]) => [k, html`${label} <span class="alt">${zahl(anzahl(k))}</span>`]), listTab, 'Liste')}
        <p class="aw-notiz">${LISTS[listTab][1]}</p>
        ${listTab === 'gaps' ? wahl('gap', Object.entries(GAPS).map(([k, label]) => [k, html`${label} <span class="alt">${zahl(r.lists.gaps[k].count)}</span>`]), gapTab, 'Art der Lücke') : ''}
        ${rows.length ? tab(['Mitglied', 'Kreis- und Ortsverband', listTab === 'gaps' ? '' : 'Datum', ''], rows.map((x) => html`<tr class="clickable" data-member="${x.id}" tabindex="0">
          <th scope="row">${x.name}<span class="sub">${x.number}</span></th>
          <td>${kvName(x.kv)}${x.ov_id ? html`<span class="sub">${ovLabel(ovById(x.ov_id))}</span>` : ''}</td>
          <td class="zahl">${x.on ? date(x.on) : ''}</td><td>${stand(x)}</td></tr>`), { klasse: 'aw-namen' }) : empty('Niemand in dieser Liste', 'Für Stichtag und Auswahl gibt es keine Einträge.')}
        ${alle.length > rows.length ? html`<p class="aw-notiz"><button type="button" class="blatt-link" data-liste-ganz>Alle ${zahl(alle.length)} zeigen</button></p>` : ''}
        ${listTab === 'gaps' && r.lists.gaps[gapTab].count > alle.length ? html`<p class="aw-notiz">Die ersten ${zahl(alle.length)} von ${zahl(r.lists.gaps[gapTab].count)}.</p>` : ''}`
    : html`<p class="aw-notiz">Die Listen nennen Mitglieder mit Namen. Sie sieht, wer die Mitgliederverwaltung lesen darf.</p>`}
    </div>
    <div class="blatt-teil schmal" aria-label="Vereinigungen und Zahlweg">
      ${reihen('Vereinigungen', r.clubs)}${reihen('Zahlweg', r.payment)}
      <p class="blatt-fuss">Mitglied am Stichtag: eingetreten bis zum Stichtag, nicht vorher ausgetreten. Alter in vollendeten Jahren am Stichtag. Kreis- und Ortsverband nach der heutigen Zuordnung.</p>
    </div>`;
}

// ---------------------------------------------------------------- Dichte: Stadtteile als Flächen (SVG aus DOM-Knoten, ohne Leaflet)

/** Fläche in km² aus Längen- und Breitengraden (ebene Näherung für ein Stadtgebiet; Ringe nach dem ersten sind Löcher). */
export function flaecheKm2(flaechen) {
  let sum = 0;
  for (const poly of flaechen) {
    poly.forEach((ring, i) => {
      let a = 0;
      for (let j = 0; j < ring.length; j++) {
        const [x1, y1] = ring[j], [x2, y2] = ring[(j + 1) % ring.length];
        a += x1 * y2 - x2 * y1;
      }
      sum += (i ? -1 : 1) * Math.abs(a / 2) * Math.cos((ring[0][1] * Math.PI) / 180) * 111.32 * 110.574;
    });
  }
  return sum;
}

/** Höchstens `stufen` Klassen mit etwa gleich vielen Stadtteilen: obere Grenzen aus den vorkommenden Werten über null. */
export function klassen(werte, stufen = 5) {
  const v = werte.filter((x) => x > 0).sort((a, b) => a - b);
  return [...new Set(Array.from({ length: v.length ? stufen : 0 }, (_, i) => v[Math.min(v.length - 1, Math.ceil(((i + 1) * v.length) / stufen) - 1)]))];
}

/** Farbstufe 0 (kein Mitglied) bis 5: die oberste Klasse ist immer die kräftigste. */
export const stufe = (wert, grenzen) => (wert > 0 ? Math.round(((grenzen.findIndex((g) => wert <= g) + 1) * 5) / grenzen.length) : 0);

let bild = null;   // Pfade der Karte: einmal je geladenen Flächen berechnet
function kartenbild(geo) {
  if (bild?.geo === geo) return bild;
  const punkte = geo.stadtteile.flatMap((s) => s.flaechen.flat(2));
  const lon0 = Math.min(...punkte.map((p) => p[0])), lon1 = Math.max(...punkte.map((p) => p[0]));
  const lat0 = Math.min(...punkte.map((p) => p[1])), lat1 = Math.max(...punkte.map((p) => p[1]));
  const k = Math.cos((((lat0 + lat1) / 2) * Math.PI) / 180), W = 1000, s = W / ((lon1 - lon0) * k), H = Math.round((lat1 - lat0) * s);
  const xy = (p) => [Math.round((p[0] - lon0) * k * s * 10) / 10, Math.round((lat1 - p[1]) * s * 10) / 10];
  const pfad = (ringe) => ringe.map((ring) => `M${ring.map((p) => xy(p).join(',')).join('L')}Z`).join('');
  const fest = (ring) => Math.min(...ring.map((p) => p[0])) >= lon0 - 0.01;   // ohne die Insel Neuwerk (liegt weit vor der Küste)
  bild = { geo, W, H,
    stadtteile: geo.stadtteile.map((st) => ({ name: st.name, bezirk: st.bezirk, d: pfad(st.flaechen.flat(1)), km2: flaecheKm2(st.flaechen) })),
    bezirke: geo.bezirke.map((b) => {
      const ringe = b.flaechen.flat(1).filter(fest), gross = ringe.reduce((a, ring) => (ring.length > a.length ? ring : a), ringe[0]).map(xy);
      return { name: b.name.replace(/^Hamburg-/, ''), d: pfad(ringe), x: (gross.reduce((t, p) => t + p[0], 0) / gross.length / W) * 100, y: (gross.reduce((t, p) => t + p[1], 0) / gross.length / H) * 100 };
    }) };
  return bild;
}

/** Mitglieder je Stadtteil für die Auswahl (Kreis-/Ortsverband) aus der Lage der Mitglieder. */
function orteFuer(karte, daten) {
  const ovVon = ovFilter ? new Map(store.data.members.map((m) => [m.id, m.ov_id])) : null;
  const passt = (p) => (!kvFilter || p.kv === kvFilter) && (!ovVon || (ovFilter === 'none' ? !ovVon.get(p.id) : String(ovVon.get(p.id)) === ovFilter));
  const zahlen = new Map();
  let verortet = 0;
  for (const p of daten.points) {
    if (!passt(p)) continue;
    verortet++;
    zahlen.set(p.stadtteil, (zahlen.get(p.stadtteil) || 0) + 1);
  }
  return { verortet, orte: karte.stadtteile.map((st) => ({ ...st, n: zahlen.get(st.name) || 0, jeKm2: st.km2 ? (zahlen.get(st.name) || 0) / st.km2 : 0 })) };
}

const NS = 'http://www.w3.org/2000/svg';
function knoten(name, attrs, text = '') {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text) e.textContent = text;
  return e;
}

/** Karte in `box` zeichnen: Stadtteile nach Farbstufe, Bezirksgrenzen darüber, Namen der Bezirke als Text. */
function zeichneKarte(box, karte, orte, grenzen, wert, text) {
  const svg = knoten('svg', { viewBox: `0 0 ${karte.W} ${karte.H}`, role: 'img',
    'aria-label': `Karte der Hamburger Stadtteile, eingefärbt nach ${dichteArt === 'flaeche' ? 'Mitgliedern je Quadratkilometer' : 'der Zahl der Mitglieder'}. Die Werte stehen in der Tabelle darunter.` });
  for (const o of orte) {
    const p = knoten('path', { d: o.d, class: `aw-st s${stufe(wert(o), grenzen)}`, 'fill-rule': 'evenodd' });
    p.append(knoten('title', {}, `${o.name}: ${text(o)}`));
    svg.append(p);
  }
  for (const b of karte.bezirke) svg.append(knoten('path', { d: b.d, class: 'aw-bz' }));
  box.replaceChildren(svg);
  for (const b of karte.bezirke) {
    const name = document.createElement('span');
    name.className = 'aw-bezirk';
    name.textContent = b.name;
    name.style.left = `${b.x}%`;
    name.style.top = `${b.y}%`;
    box.append(name);
  }
}

function dichteRahmen(inhalt) {
  return html`<div class="blatt-teil">${kopf('Dichte', 'Wo die Mitglieder wohnen.', 'aw-t-dichte')}<p class="aw-notiz">${inhalt}</p></div>`;
}

/** Abschnitt Dichte füllen; die Daten kommen einmal je Seitenaufruf (Recht: Mitgliederverwaltung lesen, wie die Karte). */
async function dichte(box, r) {
  if (!box) return;
  let geo, daten;
  try {
    [geo, daten] = await Promise.all([gebiete ||= api('/api/karte-gebiete'), lage ||= api('/api/karte')]);
  } catch {
    gebiete = lage = null;
    if (box.isConnected) mount(box, dichteRahmen('Die Karte der Stadtteile konnte nicht geladen werden.'));
    return;
  }
  if (!box.isConnected) return;
  if (!daten.available) {
    mount(box, html`<div class="blatt-teil">${kopf('Dichte', 'Für die Karte der Stadtteile fehlen die amtlichen Adressdaten.', 'aw-t-dichte')}
      <p class="aw-notiz">Sie werden auf der Seite Karte einmalig geladen. Bis dahin lässt sich kein Mitglied einem Stadtteil zuordnen.</p>
      <p class="aw-notiz"><button type="button" class="blatt-link" data-zur-karte>Zur Karte</button></p></div>`);
    return;
  }
  const karte = kartenbild(geo), { verortet, orte } = orteFuer(karte, daten);
  const flaeche = dichteArt === 'flaeche';
  const wert = (o) => (flaeche ? o.jeKm2 : o.n);
  const jeKm2 = (v) => (v > 0 && v < 0.05 ? 'unter 0,1' : komma(v));
  const text = (o) => (flaeche ? `${jeKm2(o.jeKm2)} je km² (${mitglieder(o.n)})` : mitglieder(o.n));
  const grenzen = klassen(orte.map(wert));
  const bis = (g, i) => (flaeche ? `bis ${komma(Math.ceil(g * 10) / 10)}` : (() => { const von = i ? grenzen[i - 1] + 1 : 1; return von === g ? zahl(g) : `${zahl(von)}–${zahl(g)}`; })());
  const rang = [...orte].filter((o) => o.n).sort((a, b) => wert(b) - wert(a) || a.name.localeCompare(b.name, 'de')).slice(0, 8);
  const max = Math.max(1e-9, ...rang.map(wert)), ohne = orte.filter((o) => !o.n).length;
  const heute = r.stichtag !== todayIso();
  mount(box, html`<div class="blatt-teil" aria-labelledby="aw-t-dichte">
      <div class="aw-kopfzeile">${kopf('Dichte', aussageDichte(orte), 'aw-t-dichte')}${wahl('dichte', [['anzahl', 'Anzahl'], ['flaeche', 'je km²']], dichteArt, 'Maß der Karte')}</div>
      <div class="aw-karte" data-karte></div>
      <ul class="aw-legende" data-dichte-legende><li><i class="aw-sw s0" aria-hidden="true"></i>kein Mitglied</li>${grenzen.map((g, i) => html`<li><i class="aw-sw s${stufe(g, grenzen)}" aria-hidden="true"></i>${bis(g, i)}</li>`)}
        <li class="alt">${flaeche ? 'Mitglieder je km²' : 'Mitglieder je Stadtteil'}</li></ul>
      <details class="aw-details"><summary>Alle Stadtteile als Tabelle</summary>${tab(['Stadtteil', 'Bezirk', N('Mitglieder'), N('Fläche km²'), N('je km²')],
        [...orte].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name, 'de')).map((o) => html`<tr><th scope="row">${o.name}</th><td>${o.bezirk}</td><td class="num">${zahl(o.n)}</td><td class="num">${komma(o.km2)}</td><td class="num">${o.n ? jeKm2(o.jeKm2) : '0'}</td></tr>`), { klasse: 'aw-eng' })}</details>
    </div>
    <div class="blatt-teil schmal" aria-label="Stärkste Stadtteile">
      <div class="blatt-kopf"><h3>Stärkste Stadtteile</h3><span class="small">${flaeche ? 'Mitglieder je km²' : 'Mitglieder'}</span></div>
      ${rang.length ? html`<ol class="aw-reihen rang">${rang.map((o) => html`<li title="${o.name}: ${text(o)}"><span class="name">${o.name}<span class="sub">${o.bezirk}</span></span>${strich(wert(o) / max)}
        <span class="n">${flaeche ? jeKm2(o.jeKm2) : zahl(o.n)}</span></li>`)}</ol>` : html`<p class="aw-notiz eng">Kein Stadtteil mit Mitgliedern.</p>`}
      <dl class="aw-liste">
        <div><dt>Stadtteile ohne Mitglied</dt><dd>${zahl(ohne)} von ${zahl(orte.length)}</dd></div>
        <div><dt>Mitglieder mit Stadtteil</dt><dd>${kvFilter || ovFilter ? zahl(verortet) : `${zahl(verortet)} von ${zahl(daten.members)}`}</dd></div>
      </dl>
      <p class="blatt-fuss">Wohnort nach den amtlichen Adressdaten${heute ? ', Stand heute und nicht zum Stichtag' : ''}. Wer außerhalb Hamburgs wohnt oder dessen Adresse zu prüfen ist, fehlt hier. Fläche aus den Grenzen der Stadtteile berechnet. Einwohnerzahlen kennt das Programm nicht, deshalb gibt es keine Dichte je Einwohner. Neuwerk ist nicht eingezeichnet.</p>
    </div>`);
  zeichneKarte(box.querySelector('[data-karte]'), karte, orte, grenzen, wert, text);
}

// ---------------------------------------------------------------- Seite

export function auswertung(main) {
  lage = null;   // die Seite wird neu geöffnet oder nach einer Änderung neu aufgebaut: Lage der Mitglieder neu holen
  laden(main);
}

function laden(main) {
  if (!main.querySelector('.aw-page')) mount(main, html`<div class="page wide"><p class="small muted">Wird geladen …</p></div>`);
  safe(async () => {
    const q = `stichtag=${stichtag}&kv=${kvFilter}&ov=${ovFilter}`;
    try {
      const r = await api(`/api/auswertung?${q}`);
      stichtag = r.stichtag;
      good = { stichtag, kvFilter, ovFilter };
      draw(main, r, q);
    } catch (err) {
      ({ stichtag, kvFilter, ovFilter } = good || { stichtag: '', kvFilter: '', ovFilter: '' });
      if (!main.querySelector('.aw-page')) {
        mount(main, html`<div class="page wide"><p class="notice warn">Auswertung nicht möglich.</p><button type="button" class="btn" data-retry>Erneut laden</button></div>`);
        main.querySelector('[data-retry]').onclick = () => laden(main);
      } else {
        const day = main.querySelector('#aw-day');
        if (day) day.value = stichtag;
      }
      throw err;
    }
  });
}

const TEILE = { bestand, struktur, gliederung, bindung, listen };

/** Seite zeichnen. Umschalter und Reiter zeichnen nur ihren Abschnitt neu (ohne neue Anfrage, ohne Sprung der Seite). */
function draw(main, r, q) {
  // Kreisverbände mit Mitgliedern; ohne Mitgliederrecht ist die Mitgliederliste leer, dann aus der Auswertung selbst
  const kvs = store.data.kv.filter((k) => k.code !== 'LV' && (k.code === kvFilter || store.data.members.some((m) => m.kv === k.code) || r.kv_rows.some((x) => x.code === k.code)));
  const ovChoices = kvFilter ? (store.data.ovs || []).filter((o) => o.kv === kvFilter && (o.members || o.active)) : [];
  const scope = [kvFilter && kvName(kvFilter), ovFilter === 'none' ? 'ohne Ortsverband' : ovFilter && ovLabel(ovById(Number(ovFilter)))].filter(Boolean).join(', ') || 'alle Kreisverbände';
  const mitKarte = can('mitglieder');   // /api/karte verlangt „Mitgliederverwaltung lesen“
  const sprung = [['bestand', 'Bestand'], ['struktur', 'Struktur'], ['gliederung', 'Gliederung'], ...(mitKarte ? [['dichte', 'Dichte']] : []), ['bindung', 'Bindung'], ['listen', 'Listen']];
  mount(main, html`<div class="page wide aw-page">
    ${pageHead(`Mitgliederdaten zum ${date(r.stichtag)}, ${scope}`, 'Auswertung', html`
      <a class="btn" href="/api/auswertung.pdf?${q}" target="_blank" rel="noopener" title="Nur Zahlen, keine Namen, zum Beispiel für den Vorstand">${icon('file', 16)} PDF</a>
      <button type="button" class="btn" data-csv>${icon('download', 16)} CSV</button>
      ${can('buchhaltung') ? html`<button type="button" class="btn" data-op title="Offene Beiträge je Mitglied für die Kreisverbände">OP-Listen</button>` : ''}`)}
    <div class="toolbar aw-leiste"><div class="aw-filter"><label class="field inline">Stichtag<input type="date" id="aw-day" min="2000-01-01" value="${r.stichtag}"></label>
      <label class="field inline">Kreisverband<select id="aw-kv"><option value="">alle</option>${kvs.map((k) => html`<option value="${k.code}" ${k.code === kvFilter ? 'selected' : ''}>${k.name}</option>`)}</select></label>
      ${kvFilter ? html`<label class="field inline">Ortsverband<select id="aw-ov"><option value="">alle</option><option value="none" ${ovFilter === 'none' ? 'selected' : ''}>ohne Ortsverband</option>
        ${ovChoices.map((o) => html`<option value="${o.id}" ${String(o.id) === ovFilter ? 'selected' : ''}>${ovLabel(o)}</option>`)}</select></label>` : ''}</div>
      <nav class="segmented aw-sprung" aria-label="Abschnitte der Auswertung">${sprung.map(([k, label]) => html`<button type="button" data-sprung="${k}">${label}</button>`)}</nav></div>
    <div class="blatt">
      <section class="blatt-reihe" id="aw-bestand" data-teil="bestand">${bestand(r)}</section>
      <section class="blatt-reihe" id="aw-struktur" data-teil="struktur">${struktur(r)}</section>
      <section class="blatt-teil aw-breit" id="aw-gliederung" data-teil="gliederung" aria-labelledby="aw-t-gliederung">${gliederung(r)}</section>
      ${mitKarte ? html`<section class="blatt-reihe" id="aw-dichte" data-teil="dichte">${dichteRahmen('Die Karte wird geladen …')}</section>` : ''}
      <section class="blatt-reihe" id="aw-bindung" data-teil="bindung">${bindung(r)}</section>
      <section class="blatt-reihe" id="aw-listen" data-teil="listen">${listen(r)}</section>
    </div>
  </div>`);
  const page = main.querySelector('.aw-page'), teil = (name) => page.querySelector(`[data-teil="${name}"]`);
  const neu = (name, fokus) => {   // einen Abschnitt neu zeichnen und den bedienten Knopf wieder fokussieren
    if (name === 'dichte') dichte(teil('dichte'), r).then(() => page.querySelector(fokus)?.focus());
    else { mount(teil(name), TEILE[name](r)); page.querySelector(fokus)?.focus(); }
  };
  const klick = {
    csv: () => safe(() => download(`/api/auswertung.csv?${q}`, `Mitgliederstatistik-${r.stichtag}.csv`)),
    op: () => go('openitems', { openSide: 'dues', opKv: kvFilter }),   // OP-Listen stehen in der Buchhaltung
    opKv: (b) => go('openitems', { openSide: 'dues', opKv: b.dataset.opKv }),
    kvPick: (b) => { kvFilter = b.dataset.kvPick; ovFilter = ''; laden(main); },
    sicht: (b) => { sicht = b.dataset.sicht; neu('gliederung', `[data-sicht="${sicht}"]`); },
    dichte: (b) => { dichteArt = b.dataset.dichte; neu('dichte', `[data-dichte="${dichteArt}"]`); },
    list: (b) => { listTab = b.dataset.list; listeGanz = false; neu('listen', `[data-list="${listTab}"]`); },
    gap: (b) => { gapTab = b.dataset.gap; listeGanz = false; neu('listen', `[data-gap="${gapTab}"]`); },
    listeGanz: () => { listeGanz = true; neu('listen', `tr[data-member]:nth-child(${KURZ + 1})`); },
    zurKarte: () => go('map'),
    sprung: (b) => page.querySelector(`#aw-${b.dataset.sprung}`)?.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }),
    member: (b) => account(Number(b.dataset.member)),
  };
  // ein Empfänger für alle Knöpfe: hängt an der Seite (nicht an main), damit er mit ihr verschwindet
  page.onclick = (e) => {
    const b = e.target.closest('[data-csv],[data-op],[data-op-kv],[data-kv-pick],[data-sicht],[data-dichte],[data-list],[data-gap],[data-liste-ganz],[data-zur-karte],[data-sprung],[data-member]');
    const name = b && Object.keys(klick).find((k) => k in b.dataset);
    if (name) klick[name](b);
  };
  page.onkeydown = (e) => {
    const row = (e.key === 'Enter' || e.key === ' ') && e.target.matches?.('tr[data-member]') ? e.target : null;
    if (row) { e.preventDefault(); klick.member(row); }
  };
  main.querySelector('#aw-day').onchange = (e) => { if (e.target.value) { stichtag = e.target.value; laden(main); } };
  main.querySelector('#aw-kv').onchange = (e) => { kvFilter = e.target.value; ovFilter = ''; laden(main); };
  main.querySelector('#aw-ov')?.addEventListener('change', (e) => { ovFilter = e.target.value; laden(main); });
  if (mitKarte) dichte(teil('dichte'), r);
}
