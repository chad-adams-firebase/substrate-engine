"""The §4 substrate schemas — the load-bearing contracts of Phase 2.

Every row embeds a Provenance (a field, not a base class, so each
row's natural-key fields stay flat and first in sorted-key JSON).
Machine rows must name the manifest that produced them; human rows
must not — a human's word is not an extraction artifact.

Keep these minimal: a field with no consumer does not exist
(CLAUDE.md). Where a field's consumer is not obvious it is named in
a comment.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# Decimal places every float in a StatsRow is rounded to at generation
# time (null_rate, mean, rendered min/max). One constant, read by the
# stats generator when it rounds and by the Verifier's SUM cap when it
# reads the row back at the same precision (Polish Pass): a cap
# computed from a rounded null_rate sat $7 under the true total of
# invoices.invoice_total and took the badge off a correct answer.
STATS_DECIMALS = 6


class Provenance(BaseModel):
    """Who vouches for a row, and how strongly (Brief §4)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["machine", "human"]
    confidence: float
    last_confirmed_by: str | None = None
    last_confirmed_date: date | None = None
    needs_validation: bool
    # Machine rows link to the manifest of the run that produced them
    # (consumers: staleness flagging in Phase 6, conformance validator).
    manifest_id: str | None = None

    @model_validator(mode="after")
    def _machine_rows_carry_manifest(self) -> "Provenance":
        if self.source == "machine" and self.manifest_id is None:
            raise ValueError(
                "machine-sourced rows must carry a manifest_id "
                "(Brief §5: every machine row links to its extraction manifest)"
            )
        if self.source == "human" and self.manifest_id is not None:
            raise ValueError(
                "human-sourced rows must not carry a manifest_id — "
                "human knowledge is not an extraction artifact"
            )
        return self


class Manifest(BaseModel):
    """One generator run's pinning record (Brief §13).

    manifest_id is content-addressed over every field EXCEPT
    extracted_at (see engine.substrates.manifest). That exclusion is
    the idempotency mechanism: rerunning against the same commit SHA
    and seed yields the same manifest_id, hence byte-identical
    substrate rows; only this file's timestamp line differs.
    """

    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    generator: Literal[
        "dictionary", "stats", "ckg", "sqlite_convert", "databricks_pull"
    ]
    generator_version: str
    source_commit_sha: str | None = None
    simulation_seed: int | None = None
    # DB-derived substrates: the tables actually read.
    source_tables: list[str] = []
    # A pulled world's pin: which warehouse schema, and each table's
    # Delta version, the pull read ("cat.sch|invoices@17,suppliers@3").
    # Hashed into manifest_id only when set, so every manifest that
    # predates it keeps its id (consumer: engine pull; the eval
    # preflight compares world manifest ids).
    source_snapshot: str | None = None
    extracted_at: datetime


class DictionaryRow(BaseModel):
    """One table or column of the target database (Brief §4.1).

    A row with column_name == "" describes the table itself; the
    natural key is (table_name, column_name). description is THE SME
    field: machine rows leave it empty, the human overlay fills it.
    """

    model_config = ConfigDict(extra="forbid")

    table_name: str
    column_name: str = ""
    data_type: str = ""
    nullable: bool | None = None
    is_primary_key: bool = False
    # "other_table.column" — consumer: NL->SQL join grounding (Phase 3).
    fk_target: str | None = None
    enum_values: list[str] | None = None
    # How the enum was detected — consumer: SME triage; data_scan
    # candidates are heuristic and always need validation.
    enum_source: Literal["check_constraint", "data_scan"] | None = None
    description: str = ""
    provenance: Provenance


class TopValue(BaseModel):
    """One frequent value of a column, rendered as text uniformly."""

    model_config = ConfigDict(extra="forbid")

    value: str
    count: int


class StatsRow(BaseModel):
    """Univariate statistics for one column (Brief §4.9).

    Consumers: query_univariate_stats tool (Phase 3), Verifier
    plausibility checks (Phase 4), NL->SQL grounding. Floats are
    rounded at generation time, never at serialization, so the same
    data always produces the same bytes.
    """

    model_config = ConfigDict(extra="forbid")

    table_name: str
    column_name: str
    data_type: str
    row_count: int
    null_rate: float
    distinct_count: int
    # min/max rendered as text so timestamps and strings travel the
    # same way numeric values do.
    min_value: str | None = None
    max_value: str | None = None
    mean: float | None = None
    top_values: list[TopValue] = []
    provenance: Provenance


