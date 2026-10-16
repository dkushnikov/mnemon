#!/usr/bin/env bash
# tests/live/capture-gate.sh — the manual, network-bound gate of the capture layer (spec D16, §7.17).
#
#   MNEMON_FIXTURES_DIR=/path/outside/the/repo tests/live/capture-gate.sh [corpus file] [--policy '<json>']
#
# Runs every item of tests/live/corpus.txt through the capture layer's CLIs — never through a suite — and
# snapshots the evidence outside the repository: <MNEMON_FIXTURES_DIR>/<date>/<slug>/{raw.html, fetch.json,
# capture.md, text.json} (a site: plan.json, bundle/, capture.md, site.json). Per item it asserts §7.17:
# the capture converts, completeness ∈ {complete, partial}, missing + glued ≤ 3 per page, the item's absent
# artifacts are absent from the capture-text, and the word floor holds. A markdown report per item and a
# summary (method, converter, acquisition, completeness, missing, glued, repairs, seconds — for a site item
# the landing's method, converter and acquisition) land next to the snapshot; the closing line counts
# ok / partial / failed items; tests/live/replay.sh re-converts the snapshot deterministically. Exit 1 when
# any item fails.
#
# A site item is mapped by the runner unless <snapshot>/<slug>/plan.json already exists: a landing that
# needs an --include plan is mapped by hand first (`capture-site.py map <url> --include '^/help/air/'
# --out <MNEMON_FIXTURES_DIR>/<date>/<slug>/plan.json`, the slug as slug_of below prints it) and the
# runner then uses that plan as it is, fetching the bundle from it and saying so.
#
# The gateway-level assertions of §7.17 (publication without manual repair, `--recapture` of a phase-1
# paper yielding `supersedes`, the `⤳` marker after `mnemon reindex`) are run by the integrator against the
# merged gateway; this script exercises the layer the gateway calls.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
CORPUS="${1:-$ROOT/tests/live/corpus.txt}"
POLICY=""
shift $(( $# > 0 ? 1 : 0 )) || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --policy) POLICY="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
: "${MNEMON_FIXTURES_DIR:?set MNEMON_FIXTURES_DIR to a snapshot directory outside the repository}"
DATE="$(date -u +%Y-%m-%d)"
OUT="$MNEMON_FIXTURES_DIR/$DATE"
mkdir -p "$OUT"
SUMMARY="$OUT/report.md"
printf '# Capture gate — %s\n\n| item | kind | method | converter | acquisition | completeness | missing | glued | repairs | words | seconds | verdict |\n|---|---|---|---|---|---|---|---|---|---|---|---|\n' "$DATE" > "$SUMMARY"
failed=0
n_ok=0
n_partial=0
n=0

slug_of() { printf '%s' "$1" | sed -e 's#^https\{0,1\}://##' -e 's#[^A-Za-z0-9._-]#_#g' | cut -c1-80; }
jget() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=d
for k in sys.argv[2].split("."):
    v = v.get(k) if isinstance(v, dict) else None
print("" if v is None else v)' "$1" "$2"; }
repairs_of() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); r=d.get("repairs") or {}
print(",".join("%s=%s" % (k, v) for sub in ("html","markdown") for k, v in (r.get(sub) or {}).items() if v))' "$1"; }

while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | sed 's/[[:space:]]*$//')"
  [[ -z "$line" ]] && continue
  IFS='|' read -r url kind minwords absent <<< "$line"
  url="$(printf '%s' "$url" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"; kind="$(printf '%s' "$kind" | tr -d ' ')"
  minwords="$(printf '%s' "$minwords" | tr -d ' ')"; absent="$(printf '%s' "$absent" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  if [[ "$url" == \<* ]]; then echo "skip placeholder: $url"; continue; fi
  n=$((n + 1))
  slug="$(slug_of "$url")"
  dir="$OUT/$slug"; mkdir -p "$dir"
  t0=$(date +%s)
  verdict="ok"; notes=""
  # every per-item field starts empty: a failed row never shows the previous row's values (G20)
  method=""; converter=""; acquisition=""; completeness=""; missing=""; glued=""; repairs=""; words=""; report=""
  if [[ "$kind" == "site" ]]; then
    if [[ -s "$dir/plan.json" ]]; then
      echo "using the pre-placed plan $dir/plan.json (not mapping)"; notes="$notes pre-placed plan;"
    else
      python3 "$ROOT/bin/capture-site.py" map "$url" --out "$dir/plan.json" ${POLICY:+--policy "$POLICY"} || { verdict="map-failed"; }
    fi
    if [[ "$verdict" == "ok" ]]; then
      rm -rf "$dir/bundle"
      python3 "$ROOT/bin/capture-site.py" fetch-bundle "$dir/plan.json" --out "$dir/bundle" --report "$dir/bundle.json" ${POLICY:+--policy "$POLICY"} > "$dir/bundle.stdout" || verdict="fetch-failed"
    fi
    if [[ "$verdict" == "ok" ]]; then
      python3 "$ROOT/bin/capture-site.py" assemble "$dir/bundle" --out "$dir/capture.md" --report "$dir/site.json" || verdict="assemble-failed"
    fi
    report="$dir/site.json"
    # the landing's own method and acquisition (its fetch report in the bundle) and converter (the site report)
    if [[ -f "$dir/bundle/01.fetch.json" ]]; then method="$(jget "$dir/bundle/01.fetch.json" method)"; acquisition="$(jget "$dir/bundle/01.fetch.json" acquisition.outcome)"; fi
    if [[ "$verdict" == "ok" ]]; then
      converter="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); p=[p for p in d["pages"] if p.get("role") == "landing"]; print((p[0].get("converter") if p else None) or "")' "$report")"
      completeness="$(jget "$report" completeness)"
      missing="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sum((p.get("missing_count") or 0) for p in d["pages"]))' "$report")"
      glued="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sum((p.get("glued_count") or 0) for p in d["pages"]))' "$report")"
      words="$(jget "$report" words)"; repairs="-"
      perpage="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(max([(p.get("missing_count") or 0)+(p.get("glued_count") or 0) for p in d["pages"]] or [0]))' "$report")"
      [[ "$perpage" -le 3 ]] || { verdict="lost-lines"; notes="$notes a page lost $perpage lines;"; }
    fi
  else
    raw="$dir/raw.html"; fr="$dir/fetch.json"; report="$dir/text.json"
    kindflag=""; [[ "$kind" == "paper" ]] && kindflag="--kind arxiv"
    rc=0; python3 "$ROOT/bin/capture-fetch.py" "$url" --out "$raw" --report "$fr" $kindflag ${POLICY:+--policy "$POLICY"} || rc=$?
    if [[ $rc -ne 0 ]]; then verdict="fetch-failed($(jget "$fr" error_class))"; fi
    rawkind="$(jget "$fr" raw_kind)"
    if [[ "$verdict" == "ok" && "$rawkind" == "file" ]]; then
      verdict="pdf-raw"; notes="$notes the paper has no HTML version (the gateway takes the pdf row);"
    fi
    if [[ "$verdict" == "ok" ]]; then
      python3 "$ROOT/bin/capture-text.py" "$raw" --url "$url" --out "$dir/capture.md" --report "$report" --fetch-report "$fr" --raw-kind "$rawkind" ${kindflag} || verdict="convert-failed"
    fi
    if [[ "$verdict" == "ok" ]]; then
      method="$(jget "$report" method)"; converter="$(jget "$report" converter)"; acquisition="$(jget "$report" acquisition)"
      completeness="$(jget "$report" completeness)"; missing="$(jget "$report" missing_count)"; glued="$(jget "$report" glued_count)"
      words="$(jget "$report" words)"; repairs="$(repairs_of "$report")"
      [[ $((missing + glued)) -le 3 ]] || { verdict="lost-lines"; notes="$notes missing+glued=$((missing + glued));"; }
    fi
  fi
  if [[ "$verdict" == "ok" ]]; then
    [[ "$completeness" == "complete" || "$completeness" == "partial" ]] || { verdict="completeness-$completeness"; }
    [[ -n "$minwords" && "$words" -ge "$minwords" ]] || { verdict="below-word-floor"; notes="$notes words=$words < $minwords;"; }
    IFS=',' read -r -a arts <<< "$absent"
    for a in "${arts[@]}"; do
      a="$(printf '%s' "$a" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"; [[ -z "$a" ]] && continue
      if grep -qF -- "$a" "$dir/capture.md" 2>/dev/null; then verdict="artifact-present"; notes="$notes '$a' present;"; fi
    done
  fi
  secs=$(( $(date +%s) - t0 ))
  if [[ "$verdict" == "ok" || "$verdict" == "pdf-raw" ]]; then
    if [[ "$completeness" == "partial" ]]; then n_partial=$((n_partial + 1)); else n_ok=$((n_ok + 1)); fi
  else
    failed=$((failed + 1))
  fi
  printf '| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |\n' "$slug" "$kind" "${method:--}" "${converter:--}" "${acquisition:--}" "${completeness:--}" "${missing:--}" "${glued:--}" "${repairs:--}" "${words:--}" "$secs" "$verdict$notes" >> "$SUMMARY"
  printf '# %s\n\n- url: %s\n- kind: %s\n- verdict: %s %s\n- seconds: %s\n- report: %s\n' "$slug" "$url" "$kind" "$verdict" "$notes" "$secs" "$report" > "$dir/report.md"
  echo "[$verdict] $url ($secs s)"
done < "$CORPUS"

echo "items: $n, ok: $n_ok, partial: $n_partial, failed: $failed — summary: $SUMMARY"
[[ $failed -eq 0 ]]
