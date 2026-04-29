"""molwatch CLI framework.

Two abstractions, one dispatcher, no per-command branches.

  * :class:`Subcommand` -- a leaf command that defines its argparse
    arguments and runs.
  * :class:`CommandGroup` -- a non-leaf node listing further
    Subcommands or CommandGroups (recursive).

The top-level :func:`main` walks ``COMMAND_TREE`` once at startup,
recursively registers every node into argparse via
:func:`_register`, and dispatches via ``args._run`` (set on the
leaf parser at registration time).  ``main()`` has no per-command
``if/elif`` -- adding a command means adding a class and a list
entry, not editing dispatch.

This module is a near-clone of ``molbuilder/cli/_base.py``.  The
two tools share the same CLI design (see molbuilder
``docs/architecture.md`` §8a and molwatch ``docs/architecture.md``
§6a for the principles).  The duplication is small (~80 lines)
and keeps each tool self-contained; a future shared package
could consolidate them, but the cost (cross-repo dependency)
outweighs the benefit today.

See ``docs/architecture.md`` §6a for the full design rationale,
``docs/spec/cli.md`` for the user-facing reference.
"""

from __future__ import annotations

import argparse
import sys
from abc import ABC, abstractmethod
from typing import (
    Callable, ClassVar, List, Optional, Sequence, Type, Union,
)


# --------------------------------------------------------------------- #
#  Subcommand / CommandGroup ABCs                                       #
# --------------------------------------------------------------------- #


class Subcommand(ABC):
    """A leaf command in the CLI tree.

    Subclasses set the three identification class attributes
    (``name``, ``help``, ``description``) and implement
    :meth:`configure` (define argparse arguments) and :meth:`run`
    (execute and return exit code).
    """

    #: Short slug used as the command word (e.g., "peptide", "siesta").
    name: ClassVar[str] = "<abstract>"

    #: One-line summary shown in the parent's --help listing.
    help: ClassVar[str] = ""

    #: Longer text shown at the top of this subcommand's --help.
    #: Defaults to ``help`` if empty.
    description: ClassVar[str] = ""

    @classmethod
    @abstractmethod
    def configure(cls, parser: argparse.ArgumentParser) -> None:
        """Define this subcommand's flags and positional arguments.

        The parser argument is the subcommand's own ArgumentParser,
        already created by the framework with the documented name +
        help.  Add ``parser.add_argument(...)`` calls here.
        """

    @classmethod
    @abstractmethod
    def run(cls, args: argparse.Namespace) -> int:
        """Execute the subcommand.  Return the exit code.

        Returns 0 on success, 2 on user error (bad input, invalid
        config), 1 on a logic-level failure (an unexpected exception
        downstream).  See ``docs/spec/cli.md`` for the full
        exit-code table.
        """


class CommandGroup(ABC):
    """A non-leaf node grouping further commands under a namespace.

    A group is pure data: it has a name + help + a list of
    children (Subcommands or further Groups).  The framework
    registers a sub-parser for the group and recursively walks
    ``children`` to register everything beneath.

    The grouping is by **purpose** (what kind of work the user
    is doing), not by data type or technology.  See
    architecture.md §8a Principle 1.
    """

    name: ClassVar[str] = "<abstract>"
    help: ClassVar[str] = ""
    description: ClassVar[str] = ""

    #: Subcommands or further groups inside this namespace.  Order
    #: is alphabetical for stability of --help output.
    children: ClassVar[List[Type[Union["CommandGroup", Subcommand]]]] = []


CommandTreeNode = Type[Union[CommandGroup, Subcommand]]


# --------------------------------------------------------------------- #
#  Recursive tree -> argparse registration                              #
# --------------------------------------------------------------------- #


def _register(action: argparse._SubParsersAction,
              node: CommandTreeNode) -> None:
    """Register one node (leaf or group) into the parent's
    sub-action.  Recurses into groups.

    On leaves: creates the parser, calls ``node.configure(parser)``,
    and stashes ``node.run`` on the parser via ``set_defaults`` so
    the dispatcher can find it after parse_args.

    On groups: creates a parser with its own sub-action, then
    recurses for each child.  ``required=True`` on the inner
    sub-action ensures the user can't invoke a group without
    naming a child.
    """
    if isinstance(node, type) and issubclass(node, Subcommand):
        sub_p = action.add_parser(
            node.name,
            help=node.help,
            description=node.description or node.help,
        )
        node.configure(sub_p)
        sub_p.set_defaults(_run=node.run)
        return
    if isinstance(node, type) and issubclass(node, CommandGroup):
        group_p = action.add_parser(
            node.name,
            help=node.help,
            description=node.description or node.help,
        )
        # Each group has its own sub-action (so its children share
        # the same dest namespace).  metavar makes --help cleaner.
        group_sub = group_p.add_subparsers(
            dest=f"_{node.name}_subcommand",
            metavar=node.name.upper(),
            required=True,
        )
        for child in node.children:
            _register(group_sub, child)
        return
    raise TypeError(
        f"command-tree node {node!r} is neither Subcommand "
        f"nor CommandGroup"
    )


def build_main_parser(
    tree: List[CommandTreeNode],
    *,
    prog: str,
    description: str,
) -> argparse.ArgumentParser:
    """Build the top-level argparse parser from a command tree.

    ``tree`` is the ``COMMAND_TREE`` list -- top-level groups +
    top-level Subcommands (e.g., ``serve`` is a top-level leaf).
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    sub = parser.add_subparsers(
        dest="_top_command",
        metavar="COMMAND",
        required=True,
    )
    for node in tree:
        _register(sub, node)
    return parser


# --------------------------------------------------------------------- #
#  Dispatch                                                             #
# --------------------------------------------------------------------- #


def dispatch(args: argparse.Namespace) -> int:
    """Run the leaf subcommand whose ``run`` method was stashed on
    the parser via ``set_defaults(_run=...)`` at registration time.

    Raises :class:`RuntimeError` if dispatch can't find ``_run`` --
    that means the tree was misconfigured (a node was registered
    that wasn't a Subcommand at a leaf position).
    """
    fn: Optional[Callable[[argparse.Namespace], int]] = getattr(
        args, "_run", None,
    )
    if fn is None:
        raise RuntimeError(
            "argparse didn't set _run on the parsed args; "
            "tree misconfiguration -- a leaf node should have "
            "called set_defaults(_run=cls.run) at registration."
        )
    return fn(args)


def run_main(
    tree: List[CommandTreeNode],
    argv: Optional[Sequence[str]],
    *,
    prog: str,
    description: str,
) -> int:
    """End-to-end main: build parser, parse argv, dispatch.

    Catches argparse's ``SystemExit`` on argument errors so the
    function always returns an int -- never raises.  Tests can
    invoke this directly via ``cli_main([...])`` and assert on the
    return code.
    """
    parser = build_main_parser(tree, prog=prog, description=description)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits with int code (usually 2 for argument
        # errors, 0 for --help / --version).  Surface that code
        # as our return value rather than propagating SystemExit.
        return int(exc.code) if isinstance(exc.code, int) else 2
    return dispatch(args)


__all__ = [
    "Subcommand",
    "CommandGroup",
    "CommandTreeNode",
    "build_main_parser",
    "dispatch",
    "run_main",
]
