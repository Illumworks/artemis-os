# Pipeline AI Assistant Panel — Inline AI Help in Canvas

**Owner:** Sonnet Worker (significant UX + Builder integration work)
**Branch:** `worker/pipeline-ai-assistant-panel`
**LOC budget:** ~700 (honest overrun OK to ~900)
**Depends on:** PIPE2 + PIPE2 polish + PIPE3 + all PIPE3 patches merged. Reuses O1 Builder infrastructure (SSE streaming, resolve_adapter cascade, trajectory summaries).

## Why

Pipelines are visual graphs in a canvas. Editing a complex pipeline (16+ nodes for marketing) is cognitively heavy. Users need AI assistance — but pulling the user OUT of the canvas (like the Agent-Builder does for agents) breaks flow.

**Right shape: AI Assistant lives INSIDE the canvas as a collapsible side panel.** User stays in the visual editing context; AI proposes changes that appear on the canvas as suggestions (ghosted nodes with Accept/Reject affordances).

This is closer to Figma's AI assistant or Cursor's chat sidebar than to a separate Builder page.

## Scope

### UI layout in the canvas

Pipeline canvas currently has:
- Top toolbar (Save / Run / View JSON / Zoom controls)
- Left palette (drag node types to canvas)
- Center canvas (the graph)
- Right config drawer (opens on node click)

**Add:** collapsible 4th panel — "AI Assistant" — on the right side. Toggles via a small chat-icon button in the top toolbar. When open:
- Panel takes ~340px on the right (canvas reflows)
- Chat surface at top (scrollable conversation history)
- Input box at bottom ("Ask AI...")
- Conversation messages: user messages (right-aligned), AI messages (left-aligned with the Artemis icon)
- Token-by-token streaming via existing O1 SSE infrastructure
- Persistent across canvas sessions (conversation saved per pipeline_id)

When closed: tiny floating chat icon in bottom-right corner of canvas. Click to expand.

### What the AI can do

The AI Assistant operates on the current pipeline's nodes + edges. Capabilities (in scope for v1):

1. **Propose new nodes:**
   - User: "Add a scheduled trigger that fires every Monday at 9am"
   - AI: "Sure, I'll add a `trigger_scheduled` node with cron `0 9 * * 1` and connect it to your starbridge_researcher."
   - Canvas shows a GHOST node at a sensible position with an Accept / Reject affordance
   - On Accept: node is committed to the pipeline JSON; AI's message updates with "Added."

2. **Propose edge changes:**
   - User: "Route signals from Regional News directly to Brief Composer if urgency is hot"
   - AI: "I'll add a conditional node between them that branches on urgency. Here's the proposal..."
   - Canvas shows ghost conditional + new edges; Accept / Reject

3. **Propose config tweaks:**
   - User: "The cron is too frequent; change all scout schedules to daily"
   - AI: "I'll update the trigger node's cron from `0 */4 * * *` to `0 9 * * *`. Affects: trigger_scheduled."
   - Affected nodes get a highlight ring; Accept / Reject

4. **Explain the pipeline:**
   - User: "What does this pipeline do?"
   - AI: "This is the marketing pipeline. It runs every 4 hours: scouts query 9 data sources, the qualifier rates each signal against rulesets, Josh + Angela approve at Gate 1, then the content team builds drafts that ship to Writing Studio."
   - Text-only response; no canvas changes

5. **Self-improvement (read-only suggestions from past runs):**
   - On panel open, AI proactively scans last 5 pipeline_runs for this pipeline
   - If patterns detected: "Your last 5 runs failed at gate_1_signals_inbox because Angela's timeout expired. Want me to bump the timeout from 72h to 168h?"
   - Same Accept / Reject affordance

### Architecture

Reuses O1's builder infrastructure heavily:

