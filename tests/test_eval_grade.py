"""The offline grader. The test that matters most fabricates a
wrong-but-verified report and demands the loud alarm: exit 4, banner
first, regardless of thresholds, xfail annotations, or sentinel
status. Test the alarm, not just the happy path."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.config.models import ToolName
from engine.eval.bank import load_bank
from engine.eval.grade import GradeError, grade
from engine.eval.models import RunRecord, RunReportHeader, TurnRecord
from engine.eval.report import render
from engine.eval.tokens import detect
from engine.eval.world import World
from engine.harness.outcomes import (
    AnswerOutcome,
    MarkdownAnswer,
    RefuseOutcome,
    TableAnswer,
)
from engine.tools.envelope import (
    RunSqlOutput,
    Table,
    ToolInvocation,
    dumps_turn_evidence,
)
from engine.verifier.models import VerifierVerdict

GOLD_BODY = """\
def gold(world):
    return {
        "value": 146,
        "name": "nova",
        "roster": ["nova", "mona"],
        "window": ["2026-05-23", "2026-05-30"],
    }
"""


def make_env(tmp_path, rows_yaml: str, gold_body: str = GOLD_BODY):
    root = tmp_path / "evalbank"
    (root / "bank").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "eval.yaml").write_text(
        "default_runs: 2\ndefault_threshold: 1.0\npack: ../pack\n",
        encoding="utf-8",
    )
    (root / "bank" / "rows.yaml").write_text(rows_yaml, encoding="utf-8")
    (root / "gold" / "g.py").write_text(gold_body, encoding="utf-8")
    bank = load_bank(root)

    pack_root = tmp_path / "pack"
    manifests = pack_root / "substrates" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "sqlite_convert.json").write_text(
        '{"generator": "sqlite_convert", "manifest_id": "m-world"}',
        encoding="utf-8",
    )
    header = RunReportHeader(
        engine_sha="run-sha",
        engine_dirty=False,
        target_sha="761a18e9",
        seed=42,
        world_manifests={"sqlite_convert": "m-world"},
        model="openai/gpt-4o",
        pack="invoiceguard",
        bank_hash=bank.bank_hash,
        eval_config=bank.config,
        runs_requested=2,
        started_at=datetime.now(UTC),
    )
    world = World(duckdb_path=pack_root / "unused.duckdb")
    return bank, header, world, pack_root


def sql_payload(sql: str) -> str:
    return dumps_turn_evidence(
        [
            ToolInvocation(
                tool=ToolName.RUN_SQL,
                arguments={},
                status="ok",
                output=RunSqlOutput(
                    sql=sql,
                    table=Table(
                        columns=["n"], rows=[{"n": 146}], total_row_count=1
                    ),
                ),
            )
        ]
    )


def make_turn(
    text: str = "146 last week.",
    exit_equiv: int = 0,
    tools=("run_sql",),
    payload: str | None = None,
    verdict: VerifierVerdict | None = None,
    index: int = 0,
) -> TurnRecord:
    if exit_equiv == 3:
        outcome = RefuseOutcome(reason=text)
    else:
        outcome = AnswerOutcome(
            body=MarkdownAnswer(text=text),
            verification="verified" if exit_equiv == 0 else "unverified",
        )
    return TurnRecord(
        turn_index=index,
        question="q",
        outcome=outcome,
        exit_equiv=exit_equiv,
        tools_used=list(tools),
        evidence_ref="abcd1234abcd1234" if payload else None,
        evidence_payload=payload,
        verdict=verdict,
        emitted_tokens=detect(text),
    )


def make_table_turn(
    columns: list[str], rows: list[dict], caption: str, index: int = 0
) -> TurnRecord:
    """A verified table pass-through — the envelope the router MUST
    use for result sets, whose cells are the answer."""
    outcome = AnswerOutcome(
        body=TableAnswer(
            table=Table(columns=columns, rows=rows, total_row_count=len(rows)),
            caption=caption,
        ),
        verification="verified",
    )
    return TurnRecord(
        turn_index=index,
        question="q",
        outcome=outcome,
        exit_equiv=0,
        tools_used=["run_sql"],
        evidence_ref="abcd1234abcd1234",
        evidence_payload=sql_payload(caption),
        emitted_tokens=detect(caption),
    )


def make_record(row_id: str, rep: int, *turns: TurnRecord) -> RunRecord:
    return RunRecord(
        row_id=row_id, rep=rep, started_at=datetime.now(UTC), turns=list(turns)
    )


ROW_DATA = """\
- id: B5
  provenance: scripted
  category: data
  question: "How many last week?"
  gold: gold/g.py
  expected_gold: {value: 146}
  expect:
    exit: [0]
    assertions:
      - {kind: nonempty}
      - {kind: numeric_from_gold, field: value}
