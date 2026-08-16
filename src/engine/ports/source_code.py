"""SourceCodePort — the target app's source, addressed by CKG location refs.

Local adapter: a local directory (fixture codebase). Real adapter
(later phase): local git clone of the target repo.

Paths are relative to the target repo root, at a pinned commit: the
CKG and the source snapshot must share one commit SHA or the CKG's
line references are invalid (Brief §5).
"""

from typing import Protocol


class SourceCodePort(Protocol):
    def read(
        self,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str: ...

    @property
    def commit_sha(self) -> str: ...
