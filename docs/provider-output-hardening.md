# Provider Output Hardening — design spec

**Date:** 2026-06-15 (updated 2026-06-16)  **Status:** IN PROGRESS — first feature proven on Gemini.
**Author:** Opus Lead, with Jon (planned together)
**Context:** Came out of Ares R3 (provider rebalancing — see [`ares-plan.md`](ares-plan.md) §10 R3 status).

---

## Progress + key learnings

**Proof #1 — `trajectory_summary` LIVE on Gemini flash-lite (2026-06-16, commit `8e09cda`).**
First internal/low-stakes feature moved off Claude. Live test surfaced learnings that change the
rollout for every remaining feature:

1. **MODEL CHOICE IS THE FIRST FAILURE, NOT JSON SHAPE.** Full `gemini-2.5-flash` "thinks" and
   exhausts a tight output-token budget (512 here) BEFORE emitting → **truncated/empty JSON** →
   validation fails → all-null safe default. **`gemini-2.5-flash-lite` thinks far less and
   completes cleanly.** So: **route short structured-output tasks to flash-lite** (alternatives
   if a task genuinely needs full flash: raise `max_tokens` generously, or disable thinking via
   Gemini `generationConfig.thinkingConfig.thinkingBudget=0`). **The other Gemini-targeted call
   sites (consolidator, graph_extractor, meetings summarizer) STILL hardcode full
   `gemini-2.5-flash` and will hit this same truncation when flipped — fix them the same way.**
2. **Honor the override's `model` field.** The resolver doesn't forward it; call sites hardcoded
   the model. `trajectory_summary` now reads `cascade[0].model` (default flash-lite). Replicate
   this per call site as they're flipped (or fix the resolver to forward `model` centrally).
3. **Failure layers are distinct, confirmed:** (a) *completeness* — model/thinking/budget
   (truncation); (b) *shape* — e.g. scout `reasonCodes` as strings + extra `districtId` (Layer C
   tolerant validators); (c) *format drift on retry* — Gemini's retry returned markdown bullets,
   not JSON. The shared fence-strip + JSON-extract handles fences/prose; (a) is model config; (b)
   is per-schema. Keep validation strict on MEANING.
4. **Scouts remain the hard case** (rich, customer-facing, strict anti-hallucination schema with
   `extra="forbid"` + typed `reason_codes`). Tackle after the internal features, with a *quality*
   check (not just "validated"), and keep on Claude if Gemini quality dips.

**Proof #2 — `memory_graph_extraction` LIVE on Gemini flash-lite (2026-06-16, commit `1f52827`).**
Applied learnings 1+2 up front → went fast. Live-verified additive: a sample extracted valid
entities/relations on Gemini (clean JSON, NO Layer C needed). Lossless preserved (additive upserts;
no supersession). Confirms the pattern generalizes; the per-feature cost is small once the model
trap is known.

**Codex status (2026-06-16, commit `92fa85f`):** tried Codex on `trajectory_summary` as a
provider-agnostic check. The Codex ADAPTER was broken against codex CLI v0.129+ (`--quiet` removed;
`--full-auto` deprecated → `--sandbox workspace-write`) — fixed; codex exec now launches. But the
**ChatGPT/Codex account is at its usage limit** (turn.failed "usage limit"), so Codex output is
UNVERIFIED until the cap resets. Two codex follow-ups: (1) detect the usage-limit/turn.failed event
and surface it as a rate-limit error so routing falls back (parallel to the Gemini-429 net);
(2) reconcile exit-code handling. Re-test Codex output after the cap resets.

**Still on Claude (not yet flipped):** all 6 classification scouts, memory consolidation, meeting
summary. ✅ Done on Gemini: `trajectory_summary`, `memory_graph_extraction`. Next internal target:
memory consolidation (Lead-hands-on — supersession is lossless-sensitive). Scouts last.

---

## Why this exists (the problem)