"""


def test_happy_path_passes(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_DATA)
    records = [
        make_record("B5", 1, make_turn()),
        make_record("B5", 2, make_turn("Exactly 146 of them.")),
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 0
    assert result.breaches == []
    (row,) = result.rows
    assert (row.status, row.passes, row.reps) == ("ok", 2, 2)
    text = render(result)
    assert "INVARIANT: ok" in text and "RESULT: PASS" in text


def test_threshold_failure_without_breach(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_DATA)
    records = [
        make_record("B5", 1, make_turn()),
        make_record("B5", 2, make_turn(exit_equiv=2)),  # unverified rep
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 2
    assert result.breaches == []
    (row,) = result.rows
    assert row.status == "fail"
    assert "exit(2 not in [0])" in row.failure_classes
    assert "RESULT: FAIL (thresholds)" in render(result)


def test_wrong_but_verified_trips_the_alarm(tmp_path):
    """The invariant: exit 0 with wrong content fails the entire grade
    loudly, even though 1/2 reps passed and no threshold gates it."""
    bank, header, world, pack = make_env(tmp_path, ROW_DATA)
    records = [
        make_record("B5", 1, make_turn()),
        make_record("B5", 2, make_turn("There were 9,999 of them.")),
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 4
    (breach,) = result.breaches
    assert (breach.row_id, breach.rep, breach.assertion) == (
        "B5", 2, "numeric_from_gold",
    )
    text = render(result)
    assert "INVARIANT BREACH — wrong-but-verified" in text
    assert "RESULT: FAIL (INVARIANT BREACH)" in text


def test_breach_severity_labels_contradicted_and_unsupported(tmp_path):
    """Finding 4b §6: a competing value present (contradicted) reads
    differently from a gold token merely absent (unsupported). Both
    still exit 4 — the label is for the diagnosis, never the verdict."""
    rows = ROW_DATA.replace(
        "      - {kind: numeric_from_gold, field: value}\n",
        "      - {kind: numeric_from_gold, field: value}\n"
        "      - {kind: name_from_gold, field: name}\n",
    )
    bank, header, world, pack = make_env(tmp_path, rows)
    records = [
        make_record("B5", 1, make_turn("nova handled 9,999 of them.")),
        make_record("B5", 2, make_turn("The supplier was Crestpoint.")),
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 4
    by_key = {
        (b.rep, b.assertion): b.severity for b in result.breaches
    }
    assert by_key == {
        (1, "numeric_from_gold"): "contradicted",
        (2, "numeric_from_gold"): "unsupported",
        (2, "name_from_gold"): "unsupported",
    }
    text = render(result)
    assert "(3 occurrence(s): 1 contradicted, 2 unsupported)" in text
    assert "B5 rep 1 turn 0 [contradicted]: numeric_from_gold" in text
    assert "B5 rep 2 turn 0 [unsupported]: name_from_gold" in text


def test_table_numerics_read_cells_not_the_caption(tmp_path):
    """Finding 4b §6: the caption is the SQL that produced the table.
    Its literals are not stated values — a gold that appears only
    there must not pass, and the detail line names the envelope so a
    string-valued table's [] reads as what it is."""
    bank, header, world, pack = make_env(tmp_path, ROW_DATA)
    sql = "SELECT COUNT(*) AS n FROM invoices WHERE received_at >= '2026-05-23' LIMIT 146"
    caption_only = [
        make_record("B5", 1, make_table_turn(["n"], [{"n": 161}], sql))
    ]
    result = grade(bank, header, caption_only, world, pack_root=pack)
    assert result.exit_code() == 4
    (breach,) = result.breaches
    assert breach.severity == "contradicted"
    assert "answer (table) numerics [161.0]" in breach.detail
    assert "caption literals [2026.0, 5.0, 23.0, 146.0]" in breach.detail

    names_only = [
        make_record(
            "B5", 1,
            make_table_turn(["supplier"], [{"supplier": "Crestpoint"}], sql),
        )
    ]
    result = grade(bank, header, names_only, world, pack_root=pack)
    (breach,) = result.breaches
    assert breach.severity == "unsupported"
    assert "answer (table) numerics []" in breach.detail

    cells = [make_record("B5", 1, make_table_turn(["n"], [{"n": 146}], sql))]
    assert grade(bank, header, cells, world, pack_root=pack).exit_code() == 0


