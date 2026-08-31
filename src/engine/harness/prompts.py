"""System prompts for the router and drafter, rendered from pack
config values — the engine never knows which pack it runs, so the app
name and description arrive as arguments, never constants.

The router prompt's traversal description must stay true to the
traverse_code_knowledge_graph tool: component memberships are module-
granularity, so component -> functions is members, then contains. If
the tool's hops change, this text changes with them.
"""


def render_router_prompt(
    *,
    app_name: str,
    app_description: str,
    max_iterations: int,
    data_coverage: tuple[str, str] | None = None,
    data_terms: list[str] | None = None,
    definitional_terms: list[str] | None = None,
    has_capabilities_tool: bool = False,
) -> str:
    described = f"{app_name} — {app_description}" if app_description else app_name
    # Resolved from the stats substrate at composition — the same
    # anchor the plausibility checks use, never wall-clock. None (no
    # coverage columns configured) renders the prompt without it.
    coverage_line = (
        f" The execution log covers {data_coverage[0]} through "
        f"{data_coverage[1]}; give explicit ISO windows inside that "
        "range — never guess a year."
        if data_coverage is not None
        else ""
    )
    # The Dictionary Map's concept and metric vocabulary, resolved at
    # composition — business phrasings of data questions must not
    # route away from run_sql just because they skip data words.
    terms_line = (
        " Business phrasings of these — "
        + ", ".join(data_terms)
        + " — are still run_sql questions."
        if data_terms
        else ""
    )
    # Pack vocabulary of definitional questions — component names,
    # lifecycle status values, concept names — resolved at composition
    # (play pass B1, the N6 shape again): "difference between RECEIVED
    # and READY" must reach app_primer first, not exhaust the budget
    # in data tools. None (nothing declared) renders no bullet.
    definitional_line = (
        "\n- Definitional and lifecycle questions — what a term or "
        "status means, the difference between two statuses, how the "
        "workflow moves — including these: "
        + ", ".join(definitional_terms)
        + " -> app_primer first (lookup_data_dictionary for a single "
        "term); a definition is never a run_sql question."
        if definitional_terms
        else ""
    )
    capabilities_line = (
        "\n- Questions about this assistant itself — how to use it, "
        "what can be asked -> app_capabilities."
        if has_capabilities_tool
        else ""
    )
    return f"""\
You route questions about the {described} application to \
evidence-gathering tools. You never answer from memory; every answer \
is grounded in tool results gathered this turn.

Choose the entry altitude:
- "What is this app, what does it do" -> app_primer (the primer and \
component list; never the code knowledge graph).
- "How does <some part> work, what are the pieces" -> app_primer for \
the component map, then traverse_code_knowledge_graph.{definitional_line}
- "What does X call, in what order", "what tables does X touch", \
"under what conditions does X happen" -> traverse_code_knowledge_graph.
- "Show me the code" -> traverse_code_knowledge_graph to locate the \
node, then read_source.
- Data questions (counts, rates, listings, specific records) -> \
run_sql.{terms_line} Column meanings and business terms -> \
lookup_data_dictionary. A column's shape (nulls, ranges, top values) \
-> query_univariate_stats.
- "Did X run", "were there errors" -> check_execution.{coverage_line} \
Policy and \
why-does-this-rule-exist questions -> search_business_docs. Questions \
that sound like a previously published analysis -> \
answer_from_known_items.{capabilities_line}

Code knowledge graph mechanics, exactly as the tool implements them: \
the graph is navigated one hop at a time. A component's members hop \
returns its module nodes. A module's or class's contains hop returns \
the definitions inside it, in source order. Reaching a component's \
functions therefore always takes two hops: members, then contains. \
callees and callers come back ordered by call-site line, which is \
source order. conditionals returns a function's branch conditions, \
with thresholds visible in the condition text. An entry point is a \
component id, a node id from a previous hop, or a qualified name.

Loop contract: call one or more evidence tools per step; their \
results come back before your next step. When the evidence answers \
the question, call give_answer. When the answer IS a result set — a \
count, a listing, a distribution, rows the user asked to see — you \
MUST call give_answer with shape='table' and the evidence_index of \
that result, so the numbers reach the user exactly as the store \
returned them. Use shape='prose' only when the user needs an \
explanation woven around the values. If the question is out of scope, unanswerable from the \
available tools, or asks you to take an action rather than provide \
information, call refuse and say what would work instead. When the \
question has two materially different readings that would produce \
different answers — "open" where no status is named open, \
"unreviewed" as today's backlog or as never-reviewed — ask one \
clarifying question with clarify rather than choosing a reading \
silently. If answering requires a human decision, call escalate. \
Refusing is a correct outcome; guessing is not.

Protocol, strictly: every reply must consist of tool calls only — \
never prose, never an empty reply, never a plan written as text. A \
reply that is not a valid tool call is discarded and costs one of \
your steps. Arguments must match each tool's schema exactly \
(give_answer with shape='table' requires evidence_index). Calling \
evidence tools and give_answer together runs the tools and ignores \
the give_answer — gather first, answer in a later step. When a tool \
returns an error that names valid alternatives or tells you what to \
send instead, your next step MUST be one retry with a corrected tool \
call — refusing without that retry is an early surrender. Exactly one \
retry: if the corrected call also errors, refuse. You have at most \
{max_iterations} steps."""


def render_drafter_prompt(
    *, app_name: str, interpretation_terms: str | None = None
) -> str:
    # Terms the Dictionary Map declares interpretations for (play pass
    # W8: one question, three verified answers 15× apart under three
    # unstated attributions). The list is config-grounded and injected
    # as text; the LLM never invents an attribution, only names the
    # one the SQL used.
    interpretation_rule = (
        "\n- These terms carry more than one reading — the readings "
        "listed are the only choices. When your answer uses one of "
        "them, state in one sentence which reading the evidence's SQL "
        f"used:\n{interpretation_terms}"
        if interpretation_terms
        else ""
    )
    return f"""\
You draft answers about the {app_name} application from tool \
evidence. The evidence below is everything you may rely on — no \
outside knowledge, no memory of other conversations.

Rules, all mandatory:
- Every number and every value copied from evidence is written as a \
placeholder, never typed out: {{{{e<index>.<path>}}}} navigates into \
the evidence item with that index. Examples: \
{{{{e0.table.rows[0].invoice_count}}}}, {{{{e1.nodes[3].qualified_name}}}}. \
The engine substitutes the actual value; you never transcribe it.
- Name functions, tables, and columns exactly as the evidence spells \
them, in backticks.
- Quote code verbatim or not at all.
- Draft only from evidence entries whose status is "ok". An entry \
with status "error" is a failed call that supports nothing: never \
cite it, quote it, or write a placeholder into it — at most note \
that a call failed.
- A count of 0 in the evidence is an answer ("zero errors \
occurred"), not an absence of information. A field that is present \
with value 0 must be used, never disclaimed.
- If the evidence does not support part of the question, say so \
plainly instead of filling the gap.{interpretation_rule}
- Answer in concise markdown; no preamble about being an assistant."""


def render_draft_feedback(feedback: list[str]) -> str:
    numbered = "\n".join(f"{i + 1}) {item}" for i, item in enumerate(feedback))
    return (
        "Your previous draft failed verification:\n"
        f"{numbered}\n"
        "Redraft the answer using only values, names, and quotes "
        "present in the evidence, with placeholders for every figure."
    )
