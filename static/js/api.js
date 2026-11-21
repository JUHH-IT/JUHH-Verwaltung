// Zugriff auf den lokalen Server. Schreibende Aufrufe tragen das CSRF-Token der Sitzung.

let csrf = '';
let unauthorized = null;
let confirmer = null;
let locked = null;

export function setCsrf(token) {
  csrf = token || '';
}

/** Rückruf, wenn der Server „Bitte anmelden“ meldet (Sitzung abgelaufen, z. B. nach Untätigkeit im Serverbetrieb). */
export function onUnauthorized(fn) {
  unauthorized = fn;
}

/** Rückruf für kritische Aktionen (Serverbetrieb, ab 0.35): der Server verlangt das Passwort erneut (428 `confirm`).
 *  fn fragt es ab und löst auf (bestätigt) oder wirft „Abgebrochen.“; danach wird die Anfrage genau einmal wiederholt. */
export function onConfirm(fn) {
  confirmer = fn;
}

/** Rückruf, wenn der Server wegen Notfallmodus oder Wartung sperrt (503 mit `sperre`). */
export function onLocked(fn) {
  locked = fn;
}

function expired(url, response) {
  if (response.status === 401 && unauthorized && !url.startsWith('/api/login') && !url.startsWith('/api/status')) unauthorized();
}

/** Fehlerantwort auswerten. true = Passwort bestätigt, Anfrage einmal wiederholen. */
async function failed(url, response, json, confirmed) {
  if (response.status === 428 && json.confirm && confirmer && !confirmed) {
    await confirmer();
    return true;
  }
  expired(url, response);
  if (response.status === 503 && json.sperre && locked && !url.startsWith('/api/login')) locked(json.sperre);
  return false;
}

const OFFLINE = 'Keine Verbindung zum Server. Bitte Internet- bzw. Tailscale-Verbindung prüfen und erneut versuchen.';
const inflight = new Map();   // gleiche Aktion läuft schon (Doppelklick) → dieselbe Anfrage abwarten statt doppelt buchen
let pending = 0;
let busyTimer = null;

/** Ladeanzeige (oberer Rand), wenn eine Anfrage länger als 0,4 s dauert. */
function busy(delta) {
  pending = Math.max(0, pending + delta);
  clearTimeout(busyTimer);
  if (pending) busyTimer = setTimeout(() => document.body.classList.add('busy'), 400);
  else document.body.classList.remove('busy');
}

async function request(url, data, confirmed = false) {
  const post = data !== undefined;
  let response;
  let json = {};
  busy(1);
  try {
    try {
      response = await fetch(url, {
        method: post ? 'POST' : 'GET',
        headers: post ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } : {},
        body: post ? JSON.stringify(data) : undefined,
      });
    } catch {
      throw new Error(OFFLINE);
    }
    try {
      json = await response.json();
    } catch {
      // keine JSON-Antwort
    }
  } finally {
    busy(-1);   // vor einer Passwortabfrage: keine Ladeanzeige, solange der Dialog offen ist
  }
  if (response.ok) return json;
  if (await failed(url, response, json, confirmed)) return request(url, data, true);
  const error = new Error(json.error || (response.status >= 502 ? OFFLINE : 'Aktion fehlgeschlagen.'));
  if (json.password) error.password = json.password;   // geschützte Datei: 'required' oder 'wrong' (Kennwort abfragen)
  throw error;
}

export function api(url, data) {
  if (data === undefined) return request(url);
  const key = url + '\n' + JSON.stringify(data);
  if (inflight.has(key)) return inflight.get(key);
  const promise = request(url, data).finally(() => inflight.delete(key));
  inflight.set(key, promise);
  return promise;
}

/** Datei als Base64 für die Import-Schnittstellen. */
export async function fileToBase64(file) {
  if (file.size > 10_000_000) throw new Error('Maximal 10 MB pro Datei.');
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

/** Datei vom Server laden und speichern – bei einem Fehler die Meldung des Servers statt einer kaputten Datei.
 *  data: Angaben für einen POST (z. B. eine Auswahl von Mitgliedern), sonst GET. Rückgabe: true = Datei liegt vor. */
export async function download(url, filename, data = undefined, confirmed = false) {
  let response;
  try {
    response = await fetch(url, data === undefined ? undefined
      : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(data) });
  } catch {
    throw new Error(OFFLINE);
  }
  if (!response.ok) {
    let json = {};
    try {
      json = await response.json();
    } catch {
      // keine JSON-Antwort
    }
    if (await failed(url, response, json, confirmed)) return download(url, filename, data, true);
    throw new Error(json.error || 'Download fehlgeschlagen.');
  }
  const link = document.createElement('a');
  link.href = URL.createObjectURL(await response.blob());
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 30000);
  return true;
}
