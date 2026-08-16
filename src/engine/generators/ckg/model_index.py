"""ORM model index: class name -> database table.

Built from __tablename__ declarations collected by the walker across
the whole tree. Resolution at use sites is by SIMPLE class name: the
target app re-exports models through a package __init__, so a binding
like invoiceguard.models.Invoice names the re-export, not the class's
defining module — the simple name is the stable handle. A name
claimed by two classes with different tables is ambiguous and dropped
with a warning rather than guessed.
"""

from engine.generators.ckg.walker import WalkedModule


def build_model_index(
    modules: list[WalkedModule],
) -> tuple[dict[str, str], list[str]]:
    index: dict[str, str] = {}
    warnings: list[str] = []
    for module in modules:
        for class_name, table in sorted(module.tablenames.items()):
            existing = index.get(class_name)
            if existing is not None and existing != table:
                warnings.append(
                    f"model class name {class_name!r} maps to both "
                    f"{existing!r} and {table!r}; dropping it from "
                    f"table-access resolution"
                )
                index[class_name] = ""
            elif existing is None:
                index[class_name] = table
    return {k: v for k, v in index.items() if v}, warnings
