"""The page and its vendored libraries: served from the package, no
CDN, licenses alongside (Brief §10.5 ruling 3; CLAUDE.md frontend
law)."""

import re

from engine.config.models import UiSettings
from engine.web.app import STATIC_DIR, create_app

VENDOR = STATIC_DIR / "vendor"


def _client():
    return create_app(
        object(), object(), object(), ui=UiSettings(), pack_name="p"
    ).test_client()


def test_index_is_served_and_references_only_local_assets():
    response = _client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "<script" in html and 'src="/static/' in html
    assert not re.search(r"https?://", html), "no CDN or external URL in the page"
    assert "localStorage" not in (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_vendored_libraries_are_pinned_with_licenses():
    for name in ("marked.min.js", "highlight.min.js", "highlight-github.min.css"):
        assert (VENDOR / name).is_file(), name
        assert _client().get(f"/static/vendor/{name}").status_code == 200
    assert (VENDOR / "marked.LICENSE").is_file()
    assert (VENDOR / "highlight.LICENSE").is_file()
    versions = (VENDOR / "VERSIONS.md").read_text(encoding="utf-8")
    assert "marked" in versions and "highlight.js" in versions
    assert re.search(r"\b\d+\.\d+\.\d+\b", versions), "versions are pinned"


def test_highlight_build_covers_the_languages_answers_quote():
    bundle = (VENDOR / "highlight.min.js").read_text(encoding="utf-8")
    for language in ("Python", "SQL", "JSON", "YAML"):
        assert f'name:"{language}"' in bundle, language


def test_browser_cell_formatting_mirrors_the_engine_rules():
    """The JS formatters are not unit-tested in a browser; pin that
    they exist and encode the same rules as harness/render.py: money
    (toFixed(2) plus grouping), durations (largest unit, one decimal,
    clock strings parsed), and the NULL em dash."""
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function formatMoney" in script
    assert "toFixed(2)" in script
    assert "column_formats" in script
    assert "function formatDuration" in script and "function humanizeSeconds" in script
    assert "toFixed(1)" in script
    assert '"\\u2014"' in script  # NULL_CELL, never an empty cell
    assert 'hint.kind === "duration"' in script
    # Rates (the coverage pass): one decimal, fraction x100, percent as is.
    assert "function formatRate" in script
    assert 'hint.kind === "rate"' in script
    assert 'scale === "percent" ? value : value * 100' in script
    # A zero-row table says so instead of drawing an empty box.
    assert 'el("p", "empty-rows", NO_ROWS)' in script
