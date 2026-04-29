"""Trajectory-parser interface and result schema.

Every output format (SIESTA .out, PySCF / geomeTRIC .xyz, the unified
.molwatch.log, future NWChem / ORCA / Gaussian / CP2K / ...) implements
``TrajectoryParser``.  The Flask app discovers parsers via the registry
in ``parsers/__init__.py`` and never knows about specific file formats.

This module is the single source of truth for the **parser-output
schema**.  ``ParsedTrajectory`` defines every field a parser may
return; the spec doc (``docs/spec/parsers.md``) cross-references this
class.  Schema changes go through one place: this file, which is then
consumed by every parser, the Flask serializer, and the front-end.

JSON-friendliness rule: every field must round-trip through
``json.dumps(..., allow_nan=False)``.  ``None`` round-trips to
``null``; Plotly draws those as gaps in trace lines.  ``NaN`` and
``Inf`` are forbidden -- parsers are responsible for sanitising them
to ``None`` before returning.

Index-alignment rule: all per-step list fields (``frames``,
``energies``, ``max_forces``, ``forces``, ``iterations``,
``scf_history``) MUST have the same length.  The JS slider walks
them in lockstep via the frame index.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypedDict


# --------------------------------------------------------------------- #
#  Per-cycle SCF entry                                                  #
# --------------------------------------------------------------------- #
#
#  One SCF iteration's worth of progress data.  Required keys are
#  ``cycle``, ``energy``, ``delta_E``.  The rest are engine-dependent
#  -- see ``docs/spec/parsers.md`` for the table.  Treated as
#  ``Dict[str, Optional[float]]`` rather than a strict TypedDict because
#  different engines expose different residual norms (PySCF's
#  ``gnorm`` / ``ddm`` vs SIESTA's ``dHmax`` / ``dDmax``).
ScfCycleEntry = Dict[str, Optional[float]]


# --------------------------------------------------------------------- #
#  Parsed-trajectory schema (the contract)                              #
# --------------------------------------------------------------------- #


class ParsedTrajectory(TypedDict, total=False):
    """The dict shape every parser's ``parse()`` must return.

    Field-level conventions:

    * ``frames`` (REQUIRED)
        Per step: list of ``[element, x, y, z]`` rows.  Coordinates in
        Angstrom.  Length defines N for the per-step alignment rule.

    * ``energies`` (REQUIRED, length N)
        Total energy per step in **eV** (parsers convert from native
        units at parse time).  ``None`` when not yet available (e.g.,
        an initial-state preview block).

    * ``max_forces`` (REQUIRED, length N)
        Per-step max-atomic-force magnitude in **eV/Ang**.  ``None``
        when forces aren't in the source data yet.  Definition is the
        max of per-atom force-vector magnitudes:
        ``max_i sqrt(fx_i^2 + fy_i^2 + fz_i^2)``.  Spec violation to
        return ``max(|fx|, |fy|, |fz|)`` instead.

    * ``forces`` (REQUIRED, length N)
        Per atom per step in eV/Ang, shape ``[N][n_atoms][3]``.  Empty
        list ``[]`` for steps where per-atom forces aren't available
        (the trajectory may have only the max-force scalar).

    * ``iterations`` (REQUIRED, length N)
        Step indices as reported by the engine, length matches frames.
        Usually ``list(range(N))`` but parsers MAY use the engine's
        native step number when it differs (e.g., SIESTA's `Begin CG
        opt. move = K`).

    * ``lattice`` (REQUIRED)
        3x3 cell vectors in Angstrom, or ``None`` for non-periodic
        engines (geomeTRIC trajectories of isolated molecules).

    * ``scf_history`` (REQUIRED, length N)
        Per-step list of per-cycle dicts.  Each inner list is one
        geom-opt step's SCF run; each entry has at least keys
        ``cycle`` (int), ``energy`` (eV), ``delta_E`` (eV).  Engine-
        specific residuals (``gnorm`` / ``ddm`` / ``dHmax`` /
        ``dDmax``) appear in the entry when the parser exposes them.
        Empty list ``[]`` for steps where SCF history is absent.

    * ``source_format`` (REQUIRED)
        The parser's ``name`` attribute, OR a sub-engine identifier
        when the parser is engine-agnostic (the molwatch_log parser
        sets this from the file's ``# engine:`` header so the UI can
        adapt labels).

    * ``created_at`` (OPTIONAL)
        ISO 8601 string for the run's wall-clock start time, or
        ``None`` if not extractable from the file.  Used together
        with the file's mtime to display elapsed wall-clock time.
        Parsers MUST emit this key (set to ``None`` when unavailable)
        rather than omitting it.

    * ``missing_companions`` (OPTIONAL)
        List of file paths the parser EXPECTED to find as siblings
        but didn't (e.g., PySCF's ``<job>.qdata.txt`` for forces or
        ``<job>.log`` for SCF history).  Empty list when nothing is
        missing.  The UI can surface a hint to the user so silent
        degradation ("no max-force data") becomes a clear message.
    """

    frames:        List[List[List[Any]]]
    energies:      List[Optional[float]]
    max_forces:    List[Optional[float]]
    forces:        List[List[List[float]]]
    iterations:    List[int]
    lattice:       Optional[List[List[float]]]
    scf_history:   List[List[ScfCycleEntry]]
    source_format: str
    created_at:    Optional[str]
    missing_companions: List[str]


#: Required keys every parser must populate.  Optional keys
#: (``created_at``, ``missing_companions``) are listed separately so
#: a conformance test can check both groups distinctly.
REQUIRED_KEYS = frozenset({
    "frames", "energies", "max_forces", "forces",
    "iterations", "lattice", "scf_history", "source_format",
})
OPTIONAL_KEYS = frozenset({
    "created_at", "missing_companions",
})


# --------------------------------------------------------------------- #
#  Abstract parser                                                      #
# --------------------------------------------------------------------- #


class TrajectoryParser(ABC):
    """Subclass per output format.

    Subclasses set the three identification fields (``name``,
    ``label``, ``hint``) and implement ``can_parse`` and ``parse``.
    The registry uses ``can_parse`` for content-based detection and
    ``parse`` for the actual data extraction.
    """

    #: Short identifier echoed back to the front-end ("siesta", "pyscf").
    #: Used as the default ``source_format`` value in parser output.
    name: str = "abstract"

    #: Human-readable name shown in the UI ("SIESTA .out / .log",
    #: "PySCF / geomeTRIC trajectory").
    label: str = "abstract"

    #: One-line description of WHAT FILE the user should hand us.
    #: Surfaced in the "no registered parser" error so users who
    #: uploaded the wrong file get pointed at the right one.
    hint: str = ""

    @classmethod
    @abstractmethod
    def can_parse(cls, path: str) -> bool:
        """Cheap content-based check: does this parser handle this file?

        Implementations should peek at the first ~80–300 lines for
        structural markers (block headers, characteristic prefix
        counts) rather than version-specific banner strings.  Return
        False fast on a mismatch.  MUST NOT raise -- the registry
        treats an exception here as a buggy parser and silently
        skips it.
        """

    @classmethod
    @abstractmethod
    def parse(cls, path: str) -> ParsedTrajectory:
        """Parse the entire file.

        Re-callable: the Flask app calls this on every detected mtime
        change.  Tolerant of in-progress files: torn frames at EOF
        must be dropped, and per-step values that aren't yet in the
        file should be ``None`` (not raised).

        Returns a :class:`ParsedTrajectory` -- see that class's
        docstring for the field-level contract, including which keys
        are required and which optional.
        """
