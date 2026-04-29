# Spec — parser plug-in interface

**Modules**: `parsers/base.py`, `parsers/molwatch_log.py`,
`parsers/siesta.py`, `parsers/pyscf.py`, `parsers/__init__.py`
&nbsp;·&nbsp; **Tests**: `tests/test_molwatch_log_parser.py`,
`tests/test_siesta_parser.py`, `tests/test_pyscf_parser.py`,
`tests/test_registry.py`

## TrajectoryParser interface

```python
class TrajectoryParser(ABC):
    name:  str = "abstract"   # short id ("siesta", "pyscf")
    label: str = "abstract"   # UI-friendly ("SIESTA .out / .log")
    hint:  str = ""           # what file to point us at

    @classmethod @abstractmethod
    def can_parse(cls, path: str) -> bool: ...

    @classmethod @abstractmethod
    def parse(cls, path: str) -> Dict[str, Any]: ...
```

### `can_parse`

* Cheap content-sniff.  Read at most ~80 lines.  Look for format
  markers (e.g. `"Welcome to SIESTA"`, or `"Iteration K Energy E"`
  on line 2 of an XYZ).
* Return False fast on a mismatch.
* **Must not raise** on unsupported / unreadable files.  An
  exception is treated by the registry as a buggy parser; the
  registry will skip it and try the next one.

### `parse`

* Re-callable: the Flask app calls it on every mtime change.
* Tolerant of in-progress files: torn frames at EOF are dropped, a
  partial step that has coordinates but no energy yet stores
  energy as `None`.
* Returns a dict with a fixed schema:

```python
{
    "frames":      List[List[List[Any]]],        # per step: [[el, x, y, z], ...]
    "energies":    List[Optional[float]],        # eV per step
    "max_forces":  List[Optional[float]],        # eV/Ang per step
    "forces":      List[List[List[float]]],      # eV/Ang per atom per step (or [])
    "iterations":  List[int],                    # length matches frames
    "lattice":     Optional[List[List[float]]],  # 3x3 Ang or None
    "scf_history": List[List[Dict[str, float]]], # see below
    "source_format": str,                        # the parser's `name`
}
```

### `scf_history` schema

Per-engine richer progress data.  Each top-level entry is one
**geom-opt / CG-MD step's SCF run**; each inner entry is one **SCF
iteration** within that run:

```python
[
    [   # opt step 0
        {"cycle":   int,    # SCF iteration counter (resets per step)
         "energy":  float,  # eV
         "delta_E": float,  # eV  (E_cycle - E_prev_cycle)
         <residual key(s)>: float,
         <density-change key>: float},
        ...
    ],
    [...],   # step 1
    ...
]
```

The keys `cycle`, `energy`, `delta_E` are **mandatory** for every
parser.  The remaining keys are **engine-specific** and let the
front-end pick which residual axis to plot:

| Engine | Residual key | Unit  | Density-change key | Unit |
|--------|--------------|-------|--------------------|------|
| PySCF  | `gnorm`      | eV/Å  | `ddm`              | —    |
| SIESTA | `dHmax`      | eV    | `dDmax`            | —    |

The two engines expose different physical quantities:
* PySCF reports `|g|` (orbital-gradient norm) and `|ddm|`
  (density-matrix change norm).
* SIESTA reports `dHmax` (largest Hamiltonian-matrix-element
  change between successive cycles) and `dDmax` (largest
  density-matrix-element change).

Both are valid SCF residuals — just different ones.  The front-end
detects which set of keys is present and labels the residual plot
accordingly (`|g| (eV/Å)` vs `dHmax (eV)`).  No cross-engine
conversion is performed; comparing residuals across engines is not
meaningful.

