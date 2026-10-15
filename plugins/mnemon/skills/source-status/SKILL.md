---
name: source-status
description: Show the status of your Mnemon knowledge library — total sources, breakdown by origin and domain, recent additions. Use when the user asks "how many sources", "library status", "what have I captured", "knowledge dashboard", or wants an overview of their library.
---

# /source-status — Knowledge Library Dashboard

Show a summary of the knowledge library of the selected Mnemon instance: total sources, breakdown by status/origin/domain, and recent additions.

## Usage

```
/source-status
/source-status --instance <name>    # another instance's library
```

No parameters needed for the default instance.

## How to Execute

Run the dispatcher's status command via Bash — this is the only path form to use:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] status
```

The engine prints the dashboard (vault path, totals, recent additions). Display it to the user.

If additional detail is needed (the engine shows the basics), augment with per-origin and per-domain counts. Resolve the vault root through the engine — do not read config files yourself:

```bash
MNEMON="${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon"
VAULT_PATH="$("$MNEMON" config get vault_path)"        # "$MNEMON" --instance <name> config get vault_path for another instance

# Count by origin
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
Instance: <name>
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

## If the command fails

- `No such file or directory` on the dispatcher path → the engine is not installed at `$MNEMON_HOME` (default `~/.local/share/mnemon/engine`); follow `docs/install.md` in the Mnemon repository
- no instance / config not found → `mnemon init <name> --vault <path>` creates one; `mnemon doctor` reports what is missing
- `No Sources/ directory` → the vault exists but has never received a source; suggest `/source-add`
