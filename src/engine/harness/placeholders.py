"""The {{eN.path}} placeholder grammar and its resolver.

Drafts reference evidence values as placeholders — {{e0.table.rows[0]
.invoice_count}} — and this resolver injects the actual values in
code, deterministically. The LLM never types the number (Brief §9.4);
what it types anyway, the Verifier catches. Resolution failures are
returned, not raised: the drafter retries with them as feedback.

Values, not passages (Phase 5 Block 2): a placeholder that resolves to
a whole description, a document snippet, or a block of source is a
passage, and a passage pasted mid-sentence reads as a lumpy seam or a
mid-word cut. Under an inline limit, a passage resolves only inside a
fenced code block; anywhere else it is reported as misplaced, and the
drafter retries told to quote it fenced or say it in its own words.

Pure code, heavily unit-tested: no ports, no I/O.
"""

import json
import re

from pydantic import BaseModel, ConfigDict

from engine.harness.render import format_cell
from engine.tools.envelope import ColumnFormat, ToolInvocation
from engine.verifier.models import InjectedSpan

_PLACEHOLDER = re.compile(r"\{\{e(\d+)\.([^{}]+)\}\}")
_SEGMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)((?:\[\d+\])*)$")
_INDEXES = re.compile(r"\[(\d+)\]")
# A fenced code block in the draft (an unclosed fence runs to the
# end): the one place a passage-valued placeholder may sit.
_FENCE = re.compile(r"```[^\n]*\n.*?(?:```|\Z)", re.DOTALL)


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    # Spans of injected values in the resolved text, each carrying the
    # evidence path it resolved from — the Verifier treats claims
    # inside them as verified by construction.
    injected_spans: list[InjectedSpan] = []
    # Placeholders that did not resolve, verbatim, for retry feedback.
    failures: list[str] = []
    # Placeholders that resolved to a passage — multi-line, or longer
    # than the inline limit — outside a fenced code block. Left
    # verbatim like a failure; the drafter retries with the
    # values-not-passages feedback (Block 2's text-block guard).
    misplaced: list[str] = []


def referenced_indices(surfaces: list[str]) -> list[int]:
    """Evidence indices named by placeholder surfaces, one per
    occurrence, in order — what the drafter was trying to cite when
    resolution failed."""
    return [
        int(match.group(1))
        for surface in surfaces
        for match in _PLACEHOLDER.finditer(surface)
    ]


def _candidates(path: str) -> tuple[str, ...]:
    """The paths a surface may mean, in trust order: as written, then —
    because render_evidence nests each tool result under "output" and
    drafters believe the JSON they see over the prompt's examples —
    the same path with that wrapper segment stripped once. As-written
    wins, so a genuine output field, should one ever exist, still
    resolves."""
    if path.startswith("output."):
        return (path, path[len("output.") :])
    return (path,)


def _navigate(value: object, path: str) -> object:
    """Walk a dot/bracket path into a model_dump(mode="json") tree.
    Raises KeyError/IndexError/TypeError on a bad step — the caller
    turns any of those into a resolution failure."""
    for segment in path.split("."):
        match = _SEGMENT.match(segment)
        if match is None:
            raise KeyError(segment)
        name, brackets = match.group(1), match.group(2)
        if not isinstance(value, dict):
            raise TypeError(segment)
        value = value[name]
        for index in _INDEXES.findall(brackets):
            if not isinstance(value, list):
                raise TypeError(segment)
            value = value[int(index)]
    return value


def _render(value: object, column_format: ColumnFormat | None = None) -> str | None:
    """A scalar as prose text; None for non-scalars (a placeholder
    must name one value, not a structure)."""
    if value is None:
        return json.dumps(value)  # "null", visibly
    if isinstance(value, (str, bool, int, float)):
        # ints plain, floats shortest-round-trip, booleans lowercase —
        # unless the column carries a display hint (§10.5): a money
        # cell reads $8,308.92 in prose exactly as it does in a table.
        return format_cell(value, column_format)
    return None


def _column_format(tree: object, path: str) -> ColumnFormat | None:
    """The display hint for a path into a table cell — table.rows[i]
    .<column> against the table's column_formats — else None."""
    segments = path.split(".")
    if len(segments) != 3 or not segments[1].startswith("rows["):
        return None
    if not isinstance(tree, dict):
        return None
    table = tree.get(segments[0])
    if not isinstance(table, dict):
        return None
    formats = table.get("column_formats") or {}
    hint = formats.get(segments[2])
    return ColumnFormat.model_validate(hint) if hint else None


def is_passage(rendered: str, inline_value_max_chars: int) -> bool:
    """A value is one line and short; a passage is anything else."""
    return "\n" in rendered or len(rendered) > inline_value_max_chars


def _fenced_ranges(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _FENCE.finditer(text)]


def resolve_placeholders(
    text: str,
    evidence: list[ToolInvocation],
    *,
    inline_value_max_chars: int | None = None,
    allow_passages_inline: bool = False,
) -> Resolution:
    """Inject every placeholder's value. With an inline limit set, a
    passage-valued placeholder outside a fenced code block is reported
    as misplaced instead of injected — unless allow_passages_inline,
    the exhaustion path, where it ships as written."""
    parts: list[str] = []
    spans: list[InjectedSpan] = []
    failures: list[str] = []
    misplaced: list[str] = []
    fences = _fenced_ranges(text)
    length = 0
    cursor = 0

    for match in _PLACEHOLDER.finditer(text):
        parts.append(text[cursor : match.start()])
        length += match.start() - cursor
        cursor = match.end()

        surface = match.group(0)
        index = int(match.group(1))
        path = match.group(2)
        rendered: str | None = None
        effective = path
        if 0 <= index < len(evidence) and evidence[index].output is not None:
            tree = evidence[index].output.model_dump(mode="json")
            for candidate in _candidates(path):
                try:
                    rendered = _render(
                        _navigate(tree, candidate),
                        _column_format(tree, candidate),
                    )
                except (KeyError, IndexError, TypeError, ValueError):
                    rendered = None
                if rendered is not None:
                    effective = candidate
                    break

        if rendered is None:
            failures.append(surface)
            parts.append(surface)  # leave it visible; the draft retries
            length += len(surface)
        elif (
            inline_value_max_chars is not None
            and not allow_passages_inline
            and is_passage(rendered, inline_value_max_chars)
            and not any(start <= match.start() < end for start, end in fences)
        ):
            misplaced.append(surface)
            parts.append(surface)  # verbatim, like a failure
            length += len(surface)
        else:
            parts.append(rendered)
            spans.append(
                InjectedSpan(
                    start=length,
                    end=length + len(rendered),
                    ref=f"e{index}.{effective}",
                )
            )
            length += len(rendered)

    parts.append(text[cursor:])
    return Resolution(
        text="".join(parts),
        injected_spans=spans,
        failures=failures,
        misplaced=misplaced,
    )
