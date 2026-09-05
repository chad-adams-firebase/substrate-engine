"""engine eval run: execute bank rows through the real ask path.

One JSONL report per sweep: a determinism header first, then one
self-contained record per (row, rep) — outcome, answer body, inlined
evidence, verdict, status events, cost — appended and fsynced as each
rep completes, so a 40-row × 5-rep session survives interruption and
--resume picks up exactly the missing (row, rep) keys.

The runner measures; it never grades. Grading is the offline half's
job, on a machine with no LLM egress.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from engine.config.models import PortName
from engine.config.pack_loader import LoadedPack, load_pack
from engine.eval.bank import LoadedBank
from engine.eval.metering import MeteringLLM
from engine.eval.models import (
    BankRow,
    RunRecord,
    RunReportHeader,
    TurnRecord,
)
from engine.eval.tokens import detect, flatten_answer
from engine.harness.outcomes import exit_code_of
from engine.ports.llm import LLMTimeoutError
from engine.runtime.container import ResolvedPorts, build
from engine.tools.envelope import loads_turn_evidence

# The placeholder grammar (harness/placeholders.py) — failures reach
# provenance only as status-event details, so the runner recovers
# them by shape.
_PLACEHOLDER = re.compile(r"\{\{e\d+\.[^{}]+\}\}")

# Header fields that must match for --resume to append: same engine,
# same target world, same model, same bank, same eval config. A
# mismatch means the continuation would measure a different system.
_RESUME_KEYS = (
    "engine_sha",
    "target_sha",
    "seed",
    "world_manifests",
    "model",
    "bank_hash",
    "eval_config",
)


class RunnerError(Exception):
    """The run cannot proceed; the message says what to fix."""


def _status(line: str) -> None:
    print(line, file=sys.stderr)


def _build_session(pack_dir: Path, work_db: Path, listener):
    """Seam for tests: monkeypatch this to inject a scripted session.
    Returns (session, ports, meter). The work store is redirected to a
    throwaway file-backed database beside the report — eval turns must
    not pollute the pack's work.db, and :memory: would silently break
    the checkpointer (separate connections)."""
    from engine.runtime.harness import build_harness
    from engine.runtime.tools import ToolBuildError, build_tools

    pack = load_pack(pack_dir)
    selection = pack.config.adapters.get(PortName.WORK_STORE)
    if selection is None:
        raise RunnerError("the pack configures no work_store adapter.")
    selection.settings = {**selection.settings, "database": str(work_db)}

    ports = build(pack)
    meter = MeteringLLM(ports.get(PortName.LLM))
    adapters = ports.configured()
    adapters[PortName.LLM] = meter
    metered_ports = ResolvedPorts(adapters)
    try:
        registry = build_tools(pack, metered_ports)
        session = build_harness(pack, metered_ports, registry, listener)
    except ToolBuildError as exc:
        raise RunnerError(str(exc))
    return session, metered_ports, meter


def _engine_sha(root: Path | None = None) -> tuple[str, bool]:
    """(HEAD sha, dirty). Dirty means modified tracked content —
    untracked files are ignored on purpose: the runner writes its own
    report into the repo, so counting them made every run permanently
    dirty and the flag carried no information (4b findings, provenance
    note). Uncommitted engine edits still flip it."""
    if root is None:
        root = Path(__file__).resolve().parents[3]
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        porcelain = subprocess.run(
            [
                "git", "-C", str(root), "status", "--porcelain",
                "--untracked-files=no",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha, bool(porcelain)
    except (OSError, subprocess.CalledProcessError):
        return "unknown", False


def build_header(
    bank: LoadedBank,
    pack: LoadedPack,
    runs: int,
    rows_filter: list[str] | None,
) -> RunReportHeader:
    llm = pack.config.adapters.get(PortName.LLM)
    source = pack.config.adapters.get(PortName.SOURCE_CODE)
    manifests: dict[str, str] = {}
    manifest_dir = pack.root / "substrates" / "manifests"
    if manifest_dir.is_dir():
        for path in sorted(manifest_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            manifests[data.get("generator", path.stem)] = data.get(
                "manifest_id", "?"
            )
    engine_sha, dirty = _engine_sha()
    return RunReportHeader(
        engine_sha=engine_sha,
        engine_dirty=dirty,
        target_sha=(
            source.settings.get("commit_sha") if source is not None else None
        ),
        seed=(
            pack.config.generation.simulation_seed
            if pack.config.generation is not None
            else None
        ),
        world_manifests=manifests,
        model=(llm.settings.get("model", "?") if llm is not None else "?"),
        pack=pack.config.name,
        bank_hash=bank.bank_hash,
        eval_config=bank.config,
        runs_requested=runs,
        rows_filter=rows_filter,
        started_at=datetime.now(UTC),
    )


def load_report(path: Path) -> tuple[RunReportHeader, list[RunRecord]]:
    """Parse a complete report (grade's entry point too)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise RunnerError(f"{path} is empty.")
    header = RunReportHeader.model_validate_json(lines[0])
    return header, [RunRecord.model_validate_json(line) for line in lines[1:]]


def _load_resumable(out: Path, status) -> tuple[RunReportHeader, set]:
    """Existing report → (header, completed keys), truncating a
    trailing partial line left by a hard interruption."""
    lines = out.read_text(encoding="utf-8").splitlines()
    dropped = False
    while lines:
        try:
            json.loads(lines[-1])
            break
        except json.JSONDecodeError:
            lines.pop()
            dropped = True
    if not lines:
        raise RunnerError(f"{out} has no readable header line.")
    if dropped:
        status(f"warning: {out} ended mid-record; truncating the partial line")
        out.write_text(
            "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
        )
    header = RunReportHeader.model_validate_json(lines[0])
    done = set()
    for line in lines[1:]:
        record = RunRecord.model_validate_json(line)
        done.add((record.row_id, record.rep))
    return header, done


def _check_resume_header(
    existing: RunReportHeader, current: RunReportHeader
) -> None:
    old = existing.model_dump(mode="json")
    new = current.model_dump(mode="json")
    mismatched = [key for key in _RESUME_KEYS if old[key] != new[key]]
    if mismatched:
        details = "; ".join(
            f"{key}: report has {old[key]!r}, environment has {new[key]!r}"
            for key in mismatched
        )
        raise RunnerError(
            f"--resume refused — the report was produced by a different "
            f"system: {details}. Re-run against a fresh --out instead."
        )


def run_bank(
    bank: LoadedBank,
    pack_dir: Path,
    out: Path,
    *,
    runs: int | None = None,
    rows: list[str] | None = None,
    resume: bool = False,
    listener=None,
    status=_status,
) -> int:
    selected = bank.select(rows)
    runs = runs if runs is not None else bank.config.default_runs
    pack = load_pack(pack_dir)
    header = build_header(bank, pack, runs, rows)

    done: set = set()
    if out.exists():
        if not resume:
            raise RunnerError(
                f"{out} already exists — pass --resume to continue it, or "
                f"choose a new --out."
            )
        existing, done = _load_resumable(out, status)
        _check_resume_header(existing, header)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            header.model_dump_json() + "\n", encoding="utf-8", newline="\n"
        )

    pending = [
        (row, rep)
        for row in selected
        for rep in range(1, runs + 1)
        if (row.id, rep) not in done
    ]
    if not pending:
        status("nothing to do: every selected (row, rep) is already recorded")
        print(out)
        return 0

    work_db = out.with_name(out.name + ".work.db")
    session, ports, meter = _build_session(pack_dir, work_db, listener)
    work_store = ports.get(PortName.WORK_STORE)

    with out.open("a", encoding="utf-8", newline="\n") as handle:
        for row, rep in pending:
            record = _execute_rep(
                session, work_store, meter, row, rep, runs, status
            )
            handle.write(record.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    print(out)
    return 0


# A provider brownout is not an engine result. A rep whose turn raised
# the port's LLM timeout is played once more, from turn 0 in a fresh
# conversation; the record carries the attempt count (Migration
# Readiness). Any other exception, and a second timeout, are recorded
# as before: one bad turn marks the rep and the sweep goes on.
REP_ATTEMPTS = 2


def _execute_rep(
    session, work_store, meter: MeteringLLM, row: BankRow, rep: int,
    runs: int, status,
) -> RunRecord:
    started = datetime.now(UTC)
    rep_t0 = time.perf_counter()
    attempt = 0
    while True:
        attempt += 1
        turns, retry = _play_turns(
            session, work_store, meter, row, rep, runs, status,
            final=attempt >= REP_ATTEMPTS,
        )
        if not retry:
            break
        status(
            f"[{row.id} rep {rep}/{runs}] LLM timeout — replaying the rep "
            f"from turn 0 (attempt {attempt + 1}/{REP_ATTEMPTS})"
        )

    return RunRecord(
        row_id=row.id,
        rep=rep,
        started_at=started,
        wall_ms_total=int((time.perf_counter() - rep_t0) * 1000),
        attempts=attempt,
        turns=turns,
    )


def _play_turns(
    session, work_store, meter: MeteringLLM, row: BankRow, rep: int,
    runs: int, status, *, final: bool,
) -> tuple[list[TurnRecord], bool]:
    """One pass over the row's turns in one conversation. Returns
    (turns, retry): retry is True only when the port's LLM timeout
    ended the pass and another attempt is allowed — the caller plays
    the rep again. On the final attempt a timeout is recorded like any
    other error."""
    turns: list[TurnRecord] = []
    conversation_id: int | None = None

    for index, turn in enumerate(row.all_turns()):
        meter.reset()
        turn_t0 = time.perf_counter()
        try:
            result = session.ask(
                turn.question,
                conversation_id=conversation_id,
                context=row.context,
            )
        except LLMTimeoutError as exc:
            if not final:
                status(f"[{row.id} rep {rep}/{runs}] turn {index}: LLM timeout {exc}")
                return turns, True
            turns.append(_error_turn(index, turn, conversation_id, turn_t0, meter, exc))
            status(f"[{row.id} rep {rep}/{runs}] turn {index}: ERROR {exc}")
            break  # must not sink the sweep, but later turns anchor on it
        except Exception as exc:  # record and move on: one bad turn
            turns.append(_error_turn(index, turn, conversation_id, turn_t0, meter, exc))
            status(f"[{row.id} rep {rep}/{runs}] turn {index}: ERROR {exc}")
            break  # must not sink the sweep, but later turns anchor on it
        wall_ms = int((time.perf_counter() - turn_t0) * 1000)
        conversation_id = result.conversation_id

        payload = None
        substrate_versions: list[str] = []
        if result.evidence_bundle_ref is not None:
            payload = work_store.load_evidence_bundle(
                result.evidence_bundle_ref
            )
            if payload is not None:
                substrate_versions = sorted(
                    {
                        manifest_id
                        for invocation in loads_turn_evidence(payload)
                        for manifest_id in invocation.manifest_ids
                    }
                )

        details = [event.detail for event in result.events]
        exit_equiv = exit_code_of(result.outcome)
        turns.append(
            TurnRecord(
                turn_index=index,
                question=turn.question,
                conversation_id=conversation_id,
                engine_turn=result.turn,
                outcome=result.outcome,
                exit_equiv=exit_equiv,
                tools_used=result.tools_used,
                evidence_ref=result.evidence_bundle_ref,
                evidence_payload=payload,
                verdict=result.verdict,
                status_events=result.events,
                substrate_versions=substrate_versions,
                wall_ms=wall_ms,
                llm=meter.stats(),
                emitted_tokens=detect(flatten_answer(result.outcome)),
                placeholder_failures=sorted(
                    {
                        match.group()
                        for detail in details
                        for match in _PLACEHOLDER.finditer(detail)
                    }
                ),
                nudges=sum(
                    "protocol violation" in detail for detail in details
                ),
                lenient_parses=sum(
                    detail.startswith("text-form ") for detail in details
                ),
                summary=result.summary,
                summary_through_turn=result.summary_through_turn,
            )
        )
        verification = (
            f" ({result.outcome.verification})"
            if result.outcome.kind == "answer"
            else ""
        )
        status(
            f"[{row.id} rep {rep}/{runs}] exit {exit_equiv}"
            f"{verification} {wall_ms / 1000:.1f}s "
            f"{turns[-1].llm.calls} llm calls"
        )

    return turns, False


def _error_turn(
    index: int, turn, conversation_id: int | None, turn_t0: float,
    meter: MeteringLLM, exc: BaseException,
) -> TurnRecord:
    return TurnRecord(
        turn_index=index,
        question=turn.question,
        conversation_id=conversation_id,
        exit_equiv=1,
        wall_ms=int((time.perf_counter() - turn_t0) * 1000),
        llm=meter.stats(),
        error=f"{type(exc).__name__}: {exc}",
    )
