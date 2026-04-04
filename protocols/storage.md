# Storage Protocol

## Folder Structure

```
Sources/
├── 2026-04-04_a1b2c3d4/
│   ├── source.md     (immutable)
│   └── extract.md    (mutable)
├── 2026-04-04_e5f6g7h8/
│   ├── source.md
│   └── extract.md
└── ...
```

## Folder Naming

Format: `YYYY-MM-DD_{hash8}`

- **Date**: capture date (ISO format)
- **Hash**: first 8 characters of SHA-256
  - URL sources: `echo -n "<canonical-url>" | shasum -a 256 | cut -c1-8`
  - Non-URL sources: `echo -n "<title>_<YYYY-MM-DD>" | shasum -a 256 | cut -c1-8`

## Collision Handling

If `Sources/2026-04-04_a1b2c3d4/` exists, append `-2`, `-3`, etc.:
- `Sources/2026-04-04_a1b2c3d4-2/`
- `Sources/2026-04-04_a1b2c3d4-3/`

## Immutability

- `source.md`: NEVER modified after creation. Raw captured content.
- `extract.md`: CAN be overwritten by re-extraction (e.g., with updated reader context).
- Folders: NEVER renamed or moved. The hash is permanent.

## Other Directories

- `Synthesis/`: human-written notes connecting ideas across sources. Never auto-generated.
- `_meta/`: vault protocol and configuration.
- `_inputs/pending-writes/`: failed gateway writes, queued for retry.
