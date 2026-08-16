"""Pack-build tooling: steps that produce a pack's artifacts.

Unlike engine core, this package may touch concrete storage engines —
it manufactures the files the adapters later serve. It still never
imports engine.adapters.
"""
