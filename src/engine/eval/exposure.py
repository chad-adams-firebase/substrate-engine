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

The Backlog Pass made the unit of replay the turn, not the statement:
the key lint grounds a literal against what the conversation had shown
before it — every earlier question, every key an earlier result or
filter carried, the statement's own grounding — and the anchor check
reads a follow-up's answer against the entity a prior turn's evidence
established. So the walk keeps one accumulator per conversation (a
report's row and rep, or a work store's conversation), in turn order,
and a turn with no statement at all (turn 7 of the 30-turn session was
a docs search) still faces the anchor check. Old records carry no
declared about, so what the replay measures there is the fallback
readings — the SQL filter and the prose.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from engine.config.models import PlausibilitySettings, ToolName
from engine.eval.models import RunRecord, RunReportHeader
from engine.harness.outcomes import AnswerOutcome, TurnOutcome, loads_outcome
from engine.ports.work_store import WorkStorePort
from engine.substrates.models import DictionaryMap, DictionaryRow, StatsRow
from engine.tools.entities import (
    EntityCatalog,
    harvest_turn_anchors,
    known_values,
)
from engine.tools.enum_lint import lint_enum_literals
from engine.tools.envelope import (
    KnownKey,
    RunSqlEvidence,
    RunSqlOutput,
    ToolInvocation,
    TurnAnchors,
    loads_turn_evidence,
)
from engine.tools.interval_lint import lint_interval_arithmetic
from engine.tools.key_lint import lint_placeholders, lint_ungrounded_keys
from engine.tools.run_sql import fenced_block
from engine.tools.sql_lint import lint_fan_out
from engine.verifier.anchor import check_anchor, referent_kind
from engine.verifier.checks.base import PlausibilityContext
from engine.verifier.checks.run_sql import RunSqlCheck
from engine.verifier.models import DraftAnswer

LINT_CHECKS = (
    "lint.fan_out",
    "lint.enum_literal",
    "lint.interval_arithmetic",
    "lint.placeholder",
    "lint.ungrounded_key",
)
ANCHOR_CHECKS = ("anchor.entity_mismatch",)


class ExposureError(Exception):
    """The replay cannot be trusted; the message says which pin
    mismatched."""


class ExposureHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    rep: int
    turn_index: int
    # -1 for a turn-level hit (the anchor check belongs to no statement).
    invocation_index: int
    # run_sql.<check> for a plausibility finding; lint.<name> for a
    # lint challenge the statement would draw today; anchor.<check>
    # for the turn's answer read against the conversation.
    check: str
    severity: str  # warn | fail | challenge
    detail: str
    sql: str
    question: str = ""  # a turn-level hit's question, for the rendering


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


@dataclass(frozen=True)
class Turn:
    """One turn with its attribution and what the conversation-level
    checks read (Backlog Pass): the question as the user asked it, the
    outcome, every invocation in order, the summary the turn began
    with, and the engine's turn number for "turn N established"."""

    row_id: str
    rep: int
    turn_index: int
    question: str
    outcome: TurnOutcome | None
    invocations: tuple[ToolInvocation, ...]
    summary_before: str = ""
    engine_turn: int = 0
    first_index: int = 0  # the index of invocations[0], for a lone statement


def _executed(invocations: Iterable[ToolInvocation]) -> Iterator[tuple[int, ToolInvocation]]:
    for index, invocation in enumerate(invocations):
        if invocation.tool == ToolName.RUN_SQL and isinstance(
            invocation.output, RunSqlOutput
        ):
            yield index, invocation


def report_turns(records: Iterable[RunRecord]) -> Iterator[Turn]:
    """Every turn a report's records carry, in order, each with the
    summary the previous turn of its rep ended with."""
    for record in records:
        summary = ""
        for turn in record.turns:
            invocations = (
                tuple(loads_turn_evidence(turn.evidence_payload))
                if turn.evidence_payload
                else ()
            )
            yield Turn(
                row_id=record.row_id,
                rep=record.rep,
                turn_index=turn.turn_index,
                question=turn.question,
                outcome=turn.outcome,
                invocations=invocations,
                summary_before=summary,
                engine_turn=turn.engine_turn or turn.turn_index + 1,
            )
            summary = turn.summary


