"""check_execution check: the run count is quotable; logger/event
names and error-row values ground entities and figures."""

from engine.config.models import ToolName
from engine.tools.envelope import (
    CheckExecutionEvidence,
    CheckExecutionOutput,
    ToolInvocation,
)
from engine.verifier.checks.base import SubstrateCheck, identifier_tokens
from engine.verifier.models import (
    CorpusText,
    EvidenceContribution,
    EvidenceValue,
)


class ExecutionCheck(SubstrateCheck):
    tool = ToolName.CHECK_EXECUTION

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, CheckExecutionOutput)
        contribution = EvidenceContribution()

        if output.run_status is not None:
            contribution.numbers.append(
                EvidenceValue(
                    value=float(output.run_status.count),
                    ref=f"{ref}.run_status.count",
                    salience="count",
                )
            )
            contribution.vocabulary |= identifier_tokens(output.run_status.detail)
            contribution.quote_corpus.append(
                CorpusText(
                    text=output.run_status.detail,
                    ref=f"{ref}.run_status.detail",
                )
            )

        for index, error in enumerate(output.errors or []):
            error_ref = f"{ref}.errors[{index}]"
            for key, value in error.items():
                contribution.vocabulary.add(key)
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    contribution.numbers.append(
                        EvidenceValue(
                            value=float(value),
                            ref=f"{error_ref}.{key}",
                            salience="cell",
                            group=error_ref,
                        )
                    )
                elif isinstance(value, str):
                    contribution.strings.add(value)
                    contribution.vocabulary |= identifier_tokens(value)
        if output.errors is not None:
            contribution.numbers.append(
                EvidenceValue(
                    value=float(len(output.errors)),
                    ref=f"{ref}.len(errors)",
                    salience="count",
                )
            )

        if isinstance(invocation.evidence, CheckExecutionEvidence):
            for index, line in enumerate(invocation.evidence.lines):
                contribution.vocabulary |= identifier_tokens(line)
                contribution.quote_corpus.append(
                    CorpusText(text=line, ref=f"{ref}.lines[{index}]")
                )
        return contribution
