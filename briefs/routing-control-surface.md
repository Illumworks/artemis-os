# Routing control surface — dedicated Routing page + shared backend foundation

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/routing-control-surface`
**Browser smoke owner:** Lead, post-merge — open profile menu → click Routing → verify health dashboard, feature override table, default cascade editor all render with live data; flip one override and confirm the resolver respects it on the next call.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~520 + 2 alembic migrations.
**Priority:** HIGH — foundation for self-service routing control. Unblocks `briefs/cost-phase-3-routing-opportunities.md` Apply-button work and gives Jon a way to change routing without going through an agent.
**Parent plan:** `docs/provider-routing-cost-plan.md`
**Companion audit:** `docs/provider-routing-cost-plan.md` (Section 8 surfaces the gap this brief closes)
**Depends on:** none — independent of cost-phase work; can ship in parallel with `briefs/cost-phase-1-foundation.md`.

---

## Why this exists

Per the provider-routing audit, today the only way for Jon to change which provider runs which feature is:
1. Ask a Claude session or floating Artemis to edit code
2. Direct SQL on the `agents` table (works for the 20 named agents only)
3. Edit `artemis/providers/resolver.py` or feature-specific cascades inline

There is no first-class UI. The resolver has no per-feature override mechanism — every feature's cascade is hardcoded in code.

After this brief lands:
- Jon opens **profile menu → Routing** to see every LLM call site, its current effective cascade, and which providers are reachable right now.
- He can flip any feature's routing via dropdown and Apply, with no code change, no restart.
- The resolver reads from a `feature_routing_overrides` table at call time; overrides take effect on the next call.
- A `routing_changes_log` table audits every change.
- A new `provider_health` module probes adapter availability and powers the dashboard.

This also becomes the **shared backend foundation** that `cost-phase-3-routing-opportunities.md` consumes for its Apply buttons. Same APIs, two UI surfaces.

---

## Scope

### Part A — Schema migrations

TWO new tables. Single migration (cleanest), or two if Worker prefers per-table.

**`feature_routing_overrides`** — the override layer:

```python
class FeatureRoutingOverride(Base):
    __tablename__ = "feature_routing_overrides"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    feature_tag: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    # Allowed values match the cost_events.feature_tag taxonomy:
    # 'agent_run', 'floating_artemis', 'workflow', 'pipeline', 'marketing_scout',
    # 'marketing_brief', 'meeting_summary', 'memory_consolidation',
    # 'memory_graph_extraction', 'trajectory_summary', 'signal_qualifier',
    # 'background', 'pipeline_canvas_ai', 'builder_propose_agent',
    # 'builder_propose_skill', 'okr_suggest_kr', 'okr_extract_activity',
    # 'meetings_qa', 'dev_projects_loop', 'mcp_sandbox', 'spawn_subagent',
    # 'campaign_brief_assembler', 'campaign_initiation', 'writing_studio_compose'

    cascade: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    # Ordered list of {provider, model?} steps. Example:
    # [
    #   {"provider": "gemini", "model": "gemini-2.5-flash"},
    #   {"provider": "lm-studio", "model": "qwen/qwen3-14b"},
    #   {"provider": "claude-code", "model": "claude-haiku-4-5-20251001"}
    # ]

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="operator")
```

One row per feature_tag. Re-applying same feature_tag updates rather than creates duplicates (use `ON CONFLICT (feature_tag) DO UPDATE` pattern in the endpoint).

**`routing_changes_log`** — audit:

```python
class RoutingChangeLog(Base):
    __tablename__ = "routing_changes_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    changed_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="operator")

    scope: Mapped[str] = mapped_column(Text, nullable=False)
    # 'feature' | 'default_cascade' | 'agent'

    scope_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # feature_tag for scope='feature', null for scope='default_cascade', agent_id for scope='agent'

    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Lossless — every change is logged forever, never deleted.

### Part B — Provider health module

NEW: `artemis/providers/health.py`

