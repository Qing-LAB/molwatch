# SIESTA live viewer

A small Flask + 3Dmol.js webapp that watches a SIESTA output file while
the calculation is still running and shows:

- the geometry trajectory as an interactive 3D movie (3Dmol.js)
- total energy `E_KS` per CG step
- max atomic force per CG step

The page polls the server every ~15 seconds; when the file's mtime
advances, the parser re-reads the file and the viewer + plots refresh.

## Layout

```
siesta_viewer/
  app.py             # Flask app  (entry point)
  siesta_parser.py   # output-file parser (no Flask deps)
  requirements.txt
  templates/
    index.html
  static/
    style.css
    viewer.js
```

## Install & run

```bash
pip install -r requirements.txt
python app.py                # http://127.0.0.1:5000
# or
python app.py --port 8080 --host 0.0.0.0
```

Then open the URL, paste the absolute path to your SIESTA output (e.g.
`/mnt/y/Github/quantum_simulation/BDT_tunneling/junc.out`), and click
**Load**.  The viewer jumps to the latest frame; new frames are appended
as the calculation writes them.

## Style controls

- **Representation:** stick / ball &amp; stick / sphere (CPK) / line
- **Atom radius scale:** scales sticks and sphere radii together
- **Color scheme:** Jmol / Rasmol / 3Dmol default
- **Background:** white / light grey / black
- **Show unit cell:** toggles a wireframe lattice box

## Playback

Play / Pause, step ‹ ›, frame slider, configurable FPS, optional loop.

## What is parsed

| Field         | Source line                              |
|---------------|------------------------------------------|
| frames        | `outcoor: Atomic coordinates (Ang):` blocks |
| lattice       | `outcell: Unit cell vectors (Ang):` block  |
| total energy  | `siesta: E_KS(eV) = ...`                 |
| max force     | the unconstrained `   Max <value>` line after the per-atom force block |

The parser is tolerant of partially-written files: a frame whose
coordinate block is mid-write at EOF is dropped, and a step that has no
energy / force yet is shown with a gap in the corresponding plot.
