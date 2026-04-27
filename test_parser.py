"""Sanity check for siesta_parser.parse_siesta_output().

Builds a tiny synthetic SIESTA-style output that contains the same
markers as the real thing -- two complete CG steps plus a truncated
third step that is mid-writing its outcoor block -- and verifies that
the parser keeps two complete steps with the right numbers.

Run:
    python test_parser.py
"""

import json
import math
import os
import tempfile

from siesta_parser import parse_siesta_output


SAMPLE = """\
Some header noise that should be ignored.
redata: Max. number of TDED Iter        =        1
redata: Max. number of SCF Iter                     =      500
redata: Maximum number of optimization moves        =      200
No. of atoms with KB's overlaping orbs in proc 0. Max # of overlaps:      84     159

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

outcell: Cell vector modules (Ang)   :   10.0  10.0  10.0
outcell: Cell volume (Ang**3)        :  1000.0

siesta: Eharris =   -289239.010387
siesta: Etot    =   -290967.213964
siesta: FreeEng =   -290967.445028

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
"""  # NB: third step truncated mid-coords -- should be discarded.


def main() -> None:
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".out", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(SAMPLE)
        tmp.close()
        result = parse_siesta_output(tmp.name)
    finally:
        os.unlink(tmp.name)

    frames     = result["frames"]
    energies   = result["energies"]
    max_forces = result["max_forces"]
    lattice    = result["lattice"]

    # --- frames ---------------------------------------------------------
    assert len(frames) == 2, f"expected 2 complete frames, got {len(frames)}"
    assert frames[0] == [
        ["C", 1.0, 2.0, 3.0],
        ["H", 4.0, 5.0, 6.0],
    ], frames[0]
    assert frames[1] == [
        ["C", 1.1, 2.1, 3.1],
        ["H", 4.1, 5.1, 6.1],
    ], frames[1]

    # --- energies / forces ---------------------------------------------
    assert math.isclose(energies[0],   -100.1234)
    assert math.isclose(energies[1],   -101.5678)
    assert math.isclose(max_forces[0], 1.234567)   # not the constrained one
    assert math.isclose(max_forces[1], 0.987654)

    # --- per-atom forces -----------------------------------------------
    forces = result["forces"]
    assert len(forces) == 2
    assert forces[0] == [[0.10, 0.20, 0.30], [0.40, 0.50, 0.60]]
    assert forces[1] == [[0.05, 0.06, 0.07], [0.08, 0.09, 0.10]]

    # --- lattice -------------------------------------------------------
    assert lattice == [
        [10.0,  0.0,  0.0],
        [ 0.0, 10.0,  0.0],
        [ 0.0,  0.0, 10.0],
    ], lattice

    # --- iterations ---
    assert result["iterations"] == [0, 1]

    # --- JSON safety: result must serialise with strict JSON (no NaN) ---
    json.dumps(result, allow_nan=False)

    print("OK -- parser produced:")
    print(f"  frames       : {len(frames)}")
    print(f"  energies     : {energies}")
    print(f"  max_forces   : {max_forces}")
    print(f"  lattice      : {lattice}")


if __name__ == "__main__":
    main()
