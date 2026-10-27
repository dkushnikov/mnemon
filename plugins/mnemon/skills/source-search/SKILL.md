---
name: source-search
description: Search the Mnemon knowledge library for sources, extracts, synthesis notes and collections (research dossiers). Use whenever the user asks "what do I know about X", "find sources about", "search knowledge", "have I saved anything about", references a topic they might have captured, or when you need to check for prior context before answering a substantive question. Prefer this over manual Grep on the vault — it handles tokenization, tier selection, domain filtering, and rating-based ranking in one call.
---

# /source-search — Search the Mnemon Knowledge Library

Searches the library of the selected Mnemon instance for sources matching a query. Results are ranked by rating (higher is more useful to the reader) then recency.

The engine performs a grep-style token scan — every token must appear in the file. It does **not** run a semantic (qmd) search, whatever the instance's `search_provider` says: the JSON reports `provider: "grep"` (the method that ran) and `configured_provider` (the config value, `null` when `--vault` was given). Never tell the user that semantic search was used.

## How to run

All the logic lives in the engine. Call the dispatcher via Bash — this is the only path form to use:

```bash
"${MNEMON_HOME:-$HOME/.local/share/mnemon/engine}/bin/mnemon" [--instance <name>] search \
  --query "<query>" \
  [--domain <domain>] \
  [--limit <n>] \
  [--include-source] \
  [--include-synthesis]
```

The command prints a JSON object to stdout. Parse it and format the results for the user (see "Output format" below).

`--instance <name>` goes before `search` and selects another Mnemon instance. Without it the engine uses the instance named by `MNEMON_INSTANCE` (a vault sets it in `.claude/settings.json` under `"env"`) or the default instance from `~/.config/mnemon/default`.

### Parameters

| Flag | Default | Purpose |
|---|---|---|
| `--query` | required | The search query. Tokenized into lowercase words of ≥2 chars; matching is AND across tokens (a file must contain all of them). |
| `--domain` | none | Filter by one domain tag from the extract frontmatter, e.g. `career`, `learning`, `ai`. |
| `--limit` | 10 | Max number of results to return. |
| `--include-source` | off | Also search `source.md` (raw captured content), not just `extract.md`. Use when the user is looking for a specific phrase that might only appear in the raw source. |
| `--include-synthesis` | off | Also search the `Synthesis/` directory (the reader's own notes). Use when the user might be looking for their own writing, not just captured material. |
| `--include-collections` | off | Also search `Collections/` (research dossiers: a question and the revisions filed under it). Use when the user asks what has been gathered on a topic, or which dossier a source belongs to. |

### Multi-word queries

Pass the whole query as a single `--query` argument. The engine tokenizes it and matches by AND (all tokens must appear in the same file). You do NOT need to construct separate grep calls. Examples:

- `--query "karpathy knowledge bases"` → finds files containing *karpathy* AND *knowledge* AND *bases*
- `--query "organizational design"` → finds files containing both words
- `--query "AI agents"` → same

### Fallback behavior

If zero files match all tokens (AND intersection is empty), the engine automatically relaxes to the most specific token that actually exists in the corpus — the one with the smallest nonzero match count — and returns those results with `"fallback_applied": true` and `"fallback_token": "<token>"` in the JSON. If none of the tokens appear in any file, `fallback_applied` stays `false` and `count` is `0`.

Tell the user when fallback fires, using the `fallback_token` value from the JSON:

> *"No file matched all of your search terms. Showing results for `<fallback_token>` only — the most specific term I could find in the library."*

Do not guess which token was used — read it from the JSON.

### Zero results

If `count == 0` even after fallback, report: *"No sources found for '<query>'. Try broader terms or use `/source-status` to see what's in the library."*

## Output format

The command returns JSON shaped like:

```json
{
  "schema": "mnemon-search/2",
  "query": "karpathy knowledge bases",
  "tokens": ["karpathy", "knowledge", "bases"],
  "provider": "grep",
  "configured_provider": "grep",
  "fallback_applied": false,
  "fallback_token": null,
  "count": 2,
  "results": [
    {
      "path": "Sources/2026-04-04_5c78d63f/",
      "file": "extract.md",
      "result_type": "extract",
      "tier": "full",
      "status": "extracted",
      "title": "Andrej Karpathy — LLM Knowledge Bases (tweet, Apr 2026)",
      "author": "Andrej Karpathy",
      "url": "https://x.com/karpathy/status/...",
      "domains": ["learning", "career", "ai"],
      "tags": ["knowledge-base", "llm", "obsidian"],
      "rating": 10,
      "created": "2026-04-04",
      "snippet": "Andrej Karpathy describes his personal workflow..."
    }
  ]
}
```

`result_type` says what the result is — `extract` or `source` (a captured revision), `synthesis` (the reader's note), `collection` (a research dossier; `status` is `open`, `inbox` or `closed`). `tier` is the **capture tier** of a Sources result — `full`, or `bookmark` for a lightweight capture — and `null` for a synthesis or collection result; a bookmark-tier source has no full extract, say so when you show it. (Before `mnemon-search/2` the `tier` key carried the result type; read `result_type` now.) A collection's `path` + `file` is `Collections/<name>.md`; several collections live in one folder and are distinct results.

Format it for the user like this:

```
Found 2 results for "karpathy knowledge bases":

1. **Andrej Karpathy — LLM Knowledge Bases (tweet, Apr 2026)** · rating 10
   by Andrej Karpathy · 2026-04-04 · domains: learning, career, ai
   https://x.com/karpathy/status/...
   → Andrej Karpathy describes his personal workflow...
   path: Sources/2026-04-04_5c78d63f/

2. **LLM Wiki** · rating 8
   ...
```

Lead with title + rating — rating is the reader's primary triage signal, so it should be visible immediately. Include the wikilink-friendly path at the end so the reader can jump to the note.

## When to broaden the search

If the first pass returns few or zero results and the user seemed to expect more, try:

1. Re-run with `--include-source` — the phrase might be in the raw capture, not the summary
2. Re-run with `--include-synthesis` — the reader may have written about it themselves
3. Re-run with `--include-collections` — a dossier may already gather the sources on this question
4. Re-run with a simpler / broader query — strip modifiers, keep the topic word

Don't do all three silently — tell the user what you're broadening and why.

## If the command fails

- `No such file or directory` on the dispatcher path → the engine is not installed at `$MNEMON_HOME` (default `~/.local/share/mnemon/engine`); follow `docs/install.md` in the Mnemon repository
- no instance / config not found → `mnemon init <name> --vault <path>` or set `MNEMON_INSTANCE`; `mnemon doctor` explains what is missing

## What this skill does not do

- **Write** anything to the vault. This is a read-only skill. To add a source, use `/source-add`.
- **Read full extract content.** Results include a snippet and metadata. If the user wants to read a full extract, use the `Read` tool on the returned path. Paths are relative to the instance's vault root — `mnemon [--instance <name>] config get vault_path` prints it.
- **Cross-instance federation.** One call searches one instance's vault. To look in another library, re-run with `--instance <name>`.
- **Reader-context framing.** Results are raw metadata + snippets, not a personalized narrative.
