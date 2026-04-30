"""``molwatch serve`` -- run the Flask web UI.

Defaults match the historical behavior of ``app.py`` (loopback,
port 5000, debug off).  ``serve`` is also the implicit default
when the user runs bare ``molwatch`` -- see ``molwatch.cli``.
"""

from __future__ import annotations

import click

from .. import web   # imported as a module so tests can patch
                     # ``molwatch.web.run_server`` and have the change
                     # picked up here at call time.


@click.command()
@click.option("--host", default="127.0.0.1",
              help="host to bind to.  Loopback by default; pass an "
                   "external IP only if you really mean to expose "
                   "/api/load to the network.")
@click.option("--port", type=int, default=5000,
              help="port to listen on (default 5000)")
@click.option("--debug", is_flag=True, default=False,
              help="enable Flask's debugger (NEVER on a public address; "
                   "the debugger executes arbitrary code).")
def serve(host, port, debug):
    """Run the browser UI (Flask + 3Dmol.js)."""
    web.run_server(host=host, port=port, debug=debug)


__all__ = ["serve"]
