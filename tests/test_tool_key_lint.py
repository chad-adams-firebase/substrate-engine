"""The placeholder lint (tools/key_lint.py): the 30-turn session's turn
20, replayed. "What was that invoice's history?" drafted a bind
parameter, then a literal with a comment confessing it — and the
confession executed as another invoice's history, verified. Both
recorded attempts are the fixtures that fire; the negatives are the
shapes a comparison operator, a cast, a timestamp, or a harmless
comment must never trip (user amendment: `<>`, `a < b`, `x <= y` are
operators, not placeholders)."""

from pathlib import Path

import pytest

from engine.substrates.jsonl import read_rows
from engine.substrates.models import DictionaryRow
from engine.substrates.pack_data import load_dictionary_map
from engine.tools.entities import EntityCatalog, known_values
from engine.tools.envelope import KnownKey
from engine.tools.key_lint import lint_placeholders, lint_ungrounded_keys, split_comments

PACK = Path(__file__).parent.parent / "packs" / "invoiceguard"


@pytest.fixture(scope="module")
def catalog():
    return EntityCatalog.from_substrates(
        read_rows(PACK / "substrates" / "dictionary.jsonl", DictionaryRow),
        load_dictionary_map(PACK / "dictionary_map.yaml"),
    )


def known(*texts, keys=(), grounding=""):
    return known_values(list(texts), list(keys), grounding)

# packs/invoiceguard/work.db conversation 1, turn 20, attempts 1 and 2,
# verbatim (2026-09-04). Attempt 1 failed to parse; attempt 2 executed
# and returned invoice 123's three transitions.
T20_BIND_ATTEMPT = (
    "SELECT \n    ih.from_status AS from_status,\n    ih.to_status AS to_status,\n"
    "    ih.actor AS actor,\n    ih.at AS transition_time\nFROM \n"
    "    invoice_history ih\nWHERE \n    ih.invoice_id = :invoice_id\nORDER BY \n    ih.at"
)
T20_PLACEHOLDER_ATTEMPT = (
    "SELECT \n    ih.from_status AS from_status,\n    ih.to_status AS to_status,\n"
    "    ih.actor AS actor,\n    ih.at AS transition_time\nFROM \n"
    "    invoice_history ih\nWHERE \n"
    "    ih.invoice_id = 123 -- Replace 123 with the actual invoice ID\nORDER BY \n    ih.at"
)
T20_CLEAN = (
    "SELECT ih.from_status AS from_status, ih.to_status AS to_status, "
    "ih.actor AS actor, ih.at AS transition_time FROM invoice_history ih "
    "WHERE ih.invoice_id = 1 ORDER BY ih.at"
)


def test_the_recorded_confession_is_challenged_naming_the_comment():
    reason = lint_placeholders(T20_PLACEHOLDER_ATTEMPT)
    assert reason is not None
    assert reason.startswith(
        "Placeholder check: the comment `Replace 123 with the actual invoice ID` says"
    )
    assert "Remove the comment" in reason
    assert "ask the user which one is meant" in reason
    # Hard: no license to resend unchanged.
    assert "resend" not in reason


def test_the_recorded_bind_parameter_is_challenged_as_a_shape():
    reason = lint_placeholders(T20_BIND_ATTEMPT)
    assert reason == (
        "Placeholder check: `:invoice_id` is a bind placeholder, not a value. "
        "Use a value the conversation established, or ask the user which one "
        "is meant."
    )


def test_a_leading_comment_is_read_from_the_fenced_block():
    """extract_sql pops leading comment lines before the lints see the
    statement; the confession on the first line still counts because
    run_sql hands the lint the fenced block for its comments."""
    fenced = "-- replace 5 with the real supplier id\nSELECT 1 FROM suppliers WHERE id = 5"
    stripped = "SELECT 1 FROM suppliers WHERE id = 5"
    assert lint_placeholders(stripped) is None
    reason = lint_placeholders(stripped, comment_source=fenced)
    assert reason is not None and "replace 5 with the real supplier id" in reason


def test_a_block_comment_admission_fires_too():
    sql = "SELECT 1 FROM invoices WHERE id = 7 /* TODO: use the actual id */"
    reason = lint_placeholders(sql)
    assert reason is not None and "use the actual id" in reason


def test_every_bind_shape_fires():
    for shape, sql in (
        (":invoice_id", "SELECT 1 FROM t WHERE id = :invoice_id"),
        ("?", "SELECT 1 FROM t WHERE id = ?"),
        ("$1", "SELECT 1 FROM t WHERE id = $1"),
        ("{{invoice_id}}", "SELECT 1 FROM t WHERE id = {{invoice_id}}"),
        ("<invoice_id>", "SELECT 1 FROM t WHERE id = <invoice_id>"),
        ("placeholder", "SELECT 1 FROM t WHERE id = placeholder"),
        ("?", "SELECT 1 FROM t WHERE id IN (?, ?)"),
    ):
        reason = lint_placeholders(sql)
        assert reason is not None and f"`{shape}` is a bind placeholder" in reason, sql


