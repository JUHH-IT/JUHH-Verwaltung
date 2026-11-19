# JU Hamburg Finanzen auf dem eigenen Server (Ubuntu 24.04 + Tailscale)

Ab Version 0.23 läuft das Programm auch als Dienst auf einem Ubuntu-Server. Aufgerufen wird es im Browser
(Laptop, Tablet, Handy), angemeldet wird mit Benutzername und Passwort. Erreichbar ist es **nur im eigenen
Tailnet**, nie direkt aus dem Internet.

```
Browser (Gerät im Tailnet) ──HTTPS──▶ Tailscale Serve (https://<server>.<tailnet>.ts.net)
                                         └──▶ 127.0.0.1:8769  Dienst „ju-finanzen“ (Benutzer jufinanzen)
                                                 └──▶ /var/lib/ju-finanzen  (Daten, Sicherungen)
```

Die Mac-App funktioniert unverändert weiter. Nach dem Umzug aber **nur noch auf dem Server buchen**, sonst
entstehen zwei Datenstände.

## 1. Server vorbereiten (einmalig)

Voraussetzungen:
- Ubuntu 24.04 (x86_64 oder ARM), mindestens 1 GB RAM und 5 GB freier Platz, ein Benutzer mit `sudo`.
- Die Festplatte bzw. die Snapshots enthalten Mitglieder- und Bankdaten:
  - beim VM-Anbieter verschlüsselte Datenträger wählen;
  - einen Auftragsverarbeitungsvertrag (Art. 28 DSGVO) abschließen;
  - bei einer VM zu Hause die Festplatte verschlüsseln.
- In der Tailscale-Verwaltung (login.tailscale.com → DNS): **MagicDNS** und **HTTPS Certificates** aktivieren.
- Der Rechnername erscheint mit dem HTTPS-Zertifikat in öffentlichen Zertifikatsprotokollen
  (`<name>.<tailnet>.ts.net`). Deshalb einen neutralen Namen wählen. Erreichbar ist der Server trotzdem nur im Tailnet.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y unattended-upgrades          # Sicherheitsupdates automatisch
sudo hostnamectl set-hostname srv1               # optional, neutraler Name: ergibt https://srv1.<tailnet>.ts.net
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh                          # Link im Browser öffnen; --ssh: Anmeldung per SSH über Tailscale
```

Der Dienst lauscht nur auf 127.0.0.1 und ist ohne Tailscale nicht erreichbar. Den Server selbst zusätzlich absichern:
- SSH nur noch über Tailscale:
  ```bash
  sudo ufw default deny incoming
  sudo ufw allow in on tailscale0
  sudo ufw enable
  ```
  Vorher prüfen, dass `ssh <benutzer>@srv1` über Tailscale funktioniert.
- In der Tailscale-Verwaltung (Access Controls) nur die eigenen Geräte bzw. Benutzer auf den Server lassen, z. B.:
  ```json
  {"grants": [{"src": ["dein-name@example.org"], "dst": ["tag:ju-server"], "ip": ["443"]}]}
  ```
  Den Server dazu mit `sudo tailscale up --ssh --advertise-tags=tag:ju-server` taggen. Den Tag in `tagOwners`
  anlegen und für Tailscale SSH eine passende `ssh`-Regel ergänzen. Vorsicht: Eine falsche Regel sperrt aus. Vorher
  sicherstellen, dass die Konsole des VM-Anbieters funktioniert.
- **Nie `tailscale funnel` verwenden.** Das würde den Server öffentlich ins Internet stellen. Richtig ist nur
  `tailscale serve`.

## 2. Programm installieren

Es gibt zwei Wege. Beide führen zur selben Installation; die Daten liegen in beiden Fällen getrennt vom Programm.

| | Weg A – Paket | Weg B – direkt aus GitHub (ab 0.33) |
| --- | --- | --- |
| Quelle | Datei `ju-finanzen-<Version>-server.tar.gz` vom Mac | privates Git-Repository, freigegebene Version (Tag `v<Version>`) |
| Server braucht Internet | nein (Python-Pakete liegen im Paket) | ja: `github.com` und `pypi.org`/`files.pythonhosted.org` (Prüfsummen) |
| Update | neues Paket kopieren, `install.sh` aufrufen | `sudo ju-finanzen aktualisieren` |

### Weg A – aus dem Paket

Das Paket `ju-finanzen-0.23-server.tar.gz` auf den Server kopieren, z. B. vom Mac:
`scp ju-finanzen-0.23-server.tar.gz <benutzer>@srv1:` (Tailscale-Name des Servers).
Die Datei `ju-finanzen-0.23-server.tar.gz.sha256` mitkopieren und vor dem Entpacken die Prüfsumme kontrollieren:

```bash
sha256sum -c ju-finanzen-0.23-server.tar.gz.sha256   # muss „OK“ melden
tar -xzf ju-finanzen-0.23-server.tar.gz
cd ju-finanzen-0.23
sudo ./server/install.sh --tailscale
```

Ab 0.32 braucht der Server für die Karte einmalig eine ausgehende Verbindung zu `https://api.hamburg.de` (öffentliche
Adressdaten der Stadt, Abruf nur auf Knopfdruck unter Einstellungen → Schalter; es werden keine Mitgliederdaten gesendet).
Den Kartenhintergrund lädt der Browser des Nutzers direkt von OpenStreetMap, nicht der Server; OpenStreetMap sieht dabei
als Herkunft den Namen des Servers im Tailnet (kein Pfad, keine Daten). Wer das nicht möchte, schaltet den Hintergrund unter
Einstellungen → Schalter aus.

