"""Substrate generators (Brief §13) — product code, not scripts.

Generators depend only on ports and substrate contracts; they receive
port instances by injection (the CLI composes them from pack config)
and never import adapters. Generator correctness is load-bearing: a
subtly wrong extractor poisons the substrate silently, which is why
every generator is fixture-tested against checked-in expected output.
"""
