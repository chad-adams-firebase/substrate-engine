"""Per-file AST walk: nodes, containment, conditionals, module facts.

Pure structure extraction — stdlib ast, zero LLM involvement (Brief
§5 L2). The walker also collects the per-module facts later passes
need: import bindings, module-level constants (with the string bodies
of sqlalchemy.text() constants), and __tablename__ declarations.
"""

import ast
import re
from dataclasses import dataclass, field

CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass
class WalkedNode:
    kind: str  # module | class | function | method | constant
    qualified_name: str
    start_line: int
    end_line: int
    signature: str | None = None
    docstring: str | None = None
    value: str | None = None


@dataclass
class WalkedConditional:
    owner_qualified_name: str
    condition_text: str
    line: int


@dataclass
class WalkedModule:
    """Everything one file contributes, plus resolution context."""

    module_name: str
    file_path: str
    nodes: list[WalkedNode] = field(default_factory=list)
    # (parent qualified name, child qualified name) containment pairs;
    # the child's start line is the edge line.
    contains: list[tuple[str, str, int]] = field(default_factory=list)
    conditionals: list[WalkedConditional] = field(default_factory=list)
    # local name -> fully qualified target ("Invoice" ->
    # "invoiceguard.models.Invoice", "rollup" -> "invoiceguard.spine.rollup")
    bindings: dict[str, str] = field(default_factory=dict)
    # imported module qualified names with the import line, for edges.
    imports: list[tuple[str, int]] = field(default_factory=list)
    # constant name -> SQL string, for NAME = text("...") assignments.
    sql_constants: dict[str, str] = field(default_factory=dict)
    # class simple name -> table name, from __tablename__ = "..."
    tablenames: dict[str, str] = field(default_factory=dict)
    # function/method qualified name -> its AST, for the later passes.
    function_asts: dict[str, ast.AST] = field(default_factory=dict)
    # class qualified name -> {method simple name}, for self.x() calls.
    class_methods: dict[str, set[str]] = field(default_factory=dict)


def module_name_for(file_path: str) -> str:
    """"src/invoiceguard/spine/queue.py" -> "invoiceguard.spine.queue".

    Convention: a leading src/ directory is packaging, not identity;
    __init__.py names the package itself.
    """
    parts = file_path.split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({args}){returns}"


def _text_call_string(value: ast.expr) -> str | None:
    """The string body of a text("...") call, if that is what this is."""
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "text"
        and value.args
        and isinstance(value.args[0], ast.Constant)
        and isinstance(value.args[0].value, str)
    ):
        return value.args[0].value
    return None


def _collect_conditionals(
    owner: str, body: list[ast.stmt], out: list[WalkedConditional]
) -> None:
    for statement in body:
        for child in ast.walk(statement):
            if isinstance(child, (ast.If, ast.While)):
                out.append(
                    WalkedConditional(
                        owner_qualified_name=owner,
                        condition_text=ast.unparse(child.test),
                        line=child.lineno,
                    )
                )


def walk_module(file_path: str, source: str) -> WalkedModule:
    tree = ast.parse(source, filename=file_path)
    module_name = module_name_for(file_path)
    walked = WalkedModule(module_name=module_name, file_path=file_path)

    line_count = source.count("\n") + (0 if source.endswith("\n") else 1)
    walked.nodes.append(
        WalkedNode(
            kind="module",
            qualified_name=module_name,
            start_line=1,
            end_line=max(line_count, 1),
            docstring=ast.get_docstring(tree),
        )
    )

    for statement in tree.body:
        _walk_top_level(walked, statement)
    return walked


def _walk_top_level(walked: WalkedModule, statement: ast.stmt) -> None:
    module = walked.module_name

    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        _record_import(walked, statement)

    elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        qualified = f"{module}.{statement.name}"
        walked.nodes.append(
            WalkedNode(
                kind="function",
                qualified_name=qualified,
                start_line=statement.lineno,
                end_line=statement.end_lineno or statement.lineno,
                signature=_signature(statement),
                docstring=ast.get_docstring(statement),
            )
        )
        walked.contains.append((module, qualified, statement.lineno))
        walked.function_asts[qualified] = statement
        walked.bindings[statement.name] = qualified
        _collect_conditionals(qualified, statement.body, walked.conditionals)

    elif isinstance(statement, ast.ClassDef):
        _walk_class(walked, statement)

    elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        value = statement.value
        if value is None or len(targets) != 1:
            return
        target = targets[0]
        if not (isinstance(target, ast.Name) and CONSTANT_NAME.match(target.id)):
            return
        qualified = f"{module}.{target.id}"
        walked.nodes.append(
            WalkedNode(
                kind="constant",
                qualified_name=qualified,
                start_line=statement.lineno,
                end_line=statement.end_lineno or statement.lineno,
                value=ast.unparse(value),
            )
        )
        walked.contains.append((module, qualified, statement.lineno))
        walked.bindings[target.id] = qualified
        sql = _text_call_string(value)
        if sql is not None:
            walked.sql_constants[target.id] = sql


def _walk_class(walked: WalkedModule, statement: ast.ClassDef) -> None:
    module = walked.module_name
    class_qualified = f"{module}.{statement.name}"
    walked.nodes.append(
        WalkedNode(
            kind="class",
            qualified_name=class_qualified,
            start_line=statement.lineno,
            end_line=statement.end_lineno or statement.lineno,
            docstring=ast.get_docstring(statement),
        )
    )
    walked.contains.append((module, class_qualified, statement.lineno))
    walked.bindings[statement.name] = class_qualified
    walked.class_methods[class_qualified] = set()

    for child in statement.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method_qualified = f"{class_qualified}.{child.name}"
            walked.nodes.append(
                WalkedNode(
                    kind="method",
                    qualified_name=method_qualified,
                    start_line=child.lineno,
                    end_line=child.end_lineno or child.lineno,
                    signature=_signature(child),
                    docstring=ast.get_docstring(child),
                )
            )
            walked.contains.append(
                (class_qualified, method_qualified, child.lineno)
            )
            walked.function_asts[method_qualified] = child
            walked.class_methods[class_qualified].add(child.name)
            _collect_conditionals(
                method_qualified, child.body, walked.conditionals
            )
        elif isinstance(child, ast.Assign):
            if (
                len(child.targets) == 1
                and isinstance(child.targets[0], ast.Name)
                and child.targets[0].id == "__tablename__"
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, str)
            ):
                walked.tablenames[statement.name] = child.value.value


def _record_import(
    walked: WalkedModule, statement: ast.Import | ast.ImportFrom
) -> None:
    if isinstance(statement, ast.Import):
        for alias in statement.names:
            walked.imports.append((alias.name, statement.lineno))
            walked.bindings[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )
            if alias.asname is None:
                # `import a.b.c` binds `a`; the full dotted use is
                # resolved attribute-wise at call sites.
                walked.bindings[alias.name] = alias.name
    else:
        if statement.module is None or statement.level:
            return  # relative imports: not used by the target app
        walked.imports.append((statement.module, statement.lineno))
        for alias in statement.names:
            walked.bindings[alias.asname or alias.name] = (
                f"{statement.module}.{alias.name}"
            )
