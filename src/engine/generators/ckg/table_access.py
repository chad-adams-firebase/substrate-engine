"""reads_table / writes_table extraction — the hardest, most
fixture-tested part of the CKG (Brief §5).

Two pattern families, both bounded and documented; the fixture tests
ARE the contract for what is detected:

ORM constructs (SQLAlchemy 2.0 style):
- select(Model), select(Model.column, ...)      -> reads
- session.get(Model, ...)                        -> reads
- session.add(Model(...)), session.add(var)      -> writes
  (var tracked through simple `var = Model(...)` assignments in the
  same function)

Raw SQL (the target's sanctioned text() sites):
- session.execute(SQL_CONSTANT) where the constant is a module-level
  NAME = text("...") in the same module    -> parsed FROM/JOIN reads,
  INSERT/UPDATE/DELETE writes
- session.execute(text("..."))  inline     -> same

Anything needing real type inference (relationship traversal, ORM
attribute mutation) is deliberately out; those flows are visible
through the helper functions this pass does catch.
"""

import ast
from dataclasses import dataclass

from engine.generators.ckg.sql_tables import tables_in_sql
from engine.generators.ckg.walker import WalkedModule, _text_call_string


@dataclass(frozen=True)
class TableAccess:
    owner_qualified_name: str
    kind: str  # reads_table | writes_table
    table: str
    line: int


def _simple_name(reference: str) -> str:
    return reference.rsplit(".", 1)[-1]


def _model_for(
    expression: ast.expr, module: WalkedModule, model_index: dict[str, str]
) -> str | None:
    """The table behind an expression naming a model class, if any."""
    if isinstance(expression, ast.Attribute):
        expression = expression.value  # Model.column -> Model
    if not isinstance(expression, ast.Name):
        return None
    bound = module.bindings.get(expression.id, expression.id)
    return model_index.get(_simple_name(bound))


def extract_table_access(
    module: WalkedModule, model_index: dict[str, str]
) -> list[TableAccess]:
    accesses: set[TableAccess] = set()
    for owner, function_ast in module.function_asts.items():
        # Track `var = Model(...)` so session.add(var) resolves.
        constructed: dict[str, str] = {}
        for statement in ast.walk(function_ast):
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Call)
            ):
                table = _model_for(statement.value.func, module, model_index)
                if table:
                    constructed[statement.targets[0].id] = table

        for call in ast.walk(function_ast):
            if not isinstance(call, ast.Call):
                continue
            accesses.update(
                _accesses_for_call(call, owner, module, model_index, constructed)
            )
    return sorted(
        accesses, key=lambda a: (a.owner_qualified_name, a.kind, a.table, a.line)
    )


def _accesses_for_call(
    call: ast.Call,
    owner: str,
    module: WalkedModule,
    model_index: dict[str, str],
    constructed: dict[str, str],
) -> list[TableAccess]:
    found: list[TableAccess] = []

    def add(kind: str, table: str) -> None:
        found.append(
            TableAccess(
                owner_qualified_name=owner,
                kind=kind,
                table=table,
                line=call.lineno,
            )
        )

    # select(Model) / select(Model.column, ...)
    if isinstance(call.func, ast.Name) and call.func.id == "select":
        for argument in call.args:
            table = _model_for(argument, module, model_index)
            if table:
                add("reads_table", table)
        return found

    if not isinstance(call.func, ast.Attribute):
        return found
    method = call.func.attr

    # session.get(Model, ...)
    if method == "get" and call.args:
        table = _model_for(call.args[0], module, model_index)
        if table:
            add("reads_table", table)

    # session.add(Model(...)) / session.add(var)
    elif method == "add" and call.args:
        argument = call.args[0]
        table = None
        if isinstance(argument, ast.Call):
            table = _model_for(argument.func, module, model_index)
        elif isinstance(argument, ast.Name):
            table = constructed.get(argument.id)
        if table:
            add("writes_table", table)

    # session.execute(SQL_CONSTANT, ...) / session.execute(text("..."))
    elif method == "execute" and call.args:
        argument = call.args[0]
        sql = None
        if isinstance(argument, ast.Name):
            sql = module.sql_constants.get(argument.id)
        elif isinstance(argument, ast.Call):
            sql = _text_call_string(argument)
        if sql:
            reads, writes = tables_in_sql(sql)
            for table in sorted(reads):
                add("reads_table", table)
            for table in sorted(writes):
                add("writes_table", table)

    return found
