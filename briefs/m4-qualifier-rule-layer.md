# M4 — Qualifier Rule Layer (Josh's §4: hard skip / suppress / boost)

**Owner:** Sonnet Worker (isolated worktree)
**Branch:** `worker/m4-qualifier-rule-layer`
**LOC budget:** ~250 (full-diff insertions; cap at ~300 with 10% headroom)
**Brief author:** Lead (Opus 4.7)
**Depends on:** M1 (reason code registry, FK validation in intake). Does NOT depend on M3.
**Grounded in:** `decisions/campaign-signal-spec-v1.md` §4, `docs/marketing-ops-v1/agents/qualifier/2.1-cross-reference-agent.md`

## Why this brief exists

Josh's spec §4 defines three cross-cutting qualifier rule layers that sit **above** any individual ruleset: hard skip (kill the signal), suppress (downgrade), boost (upgrade). They are not part of any single ruleset YAML — they apply to every signal regardless of which campaign type it routes to. Today the Cross-Reference Agent's hard filters live inside each ruleset YAML, which means: (a) the hard skips aren't enforced for signals that wouldn't even reach that ruleset, (b) suppress/boost don't exist at all, and (c) the spec's three-layer model has no home in code.

M4 ships those three layers as a single module the qualifier calls between phases. Declarative rule definitions so Josh can tune without code changes. Each rule application writes an audit row so we can see why a signal was killed/downgraded/boosted.

## Scope

### In scope

1. **`artemis/marketing/qualifier/rule_layer.py`** — three pure functions:
   - `apply_hard_skips(signal, district, contact_db) -> SkipDecision`
   - `apply_suppress(signal, prior_signals, qualification_result) -> SuppressDecision`
   - `apply_boost(signal, prior_signals, qualification_result) -> BoostDecision`
   Each returns a structured decision dataclass: `{applied: bool, rule_id: str | None, reason: str | None, new_priority: str | None}`.

2. **Rule definitions** declared as data, not buried in code. Module-level constants:
   ```python
   HARD_SKIP_RULES: list[HardSkipRule] = [...]
   SUPPRESS_RULES: list[SuppressRule] = [...]
   BOOST_RULES: list[BoostRule] = [...]
   ```
   Each rule has: `id`, `description`, `predicate` (callable), `action` (skip / downgrade / upgrade). Predicates are pure functions of `(signal, context)` — no DB calls inside the predicate; context is pre-fetched by the orchestrator.

3. **Audit table** — `qualifier_rule_applications`: `id`, `signal_id`, `rule_id`, `layer` (skip/suppress/boost), `applied_at`, `from_priority`, `to_priority`, `reason`. Indexed on `(signal_id, applied_at)`.

4. **Alembic migration** — creates the audit table.

5. **Integration in `2.1 Cross-Reference Agent`** — call order:
   - Pre-Phase-1: `apply_hard_skips` (cheapest kill; no LLM cost incurred if skipped).
   - Post-Phase-3 (or post-Phase-2 if no Phase 3): `apply_suppress` and `apply_boost` to adjust final priority tier.
   The agent's existing `signal_queue` status update is unchanged in shape, but new transition reasons (`hard_skipped_hmh_partner`, `suppressed_stale`, etc.) feed into the existing `rejection_reason` / metadata fields.

