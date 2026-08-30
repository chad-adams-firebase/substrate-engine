"""The invocation record's own harvest — tool-agnostic, run beside the
per-tool checks for every ok invocation.

Two things every invocation carries that no per-tool check reads: its
arguments and its output's field names. Both are evidence the drafter
legitimately restates (N13, the poolless-identifier class):

- The arguments are part of the invocation record. A draft that names
  the component or the window it asked about is grounded in this
  turn's evidence; if the router queried the wrong thing, that is a
  routing error the answer honestly reports, not a faithfulness
  violation.
- The field names are the envelope as rendered to the drafter
  (`error_count`, `run_status`, `enum_values`) — the evidence's own
  labels are part of the evidence. They are read from the same view
  the drafter saw, so a None-suppressed field cannot ground.

Shape-gated, never tokenized: an argument value enters vocabulary only
when the WHOLE value is identifier-shaped (the extraction side's own
definition of an entity) and enters strings only when it is a whole
ISO date or timestamp (then reachable by the date paths). A free-text
query is never shredded into citeable words. Nested field names are
harvested as names, not dotted composites. Failed calls contribute
nothing, as everywhere else in the verifier.
"""

import re
from typing import Any

from engine.tools.envelope import ToolInvocation
from engine.verifier.claims import IDENTIFIER_SHAPED
from engine.verifier.models import EvidenceContribution

_ISO_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")


def harvest_invocation(invocation: ToolInvocation) -> EvidenceContribution:
    contribution = EvidenceContribution()
    if invocation.status != "ok" or invocation.output is None:
        return contribution  # failed calls support no claims
    for value in _leaf_strings(invocation.arguments):
        if IDENTIFIER_SHAPED.match(value):
            if len(value) >= 2:
                contribution.vocabulary.add(value)
        elif _ISO_VALUE.match(value):
            contribution.strings.add(value)
    contribution.vocabulary |= {
        key for key in field_names(invocation.rendered_output()) if len(key) >= 2
    }
    return contribution


def field_names(tree: Any) -> set[str]:
    """Every dict key in a rendered tree, leaf and intermediate,
    through lists — the names as the drafter read them."""
    names: set[str] = set()
    if isinstance(tree, dict):
        for key, value in tree.items():
            names.add(str(key))
            names |= field_names(value)
    elif isinstance(tree, list):
        for item in tree:
            names |= field_names(item)
    return names


def _leaf_strings(tree: Any) -> list[str]:
    if isinstance(tree, str):
        return [tree]
    if isinstance(tree, dict):
        return [s for value in tree.values() for s in _leaf_strings(value)]
    if isinstance(tree, list):
        return [s for item in tree for s in _leaf_strings(item)]
    return []
