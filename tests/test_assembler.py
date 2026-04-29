"""Unit tests for ``parsers/_result.py``.

The assembler is the single source of truth for: (a) per-step list
alignment with ``frames``, (b) NaN/Inf -> None sanitisation,
(c) the canonical schema field order.  Every parser routes through
it.  These tests exercise it directly so we don't have to spelunk
through a parser to verify a NaN-handling regression.

Architecture references:
  - architecture.md §4.2 (the assembler contract)
  - parsers/base.py docstring (schema invariants)
"""

from __future__ import annotations

import json
import math

import pytest

from parsers._result import (
    assemble_trajectory,
    _finite_or_none,
    _sanitise_scf_entry,
    _pad_to_n,
    _pad_to_n_factory,
)


# --------------------------------------------------------------------- #
#  _finite_or_none -- the float sanitiser used at every numeric field   #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize("inp,expected", [
    (None,             None),
    (1.0,              1.0),
    (0.0,              0.0),
    (-1e10,            -1e10),
    (float("nan"),     None),
    (float("inf"),     None),
    (-float("inf"),    None),
    ("1.5",            1.5),     # strings convertible to float
    ("not a number",   None),    # strings that aren't
    ([1, 2],           None),    # weird types fall through cleanly
])
def test_finite_or_none_sanitises_floats(inp, expected):
    """NaN/Inf are forbidden in the JSON-strict schema; the sanitiser
    must turn every non-finite or non-numeric value into None
    deterministically.  This is the one rule that prevents a stray
    NaN slipping through to the front-end's Plotly traces."""
    assert _finite_or_none(inp) == expected or (
        expected is None and _finite_or_none(inp) is None
    )


# --------------------------------------------------------------------- #
#  Padding helpers                                                      #
# --------------------------------------------------------------------- #


def test_pad_to_n_pads_short_input():
    """Sequences shorter than n are padded with the fill value."""
    assert _pad_to_n([1, 2], 5, fill=None) == [1, 2, None, None, None]


def test_pad_to_n_truncates_long_input():
    """Sequences longer than n are truncated."""
    assert _pad_to_n([1, 2, 3, 4, 5], 3, fill=None) == [1, 2, 3]


def test_pad_to_n_handles_none_input():
    """A None sequence is treated as empty -- pads to all-fill."""
    assert _pad_to_n(None, 3, fill=0) == [0, 0, 0]


def test_pad_to_n_factory_does_not_share_identity():
    """The factory variant produces fresh objects per pad slot --
    critical for list pads, where shared identity would make
    parsers' .append into one frame mutate every other frame."""
    out = _pad_to_n_factory(None, 3, factory=list)
    out[0].append("polluted")
    assert out[1] == []
    assert out[2] == []


# --------------------------------------------------------------------- #
#  SCF cycle entry sanitisation                                         #
# --------------------------------------------------------------------- #


def test_sanitise_scf_entry_preserves_cycle_as_int():
    """`cycle` is an integer iteration counter, not a float to
    sanitise.  Must round-trip as int (or None)."""
    out = _sanitise_scf_entry({"cycle": 3, "energy": -1.0, "gnorm": 0.05})
    assert out["cycle"] == 3
    assert isinstance(out["cycle"], int)


def test_sanitise_scf_entry_neutralises_nan_and_inf_residuals():
    """Engine-specific residual keys (gnorm/dHmax/etc.) all go through
    _finite_or_none; an SCF run where the residual went non-finite
    (rare but possible at the start of a hard SCF) must serialise."""
    out = _sanitise_scf_entry({
        "cycle":   1,
        "energy":  -50.0,
        "delta_E": 0.0,
        "gnorm":   float("nan"),
        "ddm":     float("inf"),
    })
    assert out["gnorm"] is None
    assert out["ddm"]   is None


def test_sanitise_scf_entry_passes_engine_specific_keys_through():
    """SIESTA's dHmax/dDmax must survive a sanitise pass.  The
    residual-key set is engine-dependent and the helper must not
    drop unrecognised keys."""
    out = _sanitise_scf_entry({
        "cycle":   2,
        "energy":  -100.0,
        "delta_E": -0.1,
        "dHmax":   1e-3,
        "dDmax":   2e-4,
    })
    assert set(out.keys()) == {"cycle", "energy", "delta_E", "dHmax", "dDmax"}


# --------------------------------------------------------------------- #
#  assemble_trajectory: end-to-end                                      #
# --------------------------------------------------------------------- #


def _h_atoms(n=2):
    """A trivial 2-atom frame for fixture building."""
    return [["H", 0.0, 0.0, 0.0], ["H", 0.74, 0.0, 0.0]]


def test_assemble_pads_short_per_step_lists():
    """A parser that built `frames` of length 5 but only managed to
    extract energies for the first 3 must still produce an aligned
    result (energies length = frames length, padded with None)."""
    result = assemble_trajectory(
        source_format="test",
        frames=[_h_atoms() for _ in range(5)],
        energies=[1.0, 2.0, 3.0],          # short
        max_forces=None,                    # absent
    )
    assert len(result["energies"])   == 5
    assert len(result["max_forces"]) == 5
    assert result["energies"][3] is None
    assert result["energies"][4] is None
    assert all(m is None for m in result["max_forces"])


