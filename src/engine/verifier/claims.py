"""Deterministic claim extraction (§9.2 step 1) — code, never an LLM.

Segmentation runs before any number is read, because most "numbers"
in technical prose are not claims: digits inside identifiers
(rule_90), inside backticked code (`LIMIT 200`), inside dates, line
references, and markdown scaffolding. Masking replaces consumed
characters with spaces so every claim's start/end offsets stay true
to the original draft text — the inspector highlights from them.

Extraction is digit- and lexicon-anchored and therefore incomplete by
design: wholly verbal quantities ("a handful", "most") are not
extracted. The §9.4 drafting rules (placeholders, temperature 0) are
the mitigation, and Phase 4b's eval harness measures the residue.
"""

import re

from engine.verifier.models import (
    Claim,
    EntityClaim,
    NumericClaim,
    QuoteClaim,
)

_FENCED = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
# A hyphen extends a segment only inside a dotted token (component-id
# shape: ig.spine.invoice-parse). A bare hyphenated backtick body
# (`invoice-parse`) stays a quote: the vocabulary harvest never yields
# bare hyphenated tokens, so an entity claim there could not match.
# Public: the harvest side (checks/invocation.py) admits an argument
# value to vocabulary by this same shape, so what extraction calls an
# entity and what harvest calls a name stay one definition.
IDENTIFIER_SHAPED = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*$"
    r"|^[A-Za-z_]\w*(?:-\w+)*(?:\.[A-Za-z_]\w*(?:-\w+)*)+$"
)

_LIST_MARKER = re.compile(r"^\s{0,3}\d{1,3}[.)]\s", re.MULTILINE)
_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]{3,}\|?\s*$", re.MULTILINE)
_LINK_TARGET = re.compile(r"\]\([^)\s]*\)")
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)

_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
_MONTH_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})\b"
)
# Yearless prose dates ("May 29") extract as date tokens, not bare
# numerals — a 29 that means a day must never shop among counts.
_MONTH_DAY = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2})\b"
)

_LINE_REF = re.compile(r"\b([\w./-]+\.py):(\d+)(?:\s*[-–]\s*(\d+))?")
_LINE_RANGE = re.compile(r"\blines?\s+(\d+)\s*[-–]\s*(\d+)\b")

# Hyphens extend segments (ig.spine.invoice-parse is ONE token, never
# truncated at the hyphen — the carryback's L1 false positives). Only
# dotted tokens extract, and segments start [A-Za-z_], so date ranges
# ("2024-05") and hyphenated English with no dot ("well-known",
# "re-run") stay prose. An English suffix welded onto a dotted id
# still extracts whole — accepted: extraction cannot consult evidence
# to tell -parse from -like, and drafts backtick canonical names.
_DOTTED = re.compile(r"\b[A-Za-z_]\w*(?:-\w+)*(?:\.[A-Za-z_]\w*(?:-\w+)*)+\b")
_SNAKE = re.compile(r"\b[A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")

_MAGNITUDE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(thousand|million|billion|k|K|M|bn)\b"
)
_MAGNITUDE_FACTOR = {
    "thousand": 1e3, "k": 1e3, "K": 1e3,
    "million": 1e6, "M": 1e6,
    "billion": 1e9, "bn": 1e9,
}

# Verbal fractions: a deliberately closed lexicon, claimed only under
# an approximation cue ("about a third of").
_FRACTIONS = {
    "half": 0.5,
    "a third": 1 / 3,
    "a quarter": 0.25,
    "two thirds": 2 / 3,
    "three quarters": 0.75,
}
_FRACTION = re.compile(
    r"\b(half|a third|a quarter|two thirds|three quarters)\b"
)

# Spelled cardinals, claimed only after "the"/"all" ("the twelve
# rule functions") — bare "one of the reasons" is idiom, not a claim.
_CARDINALS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
_CARDINAL = re.compile(
    r"\b(?:the|all)\s+(thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|two|three|four|five|six|seven|eight|"
    r"nine|ten|eleven|twelve)\b",
    re.IGNORECASE,
)

