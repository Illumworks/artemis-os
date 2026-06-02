# PIPE2 — Visual Canvas (n8n-style editor with Artemis design language)

**Owner:** Sonnet Worker (NOT Codex — significant UI judgment + interaction design)
**Branch:** `worker/pipe2-visual-canvas`
**LOC budget:** ~1800 (estimate; honest overrun OK up to ~2400 — canvas + drag-drop + edge routing are LOC-heavy)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE1 merged (pipelines table + CRUD routes + node/edge JSONB shape stable).
**Grounded in:** PIPE1's `PipelineNode` + `PipelineEdge` TypedDicts, D6 + D6.1 in Master Plan, Artemis design language (fluidity, simplicity, purposefulness, naturalness, spacious, open).

## Why this brief exists

PIPE1 ships the data model with a placeholder JSON editor. PIPE2 replaces it with the **visual canvas** — the actual editing surface users will spend time in. This is the n8n mental model (draggable nodes, connectable edges, click-to-configure) executed with Artemis's design language (spacious, breathable, deliberate — not n8n's utilitarian density).

After PIPE2: users can visually compose a pipeline by dragging agent nodes onto a canvas, connecting them with edges, and editing each node's config inline. The JSON editor stays as a power-user fallback (toggle button to switch). Marketing pipeline (seeded via PIPE5) renders as a real graph on first load.

## Scope

### In scope

1. **Canvas surface** — replaces the JSON editor as the default view when a pipeline is opened for editing. JSON editor remains accessible via a "View JSON" toggle (small button top-right of canvas).

2. **Node rendering**:
   - Each node is a card on the canvas at `position: {x, y}` (from PIPE1 schema).
   - Card size: ~180px wide × ~80px tall. Compact but readable.
   - Card content: icon (left), label (top), node type subtitle (bottom, muted text), config-summary (one line if space, e.g., "agent: starbridge_researcher" or "every 4h").
   - Color/border treatment differs by node type:
     - `trigger_*` → subtle accent border (indicates entry point)
     - `agent_invocation` → default border
     - `human_gate` → warning-tone border (indicates pause point)
     - `conditional` → diamond shape OR distinct border treatment
     - `sub_pipeline` → bolder border + sub-graph icon
   - Selected node: lifted shadow + accent border
   - Hovered node: subtle highlight
   - Errored node (last run failed at this step): red dot indicator top-right

3. **Edge rendering**:
   - Smooth Bezier curves between nodes (NOT straight lines — feels mechanical).
   - Edges originate from a small "out" port on the right side of source node, terminate at "in" port on the left side of target node.
   - Edge color: muted default; accent on hover; status-tinted when a recent run flowed through it (green for success, red for failure — but only when run_state data is available; PIPE2 may not have it yet, so default to muted).
   - Arrowhead at target end.
   - Click on edge → opens edge inspector (small popover or side drawer) showing data_shape mapping (placeholder until PIPE3 wires real schemas).

4. **Interaction model**:
   - **Drag a node** to reposition. Persists position to `nodes[i].position` on save.
   - **Click a node** → opens config drawer on the right side (replaces the agent detail panel pattern). PIPE2 provides the drawer shell + generic JSON config view; PIPE3 fills in per-type config forms.
   - **Click empty canvas** → deselects all.
   - **Drag from a node's "out" port** → starts edge drag. Drop on target node's "in" port to create edge. Drop on empty canvas → cancels.
   - **Click an existing edge** → highlights + offers Delete affordance (small × button on the edge at midpoint, or in the inspector drawer).
   - **Right-click on canvas** → context menu: "Add node…" with submenu by type (agent_invocation, human_gate, conditional, etc.). Adding a node places it at the click position.
   - **Right-click on a node** → context menu: Edit / Duplicate / Delete.
   - **Cmd/Ctrl+S** → save (debounced; same as clicking explicit Save button).
   - **Cmd/Ctrl+Z / Y** → undo / redo (in-canvas only; not crossing save boundaries).

5. **Canvas controls (toolbar):**
   - **Save** — PATCH `/api/pipelines/{id}` with current nodes + edges + positions
   - **Run** — same as PIPE1 (records intent; PIPE4 will wire execution)
   - **View JSON** — toggle to PIPE1's raw JSON editor (preserves edits across toggles)
   - **Zoom** — slider or +/− buttons, 25% to 200%. Default 100%.
   - **Fit to view** — auto-zoom to fit all nodes
   - **Layout: Auto** — one-click auto-layout (simple top-to-bottom or left-to-right algorithm; Worker picks the simplest viable, dagre is the obvious library reference but inline implementation is fine for graph sizes < 50 nodes)

