#!/usr/bin/env python3
"""Generate the shared contract fixtures of docs/specs/2026-10-15-capture-contracts.md §5.3–5.5.

    python3 tests/fixtures/contracts/generate.py            # rewrites the JSON files next to this script

Files: envelopes.json (named envelopes + payload files), expected-argv.json (the full `mnemon add` argv per
envelope, with placeholders), gateway-outcomes.json (gateway exit/RESULT scenarios → ingest result state).
Edit this generator, never the JSON files: hashes are computed, not typed. tests/test_hygiene.sh checks that
the committed files equal the generator's output. Stdlib only, python 3.9. These are stub-interface fixtures:
identity behaviour across envelopes is proven by the chain test, not by the stub outcomes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent

PRODUCER = {"id": "test-producer", "kind": "fixture", "host": "node-a", "version": "1"}
INSTANCE = "t"
CREATED = "2026-10-15T12:00:00+00:00"
TEXT = "hello\r\n\nworld\n"
TEXT_B = TEXT.encode("utf-8")
TEXT2 = TEXT + "edited\n"
TEXT2_B = TEXT2.encode("utf-8")
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
AUDIO = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def env(name, *, instance=INSTANCE, schema="mnemon-envelope/1", request=None, payload=None, fidelity="exact",
        supersedes=None, complete=True, envelope_id=None, provenance=None):
    eid = envelope_id or f"{PRODUCER['id']}:{name}"
    req = {"origin": None, "url": None, "title": None, "author": None, "intent": None, "context": None,
           "source_type": None, "tier": "full"}
    req.update(request or {})
    pl = {"kind": "text", "text": TEXT, "path": None, "media_type": "text/plain", "sha256": sha(TEXT_B), "bytes": len(TEXT_B)}
    if payload is not None:
        pl = payload
    return {"schema": schema, "envelope_id": eid, "producer": PRODUCER, "instance": instance, "created_at": CREATED,
            "supersedes": supersedes, "request": req, "payload": pl, "fidelity": fidelity,
            "provenance": provenance or {"fixture": name}, "complete": complete}


def file_payload(rel, data, media):
    return {"kind": "file", "text": None, "path": rel, "media_type": media, "sha256": sha(data), "bytes": len(data)}


def text_payload(text):
    b = text.encode("utf-8")
    return {"kind": "text", "text": text, "path": None, "media_type": "text/plain", "sha256": sha(b), "bytes": len(b)}


NONE_PAYLOAD = {"kind": "none", "text": None, "path": None, "media_type": None, "sha256": None, "bytes": 0}

cases = []


def add(name, envelope, *, file_name=None, payload_files=None, note=""):
    cases.append({"name": name, "file": file_name or f"{envelope['envelope_id']}.json", "envelope": envelope,
                  "payload_files": payload_files or {}, "note": note})


add("text-plain", env("text-plain", request={"title": "Plain T", "author": "A. Author", "intent": "why", "context": "personal"}),
    note="text payload, no url → --origin text, stdin = text bytes")
add("text-with-url", env("text-with-url", request={"url": "https://example.com/post"}),
    note="text + provenance url, no context → --origin text --url; no --context (the gateway's resolver decides); same text as text-plain → the real gateway answers skipped-duplicate")
add("text-origin-url-invalid", env("text-origin-url-invalid", request={"origin": "url", "url": "https://example.com/x"}),
    note="text payload with origin url → invalid-request, permanent, no gateway call")
add("url-auto", env("url-auto", payload=NONE_PAYLOAD, fidelity="url-only",
                    request={"url": "https://example.com/article", "intent": "read later"}),
    note="kind none + url, origin null → --url only; the gateway auto-detects")
add("url-explicit", env("url-explicit", payload=NONE_PAYLOAD, fidelity="url-only",
                        request={"origin": "url", "url": "https://example.com/explicit", "source_type": "article"}),
    note="kind none + origin url → --origin url --url … --source-type")
add("none-no-url", env("none-no-url", payload=NONE_PAYLOAD, fidelity="url-only"),
    note="schema-valid envelope with nothing to capture → needs_input, no gateway call")
add("file-pdf", env("file-pdf", payload=file_payload("attachments/file-pdf/paper.pdf", PDF, "application/pdf"),
                    request={"title": "Paper", "url": "https://example.com/paper.pdf"}),
    payload_files={"attachments/file-pdf/paper.pdf": PDF.hex()},
    note="file payload, origin null, provenance url → --file <snapshot> and NO --url (the url lands in provenance.json request_url)")
add("file-origin-audio", env("file-origin-audio", payload=file_payload("attachments/file-origin-audio/a.wav", AUDIO, "audio/wav"),
                             request={"origin": "audio", "url": "https://example.com/ep.wav"}),
    payload_files={"attachments/file-origin-audio/a.wav": AUDIO.hex()},
    note="file payload + origin audio → --origin audio --file <snapshot>, NO --url")
add("wrong-instance", env("wrong-instance", instance="other"), note="not ours → other-instance, untouched")
add("broken-schema", env("broken-schema", schema="nope/9"), note="→ quarantined")
add("conflict-copy", env("conflict-copy"), file_name="test-producer:conflict-copy.sync-conflict-20261015-120000-ABCDEFG.json",
    note="sync conflict copy → quarantined/conflict-copy, never processed")
add("path-escape", env("path-escape", payload=file_payload("../../etc/passwd", b"x", "text/plain")), note="→ quarantined (path)")
add("sha-mismatch", env("sha-mismatch", payload={"kind": "text", "text": TEXT, "path": None, "media_type": "text/plain",
                                                 "sha256": "0" * 64, "bytes": len(TEXT_B)}), note="→ quarantined / payload-mismatch")
add("bookmark", env("bookmark", request={"tier": "bookmark"}), note="→ unsupported (D12)")
add("incomplete", env("incomplete", complete=False), note="complete:false → quarantined (schema)")
add("context-undeclared", env("context-undeclared", request={"context": "mc"}),
    note="passed through to the gateway; its exit 2 (context not in charter) → failed/contract, permanent")
add("text-plain~2", env("text-plain~2", envelope_id="test-producer:text-plain~2", supersedes="test-producer:text-plain",
                        payload=text_payload(TEXT2), request={"context": "personal"}),
    note="re-capture → --recapture --supersedes <revision:text-plain>; deferred/predecessor-pending while text-plain has no result")
add("text-plain~10", env("text-plain~10", envelope_id="test-producer:text-plain~10", supersedes="test-producer:text-plain~9",
                         payload=text_payload(TEXT2 + "again\n"), request={"context": "personal"}),
    note="sorts after ~2 (numeric suffix order); its predecessor ~9 never appears → deferred/predecessor-pending, supersedes-unknown after 24 h")

(OUT / "envelopes.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def fixed(fidelity):
    return ["--capture-id", "<envelope_id>", "--import-source", "test-producer", "--import-ref", "<envelope_id>",
            "--producer-fidelity", fidelity, "--provenance-file", "<provenance>"]


argv = {
    "text-plain": {"argv": ["--origin", "text", *fixed("exact"), "--context", "personal", "--title", "Plain T",
                            "--author", "A. Author", "--intent", "why"],
                   "stdin_sha256": sha(TEXT_B), "stdin_bytes": len(TEXT_B)},
    "text-with-url": {"argv": ["--origin", "text", "--url", "https://example.com/post", *fixed("exact")],
                      "stdin_sha256": sha(TEXT_B), "stdin_bytes": len(TEXT_B)},
    "url-auto": {"argv": ["--url", "https://example.com/article", *fixed("url-only"), "--intent", "read later"],
                 "stdin_sha256": None, "stdin_bytes": 0},
    "url-explicit": {"argv": ["--origin", "url", "--url", "https://example.com/explicit", *fixed("url-only"),
                              "--source-type", "article"], "stdin_sha256": None, "stdin_bytes": 0},
    "file-pdf": {"argv": ["--file", "<snapshot:attachments/file-pdf/paper.pdf>", *fixed("exact"), "--title", "Paper"],
                 "stdin_sha256": None, "stdin_bytes": 0},
    "file-origin-audio": {"argv": ["--origin", "audio", "--file", "<snapshot:attachments/file-origin-audio/a.wav>", *fixed("exact")],
                          "stdin_sha256": None, "stdin_bytes": 0},
    "context-undeclared": {"argv": ["--origin", "text", *fixed("exact"), "--context", "mc"],
                           "stdin_sha256": sha(TEXT_B), "stdin_bytes": len(TEXT_B)},
    "text-plain~2": {"argv": ["--origin", "text", *fixed("exact"), "--context", "personal", "--recapture", "--supersedes",
                              "<revision:text-plain>"], "stdin_sha256": sha(TEXT2_B), "stdin_bytes": len(TEXT2_B)},
}
argv_doc = {"_notes": ["argv = the arguments after `mnemon add`; the order is normative and tests compare lists.",
                       "Placeholders: <envelope_id>; <provenance> = <ingest state>/payloads/<envelope_id>/provenance.json",
                       "(schema mnemon-provenance/1: envelope_id, producer, instance, created_at, fidelity, request_url, request, provenance);",
                       "<snapshot:REL> = absolute path of the payload snapshot under <ingest state>/payloads/<envelope_id>/;",
                       "<revision:NAME> = the `revision` recorded in NAME's result record.",
                       "`--context` appears only when request.context is set; optional metadata appears only when non-empty,",
                       "in the order --title --author --intent --source-type; `--recapture --supersedes` comes last.",
                       "A recovery of a `started` result that has no gateway receipt re-dispatches exactly this argv (dispatch.json);",
                       "with a receipt it dispatches `--capture-id <envelope_id>` alone.",
                       "Envelopes absent from `cases` never reach the gateway (see gateway-outcomes.json expected_without_gateway)."],
            "cases": argv}
(OUT / "expected-argv.json").write_text(json.dumps(argv_doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

R = "RESULT:"


def lines(**kv):
    order = ["capture_id", "revision", "path", "status", "title", "source_sha256", "extract", "error_class", "retryable"]
    return [R + k + "=" + str(kv.get(k, "")) for k in order]


REV1, REV2 = "2026-10-15_aaaaaaaa", "2026-10-15_bbbbbbbb"
outcomes = {
    "_notes": ["Scenario → what a stub gateway returns (exit code, stdout lines, optional stderr/sleep) and the ingest",
               "result it must produce. `attempt_consumed` false = the attempt is refunded and the stored state is `deferred`",
               "with a deadline_at. <envelope_id> in stdout is substituted per envelope. `default_scenario_by_case` drives",
               "the stub for the contract fixtures; tests may override per case."],
    "scenarios": {
        "extracted": {"exit": 0, "stdout": lines(capture_id="<envelope_id>", revision=REV1, path=f"Sources/{REV1}/", status="extracted",
                                                 title="Plain T", source_sha256=sha(TEXT_B), extract="ok"),
                      "state": "captured", "duplicate": False, "revision": REV1, "attempt_consumed": True},
        "extracted-recapture": {"exit": 0, "stdout": lines(capture_id="<envelope_id>", revision=REV2, path=f"Sources/{REV2}/", status="extracted",
                                                           title="Plain T", source_sha256=sha(TEXT2_B), extract="ok"),
                                "state": "captured", "duplicate": False, "revision": REV2, "attempt_consumed": True},
        "duplicate": {"exit": 0, "stdout": lines(capture_id="<envelope_id>", revision="2026-10-12_aaaaaaaa", path="Sources/2026-10-12_aaaaaaaa/",
                                                 status="skipped-duplicate", extract="ok"),
                      "state": "captured", "duplicate": True, "attempt_consumed": True},
        "exit0-malformed": {"exit": 0, "stdout": ["Added something"], "state": "failed", "error_class": "malformed-result",
                            "retryable": True, "attempt_consumed": True},
        "exit4-transient": {"exit": 4, "stdout": lines(capture_id="<envelope_id>", revision=REV1, path=f"Sources/{REV1}/", status="captured",
                                                       source_sha256=sha(TEXT_B), extract="missing", error_class="transient", retryable="true"),
                            "state": "failed", "error_class": "transient", "retryable": True, "revision": REV1, "next_attempt_at": "future",
                            "attempt_consumed": True},
        "exit4-permanent": {"exit": 4, "stdout": lines(capture_id="<envelope_id>", revision=REV1, path=f"Sources/{REV1}/", status="captured",
                                                       source_sha256=sha(TEXT_B), extract="invalid", error_class="invalid-output", retryable="false"),
                            "state": "failed", "error_class": "invalid-output", "retryable": False, "revision": REV1, "attempt_consumed": True},
        "exit4-malformed": {"exit": 4, "stdout": ["something went wrong"], "state": "failed", "error_class": "malformed-result",
                            "retryable": True, "attempt_consumed": True},
        "exit1-fetch": {"exit": 1, "stdout": lines(capture_id="<envelope_id>", status="failed", extract="none", error_class="fetch", retryable="true"),
                        "state": "failed", "error_class": "fetch", "retryable": True, "attempt_consumed": True},
        "exit1-unsupported": {"exit": 1, "stdout": lines(capture_id="<envelope_id>", status="failed", extract="none", error_class="unsupported", retryable="false"),
                              "state": "failed", "error_class": "unsupported", "retryable": False, "attempt_consumed": True},
        "exit1-archive-exists": {"exit": 1, "stdout": lines(capture_id="<envelope_id>", status="failed", extract="none", error_class="archive-exists", retryable="false"),
                                 "state": "failed", "error_class": "archive-exists", "retryable": False, "attempt_consumed": True},
        "exit1-integrity": {"exit": 1, "stdout": lines(capture_id="<envelope_id>", revision=REV1, path=f"Sources/{REV1}/", status="integrity-error",
                                                       source_sha256="", extract="none", error_class="evidence-mismatch", retryable="false"),
                            "state": "failed", "error_class": "integrity-error", "retryable": False, "revision": REV1, "attempt_consumed": True},
        "exit2-contract": {"exit": 2, "stdout": [], "stderr": "ERROR: context 'mc' is not declared in charter (declared: personal)",
                           "state": "failed", "error_class": "contract", "retryable": False, "attempt_consumed": True},
        "exit3-environment": {"exit": 3, "stdout": [], "state": "deferred", "error_class": "environment", "attempt_consumed": False},
        "exit75-locked": {"exit": 75, "stdout": [], "state": "deferred", "error_class": "locked", "attempt_consumed": False},
        "other-exit": {"exit": 9, "stdout": [], "state": "failed", "error_class": "unknown-exit", "retryable": True, "attempt_consumed": True},
    },
    "default_scenario_by_case": {"text-plain": "extracted", "text-with-url": "duplicate", "url-auto": "extracted", "url-explicit": "duplicate",
                                 "file-pdf": "extracted", "file-origin-audio": "exit4-transient", "context-undeclared": "exit2-contract",
                                 "text-plain~2": "extracted-recapture"},
    "expected_without_gateway": {"text-origin-url-invalid": "failed/invalid-request", "none-no-url": "needs_input",
                                 "wrong-instance": "other-instance", "broken-schema": "quarantined", "conflict-copy": "quarantined/conflict-copy",
                                 "path-escape": "quarantined", "sha-mismatch": "quarantined/payload-mismatch", "bookmark": "unsupported",
                                 "incomplete": "quarantined", "text-plain~10": "deferred/predecessor-pending"},
}
(OUT / "gateway-outcomes.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

README = """# Contract fixtures (spec `docs/specs/2026-10-15-capture-contracts.md` §5.3–5.5)

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
"""
(OUT / "README.md").write_text(README, encoding="utf-8")
for f in sorted(OUT.iterdir()):
    if f.name != "generate.py":
        print(f.name, f.stat().st_size)
