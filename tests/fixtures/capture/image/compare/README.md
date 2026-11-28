# Comparison fixtures

One JSON file per rule and edge of the comparison of two readings of one image (rule `mnemon-read-compare/2`, capture layer spec §22.2 over §21.5; the lists are §21.13 «Comparison fixtures» and the new fixtures of §22.6). `tests/capture_layer/test_read_compare.py` reads every file here.

Each file holds `name` (the file's stem), `what` (the case, and how its numbers follow from the rule), `reading`, `witness`, `min_share`, `allowance` and `expected`: the whole `comparison` object of §22.3 that `as_report(compare(reading, witness, …), …)` must return, in the key order of its example — every count, the share, the verdict, the floor and the two list objects, `missing_lists` (what the witness read and the reading lacks) and `unconfirmed` (what the reading holds and the witness lacks). For the two fixtures of the edge row «an empty or whitespace reading», `expected` is `null` and `raises` names the error: such a reading is `no-text` before the comparison (§21.6), and the comparison refuses to run on it.

The expected values are derived by hand from the rule's text, never from the code, and are never regenerated from the code. The texts are synthetic sentences. The larger cases use a small vocabulary of repeated lines, so you can check each count by reading the fixture. A non-ASCII character that is not a letter is written as a `\u` escape, so that you can see a soft hyphen, a BOM or a superscript.

## Rule `/2` over rule `/1`

Rule `/2` gives every count of rule `/1` for the same two texts and the same floor (§22.2). The fixtures of rule `/1` were moved to rule `/2` without a change to any of their `/1` values: `rule` became `mnemon-read-compare/2`, the count of the witness's missing words moved from `missing` to `missing_words` with the value it had, and three keys were added — `witness_numbers`, `missing_numbers` and the object `missing_lists`. The unchanged `/1` values are the proof that rule `/2` is rule `/1` plus three additions.

How the new values were derived:

- **The numbers.** `witness_numbers` counts the token `\d+(?:[.,]\d+)*` on the witness text as written. `missing_numbers` and `missing_lists.numbers` are the witness's numbers left after matching them, with multiplicity and in the witness's order, against the reading's numbers. A throwaway script written apart from the code listed them, and each list was read against the fixture's two texts. Each fixture also satisfies `witness_numbers − missing_numbers = reading_numbers − unconfirmed_numbers`.
- **The missing words.** `missing_lists.words` holds the witness's words left unmatched, as the witness text spells them, in its order. For the texts without a hyphen pair the same throwaway script listed them; for the fixtures with a hyphen pair they were derived by hand from the repair of §21.5. Each list was read against the `what` of its fixture, and its length is the `/1` count of missing words.
- **The fixtures of §22.6** (`numbers-*`, `missing-words-spelled`) were written for rule `/2`; their `what` derives every value. `number-separator` is the row «a number with and without its separator, counted in both directions».

`missing-words-spelled` holds a joined pair among the missing words. A joined pair can be missing only this way: the witness's pair is joined against a whole word of the reading, and the reading's own repair then takes that word into a pair of its own. A joined pair is spelled as its two halves written together.

`hyphen-left-not-joined` records a reading of the spec's sentence «the pair on the left comes first» that the spec does not state in so many words. When the left pair is not joined, its right run can join the next pair. Change that fixture together with the spec if the maintainer rules otherwise.
