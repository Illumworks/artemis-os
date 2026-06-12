# Worker Brief — Make Gated (layer-3/4) Tools Work on the Claude Code Subscription Path (stage-and-confirm)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-gated-tools-claude-code`. Test DB at head (0081). Real DB-backed tests.
**This is a CORE-PATH change** (affects every Floating Artemis tool call on the Claude Code adapter). Be precise.

## Root cause (verified)
Artemis has NO Anthropic API key — she runs on the **Claude Code subscription adapter** (`ClaudeCodeAdapter`;
the provider default chain in `chat.py` `_resolve_adapter` is `claude-code → codex → lm-studio → anthropic`,
and the `claude` CLI is present, so claude-code wins). On that path, `handle_turn` builds tools via
`_build_auto_invoke_tool_registry`, which **DROPS every layer>2 tool** (`if entry.layer > 2: continue`) because
the MCP subprocess can't surface the in-process confirmation yield. Result: `update_okr_kr`, `update_okr_krs`,
and ALL gated tools are **absent from her toolset in Slack** — she correctly reports "the write tool isn't
wired on this MCP server." NOT a hallucination. The whole propose→confirm design assumed the *intercepting*
(Anthropic) path; on the subscription path nothing ever creates a `PendingConfirmation`, so the conversational
"go" we already built has nothing to resolve.

## The fix — stage-and-confirm wrapper (no API key needed)
In `_build_auto_invoke_tool_registry` (`artemis/floating_artemis/chat.py` ~1151), STOP dropping layer>2 tools.
Instead register each with a **staging wrapper** that, when the model calls it:
1. Builds a `PendingConfirmation(session_id=session_id, tool_use_id=<id>, tool_name=entry.tool.name,
   tool_input=inp, layer=entry.layer)` and `confirmation_store.add(pending)`. **Mirror the intercepting
   wrapper** (`_build_intercepting_tool_registry`, ~1126-1147) for the field shape.
   - `tool_use_id`: use `floating_tool_use_id_var.get()` if set; otherwise generate a `uuid4().hex`
     (server-side Python — `uuid` is fine). route_inbound resolves via `list_for_session` then the pending's
     own `tool_use_id`, so a generated id is safe.
2. **Does NOT call `entry.impl`** — no write, ever, here. (This is the approval-first guarantee.)
3. **RETURNS a tool-result string** (does NOT raise — the subprocess can't suspend). Word it so the model
   RELAYS to the operator and waits, e.g.: *"STAGED — not yet applied. This action needs the operator's
   confirmation. Tell them plainly what will change and ask them to reply 'go' to apply, or 'no' to cancel."*
   The model paraphrases this to Jon; do NOT leak the literal "STAGED" token to Slack (the model rewrites it;
   keep agent-lint clean — no em-dash/emoji/tables in what reaches Jon).

Keep layer<=2 behavior EXACTLY as-is (including the `query_memory` MemoryReadEvent emit wrapper).

## How the loop closes (already built — confirm, don't rebuild)
- The model calls e.g. `update_okr_krs` → staging wrapper creates the pending + returns the staged message →
  the model tells Jon "I've proposed KR 9→78, 7→62, 11→70, say go" → turn ends normally.
- Jon replies "go" → `routes/integrations_slack_events.py` conversational confirm (already merged):
  `confirmation_store.list_for_session(session_id)` finds the pending → classifier YES →
  `resume_after_confirm(session_id, pending.tool_use_id, "run")`. `resume_after_confirm` (chat.py ~939)
  builds its OWN full registry and executes `entry.impl(pending.tool_input)` **directly** (adapter-independent)
  → the KRs actually write + activity logs. "no" → cancel, no write. Unrelated reply → pending untouched.
- VERIFY this end-to-end in tests; do not modify resume_after_confirm or the route_inbound confirm logic unless
  a real defect is found (if so, flag it to Lead, don't silently change core confirm behavior).

## Constraints / blast radius
- **Approval-first preserved:** the staging wrapper NEVER executes the impl; writes happen ONLY via
  `resume_after_confirm` on Jon's explicit "go". No fabrication.
- **Intercepting (web/Anthropic) path UNCHANGED:** `_build_intercepting_tool_registry` still raises
  `_PendingConfirmationError` and suspends. Only the auto-invoke (claude-code) registry changes.
- **Layer<=2 unchanged** on both paths (auto-invoke still runs immediately).
- No new deps (uuid is stdlib); ruff + mypy strict; DB-backed tests. Don't regress P1/C2 routing, the channel
  gate, the OKR reconcile/opener/batch work, morning brief, or idempotency.

## Tests
- Auto-invoke registry: calling a layer-3 tool (e.g. `update_okr_krs`) **creates a pending in
  confirmation_store**, **returns the staged message**, and **does NOT call the underlying impl** (assert no
  DB write / impl not invoked). This is the regression that proves the live bug is fixed.
- A layer-1/2 tool on the auto-invoke registry still executes immediately (unchanged).
- Full round-trip: stage `update_okr_krs` (2-3 KRs) → `resume_after_confirm("run")` → all KR rows updated +
  activity entries logged; `resume_after_confirm("cancel")` → zero writes.
- A non-OKR layer-3 tool also stages (proves the fix is general, not OKR-specific).
- Intercepting registry: a layer-3 tool still raises `_PendingConfirmationError` (web path not regressed).
- route_inbound: pending staged + "go" → write happens; + "no" → cancel; + unrelated → pending intact, normal
  turn (reuse existing confirm tests; extend if needed).

## Acceptance
On the Claude Code subscription path, Artemis can CALL a gated tool: it stages a pending (no write), she asks
Jon to say "go", and his "go" performs the real write via the existing confirm flow. The Friday OKR round-trip
finally completes end to end: word-dump → she calls `update_okr_krs` → "I'll set KR 9/7/11, say go" → Jon
"go" → KR rows actually change (and only then). Lead verifies live with Jon + checks the DB rows + activity log.
