"""SourceCodePort — the target app's source, addressed by CKG location refs.

Local adapter: a local clone of the external target repo (for the
reference pack, chad-adams-firebase/invoice-guard) at a pinned commit
SHA. Real adapter (later phase): local git clone of the target repo.

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

    def list_files(self) -> list[str]:
        """All regular files under the repo root, as sorted repo-root-
        relative paths. Sorted at the adapter because the CKG
        generator's output must not depend on filesystem walk order.
        (Added in Phase 2 for the repo walk; the git adapter will use
        `git ls-tree` for the same contract.)"""
        ...

    @property
    def commit_sha(self) -> str: ...
