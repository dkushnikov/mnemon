#!/usr/bin/env bash
# backfill-archives.sh — re-archive "Gap-A" sources: url-origin sources captured
# on/after the archival feature shipped but missing an archive: field (their
# original was never preserved because the old naive curl came back empty).
# Re-fetches via the gateway's curl->render->unarchivable chain and patches the
# archive: field into source.md. Dry-run by default; --apply to mutate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/mnemon-config.sh"
source "$SCRIPT_DIR/lib-archive.sh"

CUTOFF="2026-04-23"   # archival feature ship date
APPLY=false
LIMIT=0
CONFIG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   APPLY=true; shift ;;
    --limit)   LIMIT="$2"; shift 2 ;;
    --config)  CONFIG="$2"; shift 2 ;;
    --cutoff)  CUTOFF="$2"; shift 2 ;;
    -h|--help) echo "Usage: backfill-archives.sh [--apply] [--limit N] [--config path] [--cutoff YYYY-MM-DD]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

load_config "$CONFIG"
[[ -z "${ARCHIVE_DIR:-}" ]] && { echo "ERROR: archive_dir not set in config — nothing to backfill into." >&2; exit 1; }

_fm_get() {  # file key -> frontmatter value (quotes stripped)
  local v
  v=$(awk -v k="$2" 'NR==1&&$0=="---"{f=1;next} f&&$0=="---"{exit} f{p=k": "; if(substr($0,1,length(p))==p){print substr($0,length(p)+1);exit}}' "$1")
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}

_fm_add_archive() {  # file value -> insert archive: "value" before closing frontmatter ---
  local file="$1" value="$2" tmp
  tmp=$(mktemp)
  awk -v val="$value" 'NR==1&&$0=="---"{print;f=1;next} f&&$0=="---"&&!d{print "archive: \"" val "\"";print;d=1;f=0;next} {print}' "$file" > "$tmp"
  [[ -s "$tmp" ]] && cat "$tmp" > "$file"   # guard: never overwrite with empty output
  rm -f "$tmp"
}

echo "Vault:       $VAULT_PATH"
echo "Archive dir: $ARCHIVE_DIR"
mode="DRY-RUN"; [[ "$APPLY" == true ]] && mode="APPLY"
[[ "$LIMIT" -gt 0 ]] && mode="$mode (limit $LIMIT)"
echo "Mode:        $mode"
echo ""

total=0; processed=0; archived=0; unarch=0
while IFS= read -r sm; do
  [[ "$(_fm_get "$sm" origin)" == "url" ]] || continue
  captured=$(_fm_get "$sm" captured)
  [[ "$captured" > "$CUTOFF" || "$captured" == "$CUTOFF" ]] || continue
  [[ -z "$(_fm_get "$sm" archive)" ]] || continue
  total=$((total+1))
  [[ "$LIMIT" -gt 0 && "$processed" -ge "$LIMIT" ]] && continue
  processed=$((processed+1))
  url=$(_fm_get "$sm" url)
  dir=$(basename "$(dirname "$sm")")
  if [[ "$APPLY" != true ]]; then
    echo "  [dry]    $dir  $url"
    continue
  fi
  name=$(_l1_archive_url "$url")
  if [[ -z "$name" ]]; then
    echo "  [skip]   $dir  (archival returned empty)"
    continue
  fi
  _fm_add_archive "$sm" "$name"
  if [[ "$name" == "unarchivable" ]]; then
    echo "  [unarch] $dir  $url"
    unarch=$((unarch+1))
  else
    echo "  [ok]     $dir  -> $name"
    archived=$((archived+1))
  fi
done < <(find "$VAULT_PATH/Sources" -maxdepth 2 -name source.md | sort)

echo ""
if [[ "$APPLY" == true ]]; then
  echo "Done: $archived archived, $unarch unarchivable ($processed processed of $total targets)."
else
  echo "Dry-run: $total targets. Re-run with --apply (optionally --limit N) to write."
fi