def test_assemble_truncates_long_per_step_lists():
    """A parser that over-collected (e.g. duplicated steps in a buggy
    state machine) gets truncated to len(frames) -- the assembler is
    the place that enforces alignment."""
    result = assemble_trajectory(
        source_format="test",
        frames=[_h_atoms() for _ in range(2)],
        energies=[1.0, 2.0, 3.0, 4.0, 5.0],  # too long
    )
    assert result["energies"] == [1.0, 2.0]


def test_assemble_sanitises_nan_in_per_step_floats():
    """A parser that lets a NaN through from the source file (e.g.,
    a force-field crash that wrote NaN to the qdata) gets caught
    here before reaching the JSON serialiser."""
    result = assemble_trajectory(
        source_format="test",
        frames=[_h_atoms()],
        energies=[float("nan")],
        max_forces=[float("inf")],
    )
    assert result["energies"]   == [None]
    assert result["max_forces"] == [None]
    # And the result is JSON-strict-safe.
    json.dumps(result, allow_nan=False)


def test_assemble_pads_scf_history_with_empty_inner_lists():
    """Schema invariant: scf_history index-aligned with frames.
    Empty input -> [[], [], ...] (one empty inner list per frame),
    not [].  The fact that this needs to be a separate guarantee
    from per-step floats motivated the alignment-padding fix."""
    result = assemble_trajectory(
        source_format="test",
        frames=[_h_atoms() for _ in range(3)],
        scf_history=None,                    # parser had no SCF data
    )
    assert result["scf_history"] == [[], [], []]


def test_assemble_default_iterations_to_range():
    """When the parser doesn't track engine-native step indices,
    iterations defaults to range(N)."""
    result = assemble_trajectory(
        source_format="test",
        frames=[_h_atoms() for _ in range(4)],
    )
    assert result["iterations"] == [0, 1, 2, 3]


def test_assemble_zero_frames_yields_empty_arrays():
    """Edge: an empty file or one too torn to extract any frame.
    Result is still schema-conformant (every required key present,
    every per-step list empty, JSON-safe)."""
    result = assemble_trajectory(
        source_format="test",
        frames=[],
    )
    for key in ("energies", "max_forces", "forces", "iterations",
                "scf_history"):
        assert result[key] == [], f"{key!r} should be empty for 0 frames"
    json.dumps(result, allow_nan=False)


def test_assemble_single_frame():
    """Edge: a 1-frame trajectory (initial-state preview) must align
    correctly with all per-step lists having length 1."""
    result = assemble_trajectory(
        source_format="test",
        frames=[_h_atoms()],
        energies=[None],
        max_forces=[None],
    )
    for key in ("energies", "max_forces", "forces", "iterations",
                "scf_history"):
        assert len(result[key]) == 1, f"{key!r} not aligned for 1 frame"
    assert result["energies"] == [None]


def test_assemble_missing_companions_is_a_list():
    """`missing_companions` is always a list (possibly empty), not
    None.  Front-end code can iterate it without a None check."""
    r1 = assemble_trajectory(source_format="test", frames=[])
    r2 = assemble_trajectory(source_format="test", frames=[],
                             missing_companions=["a.qdata", "b.log"])
    assert r1["missing_companions"] == []
    assert r2["missing_companions"] == ["a.qdata", "b.log"]
    assert isinstance(r1["missing_companions"], list)


def test_assemble_field_order_matches_schema():
    """The dict's key order is part of the documented contract --
    front-end code that iterates dict items relies on a consistent
    order, and JSON output is more readable when stable."""
    result = assemble_trajectory(
        source_format="test",
        frames=[_h_atoms()],
    )
    expected_order = [
        "frames", "energies", "max_forces", "forces", "iterations",
        "lattice", "scf_history", "source_format",
        "created_at", "missing_companions",
    ]
    assert list(result.keys()) == expected_order


def test_assemble_strict_json_safe_with_realistic_payload():
    """End-to-end: a realistic 3-frame trajectory with one cycle of
    SCF data per frame round-trips through strict JSON without
    surprises.  Closes the loop on every NaN-sanitisation path."""
    result = assemble_trajectory(
        source_format="test",
        frames=[_h_atoms() for _ in range(3)],
        energies=[-1.0, -1.05, -1.07],
        max_forces=[0.5, 0.2, 0.05],
        scf_history=[
            [{"cycle": 1, "energy": -1.0, "delta_E": 0.0,
              "gnorm": 0.5, "ddm": 0.1}],
            [{"cycle": 1, "energy": -1.05, "delta_E": -0.05,
              "gnorm": float("nan"), "ddm": 0.05}],     # NaN sanitised
            [{"cycle": 1, "energy": -1.07, "delta_E": -0.02,
              "gnorm": 0.001, "ddm": 0.001}],
        ],
        created_at="2026-04-28T12:00:00",
    )
    text = json.dumps(result, allow_nan=False)
    # Round-trip back; check NaN became null.
    rt = json.loads(text)
    assert rt["scf_history"][1][0]["gnorm"] is None
    assert rt["created_at"] == "2026-04-28T12:00:00"
