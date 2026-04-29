# molwatch — architecture reference

This document is the **standing reference** for how molwatch is put
together.  It covers what we're trying to build, the design choices
that shaped the codebase, the implementation contracts that hold
the pieces together, and how to extend the system without breaking
those contracts.  Expect to revisit and update it as the codebase
grows.

If a code change makes any section here untrue, the change must
update this document in the same commit.

---

## 1. Goal

molwatch is a single-page Flask application for live-watching the
output of running quantum-chemistry calculations.  Its operator is
a researcher who:

- starts a SIESTA / PySCF / similar run (often via molbuilder),
- points molwatch at the output file (a path or an upload),
- watches geometry frames, energies, and SCF convergence stream in
  as the calculation progresses,
- exports the converged structure to feed into the next step
  (typically a transport or tunneling-gap setup).

The app must work for:

- live, mtime-polled file paths on the user's local disk,
- one-shot file uploads (when the path can't be observed remotely),
- partially-written files (a run that's halfway through writing
  step 47 must not crash the parser),
- multiple engine output formats (SIESTA v4 / v5, geomeTRIC XYZ
  trajectories, the unified .molwatch.log emitted by molbuilder).

It must NOT require:

- a build step (no webpack, no transpilation: vanilla JS only),
- a database (single-user, single-tab, in-memory state),
- per-format custom UI code (one rendering pipeline serves all
  parsers).

---

## 2. Design principles

These are the rules every part of the codebase obeys.  When in
doubt, they win over local convenience.

### 2.1  Single source of truth for every contract

Every cross-module contract (the parser-result schema, the
auto-block-size rule, the file-detection order) lives in **one
canonical location** -- usually a Python class or function.  Other
modules reference it; they don't paraphrase it.  The reference is
also reproduced in human-readable docs but the **code wins** on
any drift.

Concretely: the parser-result schema is `parsers.base.ParsedTrajectory`;
the schema-conformance test enforces it; `docs/spec/parsers.md`
documents it.  When new fields are added, the TypedDict gains them
first, the test asserts on them, and the doc is updated to match.

### 2.2  Content-based detection, not name-based

File detection (`parsers.detect_parser`) keys on the structural
content of the file -- block markers, prefix counts, atom-line
shape -- not on filename or extension.  Banner strings come and go
between engine versions; the structural content of the output is
what's stable.

Concretely: the SIESTA detector accepts on either a strong content
marker (`outcoor:`, `Begin CG opt`, etc.) OR three-or-more
`siesta:`/`redata:` prefixed lines in the head.  The PySCF detector
accepts any well-formed XYZ structure regardless of the comment
line.  Neither requires a specific banner.

### 2.3  Tolerate partial input; never crash on it

Every parser must handle:

- a torn last frame (mid-write at EOF) by dropping it,
- per-step values that haven't been written yet (energy, forces,
  SCF cycles) by returning `None` rather than raising,
- absent sibling files by surfacing them via `missing_companions`
  rather than silently degrading.

The schema invariants (alignment, JSON-strict-safety) hold even
on partial input.

### 2.4  Engine-aware UI labels, data-driven where possible

The frontend's labels and axis titles adapt to the loaded file's
engine (`source_format`).  Where the *data* itself decides
behaviour (residual key sniff for `gnorm` vs `dHmax`), the UI keys
on the data, not on `state.format` -- so a fourth engine's parser
that emits the same key set Just Works without UI changes.

### 2.5  Test discipline: spec-derived, not implementation-coupled

Tests assert documented behaviour, not specific code paths.  The
schema-conformance suite is the canonical example: it parametrizes
over `PARSERS` and runs invariant assertions against every parser's
output without knowing how each parser is implemented internally.

---

## 3. Module layout

