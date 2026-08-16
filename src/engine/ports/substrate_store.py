"""SubstrateStore — typed read access to substrate tables.

Phase 1 placeholder (flagged in the plan): "typed read access" needs
the §4 substrate schemas, which are Phase 2 deliverables. Until then
this port exposes read-by-name returning name-keyed dicts; the typed
refinement lands in Phase 3 together with its local adapter.
"""

from typing import Any, Protocol


class SubstrateStorePort(Protocol):
    def read_rows(self, substrate: str) -> list[dict[str, Any]]: ...
