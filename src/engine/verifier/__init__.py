"""The Verifier (Brief §9) — the machinery that makes the harness's
one guessing component safe. Deterministic claim extraction, mechanical
matching against this turn's evidence, an LLM fuzzy judge only for the
residue, per-substrate plausibility checks, and the verdict ladder.
Mandatory on the path to every answer; there are no bypasses."""
