#!/usr/bin/env bash
# test_capture_layer.sh — the capture layer (docs/specs/2026-10-15-capture-layer.md §3, §7 items 1, 3–11,
# 15 (audit), 16): `python3 -m unittest` over tests/capture_layer/test_*.py. Hermetic: fixture pages, a local
# HTTP server under the §3.2 test hooks, stub commands; `pandoc` is used when present (tests that need it
# print a note and skip otherwise). Prints the toolchain and asserts it under the legacy PATH run (A12).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

# §7.16: the suite clears inherited keys, proxies and test variables before it starts
unset FIRECRAWL_API_KEY JINA_API_KEY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
for v in $(env | sed -n 's/^\(MNEMON_[A-Z0-9_]*\)=.*/\1/p'); do unset "$v"; done
unset RENDER_URL_CMD

echo "=== Capture Layer Tests ==="
echo "python: $(python3 --version 2>&1) ($(command -v python3))"
echo "bash:   ${BASH_VERSION}"
case "$PATH" in
  /usr/bin:/bin:*)
    pyv="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    [[ "$pyv" == "3.9" ]] || { echo "FAIL: legacy PATH run expects python 3.9.x, got $pyv"; exit 1; }
    [[ "$BASH_VERSION" == 3.2.* ]] || { echo "FAIL: legacy PATH run expects bash 3.2.x, got $BASH_VERSION"; exit 1; }
    echo "toolchain: legacy (python 3.9, bash 3.2) asserted" ;;
esac

# §7.16: every module of bin/capture parses under `ast` and imports on the running python
python3 - "$ROOT" <<'PY'
import ast, importlib, os, sys
root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "bin"))
names = sorted(f[:-3] for f in os.listdir(os.path.join(root, "bin", "capture")) if f.endswith(".py") and f != "__init__.py")
for name in names:
    path = os.path.join(root, "bin", "capture", name + ".py")
    ast.parse(open(path, encoding="utf-8").read(), path)
    importlib.import_module("capture." + name)
print("  ok: bin/capture/{%s} parse and import" % ",".join(names))
PY

python3 -m unittest discover -s "$ROOT/tests/capture_layer" -t "$ROOT/tests/capture_layer" -p 'test_*.py' 2>&1 | tail -n 60
rc=${PIPESTATUS[0]}
if [[ $rc -eq 0 ]]; then echo "PASS test_capture_layer.sh"; else echo "FAIL test_capture_layer.sh"; exit 1; fi