6. **Node palette** — left rail (collapsible) showing available node types as draggable cards:
   - Trigger (with submenu: Manual, Scheduled, Webhook, Event)
   - Agent (with searchable list of all seeded agents — uses the existing `/api/agents` endpoint)
   - Skill (with searchable list — uses `/api/skills`)
   - Human Gate
   - Conditional
   - Sub-Pipeline (with searchable list of other pipelines from `/api/pipelines`)
   - Drag from palette → drop onto canvas → creates a new node at drop position with sensible defaults.

7. **Config drawer (right side, opens on node click):**
   - Header: node label (editable inline), node type subtitle, delete affordance
   - Body: generic JSON config view (textarea + parse-on-save) for PIPE2. PIPE3 ships per-type forms (agent picker, cron picker, approver picker, etc.) that replace the generic JSON view.
   - Footer: Save / Cancel
   - Closes on outside click or ESC.

8. **Empty state** — when a pipeline has zero nodes:
   - Canvas shows a centered prompt: "This pipeline is empty. Drag a node from the left palette to get started." with a subtle illustration or just the prompt text.
   - Palette is open by default.

9. **Marketing pipeline rendering** — when user opens the seeded marketing pipeline:
   - All 16 nodes render at their seeded `position` coordinates (PIPE5 hand-placed them in a sensible layout).
   - 23 edges render as Bezier curves.
   - User can immediately see the structure: trigger top, scouts in a row, qualifier→brief composer→Gate 1→content team flowing down.

10. **Tests:**
    - Frontend integration: render a 3-node pipeline, drag a node, verify position updates in state.
    - Edge creation: drag from out-port to in-port, verify edge added to state.
    - Edge deletion via click → Delete: verify edge removed.
    - Node deletion: verify node + all attached edges removed.
    - Save: PATCH fires with current state.
    - View JSON toggle: state preserved both directions.
    - Marketing pipeline opens, all 16 nodes visible, all 23 edges visible.

### Out of scope

- Per-node-type config forms (PIPE3 ships those — for PIPE2, all node configs are raw JSON in the drawer).
- Execution wiring — Run button still records intent only (PIPE4 wires real execution).
- Sub-pipeline recursive rendering. A sub_pipeline node shows the referenced pipeline's name + a "Open" button that switches the canvas to that pipeline. No inline expansion.
- Webhook URL generation for webhook trigger nodes (PIPE3 or later).
- Collaborative editing (multiple users editing the same pipeline). Single-operator system.
- Version history of canvas state. PIPE1's pipelines table doesn't track versions; future feature.
- Export to image / PDF. Future.
- Mini-map (small overview in corner). Defer to later if pipelines grow large.
- Pan via mouse drag on empty canvas (just zoom for v1; pan via scroll/arrow keys if needed).

## Visual + interaction design notes

- **Spacious, not dense.** n8n packs information; Artemis breathes. Node cards have generous padding. Default zoom feels comfortable, not cramped.
- **Smooth, not snappy.** Drag feels weighted. Edge curves are gentle. Transitions on hover are 150ms ease-out, not 50ms.
- **Tokens only.** Use existing `--surface-*`, `--text-*`, `--accent`, `--warning`, `--success`. No new hex.
- **Light DOM.** Per CLAUDE.md. The canvas is a `<div>` with absolutely-positioned children; nodes are individual elements, edges are SVG paths. No Shadow DOM.
- **Performance** — 16 nodes + 23 edges renders instantly. 100 nodes should still feel smooth. If performance flags, add virtualization for nodes outside viewport (defer if not needed at v1 scale).

## Architecture

Two new modules:

- **`public/js/components/pipeline-canvas.js`** — the canvas surface, ~700 LOC. Handles render, drag, edge creation, zoom, layout.
- **`public/js/components/pipeline-node-card.js`** — single node rendering, ~200 LOC. Type-aware visual treatment.
- **`public/js/components/pipeline-palette.js`** — left rail palette, ~250 LOC. Draggable node-type cards + agent/skill/pipeline searchable lists.
- **`public/js/components/pipeline-config-drawer.js`** — right-side config drawer, ~150 LOC. Generic JSON config view (PIPE3 replaces).
- **`public/js/features/pipelines.js`** — wires the canvas into the Pipelines list page when a pipeline is opened, ~100 LOC delta.

