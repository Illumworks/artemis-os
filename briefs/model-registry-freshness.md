# Roadmap plan — Model registry freshness (detect drift, human-confirm pricing)

**Status:** ROADMAP — not yet scheduled. Sequence: AFTER the campaign-cost tab ships. Captured 2026-06-06
per Jon ("we should have a system that naturally updates the available models since providers regularly
update"). This is a design plan, not a ready-to-fire worker brief — refine into worker briefs when scheduled.

## Problem
Providers (Anthropic, OpenAI, Google, OpenRouter, plus CLI tools) add models, deprecate others, and change
prices regularly. Our pricing/model registry (`artemis/costs/pricing.py` — hardcoded rate table +
`MODEL_ALIASES` + `canonicalize_model`) is hand-maintained, so it silently goes stale. Stale data feeds the
Cost page + routing recommendations — the exact surfaces whose whole value is being *trustworthy*.

## Core principle (non-negotiable)
**Auto-DETECT model/price drift; HUMAN-CONFIRM anything that affects cost math.** Never auto-apply scraped or
third-party prices to a financial number — a wrong price drives wrong routing decisions and destroys trust.
External pricing is a *suggestion to confirm*, never ground truth. (Same ethos as the rest of the system:
assert/verify, don't blind-trust external data.)

## What's automatable vs not (per provider)
| Provider      | Model list (availability)        | Pricing                                   |
|---------------|----------------------------------|-------------------------------------------|
| lm-studio     | ✅ already live (loaded models)  | local = effectively $0                    |
| OpenRouter    | ✅ API lists models…             | ✅ …WITH pricing (most machine-readable)  |
| OpenAI        | ✅ /v1/models (names)            | ❌ no official price API → human-confirm  |
| Anthropic     | ⚠️ limited list; mostly static   | ❌ no official price API → human-confirm  |
| Google/Gemini | ⚠️ list endpoint                 | ❌ → human-confirm                         |
| claude-code / codex CLI | ✅ via CLI version/probe| n/a (subscription/flat)                   |

## The build (4 pieces, when scheduled)
1. **Data-driven registry.** Move the hardcoded rate table to data (seed/config rows) with per-entry
   `last_reviewed_at` + `source` (e.g. "manual", "openrouter-api"). `get_rates`/`canonicalize_model` read
   from it. Keeps the frozen-rate-snapshot invariant (rows already snapshot rates at write time).
2. **Provider catalog probes.** A read-only adapter per provider that answers "what models do you offer
   now?" (reuse the routing health module pattern — 2s timeout, never-raise, cached). OpenRouter probe also
   returns suggested pricing.
3. **Drift detector (scheduled — cron/scheduled-task).** Periodically diff registry vs probes → produce a
   **review queue**: "N new models from <provider> (unpriced)", "model X looks deprecated", "OpenRouter
   price for Y changed $A→$B". Surfaces in a small UI (Cost/Routing page section or a settings panel).
   Nothing auto-applied. Where pricing is machine-readable (OpenRouter), pre-fill the suggested rate for a
   one-click human confirm.
4. **Staleness signal.** Show "rates last reviewed: <date>" on the Cost page; visibly flag entries older
   than a threshold (e.g. 90d) so an unverified price can't masquerade as current.

## Why this shape
- Kills the manual-discovery toil (you find out about new models automatically) WITHOUT letting unverified
  prices silently drive cost decisions.
- Degrades gracefully: a probe that fails just leaves the registry as-is (never-raise), so it can't break
  the Cost page.
- Honest by construction: every price that affects math was human-confirmed; staleness is visible.

## Constraints (carry into the eventual worker briefs)
- Lossless: registry edits versioned/append-style with review history; never destructive; never overwrite a
  confirmed rate without recording the prior one. Frozen-rate snapshots on existing cost_events untouched.
- External calls: read-only, timeout-bounded, cached, never-raise. Org dep rule (no dep <7 days old). No
  auto-applied external pricing — human confirm gates anything that changes cost math.
- Coordinate with the cost track (terminal's domain — `pricing.py` / cost-infra).

## Open questions (decide when scheduled)
- Where does the review queue live — a Cost-page "Models" tab, or a settings/admin panel?
- Cadence of the drift check (daily? weekly?).
- Do we adopt OpenRouter as the canonical pricing *source* (with confirm), or keep manual + OpenRouter-as-hint?
