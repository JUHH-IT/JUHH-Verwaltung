// Browser-Durchlauf der Anmeldeseiten von Keycloak (Theme ju-hamburg, ab 0.49) gegen die laufende Probe:
// folgt die Seite der Darstellung, die in der Anwendung gewählt wurde (localStorage, gleiche Adresse)? Klassisch, Glas hell,
// Glas dunkel, Glas automatisch (Gerät dunkel) – Laptop und Handy ohne seitliches Scrollen, Kontrast der Texte.
// Mit einer erfundenen Person (angelegt und am Ende gelöscht, Passwörter nur zufällig und nie ausgegeben): falsches Passwort,
// neues Passwort festlegen, Authenticator-App einrichten. Die Seiten kommen über die öffentliche Adresse der Probe
// (KC_PROBE_ADRESSE) und werden im Browser auf 127.0.0.1:8180 umgeleitet – nur /auth/realms/ju-hamburg und /auth/resources wie Tailscale
// Serve; jede andere Anfrage ist ein Fehler. Dazu zweite Anmeldung mit Code aus der App und die Abmelden-Seite.
// Aufruf: sudo tests/keycloak_probe.sh start && sudo NODE_PATH=$(npm root -g) node tests/keycloak_seiten.mjs [Ordner für Bildschirmfotos]
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { createHmac, randomBytes } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const { chromium } = createRequire(import.meta.url)('playwright');
const shots = process.argv[2] || join(tmpdir(), 'ju-keycloak-shots');
mkdirSync(shots, { recursive: true });
const adresse = process.env.KC_PROBE_ADRESSE || 'https://srv1.example.ts.net';
const probe = process.env.KC_PROBE_DIR || join(process.env.TMPDIR || '/tmp', 'ju-keycloak-probe');
const intern = 'http://127.0.0.1:8180';
const realm = `${adresse}/auth/realms/ju-hamburg`;
const rueckkehr = `${adresse}/api/keycloak/rueckkehr`;
const anmelden = (redirect = rueckkehr, extra = {}) => `${realm}/protocol/openid-connect/auth?${new URLSearchParams({ client_id: 'ju-verwaltung',
  response_type: 'code', scope: 'openid', redirect_uri: redirect, state: 'probe', code_challenge: 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM',
  code_challenge_method: 'S256', ...extra })}`;
const failures = [];
const check = (ok, message) => { if (!ok) failures.push(message); console.log((ok ? 'ok   ' : 'FEHLER ') + message); };
const zufall = () => `${randomBytes(18).toString('base64url')}-Aa1`;
/** Code der Authenticator-App (RFC 6238, SHA-1, 6 Stellen, 30 s) zum Schlüssel, den Keycloak beim Einrichten zeigt; `schritt` = Zeitfenster
 *  später (Keycloak nimmt ±1 Fenster an und lehnt einen schon benutzten Code ab). */
function totp(schluessel, schritt = 0) {
  const bits = [...schluessel.replace(/[\s=]/g, '').toUpperCase()].map((c) => 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'.indexOf(c).toString(2).padStart(5, '0')).join('');
  const zaehler = Buffer.alloc(8);
  zaehler.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + schritt));
  const h = createHmac('sha1', Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)))).update(zaehler).digest();
  return String((h.readUInt32BE(h[19] & 15) & 0x7fffffff) % 1e6).padStart(6, '0');
}

const DARSTELLUNGEN = [   // [Name, gespeicherte Wahl, Farbschema des Geräts, erwartet data-design, erwartet data-farbe]
  ['klassisch', null, 'light', undefined, undefined],
  ['glas-hell', { design: 'glas', farbe: 'hell' }, 'dark', 'glas', 'hell'],
  ['glas-dunkel', { design: 'glas', farbe: 'dunkel' }, 'light', 'glas', 'dunkel'],
  ['glas-auto', { design: 'glas', farbe: 'auto' }, 'dark', 'glas', 'dunkel'],
];

