"""The {{eN.path}} placeholder grammar and its resolver.

Drafts reference evidence values as placeholders — {{e0.table.rows[0]
.invoice_count}} — and this resolver injects the actual values in
code, deterministically. The LLM never types the number (Brief §9.4);
what it types anyway, the Verifier catches. Resolution failures are
returned, not raised: the drafter retries with them as feedback.

Pure code, heavily unit-tested: no ports, no I/O.
"""

import json
import re

from pydantic import BaseModel, ConfigDict

from engine.tools.envelope import ToolInvocation

_PLACEHOLDER = re.compile(r"\{\{e(\d+)\.([^{}]+)\}\}")
_SEGMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)((?:\[\d+\])*)$")
_INDEXES = re.compile(r"\[(\d+)\]")


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    # Char spans of injected values in the resolved text — the
    # Verifier marks claims inside them injected=True.
    injected_spans: list[tuple[int, int]] = []
    # Placeholders that did not resolve, verbatim, for retry feedback.
    failures: list[str] = []


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


def _render(value: object) -> str | None:
    """A scalar as prose text; None for non-scalars (a placeholder
    must name one value, not a structure)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)) or value is None:
        # json.dumps renders ints plain, floats shortest-round-trip,
        # booleans lowercase, null for None.
        return json.dumps(value)
    return None


def resolve_placeholders(
    text: str, evidence: list[ToolInvocation]
) -> Resolution:
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    failures: list[str] = []
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
        if 0 <= index < len(evidence) and evidence[index].output is not None:
            tree = evidence[index].output.model_dump(mode="json")
            for candidate in _candidates(path):
                try:
                    rendered = _render(_navigate(tree, candidate))
                except (KeyError, IndexError, TypeError, ValueError):
                    rendered = None
                if rendered is not None:
                    break

        if rendered is None:
            failures.append(surface)
            parts.append(surface)  # leave it visible; the draft retries
            length += len(surface)
        else:
            parts.append(rendered)
            spans.append((length, length + len(rendered)))
            length += len(rendered)

    parts.append(text[cursor:])
    return Resolution(
        text="".join(parts), injected_spans=spans, failures=failures
    )
