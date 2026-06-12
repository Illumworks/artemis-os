# Worker Brief — OKR Apply via DB-Backed Breadcrumb Staging (cross-process, subscription-path correct)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-okr-stage-breadcrumb`. Builds on merged reconcile/opener/batch/stage-wrapper work.
Test DB at head (0081). Real DB-backed tests. **Supersedes the chat.py staging-wrapper approach for the
claude-code path — see why below.**

## Root cause (fully verified — read this)
Artemis runs on the **Claude Code subscription adapter** (no API key). On that path tools are served by a
SEPARATE SUBPROCESS: `claude -p --mcp-config` → `python -m artemis.tools.mcp_server --floating-session-id`.
Two facts make layer-3 confirm impossible the way it was attempted:
1. `artemis/tools/mcp_server.py` `build_floating_artemis_tool_set` (~line 346) drops every `entry.layer > 2`
   tool. This is the registry the subprocess actually serves — so `update_okr_kr`/`update_okr_krs` are never
   exposed to her (she ToolSearches and finds nothing). The earlier chat.py `_build_auto_invoke_tool_registry`
   wrapper does NOT fix this — that registry's impls never run on claude-code; it only feeds the adapter's
   `--allowed-tools` allowlist (`providers/claude_code/adapter.py:251`).
2. Even if exposed, the MCP server is a **separate process**. `confirmation_store` is in-memory, so a
   `PendingConfirmation` staged in the subprocess is INVISIBLE to the main process where `route_inbound` /
   `resume_after_confirm` handle Jon's "go". Cross-process confirm state MUST be DB-backed.

So: don't try to make the layer-3 in-process-yield work across the subprocess. Stage in the DB instead, using
the OKR check-in breadcrumb (already DB-backed + per-session) as the carrier.

## The fix

### 1. Migration 0082 — add staged updates to the breadcrumb
Add `staged_updates: JSONB NULL` to `okr_checkin_breadcrumbs` (`artemis/proactivity/models.py`
`OkrCheckinBreadcrumb`). Holds a list of `{kr_id, progress, basis}` the operator has been asked to confirm.
Nullable; default null/empty. (Lossless; no destructive change.)

### 2. New layer-1 tool `stage_okr_updates` (auto-invoke — exposed on the subscription path)
Register in `artemis/floating_artemis/tools/okr.py` `register_okr_tools` at **layer 1** (so
`build_floating_artemis_tool_set` serves it — NOT stripped). Input:
`{"updates": [{"kr_id": int, "progress": number, "basis": str}], "speaker_id": str}`.
Behavior:
- Validate each update against a REAL KR (kr_id exists) and require a non-empty `basis`; drop/!reject
  ungrounded or unknown-KR items (no fabrication). Clamp/validate progress 0-100.
- Write the validated list into the live breadcrumb's `staged_updates` for that speaker/recipient
  (find the active, non-expired, non-completed breadcrumb). **Writes NO KR rows.** This is staging only.
- Return a concise confirmation-request string the model relays, e.g. "Staged 3 KR updates. Ask the operator
  to say 'go' to apply, or 'no' to discard." (No em-dash/emoji/tables.)
- If there's no live breadcrumb (not in a check-in), say so and stage nothing.

### 3. Apply-on-"go" in `route_inbound` (main process — adapter-independent)
Extend the existing conversational-confirm logic in `artemis/routes/integrations_slack_events.py` (the
`confirmation_store.list_for_session` block): BEFORE/alongside it, check whether the speaker has a live
breadcrumb with non-empty `staged_updates`. If so, classify the reply (reuse the existing YES/NO/NEITHER
confirm classifier):
- **YES ("go")** → apply the staged updates SERVER-SIDE: for each, `okr.repository.update_key_result(prog=…)`
  + `okr.repository.create_activity(...)` ("updated via Friday check-in, approved by Jon — basis: …"); commit;
  clear `staged_updates` and mark the breadcrumb completed; post a result message ("Done. KR 9 -> 78, KR 7 ->
  62, KR 11 -> 70."). Do NOT call handle_turn for this — apply directly and post.
- **NO** → clear `staged_updates`, post a brief ack ("Cleared, nothing changed."), leave breadcrumb (or
  complete it). 
- **NEITHER** → fall through to normal handle_turn (staged updates remain until applied/discarded/expired or
  superseded by a new stage call).
- This runs in the MAIN process and reads the DB, so it works regardless of the subprocess adapter.

### 4. Reconcile context — tell her to stage, not to call the (stripped) write tools
Update `_get_okr_reconcile_context` (`chat.py`): map the word-dump to KRs, then call **`stage_okr_updates`**
with the mapped set (it's the supported path; layer-3 `update_okr_kr(s)` are not available on this surface).
Make clear: calling `stage_okr_updates` does NOT apply — it stages and pauses for Jon's "go". Keep cite-the-
words + no-fabrication + topic-change→`complete_okr_checkin`.

## Constraints
- **Approval-first / lossless:** `stage_okr_updates` writes ZERO KR rows; the only KR write is in route_inbound
  on Jon's explicit "go". Every staged update carries a cited basis; unknown-KR/ungrounded items dropped.
- No new deps; ruff + mypy strict; DB-backed tests. Don't regress reconcile/opener/morning-brief/idempotency,
  the existing confirmation_store conversational confirm (web/intercepting path), or routing/channel gate.
- Keep the existing layer-3 `update_okr_kr`/`update_okr_krs` for the intercepting path / ad-hoc use; they're
  just not the check-in path on the subscription. (Don't delete them.)

## Tests
- `stage_okr_updates` writes `staged_updates` to the live breadcrumb and writes NO KR rows; unknown kr_id or
  empty basis is dropped; no live breadcrumb → stages nothing.
- route_inbound: live breadcrumb with staged_updates + "go" → KR rows updated + activity logged + staged
  cleared + breadcrumb completed; "no" → staged cleared, zero KR writes; unrelated → staged intact, normal turn.
- Apply is idempotent-ish: a second "go" after clear does nothing (no double-apply).
- The served floating tool set (`build_floating_artemis_tool_set`) INCLUDES `stage_okr_updates` (layer 1) and
  still EXCLUDES layer-3 tools (proves the staging tool is reachable on the subscription path).
- End-to-end: seed a breadcrumb → stage via tool → apply via route_inbound "go" → KR 7/9/11 prog changed.

## Acceptance
On the subscription path, the Friday round-trip finally closes: word-dump → she calls `stage_okr_updates`
(found + callable, no "not wired") → "I staged KR 9/7/11, say go" → Jon "go" → the KR rows actually change
(and only then), each citing his words. Lead verifies LIVE with Jon and checks the DB rows + activity log.
