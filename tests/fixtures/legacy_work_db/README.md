# A pre-Block-3 work store, committed

`work.db` was written by the engine at commit `850f869` ("Eval:
post-guard-pass full bank", 2026-09-02), the last commit before the
turn log gained its `question` and `outcome` columns (Phase 5 Block 3)
and before the checkpoint history became `HistoryTurn` records (Phase
5 Block 4). It is the shape every work store written before those
changes has — the eval report stores on the work machine's `report/`
branches included — and `tests/test_harness_legacy_store.py` proves
such a store keeps loading: a conversation from it continues, its
history upgrades to records on read, `ensure_schema` adds the two
columns in place, and `engine store backfill-questions` recovers its
questions.

What it holds: workspace 1 (`tester`, `scratch`); conversation 1,
"How many invoice rows are there?"; two `turn_log` rows (turn 1 a
prose answer "Invoices has 50 rows." over a stats call, turn 2 a table
answer over the same call); the LangGraph `checkpoints`/`writes`
tables for thread `"1"`, whose `history` channel is four `Message`
objects — `(user, assistant)` pairs. The `turn_log` DDL at that commit,
12 columns:

```sql
CREATE TABLE IF NOT EXISTS turn_log (
    id                   INTEGER PRIMARY KEY,
    conversation_id      INTEGER NOT NULL REFERENCES conversation(id),
    turn                 INTEGER NOT NULL,
    actor                TEXT NOT NULL,
    action               TEXT NOT NULL,
    tools_used           TEXT,  -- JSON array
    substrates_read      TEXT,  -- JSON array
    evidence_bundle_ref  TEXT,  -- key into evidence_bundle
    verifier_verdict     TEXT,  -- VerifierVerdict JSON, opaque here
    substrate_versions   TEXT,  -- JSON
    status_events        TEXT,
    created_at           TEXT NOT NULL
);
```

How it was produced (once; never regenerate it from a newer commit —
the point is the old layout): a worktree at `850f869`, `uv sync`
there, this throwaway pytest module dropped into its `tests/` and run
with `pytest tests/test_make_legacy_fixture.py -s`, then `VACUUM` and
copy here. `.gitignore` un-ignores this one `.db`; `.gitattributes`
marks `*.db` binary.

```python
"""Throwaway (never committed): writes a pre-Block-3 work.db — 12-column
turn_log, Message-pair checkpoint history — from a scripted two-turn
conversation at this commit, for the Block 4 legacy-store fixture."""

import shutil
from pathlib import Path

import yaml

from engine.config.models import PortName
from engine.ports.types import LLMResponse
from tests.harness_support import build_ask_session, tool_call

DEST = Path(__file__).resolve().parents[2] / "legacy_work.db"

STATS_CALL = tool_call("query_univariate_stats", {"table": "invoices", "column": "status"})


def test_make_legacy_fixture(tool_pack, tmp_path):
    config_path = tool_pack / "config.yaml"
    config = yaml.safe_load(config_path.read_text())
    db = tmp_path / "work.db"
    config["adapters"]["work_store"]["settings"]["database"] = str(db)
    config_path.write_text(yaml.safe_dump(config))
    responses = [
        STATS_CALL,
        tool_call("give_answer", {"shape": "prose"}),
        LLMResponse(content="Invoices has {{e0.rows[0].row_count}} rows.", model="s"),
        STATS_CALL,
        tool_call("give_answer", {"shape": "table", "evidence_index": 0}),
    ]
    session, ports, _ = build_ask_session(tool_pack, responses)
    first = session.ask("How many invoice rows are there?")
    assert first.outcome.body.text == "Invoices has 50 rows."
    second = session.ask(
        "Show me the status distribution as a table.",
        conversation_id=first.conversation_id,
    )
    assert second.outcome.body.kind == "table" and second.turn == 2
    ports.get(PortName.WORK_STORE)._connection.close()
    session._graph.checkpointer.conn.close()
    shutil.copy(db, DEST)
```
