# Status Lifecycle

Superseded by the vault store design: [`docs/specs/2026-10-16-vault-store.md`](../docs/specs/2026-10-16-vault-store.md) (S2 — states).

A source has four axes, and the engine derives three of them from the files instead of storing them:

- **processing** — `captured` (a `source.md` without `extract.md`) or `extracted` (both present); `failed`, `extract-failed` and `integrity-error` live only in the capture receipt;
- **revision** — `current` or `superseded`, from the `supersedes:` graph of `source.md`;
- **relations** — `integrated` (linked from a `stable` Synthesis note) or `filed` (linked from a seed or draft), from the links;
- **judgement** — `dismissed`, the one stored verdict (`status: dismissed` in `extract.md`, program phase 3); `tier: full | bookmark` is the stored fact of how the capture was made.

`status: extracted` in `extract.md` is the schema's service value, not a state; `captured → extracted → integrated` as a hand-maintained field is retired. Legacy words (`fetch-failed`, `superseded`, `integrated`, `processed`, `bookmark`, empty) are tolerated and counted by `mnemon doctor --data` (`store/status-vocabulary`), never rewritten by the engine. Synthesis notes carry `status: seed → draft → stable` and `drafted_by:` for an agent's authorship.