Edges rendered as inline SVG: one `<svg>` overlay on the canvas with `<path d="M..."/>` per edge.

Drag-and-drop: native HTML5 drag-and-drop API for the palette → canvas drop, custom mouse-event handlers for node drag and edge creation (HTML5 D&D is too coarse for these).

State management: keep canvas state in a local module-level store; on Save, write to backend. No new state library — vanilla object + dispatch pattern matching the rest of the app.

## Files expected (honest estimate)

| File | LOC |
|---|---|
| `public/js/components/pipeline-canvas.js` (new) | ~700 |
| `public/js/components/pipeline-node-card.js` (new) | ~200 |
| `public/js/components/pipeline-palette.js` (new) | ~250 |
| `public/js/components/pipeline-config-drawer.js` (new) | ~150 |
| `public/js/features/pipelines.js` | ~100 delta (wire canvas into list page) |
| `public/css/features/pipelines.css` | ~350 delta (canvas, nodes, edges, palette, drawer) |
| Tests in `tests/unit/frontend/` | ~150 |

**Total: ~1900 LOC.** This is honest — the canvas surface is the bulk; node cards + palette + drawer round out the experience. If you find yourself heading materially over 2400, stop and ping Lead with the structural reason.

## Test plan

1. **Render empty canvas** → empty state + open palette.
2. **Drag agent from palette** → node appears at drop position.
3. **Drag existing node** → position updates in state.
4. **Drag from out-port** → edge follows cursor; drop on in-port → edge persists.
5. **Click edge** → inspector or delete affordance appears.
6. **Delete node** → node + connected edges removed.
7. **Open marketing pipeline** → 16 nodes + 23 edges render correctly at seeded positions.
8. **Save** → PATCH fires with current nodes/edges/positions. Reload → state restored.
9. **View JSON toggle** → JSON editor shows current canvas state; edit JSON, toggle back → canvas reflects.
10. **Zoom controls** → in/out works; fit-to-view re-centers.
11. **Auto-layout** → reorganizes nodes into reasonable positions.
12. **Performance smoke** → 100 mock nodes render without lag.

## Invariants Worker must NOT regress

- LOC budget calibrated honestly per session pattern. STOP if you're heading materially over the estimate (~30%+) with a structural reason, ping Lead. Otherwise proceed.
- conftest hard-fail on non-test DB (Python tests only)
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set before declaring done
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new console errors; existing Pipelines list + JSON editor still work
- The JSON shape on disk (PIPE1's `PipelineNode` + `PipelineEdge` TypedDicts) is canonical — Worker MUST round-trip without losing data. If a node has fields the canvas doesn't render, preserve them in JSONB on save.

## What "done" looks like

1. Pipelines page opens with the canvas as default view.
2. Marketing pipeline renders as a connected 16-node graph at first load.
3. Node drag + edge creation + node config (generic JSON drawer for v1) all work.
4. Palette → canvas drop adds nodes.
5. Save persists, reload restores.
6. JSON editor toggle works as a fallback view.
7. Visual feel matches Artemis design language (spacious, fluid, deliberate).
8. Tests pass.
9. `check.sh` passes within exempt set.

## Report Worker submits

1. `git diff --stat` output.
2. Screenshots: empty canvas, palette open, marketing pipeline rendered, node config drawer open, JSON toggle.
3. Performance note: render time for 16-node pipeline + responsiveness on drag/edge-create.
4. Test pass count.
5. Browser console clean check (paste any new warnings if present).
6. Branch + worktree path.
7. Any visual judgment call that wasn't pre-specified (e.g., specific edge curve algorithm, specific layout algorithm if you used one) — flag for Lead post-merge review.

---

**Lead notes (not for Worker):**
- This is the big eye-candy landing of the wave. After PIPE2, users SEE what Artemis OS is — a visual orchestration layer. Demoability shoots up.
- The config drawer being generic JSON in PIPE2 is deliberate — PIPE3 ships per-type forms (agent picker, cron picker, etc.) that replace the JSON view. Don't try to do PIPE3 inside PIPE2; the canvas itself is enough work.
- Worker has UI judgment latitude: edge curve specifics, auto-layout algorithm, palette card design — all open. Just stay within Artemis design tokens.
- If you find a really clean canvas library that's pure-JS and small (no React, no Vue), feel free to use it. But default is vanilla — the rest of the app is vanilla, no need to import a framework just for this. Inline SVG + mouse events get you 95% of n8n's polish.
