"""The interval-arithmetic lint (tools/interval_lint.py): a timestamp
difference scaled by a numeric literal draws a challenge, because the
difference is an INTERVAL and the scaling shrinks it instead of
converting it. The post-coverage bank's W3 rep 4 is the fixture — the
subtraction in a CTE, the AVG and the /86400 outside — beside every
correct shape that must stay silent."""

from engine.substrates.models import DictionaryRow
from engine.tools.interval_lint import lint_interval_arithmetic
from tests.verifier_support import MACHINE, W3_REP4_SQL


def col(table: str, name: str, data_type: str = "BIGINT") -> DictionaryRow:
    return DictionaryRow(
        table_name=table, column_name=name, data_type=data_type, provenance=MACHINE
    )


DICTIONARY = [
    col("invoice_history", ""),
    col("invoice_history", "invoice_id"),
    col("invoice_history", "at", "TIMESTAMP"),
    col("invoice_history", "to_status", "VARCHAR"),
    col("invoice_history", "from_status", "VARCHAR"),
    col("invoices", "id"),
    col("invoices", "received_at", "TIMESTAMP"),
    col("invoices", "scored_at", "TIMESTAMP"),
    col("invoice_lines", "invoice_id"),
    col("invoice_lines", "extended_price", "DOUBLE"),
    col("scheduled_tasks", "due_at", "DATE"),
    col("scheduled_tasks", "completed_at", "DATE"),
    # created_at is a timestamp in one table and text in another: an
    # unqualified reference cannot be typed.
    col("findings", "created_at", "TIMESTAMP"),
    col("audit_log", "created_at", "VARCHAR"),
]

PAIR = (
    " FROM invoice_history en JOIN invoice_history lv "
    "ON lv.invoice_id = en.invoice_id "
    "WHERE en.to_status = 'RECEIVED' AND lv.from_status = 'RECEIVED'"
)


def test_the_post_coverage_w3_shape_is_challenged_through_its_cte():
    reason = lint_interval_arithmetic(W3_REP4_SQL, DICTIONARY)
    assert reason is not None
    assert reason.startswith("Interval-arithmetic check: `avg_time_in_days` scales")
    assert "EPOCH(a - b)" in reason and "DATE_DIFF('hour', b, a)" in reason
    assert reason.endswith("resend the statement unchanged.")


def test_every_scaled_interval_shape_is_challenged():
    scaled = [
        "SELECT AVG(lv.at - en.at) / 3600 AS avg_hours" + PAIR,
        "SELECT (lv.at - en.at) * 24 AS hours" + PAIR,
        "SELECT 24 * (lv.at - en.at) AS hours" + PAIR,
        "SELECT AVG((lv.at - en.at) / 3600) AS avg_hours" + PAIR,
        "SELECT AGE(lv.at, en.at) / 3600 AS hours" + PAIR,
        # The scaling inside EPOCH is the same defect (guard pass: the
        # numeric functions keep their arguments visible).
        "SELECT EPOCH((lv.at - en.at) / 3600) AS bogus" + PAIR,
        "SELECT AVG(EPOCH((lv.at - en.at) / 86400)) AS bogus" + PAIR,
        "SELECT MAX(lv.at - en.at) / 86400 / 7 AS weeks" + PAIR,
        # The scaling inside the CTE, the aggregate outside.
        "WITH g AS (SELECT (lv.at - en.at) / 3600 AS hours" + PAIR + ") "
        "SELECT AVG(hours) AS avg_hours FROM g",
        # A derived table instead of a CTE.
        "SELECT AVG(g.gap) / 86400 AS avg_days FROM (SELECT lv.at - en.at AS gap"
        + PAIR
        + ") g",
        # One table, two timestamp columns.
        "SELECT AVG(i.scored_at - i.received_at) / 3600 AS avg_hours FROM invoices i",
    ]
    for sql in scaled:
        assert lint_interval_arithmetic(sql, DICTIONARY) is not None, sql


def test_the_correct_shapes_stay_silent():
    silent = [
        # EPOCH first, then scale — the gold's shape and the gotcha's.
        "SELECT AVG(EPOCH(lv.at - en.at)) / 3600.0 AS avg_hours" + PAIR,
        "SELECT EPOCH(lv.at - en.at) / 3600 AS hours" + PAIR,
        "SELECT EXTRACT(EPOCH FROM (lv.at - en.at)) / 60 AS minutes" + PAIR,
        "SELECT AVG(DATE_DIFF('hour', en.at, lv.at)) AS avg_hours" + PAIR,
        "SELECT AVG(JULIAN(lv.at) - JULIAN(en.at)) AS avg_days" + PAIR,
        # An unscaled interval renders as a clock string and humanizes.
        "SELECT AVG(lv.at - en.at) AS avg_gap" + PAIR,
        "SELECT lv.at - en.at AS gap" + PAIR,
        # Arithmetic over numbers is not interval arithmetic.
        "SELECT SUM(l.extended_price) / 100 AS hundreds FROM invoice_lines l",
        "SELECT (l.extended_price - l.extended_price) * 24 AS zero FROM invoice_lines l",
        # DATE - DATE is an integer day count in DuckDB.
        "SELECT (t.completed_at - t.due_at) * 24 AS hours FROM scheduled_tasks t",
        # A timestamp minus an interval literal is a timestamp.
        "SELECT (lv.at - INTERVAL '1 day') AS yesterday" + PAIR,
        # An unqualified name two tables type differently is untyped.
        "SELECT (created_at - created_at) / 3600 AS h FROM findings",
        # One-argument AGE is against the wall clock: outside the parse.
        "SELECT AGE(lv.at) / 3600 AS hours" + PAIR,
    ]
    for sql in silent:
        assert lint_interval_arithmetic(sql, DICTIONARY) is None, sql


def test_each_scaled_item_is_named_once():
    reason = lint_interval_arithmetic(
        "SELECT AVG(lv.at - en.at) / 3600 AS avg_hours, "
        "MAX(lv.at - en.at) / 86400 AS max_days, "
        "COUNT(*) AS n" + PAIR,
        DICTIONARY,
    )
    assert "`avg_hours`, `max_days` scales" in reason
    assert "`n`" not in reason


def test_a_dictionary_without_timestamps_means_no_lint():
    plain = [col("invoices", "id"), col("invoices", "received_at", "VARCHAR")]
    assert lint_interval_arithmetic(
        "SELECT (i.received_at - i.received_at) / 3600 AS h FROM invoices i", plain
    ) is None
