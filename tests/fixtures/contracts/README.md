# Contract fixtures (spec `docs/specs/2026-10-15-capture-contracts.md` §5.3–5.5)

Shared by the gateway suite and the ingest suite so the two halves cannot drift:

- `envelopes.json` — named `mnemon-envelope/1` envelopes (`file` = the name to materialize in a temp inbox;
  `payload_files` = relative path → hex bytes to write before the envelope).
- `expected-argv.json` — the exact `mnemon add` arguments the consumer must build per envelope (placeholders
  documented inside); the gateway suite asserts it accepts every argv listed here.
- `gateway-outcomes.json` — gateway exit codes + `RESULT:` lines per scenario and the ingest result state they
  must produce; `default_scenario_by_case` maps envelopes to scenarios for the stub gateway;
  `expected_without_gateway` lists envelopes the consumer settles before calling the gateway.

These are stub-interface fixtures: identity behaviour across envelopes (same text → one resource, an edited
re-capture → a new resource) is proven by the real-chain test, not by the stub outcomes.

Regenerate with `python3 tests/fixtures/contracts/generate.py` after editing the generator — never edit the
JSON by hand (hashes are computed). The hygiene suite checks the committed files match the generator.
