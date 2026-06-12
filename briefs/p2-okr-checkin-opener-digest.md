# Worker Brief — OKR Check-in Opener: Minimal, Activity-Grounded Digest (kill the data dump)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-okr-opener-digest`. Builds on merged `p2-okr-reconcile` (breadcrumb + reconcile loop).
Test DB at head (0081). Real DB-backed tests.

## The problem Jon hit (live)
The check-in opener currently recites **all 20 KRs with their full target paragraphs** — a wall of text, exactly
the consultant-deck energy we're killing. Jon: "way too much for a check-in, it's pretty intense." He wants the
opener **very minimal AND grounded in what's actually been worked on** this week + prior weeks, so it reads like
a chief of staff who's been paying attention — and **gets smarter over time** as activity history accumulates.

## What to build

### 1. Replace the full-KR recitation with a minimal, activity-grounded digest
NO reciting all 20 KRs. NO target-description prose in the opener. Instead, derive a tight digest (aim ~2-4
KR mentions total, a few short sentences) from REAL signal:
- **"In motion"** — KRs with recent OKR activity (query `okr.repository.list_activity` over a recent window,
  e.g. last ~21 days; also any KRs touched by prior check-in reconciliations). Surface as "you've been pushing
  on X and Y lately."
- **"Slipping / needs your eyes"** — 1-2 KRs that are **deadline-pressed and lagging**: target date near or
  PAST relative to today and progress low, *especially* with no recent activity (stalled). (Note: today is past
  most Q1 target dates, so several are genuinely overdue + low — e.g. Champion Engagement 0%, Founding Members
  10%, Self-Service 30%. The digest should honestly reflect that, briefly.)
- Then the ask: "What did you move this week? I'll map it. Nothing updates until you say go."
- If there's NO activity history yet (early weeks), fall back to the deadline-pressure heuristic alone (near/
  past-deadline + low %). The point of "gets smarter over time": as `list_activity` + reconciliations
  accumulate, the "in motion" half gets richer automatically — no model training, just a widening history query.

Keep it grounded: every KR named in the digest is real, with its real current %; never invent momentum. Better
to surface 2 honest items than pad it.

### 2. Date-aware header
"Friday check-in, Thursday, June 11" is wrong. Make the header date-aware: on a Friday it can say "Friday
check-in"; otherwise just "OKR check-in" (or "Weekly check-in") + the actual date. No hardcoded "Friday".

### 3. Voice (carry the Jarvis tone)
Render the digest through the existing Artemis-voice pass — dry, warm, economical, a few sentences. It should
sound like she's caught up on the week, not generating a report. Lint kept (no em-dash/emoji/tables). Plain-text
fallback on render failure (don't regress that).

## What does NOT change
- The reconcile loop, breadcrumb persistence/TTL, conversation-driven clear, and the propose→"go"→apply path are
  all correct and merged — DON'T touch them. Full KR detail still surfaces during RECONCILE (when she maps the
  word-dump to specific KRs) — that's the right place for specifics. This brief only slims the **opener**.
- Approval-first: writes still gated; nothing fabricated.

## Constraints
- No new deps; ruff + mypy strict on touched files; DB-backed tests. Don't regress P2a brief, idempotency,
  gather_sources, confirm, or reconcile work. Likely files: `proactivity/okr_checkin.py` (opener/digest build),
  `proactivity/voice_render.py` (render), maybe `gather_checkin_sources` to pull the recent-activity window.

## Tests
- Opener does NOT contain all 20 KRs and does NOT contain target-description prose (assert absence of the long
  target strings / a cap on KR mentions, e.g. <= ~5).
- Given seeded recent OKR activity on KR A + B, the digest's "in motion" mentions A/B (grounded in activity).
- Given a KR past its target date with low %, it surfaces in "slipping"; a KR with no grounding gets no claim.
- With NO activity history, opener still produces a minimal digest via the deadline heuristic (no crash, no dump).
- Header is date-aware: not "Friday" on a non-Friday.
- Reconcile path unchanged: a word-dump after the opener still proposes `update_okr_kr` (layer-3) — opener change
  didn't break reconcile.

## Acceptance
The check-in opens with a few grounded sentences — what's been moving, what's slipping — then asks what Jon
moved. No 20-KR recitation, no target paragraphs, date-aware header, Jarvis voice. Lead verifies live: re-fire
the check-in and confirm the opener is tight + grounded, then run the word-dump → propose → "go" round-trip.
