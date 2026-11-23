// Zentraler Zustand der Oberfläche. Daten kommen ausschließlich vom Server; hier wird nichts berechnet,
// was der Server verbindlich entscheidet (Buchungen, Beiträge, Budgets).
import { api } from './api.js';

const KASSE_KEY = 'ju-finanzen-kasse';
function gemerkteKasse() {
  try {
    const roh = localStorage.getItem(KASSE_KEY);
    return /^[0-9]+$/.test(roh || '') ? Number(roh) : null;
  } catch { return null; /* kein Speicher: ohne Wahl */ }
}

export const store = {
  user: '',
  version: '',
  auth: '',          // Anmeldung: 'password' | 'keycloak' (ab 0.36)
  passwortLokal: false,   // eigenes Passwort im Programm (Passwortbetrieb; im Keycloak-Betrieb nur der Notzugang der Verwaltung)
  rights: {},        // Rechte der angemeldeten Person je Bereich: 'lesen' | 'schreiben' (ab 0.34; der Server prüft verbindlich)
  year: new Date().getFullYear(),
  page: 'overview',
  data: null,        // /api/data: Mitglieder, Rechnungen, Bank, Konten, Journal, Einstellungen …
  household: null,   // /api/haushalt: Gesamthaushalt Plan/Ist
  workflow: null,    // /api/workflow: KV-Budgets, Parkkonto, Lernbeispiele
  bank: { filter: 'open', search: '', selected: null, modus: 'liste' },   // Kontoumsätze: 'liste' oder 'verbuchen' (Maske, ab 0.58)
  cash: { filter: 'open', search: '', selected: null, kasse: gemerkteKasse() },   // Kasse (Kassenbelege, ab 0.25); kasse: gewählte Kasse (ab 0.60, null = erste eingerichtete)
  budgets: { mode: 'household', onlyDeviations: false, kvSearch: '', kvAttention: false, kvSelected: null, kvKey: null, kvData: null },
  journal: { limit: 300, search: '' },
  konten: { search: '', onlyUsed: false, selected: null, kv: '', list: null, listKey: null, sheet: null, sheetKey: null, error: '' },
};

let renderer = () => {};
export function onChange(fn) {
  renderer = fn;
}
export function render() {
  renderer();
}

const RANK = { lesen: 1, schreiben: 2 };
/** Darf die angemeldete Person diesen Bereich lesen bzw. ändern? Nur für die Anzeige – verbindlich prüft der Server. */
export const can = (area, level = 'lesen') => (RANK[store.rights?.[area]] || 0) >= RANK[level];

export async function refresh() {
  const [data, household, workflow, status] = await Promise.all([
    api('/api/data?year=' + store.year),
    api('/api/haushalt?year=' + store.year),
    api('/api/workflow?year=' + store.year),
    api('/api/status'),
  ]);
  Object.assign(store, { data, household, workflow, rights: status.rights || {} });
  store.budgets.donations = null;  // Spenden-Reiter lädt nach jeder Änderung neu
  store.konten.listKey = store.konten.sheetKey = null;   // Kontenliste und Kontenblatt ebenso
  store.budgets.kvKey = null;      // Kreisverbandsseite ebenso
  render();
}

export function go(page, options = {}) {
  store.page = page;
  if (page === 'bank') store.bank.modus = options.bankFilter ? 'verbuchen' : 'liste';   // Kontoumsätze: Liste; mit Filter (Aufgabe vom Start) gleich die Maske
  if (options.bankFilter) {
    store.bank.filter = options.bankFilter;
    store.bank.selected = null;
  }
  if (options.cashFilter) {
    store.cash.filter = options.cashFilter;
    store.cash.selected = null;
  }
  if (options.kasse !== undefined) setKasse(options.kasse);   // Seite Kasse: diese Kasse zeigen
  if (options.rpjAssign) store.rpjAssign = true;   // RPJ-Seite öffnet „RPJ zuordnen“
  if (options.openSide) store.openSide = options.openSide;   // Offene Posten: Reiter (dues | receivables | payables)
  if (options.opKv !== undefined) store.opKv = options.opKv;  // … und Kreisverband der OP-Liste
  if (options.settingsTab) store.settingsTab = options.settingsTab;   // Einstellungen: Reiter
  if (options.caseId) store.caseId = options.caseId;                  // Klärfälle: diesen Fall öffnen
  if (options.budgetMode) { store.budgets.mode = options.budgetMode; store.budgets.kvSelected = options.kv || null; }
  render();
  window.scrollTo(0, 0);
}

// ---- Nachschlagen ----
export const kvName = (code) => store.data?.kv.find((k) => k.code === code)?.name || code;
export const accountName = (code) => store.data?.accounts.find((a) => a.code === code)?.name || '';
export const titleOf = (id) => store.household?.titles.find((t) => t.id === id);
export const titleLabel = (id) => {
  const t = titleOf(id);
  return t ? `${t.code ? t.code + ' · ' : ''}${t.label}` : id || '';
};

/** Kasse wählen – die Wahl gilt je Browser weiter (localStorage, wie die Darstellung), auch nach dem Neuladen der Seite. */
export function setKasse(id) {
  store.cash.kasse = id;
  try { localStorage.setItem(KASSE_KEY, String(id)); } catch { /* Speicher blockiert: gilt nur für diese Seite */ }
}
/** Gewählte Kasse der Seite Kasse (ab 0.60 mehrere: Bargeld, PayPal …) – ohne Wahl oder wenn es die gemerkte nicht (mehr) gibt
 *  oder sie stillgelegt ist, die erste eingerichtete (Bargeldkasse zuerst). */
export function currentKasse() {
  const all = (store.data?.cash?.kassen || []).filter((k) => k.aktiv);
  return all.find((k) => k.id === store.cash.kasse) || all.find((k) => k.start) || all[0];
}
/** Gegenkonto eines Umsatzes: Bank 1420 bzw. das Konto seiner Kasse (wie kasse.ledger_account). */
export const ownAccount = (b) => (b.ledger === 'cash' ? store.data.cash.kassen.find((k) => k.id === (b.kasse || 0))?.account : '1420');
/** Gehört der Umsatz zum Kontoauszug ('bank') bzw. zur gewählten Kasse ('cash')? */
export const inLedger = (b, ledger) => (b.ledger || 'bank') === ledger && (ledger !== 'cash' || (b.kasse || 0) === currentKasse()?.id);
/** Umsätze des gewählten Haushaltsjahres (nach Datum): Kontoauszug ('bank') oder Belege der gewählten Kasse ('cash'). */
export const ledgerRows = (ledger) => store.data.bank.filter((b) => inLedger(b, ledger) && b.booked.startsWith(String(store.year)));
/** Bankumsätze des gewählten Haushaltsjahres (nach Bankdatum). */
export const yearBank = () => ledgerRows('bank');

/** Offene Rechnungen, die zu einem Bankumsatz passen können. */
export function eligibleInvoices(bank) {
  return store.data.invoices.filter((i) => i.status === 'issued' && (bank.amount > 0 ? i.total > i.paid : i.paid > 0));
}

/** Offene Eingangsrechnungen (Verbindlichkeiten). */
export const openBills = () => store.data.bills.filter((b) => b.status === 'open' && b.open > 0);

/** Tage seit Fälligkeit (negativ = noch nicht fällig). */
export function daysOverdue(due) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12);
  return Math.round((today - new Date(due + 'T12:00:00')) / 86400000);
}