// Admin-API der Probe (nur 127.0.0.1) für die erfundene Person
const admin = (() => {
  let env = '';
  try { env = readFileSync(join(probe, 'keycloak-admin.env'), 'utf8'); } catch { return null; }
  const wert = (k) => (env.match(new RegExp(`^${k}=(.*)$`, 'm')) || [])[1];
  return { name: wert('KC_BOOTSTRAP_ADMIN_USERNAME'), passwort: wert('KC_BOOTSTRAP_ADMIN_PASSWORD') };
})();
async function adminToken() {
  const r = await fetch(`${intern}/auth/realms/master/protocol/openid-connect/token`, { method: 'POST',
    body: new URLSearchParams({ grant_type: 'password', client_id: 'admin-cli', username: admin.name, password: admin.passwort }) });
  if (!r.ok) throw new Error(`Admin-Anmeldung an der Probe: Status ${r.status}`);
  return (await r.json()).access_token;
}
const PERSON = 'probe-darstellung';
async function person(token, passwort) {   // löschen, falls vorhanden, und neu anlegen: Startpasswort, neues Passwort und App verlangt
  const api = (pfad, init = {}) => fetch(`${intern}/auth/admin/realms/ju-hamburg${pfad}`,
    { ...init, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } });
  for (const u of await (await api(`/users?exact=true&username=${PERSON}`)).json()) await api(`/users/${u.id}`, { method: 'DELETE' });
  if (passwort === null) return;
  const r = await api('/users', { method: 'POST', body: JSON.stringify({ username: PERSON, enabled: true, firstName: 'Erika', lastName: 'Probe',
    email: 'probe-darstellung@example.org', emailVerified: true, requiredActions: ['UPDATE_PASSWORD', 'CONFIGURE_TOTP'],
    credentials: [{ type: 'password', value: passwort, temporary: true }] }) });
  if (r.status !== 201) throw new Error(`Person anlegen: Status ${r.status}`);
}

const browser = await chromium.launch();
const nachAussen = [];
async function kontext(wahl, farbschema, viewport) {
  const context = await browser.newContext({ viewport, colorScheme: farbschema, locale: 'de-DE' });
  if (wahl) await context.addInitScript((w) => { try { localStorage.setItem('ju-finanzen-darstellung', w); } catch { /* sandbox */ } }, JSON.stringify(wahl));
  await context.route('**/*', async (route) => {
    const url = route.request().url();
    if (url.startsWith(`${adresse}/auth/realms/ju-hamburg/`) || url.startsWith(`${adresse}/auth/resources/`)) {   // nur diese veröffentlicht Tailscale Serve
      const antwort = await route.fetch({ url: intern + url.slice(adresse.length), maxRedirects: 0 });
      const ziel = antwort.status() >= 300 && antwort.status() < 400 && antwort.headers().location;
      // shortcut: Weiterleitungen folgt der Browser an route vorbei – daher als Seite mit „refresh“ (Cookies bleiben); bei Bedarf TLS-Proxy
      if (ziel) return route.fulfill({ response: antwort, status: 200, contentType: 'text/html',
        body: `<meta http-equiv="refresh" content="0;url=${ziel.replace(/&/g, '&amp;').replace(/"/g, '&quot;')}">` });
      return route.fulfill({ response: antwort });
    }
    if (url.startsWith(rueckkehr)) return route.fulfill({ status: 200, contentType: 'text/html', body: '<p>Rückkehr zur Anwendung</p>' });
    nachAussen.push(url);
    return route.abort();
  });
  return context;
}

