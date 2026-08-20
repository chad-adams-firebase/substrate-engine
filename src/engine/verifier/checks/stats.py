"""query_univariate_stats check: harvest only — the stats substrate IS
the plausibility reference, so it gets no plausibility check itself."""

from engine.config.models import ToolName
from engine.tools.envelope import StatsOutput, ToolInvocation
from engine.verifier.checks.base import SubstrateCheck
from engine.verifier.models import EvidenceContribution, EvidenceValue


class StatsCheck(SubstrateCheck):
    tool = ToolName.QUERY_UNIVARIATE_STATS

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, StatsOutput)
        contribution = EvidenceContribution()
        for index, row in enumerate(output.rows):
            base = f"{ref}.rows[{index}]"
            contribution.vocabulary |= {row.table_name, row.column_name}
            contribution.numbers.append(
                EvidenceValue(
                    value=float(row.row_count),
                    ref=f"{base}.row_count",
                    salience="count",
                    group=base,
                )
            )
            contribution.numbers.append(
                EvidenceValue(
                    value=float(row.distinct_count),
                    ref=f"{base}.distinct_count",
                    salience="count",
                    group=base,
                )
            )
            contribution.numbers.append(
                EvidenceValue(
                    value=row.null_rate,
                    ref=f"{base}.null_rate",
                    salience="stat",
                    group=base,
                )
            )
            if row.mean is not None:
                contribution.numbers.append(
                    EvidenceValue(
                        value=row.mean,
                        ref=f"{base}.mean",
                        salience="stat",
                        group=base,
                    )
                )
            for bound_name in ("min_value", "max_value"):
                bound = getattr(row, bound_name)
                if bound is None:
                    continue
                try:
                    contribution.numbers.append(
                        EvidenceValue(
                            value=float(bound),
                            ref=f"{base}.{bound_name}",
                            salience="stat",
                            group=base,
                        )
                    )
                except ValueError:
                    contribution.strings.add(bound)
            for top_index, top in enumerate(row.top_values):
                contribution.strings.add(top.value)
                contribution.numbers.append(
                    EvidenceValue(
                        value=float(top.count),
                        ref=f"{base}.top_values[{top_index}].count",
                        salience="count",
                        group=base,
                    )
                )
        return contribution
