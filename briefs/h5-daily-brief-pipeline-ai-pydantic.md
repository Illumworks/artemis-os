# H5 — Daily Brief + Pipeline AI Panel anti-hallucination completion

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/h5-daily-brief-pipeline-ai-pydantic`
**Browser smoke owner:** Lead, post-merge — trigger a daily brief generation, verify the JSON shape passes Pydantic; open Pipeline AI Panel, send a conversational message, verify the proposal JSON passes Pydantic.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~180 (2 Pydantic models + retry helpers + schema enforcement at 2 emit sites + tests).
**Priority:** HIGH — closes the H1-H4 anti-hallucination stream. After H5, every LLM-emitting JSON surface in the platform has Pydantic validation + retry-on-failure + provenance markers. The "no hallucinations" invariant becomes structurally enforced across every emit site.

---

## Why this exists

Per `docs/hallucination-audit-2026-05-29.md` MEDIUM-risk surfaces #6 (Daily Brief generator) and #7 (Pipeline AI Panel) — the last two unprotected JSON-emitting LLM surfaces in the platform.

**Current state:**

| Surface | Risk | Validation today |
|---|---|---|
| Daily Brief generator (`artemis/brief/generator.py:55-86`) | 🟡 MEDIUM | regex JSON extract → `json.loads` → DB insert. No Pydantic. |
| Pipeline AI Panel (`artemis/pipelines/assistant/turn_handler.py:290-330`) | 🟡 MEDIUM | regex extract PROPOSAL_BEGIN...PROPOSAL_END → JSON decode. No Pydantic. |

**Post-H5:**
- Both surfaces emit JSON validated against a strict Pydantic model
- Validation failure triggers single-retry-with-error-in-context (same pattern as H2/H3/H4)
- Provenance framing applied to outputs that other LLMs may consume (same pattern as H3/H4)
- Self-teaching error messages from H1's foundation when validation rejects

After H5: the "no hallucinations" invariant Jon stated (2026-05-29) is enforced at every LLM-emit site in the platform. No JSON output from any LLM can become durable data without passing strict shape validation first.

---

## Scope

### Part A — Daily Brief Pydantic + retry

**File:** `artemis/brief/generator.py`

**Current state (around lines 55-86):** LLM is prompted for a JSON brief, `result` is regex-stripped for a code-fence-wrapped JSON, then `json.loads(brief_text)` → `brief_dict`, then written to `brief_snapshots.brief_json`.

**Add Pydantic model in `artemis/brief/schemas.py` (NEW file or extend existing if present):**

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class BriefHighlight(BaseModel):
    """One highlight item in the daily brief."""
    title: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default=None, max_length=100)  # 'jira', 'calendar', 'okr', etc.

    model_config = ConfigDict(extra="forbid")


class BriefPriority(BaseModel):
    """One priority item — what the user should focus on."""
    item: str = Field(min_length=1, max_length=300)
    rationale: str | None = Field(default=None, max_length=500)
    urgency: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")


class BriefNextAction(BaseModel):
    """One concrete next action."""
    action: str = Field(min_length=1, max_length=300)
    owner: str | None = Field(default=None, max_length=100)
    due: str | None = Field(default=None, max_length=50)  # ISO date or loose token

    model_config = ConfigDict(extra="forbid")


class DailyBrief(BaseModel):
    """Full daily brief output. Strict shape."""
    highlights: list[BriefHighlight] = Field(default_factory=list, max_length=10)
    priorities: list[BriefPriority] = Field(default_factory=list, max_length=8)
    next_actions: list[BriefNextAction] = Field(default_factory=list, max_length=10)
    okr_status: str | None = Field(default=None, max_length=500)
    risks: list[str] = Field(default_factory=list, max_length=10)
    summary: str | None = Field(default=None, max_length=2000)
    confidence: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")
```

**Wrap the LLM call in retry-on-validation-failure helper:**

```python
async def _generate_brief_with_retry(
    *,
    sources: dict,
    adapter,
    max_retries: int = 1,
) -> DailyBrief:
    """Generate daily brief, validate against DailyBrief schema, retry once on validation failure."""
    last_error: str | None = None
    for attempt in range(max_retries + 1):
        prompt = _build_brief_prompt(sources, prior_error=last_error)
        response = await adapter.complete(...)
        raw_text = _extract_text(response)
        clean = _strip_markdown(raw_text)
        try:
            return DailyBrief.model_validate_json(clean)
        except ValidationError as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                logger.warning("Daily brief validation persistent failure: %s", exc)
                return DailyBrief()  # empty default — preserves audit row
    return DailyBrief()
```

**Persistence stays unchanged** — `brief_snapshots.brief_json` accepts the `model_dump(mode="json")` output. Existing M6/UI consumers continue to work.

**Note:** `DailyBrief` is consumed by the daily-brief UI surface today; in the future Floating Artemis may also retrieve it as memory context. Provenance framing should mark it as LLM-generated when surfaced downstream. Add the same `confidence_origin="brief_generator"` pattern as M1/MC writes if any memory observation is written from it (currently not — but flag if implementation reveals it).

