"""answer_from_known_items — search the published Unit-of-Work library.

The library lives in WorkStore, not SubstrateStore (Brief §4's
dual-role note — deliberate, do not "fix"). Empty until Phase 6
publishes anything; matches are suggestions, never redirects.
"""

from pydantic import BaseModel, ConfigDict

from engine.config.models import ToolName
from engine.ports.work_store import WorkStorePort
from engine.tools.base import Tool
from engine.tools.envelope import KnownItemsOutput, ToolInvocation


class KnownItemsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


class AnswerFromKnownItems(Tool):
    name = ToolName.ANSWER_FROM_KNOWN_ITEMS
    description = (
        "Search the library of published analyses for ones matching a "
        "question — surfaced as suggestions when someone has already "
        "answered something similar."
    )
    input_model = KnownItemsInput

    def __init__(self, work_store: WorkStorePort) -> None:
        self._work_store = work_store

    def run(self, params: KnownItemsInput) -> ToolInvocation:
        matches = self._work_store.search_published_units(params.query)
        # substrates_read stays empty: the library is engine-owned
        # work product, not a substrate of the target application.
        return self.ok(
            params, KnownItemsOutput(matches=matches), substrates_read=[]
        )
