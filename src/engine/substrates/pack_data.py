"""Readers for the pack-authored substrate inputs.

These are the files humans own: the components declaration, the SME
overlays, the L0 primer. Generators read them and never write them
(the overlay merge writes into substrates/, leaving overlays/
untouched — that separation is what makes human rows physically safe
from regeneration).
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from engine.substrates.jsonl import read_rows
from engine.substrates.models import (
    BusinessDoc,
    Component,
    ComponentMembership,
    DictionaryMap,
    DictionaryRow,
    Provenance,
)


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


def _split_front_matter(text: str, origin: Path) -> tuple[dict, str]:
    """Split a '---' YAML front-matter block from a markdown body."""
    if not text.startswith("---\n"):
        raise PackDataError(
            f"{origin}: expected YAML front matter (a leading '---' block)."
        )
    try:
        _, block, body = text.split("---\n", 2)
    except ValueError:
        raise PackDataError(
            f"{origin}: front matter is not closed with a '---' line."
        ) from None
    loaded = yaml.safe_load(block)
    if not isinstance(loaded, dict):
        raise PackDataError(f"{origin}: front matter must be a YAML mapping.")
    return loaded, body.lstrip("\n")


def load_dictionary_map(path: Path) -> DictionaryMap:
    """dictionary_map.yaml: the semantic/routing layer (Brief §4.2),
    authored per pack. This whole artifact is run_sql's grounding
    payload — a missing or malformed map should stop a pack that
    enables it, loudly."""
    if not path.is_file():
        raise PackDataError(
            f"{path} does not exist — a pack enabling the data_dictionary_map "
            f"substrate authors its map there (Brief §4.2)."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PackDataError(f"{path} must contain a YAML mapping.")
    try:
        return DictionaryMap.model_validate(raw)
    except ValidationError as exc:
        raise PackDataError(f"{path} is not a valid Dictionary Map: {exc}") from exc


def load_business_docs(directory: Path) -> list[BusinessDoc]:
    """business_docs/*.md: snapshotted memos with front-matter
    provenance naming exactly where and when each copy was taken.
    Sorted by file name so the substrate's order is stable."""
    if not directory.is_dir():
        raise PackDataError(
            f"{directory} does not exist — a pack enabling the "
            f"business_context_docs substrate keeps its memo snapshots there."
        )
    docs: list[BusinessDoc] = []
    for path in sorted(directory.glob("*.md")):
        front, body = _split_front_matter(
            path.read_text(encoding="utf-8"), origin=path
        )
        # The memo's own `date:` key maps to doc_date (see BusinessDoc).
        if "date" in front:
            front["doc_date"] = str(front.pop("date"))
        try:
            docs.append(BusinessDoc(slug=path.stem, body=body, **front))
        except (ValidationError, TypeError) as exc:
            raise PackDataError(
                f"{path}: front matter does not satisfy the BusinessDoc "
                f"contract: {exc}"
            ) from exc
    return docs
