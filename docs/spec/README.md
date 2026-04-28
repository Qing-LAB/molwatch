# molwatch — design specification

Companion repository to [molbuilder](https://github.com/Qing-LAB/molbuilder).
Where molbuilder produces input for SIESTA / PySCF / future quantum-
chemistry codes, molwatch tails their **output** files while the
calculation is still running and renders the live trajectory.

This directory is the contract for the package.  The same rule
applies as in molbuilder: tests must derive from the spec, not from
the implementation.  See molbuilder's
[`docs/spec/README.md`](https://github.com/Qing-LAB/molbuilder/blob/main/docs/spec/README.md)
for the rationale.

## What molwatch does

* Watches a quantum-chemistry output file by polling its mtime
  every ~15 s (configurable).  When the mtime advances, the parser
  re-reads the file and the front-end refreshes.
* Auto-detects which parser to use based on the file's content
  (not just extension).
* Renders frames in a 3Dmol.js viewer + plots energy and max-force
  per step in Plotly traces.
* Supports two load modes: live-watching (typed absolute path) and
  one-shot upload (file picker).

## Design goals

1. **Format-agnostic**.  Adding a new code (NWChem, ORCA, Gaussian,
   OpenMM, ...) is one new file under `parsers/` and one entry in
   the registry.  The Flask app and JS never reference any specific
   format by name.

2. **Live-friendly**.  The parser has to tolerate files that are
   still being written: torn frames at EOF are dropped, partial
   step data round-trips cleanly to JSON `null`, the viewer
   re-renders without flicker on incremental updates.

3. **Single-user, single-tab**.  No login system, no per-session
   state.  A plain dict + lock holds the active file.  Multi-user
   deployments are explicitly out of scope; the README documents
   the network-exposure risk and recommends ssh-tunnels or a
   reverse-proxy with auth.

4. **Cross-format unit consistency**.  Every parser returns energies
   in eV and forces in eV/Å (per-atom force magnitude, NOT max
   gradient component).  The plot axes are unit-consistent across
   formats; switching between SIESTA and PySCF runs doesn't change
   what the y-axis means.

5. **Helpful errors**.  An unsupported file produces a multi-line
   error that lists every supported format with a one-line hint
   AND, where possible, a targeted suggestion of which file the
   user probably meant ("Looks like a PySCF run — the streaming
   trajectory is `<job>_geom_optim.xyz`, not `.log`").

## Spec index

| spec | covers |
| --- | --- |
| [`parsers.md`](parsers.md)   | TrajectoryParser plug-in interface; SIESTA + PySCF parser specifics |
| [`api.md`](api.md)           | Flask endpoints + request/response shapes |
| [`ui.md`](ui.md)             | front-end behaviour: live watch vs one-shot upload, theme, polling |

## Versioning

Spec changes that change the JSON shape returned by `/api/load` or
`/api/data` are **major-version** breaking changes (front-end has to
follow).  New optional fields in the response are patch-level.  New
parsers are patch-level.
