"""traverse_code_knowledge_graph — one hop at a time, every hop a
lookup against the extracted graph, never a guess (Brief §5).

Callees and callers come back ordered by call-site line: "what does
the scoring entry point call, in order" is answered by position in
the source, which is the only order the extraction records.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.config.models import SubstrateName, ToolName
from engine.ports.substrate_store import SubstrateStoreError, SubstrateStorePort
from engine.substrates.ckg_index import CkgIndex
from engine.tools.base import Tool, manifest_ids_of
from engine.tools.envelope import CkgTraversalOutput, ToolInvocation

Hop = Literal[
    "node",
    "members",
    "contains",
    "callees",
    "callers",
    "reads_tables",
    "writes_tables",
    "conditionals",
]


class CkgTraverseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A component id (for members), a node id, or a qualified name.
    entry: str
    hop: Hop


class TraverseCodeKnowledgeGraph(Tool):
    name = ToolName.TRAVERSE_CODE_KNOWLEDGE_GRAPH
    description = (
        "Traverse the code knowledge graph one hop from an entry point "
        "(a component id, node id, or qualified name): a node's details, "
        "a component's members (its modules), the definitions a module "
        "or class contains in source order, a function's callees or "
        "callers in source order, the tables it reads or writes, or its "
        "branch conditions. Component members are modules, so reaching "
        "a component's functions takes two hops: members, then contains."
    )
    input_model = CkgTraverseInput

    def __init__(self, store: SubstrateStorePort) -> None:
        self._store = store
        self._lazy_index: CkgIndex | None = None

    @property
    def _index(self) -> CkgIndex:
        if self._lazy_index is None:
            self._lazy_index = CkgIndex(
                self._store.ckg_nodes(),
                self._store.ckg_edges(),
                self._store.ckg_conditionals(),
                self._store.components(),
                self._store.memberships(),
            )
        return self._lazy_index

    _SUBSTRATES = [
        SubstrateName.CODE_KNOWLEDGE_GRAPH,
        SubstrateName.CKG_COMPONENTS,
    ]

    def run(self, params: CkgTraverseInput) -> ToolInvocation:
        try:
            index = self._index
        except SubstrateStoreError as exc:
            return self.fail(params, str(exc))

        if params.hop == "members":
            component = index.resolve_component(params.entry)
            if component is None:
                known = ", ".join(sorted(index.component_by_id))
                return self.fail(
                    params,
                    f"No component {params.entry!r}. Components: {known}.",
                )
            members = index.members(component.id)
            return self.ok(
                params,
                CkgTraversalOutput(entry_component=component, nodes=members),
                substrates_read=self._SUBSTRATES,
                manifest_ids=manifest_ids_of(members),
            )

        node = index.resolve_node(params.entry)
        if node is None:
            hint = ""
            if index.resolve_component(params.entry) is not None:
                hint = (
                    f" ({params.entry!r} is a component — use hop='members' "
                    f"to list its nodes)"
                )
            return self.fail(
                params,
                f"No CKG node with id or qualified name {params.entry!r}{hint}.",
            )

        output = CkgTraversalOutput(entry_node=node)
        if params.hop == "node":
            output = output.model_copy(
                update={"conditionals": index.conditionals(node.id)}
            )
        elif params.hop in ("contains", "callees", "callers"):
            if params.hop == "contains":
                edges = index.contains(node.id)
            elif params.hop == "callees":
                edges = index.callees(node.id)
            else:
                edges = index.callers(node.id)
            # The counterpart nodes, in the same (line) order, so the
            # caller gets names alongside edge rows. Contains edges sit
            # at the child's start line, so this order is definition
            # order.
            counterpart_ids = [
                edge.source_id if params.hop == "callers" else edge.target_node_id
                for edge in edges
            ]
            nodes = [
                index.node_by_id[node_id]
                for node_id in counterpart_ids
                if node_id in index.node_by_id
            ]
            output = output.model_copy(update={"edges": edges, "nodes": nodes})
        elif params.hop == "reads_tables":
            output = output.model_copy(update={"edges": index.reads_tables(node.id)})
        elif params.hop == "writes_tables":
            output = output.model_copy(update={"edges": index.writes_tables(node.id)})
        else:  # conditionals
            output = output.model_copy(
                update={"conditionals": index.conditionals(node.id)}
            )

        consulted = [node, *output.nodes, *output.edges, *output.conditionals]
        return self.ok(
            params,
            output,
            substrates_read=self._SUBSTRATES,
            manifest_ids=manifest_ids_of(consulted),
        )