def report_statements(records: Iterable[RunRecord]) -> Iterator[Statement]:
    """Every executed run_sql statement a report's records carry."""
    for turn in report_turns(records):
        for index, invocation in _executed(turn.invocations):
            yield Statement(turn.row_id, turn.rep, turn.turn_index, index, invocation)


def work_store_turns(
    store: WorkStorePort,
    conversation_ids: Iterable[int],
    world_manifests: dict[str, str],
) -> list[Turn]:
    """Every turn the named conversations logged — the browser's turns,
    measured like a report's. A turn whose substrate_versions name a
    manifest this world does not have is refused, exactly as a report
    from another world is. The turn log carries no summary, so the
    replay grounds on the questions and the keys alone there."""
    known = set(world_manifests.values())
    turns: list[Turn] = []
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
            invocations: tuple[ToolInvocation, ...] = ()
            if entry.evidence_bundle_ref:
                payload = store.load_evidence_bundle(entry.evidence_bundle_ref)
                if payload is not None:
                    invocations = tuple(loads_turn_evidence(payload))
            outcome = loads_outcome(entry.outcome) if entry.outcome else None
            turns.append(
                Turn(
                    row_id=f"conv{conversation_id}",
                    rep=1,
                    turn_index=entry.turn,
                    question=entry.question,
                    outcome=outcome,
                    invocations=invocations,
                    engine_turn=entry.turn,
                )
            )
    return turns


def work_store_statements(
    store: WorkStorePort,
    conversation_ids: Iterable[int],
    world_manifests: dict[str, str],
) -> list[Statement]:
    """Every executed run_sql statement the named conversations logged."""
    return [
        Statement(turn.row_id, turn.rep, turn.turn_index, index, invocation)
        for turn in work_store_turns(store, conversation_ids, world_manifests)
        for index, invocation in _executed(turn.invocations)
    ]


def _turns(source: Iterable[RunRecord | Turn | Statement]) -> Iterator[Turn]:
    for item in source:
        if isinstance(item, Turn):
            yield item
        elif isinstance(item, Statement):
            # A lone statement, as the older callers hand it: no
            # question and no outcome, so only the statement checks read it.
            yield Turn(
                row_id=item.row_id,
                rep=item.rep,
                turn_index=item.turn_index,
                question="",
                outcome=None,
                invocations=(item.invocation,),
                engine_turn=item.turn_index,
                first_index=item.invocation_index,
            )
        else:
            yield from report_turns([item])


@dataclass
class _Thread:
    """One conversation's accumulator across its turns: the user's
    words so far, every turn's anchors, every key seen."""

    texts: list[str] = field(default_factory=list)
    prior: list[TurnAnchors] = field(default_factory=list)
    keys: list[KnownKey] = field(default_factory=list)


