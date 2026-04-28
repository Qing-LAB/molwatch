"""molwatch parser registry.

Adding support for a new output format is two steps:
  1. drop a new ``foo.py`` module here that defines a ``FooParser``
     subclass of ``TrajectoryParser`` (see base.py for the contract);
  2. import it below and append the class to ``PARSERS``.

The Flask app calls :func:`detect_parser` on every load and never
references a specific parser by name.
"""

from __future__ import annotations

from typing import List, Type

from .base import TrajectoryParser
from .siesta import SiestaParser
from .pyscf import PySCFParser


# Order matters: the first parser whose ``can_parse(path)`` returns
# True wins.  Put more-specific parsers (with stricter format markers)
# first so they aren't shadowed by a permissive one.
PARSERS: List[Type[TrajectoryParser]] = [
    SiestaParser,
    PySCFParser,
]


class UnknownFormatError(ValueError):
    """No registered parser claims to understand this file."""


def detect_parser(path: str) -> Type[TrajectoryParser]:
    """Return the first parser class whose ``can_parse(path)`` is True.

    Raises :class:`UnknownFormatError` if no parser matches.
    """
    for cls in PARSERS:
        try:
            if cls.can_parse(path):
                return cls
        except Exception:
            # A parser's sniffer should never raise -- but if it does,
            # don't let one buggy parser take down the dispatch.
            continue
    raise UnknownFormatError(
        f"no registered parser knows how to handle {path!r}.  "
        f"Supported formats: " +
        ", ".join(f"{c.label} ({c.name})" for c in PARSERS)
    )


def parser_summary() -> List[dict]:
    """Lightweight metadata used by the /api/formats endpoint."""
    return [{"name": c.name, "label": c.label} for c in PARSERS]


__all__ = [
    "PARSERS",
    "TrajectoryParser",
    "UnknownFormatError",
    "detect_parser",
    "parser_summary",
]