### Part B — Pipeline AI Panel Pydantic + retry

**File:** `artemis/pipelines/assistant/turn_handler.py` (around lines 290-330)

**Current state:** the assistant's reply may contain inline `PROPOSAL_BEGIN ... PROPOSAL_END` blocks. These get regex-extracted and JSON-decoded. The proposal is then staged for user approval before applying to the pipeline graph.

**Add Pydantic model in `artemis/pipelines/assistant/schemas.py` (NEW file):**

```python
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class PipelineNodeMod(BaseModel):
    """One modification to a pipeline node."""
    action: Literal["add", "update", "remove"]
    node_id: str = Field(min_length=1, max_length=100)
    node_type: str | None = Field(default=None, max_length=100)
    config: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class PipelineEdgeMod(BaseModel):
    """One modification to a pipeline edge."""
    action: Literal["add", "remove"]
    from_node: str = Field(min_length=1, max_length=100)
    to_node: str = Field(min_length=1, max_length=100)
    condition: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


class PipelineProposal(BaseModel):
    """Inline proposal block emitted by the Pipeline AI Panel."""
    summary: str = Field(min_length=1, max_length=500)
    node_mods: list[PipelineNodeMod] = Field(default_factory=list, max_length=20)
    edge_mods: list[PipelineEdgeMod] = Field(default_factory=list, max_length=30)
    rationale: str | None = Field(default=None, max_length=1500)
    confidence: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")
```

**Modify the regex-extract path to validate:**

```python
def _extract_and_validate_proposal(assistant_text: str) -> tuple[str, PipelineProposal | None]:
    """Extract PROPOSAL_BEGIN...PROPOSAL_END block, validate against PipelineProposal.
    
    Returns (text_without_proposal_block, parsed_proposal_or_None).
    On validation failure, removes the malformed proposal from the text (so the user
    doesn't see broken UI markup) and logs a warning. The conversational text continues.
    """
    match = re.search(r"PROPOSAL_BEGIN\s*\n(.*?)\n\s*PROPOSAL_END", assistant_text, re.DOTALL)
    if not match:
        return assistant_text, None
    
    raw_json = match.group(1).strip()
    cleaned_text = re.sub(r"PROPOSAL_BEGIN.*?PROPOSAL_END", "", assistant_text, flags=re.DOTALL).strip()
    
    try:
        proposal = PipelineProposal.model_validate_json(raw_json)
        return cleaned_text, proposal
    except ValidationError as exc:
        logger.warning("Pipeline AI Panel proposal validation failed: %s", exc)
        # Fall through: text returned without proposal; user sees the response without
        # the malformed block. Retry handled at the caller level (next turn re-prompts).
        return cleaned_text, None
```

**Retry-on-validation:** the Pipeline AI Panel's flow is operator-interactive (user types a question, gets a response with optional proposal). If validation fails:

