"""Canonical JSONL serialization — the byte-stability chokepoint.

Idempotent regeneration (Brief §13, phasing Phase 2 "done") is
enforced here and only here: every substrate file is written as one
JSON object per line, keys sorted, compact separators, ASCII-escaped,
LF line endings, trailing newline, rows sorted by the substrate's
documented natural key. Two runs over the same inputs must produce
byte-identical files; any formatting decision made elsewhere would
be a second place for that promise to break.
"""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

# Natural-key sort orders per substrate model (documented in the
# Phase 2 plan; tests pin them). Callers pass the matching key.
SortKey = Callable[[BaseModel], tuple]


def dumps_row(row: BaseModel) -> str:
    """One canonical JSON line (no trailing newline)."""
    payload = row.model_dump(mode="json")
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def write_rows(path: Path, rows: Sequence[BaseModel], sort_key: SortKey) -> None:
    """Write rows canonically. Sorting happens here so callers cannot
    accidentally ship generation order (which is not stable)."""
    ordered = sorted(rows, key=sort_key)
    text = "".join(dumps_row(row) + "\n" for row in ordered)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_rows(path: Path, model: type[M]) -> list[M]:
    """Read a substrate file back into validated models. Blank lines
    are rejected, not skipped: a blank line in a generated file means
    something else wrote it."""
    rows: list[M] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            raise ValueError(f"{path}:{number}: blank line in substrate file")
        rows.append(model.model_validate_json(line))
    return rows