Our LLM prompts and output schemas were written implicitly tuned to **Claude's**
formatting habits. When we route a feature to a **non-Claude** provider, the model
returns output that is *semantically right but structurally different*, and our strict
validators reject it.

**Concrete failure that surfaced it (2026-06-15):** flipping the classification scouts to
Gemini. After fixing two parsing issues (retired model id `e9011ee`; Gemini wrapping JSON
in ```` ```json ```` fences `206a40b`), the scout STILL rejected every item because Gemini
returned `reasonCodes` as bare strings (`"DISTRICT_STRATEGIC_LITERACY"`) plus an extra
`districtId` field — and the scout's Pydantic validator (`normalize_intake_payload`)
rejects that shape. The flips were reverted; nothing is degraded.

**This generalizes.** It is NOT a Gemini quirk:
- **Codex (GPT/OpenAI) will hit its own version** — different field conventions, occasional
  fences/prose, different date strings. So will any future provider (local Qwen, etc.).
- Solving it per-provider, per-feature = sprawl. Solve it **once, centrally.**

---

## Design principles

1. **Lenient on *shape*, strict on *meaning*.** A normalization layer makes output
   well-*formed*; the domain schema still rejects genuinely-wrong *content* (invalid reason
   codes, missing required fields, out-of-range values). We must NOT silently swallow garbage
   — that just trades a loud failure for a quiet one.
2. **Backend, automatic, at the provider boundary — never a pipeline node.** (Rationale below.)
3. **Central, not per-agent.** Build it once so every feature + every provider + every
   future builder-generated agent inherits it for free.
4. **Prove with a *valid accepted result*, not a 200.** A call that returns is not success;
   success is a result the validator accepts. Verify live (real run + `cost_events` + an
   accepted output), one feature at a time.

---

## Architecture — a provider-agnostic "anti-corruption layer"

Four layers, from input to validated output:

### Layer A — shared output-contract prompt snippet (input side; light touch)
One small, reusable instruction appended to any structured-output prompt: *"Return raw JSON
only — no markdown fences, no prose — matching this exact schema: {…}."* Reduces drift across
all providers. **Deliberately small** — we do NOT fight each model with bespoke mega-prompts;
that's a maintenance swamp and models drift anyway. The real robustness is Layer B/C.

### Layer B — shared normalization core (output side; the workhorse) — write once
Every structured response passes through this before validation:
1. **Strip a wrapping code fence** — DONE: `_strip_wrapping_code_fence` in
   `artemis/providers/gemini/adapter.py`. Generalize/relocate so it covers all providers
   (Codex fences too), e.g. into the shared parse path.
2. **Extract the JSON** — tolerate leading/trailing prose; pull the first balanced `{…}`/`[…]`.
3. **Common coercions** — provider-agnostic, schema-independent: trim, unwrap single-element
   wrappers, normalize obvious date strings, drop empty-string scalars that should be null.

Likely home: the shared LLM call path — alongside `complete_with_fallback`
(`artemis/providers/fallback.py`) and/or a new `artemis/providers/output.py`. It sits UNDER
every caller (scouts, memory, meetings, pipeline node executors), so all inherit it.

### Layer C — per-feature/per-schema adapters (thin)
The ~20% that's feature-specific lives WITH each schema as tolerant Pydantic `before`
validators. Example (the scout case): accept `reasonCodes` as **strings OR objects** and
normalize to the canonical shape; tolerate/parse extra fields like `districtId`/
`campaignFamily` instead of `extra_forbidden`. The seed pattern already exists:
`normalize_intake_payload` (`artemis/marketing/scout_intake.py`) is exactly a per-feature
normalizer — extend it on top of Layer B.

### Layer D — strict schema validation (unchanged)
The domain Pydantic schema still validates **semantics** and rejects wrong content. Layers
B/C only fix structure. This is the guardrail that keeps us honest (principle #1).

---

## Why backend, not a pipeline node

- **You'd forget it.** As a node, every pipeline author must remember to add "normalize"
  after every LLM node; one omission = silent rejection. Correctness plumbing must not be
  opt-in.
- **Most calls aren't in pipelines.** Scouts, memory consolidation, meeting summarizer run on
  schedulers, not the canvas. A node helps only canvas flows; a backend layer helps
  everything — and pipeline node-executors call the same shared LLM path, so they inherit it
  for free.
- **No user decision in it.** "Coerce a string to the right shape" is correctness, not
  workflow. Nodes represent steps you reason about (fetch / classify / gate / send).

Analogy: spell-check at the boundary, not a paragraph you insert into every document.

---

## Builder hook

Because the layer is backend-automatic, the **agent + workflow builder doesn't add a node** —
generated agents inherit provider-tolerant output with zero wiring. The builder's only job:
emit the **Layer A** output-contract snippet by default in any structured-output agent it
creates, and (later) expose provider choice knowing the layer has its back. This is the right
way to "add this knowledge to the builder" — make the runtime provider-agnostic so new work is
correct by construction, rather than teaching the builder to write per-provider prompts.

## Observability (UI — a signal, NOT a node)

Surface in the **Cost → Routing** view (`artemis/routes/costs_routing.py` + `cost_events`) a
"provider output health" signal: when a feature is *failing validation* on a given provider,
show it — so you can glance and see "Board Minutes Scout is struggling on Gemini" instead of
discovering it by hand. Needs a lightweight record of validation-failure-per-(feature,
provider) (a counter / recent-failure flag), not a new node.

---

## Build plan (sequenced; one feature at a time)

1. **Prove the pattern on ONE classification scout** (e.g. Regional News / Board Minutes):
   add the Layer C tolerant validators (`reasonCodes` string|object; tolerate `districtId`/
   `campaignFamily`) on top of the existing fence-strip. Flip just that scout to Gemini, run
   it live, and confirm via `cost_events` it (i) ran on Gemini AND (ii) produced an **accepted
   signal**, not a rejection. This scout becomes the reference implementation.
2. **Lift the reusable parts into the shared Layer B core** (`providers/output.py` or
   alongside `complete_with_fallback`); relocate the fence-strip there so Codex/all providers
   share it.
3. **Roll out feature-by-feature with live valid-result proof:** the other 5 classification
   scouts, then memory consolidation / graph extraction / meeting & trajectory summaries
   (each has the same Claude-tuned-schema risk — validate before flipping).
4. **Add the Layer A contract snippet** as a shared constant and wire the **builder** to emit
   it by default.
5. **Add the Cost→Routing "output health" surfacing.**
6. **Codex:** once the layer exists, routing a feature to Codex is the same drill — flip +
   live-verify an accepted result; no new per-provider code expected.

## Constraints / guardrails

- **GEMINI + CODEX only. No lm-studio / local until Ares is live** (Jon, 2026-06-15 — Studio
  models unloaded). See [`ares-plan.md`](ares-plan.md).
- **Never mask bad output.** Layer B/C fix structure; Layer D stays strict on semantics. If a
  coercion would accept a genuinely-wrong value, don't add it — fix the prompt or keep the
  feature on Claude.
- **Lossless / no silent drops:** a feature that can't be made valid on Gemini stays on Claude
  (its fallback) rather than dropping work.

## Open questions (decide when building)

- Where exactly Layer B lives: extend `complete_with_fallback`, or a separate
  `providers/output.py` the call sites invoke? (Leaning: separate module, called right after
  the completion, so non-fallback callers use it too.)
- Whether the Layer A contract snippet should carry the JSON schema inline (token cost) or
  just the "no fences / raw JSON" rule (cheaper, lets Layer B/C do the shape work). (Leaning:
  the cheap rule + rely on B/C.)
- Validation-failure telemetry: reuse `cost_events` (add an outcome column) vs a small new
  table.
