"""The vendored snapshot is pinned: accidental edits fail loudly.

A deliberate recarve (scripts/carve_invoiceguard_fixture.py) rewrites
the manifest hashes alongside the files; anything else touching the
snapshot is corruption.
"""

import hashlib
import json
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "fixtures" / "invoiceguard_snapshot"


def load_manifest() -> dict:
    return json.loads(
        (SNAPSHOT / "fixture_manifest.json").read_text(encoding="utf-8")
    )


def test_pin_is_recorded():
    manifest = load_manifest()
    assert manifest["commit_sha"] == "761a18e9b9253870d930f1b13b3a852ce516d603"
    assert manifest["simulation_seed"] == 42


def test_vendored_files_match_their_hashes():
    manifest = load_manifest()
    assert manifest["source_files"], "manifest lists no vendored files"
    for record in manifest["source_files"]:
        data = (SNAPSHOT / "source" / record["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"], record["path"]


def test_db_slice_matches_its_hashes():
    manifest = load_manifest()
    for name, key in (("schema.sql", "schema_sha256"), ("data.sql", "data_sha256")):
        data = (SNAPSHOT / "db" / name).read_text(encoding="utf-8")
        assert (
            hashlib.sha256(data.encode()).hexdigest()
            == manifest["db_slice"][key]
        ), name


def test_log_slice_matches_its_hash_and_count():
    manifest = load_manifest()
    record = manifest["log_slice"]
    text = (SNAPSHOT / record["path"]).read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode()).hexdigest() == record["sha256"]
    assert len(text.splitlines()) == record["line_count"]
