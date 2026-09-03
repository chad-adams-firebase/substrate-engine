"""The guard pass's rule for every lint's challenge text: a challenge
names what is wrong with the query; it never suggests a different
subject table. The post-duration bank's AMB2 breached on exactly that —
the enum challenge said where else a value was observed, the model
read it as an instruction, and 6,432 transitions shipped verified as
an invoice count.

Mechanised: every dictionary table a challenge names OUTSIDE
parentheses is a table the statement already queries. Parenthesised
text is explanation (the fan-out check's "(both columns are foreign
keys to invoices.id — …)" names the FK target to explain the fan);
imperative text is where a destination would hide. One test per lint,
on the fixture that breached it into existence."""

import re

from engine.tools.enum_lint import lint_enum_literals
from engine.tools.interval_lint import lint_interval_arithmetic
from engine.tools.sql_lint import lint_fan_out
from tests import test_tool_enum_lint as enum_fixtures
from tests import test_tool_interval_lint as interval_fixtures
from tests import test_tool_sql_lint as fan_out_fixtures
from tests.verifier_support import W3_REP4_SQL

_PARENTHESISED = re.compile(r"\([^()]*\)")
_WORD = re.compile(r"\b[a-z_]+\b")
_FROM_JOIN = re.compile(r"\b(?:from|join)\s+([A-Za-z_]\w*)", re.IGNORECASE)


def tables_named_outside_parentheses(challenge: str, tables: set[str]) -> set[str]:
    text = challenge
    while (stripped := _PARENTHESISED.sub("", text)) != text:
        text = stripped
    return {word for word in _WORD.findall(text.lower()) if word in tables}


def queried_tables(sql: str) -> set[str]:
    """Every name after FROM or JOIN — the statement's own tables, read
    the plain way the Verifier reads them."""
    return {name.lower() for name in _FROM_JOIN.findall(sql)}


def _tables(dictionary) -> set[str]:
    return {row.table_name.lower() for row in dictionary if row.table_name}


def _assert_keeps_its_tables(challenge: str, sql: str, dictionary) -> None:
    assert challenge is not None
    named = tables_named_outside_parentheses(challenge, _tables(dictionary))
    assert named <= queried_tables(sql), (
        f"challenge names an unqueried table outside parentheses: "
        f"{sorted(named - queried_tables(sql))}\n{challenge}"
    )


def test_the_enum_challenge_keeps_the_query_on_its_table():
    """R-A's fixture: 'REJECTED' is observed on invoices
    .supplier_acceptance, and the challenge does not say so."""
    challenge = lint_enum_literals(enum_fixtures.R_A, enum_fixtures.DICTIONARY)
    _assert_keeps_its_tables(challenge, enum_fixtures.R_A, enum_fixtures.DICTIONARY)
    assert "Keep the query on `invoice_history`" in challenge


def test_the_fan_out_challenges_keep_their_tables():
    fixtures = fan_out_fixtures
    for sql in (
        fixtures.MT2_FANOUT,
        fixtures.MT2_EXPRESSION_JOIN,
        fixtures.B5_DEAD_LEFT_JOIN,
        fixtures.S2_AVG_OVER_NULL_SIDE,
    ):
        challenge = lint_fan_out(sql, fixtures.DICTIONARY, fixtures.MAP)
        _assert_keeps_its_tables(challenge, sql, fixtures.DICTIONARY)


def test_the_interval_challenge_names_no_table_at_all():
    challenge = lint_interval_arithmetic(W3_REP4_SQL, interval_fixtures.DICTIONARY)
    assert challenge is not None
    assert tables_named_outside_parentheses(
        challenge, _tables(interval_fixtures.DICTIONARY)
    ) == set()


def test_the_helper_reads_parentheses_as_explanation():
    """The mechanism the rule relies on, pinned: a table inside
    parentheses is explanation and does not count; the same name
    outside does."""
    tables = {"invoices", "findings"}
    assert tables_named_outside_parentheses(
        "count findings (several findings rows share one invoices row)", tables
    ) == {"findings"}
    assert tables_named_outside_parentheses(
        "count findings; query invoices instead", tables
    ) == {"findings", "invoices"}
    assert tables_named_outside_parentheses("(a (nested invoices) note)", tables) == set()
