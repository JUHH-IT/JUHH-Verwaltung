"""Eigenes Programmfenster der Mac-App: die Oberfläche des lokalen Servers in einem macOS-Fenster statt im Browser.

Technik: WKWebView (WebKit, wie Safari) über PyObjC. Das Fenster verhält sich wie eine normale Mac-App:
Menüleiste mit Bearbeiten (Kopieren/Einsetzen), Ablage → Drucken, Darstellung → Zoom. Dokumente (Rechnungs-PDF,
Belege, Berichte) öffnen sich im Standard-PDF-Programm (z. B. Vorschau); Dateien zum Aufbewahren (Prüferpaket, Lastschrift-
datei, CSV, Sicherung) über „Sichern unter …“; Dateiauswahl über den macOS-Dialog; Internet-Links im Standardbrowser.
Sitzungsdaten bleiben nur im Arbeitsspeicher (kein WebKit-Cache mit Finanzdaten auf der Festplatte).

Beim Schließen wird der lokale Server beendet – außer während eines E-Mail-Versands: dann läuft er im Hintergrund
weiter, und die App verbindet sich beim nächsten Öffnen wieder.

Nur macOS ab 11.3. Der Starter (launcher.py) öffnet den Browser, wenn dieses Modul nicht geladen werden kann
oder das Fenster nicht startet; die Wahltaste (⌥) beim Öffnen und Daten/.im-browser-oeffnen prüft er selbst.
"""
import html
import json
import shutil
import tempfile
import time
import traceback
import urllib.parse
from pathlib import Path

import objc
from AppKit import (NSAlert, NSAlertFirstButtonReturn, NSApplication, NSApplicationActivationPolicyRegular,
                    NSBackingStoreBuffered, NSEventModifierFlagCommand, NSEventModifierFlagOption,
                    NSEventModifierFlagShift, NSImage, NSMenu, NSMenuItem, NSModalResponseOK, NSOpenPanel, NSPrintInfo,
                    NSSavePanel, NSScreen, NSTerminateCancel, NSTerminateNow, NSViewHeightSizable, NSViewWidthSizable,
                    NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
                    NSWindowStyleMaskTitled, NSWorkspace)
from Foundation import NSURL, NSBundle, NSMakeRect, NSMakeSize, NSObject, NSTemporaryDirectory, NSURLRequest
from PyObjCTools import AppHelper
from WebKit import (WKNavigationActionPolicyAllow, WKNavigationActionPolicyCancel, WKNavigationActionPolicyDownload,
                    WKNavigationResponsePolicyAllow, WKNavigationResponsePolicyCancel, WKNavigationResponsePolicyDownload,
                    WKNavigationTypeLinkActivated, WKUserScript, WKUserScriptInjectionTimeAtDocumentStart, WKWebView,
                    WKWebViewConfiguration, WKWebsiteDataStore)

APP_NAME = 'JU Hamburg Finanzen'
ZOOM_STEPS = (0.8, 0.9, 1.0, 1.1, 1.25, 1.5)
CANCELLED = -999                                # NSURLErrorCancelled
IGNORED_ERRORS = {('NSURLErrorDomain', CANCELLED),
                  ('WebKitErrorDomain', 102),    # Laden unterbrochen, weil die Antwort ein Download wurde
                  ('WebKitErrorDomain', 204),    # Inhalt von einem Plug-in übernommen
                  ('WKErrorDomain', 2)}          # Anzeigeprozess beendet (eigene Behandlung mit Neuladen)
DOCUMENT_TYPES = {'.pdf', '.png', '.jpg', '.jpeg'}   # nur diese öffnet das Fenster direkt in der Standard-App
CRASH_LIMIT = 3                                 # automatische Neustarts der Seite je Minute

# Seite → App: „Drucken“-Knöpfe der Oberfläche nutzen den macOS-Druckdialog (window.print gibt es in WKWebView nicht).
BRIDGE = "window.print = function () { window.webkit.messageHandlers.ju.postMessage('print'); };"

