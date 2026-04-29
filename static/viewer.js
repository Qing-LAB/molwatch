/* SIESTA live viewer -- 3Dmol viewport + Plotly traces.
 *
 * Frames are loaded once into a 3Dmol "movie" model (addModelsAsFrames)
 * and the slider / playback simply calls viewer.setFrame(idx), which is
 * fast (no DOM rebuild).  When the server reports a new mtime we rebuild
 * the model with the fresh frame list.
 *
 * Polling cadence: ~15 s (CG steps from SIESTA on a real workload take
 * far longer than that, so the network traffic is negligible).
 */

(function () {
    "use strict";

    const POLL_MS = 15000;

    const $ = (id) => document.getElementById(id);

    /* ------------------------------------------------------------------ */
    /*  State                                                              */
    /* ------------------------------------------------------------------ */

    const state = {
        mtime: null,
        data: null,            // {frames, lattice, iterations, energies, max_forces, forces}
        currentFrame: 0,
        pollTimer: null,
        playTimer: null,
        firstFit: true,        // call viewer.zoomTo() once after first load
        // Separate buckets so toggling one overlay doesn't clobber another.
        cellShapes:  [],
        forceShapes: [],
        indexLabels: [],
    };

    const viewer = $3Dmol.createViewer("viewer", {
        backgroundColor: "white",
        defaultcolors: $3Dmol.elementColors.Jmol,
    });

    /* ------------------------------------------------------------------ */
    /*  Status banner                                                      */
    /* ------------------------------------------------------------------ */

    function setStatus(msg, kind) {
        const el = $("status");
        el.textContent = msg;
        el.className = "status" + (kind ? " " + kind : "");
    }

    /* ------------------------------------------------------------------ */
    /*  3Dmol rendering                                                    */
    /* ------------------------------------------------------------------ */

    function framesToMultiXyz(frames) {
        const out = [];
        for (const frame of frames) {
            out.push(String(frame.length));
            out.push("");
            for (const atom of frame) {
                const [sym, x, y, z] = atom;
                out.push(
                    sym + " " +
                    x.toFixed(6) + " " +
                    y.toFixed(6) + " " +
                    z.toFixed(6)
                );
            }
        }
        return out.join("\n");
    }

    /* In 3Dmol.js a sphere `scale` value is multiplied by the element's
     * van-der-Waals radius, so per-element size differences only become
     * visible at a non-tiny scale.  Defaults below are tuned so that
     * Au / S / C / H look visibly different in every mode that draws
     * atoms.  The user's "radius scale" slider multiplies these. */
    function styleSpec() {
        const rep = $("rep").value;
        const scale = parseFloat($("radius").value) || 1.0;
        const cs = $("colorscheme").value;

        switch (rep) {
            case "sphere":
                // True CPK: full vdW radius per element.
                return { sphere: { scale: 1.0 * scale, colorscheme: cs } };
            case "line":
                return { line: { linewidth: 1 + 2 * scale, colorscheme: cs } };
            case "ballstick":
                // Balls scale with vdW radius, sticks are a fixed thickness.
                return {
                    stick:  { radius: 0.12 * scale,             colorscheme: cs },
                    sphere: { scale:  0.32 * scale,             colorscheme: cs },
                };
            case "stick":
            default:
                // Plain licorice has no per-element size; tack on tiny
                // spheres so the user can still tell Au from H.
                return {
                    stick:  { radius: 0.16 * scale,             colorscheme: cs },
                    sphere: { scale:  0.18 * scale,             colorscheme: cs },
                };
        }
    }

    function applyStyle() {
        viewer.setStyle({}, styleSpec());
        viewer.render();
    }

    function drawCell() {
        // Remove only the cell shapes -- leave force arrows alone.
        for (const s of state.cellShapes) viewer.removeShape(s);
        state.cellShapes = [];

        if (!$("show-cell").checked) {
            viewer.render();
            return;
        }
        const lat = state.data && state.data.lattice;
        if (!lat || lat.length !== 3) {
            viewer.render();
            return;
        }

        const [a, b, c] = lat;
        const corner = (i, j, k) => ({
            x: i * a[0] + j * b[0] + k * c[0],
            y: i * a[1] + j * b[1] + k * c[1],
            z: i * a[2] + j * b[2] + k * c[2],
        });

        const edges = [
            // edges along a
            [[0, 0, 0], [1, 0, 0]],
            [[0, 1, 0], [1, 1, 0]],
            [[0, 0, 1], [1, 0, 1]],
            [[0, 1, 1], [1, 1, 1]],
            // edges along b
            [[0, 0, 0], [0, 1, 0]],
            [[1, 0, 0], [1, 1, 0]],
            [[0, 0, 1], [0, 1, 1]],
            [[1, 0, 1], [1, 1, 1]],
            // edges along c
            [[0, 0, 0], [0, 0, 1]],
            [[1, 0, 0], [1, 0, 1]],
            [[0, 1, 0], [0, 1, 1]],
            [[1, 1, 0], [1, 1, 1]],
        ];

        for (const [u, v] of edges) {
            const s = viewer.addCylinder({
                start:  corner(u[0], u[1], u[2]),
                end:    corner(v[0], v[1], v[2]),
                radius: 0.04,
                color:  0x888888,
                fromCap: 1,
                toCap:   1,
            });
            state.cellShapes.push(s);
        }
        viewer.render();
    }

    /* ------------------------------------------------------------------ */
    /*  Atom-index labels                                                  */
    /* ------------------------------------------------------------------ */

    function drawIndices() {
        for (const l of state.indexLabels) viewer.removeLabel(l);
        state.indexLabels = [];

        if (!$("show-indices").checked) {
            viewer.render();
            return;
        }
        if (!state.data) return;

        const frame = state.data.frames[state.currentFrame];
        if (!frame) return;

        for (let i = 0; i < frame.length; i++) {
            const a = frame[i];
            const lbl = viewer.addLabel(String(i + 1), {
                position:          { x: a[1], y: a[2], z: a[3] },
                backgroundColor:   "black",
                backgroundOpacity: 0.55,
                fontColor:         "white",
                fontSize:          9,
                inFront:           true,
                showBackground:    true,
                alignment:         "centerCenter",
            });
            state.indexLabels.push(lbl);
        }
        viewer.render();
    }

    /* ------------------------------------------------------------------ */
    /*  Force-vector arrows                                                */
    /* ------------------------------------------------------------------ */

    /* `viewer.addArrow` has historically been unreliable across 3Dmol
     * releases (sometimes it silently fails to render).  Instead we
     * assemble each arrow from primitives that we know work in this
     * app (the unit-cell box uses the same `addCylinder` call):
     *   - one cylinder for the shaft;
     *   - a stack of CONE_SEGS cylinders with linearly decreasing
     *     radius, which approximates a true cone for the arrowhead.
     * With ~6 segments the staircase is invisible at typical zoom. */
    const HEAD_FRAC = 0.30;     // last 30% of the arrow length is the head
    const CONE_SEGS = 6;        // radial slices in the cone (more = smoother)
    function drawForces() {
        for (const s of state.forceShapes) viewer.removeShape(s);
        state.forceShapes = [];

        if (!$("show-forces").checked) {
            viewer.render();
            return;
        }
        if (!state.data) { viewer.render(); return; }

        const frame  = state.data.frames[state.currentFrame];
        const forces = state.data.forces && state.data.forces[state.currentFrame];
        if (!frame || !forces || !forces.length) {
            // Helpful diagnostic for when forces aren't shipped for this step
            // (typical for an in-flight CG step that hasn't written its force
            // block yet).
            console.info(
                "[viewer] no per-atom forces for frame",
                state.currentFrame,
                "(forces array length =", (forces || []).length, ")"
            );
            viewer.render();
            return;
        }

        const fscale    = parseFloat($("force-scale").value) || 1.0;
        const fmin      = parseFloat($("force-min").value)   || 0.0;
        const highlight = $("highlight-max").checked;

        const mags = forces.map(([fx, fy, fz]) =>
            Math.sqrt(fx * fx + fy * fy + fz * fz));

        let maxMag = 0, maxIdx = -1;
        for (let i = 0; i < mags.length; i++) {
            if (mags[i] > maxMag) { maxMag = mags[i]; maxIdx = i; }
        }

        let drawn = 0;

        for (let i = 0; i < frame.length && i < forces.length; i++) {
            const a   = frame[i];
            const f   = forces[i];
            const mag = mags[i];
            if (mag < fmin) continue;

            // Colour:
            //   - the largest force (highlight) is gold so it pops;
            //   - others go from dim red to bright orange-red by magnitude.
            let color;
            if (highlight && i === maxIdx) {
                color = 0xffc400;          // gold
            } else {
                const t = maxMag > 0 ? mag / maxMag : 0;
                const r = Math.floor(170 + 85 * t);
                const g = Math.floor( 40 + 60 * t);
                color = (r << 16) | (g << 8) | 0x20;
            }

            const sx = a[1], sy = a[2], sz = a[3];
            const ex = sx + f[0] * fscale;
            const ey = sy + f[1] * fscale;
            const ez = sz + f[2] * fscale;
            // Linear interpolation along the arrow axis.
            const lerp = (t) => ({
                x: sx + (ex - sx) * t,
                y: sy + (ey - sy) * t,
                z: sz + (ez - sz) * t,
            });

            const baseR = 0.05 + 0.04 * (maxMag > 0 ? mag / maxMag : 0);
            const headR = baseR * 2.6;
            const tShaft = 1 - HEAD_FRAC;

            // --- shaft ---
            state.forceShapes.push(viewer.addCylinder({
                start:   { x: sx, y: sy, z: sz },
                end:     lerp(tShaft),
                radius:  baseR,
                color:   color,
                fromCap: 1, toCap: 1,
            }));
            // --- arrowhead: stack of N cylinders tapering to zero ---
            // Segment k spans t1->t2 with radius taken at the midpoint of
            // the linear taper (headR at the base, 0 at the tip).
            for (let k = 0; k < CONE_SEGS; k++) {
                const t1   = tShaft + HEAD_FRAC * (k    ) / CONE_SEGS;
                const t2   = tShaft + HEAD_FRAC * (k + 1) / CONE_SEGS;
                const tmid = (k + 0.5) / CONE_SEGS;
                const r    = headR * (1 - tmid);
                if (r < 0.005) continue;       // skip the vanishing tip
                state.forceShapes.push(viewer.addCylinder({
                    start:   lerp(t1),
                    end:     lerp(t2),
                    radius:  r,
                    color:   color,
                    fromCap: 1, toCap: 1,
                }));
            }
            drawn++;
        }

        console.info(
            "[viewer] drew", drawn, "force arrows on frame",
            state.currentFrame,
            "(maxMag =", maxMag.toFixed(3), "eV/\u00C5 at atom", maxIdx + 1, ")"
        );
        viewer.render();
    }

    function rebuildModel() {
        viewer.removeAllModels();
        if (!state.data || !state.data.frames.length) {
            viewer.render();
            return;
        }
        viewer.addModelsAsFrames(framesToMultiXyz(state.data.frames), "xyz");
        applyStyle();
        drawCell();
        if (state.firstFit) {
            viewer.zoomTo();
            state.firstFit = false;
        }
    }

    function showFrame(idx) {
        if (!state.data || !state.data.frames.length) return;
        const n = state.data.frames.length;
        idx = Math.max(0, Math.min(n - 1, idx));
        state.currentFrame = idx;
        viewer.setFrame(idx);
        // Labels and force arrows are not animated by 3Dmol's frame system,
        // so we redraw them whenever the active frame changes.  Each of
        // these calls also issues viewer.render(), so no extra render here.
        drawIndices();
        drawForces();
        $("frame-idx").textContent = idx;
        $("frame-slider").value = idx;
    }

    /* ------------------------------------------------------------------ */
    /*  Plotly traces                                                      */
    /* ------------------------------------------------------------------ */

    function makePlots() {
        if (!state.data) return;
        const x = state.data.iterations;

        Plotly.react("energy-plot", [{
            x: x,
            y: state.data.energies,
            mode: "lines+markers",
            line: { color: "#1f77b4", width: 1.5 },
            marker: { size: 6 },
            name: "E_KS",
            connectgaps: false,
        }], {
            title: { text: "Total energy", font: { size: 13 } },
            margin: { l: 80, r: 16, t: 36, b: 44 },
            xaxis: { title: "CG step", dtick: 1, zeroline: false },
            yaxis: { title: "E_KS (eV)", tickformat: ".4f", zeroline: false },
            font: { family: "system-ui, sans-serif", size: 11 },
        }, { displayModeBar: false, responsive: true });

        Plotly.react("force-plot", [{
            x: x,
            y: state.data.max_forces,
            mode: "lines+markers",
            line: { color: "#d62728", width: 1.5 },
            marker: { size: 6 },
            name: "Max |F|",
            connectgaps: false,
        }], {
            title: { text: "Max force", font: { size: 13 } },
            margin: { l: 70, r: 16, t: 36, b: 44 },
            xaxis: { title: "CG step", dtick: 1, zeroline: false },
            yaxis: { title: "Max |F| (eV/\u00C5)", rangemode: "tozero", zeroline: false },
            font: { family: "system-ui, sans-serif", size: 11 },
        }, { displayModeBar: false, responsive: true });

        renderScfProgress();
    }

    /*  Render the SCF-iteration progress for the most recent step.
     *  Engine-agnostic: works for PySCF (gnorm/ddm) and SIESTA
     *  (dHmax/dDmax) by checking which keys are present in the
     *  per-cycle dicts.  Hidden when scf_history is empty (e.g.
     *  PySCF without its .log alongside the trajectory, or any
     *  format that doesn't surface per-cycle SCF detail).
     */
    function renderScfProgress() {
        const section = $("scf-section");
        const history = state.data && state.data.scf_history;
        if (!history || history.length === 0) {
            section.hidden = true;
            return;
        }
        section.hidden = false;

        // The most recent SCF run = the step currently converging
        // (or just converged).
        const current  = history[history.length - 1];
        const stepIdx  = history.length - 1;
        const cycles   = current.map(c => c.cycle);
        const energies = current.map(c => c.energy);

        // Pick the residual: prefer PySCF's |g|, fall back to
        // SIESTA's dHmax.  Both decrease toward 0 during convergence
        // and look natural on a log y-axis.
        let residual, residualName, residualUnit, residualColor;
        if (current[0].gnorm !== undefined) {
            residual      = current.map(c => c.gnorm);
            residualName  = "|g|";
            residualUnit  = "eV/Å";
            residualColor = "#fbbf24";   // amber
        } else if (current[0].dHmax !== undefined) {
            residual      = current.map(c => c.dHmax);
            residualName  = "dHmax";
            residualUnit  = "eV";
            residualColor = "#4ade80";   // green
        } else {
            residual = null;
        }

        // Status banner: step, current cycle, residual, ΔE.  Engine
        // label distinguishes "Geom-opt step" (PySCF) from "CG/MD step"
        // (SIESTA) for clarity.
        const stepLabel = (state.format === "siesta")
            ? "CG/MD step" : "Geom-opt step";
        const lastDe = current[current.length - 1].delta_E;
        let statusText = stepLabel + " " + stepIdx
            + " — SCF cycle " + cycles[cycles.length - 1]
            + " (" + current.length + " iters)";
        if (residual !== null) {
            const lastResid = residual[residual.length - 1];
            statusText += ", " + residualName + "="
                       + lastResid.toExponential(2) + " " + residualUnit;
        }
        statusText += ", ΔE=" + lastDe.toExponential(2) + " eV";
        $("scf-status").textContent = statusText;

        // SCF energy convergence within the current step.
        Plotly.react("scf-energy-plot", [{
            x: cycles,
            y: energies,
            mode: "lines+markers",
            line: { color: "#6ba6ff", width: 1.5 },
            marker: { size: 5 },
            name: "E",
        }], {
            title: { text: "SCF energy (current step)", font: { size: 12 } },
            margin: { l: 80, r: 16, t: 32, b: 40 },
            xaxis: { title: "SCF cycle", dtick: 1, zeroline: false },
            yaxis: { title: "E (eV)", tickformat: ".4f", zeroline: false },
            font: { family: "system-ui, sans-serif", size: 10 },
        }, { displayModeBar: false, responsive: true });

        // Residual on log y-axis -- spans many decades during SCF.
        const resPlotEl = $("scf-gnorm-plot");
        if (residual !== null) {
            resPlotEl.style.display = "";
            Plotly.react("scf-gnorm-plot", [{
                x: cycles,
                y: residual,
                mode: "lines+markers",
                line: { color: residualColor, width: 1.5 },
                marker: { size: 5 },
                name: residualName,
            }], {
                title: { text: "SCF residual " + residualName,
                         font: { size: 12 } },
                margin: { l: 70, r: 16, t: 32, b: 40 },
                xaxis: { title: "SCF cycle", dtick: 1, zeroline: false },
                yaxis: { title: residualName + " (" + residualUnit + ")",
                         type: "log", zeroline: false, tickformat: ".0e" },
                font: { family: "system-ui, sans-serif", size: 10 },
            }, { displayModeBar: false, responsive: true });
        } else {
            resPlotEl.style.display = "none";
        }
    }

    /* ------------------------------------------------------------------ */
    /*  Polling                                                            */
    /* ------------------------------------------------------------------ */

    async function pollOnce() {
        try {
            const url = state.mtime !== null
                ? "/api/data?mtime=" + encodeURIComponent(state.mtime)
                : "/api/data";
            const r = await fetch(url).then(x => x.json());
            if (!r.ok) {
                setStatus(r.error || "Server error.", "error");
                return;
            }
            if (!r.changed) {
                setStatus(
                    "Up to date \u2014 " + state.data.frames.length
                    + " " + (state.label || "") + " frames "
                    + "(checked " + new Date().toLocaleTimeString() + ").",
                    "ok"
                );
                return;
            }
            applyNewData(r);
        } catch (e) {
            setStatus("Network error: " + e.message, "error");
        }
    }

    function applyNewData(r) {
        const wasAtEnd = !state.data
            || state.currentFrame >= state.data.frames.length - 1;

        state.mtime  = r.mtime;
        state.data   = r.data;
        state.format = r.format || (r.data && r.data.source_format) || "?";
        state.label  = r.label  || state.format;

        const n = state.data.frames.length;
        if (n === 0) {
            setStatus("File loaded ("+ state.label +") but no frames yet.", "");
            return;
        }
        $("frame-tot").textContent = n - 1;
        $("frame-slider").max      = n - 1;

        rebuildModel();
        const targetIdx = wasAtEnd ? n - 1 : Math.min(state.currentFrame, n - 1);
        showFrame(targetIdx);
        makePlots();

        const ts = new Date(r.mtime * 1000).toLocaleTimeString();
        setStatus(
            "Loaded " + n + " " + state.label + " frames \u2014 mtime " + ts + ".",
            "ok"
        );
    }

    function startPolling() {
        if (state.pollTimer) clearInterval(state.pollTimer);
        state.pollTimer = setInterval(pollOnce, POLL_MS);
    }

    function stopPolling() {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
        }
    }

    /* ------------------------------------------------------------------ */
    /*  Playback                                                           */
    /* ------------------------------------------------------------------ */

    function step(delta) {
        if (!state.data || !state.data.frames.length) return;
        const n = state.data.frames.length;
        let next = state.currentFrame + delta;
        if (next >= n) next = $("loop").checked ? 0     : n - 1;
        if (next < 0)  next = $("loop").checked ? n - 1 : 0;
        showFrame(next);
    }

    function play() {
        if (state.playTimer) clearInterval(state.playTimer);
        const speed = parseInt($("speed").value, 10) || 150;
        state.playTimer = setInterval(() => step(1), speed);
    }

    function pause() {
        if (state.playTimer) {
            clearInterval(state.playTimer);
            state.playTimer = null;
        }
    }

    /* ------------------------------------------------------------------ */
    /*  UI wiring                                                          */
    /* ------------------------------------------------------------------ */

    /*  Load button has two behaviours:
     *    - path field has text -> POST {path:...} as JSON  (live watch)
     *    - path field empty    -> open the hidden file picker;
     *                              the picker's change handler uploads
     *                              the file as multipart and the server
     *                              parses it once (no polling).
     */
    $("load-btn").addEventListener("click", () => {
        const path = $("path-input").value.trim();
        if (path) {
            loadByPath(path);
        } else {
            $("file-picker").click();   // open the native dialog
        }
    });

    $("file-picker").addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        pause();
        setStatus("Uploading " + file.name + "\u2026", "");
        const fd = new FormData();
        fd.append("file", file);
        try {
            const r = await fetch("/api/load", { method: "POST", body: fd })
                            .then(x => x.json());
            if (!r.ok) {
                setStatus(r.error || "Upload failed.", "error");
                return;
            }
            // Reflect the upload in the path field so it's clear what
            // was loaded; prefix tells the user it isn't being polled.
            $("path-input").value = "(uploaded) " + file.name;
            state.firstFit = true;
            applyNewData({
                mtime:  r.mtime,
                data:   r.data,
                format: r.format,
                label:  r.label,
            });
            // Uploaded files are one-shot; skip the polling timer
            // because the temp file's mtime never advances.
            if (r.uploaded) {
                stopPolling();
            } else {
                startPolling();
            }
        } catch (err) {
            setStatus("Network error: " + err.message, "error");
        } finally {
            // Reset so picking the same file again still fires "change".
            e.target.value = "";
        }
    });

    async function loadByPath(path) {
        pause();
        setStatus("Loading\u2026", "");
        try {
            const r = await fetch("/api/load", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: path }),
            }).then(x => x.json());

            if (!r.ok) {
                setStatus(r.error || "Load failed.", "error");
                return;
            }
            state.firstFit = true;
            applyNewData({
                mtime:  r.mtime,
                data:   r.data,
                format: r.format,
                label:  r.label,
            });
            startPolling();
        } catch (e) {
            setStatus("Network error: " + e.message, "error");
        }
    }

    $("path-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") $("load-btn").click();
    });

    $("frame-slider").addEventListener("input", (e) => {
        showFrame(parseInt(e.target.value, 10));
    });

    $("rep").addEventListener("change", applyStyle);
    $("radius").addEventListener("input", applyStyle);
    $("colorscheme").addEventListener("change", applyStyle);
    $("show-cell").addEventListener("change", drawCell);
    $("bg").addEventListener("change", (e) => {
        viewer.setBackgroundColor(e.target.value);
        viewer.render();
    });

    /* Overlays */
    $("show-indices").addEventListener("change", drawIndices);
    $("show-forces").addEventListener("change",  drawForces);
    $("force-scale").addEventListener("input", (e) => {
        $("force-scale-val").textContent = parseFloat(e.target.value).toFixed(1);
        drawForces();
    });
    $("force-min").addEventListener("input",      drawForces);
    $("highlight-max").addEventListener("change", drawForces);

    $("play").addEventListener("click",  play);
    $("pause").addEventListener("click", pause);
    $("prev").addEventListener("click",  () => step(-1));
    $("next").addEventListener("click",  () => step(1));
    $("speed").addEventListener("change", () => {
        if (state.playTimer) play();    // restart with new cadence
    });

    /* ---- Tabs (Style / Overlays / Playback) ---------------------- */
    /* Compact controls: only one panel visible at a time so the
       aside never outgrows the viewer height.  CSS does the visibility
       toggle via `is-active`; we just sync the classes here. */
    document.querySelectorAll(".ctab").forEach((btn) => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.tab;
            document.querySelectorAll(".ctab").forEach(
                (b) => b.classList.toggle("is-active", b === btn)
            );
            document.querySelectorAll(".ctab-panel").forEach(
                (p) => p.classList.toggle(
                    "is-active", p.dataset.panel === target
                )
            );
        });
    });

    /* ---- Re-fit 3Dmol on viewport resize ------------------------- */
    /* The viewer height is `clamp(360px, 52vh, 500px)` -- it changes
       when the user resizes the window or rotates a tablet.  3Dmol
       caches canvas size at init, so we have to nudge it to refresh
       its WebGL viewport whenever the container size actually changes;
       debounce with rAF to avoid storming during drag. */
    let _resizeRAF = 0;
    const _onResize = () => {
        cancelAnimationFrame(_resizeRAF);
        _resizeRAF = requestAnimationFrame(() => {
            if (viewer && typeof viewer.resize === "function") {
                viewer.resize();
                viewer.render();
            }
        });
    };
    window.addEventListener("resize", _onResize, { passive: true });
})();
