"""molwatch -- live trajectory viewer for SIESTA / PySCF / future.

A small Flask server that watches an output file (SIESTA .out,
geomeTRIC's <prefix>_optim.xyz, etc.) while the calculation is still
running and serves it to a 3Dmol.js browser viewer that updates while
new frames are being written.

Usage
-----
    python app.py                         # http://127.0.0.1:5000
    python app.py --port 8080 --host 0.0.0.0

Open the page, paste an absolute path to the output file, and click
*Load*.  The page polls the server roughly every 15 seconds; when the
file's mtime advances the parser re-runs and the viewer + plots
refresh.

Format support is plugin-style: see ``parsers/`` for the registered
parsers and ``parsers/__init__.py`` for the auto-detection registry.
"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from threading import Lock
from typing import Any, Dict, Optional, Tuple, Type, TypedDict

from flask import Flask, jsonify, render_template, request

from parsers import (
    UnknownFormatError,
    detect_parser,
    parser_summary,
)
from parsers.base import TrajectoryParser, ParsedTrajectory


# --------------------------------------------------------------------- #
#  Process-global file-watching state                                   #
# --------------------------------------------------------------------- #


class WatchedFileState(TypedDict):
    """The single 'currently-loaded file' record.

    Held in module-global ``_state`` and guarded by ``_lock``.  All
    fields start as `None` / `False` and are repopulated atomically
    by :func:`/api/load` (the only entry point that mutates them).
    Subsequent ``/api/data`` polls only read.
    """
    #: Absolute path the user typed; ``None`` when no file has been
    #: loaded yet, or a path inside the upload temp dir for picker
    #: uploads.
    path: Optional[str]
    #: ``os.path.getmtime(path)`` at the last successful parse.  Used
    #: to short-circuit /api/data when the file hasn't changed.
    mtime: Optional[float]
    #: The most-recent parsed result, conforming to
    #: :class:`parsers.base.ParsedTrajectory`.  ``None`` when nothing
    #: has loaded yet.
    data: Optional[ParsedTrajectory]
    #: The TrajectoryParser subclass selected for this file by
    #: :func:`detect_parser`.  Reused on /api/data polls to avoid
    #: re-running detection against an already-known file.
    parser: Optional[Type[TrajectoryParser]]
    #: True when the active file was supplied via the file-picker
    #: upload route (one-shot, no live polling).  False for
    #: live-watched paths the user typed.
    uploaded: bool


app = Flask(__name__)
# /api/load accepts EITHER a JSON {"path": "..."} body (live-watching
# mode) OR a multipart upload (file-picker fallback when the user
# clicks Load without typing a path).  50 MB is a generous cap for
# realistic SIESTA / PySCF logs while still bounding memory.
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024   # 50 MB

# Single global "current file" state.  A single user / single tab is
# the expected usage so a plain dict + lock is enough; no need for
# sessions.  See :class:`WatchedFileState` for the field-level
# contract.
_lock = Lock()
_state: WatchedFileState = {
    "path":     None,
    "mtime":    None,
    "data":     None,
    "parser":   None,
    "uploaded": False,
}

# Track the last temp file we created from a file-picker upload so
# we can clean it up when a new upload comes in.
_last_temp_upload: Optional[str] = None


def _refresh_if_changed() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Re-parse the current file iff its mtime has advanced.

    Returns ``(state, None)`` on success or ``(None, error_message)`` on
    failure.  Cheap when the file is unchanged.

    Locking strategy: snapshot path/mtime/parser under the lock, then
    drop the lock during the actual parse so other concurrent requests
    aren't blocked for the duration of a multi-MB log re-parse.  After
    parsing we re-acquire and only commit the result if the active file
    hasn't changed under us (defensive against a /api/load racing with
    a /api/data poll).
    """
    # ---- Snapshot under the lock --------------------------------
    with _lock:
        path = _state["path"]
        if not path:
            return None, "No file loaded yet."
        cached_mtime = _state["mtime"]
        parser_cls   = _state["parser"]

    if not os.path.isfile(path):
        return None, f"File not found: {path}"
    try:
        mtime = os.path.getmtime(path)
    except OSError as exc:
        return None, str(exc)

    # ---- Cheap path: nothing changed ----------------------------
    if mtime == cached_mtime:
        with _lock:
            return dict(_state), None

    # ---- Parse OUTSIDE the lock ---------------------------------
    try:
        new_data = parser_cls.parse(path)
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"Parse error: {exc}"

    # ---- Re-acquire to commit (skip if a concurrent /api/load
    #      already swapped to a different file under us) ---------
    with _lock:
        if _state["path"] == path and _state["parser"] is parser_cls:
            _state["data"]  = new_data
            _state["mtime"] = mtime
        return dict(_state), None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/formats")
def api_formats():
    """Lightweight: lists registered parsers + their human labels."""
    return jsonify({"ok": True, "formats": parser_summary()})


