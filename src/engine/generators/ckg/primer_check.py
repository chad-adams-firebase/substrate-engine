"""L0 reference validation (Brief §5 L0).

The primer is human-territory prose the machine only CHECKS, never
writes: every component id referenced in it must exist, and a
regeneration that leaves a declared component unreferenced is worth a
warning (the primer may be describing a world that moved).
"""

import re


def check_primer(
    primer_text: str, prefix: str, component_ids: set[str]
) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings). Errors are references to components
    that do not exist; warnings are components the primer never
    mentions."""
    pattern = re.compile(
        rf"\b{re.escape(prefix)}\.[a-z0-9-]+(?:\.[a-z0-9-]+)*\b"
    )
    referenced = {match.group(0) for match in pattern.finditer(primer_text)}

    errors = [
        f"primer references unknown component {reference}"
        for reference in sorted(referenced - component_ids)
    ]
    warnings = [
        f"component {component_id} is never referenced in the primer"
        for component_id in sorted(component_ids - referenced)
    ]
    return errors, warnings
