"""Entity kinds at work (tools/entities.py): the pack's declarations
read against the 30-turn session's own statements (packs/invoiceguard/
work.db conversation 1, 2026-09-04) and the bank's multi-turn rows.
Offline: the shipped map and dictionary, no database, no LLM."""

from pathlib import Path

import pytest

from engine.config.models import ToolName
from engine.substrates.jsonl import read_rows
from engine.substrates.models import DictionaryRow
from engine.substrates.pack_data import load_dictionary_map
from engine.tools.entities import (
    EntityCatalog,
    anaphor_kind,
    equality_literals,
    harvest_turn_anchors,
    known_values,
    tokens_of,
)
from engine.tools.envelope import Anchor, KnownKey, RunSqlOutput, Table, ToolInvocation

PACK = Path(__file__).parent.parent / "packs" / "invoiceguard"


@pytest.fixture(scope="module")
def catalog():
    return EntityCatalog.from_substrates(
        read_rows(PACK / "substrates" / "dictionary.jsonl", DictionaryRow),
        load_dictionary_map(PACK / "dictionary_map.yaml"),
    )


def invocation(sql, columns, rows, status="ok"):
    return ToolInvocation(
        tool=ToolName.RUN_SQL,
        arguments={"question": "q"},
        status=status,
        output=RunSqlOutput(
            sql=sql, table=Table(columns=columns, rows=rows, total_row_count=len(rows))
        ) if status == "ok" else None,
        error=None if status == "ok" else "boom",
    )


# The session's statements, verbatim from the work store.
T6_SQL = (
    "SELECT f.rule_name AS rule_name, COUNT(*) AS fire_count FROM findings f "
    "GROUP BY f.rule_name ORDER BY fire_count DESC LIMIT 1"
)
T6 = invocation(T6_SQL, ["rule_name", "fire_count"], [{"rule_name": "line_note", "fire_count": 505}])
T9_SQL = "SELECT COUNT(*) AS new_supplier_findings_count FROM findings WHERE rule_name = 'new_supplier'"
T9 = invocation(T9_SQL, ["new_supplier_findings_count"], [{"new_supplier_findings_count": 197}])
T19_SQL = """WITH most_flagged_item AS (
    SELECT l.item_code AS item_code, COUNT(DISTINCT f.id) AS flag_count
    FROM invoice_lines l LEFT JOIN findings f ON l.invoice_id = f.invoice_id AND l.line_number = f.line_number
    GROUP BY l.item_code ORDER BY flag_count DESC LIMIT 1
),
example_invoice AS (
    SELECT l.invoice_id AS invoice_id FROM invoice_lines l JOIN most_flagged_item mfi ON l.item_code = mfi.item_code LIMIT 1
)
SELECT i.invoice_number AS invoice_number, i.received_at AS received_at, i.invoice_total AS invoice_total, s.name AS supplier_name
FROM invoices i JOIN example_invoice ei ON i.id = ei.invoice_id JOIN suppliers s ON i.supplier_id = s.id"""
T19 = invocation(
    T19_SQL,
    ["invoice_number", "received_at", "invoice_total", "supplier_name"],
    [{"invoice_number": "INV-00002", "received_at": "2026-03-02 08:00:00",
      "invoice_total": 8798.94, "supplier_name": "Aldergate Industrial Supply"}],
)
T20_SQL = (
    "SELECT ih.from_status AS from_status, ih.to_status AS to_status, ih.actor AS actor, "
    "ih.at AS transition_time FROM invoice_history ih "
    "WHERE ih.invoice_id = 123 -- Replace 123 with the actual invoice ID\nORDER BY ih.at"
)
T22_SQL = (
    "SELECT s.code, s.name, COUNT(*) AS corrections_ignored FROM findings f "
    "JOIN invoices i ON i.id = f.invoice_id JOIN suppliers s ON s.id = i.supplier_id "
    "WHERE f.rule_name = 'correction_ignored' GROUP BY s.code, s.name "
    "ORDER BY corrections_ignored DESC, s.code LIMIT 1"
)
T22 = invocation(T22_SQL, ["code", "name", "corrections_ignored"],
                 [{"code": "RVX01", "name": "Ravenswood Extrusion", "corrections_ignored": 9}])
T23_SQL = (
    "SELECT u.short_name AS auditor, COUNT(*) AS ignored_corrections FROM findings f "
    "JOIN invoices i ON i.id = f.invoice_id JOIN users u ON u.id = i.claimed_by "
    "WHERE f.rule_name = 'correction_ignored' GROUP BY u.short_name ORDER BY ignored_corrections DESC"
)
T23 = invocation(T23_SQL, ["auditor", "ignored_corrections"],
                 [{"auditor": a, "ignored_corrections": n}
                  for a, n in (("finch", 6), ("bo", 3), ("ava", 3), ("nova", 2), ("orin", 2), ("tass", 1))])


