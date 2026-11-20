// Browser-Durchlauf der Oberfläche mit erfundenen Daten (Playwright): die fachlichen Abläufe aller Seiten.
// Aufruf: node tests/ui_smoke.mjs [Ordner für Bildschirmfotos]
// Startet einen eigenen Server mit leerem Datenordner; berührt keine echten Daten. Anmeldung, Sperre, Handy und
// Passwortbestätigung im Dialog prüft tests/ui_server.mjs.
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const { chromium } = createRequire(import.meta.url)('playwright'); // global installiert, NODE_PATH wird beachtet
const repo = join(dirname(fileURLToPath(import.meta.url)), '..');
const shots = process.argv[2] || join(tmpdir(), 'ju-ui-shots');
mkdirSync(shots, { recursive: true });
const base = mkdtempSync(join(tmpdir(), 'ju-ui-'));
const port = 8800 + Math.floor(Math.random() * 100);
const masterPath = join(base, 'master-test.xlsx');
execFileSync('python3', ['-c', `import sys; sys.path.insert(0, ${JSON.stringify(repo)}); import test_uebernahme; open(${JSON.stringify(masterPath)}, 'wb').write(test_uebernahme.master_xlsx())`]);
// Gliederung (Format der Mitgliederverwaltung) und kennwortgeschützte Mitgliederliste – erfundene Daten aus test_gliederung.py
const structurePath = join(base, 'gliederung.xlsx'), lockedPath = join(base, 'geschuetzt.xlsx');
execFileSync('python3', ['-c', `import sys; sys.path.insert(0, ${JSON.stringify(repo)}); import test_gliederung as t
open(${JSON.stringify(structurePath)}, 'wb').write(t.xlsx([[701, 'Hamburg-Altona', 16, 'Testmarsch', 'JU'], [707, 'Hamburg-Wandsbek', 71, 'Testtal', 'JU'], [701, 'Hamburg-Altona', 12, 'Testdorf', 'JU']], t.GliederungImportTests.HEAD2))
if t.OOXMLFile: open(${JSON.stringify(lockedPath)}, 'wb').write(t.encrypt(t.xlsx([t.person(707, 'Hamburg-Wandsbek', 71, 'Testtal', 'S-1', 'Sina', 'Schutz')])))`]);
const lockedPassword = execFileSync('python3', ['-c', `import sys; sys.path.insert(0, ${JSON.stringify(repo)}); import test_gliederung as t; print(t.PASSWORD)`]).toString().trim();
const kvMasterPath = join(base, 'kv-master.xlsx');
execFileSync('python3', ['-c', `import sys; sys.path.insert(0, ${JSON.stringify(repo)}); import test_kvabgleich; open(${JSON.stringify(kvMasterPath)}, 'wb').write(test_kvabgleich.workbook())`]);

const NAME = 'Test Schatzmeisterin', PASSWORT = 'Erfundenes-Passwort-2026';
// Passwort wie auf dem Server über verwaltung.py setzen (Passwort zweimal über stdin); die erste Person bekommt alle Rechte
execFileSync('python3', [join(repo, 'verwaltung.py'), '--data', join(base, 'Daten'), 'passwort', NAME], { input: `${PASSWORT}\n${PASSWORT}\n` });

const mailDir = join(base, 'Mails');
const smtp = spawn('python3', [join(repo, 'tests', 'fake_smtp.py'), mailDir], { stdio: 'ignore' });
const env = { ...process.env, JU_FINANZEN_OHNE_SCHLUESSELBUND: '1' }; // nie den echten Schlüsselbund berühren
// Start über tests/ui_start.py: erfundene Adressdaten statt des Abrufs bei der Stadt – der Durchlauf geht nie ins Netz
const server = spawn('python3', [join(repo, 'tests', 'ui_start.py'), '--port', String(port), '--data', join(base, 'Daten'), '--backups', join(base, 'Sicherungen')], { stdio: 'ignore', env });
let smtpPort = 0;
const mails = () => readdirSync(mailDir).filter((f) => f.endsWith('.eml')).sort((a, b) => parseInt(a) - parseInt(b)).map((f) => readFileSync(join(mailDir, f), 'latin1'));
const failures = [];
const check = (ok, message) => { if (!ok) failures.push(message); console.log((ok ? 'ok   ' : 'FEHLER ') + message); };

