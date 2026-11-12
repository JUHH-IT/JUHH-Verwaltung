"""Startet app.py für die Browser-Durchläufe mit erfundenen Adressdaten statt des Abrufs bei der Stadt – Tests gehen nie ins Netz.
Aufruf wie app.py (gleiche Argumente); nur tests/ui_smoke.mjs und tests/ui_server.mjs nutzen diese Datei."""
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import adressen      # noqa: E402
import test_karte    # noqa: E402

DATA = test_karte.ui_fixtures()
adressen.MIN_ROWS = 10
adressen.fetch_page = lambda offset, version='': {'numberMatched': len(DATA), 'features': DATA[offset:offset + adressen.PAGE]}
sys.argv = [str(REPO / 'app.py'), *sys.argv[1:]]
runpy.run_path(str(REPO / 'app.py'), run_name='__main__')
