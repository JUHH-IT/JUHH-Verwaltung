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

Der Dienst lauscht nur auf 127.0.0.1 und ist ohne Tailscale nicht erreichbar. Den Server selbst zusätzlich absichern
(ab 0.40 übernimmt `sudo ju-finanzen haerten` die Härtung des Betriebssystems – Abschnitt 7; die Regeln hier bleiben die Grundlage):
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

## 6. Keycloak (Anmeldung mit Zwei-Faktor)

Keycloak läuft auf demselben Server neben dem Programm und übernimmt später die Anmeldung (Passwort + Code aus einer
Authenticator-App). Einrichten bzw. auffrischen – beliebig oft wiederholbar:

```bash
sudo ju-finanzen keycloak-einrichten
sudo ju-finanzen keycloak-status        # Dienst, Health, Version, nächste Sicherung
```

Der Befehl:
- installiert die Ubuntu-Pakete `openjdk-21-jre-headless` und `postgresql` (Sicherheitsupdates kommen über apt bzw.
  `unattended-upgrades`);
- legt in PostgreSQL die Rolle und Datenbank `keycloak` mit zufälligem Passwort an. Verbindung nur über 127.0.0.1;
- lädt Keycloak (Version und SHA-256 fest in `server/keycloak-einrichten.sh`, falsche Prüfsumme = Abbruch) nach
  `/opt/keycloak/<Version>` mit Verweis `/opt/keycloak/current`. Das Programm gehört root; der Dienst `ju-keycloak` läuft
  als Benutzer `keycloak` und darf nur `/var/lib/keycloak` beschreiben (dorthin zeigt `data/`). Einstellungen:
  `/opt/keycloak/current/conf/keycloak.conf` (root:keycloak, 640; wird bei jedem Lauf neu geschrieben);
- richtet den Realm `ju-hamburg` und den Client `ju-verwaltung` ein (deutsch, keine Registrierung, kein „Passwort
  vergessen“, Passwort ab 12 Zeichen, TOTP mit 6 Ziffern/30 s, Abmeldung nach 30 Minuten Untätigkeit bzw. 10 Stunden).
  Die Account-Konsole ist gesperrt: Niemand kann seine Zwei-Faktor-Anmeldung selbst entfernen. Von den Pflichtschritten
  (Required Actions) bleiben nur „Passwort ändern“ und „Authenticator-App einrichten“ eingeschaltet – die übrigen
  (z. B. Anmeldedaten löschen, Profil ändern) ließen sich sonst über die Anmelde-Adresse selbst anstoßen. Kein Client des
  Realms (auch nicht `admin-cli`) erlaubt die Anmeldung nur mit Passwort (Direct Access Grants aus). „Full scope allowed“
  ist beim Client aus (ab 0.38; der Schalter ist in Keycloak veraltet): Tokens tragen nur die Rollen der
  Nutzerverwaltung (`manage-users`, `view-users`, `query-users` aus `realm-management`; `query-groups` steckt in
  `view-users`), Anmelde-Tokens von Personen keine;
- kopiert die Anmeldeseiten im Aussehen der Anwendung (`server/keycloak-theme/ju-hamburg`) nach
  `/opt/keycloak/<Version>/themes/ju-hamburg` (root, für andere nur lesbar) und stellt im Realm `loginTheme=ju-hamburg`
  ein – siehe „Aussehen der Anmeldeseiten“ unten;
- trägt `JU_FINANZEN_KEYCLOAK_*` in `/etc/ju-finanzen/ju-finanzen.env` ein (Rechte bleiben root:jufinanzen, 640).
  `JU_FINANZEN_ANMELDUNG` bleibt unverändert; das Programm liest die Werte nach `sudo ju-finanzen neustart`;
- veröffentlicht über Tailscale Serve nur `/auth/realms/ju-hamburg` und `/auth/resources`. Die Admin-Konsole und der
  Realm `master` sind von außen nicht erreichbar.

Tailscale Serve mit `--set-path`: Tailscale wählt den längsten passenden Pfad (ganze Pfadteile), schneidet ihn ab und
hängt den Rest an das Ziel an. Deshalb steht der Pfad im Ziel noch einmal:

```bash
sudo tailscale serve --bg --https=443 --set-path /auth/realms/ju-hamburg http://127.0.0.1:8180/auth/realms/ju-hamburg
sudo tailscale serve --bg --https=443 --set-path /auth/resources http://127.0.0.1:8180/auth/resources
```

`https://<server>/auth/realms/ju-hamburg/protocol/…` landet so bei `http://127.0.0.1:8180/auth/realms/ju-hamburg/protocol/…`.
Alles andere, auch `/auth/admin` und `/auth/realms/master`, geht weiter an das Programm (Regel `/` → 8769), das es
ablehnt. `tailscale serve status` zeigt alle drei Regeln. Eine Regel entfernen: denselben Befehl mit `off` statt Ziel.

Speicher: Keycloak braucht etwa 0,7–1 GB (Java-Heap höchstens 768 MB, systemd-Grenze 1,5 GB).

