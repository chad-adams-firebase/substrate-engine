"""Phase 3 done-checks against the REAL InvoiceGuard pack: the full
generated substrates, the converted DuckDB world, the pinned sibling
clone, and the 32k-line simulation log.

These are the only tests that need local, gitignored/sibling state
(app.duckdb, the clone, simout). On a fresh checkout they skip with
the setup recipe named — the suite stays green offline (Brief §15).
"""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.config.models import PortName
from engine.config.pack_loader import load_pack
from engine.ports.types import LLMResponse
from engine.runtime.container import build
from engine.runtime.registry import default_registry
from engine.runtime.tools import build_tools

from tests.stubs.llm_stub import ScriptedLLM

PACK = Path(__file__).parent.parent / "packs" / "invoiceguard"
CLONE = (PACK / "../../../invoice-guard").resolve()
PINNED_SHA = "761a18e9b9253870d930f1b13b3a852ce516d603"


def _missing_pieces() -> list[str]:
    missing = []
    if not (PACK / "app.duckdb").is_file():
        missing.append("packs/invoiceguard/app.duckdb (run `engine convert`)")
    if not (CLONE / "simout" / "logs" / "invoiceguard.log").is_file():
        missing.append(
            f"{CLONE}/simout (clone invoice-guard beside this repo and run "
            f"`uv run invoiceguard simulate --seed 42`)"
        )
    if not (CLONE / ".git").exists():
        missing.append(f"{CLONE} (sibling clone of invoice-guard)")
    else:
        head = subprocess.run(
            ["git", "-C", str(CLONE), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if head != PINNED_SHA:
            missing.append(
                f"clone at {head[:12]}, pack pins {PINNED_SHA[:12]} "
                f"(check out the pinned commit)"
            )
    return missing

_MISSING = _missing_pieces()
pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason="real InvoiceGuard pack unavailable: " + "; ".join(_MISSING),
)

LAST_WEEK_SQL = (
    "SELECT COUNT(DISTINCT i.id) AS invoices_with_findings\n"
    "FROM invoices i\n"
    "JOIN findings f ON f.invoice_id = i.id\n"
    "WHERE i.received_at >= '2026-05-23 00:00:00'\n"
    "  AND i.received_at < '2026-05-30 00:00:00'"
)


def real_pack_registry(llm_responses: list | None = None):
    """The real pack, with the pytest LLM stub swapped in over the
    'openrouter' registry slot — tests never touch the network."""
    pack = load_pack(PACK)
    registry = default_registry()
    stub = ScriptedLLM(llm_responses or [])
    registry.register(PortName.LLM, "openrouter", lambda settings, root: stub)
    ports = build(pack, registry)
    return build_tools(pack, ports), ports


def test_run_sql_answers_last_week_through_the_repair_loop():
    """The phase's flagship: a real-schema question answered with
    grounded SQL, first attempt deliberately broken, error fed back,
    second attempt correct — against the live seed-42 world."""
    broken = LLMResponse(
        content="```sql\nSELECT COUNT(*) FROM invoices_with_findings\n```",
        model="scripted",
    )
    repaired = LLMResponse(content=f"```sql\n{LAST_WEEK_SQL}\n```", model="scripted")
    registry, ports = real_pack_registry([broken, repaired])

    invocation = registry.invoke(
        "run_sql",
        {"question": "How many invoices received last week had findings?"},
    )

    assert invocation.status == "ok", invocation.error
    # The seed-42 world's ground truth, cross-checked against a direct
    # execution of the same SQL.
    direct = ports.get(PortName.SQL).run_sql(
        LAST_WEEK_SQL, ports.get(PortName.IDENTITY).current_user()
    )
    assert invocation.output.table.rows == direct
    assert invocation.output.table.rows == [{"invoices_with_findings": 146}]

    attempts = invocation.evidence.attempts
    assert len(attempts) == 2 and attempts[0].error is not None
    stub = ports.get(PortName.LLM)
    assert attempts[0].error in stub.calls[1]["messages"][-1].content
    # The grounding carried the map, not raw schema alone — including
    # the data-coverage gotcha that keeps date('now') out of SQL.
    assert "adjustment_totals" in invocation.evidence.grounding_prompt
    assert "date('now')" in invocation.evidence.grounding_prompt


def test_savings_question_grounds_the_rule_savings_template():
    """U5's live shape on the real pack: the question names the
    rule_savings metric, and its LEFT-JOIN-zeroing statement leads
    the grounding prompt. The template is what the gold script
    executes (evals/invoiceguard/gold/u5_rule_savings.py)."""
    registry, ports = real_pack_registry(
        [
            LLMResponse(
                content=(
                    "```sql\nSELECT f.rule_name, SUM(CASE WHEN "
                    "ff.valid_exception = 1 THEN 0 ELSE COALESCE(f.amount, 0) "
                    "END) AS effective_savings FROM findings f LEFT JOIN "
                    "finding_feedback ff ON ff.finding_id = f.id GROUP BY "
                    "f.rule_name ORDER BY effective_savings DESC LIMIT 1\n```"
                ),
                model="scripted",
            )
        ]
    )
    invocation = registry.invoke(
        "run_sql", {"question": "Which rule produces the most savings?"}
    )
    assert invocation.status == "ok", invocation.error
    prompt = invocation.evidence.grounding_prompt
    head = prompt.partition("## Tables and columns")[0]
    assert "metric rule_savings" in head
    assert "LEFT JOIN finding_feedback ff ON ff.finding_id = f.id" in head
    assert "status_is_current" in prompt
    # No fan-out round: the map declares findings_to_feedback one_to_one.
    assert len(invocation.evidence.attempts) == 1
    (row,) = invocation.output.table.rows
    assert row["rule_name"] == "quantity_spike"
    assert round(row["effective_savings"], 2) == 610768.51


def test_ckg_answers_the_ordered_calls_question_on_the_real_graph():
    registry, _ = real_pack_registry()
    invocation = registry.invoke(
        "traverse_code_knowledge_graph",
        {"entry": "invoiceguard.spine.rules_engine.run_rules", "hop": "callees"},
    )
    assert invocation.status == "ok", invocation.error
    called = [n.qualified_name.rsplit(".", 1)[1] for n in invocation.output.nodes]
    rules = [name for name in called if name.startswith("rule_")]
    assert rules == [
        "rule_rate_variance",
        "rule_unapproved_item",
        "rule_quantity_spike",
        "rule_duplicate_line",
        "rule_rush_fee_unjustified",
        "rule_markup_over_list",
        "rule_service_hours_excessive",
        "rule_contract_lapsed_rate",
        "rule_new_supplier",
        "rule_freight_overcharge",
        "rule_total_mismatch",
        "rule_split_billing",
    ]
    lines = [edge.line for edge in invocation.output.edges]
    assert lines == sorted(lines)


def test_read_source_returns_the_exact_rule_lines_from_the_clone():
    registry, _ = real_pack_registry()
    invocation = registry.invoke(
        "read_source", {"node": "invoiceguard.spine.rules_engine.rule_rate_variance"}
    )
    assert invocation.status == "ok", invocation.error
    output = invocation.output
    assert (output.start_line, output.end_line) == (116, 149)
    clone_text = (CLONE / output.file_path).read_text(encoding="utf-8")
    expected = "".join(
        clone_text.splitlines(keepends=True)[output.start_line - 1 : output.end_line]
    )
    assert output.text == expected


def test_check_execution_answers_did_the_stale_sweep_run_on_day_n():
    registry, _ = real_pack_registry()
    day_10 = {
        "window_start": datetime(2026, 3, 11, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 3, 12, tzinfo=UTC).isoformat(),
    }
    invocation = registry.invoke(
        "check_execution", {"component": "stale_sweep", **day_10}
    )
    assert invocation.status == "ok", invocation.error
    assert invocation.output.run_status.ran is True
    assert invocation.output.run_status.count == 1
    assert "stale_sweep_completed" in invocation.evidence.lines[0]

    # And the planted outage: 30 benchmark fallbacks on day 10, none
    # the day after.
    errors = registry.invoke(
        "check_execution",
        {"component": "benchmark_scoring", "mode": "recent_errors", **day_10},
    )
    assert len(errors.output.errors) == 30


def test_full_graph_ask_to_answer_with_complete_provenance():
    """Phase 4 end-to-end against the real pack: route -> run_sql
    (real grounding, real DuckDB) -> draft with placeholders -> real
    Verifier -> verified answer, with the §12 row checked against the
    pack's real manifests. Only the LLM is scripted; the work store is
    in-memory so the committed pack directory stays untouched."""
    from engine.adapters.work_store_sqlite import (
        SqliteWorkStore,
        SqliteWorkStoreSettings,
    )
    from engine.harness.drafter import Drafter
    from engine.harness.graph import GraphDeps
    from engine.harness.prompts import (
        render_drafter_prompt,
        render_router_prompt,
        render_summarizer_prompt,
    )
    from engine.harness.session import AskSession
    from engine.ports.types import ToolCall
    from engine.runtime.harness import build_verifier
    from engine.verifier.models import VerifierVerdict

    responses = [
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(
                    name="run_sql",
                    arguments={
                        "question": (
                            "How many invoices received last week had findings?"
                        )
                    },
                )
            ],
            model="scripted",
        ),
        LLMResponse(content=f"```sql\n{LAST_WEEK_SQL}\n```", model="scripted"),
        LLMResponse(
            content="",
            tool_calls=[
                ToolCall(name="give_answer", arguments={"shape": "prose"})
            ],
            model="scripted",
        ),
        LLMResponse(
            content=(
                "Of the invoices received last week, "
                "{{e0.table.rows[0].invoices_with_findings}} had at least "
                "one finding."
            ),
            model="scripted",
        ),
    ]
    registry, ports = real_pack_registry(responses)
    pack = load_pack(PACK)
    llm = ports.get(PortName.LLM)
    deps = GraphDeps(
        llm=llm,
        registry=registry,
        verifier=build_verifier(pack, ports),
        drafter=Drafter(
            llm,
            render_drafter_prompt(app_name=pack.config.name),
            inline_value_max_chars=pack.config.harness.inline_value_max_chars,
        ),
        settings=pack.config.harness,
        summarizer_prompt=render_summarizer_prompt(app_name=pack.config.name),
        router_prompt=render_router_prompt(
            app_name=pack.config.name,
            app_description=pack.config.description,
            max_iterations=pack.config.harness.max_router_iterations,
        ),
    )
    work_store = SqliteWorkStore(SqliteWorkStoreSettings(database=":memory:"))
    session = AskSession(
        deps=deps,
        work_store=work_store,
        identity=ports.get(PortName.IDENTITY),
    )

    result = session.ask("How many invoices received last week had findings?")

    assert result.outcome.kind == "answer"
    assert result.outcome.verification == "verified"
    assert "146 had at least one finding" in result.outcome.body.text

    (entry,) = work_store.list_turn_logs(result.conversation_id)
    assert entry.tools_used == ["run_sql"]
    assert "application_database" in entry.substrates_read
    verdict = VerifierVerdict.model_validate_json(entry.verifier_verdict)
    assert verdict.disposition == "verified"
    # The logged substrate versions are real manifest ids of this pack.
    known_manifests = {
        m.manifest_id for m in ports.get(PortName.SUBSTRATE_STORE).manifests()
    }
    assert entry.substrate_versions
    assert set(entry.substrate_versions) <= known_manifests
    # The bundle round-trips and the winning SQL is inside it.
    payload = work_store.load_evidence_bundle(entry.evidence_bundle_ref)
    assert LAST_WEEK_SQL.splitlines()[0] in payload


def test_full_pack_round_trip_of_a_multi_tool_turn():
    from engine.tools.envelope import dumps_turn_evidence, loads_turn_evidence

    repaired = LLMResponse(content=f"```sql\n{LAST_WEEK_SQL}\n```", model="scripted")
    registry, _ = real_pack_registry([repaired])
    turn = [
        registry.invoke("app_primer", {}),
        registry.invoke("search_business_docs", {"query": "compliance critical 1500"}),
        registry.invoke("run_sql", {"question": "invoices with findings last week"}),
    ]
    assert [t.status for t in turn] == ["ok", "ok", "ok"]
    assert loads_turn_evidence(dumps_turn_evidence(turn)) == turn