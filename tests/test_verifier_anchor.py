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
from engine.verifier.anchor import CHECK, check_anchor, is_kindless_pronoun, open_window, read_anchor, referent_kind
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


MT_KEY_TURN_1 = TurnAnchors(turn=1, entities=[
    Anchor(kind="invoice", column="invoices.id", value="440", source="cell"),
    Anchor(kind="invoice", column="invoices.invoice_number", value="INV-00426", source="cell"),
])


def test_a_declared_about_may_wear_its_kind_noun(catalog):
    """Fix Pass R2 (MT-KEY 0/5 with the engine correct): the router said
    `invoice 440`, set equality against {440, INV-00426} failed. One
    article and one synonym of the question's kind come off, then
    equality with one name — never containment, so a list of two and
    another number still warn, and another kind's noun is not stripped."""
    question = "What was that invoice's history?"
    for about in ("invoice 440", "the invoice INV-00426", "440", "Invoice INV-00426", "`invoice 440`"):
        assert run(catalog, question=question, about=about, prior=[MT_KEY_TURN_1]) is None, about
    for about in ("invoice 441", "440 and 441", "invoice 440 and 441", "supplier 440", "invoice"):
        finding = run(catalog, question=question, about=about, prior=[MT_KEY_TURN_1])
        assert finding is not None and finding.detail.endswith(f"about `{about}`"), about
    assert run(catalog, question=TURN_7_QUESTION, about="rule line_note") is None
    assert run(catalog, question=TURN_7_QUESTION, about="the audit rule line_note") is None
    assert run(catalog, question=TURN_7_QUESTION, about="rule new_supplier") is not None


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


# --- Fix Pass R1 (b′): the pronoun window ---------------------------------

WARN_2 = "the question refers to that rule; turn 1's evidence established `line_note`, and this answer never names it"
TURN_1 = TurnAnchors(turn=1, entities=[
    Anchor(kind="rule", column="findings.rule_name", value="line_note", source="cell"),
])
WARNED_2 = TurnAnchors(turn=2, contradicted_kind="rule", contradiction=WARN_2)
TABLE = DraftAnswer(kind="table_passthrough", text="SELECT ...")
COUNT_197 = sql_turn("SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'new_supplier'", ["n"], [{"n": 197}])
COUNT_505 = sql_turn("SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'line_note'", ["n"], [{"n": 505}])
IT_QUESTION = "How many findings has it produced?"


def test_the_recorded_breach_turn_is_flagged_inside_the_window(catalog):
    """MT-ANCHOR rep 4 turn 3, reconstructed: turn 1 line_note, turn 2
    warned, "How many findings has it produced?" about new_supplier at
    197. All three readings decide as they do for a kind-bearing turn,
    and the detail names the warning the pronoun follows."""
    prior = [TURN_1, WARNED_2]
    declared = run(catalog, question=IT_QUESTION, about="new_supplier", draft=TABLE, evidence=[COUNT_197], prior=prior)
    assert declared is not None and declared.severity == "warn"
    assert declared.detail == (
        "the question's pronoun follows turn 2's anchor warning; turn 1's evidence "
        "established `line_note`, and this answer says it is about `new_supplier`"
    )
    filtered = run(catalog, question=IT_QUESTION, draft=TABLE, evidence=[COUNT_197], prior=prior)
    assert filtered is not None and filtered.detail.endswith("filters on `findings.rule_name = 'new_supplier'`")
    prose = run(catalog, question="What does its source look like?", draft=PROSE_7, prior=prior)
    assert prose is not None and prose.detail.endswith("and this answer never names it")
    # The clean path: the correction was read.
    assert run(catalog, question=IT_QUESTION, about="line_note", draft=TABLE, evidence=[COUNT_505], prior=prior) is None
    assert run(catalog, question=IT_QUESTION, draft=TABLE, evidence=[COUNT_505], prior=prior) is None
    assert run(catalog, question=IT_QUESTION, about="rule line_note", draft=TABLE, evidence=[COUNT_197], prior=prior) is None


