"""app_primer check: named-entity spot-check over curated human text
(§9.3 — minimal by design). No numbers are harvested from primer
prose: a drafted figure must trace to a data-bearing tool, not to a
sentence a human once wrote."""

from engine.config.models import ToolName
from engine.tools.envelope import PrimerOutput, ToolInvocation
from engine.verifier.checks.base import SubstrateCheck, identifier_tokens
from engine.verifier.models import CorpusText, EvidenceContribution


class PrimerCheck(SubstrateCheck):
    tool = ToolName.APP_PRIMER

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, PrimerOutput)
        contribution = EvidenceContribution()
        contribution.vocabulary |= identifier_tokens(output.primer)
        contribution.quote_corpus.append(
            CorpusText(text=output.primer, ref=f"{ref}.primer")
        )
        for index, component in enumerate(output.components):
            contribution.vocabulary.add(component.id)
            contribution.vocabulary |= identifier_tokens(component.name)
            contribution.vocabulary |= identifier_tokens(
                component.description
            )
            # The pack-authored display name, verbatim: a backticked
            # `Rules engine` in a draft must be able to match the
            # structured field, not only prose that happens to start a
            # sentence with it (carryback #1b).
            contribution.quote_corpus.append(
                CorpusText(
                    text=component.name,
                    ref=f"{ref}.components[{index}].name",
                )
            )
        return contribution
