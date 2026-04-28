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


class PySCFParser(TrajectoryParser):
    name  = "pyscf"
    label = "PySCF / geomeTRIC trajectory"

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

        return {
            "frames":        frames,
            "lattice":       None,            # geomeTRIC traj has no cell
            "iterations":    iterations or list(range(len(frames))),
            "energies":      energies,
            "max_forces":    max_forces,
            "forces":        [[] for _ in frames],
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
