"""engine eval exposure: replay the guards over a committed report.

The guard pass's rule (docs/pin-pass-residuals.md): a new plausibility
bound or lint is run read-only against the latest committed report
before it lands, and the change states its hit count and every hit's
attribution. This is the tool that does it, committed so the next pass
does not rebuild the scratch script that proved the entity-count bound
had zero false positives (208 statements, 3 hits, all three AMB2).

Offline: the report's evidence payloads carry every executed statement
and its result table; the pack's substrates carry the stats and the
dictionary the guards read. No LLM, no database. Each executed run_sql
invocation faces the Verifier's plausibility suite (RunSqlCheck) under
the pack's current settings, and the three lints under the pack's
current dictionary and map; every finding is a hit, attributed to its
row, rep, turn and statement. A report replayed under the guards it
was graded with reproduces its own findings; what a replay adds after
a guard lands is what the guard would have said.

The world must match: the bounds read the stats substrate, and a
report that ran against other stats would expose the wrong thing —
refused, as grade refuses.

A work store's conversation is measured the same way (Polish Pass): the
browser's turns log the same evidence bundles a report inlines, so the
statements a manager typed face today's guards exactly like a bank row's.
Each turn's substrate_versions must name manifests this world has.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from engine.config.models import PlausibilitySettings, ToolName
from engine.eval.models import RunRecord, RunReportHeader
from engine.ports.work_store import WorkStorePort
from engine.substrates.models import DictionaryMap, DictionaryRow, StatsRow
from engine.tools.enum_lint import lint_enum_literals
from engine.tools.envelope import RunSqlOutput, ToolInvocation, loads_turn_evidence
from engine.tools.interval_lint import lint_interval_arithmetic
from engine.tools.sql_lint import lint_fan_out
from engine.verifier.checks.base import PlausibilityContext
from engine.verifier.checks.run_sql import RunSqlCheck

LINT_CHECKS = ("lint.fan_out", "lint.enum_literal", "lint.interval_arithmetic")


class ExposureError(Exception):
    """The replay cannot be trusted; the message says which pin
    mismatched."""


class ExposureHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    rep: int
    turn_index: int
    invocation_index: int
    # run_sql.<check> for a plausibility finding; lint.<name> for a
    # lint challenge the statement would draw today.
    check: str
    severity: str  # warn | fail | challenge
    detail: str
    sql: str


class ExposureReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_path: str | None = None
    statements: int
    # The checks asked for, in order; empty means every check.
    checks: list[str] = []
    hits: list[ExposureHit]

    def counts(self) -> dict[str, int]:
        """Hits per check, the requested checks first (a requested check
        with no hits is listed at zero — silence is the finding)."""
        counted = Counter(hit.check for hit in self.hits)
        ordered: dict[str, int] = {check: 0 for check in self.checks}
        for check in sorted(counted):
            ordered[check] = counted[check]
        return ordered


def check_world(header: RunReportHeader, world_manifests: dict[str, str]) -> None:
    for generator, manifest_id in sorted(header.world_manifests.items()):
        current = world_manifests.get(generator)
        if current != manifest_id:
            raise ExposureError(
                f"world mismatch: report ran against {generator} manifest "
                f"{manifest_id}, this world has {current!r} — the bounds "
                "would read stats the statements never ran against."
            )


@dataclass(frozen=True)
class Statement:
    """One executed run_sql invocation with its attribution: a report's
    row/rep/turn, or a work store's conversation (row_id `conv<id>`,
    rep 1, the engine turn number)."""

    row_id: str
    rep: int
    turn_index: int
    invocation_index: int
    invocation: ToolInvocation


def _executed(invocations: Iterable[ToolInvocation]) -> Iterator[tuple[int, ToolInvocation]]:
    for index, invocation in enumerate(invocations):
        if invocation.tool == ToolName.RUN_SQL and isinstance(
            invocation.output, RunSqlOutput
        ):
            yield index, invocation


def report_statements(records: Iterable[RunRecord]) -> Iterator[Statement]:
    """Every executed run_sql statement a report's records carry."""
    for record in records:
        for turn in record.turns:
            if not turn.evidence_payload:
                continue
            for index, invocation in _executed(loads_turn_evidence(turn.evidence_payload)):
                yield Statement(record.row_id, record.rep, turn.turn_index, index, invocation)