```
molwatch/
├── molwatch/                  # The Python package
│   ├── __init__.py
│   ├── web.py                 # Flask app + run_server (used by `serve`)
│   ├── cli/                   # Command-line interface (§6a)
│   │   ├── __init__.py        # main() + COMMAND_TREE
│   │   ├── _base.py           # Subcommand / CommandGroup ABCs + dispatch
│   │   ├── parse.py           # `parse`  -- file -> JSON (one-shot)
│   │   ├── tail.py            # `tail`   -- file -> stream of JSON
│   │   ├── inspect.py         # `inspect parsers` / `inspect parser <n>`
│   │   └── serve.py           # `serve`  -- run the browser viewer
│   ├── parsers/               # Parser plug-in subsystem
│   │   ├── __init__.py        # Registry + detect_parser + UnknownFormatError
│   │   ├── base.py            # TrajectoryParser ABC + ParsedTrajectory
│   │   ├── _result.py         # assemble_trajectory: schema enforcement
│   │   ├── molwatch_log.py    # .molwatch.log unified format (preferred)
│   │   ├── siesta.py          # SIESTA v4.x / v5.x .out / .log
│   │   └── pyscf.py           # geomeTRIC trajectory + siblings
│   ├── templates/index.html   # Single-page UI markup
│   └── static/
│       ├── style.css          # Dark theme, responsive layout
│       └── viewer.js          # 3Dmol viewer + Plotly + polling + tabs
├── tests/                     # pytest suite
│   ├── test_schema_conformance.py   # cross-parser schema enforcement
│   ├── test_siesta_parser.py        # format-specific tests
│   ├── test_pyscf_parser.py
│   ├── test_molwatch_log_parser.py
│   ├── test_registry.py             # detect_parser, error messages
│   ├── test_api_load.py             # Flask load + polling endpoints
│   ├── test_app_concurrency.py      # lock-based race-condition guards
│   └── test_cli.py                  # parse / tail / inspect coverage
├── pyproject.toml             # entry point: `molwatch = molwatch.cli:main`
└── docs/
    ├── architecture.md        # ← this file
    └── spec/                  # per-module contract docs
        ├── README.md
        ├── api.md
        ├── cli.md
        ├── parsers.md
        └── ui.md
```

---

## 4. The parser subsystem

### 4.1  Contract: `parsers.base`

`parsers.base` is the **canonical reference** for what every parser
must produce.  The contract is **enforced via tests, not runtime
type-checking**: `ParsedTrajectory` is a `TypedDict` (documentation
+ static-checker hints), and the conformance suite at
`tests/test_schema_conformance.py` runs the invariant assertions
against every registered parser at every test run.  We do not
validate at parse time because doing so would either slow normal
parsing or raise on partial-input cases the parser is supposed to
tolerate.

Three things live in `parsers.base`:

1. **`TrajectoryParser`** — abstract base class with `name`,
   `label`, `hint`, `can_parse`, `parse`, `common_mistakes`.
   Subclasses implement `can_parse` (cheap content-based detection)
   and `parse` (full extraction returning a `ParsedTrajectory`),
   and may override `common_mistakes` to declare format-specific
   foot-gun hints.

2. **`ParsedTrajectory`** — TypedDict declaring the result schema.
   Required fields: `frames`, `energies`, `max_forces`, `forces`,
   `iterations`, `lattice`, `scf_history`, `source_format`.
   Optional fields (must be present, may be `None`/`[]`):
   `created_at`, `missing_companions`.  See the docstring in
   `parsers/base.py` for field-level units, semantics, and what
   `None` means in each context.

   Canonical `source_format` values (the front-end branches on
   these):

   | value      | source                                                     |
   |------------|------------------------------------------------------------|
   | `siesta`   | SiestaParser; or molwatch_log file with `# engine: siesta` |
   | `pyscf`    | PySCFParser; or molwatch_log file with `# engine: pyscf`   |
   | `molwatch` | MolwatchLogParser fallback when no `# engine:` header      |

   New parsers MUST pick a stable, documented value and add it to
   this table.

3. **`REQUIRED_KEYS` / `OPTIONAL_KEYS`** — frozensets used by the
   conformance suite to assert key presence.

The schema invariants:

- **Alignment**: `len(frames) == len(energies) == len(max_forces)
  == len(forces) == len(iterations) == len(scf_history)`.  The JS
  slider walks them in lockstep.
- **JSON-strict-safety**: `json.dumps(result, allow_nan=False)`
  succeeds.  No NaN, no Inf — the assembler sanitises them to
  `None` automatically.
- **Sibling transparency**: `missing_companions` lists the canonical
  paths the parser expected as siblings but didn't find (PySCF
  parser populates this; others leave it empty).

### 4.2  The assembler: `parsers._result`

