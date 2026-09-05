"""engine eval exposure (eval/exposure.py): today's guards replayed over
a committed report, offline, every hit attributed. The guard pass's
rule made this a verb so the next bound or lint is measured against
the last run before it lands, not rebuilt as a scratch script."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine import cli
from engine.config.models import PlausibilitySettings
from engine.eval.exposure import ExposureError, check_world, expose, render_exposure
from engine.eval.models import RunRecord, RunReportHeader, TurnRecord
from engine.substrates.models import DictionaryMap, DocProvenance
from engine.tools.envelope import RunSqlEvidence, RunSqlOutput, SqlAttempt, Table, ToolInvocation, dumps_turn_evidence
from tests.test_tool_enum_lint import DICTIONARY as ENUM_DICTIONARY
from tests.test_tool_enum_lint import R_A
from tests.verifier_support import sql_invocation, stats_row

ROOT = Path(__file__).resolve().parents[1]
POST_DURATION = ROOT / "evals" / "invoiceguard" / "reports" / "2026-09-02-post-duration.jsonl"
POST_BLOCK4 = ROOT / "evals" / "invoiceguard" / "reports" / "2026-09-04-post-block4.jsonl"
POST_CLOSE = ROOT / "evals" / "invoiceguard" / "reports" / "2026-09-04-post-close.jsonl"
POST_BACKLOG = ROOT / "evals" / "invoiceguard" / "reports" / "2026-09-04-post-backlog.jsonl"
POST_RIDER = ROOT / "evals" / "invoiceguard" / "reports" / "2026-09-04-post-rider.jsonl"
PACK = ROOT / "packs" / "invoiceguard"

EMPTY_MAP = DictionaryMap(
    provenance=DocProvenance(source="machine", confidence=0.5, needs_validation=True),
    join_paths=[],
)
STATS = [
    stats_row("invoices", "id", row_count=1990),
    stats_row("invoice_history", "id", row_count=8345),
]
AMB2_HISTORY = (
    "SELECT COUNT(*) AS invoice_count FROM invoice_history "
    "WHERE to_status IN ('RECEIVED', 'READY', 'CLAIMED', 'IN_REVIEW')"
)


def _record(row_id: str, rep: int, *invocations) -> RunRecord:
    return RunRecord(
        row_id=row_id,
        rep=rep,
        started_at=datetime.now(UTC),
        turns=[
            TurnRecord(
                turn_index=0,
                question="q",
                exit_equiv=0,
                evidence_payload=dumps_turn_evidence(list(invocations)),
            )
        ],
    )


def test_every_executed_statement_faces_the_verifier_and_the_lints():
    walked = sql_invocation(AMB2_HISTORY, [{"invoice_count": 6432}])
    fine = sql_invocation(
        "SELECT COUNT(*) AS invoice_count FROM invoices WHERE status = 'READY'",
        [{"invoice_count": 78}],
    )
    rejected = sql_invocation(R_A, [{"reviewer": "finch", "rejection_count": 0}])
    report = expose(
        [_record("AMB2", 1, walked), _record("AMB2", 4, fine), _record("R-A", 2, rejected)],
        stats=STATS,
        dictionary=ENUM_DICTIONARY,
        dictionary_map=EMPTY_MAP,
        settings=PlausibilitySettings(),
        report_path="synthetic.jsonl",
    )
    assert report.statements == 3
    assert [(h.row_id, h.rep, h.check, h.severity) for h in report.hits] == [
        ("AMB2", 1, "run_sql.entity_count_exceeds_table", "warn"),
        ("R-A", 2, "lint.enum_literal", "challenge"),
    ]
    assert report.counts() == {
        "lint.enum_literal": 1,
        "run_sql.entity_count_exceeds_table": 1,
    }
    text = render_exposure(report)
    assert text.startswith("Eval exposure — report: synthetic.jsonl · statements: 3")
    assert "AMB2 rep 1 turn 0 [warn]: invoice_count = 6,432" in text
    assert "R-A rep 2 turn 0 [challenge]: Enum check:" in text
    assert "SELECT COUNT(*) AS invoice_count FROM invoice_history" in text


def test_a_requested_check_with_no_hits_is_listed_at_zero():
    """Silence is the finding a new guard is measured by."""
    fine = sql_invocation("SELECT COUNT(*) AS n FROM invoices", [{"n": 1990}])
    report = expose(
        [_record("REC-SQL", 1, fine)],
        stats=STATS,
        dictionary=[],
        dictionary_map=EMPTY_MAP,
        settings=PlausibilitySettings(),
        checks=["run_sql.entity_count_exceeds_table"],
    )
    assert report.hits == []
    assert report.counts() == {"run_sql.entity_count_exceeds_table": 0}
    assert "run_sql.entity_count_exceeds_table  0" in render_exposure(report)
    # Nothing requested and nothing found reads as such.
    assert "(no hits)" in render_exposure(expose(
        [_record("REC-SQL", 1, fine)], stats=STATS, dictionary=[],
        dictionary_map=EMPTY_MAP, settings=PlausibilitySettings(),
    ))


def test_a_report_from_another_world_is_refused():
    header = RunReportHeader(
        engine_sha="a" * 40, engine_dirty=False, target_sha=None, seed=42,
        world_manifests={"stats": "111"}, model="m", pack="p", bank_hash="b" * 16,
        eval_config={"pack": "../pack"}, runs_requested=1,
        started_at=datetime.now(UTC),
    )
    check_world(header, {"stats": "111"})
    with pytest.raises(ExposureError, match="world mismatch"):
        check_world(header, {"stats": "222"})


@pytest.mark.skipif(not POST_DURATION.is_file(), reason="the committed report is absent")
def test_the_post_duration_report_exposes_exactly_the_three_amb2_statements():
    """The guard pass's evidence, pinned: the entity-count bound over
    the report that breached, 208 executed statements, three hits —
    AMB2 reps 1–3 and nothing else. A future guard is measured the
    same way, and this row is what its attribution must still say."""
    from engine.config.pack_loader import load_pack
    from engine.eval.runner import load_report
    from engine.substrates.jsonl import read_rows
    from engine.substrates.models import DictionaryRow, StatsRow
    from engine.substrates.pack_data import load_dictionary_map

    pack = load_pack(ROOT / "packs" / "invoiceguard")
    _, records = load_report(POST_DURATION)
    report = expose(
        records,
        stats=read_rows(pack.root / "substrates" / "univariate_stats.jsonl", StatsRow),
        dictionary=read_rows(pack.root / "substrates" / "dictionary.jsonl", DictionaryRow),
        dictionary_map=load_dictionary_map(pack.root / "dictionary_map.yaml"),
        settings=pack.config.verifier.plausibility,
        checks=["run_sql.entity_count_exceeds_table", "lint.enum_literal"],
    )
    assert report.statements == 208
    assert [(h.row_id, h.rep, h.check) for h in report.hits] == [
        ("AMB2", 1, "run_sql.entity_count_exceeds_table"),
        ("AMB2", 2, "run_sql.entity_count_exceeds_table"),
        ("AMB2", 3, "run_sql.entity_count_exceeds_table"),
    ]
    assert [h.detail.split(", but")[0] for h in report.hits] == [
        "invoice_count = 6,432", "invoice_count = 5,199", "invoice_count = 5,199",
    ]
    # The narrowed enum lint: no executed statement in the run draws it
    # today (the mixed IN lists that drew the old one are silent).
    assert report.counts()["lint.enum_literal"] == 0


@pytest.mark.skipif(not POST_DURATION.is_file(), reason="the committed report is absent")
def test_the_cli_verb_runs_offline_against_the_committed_report(capsys, tmp_path):
    out = tmp_path / "exposure.txt"
    code = cli.main([
        "eval", "exposure",
        "--bank", str(ROOT / "evals" / "invoiceguard"),
        "--report", str(POST_DURATION),
        "--check", "run_sql.entity_count_exceeds_table",
        "--out", str(out),
    ])
    assert code == 0
    text = capsys.readouterr().out
    assert "statements: 208" in text
    assert "run_sql.entity_count_exceeds_table  3" in text
    assert text.count("AMB2 rep") == 3
    assert out.read_text(encoding="utf-8") == text


# --- Polish Pass: a work store's conversation, measured like a report ---


def _seed_conversation(store, invocations, substrate_versions):
    """One logged turn with a run_sql bundle, written through the port
    exactly as a browser turn lands (no graph needed)."""
    from datetime import UTC, datetime

    from engine.harness.session import evidence_ref_of
    from engine.ports.types import TurnLogEntry

    store.ensure_schema()
    workspace = store.create_workspace("dev", "scratch")
    conversation = store.create_conversation(workspace.id, "browser")
    payload = dumps_turn_evidence(list(invocations))
    ref = evidence_ref_of(payload)
    store.save_evidence_bundle(ref, payload)
    store.append_turn_log(
        TurnLogEntry(
            conversation_id=conversation.id,
            turn=1,
            actor="dev",
            action="ask",
            tools_used=["run_sql"],
            evidence_bundle_ref=ref,
            substrate_versions=substrate_versions,
            question="how many open?",
            created_at=datetime.now(UTC),
        )
    )
    return conversation


def test_a_work_store_conversation_is_measured_like_a_report(tool_pack):
    from engine.config.models import PortName
    from engine.eval.exposure import work_store_statements
    from tests.harness_support import build_ask_session

    _, ports, _ = build_ask_session(tool_pack, [])
    store = ports.get(PortName.WORK_STORE)
    walked = sql_invocation(AMB2_HISTORY, [{"invoice_count": 6432}])
    conversation = _seed_conversation(store, [walked], ["stats-1", "dict-1"])
    world = {"stats": "stats-1", "dictionary": "dict-1", "ckg": "ckg-1"}

    statements = work_store_statements(store, [conversation.id], world)
    assert [(s.row_id, s.rep, s.turn_index, s.invocation_index) for s in statements] == [
        (f"conv{conversation.id}", 1, 1, 0)
    ]
    report = expose(
        statements,
        stats=STATS,
        dictionary=ENUM_DICTIONARY,
        dictionary_map=EMPTY_MAP,
        settings=PlausibilitySettings(),
        report_path=f"work store · conversation {conversation.id}",
    )
    assert report.statements == 1
    assert [(h.row_id, h.check) for h in report.hits] == [
        (f"conv{conversation.id}", "run_sql.entity_count_exceeds_table")
    ]
    assert f"conv{conversation.id} rep 1 turn 1 [warn]" in render_exposure(report)

    # A turn that ran against a manifest this world lacks is refused,
    # as a report from another world is; a missing conversation too.
    with pytest.raises(ExposureError, match="world mismatch"):
        work_store_statements(store, [conversation.id], {"stats": "stats-2"})
    with pytest.raises(ExposureError, match="does not exist"):
        work_store_statements(store, [conversation.id + 1], world)


def test_the_cli_verb_replays_the_packs_work_store(tool_pack, tmp_path, capsys):
    import yaml

    from engine.config.models import PortName
    from engine.eval.grade import pack_world_manifests
    from tests.harness_support import build_ask_session

    config_path = tool_pack / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["adapters"]["work_store"]["settings"]["database"] = str(tmp_path / "work.db")
    config_path.write_text(yaml.safe_dump(config))
    _, ports, _ = build_ask_session(tool_pack, [])
    store = ports.get(PortName.WORK_STORE)
    fine = sql_invocation("SELECT COUNT(*) AS n FROM invoices", [{"n": 50}])
    conversation = _seed_conversation(
        store, [fine], list(pack_world_manifests(tool_pack).values())
    )

    code = cli.main([
        "eval", "exposure",
        "--bank", str(ROOT / "evals" / "invoiceguard"),
        "--pack", str(tool_pack),
        "--work-store", "--conversation", str(conversation.id),
        "--check", "lint.fan_out",
    ])
    assert code == 0
    text = capsys.readouterr().out
    assert text.startswith(
        f"Eval exposure — report: work store · conversation {conversation.id} · statements: 1"
    )
    assert "lint.fan_out  0" in text

    # Exactly one source, and a conversation with it.
    assert cli.main(["eval", "exposure", "--bank", str(ROOT / "evals" / "invoiceguard")]) == 1
    assert "exactly one of --report or --work-store" in capsys.readouterr().err
    assert cli.main([
        "eval", "exposure", "--bank", str(ROOT / "evals" / "invoiceguard"),
        "--pack", str(tool_pack), "--work-store",
    ]) == 1
    assert "--conversation" in capsys.readouterr().err


@pytest.mark.skipif(not POST_BLOCK4.is_file(), reason="the committed report is absent")
def test_the_post_block4_report_exposes_exactly_the_s2_line_grain_statements():
    """The Close Pass's evidence, pinned: the CTE-aware fan-out lint over
    the report it was measured against — 237 executed statements,
    fifteen challenges before (AMB1 x5, W-F x5, U-WHO x3, S2 x2), four
    after, all S2: the CTE pair on the undeclared line-grain key (reps
    1/3) and the fan hidden in a CTE and counted outside (reps 2/4),
    which was silent. The next lint is measured against this."""
    from engine.config.pack_loader import load_pack
    from engine.eval.runner import load_report
    from engine.substrates.jsonl import read_rows
    from engine.substrates.models import DictionaryRow, StatsRow
    from engine.substrates.pack_data import load_dictionary_map

    pack = load_pack(ROOT / "packs" / "invoiceguard")
    _, records = load_report(POST_BLOCK4)
    report = expose(
        records,
        stats=read_rows(pack.root / "substrates" / "univariate_stats.jsonl", StatsRow),
        dictionary=read_rows(pack.root / "substrates" / "dictionary.jsonl", DictionaryRow),
        dictionary_map=load_dictionary_map(pack.root / "dictionary_map.yaml"),
        settings=pack.config.verifier.plausibility,
        checks=["lint.fan_out"],
    )
    assert report.statements == 237
    assert [(h.row_id, h.rep, h.check) for h in report.hits] == [
        ("S2", rep, "lint.fan_out") for rep in (1, 2, 3, 4)
    ]
    hidden = "Fan-out check: COUNT(*) reads flagged_lines, whose rows are invoice_lines"
    assert [h.detail.startswith(hidden) for h in report.hits] == [False, True, False, True]
    assert all("EXISTS rather than a LEFT JOIN" in h.detail for h in report.hits)


# --- Backlog Pass: the turn is the unit, and the conversation grounds it ---


def _conversation(row_id: str, turns: list[tuple[str, object, list]]) -> RunRecord:
    """A rep of `turns`: (question, outcome, invocations) each."""
    return RunRecord(
        row_id=row_id,
        rep=1,
        started_at=datetime.now(UTC),
        turns=[
            TurnRecord(
                turn_index=index,
                engine_turn=index + 1,
                question=question,
                outcome=outcome,
                exit_equiv=0,
                evidence_payload=dumps_turn_evidence(list(invocations)),
            )
            for index, (question, outcome, invocations) in enumerate(turns)
        ],
    )


def _pack_substrates():
    from engine.substrates.jsonl import read_rows
    from engine.substrates.models import DictionaryRow, StatsRow
    from engine.substrates.pack_data import load_dictionary_map

    return (
        read_rows(PACK / "substrates" / "univariate_stats.jsonl", StatsRow),
        read_rows(PACK / "substrates" / "dictionary.jsonl", DictionaryRow),
        load_dictionary_map(PACK / "dictionary_map.yaml"),
    )


def _table_outcome(invocation, about=""):
    from engine.harness.outcomes import AnswerOutcome, TableAnswer

    return AnswerOutcome(
        body=TableAnswer(table=invocation.output.table, caption=invocation.output.sql, about=about),
        verification="verified",
    )


def test_the_sessions_turn_7_is_exposed_by_the_anchor_check_with_no_statement():
    """Turn 6 established line_note; turn 7 searched the docs and wrote
    about new_supplier. No run_sql statement at turn 7 — the turn-level
    arm reads the answer against the turn before it."""
    from engine.harness.outcomes import AnswerOutcome, MarkdownAnswer
    from tests.test_tool_entities import T6
    from tests.test_verifier_anchor import TURN_7_TEXT

    stats, dictionary, dictionary_map = _pack_substrates()
    record = _conversation("SESSION", [
        ("Which rule fires most often?", _table_outcome(T6), [T6]),
        ("What does that rule check?", AnswerOutcome(body=MarkdownAnswer(text=TURN_7_TEXT), verification="verified"), []),
    ])
    report = expose(
        [record], stats=stats, dictionary=dictionary, dictionary_map=dictionary_map,
        settings=PlausibilitySettings(), checks=["anchor.entity_mismatch", "lint.ungrounded_key", "lint.placeholder"],
    )
    assert report.statements == 1
    assert [(h.turn_index, h.invocation_index, h.check, h.severity) for h in report.hits] == [
        (1, -1, "anchor.entity_mismatch", "warn")
    ]
    assert "turn 1's evidence established `line_note`" in report.hits[0].detail
    assert report.counts() == {
        "anchor.entity_mismatch": 1, "lint.ungrounded_key": 0, "lint.placeholder": 0,
    }
    text = render_exposure(report)
    assert "SESSION rep 1 turn 1 [warn]:" in text
    assert "(question: What does that rule check?)" in text


def test_the_sessions_turn_20_is_exposed_by_both_key_lints_and_grounded_by_turn_19():
    """Turn 19 showed INV-00002 and no id; turn 20 bound 123 with the
    comment. Both lints fire on the statement; the anchor check is
    silent (invoices.id is a column the anchor never carried)."""
    from tests.test_tool_entities import T19
    from tests.test_tool_key_lint import T20_PLACEHOLDER_ATTEMPT

    stats, dictionary, dictionary_map = _pack_substrates()
    rows = [
        {"from_status": None, "to_status": "RECEIVED", "actor": "invoice-parse", "transition_time": "2026-03-05 08:00:00"},
        {"from_status": "RECEIVED", "to_status": "READY", "actor": "system.rollup", "transition_time": "2026-03-05 09:00:00"},
        {"from_status": "READY", "to_status": "LAPSED", "actor": "system.stale-sweep", "transition_time": "2026-03-12 18:00:00"},
    ]
    t20 = ToolInvocation(
        tool="run_sql", arguments={"question": "history of the invoice from the previous query"}, status="ok",
        output=RunSqlOutput(
            sql=T20_PLACEHOLDER_ATTEMPT,
            table=Table(columns=list(rows[0]), rows=rows, total_row_count=3),
        ),
        evidence=RunSqlEvidence(grounding_prompt="", attempts=[
            SqlAttempt(raw_response=f"```sql\n{T20_PLACEHOLDER_ATTEMPT}\n```", sql=T20_PLACEHOLDER_ATTEMPT, row_count=3),
        ]),
    )
    record = _conversation("SESSION", [
        ("Show me an example invoice for it.", _table_outcome(T19), [T19]),
        ("What was that invoice's history?", _table_outcome(t20), [t20]),
    ])
    report = expose(
        [record], stats=stats, dictionary=dictionary, dictionary_map=dictionary_map,
        settings=PlausibilitySettings(),
        checks=["lint.placeholder", "lint.ungrounded_key", "anchor.entity_mismatch"],
    )
    assert report.statements == 2
    assert [(h.turn_index, h.invocation_index, h.check) for h in report.hits] == [
        (1, 0, "lint.placeholder"),
        (1, 0, "lint.ungrounded_key"),
    ]
    assert "Replace 123 with the actual invoice ID" in report.hits[0].detail
    assert "`invoice_history.invoice_id = 123` — 123 appears in no result" in report.hits[1].detail
    # The same statement after turn 19 had shown the id is grounded.
    shown = ToolInvocation(
        tool="run_sql", arguments={"question": "q"}, status="ok",
        output=RunSqlOutput(sql="SELECT i.id AS invoice_id FROM invoices i ORDER BY i.id LIMIT 1",
                            table=Table(columns=["invoice_id"], rows=[{"invoice_id": 123}], total_row_count=1)),
    )
    grounded = expose(
        [_conversation("SESSION", [("an invoice", _table_outcome(shown), [shown]),
                                    ("What was that invoice's history?", _table_outcome(t20), [t20])])],
        stats=stats, dictionary=dictionary, dictionary_map=dictionary_map,
        settings=PlausibilitySettings(), checks=["lint.ungrounded_key"],
    )
    assert grounded.counts() == {"lint.ungrounded_key": 0}


def test_a_warned_turns_drift_and_a_refusals_evidence_never_become_the_anchor():
    """Fix Pass R1(a), replayed: turn 6 established line_note; a "that
    rule" turn filtered new_supplier and was warned; a refusal ran the
    same statement. Neither establishes anything, so a later "that rule"
    answered about line_note is silent — before the pass, the warned
    turn's filter was the most recent anchor and the correct answer
    would have been flagged."""
    from engine.harness.outcomes import AnswerOutcome, MarkdownAnswer, RefuseOutcome
    from tests.test_tool_entities import T6, T9

    stats, dictionary, dictionary_map = _pack_substrates()
    correct = AnswerOutcome(
        body=MarkdownAnswer(text="The line_note rule flags a line that carries a note.", about="line_note"),
        verification="verified",
    )
    record = _conversation("SESSION", [
        ("Which rule fires most often?", _table_outcome(T6), [T6]),
        ("How many findings has that rule produced?", _table_outcome(T9, about="new_supplier"), [T9]),
        ("Show me that rule's source", RefuseOutcome(reason="no node"), [T9]),
        ("What does that rule check?", correct, []),
    ])
    report = expose(
        [record], stats=stats, dictionary=dictionary, dictionary_map=dictionary_map,
        settings=PlausibilitySettings(), checks=["anchor.entity_mismatch"],
    )
    assert [(h.turn_index, h.check) for h in report.hits] == [(1, "anchor.entity_mismatch")]
    assert "this answer says it is about `new_supplier`" in report.hits[0].detail


def test_the_sessions_turn_9_is_exposed_after_turn_7s_warning_and_turn_8s_refusal():
    """Fix Pass R1(b′): the session's own shape. Turn 6 line_note; turn
    7 warned (prose about new_supplier); turn 8 refused; turn 9 "How
    many findings has it produced?" counted new_supplier — the second
    hit the dev-store replay now shows. Turn 10 establishes an auditor
    and closes the window, so a later "it" faces no check."""
    from engine.harness.outcomes import AnswerOutcome, MarkdownAnswer, RefuseOutcome
    from tests.test_tool_entities import T6, T9, invocation
    from tests.test_verifier_anchor import TURN_7_TEXT

    stats, dictionary, dictionary_map = _pack_substrates()
    closer = invocation(
        "SELECT ih.actor AS actor, COUNT(*) AS closed FROM invoice_history ih WHERE ih.to_status = 'CLOSED' "
        "GROUP BY ih.actor ORDER BY closed DESC LIMIT 1",
        ["actor", "closed"], [{"actor": "nova", "closed": 300}],
    )
    later = invocation("SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'new_supplier'", ["n"], [{"n": 197}])
    record = _conversation("SESSION", [
        ("Which rule fires most often?", _table_outcome(T6), [T6]),
        ("What does that rule check?", AnswerOutcome(body=MarkdownAnswer(text=TURN_7_TEXT), verification="verified"), []),
        ("Show me its source", RefuseOutcome(reason="no CKG node"), []),
        ("How many findings has it produced?", _table_outcome(T9), [T9]),
        ("Who closes the most reviews?", _table_outcome(closer), [closer]),
        ("How many findings has it produced?", _table_outcome(later), [later]),
    ])
    report = expose(
        [record], stats=stats, dictionary=dictionary, dictionary_map=dictionary_map,
        settings=PlausibilitySettings(), checks=["anchor.entity_mismatch"],
    )
    assert [(h.turn_index, h.invocation_index) for h in report.hits] == [(1, -1), (3, -1)]
    assert report.hits[0].detail.endswith("and this answer never names it")
    assert report.hits[1].detail == (
        "the question's pronoun follows turn 2's anchor warning; turn 1's evidence "
        "established `line_note`, and this answer filters on `findings.rule_name = 'new_supplier'`"
    )


@pytest.mark.skipif(not POST_BACKLOG.is_file(), reason="the committed report is absent")
def test_the_post_backlog_report_exposes_exactly_the_recorded_breach_turn():
    """The Fix Pass's baseline, after it: 262 executed statements; the
    five MT-KEY warns are gone (the declared about wore its kind noun);
    what remains is MT-ANCHOR rep 4 — its turn-2 warn as recorded, and
    its turn 3, the recorded breach, now read through the window that
    warn opened. Six hits before the pass, two after."""
    import json

    from engine.eval.grade import pack_world_manifests
    from engine.eval.models import RunRecord, RunReportHeader

    stats, dictionary, dictionary_map = _pack_substrates()
    lines = POST_BACKLOG.read_text(encoding="utf-8").splitlines()
    header = RunReportHeader.model_validate(json.loads(lines[0]))
    check_world(header, pack_world_manifests(PACK))
    records = [RunRecord.model_validate(json.loads(line)) for line in lines[1:] if line.strip()]
    report = expose(
        records, stats=stats, dictionary=dictionary, dictionary_map=dictionary_map,
        settings=PlausibilitySettings(),
        checks=["lint.placeholder", "lint.ungrounded_key", "anchor.entity_mismatch"],
    )
    assert report.statements == 262
    assert report.counts() == {
        "lint.placeholder": 0,
        "lint.ungrounded_key": 0,
        "anchor.entity_mismatch": 2,
    }
    warned, breached = report.hits
    assert (warned.row_id, warned.rep, warned.turn_index, warned.invocation_index) == ("MT-ANCHOR", 4, 1, -1)
    assert warned.detail == (
        "the question refers to that rule; turn 1's evidence established `line_note`, "
        "and this answer never names it"
    )
    assert (breached.row_id, breached.rep, breached.turn_index, breached.invocation_index) == ("MT-ANCHOR", 4, 2, -1)
    assert breached.detail == (
        "the question's pronoun follows turn 2's anchor warning; turn 1's evidence "
        "established `line_note`, and this answer says it is about `new_supplier`"
    )


@pytest.mark.skipif(not POST_CLOSE.is_file(), reason="the committed report is absent")
def test_the_post_close_report_is_clean_under_the_backlog_pass_checks():
    """The baseline the Backlog Pass was measured against before it
    landed: 235 executed statements, no comment, no literal id filter,
    no follow-up answered about another entity — zero hits, all three."""
    import json

    from engine.eval.grade import pack_world_manifests
    from engine.eval.models import RunRecord, RunReportHeader

    stats, dictionary, dictionary_map = _pack_substrates()
    lines = POST_CLOSE.read_text(encoding="utf-8").splitlines()
    header = RunReportHeader.model_validate(json.loads(lines[0]))
    check_world(header, pack_world_manifests(PACK))
    records = [RunRecord.model_validate(json.loads(line)) for line in lines[1:] if line.strip()]
    report = expose(
        records, stats=stats, dictionary=dictionary, dictionary_map=dictionary_map,
        settings=PlausibilitySettings(),
        checks=["lint.placeholder", "lint.ungrounded_key", "anchor.entity_mismatch"],
    )
    assert report.statements == 235
    assert report.counts() == {
        "lint.placeholder": 0,
        "lint.ungrounded_key": 0,
        "anchor.entity_mismatch": 0,
    }


@pytest.mark.skipif(not POST_RIDER.is_file(), reason="the committed report is absent")
def test_the_post_rider_report_reads_the_join_echo_and_keeps_the_drift_warns():
    """Rider 2's evidence, pinned: 265 executed statements; ten anchor
    hits before the pass — MT-KEY's turn 2 in five reps, the router
    echoing turn 1's transcript `invoice 440 / INV-00426` and the
    declared arm rejecting the join; MT-ANCHOR's turn 2 in five reps,
    prose that never names line_note — and five after: the five drifts,
    byte-identical. Both lints at zero, as before."""
    import json

    from engine.eval.grade import pack_world_manifests
    from engine.eval.models import RunRecord, RunReportHeader

    stats, dictionary, dictionary_map = _pack_substrates()
    lines = POST_RIDER.read_text(encoding="utf-8").splitlines()
    header = RunReportHeader.model_validate(json.loads(lines[0]))
    check_world(header, pack_world_manifests(PACK))
    records = [RunRecord.model_validate(json.loads(line)) for line in lines[1:] if line.strip()]
    report = expose(
        records, stats=stats, dictionary=dictionary, dictionary_map=dictionary_map,
        settings=PlausibilitySettings(),
        checks=["lint.placeholder", "lint.ungrounded_key", "anchor.entity_mismatch"],
    )
    assert report.statements == 265
    assert report.counts() == {
        "lint.placeholder": 0,
        "lint.ungrounded_key": 0,
        "anchor.entity_mismatch": 5,
    }
    assert [(hit.row_id, hit.rep, hit.turn_index, hit.invocation_index) for hit in report.hits] == [
        ("MT-ANCHOR", rep, 1, -1) for rep in (1, 2, 3, 4, 5)
    ]
    assert {hit.detail for hit in report.hits} == {
        "the question refers to that rule; turn 1's evidence established `line_note`, "
        "and this answer never names it"
    }
