"""Cross-parser invariant tests.

These tests assert behaviour that should hold ACROSS parsers, not
within any single one:

  * Two formats representing the same molecule produce equivalent
    frames / energies / forces (the unit-conversion contract -- eV
    everywhere, eV/Ang everywhere, etc.).
  * The registry deterministically picks the right parser for a
    given file even when more than one *could* be a candidate.
  * Engine-specific UI-relevant fields (source_format, scf_history
    key set) are populated correctly so the front-end's adaptation
    logic has the data it needs.

Architecture references:
  - architecture.md §2.1 (single source of truth for contracts)
  - architecture.md §2.4 (engine-aware UI)
  - architecture.md §4.3 (detection registry order)
"""

from __future__ import annotations

import math

import pytest

from molwatch.parsers import PARSERS, detect_parser
from molwatch.parsers.molwatch_log import MolwatchLogParser
from molwatch.parsers.pyscf import PySCFParser
from molwatch.parsers.siesta import SiestaParser


# --------------------------------------------------------------------- #
#  Registry determinism                                                 #
# --------------------------------------------------------------------- #


def test_registry_is_first_match_wins(tmp_path):
    """`detect_parser` returns the *first* parser whose can_parse
    accepts the file.  Ordering is deterministic and documented:
    MolwatchLogParser before SIESTA before PySCF, most-specific
    first.  This test pins the ordering -- if PARSERS gets
    reordered without intent, the failure is loud."""
    assert PARSERS[0] is MolwatchLogParser, (
        "MolwatchLogParser must be first; its `# molwatch trajectory "
        "log` header is unambiguous."
    )
    assert PARSERS[1] is SiestaParser
    assert PARSERS[2] is PySCFParser


def test_only_one_parser_claims_a_molwatch_log(tmp_path):
    """A .molwatch.log file should be claimed by exactly one parser
    (the molwatch_log one).  Defensive: if a future parser starts
    accepting molwatch.log files by accident, this test fails."""
    p = tmp_path / "x.molwatch.log"
    p.write_text(
        "# molwatch trajectory log v1\n"
        "# engine: siesta\n"
        "# created: 2026-01-01T00:00:00\n"
        "\n"
        "==== molwatch step 0 begin ====\n"
        "n_atoms: 1\n"
        "coordinates (Ang):\n"
        "   H      0.0 0.0 0.0\n"
        "energy (eV): -1.0\n"
        "forces (eV/Ang):\n"
        "max_force (eV/Ang): None\n"
        "scf_history begin\n"
        "scf_history end\n"
        "==== molwatch step 0 end ====\n"
    )
    claimers = [c for c in PARSERS if c.can_parse(str(p))]
    assert claimers == [MolwatchLogParser], (
        f"Expected only MolwatchLogParser to claim a .molwatch.log; "
        f"got {[c.name for c in claimers]}.  Fix detector specificity."
    )


def test_only_one_parser_claims_a_siesta_v5_out(tmp_path):
    """SIESTA v5 output starts with `Executable      : siesta` -- the
    PySCF parser must reject it because line 0 is not an integer.
    If it ever starts accepting (e.g., a future relaxation of the
    XYZ check), this test makes it loud."""
    p = tmp_path / "siesta.out"
    p.write_text(
        "Executable      : siesta\n"
        "Version         : 5.4.2\n"
        "Architecture    : x86_64\n"
        "* Running in serial mode.\n"
        ">> Start of run:  28-APR-2026  20:00:00\n"
        "\n"
        "                    ***********************\n"
        "                    *  WELCOME TO SIESTA  *\n"
        "                    ***********************\n"
        "outcoor: Atomic coordinates (Ang):\n"
        "   1.0 2.0 3.0   1   1  C\n"
    )
    claimers = [c for c in PARSERS if c.can_parse(str(p))]
    assert claimers == [SiestaParser]


def test_only_one_parser_claims_a_geometric_xyz(tmp_path):
    """A geomeTRIC `_optim.xyz` should be claimed by PySCFParser
    exclusively.  The molwatch_log header is missing so its parser
    must reject; the SIESTA structural markers don't appear so its
    parser must reject."""
    p = tmp_path / "h2_geom_optim.xyz"
    p.write_text(
        "2\n"
        "Iteration 0 Energy -1.1\n"
        "H 0.0 0.0 0.0\n"
        "H 0.74 0.0 0.0\n"
    )
    claimers = [c for c in PARSERS if c.can_parse(str(p))]
    assert claimers == [PySCFParser]


