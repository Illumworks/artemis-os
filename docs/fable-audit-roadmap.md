# Artemis OS — Fable Full-System Audit Roadmap

Source: Fable 5 audited all 10 subsystems (2026-06-25). Every item Opus spot-checked
against real code held up. Status legend: **[VERIFIED]** Opus traced it; **[HIGH-CONF]**
Fable-reported, verify before build. Effort S/M/L, Impact L/M/H.

Sequencing principle: security → high-impact user-facing bugs → durable upgrades → big bets.
Pick the first production slice from Tier 1.

---

## TIER 0 — Security

### Shipped 2026-06-25 (main 78dcf0d, live)
- ✅ MCP subprocess memory-scope spoof (owner-private memory reachable by a non-owner) — subprocess now binds to the live caller's identity; PATCH /sessions agent_id locked.
- ✅ /api/integrations management endpoints owner-gated (were fully open).
- ✅ CORS wildcard+credentials pinned to app_base_url.

### Fast-follows (deferred, do next — carefully tested)
| Item | What | Effort | Impact |
|---|---|---|---|
| Prod fail-open guard | Startup assertion: env==production ⇒ cf_access_enabled + team_domain + aud, else fail hard. Prevents the whole app silently running unauthenticated on a misconfig. | S | H |
| WS auth under CF Access | `ws/routes.py` skips auth entirely when `ARTEMIS_TOKEN` unset (the CF-Access norm) — verify the CF JWT / per-run token on WS connect. | S | H |
| SSRF egress guard | Scout/Argus/pdf fetches (`_http.py`, `argus/research.py`, `pdf_extractor.py`) fetch attacker-influenced URLs with no private-IP/loopback block; add an egress allowlist + defusedxml. | S | M |
| Frontend XSS sweep | `escapeHtml` (utils.js:3) doesn't escape quotes; used in attribute position across composer/home/sessions → repo-wide XSS. Unify `escapeHtml`/`escapeAttr`/`safeUrl`, fix markdown link-scheme, set Mermaid `securityLevel:'strict'`. | M | H |

---

## TIER 1 — High-impact fixes (recommended first slice; mostly small)
| # | Item | Plain English | Effort | Impact | Status |
|---|---|---|---|---|---|
| 1 | **Artemis speaker attribution** | She proposes meetings for things *other people* said they'd do. The scheduling sweep drops the "who owns this" (post_meeting_scheduling.py:778); gate on owner-is-Jon (pattern already exists in commitments.py:251) + fix the summarizer to map `Me:`=Jon. | S | H | HIGH-CONF |
| 2 | **Writing Studio: drag-drop won't stick** | A "repair" running inside the page-load endpoint overwrites your manual folder moves every refresh (invoke.py:833). Skip rows that already have a folder; stop mutating on GET. | S | H | HIGH-CONF |
| 3 | **Writing Studio: selection/rewrite dead** | Highlight lost on focus change; the result popup positions off a cleared selection and renders off-screen (composer-v5.js). Add an inline selection decoration + position from the captured range; fix the chat-rewrite path + quote-escaping. | M | H | HIGH-CONF |
| 4 | **Memory penalizes its own learning** | Consolidated (merged, higher-confidence) memories get written at confidence 0.5 / evidence 1, so they rank *below* raw notes (consolidator.py:500). Propagate real confidence + evidence (unused `corroborate_confidence` looks purpose-built). | S | H | VERIFIED |
| 5 | **Marketing gate timeouts never fire** | Approval auto-approve/escalation after 72h is registered on a scheduler that isn't running in the pipeline subprocess → runs hang in "awaiting_approval" forever. Make gate deadlines DB-backed + swept by the web scheduler. | S–M | H | HIGH-CONF |
| 6 | **Scout fleet output is silently rejected** | Scout findings lack the fields the ingest validator requires + 6 of 9 scout types aren't registered → every finding is dropped. Canonical Finding contract + in-repo package registry. (Unblocks the board-minutes scout too.) | M | H | HIGH-CONF |

---

## TIER 1.5 — Forge (HAND TO the app-seat Opus — his active area)
| Item | What | Effort |
|---|---|---|
| Merge to wrong branch | `merge_worktree` doesn't `git checkout base` first — can land Ares's work on whatever branch is checked out while reporting "main" (dev_projects.py:851). | S |
| Zombie runs on restart | `running` runs never reconciled; `startup_sweep` exists but isn't wired into boot. | S |
| Uncommitted worktree edits lost | Review/merge only see committed work; auto-commit or block merge on dirty. | S |
| Real sandbox (later) | Write-mode Bash under bypassPermissions can escape the worktree; human merge-gate is the only real backstop today. | L |

---

## TIER 2 — Medium upgrades
- **Memory:** wire `consolidate_near_duplicates` (built+tested, never called); fix hit_count pollution (record_usage=False missing, incremental_consolidator.py:163); hybrid retrieval — union vector+keyword pools (fixes "exact name buried below vector cousins", the Sara/Lectura class) instead of vector-only-then-fallback.
- **Named agents:** declarative `AgentSpec` (one file to add Tyche/Hestia instead of six); fail-CLOSED tool registry for unknown agent_id (currently unknown agent → full action toolset); owner-only confirmation for layer-3/4 on owner-credentialed tools.
- **Writing Studio:** promote folder_id/archived/title out of JSONB into columns (fixes a pagination bug); stream span rewrites (SSE ghost-text) instead of a 10–30s dead wait; split composer-v5.js (5.2k lines).
- **Artemis:** implement or retract "yes 2" slot-pick (today may book the WRONG time); single GCal client factory (removes a known 401 from the execute path); radar auto-mute feedback loop.
- **Marketing pipeline:** fix `_push_already_sent` join bug (dedup works only via a swallowed exception); atomic run-lock (partial unique index) to close the concurrent-run race; parallel branch execution (scout cycle hours → ~15 min).
- **Scouts/Argus:** decouple Argus research from Slack delivery (the `no_channel_resolved` drop — research is lost today); persistent per-source dedup cursor.
- **Screen-Time:** fix LegiScan status mapping — vetoed/failed bills currently read as "passed" and would fire false alerts once the cron is wired; then wire the parked cron/digest/seed (fix the display-only pipeline flag first) after Angela signs off.
- **Kai:** delete the superseded `sync.py` dual-writer; guard `full_refresh` against an empty batch wiping a source; archived-row filter on exact lookup.
- **Frontend:** view registry + mount/unmount contract (collapses the 4-wiring-points footgun); route-based code-splitting (~1 MB off first paint); nav a11y (real buttons + aria-current).

---

## TIER 3 — Big bets
- Memory embedding refresh (MiniLM-L6-v2 → modern 384-dim e.g. bge-small) via the reserved `_MODEL_VERSION` slot + eval A/B harness.
- Forge OS-level sandbox; the sub-agent cost fleet (Codex/local-LLM delegation — Brief 3, unbuilt).
- Board-meeting-notes scout (peer-validation, nationwide, BoardDocs-first) — blocked on the Salesforce customer list (Neil) + needs the scout-contract fix (#6) + agenda-body retrieval + LLM sentiment + customer-exclusion.

---

## Dead-code cleanup (one batch PR)
`write_observation_with_conflict_check` (memory, dead); `try_apply_proposed_action_reply` (agency_gate, dead dup); `filter_by_surfaces`/`_extract_surface_tag` (authority); enablement `sync.py`; `_GATE_MODEL`; composer `customAskActive` no-ops + orphan CSS; 8 divergent `escapeHtml` copies (also a security fix); assorted doc drift.