ERROR_PAGE = """<!doctype html><meta charset="utf-8"><body style="font:15px -apple-system,sans-serif;padding:48px;
color:#1d1d1f"><h2>Die Seite konnte nicht geladen werden.</h2><p>Deine Daten sind gespeichert. Erst <b>⌘R</b>
(neu laden) versuchen. Hilft das nicht: App mit ⌘Q beenden und erneut öffnen – mit gedrückter Wahltaste (⌥) öffnet
sie sich im Browser. Der Grund steht in „Daten/programm.log“.</p>
<p style="color:#6e6e73">{reason}</p></body>"""


def supported():
    """Fenster nur, wenn WebKit Downloads steuern kann (macOS 11.3 und neuer)."""
    return hasattr(WKWebView, 'startDownloadUsingRequest_completionHandler_')


def is_local(address, port):
    try:
        u = urllib.parse.urlsplit(str(address))
        return u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost') and u.port == port
    except ValueError:
        return False


def is_attachment(disposition):
    return str(disposition or '').strip().lower().startswith('attachment')


def unique_path(folder, name):
    """Freier Dateiname im Ordner, ohne Pfadanteile aus dem Vorschlag des Servers."""
    name = Path(str(name or '')).name
    if name in ('', '.', '..'):
        name = 'Dokument'
    target, stem, suffix, n = Path(folder) / name, Path(name).stem, Path(name).suffix, 1
    while target.exists():
        n += 1
        target = Path(folder) / f'{stem} ({n}){suffix}'
    return target


def download_mode(preset, status_code, disposition, filename=''):
    """open = als Dokument öffnen, save = „Sichern unter …“, error = Fehlermeldung des Servers anzeigen.

    Direkt geöffnet werden nur PDF und Bilder; alles andere wird gesichert.
    """
    if status_code >= 400:
        return 'error'
    mode = preset or ('save' if is_attachment(disposition) else 'open')
    if mode == 'open' and Path(str(filename or '')).suffix.lower() not in DOCUMENT_TYPES:
        return 'save'
    return mode


def error_text(path):
    try:
        message = json.loads(Path(path).read_text(encoding='utf-8')).get('error')
        return str(message) if message else None
    except (OSError, ValueError, AttributeError):
        return None


def once(handler, fallback):
    """Completion-Handler von WebKit/AppKit genau einmal aufrufen – auch wenn vorher etwas schiefgeht."""
    state = {'done': False}

    def call(*args):
        if state['done']:
            return
        state['done'] = True
        try:
            handler(*args)
        except Exception:
            if args != tuple(fallback):      # z. B. Wert ließ sich nicht übergeben → wenigstens den Ersatzwert
                handler(*fallback)
            raise

    def finish():
        call(*fallback)
    call.finish = finish
    return call


def _header(response, name):
    try:
        return str(response.valueForHTTPHeaderField_(name) or '')
    except AttributeError:                       # keine HTTP-Antwort (z. B. blob:)
        return ''


def _status(response):
    try:
        return int(response.statusCode())
    except AttributeError:
        return 200


