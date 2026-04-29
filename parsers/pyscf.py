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
from typing import Any, Dict, List, Optional, Tuple

from .base import TrajectoryParser, ParsedTrajectory


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
    label = "XYZ trajectory (PySCF / geomeTRIC / generic multi-frame XYZ)"
    hint  = ("a multi-frame XYZ trajectory -- e.g., geomeTRIC's "
             "<job>_geom_optim.xyz (NOT the PySCF .log).  Generic XYZ "
             "with any comment-line format is also accepted; energies "
             "are extracted only when the comment matches the geomeTRIC "
             "`Iteration K Energy E` pattern.")

    # Maximum atom count we'll accept in line 0 before declaring the
    # file isn't an XYZ.  Far above any realistic chemistry use case
    # but bounded to reject obvious garbage (e.g., a CSV row count of
    # millions parsed as atom count).
    _MAX_PLAUSIBLE_ATOMS = 1_000_000

    @classmethod
    def can_parse(cls, path: str) -> bool:
        """Structural XYZ check, not banner-matching.

        An XYZ file -- regardless of which tool produced it -- has the
        invariant:

            line 0:  positive integer N      (atom count)
            line 1:  arbitrary comment       (free text)
            lines 2..N+1:  atom lines        (element symbol + 3 floats)

        We verify exactly that, with no requirement on the comment line's
        content.  geomeTRIC's `Iteration K Energy E` is one possible
        comment format; ASE writes a different one; user scripts may
        write any string.  ``parse()`` extracts an energy when the
        comment matches the geomeTRIC pattern and falls back to
        ``energy=None`` otherwise -- so accepting any well-formed XYZ
        here is safe.

        We sample at most the first 3 atom lines (or all of them if N<3)
        for the structural check -- enough to reject CSVs / namelists /
        other text files that happen to start with an integer, while
        keeping the detector cheap.
        """
        try:
            with open(path, "r", errors="replace") as fh:
                line0 = fh.readline().strip()
                if not line0.isdigit():
                    return False
                n_atoms = int(line0)
                if n_atoms <= 0 or n_atoms > cls._MAX_PLAUSIBLE_ATOMS:
                    return False
                fh.readline()  # comment line; any content accepted
                # Verify the atom lines parse: element token + 3 floats.
                # Sample at most 3 (or fewer if n_atoms < 3).
                for _ in range(min(n_atoms, 3)):
                    parts = fh.readline().split()
                    if len(parts) < 4:
                        return False
                    try:
                        float(parts[1])
                        float(parts[2])
                        float(parts[3])
                    except ValueError:
                        return False
                return True
        except OSError:
            return False

    @classmethod
    def parse(cls, path: str) -> ParsedTrajectory:
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

        # Sibling-file discovery.  Each helper returns its data plus
        # the canonical path it expected to find -- so we can build
        # missing_companions for the front-end.  The user can then
        # tell "force data missing because qdata isn't there" apart
        # from "force data missing because the run hasn't computed
        # forces yet."
        max_forces, qdata_missing = cls._read_qdata_forces(path, len(frames))
        scf_history, log_missing  = cls._read_scf_history(path)

        # Schema invariant: scf_history is index-aligned with frames.
        # The PySCF .log records SCF runs in lockstep with opt steps,
        # so on a complete run len(scf_history) == len(frames); but on
        # a partial / log-less run we pad / truncate to keep the
        # alignment contract.  See docs/spec/parsers.md.
        while len(scf_history) < len(frames):
            scf_history.append([])
        scf_history = scf_history[: len(frames)]

        missing_companions: List[str] = []
        if qdata_missing is not None:
            missing_companions.append(qdata_missing)
        if log_missing is not None:
            missing_companions.append(log_missing)

        return {
            "frames":        frames,
            "lattice":       None,            # geomeTRIC traj has no cell
            "iterations":    iterations or list(range(len(frames))),
            "energies":      energies,
            "max_forces":    max_forces,
            "forces":        [[] for _ in frames],
            "scf_history":   scf_history,
            "source_format": cls.name,
            # geomeTRIC's `_optim.xyz` carries no start timestamp.
            # PySCF's `.log` does, but parsing it for time alone isn't
            # worth the cost; the UI falls back to file mtime.
            "created_at":    None,
            "missing_companions": missing_companions,
        }

    # ------------------------------------------------------------- #
    #  qdata-companion helper                                        #
    # ------------------------------------------------------------- #
    @classmethod
    def _read_qdata_forces(
        cls, traj_path: str, n_frames: int
    ) -> Tuple[List[Optional[float]], Optional[str]]:
        """Try `<prefix>.qdata{,.txt}` next to the trajectory.

        Returns ``(forces_per_step, missing_path)``:

        * ``forces_per_step`` is a list of max-force values (eV/Ang)
          of length ``n_frames``; each entry is ``None`` if the file
          isn't there or the entry is missing for that step.
        * ``missing_path`` is the canonical expected path (``.qdata.txt``
          variant) when no qdata file was found, or ``None`` when one
          was found and read.  The caller surfaces this as part of
          ``missing_companions`` so the UI can explain why max-force
          data is unavailable.
        """
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
            return [None] * n_frames, candidates[0]

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
            return [None] * n_frames, candidates[0]

        # Pad / truncate to align with frames.
        if len(max_forces) < n_frames:
            max_forces.extend([None] * (n_frames - len(max_forces)))
        return max_forces[:n_frames], None

    # ------------------------------------------------------------- #
    #  PySCF-log SCF-iteration helper                                #
    # ------------------------------------------------------------- #
    @classmethod
    def _read_scf_history(
        cls, traj_path: str
    ) -> Tuple[List[List[Dict[str, float]]], Optional[str]]:
        """Try ``<prefix>.log`` next to the trajectory.

        Returns ``(scf_history, missing_path)``:

        * ``scf_history``: list of SCF runs, where each run is a list
          of per-cycle dicts (see schema below).  Empty list when no
          log file was found or it had no SCF tables.
        * ``missing_path``: canonical expected log path when no log
          was found, or ``None`` when one was found and read.

        Per-cycle dict schema:

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
            return [], log_path

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
            return [], log_path
        return runs, None