Das Skript legt den Dienstbenutzer `jufinanzen` an und installiert das Programm mit festen Paketversionen
(Prüfsummen, ohne Internetzugriff aus dem Paket). Es richtet den Dienst ein, startet ihn und richtet
Tailscale Serve ein. Am Ende nennt es die Adresse, z. B. `https://ju-finanzen.tail1234.ts.net`.

### Weg B – direkt aus GitHub (ab 0.33)

Der Server holt das Programm selbst aus dem privaten Repository. Dazu bekommt er einen eigenen Schlüssel, der **nur
lesen** darf und nur für dieses eine Repository gilt („Deploy key“). Im Repository liegt nur das Programm, nie Daten.

1. Schlüssel auf dem Server anlegen und den öffentlichen Teil anzeigen:

   ```bash
   sudo apt install -y git
   sudo ssh-keygen -t ed25519 -N "" -C "ju-server" -f /root/.ssh/ju-finanzen-github
   sudo cat /root/.ssh/ju-finanzen-github.pub
   ```

2. Bei GitHub im Repository: **Settings → Deploy keys → Add deploy key**. Die angezeigte Zeile einfügen, Titel z. B.
   „Server“. **„Allow write access“ nicht anhaken.**

3. Programm holen und installieren (die Version anpassen):

   ```bash
   sudo mkdir -p /opt/ju-finanzen
   sudo git clone --no-checkout \
     -c core.sshCommand="ssh -i /root/.ssh/ju-finanzen-github -o IdentitiesOnly=yes" \
     git@github.com:JUHH-IT/JUHH-Verwaltung.git /opt/ju-finanzen/quelle
   sudo git -C /opt/ju-finanzen/quelle checkout --detach refs/tags/v0.33
   sudo /opt/ju-finanzen/quelle/server/install.sh --tailscale
   ```

   Beim ersten Verbinden fragt SSH nach dem Fingerabdruck von GitHub. Nur mit `yes` bestätigen, wenn dort
   `SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU` (ED25519) steht – so veröffentlicht ihn GitHub unter
   „GitHub's SSH key fingerprints“.