def test_the_window_survives_a_refusal_and_closes_on_any_kinds_anchor(catalog):
    """The session's shape: warn at 7, refusal at 8, "it" at 9 — a
    refusal establishes nothing, so the window is open at 9. Then any
    unwarned answer that establishes an entity, of any kind, closes it:
    turn 10 established an auditor, and turn 19's "Show me an example
    invoice for it" faced no check (the dev-store replay stays at 2)."""
    refusal = TurnAnchors(turn=8)
    assert open_window([TURN_1, WARNED_2, refusal]) is WARNED_2
    assert run(catalog, question=IT_QUESTION, draft=TABLE, evidence=[COUNT_197], prior=[TURN_1, WARNED_2, refusal]) is not None
    auditor = TurnAnchors(turn=10, entities=[
        Anchor(kind="auditor", column="invoice_history.actor", value="nova", source="cell"),
    ])
    assert open_window([TURN_1, WARNED_2, refusal, auditor]) is None
    assert run(catalog, question=IT_QUESTION, draft=TABLE, evidence=[COUNT_197], prior=[TURN_1, WARNED_2, refusal, auditor]) is None
    # A declared-only record does not close it: nothing column-bearing was established.
    declared_only = TurnAnchors(turn=8, entities=[Anchor(kind="rule", column="", value="line_note", source="declared")])
    assert open_window([TURN_1, WARNED_2, declared_only]) is WARNED_2
    # A second warn inside the window keeps it open on the newest warn.
    warned_9 = TurnAnchors(turn=9, contradicted_kind="rule", contradiction="…")
    assert open_window([TURN_1, WARNED_2, refusal, warned_9]) is warned_9
    assert referent_kind(IT_QUESTION, [TURN_1, WARNED_2, refusal, warned_9], catalog) == "rule"
    assert referent_kind(IT_QUESTION, [TURN_1, WARNED_2, refusal, auditor], catalog) is None
    assert referent_kind(TURN_7_QUESTION, [TURN_1, WARNED_2, refusal, auditor], catalog) == "rule"


def test_no_window_without_a_warn_so_the_green_rows_are_untouched(catalog):
    """MT3's turn 2 ("the rule that flags it") after a turn that
    established both a supplier and the rule; MT4's kind-bearing turn 4;
    a first turn; MT1/MT2's "those": none opens a window, because none
    was ever warned."""
    mt3_turn_1 = TurnAnchors(turn=1, entities=[
        Anchor(kind="supplier", column="suppliers.code", value="RVX01", source="cell"),
        Anchor(kind="supplier", column="suppliers.name", value="Ravenswood Extrusion", source="cell"),
        Anchor(kind="rule", column="findings.rule_name", value="rate_variance", source="filter"),
    ])
    source = DraftAnswer(kind="prose", text="def rule_rate_variance(session, clock, invoice, line, contracts, config): ...")
    assert run(catalog, question="Show me the source of the rule that flags it", draft=source, prior=[mt3_turn_1]) is None
    assert referent_kind("Show me the source of the rule that flags it", [mt3_turn_1], catalog) is None
    assert run(catalog, question=IT_QUESTION, draft=TABLE, evidence=[COUNT_197], prior=[TURN_1]) is None
    assert run(catalog, question=IT_QUESTION, draft=TABLE, evidence=[COUNT_197], prior=[]) is None
    assert run(catalog, question="How many of those arrived in each month?", draft=TABLE, prior=[mt3_turn_1, WARNED_2]) is None
    for question, pronoun in (
        ("How many findings has it produced?", True), ("Show me its source", True), ("Is it flagged?", True),
        ("How many of those arrived in each month?", False), ("What did they close?", False),
        ("Show me the item's lines", False), ("Which rule fires most often?", False),
    ):
        assert is_kindless_pronoun(question) is pronoun, question


