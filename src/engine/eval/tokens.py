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
from engine.harness.outcomes import TurnOutcome, about_line, reading_line
from engine.harness.render import format_cell
from engine.tools.durations import UNIT_SECONDS, parse_clock

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
# A stated duration: digits or a word-number, then a unit word the
# humanizer prints ("1 hour", "60 minutes", "1.1 days", "twelve
# seconds"); and an H:MM:SS clock string standing alone.
_DURATION_PHRASE = re.compile(
    r"(-?)\b(\d[\d,]*(?:\.\d+)?|" + "|".join(WORD_NUMBERS) + r")"
    r"\s+(second|minute|hour|day)s?\b",
    re.IGNORECASE,
)
_CLOCK_TEXT = re.compile(r"(?<![\d:])(\d+:[0-5]\d:[0-5]\d(?:\.\d+)?)(?![\d:])")


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


def answer_envelope(outcome: TurnOutcome | None) -> str:
    """What shape the answer took — "markdown", "table", or the
    non-answer outcome kind — so a grade detail line can say which
    text it read (a string-valued table has no numerics, honestly)."""
    if outcome is None:
        return "none"
    if outcome.kind == "answer":
        return outcome.body.kind
    return outcome.kind


def answer_body(outcome: TurnOutcome | None) -> str:
    """The answer proper, without a table's caption: markdown as-is;
    tables as their header and cells (numbers travel store to screen,
    so cells ARE the answer); refuse/clarify/escalate as their message
    text. This is the numeric pool for numeric_from_gold — the caption
    is the SQL that produced the table, and its literals (a year, a
    LIMIT, a 0 in a CASE) are not values the answer stated."""
    if outcome is None:
        return ""
    if outcome.kind == "answer":
        if outcome.body.kind == "markdown":
            return outcome.body.text
        table = outcome.body.table
        cells = "\n".join(
            " ".join(
                format_cell(row.get(column), table.column_formats.get(column))
                for column in table.columns
            )
            for row in table.rows
        )
        header = " ".join(table.columns)
        return "\n".join(part for part in (header, cells) if part)
    if outcome.kind == "refuse":
        return f"{outcome.reason}\n{outcome.what_would_work}".strip()
    if outcome.kind == "clarify":
        return outcome.question
    return outcome.reason


def answer_caption(outcome: TurnOutcome | None) -> str:
    """The answer's caption as the surfaces show it: the About line the
    answer declared (Backlog Pass — on either shape), then, for a
    table, the reading line it named (Close Pass) and the SQL — the
    pool a `contains` on an entity or a reading name reads."""
    if outcome is not None and outcome.kind == "answer":
        body = outcome.body
        if body.kind == "table":
            parts = (about_line(body), reading_line(body), body.caption)
        else:
            parts = (about_line(body),)
        return "\n".join(part for part in parts if part)
    return ""


def flatten_answer(outcome: TurnOutcome | None) -> str:
    """The answer as one searchable text: the body plus, for tables,
    the caption — the pool for pattern assertions and the dump guard,
    where the SQL shown to the user is part of what was said."""
    body = answer_body(outcome)
    caption = answer_caption(outcome)
    return "\n".join(part for part in (body, caption) if part)


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


def extract_durations(text: str) -> list[tuple[float, float]]:
    """(seconds, tolerance) for every duration the prose states — the
    comparison pool for a numeric_from_gold assertion that declares a
    unit (duration pass). The tolerance is half a displayed decimal in
    the phrase's own unit, since the humanizer prints one decimal
    ("1.1 days" covers 1.05–1.15 days); a clock string is exact to
    half a second's tenth."""
    durations: list[tuple[float, float]] = []
    for match in _DURATION_PHRASE.finditer(text):
        sign, amount, unit = match.groups()
        lowered = amount.lower()
        if lowered in WORD_NUMBERS:
            count = float(WORD_NUMBERS[lowered])
        else:
            count = float(amount.replace(",", ""))
        size = UNIT_SECONDS[f"{unit.lower()}s"]  # type: ignore[index]
        seconds = count * size
        durations.append((-seconds if sign else seconds, 0.05 * size))
    for match in _CLOCK_TEXT.finditer(text):
        seconds = parse_clock(match.group(1))
        if seconds is not None:
            durations.append((seconds, 0.05))
    return durations
