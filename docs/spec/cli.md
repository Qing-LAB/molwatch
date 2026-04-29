# Spec — command-line interface

**Module**: `molwatch/cli/` package &nbsp;·&nbsp; **Entry point**:
`molwatch = "molwatch.cli:main"` (declared in `pyproject.toml`)

The molwatch CLI mirrors molbuilder's design: every command is a
class, the top-level ``main()`` walks ``COMMAND_TREE``, argparse
handles dispatch.  Adding a command means adding a class and a
list entry -- no edit to ``main()``, no central dispatch table.

For the design rationale and the five principles applied across
both tools, see ``docs/architecture.md`` §6a.  This document is
the user-facing reference: what the molwatch CLI looks like, how
to invoke it, and how to extend it.

---

## Top-level shape

```
molwatch
├── parse      file -> ParsedTrajectory JSON (one-shot)
├── tail       file -> stream of ParsedTrajectory JSON (live)
├── inspect    parser registry introspection
│   ├── parsers
│   └── parser <name> --schema
└── serve      run the browser viewer (default)
```

molwatch is web-first.  Most users hit ``serve`` (and many invoke
the bare ``molwatch`` command, which is the same).  ``parse`` /
``tail`` / ``inspect`` exist for shell scripting and for tooling
that prefers JSON over a web UI.

---

## Use case examples

### 1. Run the web viewer (the default)

```
molwatch serve
# or just:
molwatch
```

Launches the Flask app on `http://127.0.0.1:5000`.  ``--host``
and ``--port`` override.  ``--debug`` opt-in only.

### 2. Parse a single file to JSON for shell scripting

```
molwatch parse run.out > trajectory.json
```

One-shot: detects the format via the parser registry, parses
the file, prints the full ``ParsedTrajectory`` as a strict-JSON
object on stdout.  Stderr gets a one-line summary.

```
# Pull the converged energy out of a SIESTA run:
molwatch parse run.out | jq '.energies[-1]'

# Or the number of frames so far:
molwatch parse run.out | jq '.frames | length'
```

### 3. Watch a running calculation from the shell

```
molwatch tail run.out --interval 30
```

Polls the file's mtime every 30 seconds and emits one JSON line
per detected change.  Each line is a full ``ParsedTrajectory``;
the consumer is expected to diff against the previous to find
what's new.  Stops on ``Ctrl-C``.

```
# Notify when a new frame is committed:
molwatch tail run.out | jq -r '.frames | length' | uniq | \
    while read N; do echo "now $N frames"; done
```

### 4. Discover what parsers are installed

```
molwatch inspect parsers
# molwatch  -- molwatch unified log (.molwatch.log)
# siesta    -- SIESTA .out / .log
# pyscf     -- XYZ trajectory (PySCF / geomeTRIC / generic multi-frame XYZ)

molwatch inspect parser siesta --schema
# JSON dump of the parser's metadata + detection markers
```

The `inspect` group is registry introspection -- same shape as
molbuilder's `inspect engines`.  Useful when wiring up tooling
that needs to know what's available without parsing source code.

---

## Subcommand contracts

### `parse <file>`

* **Positional ``<file>``**: input file path.  Format detection
  goes through ``parsers.detect_parser``; same content-based
  rules the web `Load` button uses.
* **`--no-pretty`** (default off): emit the JSON as a single line
  rather than 2-space indented.  Useful when piping to `jq` or
  similar.
* On success: writes JSON to stdout, one-line summary to stderr,
  exits 0.
* On unrecognised format / missing file: exits 2 with a clear
  stderr message that lists supported formats (same hint pipeline
  the web's ``UnknownFormatError`` uses).

### `tail <file>`

* **Positional ``<file>``**: input file path.
* **`--interval SECONDS`** (default 15): polling cadence.  Same
  default the web UI uses.
* **`--once`**: emit once, exit immediately.  Useful for shell
  loops that want their own polling.
* Output: one strict-JSON object per line, each a complete
  ``ParsedTrajectory``.  Lines flushed on every change.
* Stops cleanly on ``Ctrl-C`` (SIGINT) with exit code 0.
* On unrecognised format: exits 2 with a clear message before
  starting the loop.

### `inspect` group

* `inspect parsers` -- list registered parsers; emits
  human-readable on stdout (one parser per line) by default,
  ``--json`` for machine-readable.
* `inspect parser <name> [--schema]` -- emit one parser's
  metadata as JSON (with `--schema`) or as human-readable text
  (default).

### `serve`

* `--host` (default `127.0.0.1`).  Loud stderr warning if
  anything other than loopback.
* `--port` (default `5000` -- the Flask default).
* `--debug` (default off; warns about Flask debugger danger).
* Equivalent to today's `molwatch` (no subcommand) for
  back-compat.

---

## Forbidden patterns

Same set as molbuilder's CLI (see ``molbuilder/docs/spec/cli.md``):

1. No per-command branches in `main()`.
2. No exit-code conflation; the table above is the contract.
3. No coupling between argv order and output.
4. No mixing structure data with logging on stdout: ``parse`` and
   ``tail`` write JSON to stdout; everything else (summaries,
   errors, debug) goes to stderr.

---

## How to extend

### Add a new subcommand

1. Drop a `Subcommand` subclass in the right module under
   ``cli/`` (`cli/parse.py`, `cli/inspect.py`, ...).  Implement
   `name`, `help`, `description`, `configure(parser)`, `run(args)`.
2. Append the class to the parent group's `children` list (or
   to `COMMAND_TREE` in `cli/__init__.py` if it's a top-level
   command).
3. Add tests in `tests/test_cli_<group>.py` covering correct +
   error code paths.

### Add a new parser

Register it in ``molwatch.parsers.PARSERS`` per
``docs/architecture.md`` §7.1.  The parser is automatically
visible to ``molwatch inspect parsers`` and to
``molwatch parse``/``tail``; no CLI edit needed.

---

## Test discipline

CLI tests parameterize over real correct + error code paths.
Same shape as molbuilder's test_cli_render.py: real inputs,
exit-code checks, stderr message assertions, end-to-end
equivalence with the underlying Python API.
