"""Erzeugt INHALT.md: Inhaltsverzeichnis des Programms nach Teilbereichen – Module, Tabellen, Funktionen, Wege der
Oberfläche (mit nötigem Recht), Seiten und Tests. Damit findet man ohne Suchen, wo etwas liegt und was es schon gibt.

    python3 werkzeuge/inhalt.py            INHALT.md neu schreiben (nach jeder Änderung an Funktionen oder Wegen)
    python3 werkzeuge/inhalt.py --pruefen  nur vergleichen (Rückgabe 1, wenn INHALT.md veraltet ist) – das macht test_inhalt.py

Bewusst ohne Zeilennummern: sie änderten sich bei jeder Kleinigkeit und führten zu Konflikten zwischen Zweigen.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Teilbereich → Module (Reihenfolge = Reihenfolge im Verzeichnis). Ein neues Modul hier eintragen – sonst steht es unter „Nicht zugeordnet“.
BEREICHE = (
    ('Kern', 'app core formate stammdaten kontenrahmen regelwerk'),
    ('Mitgliederverwaltung', 'mitglieder mitgliederlisten doppelte gliederung ovabgleich kenntnis klaerfaelle wahlrecht karte adressen auswertung'),
    ('Mitgliedsbeiträge', 'beitraege mitgliederkonto opliste sepadatei lastschrift'),
    ('Buchhaltung', 'bankimport uebernahme workflow kasse verbindlichkeiten erstattung erechnung freigabe berichte'),
    ('Rechnungen und Versand', 'rechnung_pdf pdfschrift versand geheimnis'),
    ('Verband', 'haushalt kvbudget kvabgleich rpj'),
    ('Serverbetrieb und Sicherheit', 'zugang rollen sicherheit schutz keycloak verwaltung'),
    ('Sicherung und Pflege', 'sicherung bereinigung'),
)


def _erste_zeile(node):
    text = ast.get_docstring(node) or ''
    return text.strip().split('\n')[0].strip()


def _signatur(fn):
    args = [a.arg for a in fn.args.posonlyargs + fn.args.args if a.arg != 'self']
    n_default = len(fn.args.defaults)
    for i in range(n_default):
        args[len(args) - n_default + i] += '=…'
    if fn.args.vararg:
        args.append('*' + fn.args.vararg.arg)
    args += [a.arg + ('=…' if d is not None else '') for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults)]
    if fn.args.kwarg:
        args.append('**' + fn.args.kwarg.arg)
    return f"{fn.name}({', '.join(args)})"


def _funktion(fn, prefix=''):
    doc = _erste_zeile(fn)
    return f"- `{prefix}{_signatur(fn)}`" + (f" – {doc}" if doc else '')


def modul(name):
    source = (ROOT / f'{name}.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    lines = [f'### {name}.py', '']
    doc = _erste_zeile(tree)
    if doc:
        lines += [doc, '']
    tables = sorted(set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', source)))
    if tables:
        lines += ['Tabellen: ' + ', '.join(f'`{t}`' for t in tables), '']
    tests = sorted(p.name for p in ROOT.glob('test_*.py') if re.search(rf'^\s*(import|from) .*\b{name}\b', p.read_text(encoding='utf-8'), re.M))
    if tests:
        lines += ['Tests: ' + ', '.join(tests), '']
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith('_'):
            lines.append(_funktion(node))
        elif isinstance(node, ast.ClassDef):
            lines.append(f"- Klasse `{node.name}`" + (f" – {_erste_zeile(node)}" if _erste_zeile(node) else ''))
            lines += ['  ' + _funktion(f, node.name + '.') for f in node.body
                      if isinstance(f, ast.FunctionDef) and not f.name.startswith('_')]
    return lines + ['']


def wege():
    """Alle Wege der Oberfläche mit dem nötigen Recht (aus rollen.GET/POST) – wer einen Weg sucht, findet ihn in app.py über den Pfad."""
    import app
    import rollen
    label = dict((a, l) for a, l, _ in rollen.AREAS)

    def recht(need):
        if need == rollen.FREE:
            return 'angemeldet'
        return ' oder '.join(f"{label.get(a, a)} {'ändern' if lvl == 'schreiben' else 'lesen'}" for a, lvl in need)

    lines = ['## Wege der Oberfläche (`/api/…`)', '',
             'Recht aus `rollen.GET`/`rollen.POST`; ⚠ = Passwortbestätigung (`app.CONFIRM`). Behandelt in `app.py` (`do_GET`/`do_POST`).', '']
    confirm = set(app.CONFIRM)
    groups = {}
    for method, table in (('GET', rollen.GET), ('POST', rollen.POST)):
        for path, need in table.items():
            groups.setdefault(recht(need), []).append(f"`{method} {path}`" + (' ⚠' if (method, path) in confirm else ''))
    for need in sorted(groups):
        lines += [f'**{need}:** ' + ', '.join(sorted(groups[need])), '']
    lines += ['**ohne Anmeldung** (`rollen.OUTSIDE`): ' + ', '.join(f'`{p}`' for p in sorted(rollen.OUTSIDE)), '']
    return lines


def oberflaeche():
    lines = ['## Oberfläche (`static/js`, ES-Module ohne Build-Schritt)', '']
    for path in sorted((ROOT / 'static' / 'js').rglob('*.js')):
        if 'vendor' in path.parts or path.name.endswith('.min.js'):
            continue
        source = path.read_text(encoding='utf-8')
        exports = sorted(set(re.findall(r'^export (?:async )?(?:function|const|let|class) (\w+)', source, re.M)))
        api = sorted(set(re.findall(r"""['"`](/api/[\w.\-]+)""", source)))
        rel = path.relative_to(ROOT).as_posix()
        lines.append(f"- `{rel}`" + (': ' + ', '.join(exports) if exports else '') + (' → ' + ', '.join(api) if api else ''))
    return lines + ['']


def inhalt():
    zugeordnet = [m for _, mods in BEREICHE for m in mods.split()]
    alle = sorted(p.stem for p in ROOT.glob('*.py') if not p.stem.startswith('test_'))
    rest = [m for m in alle if m not in zugeordnet]
    lines = ['# Inhaltsverzeichnis des Programms', '',
             'Automatisch erzeugt von `werkzeuge/inhalt.py` – nicht von Hand ändern. Nach Änderungen an Funktionen, Wegen oder',
             'Seiten: `python3 werkzeuge/inhalt.py`. Regeln und Zweck der Module stehen in `AGENTS.md`.', '',
             '**Bereiche:** ' + ' · '.join(name for name, _ in BEREICHE) + (' · Nicht zugeordnet' if rest else ''), '']
    lines += wege() + oberflaeche()
    for name, mods in BEREICHE + ((('Nicht zugeordnet', ' '.join(rest)),) if rest else ()):
        vorhanden = [m for m in mods.split() if (ROOT / f'{m}.py').exists()]
        if vorhanden:
            lines += [f'## {name}', '']
            for m in vorhanden:
                lines += modul(m)
    return '\n'.join(lines).rstrip() + '\n'


if __name__ == '__main__':
    ziel = ROOT / 'INHALT.md'
    neu = inhalt()
    if '--pruefen' in sys.argv:
        sys.exit(0 if ziel.exists() and ziel.read_text(encoding='utf-8') == neu else 1)
    ziel.write_text(neu, encoding='utf-8')
    print(f'INHALT.md geschrieben ({len(neu.splitlines())} Zeilen).')
