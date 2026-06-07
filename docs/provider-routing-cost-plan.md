# Provider routing & token-usage work philosophy

**Date:** 2026-06-06
**Author:** Opus 4.7 (1M) — terminal-Lead
**Brief:** `briefs/provider-routing-cost-audit.md`
**Type:** Audit + plan. No code in this pass. Jon + Lead pick keep-vs-offload calls from this report.
**Companion (potentially superseded):** `briefs/cost-prereq-multi-provider-activation.md` from the prior session. That brief proposed Gemini Flash for trajectory + memory consolidation. This audit's Tier 3 now favors **LM Studio first**, Gemini second — see Section 9.

---

## Exec summary (the 5 ranked quick wins)

The lever today is **throughput**, not dollars (everything's on subscriptions, all API keys empty). You've already hit Claude concurrency limits. The five moves below free Claude capacity without degrading customer-facing quality.

| # | Move | Why | Effort | Quality risk |
|---|---|---|---|---|
| 1 | **Defensive fix bundle**: repoint 2 broken `anthropic`-provider agents to `claude-code`; refactor 3 hardcoded `AnthropicAdapter()` call sites to use `resolve_adapter`; set a fallback on Mock Post Gate (NULL today) | Five latent bugs that fail with empty `ANTHROPIC_API_KEY`. Graph extractor's failure is the root cause of zero entities/relations in memory (238 observations stuck at `graph_status IS NULL`). | Small | None (fixes failures) |
| 2 | **Put `codex` CLI on PATH** (symlink from `/Applications/Codex.app`) | Codex.app is installed but the binary isn't on PATH. Until it is, Tier 2 of the routing strategy is theoretical. One-line install. | Tiny | None |
| 3 | **Route memory consolidator to LM Studio `qwen3-14b`** | Highest-volume internal task. Pure summarization. Output is internal (no customer surface). LM Studio = zero marginal cost + zero Claude load. Validate on 5 sample scopes before full rollout. | Small (config flip) | Medium (validate first) |
| 4 | **Route trajectory summarizer to LM Studio `qwen3-14b`** | Post-agent-run background analysis. Output is internal context for memory. Same reasoning as #3, even safer because it's post-hoc. | Small (config flip) | Low |
| 5 | **Route District Classifier scout to LM Studio `qwen3-14b`** | Pure classification (district name → bucket). Perfect Tier 3 task. Saves a high-volume scout from Claude rate budget. | Small (per-agent `provider` flip in DB) | Low (verify accuracy on a 20-row sample) |

Total expected impact of #3 + #4 + #5: shifts ~30-40% of Claude-code call volume off the subscription, onto local GPU. No customer-facing surface changes.

Two larger follow-ons not in the top 5 — Section 6 ranks the next ten.

---

## 1. Current state (verified 2026-06-06)

### CLI / adapter reality

| Adapter | Status | Notes |
|---|---|---|
| **claude-code (CLI)** | ✅ Working | `/Users/artemis/.local/bin/claude` v2.1.159. Default cascade primary. |
| **codex (CLI)** | ⚠ Installed, not on PATH | `/Applications/Codex.app` exists, `which codex` fails. Binary inside the .app bundle; needs symlink. Tier 2 is **gated on this**. |
| **lm-studio** | ✅ Live | `http://127.0.0.1:1234` responsive. Two usable chat models: `qwen/qwen3-14b` (general), `qwen/qwen2.5-coder-14b` (code-oriented). Embedding model also loaded. |
| **anthropic (SDK)** | ❌ Broken (no key) | `.env` has `ANTHROPIC_API_KEY=<empty>`. Any direct-instantiation call site fails. |
| **openai (SDK)** | ❌ Broken (no key) | `.env` has `OPENAI_API_KEY=<empty>`. |
| **gemini** | ❌ Not configured | `GEMINI_API_KEY` not in `.env` at all. Adapter code exists; would need a key. |
| **openrouter** | ❌ Not configured | Same as gemini. |

**Translation:** today only `claude-code` (subscription) + `lm-studio` (local) are reachable. Codex unblocks with a PATH fix. Anthropic/OpenAI/Gemini SDKs all need a key flip. Build the routing now; activate keys later for API billing.

### Agent provider/model distribution

20 agents total. Reading from `artemis_os.agents` table:

| Provider | Count | Notes |
|---|---|---|
| `claude-code` | 18 | Healthy — primary path, all have `anthropic` as fallback except Mock Post Gate #172 (NULL fallback) |
| `anthropic` | 2 | **Broken** with empty key: Smoke Test Agent #2, WS Integration Agent #1. Both on `claude-sonnet-4-6`, both NULL fallback. |

Model distribution:
- `claude-haiku-4-5`: 14 agents (cheap reasoning, classifications, scouts)
- `claude-sonnet-4-6`: 6 agents (heavier reasoning — Asset Selector, Brief Composer, Cross-Reference, Ruleset Manager, Writing Studio Adapter, plus the 2 broken anthropic-provider ones)

No agent uses Opus. Sensible.

### Latent bugs (broken call sites)

Five issues found that already produce silent failures or would fail under any load:

1. **Smoke Test Agent #2** — `provider=anthropic`, no fallback, empty key → fails on first invocation.
2. **WS Integration Agent #1** — same shape, same failure mode.
3. **`artemis/memory/graph_extractor.py:144`** — direct `anthropic.AsyncAnthropic()` call with hardcoded `claude-haiku-4-5-20251001`. **This is the root cause of the memory-session finding that 238 observations have `graph_status IS NULL` and 0 entities/relations exist.** Every consolidation that should have triggered graph extraction has silently failed because the SDK call has no key.
4. **`artemis/builders/workflow_executor.py:63`** — `AnthropicAdapter()` direct + hardcoded `claude-sonnet-4-6`. Workflow steps fail when triggered.
5. **`artemis/floating_artemis/tools/core.py:366`** (`spawn_subagent`) — `AnthropicAdapter()` direct. Fails when invoked.

Plus a near-miss:

6. **Mock Post Gate #172** — `provider=claude-code` (working) but `fallback_provider=NULL`. If claude-code is rate-limited or unreachable, this agent dies with no fallback.

All six should be fixed in the defensive bundle (Quick Win #1).

### Out-of-policy inline cascades

Five locations duplicate the resolver's cascade logic inline instead of calling `resolve_adapter()`:

- `artemis/floating_artemis/chat.py:294,348` — custom session cascade
- `artemis/brief/generator.py:38,49` — inline `claude-code > codex > lm-studio > anthropic`
- `artemis/meetings/summarizer.py:309` — same inline pattern
- `artemis/builder/trajectory_summarizer.py:388` — same
- `artemis/builder/agent_builder.py:559`, `artemis/builder/routes.py:63`, `artemis/routes/okr.py:486,555`, `artemis/routes/meetings.py:637` — same

These aren't broken, but if the default cascade ever changes (e.g., we add OpenRouter), drift between these locations becomes a maintenance trap. Worth a separate cleanup pass to centralize.

---

## 2. The 3-tier philosophy (locked)

This is the framing for every routing decision below.

### Tier 1 — Claude (claude-code today; Anthropic API later)

**Use when:** the output is customer-facing OR the decision gates downstream work OR the structured-output shape is non-trivial and other tiers fail it.

**Includes:** customer-visible writing (briefs, drafts, content agents), agent reasoning (scout judgment, qualifier brief composition), the builder (creating agents/skills), Floating Artemis chat (user-facing), Writing Studio.

**Models:** `claude-sonnet-4-6` for heavy reasoning, `claude-haiku-4-5` for cheap-but-Claude-quality.

**Subscription cap:** claude-code subscription has concurrency limits we've already hit. Tier 1 work *should* stay on Claude even when cap is tight — solve the bottleneck by moving Tier 2/3 work off Claude, not by downgrading Tier 1.

### Tier 2 — Codex / OpenAI

**Use when:** code-shaped task (JSON proposals, structured extraction, deterministic transformations) AND Tier 3 quality is borderline OR Codex CLI is preferable to bring in a second free subscription.

**Includes:** subagent spawning, pipeline canvas AI assistant, OKR structured extraction, dev-projects loop runner.

**Today:** Codex CLI not on PATH → blocked. After PATH fix: Codex CLI is a free second subscription. OpenAI SDK behind it once key is set.

**Models:** Codex picks per task; if/when OpenAI key is set, `gpt-5-mini` for cheap, `gpt-4o` for default.

### Tier 3 — Local (LM Studio) + Gemini

**Use when:** trivial / high-volume / internal / latency-tolerant. Summarization, classification, extraction, normalization, dedup, background batch work.

**Includes:** memory consolidator, trajectory summarizer, meeting summarizer, daily brief generator, graph extractor (after fix), classifier scouts (district classifier), low-stakes helpers (OKR suggest_kr_progress).

**Today:**
- LM Studio is live with `qwen/qwen3-14b` (general) + `qwen/qwen2.5-coder-14b` (code/JSON-shaped). $0 marginal cost.
- Gemini free-tier API is available to Jon (no key currently in `.env` but accessible). Rate-limited (~15 RPM / 1M TPM / 1500 RPD on 2.0 Flash) but generous for Tier 3 task volumes.

**Tier 3 is a cascade, not a single provider.** Decision between LM Studio and Gemini Flash depends on the task shape, not blanket preference:

| Task shape | Cascade order | Why |
|---|---|---|
| Privacy-sensitive (internal context, observations) + simple structure + plain-text output | **LM Studio → Gemini Flash** | Local privacy, free at scale, qwen3-14b handles plain summaries well |
| Strict JSON schema (graph extraction, structured proposals) + concurrent bursts | **Gemini Flash → LM Studio** | Gemini hardened for JSON adherence (drifts <1% vs qwen3-14b drifts 5-15%); free tier handles parallel requests; LM Studio queues serially on single-GPU mini |
| Long context (>32K tokens — meeting transcripts, multi-doc briefs) | **Gemini Flash → LM Studio** | Gemini Flash has 1M context window; LM Studio limited by local model size |
| Pure classification (label → bucket, tag → category) | **LM Studio → Gemini Flash** | Simple task; LM Studio is faster locally; free at any volume |
| Low-volume helper (daily brief, OKR suggest, occasional summary) | **LM Studio → Gemini Flash** | Volume sits comfortably under any rate limit either way; local preferred |

**The under-recognized LM Studio bottleneck:** the Mac mini does single-stream inference. When memory consolidator fires for 3 scopes simultaneously, LM Studio serializes them (30-60s each = wall clock 90-180s). Gemini Flash parallelizes (1-2s each = ~2s total). Given the stated Claude concurrency pain, moving concurrent Tier 3 work to LM Studio doesn't solve concurrency — it relocates it.

**Gemini free-tier failure modes:** 429 rate-limit responses are real. Any Gemini-first cascade must handle 429 cleanly by falling back to LM Studio (or claude-code haiku if both Tier 3 options are exhausted).

---

## 3. Full LLM call-site inventory

24 production LLM call sites. (Signal qualifier confirmed deterministic — excluded.)

| # | Feature | File:line | Current call | Current model | Hard-coded? | Vol | Lat | Qual | Cust? | Why current |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Memory consolidator | `artemis/memory/consolidator.py:186` | `resolve_adapter("claude-code")` | `claude-haiku-4-5-20251001` | Yes | High (per scope batch) | M | H | No | Haiku judged enough for consolidation |
| 2 | Builder agent executor | `artemis/builders/executor.py:298` | `resolve_adapter(agent.provider, agent.fallback)` | `agent.model` | Config | High (per run) | H | H | Yes | Agent rows drive routing |
| 3 | Floating Artemis chat | `artemis/floating_artemis/chat.py:294,348` | Inline cascade `[session.provider, claude-code, codex, lm-studio, anthropic]` | `session.model` | Inline | High (per turn) | H | H | Yes | User-configurable per session |
| 4 | Daily brief generator | `artemis/brief/generator.py:38,49` | Inline cascade | `claude-haiku-4-5-20251001` | Inline + hardcoded | Low (1/day) | L | H | Yes | Haiku for summary digests |
| 5 | Pipeline canvas AI | `artemis/pipelines/routes.py:61,66` | `resolve_adapter()` (default cascade) | Default per adapter | Yes | M (per UI turn) | M | M | Yes | UI proposals, user accepts/rejects |
| 6 | Pipeline agent node | `artemis/pipelines/node_executors/agent_executor.py:286` | `resolve_adapter(agent.provider, agent.fallback)` | `agent.model` | Config | H | H | H | Yes | Agent rows drive routing |
| 7 | Spawn subagent | `artemis/floating_artemis/tools/core.py:366` | **`AnthropicAdapter()` direct** | Param default sonnet-4-6 | **Hardcoded** | Rare | M | H | Yes | **Anti-pattern: hardcoded, broken with empty key** |
| 8 | Campaign brief assembler | `artemis/marketing/brief_assembler.py:447` | `resolve_adapter("claude-code", "anthropic")` | `claude-haiku-4-5` | Yes | M (per candidate) | M | H | Yes | Haiku for proposal JSON |
| 9 | Scout signal intake | `artemis/marketing/scout_runner.py:180` | Inline cascade per agent.provider/fallback | `agent.model` | Config | **Very high (50-500 / run)** | L | H | Yes | Per-agent driven |
| 10 | Writing studio compose | `artemis/marketing/routes/writing_studio.py:470` | `resolve_adapter(profile.default_model_provider)` | `profile.default_model_id` | Config | M | H | H | Yes | Writing profile drives |
| 11 | Campaign initiation HTTP | `artemis/marketing/routes/initiation.py:630` | `resolve_adapter("claude-code", "anthropic")` | `claude-haiku-4-5` | Yes | M | M | H | Yes | Haiku for proposal JSON |
| 12 | Meeting summarizer | `artemis/meetings/summarizer.py:309` | Inline cascade | provider default | Inline | L (per meeting end) | L | M | Yes | Background tick after meeting |
| 13 | Trajectory summarizer | `artemis/builder/trajectory_summarizer.py:388` | Inline cascade | provider default | Inline | L (per agent run) | L | M | No | Post-hoc; feeds memory |
| 14 | Memory graph extractor | `artemis/memory/graph_extractor.py:144` | **`anthropic.AsyncAnthropic()` direct** | `claude-haiku-4-5-20251001` | **Hardcoded** | M (per observation) | L | M | No | **Anti-pattern + currently broken** |
| 15 | Builder propose_agent | `artemis/builder/agent_builder.py:559` | Inline cascade | not specified | Inline | Rare | M | H | Yes | Agent definition; user waits |
| 16 | Builder propose_skill | `artemis/builder/routes.py:63` | Inline cascade | not specified | Inline | Rare | M | H | Yes | Skill definition; user waits |
| 17 | OKR suggest_kr_progress | `artemis/routes/okr.py:486` | Inline cascade | not specified | Inline | Rare | M | L | Yes | Optional helper |
| 18 | OKR extract_activity | `artemis/routes/okr.py:555` | Inline cascade | not specified | Inline | Rare | M | M | Yes | On-demand extraction |
| 19 | Meetings Q&A | `artemis/routes/meetings.py:637` | Inline cascade | not specified | Inline | Rare | M | H | Yes | User question on transcript |
| 20 | Dev projects loop | `artemis/dev_projects/loop_runner.py:236` | `get_adapter(provider)` | `session.model` | Config | M | H | M | Yes | User-selected provider |
| 21 | Workflow executor | `artemis/builders/workflow_executor.py:63` | **`AnthropicAdapter()` direct** | **hardcoded `claude-sonnet-4-6`** | **Hardcoded** | M | M | H | Yes | **Anti-pattern + broken with empty key** |
| 22 | MCP tool sandbox | `artemis/tools/mcp_server.py:769` | `get_adapter(candidate)` loop | not specified | Cascade | M | M | H | Yes | Tool safety eval |
| 23 | Agent client (SDK abstraction) | `artemis/agent/client.py:114,133` | `AsyncAnthropic()` — wrapped by `AnthropicAdapter` | per-call kwargs | (abstraction) | n/a | n/a | n/a | n/a | This IS the adapter; not a direct call site |
| 24 | Background graph retry helper | `artemis/memory/graph_extractor.py` retry loop | Same as #14 | Same | Same | Same | Same | Same | Same | Same issue |

**Volume legend:** Low = <10/day, Medium = 10-100/day, High = 100-1000/day, Very high = 1000+/day under normal load.

---

## 4. Proposed tier per call site

Conservative: anything customer-facing or decision-gating stays Tier 1 unless explicitly tested and proven safe in Tier 2/3.

| # | Feature | Current | Proposed tier | Proposed concrete routing | Rationale |
|---|---|---|---|---|---|
| 1 | Memory consolidator | T1 (claude-code haiku) | **T3** | **Gemini Flash → LM Studio `qwen3-14b`** → claude-code haiku | Strict JSON schema for consolidation proposals; concurrent bursts when multiple scopes hit threshold simultaneously. Gemini's JSON adherence + parallelism wins here. **Quick win #3.** |
| 2 | Builder agent executor | T1/T2/T3 (per agent) | **T1 (keep)** for customer-facing agents; T3 for classifier agents | Per-agent provider column drives | Stay agent-driven; move individual classifier agents to T3 separately (e.g. District Classifier — quick win #5) |
| 3 | Floating Artemis chat | T1 (claude-code) | **T1 (keep)** | Centralize in resolver instead of inline cascade; default claude-code | Customer-facing chat. Quality-critical. User can already override per session. |
| 4 | Daily brief generator | T1 (claude-code haiku) | **T3** | **LM Studio `qwen3-14b` → Gemini Flash** → claude-code haiku | Summary digest, low volume (1/day), low quality risk. Output internal-facing for Jon. Local is plenty. |
| 5 | Pipeline canvas AI | T1 (default cascade) | **T2** | Codex (once on PATH), fallback claude-code | UI proposal — accept/reject — moderate stakes. Codex strong for code-shaped UI ops. |
| 6 | Pipeline agent node | T1/T2/T3 (per agent) | **T1 (keep)** | Agent rows drive | Same as #2 |
| 7 | Spawn subagent | **Hardcoded T1, broken** | **T1 (claude-code)** | Refactor to resolve_adapter. **Defensive fix #1.** | Operator-triggered; quality matters |
| 8 | Campaign brief assembler | T1 (claude-code haiku) | **T1 (keep)** | claude-code haiku | Customer-facing campaign briefs. Quality-critical. |
| 9 | Scout signal intake | T1/T2/T3 (per agent) | **T1 (keep) for judgment scouts; T3 for classifier scouts** | Per agent.provider | Qualifier reasoning = T1. Pure classification = T3. **Quick win #5 = District Classifier scout to T3.** |
| 10 | Writing studio compose | T1 (profile-driven) | **T1 (keep)** | Profile drives | Customer-facing drafts. |
| 11 | Campaign initiation HTTP | T1 (claude-code haiku) | **T1 (keep)** | claude-code haiku | Customer-facing proposals |
| 12 | Meeting summarizer | T1 (claude-code) | **T3** | **Gemini Flash → LM Studio `qwen3-14b`** → claude-code haiku | Long-context (meeting transcripts may exceed 32K tokens); Gemini's 1M context wins. |
| 13 | Trajectory summarizer | T1 (claude-code) | **T3** | **LM Studio `qwen3-14b` → Gemini Flash** → claude-code haiku | Post-hoc, low-stakes, simple structure. Local fits well. **Quick win #4.** |
| 14 | Memory graph extractor | **Hardcoded T1, broken** | **T3** | **Gemini Flash → LM Studio `qwen3-14b`** → claude-code haiku. Refactor away from direct SDK. **Defensive fix #1.** | Strict JSON schema for entities/relations; Gemini's adherence is the key advantage here. Internal-facing, no customer surface. |
| 15 | Builder propose_agent | T1 (default cascade) | **T2** | Codex (once on PATH), fallback claude-code | Code/JSON-shaped output; user is operator |
| 16 | Builder propose_skill | T1 (default cascade) | **T2** | Same | Same |
| 17 | OKR suggest_kr_progress | T1 (default cascade) | **T3** | **LM Studio `qwen3-14b` → Gemini Flash** → claude-code | Low-stakes helper, low volume, plain output |
| 18 | OKR extract_activity | T1 (default cascade) | **T2** | Codex (once on PATH), fallback claude-code | Structured extraction; code-shaped |
| 19 | Meetings Q&A | T1 (default cascade) | **T1 (keep)** | claude-code | User-asked Q&A; quality matters |
| 20 | Dev projects loop | T1/T2/T3 (per session) | **T2 default** | Codex when available; session can override | Code-shaped sandbox; matches dev intent |
| 21 | Workflow executor | **Hardcoded T1, broken** | **T1 (claude-code)** | Refactor to resolve_adapter. **Defensive fix #1.** | Customer-facing workflow steps |
| 22 | MCP tool sandbox | T1 (default cascade) | **T2** | Codex, fallback claude-code | Tool eval; code-shaped |

Net move: 6 sites go T1 → T3 (consolidator, brief, meeting, trajectory, graph extractor, OKR suggest); 4 sites go T1 → T2 (pipeline canvas, builder propose_agent, builder propose_skill, OKR extract, MCP sandbox, dev projects); 12 sites stay T1 (the customer-facing or decision-gating work). District Classifier scout becomes a T3 move within the per-agent config layer.

---

## 5. Token-usage work philosophy

**The rule:** *Reasoning, quality, customer-facing → Claude. Everything else → cheapest tier that holds quality.*

**Default for new work:** Tier 3 (LM Studio or Gemini Flash). Escalate if quality requires.

**Promotion ladder when designing a new call:**

1. **Pick the Tier 3 cascade for the task shape:**
   - Plain text out, simple structure, privacy-sensitive, or pure classification → **LM Studio first**
   - Strict JSON schema, concurrent burst, or long context (>32K) → **Gemini Flash first**
   - The other Tier 3 option is the fallback in both cases.
2. **If Tier 3 fails on structured shape or accuracy, can Codex (once on PATH) handle it?** If yes → Tier 2.
3. **Only if Tier 2 + Tier 3 both fail OR the output is customer-facing OR the decision gates downstream work** → Tier 1.

**Quality validation discipline (mandatory before any T1 → T3 move):**

- Run the same prompt against both the current Tier 1 model and the candidate Tier 3 model on 5-20 representative samples.
- Score by hand: does the output meet the same correctness bar?
- If yes, ship the move. If borderline, try Tier 2. If still borderline, stay Tier 1.

**Permanent Tier 1 list (never downgrade):**

- Agent reasoning that drives decisions (scout judgment, qualifier brief composition)
- Writing Studio compose
- Campaign briefs, drafts, content agents
- Floating Artemis chat (user-facing)
- Builder agent/skill creation (operator-facing creation flow)

**Always-OK Tier 3 list (won't degrade UX even on a bad day):**

- Memory consolidation, graph extraction (**Gemini-first** — JSON discipline + burst tolerance)
- Trajectory summaries (**LM-Studio-first** — post-hoc, low-stakes, simple structure)
- Meeting summaries (**Gemini-first** — long context wins)
- Daily brief assembly (**LM-Studio-first** — low volume, plain output)
- Pure classification scouts — district classifier, content type taggers (**LM-Studio-first** — high volume, simple task)
- OKR optional helpers (**LM-Studio-first**)

**The discipline outlives this audit:** when designing a new feature, the first question isn't "which model do I use" — it's "which tier does this belong in." Then pick the cheapest provider in that tier that works.

---

## 6. Ranked next-tier moves (after the top 5)

If the top 5 land cleanly, these are the next ten ranked by safety × volume × effort:

| # | Move | Tier change | Effort | Notes |
|---|---|---|---|---|
| 6 | Daily brief generator → LM Studio `qwen3-14b` | T1 → T3 | Small | Validate on 3 days of brief output |
| 7 | Meeting summarizer → LM Studio `qwen3-14b` | T1 → T3 | Small | Validate on 3 meeting transcripts |
| 8 | OKR suggest_kr_progress → LM Studio | T1 → T3 | Small | Low-stakes; ship if `qwen3-14b` returns valid JSON |
| 9 | Pipeline canvas AI → Codex (after PATH) | T1 → T2 | Small | Quality should match Claude for UI proposals |
| 10 | OKR extract_activity → Codex (after PATH) | T1 → T2 | Small | Structured extraction; Codex strong here |
| 11 | Builder propose_agent → Codex | T1 → T2 | Small | Frees claude-code for actual agent runs |
| 12 | Builder propose_skill → Codex | T1 → T2 | Small | Same |
| 13 | MCP tool sandbox → Codex | T1 → T2 | Small | Tool safety eval; code-shaped |
| 14 | Dev projects loop → Codex default | T1 → T2 | Small | Already overridable per session |
| 15 | Centralize the 5 inline cascades into `resolve_adapter()` | refactor | Medium | Drift cleanup; no behavior change |

---

## 7. API-readiness note

If `.env` keys were activated today and the app shifted to per-token API billing, the routing strategy above translates to material savings. Rough numbers using current call-site shapes + estimated volumes:

**Without routing (all T1 at Anthropic Sonnet 4.6 rates):**
- Customer-facing + reasoning: ~$60-90/mo (current MTD trajectory from the cost-page session: $87.40)
- Background/internal/classification: ~$30-50/mo at Sonnet rates
- **Total: ~$90-140/mo**

**With routing (this plan):**
- Tier 1 stays Anthropic: ~$60-90/mo (unchanged)
- Tier 2 (Codex CLI subscription): **$0** (subscription, no per-token API)
- Tier 3 (LM Studio): **$0** (local inference)
- Tier 3 (Gemini fallback for borderline cases): ~$2-5/mo at Flash rates
- **Total: ~$62-95/mo**

**Savings: ~30-40% of API-billed total** by moving Tier 3 work to LM Studio + Tier 2 to Codex. The actual lever today isn't dollars (subscriptions absorb the cost) — it's **freeing Claude concurrency**. Same routing, two payoffs.

**Provider rate snapshot (per million tokens, as of 2026-06-06):**

| Provider · Model | Input | Output | Tier fit |
|---|---|---|---|
| Anthropic Opus 4.7 | $15 | $75 | T1 only (we don't use; sensible) |
| Anthropic Sonnet 4.6 | $3 | $15 | T1 heavy reasoning |
| Anthropic Haiku 4.5 | $0.80 | $4 | T1 cheap reasoning |
| OpenAI GPT-4o | $2.50 | $10 | T2 default (when key set) |
| OpenAI GPT-4o-mini | $0.15 | $0.60 | T2 cheap |
| OpenAI GPT-5-mini | $0.25 | $2 | T2 modern cheap |
| Gemini 2.5 Flash | $0.15 | $0.60 | T3 (when key set) |
| Gemini 1.5 Pro | $1.25 | $5 | T3 heavy |
| LM Studio (local) | $0 | $0 | T3 first choice |
| Codex CLI | (subscription) | (subscription) | T2 first choice |
| claude-code CLI | (subscription) | (subscription) | T1 first choice |

**Key insight:** Tier 3 at LM Studio is genuinely free at the margin. The marginal cost of moving memory consolidation from claude-code haiku to qwen3-14b is *zero dollars and zero subscription throughput consumed*. It's the highest-leverage move.

---

## 8. Surprises & out-of-policy findings

Beyond the latent bugs (Section 1), three things worth flagging:

1. **The cost-prereq brief from the prior session is now partially obsolete.** `briefs/cost-prereq-multi-provider-activation.md` proposed Gemini Flash for trajectory summarizer + memory consolidation. This audit's Tier 3 preference order is **LM Studio first**, Gemini second. When that brief becomes Worker-pickable, the routing should target LM Studio `qwen3-14b` instead of `gemini-2.5-flash`. Gemini is the *fallback* if LM Studio is unavailable. Lead should update the brief or write a new one.

2. **Memory graph extractor's silent failure is the root cause of the empty graph layer** the memory session uncovered. The brief at `briefs/memory-phase-5-prereq-graph-extractor-audit.md` proposed investigating the wiring; this audit nailed it: direct SDK call with empty key. The fix is in this report's defensive bundle. After fix + backfill, Phase 5 of the memory redesign (People & Things tab) unblocks.

3. **Five inline cascade duplications** create drift risk. Currently they all encode the same `claude-code > codex > lm-studio > anthropic` order. If we later add OpenRouter or change Tier 3 default, only one location updates and the others silently drift. Worth a cleanup pass to call `resolve_adapter()` everywhere with explicit overrides where needed.

---

## 9. What's NOT in this report

To stay honest:

- **No Worker briefs spawned in this pass.** This is a proposal for Jon + Lead to review. Once direction is approved, Lead breaks the moves into individual Worker briefs.
- **No code refactor.** Read-only investigation.
- **No commitment to specific .env activations.** Whether/when to enable OpenAI/Gemini/Anthropic API keys is a separate decision; the routing strategy works without them today (subscription-only).
- **No quality benchmarks.** Recommendations to use LM Studio `qwen3-14b` assume it can handle summarization quality. Each T1→T3 move needs validation on 5-20 samples before shipping; this audit doesn't pre-run those tests.

---

## 10. Decision asks

Four calls back to Jon before Lead spawns Worker briefs:

1. **Greenlight Quick Win #1 (defensive fix bundle)?** Five latent bug fixes, one PATH fix. No quality risk. Probably should go first to unblock graph extractor + close the broken-agent gap. Lead can spawn one Worker brief for the whole bundle.
2. **Greenlight Quick Wins #3-5 (LM Studio + Gemini offloads) one at a time, or as a bundle?** Each needs a 5-20 sample validation (3-way: claude-code vs LM Studio vs Gemini) before rollout. Lead can run validations as part of a single brief.
3. **Should the cost-prereq brief from the prior session be re-routed?** Per Section 8 finding #1: the original Gemini-first proposal is partially superseded — Tier 3 is now LM Studio + Gemini in task-aware cascades. Plus the DB-backed override mechanism in `briefs/routing-control-surface.md` makes the original `feature_cascades.py` config redundant. Recommend rewriting cost-prereq as a "seed initial overrides" brief once routing-control-surface lands.
4. **Greenlight the routing self-service surface?** Two new briefs from this session pair to give Jon DB-backed per-feature routing control without going through an agent:
   - **`briefs/routing-control-surface.md`** — foundation: `feature_routing_overrides` table + `provider_health` module + dedicated Routing page (profile menu). Worker brief is ~520 LOC + 2 migrations.
   - **`briefs/cost-phase-3-routing-opportunities.md`** (updated) — narrow recommendation UI with Apply buttons + availability filtering. Depends on the foundation. Worker brief is ~280 LOC.

   Order: routing-control-surface first (independent foundation), then cost-phase-3 (consumes it).

---

## Appendix — Validation script (for Quick Wins #3, #4, #5)

Before flipping any T1 → T3 routing, Lead runs a three-way comparison on 5-20 representative inputs:

```python
# pseudo — Lead writes the real script during Worker brief execution
inputs = sample_real_inputs_from_prod(n=20)  # e.g. real observations to consolidate
for inp in inputs:
    out_claude = claude_code_haiku(inp)
    out_lmstudio = lm_studio_qwen3_14b(inp)
    out_gemini = gemini_flash(inp)
    record(inp, out_claude, out_lmstudio, out_gemini)
# Manual review by Jon or Lead:
#   - Which candidate meets the same correctness bar?
#   - For JSON-strict tasks: which has zero malformed outputs?
#   - For long-context tasks: which handles the full input cleanly?
# Decision matrix:
#   - 90%+ match on both candidates → pick whichever fits the task shape (per Section 2 cascade order)
#   - 90%+ on Gemini only → Gemini first, LM Studio fallback (or skip LM Studio if JSON-strict)
#   - 90%+ on LM Studio only → LM Studio first, Gemini fallback
#   - Both <90% → stay T1, log as "Tier 3 not ready for this task"
```

**Per-task bars:**
- Pure classification (District Classifier): ≥95% accuracy match
- Summarization (trajectory, meeting, daily brief): subjective review of 5-10 samples — does it capture the essentials?
- JSON-schema-strict (memory consolidator, graph extractor): **100% schema validity** (zero malformed) + 90%+ field-level correctness
