"""Anmeldung über macOS (mac/anmeldung.py) mit nachgebildetem LocalAuthentication – läuft auch unter Linux."""
import os
os.environ['JU_FINANZEN_OHNE_SCHLUESSELBUND'] = '1'
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / 'mac'))
import anmeldung  # noqa: E402


class FakeError:
    def __init__(self, code, text):
        self._code, self._text = code, text

    def code(self):
        return self._code

    def localizedDescription(self):
        return self._text


class FakeContext:
    possible, error = True, None          # canEvaluatePolicy
    answer = (True, None)                 # Antwort der Prüfung; None = keine Antwort
    created = []

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        self.invalidated, self.reply = False, None
        FakeContext.created.append(self)
        return self

    def canEvaluatePolicy_error_(self, policy, error):
        assert policy == 2 and error is None
        return self.possible, self.error

    def evaluatePolicy_localizedReason_reply_(self, policy, reason, reply):
        assert policy == 2 and reason == anmeldung.REASON
        self.reply = reply
        if self.answer is not None:      # wie macOS: Antwort auf einem anderen Thread
            threading.Thread(target=reply, args=self.answer).start()

    def invalidate(self):
        self.invalidated = True
        if self.reply:
            self.reply(False, FakeError(-9, 'App hat abgebrochen'))


class AnmeldungTests(unittest.TestCase):
    def setUp(self):
        FakeContext.possible, FakeContext.error, FakeContext.answer, FakeContext.created = True, None, (True, None), []
        module = types.ModuleType('LocalAuthentication')
        module.LAContext, module.LAPolicyDeviceOwnerAuthentication = FakeContext, 2
        patcher = mock.patch.dict(sys.modules, {'LocalAuthentication': module})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_success(self):
        self.assertEqual(anmeldung.authenticate_and_wait(timeout=5), (True, '', False))
        self.assertEqual(anmeldung._PENDING, [])

    def test_cancel_and_failures(self):
        for code, text, cancelled in ((-2, 'Abgebrochen', True), (-9, 'App hat abgebrochen', True),
                                      (-4, 'System hat abgebrochen', False),     # wird gemeldet (sonst stummer Start)
                                      (-1, 'Falsches Passwort', False), (-1004, 'Nicht interaktiv', False)):
            FakeContext.answer = (False, FakeError(code, text))
            self.assertEqual(anmeldung.authenticate_and_wait(timeout=5), (False, text, cancelled), code)
        self.assertEqual(anmeldung._PENDING, [])

    def test_not_possible_fails_closed(self):
        FakeContext.possible, FakeContext.error = False, FakeError(-5, 'Kein Mac-Passwort festgelegt')
        self.assertEqual(anmeldung.authenticate_and_wait(timeout=5), (False, 'Kein Mac-Passwort festgelegt', False))
        FakeContext.error = None
        ok, message, cancelled = anmeldung.authenticate_and_wait(timeout=5)
        self.assertEqual((ok, cancelled), (False, False))
        self.assertIn('nicht verfügbar', message)

    def test_timeout_closes_dialog(self):
        FakeContext.answer = None
        self.assertEqual(anmeldung.authenticate_and_wait(timeout=0.2), (False, 'Keine Antwort von macOS.', False))
        self.assertTrue(FakeContext.created[-1].invalidated)
        self.assertEqual(anmeldung._PENDING, [])

    def test_without_framework(self):
        with mock.patch.dict(sys.modules, {'LocalAuthentication': None}):
            with self.assertRaises(ImportError):
                anmeldung.authenticate(lambda *a: None)


if __name__ == '__main__':
    unittest.main()
