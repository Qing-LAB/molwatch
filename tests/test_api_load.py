"""End-to-end tests for /api/load: JSON path mode + multipart upload mode.

The multipart branch is the file-picker fallback for users who click
Load without typing a path.  These tests verify both flows produce a
parseable response and that uploaded files come back tagged
``uploaded=True``.
"""

from __future__ import annotations

import io

import pytest

from molwatch import web as app_module


_SIESTA_HEAD = (
    "Welcome to SIESTA -- v4.1\n"
    "redata: prelude\n"
    "outcoor: Atomic coordinates (Ang):\n"
    "   1.00000000    2.00000000    3.00000000   1       1  C\n"
    "\n"
    "siesta: E_KS(eV) =          -50.0000\n"
)


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture(autouse=True)
def _reset_app_state():
    """Clear the global state between tests so they don't leak."""
    with app_module._lock:
        app_module._state["path"]     = None
        app_module._state["mtime"]    = None
        app_module._state["data"]     = None
        app_module._state["parser"]   = None
        app_module._state["uploaded"] = False
    yield


# --------------------------------------------------------------------- #
#  JSON path mode (live-watch)                                          #
# --------------------------------------------------------------------- #


def test_load_by_json_path(client, tmp_path):
    p = tmp_path / "run.out"
    p.write_text(_SIESTA_HEAD)
    r = client.post("/api/load", json={"path": str(p)})
    body = r.get_json()
    assert body["ok"] is True
    assert body["uploaded"] is False
    assert body["format"] == "siesta"


def test_load_by_json_path_missing_file(client, tmp_path):
    r = client.post("/api/load", json={"path": str(tmp_path / "nope.out")})
    body = r.get_json()
    assert r.status_code == 404
    assert body["ok"] is False


def test_load_by_json_path_empty(client):
    r = client.post("/api/load", json={"path": ""})
    body = r.get_json()
    assert r.status_code == 400
    assert body["ok"] is False


# --------------------------------------------------------------------- #
#  Multipart upload mode (file-picker fallback)                         #
# --------------------------------------------------------------------- #


def test_load_by_multipart(client):
    fd = {
        "file": (io.BytesIO(_SIESTA_HEAD.encode()), "run.out"),
    }
    r = client.post("/api/load",
                    data=fd,
                    content_type="multipart/form-data")
    body = r.get_json()
    assert body["ok"] is True, body
    assert body["uploaded"] is True
    assert body["uploaded_filename"] == "run.out"
    assert body["format"] == "siesta"


def test_load_by_multipart_unrecognised_format(client):
    """An upload that no parser claims should 400 cleanly and not
    leave a stale temp file referenced in _state."""
    fd = {
        "file": (io.BytesIO(b"junk content nothing recognises\n"),
                 "garbage.txt"),
    }
    r = client.post("/api/load",
                    data=fd,
                    content_type="multipart/form-data")
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False


def test_load_by_multipart_replaces_previous_upload(client, tmp_path):
    """A second upload must clean up the previous temp file (best-effort
    -- we just check that _last_temp_upload moves to the new path)."""
    a = io.BytesIO(_SIESTA_HEAD.encode())
    client.post("/api/load",
                data={"file": (a, "first.out")},
                content_type="multipart/form-data")
    first_temp = app_module._last_temp_upload
    assert first_temp is not None

    b = io.BytesIO(_SIESTA_HEAD.encode())
    client.post("/api/load",
                data={"file": (b, "second.out")},
                content_type="multipart/form-data")
    second_temp = app_module._last_temp_upload
    assert second_temp is not None
    assert second_temp != first_temp


def test_load_by_multipart_persists_path_for_data_polls(client):
    """After an upload, /api/data must still return the parsed payload.
    The temp file lingers (we don't delete it on the same request) so
    the existing _refresh_if_changed machinery handles it normally."""
    fd = {"file": (io.BytesIO(_SIESTA_HEAD.encode()), "polled.out")}
    client.post("/api/load",
                data=fd,
                content_type="multipart/form-data")

    r = client.get("/api/data")
    body = r.get_json()
    assert body["ok"] is True
    assert body["uploaded"] is True
    assert body["data"]["source_format"] == "siesta"
