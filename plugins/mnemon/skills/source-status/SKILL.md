---
name: source-status
description: Show the status of your Mnemon knowledge library — total sources, how many are extracted, the stored status words, superseded and chunked counts, recent additions. Use when the user asks "how many sources", "library status", "what have I captured", "knowledge dashboard", or wants an overview of their library.
---

# /source-status — Knowledge Library Dashboard

Show the dashboard of the knowledge library of the selected Mnemon instance. Every number comes from the engine; this skill runs one command and relays its output.

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

The engine prints the dashboard: the vault path, `Total sources` (revision folders under `Sources/`), `Extracted` (folders with an `extract.md`), `By status` (the stored `status:` words of the extracts — `extracted`, `dismissed`, legacy words, empty, unknown — a breakdown of the extracted count, never a second counter), `Superseded` (revisions a newer revision replaced), `Chunked` (extracts produced in parts) and the five most recent additions. Display it as printed. The output names only the vault; the instance name for the `Instance:` line below comes from `config get instance` (same `--instance` flag, before the command):

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] config get instance
```

Do not compute anything the engine did not print — no counting by origin or domain, no reading of `Sources/` or the instance file in shell. When the user asks for a breakdown the dashboard does not have, say that the engine does not report it yet and offer `/source-search --domain <domain>` for one domain's sources.

For one line with the vault store's health (counters plus the data doctor's `fail · warn · probed` summary), the engine has `status --brief` — use it when the user asks whether the library is healthy rather than how big it is:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] status --brief
```

## Display Format

The engine's output, line for line, with one line added after the header:

```
=== Mnemon Status ===
Instance: <name>                 ← the one added line (from `config get instance`)
Vault: <path>

Total sources: N
Extracted: M
By status: extracted X · dismissed Y · legacy words Z · empty E · unknown U
Superseded: S
Chunked: C

Recent additions:
  2026-04-04_abc12345 — Article Title
  2026-04-03_def67890 — Video Title
```

Keep the engine's numbers, wording and line breaks; do not rename the header or join lines.

## If the command fails

- `No such file or directory` on the dispatcher path → the engine is not installed at `$MNEMON_HOME` (default `~/.local/share/mnemon/engine`); follow `docs/install.md` in the Mnemon repository
- no instance / config not found → `mnemon init <name> --vault <path>` creates one; `mnemon doctor` reports what is missing
- `No Sources/ directory` → the vault exists but has never received a source; suggest `/source-add`
- exit 75 → the engine is being updated (`mnemon release`); retry in a moment
