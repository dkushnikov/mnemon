# The inbox — `mnemon-envelope/1` and `mnemon ingest`

An **inbox** is a directory of envelopes. A **producer** (a bot, a scanner, an import script, a shortcut) writes one envelope per item and never talks to the gateway. The **consumer**, `mnemon ingest`, reads the inbox, validates each envelope, snapshots its payload, and runs `mnemon add` for it under the vault lock — once per attempt, with an authoritative result record per envelope. This is contract C of [`specs/2026-10-15-capture-contracts.md`](specs/2026-10-15-capture-contracts.md) (§5); this page is the operator's and producer's guide to it.

```
producer ──writes──▶ <inbox>/<envelope_id>.json (+ payload files)
                            │
            mnemon [--instance <name>] ingest <inbox>      (one pass, under the vault lock)
                            │
                  validate → snapshot → dispatch.json → claim → mnemon add … → result record
                            │
      <vault state>/ingest/<inbox-key>/results/<envelope_id>.json   (the only authority)
```

## 1. The envelope

One JSON file per item, `<inbox-dir>/<envelope_id>.json`, UTF-8, a **regular file** (never a symlink), at most 2 MB. Written as `.<envelope_id>.json.tmp` and renamed into place (atomic on one filesystem; synced transports copy the final name only). Payload files are written **before** the envelope.

```json
{
  "schema": "mnemon-envelope/1",
  "envelope_id": "telegram-personal:-100123:456",
  "producer": { "id": "telegram-personal", "kind": "telegram-bot", "host": "node-a", "version": "1" },
  "instance": "knowledge",
  "created_at": "2026-10-15T12:00:00+00:00",
  "supersedes": null,
  "request": {
    "origin": "text",
    "url": null,
    "title": "…", "author": "…", "intent": "…",
    "context": "personal",
    "source_type": null,
    "tier": "full"
  },
  "payload": {
    "kind": "text",
    "text": "verbatim content…",
    "path": null,
    "media_type": "text/plain",
    "sha256": "…", "bytes": 1234
  },
  "fidelity": "exact",
  "provenance": { "…": "opaque producer record (message ids, raw paths, dates)" },
  "complete": true
}
```

### Field rules

