"""SIESTA .out / .log parser.

For each completed CG/MD step the parser extracts:

  * coordinates      -- from ``outcoor: Atomic coordinates (Ang):`` blocks
  * total energy     -- from ``siesta: E_KS(eV) = ...``  (eV)
  * per-atom forces  -- from ``siesta: Atomic forces (eV/Ang):`` blocks
  * max force        -- from the ``Max <value>`` line that appears after
                        the per-atom force block (skipping the duplicate
                        line ending with ``constrained``)

Also captures the most recent unit-cell vectors from
``outcell: Unit cell vectors (Ang):`` blocks so the viewer can draw the
lattice.

Tolerant to in-progress files:
  * if the outcoor block is mid-write at EOF the partial frame is dropped
  * if a step has no energy / force yet, ``None`` is stored so per-step
    arrays stay index-aligned with frames
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base import TrajectoryParser


# Matches a SIESTA SCF iteration line, e.g.:
#   scf:    1   -289239.010   -290967.214   -290967.445   0.001  -1.0   0.5
# Columns: iscf, Eharris(eV), E_KS(eV), FreeEng(eV), dDmax, Ef(eV), dHmax(eV).
# We pull cycle, E_KS, dDmax, dHmax; the other columns are SIESTA bookkeeping.
_SCF_LINE_RE = re.compile(
    r"^\s*scf:\s*(\d+)\s+"      # iscf
    r"(-?[\d.eE+-]+)\s+"        # Eharris
    r"(-?[\d.eE+-]+)\s+"        # E_KS  -- the energy we plot
    r"(-?[\d.eE+-]+)\s+"        # FreeEng
    r"([\d.eE+-]+)\s+"          # dDmax
    r"(-?[\d.eE+-]+)\s+"        # Ef
    r"([\d.eE+-]+)"             # dHmax
)


class SiestaParser(TrajectoryParser):
    name  = "siesta"
    label = "SIESTA .out / .log"
    hint  = "the main SIESTA run output (run.out, siesta.log, etc.)"

    # Format markers we look for in the file head to claim it.  These
    # strings appear in every SIESTA run somewhere in the first ~40 lines.
    _MARKERS = (
        "Welcome to SIESTA",
        "siesta: System type",
        "siesta:                                ! ",   # banner line
        "redata: ",
    )

    @classmethod
    def can_parse(cls, path: str) -> bool:
        try:
            with open(path, "r", errors="replace") as fh:
                head = "".join(next(fh, "") for _ in range(80))
        except OSError:
            return False
        return any(m in head for m in cls._MARKERS)

    @classmethod
    def parse(cls, path: str) -> Dict[str, Any]:
        frames: List[List[List[Any]]] = []
        energies: List[Optional[float]] = []
        max_forces: List[Optional[float]] = []
        forces_per_frame: List[List[List[float]]] = []
        lattice: Optional[List[List[float]]] = None
        pending_lattice: Optional[List[List[float]]] = None

        # SCF iteration history.  One inner list per CG/MD step's SCF
        # run; each entry a per-cycle dict matching the schema in
        # docs/spec/parsers.md.  SIESTA's column set differs from
        # PySCF (dHmax / dDmax instead of |g| / |ddm|); the UI picks
        # the right residual to plot based on which keys are present.
        scf_history: List[List[Dict[str, float]]] = []
        current_scf: List[Dict[str, float]] = []
        prev_E_KS: Optional[float] = None

        # Per-step buffers; flushed via _commit() when the next outcoor:
        # arrives or at EOF (only if the coords block is known to be
        # complete).
        step_frame: Optional[List[List[Any]]] = None
        step_energy: Optional[float] = None
        step_max_force: Optional[float] = None
        step_forces: List[List[float]] = []

        state = "scan"  # "scan", "in_coords", "in_cell", "in_forces"

        def commit() -> None:
            nonlocal step_frame, step_energy, step_max_force, step_forces
            if not step_frame:
                step_frame = None
                step_energy = None
                step_max_force = None
                step_forces = []
                return
            frames.append(step_frame)
            energies.append(step_energy)
            max_forces.append(step_max_force)
            forces_per_frame.append(step_forces)
            step_frame = None
            step_energy = None
            step_max_force = None
            step_forces = []

        with open(path, "r", errors="replace") as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                stripped = line.strip()

                if state == "in_coords":
                    if not stripped:
                        state = "scan"
                        continue
                    parts = stripped.split()
                    if len(parts) < 6:
                        state = "scan"
                        continue
                    try:
                        x = float(parts[0]); y = float(parts[1]); z = float(parts[2])
                    except ValueError:
                        state = "scan"
                        continue
                    step_frame.append([parts[-1], x, y, z])
                    continue

                if state == "in_forces":
                    parts = stripped.split()
                    if len(parts) >= 4:
                        try:
                            int(parts[0])  # atom index
                            fx = float(parts[1]); fy = float(parts[2]); fz = float(parts[3])
                        except ValueError:
                            state = "scan"
                        else:
                            step_forces.append([fx, fy, fz])
                            continue
                    else:
                        state = "scan"

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

                # ---- scan mode ----
                # SCF iteration line: collected into scf_history.  An
                # iscf = 1 starts a new SCF run (= new CG/MD step's
                # electronic problem).  Energy column is E_KS (already
                # eV), so no unit conversion needed -- contrast with
                # PySCF where Hartree -> eV happens at parse time.
                m_scf = _SCF_LINE_RE.match(line)
                if m_scf:
                    iscf  = int(m_scf.group(1))
                    e_ks  = float(m_scf.group(3))
                    dDmax = float(m_scf.group(5))
                    dHmax = float(m_scf.group(7))
                    if iscf == 1 and current_scf:
                        scf_history.append(current_scf)
                        current_scf = []
                        prev_E_KS = None
                    delta_E = (e_ks - prev_E_KS) if prev_E_KS is not None else 0.0
                    current_scf.append({
                        "cycle":   iscf,
                        "energy":  e_ks,        # eV
                        "delta_E": delta_E,     # eV
                        "dHmax":   dHmax,       # eV  (Hamiltonian residual)
                        "dDmax":   dDmax,       # dimensionless
                    })
                    prev_E_KS = e_ks
                    continue

                if stripped.startswith("outcoor:"):
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

                # Max force: the unconstrained value sits on a line of
                # the form "   Max    4.669483", emitted right after
                # the per-atom force block.  The duplicated line
                # ending with "constrained" has 3 tokens, so we filter
                # on token count.  Additionally we gate on a non-empty
                # `step_forces`, so a stray "Max <num>" line earlier
                # in the file (e.g. in a header / comment) can't be
                # mis-attributed to whatever step we're currently on.
                parts = stripped.split()
                if (parts and parts[0] == "Max" and len(parts) == 2
                        and step_forces):
                    try:
                        step_max_force = float(parts[1])
                    except ValueError:
                        pass
                    continue

        # End-of-file: drop torn frames, then flush.
        if state == "in_coords":
            step_frame = None
        commit()
        if current_scf:
            scf_history.append(current_scf)

        return {
            "frames":        frames,
            "lattice":       lattice,
            "iterations":    list(range(len(frames))),
            "energies":      energies,
            "max_forces":    max_forces,
            "forces":        forces_per_frame,
            "scf_history":   scf_history,
            "source_format": cls.name,
        }
