#!/usr/bin/env python3
"""
Safari Reading List + Bookmarks + Open Tabs + Cloud Tabs → Obsidian Dump vault

Parses Safari data from multiple sources and exports each item as a
markdown file with frontmatter, ready for triage in Obsidian before
batch import into Mnemon Knowledge Store.

Sources:
  - Reading List    — ~/Library/Safari/Bookmarks.plist
  - Bookmarks       — ~/Library/Safari/Bookmarks.plist
  - Open Tabs       — ~/Library/Containers/com.apple.Safari/Data/Library/Safari/SafariTabs.db
  - Cloud Tabs      — ~/Library/Containers/com.apple.Safari/Data/Library/Safari/CloudTabs.db

Usage:
    safari.py --out ~/Obsidian/Dump/Safari                    # all sources
    safari.py --out ~/Obsidian/Dump/Safari --reading-list-only
    safari.py --out ~/Obsidian/Dump/Safari --tabs-only
"""

import argparse
import hashlib
import plistlib
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


SAFARI_PLIST = Path.home() / "Library/Safari/Bookmarks.plist"
SAFARI_TABS_DB = Path.home() / "Library/Containers/com.apple.Safari/Data/Library/Safari/SafariTabs.db"
CLOUD_TABS_DB = Path.home() / "Library/Containers/com.apple.Safari/Data/Library/Safari/CloudTabs.db"


def slugify(text: str, max_len: int = 80) -> str:
    """Filesystem-safe slug from title."""
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[-\s]+", "-", text)
    return text[:max_len] or "untitled"


def frontmatter_escape(value: str) -> str:
    """Escape a string for YAML frontmatter (double-quoted)."""
    if value is None:
        return ""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def iter_items(node, path=()):
    """Walk the plist tree, yielding (item, path) for leaf bookmarks."""
    if isinstance(node, dict):
        if node.get("WebBookmarkType") == "WebBookmarkTypeLeaf":
            yield node, path
            return
        title = node.get("Title", "")
        new_path = path + (title,) if title else path
        for child in node.get("Children", []):
            yield from iter_items(child, new_path)


def is_reading_list(path):
    """Reading List items live under the 'com.apple.ReadingList' folder."""
    return any("com.apple.ReadingList" in (p or "") or p == "Reading List" for p in path)


def extract_item(item, path):
    url = item.get("URLString", "")
    uri = item.get("URIDictionary", {}) or {}
    title = uri.get("title") or url
    uuid = item.get("WebBookmarkUUID", "")

    reading_list_meta = item.get("ReadingList", {}) or {}
    preview = reading_list_meta.get("PreviewText", "")
    date_added = reading_list_meta.get("DateAdded") or item.get("DateAdded")

    return {
        "url": url,
        "title": title,
        "uuid": uuid,
        "preview": preview,
        "date_added": date_added,
        "folder": " / ".join(p for p in path if p),
    }


def write_markdown(item: dict, out_dir: Path, kind: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Stable filename from URL hash + slug — dedupe-friendly
    url_hash = hashlib.sha256(item["url"].encode()).hexdigest()[:8]
    slug = slugify(item["title"])
    fname = f"{url_hash}-{slug}.md"
    fpath = out_dir / fname

    # Skip if already exists (idempotent re-runs)
    if fpath.exists():
        return fpath

    date_str = ""
    if item["date_added"]:
        try:
            date_str = item["date_added"].strftime("%Y-%m-%d")
        except Exception:
            date_str = str(item["date_added"])[:10]

    # Triage fields: status=unclassified, value=unrated — to be filled by categorizer
    fm_lines = [
        "---",
        "type: dump-item",
        f"source: safari-{kind}",
        f'url: "{frontmatter_escape(item["url"])}"',
        f'title: "{frontmatter_escape(item["title"])}"',
        f'uuid: "{item["uuid"]}"',
        f'date_added: "{date_str}"',
        f'folder: "{frontmatter_escape(item["folder"])}"',
        "status: unclassified",  # unclassified | keep | skip | maybe
        "category: ",  # filled by categorizer
        "domain: ",  # filled by categorizer
        "value: ",  # 1-5 scored by categorizer
        "reason: ",  # categorizer rationale
        "---",
        "",
        f"# {item['title']}",
        "",
        f"**URL:** {item['url']}",
        "",
    ]
    if item["preview"]:
        fm_lines.extend(["## Preview", "", item["preview"], ""])
    if item["folder"]:
        fm_lines.extend([f"**Folder:** {item['folder']}", ""])

    fpath.write_text("\n".join(fm_lines), encoding="utf-8")
    return fpath


def copy_db_readonly(src: Path) -> Optional[Path]:
    """Copy a live SQLite DB to a temp location to avoid locks/WAL issues."""
    import shutil
    import tempfile
    if not src.exists():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="safari-import-")) / src.name
    shutil.copy2(src, tmp)
    # Also copy WAL/SHM if present, to get latest data
    for suffix in ("-wal", "-shm"):
        side = src.with_name(src.name + suffix)
        if side.exists():
            shutil.copy2(side, tmp.with_name(tmp.name + suffix))
    return tmp


