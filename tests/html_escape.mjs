// Prüft die Maskierung in static/js/html.js (ohne Abhängigkeiten): node tests/html_escape.mjs
import { html, raw, mount } from '../static/js/html.js';

const el = { innerHTML: '' };
const evil = '<img src=x onerror=alert(1)>';
const cases = [
  ['Text ohne Vorlage wird maskiert', () => mount(el, evil), (v) => !v.includes('<img')],
  ['Werte in Vorlagen werden maskiert', () => mount(el, html`<p>${evil}</p>`), (v) => v === '<p>&lt;img src=x onerror=alert(1)&gt;</p>'],
  ['Listen aus Vorlagen', () => mount(el, html`<ul>${['a', '<b>'].map((x) => html`<li>${x}</li>`)}</ul>`), (v) => v === '<ul><li>a</li><li>&lt;b&gt;</li></ul>'],
  ['Attribute können nicht verlassen werden', () => mount(el, html`<a title="${'"x" onclick=1'}">`), (v) => !v.includes('" onclick')],
  ['null/false bleiben leer', () => mount(el, html`<p>${null}${false}${undefined}</p>`), (v) => v === '<p></p>'],
  ['raw() bleibt Markup', () => mount(el, raw('<svg></svg>')), (v) => v === '<svg></svg>'],
];
let failed = 0;
for (const [name, run, ok] of cases) {
  run();
  const pass = ok(el.innerHTML);
  if (!pass) failed++;
  console.log(`${pass ? 'ok  ' : 'FEHLER'} ${name}${pass ? '' : ': ' + el.innerHTML}`);
}
process.exit(failed ? 1 : 0);
