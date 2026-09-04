"""The tool plugin shape (Brief §6): name, description, input_schema,
run(). Tools consume ports and substrate models only — never adapters
— and are constructed once, with exactly the ports they need.

run() never raises for domain failures: a bad entry name, a missing
substrate file, an exhausted repair loop all come back as
status="error" envelopes a harness can feed back to the LLM or fail
closed on. Raising is reserved for programming errors.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from engine.config.models import SubstrateName, ToolName
from engine.tools.envelope import ToolEvidence, ToolInvocation, ToolOutput, TurnContext


class Tool(ABC):
    name: ClassVar[ToolName]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    @abstractmethod
    def run(self, params: BaseModel) -> ToolInvocation: ...

    def run_in_context(
        self, params: BaseModel, context: TurnContext | None
    ) -> ToolInvocation:
        """run(), with what the harness knows of the conversation
        (Backlog Pass). The default ignores it: only a tool that
        grounds on prior turns — run_sql — overrides this."""
        return self.run(params)

    def ok(
        self,
        params: BaseModel,
        output: ToolOutput,
        *,
        evidence: ToolEvidence | None = None,
        substrates_read: list[SubstrateName],
        manifest_ids: list[str] = [],
    ) -> ToolInvocation:
        return ToolInvocation(
            tool=self.name,
            arguments=params.model_dump(mode="json"),
            status="ok",
            output=output,
            evidence=evidence,
            substrates_read=substrates_read,
            manifest_ids=manifest_ids,
        )

    def fail(
        self,
        arguments: dict[str, Any] | BaseModel,
        message: str,
        *,
        evidence: ToolEvidence | None = None,
        substrates_read: list[SubstrateName] = [],
    ) -> ToolInvocation:
        if isinstance(arguments, BaseModel):
            arguments = arguments.model_dump(mode="json")
        return ToolInvocation(
            tool=self.name,
            arguments=arguments,
            status="error",
            error=message,
            evidence=evidence,
            substrates_read=substrates_read,
        )


def manifest_ids_of(rows: list) -> list[str]:
    """Distinct manifest ids of the rows a tool consulted — sorted so
    the envelope stays byte-stable."""
    return sorted(
        {
            row.provenance.manifest_id
            for row in rows
            if row.provenance.manifest_id is not None
        }
    )
