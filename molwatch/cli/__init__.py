"""molwatch command-line interface (click-based).

    molwatch
    ├── parse      file -> ParsedTrajectory JSON (one-shot)
    ├── tail       file -> stream of ParsedTrajectory JSON (live)
    ├── inspect    parser registry introspection
    │   ├── parsers
    │   └── parser <name> --schema
    └── serve      run the browser viewer (default)

Bare ``molwatch`` (no subcommand) is equivalent to ``molwatch
serve`` -- preserves the historical CLI shape.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

import click

from .inspect import inspect
from .parse import parse
from .serve import serve
from .tail import tail


@click.group(invoke_without_command=True,
             context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def cli(ctx):
    """molwatch -- live trajectory viewer + parser CLI for SIESTA /
    PySCF / molwatch unified logs.  Run bare ``molwatch`` for the
    web UI; use the subcommands for shell-friendly JSON I/O."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)


cli.add_command(inspect)
cli.add_command(parse)
cli.add_command(serve)
cli.add_command(tail)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry-point for ``[project.scripts]`` and tests.  Catches
    ``SystemExit`` so ``cli_main([...])`` returns the int rather
    than terminating the process."""
    try:
        cli.main(args=argv, prog_name="molwatch", standalone_mode=True)
        return 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 2


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())


__all__ = ["main", "cli", "inspect", "parse", "serve", "tail"]