Netz: Der Dienst `ju-keycloak` darf nur mit 127.0.0.1/::1 sprechen (`IPAddressAllow=localhost`, `IPAddressDeny=any`);
PostgreSQL erreicht er über 127.0.0.1, Tailscale Serve und das Programm kommen von dort. Bewusste Abwägung: Keycloak und
das Programm teilen sich eine Adresse (`https://<server>`, Keycloak unter `/auth`) – sonst lehnt die Herkunftsprüfung
(`Sec-Fetch-Site`) die Rückkehr ab. Folge: Eine Lücke in den Anmeldeseiten von Keycloak (z. B. eingeschleustes Skript)
könnte im Namen einer angemeldeten Person Anfragen an das Programm stellen. Eine eigene Adresse für Keycloak würde das
trennen, verlangt aber eine andere Herkunftsprüfung – bewusst nicht umgesetzt; Keycloak deshalb zeitnah aktualisieren
(s. u.). Der erste Admin
(`ju-einrichtung`, Realm `master`) bleibt nur lokal: Tailscale Serve veröffentlicht weder `master` noch die Admin-Konsole.

**Admin-Konsole (Notzugang):** nur über einen SSH-Tunnel, nie über Tailscale Serve.

```bash
ssh -L 8180:127.0.0.1:8180 <benutzer>@srv1          # auf dem eigenen Rechner; dann http://127.0.0.1:8180/auth/admin/
sudo sed -n 's/^KC_BOOTSTRAP_ADMIN_//p' /etc/ju-finanzen/keycloak-admin.env   # auf dem Server: Benutzer und Passwort
```

Der Admin `ju-einrichtung` (Realm `master`) entsteht beim ersten Start. Sein Passwort steht nur in
`/etc/ju-finanzen/keycloak-admin.env` (root, 600) und dient `keycloak-einrichten`. Ist die Datei verloren:
`sudo systemctl stop ju-keycloak`, dann mit
`sudo -u keycloak /opt/keycloak/current/bin/kc.sh bootstrap-admin user --username notfall --optimized` (fragt zweimal nach
dem Passwort) einen zweiten Admin anlegen und `sudo systemctl start ju-keycloak`. Danach in der Admin-Konsole (Tunnel) das Passwort von
`ju-einrichtung` neu setzen und in die Datei eintragen, den Benutzer `notfall` wieder löschen.

**Sicherung:** Der Timer `ju-keycloak-sicherung` schreibt täglich `pg_dump` nach
`/var/backups/ju-keycloak/keycloak-<Datum>.dump` (30 Tage). Sofort: `sudo ju-finanzen keycloak-sicherung`.
Die Datei enthält Passwort-Hashes und TOTP-Schlüssel. Deshalb gehört der Ordner root (700, Dateien 600) und liegt nicht
bei den Sicherungen des Programms: Der Programmbenutzer `jufinanzen` kann sie nicht lesen. Wer die Sicherungen des Servers
zusätzlich wegkopiert, nimmt `/var/backups/ju-keycloak` mit. Zurückholen:

```bash
sudo systemctl stop ju-keycloak
sudo cat /var/backups/ju-keycloak/keycloak-2026-11-19.dump | sudo -u postgres pg_restore --clean --if-exists -d keycloak
sudo systemctl start ju-keycloak
```

**Aussehen der Anmeldeseiten (Theme `ju-hamburg`):** Anmeldung, neues Passwort, Authenticator-App einrichten,
Code-Eingabe, Fehler- und Abmeldeseiten sehen aus wie die Anwendung (Farben, Schrift, Rundungen, Knöpfe, Logo, Titel
„JU Hamburg Verwaltung“). Das Theme liegt im Programm unter `server/keycloak-theme/ju-hamburg/login/`:
`theme.properties` (Eltern-Theme `keycloak.v2`), `resources/css/ju-hamburg.css` (Werte aus `static/css/app.css`),
`resources/img/` (Logo, Symbol) und `messages/messages_de.properties` (Texte). Es enthält bewusst keine eigenen Vorlagen:
Aufbau und Formulare kommen weiter von Keycloak, Updates brechen es nicht. Alles liegt lokal und kommt über den schon
freigegebenen Pfad `/auth/resources`; die Content-Security-Policy von Keycloak bleibt unverändert.
- Nach einem Update des Programms, das das Theme ändert, einmal `sudo ju-finanzen keycloak-einrichten` ausführen (kopiert
  das Theme neu und startet Keycloak neu – Vorlagen und Texte liest Keycloak nur beim Start). Der Befehl hängt eine
  Prüfsumme an die Adressen von CSS und Logo und leert Keycloaks Zwischenspeicher gepackter Dateien
  (`/var/lib/keycloak/tmp/kc-gzip-cache`); sonst sähen Browser bis zu 30 Tage das alte Aussehen.
- Seiten, die unsere Nutzer nicht erreichen (Registrierung, „Passwort vergessen“, Passkeys …), bekommen dieselben Farben,
  aber die Standardtexte von Keycloak. Die Admin-Konsole (nur SSH-Tunnel) behält das Aussehen von Keycloak.
- Nach einem Update von Keycloak die Seiten einmal ansehen: Das Theme hängt an den Klassennamen von `keycloak.v2`
  (PatternFly 5). Zurück zum Standard im Notfall: Admin-Konsole → Realm `ju-hamburg` → Themes → Login theme leeren (der
  nächste Lauf von `keycloak-einrichten` stellt `ju-hamburg` wieder ein).

**Updates von Keycloak:** Keycloak liegt außerhalb der Programmversionen; `ju-finanzen aktualisieren` und `install.sh`
lassen es unverändert. Eine neue Keycloak-Version kommt mit einer neuen Programmversion (Version und SHA-256 in
`server/keycloak-einrichten.sh`) und wird erst mit `sudo ju-finanzen keycloak-einrichten` installiert. Der Befehl sichert
die Datenbank vorher und stellt `/opt/keycloak/current` um; die Datenbank passt Keycloak beim Start an. Die vorherige
Version bleibt in `/opt/keycloak/<Version>`. Ein Zurück gibt es nur mit der Sicherung, weil Keycloak die Datenbank umbaut.
Sicherheitsupdates von Keycloak deshalb zeitnah als neue Programmversion freigeben.

