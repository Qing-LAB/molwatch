"""Sanity check for the PySCF / geomeTRIC trajectory parser.

Synthesises a multi-frame XYZ in geomeTRIC's `<prefix>_optim.xyz`
format -- two complete frames + a third torn one -- and verifies the
parser drops the torn frame, converts Hartree to eV, and tags the
result with the right source_format.
"""

from __future__ import annotations

import json
import math

import pytest

from parsers.pyscf import PySCFParser


SAMPLE = """\
3
Iteration 0 Energy   -76.42675200
O   0.000000   0.000000   0.000000
H   0.957000   0.000000   0.000000
H  -0.239000   0.927000   0.000000
3
Iteration 1 Energy   -76.43012345
O   0.001000   0.001000   0.000000
H   0.957500   0.000500   0.000000
H  -0.239500   0.927500   0.000000
3
Iteration 2 Energy   -76.43500000
O   0.002000   0.002000   0.000000
"""


@pytest.fixture
def pyscf_traj_path(tmp_path):
    p = tmp_path / "myjob_geom_optim.xyz"
    p.write_text(SAMPLE)
    return str(p)


def test_can_parse(pyscf_traj_path):
    assert PySCFParser.can_parse(pyscf_traj_path) is True


def test_can_parse_rejects_non_xyz(tmp_path):
    p = tmp_path / "garbage.txt"
    p.write_text("just some random text\nhello world\n")
    assert PySCFParser.can_parse(str(p)) is False


def test_can_parse_rejects_plain_xyz_without_iteration_marker(tmp_path):
    """A plain XYZ (no 'Iteration K Energy E' comment) should not be
    claimed by the PySCF parser; the SIESTA parser shouldn't claim it
    either, so the registry will fall through to UnknownFormatError."""
    p = tmp_path / "regular.xyz"
    p.write_text("3\nwater\nO 0 0 0\nH 0 0 1\nH 1 0 0\n")
    assert PySCFParser.can_parse(str(p)) is False


def test_torn_frame_dropped(pyscf_traj_path):
    result = PySCFParser.parse(pyscf_traj_path)
    assert len(result["frames"]) == 2


def test_energy_units_converted_to_ev(pyscf_traj_path):
    """Hartree in the file -> eV in the result.  -76.4267520 Hartree
    is approximately -2079.7745 eV."""
    result = PySCFParser.parse(pyscf_traj_path)
    expected_eV_0 = -76.42675200 * 27.211386245988
    expected_eV_1 = -76.43012345 * 27.211386245988
    assert math.isclose(result["energies"][0], expected_eV_0, rel_tol=1e-6)
    assert math.isclose(result["energies"][1], expected_eV_1, rel_tol=1e-6)


def test_iteration_indices(pyscf_traj_path):
    result = PySCFParser.parse(pyscf_traj_path)
    assert result["iterations"] == [0, 1]


def test_frame_coordinates(pyscf_traj_path):
    result = PySCFParser.parse(pyscf_traj_path)
    assert result["frames"][0][0] == ["O", 0.0, 0.0, 0.0]
    assert result["frames"][1][1] == ["H", 0.9575, 0.0005, 0.0]


def test_no_lattice_for_pyscf(pyscf_traj_path):
    result = PySCFParser.parse(pyscf_traj_path)
    assert result["lattice"] is None


def test_source_format_tag(pyscf_traj_path):
    result = PySCFParser.parse(pyscf_traj_path)
    assert result["source_format"] == "pyscf"


def test_max_forces_none_without_qdata(pyscf_traj_path):
    """No companion .qdata -> max_forces is all None (placeholders)."""
    result = PySCFParser.parse(pyscf_traj_path)
    assert all(f is None for f in result["max_forces"])
    assert len(result["max_forces"]) == len(result["frames"])


