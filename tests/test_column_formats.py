"""Which result columns are money — resolved from the Dictionary Map
and pack display config, never an engine list (CLAUDE.md)."""

from engine.config.models import MoneySettings
from engine.substrates.models import (
    ColumnFormatRule,
    DictionaryMap,
    DocProvenance,
)
from engine.tools.column_formats import money_column_names, resolve_column_formats

MONEY = MoneySettings(symbol="$")
MAP = DictionaryMap(
    provenance=DocProvenance(source="human", confidence=1.0, needs_validation=False),
    column_formats=[
        ColumnFormatRule(
            format="money",
            columns=["invoices.opportunity", "invoices.invoice_total", "findings.amount"],
        )
    ],
)
COLUMNS = money_column_names(MAP)


def _kinds(columns, money=MONEY, names=COLUMNS):
    return {
        column: hint.kind
        for column, hint in resolve_column_formats(columns, names, money).items()
    }


def test_map_columns_are_bare_names_because_aliases_carry_no_table():
    assert COLUMNS == {"opportunity", "invoice_total", "amount"}


def test_declared_columns_and_aggregate_aliases_are_money():
    assert _kinds(["opportunity", "total_opportunity", "sum_invoice_total", "n"]) == {
        "opportunity": "money",
        "total_opportunity": "money",
        "sum_invoice_total": "money",
    }


def test_marker_tokens_veto_an_alias_that_merely_ends_in_a_money_name():
    # Approved narrowing: amount_count, opportunity_pct, invoice_total_rank
    # are not dollars — and neither is an alias whose LAST token is
    # not the money name at all.
    assert _kinds(
        [
            "amount_count",
            "opportunity_pct",
            "invoice_total_rank",
            "count_opportunity",
            "opportunity_share",
            "n_amount",
            "opportunity_rate",
        ]
    ) == {}


def test_last_token_rule_needs_the_whole_money_name_as_a_suffix():
    # "total" alone is not a money column; "invoice_total" is.
    assert _kinds(["grand_total", "total"]) == {}
    assert _kinds(["max_invoice_total"]) == {"max_invoice_total": "money"}


def test_pack_patterns_extend_the_list_and_bypass_the_markers():
    money = MoneySettings(symbol="$", column_patterns=["*_savings", "recovered_*"])
    assert _kinds(["rule_savings", "recovered_share", "savings_pct"], money=money) == {
        "rule_savings": "money",
        "recovered_share": "money",
    }


def test_markers_are_pack_overridable():
    money = MoneySettings(symbol="$", non_money_markers=["count"])
    assert _kinds(["pct_opportunity", "count_amount"], money=money) == {
        "pct_opportunity": "money"
    }


def test_no_money_settings_means_no_formatting():
    assert resolve_column_formats(["opportunity"], COLUMNS, None) == {}


def test_symbol_travels_with_the_hint():
    formats = resolve_column_formats(["amount"], COLUMNS, MoneySettings(symbol="£"))
    assert formats["amount"].symbol == "£"


def test_matching_is_case_insensitive_and_keys_keep_the_alias_spelling():
    assert list(resolve_column_formats(["Total_Opportunity"], COLUMNS, MONEY)) == [
        "Total_Opportunity"
    ]
