"""Phase 4b answer-verification eval harness (docs/phasing.md).

Two halves, mirroring the conformance validator's travel-back split:
`engine eval run` executes bank rows through the real ask path on the
machine with the LLM key and emits a self-contained JSONL report;
`engine eval grade` replays that report fully offline — no LLM —
against gold scripts executed fresh at grade time.

The bank lives under evals/, outside every engine-readable path: the
engine must never see its own exam.
"""
