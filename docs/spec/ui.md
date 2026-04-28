# Spec — front-end behaviour

**Modules**: `templates/index.html`, `static/viewer.js`,
`static/style.css` &nbsp;·&nbsp; **Tests**: `tests/test_api_load.py`
(integration via `index_page_has_*` checks)

The HTML page is a single-file dashboard.  No SPA framework, no
build step — vanilla JS + 3Dmol.js + Plotly.

## Page layout

* **Header**: app title `molwatch` + tagline `live trajectory viewer
  — SIESTA · PySCF / geomeTRIC`.  Loader bar with: path input, hidden
  file picker, Load button, status span.
* **Main**: two rows.
  * Row 1 (`.viewer-row`): the 3Dmol viewer + a controls aside
    (Style, Overlays, Playback fieldsets).
  * Row 2 (`.plots-row`): two Plotly canvases (energy vs step, max
    force vs step).
* Mobile breakpoint at 980 px collapses both rows to single column.
  640 px tightens header + plot heights.

## Theme

Dark theme via `:root` CSS variables.  Same palette as molbuilder
(`--bg-page #14171c`, `--accent #6ba6ff`, ...) for visual continuity
between the two tools.  Light theme is one `:root` rewrite away.

3Dmol viewer canvas keeps a **white background** (`#ffffff`) regardless
of the surrounding theme — chemistry viewers conventionally use white
for clarity / publication-readiness.

## Load button — dual mode

The Load button has two behaviours, branching on the path field's
content:

* **Path field has text**: POST `{path}` as JSON to `/api/load`.
  Server reads from disk; the front-end starts a polling timer at
  15 s intervals.  This is the live-watching mode.
* **Path field empty**: trigger the hidden `<input type="file">`.
  When the user picks a file, upload it as `multipart/form-data` to
  `/api/load`.  The path field updates to `(uploaded) <filename>`
  for clarity.  Polling timer is **stopped** because uploaded files
  don't change on disk.

Pressing Enter in the path input triggers Load.

## Polling

* Active polling timer interval: `POLL_MS` (default 15 000).
* Each tick: `GET /api/data?mtime=<state.mtime>`.
* Server-side: if mtime unchanged, returns `{changed: false}` and
  the front-end refreshes only the "Up to date — N frames" status
  text.
* When `data.changed`, `applyNewData(r)` rebuilds the model, frames,
  plots; preserves the user's frame index unless they were sitting
  at the last frame (in which case it advances to the new last
  frame so live-watching feels live).

## State invariants

`state` (a single JS object) holds:

```
state = {
    data:         <last parsed payload | null>,
    mtime:        <float | null>,
    format:       "siesta" | "pyscf" | null,
    label:        "<parser label>" | null,
    currentFrame: <int>,
    pollTimer:    <interval id | null>,
    playTimer:    <interval id | null>,
    firstFit:     <bool>           // re-fit camera on a fresh structure
}
```

* On a successful `/api/load`, `state.data / mtime / format / label`
  are replaced atomically.  Stale FDF / PySCF outputs from a
  previous load are cleared.
* `state.currentFrame` is preserved across refreshes when the user
  has scrubbed away from the end; clamped to the new last frame
  if the trajectory grew.

## Status messages

* Single-line for normal updates: "Loaded N siesta frames — mtime
  HH:MM:SS."
* Multi-line allowed for errors (e.g. unsupported-format hints).
  The status `<span>` has `white-space: pre-line` so newlines in the
  server's error message render correctly.

## Forbidden patterns

The front-end must NOT:

1. Use `innerHTML` for any user-controlled string.  Everything goes
   through `textContent` to prevent XSS via parser output (e.g. a
   malicious filename in `r.uploaded_filename`).
2. Pin the 3Dmol library at `https://3Dmol.org/build/3Dmol-min.js`
   — that URL serves a moving target.  Use the cdnjs pinned URL
   `https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.1.0/3Dmol-min.js`.
3. Continue polling after an upload — uploaded files don't change
   on disk, the timer would burn requests for nothing.
4. Retry a failed `/api/load` automatically.  User clicks Load
   again to retry.