def test_qdata_provides_max_forces(tmp_path):
    """If <prefix>.qdata sits next to <prefix>_optim.xyz, parse it for
    per-step max-force values.  Convention is per-atom |F| (matches
    SIESTA's 'Max' line) so plots overlay across formats sensibly,
    NOT max scalar gradient component.
    """
    traj = tmp_path / "myjob_geom_optim.xyz"
    traj.write_text(SAMPLE)
    qdata = tmp_path / "myjob_geom.qdata.txt"
    qdata.write_text(
        "ENERGY -76.4267520\n"
        # 3 atoms x 3 components = 9 values per frame.
        "GRADIENT 0.001 0.002 0.003 0.004 0.005 0.006 0.007 0.008 0.009\n"
        "ENERGY -76.4301234\n"
        "GRADIENT 0.0005 0.0006 0.0007 0.0008 0.0009 0.0010 0.0011 0.0012 0.0013\n"
    )
    result = PySCFParser.parse(str(traj))
    # Per-atom |F| for frame 0:
    #   atom1 = sqrt(0.001^2+0.002^2+0.003^2) ~= 0.003742
    #   atom2 = sqrt(0.004^2+0.005^2+0.006^2) ~= 0.008775
    #   atom3 = sqrt(0.007^2+0.008^2+0.009^2) ~= 0.013928   <- max
    expected = math.sqrt(0.007**2 + 0.008**2 + 0.009**2)
    expected *= 27.211386245988 / 0.5291772108     # Ha/Bohr -> eV/Ang
    assert result["max_forces"][0] is not None
    assert math.isclose(result["max_forces"][0], expected, rel_tol=1e-6)


def test_json_safe(pyscf_traj_path):
    result = PySCFParser.parse(pyscf_traj_path)
    json.dumps(result, allow_nan=False)


# --------------------------------------------------------------------- #
#  scf_history: parse PySCF .log for SCF iteration tables               #
# --------------------------------------------------------------------- #


_SCF_LOG_SAMPLE = """\
Some PySCF banner noise
init E= 0.00000
cycle= 0 E= -100.0  delta_E= 0.00  |g|= 5.0  |ddm|= 1.0
  HOMO = -0.20  LUMO = -0.10
cycle= 1 E= -100.5  delta_E= -0.5  |g|= 1.0  |ddm|= 0.3
  HOMO = -0.21  LUMO = -0.11
cycle= 2 E= -100.6  delta_E= -0.1  |g|= 0.05 |ddm|= 0.01
converged SCF energy = -100.6
some intermediate banner
cycle= 0 E= -110.0  delta_E= 0.00  |g|= 3.0  |ddm|= 0.5
cycle= 1 E= -110.4  delta_E= -0.4  |g|= 0.5  |ddm|= 0.05
cycle= 2 E= -110.5  delta_E= -0.1  |g|= 0.005 |ddm|= 0.001
converged SCF energy = -110.5
"""


def test_scf_history_parses_two_runs(tmp_path):
    """Two consecutive SCF runs (one per geom-opt step) produce two
    entries in scf_history."""
    traj = tmp_path / "myjob_geom_optim.xyz"
    traj.write_text(SAMPLE)
    log  = tmp_path / "myjob.log"          # NOTE: no _geom suffix
    log.write_text(_SCF_LOG_SAMPLE)
    result = PySCFParser.parse(str(traj))
    assert "scf_history" in result
    runs = result["scf_history"]
    assert len(runs) == 2
    assert len(runs[0]) == 3
    assert len(runs[1]) == 3
    # Energies in eV (Hartree -> eV via 27.211386...)
    HA = 27.211386245988
    assert math.isclose(runs[0][0]["energy"], -100.0 * HA)
    assert math.isclose(runs[1][2]["energy"], -110.5 * HA)


def test_scf_history_units_converted(tmp_path):
    """gnorm is converted from Ha/Bohr to eV/A; energies from
    Hartree to eV."""
    traj = tmp_path / "myjob_geom_optim.xyz"
    traj.write_text(SAMPLE)
    log  = tmp_path / "myjob.log"
    log.write_text(_SCF_LOG_SAMPLE)
    runs = PySCFParser.parse(str(traj))["scf_history"]
    HA_BOHR_TO_EV_ANG = 27.211386245988 / 0.5291772108
    # cycle 0 |g|=5.0 Ha/Bohr -> ... eV/A
    assert math.isclose(runs[0][0]["gnorm"], 5.0 * HA_BOHR_TO_EV_ANG)


def test_scf_history_empty_when_log_absent(pyscf_traj_path):
    """No <prefix>.log next to the trajectory -> scf_history = []."""
    result = PySCFParser.parse(pyscf_traj_path)
    assert result["scf_history"] == []


def test_scf_history_per_cycle_keys(tmp_path):
    """Every entry in a run has the documented keys:
       cycle, energy, delta_E, gnorm, ddm.
    """
    traj = tmp_path / "myjob_geom_optim.xyz"
    traj.write_text(SAMPLE)
    log  = tmp_path / "myjob.log"
    log.write_text(_SCF_LOG_SAMPLE)
    runs = PySCFParser.parse(str(traj))["scf_history"]
    expected = {"cycle", "energy", "delta_E", "gnorm", "ddm"}
    for run in runs:
        for entry in run:
            assert set(entry.keys()) == expected
