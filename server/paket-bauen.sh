#!/usr/bin/env bash
# Serverpaket bauen: ju-finanzen-<Version>-server.tar.gz aus dem committeten Git-Stand,
# dazu die Python-Pakete für Ubuntu 24.04 (Python 3.12, x86_64 und aarch64) – geprüft gegen die Prüfsummen
# in server/requirements-server.txt. So braucht die Installation auf dem Server kein PyPI.
#   ./server/paket-bauen.sh [Zielordner]          (ohne Python-Pakete: OHNE_WHEELS=1 ./server/paket-bauen.sh)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
VERSION="$(sed -n "s/^VERSION='\(.*\)'.*/\1/p" app.py | head -n 1)"
[ -n "$VERSION" ] || { echo "Version nicht gefunden." >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "Bitte zuerst alle Änderungen committen (das Paket enthält nur den Git-Stand)." >&2; exit 1; }
DEST="$(cd "${1:-.}" && pwd)"
NAME="ju-finanzen-$VERSION"
OUT="$DEST/$NAME-server.tar.gz"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git archive --format=tar --prefix="$NAME/" HEAD | tar -C "$tmp" -xf -
rm -rf "$tmp/$NAME/mac" "$tmp/$NAME/Finanzen starten.cmd" "$tmp/$NAME/.github"
if [ "${OHNE_WHEELS:-}" != "1" ]; then
  mkdir -p "$tmp/$NAME/server/wheels"
  for arch in x86_64 aarch64; do   # Ubuntu 24.04 (glibc 2.39) nimmt manylinux 2014/2_17 bis 2_28
    python3 -m pip download --quiet --disable-pip-version-check --no-deps --only-binary=:all: \
      --platform "manylinux_2_28_$arch" --platform "manylinux_2_17_$arch" --platform "manylinux2014_$arch" \
      --python-version 3.12 --implementation cp --abi cp312 \
      --require-hashes -r server/requirements-server.txt -d "$tmp/$NAME/server/wheels"
  done
fi
printf '%s\n' "$(git rev-parse HEAD)" > "$tmp/$NAME/server/GIT-STAND"
if tar --version 2>/dev/null | grep -q GNU; then owner=(--owner=0 --group=0); else owner=(--uid 0 --gid 0); fi   # macOS: bsdtar
tar -C "$tmp" "${owner[@]}" -czf "$OUT" "$NAME"
echo "Paket: $OUT"
# Prüfsumme daneben (auf dem Server: sha256sum -c ju-finanzen-<Version>-server.tar.gz.sha256)
if command -v sha256sum >/dev/null; then (cd "$DEST" && sha256sum "$NAME-server.tar.gz" | tee "$NAME-server.tar.gz.sha256")
else (cd "$DEST" && shasum -a 256 "$NAME-server.tar.gz" | tee "$NAME-server.tar.gz.sha256"); fi
