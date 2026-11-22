// Gliederung (Stammdaten, gliederung.py): Kreisverband mit Nummer der CDU-Mitgliederverwaltung und Ortsverbände je Kreisverband.
// kvOvFields + bindKvOv: Kreisverband als Auswahlliste, Ortsverband nur aus dem gewählten Kreisverband (Wunsch Lennert Gust 10.11.2026).
// Für jede weitere Auswahl von Kreis-/Ortsverband wiederverwenden.
import { html, mount } from './html.js';
import { store } from './store.js';

/** Kreisverbände eines Mitglieds: ohne Landesverband; Schüler Union (5800) nur, wenn schon gewählt (Budgetbereich, kein Kreisverband). */
export const memberKvs = (current = '') => store.data.kv.filter((k) => !['LV', '5800'].includes(k.code) || k.code === current);

/** „Altona / Elbvororte (701)“ – mit Nummer der Mitgliederverwaltung, falls hinterlegt. */
export const kvLabel = (k) => (k.cdu_number ? `${k.name} (${k.cdu_number})` : k.name);

export const ovById = (id) => (id ? (store.data.ovs || []).find((o) => o.id === Number(id)) : undefined);

/** „12 Blankenese“; ohne Nummer nur der Name. */
export const ovLabel = (o) => (o ? (o.number ? `${o.number} ${o.name}` : o.name) : '');

/** Ortsverbände eines Kreisverbands (aktive; ein deaktivierter nur, wenn er gerade gewählt ist). */
export const ovsOf = (kv, current = null) => (store.data.ovs || []).filter((o) => o.kv === kv && (o.active || o.id === Number(current)));

const ovOptions = (kv, current) => {
  const list = ovsOf(kv, current);
  return html`<option value="">${list.length ? '– kein Ortsverband –' : 'keine Ortsverbände hinterlegt'}</option>${list.map((o) => html`
    <option value="${o.id}" ${o.id === Number(current) ? 'selected' : ''}>${ovLabel(o)}${o.active ? '' : ' (deaktiviert)'}</option>`)}`;
};

/** Zwei Auswahlfelder (Namen kv und ov_id) für Formulare. */
export function kvOvFields(kv, ovId) {
  const kvs = memberKvs(kv);
  const selected = kvs.some((k) => k.code === kv) ? kv : '';
  return html`<label class="field">Kreisverband<select name="kv" required data-kv-select>
      ${selected ? '' : html`<option value="" selected disabled>Bitte wählen</option>`}
      ${kvs.map((k) => html`<option value="${k.code}" ${k.code === selected ? 'selected' : ''}>${kvLabel(k)}</option>`)}</select></label>
    <label class="field">Ortsverband<select name="ov_id" data-ov-select>${ovOptions(selected, ovId)}</select>
      <span class="small muted">Nur Ortsverbände des gewählten Kreisverbands (Einstellungen → Gliederung).</span></label>`;
}

/** Kreis- und Ortsverband setzen (Vorschlag nach der Adresse, 0.52) – ohne change-Ereignis, damit es nicht als „von Hand gewählt“ gilt. */
export function setKvOv(form, kv, ovId) {
  const kvSelect = form.querySelector('[data-kv-select]');
  const ov = form.querySelector('[data-ov-select]');
  if (!kvSelect || !ov || ![...kvSelect.options].some((o) => o.value === kv)) return false;
  kvSelect.value = kv;
  mount(ov, ovOptions(kv, ovId));
  return true;
}

/** Ortsverband-Liste beim Wechsel des Kreisverbands neu füllen; ein Ortsverband des alten Kreisverbands entfällt. */
export function bindKvOv(form) {
  const kv = form.querySelector('[data-kv-select]');
  const ov = form.querySelector('[data-ov-select]');
  if (!kv || !ov) return;
  kv.addEventListener('change', () => {
    const keep = ovsOf(kv.value).some((o) => o.id === Number(ov.value)) ? ov.value : '';
    mount(ov, ovOptions(kv.value, keep));
  });
}
