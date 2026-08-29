"""Load the question bank from a bank root (evals/<name>/).

Layout: <root>/eval.yaml + <root>/bank/*.yaml (each file a YAML list
of rows) + <root>/gold/*.py. Files load in sorted order.

The bank hash — what ties a run report to the exact bank that
produced it — is sha256 over the raw bytes of those files (sorted
relative paths + contents), NOT over their parsed form. Hashing
parsed rows meant every assertion-schema evolution (a new defaulted
field) changed every row's normalized dump and silently orphaned all
historical reports. Raw bytes still catch an edited assertion, gold
script, or config; file layout is part of the identity now, so
renaming or splitting a bank file is a bank edit. No line-ending
normalization: .gitattributes pins eol=lf on every machine.

Every failure message names the file and row: bank authors debug from
the error text (CLAUDE.md style law).
"""

import hashlib
from pathlib import Path

import yaml
from pydantic import ValidationError

from engine.eval.models import BankRow, EvalConfig

CONFIG_FILENAME = "eval.yaml"
BANK_DIRNAME = "bank"


class BankLoadError(Exception):
    """The bank cannot be loaded; the message tells the author what
    to fix."""


class LoadedBank:
    def __init__(
        self, root: Path, config: EvalConfig, rows: list[BankRow]
    ) -> None:
        self.root = root
        self.config = config
        self.rows = rows
        self.bank_hash = _bank_hash(root)

    def row_ids(self) -> list[str]:
        return [row.id for row in self.rows]

    def gold_path(self, row: BankRow) -> Path | None:
        if row.gold is None:
            return None
        return self.root / row.gold

    def select(self, patterns: list[str] | None) -> list[BankRow]:
        """--rows filtering: exact ids plus trailing-* globs."""
        if not patterns:
            return list(self.rows)
        import fnmatch

        selected = [
            row
            for row in self.rows
            if any(fnmatch.fnmatchcase(row.id, p) for p in patterns)
        ]
        matched = {
            p
            for p in patterns
            if any(fnmatch.fnmatchcase(row.id, p) for row in self.rows)
        }
        unmatched = [p for p in patterns if p not in matched]
        if unmatched:
            raise BankLoadError(
                f"--rows patterns match nothing: {', '.join(unmatched)}"
            )
        return selected


def load_bank(bank_root: str | Path) -> LoadedBank:
    root = Path(bank_root)
    if not root.is_dir():
        raise BankLoadError(f"Bank root does not exist: {root}")

    config_path = root / CONFIG_FILENAME
    if not config_path.is_file():
        raise BankLoadError(
            f"{root} has no {CONFIG_FILENAME} — the eval config lives "
            f"beside the bank, never in pack config."
        )
    config = _load_config(config_path)

    bank_dir = root / BANK_DIRNAME
    if not bank_dir.is_dir():
        raise BankLoadError(f"{root} has no {BANK_DIRNAME}/ directory.")
    files = sorted(bank_dir.glob("*.yaml"))
    if not files:
        raise BankLoadError(f"{bank_dir} contains no *.yaml row files.")

    rows: list[BankRow] = []
    for path in files:
        rows.extend(_load_rows(path))

    _validate(root, rows)
    return LoadedBank(root, config, rows)


def _load_config(path: Path) -> EvalConfig:
    raw = _read_yaml(path)
    try:
        return EvalConfig.model_validate(raw)
    except ValidationError as exc:
        raise BankLoadError(f"{path} failed validation:\n{_summarize(exc)}")


def _load_rows(path: Path) -> list[BankRow]:
    raw = _read_yaml(path)
    if not isinstance(raw, list):
        raise BankLoadError(
            f"{path} must contain a YAML list of rows, got "
            f"{type(raw).__name__}."
        )
    rows = []
    for index, item in enumerate(raw):
        try:
            rows.append(BankRow.model_validate(item))
        except ValidationError as exc:
            label = (
                item.get("id", f"item {index}")
                if isinstance(item, dict)
                else f"item {index}"
            )
            raise BankLoadError(
                f"{path}, row {label} failed validation:\n{_summarize(exc)}"
            )
    return rows


def _validate(root: Path, rows: list[BankRow]) -> None:
    seen: dict[str, str] = {}
    problems: list[str] = []
    for row in rows:
        if row.id in seen:
            problems.append(f"duplicate row id {row.id}")
        seen[row.id] = row.id
        if row.gold is not None and not (root / row.gold).is_file():
            problems.append(f"row {row.id}: gold script {row.gold} not found")
        if row.threshold is not None and not 0 < row.threshold <= 1:
            problems.append(
                f"row {row.id}: threshold {row.threshold} outside (0, 1]"
            )

    pairs: dict[str, list[str]] = {}
    for row in rows:
        if row.route_pair is not None:
            pairs.setdefault(row.route_pair, []).append(row.id)
    for pair, members in sorted(pairs.items()):
        if len(members) < 2:
            problems.append(
                f"route_pair {pair!r} has a single member ({members[0]}) — "
                f"a pair assertion needs at least two rows"
            )

    if problems:
        raise BankLoadError(
            "Bank validation failed:\n"
            + "\n".join(f"  - {problem}" for problem in problems)
        )


GOLD_DIRNAME = "gold"


def _hashed_files(root: Path) -> list[Path]:
    """eval.yaml, bank/*.yaml, gold/*.py — everything that decides what
    a row asks, expects, or referees against."""
    files = [root / CONFIG_FILENAME]
    files.extend((root / BANK_DIRNAME).glob("*.yaml"))
    files.extend((root / GOLD_DIRNAME).glob("*.py"))
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def _bank_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _hashed_files(root):
        content = path.read_bytes()
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()[:16]


def _read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BankLoadError(f"{path} is not valid YAML: {exc}")


def _summarize(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        location = " -> ".join(str(part) for part in err["loc"]) or "(top)"
        lines.append(f"  {location}: {err['msg']}")
    return "\n".join(lines)
