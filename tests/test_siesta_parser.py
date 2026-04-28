"""Sanity check for the SIESTA output parser.

Builds a tiny synthetic SIESTA-style output that contains the same
markers as the real thing -- two complete CG steps plus a truncated
third step that is mid-writing its outcoor block -- and verifies that
the parser keeps two complete steps with the right numbers.
"""

from __future__ import annotations

import json
import math

import pytest

from parsers.siesta import SiestaParser


SAMPLE = """\
Welcome to SIESTA -- some header noise
redata: Max. number of TDED Iter        =        1
redata: Max. number of SCF Iter                     =      500
redata: Maximum number of optimization moves        =      200

                     ====================================
                        Begin CG opt. move =      0
                     ====================================

outcoor: Atomic coordinates (Ang):
   1.00000000    2.00000000    3.00000000   1       1  C
   4.00000000    5.00000000    6.00000000   2       2  H

outcell: Unit cell vectors (Ang):
       10.000000    0.000000    0.000000
        0.000000   10.000000    0.000000
        0.000000    0.000000   10.000000

siesta: Eharris =   -289239.010387

   scf:    1  -100.0  -100.0  -100.0  0.001 -1.0 0.5
SCF Convergence by DM+H criterion

siesta: E_KS(eV) =          -100.1234

siesta: Atomic forces (eV/Ang):
     1    0.10    0.20    0.30
     2    0.40    0.50    0.60
----------------------------------------
   Tot    0.50    0.70    0.90
----------------------------------------
   Max    1.234567
   Res    0.987654    sqrt( Sum f_i^2 / 3N )
----------------------------------------
   Max    1.234567    constrained



                     ====================================
                        Begin CG opt. move =      1
                     ====================================

outcoor: Atomic coordinates (Ang):
   1.10000000    2.10000000    3.10000000   1       1  C
   4.10000000    5.10000000    6.10000000   2       2  H

   scf:    1  -101.0  -101.0  -101.0  0.001 -1.0 0.5
SCF Convergence by DM+H criterion

siesta: E_KS(eV) =          -101.5678

siesta: Atomic forces (eV/Ang):
     1    0.05    0.06    0.07
     2    0.08    0.09    0.10
----------------------------------------
   Tot    0.13    0.15    0.17
----------------------------------------
   Max    0.987654
   Res    0.123456    sqrt( Sum f_i^2 / 3N )
----------------------------------------
   Max    0.987654    constrained

                     ====================================
                        Begin CG opt. move =      2
                     ====================================

outcoor: Atomic coordinates (Ang):
   1.20000000    2.20000000    3.20000000   1       1  C
"""


@pytest.fixture
def siesta_path(tmp_path):
    p = tmp_path / "run.out"
    p.write_text(SAMPLE)
    return str(p)


def test_can_parse(siesta_path):
    assert SiestaParser.can_parse(siesta_path) is True


def test_can_parse_rejects_non_siesta(tmp_path):
    p = tmp_path / "garbage.txt"
    p.write_text("just some random text\nhello world\n")
    assert SiestaParser.can_parse(str(p)) is False


def test_torn_frame_dropped_at_eof(siesta_path):
    result = SiestaParser.parse(siesta_path)
    assert len(result["frames"]) == 2


def test_frame_coordinates(siesta_path):
    result = SiestaParser.parse(siesta_path)
    assert result["frames"][0] == [
        ["C", 1.0, 2.0, 3.0],
        ["H", 4.0, 5.0, 6.0],
    ]
    assert result["frames"][1] == [
        ["C", 1.1, 2.1, 3.1],
        ["H", 4.1, 5.1, 6.1],
    ]


def test_energies(siesta_path):
    result = SiestaParser.parse(siesta_path)
    assert math.isclose(result["energies"][0], -100.1234)
    assert math.isclose(result["energies"][1], -101.5678)


def test_max_forces_skip_constrained_line(siesta_path):
    result = SiestaParser.parse(siesta_path)
    assert math.isclose(result["max_forces"][0], 1.234567)
    assert math.isclose(result["max_forces"][1], 0.987654)


def test_per_atom_forces(siesta_path):
    result = SiestaParser.parse(siesta_path)
    assert result["forces"][0] == [[0.10, 0.20, 0.30], [0.40, 0.50, 0.60]]
    assert result["forces"][1] == [[0.05, 0.06, 0.07], [0.08, 0.09, 0.10]]


def test_lattice_captured(siesta_path):
    result = SiestaParser.parse(siesta_path)
    assert result["lattice"] == [
        [10.0,  0.0,  0.0],
        [ 0.0, 10.0,  0.0],
        [ 0.0,  0.0, 10.0],
    ]


def test_iterations(siesta_path):
    result = SiestaParser.parse(siesta_path)
    assert result["iterations"] == [0, 1]


def test_source_format_tag(siesta_path):
    result = SiestaParser.parse(siesta_path)
    assert result["source_format"] == "siesta"


def test_json_safe_no_nan(siesta_path):
    """Result must serialise with strict JSON (no NaN)."""
    result = SiestaParser.parse(siesta_path)
    json.dumps(result, allow_nan=False)
