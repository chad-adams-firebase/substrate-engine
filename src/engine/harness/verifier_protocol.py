"""The harness-side view of the Verifier: exactly the surface the
verify node calls. The graph depends on this Protocol, not the
concrete class, so graph tests can script verdicts."""

from typing import Protocol

from engine.tools.envelope import ToolInvocation
from engine.verifier.models import DraftAnswer, VerifierResult, VerifyContext


class VerifierProtocol(Protocol):
    def verify(
        self,
        *,
        question: str,
        draft: DraftAnswer,
        evidence: list[ToolInvocation],
        attempt: int,
        context: VerifyContext | None = None,
    ) -> VerifierResult: ...
