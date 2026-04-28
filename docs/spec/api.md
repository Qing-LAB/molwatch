# Spec — Flask endpoints

**Module**: `app.py` &nbsp;·&nbsp; **Tests**: `tests/test_api_load.py`,
`tests/test_app_concurrency.py`, `tests/test_registry.py`

The Flask app exposes a small JSON API that the front-end consumes,
plus the `/` HTML page.  All endpoints are JSON in / JSON out, except
`/api/load` which also accepts multipart for the file-picker upload
mode.

## Endpoints

| route          | method | body                          | success                          | error |
| ---            | ---    | ---                           | ---                              | ---   |
| `/`            | GET    | —                             | HTML (Jinja2)                    | —     |
| `/api/health`  | GET    | —                             | `{ok: true, version}`            | —     |
| `/api/formats` | GET    | —                             | `{ok, formats: [...]}`           | —     |
| `/api/load`    | POST   | json `{path}` OR multipart    | structure JSON (see below)       | 400, 404, 413, 500 |
| `/api/data`    | GET    | optional `?mtime=<float>`     | structure JSON or "unchanged"    | various |

## Request body cap

`MAX_CONTENT_LENGTH = 50 MiB`.  Realistic SIESTA logs go up to ~10s
of MB; 50 MiB is generous.  Larger bodies → HTTP 413 automatically.

## `/api/formats` response

```json
{
  "ok": true,
  "formats": [
    {"name": "siesta", "label": "SIESTA .out / .log", "hint": "..."},
    {"name": "pyscf",  "label": "PySCF / geomeTRIC trajectory", "hint": "..."}
  ]
}
```

Mirrors `parsers.parser_summary()`.

## `/api/load` modes

### Mode A — JSON path (live-watching)

```json
{ "path": "/abs/path/to/output_file" }
```

Server side:

1. Reject empty path with HTTP 400.
2. Resolve via `os.path.abspath(os.path.expanduser(path))`.
3. Reject non-existent file with HTTP 404.
4. `detect_parser(path)`; reject unsupported with HTTP 400 (the error
   body uses the multi-line message from `parsers/__init__.py`).
5. Replace `_state` (path / parser) atomically; force a re-parse on
   the next refresh.

The detection step happens **before** the new path is committed to
`_state`, so an unsupported file doesn't blank out a working one.

### Mode B — multipart upload (file-picker)

```
Content-Type: multipart/form-data
file=<binary>
```

Server side:

1. Save to `tempfile.gettempdir()` with prefix
   `molwatch_<unix_ts>_<sanitised_basename>` and the original suffix
   preserved (so format-detection's content sniff sees the right
   extension).
2. `detect_parser` on the temp path.  If unsupported, delete the
   temp file and HTTP 400.
3. Clean up any previous upload's temp file.
4. `_state["uploaded"] = True` so /api/data and the front-end know
   not to expect mtime-driven updates.

### Response shape (both modes)

```json
{
  "ok": true,
  "path": "<resolved or temp path>",
  "mtime": <float>,
  "format": "siesta" | "pyscf",
  "label": "<parser label>",
  "data": { /* parser output dict */ },
  "uploaded": true | false,
  "uploaded_filename": "<original name>"   // multipart only
}
```

### Error response

```json
{ "ok": false, "error": "<multi-line human-readable>" }
```

## `/api/data`

Polled by the front-end every ~15 s.  Optional `?mtime=<float>`
short-circuits when nothing changed.

```json
// when mtime unchanged:
{ "ok": true, "changed": false, "mtime": <float> }

// when changed:
{
  "ok":       true,
  "changed":  true,
  "path":     "...",
  "mtime":    <float>,
  "format":   "siesta" | "pyscf",
  "label":    "<parser label>",
  "data":     { /* parser output */ },
  "uploaded": true | false
}
```

If no file is loaded yet, `{"ok": false, "error": "No file loaded yet."}`.

## Concurrency contract

`_refresh_if_changed` snapshots `(path, parser, cached_mtime)` under
`_lock`, **drops the lock during the parse**, then re-acquires
briefly to commit.  Three guarantees:

1. A long parse (multi-MB log) doesn't block other concurrent
   requests for its duration.
2. If a `/api/load` swaps the active file mid-parse, the stale
   parse result is dropped on the floor instead of clobbering the
   new state.  Tested by `test_stale_parse_doesnt_clobber_swapped_state`.
3. Cheap path (mtime unchanged) returns the cached state under a
   short lock — no parse, no I/O.

## Security model (defaults)

* Default bind is `127.0.0.1` (loopback only).
* When `--host` is set to anything other than `127.0.0.1` /
  `localhost` / `::1`, the CLI prints a loud stderr warning that
  `/api/load` reads any local file the server can access.
* Browser CORS provides a default CSRF mitigation: `/api/load`
  requires `Content-Type: application/json` for the path mode, which
  triggers a CORS preflight that the default Flask response (no
  `Access-Control-Allow-Origin` header) fails.  Form-style
  cross-origin POSTs land with the wrong content-type and are
  rejected with "Empty path".  Document this; don't rely on it for
  security if exposing publicly.

## Forbidden patterns

The Flask app must NOT:

1. Hold the global lock during a parse — see "Concurrency contract".
2. Default `--host 0.0.0.0` — that exposes arbitrary file read to
   anyone on the network.
3. Return parser-specific keys outside of `data` — the JSON shape
   is uniform across formats; format-specific fields go inside
   `data` (parser's responsibility).
4. Re-parse on every poll when mtime hasn't changed — the snapshot
   under the lock catches this.
