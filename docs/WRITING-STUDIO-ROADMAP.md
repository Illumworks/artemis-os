# Writing Studio — Roadmap Index (cross-session pickup)

**Read this first for anything Writing Studio.** Single entry point to the full plan: what's built, what's
active, what's designed-but-not-built, and the build order. Maintained by Opus Lead.

**Why it matters (Jon, 2026-06-06):** **Writing Studio + Campaigns/Signals are the most important parts of
the app right now — they must be solid before the v1 MVP.** This index covers Writing Studio; the shared
content-engine gate (below) and the Signals/Approvals unification are the bridge to the Campaigns side.

**Status legend:** ✅ done+verified · 🔴 active/blocking · 📐 designed, not built · 🗂 roadmap (later)

---

## 🔴 THE GATE — everything waits on this
**Content-node P0** — `briefs/content-draft-node-hang.md`. The auto-draft pipeline hangs/produces empty
shells, so campaigns don't yet write real drafts. `content_asset_selector` fixed (13s); `content_writing_
studio_adapter` still times out at 120s → empty draft. **With terminal** (sent back after Lead live-verify).
Until this lands, the studio has nothing real to compose/edit. **Nothing else builds before this.**

## ✅ DONE + verified (merged to main)
| Area | Brief | Note |
|---|---|---|
| Campaign → WS handoff ("Create draft") | `writing-studio-campaign-handoff.md` | route added; live-verified |
| Folder CRUD (create/rename/delete) | `writing-studio-folder-crud.md` | lossless delete; migration 0069 |
| Folder respawn fix | `ws-folder-respawn-fix.md` | tombstone-aware; live no-respawn verified |

## 🐛 Briefed bug (quick, anytime)
- `campaign-assets-tab-inert-rows.md` — campaign Assets tab shows inert "Draft" rows; make them
  labeled/status-bearing/clickable.

## 📐 DESIGNED — not built (the real build queue, after the gate)
| # | Piece | Brief / artifact |
|---|---|---|
| 1 | **Composer rebuild (v5 spec)** — chat LEFT · document CENTER (inline editor, format-aware pagination) · comments FLOAT (Google-Docs, @mention+ping) · drafts picker pops from header w/ single "+" · orange double-underline claim flag · Google Doc in header · export NOT here | `writing-studio-composer-ux.md` + **`docs/mockups/composer-design-pass.html`** (the visual spec) |
| 2 | **Identity — Google SSO via Cloudflare Access** (unlocks comments/@mention/attribution); **Google Doc import/export**; **multi-user presence + soft-lock + version-guard** | `writing-studio-identity-and-gdoc.md` (Jon owns the Cloudflare policy change; Lead builds JWT-verify + user directory + Docs API + presence) |
| 3 | **Tagging taxonomy + tag-scoped rules engine** — registry (audience/type/platform/format + subtypes, extensible) feeds the agent; rules keyed to tags; captured manually + from conversation | `writing-studio-tagging-and-rules-engine.md` |
| 4 | **Feature set + memory audit** — ★learn-from-edits · ★claims guardrail (LIVING Claims Register, 1-click approve) · ★custom-GPT export (on the **Memory page**) · templates (create+apply) · repurposing · personalization/merge-fields · versions/variants/linter · comments · performance loop | `writing-studio-feature-roadmap.md` |

(Memory audit finding: the profile already has 9 sources — incl. **Claims Register, Proof Pack, Templates,
Audience Router** — + 3 rules; several "features" are wire-ups, not from-scratch.)

## 🗂 Roadmap (later / documented so not lost)
- **Full real-time co-editing** (CRDT/multi-cursor, true Google-Docs) — deferred; presence+soft-lock+
  version-guard meets the near-term need. (`writing-studio-feature-roadmap.md` → Collaboration; `identity-
  and-gdoc.md` Q3.)
- **Model-registry freshness** — `model-registry-freshness.md` (detect model/price drift, human-confirm).
- **Unify Signals Inbox + Approval Queue → one "Review" page** — `unify-signals-inbox-approval-queue.md`
  (Campaigns/Signals side of the MVP core).

## Build order (once the gate clears)
1. 🔴 **Content-node P0** → campaigns write real drafts (terminal).
2. **Composer rebuild** to the v5 spec — the canvas everything else attaches to. Do the **composer design
   pass IA together (Jon+Lead)** → it's done (v5 locked); build follows the mockup.
3. **Identity (Google SSO)** — Jon flips Cloudflare to Google + adds team; Lead builds JWT-verify + user
   directory. (Comments/@mention/presence all depend on this.)
4. **Tagging + rules engine** (structures the prose Audience Router + the 3 rules).
5. **Feature set** in ★ order: learn-from-edits → claims guardrail → custom-GPT export → templates →
   repurposing → personalization → editor-quality/comments/presence → performance loop.
6. **Google Doc import/export** (rides the same Google OAuth as SSO).
7. 🗂 Full real-time co-editing — only if genuinely needed.
- Anytime: the Assets-tab bug.

## Key decisions locked (so they're not relitigated)
- Composer = 3 modes (manual edit · AI chat · highlight→AI edit); clean canvas, **contextual surfaces**
  (no persistent chrome). v5 mockup is the spec.
- Claims Register is a **living bible** curated in-flow (1-click approve); WS users are the approvers, the
  exported company GPT consumes it **read-only**.
- Tags are a **controlled, extensible vocabulary**; AI proposes values, human confirms; **format/length is a
  separate dimension** from purpose.
- Custom-GPT export lives on the **Memory page** (acts on the profile), not the composer.
- Identity = **Cloudflare Access + Google SSO** (verify the JWT); no separate account system.
- "AI proposes, human confirms" everywhere that affects how we write or what's approved.