class JUFensterDelegate(NSObject):
    """App-, Fenster-, Navigations-, Oberflächen- und Download-Delegate in einem."""

    def initWithURL_options_(self, url, options):
        self = objc.super(JUFensterDelegate, self).init()
        if self is None:
            return None
        self.start_url = url
        self.options = options          # port, status(), stop(), log(text), icon, authenticate(done), login() → Code
        self.downloads = {}             # WKDownload → {'mode', 'path' (Ladeort), 'target' (Sichern unter), 'popup'}
        self.popups = []                # unsichtbare Hilfsansichten für Links mit target=_blank
        self.crashes = []
        self.closing_ok = False
        self.signing = False
        self.main_window = None
        self.webview = None
        self.documents = Path(NSTemporaryDirectory()) / APP_NAME
        shutil.rmtree(self.documents, ignore_errors=True)       # Dokumente der letzten Sitzung entfernen
        self.documents.mkdir(parents=True, exist_ok=True)
        return self

    # ------------------------------------------------------------ Hilfen

    @objc.python_method
    def log(self, text):
        try:
            self.options['log'](text)
        except Exception:
            pass

    @objc.python_method
    def alert(self, title, text, buttons=('OK',)):
        """Hinweisdialog; liefert die Nummer des gewählten Knopfs (0 = erster)."""
        box = NSAlert.alloc().init()
        box.setMessageText_(title)
        box.setInformativeText_(text or '')
        for button in buttons:
            box.addButtonWithTitle_(button)
        return int(box.runModal()) - NSAlertFirstButtonReturn

    @objc.python_method
    def local(self, url):
        return url is not None and is_local(url.absoluteString(), self.options['port'])

    @objc.python_method
    def open_outside(self, url):
        if url is not None and str(url.scheme() or '').lower() in ('http', 'https', 'mailto'):
            NSWorkspace.sharedWorkspace().openURL_(url)

    @objc.python_method
    def show_error(self, reason):
        self.webview.loadHTMLString_baseURL_(ERROR_PAGE.replace('{reason}', html.escape(str(reason or ''))), None)

    # ------------------------------------------------------------ Aufbau

    @objc.python_method
    def build(self, app):
        try:   # Name in der Menüleiste, falls macOS das Bundle nicht erkennt
            info = NSBundle.mainBundle().infoDictionary()
            if info is not None and info.get('CFBundleName') != APP_NAME:
                info['CFBundleName'] = APP_NAME
        except Exception:
            pass
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        icon = self.options.get('icon')
        if icon and Path(icon).is_file():
            image = NSImage.alloc().initWithContentsOfFile_(str(icon))
            if image is not None:
                app.setApplicationIconImage_(image)
        app.setDelegate_(self)
        self.build_menu(app)

        config = WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
        controller = config.userContentController()
        controller.addUserScript_(WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            BRIDGE, WKUserScriptInjectionTimeAtDocumentStart, True))
        controller.addScriptMessageHandler_name_(self, 'ju')

        width, height = 1280, 840
        screen = NSScreen.mainScreen()
        if screen is not None:
            area = screen.visibleFrame().size
            width, height = min(width, area.width * 0.95), min(height, area.height * 0.95)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, width, height), style,
                                                                             NSBackingStoreBuffered, False)
        window.setTitle_(APP_NAME)
        window.setMinSize_(NSMakeSize(900, 600))
        window.setReleasedWhenClosed_(False)
        window.setRestorable_(False)            # kein gespeicherter Fensterzustand mit Finanzdaten
        if not window.setFrameUsingName_('JUFinanzenFenster'):
            window.center()
        window.setFrameAutosaveName_('JUFinanzenFenster')
        window.setDelegate_(self)

        webview = WKWebView.alloc().initWithFrame_configuration_(window.contentView().bounds(), config)
        webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        webview.setNavigationDelegate_(self)
        webview.setUIDelegate_(self)
        webview.setAllowsBackForwardNavigationGestures_(False)
        window.setContentView_(webview)
        self.main_window, self.webview = window, webview
        webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(self.start_url)))
        window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def build_menu(self, app):
        command, option, shift = NSEventModifierFlagCommand, NSEventModifierFlagOption, NSEventModifierFlagShift

        def item(title, action, key='', mods=command, target=None):
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            entry.setKeyEquivalentModifierMask_(mods)
            if target is not None:
                entry.setTarget_(target)
            return entry

        def menu(title, entries):
            submenu = NSMenu.alloc().initWithTitle_(title)
            for entry in entries:
                submenu.addItem_(entry if entry is not None else NSMenuItem.separatorItem())
            holder = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, '')
            holder.setSubmenu_(submenu)
            bar.addItem_(holder)
            return submenu

        bar = NSMenu.alloc().initWithTitle_('Hauptmenü')
        menu(APP_NAME, [item(f'Über {APP_NAME}', 'orderFrontStandardAboutPanel:', target=app), None,
                        item(f'{APP_NAME} ausblenden', 'hide:', 'h', target=app),
                        item('Andere ausblenden', 'hideOtherApplications:', 'h', command | option, target=app),
                        item('Alle einblenden', 'unhideAllApplications:', target=app), None,
                        item(f'{APP_NAME} beenden', 'terminate:', 'q', target=app)])
        # ⌘W schließt nur den obersten Dialog der Oberfläche, nie das Programm (Wunsch Lennert Gust 29.10.2026);
        # Programm beenden: ⌘Q, „Programm beenden“ oder der rote Knopf des Fensters.
        menu('Ablage', [item('Drucken …', 'printPage:', 'p', target=self), None,
                        item('Dialog schließen', 'closeDialog:', 'w', target=self)])
        menu('Bearbeiten', [item('Widerrufen', 'undo:', 'z'), item('Wiederholen', 'redo:', 'z', command | shift), None,
                            item('Ausschneiden', 'cut:', 'x'), item('Kopieren', 'copy:', 'c'),
                            item('Einsetzen', 'paste:', 'v'), item('Alles auswählen', 'selectAll:', 'a')])
        menu('Darstellung', [item('Neu laden', 'reloadPage:', 'r', target=self), None,
                             item('Größer', 'zoomIn:', '+', target=self), item('Kleiner', 'zoomOut:', '-', target=self),
                             item('Originalgröße', 'zoomReset:', '0', target=self)])
        windows = menu('Fenster', [item('Im Dock ablegen', 'performMiniaturize:', 'm'), item('Zoomen', 'performZoom:')])
        app.setMainMenu_(bar)
        app.setWindowsMenu_(windows)

    # ------------------------------------------------------------ Menübefehle

    def printPage_(self, sender):
        try:
            operation = self.webview.printOperationWithPrintInfo_(NSPrintInfo.sharedPrintInfo())
            operation.setShowsPrintPanel_(True)
            operation.setShowsProgressPanel_(True)
            operation.view().setFrame_(self.webview.bounds())
            operation.runOperationModalForWindow_delegate_didRunSelector_contextInfo_(self.main_window, None, None, None)
        except Exception:
            self.log('Drucken fehlgeschlagen:\n' + traceback.format_exc())

    def closeDialog_(self, sender):
        """Obersten Dialog bzw. die Liste darüber schließen (wie Esc); ohne offenen Dialog passiert nichts."""
        try:
            self.webview.evaluateJavaScript_completionHandler_('window.juCloseTop ? window.juCloseTop() : false', None)
        except Exception:
            self.log('Dialog schließen:\n' + traceback.format_exc())

    def reloadPage_(self, sender):
        """Aktuelle Seite neu laden; nach einer Fehlerseite die Startadresse (Anmeldeseite bzw. Programm)."""
        if self.local(self.webview.URL()):
            self.webview.reload()
        else:
            self.webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(self.start_url)))

    @objc.python_method
    def zoom(self, step):
        current = float(self.webview.pageZoom())
        index = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - current))
        target = ZOOM_STEPS[max(0, min(len(ZOOM_STEPS) - 1, index + step))] if step else 1.0
        self.webview.setPageZoom_(target)

    def zoomIn_(self, sender):
        self.zoom(1)

    def zoomOut_(self, sender):
        self.zoom(-1)

    def zoomReset_(self, sender):
        self.zoom(0)

    # ------------------------------------------------------------ App und Fenster

    def applicationDidFinishLaunching_(self, notification):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.sign_in()                         # erst als aktive App nach Touch ID bzw. Mac-Passwort fragen

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        if self.main_window is not None:
            self.main_window.makeKeyAndOrderFront_(None)
        return True

    @objc.python_method
    def may_close(self):
        """Vor dem Schließen: während eines E-Mail-Versands nachfragen, sonst den Server beenden.

        status() liefert nur den Server, mit dem dieses Fenster gestartet wurde (sonst None) – ein altes Fenster
        beendet nie den Server einer neueren Version.
        """
        if self.closing_ok:
            return True
        loading = [e for e in self.downloads.values() if e.get('mode') not in ('cancelled', 'error')]
        if loading and self.alert('Es wird noch eine Datei geladen.', 'Beim Schließen geht sie verloren.',
                                  ('Trotzdem schließen', 'Abbrechen')) != 0:
            return False
        state = self.options['status']() or {}
        if state.get('mail_busy'):
            choice = self.alert('Gerade läuft ein E-Mail-Versand.',
                                'Der Versand läuft im Hintergrund weiter. Öffne die App später erneut, um das Ergebnis zu '
                                'sehen.', ('Fenster schließen', 'Abbrechen'))
            if choice != 0:
                return False
        elif state and not self.options['stop']():
            self.log('Server ließ sich beim Schließen nicht beenden – er läuft weiter.')
        self.closing_ok = True
        return True

    def windowShouldClose_(self, window):
        return bool(self.may_close())

    def windowWillClose_(self, notification):
        if notification.object() == self.main_window:     # auch wenn noch das „Über“-Fenster offen ist
            NSApplication.sharedApplication().performSelector_withObject_afterDelay_('terminate:', None, 0)

    def applicationShouldTerminate_(self, app):
        return NSTerminateNow if self.may_close() else NSTerminateCancel

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

    # ------------------------------------------------------------ Anmeldung mit macOS

    @objc.python_method
    def sign_in(self):
        """Touch ID / Apple Watch / Mac-Passwort prüfen lassen; danach mit einmaligem Code anmelden."""
        if self.signing:
            return
        self.signing = True
        try:
            self.options['authenticate'](lambda ok, message, cancelled: AppHelper.callAfter(self.signed_in, ok, message, cancelled))
        except Exception as exc:
            self.signing = False
            self.log('Anmeldung über macOS nicht möglich:\n' + traceback.format_exc())
            self.alert('Anmeldung nicht möglich.', f'Touch ID bzw. Mac-Passwort sind nicht verfügbar ({exc}).')

    @objc.python_method
    def signed_in(self, ok, message, cancelled):
        self.signing = False
        if not ok:
            self.log('Anmeldung nicht erfolgt: ' + (message or 'abgebrochen'))
            if not cancelled:
                self.alert('Anmeldung nicht möglich.', message)
            return
        try:
            code = self.options['login']()
        except Exception as exc:
            self.log('Anmeldecode nicht erhalten:\n' + traceback.format_exc())
            self.alert('Anmeldung nicht möglich.', str(exc))
            return
        address = f'http://127.0.0.1:{self.options["port"]}/?login={code}'
        self.log('Angemeldet über macOS.')
        self.webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(address)))

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = str(message.body())
        if body == 'login':
            self.sign_in()
        elif body == 'print':
            self.printPage_(None)
        elif body == 'quit':             # „Programm beenden“ in der Oberfläche: Server ist schon beendet
            self.closing_ok = True
            NSApplication.sharedApplication().terminate_(None)

    # ------------------------------------------------------------ Navigation

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, handler):
        """Nur der lokale Server darf im Fenster laden; Internet-Links nach Klick im Standardbrowser, sonst nichts."""
        decision = WKNavigationActionPolicyCancel
        try:
            url = action.request().URL()
            scheme = str(url.scheme() or '').lower()
            if action.shouldPerformDownload() and (self.local(url) or scheme == 'blob'):
                decision = WKNavigationActionPolicyDownload          # <a download> und Blob-Downloads
            elif self.local(url) or str(url.absoluteString()) in ('about:blank', 'about:srcdoc'):
                decision = WKNavigationActionPolicyAllow
            elif scheme in ('http', 'https', 'mailto') and action.navigationType() == WKNavigationTypeLinkActivated:
                self.open_outside(url)
            else:
                self.log(f'Navigation abgelehnt: {scheme}:')
        except Exception:
            self.log('Navigation:\n' + traceback.format_exc())
        handler(decision)

    def webView_decidePolicyForNavigationResponse_decisionHandler_(self, webview, navigation, handler):
        decision, notice = WKNavigationResponsePolicyAllow, False
        try:
            response = navigation.response()
            document = str(response.MIMEType() or '') == 'text/html' and not is_attachment(_header(response, 'Content-Disposition'))
            if webview in self.popups:
                decision = WKNavigationResponsePolicyCancel if document else WKNavigationResponsePolicyDownload
                if document:
                    self.popups.remove(webview)
                    self.log('Neues Fenster mit HTML-Seite verworfen.')
                    notice = True
            elif navigation.isForMainFrame() and not document:
                decision = WKNavigationResponsePolicyDownload            # Dateien nie im Programmfenster anzeigen
        except Exception:
            self.log('Antwort:\n' + traceback.format_exc())
        handler(decision)
        if notice:                                                       # Hinweis erst nach der Entscheidung
            self.alert('Das Dokument konnte nicht geöffnet werden.', 'Statt einer Datei kam eine Seite zurück.')

    def webView_navigationAction_didBecomeDownload_(self, webview, action, download):
        self.track(download, 'save', webview)

    def webView_navigationResponse_didBecomeDownload_(self, webview, navigation, download):
        self.track(download, None, webview)

    @objc.python_method
    def failed(self, webview, error):
        if error is None:
            return
        domain, code = str(error.domain()), int(error.code())
        if (domain, code) in IGNORED_ERRORS:
            if webview in self.popups:
                self.popups.remove(webview)
            return
        text = str(error.localizedDescription())
        self.log(f'Laden fehlgeschlagen ({domain} {code}): {text}')
        if webview in self.popups:
            self.popups.remove(webview)
            self.alert('Das Dokument konnte nicht geöffnet werden.', text)
        elif webview == self.webview:
            self.show_error(text)

    def webView_didFailProvisionalNavigation_withError_(self, webview, navigation, error):
        self.failed(webview, error)

    def webView_didFailNavigation_withError_(self, webview, navigation, error):
        self.failed(webview, error)

    def webViewWebContentProcessDidTerminate_(self, webview):
        if webview != self.webview:
            if webview in self.popups:
                self.popups.remove(webview)
            return
        now = time.monotonic()
        self.crashes = [t for t in self.crashes if now - t < 60] + [now]
        self.log('Anzeigeprozess beendet – Seite wird neu geladen.')
        if len(self.crashes) > CRASH_LIMIT:
            self.show_error('Die Anzeige ist mehrfach abgestürzt.')
        else:
            self.reloadPage_(None)

    def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(self, webview, config, action, features):
        """Links mit target=_blank (PDF, Belege): unsichtbar laden und als Dokument in der Standard-App öffnen.

        Die Hilfsansicht lädt wie ein neuer Browser-Tab (Sitzungs-Cookie mit SameSite=Strict wird mitgeschickt).
        """
        try:
            url = action.request().URL()
            if not self.local(url):
                self.open_outside(url)
                return None
            popup = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 800, 600), config)
            popup.setNavigationDelegate_(self)
            self.popups.append(popup)
            return popup
        except Exception:
            self.log('Neues Fenster:\n' + traceback.format_exc())
            return None

    def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(self, webview, parameters, frame, handler):
        handler = once(handler, (None,))
        try:
            panel = NSOpenPanel.openPanel()
            panel.setCanChooseFiles_(True)
            panel.setCanChooseDirectories_(False)
            panel.setAllowsMultipleSelection_(bool(parameters.allowsMultipleSelection()))

            def chosen(result):
                try:
                    handler(panel.URLs() if result == NSModalResponseOK else None)
                except Exception:
                    self.log('Dateiauswahl:\n' + traceback.format_exc())
                finally:
                    handler.finish()
            panel.beginSheetModalForWindow_completionHandler_(self.main_window, chosen)
        except Exception:
            self.log('Dateiauswahl:\n' + traceback.format_exc())
            handler.finish()

    def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(self, webview, message, frame, handler):
        try:
            self.alert(APP_NAME, str(message))
        except Exception:
            self.log('Hinweis:\n' + traceback.format_exc())
        finally:
            handler()

    def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(self, webview, message, frame, handler):
        answer = False
        try:
            answer = self.alert(APP_NAME, str(message), ('OK', 'Abbrechen')) == 0
        except Exception:
            self.log('Rückfrage:\n' + traceback.format_exc())
        finally:
            handler(answer)

    # ------------------------------------------------------------ Downloads

    @objc.python_method
    def track(self, download, mode, popup=None):
        download.setDelegate_(self)
        entry = self.downloads.setdefault(download, {'mode': None, 'path': None, 'target': None, 'popup': None})
        entry['mode'] = entry['mode'] or mode
        if popup is not None and popup in self.popups:
            self.popups.remove(popup)
            entry['popup'] = popup       # bis zum Ende des Downloads behalten

    def download_decideDestinationUsingResponse_suggestedFilename_completionHandler_(self, download, response, filename, handler):
        """Immer zuerst in den eigenen Zwischenordner laden; „Sichern unter“ ersetzt eine vorhandene Datei erst am Ende."""
        handler = once(handler, (None,))
        try:
            entry = self.downloads.setdefault(download, {'mode': None, 'path': None, 'target': None, 'popup': None})
            entry['mode'] = download_mode(entry['mode'], _status(response), _header(response, 'Content-Disposition'), filename)
            entry['path'] = unique_path(tempfile.mkdtemp(dir=self.documents), filename)   # eigener Ordner je Download
            if entry['mode'] in ('open', 'error'):
                handler(NSURL.fileURLWithPath_(str(entry['path'])))
                return
            panel = NSSavePanel.savePanel()
            panel.setNameFieldStringValue_(entry['path'].name)
            downloads = Path.home() / 'Downloads'
            if downloads.is_dir():
                panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(downloads)))

            def chosen(result):
                try:
                    if result == NSModalResponseOK and panel.URL() is not None:
                        entry['target'] = Path(str(panel.URL().path()))
                        handler(NSURL.fileURLWithPath_(str(entry['path'])))
                    else:
                        entry['mode'] = 'cancelled'
                except Exception:
                    self.log('Sichern unter:\n' + traceback.format_exc())
                finally:
                    handler.finish()
            panel.beginSheetModalForWindow_completionHandler_(self.main_window, chosen)
        except Exception:
            self.log('Download:\n' + traceback.format_exc())
            handler.finish()

    def downloadDidFinish_(self, download):
        entry = self.downloads.pop(download, None) or {}
        path, mode = entry.get('path'), entry.get('mode')
        try:
            if mode == 'open' and path:
                NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))
            elif mode == 'save' and path and entry.get('target'):
                shutil.move(str(path), str(entry['target']))
            elif mode == 'error' and path:
                message = error_text(path)
                Path(path).unlink(missing_ok=True)
                self.alert('Die Datei konnte nicht geöffnet werden.', message or 'Der Server hat einen Fehler gemeldet.')
        except Exception as exc:
            self.log('Download fertig:\n' + traceback.format_exc())
            self.alert('Die Datei konnte nicht gespeichert werden.', str(exc))

    def download_didFailWithError_resumeData_(self, download, error, data):
        entry = self.downloads.pop(download, None) or {}
        if entry.get('path'):
            Path(entry['path']).unlink(missing_ok=True)
        if entry.get('mode') == 'cancelled' or (error is not None and error.code() == CANCELLED):
            return
        text = str(error.localizedDescription()) if error is not None else ''
        self.log('Download fehlgeschlagen: ' + text)
        self.alert('Die Datei konnte nicht geladen werden.', text)


def run(url, *, port, status, stop, log, authenticate, login, icon=None):
    """Fenster öffnen und die App laufen lassen; endet erst mit dem Beenden der App (Prozessende).

    status() liefert den Server dieses Fensters oder None, stop() beendet ihn (True/False), log(text) schreibt ins
    Protokoll, authenticate(done) startet die Prüfung durch macOS, login() holt danach einen Anmeldecode.
    Fehlerausgaben leitet der Starter ins Protokoll um.
    """
    app = NSApplication.sharedApplication()
    delegate = JUFensterDelegate.alloc().initWithURL_options_(
        url, {'port': port, 'status': status, 'stop': stop, 'log': log, 'icon': icon, 'authenticate': authenticate, 'login': login})
    delegate.build(app)
    log(f'Fenster geöffnet (App: {NSBundle.mainBundle().bundleIdentifier() or "unbekannt"}).')
    app.run()
    return 0
