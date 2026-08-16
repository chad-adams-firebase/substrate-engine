"""Call-edge resolution: who invokes whom, by static name resolution.

Second pass over the walked modules, once every node is known.
Resolution order for a call site:

1. The module's own bindings (imports, local defs) give a qualified
   candidate; an exact node match wins.
2. The candidate's simple name, when exactly one node in the whole
   graph bears it — this absorbs the target app's package re-exports
   (invoiceguard.models.Invoice is a re-export of
   invoiceguard.models.invoice.Invoice).
3. Otherwise: no edge. A skipped call is honest; a guessed one
   poisons traversal answers.

self.method() resolves within the owning class. Calls to classes are
instantiations and get edges too — construction is control flow.

Method calls on typed locals resolve through a bounded type map — the
statically clear patterns only (no inference):
- a parameter annotated with a known class (`invoice: Invoice`),
- a local assigned from `session.get(Model, ...)`,
- a local assigned from a constructor (`entry = InvoiceHistory(...)`).
This is what makes `invoice.transition_to(...)` — the app's single
status-transition path — a real edge instead of a blind spot.
"""

import ast
from dataclasses import dataclass

from engine.generators.ckg.walker import WalkedModule


@dataclass(frozen=True)
class ResolvedCall:
    caller_qualified_name: str
    callee_qualified_name: str
    line: int


CALLABLE_KINDS = {"function", "method", "class"}


@dataclass
class CallIndex:
    qualified: set[str]
    by_simple: dict[str, list[str]]
    # class qualified name -> its method simple names, across ALL
    # modules, for typed-local method resolution.
    class_methods: dict[str, set[str]]


def build_call_index(modules: list[WalkedModule]) -> CallIndex:
    qualified: set[str] = set()
    by_simple: dict[str, list[str]] = {}
    class_methods: dict[str, set[str]] = {}
    for module in modules:
        for name, methods in module.class_methods.items():
            class_methods[name] = set(methods)
        for node in module.nodes:
            if node.kind in CALLABLE_KINDS:
                qualified.add(node.qualified_name)
                simple = node.qualified_name.rsplit(".", 1)[-1]
                by_simple.setdefault(simple, []).append(node.qualified_name)
    return CallIndex(
        qualified=qualified, by_simple=by_simple, class_methods=class_methods
    )


def extract_calls(
    module: WalkedModule, index: CallIndex
) -> list[ResolvedCall]:
    calls: set[ResolvedCall] = set()
    for owner, function_ast in module.function_asts.items():
        owner_class = owner.rsplit(".", 1)[0]
        local_types = _typed_locals(function_ast, module, index)
        for call in ast.walk(function_ast):
            if not isinstance(call, ast.Call):
                continue
            callee = _resolve(call.func, module, owner_class, local_types, index)
            if callee and callee != owner:
                calls.add(
                    ResolvedCall(
                        caller_qualified_name=owner,
                        callee_qualified_name=callee,
                        line=call.lineno,
                    )
                )
    return sorted(
        calls,
        key=lambda c: (c.caller_qualified_name, c.line, c.callee_qualified_name),
    )


def _class_of(
    expression: ast.expr, module: WalkedModule, index: CallIndex
) -> str | None:
    """The known class an expression names, if it names one."""
    if not isinstance(expression, ast.Name):
        return None
    bound = module.bindings.get(expression.id)
    if bound is None:
        return None
    resolved = _match(bound, index)
    return resolved if resolved in index.class_methods else None


def _typed_locals(
    function_ast: ast.AST, module: WalkedModule, index: CallIndex
) -> dict[str, str]:
    """name -> class qualified name, from the bounded patterns."""
    types: dict[str, str] = {}
    for argument in ast.walk(function_ast):
        if isinstance(argument, ast.arg) and argument.annotation is not None:
            resolved = _class_of(argument.annotation, module, index)
            if resolved:
                types[argument.arg] = resolved
    for statement in ast.walk(function_ast):
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Call)
        ):
            continue
        value = statement.value
        resolved = None
        if (
            isinstance(value.func, ast.Attribute)
            and value.func.attr == "get"
            and value.args
        ):
            resolved = _class_of(value.args[0], module, index)
        else:
            resolved = _class_of(value.func, module, index)
        if resolved:
            types[statement.targets[0].id] = resolved
    return types


def _resolve(
    func: ast.expr,
    module: WalkedModule,
    owner_class: str,
    local_types: dict[str, str],
    index: CallIndex,
) -> str | None:
    if isinstance(func, ast.Name):
        candidate = module.bindings.get(func.id)
        if candidate is None:
            return None
        return _match(candidate, index)

    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        # self.method() inside a class
        if func.value.id == "self" and func.attr in module.class_methods.get(
            owner_class, set()
        ):
            return f"{owner_class}.{func.attr}"
        # typed_local.method()
        local_class = local_types.get(func.value.id)
        if local_class and func.attr in index.class_methods.get(
            local_class, set()
        ):
            return f"{local_class}.{func.attr}"
        # imported_module.function()
        bound = module.bindings.get(func.value.id)
        if bound is None:
            return None
        return _match(f"{bound}.{func.attr}", index)

    return None


def _match(candidate: str, index: CallIndex) -> str | None:
    if candidate in index.qualified:
        return candidate
    matches = index.by_simple.get(candidate.rsplit(".", 1)[-1], [])
    if len(matches) == 1:
        return matches[0]
    return None
