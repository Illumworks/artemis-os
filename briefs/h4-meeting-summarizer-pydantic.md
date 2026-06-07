# H4 — Meeting summarizer Pydantic + Floating Artemis read-site revalidation

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/h4-meeting-summarizer-pydantic`
**Browser smoke owner:** Lead, post-merge — trigger a meeting summary (or wait for the next 2-minute granola tick), verify the summary lands with constrained shape; open Floating Artemis chat, verify the system prompt frames summaries as LLM-generated content with provenance.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~150 (Pydantic models + revalidation wrapper + Floating Artemis read-site hardening + tests).
**Priority:** HIGH — closes the meeting→Floating Artemis pollution chain (🔴 surface #3 per `docs/hallucination-audit-2026-05-29.md`). Mirror of H3's shape applied to a different surface.

---

## Why this exists

Per the hallucination audit:

> The meeting summarizer (`artemis/meetings/summarizer.py:273-334`) emits JSON `{bullets: [...], action_items: [{text, owner, due}, ...]}` with `json.loads` only. NO Pydantic shape validation, NO owner format check, NO due-date format check. Written to `meeting_summaries` table verbatim.
>
> **Read back by Floating Artemis** (`chat.py:136-156` via `get_recent_summaries()`) and INJECTED INTO THE SYSTEM PROMPT. A hallucinated action_item ("Jon committed to writing the grant proposal by Friday") becomes durable Floating Artemis context. Jon may be reminded about commitments he never made. Pollution affects every future Floating Artemis turn.

This is the meeting-side analog of H3's trajectory→Builder pollution chain. Same shape, different surface. H4 mirrors H3's design.

Floating Artemis is Jon's primary personal assistant. False action items entering its reasoning context is the highest-impact pollution scenario on the platform — it directly affects what Jon hears about his own commitments.

---

## Scope

### Part A — Pydantic model for meeting summary emission

In `artemis/meetings/summarizer.py` (or new `artemis/meetings/summary_schemas.py`), define:

```python
class ActionItem(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=100)  # name, email, or null
    due: str | None = None  # ISO date, ISO datetime, or null — validated by validator below
    
    @field_validator("due")
    @classmethod
    def validate_due_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Accept ISO date (YYYY-MM-DD), ISO datetime, or pass through "today" / "this week" / "TBD"
        # Reject random free-text dates like "next Tuesday-ish"
        if v.lower() in {"today", "tomorrow", "this week", "next week", "tbd"}:
            return v
        try:
            datetime.fromisoformat(v)
            return v
        except ValueError as exc:
            raise ValueError(f"Invalid due date format: {v!r}. Use ISO 8601 (YYYY-MM-DD) or one of: today, tomorrow, this week, next week, TBD.") from exc
    
    model_config = ConfigDict(extra="forbid")


class MeetingSummary(BaseModel):
    bullets: list[str] = Field(default_factory=list, max_length=20)  # cap at 20 bullets
    action_items: list[ActionItem] = Field(default_factory=list, max_length=15)  # cap at 15 actions
    
    @field_validator("bullets")
    @classmethod
    def validate_bullet_length(cls, v: list[str]) -> list[str]:
        for bullet in v:
            if len(bullet) > 500:
                raise ValueError(f"Bullet exceeds 500 chars: {bullet[:80]!r}...")
            if len(bullet.strip()) == 0:
                raise ValueError("Bullet cannot be empty")
        return v
    
    model_config = ConfigDict(extra="forbid")
```

Replace the bare `json.loads` + dict access at lines 320-334 with:

```python
try:
    raw_text = "".join(b.text for b in response.message.content if isinstance(b, TextBlock))
    summary = MeetingSummary.model_validate_json(raw_text.strip())
    bullets_text = "\n".join(f"- {b}" for b in summary.bullets)
    action_items = [item.model_dump() for item in summary.action_items]
    return bullets_text, action_items
