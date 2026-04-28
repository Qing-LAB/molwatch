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
    per-step max-force values."""
    traj = tmp_path / "myjob_geom_optim.xyz"
    traj.write_text(SAMPLE)
    qdata = tmp_path / "myjob_geom.qdata.txt"
    qdata.write_text(
        "ENERGY -76.4267520\n"
        "GRADIENT 0.001 0.002 0.003 0.004 0.005 0.006 0.007 0.008 0.009\n"
        "ENERGY -76.4301234\n"
        "GRADIENT 0.0005 0.0006 0.0007 0.0008 0.0009 0.0010 0.0011 0.0012 0.0013\n"
    )
    result = PySCFParser.parse(str(traj))
    # Max gradient component for frame 0 is 0.009 Ha/Bohr ~ 0.463 eV/A
    assert result["max_forces"][0] is not None
    assert math.isclose(result["max_forces"][0],
                        0.009 * 27.211386245988 / 0.5291772108,
                        rel_tol=1e-6)


def test_json_safe(pyscf_traj_path):
    result = PySCFParser.parse(pyscf_traj_path)
    json.dumps(result, allow_nan=False)