6. **Tests** — for each of the 11 rules below: positive case (rule fires), negative case (rule doesn't fire), audit row written, priority transition correct.

### Out of scope

- Replacing existing ruleset YAML hard filters. Those stay. M4's hard skips are a **superset** — they apply globally, ahead of any ruleset.
- Tuning thresholds (similarity 0.92 / 0.70, snooze 7 days, etc.). Use spec defaults; Josh tunes later.
- The Ruleset Manager Agent (2.2). Out of scope.
- UI for inspecting audit rows. Later.

## The 11 rules from Josh's §4

Verbatim from spec. Each becomes one rule with `id` matching the row label.

### §4.1 — Hard skip (3 rules)

| Rule id | Predicate | Action |
|---|---|---|
| `skip_hmh_partner` | District board adoption record names HMH Into Reading as current core ELA, OR `salesforce_account.is_hmh_partner = true` | Skip signal; status `rejected_hard_skip`; reason `hmh_partner_channel_conflict` |
| `skip_single_school` | `signal.geography.scope == "school"` (not "district") | Skip; reason `single_school_below_motion` |
| `skip_below_enrollment` | `district.enrollment < 5000` | Skip; reason `district_below_enrollment_threshold` |

All three: log to `skipped_signals` for visibility, do NOT surface to inbox. (Existing `skipped_signals` table — if missing, create in this migration.)

### §4.2 — Suppress (4 rules)

| Rule id | Predicate | Action |
|---|---|---|
| `suppress_stale_signal` | Same `district_id` + same `reason_code` emitted in last 30 days AND `material_change_check_passed == false` | Suppress; status `suppressed_stale` |
| `downgrade_speculation_not_action` | `reason_code == "BOARD_OBC_DISCUSSION"` AND no paired `BOARD_OBC_RFP_APPROVED` or posted RFP within 7 days | Force `priority = "standard"` even if scout emitted `"hot"` |
| `hold_single_source_leader_transition` | `reason_code == "LEADER_TRANSITION_FORMAL"` AND `source.type == "linkedin_post"` AND no corroborating board/press source within 7 days | Status `held_pending_corroboration`; re-queue with 7-day delay; if still single-source after 7 days, downgrade to `priority = "enrichment"` |
| `downgrade_paywalled_evidence` | `"evidence_quote_partial" in signal.flags` | Downgrade priority one tier (hot→standard, standard→enrichment). Add `paywalled_source` to audit reason |

Note on `BOARD_OBC_DISCUSSION` — that code is NOT in Josh's 17. M1's registry rejects it. Treatment: use the closer code from Josh's 17 — `PROCUREMENT_ELA_ADOPTION` at discussion stage. The predicate becomes: `reason_code == "PROCUREMENT_ELA_ADOPTION"` AND no paired `PROCUREMENT_LITERACY_RFP` within 7 days. Flag this swap explicitly in the audit reason so we can spot if the substitution is wrong.

### §4.3 — Boost (3 rules)

| Rule id | Predicate | Action |
|---|---|---|
| `boost_stacked_signals` | Two distinct `reason_code` entries on same `district_id` within 30 days | Upgrade priority one tier (standard→hot). Specifically called out in spec: `DISTRICT_PROFICIENCY_GAP + VENDOR_DISSATISFACTION → hot` |
| `boost_leader_plus_curriculum` | `LEADER_TRANSITION_FORMAL` within 90 days AND (paired with `PROCUREMENT_ELA_ADOPTION` OR `DISTRICT_STRATEGIC_LITERACY`) | Force `priority = "hot"` |
| `boost_texas_approval_signals` | `reason_code in {"TX_HB1416_WAIVER", "TX_HB3_DYSLEXIA_COMPLIANCE"}` | Force `priority = "hot"` (Amira is TEA-approved for both; substitution signal) |

### One implicit rule (from §5 nuances)

| Rule id | Predicate | Action |
|---|---|---|
| `suppress_tx_biliteracy_v1` | `signal.geography.state == "TX"` AND `reason_code == "DISTRICT_DLL_EXPANSION"` | Status `suppressed_deprioritized`; reason `tx_biliteracy_v01_skip` |

Spec §5 Texas: "Skip TX biliteracy for v0.1 (deprioritized — revisit)." Implementing as a suppress rule rather than a hard skip — easier to flip back on when Josh decides.

## Resolution of the three ambiguities from the scout-files alignment

The sub-agent flagged three open questions when it propagated Josh's 17 codes. M4 resolves them as qualifier routing concerns:

1. **`FUNDING_HB2_ELIA` scope.** Stay with board_minutes_scout only. Spec is explicit: enrichment context, not a discrete event. M4 does NOT boost this code. It rides on whatever other signal pairs with it. No change needed.

2. **`DISTRICT_DLL_EXPANSION` single-scout.** Stay with board_minutes_scout only. Combined with the `suppress_tx_biliteracy_v1` rule, the volume on this code will be near-zero for v1. Acceptable.

3. **`POLICY_LIT_MANDATE` collapsed `STATE_GUIDANCE_ISSUED` / `STATE_MANDATE_ISSUED`.** Disambiguation lives in `signal.metadata.bill_stage` (added to the signal schema as an optional field — scouts populate when they have it). Possible values: `GUIDANCE`, `INTRODUCED`, `PASSED_CHAMBER`, `ENACTED`. The qualifier uses this to set priority per spec §2 ("hot at PASSED_CHAMBER or ENACTED; standard at INTRODUCED"). Add a rule:

   | Rule id | Predicate | Action |
   |---|---|---|
   | `urgency_bill_stage` | `reason_code == "POLICY_LIT_MANDATE"` AND `signal.metadata.bill_stage in {"PASSED_CHAMBER", "ENACTED"}` | Force `priority = "hot"` |

   This makes it 12 rules total. Brief grew by one rule but the LOC budget absorbs it.

## Invariants (structural — not "be careful")

1. **Rule predicates are pure.** No DB access, no side effects, no LLM calls. Context object passed in pre-fetched. Makes them unit-testable without infrastructure.
2. **Audit row written atomically with state/priority change.** Same transaction. If audit insert fails, the change rolls back.
3. **Rules apply in fixed order.** Hard skip > suppress > boost. A signal hard-skipped does NOT have suppress/boost applied. A signal suppressed does NOT get boosted.
4. **Within a layer, all matching rules apply** — not just the first. Two suppress rules can fire on the same signal (e.g., stale + paywalled); the priority floor is the lowest tier reached.
5. **A boost cannot raise above `hot`**. A downgrade cannot fall below `enrichment`. Saturation, not wrap-around.
6. **Application uses M3 `transition()`** when M3 lands — until then, direct status updates with TODO comment. (Brief allows for M3 not being merged yet.)

## Files expected (rough — Worker adjusts)

- `artemis/marketing/qualifier/rule_layer.py` — rule definitions + 3 apply functions + dataclasses. ~150 LOC.
- `artemis/marketing/models/qualifier_rule_application.py` — SQLAlchemy model. ~25 LOC.
- `alembic/versions/<rev>_qualifier_rule_layer.py` — audit table (+ `skipped_signals` if missing). ~40 LOC.
- `artemis/marketing/qualifier/cross_reference.py` — integration points (call sites). Surgical edits. ~20 LOC delta.
- `artemis/marketing/tests/test_qualifier_rule_layer.py` — exhaustive tests. ~80 LOC.

## Test plan

1. **Each of the 12 rules — positive case.** Construct signal that satisfies predicate; assert rule fires; assert priority/status correctly changed; assert audit row written with right `rule_id`, `from_priority`, `to_priority`, `reason`.
2. **Each rule — negative case.** Construct signal that does NOT satisfy predicate; assert rule does NOT fire; no audit row.
3. **Ordering invariant.** Hard-skipped signal: suppress/boost never called. Mock the suppress/boost functions and assert zero calls.
4. **Within-layer stacking.** Signal that satisfies `suppress_stale_signal` AND `downgrade_paywalled_evidence`. Assert both audit rows written. Assert final priority = lowest reached.
5. **Saturation.** Boost a `hot` signal: stays `hot`, no error. Downgrade an `enrichment` signal: stays `enrichment`, no error. Audit row still written with `from == to` to preserve trace? — **Decision: audit row only written when state actually changes.** Reduces noise.
6. **Unknown reason_code in predicate.** Predicate references a code; signal has no matching code → predicate returns False cleanly. No crash.
7. **Pure predicate test.** Run each predicate with no DB session in scope; assert it works. Catches accidental DB calls inside predicates.

## Invariants Worker must NOT regress

- **conftest hard-fail on non-test DB.** Commit `f083ab4`.
- **dotenv `override=False`.** Commit `7ad1598`.
- **No `git push`.** Local-only.
- **`pwd && git branch --show-current`** before every state-changing Bash call.
- **`git diff --stat` insertions** for LOC self-reporting. No estimating.
- **All 17 reason codes are FK-valid** (M1 enforces). Predicates reference codes by string — those strings must be in Josh's 17, else the test will fail at intake. If you need a code not in Josh's 17, stop and ping Lead.

## What "done" looks like

1. 12 rules declared as data in `rule_layer.py`, identifiable by `rule_id`.
2. Three pure apply functions, each returning a dataclass decision.
3. Audit table populated on every actual change; never on no-op or failure.
4. Cross-Reference Agent calls hard_skips before Phase 1, suppress + boost after Phase 3.
5. Tests cover the 7 plans above and pass.
6. `./scripts/check.sh` does not regress (note pre-existing unrelated failures).
7. Full-diff insertions ≤ 300. Over budget → stop and ping Lead.

## Report Worker submits

1. `git diff --stat` output.
2. The 12 rule definitions (paste — IDs + one-line predicates).
3. Test pass count.
4. Confirmation that all rules reference codes within Josh's 17 (paste the set you used).
5. Any predicate that needed a code outside Josh's 17 — STOP, do not implement, ping Lead.
6. Branch + worktree path.

---

**Lead notes (not for Worker):**
- The §4.2 `downgrade_speculation_not_action` rule originally referenced `BOARD_OBC_DISCUSSION`, which is not in Josh's 17. Substituted `PROCUREMENT_ELA_ADOPTION` at the "discussion-without-RFP" stage. If this substitution proves wrong in practice, we add a new code to the registry (via the `proposed_new_code` flow) and update the rule.
- The 12th rule (`urgency_bill_stage`) resolves the `POLICY_LIT_MANDATE` granularity question. The `bill_stage` metadata field is added to the signal schema implicitly here — when M5 ships scout prompts, scouts that emit `POLICY_LIT_MANDATE` should populate it.
- M3 dependency is soft. When M3 lands, Worker should call `transition()` instead of direct status writes. Until then, TODO comments mark the integration points.