### Umstieg auf die Anmeldung über Keycloak

1. `sudo ju-finanzen keycloak-einrichten` (s. o.) und `sudo ju-finanzen keycloak-status` – Health muss `UP` zeigen.
2. Eigene Person verbinden: `sudo ju-finanzen keycloak-verbinden "Benutzername"` – zeigt **einmal** ein Startpasswort
   (nicht gespeichert). Vorher in „Benutzer & Rollen“ Vorname, Nachname und E-Mail ergänzen (sonst bricht der Befehl ab).
   Passt der Benutzername nicht für Keycloak (Leerzeichen, Umlaute), eigenen Anmeldenamen angeben:
   `sudo ju-finanzen keycloak-verbinden "Max Muster" --kc-name max.muster`.
3. `sudo ju-finanzen anmeldung keycloak` – stellt um und startet den Dienst neu. Startet er nicht, schaltet der Befehl
   selbst auf `passwort` zurück (Ursache: `sudo ju-finanzen protokoll`).
4. Im Browser „Mit JU-Konto anmelden“: Startpasswort, neues Passwort, Authenticator-App einrichten.
5. Weitere Personen legt die Verwaltung in „Benutzer & Rollen“ an; das Startpasswort erscheint dort einmal.

Der bisherige Passwortzugang bleibt nur als **Notzugang** (Anmeldeseite → „Notzugang (Administrator)“): für Personen mit
„Verwaltung schreiben“ und lokalem Passwort und nur, solange er auf dem Server freigeschaltet ist:

```bash
sudo ju-finanzen notzugang               # 60 Minuten offen
sudo ju-finanzen notzugang --minuten 15  # 1 bis 240 Minuten
sudo ju-finanzen notzugang --aus         # sofort schließen
```

Sonst lehnt das Programm das Passwort ab wie ein falsches – ein abgegriffenes Passwort allein umgeht so die
Authenticator-App nicht. Ob der Notzugang offen ist, zeigt Sicherheit → Lagebild; Freischalten und Schließen stehen in
den Ereignissen. Zurück zur Passwort-Anmeldung für alle: `sudo ju-finanzen anmeldung passwort`.

Die Verwaltungsbefehle lesen `JU_FINANZEN_KEYCLOAK_*` selbst aus `/etc/ju-finanzen/ju-finanzen.env`; das Client-Secret
steht nie in der Befehlszeile (Prozessliste).

## 7. Härtung des Betriebssystems (ab 0.40)

Das Programm ist nur im Tailnet erreichbar. Der Server selbst hängt trotzdem am Internet (SSH, Tailscale). Der Befehl
`haerten` stellt ihn so ein, dass er sich auch ohne Tailscale selbst schützt: Firewall mit Sperrliste, fail2ban,
Kern-Einstellungen, automatische Sicherheitsupdates, SSH-Grundhärtung, Statusbericht für das Programm.
Oberstes Gebot: **niemanden aussperren, nichts stillschweigend ändern.**

### Ablauf

Der erste Lauf, Schritt für Schritt:

1. **Snapshot beim Anbieter anlegen** (Hostinger: VPS → „Snapshots“). Er ist der letzte Rückweg, falls alles andere versagt.
2. **Browser-Terminal des Anbieters öffnen und offen lassen** (Hostinger: VPS → „Browser-Terminal“). Es ist der Rückweg,
   falls SSH einmal nicht mehr geht.
3. **Ansehen, was geschehen würde** – ändert nichts:
   ```bash
   sudo ju-finanzen haerten --pruefen
   ```
   Die Ausgabe lesen: je Maßnahme *Stand* (wie es jetzt ist), *Änderung* (was der Lauf täte), *Risiko*, *Hinweis* (nur Meldung).
4. **Anwenden:**
   ```bash
   sudo ju-finanzen haerten
   ```
   Der Lauf endet mit der Aufforderung „Öffne jetzt ein ZWEITES Fenster …“. Die Änderungen gelten ab jetzt auf Probe:
   Nach fünf Minuten nimmt der Server sie von selbst zurück, wenn sie niemand bestätigt.
5. **Zweites Fenster öffnen und dort neu anmelden** (ein weiteres Browser-Terminal oder eine neue SSH-Verbindung) – das
   erste Fenster bleibt offen. Gelingt die neue Anmeldung nicht: nichts tun, nach fünf Minuten gilt wieder der alte Stand.
6. **Im zweiten Fenster bestätigen:**
   ```bash
   sudo ju-finanzen haerten --bestaetigen
   ```
7. **Ergebnis ansehen:**
   ```bash
   sudo ju-finanzen schutz-status
   ```
8. **Neustart zu einem ruhigen Zeitpunkt** (`sudo reboot`, niemand arbeitet gerade im Programm), danach neu anmelden und
   noch einmal `sudo ju-finanzen schutz-status`: Schutztabelle, Sperrliste und Dienste müssen auch nach dem Start „ok“ sein.

Jederzeit zurück auf den Stand vor dem letzten Lauf: `sudo ju-finanzen haerten --zurueck` – notfalls im Browser-Terminal
des Anbieters.