`refs/tags/v0.33` holt genau die freigegebene Version 0.33, nicht den Entwicklungsstand und auch keinen Zweig, der
zufällig genauso heißt. Der Ordner
`/opt/ju-finanzen/quelle` gehört root und wird nicht von Hand geändert. Welcher Stand läuft, zeigt
`sudo ju-finanzen status` (Programmversion und Git-Stand zum Vergleich mit GitHub).

## 3. Daten vom Mac übernehmen

1. In der Mac-App: **Stammdaten → Sicherung → Sicherung herunterladen**. Es entsteht eine ZIP-Datei mit
   Datenbank, Belegen und importierten Kontoauszügen.
2. Die ZIP-Datei auf den Server kopieren: `scp JU-Sicherung-*.zip <benutzer>@ju-finanzen:`
3. Auf dem Server:

```bash
sudo ju-finanzen uebernehmen ~/JU-Sicherung-2026-11-08.zip
```

Der Befehl hält den Dienst an, prüft die Datei (Integrität, Soll = Haben) und übernimmt sie. Danach zeigt er
die Anzahlen (Buchungen, Mitglieder, Belege …) und startet den Dienst wieder. Eine leere Neuinstallation wird
dabei zur Seite gelegt, nicht gelöscht. Vorhandene echte Daten ersetzt der Befehl nur mit `--ersetzen`, und
auch dann wird vorher alles gesichert. Danach die ZIP-Datei vom Server löschen: `rm ~/JU-Sicherung-*.zip`.

## 4. Passwort setzen

```bash
sudo ju-finanzen benutzer                        # zeigt den Benutzernamen aus der Mac-App, z. B. Lennert-Gust
sudo ju-finanzen passwort "Lennert-Gust"         # fragt zweimal, mindestens 12 Zeichen
```

Jetzt die Adresse im Browser öffnen und anmelden. Auf dem Handy über „Zum Home-Bildschirm“ wie eine App
ablegen. Das Passwort lässt sich später auch im Programm ändern (Stammdaten → Anmeldung). Wird es auf dem Server neu
gesetzt, enden alle bestehenden Anmeldungen dieses Benutzers sofort.

## 5. E-Mail-Versand

Unter **Stammdaten → E-Mail-Versand** das Passwort des Postfachs eingeben und „Auf dem Server speichern“
anhaken. Es liegt dann nur für den Dienst lesbar in `/var/lib/ju-finanzen/geheim`, nie in der Datenbank
oder in Sicherungen. Ohne Haken gilt es nur bis zum nächsten Neustart des Dienstes.

## Sicherungen

- Automatisch auf dem Server (`/var/lib/ju-finanzen/Sicherungen`):
  - täglich, die letzten 30 Tage;
  - vor jeder neuen Programmversion;
  - vor Excel- und Mitgliederimporten;
  - Belege werden zusätzlich gespiegelt.
- **Zusätzlich außerhalb des Servers:** regelmäßig (z. B. monatlich) in **Stammdaten → Sicherung** die
  ZIP-Datei herunterladen. Wenn möglich, auch die Snapshot- bzw. Backup-Funktion des VM-Anbieters einschalten.
- Sicherung sofort anlegen: `sudo ju-finanzen sicherung`
- Zurückholen:
  - aus einer ZIP-Datei: `sudo ju-finanzen uebernehmen <ZIP> --ersetzen`;
  - nur die Datenbank, z. B. aus einer Tagessicherung:
    `sudo ju-finanzen uebernehmen /var/lib/ju-finanzen/Sicherungen/Automatisch/finanzen-2026-11-07.sqlite3 --ersetzen`.
    Die Belege bleiben dabei erhalten.
  - In beiden Fällen wird der bisherige Stand vorher vollständig nach `Sicherungen/Vor Änderungen/` gelegt.

## Updates