def work_store_statements(
    store: WorkStorePort,
    conversation_ids: Iterable[int],
    world_manifests: dict[str, str],
) -> list[Statement]:
    """Every executed run_sql statement the named conversations logged —
    the browser's turns, measured like a report. A turn whose
    substrate_versions name a manifest this world does not have is
    refused, exactly as a report from another world is."""
    known = set(world_manifests.values())
    statements: list[Statement] = []
    for conversation_id in conversation_ids:
        if store.get_conversation(conversation_id) is None:
            raise ExposureError(
                f"conversation {conversation_id} does not exist in this work store."
            )
        for entry in store.list_turn_logs(conversation_id):
            foreign = sorted(set(entry.substrate_versions) - known)
            if foreign:
                raise ExposureError(
                    f"world mismatch: conversation {conversation_id} turn "
                    f"{entry.turn} ran against manifest(s) {', '.join(foreign)} "
                    "this world does not have — the bounds would read stats "
                    "the statements never ran against."
                )
            if not entry.evidence_bundle_ref:
                continue
            payload = store.load_evidence_bundle(entry.evidence_bundle_ref)
            if payload is None:
                continue
            for index, invocation in _executed(loads_turn_evidence(payload)):
                statements.append(
                    Statement(
                        f"conv{conversation_id}", 1, entry.turn, index, invocation
                    )
                )
    return statements


def _statements(source: Iterable[RunRecord | Statement]) -> Iterator[Statement]:
    for item in source:
        if isinstance(item, Statement):
            yield item
        else:
            yield from report_statements([item])


def expose(
    records: Iterable[RunRecord | Statement],
    *,
    stats: list[StatsRow],
    dictionary: list[DictionaryRow],
    dictionary_map: DictionaryMap,
    settings: PlausibilitySettings,
    checks: Iterable[str] | None = None,
    report_path: str | None = None,
) -> ExposureReport:
    """Every executed run_sql statement in the records (or the
    already-attributed statements), under today's guards. `checks`
    narrows the hits to the named checks."""
    wanted = list(checks or [])
    keep = set(wanted)
    context = PlausibilityContext(stats=stats, settings=settings)
    verifier_check = RunSqlCheck()
    hits: list[ExposureHit] = []
    statements = 0
    for statement in _statements(records):
        invocation = statement.invocation
        output = invocation.output
        assert isinstance(output, RunSqlOutput)
        statements += 1
        found: list[tuple[str, str, str]] = [
            (finding.check, finding.severity, finding.detail)
            for finding in verifier_check.plausibility(invocation, context)
        ]
        for name, reason in (
            ("lint.fan_out", lint_fan_out(output.sql, dictionary, dictionary_map)),
            ("lint.enum_literal", lint_enum_literals(output.sql, dictionary)),
            (
                "lint.interval_arithmetic",
                lint_interval_arithmetic(output.sql, dictionary),
            ),
        ):
            if reason is not None:
                found.append((name, "challenge", reason))
        for check, severity, detail in found:
            if keep and check not in keep:
                continue
            hits.append(
                ExposureHit(
                    row_id=statement.row_id,
                    rep=statement.rep,
                    turn_index=statement.turn_index,
                    invocation_index=statement.invocation_index,
                    check=check,
                    severity=severity,
                    detail=detail,
                    sql=output.sql,
                )
            )
    return ExposureReport(
        report_path=report_path, statements=statements, checks=wanted, hits=hits
    )


def render_exposure(report: ExposureReport, *, sql_chars: int = 160) -> str:
    """The small text artifact: counts first, then every hit with its
    attribution and the statement on one line."""
    lines = [
        "Eval exposure"
        + (f" — report: {report.report_path}" if report.report_path else "")
        + f" · statements: {report.statements}",
        "",
    ]
    counts = report.counts()
    if not counts:
        lines.append("(no hits)")
        return "\n".join(lines) + "\n"
    width = max(len(check) for check in counts)
    for check, count in counts.items():
        lines.append(f"{check.ljust(width)}  {count}")
    for check in counts:
        hits = [hit for hit in report.hits if hit.check == check]
        if not hits:
            continue
        lines.append("")
        lines.append(f"{check}:")
        for hit in hits:
            sql = " ".join(hit.sql.split())
            if len(sql) > sql_chars:
                sql = sql[: sql_chars - 1] + "…"
            lines.append(
                f"  {hit.row_id} rep {hit.rep} turn {hit.turn_index} "
                f"[{hit.severity}]: {hit.detail}"
            )
            lines.append(f"    {sql}")
    return "\n".join(lines) + "\n"
