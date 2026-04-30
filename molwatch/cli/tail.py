"""``molwatch tail`` -- live polling: file -> stream of JSON lines.

Polls the file's mtime every ``--interval`` seconds and emits one
JSON line per detected change.  Each line is a complete
:class:`ParsedTrajectory`; consumers diff against the previous to
find what's new.  Stops cleanly on Ctrl-C.

    molwatch tail run.out --interval 30 | \\
        jq -r '.frames | length' | uniq | \\
        while read N; do echo "now $N frames"; done
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import click

from ..parsers import UnknownFormatError, detect_parser


@click.command()
@click.argument("file", type=click.Path(dir_okay=False))
@click.option("--interval", type=float, default=15.0,
              help="polling cadence in seconds (default: 15, the same "
                   "as the web UI).  Smaller is more responsive but "
                   "burns more I/O.")
@click.option("--once", is_flag=True, default=False,
              help="emit a single snapshot immediately and exit; "
                   "useful for shell loops that want their own polling.")
@click.pass_context
def tail(ctx, file, interval, once):
    """Stream ParsedTrajectory JSON on every detected file change."""
    path = Path(file)
    if not path.is_file():
        click.echo(f"error: file not found: {file}", err=True)
        ctx.exit(2)

    try:
        parser_cls = detect_parser(str(path))
    except UnknownFormatError as exc:
        click.echo(f"error: {exc}", err=True)
        ctx.exit(2)

    # SIGINT -> clean exit (rc 0).
    try:
        signal.signal(signal.SIGINT,
                      lambda *_a: (_ for _ in ()).throw(KeyboardInterrupt))
    except ValueError:
        pass  # not main thread; --once is the only safe path.

    last_mtime = -1.0
    try:
        while True:
            try:
                mtime = os.path.getmtime(str(path))
            except OSError as exc:
                click.echo(f"error: cannot stat {file}: {exc}", err=True)
                ctx.exit(2)

            if mtime != last_mtime:
                try:
                    data = parser_cls.parse(str(path))
                except Exception as exc:
                    # Parse error mid-stream -- log to stderr but keep
                    # the loop alive; SIESTA / PySCF outputs can briefly
                    # look torn while a step is being written.
                    click.echo(
                        f"warning: parse failed at mtime={mtime}: {exc}",
                        err=True,
                    )
                else:
                    sys.stdout.write(json.dumps(data, default=str) + "\n")
                    sys.stdout.flush()
                last_mtime = mtime

            if once:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        return


__all__ = ["tail"]
