---
type: collection
status: open
question: "Every rejected line form of the grammar in one dossier"
curated_by: owner
created: 2026-10-01
---

## Items

- [[Sources/2026-01-10_aaaaaaaa/extract]] — **valid** — the one good item
- [[Sources/2026-02-11_bbbbbbbb/extract]] and [[Sources/2026-03-12_cccccccc/extract]] — two links on one line
- a list line without any link
- [[Synthesis/Stable note]] — a link outside Sources/
- [[Sources/../Other/extract]] — path traversal
- [[/Sources/2026-01-10_aaaaaaaa/extract]] — absolute path
- [[Other vault/Sources/2026-01-10_aaaaaaaa/extract]] — another instance
- [[Sources/2026-01-10_aaaaaaaa/source]] — the same revision twice
- [[Sources/2026-01-10_aaaaaaaa/notes.md]] — an unknown file in the revision
- [[Sources/not-a-revision/extract]] — a malformed revision name
free text between two items
  - a continuation under the free text, not under an item
```
- [[Sources/2026-03-12_cccccccc-2/extract]] — inside a fence: skipped, neither an item nor malformed
```
- [[Sources/2026-03-12_cccccccc-2/extract]] — **valid** — the last good item
