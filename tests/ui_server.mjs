// Browser-Durchlauf im Serverbetrieb (Playwright) mit erfundenen Daten:
// Anmeldung mit Passwort, Sperre, alle Seiten auf Laptop und Handy (kein seitliches Scrollen), Menü, Dialoge,
// Passwort ändern, abgelaufene Sitzung. Aufruf: NODE_PATH=$(npm root -g) node tests/ui_server.mjs [Ordner für Bildschirmfotos]
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
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
// Prüfbefund 0.50: eine Person nur mit „Sicherheit lesen“ (Rolle Landesgeschäftsführer) löst den Notfall aus
execFileSync('python3', [join(repo, 'verwaltung.py'), '--data', data, 'passwort', '--neu', 'Test Leitung'], { input: `${PASSWORT}\n${PASSWORT}\n` });
execFileSync('python3', [join(repo, 'verwaltung.py'), '--data', data, 'rolle', 'Test Leitung', 'Landesgeschäftsführer']);
// Bericht des Serverschutzes (0.39): erfundene Datei, wie sie der Zeitgeber der Betriebssystem-Härtung schreibt – mit einem Text, der maskiert werden muss
const serverschutz = join(base, 'serverschutz.json');
writeFileSync(serverschutz, JSON.stringify({ zeit: new Date().toISOString(), version: 1,
  pruefungen: [{ id: 'firewall', titel: 'Firewall aktiv', stand: 'ok', detail: 'nftables, Standard: verwerfen' },
    { id: 'ssh', titel: 'SSH nur mit Schlüssel', stand: 'ok', detail: 'Passwort-Anmeldung aus' },
    { id: 'updates', titel: 'Sicherheitsupdates', stand: 'hinweis', detail: '2 Updates offen <b id="xss-serverschutz">fett</b>' }],
  zahlen: { sperrliste_netze: 1837, sperrliste_stand: new Date().toISOString(), gebannt: 3, abgewiesen_24h: 412, updates_offen: 2, neustart_noetig: false } }));
const env = { ...process.env, JU_FINANZEN_GEHEIMNISSE: join(base, 'geheim'), JU_FINANZEN_SERVERSCHUTZ: serverschutz };
const server = spawn('python3', [join(repo, 'tests', 'ui_start.py'), '--port', String(port), '--data', data, '--backups', join(base, 'Sicherungen')], { stdio: 'ignore', env });

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
  await post('/api/bestaetigen', { password: PASSWORT });   // Stammdaten speichern verlangt die Passwortbestätigung (0.35)
  const preview = await post('/api/preview-master', { filename: 'master-test.xlsx', content: master });
  await post('/api/import-master', { token: preview.token });
  await post('/api/settings', { issuer: 'JU Test', issuer_address: 'Teststraße 1\n20000 Hamburg', iban: 'DE89370400440532013000', contact_email: 'kasse@example.org' });
  await post('/api/member', { number: 'T1', first_name: 'Erika', last_name: 'Beispiel', street: 'Testweg 1', zip: '20095', city: 'Hamburg', birth: '2000-01-01', joined: '2024-01-01', kv: '5100' });
  await post('/api/logout', {});
}

