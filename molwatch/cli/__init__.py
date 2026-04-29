"""molwatch command-line interface.

Top-level shape (see ``docs/spec/cli.md`` for the full reference):

    molwatch
    ├── parse      file -> ParsedTrajectory JSON (one-shot)
    ├── tail       file -> stream of ParsedTrajectory JSON (live)
    ├── inspect    parser registry introspection
    │   ├── parsers
    │   └── parser <name> --schema
    └── serve      run the browser viewer (default)

Bare ``molwatch`` (no subcommand) is equivalent to ``molwatch
serve``.  This back-compat route preserves the historical CLI
shape -- everything that used to invoke ``app.py`` keeps working.

Design rationale: ``docs/architecture.md`` §6a (mirrors molbuilder's
§8a, with the same five principles).
"""

from __future__ import annotations

import sys
from typing import List, Optional, Sequence

from ._base import CommandTreeNode, run_main
from .inspect import InspectGroup
from .parse import ParseCmd
from .serve import ServeCmd
from .tail import TailCmd


# --------------------------------------------------------------------- #
#  The command tree                                                     #
# --------------------------------------------------------------------- #

#: Top-level commands, alphabetical for stable ``--help`` output.
COMMAND_TREE: List[CommandTreeNode] = [
    InspectGroup,
    ParseCmd,
    ServeCmd,
    TailCmd,
]


# --------------------------------------------------------------------- #
#  Entry point                                                          #
# --------------------------------------------------------------------- #


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse argv, dispatch.  Bare ``molwatch`` -> ``molwatch serve``.

    The bare-form back-compat handling lives here rather than in
    ``run_main`` so the framework stays generic (molbuilder doesn't
    want a default subcommand; molwatch does).
    """
    # `argv is None` -> argparse picks up sys.argv[1:].  Look at it
    # ourselves first so we can default-route an empty invocation.
    effective = list(sys.argv[1:] if argv is None else argv)

    # If the user invoked bare `molwatch` (no subcommand and no
    # global flag like `--help`), route through `serve` so the
    # historical "just run the web UI" workflow still works.
    if not effective:
        effective = ["serve"]

    return run_main(
        COMMAND_TREE,
        effective,
        prog="molwatch",
        description=(
            "molwatch -- live trajectory viewer + parser CLI for "
            "SIESTA / PySCF / molwatch unified logs.  Run bare "
            "``molwatch`` for the web UI; use the subcommands for "
            "shell-friendly JSON I/O."
        ),
    )


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())


__all__ = [
    "main",
    "COMMAND_TREE",
    "InspectGroup",
    "ParseCmd",
    "ServeCmd",
    "TailCmd",
]