Every rule below is checked by the consumer; a violation quarantines the envelope (permanent, reason recorded, a copy kept under `quarantine/`). Unknown extra keys are ignored. The whole document must be representable as UTF-8 (no lone surrogate escapes such as `"\udcff"` in any string) and nest at most 64 levels deep — a document the decoder cannot even parse (nested beyond its recursion limit) is quarantined like any other broken one, never a crashed pass (§10 of the spec's §5.2: one envelope never ends a pass).

| Field | Rule |
|---|---|
| `schema` | exactly `mnemon-envelope/1` |
| `envelope_id` | `<producer.id>:<item_id>`, matching `^[A-Za-z0-9][A-Za-z0-9._:~-]{0,199}$`; **equals the file stem**. A re-capture of the same item is `<id>~2`, `<id>~3`, … with `supersedes` naming the previous `envelope_id`. The numeric suffix orders processing: `~2` before `~10` |
| `producer.id` | stable per producer **and account**, `^[a-z0-9][a-z0-9._-]{0,63}$`; with the **vault** it is the idempotency scope — envelope IDs are vault-wide: the same `envelope_id` arriving for two instances of one vault is the same item (the second is a duplicate by the gateway's rules). `kind`, `host`, `version`: single-line strings or `null` |
| `instance` | the target instance name; a consumer processes only envelopes whose `instance` equals its resolved `MNEMON_INSTANCE` — everything else is reported `other-instance` and left untouched (no record, nothing read beyond the file) |
| `created_at` | an ISO-8601 timestamp (`2026-10-15T12:00:00+00:00`; a trailing `Z` is accepted) |
| `supersedes` | `null`, or the `envelope_id` of the item this one re-captures (never itself) |
| `request.origin` | `text` · `url` · `file` · `pdf` · `audio` · `null` (auto-detect) |
| `request.url` | `null` or a non-empty single-line string |
| `request.title` / `author` / `intent` | `null` or single-line strings of at most 300 / 200 / 2000 characters; empty strings count as absent |
| `request.context` | `null` or a single-line string; an empty string counts as absent. The consumer **never validates it** — the gateway does, against the instance charter (spec §4.8); a refusal is a permanent `contract` failure |
| `request.source_type` | `null` or a single-line string (`article`, `video`, …); passed through |
| `request.tier` | `full` or `bookmark`; `bookmark` is still refused here (`unsupported`, permanent, visible) although the engine captures bookmarks since phase 3-capture (`mnemon add --tier bookmark`, [capture.md §15](capture.md)) — D12 is lifted with the producers (phase 4), together with a `request.canonical_url` key and the `RESULT:judgement=dismissed` backoff, once a real scanner states what its envelope needs (owner's decision, 2026-10-28) |
| `payload.kind` | `text` · `file` · `none` |
| `payload.text` (kind `text`) | the verbatim content; `bytes` = its **UTF-8 byte length** (not its character count), `sha256` of those bytes, at most 2 MB. A mismatch quarantines the envelope (`payload-mismatch`) |
| `payload.path` (kind `file`) | relative to the inbox dir: not absolute, no `..`, `.` or empty components (`/` and `\` are both separators), resolving (through symlinks) **inside** the inbox — checked again on the path the consumer actually opens; a regular file of at most 20 MB; `sha256` / `bytes` of the file. A missing or not-yet-matching file is `incomplete` (re-checked for 24 h), not a schema error |
| `payload` (kind `none`) | `sha256` null, `bytes` 0; a `request.url` is what gets captured; without one the envelope is `needs_input` — a valid envelope, not a schema error |
| `payload.media_type` | `null` or a single-line string; informational |
| `fidelity` | the **producer's claim**: `exact` · `embed` · `model-transcribed` · `url-only`. It reaches the vault as `capture.producer_fidelity`; the engine's own `capture.fidelity` is observed from the chain it ran |
| `provenance` | any JSON object; carried into `provenance.json` and the result record, never into the vault |
| `complete` | must be `true`; `false` is a schema error (the producer wrote the envelope before it was done) |

Only the `payload.kind` × `request.origin` pairs of the mapping table (§3) are valid; every other pair is `failed`/`invalid-request`, permanent.

Strings follow the serialization subset of the spec (§4.4): single-line, no control characters. The consumer rejects control characters and the length bounds; the gateway normalizes whitespace.

### Producer rules

1. **Payload first.** Write every payload file, then the envelope (`.<envelope_id>.json.tmp` → rename). The consumer may read the inbox at any moment.
2. **Never reuse an `envelope_id`.** The envelope's bytes are hashed; an envelope that changes under the same id is quarantined as `envelope-replaced` in **every** stored state, with the previous record kept under `history`. A corrected or edited item is a new envelope: `<id>~2` with `supersedes: "<id>"`.
3. **Re-captures are `~n` envelopes.** `supersedes` lets the gateway record the new revision as superseding the old one. The consumer resolves the superseded envelope's revision from its own result record; while that record does not exist yet, the re-capture waits (`deferred`/`predecessor-pending`), and after 24 h without a predecessor it is quarantined (`supersedes-unknown`). Write re-captures only for items you have delivered.
4. **One `producer.id` per producer and account.** Two bots for two accounts are two producers; an id that collides across accounts makes their item ids collide.
5. **`bytes` is the UTF-8 length** of `text`, and `sha256` is over those bytes — not over the file, not over a normalized form. CRLF, trailing newlines and unusual whitespace are preserved end to end; the gateway publishes exactly those bytes.
6. **The envelope is not an acknowledgement channel.** The consumer never moves, renames, edits or deletes an envelope; it only ever writes under the vault state dir. A producer that wants to know what happened reads the result records (§6) on the consumer's host — acknowledgements are **local-only** in v1.
7. **Sync conflicts.** A `.sync-conflict-` copy (Syncthing's naming) is quarantined under its own file name and never processed; the original keeps its id.

### Examples

A text item (a saved message):

```json
{
  "schema": "mnemon-envelope/1",
  "envelope_id": "notes-import:2026-10-15-0007",
  "producer": { "id": "notes-import", "kind": "import-script", "host": "laptop", "version": "1" },
  "instance": "knowledge",
  "created_at": "2026-10-15T12:00:00+00:00",
  "supersedes": null,
  "request": { "origin": "text", "url": null, "title": "On deliberate practice", "author": null,
               "intent": "reading notes", "context": "personal", "source_type": null, "tier": "full" },
  "payload": { "kind": "text", "text": "The note's full text…\n", "path": null, "media_type": "text/plain",
               "sha256": "<sha256 of the UTF-8 bytes>", "bytes": 23 },
  "fidelity": "exact",
  "provenance": { "note_id": "x-coredata://…", "exported": "2026-10-15" },
  "complete": true
}
```

A URL (nothing to attach; the gateway fetches it):

```json
{
  "schema": "mnemon-envelope/1",
  "envelope_id": "reading-list:a1b2c3",
  "producer": { "id": "reading-list", "kind": "shortcut", "host": "phone", "version": "2" },
  "instance": "knowledge",
  "created_at": "2026-10-15T12:00:00+00:00",
  "supersedes": null,
  "request": { "origin": null, "url": "https://example.com/article", "title": null, "author": null,
               "intent": "read later", "context": null, "source_type": null, "tier": "full" },
  "payload": { "kind": "none", "text": null, "path": null, "media_type": null, "sha256": null, "bytes": 0 },
  "fidelity": "url-only",
  "provenance": { "shared_from": "Safari" },
  "complete": true
}
```

A file (a PDF saved next to the envelope; the URL it came from is provenance, not a fetch instruction):

```json
{
  "schema": "mnemon-envelope/1",
  "envelope_id": "mail-attachments:msg-8812:1",
  "producer": { "id": "mail-attachments", "kind": "mail-rule", "host": "laptop", "version": "1" },
  "instance": "knowledge",
  "created_at": "2026-10-15T12:00:00+00:00",
  "supersedes": null,
  "request": { "origin": null, "url": "https://example.com/paper.pdf", "title": "Paper", "author": null,
               "intent": null, "context": null, "source_type": null, "tier": "full" },
  "payload": { "kind": "file", "text": null, "path": "attachments/msg-8812/paper.pdf", "media_type": "application/pdf",
               "sha256": "<sha256 of the file>", "bytes": 48213 },
  "fidelity": "exact",
  "provenance": { "message_id": "<…@example.com>" },
  "complete": true
}
```

## 2. The consumer: `mnemon ingest`

```
mnemon [--instance <name>] ingest <inbox-dir> [--dry-run] [--only <envelope_id>] [--limit N]
                                              [--max-attempts N] [--retry <envelope_id>] [--json]
```

The dispatcher resolves the instance once (global `--instance` / `--config`, or the environment) and runs `bin/mnemon-ingest.py` with the pair exported; a selector after the command is refused. Run it from cron, launchd or by hand — one pass per invocation, no daemon.

| Option | Effect |
|---|---|
| `--dry-run` | prints what the pass would do and the exact `mnemon add` argv per eligible envelope; writes nothing, takes no lock |
| `--only <envelope_id>` | processes that envelope only (by file stem) |
| `--limit N` | claims at most N **eligible** envelopes in this pass — settled, waiting and quarantined ones do not count; the rest are reported `waiting` |
| `--max-attempts N` | attempts allowed per envelope before it is `exhausted` (default 5), multiplied by `1 + retries` |
| `--retry <envelope_id>` | reopens an exhausted or permanently failed envelope (§7) and dispatches it in the same pass |
| `--json` | one JSON document (`mnemon-ingest-pass/1`) on stdout instead of the text lines |

**Exit codes:** 0 after a pass (whatever the envelopes' outcomes), 2 for usage (bad arguments, a missing inbox, an ineligible `--retry`, a non-writer host), 75 when another process holds the vault lock.

### One pass (spec §5.2)

1. **Lock.** `fcntl.flock(LOCK_EX | LOCK_NB)` on `<vault state>/lock`; held → exit 75. A process that was started under the lock (by `bin/vault-lock.py`, with `MNEMON_VAULT_LOCK_FD` / `MNEMON_VAULT_LOCK_INODE` naming an open descriptor on that file) does not acquire again. Every dispatched gateway inherits the descriptor; the kernel releases the lock when the last holder exits. `writer_host` set and not this host → exit 2 before any write.
2. **List** `*.json` in the inbox: dotfiles, `*.tmp`, directories and other non-regular files are skipped; a **symlinked** envelope gets a `quarantined`/`not-regular` record; a `.sync-conflict-` copy is `quarantined`/`conflict-copy`. Sorted by `(base id, numeric ~n)`, so `item` → `item~2` → `item~10`.
3. **Replacement check, first, for every stored state.** The envelope bytes are hashed; a result with a different `envelope_sha256` → `quarantined`/`envelope-replaced`, the previous record appended to `history`.
4. **Validate** (§1). Broken → `quarantined`; wrong `instance` → `other-instance`, untouched; `tier: bookmark` → `unsupported`; `kind: none` without a url → `needs_input`; an invalid kind × origin pair → `failed`/`invalid-request`. These settle without a gateway call and without spending an attempt.
5. **Transition table** (§5): terminal → `done-before`; not yet due → `waiting`; otherwise continue.
6. **Snapshot the payload** under `payloads/<envelope_id>/`: a file is copied to `files/<basename>` (apart from the control files `dispatch.json`, `provenance.json`, `stdin`, so a payload named like one of them never collides) and verified on the copy (sha256 and size) — a verified snapshot is kept and reused, so a payload replaced in the inbox after the snapshot changes nothing; missing or not yet matching → `incomplete` (re-checked every 15 minutes, `quarantined`/`incomplete-expired` 24 h after `first_seen`); a copy that fails with an `OSError` (an unreadable file) → `quarantined`/`payload-unreadable`; text is written to `payloads/<envelope_id>/stdin` after its sha256/bytes are verified (mismatch → `quarantined`/`payload-mismatch`). `provenance.json` is written (§3).
7. **Predecessor** (`supersedes`): resolved from the superseded envelope's result (§5); unresolved predecessors defer the envelope.
8. **`dispatch.json`** — the exact request about to be dispatched — is persisted **before** the claim, so a crash between the claim and the gateway's answer can be recovered without reconstructing anything. From this point the request is **frozen**: every later dispatch of the envelope (a retry, a deferred re-check, a crash recovery) reuses `dispatch.json` and the snapshot as they are — the producer-side payload is never re-read, the predecessor is never resolved again, and `incomplete` can only occur before the freeze.
9. **Claim** = one atomic rewrite of the result record: `state: started`, `attempts + 1`, `envelope_sha256`, `dispatch_sha256`, `started_at`. It is the only place an attempt is spent; a first attempt, a retry and a crash recovery all go through it.
10. **Dispatch.** The saved argv runs with stdin from the snapshot (`dispatch_mode: request`) until this inbox's result record is `receipt_bound` — set once a dispatch of the full saved request came back exit 0 or 4 with `RESULT:capture_id=<envelope_id>`, i.e. the gateway bound the request to its receipt. From then on the gateway is replayed with `mnemon add --capture-id <envelope_id>` alone (`dispatch_mode: replay`), for retries and crash recoveries alike. A receipt that already exists in the vault ledger for the id without `receipt_bound` here (another inbox, an operator's own capture) gets the full request and the gateway's binding check — never a silent acknowledgement; a conflicting request is exit 2 → `failed`/`contract`. The consumer waits — it has **no timeout of its own**; timeouts live in the extractor's process group. Acquisition and transcription have **no timeout in phase 1** either (spec §12 A8): a hung download or whisper run holds the vault lock until it ends or the operator kills it; the next pass then recovers the claim from `dispatch.json` and the gateway from its receipt.
11. **Outcome** (§4) → `last_exit`, `last_result` (the `RESULT:` map), `last_error` (the stderr tail), written atomically.
12. **Backoff** for a retryable failure: `next_attempt_at = now + min(15 min × 2^(attempts − 1), 24 h)`; when `attempts >= attempts_allowed` after that failure the state is `exhausted` — `attempts_allowed` is an allowance, not a strict invocation cap: a crash-recovery claim may exceed it by one, and `>=` catches that. Exit 3 and exit 75 → `deferred` for 15 minutes with the attempt **refunded**.
13. `--retry` (§7).
14. **Summary line**: `ingest: processed N · captured N · failed N · needs_input N · waiting N · deferred N · done-before N · other-instance N · unsupported N · quarantined N` (`exhausted` counts under `failed`, a new `incomplete` under `deferred`); `--json` for machines. Exit 0 unless the lock or usage refused.

Per envelope one line is printed: `<report> <envelope_id> — <detail>`, e.g. `captured test-producer:text-plain — Sources/2026-10-15_c2e16f9b/`, `waiting x — failed/transient, next attempt at …`, `done-before x — captured`. The gateway's stderr is forwarded; its stdout is kept in the record only.

## 3. Envelope → gateway mapping (normative)

The argv after `mnemon add`, in this order: the origin-dependent part first, then the fixed part `--capture-id <envelope_id> --import-source <producer.id> --import-ref <envelope_id> --producer-fidelity <fidelity> --provenance-file <payloads/<envelope_id>/provenance.json>`, then `--context <ctx>` **only when `request.context` is set**, then the optional metadata in the order `--title --author --intent --source-type` (each only when non-empty), then `--recapture --supersedes <revision>` when the predecessor resolved to one.

| `payload.kind` | `request.origin` | `request.url` | Leading argv | stdin |
|---|---|---|---|---|
| `text` | `null` / `text` | absent | `--origin text` | the snapshot bytes |
| `text` | `null` / `text` | present | `--origin text --url <url>` (the URL is provenance; the gateway never fetches for `origin text`) | the snapshot bytes |
| `none` | `null` | present | `--url <url>` (origin auto-detected: `url`, `youtube`, `pdf`) | — |
| `none` | `url` | present | `--origin url --url <url>` | — |
| `none` | any | absent | `needs_input` (no call) | — |
| `file` | `null` | any | `--file <snapshot path>` — **never `--url`**: the verified file is authoritative; the request URL goes into `provenance.json` | — |
| `file` | `file` / `pdf` / `audio` | any | `--origin <origin> --file <snapshot path>` — never `--url` | — |
| anything else | | | `failed`/`invalid-request` (permanent) | |

The executable form of this table is `tests/fixtures/contracts/expected-argv.json`, generated by `generate.py` next to it and asserted by `tests/test_ingest.sh` (consumer) and the gateway suite (acceptance of every argv).

**`provenance.json`** (`mnemon-provenance/1`), written next to the snapshot and named in the argv:

```json
{"schema": "mnemon-provenance/1", "envelope_id": "…", "producer": {…}, "instance": "…", "created_at": "…",
 "fidelity": "…", "request_url": "<request.url or null>", "request": {…the envelope's request…},
 "provenance": {…the envelope's provenance…}}
```

The gateway records its path in the receipt (`metadata.provenance_file`) and nothing of it in the vault.

## 4. Outcome matrix (gateway → result)

The exit code is read first, then the `RESULT:` lines (`RESULT:<key>=<value>`, spec §3.6).

| Gateway exit | `RESULT:` | Result state |
|---|---|---|
| 0, `status=extracted` | `revision`, `path`, `source_sha256` recorded | `captured` |
| 0, `status=skipped-duplicate` | `revision`, `path` recorded | `captured`, `duplicate: true` |
| 0 without a well-formed `RESULT:status` | — | `failed`/`malformed-result`, retryable |
| 4 with `RESULT:retryable=true\|false` and an `error_class` | `error_class`, `retryable`, `revision`, `path` | `failed` with the revision recorded (a source was published), `retryable` as reported — `false` is permanent with that class |
| 4 without a well-formed `RESULT:` block | — | `failed`/`malformed-result`, retryable |
| 1, `status=failed` | `error_class`, `retryable` | `failed`, `retryable` as reported (when the gateway omits it: `fetch`, `convert` → true; `unsupported`, `no-text`, `invalid-utf8`, `archive-exists`, `input-missing` → false) |
| 1, `status=integrity-error` | `error_class`, `revision`, `path` | `failed`/`integrity-error`, permanent, revision recorded |
| 1 without a well-formed `RESULT:` block | — | `failed`/`malformed-result`, retryable |
| 2 | — | `failed`/`contract`, permanent, stderr kept (`last_error`) |
| 3 | — | `deferred`/`environment`, attempt refunded, `deadline_at` = now + 15 min |
| 75 | — | `deferred`/`locked`, attempt refunded, `deadline_at` = now + 15 min |
| other | — | `failed`/`unknown-exit`, retryable once, then permanent |

## 5. States and transitions

Stored states: `started` · `incomplete` · `deferred` · `failed` · `exhausted` · `captured` · `needs_input` · `unsupported` · `quarantined`. **`waiting` is a report, never a stored state.**

| Stored state | On the next pass (after the replacement check) |
|---|---|
| (none) | validate → `needs_input` / `unsupported` / `quarantined` / `invalid-request`, else snapshot, dispatch request, claim |
| `started` | recover: `dispatch.json` present → claim again (`attempts + 1`) and dispatch per step 10 (by id when the record is `receipt_bound`, else the frozen request); `dispatch.json` absent (a crash before step 8) → as (none) |
| `incomplete` | before `deadline_at` → `waiting`; after → re-check the payload; still missing → `incomplete` again until 24 h after `first_seen` → `quarantined`/`incomplete-expired` |
| `deferred` (`environment`, `locked`, `predecessor-pending`) | before `deadline_at` → `waiting`; after → `environment`/`locked`: claim and dispatch the frozen request; `predecessor-pending`: resolve the predecessor again (the request is not frozen yet); 24 h after `first_seen` → `quarantined`/`supersedes-unknown` |
| `failed`, `retryable: true` | before `next_attempt_at` → `waiting`; after → claim and dispatch (by id when `receipt_bound`, else the frozen request as it is) |
| `failed`, `retryable: false` | terminal (`done-before`) unless `--retry` |
| `exhausted` | terminal unless `--retry` |
| `captured`, `needs_input`, `unsupported`, `quarantined` | terminal (`done-before`) |

**Predecessor rule** (`supersedes`), by the superseded envelope's stored result:

| Predecessor's result | The re-capture |
|---|---|
| `captured`, or `failed` / `exhausted` **with** a `revision` (a source was published) | `--recapture --supersedes <that revision>` |
| `captured` without a revision, `exhausted` without one, a permanent `failed` without one, `needs_input`, `unsupported`, `quarantined` | captured without `supersedes`; `supersedes_unresolved: true` in the result |
| `started`, `incomplete`, `deferred`, a retryable `failed` without a revision, or no result at all | `deferred`/`predecessor-pending` (no attempt spent); `quarantined`/`supersedes-unknown` 24 h after `first_seen` |

Results are read as they are **at the moment** the re-capture is examined: within one pass `item` is processed before `item~2`, so a predecessor captured in the same pass resolves immediately.

## 6. State on disk

Everything the consumer writes lives under the **vault state dir** (spec D6): `${XDG_STATE_HOME:-~/.local/state}/mnemon/vaults/<sha256(physical vault path)[:16]>/`, mode `0700`, shared by every instance and ad-hoc config that points at the same vault.

```
<vault state>/
  lock                               the vault lock (flock)
  captures/<capture_id>.json         the gateway's receipts (the ledger)
  ingest/<inbox-key>/                inbox-key = sha256(physical absolute inbox path)[:8]
    inbox.json                       {"inbox": "<path>", "instance": "<name>"} — for `mnemon doctor`
    results/<envelope_id>.json       the result record — the only authority (mnemon-ingest-result/1)
    payloads/<envelope_id>/
      stdin                          the text snapshot (kind text)
      files/<basename>               the file snapshot (kind file), verified on the copy — apart from the control files
      provenance.json                mnemon-provenance/1
      dispatch.json                  mnemon-dispatch/1 — the exact request, persisted before the claim and frozen from then on
    quarantine/<file name>           a copy of every quarantined envelope
```

Records are `0600`, directories `0700`, every write is `<name>.tmp` + fsync + rename. Nothing here is synced; a second host runs producers only (one writer per vault). Phase 1 assumes **one writer account and one state root** per vault (spec §12 A8): the ledger and the lock live under `${XDG_STATE_HOME:-~/.local/state}` of the account that runs `mnemon ingest` and `mnemon add`; a second account or a second `XDG_STATE_HOME` on the same vault would have its own ledger and its own lock, and the two would not see each other.

### The result record (`mnemon-ingest-result/1`)

| Field | Meaning |
|---|---|
| `envelope_id`, `envelope_sha256` | the envelope and the hash of its bytes (the replacement check) |
| `dispatch_sha256` | sha256 of `dispatch.json` at the last claim |
| `state`, `error_class`, `error`, `retryable` | the outcome (§4, §5); `error` is a one-line explanation |
| `attempts`, `attempts_allowed`, `retries` | lifetime attempts (refunded for exit 3/75), `--max-attempts × (1 + retries)`, the number of `--retry` |
| `next_attempt_at` | when a retryable failure is due again |
| `deadline_at` | when a `deferred` or `incomplete` record is re-examined |
| `capture_id`, `revision`, `path`, `source_sha256`, `duplicate` | from the gateway's `RESULT:` lines (`capture_id` = `envelope_id`) |
| `supersedes_unresolved` | the predecessor had no revision; captured without `supersedes` |
| `last_exit`, `last_result`, `last_error` | the gateway's exit code, its `RESULT:` map and its stderr tail |
| `history` | previous records of this id (set by `envelope-replaced`), oldest first |
| `first_seen`, `started_at`, `updated_at`, `retry_requested_at` | timestamps (UTC, ISO-8601) |
| `writer` | the host that wrote the record |
| `dispatch_mode` | `request` (the saved argv) or `replay` (`--capture-id` alone, once `receipt_bound`) |
| `receipt_bound` | `true` once a dispatch of the full saved request came back exit 0 or 4 with `RESULT:capture_id=<envelope_id>` — the gateway bound the request to its receipt; from then on dispatches replay by id (step 10) |

### `dispatch.json` (`mnemon-dispatch/1`)

```json
{"schema": "mnemon-dispatch/1", "argv": ["--origin", "text", "--capture-id", "…", "…"], "stdin": "stdin",
 "env": {"MNEMON_INSTANCE": "knowledge", "MNEMON_CONFIG": "<resolved config file>"}}
```

`argv` are the arguments after `mnemon add`; `stdin` is the snapshot file name under `payloads/<envelope_id>/` or `null`.

### Quarantine reasons (`error_class` of a `quarantined` record)

| Reason | Meaning |
|---|---|
| `not-regular` | the envelope is a symbolic link |
| `conflict-copy` | a `.sync-conflict-` copy; never processed |
| `too-large` | the envelope file exceeds 2 MB |
| `invalid-json` | not valid UTF-8 JSON: bad bytes, bad syntax, a document nested beyond the decoder's recursion limit, or a string that no UTF-8 file can carry (a lone surrogate escape such as `"\udcff"`); `error` names the exception and, for a string, its path |
| `schema` | a field rule of §1 failed, or the document nests deeper than 64 levels; `error` names the field |
| `path-escape` | `payload.path` is absolute, contains `..`, or resolves outside the inbox |
| `payload-mismatch` | `payload.sha256` / `bytes` do not match the UTF-8 bytes of `payload.text` |
| `payload-not-regular` / `payload-too-large` | the payload file is not a regular file / exceeds 20 MB |
| `payload-unreadable` | the payload file could not be copied into the snapshot (an `OSError` — unreadable, I/O error, the state dir unwritable); `error` names the exception |
| `envelope-replaced` | the envelope's bytes changed under the same id (previous record under `history`) |
| `incomplete-expired` | the payload file never arrived or never matched within 24 h of `first_seen` |
| `supersedes-unknown` | the superseded envelope produced no result within 24 h of `first_seen` |
| `internal-error` | an exception the consumer did not expect while reading, parsing, validating or snapshotting this envelope; `error` names it. The pass went on to the next envelope — one envelope never ends a pass. A corrected `~2` envelope is the way forward, or a fix of the consumer when the envelope was fine |

## 7. `--retry`

Allowed for `exhausted`, and for a permanent `failed` whose class is not `contract` or `invalid-request` (those need a corrected `~2` envelope, not a retry). Never for `quarantined`, `needs_input`, `unsupported`, `captured`, nor for a retryable `failed` that is merely waiting for its `next_attempt_at`, nor for `started` / `incomplete` / `deferred`. An ineligible `--retry` is a usage error (exit 2) and changes nothing.

It sets `retries + 1` (raising `attempts_allowed`), state `failed` with `retryable: true`, `next_attempt_at: now` and `retry_requested_at`; lifetime `attempts` are kept, and the same pass dispatches the envelope (by id when the record is `receipt_bound`, else the saved request).

## 8. `mnemon doctor`

`doctor` reports the ledger without writing anything: `vault-state` (the vault state dir exists and is writable, mode `0700`), `vault-lock` (a non-blocking probe: `free` / `held`), `captures` (receipts in `failed` / `extract-failed` with the `--resume` hint, receipts whose published files are missing), `ingest` (per inbox: `failed` — the retryable ones — `exhausted`, `incomplete` and `deferred` counts raise a `warn`; permanent failures are listed apart as `permanent N (not retried)` and never raise the status — they wait for a corrected `~2` envelope or a `--retry`, not for the next pass), and `fence` (the installed Claude Code's version, `--restricted`, and a recorded probe pass — `mnemon fence-probe` writes it).

## 9. Writing a producer — checklist

Choose a stable `producer.id` per producer and account, and an `item_id` that is stable for the item in its system (a message id, a note id, a hash of the URL) so that a re-run of the producer never writes a second envelope for the same item; compute `sha256` and `bytes` over the UTF-8 bytes of `text`, or over the file; write the payload file first, then the envelope as `.<envelope_id>.json.tmp` and rename it; put the target `instance` in; set `fidelity` honestly (`exact` for the bytes you were given, `url-only` when you hand over a URL, `model-transcribed` / `embed` when a model or an embed produced the text); keep everything you know about the item in `provenance` (the consumer carries it, the vault never sees it); never edit an envelope in place — re-capture with `~2` and `supersedes`; and read the result records on the consumer's host to learn what happened. Test against `tests/fixtures/contracts/envelopes.json` (every rule above has a fixture) and run `mnemon ingest <inbox> --dry-run` to see the argv your envelopes produce before the first real pass.

## 10. Test hooks

Honoured only with `MNEMON_TEST_HOOKS=1`: `MNEMON_INGEST_ADD_CMD` replaces the `mnemon add` call (it receives the same argv after `add`, the same stdin and the same environment, lock descriptor included); `MNEMON_TEST_CRASH_AT=after-snapshot | after-dispatch-saved | after-claim` kills the pass at that boundary; `MNEMON_TEST_RAISE_FOR=<envelope_id>` raises an exception while that envelope is processed (it must end as `quarantined`/`internal-error` with the pass continuing). Without the guard all three are ignored. `tests/test_ingest.sh` is the executable form of this page.
