// Darstellung von Beträgen (Cent) und Daten.

const currency = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });
const number = new Intl.NumberFormat('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 12345 → „123,45 €“ */
export const eur = (cents) => currency.format((cents || 0) / 100);

/** Mit Vorzeichen: 12345 → „+123,45 €“, -500 → „−5,00 €“ */
export const signed = (cents) => (cents > 0 ? '+' : cents < 0 ? '−' : '') + currency.format(Math.abs(cents || 0) / 100);

/** Ohne Währungszeichen, für Tabellen: 12345 → „123,45“; null → null */
export const plain = (cents) => (cents === null || cents === undefined ? null : number.format(cents / 100));

/** Für Eingabefelder: 12345 → „123,45“ */
export const toInput = (cents) => (cents === null || cents === undefined ? '' : (cents / 100).toFixed(2).replace('.', ','));

/** „2026-10-28“ → „28.10.2026“ */
export const date = (iso) => (iso ? new Date(iso + 'T12:00:00').toLocaleDateString('de-DE') : '—');

/** „2026-10-28T14:25:11“ → „28.10.2026 · 14:25“ (Ortszeit wie gespeichert) */
export const dateTime = (stamp) => {
  if (!stamp) return '—';
  const [day, time = ''] = stamp.replace('T', ' ').split(' ');
  return time ? `${date(day)} · ${time.slice(0, 5)}` : date(day);
};

/** „2027-01-30“ → „Sa., 30.01.2027“ (Stichtage: der Wochentag gehört zur Prüfung) */
export const weekdayDate = (iso) => (iso ? new Date(iso + 'T12:00:00').toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric' }) : '—');

/** „2026-10-28“ → „28.10.“ */
export const shortDate = (iso) => (iso ? new Date(iso + 'T12:00:00').toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }) : '—');

/**
 * Eingabe → Cent oder null (nur für die Anzeige der Differenz; verbindlich prüft der Server).
 * „1.500“ = 1.500 €, „12,5“ = 12,50 €, entspricht formate.money auf dem Server.
 */
export function parseCents(text) {
  let s = String(text ?? '').trim().replace(/[\s€']/g, '').replace('−', '-');
  if (!s) return null;
  let sign = 1;
  if (s.endsWith('-')) { sign = -1; s = s.slice(0, -1); }
  if (s.startsWith('-')) { sign = -sign; s = s.slice(1); } else if (s.startsWith('+')) s = s.slice(1);
  if (s.includes(',') && s.includes('.')) s = s.lastIndexOf(',') > s.lastIndexOf('.') ? s.replace(/\./g, '').replace(',', '.') : s.replace(/,/g, '');
  else if (s.includes(',')) { if ((s.match(/,/g) || []).length > 1) return null; s = s.replace(',', '.'); }
  else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  return sign * Math.round(Number(s) * 100);
}

/** IBAN aus einem Verwendungszweck herauslesen (nur Anzeige). */
export function findIban(text) {
  const match = String(text || '').match(/\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b/);
  return match ? match[0].replace(/(.{4})/g, '$1 ').trim() : '';
}

/** Buchungsart (erstes Wort in Großbuchstaben) aus dem Verwendungszweck. */
export function bookingKind(text) {
  const first = String(text || '').split(/\s+/)[0] || '';
  return /^[A-ZÄÖÜ][A-ZÄÖÜ-]{2,}$/.test(first) ? first.charAt(0) + first.slice(1).toLowerCase() : '';
}

/** Heutiges Datum (Ortszeit) als „JJJJ-MM-TT“. */
export function todayIso() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
