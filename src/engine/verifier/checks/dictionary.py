"""lookup_data_dictionary check.

§9.3's "definitional claims match retrieved rows" meets prose reality
here: descriptions, concept definitions, and gotcha details are human
text, and paraphrase cannot be adjudicated mechanically (and will not
be adjudicated by the judge — no scope creep). What IS checked:
entity names, verbatim quotes of the retrieved text, and numeric
figures appearing in it (as literals). Definitional paraphrase ships
unverified by design, like primer paraphrase.
"""

from engine.config.models import ToolName
from engine.tools.envelope import DictionaryLookupOutput, ToolInvocation
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


class DictionaryCheck(SubstrateCheck):
    tool = ToolName.LOOKUP_DATA_DICTIONARY

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, DictionaryLookupOutput)
        contribution = EvidenceContribution()

        for index, row in enumerate(output.rows):
            contribution.vocabulary.add(row.table_name)
            if row.column_name:
                contribution.vocabulary.add(row.column_name)
            if row.fk_target:
                contribution.vocabulary |= identifier_tokens(row.fk_target)
            for value in row.enum_values or []:
                contribution.strings.add(value)
                contribution.vocabulary |= identifier_tokens(value)
            if row.description:
                self._prose(
                    contribution, row.description, f"{ref}.rows[{index}]"
                )

        for index, concept in enumerate(output.concepts):
            contribution.vocabulary |= identifier_tokens(concept.name)
            contribution.vocabulary.update(concept.tables)
            for synonym in concept.synonyms:
                contribution.vocabulary |= identifier_tokens(synonym)
            self._prose(
                contribution, concept.definition, f"{ref}.concepts[{index}]"
            )

        for index, metric in enumerate(output.metrics):
            contribution.vocabulary |= identifier_tokens(metric.name)
            contribution.vocabulary.update(metric.tables)
            contribution.vocabulary |= identifier_tokens(metric.aggregation_sql)
            contribution.vocabulary |= identifier_tokens(metric.filter_sql)
            for text in (metric.description, metric.notes):
                self._prose(contribution, text, f"{ref}.metrics[{index}]")
            contribution.quote_corpus.append(
                CorpusText(
                    text=metric.aggregation_sql,
                    ref=f"{ref}.metrics[{index}].aggregation_sql",
                )
            )

        for index, path in enumerate(output.join_paths):
            for step in path.steps:
                contribution.vocabulary.update(
                    {
                        step.from_table,
                        step.from_column,
                        step.to_table,
                        step.to_column,
                    }
                )

        for index, gotcha in enumerate(output.gotchas):
            contribution.vocabulary |= identifier_tokens(gotcha.name)
            contribution.vocabulary.update(gotcha.tables)
            for text in (gotcha.summary, gotcha.detail):
                self._prose(contribution, text, f"{ref}.gotchas[{index}]")
        return contribution

    @staticmethod
    def _prose(
        contribution: EvidenceContribution, text: str, ref: str
    ) -> None:
        if not text:
            return
        contribution.vocabulary |= identifier_tokens(text)
        contribution.quote_corpus.append(CorpusText(text=text, ref=ref))
        contribution.numbers.extend(
            EvidenceValue(value=literal, ref=ref, salience="literal")
            for literal in numeric_literals(text)
        )
