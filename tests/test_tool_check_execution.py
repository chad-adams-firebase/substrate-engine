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
    status = invocation.output.status
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
    assert invocation.output.status.ran is False


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
