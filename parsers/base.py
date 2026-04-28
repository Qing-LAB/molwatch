"""Trajectory-parser interface.

Every output format (SIESTA .out, PySCF / geomeTRIC .xyz, future
NWChem / ORCA / Gaussian / OpenMM / ...) implements ``TrajectoryParser``.
The Flask app in ``molwatch.app`` discovers parsers via the registry
in ``parsers/__init__.py`` and never knows about specific file formats.

Return shape from ``parse()``: a JSON-friendly dict that the front-end
already understands (back-compat with the old siesta_viewer API):

    {
        "frames":      [ [[el, x, y, z], ...], ... ],   # per step, Ang
        "energies":    [ float | null, ... ],           # eV per step
        "max_forces":  [ float | null, ... ],           # eV/Ang per step
        "forces":      [ [[fx, fy, fz], ...] | [], ... ],   # eV/Ang per atom per step
        "iterations":  [ int, ... ],
        "lattice":     [[ax, ay, az], ...] | null,      # 3x3 Ang or null
        "source_format": "siesta" | "pyscf" | ...,
    }

Use ``None`` for unknown values; they round-trip to JSON ``null`` and
Plotly draws those as gaps in the trace.  Keep arrays index-aligned
across all per-step lists -- the JS slider walks them in lockstep.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class TrajectoryParser(ABC):
    """Subclass per output format."""

    #: Short identifier echoed back to the front-end ("siesta", "pyscf").
    name: str = "abstract"

    #: Human-readable name shown in the UI ("SIESTA .out", "PySCF / geomeTRIC").
    label: str = "abstract"

    @classmethod
    @abstractmethod
    def can_parse(cls, path: str) -> bool:
        """Cheap check: does this parser handle this file?

        Implementations should peek at the first ~50 lines for format
        markers and return False fast on a mismatch.  Avoid raising;
        an unsupported file should yield False, not crash the registry.
        """

    @classmethod
    @abstractmethod
    def parse(cls, path: str) -> Dict[str, Any]:
        """Parse the entire file.  Re-callable; the Flask app calls this
        on every mtime change."""