def expose(
    records: Iterable[RunRecord | Turn | Statement],
    *,
    stats: list[StatsRow],
    dictionary: list[DictionaryRow],
    dictionary_map: DictionaryMap,
    settings: PlausibilitySettings,
    checks: Iterable[str] | None = None,
    report_path: str | None = None,
) -> ExposureReport:
    """Every turn in the records (or the already-attributed turns or
    statements), under today's guards: each executed run_sql statement
    faces the plausibility suite and the five lints, with the key lint
    grounded on what its conversation had shown; each answered turn
    faces the anchor check against the turns before it. `checks`
    narrows the hits to the named checks."""
    wanted = list(checks or [])
    keep = set(wanted)
    context = PlausibilityContext(stats=stats, settings=settings)
    verifier_check = RunSqlCheck()
    catalog = EntityCatalog.from_substrates(dictionary, dictionary_map)
    threads: dict[tuple[str, int], _Thread] = {}
    hits: list[ExposureHit] = []
    statements = 0

    def hit(turn: Turn, index: int, check: str, severity: str, detail: str, sql: str) -> None:
        if keep and check not in keep:
            return
        hits.append(
            ExposureHit(
                row_id=turn.row_id,
                rep=turn.rep,
                turn_index=turn.turn_index,
                invocation_index=index,
                check=check,
                severity=severity,
                detail=detail,
                sql=sql,
                question=turn.question if index < 0 else "",
            )
        )

    for turn in _turns(records):
        thread = threads.setdefault((turn.row_id, turn.rep), _Thread())
        texts = [*thread.texts, turn.question]
        if turn.summary_before:
            texts.append(turn.summary_before)
        seen_this_turn: list[KnownKey] = []
        for position, invocation in _executed(turn.invocations):
            output = invocation.output
            assert isinstance(output, RunSqlOutput)
            statements += 1
            index = turn.first_index + position
            for finding in verifier_check.plausibility(invocation, context):
                hit(turn, index, finding.check, finding.severity, finding.detail, output.sql)
            evidence = invocation.evidence
            grounding = evidence.grounding_prompt if isinstance(evidence, RunSqlEvidence) else ""
            raw = (
                evidence.attempts[-1].raw_response
                if isinstance(evidence, RunSqlEvidence) and evidence.attempts
                else output.sql
            )
            known = known_values(texts, [*thread.keys, *seen_this_turn], grounding)
            for name, reason in (
                ("lint.fan_out", lint_fan_out(output.sql, dictionary, dictionary_map)),
                ("lint.enum_literal", lint_enum_literals(output.sql, dictionary)),
                ("lint.interval_arithmetic", lint_interval_arithmetic(output.sql, dictionary)),
                ("lint.placeholder", lint_placeholders(output.sql, comment_source=fenced_block(raw))),
                ("lint.ungrounded_key", lint_ungrounded_keys(output.sql, catalog, known)),
            ):
                if reason is not None:
                    hit(turn, index, name, "challenge", reason, output.sql)
            seen_this_turn.extend(harvest_turn_anchors([invocation], catalog).keys)

        about: str | None = None
        finding = None
        if isinstance(turn.outcome, AnswerOutcome) and turn.question:
            body = turn.outcome.body
            about = body.about or None
            draft = (
                DraftAnswer(kind="table_passthrough", text=body.caption)
                if body.kind == "table"
                else DraftAnswer(kind="prose", text=body.text)
            )
            finding = check_anchor(
                question=turn.question,
                about=about,
                draft=draft,
                evidence=list(turn.invocations),
                prior=thread.prior,
                catalog=catalog,
            )
            if finding is not None:
                hit(turn, -1, finding.check, finding.severity, finding.detail, "")

        # The harness's bookkeeping, replayed (Fix Pass): a warned turn
        # and a non-answer establish nothing, so the replay's prior
        # anchors are the ones a live conversation would have carried.
        kind = referent_kind(turn.question, thread.prior, catalog) if turn.question else None
        anchors = harvest_turn_anchors(
            list(turn.invocations),
            catalog,
            about=about,
            question_kind=kind,
            turn=turn.engine_turn or turn.turn_index,
            answered=isinstance(turn.outcome, AnswerOutcome),
            contradiction=(kind or "", finding.detail) if finding is not None else None,
        )
        thread.prior.append(anchors)
        thread.keys.extend(anchors.keys)
        if turn.question:
            thread.texts.append(turn.question)
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
            lines.append(
                f"  {hit.row_id} rep {hit.rep} turn {hit.turn_index} "
                f"[{hit.severity}]: {hit.detail}"
            )
            if hit.sql:
                sql = " ".join(hit.sql.split())
                if len(sql) > sql_chars:
                    sql = sql[: sql_chars - 1] + "…"
                lines.append(f"    {sql}")
            elif hit.question:
                lines.append(f"    (question: {hit.question})")
    return "\n".join(lines) + "\n"
