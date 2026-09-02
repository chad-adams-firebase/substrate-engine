"""Duration arithmetic shared by every surface that reads a duration
cell: the renderer (harness/render.py), the Verifier's duration bound
(verifier/checks/run_sql.py), and the eval grader's unit arm
(eval/tokens.py). One definition of "how many seconds is this cell",
so a bound never disagrees with the digits shown (the duration pass's
rule: every display-hint kind carries a plausibility bound, and the
bound reads the same hint the renderer does).

Pure code: no ports, no I/O.
"""

import re

from engine.tools.envelope import DurationUnit

UNIT_SECONDS: dict[DurationUnit, int] = {
    "seconds": 1,
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
}
# SQLite's time()/strftime output for an elapsed interval, and DuckDB's
# INTERVAL serialization: H:MM:SS, hours unpadded or padded, optional
# fraction.
_CLOCK = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)(?:\.(\d+))?$")


def is_timestamp_type(data_type: str | None) -> bool:
    """Whether a dictionary/stats data type is a timestamp — the types
    whose difference is an INTERVAL in DuckDB. DATE is excluded on
    purpose: DATE - DATE is an integer day count, not an interval."""
    if not data_type:
        return False
    upper = data_type.strip().upper()
    return upper.startswith("TIMESTAMP") or upper in {"DATETIME", "DATETIME2"}


def parse_clock(text: str) -> float | None:
    """Seconds in an H:MM:SS string, or None when the text is not one."""
    match = _CLOCK.match(text.strip())
    if match is None:
        return None
    hours, minutes, seconds, fraction = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    if fraction:
        total += float(f"0.{fraction}")
    return float(total)


def duration_seconds(cell: object, unit: DurationUnit | None) -> float | None:
    """Seconds in a duration-hinted cell: an H:MM:SS string carries its
    own unit; a number counts in the column's unit; anything else (a
    number under a clock-string hint, a bool, NULL, other text) is not
    a duration — the renderer prints it plain and the bounds skip it."""
    if isinstance(cell, str):
        return parse_clock(cell)
    if unit is not None and isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return float(cell) * UNIT_SECONDS[unit]
    return None
