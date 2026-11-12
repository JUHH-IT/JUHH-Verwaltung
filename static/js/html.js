// Sichere HTML-Vorlagen: Werte in html`…` werden immer maskiert, außer sie stammen selbst aus html`…` oder raw().

class Safe {
  constructor(text) {
    this.text = text;
  }
  toString() {
    return this.text;
  }
}

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function part(value) {
  if (value instanceof Safe) return value.text;
  if (Array.isArray(value)) return value.map(part).join('');
  if (value === null || value === undefined || value === false) return '';
  return esc(value);
}

/** Vorlagen-Tag: html`<p>${name}</p>` */
export function html(strings, ...values) {
  let out = '';
  strings.forEach((s, i) => {
    out += s;
    if (i < values.length) out += part(values[i]);
  });
  return new Safe(out);
}

/** Bereits geprüftes Markup unverändert einsetzen (nur für feste Texte wie Symbole). */
export function raw(markup) {
  return new Safe(String(markup));
}

/** Inhalt in ein Element schreiben. Nur html`…`/raw() wird als Markup eingesetzt, alles andere maskiert. */
export function mount(element, content) {
  element.innerHTML = part(content);
  return element;
}