def test_comparison_operators_casts_and_timestamps_never_trip():
    """User amendment: `<>`, `a < b`, `x <= y` are operators. And the
    other shapes a real statement carries around a colon or a bracket."""
    for sql in (
        "SELECT 1 FROM t WHERE a <> b",
        "SELECT 1 FROM t WHERE a < b AND c > d",
        "SELECT 1 FROM t WHERE x <= y OR x >= y",
        "SELECT 1 FROM t WHERE a<b AND c>d",
        "SELECT x::DOUBLE / y::DOUBLE AS r FROM t",
        "SELECT 1 FROM t WHERE at >= TIMESTAMP '2026-03-20 11:05:00'",
        "SELECT 1 FROM t WHERE note = 'replace me' AND code = ':x' AND q = '?'",
        "SELECT COUNT(*) AS n FROM t WHERE status IN ('CLOSED', 'NO_REVIEW_NEEDED')",
        "SELECT 1 FROM t WHERE amount > 100 -- terminal statuses only",
        "SELECT 1 FROM t /* the received-to-ready window */ WHERE a = 1",
        "SELECT CASE WHEN a > b THEN 1 ELSE 0 END AS flag FROM t",
        T20_CLEAN,
    ):
        assert lint_placeholders(sql) is None, sql


def test_split_comments_steps_over_literals_and_spans_block_comments():
    text = "SELECT 'a -- not a comment' AS s /* multi\nline */ FROM t -- tail\nWHERE x = 'it''s'"
    stripped, comments = split_comments(text)
    assert comments == ["/* multi\nline */", "-- tail"]
    assert "'a -- not a comment'" in stripped
    assert "it''s" in stripped
    assert "multi" not in stripped and "tail" not in stripped


# --- The ungrounded-key lint -------------------------------------------


def test_the_recorded_literal_is_challenged_on_its_own_table(catalog):
    """Turn 20: 123 appeared in no result, question, or grounding —
    turn 19 had shown INV-00002 and never an id."""
    reason = lint_ungrounded_keys(
        T20_PLACEHOLDER_ATTEMPT, catalog,
        known("What was that invoice's history?", keys=[KnownKey(column="invoices.invoice_number", value="INV-00002")]),
    )
    assert reason == (
        "Key check: `invoice_history.invoice_id = 123` — 123 appears in no "
        "result, question, or grounding this conversation has seen. Filter on "
        "a key the conversation carries, or ask the user which one is meant. "
        "If the value came from the user, resend the statement unchanged."
    )


def test_a_key_the_conversation_showed_passes(catalog):
    sql = "SELECT ih.to_status AS to_status FROM invoice_history ih WHERE ih.invoice_id = 440"
    assert lint_ungrounded_keys(sql, catalog, known(keys=[KnownKey(column="invoices.id", value="440")])) is None
    assert lint_ungrounded_keys(sql, catalog, known("show invoice 440")) is None
    assert lint_ungrounded_keys(sql, catalog, known(grounding="... id = 440 ...")) is None
    assert lint_ungrounded_keys(sql, catalog, known("nothing here")) is not None


def test_legitimate_literals_never_trip(catalog):
    """Thresholds, dates, LIMIT, enum values, a revision, a line number:
    not id-like. The user's own item code and rule code (S2, ZT-CR147)
    and a name column are never challenged; casefold reads Orin as orin."""
    for sql, texts in (
        ("SELECT COUNT(*) AS n FROM invoices WHERE invoice_total > 100 AND revision = 1 LIMIT 5", ()),
        ("SELECT COUNT(*) AS n FROM invoices WHERE status IN ('CLOSED', 'NO_REVIEW_NEEDED')", ()),
        ("SELECT COUNT(*) AS n FROM invoices WHERE received_at >= DATE '2026-03-20'", ()),
        ("SELECT COUNT(*) AS n FROM invoice_lines WHERE line_number = 3", ()),
        ("SELECT COUNT(*) AS n FROM invoice_lines l WHERE l.item_code = 'SVC-4410'",
         ("What share of item SVC-4410 service hours were flagged?",)),
        ("SELECT COUNT(*) AS n FROM compliance_rules WHERE rule_code = 'CR-147'", ()),
        ("SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'duplicate_line'", ()),
        ("SELECT COUNT(*) AS n FROM invoices i JOIN suppliers s ON s.id = i.supplier_id WHERE s.name = 'Ravenswood Extrusion'", ()),
        ("SELECT COUNT(*) AS n FROM users u WHERE u.short_name = 'orin'", ("How many days has Orin worked?",)),
        ("SELECT COUNT(*) AS n FROM invoices i WHERE i.supplier_id = (SELECT id FROM suppliers WHERE code = 'RVX01')",
         ("How many invoices from RVX01?",)),
    ):
        assert lint_ungrounded_keys(sql, catalog, known(*texts)) is None, sql


def test_an_invented_natural_key_is_challenged_and_an_in_list_names_its_missing_members(catalog):
    reason = lint_ungrounded_keys(
        "SELECT 1 FROM invoices WHERE invoice_number = 'INV-99999'", catalog, known("the invoice")
    )
    assert reason is not None and "`invoices.invoice_number = 'INV-99999'`" in reason
    reason = lint_ungrounded_keys(
        "SELECT 1 FROM invoices i WHERE i.id IN (1, 2, 3)", catalog, known("invoices 1 and 2")
    )
    assert reason is not None
    assert "`invoices.id IN (1, 2, 3)` — 3 appears in no result" in reason


def test_the_key_challenge_licenses_a_resend(catalog):
    reason = lint_ungrounded_keys(T20_PLACEHOLDER_ATTEMPT, catalog, known())
    assert reason is not None and reason.endswith("resend the statement unchanged.")
