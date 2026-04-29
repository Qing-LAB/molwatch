"""``molwatch parse`` -- one-shot file -> ParsedTrajectory JSON.

Detects the format via the parser registry, parses the file, and
prints the full :class:`ParsedTrajectory` as a strict-JSON object
on stdout.  Stderr gets a one-line summary so pipes stay clean.

Useful for shell scripting and tooling that prefers JSON over a
web UI:

    molwatch parse run.out > trajectory.json
    molwatch parse run.out | jq '.energies[-1]'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..parsers import UnknownFormatError, detect_parser
from ._base import Subcommand


class ParseCmd(Subcommand):
    name = "parse"
    help = "parse a file once and emit ParsedTrajectory JSON to stdout"
    description = (
        "Auto-detect the format of FILE, parse it, and print the "
        "full ParsedTrajectory as a JSON object on stdout.  A "
        "one-line summary goes to stderr.  See "
        "``docs/spec/cli.md`` for the contract."
    )

    @classmethod
    def configure(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "file",
            help="input file (SIESTA .out, PySCF .xyz trajectory, "
                 ".molwatch.log, ...)",
        )
        parser.add_argument(
            "--no-pretty",
            action="store_true",
            dest="no_pretty",
            help="emit JSON as a single line (default: 2-space indent). "
                 "Useful when piping to jq.",
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        path = Path(args.file)
        if not path.is_file():
            print(f"error: file not found: {args.file}", file=sys.stderr)
            return 2

        try:
            parser_cls = detect_parser(str(path))
        except UnknownFormatError as exc:
            # The registry's message already lists supported formats
            # plus per-parser mis-load hints -- surface verbatim.
            print(f"error: {exc}", file=sys.stderr)
            return 2

        try:
            data = parser_cls.parse(str(path))
        except Exception as exc:
            print(
                f"error: {parser_cls.name} parser failed on "
                f"{args.file}: {exc}",
                file=sys.stderr,
            )
            return 2

        indent = None if args.no_pretty else 2
        sys.stdout.write(json.dumps(data, indent=indent, default=str))
        if not args.no_pretty:
            sys.stdout.write("\n")

        n_frames = len(data.get("frames") or [])
        print(
            f"parsed {args.file} via {parser_cls.name} "
            f"({n_frames} frames)",
            file=sys.stderr,
        )
        return 0


__all__ = ["ParseCmd"]
