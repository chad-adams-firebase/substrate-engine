"""SubstrateStore — typed read access to substrate tables (Brief §3).

The Phase 1 read-by-name placeholder is gone; this is the flagged
Phase 1→3 refinement. One getter per substrate, each returning the
whole substrate as §4 models: substrate volumes are small enough to
hold, tools index in memory (engine.substrates.ckg_index), and the
Delta real adapter becomes one SELECT * per getter — the simplest
thing to debug at work without an agent.

Read-only by construction: there are no write methods. Substrates are
written by generators and pack authors, never by the engine at answer
time.
"""

from typing import Protocol

from engine.substrates.models import (
    BusinessDoc,
    CkgConditional,
    CkgEdge,
    CkgNode,
    Component,
    ComponentMembership,
    DictionaryMap,
    DictionaryRow,
    StatsRow,
)


class SubstrateStoreError(Exception):
    """A substrate this store was asked for cannot be served. The
    message names the missing file or artifact, speaking to the pack
    author."""


class SubstrateStorePort(Protocol):
    def dictionary(self) -> list[DictionaryRow]: ...

    def stats(self) -> list[StatsRow]: ...

    def ckg_nodes(self) -> list[CkgNode]: ...

    def ckg_edges(self) -> list[CkgEdge]: ...

    def ckg_conditionals(self) -> list[CkgConditional]: ...

    def components(self) -> list[Component]: ...

    def memberships(self) -> list[ComponentMembership]: ...

    def primer(self) -> str | None: ...

    def dictionary_map(self) -> DictionaryMap: ...

    def business_docs(self) -> list[BusinessDoc]: ...