def extract_open_tabs(out_dir: Path, limit: int = 0) -> int:
    """Extract open tabs from SafariTabs.db → one markdown file per tab."""
    db = copy_db_readonly(SAFARI_TABS_DB)
    if not db:
        print("  (SafariTabs.db not found — skipping open tabs)", file=sys.stderr)
        return 0

    count = 0
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cursor = conn.execute(
            "SELECT title, url, last_modified FROM bookmarks "
            "WHERE url LIKE 'http%' AND deleted=0 AND date_closed IS NULL "
            "ORDER BY order_index"
        )
        for title, url, last_mod in cursor:
            if limit and count >= limit:
                break
            date_str = ""
            if last_mod:
                try:
                    # Safari uses Mac epoch (seconds since 2001-01-01)
                    ts = float(last_mod) + 978307200
                    date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                except Exception:
                    pass
            item = {
                "url": url,
                "title": title or url,
                "uuid": "",
                "preview": "",
                "date_added": date_str,
                "folder": "open-tabs",
            }
            write_markdown(item, out_dir, "open-tabs")
            count += 1
        conn.close()
    finally:
        # Cleanup temp db
        import shutil
        shutil.rmtree(db.parent, ignore_errors=True)
    return count


def extract_cloud_tabs(out_dir: Path, limit: int = 0) -> int:
    """Extract iCloud Tabs from CloudTabs.db → one markdown file per tab."""
    db = copy_db_readonly(CLOUD_TABS_DB)
    if not db:
        print("  (CloudTabs.db not found — skipping cloud tabs)", file=sys.stderr)
        return 0

    count = 0
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        # Join device info for provenance
        cursor = conn.execute(
            "SELECT ct.title, ct.url, ct.last_viewed_time, cd.device_name "
            "FROM cloud_tabs ct "
            "LEFT JOIN cloud_tab_devices cd ON ct.device_uuid = cd.device_uuid"
        )
        for row in cursor:
            if limit and count >= limit:
                break
            title, url, last_viewed, device_name = row
            if not url or not url.startswith("http"):
                continue
            date_str = ""
            if last_viewed:
                try:
                    ts = float(last_viewed) + 978307200
                    date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                except Exception:
                    pass
            item = {
                "url": url,
                "title": title or url,
                "uuid": "",
                "preview": "",
                "date_added": date_str,
                "folder": f"cloud-tabs / {device_name or 'unknown-device'}",
            }
            write_markdown(item, out_dir, "cloud-tabs")
            count += 1
        conn.close()
    finally:
        import shutil
        shutil.rmtree(db.parent, ignore_errors=True)
    return count


def main():
    parser = argparse.ArgumentParser(description="Export Safari data (reading list, bookmarks, open tabs, cloud tabs) to markdown")
    parser.add_argument("--out", required=True, help="Output directory (e.g. ~/Obsidian/Dump/Safari)")
    parser.add_argument("--reading-list-only", action="store_true")
    parser.add_argument("--bookmarks-only", action="store_true")
    parser.add_argument("--tabs-only", action="store_true", help="Only open tabs + cloud tabs")
    parser.add_argument("--skip-plist", action="store_true", help="Skip reading list + bookmarks")
    parser.add_argument("--skip-tabs", action="store_true", help="Skip open tabs + cloud tabs")
    parser.add_argument("--plist", default=str(SAFARI_PLIST), help="Path to Safari Bookmarks.plist")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of items per category (for testing)")
    args = parser.parse_args()

    out_root = Path(args.out).expanduser()

    do_plist = not args.skip_plist and not args.tabs_only
    do_tabs = not args.skip_tabs and not (args.reading_list_only or args.bookmarks_only)

    rl_count = 0
    bm_count = 0
    ot_count = 0
    ct_count = 0
    skipped = 0

    if do_plist:
        plist_path = Path(args.plist).expanduser()
        if not plist_path.exists():
            print(f"WARN: plist not found: {plist_path}", file=sys.stderr)
        else:
            rl_dir = out_root / "Reading List"
            bm_dir = out_root / "Bookmarks"
            with open(plist_path, "rb") as f:
                data = plistlib.load(f)
            for item, path in iter_items(data):
                if item.get("WebBookmarkType") != "WebBookmarkTypeLeaf":
                    continue
                if not item.get("URLString"):
                    continue
                extracted = extract_item(item, path)
                if not extracted["url"]:
                    skipped += 1
                    continue
                if is_reading_list(path):
                    if args.bookmarks_only:
                        continue
                    if args.limit and rl_count >= args.limit:
                        continue
                    write_markdown(extracted, rl_dir, "reading-list")
                    rl_count += 1
                else:
                    if args.reading_list_only:
                        continue
                    if args.limit and bm_count >= args.limit:
                        continue
                    write_markdown(extracted, bm_dir, "bookmarks")
                    bm_count += 1

    if do_tabs:
        ot_dir = out_root / "Open Tabs"
        ct_dir = out_root / "Cloud Tabs"
        ot_count = extract_open_tabs(ot_dir, args.limit)
        ct_count = extract_cloud_tabs(ct_dir, args.limit)

    if do_plist:
        print(f"Reading List: {rl_count:5d} items → {out_root / 'Reading List'}")
        print(f"Bookmarks:    {bm_count:5d} items → {out_root / 'Bookmarks'}")
    if do_tabs:
        print(f"Open Tabs:    {ot_count:5d} items → {out_root / 'Open Tabs'}")
        print(f"Cloud Tabs:   {ct_count:5d} items → {out_root / 'Cloud Tabs'}")
    if skipped:
        print(f"Skipped:      {skipped} (no URL)")


if __name__ == "__main__":
    main()
