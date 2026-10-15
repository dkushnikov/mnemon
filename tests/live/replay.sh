#!/usr/bin/env bash
# tests/live/replay.sh — re-convert a capture-gate snapshot deterministically (spec D16, §7.17).
#
#   tests/live/replay.sh <snapshot dir>        # e.g. $MNEMON_FIXTURES_DIR/2026-10-15
#
# For every item directory of the snapshot it re-runs capture-text.py on the saved raw with the saved fetch
# report (a site: capture-site.py assemble on the saved bundle, nothing fetched) into a temp directory and
# diffs the outcome fields — converter, completeness, missing_count, glued_count, headings_missing_count,
# words (a site: completeness, converted, words and every page's converter/completeness) — against the saved
# report. Exit 1 on any difference. Never run by a suite; no network.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
SNAP="${1:?snapshot directory}"
TMP="$(mktemp -d -t mnemon-replay-XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
diffs=0
items=0

outcome_text() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))
print(json.dumps({k: d.get(k) for k in ("converter","completeness","missing_count","glued_count","headings_missing_count","words")}, sort_keys=True))' "$1"; }
outcome_site() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))
print(json.dumps({"completeness": d.get("completeness"), "converted": d.get("converted"), "words": d.get("words"),
  "pages": [(p["n"], p.get("disposition"), p.get("converter"), p.get("completeness"), p.get("missing_count"), p.get("glued_count")) for p in d["pages"]]}, sort_keys=True))' "$1"; }

for dir in "$SNAP"/*/; do
  dir="${dir%/}"
  [[ -d "$dir" ]] || continue
  slug="$(basename "$dir")"
  if [[ -f "$dir/raw.html" && -f "$dir/fetch.json" && -f "$dir/text.json" ]]; then
    items=$((items + 1))
    url="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["url"])' "$dir/text.json")"
    rawkind="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("raw_kind") or "response")' "$dir/fetch.json")"
    kind="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("kind") or "page")' "$dir/fetch.json")"
    [[ "$kind" == "youtube" || "$kind" == "github" ]] && kind=page
    mkdir -p "$TMP/$slug"
    if ! python3 "$ROOT/bin/capture-text.py" "$dir/raw.html" --url "$url" --out "$TMP/$slug/capture.md" --report "$TMP/$slug/text.json" --fetch-report "$dir/fetch.json" --raw-kind "$rawkind" --kind "$kind"; then
      echo "[DIFF] $slug: the reconversion failed"; diffs=$((diffs + 1)); continue
    fi
    a="$(outcome_text "$dir/text.json")"; b="$(outcome_text "$TMP/$slug/text.json")"
    if [[ "$a" == "$b" ]]; then echo "[same] $slug $b"; else echo "[DIFF] $slug"; echo "  saved:  $a"; echo "  replay: $b"; diffs=$((diffs + 1)); fi
  elif [[ -d "$dir/bundle" && -f "$dir/site.json" ]]; then
    items=$((items + 1))
    mkdir -p "$TMP/$slug"
    if ! python3 "$ROOT/bin/capture-site.py" assemble "$dir/bundle" --out "$TMP/$slug/capture.md" --report "$TMP/$slug/site.json"; then
      echo "[DIFF] $slug: the assembly failed"; diffs=$((diffs + 1)); continue
    fi
    a="$(outcome_site "$dir/site.json")"; b="$(outcome_site "$TMP/$slug/site.json")"
    if [[ "$a" == "$b" ]]; then echo "[same] $slug"; else echo "[DIFF] $slug"; echo "  saved:  $a"; echo "  replay: $b"; diffs=$((diffs + 1)); fi
  fi
done
echo "items: $items, differences: $diffs"
[[ $diffs -eq 0 ]]
