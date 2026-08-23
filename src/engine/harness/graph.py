"""The LangGraph graph (Brief §8): a single well-routed agent.

    begin -> route --(tools)--> act -> route          (bounded cycle)
                |--(answer)--> draft -> verify --(retry)--> draft
                |                          '--> finalize -> END
                '--(refuse|clarify|escalate|cap)-----> finalize -> END

Every bound the loop enforces comes from pack config (HarnessSettings,
VerifierSettings); the cap converts to a first-class refuse outcome.
The Verifier node sits on the path to EVERY answer — table envelopes
included. No bypasses.

GraphDeps carries the per-session collaborators; deps.events is
swapped per turn by AskSession (an in-process callback does not
belong in checkpointed state).
"""

import re

from langgraph.graph import END, START, StateGraph

from engine.config.models import HarnessSettings
from engine.harness.control import (
    RouteProtocolViolation,
    control_specs,
    parse_route,
)
from engine.harness.drafter import Drafter
from engine.harness.events import EventLog
from engine.harness.outcomes import (
    AnswerOutcome,
    ClarifyOutcome,
    EscalateOutcome,
    MarkdownAnswer,
    RefuseOutcome,
    TableAnswer,
)
from engine.harness.router import (
    assistant_echo,
    build_router_messages,
    execute_selections,
    results_message,
    summarize_invocation,
)
from engine.harness.placeholders import referenced_indices
from engine.harness.state import RouteDecision, TurnState
from engine.harness.tables import caption_for, project_table
from engine.harness.verifier_protocol import VerifierProtocol
from engine.ports.llm import LLMPort
from engine.ports.types import Message
from engine.tools.registry import ToolRegistry
from engine.verifier.models import DraftAnswer
from engine.verifier.verdict import finalize as finalize_verdict
from engine.verifier.verdict import render_feedback


