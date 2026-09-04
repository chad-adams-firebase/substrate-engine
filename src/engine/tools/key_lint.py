"""Two SQL lints for the value a generated statement binds a key to
(Backlog Pass, gate verdict §7 item 1). The 30-turn session's turn 20
asked for "that invoice's history" and ran
`WHERE ih.invoice_id = 123 -- Replace 123 with the actual invoice ID`:
another invoice's history, verified, because nothing read comments and
nothing knew which literals the conversation had seen.

lint_placeholders — a comment that admits the value is invented
("replace", "placeholder", "actual …"), or a bind shape standing where
a value belongs (`:invoice_id`, `?`, `$1`, `{{…}}`, `<id>`). A HARD
challenge: run_sql blocks on it every time it fires, with no license
to resend unchanged, because the comment is the model's own confession.

Both lints follow the challenge principle: they name what is wrong
with this statement and never a table — not even the one queried.
"""

import re

from engine.tools.sql_scopes import STRING_LITERAL

# The vocabulary of a model telling itself a value is invented.
_ADMISSION = re.compile(
    r"\b(?:replace|placeholder|substitute|fill\s+in|todo|dummy|hypothetical|"
    r"assum\w*|actual|desired|appropriate|specific|sample|example)\b",
    re.IGNORECASE,
)
# Bind shapes where a value belongs. `::` is a cast, never a bind; `<`
# is a placeholder only when it directly wraps one identifier — `<>`,
# `a < b`, `x <= y` are comparison operators, not placeholders.
_NAMED_BIND = re.compile(r"(?<![\w:]):[A-Za-z_]\w*\b")
_QMARK_BIND = re.compile(r"(?<=[=(,\s])\?(?=[\s,)]|$)")
_DOLLAR_BIND = re.compile(r"(?<![\w$])\$\d+\b")
_BRACES_BIND = re.compile(r"\{\{[^{}]*\}\}")
_ANGLE_BIND = re.compile(r"(?<![\w>])<[A-Za-z_]\w*>(?![\w<])")
_WORD_PLACEHOLDER = re.compile(r"\bplaceholder\b", re.IGNORECASE)
_BIND_SHAPES = (_NAMED_BIND, _QMARK_BIND, _DOLLAR_BIND, _BRACES_BIND, _ANGLE_BIND, _WORD_PLACEHOLDER)


def split_comments(text: str) -> tuple[str, list[str]]:
    """(the text without its comments, the comments in order) — read by
    a walk that steps over string literals, so an apostrophe inside a
    comment never opens a literal and a `--` inside a literal is never
    a comment. `--` runs to end of line; `/* */` may span lines (nothing
    else in the engine strips block comments, so a lint that must see
    past one strips it here first)."""
    kept: list[str] = []
    comments: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            kept.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments.append(text[i:j])
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            comments.append(text[i:j])
            kept.append(" ")
            i = j
        else:
            kept.append(ch)
            i += 1
    return "".join(kept), comments


def _comment_body(comment: str) -> str:
    body = comment.strip()
    if body.startswith("--"):
        body = body[2:]
    elif body.startswith("/*"):
        body = body[2:-2] if body.endswith("*/") else body[2:]
    body = " ".join(body.split())
    return body if len(body) <= 120 else body[:117] + "..."


def lint_placeholders(sql: str, *, comment_source: str | None = None) -> str | None:
    """The reason a statement binds a placeholder where a value belongs,
    or None. comment_source is the text whose comments are read when it
    differs from the statement — run_sql passes the fenced block, since
    extract_sql drops leading comment lines before the lints see the
    statement and a confession on the first line is still a confession."""
    _, comments = split_comments(comment_source if comment_source is not None else sql)
    stripped, _ = split_comments(sql)
    reasons: list[str] = []
    admitted = next((c for c in comments if _ADMISSION.search(c)), None)
    if admitted is not None:
        reasons.append(
            f"Placeholder check: the comment `{_comment_body(admitted)}` says "
            "this value was not taken from the conversation. Remove the "
            "comment and use a value the conversation established, or ask "
            "the user which one is meant."
        )
    blanked = STRING_LITERAL.sub("''", stripped)
    shape = next(
        (m.group(0) for pattern in _BIND_SHAPES for m in [pattern.search(blanked)] if m),
        None,
    )
    if shape is not None:
        reasons.append(
            f"Placeholder check: `{shape}` is a bind placeholder, not a "
            "value. Use a value the conversation established, or ask the "
            "user which one is meant."
        )
    return " ".join(reasons) or None
