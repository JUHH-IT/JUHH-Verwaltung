# Inhaltsverzeichnis des Programms

Automatisch erzeugt von `werkzeuge/inhalt.py` – nicht von Hand ändern. Nach Änderungen an Funktionen, Wegen oder
Seiten: `python3 werkzeuge/inhalt.py`. Regeln und Zweck der Module stehen in `AGENTS.md`.

**Bereiche:** Kern · Mitgliederverwaltung · Mitgliedsbeiträge · Buchhaltung · Rechnungen und Versand · Verband · Serverbetrieb und Sicherheit · Sicherung und Pflege

## Wege der Oberfläche (`/api/…`)

Recht aus `rollen.GET`/`rollen.POST`; ⚠ = Passwortbestätigung (`app.CONFIRM`). Behandelt in `app.py` (`do_GET`/`do_POST`).

**Auswertung lesen:** `GET /api/auswertung.csv`, `GET /api/auswertung.pdf`, `GET /api/auswertung`

**Benutzer und Rollen lesen:** `GET /api/benutzer`

**Benutzer und Rollen ändern:** `POST /api/benutzer-abgleichen` ⚠, `POST /api/benutzer-app-zuruecksetzen` ⚠, `POST /api/benutzer-startpasswort` ⚠, `POST /api/benutzer` ⚠, `POST /api/rolle-loeschen` ⚠, `POST /api/rolle` ⚠

**Buchhaltung lesen:** `GET /api/abgleich`, `GET /api/beitraege-offen`, `GET /api/bill-file`, `GET /api/bill-qr.svg`, `GET /api/bill-transfers`, `GET /api/donations`, `GET /api/dues-preview`, `GET /api/invoice.pdf`, `GET /api/invoices.pdf`, `GET /api/journal.csv`, `GET /api/kasse-datei`, `GET /api/kasse.csv`, `GET /api/kasse.pdf`, `GET /api/kasse`, `GET /api/konten`, `GET /api/konto.csv`, `GET /api/konto`, `GET /api/mail-preview`, `GET /api/mail-status`, `GET /api/mandate.pdf`, `GET /api/matches`, `GET /api/member-account.pdf`, `GET /api/member-dues`, `GET /api/mitgliederkonten.csv`, `GET /api/monatsbericht.pdf`, `GET /api/op-liste.csv`, `GET /api/op-liste.pdf`, `GET /api/op-liste`, `GET /api/op-listen.zip` ⚠, `GET /api/preview.pdf`, `GET /api/pruefpaket.zip` ⚠, `GET /api/quarter-review.pdf`, `GET /api/quarter-review`, `GET /api/refund-form.pdf`, `GET /api/reminders`, `GET /api/sepa`, `GET /api/suggestions`, `GET /api/umsatz-akte`, `GET /api/umsatz-beleg`

**Buchhaltung ändern:** `GET /api/sepa.xml` ⚠, `POST /api/abgleich`, `POST /api/beitraege-vorschlaege`, `POST /api/beitrag-zuordnen`, `POST /api/bill-approval-revoke`, `POST /api/bill-approve`, `POST /api/bill-cancel`, `POST /api/bill-release`, `POST /api/bill-transferred`, `POST /api/bill-writeoff`, `POST /api/bill`, `POST /api/book`, `POST /api/cancel-invoice`, `POST /api/debit-notice`, `POST /api/dues`, `POST /api/einvoice`, `POST /api/forget`, `POST /api/import-bank`, `POST /api/import-master`, `POST /api/invoice`, `POST /api/kasse-anfang`, `POST /api/kasse-beleg`, `POST /api/kasse-ungueltig`, `POST /api/klaerfall-melden`, `POST /api/mail-cancel`, `POST /api/mail-send`, `POST /api/mandate-revoke`, `POST /api/mandate`, `POST /api/member-target`, `POST /api/park`, `POST /api/preview-bank`, `POST /api/preview-master`, `POST /api/quarter-review`, `POST /api/reverse-bank`, `POST /api/sepa-aufteilen`, `POST /api/sepa-batch-status`, `POST /api/sepa-batch`, `POST /api/sepa-begleitzettel`, `POST /api/sepa-debit-reject`, `POST /api/sepa-debit-remove`, `POST /api/sepa-mandat-vergessen` ⚠, `POST /api/sepa-vorschau`, `POST /api/series-preview`, `POST /api/series`, `POST /api/umsatz-beleg-entfernen`, `POST /api/umsatz-beleg`, `POST /api/umsatz-gueltig`, `POST /api/umsatz-notiz`, `POST /api/umsatz-ungueltig`

**Einstellungen ändern:** `POST /api/mail-check`, `POST /api/mail-forget`, `POST /api/mail-password` ⚠, `POST /api/mail-test`, `POST /api/settings` ⚠

**Mitgliederverwaltung lesen:** `GET /api/doppelte`, `GET /api/karte`, `GET /api/kenntnis`, `GET /api/klaerfaelle`, `GET /api/member-account`, `GET /api/mitgliedsnummer`, `GET /api/ov-abgleich`, `GET /api/ov-gebiete`, `GET /api/wahlrecht.csv`, `GET /api/wahlrecht.pdf`, `GET /api/wahlrecht`, `POST /api/adresse-pruefen`, `POST /api/mitglieder-liste` ⚠, `POST /api/strassen-suche`, `POST /api/zusammenfuehren-vorschau`

**Mitgliederverwaltung lesen oder Buchhaltung lesen:** `GET /api/beitrag-nach-alter`

**Mitgliederverwaltung lesen oder Einstellungen lesen oder Benutzer und Rollen lesen:** `GET /api/adressen-status`

**Mitgliederverwaltung ändern:** `POST /api/gliederung-import`, `POST /api/gliederung-vorschau`, `POST /api/kein-duplikat`, `POST /api/kenntnis-abhaken`, `POST /api/klaerfall-klaeren`, `POST /api/klaerfall-notiz`, `POST /api/kv-nummern`, `POST /api/member-austritt`, `POST /api/member-delete` ⚠, `POST /api/member-notiz`, `POST /api/member-update`, `POST /api/member`, `POST /api/members-import` ⚠, `POST /api/members-preview`, `POST /api/ortsverband-loeschen` ⚠, `POST /api/ortsverband-status`, `POST /api/ortsverband`, `POST /api/ov-abgleich` ⚠, `POST /api/ov-gebiet`, `POST /api/zusammenfuehren` ⚠

**Mitgliederverwaltung ändern oder Benutzer und Rollen ändern:** `POST /api/adressen-laden`

**Sicherheit lesen:** `GET /api/schutz`, `GET /api/sicherheit-ereignisse.csv`, `GET /api/sicherheit-ereignisse`, `GET /api/sicherheit`, `POST /api/notfall-an`

**Sicherheit ändern:** `GET /api/sicherheit-verwaltung`, `POST /api/dateipruefung`, `POST /api/notfall-aus` ⚠, `POST /api/schutz-einstellungen` ⚠, `POST /api/schutz-freigeben` ⚠, `POST /api/schutz-liste-laden` ⚠, `POST /api/schutz-mitschnitt` ⚠, `POST /api/schutz-sperren` ⚠, `POST /api/sitzung-beenden` ⚠, `POST /api/sperre-aufheben` ⚠, `POST /api/wartung` ⚠

**Sicherung ändern:** `GET /api/backup` ⚠

**Verband lesen:** `GET /api/haushalt.pdf`, `GET /api/kv-detail`, `GET /api/kv-ledger`, `GET /api/kv.pdf`, `GET /api/rpj-buchungen.csv`, `GET /api/rpj-projekt.pdf`, `GET /api/rpj-projekt`, `GET /api/rpj-vorlage.csv`, `GET /api/rpj.csv`, `GET /api/rpj.pdf`

**Verband lesen oder Buchhaltung lesen:** `GET /api/haushalt-reference`, `GET /api/rpj-offen`, `GET /api/rpj`

**Verband ändern:** `POST /api/budget`, `POST /api/haushalt-import`, `POST /api/haushalt-plan`, `POST /api/import-kv-master`, `POST /api/kv-close`, `POST /api/kv-label`, `POST /api/kv-plan`, `POST /api/kv-transfer-reverse`, `POST /api/kv-transfer`, `POST /api/kv-values`, `POST /api/preview-kv-master`, `POST /api/rpj-abruf-loeschen` ⚠, `POST /api/rpj-abruf`, `POST /api/rpj-import-vorschau`, `POST /api/rpj-import`, `POST /api/rpj-jahr`, `POST /api/rpj-projekt-loeschen` ⚠, `POST /api/rpj-projekt`

**Verband ändern oder Buchhaltung ändern:** `POST /api/cash-donation`, `POST /api/rpj-zuordnen-mehrere`, `POST /api/rpj-zuordnen`

**angemeldet:** `GET /api/data`, `GET /api/haushalt`, `GET /api/karte-gebiete`, `GET /api/regelwerk`, `GET /api/workflow`, `POST /api/bestaetigen`, `POST /api/logout`, `POST /api/password`

**ohne Anmeldung** (`rollen.OUTSIDE`): `/api/keycloak/rueckkehr`, `/api/keycloak/start`, `/api/login`, `/api/status`

## Oberfläche (`static/js`, ES-Module ohne Build-Schritt)

- `static/js/account-picker.js`: accountField, bindAccountPickers, bindPosten, isBalance, openPicker, postenCodes, postenComplete, postenField, setPosten, titleFor
- `static/js/adressdaten.js`: addressCredit, addressState, addressSummary, loadAddressData, loadedText → /api/adressen-laden, /api/adressen-status
- `static/js/api.js`: api, download, fileToBase64, onConfirm, onLocked, onUnauthorized, setCsrf → /api/login, /api/status
- `static/js/darstellung-start.js`
- `static/js/darstellung.js`: bindDarstellung, darstellung, darstellungDialog, darstellungWahl, setDarstellung
- `static/js/format.js`: bookingKind, date, dateTime, eur, findIban, parseCents, plain, shortDate, signed, toInput, todayIso, weekdayDate
- `static/js/gliederung.js`: bindKvOv, kvLabel, kvOvFields, memberKvs, ovById, ovLabel, ovsOf, setKvOv
- `static/js/html.js`: esc, html, mount, raw
- `static/js/main.js` → /api/bestaetigen, /api/keycloak, /api/login, /api/logout, /api/status
- `static/js/member-picker.js`: CASE_REASON, EXTERNAL_REASON, addressLine, caseLabel, externalLabel, fold, matchMembers, membersLabel, membersPayload, pickMembers, sortName, suggestMembers
- `static/js/mitglieder-listen.js`: anschriftVollstaendig, fehlt, gespeichert, isActive, listen, speichern
- `static/js/ov-gebiete.js`: gebietFelder, gebietText, gebietVon, gebietVorschlaege, gebieteDialog → /api/ov-gebiet, /api/ov-gebiete
- `static/js/rpj-field.js`: RPJ_CATEGORIES, bindRpj, categoryOptions, projectLabel, requestLabel, rpjCategory, rpjField, rpjKind, rpjMissing, rpjProjects, rpjRequests, rpjValue, targetOptions
- `static/js/store.js`: accountName, can, daysOverdue, eligibleInvoices, go, kvName, ledgerRows, onChange, openBills, refresh, render, store, titleLabel, titleOf, yearBank → /api/data, /api/haushalt, /api/status, /api/workflow
- `static/js/ui.js`: badge, closeDialog, closeTop, confirmAction, dialog, empty, icon, mailPreview, meter, overlay, pageHead, safe, table, toast, uploadProtected → /api/mail-preview, /api/preview.pdf
- `static/js/views/auswertung.js`: aussageBestand, aussageBindung, aussageDichte, aussageGliederung, aussageStruktur, auswertung, flaecheKm2, klassen, skala, stufe, vorn → /api/auswertung, /api/auswertung.csv, /api/auswertung.pdf, /api/karte, /api/karte-gebiete
- `static/js/views/bank.js`: bankKeys, parkForm, renderLedger, reversedNote, roundSources, useLedger, voidBankDialog → /api/kasse-datei, /api/park, /api/reverse-bank, /api/umsatz-gueltig, /api/umsatz-ungueltig
- `static/js/views/benutzer.js`: benutzer → /api/benutzer, /api/benutzer-abgleichen, /api/benutzer-app-zuruecksetzen, /api/benutzer-startpasswort, /api/rolle, /api/rolle-loeschen
- `static/js/views/bills.js`: approvalBadge, billBadge, bills, paymentDialog, reviewDialog → /api/bill, /api/bill-approval-revoke, /api/bill-approve, /api/bill-cancel, /api/bill-file, /api/bill-qr.svg, /api/bill-release, /api/bill-transferred, /api/bill-transfers, /api/bill-writeoff, /api/einvoice, /api/quarter-review, /api/quarter-review.pdf, /api/refund-form.pdf
- `static/js/views/booking.js`: bookingEditor → /api/book, /api/matches, /api/suggestions
- `static/js/views/budgets.js`: budgets → /api/cash-donation, /api/donations, /api/haushalt-import, /api/haushalt-plan, /api/haushalt-reference, /api/haushalt.pdf, /api/kv-close
- `static/js/views/cash.js`: cash → /api/kasse, /api/kasse-anfang, /api/kasse-beleg, /api/kasse-datei, /api/kasse-ungueltig, /api/kasse.csv, /api/kasse.pdf
- `static/js/views/doppelte.js`: doppelteDialog → /api/doppelte, /api/kein-duplikat, /api/zusammenfuehren, /api/zusammenfuehren-vorschau
- `static/js/views/dues.js`: dues → /api/beitraege-offen, /api/beitraege-vorschlaege, /api/beitrag-zuordnen, /api/klaerfall-melden
- `static/js/views/imports.js`: importDialog, learningDialog, masterDialog → /api/forget, /api/import-bank, /api/import-master, /api/preview-bank, /api/preview-master
- `static/js/views/invoice-create.js`: createDialog → /api/preview.pdf, /api/series, /api/series-preview
- `static/js/views/invoices.js`: invoiceBadge, invoices → /api/cancel-invoice, /api/dues, /api/dues-preview, /api/invoice.pdf, /api/preview.pdf
- `static/js/views/journal.js`: journal → /api/journal.csv
- `static/js/views/karte.js`: karte, leaveMap → /api/karte, /api/karte-gebiete, /api/strassen-suche
- `static/js/views/kenntnis.js`: kenntnis, wann, wasGeaendert → /api/kenntnis, /api/kenntnis-abhaken, /api/member-account
- `static/js/views/klaerfaelle.js`: cases → /api/klaerfaelle, /api/klaerfall-klaeren, /api/klaerfall-notiz
- `static/js/views/konten.js`: konten, saldo → /api/konten, /api/konto, /api/konto.csv
- `static/js/views/kv.js`: bindKv, bindKvPage, bindTransfers, kvPage, kvTable, masterDialog, needsAttention, openKv, planRest, transferDialog, transferList, valuesDialog → /api/import-kv-master, /api/kv-detail, /api/kv-label, /api/kv-plan, /api/kv-transfer, /api/kv-transfer-reverse, /api/kv-values, /api/kv.pdf, /api/op-liste.pdf, /api/preview-kv-master
- `static/js/views/mailing.js`: afterIssue, mailBadge, printLink, reminderBadge, reminderDialog, sendDialog, sendable → /api/invoices.pdf, /api/mail-cancel, /api/mail-send, /api/mail-status, /api/mail-test, /api/reminders
- `static/js/views/members.js`: account, balanceCell, importMembersDialog, memberDialog, members, openList, wahlBadge → /api/adresse-pruefen, /api/beitrag-nach-alter, /api/member, /api/member-account, /api/member-account.pdf, /api/member-austritt, /api/member-delete, /api/member-notiz, /api/member-target, /api/member-update, /api/members-import, /api/members-preview, /api/mitglieder-liste, /api/mitgliedsnummer, /api/sepa-mandat-vergessen
- `static/js/views/mitglieder-ueberblick.js`: anlaesse, mitgliederUeberblick → /api/auswertung, /api/doppelte, /api/kenntnis, /api/kenntnis-abhaken, /api/ov-abgleich, /api/regelwerk
- `static/js/views/open-items.js`: openItems → /api/invoice.pdf, /api/op-liste, /api/op-liste.csv, /api/op-liste.pdf, /api/op-listen.zip
- `static/js/views/ov-abgleich.js`: ovAbgleichDialog → /api/ov-abgleich
- `static/js/views/overview.js`: overview → /api/monatsbericht.pdf, /api/pruefpaket.zip
- `static/js/views/reconcile.js`: reconcileDialog → /api/abgleich
- `static/js/views/regelwerk.js`: regelwerk → /api/regelwerk
- `static/js/views/rpj.js`: assignDialog, projectDialog, rpj → /api/rpj, /api/rpj-abruf, /api/rpj-abruf-loeschen, /api/rpj-buchungen.csv, /api/rpj-import, /api/rpj-import-vorschau, /api/rpj-jahr, /api/rpj-offen, /api/rpj-projekt, /api/rpj-projekt-loeschen, /api/rpj-projekt.pdf, /api/rpj-vorlage.csv, /api/rpj-zuordnen, /api/rpj-zuordnen-mehrere, /api/rpj.csv, /api/rpj.pdf
- `static/js/views/sepa-datei.js`: sepaFileDialog → /api/sepa-aufteilen, /api/sepa-begleitzettel, /api/sepa-vorschau
- `static/js/views/sepa.js`: activeMandate, bindMandateSection, mandateSection, sepaDialog → /api/debit-notice, /api/mandate, /api/mandate-revoke, /api/mandate.pdf, /api/sepa, /api/sepa-batch, /api/sepa-batch-status, /api/sepa-debit-reject, /api/sepa-debit-remove, /api/sepa.xml
- `static/js/views/settings.js`: settings → /api/backup, /api/gliederung-import, /api/gliederung-vorschau, /api/keycloak, /api/kv-nummern, /api/mail-check, /api/mail-forget, /api/mail-password, /api/mail-status, /api/mail-test, /api/mandate.pdf, /api/ortsverband, /api/ortsverband-loeschen, /api/ortsverband-status, /api/ov-gebiet, /api/ov-gebiete, /api/password, /api/preview.pdf, /api/settings
- `static/js/views/sicherheit.js`: sicherheit → /api/dateipruefung, /api/notfall-an, /api/notfall-aus, /api/schutz, /api/schutz-einstellungen, /api/schutz-freigeben, /api/schutz-liste-laden, /api/schutz-mitschnitt, /api/schutz-sperren, /api/sicherheit, /api/sicherheit-ereignisse, /api/sicherheit-ereignisse.csv, /api/sicherheit-verwaltung, /api/sitzung-beenden, /api/sperre-aufheben, /api/wartung
- `static/js/views/umsaetze.js`: kontoumsaetze, umsatzKeys → /api/reverse-bank, /api/umsatz-gueltig
- `static/js/views/umsatz-akte.js`: akteFeld, umsatzName → /api/umsatz-akte, /api/umsatz-beleg, /api/umsatz-beleg-entfernen, /api/umsatz-notiz
- `static/js/views/verbuchen.js`: verbuchen, verbuchenKeys
- `static/js/views/wahlrecht.js`: wahlrecht → /api/wahlrecht, /api/wahlrecht.csv, /api/wahlrecht.pdf

