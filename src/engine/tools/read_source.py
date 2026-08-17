"""read_source — the exact lines of a CKG-located node, via
SourceCodePort at the pinned commit.

Before reading, the CKG manifest's extraction SHA is checked against
the source adapter's pin: if they diverge, every line reference is
invalid (Brief §5), and serving plausible-but-wrong code would be
worse than refusing.
"""

from pydantic import BaseModel, ConfigDict

from engine.config.models import SubstrateName, ToolName
from engine.ports.source_code import SourceCodePort
from engine.ports.substrate_store import SubstrateStoreError, SubstrateStorePort
from engine.substrates.ckg_index import CkgIndex
from engine.tools.base import Tool, manifest_ids_of
from engine.tools.envelope import ReadSourceOutput, ToolInvocation


class ReadSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A CKG node id or qualified name.
    node: str


class ReadSource(Tool):
    name = ToolName.READ_SOURCE
    description = (
        "Retrieve the exact source lines of a code-knowledge-graph node "
        "(function, method, class, or constant) at the pinned commit."
    )
    input_model = ReadSourceInput

    def __init__(self, store: SubstrateStorePort, source: SourceCodePort) -> None:
        self._store = store
        self._source = source
        self._lazy_index: CkgIndex | None = None

    @property
    def _index(self) -> CkgIndex:
        if self._lazy_index is None:
            self._lazy_index = CkgIndex(
                self._store.ckg_nodes(), [], [], [], []
            )
        return self._lazy_index

    def _sha_mismatch(self) -> str | None:
        ckg_manifests = [
            manifest
            for manifest in self._store.manifests()
            if manifest.generator == "ckg"
        ]
        for manifest in ckg_manifests:
            extracted = manifest.source_commit_sha
            if extracted is not None and extracted != self._source.commit_sha:
                return (
                    f"CKG extracted at {extracted[:12]} but the source is "
                    f"pinned to {self._source.commit_sha[:12]} — line "
                    f"references are invalid; regenerate the CKG or re-pin "
                    f"the clone."
                )
        return None

    def run(self, params: ReadSourceInput) -> ToolInvocation:
        try:
            mismatch = self._sha_mismatch()
            if mismatch is not None:
                return self.fail(params, mismatch)
            node = self._index.resolve_node(params.node)
        except SubstrateStoreError as exc:
            return self.fail(params, str(exc))
        if node is None:
            return self.fail(
                params,
                f"No CKG node with id or qualified name {params.node!r} — "
                f"locate one with traverse_code_knowledge_graph first.",
            )
        try:
            text = self._source.read(node.file_path, node.start_line, node.end_line)
        except (FileNotFoundError, ValueError) as exc:
            return self.fail(params, str(exc))
        return self.ok(
            params,
            ReadSourceOutput(
                qualified_name=node.qualified_name,
                file_path=node.file_path,
                start_line=node.start_line,
                end_line=node.end_line,
                commit_sha=self._source.commit_sha,
                text=text,
            ),
            substrates_read=[
                SubstrateName.CODE_KNOWLEDGE_GRAPH,
                SubstrateName.SOURCE_CODE,
            ],
            manifest_ids=manifest_ids_of([node]),
        )