const PAGES = ['overview', 'mv-overview', 'members', 'cases', 'map', 'wahl', 'regelwerk', 'bank', 'dues', 'invoices', 'bills', 'openitems', 'budgets', 'auswertung', 'journal', 'konten', 'settings'];
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
      if (m.location()?.url?.endsWith('/api/bestaetigen')) return;   // falsches Passwort in der Bestätigung (400) wird absichtlich geprüft
      if (m.location()?.url?.endsWith('/api/schutz-sperren')) return;   // eigener Rechner lässt sich nicht sperren (400) – absichtlich geprüft
      if (m.type() === 'error' && !/status of (401|428|429|503)/.test(m.text())) errors.push(`${label}: ${m.text()}`);   // 428/503: Bestätigung, Sperre (ab 0.35)
    });
  };
  // Kritische Aktion (ab 0.35): der Server verlangt das Passwort erneut – im Dialog bestätigen
  const confirmPassword = async (p, password) => {
    await p.waitForSelector('#preview[open] #confirm-form', { timeout: 8000 });
    await p.fill('#confirm-form input[name=password]', password);
    await p.click('#confirm-form button[type=submit]');
    await p.waitForSelector('#preview[open]', { state: 'detached' });
  };
  const login = async (p, name, password) => {
    await p.waitForSelector('#login-form');
    await p.fill('input[name=name]', name);
    await p.fill('input[name=password]', password);
    await p.click('#login-form button[type=submit]');
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
  check(await page.locator('#shutdown').count() === 0, 'kein „Programm beenden“ (gab es nur in der Mac-App)');
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

  // ---------- Darstellung „Glas“ (0.48): Wahl beim Anmelden, Laptop hell, Handy dunkel – überall ohne seitliches Scrollen ----------
  const design = (p) => p.evaluate(() => [document.documentElement.dataset.design || 'klassisch', document.documentElement.dataset.farbe || ''].join(' '));
  const glass = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await guard(glass);
  const gp = await glass.newPage();
  watch(gp, 'Glas');
  await gp.goto(url);
  await gp.waitForSelector('#login-form [data-darstellung]');
  check(await design(gp) === 'klassisch ', 'Anmeldung: ohne Wahl das klassische Design');
  await gp.click('.ds-option:has(input[value=glas])');
  await gp.click('.ds-farben label:has(input[value=hell])');
  check(await design(gp) === 'glas hell', 'Anmeldung: Glas hell gewählt, gilt sofort');
  await gp.screenshot({ path: join(shots, 'g10-glas-anmeldung.png') });
  await login(gp, 'Test Schatzmeisterin', NEU);
  await gp.waitForSelector('.sidebar');
  check(await design(gp) === 'glas hell', 'Glas bleibt nach der Anmeldung');
  // Fokus mit der Tastatur bleibt sichtbar (die Glas-Schatten der Knöpfe dürfen ihn nicht verdecken)
  await gp.click('main h1');
  await gp.keyboard.press('Tab');
  const ring = await gp.evaluate(() => [document.activeElement?.className || '', getComputedStyle(document.activeElement).boxShadow]);
  check(ring[0].includes('btn') && ring[1].includes('8, 127, 140'), `Glas: Fokusring auf Knöpfen sichtbar (${ring.join(' · ')})`);
  for (const id of PAGES) {
    await gp.click(`[data-page=${id}]`);
    await gp.waitForTimeout(250);
    const wide = await gp.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(wide <= 0, `Glas Laptop ${id}: kein seitliches Scrollen (${wide})`);
    if (['overview', 'members', 'bank', 'budgets', 'auswertung'].includes(id)) await gp.screenshot({ path: join(shots, `g1${PAGES.indexOf(id)}-glas-${id}.png`) });
  }
  await gp.click('#darstellung');
  await gp.waitForSelector('#modal [data-darstellung]');
  await gp.click('#modal .ds-farben label:has(input[value=dunkel])');
  check(await design(gp) === 'glas dunkel', 'Darstellung: dunkel über die Seitenleiste');
  await gp.screenshot({ path: join(shots, 'g20-glas-dunkel-darstellung.png') });
  await gp.locator('#modal .dialog-head [data-close]').click();
  const light = (rgb) => rgb.match(/\d+/g).slice(0, 3).every((v) => Number(v) > 190);
  check(light(await gp.evaluate(() => getComputedStyle(document.querySelector('main h1')).color)), 'Dunkel: Überschrift hell auf dunklem Grund');
  for (const id of ['overview', 'members', 'bank', 'budgets', 'security']) {
    await gp.click(`[data-page=${id}]`);
    await gp.waitForTimeout(300);
    if (id === 'security') await gp.waitForSelector('.sec-lage');
    await gp.screenshot({ path: join(shots, `g2${['overview', 'members', 'bank', 'budgets', 'security'].indexOf(id) + 1}-glas-dunkel-${id}.png`), fullPage: id === 'security' });
  }
  check(light(await gp.evaluate(() => getComputedStyle(document.querySelector('.sec-lage h2')).color)), 'Dunkel: Lage im Sicherheits-Cockpit hell auf dunklem Grund');
  await gp.reload();
  await gp.waitForSelector('.sidebar');
  check(await design(gp) === 'glas dunkel', 'Darstellung bleibt nach dem Neuladen');
  await gp.click('#darstellung');
  await gp.click('#modal .ds-option:has(input[value=klassisch])');
  check(await design(gp) === 'klassisch ' && await gp.locator('#modal .ds-farben').isHidden(), 'zurück zu Klassisch, Helligkeit dann ausgeblendet');
  await glass.close();

  // Website-Daten blockiert: die Wahl gilt trotzdem für die Seite (wird nur nicht gespeichert)
  const ohneSpeicher = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await guard(ohneSpeicher);
  await ohneSpeicher.addInitScript(() => {
    const nein = () => { throw new DOMException('blockiert', 'SecurityError'); };
    Object.defineProperty(window, 'localStorage', { get: nein });
  });
  const bp = await ohneSpeicher.newPage();
  watch(bp, 'ohne Speicher');
  await bp.goto(url);
  await bp.waitForSelector('#login-form [data-darstellung]');
  await bp.click('.ds-option:has(input[value=glas])');
  await bp.click('.ds-farben label:has(input[value=dunkel])');
  check(await design(bp) === 'glas dunkel', 'ohne Browser-Speicher: Glas dunkel gilt trotzdem für die Seite');
  await ohneSpeicher.close();

  const glassPhone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await guard(glassPhone);
  await glassPhone.addInitScript(() => { try { localStorage.setItem('ju-finanzen-darstellung', JSON.stringify({ design: 'glas', farbe: 'dunkel' })); } catch { /* abgeschotteter Rahmen */ } });
  const gm = await glassPhone.newPage();
  watch(gm, 'Glas Handy');
  await gm.goto(url);
  check(await design(gm) === 'glas dunkel', 'Handy: gespeicherte Wahl gilt schon auf der Anmeldeseite');
  await gm.screenshot({ path: join(shots, 'g30-glas-handy-anmeldung.png') });
  await login(gm, 'Test Schatzmeisterin', NEU);
  await gm.waitForSelector('.topbar');
  check(await gm.evaluate(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0), 'Glas Handy: Seitenleiste eingeklappt');
  for (const id of [...PAGES, 'security']) {
    await gm.click('#menu-toggle');
    await gm.waitForFunction(() => document.querySelector('.shell').classList.contains('menu-open'));
    await gm.waitForTimeout(260);
    if (id === 'overview') await gm.screenshot({ path: join(shots, 'g31-glas-handy-menue.png') });
    await gm.click(`[data-page=${id}]`);
    await gm.waitForTimeout(350);
    check(!(await gm.evaluate(() => document.querySelector('.shell')?.classList.contains('menu-open'))), `Glas Handy ${id}: Menü schließt nach Auswahl`);
    const wide = await gm.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(wide <= 0, `Glas Handy ${id}: kein seitliches Scrollen (${wide})`);
    if (['overview', 'members', 'bank', 'security'].includes(id)) await gm.screenshot({ path: join(shots, `g3${['overview', 'members', 'bank', 'security'].indexOf(id) + 2}-glas-handy-${id}.png`), fullPage: id === 'security' });
  }
  await glassPhone.close();

  // ---------- Rollen und Rechte (ab 0.34) ----------
  const adminContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await guard(adminContext);
  const ap = await adminContext.newPage();
  watch(ap, 'Verwaltung');
  await ap.goto(url);
  await ap.fill('input[name=name]', 'Test Schatzmeisterin');
  await ap.fill('input[name=password]', NEU);
  await ap.click('#login-form button[type=submit]');
  await ap.waitForSelector('.sidebar');
  await ap.click('[data-page=users]');
  await ap.waitForSelector('[data-new-user]');
  check((await ap.locator('#main').textContent()).includes('Test Schatzmeisterin'), 'Benutzer & Rollen: eigene Person in der Liste');
  check(await ap.locator('[data-role]').count() === 5, 'Benutzer & Rollen: fünf Rollen als Anfangsbestand');
  await ap.screenshot({ path: join(shots, 's40-benutzer-rollen.png') });
  await ap.click('[data-role] >> nth=1');
  await ap.waitForSelector('#modal[open] #role-form');
  await ap.screenshot({ path: join(shots, 's41-rolle-bearbeiten.png') });
  await ap.locator('#modal [data-cancel]').click();
  await ap.click('[data-new-user]');
  await ap.waitForSelector('#modal[open] #user-form');
  await ap.fill('#user-form input[name=vorname]', 'Test');
  await ap.fill('#user-form input[name=nachname]', 'Mitgliederbeauftragte');
  await ap.fill('#user-form input[name=name]', 'test.mitglieder');
  await ap.fill('#user-form input[name=email]', 'test.mitglieder@example.org');
  await ap.fill('#user-form input[name=funktion]', 'Landesmitgliederbeauftragte');
  check(await ap.locator('#user-form input[name=totp_pflicht]').isChecked(), 'Person anlegen: Authenticator-App standardmäßig Pflicht');
  await ap.locator('#user-form label.check', { hasText: 'Landesmitgliederbeauftragter' }).locator('input').check();
  await ap.fill('#user-form input[name=password]', PASSWORT);
  await ap.screenshot({ path: join(shots, 's42-person-anlegen.png') });
  await ap.click('#user-form button[type=submit]');
  await confirmPassword(ap, NEU);   // Person anlegen ist eine kritische Aktion (ab 0.35)
  await ap.waitForFunction(() => document.getElementById('toast').textContent.includes('angelegt'));
  const listText = await ap.locator('#main').textContent();
  check(listText.includes('Test Mitgliederbeauftragte') && listText.includes('test.mitglieder') && listText.includes('Landesmitgliederbeauftragte'), 'Benutzer & Rollen: neue Person mit Name, Benutzername und Funktion in der Liste');

  const limited = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await guard(limited);
  const mb = await limited.newPage();
  watch(mb, 'Mitgliederbeauftragte');
  await mb.goto(url);
  await mb.fill('input[name=name]', 'test.mitglieder');
  await mb.fill('input[name=password]', PASSWORT);
  await mb.click('#login-form button[type=submit]');
  await mb.waitForSelector('.sidebar');
  const nav = await mb.locator('.nav [data-page]').evaluateAll((els) => els.map((e) => e.dataset.page));
  check(JSON.stringify(nav) === JSON.stringify(['overview', 'mv-overview', 'members', 'cases', 'map', 'wahl', 'auswertung', 'regelwerk', 'settings']), `Mitgliederbeauftragte: nur ihre Seiten (${nav.join(', ')})`);
  check(await mb.locator('.cards').count() === 0 && await mb.locator('[data-monthly]').count() === 0, 'Mitgliederbeauftragte: Start ohne Finanzkennzahlen');
  check(await mb.locator('.task-card').count() === 1, 'Mitgliederbeauftragte: Start zeigt nur die Mitgliederverwaltung');
  await mb.screenshot({ path: join(shots, 's43-start-mitgliederbeauftragte.png') });
  await mb.click('[data-page=members]');
  await mb.waitForTimeout(400);
  check(await mb.locator('#main table tbody tr').count() > 0, 'Mitgliederbeauftragte: Mitgliederliste sichtbar');
  // 0.43: Akte ohne Beitragskonto, Stand je Jahr ohne Beträge; Überblick der Mitgliederverwaltung
  await mb.locator('#main [data-member]').first().click();
  await mb.waitForSelector('#modal .akte-head');
  check(await mb.locator('#modal [data-tab=konto]').count() === 0 && await mb.locator('#modal [data-tab=ueberblick][aria-selected=true]').count() === 1
    && await mb.locator('#modal .akte-years li').count() > 0, 'Mitgliederbeauftragte: Akte mit Überblick, ohne Beitragskonto');
  await mb.screenshot({ path: join(shots, 's44-akte-mitgliederbeauftragte.png') });
  await mb.locator('#modal .dialog-head [data-close]').click();
  await mb.click('[data-page=mv-overview]');
  await mb.waitForSelector('.mu-page .mu-dates');
  check(!(await mb.locator('.mu-page').textContent()).includes('€'), 'Mitgliederbeauftragte: Überblick ohne Beträge');
  await mb.click('[data-page=settings]');
  await mb.waitForTimeout(300);
  const tabNames = await mb.locator('#main [data-tab]').evaluateAll((els) => els.map((e) => e.dataset.tab));
  check(JSON.stringify(tabNames) === JSON.stringify(['zugang']), `Mitgliederbeauftragte: in den Einstellungen nur die eigene Anmeldung (${tabNames.join(', ')})`);
  for (const path of ['/api/kasse?year=2026', '/api/benutzer', '/api/backup', '/api/journal.csv?year=2026']) {
    const answer = await limited.request.get(url + path);
    check(answer.status() === 403, `Mitgliederbeauftragte: Server lehnt ${path.split('?')[0]} ab (${answer.status()})`);
  }
  // 0.37: Wahlberechtigung und Austritt mit „Mitglieder ändern“ ohne Buchhaltung und ohne Einstellungen
  await mb.click('[data-page=wahl]');
  await mb.waitForSelector('.wr-page .cards');
  check(await mb.locator('.wr-page [data-member]').count() > 0 && await mb.locator('.wr-page [data-settings]').count() === 0,
    'Mitgliederbeauftragte: Wahlberechtigung mit Mitgliedern, ohne „Stichtage ändern“');
  check(!(await mb.locator('.wr-page').textContent()).includes('€'), 'Mitgliederbeauftragte: Wahlberechtigung ohne Beträge');
  for (const path of ['/api/wahlrecht', '/api/wahlrecht.pdf', '/api/wahlrecht.csv']) {
    const answer = await limited.request.get(url + path);
    check(answer.status() === 200, `Mitgliederbeauftragte: Server liefert ${path} (${answer.status()})`);
  }
  await mb.locator('.wr-page [data-member]').first().click();
  await mb.waitForSelector('#modal [data-leave]');
  await mb.click('#modal [data-leave]');
  await mb.waitForSelector('#preview[open] #leave-form');
  check(!(await mb.locator('#leave-form').textContent()).includes('€'), 'Mitgliederbeauftragte: Austritt-Dialog ohne Beträge');
  check(await mb.locator('#leave-form [data-leave-year]').isVisible() && (await mb.locator('#leave-form [data-leave-year]').textContent()).includes('Austrittsjahres'),
    'Austritt-Dialog warnt: Beitrag des Austrittsjahres muss festgelegt werden');
  await mb.screenshot({ path: join(shots, 's44-austritt-mitgliederbeauftragte.png') });
  await mb.click('#leave-form button[type=submit]');
  check(await mb.waitForFunction(() => document.querySelector('#modal [data-status]')?.textContent.includes('ausgetreten'), null, { timeout: 5000 }).then(() => true, () => false),
    'Mitgliederbeauftragte: Austritt eingetragen, Status ausgetreten');
  await mb.click('#modal [data-leave]');
  await mb.waitForSelector('#preview[open] #leave-form [data-undo]');
  await mb.click('#leave-form [data-undo]');
  check(await mb.waitForFunction(() => { const t = document.querySelector('#modal [data-status]')?.textContent || ''; return t && !t.includes('ausgetreten'); }, null, { timeout: 5000 })
    .then(() => true, () => false), 'Mitgliederbeauftragte: Austritt zurückgenommen');
  await mb.keyboard.press('Escape');

  // ---------- Sicherheit (ab 0.35, Cockpit ab 0.50): Übersicht, Protokoll, Notfall mit Sperrbildschirm und Passwortbestätigung ----------
  // eigene, frische Sitzung: die Bestätigung von „Person anlegen“ gilt hier nicht, „Notfall beenden“ muss nachfragen
  const secContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await guard(secContext);
  const sp = await secContext.newPage();
  watch(sp, 'Sicherheit');
  await sp.goto(url);
  await login(sp, 'Test Schatzmeisterin', NEU);
  await sp.waitForSelector('.sidebar');
  check(await mb.locator('[data-page=security]').count() === 0, 'Mitgliederbeauftragte: keine Seite Sicherheit');
  await sp.click('[data-page=security]');
  await sp.waitForSelector('.sec-page .sec-lage');
  const secTabs = await sp.locator('.sec-page [data-tab]').evaluateAll((els) => els.map((e) => e.dataset.tab));
  check(secTabs.join(',') === 'uebersicht,abwehr,zugaenge,protokoll', `Sicherheit: Bereiche Übersicht, Abwehr, Zugänge, Protokoll (${secTabs.join(', ')})`);
  check(await sp.locator('.sec-page .card').count() === 4, 'Sicherheit: vier Kennzahlen in der Übersicht');
  check((await sp.locator('.sec-zustand').textContent()).includes('Normalbetrieb'), 'Sicherheit: Kopf zeigt Normalbetrieb');
  check(await sp.locator('.pagehead [data-emergency-on]').count() === 1 && await sp.locator('.pagehead [data-wartung]').count() === 1, 'Sicherheit: Notfall und Wartung im Kopf');
  check((await sp.locator('.sec-lage h2').textContent()).trim().length > 0 && await sp.locator('.sec-ring').count() === 1, 'Sicherheit: Lage mit Ring und Satz');
  check(await sp.locator('.sec-zuletzt li').count() > 0, 'Sicherheit: „Zuletzt passiert“ zeigt Ereignisse');
  await sp.screenshot({ path: join(shots, 's50-sicherheit-uebersicht.png'), fullPage: true });
  await sp.click('[data-tab=protokoll]');
  await sp.waitForSelector('#sec-filter');
  await sp.waitForSelector('.sec-page table tbody tr');
  const events = await sp.locator('.sec-page table tbody').textContent();
  check(events.includes('Anmeldung'), 'Sicherheit: Ereignisse zeigen eine Anmeldung');
  check(!events.includes('{"'), 'Sicherheit: Einzelheiten ohne rohes JSON');
  await sp.selectOption('#sec-filter select[name=art]', 'anmeldung');
  await sp.waitForTimeout(400);
  const kinds = await sp.locator('.sec-page table tbody tr td:nth-child(2)').allTextContents();
  check(kinds.length > 0 && kinds.every((k) => k === 'Anmeldung'), `Sicherheit: Filter nach Art (${kinds.length} Anmeldungen)`);
  await sp.screenshot({ path: join(shots, 's52-sicherheit-protokoll.png') });
  // ---------- Abwehr (ab 0.39): Musterdaten wie von außen (X-Forwarded-For vom lokalen Proxy), Übersicht, Bereich „Abwehr“ ----------
  const outsideCall = (path, ip, agent = 'Mozilla/5.0 (Musterdaten)') => fetch(url + path, { headers: { 'X-Forwarded-For': ip, 'User-Agent': agent } }).then((r) => r.status);
  // Verlauf der letzten 24 Stunden: erfundene Zahlen direkt in die Stundenstatistik (die Uhr des Servers lässt sich nicht verstellen)
  execFileSync('python3', ['-c', `import sqlite3, sys
from datetime import datetime, timedelta
con = sqlite3.connect(sys.argv[1], timeout=10)
werte = [0, 2, 0, 0, 1, 0, 4, 9, 3, 0, 0, 6, 14, 5, 2, 0, 1, 0, 3, 7, 2, 0, 1]
for h, n in enumerate(werte):
    stunde = (datetime.now() - timedelta(hours=23 - h)).strftime('%Y-%m-%dT%H')
    con.executemany('INSERT OR REPLACE INTO schutz_stunden(stunde,art,n) VALUES(?,?,?)', [(stunde, 'falle', n), (stunde, 'abgewiesen', n * 2), (stunde, 'sperre', n // 5)])
con.commit()`, join(data, 'finanzen.sqlite3')]);
  check(await outsideCall('/wp-login.php', '203.0.113.50') === 404, 'Abwehr: Falle antwortet wie eine unbekannte Adresse (404)');
  check(await outsideCall('/api/status?q=%3Cscript%3Ealert(1)%3C/script%3E', '2001:db8:1::5') === 403, 'Abwehr: Angriffsmuster abgewiesen (403)');
  check(await outsideCall('/', '198.51.100.23', 'sqlmap/1.7') === 403, 'Abwehr: Angriffswerkzeug abgewiesen (403)');
  for (const path of ['/.env', '/.git/config', '/phpmyadmin/', '/backup.zip', '/cgi-bin/luci']) await outsideCall(path, '198.51.100.77');
  check(await outsideCall('/', '198.51.100.77') === 403, 'Abwehr: nach fünf Treffern automatisch gesperrt – auch die Startseite');
  check(await outsideCall('/', '198.51.100.78') === 200, 'Abwehr: andere Adressen bleiben frei');
  // eigene Sitzung: die Passwortbestätigung hier darf weiter unten bei „Notfall beenden“ (Sitzung sp) nicht gelten
  const fwContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await guard(fwContext);
  const fp = await fwContext.newPage();
  watch(fp, 'Abwehr');
  await fp.goto(url);
  await login(fp, 'Test Schatzmeisterin', NEU);
  await fp.waitForSelector('.sidebar');
  await fp.click('[data-page=security]');
  await fp.waitForSelector('.sec-cols');
  check(await fp.locator('.sec-cols li').count() === 24, 'Übersicht: Verlauf mit 24 Stunden');
  check((await fp.locator('.sec-peak').textContent()).trim() === '42', 'Übersicht: Höchstwert am Balken beschriftet');
  check((await fp.locator('.sec-arten').textContent()).includes('Fallen'), 'Übersicht: Abwehr der letzten 24 Stunden mit Zahlen');
  check(await fp.locator('.sec-todo li').count() + await fp.locator('.sec-ok li').count() === 12, 'Übersicht: alle 12 Prüfungen – offen unter „Zu tun“, der Rest unter „In Ordnung“');
  check(await fp.locator('.sec-todo li', { hasText: 'Bedrohungsliste' }).locator('[data-fw-list]').count() === 1, 'Zu tun: Bedrohungsliste mit „Jetzt laden“ direkt daneben');
  check(await fp.locator('.sec-page .card').count() === 4, 'Übersicht: vier Kennzahlen');
  await fp.screenshot({ path: join(shots, 's55-uebersicht-1280.png'), fullPage: true });
  await fp.locator('.sec-arten [data-ziel=abwehr]').click();   // Sprung aus dem Verlauf
  await fp.waitForSelector('[data-fw-edit]');
  check(await fp.locator('[data-tab=abwehr]').getAttribute('aria-selected') === 'true', 'Übersicht: Sprung zum Bereich „Abwehr“');
  const fwText = () => fp.locator('.sec-page').textContent();
  // nach einer Aktion lädt der Reiter neu: auf den Text warten statt sofort zu lesen
  const fwHas = (text, there = true) => fp.waitForFunction(([t, want]) => document.querySelector('.sec-page')?.textContent.includes(t) === want, [text, there], { timeout: 5000 }).then(() => true, () => false);
  check((await fwText()).includes('198.51.100.77/32') && (await fwText()).includes('automatisch'), 'Firewall: automatische Sperre in der Sperrliste');
  check((await fwText()).includes('Falle') && (await fwText()).includes('Angriffswerkzeug'), 'Firewall: Live-Verkehr zeigt Auffälligkeiten');
  check(!(await fwText()).includes('alert(1)'), 'Firewall: Live-Verkehr ohne Abfrageparameter');
  await fp.click('[data-fw-edit]');
  await fp.waitForSelector('#modal[open] #sec-fw-form');
  await fp.fill('#sec-fw-form input[name=schwelle]', '4');
  await fp.click('#sec-fw-form button[type=submit]');
  await confirmPassword(fp, NEU);                                         // Einstellungen ändern verlangt das Passwort
  await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Firewall-Einstellungen gespeichert'));
  check(await fwHas('nach 4 Treffern'), 'Firewall: Einstellungen gespeichert (nach Passwortbestätigung)');
  await fp.click('[data-fw-block]');
  await fp.waitForSelector('#modal[open] #sec-block-form');
  await fp.fill('#sec-block-form input[name=netz]', '127.0.0.1');
  await fp.click('#sec-block-form button[type=submit]');
  await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('eigene Rechner'));
  check(true, 'Firewall: der eigene Rechner lässt sich nicht sperren (Meldung statt Sperre)');
  await fp.fill('#sec-block-form input[name=netz]', '192.0.2.99');
  await fp.fill('#sec-block-form input[name=grund]', 'Musterdaten: Scan am Wochenende');
  await fp.selectOption('#sec-block-form select[name=minuten]', '1440');
  await fp.click('#sec-block-form button[type=submit]');
  await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('192.0.2.99/32 gesperrt'));
  check(await fwHas('Musterdaten: Scan am Wochenende') && await outsideCall('/', '192.0.2.99') === 403, 'Firewall: von Hand gesperrte Adresse steht in der Liste und wird abgewiesen');
  await fp.click('[data-fw-list]');
  await fp.click('#modal[open] [data-ok]');
  await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Bedrohungsliste geladen: 66 Netze'));
  check(await fwHas('66 Netze'), 'Firewall: Bedrohungsliste geladen (erfundene Liste, kein Abruf im Netz)');
  check(await outsideCall('/', '192.0.2.200') === 403, 'Firewall: Adresse aus der Bedrohungsliste wird abgewiesen');
  await fp.click('[data-fw-capture]');
  await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Mitschnitt gestartet'));
  await fp.waitForFunction(() => [...document.querySelectorAll('.sec-page td')].some((td) => td.textContent.includes('/api/schutz')), null, { timeout: 12000 });
  check(true, 'Firewall: Mitschnitt zeigt auch unauffällige Anfragen und frischt sich auf');
  await fp.screenshot({ path: join(shots, 's56-firewall-1280.png'), fullPage: true });
  await fp.click('[data-fw-edit]');
  await fp.waitForSelector('#modal[open] #sec-fw-form');
  await fp.screenshot({ path: join(shots, 's57-firewall-einstellungen.png') });
  await fp.keyboard.press('Escape');
  await fp.click('[data-tab=uebersicht]');
  await fp.waitForSelector('#sec-server');
  await fp.click('#sec-server details:last-of-type summary');
  check((await fp.locator('#sec-server').textContent()).includes('<b id="xss-serverschutz">'), 'Server & Daten: Serverschutz-Bericht als Text (maskiert)');
  check(await fp.locator('#xss-serverschutz').count() === 0, 'Server & Daten: kein Markup aus dem Serverschutz-Bericht');
  await fp.locator('#sec-server').screenshot({ path: join(shots, 's58-server-und-daten-1280.png') });
  // Prüfbefund 0.50: zwei Knöpfe – beide müssen wirken (ohne Prüfliste steht die Dateiprüfung in der Testumgebung immer unter „Zu tun“)
  check(await fp.locator('.sec-todo [data-check]').count() === 1, 'Zu tun: Dateiprüfung mit „Jetzt prüfen“');
  await fp.click('#sec-server [data-check]');
  check(await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Dateiprüfung'), null, { timeout: 8000 }).then(() => true, () => false),
    'Server & Daten: „Dateiprüfung jetzt“ wirkt auch, wenn „Zu tun“ die Dateiprüfung nennt');
  // Wartung aus dem Kopf: ein- und ausschalten (eigene Sitzung fp – die Passwortbestätigung gilt dort schon)
  await fp.click('[data-wartung]');
  await fp.waitForSelector('#modal[open] #sec-maintenance');
  await fp.fill('#sec-maintenance input[name=text]', 'Musterdaten: Update – gleich wieder da');
  await fp.click('#sec-maintenance button[type=submit]');
  await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Wartung eingeschaltet'));
  await fp.waitForSelector('.sec-lage.sec-hinweis');
  check((await fp.locator('.sec-zustand').textContent()).includes('Wartung läuft') && (await fp.locator('.sec-lage').textContent()).includes('gleich wieder da'),
    'Wartung: Kopf und Lage zeigen die Wartung mit Hinweis');
  await fp.click('[data-wartung]');   // laufende Wartung: Enter im Feld speichert nur den Hinweis (Prüfbefund 0.50), beendet nie die Wartung
  await fp.fill('#sec-maintenance input[name=text]', 'Musterdaten: zweiter Hinweis');
  await fp.press('#sec-maintenance input[name=text]', 'Enter');
  check(await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Hinweis gespeichert')
    && (document.querySelector('.sec-lage')?.textContent || '').includes('zweiter Hinweis') && document.querySelector('.sec-zustand')?.textContent.includes('Wartung läuft'),
    null, { timeout: 8000 }).then(() => true, () => false), 'Wartung: Hinweis während der Wartung geändert, Wartung läuft weiter');
  await fp.click('[data-wartung]');
  await fp.click('#sec-maintenance [data-end-wartung]');
  check(await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Wartung ausgeschaltet')
    && document.querySelector('.sec-zustand')?.textContent.includes('Normalbetrieb'), null, { timeout: 8000 }).then(() => true, () => false),
    'Wartung: aus dem Kopf wieder ausgeschaltet');
  // Handy: alle Reiter ohne seitliches Scrollen
  await sp.setViewportSize({ width: 390, height: 844 });
  for (const t of secTabs) {
    await sp.click(`[data-tab=${t}]`);
    await sp.waitForTimeout(400);
    const wide = await sp.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(wide <= 0, `Handy Sicherheit ${t}: kein seitliches Scrollen (${wide})`);
    if (t === 'uebersicht' || t === 'abwehr') await sp.screenshot({ path: join(shots, `s59-handy-390-${t}.png`), fullPage: true });
  }
  await sp.screenshot({ path: join(shots, 's53-handy-sicherheit-protokoll.png') });
  await sp.setViewportSize({ width: 1440, height: 900 });
  await fp.click('[data-tab=abwehr]');
  await fp.waitForSelector('[data-fw-capture]');
  await fp.click('[data-fw-capture]');
  await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Mitschnitt beendet'));
  await fp.click('[data-fw-free][data-netz="192.0.2.99/32"]');
  await fp.click('#modal[open] [data-ok]');
  await fp.waitForFunction(() => document.getElementById('toast').textContent.includes('Adresse freigegeben'));
  check(await fwHas('192.0.2.99/32', false), 'Firewall: Sperre von Hand freigegeben');
  await fwContext.close();
  // Notfall auslösen – aus dem Kopf, hier aus dem Protokoll
  await sp.click('[data-emergency-on]');
  await sp.waitForSelector('#modal[open] #sec-emergency');
  check(await sp.locator('#sec-emergency button[type=submit]').isDisabled(), 'Notfall: Auslösen erst nach „Ich habe die Wirkungen gelesen“');
  await sp.fill('#sec-emergency textarea[name=grund]', 'Übung mit erfundenem Verdacht');
  await sp.check('#sec-emergency input[name=gelesen]');
  await sp.click('#sec-emergency button[type=submit]');
  await sp.waitForFunction(() => document.getElementById('toast').textContent.includes('Notfallmodus ausgelöst'));
  await sp.waitForSelector('[data-emergency-off]');
  await sp.click('[data-tab=uebersicht]');
  await sp.waitForSelector('.sec-lage.sec-alert');
  check((await sp.locator('.sec-lage').textContent()).includes('Übung mit erfundenem Verdacht') && (await sp.locator('.sec-zustand').textContent()).includes('Notfallmodus aktiv'),
    'Notfall: Kopf und Lage zeigen den Notfall mit Grund');
  await sp.screenshot({ path: join(shots, 's60-notfall-uebersicht.png') });
  // eingeschränkte Person: abgemeldet, nach erneuter Anmeldung nur der Sperrbildschirm
  await mb.reload();
  await mb.waitForSelector('#login-form, #lock-retry');
  if (await mb.locator('#login-form').count()) await login(mb, 'test.mitglieder', PASSWORT);
  await mb.waitForSelector('#lock-retry');
  const lockText = await mb.locator('.login-card').textContent();
  check(lockText.includes('Notfallmodus aktiv'), 'Notfall: eingeschränkte Person sieht den Sperrbildschirm');
  check(!lockText.includes('Übung'), 'Notfall: Grund nur für Personen mit Sicherheitsrecht');
  check(await mb.locator('.sidebar').count() === 0, 'Notfall: keine Oberfläche hinter dem Sperrbildschirm');
  await mb.screenshot({ path: join(shots, 's51-sperrbildschirm.png') });
  const blocked = await limited.request.get(url + '/api/data?year=2026');
  check(blocked.status() === 503, `Notfall: Server sperrt Anfragen der eingeschränkten Person (${blocked.status()})`);
  await mb.click('#lock-retry');
  await mb.waitForSelector('#lock-retry');
  await mb.click('#lock-logout');
  await mb.waitForSelector('#login-form');
  check(true, 'Sperrbildschirm: Abmelden führt zur Anmeldung');
  // Notfall beenden: nur mit Passwortbestätigung; Abbrechen ändert nichts
  await sp.click('[data-emergency-off]');
  await sp.waitForSelector('#preview[open] #confirm-form');
  await sp.screenshot({ path: join(shots, 's54-passwort-bestaetigen.png') });
  await sp.click('#confirm-form [data-close]');
  await sp.waitForFunction(() => document.getElementById('toast').textContent.includes('Abgebrochen'));
  check(await sp.locator('[data-emergency-off]').count() === 1, 'Passwortbestätigung abgebrochen: Notfall bleibt aktiv');
  await sp.click('[data-emergency-off]');
  await sp.waitForSelector('#preview[open] #confirm-form');
  await sp.fill('#confirm-form input[name=password]', 'falsch-falsch-falsch');
  await sp.click('#confirm-form button[type=submit]');
  await sp.waitForSelector('#confirm-form .login-error:not([hidden])');
  check(await sp.inputValue('#confirm-form input[name=password]') === '', 'Passwortbestätigung: falsches Passwort, Feld geleert');
  await sp.fill('#confirm-form input[name=password]', NEU);
  await sp.click('#confirm-form button[type=submit]');
  await sp.waitForFunction(() => document.getElementById('toast').textContent.includes('Notfallmodus beendet'));
  await sp.waitForSelector('[data-emergency-on]');
  check(true, 'Notfall beendet nach Passwortbestätigung');
  await login(mb, 'test.mitglieder', PASSWORT);
  await mb.waitForSelector('.sidebar');
  check(true, 'Nach dem Notfall: eingeschränkte Person kommt nach neuer Anmeldung wieder hinein');
  // Leserecht „Sicherheit“: Notfall auslösen – danach Erfolgsmeldung und Lage, nicht der Sperrbildschirm mit Fehlermeldung
  const leserContext = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await guard(leserContext);
  const lp = await leserContext.newPage();
  watch(lp, 'Leitung');
  await lp.goto(url);
  await login(lp, 'Test Leitung', PASSWORT);
  await lp.waitForSelector('.sidebar');
  await lp.click('[data-page=security]');
  await lp.waitForSelector('.pagehead [data-emergency-on]');
  check(await lp.locator('.pagehead [data-wartung]').count() === 0, 'Leitung: Notfall im Kopf, keine Wartung');
  await lp.click('.pagehead [data-emergency-on]');
  await lp.waitForSelector('#modal[open] #sec-emergency');
  await lp.fill('#sec-emergency textarea[name=grund]', 'Übung der Leitung mit erfundenem Verdacht');
  await lp.check('#sec-emergency input[name=gelesen]');
  await lp.click('#sec-emergency button[type=submit]');
  check(await lp.waitForFunction(() => document.getElementById('toast').textContent.includes('Notfallmodus ausgelöst') && !document.getElementById('toast').classList.contains('error'),
    null, { timeout: 8000 }).then(() => true, () => false), 'Leitung: Notfall ausgelöst mit Erfolgsmeldung');
  // Prüfbefund 0.51: erst warten, bis die Lage den Notfall zeigt (sec-alert gibt es in der Testumgebung schon vorher), dann einen Moment,
  // in dem der alte Fehler (weitere Anfrage → 503 → Sperrbildschirm mit Fehlermeldung) zuschlagen würde
  const lageNotfall = await lp.waitForFunction(() => (document.querySelector('.sec-lage h2')?.textContent || '').includes('Notfallmodus aktiv'), null, { timeout: 8000 })
    .then(() => true, () => false);
  await lp.waitForTimeout(1500);
  check(lageNotfall && await lp.locator('#lock-retry').count() === 0 && !(await lp.getAttribute('#toast', 'class') || '').includes('error')
    && await lp.locator('.sec-page [data-tab]').count() === 1 && await lp.locator('.sec-page [data-ziel="abwehr"], .sec-page [data-ziel="protokoll"]').count() === 0,
    'Leitung: danach die Lage mit dem Notfall – kein Sperrbildschirm, keine Fehlermeldung, nur die Übersicht ohne Sprünge zu anderen Reitern');
  await lp.screenshot({ path: join(shots, 's61-notfall-leitung.png') });
  await leserContext.close();
  await sp.reload();
  await sp.waitForSelector('.sidebar');
  await sp.click('[data-page=security]');
  await sp.waitForSelector('.sec-page .sec-lage');
  await sp.waitForSelector('[data-emergency-off]', { timeout: 15000 });
  await sp.click('[data-emergency-off]');
  const beendet = () => document.getElementById('toast').textContent.includes('Notfallmodus beendet');
  await Promise.race([sp.waitForSelector('#preview[open] #confirm-form'), sp.waitForFunction(beendet)]);
  if (await sp.locator('#preview[open] #confirm-form').count()) await confirmPassword(sp, NEU);   // Bestätigung gilt 5 Minuten
  await sp.waitForFunction(beendet);
  await secContext.close();
  await limited.close();
  await adminContext.close();

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
