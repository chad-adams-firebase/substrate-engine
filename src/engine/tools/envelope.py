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
    Interpretation,
    JoinPath,
    StatsRow,
)

JsonValue = str | int | float | bool | None


DurationUnit = Literal["seconds", "minutes", "hours", "days"]
RateScale = Literal["fraction", "percent"]


class ColumnFormat(BaseModel):
    """A per-column display hint that travels with the table, store to
    screen (§10.5): every renderer — CLI text, eval flattening,
    placeholder injection, the browser — applies the same rule, so a
    money cell never reaches a human as a float tail, a duration never
    as 1.0806402437502474 days, and a rate never as 0.9221105527638191.
    The symbol, unit and scale ride along because they are pack config
    (locale/branding, the SQL author's unit, the alias's scale), and a
    persisted table must render identically without the pack in hand.
    A duration column's numeric cells are measured in `unit`; its
    H:MM:SS string cells carry their own unit, so a clock-string column
    may leave unit unset. A rate column's cells are fractions in [0, 1]
    or already percents in [0, 100] — `scale` says which, and the
    Verifier's rate bounds read the same hint (the coverage pass: two
    resolvers cannot disagree on one column)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["money", "duration", "rate"]
    symbol: str = ""  # money
    unit: DurationUnit | None = None  # duration: what a number counts
    scale: RateScale | None = None  # rate: fraction (x100 to show) or percent


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
    # Keyed by column name (name-based access, never positional);
    # absent = render as-is. Resolved by the producing tool from the
    # Dictionary Map's column_formats and the pack's display config
    # (money from both; durations from the pack's alias patterns).
    column_formats: dict[str, ColumnFormat] = {}


# --- What a turn establishes, and what a tool may know of the
# --- conversation it runs in (Backlog Pass) ----------------------------


class Anchor(BaseModel):
    """One column's word for the entity a turn established — "that
    invoice" resolved: the kind, the column (canonical "table.column";
    "" for a declared about that names no column), the value, and where
    it came from — a single-valued result column, a filter literal, or
    the router's own declaration. A turn's anchors of one kind all
    describe one entity."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    column: str
    value: str
    source: Literal["cell", "filter", "declared"]


class KnownKey(BaseModel):
    """A key value the conversation has seen: a cell of an id-like
    result column, or a literal a statement filtered an id-like column
    on. What the ungrounded-key lint grounds against."""

    model_config = ConfigDict(extra="forbid")

    column: str  # canonical "table.column"
    value: str


class TurnAnchors(BaseModel):
    """What one finished turn's evidence established, kept on the
    checkpoint's history: the determinate entities (for the router's
    transcript and the Verifier's anchor check) and every key value
    (for the key lint). turn is explicit so a consumer can say
    "turn 6 established"."""

    model_config = ConfigDict(extra="forbid")

    turn: int = 0
    entities: list[Anchor] = []
    keys: list[KnownKey] = []
    # A turn whose verdict carried the anchor check's warn (Fix Pass)
    # established nothing: entities is empty and the prior anchor
    # survives on the history. The kind it drifted on and the
    # contradiction the warn printed are kept, so the transcript names
    # the correction and a following kind-less "it" is read against
    # the surviving anchor until an unwarned answer establishes a new
    # entity. Defaults, so a legacy checkpoint loads with neither.
    contradicted_kind: str = ""
    contradiction: str = ""


class TurnContext(BaseModel):
    """What a tool may know of the conversation it runs in — handed by
    the harness, never checkpointed: the user's own words (history
    questions, the current question, the running summary — never a
    tool argument, which is the router's paraphrase), the most recent
    turn's determinate entities, and every key the conversation has
    seen. run_sql grounds on it; no other tool reads it."""

    model_config = ConfigDict(extra="forbid")

    texts: list[str] = []
    anchors: list[Anchor] = []
    anchors_turn: int = 0
    known_keys: list[KnownKey] = []


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
    # The readings the canonical metrics the question named declare,
    # names and meanings, in declaration order (Close Pass): the router
    # names one on give_answer(shape='table') and the answer carries it,
    # so a table answer can say which reading its SQL computed. Empty
    # when the question named no metric with interpretations.
    readings: list[Interpretation] = []


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


class CapabilitiesOutput(BaseModel):
    """Pack-configured self-description (ui.capabilities +
    starter_prompts) — the whole evidence for a meta question about
    the assistant itself. Config, not a substrate: substrates_read
    stays empty."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["app_capabilities"] = "app_capabilities"
    capabilities: str
    starter_prompts: list[str]


ToolOutput = Annotated[
    StatsOutput
    | DictionaryLookupOutput
    | CkgTraversalOutput
    | ReadSourceOutput
    | RunSqlOutput
    | PrimerOutput
    | DocSearchOutput
    | CheckExecutionOutput
    | KnownItemsOutput
    | CapabilitiesOutput,
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
    # The fan-out challenge, typed so the audit trail distinguishes
    # "never fired" / "fired and repaired" / "fired and overridden":
    # set alongside error on the blocking round, and set on an
    # EXECUTED attempt when the re-lint still trips (the play pass's
    # W1: the licensed resend kept its fanned SUMs). The Verifier
    # reads the final attempt's value; a set lint there caps the
    # answer at unverified.
    lint: str | None = None
    # The enum-literal challenge (coverage pass, R-A), recorded the same
    # way: beside error on the blocking round, and on an EXECUTED
    # attempt when the licensed resend still filters on a value the
    # column never holds — the Verifier's run_sql.enum_literal_override.
    enum_lint: str | None = None
    # The interval-arithmetic challenge (duration pass, W3 rep 4),
    # recorded the same way: beside error on the blocking round, and on
    # an EXECUTED attempt when the licensed resend still scales an
    # interval by a literal — the Verifier's
    # run_sql.interval_arithmetic_override.
    interval_lint: str | None = None
    # The placeholder challenge (Backlog Pass, turn 20's `invoice_id =
    # 123 -- Replace 123 with the actual invoice ID`), recorded beside
    # error on every round it blocks. Never on an executed attempt: it
    # is hard — a resend that keeps the confession is blocked again.
    placeholder_lint: str | None = None
    # The ungrounded-key challenge (Backlog Pass, the same turn 20:
    # 123 appeared in no result, question, or grounding), recorded the
    # same way as the other repairable lints: beside error on the
    # blocking round, and on an EXECUTED attempt when the licensed
    # resend still binds the key — the Verifier's
    # run_sql.ungrounded_key_override.
    key_lint: str | None = None


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

    def rendered_output(self) -> dict[str, Any]:
        """The output exactly as the drafter sees it: a JSON-mode dump
        with None-valued fields suppressed (a mode's unused half reads
        as emptiness and lures disclaimers of present fields). The
        verifier harvests the envelope's field names from this same
        view, so a name the drafter never saw cannot ground a claim."""
        assert self.output is not None, "rendered_output needs an output"
        return self.output.model_dump(mode="json", exclude_none=True)


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
