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

from .base import TrajectoryParser, ParsedTrajectory
from ._result import assemble_trajectory


# `>> Start of run:  28-APR-2026  20:01:39`  (SIESTA v5.x; v4.x uses
# the same shape).  Captures day, abbreviated month, year, h:m:s.
_START_RE = re.compile(
    r">>\s*Start of run:\s+(\d{1,2})-([A-Z][A-Za-z]{2})-(\d{4})\s+"
    r"(\d{1,2}):(\d{2}):(\d{2})"
)
_MONTH = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_siesta_start_line(line: str) -> Optional[str]:
    """Convert SIESTA's `>> Start of run: 28-APR-2026 20:01:39`
    into ISO 8601 (`2026-04-28T20:01:39`).  Returns None if the line
    doesn't match -- the caller treats `created_at` as optional."""
    m = _START_RE.search(line)
    if not m:
        return None
    day, mon_abbr, year, hh, mm, ss = m.groups()
    # SIESTA prints the month uppercase; canonicalise so the dict
    # lookup works regardless.
    mon = _MONTH.get(mon_abbr.title())
    if mon is None:
        return None
    return (f"{int(year):04d}-{mon:02d}-{int(day):02d}"
            f"T{int(hh):02d}:{int(mm):02d}:{int(ss):02d}")


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

    # `can_parse` is content-based, not banner-based.  SIESTA reshuffles
    # its header text across versions (v4.x had `Welcome to SIESTA`, v5
    # has `*  WELCOME TO SIESTA  *` plus a top-of-file `Executable:
    # siesta` line, future versions may reformat again), so we don't
    # rely on any specific banner string.  We accept the file if EITHER:
    #
    #   1. ANY one strong, content-bearing marker is present in the
    #      first 300 lines.  These are structural elements of SIESTA
    #      output -- block headers (`outcoor:`, `outcell:`), step
    #      banners (`Begin CG opt`), characteristic key lines
    #      (`siesta: System type`, `siesta: Atomic forces`).  They
    #      don't depend on banner text.
    #   2. We see at least 3 lines prefixed by `siesta:` or `redata:`
    #      in those 300 lines.  Real SIESTA output has dozens of such
    #      lines, so 3 is a near-certain match while still rejecting
    #      arbitrary log files that happen to contain the word
    #      "siesta:" once or twice.
    #
    # 300 lines is a generous scan window: a real SIESTA output has
    # plenty of structural markers within the first 100 lines on small
    # runs, and within ~700-800 on big v5 runs whose preamble grew.
    _STRONG_MARKERS = (
        "Executable      : siesta",      # v5.x line 1
        "WELCOME TO SIESTA",             # v5.x banner (uppercase)
        "Welcome to SIESTA",             # v4.x banner (mixed case)
        "siesta: System type",
        "siesta: Atomic forces",
        "outcoor: Atomic coordinates",
        "outcell: Unit cell vectors",
        "Begin CG opt",
        "Begin MD opt",
        "Begin Broyden opt",
        "Begin FIRE opt",
    )
    _PREFIX_MARKERS = ("siesta:", "redata:")
    _SCAN_LINES = 300
    _PREFIX_THRESHOLD = 3

    @classmethod
    def can_parse(cls, path: str) -> bool:
        try:
            with open(path, "r", errors="replace") as fh:
                head_lines = [next(fh, "") for _ in range(cls._SCAN_LINES)]
        except OSError:
            return False
        head = "".join(head_lines)
        # 1. Any strong content marker wins immediately.
        if any(m in head for m in cls._STRONG_MARKERS):
            return True
        # 2. Otherwise, count `siesta:` / `redata:` lines.
        prefix_hits = sum(
            1 for ln in head_lines
            if any(ln.lstrip().startswith(p) for p in cls._PREFIX_MARKERS)
        )
        return prefix_hits >= cls._PREFIX_THRESHOLD

    @classmethod
    def common_mistakes(cls, path: str) -> Optional[str]:
        """SIESTA's most common foot-gun: the user pointed at the
        ``.fdf`` (the input file molbuilder generated) instead of
        the ``.out`` SIESTA writes when run.  We can spot this from
        the file extension alone -- ``.fdf`` is unambiguous."""
        import os
        base = os.path.basename(path)
        if not base.lower().endswith(".fdf"):
            return None
        stem = base[:-len(".fdf")]
        return (
            f"That looks like a SIESTA INPUT file (.fdf), not the "
            f"output.  SIESTA writes its output to wherever you "
            f"redirected stdout, typically:\n"
            f"    mpirun siesta < {base} > {stem}.out\n"
            f"Load '{stem}.out' (or whatever you redirected to) "
            f"instead.  If you generated this run with molbuilder, "
            f"there's also a '{stem}.molwatch.log' alongside it that "
            f"shows the initial geometry immediately."
        )

    @classmethod
    def parse(cls, path: str) -> ParsedTrajectory:
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

        # Wall-clock start time, populated from `>> Start of run:` (v4
        # and v5 print this).  Stored as ISO 8601 so the front-end can
        # subtract it from the file's mtime to display elapsed wall
        # time without timezone fuss.
        created_at: Optional[str] = None

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
                # Wall-clock start time: appears once near the top of
                # the file in both v4 and v5.  Capture the first hit;
                # ignore any later occurrences from interrupted reruns.
                if created_at is None and ">> Start of run:" in line:
                    created_at = _parse_siesta_start_line(line)

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

        # Hand the collected lists to the schema-conformant assembler
        # (parsers/_result.py).  The helper enforces the schema
        # invariants: per-step alignment with frames, NaN/Inf -> None
        # sanitisation, the canonical field order.  SIESTA's main
        # `.out` is the single source of truth for the run, so no
        # sibling files are expected -- missing_companions is empty.
        return assemble_trajectory(
            source_format=cls.name,
            frames=frames,
            energies=energies,
            max_forces=max_forces,
            forces=forces_per_frame,
            scf_history=scf_history,
            lattice=lattice,
            created_at=created_at,
            missing_companions=[],
        )
