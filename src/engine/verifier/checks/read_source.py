"""read_source check: quoted-as-literal code must string-match the
retrieved content; the retrieved line range grounds location claims.
Numeric literals in the code support prose like "the 0.15 threshold"
but never participate in derivations."""

from engine.config.models import ToolName
from engine.tools.envelope import ReadSourceOutput, ToolInvocation
from engine.verifier.checks.base import (
    SubstrateCheck,
    dotted_suffixes,
    identifier_tokens,
    numeric_literals,
)
from engine.verifier.models import (
    CorpusText,
    EvidenceContribution,
    EvidenceValue,
    LineRef,
)


class ReadSourceCheck(SubstrateCheck):
    tool = ToolName.READ_SOURCE

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, ReadSourceOutput)
        contribution = EvidenceContribution()
        contribution.vocabulary |= dotted_suffixes(output.qualified_name)
        contribution.vocabulary.add(output.file_path)
        contribution.vocabulary |= identifier_tokens(output.text)
        contribution.strings.add(output.commit_sha)
        contribution.line_refs.append(
            LineRef(
                file_path=output.file_path,
                start=output.start_line,
                end=output.end_line,
                ref=f"{ref}.text",
            )
        )
        contribution.quote_corpus.append(
            CorpusText(text=output.text, ref=f"{ref}.text")
        )
        contribution.numbers.extend(
            EvidenceValue(value=literal, ref=f"{ref}.text", salience="literal")
            for literal in numeric_literals(output.text)
        )
        return contribution