_NUMBER = re.compile(
    r"(?<![\w.,-])(-?)(\$?)((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(%?)"
)

_APPROX_CUE = re.compile(
    r"(?:about|approximately|roughly|around|nearly|~)\s*$", re.IGNORECASE
)
_COMPARATORS = [
    (re.compile(r"(?:more than|over)\s*$", re.IGNORECASE), "over"),
    (re.compile(r"(?:less than|under|fewer than)\s*$", re.IGNORECASE), "under"),
    (re.compile(r"at least\s*$", re.IGNORECASE), "at_least"),
    (re.compile(r"at most\s*$", re.IGNORECASE), "at_most"),
]


def _blank(masked: list[str], start: int, end: int) -> None:
    for i in range(start, end):
        masked[i] = " "


def _mask_regex(masked: list[str], pattern: re.Pattern) -> None:
    text = "".join(masked)
    for match in pattern.finditer(text):
        _blank(masked, match.start(), match.end())


def _resolution_of(number_text: str) -> float:
    """Half the last displayed unit: "146" -> 0.5, "34.2" -> 0.05."""
    if "." in number_text:
        decimals = len(number_text.split(".")[1])
        return 0.5 * 10**-decimals
    return 0.5


def _cues(text: str, start: int) -> tuple[bool, str | None]:
    window = text[max(0, start - 16) : start]
    approx = _APPROX_CUE.search(window) is not None
    for pattern, name in _COMPARATORS:
        if pattern.search(window):
            return approx, name
    return approx, None


def containing_sentence(text: str, start: int, end: int) -> str:
    """The sentence around a span — what the judge and the feedback
    show. Boundaries: sentence punctuation or newlines."""
    left = max(
        text.rfind(". ", 0, start),
        text.rfind("! ", 0, start),
        text.rfind("? ", 0, start),
        text.rfind("\n", 0, start),
    )
    right_candidates = [
        pos
        for pos in (
            text.find(". ", end),
            text.find("! ", end),
            text.find("? ", end),
            text.find("\n", end),
        )
        if pos != -1
    ]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return text[left + 1 : right].strip()