```python
"""Provider health probing.

Single function: probe_provider_health(provider) -> {available, latency_ms, version?, error?}

Caches results for 60s per provider to avoid hammering.
"""

async def probe_provider_health(provider: str) -> dict:
    """Return health info for a single provider.

    {
      "provider": "lm-studio",
      "available": true,
      "latency_ms": 42,
      "version": "qwen3-14b loaded",
      "error": null,
      "checked_at": "2026-06-06T15:30:00Z"
    }
    """

async def probe_all_providers() -> list[dict]:
    """Probe every provider in parallel; return list of health records."""
```

Per-provider probes:

| Provider | Probe |
|---|---|
| `claude-code` | `shutil.which("claude")` + best-effort `--version` |
| `codex` | `shutil.which("codex")` + best-effort `--version` |
| `lm-studio` | `GET http://127.0.0.1:1234/v1/models` with 2s timeout |
| `anthropic` | `bool(os.environ.get("ANTHROPIC_API_KEY"))` — no actual API call |
| `openai` | `bool(os.environ.get("OPENAI_API_KEY"))` |
| `gemini` | `bool(os.environ.get("GEMINI_API_KEY"))` or check `GOOGLE_API_KEY` fallback |
| `openrouter` | `bool(os.environ.get("OPENROUTER_API_KEY"))` |
| `lm-studio` model list | If healthy, also return loaded model IDs from `/v1/models` |

Cache: per-provider TTL of 60s. Cache invalidation manual via `clear_health_cache()` (called when settings change).

### Part C — Resolver integration

Edit `artemis/providers/resolver.py`. Add `feature_tag` parameter to `resolve_adapter`:

```python
async def resolve_adapter(
    provider: str | None = None,
    fallback_provider: str | None = None,
    *,
    feature_tag: str | None = None,
    session: AsyncSession | None = None,
) -> Adapter:
    """Resolve provider with optional per-feature override.

    Resolution order:
      1. If feature_tag is set AND an active override exists for it
         in feature_routing_overrides → walk that cascade.
      2. Otherwise → use (provider, fallback_provider, DEFAULT_CASCADE) as today.
    """
```

The override lookup needs a DB session. Two patterns:
- Callers pass `session=` explicitly (cleanest)
- Helper opens a short-lived session if none passed (convenient but adds DB chatter)

Worker picks the cleaner integration; recommend explicit `session=` with a `get_routing_override_for_feature(session, feature_tag)` repository helper.

**Importantly**: the resolver's existing `(provider, fallback_provider, DEFAULT_CASCADE)` behavior is unchanged when `feature_tag` is None. All existing callers continue to work. Only callers that opt in by passing `feature_tag` see override behavior. This means rolling out overrides is opt-in per call site.

### Part D — Backend endpoints

NEW: `artemis/routes/routing.py` — under `/api/routing`. All require token.

```
GET    /api/routing/health
       → list of provider health records (see Part B)

GET    /api/routing/features
       → list of all known feature_tags with their effective cascade
         {
           "features": [
             {
               "feature_tag": "memory_consolidation",
               "current_cascade": [{"provider":"claude-code", "model":"claude-haiku-4-5-20251001"}],
               "is_override": false,
               "default_cascade": [{"provider":"claude-code", "model":"claude-haiku-4-5-20251001"}],
               "updated_at": null,
               "updated_by": null
             },
             ...
           ]
         }

POST   /api/routing/features/{feature_tag}/override
       body: {"cascade": [{"provider":"...","model":"..."}, ...], "reason": "..."}
       → upsert override; log change; return updated record

DELETE /api/routing/features/{feature_tag}/override
       → set active=false (lossless); log change

GET    /api/routing/default-cascade
       → current DEFAULT_CASCADE value

POST   /api/routing/default-cascade
       body: {"cascade": ["claude-code","codex","lm-studio","anthropic"], "reason": "..."}
       → persist to a new app_settings row OR a single-row table; log change

GET    /api/routing/changes-log?limit=50&offset=0
       → paginated audit of all routing changes
```

**Validation on POST `/features/{tag}/override`:**
- Reject if `feature_tag` not in canonical list (422)
- Reject if `cascade` is empty (422)
- Reject if any cascade step references an unknown provider (422)
- Reject if any cascade step references a model not in `pricing.py` registry for that provider (422 — soft check, useful for typos)
- WARN (in response payload, not error) if any cascade step's provider is currently unavailable per `provider_health` — let user proceed but flag it

