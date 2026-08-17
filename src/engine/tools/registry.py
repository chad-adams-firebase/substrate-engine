"""The tool registry: the pack's complete, closed capability list.

invoke() never raises for anything an LLM could cause: malformed
arguments and tool-domain failures come back as status="error"
envelopes the harness can feed back or fail closed on. The only
exceptions that escape are programming errors of the harness itself —
asking for a tool name that is not in the closed ToolName enum or was
never registered (an LLM cannot do that: it only sees to_specs()).
"""

from typing import Any

from pydantic import ValidationError

from engine.config.models import ToolName
from engine.ports.types import ToolSpec
from engine.tools.base import Tool
from engine.tools.envelope import ToolInvocation


class UnknownToolError(Exception):
    """The caller asked for a tool outside the registry — a harness
    bug, not an LLM mistake."""


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[ToolName, Tool] = {tool.name: tool for tool in tools}

    def names(self) -> list[ToolName]:
        return sorted(self._tools)

    def get(self, name: ToolName) -> Tool:
        if name not in self._tools:
            enabled = ", ".join(self.names()) or "(none)"
            raise UnknownToolError(
                f"No tool named {name!r} is registered. Enabled: {enabled}."
            )
        return self._tools[name]

    def to_specs(self) -> list[ToolSpec]:
        """The closed surface, as LLMPort tool specs."""
        return [
            ToolSpec(
                name=tool.name.value,
                description=tool.description,
                input_schema=tool.input_model.model_json_schema(),
            )
            for _, tool in sorted(self._tools.items())
        ]

    def invoke(self, name: ToolName | str, arguments: dict[str, Any]) -> ToolInvocation:
        try:
            tool_name = ToolName(name)
        except ValueError:
            raise UnknownToolError(
                f"{name!r} is not a tool name — the surface is closed (Brief §6)."
            ) from None
        tool = self.get(tool_name)
        try:
            params = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            details = "; ".join(
                f"{' -> '.join(str(part) for part in err['loc']) or '(root)'}: "
                f"{err['msg']}"
                for err in exc.errors()
            )
            return ToolInvocation(
                tool=tool.name,
                arguments=arguments,
                status="error",
                error=f"Invalid arguments — {details}",
            )
        try:
            return tool.run(params)
        except Exception as exc:
            # Domain failures are handled inside tools with better
            # messages; this catch-all keeps a future harness alive
            # through what would otherwise be a crash.
            return ToolInvocation(
                tool=tool.name,
                arguments=params.model_dump(mode="json"),
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            )
