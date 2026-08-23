"""Builders for full-graph harness tests: an AskSession over the tool
pack with a scripted LLM and (by default) a scripted verifier."""

from engine.config.models import PortName
from engine.config.pack_loader import load_pack
from engine.harness.drafter import Drafter
from engine.harness.graph import GraphDeps
from engine.harness.prompts import render_drafter_prompt, render_router_prompt
from engine.harness.session import AskSession
from engine.ports.types import LLMResponse, ToolCall
from engine.runtime.harness import build_verifier
from engine.runtime.tools import resolve_data_terms, resolve_pack_coverage
from engine.tools.envelope import ToolInvocation
from engine.verifier.models import (
    AttemptRecord,
    DraftAnswer,
    PlausibilityRecord,
    RegenerationFeedback,
    VerifierResult,
)
from tests.conftest import build_tool_registry


def verified_result(attempt: int = 1) -> VerifierResult:
    return VerifierResult(
        disposition="verified",
        attempt_record=AttemptRecord(attempt=attempt, claims=[], unmatched_count=0),
    )


def retry_result(feedback: RegenerationFeedback, attempt: int = 1) -> VerifierResult:
    return VerifierResult(
        disposition="retry",
        attempt_record=AttemptRecord(attempt=attempt, claims=[], unmatched_count=1),
        feedback=feedback,
    )


def unverified_result(attempt: int = 2) -> VerifierResult:
    return VerifierResult(
        disposition="unverified",
        attempt_record=AttemptRecord(attempt=attempt, claims=[], unmatched_count=1),
    )


def refused_result(detail: str = "evidence contradicts stats") -> VerifierResult:
    return VerifierResult(
        disposition="refused",
        attempt_record=AttemptRecord(attempt=1, claims=[], unmatched_count=0),
        plausibility=[
            PlausibilityRecord(
                check="run_sql.count_vs_stats",
                tool="run_sql",
                severity="fail",
                detail=detail,
            )
        ],
    )


class StubVerifier:
    """FIFO-scripted verifier that records every call."""

    def __init__(self, results: list[VerifierResult] | None = None) -> None:
        self._results = list(results) if results else []
        self.calls: list[dict] = []

    def verify(
        self,
        *,
        question: str,
        draft: DraftAnswer,
        evidence: list[ToolInvocation],
        attempt: int,
    ) -> VerifierResult:
        self.calls.append(
            {
                "question": question,
                "draft": draft,
                "evidence": evidence,
                "attempt": attempt,
            }
        )
        if self._results:
            return self._results.pop(0)
        return verified_result(attempt)


def build_ask_session(
    pack_dir,
    llm_responses: list[LLMResponse],
    verifier=None,
    listener=None,
    real_verifier: bool = False,
):
    """(session, ports, verifier). The scripted LLM serves router,
    drafter, and (with real_verifier) judge calls from one FIFO."""
    registry, ports = build_tool_registry(pack_dir, llm_responses)
    pack = load_pack(pack_dir)
    llm = ports.get(PortName.LLM)
    if verifier is None:
        verifier = (
            build_verifier(pack, ports) if real_verifier else StubVerifier()
        )
    coverage = resolve_pack_coverage(pack, ports)
    deps = GraphDeps(
        llm=llm,
        registry=registry,
        verifier=verifier,
        drafter=Drafter(llm, render_drafter_prompt(app_name=pack.config.name)),
        settings=pack.config.harness,
        router_prompt=render_router_prompt(
            app_name=pack.config.name,
            app_description=pack.config.description,
            max_iterations=pack.config.harness.max_router_iterations,
            data_coverage=(
                (coverage.start.isoformat(), coverage.end.isoformat())
                if coverage is not None
                else None
            ),
            data_terms=resolve_data_terms(pack, ports),
        ),
    )
    session = AskSession(
        deps=deps,
        work_store=ports.get(PortName.WORK_STORE),
        identity=ports.get(PortName.IDENTITY),
        listener=listener,
    )
    return session, ports, verifier


def tool_call(name: str, arguments: dict | None = None) -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(name=name, arguments=arguments or {})],
        model="scripted",
    )