**Default cascade is stored where?** Two options:
- In a new `app_settings` row (key/value json) — clean
- In code only (`DEFAULT_CASCADE` constant) — current state; can't change at runtime

This brief picks the `app_settings` row path. Add a single-row `app_settings` table OR reuse if one exists (Worker greps).

### Part E — Frontend: profile menu wiring

Edit `public/js/ui/artemis-shell.js`. Add a new item to the profile popover (between "Cost" — which Cost Phase 2 adds — and the divider, OR if Cost Phase 2 hasn't shipped yet, between Connectors and the divider).

Final menu order target:
```
Account & workspace
Settings
Connectors
Cost         ← added by cost-phase-2-visibility-dashboard.md
Routing      ← added by THIS brief
—
Help & docs
Sign out
```

Handler: `setState("view", "routing")`.

### Part F — Frontend: Routing page (shell route)

NEW: `public/js/features/routing-shell.js`

Add `routing` as a recognized view in `public/js/core/navigation.js`.

Three sections, stacked vertically (no tabs — keep it scannable):

**Section 1: Provider health**

```
┌─ Provider health ────────────────────────────────────────────────┐
│                                                                  │
│  ✓ claude-code     2.1.159 · ready                              │
│  ✗ codex           not on PATH · [Setup instructions]            │
│  ✓ lm-studio       :1234 · 2 models loaded · 38ms                │
│  ✗ anthropic       no API key · [Open Connectors →]              │
│  ✗ openai          no API key · [Open Connectors →]              │
│  ✗ gemini          no API key · [Open Connectors →]              │
│  ✗ openrouter      no API key · [Open Connectors →]              │
│                                                                  │
│  [Refresh health]                                                │
└──────────────────────────────────────────────────────────────────┘
```

Setup links:
- "not on PATH" links to a small instructions modal: "Run `ln -s /Applications/Codex.app/Contents/MacOS/codex /usr/local/bin/codex` to expose the codex CLI on PATH."
- "no API key" deep-links to Connectors modal scoped to the relevant provider (already a pattern in the app).

**Section 2: Default cascade**

```
┌─ Default cascade ────────────────────────────────────────────────┐
│  Applies to any feature without a custom override.               │
│                                                                  │
│  1. claude-code                                                  │
│  2. codex                                                        │
│  3. lm-studio                                                    │
│  4. anthropic                                                    │
│                                                                  │
│  [Edit default cascade]                                          │
└──────────────────────────────────────────────────────────────────┘
```

Edit modal: drag-to-reorder list of providers, plus add/remove. Save = POST `/api/routing/default-cascade` with the new ordering. Reason field required (small free-text input).

**Section 3: Per-feature overrides**

```
┌─ Per-feature routing ───────────────────────────────────────────────────────┐
│                                                                              │
│  Feature                       Current cascade                       Status  │
│  ─────────────────────────────────────────────────────────────────────────── │
│  Memory consolidator           gemini → lm-studio → claude-code      Custom  │
│                                  [Edit] [Reset to default]                   │
│                                                                              │
│  Trajectory summarizer         lm-studio → gemini → claude-code      Custom  │
│                                  [Edit] [Reset to default]                   │
│                                                                              │
│  Floating Artemis chat         (default)                             Default │
│                                  [Edit]                                      │
│                                                                              │
│  ... (every feature_tag listed) ...                                          │
│                                                                              │
│  [Show change log →]                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

Edit modal per feature:
- Header: "Routing for **{feature_label}**"
- Current cascade displayed as ordered list with drag-handles
- Per step: provider dropdown + model dropdown (model dropdown populated by querying `/api/routing/health` for that provider's available models — for lm-studio that's its loaded models; for others a curated list from `pricing.py`)
- Step shows ⚠ if provider is currently unavailable (per health), but doesn't block save
- Add step / Remove step controls
- Free-text "reason" field (required)
- "Apply" + "Cancel" buttons
- On Apply: POST `/api/routing/features/{tag}/override` → close modal → re-fetch features list → toast: "Routing updated. Next call will use the new cascade."

Reset to default: DELETE `/api/routing/features/{tag}/override` → confirm modal: "Reset {feature_label} to default cascade?" → execute → toast.

Change log:
- Modal/drawer listing the last 50 changes from `GET /api/routing/changes-log`
- Each row: timestamp · scope · scope_value · before → after (concise) · reason · changed_by
- "Show more" button paginates

### Part G — Feature catalog

NEW: `artemis/providers/feature_catalog.py` — single source of truth for which feature_tags exist + their human-readable labels + descriptions.

```python
FEATURES = {
    "memory_consolidation": {
        "label": "Memory consolidator",
        "description": "Batches observations into consolidated summaries. Runs per scope when 25 observations accumulate. Internal-facing.",
        "default_cascade": [{"provider": "claude-code", "model": "claude-haiku-4-5-20251001"}],
        "recommended_tier": 3,
    },
    "trajectory_summary": {
        "label": "Trajectory summarizer",
        "description": "Post-hoc analysis of agent runs. Internal context for memory. Background.",
        "default_cascade": [...],
        "recommended_tier": 3,
    },
    ...  # one entry per feature_tag from Part A's allowed list
}
```

Resolver's `get_default_cascade(feature_tag)` reads from this catalog when no override exists.

### Part H — Tests

`artemis/providers/tests/test_health.py`:
1. `probe_provider_health('claude-code')` returns available=True on this machine.
2. `probe_provider_health('codex')` returns available=False (not on PATH).
3. `probe_provider_health('lm-studio')` returns available=True with models list.
4. `probe_provider_health('anthropic')` returns available=False when env key empty.
5. Cache TTL: two consecutive calls within 60s do not re-probe.

`artemis/routes/tests/test_routing_endpoints.py`:
6. `GET /api/routing/health` returns all providers.
7. `GET /api/routing/features` returns full feature catalog with current cascades.
8. `POST /api/routing/features/memory_consolidation/override` with valid cascade → 200 + row inserted.
9. POST same feature twice → row updated, not duplicated.
10. POST with unknown feature_tag → 422.
11. POST with empty cascade → 422.
12. POST with unknown provider in cascade → 422.
13. POST with unavailable provider in cascade → 200 with warning in response.
14. `DELETE /api/routing/features/memory_consolidation/override` → active=false, log entry written.
15. `GET /api/routing/changes-log` returns audit rows newest first.

`artemis/providers/tests/test_resolver_overrides.py`:
16. `resolve_adapter(feature_tag='memory_consolidation', session=...)` with no override returns default cascade.
17. With override in DB, returns the override cascade.
18. With override `active=false`, falls back to default.
19. Without `feature_tag` param, existing behavior unchanged (regression guard).

---

## Files owned

- NEW: `alembic/versions/00XX_add_feature_routing_overrides_and_changes_log.py`
- NEW: `artemis/providers/health.py`
- NEW: `artemis/providers/feature_catalog.py`
- NEW: `artemis/providers/routing_models.py` (or add to existing models module)
- NEW: `artemis/providers/routing_repository.py` (or extend existing)
- NEW: `artemis/routes/routing.py`
- EDIT: `artemis/providers/resolver.py` (add `feature_tag` param + override lookup)
- EDIT: `artemis/main.py` (register new router)
- EDIT: `public/js/ui/artemis-shell.js` (add Routing item to profile popover)
- EDIT: `public/js/core/navigation.js` (ROUTING_VIEW)
- NEW: `public/js/features/routing-shell.js`
- NEW: `public/css/panels/routing.css`
- EDIT: `public/js/features/home.js` (mount routing-shell on view change)
- NEW: `artemis/providers/tests/test_health.py`
- NEW: `artemis/providers/tests/test_resolver_overrides.py`
- NEW: `artemis/routes/tests/test_routing_endpoints.py`

---

## Acceptance criteria

1. **Migrations apply cleanly.** `uv run alembic upgrade head` — both tables present. **Paste `\d feature_routing_overrides` + `\d routing_changes_log`.**
2. **All tests pass.** `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/providers/tests/ artemis/routes/tests/test_routing_endpoints.py -v`. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Live smoke (Lead does this post-merge):**
   - Click avatar → profile popover → verify "Routing" is present between Cost and the divider.
   - Open Routing page; verify the three sections render.
   - Provider health: claude-code = ✓, codex = ✗ (not on PATH), lm-studio = ✓ with model list. **Paste a screenshot.**
   - Default cascade section renders current `["claude-code", "codex", "lm-studio", "anthropic"]`.
   - Per-feature table lists ~24 features with most marked "Default".
   - Edit one feature (e.g. trajectory_summary): set cascade to `["lm-studio", "claude-code"]` with reason "test override"; Apply.
   - Verify the row in `feature_routing_overrides` exists with the new cascade.
   - Trigger an agent run that produces a trajectory summary; verify the resolver picked lm-studio (check the `cost_events` row's provider field — assumes Cost Phase 1 is also merged; if not, check via logging).
   - Reset trajectory_summary to default; verify `active=false` + a new log entry.
   - Open change log; verify both changes appear.
   - **Paste DOM snippets of each section.**
5. `git diff --stat` + `git log --oneline -1` on `worker/routing-control-surface`. **Paste.**

---

## Hard constraints

- **Resolver backwards compatibility.** Callers that don't pass `feature_tag` see exactly the same behavior as today. Verified by test #19.
- **Lossless audit.** No DELETE on `routing_changes_log`. Override "delete" sets `active=false`, never hard-deletes.
- **Default cascade changes are also logged.** Same audit table, `scope='default_cascade'`.
- **No automatic re-routing.** Changes take effect on the NEXT call to `resolve_adapter(feature_tag=...)`. In-flight calls continue with their resolved adapter. No retry, no migration.
- **Health probes never block.** All probes have ≤2s timeout. If a probe hangs, the dashboard shows "unknown" not "down."
- **Health is cached 60s.** A bursty UI doesn't hammer probes.
- **Apply with warning, not block.** If user picks an unavailable provider as primary, the form shows a warning ("anthropic is currently unavailable — no API key. The cascade will fall through to the next step.") but allows save. Reason: user may be configuring a future cascade ahead of a key.
- **Validation is strict on schema, lenient on intent.** Unknown feature_tag, unknown provider, empty cascade → 422. Unavailable provider in cascade → warn but accept.
- **No model-name typos.** Cascade entries with `model` set must reference a model in `artemis/costs/pricing.py` for that provider. If not, 422 with a hint.
- **Setup-links are deep-links.** "Open Connectors" goes to the existing Integrations modal scoped to the relevant provider; "Setup instructions" opens a small modal with shell command + reasoning.
- **Local-only git.** Worker on `worker/routing-control-surface`; Lead merges after smoke.

---

## Reconciliation with prior briefs

This brief is the foundation that:
- **Unblocks** `briefs/cost-phase-3-routing-opportunities.md` — the Apply buttons there will hit the endpoints defined here. (See the cost-phase-3 update for the integration spec.)
- **Supersedes the routing portion of** `briefs/cost-prereq-multi-provider-activation.md` — that brief's `artemis/providers/feature_cascades.py` becomes redundant once this DB-backed override mechanism exists. After this brief lands, the cost-prereq brief can become: "set initial overrides for memory_consolidation + trajectory_summary in `feature_routing_overrides` table via seed data" — much smaller scope.
- **Pairs with** `briefs/memory-phase-5-prereq-graph-extractor-audit.md` — once that audit fixes the hardcoded SDK call in `graph_extractor.py` and routes it through `resolve_adapter(feature_tag='memory_graph_extraction')`, the override surface here controls where extraction runs.

---

## Phase ordering reminder

Recommended ship order for the broader workstream:

1. **`briefs/cost-phase-1-foundation.md`** (cost_events + pricing registry) — independent
2. **`briefs/routing-control-surface.md`** ← THIS BRIEF — independent, can ship in parallel with Phase 1
3. **`briefs/cost-phase-2-visibility-dashboard.md`** — depends on Phase 1
4. **`briefs/cost-phase-3-routing-opportunities.md`** (updated) — depends on this brief + Phase 2
5. Phases 4, 5, 6 of the cost work — depend on Phase 2

Phases 1 + 2 (this brief) can be assigned to two different Workers in parallel since they share no files.
