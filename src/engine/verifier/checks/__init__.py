"""Per-substrate verifier checks (§9.3) — a plugin registry keyed by
tool name, mirroring the tool registry. Checks contribute evidence
pools (harvest) and evidence-side sanity findings (plausibility); ONE
global matcher consumes the merged pools, because a drafted number may
be supported by any invocation, not just the one it "belongs" to."""

from engine.verifier.checks.base import CheckRegistry, SubstrateCheck
from engine.verifier.checks.ckg import CkgCheck
from engine.verifier.checks.dictionary import DictionaryCheck
from engine.verifier.checks.docs import DocsCheck
from engine.verifier.checks.execution import ExecutionCheck
from engine.verifier.checks.known_items import KnownItemsCheck
from engine.verifier.checks.primer import PrimerCheck
from engine.verifier.checks.read_source import ReadSourceCheck
from engine.verifier.checks.run_sql import RunSqlCheck
from engine.verifier.checks.stats import StatsCheck

__all__ = ["CheckRegistry", "SubstrateCheck", "default_checks"]


def default_checks() -> list[SubstrateCheck]:
    return [
        RunSqlCheck(),
        StatsCheck(),
        CkgCheck(),
        ReadSourceCheck(),
        DictionaryCheck(),
        PrimerCheck(),
        DocsCheck(),
        ExecutionCheck(),
        KnownItemsCheck(),
    ]
