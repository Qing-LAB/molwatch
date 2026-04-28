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
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, render_template, request

from parsers import (
    UnknownFormatError,
    detect_parser,
    parser_summary,
)


app = Flask(__name__)
# /api/load only takes a JSON path (no file body), so a small content
# cap is plenty -- this just stops a runaway client from posting a
# multi-megabyte JSON blob.
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024   # 1 MB

# Single global "current file" state.  A single user / single tab is
# the expected usage so a plain dict + lock is enough; no need for
# sessions.
_lock = Lock()
_state: Dict[str, Any] = {
    "path":   None,
    "mtime":  None,
    "data":   None,
    "parser": None,    # the TrajectoryParser class chosen for this file
}


def _refresh_if_changed() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Re-parse the current file iff its mtime has advanced.

    Returns ``(state, None)`` on success or ``(None, error_message)`` on
    failure.  Cheap when the file is unchanged.
    """
    with _lock:
        path = _state["path"]
        if not path:
            return None, "No file loaded yet."
        if not os.path.isfile(path):
            return None, f"File not found: {path}"
        try:
            mtime = os.path.getmtime(path)
        except OSError as exc:
            return None, str(exc)
        parser_cls = _state["parser"]
        if mtime != _state["mtime"]:
            try:
                _state["data"] = parser_cls.parse(path)
            except Exception as exc:  # pragma: no cover - defensive
                return None, f"Parse error: {exc}"
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
        _state["path"]   = path
        _state["mtime"]  = None        # force a re-parse next time
        _state["data"]   = None
        _state["parser"] = parser_cls

    state, err = _refresh_if_changed()
    if err:
        return jsonify({"ok": False, "error": err}), 500
    payload = {
        "ok":     True,
        "path":   state["path"],
        "mtime":  state["mtime"],
        "format": parser_cls.name,
        "label":  parser_cls.label,
        "data":   state["data"],
    }
    return jsonify(payload)


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
        "ok":      True,
        "changed": True,
        "path":    state["path"],
        "mtime":   state["mtime"],
        "format":  parser_cls.name,
        "label":   parser_cls.label,
        "data":    state["data"],
    })


def main() -> None:
    parser = argparse.ArgumentParser(
        description="molwatch -- live SIESTA / PySCF trajectory viewer."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
