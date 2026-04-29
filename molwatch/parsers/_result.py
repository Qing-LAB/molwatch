"""Internal helper for assembling the parser-output dict.

Every parser ends up doing the same three things at its return path:

  1. Pad / truncate per-step lists so they're index-aligned with
     ``frames`` (the schema invariant from ``parsers.base``).
  2. Sanitise floats: NaN / Inf go to None so the result is
     JSON-strict-safe.
  3. Build the ``ParsedTrajectory`` dict in the documented field
     order.

Before this helper, every parser implemented those three things
inline -- with subtle differences and the alignment-padding step
duplicated in two places.  ``assemble_trajectory`` is the single
place that knows how to take whatever a parser collected (in
whatever shape, possibly partial) and produce a contract-conformant
``ParsedTrajectory``.

The state machines in each parser are intentionally NOT abstracted.
SIESTA / molwatch.log / PySCF have meaningfully different parse
shapes; forcing a generic state-machine framework would create more
code than it removes.  This helper sits below the state machine,
not above it: each parser still scans its file in whatever way
makes sense, then hands the collected lists to ``assemble_trajectory``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Union

from .base import ParsedTrajectory, ScfCycleEntry


# --------------------------------------------------------------------- #
#  Float sanitisation                                                   #
# --------------------------------------------------------------------- #


def _finite_or_none(v: Any) -> Optional[float]:
    """Convert to a JSON-safe float, or None.

    NaN, Inf, and values that don't convert at all return None.
    Used at every per-step float that gets serialised so a stray
    NaN can't slip through to the front-end.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _sanitise_scf_entry(entry: Dict[str, Any]) -> ScfCycleEntry:
    """Float-or-None every value in an SCF cycle dict, except
    ``cycle`` which stays an integer counter.  Engine-specific keys
    (``gnorm`` / ``ddm`` / ``dHmax`` / ``dDmax``) pass through unchanged
    in name; only their values are sanitised.
    """
    out: ScfCycleEntry = {}
    for k, v in entry.items():
        if k == "cycle":
            try:
                out[k] = int(v) if v is not None else None
            except (TypeError, ValueError):
                out[k] = None
        else:
            out[k] = _finite_or_none(v)
    return out


# --------------------------------------------------------------------- #
#  Alignment-padding helpers                                            #
# --------------------------------------------------------------------- #


def _pad_to_n(seq: Optional[Sequence[Any]], n: int, fill: Any) -> List[Any]:
    """Return a length-N list: pad with ``fill`` if short, truncate if
    long.  ``None`` input is treated as an empty sequence."""
    seq = list(seq) if seq else []
    if len(seq) < n:
        seq.extend([fill] * (n - len(seq)))
    return seq[:n]


def _pad_to_n_factory(seq: Optional[Sequence[Any]], n: int,
                      factory) -> List[Any]:
    """Same as ``_pad_to_n`` but uses a fresh ``factory()`` instance
    for each pad slot (so list/dict pads don't share identity)."""
    seq = list(seq) if seq else []
    if len(seq) < n:
        seq.extend(factory() for _ in range(n - len(seq)))
    return seq[:n]


# --------------------------------------------------------------------- #
#  The single assembly entry point                                      #
# --------------------------------------------------------------------- #


def assemble_trajectory(
    *,
    source_format: str,
    frames: List[List[List[Any]]],
    energies: Optional[Sequence[Any]] = None,
    max_forces: Optional[Sequence[Any]] = None,
    forces: Optional[Sequence[Sequence[Sequence[float]]]] = None,
    scf_history: Optional[Sequence[Sequence[Dict[str, Any]]]] = None,
    iterations: Optional[Sequence[int]] = None,
    lattice: Optional[List[List[float]]] = None,
    created_at: Optional[str] = None,
    missing_companions: Optional[Sequence[str]] = None,
) -> ParsedTrajectory:
    """Assemble a contract-conformant :class:`ParsedTrajectory`.

    Each per-step argument may be ``None`` or shorter than ``frames``
    -- the helper pads with neutral defaults (``None`` for scalars,
    ``[]`` for list-valued, ``range(n)`` for ``iterations``).  Long
    inputs are truncated to ``len(frames)``.  All float values are
    sanitised through :func:`_finite_or_none` so NaN / Inf can't reach
    the JSON serialiser.

    The return value is the schema dict from ``parsers.base.ParsedTrajectory``.
    """
    n = len(frames)

    # Energies / max_forces: scalar-per-step, with float sanitisation.
    e_list = _pad_to_n(energies, n, fill=None)
    e_list = [_finite_or_none(x) for x in e_list]
    f_list = _pad_to_n(max_forces, n, fill=None)
    f_list = [_finite_or_none(x) for x in f_list]

    # Forces (per-atom-per-step) and SCF history: list-per-step,
    # default empty.  Use the factory variant so distinct slots
    # don't share identity.
    forces_list = _pad_to_n_factory(forces, n, factory=list)
    scf_list = _pad_to_n_factory(scf_history, n, factory=list)
    scf_list = [
        [_sanitise_scf_entry(c) for c in run]
        for run in scf_list
    ]

    # Iterations default to a 0..N-1 range when the parser didn't
    # track engine-native step indices.
    if iterations is None:
        iter_list: List[int] = list(range(n))
    else:
        iter_list = [int(x) for x in iterations]
        iter_list = _pad_to_n(iter_list, n, fill=0)

    return {
        "frames":             frames,
        "energies":           e_list,
        "max_forces":         f_list,
        "forces":             forces_list,
        "iterations":         iter_list,
        "lattice":            lattice,
        "scf_history":        scf_list,
        "source_format":      source_format,
        "created_at":         created_at,
        "missing_companions": list(missing_companions or []),
    }


__all__ = ["assemble_trajectory"]
