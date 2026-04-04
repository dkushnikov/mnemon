---
name: source-status
description: Show the status of your Mnemon knowledge library — total sources, breakdown by origin and domain, recent additions. Use when the user asks "how many sources", "library status", "what have I captured", "knowledge dashboard", or wants an overview of their library.
---

# /source-status — Knowledge Library Dashboard

Show a summary of the knowledge library: total sources, breakdown by status/origin/domain, and recent additions.

## Usage

```
/source-status
```

No parameters needed.

## How to Execute

Call the gateway status action:

```bash
~/Mnemon/bin/knowledge-gateway.sh status
```

The gateway outputs a formatted dashboard. Display it to the user.

If additional detail is needed (the gateway shows basics), you can augment with:

```bash
# Count by origin
source ~/Mnemon/bin/mnemon-config.sh && load_config
for f in "$VAULT_PATH"/Sources/*/extract.md; do
  grep -m1 '^origin:' "$f" 2>/dev/null
done | sort | uniq -c | sort -rn

# Count by domain
for f in "$VAULT_PATH"/Sources/*/extract.md; do
  grep -m1 '^domains:' "$f" 2>/dev/null
done | tr '[],' '\n' | sed 's/^[[:space:]]*//' | grep -v '^$' | grep -v '^domains:' | sort | uniq -c | sort -rn
```

## Display Format

```
=== Mnemon Knowledge Library ===
Vault: <path>
Total: N sources

By status:   extracted: X | captured: Y
By origin:   url: A | youtube: B | audio: C | text: D
By domain:   learning: E | career: F | ...

Recent (last 5):
  2026-04-04_abc12345 — Article Title
  2026-04-03_def67890 — Video Title
  ...
```
