"""The LangGraph graph (Brief §8): a single well-routed agent.

    begin -> route --(tools)--> act -> route          (bounded cycle)
                |--(answer)--> draft -> verify --(retry)--> draft
                |                          '--> finalize -> summarize -> END
                '--(refuse|clarify|escalate|cap)-----> finalize -'

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

from engine.config.models import ContextSettings, HarnessSettings
from engine.harness.control import (
    RouteProtocolViolation,
    control_specs,
    parse_route,
    control_verb,
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
from engine.harness.prompts import render_summary_feedback
from engine.harness.router import (
    assistant_echo,
    build_router_messages,
    context_window,
    execute_selections,
    tool_results,
    with_call_ids,
)
from engine.harness.placeholders import referenced_indices
from engine.harness.state import (
    HistoryTurn,
    RouteDecision,
    TurnState,
    kind_of_outcome,
    transcript_text,
    upgrade_history,
)
from engine.harness.summary import (
    build_summary_messages,
    scrub_figures,
    summary_problems,
)
from engine.harness.tables import caption_for, declared_readings, project_table
from engine.harness.verifier_protocol import VerifierProtocol
from engine.ports.llm import LLMPort
from engine.ports.types import Message
from engine.tools.entities import EntityCatalog, harvest_turn_anchors
from engine.tools.envelope import TurnAnchors, TurnContext
from engine.tools.registry import ToolRegistry
from engine.verifier.anchor import CHECK as ANCHOR_CHECK
from engine.verifier.anchor import referent_kind
from engine.verifier.models import DraftAnswer, VerifyContext
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
        summarizer_prompt: str,
        catalog: EntityCatalog | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.verifier = verifier
        self.drafter = drafter
        self.settings = settings
        # The pack's entity kinds (Backlog Pass): what a turn's evidence
        # establishes is harvested with it at finalize, and what the
        # conversation has shown reaches run_sql through it. None means
        # the pack declares none, and every consumer is silent.
        self.catalog = catalog
        self.router_prompt = router_prompt
        self.summarizer_prompt = summarizer_prompt
        self.events: EventLog = EventLog()
        # The context window and summary cadence in force for the
        # current turn — the pack's, unless the caller of ask() names
        # another (the eval bank's per-row override). Swapped per turn
        # by AskSession under its lock, like events.
        self.context: ContextSettings = settings.context


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


def turn_context(state: TurnState, catalog: EntityCatalog | None) -> TurnContext:
    """What the conversation has put in front of the model, for the
    tools of this turn (Backlog Pass): the user's own words — every
    history question, this question, the running summary — the most
    recent turn that established an entity, and every key seen so far,
    this turn's earlier evidence included."""
    texts = [record.question for record in state.history] + [state.question]
    if state.summary:
        texts.append(state.summary)
    anchors: list = []
    anchors_turn = 0
    for record in reversed(state.history):
        keyed = [a for a in record.anchors.entities if a.column]
        if keyed:
            anchors, anchors_turn = keyed, record.anchors.turn or record.turn
            break
    known = [key for record in state.history for key in record.anchors.keys]
    if catalog is not None and state.evidence:
        known += harvest_turn_anchors(state.evidence, catalog).keys
    return TurnContext(
        texts=texts, anchors=anchors, anchors_turn=anchors_turn, known_keys=known
    )


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
                "decision": RouteDecision(kind="refuse"),
                "outcome": RefuseOutcome(
                    reason=(
                        "I couldn't gather enough evidence to answer this "
                        "reliably, so I'm not guessing."
                    ),
                    what_would_work=(
                        "A narrower question — one table, one component, "
                        "or one date range at a time."
                    ),
                    detail=(
                        "tool budget exhausted: "
                        f"{deps.settings.max_router_iterations} router "
                        "steps without a terminal decision"
                    ),
                ),
            }

        messages = build_router_messages(
            deps.router_prompt,
            context_window(state.history, state.summary_through_turn),
            state.question,
            state.scratch,
            summary=state.summary,
            summary_through_turn=state.summary_through_turn,
        )
        specs = deps.registry.to_specs() + control_specs()
        response = deps.llm.complete(messages, tools=specs, temperature=0.0)
        try:
            decision = parse_route(response)
        except RouteProtocolViolation as violation:
            deps.events.emit(
                "route",
                "finish",
                "protocol violation — nudging",
                raw_response=violation.raw_response or None,
            )
            return {
                "iterations": state.iterations + 1,
                "scratch": state.scratch
                + [Message(role="user", content=str(violation))],
            }

        if decision.parsed_from_text:
            # The right call in the wrong channel, tolerated — and said
            # so in provenance (raw_response keeps what was written),
            # never on the live trail line.
            deps.events.emit(
                "route",
                "finish",
                f"text-form {control_verb(decision)} parsed as the call",
                raw_response=response.content or None,
            )
        deps.events.emit("route", "finish", f"decision: {decision.kind}")
        if decision.kind == "tools":
            # Every call gets the id its tool message will answer — the
            # provider's, or a synthesized one when the response
            # carried none (stubs) — before the echo replays it.
            decision = decision.model_copy(
                update={
                    "selections": with_call_ids(
                        decision.selections, state.iterations + 1
                    )
                }
            )
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
        results = execute_selections(
            deps.registry,
            state.decision.selections,
            evidence_so_far=len(state.evidence),
            events=deps.events,
            context=turn_context(state, deps.catalog),
        )
        invocations = [r.invocation for r in results if r.invocation is not None]
        # The transcript invariant: route's assistant tool-call message
        # is followed by exactly one role="tool" message per call, in
        # order. Nudges — the protocol violation in route, the untabular
        # give_answer in _draft_table — stay role="user", which is valid
        # after tool messages.
        return {
            "evidence": state.evidence + invocations,
            "scratch": state.scratch
            + tool_results(
                results, len(state.evidence), deps.settings.max_rows_in_context
            ),
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
        resolution = result.resolution
        if resolution.failures or resolution.misplaced:
            attempts = state.draft_attempts + 1
            failed = ", ".join(resolution.failures)
            if attempts > deps.settings.max_draft_retries and not resolution.failures:
                # Only passages sit mid-sentence: every placeholder
                # resolves. A lumpy seam must not cost the answer, so
                # the passages ship as written — evented, and still
                # verified like any prose.
                resolution = deps.drafter.resolve(
                    result.raw, state.evidence, allow_passages_inline=True
                )
                deps.events.emit(
                    "draft",
                    "finish",
                    "passage placeholder(s) still mid-sentence after "
                    f"{deps.settings.max_draft_retries} retries: "
                    f"{', '.join(result.resolution.misplaced)} — shipping "
                    "as written",
                )
                return {
                    "draft": MarkdownAnswer(text=resolution.text, about=state.decision.about or ""),
                    "draft_raw": result.raw,
                    "injected_spans": resolution.injected_spans,
                    "draft_feedback": [],
                }
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
                        "draft": TableAnswer(
                            table=table, caption=caption, about=state.decision.about or ""
                        ),
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
                            "I found evidence for this but couldn't write "
                            "it up faithfully, so I'm not showing it."
                        ),
                        what_would_work=(
                            "Asking for the underlying data directly, or "
                            "a narrower question."
                        ),
                        detail=(
                            "placeholder resolution failed after "
                            f"{attempts} attempt(s): {failed}"
                        ),
                    )
                }
            problems = ", ".join(resolution.failures + resolution.misplaced)
            deps.events.emit(
                "draft",
                "finish",
                f"placeholder(s) failed: {problems} — retrying "
                f"({attempts}/{deps.settings.max_draft_retries})",
            )
            return {
                "draft_raw": result.raw,
                "draft_attempts": attempts,
                "draft_feedback": [
                    (
                        f"The placeholder {failure} paths into a text "
                        "passage — the field it reaches into is text, not "
                        "a structure. Quote the passage in a fenced code "
                        "block and read the value from it; placeholders "
                        "never reach inside text."
                        if failure in resolution.pathed_into_text
                        else f"The placeholder {failure} did not resolve "
                        "against the evidence — use an index and path "
                        "that exist."
                    )
                    for failure in resolution.failures
                ]
                + [
                    f"The placeholder {surface} resolves to a passage, not "
                    "a value, and sits inside a sentence. Placeholders "
                    "inject values; a passage is quoted on its own line "
                    "inside a fenced code block when it is code, and "
                    "otherwise said in your own words — never pasted "
                    "mid-sentence."
                    for surface in resolution.misplaced
                ],
            }
        deps.events.emit("draft", "finish", "draft ready")
        return {
            "draft": MarkdownAnswer(text=resolution.text, about=state.decision.about or ""),
            "draft_raw": result.raw,
            "injected_spans": resolution.injected_spans,
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
        # The reading (Close Pass): validated against what the result
        # declares — an undeclared name is a protocol violation and is
        # nudged with the valid set; a missing one is accepted without a
        # reading, because phrase matching over-reaches (a count table
        # under a metric whose readings are money) and a forced reading
        # would be a wrong sentence on a right table.
        declared = declared_readings(output)
        reading = state.decision.reading
        if reading is not None and declared and reading not in declared:
            deps.events.emit(
                "draft", "finish", "protocol violation — reading not declared — nudging"
            )
            listed = ", ".join(f"'{name}'" for name in declared)
            return {
                "decision": None,
                "iterations": state.iterations + 1,
                "scratch": state.scratch
                + [
                    Message(
                        role="user",
                        content=(
                            f"give_answer(shape='table', evidence_index="
                            f"{index}) failed: reading '{reading}' is not one "
                            f"this result lists. Name one of {listed} — the "
                            "reading that result's SQL computed — or omit it."
                        ),
                    )
                ],
            }
        deps.events.emit("draft", "finish", "table envelope ready")
        return {
            "draft": TableAnswer(
                table=table,
                caption=caption_for(output),
                reading=reading if reading is not None and declared else "",
                about=state.decision.about or "",
            )
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
            # The full history, not the router's window: an anchor a
            # folded turn established is still the anchor.
            context=VerifyContext(
                prior=[record.anchors for record in state.history],
                about=state.draft.about or None,
            ),
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
            outcome = _verifier_refusal(verdict)
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
                detail=(
                    "verified but content-free: zero claims and "
                    "insufficiency prose (Addendum N7)"
                ),
            )
        else:
            body = state.draft
            if result.about_default and not body.about:
                # The About the router did not declare (Rider Pass): the
                # anchor check confirmed this answer is about the prior
                # anchor — a filter on its key, or its name in the prose
                # — so the engine writes what the check read, exactly
                # where a declaration would sit; finalize keeps it as
                # one. Only on this branch: a warned turn carries no
                # default, a refusal wears no About, and a redraft is
                # verified again before anything is written.
                deps.events.emit(
                    "verify",
                    "finish",
                    f"about defaulted to `{result.about_default}` "
                    "— the anchor check confirmed it",
                )
                body = body.model_copy(update={"about": result.about_default})
            outcome = AnswerOutcome(body=body, verification=result.disposition)
            return base | {"verdict": verdict, "outcome": outcome, "draft": body}
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
                outcome = RefuseOutcome(
                    reason="No answer could be produced for this question.",
                    detail="finalize reached with neither a decision nor an outcome",
                )
        deps.events.emit("finalize", "finish", outcome.kind)
        # What this turn established (Backlog Pass): harvested from the
        # evidence with the pack's entity kinds, the About beside it —
        # the router's declaration, or the one verify wrote from the
        # anchor check's confirmation (Rider Pass), read alike — and
        # kept on the history for the transcript,
        # the next turn's grounding, and the Verifier's anchor check.
        # A warned turn establishes nothing (Fix Pass): MT-ANCHOR rep 4
        # warned at turn 2 and the drift still became turn 3's anchor,
        # because the harvest read the evidence unconditionally. The
        # verdict's anchor finding empties the entities and rides into
        # the transcript instead; a non-answer establishes nothing too.
        about = outcome.body.about if isinstance(outcome, AnswerOutcome) else None
        warned = next(
            (record for record in state.verifier_plausibility if record.check == ANCHOR_CHECK),
            None,
        )
        anchors = TurnAnchors(turn=state.turn)
        if deps.catalog is not None:
            kind = referent_kind(
                state.question, [record.anchors for record in state.history], deps.catalog
            )
            anchors = harvest_turn_anchors(
                state.evidence,
                deps.catalog,
                about=about or None,
                question_kind=kind,
                turn=state.turn,
                answered=isinstance(outcome, AnswerOutcome),
                contradiction=(kind or "", warned.detail) if warned is not None else None,
            )
        return {
            "outcome": outcome,
            "history": state.history
            + [
                HistoryTurn(
                    turn=state.turn,
                    question=state.question,
                    answer=transcript_text(outcome, anchors),
                    kind=kind_of_outcome(outcome),
                    anchors=anchors,
                )
            ],
        }

    def summarize(state: TurnState) -> dict:
        """Fold the turns that have fallen past the verbatim window into
        the running summary (Brief §10.3) — once enough of them have,
        so the LLM call is every summary_refresh_after_turns turns, not
        every turn. Any failure keeps the previous summary: this node
        runs after the outcome exists and can never cost the answer."""
        context = deps.context
        fold_through = state.turn - context.last_n_turns
        if (
            fold_through - state.summary_through_turn
            < context.summary_refresh_after_turns
        ):
            return {}
        records = [
            record
            for record in state.history
            if state.summary_through_turn < record.turn <= fold_through
        ]
        if not records:  # every turn in the range raised before finalize
            return {"summary_through_turn": fold_through}
        deps.events.emit("summarize", "start", "Updating conversation summary…")
        try:
            messages = build_summary_messages(
                deps.summarizer_prompt, state.summary, records, fold_through
            )
            reply = deps.llm.complete(messages, temperature=0.0).content.strip()
            if not reply:
                raise ValueError("empty reply")
            problems = summary_problems(reply, records, fold_through)
            if problems.any():
                # One regeneration with the rule named, then the scrub
                # settles whatever is left.
                messages = messages + [
                    Message(role="assistant", content=reply),
                    Message(
                        role="user",
                        content=render_summary_feedback(
                            problems.figures, problems.bad_refs, fold_through
                        ),
                    ),
                ]
                retry = deps.llm.complete(messages, temperature=0.0).content.strip()
                reply = retry or reply
            summary, scrubbed = scrub_figures(reply, records, fold_through)
        except Exception as exc:  # noqa: BLE001 - never sink a finished turn
            deps.events.emit(
                "summarize",
                "finish",
                f"summary refresh failed: {type(exc).__name__}: {exc} — "
                "previous summary kept",
            )
            return {}
        detail = f"summary updated through turn {fold_through}"
        if scrubbed:
            detail += f"; {scrubbed} scrubbed"
        deps.events.emit("summarize", "finish", detail)
        return {"summary": summary, "summary_through_turn": fold_through}

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
    builder.add_node("summarize", summarize)
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
    builder.add_edge("finalize", "summarize")
    builder.add_edge("summarize", END)
    return builder.compile(checkpointer=checkpointer)


def _verifier_refusal(verdict) -> RefuseOutcome:
    """The Verifier's refusal, said for the person who asked. The
    verdict's reason is the engineer's diagnosis (the check name, the
    numbers, the tolerance) and rides in detail; the card gets the
    plain version of which gate closed."""
    if any(record.severity == "fail" for record in verdict.plausibility):
        reason = (
            "The figures this query produced don't hold up against what "
            "the data can support, so the answer was withheld rather than "
            "shown unchecked."
        )
    else:
        reason = (
            "The draft made statements the evidence doesn't support, even "
            "after redrafting, so it was withheld."
        )
    return RefuseOutcome(
        reason=reason,
        what_would_work=(
            "Rephrasing the question, or asking for the underlying data "
            "directly."
        ),
        detail=verdict.reason or "the answer failed verification",
    )


def question_of_turn(history: list, turn: int) -> str | None:
    """The question turn N asked, read from a checkpoint's history —
    today's HistoryTurn records, or the (user, assistant) Message pairs
    a pre-Block-4 checkpoint holds, which upgrade_history reads the
    same way. The backfill verb's reader (engine store
    backfill-questions)."""
    for record in upgrade_history(history):
        if getattr(record, "turn", None) == turn:
            return record.question
    return None
