"""``molwatch inspect`` -- parser registry introspection.

Mirror of molbuilder's ``inspect engines`` group: read-only
tooling that lets the user discover what parsers are installed
and inspect a single parser's metadata, without parsing source
code or peeking at internal data structures.

  * ``molwatch inspect parsers`` -- list registered parsers.
  * ``molwatch inspect parser <name> [--schema]`` -- show one
    parser's metadata.

Useful when wiring up tooling that needs to know what's available:

    molwatch inspect parsers
    # molwatch_log  -- molwatch unified log (.molwatch.log)
    # siesta        -- SIESTA .out / .log
    # pyscf         -- PySCF / geomeTRIC trajectory

    molwatch inspect parser siesta --schema
    # JSON dump of metadata
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Type

from ..parsers import PARSERS
from ..parsers.base import TrajectoryParser
from ._base import CommandGroup, Subcommand


def _find_parser(name: str) -> Type[TrajectoryParser] | None:
    """Look up a parser class by its ``name`` slug.  Returns ``None``
    if no match -- the caller decides how to surface the error."""
    for cls in PARSERS:
        if cls.name == name:
            return cls
    return None


def _parser_metadata(cls: Type[TrajectoryParser]) -> dict:
    """One parser's metadata as a plain dict, ready for JSON dump.

    Kept minimal on purpose: ``name`` / ``label`` / ``hint`` are
    the registry's public surface today.  When parsers grow more
    metadata (version markers, sample-input pointers, ...) extend
    this here -- everything in :class:`TrajectoryParser` that
    matters to consumers should flow through this helper.
    """
    return {
        "name":  cls.name,
        "label": cls.label,
        "hint":  cls.hint,
        "module": cls.__module__,
        "class":  cls.__qualname__,
    }


# --------------------------------------------------------------------- #
#  Subcommands                                                          #
# --------------------------------------------------------------------- #


class ParsersCmd(Subcommand):
    """List every registered parser."""

    name = "parsers"
    help = "list registered parsers"
    description = (
        "List every parser registered in molwatch.parsers.PARSERS.  "
        "Default output is one line per parser ('<name> -- <label>'); "
        "pass --json for a machine-readable JSON list."
    )

    @classmethod
    def configure(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--json", action="store_true", dest="as_json",
            help="emit a JSON list of parser metadata",
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        if args.as_json:
            print(json.dumps(
                [_parser_metadata(c) for c in PARSERS],
                indent=2,
            ))
            return 0

        # Width-aligned for readability; trivially parseable too
        # (cut -d ' ' -f 1 gets the slug).
        width = max((len(c.name) for c in PARSERS), default=0)
        for c in PARSERS:
            print(f"{c.name:<{width}}  -- {c.label}")
        return 0


class ParserCmd(Subcommand):
    """Show one parser's metadata."""

    name = "parser"
    help = "show one parser's metadata (use --schema for JSON)"
    description = (
        "Show metadata for the parser whose ``name`` is NAME.  By "
        "default emits human-readable text; pass --schema for the "
        "same payload as a JSON object (handy for tooling)."
    )

    @classmethod
    def configure(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("parser_name", metavar="NAME",
                            help="parser slug (e.g. 'siesta')")
        parser.add_argument(
            "--schema", action="store_true",
            help="emit metadata as a JSON object",
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        cls_ = _find_parser(args.parser_name)
        if cls_ is None:
            registered = ", ".join(c.name for c in PARSERS)
            print(
                f"error: no parser named {args.parser_name!r}; "
                f"registered: {registered}",
                file=sys.stderr,
            )
            return 2

        meta = _parser_metadata(cls_)
        if args.schema:
            print(json.dumps(meta, indent=2))
            return 0

        print(f"name:   {meta['name']}")
        print(f"label:  {meta['label']}")
        if meta["hint"]:
            print(f"hint:   {meta['hint']}")
        print(f"module: {meta['module']}.{meta['class']}")
        return 0


# --------------------------------------------------------------------- #
#  The group                                                            #
# --------------------------------------------------------------------- #


class InspectGroup(CommandGroup):
    """Top-level ``inspect`` namespace -- read-only registry views."""

    name = "inspect"
    help = "introspect the parser registry"
    description = (
        "Read-only views into molwatch's parser registry.  Useful "
        "when wiring up tooling that needs to know what's installed "
        "without parsing source code."
    )

    children = [ParserCmd, ParsersCmd]


__all__ = ["InspectGroup", "ParsersCmd", "ParserCmd"]
