"""capture.read_compare — the comparison of two readings of one image (rule `mnemon-read-compare/1`, capture
layer §21.5; the `comparison` object of the report, §21.7).

The reading is the model's text of the image, the witness text is the local recognizer's. The comparison
counts what the witness saw and the reading lacks (the loss, which can refuse the capture) and what the
reading holds and the witness lacks (the unconfirmed words and numbers, which are counted and never refused).

- A run is a maximal run of letters (`str.isalpha`: a Unicode general category that starts with `L`), folded:
  `str.lower`, then every small `l` written as `i`. A word is a folded run of three code points or more; the
  fold comes first, the length test after it. A number is `\\d+(?:[.,]\\d+)*` on the text as written.
- The hyphen repair runs in each text before the matching: two runs with nothing between them but one hyphen
  (`-`, U+00AD, U+2010), optional spaces or tabs, a line end and optional whitespace are a pair; a pair is joined
  when the other text holds the joined form as a word more often than this text does, both as written, counting
  the pairs already joined here. Left to right; a run joins one pair at most.
- Words are matched with multiplicity, in the order of the text; numbers the same way, apart from the words.
- The verdict, the first that matches: `no-confirmation` (the reading has a word and none was matched), `loss`
  (more than `allowance` witness words missing and `100 * found < min_share * witness_words`, exact), `agree`.

A pure function of two texts and two numbers: no file, no process, no clock. Stdlib only, python 3.9.
"""
from __future__ import annotations

import re
from collections import Counter
from fractions import Fraction
from typing import Dict, List, NamedTuple, Optional, Tuple

RULE = "mnemon-read-compare/1"
LIST_LIMIT = 200

AGREE = "agree"
LOSS = "loss"
NO_CONFIRMATION = "no-confirmation"
VERDICTS = (AGREE, LOSS, NO_CONFIRMATION)

WORD_MIN = 3                                    # code points of a folded run that make a word
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
HYPHENS = "-" + chr(0x00AD) + chr(0x2010)
# what may stand between the two runs of a pair: one hyphen, spaces or tabs, a line end, any whitespace
PAIR_GAP = re.compile("[" + re.escape(HYPHENS) + "][ \t]*(?:\r\n|\n|\r)\\s*")
BOM = chr(0xFEFF)


class Comparison(NamedTuple):
    verdict: str                    # agree | loss | no-confirmation
    witness_words: int
    found: int
    missing: int
    share: Optional[float]          # one decimal, rounded half to even; None when the witness text has no word
    reading_words: int
    unconfirmed_words: int
    reading_numbers: int
    unconfirmed_numbers: int
    hyphen_joined: int
    unconfirmed_word_list: List[str]     # as the reading spells them, in the reading's order, all of them
    unconfirmed_number_list: List[str]


class _Run(NamedTuple):
    folded: str
    spelling: str
    start: int
    end: int


def _fold(spelling: str) -> str:
    return spelling.lower().replace("l", "i")


def _runs(text: str) -> List[_Run]:
    """Every maximal run of letters of `text`, in order, with its fold and its place."""
    out = []
    start = None
    for index, char in enumerate(text):
        if char.isalpha():
            if start is None:
                start = index
        elif start is not None:
            out.append(_Run(_fold(text[start:index]), text[start:index], start, index))
            start = None
    if start is not None:
        out.append(_Run(_fold(text[start:]), text[start:], start, len(text)))
    return out


def _plain_words(runs: List[_Run]) -> Counter:
    """The words of a text as written, before any repair: folded runs of WORD_MIN code points or more."""
    return Counter(r.folded for r in runs if len(r.folded) >= WORD_MIN)


def _repair(text: str, runs: List[_Run], own: Counter, other: Counter) -> Tuple[List[Tuple[str, str]], int]:
    """The words of `text` after the hyphen repair: [(folded, spelling)] in the text's order, and the number of
    pairs joined. `own` and `other` are the two texts' words as written. A pair is joined while the other text
    holds its joined form more often than this text's own words and the pairs already joined here; the left pair
    is tried first, and a joined pair takes both its runs."""
    joined_here = Counter()
    words = []
    joined = 0
    k = 0
    while k < len(runs):
        a = runs[k]
        if k + 1 < len(runs):
            b = runs[k + 1]
            if PAIR_GAP.fullmatch(text, a.end, b.start):
                spelling = a.spelling + b.spelling
                form = _fold(spelling)
                if other[form] > own[form] + joined_here[form]:
                    joined_here[form] += 1
                    words.append((form, spelling))
                    joined += 1
                    k += 2
                    continue
        if len(a.folded) >= WORD_MIN:
            words.append((a.folded, a.spelling))
        k += 1
    return words, joined


def _unmatched(tokens: List[str], other: List[str]) -> List[int]:
    """The indexes of `tokens` left after matching with multiplicity against `other`, in order: the first
    occurrences of a token are the matched ones."""
    pool = Counter(other)
    left = []
    for index, token in enumerate(tokens):
        if pool[token] > 0:
            pool[token] -= 1
        else:
            left.append(index)
    return left