class GraphDeps:
    def __init__(
        self,
        *,
        llm: LLMPort,
        registry: ToolRegistry,
        verifier: VerifierProtocol,
        drafter: Drafter,
        settings: HarnessSettings,
        router_prompt: str,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.verifier = verifier
        self.drafter = drafter
        self.settings = settings
        self.router_prompt = router_prompt
        self.events: EventLog = EventLog()


# A prose answer that asserts the evidence cannot answer, and grounds
# no claim of its own, is a refusal wearing an answer's exit code —
# the worst shape in the taxonomy (Addendum N7). Generic-English
# lexicon, engine-owned like the claim extractor's; promote to pack
# config only if a pack ever needs its own phrasing.
_INSUFFICIENCY = re.compile(
    r"(?:evidence|data|results?|tools?)\s+(?:do(?:es)?\s+not|don't|doesn't|"
    r"cannot|can't)\s+(?:provide|contain|show|support|include|answer|"
    r"indicate|specify)"
    r"|no\s+(?:evidence|data|information|record)\b"
    r"|insufficient\s+(?:evidence|data|information)"
    r"|(?:cannot|can't|unable\s+to)\s+(?:determine|answer|be\s+determined|"
    r"be\s+answered)"
    r"|not\s+(?:available|present|found)\s+in\s+the\s+(?:evidence|data|"
    r"results?)",
    re.IGNORECASE,
)


def _fallback_table(evidence, failures):
    """The evidence to ship as a table when placeholder resolution is
    exhausted: the invocation the failed placeholders cite (most
    referenced first, ties to the latest), else the latest ok
    table-shaped invocation, else None (the refusal stands)."""
    counts: dict[int, int] = {}
    for index in referenced_indices(failures):
        counts[index] = counts.get(index, 0) + 1
    cited = sorted(counts, key=lambda index: (counts[index], index), reverse=True)
    uncited = [
        index for index in range(len(evidence) - 1, -1, -1) if index not in counts
    ]
    for index in cited + uncited:
        if not 0 <= index < len(evidence):
            continue
        invocation = evidence[index]
        if invocation.status != "ok" or invocation.output is None:
            continue
        table = project_table(invocation.output)
        if table is not None:
            return index, table, caption_for(invocation.output)
    return None


def build_graph(deps: GraphDeps, checkpointer=None):
    def begin(state: TurnState) -> dict:
        return {
            "turn": state.turn + 1,
            "scratch": [],
            "evidence": [],
            "iterations": 0,
            "decision": None,
            "draft": None,
            "draft_raw": None,
            "injected_spans": [],
            "draft_feedback": [],
            "draft_attempts": 0,
            "verify_attempt": 0,
            "verifier_attempts": [],
            "verifier_plausibility": [],
            "judge_calls_total": 0,
            "verdict": None,
            "outcome": None,
        }

    def route(state: TurnState) -> dict:
        step = state.iterations + 1
        deps.events.emit("route", "start", f"Consulting router (step {step})…")
        if state.iterations >= deps.settings.max_router_iterations:
            deps.events.emit(
                "route", "finish", "tool budget exhausted — refusing"
            )
            return {
                "decision": RouteDecision(
                    kind="refuse",
                    reason=(
                        "Could not assemble sufficient evidence within "
                        f"the {deps.settings.max_router_iterations}-step "
                        "tool budget."
                    ),
                    what_would_work="A narrower or more specific question.",
                )
            }

        messages = build_router_messages(
            deps.router_prompt, state.history, state.question, state.scratch
        )
        specs = deps.registry.to_specs() + control_specs()
        response = deps.llm.complete(messages, tools=specs, temperature=0.0)
        try:
            decision = parse_route(response)
        except RouteProtocolViolation as violation:
            deps.events.emit("route", "finish", "protocol violation — nudging")
            return {
                "iterations": state.iterations + 1,
                "scratch": state.scratch
                + [Message(role="user", content=str(violation))],
            }

        deps.events.emit("route", "finish", f"decision: {decision.kind}")
        update: dict = {
            "decision": decision,
            "iterations": state.iterations + 1,
        }
        if decision.kind == "tools":
            update["scratch"] = state.scratch + [
                assistant_echo(response, decision.selections)
            ]
        return update

    def act(state: TurnState) -> dict:
        invocations, unknown = execute_selections(
            deps.registry,
            state.decision.selections,
            evidence_so_far=len(state.evidence),
            events=deps.events,
        )
        summaries = [
            summarize_invocation(
                invocation,
                evidence_index=len(state.evidence) + offset,
                max_rows=deps.settings.max_rows_in_context,
            )
            for offset, invocation in enumerate(invocations)
        ]
        return {
            "evidence": state.evidence + invocations,
            "scratch": state.scratch + [results_message(summaries, unknown)],
            "decision": None,
        }

    def draft(state: TurnState) -> dict:
        deps.events.emit("draft", "start", "Drafting answer…")
        if state.decision.answer_shape == "table":
            return _draft_table(state)

        result = deps.drafter.draft(
            state.question,
            state.evidence,
            previous_draft=state.draft_raw,
            feedback=state.draft_feedback or None,
        )
        if result.resolution.failures:
            attempts = state.draft_attempts + 1
            failed = ", ".join(result.resolution.failures)
            if attempts > deps.settings.max_draft_retries:
                # §6: when the answer's substance is a result set the
                # store already returned, deliver it as a table
                # envelope instead of refusing — deterministically,
                # and still through the Verifier (table_passthrough).
                fallback = _fallback_table(
                    state.evidence, result.resolution.failures
                )
                if fallback is not None:
                    index, table, caption = fallback
                    deps.events.emit(
                        "draft",
                        "finish",
                        f"placeholder resolution exhausted; failed: {failed}"
                        f" — returning evidence e{index} as a table",
                    )
                    return {
                        "draft": TableAnswer(table=table, caption=caption),
                        "draft_feedback": [],
                    }
                deps.events.emit(
                    "draft",
                    "finish",
                    f"placeholder budget exhausted; failed: {failed} — no "
                    "table-shaped evidence; refusing",
                )
                return {
                    "outcome": RefuseOutcome(
                        reason=(
                            "Could not produce a faithful draft: evidence "
                            "references failed to resolve after "
                            f"{attempts} attempt(s)."
                        )
                    )
                }
            deps.events.emit(
                "draft",
                "finish",
                f"placeholder(s) failed: {failed} — retrying "
                f"({attempts}/{deps.settings.max_draft_retries})",
            )
            return {
                "draft_raw": result.raw,
                "draft_attempts": attempts,
                "draft_feedback": [
                    f"The placeholder {failure} did not resolve against "
                    "the evidence — use an index and path that exist."
                    for failure in result.resolution.failures
                ],
            }
        deps.events.emit("draft", "finish", "draft ready")
        return {
            "draft": MarkdownAnswer(text=result.resolution.text),
            "draft_raw": result.raw,
            "injected_spans": result.resolution.injected_spans,
            "draft_feedback": [],
        }

    def _draft_table(state: TurnState) -> dict:
        index = state.decision.evidence_index
        output = (
            state.evidence[index].output
            if index is not None and 0 <= index < len(state.evidence)
            else None
        )
        table = project_table(output) if output is not None else None
        if table is None:
            deps.events.emit(
                "draft", "finish", "selected evidence is not table-shaped"
            )
            return {
                "decision": None,
                "iterations": state.iterations + 1,
                "scratch": state.scratch
                + [
                    Message(
                        role="user",
                        content=(
                            f"give_answer(shape='table', evidence_index="
                            f"{index}) failed: that result is not "
                            "table-shaped. Choose a run_sql, stats, or "
                            "dictionary result, or answer in prose."
                        ),
                    )
                ],
            }
        deps.events.emit("draft", "finish", "table envelope ready")
        return {
            "draft": TableAnswer(table=table, caption=caption_for(output))
        }

    def verify(state: TurnState) -> dict:
        deps.events.emit("verify", "start", "Verifying against evidence…")
        if state.draft.kind == "table":
            draft_answer = DraftAnswer(
                kind="table_passthrough", text=state.draft.caption
            )
        else:
            draft_answer = DraftAnswer(
                kind="prose",
                text=state.draft.text,
                injected_spans=state.injected_spans,
            )
        attempt = state.verify_attempt + 1
        result = deps.verifier.verify(
            question=state.question,
            draft=draft_answer,
            evidence=state.evidence,
            attempt=attempt,
        )
        attempts = state.verifier_attempts + [result.attempt_record]
        judge_calls = state.judge_calls_total + result.judge_calls
        base = {
            "verify_attempt": attempt,
            "verifier_attempts": attempts,
            "verifier_plausibility": result.plausibility,
            "judge_calls_total": judge_calls,
        }

        if result.disposition == "retry":
            deps.events.emit(
                "verify",
                "finish",
                f"{result.attempt_record.unmatched_count} claim(s) "
                "unsupported — redrafting",
            )
            return base | {
                "draft": None,
                "draft_feedback": render_feedback(result.feedback),
            }

        mode = (
            "table_passthrough" if state.draft.kind == "table" else "prose"
        )
        verdict = finalize_verdict(
            attempts=attempts,
            plausibility=result.plausibility,
            mode=mode,
            judge_calls=judge_calls,
            disposition=result.disposition,
        )
        deps.events.emit("verify", "finish", f"verdict: {verdict.disposition}")
        if result.disposition == "refused":
            outcome = RefuseOutcome(
                reason=verdict.reason or "The answer failed verification.",
                what_would_work=(
                    "Rephrasing the question, or asking for the underlying "
                    "data directly."
                ),
            )
        elif (
            state.draft.kind == "markdown"
            and not result.attempt_record.claims
            and _INSUFFICIENCY.search(state.draft.text)
        ):
            # Addendum N7: zero claims means verification was vacuous;
            # insufficiency prose means the draft itself says so. Ship
            # it as the refusal it is — never exit 0. (Clarify needs a
            # question to ask, which no deterministic rule can mint;
            # the router's clarify verb remains that path.)
            deps.events.emit(
                "verify", "finish", "verified but content-free — refusing"
            )
            outcome = RefuseOutcome(
                reason=state.draft.text,
                what_would_work=(
                    "Asking about data or code the connected substrates "
                    "cover, or naming the specific record to look up."
                ),
            )
        else:
            outcome = AnswerOutcome(
                body=state.draft, verification=result.disposition
            )
        return base | {"verdict": verdict, "outcome": outcome}

    def finalize(state: TurnState) -> dict:
        outcome = state.outcome
        if outcome is None:
            decision = state.decision
            if decision is not None and decision.kind == "refuse":
                outcome = RefuseOutcome(
                    reason=decision.reason,
                    what_would_work=decision.what_would_work,
                )
            elif decision is not None and decision.kind == "clarify":
                outcome = ClarifyOutcome(question=decision.question)
            elif decision is not None and decision.kind == "escalate":
                outcome = EscalateOutcome(reason=decision.reason)
            else:  # unreachable by construction; refuse beats crashing
                outcome = RefuseOutcome(reason="No outcome was produced.")
        deps.events.emit("finalize", "finish", outcome.kind)
        return {
            "outcome": outcome,
            "history": state.history
            + [
                Message(role="user", content=state.question),
                Message(role="assistant", content=_transcript_text(outcome)),
            ],
        }

    def after_route(state: TurnState) -> str:
        if state.decision is None:
            return "route"  # protocol violation nudge; cap ends the loop
        if state.decision.kind == "tools":
            return "act"
        if state.decision.kind == "answer":
            return "draft"
        return "finalize"

    def after_draft(state: TurnState) -> str:
        if state.outcome is not None:
            return "finalize"
        if state.draft is not None:
            return "verify"
        if state.decision is None:
            return "route"  # table-shape failure fed back
        return "draft"  # placeholder retry

    def after_verify(state: TurnState) -> str:
        return "finalize" if state.outcome is not None else "draft"

    builder = StateGraph(TurnState)
    builder.add_node("begin", begin)
    builder.add_node("route", route)
    builder.add_node("act", act)
    builder.add_node("draft", draft)
    builder.add_node("verify", verify)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "begin")
    builder.add_edge("begin", "route")
    builder.add_conditional_edges(
        "route",
        after_route,
        {"route": "route", "act": "act", "draft": "draft", "finalize": "finalize"},
    )
    builder.add_edge("act", "route")
    builder.add_conditional_edges(
        "draft",
        after_draft,
        {
            "finalize": "finalize",
            "verify": "verify",
            "route": "route",
            "draft": "draft",
        },
    )
    builder.add_conditional_edges(
        "verify", after_verify, {"finalize": "finalize", "draft": "draft"}
    )
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def _transcript_text(outcome) -> str:
    if isinstance(outcome, AnswerOutcome):
        if isinstance(outcome.body, TableAnswer):
            return f"[table: {outcome.body.caption or 'result set'}]"
        return outcome.body.text
    if isinstance(outcome, RefuseOutcome):
        return f"[refused: {outcome.reason}]"
    if isinstance(outcome, ClarifyOutcome):
        return f"[clarify: {outcome.question}]"
    return f"[escalated: {outcome.reason}]"
