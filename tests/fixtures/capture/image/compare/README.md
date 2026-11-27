# Comparison fixtures

One JSON file per rule and edge of the comparison of two readings of one image (rule `mnemon-read-compare/1`, capture layer spec §21.5; the list is §21.13 «Comparison fixtures»). `tests/capture_layer/test_read_compare.py` reads every file here.

Each file holds `name` (the file's stem), `what` (the case, and how its numbers follow from the rule), `reading`, `witness`, `min_share`, `allowance` and `expected`: the whole `comparison` object of §21.7 that `as_report(compare(reading, witness, …), …)` must return, every count, the share, the verdict, the floor and the two unconfirmed lists. For the two fixtures of the edge row «an empty or whitespace reading», `expected` is `null` and `raises` names the error: such a reading is `no-text` before the comparison (§21.6), and the comparison refuses to run on it.

The expected values are derived by hand from the rule of §21.5, never from the code, and are never regenerated from the code. The texts are synthetic sentences. The larger cases use a small vocabulary of repeated lines, so you can check each count by reading the fixture. A non-ASCII character that is not a letter is written as a `\u` escape, so that you can see a soft hyphen, a BOM or a superscript.

`hyphen-left-not-joined` records a reading of the spec's sentence «the pair on the left comes first» that the spec does not state in so many words. When the left pair is not joined, its right run can join the next pair. Change that fixture together with the spec if the maintainer rules otherwise.