1. Strip the malformed proposal from the response (so UI doesn't break)
2. Log warning with the validation error
3. **Don't retry within this turn** — the operator's response is already mostly useful (the conversational text). They can ask the LLM to try again with a follow-up message.

This differs from H3/H4 (autonomous retry) because Pipeline AI Panel is interactive. The user's next turn is the natural retry.

### Part C — Provenance markers on consumed-by-LLM outputs

Daily Brief content is occasionally consumed by Floating Artemis as recent context. Apply the H3/H4 provenance pattern:

When Floating Artemis (or anywhere else) retrieves a brief_snapshot, surface it with provenance framing:

```python
brief_block = (
    "\n\n## Latest daily brief (LLM-generated structured summary)\n\n"
    "This brief was generated by the brief_generator LLM. Use it for context "
    "but verify specific claims (Jira ticket numbers, OKR percentages, etc.) "
    "before acting on them.\n\n"
    f"{formatted_brief}"
)
```

For Pipeline AI Panel proposals: they're already user-approved before applying, so additional provenance framing is less load-bearing. Skip unless the Worker sees a natural place to add it.

### Part D — Tests

`artemis/brief/tests/test_h5_brief_pydantic.py`:

1. **Valid brief passes Pydantic.** Fixture brief with reasonable highlights + priorities. Verify `DailyBrief.model_validate_json` accepts.
2. **Oversized highlight rejected.** `title` 500 chars. Validation fails.
3. **Extra field rejected.** `{"hallucinated_field": "..."}` triggers `extra="forbid"`.
4. **Invalid urgency rejected.** `urgency="extreme"` rejected (Literal).
5. **Empty bullet rejected.** Empty `item` in priority list.
6. **Retry on validation failure.** Mock adapter to return invalid JSON first, valid second. Verify second result lands.
7. **Persistent failure produces empty brief.** Mock adapter always invalid. Verify `brief_snapshots` row has empty DailyBrief + warning logged.

`artemis/pipelines/assistant/tests/test_h5_pipeline_ai_pydantic.py`:

8. **Valid proposal passes Pydantic.** Inline PROPOSAL_BEGIN/END block with valid shape.
9. **Invalid node_mods.action rejected.** `action="extreme"` (not in Literal).
10. **Malformed JSON in proposal block triggers strip + log.** Verify conversational text returned without the block, warning logged, no exception bubbles.
11. **No PROPOSAL block in response → text returned as-is.** No-op test.

---

## Files owned

- NEW: `artemis/brief/schemas.py` (DailyBrief + sub-models)
- EDIT: `artemis/brief/generator.py` (replace json.loads + dict with Pydantic validate; add retry helper)
- NEW: `artemis/pipelines/assistant/schemas.py` (PipelineProposal + sub-models)
- EDIT: `artemis/pipelines/assistant/turn_handler.py` (extract_and_validate_proposal helper)
- POSSIBLE EDIT: `artemis/floating_artemis/chat.py` (provenance framing if brief is injected — likely a small change)
- NEW: `artemis/brief/tests/test_h5_brief_pydantic.py`
- NEW: `artemis/pipelines/assistant/tests/test_h5_pipeline_ai_pydantic.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0051`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/brief/tests/test_h5_brief_pydantic.py artemis/pipelines/assistant/tests/test_h5_pipeline_ai_pydantic.py -v` — all 11 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt (j5b Jira + b3_consolidation flakes). **Paste.**
4. **No regressions in existing brief / pipeline tests.** **Paste pytest summary for `artemis/brief/tests/` and `artemis/pipelines/assistant/tests/`.**
5. **Manual smoke (Lead does this post-merge):**
   - Trigger a daily brief generation via the API or scheduler
   - Verify `brief_snapshots` row lands with valid DailyBrief shape (query the row, parse, check fields)
   - Open Pipeline AI Panel via UI, send a conversational message that might produce a proposal
   - Verify proposal (if any) passes through validation OR is cleanly stripped if malformed
   - **Paste evidence.**
6. `git diff --stat` + `git log --oneline -1` on `worker/h5-daily-brief-pipeline-ai-pydantic`. **Paste.**

---

## Hard constraints

- **Failure isolation.** Validation failure produces empty/default object + log; doesn't break the API or UI flow.
- **No schema changes.** Migration stays at 0051.
- **Backward-compat on consumers.** Existing UI reading `brief_snapshots.brief_json` continues to work. Pipeline AI Panel proposal application stays unchanged (only the extract+validate step is new).
- **Pipeline AI Panel doesn't auto-retry** — operator's next turn is the natural retry path (different from H3/H4 autonomous retry).
- **No Pydantic strict_int / strict_str modes** — use ConfigDict(extra="forbid") for shape strictness; trust Pydantic's default type coercion for primitives.
- **Length limits matter.** Per H3/H4 pattern: `max_length` on all string fields, `max_length` (Pydantic 2 syntax `max_length` not `max_items` for lists) on all lists.
- **Local-only git.** Worker commits on `worker/h5-daily-brief-pipeline-ai-pydantic`; terminal-Lead merges after Lead approves.

---

## Why this closes the anti-hallucination loop

Per `docs/hallucination-audit-2026-05-29.md`:

```
Pre-H1:  every JSON-emitting LLM surface was unvalidated → pollution
Post-H1: tool errors are self-teaching → runtime recovery
Post-H2: scout intake Pydantic + reason_code allowlist
Post-H3: trajectory summarizer Pydantic + Builder revalidation provenance
Post-H4: meeting summarizer Pydantic + Floating Artemis revalidation provenance
Post-H5: Daily Brief Pydantic + Pipeline AI Panel Pydantic
```

After H5: **the "no hallucinations" invariant Jon stated 2026-05-29 is structurally enforced across every JSON-emitting LLM surface in the platform.** Every emission goes through Pydantic validation. Every failure triggers self-correction (retry or operator-prompted next turn). Every output consumed by a downstream LLM carries provenance framing.

This completes the anti-hallucination architectural layer. The platform's substrate is now structurally hardened against the hallucination class of bug that produced CC19's smoke + Run #329's failures.

---

## Report-back format

```
H5 — Daily Brief + Pipeline AI Panel Pydantic report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count (especially test #6 brief retry, test #10 pipeline malformed-strip)
4. No-regression check (existing brief/pipeline tests pass)
5. Manual smoke result — PASTE evidence of valid brief generation + pipeline AI panel response
6. Provenance framing — was brief injected into Floating Artemis or anywhere else? If yes, framing applied per H3/H4 pattern?
7. check.sh summary
8. Anything surprising — especially around the existing JSON-extraction code paths or interactions with the UI surface
```

---

**Worker: H5 closes the anti-hallucination architectural layer. After this lands, no JSON-emitting LLM surface in the platform can produce durable data without passing Pydantic validation first. The "no hallucinations" invariant Jon stated becomes structurally enforced across every emit site. The substrate is ready for the next layer of work (Stewardship, integration, etc.) with the hallucination-class of bug structurally impossible.**