class CkgNode(BaseModel):
    """One L2 code-knowledge-graph node (Brief §5).

    id is content-addressed — hash of qualified name + kind — never
    positional or generation-ordered, so incremental refresh stays a
    later feature, not a rewrite. Line numbers are 1-based inclusive,
    matching SourceCodePort.read.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["module", "class", "function", "method", "constant"]
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str | None = None
    # Constants only: the unparse of the assigned expression. Captures
    # thresholds (RATE_VARIANCE_PCT = 0.15) and raw-SQL text() blocks.
    value: str | None = None
    provenance: Provenance


class CkgEdge(BaseModel):
    """One L2 edge (Brief §5).

    calls/imports/contains point at another node; reads_table/
    writes_table point at a database table by name (joining to the
    data dictionary by name, per Brief §4's cross-substrate joins).
    Exactly one of target_node_id / target_table is set.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    kind: Literal["calls", "imports", "contains", "reads_table", "writes_table"]
    target_node_id: str | None = None
    target_table: str | None = None
    line: int
    provenance: Provenance

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "CkgEdge":
        table_kinds = {"reads_table", "writes_table"}
        if self.kind in table_kinds:
            if self.target_table is None or self.target_node_id is not None:
                raise ValueError(
                    f"{self.kind} edges target a table by name, not a node"
                )
        else:
            if self.target_node_id is None or self.target_table is not None:
                raise ValueError(
                    f"{self.kind} edges target a node id, not a table"
                )
        return self


class CkgConditional(BaseModel):
    """One branch condition inside a function (Brief §5).

    This is what makes "do we always flag X over $Y?" answerable —
    the threshold appears in condition_text. Natural key is
    (node_id, line); no id field because no consumer references a
    conditional from elsewhere.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    condition_text: str
    line: int
    provenance: Provenance


class Component(BaseModel):
    """One L1 component (Brief §5) — stable, human-meaningful id."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    tier: int = 1
    provenance: Provenance


class ComponentMembership(BaseModel):
    """One node's assignment to a component (Brief §5 L1).

    Machine proposals land needs_validation=true; human confirmations
    arrive via the overlay and are never overwritten. The qualified
    name rides along because humans author and review by name — the
    id is a hash only machines enjoy reading.
    """

    model_config = ConfigDict(extra="forbid")

    component_id: str
    ckg_node_id: str
    node_qualified_name: str
    provenance: Provenance


class DocProvenance(BaseModel):
    """Document-level provenance for authored (not generated) artifacts
    — the primer's front-matter pattern, as a model.

    The Dictionary Map and business docs are authored files, so the
    per-row Provenance contract does not fit: its validator demands a
    manifest_id for machine rows, and no generator run produced these.
    A document vouched for as a whole gets one provenance block.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["machine", "human"]
    confidence: float
    needs_validation: bool
    note: str = ""


class Interpretation(BaseModel):
    """One reading a manager term can carry — 'recovered' as
    feedback-authored findings vs closed-invoice opportunity, 15×
    apart in the play session (W8). Consumers: run_sql grounding
    (rendered under the declaring entry) and the drafter rule that
    makes an answer name the reading it used."""

    model_config = ConfigDict(extra="forbid")

    name: str
    meaning: str


class Concept(BaseModel):
    """One business concept, mapped to where it lives in the schema
    (Brief §4.2). Joins to the dictionary by table/column names."""

    model_config = ConfigDict(extra="forbid")

    name: str
    definition: str
    tables: list[str] = []
    synonyms: list[str] = []
    interpretations: list[Interpretation] = []
    # Per-entry override of the document provenance block: the map is
    # a living artifact, and entries hand-authored through use are
    # source=human while the original machine draft stays machine.
    # None inherits the document block.
    provenance: DocProvenance | None = None


class CanonicalMetric(BaseModel):
    """One canonical metric: the exact SQL ingredients, so run_sql
    grounds on a vetted definition instead of improvising one."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    tables: list[str]
    # SQL fragments, not full statements: the WHERE condition that
    # scopes the metric and the aggregate expression that computes it.
    filter_sql: str = ""
    aggregation_sql: str
    notes: str = ""
    # Business phrasings that name this metric. Consumers: run_sql's
    # grounding, which renders a matched metric as the statement
    # template at the top of the prompt (fix pass 3: retrieval beats
    # exhortation), and the router's data vocabulary.
    synonyms: list[str] = []
    # The full canonical statement, when the fragments alone leave the
    # join shape to the model (U5: the LEFT JOIN that must not become
    # an INNER JOIN). Rendered verbatim as the template.
    template_sql: str = ""
    interpretations: list[Interpretation] = []
    provenance: DocProvenance | None = None  # None inherits the document block


class JoinStep(BaseModel):
    """One hop of a canonical join path."""

    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_column: str
    to_table: str
    to_column: str


class CardinalityCondition(BaseModel):
    """A filter under which a join path is one row per key — a
    lifecycle fact (an invoice reaches a terminal status once, is
    received once) that no schema constraint carries and no lint can
    infer from SQL, so the pack declares it (Close Pass). Consumers:
    run_sql's fan-out lint, which treats a step as one_to_one in a
    scope whose WHERE, or the step's own ON, restricts the column to
    the declared values; and the eval's --check-gold, which executes
    every declared condition against the world so a declaration never
    quietly becomes a data coincidence."""

    model_config = ConfigDict(extra="forbid")

    column: str  # "table.column", on one of the path's step tables
    values: list[str]

    @model_validator(mode="after")
    def _shape(self) -> "CardinalityCondition":
        table, dot, column = self.column.partition(".")
        if not (table and dot and column) or "." in column:
            raise ValueError(
                f"column must be written table.column, got {self.column!r}"
            )
        if not self.values:
            raise ValueError("values must name at least one value")
        return self

    @property
    def table(self) -> str:
        return self.column.partition(".")[0]

    @property
    def column_name(self) -> str:
        return self.column.partition(".")[2]


class JoinPath(BaseModel):
    """A vetted way to join tables — the routes that are correct, as
    opposed to the ones that merely typecheck."""

    model_config = ConfigDict(extra="forbid")

    name: str
    steps: list[JoinStep]
    notes: str = ""
    # Declared row multiplicity — consumer: run_sql's fan-out lint,
    # which exempts a one_to_one path from the COUNT/SUM-over-join
    # challenge. Absent means unknown, and unknown is challenged.
    cardinality: Literal["one_to_one"] | None = None
    # One row per key under any of these filters (any-of) — the
    # conditional form of cardinality, for a path that fans in general
    # and not under a lifecycle filter. Exclusive with cardinality.
    one_to_one_when: list[CardinalityCondition] = []

    @model_validator(mode="after")
    def _one_declaration(self) -> "JoinPath":
        if self.cardinality is not None and self.one_to_one_when:
            raise ValueError(
                f"join path {self.name!r} declares both cardinality and "
                "one_to_one_when; a path is one-to-one always or under a filter"
            )
        return self


class Gotcha(BaseModel):
    """A place where the obvious query is wrong. Rendered verbatim
    into run_sql grounding — this is the artifact's reason to exist."""

    model_config = ConfigDict(extra="forbid")

    name: str
    summary: str
    detail: str
    tables: list[str] = []
    provenance: DocProvenance | None = None  # None inherits the document block


class WhereToLookExample(BaseModel):
    """A question paired with where its answer lives — routing
    examples for grounding, not answers."""

    model_config = ConfigDict(extra="forbid")

    question: str
    guidance: str


class ColumnFormatRule(BaseModel):
    """Columns that share a display format — "these are money" — as
    the pack author knows them. Semantic knowledge about the schema,
    so it lives in the map beside concepts and metrics, not in the
    generated dictionary (whose DOUBLE cannot tell dollars from a
    weight) and not in engine code (CLAUDE.md: config over code).
    Consumer: run_sql's table envelope, which tags result columns."""

    model_config = ConfigDict(extra="forbid")

    # "table.column", joined to the dictionary by the validator.
    columns: list[str]
    format: Literal["money"]


class EntityKind(BaseModel):
    """A kind of thing a conversation refers back to — "that invoice",
    "this rule" — as the pack author names it: the columns that identify
    one (keys), the columns that name one (names), and the nouns a
    question uses for it (Backlog Pass). Consumers: run_sql's key lint
    reads key_columns, with every dictionary primary and foreign key, as
    id-like — a literal on one that no result, question, or grounding in
    the conversation carried draws a challenge; the harness records the
    entity a turn's evidence established from key ∪ name columns; the
    Verifier checks a follow-up's declared entity against that anchor.
    Name columns are never id-like: a label the model legitimately
    spells from the docs on a first turn (a rule name) must not draw a
    key challenge — a ruling, recorded in the pass ledger."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    synonyms: list[str] = []
    key_columns: list[str] = []  # "table.column"
    name_columns: list[str] = []  # "table.column"
    provenance: DocProvenance | None = None  # None inherits the document block

    @model_validator(mode="after")
    def _shape(self) -> "EntityKind":
        if not self.kind.strip():
            raise ValueError("entity kind must be a non-empty name")
        for qualified in [*self.key_columns, *self.name_columns]:
            table, dot, column = qualified.partition(".")
            if not (table and dot and column) or "." in column:
                raise ValueError(
                    f"entity {self.kind!r}: column must be written "
                    f"table.column, got {qualified!r}"
                )
        if not self.key_columns and not self.name_columns:
            raise ValueError(
                f"entity {self.kind!r} must declare at least one key or "
                "name column"
            )
        return self

    @property
    def columns(self) -> list[str]:
        """key ∪ name columns, keys first, in declaration order."""
        return list(dict.fromkeys([*self.key_columns, *self.name_columns]))


class DictionaryMap(BaseModel):
    """The Data Dictionary Map substrate (Brief §4.2): the semantic /
    routing layer over the dictionary. This whole artifact IS the
    grounding payload for run_sql (Brief §7)."""

    model_config = ConfigDict(extra="forbid")

    provenance: DocProvenance
    concepts: list[Concept] = []
    metrics: list[CanonicalMetric] = []
    join_paths: list[JoinPath] = []
    gotchas: list[Gotcha] = []
    examples: list[WhereToLookExample] = []
    column_formats: list[ColumnFormatRule] = []
    entities: list[EntityKind] = []


class BusinessDoc(BaseModel):
    """One business-context document (Brief §4.10): a curated markdown
    memo snapshotted into the pack, with front-matter provenance naming
    exactly where and when the snapshot was taken."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    author: str = ""
    # The memo's own front-matter date, kept as authored text. (Named
    # doc_date because a field named `date` would shadow the type.)
    doc_date: str = ""
    status: str = ""
    source_repo: str
    source_path: str
    source_commit_sha: str
    copied_date: date
    body: str
