# molwatch

A small Flask + 3Dmol.js webapp that watches a quantum-chemistry
output file *while the calculation is still running* and shows:

- the geometry trajectory as an interactive 3D movie (3Dmol.js)
- total energy per step
- max atomic force per step

The page polls the server every ~15 seconds; when the file's mtime
advances, the parser re-reads the file and the viewer + plots refresh.

## Supported output formats

Output formats are plug-in modules under `parsers/`.  Adding a new
backend is two steps: drop a `foo.py` that subclasses
`TrajectoryParser` (see `parsers/base.py` for the contract), and
append it to the `PARSERS` list in `parsers/__init__.py`.

| backend       | source                                                          | what's parsed                                                                                  |
| ------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `molwatch`    | `<job>.molwatch.log` (molbuilder-generated unified log)         | per-step coords + energy + forces + per-cycle SCF history, all in one self-describing file    |
| `siesta`      | `<run>.out` / `siesta.out` / `siesta.log` (SIESTA v4.x or v5.x) | per-step coords + E\_KS + max-force + per-atom forces + lattice + per-cycle SCF history       |
| `pyscf`       | `<job>_geom_optim.xyz` (or any multi-frame XYZ)                  | per-step coords + energy (Hartree → eV); max-force from optional `<job>_geom.qdata.txt` sibling |

Format is **auto-detected** from file content, so the same `Load`
button handles every type — paste an absolute path to any of them and
the right parser is selected automatically.

### How auto-detection decides

Detection is **content-based** — the file's name and extension are
not used.  When you click Load, molwatch walks the parser registry
in order and picks the **first** parser whose detector accepts the
file:

1. **`molwatch_log`** — checks the first 5 lines for the literal
   marker `# molwatch trajectory log`.  This header is unique to
   molbuilder-generated logs, so any file carrying it wins this
   step regardless of what else is in it.
2. **`siesta`** — reads the first 300 lines.  Accepts the file if
   any structural SIESTA marker appears (`outcoor:`, `outcell:`,
   `Begin CG opt`, `siesta: System type`, ...) **OR** at least 3
   lines start with `siesta:` or `redata:`.  Both v4.x and v5.x
   banners count as markers.
3. **`pyscf`** — reads the first 5 lines.  Accepts the file if it
   has the structural shape of an XYZ trajectory: line 0 is a
   positive integer (atom count), line 1 is any comment, and the
   following lines parse as element + 3 floats.

If none accepts it, you'll see the "No registered parser knows how
to handle ..." error in the status bar, listing what's supported.

### Common debugging cases

| What you see                               | Likely cause                                                            | What to load instead                                                                                  |
| ------------------------------------------ | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| "No parser knows ... `siesta.fdf`"         | You loaded the SIESTA **input** file, not the output                    | `siesta.out` (or whatever you redirected stdout to: `mpirun siesta < siesta.fdf > siesta.out`)         |
| "No parser knows ... `pyscf_relax.log`"    | You loaded the PySCF **runtime log**, not the geomeTRIC trajectory      | `pyscf_relax_geom_optim.xyz` (the `_optim.xyz` sibling), or `pyscf_relax.molwatch.log` if it exists    |
| Empty viewer / "0 frames" mid-run          | The output file exists but no full step has been written yet            | Wait one polling tick (15 s by default), or load `<job>.molwatch.log` for the initial-geometry preview |
| Your file is recognised but parsed wrongly | Borderline format — first parser that claimed it may not be the right one | Check the parsed result's `source_format` field via the API; file an issue with a 30-line head sample  |

### Inspecting a misbehaving file by hand

When detection or parsing seems wrong, the fastest debug step is to
look at what the parser sees:

```bash
head -30 /path/to/your/file
```

Compare against the per-parser markers above — the file should match
exactly one set of detection rules.  If it matches none, see the
"Common debugging cases" table.  If it matches multiple, the
registration order in `parsers/__init__.py` decides which wins (the
order is `molwatch_log` → `siesta` → `pyscf`, most-specific first).

## Layout

```
molwatch/
  app.py                       # Flask app (entry point)
  parsers/                     # plug-in parser modules
    __init__.py                # registry + auto-detect
    base.py                    # TrajectoryParser ABC
    siesta.py                  # SIESTA .out / .log
    pyscf.py                   # PySCF / geomeTRIC trajectory
  templates/index.html         # single-page UI
  static/{style.css, viewer.js}
  tests/                       # pytest suite
  requirements.txt             # flask only; tests via [test] extra
  pyproject.toml
```

## Quick start

```bash
pip install -e .            # or: pip install -r requirements.txt
molwatch                    # http://127.0.0.1:5000
```

Or with custom host/port:

```bash
molwatch --port 8080 --host 0.0.0.0
```

Then open the page, paste the absolute path to your output file, and
click **Load**.

## Security model

`/api/load` accepts an absolute filesystem path from the user and reads
that file off disk.  This is fine when the server is bound to loopback
(`--host 127.0.0.1`, the default) on a single-user machine, but exposing
it on a network interface effectively gives anyone who can reach the
port arbitrary read access to whatever the server process can see.

molwatch prints a loud warning when you bind to anything that isn't
loopback.  If you really need network access (pair-programming, a
shared lab box, etc.):

  * keep it behind a reverse-proxy with auth (nginx + basic-auth, or
    Caddy + JWT, etc.); OR
  * run it on the local machine and tunnel via ssh:
    `ssh -L 5000:localhost:5000 user@compute-node` and use it as if
    it were local.

Browser-side cross-site POSTs to `/api/load` are blocked by the default
CORS policy because the endpoint requires `Content-Type:
application/json` — that triggers a CORS preflight that the browser
rejects (no `Access-Control-Allow-Origin` is sent).  This isn't a
substitute for proper auth, but it does block the obvious cross-origin
phishing-style vector when running locally.

## Running with `molbuilder`

`molwatch` is the live-streaming companion to
[`molbuilder`](https://github.com/Qing-LAB/molbuilder)'s SIESTA / PySCF
script generators.  Typical pipeline:

```bash
# 1. Build a structure and emit a runnable PySCF script
molbuilder dna ATGCATGC --out dna.xyz
molbuilder pyscf dna.xyz dna_relax.py

# 2. Run the script (in another terminal)
python dna_relax.py

# 3. Watch it live in molwatch
python -m molwatch.app          # paste /path/to/dna_relax_geom_optim.xyz
```

For SIESTA: `molbuilder fdf in.xyz dna.fdf` then `siesta < dna.fdf`,
then point molwatch at the resulting `siesta.out`.

## Tests

```bash
pip install -e ".[test]"
pytest -q
```
