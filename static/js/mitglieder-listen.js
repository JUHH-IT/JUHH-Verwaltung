// Listen der Mitgliederverwaltung (0.43): feste Ansichten der Mitgliederliste – eine Stelle für die Reiter der Seite „Mitglieder“ und
// die Aufgaben im Überblick. Jede Liste prüft ein Mitglied aus store.data.members (ohne Beträge: dues_state / dues_open_years kommen
// mit „Mitglieder lesen“). Gespeicherte Ansichten (Liste + Kreis-/Ortsverband + Suche) liegen nur in diesem Browser.
import { todayIso } from './format.js';
import { store } from './store.js';

export const isActive = (m, today = todayIso()) => !m.left_on || m.left_on > today;
const birthYear = (m) => (m.birth ? Number(m.birth.slice(0, 4)) : 0);

/** Was in den Stammdaten fehlt (ohne Kontaktdaten, die erst die Akte lädt). */
export const fehlt = (m) => [!m.zip && 'Anschrift', !m.email && 'E-Mail', !m.birth && 'Geburtsdatum', !m.ov_id && 'Ortsverband'].filter(Boolean);

/** Feste Listen: id, Bezeichnung, Prüfung, Hinweis für den Überblick. year = gewähltes Haushaltsjahr. */
export function listen(year = store.year, today = todayIso()) {
  const y = String(year);
  const wahl = store.data.members.some((m) => m.wahl);
  const monthEnd = `${today.slice(0, 7)}-31`;
  return [
    { id: 'aktiv', label: 'Alle aktiven', test: (m) => isActive(m, today) },
    { id: 'neu', label: `Neu ${year}`, test: (m) => (m.joined || '').startsWith(y), hint: `Eintritte seit 01.01.${year}` },
    { id: 'ohne-mail', label: 'Ohne E-Mail', test: (m) => isActive(m, today) && !m.email, hint: 'Für Einladungen und Rechnungen per E-Mail' },
    { id: 'anschrift', label: 'Anschrift prüfen', test: (m) => isActive(m, today) && !m.zip, hint: 'Straße, PLZ oder Ort fehlen' },
    { id: 'werden-35', label: `Werden ${year} 35`, test: (m) => isActive(m, today) && birthYear(m) === year - 35, hint: 'Altersgrenze nach Satzung § 2 Abs. 1 Nr. 4' },
    { id: 'beitrag-offen', label: 'Beitrag offen', test: (m) => isActive(m, today) && (m.dues_open_years || []).length > 0, hint: 'Fällige Beiträge nicht eingegangen' },
    ...(wahl ? [{ id: 'wahlberechtigt', label: 'Wahlberechtigt', test: (m) => isActive(m, today) && m.wahl?.status === 'wahlberechtigt' },
      { id: 'nicht-wahlberechtigt', label: 'Nicht wahlberechtigt', test: (m) => isActive(m, today) && m.wahl?.status === 'nicht', hint: 'Grund steht in der Liste' }] : []),
    { id: 'austritt-bald', label: 'Austritt angekündigt', test: (m) => !!m.left_on && m.left_on > today, hint: 'Austritt mit künftigem Datum eingetragen',
      soon: (m) => m.left_on <= monthEnd },
    { id: 'ausgetreten', label: 'Ausgetreten', test: (m) => !isActive(m, today) },
    { id: 'alle', label: 'Alle', test: () => true },
  ];
}

const KEY = () => `ju-ansichten:${store.user || ''}`;
/** Gespeicherte Ansichten dieses Browsers: [{name, liste, kv, ov, suche}] – nur Filter, keine Mitgliederdaten. */
export function gespeichert() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY()) || '[]');
    return Array.isArray(list) ? list.filter((v) => v && typeof v.name === 'string') : [];
  } catch {
    return [];
  }
}
export function speichern(list) {
  try {
    localStorage.setItem(KEY(), JSON.stringify(list));
    return true;
  } catch {
    return false;
  }
}