- **Backend:** new route `POST /api/pipelines/{id}/assistant/turn` (SSE stream)
  - Reuses `artemis/builder/agent_builder.py` for turn handling (or extracts a base class if needed)
  - System prompt includes the current pipeline's JSON + recent runs summary
  - Streaming response includes structured proposals (`PROPOSAL_BEGIN <json> PROPOSAL_END` tokens)
  - Trajectory summary saved per pipeline_id

- **Frontend:** new component `public/js/components/pipeline-ai-panel.js`
  - Mounts inside canvas, communicates with backend via SSE
  - Parses proposals from stream, renders ghost nodes/edges on canvas
  - Accept/Reject buttons apply changes to canvas state, save to backend

- **Proposal schema:** JSON shape that describes a pipeline modification:
  ```json
  {
    "kind": "add_node" | "remove_node" | "add_edge" | "remove_edge" | "update_node_config",
    "payload": { ... },
    "explanation": "Human-readable why"
  }
  ```

### Tests

- Backend turn endpoint streams SSE with structured proposals
- Frontend parses proposal stream, renders ghost on canvas
- Accept applies the proposal to pipeline JSON + PATCHes the backend
- Reject discards
- Self-improvement loop fires on panel open, scans runs, surfaces 0-N suggestions
- Conversation persists per pipeline_id (reload preserves history)

### Out of scope

- Multi-turn agent reasoning (e.g., "let me first ask you a question"). v1 is one-shot proposal per user message.
- AI editing of node config drawer fields (only structural changes — nodes/edges/configs). UI-level interactions like "drag this node to a different position" are out.
- Voice input. Text only.
- Collaborative AI (multiple users sharing the conversation). Single-operator system.
- AI proposing changes to OTHER pipelines (sub-pipeline references stay manual).
- Branching conversations (forks of the chat thread). Single linear conversation per pipeline.
- AI suggesting connector setup ("you need a Starbridge connector"). Defer to Agent Card warnings (Connectors brief handles).

## Files expected

| File | LOC |
|---|---|
| `artemis/pipelines/assistant/__init__.py` | ~5 |
| `artemis/pipelines/assistant/turn_handler.py` (reuses O1 patterns) | ~150 |
| `artemis/pipelines/assistant/proposals.py` (schemas + apply logic) | ~120 |
| `artemis/pipelines/routes.py` (new SSE turn endpoint) | ~50 delta |
| `public/js/components/pipeline-ai-panel.js` (new) | ~250 |
| `public/js/components/pipeline-canvas.js` (ghost node rendering) | ~80 delta |
| `public/css/features/pipelines.css` (AI panel + ghost styles) | ~70 delta |
| Tests | ~120 |

**Total: ~845 LOC.** Cap 1000. Honest budget for a significant feature. Heavy on JS + new backend module.

## Invariants

- Reuses O1 Builder's SSE streaming + adapter cascade — DO NOT duplicate; extend the existing infrastructure
- AI proposals never apply automatically — explicit user Accept required
- Canvas state remains the source of truth; AI suggestions never silently mutate
- Conversation history is per pipeline_id, saved server-side
- node --check on all modified JS
- conftest hard-fail on non-test DB
- ./scripts/check.sh passes within exempt set
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, proposal schema definition, screenshot of AI panel open with a ghost node proposal visible on canvas, screenshot of self-improvement suggestion on panel open, test pass count, branch.

---

**Lead notes (not for Worker):**
- The "AI panel inside the canvas" framing comes from Jon's instinct that pulling the AI out of the workflow context (like the Agent-Builder does for agents) breaks flow. Agents are listed/edited in a separate Builder; pipelines are already in a canvas → AI assistance lives there.
- This brief uses O1 infrastructure for the streaming + trajectory summary pattern. Worker should look at `artemis/builder/agent_builder.py` to understand the conventions before writing the pipeline turn handler. If extracting a common base class makes sense, do it; otherwise duplicate the pattern with clear "see agent_builder.py for parallel pattern" comment.
