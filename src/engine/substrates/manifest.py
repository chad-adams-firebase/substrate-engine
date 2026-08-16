"""Manifest construction and content-addressed manifest ids.

The manifest_id hashes the pinning tuple — generator, version, commit
SHA, seed, source tables — and deliberately excludes extracted_at.
Substrate rows reference the manifest by this id, so a rerun against
identical inputs produces identical rows; the timestamp lives only in
the manifest file itself, where a one-line diff on regeneration is an
honest record of "ran again, nothing changed".
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from engine.substrates.models import Manifest


def build_manifest(
    generator: str,
    generator_version: str,
    *,
    source_commit_sha: str | None = None,
    simulation_seed: int | None = None,
    source_tables: list[str] | None = None,
    extracted_at: datetime | None = None,
) -> Manifest:
    tables = sorted(source_tables or [])
    pinning = {
        "generator": generator,
        "generator_version": generator_version,
        "source_commit_sha": source_commit_sha,
        "simulation_seed": simulation_seed,
        "source_tables": tables,
    }
    canonical = json.dumps(pinning, sort_keys=True, separators=(",", ":"))
    manifest_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return Manifest(
        manifest_id=manifest_id,
        generator=generator,
        generator_version=generator_version,
        source_commit_sha=source_commit_sha,
        simulation_seed=simulation_seed,
        source_tables=tables,
        extracted_at=extracted_at or datetime.now(UTC),
    )


def save_manifest(path: Path, manifest: Manifest) -> None:
    payload = manifest.model_dump(mode="json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
