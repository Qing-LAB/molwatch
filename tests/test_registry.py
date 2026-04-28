"""Parser-registry tests.

Verifies that ``detect_parser`` picks the right parser based on file
content, and that ``UnknownFormatError`` fires for unrecognised input.
"""

from __future__ import annotations

import pytest

from parsers import (
    PARSERS,
    UnknownFormatError,
    detect_parser,
    parser_summary,
)
from parsers.siesta import SiestaParser
from parsers.pyscf  import PySCFParser


_SIESTA_HEAD = (
    "Welcome to SIESTA -- v4.1\n"
    "redata: Some metadata\n"
)
_PYSCF_HEAD = (
    "3\n"
    "Iteration 0 Energy -76.4267\n"
    "O 0 0 0\nH 1 0 0\nH 0 1 0\n"
)


def test_detect_siesta(tmp_path):
    p = tmp_path / "run.out"
    p.write_text(_SIESTA_HEAD)
    assert detect_parser(str(p)) is SiestaParser


def test_detect_pyscf(tmp_path):
    p = tmp_path / "myjob_geom_optim.xyz"
    p.write_text(_PYSCF_HEAD)
    assert detect_parser(str(p)) is PySCFParser


def test_detect_unknown_format(tmp_path):
    p = tmp_path / "garbage.txt"
    p.write_text("just some random text\n")
    with pytest.raises(UnknownFormatError):
        detect_parser(str(p))


def test_registry_lists_all_parsers():
    names = [c.name for c in PARSERS]
    assert "siesta" in names
    assert "pyscf" in names


def test_summary_shape():
    s = parser_summary()
    assert isinstance(s, list) and s
    for entry in s:
        assert "name" in entry and "label" in entry
