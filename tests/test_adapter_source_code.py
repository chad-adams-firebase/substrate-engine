"""Local-directory SourceCodePort adapter: exact line slices, pinned
commit SHA, and no reads outside the configured root."""

import pytest

from engine.adapters.source_code_local import (
    LocalDirectorySource,
    LocalSourceSettings,
)

FILE_CONTENT = "line one\nline two\nline three\nline four\nline five\n"


@pytest.fixture
def adapter(tmp_path):
    root = tmp_path / "codebase"
    (root / "scoring").mkdir(parents=True)
    (root / "scoring" / "rules.py").write_text(FILE_CONTENT, encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret\n", encoding="utf-8")
    return LocalDirectorySource(
        LocalSourceSettings(root=str(root), commit_sha="abc1234")
    )


def test_reads_whole_file(adapter):
    assert adapter.read("scoring/rules.py") == FILE_CONTENT


def test_reads_inclusive_one_based_line_range(adapter):
    """1-based and inclusive, matching CKG start_line/end_line refs."""
    assert adapter.read("scoring/rules.py", 2, 4) == (
        "line two\nline three\nline four\n"
    )


def test_open_ended_ranges(adapter):
    assert adapter.read("scoring/rules.py", start_line=4) == "line four\nline five\n"
    assert adapter.read("scoring/rules.py", end_line=2) == "line one\nline two\n"


def test_commit_sha_is_reported(adapter):
    assert adapter.commit_sha == "abc1234"


def test_escaping_the_root_is_refused(adapter):
    """CKG location refs must not become arbitrary-file reads."""
    with pytest.raises(ValueError, match="outside the source root"):
        adapter.read("../outside.txt")


def test_missing_file_is_legible(adapter):
    with pytest.raises(FileNotFoundError, match="scoring/absent.py"):
        adapter.read("scoring/absent.py")


def test_list_files_is_sorted_and_skips_environment_dirs(tmp_path):
    root = tmp_path / "codebase"
    (root / "scoring" / "__pycache__").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "scoring" / "rules.py").write_text("x = 1\n", encoding="utf-8")
    (root / "app.py").write_text("y = 2\n", encoding="utf-8")
    (root / "scoring" / "__pycache__" / "rules.pyc").write_bytes(b"\x00")
    (root / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    adapter = LocalDirectorySource(
        LocalSourceSettings(root=str(root), commit_sha="abc1234")
    )
    assert adapter.list_files() == ["app.py", "scoring/rules.py"]


def test_missing_root_fails_at_construction(tmp_path):
    with pytest.raises(FileNotFoundError, match="root does not exist"):
        LocalDirectorySource(
            LocalSourceSettings(root=str(tmp_path / "nope"), commit_sha="abc1234")
        )
