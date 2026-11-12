"""Anmeldung mit macOS: Touch ID, Apple Watch oder das Passwort des Mac-Benutzers (LocalAuthentication).

Es gibt kein eigenes Passwort des Programms mehr. Nach erfolgreicher Prüfung holen Starter bzw. Programmfenster
beim lokalen Server einen einmaligen Anmeldecode (`/api/login-code`, mit dem Steuerschlüssel aus Daten/.steuerung).
Ist die Prüfung nicht möglich oder wird sie abgebrochen, gibt es keine Anmeldung („fail closed“).
"""
import threading

REASON = 'die Finanzdaten zu öffnen'           # macOS zeigt: „JU Hamburg Finanzen“ versucht, die Finanzdaten zu öffnen.
CANCELLED = {-2, -9}                           # LAErrorUserCancel, LAErrorAppCancel – ohne Meldung; Abbruch durch das
                                               # System (-4) wird gemeldet, sonst bliebe der Start im Browser stumm
_PENDING = []                                  # laufende Prüfungen bis zur Antwort festhalten


def _message(error):
    if error is None:
        return ''
    try:
        return str(error.localizedDescription())
    except AttributeError:
        return str(error)


def authenticate(done, reason=REASON):
    """Prüfung starten; done(ok, meldung, abgebrochen) wird später aufgerufen – auf einem Hintergrund-Thread von macOS.
    Liefert den laufenden LAContext (zum Abbrechen mit invalidate()) oder None, wenn keine Prüfung möglich ist."""
    from LocalAuthentication import LAContext, LAPolicyDeviceOwnerAuthentication
    context = LAContext.alloc().init()
    possible, error = context.canEvaluatePolicy_error_(LAPolicyDeviceOwnerAuthentication, None)
    if not possible:
        done(False, _message(error) or 'Touch ID und Mac-Passwort sind auf diesem Mac nicht verfügbar.', False)
        return None
    _PENDING.append(context)

    def reply(success, error):
        try:
            _PENDING.remove(context)
        except ValueError:
            pass
        code = None
        try:
            code = int(error.code()) if error is not None else None
        except AttributeError:
            pass
        done(bool(success), '' if success else _message(error), code in CANCELLED)
    context.evaluatePolicy_localizedReason_reply_(LAPolicyDeviceOwnerAuthentication, reason, reply)
    return context


def authenticate_and_wait(reason=REASON, timeout=600):
    """Für den Starter (Browser-Modus): Prüfung starten und warten. Liefert (ok, meldung, abgebrochen)."""
    finished, result = threading.Event(), {}

    def done(ok, message, cancelled):
        result.update(ok=ok, message=message, cancelled=cancelled)
        finished.set()
    context = authenticate(done, reason)
    if not finished.wait(timeout):
        if context is not None:
            context.invalidate()                   # Dialog schließen; die Antwort (-9) wird danach ignoriert
        return False, 'Keine Antwort von macOS.', False
    return result['ok'], result['message'], result['cancelled']
