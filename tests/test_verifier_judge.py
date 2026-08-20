"""Judge-call shaping and failure directions: everything that is not
a clean YES resolves toward unmatched."""

import pytest

from engine.config.models import JudgeSettings
from engine.ports.types import LLMResponse
from engine.verifier.judge import JudgeRunner, build_judge_messages
from engine.verifier.matching import EvidencePools
from engine.verifier.models import EvidenceValue, NumericClaim
from tests.stubs.llm_stub import ScriptedLLM

CLAIM = NumericClaim(
    surface="about 1.4 million",
    start=10,
    end=27,
    value=1_400_000.0,
    is_approximate=True,
    resolution=50_000.0,
)
POOLS = EvidencePools(
    numbers=[
        EvidenceValue(value=1_442_986, ref="e0.rows[0].n", salience="cell"),
        EvidenceValue(value=161, ref="e0.total_row_count", salience="count"),
    ]
)


def _runner(responses, **settings) -> tuple[JudgeRunner, ScriptedLLM]:
    llm = ScriptedLLM(responses)
    return JudgeRunner(llm, JudgeSettings(**settings)), llm


def test_judge_prompt_contains_claim_sentence_and_labeled_candidates():
    runner, llm = _runner([LLMResponse(content="YES — close.", model="s")])
    supported, _ = runner.judge(CLAIM, "There are about 1.4 million lines.", POOLS)
    assert supported

    call = llm.calls[0]
    assert call["temperature"] == 0.0
    assert call["tools"] is None
    body = call["messages"][1].content
    assert "'about 1.4 million'" in body
    assert "about 1.4 million lines" in body
    assert "1442986" in body and "e0.rows[0].n" in body
    system = call["messages"][0].content
    assert "YES or NO" in system and "outside knowledge" in system


def test_candidates_are_proximity_ranked_and_capped():
    many = EvidencePools(
        numbers=[
            EvidenceValue(value=v, ref=f"e0.v{i}", salience="cell")
            for i, v in enumerate([5, 1_500_000, 42, 1_400_100, 7, 9, 11, 13])
        ]
    )
    messages = build_judge_messages(CLAIM, "s", None or [])
    assert "(none returned this turn)" in messages[1].content

    runner, llm = _runner(
        [LLMResponse(content="NO", model="s")], max_candidate_values=2
    )
    runner.judge(CLAIM, "s", many)
    body = llm.calls[0]["messages"][1].content
    assert "1400100" in body and "1500000" in body
    assert "42" not in body.split("Evidence values:")[1]


@pytest.mark.parametrize(
    "content",
    ["NO", "no, that is wrong", "maybe?", "", "YESTERDAY it was fine"],
)
def test_anything_but_a_clean_yes_is_unsupported(content):
    runner, _ = _runner([LLMResponse(content=content, model="s")])
    supported, _ = runner.judge(CLAIM, "s", POOLS)
    assert not supported


def test_yes_with_punctuation_and_justification_is_supported():
    runner, _ = _runner([LLMResponse(content="YES.\nWithin rounding.", model="s")])
    supported, reason = runner.judge(CLAIM, "s", POOLS)
    assert supported and "rounding" in reason


def test_port_exception_fails_toward_unmatched():
    class ExplodingLLM:
        def complete(self, messages, *, tools=None, temperature=0.0):
            raise RuntimeError("socket closed")

    runner = JudgeRunner(ExplodingLLM(), JudgeSettings())
    supported, reason = runner.judge(CLAIM, "s", POOLS)
    assert not supported and "socket closed" in reason


def test_budget_exhaustion_and_disabled_mode():
    runner, llm = _runner(
        [LLMResponse(content="YES", model="s")] * 2, max_calls_per_turn=2
    )
    assert runner.judge(CLAIM, "s", POOLS)[0]
    assert runner.judge(CLAIM, "s", POOLS)[0]
    supported, reason = runner.judge(CLAIM, "s", POOLS)
    assert not supported and "budget" in reason
    assert len(llm.calls) == 2  # the third never called out

    disabled, stub = _runner([], enabled=False)
    supported, reason = disabled.judge(CLAIM, "s", POOLS)
    assert not supported and "disabled" in reason
    assert stub.calls == []
