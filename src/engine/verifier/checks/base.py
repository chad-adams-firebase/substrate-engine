"""SubstrateCheck contract + registry + shared harvest helpers.

Checks are pure: harvest and plausibility receive data (the invocation
and a context of stats rows + settings), never ports — the Verifier
loads stats once and hands them in.
"""

import re
from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from engine.config.models import PlausibilitySettings, ToolName
from engine.substrates.models import StatsRow
from engine.tools.envelope import ToolInvocation
from engine.verifier.models import EvidenceContribution, PlausibilityFinding


class PlausibilityContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stats: list[StatsRow]
    settings: PlausibilitySettings


class SubstrateCheck(ABC):
    tool: ClassVar[ToolName]

    @abstractmethod
    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        """The invocation's quotable material, refs prefixed with the
        evidence index handle (e.g. "e1")."""

    def plausibility(
        self, invocation: ToolInvocation, ctx: PlausibilityContext
    ) -> list[PlausibilityFinding]:
        return []


class CheckRegistry:
    def __init__(self, checks: list[SubstrateCheck]) -> None:
        self._checks = {check.tool: check for check in checks}

    def for_tool(self, tool: ToolName) -> SubstrateCheck | None:
        return self._checks.get(tool)


_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
# Harvest-side mirror of claims._DOTTED: identifier_tokens splits at
# dots, so a dotted logger/component name never enters vocabulary
# whole unless harvested whole.
_DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:-\w+)*(?:\.[A-Za-z_]\w*(?:-\w+)*)+\b")


def identifier_tokens(text: str) -> set[str]:
    """Every identifier-shaped token of length >= 2 — the vocabulary a
    drafted entity name may cite."""
    return {t for t in _IDENTIFIER.findall(text) if len(t) >= 2}


def dotted_tokens(text: str) -> set[str]:
    """Dotted identifier-shaped tokens, whole — the exact strings a
    drafted dotted name (invoiceguard.benchmark_scoring) may cite."""
    return set(_DOTTED.findall(text))


def numeric_literals(text: str) -> list[float]:
    return [float(m) for m in _NUMBER.findall(text)]
