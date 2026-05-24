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

_detect_ext() {  # downloaded file -> archive extension, from content type
  case "$(file -b --mime-type "$1" 2>/dev/null)" in
    application/pdf) echo pdf ;;
    image/png)       echo png ;;
    image/jpeg)      echo jpg ;;
    image/gif)       echo gif ;;
    *)               echo html ;;   # default for web fetches (html/text)
  esac
}

_l1_archive_url() {
  # Archive a URL's content to L1. Downloads to a file (binary-safe — $(...) strips
  # null bytes and would corrupt PDFs/images) with gzip decoded; extension inferred
  # from content. Falls back to a rendered text capture, else marks unarchivable.
  # Echoes the archive filename, or "unarchivable" if both fetches come back empty.
  local url="$1"
  local dir tmp name ext raw
  dir=$(_l1_archive_dir)
  [[ -z "$dir" ]] && return 0   # archival disabled — stay silent, like other origins
  mkdir -p "$dir"
  tmp=$(mktemp)
  if curl -fsSL --compressed -A "Mozilla/5.0" --max-time 30 -o "$tmp" "$url" 2>/dev/null && [[ -s "$tmp" ]]; then
    ext=$(_detect_ext "$tmp")
    name=$(_l1_archive_name "$url" "$ext")
    mv "$tmp" "$dir/$name"
    echo "$name"
    return 0
  fi
  rm -f "$tmp"
  raw=$("$RENDER_URL_CMD" "$url" 2>/dev/null) || raw=""
  if [[ -n "$raw" ]]; then
    _l1_archive_text "$raw" "$url" "txt"
    return 0
  fi
  echo "unarchivable"
}
