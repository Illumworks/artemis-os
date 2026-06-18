# Writing Studio × Codex Pilot — Close-out Report (Lead → Lead)

**Date:** 2026-06-18 · **From:** Opus Lead (ran the pilot) · **To:** Opus Lead (current marketing/Writing-Studio lane owner)
**Re:** the Codex-on-Writing-Studio compose pilot Jon requested. Pilot is **CLOSED**.

## TL;DR — Jon's decisions (2026-06-18)
1. **Codex is PARKED for Writing Studio compose.** Do NOT offer it — not even as a human-gated "bootstrap" tier. The pilot showed it's off-voice AND (the bigger issue) it doesn't reason about brief quality.
2. **Compose HARD-FAILS on Claude.** Customer-facing compose must NOT silently fall back to Codex/lm-studio/anthropic. If `claude-code` is unavailable, **fail loud**. ← **ACTION for you** (you own the lane + are already in these files). Details below.

## What the pilot found (evidence)
Method: 4 real briefs (TX HB27 campaign, drafts 42–45), composed on Claude (default) and Codex via an opt-in flag; both run through `lint_agent_text`. Branch-only, default never changed. Samples committed (see Artifacts).

- **Call path:** compose is tool-less `run_turn`, single-shot; provider via `resolve_adapter(profile.default_model_provider or None)` → since the active profile's `default_model_provider` is NULL, it uses the shared `DEFAULT_CASCADE`.
- **Lint:** both providers clean (0 em-dash / 0 emoji even *before* the post-gen strip). The writing rules hold on Codex.
- **Voice:** Codex PARTIALLY holds — vocabulary right (Coherence Map, Assess/Instruct/Tutor), structure sound, no fabrication *with context loaded* — but drifts **formal** ("Dear Texas Education Leader," "I would be glad to share"), **generic openers** ("As Texas districts plan for HB27…" twice), weaker CTAs, and identical boilerplate chat-wrap every turn. Off-Callie to a superintendent.
- **Bigger finding — judgment/fabrication, not just voice:** on a *mismatched* brief (HB27 is a **financial-literacy** mandate, not a reading bill), **Claude refused to fabricate**, flagged the mismatch, and asked for direction before writing. **Codex composed immediately** with no brief-quality reasoning — it only avoided fabricating because the Amira context happened to be in the prompt; it did not independently catch the bad brief. For autonomous / light-review use that's a fabrication risk. **This is why Codex is parked, not merely gated.**
- You're already aligned: `794facc` keeps Callie's Argus summaries on claude-code (her voice), not codex.

## ACTION — make compose hard-fail on Claude
- **Where:** `artemis/marketing/routes/writing_studio.py:406-407` (and `compose_draft`, ~665/698) call `resolve_adapter(profile.default_model_provider or None, ...)`. With `writing_profiles.default_model_provider = NULL`, that resolves via the shared `DEFAULT_CASCADE` = `("claude-code","codex","lm-studio","anthropic")` (`artemis/providers/resolver.py:42`). So a `claude-code` outage silently composes customer-facing drafts on **Codex** (off-voice + non-reasoning).
- **Change:** pin customer-facing compose to **claude-code only** and **fail loud** if it's unavailable (raise/return — `NoProviderAvailableError` is already imported in that route) instead of cascading to codex/lm-studio/anthropic. (Either pass an explicit claude-only provider with no fallback, or a compose-specific resolution that bypasses the generic cascade.) This is a Writing Studio rules change — **Jon-approved**.
- **Consider applying the pattern** to other customer-facing *voice* surfaces (floating Callie chat; the Argus summary you just routed) — claude-only / fail-loud for voice-bearing output, rather than a silent cross-provider cascade.

## Pilot artifacts (NOT merged — reference only)
- Branch `worktree-agent-a7c0081a3f09e5821`, commit `b6ba874`: `artemis/marketing/writing_studio/compose_pilot.py` (the opt-in `provider_override` flag — now unused since Codex is parked) + `docs/pilot-writing-studio-codex-samples.md` (the 4-brief side-by-side).
- Read the samples without checking out the branch: `git show b6ba874:docs/pilot-writing-studio-codex-samples.md`.
- Default routing was never changed; no production code was merged. Prune the worktree/branch once you've reviewed — or keep the branch as the comparison record.

## Future note (Jon's hypothesis — not now)
The **Codex CLI ≠ a clean OpenAI GPT API call.** The CLI's coding-agent wrapper most likely caused the boilerplate chat-wrap + formal drift, not GPT itself. If voice-quality cost-tiering is ever revisited, **pilot the OpenAI API (gpt-4o/gpt-5) directly**, not the Codex CLI. (`OPENAI_API_KEY` is empty today; not pursued now.)