def test_the_catalog_resolves_foreign_keys_to_their_kind(catalog):
    assert catalog.kinds == ("invoice", "supplier", "auditor", "rule", "item")
    assert catalog.kind_of("findings.invoice_id") == "invoice"
    assert catalog.canonical_of("invoice_history.invoice_id") == "invoices.id"
    assert catalog.kind_of("invoices.invoice_number") == "invoice"
    assert catalog.kind_of("compliance_rules.rule_code") == "rule"
    assert catalog.synonyms["vendor"] == "supplier"
    assert catalog.synonyms["rule"] == "rule"


def test_id_like_is_keys_and_foreign_keys_and_never_a_name(catalog):
    """Ruling 3 and its amendment: PK/FK plus declared key columns; a
    name column — rule labels, supplier names — is never id-like."""
    for qualified in ("invoices.id", "findings.invoice_id", "invoices.invoice_number",
                      "suppliers.code", "users.short_name", "invoice_lines.item_code"):
        assert catalog.is_id_like(qualified), qualified
    for qualified in ("findings.rule_name", "compliance_rules.rule_code", "suppliers.name",
                      "invoice_history.actor", "invoices.status", "invoices.revision"):
        assert not catalog.is_id_like(qualified), qualified


def test_the_alias_fallback_names_exactly_one_declared_column(catalog):
    assert catalog.entity_column_by_name("rule_name") == "findings.rule_name"
    assert catalog.entity_column_by_name("invoice_number") == "invoices.invoice_number"
    assert catalog.entity_column_by_name("id") is None  # every table has one
    assert catalog.entity_column_by_name("fire_count") is None


def test_equality_literals_read_the_sessions_predicates(catalog):
    (t20,) = equality_literals(T20_SQL, catalog)
    assert (t20.table, t20.column, t20.values) == ("invoice_history", "invoice_id", ("123",))
    assert (t20.canonical, t20.kind, t20.id_like) == ("invoices.id", "invoice", True)
    (t9,) = equality_literals(T9_SQL, catalog)
    assert (t9.canonical, t9.values, t9.kind, t9.id_like) == ("findings.rule_name", ("new_supplier",), "rule", False)
    numbers = equality_literals(
        "SELECT 1 FROM invoices i WHERE i.id IN (1, 2, 3) AND i.revision = 1 "
        "AND i.invoice_total >= 100 AND i.supplier_id <> 4 AND i.claimed_by != 5", catalog,
    )
    assert [(l.column, l.values, l.id_like) for l in numbers] == [
        ("revision", ("1",), False), ("id", ("1", "2", "3"), True),
    ]


def test_an_expression_right_side_is_not_a_literal(catalog):
    """MT2's derived join: rule_name = 'compliance_' || cr.rule_code
    binds no value."""
    found = equality_literals(
        "SELECT COUNT(*) AS n FROM findings f JOIN compliance_rules cr "
        "ON f.rule_name = 'compliance_' || cr.rule_code WHERE cr.severity = 'CRITICAL'",
        catalog,
    )
    assert [(l.column, l.values) for l in found] == [("severity", ("CRITICAL",))]


def test_a_block_comment_with_an_apostrophe_does_not_desync_the_literals(catalog):
    found = equality_literals(
        "SELECT 1 FROM findings f /* the rule's own name */ WHERE f.rule_name = 'line_note'",
        catalog,
    )
    assert [(l.column, l.values) for l in found] == [("rule_name", ("line_note",))]


def test_turn_6_establishes_the_rule_and_carries_no_key(catalog):
    anchors = harvest_turn_anchors([T6], catalog, turn=6)
    assert anchors.turn == 6
    assert anchors.entities == [
        Anchor(kind="rule", column="findings.rule_name", value="line_note", source="cell")
    ]
    assert anchors.keys == []


def test_turn_19_establishes_the_invoice_by_its_number_and_the_supplier_by_name(catalog):
    """The finding's shape: the natural key surfaced, the surrogate id
    never did — so 123 could not have come from here."""
    anchors = harvest_turn_anchors([T19], catalog, turn=19)
    assert anchors.entities == [
        Anchor(kind="invoice", column="invoices.invoice_number", value="INV-00002", source="cell"),
        Anchor(kind="supplier", column="suppliers.name", value="Aldergate Industrial Supply", source="cell"),
    ]
    assert anchors.keys == [KnownKey(column="invoices.invoice_number", value="INV-00002")]


def test_two_columns_of_one_kind_are_one_entity_and_a_filter_anchors_too(catalog):
    """MT4 turn 1 / turn 22: code and name together are one supplier,
    not an ambiguity; the WHERE literal pins the rule."""
    anchors = harvest_turn_anchors([T22], catalog, turn=22)
    assert anchors.entities == [
        Anchor(kind="supplier", column="suppliers.code", value="RVX01", source="cell"),
        Anchor(kind="supplier", column="suppliers.name", value="Ravenswood Extrusion", source="cell"),
        Anchor(kind="rule", column="findings.rule_name", value="correction_ignored", source="filter"),
    ]
    assert anchors.keys == [KnownKey(column="suppliers.code", value="RVX01")]


