# PLAN — Artemis Python Rebuild, Phased

**Companion to:** `decisions/artemis-python-rebuild.md` (the why).
**This doc:** the what, in what order, and who owns each slice.

---

## Operating principles

1. **Reference, don't reinvent.** Every slice starts by reading the equivalent module(s) in `claudeck-artemis/`. The behavior is the spec. Only re-design where the new stack genuinely changes the shape (e.g. Postgres FTS vs. SQLite FTS5).
2. **Keystone first.** The memory keystone is load-bearing for every other module. It ships before anything that depends on it.
3. **Vertical slices over horizontal layers.** We do not build all DB models, then all routes, then all services. We build a thin vertical end-to-end of one feature, then thicken. This gets us to "running and testable" early, not last.
4. **Slice = one Worker brief.** Each slice is small enough for the Worker to pick up, ship, hand back. Lead reviews diff, merges to local `main`, briefs the next slice.
5. **Tests are not optional.** Same discipline as the current Node app: >90% backend, 100% on keystone-class modules. pytest from slice 1.

---

## Repo name — Creative Director call

The new directory lives at `/Users/artemis/Desktop/Artemis/<name>/`. Three options for Jon:

| Name | Vibe |
|---|---|
| `artemis` | Clean, unambiguous, owns the brand. Sibling to `claudeck-artemis` makes the rename evident. |
| `artemis-os` | Matches the spec language (`artemis-os/`, "Artemis OS / Marketing OS"). Clear it's a system, not a tool. |
| `amira-os` | Centers on the company, not the codename. Most external-facing. |

**Default if Jon doesn't pick:** `artemis-os`. Matches the spec, most discoverable for anyone reading later, lets `artemis` stay free if Jon wants it as the brand label.

---

## Phase A — Scaffolding (Lead, 1 slice)

**Owner:** Lead.
**Why Lead, not Worker:** the foundation choices (dependency manager, project layout, FastAPI conventions, pytest setup, Alembic config, Docker for Postgres) shape every later slice. Get them right once.

**Deliverables:**

1. New repo at `/Users/artemis/Desktop/Artemis/<name>/`. `git init`, local-only.
2. `pyproject.toml` with `uv` (or `poetry` — Lead picks; default `uv` for speed).
3. FastAPI app skeleton with health check.
4. Postgres + pgvector via `docker-compose.yml` for local dev.
5. SQLAlchemy 2.x async engine + session pattern.
6. Alembic migrations bootstrapped.
7. pytest + httpx async client + pytest-asyncio.
8. CI-equivalent local script (`scripts/check.sh`: lint, type, test).
9. `CLAUDE.md` at new repo root with the operating rules, a pointer back to this repo's `PROJECT_LOG.md`, and the "frozen reference" pointer to `claudeck-artemis/`.
10. `.env.example` with placeholder keys.

**Exit:** `docker compose up`, then `uv run uvicorn artemis.main:app --reload` works. `GET /healthz` returns `{ok:true}`. `pytest` passes (one smoke test).

---

## Phase B — Memory keystone (Lead + Worker, 4 slices)

**Why Lead leads architecture, Worker executes slices:** keystone is load-bearing. Lead reads the Node implementation slice-by-slice, writes the Python translation in a brief, Worker implements, Lead reviews diff.

**Slice B1 — Storage + write path.**
- Postgres schema for `memory_scopes`, `memory_drawers`, `memory_observations`, `memory_evidence` (drop the "view over observations" hack from current Node — clean from the start).
- Alembic migration.
- SQLAlchemy models, Pydantic schemas.
- Write API: `write_drawer`, `write_observation`, `link_evidence`.
- Lossless rule enforced at the type level: `delete_observation` does **not** drop drawers.
- 25+ tests.

**Slice B2 — Embeddings + retrieval.**
- pgvector column on `memory_drawers` and `memory_observations`.
- Embedding service: sentence-transformers MiniLM-L6 default, OpenAI/Voyage swap behind one interface.
- FTS via Postgres tsvector (replaces SQLite FTS5).
- Fusion reranker: tsvector BM25 + pgvector cosine + recency + score. Weights in `config/memory-retrieval.yaml`.
- 30+ tests including a retrieval-quality fixture mirroring the Node `MEMORY-RETRIEVAL-QUALITY-VALIDATION.md`.