## Kern

### app.py

JU Hamburg Finanzen – local authenticated pilot server.

Tests: test_doppelte.py, test_http.py, test_karte.py, test_keycloak_app.py, test_mitgliederverwaltung.py, test_rollen.py, test_schutz.py, test_server.py, test_sicherheit.py, test_static.py, test_uebernahme.py, test_umsatzakte.py

- `backups_dir()`
- `save_receipt(store, table, rid, number, filename, suffix, raw)` – Beleg (Eingangsrechnung, Kassenbeleg oder Beleg am Kontoumsatz) in Daten/Belege speichern – nie einen vorhandenen überschreiben.
- `keycloak_mode()`
- `notzugang(db, name)` – Keycloak-Betrieb: Anmeldung mit lokalem Passwort nur als Notzugang – solange auf dem Server freigeschaltet
- `kc_abmelden(db, name)` – Keycloak-Sitzungen der Person beenden – best effort: ein Fehler wird nur protokolliert (Meldung ohne Tokens).
- `kc_abgleichen(db, uid, actor)` – Person nach dem Speichern in Keycloak anlegen bzw. angleichen. Liefert beim Anlegen {'startpasswort'} – genau einmal,
- `clean_address(text)` – Adresse wie https://ju-finanzen.beispiel.ts.net oder http://100.64.0.1:8769 → 'schema://host[:port]'.
- `allowed_hosts(port)`
- `allowed_origins(port)`
- `wait_text(seconds)`
- `user_fingerprint(name)` – Fingerabdruck aus Passwort-Hash und Sitzungsstand (Servermodus): ändert sich das Passwort – auch über verwaltung.py –
- `log(text)`
- `log_error(where)` – Unerwarteten Fehler ohne Inhalte protokollieren: Art und Programmstelle, keine Werte (keine Daten im Protokoll).
- `static_file(path)` – Datei der Oberfläche zu einem URL-Pfad – nur innerhalb von static/, ohne versteckte Dateien.
- `keep_original(preview, raw)`
- Klasse `LocalServer`
  - `LocalServer.process_request(request, client_address)`
  - `LocalServer.process_request_thread(request, client_address)`
  - `LocalServer.server_bind()`
