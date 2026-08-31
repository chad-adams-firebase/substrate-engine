"""app_capabilities (play pass R6): meta questions answer from pack
config through the normal route→draft→verify path — a registered tool,
never ad-hoc LLM freedom."""

from engine.config.models import UiSettings
from engine.ports.types import LLMResponse
from engine.tools.app_capabilities import AppCapabilities, CapabilitiesInput
from engine.tools.envelope import CapabilitiesOutput, ToolInvocation
from engine.verifier.checks.capabilities import CapabilitiesCheck
from tests.harness_support import build_ask_session, tool_call


def test_capabilities_answers_from_config_alone():
    tool = AppCapabilities(
        UiSettings(
            starter_prompts=["How many invoices had findings?"],
            capabilities="This assistant answers data questions.",
        )
    )
    invocation = tool.run(CapabilitiesInput())
    assert invocation.status == "ok"
    assert invocation.output.kind == "app_capabilities"
    assert invocation.output.capabilities == (
        "This assistant answers data questions."
    )
    assert invocation.output.starter_prompts == [
        "How many invoices had findings?"
    ]
    assert invocation.substrates_read == []  # config, not a substrate
    assert invocation.evidence is None


def test_empty_ui_block_fails_loudly_at_use():
    invocation = AppCapabilities(UiSettings()).run(CapabilitiesInput())
    assert invocation.status == "error"
    assert "ui block" in invocation.error


def test_check_harvests_text_and_prompts_no_numbers():
    invocation = ToolInvocation(
        tool="app_capabilities",
        arguments={},
        status="ok",
        output=CapabilitiesOutput(
            capabilities="Ask about invoiceguard findings and rules.",
            starter_prompts=["Show me the source of rule_rate_variance"],
        ),
    )
    contribution = CapabilitiesCheck().harvest(invocation, "e0")
    assert "invoiceguard" in contribution.vocabulary
    assert "rule_rate_variance" in contribution.vocabulary
    texts = [corpus.text for corpus in contribution.quote_corpus]
    assert "Ask about invoiceguard findings and rules." in texts
    assert "Show me the source of rule_rate_variance" in texts
    assert contribution.numbers == []  # configured prose grounds no figure


def test_meta_question_routes_to_a_verified_answer(tool_pack):
    """The R6 shape end-to-end: 'how do I use this chat?' reaches
    app_capabilities, drafts from the configured text, and ships as a
    normal verified markdown answer — exit 0, Verifier included."""
    responses = [
        tool_call("app_capabilities"),
        tool_call("give_answer", {"shape": "prose"}),
        LLMResponse(
            content=(
                "You can ask data, code, and workflow questions about "
                "the invoiceguard application."
            ),
            model="s",
        ),
    ]
    session, _, verifier = build_ask_session(tool_pack, responses)
    result = session.ask("How do I use this chat?")

    assert result.outcome.kind == "answer"
    assert result.outcome.verification == "verified"
    assert result.tools_used == ["app_capabilities"]
    (call,) = verifier.calls
    assert call["draft"].kind == "prose"
