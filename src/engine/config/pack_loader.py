"""Instance-pack loader: read a pack directory, validate its config.

Every failure mode gets its own legible message, because the person
reading it may be authoring a pack at work with no agent to help —
the error text is the debugging tool (CLAUDE.md style law).
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from engine.config.models import PackConfig

CONFIG_FILENAME = "config.yaml"


class PackLoadError(Exception):
    """A pack directory could not be loaded. The message says why,
    in terms a pack author (not an engine developer) can act on."""


class LoadedPack:
    """A validated pack: its config plus the directory it came from.

    The directory travels with the config because adapter settings may
    contain paths relative to the pack root, which the container
    resolves at build time.
    """

    def __init__(self, config: PackConfig, root: Path) -> None:
        self.config = config
        self.root = root


def load_pack(pack_dir: str | Path) -> LoadedPack:
    root = Path(pack_dir)

    if not root.is_dir():
        raise PackLoadError(f"Pack directory does not exist: {root}")

    config_path = root / CONFIG_FILENAME
    if not config_path.is_file():
        raise PackLoadError(
            f"Pack directory {root} has no {CONFIG_FILENAME} — every pack "
            f"needs one (see docs/technical-build-brief-v2.md §14)."
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackLoadError(f"{config_path} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise PackLoadError(
            f"{config_path} must contain a YAML mapping at the top level, "
            f"got {type(raw).__name__}."
        )

    try:
        config = PackConfig.model_validate(raw)
    except ValidationError as exc:
        raise PackLoadError(
            f"{config_path} failed validation:\n{_summarize(exc)}"
        ) from exc

    return LoadedPack(config=config, root=root)


def _summarize(exc: ValidationError) -> str:
    """Turn pydantic's error list into pack-author-readable lines."""
    lines = []
    for err in exc.errors():
        location = " -> ".join(str(part) for part in err["loc"]) or "(top level)"
        lines.append(f"  {location}: {err['msg']}")
    return "\n".join(lines)