**Slice B3 — Consolidation + score + temporal (P1 + P2 from the Node plan, condensed).**
- Incremental consolidation triggered by drawer-write debounce.
- Source-quality weighting (write paths tagged at insert).
- Multi-feature scoring, category decay, validity windows.
- 25+ tests.

**Slice B4 — Graph & MCP (P3 from the Node plan, condensed).**
- Entities, aliases, mentions, relations schema.
- Haiku-based extraction at consolidation completion.
- `graph_proximity` fusion modality.
- MCP read-side server (Python equivalent of `server/mcp-memory.js`).
- 40+ tests.

**Exit Phase B:** the keystone matches the Node app's tested behavior on the same retrieval-quality validation set, with comparable or better top-5 precision.

---

## Phase C — Marketing OS contracts + plumbing (Worker, 3 slices)

**C1 — Domain models.** Signal, Candidate, Ruleset, TerritoryConfig, ContentAsset, CampaignBrief, ScoutRun, ScoutPackage. Pydantic schemas + SQLAlchemy models + Alembic migration. Tests round-trip every schema.

**C2 — Routes.** `/api/scouts`, `/api/signal-queue`, `/api/campaign-ops`, `/api/content-assets`, `/api/writing-studio` (adapter only, not the studio itself). Match the Node app's contract behavior — same payload shapes, same error semantics.

**C3 — Qualifier + brief assembler.** Port `server/signal-qualifier.js` (pure math) to Python; port `server/campaign-brief-assembler.js`. Tests mirror the Node test suite.

**Exit Phase C:** the equivalent smoke path from the Node 2026-05-15 worklog passes end-to-end against the Python app, with synthetic findings.

---

## Phase D — Scout workers (Worker, 1 slice per scout)

**D1 — Worker process scaffold.** Separate Python process. APScheduler. Per-scout module pattern. One shared `BaseScout` class with `run_once()` and `emit_signal()`. Worker submits findings to the Python app via `POST /api/scouts/runs`.

**D2 — Scout 1.4 Legislative.** LegiScan API. Cleanest scout to validate the pattern.

**D3 — Scout 1.5 Federal Funding.** Federal Register + Grants.gov + ED.gov RSS.

**D4–D9 — Remaining scouts** in this order: 1.1 Starbridge, 1.6 State DoE, 1.8 Board Minutes, 1.7 Procurement, 1.9 Leadership Transition, 1.2 Regional News, 1.3 LinkedIn (Mode B).

**Why this order:** API-shaped first (validates the pattern), then PDF-heavy, then aggregator (1.9 reads from the others), then news, then LinkedIn (third-party scraper service, depends on the LegiScan-equivalent license decision).

**Exit Phase D:** all nine scouts run on schedule against real sources, write into the signal queue, with rate-limiting and per-source health metrics.

---

## Phase E — UI port (Lead, 2 slices)

**E1 — Asset port.** Move the vanilla HTML + CSS + web components from `public/` largely as-is into the new repo's static surface (FastAPI's `StaticFiles`). Rewrite the API client (`public/js/core/api.js`) to match new endpoints; everything downstream survives.

**E2 — WebSocket relay.** Python equivalent of `server/ws-handler.js`. Same broadcast room model, same cross-connection approval pattern. FastAPI's WebSocket + asyncio.

**Exit Phase E:** the marketing-OS UI loads, the chat works, the signals inbox renders. Visually identical to current; functionally on Python.

---

## Phase F — Agent / skill / workflow builders (Lead + Worker, 3 slices)

**F1 — Agent loop.** Python rebuild of `server/agent-loop.js` against the Anthropic Python SDK. Prompt caching wired from day one. Tool calling, streaming, hooks.

**F2 — Builders backend.** `/api/agents`, `/api/skills`, `/api/workflows` with full CRUD + execution + DAG support.

**F3 — Builders frontend.** Port the agent / skill / workflow builder UIs from the Node app. Same UX, new backend.

**Exit Phase F:** Jon can build, edit, and run agents / skills / workflows in the new app.

---

## Phase G — Floating Artemis (Lead, 1 slice)

Rebuild as a Python agent. Same WebSocket integration, same memory access (via Python keystone APIs), same orchestrator behavior. Master of one universe — the Python one.

---

## Phase H — Data migration (Lead, 1 slice)

Export OKR Studio rows + Writing Studio rules from `claudeck-artemis/data.db`. Map to Python schema. Import. Dry-run first; commit second. Round-trip validated.