`scf_history` may be `[]` when the parser couldn't find a companion
log file (PySCF: `<job>.log` missing) or when the SCF section of
the run output is empty.  Front-end consumers MUST handle the
empty case gracefully (hide the SCF panel, don't crash).

The most-recent entry (`scf_history[-1]`) is "the current opt
step's SCF" — what molwatch shows in its live SCF-progress panel.

* All per-step lists must be **index-aligned with `frames`** — the
  JS viewer walks them in lockstep via the slider.
* `None` round-trips to JSON `null`; Plotly draws those as gaps.
* JSON-strict-safe: no `NaN`, no `Inf`.  Tested via
  `json.dumps(result, allow_nan=False)`.

## Unit conventions (cross-format consistency)

This is the spec contract that prevents the SIESTA/PySCF axis-mismatch
bug:

* **Energy** is reported in **eV** by every parser, regardless of the
  source file's native units.  PySCF / geomeTRIC writes Hartree in its
  XYZ comment; the PySCFParser must convert via the standard CODATA
  factor (`1 Hartree = 27.211386245988 eV`).
* **Force** is reported in **eV/Å** as the **maximum per-atom
  magnitude**, i.e. `max_i sqrt(fx_i² + fy_i² + fz_i²)`.  This matches
  SIESTA's `Max <num>` line.  The PySCFParser must compute this from
  the 3N gradient components in the qdata file (Hartree/Bohr →
  eV/Å with `1 Ha/Bohr = 51.42208619 eV/Å`).  Using `max(|F_component|)`
  instead is a spec violation.

## molwatch unified-log parser specifics

`parsers/molwatch_log.py`:

* `name="molwatch"`, `label="molwatch unified log (.molwatch.log)"`,
  `hint="the unified per-step log emitted by molbuilder-generated
  PySCF scripts (e.g. <job>.molwatch.log)"`.
* `can_parse`: matches if any of the first 5 lines starts with the
  literal marker `# molwatch trajectory log`.  This is unambiguous
  by design -- no engine-native format emits that line.
* Registered **first** in `PARSERS`: any run generated through
  molbuilder produces a `.molwatch.log` and that's what the user
  should point at; SIESTA / raw-PySCF parsers stay as fallbacks for
  runs that didn't go through molbuilder.

### Format -- marker-driven, single-file

The file is a sequence of self-describing blocks.  Each block is the
complete, index-aligned data for one geom-opt / CG-MD step:

```text
# molwatch trajectory log v1
# generator: molbuilder/pyscf_input
# engine: <name>                # -> result["source_format"]
# job: <job_name>
# units: energy=eV, force=eV/Ang, coords=Ang
# created: <ISO8601>

==== molwatch step <N> begin ====
step_index: <N>
n_atoms:    <K>
coordinates (Ang):
   <element>   <x>   <y>   <z>
   ...
energy (eV): <E>
forces (eV/Ang):
   <element>  <fx>  <fy>  <fz>
   ...
max_force (eV/Ang): <Fmax>
scf_history begin
#  cycle    energy(eV)    delta_E(eV)    gnorm(eV/Ang)    ddm
       <c>     <e>           <de>           <g>            <d>
   ...
scf_history end
==== molwatch step <N> end ====
```

The `==== molwatch step <N> begin ==== / end ====` markers are the
parser's primary anchors -- everything else is matched by string
prefix on lines like `energy (eV):`, `forces (eV/Ang):`,
`scf_history begin` etc.  Column widths in `scf_history` rows are
cosmetic; whitespace-split + position is what the parser uses.

### Robustness invariants

* **Torn final block** (a `begin` without a matching `end`) is
  dropped silently.  This is the live-tailing case: molwatch reads
  the file while the run is still writing the next step.
* **None residuals**: gnorm or ddm may legitimately be missing for
  a given cycle (e.g. cycle 0 of some SCFs).  The emitter writes the
  literal token `None`; the parser converts to JSON `null`.
* **Engine fallback**: if the `# engine: <name>` header is absent,
  `source_format` defaults to `"molwatch"` so the result still has
  a non-null string.
* **Initial-state preview** (`kind: initial_preview` line inside a
  block): a special block that carries coordinates only, with
  `energy (eV): None`, an empty `forces` section, and an empty
  `scf_history`.  Emitted by molbuilder *before* the engine has
  produced any data, so molwatch can render the molecular structure
  immediately on load.  The parser treats it like any other block
  (frame is captured, energies/forces become `None`); the UI shows
  the geometry but plots a gap on the energy / max-force charts at
  that index.  Subsequent real opt-step blocks fill in the data.

### `scf_history` keys

The molwatch unified log uses the **PySCF residual key set**
(`gnorm` / `ddm`) regardless of who emitted the log -- it's an
internal-format choice, not engine-dependent.  Mandatory: `cycle`,
`energy`, `delta_E`.  Optional / nullable: `gnorm`, `ddm`.  The
front-end residual selector keys on `gnorm` presence (treats it
like a PySCF run for axis labeling).  Future engines that emit a
`.molwatch.log` should populate the same keys; they can leave
`gnorm` / `ddm` as `None` if they don't have analogues, and the UI
will simply not render the residual axis for those runs.

## SIESTA parser specifics

`parsers/siesta.py`:

* `name="siesta"`, `label="SIESTA .out / .log"`, `hint="the main
  SIESTA run output (run.out, siesta.log, etc.)"`.
* `can_parse` is content-based, not banner-based.  Banners reformat
  across versions (v4.x had `Welcome to SIESTA` mixed-case; v5.x
  has `*  WELCOME TO SIESTA  *` and a top-of-file
  `Executable      : siesta` line; future versions may reshuffle
  again).  The detector accepts the file if **either**:
    1. any one *strong content marker* appears in the first 300
       lines -- `outcoor: Atomic coordinates`, `outcell: Unit cell
       vectors`, `Begin CG opt`, `siesta: System type`, `siesta:
       Atomic forces`, plus banner text from v4.x and v5.x as
       safety; **or**
    2. there are at least 3 lines in the first 300 starting with
       `siesta:` or `redata:` (real SIESTA output has dozens of
       these; 3 is a near-certain match while still rejecting
       arbitrary log files).
  Either branch is sufficient on its own; we deliberately don't
  require any specific banner string.
* Per step extracts:
  * coordinates from `outcoor: Atomic coordinates (Ang):` blocks
  * total energy from `siesta: E_KS(eV) = ...`
  * per-atom forces from `siesta: Atomic forces (eV/Ang):` blocks
  * max force from the post-block `Max <value>` line (the
    `constrained` duplicate has 3 tokens, filtered out)
* Lattice: most-recent `outcell: Unit cell vectors (Ang):` block.
* The "Max" line is gated on `step_forces` being non-empty so a
  stray `Max <num>` in a header (M7 fix) can't be misattributed.
* Torn-frame rule: if state is `in_coords` at EOF, the partial
  frame is dropped.
* `scf_history`: collected from the inline `scf:` iteration table
  that appears between every CG/MD step.  Columns parsed:
  `iscf` (cycle), `E_KS` (eV → `energy`), `dDmax`
  (dimensionless), `dHmax` (eV).  A new SCF run starts every time
  `iscf == 1`; the previous run is flushed into `scf_history`.
  `delta_E` is computed within a run as `E_KS - E_KS_prev`, with
  `0.0` for the first cycle of each run.

## PySCF / geomeTRIC parser specifics

`parsers/pyscf.py`:

* `name="pyscf"`, `label="XYZ trajectory (PySCF / geomeTRIC /
  generic multi-frame XYZ)"`, `hint="a multi-frame XYZ trajectory
  -- e.g., geomeTRIC's <job>_geom_optim.xyz.  Generic XYZ with any
  comment-line format is also accepted; energies are extracted only
  when the comment matches the geomeTRIC `Iteration K Energy E`
  pattern."`.
* `can_parse` is structural, not banner-based.  Detection used to
  require the comment line on row 1 to match
  `Iteration <int> Energy <float>` -- that tied the parser to one
  specific tool's comment format and rejected any other trajectory
  writer (ASE, ChemShell, future geomeTRIC reformats, user
  scripts).  The current detector accepts the file if:
    - line 0 is a positive integer (≤ 1,000,000 — atom count),
    - line 1 is anything (comment is informational only),
    - the next ≤ 3 lines parse as atoms (element token + 3 floats).
  Comment-line content is read by `parse()` but not by `can_parse()`.
  When the comment matches the geomeTRIC pattern, energies are
  extracted (Hartree → eV); otherwise the frame is captured with
  `energy=None`.
* Multi-frame XYZ; one frame per `Iteration K Energy E` block.
* Energy converted Hartree → eV per spec.
* `lattice` is always None (geomeTRIC trajectories carry no cell).
* `forces` is always `[[] for _ in frames]` (geomeTRIC's _optim.xyz
  doesn't include per-atom forces).
* Optional companion `<prefix>.qdata.txt` parser: if present
  alongside the `_optim.xyz`, populates `max_forces` per step.
  Format: `ENERGY ...` opens a new frame (flush previous frame's
  max-force on close); `GRADIENT g1 g2 ... g_3N` provides the
  components for the current frame's max-force computation.

* Optional companion **PySCF main log** `<job>.log` parser (note
  the **un-suffixed** name — molbuilder writes the trajectory as
  `<job>_geom_optim.xyz` but the PySCF main log as `<job>.log`).
  When present, `scf_history` is populated from the per-cycle
  table lines `cycle= N E= ... delta_E= ... |g|= ... |ddm|= ...`,
  one inner list per geom-opt step's SCF run (split on
  `converged SCF energy = ...` or `cycle= 0` boundaries).
  Energies are converted to eV, gradient norms to eV/Å for cross-
  format consistency.  When the log is absent or unreadable,
  `scf_history` is the empty list.

## Registry contract (`parsers/__init__.py`)

```python
PARSERS: List[Type[TrajectoryParser]] = [
    SiestaParser,
    PySCFParser,
]

def detect_parser(path) -> Type[TrajectoryParser]:
    """First parser whose can_parse(path) is True wins."""

def parser_summary() -> List[dict]:
    """[{name, label, hint}, ...] — feeds /api/formats."""
```

* Order matters: more-specific format markers go first so a permissive
  parser can't shadow them.
* `detect_parser` raises `UnknownFormatError` (subclass of
  `ValueError`) when nothing matches.  The error message lists every
  registered parser's label + hint AND, for `.log` filenames that
  look like a PySCF run, suggests the corresponding `_geom_optim.xyz`.
* A parser's `can_parse` raising is caught and treated as False
  (registry resilience).

## Adding a new format

Two steps:

1. Drop a new `parsers/<name>.py` defining `<Name>Parser` that
   subclasses `TrajectoryParser` and implements `can_parse` + `parse`
   per this spec.
2. Add the class to `PARSERS` in `parsers/__init__.py`.

The Flask app + front-end pick it up automatically with no other
changes.

## Forbidden patterns

A parser must NOT:

1. Return energies or forces in non-spec units (Hartree / Ha-Bohr /
   etc.).  The eV / eV/Å convention is fixed.
2. Return per-step arrays of mismatched length — `len(frames) ==
   len(energies) == len(max_forces) == len(forces) ==
   len(iterations)` is invariant.
3. Raise from `can_parse`.  Bad input → return False.
4. Open the file in binary mode without an encoding fallback.  Use
   `open(path, "r", errors="replace")` so a stray non-UTF8 byte
   doesn't crash mid-stream.
