"""The anchor check (verifier/anchor.py): the 30-turn session's turn 7,
reconstructed — turn 6 established line_note, "What does that rule
check?" was answered about new_supplier, verified — must fail; the
bank's clean follow-ups must stay silent. Offline against the shipped
map and dictionary."""

from pathlib import Path

import pytest

from engine.config.models import ToolName
from engine.substrates.jsonl import read_rows
from engine.substrates.models import DictionaryRow
from engine.substrates.pack_data import load_dictionary_map
from engine.tools.entities import EntityCatalog
from engine.tools.envelope import Anchor, RunSqlOutput, Table, ToolInvocation, TurnAnchors
from engine.verifier.anchor import CHECK, check_anchor
from engine.verifier.models import DraftAnswer, VerifyContext

from tests.verifier_support import make_verifier

PACK = Path(__file__).parent.parent / "packs" / "invoiceguard"


@pytest.fixture(scope="module")
def catalog():
    return EntityCatalog.from_substrates(
        read_rows(PACK / "substrates" / "dictionary.jsonl", DictionaryRow),
        load_dictionary_map(PACK / "dictionary_map.yaml"),
    )


def sql_turn(sql, columns, rows):
    return ToolInvocation(
        tool=ToolName.RUN_SQL, arguments={"question": "q"}, status="ok",
        output=RunSqlOutput(sql=sql, table=Table(columns=columns, rows=rows, total_row_count=len(rows))),
    )


TURN_6 = TurnAnchors(turn=6, entities=[
    Anchor(kind="rule", column="findings.rule_name", value="line_note", source="cell"),
])
# The turn-7 answer, verbatim from the work store.
TURN_7_TEXT = (
    "The rule checks invoices from suppliers within their first contracted year. "
    "It applies a flat review flag (`new_supplier`) without a dollar amount, as noted "
    "in Supplier Onboarding Note — The First-Year Review Flag. This flag exists for "
    "monitoring purposes and is intentionally non-monetary."
)
TURN_7_QUESTION = "What does that rule check?"
PROSE_7 = DraftAnswer(kind="prose", text=TURN_7_TEXT)


def run(catalog, *, question, about=None, draft=PROSE_7, evidence=(), prior=(TURN_6,)):
    return check_anchor(
        question=question, about=about, draft=draft,
        evidence=list(evidence), prior=list(prior), catalog=catalog,
    )


def test_the_recorded_turn_7_fails_the_check_three_ways(catalog):
    """Undeclared: the prose never names line_note. Declared wrong: the
    router said new_supplier. Both are the drift, named with its turn."""
    finding = run(catalog, question=TURN_7_QUESTION)
    assert finding is not None and finding.check == CHECK and finding.severity == "warn"
    assert finding.detail == (
        "the question refers to that rule; turn 6's evidence established "
        "`line_note`, and this answer never names it"
    )
    declared = run(catalog, question=TURN_7_QUESTION, about="new_supplier")
    assert declared is not None
    assert declared.detail.endswith("and this answer says it is about `new_supplier`")
    # Turn 9's shape as a table: the SQL filters on the other rule.
    table = DraftAnswer(kind="table_passthrough", text="SELECT ...")
    counted = sql_turn(
        "SELECT COUNT(*) AS new_supplier_findings_count FROM findings WHERE rule_name = 'new_supplier'",
        ["new_supplier_findings_count"], [{"new_supplier_findings_count": 197}],
    )
    filtered = run(catalog, question="How many findings has that rule produced?", draft=table, evidence=[counted])
    assert filtered is not None
    assert filtered.detail.endswith("and this answer filters on `findings.rule_name = 'new_supplier'`")


def test_an_answer_about_the_anchor_is_silent_however_it_says_so(catalog):
    assert run(catalog, question=TURN_7_QUESTION, about="line_note") is None
    assert run(catalog, question=TURN_7_QUESTION, about="`Line Note`") is None
    named = DraftAnswer(kind="prose", text="The line_note rule flags a line that carries a note.")
    assert run(catalog, question=TURN_7_QUESTION, draft=named) is None
    spaced = DraftAnswer(kind="prose", text="The line note rule flags a line that carries a note.")
    assert run(catalog, question=TURN_7_QUESTION, draft=spaced) is None
    table = DraftAnswer(kind="table_passthrough", text="SELECT ...")
    counted = sql_turn(
        "SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'line_note'", ["n"], [{"n": 505}]
    )
    assert run(catalog, question="How many findings has that rule produced?", draft=table, evidence=[counted]) is None
    # A declared about decides before the SQL does.
    wrong_sql = sql_turn(
        "SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'new_supplier'", ["n"], [{"n": 197}]
    )
    assert run(catalog, question=TURN_7_QUESTION, about="line_note", draft=table, evidence=[wrong_sql]) is None


def test_no_kind_no_anchor_or_an_ambiguous_anchor_is_silent(catalog):
    """Kind-less pronouns, a first turn, a refusal before, a multi-row
    table: none manufactures a warn."""
    assert run(catalog, question="How many findings has it produced?") is None
    assert run(catalog, question="Show me its source") is None
    assert run(catalog, question=TURN_7_QUESTION, prior=[]) is None
    assert run(catalog, question=TURN_7_QUESTION, prior=[TurnAnchors(turn=6)]) is None
    six_auditors = TurnAnchors(turn=23, entities=[
        Anchor(kind="rule", column="findings.rule_name", value="correction_ignored", source="filter"),
    ])
    assert run(catalog, question="What did that auditor close?", prior=[six_auditors]) is None
    # A table answer with no filter on the kind says nothing either way.
    table = DraftAnswer(kind="table_passthrough", text="SELECT ...")
    recount = sql_turn("SELECT f.rule_name AS rule_name, COUNT(*) AS n FROM findings f GROUP BY f.rule_name ORDER BY n DESC LIMIT 1",
                       ["rule_name", "n"], [{"rule_name": "line_note", "n": 505}])
    assert run(catalog, question=TURN_7_QUESTION, draft=table, evidence=[recount]) is None