def extract_claims(text: str) -> list[Claim]:
    claims: list[Claim] = []
    masked = list(text)

    # 1. Fenced code blocks: quote claims; interiors leave extraction.
    for match in _FENCED.finditer(text):
        claims.append(
            QuoteClaim(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                text=match.group(1),
                fenced=True,
            )
        )
        _blank(masked, match.start(), match.end())

    # 2. Inline backticks: identifier-shaped -> entity, else quote.
    current = "".join(masked)
    for match in _INLINE_CODE.finditer(current):
        body = match.group(1)
        if IDENTIFIER_SHAPED.match(body):
            claims.append(
                EntityClaim(
                    surface=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    entity=body,
                )
            )
        else:
            claims.append(
                QuoteClaim(
                    surface=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    text=body,
                )
            )
        _blank(masked, match.start(), match.end())

    # 3. Markdown scaffolding is never a claim.
    for pattern in (_LIST_MARKER, _TABLE_SEPARATOR, _LINK_TARGET, _HEADING):
        _mask_regex(masked, pattern)

    # 4. Dates before numbers: 2026-05-30 is one date, never three ints.
    current = "".join(masked)
    for match in _ISO_DATE.finditer(current):
        claims.append(
            NumericClaim(
                surface=match.group(1),
                start=match.start(1),
                end=match.end(1),
                date=match.group(1),
            )
        )
        _blank(masked, match.start(), match.end())
    current = "".join(masked)
    for match in _MONTH_DATE.finditer(current):
        month = _MONTHS[match.group(1).lower()]
        iso = f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"
        claims.append(
            NumericClaim(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                date=iso,
            )
        )
        _blank(masked, match.start(), match.end())
    current = "".join(masked)
    for match in _MONTH_DAY.finditer(current):
        day = int(match.group(2))
        if not 1 <= day <= 31:
            continue  # "March 45" is a numeral, not a date
        month = _MONTHS[match.group(1).lower()]
        claims.append(
            NumericClaim(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                date=f"{month:02d}-{day:02d}",
            )
        )
        _blank(masked, match.start(), match.end())

    # 5. Line references: location entities, never numeric values.
    current = "".join(masked)
    for match in _LINE_REF.finditer(current):
        line_start = int(match.group(2))
        line_end = int(match.group(3)) if match.group(3) else line_start
        claims.append(
            EntityClaim(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                entity=match.group(0),
                subkind="location",
                file_path=match.group(1),
                line_start=line_start,
                line_end=line_end,
            )
        )
        _blank(masked, match.start(), match.end())
    current = "".join(masked)
    for match in _LINE_RANGE.finditer(current):
        claims.append(
            EntityClaim(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                entity=match.group(0),
                subkind="location",
                line_start=int(match.group(1)),
                line_end=int(match.group(2)),
            )
        )
        _blank(masked, match.start(), match.end())

    # 6. Identifiers before numbers: rule_90 never yields a 90.
    current = "".join(masked)
    for pattern in (_DOTTED, _SNAKE):
        for match in pattern.finditer(current):
            token = match.group(0)
            if pattern is _DOTTED:
                segments = token.split(".")
                # "e.g." shapes are prose, not identifiers.
                if not (
                    all(len(s) >= 2 for s in segments)
                    and any(len(s) >= 3 for s in segments)
                ):
                    continue
            claims.append(
                EntityClaim(
                    surface=token,
                    start=match.start(),
                    end=match.end(),
                    entity=token,
                )
            )
            _blank(masked, match.start(), match.end())
        current = "".join(masked)

    # 7. Magnitude words: "1.4 million" is mechanical, not judge bait.
    for match in _MAGNITUDE.finditer(current):
        mantissa = match.group(1)
        factor = _MAGNITUDE_FACTOR[match.group(2)]
        approx, comparator = _cues(current, match.start())
        claims.append(
            NumericClaim(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                value=float(mantissa) * factor,
                is_approximate=approx,
                comparator=comparator,
                resolution=_resolution_of(mantissa) * factor,
            )
        )
        _blank(masked, match.start(), match.end())

    # 8. Verbal fractions, only under an approximation cue.
    current = "".join(masked)
    for match in _FRACTION.finditer(current):
        approx, comparator = _cues(current, match.start())
        if not approx:
            continue
        claims.append(
            NumericClaim(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                value=_FRACTIONS[match.group(1)],
                is_approximate=True,
                comparator=comparator,
                resolution=0.10,  # verbal fractions are coarse by nature
            )
        )
        _blank(masked, match.start(), match.end())

    # 9. Spelled cardinals after the/all: "the twelve rule functions".
    current = "".join(masked)
    for match in _CARDINAL.finditer(current):
        claims.append(
            NumericClaim(
                surface=match.group(1),
                start=match.start(1),
                end=match.end(1),
                value=float(_CARDINALS[match.group(1).lower()]),
                resolution=0.5,
                spelled=True,
            )
        )
        _blank(masked, match.start(1), match.end(1))

    # 10. Digit numbers: currency, percent, separators, decimals.
    current = "".join(masked)
    for match in _NUMBER.finditer(current):
        sign, currency, digits, percent = match.groups()
        approx, comparator = _cues(current, match.start())
        value = float(sign + digits.replace(",", ""))
        claims.append(
            NumericClaim(
                surface=match.group(0),
                start=match.start(),
                end=match.end(),
                value=value,
                is_percent=bool(percent),
                is_currency=bool(currency),
                is_approximate=approx,
                comparator=comparator,
                resolution=_resolution_of(digits.replace(",", "")),
            )
        )
        _blank(masked, match.start(), match.end())

    claims.sort(key=lambda c: c.start)
    return claims
