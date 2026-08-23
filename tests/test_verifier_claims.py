"""Claim extraction pins on tricky prose — the segmentation rules are
the whole defense against false claims."""

from engine.verifier.claims import containing_sentence, extract_claims


def _numeric(claims):
    return [c for c in claims if c.kind == "numeric"]


def _entities(claims):
    return [c for c in claims if c.kind == "entity"]


def _quotes(claims):
    return [c for c in claims if c.kind == "quote"]


def test_separators_percent_currency_negative():
    claims = _numeric(
        extract_claims(
            "There are 1,442,986 lines totaling $1,200.50; 34.2% flagged, "
            "a delta of -3.5."
        )
    )
    by_surface = {c.surface: c for c in claims}
    assert by_surface["1,442,986"].value == 1442986
    assert by_surface["$1,200.50"].value == 1200.50
    assert by_surface["$1,200.50"].is_currency
    assert by_surface["34.2%"].value == 34.2
    assert by_surface["34.2%"].is_percent
    assert by_surface["34.2%"].resolution == 0.05
    assert by_surface["-3.5"].value == -3.5


def test_identifiers_consume_their_digits():
    claims = extract_claims("The rule_90 check ran, then check2_final.")
    assert [c.entity for c in _entities(claims)] == ["rule_90", "check2_final"]
    assert _numeric(claims) == []


def test_backticked_code_is_never_a_numeric_claim():
    claims = extract_claims("We cap with `LIMIT 200` in `run_sql`.")
    assert _numeric(claims) == []
    quotes = _quotes(claims)
    assert [q.text for q in quotes] == ["LIMIT 200"]
    assert [e.entity for e in _entities(claims)] == ["run_sql"]


def test_dates_never_decompose():
    claims = extract_claims("Data ends 2026-05-30 (May 30, 2026).")
    numerics = _numeric(claims)
    assert [c.date for c in numerics] == ["2026-05-30", "2026-05-30"]
    assert all(c.value is None for c in numerics)


def test_line_references_are_location_entities():
    claims = extract_claims(
        "See rules_engine.py:116-149, i.e. lines 116-149 of that file."
    )
    locations = [e for e in _entities(claims) if e.subkind == "location"]
    assert len(locations) == 2
    assert locations[0].file_path == "rules_engine.py"
    assert (locations[0].line_start, locations[0].line_end) == (116, 149)
    assert locations[1].file_path is None
    assert _numeric(claims) == []


def test_markdown_scaffolding_is_claim_free():
    text = (
        "## Findings\n"
        "1. First point\n"
        "2. Second point\n"
        "| a | b |\n"
        "| --- | --- |\n"
        "See [docs](https://example.com/p/123).\n"
    )
    claims = extract_claims(text)
    assert _numeric(claims) == []


def test_fenced_block_is_one_quote_and_fully_masked():
    text = "Here:\n```python\nif variance_pct > 0.15:\n    flag(146)\n```\nDone."
    claims = extract_claims(text)
    quotes = _quotes(claims)
    assert len(quotes) == 1 and quotes[0].fenced
    assert "variance_pct > 0.15" in quotes[0].text
    assert _numeric(claims) == []


def test_magnitude_words_are_mechanical():
    (claim,) = _numeric(extract_claims("There are about 1.4 million lines."))
    assert claim.value == 1_400_000
    assert claim.resolution == 50_000
    assert claim.is_approximate


def test_verbal_fractions_need_an_approximation_cue():
    (claim,) = _numeric(extract_claims("Roughly a third were flagged."))
    assert abs(claim.value - 1 / 3) < 1e-9
    assert claim.is_approximate

    # No cue, no claim: "the third invoice" is an ordinal, not a rate.
    assert _numeric(extract_claims("A third reviewer joined.")) == []


def test_spelled_cardinals_only_after_the_or_all():
    (claim,) = _numeric(extract_claims("The twelve rule functions run."))
    assert claim.value == 12
    assert _numeric(extract_claims("One of the reasons is speed.")) == []


def test_comparators_and_approximation_cues():
    claims = _numeric(
        extract_claims("There were more than 100 findings, roughly 150 total.")
    )
    assert claims[0].comparator == "over"
    assert claims[1].is_approximate and claims[1].comparator is None


def test_dotted_suffix_entities_but_not_prose_abbreviations():
    claims = extract_claims(
        "invoiceguard.spine.rules_engine.run_rules calls helpers, e.g. more."
    )
    entities = [e.entity for e in _entities(claims)]
    assert "invoiceguard.spine.rules_engine.run_rules" in entities
    assert "e.g" not in entities


def test_hyphenated_dotted_id_extracts_whole():
    # Carryback #1a: every unmatched entity in the L1 run was a
    # hyphenated component id truncated at the hyphen.
    prose = extract_claims("Parsing is handled by ig.spine.invoice-parse.")
    assert [c.entity for c in _entities(prose)] == ["ig.spine.invoice-parse"]

    ticked = extract_claims("Then `ig.spine.invoice-parse` takes over.")
    assert [c.entity for c in _entities(ticked)] == ["ig.spine.invoice-parse"]
    assert _quotes(ticked) == []

    # The offsets span the whole id — the inspector highlight anchor.
    text = "See ig.platform.file-lifecycle here."
    (claim,) = _entities(extract_claims(text))
    assert text[claim.start : claim.end] == "ig.platform.file-lifecycle"


def test_hyphenated_prose_and_ranges_are_not_entities():
    claims = extract_claims(
        "A well-known, invoice-level check; re-run it for 2024-05 data."
    )
    assert _entities(claims) == []


def test_bare_hyphenated_backtick_stays_a_quote():
    # No dot means no component-id shape; the vocabulary harvest never
    # yields bare hyphenated tokens, so an entity claim could not match.
    claims = extract_claims("The `invoice-parse` step runs first.")
    assert _entities(claims) == []
    assert [q.text for q in _quotes(claims)] == ["invoice-parse"]


def test_plain_words_are_not_entity_claims():
    claims = extract_claims("The invoices arrived and reviewers worked.")
    assert _entities(claims) == []


def test_offsets_index_the_original_text():
    text = "Count: 146 of 161."
    claims = _numeric(extract_claims(text))
    assert [text[c.start : c.end] for c in claims] == ["146", "161"]


def test_containing_sentence():
    text = "First sentence. There were 146 findings last week. Third."
    assert (
        containing_sentence(text, text.index("146"), text.index("146") + 3)
        == "There were 146 findings last week."
    )
