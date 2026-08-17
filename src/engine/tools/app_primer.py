"""app_primer — the L0 primer plus L1 components, and nothing deeper.

Reads exactly two artifacts by design (Brief §6): "what is this app"
must never pull the full CKG into context.
"""

from pydantic import BaseModel, ConfigDict

from engine.config.models import SubstrateName, ToolName
from engine.ports.substrate_store import SubstrateStoreError, SubstrateStorePort
from engine.tools.base import Tool
from engine.tools.envelope import PrimerOutput, ToolInvocation


class PrimerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppPrimer(Tool):
    name = ToolName.APP_PRIMER
    description = (
        "Read the application primer: what the app is, how its pipeline "
        "works, and its named components. The right entry point for "
        "orientation questions."
    )
    input_model = PrimerInput

    def __init__(self, store: SubstrateStorePort) -> None:
        self._store = store

    def run(self, params: PrimerInput) -> ToolInvocation:
        try:
            primer = self._store.primer()
            components = self._store.components()
        except SubstrateStoreError as exc:
            return self.fail(params, str(exc))
        if primer is None:
            return self.fail(
                params,
                "This pack enables the primer substrate but has no "
                "primer.md — author one (Brief §5 L0).",
            )
        return self.ok(
            params,
            PrimerOutput(primer=primer, components=components),
            substrates_read=[SubstrateName.PRIMER, SubstrateName.CKG_COMPONENTS],
        )
