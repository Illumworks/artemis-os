# Cost prerequisite — Multi-provider activation

**Paste-into:** terminal-Lead → Opus or Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cost-multi-provider-activation`
**Browser smoke owner:** Lead, post-merge — trigger a trajectory summary or memory consolidation, confirm it routed to Gemini Flash, verify cost_events row reflects Gemini provider.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~250 (cascade refactor + per-feature config + tests).
**Priority:** MEDIUM — converts the Routing Opportunities tab's recommendations into actual savings. Independent of the cost page; can run in parallel with Phases 2-6.
**Parent plan:** `briefs/cost-page-design.md`
**Companion audit:** `audits/cost-page-audit.md`

---

## Why this exists

The audit confirmed that multi-provider infrastructure is fully wired (adapters, registry, resolver) but the default cascades terminate at Anthropic for every feature. OpenAI and Gemini are reachable only via Floating Artemis user selection or MCP sandbox.

That means the Routing Opportunities tab (Phase 3) recommends savings the system can't actually realize without per-feature routing wiring. This brief activates the routing strategy for the two cheapest-win features:

- **Trajectory summarizer** — runs after every agent run. Currently Sonnet 4.6. Recommended: Gemini 2.5 Flash. Estimated savings: ~$10-15/mo at current volumes.
- **Memory consolidator** — fires on 25-obs threshold per scope. Currently Haiku 4.5 (via `claude-code`). Recommended: Gemini 2.5 Flash. Estimated savings: ~$3-5/mo.

Both are "low_stakes" per the routing candidate taxonomy — summarization-class work where Gemini Flash performs comparably and costs ~10× less.

After this brief, the Routing Opportunities tab's recommendations begin to land as real savings in the Spend tab.

---

## Scope

### Part A — Feature-specific cascade config

NEW: `artemis/providers/feature_cascades.py`

```python
"""Per-feature provider cascade overrides.

Default cascade (from resolver.DEFAULT_CASCADE) is Anthropic-terminating.
This module overrides it for specific low-stakes features where Gemini Flash
is the preferred provider with Anthropic as fallback.
"""

# feature_tag → ordered cascade
FEATURE_CASCADES = {
    "trajectory_summary": ("gemini", "anthropic"),      # Gemini Flash → Sonnet fallback
    "memory_consolidation": ("gemini", "claude-code"),  # Gemini Flash → CLI fallback
}

# feature_tag → preferred model per provider (if non-default)
FEATURE_MODELS = {
    "trajectory_summary": {
        "gemini": "gemini-2.5-flash",
        "anthropic": "claude-sonnet-4-6",  # current behavior preserved on fallback
    },
    "memory_consolidation": {
        "gemini": "gemini-2.5-flash",
        "claude-code": "claude-haiku-4-5-20251001",  # current
    },
}

def get_cascade(feature_tag: str) -> tuple[str, ...]:
    """Return the cascade for a feature, falling back to DEFAULT_CASCADE."""

def get_model(feature_tag: str, provider: str) -> str | None:
    """Return the preferred model for (feature, provider), or None to use provider default."""
```

### Part B — Resolver extension

Edit `artemis/providers/resolver.py`. Add an optional `feature_tag` parameter to `resolve_adapter`:

```python
def resolve_adapter(
    provider: str | None = None,
    fallback_provider: str | None = None,
    *,
    feature_tag: str | None = None,
) -> Adapter:
    """Resolve provider, with feature-tag-aware cascade override.

    If feature_tag is set and has a configured cascade in FEATURE_CASCADES,
    walk that cascade. Otherwise fall back to (provider, fallback_provider, DEFAULT_CASCADE).
    """
