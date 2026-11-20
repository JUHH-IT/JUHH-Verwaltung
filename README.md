# JU Hamburg Finanzen

Finanz- und Mitgliederprogramm der Jungen Union Hamburg: Buchhaltung nach dem CDU-Kontenrahmen, Kontoauszüge, Kasse,
Rechnungen, Mitglieder und Beiträge, Budgets der Kreisverbände, RPJ-Mittel, Karte.

**Dieses Repository enthält nur das Programm – niemals Daten.** Mitglieder-, Bank- und Finanzdaten, Belege, Sicherungen,
Kennwörter und Zugangsschlüssel gehören nicht hierher, auch nicht in Issues, Pull Requests oder Commit-Texte.
`test_repo.py` prüft das bei jedem Testlauf. Das Repository bleibt privat.

## Betrieb

Das Programm läuft auf einem eigenen Ubuntu-Server (bis 0.41 gab es daneben eine Mac-App). Mehrere Personen arbeiten im
Browser (Laptop, Tablet, Handy), angemeldet mit Benutzername und Passwort bzw. über Keycloak. Erreichbar ist es nur im
eigenen Tailnet (Tailscale), nie offen im Internet. Einrichtung: `SERVER.md`, Bedienung: `ANLEITUNG.md`.

Die Daten liegen außerhalb des Programms unter `/var/lib/ju-finanzen` und kommen nie über GitHub.

## Auf den Server bringen

Schritt für Schritt in `SERVER.md`, Abschnitt „Weg B – direkt aus GitHub“ (Ubuntu Server 24.04 LTS, Tailscale, ein
Schlüssel für den Server, der nur lesen darf). Danach genügt für jede neue Version:

```bash
sudo ju-finanzen aktualisieren        # neueste freigegebene Version holen und installieren
```

## Entwickeln

Python 3.12 oder 3.13, keine Datenbank und kein Build-Schritt nötig.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover                 # alle Tests, nur erfundene Daten, kein Netzzugriff
node tests/html_escape.mjs                            # Maskierung in der Oberfläche
.venv/bin/python app.py --data /tmp/ju/Daten --adresse http://127.0.0.1:8769
```

Regeln für die Entwicklung (Aufbau, Datenregel, Arbeitsweise mit Branches und Pull Requests): `AGENTS.md`.
Fachliche Regeln und offene Punkte: `REGELN-UND-OFFENE-PUNKTE.md`.

## Versionen und Freigabe

- Entwickelt wird in eigenen Branches, zusammengeführt nur über Pull Requests nach Freigabe.
- Eine freigegebene Version ist ein Tag `v<Version>` auf `main` (z. B. `v0.33`); `app.py` nennt dieselbe Version.
- Der Server installiert ausschließlich solche Tags, und nur auf ausdrücklichen Befehl. Tags außerhalb von `main`
  lehnt er ab; bei jedem Tag `v*` laufen die Tests und der Abgleich mit der Programmversion.
