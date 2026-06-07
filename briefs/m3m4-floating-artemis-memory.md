# M3+M4 — Floating Artemis auto-write conversation + auto-read at prompt build (combined brief)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/m3m4-floating-artemis-memory`
**Browser smoke owner:** Lead, post-merge — open Floating Artemis, send a multi-turn conversation, verify memory drawers land per turn AND the next turn's system prompt includes retrieved memory.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~220 (drawer-write helper + retrieval injection + dedup + tests).
**Priority:** HIGH — Round 2 of memory keystone P4. Combined into one brief because both touch `artemis/floating_artemis/chat.py` and would conflict if split.

---

## Why this exists

Per `docs/memory-audit-2026-05-29.md` findings #D + #E:

> Floating Artemis doesn't auto-write conversations. Every chat turn could become a drawer (verbatim user message + assistant response). It doesn't. The system has no episodic memory of conversations.
>
> Floating Artemis doesn't auto-read at prompt-build time. No retrieval pass before each turn. The assistant has access to memory only when the user explicitly asks via tool.

Jon's primary personal assistant is amnesiac. Today it forgets every conversation the moment the turn ends. M3+M4 fixes both sides in one brief:

- **M3 (auto-write):** every turn writes a drawer with verbatim conversation content
- **M4 (auto-read):** before each turn, retrieval pass injects relevant observations into the system prompt

After M3+M4: Floating Artemis becomes context-aware. It remembers what was discussed. It reasons over what the platform knows. Multi-turn coherence improves materially — Jon experiences the "feels like Claude Code" responsiveness Builder Phase 2 was designed for, on the memory dimension.

---

## Scope

### Part A — M3: Auto-write conversation drawers

In `artemis/floating_artemis/chat.py`, after each turn completes successfully (assistant message persisted), write a memory drawer:

**Drawer shape:**
- `scope`: `(scope_kind="agent", scope_id="floating-artemis")`
- `content`: verbatim turn pair, structured:
  ```
  [USER] {user_message_text}
  [ASSISTANT] {assistant_message_text}
  ```
