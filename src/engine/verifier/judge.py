"""The LLM fuzzy judge (§9.2 step 3): one yes/no call per residual
numeric claim, temperature 0, no tools, no retries.

The judge never produces the claim it checks and never sees the full
draft or the question — only the claim, its sentence, and labeled pool
values. Every failure mode (NO, prose, empty, port exception, budget
exhausted, disabled) resolves toward unmatched, never toward verified.
"""

from engine.config.models import JudgeSettings
from engine.ports.llm import LLMPort
from engine.ports.types import Message
from engine.verifier.matching import EvidencePools, nearest_values
from engine.verifier.models import EvidenceValue, NumericClaim

_SYSTEM = (
    "You are a numeric verification judge. You will see one claim from "
    "a drafted answer and the evidence values that tools actually "
    "returned. Decide whether the claim is fully supported by these "
    "values, allowing approximation language its plain meaning. Use no "
    "outside knowledge and compute nothing beyond comparing the claim "
    "to the listed values. Reply with exactly YES or NO on the first "
    "line, then one short sentence of justification."
)


def build_judge_messages(
    claim: NumericClaim,
    sentence: str,
    candidates: list[tuple[float, EvidenceValue]],
) -> list[Message]:
    lines = [f"Claim: {claim.surface!r}", f"In sentence: {sentence!r}"]
    if claim.comparator is not None:
        lines.append(f"Comparator: {claim.comparator}")
    lines.append("Evidence values:")
    if candidates:
        lines.extend(
            f"- {shown!r} ({value.salience}, {value.ref})"
            for shown, value in candidates
        )
    else:
        lines.append("- (none returned this turn)")
    return [
        Message(role="system", content=_SYSTEM),
        Message(role="user", content="\n".join(lines)),
    ]


class JudgeRunner:
    def __init__(self, llm: LLMPort, settings: JudgeSettings) -> None:
        self._llm = llm
        self._settings = settings
        self.calls_made = 0

    def judge(
        self, claim: NumericClaim, sentence: str, pools: EvidencePools
    ) -> tuple[bool, str]:
        """(supported, reason). False whenever anything is off."""
        if not self._settings.enabled:
            return False, "judge disabled"
        if self.calls_made >= self._settings.max_calls_per_turn:
            return False, "judge budget exhausted"
        self.calls_made += 1

        candidates = nearest_values(
            claim, pools, self._settings.max_candidate_values
        )
        messages = build_judge_messages(claim, sentence, candidates)
        try:
            response = self._llm.complete(messages, temperature=0.0)
        except Exception as exc:  # port failure -> unmatched, never verified
            return False, f"judge call failed: {type(exc).__name__}: {exc}"

        content = response.content.strip()
        first = content.split(None, 1)[0].strip(".,:;!").upper() if content else ""
        if first == "YES":
            return True, content
        return False, content or "empty judge response"