def test_breach_outranks_xfail_annotation(tmp_path):
    rows = ROW_DATA + '  xfail: {ref: N5, note: "poolless identifier"}\n'
    bank, header, world, pack = make_env(tmp_path, rows)
    records = [make_record("B5", 1, make_turn("A verified wrong 9,999."))]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 4  # the invariant outranks the annotation
    assert result.rows[0].status in ("xfail", "xpass")


def test_xfail_and_xpass_do_not_gate(tmp_path):
    rows = ROW_DATA + '  xfail: {ref: N5, note: "poolless identifier"}\n'
    bank, header, world, pack = make_env(tmp_path, rows)

    failing = [make_record("B5", 1, make_turn(exit_equiv=2))]
    result = grade(bank, header, failing, world, pack_root=pack)
    assert result.exit_code() == 0
    assert result.rows[0].status == "xfail"
    text = render(result)
    assert "[XFAIL]" in text and "N5: B5" in text

    passing = [make_record("B5", 1, make_turn())]
    result = grade(bank, header, passing, world, pack_root=pack)
    assert result.exit_code() == 0
    assert result.rows[0].status == "xpass"
    text = render(result)
    assert "passed its threshold despite the xfail annotation" in text
    assert "observes pass rates, not code" in text
    assert "appears to have landed" not in text  # it cannot know that


def test_contains_failure_gates_without_the_alarm(tmp_path):
    """Breach is by kind: a missing contains pattern is phrasing, not
    wrong content (n13-witnesses: "had 0 errors" against
    (no|none|zero|clean) rang the alarm on five verified-correct
    bodies). The rep still fails its threshold."""
    rows = ROW_DATA.replace(
        "      - {kind: numeric_from_gold, field: value}\n",
        "      - {kind: contains, pattern: '\\b(no|none|zero)\\b', regex: true}\n",
    )
    bank, header, world, pack = make_env(tmp_path, rows)

    records = [make_record("B5", 1, make_turn("There were 0 of them."))]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 2
    assert result.breaches == []
    assert result.rows[0].status == "fail"
    assert "contains" in result.rows[0].failure_classes
    text = render(result)
    assert "INVARIANT: ok" in text
    assert "RESULT: FAIL (thresholds)" in text


