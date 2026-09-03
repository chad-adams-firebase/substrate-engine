"""Router-loop mechanics: message assembly, tool execution, and the
prose feedback that carries results back to the router.

Feedback is prose (assistant echo + user message), the run_sql repair
loop's precedent: we never emit role="tool" messages, so the port's
Message type needs no tool-call ids. The router sees each result under
an evidence index — the same index give_answer(evidence_index=...)
and the drafter's placeholders refer to, one numbering everywhere.
"""

import json
from typing import Any

from engine.harness.events import EventLog
from engine.harness.state import HistoryTurn, ToolSelection
from engine.ports.types import LLMResponse, Message
from engine.tools.envelope import ToolInvocation
from engine.tools.registry import ToolRegistry, UnknownToolError


def expand_history(history: list[HistoryTurn]) -> list[Message]:
    """The records back into the (user, assistant) pairs the router has
    always seen — the same messages the pre-Block-4 history held."""
    messages: list[Message] = []
    for record in history:
        messages.append(Message(role="user", content=record.question))
        messages.append(Message(role="assistant", content=record.answer))
    return messages


def build_router_messages(
    system_prompt: str,
    history: list[HistoryTurn],
    question: str,
    scratch: list[Message],
) -> list[Message]:
    return [
        Message(role="system", content=system_prompt),
        *expand_history(history),
        Message(role="user", content=question),
        *scratch,
    ]


def execute_selections(
    registry: ToolRegistry,
    selections: list[ToolSelection],
    evidence_so_far: int,
    events: EventLog,
) -> tuple[list[ToolInvocation], list[str]]:
    """Run the router's selections in order. Hallucinated tool names
    become feedback lines, not invocations — registry.invoke treats an
    unknown name as a harness bug, but here the LLM chose it, so the
    harness absorbs the mistake and tells the model what exists."""
    invocations: list[ToolInvocation] = []
    unknown: list[str] = []
    for selection in selections:
        events.emit("tool:" + selection.name, "start", f"Running {selection.name}…")
        try:
            invocation = registry.invoke(selection.name, selection.arguments)
        except UnknownToolError:
            available = ", ".join(name.value for name in registry.names())
            unknown.append(
                f"There is no tool named {selection.name!r}. "
                f"Available tools: {available}."
            )
            events.emit(
                "tool:" + selection.name, "finish", "unknown tool — skipped"
            )
            continue
        invocations.append(invocation)
        index = evidence_so_far + len(invocations) - 1
        detail = (
            f"evidence[{index}] ok"
            if invocation.status == "ok"
            else f"error: {invocation.error}"
        )
        events.emit("tool:" + selection.name, "finish", detail)
    return invocations, unknown


def summarize_invocation(
    invocation: ToolInvocation, evidence_index: int, max_rows: int
) -> dict[str, Any]:
    """The compact, router-facing view of one result: output only —
    never the evidence residue — with big tables truncated visibly."""
    summary: dict[str, Any] = {
        "evidence_index": evidence_index,
        "tool": invocation.tool.value,
        "status": invocation.status,
    }
    if invocation.error is not None:
        summary["error"] = invocation.error
    if invocation.output is not None:
        output = invocation.output.model_dump(mode="json")
        table = output.get("table")
        if isinstance(table, dict) and len(table.get("rows", [])) > max_rows:
            shown = table["rows"][:max_rows]
            table["rows"] = shown
            table["note"] = (
                f"showing {len(shown)} of {table['total_row_count']} rows"
            )
        summary["output"] = output
    return summary


def assistant_echo(response: LLMResponse, selections: list[ToolSelection]) -> Message:
    """The router's own turn, replayed into the loop transcript. Tool-
    calling responses usually have empty content, so synthesize one."""
    if response.content.strip():
        return Message(role="assistant", content=response.content)
    requested = "; ".join(
        f"{s.name}({json.dumps(s.arguments, sort_keys=True)})" for s in selections
    )
    return Message(role="assistant", content=f"Requested: {requested}")


def results_message(summaries: list[dict[str, Any]], notes: list[str]) -> Message:
    lines = ["Tool results:"]
    lines.extend(
        json.dumps(summary, sort_keys=True, separators=(",", ":"))
        for summary in summaries
    )
    lines.extend(notes)
    return Message(role="user", content="\n".join(lines))
