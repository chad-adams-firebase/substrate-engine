"""traverse_code_knowledge_graph check: faithfulness against THIS
TURN's traversal results only — an entity that exists somewhere in the
substrate but was never retrieved this turn is not evidence-grounded
(that is exactly how a plausible hallucinated name would sneak
through). Counterpart nodes ride in the envelope, so no store lookup.

v1 boundary, stated not hidden: call ORDER in prose is a relational
claim deterministic parsing cannot check; the entities and the quoted
conditions are checked, and the drafter is expected to inject ordered
lists from the line-ordered edges. No plausibility check (Brief §9.3).
"""

from engine.config.models import ToolName
from engine.tools.envelope import CkgTraversalOutput, ToolInvocation
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


class CkgCheck(SubstrateCheck):
    tool = ToolName.TRAVERSE_CODE_KNOWLEDGE_GRAPH

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, CkgTraversalOutput)
        contribution = EvidenceContribution()

        nodes = list(output.nodes)
        if output.entry_node is not None:
            nodes.append(output.entry_node)
        for index, node in enumerate(nodes):
            node_ref = f"{ref}.nodes[{index}]"
            contribution.vocabulary |= dotted_suffixes(node.qualified_name)
            contribution.vocabulary.add(node.file_path)
            contribution.line_refs.append(
                LineRef(
                    file_path=node.file_path,
                    start=node.start_line,
                    end=node.end_line,
                    ref=node_ref,
                )
            )
            for text_field in ("signature", "docstring", "value"):
                text = getattr(node, text_field, None)
                if not text:
                    continue
                contribution.vocabulary |= identifier_tokens(text)
                contribution.quote_corpus.append(
                    CorpusText(text=text, ref=f"{node_ref}.{text_field}")
                )
                contribution.numbers.extend(
                    EvidenceValue(
                        value=literal,
                        ref=f"{node_ref}.{text_field}",
                        salience="literal",
                    )
                    for literal in numeric_literals(text)
                )

        if output.entry_component is not None:
            contribution.vocabulary.add(output.entry_component.id)
            contribution.vocabulary |= identifier_tokens(
                output.entry_component.name
            )
            contribution.quote_corpus.append(
                CorpusText(
                    text=output.entry_component.name,
                    ref=f"{ref}.entry_component.name",
                )
            )

        for index, edge in enumerate(output.edges):
            if edge.target_table is not None:
                contribution.vocabulary.add(edge.target_table)

        for index, conditional in enumerate(output.conditionals):
            cond_ref = f"{ref}.conditionals[{index}]"
            contribution.vocabulary |= identifier_tokens(
                conditional.condition_text
            )
            contribution.quote_corpus.append(
                CorpusText(text=conditional.condition_text, ref=cond_ref)
            )
            contribution.numbers.extend(
                EvidenceValue(value=literal, ref=cond_ref, salience="literal")
                for literal in numeric_literals(conditional.condition_text)
            )

        # "the twelve rule functions" is derivable from what came back.
        for name, count in (
            ("nodes", len(output.nodes)),
            ("edges", len(output.edges)),
            ("conditionals", len(output.conditionals)),
        ):
            if count:
                contribution.numbers.append(
                    EvidenceValue(
                        value=float(count),
                        ref=f"{ref}.len({name})",
                        salience="count",
                    )
                )
        return contribution
