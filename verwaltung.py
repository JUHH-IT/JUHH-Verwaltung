"""Verwaltung im Serverbetrieb (auf dem Ubuntu-Server über `sudo ju-finanzen …`).

  passwort NAME        Passwort setzen bzw. ändern (legt den Benutzer an, wenn es ihn noch nicht gibt)
  benutzer             Benutzer anzeigen (ohne Passwörter)
  sicherung            Sicherung der Datenbank jetzt anlegen (Sicherungen/Manuell)
  pruefen              Datenbank prüfen (Integrität, Anzahl Buchungen usw. – nur Zahlen)
  keycloak-verbinden NAME   bestehende Person in Keycloak anlegen (Startpasswort erscheint einmal auf der Konsole) bzw. mit
                       --ja eine dort schon vorhandene Person gleichen Benutzernamens verknüpfen; --kc-name NAME = eigener
                       Anmeldename in Keycloak (z. B. bei Leerzeichen im Benutzernamen)
  notzugang            Keycloak-Betrieb: Passwort-Anmeldung der Verwaltung (Notzugang) für --minuten N freischalten
                       (Standard 60, höchstens 240); --aus schließt ihn wieder
  uebernehmen DATEI    Daten aus der Mac-App übernehmen: ZIP aus „Sicherung herunterladen“ oder eine .sqlite3-Datei;
                       nur bei gestopptem Dienst; vorhandene Daten nur mit --ersetzen (werden vorher gesichert)

Ordner aus --data/--backups bzw. JU_FINANZEN_DATEN/JU_FINANZEN_SICHERUNGEN (siehe /etc/ju-finanzen/ju-finanzen.env).
JU_FINANZEN_KEYCLOAK_* und JU_FINANZEN_ANMELDUNG liest der Befehl selbst aus dieser Datei (`einstellungen`), damit das
Client-Secret nie als Argument in der Prozessliste steht.
Gibt nie Passwörter oder Inhalte (Namen, Beträge, IBAN) aus – nur Anzahlen.
"""
import argparse
import getpass
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import sicherung
import zugang

MAX_ZIP_MEMBER = 200 * 1024 * 1024
MAX_ZIP_TOTAL = 2 * 1024 * 1024 * 1024
NEEDED_TABLES = {'users', 'accounts', 'entries', 'bank', 'members', 'invoices', 'audit', 'settings'}
ENV_DATEI = '/etc/ju-finanzen/ju-finanzen.env'
UMLAUTE = str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss'})


def einstellungen():
    """JU_FINANZEN_KEYCLOAK_* und JU_FINANZEN_ANMELDUNG: aus der Einstellungsdatei des Dienstes (JU_FINANZEN_ENV_DATEI, sonst
    ENV_DATEI; Zeilen KEY=VALUE, letzte gilt, # = Kommentar), die Umgebung hat Vorrang."""
    pfad = Path(os.environ.get('JU_FINANZEN_ENV_DATEI') or ENV_DATEI)
    try:
        zeilen = pfad.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        zeilen = []
    except OSError:
        raise SystemExit(f'{pfad} ist nicht lesbar.')
    gesucht = lambda key: key.startswith('JU_FINANZEN_KEYCLOAK_') or key == 'JU_FINANZEN_ANMELDUNG'
    werte = {}
    for zeile in zeilen:
        key, sep, value = zeile.strip().partition('=')
        if sep and gesucht(key):
            werte[key] = value.strip()
    werte.update({k: v for k, v in os.environ.items() if gesucht(k)})
    return werte


def folders(args):
    data = Path(args.data or os.environ.get('JU_FINANZEN_DATEN') or '')
    if not str(data) or str(data) == '.':
        raise SystemExit('Datenordner fehlt: --data angeben oder JU_FINANZEN_DATEN setzen.')
    backups = Path(args.backups or os.environ.get('JU_FINANZEN_SICHERUNGEN') or data.parent / 'Sicherungen')
    return data, backups


