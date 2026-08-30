"""The tool-invocation envelope — Phase 4's raw material, fixed now.

Every tool invocation returns a ToolInvocation. The whole serialized
invocation is the evidence-bundle unit (Brief §6/§9): the Verifier
walks it for claim matching, and turn_log.evidence_bundle_ref will
point at serialized TurnEvidence. The split inside it follows one
rule:

  output   = everything the drafting LLM may see (compact, typed,
             result rows included — data-shaped answers become table
             envelopes directly, §9.4);
  evidence = process residue the LLM must not or need not see (failed
             SQL attempts, prompts, raw responses, matched log lines,
             full doc sections behind snippets). None means the output
             already IS the complete raw record.

Both are discriminated unions so a persisted bundle deserializes with
no external context. No timestamps here: turn-level timing belongs to
the Phase 4 harness (status events, §10.2), and a timestamp-free
envelope stays byte-comparable in fixtures.

Errors are first-class: tools never raise for domain failures — a bad
input, an exhausted repair loop, an unknown component all come back as
status="error" envelopes (evidence intact) that a harness can feed
back to the LLM or fail closed on.
"""

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from engine.config.models import SubstrateName, ToolName
from engine.ports.types import RunStatus, UnitSummary
from engine.substrates.models import (
    CanonicalMetric,
    CkgConditional,
    CkgEdge,
    CkgNode,
    Component,
    Concept,
    DictionaryRow,
    Gotcha,
    JoinPath,
    StatsRow,
)

JsonValue = str | int | float | bool | None


class Table(BaseModel):
    """Rows as data, not prose (§9.4) — the §10.5 table-envelope
    precursor and the canonical home of run_sql's result set (the
    Verifier's §9.3 run_sql check reads exactly here)."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[dict[str, JsonValue]]
    # The pre-truncation count: "how many rows did the query return"
    # must stay answerable after truncation.
    total_row_count: int
    truncated: bool = False


# --- Per-tool outputs (what the drafting LLM may see) -----------------


class StatsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["query_univariate_stats"] = "query_univariate_stats"
    rows: list[StatsRow]


class DictionaryLookupOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["lookup_data_dictionary"] = "lookup_data_dictionary"
    rows: list[DictionaryRow]
    concepts: list[Concept] = []
    metrics: list[CanonicalMetric] = []
    join_paths: list[JoinPath] = []
    gotchas: list[Gotcha] = []


class CkgTraversalOutput(BaseModel):
    """One hop's results. Edge-shaped hops (callers/callees/reads/
    writes) fill edges AND the resolved counterpart nodes in the same
    order — call order is line order, and the caller needs names, not
    hashes."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["traverse_code_knowledge_graph"] = "traverse_code_knowledge_graph"
    entry_node: CkgNode | None = None
    entry_component: Component | None = None
    nodes: list[CkgNode] = []
    edges: list[CkgEdge] = []
    conditionals: list[CkgConditional] = []


class ReadSourceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["read_source"] = "read_source"
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    commit_sha: str
    text: str


class RunSqlOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["run_sql"] = "run_sql"
    sql: str
    table: Table


class PrimerOutput(BaseModel):
    """L0 + L1 only — never the full CKG (Brief §6)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["app_primer"] = "app_primer"
    primer: str
    components: list[Component]


class DocSearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    heading: str
    snippet: str
    score: int


class DocSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["search_business_docs"] = "search_business_docs"
    hits: list[DocSearchHit]


class CheckExecutionOutput(BaseModel):
    """did_run fills run_status; recent_errors fills errors (name-keyed
    parsed log fields) and error_count. The unused fields stay None.
    The key is named run_status, not status: the invocation-level
    envelope already has a status, and two keys spelled the same at
    different depths invited drafters to write the wrong placeholder
    path. error_count is recent_errors' scalar mirror of
    run_status.count — the pre-truncation total, so a clean day is a
    placeholder-sayable 0 and a capped list still reports what
    happened, not what was shown."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["check_execution"] = "check_execution"
    run_status: RunStatus | None = None
    errors: list[dict[str, JsonValue]] | None = None
    error_count: int | None = None


class KnownItemsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["answer_from_known_items"] = "answer_from_known_items"
    matches: list[UnitSummary]


ToolOutput = Annotated[
    StatsOutput
    | DictionaryLookupOutput
    | CkgTraversalOutput
    | ReadSourceOutput
    | RunSqlOutput
    | PrimerOutput
    | DocSearchOutput
    | CheckExecutionOutput
    | KnownItemsOutput,
    Field(discriminator="kind"),
]


# --- Per-tool evidence (process residue behind the output) ------------


class SqlAttempt(BaseModel):
    """One turn of the execute–check–repair loop, kept even when the
    loop fails: the Verifier and the debugging human both need to see
    what was tried, not just what won."""

    model_config = ConfigDict(extra="forbid")

    raw_response: str
    sql: str | None = None
    error: str | None = None
    row_count: int | None = None


class RunSqlEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["run_sql"] = "run_sql"
    grounding_prompt: str
    attempts: list[SqlAttempt]


class DocSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    heading: str
    text: str


class DocSearchEvidence(BaseModel):
    """Full text of the matched sections — the snippet in the output
    is for reading, this is for §9.2 quote matching."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["search_business_docs"] = "search_business_docs"
    sections: list[DocSection]


class CheckExecutionEvidence(BaseModel):
    """The matched raw log lines (capped by adapter settings) — the
    verbatim material behind the counted answer."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["check_execution"] = "check_execution"
    lines: list[str]
    truncated: bool = False


ToolEvidence = Annotated[
    RunSqlEvidence | DocSearchEvidence | CheckExecutionEvidence,
    Field(discriminator="kind"),
]


# --- The envelope -----------------------------------------------------


class ToolInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: ToolName
    # Dump of the validated input model (JSON mode) — or the raw
    # arguments as received, when validation itself failed.
    arguments: dict[str, Any]
    status: Literal["ok", "error"]
    error: str | None = None
    output: ToolOutput | None = None
    evidence: ToolEvidence | None = None
    substrates_read: list[SubstrateName] = []
    # Distinct provenance.manifest_id values of the rows consulted —
    # consumers: turn provenance (§8) and staleness flagging (§11).
    manifest_ids: list[str] = []


TurnEvidence = list[ToolInvocation]

_TURN_EVIDENCE = TypeAdapter(TurnEvidence)


def dumps_turn_evidence(invocations: TurnEvidence) -> str:
    """Canonical JSON for a turn's evidence bundle — same discipline
    as jsonl.dumps_row (sorted keys, compact, ASCII) so the bytes
    behind an evidence_bundle_ref are stable."""
    payload = _TURN_EVIDENCE.dump_python(invocations, mode="json")
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def loads_turn_evidence(text: str) -> TurnEvidence:
    return _TURN_EVIDENCE.validate_json(text)
