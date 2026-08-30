"""The drafting node's mechanics (Brief §9.4).

Prose drafting: temperature 0, from this turn's evidence only — the
outputs, never the evidence residue (failed SQL, prompts, raw log
lines stay invisible to the model by construction). The draft carries
placeholders; resolve_placeholders injects the figures in code.

Table pass-through drafting happens in tables.py; this module is the
prose path.
"""

import json

from pydantic import BaseModel, ConfigDict

from engine.harness.placeholders import Resolution, resolve_placeholders
from engine.harness.prompts import render_draft_feedback
from engine.ports.llm import LLMPort
from engine.ports.types import Message
from engine.tools.envelope import ToolInvocation


class DraftResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw: str  # as the model wrote it, placeholders intact
    resolution: Resolution


def render_evidence(evidence: list[ToolInvocation]) -> str:
    """The drafter-facing evidence: one indexed JSON line per
    invocation, outputs whole, residue never. Failed calls render
    collapsed — index, tool, status, and a fixed note, never the
    error text: the verifier harvests nothing from a non-ok
    invocation, so every token echoed from one is guaranteed
    unmatched. The router already saw the error in its own loop."""
    lines = []
    for index, invocation in enumerate(evidence):
        entry: dict = {
            "index": index,
            "tool": invocation.tool.value,
            "status": invocation.status,
        }
        if invocation.status != "ok":
            entry["note"] = "call failed; supports no citations or placeholders"
        elif invocation.output is not None:
            entry["output"] = invocation.output.model_dump(mode="json")
        lines.append(json.dumps(entry, sort_keys=True, separators=(",", ":")))
    return "\n".join(lines)


def build_drafting_messages(
    system_prompt: str,
    question: str,
    evidence: list[ToolInvocation],
    previous_draft: str | None = None,
    feedback: list[str] | None = None,
) -> list[Message]:
    messages = [
        Message(role="system", content=system_prompt),
        Message(
            role="user",
            content=(
                f"Question: {question}\n\n"
                "Evidence (reference values with {{e<index>.<path>}} "
                "placeholders):\n" + render_evidence(evidence)
            ),
        ),
    ]
    if previous_draft is not None and feedback:
        messages.append(Message(role="assistant", content=previous_draft))
        messages.append(
            Message(role="user", content=render_draft_feedback(feedback))
        )
    return messages


class Drafter:
    def __init__(self, llm: LLMPort, system_prompt: str) -> None:
        self._llm = llm
        self._system_prompt = system_prompt

    def draft(
        self,
        question: str,
        evidence: list[ToolInvocation],
        previous_draft: str | None = None,
        feedback: list[str] | None = None,
    ) -> DraftResult:
        messages = build_drafting_messages(
            self._system_prompt, question, evidence, previous_draft, feedback
        )
        response = self._llm.complete(messages, temperature=0.0)
        resolution = resolve_placeholders(response.content, evidence)
        return DraftResult(raw=response.content, resolution=resolution)
