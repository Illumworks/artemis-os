# Cost Phase 3 — Routing opportunities tab

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cost-phase-3-routing-opportunities`
**Browser smoke owner:** Lead, post-merge — open Cost page → Routing opportunities tab, verify recommendations render with sensible numbers + trade-off notes.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~280 (recommendation endpoint + tab renderer + Apply-button integration + availability filtering + tests).
**Priority:** MEDIUM — fills out the "where can I save" question AND makes recommendations self-service.
**Parent plan:** `briefs/cost-page-design.md`
**Companion audit:** `audits/cost-page-audit.md`
**Depends on:** Phase 2 merged + **`briefs/routing-control-surface.md` merged** (provides the override backend this tab calls).

---

## Why this exists

The audit found that today essentially 100% of spend is Anthropic-routed. The page should be honest about that AND surface what-if math: "for the work you've already done, if you'd routed these tokens to Gemini Flash, you'd have saved $X/mo."

This tab is **recommendations PLUS Apply buttons** — recommendations come from cost data, Apply hits the routing-control-surface backend to persist an override. Two surfaces, one source of truth:

- The **dedicated Routing page** (from `briefs/routing-control-surface.md`) is the full configuration surface: every feature, current cascade, manual edit.
- This **Cost → Routing opportunities tab** is the *narrow, recommendation-driven* surface: "here's where there's measurable savings, click Apply to act on it." Same backend, different framing.

**Critical: recommendations must be grounded.** The audit found that not every provider is reachable today (codex CLI not on PATH, all API keys empty). A naive recommendation engine would suggest "switch to Gemini Flash, save $X" when Gemini has no key set, then Apply would set up a broken cascade. Recommendations must filter by `provider_health` and only surface alternatives where the target provider is actually available — OR clearly distinguish "Available now" recommendations from "Setup required" recommendations.

---

## Scope

### Part A — Backend: `/api/costs/routing-opportunities`

New endpoint. Query params: `from`, `to` (same as `/summary`).

Logic:

1. For each (feature_tag, model) group in the time window, compute:
   - Total `input_tokens`, `output_tokens` for that group.
   - Current spend (sum of `cost_usd`).
2. For each alternative model in `pricing.py` (from a different provider):
   - Compute hypothetical cost using alternative rates.
   - Compare to current spend.
3. For each (feature_tag, current_model) pair, return the 1-2 alternatives with biggest savings, but only if savings exceed $1/mo at the current pace (avoid noise).

Eligible alternatives (the "candidates" list — encoded in `artemis/costs/routing_candidates.py`):

```python
CANDIDATES = {
    # For low-stakes summarization or transformation
    "low_stakes": [
        ("lm-studio", "qwen/qwen3-14b"),                    # local, free, privacy-friendly
        ("gemini", "gemini-2.5-flash"),                     # cloud, strict JSON, free-tier
        ("openai", "gpt-4o-mini"),                          # cloud, general-purpose cheap
        ("anthropic", "claude-haiku-4-5-20251001"),         # same-provider downgrade
    ],
    # For low-stakes tasks needing strict JSON schema (graph extraction, consolidation)
    "low_stakes_json_strict": [
        ("gemini", "gemini-2.5-flash"),                     # Gemini's JSON adherence wins
        ("lm-studio", "qwen/qwen3-14b"),                    # local fallback
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
    # For low-stakes pure classification (district classifier, content type tagger)
    "low_stakes_classification": [
        ("lm-studio", "qwen/qwen3-14b"),                    # high volume favors local + free
        ("gemini", "gemini-2.5-flash"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
    # For agentic / structured-output / customer-facing critical work
    "critical": [
        ("anthropic", "claude-haiku-4-5-20251001"),         # only suggest haiku as downgrade; never offload off-provider
    ],
}

# Tag each feature_tag with which candidate set applies
# (See docs/provider-routing-cost-plan.md Section 4 for the canonical tier assignments)
FEATURE_TIER = {
    "agent_run": "critical",
    "floating_artemis": "critical",
    "workflow": "critical",
    "writing_studio_compose": "critical",
    "campaign_brief_assembler": "critical",
    "campaign_initiation": "critical",
    "meetings_qa": "critical",
    "spawn_subagent": "critical",
    "memory_consolidation": "low_stakes_json_strict",
    "memory_graph_extraction": "low_stakes_json_strict",
    "trajectory_summary": "low_stakes",
    "meeting_summary": "low_stakes",                        # long context — Gemini wins; see notes
    "marketing_brief": "critical",
    "marketing_scout": "critical",                          # default; per-agent classifier scouts can override
    "signal_qualifier": "low_stakes",
    "background": "low_stakes",
    "unknown": "low_stakes",
    "pipeline_canvas_ai": "critical",                       # UI proposals; user-facing
    "builder_propose_agent": "critical",                    # operator-facing creation
    "builder_propose_skill": "critical",
    "okr_suggest_kr": "low_stakes",
    "okr_extract_activity": "low_stakes_json_strict",
    "dev_projects_loop": "critical",
    "mcp_sandbox": "critical",
}

# Trade-off notes for the UI
TRADEOFF_NOTES = {
    ("lm-studio", "qwen/qwen3-14b"): "Local model on the Mac mini. $0 marginal cost, full privacy, but single-stream — concurrent calls queue. Drifts on strict JSON schemas ~5-15% of the time without explicit JSON-mode tuning.",
    ("gemini", "gemini-2.5-flash"): "Strong for summarization + strict JSON. 1M context window. Free-tier rate-limited (~15 RPM / 1500 RPD on 2.0 Flash) — bursts may 429.",
    ("openai", "gpt-4o-mini"): "Cheap general-purpose; less reliable on tool-calling than Anthropic.",
    ("anthropic", "claude-haiku-4-5-20251001"): "Same provider, smaller model. Good drop-in for low-stakes tasks; doesn't reduce Claude subscription load.",
}
```

### Part A.5 — Availability filtering (critical)

Before returning recommendations, every alternative is filtered through `provider_health.probe_provider_health(provider)` (from `briefs/routing-control-surface.md`). For each candidate alternative:

- If provider is **available** → include with `availability: "available"`
- If provider is **unavailable** (no key, not on PATH, unreachable) → include with `availability: "setup_required"` + a `setup_hint` field linking to where to fix it (Connectors modal for keys, instructions modal for codex PATH)
- Frontend renders **available** recommendations as **clickable Apply** buttons; **setup_required** recommendations as **dimmed** with a "Set up to enable" link

This is what keeps recommendations grounded — no Apply button leads to a broken cascade.

Response shape:

```json
{
  "window": {"from": "...", "to": "..."},
  "monthly_pace": {
    "current_total_usd": 87.40,
    "projected_savings_usd_if_all_available_applied": 27.30,
    "projected_total_usd": 60.10
  },
  "opportunities": [
    {
      "feature_tag": "trajectory_summary",
      "current": {"provider": "claude-code", "model": "claude-sonnet-4-6", "cost_usd_in_window": 14.20, "monthly_pace_usd": 14.20},
      "current_routing_is_override": false,
      "alternatives": [
        {
          "provider": "lm-studio",
          "model": "qwen/qwen3-14b",
          "monthly_pace_usd": 0.00,
          "savings_usd": 14.20,
          "availability": "available",
          "tradeoff_note": "Local model on the Mac mini. $0 marginal cost, full privacy, but single-stream — concurrent calls queue.",
          "apply_cascade": [
            {"provider": "lm-studio", "model": "qwen/qwen3-14b"},
            {"provider": "gemini", "model": "gemini-2.5-flash"},
            {"provider": "claude-code", "model": "claude-haiku-4-5-20251001"}
          ]
        },
        {
          "provider": "gemini",
          "model": "gemini-2.5-flash",
          "monthly_pace_usd": 0.84,
          "savings_usd": 13.36,
          "availability": "setup_required",
          "setup_hint": "Add GEMINI_API_KEY in Connectors to enable.",
          "tradeoff_note": "Strong for summarization + strict JSON. 1M context window. Free-tier rate-limited.",
          "apply_cascade": [
            {"provider": "gemini", "model": "gemini-2.5-flash"},
            {"provider": "lm-studio", "model": "qwen/qwen3-14b"},
            {"provider": "claude-code", "model": "claude-haiku-4-5-20251001"}
          ]
        }
      ]
    },
    ...
  ]
}
```

Each alternative now carries:
- `availability` — `"available"` | `"setup_required"`
- `apply_cascade` — the full cascade the Apply button would POST to `/api/routing/features/{tag}/override`. Three steps: primary (the recommendation), secondary (an available fallback), tertiary (the current routing as final fallback). This ensures clicking Apply never leaves the user with a single-step cascade that could 429 the system into a hard failure.
- `setup_hint` (when `availability=setup_required`) — short text + deep link to Connectors or instructions

"Monthly pace" extrapolates current window's daily rate to a full month.

Opportunities sorted by `top alternative savings` descending. Limit to top 10 to avoid the wall-of-recommendations problem.

### Part B — Frontend tab renderer

Replace the placeholder in `public/js/features/cost-shell.js` for the Routing opportunities tab.

UI:

```
Routing opportunities

If you applied the available recommendations below, you'd save approximately
$14.20 / month at the current pace.
$13.10 / month more is available after setup (Gemini, OpenAI keys).

┌──────────────────────────────────────────────────────────────────────┐
│ Trajectory summarizer                                                │
│ Currently: claude-code · Sonnet 4.6 · $14.20/mo                      │
│                                                                       │
│ → LM Studio qwen3-14b · $0.00/mo · Save $14.20/mo                    │
│   ⚠ Local; concurrent calls queue. Drifts on strict JSON ~5-15%.    │
│   Cascade: lm-studio → gemini → claude-code haiku                    │
│   [Apply this routing]                                               │
│                                                                       │
│ → Gemini 2.5 Flash · $0.84/mo · Save $13.36/mo  (setup required)    │
│   ⚠ Strong for JSON. Free-tier rate-limited.                         │
│   Set up GEMINI_API_KEY in Connectors → [Open Connectors]            │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│ Memory consolidator                                                  │
│ Currently: claude-code · Haiku 4.5 · $5.40/mo                        │
│ Currently using a custom override (manage in [Routing →])            │
│                                                                       │
│ → Gemini 2.5 Flash · $0.30/mo · Save $5.10/mo  (setup required)     │
│   ⚠ JSON adherence wins for this task.                               │
│   Set up GEMINI_API_KEY → [Open Connectors]                          │
│                                                                       │
│ → LM Studio qwen3-14b · $0.00/mo · Save $5.40/mo                     │
│   ⚠ Local; may queue under concurrent consolidation bursts.          │
│   [Apply this routing]                                               │
└──────────────────────────────────────────────────────────────────────┘

[More opportunities ...]

For full per-feature routing control → [Open Routing page]
```

**Apply button behavior:**

Click [Apply this routing] →
1. Confirm modal: "Switch **{feature_label}** to **{primary provider/model}**? Cascade: {full cascade}. Reason: [free-text required]."
2. On confirm: POST `/api/routing/features/{feature_tag}/override` with the `apply_cascade` and the reason.
3. On success: toast "Routing updated. Next call uses the new cascade." + refetch the opportunities list (the just-applied feature should now show "Currently using a custom override" indicator).
4. On failure (404, 422, 5xx): toast with error + suggest opening the Routing page for manual edit.

**Setup-required state:**

Click [Open Connectors] → deep-links to the existing Integrations modal (per Connectors menu item pattern), scoped to the relevant provider. After Jon sets the key, refreshing the Cost page re-probes health, and the dimmed recommendation becomes Applyable.

**"Currently using a custom override" indicator:**

If `current_routing_is_override = true`, the card shows a small badge "Using custom override" with a link to the Routing page where the override is managed. The recommendation alternatives still render but the user is informed they're already off-default.

Footer (replaces the old read-only disclaimer):

> "Recommendations use your last 30 days of token volume. Apply buttons change routing immediately — next call uses the new cascade. For full per-feature control, open the [Routing page]."

### Part C — Tests

`artemis/routes/tests/test_routing_opportunities.py` (new):

1. **Endpoint computes savings correctly.** Seed 1 feature_tag with N tokens at Sonnet rates. Verify LM Studio cost = $0, Gemini Flash projected cost = expected.
2. **Endpoint filters out small savings (< $1/mo).** Seed a feature_tag with very low volume. Verify it doesn't appear in the response.
3. **Critical features only get critical-tier candidates.** Seed `feature_tag='floating_artemis'`. Verify recommendations include Haiku only (no LM Studio, no Gemini, no OpenAI).
4. **Monthly pace extrapolates correctly.** Seed 7 days of events. Verify monthly_pace ≈ 7-day total × (30/7).
5. **Availability filtering: LM Studio available → "available" status.** Mock health probe to return available=true. Verify response has `availability: "available"`.
6. **Availability filtering: Gemini key empty → "setup_required" status.** Mock health to return available=false. Verify `availability: "setup_required"` + `setup_hint` present.
7. **Apply cascade includes fallback steps.** For each available alternative, verify `apply_cascade` has ≥2 steps and ends with the current routing as final fallback.
8. **current_routing_is_override flag set correctly.** Seed a feature with an active override row in `feature_routing_overrides`. Verify response flags it.

`artemis/routes/tests/test_cost_routing_apply.py` (new — Apply integration tests):

9. **Apply button POST hits routing-control-surface endpoint.** Mock the override endpoint; verify the Apply path constructs the correct POST.
10. **Apply with unavailable provider returns 422 from foundation endpoint (and frontend shows error).** Backend rejection bubbles up cleanly.

---

## Files owned

- NEW: `artemis/costs/routing_candidates.py`
- NEW: `artemis/routes/costs_routing.py` (or extend existing `artemis/routes/costs.py`)
- EDIT: `public/js/features/cost-shell.js` (replace placeholder for routing tab + Apply button wiring)
- EDIT: `public/css/panels/cost.css` (opportunity card, Apply button, setup-required dimmed state)
- NEW: `artemis/routes/tests/test_routing_opportunities.py`
- NEW: `artemis/routes/tests/test_cost_routing_apply.py`

---

## Acceptance criteria

1. **No schema changes.** **Paste.**
2. **Backend tests pass.** **Paste.**
3. `./scripts/check.sh` passes. **Paste.**
4. **Live smoke (Lead does post-merge):**
   - Open Cost page → Routing opportunities tab.
   - Verify at least one opportunity card renders with plausible numbers (trajectory summarizer + memory consolidation are the most likely candidates given current usage).
   - Verify the "Save $X/mo" math is correct: take one card, compute by hand from token volumes × alternative rate, compare.
   - Verify the trade-off note is present + readable.
   - **Verify availability filtering works**: LM Studio recommendations should show [Apply this routing] buttons (active); Gemini/OpenAI recommendations should be dimmed with "Set up..." links (since keys are empty today).
   - Click [Apply this routing] on an LM Studio recommendation → confirm modal opens → enter reason → confirm → toast appears.
   - Verify `feature_routing_overrides` table has the new row: `SELECT feature_tag, cascade, updated_by FROM feature_routing_overrides WHERE active = true;`. **Paste the row.**
   - Open the dedicated Routing page (profile menu → Routing); verify the same feature now shows "Custom" with the cascade applied here.
   - Trigger a relevant call (e.g. fire a new agent run for trajectory summary); verify the next `cost_events` row reflects the new provider.
   - **Paste a screenshot of the populated tab with mixed available/setup-required states.**
5. `git diff --stat`. **Paste.**

---

## Hard constraints

- **Recommendations are tier-aware.** Critical features (agent_run, floating_artemis, workflow, marketing_brief, writing_studio_compose, etc.) only suggest Anthropic Haiku as a downgrade, never LM Studio / Gemini / OpenAI.
- **Recommendations are availability-aware.** Every alternative must carry an `availability` flag and (when `setup_required`) a deep link to the setup surface. No "ghost" recommendations.
- **Apply buttons hit the foundation endpoint.** Never write directly to `feature_routing_overrides` from this tab — always go through `POST /api/routing/features/{tag}/override` (from `briefs/routing-control-surface.md`). Same backend, same audit log.
- **Apply requires a reason.** Confirm modal has a required reason field; reason is passed through to the override endpoint and logged.
- **Apply cascade always has a fallback.** Never persist a single-step cascade. The Apply cascade is always ≥2 steps and includes the current routing as the final fallback. This protects against rate-limits and outages.
- **Trade-off notes are MANDATORY** on every alternative. Never show a savings number without context.
- **Filter noise.** Don't recommend an alternative if monthly savings < $1.
- **Top 10 opportunities max.** Sorted by top alternative savings descending.
- **Honest framing.** The footer points to the Routing page for full control — keep that link prominent so users don't think the Cost tab is the only way to manage routing.
- **Local-only git.** Worker on `worker/cost-phase-3-routing-opportunities`; Lead merges after smoke.

---

## Reconciliation with prior briefs

- **Depends on `briefs/routing-control-surface.md`** — that brief lays the backend (override table, audit log, resolver patch, health module, endpoints) that this tab consumes. Ship it first.
- **Supersedes `briefs/cost-prereq-multi-provider-activation.md`'s routing approach.** That brief proposed a code-level `feature_cascades.py` config; the DB-backed override system from the foundation brief replaces it. After both briefs land, `cost-prereq-multi-provider-activation.md` can become a smaller "seed initial overrides" brief (just POSTs to the override endpoint for the recommended starting set: memory_consolidation → Gemini-first, trajectory_summary → LM-Studio-first, district_classifier → LM-Studio-first).
