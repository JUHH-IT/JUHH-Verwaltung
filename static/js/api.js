// Zugriff auf den lokalen Server. Schreibende Aufrufe tragen das CSRF-Token der Sitzung.

let csrf = '';
let unauthorized = null;

export function setCsrf(token) {
  csrf = token || '';
}

/** Rückruf, wenn der Server „Bitte anmelden“ meldet (Sitzung abgelaufen, z. B. nach Untätigkeit im Serverbetrieb). */
export function onUnauthorized(fn) {
  unauthorized = fn;
}

function expired(url, response) {
  if (response.status === 401 && unauthorized && !url.startsWith('/api/login') && !url.startsWith('/api/status')) unauthorized();
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

async function request(url, data) {
  const post = data !== undefined;
  busy(1);
  try {
    let response;
    try {
      response = await fetch(url, {
        method: post ? 'POST' : 'GET',
        headers: post ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } : {},
        body: post ? JSON.stringify(data) : undefined,
      });
    } catch {
      throw new Error(OFFLINE);
    }
    let json = {};
    try {
      json = await response.json();
    } catch {
      // keine JSON-Antwort
    }
    if (!response.ok) {
      expired(url, response);
      const error = new Error(json.error || (response.status >= 502 ? OFFLINE : 'Aktion fehlgeschlagen.'));
      if (json.password) error.password = json.password;   // geschützte Datei: 'required' oder 'wrong' (Kennwort abfragen)
      throw error;
    }
    return json;
  } finally {
    busy(-1);
  }
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
 *  Im Programmfenster lädt macOS die Datei direkt (Sichern-Dialog, Fehlermeldung des Servers als Hinweis); der Umweg
 *  über eine Blob-Adresse entfällt dort, weil WebKit ihn nicht zuverlässig als Download behandelt.
 *  Rückgabe: true = Datei liegt vor (Browser), false = Programmfenster übernimmt (Ergebnis zeigt macOS). */
export async function download(url, filename) {
  if (window.webkit?.messageHandlers?.ju) {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    return false;
  }
  let response;
  try {
    response = await fetch(url);
  } catch {
    throw new Error(OFFLINE);
  }
  if (!response.ok) {
    expired(url, response);
    let json = {};
    try {
      json = await response.json();
    } catch {
      // keine JSON-Antwort
    }
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
