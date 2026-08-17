"""One shared definition of the golden grounding prompt.

The run_sql grounding rendering is pinned the same way generator
output is: a checked-in expected file, regenerated deliberately after
an intentional rendering change:

    uv run python -m tests.golden_grounding --write
"""

import argparse
import tempfile
from pathlib import Path

from engine.config.models import RunSqlSettings
from engine.substrates.pack_data import load_dictionary_map
from engine.tools.grounding import render_grounding

from tests.fixture_generation import build_snapshot_duckdb, generate_all

GOLDEN = Path(__file__).parent / "fixtures" / "grounding_prompt.txt"
ARTIFACTS = Path(__file__).parent / "fixtures" / "pack_artifacts"


def render_snapshot_grounding(outputs: dict) -> str:
    return render_grounding(
        outputs["dictionary"],
        load_dictionary_map(ARTIFACTS / "dictionary_map.yaml"),
        outputs["univariate_stats"],
        dialect=RunSqlSettings().dialect,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", required=True, help="Rewrite the golden file."
    )
    parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        outputs = generate_all(build_snapshot_duckdb(Path(scratch)))
    GOLDEN.write_text(
        render_snapshot_grounding(outputs), encoding="utf-8", newline="\n"
    )
    print(f"wrote {GOLDEN} ({GOLDEN.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
