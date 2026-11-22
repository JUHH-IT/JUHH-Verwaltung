// Darstellung vor dem ersten Bild setzen (0.48), damit nichts aufblitzt: klassisches Script im <head>, kein Modul.
// Gleiche Regeln wie darstellung.js (lesen/anwenden) – dort ändern heißt hier ändern.
(function () {
  var wahl = null;
  try { wahl = JSON.parse(localStorage.getItem('ju-finanzen-darstellung') || 'null'); } catch (e) { wahl = null; }
  if (!wahl || wahl.design !== 'glas') return;
  var farbe = wahl.farbe === 'hell' || wahl.farbe === 'dunkel' ? wahl.farbe
    : (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dunkel' : 'hell');
  document.documentElement.setAttribute('data-design', 'glas');
  document.documentElement.setAttribute('data-farbe', farbe);
})();