async function waitForServer() {
  for (let i = 0; i < 50; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/api/status`); if (r.ok) return; } catch { /* startet noch */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error('Server startet nicht');
}

let browser, confirmTimer;
try {
  await waitForServer();
  for (let i = 0; i < 50 && !existsSync(join(mailDir, 'port')); i++) await new Promise((r) => setTimeout(r, 100));
  smtpPort = Number(readFileSync(join(mailDir, 'port'), 'utf8'));
  const origin = `http://127.0.0.1:${port}`;
  browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: `http://127.0.0.1:${port}` });
  // Kartenkacheln (OpenStreetMap) nie wirklich laden: leeres Bild; jede andere Anfrage nach außen ist ein Fehler
  const outside = [], tiles = [];
  const blank = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGN49/ErAAWlAtW8cl6DAAAAAElFTkSuQmCC', 'base64');
  await context.route((url) => !['127.0.0.1', 'localhost'].includes(url.hostname) && !['data:', 'blob:', 'about:'].includes(url.protocol), (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === 'tile.openstreetmap.org') { tiles.push(url.pathname); return route.fulfill({ status: 200, contentType: 'image/png', body: blank }); }
    outside.push(url.href);
    return route.abort();
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  let rejected = 0; // absichtlich provozierte Ablehnungen (400) werden gezählt statt als Fehler gewertet
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    if (m.text().includes('status of 400')) rejected++;
    else errors.push(m.text());
  });
  // Haushaltsposten: Knopf öffnet die Liste, Nummer eintippen, Enter übernimmt
  const pick = async (button, code) => {
    await (typeof button === 'string' ? page.locator(button).first() : button).click();
    await page.fill('#account-query', code);
    await page.keyboard.press('Enter');
    await page.waitForSelector('#account-query', { state: 'detached', timeout: 3000 }).catch(() => {});
  };
  // Meldung der nächsten Aktion: alte Meldung leeren, dann auf die neue warten.
  const clearToast = () => page.evaluate(() => { const t = document.getElementById('toast'); t.className = ''; t.textContent = ''; });
  const toast = async () => {
    await page.waitForFunction(() => document.getElementById('toast').classList.contains('show'), null, { timeout: 8000 });
    const text = await page.locator('#toast').textContent();
    await clearToast();
    return text || '';
  };
  const items = (n) => page.waitForFunction((count) => document.querySelectorAll('.bank-item').length === count, n, { timeout: 8000 }).then(() => true, () => false);
  const shot = (name) => page.screenshot({ path: join(shots, name + '.png'), fullPage: false });
  const dialogClose = () => page.locator('#modal .dialog-head [data-close]').click();

  // Kritische Aktionen verlangen die Passwortbestätigung (app.CONFIRM, 5 Minuten): jede Minute im Hintergrund über die Cookies
  // des Browsers bestätigen – den Dialog dafür prüft tests/ui_server.mjs
  const confirm = async () => {
    const { csrf } = await (await context.request.get(`${origin}/api/status`)).json();
    if (csrf) await context.request.post(`${origin}/api/bestaetigen`, { headers: { 'X-CSRF-Token': csrf, Origin: origin }, data: { password: PASSWORT } });
  };
  confirmTimer = setInterval(() => confirm().catch(() => {}), 60_000);
  const login = async () => {
    await page.goto(origin);
    await page.waitForSelector('#login-form');
    await page.fill('input[name=name]', NAME);
    await page.fill('input[name=password]', PASSWORT);
    await page.click('#login-form button[type=submit]');
    await page.waitForSelector('.sidebar');
    await confirm();
  };
  await login();
  check(await page.locator('h1').textContent() === 'Start' && await page.locator('main h2#tasks-title').textContent() === 'Aufgaben', 'Einrichtung und Start (Aufgaben je Bereich)');
  check(await page.evaluate(() => { const k = document.querySelector('main .cards'), t = document.querySelector('main .task-grid'); return k && t && k.getBoundingClientRect().bottom <= t.getBoundingClientRect().top; }),
    'Start: Kennzahlen stehen über den Aufgaben');
  check(await page.locator('.task-card').count() === 4 && (await page.locator('.nav-group').allTextContents()).join('|') === 'Mitgliederverwaltung|Buchhaltung|Verband|Verwaltung',
    'Navigation nach Bereichen');
  const version = readFileSync(join(repo, 'app.py'), 'utf8').match(/^VERSION='([^']+)'/m)[1];
  check((await page.locator('.sidebar-foot').textContent()).includes(`Version ${version}`), 'Version in der Seitenleiste');

  // Stammdaten: Verein & Bank
  await page.click('[data-page=settings]');
  check((await page.locator('h1').textContent()) === 'Einstellungen', 'Einstellungen (Verwaltung)');
  check(await page.locator('.switch input[name=kv_share_auto]').count() === 1 && !(await page.locator('.switch input[name=kv_share_auto]').isChecked()), 'Schalter „Beitragsanteil automatisch“ ist zuerst aus');
  // 0.31: Schalter ein- und wieder ausschalten (gilt ab dem gewählten Haushaltsjahr); Stand steht auf der Startseite
  await page.check('.switch input[name=kv_share_auto]');
  await page.click('form[data-save] button[type=submit]');
  check((await toast()).includes('gespeichert'), 'Schalter „Beitragsanteil automatisch“ eingeschaltet');
  await page.click('[data-page=overview]');
  const autoRow = page.locator('.task-row:has-text("Beitragsanteil der Kreisverbände automatisch")');
  check((await autoRow.textContent()).includes(`ab Haushaltsjahr ${new Date().getFullYear() + 1}`) && (await autoRow.locator('.task-count').textContent()) === 'an', 'Schalter-Stand auf der Startseite');
  await autoRow.click();
  check(await page.locator('.switch input[name=kv_share_auto]').isChecked(), 'Sprung vom Start zum Schalter');
  await shot('02b-schalter');
  await page.uncheck('.switch input[name=kv_share_auto]');
  await page.click('form[data-save] button[type=submit]');
  check((await toast()).includes('gespeichert'), 'Schalter wieder ausgeschaltet');
  await page.click('[data-tab=verein]');
  await page.fill('input[name=issuer]', 'JU Test');
  await page.fill('textarea[name=issuer_address]', 'Teststraße 1\n20000 Hamburg');
  await page.fill('input[name=iban]', 'DE00 1234');
  await page.click('form[data-save] button[type=submit]');
  check((await toast()).includes('IBAN'), 'Ungültige IBAN wird abgelehnt');
  await page.fill('input[name=iban]', 'de89370400440532013000');
  await page.fill('input[name=bic]', 'COBADEFFXXX');
  await page.fill('input[name=creditor_id]', 'DE98ZZZ09999999999');            // Beispiel-Gläubiger-ID der Bundesbank
  await page.fill('input[name=contact_email]', 'kasse@example.org');
  await page.fill('textarea[name=tax_note]', 'Fiktiver Steuerhinweis');
  await page.check('input[name=invoice_ready]');
  await page.click('form[data-save] button[type=submit]');
  check((await toast()).includes('gespeichert'), 'Stammdaten speichern');
  check(await page.inputValue('input[name=liability_account]') === '1600', 'Verbindlichkeitenkonto vorbelegt (1600)');
  await page.fill('input[name=liability_name]', 'Probe');
  await page.fill('input[name=liability_account]', '1600');
  await page.dispatchEvent('input[name=liability_account]', 'change');
  check(await page.inputValue('input[name=liability_name]') === 'Verbindlichkeiten (Eingangsrechnungen)', 'vorhandenes Konto behält seine Bezeichnung');
  check(await page.inputValue('input[name=iban]') === 'DE89 3704 0044 0532 0130 00', 'IBAN wird formatiert');
  // Beiträge – ungespeicherte Eingaben (Bezeichnung oben): Rückfrage vor dem Reiterwechsel
  await page.click('[data-tab=beitraege]');
  await page.waitForSelector('#modal[open] [data-drop]');
  check((await page.locator('#modal').textContent()).includes('ungespeicherte'), 'Reiterwechsel: Rückfrage bei ungespeicherten Eingaben');
  await page.click('#modal [data-drop]');
  await page.selectOption('select[name=age_basis]', 'jan1');
  await page.click('form[data-save] button[type=submit]');
  await toast();
  check(await page.inputValue('input[name=dues_old]') === '30,00' && await page.inputValue('input[name=dues_young]') === '10,00', 'Beitragsstaffel bleibt beim Speichern erhalten');
  // E-Mail-Versand (lokaler Testserver statt Strato)
  await page.click('[data-tab=versand]');
  await page.fill('input[name=mail_from]', 'kasse@example.org');
  await page.fill('input[name=smtp_host]', '127.0.0.1');
  await page.fill('input[name=smtp_port]', String(smtpPort));
  await page.fill('input[name=smtp_user]', 'test@example.org');
  await page.fill('input[name=mail_pause]', '0');
  await page.click('form[data-save] button[type=submit]');
  await toast();
  await page.selectOption('select[name=smtp_security]', 'none');
  await page.click('form[data-save] button[type=submit]');
  check((await toast()).includes('gespeichert'), 'E-Mail-Konto speichern');
  check(await page.locator('#pw-form input[name=remember]').isDisabled(), 'Ohne Schlüsselbund: Passwort nur für die Sitzung');
  await page.fill('#pw-form input[name=password]', 'falsch');
  await page.click('#pw-form button[type=submit]');
  await toast();
  await page.click('#mail-check');
  check((await toast()).includes('Anmeldung'), 'Falsches Passwort wird erkannt');
  await page.fill('#pw-form input[name=password]', 'geheim');
  await page.click('#pw-form button[type=submit]');
  check((await toast()).includes('Beenden'), 'Passwort gespeichert');
  await page.click('#mail-check');
  check((await toast()).includes('erfolgreich'), 'Verbindung prüfen');
  await page.click('#mail-test');
  check((await toast()).includes('Testmail an kasse@example.org'), 'Testmail an mich');
  check(mails().length === 1 && mails()[0].includes('[TEST]'), 'Testmail kommt an');
  await shot('12-stammdaten-versand');
  // Vorlagen mit Vorschau
  await page.click('[data-tab=vorlagen]');
  await page.fill('input[name=tpl_dues_subject]', 'Beitrag {jahr} – {nummer}');
  await page.click('[data-preview-mail=dues]');
  const frame = page.frameLocator('#preview iframe.mail-frame');
  check(await frame.locator('text=Beitrag').first().waitFor({ timeout: 8000 }).then(() => true, () => false), 'E-Mail-Vorschau im Programm');
  check((await frame.locator('body').textContent()).includes(`Beitrag ${new Date().getFullYear() + 1} – B-0000-00000`), 'Vorschau nutzt geänderte Vorlage');
  await shot('14-vorlagen-vorschau');
  await page.locator('#preview [data-close]').first().click();
  await page.fill('textarea[name=tpl_dues_body]', 'Hallo {vornahme}');
  await page.click('form[data-save] button[type=submit]');
  check((await toast()).includes('Unbekannte Platzhalter'), 'Unbekannter Platzhalter wird abgelehnt');
  await page.click('[data-tab=verein]');
  await page.click('#modal [data-drop]');                                    // abgelehnte Eingabe verwerfen

  // Stammdaten → Gliederung: Nummer der Mitgliederverwaltung, Ortsverbände anlegen und importieren (erfundene Namen)
  await page.click('[data-tab=gliederung]');
  check(await page.inputValue('#kv-numbers input[name="5100"]') === '701', 'Gliederung: Nummer der Mitgliederverwaltung vorbelegt');
  for (const [kv, nr, name] of [['5100', '12', 'Testdorf'], ['5100', '0', 'Kreisverband'], ['5300', '31', 'Testviertel']]) {
    await page.click('#ov-new');
    await page.selectOption('#ov-form select[name=kv]', kv);
    await page.fill('#ov-form input[name=number]', nr);
    await page.fill('#ov-form input[name=name]', name);
    await page.click('#ov-form button[type=submit]');
    check((await toast()).includes('angelegt'), `Ortsverband ${name} anlegen`);
  }
  await page.click('#ov-new');
  await page.selectOption('#ov-form select[name=kv]', '5100');
  await page.fill('#ov-form input[name=number]', '12');
  await page.fill('#ov-form input[name=name]', 'Doppelt');
  await page.click('#ov-form button[type=submit]');
  check((await toast()).includes('schon den Ortsverband 12'), 'Doppelte OV-Nummer im Kreisverband abgelehnt');
  await page.keyboard.press('Escape');
  await page.click('#ov-import');
  await page.setInputFiles('#ov-file', structurePath);
  await page.waitForSelector('#ov-confirm');
  check((await page.locator('#ov-preview').textContent()).includes('2 Ortsverbände neu · 1 schon vorhanden'), 'Gliederung-Import: Vorschau neu/vorhanden');
  await page.click('#ov-confirm');
  check((await toast()).includes('2 Ortsverbände angelegt'), 'Gliederung importiert');
  check((await page.locator('main table').last().textContent()).includes('Testtal'), 'Ortsverband aus dem Import in der Liste');
  await page.selectOption('#ov-kv', '5300');
  check((await page.locator('main table').last().locator('tbody tr').count()) === 1, 'Ortsverbände nach Kreisverband filtern');
  await shot('12b-gliederung');

  // Mitglied anlegen und bearbeiten
  await page.click('[data-page=members]');
  await page.click('[data-new]');
  await page.keyboard.press('Escape');                                       // Esc schließt den Dialog (auch im Programmfenster)
  check(!(await page.evaluate(() => document.getElementById('modal').open)), 'Esc schließt den Dialog');
  await page.click('[data-new]');
  await page.fill('input[name=number]', 'T-1');
  await page.fill('input[name=first_name]', 'Erika');
  await page.fill('input[name=last_name]', 'Musterfrau');
  await page.fill('input[name=birth]', '2000-02-01');
  await page.fill('input[name=joined]', '2024-01-01');
  await page.fill('input[name=street]', 'Musterweg 1');
  await page.fill('input[name=zip]', '20000');
  await page.fill('input[name=city]', 'Hamburg');
  // Beitrag automatisch nach Alter: Betrag laut Staffel erscheint, fester Betrag gesperrt (geboren 2000 → ab 22 Jahren 30 €)
  await page.waitForFunction(() => document.querySelector('#member-form [data-dues-auto]')?.textContent.includes('30,00'), null, { timeout: 8000 })
    .then(() => check(true, 'Beitrag automatisch nach Alter angezeigt'), () => check(false, 'Beitrag automatisch nach Alter angezeigt'));
  check((await page.getAttribute('#member-form input[name=dues]', 'class')).includes('is-off'), 'Fester Betrag ohne Auswahl ausgegraut');
  await page.locator('#member-form .member-dues').scrollIntoViewIfNeeded();
  await shot('10a-beitrag-nach-alter');
  const ovOptions = () => page.locator('#member-form select[name=ov_id] option').allTextContents();
  await page.selectOption('#member-form select[name=kv]', '5300');
  check((await ovOptions()).join('|') === '– kein Ortsverband –|31 Testviertel', 'Ortsverband: nur die des gewählten Kreisverbands');
  await page.selectOption('#member-form select[name=ov_id]', { label: '31 Testviertel' });
  await page.selectOption('#member-form select[name=kv]', '5100');
  const ov5100 = await ovOptions();
  check(ov5100.includes('12 Testdorf') && ov5100.includes('Kreisverband') && !ov5100.includes('31 Testviertel')
    && await page.inputValue('#member-form select[name=ov_id]') === '', 'Kreisverband gewechselt: Ortsverbände neu, alte Auswahl entfällt');
  await page.selectOption('#member-form select[name=ov_id]', { label: '12 Testdorf' });
  await page.fill('#member-form input[name=phone_work]', '040 000');
  await page.check('#member-form input[name=no_mail]');
  await page.click('#member-form button[type=submit]');
  check((await toast()).includes('angelegt'), 'Mitglied anlegen');
  check((await page.locator('tbody tr').first().textContent()).includes('12 Testdorf'), 'Ortsverband in der Liste');
  check((await page.locator('tbody tr').first().textContent()).includes('Musterfrau, Erika'), 'Liste: Nachname, Vorname');
  await page.click('[data-member]');
  check((await page.locator('#modal h2').textContent()).includes('Erika'), 'Mitglied öffnen');
  await page.click('#modal [data-edit]');
  await page.fill('input[name=email]', 'erika@example.org');
  await page.click('#member-form input[name=dues]');                         // Klick ins Feld wählt „fester Betrag“
  check(await page.isChecked('#member-form input[name=dues_mode][value=fixed]'), 'Klick in den Betrag wählt „fester Betrag“');
  await page.fill('input[name=dues]', '30');
  await page.click('#member-form button[type=submit]');
  check((await toast()).includes('gespeichert'), 'Mitglied bearbeiten');
  check((await page.locator('tbody tr').first().textContent()).includes('erika@example.org'), 'E-Mail in der Liste');
  await shot('10-mitglieder');

  // Beitragslauf mit Vorschau, Festschreiben und Versand
  await page.click('[data-page=invoices]');
  await page.click('[data-dues]');
  await page.selectOption('#dues-year', '2026');
  await page.waitForSelector('#modal [data-mail]');
  check((await page.locator('#modal tbody').textContent()).includes('individueller Jahresbeitrag'), 'Beitragslauf zeigt Berechnungsgrundlage');
  await page.click('#modal [data-mail]');
  check(await page.frameLocator('#preview iframe.mail-frame').locator('text=erika@example.org').first().waitFor({ timeout: 8000 }).then(() => true, () => false), 'E-Mail-Vorschau vor dem Festschreiben');
  await page.locator('#preview [data-close]').first().click();
  await page.click('#issue-dues');
  await page.waitForFunction(() => (document.querySelector('#modal h2')?.textContent || '').includes('erstellt'), null, { timeout: 8000 });
  check((await page.locator('#modal h2').textContent()).includes('1 Beitragsrechnung erstellt'), 'Beitragslauf');
  const dueNumber = (await page.locator('main tbody tr strong').first().textContent()).trim();
  await page.click('#modal [data-send]');
  await page.waitForSelector('#modal [data-go]');
  await shot('15-versand-auswahl');
  await page.click('#modal [data-go]');
  await page.click('#modal [data-start]');
  await page.waitForFunction(() => (document.querySelector('#modal h2')?.textContent || '').includes('abgeschlossen'), null, { timeout: 15000 });
  check((await page.locator('#modal').textContent()).includes('1 versendet'), 'Beitragsrechnung per E-Mail versendet');
  await shot('16-versand-fertig');
  await page.click('#modal [data-done]');
  check(mails().length === 2 && mails()[1].includes('erika@example.org') && mails()[1].includes(dueNumber + '.pdf'), 'E-Mail mit PDF beim Empfänger');

  // Rechnung für mehrere Empfänger (Mitglied + Gast) mit Vorschau
  await page.click('[data-normal]');
  await page.fill('[data-line="0"][data-key=description]', 'Teilnahme 01.11.2026');
  await page.fill('[data-line="0"][data-key=amount]', '100');
  await page.fill('input[name=due]', '2026-12-01');
  await page.check('#modal [data-member]');
  await page.click('[data-rtab=others]');
  await page.fill('[data-other="0"][data-key=name]', 'Test GmbH');
  await page.fill('[data-other="0"][data-key=address]', 'Adresse 1\n20000 Hamburg');
  check((await page.locator('#modal .bulkbar strong').textContent()).includes('2 Empfänger'), 'Empfänger werden gezählt');
  await page.click('[data-rtab=members]');
  await shot('18-rechnung-formular');
  await page.click('#series-form button[type=submit]');
  await page.waitForSelector('#modal [data-issue]');
  check(await page.locator('#modal tbody tr').count() === 2, 'Vorschau zeigt beide Rechnungen');
  await shot('17-rechnung-vorschau');
  await page.click('#modal [data-mail="1"]');
  check(await page.frameLocator('#preview iframe.mail-frame').locator('text=keine E-Mail-Adresse').first().waitFor({ timeout: 8000 }).then(() => true, () => false), 'Vorschau zeigt fehlende E-Mail');
  await page.locator('#preview [data-close]').first().click();
  const [draftPdf] = await Promise.all([page.waitForEvent('popup'), page.locator('#modal a:has-text("PDF")').first().click()]);
  await draftPdf.close();
  await page.click('#modal [data-issue]');
  await page.waitForFunction(() => (document.querySelector('#modal h2')?.textContent || '').includes('erstellt'), null, { timeout: 8000 });
  check((await page.locator('#modal h2').textContent()).includes('2 Rechnungen erstellt'), 'Rechnungen festgeschrieben');
  await page.click('#modal [data-close-result]');
  check((await page.locator('main tbody').textContent()).includes('Nicht versendet'), 'Versandstatus in der Liste');
  const [pdf] = await Promise.all([page.waitForEvent('popup'), page.locator('main a:has-text("PDF")').first().click()]);
  await pdf.close();
  check(true, 'Rechnungs-PDF öffnet');
  await page.click('[data-filter=unsent]');
  await page.check('main [data-all]');
  const printHref = await page.locator('main .bulkbar a:has-text("Drucken")').getAttribute('href');
  const printRes = await page.request.get(`http://127.0.0.1:${port}${printHref}`);
  const printBody = await printRes.body();
  check(printHref.startsWith('/api/invoices.pdf?ids=') && printRes.ok() && printBody.subarray(0, 4).toString() === '%PDF', 'Sammel-PDF zum Drucken');
  await page.click('main [data-send-picked]');
  await page.waitForSelector('#modal [data-go]');
  check((await page.locator('#modal .bulkbar strong').textContent()).includes('1 per E-Mail'), 'Ohne E-Mail nur Druck');
  await page.click('#modal [data-go]');
  await page.click('#modal [data-start]');
  await page.waitForFunction(() => (document.querySelector('#modal h2')?.textContent || '').includes('abgeschlossen'), null, { timeout: 15000 });
  await page.click('#modal [data-done]');
  check(mails().length === 3, 'Zweite Rechnung versendet, keine Doppelversendung');

  // Zahlungserinnerung für eine überfällige Rechnung (Fälligkeit und Rechnungsversand in der Testdatenbank zurückdatiert)
  execFileSync('python3', ['-c', `import sqlite3, datetime; c = sqlite3.connect(${JSON.stringify(join(base, 'Daten', 'finanzen.sqlite3'))}); c.execute('UPDATE invoices SET due=? WHERE number=?', ((datetime.date.today() - datetime.timedelta(days=30)).isoformat(), ${JSON.stringify(dueNumber)})); c.execute("UPDATE mailings SET time=? WHERE invoice_id=(SELECT id FROM invoices WHERE number=?)", ((datetime.date.today() - datetime.timedelta(days=45)).isoformat() + 'T10:00:00', ${JSON.stringify(dueNumber)})); c.commit()`]);
  await page.reload();
  await page.waitForSelector('[data-page=openitems]');
  await page.click('[data-page=openitems]');
  await page.click('[data-side=receivables]');   // erster Reiter sind die Mitgliedsbeiträge (OP-Listen)
  await page.click('[data-remind]');
  await page.waitForSelector('#modal [data-go]');
  check((await page.locator('#modal tbody').textContent()).includes('1. Erinnerung bereit'), 'Überfällige Rechnung zur Erinnerung bereit');
  await page.locator('#modal tr:has-text("Erinnerung bereit") [data-look]').click();
  check(await page.frameLocator('#preview iframe.mail-frame').locator('text=Bitte zahlen bis').first().waitFor({ timeout: 8000 }).then(() => true, () => false), 'Vorschau der Zahlungserinnerung');
  await page.locator('#preview [data-close]').first().click();
  await shot('27-zahlungserinnerung');
  await page.click('#modal [data-go]');
  await page.click('#modal [data-start]');
  await page.waitForFunction(() => (document.querySelector('#modal h2')?.textContent || '').includes('abgeschlossen'), null, { timeout: 15000 });
  await page.click('#modal [data-done]');
  const lastMail = readdirSync(mailDir).filter((f) => f.endsWith('.eml')).sort((a, b) => parseInt(a) - parseInt(b)).at(-1);
  const subject = execFileSync('python3', ['-c', 'import email, sys; from email import policy; print(email.message_from_bytes(open(sys.argv[1], "rb").read(), policy=policy.default)["Subject"])', join(mailDir, lastMail)]).toString();
  check(mails().length === 4 && subject.startsWith('Zahlungserinnerung zur Rechnung ' + dueNumber) && mails()[3].includes(dueNumber + '.pdf'), 'Zahlungserinnerung mit Rechnung beim Empfänger');
  // die Liste lädt nach dem Schließen des Dialogs neu – auf den erwarteten Stand warten statt sofort zu lesen
  check(await page.locator('main tbody', { hasText: '1× erinnert' }).first().waitFor({ timeout: 8000 }).then(() => true, () => false), 'Erinnerung in den offenen Posten');
  await page.click('[data-page=invoices]');

  // SEPA-Lastschrift: Mandat erfassen, Beitrag 2027 mit Vorabankündigung, Lastschriftdatei pain.008.001.08
  await page.click('[data-page=members]');
  await page.click('[data-new]');
  await page.fill('input[name=number]', 'T-2');
  await page.fill('input[name=first_name]', 'Lena');
  await page.fill('input[name=last_name]', 'Lastschrift');
  await page.fill('input[name=birth]', '2001-05-05');
  await page.fill('input[name=joined]', '2025-01-01');
  await page.fill('input[name=email]', 'lena@example.org');
  await page.fill('input[name=street]', 'Musterweg 2');
  await page.fill('input[name=zip]', '20000');
  await page.fill('input[name=city]', 'Hamburg');
  await page.selectOption('#member-form select[name=kv]', '5300');
  await page.click('#member-form button[type=submit]');
  check((await toast()).includes('angelegt'), 'Zweites Mitglied anlegen');
  await page.click('[data-member]:has-text("Lena")');
  await page.click('#modal [data-new-mandate]');
  await page.fill('#mandate-form input[name=iban]', 'DE02 1203 0000 0000 2020 51');
  await page.click('#mandate-form button[type=submit]');
  check((await toast()).includes('Mandat JUHH-T-2-1'), 'SEPA-Mandat erfassen');
  check((await page.locator('#modal').textContent()).includes('DE02 •••• 2051'), 'Mandat im Mitgliedskonto');
  await shot('28-mandat');
  await dialogClose();
  await page.click('[data-page=invoices]');
  await page.click('[data-dues]');
  await page.selectOption('#dues-year', '2027');
  await page.waitForSelector('#modal tr:has-text("Lena")');
  await page.locator('#modal tr:has-text("Erika") .dues-check').uncheck();
  check((await page.locator('#modal tr:has-text("Lena")').textContent()).includes('per Lastschrift'), 'Beitragslauf zeigt Lastschrift');
  await page.click('#issue-dues');
  await page.waitForFunction(() => (document.querySelector('#modal h2')?.textContent || '').includes('erstellt'), null, { timeout: 8000 });
  await page.click('#modal [data-close-result]');
  await page.click('[data-sepa]');
  await page.waitForSelector('#modal [data-create]');
  check((await page.locator('#modal tbody').textContent()).includes('Vorabankündigung nicht versendet'), 'Lastschrift erst nach Versand der Vorabankündigung');
  await page.click('#modal [data-notice]');
  await page.click('#notice-form button[type=submit]');
  check((await toast()).includes('Postversand eingetragen'), 'Postversand der Vorabankündigung eintragen');
  await page.waitForSelector('#modal [data-pick]:not([disabled])');
  check((await page.locator('#modal tbody').textContent()).includes('JUHH-T-2-1'), 'Lastschrift mit Mandat bereit');
  await page.locator('#modal [data-pick]').first().check();
  await page.click('#modal [data-create]');
  check((await toast()).includes('Lastschriftdatei erstellt'), 'Lastschriftdatei erstellen');
  const [xmlDownload] = await Promise.all([page.waitForEvent('download'), page.click('#modal [data-download]')]);
  const xmlText = readFileSync(await xmlDownload.path(), 'utf8');
  check(xmlDownload.suggestedFilename().startsWith('Lastschrift-JUHH-') && xmlText.includes('pain.008.001.08') && xmlText.includes('JUHH-T-2-1')
    && xmlText.includes('DE98ZZZ09999999999'), 'Lastschriftdatei pain.008.001.08 herunterladen');
  check((await toast()).includes('Datei heruntergeladen'), 'Hinweis nach dem Herunterladen');
  await page.waitForSelector('#modal [data-submitted]');
  await shot('29-lastschriften');
  await page.click('#modal [data-submitted]');
  check((await toast()).includes('Als eingereicht markiert'), 'Als eingereicht markiert');
  await page.waitForSelector('#modal [data-submitted]', { state: 'detached' });
  check(await page.locator('#modal [data-download]').count() === 0, 'Eingereichte Datei nicht erneut herunterladbar');
  check(await page.locator('#modal [data-reject-debit]').count() === 1, 'Einzelne Lastschrift als nicht ausgeführt markierbar');
  await dialogClose();
  await page.click('[data-filter=all]');
  await shot('09-rechnungen');

  // Kontoauszug importieren (Testdatei mit erfundenen Umsätzen) + Rechnungsnummer im Zweck
  const csv = `Datum;Betrag;Name;Verwendungszweck\n05.06.2026;30,00;Erika Musterfrau;Beitrag ${dueNumber}\n06.06.2026;-12,50;Büro Demo;Druckerpatronen\n07.06.2026;-40,00;Getränke Demo;Stammtisch KV Mitte\n08.06.2026;-9,90;Unklar GmbH;Ohne Beleg\n`;
  writeFileSync(join(base, 'auszug.csv'), csv);
  await page.click('[data-page=bank]');
  await page.click('[data-import]');
  await page.setInputFiles('#bank-file', join(base, 'auszug.csv'));
  await page.click('#confirm-import');
  check((await toast()).includes('4 Umsätze importiert'), 'CSV-Import');
  await page.selectOption('#year', '2026');
  await page.waitForSelector('.bank-item');
  check(await items(4), 'Arbeitsliste zeigt 4 offene Umsätze');
  await shot('01-buchungsplatz');

  // 1) Rechnung erkannt → übernehmen und verbuchen
  check(await page.locator('.suggestion:has-text("Rechnung erkannt")').count() === 1, 'Rechnung im Zweck erkannt');
  await page.click('#book-submit');
  check((await toast()).includes('verbucht'), 'Beitrag auf Rechnung verbucht');
  check(await items(3), 'nächster Umsatz ausgewählt, 3 offen');

  // 2) Sachkonto mit automatischem Haushaltstitel, als Lernbeispiel
  check(await page.locator('.part select[data-account]').count() === 0 && await page.locator('.part [data-title]:visible').count() === 0, 'Ein Feld Haushaltsposten, keine zweite Titelliste');
  await pick('.part [data-account]', '4210');
  check(await page.locator('.part [data-title]').inputValue() === 'H27', 'Haushaltstitel automatisch passend zum Konto');
  check((await page.locator('.part [data-account]').textContent()).includes('Bürobedarf / Telefon / EDV') && !(await page.locator('.part [data-title-hint]').textContent()).includes('Bürobedarf'), 'Name wie in der Excel, Titel nicht doppelt');
  await page.check('#learn');
  await shot('02-kontierung');
  await page.keyboard.press('Escape');
  await page.locator('body').click({ position: { x: 5, y: 890 } });
  await page.keyboard.press('Enter');
  check((await toast()).includes('verbucht'), 'Verbuchen per Enter-Taste');

  // 3) Aufteilen: KV-Sammelkonto + Sachkonto (erst warten, bis der nächste Umsatz geladen ist)
  await items(2);
  await page.waitForFunction(() => document.querySelector('.part [data-amount]')?.value === '-40,00', null, { timeout: 8000 }).catch(() => {});
  await page.fill('.part [data-amount]', '-30,00');
  await pick('.part [data-account]', 'KV');
  check((await page.locator('.part [data-title-hint]').textContent()).includes('Sammelkonto KV nur mit einem Kreisverband'), 'Sammelkonto KV ohne Kreisverband abgelehnt');
  await page.selectOption('.part [data-kv]', '5500');
  check(await page.locator('.part [data-title]').inputValue() === 'H54', 'KV-Titel passend zum Bereich');
  await page.click('#add-part');
  const second = page.locator('.part').nth(1);
  check(await second.locator('[data-amount]').inputValue() === '-10,00', 'Rest wird als Teilbetrag vorgeschlagen');
  await pick(second.locator('[data-account]'), '4470');
  check(await second.locator('[data-title]').inputValue() === 'H44', 'Titel für Teilbetrag');
  check((await page.locator('#difference').textContent()).includes('Vollständig'), 'Differenz 0');
  await page.click('#book-submit');
  check((await toast()).includes('verbucht'), 'Aufteilung verbucht');

  // 4) Parken, dann auflösen
  // nach dem Verbuchen lädt die Seite neu und baut die Liste neu auf – erst danach ist das Formular stabil
  await page.waitForLoadState('networkidle').catch(() => {});
  for (let attempt = 0; ; attempt++) {
    try {
      await page.click('#park', { timeout: 5000 });
      await page.fill('#park-form input', 'Beleg fehlt', { timeout: 3000 });
      await page.click('#park-form button[type=submit]', { timeout: 3000 });
      break;
    } catch (e) { if (attempt >= 3) throw e; }
  }
  check((await toast()).includes('1370'), 'Parken');
  check((await page.locator('.empty').textContent()).includes('Alles verbucht'), 'Leerer Zustand „Alles verbucht“');
  await page.click('[data-filter=parked]');
  check((await page.locator('.notice.warn').first().textContent()).includes('Beleg fehlt'), 'Klärungsgrund sichtbar');
  await pick('.part [data-account]', '4210');
  await page.click('#book-submit');
  check((await toast()).includes('aufgelöst'), 'Parkkonto aufgelöst');

  // 5) Storno einer verbuchten Zuordnung
  await page.click('[data-filter=booked]');
  await page.locator('.bank-item').first().click();
  check((await page.locator('.booked-lines').textContent()).length > 5, 'Gebuchte Zeilen sichtbar');
  await page.click('[data-reverse]');
  await page.click('#modal [data-ok]');
  check((await toast()).includes('storniert'), 'Storno');
  check(await page.locator('[data-filter=open][aria-selected=true]').count() === 1, 'nach Storno: Ansicht „Offen“');
  check((await page.locator('.bank-item[aria-current=true]').textContent()).includes('Storniert – neu zuordnen'), 'Umsatz wieder offen und markiert');
  check((await page.locator('.detail .notice').first().textContent()).includes('Zuletzt gebucht auf'), 'frühere Kontierung sichtbar');
  await shot('24-storno-neu-zuordnen');
  await pick('.part [data-account]', '4210');
  await page.click('#book-submit');
  check((await toast()).includes('verbucht'), 'stornierten Umsatz neu verbucht');

  // Lernbeispiele
  await page.click('[data-learning]');
  check(await page.locator('#modal [data-forget]').count() === 1, 'Lernbeispiel gespeichert');
  await page.click('#modal [data-forget]');
  check((await toast()).includes('entfernt'), 'Lernbeispiel entfernen');

  // Excel-Übernahme (erfundene Masterdatei)
  await page.click('[data-master]');
  await page.setInputFiles('#master-file', masterPath);
  await page.waitForSelector('#confirm-master');
  await shot('03-excel-uebernahme');
  await page.click('#confirm-master');
  check((await toast()).includes('Übernommen'), 'Excel-Übernahme');

  // Mitgliedsbeitrag ohne Rechnung (8100): Mitglied suchen ist Pflicht, Vorschlag nach Zahlungspartner
  writeFileSync(join(base, 'auszug-beitrag.csv'), 'Datum;Betrag;Name;Verwendungszweck\n10.06.2026;24,00;Lena Lastschrift;Mitgliedsbeitrag 2026\n');
  await page.click('[data-import]');
  await page.setInputFiles('#bank-file', join(base, 'auszug-beitrag.csv'));
  await page.click('#confirm-import');
  check((await toast()).includes('importiert'), 'Beitragszahlung importiert');
  await page.click('[data-filter=open]');
  await page.locator('.bank-item:has-text("Lena Lastschrift")').first().click();
  await page.locator('.part [data-account]').first().click();
  await page.fill('#account-query', '8100');
  await page.keyboard.press('Enter');
  check(await page.locator('#member-query').waitFor({ timeout: 4000 }).then(() => true, () => false), '8100 öffnet die Mitgliedersuche');
  check((await page.locator('#member-list').textContent()).includes('Passend zur Zahlung') && (await page.locator('#member-list .member-option').first().textContent()).includes('Lastschrift, Lena'), 'Vorschlag nach Zahlungspartner');
  await page.locator('.kv-chip:has-text("Eimsbüttel")').click();
  await page.fill('#member-query', '20000 musterweg 2');
  check(await page.locator('#member-list .member-option').count() === 1, 'Suche nach Anschrift innerhalb des Kreisverbands');
  await shot('34-mitgliedersuche');
  await page.keyboard.press('Enter');
  check((await page.locator('.part [data-members-label]').textContent()).includes('Lastschrift, Lena') && await page.locator('.part [data-kv-field]').isHidden(), 'Mitglied zugeordnet, Bereich = Kreisverband des Mitglieds');
  await page.click('#book-submit');
  check((await toast()).includes('verbucht'), 'Mitgliedsbeitrag mit Mitglied verbucht');

  // Beiträge zuordnen: 8100-Zahlungen aus der Excel-Übernahme nachträglich einem Mitglied zuordnen
  await page.waitForFunction(() => !document.querySelector('.part [data-members-label]')?.textContent.includes('Lastschrift, Lena'), null, { timeout: 8000 }).catch(() => {});
  await page.click('[data-page=members]');
  check(await page.waitForFunction(() => [...document.querySelectorAll('main tbody tr')].some((r) => r.textContent.includes('Lastschrift, Lena') && /bezahlt|offen/.test(r.textContent)),
    null, { timeout: 8000 }).then(() => true, () => false), 'Stand des Beitrags in der Mitgliederliste');
  check(!(await page.locator('main tbody').textContent()).includes('€') && await page.locator('main [data-dues], main [data-arrears], main [data-sepa]').count() === 0,
    'Mitgliederliste ohne Beträge und ohne Buchhaltungsknöpfe');
  await shot('10b-mitglieder-liste');
  await page.click('[data-page=dues]');
  check((await page.locator('h1').textContent()) === 'Beitragseingänge', 'Beitragseingänge unter Buchhaltung');
  await page.waitForSelector('main [data-pick]');
  const openDues = await page.locator('main [data-pick]').count();
  await page.locator('main [data-pick]').first().click();
  await page.fill('#member-query', 'musterfrau');
  await page.keyboard.press('Enter');
  check((await toast()).includes('zugeordnet'), 'Beitrag aus der Excel-Übernahme zugeordnet');
  await page.waitForFunction((n) => document.querySelectorAll('main [data-pick]').length === n - 1, openDues, { timeout: 5000 }).catch(() => {});
  check(await page.locator('main [data-pick]').count() === openDues - 1, 'Zugeordnete Zahlung verschwindet aus der Liste');
  await shot('34a-beitragseingaenge');

  // 0.29: Beitrag einer Person, die nicht mehr in der Mitgliederliste steht (ausgetreten), und SEPA-Sammler aus einer Lastschriftdatei
  writeFileSync(join(base, 'auszug-extern.csv'), 'Datum;Betrag;Name;Verwendungszweck\n11.06.2026;30,00;Otto Ehemalig;Beitrag 2026\n'
    + '12.06.2026;54,00;Sparkasse;SEPA-Sammler Lastschrift\n');
  await page.click('[data-page=bank]');
  await page.click('[data-import]');
  await page.setInputFiles('#bank-file', join(base, 'auszug-extern.csv'));
  await page.click('#confirm-import');
  check((await toast()).includes('importiert'), 'Zahlung einer ausgetretenen Person und SEPA-Sammler importiert');
  await page.click('[data-filter=open]');
  await page.locator('.bank-item:has-text("Otto Ehemalig")').first().click();
  await page.locator('.part [data-account]').first().click();
  await page.fill('#account-query', '8100');
  await page.keyboard.press('Enter');
  await page.waitForSelector('#member-external');
  await page.click('#member-external summary');
  check(await page.inputValue('#ext-name') === 'Otto Ehemalig', 'Name aus dem Kontoauszug vorgegeben');
  await page.selectOption('#ext-kv', '5300');
  await shot('34b-nicht-in-mitgliederliste');
  await page.click('#ext-add');
  check((await page.locator('.part [data-members-label]').textContent()).includes('nicht in der Mitgliederliste'), 'Person außerhalb der Mitgliederliste zugeordnet');
  await page.click('#book-submit');
  check((await toast()).includes('verbucht'), 'Beitrag einer ausgetretenen Person verbucht');
  const tx = (no, amount, name) => `<DrctDbtTxInf><PmtId><EndToEndId>UI-E2E-${no}</EndToEndId></PmtId><InstdAmt Ccy="EUR">${amount}</InstdAmt>`
    + `<DrctDbtTx><MndtRltdInf><MndtId>UI-M-${no}</MndtId></MndtRltdInf></DrctDbtTx><Dbtr><Nm>${name}</Nm></Dbtr>`
    + `<DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct><RmtInf><Ustrd>Mitgliedsbeitrag 2026</Ustrd></RmtInf></DrctDbtTxInf>`;
  writeFileSync(join(base, 'lastschrift.xml'), '<?xml version="1.0" encoding="UTF-8"?><Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.008.001.08">'
    + '<CstmrDrctDbtInitn><GrpHdr><MsgId>UI-1</MsgId><NbOfTxs>2</NbOfTxs></GrpHdr><PmtInf><PmtInfId>UI-1-1</PmtInfId><PmtMtd>DD</PmtMtd><NbOfTxs>2</NbOfTxs>'
    + `<ReqdColltnDt>2026-06-12</ReqdColltnDt>${tx(1, '24.00', 'Lena Lastschrift')}${tx(2, '30.00', 'Unbekannte Person')}</PmtInf></CstmrDrctDbtInitn></Document>`);
  await page.click('[data-page=dues]');
  await page.click('main [data-sepa]');
  await page.setInputFiles('#sepa-file', join(base, 'lastschrift.xml'));
  await page.waitForSelector('#modal .sepa-batch');
  const sepaText = await page.locator('#modal .sepa-batch').textContent();
  check(sepaText.includes('2 Einzelzahlungen') && sepaText.includes('SEPA-Sammler'), 'SEPA-Datei gelesen, Sammelbuchung im Kontoauszug gefunden');
  check(await page.locator('#modal [data-apply]').isDisabled(), 'Aufteilen erst, wenn jede Einzelzahlung zugeordnet ist');
  // 0.30: Begleitzettel (PDF) mit der Spalte „Zahler“ (erfunden, wie test_sepadatei.begleitzettel)
  execFileSync('python3', ['-c', `import sys; sys.path.insert(0, ${JSON.stringify(repo)}); import test_sepadatei as t
open(${JSON.stringify(join(base, 'begleitzettel.pdf'))}, 'wb').write(t.begleitzettel([('UI-E2E-1', 'LENA LASTSCHRIFT', 'Lastschrift, Lena', 'INGDDEFFXXX', 'DE89370400440532013000', 'UI-M-1', 'Mitgliedsbeitrag 2026', '24,00'), ('UI-E2E-2', 'UNBEKANNTE PERSON', 'Person, Unbekannte', 'INGDDEFFXXX', 'DE89370400440532013000', 'UI-M-2', 'Mitgliedsbeitrag 2026', '30,00')]))`]);
  await page.setInputFiles('#modal [data-pdf]', join(base, 'begleitzettel.pdf'));
  check((await toast()).includes('Zahler für 2 Einzelzahlungen'), 'Begleitzettel eingelesen');
  check((await page.locator('#modal .sepa-batch').textContent()).includes('Zahler: Lastschrift, Lena'), 'Zahler aus dem Begleitzettel angezeigt');
  for (const btn of await page.locator('#modal [data-choose]').all()) {
    const row = await btn.locator('xpath=ancestor::tr').textContent();
    if (row.includes('Unbekannte Person')) {
      await btn.click();
      await page.click('#member-external summary');
      await page.fill('#ext-reason', 'ausgetreten 2025, Daten gelöscht');
      await page.click('#ext-add');
    } else if (row.includes('noch nicht zugeordnet')) {
      await btn.click();
      await page.fill('#member-query', 'lastschrift');
      await page.keyboard.press('Enter');
    }
  }
  if (await page.locator('#modal [data-confirm-all]').count()) await page.click('#modal [data-confirm-all]');   // einfache Namensvorschläge auf einmal
  while (await page.locator('#modal [data-confirm]').count()) await page.locator('#modal [data-confirm]').first().click();   // Hinweise einzeln
  check(await page.locator('#modal [data-confirm]').count() === 0, 'Vorschläge bestätigt (Sammel- und Einzelbestätigung)');
  check(await page.locator('#modal [data-apply]').isEnabled(), 'Aufteilen möglich, wenn alles zugeordnet und bestätigt ist');
  await shot('34c-sepa-datei');
  await page.click('#modal [data-apply]');
  check((await toast()).includes('aufgeteilt'), 'SEPA-Sammler auf Mitglieder aufgeteilt');
  check(await page.waitForFunction(() => document.querySelector('#modal .sepa-batch')?.textContent.includes('Aufgeteilt'), null, { timeout: 8000 }).then(() => true, () => false),
    'Ergebnis im Dialog');
  await page.keyboard.press('Escape');
  await page.click('[data-page=members]');

  // Mitgliederliste importieren (erfundene Daten): ein neues Mitglied, eine Änderung, ein Fehler
  writeFileSync(join(base, 'mitglieder.csv'), 'Mitgliedsnummer;Vorname;Nachname;Straße;PLZ;Ort;Geburtsdatum;Eintritt;Kreisverband;E-Mail\n'
    + 'T-1;Erika;Musterfrau;Neuer Weg 5;20000;Hamburg;01.02.2000;01.01.2024;Altona;erika@example.org\n'
    + 'T-3;Paul;Probe;Testallee 3;21029;Hamburg;03.03.2003;01.04.2025;Bergedorf;\n'
    + 'T-4;Ohne;KV;Testallee 4;21029;Hamburg;03.03.2003;01.04.2025;;\n');
  await page.click('[data-import]');
  await page.setInputFiles('#members-file', join(base, 'mitglieder.csv'));
  await page.waitForSelector('#members-confirm');
  check((await page.locator('#members-preview').textContent()).includes('1 neu · 1 geändert'), 'Import-Vorschau mit neu/geändert/Fehler');
  await shot('35-mitglieder-import');
  await page.click('#members-confirm');
  check((await toast()).includes('1 Mitglieder angelegt, 1 aktualisiert'), 'Mitgliederliste übernommen');
  await page.fill('#member-search', 'testallee');
  check(await page.locator('main tbody tr').count() === 1 && (await page.locator('main tbody tr').textContent()).includes('Probe, Paul'), 'Mitgliedersuche nach Straße');

  // Export der CDU-Mitgliederverwaltung (Spalten wie finanzen_mitglieder_gesamt.csv, Windows-1252) – erfundene Personen
  await page.fill('#member-search', '');
  const cduHead = 'LVKV_Name;Ortsverband;Ortsbezeichnung;Mitgliedsnummer;Anrede;Briefanrede_1;Titel;Adelname;Vorname;Nachname;Strasse;PLZ;PLZ_Ort;Nationalität;'
    + 'Geschlecht;Geburtstag;Geburtsmonat;Geburtsjahr;Jahre;Vereine;Verein_Eintritt;KV_Eintritt;Jubiläumjahre;Beitragsfrei;Gastmitglied;Verein;Kontostand;'
    + 'Jahresbeitrag;Zahler_Zahlungsart;Telefon_Privat;Handy_Privat;Email_Privat';
  writeFileSync(join(base, 'cdu-export.csv'), Buffer.from([cduHead,
    'Hamburg-Altona;11;Testort;C-1;Herrn;Lieber Max;Dr.;von;Max;Probe;Teststraße 1;21999;21999 Teststadt;D;M;17;8;2002;24;CDU,JU;14.09.22;03.10.22;3;N;N;JU;0;30;R;;0170 1;max@example.org',
    'Hamburg-Nord;3;Nordort;C-2;Frau;Liebe Gina;;;Gina;Gast;Weg 2;22301;22301 Hamburg;D;W;1;12;2005;20;JU;01.07.25;01.07.25;1;N;J;JU;0;;;;;'].join('\r\n') + '\r\n', 'latin1'));
  await page.click('[data-import]');
  await page.setInputFiles('#members-file', join(base, 'cdu-export.csv'));
  await page.waitForSelector('#members-confirm');
  const cduPreview = await page.locator('#members-preview').textContent();
  check(cduPreview.includes('2 neu') && cduPreview.includes('1 Gastmitglieder') && cduPreview.includes('Austritt prüfen'), 'CDU-Export erkannt (neu, Gast, fehlende Mitglieder)');
  check(cduPreview.includes('2 Ortsverbände sind noch nicht in den Stammdaten') && cduPreview.includes('11 Testort (neu)'), 'Unbekannte Ortsverbände in der Vorschau');
  await page.click('#members-confirm');
  check((await toast()).includes('2 Mitglieder angelegt, 0 aktualisiert, 2 Ortsverbände angelegt'), 'CDU-Export übernommen');
  await page.fill('#member-search', 'teststadt');
  check((await page.locator('main tbody tr').count()) === 1 && (await page.locator('main tbody tr').textContent()).includes('Probe, Max'), 'Mitglied aus dem CDU-Export');
  await page.locator('main tbody tr').first().click();
  await page.waitForSelector('#modal [data-account] h3');
  check((await page.locator('#modal .panel-head').textContent()).includes('OV 11 Testort') && (await page.locator('#modal [data-account]').textContent()).includes('individueller Jahresbeitrag'),
    'Zusatzangaben und Jahresbeitrag aus der Mitgliederverwaltung');
  await page.click('#modal [data-edit]');
  check(await page.inputValue('#member-form input[name=mobile]') === '0170 1' && await page.inputValue('#member-form input[name=letter_salutation]') === 'Lieber Max'
    && (await page.locator('#member-form select[name=ov_id] option:checked').textContent()) === '11 Testort', 'Alle Angaben im Formular, Ortsverband gewählt');
  await shot('10b-mitglied-formular');
  await page.keyboard.press('Escape');
  // kennwortgeschützte Excel-Datei: Kennwort wird abgefragt, falsches abgelehnt
  if (existsSync(lockedPath)) {
    await page.click('[data-import]');
    await page.setInputFiles('#members-file', lockedPath);
    await page.waitForSelector('#members-preview .pw-box');
    check((await page.locator('#members-preview').textContent()).includes('Kennwort geschützt'), 'Geschützte Datei: Kennwort wird abgefragt');
    await page.fill('#members-preview input[name=pw]', 'Irrtum-999');
    await page.click('#members-preview .pw-box button[type=submit]');
    await page.waitForFunction(() => document.querySelector('#members-preview')?.textContent.includes('falsch'), null, { timeout: 8000 });
    check(true, 'Falsches Kennwort erkannt');
    await page.fill('#members-preview input[name=pw]', lockedPassword);
    await page.click('#members-preview .pw-box button[type=submit]');
    await page.waitForSelector('#members-confirm');
    check((await page.locator('#members-preview').textContent()).includes('1 neu'), 'Geschützte Datei mit Kennwort gelesen');
    await page.click('#members-confirm');
    check((await toast()).includes('1 Mitglieder angelegt'), 'Mitglied aus geschützter Datei übernommen');
  } else console.log('übersprungen: msoffcrypto-tool fehlt (geschützte Datei)');
  await page.fill('#member-search', 'gast');
  check((await page.locator('main tbody tr').first().textContent()).includes('Gast'), 'Gastmitglied gekennzeichnet');
  // Mitglied löschen: ohne Buchhaltungsbezug möglich (mit Sicherung), mit Rechnungen gesperrt
  await page.fill('#member-search', 'gast');
  await page.locator('main tbody tr').first().click();
  await page.click('#modal [data-delete]');
  await page.click('#preview [data-confirm]');
  check((await toast()).includes('Mitglied gelöscht. Sicherung: vor-mitglied-loeschen'), 'Mitglied ohne Buchhaltungsbezug gelöscht');
  check(!(await page.locator('main tbody').textContent().catch(() => '')).includes('Gast, Gina'), 'Gelöschtes Mitglied nicht mehr in der Liste');
  await page.fill('#member-search', 'musterfrau');
  await page.locator('main tbody tr').first().click();
  await page.click('#modal [data-delete]');
  check((await page.locator('#preview').textContent()).includes('Austritt') && await page.locator('#preview [data-confirm]').count() === 0,
    'Mitglied mit Rechnungen: Löschen gesperrt (Hinweis statt Knopf)');
  await page.locator('#preview [data-close]').last().click();
  await page.keyboard.press('Escape');
  await page.fill('#member-search', 'testallee');

  // Mitgliederbuchhaltung: Beitragskonto, Soll festlegen, Rückstände
  check((await page.locator('main .cards').textContent()).includes('Aktive Mitglieder'), 'Kennzahlen des Mitgliederbestands');
  await page.locator('main tbody tr').first().click();
  await page.waitForSelector('#modal [data-target]');
  check((await page.locator('#modal [data-account]').textContent()).includes('Rückstand'), 'Beitragskonto mit Rückstand (Soll laut Staffel)');
  check(await page.locator('#modal a[href^="/api/member-account.pdf"]').count() === 1, 'Beitragskonto als PDF');
  const paulPdf = await page.evaluate(async () => (await fetch(document.querySelector('#modal a[href^="/api/member-account.pdf"]').href)).headers.get('content-type'));
  check(paulPdf === 'application/pdf', 'Beitragskonto-PDF wird erzeugt');
  await shot('36-beitragskonto');
  await page.locator('#modal [data-target]').last().click();
  await page.fill('#target-form input[name=amount]', '0');
  await page.fill('#target-form input[name=reason]', 'Beitragsbefreiung (Testfall)');
  await page.click('#target-form button[type=submit]');
  check((await toast()).includes('festgelegt'), 'Soll von Hand festgelegt');
  await page.waitForFunction(() => document.querySelector('#modal [data-account]')?.textContent.includes('festgelegt: Beitragsbefreiung'), null, { timeout: 5000 }).catch(() => {});
  check((await page.locator('#modal [data-account]').textContent()).includes('festgelegt: Beitragsbefreiung'), 'Festlegung im Beitragskonto sichtbar');
  await page.keyboard.press('Escape');
  await page.fill('#member-search', '');
  await page.click('[data-page=openitems]');
  await page.click('[data-side=dues]');
  check((await page.locator('.tabs button').first().textContent()).includes('Mitgliedsbeiträge'), 'Offene Posten: Mitgliedsbeiträge als erster Reiter');
  await page.waitForSelector('main [data-op] .toolbar');
  check(await page.locator('main a[href^="/api/op-liste.pdf"]').count() >= 1, 'OP-Liste mit PDF');
  check((await page.locator('main [data-op] .cards').textContent()).includes('Offene Beiträge (Stand heute)'), 'OP-Liste in der Buchhaltung angezeigt');
  const opPdf = await page.evaluate(async () => (await fetch(document.querySelector('main a[href^="/api/op-liste.pdf"]').href)).headers.get('content-type'));
  check(opPdf === 'application/pdf', 'OP-Liste als PDF');
  await shot('37-op-listen');
  await page.locator('main [data-op] tr[data-member]').first().click();
  check(await page.waitForSelector('#modal [data-account]', { timeout: 5000 }).then(() => true, () => false), 'Beitragskonto aus der OP-Liste');
  await page.keyboard.press('Escape');
  await page.click('[data-side=receivables]');
  check(await page.locator('main [data-remind]').count() === 1, 'Offene Posten: Reiter Rechnungen');
  await page.click('[data-page=dues]');
  check((await page.locator('main .cards').textContent()).includes('Zahlungen ohne Mitglied'), 'Kennzahlen der Mitgliederbuchhaltung bei den Beitragseingängen');

  // 0.31: Klärfälle – die Buchhaltung meldet, die Mitgliederverwaltung klärt (erfundene Daten)
  await page.waitForSelector('main [data-report]');
  await page.locator('main [data-report]').first().click();
  check((await toast()).includes('Klärfall'), 'Zahlung ohne Mitglied als Klärfall gemeldet');
  // Prüfbefund 0.31: „Zum Klärfall“ öffnet genau diesen Fall – auch wenn die Seite Klärfälle schon offen war und einen anderen zeigt
  await page.check('main [data-all]');
  await page.waitForSelector('main [data-go-case]');
  const caseId = await page.locator('main [data-go-case]').first().getAttribute('data-go-case');
  await page.click('[data-page=cases]');
  await page.waitForSelector('main [data-case]');
  await page.locator(`main [data-case]:not([data-case="${caseId}"])`).first().click();
  const shownBefore = await page.locator('main [data-case][aria-current=true]').getAttribute('data-case');
  await page.click('[data-page=dues]');
  await page.waitForSelector('main [data-go-case]');
  await page.locator('main [data-go-case]').first().click();
  await page.waitForSelector(`main [data-case="${caseId}"][aria-current=true]`, { timeout: 8000 }).catch(() => {});
  check(Number(caseId) > 0 && shownBefore !== caseId && (await page.locator('main [data-case][aria-current=true]').getAttribute('data-case')) === caseId
    && (await page.locator('main [data-case][aria-current=true]').textContent()).includes('ohne Zuordnung'), '„Zum Klärfall“ öffnet den gemeldeten Fall, nicht den zuletzt gezeigten');
  await page.click('[data-page=dues]');
  await page.waitForSelector('main [data-all]');
  await page.uncheck('main [data-all]');
  await page.click('[data-page=cases]');
  await page.waitForSelector('main [data-case]');
  check((await page.locator('h1').textContent()) === 'Klärfälle' && await page.locator('main [data-case]').count() === 3,
    'Klärfälle: gemeldete Zahlung und zwei als „nicht in der Mitgliederliste“ gebuchte');
  check((await page.locator('.nav [data-page=cases] .count').textContent()) === '3', 'Zähler in der Seitenleiste');
  await shot('39-klaerfaelle');
  for (const [id, name] of [['cases', 'Klärfälle'], ['dues', 'Beitragseingänge'], ['overview', 'Start'], ['members', 'Mitglieder']]) {   // Handy 390 px
    await page.setViewportSize({ width: 390, height: 844 });
    await page.click('#menu-toggle');
    await page.click(`[data-page=${id}]`);
    await page.waitForTimeout(400);
    check(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) <= 0, `${name} auf dem Handy ohne seitliches Scrollen`);
    if (id === 'cases') await shot('39b-klaerfaelle-handy');
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.click('[data-page=cases]');
  await page.waitForSelector('main [data-case]');
  await page.locator('main [data-case]:has-text("ohne Zuordnung")').first().click();
  await page.check('input[name="case-how"][value=former]');
  check(await page.evaluate(() => document.activeElement?.name === 'case-how' && document.activeElement.value === 'former'),
    'Auswahl im Klärfall behält den Tastaturfokus');
  await page.selectOption('[data-ext=kv]', '5100');
  await page.fill('[data-note]', 'Testnotiz: laut Kreisverband ausgetreten');
  await page.click('[data-resolve]');
  check((await toast()).includes('nicht in der Mitgliederliste'), 'Klärfall ohne Zuordnung als ehemaliges Mitglied gebucht');
  await page.waitForFunction(() => document.querySelectorAll('main [data-case]').length === 2, null, { timeout: 8000 }).catch(() => {});
  await page.locator('main [data-case]:has-text("Otto Ehemalig")').click();
  await page.check('input[name="case-how"][value=former]');
  await page.click('[data-resolve]');
  check((await toast()).includes('Bestätigt'), 'Gebuchten Klärfall bestätigt');
  await page.waitForFunction(() => document.querySelectorAll('main [data-case]').length === 1, null, { timeout: 8000 }).catch(() => {});
  await page.selectOption('[data-inquiry-kv]', '5100');
  await page.click('[data-inquiry-on]');
  check((await toast()).includes('Rückfrage vermerkt'), 'Rückfrage beim Kreisverband vermerkt');
  await page.waitForSelector('main [data-inquiry-off]');
  check((await page.locator('main [data-case]').first().textContent()).includes('Rückfrage läuft'), 'Rückfrage in der Liste sichtbar');
  await page.click('[data-resolve]');   // „Ist Mitglied“: Mitglied suchen und zuordnen
  await page.fill('#member-query', 'musterfrau');
  await page.keyboard.press('Enter');
  check((await toast()).includes('zugeordnet'), 'Klärfall einem Mitglied zugeordnet');
  await page.waitForFunction(() => !document.querySelector('main [data-case]'), null, { timeout: 8000 }).catch(() => {});
  check(await page.locator('main [data-case]').count() === 0 && await page.locator('.nav [data-page=cases] .count').count() === 0, 'Alle Klärfälle erledigt');
  await page.selectOption('main [data-status]', 'done');
  check(await page.locator('main [data-case]').count() === 3, 'Erledigte Klärfälle bleiben nachvollziehbar');
  await page.selectOption('main [data-status]', 'active');
  await page.click('[data-page=dues]');
  await page.click('[data-page=members]');
  await page.selectOption('#member-state', 'arrears');
  check(await page.locator('main tbody tr').count() >= 1, 'Filter „mit Rückstand“');
  await page.selectOption('#member-state', '');

  // 0.37: Mitgliederstatus – Austritt eintragen und zurücknehmen, Seite Wahlberechtigung (auch im Mac-Betrieb)
  await page.locator('main [data-member]').first().click();
  await page.waitForSelector('#modal [data-leave]');
  check(/aktiv|wahlberechtigt/.test(await page.locator('#modal [data-status]').textContent()), 'Geöffnetes Mitglied zeigt den Status');
  await page.click('#modal [data-leave]');
  await page.waitForSelector('#preview[open] #leave-form');
  const heute = await page.evaluate(() => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); });
  check(await page.inputValue('#leave-form [name=left_on]') === heute && await page.locator('#leave-form [data-undo]').count() === 0, 'Austritt eintragen: Vorgabe heute');
  await page.fill('#leave-form [name=reason]', 'Umzug');
  await shot('47-austritt');
  await page.click('#leave-form button[type=submit]');
  check((await toast()).includes('Austritt eingetragen'), 'Austritt eingetragen');
  check(await page.waitForFunction(() => document.querySelector('#modal [data-status]')?.textContent.includes('ausgetreten')
    && document.querySelector('#modal [data-contact]')?.textContent.includes('Austrittsgrund: Umzug'), null, { timeout: 5000 }).then(() => true, () => false),
  'Mitglied danach: Status ausgetreten mit Grund');
  await page.click('#modal [data-leave]');
  await page.waitForSelector('#preview[open] #leave-form [data-undo]');
  await page.click('#leave-form [data-undo]');
  check((await toast()).includes('zurückgenommen'), 'Austritt zurückgenommen');
  check(await page.waitForFunction(() => /aktiv|wahlberechtigt/.test(document.querySelector('#modal [data-status]')?.textContent || '')
    && !document.querySelector('#modal [data-status]').textContent.includes('ausgetreten'), null, { timeout: 5000 }).then(() => true, () => false), 'Mitglied wieder aktiv');
  await dialogClose();
  await page.click('[data-page=wahl]');
  await page.waitForSelector('.wr-page .cards');
  check((await page.locator('.wr-page .cards').textContent()).includes('Nicht wahlberechtigt') && await page.locator('.wr-page #wr-day option').count() >= 2,
    'Wahlberechtigung: Kennzahlen und Stichtag-Auswahl');
  check(await page.locator('.wr-page [data-member]').count() >= 1 && (await page.locator('.wr-page').textContent()).includes('Beitrittsstichtag'), 'Wahlberechtigung: Mitglieder mit Status und Grund');
  const wrPdf = await page.evaluate(async () => (await fetch(document.querySelector('.wr-page a[href^="/api/wahlrecht.pdf"]').href)).headers.get('content-type'));
  check(wrPdf === 'application/pdf', 'Wahlberechtigung als PDF');
  await shot('48-wahlberechtigung');
  await page.selectOption('#wr-day', '');
  check(await page.waitForFunction(() => document.querySelector('.wr-page #wr-day')?.value === '' && document.querySelector('.wr-page .toolbar')?.textContent.includes('Beitrittsstichtag'),
    null, { timeout: 5000 }).then(() => true, () => false), 'Wahlberechtigung: laufender Status heute');
  await page.selectOption('#wr-day', 'custom');
  await page.waitForSelector('#wr-custom');
  await page.fill('#wr-custom', '2026-03-15');
  await page.dispatchEvent('#wr-custom', 'change');
  check(await page.waitForFunction(() => document.querySelector('.wr-page .cards')?.textContent.includes('15.3.2026') && document.querySelector('#wr-day')?.value === 'custom'
    && document.querySelector('.wr-page [data-preview]')?.textContent.includes('erst ab 2027'), null, { timeout: 5000 }).then(() => true, () => false),
  'Wahlberechtigung: anderes Datum frei wählbar (2026 nur als Vorschau)');
  await page.selectOption('#wr-status', 'ausgetreten');
  check(await page.locator('.wr-page [data-member]').count() === await page.locator('.wr-page [data-member] .badge:text-is("ausgetreten")').count(), 'Wahlberechtigung: Filter nach Status');
  await page.selectOption('#wr-status', '');
  await page.locator('.wr-page [data-member]').first().click();
  await page.waitForSelector('#modal [data-leave]');
  check(true, 'Wahlberechtigung: Klick öffnet das Mitglied');
  await dialogClose();

  // 0.29: Auswertung der Mitgliederdaten
  await page.click('[data-page=auswertung]');
  await page.waitForSelector('.aw-page .cards');
  check((await page.locator('.aw-page .cards').textContent()).includes('Mitglieder am Stichtag'), 'Auswertung mit Kennzahlen');
  check(await page.locator('.aw-page .aw-bars').count() >= 4, 'Auswertung: Altersgruppen, Eintrittsalter, Mitgliedsdauer, Zahlweg');
  // 0.38: Diagramme – jeder Wert steht als Text am Balken (ohne Zeigen lesbar), nichts läuft auf dem Handy über
  check(await page.locator('.aw-page .aw-two tbody tr').count() >= 7 && (await page.locator('.aw-page .aw-two').first().textContent()).includes('35 und älter'),
    'Auswertung: Alterspyramide nach Geschlecht');
  check((await page.locator('.aw-page .aw-sex li').first().textContent()).includes('weiblich') && await page.locator('.aw-page .aw-sex li').count() >= 2,
    'Auswertung: Geschlechteranteile gesamt und bei den Eintritten');
  check(await page.locator('.aw-page .aw-cols').count() === 3 && await page.locator('.aw-page .aw-cols').last().locator('li').count() === 12
    && await page.locator('.aw-page .aw-cols.both').count() === 2, 'Auswertung: Bestand, Ein-/Austritte je Jahr und je Monat');
  check(await page.evaluate(() => [...document.querySelectorAll('.aw-page .aw-bars li, .aw-page .aw-two tbody tr, .aw-page .aw-cols li:not([aria-hidden])')]
    .every((e) => /\d/.test(e.textContent))), 'Auswertung: jedes Diagramm nennt seine Zahlen als Text');
  await page.setViewportSize({ width: 390, height: 844 });
  // erst prüfen, wenn main.js auf die kompakte Ansicht umgeschaltet hat (sonst misst man noch die Seitenleiste daneben – in der CI gescheitert)
  await page.waitForFunction(() => document.documentElement.classList.contains('compact'));
  const awFits = () => page.evaluate(() => {   // nichts ragt über den Bildschirm oder aus seinem Abschnitt (Tabellen und Reiter haben eigene Rahmen)
    const out = [...document.querySelectorAll('.aw-page .panel *, .aw-page .card *')].filter((e) => !e.closest('.tablewrap, .tabs, .sr-only') && e.getClientRects().length).filter((e) => {
      const box = e.closest('.panel, .card').getBoundingClientRect(), r = e.getBoundingClientRect();
      return r.right > box.right + 0.5 || r.left < box.left - 0.5 || (!e.children.length && getComputedStyle(e).display !== 'inline' && e.scrollWidth > e.clientWidth + 1);
    }).map((e) => `${e.tagName.toLowerCase()}.${e.className} „${e.textContent.trim().slice(0, 20)}“`);
    return { ok: document.documentElement.scrollWidth <= window.innerWidth && !out.length, out: out.slice(0, 5) };
  });
  let fits = await awFits();
  check(fits.ok, `Auswertung: Handy (390 px) ohne seitliches Scrollen ${fits.out.join(' | ')}`);
  check(await page.evaluate(() => { const w = document.querySelector('.aw-page table.aw-ov')?.closest('.tablewrap'); return !w || w.scrollWidth <= w.clientWidth; }),
    'Auswertung: Ortsverbände auf dem Handy ohne Scrollen in der Tabelle');
  const wide = await page.addStyleTag({ content: '* { font-family: "DejaVu Sans", Verdana, sans-serif !important; letter-spacing: .06em; }' });   // Geräte mit breiterer Schrift
  await page.waitForTimeout(300);
  fits = await awFits();
  check(fits.ok, `Auswertung: Handy (390 px) auch mit breiter Schrift ohne Überlauf ${fits.out.join(' | ')}`);
  await wide.evaluate((e) => e.remove());
  await shot('38-auswertung-handy');
  await page.setViewportSize({ width: 1440, height: 900 });
  const awPdf = await page.evaluate(async () => (await fetch(document.querySelector('.aw-page a[href^="/api/auswertung.pdf"]').href)).headers.get('content-type'));
  check(awPdf === 'application/pdf', 'Auswertung als PDF');
  await shot('38-auswertung');
  await page.click('[data-list=gaps]');
  check(await page.locator('.aw-page [data-gap]').count() === 5, 'Datenlücken nach Art');
  await page.selectOption('#aw-kv', { index: 1 });
  await page.waitForFunction(() => document.querySelector('.aw-page .eyebrow')?.textContent && !document.querySelector('.aw-page .eyebrow').textContent.includes('alle Kreisverbände'), null, { timeout: 5000 }).catch(() => {});
  check(!(await page.locator('.aw-page .eyebrow').textContent()).includes('alle Kreisverbände'), 'Auswertung je Kreisverband');
  await page.click('.aw-page [data-op]');
  await page.waitForSelector('main [data-op] .toolbar');
  check(await page.waitForFunction(() => document.querySelectorAll('main .toolbar a[href^="/api/op-liste.pdf?kv=5"]').length === 1, null, { timeout: 5000 }).then(() => true, () => false),
    'OP-Liste aus der Auswertung für den Kreisverband (Buchhaltung → Offene Posten)');
  await page.selectOption('main [data-op] [data-kv]', '');

  // Kasse (0.25): Anfangsbestand, Ausgabe erfassen und wie einen Kontoumsatz verbuchen, Geldtransit, Kassenbuch
  await page.click('[data-page=cash]');
  check((await page.locator('main .notice').first().textContent()).includes('noch nicht eingerichtet'), 'Kasse ohne Anfangsbestand');
  await page.click('[data-opening]');
  await page.fill('#cash-opening input[name=amount]', '100');
  await page.click('#cash-opening button[type=submit]');
  check((await toast()).includes('Anfangsbestand'), 'Anfangsbestand der Kasse');
  await page.click('[data-new-cash]');
  await page.fill('#cash-form input[name=amount]', '12,50');
  await page.fill('#cash-form input[name=text]', 'Porto Testbrief');
  await page.click('#cash-form button[type=submit]');
  check((await toast()).includes('jetzt verbuchen'), 'Kassenbeleg erfasst');
  await page.waitForSelector('#book-form');
  await pick('.part [data-account]', '4220');
  await page.click('#book-submit');
  check((await toast()).includes('verbucht'), 'Kassenbeleg wie Kontoumsatz verbucht');
  await page.click('[data-new-cash]');
  await page.check('#cash-form input[value=withdraw]');
  await page.fill('#cash-form input[name=amount]', '50');
  await page.click('#cash-form button[type=submit]');
  check((await toast()).includes('2380'), 'Geldtransit gebucht');
  await page.waitForSelector('[data-cash-csv]');
  const cashBook = await page.locator('[data-custom]').textContent();
  check(cashBook.includes('Anfangsbestand') && cashBook.includes('137,50') && cashBook.includes('4220'), 'Kassenbuch mit Kontierung und Bestand');
  check((await page.locator('main .cards').textContent()).includes('Geldtransit'), 'Kennzahlen der Kasse');
  await shot('38-kassenbuch');
  const cashPdf = await page.evaluate(async () => (await fetch(document.querySelector('a[href^="/api/kasse.pdf"]').href)).headers.get('content-type'));
  check(cashPdf === 'application/pdf', 'Kassenbuch als PDF');

  // Eingangsrechnung erfassen → Zahlungsvorschlag (Kopieren, GiroCode) → Kontoauszug → Abgleich
  const billNo = `E-${new Date().getFullYear()}-00001`;
  await page.click('[data-page=bills]');
  await page.click('[data-new-bill]');
  await page.fill('#bill-form input[name=creditor]', 'Druckerei Muster GmbH');
  await page.fill('#bill-form input[name=ext_number]', 'RE-4711');
  await page.fill('#bill-form input[name=iban]', 'DE02 1203 0000 0000 2020 51');
  await page.fill('#bill-form input[name=issued]', '2026-10-20');
  await page.fill('.bill-line [data-key=description]', 'Flyer Sommerfest');
  await page.fill('.bill-line [data-key=amount]', '119');
  await page.click('.bill-line [data-account]');
  await page.fill('#account-query', 'büro');
  check(await page.locator('#account-list [data-code]').count() >= 1 && (await page.locator('#account-list').textContent()).includes('4210'), 'Kontenliste filtert nach Bezeichnung');
  await shot('23-kontenauswahl');
  await page.fill('#account-query', '4210');
  await page.keyboard.press('Enter');
  check(await page.locator('.bill-line [data-key=account]').evaluate((e) => e.value) === '4210', 'Konto aus der Liste übernommen');
  check(await page.inputValue('.bill-line [data-key=title]') === 'H27', 'Haushaltstitel der Eingangsrechnung automatisch');
  await pick('.bill-line [data-key=account]', '7200');                        // zwei Titel → Auswahl erscheint
  check(await page.locator('.bill-line [data-key=title]').isVisible(), 'Titelauswahl nur bei mehrdeutigem Posten');
  await page.selectOption('.bill-line [data-key=title]', 'H47');
  await pick('.bill-line [data-key=account]', '4210');                        // Kontowechsel: Titel wieder aus dem Konto
  check(await page.inputValue('.bill-line [data-key=title]') === 'H27' && !(await page.locator('.bill-line [data-key=title]').isVisible()), 'Kontowechsel setzt den Titel zurück');
  writeFileSync(join(base, 'beleg.pdf'), '%PDF-1.4 fiktiver Beleg');
  await page.setInputFiles('#bill-form input[name=file]', join(base, 'beleg.pdf'));
  await page.click('#bill-form button[type=submit]');
  check((await toast()).includes(`${billNo} erfasst`), 'Eingangsrechnung erfassen und buchen');
  const belegHref = await page.locator('main a:has-text("Beleg")').getAttribute('href');
  const beleg = await page.request.get(`http://127.0.0.1:${port}${belegHref}`);
  check(beleg.ok() && (await beleg.body()).subarray(0, 4).toString() === '%PDF', 'Beleg abrufbar');
  await shot('19-eingangsrechnungen');
  // Dieselbe Rechnung noch einmal → Warnung statt doppelter Buchung
  await page.click('[data-new-bill]');
  await page.fill('#bill-form input[name=creditor]', 'druckerei muster gmbh');
  await page.fill('#bill-form input[name=ext_number]', 're 4711');
  await page.fill('#bill-form input[name=issued]', '2026-10-20');
  await page.fill('.bill-line [data-key=description]', 'Flyer');
  await page.fill('.bill-line [data-key=amount]', '119');
  await pick('.bill-line [data-key=account]', '4210');
  await page.click('#bill-form button[type=submit]');
  check(await page.locator('#preview h2:has-text("Möglicherweise doppelt")').waitFor({ timeout: 8000 }).then(() => true, () => false), 'Doppelte Eingangsrechnung wird erkannt');
  await page.locator('#preview [data-close]').last().click();
  await dialogClose();
  check(await page.locator('main tbody tr').count() === 1, 'Keine doppelte Eingangsrechnung angelegt');
  await page.click('[data-pay]');
  await page.waitForSelector('#modal [data-show]');
  check((await page.locator('#modal .bulkbar strong').textContent()).includes('1 Überweisung'), 'Zahlungsvorschlag wählt fällige Rechnung vor');
  await page.click('#modal [data-show]');
  await page.waitForSelector('.transfer-card');
  await page.locator('.transfer-card [aria-label="IBAN kopieren"]').click();
  check((await toast()).includes('IBAN kopiert') && await page.evaluate(() => navigator.clipboard.readText()) === 'DE02120300000000202051', 'IBAN in die Zwischenablage');
  await page.locator('.transfer-card [aria-label="Betrag kopieren"]').click();
  check(await page.evaluate(() => navigator.clipboard.readText()) === '119,00', 'Betrag zum Einfügen');
  await clearToast();
  check(await page.waitForFunction(() => document.querySelector('.transfer-qr img')?.naturalWidth > 0, null, { timeout: 5000 }).then(() => true, () => false), 'GiroCode wird angezeigt');
  await shot('20-zahlungsvorschlag');
  await page.click('#modal [data-mark-all]');
  check((await toast()).includes('Als überwiesen markiert'), 'Als überwiesen markieren');
  await dialogClose();
  await page.click('[data-filter=transferred]');
  check((await page.locator('main tbody').textContent()).includes('Angewiesen'), 'Status angewiesen');
  writeFileSync(join(base, 'abbuchung.csv'), 'Datum;Betrag;Name;Verwendungszweck\n27.10.2026;-119,00;Druckerei Muster GmbH;RE-4711 Flyer\n');
  await page.click('[data-page=bank]');
  await page.click('[data-import]');
  await page.setInputFiles('#bank-file', join(base, 'abbuchung.csv'));
  await page.click('#confirm-import');
  check((await toast()).includes('1 Umsätze importiert'), 'Kontoauszug mit Abbuchung');
  await page.click('[data-filter=open]');
  await page.locator('.bank-item:has-text("Druckerei")').click();
  check(await page.locator('.suggestion:has-text("Eingangsrechnung erkannt")').waitFor({ timeout: 8000 }).then(() => true, () => false), 'Buchungsplatz erkennt die Eingangsrechnung');
  check(await page.locator('.part[data-mode=bill]').count() === 1 && (await page.locator('.part [data-bill]').inputValue()) !== '', 'Zuordnung zur Eingangsrechnung vorbelegt');
  await page.click('[data-reconcile]');
  await page.waitForSelector('#modal [data-apply]');
  check((await page.locator('#modal tbody').textContent()).includes(billNo), 'Abgleich schlägt Zuordnung vor');
  await shot('22-abgleich');
  await page.click('#modal [data-apply]');
  check((await toast()).includes('1 Kontoumsätze zugeordnet'), 'Abgleich verbucht');
  await page.click('[data-page=openitems]');
  await page.click('[data-side=receivables]');
  check((await page.locator('h1').textContent()) === 'Offene Posten' && await page.locator('main tbody tr').count() >= 1, 'Offene Posten: Forderungen');
  await shot('21-offene-posten');
  await page.click('[data-side=payables]');
  check((await page.locator('main').textContent()).includes('Keine offenen Verbindlichkeiten'), 'Eingangsrechnung nach Abgleich bezahlt');

  // Freigabe über 500 € (Finanzordnung § 7 Abs. 4) und Quartalsprüfung (§ 7 Abs. 3) – Rechnung im Vorquartal
  const now = new Date();
  const prevQuarter = new Date(now.getFullYear(), (Math.floor(now.getMonth() / 3) - 1) * 3, 15, 12);
  const prevDay = `${prevQuarter.getFullYear()}-${String(prevQuarter.getMonth() + 1).padStart(2, '0')}-15`;
  await page.click('[data-page=bills]');
  await page.click('[data-new-bill]');
  await page.fill('#bill-form input[name=creditor]', 'Saalvermietung Beispiel KG');
  await page.fill('#bill-form input[name=ext_number]', 'SV-77');
  await page.fill('#bill-form input[name=iban]', 'DE89 3704 0044 0532 0130 00');
  await page.fill('#bill-form input[name=issued]', prevDay);
  await page.fill('.bill-line [data-key=description]', 'Saalmiete Landesversammlung');
  await page.fill('.bill-line [data-key=amount]', '750');
  check(await page.locator('#bill-approval-hint').isVisible(), 'Hinweis: über 500 € Freigabe nötig');
  await pick('.bill-line [data-key=account]', '4210');
  await page.click('#bill-form button[type=submit]');
  check((await toast()).includes('erfasst'), 'Fremdrechnung über 500 € erfasst');
  await page.click('[data-filter=open]');
  check((await page.locator('main tbody').textContent()).includes('Freigabe 0/2'), 'Freigabestand 0/2');
  await page.click('[data-pay]');
  await page.waitForSelector('#modal [data-show]');
  check(await page.locator('#modal tbody tr:has-text("Saalvermietung") input[type=checkbox]').isDisabled(), 'Zahlungsvorschlag sperrt Rechnung ohne Freigabe');
  await dialogClose();
  await page.click('[data-approve]');
  await page.fill('#modal form[data-role=chair] input[name=name]', 'Vorsitz Beispiel');
  await page.click('#modal form[data-role=chair] button[type=submit]');
  check((await toast()).includes('Freigabe eingetragen'), 'Freigabe Landesvorsitz');
  await page.fill('#modal form[data-role=treasurer] input[name=name]', 'Schatz Beispiel');
  await page.selectOption('#modal form[data-role=treasurer] select[name=how]', 'schriftlich');
  await page.click('#modal form[data-role=treasurer] button[type=submit]');
  check((await toast()).includes('Freigabe eingetragen'), 'Freigabe Landesschatzmeister');
  check(await page.locator('#modal .approval-card.done').count() === 2, 'beide Freigaben dokumentiert');
  await shot('25-freigabe');
  await dialogClose();
  check((await page.locator('main tbody').textContent()).includes('Freigegeben'), 'Status freigegeben');
  await page.click('[data-review]');
  await page.waitForSelector('#review-form');
  check((await page.locator('#modal tbody').textContent()).includes('Saalvermietung'), 'Quartalsprüfung listet die Rechnung');
  const protocol = await page.request.get(new URL(await page.locator('#modal a[href*="quarter-review.pdf"]').getAttribute('href'), page.url()).href);
  check((await protocol.body()).subarray(0, 4).toString() === '%PDF', 'Prüfprotokoll als PDF');
  await page.fill('#review-form input[name=reviewer]', 'Schatz Beispiel');
  await page.selectOption('#review-form select[name=result]', 'remarks');
  await page.fill('#review-form textarea[name=remarks]', 'Beleg der Saalmiete nachreichen.');
  await shot('26-quartalspruefung');
  await page.click('#review-form button[type=submit]');
  check((await toast()).includes('protokolliert'), 'Quartalsprüfung protokolliert');
  check((await page.locator('#modal .review-list').textContent()).includes('mit Bemerkungen'), 'Protokollierte Prüfung sichtbar');
  await dialogClose();

  // E-Rechnung (XRechnung UBL) einlesen und erfassen; Auslagenerstattung mit Antrag (Finanzordnung § 8)
  const xrechnung = readFileSync(join(repo, 'tests', 'beispiel-xrechnung.xml'));
  writeFileSync(join(base, 'xrechnung.xml'), xrechnung);
  await page.click('[data-new-bill]');
  await page.setInputFiles('#einvoice-file', join(base, 'xrechnung.xml'));
  check((await toast()).includes('E-Rechnung eingelesen'), 'E-Rechnung einlesen');
  check((await page.locator('#einvoice-result').textContent()).includes('XRechnung (UBL)') && await page.inputValue('#bill-form input[name=creditor]') === 'Saalvermietung Muster e.K.'
    && await page.locator('.bill-line').count() === 2, 'Formular aus der E-Rechnung gefüllt');
  for (const line of await page.locator('.bill-line [data-key=account]').all()) await pick(line, '4210');
  await shot('32-e-rechnung');
  await page.click('#bill-form button[type=submit]');
  check((await toast()).includes('erfasst'), 'E-Rechnung als Eingangsrechnung erfasst');
  await page.click('[data-filter=all]');
  const view = await page.request.get(`http://127.0.0.1:${port}` + await page.locator('main a:has-text("E-Rechnung")').first().getAttribute('href'));
  check(view.ok() && (await view.body()).subarray(0, 4).toString() === '%PDF', 'Sichtfassung der E-Rechnung');
  await page.click('[data-new-bill]');
  await page.selectOption('#bill-form select[name=kind]', 'refund');
  check(await page.locator('#refund-fields').isVisible() && (await page.textContent('#issued-label')).includes('Ausgabe'), 'Erstattungsfelder sichtbar');
  await page.selectOption('#bill-form select[name=member_id]', { label: 'Lena Lastschrift (T-2)' });
  check(await page.inputValue('#bill-form input[name=creditor]') === 'Lena Lastschrift', 'Antragsteller als Kreditor');
  const refundForm = await page.request.get(`http://127.0.0.1:${port}` + await page.locator('#refund-form-link').getAttribute('href'));
  check((await refundForm.body()).subarray(0, 4).toString() === '%PDF', 'Antragsformular als PDF');
  await page.fill('#bill-form input[name=iban]', 'DE02 1203 0000 0000 2020 51');
  await page.fill('.bill-line [data-key=description]', 'Getränke Stammtisch');
  await page.fill('.bill-line [data-key=amount]', '23,40');
  await pick('.bill-line [data-key=account]', '4210');
  writeFileSync(join(base, 'kassenbon.jpg'), Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 0]));
  await page.setInputFiles('#bill-form input[name=file]', join(base, 'kassenbon.jpg'));
  await page.click('#bill-form button[type=submit]');
  check((await toast()).includes('unterschrieben'), 'Erstattung ohne Bestätigungen abgelehnt');
  for (const name of ['signed', 'complete', 'authorized', 'no_other']) await page.check(`#bill-form input[name=${name}]`);
  await shot('33-erstattung');
  await page.click('#bill-form button[type=submit]');
  check((await toast()).includes('erfasst'), 'Auslagenerstattung erfasst');
  check((await page.locator('main tbody').textContent()).includes('Erstattung · Antrag vom'), 'Erstattung in der Liste');

  // RPJ-Mittel (0.26): Haushalt importieren, Bescheid, Mittelabruf, Excel-Buchung auf 1592 zuordnen, Pflicht beim Verbuchen
  // (Kasse und Eingangsrechnung), Einzelübersicht mit PDF/CSV – erfundene Projekte
  writeFileSync(join(base, 'rpj.csv'), 'Jahr;Nr.;Maßnahme;Veranstaltung;Quartal;Teilnehmer;Antrag vom;a) Raummiete;b) An- und Abreisekosten;c) Verpflegungskosten;'
    + 'd) Referentenkosten;e) Marketing / Bewerbungskosten;f) Reisekosten;g) Eintritte;h) sonstige Veranstaltungskosten;i) Teilnahmebeiträge;j) Technische Ausstattung;k) Geschäftsführungskosten\n'
    + '2026;1;Podiumsdiskussion;Erfundenes Podium;Q2;30;20.12.2025;900;;400;;50;;;;;;\n2026;2;Bildungsreise;Erfundene Reise;Q3;15;20.12.2025;;1.200;;;25;;;2.400;1.125;;\n');
  await page.click('[data-page=rpj]');
  await page.waitForSelector('main [data-import]');
  check((await page.locator('main').textContent()).includes('Noch keine Projekte') && await page.locator('[data-page=rpj] .count').count() === 1, 'RPJ-Seite: Excel-Buchung auf 1592 noch ohne Zuordnung');
  await page.click('main [data-import]');
  await page.setInputFiles('#rpj-file', join(base, 'rpj.csv'));
  await page.waitForSelector('#modal [data-apply]');
  check((await page.locator('#modal').textContent()).includes('2 neu'), 'RPJ-Haushalt: Vorschau');
  await page.click('#modal [data-apply]');
  check((await toast()).includes('2 neu'), 'RPJ-Haushalt importiert');
  await page.waitForSelector('main [data-project]');
  check(await page.locator('main [data-project]').count() === 2 && (await page.locator('main .cards').textContent()).includes('3.850,00'), 'Projekte mit beantragten Mitteln');
  await page.click('main [data-year]');
  await page.fill('#rpj-year input[name=contingent]', '3.850');
  await page.click('#rpj-year button[type=submit]');
  check((await toast()).includes('Bescheid gespeichert'), 'RPJ-Bescheid');
  await page.click('main .pagehead [data-request]');
  await page.selectOption('#rpj-request select[name=quarter]', '2');
  await page.fill('#rpj-request input[name=amount]', '500');
  await page.click('#rpj-request button[type=submit]');
  check((await toast()).includes('Mittelabruf gespeichert'), 'Mittelabruf erfasst');
  await page.click('main .pagehead [data-assign]');
  await page.waitForSelector('#modal [data-select]');
  await page.check('#modal [data-select]');                                  // Auswahl → gemeinsame Zuordnung
  check(await page.locator('#modal [data-bulk]').isVisible(), 'Leiste für ausgewählte Buchungen');
  const requestOption = await page.locator('#modal [data-bulk-target] option[value^="r:"]').first().getAttribute('value');
  await page.selectOption('#modal [data-bulk-target]', requestOption);
  await page.click('#modal [data-bulk-apply]');
  check((await toast()).includes('1 RPJ-Buchung zugeordnet'), 'Excel-Buchung dem Mittelabruf zugeordnet');
  check(await page.waitForFunction(() => !document.querySelector('[data-page=rpj] .count'), null, { timeout: 5000 }).then(() => true, () => false), 'Keine offenen RPJ-Buchungen mehr');
  await dialogClose();
  // Pflicht beim Verbuchen: Kassenausgabe auf 4380 nur mit Projekt und Kostenart
  await page.click('[data-page=cash]');
  await page.click('[data-new-cash]');
  await page.fill('#cash-form input[name=amount]', '20');
  await page.fill('#cash-form input[name=text]', 'Getränke Podium');
  await page.click('#cash-form button[type=submit]');
  check((await toast()).includes('jetzt verbuchen'), 'Kassenbeleg für RPJ erfasst');
  await page.waitForSelector('#book-form');
  await pick('.part [data-account]', '4380');
  check(await page.locator('.part [data-rpj]').isVisible() && await page.locator('#book-submit').isDisabled()
    && (await page.textContent('#difference')).includes('RPJ-Projekt wählen'), 'RPJ-Zuordnung beim Verbuchen Pflicht');
  const projectOption = await page.locator('.part [data-rpj-target] option[value^="p:"]').first().getAttribute('value');
  await page.selectOption('.part [data-rpj-target]', projectOption);
  check(await page.locator('#book-submit').isDisabled() && (await page.textContent('#difference')).includes('Kostenart'), 'Kostenart ebenfalls Pflicht');
  await page.selectOption('.part [data-rpj-category]', 'c');
  await shot('41-rpj-buchung');
  await page.click('#book-submit');
  check((await toast()).includes('verbucht'), 'Kassenausgabe mit RPJ-Projekt verbucht');
  // Eingangsrechnung mit RPJ-Position
  await page.click('[data-page=bills]');
  await page.click('[data-new-bill]');
  await page.fill('#bill-form input[name=creditor]', 'Tagungshaus Beispiel');
  await page.fill('#bill-form input[name=ext_number]', 'TH-2026-1');
  await page.fill('#bill-form input[name=iban]', 'DE89 3704 0044 0532 0130 00');
  await page.fill('#bill-form input[name=issued]', '2026-10-21');
  await page.fill('.bill-line [data-key=description]', 'Saalmiete Podium');
  await page.fill('.bill-line [data-key=amount]', '300');
  await pick('.bill-line [data-key=account]', '4380');
  check(await page.locator('.bill-line [data-rpj]').isVisible(), 'RPJ-Feld in der Eingangsrechnung');
  await page.selectOption('.bill-line [data-rpj-target]', projectOption);
  await page.selectOption('.bill-line [data-rpj-category]', 'a');
  await page.click('#bill-form button[type=submit]');
  check((await toast()).includes('erfasst'), 'Eingangsrechnung mit RPJ-Projekt erfasst');
  // Einzelübersicht, PDF und CSV
  await page.click('[data-page=rpj]');
  await page.waitForSelector('main [data-project]');
  await shot('39-rpj');
  await page.locator('main [data-project]').first().click();
  await page.waitForSelector('#modal [data-edit]');
  const projectText = await page.locator('#modal').textContent();
  check(projectText.includes('Kasse') && projectText.includes('Eingangsrechnung') && projectText.includes('320,00'), 'Einzelübersicht mit Kasse und Eingangsrechnung');
  await shot('40-rpj-projekt');
  await page.locator('#modal tr:has-text("Getränke Podium") [data-reassign]').click();   // Zuordnung einer Buchung ändern
  await page.waitForSelector('#rpj-assign');
  await page.selectOption('#rpj-assign [data-category]', 'e');
  await page.click('#rpj-assign button[type=submit]');
  check((await toast()).includes('RPJ-Zuordnung gespeichert'), 'Zuordnung aus der Einzelübersicht geändert');
  await page.waitForFunction(() => (document.querySelector('#modal')?.textContent || '').includes('e) Marketing'), null, { timeout: 5000 });
  const projectPdf = await page.request.get(new URL(await page.locator('#modal a[href^="/api/rpj-projekt.pdf"]').getAttribute('href'), page.url()).href);
  check((await projectPdf.body()).subarray(0, 4).toString() === '%PDF', 'Einzelübersicht als PDF');
  const [projectCsv] = await Promise.all([page.waitForEvent('download'), page.click('#modal [data-csv]')]);
  check(projectCsv.suggestedFilename() === 'RPJ-2026-Projekt-01-Buchungen.csv' && readFileSync(await projectCsv.path(), 'utf8').includes('e) Marketing'), 'Buchungen des Projekts als CSV');
  await dialogClose();
  const yearPdf = await page.request.get(new URL(await page.locator('main a[href^="/api/rpj.pdf"]').getAttribute('href'), page.url()).href);
  check((await yearPdf.body()).subarray(0, 4).toString() === '%PDF', 'RPJ-Jahresübersicht als PDF');
  const [yearCsv] = await Promise.all([page.waitForEvent('download'), page.click('main [data-csv]')]);
  check(yearCsv.suggestedFilename() === 'RPJ-2026.csv' && readFileSync(await yearCsv.path(), 'utf8').includes('Erfundene Reise'), 'RPJ-Jahresübersicht als CSV');
  check((await page.locator('main .cards').textContent()).includes('500,00'), 'Eingang des Mittelabrufs in den Kennzahlen');

  // Start: Aufgaben je Bereich, Kennzahlen; Kreisverbandsseite über die Budgets
  await page.click('[data-page=overview]');
  check(await page.locator('main .cards .card').count() === 4, 'Kennzahlen');
  check(await page.locator('.task-card').count() === 4 && await page.locator('.task-row').count() >= 4, 'Aufgaben je Bereich');
  await shot('04-start-aufgaben');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.click('#menu-toggle');
  await page.click('[data-page=overview]');
  await page.waitForTimeout(400);
  check(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) <= 0 && await page.evaluate(() => [...document.querySelectorAll('.task-card h3')].sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top).map((h) => h.textContent).join('|')) === 'Mitgliederverwaltung|Kreisverbände und Haushalt|Buchhaltung|Verwaltung', 'Start auf dem Handy: eine Spalte ohne seitliches Scrollen, Reihenfolge wie im Dokument');
  await shot('04b-start-handy');
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.locator('.task-row:has-text("KV-Budgets")').click();
  await page.locator('[data-kv-open]').first().click();
  check(await page.waitForFunction(() => (document.querySelector('main')?.textContent || '').includes('Budgetkonto 2026'), null, { timeout: 6000 })
    .then(() => true, () => false), 'Seite des Kreisverbands aus der Übersicht');
  check((await page.locator('main').textContent()).includes('Beitragseingänge'), 'KV-Seite zeigt Beitragseingänge');
  await shot('30-kv-seite');
  await page.click('[data-page=overview]');
  await page.click('[data-monthly]');
  await page.waitForSelector('#modal select[name=month]');
  const month = await page.locator('#modal select[name=month] option').last().getAttribute('value');
  await page.selectOption('#modal select[name=month]', month);
  const monthly = await page.request.get(`http://127.0.0.1:${port}` + await page.locator('#modal [data-open]').getAttribute('href'));
  check(monthly.ok() && (await monthly.body()).subarray(0, 4).toString() === '%PDF' && monthly.headers()['content-disposition'].includes(`Monatsbericht-${month}`), 'Monatsbericht für einen gewählten Monat');
  await dialogClose();
  const [pkg] = await Promise.all([page.waitForEvent('download'), page.click('[data-audit-package]')]);
  const pkgBytes = readFileSync(await pkg.path());
  check(pkg.suggestedFilename() === 'Kassenpruefung-2026.zip' && pkgBytes.subarray(0, 2).toString() === 'PK', 'Kassenprüfer-Paket herunterladen');
  check((await toast()).includes('Kassenprüfer-Paket 2026'), 'Hinweis zum Kassenprüfer-Paket');

  // Haushalt
  await page.click('[data-page=budgets]');
  await page.click('[data-mode=household]');                                  // zuletzt war die Kreisverbandsseite offen
  check(await page.locator('.hh-table tbody tr').count() > 40, 'Haushaltstabelle');
  await page.locator('.plan-cell[data-plan=H4][data-year="2026"]').click();
  await page.fill('#plan-form input', '50');
  await page.click('#plan-form button[type=submit]');
  check((await toast()).includes('Planwert'), 'Planwert bearbeiten');
  await page.locator('.plan-cell[data-plan=H27][data-year="2026"]').click();
  await page.fill('#plan-form input', '-10');
  await page.click('#plan-form button[type=submit]');
  await toast();
  check((await page.locator('.notice.warn').first().textContent()).includes('Titelüberschreitung'), '10-%-Hinweis bei Überschreitung');
  await page.click('[data-dev="1"]');
  const deviationRows = await page.locator('.hh-table tbody tr').count();
  check(deviationRows > 0 && deviationRows < 20, 'Filter „Nur Abweichungen“');
  await page.click('[data-dev="0"]');
  await shot('05-haushalt');
  await page.click('[data-reference]');
  check(await page.waitForFunction(() => (document.querySelector('#modal h2')?.textContent || '').includes('Excel-Plan'), null, { timeout: 5000 }).then(() => true, () => false), 'Excel-Plan-Dialog');
  await dialogClose();
  await page.click('[data-mode=kv]');
  check(await page.locator('.kv-table [data-kv-open]').count() === 8, 'Kreisverbände als Tabelle');
  await page.locator('[data-kv-values]').first().click();
  await page.fill('#kv-values-form input[name="plan-5100"]', '600');
  await page.fill('#kv-values-form input[name="ffk-5100"]', '150');
  await page.fill('#kv-values-form input[name="dues-5800"]', '350');
  await shot('34-kv-werte');
  await page.click('#kv-values-form button[type=submit]');
  check((await toast()).includes('Werte gespeichert'), 'Werte festlegen (Plan, FFK, Budget Schüler Union)');
  const altonaRow = await page.locator('.kv-table tr:has-text("Altona")').textContent();
  check(altonaRow.includes('600,00') && altonaRow.includes('150,00'), 'Plan und FFK in der Tabelle');
  // Abgleich mit der Masterdatei (erfundene Arbeitsmappe; KV-Budgets beginnen hier 2026 → Hinweis, nichts zu übernehmen)
  await page.click('[data-kv-master]');
  await page.setInputFiles('#kv-master-file', kvMasterPath);
  await page.waitForSelector('#kv-master-preview h3');
  const kvPreview = await page.locator('#kv-master-preview').textContent();
  check(kvPreview.includes('ab Haushaltsjahr') && kvPreview.includes('KV Altona'), 'Abgleich Masterdatei: Vorschau mit Hinweis zum Beginn');
  await shot('35-kv-abgleich');
  await dialogClose();
  // Budget-Umbuchung: KV Mitte beteiligt sich an Kosten von KV Nord (gemeinsame Feier)
  await page.click('[data-transfer-new]');
  await page.selectOption('#transfer-form select[name=to]', '5500');
  await page.selectOption('#transfer-form select[name=from]', '5600');
  await page.fill('#transfer-form input[name=amount]', '25');
  await page.fill('#transfer-form input[name=reason]', 'Beteiligung gemeinsames Sommerfest');
  check((await page.textContent('#transfer-effect')).includes('KV Mitte wird belastet'), 'Umbuchung: Wirkung angezeigt');
  await shot('33-budget-umbuchung');
  await page.click('#transfer-form button[type=submit]');
  check((await toast()).includes('umgebucht'), 'Budget umgebucht');
  check(await page.locator('main tr:has-text("Beteiligung gemeinsames Sommerfest")').count() === 1, 'Umbuchung in der Liste');
  await page.click('[data-transfer-reverse]');
  await page.fill('#transfer-reverse input[name=reason]', 'Testlauf');
  await page.click('#transfer-reverse button[type=submit]');
  check((await toast()).includes('storniert'), 'Umbuchung storniert');
  await shot('06-kreisverbaende');
  await page.fill('#kv-search', 'alt');
  check(await page.locator('.kv-table [data-kv-open]').count() === 1, 'KV-Suche');
  await page.locator('.kv-table [data-kv-open]').first().click();
  await page.waitForSelector('[data-kv-back]');
  check((await page.locator('main .cards').textContent()).includes('600,00'), 'KV-Seite mit Plan');
  check((await page.locator('main .cards').textContent()).includes('Freundes- und Förderkreis'), 'FFK als eigener Punkt');
  const kvPdf = await page.request.get(`http://127.0.0.1:${port}` + await page.locator('main a[href^="/api/kv.pdf"]').getAttribute('href'));
  check(kvPdf.ok() && (await kvPdf.body()).subarray(0, 4).toString() === '%PDF', 'PDF für den Kreisvorsitz');
  if (await page.locator('[data-kv-label]').count()) {
    await page.locator('[data-kv-label]').first().click();
    await page.fill('#kv-label-form input[name=label]', 'Getränke Stammtisch');
    await page.click('#kv-label-form button[type=submit]');
    check((await toast()).includes('Bezeichnung gespeichert'), 'Bezeichnung im KV-Budget ändern');
    check(await page.waitForFunction(() => (document.querySelector('.kv-statement')?.textContent || '').includes('Getränke Stammtisch'), null, { timeout: 6000 })
      .then(() => true, () => false), 'Bezeichnung im Budgetkonto');
  }
  await shot('31-kv-budgetkonto');
  await page.click('[data-kv-back]');
  await page.fill('#kv-search', '');
  // Haushalt: derselbe Planwert in der Zeile des Kreisverbands (nur ein Planwert)
  await page.click('[data-mode=household]');
  check((await page.locator('.hh-table tr:has-text("KV Altona")').textContent()).includes('-600,00'), 'KV-Plan steht im Gesamthaushalt');
  await page.click('[data-mode=kv]');
  await page.click('[data-mode=donations]');
  await page.waitForSelector('[data-cash]');
  await page.click('[data-cash]');
  const old = new Date(Date.now() - 40 * 86400000).toISOString().slice(0, 10);
  await page.fill('#cash-form input[name=received_on]', old);
  await page.fill('#cash-form input[name=amount]', '50');
  await page.fill('#cash-form input[name=donor]', 'Max Spender');
  await page.fill('#cash-form input[name=receiver1]', 'Vorsitz Beispiel');
  await page.fill('#cash-form input[name=receiver2]', 'vorsitz beispiel');
  await page.fill('#cash-form input[name=receipt]', 'Q-2026-001');
  await page.click('#cash-form button[type=submit]');
  check((await toast()).includes('zwei verschiedene Vertreter'), 'Barspende: zwei verschiedene Vertreter nötig');
  await page.fill('#cash-form input[name=receiver2]', 'Schatz Beispiel');
  await page.click('#cash-form button[type=submit]');
  check((await toast()).includes('Barspende gespeichert'), 'Barspende erfassen');
  await page.waitForSelector('main tr:has-text("Max Spender")');
  check((await page.locator('main tr:has-text("Max Spender")').textContent()).includes('Einzahlung überfällig'), 'Barspende: 30-Tage-Frist überwacht');
  await shot('31-spenden');
  await page.click('[data-page=overview]');
  check((await page.locator('.task-grid').textContent()).includes('Barspenden einzahlen'), 'Frist Barspende auf der Startseite');

  // Konten: alle Konten des Kontenrahmens, Kontenblatt mit Anfangsbestand, CSV
  await page.click('[data-page=konten]');
  await page.waitForSelector('.konten-table');
  check(await page.locator('.konten-table [data-konto]').count() > 140, 'Alle Konten des Kontenrahmens');
  check((await page.locator('.konten-table').textContent()).includes('Klasse 0 · Anlagevermögen'), 'Gliederung nach Kontenklassen');
  await page.fill('#konten-search', '1420');
  await page.locator('[data-konto="1420"]').click();
  await page.waitForSelector('[data-back]');
  await page.waitForFunction(() => document.querySelector('main .cards'), null, { timeout: 5000 });
  check((await page.locator('main .cards').textContent()).includes('Anfangsbestand') && await page.locator('main tbody tr').count() > 3, 'Kontenblatt Bank mit Anfangsbestand');
  await shot('36-kontenblatt');
  const [kontoCsv] = await Promise.all([page.waitForEvent('download'), page.click('[data-csv]')]);
  check(kontoCsv.suggestedFilename() === 'Konto-1420-2026.csv' && readFileSync(await kontoCsv.path(), 'utf8').includes('Saldo (Soll − Haben)'), 'Kontenblatt als CSV');
  await page.click('[data-back]');
  await page.fill('#konten-search', '');

  // Journal
  await page.click('[data-page=journal]');
  check(await page.locator('tbody tr').count() > 5, 'Journal');
  await page.fill('#journal-search', 'Storno');
  check(await page.locator('tbody tr').first().textContent().then((t) => t.includes('Storno')), 'Journal-Suche');
  await shot('11-journal');

  // Druckansicht ohne Fehler
  await page.click('[data-page=budgets]');
  await page.click('[data-mode=household]');
  await page.emulateMedia({ media: 'print' });
  await page.screenshot({ path: join(shots, '07-druck.png') });
  check(await page.locator('.pagehead').isHidden() && await page.locator('.print-head').isVisible(), 'Druck: eigener Kopf statt Seitentitel');
  await page.emulateMedia({ media: 'screen' });

  // 0.32: Karte – Adressdaten laden (erfunden, tests/ui_start.py), Mitglieder auf der Karte, Listen „zu prüfen“, Adressprüfung im Mitglied
  await page.click('[data-page=overview]');
  const addressTask = page.locator('.task-row:has-text("Amtliche Adressdaten laden")');
  check(await addressTask.count() === 1, 'Start weist auf fehlende Adressdaten hin');
  await addressTask.click();
  await page.waitForSelector('main [data-load]');
  check((await page.locator('h1').textContent()) === 'Karte' && (await page.locator('main .map-missing').textContent()).includes('keine Mitgliederadresse'), 'Karte ohne Adressdaten erklärt den Abruf');
  await shot('40-karte-ohne-daten');
  await page.click('main [data-load]');
  await page.waitForSelector('main .map-canvas.leaflet-container', { timeout: 20000 });
  await page.waitForTimeout(800);
  check(await page.locator('main .map-legend [data-kv]').count() === 7, 'Karte: sieben Kreisverbände in der Legende');
  const onMap = Number((await page.locator('main .map-toolbar .small').textContent()).match(/^(\d+) von/)?.[1] || 0);
  check(onMap >= 2, `Karte: Mitglieder an ihrer Anschrift (${onMap})`);
  check(tiles.length > 0, 'Kartenhintergrund: nur Kartenbilder von OpenStreetMap angefragt');
  check(await page.locator('main .leaflet-tooltip.map-area-label').count() === 7, 'Namen der Kreisverbände stehen auf der Karte');
  await shot('41-karte');
  const fremd = Number(await page.locator('main [data-check=fremd] .map-check-count').textContent());
  const unknown = Number(await page.locator('main [data-check=ausserhalb] .map-check-count').textContent());
  check(fremd >= 1 && unknown >= 1, `Zu prüfen: Wohnort in anderem Bezirk (${fremd}), außerhalb Hamburgs (${unknown})`);
  await page.click('main [data-check=ausserhalb]');
  await page.waitForSelector('#modal [data-edit]');
  check((await page.locator('#modal table').textContent()).includes('außerhalb Hamburgs'), 'Liste „Wohnt außerhalb Hamburgs“ mit Hinweis');
  await shot('42-karte-pruefen');
  await page.locator('#modal [data-edit]').first().click();
  await page.waitForSelector('#member-form [data-address-check]');
  await page.fill('#member-form input[name=street]', 'Musterwegg 3, 2. OG');
  await page.fill('#member-form input[name=zip]', '20000');
  await page.fill('#member-form input[name=city]', 'Hamburg');
  await page.fill('#member-form input[name=country]', '');
  await page.locator('#member-form input[name=street]').focus();
  await page.waitForSelector('#member-form [data-suggestion]', { timeout: 6000 }).catch(() => {});
  check((await page.locator('#member-form [data-address-check]').textContent()).includes('gemeint ist wohl'), 'Mitglied: Tippfehler in der Straße wird gemeldet');
  await page.locator('#member-form [data-suggestion]').first().click();
  await page.waitForFunction(() => document.querySelector('#member-form [data-address-check]')?.textContent.includes('Adresse gefunden'), null, { timeout: 6000 }).catch(() => {});
  check(await page.inputValue('#member-form input[name=street]') === 'Musterweg 3, 2. OG' && (await page.locator('#member-form [data-address-check]').textContent()).includes('Adresse gefunden'),
    'Mitglied: Vorschlag ersetzt nur den Straßennamen (Hausnummer und Zusatz bleiben), Adresse gefunden');
  await shot('43-mitglied-adresspruefung');
  await page.click('#member-form button[type=submit]');
  await page.waitForSelector('main .map-canvas.leaflet-container', { timeout: 10000 });
  await page.waitForFunction((n) => Number(document.querySelector('main [data-check=ausserhalb] .map-check-count')?.textContent) === n - 1, unknown, { timeout: 8000 }).catch(() => {});
  check(Number(await page.locator('main [data-check=ausserhalb] .map-check-count').textContent()) === unknown - 1
    && Number((await page.locator('main .map-toolbar .small').textContent()).match(/^(\d+) von/)?.[1] || 0) === onMap + 1, 'Nach dem Berichtigen steht das Mitglied auf der Karte');
  await page.click('main [data-mode=density]');
  check(await page.locator('main .map-scale span').count() === 5, 'Dichte je Stadtteil mit Farbstufen');
  await shot('44-karte-dichte');
  await page.click('main [data-mode=members]');
  await page.locator('main .map-legend [data-kv]').first().click();
  check(await page.locator('main .map-legend [data-kv][aria-pressed=true]').count() === 1, 'Filter auf einen Kreisverband');
  await page.locator('main .map-legend [data-kv][aria-pressed=true]').click();
  await page.fill('main [data-search]', 'musterw');
  await page.waitForSelector('main .map-result', { timeout: 6000 }).catch(() => {});
  check(await page.locator('main .map-result[data-street]').count() >= 1 && await page.locator('main .map-result[data-member]').count() >= 1, 'Suche findet Straße und Mitglied');
  await page.locator('main .map-result[data-street]').first().click();
  // Prüfbefund 0.32: mitten im Zoomen die Seite wechseln und zurück – genau eine Karte, keine Reste, keine Fehler
  for (let i = 0; i < 3; i++) {
    await page.click('[data-page=members]');
    await page.click('[data-page=map]');
    await page.waitForSelector('main .map-canvas.leaflet-container');
  }
  await page.waitForTimeout(500);
  check(await page.evaluate(() => document.querySelectorAll('.leaflet-container').length) === 1 && await page.locator('main .leaflet-control-zoom-in').getAttribute('title') === 'Vergrößern',
    'Seitenwechsel: genau eine Karte, Zoom-Knöpfe auf Deutsch');
  // CI-Befund 19.11.2026 (clearRect): Karte neu zeichnen und noch vor dem nächsten Bild verlassen – nichts zeichnet danach auf der abgebauten Karte
  const beforeLeave = errors.length;
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => { document.querySelector('main [data-mode=members]').click(); document.querySelector('[data-page=members]').click(); });
    await page.click('[data-page=map]');
    await page.waitForSelector('main .map-canvas.leaflet-container');
  }
  await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
  check(errors.length === beforeLeave, 'Karte neu zeichnen und sofort verlassen: kein Zeichnen auf der abgebauten Karte');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.click('#menu-toggle');
  await page.click('[data-page=map]');
  await page.waitForSelector('main .map-canvas.leaflet-container');
  await page.waitForTimeout(400);
  check(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) <= 0, 'Karte auf dem Handy ohne seitliches Scrollen');
  await shot('45-karte-handy');
  await page.setViewportSize({ width: 1440, height: 900 });
  // Schalter: Kartenhintergrund aus – dann geht für die Karte gar nichts mehr ins Netz
  await page.click('[data-page=settings]');
  await page.click('[data-tab=schalter]').catch(() => {});
  await page.waitForFunction(() => /Adressen in/.test(document.querySelector('main [data-address-state]')?.textContent || ''), null, { timeout: 6000 }).catch(() => {});
  check(/\d+ Adressen in \d+ Straßen/.test(await page.locator('main [data-address-state]').textContent()), 'Einstellungen zeigen den Stand der Adressdaten');
  await page.uncheck('main .switch input[name=map_tiles]');
  await page.click('main form[data-save] button[type=submit]');
  await toast();
  const before = tiles.length;
  await page.click('[data-page=map]');
  await page.waitForSelector('main .map-canvas.map-plain');
  await page.waitForTimeout(600);
  check(tiles.length === before && (await page.locator('main .map-side').textContent()).includes('Kartenhintergrund ist ausgeschaltet'), 'Schalter aus: keine Kartenbilder aus dem Netz');
  await shot('46-karte-ohne-hintergrund');
  check(outside.length === 0, 'Keine Anfrage an fremde Adressen' + (outside.length ? ': ' + outside.slice(0, 3).join(' | ') : ''));

  // Kleiner Bildschirm
  await page.setViewportSize({ width: 390, height: 844 });
  await page.click('#menu-toggle');                                          // Seitenleiste ist auf dem Handy ein Menü
  await page.click('[data-page=bank]');
  await shot('08-mobil');
  await page.setViewportSize({ width: 1440, height: 900 });

  // Abmelden und wieder anmelden
  await page.click('#logout');
  await page.waitForSelector('.login');
  await shot('13-anmeldung');
  await login();
  check(true, 'Abmelden und Anmelden');

  // 6 bisherige + doppelte OV-Nummer + geschützte Datei (Kennwort fehlt, Kennwort falsch)
  const expected = 7 + (existsSync(lockedPath) ? 2 : 0);
  check(rejected === expected, `Nur die ${expected} absichtlich falschen Eingaben abgelehnt (${rejected})`);
  check(errors.length === 0, 'Keine Fehler in der Browser-Konsole' + (errors.length ? ': ' + errors.join(' | ') : ''));
} catch (e) {
  failures.push(e.message);
  console.log('ABBRUCH ' + e.stack);
} finally {
  clearInterval(confirmTimer);
  await browser?.close();
  server.kill();
  smtp.kill();
}
console.log(failures.length ? `\n${failures.length} Fehler` : '\nAlle Prüfungen bestanden');
process.exit(failures.length ? 1 : 0);