`assemble_trajectory()` is the single function every parser calls
at its return path.  It takes whatever per-step lists the parser
collected (in whatever shape, possibly partial, possibly with
NaN/Inf in the floats) and produces a contract-conformant
`ParsedTrajectory`:

- **Pads / truncates** per-step arrays to `len(frames)`.
- **Sanitises** floats (NaN / Inf → `None`).
- **Defaults** `iterations` to `range(N)` when the parser doesn't
  track engine-native step indices.
- **Builds** the dict in the canonical field order.

This is intentionally a thin function, not a framework.  The
state-machine logic in each parser's `parse()` is meaningfully
different by format and isn't shared; only the assembly+invariant
layer is.

### 4.3  Detection: `parsers.__init__`

`detect_parser(path)` walks the `PARSERS` list in order and returns
the first parser whose `can_parse(path)` returns `True`.  Order
matters: more-specific parsers (e.g., MolwatchLogParser, which
keys on a unique header) come before more-permissive ones (e.g.,
PySCFParser, which accepts any well-formed XYZ).

When no parser claims the file, `UnknownFormatError` is raised
with an enumerated list of supported formats and targeted hints
for the most common foot-guns (`.fdf` is the SIESTA *input*, not
output; `.log` is PySCF runtime output, not the geomeTRIC
trajectory).

The hint logic lives on each parser class via the
``common_mistakes(path)`` classmethod.  ``detect_parser`` simply
asks every registered parser for its hints when no `can_parse`
match is found and concatenates them.  Adding a fourth or fifth
parser with new foot-guns means overriding ``common_mistakes`` on
that parser -- no change to the registry function.

### 4.4  Conformance enforcement: `tests/test_schema_conformance`

The conformance suite parametrizes over every registered parser
and runs five invariant assertions per parser:

1. The parser's `can_parse` accepts its own minimal fixture.
2. All `REQUIRED_KEYS` appear in the result.
3. No keys outside `REQUIRED_KEYS | OPTIONAL_KEYS` appear.
4. All per-step lists have length `len(frames)`.
5. Result round-trips through `json.dumps(allow_nan=False)`.
6. Value types match (e.g., `iterations` is `List[int]`,
   `missing_companions` is `List[str]`).

Adding a new parser requires adding a fixture builder to
`_FIXTURE_BUILDERS` in this test file; a new parser without a
fixture fails fast with an explicit "no fixture" error.

---

## 5. The Flask app

`molwatch/web.py` exposes three routes (used by the browser UI
loaded via ``molwatch serve``):

- `GET /` — the single-page UI (templates/index.html).
- `POST /api/load` — load a file by path (JSON `{"path": ...}`)
  or by upload (multipart).  Atomic state replacement under
  `_lock`; on success, returns the parsed result alongside
  metadata.
- `GET /api/data?mtime=<float>` — poll endpoint.  Returns
  `{changed: false}` when the file's mtime is unchanged; otherwise
  re-parses and returns the new result.