def connect(db):
    if not db.exists():
        raise SystemExit(f'Keine Datenbank gefunden: {db}')
    con = sqlite3.connect(db, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def ask_password():
    if not sys.stdin.isatty():
        first = sys.stdin.readline().rstrip('\n')   # für Skripte: Passwort zweimal über stdin
        second = sys.stdin.readline().rstrip('\n')
    else:
        first = getpass.getpass('Neues Passwort (mindestens 12 Zeichen): ')
        second = getpass.getpass('Passwort wiederholen: ')
    if first != second:
        raise SystemExit('Die Passwörter stimmen nicht überein. Nichts geändert.')
    return first


def cmd_passwort(args):
    data, _ = folders(args)
    db = data / 'finanzen.sqlite3'
    if not db.exists():
        # Neuinstallation ohne übernommene Daten: leere Datenbank mit allen Tabellen anlegen
        from core import Store
        data.mkdir(parents=True, exist_ok=True)
        Store(db).db.close()
    con = connect(db)
    try:
        exists = zugang.find_user(con, args.name) is not None
        if not exists:
            users = [r['name'] for r in con.execute('SELECT name FROM users ORDER BY id')]
            if users and not args.neu:
                raise SystemExit(f'Benutzer „{args.name}“ gibt es nicht. Vorhanden: {", ".join(users)}. '
                                 'Neuen Benutzer mit --neu anlegen.')
        password = ask_password()
        try:
            name = zugang.set_password(con, args.name, password, actor='Serververwaltung', create=True)
        except ValueError as e:
            raise SystemExit(f'{e} Nichts geändert.')
    finally:
        con.close()
    print(f'Passwort für „{name}“ gesetzt.' if exists else f'Benutzer „{name}“ angelegt und Passwort gesetzt.')
    print('Bereits angemeldete Sitzungen bleiben bis zum Neustart des Dienstes gültig (sudo systemctl restart ju-finanzen).')


def cmd_benutzer(args):
    data, _ = folders(args)
    con = connect(data / 'finanzen.sqlite3')
    try:
        import rollen
        rollen.init(con)
        rows = con.execute(f'SELECT id,name,password,active,vorname,nachname,funktion,{rollen.GILT} AS gilt FROM users ORDER BY id').fetchall()
        roles = {r['id']: [x[0] for x in con.execute('SELECT ro.name FROM user_roles ur JOIN roles ro ON ro.id=ur.role_id WHERE ur.user_id=? ORDER BY ro.id', (r['id'],))] for r in rows}
    finally:
        con.close()
    if not rows:
        print('Noch keine Benutzer. Anlegen mit: sudo ju-finanzen passwort <Name>')
    for r in rows:
        person = ', '.join(x for x in (' '.join(filter(None, (r['vorname'], r['nachname']))), r['funktion']) if x)
        print(f'{r["name"]}' + (f' ({person})' if person else '') + ': '
              + ('Passwort gesetzt' if zugang.is_set(r['password']) else 'kein Passwort (Anmeldung gesperrt)')
              + ('' if r['gilt'] else ', deaktiviert' if not r['active'] else ', abgelaufen') + '; Rollen: ' + (', '.join(roles[r['id']]) or 'keine'))


def cmd_notfall_aus(args):
    """Notzugang: Notfall- und Wartungsmodus auf dem Server beenden (z. B. wenn sich niemand mehr anmelden kann)."""
    data, _ = folders(args)
    con = connect(data / 'finanzen.sqlite3')
    try:
        import rollen, sicherheit
        rollen.init(con)
        sicherheit.init(con)
        sperre = sicherheit.sperre(con)
        if not sperre:
            print('Kein Notfall- oder Wartungsmodus aktiv.')
            return 0
        if sicherheit.sperre(con)['art'] == 'notfall':
            sicherheit.notfall_aus(con, 'Serververwaltung')
        sicherheit.wartung(con, 'Serververwaltung', False)
    finally:
        con.close()
    print('Notfall- bzw. Wartungsmodus beendet. Alle können sich wieder anmelden.')
    return 0


def cmd_rolle(args):
    """Notzugang: einer Person eine Rolle geben oder nehmen, wenn sich niemand mehr für die Verwaltung anmelden kann."""
    data, _ = folders(args)
    con = connect(data / 'finanzen.sqlite3')
    try:
        import rollen
        rollen.init(con)
        user = zugang.find_user(con, args.name)
        role = con.execute('SELECT id,name FROM roles WHERE lower(name)=lower(?)', (' '.join(args.rolle.split()),)).fetchone()
        if not user:
            raise SystemExit(f'Benutzer „{args.name}“ gibt es nicht.')
        if not role:
            names = ', '.join(r[0] for r in con.execute('SELECT name FROM roles ORDER BY id'))
            raise SystemExit(f'Rolle „{args.rolle}“ gibt es nicht. Vorhanden: {names}')
        with con:
            reactivated = False
            if args.entfernen:
                con.execute('DELETE FROM user_roles WHERE user_id=? AND role_id=?', (user['id'], role['id']))
                if not rollen._admins(con):   # sonst käme niemand mehr in die Verwaltung; der Abbruch macht das Entfernen rückgängig
                    raise SystemExit('Nicht entfernt: Danach könnte sich niemand mehr für „Benutzer und Rollen“ anmelden.')
            else:
                con.execute('INSERT OR IGNORE INTO user_roles(user_id,role_id) VALUES(?,?)', (user['id'], role['id']))
                reactivated = not con.execute(f'SELECT {rollen.GILT} FROM users WHERE id=?', (user['id'],)).fetchone()[0]
                if reactivated:   # deaktiviert oder abgelaufen: wieder aktiv, ohne Ablaufdatum
                    con.execute('UPDATE users SET active=1,gueltig_bis=NULL WHERE id=?', (user['id'],))
            con.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)',
                        (datetime.now().isoformat(timespec='seconds'), 'Serververwaltung', 'Rolle entzogen' if args.entfernen else 'Rolle vergeben',
                         json.dumps({'benutzer': user['name'], 'rolle': role['name']}, ensure_ascii=False)))
    finally:
        con.close()
    print(f'Rolle „{role["name"]}“ für „{user["name"]}“ ' + ('entfernt.' if args.entfernen else 'vergeben.')
          + (' Die Person war deaktiviert oder abgelaufen und ist jetzt wieder aktiv (ohne Ablaufdatum).' if reactivated else ''))


