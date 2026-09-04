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
from engine.tools.envelope import Anchor
from engine.tools.grounding import render_grounding

from tests.fixture_generation import build_snapshot_duckdb, generate_all

GOLDEN = Path(__file__).parent / "fixtures" / "grounding_prompt.txt"
# The same rendering for a question that names a canonical metric: the
# metric's template leads the prompt (fix pass 3).
GOLDEN_METRIC = Path(__file__).parent / "fixtures" / "grounding_prompt_metric.txt"
METRIC_QUESTION = "What is the flagged share of invoices?"
# The same rendering when a prior turn established a key (Backlog
# Pass): the keys section leads the tables. MT-KEY's anchor, as the
# pinned world names it.
GOLDEN_ANCHORED = Path(__file__).parent / "fixtures" / "grounding_prompt_anchored.txt"
ANCHORS = [
    Anchor(kind="invoice", column="invoices.invoice_number", value="INV-00426", source="cell"),
    Anchor(kind="supplier", column="suppliers.name", value="Meridian Gasket Company", source="cell"),
]
ANCHORS_TURN = 1
ARTIFACTS = Path(__file__).parent / "fixtures" / "pack_artifacts"


def render_snapshot_grounding(
    outputs: dict,
    question: str | None = None,
    *,
    anchors: list[Anchor] | None = None,
    anchors_turn: int = 0,
) -> str:
    return render_grounding(
        outputs["dictionary"],
        load_dictionary_map(ARTIFACTS / "dictionary_map.yaml"),
        outputs["univariate_stats"],
        dialect=RunSqlSettings().dialect,
        question=question,
        anchors=anchors,
        anchors_turn=anchors_turn,
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
    GOLDEN_METRIC.write_text(
        render_snapshot_grounding(outputs, METRIC_QUESTION),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {GOLDEN_METRIC} ({GOLDEN_METRIC.stat().st_size} bytes)")
    GOLDEN_ANCHORED.write_text(
        render_snapshot_grounding(outputs, anchors=ANCHORS, anchors_turn=ANCHORS_TURN),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {GOLDEN_ANCHORED} ({GOLDEN_ANCHORED.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