- Klasse `LocalServer6`
- `preview_invoice(store, query, actor)` – Rechnung für Vorschau oder Testmail: festgeschrieben (id), Beitragsentwurf (dues+year),
- `mail_preview_page(cfg, inv)` – HTML-Seite für die Vorschau im Programm: Kopfangaben und die E-Mail, wie sie verschickt wird.
- `prune_previews()`
- Klasse `Handler`
  - `Handler.log_message(*args)`
  - `Handler.send(status, data, ctype=…, cookie=…, attachment=…, inline=…, frame=…)`
  - `Handler.send_file(fileobj, size, ctype, attachment)` – Große Antwort (z. B. Kassenprüfer-Paket) stückweise aus einer Datei senden.
  - `Handler.head(status, ctype, length, cookie=…, attachment=…, inline=…, frame=…, encoding=…)`
  - `Handler.valid_host()`
  - `Handler.via_https()` – Kam die Anfrage über HTTPS (Tailscale Serve auf demselben Rechner bzw. eingetragene https-Adresse)?
  - `Handler.cookie_name(base=…)`
  - `Handler.cookie(sid=…, base=…)` – Sitzung (SameSite=Strict) bzw. Keycloak-Vorgang `ju_kc` (Lax, 10 Minuten – muss die Rückkehr von Keycloak überstehen).
  - `Handler.cookie_value(base=…)` – Wert aus dem Cookie-Kopf – selbst gelesen, damit fremde Cookies (z. B. von anderen Rechnern im Tailnet)
  - `Handler.session_id()`
  - `Handler.redirect(target, cookies=…)`
  - `Handler.keycloak_start(query)` – Anmeldung über Keycloak beginnen (`aktion`: passwort = danach Passwort ändern, bestaetigen = erneut anmelden als
  - `Handler.keycloak_rueckkehr(store, query)` – Rückkehr von Keycloak: Vorgang einmalig einlösen, Person NUR über keycloak_id = sub finden (nie über den Namen),
  - `Handler.session()`
  - `Handler.new_session(actor)`
  - `Handler.sitzungen()` – Aktive Sitzungen für die Sicherheit – mit öffentlicher Kennung (Prüfsumme), nie dem Sitzungsschlüssel selbst.
  - `Handler.sperren(store)` – Gesperrte Schlüssel mit Kennung (Prüfsumme) statt Schlüssel – ein unbekannter „Name“ kann ein vertipptes Passwort sein.
  - `Handler.system_info()`
  - `Handler.verweigert(store, path)`
  - `Handler.gesperrt(store, rights, method, path)` – Notfall/Wartung: 503 für alle ohne „Sicherheit ändern“, außer den wenigen erlaubten Wegen. True = Antwort gesendet.
  - `Handler.braucht_bestaetigung(method, path)`
  - `Handler.sperre_info(store, s)` – Sperre für /api/status: nur für Angemeldete; Grund und Auslöser nur mit „Sicherheit lesen“.
  - `Handler.rights(store)` – Rechte der angemeldeten Person (rollen.py).
  - `Handler.from_proxy()`
  - `Handler.adresse()` – Adresse des Aufrufers – nur über schutz.aufrufer (X-Forwarded-For zählt allein von 127.0.0.1); None = unlesbar.
  - `Handler.device()` – Gerät für Sperre und Protokoll: hinter Tailscale Serve (Anfrage von 127.0.0.1) der Tailscale-Login, sonst die
  - `Handler.abwehr(daten=…)` – Der eine Einstieg der Abwehr (schutz.py) vor jeder Anfrage im Serverbetrieb – auch für statische Dateien und unbekannte
  - `Handler.send_response(code, message=…)` – Jede Antwort – auch Fehler der Basisklasse – läuft hier durch: Fehlerbremse und Live-Verkehr (schutz.nachher).
  - `Handler.who()` – Herkunft für das Protokoll (keine Inhalte).
  - `Handler.same_site()` – Browser melden mit Sec-Fetch-Site, woher eine Anfrage kommt: nur eigene Seite oder direkte Eingabe zulassen
  - `Handler.andere_methode()` – Jede Methode außer GET und POST: auch hier zuerst die Abwehr (Sperren, Fallen, Zählung), sonst 405.
  - `Handler.do_GET()`
  - `Handler.do_POST()`
- `write_private(name, text)` – Datei im Datenordner anlegen, die nur der Benutzer lesen kann.
- `env_list(name)`
- `run_backups(backups)` – Tägliche Sicherung der Datenbank und neue Belege spiegeln (beim Start und im Servermodus stündlich geprüft).
- `schutz_stuendlich()` – Abwehr im Hintergrundlauf: Stundenstatistik schreiben, Bedrohungsliste täglich auffrischen – Fehler nur protokollieren.
- `notizen_stuendlich()` – Notizen ausgetretener Mitglieder löschen (mitglieder.notizen_bereinigen, DSGVO) – Fehler nur protokollieren.
- `check_bind(host)` – Servermodus: nur eine bestimmte Adresse (127.0.0.1 hinter Tailscale Serve oder die Tailscale-Adresse 100.x),
- `keycloak_from_env(env)` – JU_FINANZEN_ANMELDUNG = passwort (Standard) | keycloak. Keycloak nur mit vollständiger, passender Einrichtung – sonst
- `main(argv=…)`

### core.py

Local JU Hamburg finance pilot. All currency values are integer cents.

Tabellen: `accounts`, `audit`, `bank`, `budgets`, `entries`, `imports`, `invoices`, `journals`, `kv`, `lines`, `mailings`, `members`, `payments`, `reversed_allocations`, `settings`, `users`

Tests: test_auswertung.py, test_beitraege.py, test_bereinigung.py, test_berichte.py, test_core.py, test_doppelte.py, test_erechnung.py, test_erstattung.py, test_freigabe.py, test_gliederung.py, test_haushaltsposten.py, test_http.py, test_import.py, test_karte.py, test_kasse.py, test_kenntnis.py, test_keycloak_app.py, test_konten.py, test_kvabgleich.py, test_kvbudget.py, test_lastschrift.py, test_mahnung.py, test_mitgliedbank.py, test_mitglieder.py, test_mitgliederkonto.py, test_mitgliederverwaltung.py, test_opliste.py, test_ovabgleich.py, test_pdfschrift.py, test_pruefung.py, test_rechnungen.py, test_regelwerk.py, test_rollen.py, test_rpj.py, test_schutz.py, test_sepadatei.py, test_server.py, test_sicherheit.py, test_storno.py, test_uebernahme.py, test_umsatz_ungueltig.py, test_umsatzakte.py, test_verbindlichkeiten.py, test_wahlrecht.py

- `bank_source(kind, b)` – Journal-Quelle eines Bankumsatzes je Zuordnungsrunde: bank:7, nach dem ersten Storno bank:7/1 usw.
- `ensure_rows(db, table, rows)` – Grunddaten (Bereiche, Konten) anlegen, falls sie fehlen. Erst lesen: Store() entsteht je Anfrage, ein INSERT OR IGNORE
- Klasse `Store`
  - `Store.rows(sql, args=…)`
  - `Store.data_start()` – Datenbeginn (JJJJ-MM-TT): ältere Umsätze werden nicht mehr importiert (gesetzt beim Löschen eines Jahres).
  - `Store.settings()`
  - `Store.config()` – Stammdaten mit Standardwerten für nicht gesetzte Felder.
  - `Store.audit(actor, action, detail)`
  - `Store.save_settings(data, actor)`
  - `Store.member(data, actor)`
  - `Store.update_member(mid, data, actor)`
  - `Store.individual_dues(m, year, rates=…)` – Individueller Jahresbeitrag für ein Jahr laut Verlauf (mitglieder.record_dues); ohne Verlauf der Wert am Mitglied (vor 0.24).
  - `Store.dues_for_invoice(m, year, cfg=…)` – Beitrag für den Beitragslauf: festgelegtes Soll (Mitgliederbuchhaltung) vor der Berechnung; 0 € → keine Rechnung.
  - `Store.dues_calc(m, year, cfg=…, rates=…)` – Beitrag eines Mitglieds: {'amount': Cent, 'basis': Text} oder None (kein Beitrag in diesem Jahr).
  - `Store.dues_by_age(birth, joined, year, guest=…, dues_free=…, left_on=…)` – Beitrag nach Alter (Beitragsstaffel, Satzung § 49) für ein Jahr und das folgende – für die Anzeige im Mitgliedsformular,
  - `Store.dues_amount(m, year)`
  - `Store.dues_due(m, year, today, cfg)` – Fällig zum 1.1. (bzw. Eintritt), frühestens nach dem Zahlungsziel ab Rechnungsdatum.
  - `Store.journal(day, description, source, entries, reversal=…, year=…)`
  - `Store.check_issuer(kind, cfg=…)`
  - `Store.next_numbers(kind, count, issued=…)` – Die nächsten fortlaufenden Rechnungsnummern (B = Beitrag, R = Rechnung, je Kalenderjahr).
  - `Store.snapshot_for(kind, cfg, member_number=…)` – Angaben, die mit der Rechnung festgeschrieben werden (spätere Stammdatenänderungen ändern alte Rechnungen nicht).
  - `Store.dues_preview(year)`
  - `Store.issue_dues(year, ids, actor)`
  - `Store.invoice_accounts()` – Einnahmenkonten für normale Rechnungen (Veranstaltungen u. Ä., Kontenbereich 85xx).
  - `Store.series_preview(data)` – Vorschau ohne Schreiben. Festgeschrieben wird später genau dieser Stand (app: Vorschau-Schlüssel).
  - `Store.issue_series(data, actor)`
  - `Store.issue_normal(data, actor)` – Einzelne Rechnung an einen frei eingegebenen Empfänger (ältere Schnittstelle).
  - `Store.invoices()`
  - `Store.reverse_invoice(iid, actor)`
  - `Store.preview_bank(raw, filename)`
  - `Store.import_bank(preview, selected, actor)`
  - `Store.book(bid, parts, actor, learn=…, confirm_no_bill=…)`
  - `Store.reverse_bank(bid, actor)`
  - `Store.bank_rows()` – Umsätze und Kassenbelege für die Oberfläche (mit `hat_notiz`, `belege`, `ohne_beleg`, `bezug` aus umsatzakte.ergaenzen); 'fy' = Haushaltsjahr, in das eine neue Zuordnung fällt, wenn es vom Buchungsdatum
  - `Store.budget_view(year)` – Kreisverbände eines Jahres: Plan und Ist-Budget (kvbudget.year_overview).
  - `Store.snapshot(year)`

### formate.py

Geld- und Datumsformate. Alle Geldbeträge werden als ganze Cent geführt.

Tests: test_import.py

- `money(value)` – Liest einen Betrag im deutschen oder englischen Format und liefert Cent.
- `cents_from_number(value)` – Zahl aus einer Tabellenzelle (float) exakt in Cent umrechnen.
- `iso(value)`
- `euro(cents)` – Cent → „1.234,50 €“ (für PDF und E-Mail).
- `german_date(value)` – „2026-10-28“ → „28.10.2026“.
- `add_months(day, months)` – „2024-02-29“ + 36 Monate → „2027-02-28“ (Monatsende, wenn der Tag im Zielmonat fehlt).
- `iban_compact(value)`
- `iban_valid(value)` – Prüfziffer nach ISO 13616 (Modulo 97).
- `iban_pretty(value)`
- `email_address(value, required=…)` – Prüft eine einzelne E-Mail-Adresse; liefert sie bereinigt oder '' (wenn erlaubt).

### stammdaten.py

Stammdaten (Einstellungen): erlaubte Felder, Standardwerte und Prüfung an einer Stelle.

Tests: test_kenntnis.py, test_rechnungen.py, test_regelwerk.py

- `unknown_placeholders(text)`
- `clean(data, current)` – Prüft geänderte Felder; liefert {Schlüssel: Text}. current = bisherige Stammdaten (mit Standards).
- `dues_scale(cfg)` – [(bis Alter, Cent), …, (None, Cent)] – letzte Stufe ohne Obergrenze.
- `first_name(inv)` – Anrede: bei Mitgliedern der Vorname, sonst der vollständige Empfängername.
- `invoice_values(inv, sender=…)` – Werte für die Platzhalter aus einer Rechnung (siehe rechnung_pdf.invoice_data).
- `fill(text, values)` – Ersetzt {platzhalter}; Unbekanntes bleibt sichtbar stehen.

### kontenrahmen.py

Kontenrahmen der CDU Deutschlands (gültig ab 1.1.2016) mit den zusätzlichen Konten der JU Hamburg; Kontenblätter.

Tests: test_konten.py

- `init(db)` – Fehlende Konten des Kontenrahmens anlegen; vorhandene Konten (Namen, Art) bleiben unverändert.
- `group_of(code)`
- `liability_account(store)`
- `system_reason(code, liability=…)` – Warum ein Konto nicht direkt aus dem Kontoauszug gebucht wird – None, wenn es bebuchbar ist.
- `bank_bookable(code, kind, liability=…)` – Bestandskonto direkt aus dem Kontoauszug buchen (z. B. Kasse, Durchlaufkonto, Darlehen) – ohne Haushaltstitel.
- `catalog(store)` – Konten für Auswahllisten: Gruppe, Haushalt (mit Titel) ja/nein, direkt aus dem Kontoauszug bebuchbar, Hinweis.
- `accounts(store, year)` – Alle Konten mit Kontenrahmen-Gruppe, Haushaltstitel und Bewegungen im Jahr (eine Abfrage für alle Konten).
- `ledger(store, code, year, kv=…)` – Kontenblatt eines Kontos für ein Jahr; kv filtert auf einen Bereich.
- `source_label(source)`
- `ledger_csv(store, code, year, kv=…)` – Kontenblatt als CSV (Semikolon, Euro mit Komma, wie die übrigen Exporte).

### regelwerk.py

Regelwerk (ab 0.43, Wunsch Lennert Gust 21.11.2026): die Regeln aus Satzung und Finanzordnung, die das Programm anwendet.

Tests: test_regelwerk.py

- `zweiter_freitag_dezember(year)` – Rechnungsausgang (Satzung § 49 Abs. 4): bis zum zweiten Freitag im Dezember.
- `termine(cfg, today=…)` – Nächste Termine der Mitgliederverwaltung: Beitrittsstichtag, Rechnungsausgang, Beitragsstichtag, Liste an den LMA (§ 17).
- `regeln(cfg, today=…)` – Regelwerk für die Seite: Quellen, Bereiche, Regeln mit heutigem Wert und Stand, Termine, offene Fragen.

## Mitgliederverwaltung

### mitglieder.py

Mitglieder-Stammdaten: Vorname/Nachname und Anschrift in Feldern, Prüfung, Mitgliederliste aus Excel/CSV importieren.

Tabellen: `deleted_members`, `dues_rates`

Tests: test_doppelte.py, test_gliederung.py, test_kenntnis.py, test_mitgliedbank.py, test_mitglieder.py, test_mitgliederkonto.py, test_mitgliederverwaltung.py, test_ovabgleich.py, test_pruefung.py, test_wahlrecht.py

- `next_id(db)` – Nächste Mitglieds-id: nie eine schon einmal vergebene (auch nicht die eines gelöschten Mitglieds).
- `insert(db, v)` – Mitglied mit geprüften Werten (values) anlegen; liefert die id.
- `init(db)` – Neue Spalten anlegen und vorhandene Mitglieder einmalig aufteilen (additiv; name/address bleiben).
- `split_name(name)` – „Anna von Berg“ → („Anna“, „von Berg“), „Berg, Anna“ → („Anna“, „Berg“); ein einzelnes Wort ist der Nachname.
- `country(value)` – Land für die Anschrift: leer bei Deutschland (auch DEU, GER …) und bei einem Bundesland (Spalte „Land“ = Bundesland).
- `split_address(address)` – Mehrzeilige Anschrift → (Zusatz, Straße, PLZ, Ort, Land); Zeilen nach „PLZ Ort“ (z. B. Österreich) sind das Land.
- `compose(v)` – name und address aus den Einzelfeldern (für Rechnungen, PDF, E-Mail); Namenszusatz (z. B. „von“) vor dem Nachnamen.
- `left_day(value, joined)` – Austrittsdatum prüfen (auch künftig erlaubt, nie vor dem Eintritt) – eine Prüfung für Formular, Import und „Austritt eintragen“.
- `austritt(store, member_id, left_on, reason, actor)` – Austritt eintragen bzw. ändern (left_on = Datum, Grund optional) oder zurücknehmen (left_on leer) – ab 0.37.
- `notiz(store, member_id, text, actor)` – Notiz der Mitgliederverwaltung zum Mitglied speichern (ab 0.43; leer = löschen) – im Protokoll ohne den Text.
- `notizen_bereinigen(store, today=…)` – Notizen ausgetretener Mitglieder löschen (ab 0.47, DSGVO: der Zweck endet mit der Mitgliedschaft, keine Aufbewahrungspflicht)
- `verlauf(store, member_id, limit=…)` – Änderungen an einem Mitglied aus dem Protokoll (neueste zuerst): Zeit, wer, was – nur Feldnamen und Daten, keine Werte.
- `angaben(store, m)` – Alle Angaben eines Mitglieds als [(Bezeichnung, Wert)] in der Reihenfolge der Mitgliederverwaltung – leere weggelassen.
- `record_dues(db, member_id, old, new, year=…)` – Individuellen Jahresbeitrag ab einem Jahr festhalten (Tabelle dues_rates): ändert er sich – z. B. durch einen neuen Import der
- `flag(value, label)` – Ja/Nein-Angabe (J/N, ja/nein, 1/0, x) → 1/0; leer = 0.
- `naechste_nummer(db)` – Vorschlag für die Mitgliedsnummer eines neuen Mitglieds (ab 0.53): die höchste Nummer im häufigsten Format + 1, Breite und
- `values(data, kvs, ovs=…, inactive=…)` – Geprüfte Stammdaten eines Mitglieds. Ältere Aufrufe mit name/address werden aufgeteilt.
- `map_header(cells)` – Spalte → Feld; None, wenn die Zeile keine Kopfzeile einer Mitgliederliste ist.
- Klasse `PasswordNeeded` – Kennwortgeschützte Datei: state = 'required' (Kennwort fehlt) oder 'wrong' (Kennwort falsch) – die Oberfläche fragt danach.
- `unlock(raw, password=…)` – Kennwortgeschützte Excel-Datei (Office-Verschlüsselung, „Kennwort zum Öffnen“) im Arbeitsspeicher entschlüsseln.
- `read_table(raw, filename, mapper, password=…, missing=…)` – Erste Tabelle (Excel .xlsx/.xls, auch kennwortgeschützt, oder CSV) mit erkannter Kopfzeile:
- `kv_code(value, kvs)` – Kreisverband aus Kennziffer oder Namen („KV Hamburg-Nord“, „Altona/Elbvororte“, „5300“).
- `preview(store, raw, filename, password=…)` – Vorschau eines Imports: je Zeile neu / update / same / error, nichts wird gespeichert.
- `apply(store, prev, selected, actor)` – Ausgewählte Zeilen der Vorschau übernehmen (neu anlegen bzw. aktualisieren) – alles oder nichts.
- `delete_blockers(db, member_id)` – Gründe gegen das Löschen: [„3 Rechnungen“, …]; leer = löschbar.
- `check_delete(store, member_id, arrears_ok=…)` – Löschbar? Sperrt bei Buchhaltungsbezug; ein offener Rückstand (Soll nach Staffel ohne Zahlung) verlangt eine ausdrückliche Bestätigung.
- `delete(store, member_id, actor, arrears_ok=…)` – Mitglied ohne Buchhaltungsbezug endgültig löschen (vorher sichert der Aufrufer die Datenbank). Prüft in der Transaktion erneut.

### mitgliederlisten.py

Listen der Mitgliederverwaltung (ab 0.43, Wunsch Lennert Gust 21.11.2026): eine Auswahl aus der Mitgliederliste als CSV, als

Tests: test_mitgliedbank.py, test_mitgliederverwaltung.py

- `auswahl(store, ids)` – Mitglieder zu den Kennungen der Auswahl (sortiert nach Name) – prüft die Eingabe: Liste ganzer Zahlen, höchstens MAX, alle vorhanden.
- `postfaehig(m)` – Bekommt Post: vollständige Anschrift (Straße, PLZ, Ort) und kein Postausschluss.
- `anschrift(m)` – Zeilen für Etikett und Anschriftfeld: Anrede, Titel und Name, Adresszusätze, Straße, PLZ Ort, Land.
- `csv(store, members)` – Mitglieder der Auswahl mit Anschrift und Kontakt als CSV (Semikolon, Formelschutz wie die anderen Listen).
- `excel(store, members)` – Auswahl als Excel zum Weiterleiten an die Kreisverbände (ab 0.45): je Kreisverband eine eigene Datei – so bekommt kein
- `etiketten(members)` – Adressetiketten (Zweckform 3474: 3 × 8 Etiketten à 70 × 37 mm auf A4) – nur Mitglieder, die Post bekommen.
- `anschreiben(cfg, members, betreff, text, today=…)` – Ein Brief je Mitglied (DIN 5008): Absender, Anschriftfeld, Datum, Betreff, Briefanrede (sonst „Hallo Vorname,“), Text, Gruß.
- `datei(store, data, actor)` – Abruf der Oberfläche: {art, ids, betreff?, text?} → (Inhalt, Medientyp, Dateiname); im Protokoll nur Art und Anzahl.

### doppelte.py

Doppelte Mitglieder finden (ab 0.45, Wunsch Lennert Gust 21.11.2026: „eine Funktion, um doppelte Mitglieder zu finden“).

Tabellen: `mitglieder_keine_doppelten`

Tests: test_doppelte.py, test_mitgliedbank.py, test_ovabgleich.py

- `init(db)`
- `paare(members)` – {(a, b): {Grund, …}} für alle Paare möglicher Doppelter (a < b); members: Zeilen mit id, name, first_name, last_name, birth,
- `gruppen(store)` – Mögliche Doppelte als Gruppen: [{'ids': [...], 'gruende': [Text, …]}] – Paare mit „kein Duplikat“ zählen nicht.
- `kein_duplikat(store, ids, actor)` – Mitglieder einer Gruppe als „kein Duplikat“ bestätigen (alle Paare untereinander); im Protokoll nur die Kennungen.
- `vorschau(store, keep, drop, buchhaltung=…)` – Was beim Zusammenführen passiert: bleibendes und entfallendes Mitglied, ergänzte Angaben, Unterschiede, umgehängte Einträge,
- `pruefen(store, keep, drop, buchhaltung=…)` – Vorschau oder ValueError mit allen Hindernissen – vor der Sicherung und erneut in der Transaktion.
- `zusammenfuehren(store, keep, drop, actor, buchhaltung=…)` – Mitglied `drop` in `keep` aufgehen lassen (vorher sichert der Aufrufer die Datenbank): fehlende Angaben ergänzen, Rechnungen,

### gliederung.py

Gliederung: Kreisverbände (mit Nummer der CDU-Mitgliederverwaltung) und Ortsverbände als Stammdaten.

Tabellen: `ortsverbaende`

Tests: test_auswertung.py, test_doppelte.py, test_gliederung.py, test_kenntnis.py, test_opliste.py, test_ovabgleich.py, test_wahlrecht.py

- `digits(text)`
- `init(db)` – Tabelle der Ortsverbände und Nummer der Mitgliederverwaltung je Kreisverband (additiv).
- `fold(text)` – Vergleichsform eines Namens: ohne Groß-/Kleinschreibung, Akzente, Leer- und Satzzeichen („St. Georg / Borgfelde“ = „St.Georg/Borgfelde“).
- `clean_name(value)`
- `label(ov)` – Anzeige „12 Blankenese“; ohne Nummer nur der Name.
- `link_legacy(db)` – Einmalig (Merker in settings): Ortsverband-Angaben aus 0.24 (Textspalten ov_number/ov_name) in Ortsverbände überführen und
- `kvs(store)` – Kreisverbände in der festen Reihenfolge von core.KVS mit Nummer der Mitgliederverwaltung.
- `rows(db)`
- `ovs(store)` – Alle Ortsverbände mit Anzahl der zugeordneten Mitglieder (für Auswahl, Filter und Stammdaten).
- `ov_map(db)` – {id: Ortsverband} für die Prüfung der Mitgliedsdaten (mitglieder.values).
- `match(ovs_list, kv, number, name)` – Vorhandenen Ortsverband finden: mit Nummer über (Kreisverband, Nummer), ohne Nummer über den Namen.
- `ensure(db, kv, number, name)` – Ortsverband finden oder anlegen (innerhalb einer laufenden Transaktion des Aufrufers). Liefert (id, neu angelegt).
- `save_ov(store, data, actor, ov_id=…)` – Ortsverband anlegen oder ändern (Nummer, Name; Kreisverband nur ohne zugeordnete Mitglieder).
- `set_active(store, ov_id, active, actor)`
- `delete_ov(store, ov_id, actor)`
- `set_cdu_numbers(store, data, actor)` – Nummern der Mitgliederverwaltung je Kreisverband: {code: Nummer}; leer = keine Nummer. Jede Nummer höchstens einmal.
- `kv_by_number(store)` – {Nummer der Mitgliederverwaltung: Kostenstelle} für den Import.
- `resolve_kv(numbers, kvs_set, number_raw, name_raw)` – Kreisverband einer Importzeile: zuerst über die Nummer der Mitgliederverwaltung, sonst über den Namen.
- `map_header(cells)`
- `preview(store, raw, filename, password=…)` – Ortsverbände aus einer Datei wie dem Export der Mitgliederverwaltung (eine Zeile je Mitglied oder je Ortsverband):
- `apply_import(store, prev, actor)` – Neue Ortsverbände der Vorschau anlegen (vorhandene bleiben unverändert) – alles oder nichts.

### ovabgleich.py

Ortsverband nach der Adresse (ab 0.52, Wunsch Lennert Gust 22.11.2026): Gebiete der Ortsverbände, Vorschlag je Anschrift und

Tabellen: `ortsverband_gebiete`

Tests: test_ovabgleich.py

- `init(db)` – Tabelle der Gebiete (additiv): je Ortsverband die Stadtteile – Namen wie im amtlichen Adressbestand.
- `gebiete(db)` – {ov_id: [Stadtteil, …]} – nur Ortsverbände mit Gebiet.
- `stadtteile(data_dir)` – Stadtteile aus den amtlichen Adressdaten [{'name', 'bezirk', 'kv'}]; ohne Adressdaten leer.
- `namensvorschlag(ovs, teile, vorhanden)` – Gebiet aus dem Namen für aktive Ortsverbände ohne Gebiet – nur ein Vorschlag zum Bestätigen. {ov_id: [Stadtteil]}
- `gebiet_speichern(store, data_dir, ov_id, namen, actor)` – Stadtteile eines Ortsverbands festlegen (ersetzt die bisherigen). Mit Adressdaten muss jeder Stadtteil im Bezirk des
- `ovs_fuer(db, kv, stadtteil)` – Aktive Ortsverbände eines Kreisverbands, zu deren Gebiet der Stadtteil gehört (Vorschlag im Mitgliedsdialog und im Abgleich).
- `abgleich(store, data_dir, today=…)` – Alle aktiven Mitglieder mit dem Ortsverband nach ihrer Adresse vergleichen.
- `auswahl(aenderungen, fest)` – Eingabe der Übernahme prüfen (vor der Sicherung): [{id, ov, ov_alt, kv_alt}] und [id]. ov_alt/kv_alt = Stand, den die Person
- `uebernehmen(store, data_dir, aenderungen, fest, actor, today=…)` – Ausgewählte Vorschläge übernehmen – der Kreisverband folgt dem Ortsverband – und Mitglieder als „Ortsverband fest“ markieren.

### kenntnis.py

Kenntnisnahme von Änderungen an den Mitglieder-Stammdaten (ab 0.54, REGELN Nr. 35, Wunsch Lennert Gust 22.11.2026).

Tabellen: `mitglied_aenderungen`

Tests: test_kenntnis.py

- `init(db)` – Tabelle und Auslöser (additiv). Nach `mitglieder.init` aufrufen – die Auslöser nennen alle Spalten der Stammdaten.
- `felder()` – Angaben, die festgehalten werden können, mit Beschriftung – für die Auswahl „ausnehmen“ in den Einstellungen.
- `ohne(db)` – Angaben, die nicht festgehalten werden (Einstellung `kenntnis_ohne`, mit Komma getrennt).
- `an(db)` – Ist die Kenntnisnahme eingeschaltet (Einstellung `kenntnis`, Vorgabe an)?
- `zuordnen(db, actor, action, detail=…)` – Frisch festgehaltene Änderungen dieser Verbindung der Person und dem Vorgang zuordnen – ruft `Store.audit` bei jedem Eintrag.
- `kurz(db)` – Für die Gesamtdaten: eingeschaltet?, Kennungen der Mitglieder mit offener Änderung (Liste „Änderung offen“, Lage im Cockpit) und
- `liste(store, today=…)` – Offene und in den letzten `FRIST` Tagen abgehakte Änderungen je Mitglied; `stand` = letzte gesehene Zeile (für `abhaken`).
- `abhaken(store, punkte, bis, actor)` – Offene Punkte zur Kenntnis nehmen – je Mitglied genau den gezeigten Ausschnitt (einzeln geändert oder ein Sammelpunkt) und nur
- `bereinigen(store, today=…)` – Abgehakte Zeilen nach `FRIST` Tagen löschen; offene Zeilen ohne Unterschied (hin und zurück geändert) löschen; ist die

### klaerfaelle.py

Klärfälle der Mitgliederverwaltung (ab 0.31, Wunsch Lennert Gust 10.11.2026).

Tabellen: `dues_cases`

Tests: test_bereinigung.py, test_klaerfaelle.py, test_sepadatei.py

- Klasse `FromFile` – Angaben zu einem Klärfall, die das Programm selbst aus einer SEPA-Datei gelesen hat (sepadatei._targets): nur dann werden Herkunft,
- `init(db)`
- `clean(raw)` – Angaben zu einem neuen Klärfall ohne Zuordnung prüfen → „Person“ wie in beitraege._members (ohne Kreisverband: Landesverband).
- `insert(store, entry_id, case, year, amount, now, actor)` – Klärfall ohne Zuordnung anlegen (innerhalb der Transaktion des Aufrufers).
- `link_external(store, entry_id, external_id, x, year, amount, now, actor, reuse=…)` – Zu einer Zuordnung „nicht in der Mitgliederliste“ gehört ein Klärfall: bei geänderter Zuordnung derselben Person der bisherige
- `detach_externals(store, entry_id)` – Vor dem Ersetzen einer Zuordnung (beitraege.assign): Klärfälle der bisherigen Personen außerhalb der Liste merken und lösen.
- `close_detached(store, reuse, now, actor)` – Nach dem Ersetzen: Klärfälle, deren Person nicht mehr zugeordnet ist, sind erledigt (die Buchhaltung hat neu zugeordnet).
- `close_entry_cases(store, entry_id, now, actor)` – Offene Klärfälle ohne Zuordnung einer Buchungszeile erledigen – die Zeile wird vollständig neu aufgeteilt (SEPA-Datei).
- `acknowledge_returned(store, entry_id)` – Die Buchhaltung hat eine zurückgegebene Zahlung („kein Mitgliedsbeitrag“) wieder angefasst (neu zugeordnet oder erneut gemeldet):
- `number(row)`
- `items(store)` – Alle Klärfälle (ohne stornierte Buchungen), älteste Zahlung zuerst; offene mit Vorschlägen aus der Mitgliederliste.
- `counts(store, today=…)` – Kennzahlen für Start und Seitenleiste: offen, in Rückfrage, davon ohne Zuordnung, geklärt in den letzten 30 Tagen.
- `report(store, entry_id, data, actor)` – Buchhaltung: den offenen Teil einer gebuchten 8100-Zahlung als Klärfall an die Mitgliederverwaltung melden.
- `report_open(store, year, actor)` – Alle Zahlungen (des Jahres) ohne Mitglied auf einmal als Klärfälle melden; liefert die Anzahl. Zahlungen, die die Mitgliederverwaltung
- `resolve(store, case_id, data, actor)` – Mitgliederverwaltung klärt: {'member_id'} (mit 'new': neu angelegt), {'external': {name, number, kv, reason}} bzw. {'confirm': True}
- `update(store, case_id, data, actor)` – Notiz und Rückfrage beim Kreisverband: {'note'}, {'status': 'rueckfrage', 'kv'} bzw. {'status': 'offen'}.

### wahlrecht.py

Mitgliederstatus und Wahlberechtigung (ab 0.37, Wunsch Lennert Gust 19.11.2026): wahlberechtigt / nicht wahlberechtigt / ausgetreten.

Tests: test_wahlrecht.py

- `letzter_werktag_januar(year)` – Beitragsstichtag (§ 15 Abs. 3): letzter Werktag im Januar. Werktag = Montag bis Samstag, kein Feiertag – Ende Januar gibt es
- `beitritt_vor(beitrag)` – Beitrittsstichtag: der 30. November vor dem Beitragsstichtag.
- `stichtage(cfg, today=…)` – Stichtage der nächsten turnusmäßigen Wahlen: aus den Einstellungen, leer = Vorschlag (letzter Werktag im Januar, 30.11. davor).
- `pruefe_einstellungen(cfg, today=…)` – Eingetragener Beitrittsstichtag muss vor dem geltenden Beitragsstichtag liegen (§ 37 Abs. 1 Nr. 1). Verglichen wird mit dem
- `auswertung(store, stichtag=…, today=…, book=…)` – Status je Mitglied mit Gründen, Zahlen je Kreis- und Ortsverband. stichtag (JJJJ-MM-TT) = Liste des LMA zu diesem Tag, der
- `laufend(store, book=…, today=…)` – Laufender Status für die Mitgliederliste: {Mitglied: {'status', 'gruende'}} – leer, solange das Jahr vor `wahlrecht_ab` liegt
- `pdf(store, stichtag=…, today=…)` – Bericht nach § 17 Abs. 1 Nr. 3: Zahlen je Kreis- und Ortsverband, dann je Ortsverband die Mitglieder mit Status und Grund.
- `csv(store, stichtag=…, today=…)` – Mitglieder am Stichtag mit Status und Grund als CSV (Semikolon, Formelschutz wie die anderen Listen).

### karte.py

Karte der Mitglieder (ab 0.32, nur lesend): jedes aktive Mitglied an seiner Anschrift, die Kreisverbände als Gebiete.

Tests: test_karte.py

- `gebiete()` – Bezirke (mit Kreisverband) und Stadtteile als Flächen – öffentliche Daten, einmal gelesen.
- `data(store, data_dir, today=…)` – Lage aller aktiven Mitglieder und die Listen „zu prüfen“. Ohne Adressdaten: available False.

### adressen.py

Amtliche Hamburger Adressen (ab 0.32) – Grundlage für die Karte und die Adressprüfung beim Mitglied.

Tests: test_http.py, test_karte.py, test_ovabgleich.py

- `norm_street(name)` – Straßenname zum Vergleichen: „Musterstr.“, „Muster Straße“ und „Musterstrasse“ sind dasselbe.
- `split_street(text)` – „Musterstraße 12 a“ → ('Musterstraße', 12, 'a'); ohne Hausnummer (name, None, '').
- `candidates(text)` – Mögliche Lesarten eines Anschriftfelds, die wahrscheinlichste zuerst: (Straßenname, Hausnummer oder None, Zusatz, Text nach der Nummer).
- `is_hamburg(city)`
- `path(data_dir)`
- Klasse `_Writer` – Schreibt den Bestand in eine neue Datei (innerhalb von build bzw. download).
  - `_Writer.add(features)` – GeoJSON-Features der Schnittstelle; Unbrauchbares (ohne Straße, Nummer, PLZ oder Koordinate in Hamburg) wird übergangen.
  - `_Writer.finish(stand, reported=…)`
- `build(target, features, stand=…)` – Bestand aus Features anlegen (Tests, Probeläufe) – ersetzt die Datei erst, wenn alles geschrieben ist.
- Klasse `Incomplete` – Abruf abgebrochen, weil zu wenig oder zu viel ankam – der Text ist für die Oberfläche gedacht.
- Klasse `_SameHost`
  - `_SameHost.redirect_request(req, fp, code, msg, headers, newurl)`
- `fetch_page(offset, version=…)` – Eine Seite des Adressbestands (ohne Suchbegriff – immer der ganze Bestand der Reihe nach).
- `download(data_dir, fetch=…, progress=…, minimum=…)` – Ganzen Bestand abrufen und die Datei ersetzen; bricht ohne Änderung ab, wenn zu wenig ankommt.
- `start(data_dir, fetch=…, version=…, wait=…)` – Abruf im Hintergrund starten (läuft schon einer, bleibt es bei dem); Stand über status().
- `status(data_dir)` – Stand für die Oberfläche: vorhanden?, Stand, Anzahl, laufender Abruf.
- `connect(data_dir)` – Lesende Verbindung zur Adressdatei oder None, wenn sie (noch) fehlt bzw. unlesbar ist.
- Klasse `Index` – Nachschlagen in der Adressdatei – eine Instanz je Anfrage (Karte: viele Mitglieder nacheinander).
  - `Index.close()`
  - `Index.norms()`
  - `Index.places()` – Namen der Bezirke und Stadtteile (als Ort in der Anschrift gilt das als Hamburg), auch ohne „Hamburg-“ davor.
  - `Index.zips()`
  - `Index.stadtteile()` – Stadtteile des Bestands mit Bezirk und Kreisverband, sortiert: [{'name', 'bezirk', 'kv'}] (Gebiete der Ortsverbände, ab 0.52).
  - `Index.lookup(street, plz=…, city=…)` – Anschrift prüfen. status: 'haus' (Hausnummer gefunden), 'strasse' (Straße gefunden, Hausnummer nicht – Lage ungefähr),
  - `Index.streets(query, limit=…)` – Straßensuche für die Karte: Namen, die mit dem Suchtext beginnen (sonst: ihn enthalten), mit Lage.
- `index(data_dir)`
- `check(data_dir, street, plz=…, city=…)` – Eine Anschrift prüfen (Mitgliedsdialog); ohne Adressdatei: status 'keine_daten'.

### auswertung.py

Auswertung der Mitgliederdaten (Wunsch Lennert Gust 10.11.2026: „ein Punkt Auswertung, wo ich die Mitgliederdaten auswerten kann“).

Tests: test_auswertung.py

- `cohorts(pool, day)` – Bindung je Eintrittsjahr (die letzten COHORT_YEARS Jahre bis zum Tag): Eintritte und in `kept` der Anteil, der 1 bis KEPT_YEARS Jahre
- `left_by_tenure(left)` – Austritte nach vollendeten Jahren Mitgliedschaft am Austrittstag (LEFT_GROUPS) und wie viele am Tag danach 35 oder älter waren
- `age_limit_ahead(active, day)` – Wie viele der Mitglieder im Rest des Jahres (nach dem Tag) und in jedem der nächsten AHEAD_YEARS Jahre 35 werden. Reine Zahlen.
- `recruited(joined)` – Eintritte mit eingetragenem Werber (Mitglied `geworben_von` oder Freitext `geworben_text`): Anzahl und Anteil. Reine Zahlen.
- `report(store, stichtag=…, kv=…, ov=…, today=…)` – Auswertung zum Stichtag (JJJJ-MM-TT); kv: Kostenstelle, ov: id oder 'none' (ohne Ortsverband).
- `redact(r, amounts=…, names=…)` – Auswertung für eine Rolle (rollen.py, ab 0.34): ohne Buchhaltung keine Beitragsbeträge, ohne Mitgliederverwaltung
- `pdf(store, stichtag=…, kv=…, ov=…, amounts=…)` – Mitgliederstatistik als PDF – nur Zahlen, keine Namen (für Vorstand und Gremien); `amounts=False`: ohne Beitragsbeträge.
- `csv(store, stichtag=…, kv=…, ov=…, amounts=…)` – Kennzahlen je Kreis- und Ortsverband, Altersgruppen, Eintrittsalter, Geschlecht, Ein- und Austritte je Monat als CSV (nur Zahlen);

## Mitgliedsbeiträge

### beitraege.py

Mitgliedsbeiträge ohne Beitragsrechnung (Konto 8100) einem Mitglied zuordnen.

Tabellen: `dues_assignments`, `dues_external`

Tests: test_beitraege.py, test_bereinigung.py, test_kasse.py, test_klaerfaelle.py, test_mitgliederkonto.py, test_sepadatei.py

- `init(db)`
- `number_key(number)` – Mitgliedsnummer zum Vergleichen: nur die Ziffernfolgen ohne führende Nullen (0012345 = 12345 = JU-12345 ≠ 12346);
- `external(store, raw)` – Person ohne Eintrag in der Mitgliederliste (z. B. ausgetreten, Daten gelöscht): Name, Nummer (falls bekannt), Kreisverband, Grund.
- `split_entries(store, part, amount, title)` – Buchungszeilen einer Beitragszahlung aus dem Kontoauszug: je Mitglied eine 8100-Zeile (Bereich = Kreisverband).
- `audit_parts(parts)` – Teilbeträge einer Buchung für das Änderungsprotokoll: Personen außerhalb der Mitgliederliste ohne Namen (nur Nummer),
- `attach(store, entries, year, actor)` – Nach dem Buchen: Zuordnung der 8100-Zeilen festhalten (entry_id setzt Store.journal).
- `entries(store, year=…, only_open=…)` – 8100-Zahlungen aus dem Kontoauszug mit Zuordnung (stornierte Buchungen nicht). Je Zahlung: members (zugeordnet), cases und
- `pending(store, year=…)` – Zahlungen, die noch nicht vollständig einem Mitglied (bzw. einer Person außerhalb der Liste) zugeordnet sind – offen bei der
- `suggest(store, entry, members=…, index=…)` – Mitglieder, deren Name (bzw. Mitgliedsnummer) im Namen oder Verwendungszweck der Zahlung steht – stärkste zuerst.
- `learned(store, items=…)` – Zahler (Name laut Kontoauszug), deren Zahlungen bisher genau einem Mitglied zugeordnet sind – z. B. Eltern, die für ihr Kind
- `open_list(store, year=…)` – Liste „Beiträge zuordnen“: offene 8100-Zahlungen mit Vorschlägen nach Name bzw. Mitgliedsnummer, sonst nach früherer
- `assign(store, entry_id, items, actor)` – Bereits gebuchte 8100-Zeile (z. B. Excel-Übernahme) Mitgliedern zuordnen; ersetzt eine frühere Zuordnung. Ein Teil, der als
- `assign_in_transaction(store, entry_id, items, actor, replace_cases=…)` – Wie assign, innerhalb einer schon offenen Transaktion des Aufrufers (z. B. SEPA-Sammler aufteilen; replace_cases: die Zeile
- `unique_suggestion(item)` – Eindeutiger Vorschlag für die Sammelübernahme: genau ein Mitglied mit der höchsten Punktzahl, dessen Nachname in der
- `assign_suggested(store, year=…, actor=…)` – Alle offenen Zahlungen des Jahres mit eindeutigem Vorschlag zuordnen (ganzer Betrag an ein Mitglied).
- `paid_by_kv(store, kv, until=…)` – Zugeordnete Beitragszahlungen eines Kreisverbands je Beitragsjahr (Ist, ohne Stornos); until: Zahlungen bis zu diesem Tag.
- `year_mismatch(store)` – Zugeordnete Zahlungen, deren Beitragsjahr (Haushaltsjahr der Buchung) nicht dem Zahlungsjahr entspricht – zählen nicht.
- `by_member(store, member_id)` – Beitragszahlungen eines Mitglieds ohne Beitragsrechnung (für das Beitragskonto).
- `summary(store, year)` – Je Mitglied gezahlte Beiträge eines Jahres (Rechnungszahlungen und zugeordnete 8100-Zahlungen) – für Suche und Liste.

### mitgliederkonto.py

Mitgliederbuchhaltung: Beitragskonto je Mitglied (Nebenbuch zu den Mitgliedsbeiträgen).

Tabellen: `dues_targets`

Tests: test_beitraege.py, test_bereinigung.py, test_doppelte.py, test_kasse.py, test_klaerfaelle.py, test_mitgliederkonto.py, test_mitgliederverwaltung.py

- `init(db)`
- `first_year(store)` – Erstes Jahr der Mitgliederkonten: Datenbeginn (ältere Daten sind gelöscht), sonst KV-Finanzrahmen-Beginn.
- `target(store, m, year, ctx)` – Soll eines Mitglieds für ein Jahr: {'amount': Cent oder None (unklar), 'basis', 'source', 'issue', 'due'} oder None (kein Beitrag).
- `member_years(m, ctx_first, this_year)` – Mögliche Beitragsjahre eines Mitglieds: ab Datenbeginn bzw. Eintritt bis zum laufenden Jahr bzw. Austrittsjahr.
- `account(store, member_id, today=…)` – Beitragskonto eines Mitglieds: Jahre (Soll, Grundlage, gezahlt, offen, Stand 31.12.) und Buchungen mit laufendem Saldo.
- `overview(store, year=…, today=…)` – Alle Mitglieder: Soll und gezahlt im Jahr, Saldo (heute) – für die Mitgliederliste, Rückstände und Abstimmung.
- `jahr_stand(y)` – Stand eines Beitragsjahres ohne Betrag (Akte und Mitgliederliste, 0.43): klaeren / frei / spaeter (noch nicht fällig) / offen /
- `returned_after(store, after, years, today=…)` – Mitglieder, bei denen nach dem Tag `after` (bis heute) eine Rücklastschrift oder Rückzahlung für eines der Beitragsjahre gebucht ist.
- `reconciliation(store, year, until=…)` – Abstimmung Hauptbuch ↔ Mitgliederkonten für ein Haushaltsjahr (nur Zahlungen, ohne Stornos; until: Zahlungen bis zu diesem Tag).
- `summary(store, year=…, today=…, rows=…)` – Kennzahlen der Mitgliederbuchhaltung für ein Jahr.
- `arrears(store, kv=…, today=…)` – Rückstandsliste (Stand heute): Mitglieder mit negativem Saldo, nach Kreisverband und Name.
- `issues(store, kv=…, today=…)` – Mitglieder, deren Soll sich nicht berechnen lässt (z. B. Austrittsjahr, fehlende Beitragsregel) – Soll festlegen.
- `set_target(store, member_id, year, amount, reason, actor)` – Soll eines Jahres von Hand festlegen (Betrag ≥ 0 mit Grund) – geht Beitragsrechnung und Staffel vor.
- `remove_target(store, member_id, year, actor)` – Festgelegtes Soll entfernen: es gilt wieder Beitragsrechnung bzw. Staffel.
- `overview_csv(store, year, kv=…, today=…)` – Mitgliederkonten eines Jahres als CSV (für Kassenprüfung und Kreisverbände).

### opliste.py

OP-Listen (offene Posten) der Mitgliedsbeiträge je Kreisverband – Wunsch Lennert Gust 10.11.2026: Die Kreisverbände sollen sehen,

Tests: test_mitgliederkonto.py, test_opliste.py

- `items(store, kv=…, ov=…, today=…)` – Offene Posten je Mitglied: offene Beitragsjahre (Jahr, Grundlage, Soll, gezahlt, offen, fällig seit), Erinnerungen, Lastschrift.
- `pdf(store, kv=…, ov=…, today=…)` – OP-Liste als PDF (A4 quer): je Kreisverband ein Abschnitt, je Mitglied eine Zeile mit offenen Jahren, Kontakt und Hinweis.
- `csv(store, kv=…, ov=…, today=…)` – OP-Liste als CSV: eine Zeile je Mitglied und offenem Beitragsjahr.
- `zip_all(store, today=…)` – Alle OP-Listen zum Verteilen: je Kreisverband (mit Mitgliedern) ein PDF und eine CSV.

### sepadatei.py

SEPA-Dateien einlesen: Sammelbuchungen (z. B. Lastschrifteinzug der Mitgliedsbeiträge) auf Mitglieder und Rechnungen aufteilen.

Tabellen: `sepa_files`, `sepa_mandates`

Tests: test_bereinigung.py, test_sepadatei.py

- `init(db)`
- Klasse `_Doctype`
- `parse(raw, filename)` – SEPA-Datei (XML oder ZIP mit XML-Dateien) → {'kind', 'batches': [{ref, date, total, items, note}]} (nur Gutschriften/Einzüge).
- `fold(text)` – Vergleichsform für Namen aus SEPA-Dateien (dort oft ohne Umlaute, groß geschrieben): Wörter ohne Akzente, ä → ae …
- `learn_key(item)` – Schlüssel einer gemerkten Mandatsreferenz: Gläubiger-ID der Datei und Referenz unverändert.
- `match(item, ix, day=…)` – Ziel einer Einzelzahlung: {'member_id', 'invoice_id', 'invoice', 'reason', 'sure', 'bulk'} – leer, wenn nichts passt.
- `candidates(store, batch)` – Sammelbuchung(en) im Kontoauszug: gleicher Betrag, Datum passend. mode 'book' (offen/geparkt) oder 'assign' (eine 8100-Zeile).
- `preview(store, raw, filename)` – Datei lesen, Einzelzahlungen zuordnen, Sammelbuchung suchen – nichts wird gespeichert.
- Klasse `_TooLong`
- `payers_in_pdf(raw, items)` – Spalte „Zahler“ des Begleitzettels je Einzelzahlung: {EndToEndId: Zahler}. Eine Zeile wird über die EndToEndId (Textstück) erkannt;
- `add_payers(store, prev, raw)` – Begleitzettel (PDF) zur Lastschriftdatei einlesen: Spalte „Zahler“ je Einzelzahlung (payers_in_pdf), danach neu zuordnen.
- `public(prev)` – Vorschau für die Oberfläche: ohne vollständige IBAN.
- `validate(store, prev, batch_no, bank_id, choices)` – Wie apply, ohne zu schreiben – damit ein Fehler gemeldet wird, bevor eine Sicherung angelegt wird.
- `apply(store, prev, batch_no, bank_id, choices, actor)` – Sammler aufteilen. choices: {Nr. der Einzelzahlung: {'member_id'}, {'invoice_id'} oder {'external': {name, number, kv, reason}}
- `on_bank_reversed(store, bank_id)` – Storno einer aufgeteilten Sammelbuchung: dabei gemerkte Mandatsreferenzen gelten nicht mehr (innerhalb der Storno-Transaktion).
- `learn_one(store, creditor, mandate, member_id, now, actor)` – Mandatsreferenz aus einem geklärten Klärfall merken (member_id) bzw. eine gemerkte Zuordnung löschen (None: Person steht nicht
- `learned_for(store, member_id)` – Gemerkte Mandatsreferenzen eines Mitglieds (Ansicht im Mitglied, dort einzeln entfernbar).
- `forget(store, creditor, mandate, actor)`
- `set_stored(store, file_id, name)`
- `files_for_year(store, year)` – Belege (SEPA-Dateien) zu Sammelbuchungen eines Jahres – für das Kassenprüfer-Paket.

### lastschrift.py

SEPA-Basislastschrift (CORE) für Mitgliedsbeiträge – Mandate, Vorabankündigung, Lastschriftdatei pain.008.001.08.

Tabellen: `debit_batches`, `debit_notices`, `debits`, `mandates`

Tests: test_lastschrift.py, test_mitgliedbank.py, test_sepadatei.py, test_wahlrecht.py

- `init(db)`
- `creditor_id_valid(value)` – Gläubiger-Identifikationsnummer: Land, Prüfziffer (ISO 7064 Mod 97, ohne Geschäftsbereich), Geschäftsbereich, Kennung.
- `masked(iban)`
- `sepa_text(value, limit)` – Text im SEPA-Zeichensatz: Umlaute umschreiben, Akzente entfernen, Unzulässiges durch Leerzeichen ersetzen.
- `business_day(day)` – Nächster TARGET-Geschäftstag ab day (Wochenende, Neujahr, Karfreitag, Ostermontag, 1. Mai, 25./26. Dezember).
- `collection_date(cfg, due, issued=…)` – Einzugsdatum für eine neue Rechnung: frühestens Fälligkeit und Rechnungsdatum + Vorabankündigungsfrist.
- `enabled(cfg)`
- `active_mandate(store, member_id)`
- `last_use(store, mandate, exclude=…)` – Letzte Nutzung (eingereichte oder geplante Lastschrift, sonst Unterschrift bzw. bekannte Nutzung außerhalb).
- `expires(store, mandate, exclude=…)` – Letzter möglicher Einzugstag: 36 Monate nach der letzten Nutzung (exclude: diese Lastschrift nicht mitzählen).
- `mandat_gueltig(store, today=…, rows=…)` – {Mitglied: IBAN} der aktiven, nicht verfallenen Mandate (ab 0.53, Liste „IBAN ohne Mandat“); rows = schon gelesene mandates().
- `mandates(store)`
- `create_mandate(store, member_id, data, actor)` – Unterschriebenes Mandat erfassen. Ein aktives Mandat je Mitglied; Kontowechsel = neues Mandat.
- `revoke_mandate(store, mandate_id, reason, actor)`
- `debit_snapshot(store, cfg, member, due, issued)` – Vorabankündigung für die Rechnung (wird mit der Rechnung festgeschrieben) oder None ohne aktives Mandat.
- `debit_state(store, invoice_id)` – 'pending' (in einer Lastschriftdatei), 'returned' (zuletzt zurückgegeben) oder None.
- `earliest(today=…)` – Frühestes Einzugsdatum für eine heute erstellte Datei (Vorlage bis einen Bankarbeitstag vorher).
- `notified_on(store, invoice_id)` – Versand der Vorabankündigung: erste erfolgreich versendete Rechnungs-E-Mail oder eingetragener Postversand.
- `info(store, inv, today=…)` – Lastschriftstand einer Rechnung mit Vorabankündigung – oder None ohne Lastschrift.
- `payable_by(inv)` – Zahlungsfrist für PDF und E-Mail: Fälligkeit – oder die feste neue Frist, wenn die Lastschrift entfallen ist
- `record_notice(store, invoice_id, sent_on, actor)` – Postversand der Rechnung mit Vorabankündigung eintragen (für Mitglieder ohne E-Mail) – nur solange sie noch
- `collectable(store, today=…)` – Rechnungen mit Vorabankündigung, die eingezogen werden können (reason None) – sonst mit Grund.
- `create_batch(store, ids, actor)` – Lastschriftdatei für die gewählten Rechnungen anlegen (Status „erstellt“). Liefert die Batch-ID.
- `issues(store, batch_id, today=…)` – Erneute Prüfung einer noch nicht eingereichten Datei: debit_id → Grund, warum diese Lastschrift nicht mehr raus darf.
- `batches(store, today=…)`
- `remove_debit(store, debit_id, actor)` – Eine Lastschrift aus einer noch nie heruntergeladenen Datei nehmen – eine heruntergeladene Datei bleibt, wie sie ist.
- `set_batch(store, batch_id, status, actor, reason=…)` – Datei als eingereicht markieren oder verwerfen.
- `reject_debit(store, debit_id, reason, actor)` – Einzelne Lastschrift einer eingereichten Datei, die die Bank nicht ausgeführt hat (ohne Geldfluss): die Rechnung
- `on_invoice_payment(store, invoice_id, amount, day)` – Aus core.book: Rückbuchung genau des eingezogenen Betrags = Rücklastschrift (eine andere Erstattung nicht).
- `on_payment_reversed(store, invoice_id, amount, day)` – Aus core.reverse_bank: Zuordnung einer Rücklastschrift storniert → Lastschrift gilt wieder als eingezogen.
- `download(store, batch_id, actor)` – Datei zum Hochladen: nur solange nicht eingereicht und ohne Befund der erneuten Prüfung; Download wird vermerkt.
- `xml_file(store, batch_id)` – pain.008.001.08 für eine Lastschriftdatei (je Einzugsdatum und Gläubiger-ID ein Zahlungsblock, Einzelbuchung gewünscht).
- `filename(store, batch_id)`
- `mandate_pdf(store, member_id=…, member_ids=…, mit_mandat=…)` – SEPA-Lastschriftmandat zum Unterschreiben (vorausgefüllt mit Mitglied, Bankdaten des Mitglieds und ggf. Mandat);

## Buchhaltung

### bankimport.py

Kontoauszüge (CSV oder Excel .xls) in ein einheitliches Zeilenformat überführen.

Tests: test_import.py

- `map_header(cells)` – Ordnet Spaltenüberschriften Feldern zu. Liefert {feld: spaltenindex} oder None.
- `split_combined(text)` – Leitet bei Bankexporten ohne Namensspalte den Zahlungspartner aus dem Verwendungszweck ab.
- `parse_csv(raw)`
- `parse_xls(raw)`
- `parse_statement(raw, filename=…)` – Erkennt das Format am Dateiinhalt und liefert die Datenzeilen.

### uebernahme.py

Übernahme der vorkontierten Umsätze aus der Excel-Masterdatei („Umsatz JJJJ“-Blätter).

Tests: test_bereinigung.py, test_uebernahme.py

- `fingerprint(booked, amount, name, reference)` – Gleiche Berechnung wie beim Kontoauszugsimport – erkennt dieselben Umsätze wieder.
- `plan_row(code, amount, kst)` – Ermittelt die Buchung für einen Haushaltposten: (status, account, kv, title, hinweis).
- `parse(raw)`
- `words(text)` – Aussagekräftige Wörter: Namensbestandteile ab 3 Buchstaben, IBAN und lange Nummern (Referenz) ab 6 Ziffern.
- `strong(common)` – Starker Bezug: gemeinsame IBAN bzw. lange Nummer oder mindestens zwei gemeinsame Namenswörter.
- `match_loose(items, banks, days=…)` – Excel-Zeilen ohne Fingerabdruck-Treffer den freien Bankumsätzen zuordnen.
- `statement_spans(banks, statement_files=…, merge_days=…)` – Zeiträume der importierten Kontoauszüge, zusammengefasst: [(von, bis)].
- `preview(store, raw, filename)`
- `execute(store, data, actor)` – Legt alle Zeilen der Vorschau in einer Transaktion an. Gibt Zähler je Status zurück.

### workflow.py

Local, opt-in booking examples and auditable suspense-account transfers.

Tabellen: `booking_examples`

Tests: test_beitraege.py, test_import.py, test_kasse.py, test_kvbudget.py, test_storno.py, test_umsatz_ungueltig.py, test_umsatzakte.py

- `init(db)`
- `normalized(value)` – Verwendungszweck ohne wechselnde Teile: Wörter mit Ziffern (Daten, Referenzen, IBAN) werden zu #.
- `partner(b)`
- `remember(store, b, parts, actor)`
- `suggest(store, bid)`
- `park(store, bid, reason, actor)`
- `ungueltig(store, bid, reason, actor)` – Offenen Kontoumsatz mit Grund für ungültig erklären (ab 0.55, Wunsch Lennert Gust 23.11.2026: ein Umsatz war nach dem Import
- `gueltig(store, bid, actor)` – Einen für ungültig erklärten Kontoumsatz wieder öffnen (Irrtum) – dieselbe Datei lässt sich nicht noch einmal importieren.
- `forget(store, bid, actor)`
- `details(store, year)`

### umsatzakte.py

Notiz und angeheftete Belege am Kontoumsatz (ab 0.58, REGELN Nr. 40, Wunsch Lennert Gust 23.11.2026).

Tabellen: `umsatz_belege`, `umsatz_notizen`

Tests: test_umsatzakte.py

- `init(db)`
- `ergaenzen(store, rows)` – Umsätze der Gesamtdaten um das ergänzen, was die Liste der Kontoumsätze zeigt – ohne Notiztext und Dateinamen:
- `stand(store)` – Stand des Kontoauszugs für den Satz über der Liste: `am` = letzter Import (Zeitpunkt), `bis` = jüngster Buchungstag.
- `ohne_beleg(store, year)` – Anzahl verbuchter Ausgaben eines Jahres ohne Beleg (Regel `OHNE_BELEG`) – für das Prüfpaket.
- `akte(store, bid)` – Notiz (Text, wer, wann) und Belege (Kennung, Name, Größe, wer, wann) eines Kontoumsatzes – Abruf für die Oberfläche.
- `notiz(store, bid, text, actor)` – Notiz zum Kontoumsatz speichern (leer = löschen) – im Protokoll ohne den Text. → gespeicherte Notiz oder None.
- `datei(name, content_b64)` – Belegdatei prüfen wie alle Belege (`verbindlichkeiten.check_file`), aber ohne E-Rechnung: nur PDF, PNG, JPG. → (Endung, Bytes)
- `anheften(store, bid, name, suffix, size, actor, speichern)` – Beleg am Kontoumsatz eintragen und die Datei über `speichern(Kennung, Nummer, Dateiname)` ablegen lassen
- `beleg(store, rid, folder)` – Angehefteter Beleg mit dem Pfad seiner Datei in `folder` (Daten/Belege) – `pfad` ist None, wenn die Datei fehlt oder nicht
- `entfernen(store, rid, folder, actor)` – Beleg vom Kontoumsatz entfernen: erst die Datei in `folder` wirklich löschen, dann den Eintrag; steht im Protokoll.
- `fuer_jahr(store, year)` – Notizen und Belege der Kontoumsätze eines Jahres für das Prüfpaket: `{Umsatz: {'notiz': Text, 'belege': [Zeilen]}}`.

### kasse.py

Kassenbuch (Bargeld) – Kassenbelege werden wie Bankumsätze verbucht.

Tests: test_kasse.py, test_klaerfaelle.py, test_rpj.py, test_umsatz_ungueltig.py, test_umsatzakte.py

- `init(db)`
- `ledger_account(b)` – Gegenkonto eines Umsatzes: Kasse für Kassenbelege, sonst Bank.
- `start(store)` – Beginn des Kassenbuchs (Tag des Anfangsbestands) oder None, solange keine Kasse eingerichtet ist.
- `opening(store)` – Gültiger Anfangsbestand {'day', 'amount', 'journal'} oder None.
- `set_opening(store, day, amount, actor)` – Anfangsbestand der Kasse festlegen (Kasse an 9100 Eröffnungsbilanz); eine frühere Festlegung wird gegengebucht.
- `next_number(store, year)` – Belegnummer K-JJJJ-nnnn, fortlaufend je Jahr.
- `timeline(store, extra=…)` – Kassenbewegungen in zeitlicher Folge mit Bestand: Buchungen auf 1400 (Kassenbelege, Kontoauszug, Anfangsbestand) und noch
- `balance(store, until=…)` – Kassenbestand am Ende eines Tages (Standard: alles).
- `check_after_booking(store, storno=…)` – Nach einer Buchung auf 1400 (z. B. aus dem Kontoauszug): Bestand darf nie negativ werden – nur bei eingerichteter Kasse.
- `create(store, data, actor, attachment_suffix=…)` – Kassenbeleg erfassen. Einnahme/Ausgabe bleiben offen und werden im Buchungsplatz der Kasse verbucht; Geldtransit wird sofort
- `void(store, bid, reason, actor)` – Offenen Kassenbeleg mit Grund für ungültig erklären (Tippfehler, doppelt erfasst) – er bleibt sichtbar, zählt aber nicht.
- `summary(store)` – Kurzüberblick für die Oberfläche: Beginn, Bestand heute, offene Belege, Geldtransit.
- `transit_open(store, until=…)` – Saldo des Durchlaufkontos 2380 (Geldtransit) am Ende eines Tages: ≠ 0, solange Abhebung/Einzahlung nur auf einer Seite
- `book(store, year)` – Kassenbuch eines Jahres: Übertrag, Bewegungen mit Beleg, Kontierung und Bestand; ungültige Belege nur zur Information.
- `balance_today(store)`
- `is_transit(b)` – Geldtransit (Abhebung/Einzahlung) – braucht keinen eigenen Beleg; die Gegenseite steht im Kontoauszug.
- `csv(store, year)` – Kassenbuch als CSV (Kassenprüfung).

### verbindlichkeiten.py

Eingangsrechnungen (Verbindlichkeiten), Zahlungsvorschlag und Abgleich mit Kontoumsätzen.

Tabellen: `bill_lines`, `bill_payments`, `bills`

Tests: test_berichte.py, test_erechnung.py, test_erstattung.py, test_freigabe.py, test_haushaltsposten.py, test_kasse.py, test_lastschrift.py, test_pruefung.py, test_rpj.py, test_storno.py, test_uebernahme.py, test_umsatzakte.py, test_verbindlichkeiten.py

- `invoice_numbers(text)` – Rechnungsnummern in beliebiger Schreibweise, normalisiert auf B-JJJJ-nnnnn.
- `init(db)`
- `paid(store, bill_id)`
- `bills(store)`
- `pending_in_bank(store)` – Eingangsrechnungen, deren Abbuchung schon im Kontoauszug steht, aber noch nicht zugeordnet ist (Rechnung → Datum).
- `liability_account(store)` – Verbindlichkeitenkonto für neue Eingangsrechnungen laut Stammdaten (Standard 1600).
- `next_number(store, day=…)`
- `check_file(filename, content_b64)` – Beleg prüfen: PDF/PNG/JPEG oder E-Rechnung als XML, höchstens 10 MB, Inhalt passt zur Endung. Liefert (Endung, Bytes).
- `duplicates(store, v, total)` – Bereits erfasste (nicht stornierte) Eingangsrechnungen, die dieselbe sein könnten.
- `create(store, data, actor, attachment_suffix=…)` – Eingangsrechnung erfassen und buchen (Aufwand an Verbindlichkeiten). Liefert (id, Belegnummer, Dateiname).
- `cancel(store, bill_id, actor)`
- `mark_transferred(store, ids, actor, undo=…)`
- `transfer(store, bill_id)` – Überweisungsdaten für das Online-Banking (offener Betrag).
- `release(store, bill_id, actor)` – Nach einer stornierten Zahlung: geprüft, Rechnung darf wieder in den Zahlungsvorschlag.
- `on_bank_reversed(store, bank_id)` – Zahlung storniert: betroffene Eingangsrechnungen sperren (nicht automatisch erneut überweisen).
- `write_off(store, bill_id, reason, actor)` – Restbetrag ausbuchen (z. B. Skonto, Kürzung): mindert den Aufwand, Verbindlichkeit wird ausgeglichen.
- `book_part(store, bank_id, part_bill_id, amount)` – Teil einer Bankzuordnung: Abbuchung bezahlt Eingangsrechnung. amount in Cent (negativ). Liefert Buchungszeile.
- `bill_candidates(store, bank)` – Offene Eingangsrechnungen, deren offener Betrag genau zur Abbuchung passt – mit Begründung, beste zuerst.
- `unique_bill(store, bank)` – Genau eine stark passende Eingangsrechnung für diese Abbuchung, sonst None.
- `invoice_candidates(store, bank)` – Offene Ausgangsrechnungen, deren Nummer im Verwendungszweck steht und deren offener Betrag genau passt.
- `invoices_in_bank(store)` – Ausgangsrechnungen, deren Nummer in einem noch nicht zugeordneten Zahlungseingang steht (Nummer → Datum).
- `incoming_hint(store)` – Zahlungserinnerung und Lastschrift: Hinweis auf einen noch nicht zugeordneten Zahlungseingang zu einer Rechnung
- `matches(store, bank_id)`
- `proposals(store, year=…)` – Eindeutige Zuordnungen für offene Bankumsätze: genau ein starker Treffer, der nicht von einem anderen Umsatz beansprucht wird.
- `apply(store, items, actor)` – Bestätigte Zuordnungen verbuchen. Jede muss noch genau so im aktuellen Abgleich stehen (eindeutig, nicht doppelt

### erstattung.py

Erstattung finanzieller Aufwendungen (Finanzordnung § 8) – Antragsdaten zur Eingangsrechnung der Art „refund“.

Tabellen: `refunds`

Tests: test_erstattung.py

- `init(db)`
- `deadline(cost_date)`
- `prepare(store, data)` – Vor der Prüfung der Eingangsrechnung: bei Erstattungen ist der Kreditor immer der Antragsteller (§ 8 Abs. 7) –
- `clean(store, data, bill, has_file)` – Antragsdaten prüfen (bill: geprüfte Eingangsrechnung aus verbindlichkeiten._clean). Liefert die Werte für save().
- `save(store, bill_id, values, actor)` – Innerhalb der Transaktion von verbindlichkeiten.create.
- `annotate(store, rows)` – Ergänzt Eingangsrechnungen der Art „refund“ um die Antragsdaten (refund: dict oder None, wenn vor 0.17 erfasst).
- `findings(b)` – Hinweise für die Quartalsprüfung (§ 8 Abs. 8).
- `form_pdf(store, member_id=…)` – Antrag auf Erstattung finanzieller Aufwendungen – vorausgefüllt mit dem Mitglied oder leer.

### erechnung.py

E-Rechnungen einlesen: XRechnung (UBL oder CII als XML) und ZUGFeRD/Factur-X (PDF mit eingebetteter CII-XML).

Tests: test_erechnung.py

- `extract(raw)` – XML der E-Rechnung aus einer XML- oder PDF-Datei (bytes).
- `parse(xml)` – Angaben aus der XML (CII oder UBL). Wirft ValueError mit verständlichem Grund.
- `check(raw)` – Nur die Form prüfen (für XML-Belege): wohlgeformt, kein DOCTYPE, Rechnung oder Gutschrift im XRechnung-Format.
- `read(raw)` – Datei (bytes) → Angaben der E-Rechnung.
- `view_pdf(d)` – Lesbare Fassung einer XML-Rechnung (für Belegansicht und Kassenprüfung) – ersetzt nicht die XML.

### freigabe.py

Freigabe und Prüfung von Eingangsrechnungen nach der Finanzordnung der JU Hamburg (Stand 13.03.2024).

Tabellen: `bill_approvals`, `bill_reviews`

Tests: test_berichte.py, test_erstattung.py, test_freigabe.py

- `init(db)`
- `limit(store)` – Grenze für neu erfasste Eingangsrechnungen (Stammdaten).
- `status(bill, given, paid_on=…)` – Freigabestand einer Eingangsrechnung (given: aktive Freigaben je Rolle; paid_on: erste Zahlung/Anweisung).
- `annotate(store, rows)` – Ergänzt die Zeilen aus verbindlichkeiten.bills um den Freigabestand.
- `bill_status(store, bill_id)`
- `check_payable(store, bill)` – Vor Zahlungsvorschlag und Anweisung: fehlende Freigabe nach § 7 Abs. 4 → ValueError.
- `approve(store, bill_id, role, data, actor)`
- `revoke(store, approval_id, actor)`
- `quarter_of(day)`
- `bounds(year, quarter)`
- `deadline(year, quarter)`
- `report(store, year, quarter)`
- `record_review(store, year, quarter, data, actor)`
- `due(store, today=…)` – Abgeschlossene Quartale mit Eingangsrechnungen, deren Prüfung noch nicht protokolliert ist (älteste zuerst).
- `pending(store)` – Offene Fremdrechnungen, denen noch eine Freigabe fehlt.
- `protocol_basis(store, year, quarter, rep=…)` – Grundlage des Protokolls: festgeschriebener Stand der letzten Prüfung (sonst aktueller Stand als Entwurf)
- `protocol_pdf(store, year, quarter)`

### berichte.py

Berichte: Monatsbericht für den Landesvorstand (PDF) und Kassenprüfer-Paket (ZIP, Satzung § 19 Abs. 2).

Tests: test_beitraege.py, test_berichte.py, test_erechnung.py, test_kasse.py, test_mitgliederkonto.py, test_rpj.py, test_umsatz_ungueltig.py, test_umsatzakte.py

- `balance(store, account, until=…)` – Saldo Soll − Haben eines Kontos bis einschließlich until (Buchungsdatum).
- `report_day(month=…, data_start=…, today=…)` – Stichtag des Monatsberichts: Monatsende des Monats JJJJ-MM, im laufenden Monat heute; ohne Monat heute.
- `monthly(store, day=…)` – Kennzahlen für den Monatsbericht (Stand day; Haushaltsjahr = Jahr von day).
- `monthly_pdf(store, day=…)`
- `kv_pdf(store, kv, year, today=…)` – Kompakter Auszug für den Kreisvorsitz: Zugänge (FFK als eigener Punkt), Ausgaben mit kurzer Bezeichnung, Stand, Verfall.
- `cash_pdf(store, year, today=…)` – Kassenbuch eines Jahres: Übertrag, Einnahmen, Ausgaben, Bestand, alle Bewegungen mit Beleg und Bestand.
- `member_pdf(store, member_id, today=…)` – Beitragskonto eines Mitglieds: je Jahr Soll, gezahlt, Stand und die Buchungen mit laufendem Saldo.
- `rpj_pdf(store, year, today=…)` – RPJ-Jahresübersicht (Schattenhaushalt): Bescheid, Mittelabrufe, Projekte mit Plan und Ist, Kostenarten, Abstimmung.
- `rpj_project_pdf(store, project_id, today=…)` – Einzelübersicht eines RPJ-Projekts: Plan und Ist je Kostenart und alle zugeordneten Buchungen.
- `audit_package(store, year, data_dir, out)` – Kassenprüfer-Paket als ZIP in das Dateiobjekt out schreiben (Ordner Kassenpruefung-JJJJ) – Datei für Datei,
- `audit_files(store, year, data_dir)` – Inhalt des Pakets als (Dateiname → Bytes, Prüfhinweise) – für Tests und kleine Bestände.

## Rechnungen und Versand

### rechnung_pdf.py

Rechnungs-PDF (A4, Briefaufbau nach DIN 5008 Form B) – einzeln, als Sammel-PDF oder als Vorschau.

Tests: test_core.py, test_lastschrift.py, test_mahnung.py, test_pdfschrift.py, test_pruefung.py, test_rechnungen.py

- `invoice_data(store, iid)` – Festgeschriebene Rechnung aus der Datenbank.
- `debit_of(inv)` – Vorabankündigung der Lastschrift, solange sie gilt (lastschrift.info: nicht zurückgegeben, Mandat gültig, Datum nicht
- `draft(kind, cfg, recipient, address, lines, due, issued, year=…, member_number=…, number=…, basis=…, debit=…)` – Noch nicht festgeschriebene Rechnung für Vorschau (PDF und E-Mail).
- `epc(name, iban, bic, cents, purpose)` – GiroCode-Inhalt (EPC069-12, Version 002, UTF-8) für eine SEPA-Überweisung.
- `amount_due(inv)` – Zu zahlender Betrag: Rechnungsbetrag abzüglich bereits zugeordneter Zahlungen (beim Versand bekannt).
- `epc_payload(inv)` – GiroCode über den offenen Betrag oder None, wenn nicht möglich (bezahlt, storniert, IBAN ungültig).
- `qr_svg(payload, size=…)` – GiroCode als SVG (für die Anzeige im Programm).
- `title_of(inv)`
- Klasse `Start` – Unsichtbare Marke: ab hier gilt diese Rechnung für Fußzeile und Seitenzählung.
  - `Start.wrap(*_)`
  - `Start.draw()`
- `sender_line(snap, width=…)` – Absenderzeile (Rücksendeangabe) passend zur Breite des Anschriftfelds: (Text, Schriftgröße).
- `fit(text, size, width, font=…)` – Text auf eine Breite kürzen (…), statt in die Nachbarspalte zu laufen.
- `footer_address(snap, width=…, size=…)` – Anschrift für die Fußzeile in höchstens drei Zeilen: bei mehr Zeilen erst Straße und PLZ/Ort zusammenfassen,
- Klasse `Letterhead` – Logo, Absenderzeile, Anschriftfeld und Informationsblock (oberer Briefbereich bis 100 mm).
  - `Letterhead.wrap(availWidth, availHeight)`
  - `Letterhead.draw()`
- `render(invoices)` – Eine oder mehrere Rechnungen (dicts) als ein PDF; jede beginnt auf einer neuen Seite.
- `filename(inv)`

### pdfschrift.py

Schrift für alle PDF-Dateien: Liberation Sans (SIL OFL 1.1, Laufweite wie Helvetica/Arial).

Tests: test_pdfschrift.py

- `exclusive(fn)` – PDF-Erzeugung nacheinander: reportlab bildet Teilschriften über eine gemeinsame Leseposition der TTF-Datei,
- `covers(text, font=…)` – True, wenn die Schrift alle Zeichen des Textes darstellen kann (Leerraum ausgenommen).

### versand.py

E-Mail-Versand von Rechnungen über SMTP (z. B. Strato) – Nachricht, Verbindung, Versandauftrag.

Tests: test_lastschrift.py, test_mahnung.py, test_rechnungen.py

- `texts(cfg, inv, test=…)` – Betreff und Text aus der Vorlage für die Rechnungsart (oder die Zahlungserinnerung, wenn inv['reminder']).
- `html_body(cfg, inv, body)` – Schlichte HTML-Fassung (Inline-Stile, ohne externe Bilder oder Skripte).
- `message(cfg, inv, pdf, to, test=…)`
- `recipient_address(store, inv)` – Versandadresse: an der Rechnung hinterlegt, sonst die aktuelle E-Mail des Mitglieds.
- `tls_context()`
- Klasse `_Tracking` – Merkt sich, ob die Nachricht schon übertragen wurde (DATA begonnen).
  - `_Tracking.data(msg)`
- Klasse `_SMTP`
- Klasse `_SMTP_SSL`
- Klasse `RecipientRefused` – Der Hauptempfänger wurde abgelehnt (eine angenommene Bcc-Kopie zählt nicht als Zustellung).
- Klasse `Uncertain` – Verbindung brach nach der Übertragung ab – ob die E-Mail angekommen ist, ist unklar.
- Klasse `ConnectionLost` – Verbindung vor der Übertragung verloren – die E-Mail wurde nicht versendet.
- `friendly(error)`
- `connect(cfg, password, timeout=…)`
- `password_for(cfg)`
- `check(cfg)` – Nur anmelden und wieder abmelden – es wird nichts versendet.
- `deliver(smtp, msg, to)` – Genau ein Übertragungsversuch – nie automatisch wiederholen (sonst Gefahr doppelter E-Mails).
- `alive(smtp)`
- `send_one(cfg, msg)`
- `sample_invoice(cfg, kind)` – Erfundene Beispielrechnung für die Vorschau der Vorlagen (kind 'reminder': Erinnerung zur Beispielrechnung).
- `send_test(cfg, inv)` – Schickt die Nachricht zu einer Rechnung als Test an die eigene Absenderadresse.
- `status()`
- `busy()`
- `paid_amount(store, iid)`
- `plan(store, ids, resend=…)` – Welche Rechnungen werden versendet, welche übersprungen (mit Grund).
- `reminder_terms(cfg, level, today=…)` – Angaben einer Zahlungserinnerung: laufende Nummer und neue Frist (heute + Frist laut Stammdaten).
- `reminders(store, ids=…, resend=…, today=…)` – Überfällige offene Rechnungen mit Erinnerungsstand; reason None = Erinnerung kann jetzt raus.
- `start(db_path, ids, actor, resend=…, kind=…)`
- `cancel()`
- `stop_and_wait(seconds=…)` – Beim Beenden: laufenden Versand abbrechen und auf die aktuelle E-Mail warten.

### geheimnis.py

Passwort für den E-Mail-Versand: auf dem Server in einer Datei, sonst nur im Arbeitsspeicher.

Tests: test_mahnung.py, test_rechnungen.py, test_server.py

- Klasse `FileStore` – Ablage auf dem Server: eine JSON-Datei {Konto: Passwort} mit Rechten 0600 im eigenen Ordner.
  - `FileStore.get_password(service, account)`
  - `FileStore.set_password(service, account, password)`
  - `FileStore.delete_password(service, account)`
- `available()`
- `save(account, password, remember=…)` – Speichert das Passwort; liefert 'datei' oder 'sitzung'.
- `load(account)`
- `where(account)` – 'datei', 'sitzung' oder None – ohne das Passwort preiszugeben.
- `forget(account)`

## Verband

### haushalt.py

Haushaltsansicht nach der Excel-Vorlage; Sachkonten und Titel bleiben getrennt.

Tabellen: `household_plan`

Tests: test_berichte.py, test_haushaltsposten.py, test_kasse.py, test_konten.py, test_kvbudget.py, test_pruefung.py, test_uebernahme.py

- `default_title(account, kv=…)` – Haushaltstitel, der sich aus Konto und Bereich ergibt (Sammelkonto KV: Zeile des Kreisverbands), sonst None.
- `init(db)`
- `sync_account_names(db)` – Einmalig (Entscheidung Lennert Gust 29.10.2026): Kontenbezeichnungen wie im Haushalt bzw. in der Excel-Vorlage,
- `reference()`
- `save_plan(store, year, title, amount, actor)`
- `import_reference(store, year, actor)`
- `actuals(store, year, until=…)` – Ist je Haushaltstitel; until (JJJJ-MM-TT): nur Buchungen bis einschließlich diesem Tag (Monatsbericht).
- `report(store, year, until=…)` – Haushalt Plan/Ist für year und Vorjahr; until begrenzt das Ist des Jahres auf Buchungen bis zu diesem Tag.
- `overruns(rows)` – Ausgabentitel mehr als 10 % über Plan (Satzung § 50: Nachtragshaushalt vor weiteren Verpflichtungen). Ausgaben sind negativ.
- `pdf_report(store, year)`

### kvbudget.py

Budgets der Kreisverbände und der Schüler Union – Regel wie in der Excel-Masterdatei (Entscheidung Lennert Gust, 08.11.2026).

Tabellen: `cash_donations`, `kv_closings`, `kv_labels`, `kv_year_values`

Tests: test_beitraege.py, test_berichte.py, test_klaerfaelle.py, test_kvabgleich.py, test_kvbudget.py, test_mitgliederkonto.py, test_pruefung.py

- `init(db)`
- `areas()` – Bereiche mit Budget: Kreisverbände und Schüler Union (Code, Name).
- `area_name(code)`
- `set_plan(store, year, kv, amount, actor)` – Planwert eines Kreisverbands bzw. der Schüler Union (positiver Betrag) = Zeile im Gesamthaushalt (Ausgabe, negativ).
- `start(store)` – Beginn der KV-Budgets (Stammdaten, immer ein 1. Januar): ältere Buchungen und Jahre zählen nicht.
- `year_values(store)` – Jahreswerte {(Jahr, Bereich): {ffk, dues_share, opening, …}}.
- `check_values(store, year, kv, data, cents=…)` – Jahreswerte prüfen und in Cent umwandeln (ohne zu speichern): {Feld: Cent oder None}.
- `set_values(store, year, kv, data, actor, cents=…, source=…, in_transaction=…)` – Jahreswerte eines Bereichs: nur übergebene Felder ändern, leer = nicht festgelegt. FFK und Beitragsanteil ≥ 0;
- `set_label(store, journal_id, label, recipient, actor, source=…, in_transaction=…)` – Kurze Bezeichnung und Empfänger einer Buchung im KV-Budget (Bericht für den Kreisvorsitz). Leer = automatisch.
- `short_text(text, limit=…)` – Kurze Bezeichnung aus einem Buchungstext: ohne Übernahmehinweis, Buchungsart (LASTSCHRIFT, ÜBERWEISUNG …),
- `run(first, opening, credits, events, today, lapse=…)` – Budgetkonto nach der Regel der Masterdatei (reine Funktion: Programm und nachgerechnete Masterdatei).
- `auto_from(cfg)` – Schalter „Beitragsanteil der Kreisverbände automatisch“ (Verwaltung, ab 0.31): erstes Haushaltsjahr, ab dem der Anteil immer aus
- `share_is_auto(auto, kv, year)`
- `check_auto_change(store, before, after)` – Der Schalter darf kein abgeschlossenes Budgetjahr umstellen (der Abschluss hält die Werte fest).
- `events(store, kv, until=…, ctx=…)` – Bewegungen eines Bereichs ab Beginn der KV-Budgets: Ausgaben, Gutschriften und Budget-Umbuchungen auf Ausgabenkonten
- `ledger(store, kv, today=…, until=…, ctx=…, values=…)` – Budgetkonto eines Bereichs (Stand heute bzw. until). values: Jahreswerte zur Probe (Vorschau Abgleich).
- `overview(store, today=…, until=…)`
- `year_overview(store, year, until=…)` – Bereiche eines Haushaltsjahres: Plan (Zeile im Gesamthaushalt), Übertrag, FFK, Beitragsanteil, Zugänge, Ausgaben, Stand.
- `dues_paid(store, kv, until=…)` – Tatsächlich gezahlte Beiträge (Ist) der Mitglieder eines Kreisverbands je Beitragsjahr: Zahlungen auf Beitragsrechnungen
- `detail(store, kv, year)` – Seite eines Bereichs: Kennzahlen des Jahres, Budgetkonto (Bewegungen mit Stand) und Beitragseingänge je Mitglied.
- `statement_for(led, year)` – Budgetkonto eines Jahres für Seite und PDF: Kennzahlen und Bewegungen mit laufendem Stand.
- `closing_due(store, today=…)` – Haushaltsjahre ab Beginn der KV-Budgets, deren Rest am 31.01. des Folgejahres verfallen ist, ohne Budgetabschluss.
- `close_year(store, year, actor, today=…)` – Budgetabschluss eines Jahres (nach dem 31.01. des Folgejahres): je Bereich Zugänge, Ausgaben, verfallen, vorgetragen.
- `closings(store)` – Festgehaltene Abschlüsse mit Hinweis, wenn sich der berechnete Wert seither geändert hat.
- `transfer(store, data, actor)` – Budget-Umbuchung (Wunsch Lennert Gust 29.10.2026): ein Bereich beteiligt sich an Kosten eines anderen, z. B. an
- `transfers(store, year=…)` – Umbuchungen (je Haushaltsjahr) mit Storno-Kennzeichen.
- `reverse_transfer(store, jid, reason, actor)` – Storno einer Umbuchung: Gegenbuchung im selben Haushaltsjahr; beide zählen danach nicht mehr im Budget.
- `donations(store, year=…)` – Spenden laut Journal (Konten 8200, 8202, 1371) mit Anteil für das Budget des Bereichs (ohne Storno-Paare):
- `save_cash(store, data, actor, cid=…)`
- `cash_list(store, today=…)`

### kvabgleich.py

Abgleich der KV-Budgets mit der Excel-Masterdatei (Blätter „KV <Name> 5100“ … „Schüler Union 5800“).

Tests: test_kvabgleich.py

- `parse(raw)` – Masterdatei → {Bereich: {'sheet', 'blocks': [...]}}; Hinweise zu nicht lesbaren Blättern.
- `preview(store, raw, filename, today=…, parsed=…)`
- `execute(store, raw, actor)` – Jahreswerte und Bezeichnungen übernehmen – neu berechnet aus derselben Datei, alles in einer Transaktion (alles oder nichts).

### rpj.py

RPJ-Schattenhaushalt: Mittel des Rings Politischer Jugend je Projekt (ab 0.26).

Tabellen: `rpj_links`, `rpj_plan`, `rpj_projects`, `rpj_requests`, `rpj_years`

Tests: test_bereinigung.py, test_rpj.py

- `init(db)`
- `kind_of(account, title)` – RPJ-Art einer Buchungszeile: 'cost', 'fee', 'grant' oder None (kein RPJ-Posten).
- `request_label(r)`
- `project_label(p)`
- `year_info(store, year)`
- `save_year(store, data, actor)` – Antrag und Bescheid eines Jahres: Kontingent (bewilligte Summe), Datum und Aktenzeichen des Bescheids, Antragsdatum.
- `save_project(store, data, actor)` – Projekt des RPJ-Haushalts mit Plan je Kostenart a–k anlegen oder ändern.
- `delete_project(store, pid, actor)`
- `save_request(store, data, actor)` – Mittelabruf (Quartal, Betrag, Datum) anlegen oder ändern – höchstens bis zum Kontingent laut Bescheid.
- `delete_request(store, rid, actor)`
- `parse_target(value)` – „p:12“ (Projekt) bzw. „r:3“ (Mittelabruf) → ('p', 12) / ('r', 3); leer → None.
- `target(store, kind, value, category, fy, label=…)` – Prüft eine Zuordnung für eine Buchungszeile der Art kind im Haushaltsjahr fy.
- `check_part(store, account, title, rpj, fy, label=…)` – Teilbetrag beim Verbuchen (Kontoauszug, Kasse, Eingangsrechnung): RPJ-Zuordnung prüfen. None = kein RPJ-Posten.
- `attach(store, entries, actor)` – Nach dem Buchen (entry_id gesetzt): Zuordnungen der Zeilen mit 'rpj' festhalten – innerhalb der Transaktion.
- `inherit(store, entry, source_entry_ids, actor)` – Ausbuchung eines Restbetrags (Eingangsrechnung): Zuordnung der größten zugeordneten Position übernehmen.
- `entries(store, year=…, skip=…)` – RPJ-Buchungszeilen (Kontoauszug, Kasse, Eingangsrechnungen, ohne Stornos) mit ihren Zuordnungen; year = Haushaltsjahr.
- `open_entries(store, year=…, skip=…)`
- `assign(store, entry_id, items, actor)` – RPJ-Buchungszeile zuordnen (auch nachträglich, z. B. Excel-Übernahme) bzw. Zuordnung ändern; ersetzt die bisherige.
- `assign_many(store, entry_ids, value, category, actor)` – Mehrere noch nicht zugeordnete RPJ-Buchungszeilen mit dem ganzen Betrag demselben Projekt (mit Kostenart) bzw. Mittelabruf
- `year_view(store, year)` – RPJ-Jahresübersicht: Bescheid, Mittelabrufe, Projekte mit Plan/Ist, Kostenarten, Abstimmung mit dem Haushalt.
- `project_view(store, pid)` – Einzelübersicht eines Projekts: Plan und Ist je Kostenart, alle zugeordneten Buchungen.
- `summary(store, year)` – Für die Oberfläche (/api/data): Projekte und Mittelabrufe (Auswahl beim Verbuchen) und offene Zuordnungen.
- `year_csv(store, year)` – Projekte × Kostenarten mit Plan, Ist und Abweichung (eine Zeile je Projekt und Kostenart mit Werten).
- `bookings_csv(store, year=…, project_id=…)` – Zugeordnete RPJ-Buchungen (eines Jahres bzw. eines Projekts) – Betrag wie gebucht: Ausgaben negativ. Jahr = RPJ-Jahr des Projekts bzw.
- `preview_import(store, raw, filename)` – RPJ-Haushalt (Projekte mit Plan a–k) lesen und mit dem Bestand vergleichen – schreibt nichts.
- `apply_import(store, prev, selected, actor)` – Gewählte Zeilen der Vorschau übernehmen: neue Projekte anlegen, geänderte mit Plan aktualisieren (Status, Durchführung,
- `template_csv()` – Leere Vorlage mit Beispielzeile (erfunden) für den Import.

## Serverbetrieb und Sicherheit

### zugang.py

Anmeldung mit Benutzername und Passwort im Serverbetrieb (Ubuntu-Server hinter Tailscale).

Tests: test_http.py, test_keycloak_app.py, test_rollen.py, test_schutz.py, test_server.py, test_sicherheit.py, test_uebernahme.py, test_umsatzakte.py

- `check_new(password)` – Anforderungen an ein neues Passwort; Fehlermeldung als ValueError.
- `hash_password(password)`
- `is_set(stored)` – Gilt der gespeicherte Eintrag als Passwort dieses Verfahrens?
- `verify(password, stored)`
- `burn_time(password)` – Bei unbekanntem Benutzernamen oder unbrauchbarer Eingabe genauso lange rechnen wie bei einer echten Prüfung.
- `find_user(db, name)` – Benutzer zu einem Namen (Groß-/Kleinschreibung und Leerzeichen am Rand egal).
- `check_login(db, name, password)` – Name des Benutzers bei richtigem Passwort, sonst None – in jedem Fall mit einer vollen scrypt-Prüfung.
- `fingerprint(stored)` – Kurzer Fingerabdruck des gespeicherten Hashes: Sitzungen enden, sobald das Passwort neu gesetzt wird.
- `set_password(db, name, password, actor=…, create=…)` – Passwort setzen (Benutzer ggf. anlegen); schreibt einen Eintrag ins Änderungsprotokoll ohne das Passwort.
- Klasse `Throttle` – Sperre nach wiederholten Fehlversuchen je Schlüssel (Gerät, Benutzername) und für alle zusammen.
  - `Throttle.wait(keys=…)` – Sekunden bis zum nächsten erlaubten Versuch für diese Schlüssel (0 = sofort), ohne Vormerkung.
  - `Throttle.gesperrt()` – Gerade gesperrte Schlüssel als (Schlüssel, Versuche, Wartezeit in Sekunden); '*' = alle Geräte zusammen.
  - `Throttle.freigeben(key)` – Fehlversuche eines Schlüssels löschen ('*' = die Obergrenze für alle).
  - `Throttle.attempt(keys=…)`
  - `Throttle.failed(keys=…)` – Fehlversuch ohne vorherige Vormerkung (z. B. falsches altes Passwort beim Ändern).
  - `Throttle.succeeded(keys=…)`

### rollen.py

Rollen und Rechte im Serverbetrieb (ab 0.34).

Tabellen: `roles`, `user_roles`

Tests: test_kenntnis.py, test_keycloak_app.py, test_mitgliedbank.py, test_rollen.py, test_schutz.py, test_sicherheit.py, test_umsatz_ungueltig.py

- `required(method, path)` – Nötiges Recht für eine Adresse; unbekannte Adressen verlangen „Verwaltung schreiben“.
- `allowed(rights, need)`
- `can(rights, area, level=…)`
- `blank(value)` – Gleiche Form ohne Inhalt – die Oberfläche bekommt leere Listen und Nullen statt fehlender Schlüssel.
- `filter_snapshot(snapshot, rights)`
- `clean_rights(raw)` – Rechte aus einer Eingabe: nur bekannte Bereiche und Stufen.
- `init(db)`
- `on_user_created(db, user_id)` – Neue Person über den Verwaltungsbefehl: gibt es noch niemanden für die Verwaltung,
- `rights_of(db, name)` – Rechte einer Person: je Bereich die höchste Stufe ihrer Rollen; inaktiv, abgelaufen oder unbekannt = keine.
- `overview(db)` – Personen, Rollen und Bereiche für die Verwaltung – ohne Passwörter.
- `save_role(db, data, actor)` – Rolle anlegen oder ändern (`id` leer = neu). Liefert die Kennung. Das Protokoll hält Rechte vorher und nachher fest.
- `delete_role(db, role_id, actor)`
- `sitzungen_beenden(db, user_id)` – Alle Sitzungen einer Person beenden (auch ohne lokales Passwort): `app.user_fingerprint` enthält `sitzung_stand`.
- `save_user(db, data, actor, lokal=…)` – Person anlegen oder ändern: Benutzername, Angaben zur Person (`_person`), aktiv, Rollen, optional neues Passwort (neu: Pflicht).

### sicherheit.py

Sicherheit im Serverbetrieb (ab 0.35): Ereignisse, Notfall- und Wartungsmodus, Dateiprüfung, Lagebild.

Tabellen: `sicherheit_ereignisse`, `sicherheit_zustand`

Tests: test_keycloak_app.py, test_schutz.py, test_sicherheit.py

- `init(db)`
- `ereignis(db, art, person=…, geraet=…, detail=…)` – Ereignis festhalten (eigene Transaktion). Ältere Einträge als AUFBEWAHREN_TAGE fallen dabei weg.
- `sperre(db)` – Aktive Sperre: Notfall vor Wartung; None = normaler Betrieb.
- `darf_trotz_sperre(rights)`
- `notfall_an(db, actor, grund, geraet=…)`
- `notfall_aus(db, actor, geraet=…)`
- `wartung(db, actor, an, text=…, geraet=…, nur_text=…)` – Wartung ein- oder ausschalten bzw. mit nur_text den Hinweis einer laufenden Wartung ändern (Beginn und Auslöser bleiben).
- `notzugang_offen(db)` – Freischaltung des Notzugangs ({'bis', 'von'}) oder None. Im Keycloak-Betrieb gilt die Passwort-Anmeldung nur solange.
- `notzugang_setzen(db, minuten, actor, geraet=…)` – Notzugang für `minuten` freischalten (1 … NOTZUGANG_HOECHSTENS) bzw. mit 0 schließen. Liefert das Ende oder None.
- `ereignisse(db, art=…, tage=…, suche=…, limit=…)` – Sicherheitsereignisse und Änderungen an Personen/Rollen, neueste zuerst.
- `ereignisse_csv(rows)`
- `dateipruefung(app_dir)` – Programmdateien gegen die Prüfliste: veränderte, fehlende und zusätzliche Dateien (z. B. ein eingeschleustes Modul).
- `dateipruefung_lauf(db, app_dir, actor=…, geraet=…)`
- `letzte_dateipruefung(db, app_dir)` – Letztes Ergebnis; älter als einen Tag → jetzt neu prüfen (dauert unter einer Sekunde).
- `konten(db)` – Personen mit letzter Anmeldung, Verwaltungs-/Sicherheitsrechten und Hinweisen (ungenutzt, ohne Passwort, deaktiviert).
- `lagebild(db, sitzungen, sperren, letzte_sicherung, app_dir)`

### schutz.py

Abwehr in der Anwendung im Serverbetrieb (ab 0.39): Firewall, Sperrliste, Bedrohungsliste, Live-Verkehr, Prüfungen.

Tabellen: `schutz_sperren`, `schutz_stunden`

Tests: test_haertung.py, test_schutz.py

- `init(db)`
- `aufrufer(verbindung, weitergereicht=…)` – Adresse des Aufrufers (einzige Stelle): X-Forwarded-For zählt nur, wenn die Verbindung von 127.0.0.1/::1 kommt
- Klasse `_Stand`
- `einstellungen(db)` – Einstellungen der Firewall mit Vorgaben für alles, was nicht gespeichert ist.
- `vergessen(alles=…)` – Stand bei der nächsten Anfrage neu laden (nach Änderungen); `alles` leert auch Zähler, Verkehr und Statistik (Tests).
- `muster(*texte)` – Gruppe des ersten Angriffsmusters in den Texten (auch ein- und zweifach URL-dekodiert) oder None.
- `eingang(pfad, ip, ziel, agent=…, angemeldet=…, fremd=…)` – Prüfung VOR der Verarbeitung einer Anfrage. Liefert (Status, Art): Status 403/404 = so abweisen, None = durchlassen;
- `inhalt(pfad, ip, daten)` – JSON-Werte einer Anfrage OHNE Anmeldung prüfen (Passwortfelder nie). Liefert (Status, Art) wie `eingang`.
- `nachher(pfad, ip, methode, ziel, status, art=…, angemeldet=…, fremd=…)` – Nach jeder Antwort: Fehlerbremse (viele Fehlerantworten einer Adresse ohne Anmeldung = Treffer) und Live-Verkehr.
- `einstellungen_speichern(db, daten, actor, eigene=…, geraet=…)` – Einstellungen prüfen und speichern (nur genannte Felder). Schutz vor Aussperrung: die Bedrohungsliste lässt sich nicht
- `sperren(db, netz, grund, minuten, actor, eigene=…, geraet=…)` – Adresse oder Netz von Hand sperren (minuten 0 = unbefristet). Nie: die eigene Adresse, 127.0.0.1, „nie sperren“.
- `freigeben(db, wen, actor, geraet=…)` – Sperre freigeben: Kennung einer Sperre oder 'auto' für alle automatischen. Liefert die Anzahl.
- `aus(db, actor=…)` – Notausgang (verwaltung.py schutz-aus): alle Sperren löschen, Firewall auf „nur protokollieren“. Liefert die Anzahl.
- `notfall(db, an)` – Notfallmodus: Vorlage NOTFALL anwenden und den vorherigen Stand merken bzw. beim Beenden zurückbringen – je Feld und
- `mitschnitt(db, an, actor, geraet=…)` – Mitschnitt aller Anfragen für MITSCHNITT_MINUTEN starten bzw. beenden (nur Adresse, Methode, Pfad, Status).
- `status(db, eigene=…)` – Übersicht für den Bereich „Abwehr“: Einstellungen, Sperrliste, Bedrohungsliste, Live-Verkehr.
- `abwehr(db)` – Abwehr der letzten 24 Stunden für die Übersicht: Treffer nach Art, Abweisungen, Sperren – gesamt und je Stunde.
- Klasse `_KeineWeiterleitung`
  - `_KeineWeiterleitung.redirect_request(req, fp, code, msg, headers, newurl)`
- `holen(url)` – Eine Quelle der Bedrohungsliste abrufen: nur https, ohne Weiterleitung, höchstens LISTE_BYTES + 1 Bytes und HOLEN_FRIST
- `liste_lesen(text)` – Netze aus einer DROP-Datei („Netz ; SBL-Nummer“, Kommentare mit ;). Jede Zeile muss ein plausibles öffentliches Netz
- `liste_laden(db, actor=…, geraet=…)` – Bedrohungsliste neu laden und prüfen; bei jedem Fehler bleibt die bisherige Liste. Liefert Stand und Anzahl.
- `liste_info(db)` – Stand der Bedrohungsliste: {'stand', 'anzahl', 'veraltet'} – ohne Liste stand = ''.
- `stuendlich(db)` – Hintergrundlauf (app.py, jede Stunde): Stundenstatistik schreiben; Bedrohungsliste neu laden, wenn eingeschaltet und
- `serverschutz(pfad=…, jetzt=…)` – Bericht der Betriebssystem-Härtung lesen: {'vorhanden', 'aktuell', 'hinweis', 'zeit', 'pruefungen', 'zahlen'}.
- `pruefung(db, lage, bericht, keycloak=…, https=…)` – Konfigurationsprüfung für die Übersicht: Liste aus {'id', 'titel', 'stand' ok|hinweis|kritisch, 'detail', 'ziel', 'aktion'}.

### keycloak.py

Anmeldung über Keycloak im Serverbetrieb (ab 0.36).

Tests: test_keycloak.py, test_keycloak_app.py

- Klasse `KeycloakFehler` – Fehler mit deutscher, nutzerfreundlicher Meldung – nie mit Tokens, Passwörtern oder Antwortinhalten.
- Klasse `Einstellungen`
- `aus_umgebung(env=…)` – Einstellungen aus JU_FINANZEN_KEYCLOAK_*; None ohne Keycloak, ValueError bei halber oder unsicherer Angabe.
- Klasse `_OhneUmleitung`
  - `_OhneUmleitung.redirect_request(*args, **kwargs)`
- `anmeldung_starten(cfg, aktion=…)` – (Adresse für den Browser, Vorgang). Der Vorgang bleibt auf dem Server (Sitzung), nie im Browser.
- `anmeldung_abschliessen(cfg, vorgang, query)` – Rückkehr aus Keycloak prüfen und Code einlösen → {'sub', 'benutzername'}; sonst KeycloakFehler.
- `abmelden_url(cfg, id_token_hint=…, zurueck=…)` – Adresse für die Abmeldung bei Keycloak (RP-initiated Logout).
- Klasse `Verwaltung` – Personen in Keycloak anlegen und pflegen (Service-Account des Clients). Threadsicher.
  - `Verwaltung.anlegen(benutzername, vorname, nachname, email, aktiv, startpasswort, app_pflicht)`
  - `Verwaltung.suchen(benutzername)`
  - `Verwaltung.aendern(keycloak_id, vorname, nachname, email, aktiv, app_pflicht)`
  - `Verwaltung.startpasswort_setzen(keycloak_id, passwort)`
  - `Verwaltung.hat_app(keycloak_id)`
  - `Verwaltung.app_zuruecksetzen(keycloak_id)` – Authenticator-App entfernen; bei der nächsten Anmeldung muss eine neue eingerichtet werden.
  - `Verwaltung.abmelden(keycloak_id)` – Alle Keycloak-Sitzungen der Person beenden.
  - `Verwaltung.erreichbar()`
- `startpasswort()` – Zufälliges, gut vorlesbares Startpasswort: 4 Gruppen à 4 Zeichen (19 Zeichen), mit Ziffer und Buchstaben.

### verwaltung.py

Verwaltung im Serverbetrieb (auf dem Ubuntu-Server über `sudo ju-finanzen …`).

Tests: test_keycloak_app.py, test_rollen.py, test_schutz.py, test_server.py

- `einstellungen()` – JU_FINANZEN_KEYCLOAK_* und JU_FINANZEN_ANMELDUNG: aus der Einstellungsdatei des Dienstes (JU_FINANZEN_ENV_DATEI, sonst
- `folders(args)`
- `connect(db)`
- `ask_password()`
- `cmd_passwort(args)`
- `cmd_benutzer(args)`
- `cmd_notfall_aus(args)` – Notzugang: Notfall- und Wartungsmodus auf dem Server beenden (z. B. wenn sich niemand mehr anmelden kann).
- `cmd_schutz_aus(args)` – Notausgang der Abwehr: alle Sperren löschen und die Firewall auf „nur protokollieren“ stellen (Ereignis in der Sicherheit).
- `cmd_rolle(args)` – Notzugang: einer Person eine Rolle geben oder nehmen, wenn sich niemand mehr für die Verwaltung anmelden kann.
- `cmd_keycloak_verbinden(args)` – Bestehende Person mit Keycloak verbinden (z. B. nach dem Umstieg). Gibt es den Benutzernamen in Keycloak schon, wird nur
- `cmd_notzugang(args)` – Notzugang im Keycloak-Betrieb kurz freischalten bzw. schließen (Ereignis in der Sicherheit).
- `cmd_sicherung(args)`
- `counts(con)`
- `cmd_pruefen(args)`
- `server_running(port)`
- `safe_member(name)` – Erlaubte Einträge der Sicherungs-ZIP: Daten/finanzen.sqlite3, Daten/Importe/<Datei>, Daten/Belege/<Datei>.
- `is_fresh(db)` – Leere Datenbank einer Neuinstallation (keine Buchungen, Umsätze, Mitglieder, Rechnungen; kein Passwort)?
- `check_db(path)`
- `cmd_uebernehmen(args)`
- `main(argv=…)`

## Sicherung und Pflege

### sicherung.py

Automatische tägliche Sicherung der Datenbank beim Programmstart.

Tests: test_sicherung.py

- `last_daily(backup_dir)` – Datum (JJJJ-MM-TT) der jüngsten automatischen Sicherung oder ''.
- `daily_backup(db_path, backup_dir, keep=…, today=…)` – Legt höchstens eine Sicherung pro Tag an und behält die letzten ``keep`` Stände.
- `mirror_files(source, target)` – Neue Dateien (z. B. Belege) in die Sicherung kopieren; vorhandene werden nie überschrieben. Liefert die Anzahl.
- `named_backup(db_path, backup_dir, label)` – Sicherung vor einer größeren Änderung (z. B. Excel-Übernahme). Gibt den Pfad zurück.

### bereinigung.py

Endgültiges Löschen von Daten vor einem Stichtag (Anweisung Lennert Gust 29.10.2026: „die 2024er Daten

Tests: test_beitraege.py, test_bereinigung.py, test_kvbudget.py, test_mitgliederkonto.py, test_umsatzakte.py

- `check(store, cutoff)` – Was gelöscht würde – und was die Bereinigung verhindert.
- `purge_before(store, cutoff, actor)` – Löscht Bankumsätze vor dem Stichtag samt Buchungen, bereinigt das Protokoll, setzt den Datenbeginn.