/** Zustand der Seite: Darstellung, Unschärfe der Karte, Kontrast der wichtigsten Texte, seitliches Scrollen. */
const zustand = (page) => page.evaluate(() => {
  const rgba = (s) => { const m = s.match(/[\d.]+/g) || [0, 0, 0, 0]; return m.length === 3 ? [...m.map(Number), 1] : m.map(Number); };
  const ueber = (o, u) => [0, 1, 2].map((i) => o[i] * o[3] + u[i] * (1 - o[3])).concat(1);
  const grund = (el) => {   // Hintergrund hinter el: durchscheinende Flächen auf den Grund der Seite gelegt (Verläufe zählen nicht)
    const schichten = [];
    for (let e = el; e; e = e.parentElement) schichten.push(rgba(getComputedStyle(e).backgroundColor));
    return schichten.reverse().reduce((u, o) => ueber(o, u), [255, 255, 255, 1]);
  };
  const lum = (c) => { const [r, g, b] = c.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const kontrast = (el) => { const a = lum(ueber(rgba(getComputedStyle(el).color), grund(el))), b = lum(grund(el));
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); };
  const texte = ['#kc-page-title', '.pf-v5-c-form__label-text', '.pf-v5-c-alert__title', '.instruction', '#kc-error-message p', '#kc-totp-settings li',
    '.kc-feedback-text', '.pf-v5-c-check__label'];
  const karte = document.querySelector('.pf-v5-c-login__main');
  return {
    design: document.documentElement.dataset.design, farbe: document.documentElement.dataset.farbe,
    unschaerfe: karte ? getComputedStyle(karte).backdropFilter : '',
    titelHell: lum(rgba(getComputedStyle(document.querySelector('#kc-page-title') || document.body).color)) > 0.5,
    kontraste: texte.flatMap((s) => [...document.querySelectorAll(s)].slice(0, 1).map((el) => [s, Math.round(kontrast(el) * 10) / 10])),
    seitlich: document.documentElement.scrollWidth - window.innerWidth,
  };
});

async function pruefe(page, name, erwartet, datei) {
  const z = await zustand(page);
  check(z.design === erwartet[0] && z.farbe === erwartet[1], `${name}: Darstellung ${erwartet[0] || 'klassisch'}${erwartet[1] ? ' ' + erwartet[1] : ''} (${z.design || '–'} ${z.farbe || ''})`);
  check(erwartet[0] === 'glas' ? /blur/.test(z.unschaerfe) : !/blur/.test(z.unschaerfe), `${name}: Karte ${erwartet[0] === 'glas' ? 'aus Glas (Unschärfe)' : 'ohne Unschärfe'}`);
  check(z.titelHell === (erwartet[1] === 'dunkel'), `${name}: Titel ${erwartet[1] === 'dunkel' ? 'hell auf dunkel' : 'dunkel auf hell'}`);
  const schwach = z.kontraste.filter(([, k]) => k < 4.5);
  check(!schwach.length, `${name}: Kontrast der Texte mindestens 4,5 : 1${schwach.length ? ' – ' + schwach.map(([s, k]) => `${s} ${k}`).join(', ') : ''}`);
  check(z.seitlich <= 0, `${name}: kein seitliches Scrollen`);
  await page.screenshot({ path: join(shots, `${datei}.png`), fullPage: true });
}