def test_two_kinds_coexist_and_only_the_warned_kind_is_read(catalog):
    """Turn 1 established supplier S and rule R; "that rule" was warned at
    turn 2. "It" at turn 3 is read against R alone: a filter on S's
    columns carries no literal on the rule's, so it is silent; a filter
    on another rule is not. An about naming S is the documented residue
    — [UNVERIFIED] on a turn that meant the supplier — recorded, not
    hidden."""
    turn_1 = TurnAnchors(turn=1, entities=[
        Anchor(kind="supplier", column="suppliers.code", value="RVX01", source="cell"),
        Anchor(kind="rule", column="findings.rule_name", value="rate_variance", source="filter"),
    ])
    warned = TurnAnchors(turn=2, contradicted_kind="rule", contradiction="…")
    by_supplier = sql_turn(
        "SELECT COUNT(*) AS n FROM invoices i JOIN suppliers s ON s.id = i.supplier_id WHERE s.code = 'RVX01'",
        ["n"], [{"n": 126}],
    )
    assert run(catalog, question="How many invoices did it send?", draft=TABLE, evidence=[by_supplier], prior=[turn_1, warned]) is None
    other_rule = sql_turn("SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'line_note'", ["n"], [{"n": 505}])
    finding = run(catalog, question=IT_QUESTION, draft=TABLE, evidence=[other_rule], prior=[turn_1, warned])
    assert finding is not None and "`rate_variance`" in finding.detail
    residue = run(catalog, question="How many invoices did it send?", about="RVX01", draft=TABLE, evidence=[by_supplier], prior=[turn_1, warned])
    assert residue is not None and residue.severity == "warn"


# --- Rider Pass: what the check confirmed ---------------------------------


def read(catalog, *, question, about=None, draft=PROSE_7, evidence=(), prior=(TURN_6,)):
    return read_anchor(
        question=question, about=about, draft=draft,
        evidence=list(evidence), prior=list(prior), catalog=catalog,
    )


def test_read_anchor_reports_how_the_answer_was_confirmed(catalog):
    """The same reading that warns knows which arm confirmed: the prose
    naming the anchor, a filter on its key, the router's declaration —
    and a table that filtered on nothing is silent without confirming."""
    named = DraftAnswer(kind="prose", text="The line_note rule flags a line that carries a note.")
    prose = read(catalog, question=TURN_7_QUESTION, draft=named)
    assert (prose.kind, prose.turn) == ("rule", 6)
    assert (prose.confirmed_by, prose.default_about, prose.finding) == ("prose", "line_note", None)
    counted = read(catalog, question="How many findings has that rule produced?", draft=TABLE, evidence=[COUNT_505])
    assert (counted.confirmed_by, counted.default_about) == ("filter", "line_note")
    declared = read(catalog, question=TURN_7_QUESTION, about="rule line_note")
    assert declared.confirmed_by == "declared" and declared.default_about == "" and declared.finding is None
    grouped = sql_turn(
        "SELECT rule_name, COUNT(*) AS n FROM findings GROUP BY rule_name",
        ["rule_name", "n"], [{"rule_name": "line_note", "n": 505}, {"rule_name": "new_supplier", "n": 197}],
    )
    silent = read(catalog, question="How many findings has that rule produced?", draft=TABLE, evidence=[grouped])
    assert silent.confirmed_by is None and silent.finding is None and silent.default_about == ""
    drift = read(catalog, question=TURN_7_QUESTION)  # the recorded turn 7 never names line_note
    assert drift.finding is not None and drift.confirmed_by is None and drift.default_about == ""
    assert drift.finding.detail == check_anchor(
        question=TURN_7_QUESTION, about=None, draft=PROSE_7, evidence=[], prior=[TURN_6], catalog=catalog
    ).detail
    assert read(catalog, question=IT_QUESTION, prior=[TURN_6]) is None  # no kind, no window
    assert read(catalog, question=TURN_7_QUESTION, prior=[]) is None  # no prior


