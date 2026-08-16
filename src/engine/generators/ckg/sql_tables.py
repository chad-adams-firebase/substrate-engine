"""Table names out of raw SQL strings.

Serves the reads_table/writes_table extraction for the target app's
sanctioned raw-SQL sites (SQL arriving as sqlalchemy.text() constants).
Regex-level on purpose: the sites are declared and few, and a wrong
guess here is caught by the fixture tests, not hidden behind a parser
dependency the work machine cannot install.
"""

import re

_READ = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def tables_in_sql(sql: str) -> tuple[set[str], set[str]]:
    """(reads, writes) as lowercase table names. A DELETE's FROM is a
    write, not a read, so write matches are removed from reads."""
    writes = {match.lower() for match in _WRITE.findall(sql)}
    reads = {match.lower() for match in _READ.findall(sql)} - writes
    return reads, writes
