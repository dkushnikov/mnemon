---
name: source-search
description: Search your Mnemon knowledge library for sources and ideas. Use when the user asks "what do I know about X", "find sources about", "search knowledge", "have I saved anything about", or wants to find connections across their knowledge library.
---

# /source-search — Search Knowledge Library

Search across all sources in the knowledge vault. Uses grep (default) or QMD (if configured) for hybrid semantic + keyword search.

## Usage Examples

```
/source-search AI agents
/source-search "organizational design" --domain career
/source-search knowledge management --limit 5
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| query | — | Search terms (required) |
| `--domain <d>` | all | Filter by domain tag |
| `--limit <n>` | 10 | Max results |

## How to Execute

### Step 1: Load config

```bash
source ~/Mnemon/bin/mnemon-config.sh && load_config
```

This gives you `$VAULT_PATH`, `$SEARCH_PROVIDER`, `$QMD_COLLECTION`.

### Step 2: Run search based on provider

**If SEARCH_PROVIDER=grep (default):**

```bash
grep -rl --include="extract.md" -i "<query>" "$VAULT_PATH/Sources/" 2>/dev/null | head -<limit>
```

For each result file, extract metadata:
```bash
grep -m1 '^title:' "<file>" | sed 's/^title:[[:space:]]*//' | tr -d '"'
grep -m1 '^domains:' "<file>"
grep -m1 -i "<query>" "<file>"
```

**If SEARCH_PROVIDER=qmd:**

```bash
qmd search "<query>" --collection "$QMD_COLLECTION" --limit <limit> --format json
```

### Step 3: Apply domain filter (if --domain specified)

Filter results to only include files where `domains:` contains the requested domain.

### Step 4: Format output

Show results as:
```
Found N results for "<query>":

1. **Title** (date)
   Domains: [domain1, domain2]
   ...matching snippet...
   Path: Sources/YYYY-MM-DD_hash8/

2. **Title** (date)
   ...
```

If no results: "No sources found for '<query>'. Try broader terms or check /source-status for library overview."

If QMD not installed and search_provider=qmd: "QMD not installed. Falling back to grep. Install QMD for semantic search."