`haerten` ohne Schalter wendet an, `--pruefen` berichtet nur – wie bei `install.sh --pruefen` und
`aktualisieren --pruefen`. Das ist vertretbar, weil das Anwenden gesichert und wiederholbar ist, alles am Zugang zum Server
erst nach der Bestätigung aus einem zweiten Fenster bleibt und alles, womit man sich aussperren könnte, zusätzlich einen
eigenen Schalter mit Nachweis braucht (siehe „SSH“).

So schützt der Befehl vor Fehlern:
- Geändert wird an genau einer Stelle im Skript; `--pruefen` durchläuft denselben Weg und berichtet nur.
- **Rücknahme, die nicht an der Sitzung hängt.** Ändert ein Lauf die Firewall-Maßnahme oder einen der beiden SSH-Schalter,
  plant er *vor* der ersten Änderung eine eigene Timer-Unit (`systemd-run`, Unit `ju-haertung-ruecknahme`), die nach fünf
  Minuten `haerten --zurueck` ausführt. Sie läuft auch, wenn das Fenster geschlossen wird, die Verbindung abreißt oder der
  Lauf abstürzt. Nur `haerten --bestaetigen` sagt sie ab – und nur aus einer *anderen* Anmeldung als der, die den Lauf
  gestartet hat: Eine bestehende Verbindung beweist nicht, dass eine neue noch gelingt. Aus derselben Anmeldung wird die
  Bestätigung abgelehnt. Lässt sich die Rücknahme nicht planen, werden diese Maßnahmen nicht angewendet (der Rest schon).
  Solange eine Rücknahme aussteht, zeigen `--pruefen` und `schutz-status` sie mit der Restzeit, und ein neuer Lauf beginnt
  nicht. Ansehen: `systemctl list-timers ju-haertung-ruecknahme`. Nach einem Neustart innerhalb der fünf Minuten gibt es die
  Unit nicht mehr – dann selbst entscheiden: `--bestaetigen` oder `--zurueck`.
- Jede geänderte Datei liegt vorher in `/var/backups/ju-haertung/<Zeit>/dateien/` (nur root), dazu `MANIFEST` (Dateien mit
  Prüfsumme nach dem Lauf, Rechte, Dienste, Kern-Werte vor dem Lauf, Gegenbefehle für ufw) und `protokoll.log`. Ein Lauf ohne
  Änderung legt nichts an.
- Jede Maßnahme läuft für sich. Scheitert ein Schritt, wird die begonnene Maßnahme zurückgenommen und der Lauf geht mit
  den übrigen weiter; am Ende steht die Liste „nicht angewendet: … (Grund)“ und der Befehl endet mit einem Fehler. Nach dem
  Beheben einfach erneut aufrufen. Strg-C oder ein geschlossenes Fenster nehmen die begonnene Maßnahme ebenfalls zurück;
  eine schon geplante Rücknahme bleibt bestehen.
- Firewall: Das Regelwerk wird vor dem Laden geprüft (`nft -c`). Nach dem Laden prüft der Befehl, dass Tailscale, das
  Programm (127.0.0.1), Keycloak und der Weg nach außen weiter erreichbar sind – was vorher ging, muss weiter gehen,
  sonst nimmt er die Maßnahme zurück.
- `--zurueck` nimmt den letzten Lauf zurück, ein weiteres `--zurueck` den davor. Es nimmt nur zurück, was der Lauf selbst
  getan hat: In ufw führt es die Gegenbefehle zu den eigenen Ergänzungen aus (später ergänzte Regeln bleiben), in
  `/etc/ufw/sysctl.conf` stellt es nur die eigenen Zeilen zurück, und eine Datei, die seit dem Lauf von Hand geändert wurde,
  überschreibt es nicht, sondern meldet sie. Neu installierte Pakete bleiben installiert, ihre Dienste werden abgeschaltet.
  Statusbericht und Sperrliste (`status.json`, `drop4.txt`, `drop6.txt`, `ju_mengen.nft`, `zaehler.json`) werden gelöscht,
  damit das Programm keinen veralteten Stand als „ok“ zeigt. `--zurueck --pruefen` zeigt nur, was zurückgenommen würde.
- `--ohne-rueckfrage` heißt: keine Bestätigung nötig, es wird keine Rücknahme geplant. Der Schalter ist für die
  Rücknahme-Unit und die Tests da – **für den Aufruf von Hand nicht empfohlen**, weil damit genau das Netz fehlt, das vor
  dem Aussperren schützt.

### Maßnahmen und Schalter

Schalter stehen in `/etc/ju-finanzen/haertung.conf` (legt der erste Lauf an; Vorlage `server/haertung/haertung.conf`).
`nein` heißt: Maßnahme nicht anwenden und ihre eigenen Dateien und Zeitgeber wieder entfernen. Danach `haerten` aufrufen.

