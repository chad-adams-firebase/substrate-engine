"""answer_from_known_items check: titles and snippets are quotable;
nothing else — suggestions are not claims material (§11: suggested,
never authoritative)."""

from engine.config.models import ToolName
from engine.tools.envelope import KnownItemsOutput, ToolInvocation
from engine.verifier.checks.base import SubstrateCheck, identifier_tokens
from engine.verifier.models import CorpusText, EvidenceContribution


class KnownItemsCheck(SubstrateCheck):
    tool = ToolName.ANSWER_FROM_KNOWN_ITEMS

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, KnownItemsOutput)
        contribution = EvidenceContribution()
        for index, match in enumerate(output.matches):
            contribution.strings.add(match.title)
            contribution.vocabulary |= identifier_tokens(match.title)
            contribution.quote_corpus.append(
                CorpusText(
                    text=f"{match.title}\n{match.snippet}",
                    ref=f"{ref}.matches[{index}]",
                )
            )
        return contribution
