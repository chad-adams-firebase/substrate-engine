"""Carve the vendored InvoiceGuard fixture snapshot (Brief §15).

Dev tooling, run manually on the Mac with the invoice-guard clone
present and simulated — never part of the test suite. Recarving is a
deliberate act: it changes the pinned fixture bytes, so every
generator expectation must be re-reviewed afterwards.

Usage:
    uv run python scripts/carve_invoiceguard_fixture.py \
        --clone ../invoice-guard --seed 42

Copies a byte-exact subset of source files (both raw-SQL sites, two
rich spine modules, the full models package), dumps a deterministic
SQL-text slice of the simulated database (schema for all tables, data
for a bounded subset — SQL text because the repo gitignores binary
databases), and records everything in fixture_manifest.json with the
clone's commit SHA and the simulation seed.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "invoiceguard_snapshot"

# Byte-exact vendored files: the two raw-SQL sites, two rich spine
# modules, and the whole models package (the CKG model index resolves
# ORM classes across it, so partial vendoring would silently shrink
# the reads/writes ground truth).
VENDORED_FILES = [
    "src/invoiceguard/models/__init__.py",
    "src/invoiceguard/models/base.py",
    "src/invoiceguard/models/config.py",
    "src/invoiceguard/models/finding.py",
    "src/invoiceguard/models/invoice.py",
    "src/invoiceguard/models/report.py",
    "src/invoiceguard/models/scheduled_task.py",
    "src/invoiceguard/models/supplier.py",
    "src/invoiceguard/models/user.py",
    "src/invoiceguard/spine/lapse_lifecycle.py",
    "src/invoiceguard/spine/queue.py",
    "src/invoiceguard/spine/rules_engine.py",
    "src/invoiceguard/platform/api/teams.py",
]

MAX_INVOICE_ID = 50

# Log-slice days (seed-42 world): the planted benchmark-outage day
# (all 30 benchmark_fallback WARNINGs) plus the following normal day —
# so did_run true/false and recent_errors full/empty are all
# exercisable offline across many loggers.
LOG_SLICE_DATES = ("2026-03-11", "2026-03-12")

# (table, WHERE clause or None for all rows, ORDER BY column). Tables
# absent from this list ship schema-only; the dictionary still covers
# them and stats record row_count=0 — deliberately exercised.
DATA_SLICES = [
    ("suppliers", None, "id"),
    ("users", None, "id"),
    ("config", "retired = 0", "id"),
    ("invoices", f"id <= {MAX_INVOICE_ID}", "id"),
    ("invoice_lines", f"invoice_id <= {MAX_INVOICE_ID}", "id"),
    ("findings", f"invoice_id <= {MAX_INVOICE_ID}", "id"),
    (
        "finding_feedback",
        f"finding_id IN (SELECT id FROM findings WHERE invoice_id <= {MAX_INVOICE_ID})",
        "id",
    ),
    ("invoice_history", f"invoice_id <= {MAX_INVOICE_ID}", "id"),
]


def sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def dump_schema(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return "".join(f"{sql};\n\n" for _, sql in rows)


def dump_data(connection: sqlite3.Connection) -> tuple[str, dict[str, int]]:
    connection.row_factory = sqlite3.Row
    statements: list[str] = []
    counts: dict[str, int] = {}
    for table, where, order in DATA_SLICES:
        clause = f" WHERE {where}" if where else ""
        rows = connection.execute(
            f"SELECT * FROM {table}{clause} ORDER BY {order}"
        ).fetchall()
        counts[table] = len(rows)
        for row in rows:
            columns = ", ".join(row.keys())
            values = ", ".join(sql_literal(row[key]) for key in row.keys())
            statements.append(f"INSERT INTO {table} ({columns}) VALUES ({values});\n")
    return "".join(statements), counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clone", required=True, help="Path to the invoice-guard clone.")
    parser.add_argument("--seed", type=int, required=True, help="Simulation seed the clone's simout was produced with.")
    parser.add_argument(
        "--db",
        default=None,
        help="Path to the simulated SQLite DB (default: <clone>/simout/invoiceguard.db).",
    )
    args = parser.parse_args()

    clone = Path(args.clone).resolve()
    db_path = Path(args.db) if args.db else clone / "simout" / "invoiceguard.db"
    if not db_path.is_file():
        print(f"error: simulated database not found: {db_path}", file=sys.stderr)
        return 1

    commit_sha = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "-C", str(clone), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        print("error: clone has uncommitted changes; carve only from a clean pin.", file=sys.stderr)
        return 1

    source_root = FIXTURE_ROOT / "source"
    if source_root.exists():
        shutil.rmtree(source_root)

    file_records = []
    for relative in VENDORED_FILES:
        origin = clone / relative
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = origin.read_bytes()
        destination.write_bytes(data)
        file_records.append(
            {"path": relative, "sha256": hashlib.sha256(data).hexdigest()}
        )

    connection = sqlite3.connect(str(db_path))
    schema_sql = dump_schema(connection)
    data_sql, counts = dump_data(connection)
    connection.close()

    db_dir = FIXTURE_ROOT / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "schema.sql").write_text(schema_sql, encoding="utf-8", newline="\n")
    (db_dir / "data.sql").write_text(data_sql, encoding="utf-8", newline="\n")

    log_path = db_path.parent / "logs" / "invoiceguard.log"
    if not log_path.is_file():
        print(f"error: simulation log not found: {log_path}", file=sys.stderr)
        return 1
    prefixes = tuple(f"ts={day}T" for day in LOG_SLICE_DATES)
    log_lines = [
        line
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefixes)
    ]
    log_text = "".join(line + "\n" for line in log_lines)
    log_dir = FIXTURE_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "invoiceguard.log").write_text(
        log_text, encoding="utf-8", newline="\n"
    )

    manifest = {
        "source_repo": "chad-adams-firebase/invoice-guard",
        "commit_sha": commit_sha,
        "simulation_seed": args.seed,
        "source_files": file_records,
        "db_slice": {
            "schema_sha256": hashlib.sha256(schema_sql.encode()).hexdigest(),
            "data_sha256": hashlib.sha256(data_sql.encode()).hexdigest(),
            "data_row_counts": counts,
        },
        "log_slice": {
            "path": "logs/invoiceguard.log",
            "criteria": (
                "lines whose ts= date is in date_range, from "
                "simout/logs/invoiceguard.log (planted benchmark-outage "
                "day plus the following normal day)"
            ),
            "date_range": list(LOG_SLICE_DATES),
            "line_count": len(log_lines),
            "sha256": hashlib.sha256(log_text.encode()).hexdigest(),
        },
        "carved_at": datetime.now(UTC).isoformat(),
    }
    (FIXTURE_ROOT / "fixture_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"carved {len(file_records)} files at {commit_sha[:12]}, rows: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
