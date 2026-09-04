"""The conformance checks: pack substrates vs the live target.

Each check produces PASS, WARN, or FAIL with detail lines naming the
offending row — the person reading the report is debugging a real
pack at work with no agent to help, so "FAIL: 3 rows" is useless and
"FAIL: dictionary row invoices.legacy_col absent from database" is
the tool.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.generators.ckg.primer_check import check_primer
from engine.ports.source_code import SourceCodePort
from engine.ports.sql import SqlPort
from engine.ports.types import User
from engine.substrates.jsonl import read_rows
from engine.substrates.manifest import load_manifest
from engine.substrates.models import (
    CkgConditional,
    CkgEdge,
    CkgNode,
    ComponentMembership,
    DictionaryRow,
    Manifest,
    StatsRow,
)
from engine.substrates.pack_data import (
    PackDataError,
    load_business_docs,
    load_components,
    load_dictionary_map,
    load_primer,
)

Status = Literal["PASS", "WARN", "FAIL"]


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: Status
    details: list[str] = []


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_name: str
    checks: list[CheckResult]

    @property
    def passed(self) -> bool:
        return all(check.status != "FAIL" for check in self.checks)


class ConformanceValidator:
    def __init__(
        self,
        sql: SqlPort,
        source: SourceCodePort,
        identity: User,
        component_id_prefix: str,
    ) -> None:
        self._sql = sql
        self._source = source
        self._identity = identity
        self._prefix = component_id_prefix

    def validate(self, pack_root: Path, pack_name: str) -> ValidationReport:
        substrates = pack_root / "substrates"
        checks: list[CheckResult] = []

        loaded, load_check = self._load_substrates(substrates)
        checks.append(load_check)
        if load_check.status == "FAIL":
            return ValidationReport(pack_name=pack_name, checks=checks)

        manifests = self._load_manifests(substrates / "manifests")
        checks.append(self._check_dictionary_against_db(loaded["dictionary"]))
        checks.append(
            self._check_edges_resolve(
                loaded["ckg_nodes"], loaded["ckg_edges"], loaded["dictionary"]
            )
        )
        checks.append(self._check_source_locations(loaded["ckg_nodes"]))
        checks.append(self._check_manifest_links(loaded, manifests))
        checks.append(self._check_pinned_sha(manifests))
        checks.append(self._check_primer(pack_root))
        checks.append(
            self._check_dictionary_map(pack_root, loaded["dictionary"])
        )
        checks.append(self._check_business_docs(pack_root))
        return ValidationReport(pack_name=pack_name, checks=checks)

    def _load_substrates(self, substrates: Path) -> tuple[dict, CheckResult]:
        expected = {
            "dictionary": DictionaryRow,
            "univariate_stats": StatsRow,
            "ckg_nodes": CkgNode,
            "ckg_edges": CkgEdge,
            "ckg_conditionals": CkgConditional,
            "component_memberships": ComponentMembership,
        }
        loaded: dict = {}
        details: list[str] = []
        for name, model in expected.items():
            path = substrates / f"{name}.jsonl"
            if not path.is_file():
                details.append(f"missing substrate file {path.name}")
                continue
            try:
                loaded[name] = read_rows(path, model)
            except Exception as exc:  # legible over precise: report and stop
                details.append(f"{path.name} failed to load: {exc}")
        status: Status = "FAIL" if details else "PASS"
        return loaded, CheckResult(
            name="substrate files load against the §4 contracts",
            status=status,
            details=details,
        )

    def _load_manifests(self, directory: Path) -> dict[str, Manifest]:
        manifests: dict[str, Manifest] = {}
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                manifest = load_manifest(path)
                manifests[manifest.manifest_id] = manifest
        return manifests

    def _check_dictionary_against_db(
        self, dictionary: list[DictionaryRow]
    ) -> CheckResult:
        details: list[str] = []
        try:
            rows = self._sql.run_sql(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'main'",
                self._identity,
            )
        except Exception as exc:
            return CheckResult(
                name="dictionary matches the live database",
                status="FAIL",
                details=[f"database unreachable: {exc}"],
            )
        live = {(row["table_name"], row["column_name"]) for row in rows}
        live_tables = {table for table, _ in live}
        for row in dictionary:
            if row.provenance.source == "human":
                continue  # orphaned human rows are the generator's warning
            if row.column_name == "":
                if row.table_name not in live_tables:
                    details.append(
                        f"dictionary table {row.table_name} absent from database"
                    )
            elif (row.table_name, row.column_name) not in live:
                details.append(
                    f"dictionary row {row.table_name}.{row.column_name} "
                    f"absent from database"
                )
        return CheckResult(
            name="dictionary matches the live database",
            status="FAIL" if details else "PASS",
            details=details,
        )

    def _check_edges_resolve(
        self,
        nodes: list[CkgNode],
        edges: list[CkgEdge],
        dictionary: list[DictionaryRow],
    ) -> CheckResult:
        details: list[str] = []
        node_ids = {node.id for node in nodes}
        known_tables = {
            row.table_name for row in dictionary if row.column_name == ""
        }
        for edge in edges:
            if edge.source_id not in node_ids:
                details.append(f"edge {edge.id}: source node {edge.source_id} missing")
            if edge.target_node_id is not None and edge.target_node_id not in node_ids:
                details.append(
                    f"edge {edge.id}: target node {edge.target_node_id} missing"
                )
            if edge.target_table is not None and edge.target_table not in known_tables:
                details.append(
                    f"edge {edge.id}: {edge.kind} targets unknown table "
                    f"{edge.target_table}"
                )
        return CheckResult(
            name="CKG edges resolve to nodes and dictionary tables",
            status="FAIL" if details else "PASS",
            details=details,
        )

    def _check_source_locations(self, nodes: list[CkgNode]) -> CheckResult:
        details: list[str] = []
        line_counts: dict[str, int] = {}
        for node in nodes:
            if node.file_path not in line_counts:
                try:
                    text = self._source.read(node.file_path)
                except FileNotFoundError:
                    details.append(f"node {node.qualified_name}: {node.file_path} unreadable")
                    line_counts[node.file_path] = -1
                    continue
                line_counts[node.file_path] = len(text.splitlines())
            count = line_counts[node.file_path]
            if count >= 0 and node.end_line > count:
                details.append(
                    f"node {node.qualified_name}: lines "
                    f"{node.start_line}-{node.end_line} exceed "
                    f"{node.file_path} ({count} lines)"
                )
        return CheckResult(
            name="CKG locations readable at the pinned commit",
            status="FAIL" if details else "PASS",
            details=details,
        )

    def _check_manifest_links(
        self, loaded: dict, manifests: dict[str, Manifest]
    ) -> CheckResult:
        details: list[str] = []
        for substrate, rows in loaded.items():
            for row in rows:
                manifest_id = row.provenance.manifest_id
                if (
                    row.provenance.source == "machine"
                    and manifest_id not in manifests
                ):
                    details.append(
                        f"{substrate}: machine row links to unknown manifest "
                        f"{manifest_id}"
                    )
                    break  # one per substrate keeps the report readable
        return CheckResult(
            name="machine rows link to recorded manifests",
            status="FAIL" if details else "PASS",
            details=details,
        )

    def _check_pinned_sha(self, manifests: dict[str, Manifest]) -> CheckResult:
        details: list[str] = []
        pinned = self._source.commit_sha
        for manifest in manifests.values():
            if (
                manifest.source_commit_sha is not None
                and manifest.source_commit_sha != pinned
            ):
                details.append(
                    f"manifest {manifest.generator} extracted at "
                    f"{manifest.source_commit_sha[:12]} but the source is "
                    f"pinned to {pinned[:12]} — line references are invalid"
                )
        return CheckResult(
            name="manifests share the source's pinned commit",
            status="FAIL" if details else "PASS",
            details=details,
        )

    def _check_dictionary_map(
        self, pack_root: Path, dictionary: list[DictionaryRow]
    ) -> CheckResult:
        """The map joins to the dictionary by table/column names
        (Brief §4.2) — a reference to a table or column the dictionary
        does not know is a broken route run_sql would ground on."""
        name = "dictionary map references resolve against the dictionary"
        path = pack_root / "dictionary_map.yaml"
        if not path.is_file():
            return CheckResult(
                name=name, status="WARN", details=["no dictionary_map.yaml in the pack"]
            )
        try:
            dictionary_map = load_dictionary_map(path)
        except PackDataError as exc:
            return CheckResult(name=name, status="FAIL", details=[str(exc)])

        known_tables = {row.table_name for row in dictionary if row.column_name == ""}
        known_columns = {
            (row.table_name, row.column_name)
            for row in dictionary
            if row.column_name != ""
        }
        details: list[str] = []

        def check_tables(kind: str, entry_name: str, tables: list[str]) -> None:
            for table in tables:
                if table not in known_tables:
                    details.append(
                        f"{kind} {entry_name!r}: unknown table {table}"
                    )

        for concept in dictionary_map.concepts:
            check_tables("concept", concept.name, concept.tables)
        for metric in dictionary_map.metrics:
            check_tables("metric", metric.name, metric.tables)
        for gotcha in dictionary_map.gotchas:
            check_tables("gotcha", gotcha.name, gotcha.tables)
        for join_path in dictionary_map.join_paths:
            for step in join_path.steps:
                for table, column in (
                    (step.from_table, step.from_column),
                    (step.to_table, step.to_column),
                ):
                    if (table, column) not in known_columns:
                        details.append(
                            f"join path {join_path.name!r}: unknown column "
                            f"{table}.{column}"
                        )
            path_tables = {
                table
                for step in join_path.steps
                for table in (step.from_table, step.to_table)
            }
            for condition in join_path.one_to_one_when:
                if (condition.table, condition.column_name) not in known_columns:
                    details.append(
                        f"join path {join_path.name!r}: one_to_one_when names "
                        f"unknown column {condition.column}"
                    )
                elif condition.table not in path_tables:
                    details.append(
                        f"join path {join_path.name!r}: one_to_one_when column "
                        f"{condition.column} is not on the path's tables"
                    )
        for rule in dictionary_map.column_formats:
            for qualified in rule.columns:
                table, _, column = qualified.partition(".")
                if not column or (table, column) not in known_columns:
                    details.append(
                        f"column format {rule.format!r}: unknown column "
                        f"{qualified}"
                    )
        return CheckResult(
            name=name, status="FAIL" if details else "PASS", details=details
        )

    def _check_business_docs(self, pack_root: Path) -> CheckResult:
        name = "business docs carry valid snapshot front matter"
        directory = pack_root / "business_docs"
        if not directory.is_dir():
            return CheckResult(
                name=name, status="WARN", details=["no business_docs/ in the pack"]
            )
        try:
            docs = load_business_docs(directory)
        except PackDataError as exc:
            return CheckResult(name=name, status="FAIL", details=[str(exc)])
        if not docs:
            return CheckResult(
                name=name, status="WARN", details=["business_docs/ holds no .md files"]
            )
        return CheckResult(
            name=name,
            status="PASS",
            details=[f"{len(docs)} doc(s) snapshotted"],
        )

    def _check_primer(self, pack_root: Path) -> CheckResult:
        primer = load_primer(pack_root / "primer.md")
        if primer is None:
            return CheckResult(
                name="primer references declared components",
                status="WARN",
                details=["no primer.md in the pack"],
            )
        components = load_components(pack_root / "components.yaml")
        errors, warnings = check_primer(
            primer, self._prefix, {component.id for component in components}
        )
        if errors:
            return CheckResult(
                name="primer references declared components",
                status="FAIL",
                details=errors,
            )
        return CheckResult(
            name="primer references declared components",
            status="WARN" if warnings else "PASS",
            details=warnings,
        )
