"""app_capabilities — the assistant describing itself, from pack
config alone (play pass R6).

"How do I use this chat?" matched no altitude and burned router steps
into a refusal. It is a real question with a real answer, and the
answer is the pack's to give: ui.capabilities text plus the starter
prompts, returned as evidence like any other tool output. The LLM
phrases; the config grounds; the Verifier still runs — no ad-hoc
freedom (CLAUDE.md: new capabilities are registered tools).
"""

from pydantic import BaseModel, ConfigDict

from engine.config.models import ToolName, UiSettings
from engine.tools.base import Tool
from engine.tools.envelope import CapabilitiesOutput, ToolInvocation


class CapabilitiesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppCapabilities(Tool):
    name = ToolName.APP_CAPABILITIES
    description = (
        "Describe this assistant itself: what kinds of questions it "
        "answers, how its evidence discipline works, and example "
        "questions to try. The right tool for how-do-I-use-this and "
        "what-can-I-ask questions."
    )
    input_model = CapabilitiesInput

    def __init__(self, ui: UiSettings) -> None:
        self._ui = ui

    def run(self, params: CapabilitiesInput) -> ToolInvocation:
        if not self._ui.capabilities and not self._ui.starter_prompts:
            return self.fail(
                params,
                "This pack enables app_capabilities but configures "
                "neither ui.capabilities text nor starter prompts — "
                "author the ui block.",
            )
        return self.ok(
            params,
            CapabilitiesOutput(
                capabilities=self._ui.capabilities,
                starter_prompts=list(self._ui.starter_prompts),
            ),
            substrates_read=[],  # config, not a substrate
        )
