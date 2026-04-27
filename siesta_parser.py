"""Parser for SIESTA output files.

For each completed CG/MD step the parser extracts:

  * coordinates  -- from `outcoor: Atomic coordinates (Ang):` blocks
  * total energy -- from `siesta: E_KS(eV) = ...`           (eV)
  * per-atom forces -- from the `siesta: Atomic forces (eV/Ang):` block
                       (an [N_atoms x 3] array per frame)
  * max force    -- from the `   Max  <value>` line that appears
                    right after the per-atom force block; the duplicated
                    line ending with `constrained` is skipped.

It also captures the most recent unit-cell vectors from
`outcell: Unit cell vectors (Ang):` blocks so the viewer can draw the
lattice.

The parser is tolerant of files that are still being written:
* if the outcoor block is mid-write at EOF the partial frame is dropped;
* if a step has no energy / force yet, ``None`` is stored so all arrays
  stay index-aligned with frames (and serialise cleanly to JSON ``null``,
  which Plotly draws as a gap).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def parse_siesta_output(path: str) -> Dict[str, Any]:
    frames: List[List[List[Any]]] = []
    energies: List[Optional[float]] = []
    max_forces: List[Optional[float]] = []
    forces_per_frame: List[List[List[float]]] = []
    lattice: Optional[List[List[float]]] = None
    pending_lattice: Optional[List[List[float]]] = None

    # Per-step buffers; flushed via _commit() when the next outcoor: arrives
    # or at EOF (only if the coords block is known to be complete).
    step_frame: Optional[List[List[Any]]] = None
    step_energy: Optional[float] = None
    step_max_force: Optional[float] = None
    step_forces: List[List[float]] = []

    state = "scan"  # one of: "scan", "in_coords", "in_cell", "in_forces"

    def commit() -> None:
        nonlocal step_frame, step_energy, step_max_force, step_forces
        if not step_frame:
            step_frame = None
            step_energy = None
            step_max_force = None
            step_forces = []
            return
        frames.append(step_frame)
        energies.append(step_energy)        # may be None -> JSON null
        max_forces.append(step_max_force)   # may be None -> JSON null
        forces_per_frame.append(step_forces)  # [] if not yet written
        step_frame = None
        step_energy = None
        step_max_force = None
        step_forces = []

    with open(path, "r", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            stripped = line.strip()

            # --- inside a coordinate block --------------------------------
            if state == "in_coords":
                if not stripped:
                    state = "scan"
                    continue
                parts = stripped.split()
                if len(parts) < 6:
                    state = "scan"
                    continue
                try:
                    x = float(parts[0])
                    y = float(parts[1])
                    z = float(parts[2])
                except ValueError:
                    state = "scan"
                    continue
                step_frame.append([parts[-1], x, y, z])
                continue

            # --- inside the per-atom force block --------------------------
            if state == "in_forces":
                parts = stripped.split()
                if len(parts) >= 4:
                    try:
                        int(parts[0])  # atom index, just to check
                        fx = float(parts[1])
                        fy = float(parts[2])
                        fz = float(parts[3])
                    except ValueError:
                        state = "scan"
                        # fall through so the same line is re-evaluated
                    else:
                        step_forces.append([fx, fy, fz])
                        continue
                else:
                    state = "scan"
                # fall through to scan-mode handling

            # --- inside an outcell block ----------------------------------
            if state == "in_cell":
                parts = stripped.split()
                if len(parts) >= 3:
                    try:
                        row = [float(parts[0]), float(parts[1]), float(parts[2])]
                    except ValueError:
                        state = "scan"
                    else:
                        pending_lattice.append(row)
                        if len(pending_lattice) >= 3:
                            lattice = pending_lattice
                            pending_lattice = None
                            state = "scan"
                        continue
                else:
                    state = "scan"
                # fall through to scan-mode handling for the same line

            # --- scan mode -----------------------------------------------
            if stripped.startswith("outcoor:"):
                # An outcoor: block always sits between SCF steps, so the
                # previous step has by now produced its energy and forces.
                commit()
                step_frame = []
                state = "in_coords"
                continue

            if stripped.startswith("outcell: Unit cell vectors"):
                pending_lattice = []
                state = "in_cell"
                continue

            if "siesta: E_KS(eV)" in line:
                try:
                    step_energy = float(line.split("=", 1)[1].split()[0])
                except (ValueError, IndexError):
                    pass
                continue

            if "siesta: Atomic forces" in line:
                step_forces = []
                state = "in_forces"
                continue

            # Max force: the unconstrained value sits on a line of the form
            # "   Max    4.669483".  The duplicated line that ends with the
            # word "constrained" has 3 tokens, so we filter on token count.
            parts = stripped.split()
            if parts and parts[0] == "Max" and len(parts) == 2:
                try:
                    step_max_force = float(parts[1])
                except ValueError:
                    pass
                continue

    # End-of-file flush.  If we are still inside a coordinate block, the
    # frame is half-written; drop it so the viewer never sees a torn frame.
    if state == "in_coords":
        step_frame = None
    commit()

    return {
        "frames": frames,
        "lattice": lattice,
        "iterations": list(range(len(frames))),
        "energies": energies,
        "max_forces": max_forces,
        "forces": forces_per_frame,
    }
