"""Schneller Testlauf: nur die Tests, die von den geänderten Dateien abhängen – parallel je Testdatei.

    python3 werkzeuge/tests.py                 betroffene Tests gegenüber origin/main (auch ungespeicherte Änderungen)
    python3 werkzeuge/tests.py --basis REF     gegenüber einem anderen Stand
    python3 werkzeuge/tests.py --tief          auch Tests, die das Geänderte nur über andere Module laden (meist fast alle)
    python3 werkzeuge/tests.py --alle          alle Tests parallel
    python3 werkzeuge/tests.py --zeigen        nur anzeigen, was laufen würde

Betroffen = Testdateien, die ein geändertes Modul selbst importieren (mit --tief auch über mehrere Stufen – weil core
und app fast alles laden, sind das dann meist alle). Die volle Suite läuft in der CI bei jedem Pull Request – lokal
reicht während der Arbeit dieser Lauf. Scheitert eine Datei, läuft sie einmal allein nach
(zeitabhängige Tests kippen unter Last); besteht sie dann, gilt sie als „wackelig“ und der Lauf bleibt grün.
"""
import ast
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def importe():
    """Modul → direkt importierte Module des Programms (auch Importe innerhalb von Funktionen)."""
    module = {p.stem for p in ROOT.glob('*.py')}
    graph = {}
    for name in module:
        found = set()
        for node in ast.walk(ast.parse((ROOT / f'{name}.py').read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                found |= {a.name.split('.')[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                found.add(node.module.split('.')[0])
        graph[name] = found & module
    return graph


def betroffen(geaendert, graph=None, tief=False):
    """Testdateien, die von den geänderten Dateien abhängen, und Hinweise auf Prüfungen außerhalb von unittest."""
    graph = graph or importe()
    stems = {Path(f).stem for f in geaendert if f.endswith('.py') and '/' not in f}
    tests, hinweise = set(), []
    for test in sorted(m for m in graph if m.startswith('test_')):
        seen, todo = {test} | set(graph[test]), list(graph[test]) if tief else []
        while todo:                      # --tief: auch Module, die dieser Test nur über andere lädt
            m = todo.pop()
            for n in graph.get(m, ()):
                if n not in seen:
                    seen.add(n)
                    todo.append(n)
        if seen & stems:
            tests.add(test)
    if any(f.startswith(('static/', 'tests/')) for f in geaendert):
        hinweise.append('Oberfläche geändert: node tests/html_escape.mjs · NODE_PATH=$(npm root -g) node tests/ui_smoke.mjs <Ordner> · … tests/ui_server.mjs <Ordner>')
    if any(f.startswith(('server/', 'tests/haertung_')) for f in geaendert):
        tests |= {'test_server', 'test_repo', 'test_haertung'} & set(graph)
        hinweise.append('Server-Skripte geändert: shellcheck server/*.sh server/ju-finanzen tests/haertung_stub.sh')
    if geaendert:
        tests |= {'test_repo', 'test_inhalt'} & set(graph)   # Wächter und Inhaltsverzeichnis gelten für jede Änderung
    return sorted(tests), hinweise


def geaenderte_dateien(basis):
    def git(*args):
        return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    try:
        files = set(git('diff', '--name-only', f'{basis}...HEAD'))
    except subprocess.CalledProcessError:
        sys.exit(f'Stand „{basis}“ gibt es hier nicht – bitte --basis angeben (oder --alle).')
    files |= set(git('diff', '--name-only', 'HEAD'))
    return sorted(files | set(git('ls-files', '--others', '--exclude-standard')))


def lauf(test):
    r = subprocess.run([sys.executable, '-m', 'unittest', '-q', test], cwd=ROOT, capture_output=True, text=True)
    return test, r.returncode == 0, r.stdout + r.stderr


def main(argv):
    graph = importe()
    if '--alle' in argv:
        tests, hinweise = sorted(m for m in graph if m.startswith('test_')), []
    else:
        basis = argv[argv.index('--basis') + 1] if '--basis' in argv else 'origin/main'
        tests, hinweise = betroffen(geaenderte_dateien(basis), graph, '--tief' in argv)
    print(f"{len(tests)} von {sum(m.startswith('test_') for m in graph)} Testdateien: {' '.join(tests) or '–'}")
    for h in hinweise:
        print('Hinweis – ' + h)
    if '--zeigen' in argv or not tests:
        return 0
    rot = []
    with ThreadPoolExecutor(max_workers=int(os.environ.get('JU_JOBS') or os.cpu_count() or 2)) as pool:
        for test, ok, out in pool.map(lauf, tests):
            if not ok:
                test, ok, out2 = lauf(test)           # einmal allein wiederholen
                print(f'wackelig: {test} (unter Last gescheitert, allein bestanden)' if ok else f'ROT: {test}\n{out2}')
                if not ok:
                    rot.append(test)
    print('Alle bestanden.' if not rot else f"Gescheitert: {' '.join(rot)}")
    return 1 if rot else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
