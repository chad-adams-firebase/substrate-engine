"""search_business_docs check: the FULL section texts live in the
invocation's evidence residue (envelope contract) — the drafter never
saw them, but the verifier quotes against them. Numbers in policy
memos (thresholds) are admitted as literals, excluded from
derivations."""

from engine.config.models import ToolName
from engine.tools.envelope import (
    DocSearchEvidence,
    DocSearchOutput,
    ToolInvocation,
)
from engine.verifier.checks.base import (
    SubstrateCheck,
    identifier_tokens,
    numeric_literals,
)
from engine.verifier.models import (
    CorpusText,
    EvidenceContribution,
    EvidenceValue,
)


class DocsCheck(SubstrateCheck):
    tool = ToolName.SEARCH_BUSINESS_DOCS

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, DocSearchOutput)
        contribution = EvidenceContribution()
        for hit in output.hits:
            contribution.vocabulary.add(hit.slug)
            contribution.vocabulary |= identifier_tokens(hit.title)
            contribution.vocabulary |= identifier_tokens(hit.heading)
            contribution.strings.update({hit.title, hit.heading})

        if isinstance(invocation.evidence, DocSearchEvidence):
            for index, section in enumerate(invocation.evidence.sections):
                section_ref = f"{ref}.sections[{index}]"
                contribution.vocabulary |= identifier_tokens(section.text)
                contribution.quote_corpus.append(
                    CorpusText(text=section.text, ref=section_ref)
                )
                contribution.numbers.extend(
                    EvidenceValue(
                        value=literal, ref=section_ref, salience="literal"
                    )
                    for literal in numeric_literals(section.text)
                )
        return contribution
