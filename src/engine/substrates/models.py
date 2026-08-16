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
    generator: Literal["dictionary", "stats", "ckg", "sqlite_convert"]
    generator_version: str
    source_commit_sha: str | None = None
    simulation_seed: int | None = None
    # DB-derived substrates: the tables actually read.
    source_tables: list[str] = []
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
