#!/usr/bin/env bash
# L1 archive helpers — shared by knowledge-gateway.sh and backfill-archives.sh.
# Source this file (it only defines functions + RENDER_URL_CMD, runs nothing).
# Requires ARCHIVE_DIR in the environment (set by mnemon-config.sh load_config).

_LIB_ARCHIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Renderer command — overridable so tests can stub Chrome.
RENDER_URL_CMD="${RENDER_URL_CMD:-$_LIB_ARCHIVE_DIR/render-url.sh}"

_l1_archive_dir() {
  echo "${ARCHIVE_DIR:-}"
}

_l1_archive_name() {
  local canonical="$1" ext="$2"
  local hash8
  hash8=$(printf '%s' "$canonical" | shasum -a 256 | cut -c1-8)
  echo "$(date +%Y-%m-%d)_${hash8}.${ext}"
}

_l1_archive_text() {
  local content="$1" canonical="$2" ext="${3:-txt}"
  local dir name
  dir=$(_l1_archive_dir)
  [[ -z "$dir" ]] && return 0
  name=$(_l1_archive_name "$canonical" "$ext")
  mkdir -p "$dir"
  printf '%s' "$content" > "${dir}/${name}"
  echo "${name}"
}

_l1_archive_file() {
  local source_file="$1" canonical="$2"
  local ext="${source_file##*.}"
  local dir name
  dir=$(_l1_archive_dir)
  [[ -z "$dir" ]] && return 0
  name=$(_l1_archive_name "$canonical" "$ext")
  mkdir -p "$dir"
  cp "$source_file" "${dir}/${name}"
  echo "${name}"
}

_l1_archive_url() {
  # Archive a URL's content to L1, trying curl then a render fallback.
  # Echoes the archive filename on success, or "unarchivable" if both fetches
  # come back empty (the extractor still runs via WebFetch — this just records
  # that the original could not be preserved, instead of failing silently).
  local url="$1"
  local dir raw
  dir=$(_l1_archive_dir)
  [[ -z "$dir" ]] && return 0   # archival disabled — stay silent, like other origins
  raw=$(curl -sfL -A "Mozilla/5.0" --max-time 30 "$url" 2>/dev/null) || raw=""
  if [[ -n "$raw" ]]; then
    _l1_archive_text "$raw" "$url" "html"
    return 0
  fi
  raw=$("$RENDER_URL_CMD" "$url" 2>/dev/null) || raw=""
  if [[ -n "$raw" ]]; then
    _l1_archive_text "$raw" "$url" "txt"
    return 0
  fi
  echo "unarchivable"
}
