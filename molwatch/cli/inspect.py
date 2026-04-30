"""``molwatch inspect`` -- parser registry introspection.

  * ``molwatch inspect parsers`` -- list registered parsers.
  * ``molwatch inspect parser <name> [--schema]`` -- show one
    parser's metadata (or JSON dump with ``--schema``).
"""

from __future__ import annotations

import json
from typing import Type

import click

from ..parsers import PARSERS
from ..parsers.base import TrajectoryParser


def _find_parser(name: str):
    for cls in PARSERS:
        if cls.name == name:
            return cls
    return None


def _parser_metadata(cls: Type[TrajectoryParser]) -> dict:
    return {
        "name":   cls.name,
        "label":  cls.label,
        "hint":   cls.hint,
        "module": cls.__module__,
        "class":  cls.__qualname__,
    }


@click.group()
def inspect():
    """Introspect the parser registry."""


@inspect.command()
@click.option("--json", "as_json", is_flag=True, default=False,
              help="emit JSON list of parser metadata")
def parsers(as_json):
    """List registered parsers."""
    if as_json:
        click.echo(json.dumps(
            [_parser_metadata(c) for c in PARSERS], indent=2,
        ))
        return
    width = max((len(c.name) for c in PARSERS), default=0)
    for c in PARSERS:
        click.echo(f"{c.name:<{width}}  -- {c.label}")


@inspect.command("parser")
@click.argument("parser_name", metavar="NAME")
@click.option("--schema", is_flag=True, default=False,
              help="emit metadata as a JSON object")
@click.pass_context
def parser_cmd(ctx, parser_name, schema):
    """Show one parser's metadata."""
    cls_ = _find_parser(parser_name)
    if cls_ is None:
        registered = ", ".join(c.name for c in PARSERS)
        click.echo(
            f"error: no parser named {parser_name!r}; "
            f"registered: {registered}",
            err=True,
        )
        ctx.exit(2)

    meta = _parser_metadata(cls_)
    if schema:
        click.echo(json.dumps(meta, indent=2))
        return

    click.echo(f"name:   {meta['name']}")
    click.echo(f"label:  {meta['label']}")
    if meta["hint"]:
        click.echo(f"hint:   {meta['hint']}")
    click.echo(f"module: {meta['module']}.{meta['class']}")


__all__ = ["inspect"]