| Nr. | Schalter (Vorgabe) | Was geschieht | Risiko |
| --- | --- | --- | --- |
| 1 | `FIREWALL=ja`, `TAILSCALE_PORT=nein` | ufw bleibt die Grundlage: eingehend gesperrt, `tailscale0` erlaubt. Ergänzt wird nur, was fehlt (stimmt das schon, ändert der Lauf an ufw nichts); vorhandene Regeln bleiben, SSH wird so gelassen wie vorgefunden und nur gemeldet. Dazu die eigene Tabelle `inet ju_schutz` (unten). | gering; öffnet nichts |
| 2 | `SPERRLISTE=ja` | Spamhaus DROP (IPv4 und IPv6) täglich laden, prüfen, in die Tabelle übernehmen | gering |
| 3 | `FAIL2BAN=ja` | fail2ban für SSH (5 Fehlversuche in 10 Minuten → 1 Stunde, wachsend bis 1 Woche) und „recidive“ (3 Sperren an einem Tag → 1 Woche, alle Ports) | gering |
| 4 | `KERN=ja` | Kern-Einstellungen in `/etc/sysctl.d/80-ju-haertung.conf`, sofort wirksam | gering |
| 5 | `UPDATES=ja`, `UPDATES_NEUSTART=nein` | `unattended-upgrades` sicherstellen; Neustart nach Kernel-Updates nur melden | gering |
| 6 | `DOCKER_AUS=ja` | Docker und containerd abschalten, wenn kein Container existiert (sonst nur melden); weitere unnötige Dienste nur melden | gering |
| 7 | `SSH=ja` | SSH-Grundhärtung in `/etc/ssh/sshd_config.d/00-ju-haertung.conf` | gering |
| 8 | `DATEIEN=ja` | Rechte von `/etc/ju-finanzen`, Geheimnisdateien und Sicherungsordnern; keine Speicherabzüge; journald begrenzt | gering |
| 9 | `STATUS=ja` | Statusbericht alle 15 Minuten nach `/var/lib/ju-serverschutz/status.json` | keines |
| – | `SSH_NUR_SCHLUESSEL=nein`, `SSH_NUR_TAILSCALE=nein` | riskant – setzt nur der Befehl selbst nach bestandenem Nachweis (siehe „SSH“) | mittel |
| – | `SSH_NEU_JE_MINUTE=12`, `NIE_SPERREN=` | Takt neuer SSH-Verbindungen je Quelle; eigene Adressen, die nie gesperrt werden | – |

**1 Firewall – Tabelle `inet ju_schutz`.** Sie lebt neben ufw und Tailscale und greift vor ufw (Priorität −150, nach der
Verbindungsverfolgung). Sie kann nur zusätzlich verwerfen, nie etwas öffnen: Ein „accept“ beendet nur die eigene Kette,
danach entscheidet weiter ufw. Reihenfolge: zuerst angenommen werden `lo`, `tailscale0`, bestehende Verbindungen,
127.0.0.0/8, 169.254.0.0/16 (von dort kommt das Browser-Terminal des Anbieters), 100.64.0.0/10, die entsprechenden
IPv6-Bereiche (auch fe80::/10) und die Nachbar- und Router-Erkennung von IPv6 (ICMPv6 133–136). Dann verworfen: Absender aus der Sperrliste, ungültige Pakete, mehr als 12 neue SSH-Verbindungen je Quelle und
Minute. Ausgehend wird nichts beschränkt – Updates, Tailscale, der Abruf der Sperrliste, die Adressdaten der Stadt
(`api.hamburg.de`) und der E-Mail-Versand (SMTP) gehen unverändert.
Die Tabelle lädt der Dienst `ju-serverschutz` beim Start (früh wie `ufw.service`): zuerst die Regeln aus
`/var/lib/ju-serverschutz/ju_schutz.nft`, dann die Sperrliste aus `ju_mengen.nft`. Die Regeln ändert ausschließlich ein
`haerten`-Lauf; der tägliche Zeitgeber tauscht nur den Inhalt der beiden Mengen der Sperrliste aus (ein Schritt, vorher mit
`nft -c` geprüft). Ein von Hand gesetzter Schalter wird so nie nebenbei wirksam.
Bewusst nicht über `/etc/nftables.conf` und `nftables.service`: Diese Datei beginnt üblicherweise mit `flush ruleset` und
würde die Regeln von ufw und Tailscale löschen; den Dienst `nftables` fasst der Befehl deshalb nie an (gebraucht wird nur
das Programm `nft`). Der eigene Dienst ersetzt immer nur die eigene Tabelle in einem Schritt. Lädt sie beim Start nicht, startet der Server trotzdem und ufw schützt weiter; der
Statusbericht meldet es als kritisch.
Notaus: `sudo systemctl stop ju-serverschutz` entfernt nur diese Tabelle (wieder an: `sudo systemctl start ju-serverschutz`).
Zähler ansehen: `sudo nft list counters table inet ju_schutz`.
Tailscale-Port: `tailscaled` öffnet seinen Port 41641/udp selbst (eigene Kette `ts-input`); in ufw braucht er keine Regel.
`TAILSCALE_PORT=ja` ergänzt `ufw allow 41641/udp` – nur nötig, wenn Tailscale mit `--netfilter-mode=off` oder `nodivert` läuft.
Hinweis: Der Anbieter hat zusätzlich eine eigene Firewall vor dem Server (Hostinger-Verwaltung) – sie ist unabhängig davon.