```

Callers can opt in by passing `feature_tag`. Existing callers without `feature_tag` continue to use the old behavior — no breaking changes.

### Part C — Wire the two features

**Trajectory summarizer** (`artemis/builder/trajectory_summarizer.py:180`):

Replace:
```python
adapter = resolve_adapter("claude-code", "codex")
```
With:
```python
adapter = resolve_adapter(feature_tag="trajectory_summary")
```

The adapter resolution now prefers Gemini Flash. If `GEMINI_API_KEY` is not set or Gemini call fails, falls through to Sonnet via Anthropic.

**Memory consolidator** (`artemis/memory/consolidator.py:168`):

Replace:
```python
adapter = resolve_adapter(provider="claude-code")
```
With:
```python
adapter = resolve_adapter(feature_tag="memory_consolidation")
```

Same fallback discipline: Gemini Flash → CLI fallback.

### Part D — Telemetry: confirm cost_events reflect the new routing

After this brief lands, every new trajectory summary and memory consolidation writes a `cost_events` row with `provider='gemini'` and `model='gemini-2.5-flash'`. Verify by query post-smoke (acceptance #5 below).

### Part E — API key + graceful degradation

Verify `GEMINI_API_KEY` is set in `.env`. If missing, both features should:
1. Log a WARNING (one-time, not per-call).
2. Fall back to the cascade's second entry without errors.
3. Continue producing `cost_events` rows with the fallback provider/model.

Test this by setting `GEMINI_API_KEY=` (empty) in a test env and verifying both surfaces still complete successfully on the fallback.

### Part F — Tests

`artemis/providers/tests/test_feature_cascades.py` (new):

1. **`get_cascade` returns feature-specific cascade.** `trajectory_summary` → `("gemini", "anthropic")`.
2. **`get_cascade` falls back to DEFAULT_CASCADE for unknown features.** `unknown_feature` → DEFAULT_CASCADE.
3. **`resolve_adapter(feature_tag=...)` walks feature cascade.** Mock adapter availability; verify call order.
4. **Missing API key triggers fallback.** Mock Gemini missing key; verify Anthropic adapter is used; verify one WARNING log.

`artemis/builder/tests/test_trajectory_summarizer_routing.py` (new):

5. **Trajectory summarizer with GEMINI_API_KEY routes to Gemini.** Mock both adapters. Verify Gemini is called.
6. **Trajectory summarizer without GEMINI_API_KEY routes to Anthropic Sonnet.** Mock no-key state. Verify Anthropic call.

`artemis/memory/tests/test_consolidator_routing.py` (new):

7. **Memory consolidator with GEMINI_API_KEY routes to Gemini.**
8. **Memory consolidator without GEMINI_API_KEY routes to CLI.**

---

## Files owned

- NEW: `artemis/providers/feature_cascades.py`
- EDIT: `artemis/providers/resolver.py` (add `feature_tag` param)
- EDIT: `artemis/builder/trajectory_summarizer.py` (route via feature_tag)
- EDIT: `artemis/memory/consolidator.py` (route via feature_tag)
- NEW: `artemis/providers/tests/test_feature_cascades.py`
- NEW: `artemis/builder/tests/test_trajectory_summarizer_routing.py`
- NEW: `artemis/memory/tests/test_consolidator_routing.py`

---

## Acceptance criteria

1. **No schema changes.** **Paste.**
2. **Backend tests pass.** `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/providers/tests/test_feature_cascades.py artemis/builder/tests/test_trajectory_summarizer_routing.py artemis/memory/tests/test_consolidator_routing.py -v`. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Live smoke (Lead does post-merge):**
   - Confirm `GEMINI_API_KEY` is set: `printenv GEMINI_API_KEY | head -c 8`. **Paste (first 8 chars).**
   - Trigger an agent run that completes → wait for trajectory summarizer to fire → query: `SELECT provider, model FROM cost_events WHERE feature_tag = 'trajectory_summary' ORDER BY created_at DESC LIMIT 1`. **Paste.** Expect `gemini` / `gemini-2.5-flash`.
   - Force a memory consolidation (or wait for the threshold to trigger naturally) → query: `SELECT provider, model FROM cost_events WHERE feature_tag = 'memory_consolidation' ORDER BY created_at DESC LIMIT 1`. **Paste.** Expect `gemini` / `gemini-2.5-flash`.
   - Temporarily unset `GEMINI_API_KEY` in a test shell, trigger another summary, verify cost_events row shows Anthropic fallback. **Paste before-after.**
5. **Open Cost page → Spend tab.** Verify "by_model" breakdown now shows `gemini-2.5-flash` as a contributor (non-zero rows). **Paste a screenshot.**
6. `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **Backwards-compatible.** Existing callers that don't pass `feature_tag` see no behavior change. Only the two wired features (trajectory_summary, memory_consolidation) route to Gemini.
- **Graceful degradation.** Missing GEMINI_API_KEY does NOT break either feature. Logs WARNING once at startup or first miss; continues on fallback.
- **No new dependencies.** Per `CLAUDE.md` 7-day rule. Use existing `gemini` adapter.
- **Quality monitoring.** After 1 week post-merge, Lead spot-checks 5 trajectory summaries and 3 memory consolidations to ensure Gemini Flash output quality is comparable to Sonnet/Haiku. If quality regresses, revert via the cascade override (set `trajectory_summary` back to Anthropic in `FEATURE_CASCADES`).
- **Conservative rollout.** Only two features in this brief. Don't bulk-convert all low_stakes features at once. After the smoke + 1-week quality monitor, follow-up briefs can add: meeting_summary, marketing_scout, signal_qualifier, memory_graph_extraction.
- **Local-only git.** Worker on `worker/cost-multi-provider-activation`; Lead merges after smoke + a brief observation period.

---

## After this brief lands

The Cost page's Routing Opportunities tab will show fewer recommendations (because the largest ones got acted on). The Spend tab's "by_model" breakdown will start showing Gemini share. Routing remains advisory; Jon can revert any feature via `FEATURE_CASCADES` config without DB changes.

Future follow-ups (not in this brief):
- Meeting summarizer → Gemini Flash
- Marketing scout → Gemini Flash
- Signal qualifier → Gemini Flash (only if Phase 3 audit shows it's low_stakes — currently tagged as such)
- Memory graph extraction → Gemini Flash (depends on `briefs/memory-phase-5-prereq-graph-extractor-audit.md` first)
