"""Pytest fixtures shared across the molwatch test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """Absolute path to ``tests/data/`` (sample files for parsers)."""
    return Path(__file__).parent / "data"
