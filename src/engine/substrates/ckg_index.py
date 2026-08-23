"""In-memory index over CKG rows — every hop a lookup, never a guess.

Pure code over the substrate models: no ports, no I/O. Both the
traverse_code_knowledge_graph tool (Phase 3) and the Verifier's CKG
faithfulness check (Phase 4) consult this same index, so "does this
edge exist" has exactly one implementation.

Edge lists are ordered by source line: "what does run_rules call, in
order" is answered by call-site position, which is the only order the
extraction records.
"""

from engine.substrates.models import (
    CkgConditional,
    CkgEdge,
    CkgNode,
    Component,
    ComponentMembership,
)


def dotted_suffixes(qualified_name: str) -> set[str]:
    """run_rules, rules_engine.run_rules, ... — every suffix a human
    or drafter may reasonably use to name the same thing. Shared by
    the Verifier's suffix vocabulary and resolve_suffix below, so the
    names the engine recognizes and the names its tools can resolve
    stay one set."""
    parts = qualified_name.split(".")
    return {".".join(parts[i:]) for i in range(len(parts))}


class CkgIndex:
    def __init__(
        self,
        nodes: list[CkgNode],
        edges: list[CkgEdge],
        conditionals: list[CkgConditional],
        components: list[Component],
        memberships: list[ComponentMembership],
    ) -> None:
        self.node_by_id: dict[str, CkgNode] = {n.id: n for n in nodes}
        self.node_by_qualified_name: dict[str, CkgNode] = {
            n.qualified_name: n for n in nodes
        }
        self.component_by_id: dict[str, Component] = {c.id: c for c in components}

        self._nodes_by_suffix: dict[str, list[CkgNode]] = {}
        for node in nodes:
            for suffix in dotted_suffixes(node.qualified_name):
                self._nodes_by_suffix.setdefault(suffix, []).append(node)

        self._edges_from: dict[str, list[CkgEdge]] = {}
        self._edges_to: dict[str, list[CkgEdge]] = {}
        for edge in sorted(edges, key=lambda e: e.line):
            self._edges_from.setdefault(edge.source_id, []).append(edge)
            if edge.target_node_id is not None:
                self._edges_to.setdefault(edge.target_node_id, []).append(edge)

        self._conditionals_by_node: dict[str, list[CkgConditional]] = {}
        for conditional in sorted(conditionals, key=lambda c: c.line):
            self._conditionals_by_node.setdefault(conditional.node_id, []).append(
                conditional
            )

        self._members_of: dict[str, list[ComponentMembership]] = {}
        for membership in memberships:
            self._members_of.setdefault(membership.component_id, []).append(membership)

    def resolve_node(self, entry: str) -> CkgNode | None:
        """A node by id or by qualified name — the two handles callers
        legitimately hold (ids from prior hops, names from humans)."""
        return self.node_by_id.get(entry) or self.node_by_qualified_name.get(entry)

    def resolve_suffix(self, entry: str) -> list[CkgNode]:
        """Nodes whose qualified name ends with this dotted suffix, by
        qualified name. The deliberate asymmetry with the Verifier's
        vocabulary: vocabulary accepts ambiguous suffixes (naming),
        resolution demands uniqueness (dereferencing) — callers
        proceed only on exactly one candidate and error naming the
        rest, never guess."""
        return sorted(
            self._nodes_by_suffix.get(entry, []),
            key=lambda n: n.qualified_name,
        )

    def resolve_component(self, entry: str) -> Component | None:
        return self.component_by_id.get(entry)

    def members(self, component_id: str) -> list[CkgNode]:
        """A component's member nodes, ordered by qualified name (the
        membership file's natural key), skipping memberships whose node
        vanished in a regeneration — the L0 checker reports those."""
        memberships = sorted(
            self._members_of.get(component_id, []),
            key=lambda m: m.node_qualified_name,
        )
        return [
            self.node_by_id[m.ckg_node_id]
            for m in memberships
            if m.ckg_node_id in self.node_by_id
        ]

    def edges_from(self, node_id: str, kind: str) -> list[CkgEdge]:
        return [e for e in self._edges_from.get(node_id, []) if e.kind == kind]

    def edges_to(self, node_id: str, kind: str) -> list[CkgEdge]:
        return [e for e in self._edges_to.get(node_id, []) if e.kind == kind]

    def callees(self, node_id: str) -> list[CkgEdge]:
        return self.edges_from(node_id, "calls")

    def contains(self, node_id: str) -> list[CkgEdge]:
        """A module's or class's contained definitions. The walker
        records each contains edge at the child's own start line, so
        line order here IS definition order."""
        return self.edges_from(node_id, "contains")

    def callers(self, node_id: str) -> list[CkgEdge]:
        return self.edges_to(node_id, "calls")

    def reads_tables(self, node_id: str) -> list[CkgEdge]:
        return self.edges_from(node_id, "reads_table")

    def writes_tables(self, node_id: str) -> list[CkgEdge]:
        return self.edges_from(node_id, "writes_table")

    def conditionals(self, node_id: str) -> list[CkgConditional]:
        return self._conditionals_by_node.get(node_id, [])
