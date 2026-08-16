"""Smoke CLI: `uv run engine info --pack <path>`.

Loads a pack, builds its adapters through the DI container, and prints
a human-readable report — the fastest way to see that a pack directory
is well-formed and what concrete adapter each port resolved to.

argparse over a CLI framework: one subcommand does not justify a
dependency (CLAUDE.md: clear beats clever, and every dep must clear
the wheel rule).
"""

import argparse
import sys

from engine.config.models import PortName
from engine.config.pack_loader import PackLoadError, load_pack
from engine.runtime.container import AdapterBuildError, build
from engine.runtime.registry import UnknownAdapterError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="engine",
        description="Configurable crowdsourced knowledge engine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    info = subparsers.add_parser(
        "info", help="Load a pack and report what it enables and resolves."
    )
    info.add_argument("--pack", required=True, help="Path to a pack directory.")

    args = parser.parse_args(argv)
    if args.command == "info":
        return _info(args.pack)
    return 2  # unreachable while `required=True`, kept for safety


def _info(pack_dir: str) -> int:
    try:
        pack = load_pack(pack_dir)
        ports = build(pack)
    except (PackLoadError, UnknownAdapterError, AdapterBuildError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    config = pack.config
    print(f"Pack: {config.name}")
    if config.description:
        print(f"  {config.description}")
    print()
    print(f"Substrates enabled ({len(config.substrates)}):")
    for substrate in config.substrates:
        print(f"  - {substrate}")
    print()
    print(f"Tools enabled ({len(config.tools)}):")
    for tool in config.tools:
        print(f"  - {tool}")
    print()
    print("Ports:")
    resolved = ports.configured()
    for port in PortName:
        if port in resolved:
            adapter = resolved[port]
            settings = getattr(adapter, "settings", None)
            detail = (
                f" ({', '.join(f'{k}={v}' for k, v in settings.model_dump().items())})"
                if settings is not None
                else ""
            )
            print(f"  {port.value:<15} -> {type(adapter).__name__}{detail}")
        else:
            print(f"  {port.value:<15} -> not configured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