const LAPTOP = { width: 1280, height: 860 };
const HANDY = { width: 390, height: 844 };
let token = null;   // nur als Zeichen, dass die Admin-API erreichbar ist – jede Verwendung holt einen frischen (er lebt 60 s)
try {
  if (admin?.passwort) token = await adminToken();
  else console.log('Hinweis: keine Admin-Daten der Probe lesbar – nur Seiten ohne Anmeldung (als root ausführen).');
  let nr = 0;
  for (const [name, wahl, farbschema, design, farbe] of DARSTELLUNGEN) {
    const erwartet = [design, farbe];
    for (const [geraet, viewport] of [['laptop', LAPTOP], ['handy', HANDY]]) {
      const context = await kontext(wahl, farbschema, viewport);
      const page = await context.newPage();
      const n = String(++nr).padStart(2, '0');
      const r = await page.goto(anmelden());
      check(r?.status() === 200 && await page.locator('#kc-form-login').count() === 1, `${name} ${geraet}: Anmeldeseite`);
      await pruefe(page, `${name} ${geraet} Anmeldung`, erwartet, `k${n}-${name}-${geraet}-anmeldung`);
      if (geraet === 'laptop') {
        await page.goto(anmelden(`${adresse}/fremd`));
        check(await page.locator('#kc-error-message').count() === 1, `${name}: Fehlerseite (fremde Rückkehradresse)`);
        await pruefe(page, `${name} Fehlerseite`, erwartet, `k${n}-${name}-fehlerseite`);
      }
      if (token && (geraet === 'laptop' || name === 'glas-dunkel')) {   // mit Person: falsches Passwort, neues Passwort, App einrichten
        const start = zufall();
        await person(await adminToken(), start);
        await page.goto(anmelden());
        await page.fill('#username', PERSON);
        await page.fill('#password', zufall());
        await page.click('#kc-login');
        await page.waitForSelector('.kc-feedback-text, .pf-v5-c-alert');   // „Benutzername oder Passwort stimmt nicht.“ am Feld
        await pruefe(page, `${name} ${geraet} falsches Passwort`, erwartet, `k${n}-${name}-${geraet}-fehlanmeldung`);
        await page.fill('#password', start);
        await page.click('#kc-login');
        let geheim = '';
        let passwort = start;
        for (let schritt = 0; schritt < 2; schritt++) {   // Pflichtschritte in der Reihenfolge von Keycloak: App einrichten, neues Passwort
          await page.waitForSelector('#kc-totp-settings, #password-new');
          if (await page.locator('#kc-totp-settings').count()) {
            await page.locator('.kc-qr-code img, #kc-totp-secret-qr-code').first().waitFor();
            await pruefe(page, `${name} ${geraet} Authenticator-App`, erwartet, `k${n}-${name}-${geraet}-app`);
            if (geraet === 'laptop' && name === 'glas-dunkel') {   // Fokus mit der Tastatur sichtbar (Glas-Schatten verdecken ihn nicht)
              await page.focus('#userLabel');
              for (let i = 0; i < 4 && !(await page.evaluate(() => document.activeElement?.type === 'submit')); i++) await page.keyboard.press('Tab');
              const ring = await page.evaluate(() => getComputedStyle(document.activeElement).boxShadow);
              check(ring.includes('44, 194, 207'), `${name}: Fokusring am Knopf „Speichern“ sichtbar (${ring.slice(0, 60)})`);
            }
            await page.click('#mode-manual');
            geheim = await page.textContent('#kc-totp-secret-key');
            await page.fill('#totp', totp(geheim));
            await page.locator('#totp').press('Enter');
            await page.waitForSelector('#kc-totp-settings', { state: 'detached' });
          } else {
            await pruefe(page, `${name} ${geraet} neues Passwort`, erwartet, `k${n}-${name}-${geraet}-neues-passwort`);
            passwort = zufall();
            await page.fill('#password-new', passwort);
            await page.fill('#password-confirm', passwort);
            await page.locator('#password-confirm').press('Enter');
            await page.waitForSelector('#password-new', { state: 'detached' });
          }
        }
        await page.waitForURL(`${rueckkehr}**`);
        check(true, `${name} ${geraet}: Anmeldung mit App und neuem Passwort bis zur Rückkehr in die Anwendung`);
        // zweite Anmeldung wie jeden Tag: Passwort, dann der Code aus der App (login-otp); danach Abmelden
        await page.goto(anmelden(rueckkehr, { prompt: 'login' }));
        if (await page.locator('#username').isEditable().catch(() => false)) await page.fill('#username', PERSON);
        await page.fill('#password', passwort);
        await page.click('#kc-login');
        await page.waitForSelector('#otp');
        await pruefe(page, `${name} ${geraet} Code eingeben`, erwartet, `k${n}-${name}-${geraet}-code`);
        await page.fill('#otp', totp(geheim, 1));
        await page.locator('#otp').press('Enter');
        await page.waitForURL(`${rueckkehr}**`);
        check(true, `${name} ${geraet}: zweite Anmeldung mit Code aus der App`);
        await page.goto(`${realm}/protocol/openid-connect/logout`);
        await page.waitForSelector('#kc-logout');
        await pruefe(page, `${name} ${geraet} Abmelden`, erwartet, `k${n}-${name}-${geraet}-abmelden`);
      }
      await context.close();
    }
  }
  check(!nachAussen.length, `keine Anfrage nach außen oder an nicht veröffentlichte Pfade von Keycloak${nachAussen.length ? ' – ' + nachAussen.slice(0, 3).join(', ') : ''}`);
} catch (e) {
  failures.push(String(e?.message || e));
  console.log('FEHLER ' + (e?.message || e));
} finally {
  if (token) await adminToken().then((t) => person(t, null)).catch((e) => check(false, `Testperson löschen: ${e.message}`));
  await browser.close();
}
console.log(`\n${failures.length ? failures.length + ' Prüfung(en) fehlgeschlagen.' : 'Alle Prüfungen bestanden.'} Bildschirmfotos: ${shots}`);
process.exit(failures.length ? 1 : 0);
