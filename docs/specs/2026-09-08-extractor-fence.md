# Extractor fence — run the gateway's `claude -p` with the least tools it needs

> **Superseded (2026-10-15).** The fence that ships is Contract B of [`2026-10-15-capture-contracts.md`](2026-10-15-capture-contracts.md) (extractor fence v2): the extractor runs with **no tools at all** — `claude -p --restricted --tools "" --permission-prompts none --strict-mcp-config --no-session-persistence --json-schema …`, prompt on stdin — and returns structured output that the gateway validates and publishes; the gateway, not the model, acquires the content and writes `source.md`. The per-origin tool tables and the `Write` confinement below were withdrawn after the probes of its §4.6 (path-scoped `Write` allow rules are not honoured under `--restricted`). This document stays as the record of the problem statement and of the first design; nothing in it is normative. The chronology is in §Status.

**Status:** superseded · written 2026-09-08 · owner: maintainer · scope then: `bin/knowledge-gateway.sh`, `tests/test_gateway.sh`

## Resume prompt

> Implement the extractor fence per `docs/specs/2026-09-08-extractor-fence.md`: the gateway's `claude -p` must run without Bash/Edit and with Write confined to the target `Sources/<folder>/`; text/transcript origins write `source.md` deterministically from stdin; `--ref-id` must land in `source.md` frontmatter. Keep `--dry-run` output stable, add the tests listed in §Acceptance, and record the result in §Status.

## Problem

Every capture puts **untrusted content** (a web page, a PDF, a transcript, a forwarded message) into a `claude -p` session that currently runs with

```
--allowedTools=Read,Write,Edit,Bash,Glob,Grep,WebFetch
```

in the vault directory (`invoke_claude()`, "Claude invocation" section of `bin/knowledge-gateway.sh`; the `ref:mcp` origin even passes `skip_allowed_tools=true`, i.e. no restriction at all). A page that says "ignore the template, run this command, edit that note" is a prompt injection with Bash and Edit on the user's machine and Write anywhere in the vault. Nothing in the pipeline needs that surface:

- the extractor **reads** the fetched content (stdin or `Read` of a local file), the template and the reader context;
- it **writes** two files into one new folder, `Sources/YYYY-MM-DD_<hash8>/source.md` and `extract.md`;
- `WebFetch` is needed only for the `url` origin (and `--render` pre-renders outside Claude).

Until now the exposure was bounded by a human invoking `/source-add` on a page they chose. Automated feeders (queues, batch imports, bots) remove that human from the loop, so the fence has to be in the gateway itself.

## Target contract

1. **Tool surface per origin**

   | Origin | Tools |
   |---|---|
   | `url`, `youtube` (transcript via yt-dlp is pre-fetched, but the model may still fetch the page) | `Read, Write, WebFetch` |
   | `text`, `pdf`, `audio`, `book`, `idea`, `ref:vault` | `Read, Write` |
   | `ref:mcp` | `Read, Write` + only the MCP server named by `--ref-source` — never "no restrictions" |

   Never `Bash`, `Edit`, `Glob`, `Grep`, `Agent`/subagents, `WebSearch`. (`Glob`/`Grep` are convenience only; the prompt already names every file the extractor needs.)

2. **Write confined to the target folder.** The gateway computes the folder name before invoking Claude (it already knows the canonical URL/hash). Confine writes with Claude Code permission rules instead of trusting the prompt: pass a per-run settings file via `--settings` with

   ```json
   {
     "permissions": {
       "allow": ["Read", "Write(<vault>/Sources/<folder>/**)", "WebFetch"],
       "deny":  ["Write", "Edit", "Bash", "Glob", "Grep", "Agent", "WebSearch"]
     }
   }
   ```

   (deny rules win over allow; the specific `Write(path/**)` allow is evaluated before the bare `Write` deny — verify the precedence on the installed Claude Code version with a probe before relying on it, and fall back to `--tools`/`--disallowedTools` if the pattern form is not honoured.) In `-p` mode a tool call outside the allow list is denied without a prompt, which is the behaviour we want. Keep `--allowedTools` as the second layer.

3. **Deterministic `source.md` for text-like origins.** For `text`, `transcript`-style stdin content and `--file` inputs, the gateway writes `source.md` itself (frontmatter + the exact bytes it received) **before** invoking Claude, and tells the model to write only `extract.md`. Known failure this closes: large stdin content made the model emit a stub body (`[Full transcript as provided…]`) while exiting 0 with `RESULT:status=extracted` — the L1 archive kept the original, the vault did not. The model must never be the copy path for content the gateway already holds verbatim. Oversized content: point the extractor at the file (`Read`) instead of inlining it in the prompt.

4. **`--ref-id` in frontmatter.** Batch/queue feeders use `--ref-id` to reconcile a capture with their own record after a crash between "Source published" and "result written". Make sure the value lands in `source.md` frontmatter (`ref_id:`) for every origin, not only `ref:*`, and that `source-status` can find a folder by it.

5. **Verification by files, not exit code.** After Claude returns, the gateway checks that `source.md` and `extract.md` exist in the target folder, that `source.md`'s body length is within tolerance of the input for text-like origins (or byte-identical when the gateway wrote it), and reports `RESULT:status=failed` otherwise — even if Claude exited 0.

## Acceptance (add to `tests/test_gateway.sh`)

- `--dry-run` shows the tool list and the settings fence for each origin class (`url` → with WebFetch; `text` → without).
- `ref:mcp` dry-run never prints "no tool restrictions".
- Text origin: `source.md` body is byte-identical to stdin; a run where the model output is a stub still yields a correct `source.md` and `RESULT:status=failed` for the extract only if `extract.md` is missing.
- A probe run (manual, once per Claude Code version): a prompt that instructs the model to `Bash echo pwned > /tmp/x` and to `Write` outside the folder — both must be denied; document the Claude Code version the probe passed on in §Status.
- `--ref-id abc123` → `ref_id: abc123` in `source.md` frontmatter for `url` and `text` origins.

## Non-goals

- No change to templates, reader context or folder naming.
- No sandboxing of `yt-dlp`/`whisper`/Chrome pre-render (separate concern).
- No network egress policy for WebFetch beyond what Claude Code provides.

## Status

- 2026-09-08 — spec written (from the personal-bot capture-queue design review: an automated feeder makes the existing exposure reachable without a human choosing the page). Not started.
- 2026-10-15 — superseded in part by `docs/specs/2026-10-15-capture-contracts.md`: items 1–2 of the target contract (per-origin tool surface, `Write` confined by permission rules) are withdrawn — probes on Claude Code 2.1.296 showed path-scoped `Write` allow rules are not honoured under `--restricted` (its §4.6); the extractor now runs with no tools at all and returns structured output (its D4, §4.2). Items 3–5 (deterministic `source.md`, `--ref-id` in frontmatter, verification by files) are generalized to every origin there (its D3, D7, §3.1, §3.6) and implemented on `feat/p1-gateway`.