def test_a_multi_row_column_is_ambiguous_and_its_values_are_still_keys(catalog):
    anchors = harvest_turn_anchors([T23], catalog, turn=23)
    assert [a.kind for a in anchors.entities] == ["rule"]
    assert {k.value for k in anchors.keys} == {"finch", "bo", "ava", "nova", "orin", "tass"}
    assert all(k.column == "users.short_name" for k in anchors.keys)


def test_turn_9s_filter_is_the_rule_it_counted(catalog):
    anchors = harvest_turn_anchors([T9], catalog, turn=9)
    assert anchors.entities == [
        Anchor(kind="rule", column="findings.rule_name", value="new_supplier", source="filter")
    ]


def test_a_cte_pass_through_resolves_to_its_table_and_an_id_column_is_a_key(catalog):
    cte = invocation(
        "WITH top AS (SELECT rule_name, COUNT(*) AS n FROM findings GROUP BY rule_name "
        "ORDER BY n DESC LIMIT 1) SELECT t.rule_name AS rule_name, t.n AS n FROM top t",
        ["rule_name", "n"], [{"rule_name": "line_note", "n": 505}],
    )
    assert harvest_turn_anchors([cte], catalog).entities[0].column == "findings.rule_name"
    ids = invocation(
        "SELECT ih.invoice_id AS invoice_id, COUNT(*) AS n FROM invoice_history ih "
        "GROUP BY ih.invoice_id ORDER BY n DESC LIMIT 1",
        ["invoice_id", "n"], [{"invoice_id": 3, "n": 6}],
    )
    anchors = harvest_turn_anchors([ids], catalog, turn=1)
    assert anchors.entities == [Anchor(kind="invoice", column="invoices.id", value="3", source="cell")]
    assert anchors.keys == [KnownKey(column="invoices.id", value="3")]


def test_two_invocations_that_disagree_are_ambiguous(catalog):
    other = invocation(T6_SQL, ["rule_name", "fire_count"],
                       [{"rule_name": "rate_variance", "fire_count": 383}])
    assert harvest_turn_anchors([T6, other], catalog).entities == []


def test_a_failed_invocation_contributes_nothing_and_a_declared_about_rides_along(catalog):
    failed = invocation(T6_SQL, [], [], status="error")
    anchors = harvest_turn_anchors([failed], catalog, about="new_supplier", question_kind="rule", turn=7)
    assert anchors.entities == [Anchor(kind="rule", column="", value="new_supplier", source="declared")]
    assert harvest_turn_anchors([], catalog, about=None).entities == []


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        # The session, turns 7, 8, 9, 14, 19, 20, 24, 26, 27.
        ("What does that rule check?", "rule"),
        ("Show me its source", None),
        ("How many findings has it produced?", None),
        ("Is that the same for the supplier from earlier?", "supplier"),
        ("Show me an example invoice for it.", None),
        ("What was that invoice's history?", "invoice"),
        ("Did any of those invoices get reactivated?", None),
        ("Back to the first supplier — how many of their invoices are still READY?", None),
        ("What was the backlog total again?", None),
        # The bank's multi-turn follow-ups.
        ("How many of those arrived in each month?", None),
        ("How many of those are critical severity?", None),
        ("Show me the source of the rule that flags it", None),
        ("What was that supplier's total invoice amount again?", "supplier"),
        ("Add each supplier's total invoice-line amount (the summed line prices)", None),
        ("Tell me more about that rule.", "rule"),
        # Conversation 2's back-references name no declared kind.
        ("Please show me that code", None),
        ("And what are the names of each of those rules?", None),
        ("Can you list those columns with their descriptions?", None),
        # A first-turn question refers back to nothing.
        ("Which supplier gets flagged most often for rate variance?", None),
        ("What share of item SVC-4410 service hours were flagged?", None),
        # The other spellings the scan reads.
        ("How many invoices does this vendor have?", "supplier"),
        ("What did the auditor above close?", "auditor"),
        ("Show me the same invoice's lines", "invoice"),
    ],
)
def test_anaphor_kind_reads_only_a_singular_kind_noun(catalog, question, kind):
    assert anaphor_kind(question, catalog) == kind


def test_tokens_keep_codes_whole_and_never_split_a_figure_into_a_key():
    tokens = tokens_of("How many days has Orin worked? INV-00426. $8,123.45 and 1,990 invoices")
    assert {"orin", "inv-00426", "123.45"} <= tokens
    assert "123" not in tokens
    assert "inv-00426." not in tokens


def test_known_values_are_the_users_words_the_keys_and_the_grounding():
    known = known_values(
        ["invoice 123 please", "What about Orin?"],
        [KnownKey(column="invoices.invoice_number", value="INV-00426")],
        grounding_text="WHERE f.rule_name = 'correction_ignored'",
    )
    assert {"123", "orin", "inv-00426", "correction_ignored"} <= known
