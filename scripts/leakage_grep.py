"""The de-identification pre-flight: no real name of the target
application, the production pack, the enterprise workspace — anywhere
in the tree or in history.

The terms are never in this repository. They live in an untracked,
gitignored file (default: .leakage-terms at the repo root, one term
per line, case-insensitive, fixed strings, blank lines and #comments
ignored) on the personal machine only. This script is the procedure;
the file is the knowledge.

    uv run python scripts/leakage_grep.py            # exit 0: clean
    uv run python scripts/leakage_grep.py --terms /path/to/terms

Four places are searched, each with git so untracked scratch never
counts: the tracked tree at HEAD (git grep -i -F), tracked paths,
every commit message on every branch (git log --all -i -F --grep),
and every commit's content on every branch (git log --all -i -S, the
pickaxe: commits where the number of occurrences changed). Exit 1 on
any hit with a compact listing; exit 2 when the terms file is missing.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def _git(*args: str, root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if result.returncode not in (0, 1):  # 1 is "no match" for grep/log
        raise SystemExit(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def load_terms(path: Path) -> list[str]:
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def scan(root: Path, terms: list[str]) -> list[str]:
    """Every hit as one line: where, then what. Term text is never
    echoed in full — the first two characters and the length, so the
    listing itself cannot leak when photographed or pasted."""
    hits: list[str] = []
    for term in terms:
        label = f"{term[:2]}…({len(term)})"
        tree = _git("grep", "-i", "-F", "-n", "-e", term, "HEAD", "--", ".", root=root)
        for line in tree.splitlines():
            # "HEAD:path:line:text" — the text is never echoed.
            parts = line.removeprefix("HEAD:").split(":", 2)
            location = parts[0]
            number = parts[1] if len(parts) > 1 else "?"
            hits.append(f"tree     {location}:{number}  [{label}]")
        for path in _git("ls-files", root=root).splitlines():
            if term.lower() in path.lower():
                hits.append(f"path     {path}  [{label}]")
        # Hashes only: a subject line could carry the term itself.
        messages = _git(
            "log", "--all", "-i", "-F", f"--grep={term}", "--format=%h", root=root
        )
        for line in messages.splitlines():
            hits.append(f"message  {line}  [{label}]")
        content = _git("log", "--all", "-i", f"-S{term}", "--format=%h", root=root)
        for line in content.splitlines():
            hits.append(f"history  {line}  [{label}]")
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--root", default=".", help="Repository root (default: current directory)."
    )
    parser.add_argument(
        "--terms",
        default=None,
        help="Terms file (default: <root>/.leakage-terms, gitignored, one per line).",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    terms_path = Path(args.terms).resolve() if args.terms else root / ".leakage-terms"
    if not terms_path.is_file():
        print(
            f"no terms file at {terms_path} — write one term per line there "
            f"(it is gitignored; it never enters the repository) and re-run.",
            file=sys.stderr,
        )
        return 2
    terms = load_terms(terms_path)
    if not terms:
        print(f"{terms_path} holds no terms.", file=sys.stderr)
        return 2
    hits = scan(root, terms)
    if hits:
        print(f"LEAKAGE: {len(hits)} hit(s) for {len(terms)} term(s)")
        for hit in hits:
            print("  " + hit)
        return 1
    print(f"clean: {len(terms)} term(s), no hits in tree, paths, messages, or history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
