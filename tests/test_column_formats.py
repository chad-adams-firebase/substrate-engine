"""Which result columns are money — resolved from the Dictionary Map
and pack display config, never an engine list (CLAUDE.md)."""

from engine.config.models import MoneySettings, RateSettings
from engine.substrates.models import (
    ColumnFormatRule,
    DictionaryMap,
    DocProvenance,
)
from engine.tools.column_formats import money_column_names, resolve_column_formats
from engine.tools.envelope import ColumnFormat

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


# --- Parse-first resolution (the coverage pass, Play Session #2's S-B) ----

RATE = RateSettings(
    fraction=["*_rate", "*_share", "share_*", "*_fraction"],
    percent=["*_pct", "pct_*", "*_percent", "*_percentage", "percent_*"],
)


# The pack's real money list also names invoice_lines.extended_price.
PARSE_COLUMNS = COLUMNS | {"extended_price"}


def _resolve(sql, columns, rate=RATE):
    return resolve_column_formats(columns, PARSE_COLUMNS, MONEY, None, rate, sql=sql)


def test_an_aggregate_inherits_its_source_columns_format():
    """AVG(invoices.opportunity) AS mean_value: the alias says nothing,
    the statement says money."""
    hints = _resolve(
        "SELECT AVG(i.opportunity) AS mean_value, MIN(i.invoice_total) AS smallest "
        "FROM invoices i",
        ["mean_value", "smallest"],
    )
    assert hints["mean_value"].kind == "money"
    assert hints["smallest"].kind == "money"


def test_arithmetic_over_money_columns_is_money_and_a_ratio_of_money_is_not():
    hints = _resolve(
        "SELECT (l.extended_price - f.amount) AS corrected_cost, "
        "SUM(f.amount) * 1.0 / COUNT(DISTINCT i.id) AS per_invoice, "
        "SUM(f.amount) / SUM(i.invoice_total) AS flagged_share, "
        "COUNT(*) AS total_amount "
        "FROM invoices i JOIN findings f ON f.invoice_id = i.id "
        "JOIN invoice_lines l ON l.invoice_id = i.id",
        ["corrected_cost", "per_invoice", "flagged_share", "total_amount"],
        rate=RATE,
    )
    assert hints["corrected_cost"].kind == "money"  # money minus money
    assert hints["per_invoice"].kind == "money"  # money over a count
    assert hints["flagged_share"].kind == "rate"  # money over money: the alias decides
    assert hints["flagged_share"].scale == "fraction"
    # The parse ruled money OUT for a COUNT; the alias's money suffix
    # cannot put it back.
    assert "total_amount" not in hints


def test_cte_columns_are_followed_to_their_real_source():
    """Turn 2.12's row: $1,641.64 beside a raw 2202.2 — original_cost
    was l.extended_price inside a CTE."""
    sql = (
        "WITH example_invoice AS ("
        "  SELECT f.invoice_id, f.amount AS flagged_amount, "
        "         l.extended_price AS original_cost, "
        "         (l.extended_price - f.amount) AS corrected_cost "
        "  FROM findings f JOIN invoice_lines l ON l.invoice_id = f.invoice_id LIMIT 1) "
        "SELECT ei.original_cost, ei.flagged_amount, ei.corrected_cost "
        "FROM example_invoice ei"
    )
    hints = _resolve(sql, ["original_cost", "flagged_amount", "corrected_cost"])
    assert {c: h.kind for c, h in hints.items()} == {
        "original_cost": "money",
        "flagged_amount": "money",
        "corrected_cost": "money",
    }


def test_an_opaque_item_falls_back_to_the_alias_rules():
    """SUM(CASE ...) over a count is unclassifiable; the alias glob
    decides — with no glob, no hint (the pack adds *_savings_*)."""
    sql = (
        "WITH ic AS (SELECT SUM(CASE WHEN x = 1 THEN 0 ELSE f.amount END) AS total_savings, "
        "COUNT(DISTINCT f.invoice_id) AS invoice_count FROM findings f) "
        "SELECT ic.total_savings * 1.0 / ic.invoice_count AS avg_savings_per_invoice, "
        "ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM invoices), 2) AS flag_pct FROM ic"
    )
    hints = _resolve(sql, ["avg_savings_per_invoice", "flag_pct"])
    assert "avg_savings_per_invoice" not in hints
    assert hints["flag_pct"] == ColumnFormat(kind="rate", scale="percent")
    patterned = MoneySettings(symbol="$", column_patterns=["*_savings_*"])
    hints = resolve_column_formats(
        ["avg_savings_per_invoice"], COLUMNS, patterned, None, RATE, sql=sql
    )
    assert hints["avg_savings_per_invoice"].kind == "money"


def test_rate_globs_name_the_scale_and_money_still_wins():
    hints = _kinds_with_rate(["flag_rate", "flag_pct", "avg_unit_rate", "flag_ratio", "n"])
    assert hints["flag_rate"] == ColumnFormat(kind="rate", scale="fraction")
    assert hints["flag_pct"] == ColumnFormat(kind="rate", scale="percent")
    assert hints["avg_unit_rate"].kind == "money"  # unit_rate is a money column
    assert "flag_ratio" not in hints  # a ratio may exceed 1: in neither list
    assert "n" not in hints


def _kinds_with_rate(columns):
    names = COLUMNS | {"unit_rate"}
    return resolve_column_formats(columns, names, MONEY, None, RATE)


def test_no_rate_settings_means_no_rate_formatting():
    assert resolve_column_formats(["flag_rate"], COLUMNS, MONEY, None, None) == {}


def test_without_the_sql_the_alias_rules_stand_alone():
    """The eval grader and older tables resolve by alias only; the
    parse is an addition, never a requirement."""
    assert _kinds(["total_opportunity"]) == {"total_opportunity": "money"}


def test_the_real_pack_config_declares_the_rate_shapes():
    from pathlib import Path

    from engine.config.pack_loader import load_pack

    pack = load_pack(Path(__file__).resolve().parents[1] / "packs" / "invoiceguard")
    display = pack.config.display
    hints = resolve_column_formats(
        ["flag_rate", "application_rate", "flag_pct", "avg_savings_per_invoice", "flag_ratio"],
        set(),
        display.money,
        display.duration,
        display.rate,
    )
    assert hints["flag_rate"].scale == "fraction"
    assert hints["application_rate"].scale == "fraction"
    assert hints["flag_pct"].scale == "percent"
    assert hints["avg_savings_per_invoice"].kind == "money"  # the *_savings_* glob
    assert "flag_ratio" not in hints
