"""Startet app.py für die Browser-Durchläufe mit erfundenen Adressdaten statt des Abrufs bei der Stadt und einer erfundenen
Bedrohungsliste statt des Abrufs bei Spamhaus – Tests gehen nie ins Netz.
Aufruf wie app.py (gleiche Argumente); nur tests/ui_smoke.mjs und tests/ui_server.mjs nutzen diese Datei."""
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import adressen      # noqa: E402
import schutz        # noqa: E402
import test_karte    # noqa: E402

DATA = test_karte.ui_fixtures()
adressen.MIN_ROWS = 10
adressen.fetch_page = lambda offset, version='': {'numberMatched': len(DATA), 'features': DATA[offset:offset + adressen.PAGE]}
# Bedrohungsliste im Format von Spamhaus DROP aus Dokumentationsnetzen (192.0.2.0/24 lückenlos, zwei IPv6-Netze)
DROP = {schutz.QUELLEN[0]: '; erfundene Testdaten\n' + '\n'.join(f'192.0.2.{i * 4}/30 ; SBL{1000 + i}' for i in range(64)),
        schutz.QUELLEN[1]: '2001:db8:dead::/48 ; SBL3000\n2001:db8:0:1::/64 ; SBL3001\n'}
schutz.holen = lambda url: DROP[url].encode()
sys.argv = [str(REPO / 'app.py'), *sys.argv[1:]]
runpy.run_path(str(REPO / 'app.py'), run_name='__main__')
