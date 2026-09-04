"""The guard pass's rule for every lint's challenge text: a challenge
names what is wrong with the query; it never suggests a different
subject table. The post-duration bank's AMB2 breached on exactly that —
the enum challenge said where else a value was observed, the model
read it as an instruction, and 6,432 transitions shipped verified as
an invoice count.

Mechanised: every dictionary table a challenge names — anywhere in
its text — is a table the statement already queries. The guard pass
exempted parenthesised explanation (the fan-out check's "(both
columns are foreign keys to invoices.id — …)"); the Polish Pass
removed the exemption and rewrote that reason ("… with the same
target"), so a parenthetical cannot hide a destination either. One
test per lint, on the fixtures that breached it into existence."""

import re

from engine.tools.enum_lint import lint_enum_literals
from engine.tools.interval_lint import lint_interval_arithmetic
from engine.tools.key_lint import lint_placeholders
from engine.tools.sql_lint import lint_fan_out
from tests import test_tool_enum_lint as enum_fixtures
from tests import test_tool_interval_lint as interval_fixtures
from tests import test_tool_key_lint as key_fixtures
from tests import test_tool_sql_lint as fan_out_fixtures
from tests.verifier_support import W3_REP4_SQL

_WORD = re.compile(r"\b[a-z_]+\b")
_FROM_JOIN = re.compile(r"\b(?:from|join)\s+([A-Za-z_]\w*)", re.IGNORECASE)


def tables_named(challenge: str, tables: set[str]) -> set[str]:
    """Every dictionary table the challenge names, parentheses included:
    explanation is text a model reads too."""
    return {word for word in _WORD.findall(challenge.lower()) if word in tables}


def queried_tables(sql: str) -> set[str]:
    """Every name after FROM or JOIN — the statement's own tables, read
    the plain way the Verifier reads them."""
    return {name.lower() for name in _FROM_JOIN.findall(sql)}


def _tables(dictionary) -> set[str]:
    return {row.table_name.lower() for row in dictionary if row.table_name}


def _assert_keeps_its_tables(challenge: str, sql: str, dictionary) -> None:
    assert challenge is not None
    named = tables_named(challenge, _tables(dictionary))
    assert named <= queried_tables(sql), (
        f"challenge names an unqueried table: "
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
        fixtures.FO_EXCEPT_NAIVE,
        fixtures.W1_OVERRIDE,
        fixtures.W7_BANDAID,
        fixtures.S3_NO_FK,
        fixtures.FLAGSHIP_ATTEMPT_1,
        fixtures.TWO_MANY_SIDES,
        fixtures.LOOKUP_SIDE_SUM,
        fixtures.WF_UNFILTERED,
        fixtures.HISTORY_FILTER_OUTSIDE_SET,
        fixtures.HISTORY_FILTER_NOT_IN,
        fixtures.HISTORY_FILTER_WITH_TOP_LEVEL_OR,
        fixtures.HISTORY_FILTER_ON_THE_OTHER_ALIAS,
        fixtures.S2_REPAIRED_WITH_COALESCE,
        fixtures.GROUPED_CTE_TO_FK_SIDE,
        fixtures.DERIVED_TABLE_TO_FK_SIDE,
        fixtures.GROUPED_CTE_ON_NON_KEY,
        fixtures.LOOKUP_CTE_SUM_OVER_LOOKUP,
        fixtures.FILTERED_PASSTHROUGH_CTE_COUNT,
        fixtures.S2_CTE_PAIR,
        fixtures.PASSTHROUGH_PK_THROUGH_A_FANNED_BODY,
        fixtures.S2_HIDDEN_FAN,
        fixtures.HIDDEN_W1_SUM,
        fixtures.CTE_CHAIN,
    ):
        challenge = lint_fan_out(sql, fixtures.DICTIONARY, fixtures.MAP)
        _assert_keeps_its_tables(challenge, sql, fixtures.DICTIONARY)
    # The self-join challenged only when nothing vouches for its sides,
    # flat and read through a CTE.
    for sql in (fixtures.W3_SELF_JOIN, fixtures.W3_CTE_SELF_JOIN):
        challenge = lint_fan_out(sql, fixtures.DICTIONARY, fixtures.MAP_WITHOUT_RECEIVED)
        _assert_keeps_its_tables(challenge, sql, fixtures.DICTIONARY)


def test_the_interval_challenge_names_no_table_at_all():
    challenge = lint_interval_arithmetic(W3_REP4_SQL, interval_fixtures.DICTIONARY)
    assert challenge is not None
    assert tables_named(
        challenge, _tables(interval_fixtures.DICTIONARY)
    ) == set()


def test_the_helper_reads_parentheses_too():
    """The mechanism the rule relies on, pinned: a table named inside
    parentheses counts exactly like one outside — the Polish Pass
    dropped the explanation exemption."""
    tables = {"invoices", "findings"}
    assert tables_named(
        "count findings (several findings rows share one invoices row)", tables
    ) == {"findings", "invoices"}
    assert tables_named("count findings; query invoices instead", tables) == {
        "findings", "invoices",
    }
    assert tables_named("(a (nested invoices) note)", tables) == {"invoices"}
    assert tables_named("the invoices_to_lines path", tables) == set()


def test_the_placeholder_challenge_names_no_table_at_all():
    """Backlog Pass: the confession is about a value, and the challenge
    says so without naming even the queried table — the stricter form,
    like the interval check."""
    for sql in (key_fixtures.T20_PLACEHOLDER_ATTEMPT, key_fixtures.T20_BIND_ATTEMPT):
        challenge = lint_placeholders(sql)
        assert challenge is not None
        assert tables_named(challenge, _tables(enum_fixtures.DICTIONARY)) == set()
        assert "resend" not in challenge
