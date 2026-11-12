// Browser-Durchlauf im Serverbetrieb (Playwright) mit erfundenen Daten:
// Anmeldung mit Passwort, Sperre, alle Seiten auf Laptop und Handy (kein seitliches Scrollen), Menü, Dialoge,
// Passwort ändern, abgelaufene Sitzung. Aufruf: NODE_PATH=$(npm root -g) node tests/ui_server.mjs [Ordner für Bildschirmfotos]
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)('playwright');
const repo = join(dirname(fileURLToPath(import.meta.url)), '..');
const shots = process.argv[2] || join(tmpdir(), 'ju-ui-server-shots');
mkdirSync(shots, { recursive: true });
const base = mkdtempSync(join(tmpdir(), 'ju-server-'));
const data = join(base, 'Daten');
const port = 8900 + Math.floor(Math.random() * 90);
const url = `http://127.0.0.1:${port}`;
const PASSWORT = 'Erfundenes-Passwort-2026';
const NEU = 'Noch-ein-Passwort-2027';
const failures = [];
const check = (ok, message) => { if (!ok) failures.push(message); console.log((ok ? 'ok   ' : 'FEHLER ') + message); };

// Passwort wie auf dem Server über verwaltung.py setzen (Passwort zweimal über stdin)
execFileSync('python3', [join(repo, 'verwaltung.py'), '--data', data, 'passwort', 'Test Schatzmeisterin'], { input: `${PASSWORT}\n${PASSWORT}\n` });
const env = { ...process.env, JU_FINANZEN_OHNE_SCHLUESSELBUND: '1', JU_FINANZEN_MODUS: 'server', JU_FINANZEN_GEHEIMNISSE: join(base, 'geheim') };
const server = spawn('python3', [join(repo, 'tests', 'ui_start.py'), '--no-browser', '--port', String(port), '--data', data, '--backups', join(base, 'Sicherungen')], { stdio: 'ignore', env });