- `source`: `floating_artemis_message:{user_message_id}` (the user message's primary key, anchoring the turn)

**Implementation:**

```python
# After turn completes in chat.py
from artemis.db import SessionLocal  # per M1's session pattern
from artemis.memory.store import write_drawer, get_or_create_scope
from artemis.memory.schemas import Scope

async def _write_turn_drawer(user_msg_id: int, user_text: str, assistant_text: str) -> None:
    """Write a memory drawer capturing this turn. Failure-isolated."""
    try:
        async with SessionLocal() as session:  # fresh session per M1's surprise note
            scope = await get_or_create_scope(session, "agent", "floating-artemis")
            content = f"[USER] {user_text}\n[ASSISTANT] {assistant_text}"
            await write_drawer(
                session,
                scope=scope,
                content=content,
                source=f"floating_artemis_message:{user_msg_id}",
            )
            await session.commit()
    except Exception:
        logger.warning(
            "Floating Artemis turn-drawer write failed for msg_id=%s",
            user_msg_id,
            exc_info=True,
        )
```

Call this after every turn. **Failure isolation is non-negotiable** — if the drawer write fails, the chat must still work.

### Part B — M3 deferred follow-up (banked, NOT in this brief): consolidation

Every N turns (N=10 default), consolidate the N most recent drawers into a single curated observation. This is the long-term memory layer: drawers = verbatim short-term, observations = summarized long-term. **Not in scope for M3 here.** Banked as M3-B for after we see real conversation data accumulate.

### Part C — M4: Auto-read at prompt build

In `chat.py`, the `_build_system_prompt` function (or wherever the per-turn system prompt is assembled — confirm via grep), add a memory retrieval pass:

**Before each turn:**

1. Take the user's incoming message text as the query
2. Combine with the last 3 turns of conversation as additional context
3. Call `search_observations(scope, query, limit=5)` against scope `agent:floating-artemis`
4. Inject the top-K observations into the system prompt:

```python
# In _build_system_prompt(user_message, conversation_history):
from artemis.memory.retrieval import search_observations
from artemis.memory.schemas import Scope

async def _inject_memory_context(prompt: str, user_msg: str, history: list, db_session) -> str:
    """Inject relevant memory observations into the system prompt."""
    try:
        query = user_msg + "\n" + "\n".join(h.content for h in history[-3:])
        results = await search_observations(
            db_session,
            scope=Scope(scope_kind="agent", scope_id="floating-artemis"),
            query=query,
            limit=5,
        )
        if results:
            memory_block = "\n\n## Recent memory (LLM-curated observations from prior conversations)\n\n"
            memory_block += "These are observations the platform has recorded across past conversations. Use them for continuity but verify before acting on specific claims.\n\n"
            for obs in results:
                memory_block += f"- {obs.content}\n"
            return prompt + memory_block
        return prompt
    except Exception:
        logger.warning("Floating Artemis memory injection failed", exc_info=True)
        return prompt  # graceful degradation
```

Mark the injection with provenance framing (same pattern as H3 + H4): observations are LLM-curated content, not source-of-truth. Floating Artemis must verify before acting on specific claims.

### Part D — Session lifecycle pattern (per M1's surprise)

**Both M3 writes and M4 reads use `SessionLocal()` for memory operations.** This is the cross-cutting pattern M1's Worker surfaced. The chat.py existing db_session may be transaction-scoped for the chat flow; memory operations need their own session to avoid deadlocks.

For reads (M4): if `search_observations` accepts the existing db_session safely (no SAVEPOINT issues), reuse. Otherwise open a fresh SessionLocal.

For writes (M3): always open a fresh SessionLocal per the M1 pattern.

### Part E — Existing read paths NOT replaced

M4 ADDS automatic memory injection. The existing `_query_memory` tool (already in `floating_artemis/tools/core.py`) STAYS — it remains user-callable for explicit queries. M3+M4 are additive auto-grounding; the explicit tool path keeps working.

### Part F — Performance hot-path

`chat.py:_build_system_prompt` runs on every turn. The memory injection adds an extra DB call. Mitigations:

1. **5-second cache** of memory results per session: same session_id + same query within 5s reuses the cached result. Floating Artemis turns are typically <5s apart.
2. **Bounded retrieval limit (5).** Don't fetch more.
3. **Truncate observation content to 500 chars in the injection block.** Long observations cap to a preview.
4. **Async-friendly.** Use `asyncio.create_task` if the memory call can be deferred; otherwise await inline (acceptable: <100ms typical).

### Part G — Tests

`artemis/floating_artemis/tests/test_m3m4_chat_memory.py`:

1. **M3: drawer lands after turn.** Fixture: send a turn. Verify a row in `memory_drawers` with the expected scope + content shape.
2. **M3: idempotency on duplicate turn content.** Send the same turn pair twice (via mocked re-trigger). Verify exactly one drawer row.
3. **M3: failure isolation.** Monkeypatch `write_drawer` to raise. Verify (a) chat completes successfully, (b) assistant_message lands in `floating_artemis_messages` table, (c) warning logged.
4. **M4: memory injection appears in prompt when observations exist.** Fixture: 3 observations in `agent:floating-artemis` scope. Trigger turn. Verify the system prompt sent to the LLM contains "## Recent memory" section + at least one observation.
5. **M4: empty memory doesn't break prompt.** No observations. Trigger turn. Verify prompt assembles without the memory block (or with empty section) — no error.
6. **M4: query-based retrieval narrows.** 5 observations with diverse content. User asks about topic matching only 2. Verify the injection includes those 2, not the unrelated 3.
7. **Cache: same query within 5s reuses result.** Mock retrieval call. Send 2 turns within 5s with same query. Verify retrieval called only once.
8. **End-to-end: 3-turn conversation populates 3 drawers + uses 1st drawer's memory in 2nd turn's prompt.** Smoke that proves the loop closes.

---

## Files owned

- EDIT: `artemis/floating_artemis/chat.py` (turn-completion drawer write + system-prompt memory injection + cache layer)
- POSSIBLE: new `artemis/floating_artemis/memory.py` if helper extraction is cleaner
- NEW: `artemis/floating_artemis/tests/test_m3m4_chat_memory.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/floating_artemis/tests/test_m3m4_chat_memory.py -v` — all 8 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Live smoke (Lead does this post-merge):**
   - Open Floating Artemis chat
   - Send 3 message turns about distinct topics
   - Query `psql -c "SELECT COUNT(*) FROM memory_drawers WHERE scope_id='floating-artemis';"` — expect 3 new rows
   - Send a 4th turn referencing topic from turn 1 — verify the response uses memory from earlier
   - **Paste the SQL output + the 4th turn's response excerpt.**
5. `git diff --stat` + `git log --oneline -1` on `worker/m3m4-floating-artemis-memory`. **Paste.**

---

## Hard constraints

- **Failure isolation is the load-bearing constraint.** Memory failures NEVER break chat. Wrap in try/except, log warning, continue. Tested in #3.
- **SessionLocal pattern.** Per M1's finding, memory operations get their own session to avoid deadlocks. Especially for writes (M3).
- **No schema changes.** Migration 0047 unchanged.
- **Cache the read path.** Memory injection is on a hot loop (every turn). 5-second cache mandatory.
- **Existing tool paths stay.** `_query_memory` and `_write_memory` tools in `floating_artemis/tools/core.py` continue working. M3+M4 is additive auto-grounding, not a replacement.
- **No new visual languages or UI changes.** Backend-only brief.
- **Provenance framing.** Same as H3/H4: memory observations are LLM-curated content with provenance. Inject as "LLM-curated observations from prior conversations" — not as ground truth.
- **Local-only git.** Worker commits on `worker/m3m4-floating-artemis-memory`; terminal-Lead merges after Lead approves.

---

## Coordination with M2 (firing in parallel)

M2 (Builder reads agent memory) and M3+M4 (Floating Artemis memory) touch different files. Zero overlap. Both depend on M1's foundation (which has merged).

After both Round 2 briefs land: Floating Artemis is no longer amnesiac AND Builder reasons over full agent history. Memory becomes a first-class platform substrate that every LLM-driven surface uses.

---

## Report-back format

```
M3+M4 — Floating Artemis memory report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially test #3 failure isolation, #7 cache, #8 end-to-end)
4. Live smoke result — PASTE: drawer count delta after 3 turns + the 4th turn's response showing memory reference
5. SessionLocal pattern adherence — did you open fresh sessions for memory ops?
6. Cache implementation — 5-second window confirmed?
7. check.sh summary
8. Anything surprising — especially around chat.py's existing session lifecycle or interaction with the meeting-summary injection block (H4 territory)
```

---

**Worker: M3+M4 is the moment Floating Artemis becomes context-aware. Combined into one brief because both halves touch the same file. After this lands, Jon's primary personal assistant remembers conversations + uses what the platform knows. The amnesia that made Floating Artemis feel "useful but limited" disappears in one merge.**
