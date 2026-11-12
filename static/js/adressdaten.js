// Amtliche Hamburger Adressdaten (ab 0.32, Server: adressen.py): Stand abfragen und den ganzen öffentlichen Bestand laden.
// Der Abruf läuft im Hintergrund auf dem Server; hier wird nur der Fortschritt gezeigt. Es geht dabei nie eine Mitgliederadresse ins Netz.
import { api } from './api.js';
import { html } from './html.js';
import { dateTime } from './format.js';

export const addressState = () => api('/api/adressen-status');

/** Text zum Stand der Adressdaten. */
export function addressSummary(state) {
  if (state.job?.state === 'running') return `Adressdaten werden geladen … ${state.job.total ? `${Math.round(state.job.done / state.job.total * 100)} %` : ''}`;
  if (!state.available) return 'Noch nicht geladen.';
  return `${state.adressen.toLocaleString('de-DE')} Adressen in ${state.strassen.toLocaleString('de-DE')} Straßen · Stand ${dateTime(state.stand)}`;
}

/** Quellenangabe (Lizenz dl-de/by-2-0 verlangt die Namensnennung). */
export const addressCredit = () => html`Adressen und Gebiete: Freie und Hansestadt Hamburg, Landesbetrieb Geoinformation und Vermessung
  (Datenlizenz Deutschland – Namensnennung – Version 2.0).`;

/** Abruf starten (start: false = einen schon laufenden nur verfolgen) und warten, bis er fertig ist; onProgress(state) etwa jede
 * Sekunde. Wirft bei einem Fehler dessen Meldung. Ergebnis: Stand mit `loaded` (wurde wirklich abgerufen?). */
export const loadedText = (state) => (state.loaded ? 'Adressdaten geladen.' : 'Die Adressdaten sind aktuell – sie wurden eben erst geladen.');

export async function loadAddressData(onProgress = () => {}, alive = () => true, { start = true } = {}) {
  let state = start ? await api('/api/adressen-laden', {}) : await addressState();
  const loaded = state.job.state === 'running';   // false: eben erst geladen (Sperrfrist) – es wurde nichts abgerufen
  onProgress(state);
  while (state.job.state === 'running' && alive()) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    state = await addressState();
    onProgress(state);
  }
  if (state.job.state === 'error') throw new Error(state.job.error || 'Die Adressdaten konnten nicht geladen werden.');
  return { ...state, loaded };
}
