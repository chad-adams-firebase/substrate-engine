"""Render a GradeReport as the small text artifact that travels back
by commit (the conformance validator's pattern, Brief §13). The
invariant line comes first: a breach is the headline, never a row in
a table."""

from engine.eval.gold import GoldCheck
from engine.eval.grade import GradeReport

_MARKS = {
    "ok": "ok",
    "fail": "FAIL",
    "xfail": "XFAIL",
    "xpass": "XPASS",
    "rot": "ROT",
}


def render(report: GradeReport) -> str:
    header = report.header
    lines = [
        f"Eval grade — pack: {report.pack}"
        + (f" · report: {report.report_path}" if report.report_path else ""),
        f"engine {header.engine_sha[:12]}"
        + ("(dirty)" if header.engine_dirty else "")
        + (f" · target {header.target_sha[:12]}" if header.target_sha else "")
        + (f" · seed {header.seed}" if header.seed is not None else "")
        + f" · model {header.model} · bank {header.bank_hash}",
        "",
    ]
    for warning in report.warnings:
        lines.append(f"warning: {warning}")
    if report.warnings:
        lines.append("")

    if report.breaches:
        contradicted = sum(
            breach.severity == "contradicted" for breach in report.breaches
        )
        lines.append(
            "INVARIANT BREACH — wrong-but-verified "
            f"({len(report.breaches)} occurrence(s): {contradicted} "
            f"contradicted, {len(report.breaches) - contradicted} "
            "unsupported):"
        )
        for breach in report.breaches:
            lines.append(
                f"  !! {breach.row_id} rep {breach.rep} turn "
                f"{breach.turn_index} [{breach.severity}]: "
                f"{breach.assertion} — {breach.detail}"
                + (
                    f" [evidence {breach.evidence_ref}]"
                    if breach.evidence_ref
                    else ""
                )
            )
    else:
        lines.append("INVARIANT: ok — no wrong-but-verified occurrence")
    lines.append("")

    for row in report.rows:
        mark = _MARKS[row.status]
        annotation = f" ({row.xfail_ref})" if row.xfail_ref else ""
        summary = (
            f"[{mark:>5}] {row.row_id:<10} {row.passes}/{row.reps}"
            f"  threshold {row.threshold:.2f}{annotation}"
        )
        if row.failure_classes:
            summary += f"  failures: {', '.join(row.failure_classes)}"
        lines.append(summary)
        if row.status == "xpass":
            lines.append(
                "        - passed its threshold despite the xfail "
                "annotation. This grader observes pass rates, not code: "
                "confirm the fix landed, then delete the xfail block "
                "deliberately"
            )
        for note in row.notes:
            lines.append(f"        - {note}")

    if report.route_pairs:
        lines.append("")
        lines.append("Route pairs (first-decision tool must agree):")
        for pair in report.route_pairs:
            observed = ", ".join(
                f"{tool} ×{count}"
                for tool, count in sorted(pair.observed.items())
            )
            verdict = "consistent" if pair.consistent else "SPLIT"
            lines.append(
                f"  {pair.pair} [{', '.join(pair.rows)}]: "
                f"{observed} — {verdict}"
            )

    xfails = [row for row in report.rows if row.xfail_ref]
    if xfails:
        lines.append("")
        lines.append("Xfail ledger:")
        by_ref: dict[str, list[str]] = {}
        for row in xfails:
            by_ref.setdefault(row.xfail_ref or "", []).append(row.row_id)
        for ref, ids in sorted(by_ref.items()):
            lines.append(f"  {ref}: {', '.join(ids)}")

    lines.append("")
    exit_code = report.exit_code()
    verdict = {
        0: "PASS",
        2: "FAIL (thresholds)",
        3: "FAIL (bank rot)",
        4: "FAIL (INVARIANT BREACH)",
    }[exit_code]
    lines.append(f"RESULT: {verdict}")
    return "\n".join(lines) + "\n"


def render_gold_checks(checks: list[GoldCheck]) -> str:
    lines = ["Gold check — executed scripts vs committed expectations", ""]
    for check in checks:
        mark = {"ok": "ok", "rot": "ROT", "error": "ERROR"}[check.status]
        lines.append(f"[{mark:>5}] {check.row_id:<10} {check.script}")
        for mismatch in check.mismatches:
            lines.append(f"        - {mismatch}")
        if check.error:
            lines.append(f"        - {check.error}")
    rotten = sum(check.status != "ok" for check in checks)
    lines.append("")
    lines.append(
        "RESULT: "
        + ("PASS" if rotten == 0 else f"FAIL ({rotten} script(s) rotten)")
    )
    return "\n".join(lines) + "\n"
