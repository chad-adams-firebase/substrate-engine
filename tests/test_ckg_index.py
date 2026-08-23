"""CkgIndex: every hop a lookup over rows, ordered by source line."""

from engine.substrates.ckg_index import CkgIndex
from engine.substrates.models import (
    CkgConditional,
    CkgEdge,
    CkgNode,
    Component,
    ComponentMembership,
    Provenance,
)

MACHINE = Provenance(
    source="machine", confidence=1.0, needs_validation=False, manifest_id="m1"
)
HUMAN = Provenance(source="human", confidence=1.0, needs_validation=False)


def _node(node_id: str, qualified_name: str, kind: str = "function") -> CkgNode:
    return CkgNode(
        id=node_id,
        kind=kind,
        qualified_name=qualified_name,
        file_path="pkg/mod.py",
        start_line=1,
        end_line=10,
        provenance=MACHINE,
    )


def _call(edge_id: str, source: str, target: str, line: int) -> CkgEdge:
    return CkgEdge(
        id=edge_id,
        source_id=source,
        kind="calls",
        target_node_id=target,
        line=line,
        provenance=MACHINE,
    )


def _contains(edge_id: str, source: str, target: str, line: int) -> CkgEdge:
    return CkgEdge(
        id=edge_id,
        source_id=source,
        kind="contains",
        target_node_id=target,
        line=line,
        provenance=MACHINE,
    )


def _index() -> CkgIndex:
    nodes = [
        _node("n0", "pkg.mod", kind="module"),
        _node("n1", "pkg.mod.entry"),
        _node("n2", "pkg.mod.first"),
        _node("n3", "pkg.mod.second"),
    ]
    edges = [
        # Deliberately declared out of line order: the index must sort.
        _call("e2", "n1", "n3", 8),
        _call("e1", "n1", "n2", 3),
        # Contains edges too — declaration order must be line order.
        _contains("c2", "n0", "n2", 12),
        _contains("c1", "n0", "n1", 2),
        _contains("c3", "n0", "n3", 20),
        CkgEdge(
            id="e3",
            source_id="n2",
            kind="reads_table",
            target_table="invoices",
            line=5,
            provenance=MACHINE,
        ),
    ]
    conditionals = [
        CkgConditional(node_id="n2", condition_text="x > 0.15", line=4, provenance=MACHINE)
    ]
    components = [
        Component(id="app.core", name="Core", description="", provenance=HUMAN)
    ]
    memberships = [
        ComponentMembership(
            component_id="app.core",
            ckg_node_id="n1",
            node_qualified_name="pkg.mod.entry",
            provenance=MACHINE,
        ),
        ComponentMembership(
            component_id="app.core",
            ckg_node_id="gone",  # orphaned by a regeneration; skipped
            node_qualified_name="pkg.mod.vanished",
            provenance=MACHINE,
        ),
    ]
    return CkgIndex(nodes, edges, conditionals, components, memberships)


def test_resolve_by_id_and_qualified_name():
    index = _index()
    assert index.resolve_node("n1").qualified_name == "pkg.mod.entry"
    assert index.resolve_node("pkg.mod.entry").id == "n1"
    assert index.resolve_node("nope") is None
    assert index.resolve_component("app.core").name == "Core"


def test_suffix_resolution_unique_ambiguous_and_empty():
    # Addendum N4: a bare dotted suffix — the most human phrasing —
    # resolves when unique; resolve_node stays exact (dereferencing
    # never guesses), and ambiguity returns every candidate for the
    # caller's steering error.
    index = _index()
    (only,) = index.resolve_suffix("entry")
    assert only.id == "n1"
    assert index.resolve_suffix("nope") == []
    assert index.resolve_node("entry") is None

    ambiguous = CkgIndex(
        [_node("a1", "pkg.alpha.run"), _node("a2", "pkg.beta.run")],
        [],
        [],
        [],
        [],
    )
    assert [n.id for n in ambiguous.resolve_suffix("run")] == ["a1", "a2"]


def test_callees_ordered_by_call_site_line():
    index = _index()
    callees = index.callees("n1")
    assert [(e.target_node_id, e.line) for e in callees] == [("n2", 3), ("n3", 8)]


def test_callers_reads_and_conditionals():
    index = _index()
    assert [e.source_id for e in index.callers("n2")] == ["n1"]
    assert [e.target_table for e in index.reads_tables("n2")] == ["invoices"]
    assert index.writes_tables("n2") == []
    assert [c.condition_text for c in index.conditionals("n2")] == ["x > 0.15"]


def test_contains_ordered_by_definition_line():
    index = _index()
    contained = index.contains("n0")
    assert [(e.target_node_id, e.line) for e in contained] == [
        ("n1", 2),
        ("n2", 12),
        ("n3", 20),
    ]
    # calls edges from another node never leak into the contains hop
    assert index.contains("n1") == []


def test_members_skip_orphaned_node_ids():
    index = _index()
    assert [n.id for n in index.members("app.core")] == ["n1"]
