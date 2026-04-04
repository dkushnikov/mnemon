# Status Lifecycle

Sources progress through three statuses:

```
captured → extracted → integrated
```

## States

### captured
- source.md exists with raw content
- extract.md is missing or contains an error
- Gateway failed during extraction, or extraction was deferred

### extracted
- Both source.md and extract.md exist
- extract.md has full structured content (Summary, Executive Summary, Key Ideas, etc.)
- This is the default end state for successful `/source-add`

### integrated
- User has reviewed the extract
- Connected to other knowledge (Synthesis notes, links, applied in work)
- Manually set by the user — never auto-promoted

## Transitions

| From | To | Trigger |
|------|----|---------|
| (new) | captured | source.md created, extraction fails |
| (new) | extracted | source.md + extract.md both created successfully |
| captured | extracted | Re-extraction succeeds (`/source-extract`) |
| extracted | integrated | User manually marks as integrated |

## Convention, Not Enforcement

Status is a frontmatter field, not a database constraint. There is no state machine enforcement. The status is a convention that helps users track their knowledge processing pipeline.