**This is the cutover moment.** Jon is in the loop here — it's the only data we preserve, and once this lands he should start using the Python app as his Artemis.

---

## Phase I — Polish + readiness (Lead + Worker, ongoing)

- Run the nine scouts against real sources for a week. Tune dedupe thresholds, daily caps.
- Multi-user identity model activation (just `owner_user_id` wiring + access middleware; schema already supports it).
- Deployment story: pick hosting (Fly / Render / VPS / Mac mini), wire it up, document. Out of scope for the build window; in scope before the team uses it.

---

## Parallelization

**Lead and Worker run continuously, mostly in parallel.** Lead writes briefs, reviews diffs, and works on Phase A, Phase E, Phase G. Worker takes Phase B / C / D / F slices.

When Lead is between briefs, Lead picks up the next slice the Worker isn't on. When Worker hits a `COORDINATION.md` trigger, Lead responds in-thread.

---

## What we do *not* do during the build

- No deploys.
- No multi-user activation (schema-ready, runtime-deferred until Phase I).
- No live scout runs against production sources until Phase D is complete for that scout.
- No back-feature of new ideas into the Node reference repo. Reference stays frozen.

---

## What we ask Jon for (and when)

| Moment | Ask |
|---|---|
| End of Phase A | Repo name. Default `artemis-os` if no response in 30 min. |
| End of Phase B | Quick "looks right" check on retrieval quality results vs. Node baseline. |
| End of Phase D | Approval to point a scout at real sources (a "go live" moment for that signal stream). |
| End of Phase E | Creative Director eye on the UI port — anything visually wrong that should be fixed now while it's cheap. |
| Phase H | Cutover decision: ready to switch your daily-use Artemis from Node to Python? |

Everything else: we just do it.

---

## Post-V1 phases — added 2026-05-16

The original plan stopped at H+I. As the build progressed Jon expanded the vision: Artemis is also a personal-assistant platform beyond marketing-OS, with integration surfaces (Slack/Cal/Jira/Gmail/etc.), a polished design system, and a packagable personal variant. These land as Phases J, K, L *after* V1 (B-H) ships.

### Phase J — Integration surfaces

Each integration is a small slice. The Node app had source modules for all of these; none are in the Python rebuild yet. Floating Artemis (G) accommodates these via her tool-registry filtering — when an integration's surface is live (per `/api/_status`), her tools for it appear.

Slice list, each a separate Sonnet sub-agent (can run several in parallel):

- **J1 — Slack** — channels, threads, send/schedule messages, summarize. Tools: `slack_list_channels`, `slack_read_channel`, `slack_send_message` (Layer 3), `slack_schedule_message` (Layer 3), `slack_summarize_thread`.
- **J2 — Calendar** — read events, propose times, schedule. Tools: `cal_list_events`, `cal_propose_times`, `cal_create_event` (Layer 3), `cal_update_event` (Layer 3). Source: Google Calendar via OAuth.
- **J3 — Jira** — list/create/transition/comment. Tools: `jira_list_issues`, `jira_get_issue`, `jira_create_issue` (Layer 3), `jira_transition_issue` (Layer 3), `jira_add_comment` (Layer 3).
- **J4 — Gmail** — read inbox, summarize threads, draft replies (V1 drafts only — sending stays manual). Tools: `gmail_read_inbox`, `gmail_summarize_thread`, `gmail_draft_reply` (Layer 3).
- **J5 — Granola** (meeting transcripts) — list meetings, fetch transcripts. Tools: `granola_list_meetings`, `granola_get_transcript`.
- ~~**J6 — Telegram**~~ — **DROPPED** per Jon 2026-05-17 (too much spam/bots). Replaced by:
  - **J6 — Slack-self comms channel.** Create `#artemis` dedicated channel; she pushes proactive cards there as messages; thread support for ongoing topics. Uses J1 Slack integration; no new infra. *V1 path for "send to my channel" surfaces.*
  - **J7 (Phase L stretch) — iMessage** for personal variant. Mac-only via AppleScript. Implements `imessage_send_message` (Layer 3 — always confirms in V1). Only available when `ARTEMIS_DEPLOYMENT_MODE=personal`.

**Posture for ALL J slices — "draft, never send" until trust is built.** Every send/schedule/post/create tool starts at Layer 3 with explicit operator confirmation. Per-tool flip to Layer 2 (silent execution + after-the-fact mention) only via explicit config when operator trusts that specific action.

