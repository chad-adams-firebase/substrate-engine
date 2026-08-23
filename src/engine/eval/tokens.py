"""Emitted-token detection and answer-text extraction.

The gate verdict's §7.2 requirement: N9/N10-class failures fire only
when a draft happens to state a file path or a prose date, so every
run records which optional tokens its draft emitted — grade then
stratifies pass-rates by them, making coin-flips visible instead of
letting green runs mask them.

Shared by runner (record) and grader (assert): one set of regexes,
one answer-flattening rule, so the two halves can never disagree
about what "the answer said".
"""

import re

from engine.eval.models import EmittedTokens
from engine.harness.outcomes import TurnOutcome

LINE_NUMBERS = re.compile(r"\blines?\s+\d+(?:\s*[–—-]\s*\d+)?", re.IGNORECASE)
FILE_PATHS = re.compile(r"\b[\w./-]*\w\.(?:py|md|ya?ml|json|sql|txt)\b")
ISO_DATES = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
PROSE_DATES = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?"
    r"|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}\b"
)
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
FLOAT_TAILS = re.compile(r"\b\d+\.\d{3,}\b")
BACKTICKED = re.compile(r"`([^`\n]+)`")

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
_WORD_NUMBER = re.compile(
    r"\b(" + "|".join(WORD_NUMBERS) + r")\b", re.IGNORECASE
)
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def detect(text: str) -> EmittedTokens:
    return EmittedTokens(
        line_numbers=LINE_NUMBERS.findall(text),
        file_paths=FILE_PATHS.findall(text),
        iso_dates=ISO_DATES.findall(text),
        prose_dates=PROSE_DATES.findall(text),
        money=MONEY.findall(text),
        float_tails=FLOAT_TAILS.findall(text),
        word_numbers=[m.group(1) for m in _WORD_NUMBER.finditer(text)],
        backticked=BACKTICKED.findall(text),
    )


def flatten_answer(outcome: TurnOutcome | None) -> str:
    """The answer as one searchable text: markdown as-is; tables as
    their cells plus caption (numbers travel store to screen, so cells
    ARE the answer); refuse/clarify/escalate as their message text."""
    if outcome is None:
        return ""
    if outcome.kind == "answer":
        if outcome.body.kind == "markdown":
            return outcome.body.text
        table = outcome.body.table
        cells = "\n".join(
            " ".join("" if v is None else str(v) for v in row.values())
            for row in table.rows
        )
        header = " ".join(table.columns)
        return "\n".join(part for part in (header, cells, outcome.body.caption) if part)
    if outcome.kind == "refuse":
        return f"{outcome.reason}\n{outcome.what_would_work}".strip()
    if outcome.kind == "clarify":
        return outcome.question
    return outcome.reason


def extract_numbers(text: str) -> list[float]:
    """Every numeric the prose states: digit groups ($, %, commas
    stripped) plus word-numbers one..twenty — the comparison pool for
    numeric_from_gold."""
    values = []
    for match in _NUMBER.finditer(text):
        cleaned = match.group().lstrip("$").rstrip("%").replace(",", "")
        try:
            values.append(float(cleaned))
        except ValueError:  # pragma: no cover - regex admits digits only
            continue
    values.extend(
        float(WORD_NUMBERS[m.group(1).lower()])
        for m in _WORD_NUMBER.finditer(text)
    )
    return values
