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

| backend | source | what's parsed |
| --- | --- | --- |
| `siesta` | `<run>.out` (the main SIESTA log) | per-step coords + E_KS + max-force + per-atom forces + lattice |
| `pyscf`  | `<job>_geom_optim.xyz` (geomeTRIC trajectory) | per-step coords + energy (Hartree → eV).  Max-force pulled from `<job>_geom.qdata.txt` if present alongside. |

Format is **auto-detected** from file content, so the same `Load`
button handles either type — paste in either an absolute path to
`run.out` or to `myjob_geom_optim.xyz` and the right parser is
selected automatically.

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