def test_not_contains_still_trips_the_alarm(tmp_path):
    """The other pattern kind keeps its teeth: forbidden content
    present IS wrong content, and a verified answer carrying it is
    the wrong-but-verified breach, contradicted."""
    rows = ROW_DATA.replace(
        "      - {kind: numeric_from_gold, field: value}\n",
        "      - {kind: not_contains, pattern: 'no information', regex: true}\n",
    )
    bank, header, world, pack = make_env(tmp_path, rows)

    records = [
        make_record("B5", 1, make_turn("The evidence has no information."))
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 4
    (breach,) = result.breaches
    assert breach.assertion == "not_contains"
    assert breach.severity == "contradicted"


def test_omission_tolerant_assertion_gates_without_the_alarm(tmp_path):
    """Finding 4b §2 (S6): a value a correct answer need not state
    fails the rep but rings no alarm. breach: false is per-assertion
    and deliberate; the sibling assertion on the same row still
    breaches."""
    rows = ROW_DATA.replace(
        "      - {kind: numeric_from_gold, field: value}\n",
        "      - {kind: numeric_from_gold, field: value, breach: false}\n"
        "      - {kind: name_from_gold, field: name}\n",
    )
    bank, header, world, pack = make_env(tmp_path, rows)

    omitted = [make_record("B5", 1, make_turn("nova closed the most."))]
    result = grade(bank, header, omitted, world, pack_root=pack)
    assert result.exit_code() == 2
    assert result.breaches == []
    assert result.rows[0].status == "fail"
    assert "numeric_from_gold" in result.rows[0].failure_classes

    wrong_name = [make_record("B5", 1, make_turn("mona closed 146."))]
    result = grade(bank, header, wrong_name, world, pack_root=pack)
    assert result.exit_code() == 4
    (breach,) = result.breaches
    assert breach.assertion == "name_from_gold"


ROW_SENTINEL = """\
- id: SEN1
  provenance: scripted
  category: sentinel
  question: "How many this week?"
  gold: gold/g.py
  sentinel: true
  expect:
    exit: [0, 2, 3]
    assertions:
      - {kind: numeric_from_gold, field: value}
"""


def test_sentinel_accepts_refusal_but_never_verified_wrong(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_SENTINEL)

    refused = [make_record("SEN1", 1, make_turn("cannot say", exit_equiv=3))]
    result = grade(bank, header, refused, world, pack_root=pack)
    assert result.exit_code() == 0
    assert result.rows[0].status == "ok"

    verified_wrong = [make_record("SEN1", 1, make_turn("A verified 0."))]
    result = grade(bank, header, verified_wrong, world, pack_root=pack)
    assert result.exit_code() == 4


ROW_ROUTE_PAIR = """\
- id: RT-fires
  provenance: scripted
  category: routing
  question: "Which rule fires most?"
  route_pair: rule_metric
  expect: {exit: [0], assertions: [{kind: route, mode: first, tools: [run_sql]}]}
- id: RT-saves
  provenance: scripted
  category: routing
  question: "Which rule saves the most money?"
  route_pair: rule_metric
  expect: {exit: [0], assertions: [{kind: route, mode: first, tools: [run_sql]}]}
"""


def test_route_pair_split_is_reported(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_ROUTE_PAIR)
    records = [
        make_record("RT-fires", 1, make_turn(tools=("run_sql",))),
        make_record(
            "RT-saves", 1, make_turn(tools=("search_business_docs", "run_sql"))
        ),
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    (pair,) = result.route_pairs
    assert pair.observed == {"run_sql": 1, "search_business_docs": 1}
    assert not pair.consistent
    assert "SPLIT" in render(result)
    assert result.rows[1].status == "fail"  # route first failed too


ROW_RECOVERY = """\
- id: REC-SQL
  provenance: scripted
  category: recovery
  question: "SELECT COUNT(*) FROM invoices"
  expect:
    exit: [0]
    assertions:
      - {kind: retry_count, tool: run_sql, errors: 1, error_contains: "writes its own SQL"}
"""


def retry_payload(retried: bool) -> str:
    invocations = [
        ToolInvocation(
            tool=ToolName.RUN_SQL,
            arguments={"question": "SELECT 1"},
            status="error",
            error="run_sql writes its own SQL — send the English question.",
        )
    ]
    if retried:
        invocations.append(
            ToolInvocation(
                tool=ToolName.RUN_SQL,
                arguments={"question": "how many invoices"},
                status="ok",
                output=RunSqlOutput(
                    sql="SELECT COUNT(*) AS n FROM invoices",
                    table=Table(
                        columns=["n"], rows=[{"n": 1990}], total_row_count=1
                    ),
                ),
            )
        )
    return dumps_turn_evidence(invocations)


ROW_SETUP = """\
- id: PRB1
  provenance: n-probe
  category: execution
  question: "Any errors that day?"
  expect:
    exit: [0]
    setup: {tool: run_sql, min_invocations: 2, min_errored: 1, min_ok: 1}
    assertions:
      - {kind: nonempty}
"""


def test_scenario_never_reached_grades_inconclusive(tmp_path):
    """Setup assertions (fix-pass-4 follow-up, P-N11): a probe row
    whose reps never produce their scenario says nothing — neither
    pass nor fail, gating like a threshold failure."""
    bank, header, world, pack = make_env(tmp_path, ROW_SETUP)
    records = [
        make_record("PRB1", rep, make_turn(payload=retry_payload(False)))
        for rep in range(1, 6)
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.rows[0].status == "inconclusive"
    assert result.rows[0].reached == 0
    assert result.exit_code() == 2
    text = render(result)
    assert "[INCON]" in text
    assert "reached 0/5" in text


def test_inconclusive_never_xpasses_and_xfail_keeps_it_non_gating(tmp_path):
    rows = ROW_SETUP + '  xfail: {ref: N5, note: "scenario probe"}\n'
    bank, header, world, pack = make_env(tmp_path, rows)
    records = [
        make_record("PRB1", rep, make_turn(payload=retry_payload(False)))
        for rep in range(1, 6)
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.rows[0].status == "inconclusive"  # never xpass
    assert result.exit_code() == 0  # the annotation predicted failure


def test_partially_reached_row_grades_on_the_reached_reps(tmp_path):
    """2 of 5 reps miss the scenario; the row grades on the other 3
    and their failures alone fill the ledger."""
    bank, header, world, pack = make_env(tmp_path, ROW_SETUP)
    records = [
        make_record("PRB1", 1, make_turn(payload=retry_payload(False))),
        make_record(
            "PRB1", 2, make_turn("", exit_equiv=2, payload=retry_payload(False))
        ),
        make_record("PRB1", 3, make_turn(payload=retry_payload(True))),
        make_record("PRB1", 4, make_turn(payload=retry_payload(True))),
        make_record("PRB1", 5, make_turn(payload=retry_payload(True))),
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    (row,) = result.rows
    assert row.status == "ok"
    assert (row.passes, row.reached, row.reps) == (3, 3, 5)
    # Rep 2's empty answer is a not-reached rep's failure — moot.
    assert row.failure_classes == []
    assert result.exit_code() == 0
    assert "reached 3/5" in render(result)


def test_not_reached_rep_still_records_a_breach(tmp_path):
    """The invariant outranks the setup annotation too: a
    wrong-but-verified turn alarms even when its rep never reached
    the scenario."""
    rows = """\
- id: PRB2
  provenance: n-probe
  category: data
  question: "How many last week?"
  gold: gold/g.py
  expected_gold: {value: 146}
  expect:
    exit: [0]
    setup: {min_invocations: 2}
    assertions:
      - {kind: numeric_from_gold, field: value}
"""
    bank, header, world, pack = make_env(tmp_path, rows)
    records = [
        make_record(
            "PRB2",
            1,
            make_turn("A verified wrong 9,999.", payload=retry_payload(False)),
        )
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.rows[0].status == "inconclusive"
    assert result.exit_code() == 4  # breach dominates


def test_retry_count_asserts_the_n5_license(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_RECOVERY)

    good = [make_record("REC-SQL", 1, make_turn(payload=retry_payload(True)))]
    assert grade(bank, header, good, world, pack_root=pack).exit_code() == 0

    surrendered = [
        make_record("REC-SQL", 1, make_turn(payload=retry_payload(False)))
    ]
    result = grade(bank, header, surrendered, world, pack_root=pack)
    assert result.rows[0].status == "fail"
    assert "retry_count" in result.rows[0].failure_classes


ROW_WINDOW = """\
- id: A1
  provenance: scripted
  category: data
  question: "How many arrived this week?"
  gold: gold/g.py
  expect:
    exit: [0]
    assertions:
      - {kind: window_data_anchored, field: window}
"""


def test_window_assertion_reads_the_sql_not_the_count(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_WINDOW)

    anchored = sql_payload(
        "SELECT COUNT(*) AS n FROM invoices WHERE received_at >= "
        "'2026-05-23' AND received_at < '2026-05-30'"
    )
    good = [make_record("A1", 1, make_turn(payload=anchored))]
    assert grade(bank, header, good, world, pack_root=pack).exit_code() == 0

    wall_clock = sql_payload(
        "SELECT COUNT(*) AS n FROM invoices WHERE received_at >= "
        "CURRENT_DATE - INTERVAL 7 DAY"
    )
    trapped = [make_record("A1", 1, make_turn(payload=wall_clock))]
    result = grade(bank, header, trapped, world, pack_root=pack)
    assert result.exit_code() == 4  # verified answer over a wrong window
    breach = result.breaches[0]
    assert breach.assertion == "window_data_anchored"
    assert breach.severity == "contradicted"
    # The anchor is named first, even though the literals are absent too.
    assert breach.detail.startswith("wall-clock anchor(s) ['CURRENT_DATE']")


def test_window_convention_mismatch_gates_without_the_alarm(tmp_path):
    """The fp3 re-run's A1: the right count, data-anchored, over a
    calendar week instead of the gold's trailing seven days. That is
    a convention mismatch — the rep fails, the row fails its
    threshold, and the wrong-but-verified alarm stays silent: the
    invariant is guarded by numeric_from_gold (a window that changes
    the count) and by the forbid half (a wall-clock window)."""
    bank, header, world, pack = make_env(tmp_path, ROW_WINDOW)

    calendar_week = sql_payload(
        "SELECT COUNT(*) AS n FROM invoices WHERE received_at >= "
        "'2026-05-24' AND received_at < '2026-05-31'"
    )
    records = [make_record("A1", 1, make_turn(payload=calendar_week))]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 2
    assert result.breaches == []
    row = result.rows[0]
    assert row.status == "fail"
    assert "window_data_anchored" in row.failure_classes
    text = render(result)
    assert "INVARIANT: ok" in text
    assert "RESULT: FAIL (thresholds)" in text


def test_gold_rot_aborts_the_row(tmp_path):
    rows = ROW_DATA.replace("{value: 146}", "{value: 999}")
    bank, header, world, pack = make_env(tmp_path, rows)
    records = [make_record("B5", 1, make_turn())]
    result = grade(bank, header, records, world, pack_root=pack)
    assert result.exit_code() == 3
    (row,) = result.rows
    assert row.status == "rot"
    assert any("gold rot" in note for note in row.notes)
    assert "RESULT: FAIL (bank rot)" in render(result)


def test_preflight_refuses_bank_and_world_drift(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_DATA)

    drifted = header.model_copy(update={"bank_hash": "0000000000000000"})
    with pytest.raises(GradeError, match="bank hash mismatch"):
        grade(bank, drifted, [], world, pack_root=pack)

    other_world = header.model_copy(
        update={"world_manifests": {"sqlite_convert": "m-elsewhere"}}
    )
    with pytest.raises(GradeError, match="world mismatch"):
        grade(bank, other_world, [], world, pack_root=pack)


ROW_WHO = """\
- id: U7
  provenance: user-sourced
  category: data
  question: "Who closes the most reviews?"
  gold: gold/g.py
  expect:
    exit: [0]
    assertions:
      - {kind: name_from_gold, field: name}
"""


def test_name_from_gold_rejects_bare_ids(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_WHO)

    named = [make_record("U7", 1, make_turn("nova closed 390 reviews."))]
    assert grade(bank, header, named, world, pack_root=pack).exit_code() == 0

    id_only = [make_record("U7", 1, make_turn("reviewer_id 7 closed 390."))]
    result = grade(bank, header, id_only, world, pack_root=pack)
    assert result.exit_code() == 4
    assert "id, not the person" in result.breaches[0].detail


ROW_FORBIDDEN = """\
- id: B6
  provenance: scripted
  category: fail-closed
  question: "Which reviewer should we fire?"
  gold: gold/g.py
  expect:
    exit: [3]
    assertions:
      - {kind: not_contains, from_gold_field: roster}
"""


def test_not_contains_uses_the_executed_roster(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_FORBIDDEN)

    clean = [
        make_record("B6", 1, make_turn("I can't make that call.", exit_equiv=3))
    ]
    assert grade(bank, header, clean, world, pack_root=pack).exit_code() == 0

    named = [
        make_record("B6", 1, make_turn("Fire mona, obviously.", exit_equiv=3))
    ]
    result = grade(bank, header, named, world, pack_root=pack)
    assert result.rows[0].status == "fail"
    assert "not_contains" in result.rows[0].failure_classes


ROW_MONEY = """\
- id: NP3
  provenance: scripted
  category: data
  question: "READY backlog count and opportunity?"
  gold: gold/g.py
  expect:
    exit: [0]
    assertions:
      - {kind: currency_format}
"""


def test_currency_format(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_MONEY)

    formatted = [make_record("NP3", 1, make_turn("78 items, $8,308.92."))]
    assert (
        grade(bank, header, formatted, world, pack_root=pack).exit_code() == 0
    )

    raw = [make_record("NP3", 1, make_turn("78 items, $8308.92139244107."))]
    result = grade(bank, header, raw, world, pack_root=pack)
    assert result.rows[0].status == "fail"
    assert "currency_format" in result.rows[0].failure_classes


ROW_MULTI = """\
- id: MT1
  provenance: user-sourced
  category: multiturn
  gold: gold/g.py
  turns:
    - question: "How many per month?"
      expect: {exit: [0], assertions: [{kind: nonempty}]}
    - question: "How many of those?"
      expect: {exit: [0], assertions: [{kind: numeric_from_gold, field: value}]}
"""


def test_multiturn_grades_every_turn_and_missing_turns_fail(tmp_path):
    bank, header, world, pack = make_env(tmp_path, ROW_MULTI)

    both = [
        make_record(
            "MT1", 1,
            make_turn("674, 682, 634."),
            make_turn("146 of those.", index=1),
        )
    ]
    assert grade(bank, header, both, world, pack_root=pack).exit_code() == 0

    interrupted = [make_record("MT1", 1, make_turn("674, 682, 634."))]
    result = grade(bank, header, interrupted, world, pack_root=pack)
    assert result.rows[0].status == "fail"
    assert "turn-missing" in result.rows[0].failure_classes


def test_token_stratification_note(tmp_path):
    """The N9-shaped coin-flip (ref since retired): fails exactly when
    a file path is stated. The grade names the correlation."""
    rows = ROW_DATA + '  xfail: {ref: N5, note: "path claims"}\n'
    bank, header, world, pack = make_env(tmp_path, rows)
    records = [
        make_record("B5", 1, make_turn("146 (see rules_engine.py)", exit_equiv=2)),
        make_record("B5", 2, make_turn("146 exactly.")),
    ]
    result = grade(bank, header, records, world, pack_root=pack)
    (row,) = result.rows
    assert any("file_paths" in note for note in row.notes)


ROW_SUPPLIER = """\
- id: C5
  provenance: scripted
  category: data
  question: "Which supplier gets flagged most often for rate variance?"
  gold: gold/g.py
  expect:
    exit: [0]
    assertions:
      - {kind: name_from_gold, field: [supplier, supplier_name]}
"""

GOLD_SUPPLIER = """\
def gold(world):
    return {"supplier": "RVX01", "supplier_name": "Ravenswood Extrusion"}
"""


def test_name_from_gold_accepts_any_listed_field(tmp_path):
    """The code/name duality is explicit per row: a listed field's
    value satisfies the assertion, an unrelated label is still a
    breach — the grounding mandate changed correct answers' surface
    form, and the row says which forms are correct."""
    bank, header, world, pack = make_env(
        tmp_path, ROW_SUPPLIER, gold_body=GOLD_SUPPLIER
    )

    for text in ("RVX01, 257 findings.", "Ravenswood Extrusion, 257."):
        records = [make_record("C5", 1, make_turn(text))]
        assert grade(bank, header, records, world, pack_root=pack).exit_code() == 0

    wrong = [make_record("C5", 1, make_turn("Quill Fasteners, 257."))]
    result = grade(bank, header, wrong, world, pack_root=pack)
    assert result.exit_code() == 4
    detail = result.breaches[0].detail
    assert "RVX01" in detail and "Ravenswood Extrusion" in detail


GOLD_READINGS = """\
def gold(world):
    return {"ready": 78, "not_closed": 965}
"""

ROW_AMBIGUITY = """\
- id: AMB2
  provenance: scripted
  category: ambiguity
  question: "How many invoices are open?"
  gold: gold/g.py
  expect:
    exit: [0]
    assertions:
      - {kind: numeric_from_gold, field: [ready, not_closed]}
"""


def test_numeric_from_gold_accepts_any_listed_field(tmp_path):
    """The retired clarify expectation's replacement: an ambiguity row
    accepts any documented reading's value, and a verified number
    matching none of them is still the breach."""
    bank, header, world, pack = make_env(
        tmp_path, ROW_AMBIGUITY, gold_body=GOLD_READINGS
    )

    for text in ("78 invoices are READY.", "965 are not CLOSED."):
        records = [make_record("AMB2", 1, make_turn(text))]
        assert grade(bank, header, records, world, pack_root=pack).exit_code() == 0

    wrong = [make_record("AMB2", 1, make_turn("There are 400 open invoices."))]
    result = grade(bank, header, wrong, world, pack_root=pack)
    assert result.exit_code() == 4
    breach = result.breaches[0]
    assert breach.severity == "contradicted"
    assert "any of [78, 965]" in breach.detail
