Leaflet 1.9.4 (BSD-2-Clause, siehe LICENSE) – unverändert aus dem npm-Paket `leaflet@1.9.4`:
`dist/leaflet-src.esm.js` → `leaflet.js` (nur die letzte Zeile mit dem Verweis auf die Source-Map entfernt), `dist/leaflet.css` → `leaflet.css`.
Die Bilder des Pakets (Marker, Ebenenwahl) werden nicht gebraucht und fehlen: die Karte zeichnet Punkte und Flächen selbst.
Eingebunden nur von `static/js/views/karte.js`. Aktualisieren: neue Dateien einsetzen, Prüfsumme in `test_karte.py` anpassen.