def test_the_default_about_is_one_value_the_anchor_carries_and_replays_silent(catalog):
    """Ruling: the injected About always equals a value the anchor
    actually carries — never a paraphrase, never a join of two values —
    so re-checking it as a declared about is silent. The join is the
    transcript's rendering, which the declared arm reads (Rider 2) and
    the engine never writes."""
    question = "What was that invoice's history?"
    history = sql_turn(
        "SELECT ih.from_status, ih.to_status FROM invoice_history ih WHERE ih.invoice_id = 440 ORDER BY ih.at",
        ["from_status", "to_status"], [{"from_status": None, "to_status": "RECEIVED"}],
    )
    by_key = read(catalog, question=question, draft=TABLE, evidence=[history], prior=[MT_KEY_TURN_1])
    assert (by_key.confirmed_by, by_key.default_about) == ("filter", "440")
    by_number = read(
        catalog, question=question, prior=[MT_KEY_TURN_1],
        draft=DraftAnswer(kind="prose", text="Invoice INV-00426 moved through five statuses on one day."),
    )
    assert (by_number.confirmed_by, by_number.default_about) == ("prose", "INV-00426")
    both = read(
        catalog, question=question, prior=[MT_KEY_TURN_1],
        draft=DraftAnswer(kind="prose", text="Invoice INV-00426 (id 440) moved through five statuses."),
    )
    assert both.default_about in {"440", "INV-00426"}
    supplier = TurnAnchors(turn=1, entities=[
        Anchor(kind="supplier", column="suppliers.code", value="RVX01", source="cell"),
        Anchor(kind="supplier", column="suppliers.name", value="Ravenswood Extrusion", source="cell"),
    ])
    named = read(
        catalog, question="What was that supplier's total again?", prior=[supplier],
        draft=DraftAnswer(kind="prose", text="Ravenswood Extrusion (RVX01) billed the most."),
    )
    assert named.default_about == "Ravenswood Extrusion"  # the name before the id-like code
    spelled = read(
        catalog, question=TURN_7_QUESTION,
        draft=DraftAnswer(kind="prose", text="The line note rule flags a line that carries a note."),
    )
    assert spelled.default_about == "line_note"  # the anchor's spelling, not the prose's
    for reading_, q, prior in (
        (by_key, question, [MT_KEY_TURN_1]), (by_number, question, [MT_KEY_TURN_1]),
        (both, question, [MT_KEY_TURN_1]), (named, "What was that supplier's total again?", [supplier]),
        (spelled, TURN_7_QUESTION, [TURN_6]),
    ):
        assert any(reading_.default_about == a.value for a in reading_.anchors)
        assert run(catalog, question=q, about=reading_.default_about, prior=prior) is None


def test_the_prose_confirmation_needs_a_whole_word(catalog):
    """A substring hit keeps the check silent, as before the pass — but it
    confirms nothing: "ava" inside "available" must never become an
    About the engine writes."""
    auditor = TurnAnchors(turn=1, entities=[
        Anchor(kind="auditor", column="invoice_history.actor", value="ava", source="cell"),
    ])
    question = "How many invoices did that auditor close?"
    inside = read(
        catalog, question=question, prior=[auditor],
        draft=DraftAnswer(kind="prose", text="No closing data is available for the period."),
    )
    assert inside.finding is None and inside.confirmed_by is None and inside.default_about == ""
    whole = read(
        catalog, question=question, prior=[auditor],
        draft=DraftAnswer(kind="prose", text="ava closed the most invoices in the period."),
    )
    assert (whole.confirmed_by, whole.default_about) == ("prose", "ava")
    numbered = read(
        catalog, question="What was that invoice's history?", prior=[MT_KEY_TURN_1],
        draft=DraftAnswer(kind="prose", text="Invoice 1440 is unrelated."),
    )
    # "1440" is a substring hit on 440: silent, as before the pass — and
    # unconfirmed, so no About is written for it.
    assert numbered.finding is None and numbered.confirmed_by is None