Each slice ports the Node source module's auth + API client + the small tool functions. Tests mock the external services. Per integration: ~150-300 LOC + ~15 tests.

### Phase K — UI/UX polish (design system)

The Python rebuild copied the Node frontend verbatim in E1; it inherits the same visual sprawl. K turns it into a coherent design system.

**K1 — Style-board reference** (pre-design, can ship before backend slices complete). A Sonnet sub-agent inventories every UI primitive in `public/css/` + `public/index.html`. Produces `public/style-board.html` (rendered reference) + `public/style-board.md` (index with inconsistency flags). Jon imports the HTML into Figma and designs the v1 design system: colors, typography scale, spacing scale, button hierarchy, card variants, etc.

**K2 — Design tokens** (after K1 + Jon's design). Jon exports tokens from Figma (CSS variables, JSON, or shared Figma link). A Sonnet sub-agent translates to `public/css/tokens.css` (CSS custom properties for color / font / spacing / radius / shadow).

**K3 — Component restyling + CSS cleanup** (parallel sub-agents per feature area, after K2). **Jon's directive 2026-05-17: K3 is restyle + DELETE.** End state is the minimum number of stylesheets, all using design tokens from K2, no duplicates. The 8 status-chip implementations collapse to one. The 163 hex literals collapse to ~15-20 token references. After K3 lands, unused CSS files get DELETED — not left as cruft. Single source of truth; future updates are cleaner and easier. Multiple Sonnet sub-agents in parallel:
- K3a — marketing-os surfaces
- K3b — builders surfaces
- K3c — memory shell + observations
- K3d — operations integrations (when Phase J lands)
- K3e — floating panel
- K3f — global chrome (top nav, side nav, modals, base typography)

Each consumes `tokens.css` + the Figma spec, rewrites that area's CSS to match. Smoke tests confirm nothing layout-broke. No backend changes.

**K4 — Visual regression baseline** (optional, may defer). After K3 lands, capture screenshots of every surface; lock them as visual baselines so future changes can diff against.

### Phase L — Personal variant + OKR sync-back

The packagable version. Same codebase, feature-flagged.

**L1 — Deployment-mode flag** — `ARTEMIS_DEPLOYMENT_MODE = marketing | personal`. In personal mode, marketing-OS routes + scout schedulers don't mount; `/api/_status` reports those surfaces as unavailable; frontend gates accordingly; Floating Artemis's tool registry auto-filters.

**L2 — OKR sync-back protocol** — daily job in personal instances that exports `okr:*` observations (memory archive format) and POSTs to the main marketing instance's import endpoint. The main instance's keystone receives scoped to that user. No new tables; uses keystone export/import.

**L3 — Installer / packaging** — one-command install for a personal user. Probably `pipx install artemis-os` or a Docker image. Sets up Postgres locally, runs migrations, scaffolds `.env`. Decided when Phase L is on deck.

### Phase J/K/L sequencing relative to V1

V1 (B-H) ships first. After cutover (H), the order is flexible:

- **K1 (style-board) can run anytime** — it's read-only inventory, no dependencies. **Best to ship K1 in parallel with the late V1 slices** so Jon can start designing while the backend completes.
- **K2 + K3 wait on Jon's Figma work** — Jon's calendar drives this.
- **Phase J slices run independently** — each is a small Sonnet sub-agent slice with no cross-dependencies. They can ship one per week in real use.
- **Phase L waits on Phase J + K3 + cutover** — the packagable version doesn't make sense until the operations integrations exist and the design is locked.

---

## Updated "What we ask Jon for"

| Moment | Ask |
|---|---|
| End of Phase A | Repo name. Default `artemis-os` if no response in 30 min. |
| End of Phase B | Quick "looks right" check on retrieval quality results vs. Node baseline. |
| End of Phase D | Approval to point a scout at real sources (a "go live" moment for that signal stream). |
| End of Phase E | Creative Director eye on the UI port — anything visually wrong that should be fixed now while it's cheap. |
| Pre-G | Sign-off on the floating Artemis design + personality profile. |
| Phase H | Cutover decision: ready to switch your daily-use Artemis from Node to Python? |
| Phase K1 done | Pick up the style-board in Figma; design the v1 design system. Then return design tokens for K2/K3. |
| Phase L | "Want to package the personal variant?" Triggers L1/L2/L3 slices. |

Everything else: we just do it.