def cmd_keycloak_verbinden(args):
    """Bestehende Person mit Keycloak verbinden (z. B. nach dem Umstieg). Gibt es den Benutzernamen in Keycloak schon, wird nur
    nach Rückfrage bzw. mit --ja verknüpft (einziger Weg, auf dem der Name zählt); sonst dort neu anlegen. Das Startpasswort
    erscheint genau einmal hier und wird nirgends gespeichert."""
    import keycloak
    import rollen
    try:
        cfg = keycloak.aus_umgebung(einstellungen())
    except ValueError as e:
        raise SystemExit(str(e))
    if not cfg:
        raise SystemExit('Keycloak ist nicht eingerichtet (JU_FINANZEN_KEYCLOAK_* fehlt; sudo ju-finanzen keycloak-einrichten).')
    data, _ = folders(args)
    con = connect(data / 'finanzen.sqlite3')
    password = None
    try:
        rollen.init(con)
        user = zugang.find_user(con, args.name)
        if not user:
            raise SystemExit(f'Benutzer „{args.name}“ gibt es nicht.')
        name = user['name']
        if user['keycloak_id']:
            raise SystemExit(f'„{name}“ ist schon mit Keycloak verbunden. Ein neues Startpasswort gibt es unter Benutzer & Rollen.')
        if not (user['vorname'] and user['nachname'] and user['email']):
            raise SystemExit(f'Für „{name}“ fehlen Vorname, Nachname oder E-Mail – bitte zuerst in „Benutzer & Rollen“ ergänzen. Nichts geändert.')
        kc_name = args.kc_name or name
        if not rollen.USERNAME.fullmatch(kc_name):
            vorschlag = '.'.join(kc_name.lower().split()).translate(UMLAUTE)
            vorschlag = vorschlag if rollen.USERNAME.fullmatch(vorschlag) else 'erika.muster'
            raise SystemExit(f'„{kc_name}“ geht nicht als Anmeldename in Keycloak (2–40 Zeichen: Buchstaben ohne Umlaute, Ziffern, '
                             f'Punkt, - und _). Eigenen Anmeldenamen angeben, z. B.: sudo ju-finanzen keycloak-verbinden "{name}" '
                             f'--kc-name {vorschlag}')
        kc_name = kc_name.lower()   # Keycloak speichert Anmeldenamen klein
        gilt = con.execute(f'SELECT {rollen.GILT} FROM users WHERE id=?', (user['id'],)).fetchone()[0]
        admin = keycloak.Verwaltung(cfg)
        try:
            kid = admin.suchen(kc_name)
            if kid and not args.ja:
                frage = f'„{kc_name}“ gibt es in Keycloak schon. Genau diese Keycloak-Person verknüpfen? [j/N] '
                if not sys.stdin.isatty() or input(frage).strip().lower() not in ('j', 'ja'):
                    raise SystemExit('Nichts geändert. Verknüpfen nur nach Rückfrage oder mit --ja.')
            if not kid:
                password = keycloak.startpasswort()
                kid = admin.anlegen(kc_name, user['vorname'], user['nachname'], user['email'], bool(gilt), password, bool(user['totp_pflicht']))
        except keycloak.KeycloakFehler as e:
            raise SystemExit(f'{e} Nichts geändert.')
        try:
            with con:
                con.execute('UPDATE users SET keycloak_id=? WHERE id=?', (kid, user['id']))
                con.execute('INSERT INTO audit(time,actor,action,detail) VALUES(?,?,?,?)',
                            (datetime.now().isoformat(timespec='seconds'), 'Serververwaltung', 'Mit Keycloak verbunden',
                             json.dumps({'benutzer': name, 'anmeldename': kc_name, 'verknuepft': password is None}, ensure_ascii=False)))
        except sqlite3.IntegrityError:
            raise SystemExit('Diese Keycloak-Person ist schon mit einer anderen Person des Programms verbunden. Nichts geändert.')
    finally:
        con.close()
    if password:
        print(f'„{name}“ in Keycloak angelegt (Anmeldename: {kc_name}).')
        print(f'Startpasswort (nur jetzt sichtbar, wird nicht gespeichert): {password}')
        print('Beim ersten Anmelden ändert die Person es und richtet ggf. die Authenticator-App ein.')
    else:
        print(f'„{name}“ mit der vorhandenen Keycloak-Person verknüpft. Passwort und App bleiben wie in Keycloak.')
    return 0