def test_through_the_verifier_the_default_rides_only_on_a_clean_undeclared_confirmation(catalog):
    """Claim-free prose, so the anchor reading alone decides: an
    undeclared answer the prose arm confirms carries the default; a
    declared one, a drift, and a context-free call carry none; inside
    the window the surviving anchor is the default."""
    verifier, llm = make_verifier([], stats=[], catalog=catalog)
    named = DraftAnswer(kind="prose", text="It is the line note rule, which flags a line carrying a note.")
    confirmed = verifier.verify(
        question=TURN_7_QUESTION, draft=named, evidence=[], attempt=1,
        context=VerifyContext(prior=[TURN_6]),
    )
    assert confirmed.disposition == "verified" and confirmed.about_default == "line_note"
    declared = verifier.verify(
        question=TURN_7_QUESTION, draft=named, evidence=[], attempt=1,
        context=VerifyContext(prior=[TURN_6], about="line_note"),
    )
    assert declared.disposition == "verified" and declared.about_default is None
    drifted = DraftAnswer(kind="prose", text="It flags every invoice from a supplier in its first year.")
    drift = verifier.verify(
        question=TURN_7_QUESTION, draft=drifted, evidence=[], attempt=1,
        context=VerifyContext(prior=[TURN_6]),
    )
    assert drift.disposition == "unverified" and drift.about_default is None
    assert verifier.verify(question=TURN_7_QUESTION, draft=named, evidence=[], attempt=1).about_default is None
    windowed = verifier.verify(
        question=IT_QUESTION, draft=named, evidence=[], attempt=1,
        context=VerifyContext(prior=[TURN_1, WARNED_2]),
    )
    assert windowed.disposition == "verified" and windowed.about_default == "line_note"
    assert llm.calls == []


# --- Rider 2: the join-echo ------------------------------------------------

# The pack's three-column kind: a supplier's id and code are keys, its
# name a name column — all three surface in one row of evidence.
RVX_TURN_1 = TurnAnchors(turn=1, entities=[
    Anchor(kind="supplier", column="suppliers.id", value="22", source="cell"),
    Anchor(kind="supplier", column="suppliers.code", value="RVX01", source="cell"),
    Anchor(kind="supplier", column="suppliers.name", value="Ravenswood Extrusion", source="cell"),
])


def test_a_declared_about_may_echo_the_transcripts_join(catalog):
    """Rider 2 (MT-KEY 0/5 with the engine correct, again): turn 1's
    transcript renders a two-column anchor as `About: invoice 440 /
    INV-00426.` and the router echoes it verbatim. The declared arm
    reads the join: every component, stripped and normalized on its
    own, must be one of the anchor's names — a stranger, another kind's
    value, a list, a dangling separator (`440 /` after the strip's
    trim) or a doubled one (an empty component is no name) warn as
    before. It confirms as a declaration, so no default is stamped; the
    pack's three-column kind reads the same way."""
    question = "What was that invoice's history?"
    for about in (
        "invoice 440 / INV-00426", "440 / INV-00426", "the invoice 440 / INV-00426",
        "INV-00426 / 440", "440 / 440", "`invoice 440 / INV-00426`",
        "invoice 440", "INV-00426", "440",
    ):
        assert run(catalog, question=question, about=about, prior=[MT_KEY_TURN_1]) is None, about
    for about in (
        "440 / INV-00427", "invoice 441 / INV-00426", "440 / ", "440 /  / INV-00426",
        "440 / RVX01", "440 / INV-00426 / 441", "440 and INV-00426",
    ):
        finding = run(catalog, question=question, about=about, prior=[MT_KEY_TURN_1])
        assert finding is not None and finding.detail.endswith(f"about `{about}`"), about
    echoed = read(catalog, question=question, about="invoice 440 / INV-00426", prior=[MT_KEY_TURN_1])
    assert (echoed.confirmed_by, echoed.default_about, echoed.finding) == ("declared", "", None)
    question = "What was that supplier's total again?"
    for about in (
        "supplier 22 / RVX01 / Ravenswood Extrusion", "RVX01 / Ravenswood Extrusion",
        "vendor Ravenswood Extrusion", "22",
    ):
        assert run(catalog, question=question, about=about, prior=[RVX_TURN_1]) is None, about
    assert run(catalog, question=question, about="22 / RVX01 / ALDERGATE1", prior=[RVX_TURN_1]) is not None
