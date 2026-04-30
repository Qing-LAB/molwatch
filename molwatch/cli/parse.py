"""``molwatch parse`` -- one-shot file -> ParsedTrajectory JSON.

Detects the format via the parser registry, parses the file, and
prints the full :class:`ParsedTrajectory` as a strict-JSON object
on stdout.  Stderr gets a one-line summary so pipes stay clean.

    molwatch parse run.out > trajectory.json
    molwatch parse run.out | jq '.energies[-1]'
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..parsers import UnknownFormatError, detect_parser


@click.command()
@click.argument("file", type=click.Path(dir_okay=False))
@click.option("--no-pretty", is_flag=True, default=False,
              help="emit JSON as a single line (default: 2-space indent). "
                   "Useful when piping to jq.")
@click.pass_context
def parse(ctx, file, no_pretty):
    """Parse FILE once and emit ParsedTrajectory JSON to stdout."""
    path = Path(file)
    if not path.is_file():
        click.echo(f"error: file not found: {file}", err=True)
        ctx.exit(2)

    try:
        parser_cls = detect_parser(str(path))
    except UnknownFormatError as exc:
        # The registry's message lists supported formats + per-parser
        # mis-load hints; surface verbatim.
        click.echo(f"error: {exc}", err=True)
        ctx.exit(2)

    try:
        data = parser_cls.parse(str(path))
    except Exception as exc:
        click.echo(
            f"error: {parser_cls.name} parser failed on {file}: {exc}",
            err=True,
        )
        ctx.exit(2)

    indent = None if no_pretty else 2
    sys.stdout.write(json.dumps(data, indent=indent, default=str))
    if not no_pretty:
        sys.stdout.write("\n")

    n_frames = len(data.get("frames") or [])
    click.echo(
        f"parsed {file} via {parser_cls.name} ({n_frames} frames)",
        err=True,
    )


__all__ = ["parse"]
