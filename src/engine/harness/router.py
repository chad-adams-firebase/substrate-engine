"""Router-loop mechanics: message assembly, tool execution, and the
transcript that carries results back to the router.

The loop's working messages are native tool messages: each assistant
message that requested tools carries them as tool_calls and is
followed by exactly one role="tool" message per call, in order — the
transcript invariant. The model never sees a text format it could
complete. (The Close Pass: the old rendering was prose, "Requested:
..." then "Tool results: ...", and on B2 the router completed it —
a fabricated "Tool results:" block for a call it never made, and
give_answer written as text under the same echo.) Nudges — a protocol
violation, a give_answer over untabular evidence — remain user
messages, which are valid after tool messages.

The router sees each result under an evidence index — the same index
give_answer(evidence_index=...) and the drafter's placeholders refer
to, one numbering everywhere.
"""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from engine.harness.events import EventLog
from engine.harness.state import HistoryTurn, ToolSelection
from engine.ports.types import LLMResponse, Message, ToolCall
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


def context_window(
    history: list[HistoryTurn], summary_through_turn: int
) -> list[HistoryTurn]:
    """The turns the router sees verbatim: every record newer than the
    running summary (Brief §10.3) — at least last_n_turns of them,
    by the summarize node's gate."""
    return [record for record in history if record.turn > summary_through_turn]


def build_router_messages(
    system_prompt: str,
    history: list[HistoryTurn],
    question: str,
    scratch: list[Message],
    *,
    summary: str = "",
    summary_through_turn: int = 0,
) -> list[Message]:
    """System prompt (with the running summary as a section of it, when
    there is one — never a second system role mid-list), the verbatim
    window as (user, assistant) pairs, the question, the loop's
    working messages."""
    system = system_prompt
    if summary:
        system += (
            f"\n\nConversation summary through turn {summary_through_turn} "
            "(those turns are not shown; when a figure from one is "
            "needed, gather it again with a tool this turn):\n"
            f"{summary}"
        )
    return [
        Message(role="system", content=system),
        *expand_history(history),
        Message(role="user", content=question),
        *scratch,
    ]


def with_call_ids(
    selections: list[ToolSelection], step: int
) -> list[ToolSelection]:
    """Every selection with the id its tool message will answer: the
    provider's when the response carried one, else call_{step}_{index}
    — unique within a turn, since step is the router iteration and
    scratch resets per turn."""
    return [
        selection
        if selection.call_id
        else selection.model_copy(update={"call_id": f"call_{step}_{index}"})
        for index, selection in enumerate(selections)
    ]


class SelectionResult(BaseModel):
    """What one selection came to: the invocation it ran, or — for a
    tool name the registry does not know — a note saying what exists,
    and no invocation. One result per selection, so every call gets
    exactly one tool message (the transcript invariant)."""

    model_config = ConfigDict(extra="forbid")

    selection: ToolSelection
    invocation: ToolInvocation | None = None
    note: str = ""


def execute_selections(
    registry: ToolRegistry,
    selections: list[ToolSelection],
    evidence_so_far: int,
    events: EventLog,
) -> list[SelectionResult]:
    """Run the router's selections in order. Hallucinated tool names
    become notes, not invocations — registry.invoke treats an unknown
    name as a harness bug, but here the LLM chose it, so the harness
    absorbs the mistake and tells the model what exists."""
    results: list[SelectionResult] = []
    invoked = 0
    for selection in selections:
        events.emit("tool:" + selection.name, "start", f"Running {selection.name}…")
        try:
            invocation = registry.invoke(selection.name, selection.arguments)
        except UnknownToolError:
            available = ", ".join(name.value for name in registry.names())
            results.append(
                SelectionResult(
                    selection=selection,
                    note=(
                        f"There is no tool named {selection.name!r}. "
                        f"Available tools: {available}."
                    ),
                )
            )
            events.emit(
                "tool:" + selection.name, "finish", "unknown tool — skipped"
            )
            continue
        results.append(SelectionResult(selection=selection, invocation=invocation))
        index = evidence_so_far + invoked
        invoked += 1
        detail = (
            f"evidence[{index}] ok"
            if invocation.status == "ok"
            else f"error: {invocation.error}"
        )
        events.emit("tool:" + selection.name, "finish", detail)
    return results


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
    """The router's own turn, replayed into the loop transcript as the
    tool-call message it was: its content as spoken (usually empty),
    its calls native. No prose stands in for them."""
    return Message(
        role="assistant",
        content=response.content,
        tool_calls=[
            ToolCall(id=s.call_id, name=s.name, arguments=s.arguments)
            for s in selections
        ],
    )


def tool_results(
    results: list[SelectionResult], evidence_so_far: int, max_rows: int
) -> list[Message]:
    """One role="tool" message per result, in order, each answering its
    selection's call id: the compact summary of an invocation, or the
    unknown-tool note. Evidence indices count invocations only — an
    unknown tool never joins the bundle."""
    messages: list[Message] = []
    invoked = 0
    for result in results:
        if result.invocation is None:
            content = result.note
        else:
            summary = summarize_invocation(
                result.invocation, evidence_so_far + invoked, max_rows
            )
            invoked += 1
            content = json.dumps(summary, sort_keys=True, separators=(",", ":"))
        messages.append(
            Message(
                role="tool", tool_call_id=result.selection.call_id, content=content
            )
        )
    return messages
