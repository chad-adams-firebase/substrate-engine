"""app_capabilities check: the pack's self-description is curated
human text, so the harvest mirrors PrimerCheck — tokens and verbatim
corpus, no numbers (a drafted figure must trace to a data-bearing
tool, not to configured prose)."""

from engine.config.models import ToolName
from engine.tools.envelope import CapabilitiesOutput, ToolInvocation
from engine.verifier.checks.base import SubstrateCheck, identifier_tokens
from engine.verifier.models import CorpusText, EvidenceContribution


class CapabilitiesCheck(SubstrateCheck):
    tool = ToolName.APP_CAPABILITIES

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, CapabilitiesOutput)
        contribution = EvidenceContribution()
        contribution.vocabulary |= identifier_tokens(output.capabilities)
        contribution.quote_corpus.append(
            CorpusText(text=output.capabilities, ref=f"{ref}.capabilities")
        )
        for index, prompt in enumerate(output.starter_prompts):
            contribution.vocabulary |= identifier_tokens(prompt)
            contribution.quote_corpus.append(
                CorpusText(
                    text=prompt, ref=f"{ref}.starter_prompts[{index}]"
                )
            )
        return contribution
