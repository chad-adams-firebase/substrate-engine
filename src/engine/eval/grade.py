"""engine eval grade: the fully offline half.

Reads a run report, executes each row's gold script fresh against the
world (never the committed transcription — the grader's-correction
law), evaluates assertions per rep, and aggregates pass-rates against
thresholds. No LLM anywhere: every assertion is mechanical, which is
why the assertion vocabulary is closed.

The one law above all thresholds: any rep that exits 0 while a
content assertion fails is a wrong-but-verified occurrence — the
invariant the Verifier exists to enforce — and one occurrence fails
the entire grade loudly, xfail and sentinel rows included.
"""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from engine.eval.bank import LoadedBank
from engine.eval.gold import GoldError, compare_expected, run_gold
from engine.eval.models import (
    Assertion,
    BankRow,
    EmittedTokens,
    Expectation,
    RunRecord,
    RunReportHeader,
    SetupSpec,
    TurnRecord,
)
from engine.eval.tokens import (
    FLOAT_TAILS,
    MONEY,
    answer_body,
    answer_caption,
    answer_envelope,
    extract_numbers,
    flatten_answer,
)
from engine.eval.world import World
from engine.tools.envelope import ToolInvocation, loads_turn_evidence

_CURRENCY = re.compile(r"\$\s?\d{1,3}(?:,\d{3})*\.\d{2}$")
_BARE_ID = re.compile(r"\b[a-z_]*_?id\b\s*[:=]?\s*\d+", re.IGNORECASE)


class GradeError(Exception):
    """Grading cannot proceed (wrong world, wrong bank, unreadable
    report); the message says which pin mismatched."""


# --- Result models ------------------------------------------------------


# Both severities exit 4 — a breach is a breach. The label exists so
# the next reader diagnoses in minutes: "contradicted" means the answer
# stated a competing value (the catastrophic shape); "unsupported"
# means the gold token is simply absent — an omission, possibly a
# right-but-incomplete answer, possibly an assertion-shape mismatch.
# A window whose literals merely differ from the gold's never gets
# here: only a wall-clock anchor is the window breach (_alarm_worthy).
BreachSeverity = Literal["contradicted", "unsupported"]


class BreachRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    rep: int
    turn_index: int
    assertion: str
    severity: BreachSeverity
    detail: str
    evidence_ref: str | None = None


