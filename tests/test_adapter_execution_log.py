"""LogfmtExecutionLog against the carved slice (2026-03-11 + -12).

The slice contains the planted benchmark-outage day: 30
benchmark_fallback WARNINGs on 03-11 and none on 03-12, plus one
stale_sweep_completed per day — so ran/didn't-run and errors/no-errors
are all real lookups, not synthetic lines.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.adapters.execution_log_logfmt import (
    ComponentTemplate,
    LogfmtExecutionLog,
    LogfmtSettings,
)
from engine.ports.execution_log import ExecutionLogError
from engine.ports.types import TimeWindow

SLICE = (
    Path(__file__).parent
    / "fixtures"
    / "invoiceguard_snapshot"
    / "logs"
    / "invoiceguard.log"
)

COMPONENTS = {
    "stale_sweep": ComponentTemplate(
        logger="invoiceguard.lapse_lifecycle", ran_event="stale_sweep_completed"
    ),
    "benchmark_scoring": ComponentTemplate(
        logger="invoiceguard.benchmark_scoring",
        ran_event="benchmark_scored",
        key_field="invoice_id",
        error_levels=["WARNING", "ERROR"],
    ),
    "rules_engine": ComponentTemplate(
        logger="invoiceguard.rules_engine",
        ran_event="rules_completed",
        key_field="invoice_id",
    ),
}


def _adapter(max_evidence_lines: int = 50) -> LogfmtExecutionLog:
    return LogfmtExecutionLog(
        LogfmtSettings(
            path=str(SLICE),
            components=COMPONENTS,
            max_evidence_lines=max_evidence_lines,
        )
    )


def _day(day: int) -> TimeWindow:
    return TimeWindow(
        start=datetime(2026, 3, day, tzinfo=UTC),
        end=datetime(2026, 3, day + 1, tzinfo=UTC),
    )


def test_did_run_finds_the_stale_sweep_on_the_outage_day():
    status = _adapter().did_run("stale_sweep", "", _day(11))
    assert status.ran is True
    assert status.count == 1
    assert len(status.matched_lines) == 1
    assert "stale_sweep_completed" in status.matched_lines[0]
    assert "1" in status.detail


def test_did_run_is_false_outside_the_slice():
    status = _adapter().did_run("stale_sweep", "", _day(13))
    assert status.ran is False
    assert status.count == 0
    assert status.matched_lines == []


def test_did_run_counts_across_days():
    window = TimeWindow(
        start=datetime(2026, 3, 11, tzinfo=UTC),
        end=datetime(2026, 3, 13, tzinfo=UTC),
    )
    assert _adapter().did_run("stale_sweep", "", window).count == 2


def test_keyed_did_run_narrows_to_one_unit():
    status = _adapter().did_run("rules_engine", "219", _day(11))
    assert status.ran is True
    assert status.count == 1
    assert "invoice_id=219" in status.matched_lines[0]
    assert _adapter().did_run("rules_engine", "999999", _day(11)).ran is False


def test_key_without_key_field_is_an_error_not_a_wider_answer():
    with pytest.raises(ExecutionLogError, match="key_field"):
        _adapter().did_run("stale_sweep", "42", _day(11))


def test_unknown_component_lists_known_ones():
    with pytest.raises(ExecutionLogError, match="stale_sweep"):
        _adapter().did_run("nope", "", _day(11))


def test_naive_window_is_rejected():
    naive = TimeWindow(
        start=datetime(2026, 3, 11), end=datetime(2026, 3, 12)
    )
    with pytest.raises(ExecutionLogError, match="aware"):
        _adapter().did_run("stale_sweep", "", naive)


def test_recent_errors_returns_the_planted_outage():
    events = _adapter().recent_errors("benchmark_scoring", _day(11))
    assert len(events) == 30
    assert {e.event for e in events} == {"benchmark_fallback"}
    assert {e.level for e in events} == {"WARNING"}
    # Quoted values parse whole, spaces and all.
    assert "Connection refused" in events[0].fields["error"]
    assert events[0].raw.startswith("ts=2026-03-11T")


def test_recent_errors_is_empty_on_the_normal_day():
    assert _adapter().recent_errors("benchmark_scoring", _day(12)) == []


def test_matched_lines_are_capped_but_count_is_not():
    # 30 rules_completed events on 03-11; receipts cap at 5, the count
    # stays truthful.
    status = _adapter(max_evidence_lines=5).did_run("rules_engine", "", _day(11))
    assert status.count == 30
    assert len(status.matched_lines) == 5


def test_malformed_line_raises(tmp_path):
    path = tmp_path / "bad.log"
    path.write_text(
        "ts=2026-03-11T08:00:00+00:00 level=INFO logger=x event=y ok=1\n"
        "this is not logfmt\n",
        encoding="utf-8",
    )
    adapter = LogfmtExecutionLog(
        LogfmtSettings(
            path=str(path),
            components={"x": ComponentTemplate(logger="x", ran_event="y")},
        )
    )
    with pytest.raises(ExecutionLogError, match="bad.log:2"):
        adapter.did_run("x", "", _day(11))
