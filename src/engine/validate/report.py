"""Render a ValidationReport as the small text artifact that travels
back from the work machine by commit (Brief §13)."""

from engine.validate.conformance import ValidationReport

_MARKS = {"PASS": "ok", "WARN": "warn", "FAIL": "FAIL"}


def render(report: ValidationReport) -> str:
    lines = [f"Conformance report — pack: {report.pack_name}", ""]
    for check in report.checks:
        lines.append(f"[{_MARKS[check.status]:>4}] {check.name}")
        for detail in check.details:
            lines.append(f"       - {detail}")
    lines.append("")
    lines.append("RESULT: " + ("PASS" if report.passed else "FAIL"))
    return "\n".join(lines) + "\n"
