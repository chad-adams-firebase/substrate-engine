"""Regeneration is idempotent: two full runs from scratch, byte-equal
files (phasing Phase 2 "done"). Fresh directories, fresh conversions —
nothing shared but the inputs."""

from engine.substrates.jsonl import write_substrate

from tests.fixture_generation import build_snapshot_duckdb, generate_all


def test_full_generation_twice_is_byte_identical(tmp_path):
    file_bytes = []
    for run in ("first", "second"):
        run_dir = tmp_path / run
        run_dir.mkdir()
        duckdb_path = build_snapshot_duckdb(run_dir)
        outputs = generate_all(duckdb_path)
        file_bytes.append(
            {
                substrate: write_substrate(run_dir, substrate, rows).read_bytes()
                for substrate, rows in outputs.items()
            }
        )
    assert file_bytes[0].keys() == file_bytes[1].keys()
    for substrate in file_bytes[0]:
        assert file_bytes[0][substrate] == file_bytes[1][substrate], substrate
