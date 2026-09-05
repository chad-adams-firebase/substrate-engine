"""The wheels law (CLAUDE.md), made checkable: every package the work
machine installs must ship a prebuilt wheel for CPython 3.12 on
Windows x86-64 — its uv goes through an Artifactory proxy that serves
wheels only, and nothing compiles there.

Offline by design: uv.lock already records every wheel the index
offered for each locked version, so the audit reads the lock, walks
the dependency closure the way uv would on that machine (markers
evaluated under a Windows / CPython 3.12 environment, dev group
included because the suite runs there), and checks each reachable
package for a wheel whose tags fit cp312 / win_amd64 — py3-none-any,
cp312-cp312-win_amd64, cp3N-abi3-win_amd64, py3-none-win_amd64.

    uv run python scripts/wheel_audit.py            # print the report
    uv run python scripts/wheel_audit.py --write    # refresh docs/wheel-audit.md
    uv run python scripts/wheel_audit.py --check    # exit 1 on a failure or a stale report

The committed report is the record; tests/test_wheel_audit.py keeps
it current and keeps the lock clean, so a new dependency without a
Windows wheel fails the suite on the Mac, not `uv sync` at work.
Outside the lock and therefore outside this check: the root package
(built from source by hatchling, a pure-Python backend the proxy
serves as a wheel) and uv itself.
"""

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from packaging.markers import Marker
from packaging.tags import Tag, compatible_tags, cpython_tags
from packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "uv.lock"
REPORT = ROOT / "docs" / "wheel-audit.md"

PYTHON = (3, 12)
PLATFORM = "win_amd64"

# The work machine, as a PEP 508 marker environment. Every key is set:
# an unset key would silently take the Mac interpreter's value.
WINDOWS_ENV = {
    "os_name": "nt",
    "sys_platform": "win32",
    "platform_system": "Windows",
    "platform_machine": "AMD64",
    "platform_release": "10",
    "platform_version": "",
    "platform_python_implementation": "CPython",
    "implementation_name": "cpython",
    "implementation_version": "3.12.0",
    "python_version": "3.12",
    "python_full_version": "3.12.0",
    "extra": "",
}


def accepted_tags() -> frozenset[Tag]:
    """Every wheel tag pip/uv would accept on CPython 3.12 / win_amd64."""
    return frozenset(
        list(cpython_tags(python_version=PYTHON, abis=["cp312"], platforms=[PLATFORM]))
        + list(compatible_tags(python_version=PYTHON, interpreter="cp312", platforms=[PLATFORM]))
    )


def wheel_satisfies(filename: str, tags: frozenset[Tag] | None = None) -> bool:
    _name, _version, _build, wheel_tags = parse_wheel_filename(filename)
    return bool(wheel_tags & (tags or accepted_tags()))


def load_lock(path: Path = LOCK) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _marker_true(marker: str | None, extra: str = "") -> bool:
    if not marker:
        return True
    return Marker(marker).evaluate({**WINDOWS_ENV, "extra": extra})


def _entry_for(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """A package forked by resolution markers has several entries; the
    one whose markers hold on Windows is the one uv installs there."""
    if len(entries) == 1:
        return entries[0]
    for entry in entries:
        markers = entry.get("resolution-markers") or []
        if any(_marker_true(marker) for marker in markers):
            return entry
    raise SystemExit(f"no entry of {entries[0]['name']} resolves on Windows")


def reachable(lock: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """(reachable packages by name, packages dropped by a marker)."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    root = None
    for entry in lock["package"]:
        by_name.setdefault(entry["name"], []).append(entry)
        if entry.get("source", {}).get("editable") == "." or entry.get("source", {}).get("virtual") == ".":
            root = entry
    if root is None:
        raise SystemExit("uv.lock has no editable/virtual root package")

    reached: dict[str, dict[str, Any]] = {}
    dropped: set[str] = set()

    def visit(edges: list[dict[str, Any]]) -> None:
        for edge in edges:
            name = edge["name"]
            if not _marker_true(edge.get("marker")):
                if name not in reached:
                    dropped.add(name)
                continue
            entry = _entry_for(by_name[name])
            first_visit = name not in reached
            reached[name] = entry
            dropped.discard(name)
            if first_visit:
                visit(entry.get("dependencies", []))
            optional = entry.get("optional-dependencies", {})
            for extra in edge.get("extra", []):
                visit(optional.get(extra, []))

    visit(root.get("dependencies", []))
    for group in root.get("dev-dependencies", {}).values():
        visit(group)
    return reached, sorted(dropped - set(reached))


class Row:
    def __init__(self, name: str, version: str, wheel: str | None) -> None:
        self.name = name
        self.version = version
        self.wheel = wheel  # the satisfying wheel's filename, or None


def audit(lock_path: Path = LOCK) -> tuple[list[Row], list[str]]:
    lock = load_lock(lock_path)
    reached, dropped = reachable(lock)
    tags = accepted_tags()
    rows = []
    for name in sorted(reached):
        entry = reached[name]
        wheels = [w["url"].rsplit("/", 1)[-1] for w in entry.get("wheels", [])]
        satisfying = next((w for w in wheels if wheel_satisfies(w, tags)), None)
        rows.append(Row(name, entry["version"], satisfying))
    return rows, dropped


def render(rows: list[Row], dropped: list[str]) -> str:
    failures = [row for row in rows if row.wheel is None]
    lines = [
        "# Wheel audit — cp312 / win_amd64",
        "",
        "Generated by `scripts/wheel_audit.py` from `uv.lock`; the test suite",
        "keeps it current. The law (CLAUDE.md): every dependency ships a",
        "prebuilt wheel for CPython 3.12 on Windows x86-64, because the work",
        "machine's package proxy serves wheels only and nothing compiles there.",
        "",
        f"Result: **{'PASS' if not failures else 'FAIL'}** — "
        f"{len(rows) - len(failures)} of {len(rows)} reachable packages ship a "
        f"compatible wheel.",
        "",
        "| package | version | wheel that satisfies |",
        "|---|---|---|",
    ]
    for row in rows:
        lines.append(f"| {row.name} | {row.version} | {row.wheel or '**NONE — justify or replace**'} |")
    lines.append("")
    if dropped:
        lines.append("Not installed on Windows (excluded by a platform marker): " + ", ".join(dropped) + ".")
        lines.append("")
    lines.append("Outside the lock, and so outside this check:")
    lines.append("")
    lines.append("- `engine` itself is built from source by `hatchling`, a pure-Python build backend the proxy serves as a `py3-none-any` wheel (with `pathspec`, `pluggy`, `packaging`, `trove-classifiers`, all pure).")
    lines.append("- `uv` is installed on the work machine by the enterprise; Phase 1 ran green there through it.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help=f"Write {REPORT.relative_to(ROOT)}.")
    parser.add_argument("--check", action="store_true", help="Exit 1 on a failure or a stale report.")
    args = parser.parse_args(argv)

    rows, dropped = audit()
    text = render(rows, dropped)
    failures = [row.name for row in rows if row.wheel is None]
    if args.write:
        REPORT.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {REPORT.relative_to(ROOT)}")
    else:
        print(text, end="")
    if args.check:
        stale = not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != text
        if failures:
            print(f"FAIL: no cp312/win_amd64 wheel for {failures}", file=sys.stderr)
        if stale:
            print("STALE: docs/wheel-audit.md differs — run with --write", file=sys.stderr)
        return 1 if failures or stale else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
