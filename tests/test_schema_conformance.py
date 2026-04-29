"""Cross-parser schema-conformance suite.

Every parser registered in ``PARSERS`` must produce a result dict
that conforms to :class:`parsers.base.ParsedTrajectory` -- both the
required-key set and the index-alignment / type / unit invariants
documented in ``docs/spec/parsers.md``.

These tests are the single place where the schema contract is
enforced.  When a new parser is added, it must pass this suite
without per-parser exceptions, which is what keeps the contract
non-rotten.

Each parser's test fixture builds a minimal valid input the parser
will claim, then runs the result through the conformance checks.
"""

from __future__ import annotations

import json
import math

import pytest

from molwatch.parsers import PARSERS
from molwatch.parsers.base import (
    ParsedTrajectory,
    REQUIRED_KEYS,
    OPTIONAL_KEYS,
)


# --------------------------------------------------------------------- #
#  Per-parser minimal fixtures                                          #
# --------------------------------------------------------------------- #
#
#  Each fixture writes a tiny but valid file the parser will accept.
#  The parametrized tests below run conformance checks against each
#  parser's parsed output.


def _siesta_min(tmp_path):
    p = tmp_path / "siesta.out"
    p.write_text(
        "Welcome to SIESTA -- v4.1\n"
        "redata: prelude\n"
        ">> Start of run:  28-APR-2026  20:01:39\n"
        "outcoor: Atomic coordinates (Ang):\n"
        "   1.00000000    2.00000000    3.00000000   1       1  C\n"
        "\n"
        "siesta: E_KS(eV) =          -50.0000\n"
        "siesta: Atomic forces (eV/Ang):\n"
        "     1    0.10    0.20    0.30\n"
        "----------------------------------------\n"
        "   Max    1.000000\n"
    )
    return str(p)


def _pyscf_min(tmp_path):
    p = tmp_path / "h2_geom_optim.xyz"
    p.write_text(
        "2\n"
        "Iteration 0 Energy -1.1000000\n"
        "H 0.0 0.0 0.0\n"
        "H 0.74 0.0 0.0\n"
    )
    return str(p)


def _molwatch_log_min(tmp_path):
    p = tmp_path / "x.molwatch.log"
    p.write_text(
        "# molwatch trajectory log v1\n"
        "# generator: test\n"
        "# engine: siesta\n"
        "# created: 2026-04-28T20:00:00\n"
        "\n"
        "==== molwatch step 0 begin ====\n"
        "step_index: 0\n"
        "n_atoms: 1\n"
        "coordinates (Ang):\n"
        "   H      0.00000000      0.00000000      0.00000000\n"
        "energy (eV): -1.0\n"
        "forces (eV/Ang):\n"
        "max_force (eV/Ang): None\n"
        "scf_history begin\n"
        "scf_history end\n"
        "==== molwatch step 0 end ====\n"
    )
    return str(p)


_FIXTURE_BUILDERS = {
    "siesta":   _siesta_min,
    "pyscf":    _pyscf_min,
    "molwatch": _molwatch_log_min,
}


def _build_fixture_for(parser_cls, tmp_path):
    builder = _FIXTURE_BUILDERS.get(parser_cls.name)
    if builder is None:
        pytest.fail(
            f"No conformance fixture builder for parser {parser_cls.name!r}; "
            f"add one to test_schema_conformance.py when you register a new "
            f"parser."
        )
    return builder(tmp_path)


# --------------------------------------------------------------------- #
#  Conformance assertions                                               #
# --------------------------------------------------------------------- #


def _assert_required_keys(result, parser_name):
    """Every required key in ParsedTrajectory must be present.

    The schema contract: all REQUIRED_KEYS appear in every parser's
    result.  A missing key is a contract violation, even if the value
    would be empty.
    """
    missing = REQUIRED_KEYS - set(result.keys())
    assert not missing, (
        f"{parser_name!r} parser missing required keys: {sorted(missing)}"
    )


def _assert_no_unknown_keys(result, parser_name):
    """Result keys are confined to the documented schema (required +
    optional).  Drift is a quiet way contracts get violated; this
    catches it loudly."""
    allowed = REQUIRED_KEYS | OPTIONAL_KEYS
    extra = set(result.keys()) - allowed
    assert not extra, (
        f"{parser_name!r} parser returned unknown keys: {sorted(extra)}.  "
        f"Either add them to ParsedTrajectory in parsers/base.py or "
        f"remove them from the parser."
    )


def _assert_index_aligned(result, parser_name):
    """All per-step lists must have the same length as `frames`."""
    n = len(result["frames"])
    aligned_keys = (
        "energies", "max_forces", "forces", "iterations", "scf_history",
    )
    for key in aligned_keys:
        actual = len(result[key])
        assert actual == n, (
            f"{parser_name!r}: result[{key!r}] has length {actual} "
            f"but frames has length {n}.  Per-step lists must be index-"
            f"aligned with frames."
        )


def _assert_json_strict(result, parser_name):
    """Result must round-trip via strict JSON -- no NaN, no Inf."""
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as exc:
        pytest.fail(
            f"{parser_name!r} parser result is not JSON-strict-safe: {exc}.  "
            f"Sanitize NaN/Inf to None before returning."
        )


