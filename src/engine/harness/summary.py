"""The running conversation summary (Brief §10.3, Phase 5 Block 4):
what the summarizer is asked, and the guard that keeps figures out.

The property is *no figure from evidence*: an answer's numbers were
code-injected and verified this turn; a summary restating one would
be a second, unverified source the router could quote from. So the
scrub collects every numeric surface form from the prose answers being
folded (a table answer contributes its caption line only — its cells
never entered the history), allows the numbers the user typed (item
codes, dates, windows — the Brief's own "item 4471 in turn 12"), and
replaces any that reappears with "(see turn N)". A cited turn the
summary could not have seen is blanked to "(an earlier turn)" — never
rewritten to a number the summarizer did not write.

The figure grammar mirrors what render.py prints: money with the
symbol and thousands commas ($8,308.92, -$1,234.00), rates with one
decimal and a percent sign (92.2%), durations spelled out in a unit
word (1 hour, 1.1 days, 45 seconds), plain ints and floats, ISO dates.
It is anchored on digits with no letter, digit or underscore on either
side, so a code (CR147), an evidence ref (e0), an identifier
(rule_rate_variance) or a reading name never matches, and the em dash
a NULL renders as is never a figure. Pure code: no ports, no I/O.
"""

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

from engine.harness.state import HistoryTurn
from engine.ports.types import Message

_UNIT_WORDS = r"days?|hours?|minutes?|seconds?|percent"

FIGURE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:(?P<date>\d{4}-\d{2}-\d{2})"
    r"|(?P<sign>-?)(?P<symbol>\$?)(?P<digits>\d[\d,]*(?:\.\d+)?)(?P<percent>%?)"
    rf"(?P<unit>\s+(?:{_UNIT_WORDS}))?)"
    r"(?![A-Za-z0-9_])"
)

# "turn 3", "turns 3 and 5", "turns 2–4": every number inside is a
# turn reference, never a figure, and each is range-checked.
TURN_REF = re.compile(
    r"\bturns?\s+\d+(?:\s*(?:[-–—]|,|and|to)\s*\d+)*", re.IGNORECASE
)


class SummaryProblems(BaseModel):
    """What a summarizer reply got wrong: figures it restated (their
    surface forms, in order) and turn references outside 1..through."""

    model_config = ConfigDict(extra="forbid")

    figures: list[str] = []
    bad_refs: list[str] = []

    def any(self) -> bool:
        return bool(self.figures or self.bad_refs)


def figure_key(match: re.Match) -> str:
    """The comparison key of a matched figure: a date as itself, a
    number normalized so $8,308.92, 8,308.92 and 8308.92 agree."""
    if match.group("date"):
        return match.group("date")
    digits = match.group("digits").replace(",", "")
    try:
        value = Decimal(match.group("sign") + digits)
    except InvalidOperation:  # pragma: no cover - the regex admits digits only
        return digits
    return format(value.normalize(), "f")


def figure_set(records: list[HistoryTurn]) -> dict[str, int]:
    """Every figure the folded prose answers stated, keyed to the
    first turn that stated it, minus every number the user typed in
    any folded question."""
    figures: dict[str, int] = {}
    for record in sorted(records, key=lambda r: r.turn):
        if record.kind != "prose":
            continue
        for match in FIGURE.finditer(record.answer):
            figures.setdefault(figure_key(match), record.turn)
    for record in records:
        for match in FIGURE.finditer(record.question):
            figures.pop(figure_key(match), None)
    return figures


def _check_refs(text: str, through_turn: int) -> list[tuple[int, int, bool]]:
    spans = []
    for match in TURN_REF.finditer(text):
        numbers = [int(n) for n in re.findall(r"\d+", match.group())]
        valid = all(1 <= n <= through_turn for n in numbers)
        spans.append((match.start(), match.end(), valid))
    return spans


def _offending_figures(
    text: str, figures: dict[str, int], ref_spans: list[tuple[int, int, bool]]
) -> list[tuple[int, int, int]]:
    """(start, end, turn) per figure token that restates a folded
    answer's figure — tokens inside a turn reference excluded."""
    found = []
    for match in FIGURE.finditer(text):
        if any(start <= match.start() < end for start, end, _ in ref_spans):
            continue
        turn = figures.get(figure_key(match))
        if turn is not None:
            found.append((match.start(), match.end(), turn))
    return found


def summary_problems(
    text: str, records: list[HistoryTurn], through_turn: int
) -> SummaryProblems:
    figures = figure_set(records)
    ref_spans = _check_refs(text, through_turn)
    restated: list[str] = []
    for start, end, _ in _offending_figures(text, figures, ref_spans):
        surface = text[start:end]
        if surface not in restated:
            restated.append(surface)
    bad_refs = [text[start:end] for start, end, valid in ref_spans if not valid]
    return SummaryProblems(figures=restated, bad_refs=bad_refs)


def scrub_figures(
    text: str, records: list[HistoryTurn], through_turn: int
) -> tuple[str, int]:
    """The summary with every restated figure replaced by "(see turn
    N)" and every impossible turn reference by "(an earlier turn)", and
    how many replacements were made."""
    figures = figure_set(records)
    ref_spans = _check_refs(text, through_turn)
    edits: list[tuple[int, int, str]] = [
        (start, end, "(an earlier turn)")
        for start, end, valid in ref_spans
        if not valid
    ]
    edits.extend(
        (start, end, f"(see turn {turn})")
        for start, end, turn in _offending_figures(text, figures, ref_spans)
    )
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, len(edits)


def build_summary_messages(
    system_prompt: str,
    previous_summary: str,
    records: list[HistoryTurn],
    through_turn: int,
) -> list[Message]:
    """The summarizer's input: the previous summary and the turns to
    fold in, each labelled with its number and side."""
    lines = []
    if previous_summary:
        lines.append(f"Previous summary (through turn {through_turn - len(records)}):")
        lines.append(previous_summary)
    else:
        lines.append("Previous summary: (none yet)")
    lines.append("")
    lines.append("Turns to fold in:")
    for record in sorted(records, key=lambda r: r.turn):
        lines.append(f"Turn {record.turn} — user: {record.question}")
        lines.append(f"Turn {record.turn} — assistant: {record.answer}")
    lines.append("")
    lines.append(f"Write the updated summary through turn {through_turn}.")
    return [
        Message(role="system", content=system_prompt),
        Message(role="user", content="\n".join(lines)),
    ]
