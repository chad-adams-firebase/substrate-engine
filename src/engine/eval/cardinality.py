"""Declared cardinalities, executed against the world (Close Pass).

A join path's one_to_one_when says the path is one row per key under
a filter — a lifecycle fact (an invoice reaches a terminal status
once, is received once) the schema does not constrain. The fan-out
lint vouches for a statement on the strength of it, so the declaration
must stay true as the world changes: `engine eval grade --check-gold`
executes every declared condition here, beside the gold scripts, and a
condition the world contradicts — or one no row satisfies, which is
how a misspelled value reads — is rot. The check reads the map and
never a table name of its own, so a production pack's lifecycle tables
get the same tripwire by declaring, not by writing.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.eval.world import World
from engine.substrates.models import DictionaryMap
from engine.substrates.pack_data import load_dictionary_map


class CardinalityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    column: str
    values: list[str]
    key: str  # table.column the rows are counted per
    status: Literal["ok", "rot", "error"]
    matched_rows: int = 0
    max_per_key: int = 0
    detail: str = ""

    def describe(self) -> str:
        return (
            f"{self.column} IN ({', '.join(self.values)}): "
            f"at most one row per {self.key}"
        )


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def check_declared_cardinalities(
    dictionary_map: DictionaryMap, world: World
) -> list[CardinalityCheck]:
    """One check per declared condition: under the filter, the rows of
    the condition's table grouped by the path's key column on that
    table must number at most one per key, and at least one in all."""
    checks: list[CardinalityCheck] = []
    for path in dictionary_map.join_paths:
        for condition in path.one_to_one_when:
            table, column = condition.table, condition.column_name
            keys = [
                step.from_column if step.from_table == table else step.to_column
                for step in path.steps
                if table in (step.from_table, step.to_table)
            ]
            fields = dict(
                path=path.name,
                column=condition.column,
                values=list(condition.values),
                key=f"{table}.{keys[0]}" if keys else condition.column,
            )
            if not keys:
                checks.append(
                    CardinalityCheck(
                        **fields,
                        status="error",
                        detail="the condition's table is not on the path's steps",
                    )
                )
                continue
            literals = ", ".join(_literal(value) for value in condition.values)
            query = (
                "SELECT COALESCE(SUM(n), 0) AS matched_rows, "
                "COALESCE(MAX(n), 0) AS max_per_key FROM ("
                f"SELECT {keys[0]} AS k, COUNT(*) AS n FROM {table} "
                f"WHERE {column} IN ({literals}) GROUP BY {keys[0]})"
            )
            try:
                (row,) = world.sql(query)
            except Exception as exc:  # the database names its own errors
                checks.append(
                    CardinalityCheck(**fields, status="error", detail=f"{exc}")
                )
                continue
            matched, max_per_key = int(row["matched_rows"]), int(row["max_per_key"])
            if matched == 0:
                status, detail = "rot", "no row matches the declared values"
            elif max_per_key > 1:
                status, detail = "rot", f"{max_per_key} rows share one {fields['key']}"
            else:
                status, detail = "ok", ""
            checks.append(
                CardinalityCheck(
                    **fields,
                    status=status,
                    matched_rows=matched,
                    max_per_key=max_per_key,
                    detail=detail,
                )
            )
    return checks


def check_pack_cardinalities(pack_dir: Path, world: World) -> list[CardinalityCheck]:
    """The pack's declarations against the world; nothing when the
    pack has no Dictionary Map."""
    path = Path(pack_dir) / "dictionary_map.yaml"
    if not path.is_file():
        return []
    return check_declared_cardinalities(load_dictionary_map(path), world)