def _assert_value_types(result, parser_name):
    """Spot-check critical field types so a parser swap doesn't silently
    drift (e.g. returning numpy floats that don't JSON-serialise)."""
    # source_format is always a non-empty string.
    assert isinstance(result["source_format"], str) and result["source_format"], (
        f"{parser_name!r}: source_format must be a non-empty string, "
        f"got {type(result['source_format']).__name__}"
    )
    # missing_companions is a list (possibly empty) of strings.
    mc = result["missing_companions"]
    assert isinstance(mc, list), (
        f"{parser_name!r}: missing_companions must be a list, "
        f"got {type(mc).__name__}"
    )
    for p in mc:
        assert isinstance(p, str), (
            f"{parser_name!r}: missing_companions entries must be strings, "
            f"got {type(p).__name__}"
        )
    # created_at is None or an ISO-8601-ish string.
    ca = result.get("created_at")
    if ca is not None:
        assert isinstance(ca, str), (
            f"{parser_name!r}: created_at must be a string or None, "
            f"got {type(ca).__name__}"
        )
    # iterations is a list of ints.
    for i, v in enumerate(result["iterations"]):
        assert isinstance(v, int), (
            f"{parser_name!r}: iterations[{i}] must be int, "
            f"got {type(v).__name__}"
        )
    # energies / max_forces: float-or-None per step.
    for key in ("energies", "max_forces"):
        for i, v in enumerate(result[key]):
            if v is not None:
                assert isinstance(v, float) and math.isfinite(v), (
                    f"{parser_name!r}: {key}[{i}] must be a finite float "
                    f"or None, got {v!r}"
                )


# --------------------------------------------------------------------- #
#  Parametrized over every registered parser                            #
# --------------------------------------------------------------------- #


@pytest.fixture(params=PARSERS, ids=lambda c: c.name)
def parser_and_input(request, tmp_path):
    """Yields (parser_cls, path-to-minimal-valid-input) for each parser."""
    parser_cls = request.param
    path = _build_fixture_for(parser_cls, tmp_path)
    return parser_cls, path


def test_parser_can_parse_its_own_fixture(parser_and_input):
    """Every fixture must be claimed by its target parser.  If this
    fails, either the parser's can_parse is broken or the fixture
    isn't representative."""
    parser_cls, path = parser_and_input
    assert parser_cls.can_parse(path), (
        f"{parser_cls.name!r} fixture wasn't claimed by its own parser."
    )


def test_parse_returns_required_keys(parser_and_input):
    parser_cls, path = parser_and_input
    result = parser_cls.parse(path)
    _assert_required_keys(result, parser_cls.name)


def test_parse_emits_no_unknown_keys(parser_and_input):
    parser_cls, path = parser_and_input
    result = parser_cls.parse(path)
    _assert_no_unknown_keys(result, parser_cls.name)


def test_parse_per_step_lists_aligned(parser_and_input):
    parser_cls, path = parser_and_input
    result = parser_cls.parse(path)
    _assert_index_aligned(result, parser_cls.name)


def test_parse_strict_json_safe(parser_and_input):
    parser_cls, path = parser_and_input
    result = parser_cls.parse(path)
    _assert_json_strict(result, parser_cls.name)


def test_parse_value_types(parser_and_input):
    parser_cls, path = parser_and_input
    result = parser_cls.parse(path)
    _assert_value_types(result, parser_cls.name)


def test_parse_emits_missing_companions_key(parser_and_input):
    """Every parser MUST emit `missing_companions` (the OPTIONAL set
    is "must be present, may be empty/None", not "may be absent").
    Architecture §2.3: 'never silently degrade.'  Front-end iterates
    this list without a None check; absence here is a contract bug."""
    parser_cls, path = parser_and_input
    result = parser_cls.parse(path)
    assert "missing_companions" in result, (
        f"{parser_cls.name!r}: missing_companions key absent"
    )


def test_parse_emits_created_at_key(parser_and_input):
    """Every parser MUST emit `created_at` (set to None when no
    timestamp can be extracted).  Frontend uses presence of this key
    to decide whether to show 'started ... running for ...' in the
    status banner."""
    parser_cls, path = parser_and_input
    result = parser_cls.parse(path)
    assert "created_at" in result, (
        f"{parser_cls.name!r}: created_at key absent"
    )


def test_parse_flask_jsonify_round_trip(parser_and_input):
    """End-to-end JSON serialisation must succeed via Flask's jsonify
    (which is what /api/load and /api/data actually use).  Catches
    the case where a parser's result is `json.dumps`-safe but Flask's
    encoder rejects it for some other reason (custom types, etc.)."""
    from flask import Flask, jsonify
    parser_cls, path = parser_and_input
    result = parser_cls.parse(path)
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    with app.app_context():
        response = jsonify(result)
    payload = response.get_data(as_text=True)
    # Round-trip back; the data we ship to the client must match
    # what the parser produced (modulo dict-vs-TypedDict subtleties).
    decoded = json.loads(payload)
    assert decoded["source_format"] == result["source_format"]
    assert len(decoded["frames"]) == len(result["frames"])
    # Critically: nothing serialised to a string "NaN" or similar.
    assert "NaN" not in payload
    assert "Infinity" not in payload
