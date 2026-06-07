# H2 — Scout intake Pydantic + reason_code allowlist enforcement

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/h2-scout-intake-pydantic`
**Browser smoke owner:** Lead, post-merge — re-run a marketing pipeline, verify that no signal lands in `signal_queue` with a hallucinated `reason_code.code` or invented `urgency_tier`.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~150 (Pydantic models + validation wrapper + retry-on-violation + tests).
**Priority:** HIGH — closes the scout pollution surface identified in `docs/hallucination-audit-2026-05-29.md` (🔴 surface #1). Scouts produce the highest-volume LLM output in the platform and feed everything downstream.

---

## Why this exists

Per the hallucination audit:

> Scouts emit JSON with `reasonCodes`, `urgencyTier`, `sourceType`, `campaignFamily`, etc. The system prompt tells the LLM what codes are valid (`reason_code_system_suffix(agent.reason_codes_emitted)`) but ENFORCEMENT is trust-based.
>
> `artemis/marketing/scout_intake.py:193-204` (`normalize_intake_payload`) extracts reason_codes_raw but does NOT validate `reason_code.code` against the agent's `reason_codes_emitted` allowlist. The LLM can invent codes and they get persisted to `signal_reason_codes`. Verified empirically.

Run #329's `pending_human_review` hallucination has a same-class sibling at scout intake: any scout LLM can emit a `reasonCode.code` not in the allowlist (e.g. `LEADER_DEPARTURE_RUMOR`) and it'll be written to `signal_reason_codes` verbatim. Downstream the qualifier reads these codes, looks up rules, finds no rule matches the hallucinated code, and silently no-ops. The signal is misclassified without anyone noticing.

H2 closes this at intake.

---

## Scope

### Part A — Pydantic model for the scout's emitted payload

In `artemis/marketing/scout_intake.py` (or a new `artemis/marketing/scout_schemas.py`), define a Pydantic model that mirrors the JSON shape the scout LLM is instructed to emit:

```python
class ReasonCode(BaseModel):
    code: str  # validated against agent.reason_codes_emitted at instantiation
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str | None = None

class ScoutEmittedSignal(BaseModel):
    headline: str = Field(min_length=1, max_length=500)
    sourceType: Literal["manual", "starbridge", "news_article", "board_minutes", "state_doe", "linkedin_post", "regional_news", "federal_register", "grants_gov", "legiscan"]
    sourceUrl: str | None = None
    sourceTitle: str | None = None
    campaignFamily: str | None = None  # validated against known campaign families if available
    urgencyTier: Literal["hot", "standard", "low"] = "standard"
    reasonCodes: list[ReasonCode] = Field(default_factory=list)
    whyFlagged: str | None = None
    evidence: str | None = None
    fitScore: float | None = Field(default=None, ge=0.0, le=1.0)
    stateCode: str | None = None  # validated format if present
    discoveredBy: str  # anti-spoof: overridden to scout_type at intake
