"""The architecture law, mechanically enforced: ports never import
adapters — and neither does any other core code. Only the composition
root (engine.runtime) may.

This walks every module under src/engine as source (ast, no imports
executed) so a violation is caught even in modules no test imports.
"""

import ast
from pathlib import Path

SRC_ENGINE = Path(__file__).parent.parent / "src" / "engine"

# The composition root: the ONLY modules allowed to import concrete
# adapters. Everything else — ports, config, cli, future core — sees
# port interfaces only. (engine/adapters itself is excluded because
# adapters importing their own package is not a boundary violation.)
COMPOSITION_ROOT = {"runtime"}


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_core_never_imports_adapters():
    violations = []
    for path in SRC_ENGINE.rglob("*.py"):
        relative = path.relative_to(SRC_ENGINE)
        top_level = relative.parts[0]
        if top_level in COMPOSITION_ROOT or top_level == "adapters":
            continue
        offending = {
            module
            for module in _imports_of(path)
            if module == "engine.adapters" or module.startswith("engine.adapters.")
        }
        if offending:
            violations.append(f"src/engine/{relative}: imports {sorted(offending)}")

    assert not violations, (
        "Core code imports concrete adapters (ports never import adapters "
        "— CLAUDE.md):\n" + "\n".join(violations)
    )


def test_ports_import_only_ports_and_stdlib():
    """Stricter still for the ports package: a port interface may import
    other ports modules, pydantic, and stdlib — never config, runtime,
    adapters, or anything else in the engine."""
    violations = []
    for path in (SRC_ENGINE / "ports").rglob("*.py"):
        offending = {
            module
            for module in _imports_of(path)
            if module.startswith("engine.") and not module.startswith("engine.ports")
        }
        if offending:
            relative = path.relative_to(SRC_ENGINE)
            violations.append(f"src/engine/{relative}: imports {sorted(offending)}")

    assert not violations, "Ports must depend on nothing concrete:\n" + "\n".join(
        violations
    )