def cmd_notzugang(args):
    """Notzugang im Keycloak-Betrieb kurz freischalten bzw. schließen (Ereignis in der Sicherheit)."""
    data, _ = folders(args)
    con = connect(data / 'finanzen.sqlite3')
    try:
        import rollen, sicherheit
        rollen.init(con)
        sicherheit.init(con)
        try:
            if not args.aus and args.minuten < 1:   # 0 hieße „schließen“ – dafür gibt es --aus
                raise ValueError(f'Der Notzugang lässt sich für 1 bis {sicherheit.NOTZUGANG_HOECHSTENS} Minuten freischalten.')
            bis = sicherheit.notzugang_setzen(con, 0 if args.aus else args.minuten, 'Serververwaltung', 'Server')
        except ValueError as e:
            raise SystemExit(f'{e} Nichts geändert.')
    finally:
        con.close()
    if not bis:
        print('Notzugang geschlossen. Anmeldung nur noch über Keycloak.')
        return 0
    print(f'Notzugang freigeschaltet bis {bis[11:16]} Uhr: Anmeldeseite → „Notzugang (Administrator)“, nur Personen mit '
          '„Verwaltung schreiben“ und eigenem Passwort. Vorher schließen: sudo ju-finanzen notzugang --aus')
    if einstellungen().get('JU_FINANZEN_ANMELDUNG', 'passwort') != 'keycloak':
        print('Hinweis: Die Anmeldung läuft derzeit mit Passwort – der Notzugang wirkt erst bei Anmeldung über Keycloak.')
    return 0