async function waitForServer() {
  for (let i = 0; i < 50; i++) {
    try { if ((await fetch(`${url}/api/status`)).ok) return; } catch { /* startet noch */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error('Server startet nicht');
}

// Testdaten über die Schnittstelle (eigene Sitzung): Excel-Master mit Bankumsätzen und Buchungen
async function seed() {
  const login = await fetch(`${url}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: url },
    body: JSON.stringify({ name: 'Test Schatzmeisterin', password: PASSWORT }) });
  const cookie = login.headers.get('set-cookie').split(';')[0];
  const { csrf } = await login.json();
  const post = async (path, body) => {
    const r = await fetch(url + path, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: url, Cookie: cookie, 'X-CSRF-Token': csrf }, body: JSON.stringify(body) });
    const json = await r.json();
    if (!r.ok) throw new Error(path + ': ' + json.error);
    return json;
  };
  const master = execFileSync('python3', ['-c', `import sys,base64; sys.path.insert(0, ${JSON.stringify(repo)}); import test_uebernahme; print(base64.b64encode(test_uebernahme.master_xlsx()).decode())`]).toString().trim();
  const preview = await post('/api/preview-master', { filename: 'master-test.xlsx', content: master });
  await post('/api/import-master', { token: preview.token });
  await post('/api/settings', { issuer: 'JU Test', issuer_address: 'Teststraße 1\n20000 Hamburg', iban: 'DE89370400440532013000', contact_email: 'kasse@example.org' });
  await post('/api/member', { number: 'T1', first_name: 'Erika', last_name: 'Beispiel', street: 'Testweg 1', zip: '20095', city: 'Hamburg', birth: '2000-01-01', joined: '2024-01-01', kv: '5100' });
  await post('/api/logout', {});
}

const PAGES = ['overview', 'members', 'cases', 'map', 'bank', 'dues', 'invoices', 'bills', 'openitems', 'budgets', 'auswertung', 'journal', 'konten', 'settings'];
// Der Durchlauf geht nie ins Netz: Kartenkacheln bekommen ein leeres Bild, jede andere Anfrage nach außen ist ein Fehler
const outside = [];
const guard = (context) => context.route((url) => !['127.0.0.1', 'localhost'].includes(url.hostname) && !['data:', 'blob:', 'about:'].includes(url.protocol), (route) => {
  const url = new URL(route.request().url());
  if (url.hostname !== 'tile.openstreetmap.org') outside.push(url.href);
  return route.abort();
});
let browser;
try {
  await waitForServer();
  await seed();
  browser = await chromium.launch();
  const errors = [];
  const watch = (page, label) => {
    page.on('pageerror', (e) => errors.push(`${label}: ${e.message}`));
    page.on('console', (m) => {
      if (m.type() === 'error' && !/status of (401|429)/.test(m.text())) errors.push(`${label}: ${m.text()}`);
    });
  };

  // ---------- Laptop ----------
  const desk = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await guard(desk);
  const page = await desk.newPage();
  watch(page, 'Laptop');
  await page.goto(url);
  await page.waitForSelector('#login-form');
  check(await page.locator('input[name=password]').count() === 1, 'Anmeldeformular mit Passwortfeld');
  check(!(await page.locator('.login').textContent()).includes('Noch kein Passwort'), 'kein Einrichtungshinweis, wenn ein Passwort gesetzt ist');
  await page.screenshot({ path: join(shots, 's01-anmeldung.png') });
  await page.fill('input[name=name]', 'test schatzmeisterin');
  await page.fill('input[name=password]', 'falsch-falsch-falsch');
  await page.click('#login-form button[type=submit]');
  await page.waitForSelector('.login-error:not([hidden])');
  check((await page.locator('.login-error').textContent()).includes('falsch'), 'falsches Passwort: Meldung im Formular');
  check(await page.inputValue('input[name=password]') === '', 'Passwortfeld nach Fehler geleert');
  await page.fill('input[name=password]', PASSWORT);
  await page.click('#login-form button[type=submit]');
  await page.waitForSelector('.sidebar');
  check((await page.locator('.sidebar-foot').textContent()).includes('Server'), 'Seitenleiste: Serverbetrieb');
  check(await page.locator('#shutdown').count() === 0, 'kein „Programm beenden“ im Serverbetrieb');
  check(await page.locator('.topbar').isHidden(), 'keine Kopfzeile auf dem Laptop');
  for (const id of PAGES) {
    await page.click(`[data-page=${id}]`);
    await page.waitForTimeout(250);
    const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(wide <= 0, `Laptop ${id}: kein seitliches Scrollen (${wide})`);
  }
  await page.click('[data-page=overview]');
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(shots, 's02-uebersicht.png') });
  await page.click('[data-page=bank]');
  await page.waitForTimeout(250);
  await page.screenshot({ path: join(shots, 's03-buchungsplatz.png') });
  await page.click('[data-page=budgets]');
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(shots, 's04-haushalt.png') });

  // Passwort ändern (Einstellungen → Anmeldung); andere Sitzungen enden
  const other = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await guard(other);
  const second = await other.newPage();
  watch(second, 'zweite Sitzung');
  await second.goto(url);
  await second.fill('input[name=name]', 'Test Schatzmeisterin');
  await second.fill('input[name=password]', PASSWORT);
  await second.click('#login-form button[type=submit]');
  await second.waitForSelector('.sidebar');
  await page.click('[data-page=settings]');
  await page.click('[data-tab=zugang]');
  await page.screenshot({ path: join(shots, 's05-passwort.png') });
  await page.fill('input[name=current]', PASSWORT);
  await page.fill('input[name=new]', NEU);
  await page.fill('input[name=repeat]', NEU + 'x');
  await page.click('#password-form button[type=submit]');
  await page.waitForFunction(() => document.getElementById('toast').classList.contains('error'));
  check((await page.locator('#toast').textContent()).includes('stimmen nicht'), 'Passwort ändern: Wiederholung wird geprüft');
  await page.fill('input[name=current]', PASSWORT);
  await page.fill('input[name=new]', NEU);
  await page.fill('input[name=repeat]', NEU);
  await page.click('#password-form button[type=submit]');
  await page.waitForFunction(() => (document.getElementById('toast').textContent || '').includes('Passwort geändert'));
  check(true, 'Passwort geändert');
  // zweite Sitzung ist abgelaufen: nächste Aktion führt zur Anmeldung mit Hinweis
  await second.click('[data-page=journal]');
  await second.click('[data-page=konten]');
  await second.waitForSelector('#login-form', { timeout: 8000 });
  check((await second.locator('.login').textContent()).includes('abgelaufen'), 'abgelaufene Sitzung: zurück zur Anmeldung mit Hinweis');
  await second.fill('input[name=password]', NEU);
  await second.click('#login-form button[type=submit]');
  await second.waitForSelector('.sidebar');
  check(true, 'Anmeldung mit neuem Passwort');
  await other.close();

  // Abmelden; altes Passwort gilt nicht mehr
  await page.click('#logout');
  await page.waitForSelector('#login-form');
  check(await page.inputValue('input[name=name]') === 'Test Schatzmeisterin', 'Benutzername wird vorgeschlagen');
  await page.fill('input[name=password]', PASSWORT);
  await page.click('#login-form button[type=submit]');
  await page.waitForSelector('.login-error:not([hidden])');
  check(true, 'altes Passwort abgelehnt');
  await desk.close();

  // ---------- Tablet quer (1024 px): Menü statt Seitenleiste, Buchungsplatz nebeneinander ----------
  const tab = await browser.newContext({ viewport: { width: 1024, height: 768 } });
  await guard(tab);
  const tp = await tab.newPage();
  watch(tp, 'Tablet');
  await tp.goto(url);
  await tp.fill('input[name=name]', 'Test Schatzmeisterin');
  await tp.fill('input[name=password]', NEU);
  await tp.click('#login-form button[type=submit]');
  await tp.waitForSelector('.topbar');
  await tp.click('#menu-toggle');
  await tp.click('[data-page=bank]');
  await tp.click('[data-filter=all]').catch(() => {});
  await tp.waitForTimeout(400);
  const list = await tp.locator('.worklist .list').boundingBox();
  const detail = await tp.locator('.worklist .detail').boundingBox();
  check(list && detail && detail.x > list.x + list.width - 1, 'Tablet: Liste und Kontierung nebeneinander');
  await tp.screenshot({ path: join(shots, 's06-tablet-buchungsplatz.png') });
  await tab.close();

  // ---------- Handy ----------
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await guard(phone);
  const mob = await phone.newPage();
  watch(mob, 'Handy');
  await mob.goto(url);
  await mob.waitForSelector('#login-form');
  await mob.screenshot({ path: join(shots, 's10-handy-anmeldung.png') });
  await mob.fill('input[name=name]', 'Test Schatzmeisterin');
  await mob.fill('input[name=password]', NEU);
  await mob.click('#login-form button[type=submit]');
  await mob.waitForSelector('.topbar');
  check(await mob.locator('.topbar').isVisible(), 'Handy: Kopfzeile mit Menü');
  check(await mob.evaluate(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0), 'Handy: Seitenleiste eingeklappt');
  await mob.screenshot({ path: join(shots, 's11-handy-uebersicht.png') });
  for (const id of PAGES) {
    await mob.click('#menu-toggle');
    await mob.waitForFunction(() => document.querySelector('.shell').classList.contains('menu-open'));
    await mob.waitForTimeout(260);
    if (id === 'overview') await mob.screenshot({ path: join(shots, 's12-handy-menue.png') });
    await mob.click(`[data-page=${id}]`);
    await mob.waitForTimeout(350);
    check(!(await mob.evaluate(() => document.querySelector('.shell')?.classList.contains('menu-open'))), `Handy ${id}: Menü schließt nach Auswahl`);
    const wide = await mob.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(wide <= 0, `Handy ${id}: kein seitliches Scrollen (${wide})`);
    await mob.screenshot({ path: join(shots, `s2${PAGES.indexOf(id)}-handy-${id}.png`) });
  }
  // Menü schließt auch mit Esc und über den abgedunkelten Bereich
  await mob.click('#menu-toggle');
  await mob.keyboard.press('Escape');
  check(!(await mob.evaluate(() => document.querySelector('.shell').classList.contains('menu-open'))), 'Handy: Esc schließt das Menü');
  await mob.click('#menu-toggle');
  await mob.waitForTimeout(260);
  await mob.mouse.click(370, 400);
  check(!(await mob.evaluate(() => document.querySelector('.shell').classList.contains('menu-open'))), 'Handy: Tippen daneben schließt das Menü');
  // Dialog auf dem Handy: ganze Fläche, Schließen erreichbar
  await mob.click('#menu-toggle');
  await mob.click('[data-page=overview]');
  await mob.waitForTimeout(300);
  await mob.locator('.pagehead .actions .btn').first().click();
  await mob.waitForSelector('#modal[open]');
  const box = await mob.locator('#modal').boundingBox();
  check(box && box.width >= 389 && box.x <= 1, `Handy: Dialog nutzt die volle Breite (${box && Math.round(box.width)})`);
  check(await mob.locator('#modal .dialog-head [data-close]').isVisible(), 'Handy: Schließen-Knopf sichtbar');
  await mob.screenshot({ path: join(shots, 's30-handy-dialog.png') });
  await mob.locator('#modal .dialog-head [data-close]').click();
  check(!(await mob.locator('#modal').evaluate((d) => d.open)), 'Handy: Dialog schließt');
  await phone.close();

  // ---------- Sperre nach Fehlversuchen ----------
  for (let i = 0; i < 5; i++) {
    await fetch(`${url}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: url }, body: JSON.stringify({ name: 'x', password: 'falsch-falsch-falsch' }) });
  }
  const locked = await fetch(`${url}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: url }, body: JSON.stringify({ name: 'Test Schatzmeisterin', password: NEU }) });
  check(locked.status === 429, 'nach 5 Fehlversuchen gesperrt (429)');

  check(outside.length === 0, 'Keine Anfrage an fremde Adressen' + (outside.length ? ': ' + outside.slice(0, 3).join(' | ') : ''));
  check(errors.length === 0, 'Keine Fehler in der Browser-Konsole' + (errors.length ? ': ' + errors.slice(0, 5).join(' | ') : ''));
} catch (e) {
  failures.push(String(e && e.stack || e));
  console.log('FEHLER ' + (e && e.stack || e));
} finally {
  if (browser) await browser.close();
  server.kill();
}
console.log(failures.length ? `\n${failures.length} Prüfung(en) fehlgeschlagen` : '\nAlle Prüfungen bestanden');
process.exit(failures.length ? 1 : 0);
