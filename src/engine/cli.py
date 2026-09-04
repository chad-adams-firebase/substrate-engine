"""Engine CLI: `uv run engine <info|convert|generate|validate|tool>`.

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

Path convention: flag values (--pack, --sqlite, --source, --out)
resolve against the invoker's current directory, like any Unix tool;
paths inside a pack's config.yaml resolve against the pack directory.
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
        "--sqlite",
        type=_cwd_path,
        help="Override generation.source_sqlite from config. Resolved "
        "relative to the current directory (config values stay "
        "pack-relative).",
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
        "--source",
        type=_cwd_path,
        help="Override the source_code adapter's repo root. Resolved "
        "relative to the current directory (config values stay "
        "pack-relative).",
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

    tool = subparsers.add_parser(
        "tool",
        help="Invoke one registered tool by hand and inspect its envelope.",
    )
    tool.add_argument("--pack", required=True)
    tool.add_argument(
        "name", help="Tool name (run_sql, app_primer, ...); `engine info` lists them."
    )
    tool.add_argument(
        "--args",
        default="{}",
        help='Tool arguments as a JSON object, e.g. \'{"table": "invoices"}\'.',
    )
    tool.add_argument(
        "--evidence",
        action="store_true",
        help="Print the full invocation envelope (evidence bundle included) "
        "instead of just the output.",
    )

    ask = subparsers.add_parser(
        "ask",
        help="Ask a question through the full graph: route, tools, draft, "
        "verify.",
        epilog="Exit codes: 0 verified answer · 1 error · 2 unverified "
        "answer · 3 refuse · 4 clarify · 5 escalate.",
    )
    ask.add_argument("--pack", required=True)
    ask.add_argument("question", help="A plain-English question.")
    ask.add_argument(
        "--conversation",
        type=int,
        help="Continue this conversation id; omitted starts a new one in "
        "the scratch workspace.",
    )
    ask.add_argument(
        "--json",
        action="store_true",
        help="Print the full TurnResult as JSON instead of rendered text.",
    )
    ask.add_argument(
        "--show-evidence",
        action="store_true",
        help="Append the turn's evidence bundle JSON after the answer.",
    )
    ask.add_argument(
        "--show-verdict",
        action="store_true",
        help="Append the verifier verdict (claim-level detail) after the "
        "answer.",
    )

    serve = subparsers.add_parser(
        "serve",
        help="Serve the chat UI: the Flask shell over the same ask path "
        "(status trail streams live; the outcome arrives verified).",
    )
    serve.add_argument("--pack", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=5000)

    turns = subparsers.add_parser(
        "turns",
        help="Inspect turn provenance: conversations, per-turn §12 rows, "
        "evidence bundles.",
    )
    turns.add_argument("--pack", required=True)
    turns.add_argument(
        "--conversation", type=int, help="List this conversation's turns."
    )
    turns.add_argument(
        "--turn", type=int, help="Show one turn's full provenance row."
    )
    turns.add_argument(
        "--evidence",
        action="store_true",
        help="With --turn: also print the resolved evidence bundle.",
    )

    store_cmd = subparsers.add_parser(
        "store",
        help="One-shot maintenance of the pack's work store.",
    )
    store_sub = store_cmd.add_subparsers(dest="store_command", required=True)
    backfill = store_sub.add_parser(
        "backfill-questions",
        help="Recover the question of every turn_log row written before "
        "the log kept it (Phase 5 Block 3), from the conversation's "
        "checkpoint history, so past conversations read back whole.",
        epilog="Reads the conversation's checkpoint history — today's "
        "turn records, or the user/assistant message pairs a store "
        "written before Phase 5 Block 4 holds, which upgrade on read.",
    )
    backfill.add_argument("--pack", required=True)
    backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be recovered; write nothing.",
    )

    eval_cmd = subparsers.add_parser(
        "eval",
        help="Phase 4b answer-verification harness: run the bank live, "
        "grade the report offline.",
    )
    eval_sub = eval_cmd.add_subparsers(dest="eval_command", required=True)

    eval_run = eval_sub.add_parser(
        "run",
        help="Execute bank rows through the real ask path (needs "
        "OPENROUTER_API_KEY); emits a JSONL report.",
        epilog="Appends one record per (row, rep) as it completes; "
        "interrupt freely and continue with --resume.",
    )
    eval_run.add_argument(
        "--bank", required=True, help="Bank root (e.g. evals/invoiceguard)."
    )
    eval_run.add_argument(
        "--pack",
        help="Pack directory; defaults to the pack path in the bank's "
        "eval.yaml.",
    )
    eval_run.add_argument(
        "--out", required=True, type=_cwd_path, help="Report JSONL path."
    )
    eval_run.add_argument(
        "--runs", type=int, help="Repetitions per row (default: eval.yaml)."
    )
    eval_run.add_argument(
        "--rows",
        help="Comma-separated row ids; trailing * globs allowed "
        "(e.g. B5,C1,MT*).",
    )
    eval_run.add_argument(
        "--resume",
        action="store_true",
        help="Continue an interrupted report, skipping recorded "
        "(row, rep) keys.",
    )

    eval_grade = eval_sub.add_parser(
        "grade",
        help="Grade a run report fully offline (no LLM): execute gold "
        "scripts against the world, evaluate assertions, render the "
        "verdict table.",
        epilog="Exit codes: 0 pass · 1 error · 2 threshold failures · "
        "3 bank rot · 4 wrong-but-verified invariant breach.",
    )
    eval_grade.add_argument(
        "--bank", required=True, help="Bank root (e.g. evals/invoiceguard)."
    )
    eval_grade.add_argument(
        "--report",
        type=_cwd_path,
        help="Run report JSONL (omit with --check-gold).",
    )
    eval_grade.add_argument(
        "--pack",
        help="Pack directory; defaults to the pack path in the bank's "
        "eval.yaml.",
    )
    eval_grade.add_argument(
        "--out", help="Also write the rendered text report to this file."
    )
    eval_grade.add_argument(
        "--json",
        action="store_true",
        help="Print the GradeReport as JSON instead of rendered text.",
    )
    eval_grade.add_argument(
        "--check-gold",
        action="store_true",
        dest="check_gold",
        help="Execute every gold script and compare against committed "
        "expectations (bank-rot detector); needs no report.",
    )

    eval_exposure = eval_sub.add_parser(
        "exposure",
        help="Replay today's guards (the Verifier's run_sql plausibility "
        "suite and the three lints) over every executed statement in a "
        "committed report, fully offline; list every hit with its "
        "attribution.",
        epilog="The guard pass's rule: a new bound or lint is run here "
        "against the latest committed report before it lands, and the "
        "change states its hit count and every hit's attribution.",
    )
    eval_exposure.add_argument(
        "--bank", required=True, help="Bank root (e.g. evals/invoiceguard)."
    )
    eval_exposure.add_argument(
        "--report", type=_cwd_path, help="Run report JSONL."
    )
    eval_exposure.add_argument(
        "--work-store",
        action="store_true",
        help="Instead of a report: the pack's configured work store — the "
        "browser's turns, measured like a report. Requires --conversation.",
    )
    eval_exposure.add_argument(
        "--conversation",
        action="append",
        dest="conversations",
        type=int,
        metavar="ID",
        help="With --work-store: the conversation to replay (repeatable).",
    )
    eval_exposure.add_argument(
        "--pack",
        help="Pack directory; defaults to the pack path in the bank's "
        "eval.yaml.",
    )
    eval_exposure.add_argument(
        "--check",
        action="append",
        dest="checks",
        metavar="NAME",
        help="Only this check (repeatable): run_sql.<finding> or "
        "lint.fan_out / lint.enum_literal / lint.interval_arithmetic. "
        "A requested check with no hits is listed at zero.",
    )
    eval_exposure.add_argument(
        "--out", help="Also write the rendered text to this file."
    )
    eval_exposure.add_argument(
        "--json",
        action="store_true",
        help="Print the ExposureReport as JSON instead of rendered text.",
    )

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
        if args.command == "tool":
            return _tool(args.pack, args.name, args.args, args.evidence)
        if args.command == "ask":
            return _ask(
                args.pack,
                args.question,
                args.conversation,
                as_json=args.json,
                show_evidence=args.show_evidence,
                show_verdict=args.show_verdict,
            )
        if args.command == "serve":
            return _serve(args.pack, args.host, args.port)
        if args.command == "turns":
            return _turns(args.pack, args.conversation, args.turn, args.evidence)
        if args.command == "store" and args.store_command == "backfill-questions":
            return _store_backfill_questions(args.pack, args.dry_run)
        if args.command == "eval" and args.eval_command == "run":
            return _eval_run(args)
        if args.command == "eval" and args.eval_command == "grade":
            return _eval_grade(args)
        if args.command == "eval" and args.eval_command == "exposure":
            return _eval_exposure(args)
    except (PackLoadError, UnknownAdapterError, AdapterBuildError, CliError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2  # unreachable while `required=True`, kept for safety


class CliError(Exception):
    """A subcommand cannot proceed; the message tells the pack author
    what to fix."""


def _cwd_path(value: str) -> str:
    """CLI path flags resolve against the invoker's cwd (standard
    tool behavior); paths inside config.yaml stay pack-relative.
    Resolving here, at the argparse boundary, keeps the two origins
    from ever mixing."""
    return str(Path(value).resolve())


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


def _tool(pack_dir: str, name: str, args_json: str, show_evidence: bool) -> int:
    import json

    from engine.runtime.tools import ToolBuildError, build_tools
    from engine.tools.registry import UnknownToolError

    try:
        arguments = json.loads(args_json)
    except json.JSONDecodeError as exc:
        raise CliError(f"--args is not valid JSON: {exc}")
    if not isinstance(arguments, dict):
        raise CliError("--args must be a JSON object.")

    pack = load_pack(pack_dir)
    try:
        registry = build_tools(pack, build(pack))
        invocation = registry.invoke(name, arguments)
    except (ToolBuildError, UnknownToolError) as exc:
        raise CliError(str(exc))

    if show_evidence:
        print(json.dumps(invocation.model_dump(mode="json"), indent=2))
    elif invocation.status == "ok":
        print(json.dumps(invocation.output.model_dump(mode="json"), indent=2))
    else:
        print(f"error: {invocation.error}", file=sys.stderr)
    return 0 if invocation.status == "ok" else 1


def _eval_run(args) -> int:
    from engine.eval.bank import BankLoadError, load_bank
    from engine.eval.runner import RunnerError, run_bank

    try:
        bank = load_bank(args.bank)
        pack_dir = (
            Path(args.pack)
            if args.pack
            else (bank.root / bank.config.pack).resolve()
        )
        return run_bank(
            bank,
            pack_dir,
            Path(args.out),
            runs=args.runs,
            rows=args.rows.split(",") if args.rows else None,
            resume=args.resume,
        )
    except (BankLoadError, RunnerError) as exc:
        raise CliError(str(exc))


def _eval_grade(args) -> int:
    import json

    from engine.eval.bank import BankLoadError, load_bank
    from engine.eval.gold import check_gold
    from engine.eval.cardinality import check_pack_cardinalities
    from engine.eval.grade import GradeError, grade
    from engine.eval.report import render, render_gold_checks
    from engine.eval.runner import RunnerError, load_report
    from engine.eval.world import World, WorldError

    try:
        bank = load_bank(args.bank)
        pack_dir = (
            Path(args.pack)
            if args.pack
            else (bank.root / bank.config.pack).resolve()
        )
        world = World.from_pack(pack_dir)

        if args.check_gold:
            checks = check_gold(bank, world)
            # The map's one_to_one_when declarations face the same world
            # as the gold scripts: a lifecycle fact the lint vouches on
            # is a tripwire here, not a coincidence of the fixture.
            cardinalities = check_pack_cardinalities(pack_dir, world)
            text = render_gold_checks(checks, cardinalities)
            print(text, end="")
            if args.out is not None:
                Path(args.out).write_text(text, encoding="utf-8", newline="\n")
            healthy = all(c.status == "ok" for c in checks) and all(
                c.status == "ok" for c in cardinalities
            )
            return 0 if healthy else 3

        if not args.report:
            raise CliError("--report is required (or pass --check-gold).")
        header, records = load_report(Path(args.report))
        result = grade(
            bank,
            header,
            records,
            world,
            pack_root=pack_dir,
            report_path=Path(args.report).name,
        )
        if args.json:
            print(json.dumps(result.model_dump(mode="json"), indent=2))
        else:
            text = render(result)
            print(text, end="")
            if args.out is not None:
                Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        return result.exit_code()
    except (BankLoadError, RunnerError, GradeError, WorldError) as exc:
        raise CliError(str(exc))


def _eval_exposure(args) -> int:
    import json

    from engine.eval.bank import BankLoadError, load_bank
    from engine.eval.exposure import (
        ExposureError,
        check_world,
        expose,
        render_exposure,
        work_store_statements,
    )
    from engine.eval.grade import pack_world_manifests
    from engine.eval.runner import RunnerError, load_report
    from engine.ports.substrate_store import SubstrateStoreError
    from engine.runtime.container import AdapterBuildError, build_port

    if (args.report is None) == (not args.work_store):
        raise CliError("Give exactly one of --report or --work-store.")
    if args.work_store and not args.conversations:
        raise CliError("--work-store needs at least one --conversation ID.")
    try:
        bank = load_bank(args.bank)
        pack_dir = (
            Path(args.pack)
            if args.pack
            else (bank.root / bank.config.pack).resolve()
        )
        pack = load_pack(pack_dir)
        store = build_port(pack, PortName.SUBSTRATE_STORE)
        world = pack_world_manifests(pack_dir)
        if args.work_store:
            work_store = build_port(pack, PortName.WORK_STORE)
            work_store.ensure_schema()
            source = work_store_statements(work_store, args.conversations, world)
            label = "work store · conversation " + ", ".join(
                str(c) for c in args.conversations
            )
        else:
            header, source = load_report(Path(args.report))
            check_world(header, world)
            label = Path(args.report).name
        result = expose(
            source,
            stats=store.stats(),
            dictionary=store.dictionary(),
            dictionary_map=store.dictionary_map(),
            settings=pack.config.verifier.plausibility,
            checks=args.checks,
            report_path=label,
        )
    except (
        BankLoadError,
        RunnerError,
        ExposureError,
        AdapterBuildError,
        SubstrateStoreError,
        PackLoadError,
    ) as exc:
        raise CliError(str(exc))
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        text = render_exposure(result)
        print(text, end="")
        if args.out is not None:
            Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    return 0


def _build_session(pack_dir: str, listener):
    """Seam for CLI tests: monkeypatch this to inject a scripted
    session. Returns (session, ports)."""
    from engine.runtime.harness import build_harness
    from engine.runtime.tools import ToolBuildError, build_tools

    pack = load_pack(pack_dir)
    ports = build(pack)
    try:
        registry = build_tools(pack, ports)
        session = build_harness(pack, ports, registry, listener)
    except ToolBuildError as exc:
        raise CliError(str(exc))
    return session, ports


def _print_status(event) -> None:
    marker = "·" if event.phase == "start" else "  ✓"
    print(f"{marker} {event.detail}", file=sys.stderr)


def _ask(
    pack_dir: str,
    question: str,
    conversation_id: int | None,
    *,
    as_json: bool,
    show_evidence: bool,
    show_verdict: bool,
) -> int:
    import json

    from engine.harness.outcomes import exit_code_of, reading_line
    from engine.harness.session import UnknownConversationError

    session, ports = _build_session(pack_dir, _print_status)
    try:
        result = session.ask(question, conversation_id=conversation_id)
    except UnknownConversationError as exc:
        raise CliError(str(exc))

    print(
        f"conversation {result.conversation_id} · turn {result.turn}",
        file=sys.stderr,
    )
    outcome = result.outcome
    exit_code = exit_code_of(outcome)

    if as_json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return exit_code

    if outcome.kind == "answer":
        if outcome.verification == "unverified":
            print("[UNVERIFIED] This answer could not be fully verified "
                  "against its evidence.")
        if outcome.body.kind == "table":
            from engine.harness.render import render_table_text

            print(render_table_text(outcome.body.table))
            if reading_line(outcome.body):
                print(f"\n{reading_line(outcome.body)}")
            if outcome.body.caption:
                print(f"\n({outcome.body.caption})")
        else:
            print(outcome.body.text)
    elif outcome.kind == "refuse":
        print(f"REFUSED: {outcome.reason}")
        if outcome.what_would_work:
            print(f"What would work: {outcome.what_would_work}")
        if outcome.detail:
            print(f"Detail: {outcome.detail}")
    elif outcome.kind == "clarify":
        print(f"CLARIFY: {outcome.question}")
    else:
        print(f"ESCALATED: {outcome.reason}")

    if show_verdict and result.verdict is not None:
        print("\n--- verdict ---")
        print(json.dumps(result.verdict.model_dump(mode="json"), indent=2))
    if show_evidence and result.evidence_bundle_ref is not None:
        from engine.config.models import PortName as _PortName

        store = ports.get(_PortName.WORK_STORE)
        payload = store.load_evidence_bundle(result.evidence_bundle_ref)
        print(f"\n--- evidence {result.evidence_bundle_ref} ---")
        print(payload)
    return exit_code


def _serve(pack_dir: str, host: str, port: int) -> int:
    """The web layer gets the same four-line composition the CLI's ask
    path uses — load, build ports, build tools, build harness — and
    only the resolved objects; it never sees an adapter."""
    from engine.web.app import create_app

    session, ports = _build_session(pack_dir, None)
    pack = load_pack(pack_dir)
    app = create_app(
        session,
        ports.get(PortName.WORK_STORE),
        ports.get(PortName.IDENTITY),
        ui=pack.config.ui,
        pack_name=pack.config.name,
        context=pack.config.harness.context,
    )
    print(f"serving {pack.config.name} on http://{host}:{port}", file=sys.stderr)
    # threaded: the turn runs on a worker thread while the request
    # thread streams. No reloader: it forks two processes, i.e. two
    # sessions over one work.db.
    app.run(host=host, port=port, threaded=True, use_reloader=False)
    return 0


def _store_backfill_questions(pack_dir: str, dry_run: bool) -> int:
    """engine store backfill-questions: the 18 rows (dev store,
    2026-09-02) that render "(question not recorded)" have their
    question in the checkpoint thread the conversation id names. A
    verb, not ensure_schema: ensure_schema runs on every request and
    stays a cheap idempotent DDL step, while this opens the
    checkpointer, decodes history, and says once what it recovered."""
    from engine.harness.graph import question_of_turn

    pack = load_pack(pack_dir)
    ports = build(pack)
    store = ports.get(PortName.WORK_STORE)
    store.ensure_schema()
    worklist = store.turns_without_question()
    if not worklist:
        print("Every turn_log row has its question; nothing to backfill.")
        return 0
    saver = store.checkpointer()
    recovered = missing = 0
    try:
        histories: dict[int, list | None] = {}
        for conversation_id, turn in worklist:
            if conversation_id not in histories:
                state = saver.get(
                    {"configurable": {"thread_id": str(conversation_id), "checkpoint_ns": ""}}
                )
                values = state.get("channel_values", {}) if state else {}
                histories[conversation_id] = values.get("history")
            history = histories[conversation_id]
            question = question_of_turn(history, turn) if history else None
            if question is None:
                missing += 1
                print(
                    f"conversation {conversation_id} turn {turn}: no checkpoint "
                    "history for this turn — left as is"
                )
                continue
            recovered += 1
            verb = "would recover" if dry_run else "recovered"
            print(f"conversation {conversation_id} turn {turn}: {verb} {question[:60]!r}")
            if not dry_run:
                store.set_turn_question(conversation_id, turn, question)
    finally:
        connection = getattr(saver, "conn", None)
        if connection is not None:
            connection.close()
    summary = "would recover" if dry_run else "recovered"
    print(f"{summary} {recovered} question(s); {missing} without history.")
    return 0


def _turns(
    pack_dir: str,
    conversation_id: int | None,
    turn: int | None,
    show_evidence: bool,
) -> int:
    import json

    pack = load_pack(pack_dir)
    ports = build(pack)
    store = ports.get(PortName.WORK_STORE)
    identity = ports.get(PortName.IDENTITY)
    store.ensure_schema()

    if conversation_id is None:
        owner = identity.current_user().username
        for workspace in store.list_workspaces(owner):
            for conversation in store.list_conversations(workspace.id):
                count = len(store.list_turn_logs(conversation.id))
                print(
                    f"{conversation.id:>4}  {conversation.created_at:%Y-%m-%d %H:%M}  "
                    f"{count} turn(s)  {conversation.title}"
                )
        return 0

    entries = store.list_turn_logs(conversation_id)
    if not entries:
        raise CliError(f"No turns logged for conversation {conversation_id}.")

    if turn is None:
        for entry in entries:
            verdict = "-"
            if entry.verifier_verdict:
                verdict = json.loads(entry.verifier_verdict).get(
                    "disposition", "?"
                )
            tools = ",".join(entry.tools_used) or "-"
            question = entry.question[:60] + ("…" if len(entry.question) > 60 else "")
            print(
                f"turn {entry.turn:>2}  {entry.actor}  {entry.action}  "
                f"tools={tools}  verdict={verdict}  "
                f"evidence={entry.evidence_bundle_ref or '-'}"
                + (f"  {question!r}" if question else "")
            )
        return 0

    matching = [e for e in entries if e.turn == turn]
    if not matching:
        raise CliError(
            f"No turn {turn} in conversation {conversation_id}."
        )
    (entry,) = matching[:1]
    print(json.dumps(entry.model_dump(mode="json"), indent=2))
    if show_evidence and entry.evidence_bundle_ref:
        payload = store.load_evidence_bundle(entry.evidence_bundle_ref)
        print(f"\n--- evidence {entry.evidence_bundle_ref} ---")
        print(payload)
    return 0


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