except ValidationError as exc:
    logger.warning("Meeting summarizer validation failed for %r: %s", title, exc)
    return "- Summary unavailable (validation failed)", []
except Exception:
    logger.warning("LLM summarization failed for meeting %r", title, exc_info=True)
    return "- Summary unavailable (LLM call failed)", []
```

### Part B — Retry-on-validation-failure (producer-side recovery)

Same pattern as H2 and H3. On validation failure, retry once with the error appended to the next prompt:

```python
async def _summarize_with_retry(transcript, title, adapter, max_retries=1):
    last_error: str | None = None
    for attempt in range(max_retries + 1):
        prompt = _build_summary_prompt(transcript, title, prior_error=last_error)
        response = await adapter.complete(request)
        raw_text = _extract_text(response)
        try:
            return MeetingSummary.model_validate_json(raw_text.strip())
        except ValidationError as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                logger.warning("Meeting summarizer persistent validation failure for %r: %s", title, exc)
                return MeetingSummary()  # empty default
    return MeetingSummary()
```

If the retry fails twice, persist an empty MeetingSummary (no bullets, no action_items) — the meeting record still lands in `meeting_summaries` with placeholder content, but no hallucinated commitments propagate.

### Part C — Floating Artemis read-site revalidation

In `artemis/floating_artemis/chat.py:120-156` (where `get_recent_summaries()` injects meeting content into the system prompt), reframe summaries as LLM-generated content with provenance:

```python
# In _build_system_prompt or wherever meeting summaries are injected
summary_block = f"""
## Recent meeting summaries (LLM-generated by the meeting summarizer — treat as inferences)

The following summaries were produced by the meeting_summarizer LLM after each meeting.
Bullets and action_items reflect what the analyzer INFERRED from the transcript, not
necessarily verbatim commitments.

Before treating any action_item as a firm commitment by the user or another person:
- For action_items with `owner` set: confirm with the user before acting
- For action_items with `due` set: do not autonomously schedule reminders without user approval
- If asked about a specific meeting decision, retrieve the raw transcript via granola tools
  rather than trusting the summary alone

{formatted_summaries}
"""
```

This is verbal framing — Floating Artemis's LLM is sensitive to "these are inferences" cues in its system prompt.

### Part D — Provenance marker on `get_recent_summaries` return

The function in `chat.py` that fetches meeting summaries for injection should return them with explicit provenance fields:

```python
[
  {
    "meeting_id": ...,
    "title": "...",
    "bullets": [...],
    "action_items": [...],
    "provenance": {
      "source": "llm_meeting_summarizer",
      "generated_at": "<iso>",
      "model": "<adapter_id>",
      "transcript_truncated_at_chars": 6000,  # surface that the source was capped
    }
  }
]
```

Floating Artemis sees the provenance and can reason about reliability.

### Part E — Action-item shape audit + cleanup

The Worker should audit existing `meeting_summaries.action_items` rows for shape violations under the new Pydantic model. If existing rows would fail the new model:

1. Don't migrate or modify existing rows (lossless invariant)
2. DO add a `validate_existing()` helper that flags rows that wouldn't validate today — produce a report (paste in the Worker's response) showing how many existing rows have unparseable due dates, invalid owners, etc.
3. Future writes use the strict model; existing rows continue to be readable but flagged with `provenance.legacy_format = True` when read

### Part F — Tests

`artemis/meetings/tests/test_h4_meeting_summarizer_pydantic.py`:

1. **Valid summary passes Pydantic.** Real fixture (or hand-built) summary with bullets + action_items passes.
2. **Invalid due format is rejected.** Action item with `due="next Tuesday-ish"` triggers validation failure.
3. **Empty bullet is rejected.** Bullets list contains `""` triggers failure.
4. **Extra field rejected.** Action item with `hallucinated_field` triggers failure.
5. **Bullet over 500 chars triggers failure.** Long bullet rejected.
6. **Validation failure triggers retry.** Mock adapter to return invalid JSON first, valid second. Verify second result lands.
7. **Persistent failure produces empty summary.** Mock adapter to always return invalid. Verify `meeting_summaries` row has empty bullets + empty action_items, with warning logged.
8. **Floating Artemis read-site provenance.** Mock `get_recent_summaries()` call. Verify return includes provenance block.
9. **Existing-rows audit.** Run the `validate_existing()` helper. Report count of rows that wouldn't pass new validation.

---

## Files owned

- NEW or EDIT: `artemis/meetings/summary_schemas.py` (Pydantic models)
- EDIT: `artemis/meetings/summarizer.py` (use Pydantic; retry helper)
- EDIT: `artemis/floating_artemis/chat.py` (provenance framing in system prompt; provenance metadata in `get_recent_summaries` return shape)
- NEW: `artemis/meetings/tests/test_h4_meeting_summarizer_pydantic.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047` unchanged. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/meetings/tests/test_h4_meeting_summarizer_pydantic.py -v` — all 9 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt failures. **Paste.**
4. **Existing rows audit result.** PASTE the count of existing `meeting_summaries.action_items` rows that would NOT validate under the new model. (Expect a number — 0 is unlikely if action_items have been emitted with `null` for missing fields.)
5. **Live smoke (Lead does this post-merge):**
   - Trigger a meeting summarizer tick (or wait for next 2-min schedule)
   - Inspect new `meeting_summaries` rows — verify shape matches Pydantic model
   - Open Floating Artemis chat (via API or UI) — verify the system prompt response includes the "LLM-generated meeting summaries — treat as inferences" framing
   - **Paste the validation evidence.**