@app.route("/api/load", methods=["POST"])
def api_load():
    """Two body shapes:

      * multipart/form-data with a single file field "file" -- file
        is saved to a temp file and parsed (one-shot, no live update);
      * application/json with {"path": "..."} -- server reads the
        absolute path off disk and polls it for live updates.

    The multipart branch is the file-picker fallback for users who
    don't want to type an absolute path.
    """
    # ---- multipart upload (file-picker mode) -----------------------
    if "file" in request.files:
        return _api_load_multipart(request.files["file"])

    # ---- JSON path (live-watch mode) -------------------------------
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "Empty path."}), 400
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": f"File not found: {path}"}), 404
    # Auto-detect parser before committing to the new path so an
    # unsupported file doesn't blank out a working one.
    try:
        parser_cls = detect_parser(path)
    except UnknownFormatError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with _lock:
        _state["path"]     = path
        _state["mtime"]    = None      # force a re-parse next time
        _state["data"]     = None
        _state["parser"]   = parser_cls
        _state["uploaded"] = False

    state, err = _refresh_if_changed()
    if err:
        return jsonify({"ok": False, "error": err}), 500
    return jsonify({
        "ok":       True,
        "path":     state["path"],
        "mtime":    state["mtime"],
        "format":   parser_cls.name,
        "label":    parser_cls.label,
        "data":     state["data"],
        "uploaded": False,
    })


def _api_load_multipart(uploaded_file):
    """Save the uploaded file to a tempdir, parse, and stash the temp
    path on _state.  Future /api/data polls work like always but the
    mtime never advances (we don't write to the temp file again), so
    the data effectively snapshots at upload time.

    Old temp uploads are cleaned up when a new one comes in -- a
    process restart drops the rest.
    """
    global _last_temp_upload

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"ok": False, "error": "Empty filename."}), 400

    # Keep the original suffix (.xyz / .out / .log) so the parser-
    # detection layer's content sniff isn't fooled by extension-less
    # names.  Sanitise the basename to dodge path-traversal in the
    # temp filename itself.
    safe_name = os.path.basename(uploaded_file.filename) or "upload"
    tmp_path = os.path.join(
        tempfile.gettempdir(),
        f"molwatch_{int(time.time())}_{safe_name}"
    )
    try:
        uploaded_file.save(tmp_path)
    except OSError as exc:
        return jsonify({"ok": False,
                        "error": f"Failed to write upload: {exc}"}), 500

    try:
        parser_cls = detect_parser(tmp_path)
    except UnknownFormatError as exc:
        # Don't keep an unrecognised upload around.
        try: os.remove(tmp_path)
        except OSError: pass
        return jsonify({"ok": False, "error": str(exc)}), 400

    with _lock:
        # Clean up any previous upload's temp file.
        if _last_temp_upload and _last_temp_upload != tmp_path:
            try: os.remove(_last_temp_upload)
            except OSError: pass
        _last_temp_upload = tmp_path
        _state["path"]     = tmp_path
        _state["mtime"]    = None
        _state["data"]     = None
        _state["parser"]   = parser_cls
        _state["uploaded"] = True

    state, err = _refresh_if_changed()
    if err:
        return jsonify({"ok": False, "error": err}), 500
    return jsonify({
        "ok":               True,
        "path":             tmp_path,
        "mtime":            state["mtime"],
        "format":           parser_cls.name,
        "label":            parser_cls.label,
        "data":             state["data"],
        "uploaded":         True,
        "uploaded_filename": uploaded_file.filename,
    })


@app.route("/api/data")
def api_data():
    """Return the parsed payload, or just an mtime if nothing changed."""
    client_mtime = request.args.get("mtime", type=float)
    state, err = _refresh_if_changed()
    if err:
        return jsonify({"ok": False, "error": err})
    if client_mtime is not None and client_mtime == state["mtime"]:
        return jsonify({"ok": True, "changed": False, "mtime": state["mtime"]})
    parser_cls = state["parser"]
    return jsonify({
        "ok":       True,
        "changed":  True,
        "path":     state["path"],
        "mtime":    state["mtime"],
        "format":   parser_cls.name,
        "label":    parser_cls.label,
        "data":     state["data"],
        "uploaded": state.get("uploaded", False),
    })


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="molwatch -- live SIESTA / PySCF trajectory viewer."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # Loud warning when binding to non-loopback.  /api/load reads any
    # file the server can access, so exposing it on a network interface
    # is effectively a remote arbitrary-file-read endpoint.
    if args.host not in _LOCAL_HOSTS:
        import sys as _sys
        print(f"WARNING: --host={args.host} exposes /api/load to the network.",
              file=_sys.stderr)
        print("         The endpoint reads ANY local file the server can",
              file=_sys.stderr)
        print("         access.  Only do this on a trusted single-user",
              file=_sys.stderr)
        print("         machine, or add a reverse-proxy with auth in front.",
              file=_sys.stderr)

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
