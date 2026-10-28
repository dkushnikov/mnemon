---
type: wiki
kind: concept                      # concept | comparison | contradiction | open-question — must equal inputs.kind
collections: ["[[Collections/<dossier>]]"]   # the supporting dossiers: their open status and veto govern this page
domains: []                        # the union of the supporting dossiers' domains
based_on:                          # print: the revisions the evidential body rests on
  - rev: "[[Sources/YYYY-MM-DD_hash8/extract]]"
    text_sha256: "<sha256 of the revision's source.md body>"
    seal: capture                  # capture = equals capture.text_sha256 of a managed revision; observed = the body hash of a legacy one
inputs:                            # print: the build package
  page: "<this file's name without .md>"
  kind: concept
  collections: {"Collections/<dossier>": "<sha256 of the dossier file>"}
  charter_sha256: "<sha256 of _meta/charter.md, omitted without a charter>"
  protocol: "research/1"
  template: "wiki/concept@1"
maintained_by: <agent>
created: {{date}}
updated: {{date}}
body_sha256: "<sha256 of the evidential body: from the first heading to ## Atlas reading / ## Log>"
visibility: personal
---

# {{title}}

## State of evidence

- <a claim> — [[Sources/YYYY-MM-DD_hash8/source#<heading that exists once in source.md>]]
- <a claim backed by a quote>:
  > "<an exact fragment of source.md>" — [[Sources/YYYY-MM-DD_hash8/source]]

## Positions

<`comparison` only: one row per position, each with its locator>

## Contradictions

<`contradiction` only: both positions and the conditions under which each holds; no invented consensus; a later date proves nothing>

## Open questions

- <what the evidence does not settle; a claim taken from an extract without a locator in source.md stays here>

## Atlas reading

<the agent's position, dated — an opinion, never evidence: outside body_sha256, cut from search snippets, omitted on recusal>

## Log

- {{date}} — built from [[Collections/<dossier>]] on <n> revisions
