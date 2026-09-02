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


def test_rate_named_money_columns_survive_the_rate_marker():
    """The play-pass veto-ordering fix: the pack declares three _rate
    money columns (unit_rate, contract_rate, requested_rate), and the
    engine-default "rate" marker used to veto every aggregate alias
    over them (avg_unit_rate rendered unformatted). Markers veto only
    tokens BEFORE the matched money suffix — a marker inside the
    money column's own name is the name, not a veto."""
    rate_map = DictionaryMap(
        provenance=DocProvenance(
            source="human", confidence=1.0, needs_validation=False
        ),
        column_formats=[
            ColumnFormatRule(
                format="money",
                columns=["invoice_lines.unit_rate", "invoices.opportunity"],
            )
        ],
    )
    names = money_column_names(rate_map)
    assert _kinds(
        ["avg_unit_rate", "unit_rate", "max_unit_rate"], names=names
    ) == {
        "avg_unit_rate": "money",
        "unit_rate": "money",
        "max_unit_rate": "money",
    }
    # Markers before the suffix still veto; a bare marker suffix and a
    # partial name still fail the suffix rule.
    assert _kinds(
        ["count_unit_rate", "unit_rate_pct", "rate", "opportunity_rate"],
        names=names,
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


# --- Durations (Phase 5 Block 2) -------------------------------------

from engine.config.models import DurationSettings  # noqa: E402

DURATION = DurationSettings(
    days=["*_days", "days_*"],
    hours=["*_hours"],
    clock=["*duration*", "*time_in_*"],
)


def _hints(columns, money=MONEY, duration=DURATION):
    return {
        column: (hint.kind, hint.unit)
        for column, hint in resolve_column_formats(
            columns, COLUMNS, money, duration
        ).items()
    }


def test_duration_aliases_carry_the_unit_their_list_names():
    assert _hints(["avg_days", "days_in_ready", "service_hours", "n"]) == {
        "avg_days": ("duration", "days"),
        "days_in_ready": ("duration", "days"),
        "service_hours": ("duration", "hours"),
    }


def test_clock_string_aliases_carry_no_unit():
    # The cells are H:MM:SS strings; the string says what it counts.
    assert _hints(["avg_duration", "time_in_received"]) == {
        "avg_duration": ("duration", None),
        "time_in_received": ("duration", None),
    }


def test_money_wins_when_an_alias_reads_as_both():
    money = MoneySettings(symbol="$", column_patterns=["*_hours"])
    assert _hints(["billed_hours"], money=money) == {"billed_hours": ("money", None)}


def test_no_duration_settings_means_no_duration_formatting():
    assert _hints(["avg_days"], duration=None) == {}
    assert resolve_column_formats(["avg_days"], COLUMNS, None, None) == {}


def test_the_real_pack_config_declares_the_play_session_shapes():
    """The InvoiceGuard config's duration block must catch the aliases
    the play session actually produced — a julianday difference and a
    time() string — or the sighting recurs."""
    from pathlib import Path

    from engine.config.pack_loader import load_pack

    pack = load_pack(Path(__file__).resolve().parents[1] / "packs" / "invoiceguard")
    display = pack.config.display
    hints = resolve_column_formats(
        ["avg_days_between", "avg_time_in_received", "avg_service_hours", "invoice_count"],
        set(),
        display.money,
        display.duration,
    )
    assert hints["avg_days_between"].unit == "days"
    assert hints["avg_time_in_received"].kind == "duration"
    assert hints["avg_service_hours"].unit == "hours"
    assert "invoice_count" not in hints
