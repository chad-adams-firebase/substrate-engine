"""The leakage pre-flight finds a planted term wherever it hides — a
tracked file, a path, a commit message, a file that was later deleted
(history) — and stays quiet on a clean repository. The planted term is
synthetic; real terms never enter this repository."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "leakage_grep.py"
TERM = "zebra-pineapple"


def _git(*args, cwd):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("A clean file.\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "clean start", cwd=repo)
    return repo


def _run(repo: Path, terms: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), "--terms", str(terms)],
        capture_output=True,
        text=True,
    )


def test_a_clean_repository_is_clean(tmp_path):
    repo = _repo(tmp_path)
    terms = tmp_path / "terms"
    terms.write_text(f"# synthetic\n{TERM}\n\n", encoding="utf-8")

    result = _run(repo, terms)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("clean: 1 term(s)")


def test_every_hiding_place_is_found_without_echoing_the_term(tmp_path):
    repo = _repo(tmp_path)
    terms = tmp_path / "terms"
    terms.write_text(f"{TERM}\n", encoding="utf-8")

    # In a tracked file, in a path, in a commit message.
    (repo / "notes.md").write_text(f"see {TERM.upper()} for details\n", encoding="utf-8")
    (repo / f"{TERM}.txt").write_text("x\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", f"mention {TERM} here", cwd=repo)
    # In history only: added then deleted.
    (repo / "gone.txt").write_text(f"{TERM}\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "add", cwd=repo)
    (repo / "gone.txt").unlink()
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "remove", cwd=repo)

    result = _run(repo, terms)
    assert result.returncode == 1
    out = result.stdout
    assert out.startswith("LEAKAGE:")
    assert "tree     notes.md:1" in out
    assert f"path     {TERM}.txt" in out  # a path hit shows the path; nothing else does
    assert out.count("message  ") == 1  # the "mention" commit, by hash only
    assert out.count("history  ") == 3  # mention, add, remove — by hash only
    # The term itself never appears outside the path line: hashes, not
    # subjects, because a subject can carry the term.
    assert TERM not in "\n".join(
        line for line in out.splitlines() if "path     " not in line
    )


def test_a_missing_terms_file_is_exit_2_with_guidance(tmp_path):
    repo = _repo(tmp_path)
    result = _run(repo, tmp_path / "absent")
    assert result.returncode == 2
    assert "gitignored" in result.stderr