def cmd_sicherung(args):
    data, backups = folders(args)
    db = data / 'finanzen.sqlite3'
    if not db.exists():
        raise SystemExit(f'Keine Datenbank gefunden: {db}')
    target = sicherung.named_backup(db, backups / 'Manuell', 'sicherung')
    check = sqlite3.connect(target)
    try:
        ok = check.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        check.close()
    copied = sicherung.mirror_files(data / 'Belege', backups / 'Belege')
    print(f'Sicherung: {target} ({"geprüft" if ok else "FEHLER bei der Prüfung"}); {copied} neue Belege gespiegelt.')
    return 0 if ok else 1


def counts(con):
    one = lambda sql: con.execute(sql).fetchone()[0]
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {'integritaet': one('PRAGMA integrity_check'), 'fremdschluessel': len(con.execute('PRAGMA foreign_key_check').fetchall())}
    for label, table in (('buchungszeilen', 'entries'), ('bankumsaetze', 'bank'), ('mitglieder', 'members'),
                         ('rechnungen', 'invoices'), ('eingangsrechnungen', 'bills'), ('benutzer', 'users')):
        result[label] = one(f'SELECT COUNT(*) FROM {table}') if table in names else None
    if 'bank' in names and 'ledger' in [c[1] for c in con.execute('PRAGMA table_info(bank)')]:   # ab 0.25: Kassenbelege getrennt
        result['bankumsaetze'] = one("SELECT COUNT(*) FROM bank WHERE ledger='bank'")
        result['kassenbelege'] = one("SELECT COUNT(*) FROM bank WHERE ledger='cash'")
    if 'entries' in names:
        result['soll_gleich_haben'] = one('SELECT COALESCE(SUM(debit),0)-COALESCE(SUM(credit),0) FROM entries') == 0
    return result


def cmd_pruefen(args):
    data, _ = folders(args)
    con = connect(data / 'finanzen.sqlite3')
    try:
        result = counts(con)
    finally:
        con.close()
    belege = data / 'Belege'
    result['belege_dateien'] = sum(1 for f in belege.iterdir() if f.is_file()) if belege.is_dir() else 0
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result['integritaet'] == 'ok' and result['fremdschluessel'] == 0 else 1


