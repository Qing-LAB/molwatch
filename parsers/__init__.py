"""molwatch parser registry.

Adding support for a new output format is two steps:
  1. drop a new ``foo.py`` module here that defines a ``FooParser``
     subclass of ``TrajectoryParser`` (see base.py for the contract);
  2. import it below and append the class to ``PARSERS``.

The Flask app calls :func:`detect_parser` on every load and never
references a specific parser by name.
"""

from __future__ import annotations

import os
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

    Raises :class:`UnknownFormatError` if no parser matches; the error
    message lists every registered format with its hint, plus a
    targeted suggestion for the most common foot-gun (uploading the
    PySCF .log instead of the geomeTRIC _optim.xyz).
    """
    for cls in PARSERS:
        try:
            if cls.can_parse(path):
                return cls
        except Exception:
            # A parser's sniffer should never raise -- but if it does,
            # don't let one buggy parser take down the dispatch.
            continue

    base = os.path.basename(path)
    lines = [f"No registered parser knows how to handle {base!r}."]
    lines.append("Supported formats:")
    for c in PARSERS:
        suffix = f" -- {c.hint}" if c.hint else ""
        lines.append(f"  * {c.label}{suffix}")

    # Targeted suggestion: a PySCF run produces several files; users
    # often grab the .log because the name is familiar, but the file
    # we want is the geomeTRIC trajectory.  Stem-match instead of just
    # `.log` so we don't spam the suggestion for SIESTA users (whose
    # main output ALSO often ends in .log -- e.g. siesta.log).
    lower = base.lower()
    if (lower.endswith(".log") and
        ("pyscf" in lower or "_relax" in lower or "geom" in lower)):
        # Best guess at what file they wanted.
        stem = base
        for suffix in (".log", "_geom.log", "_pyscf.log"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        lines.append("")
        lines.append(
            f"Looks like a PySCF run -- the streaming trajectory is "
            f"'{stem}_geom_optim.xyz', not '.log'.  Look for the *.xyz "
            f"file in the same folder."
        )

    raise UnknownFormatError("\n".join(lines))


def parser_summary() -> List[dict]:
    """Lightweight metadata used by the /api/formats endpoint."""
    return [
        {"name": c.name, "label": c.label, "hint": c.hint}
        for c in PARSERS
    ]


__all__ = [
    "PARSERS",
    "TrajectoryParser",
    "UnknownFormatError",
    "detect_parser",
    "parser_summary",
]
