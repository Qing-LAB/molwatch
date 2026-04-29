"""molwatch -- live trajectory viewer for SIESTA / PySCF / future codes.

Two surfaces:

  * :mod:`molwatch.cli` -- the command-line interface (``parse``,
    ``tail``, ``inspect``, ``serve``).
  * :mod:`molwatch.web` -- the Flask app + WSGI ``app`` object
    used by ``molwatch serve``.

Parser plug-ins live in :mod:`molwatch.parsers`; see
``docs/architecture.md`` §7 for the registry contract.
"""

from __future__ import annotations

__version__ = "0.1.0"
