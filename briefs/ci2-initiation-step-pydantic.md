# CI2 — Initiation pipeline step + Pydantic proposal + brief_assembler (Stream 2)

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4` · reasoning effort `medium`. Real wiring: a new pipeline step between Gate 1 and content, an extended agent prompt, and a Pydantic proposal schema with registry/territory validation. Some design inference about the pipeline executor + brief_assembler — use the flagship.
**Target branch:** `worker/ci2-initiation-step`
**Fires:** AFTER CI1 merges (needs initiation columns + deliverable_types registry + TargetScope).
**Browser smoke owner:** Lead post-merge (approve a signal → initiation proposal appears).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~400.
**Priority:** HIGH — the heart of Stream 2.

---

## Why this exists

Per `docs/campaign-initiation-and-district-design.md` CI-1/CI-2/CI-5: after Gate 1 approval, a **separate Campaign Initiation step** pauses the pipeline. The extended `brief_assembler` agent **proposes** name + objective + recommended deliverable mix + target scope; the operator confirms/edits (UI is CI3); only then does content work fire. Everything Pydantic-validated — the LLM proposes, never auto-commits, and cannot emit an invalid deliverable type or target scope.

This is the anti-hallucination discipline (H1–H5) applied to campaign creation.

---

## Scope

### Part A — `CampaignInitiationProposal` Pydantic model

`artemis/marketing/initiation_schemas.py` (CI1 started this file):
```python
class CampaignInitiationProposal(BaseModel):
    name: str                      # 1..120 chars
    objective: str                 # 1..500 chars
    recommended_deliverable_types: list[str]   # each validated against ACTIVE registry slugs
    target_scope: TargetScope      # the CI1 discriminated union
    rationale: str | None = None   # why these choices (for the operator)
```
- Validator: every `recommended_deliverable_types` entry must be an **active** deliverable-type slug (query registry). Invalid → ValidationError whose message lists active slugs (self-teaching → flows into the H1 retry the adapter already does).
- `target_scope` reuses CI1's validated union.

### Part B — Extend brief_assembler to emit the proposal

`marketing.content.brief_assembler` already "builds the immutable campaign brief." Extend it (prompt + tool) to ALSO emit a `CampaignInitiationProposal`:
- **Audit the prompt file FIRST** (memory lesson — Pydantic on LLM JSON requires the prompt to emit the matching shape). Find brief_assembler's prompt (`marketing-ops-v1/.../5.1-campaign-brief-assembler.md` or wherever it's sourced) and update it to instruct the exact proposal JSON shape, including: propose a human-readable campaign NAME, a one-line objective, the recommended deliverable mix (**default to `["outreach_email"]` since that's the only active type today**), and a target scope (default to the signal's resolved district's state via `{"mode":"states",...}` when known, else `{"mode":"all_districts"}`).
- Validate the emitted JSON through `CampaignInitiationProposal`. On failure, the existing self-teaching-retry path applies.
- Persist the proposal (e.g. on the candidate or a proposal record) so CI3's UI can load it for confirmation. Do NOT auto-call `initiate_campaign` — that's the operator's confirm action (CI3).

### Part C — Initiation pipeline step

In `artemis/pipelines/seeds/marketing_pipeline.py`: insert a step **between `gate_1_signals_inbox` and `content_brief_assembler`** that:
- Runs brief_assembler in "propose initiation" mode → produces + persists the `CampaignInitiationProposal`.
- Then **pauses for operator confirmation** (a `human_gate`-style pause, OR a new `campaign_initiation` node type — reuse the human_gate_executor pause/resume machinery; don't reinvent). The pipeline does NOT proceed to content nodes until the operator confirms (CI3 calls `initiate_campaign`, which releases the step).
- On confirm: content nodes fire **only for the confirmed `deliverable_types`** (today: just outreach_email). Wire the content/deliverable nodes to read the candidate's `deliverable_types_json` instead of the hardcoded 4-deliverable fan-out.

### Part D — Tests

`artemis/marketing/tests/test_ci2_initiation_step.py`:
1. `CampaignInitiationProposal` accepts a valid proposal (mix=`["outreach_email"]`).
2. Proposal with an inactive type (`social`) → ValidationError listing active slugs.
3. Proposal with invalid target_scope → ValidationError (self-teaching).
4. brief_assembler propose-mode (mocked LLM emitting valid JSON) → proposal persisted on the candidate; `initiate_campaign` NOT auto-called.
5. brief_assembler emits OLD shape (no name) → validation fails → retry path engaged (assert no silent empty proposal).
6. After `initiate_campaign` confirms mix=`["outreach_email"]`, only the email deliverable node is scheduled (not all 4).

---

## Files owned

- EDIT: `artemis/marketing/initiation_schemas.py` (+CampaignInitiationProposal)
- EDIT: brief_assembler prompt file (audit + update to new shape) ⚠️ **mandatory — see memory lesson**
- EDIT: `artemis/marketing/` brief_assembler invocation/handler
- EDIT: `artemis/pipelines/seeds/marketing_pipeline.py` (+initiation step, deliverable nodes read mix)
- POSSIBLE EDIT: `artemis/pipelines/node_executors/` if a new node type is cleaner than reusing human_gate
- NEW: `artemis/marketing/tests/test_ci2_initiation_step.py`

---

## Acceptance criteria

1. `pytest .../test_ci2_initiation_step.py -v` — 6 pass. **Paste.**
2. **Prompt-shape audit confirmed:** paste the diff of the brief_assembler prompt file showing the new proposal-JSON instructions. **Required.**
3. **No auto-commit:** test #4 proves proposal is persisted but `initiate_campaign` is operator-gated. **Paste.**
4. **Deliverable mix respected:** test #6 proves only confirmed types schedule. **Paste.**
5. `./scripts/check.sh` + `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **AUDIT THE PROMPT FILE** (memory lesson: Pydantic on LLM JSON without updating the prompt = silent empty output in prod). The prompt MUST instruct the exact proposal shape. Paste the diff.
- **LLM proposes, operator disposes.** Never auto-call `initiate_campaign`. The pipeline pauses for human confirm.
- **Content fires only for confirmed deliverable types.** Kill the hardcoded 4-deliverable fan-out; read `deliverable_types_json`.
- **Reuse human_gate pause/resume** machinery — don't build a parallel pause mechanism.
- **Fires after CI1.** Coordinate any migration number.
- **Local-only git.**

---

## Report-back format

```
CI2 — initiation step report
1. Commit / branch
2. LOC per file
3. brief_assembler PROMPT DIFF (new proposal shape) — mandatory
4. Test pass count (esp. no-auto-commit #4, old-shape-retry #5, mix-respected #6)
5. Where the pause lives (reused human_gate vs new node type) + why
6. check.sh summary
7. Surprises — esp. brief_assembler current prompt/output shape, pipeline executor pause wiring
```
