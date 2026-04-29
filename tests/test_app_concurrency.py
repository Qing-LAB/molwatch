"""Concurrency-correctness tests for app._refresh_if_changed.

The implementation drops the lock during parse so other requests
aren't blocked.  This file pins down the race window: if a concurrent
/api/load swaps the active file mid-parse, the stale parse result
from the previous file must NOT clobber the new state.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict

import pytest

from molwatch import web as app_module
from molwatch.parsers.base import TrajectoryParser


# ----------------------------------------------------------------- #
#  A pretend "slow parser" that blocks until told to continue.       #
# ----------------------------------------------------------------- #


class SlowParser(TrajectoryParser):
    name  = "slow"
    label = "slow (test)"

    # When a parse() runs, it waits on this event before returning,
    # so the test thread can flip the active state mid-parse.
    release: threading.Event = threading.Event()
    parse_started: threading.Event = threading.Event()

    @classmethod
    def can_parse(cls, path: str) -> bool:
        return True   # not used in these tests

    @classmethod
    def parse(cls, path: str) -> Dict[str, Any]:
        cls.parse_started.set()
        cls.release.wait(timeout=5.0)
        return {
            "frames":        [],
            "lattice":       None,
            "iterations":    [],
            "energies":      [],
            "max_forces":    [],
            "forces":        [],
            "source_format": cls.name,
            "_marker":       path,
        }


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state and event flags between tests."""
    SlowParser.release.clear()
    SlowParser.parse_started.clear()
    with app_module._lock:
        app_module._state["path"]   = None
        app_module._state["mtime"]  = None
        app_module._state["data"]   = None
        app_module._state["parser"] = None
    yield
    SlowParser.release.set()


def test_stale_parse_doesnt_clobber_swapped_state(tmp_path):
    """While SlowParser is mid-parse on /tmp/A, swap state to /tmp/B.
    The eventual return value of the first parse must NOT overwrite
    /tmp/B's _state because path / parser changed under us."""
    file_a = tmp_path / "a.out"
    file_a.write_text("Welcome to SIESTA -- A\n")
    file_b = tmp_path / "b.out"
    file_b.write_text("Welcome to SIESTA -- B\n")

    with app_module._lock:
        app_module._state["path"]   = str(file_a)
        app_module._state["mtime"]  = None       # force reparse
        app_module._state["parser"] = SlowParser

    captured = {}

    def run_refresh_first():
        state, err = app_module._refresh_if_changed()
        captured["a_state"] = state
        captured["a_err"]   = err

    t1 = threading.Thread(target=run_refresh_first)
    t1.start()

    # Wait until the parse is actually running, then swap state.
    assert SlowParser.parse_started.wait(timeout=2.0)
    with app_module._lock:
        app_module._state["path"]   = str(file_b)
        app_module._state["mtime"]  = 12345.0
        app_module._state["data"]   = {"_marker": "B-data"}
        app_module._state["parser"] = SlowParser

    # Now release the slow parse on A.
    SlowParser.release.set()
    t1.join(timeout=5.0)
    assert not t1.is_alive(), "refresh thread didn't terminate"

    # The refresh must NOT have overwritten the B-data we just wrote.
    with app_module._lock:
        assert app_module._state["path"] == str(file_b)
        assert app_module._state["mtime"] == 12345.0
        assert app_module._state["data"] == {"_marker": "B-data"}


def test_concurrent_polls_dont_serialize_on_parse(tmp_path):
    """Two concurrent /api/data-style polls should both proceed: one
    parses (slow), the other sees the cached state and returns fast.
    The fast one shouldn't have to wait for the slow parse to finish."""
    file_a = tmp_path / "a.out"
    file_a.write_text("Welcome to SIESTA -- A\n")

    # Prime the cache with a parse first (synchronous, mtime=now).
    SlowParser.release.set()      # let the priming parse run through
    with app_module._lock:
        app_module._state["path"]   = str(file_a)
        app_module._state["mtime"]  = None
        app_module._state["parser"] = SlowParser
    primed_state, _ = app_module._refresh_if_changed()
    assert primed_state is not None

    # Now bump mtime by touching the file -> the next call will reparse.
    SlowParser.release.clear()
    SlowParser.parse_started.clear()
    file_a.write_text("Welcome to SIESTA -- A (modified)\n")

    timings = {}

    def slow_caller():
        t0 = time.time()
        app_module._refresh_if_changed()
        timings["slow"] = time.time() - t0

    def fast_caller():
        # Wait until the slow parse has begun, then immediately poll.
        SlowParser.parse_started.wait(timeout=2.0)
        # Force an mtime read that matches cached -> fast path.
        # (It will re-snapshot under the lock and return cached state.)
        t0 = time.time()
        # We can't easily make this hit the cache-equal branch (mtime
        # advanced), but we CAN measure that this call doesn't block on
        # the lock for the duration of the slow parse.
        with app_module._lock:
            pass
        timings["fast"] = time.time() - t0

    t_slow = threading.Thread(target=slow_caller)
    t_fast = threading.Thread(target=fast_caller)
    t_slow.start()
    t_fast.start()

    # Hold the slow parse for ~0.4 s, then release.
    time.sleep(0.4)
    SlowParser.release.set()

    t_slow.join(timeout=5.0)
    t_fast.join(timeout=5.0)
    # The fast caller should have grabbed the lock briefly even while
    # the slow parser was blocked.  If we held the lock during parse,
    # this would have been > 0.4 s.
    assert timings["fast"] < 0.2, (
        f"fast caller blocked for {timings['fast']:.2f} s -- lock was "
        f"held during parse"
    )