- **Weg A (Paket):** Neues Paket kopieren, entpacken und erneut `sudo ./server/install.sh` aufrufen.
- **Weg B (GitHub):**

  ```bash
  sudo ju-finanzen aktualisieren --pruefen     # zeigt nur, welche Version es gäbe
  sudo ju-finanzen aktualisieren               # neueste freigegebene Version installieren
  sudo ju-finanzen aktualisieren 0.34          # genau diese Version
  ```

  Der Befehl installiert nur freigegebene Versionen: ein Tag `v<Version>`, der auf `main` liegt, jetzt im Repository
  steht und zur Programmversion passt. Nie automatisch und nie eine ältere als die laufende. Wurde ein Tag
  nachträglich versetzt oder im Ordner `/opt/ju-finanzen/quelle` etwas von Hand geändert oder hinzugefügt, bricht er ab.
  Nach `install.sh --zurueck` holt der Befehl ohne Versionsangabe wieder die neueste Version – also die, von der gerade
  zurückgeschaltet wurde. Erst wieder aufrufen, wenn eine korrigierte Version freigegeben ist.

Jede Version liegt in einem eigenen Ordner. Die Daten bleiben unverändert, und das Programm sichert die Datenbank
vor dem ersten Start einer neuen Version. Startet die neue Version nicht, schaltet das Skript automatisch zurück.

Zur vorherigen Version zurück: `sudo /opt/ju-finanzen/current/server/install.sh --zurueck`

## Verwaltungsbefehle

| Befehl | Zweck |
| --- | --- |
| `sudo ju-finanzen passwort "Name"` | Passwort setzen/ändern (neuer Benutzer zusätzlich mit `--neu`) |
| `sudo ju-finanzen benutzer` | Benutzer mit Rollen anzeigen (ohne Passwörter) |
| `sudo ju-finanzen notfall-aus` | Notzugang: Notfall- und Wartungsmodus beenden (0.35) |
| `sudo ju-finanzen rolle "Name" Administrator` | Notzugang: einer Person eine Rolle geben (`--entfernen` nimmt sie); sonst im Programm unter Verwaltung → Benutzer & Rollen |
| `sudo ju-finanzen uebernehmen DATEI [--ersetzen]` | Daten übernehmen bzw. zurückholen (ZIP oder .sqlite3) |
| `sudo ju-finanzen sicherung` | Sicherung jetzt anlegen |
| `sudo ju-finanzen pruefen` | Datenbank prüfen (Integrität, Anzahlen) |
| `sudo ju-finanzen aktualisieren [Version] [--pruefen]` | neue freigegebene Version aus dem Git-Repository installieren (Weg B) |
| `sudo ju-finanzen status` | Programmversion, Dienst und Tailscale Serve anzeigen |
| `sudo ju-finanzen protokoll [Zeilen]` | Protokoll des Dienstes |
| `sudo ju-finanzen neustart` | Dienst neu starten |

Einstellungen stehen in `/etc/ju-finanzen/ju-finanzen.env`. Nach einer Änderung: `sudo ju-finanzen neustart`.
Die Dienstdatei überschreibt jedes Update. Eigene Ergänzungen deshalb nur mit `sudo systemctl edit ju-finanzen` anlegen.
Ein Neustart während eines E-Mail-Versands hält den Versand nach der aktuellen Nachricht an; sie wird protokolliert.

## Sicherheit

- Nur im Tailnet erreichbar. Der Dienst lauscht auf 127.0.0.1, und Tailscale Serve stellt HTTPS mit einem
  gültigen Zertifikat bereit. In den Tailscale-ACLs lässt sich der Zugriff zusätzlich auf eigene Geräte
  beschränken.
- Anmeldung:
  - Passwort mit mindestens 12 Zeichen, gespeichert nur als scrypt-Hash (N=2^16, r=8, p=2).
  - Nach 5 Fehlversuchen je Gerät bzw. Benutzername wachsende Wartezeit, höchstens 15 Minuten. Für alle Geräte
    zusammen gilt eine Obergrenze. Die Sperre übersteht einen Neustart.
  - Abmeldung nach 2 Stunden ohne Aktivität, spätestens nach 8 Stunden. Ein neu gesetztes Passwort beendet
    bestehende Anmeldungen.
