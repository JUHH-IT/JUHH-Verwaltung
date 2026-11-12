// RPJ-Zuordnung beim Verbuchen (ab 0.26): auf den RPJ-Posten ist sie Pflicht – 4380/H45 Kosten (Projekt + Kostenart),
// H19 Teilnahmebeiträge (Projekt), 1592/H18 Mittelzufluss (Mittelabruf bzw. Teilnahmebeitrag zu einem Projekt).
// Verbindlich prüft der Server (rpj.check_part); hier nur Auswahl, Hinweis und sofortige Rückmeldung.
import { html, mount } from './html.js';
import { store } from './store.js';
import { eur } from './format.js';

export const RPJ_CATEGORIES = [['a', 'Raummiete'], ['b', 'An- und Abreisekosten'], ['c', 'Verpflegungskosten'], ['d', 'Referentenkosten'],
  ['e', 'Marketing / Bewerbungskosten'], ['f', 'Reisekosten'], ['g', 'Eintritte'], ['h', 'Sonstige Veranstaltungskosten'],
  ['i', 'Teilnahmebeiträge'], ['j', 'Technische Ausstattung'], ['k', 'Geschäftsführungskosten']];
export const rpjCategory = (code) => RPJ_CATEGORIES.find(([c]) => c === code)?.[1] || '';

/** RPJ-Art eines Postens wie rpj.kind_of: 'cost', 'fee', 'grant' oder null. */
export function rpjKind(account, title) {
  if (account === '4380' || title === 'H45') return 'cost';
  if (title === 'H19') return 'fee';
  if (account === '1592' || title === 'H18') return 'grant';
  return null;
}

/** Projekte bzw. Mittelabrufe, die zum Haushaltsjahr passen (gleiches Jahr zuerst, höchstens ein Jahr Abstand). */
export const rpjProjects = (year) => (store.data?.rpj?.projects || []).filter((p) => Math.abs(p.year - year) <= 1)
  .sort((a, b) => (b.year === year) - (a.year === year) || b.year - a.year || a.no - b.no);
export const rpjRequests = (year) => (store.data?.rpj?.requests || []).filter((r) => Math.abs(r.year - year) <= 1)
  .sort((a, b) => (b.year === year) - (a.year === year) || b.year - a.year || a.quarter - b.quarter);
export const projectLabel = (p, year) => `${p.year !== year ? p.year + ' · ' : ''}Nr. ${p.no} ${p.name}${p.status === 'cancelled' ? ' (abgesagt)' : ''}`;
export const requestLabel = (r) => `Mittelabruf Q${r.quarter}/${r.year} · ${eur(r.amount)} · offen ${eur(r.amount - r.received)}`;

/** Auswahl Projekt/Mittelabruf als Optionen (für eine Art und ein Haushaltsjahr). */
export function targetOptions(kind, year, current = '') {
  const projects = rpjProjects(year), requests = kind === 'grant' ? rpjRequests(year) : [];
  return html`<option value="" ${current ? '' : 'selected'}>${kind === 'grant' ? 'Mittelabruf oder Projekt wählen' : 'RPJ-Projekt wählen'}</option>
    ${requests.length ? html`<optgroup label="Mittelabruf (Zuwendung der Stadt)">${requests.map((r) => html`<option value="r:${r.id}" ${current === `r:${r.id}` ? 'selected' : ''}>${requestLabel(r)}</option>`)}</optgroup>` : ''}
    ${projects.length ? html`<optgroup label="${kind === 'grant' ? 'Teilnahmebeitrag zu Projekt' : 'Projekt'}">${projects.map((p) => html`<option value="p:${p.id}" ${current === `p:${p.id}` ? 'selected' : ''}>${projectLabel(p, year)}</option>`)}</optgroup>` : ''}`;
}

export function categoryOptions(current = '', kind = 'cost') {
  return html`<option value="" ${current ? '' : 'selected'}>Kostenart wählen</option>${RPJ_CATEGORIES.map(([c, l]) => html`<option value="${c}" ${c === current ? 'selected' : ''}>${c}) ${l}${c === 'i' && kind === 'cost' ? ' (Erstattung)' : ''}</option>`)}`;
}

const LABEL = { cost: 'RPJ-Projekt und Kostenart (Pflicht)', fee: 'RPJ-Projekt des Teilnahmebeitrags (Pflicht)', grant: 'RPJ: Mittelabruf bzw. Projekt (Pflicht)' };

/** Feld für die RPJ-Zuordnung (versteckt, solange kein RPJ-Posten gewählt ist). key=true: data-key für Formulare (Eingangsrechnungen). */
export function rpjField({ key = false } = {}) {
  return html`<div class="field rpj-field" data-rpj hidden><span data-rpj-label>RPJ-Zuordnung (Pflicht)</span>
    <div class="rpj-pick">
      <select data-rpj-target ${key ? html`data-key="rpj_target"` : ''} aria-label="RPJ-Projekt bzw. Mittelabruf"></select>
      <select data-rpj-category ${key ? html`data-key="rpj_category"` : ''} aria-label="RPJ-Kostenart"></select>
    </div>
    <p class="small muted" data-rpj-hint hidden></p></div>`;
}

/** RPJ-Feld in box aktivieren. kind(): RPJ-Art des gewählten Postens (oder null), year(): Haushaltsjahr der Buchung. */
export function bindRpj(box, { kind, year, onChange = () => {} }) {
  const target = box.querySelector('[data-rpj-target]'), category = box.querySelector('[data-rpj-category]');
  const hint = box.querySelector('[data-rpj-hint]');
  let drawn = '';
  const refresh = () => {
    const k = kind(), y = year();
    box.hidden = !k;
    if (k) {
      if (drawn !== `${k}|${y}`) {
        mount(target, targetOptions(k, y, target.value));
        mount(category, categoryOptions(category.value, k));
        drawn = `${k}|${y}`;
      }
      category.hidden = k !== 'cost';
      const none = !rpjProjects(y).length && !(k === 'grant' && rpjRequests(y).length);
      hint.hidden = !none;
      hint.textContent = none ? `Für ${y} gibt es noch keine RPJ-Projekte${k === 'grant' ? ' oder Mittelabrufe' : ''} – zuerst auf der Seite „RPJ-Mittel“ anlegen.` : '';
      box.querySelector('[data-rpj-label]').textContent = LABEL[k];
    }
    onChange();
  };
  target.onchange = refresh;
  category.onchange = () => onChange();
  box.refresh = refresh;
  refresh();
}

/** Zuordnung für den Server ({target, category}) bzw. null, wenn kein RPJ-Posten gewählt ist. */
export function rpjValue(box) {
  if (!box || box.hidden) return null;
  const category = box.querySelector('[data-rpj-category]');
  return { target: box.querySelector('[data-rpj-target]').value, category: category.hidden ? '' : category.value };
}

/** Was noch fehlt ('' = vollständig). */
export function rpjMissing(box) {
  const v = rpjValue(box);
  if (!v) return '';
  if (!v.target) return box.querySelector('[data-rpj-category]').hidden ? 'RPJ-Zuordnung wählen' : 'RPJ-Projekt wählen';
  if (!box.querySelector('[data-rpj-category]').hidden && !v.category) return 'RPJ-Kostenart wählen';
  return '';
}
