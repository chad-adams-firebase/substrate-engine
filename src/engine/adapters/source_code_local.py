"""Local-directory adapter for SourceCodePort.

Serves the target app's source from a directory on disk (the fixture
codebase, locally). The commit SHA is declared in settings because a
plain directory has no VCS to ask; the git-clone real adapter (later
phase) reads it from the clone instead. The CKG built against this
source must carry the same SHA or its line references are invalid.

read() never serves a path outside the configured root: file paths
arrive from CKG location refs, and a corrupt or malicious ref must
not turn into an arbitrary-file read.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class LocalSourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Root of the target codebase, relative to the pack directory
    # (resolved by the container).
    root: str
    commit_sha: str


class LocalDirectorySource:
    def __init__(self, settings: LocalSourceSettings) -> None:
        self._settings = settings
        self._root = Path(settings.root).resolve()
        if not self._root.is_dir():
            raise FileNotFoundError(
                f"Source code root does not exist: {self._root}"
            )

    @property
    def settings(self) -> LocalSourceSettings:
        return self._settings

    @property
    def commit_sha(self) -> str:
        return self._settings.commit_sha

    def list_files(self) -> list[str]:
        # Sorted so downstream extraction never depends on walk order;
        # VCS and environment directories are not source.
        skipped = {".git", ".venv", "__pycache__", ".pytest_cache"}
        found: list[str] = []
        for path in self._root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self._root)
            if any(part in skipped for part in relative.parts):
                continue
            found.append(relative.as_posix())
        return sorted(found)

    def read(
        self,
        file_path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        resolved = (self._root / file_path).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError(
                f"Refusing to read outside the source root: {file_path}"
            )
        if not resolved.is_file():
            raise FileNotFoundError(f"No such file in source root: {file_path}")

        text = resolved.read_text(encoding="utf-8")
        if start_line is None and end_line is None:
            return text

        # Line numbers are 1-based and inclusive, matching how the CKG
        # records start_line/end_line.
        lines = text.splitlines(keepends=True)
        start = (start_line or 1) - 1
        end = end_line if end_line is not None else len(lines)
        return "".join(lines[start:end])
