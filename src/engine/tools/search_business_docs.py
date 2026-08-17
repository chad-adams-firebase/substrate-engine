"""search_business_docs — deterministic keyword search over the pack's
business-context memos.

Retrieval is deliberately simple and offline (no embeddings, no new
dependencies): docs split into sections at markdown headings, scored
by query-term frequency with a boost for title/heading hits, stable
tie-break by (slug, section order). The corpus is a handful of
curated memos; a ranking model would be machinery without a consumer.
"""

import re

from pydantic import BaseModel, ConfigDict

from engine.config.models import (
    SearchBusinessDocsSettings,
    SubstrateName,
    ToolName,
)
from engine.ports.substrate_store import SubstrateStoreError, SubstrateStorePort
from engine.substrates.models import BusinessDoc
from engine.tools.base import Tool
from engine.tools.envelope import (
    DocSearchEvidence,
    DocSearchHit,
    DocSearchOutput,
    DocSection,
    ToolInvocation,
)

_TOKEN = re.compile(r"\w+")
_HEADING_BOOST = 2
_SNIPPET_CHARS = 240


class DocSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _sections(doc: BusinessDoc) -> list[tuple[str, str]]:
    """(heading, text) per markdown section; the preamble before the
    first heading rides under the document title."""
    sections: list[tuple[str, list[str]]] = [(doc.title, [])]
    for line in doc.body.splitlines():
        if line.startswith("#"):
            sections.append((line.lstrip("#").strip(), []))
        else:
            sections[-1][1].append(line)
    return [
        (heading, "\n".join(lines).strip())
        for heading, lines in sections
        if "\n".join(lines).strip()
    ]


class SearchBusinessDocs(Tool):
    name = ToolName.SEARCH_BUSINESS_DOCS
    description = (
        "Search the curated business-context documents (policy memos, "
        "process notes — the 'why' behind thresholds and rules) and "
        "return the most relevant sections."
    )
    input_model = DocSearchInput

    def __init__(
        self, store: SubstrateStorePort, settings: SearchBusinessDocsSettings
    ) -> None:
        self._store = store
        self._settings = settings

    def run(self, params: DocSearchInput) -> ToolInvocation:
        query_tokens = set(_tokens(params.query))
        if not query_tokens:
            return self.fail(params, "The query contains no searchable words.")
        try:
            docs = self._store.business_docs()
        except SubstrateStoreError as exc:
            return self.fail(params, str(exc))

        scored: list[tuple[int, str, int, DocSearchHit, DocSection]] = []
        for doc in docs:
            for order, (heading, text) in enumerate(_sections(doc)):
                body_counts = _tokens(text)
                score = sum(1 for token in body_counts if token in query_tokens)
                heading_tokens = _tokens(heading) + _tokens(doc.title)
                score += _HEADING_BOOST * sum(
                    1 for token in heading_tokens if token in query_tokens
                )
                if score == 0:
                    continue
                scored.append(
                    (
                        score,
                        doc.slug,
                        order,
                        DocSearchHit(
                            slug=doc.slug,
                            title=doc.title,
                            heading=heading,
                            snippet=text[:_SNIPPET_CHARS],
                            score=score,
                        ),
                        DocSection(slug=doc.slug, heading=heading, text=text),
                    )
                )
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        top = scored[: self._settings.top_k]
        return self.ok(
            params,
            DocSearchOutput(hits=[hit for _, _, _, hit, _ in top]),
            evidence=DocSearchEvidence(
                sections=[section for _, _, _, _, section in top]
            ),
            substrates_read=[SubstrateName.BUSINESS_CONTEXT_DOCS],
        )