```

Validate the model strictly. Any extra fields = reject. Any wrong types = reject. Any wrong enum values = reject.

### Part B — Reason-code allowlist enforcement

The current `agent.reason_codes_emitted` field on the `agents` table is the source of truth for what codes a given scout is permitted to emit. Add a validator that:

1. At signal intake time, look up the scout's `reason_codes_emitted` list
2. For each emitted `reasonCode.code`, check membership in the allowlist
3. If a hallucinated code is present, REJECT the entire signal (don't silently strip — that loses information about the LLM's actual emission)

Return a structured error that names the specific bad code(s) + lists the allowed codes for the scout. Same self-teaching shape as H1 (consistent error language across the platform).

### Part C — Retry-on-validation-failure (the recovery loop)

In `artemis/marketing/scout_runner.py:172-205` (where the LLM call + json.loads happens), wrap the parse + validate flow:

1. Call `llm_adapter.complete(...)` → get raw JSON text
2. Parse + Pydantic-validate → if SUCCESS, persist normally; if FAIL, capture the structured error
3. On FAIL: log the error, increment `signals_rejected`, AND append the error message to the LLM context for the next iteration so the scout can self-correct on the next item

The scout already iterates over multiple items (line 169 area is in a for-loop over input items). The retry isn't per-item-retry — it's per-batch learning. The error message from item N teaches item N+1 implicitly via the system prompt's reason-code suffix that the LLM already sees.

**For per-item retry (optional, lower priority):** if a single item fails validation, retry once with the validation error appended to the user message. Cap at 1 retry to avoid infinite loops on persistently bad emissions.

### Part D — `signal_status` enum source-of-truth alignment

H2 should also fix the `signal_status` drift CC20's worker surfaced: `suppressed_deprioritized` is in the live DB but not in the `SignalState` enum. Either:

1. Add `suppressed_deprioritized` to `SignalState` (if it's a legitimate state)
2. OR migrate the legacy rows to a current enum value (if it's deprecated)

Document the canonical enum in `artemis/marketing/models.py` as the single source of truth. CC20's grounding tools already union-query both sources, but the underlying drift is a real cleanup that belongs in this brief.

### Part E — Tests

`artemis/marketing/tests/test_h2_scout_intake_pydantic.py`:

1. **Valid scout payload passes through unchanged.** Use a real fixture payload that currently lands in DB. Pydantic model should accept it; persistence should match current behavior.
2. **Hallucinated reason_code.code is rejected.** Scout's `reason_codes_emitted = ["FOO", "BAR"]`. LLM emits `reasonCode.code = "BAZ"`. Validation fails with error message listing `["FOO", "BAR"]` as the allowed set.
3. **Invalid urgencyTier is rejected.** LLM emits `urgencyTier = "extreme"`. Validation fails with error message listing valid values.
4. **Invalid sourceType is rejected.** LLM emits `sourceType = "tweet"`. Validation fails.
5. **Confidence out of bounds is clamped or rejected.** Test policy: if Pydantic constraints fire, validation fails (consistent with strict-shape principle). Document the choice in the model.
6. **`signal_status` drift resolved.** Empirically query DB for distinct `signal_status` values; assert every one is present in the canonical `SignalState` enum (or document the migration).
7. **End-to-end scout run integration.** Mock a scout LLM that emits an invalid reason_code. Run the scout. Assert: no row in `signal_queue` for that bad emission; the rejection is logged; the next item's emission succeeds.

### Part F — Banked observation: reason_code emission allowlist drift

While auditing, the Worker may notice that some scouts have very SHORT `reason_codes_emitted` lists (e.g. 2-3 codes) and the LLM commonly emits codes outside that list. This isn't H2's problem to solve, but flag it in the report: agent definitions may need refreshing if the LLM is consistently emitting codes the agent isn't permitted to use. Banked as a follow-up brief if the data supports it.

---

## Files owned

- NEW or EDIT: `artemis/marketing/scout_schemas.py` (Pydantic models for scout emission)
- EDIT: `artemis/marketing/scout_intake.py` (`normalize_intake_payload` calls the Pydantic validator + returns structured errors)
- EDIT: `artemis/marketing/scout_runner.py` (catch validation errors, log + increment rejected counter + thread to next iteration)
- EDIT: `artemis/marketing/models.py` (consolidate canonical `SignalState` enum if drift exists; document)
- NEW: `artemis/marketing/tests/test_h2_scout_intake_pydantic.py`
- Possible: alembic migration if `suppressed_deprioritized` requires data migration (only if Part D's resolution requires it)

---

## Acceptance criteria

1. **Migration check** — if Part D requires a migration, paste `alembic upgrade head` output. If not, paste `0047 (head)` unchanged.
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_h2_scout_intake_pydantic.py -v` — all 7 tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt failures. **Paste.**
4. **Live smoke (Lead does this post-merge):**
   - Trigger a marketing pipeline run
   - Check `signal_queue` for new rows — verify every `signal_status` is in the canonical enum
   - Check `signal_reason_codes` for new rows — verify every `code` is in the corresponding scout's `reason_codes_emitted` allowlist
   - **Paste the query results.**
5. `git diff --stat` + `git log --oneline -1` on `worker/h2-scout-intake-pydantic`. **Paste.**

---

## Hard constraints

- **Reject the whole signal on validation failure — don't silent-strip.** Information about the LLM's emission must be preserved (in the rejection log) so we can see hallucination rates over time.
- **Don't change the LLM prompt structure** in this brief. H2 is about enforcing what the prompt already says. Prompt evolution is a separate concern.
- **Backward-compat with the existing intake path.** Existing real-world scout outputs (`signal_queue` rows with current shape) should still validate. Test #1 covers this.
- **Local-only git.** Worker commits on `worker/h2-scout-intake-pydantic`; terminal-Lead merges after Lead approves.
- **Don't depend on H1.** H2 can land independently. If H1 lands first, the scout-intake error messages should match H1's self-teaching format. If H2 lands first, H1's later changes apply to the agent-runtime tool layer, not the intake layer.

---

## Why this matters beyond scouts

The scout intake is the canonical pattern for "LLM emits JSON → persist." H3 (trajectory summarizer) and H4 (meeting summarizer) follow the same shape with the same Pydantic-validation fix. H2 establishes the pattern. The Worker may want to factor out a shared `validate_llm_json_emission(model, raw_text)` helper that H3 and H4 reuse. If so, document the helper's location in the report.

---

## Report-back format

```
H2 — Scout intake Pydantic + reason_code allowlist report
1. Commit / branch / worktree
2. LOC diff stats per file
3. Tests added + pass count
4. Live smoke results — PASTE: query showing every signal_status is in canonical enum; query showing every reason_code.code is in the scout's allowlist
5. signal_status drift resolution — what was done (migration? enum addition? cleanup?)
6. Banked observation about reason_code allowlist coverage (per Part F)
7. Helper extraction — did you factor out a shared `validate_llm_json_emission` for H3/H4 to reuse?
8. check.sh summary
9. Anything surprising — especially around existing real-world signals that no longer validate (would be a regression)
```

---

**Worker: H2 closes the highest-volume LLM hallucination surface in the platform. Scouts run on every pipeline tick; every emission is a potential pollution vector for the qualifier downstream. After H2, no hallucinated `reasonCode.code` or `urgencyTier` can land in `signal_reason_codes` or `signal_queue`. The architectural shape (Pydantic on JSON emit) is the precedent H3 + H4 follow.**
