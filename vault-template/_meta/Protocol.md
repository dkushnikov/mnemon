# Mnemon Vault Protocol

## Folder Naming

`Sources/YYYY-MM-DD_{hash8}/` where:
- Date = capture date
- hash8 = first 8 characters of SHA-256 hash
  - For URLs: hash of canonical URL
  - For non-URL: hash of `{title}_{created_date}`
- Collision: append `-2`, `-3`, etc.

## Status Lifecycle

```
captured → extracted → integrated
```

- **captured**: source.md exists, extract.md missing or failed
- **extracted**: both source.md and extract.md exist with full content
- **integrated**: user has reviewed, connected to other knowledge, or created Synthesis

## Immutability

- source.md: NEVER modified after creation. Contains raw captured content.
- extract.md: CAN be overwritten by re-extraction (with updated reader context).
- Re-capture of same URL: new folder (different date = different hash input? No — same URL = same hash. Use collision handling.)

## Domains

Default domains (Life Capital framework):
- `learning` — what changes how I think
- `health` — evidence-based optimization
- `relationships` — social capital, peers, network
- `home` — environment, space, routines
- `finance` — wealth, investment, tax
- `career` — professional growth, leadership
- `culture` — joy, travel, hobbies
- `influence` — personal brand, community
- `inner-work` — coaching, self-awareness

Users can add custom domains in reader-context.md.
