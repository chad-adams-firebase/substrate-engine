"""Readers for the pack-authored substrate inputs.

These are the files humans own: the components declaration, the SME
overlays, the L0 primer. Generators read them and never write them
(the overlay merge writes into substrates/, leaving overlays/
untouched — that separation is what makes human rows physically safe
from regeneration).
"""

from pathlib import Path

import yaml

from engine.substrates.jsonl import read_rows
from engine.substrates.models import Component, ComponentMembership, DictionaryRow, Provenance


class PackDataError(Exception):
    """A pack-authored file is malformed. The message speaks to the
    pack author, not the engine developer."""


def load_components(path: Path) -> list[Component]:
    """components.yaml: the declared L1 components. Human pack data —
    provenance is implied and attached here."""
    if not path.is_file():
        raise PackDataError(
            f"{path} does not exist — a pack enabling the CKG substrates "
            f"declares its components there (Brief §5 L1)."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("components"), list):
        raise PackDataError(
            f"{path} must contain a top-level 'components:' list."
        )
    components = []
    for entry in raw["components"]:
        components.append(
            Component(
                **entry,
                provenance=Provenance(
                    source="human",
                    confidence=1.0,
                    needs_validation=False,
                ),
            )
        )
    return components


def load_dictionary_overlay(path: Path) -> list[DictionaryRow]:
    if not path.is_file():
        return []
    return read_rows(path, DictionaryRow)


def load_membership_overlay(path: Path) -> list[ComponentMembership]:
    if not path.is_file():
        return []
    return read_rows(path, ComponentMembership)


def load_primer(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")
