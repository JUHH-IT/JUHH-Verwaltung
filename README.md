# JU Hamburg Finanzen

Finanz- und Mitgliederprogramm der Jungen Union Hamburg: Buchhaltung nach dem CDU-Kontenrahmen, Kontoauszüge, Kasse,
Rechnungen, Mitglieder und Beiträge, Budgets der Kreisverbände, RPJ-Mittel, Karte.

**Dieses Repository enthält nur das Programm – niemals Daten.** Mitglieder-, Bank- und Finanzdaten, Belege, Sicherungen,
Kennwörter und Zugangsschlüssel gehören nicht hierher, auch nicht in Issues, Pull Requests oder Commit-Texte.
`test_repo.py` prüft das bei jedem Testlauf. Das Repository bleibt privat.

## Zwei Betriebsarten

| | Mac-App | Server |
| --- | --- | --- |
| Für wen | eine Person an ihrem Mac | mehrere Personen im Browser (Laptop, Tablet, Handy) |
| Anmeldung | Touch ID / Mac-Passwort | Benutzername und Passwort |
| Erreichbar | nur auf dem Mac | nur im eigenen Tailnet (Tailscale), nie offen im Internet |
| Anleitung | `ANLEITUNG.md`, Bau: `mac/build_app.py` | `SERVER.md` |

Die Daten liegen immer außerhalb des Programms: auf dem Mac im Ordner `Daten/` neben der App, auf dem Server unter
`/var/lib/ju-finanzen`. Vom Mac auf den Server kommen sie als Sicherungsdatei (`SERVER.md`, Abschnitt 3) – nie über GitHub.

## Auf den Server bringen

Kurzfassung, ausführlich in `SERVER.md`:

```bash
sudo git clone --branch v0.33 git@github.com:JUHH-IT/JUHH-Verwaltung.git /opt/ju-finanzen/quelle
sudo /opt/ju-finanzen/quelle/server/install.sh --tailscale
sudo ju-finanzen aktualisieren        # später: neueste freigegebene Version holen und installieren
```

## Entwickeln

Python 3.12 oder 3.13, keine Datenbank und kein Build-Schritt nötig.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover                 # alle Tests, nur erfundene Daten, kein Netzzugriff
node tests/html_escape.mjs                            # Maskierung in der Oberfläche
.venv/bin/python app.py --modus server --data /tmp/ju/Daten --adresse http://127.0.0.1:8769 --no-browser
```

Regeln für die Entwicklung (Aufbau, Datenregel, Arbeitsweise mit Branches und Pull Requests): `AGENTS.md`.
Fachliche Regeln und offene Punkte: `REGELN-UND-OFFENE-PUNKTE.md`.

## Versionen und Freigabe

- Entwickelt wird in eigenen Branches, zusammengeführt nur über Pull Requests nach Freigabe.
- Eine freigegebene Version ist ein Tag `v<Version>` auf `main` (z. B. `v0.33`); `app.py` nennt dieselbe Version.
- Der Server installiert ausschließlich solche Tags, und nur auf ausdrücklichen Befehl.
