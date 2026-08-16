"""CKG generator: the full L2 extraction + L1 proposals + L0 check.

Orchestrates the passes over a source tree reached only through
SourceCodePort at a pinned commit SHA (CLAUDE.md architecture law):

  1. walk      — per-file AST: nodes, containment, conditionals,
                 module facts (walker.py)
  2. index     — ORM model index (model_index.py), call index (calls.py)
  3. resolve   — imports, calls, table access edges
  4. propose   — L1 memberships from module structure (membership.py)
  5. check     — L0 primer references (primer_check.py)

Node ids are content-addressed (hash of qualified name + kind) and
edge ids hash their endpoints + kind + line, so regeneration against
the same commit is byte-stable regardless of walk order — the
phasing's idempotency criterion is a property of these ids, not of
effort.
"""

import fnmatch
import hashlib

from engine.config.models import GenerationConfig
from engine.generators.ckg import calls as calls_pass
from engine.generators.ckg.membership import propose_memberships
from engine.generators.ckg.model_index import build_model_index
from engine.generators.ckg.primer_check import check_primer
from engine.generators.ckg.table_access import extract_table_access
from engine.generators.ckg.walker import WalkedModule, walk_module
from engine.ports.source_code import SourceCodePort
from engine.substrates.manifest import build_manifest
from engine.substrates.models import (
    CkgConditional,
    CkgEdge,
    CkgNode,
    Component,
    ComponentMembership,
    Manifest,
    Provenance,
)

GENERATOR_VERSION = "1.0.0"


def node_id(qualified_name: str, kind: str) -> str:
    """Content-addressed node identity (Brief §5 L2): never positional,
    never generation-ordered."""
    digest = hashlib.sha256(f"{qualified_name}\x00{kind}".encode("utf-8"))
    return digest.hexdigest()[:16]


def _edge_id(source_id: str, kind: str, target: str, line: int) -> str:
    digest = hashlib.sha256(
        f"{source_id}\x00{kind}\x00{target}\x00{line}".encode("utf-8")
    )
    return digest.hexdigest()[:16]


class CkgExtraction:
    """One extraction's complete output. A plain result object — the
    caller (CLI) writes the files."""

    def __init__(
        self,
        nodes: list[CkgNode],
        edges: list[CkgEdge],
        conditionals: list[CkgConditional],
        memberships: list[ComponentMembership],
        manifest: Manifest,
        warnings: list[str],
        errors: list[str],
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.conditionals = conditionals
        self.memberships = memberships
        self.manifest = manifest
        self.warnings = warnings
        self.errors = errors


class CkgGenerator:
    def __init__(self, source: SourceCodePort, config: GenerationConfig) -> None:
        self._source = source
        self._config = config

    def generate(
        self,
        components: list[Component],
        membership_overlay: list[ComponentMembership],
        primer_text: str | None,
    ) -> CkgExtraction:
        manifest = build_manifest(
            "ckg",
            GENERATOR_VERSION,
            source_commit_sha=self._source.commit_sha,
            simulation_seed=self._config.simulation_seed,
        )
        machine = Provenance(
            source="machine",
            confidence=1.0,
            needs_validation=False,
            manifest_id=manifest.manifest_id,
        )
        warnings: list[str] = []

        modules = [
            walk_module(path, self._source.read(path))
            for path in self._selected_files()
        ]

        nodes: list[CkgNode] = []
        conditionals: list[CkgConditional] = []
        edges: list[CkgEdge] = []
        for module in modules:
            for walked in module.nodes:
                nodes.append(
                    CkgNode(
                        id=node_id(walked.qualified_name, walked.kind),
                        kind=walked.kind,
                        qualified_name=walked.qualified_name,
                        file_path=module.file_path,
                        start_line=walked.start_line,
                        end_line=walked.end_line,
                        signature=walked.signature,
                        docstring=walked.docstring,
                        value=walked.value,
                        provenance=machine,
                    )
                )
        kind_by_name = {node.qualified_name: node.kind for node in nodes}
        known_modules = {
            module.module_name: module.file_path for module in modules
        }

        def edge(
            source_name: str,
            kind: str,
            line: int,
            *,
            target_name: str | None = None,
            target_table: str | None = None,
        ) -> CkgEdge:
            source = node_id(source_name, kind_by_name[source_name])
            target = (
                target_table
                if target_table is not None
                else node_id(target_name, kind_by_name[target_name])
            )
            return CkgEdge(
                id=_edge_id(source, kind, target, line),
                source_id=source,
                kind=kind,
                target_node_id=None if target_table is not None else target,
                target_table=target_table,
                line=line,
                provenance=machine,
            )

        model_index, index_warnings = build_model_index(modules)
        warnings.extend(index_warnings)
        call_index = calls_pass.build_call_index(modules)

        for module in modules:
            for parent, child, line in module.contains:
                edges.append(edge(parent, "contains", line, target_name=child))
            for imported, line in module.imports:
                if imported in known_modules:
                    edges.append(
                        edge(
                            module.module_name,
                            "imports",
                            line,
                            target_name=imported,
                        )
                    )
            for resolved in calls_pass.extract_calls(module, call_index):
                edges.append(
                    edge(
                        resolved.caller_qualified_name,
                        "calls",
                        resolved.line,
                        target_name=resolved.callee_qualified_name,
                    )
                )
            for access in extract_table_access(module, model_index):
                edges.append(
                    edge(
                        access.owner_qualified_name,
                        access.kind,
                        access.line,
                        target_table=access.table,
                    )
                )
            for walked in module.conditionals:
                conditionals.append(
                    CkgConditional(
                        node_id=node_id(
                            walked.owner_qualified_name,
                            kind_by_name[walked.owner_qualified_name],
                        ),
                        condition_text=walked.condition_text,
                        line=walked.line,
                        provenance=machine,
                    )
                )

        module_nodes = [
            (module.module_name, node_id(module.module_name, "module"))
            for module in modules
        ]
        memberships, membership_warnings = propose_memberships(
            module_nodes,
            self._config.component_id_prefix,
            {component.id for component in components},
            membership_overlay,
            manifest.manifest_id,
        )
        warnings.extend(membership_warnings)

        errors: list[str] = []
        if primer_text is not None:
            primer_errors, primer_warnings = check_primer(
                primer_text,
                self._config.component_id_prefix,
                {component.id for component in components},
            )
            errors.extend(primer_errors)
            warnings.extend(primer_warnings)

        return CkgExtraction(
            nodes=nodes,
            edges=edges,
            conditionals=conditionals,
            memberships=memberships,
            manifest=manifest,
            warnings=warnings,
            errors=errors,
        )

    def _selected_files(self) -> list[str]:
        selected = []
        for path in self._source.list_files():
            if not path.endswith(".py"):
                continue
            if not any(
                fnmatch.fnmatch(path, glob) for glob in self._config.source_globs
            ):
                continue
            if any(
                fnmatch.fnmatch(path, glob)
                for glob in self._config.exclude_globs
            ):
                continue
            selected.append(path)
        return sorted(selected)
