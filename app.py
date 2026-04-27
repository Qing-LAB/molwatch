"""Live SIESTA output viewer.

A small Flask server that watches a SIESTA output file, extracts the
geometry / energy / max-force trajectory, and serves it to a 3Dmol.js
browser viewer that updates while the calculation is still running.

Usage
-----
    python app.py                         # http://127.0.0.1:5000
    python app.py --port 8080 --host 0.0.0.0

Open the page, paste an absolute path to the SIESTA output file, and
click *Load*.  The page polls the server roughly every 15 seconds; when
the file's mtime advances the parser re-runs and the viewer + plots
refresh.
"""

from __future__ import annotations

import argparse
import os
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, render_template, request

from siesta_parser import parse_siesta_output


app = Flask(__name__)

# Single global "current file" state.  A single user / single tab is the
# expected usage so a plain dict + lock is enough; no need for sessions.
_lock = Lock()
_state: Dict[str, Any] = {
    "path": None,
    "mtime": None,
    "data": None,
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
        if mtime != _state["mtime"]:
            try:
                _state["data"] = parse_siesta_output(path)
            except Exception as exc:  # pragma: no cover - defensive
                return None, f"Parse error: {exc}"
            _state["mtime"] = mtime
        # Return a shallow copy so callers can't mutate our cache.
        return dict(_state), None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/load", methods=["POST"])
def api_load():
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "Empty path."}), 400
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": f"File not found: {path}"}), 404
    with _lock:
        _state["path"] = path
        _state["mtime"] = None  # force a re-parse next time we check
        _state["data"] = None
    state, err = _refresh_if_changed()
    if err:
        return jsonify({"ok": False, "error": err}), 500
    return jsonify(
        {
            "ok": True,
            "path": state["path"],
            "mtime": state["mtime"],
            "data": state["data"],
        }
    )


@app.route("/api/data")
def api_data():
    """Return the parsed payload, or just an mtime if nothing changed."""
    client_mtime = request.args.get("mtime", type=float)
    state, err = _refresh_if_changed()
    if err:
        return jsonify({"ok": False, "error": err})
    if client_mtime is not None and client_mtime == state["mtime"]:
        return jsonify({"ok": True, "changed": False, "mtime": state["mtime"]})
    return jsonify(
        {
            "ok": True,
            "changed": True,
            "path": state["path"],
            "mtime": state["mtime"],
            "data": state["data"],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Live SIESTA output viewer.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