- Cookie und Anfragen:
  - Das Sitzungs-Cookie heißt über HTTPS `__Host-ju_session` und ist `HttpOnly`, `SameSite=Strict` und `Secure`.
  - Änderungen gehen nur per POST mit Herkunftsprüfung und CSRF-Token.
  - Anfragen, die der Browser als von einer anderen Seite kommend meldet (`Sec-Fetch-Site`), werden abgewiesen, auch
    von anderen Rechnern im Tailnet.
- Schutz vor Überlastung:
  - Anfragegrößen sind begrenzt; die Sitzung wird geprüft, bevor größere Daten gelesen werden.
  - Höchstens 48 gleichzeitige Verbindungen.
  - systemd begrenzt den Speicher (768 MB).
- Der Dienst läuft als eigener Benutzer ohne Anmeldung. systemd beschränkt ihn: Er darf nur
  `/var/lib/ju-finanzen` beschreiben und hat keinen Zugriff auf `/home`, Geräte oder Kernel-Einstellungen.
- Das Protokoll (`journalctl`) enthält nur:
  - Start und Stopp;
  - Anmeldungen mit Benutzername und Tailscale-Gerät;
  - bei Fehlern die Programmstelle.
  Mitgliedernamen, Beträge und IBANs stehen nie darin.
- Sicherheit (ab 0.35): Programm → Verwaltung → Sicherheit (Lagebild, Ereignisse, Notfall, Wartung, Sitzungen, Dateiprüfung).
  Die Dateiprüfung vergleicht die Programmdateien mit der Prüfliste, die `install.sh` beim Installieren anlegt
  (`server/PRUEFLISTE`). Sie erkennt Änderungen ohne root-Rechte; wer root ist, kann Prüfliste und Programm zugleich
  ändern – dagegen schützt nur der Zugang zum Server (Tailscale, SSH-Schlüssel).
- Rollen (ab 0.34): Jede Person hat nur die Rechte ihrer Rollen (Programm: Verwaltung → Benutzer & Rollen). Der Server
  prüft jede Anfrage; unbekannte Adressen sind nur mit „Benutzer ändern“ erreichbar. Die erste Person, die auf dem Server
  ein Passwort bekommt, erhält alle Rechte; weitere Personen legt die Verwaltung im Programm an. Wer auf dem Server root
  ist, kann unabhängig von Rollen alle Daten lesen.
  - Nach dem Update auf 0.34 einmal `sudo ju-finanzen benutzer` ansehen: Alle bisherigen Personen haben alle Rechte
    behalten (Schatzmeister, Landesmitgliederbeauftragter, Administrator) – auch solche ohne Passwort aus der Mac-App.
    Was nicht mehr passt, in der Verwaltung des Programms ändern.
  - `sudo ju-finanzen passwort "Name"` gibt der Person alle Rechte, wenn sich sonst niemand mehr für „Benutzer und
    Rollen“ anmelden kann (Notzugang). `sudo ju-finanzen rolle … --entfernen` lässt immer eine solche Person übrig.
- GitHub (Weg B): Wer im Repository eine Version freigeben (einen Tag `v…` setzen) kann, bestimmt, welches Programm
  auf dem Server mit den Mitglieder- und Bankdaten läuft. Deshalb:
  - Repository privat halten; für alle Konten mit Schreibrecht die Zwei-Faktor-Anmeldung einschalten.
  - `main` und die Tags `v*` schützen (Settings → Rules): Änderungen an `main` nur über Pull Requests, Tags nicht
    löschen oder versetzen, keine Zweige mit Namen `v*` anlegen.
  - Der Schlüssel des Servers darf nur lesen. Der Server holt Updates nie von selbst, nur auf Befehl.
  - Daten, Sicherungen, Kennwörter und Schlüssel nie ins Repository, in Issues oder Pull Requests (`test_repo.py`).