MT4_TURN_1 = TurnAnchors(turn=1, entities=[
    Anchor(kind="supplier", column="suppliers.code", value="RVX01", source="cell"),
    Anchor(kind="supplier", column="suppliers.name", value="Ravenswood Extrusion", source="cell"),
])


def test_mt4s_follow_up_is_silent_on_either_column_and_flagged_on_a_stranger(catalog):
    table = DraftAnswer(kind="table_passthrough", text="SELECT ...")
    question = "What was that supplier's total invoice amount again?"
    by_name = sql_turn(
        "SELECT SUM(i.invoice_total) AS total FROM invoices i JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE s.name = 'Ravenswood Extrusion'", ["total"], [{"total": 1005028.4}],
    )
    by_code = sql_turn(
        "SELECT SUM(i.invoice_total) AS total FROM invoices i JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE s.code = 'RVX01'", ["total"], [{"total": 1005028.4}],
    )
    stranger = sql_turn(
        "SELECT SUM(i.invoice_total) AS total FROM invoices i JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE s.code = 'ALDERGATE1'", ["total"], [{"total": 1.0}],
    )
    for shape in (by_name, by_code):
        assert run(catalog, question=question, draft=table, evidence=[shape], prior=[MT4_TURN_1]) is None
    finding = run(catalog, question=question, draft=table, evidence=[stranger], prior=[MT4_TURN_1])
    assert finding is not None and "`suppliers.code = 'ALDERGATE1'`" in finding.detail
    assert "`RVX01 / Ravenswood Extrusion`" in finding.detail


def test_a_key_column_the_anchor_never_carried_is_not_compared(catalog):
    """MT-KEY: turn 1 showed INV-00426 and no id; a follow-up on
    invoices.id is neither consistent nor contradictory here (the key
    lint judges its grounding), while a different number contradicts."""
    prior = [TurnAnchors(turn=1, entities=[
        Anchor(kind="invoice", column="invoices.invoice_number", value="INV-00426", source="cell"),
    ])]
    table = DraftAnswer(kind="table_passthrough", text="SELECT ...")
    question = "What was that invoice's history?"
    by_id = sql_turn("SELECT ih.to_status AS to_status FROM invoice_history ih WHERE ih.invoice_id = 440",
                     ["to_status"], [{"to_status": "CLOSED"}])
    assert run(catalog, question=question, draft=table, evidence=[by_id], prior=prior) is None
    other = sql_turn("SELECT ih.to_status AS to_status FROM invoice_history ih JOIN invoices i ON i.id = ih.invoice_id "
                     "WHERE i.invoice_number = 'INV-00002'", ["to_status"], [{"to_status": "LAPSED"}])
    finding = run(catalog, question=question, draft=table, evidence=[other], prior=prior)
    assert finding is not None and "`invoices.invoice_number = 'INV-00002'`" in finding.detail


def test_the_most_recent_turn_that_established_the_kind_is_the_anchor(catalog):
    """Turn 8 refused (nothing established); turn 9's filter established
    new_supplier — so a later "that rule" is checked against turn 9,
    where the conversation now stands, and a declaration counts when
    the evidence carried nothing."""
    turn_9 = TurnAnchors(turn=9, entities=[
        Anchor(kind="rule", column="findings.rule_name", value="new_supplier", source="filter"),
    ])
    prior = [TURN_6, TurnAnchors(turn=8), turn_9]
    finding = run(catalog, question="What does that rule check?", about="line_note", prior=prior)
    assert finding is not None and "turn 9's evidence established `new_supplier`" in finding.detail
    declared_only = TurnAnchors(turn=7, entities=[
        Anchor(kind="rule", column="", value="new_supplier", source="declared"),
    ])
    finding = run(catalog, question="Show me that rule's source", about="rate_variance", prior=[TURN_6, declared_only])
    assert finding is not None and "turn 7's evidence established `new_supplier`" in finding.detail


def test_through_the_verifier_the_contradiction_is_a_warn_with_no_tool(catalog):
    """A claim-free prose draft (no identifier for the claim extractor to
    prosecute), so the anchor finding alone decides the ladder step."""
    verifier, llm = make_verifier([], stats=[], catalog=catalog)
    prose = DraftAnswer(kind="prose", text="It flags every invoice from a supplier in its first year.")
    result = verifier.verify(
        question=TURN_7_QUESTION, draft=prose, evidence=[], attempt=1,
        context=VerifyContext(prior=[TURN_6], about="new_supplier"),
    )
    assert result.disposition == "unverified"
    (record,) = [r for r in result.plausibility if r.check == CHECK]
    assert record.tool is None and record.severity == "warn"
    assert "turn 6's evidence established `line_note`" in record.detail
    assert llm.calls == []
    # No context, or no catalog: silent, as before the pass.
    assert verifier.verify(question=TURN_7_QUESTION, draft=prose, evidence=[], attempt=1).disposition == "verified"
    bare, _ = make_verifier([], stats=[])
    assert bare.verify(
        question=TURN_7_QUESTION, draft=prose, evidence=[], attempt=1,
        context=VerifyContext(prior=[TURN_6], about="new_supplier"),
    ).disposition == "verified"