class DocumentedMiss(BaseModel):
    """A WBV-class event the alarm deliberately ignores: a content
    assertion with breach:false failing alarm-worthily at exit 0
    (play pass W5/W6 — known stochastic misses that verify inside
    every plausibility bound). Counted, never alarming, so the
    invariant claim stays precise: INVARIANT ok = zero UNDOCUMENTED
    wrong-but-verified."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    rep: int
    turn_index: int
    assertion: str
    detail: str


class RowGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str
    category: str
    reps: int
    passes: int
    threshold: float
    status: Literal["ok", "fail", "xfail", "xpass", "rot", "inconclusive"]
    xfail_ref: str | None = None
    # Rows with a setup block: how many reps reached the scenario.
    # None means the row has no setup and every rep counts.
    reached: int | None = None
    failure_classes: list[str] = []
    notes: list[str] = []

    @property
    def pass_rate(self) -> float:
        return self.passes / self.reps if self.reps else 0.0


class RoutePairGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair: str
    rows: list[str]
    observed: dict[str, int]  # first-decision tool -> rep count

    @property
    def consistent(self) -> bool:
        return len(self.observed) == 1


class GradeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack: str
    report_path: str
    header: RunReportHeader
    warnings: list[str] = []
    breaches: list[BreachRecord] = []
    documented_misses: list[DocumentedMiss] = []
    rows: list[RowGrade] = []
    route_pairs: list[RoutePairGrade] = []

    def exit_code(self) -> int:
        """4 breach (dominates) · 3 bank rot · 2 threshold failures ·
        0 pass. 1 is reserved for usage/IO errors upstream. An
        inconclusive row gates like a threshold failure unless its
        xfail annotation predicted failure anyway."""
        if self.breaches:
            return 4
        if any(row.status == "rot" for row in self.rows):
            return 3
        if any(
            row.status == "fail"
            or (row.status == "inconclusive" and row.xfail_ref is None)
            for row in self.rows
        ):
            return 2
        return 0


# --- Preflight ----------------------------------------------------------


def preflight(
    bank: LoadedBank, header: RunReportHeader, world_manifests: dict[str, str]
) -> list[str]:
    """Refuse on world/bank drift; warn on engine drift. Returns the
    warnings; raises GradeError on refusals."""
    if bank.bank_hash != header.bank_hash:
        raise GradeError(
            f"bank hash mismatch: report ran bank {header.bank_hash}, this "
            f"bank is {bank.bank_hash} — grade against the bank the run "
            f"used (or re-run)."
        )
    for generator, manifest_id in sorted(header.world_manifests.items()):
        current = world_manifests.get(generator)
        if current != manifest_id:
            raise GradeError(
                f"world mismatch: report ran against {generator} manifest "
                f"{manifest_id}, this world has {current!r} — gold "
                f"recomputed here would referee a different world."
            )
    warnings = []
    from engine.eval.runner import _engine_sha

    current_sha, _ = _engine_sha()
    if current_sha != header.engine_sha:
        warnings.append(
            f"engine drift: report from {header.engine_sha[:12]}, grading "
            f"at {current_sha[:12]} (grading logic may legitimately be "
            f"newer)"
        )
    if header.engine_dirty:
        warnings.append("the run's engine working tree was dirty")
    return warnings


def pack_world_manifests(pack_root) -> dict[str, str]:
    manifests: dict[str, str] = {}
    directory = pack_root / "substrates" / "manifests"
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            manifests[data.get("generator", path.stem)] = data.get(
                "manifest_id", "?"
            )
    return manifests


# --- Assertion evaluation ------------------------------------------------


class _TurnView:
    """One recorded turn, with its lazily parsed evidence."""

    def __init__(self, record: TurnRecord) -> None:
        self.record = record
        self.text = flatten_answer(record.outcome)
        self.body = answer_body(record.outcome)
        self.caption = answer_caption(record.outcome)
        self.envelope = answer_envelope(record.outcome)
        self._invocations: list[ToolInvocation] | None = None

    @property
    def invocations(self) -> list[ToolInvocation]:
        if self._invocations is None:
            self._invocations = (
                loads_turn_evidence(self.record.evidence_payload)
                if self.record.evidence_payload
                else []
            )
        return self._invocations


def evaluate(
    assertion: Assertion, view: _TurnView, gold: dict[str, Any] | None
) -> tuple[bool, str]:
    """(passed, detail). Every branch is mechanical and offline."""
    kind = assertion.kind
    if kind == "nonempty":
        return bool(view.text.strip()), "answer is empty"

    if kind == "numeric_from_gold":
        wants = []
        for field in assertion.fields():
            want = _gold_value(gold, field)
            if not isinstance(want, (int, float)) or isinstance(want, bool):
                return False, f"gold field {field} is not numeric"
            wants.append(want)
        stated = extract_numbers(view.body)
        # Any listed reading satisfies the row (AMB2: READY or
        # not-CLOSED); for most rows the single field is the answer.
        if any(
            _numbers_match(value, float(want))
            for value in stated
            for want in wants
        ):
            return True, ""
        label = str(wants[0]) if len(wants) == 1 else f"any of {wants!r}"
        detail = (
            f"gold {label} absent from answer ({view.envelope}) numerics "
            f"{_head(stated)}"
        )
        if view.caption:
            detail += (
                f"; caption literals {_head(extract_numbers(view.caption))}"
            )
        return False, detail

    if kind == "name_from_gold":
        wants = []
        for field in assertion.fields():
            want = _gold_value(gold, field)
            if not isinstance(want, str) or not want:
                return False, f"gold field {field} is not a name"
            wants.append(want)
        # Any listed form satisfies the row: the code and the joined
        # display name are both the right answer where a row says so.
        if any(
            re.search(rf"\b{re.escape(want)}\b", view.text, re.IGNORECASE)
            for want in wants
        ):
            return True, ""
        label = repr(wants[0]) if len(wants) == 1 else f"any of {wants!r}"
        if assertion.forbid_bare_ids and _BARE_ID.search(view.text):
            return False, f"answer names an id, not the person {label}"
        return False, f"gold name {label} absent from the answer"

    if kind == "contains":
        return _contains(view.text, assertion.pattern, assertion.regex,
                         assertion.case_sensitive), (
            f"pattern {assertion.pattern!r} absent"
        )

    if kind == "not_contains":
        if assertion.from_gold_field is not None:
            names = _gold_value(gold, assertion.from_gold_field)
            if not isinstance(names, list):
                return False, (
                    f"gold field {assertion.from_gold_field} is not a list"
                )
            offending = [
                str(name)
                for name in names
                if re.search(
                    rf"\b{re.escape(str(name))}\b", view.text, re.IGNORECASE
                )
            ]
            return not offending, f"forbidden name(s) present: {offending}"
        present = _contains(
            view.text, assertion.pattern, assertion.regex,
            assertion.case_sensitive,
        )
        return not present, f"forbidden pattern {assertion.pattern!r} present"

    if kind == "currency_format":
        bad = [
            token
            for token in MONEY.findall(view.text)
            if not _CURRENCY.match(token)
        ]
        tails = FLOAT_TAILS.findall(view.text)
        if bad or tails:
            return False, f"unformatted money {bad}, float tails {tails}"
        return True, ""

    if kind == "window_data_anchored":
        forbidden, missing, problem = _window_findings(assertion, view, gold)
        if problem:
            return False, problem
        details = []
        if forbidden:
            details.append(f"wall-clock anchor(s) {forbidden} in executed SQL")
        if missing:
            details.append(
                f"window literal(s) {missing} absent from executed SQL "
                "(convention mismatch; gates the rep, not a breach)"
            )
        return not details, "; ".join(details)

    if kind == "route":
        return _check_route(assertion, view.record.tools_used)

    if kind == "retry_count":
        return _check_retries(assertion, view.invocations)

    if kind == "envelope":
        outcome = view.record.outcome
        if outcome is None or outcome.kind != "answer":
            return False, "no answer envelope"
        return (
            outcome.body.kind == assertion.body,
            f"envelope is {outcome.body.kind}, expected {assertion.body}",
        )

    if kind == "no_text_block_dump":
        return _check_no_dump(assertion, view)

    if kind == "verdict_check":
        return _check_verdict(assertion, view.record)

    raise GradeError(f"unknown assertion kind {kind!r}")  # unreachable


def _head(values: list[float]) -> str:
    return f"{values[:8]}{'…' if len(values) > 8 else ''}"


def _gold_value(gold: dict[str, Any] | None, field: str):
    if gold is None:
        return None
    return gold.get(field)


def _numbers_match(stated: float, want: float) -> bool:
    def close(a: float, b: float, slack: float = 0.005) -> bool:
        return abs(a - b) <= max(1e-6 * max(abs(a), abs(b)), slack)

    if close(stated, want):
        return True
    # A gold ratio may legitimately surface as its percent form — and
    # rates render at one decimal (0.9545 -> 95.5%), so the percent arm
    # tolerates half a displayed decimal, the way 0.005 tolerates half
    # a cent on money.
    return 0 < abs(want) <= 1 and close(stated, want * 100, slack=0.05)


def _contains(
    text: str, pattern: str | None, regex: bool, case_sensitive: bool
) -> bool:
    if pattern is None:
        return False
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        return re.search(pattern, text, flags) is not None
    return re.search(re.escape(pattern), text, flags) is not None


def _window_findings(
    assertion, view: _TurnView, gold
) -> tuple[list[str], list[str], str | None]:
    """(forbidden anchors, missing gold literals, problem). The two
    halves of the window assertion do different jobs: a wall-clock
    anchor is the invariant (a verified count through a real-today
    window); a missing literal is a convention mismatch — the fp3
    re-run's A1 answered the right count over a calendar week, which
    must fail the rep and must not sound the alarm. A problem (no
    gold, no windowed SQL) is reported as-is and gates only."""
    literals = _gold_value(gold, assertion.field)
    if literals is None:
        return [], [], f"gold field {assertion.field} missing"
    if not isinstance(literals, list):
        literals = [literals]
    corpus_parts = []
    for invocation in view.invocations:
        if invocation.tool.value == "run_sql":
            if invocation.output is not None:
                corpus_parts.append(invocation.output.sql)
            if invocation.evidence is not None:
                corpus_parts.extend(
                    attempt.sql or "" for attempt in invocation.evidence.attempts
                )
        if invocation.tool.value == "check_execution":
            corpus_parts.append(json.dumps(invocation.arguments))
    corpus = "\n".join(corpus_parts)
    if not corpus:
        return [], [], "no windowed-tool SQL/arguments in evidence"
    lowered = corpus.lower()
    forbidden = [tok for tok in assertion.forbid if tok.lower() in lowered]
    missing = [str(lit) for lit in literals if str(lit) not in corpus]
    return forbidden, missing, None


def _check_route(assertion, tools_used: list[str]) -> tuple[bool, str]:
    tools = [tool.value for tool in assertion.tools]
    detail = f"tools_used={tools_used}"
    if assertion.mode == "first":
        return (
            bool(tools_used) and tools_used[0] in tools,
            f"first tool not in {tools}; {detail}",
        )
    if assertion.mode == "must_include":
        return (
            all(tool in tools_used for tool in tools),
            f"missing required tool(s) from {tools}; {detail}",
        )
    if assertion.mode == "must_include_any_of":
        return (
            any(tool in tools_used for tool in tools),
            f"none of {tools} used; {detail}",
        )
    if assertion.mode == "must_not_include":
        return (
            not any(tool in tools_used for tool in tools),
            f"forbidden tool(s) from {tools} used; {detail}",
        )
    return (
        set(tools_used) == set(tools),
        f"route set differs from {tools}; {detail}",
    )


def _check_retries(assertion, invocations) -> tuple[bool, str]:
    tool = assertion.tool.value
    errored = [
        index
        for index, invocation in enumerate(invocations)
        if invocation.tool.value == tool
        and invocation.status == "error"
        and assertion.error_contains in (invocation.error or "")
    ]
    if len(errored) != assertion.errors:
        return False, (
            f"expected {assertion.errors} matching {tool} error(s), "
            f"saw {len(errored)}"
        )
    for index in errored:
        followed = any(
            later.tool.value == tool
            for later in invocations[index + 1 :]
        )
        if not followed:
            return False, (
                f"{tool} error at invocation {index} was never retried "
                f"(the N5 license)"
            )
    return True, ""


def _check_no_dump(assertion, view: _TurnView) -> tuple[bool, str]:
    minimum = assertion.min_length
    if len(view.text) < minimum:
        return True, ""
    for invocation in view.invocations:
        for value in _long_strings(
            invocation.model_dump(mode="json"), minimum
        ):
            step = max(1, minimum // 4)
            for start in range(0, len(value) - minimum + 1, step):
                window = value[start : start + minimum]
                if window in view.text:
                    return False, (
                        f"answer pastes ≥{minimum} chars of evidence "
                        f"verbatim: {window[:60]!r}…"
                    )
    return True, ""


def _long_strings(payload, minimum: int):
    if isinstance(payload, str):
        if len(payload) >= minimum:
            yield payload
    elif isinstance(payload, dict):
        for value in payload.values():
            yield from _long_strings(value, minimum)
    elif isinstance(payload, list):
        for value in payload:
            yield from _long_strings(value, minimum)


def _check_verdict(assertion, record: TurnRecord) -> tuple[bool, str]:
    verdict = record.verdict
    if verdict is None:
        return False, "no verifier verdict recorded"
    if (
        assertion.disposition is not None
        and verdict.disposition != assertion.disposition
    ):
        return False, (
            f"disposition {verdict.disposition}, expected "
            f"{assertion.disposition}"
        )
    if (
        assertion.max_judge_calls is not None
        and verdict.judge_calls > assertion.max_judge_calls
    ):
        return False, (
            f"judge_calls {verdict.judge_calls} > {assertion.max_judge_calls}"
        )
    if assertion.min_injected_claims is not None:
        injected = (
            sum(
                claim.status == "matched_injected"
                for claim in verdict.attempts[-1].claims
            )
            if verdict.attempts
            else 0
        )
        if injected < assertion.min_injected_claims:
            return False, (
                f"{injected} matched_injected claim(s) < "
                f"{assertion.min_injected_claims}"
            )
    return True, ""


# --- Rep and row grading -------------------------------------------------


def _check_setup(spec: SetupSpec, view: _TurnView) -> bool:
    """Scenario preconditions over the turn's recorded invocations and,
    for a row that measures a drafted answer, its exit: a refusal is
    scenario-not-reached, not an expected failure (W4)."""
    if spec.exit is not None and view.record.exit_equiv not in spec.exit:
        return False
    invocations = view.invocations
    if spec.tool is not None:
        invocations = [
            inv for inv in invocations if inv.tool.value == spec.tool
        ]
    if (
        spec.min_invocations is not None
        and len(invocations) < spec.min_invocations
    ):
        return False
    errored = sum(inv.status == "error" for inv in invocations)
    if spec.min_errored is not None and errored < spec.min_errored:
        return False
    ok = sum(inv.status == "ok" for inv in invocations)
    if spec.min_ok is not None and ok < spec.min_ok:
        return False
    return True


RepOutcome = Literal["passed", "failed", "not-reached"]


def _grade_rep(
    row: BankRow,
    record: RunRecord,
    gold: dict[str, Any] | None,
    breaches: list[BreachRecord],
    documented: list[DocumentedMiss],
) -> tuple[RepOutcome, list[str]]:
    """(rep outcome, failure classes). Breach detection runs on every
    turn with exit 0 — xfail, sentinel, and scenario-not-reached reps
    included — because the invariant outranks every annotation. A rep
    whose setup preconditions fail is not-reached: excluded from the
    pass-rate denominator, neither a pass nor a fail."""
    expected_turns = row.all_turns()
    failures: list[str] = []
    reached = True
    for index, bank_turn in enumerate(expected_turns):
        if index >= len(record.turns):
            if bank_turn.expect.setup is not None:
                reached = False
            failures.append("turn-missing")
            continue
        turn = record.turns[index]
        view = _TurnView(turn)
        expectation = bank_turn.expect
        if expectation.setup is not None and not _check_setup(
            expectation.setup, view
        ):
            reached = False

        exit_ok = turn.exit_equiv in expectation.exit
        if not exit_ok:
            failures.append(
                f"exit({turn.exit_equiv} not in {expectation.exit})"
            )

        for assertion in expectation.assertions:
            applicable = not (
                row.sentinel
                and type(assertion).content
                and turn.exit_equiv != 0
            )
            if not applicable:
                continue
            if (
                assertion.at_exit is not None
                and turn.exit_equiv not in assertion.at_exit
            ):
                continue
            passed, detail = evaluate(assertion, view, gold)
            if passed:
                continue
            if (
                type(assertion).content
                and turn.exit_equiv == 0
                and _alarm_worthy(assertion, view, gold)
            ):
                if assertion.breach:
                    breaches.append(
                        BreachRecord(
                            row_id=row.id,
                            rep=record.rep,
                            turn_index=index,
                            assertion=assertion.kind,
                            severity=_severity(assertion, view),
                            detail=detail,
                            evidence_ref=turn.evidence_ref,
                        )
                    )
                else:
                    # The alarm's blind spot, kept visible: WBV-class,
                    # deliberately non-breaching, threshold-gated.
                    documented.append(
                        DocumentedMiss(
                            row_id=row.id,
                            rep=record.rep,
                            turn_index=index,
                            assertion=assertion.kind,
                            detail=detail,
                        )
                    )
            if assertion.xfail_ref is not None:
                continue  # expected-fail assertion: never gates the rep
            failures.append(assertion.kind)
    if not reached:
        return "not-reached", failures
    return ("failed" if failures else "passed"), failures


def _alarm_worthy(assertion: Assertion, view: _TurnView, gold) -> bool:
    """Whether a content assertion's failure at exit 0 is the
    wrong-but-verified breach.

    Breach is by kind. A missing contains pattern is phrasing, not
    wrong content: both historical false alarms — A1's window literals
    and HN-ERRORS' digit/word gap ("had 0 errors" against
    (no|none|zero|clean)) — were pattern-kind, while both catastrophic
    shapes (S4, S7) were numeric_from_gold, whose semantics are
    untouched. So contains gates the rep and rings no alarm, ever;
    not_contains still breaches, because forbidden content present IS
    wrong content. A window whose only fault is a convention mismatch
    is the other exception: the invariant there is double-guarded
    already (a wrong window that changes the count breaches via
    numeric_from_gold; a wall-clock window breaches here), so a right,
    data-anchored count over a different window fails the rep and
    nothing more."""
    if assertion.kind == "contains":
        return False
    if assertion.kind != "window_data_anchored":
        return True
    forbidden, _, _ = _window_findings(assertion, view, gold)
    return bool(forbidden)


def _severity(assertion: Assertion, view: _TurnView) -> BreachSeverity:
    """Contradicted when the answer holds a competing value the gold
    disagrees with; unsupported when the gold token is merely absent.
    A window breach is always a wall-clock anchor (the missing-literal
    half never alarms), hence contradicted. Reporting only — the exit
    code does not depend on this."""
    if assertion.kind == "numeric_from_gold":
        return "contradicted" if extract_numbers(view.body) else "unsupported"
    if assertion.kind in ("not_contains", "window_data_anchored"):
        return "contradicted"
    return "unsupported"


def _token_notes(
    reps: list[tuple[RunRecord, bool]]
) -> list[str]:
    """Where failure splits cleanly on an emitted token, say so — the
    coin-flip made visible (verdict §7.2)."""
    notes = []
    for field in EmittedTokens.model_fields:
        with_token = [
            passed
            for record, passed in reps
            if any(getattr(t.emitted_tokens, field) for t in record.turns)
        ]
        without = [
            passed
            for record, passed in reps
            if not any(getattr(t.emitted_tokens, field) for t in record.turns)
        ]
        if not with_token or not without:
            continue
        if not any(with_token) and all(without):
            notes.append(
                f"fails exactly when {field} emitted "
                f"({len(with_token)} with, {len(without)} without)"
            )
    return notes


def grade(
    bank: LoadedBank,
    header: RunReportHeader,
    records: list[RunRecord],
    world: World,
    *,
    pack_root,
    report_path: str = "",
) -> GradeReport:
    warnings = preflight(bank, header, pack_world_manifests(pack_root))

    by_row: dict[str, list[RunRecord]] = {}
    for record in records:
        by_row.setdefault(record.row_id, []).append(record)
    unknown = sorted(set(by_row) - set(bank.row_ids()))
    if unknown:
        raise GradeError(
            f"report contains rows the bank does not: {unknown} — "
            f"bank hash matched, so this is a loader bug; refusing."
        )

    breaches: list[BreachRecord] = []
    documented_misses: list[DocumentedMiss] = []
    row_grades: list[RowGrade] = []
    first_tools: dict[str, dict[str, int]] = {}

    for row in bank.rows:
        row_records = sorted(by_row.get(row.id, []), key=lambda r: r.rep)
        if not row_records:
            continue  # not part of this run's --rows selection
        threshold = (
            row.threshold
            if row.threshold is not None
            else bank.config.default_threshold
        )

        gold_values = None
        notes: list[str] = []
        if row.gold is not None:
            try:
                gold_values = run_gold(bank.gold_path(row), world)
            except GoldError as exc:
                row_grades.append(
                    RowGrade(
                        row_id=row.id, category=row.category,
                        reps=len(row_records), passes=0, threshold=threshold,
                        status="rot", xfail_ref=_xfail_ref(row),
                        notes=[str(exc)],
                    )
                )
                continue
            mismatches = compare_expected(
                row.expected_gold or {}, gold_values
            )
            if mismatches:
                row_grades.append(
                    RowGrade(
                        row_id=row.id, category=row.category,
                        reps=len(row_records), passes=0, threshold=threshold,
                        status="rot", xfail_ref=_xfail_ref(row),
                        notes=[f"gold rot: {m}" for m in mismatches],
                    )
                )
                continue

        graded = []
        failure_classes: list[str] = []
        for record in row_records:
            outcome, failures = _grade_rep(
                row, record, gold_values, breaches, documented_misses
            )
            graded.append((record, outcome))
            if outcome == "not-reached":
                continue  # its failures are moot — the scenario never ran
            for failure in failures:
                if failure not in failure_classes:
                    failure_classes.append(failure)

        has_setup = any(
            turn.expect.setup is not None for turn in row.all_turns()
        )
        reached = [
            (record, outcome)
            for record, outcome in graded
            if outcome != "not-reached"
        ]
        passes = sum(outcome == "passed" for _, outcome in reached)
        if has_setup and len(reached) < row.reached_floor:
            # Too few reps produced the scenario to say anything —
            # neither pass nor fail, and never xpass.
            status = "inconclusive"
        else:
            met = passes / len(reached) >= threshold
            if row.xfail is not None:
                status = "xpass" if met else "xfail"
            else:
                status = "ok" if met else "fail"
        notes.extend(
            _token_notes(
                [(record, outcome == "passed") for record, outcome in reached]
            )
        )
        row_grades.append(
            RowGrade(
                row_id=row.id, category=row.category, reps=len(graded),
                passes=passes, threshold=threshold, status=status,
                xfail_ref=_xfail_ref(row), failure_classes=failure_classes,
                notes=notes, reached=len(reached) if has_setup else None,
            )
        )

        if row.route_pair is not None:
            observed = first_tools.setdefault(row.route_pair, {})
            for record, _ in graded:
                if record.turns and record.turns[0].tools_used:
                    first = record.turns[0].tools_used[0]
                    observed[first] = observed.get(first, 0) + 1

    route_pairs = [
        RoutePairGrade(
            pair=pair,
            rows=[r.id for r in bank.rows if r.route_pair == pair],
            observed=observed,
        )
        for pair, observed in sorted(first_tools.items())
    ]

    return GradeReport(
        pack=header.pack,
        report_path=report_path,
        header=header,
        warnings=warnings,
        breaches=breaches,
        documented_misses=documented_misses,
        rows=row_grades,
        route_pairs=route_pairs,
    )


def _xfail_ref(row: BankRow) -> str | None:
    return row.xfail.ref if row.xfail is not None else None
