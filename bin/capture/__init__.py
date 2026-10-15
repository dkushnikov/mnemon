"""capture — the Mnemon capture layer (docs/specs/2026-10-15-capture-layer.md, §3).

resolve  · canonical identity (v1) and the per-host resolvers (arXiv, YouTube)
policy   · the one network policy of the engine's own requests (SSRF checks, pinned connections, caps)
fetch    · the fetch chain direct → rendered → external, `--expect pdf|audio`, the arXiv version resolution
check    · the acquisition decision table, the conversion check, the inventory and the outcome
sanitize · the HTML-level and the fence-aware markdown-level repairs (idempotent, counted)
lib_table· merged-cell tables rewritten into simple tables
convert  · one projection pipeline: reference, candidates and inventory from the same sanitized subtree
site     · map · fetch-bundle · verify · assemble
report   · the report schemas and the atomic writers
proc     · supervised children (one process group each) and the cancellation hierarchy

Stdlib only, python 3.9. The gateway calls the three CLIs (bin/capture-fetch.py, bin/capture-text.py,
bin/capture-site.py); their argv and reports are the contract.
"""
from __future__ import annotations

VERSION = "2.0"