def server_running(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def safe_member(name):
    """Erlaubte Einträge der Sicherungs-ZIP: Daten/finanzen.sqlite3, Daten/Importe/<Datei>, Daten/Belege/<Datei>."""
    parts = name.split('/')
    if name == 'Daten/finanzen.sqlite3':
        return 'db', None
    if len(parts) == 3 and parts[0] == 'Daten' and parts[1] in ('Importe', 'Belege') and parts[2] \
            and not parts[2].startswith('.') and '\\' not in parts[2] and parts[2] not in ('.', '..'):
        return parts[1], parts[2]
    return None, None


def is_fresh(db):
    """Leere Datenbank einer Neuinstallation (keine Buchungen, Umsätze, Mitglieder, Rechnungen; kein Passwort)?"""
    con = sqlite3.connect(db)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('entries', 'bank', 'members', 'invoices', 'bills'):
            if table in names and con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
                return False
        return 'users' not in names or not any(zugang.is_set(r[0]) for r in con.execute('SELECT password FROM users'))
    finally:
        con.close()


def check_db(path):
    con = sqlite3.connect(path)
    try:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise SystemExit('Die übernommene Datenbank ist beschädigt (Integritätsprüfung). Nichts übernommen.')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = NEEDED_TABLES - tables
        if missing:
            raise SystemExit('Das ist keine Datenbank von JU Hamburg Finanzen (es fehlen: ' + ', '.join(sorted(missing)) + '). Nichts übernommen.')
        con.row_factory = sqlite3.Row
        return counts(con)
    finally:
        con.close()


def cmd_uebernehmen(args):
    data, backups = folders(args)
    source = Path(args.datei)
    if not source.is_file():
        raise SystemExit(f'Datei nicht gefunden: {source}')
    if server_running(args.port):
        raise SystemExit('Der Dienst läuft noch. Bitte zuerst „sudo systemctl stop ju-finanzen“ (der Befehl „sudo ju-finanzen uebernehmen“ macht das automatisch).')
    db = data / 'finanzen.sqlite3'
    if db.exists() and not args.ersetzen and not is_fresh(db):
        raise SystemExit('Im Datenordner liegen schon Daten. Zum Ersetzen „--ersetzen“ angeben (die bisherigen Daten werden vorher gesichert).')
    data.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    work = data.parent / f'.uebernahme-{stamp}'
    work.mkdir(mode=0o700)
    try:
        new = work / 'Daten'
        new.mkdir()
        (new / 'Importe').mkdir()
        (new / 'Belege').mkdir()
        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
                    raise SystemExit('Die ZIP-Datei ist zu groß. Nichts übernommen.')
                found = False
                for info in infos:
                    kind, name = safe_member(info.filename)
                    if kind is None:
                        print(f'Übersprungen (nicht vorgesehen): {info.filename[:80]}')
                        continue
                    if info.file_size > MAX_ZIP_MEMBER:
                        raise SystemExit(f'Datei in der ZIP zu groß: {info.filename[:80]}. Nichts übernommen.')
                    target = new / 'finanzen.sqlite3' if kind == 'db' else new / kind / name
                    with z.open(info) as src, open(target, 'xb') as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                    found = found or kind == 'db'
                if not found:
                    raise SystemExit('In der ZIP fehlt Daten/finanzen.sqlite3 – bitte die Datei aus „Sicherung herunterladen“ verwenden. Nichts übernommen.')
        else:
            # einzelne Datenbankdatei (z. B. aus Sicherungen/Automatisch) – konsistent über die Backup-Schnittstelle kopieren
            src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
            try:
                dst = sqlite3.connect(new / 'finanzen.sqlite3')
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            except sqlite3.DatabaseError:
                raise SystemExit('Die Datei ist weder eine Sicherungs-ZIP noch eine SQLite-Datenbank. Nichts übernommen.')
            finally:
                src.close()
        result = check_db(new / 'finanzen.sqlite3')
        if not zipfile.is_zipfile(source) and data.exists():
            # nur die Datenbank zurückgeholt (z. B. Tagessicherung): vorhandene Belege und Importe bleiben in Gebrauch
            for folder in ('Belege', 'Importe'):
                if (data / folder).is_dir():
                    for f in (data / folder).iterdir():
                        if f.is_file():
                            shutil.copy2(f, new / folder / f.name)
        if data.exists():
            # bisherigen Datenordner vollständig (inkl. Belege) zur Seite legen – nie löschen
            old = backups / 'Vor Änderungen' / f'vor-uebernahme-{stamp}'
            n = 1
            while old.exists():
                n += 1
                old = old.with_name(f'vor-uebernahme-{stamp}-{n}')
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(data), str(old))
            print(f'Bisheriger Datenordner gesichert unter: {old}')
        for f in new.rglob('*'):
            os.chmod(f, 0o700 if f.is_dir() else 0o600)
        shutil.move(str(new), str(data))
        os.chmod(data, 0o700)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    belege = sum(1 for f in (data / 'Belege').iterdir() if f.is_file())
    print(f"Übernommen: {result['buchungszeilen']} Buchungszeilen, {result['bankumsaetze']} Bankumsätze, {result['mitglieder']} Mitglieder, "
          f"{result['rechnungen']} Rechnungen, {result['eingangsrechnungen']} Eingangsrechnungen, {belege} Belege, {result['benutzer']} Benutzer.")
    print(f"Prüfung: Integrität {result['integritaet']}, Soll = Haben: {'ja' if result.get('soll_gleich_haben') else 'NEIN'}. "
          'Beim nächsten Start legt das Programm eine Sicherung „vor Version“ an und ergänzt fehlende Tabellen.')
    con = sqlite3.connect(data / 'finanzen.sqlite3')
    try:
        if not any(zugang.is_set(r[0]) for r in con.execute('SELECT password FROM users')):
            names = [r[0] for r in con.execute('SELECT name FROM users ORDER BY id')]
            print('Jetzt ein Passwort setzen: sudo ju-finanzen passwort ' + (f'"{names[0]}"' if names else '<Name>'))
    finally:
        con.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ju-finanzen', description='Verwaltung von JU Hamburg Finanzen im Serverbetrieb')
    parser.add_argument('--data', help='Datenordner (Standard: JU_FINANZEN_DATEN)')
    parser.add_argument('--backups', help='Sicherungsordner (Standard: JU_FINANZEN_SICHERUNGEN)')
    parser.add_argument('--port', type=int, default=int(os.environ.get('JU_FINANZEN_PORT') or 8769))
    sub = parser.add_subparsers(dest='befehl', required=True)
    p = sub.add_parser('passwort', help='Passwort setzen bzw. ändern')
    p.add_argument('name')
    p.add_argument('--neu', action='store_true', help='weiteren Benutzer anlegen')
    sub.add_parser('benutzer', help='Benutzer mit Rollen anzeigen')
    sub.add_parser('notfall-aus', help='Notfall- und Wartungsmodus beenden (Notzugang)')
    p = sub.add_parser('rolle', help='einer Person eine Rolle geben (Notzugang, sonst in der Verwaltung des Programms)')
    p.add_argument('name')
    p.add_argument('rolle')
    p.add_argument('--entfernen', action='store_true', help='Rolle wieder nehmen')
    p = sub.add_parser('keycloak-verbinden', help='bestehende Person mit Keycloak verbinden (Startpasswort einmal auf der Konsole)')
    p.add_argument('name')
    p.add_argument('--ja', action='store_true', help='vorhandene Keycloak-Person gleichen Benutzernamens ohne Rückfrage verknüpfen')
    p.add_argument('--kc-name', help='eigener Anmeldename in Keycloak (z. B. erika.muster), wenn der Benutzername nicht passt')
    p = sub.add_parser('notzugang', help='Keycloak-Betrieb: Notzugang (Passwort der Verwaltung) befristet freischalten')
    p.add_argument('--minuten', type=int, default=60, help='Dauer in Minuten (Standard 60, höchstens 240)')
    p.add_argument('--aus', action='store_true', help='Notzugang sofort schließen')
    sub.add_parser('sicherung', help='Sicherung jetzt anlegen')
    sub.add_parser('pruefen', help='Datenbank prüfen (nur Anzahlen)')
    p = sub.add_parser('uebernehmen', help='Daten aus der Mac-App übernehmen (ZIP aus „Sicherung herunterladen“)')
    p.add_argument('datei')
    p.add_argument('--ersetzen', action='store_true', help='vorhandene Daten ersetzen (werden vorher gesichert)')
    args = parser.parse_args(argv)
    os.umask(0o077)
    return {'passwort': cmd_passwort, 'benutzer': cmd_benutzer, 'rolle': cmd_rolle, 'notfall-aus': cmd_notfall_aus, 'keycloak-verbinden': cmd_keycloak_verbinden, 'notzugang': cmd_notzugang, 'sicherung': cmd_sicherung,
            'pruefen': cmd_pruefen, 'uebernehmen': cmd_uebernehmen}[args.befehl](args) or 0


if __name__ == '__main__':
    sys.exit(main())