def _floor(min_share) -> Fraction:
    """`min_share` (int, float, str or Decimal) as an exact fraction of its decimal spelling: 89.95 is 1799/20,
    never the binary float next to it."""
    if isinstance(min_share, bool):
        raise TypeError("min_share must be a number, not a bool")
    try:
        return Fraction(str(min_share))
    except (ValueError, ZeroDivisionError):
        raise ValueError("min_share is not a finite number: %r" % (min_share,))


def _allowance(allowance) -> int:
    if isinstance(allowance, bool) or not isinstance(allowance, int):
        raise TypeError("allowance must be an int")
    return allowance


def _zero_text(text: str) -> bool:
    """The zero-text predicate of §21.6: Unicode whitespace (`str.isspace`) and a BOM count as nothing."""
    return all(char.isspace() or char == BOM for char in text)


def compare(reading: str, witness_text: str, *, min_share, allowance: int) -> Comparison:
    """The comparison of §21.5. A zero reading never reaches it (it is `no-text`, §21.6): ValueError."""
    if not isinstance(reading, str) or not isinstance(witness_text, str):
        raise TypeError("the reading and the witness text must be str")
    floor = _floor(min_share)
    allowance = _allowance(allowance)
    if _zero_text(reading):
        raise ValueError("a zero reading is no-text before the comparison")

    r_runs, w_runs = _runs(reading), _runs(witness_text)
    r_plain, w_plain = _plain_words(r_runs), _plain_words(w_runs)
    r_words, r_joined = _repair(reading, r_runs, r_plain, w_plain)
    w_words, w_joined = _repair(witness_text, w_runs, w_plain, r_plain)
    r_folded = [w[0] for w in r_words]
    w_folded = [w[0] for w in w_words]

    missing = len(_unmatched(w_folded, r_folded))
    unconfirmed_at = _unmatched(r_folded, w_folded)
    found = len(w_folded) - missing
    confirmed = len(r_folded) - len(unconfirmed_at)

    r_numbers, w_numbers = NUMBER.findall(reading), NUMBER.findall(witness_text)
    unconfirmed_numbers = [r_numbers[i] for i in _unmatched(r_numbers, w_numbers)]

    if r_folded and confirmed == 0:
        verdict = NO_CONFIRMATION
    elif missing > allowance and 100 * found < floor * len(w_folded):
        verdict = LOSS
    else:
        verdict = AGREE

    share = None
    if w_folded:
        # tenths of a percent, rounded half to even on the exact ratio of the integers
        share = round(Fraction(1000 * found, len(w_folded))) / 10

    return Comparison(
        verdict=verdict,
        witness_words=len(w_folded),
        found=found,
        missing=missing,
        share=share,
        reading_words=len(r_folded),
        unconfirmed_words=len(unconfirmed_at),
        reading_numbers=len(r_numbers),
        unconfirmed_numbers=len(unconfirmed_numbers),
        hyphen_joined=r_joined + w_joined,
        unconfirmed_word_list=[r_words[i][1] for i in unconfirmed_at],
        unconfirmed_number_list=unconfirmed_numbers,
    )


def as_report(c: Comparison, *, min_share, allowance: int) -> Dict[str, object]:
    """The `comparison` object of §21.7; each unconfirmed list cut to LIST_LIMIT, `truncated` when one was cut."""
    words = list(c.unconfirmed_word_list[:LIST_LIMIT])
    numbers = list(c.unconfirmed_number_list[:LIST_LIMIT])
    truncated = len(c.unconfirmed_word_list) > LIST_LIMIT or len(c.unconfirmed_number_list) > LIST_LIMIT
    return {
        "rule": RULE,
        "verdict": c.verdict,
        "floor": {"min_share": float(_floor(min_share)), "allowance": _allowance(allowance)},
        "witness_words": c.witness_words,
        "found": c.found,
        "missing": c.missing,
        "share": c.share,
        "reading_words": c.reading_words,
        "unconfirmed_words": c.unconfirmed_words,
        "reading_numbers": c.reading_numbers,
        "unconfirmed_numbers": c.unconfirmed_numbers,
        "hyphen_joined": c.hyphen_joined,
        "unconfirmed": {"words": words, "numbers": numbers, "truncated": truncated},
    }


def failure_of(c: Comparison) -> Optional[Dict[str, object]]:
    """None for `agree`; the `reading-mismatch` failure of §21.7 for the two refusals."""
    if c.verdict == AGREE:
        return None
    if c.verdict == LOSS:
        return {"class": "reading-mismatch", "acquisition": "words-missing", "reason": "loss", "retryable": False}
    if c.verdict == NO_CONFIRMATION:
        return {"class": "reading-mismatch", "acquisition": "no-confirmation", "reason": "no-confirmation",
                "retryable": False}
    raise ValueError("unknown verdict: %r" % (c.verdict,))
