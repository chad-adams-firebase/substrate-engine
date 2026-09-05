"""The wheels law as a test: the committed lock reaches only packages
with a cp312/win_amd64 wheel, and the committed report matches the
lock — so a dependency without a Windows wheel, or a stale report,
fails here on the Mac rather than at `uv sync` on the work machine."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "wheel_audit.py"


@pytest.fixture(scope="module")
def wheel_audit():
    spec = importlib.util.spec_from_file_location("wheel_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wheel_audit"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "filename, fits",
    [
        ("pkg-1.0-py3-none-any.whl", True),
        ("pkg-1.0-py2.py3-none-any.whl", True),
        ("pkg-1.0-cp312-cp312-win_amd64.whl", True),
        ("pkg-1.0-cp310-abi3-win_amd64.whl", True),
        ("pkg-1.0-py3-none-win_amd64.whl", True),
        ("pkg-1.0-cp312-cp312-macosx_11_0_arm64.whl", False),
        ("pkg-1.0-cp311-cp311-win_amd64.whl", False),
        ("pkg-1.0-cp312-cp312-win32.whl", False),
        ("pkg-1.0-cp313-abi3-win_amd64.whl", False),
        ("pkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl", False),
    ],
)
def test_wheel_tag_matching(wheel_audit, filename, fits):
    assert wheel_audit.wheel_satisfies(filename) is fits


def test_the_committed_lock_is_clean_and_the_dev_group_counts(wheel_audit):
    rows, dropped = wheel_audit.audit()
    failures = [row.name for row in rows if row.wheel is None]
    assert failures == [], f"no cp312/win_amd64 wheel: {failures}"
    names = {row.name for row in rows}
    assert {"duckdb", "openai", "flask", "pydantic-core", "pytest", "packaging"} <= names
    assert "engine" not in names  # the root builds from source, by design
    assert "httpx2-jsfetch" in dropped  # emscripten-only, never installed on Windows
    assert "httpx2-jsfetch" not in names


def test_the_windows_environment_is_complete(wheel_audit):
    """An unset marker key would take the Mac's value; every key the
    PEP 508 grammar knows is pinned to the work machine."""
    from packaging.markers import Marker

    env = wheel_audit.WINDOWS_ENV
    assert Marker("sys_platform == 'win32' and os_name == 'nt'").evaluate(env)
    assert not Marker("sys_platform == 'darwin'").evaluate(env)
    assert Marker("python_version >= '3.12' and implementation_name == 'cpython'").evaluate(env)


def test_the_committed_report_matches_the_lock(wheel_audit):
    rows, dropped = wheel_audit.audit()
    expected = wheel_audit.render(rows, dropped)
    committed = wheel_audit.REPORT.read_text(encoding="utf-8")
    assert committed == expected, "docs/wheel-audit.md is stale: uv run python scripts/wheel_audit.py --write"
