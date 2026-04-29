"""``molwatch tail`` -- live polling: file -> stream of JSON lines.

Polls the file's mtime every ``--interval`` seconds and emits one
JSON line per detected change.  Each line is a complete
:class:`ParsedTrajectory`; the consumer is expected to diff
against the previous to find what's new.  Stops cleanly on Ctrl-C.

Useful for shell pipelines that want to react to a running
calculation:

    molwatch tail run.out --interval 30 | \
        jq -r '.frames | length' | uniq | \
        while read N; do echo "now $N frames"; done
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from ..parsers import UnknownFormatError, detect_parser
from ._base import Subcommand


class TailCmd(Subcommand):
    name = "tail"
    help = "stream ParsedTrajectory JSON on every detected file change"
    description = (
        "Poll FILE's mtime every --interval seconds and emit one "
        "JSON line per detected change.  Each line is a complete "
        "ParsedTrajectory.  Use --once to emit a single snapshot "
        "and exit (handy when wrapping in your own polling loop).  "
        "Stops cleanly on Ctrl-C."
    )

    @classmethod
    def configure(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("file", help="input file to watch")
        parser.add_argument(
            "--interval", type=float, default=15.0,
            help="polling cadence in seconds (default: 15, the same "
                 "as the web UI).  Smaller is more responsive but "
                 "burns more I/O.",
        )
        parser.add_argument(
            "--once", action="store_true",
            help="emit a single snapshot immediately and exit; "
                 "useful for shell loops that want their own polling.",
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
            print(f"error: {exc}", file=sys.stderr)
            return 2

        # SIGINT -> clean exit (rc 0).  Without this, Ctrl-C raises
        # KeyboardInterrupt up through argparse and we'd return 1.
        # Setting a no-op handler lets the loop's `try/except
        # KeyboardInterrupt` run.
        def _quiet_sigint(_signum, _frame):
            raise KeyboardInterrupt
        try:
            signal.signal(signal.SIGINT, _quiet_sigint)
        except ValueError:
            # Not main thread (e.g. embedded in a test harness);
            # the test will call us with --once anyway.
            pass

        last_mtime = -1.0
        try:
            while True:
                try:
                    mtime = os.path.getmtime(str(path))
                except OSError as exc:
                    print(f"error: cannot stat {args.file}: {exc}",
                          file=sys.stderr)
                    return 2

                if mtime != last_mtime:
                    try:
                        data = parser_cls.parse(str(path))
                    except Exception as exc:
                        # Parse error mid-stream -- log to stderr but
                        # keep the loop alive; SIESTA / PySCF outputs
                        # can briefly look torn while a step is still
                        # being written.
                        print(
                            f"warning: parse failed at mtime={mtime}: "
                            f"{exc}",
                            file=sys.stderr,
                        )
                    else:
                        sys.stdout.write(
                            json.dumps(data, default=str) + "\n"
                        )
                        sys.stdout.flush()
                    last_mtime = mtime

                if args.once:
                    return 0
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


__all__ = ["TailCmd"]
