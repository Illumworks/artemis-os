# PIPE2 Polish — Pan + Edge Render Fix + Performance

**Owner:** Sonnet Worker (canvas interaction work — judgment-heavy)
**Branch:** `worker/pipe2-polish-pan-perf-edge-render`
**LOC budget:** ~400 (estimate; honest overrun OK up to ~520)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE2 merged (canvas surface + node rendering + edge rendering exist).
**Grounded in:** Jon's 2026-05-21 walkthrough — three issues:
1. Canvas feels sluggish
2. No pan (middle-mouse drag) — only zoom available
3. Edges visually break when zoomed out AND dragging a node (return on zoom back to 100%)

## Why this brief exists

PIPE2 shipped the canvas substrate but three real UX gaps surfaced in walkthrough. None are functional regressions (canvas saves, edges persist, nodes render) — but they make the canvas feel half-built. This patch closes them so the canvas feels like a finished tool.

## Scope

### In scope

1. **Pan via middle-mouse drag** (primary), with secondary support for space+drag:
   - **Middle-mouse drag on canvas (empty area or any node):** start pan; cursor changes to grabbing; canvas translates with mouse movement; release → end pan
   - **Space+left-drag** as alternative: hold spacebar, mouse cursor changes to grabbing, left-mouse drag pans
   - **Two-finger scroll on trackpad:** also pans (deltaX/deltaY without ctrl-key triggers translate, not zoom — typical n8n / Figma convention)
   - Pan persists between zoom operations — zoom out, pan around, zoom in: position preserved
   - Pan state stored in canvas component, NOT persisted to DB (panning the view doesn't change pipeline data)
   - **Edge case:** if mid-drag of a node when middle-mouse is pressed, the node drag wins. Pan starts only on empty-canvas or node-but-without-active-node-drag.

2. **Edge rendering fix — break-on-zoom-and-drag bug:**
   - Diagnosis: edges are rendered as SVG paths whose coordinates come from `node.position`. When zoom is applied (CSS transform on the canvas container), the SVG coordinate space may not transform correctly, OR the edge re-render uses untransformed positions while nodes use transformed positions, causing visual disconnect.
   - **Fix path 1 (preferred):** ensure edges and nodes share the same coordinate transform. The SVG overlay should be wrapped in the same transform container as the nodes. When zoom changes, both transform together; edges follow nodes pixel-for-pixel.
   - **Fix path 2 (fallback if path 1 has performance issues):** recompute edge path coordinates from `node.position` × zoom factor on every node drag event, NOT just on zoom change. Adds compute but guarantees alignment.
   - **Test:** zoom to 50%, drag a node, watch edges — they MUST track the node continuously, not detach and snap back on zoom-in.

3. **Performance optimization** — diagnose and fix sluggish feel:
   - **Profile first:** open DevTools Performance, record a 5-second session of opening canvas + dragging a node + creating an edge. Identify the bottleneck.
   - **Likely culprits (in order of probability):**
     - **Full canvas re-render on every node drag:** if the canvas does a full DOM rebuild on every mousemove event during drag, that's the bottleneck. Fix: only update the dragged node's `transform` property + the SVG paths of edges connected to it. Other nodes stay untouched.
     - **No event throttling:** mousemove fires 60+ times per second; if every event triggers a state update + render, the main thread chokes. Fix: throttle the position update via requestAnimationFrame.
     - **SVG path recomputation O(n) per drag event:** if every drag event recomputes all 23 edge paths even when only 2-3 are connected to the dragged node, that's O(n) when it should be O(connected-edges). Fix: index edges by source/target node_id and only recompute the connected subset.
   - **Specific target:** dragging a node in the 16-node marketing pipeline should feel as smooth as moving a window. Not a stutter, not a delay, not a lurch.

4. **Tests:**
   - Pan via middle-mouse: drag empty canvas → canvas translates → release → state stays
   - Pan via space+drag: hold space, drag → same behavior
   - Pan via two-finger scroll (manual smoke; hard to automate)
   - Edge tracking at 50% zoom: drag node → edges follow continuously (visual test or computed snapshot)
   - Performance smoke: 16-node pipeline drag for 3 seconds → frame rate stays ≥ 50fps (use `performance.now()` deltas in test if needed)
   - Pan + zoom independence: pan to (200, 200), zoom to 75%, pan to (-100, -100), zoom to 125% → final view is correct

### Out of scope

- Touch gestures (pinch-zoom, two-finger pan on touchscreens). Desktop trackpad/mouse only.
- Save pan position to DB. View state is per-session.
- Mini-map / overview corner. Defer.
- Snap-to-grid for node positions. Defer.
- Multi-node selection + drag. Defer.
- Keyboard arrow-key pan. Defer (low-value vs mouse pan).

## Invariants

1. **Pan doesn't modify node positions.** Pan is view-only; the saved pipeline JSON has the same `node.position` values before and after pan.
2. **Zoom + pan compose cleanly.** No coordinate-space bugs where zoom out, pan, zoom in lose track of where things are.
3. **Edge tracking is exact** — at any zoom level, when a node moves, the edges connected to it MUST follow without visual delay or detachment.
4. **No new dependencies.** Vanilla JS + existing patterns only.
5. **No regression** in PIPE2's existing save / node-drag / edge-create / palette / config-drawer behaviors.

## Files expected

| File | LOC |
|---|---|
| `public/js/components/pipeline-canvas.js` | ~200 delta (pan logic, coordinate transform fix for edges, performance optimization) |
| `public/css/features/pipelines.css` | ~30 delta (cursor states for pan: default vs grabbing) |
| `tests/unit/frontend/test_pipeline_canvas_polish.py` (new or appended) | ~80 |

**Total: ~310 LOC.** Cap at 520. Performance fix could be the wildcard — if profiling reveals a deeper issue (e.g., needs DOM virtualization for 100+ nodes), STOP and ping Lead before going past 500.

## Test plan

1. **Middle-mouse pan:** middle-drag on canvas → translates; release → stays.
2. **Space+drag pan:** hold space + left-drag → same behavior.
3. **Two-finger scroll pan:** trackpad gesture → translates (manual smoke).
4. **Edge tracking at 50% zoom:** zoom to 50%, drag a node, edges follow continuously. Repeat at 25% and 200%.
5. **Performance — 16-node drag smoothness:** open marketing pipeline canvas, drag any node for 3+ seconds, frame rate stays at ~60fps. Test with Performance.now() or visual smoke.
6. **Pan + zoom compose:** sequence of pan + zoom operations end in the expected view position.
7. **Existing PIPE2 features still work:** save, edge-create, palette-drag, config-drawer.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (Python tests only)
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new console errors; PIPE2 canvas saves still persist

## What "done" looks like

1. Pan via middle-mouse, space+drag, and trackpad two-finger all work.
2. Edges track nodes pixel-accurately at any zoom level.
3. 16-node drag feels smooth (no perceptible lag).
4. Existing PIPE2 features unchanged.
5. Tests pass.
6. `check.sh` passes within exempt set.

## Report Worker submits

1. `git diff --stat` output.
2. Performance profiling note: BEFORE (what was the bottleneck) and AFTER (frame rate during 16-node drag).
3. Description of the edge-render fix: which path (1 — shared transform or 2 — recompute on drag) was used, and why.
4. Screenshots or screencap of: pan working, edges tracking at 50% zoom, smooth drag.
5. Test pass count.
6. Branch.

---

**Lead notes (not for Worker):**
- This is canvas polish, not architectural change. The PIPE2 substrate (data model, save flow, node/edge rendering) is solid; this brief just makes it feel finished.
- If performance profiling reveals something deeper than the three likely culprits (e.g., the SVG overlay itself needs replacing with canvas-rendered edges), STOP and ping Lead. That's a bigger architectural call than this brief.
- After this lands, PIPE2 + this polish = a credible n8n-style canvas. Then PIPE3 (per-type config forms) + PIPE4 (execution engine) bring it to feature-complete.
