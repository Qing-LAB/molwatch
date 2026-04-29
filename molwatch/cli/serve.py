"""``molwatch serve`` -- run the Flask web UI.

Defaults match the historical behavior of ``app.py`` from before
the CLI refactor: loopback, port 5000, debug off.  ``serve`` is
also the *implicit* default when the user runs ``molwatch`` with
no subcommand -- see ``molwatch.cli.__init__``.
"""

from __future__ import annotations

import argparse

from ..web import run_server
from ._base import Subcommand


class ServeCmd(Subcommand):
    name = "serve"
    help = "run the browser UI (Flask + 3Dmol.js)"
    description = (
        "Start the Flask server backing the molwatch browser UI.  "
        "Bare ``molwatch`` (no subcommand) is equivalent to "
        "``molwatch serve``."
    )

    @classmethod
    def configure(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--host", default="127.0.0.1",
            help="host to bind to.  Loopback by default; pass an "
                 "external IP only if you really mean to expose "
                 "/api/load to the network (it can read any local "
                 "file the server can access).",
        )
        parser.add_argument(
            "--port", type=int, default=5000,
            help="port to listen on (default 5000)",
        )
        parser.add_argument(
            "--debug", action="store_true",
            help="enable Flask's debugger (NEVER on a public address; "
                 "the debugger executes arbitrary code).",
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        run_server(host=args.host, port=args.port, debug=args.debug)
        return 0


__all__ = ["ServeCmd"]
