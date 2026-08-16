"""The scripted LLM stub itself: deterministic order, call recording,
loud exhaustion, and registrability as a DI adapter."""

import copy

import pytest

from engine.config.models import PortName
from engine.config.pack_loader import load_pack
from engine.ports.types import LLMResponse, Message
from engine.runtime.container import build
from engine.runtime.registry import AdapterRegistry
from tests.conftest import VALID_CONFIG
from tests.stubs.llm_stub import ScriptedLLM


def _response(text: str) -> LLMResponse:
    return LLMResponse(content=text, model="scripted")


def test_returns_responses_in_scripted_order():
    stub = ScriptedLLM([_response("first"), _response("second")])

    assert stub.complete([Message(role="user", content="a")]).content == "first"
    assert stub.complete([Message(role="user", content="b")]).content == "second"


def test_records_calls_for_assertions():
    stub = ScriptedLLM([_response("ok")])

    stub.complete([Message(role="user", content="hello")], temperature=0.3)

    assert len(stub.calls) == 1
    assert stub.calls[0]["messages"][0].content == "hello"
    assert stub.calls[0]["temperature"] == 0.3


def test_exhaustion_fails_loudly():
    stub = ScriptedLLM([])

    with pytest.raises(AssertionError, match="exhausted"):
        stub.complete([Message(role="user", content="a")])


def test_stub_registers_as_an_llm_adapter(make_pack):
    """The stub is pytest plumbing, so it is NOT in the default
    registry — tests inject it. This also proves the registry is open:
    a new adapter is a registration, not an engine change."""
    registry = AdapterRegistry()
    registry.register(
        PortName.LLM, "scripted", lambda s, root: ScriptedLLM([_response("hi")])
    )
    config = copy.deepcopy(VALID_CONFIG)
    config["adapters"] = {"llm": {"adapter": "scripted"}}
    pack = load_pack(make_pack(config))

    llm = build(pack, registry).get(PortName.LLM)

    assert llm.complete([Message(role="user", content="x")]).content == "hi"
