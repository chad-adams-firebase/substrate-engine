"""The page and its vendored libraries: served from the package, no
CDN, licenses alongside (Brief §10.5 ruling 3; CLAUDE.md frontend
law); and the Block 3 shape — three panes, the inspector's receipts,
and the play-session findings — pinned by source text, since the JS
is not executed here."""

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
    # Duration pass: the unit is chosen after rounding to the
    # millisecond, with the engine's exact expression (floor of
    # x * 1000 + 0.5, so ties round the same way on both surfaces).
    assert "Math.floor(Math.abs(seconds) * 1000 + 0.5) / 1000" in script
    assert '"\\u2014"' in script  # NULL_CELL, never an empty cell
    assert 'hint.kind === "duration"' in script
    # Rates (the coverage pass): one decimal, fraction x100, percent as is.
    assert "function formatRate" in script
    assert 'hint.kind === "rate"' in script
    assert 'scale === "percent" ? value : value * 100' in script
    # A zero-row table says so instead of drawing an empty box.
    assert 'el("p", "empty-rows", NO_ROWS)' in script


# --- Block 3: three panes, the inspector, the play-session findings ------


def test_the_page_has_three_panes_and_the_phase_6_seams():
    html = _client().get("/").get_data(as_text=True)
    for anchor in ('id="workspaces"', 'id="conversations"', 'id="new-conversation"',
                   'id="new-workspace"', 'id="transcript"', 'id="transcript-inner"',
                   'id="inspector"', 'id="inspector-body"', 'id="starters"'):
        assert anchor in html, anchor
    # Package mode is a disabled tab until Phase 6; the Library is a
    # placeholder link, not a route.
    assert 'data-mode="package" disabled' in html
    assert "Phase 6" in html and 'class="library"' in html


def test_the_favicon_answers_on_both_paths():
    client = _client()
    for path in ("/favicon.ico", "/static/favicon.svg"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.mimetype == "image/svg+xml"
    html = client.get("/").get_data(as_text=True)
    assert 'rel="icon"' in html and 'href="/static/favicon.svg"' in html


def test_the_scroll_container_is_full_width_and_the_reading_width_is_inside():
    """The play-session dead zones: max-width sat on the scroll
    container, so the wheel did nothing beside the column. The rule
    now lives on the inner wrapper."""
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        rules[selector.strip()] = body
    assert "max-width" not in rules[".transcript"]
    assert "overflow-y: auto" in rules[".transcript"]
    assert "max-width: 900px" in rules[".transcript-inner"]


def test_the_inspector_reads_what_the_interludes_recorded():
    """Pinned by source text: the SQL attempt ledger with all three
    lint fields and the override marking, the verdict's plausibility
    findings by check name and severity, the claim offsets, the
    diagnosis, and the raw router text on a violation."""
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert '["lint", "Fan-out check"]' in script
    assert '["enum_lint", "Enum check"]' in script
    assert '["interval_lint", "Interval check"]' in script
    assert '"executed · override"' in script and '"blocked by lint"' in script
    assert "finding.check" in script and "finding.severity" in script
    assert "claim.start" in script and "claim.end" in script and "claim.status" in script
    assert "event.raw_response" in script
    assert "function renderSqlLedger" in script and "function renderTrail" in script
    # Evidence tables honor column_formats through the one renderTable.
    assert "renderTable(output.table" in script
    # The inspector fetches the bundle on demand, never with the turn.
    assert "/api/evidence/" in script
    # A new conversation is a client-side reset; the row is created by
    # the first turn (workspace_id on the ask), never by the click.
    assert "function newConversation" in script
    assert "body.workspace_id = state.workspaceId" in script
    assert "localStorage" not in script and "sessionStorage" not in script
    # Starters ask directly; the empty state is where every new
    # conversation, a fresh reload included, begins.
    assert 'addEventListener("click", () => ask(text))' in script
