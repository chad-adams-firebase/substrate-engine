"""Substrate contracts and serialization (Brief §4, §5, §13).

The pydantic models here are generator output contracts: designed on
the personal side, produced identically by generators against any
codebase/database. Everything a generator writes or a later phase
reads goes through these shapes — no naked dicts across the boundary.
"""
