"""Logik des Mac-Programmfensters (mac/fenster.py) mit nachgebildetem PyObjC – läuft auch ohne macOS.

Geprüft wird, was das Fenster entscheidet (Links, Dokumente, Downloads, Fehler, Schließen). Ob AppKit/WebKit das
Fenster wirklich zeigen, lässt sich nur auf dem Mac prüfen (Abnahme vor dem Einbau). Die Nachbildung kennt nur die
Namen, die fenster.py importieren darf (von Hand mit den Metadaten von PyObjC 12.2.2 abgeglichen) – ein Tippfehler im
Import fällt hier auf.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent
EVENTS = []


class Stub:
    """Cocoa-Klasse, deren Methoden nur aufgezeichnet werden (für Aufbau-Code, der hier nicht läuft)."""

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return lambda *args: EVENTS.append((name, args)) or Stub()

    @classmethod
    def alloc(cls):
        return cls()


class FakeURL:
    def __init__(self, text):
        self.text = text

    def absoluteString(self):
        return self.text

    def scheme(self):
        return self.text.split(':', 1)[0] if ':' in self.text else ''

    def path(self):
        return self.text[len('file://'):] if self.text.startswith('file://') else self.text

    @classmethod
    def URLWithString_(cls, text):
        return cls(text)

    @classmethod
    def fileURLWithPath_(cls, path):
        return cls('file://' + str(path))


class FakeObject:
    @classmethod
    def alloc(cls):
        return cls.__new__(cls)

    def init(self):
        return self


class FakeWorkspace:
    @classmethod
    def sharedWorkspace(cls):
        return cls()

    def openURL_(self, url):
        EVENTS.append(('open', url.absoluteString()))


class FakeApp:
    @classmethod
    def sharedApplication(cls):
        return cls()

    def terminate_(self, sender):
        EVENTS.append(('terminate', sender))

    def activateIgnoringOtherApps_(self, flag):
        EVENTS.append(('activate', flag))

    def performSelector_withObject_afterDelay_(self, selector, obj, delay):
        EVENTS.append(('later', selector))


class FakeSavePanel:
    answer, chosen = 1, None

    @classmethod
    def savePanel(cls):
        return cls()

    def __getattr__(self, name):
        return lambda *args: EVENTS.append(('panel.' + name, args))

    def URL(self):
        if FakeSavePanel.chosen is None:
            raise RuntimeError('kein Pfad')
        return FakeURL.fileURLWithPath_(FakeSavePanel.chosen)

    def beginSheetModalForWindow_completionHandler_(self, window, done):
        done(FakeSavePanel.answer)


class FakeWebView:
    def __init__(self, url='http://127.0.0.1:8769/'):
        self.url = url

    @classmethod
    def alloc(cls):
        return cls(None)

    def initWithFrame_configuration_(self, frame, config):
        return self

    def setNavigationDelegate_(self, delegate):
        self.delegate = delegate

    def URL(self):
        return FakeURL(self.url) if self.url else None

    def reload(self):
        EVENTS.append(('reload', self.url))

    def loadRequest_(self, request):
        EVENTS.append(('load', request))

    def loadHTMLString_baseURL_(self, text, base):
        EVENTS.append(('error-page', text))
        self.url = 'about:blank'

    def evaluateJavaScript_completionHandler_(self, script, handler):
        EVENTS.append(('js', script))


def module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def fake_modules(tmp):
    classes = lambda *names: {n: type(n, (Stub,), {}) for n in names}   # noqa: E731
    return {
        'objc': module('objc', python_method=lambda f: f, super=super),
        'AppKit': module('AppKit', NSAlertFirstButtonReturn=1000, NSApplicationActivationPolicyRegular=0,
                         NSBackingStoreBuffered=2, NSEventModifierFlagCommand=1 << 20, NSEventModifierFlagOption=1 << 19,
                         NSEventModifierFlagShift=1 << 17, NSModalResponseOK=1, NSTerminateCancel=0, NSTerminateNow=1,
                         NSViewHeightSizable=16, NSViewWidthSizable=2, NSWindowStyleMaskClosable=2,
                         NSWindowStyleMaskMiniaturizable=4, NSWindowStyleMaskResizable=8, NSWindowStyleMaskTitled=1,
                         NSWorkspace=FakeWorkspace, NSApplication=FakeApp, NSSavePanel=FakeSavePanel,
                         **classes('NSAlert', 'NSEvent', 'NSImage', 'NSMenu', 'NSMenuItem', 'NSOpenPanel', 'NSPrintInfo',
                                   'NSScreen', 'NSWindow')),
        'Foundation': module('Foundation', NSObject=FakeObject, NSURL=FakeURL, NSTemporaryDirectory=lambda: str(tmp),
                             NSMakeRect=lambda *a: a, NSMakeSize=lambda *a: a, NSURLRequest=types.SimpleNamespace(
                                 requestWithURL_=lambda url: url), **classes('NSBundle')),
        'WebKit': module('WebKit', WKNavigationActionPolicyAllow=1, WKNavigationActionPolicyCancel=0,
                         WKNavigationActionPolicyDownload=2, WKNavigationResponsePolicyAllow=1,
                         WKNavigationResponsePolicyCancel=0, WKNavigationResponsePolicyDownload=2,
                         WKNavigationTypeLinkActivated=0, WKUserScriptInjectionTimeAtDocumentStart=0,
                         WKWebView=FakeWebView, **classes('WKUserScript', 'WKWebViewConfiguration', 'WKWebsiteDataStore')),
        'PyObjCTools': module('PyObjCTools', AppHelper=types.SimpleNamespace(callAfter=lambda f, *a: f(*a))),   # sofort statt Hauptthread
    }


def load(tmp):
    spec = importlib.util.spec_from_file_location('fenster_unter_test', REPO / 'mac' / 'fenster.py')
    loaded = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, fake_modules(tmp)):     # PyObjC nur für diesen Import nachbilden
        spec.loader.exec_module(loaded)
    return loaded


class Action:
    def __init__(self, url, download=False, link=True):
        self.url, self.download, self.link = url, download, link

    def request(self):
        return types.SimpleNamespace(URL=lambda: FakeURL(self.url))

    def shouldPerformDownload(self):
        return self.download

    def navigationType(self):
        return 0 if self.link else -1


class Response:
    def __init__(self, mime, status=200, disposition='', main=True):
        self.mime, self.status, self.disposition, self.main = mime, status, disposition, main

    def response(self):
        return self

    def isForMainFrame(self):
        return self.main

    def MIMEType(self):
        return self.mime

    def statusCode(self):
        return self.status

    def valueForHTTPHeaderField_(self, name):
        return self.disposition if name == 'Content-Disposition' else None


class Error:
    def __init__(self, domain, code, text='Fehler'):
        self._domain, self._code, self.text = domain, code, text

    def domain(self):
        return self._domain

    def code(self):
        return self._code

    def localizedDescription(self):
        return self.text


class Download:
    def setDelegate_(self, delegate):
        self.delegate = delegate


class WindowLogicTests(unittest.TestCase):
    PORT = 8769

    def setUp(self):
        EVENTS.clear()
        FakeSavePanel.answer, FakeSavePanel.chosen = 1, None
        self.tmp = Path(tempfile.mkdtemp())
        self.fenster = load(self.tmp)
        self.state, self.stops, self.alerts, self.answer, self.stop_result = {'app': 'JU Hamburg Finanzen'}, [], [], 0, True
        self.auth_result, self.auth_calls, self.codes = (True, '', False), [], iter(['code-1', 'code-2'])

        def authenticate(done):
            self.auth_calls.append(done)
            if self.auth_result is not None:
                done(*self.auth_result)
        options = {'port': self.PORT, 'status': lambda: self.state,
                   'stop': lambda: self.stops.append(1) or self.stop_result,
                   'log': lambda text: EVENTS.append(('log', text)), 'icon': None,
                   'authenticate': authenticate, 'login': lambda: next(self.codes)}
        self.window = self.fenster.JUFensterDelegate.alloc().initWithURL_options_(
            f'http://127.0.0.1:{self.PORT}/', options)
        self.window.alert = lambda title, text, buttons=('OK',): self.alerts.append((title, text)) or self.answer
        self.window.main_window = Stub()
        self.window.webview = FakeWebView()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def events(self, kind):
        return [e[1] for e in EVENTS if e[0] == kind]

    def decide_action(self, url, download=False, link=True, view=None):
        result = []
        self.window.webView_decidePolicyForNavigationAction_decisionHandler_(view, Action(url, download, link), result.append)
        self.assertEqual(len(result), 1)
        return result[0]

    def decide_response(self, response, view=None):
        result = []
        self.window.webView_decidePolicyForNavigationResponse_decisionHandler_(view, response, result.append)
        self.assertEqual(len(result), 1)
        return result[0]

    def destination(self, download, response, name):
        result = []
        self.window.download_decideDestinationUsingResponse_suggestedFilename_completionHandler_(download, response, name, result.append)
        self.assertEqual(len(result), 1)                      # Handler genau einmal
        return result[0]

    def test_helpers(self):
        f = self.fenster
        self.assertTrue(f.is_local('http://127.0.0.1:8769/api/x.pdf', 8769))
        self.assertTrue(f.is_local('http://localhost:8769/', 8769))
        for other in ('http://127.0.0.1:8770/', 'https://127.0.0.1:8769/', 'http://example.org/', 'file:///etc/passwd', 'x'):
            self.assertFalse(f.is_local(other, 8769), other)
        self.assertEqual(f.download_mode(None, 200, 'inline; filename="R.pdf"', 'R.pdf'), 'open')
        self.assertEqual(f.download_mode(None, 200, 'inline', 'Beleg.JPG'), 'open')
        self.assertEqual(f.download_mode(None, 200, 'inline', 'Rechnung.xml'), 'save')     # nur PDF/Bilder öffnen
        self.assertEqual(f.download_mode('open', 200, '', 'skript.command'), 'save')
        self.assertEqual(f.download_mode(None, 200, 'attachment; filename="p.zip"', 'p.zip'), 'save')
        self.assertEqual(f.download_mode('save', 200, '', 'a.pdf'), 'save')
        self.assertEqual(f.download_mode('open', 401, '', 'a.pdf'), 'error')
        (self.tmp / 'R.pdf').touch()
        self.assertEqual(f.unique_path(self.tmp, '../../R.pdf').name, 'R (2).pdf')   # keine Pfadanteile vom Server
        self.assertEqual(f.unique_path(self.tmp, '..').name, 'Dokument')
        self.assertEqual(f.unique_path(self.tmp, None).parent, self.tmp)
        blob = types.SimpleNamespace(MIMEType=lambda: 'application/zip')                 # Blob: ohne HTTP-Angaben
        self.assertEqual((f._status(blob), f._header(blob, 'Content-Disposition')), (200, ''))
        calls = []
        handler = f.once(calls.append, ('ersatz',))
        handler.finish()
        handler('zu spät')
        self.assertEqual(calls, ['ersatz'])

        def picky(value):
            if value != 'ersatz':
                raise ValueError('nicht übergebbar')
            calls.append(value)
        handler = f.once(picky, ('ersatz',))
        with self.assertRaises(ValueError):
            handler('unpassend')
        handler.finish()
        self.assertEqual(calls, ['ersatz', 'ersatz'])           # Ersatzwert genau einmal nachgereicht

    def test_only_the_local_server_loads_in_the_window(self):
        self.assertEqual(self.decide_action(f'http://127.0.0.1:{self.PORT}/'), 1)
        self.assertEqual(self.decide_action('about:blank'), 1)
        self.assertEqual(self.decide_action('blob:http://127.0.0.1:8769/abc', download=True), 2)
        self.assertEqual(self.decide_action(f'http://127.0.0.1:{self.PORT}/api/backup', download=True), 2)
        for url in ('file:///etc/passwd', 'data:text/html,x', 'javascript:alert(1)', 'blob:http://127.0.0.1:8769/x',
                    'ftp://example.org/'):
            self.assertEqual(self.decide_action(url), 0, url)
        self.assertEqual(self.decide_action('https://www.ju-hamburg.de/'), 0)      # Klick → Standardbrowser
        self.assertEqual(self.decide_action('mailto:schatzmeister@example.org'), 0)
        self.assertEqual(self.decide_action('https://example.org/weiterleitung', link=False), 0)   # ohne Klick: nichts
        self.assertEqual(self.events('open'), ['https://www.ju-hamburg.de/', 'mailto:schatzmeister@example.org'])

    def test_documents_never_replace_the_program(self):
        self.assertEqual(self.decide_response(Response('text/html')), 1)
        self.assertEqual(self.decide_response(Response('application/pdf')), 2)
        self.assertEqual(self.decide_response(Response('text/html', disposition='attachment; filename=a.html')), 2)
        self.assertEqual(self.decide_response(Response('application/pdf', main=False)), 1)

    def test_new_tab_link_opens_document_in_preview(self):
        external = self.window.webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            None, None, Action('https://example.org/'), None)
        self.assertIsNone(external)
        popup = self.window.webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            None, None, Action(f'http://127.0.0.1:{self.PORT}/api/invoice.pdf?id=1'), None)
        self.assertIn(popup, self.window.popups)
        self.assertEqual(self.decide_response(Response('application/pdf'), view=popup), 2)
        download = Download()
        self.window.webView_navigationResponse_didBecomeDownload_(popup, None, download)
        self.assertEqual(self.window.popups, [])
        self.assertIs(self.window.downloads[download]['popup'], popup)
        target = self.destination(download, Response('application/pdf', disposition='inline; filename="R-2026-001.pdf"'), 'R-2026-001.pdf')
        path = Path(target.path())
        self.assertEqual(path.parent.parent, self.tmp / 'JU Hamburg Finanzen')   # eigener Ordner je Download
        path.write_bytes(b'%PDF-1.4')
        self.window.downloadDidFinish_(download)
        self.assertEqual(self.events('open'), ['https://example.org/', target.absoluteString()])   # extern: Browser
        self.assertEqual(self.window.downloads, {})

    def test_popup_without_document(self):
        popup = self.window.webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            None, None, Action(f'http://127.0.0.1:{self.PORT}/'), None)
        self.assertEqual(self.decide_response(Response('text/html'), view=popup), 0)
        self.assertEqual(self.window.popups, [])
        self.assertEqual(len(self.alerts), 1)
        popup = self.window.webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            None, None, Action(f'http://127.0.0.1:{self.PORT}/api/x.pdf'), None)
        self.window.webView_didFailProvisionalNavigation_withError_(popup, None, Error('NSURLErrorDomain', -1004, 'Keine Verbindung'))
        self.assertEqual((self.window.popups, self.alerts[-1][1]), ([], 'Keine Verbindung'))
        self.assertEqual(self.events('error-page'), [])                 # Hauptfenster bleibt, wie es ist

    def test_server_error_is_shown_not_opened(self):
        download = Download()
        self.window.track(download, 'open')
        target = self.destination(download, Response('application/json', status=401), 'x.json')
        Path(target.path()).write_text(json.dumps({'error': 'Bitte anmelden.'}), encoding='utf-8')
        self.window.downloadDidFinish_(download)
        self.assertEqual(self.alerts, [('Die Datei konnte nicht geöffnet werden.', 'Bitte anmelden.')])
        self.assertFalse(Path(target.path()).exists())
        self.assertEqual(self.events('open'), [])

    def test_save_as_replaces_only_after_complete_download(self):
        existing = self.tmp / 'Pruefpaket-2026.zip'
        existing.write_bytes(b'alt')
        FakeSavePanel.chosen = str(existing)
        download = Download()
        self.window.webView_navigationAction_didBecomeDownload_(None, None, download)
        loading = Path(self.destination(download, Response('application/zip', disposition='attachment; filename="Pruefpaket-2026.zip"'),
                                        'Pruefpaket-2026.zip').path())
        self.assertEqual(loading.parent.parent, self.tmp / 'JU Hamburg Finanzen')   # erst in den Zwischenordner
        self.assertEqual(existing.read_bytes(), b'alt')                      # alte Datei bleibt bis zum Ende
        self.assertIn(('panel.setNameFieldStringValue_', ('Pruefpaket-2026.zip',)), EVENTS)
        loading.write_bytes(b'neu')
        self.window.downloadDidFinish_(download)
        self.assertEqual(existing.read_bytes(), b'neu')
        self.assertFalse(loading.exists())
        self.assertEqual((self.events('open'), self.alerts), ([], []))

    def test_failed_download_keeps_old_file(self):
        existing = self.tmp / 'Sicherung.zip'
        existing.write_bytes(b'alt')
        FakeSavePanel.chosen = str(existing)
        download = Download()
        self.window.track(download, 'save')
        loading = Path(self.destination(download, Response('application/zip', disposition='attachment'), 'Sicherung.zip').path())
        loading.write_bytes(b'halb')
        self.window.download_didFailWithError_resumeData_(download, Error('NSURLErrorDomain', -1005, 'Verbindung verloren'), None)
        self.assertEqual(existing.read_bytes(), b'alt')
        self.assertFalse(loading.exists())
        self.assertEqual(self.alerts, [('Die Datei konnte nicht geladen werden.', 'Verbindung verloren')])

    def test_cancel_and_broken_save_panel_still_answer_webkit(self):
        FakeSavePanel.answer = 0                                     # „Abbrechen“
        cancelled = Download()
        self.window.track(cancelled, 'save')
        self.assertIsNone(self.destination(cancelled, Response('application/zip', disposition='attachment'), 'p.zip'))
        self.window.download_didFailWithError_resumeData_(cancelled, Error('NSURLErrorDomain', -999), None)
        self.assertEqual(self.alerts, [])
        FakeSavePanel.answer, FakeSavePanel.chosen = 1, None          # Dialog liefert keinen Pfad → Ausnahme
        broken = Download()
        self.window.track(broken, 'save')
        self.assertIsNone(self.destination(broken, Response('application/zip', disposition='attachment'), 'p.zip'))
        self.assertTrue(any('Sichern unter' in text for text in self.events('log')))

    def test_page_errors(self):
        self.window.webView_didFailProvisionalNavigation_withError_(self.window.webview, None, Error('WebKitErrorDomain', 102))
        self.window.webView_didFailNavigation_withError_(self.window.webview, None, Error('NSURLErrorDomain', -999))
        self.assertEqual(self.events('error-page'), [])
        self.window.webView_didFailProvisionalNavigation_withError_(
            self.window.webview, None, Error('NSURLErrorDomain', -1004, 'Verbindung <abgelehnt>'))
        page = self.events('error-page')[0]
        self.assertIn('Verbindung &lt;abgelehnt&gt;', page)                     # Fehlertext maskiert
        self.assertTrue(any('-1004' in text for text in self.events('log')))
        self.window.reloadPage_(None)                                         # nach Fehlerseite: Startadresse
        self.assertEqual(self.events('load')[-1].absoluteString(), f'http://127.0.0.1:{self.PORT}/')
        self.window.webview.url = f'http://127.0.0.1:{self.PORT}/'
        self.window.reloadPage_(None)
        self.assertEqual(self.events('reload'), [f'http://127.0.0.1:{self.PORT}/'])

    def test_crash_loop_stops(self):
        for _ in range(self.fenster.CRASH_LIMIT):
            self.window.webViewWebContentProcessDidTerminate_(self.window.webview)
        self.assertEqual(len(self.events('reload')), self.fenster.CRASH_LIMIT)
        self.assertEqual(self.events('error-page'), [])
        self.window.webViewWebContentProcessDidTerminate_(self.window.webview)
        self.assertEqual(len(self.events('error-page')), 1)

    def test_javascript_panels_always_answer(self):
        self.window.alert = mock.Mock(side_effect=RuntimeError('kaputt'))
        answers = []
        self.window.webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(
            None, 'Hinweis', None, lambda: answers.append('alert'))
        self.window.webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(
            None, 'Sicher?', None, answers.append)
        self.assertEqual(answers, ['alert', False])
        self.assertEqual(len([t for t in self.events('log') if 'kaputt' in t]), 2)

    def test_same_name_twice_and_failed_move(self):
        FakeSavePanel.chosen = str(self.tmp / 'gesperrt' / 'Datei.xml')    # Zielordner gibt es nicht
        first, second = Download(), Download()
        for d in (first, second):
            self.window.track(d, 'save')
        a = Path(self.destination(first, Response('application/xml', disposition='attachment'), 'Datei.xml').path())
        b = Path(self.destination(second, Response('application/xml', disposition='attachment'), 'Datei.xml').path())
        self.assertNotEqual(a, b)
        self.assertEqual((a.name, b.name), ('Datei.xml', 'Datei.xml'))      # Originalname im Dialog
        a.write_text('x')
        self.window.downloadDidFinish_(first)
        self.assertEqual(self.alerts[0][0], 'Die Datei konnte nicht gespeichert werden.')

    def test_save_panel_failing_before_sheet(self):
        download = Download()
        self.window.track(download, 'save')
        with mock.patch.object(FakeSavePanel, 'savePanel', side_effect=RuntimeError('kein Dialog')):
            self.assertIsNone(self.destination(download, Response('application/zip', disposition='attachment'), 'p.zip'))
        self.assertTrue(any('kein Dialog' in text for text in self.events('log')))

    def test_navigation_error_still_answers(self):
        broken = types.SimpleNamespace(request=mock.Mock(side_effect=RuntimeError('kaputt')))
        result = []
        self.window.webView_decidePolicyForNavigationAction_decisionHandler_(None, broken, result.append)
        self.assertEqual(result, [0])
        response = types.SimpleNamespace(response=mock.Mock(side_effect=RuntimeError('kaputt')))
        self.window.webView_decidePolicyForNavigationResponse_decisionHandler_(None, response, result.append)
        self.assertEqual(result, [0, 1])

    def test_open_panel_answers_once(self):
        class Panel(Stub):
            answer = 1

            @classmethod
            def openPanel(cls):
                return cls()

            def URLs(self):
                return ['file:///beleg.pdf']

            def beginSheetModalForWindow_completionHandler_(self, window, done):
                done(Panel.answer)
        params = types.SimpleNamespace(allowsMultipleSelection=lambda: False)
        with mock.patch.object(self.fenster, 'NSOpenPanel', Panel):
            for answer, expected in ((1, [['file:///beleg.pdf']]), (0, [None])):
                Panel.answer, result = answer, []
                self.window.webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(None, params, None, result.append)
                self.assertEqual(result, expected)
            result = []
            with mock.patch.object(Panel, 'URLs', side_effect=RuntimeError('weg')):
                Panel.answer = 1
                self.window.webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(None, params, None, result.append)
            self.assertEqual(result, [None])

    def test_popups_are_released(self):
        for error in (Error('WebKitErrorDomain', 102), Error('NSURLErrorDomain', -999)):
            popup = self.window.webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
                None, None, Action(f'http://127.0.0.1:{self.PORT}/api/x.pdf'), None)
            self.window.webView_didFailProvisionalNavigation_withError_(popup, None, error)
            self.assertEqual(self.window.popups, [])
        popup = self.window.webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            None, None, Action(f'http://127.0.0.1:{self.PORT}/api/x.pdf'), None)
        self.window.webViewWebContentProcessDidTerminate_(popup)
        self.assertEqual((self.window.popups, self.events('reload')), ([], []))
        self.assertEqual(self.alerts, [])

    def test_closing_with_running_download(self):
        download = Download()
        self.window.track(download, 'save')
        self.answer = 1                                       # „Abbrechen“
        self.assertFalse(self.window.may_close())
        self.assertEqual(self.alerts[-1][0], 'Es wird noch eine Datei geladen.')
        self.answer = 0                                       # „Trotzdem schließen“
        self.assertTrue(self.window.may_close())
        self.assertEqual(self.stops, [1])

    def test_sign_in_with_mac(self):
        self.window.applicationDidFinishLaunching_(None)       # Prüfung erst, wenn die App aktiv ist
        self.assertEqual(self.events('activate'), [True])
        self.assertIn('Angemeldet über macOS.', self.events('log'))
        self.assertEqual(self.events('load')[-1].absoluteString(), f'http://127.0.0.1:{self.PORT}/?login=code-1')
        self.window.userContentController_didReceiveScriptMessage_(None, types.SimpleNamespace(body=lambda: 'login'))
        self.assertEqual(self.events('load')[-1].absoluteString(), f'http://127.0.0.1:{self.PORT}/?login=code-2')
        self.assertEqual(self.alerts, [])

    def test_sign_in_cancelled_or_failed(self):
        self.auth_result = (False, 'Abgebrochen', True)
        self.window.sign_in()
        self.assertEqual((self.events('load'), self.alerts), ([], []))                    # Abbruch: Anmeldeseite bleibt
        self.auth_result = (False, 'Touch ID ist gesperrt.', False)
        self.window.sign_in()
        self.assertEqual(self.alerts, [('Anmeldung nicht möglich.', 'Touch ID ist gesperrt.')])
        self.assertEqual(self.events('load'), [])

    def test_sign_in_once_at_a_time_and_errors(self):
        self.auth_result = None                                                            # Prüfung läuft noch
        self.window.sign_in()
        self.window.sign_in()
        self.assertEqual(len(self.auth_calls), 1)
        self.auth_calls[0](True, '', False)
        self.assertEqual(len(self.events('load')), 1)
        self.window.options['login'] = mock.Mock(side_effect=OSError('Server weg'))
        self.auth_result = (True, '', False)
        self.window.sign_in()
        self.assertEqual(self.alerts[-1], ('Anmeldung nicht möglich.', 'Server weg'))
        self.window.options['authenticate'] = mock.Mock(side_effect=ImportError('LocalAuthentication'))
        self.window.sign_in()
        self.assertIn('nicht verfügbar', self.alerts[-1][1])
        self.assertFalse(self.window.signing)

    def test_closing(self):
        self.assertTrue(self.window.may_close())
        self.assertEqual(self.stops, [1])                     # Fenster zu → Server beendet
        self.assertTrue(self.window.may_close())
        self.assertEqual(self.stops, [1])                     # nur einmal
        self.window.windowWillClose_(types.SimpleNamespace(object=lambda: Stub()))   # anderes Fenster: nichts
        self.assertEqual(self.events('later'), [])
        self.window.windowWillClose_(types.SimpleNamespace(object=lambda: self.window.main_window))
        self.assertEqual(self.events('later'), ['terminate:'])   # auch mit offenem „Über“-Fenster beenden

    def test_failed_stop_is_logged(self):
        self.stop_result = False
        self.assertTrue(self.window.windowShouldClose_(None))
        self.assertTrue(any('nicht beenden' in text for text in self.events('log')))

    def test_closing_during_mail(self):
        self.state = {'app': 'JU Hamburg Finanzen', 'mail_busy': True}
        self.answer = 1                                       # „Abbrechen“
        self.assertFalse(self.window.may_close())
        self.assertEqual(self.window.applicationShouldTerminate_(None), 0)
        self.answer = 0                                       # „Fenster schließen“: Server läuft weiter
        self.assertTrue(self.window.may_close())
        self.assertEqual(self.stops, [])

    def test_other_or_no_server(self):
        self.state = None                                     # beendet oder Server einer anderen Instanz
        self.assertTrue(self.window.windowShouldClose_(None))
        self.assertEqual(self.stops, [])

    def test_command_w_closes_dialog_not_program(self):
        with mock.patch.object(self.fenster.NSMenuItem, 'separatorItem', Stub, create=True):
            self.window.build_menu(Stub())
        items = [e[1][:3] for e in EVENTS if e[0] == 'initWithTitle_action_keyEquivalent_']
        self.assertIn(('Dialog schließen', 'closeDialog:', 'w'), items)
        self.assertFalse([i for i in items if i[2] == 'w' and i[1] != 'closeDialog:'])   # ⌘W nur für Dialoge
        self.window.closeDialog_(None)
        self.assertEqual(self.events('js'), ['window.juCloseTop ? window.juCloseTop() : false'])
        self.assertEqual((self.stops, self.events('terminate'), self.events('later')), ([], [], []))
        self.window.webview = None                            # Fehler werden nur protokolliert
        self.window.closeDialog_(None)
        self.assertTrue(any('Dialog schließen' in text for text in self.events('log')))

    def test_quit_from_page(self):
        self.window.userContentController_didReceiveScriptMessage_(None, types.SimpleNamespace(body=lambda: 'quit'))
        self.assertEqual(self.events('terminate'), [None])
        self.assertEqual(self.window.applicationShouldTerminate_(None), 1)
        self.assertEqual(self.stops, [])                      # Server hat sich selbst beendet


if __name__ == '__main__':
    unittest.main(verbosity=2)