## Fehlersuche

- **`ju-finanzen aktualisieren` meldet „Abruf fehlgeschlagen“:** `sudo git -C /opt/ju-finanzen/quelle fetch --tags origin`
  zeigt die Ursache. „Permission denied (publickey)“: Der Deploy key fehlt im Repository oder wurde entfernt.
  „would clobber existing tag“: Eine freigegebene Version wurde nachträglich versetzt – nicht installieren, klären.
  Solange der Tag versetzt bleibt, installiert der Server nichts mehr. Rückweg: bei GitHub den Tag wieder auf den alten
  Stand setzen (`sudo git -C /opt/ju-finanzen/quelle rev-parse v<Version>` zeigt ihn); nie mit `--force` holen.

- **„Ungültiger Host“ oder „Ungültige Anfrageherkunft“:** Die aufgerufene Adresse fehlt in
  `JU_FINANZEN_ADRESSEN` (`/etc/ju-finanzen/ju-finanzen.env`). Dort eintragen und `sudo ju-finanzen neustart`
  ausführen.
- **Seite nicht erreichbar:**
  - `sudo ju-finanzen status` zeigt, ob der Dienst läuft und ob Tailscale Serve eingerichtet ist.
  - `tailscale serve status` muss `https://…` → `http://127.0.0.1:8769` zeigen.
  - Sonst: `sudo tailscale serve --bg --https=443 http://127.0.0.1:8769`
- **Tailscale Serve meldet ein Zertifikatsproblem:** In der Tailscale-Verwaltung „HTTPS Certificates“
  aktivieren.
- **Passwort vergessen:** `sudo ju-finanzen passwort "Name"` auf dem Server.

### Ohne Tailscale Serve (nur falls nötig)

In `/etc/ju-finanzen/ju-finanzen.env` folgendes eintragen:
- `JU_FINANZEN_HOST` = die Tailscale-Adresse des Servers (`tailscale ip -4`, z. B. 100.64.0.7);
- `JU_FINANZEN_ADRESSEN=http://100.64.0.7:8769`.

Die Verbindung ist dann innerhalb von Tailscale verschlüsselt, der Browser zeigt aber „nicht sicher“ an.
0.0.0.0 (alle Netzwerke) lehnt das Programm absichtlich ab.

## Für die Entwicklung

- Version freigeben (GitHub): nach der Freigabe durch Lennert Gust den Stand auf `main` mit `git tag v<Version>`
  kennzeichnen und den Tag übertragen. `VERSION` in `app.py` muss dieselbe Version nennen, sonst lehnt der Server ab.
  Einen einmal übertragenen Tag nie versetzen – stattdessen eine neue Version.
- Paket bauen (auf dem Mac oder unter Linux, sauberer Git-Stand): `./server/paket-bauen.sh <Zielordner>`.
  Das Skript lädt die Python-Pakete für Ubuntu 24.04 (x86_64 und aarch64) und prüft sie gegen
  `server/requirements-server.txt`.
- Abhängigkeiten aktualisieren:
  1. Versionen in `server/requirements-server.txt` anheben.
  2. Die Wheels mit `pip download --only-binary=:all: --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64
     --platform manylinux2014_x86_64` bzw. dasselbe für `aarch64` und `--python-version 3.12 --abi cp312 --no-deps` laden
     (wie `server/paket-bauen.sh`; z. B. cffi gibt es nur als manylinux2014-Wheel).
  3. Die Prüfsummen mit `pip hash` neu eintragen.
  4. Alle Tests mit Python 3.12 laufen lassen.
- Start im Serverbetrieb zum Testen:
  `python3 app.py --modus server --data /tmp/ju/Daten --adresse http://127.0.0.1:8769 --no-browser`;
  Passwort setzen mit `python3 verwaltung.py --data /tmp/ju/Daten passwort "Test"`.