Process-global state lives in `_state: WatchedFileState` (a
TypedDict with field-level documentation).  Concurrency is managed
by `_lock`: parses happen outside the lock (so a slow parse
doesn't block other requests), and the lock is re-acquired only
to commit if the file's identity hasn't changed since we started.

`/api/load` clears any previously uploaded temp file, so multiple
file-picker uploads in a session don't leak disk space.

---

## 6. The UI

### 6.1  Layout (`static/style.css`)

The dashboard is a single-page grid with three rows:

- **Row 1 (`viewer-row`)**: 3D viewer + tabbed controls aside.
  Both columns are locked to the same height via the
  `--viewer-height` CSS variable (`clamp(360px, 52vh, 500px)`).
  The controls aside has internal scroll so it can never push
  the rest of the page down.
- **Row 2 (`plots-row`)**: energy-vs-step + max-force-vs-step
  Plotly canvases.
- **Row 3 (`scf-row`, hidden when empty)**: per-cycle SCF
  progress for the most recent step, with engine-aware banner
  title and residual axis.

### 6.2  Controls aside

Three tabs: Style (representation, color, background), Overlays
(force vectors, atom indices), Playback (speed, loop, save-frame).
A persistent "frame strip" above the tabs holds the
prev/play/pause/next buttons and the frame slider, so playback
controls are reachable regardless of which tab is open.

### 6.3  Engine adaptation in `viewer.js`

Three things adapt by engine, all keyed on `state.format` (which
is the parser's `source_format` field):

- **SCF banner title**: "SIESTA DFT SCF progress" (always DFT for
  SIESTA), "PySCF SCF progress" (could be HF or DFT — generic),
  "SCF progress" (fallback).
- **Step label**: "CG/MD step" (SIESTA), "Geom-opt step" (PySCF),
  "Opt step" (fallback).
- **Residual axis**: `|g|` (eV/Å) when cycle dicts have `gnorm`
  (PySCF set), `dHmax` (eV) when they have `dHmax` (SIESTA set).
  This last selection is **data-driven** -- it inspects
  `scf_history[-1][0]`'s keys -- so a future engine that emits the
  same key set works without code changes.

---

## 6a. CLI design

molwatch is web-first -- the primary user surface is the browser
viewer at ``/``.  The CLI keeps that as the default (bare
``molwatch`` routes to ``serve``) and adds a small set of utility
commands organised under the **same registry + namespace pattern**
molbuilder uses.  The shared discipline makes the two tools feel
like one ecosystem.

### Five principles (same as molbuilder)

1. **Two-level namespace by *kind of work***.
2. **Subcommand registry**, not hardcoded dispatch.
3. **Config / contract metadata drives the surface** -- when
   relevant (here it's the parser registry, not an engine config,
   but the principle is the same: "the thing being plugged in
   describes its own surface").
4. **Argument groups reflect contract sections** (when the
   subcommand has more than a handful of flags).
5. **Same data path for CLI / web / future automation** -- both
   surfaces dispatch through the parser registry.

### Top-level shape

```
molwatch
├── parse      (file -> ParsedTrajectory; one-shot, exit on EOF)
├── tail       (file -> stream of ParsedTrajectory deltas as it grows)
├── inspect    (introspection on the parser registry)
│   ├── parsers
│   └── parser <name> --schema
└── serve      (single command, no group; the default)
```

The grouping is intentionally **shallower** than molbuilder's
because molwatch has fewer kinds of work: there's no "build" /
"modeling" distinction (molwatch reads files, it doesn't generate
them).  ``parse`` and ``tail`` are useful for cluster scripting
(grep a converged energy out of a SIESTA ``.out`` without firing
up a browser).  ``inspect`` mirrors molbuilder's.  ``serve`` is
the current default behaviour and remains the entry point most
users will hit.

### Subcommand contracts

* **``molwatch parse <file>``** -- detect format via
  ``parsers.detect_parser``, parse, emit a single JSON object
  conforming to ``ParsedTrajectory`` to stdout.  Suitable for
  shell pipelines.  Exit codes: ``0`` success, ``2`` user
  error (file missing / unrecognised format), ``1`` parser
  exception.

* **``molwatch tail <file>``** -- like ``parse``, but instead of
  one-shot it polls ``mtime`` (default every 15 s, ``--interval``
  to override) and emits one JSON line per detected change.
  Each line is a complete ``ParsedTrajectory``; consumers can
  diff against the previous to find what's new.  Stops on
  ``Ctrl-C``.

* **``molwatch inspect parsers``** -- list registered parsers
  with their hints (the same payload the
  ``parser_summary()`` helper returns, used internally for
  ``UnknownFormatError`` messages).

* **``molwatch inspect parser <name> --schema``** -- emit the
  parser's name / label / hint / detection-marker docstring as
  JSON.  Future automation can build documentation or web-form
  pickers from this.

* **``molwatch serve``** -- the existing Flask launcher.
  ``--host`` / ``--port`` / ``--debug``, same defaults as today.

### How to extend (CLI)

Same as molbuilder.  Add a ``Subcommand`` subclass in the right
module under ``cli/`` (or create a new ``CommandGroup``); append
to ``COMMAND_TREE`` in ``cli/__init__.py``.  No edit to
``main()`` or central dispatch.

### Status (Apr 2026)

Implemented.  ``molwatch/cli/`` ships the framework
(``_base.py``) and the four subcommand modules
(``parse.py`` / ``tail.py`` / ``inspect.py`` / ``serve.py``).
Bare ``molwatch`` invokes ``serve`` for back-compat with the
pre-CLI shape.  The pyproject entry point is
``molwatch = molwatch.cli:main``.

Tests: ``tests/test_cli.py`` covers happy paths + error matrix
(missing file, unrecognised format, unknown parser name, bad
subcommand) for every subcommand.  148 tests passing across
the suite (134 existing + 14 new).

---

## 7. How to extend

### 7.1  Add a new parser for a new engine output format

1. Drop a new module under `parsers/` (e.g., `parsers/orca.py`)
   that subclasses `TrajectoryParser`.
2. Implement `can_parse(path)` keyed on **structural content**,
   not banner text.  Read the first 80–300 lines.  Return False
   fast on a mismatch; do not raise.
3. Implement `parse(path)` collecting per-step data however makes
   sense for the format.  At the return path, hand everything to
   `assemble_trajectory()` -- do not build the dict by hand.
4. Register the class in `parsers/__init__.py`'s `PARSERS` list.
   Order: more-specific parsers first, more-permissive last.
5. Add a fixture builder to `_FIXTURE_BUILDERS` in
   `tests/test_schema_conformance.py`.  The conformance suite
   will then automatically run all six invariant assertions
   against the new parser.
6. Update `docs/spec/parsers.md`'s file-to-parser map and the
   per-parser specifics section.
7. If the engine's UI labels should differ from the existing
   set, add a branch in `viewer.js`'s `renderScfProgress`.

### 7.2  Add a new optional schema field

1. Add the field to `ParsedTrajectory` in `parsers/base.py` with
   a docstring describing units / semantics / when `None` means
   what.
2. Add the key to `OPTIONAL_KEYS`.
3. Update each existing parser to populate the field (set to
   `None` / `[]` when the parser can't extract it).  Parsers
   MUST emit every optional key, even if the value is `None`.
4. Update `assemble_trajectory()` if the field needs default-
   filling or sanitisation.
5. Update the schema-conformance suite if the field has type
   constraints worth checking.
6. Update `docs/spec/parsers.md` and this document's section 4.1.
7. Update `viewer.js` if the field should affect the UI.

### 7.3  Change a UI label or layout

1. Templates and styles live in `templates/index.html` and
   `static/style.css`.  Add new IDs so the JS can find elements;
   never rely on classnames for queryable behaviour.
2. Document any new id in `docs/spec/ui.md`.
3. If the change is engine-dependent, branch on `state.format`
   (or sniff data keys when the choice is data-driven) inside
   `viewer.js`.  Don't hard-code engine-specific text in the HTML.

---

## 8. Forbidden patterns

These are decisions that have hurt us before; the comments at the
relevant sites also flag them.

- **Banner-string detection**.  SIESTA changes its banner across
  versions; it's not a stable identifier.  Use structural markers.
- **Direct `result["frames"].append(...)` without going through
  `assemble_trajectory`**.  The bookkeeping (alignment, NaN
  sanitisation, schema-key consistency) lives in one place;
  parsers that bypass it will silently violate invariants.
- **JS-side duplication of a Python rule**.  If the backend has
  the algorithm, the backend computes the value and ships it in
  the response.  No "mirror this Python function in JS" with a
  TODO comment.
- **Silent degradation when a sibling file is missing**.  If the
  PySCF parser expects `<job>.qdata.txt` and doesn't find it,
  it must add the path to `missing_companions` so the UI can tell
  the user.  Returning empty arrays without explanation is the
  hostile behaviour that motivated the field.
- **Tests that assert exact response strings or implementation
  internals**.  Spec-derived tests (TypedDict conformance,
  invariant assertions, contract checks) survive refactors;
  string-matching tests don't.

---

## 9. Future work (under consideration)

These are known improvements not yet implemented; they would each
be their own focused commit / session.

- **Modular `viewer.js`** — the IIFE could host named sub-objects
  (`molwatch.ui`, `molwatch.poll`, `molwatch.plots`) for clarity
  on a 800+-line file, without introducing a build step.
- **PySCF method extraction** from `<job>.log` so the SCF banner
  title can read "PySCF DFT SCF progress" or "PySCF HF SCF
  progress" specifically.
- **In-app crisis support**: when a run hangs or fails, surface
  the SIESTA / PySCF error message inline rather than just
  showing "0 frames".

---

## 10. Versioning of this document

When the architecture meaningfully changes — new parser added,
schema field added or removed, layout reshaped, contract changed
— this document MUST be updated in the same commit.  The other
spec docs in `docs/spec/` are detail-level companions to this
overview.
