// Listen der Mitgliederverwaltung (0.43): feste Ansichten der Mitgliederliste – eine Stelle für die Reiter der Seite „Mitglieder“ und
// die Aufgaben im Überblick. Jede Liste prüft ein Mitglied aus store.data.members (ohne Beträge: dues_state / dues_open_years kommen
// mit „Mitglieder lesen“). Gespeicherte Ansichten (Liste + Kreis-/Ortsverband + Suche) liegen nur in diesem Browser.
import { todayIso } from './format.js';
import { store } from './store.js';

export const isActive = (m, today = todayIso()) => !m.left_on || m.left_on > today;
const birthYear = (m) => (m.birth ? Number(m.birth.slice(0, 4)) : 0);

/** Anschrift vollständig (Straße, PLZ, Ort) – dieselbe Regel wie mitgliederlisten.postfaehig ohne den Postausschluss. */
export const anschriftVollstaendig = (m) => !!(m.street && m.zip && m.city);
/** Was in den Stammdaten fehlt (ohne Kontaktdaten, die erst die Akte lädt). */
export const fehlt = (m) => [!anschriftVollstaendig(m) && 'Anschrift', !m.email && 'E-Mail', !m.birth && 'Geburtsdatum', !m.ov_id && 'Ortsverband'].filter(Boolean);

/** Feste Listen: id, Bezeichnung, Prüfung, Hinweis für den Überblick. year = gewähltes Haushaltsjahr.
 * arbeit: Arbeitsliste (etwas ist zu prüfen oder zu pflegen) – die Mitgliederliste zeigt sie in einer eigenen Reihe unter dem Bestand. */
export function listen(year = store.year, today = todayIso()) {
  const y = String(year);
  const wahl = store.data.members.some((m) => m.wahl);
  const monthEnd = `${today.slice(0, 7)}-31`;
  const offen = new Set(store.data.kenntnis?.offen || []);   // ab 0.54: Kennungen mit offener Änderung (kenntnis.py)
  return [
    { id: 'aktiv', label: 'Alle aktiven', test: (m) => isActive(m, today) },
    { id: 'neu', label: `Neu ${year}`, test: (m) => (m.joined || '').startsWith(y), hint: `Eintritte seit 01.01.${year}` },
    { id: 'ohne-mail', arbeit: true, label: 'Ohne E-Mail', test: (m) => isActive(m, today) && !m.email, hint: 'Für Einladungen und Rechnungen per E-Mail' },
    { id: 'aenderung-offen', arbeit: true, label: 'Änderung offen', test: (m) => offen.has(m.id), hint: 'Geändert und noch nicht zur Kenntnis genommen' },
    { id: 'anschrift', arbeit: true, label: 'Anschrift prüfen', test: (m) => isActive(m, today) && !anschriftVollstaendig(m), hint: 'Straße, PLZ oder Ort fehlen' },
    { id: 'ohne-ov', arbeit: true, label: 'Ohne Ortsverband', test: (m) => isActive(m, today) && !m.ov_id, hint: 'Kein Ortsverband eingetragen' },
    { id: 'werden-35', arbeit: true, label: `Werden ${year} 35`, test: (m) => isActive(m, today) && birthYear(m) === year - 35, hint: 'Altersgrenze nach Satzung § 2 Abs. 1 Nr. 4' },
    { id: 'iban-ohne-mandat', arbeit: true, label: 'IBAN ohne Mandat', test: (m) => isActive(m, today) && m.hat_iban && (!m.sepa_mandat || m.iban_abweichend),
      hint: 'Bankdaten liegen vor, ein gültiges SEPA-Mandat für dieses Konto fehlt (auch Kontowechsel) – auswählen und „SEPA-Mandate“ drucken' },
    { id: 'beitrag-offen', arbeit: true, label: 'Beitrag offen', test: (m) => isActive(m, today) && (m.dues_open_years || []).length > 0, hint: 'Fällige Beiträge nicht eingegangen' },
    ...(wahl ? [{ id: 'wahlberechtigt', label: 'Wahlberechtigt', test: (m) => isActive(m, today) && m.wahl?.status === 'wahlberechtigt' },
      { id: 'nicht-wahlberechtigt', arbeit: true, label: 'Nicht wahlberechtigt', test: (m) => isActive(m, today) && m.wahl?.status === 'nicht', hint: 'Grund steht in der Liste' }] : []),
    { id: 'austritt-bald', arbeit: true, label: 'Austritt angekündigt', test: (m) => !!m.left_on && m.left_on > today, hint: 'Austritt mit künftigem Datum eingetragen',
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
