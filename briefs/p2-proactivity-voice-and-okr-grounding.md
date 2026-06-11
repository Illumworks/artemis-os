# Worker Brief — Proactivity in Artemis's Voice + OKR Grounding Correctness

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2-proactivity-voice`. Test DB at head — real tests.
Jon's feedback: the morning brief + OKR check-in are **too robotic**, and the OKR check-in **mis-attributed
other people's closed Jira tickets as his work**.

## Part A — Sound human (both the morning brief + the OKR check-in)
Today the morning brief renders labeled sections and the OKR check-in is a deterministic template — both read
robotic. Make them sound like **Artemis** (chief-of-staff: confident, direct, plain-English, dry wit, concise).
- Add an **Artemis-voice rendering pass**: take the GROUNDED data (the brief's structured fields / the OKR
  state + cited evidence) and have an LLM phrase the Slack message in her voice, using
  `load_agent_profile("artemis").persona_core` + her voice corpus (`select_voice_samples`). Keep it short and
  scannable, but conversational — like a sharp chief of staff briefing you, NOT a form with `Summary:` /
  `Highlights:` headers. Run the output through `lint_agent_text` (no tables/em-dash/emoji).
- **Grounding stays underneath** — the voice pass phrases ONLY the grounded facts; it must not invent. (The
  morning brief generator already produces grounded fields; the OKR generator produces cited bases — the
  voice pass narrates those, nothing more.)

## Part B — OKR check-in grounding correctness (the mis-attribution bug)
The check-in claimed Jon completed things he didn't — it pulled **all** closed Jira this week (other people's
tickets) and even mapped one ticket to multiple KRs. Fix `artemis/proactivity/okr_checkin.py`:
1. **Never assert "you did X" from Jira alone.** Either scope Jira to JON's own tickets (assignee = Jon /
   his Slack/identity), or present noticed activity as **clearly-labeled team context** ("I saw the team
   close X — does that move KR Y?"), never as his accomplishment.
2. **One ticket must not map to multiple KRs** — drop the spurious fan-out (the same ticket appeared under 3
   KRs). If a ticket→KR link isn't real, don't make it.
3. **Reframe the check-in:** LEAD with where his KRs stand + **ask what HE actually moved this week** — his
   word-dump is the source of truth for his accomplishments. Auto-noticed activity is optional *context*,
   honestly attributed, not asserted. Better honest+sparse than confidently wrong.
4. Keep the hard rule: **nothing fabricated**; every stated fact cites a real basis or is framed as a question.

## Constraints
- Lossless; no new deps; ruff + mypy strict; DB-backed tests.
- The voice pass adds an LLM call to the scheduled jobs — that's fine (once/day, once/Friday); keep it a
  single cheap-ish call, not an agent loop. Failure-isolated: if the voice pass errors, fall back to a clean
  plain rendering rather than failing delivery.
- Don't regress P2a delivery/idempotency or the gather_sources fix.

## Tests
- Voice pass: given grounded data, output is non-empty, lint-clean, contains the grounded facts, and is NOT
  the labeled-section template (assert no `Summary:`/`Highlights:` header scaffolding).
- OKR grounding: a closed ticket NOT assigned to Jon is NOT asserted as his accomplishment; one ticket does
  not appear under multiple KRs; a KR with no real basis gets no claimed change.

## Acceptance
The morning brief + OKR check-in read like Artemis talking to Jon (human, concise), and the OKR check-in
only attributes Jon's real work (or honestly-labeled context), nothing fabricated. Lead verifies by re-firing
both to Jon's DM and eyeballing voice + attribution.