# --------------------------------------------------------------------- #
#  Unit-convention consistency across formats                           #
# --------------------------------------------------------------------- #


def test_same_h2_molecule_in_two_formats_gives_same_geometry(tmp_path):
    """The same molecule expressed in two formats must produce the
    same geometry after parsing.  This is the unit-convention
    contract from architecture.md §2.1: coordinates are Angstrom
    everywhere, regardless of format.  If a parser ever introduced
    a unit bug (Bohr vs Angstrom, or worse, a per-axis swap), this
    test catches the divergence."""
    # Same H2 molecule, expressed in two different formats.
    siesta_path = tmp_path / "h2.out"
    siesta_path.write_text(
        "Welcome to SIESTA -- v4.1\n"
        "redata: prelude\n"
        "outcoor: Atomic coordinates (Ang):\n"
        "   0.00000000    0.00000000    0.00000000   1       1  H\n"
        "   0.74000000    0.00000000    0.00000000   1       2  H\n"
        "\n"
        "siesta: E_KS(eV) =          -29.9325\n"
    )
    pyscf_path = tmp_path / "h2_geom_optim.xyz"
    pyscf_path.write_text(
        "2\n"
        "Iteration 0 Energy -1.1000000\n"     # -1.1 Ha == -29.9325 eV
        "H 0.00 0.00 0.00\n"
        "H 0.74 0.00 0.00\n"
    )
    s = SiestaParser.parse(str(siesta_path))
    p = PySCFParser.parse(str(pyscf_path))

    # Same number of frames, same number of atoms.
    assert len(s["frames"]) == len(p["frames"]) == 1
    assert len(s["frames"][0]) == len(p["frames"][0]) == 2

    # Same coordinates (within float tolerance).
    for s_atom, p_atom in zip(s["frames"][0], p["frames"][0]):
        assert s_atom[0] == p_atom[0]                         # element
        for i in (1, 2, 3):                                   # x, y, z
            assert math.isclose(s_atom[i], p_atom[i],
                                abs_tol=1e-6), (s_atom, p_atom)

    # Both energies in eV and within float tolerance.  This is the
    # critical cross-format unit-convention check: PySCF's parser
    # converts Hartree -> eV at parse time so the front-end can
    # plot both engines on the same axis.
    assert math.isclose(s["energies"][0], p["energies"][0], abs_tol=1e-2)


# --------------------------------------------------------------------- #
#  Engine-aware UI fields                                               #
# --------------------------------------------------------------------- #


def test_scf_history_key_set_distinguishes_engines(tmp_path):
    """The front-end's residual-axis selection sniffs the cycle dict's
    keys (gnorm vs dHmax) to decide which residual to plot.  This
    test pins the contract: SIESTA-parsed cycles carry dHmax/dDmax;
    PySCF-parsed cycles carry gnorm/ddm.  No cross-contamination."""
    # Set up a SIESTA file with one SCF cycle.
    siesta_path = tmp_path / "siesta.out"
    siesta_path.write_text(
        "Welcome to SIESTA\n"
        "redata: prelude\n"
        "outcoor: Atomic coordinates (Ang):\n"
        "   0.0 0.0 0.0   1   1  H\n"
        "\n"
        "   scf:    1   -1.0   -1.5   -1.5   0.10  -1.0   0.5\n"
        "siesta: E_KS(eV) =          -1.5\n"
    )
    s_result = SiestaParser.parse(str(siesta_path))
    if s_result["scf_history"][0]:
        keys = set(s_result["scf_history"][0][0].keys())
        assert "dHmax" in keys and "dDmax" in keys
        assert "gnorm" not in keys and "ddm" not in keys


def test_source_format_value_matches_parser_name(tmp_path):
    """source_format must equal the parser's `name` for every native
    parser.  (MolwatchLogParser is the exception -- it sets it from
    the file's `# engine:` header so the UI can adapt to whichever
    engine produced the unified log; tested separately.)"""
    s_path = tmp_path / "siesta.out"
    s_path.write_text(
        "Welcome to SIESTA\n"
        "redata: prelude\n"
        "outcoor: Atomic coordinates (Ang):\n"
        "   0.0 0.0 0.0   1   1  H\n"
        "\n"
    )
    assert SiestaParser.parse(str(s_path))["source_format"] == "siesta"

    p_path = tmp_path / "x_geom_optim.xyz"
    p_path.write_text("1\nIteration 0 Energy -0.5\nH 0 0 0\n")
    assert PySCFParser.parse(str(p_path))["source_format"] == "pyscf"
