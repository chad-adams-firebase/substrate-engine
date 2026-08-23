"""check_execution through the tool envelope: the answer in output,
the raw lines in evidence, config-shaped errors."""

from datetime import UTC, datetime

from tests.conftest import build_tool_registry


def _day(day: int) -> dict:
    return {
        "window_start": datetime(2026, 3, day, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 3, day + 1, tzinfo=UTC).isoformat(),
    }


def test_did_run_answers_from_the_real_slice(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "check_execution", {"component": "stale_sweep", **_day(11)}
    )
    assert invocation.status == "ok", invocation.error
    status = invocation.output.run_status
    assert status.ran is True and status.count == 1
    # Receipts live in evidence, not in the LLM-visible output.
    assert status.matched_lines == []
    assert len(invocation.evidence.lines) == 1
    assert "stale_sweep_completed" in invocation.evidence.lines[0]
    assert invocation.evidence.truncated is False


def test_did_run_false_outside_the_window(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "check_execution", {"component": "stale_sweep", **_day(13)}
    )
    assert invocation.output.run_status.ran is False


def test_recent_errors_returns_parsed_rows_and_raw_lines(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "check_execution",
        {"component": "benchmark_scoring", "mode": "recent_errors", **_day(11)},
    )
    assert invocation.status == "ok"
    errors = invocation.output.errors
    assert len(errors) == 30
    assert errors[0]["event"] == "benchmark_fallback"
    assert "Connection refused" in errors[0]["error"]
    assert len(invocation.evidence.lines) == 30


def test_recent_errors_cap_is_visible(tool_pack):
    import yaml

    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    config["tool_settings"] = {"check_execution": {"max_errors": 10}}
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "check_execution",
        {"component": "benchmark_scoring", "mode": "recent_errors", **_day(11)},
    )
    assert len(invocation.output.errors) == 10
    assert invocation.evidence.truncated is True


def test_unknown_component_error_lists_known_ones(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "check_execution", {"component": "no_such", **_day(11)}
    )
    assert invocation.status == "error"
    assert "stale_sweep" in invocation.error


def test_backwards_window_rejected_at_validation(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "check_execution",
        {
            "component": "stale_sweep",
            "window_start": "2026-03-12T00:00:00+00:00",
            "window_end": "2026-03-11T00:00:00+00:00",
        },
    )
    assert invocation.status == "error"
    assert "window_start" in invocation.error


def _enable_coverage(tool_pack) -> None:
    import yaml

    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    config["tool_settings"] = {
        "check_execution": {
            "coverage_columns": [
                "invoices.received_at",
                "invoice_history.at",
            ],
        }
    }
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))


def test_window_outside_coverage_is_a_steering_error(tool_pack):
    # Carryback #3a: "May 29" with no year became a 2023 window, and
    # the honest ran:false for that window would have VERIFIED a wrong
    # "no". Entirely-outside windows now come back as a steering error.
    _enable_coverage(tool_pack)
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "check_execution",
        {
            "component": "stale_sweep",
            "window_start": datetime(2023, 5, 29, tzinfo=UTC).isoformat(),
            "window_end": datetime(2023, 5, 30, tzinfo=UTC).isoformat(),
        },
    )
    assert invocation.status == "error"
    assert invocation.output is None
    assert "2026-03-02" in invocation.error  # coverage start, named
    assert "2026-03-10" in invocation.error  # coverage end, named
    assert "coverage" in invocation.error


def test_window_inside_or_overhanging_coverage_runs(tool_pack):
    # The snapshot slice's coverage ends 2026-03-10; day 11 overhangs
    # within the grace days and must still answer from the log.
    _enable_coverage(tool_pack)
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "check_execution", {"component": "stale_sweep", **_day(11)}
    )
    assert invocation.status == "ok", invocation.error
    assert invocation.output.run_status.ran is True


def test_missing_coverage_column_fails_at_build(tool_pack):
    import pytest
    import yaml

    from engine.runtime.tools import ToolBuildError

    config = yaml.safe_load((tool_pack / "config.yaml").read_text())
    config["tool_settings"] = {
        "check_execution": {"coverage_columns": ["invoices.no_such"]}
    }
    (tool_pack / "config.yaml").write_text(yaml.safe_dump(config))
    with pytest.raises(ToolBuildError, match="invoices.no_such"):
        build_tool_registry(tool_pack)


def test_resolve_coverage_window_reduces_named_columns_only():
    from engine.tools.coverage import resolve_coverage_window
    from tests.verifier_support import stats_row

    stats = [
        stats_row(
            "invoices",
            "received_at",
            min_value="2026-03-02T08:00:00",
            max_value="2026-05-29T08:00:00",
        ),
        stats_row(
            "scheduled_tasks",
            "completed_at",
            min_value="2026-03-02T06:00:00",
            max_value="2026-05-30T22:00:00",
        ),
        # Named nowhere: must not drag coverage back six years.
        stats_row(
            "contracts",
            "effective_from",
            min_value="2020-03-23T00:00:00",
            max_value="2025-11-01T00:00:00",
        ),
    ]
    window = resolve_coverage_window(
        stats, ["invoices.received_at", "scheduled_tasks.completed_at"]
    )
    assert window.start.isoformat() == "2026-03-02"
    assert window.end.isoformat() == "2026-05-30"
    assert resolve_coverage_window(stats, []) is None
