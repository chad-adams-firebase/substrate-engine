"""Render a ValidationReport as the small text block that comes back
from the work machine as a photograph of the screen (Brief §13; the
work machine has no egress, so nothing travels by commit). One line
per check, a bounded list of details under a failing one — a full
report fits one screen even when it fails."""

from engine.validate.conformance import ValidationReport

_MARKS = {"PASS": "ok", "WARN": "warn", "FAIL": "FAIL"}

# Details shown per check before "… and N more". Enough to act on;
# few enough to photograph. The full list is in the report object.
MAX_DETAILS = 8


def render(report: ValidationReport) -> str:
    lines = [f"Conformance report — pack: {report.pack_name}", ""]
    for check in report.checks:
        lines.append(f"[{_MARKS[check.status]:>4}] {check.name}")
        for detail in check.details[:MAX_DETAILS]:
            lines.append(f"       - {detail}")
        hidden = len(check.details) - MAX_DETAILS
        if hidden > 0:
            lines.append(f"       … and {hidden} more (fix the first, re-run)")
    lines.append("")
    lines.append("RESULT: " + ("PASS" if report.passed else "FAIL"))
    return "\n".join(lines) + "\n"
