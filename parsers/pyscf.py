"""PySCF / geomeTRIC trajectory parser.

When molbuilder generates a PySCF script with `prefix=JOB+'_geom'` on
the optimize() call (the default), geomeTRIC streams a multi-frame XYZ
to ``<JOB>_geom_optim.xyz`` -- one frame per accepted geometry step.

Frame format:

    {N}
    Iteration {K} Energy {E:.8f}
    {El}  {x:14.8f}  {y:14.8f}  {z:14.8f}
    ...

Energy is in Hartree.  We convert to eV (matches what the SIESTA parser
emits) so the energy plot is unit-consistent across formats.

geomeTRIC's `_optim.xyz` doesn't include force info per frame; if the
companion ``<prefix>.qdata`` file is present alongside, we additionally
pull the maximum force per step from there (Hartree/Bohr -> eV/Ang).
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Dict, List, Optional

from .base import TrajectoryParser


# Hartree -> eV
_HARTREE_TO_EV = 27.211386245988
# Hartree/Bohr -> eV/Ang
_HA_BOHR_TO_EV_ANG = _HARTREE_TO_EV / 0.5291772108


_COMMENT_RE = re.compile(
    r"Iteration\s+(\d+)\s+Energy\s+(-?[\d.eE+-]+)",
    re.IGNORECASE,
)


# Matches a PySCF SCF iteration line, e.g.:
#   cycle= 1 E= -5005.99362145001  delta_E= 33.1  |g|= 13.4  |ddm|= 23.1
_SCF_LINE_RE = re.compile(
    r"cycle\s*=\s*(\d+)\s+"
    r"E\s*=\s*(-?[\d.eE+-]+)\s+"
    r"delta_E\s*=\s*(-?[\d.eE+-]+)\s+"
    r"\|g\|\s*=\s*([\d.eE+-]+)\s+"
    r"\|ddm\|\s*=\s*([\d.eE+-]+)"
)

# "converged SCF energy = ..." marks the end of one SCF run (one
# geom-opt step's electronic problem).  Used to delimit run boundaries
# robustly even when cycle= 0 also appears in mid-run extra-cycle blocks.
_SCF_CONVERGED_RE = re.compile(
    r"converged SCF energy\s*=\s*(-?[\d.eE+-]+)"
)


class PySCFParser(TrajectoryParser):
    name  = "pyscf"
    label = "PySCF / geomeTRIC trajectory"
    hint  = ("geomeTRIC's streaming trajectory <job>_geom_optim.xyz "
             "(NOT the PySCF .log)")

    @classmethod
    def can_parse(cls, path: str) -> bool:
        """A geomeTRIC `_optim.xyz` looks like multi-frame XYZ whose
        comment lines match `Iteration K Energy E`.  Peek at line 2."""
        try:
            with open(path, "r", errors="replace") as fh:
                lines = [next(fh, "") for _ in range(4)]
        except OSError:
            return False
        # Line 0: atom count.  Line 1: comment.
        if not lines[0].strip().isdigit():
            return False
        if not _COMMENT_RE.search(lines[1]):
            return False
        return True

    @classmethod
    def parse(cls, path: str) -> Dict[str, Any]:
        frames: List[List[List[Any]]] = []
        energies: List[Optional[float]] = []
        iterations: List[int] = []

        with open(path, "r", errors="replace") as fh:
            while True:
                header = fh.readline()
                if not header:
                    break                     # clean EOF
                header = header.strip()
                if not header.isdigit():
                    # Probably a torn write at EOF; bail out cleanly.
                    break
                n_atoms = int(header)
                comment = fh.readline()
                if not comment:
                    break                     # torn frame
                m = _COMMENT_RE.search(comment)
                if m:
                    step_idx = int(m.group(1))
                    energy_ha = float(m.group(2))
                    energy_eV: Optional[float] = energy_ha * _HARTREE_TO_EV
                else:
                    # Comment line we don't recognise; record the frame
                    # but with unknown step / energy.
                    step_idx = len(frames)
                    energy_eV = None

                atoms: List[List[Any]] = []
                torn = False
                for _ in range(n_atoms):
                    line = fh.readline()
                    if not line:
                        torn = True
                        break
                    parts = line.split()
                    if len(parts) < 4:
                        torn = True
                        break
                    try:
                        atoms.append([
                            parts[0],
                            float(parts[1]),
                            float(parts[2]),
                            float(parts[3]),
                        ])
                    except ValueError:
                        torn = True
                        break
                if torn or len(atoms) != n_atoms:
                    # Last frame is mid-write; drop it so the JS slider
                    # never sees a torn frame.
                    break
                frames.append(atoms)
                energies.append(energy_eV)
                iterations.append(step_idx)

        # Optional: pull max-force per step from the companion .qdata.
        # geomeTRIC writes `<prefix>.qdata`, where this file is named
        # `<prefix>_optim.xyz`.  Same prefix, so derive it:
        max_forces = cls._read_qdata_forces(path, len(frames))

        # PySCF's main .log has the SCF iteration tables (one block per
        # geom-opt step's electronic problem).  Surface this as
        # scf_history so molwatch can show progress within the current
        # geom-opt step.
        scf_history = cls._read_scf_history(path)

        return {
            "frames":        frames,
            "lattice":       None,            # geomeTRIC traj has no cell
            "iterations":    iterations or list(range(len(frames))),
            "energies":      energies,
            "max_forces":    max_forces,
            "forces":        [[] for _ in frames],
            "scf_history":   scf_history,
            "source_format": cls.name,
        }

    # ------------------------------------------------------------- #
    #  qdata-companion helper                                        #
    # ------------------------------------------------------------- #
    @classmethod
    def _read_qdata_forces(cls, traj_path: str,
                           n_frames: int) -> List[Optional[float]]:
        """Try `<prefix>.qdata` next to the trajectory.  Returns a
        list of max-force values (eV/Ang) of length ``n_frames``;
        each entry is ``None`` if the file isn't there or the entry
        is missing for that step."""
        base, fname = os.path.split(traj_path)
        # geomeTRIC's pair: <prefix>_optim.xyz <-> <prefix>.qdata.txt
        # But qdata extension varies across versions; try a couple.
        stem = fname
        if stem.endswith("_optim.xyz"):
            stem = stem[: -len("_optim.xyz")]
        candidates = [
            os.path.join(base, f"{stem}.qdata.txt"),
            os.path.join(base, f"{stem}.qdata"),
        ]
        qpath = next((p for p in candidates if os.path.isfile(p)), None)
        if qpath is None:
            return [None] * n_frames

        # Per-step: ENERGY starts a frame, GRADIENT (for THAT frame)
        # follows.  We flush the current frame's max only on the NEXT
        # ENERGY line (or at EOF), so the gradient is bound to the
        # right frame.
        max_forces: List[Optional[float]] = []
        try:
            with open(qpath, "r", errors="replace") as fh:
                step_max: Optional[float] = None
                in_frame = False
                for raw in fh:
                    s = raw.strip()
                    if s.startswith("ENERGY"):
                        if in_frame:
                            max_forces.append(step_max)   # close previous
                        in_frame = True
                        step_max = None
                    elif s.startswith("GRADIENT"):
                        # GRADIENT line lists 3N gradient components in
                        # Hartree/Bohr.  Convention compatibility: SIESTA's
                        # "Max" is the largest per-atom force MAGNITUDE
                        # (sqrt(fx^2+fy^2+fz^2)), not the largest scalar
                        # component.  Match it here so the energy/force
                        # plots overlay sensibly across formats.
                        try:
                            comps = [float(x) for x in s.split()[1:]]
                        except ValueError:
                            continue
                        if len(comps) >= 3:
                            per_atom = (
                                math.sqrt(comps[i]**2 + comps[i+1]**2 + comps[i+2]**2)
                                for i in range(0, len(comps) - 2, 3)
                            )
                            step_max = max(per_atom) * _HA_BOHR_TO_EV_ANG
                if in_frame:
                    max_forces.append(step_max)
        except OSError:
            return [None] * n_frames

        # Pad / truncate to align with frames.
        if len(max_forces) < n_frames:
            max_forces.extend([None] * (n_frames - len(max_forces)))
        return max_forces[:n_frames]

    # ------------------------------------------------------------- #
    #  PySCF-log SCF-iteration helper                                #
    # ------------------------------------------------------------- #
    @classmethod
    def _read_scf_history(cls, traj_path: str) -> List[List[Dict[str, float]]]:
        """Try ``<prefix>.log`` next to the trajectory.

        Returns a list of SCF runs, where each run is a list of
        per-cycle dicts:

            [
              [   # geom-opt step 0
                {"cycle": 0, "energy": <eV>, "delta_E": <eV>,
                 "gnorm":  <eV/Ang>, "ddm": <dimensionless>},
                ...
              ],
              [...],   # step 1
              ...
            ]

        molwatch uses the LAST entry as "the current step's SCF" --
        it's the one most useful for live monitoring.

        Returns an empty list if the .log file isn't present (or
        can't be opened); the front-end then hides the SCF-progress
        panel.

        Filename derivation:
          * traj is `<base>_geom_optim.xyz` (molbuilder convention)
          * pyscf log is `<base>.log` (no `_geom`)
        We strip the suffix(es) accordingly.
        """
        base, fname = os.path.split(traj_path)
        stem = fname
        if stem.endswith("_optim.xyz"):
            stem = stem[: -len("_optim.xyz")]
        if stem.endswith("_geom"):
            stem = stem[: -len("_geom")]
        log_path = os.path.join(base, stem + ".log")
        if not os.path.isfile(log_path):
            return []

        runs: List[List[Dict[str, float]]] = []
        current: List[Dict[str, float]] = []

        try:
            with open(log_path, "r", errors="replace") as fh:
                for raw in fh:
                    m = _SCF_LINE_RE.search(raw)
                    if m:
                        cycle, e, de, g, ddm = m.groups()
                        cy = int(cycle)
                        # New SCF run boundary: cycle resets to a small
                        # number after a converged-or-extra-cycle line.
                        # We use _SCF_CONVERGED_RE to mark boundaries
                        # explicitly below; here we just build the list.
                        if cy == 0 and current:
                            runs.append(current)
                            current = []
                        try:
                            current.append({
                                "cycle":   cy,
                                "energy":  float(e)  * _HARTREE_TO_EV,
                                "delta_E": float(de) * _HARTREE_TO_EV,
                                "gnorm":   float(g)  * _HA_BOHR_TO_EV_ANG,
                                "ddm":     float(ddm),
                            })
                        except ValueError:
                            continue
                    elif _SCF_CONVERGED_RE.search(raw):
                        # End of an SCF run.  Flush only if we
                        # collected anything; consecutive converged
                        # lines without intervening cycles can happen
                        # in pathological logs.
                        if current:
                            runs.append(current)
                            current = []
                if current:
                    runs.append(current)
        except OSError:
            return []
        return runs
