// Zentraler Zustand der Oberfläche. Daten kommen ausschließlich vom Server; hier wird nichts berechnet,
// was der Server verbindlich entscheidet (Buchungen, Beiträge, Budgets).
import { api } from './api.js';

export const store = {
  user: '',
  version: '',
  mode: 'mac',       // 'mac' (Programmfenster) oder 'server' (Browser, Anmeldung mit Passwort)
  year: new Date().getFullYear(),
  page: 'overview',
  data: null,        // /api/data: Mitglieder, Rechnungen, Bank, Konten, Journal, Einstellungen …
  household: null,   // /api/haushalt: Gesamthaushalt Plan/Ist
  workflow: null,    // /api/workflow: KV-Budgets, Parkkonto, Lernbeispiele
  bank: { filter: 'open', search: '', selected: null },
  cash: { filter: 'open', search: '', selected: null },   // Kasse (Kassenbelege, ab 0.25)
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

export async function refresh() {
  const [data, household, workflow] = await Promise.all([
    api('/api/data?year=' + store.year),
    api('/api/haushalt?year=' + store.year),
    api('/api/workflow?year=' + store.year),
  ]);
  Object.assign(store, { data, household, workflow });
  store.budgets.donations = null;  // Spenden-Reiter lädt nach jeder Änderung neu
  store.konten.listKey = store.konten.sheetKey = null;   // Kontenliste und Kontenblatt ebenso
  store.budgets.kvKey = null;      // Kreisverbandsseite ebenso
  render();
}

export function go(page, options = {}) {
  store.page = page;
  if (options.bankFilter) {
    store.bank.filter = options.bankFilter;
    store.bank.selected = null;
  }
  if (options.cashFilter) {
    store.cash.filter = options.cashFilter;
    store.cash.selected = null;
  }
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

/** Umsätze des gewählten Haushaltsjahres (nach Datum): Kontoauszug ('bank') oder Kassenbelege ('cash'). */
export const ledgerRows = (ledger) => store.data.bank.filter((b) => (b.ledger || 'bank') === ledger && b.booked.startsWith(String(store.year)));
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