6. `git diff --stat` + `git log --oneline -1` on `worker/h4-meeting-summarizer-pydantic`. **Paste.**

---

## Hard constraints

- **Lossless invariant on existing rows.** Don't modify or migrate existing `meeting_summaries.action_items` rows. They get a `legacy_format` marker on read but stay verbatim in DB.
- **Failure isolation.** Validation failure → placeholder summary, never an exception that breaks the granola summarizer tick. The granola integration is real-time-sensitive (2-min ticks); H4 must not break it.
- **Floating Artemis read path is hot.** `get_recent_summaries()` is called on every chat turn. Don't add latency. The provenance marker is a cheap metadata addition, not a re-fetch.
- **Reuse H3's helper if present.** If H3 lands first and exposes a shared `validate_llm_json_emission` (per H2's banked observation), H4 should use it. If H4 lands first, factor the helper out so H3 (or its already-merged version) can adopt it later.
- **Don't change the meeting summarizer's LLM prompt.** Same constraint as H3 — H4 enforces shape, doesn't change instruction.
- **Local-only git.** Worker commits on `worker/h4-meeting-summarizer-pydantic`; terminal-Lead merges after Lead approves.

---

## Report-back format

```
H4 — Meeting summarizer Pydantic + Floating Artemis revalidation report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially test #6 retry, test #8 provenance, test #9 existing-rows audit)
4. Existing rows audit — count of rows that wouldn't pass new validation
5. Live smoke results — PASTE the system_prompt framing change from Floating Artemis and a new meeting_summaries row with constrained shape
6. Helper extraction — used shared validate_llm_json_emission? Or factored own?
7. Retry-on-validation behavior — single-retry capped?
8. check.sh summary
9. Anything surprising — especially how existing meeting_summaries rows interact with the new read provenance, and any latency observations from the chat.py path
```

---

**Worker: H4 closes the meeting → Floating Artemis pollution chain — the highest-impact hallucination scenario on the platform (Jon's personal assistant reasoning over false commitments he didn't make). Same architectural shape as H3. After H4 + H1 + H2 + H3 all merge, every LLM-emitted JSON in the platform is bounded by Pydantic; every consuming LLM has explicit provenance framing. The "no hallucinations" invariant becomes structurally enforceable.**
