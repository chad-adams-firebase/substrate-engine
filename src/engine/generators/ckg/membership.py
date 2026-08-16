"""L1 membership proposals from module structure (Brief §5 L1).

No LLM involvement in this phase: the deterministic signal is the
module path itself. A module's dotted segments (top-level package
dropped, underscores slugged to hyphens) are matched against the
declared component ids, truncating from the right until one fits:

    invoiceguard.spine.lapse_lifecycle -> ig.spine.lapse-lifecycle
    invoiceguard.platform.api.teams    -> ig.platform.api.teams (no)
                                       -> ig.platform.api        (yes)

Proposals are made for MODULE nodes only — traversal reaches the
functions through contains edges, and per-function membership would
drown the human validators the proposals exist to serve. Modules that
match nothing are reported unassigned; that list is signal (models/,
by design, and anything genuinely new).

Human overlay rows are keyed by (component_id, node_qualified_name) —
the hash id is machine detail — and suppress the machine proposal for
that node. They are never overwritten (CLAUDE.md data law).
"""

from engine.substrates.models import ComponentMembership, Provenance

PROPOSAL_CONFIDENCE = 0.6


def _slug(segment: str) -> str:
    return segment.replace("_", "-")


def propose_component_id(
    module_qualified_name: str, prefix: str, component_ids: set[str]
) -> str | None:
    segments = module_qualified_name.split(".")[1:]
    while segments:
        candidate = ".".join([prefix, *(_slug(part) for part in segments)])
        if candidate in component_ids:
            return candidate
        segments = segments[:-1]
    return None


def propose_memberships(
    module_nodes: list[tuple[str, str]],  # (qualified_name, node_id)
    prefix: str,
    component_ids: set[str],
    overlay: list[ComponentMembership],
    manifest_id: str,
) -> tuple[list[ComponentMembership], list[str]]:
    """Returns (memberships, warnings): machine proposals for matched
    modules, human overlay rows passed through with resolved node ids,
    and warnings for unassigned modules, unreferenced components, and
    unresolvable overlay rows."""
    warnings: list[str] = []
    node_id_by_name = dict(module_nodes)

    human_by_name: dict[str, ComponentMembership] = {}
    for row in overlay:
        if row.provenance.source != "human":
            raise ValueError(
                f"membership overlay row for {row.node_qualified_name!r} is "
                f"not source=human — overlays are the human layer only"
            )
        human_by_name[row.node_qualified_name] = row

    rows: list[ComponentMembership] = []
    assigned_components: set[str] = set()

    for qualified_name, node_id in sorted(module_nodes):
        human = human_by_name.pop(qualified_name, None)
        if human is not None:
            rows.append(human.model_copy(update={"ckg_node_id": node_id}))
            assigned_components.add(human.component_id)
            continue
        component_id = propose_component_id(qualified_name, prefix, component_ids)
        if component_id is None:
            warnings.append(f"module {qualified_name} matches no component")
            continue
        assigned_components.add(component_id)
        rows.append(
            ComponentMembership(
                component_id=component_id,
                ckg_node_id=node_id,
                node_qualified_name=qualified_name,
                provenance=Provenance(
                    source="machine",
                    confidence=PROPOSAL_CONFIDENCE,
                    needs_validation=True,
                    manifest_id=manifest_id,
                ),
            )
        )

    for qualified_name, human in sorted(human_by_name.items()):
        warnings.append(
            f"membership overlay row for {qualified_name} matches no module "
            f"node; preserved but needs validation"
        )
        rows.append(
            human.model_copy(
                update={
                    "provenance": human.provenance.model_copy(
                        update={"needs_validation": True}
                    )
                }
            )
        )

    for component_id in sorted(component_ids - assigned_components):
        warnings.append(f"component {component_id} has no member modules")

    return rows, warnings
