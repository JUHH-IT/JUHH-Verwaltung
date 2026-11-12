"""Schrift für alle PDF-Dateien: Liberation Sans (SIL OFL 1.1, Laufweite wie Helvetica/Arial).

Die eingebaute PDF-Schrift Helvetica kennt nur westeuropäische Zeichen; Namen wie Şahin, Yıldız oder Łukasz
erschienen als Kästchen. Liberation Sans deckt Mittel- und Osteuropa sowie Türkisch ab und wird eingebettet.
Lizenz: schriften/LICENSE-Liberation.txt.
"""
import functools
import threading
from pathlib import Path

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FOLDER = Path(__file__).resolve().parent / 'schriften'
SANS, SANS_BOLD = 'JUSans', 'JUSans-Bold'

pdfmetrics.registerFont(TTFont(SANS, str(FOLDER / 'LiberationSans-Regular.ttf')))
pdfmetrics.registerFont(TTFont(SANS_BOLD, str(FOLDER / 'LiberationSans-Bold.ttf')))
for bold, italic, name in ((0, 0, SANS), (1, 0, SANS_BOLD), (0, 1, SANS), (1, 1, SANS_BOLD)):
    addMapping(SANS, bold, italic, name)  # <b> in Absätzen nutzt die fette Schnittart


_lock = threading.RLock()


def exclusive(fn):
    """PDF-Erzeugung nacheinander: reportlab bildet Teilschriften über eine gemeinsame Leseposition der TTF-Datei,
    gleichzeitige Aufrufe aus Versand- und Server-Threads könnten sich sonst stören."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapper


def covers(text, font=SANS):
    """True, wenn die Schrift alle Zeichen des Textes darstellen kann (Leerraum ausgenommen)."""
    glyphs = pdfmetrics.getFont(font).face.charToGlyph
    return all(ord(c) in glyphs for c in str(text) if not c.isspace())
