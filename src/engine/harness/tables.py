"""Table projections for data-shaped answers (Brief §6, §10.5).

run_sql already carries a Table; stats and dictionary outputs are
lists of typed rows, projected here so they too can travel store to
screen untouched by the model. Non-scalar fields flatten to compact
text; provenance stays out (the turn log carries it — a Table cell is
for reading, not auditing).
"""

import json

from engine.tools.envelope import (
    DictionaryLookupOutput,
    RunSqlOutput,
    StatsOutput,
    Table,
    ToolOutput,
)

_DROPPED_FIELDS = {"provenance"}


def _scalar_rows(dumped_rows: list[dict]) -> list[dict]:
    projected = []
    for row in dumped_rows:
        cells = {}
        for key, value in row.items():
            if key in _DROPPED_FIELDS:
                continue
            if isinstance(value, (dict, list)):
                cells[key] = json.dumps(value, separators=(",", ":"))
            else:
                cells[key] = value
        projected.append(cells)
    return projected


def project_table(output: ToolOutput) -> Table | None:
    """The output as a table envelope, or None if it is not
    table-shaped — the router gets that fed back and re-decides."""
    if isinstance(output, RunSqlOutput):
        return output.table
    if isinstance(output, (StatsOutput, DictionaryLookupOutput)):
        dumped = [row.model_dump(mode="json") for row in output.rows]
        rows = _scalar_rows(dumped)
        columns = list(rows[0].keys()) if rows else []
        return Table(
            columns=columns, rows=rows, total_row_count=len(rows)
        )
    return None


def caption_for(output: ToolOutput) -> str:
    if isinstance(output, RunSqlOutput):
        return output.sql
    return ""


def declared_readings(output: ToolOutput) -> list[str]:
    """The reading names a result declares — the closed vocabulary
    give_answer(shape='table', reading=...) is validated against."""
    if isinstance(output, RunSqlOutput):
        return [interpretation.name for interpretation in output.readings]
    return []
