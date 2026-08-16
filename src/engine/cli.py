"""Engine CLI: `uv run engine <info|convert|generate|validate>`.

The composition root for pack tooling: subcommands load a pack, build
its adapters through the DI container, and inject the resulting ports
into generators and the validator — the only place that wiring
happens, so the generators themselves never see an adapter.

The documented pack-build flow (Phase 2):

    uv run engine convert  --pack packs/invoiceguard
    uv run engine generate --pack packs/invoiceguard
    uv run engine validate --pack packs/invoiceguard

argparse over a CLI framework: four subcommands still do not justify
a dependency (CLAUDE.md: clear beats clever, and every dep must clear
the wheel rule).
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from engine.config.models import PortName
from engine.config.pack_loader import LoadedPack, PackLoadError, load_pack
from engine.runtime.container import AdapterBuildError, ResolvedPorts, build
from engine.runtime.registry import UnknownAdapterError

SUBSTRATE_GENERATORS = ("dictionary", "stats", "ckg")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="engine",
        description="Configurable crowdsourced knowledge engine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    info = subparsers.add_parser(
        "info", help="Load a pack and report what it enables and resolves."
    )
    info.add_argument("--pack", required=True, help="Path to a pack directory.")

    convert = subparsers.add_parser(
        "convert",
        help="Produce the pack's DuckDB database from the target's SQLite.",
    )
    convert.add_argument("--pack", required=True)
    convert.add_argument(
        "--sqlite", help="Override generation.source_sqlite from config."
    )
    convert.add_argument(
        "--seed", type=int, help="Override generation.simulation_seed."
    )

    generate = subparsers.add_parser(
        "generate",
        help="Run the substrate generators and write the pack's substrates.",
    )
    generate.add_argument("--pack", required=True)
    generate.add_argument(
        "--source", help="Override the source_code adapter's repo root."
    )
    generate.add_argument(
        "--only",
        help=f"Comma-separated subset of {','.join(SUBSTRATE_GENERATORS)}.",
    )
    generate.add_argument(
        "--check",
        action="store_true",
        help="Regenerate to a scratch directory and byte-compare against "
        "the pack's committed substrates instead of writing.",
    )

    validate = subparsers.add_parser(
        "validate", help="Run the conformance validator against the pack."
    )
    validate.add_argument("--pack", required=True)
    validate.add_argument("--out", help="Also write the report to this file.")

    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            return _info(args.pack)
        if args.command == "convert":
            return _convert(args.pack, args.sqlite, args.seed)
        if args.command == "generate":
            return _generate(args.pack, args.source, args.only, args.check)
        if args.command == "validate":
            return _validate(args.pack, args.out)
    except (PackLoadError, UnknownAdapterError, AdapterBuildError, CliError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2  # unreachable while `required=True`, kept for safety


class CliError(Exception):
    """A subcommand cannot proceed; the message tells the pack author
    what to fix."""


def _pack_path(pack_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else pack_root / path


def _require_generation(pack: LoadedPack):
    generation = pack.config.generation
    if generation is None:
        raise CliError(
            f"{pack.root}/config.yaml has no 'generation:' section — "
            f"convert/generate need one (see packs/invoiceguard)."
        )
    return generation


def _build_ports(pack: LoadedPack, source_override: str | None) -> ResolvedPorts:
    if source_override is not None:
        selection = pack.config.adapters.get(PortName.SOURCE_CODE)
        if selection is None:
            raise CliError("--source given but the pack configures no source_code adapter.")
        selection.settings = {**selection.settings, "root": source_override}
    return build(pack)


def _verify_pinned_sha(pack: LoadedPack, ports: ResolvedPorts) -> None:
    """When the source root is a git clone, the declared pin must match
    its HEAD — extracting at the wrong commit silently invalidates
    every line reference (Brief §5)."""
    source = ports.get(PortName.SOURCE_CODE)
    root = Path(source.settings.root)
    if not (root / ".git").exists():
        return
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != source.commit_sha:
        raise CliError(
            f"source clone {root} is at {head[:12]} but the pack pins "
            f"{source.commit_sha[:12]} — check out the pinned commit or "
            f"update the pack's commit_sha deliberately."
        )


def _convert(pack_dir: str, sqlite_override: str | None, seed_override: int | None) -> int:
    from engine.packtools.convert_sqlite import convert
    from engine.substrates.manifest import save_manifest

    pack = load_pack(pack_dir)
    generation = _require_generation(pack)
    sql_selection = pack.config.adapters.get(PortName.SQL)
    if sql_selection is None or "database" not in sql_selection.settings:
        raise CliError("the pack's sql adapter declares no database path.")
    source_selection = pack.config.adapters.get(PortName.SOURCE_CODE)
    commit_sha = (
        source_selection.settings.get("commit_sha") if source_selection else None
    )

    sqlite_path = _pack_path(
        pack.root, sqlite_override or generation.source_sqlite
    )
    duckdb_path = _pack_path(pack.root, sql_selection.settings["database"])
    seed = seed_override if seed_override is not None else generation.simulation_seed

    manifest = convert(
        sqlite_path,
        duckdb_path,
        source_commit_sha=commit_sha,
        simulation_seed=seed,
    )
    save_manifest(
        pack.root / "substrates" / "manifests" / "sqlite_convert.json", manifest
    )
    print(
        f"converted {sqlite_path.name} -> {duckdb_path} "
        f"({len(manifest.source_tables)} tables, manifest {manifest.manifest_id})"
    )
    return 0


def _generate(
    pack_dir: str, source_override: str | None, only: str | None, check: bool
) -> int:
    from engine.generators.ckg import CkgGenerator
    from engine.generators.dictionary import DictionaryGenerator
    from engine.generators.stats import StatsGenerator
    from engine.substrates.jsonl import write_substrate
    from engine.substrates.manifest import save_manifest
    from engine.substrates.pack_data import (
        load_components,
        load_dictionary_overlay,
        load_membership_overlay,
        load_primer,
    )

    selected = set((only or ",".join(SUBSTRATE_GENERATORS)).split(","))
    unknown = selected - set(SUBSTRATE_GENERATORS)
    if unknown:
        raise CliError(f"--only names unknown generators: {sorted(unknown)}")

    pack = load_pack(pack_dir)
    generation = _require_generation(pack)
    ports = _build_ports(pack, source_override)
    _verify_pinned_sha(pack, ports)

    sql = ports.get(PortName.SQL)
    source = ports.get(PortName.SOURCE_CODE)
    identity = ports.get(PortName.IDENTITY).current_user()
    commit_sha = source.commit_sha

    outputs: dict[str, list] = {}
    manifests: dict[str, object] = {}
    warnings: list[str] = []
    errors: list[str] = []

    if "dictionary" in selected:
        overlay = load_dictionary_overlay(
            pack.root / "overlays" / "dictionary.jsonl"
        )
        rows, manifest, generator_warnings = DictionaryGenerator(
            sql, identity, generation
        ).generate(overlay, source_commit_sha=commit_sha)
        outputs["dictionary"] = rows
        manifests["dictionary"] = manifest
        warnings.extend(generator_warnings)

    if "stats" in selected:
        rows, manifest = StatsGenerator(sql, identity, generation).generate(
            source_commit_sha=commit_sha
        )
        outputs["univariate_stats"] = rows
        manifests["stats"] = manifest

    if "ckg" in selected:
        components = load_components(pack.root / "components.yaml")
        membership_overlay = load_membership_overlay(
            pack.root / "overlays" / "component_memberships.jsonl"
        )
        primer = load_primer(pack.root / "primer.md")
        extraction = CkgGenerator(source, generation).generate(
            components, membership_overlay, primer
        )
        outputs["ckg_nodes"] = extraction.nodes
        outputs["ckg_edges"] = extraction.edges
        outputs["ckg_conditionals"] = extraction.conditionals
        outputs["component_memberships"] = extraction.memberships
        manifests["ckg"] = extraction.manifest
        warnings.extend(extraction.warnings)
        errors.extend(extraction.errors)

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1

    if check:
        return _check_against_committed(pack.root, outputs)

    substrates_dir = pack.root / "substrates"
    for substrate, rows in outputs.items():
        path = write_substrate(substrates_dir, substrate, rows)
        print(f"wrote {path} ({len(rows)} rows)")
    for name, manifest in manifests.items():
        save_manifest(
            substrates_dir / "manifests" / f"{name}.json", manifest
        )
    return 0


def _check_against_committed(pack_root: Path, outputs: dict[str, list]) -> int:
    from engine.substrates.jsonl import write_substrate

    differences = []
    with tempfile.TemporaryDirectory() as scratch:
        for substrate, rows in outputs.items():
            fresh = write_substrate(Path(scratch), substrate, rows)
            committed = pack_root / "substrates" / f"{substrate}.jsonl"
            if not committed.is_file():
                differences.append(f"{substrate}: not present in the pack")
            elif fresh.read_bytes() != committed.read_bytes():
                differences.append(f"{substrate}: differs from the pack")
    if differences:
        for difference in differences:
            print(f"check: {difference}", file=sys.stderr)
        return 1
    print(f"check: {len(outputs)} substrate files byte-identical")
    return 0


def _validate(pack_dir: str, out: str | None) -> int:
    from engine.validate.conformance import ConformanceValidator
    from engine.validate.report import render

    pack = load_pack(pack_dir)
    generation = _require_generation(pack)
    ports = build(pack)
    validator = ConformanceValidator(
        ports.get(PortName.SQL),
        ports.get(PortName.SOURCE_CODE),
        ports.get(PortName.IDENTITY).current_user(),
        generation.component_id_prefix,
    )
    report = validator.validate(pack.root, pack.config.name)
    text = render(report)
    print(text, end="")
    if out is not None:
        Path(out).write_text(text, encoding="utf-8", newline="\n")
    return 0 if report.passed else 1


def _info(pack_dir: str) -> int:
    pack = load_pack(pack_dir)
    ports = build(pack)

    config = pack.config
    print(f"Pack: {config.name}")
    if config.description:
        print(f"  {config.description}")
    print()
    print(f"Substrates enabled ({len(config.substrates)}):")
    for substrate in config.substrates:
        print(f"  - {substrate}")
    print()
    print(f"Tools enabled ({len(config.tools)}):")
    for tool in config.tools:
        print(f"  - {tool}")
    print()
    print("Ports:")
    resolved = ports.configured()
    for port in PortName:
        if port in resolved:
            adapter = resolved[port]
            settings = getattr(adapter, "settings", None)
            detail = (
                f" ({', '.join(f'{k}={v}' for k, v in settings.model_dump().items())})"
                if settings is not None
                else ""
            )
            print(f"  {port.value:<15} -> {type(adapter).__name__}{detail}")
        else:
            print(f"  {port.value:<15} -> not configured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
