"""Execute gold scripts and detect bank rot.

A gold script is a committed executable artifact exposing
`gold(world) -> dict` (JSON-serializable, string keys). Grade always
compares answers against the EXECUTED value; the row's committed
expected_gold is a tripwire only — a mismatch between the two is
bank rot, and a rotten row grades nothing, because neither the bank
author nor the engine can be trusted about it until a human looks.

The dict return is a deliberate, contained exception to the
no-naked-dicts law: gold scripts are bank data authored beside YAML
rows, not engine modules, and their keys are named by each row's own
assertions.
"""

import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from engine.eval.bank import LoadedBank
from engine.eval.world import World


class GoldError(Exception):
    """A gold script could not be executed; the message names the
    script and the failure."""


def run_gold(script_path: Path, world: World) -> dict[str, Any]:
    if not script_path.is_file():
        raise GoldError(f"gold script not found: {script_path}")
    spec = importlib.util.spec_from_file_location(
        f"eval_gold_{script_path.stem}", script_path
    )
    if spec is None or spec.loader is None:
        raise GoldError(f"gold script not importable: {script_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise GoldError(f"{script_path} failed to import: {exc}") from exc
    gold = getattr(module, "gold", None)
    if not callable(gold):
        raise GoldError(f"{script_path} defines no gold(world) function")
    try:
        result = gold(world)
    except Exception as exc:
        raise GoldError(f"{script_path} raised: {exc}") from exc
    if not isinstance(result, dict) or not all(
        isinstance(key, str) for key in result
    ):
        raise GoldError(
            f"{script_path} must return a dict with string keys, got "
            f"{type(result).__name__}"
        )
    try:
        json.dumps(result)
    except (TypeError, ValueError) as exc:
        raise GoldError(
            f"{script_path} returned non-JSON-serializable values: {exc}"
        ) from exc
    return result


class GoldCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    script: str
    status: Literal["ok", "rot", "error"]
    mismatches: list[str] = []
    error: str | None = None


def compare_expected(
    expected: dict[str, Any], executed: dict[str, Any]
) -> list[str]:
    """Every committed key must match its executed value; the script
    may return extra keys (working values assertions reference but the
    row does not commit)."""
    mismatches = []
    for key, want in expected.items():
        if key not in executed:
            mismatches.append(f"{key}: missing from executed gold")
        elif not _values_match(want, executed[key]):
            mismatches.append(
                f"{key}: committed {want!r}, executed {executed[key]!r}"
            )
    return mismatches


def check_gold(bank: LoadedBank, world: World) -> list[GoldCheck]:
    """--check-gold: execute every gold script and confirm it still
    produces the committed expectation — the bank-rot detector."""
    checks: list[GoldCheck] = []
    for row in bank.rows:
        path = bank.gold_path(row)
        if path is None:
            continue
        try:
            executed = run_gold(path, world)
        except GoldError as exc:
            checks.append(
                GoldCheck(
                    row_id=row.id,
                    script=row.gold or "",
                    status="error",
                    error=str(exc),
                )
            )
            continue
        mismatches = compare_expected(row.expected_gold or {}, executed)
        checks.append(
            GoldCheck(
                row_id=row.id,
                script=row.gold or "",
                status="rot" if mismatches else "ok",
                mismatches=mismatches,
            )
        )
    return checks


def _values_match(want: Any, got: Any) -> bool:
    if isinstance(want, bool) or isinstance(got, bool):
        return want is got
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return math.isclose(float(want), float(got), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(want, list) and isinstance(got, list):
        return len(want) == len(got) and all(
            _values_match(w, g) for w, g in zip(want, got)
        )
    if isinstance(want, dict) and isinstance(got, dict):
        return set(want) == set(got) and all(
            _values_match(want[k], got[k]) for k in want
        )
    return want == got