**2 Sperrliste.** Quelle: `https://www.spamhaus.org/drop/drop.txt` und `dropv6.txt` (nur HTTPS, 60 Sekunden, höchstens 2 MB),
täglich gegen 03:40 Uhr mit gestreuter Verzögerung. Eine Liste wird nur übernommen, wenn jede Zeile ein gültiges Netz ist,
die Anzahl plausibel ist (IPv4 100–20 000, IPv6 5–5 000 Netze) und kein privater, lokaler oder Tailscale-Bereich darin steht.
Einträge, die eine eigene Adresse des Servers, sein Gateway oder `NIE_SPERREN` treffen, werden ausgelassen. Bei jedem
Fehler bleibt die letzte gute Liste (`/var/lib/ju-serverschutz/drop4.txt`, `drop6.txt`). Sofort laden:
`sudo systemctl start ju-serverschutz-liste`.
Nutzungsbedingungen (geprüft am 19.11.2026, <https://www.spamhaus.org/drop/terms/> und die DROP-FAQ): Die Liste ist für alle
kostenlos nutzbar. Sie ist urheberrechtlich geschützt und wird nicht weitergegeben (sie liegt nur auf dem Server, nie im
Repository). Der Name „Spamhaus“ darf nicht in Werbung verwendet werden; wer die Liste in einem Produkt nutzt, nennt die
Quelle (The Spamhaus Project – hier und im Programm). Automatisch höchstens einmal pro Stunde abrufen, einmal am Tag
genügt. Spamhaus kann die Nutzung widerrufen – dann `SPERRLISTE=nein`.

**3 fail2ban.** Ubuntu-Paket, liest das Journal, sperrt über nftables (eigene Tabelle `inet f2b-table`). Nie gesperrt
werden 127.0.0.0/8, ::1, 169.254.0.0/16 (Browser-Terminal des Anbieters), 100.64.0.0/10 und fd7a:115c:a1e0::/48 (Tailscale)
sowie `NIE_SPERREN`. Ansehen: `sudo fail2ban-client status sshd`; entsperren: `sudo fail2ban-client unban 203.0.113.7`.

**4 Kern.** Jede Einstellung steht mit einem Satz Begründung in `/etc/sysctl.d/80-ju-haertung.conf`: SYN-Cookies, keine
Redirects, kein Source-Routing, unmögliche Absender protokollieren, `kptr_restrict`, `dmesg_restrict`, `ptrace_scope`, BPF nur
für root, `protected_*`, `suid_dumpable=0`. Strengere vorhandene Werte bleiben. Verträglichkeit:
- `rp_filter` bleibt bzw. wird 2 (locker) – so steht es auf Ubuntu 24.04 bereits. Strikt (1) verträgt sich nicht mit
  Tailscale (Exit-Nodes und Subnetz-Routen; Tailscale meldet das selbst als Störung) und wird deshalb nie gesetzt.
- `net.ipv4.ip_forward` wird nur dann 0, wenn kein Docker-Container existiert, Tailscale keine Routen und keinen
  Exit-Node anbietet und keine andere Datei (`/etc/sysctl.conf`, `/etc/sysctl.d/`, `/usr/lib/sysctl.d/`, z. B.
  `99-tailscale.conf`) die Weiterleitung einschaltet; sonst nur eine Meldung. Weil der Kern beim Abschalten der
  Weiterleitung `accept_redirects` wieder einschaltet, wendet der Befehl seine Datei als Ganzes an (`sysctl -p`), die
  Weiterleitung zuerst.
- Nicht angefasst: IPv6-Router-Ankündigungen (`accept_ra`, Netz des Anbieters), `kernel.unprivileged_userns_clone`, `kernel.sysrq`.
- ufw setzt beim Start eigene Werte aus `/etc/ufw/sysctl.conf`; dort stellt der Befehl die betroffenen Zeilen auf
  dieselben Werte (sonst stellte ufw z. B. das Protokollieren wieder ab) und merkt sich je Zeile den alten Wert.

**5 Updates.** `/etc/apt/apt.conf.d/52ju-haertung`: Paketlisten und Sicherheitsupdates täglich, kein automatischer Neustart
(`UPDATES_NEUSTART=ja`: Neustart um 04:30). Steht ein Neustart aus, meldet es der Statusbericht: `sudo reboot` zu einer
passenden Zeit. Tailscale kommt aus einer eigenen Paketquelle: `sudo tailscale set --auto-update`. Keycloak: Abschnitt 6.

**7 SSH.** Ohne Schalter nur Gefahrloses: `MaxAuthTries 3`, `LoginGraceTime 30`, `X11Forwarding no`,
`AllowAgentForwarding no`, `AllowTcpForwarding local` (der Tunnel zur Keycloak-Konsole aus Abschnitt 6 geht weiter),
`PermitEmptyPasswords no`, hängende Verbindungen nach zehn Minuten beenden. `PermitRootLogin` und die Anmeldung per
Passwort bleiben unverändert. Vor jedem Neuladen läuft `sshd -t` (den dafür nötigen Ordner `/run/sshd` legt der Befehl an,
falls der SSH-Dienst seit dem Start noch nicht lief); laufende Sitzungen bleiben. `MaxAuthTries 3`: Wer sich von außen mit
dem Passwort anmeldet und Schlüssel im SSH-Agenten hat, nimmt `ssh -o PubkeyAuthentication=no …` – sonst verbrauchen die
Schlüssel die drei Versuche, und solche Fehlverbindungen zählen bei fail2ban mit.

Die beiden riskanten Schalter nimmt der Befehl nur an, wenn der Nachweis gelingt – sonst bricht er ab, bevor er
irgendetwas ändert. Auch danach gelten sie erst auf Probe, bis `haerten --bestaetigen` aus einem zweiten Fenster kommt:

```bash
sudo ju-finanzen haerten --ssh-nur-schluessel   # Anmeldung per Passwort abschalten
sudo ju-finanzen haerten --ssh-nur-tailscale    # neue SSH-Verbindungen nur noch aus dem Tailnet
```

- `--ssh-nur-schluessel` verlangt: Für das aufrufende Konto ist ein Schlüssel in `~/.ssh/authorized_keys` hinterlegt
  **und** in den letzten 30 Tagen ist damit eine Anmeldung gelungen (Journal). Vorgehen: Schlüssel auf dem eigenen Rechner
  erzeugen (`ssh-keygen -t ed25519`), mit `ssh-copy-id root@<server>` hinterlegen, einmal damit anmelden, dann den Befehl
  aufrufen. **Achtung:** Danach funktioniert das Browser-Terminal des Anbieters möglicherweise nicht mehr (falls es
  sich mit dem Passwort anmeldet – vorher beim Anbieter klären); Rückwege sind Tailscale-SSH, der Schlüssel und der Rettungsmodus des Anbieters.
- `--ssh-nur-tailscale` verlangt: Tailscale läuft **und** Tailscale-SSH ist eingeschaltet oder es besteht gerade eine
  SSH-Verbindung über das Tailnet. Die Schutztabelle verwirft dann neue SSH-Verbindungen von außen; die Regeln in ufw
  bleiben unverändert, das Browser-Terminal des Anbieters (169.254.0.0/16) geht weiter.
- Zurück: in `/etc/ju-finanzen/haertung.conf` den Schalter auf `nein` stellen und `sudo ju-finanzen haerten` aufrufen –
  oder `--zurueck`, wenn es der letzte Lauf war. Ein von Hand auf `ja` gestellter Schalter bewirkt allein nichts: Wirksam
  wird er nur in einem `haerten`-Lauf, der den Nachweis besteht – nie durch den nächtlichen Zeitgeber.

**8 Rechte und Dateien.** `/etc/ju-finanzen` 750 root:jufinanzen, `ju-finanzen.env` 640, `keycloak-admin.env` 600,
`/var/backups/ju-keycloak` und `/var/backups/ju-haertung` 700; im Datenordner `/var/lib/ju-finanzen` ändert root nichts
(nur Meldung). Speicherabzüge (Core-Dumps) könnten Mitglieder- und Bankdaten enthalten: `kernel.core_pattern=|/bin/false`,
Grenze 0, Fehlerberichte von Ubuntu (apport) aus. journald: dauerhaft, höchstens 1 GB. `/tmp`: Rechte geprüft; ein eigenes
Dateisystem (`nosuid`, `nodev`, `noexec`) wird nur gemeldet, nicht eingerichtet (bräuchte einen Neustart und kann apt und Java stören).

**9 Statusbericht.** `/var/lib/ju-serverschutz/status.json` (Ordner root 755, Datei 644, in einem Schritt ersetzt). Der
Dienst `ju-finanzen` kann sie lesen (`ProtectSystem=strict` macht nur schreibgeschützt), aber nicht ändern. Inhalt:

```json
{"zeit": "2026-11-19T21:15:00+01:00", "version": 1,
 "pruefungen": [{"id": "firewall", "titel": "Firewall aktiv", "stand": "ok", "detail": "ufw aktiv, eingehend gesperrt"}],
 "zahlen": {"sperrliste_netze": 1836, "sperrliste_stand": "2026-11-19T04:12:07+01:00", "gebannt": 0, "abgewiesen_24h": 0,
            "updates_offen": 0, "neustart_noetig": false}}
```

`stand` ist `ok`, `hinweis` oder `kritisch`. Prüfungen (`id`): `firewall`, `schutztabelle`, `sperrliste`, `fail2ban`,
`ssh_passwort`, `ssh_erreichbar`, `ports`, `updates`, `updates_offen`, `neustart`, `kern`, `tailscale`, `dienste`, `docker`,
`keycloak_sicherung` (nur mit Keycloak), `rechte`, `platz`. `schutztabelle` ist nur „ok“, wenn die Tabelle wirklich geladen
ist; `sperrliste` nur, wenn ihre Einträge in der geladenen Tabelle stehen (`SPERRLISTE=ja` mit `FIREWALL=nein`: Hinweis
„wirkt nicht“) – läuft der tägliche Zeitgeber seit zwei Tagen nicht mehr, wird daraus ein Hinweis. `ports` ist nur kritisch, wenn außer SSH und Tailscale etwas auf
allen Adressen lauscht **und** ufw es durchlässt; nur lokal Gebundenes (auch `[::ffff:127.0.0.1]`, so erscheint Keycloak) zählt
nicht. `abgewiesen_24h` zählt die von der Schutztabelle verworfenen Pakete. Der Bericht enthält nie Adressen, Namen oder
Geheimnisse. `sudo ju-finanzen schutz-status` schreibt ihn neu und zeigt ihn an.

### Bewusst nicht umgesetzt

- **Öffentlicher Zugang (Reverse Proxy, Domain, TLS):** eigener Schritt, siehe unten.
- **Ländersperre:** sperrt auch Berechtigte (Reise, Mobilfunk, VPN) und hält gezielte Angreifer nicht auf.
- **Eigenes IDS bzw. auditd-Regelwerk:** viel Pflege und Fehlalarme für einen einzelnen Server; Hostinger betreibt
  bereits einen Malware-Scanner (monarx).
- **AIDE (Dateiprüfung des Systems):** das Programm prüft seine eigenen Dateien (Sicherheit → Dateiprüfung); Systemdateien
  ändern sich mit jedem Update.
- **Automatische Neustarts:** ein Neustart mitten in der Arbeit oder im E-Mail-Versand soll eine Entscheidung bleiben
  (Schalter `UPDATES_NEUSTART`).
- **Festplattenverschlüsselung:** lässt sich auf einem gemieteten Server nicht nachträglich und nicht ohne Eingabe beim
  Start einrichten; der Anbieter hätte den Schlüssel ohnehin im Speicher. Abschnitt 1 (Auftragsverarbeitung) gilt.
- **Beschränkung ausgehender Verbindungen** und **`/tmp` mit `noexec`:** Nutzen gering, Gefahr, Updates, Tailscale oder
  Java still zu brechen.

### Nächster Schritt: Betrieb ohne Tailscale

Tailscale bleibt vorerst an. Nach der Härtung fehlt für einen öffentlichen Betrieb nur noch ein eigener Schritt (noch nicht
umgesetzt): ein Reverse Proxy auf dem Server (z. B. Caddy) mit eigener Domain und TLS-Zertifikat, der Port 443 annimmt und an
127.0.0.1:8769 bzw. die Keycloak-Anmeldeseiten (`/auth/realms/ju-hamburg`, `/auth/resources`) weiterreicht. Dazu gehören:
`ufw allow 443/tcp`, die Domain in `JU_FINANZEN_ADRESSEN` und in der Keycloak-Einrichtung, die Anmeldung über Keycloak mit
Zwei-Faktor als Pflicht, ein fail2ban-Abschnitt für den Proxy und der Port 443 in den erwarteten Ports des Statusberichts.
Der Proxy muss auf demselben Server laufen und die Adresse des Aufrufers in `X-Forwarded-For` selbst **setzen** oder als
letzten Eintrag **anhängen** – nie den Kopf des Clients durchreichen: Die Abwehr der Anwendung (ab 0.39, `schutz.py`) wertet
nur den letzten Eintrag aus; fehlt der Kopf oder steht dort eine lokale Adresse, gilt jede Anfrage als lokal und die Abwehr
ist wirkungslos (die Konfigurationsprüfung auf der Seite „Sicherheit“ meldet das als kritisch).
Sperrliste, Schutztabelle, Kern-Einstellungen und Updates gelten dann unverändert weiter.

### Wenn etwas nicht geht

- **Keine SSH-Anmeldung mehr:** Browser-Terminal des Anbieters öffnen, `sudo ju-finanzen haerten --zurueck`. War der Lauf
  noch nicht bestätigt, genügt Warten: Nach fünf Minuten gilt von selbst wieder der alte Stand.
- **„Nicht bestätigt: … derselben Anmeldung“:** Die Bestätigung muss aus einem zweiten Fenster mit eigener, neuer Anmeldung
  kommen – nicht aus dem Fenster, in dem `haerten` lief.
- **„nicht angewendet: … (Grund)“ am Ende eines Laufs:** Diese Maßnahme wurde zurückgenommen, die übrigen gelten. Grund
  beheben (bei einem Paket: einige Minuten warten, dann `sudo apt-get update && sudo apt-get install <Paket>`) und
  `sudo ju-finanzen haerten` erneut aufrufen.
- **Eigene Adresse gesperrt:** `sudo fail2ban-client unban <Adresse>`; dauerhaft ausnehmen: `NIE_SPERREN=` in
  `/etc/ju-finanzen/haertung.conf`, dann `sudo ju-finanzen haerten`.
- **Verdacht auf die Schutztabelle:** `sudo systemctl stop ju-serverschutz` (Notaus), prüfen, wieder starten.
- **Was hat der Lauf getan?** `/var/backups/ju-haertung/<Zeit>/protokoll.log`; Zeitgeber: `systemctl list-timers 'ju-*'`,
  Protokoll: `sudo journalctl -u ju-serverschutz-liste -u ju-serverschutz-status -n 30`.
- **Nach einem Programm-Update:** `sudo ju-finanzen haerten --pruefen` zeigt, ob eine neue Version etwas ergänzen würde.

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
| `sudo ju-finanzen status` | Programmversion, Dienst, Tailscale Serve und Keycloak anzeigen |
| `sudo ju-finanzen keycloak-einrichten` | Keycloak einrichten bzw. auffrischen (wiederholbar, Abschnitt 6) |
| `sudo ju-finanzen keycloak-status` | Keycloak: Dienst, Health, Version |
| `sudo ju-finanzen keycloak-sicherung` | Keycloak-Datenbank jetzt sichern (nach `/var/backups/ju-keycloak`, nur root) |
| `sudo ju-finanzen keycloak-verbinden "Name" [--kc-name name]` | bestehende Person mit Keycloak verbinden (Startpasswort einmal) |
| `sudo ju-finanzen notzugang [--minuten N \| --aus]` | Keycloak-Betrieb: Notzugang (Passwort-Anmeldung der Verwaltung) befristet öffnen bzw. schließen |
| `sudo ju-finanzen haerten [--pruefen \| --zurueck]` | Betriebssystem härten: Firewall mit Sperrliste, fail2ban, Kern, Updates, SSH (Abschnitt 7); `--pruefen` ändert nichts |
| `sudo ju-finanzen haerten --bestaetigen` | nach `haerten` aus einem zweiten Fenster (neu angemeldet): Änderungen an Firewall und SSH-Zugang behalten – sonst nimmt der Server sie nach fünf Minuten zurück |
| `sudo ju-finanzen haerten --ssh-nur-schluessel` bzw. `--ssh-nur-tailscale` | SSH enger stellen – nur nach bestandenem Nachweis (Abschnitt 7, „SSH“) |
| `sudo ju-finanzen schutz-status` | Statusbericht des Serverschutzes (derselbe, den das Programm liest) |
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
- Betriebssystem (ab 0.40, Abschnitt 7): Firewall mit Sperrliste (Spamhaus DROP), fail2ban, Kern-Einstellungen, automatische
  Sicherheitsupdates, SSH-Grundhärtung; der Stand steht alle 15 Minuten in `/var/lib/ju-serverschutz/status.json`.
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
