# Worker Brief — Artemis natural-conversation layer (intent understanding, not command-matching)

**Owner:** Codex (backend, Artemis's brain) — terminal if Codex is limited. **Lead:** Artemis (Opus) verifies
live + **merges (safety-adjacent — non-negotiable Lead-merge).** **Isolation:** own worktree + own test DB
(name contains `artemis_test`); commit before reporting; do-NOT-merge.
**Status:** READY. Highest-leverage item after P3.

## Why (Jon's words: "she's way too brittle vs you… I want natural conversations, not robotic")
Today exposed the root cause — Artemis's front door is **regex command-matching + templated replies**, where a
real assistant **understands intent over context**:
- "Yes a2 and yes a3" bounced off `^yes A\d+$` and leaked into the conversational layer.
- That layer had no idea the **agency proposals** were pending, so it mis-mapped "a2/a3" onto staged **OKR**
  updates. Her flows don't share one view of "what's on the table."
- Replies feel templated/stiff.

## The goal
Artemis understands natural language, is aware of everything currently pending with Jon, and replies like a
person — **without loosening any safety guarantee.** "yes do both", "go ahead with the Slack one", "approve
them", "skip the email" all just work.

## Design — three pieces, built on her EXISTING brain (don't replace it)
1. **Unified pending-context assembler.** One function that gathers everything currently awaiting Jon into a
   single structured view: pending `proposed_actions` (id + type + preview), staged OKR check-in updates, open
   commitments, recent radar items. This view is injected into Artemis's reply handling so *every* inbound
   message is interpreted against the whole picture — not one flow guessing.
2. **LLM intent-router replaces the regex cascade.** On an inbound DM, instead of a chain of regexes
   (yes/no, `dismiss radar`, `reply radar`, OKR "go", …), one LLM step interprets the message against the
   unified context + recent conversation and returns a **structured decision**, e.g.
   `{intent: "approve_proposals", ids:[2,3]}`, `{intent:"reject_proposals", ids:[2]}`,
   `{intent:"apply_okr_updates"}`, `{intent:"dismiss_radar", id:5}`, `{intent:"reply_to_mention",…}`,
   `{intent:"converse"}`, `{intent:"clarify", question:"…"}`. The router then calls the **existing, unchanged
   backend handlers** (`try_apply_proposed_action_reply`'s approve path, the OKR apply, radar dismiss, etc.).
   It's a smarter front door over the same safe backends.
3. **Natural response generation.** Replies generated in Artemis's voice (warm, brief, human; within her
   persona/brand-voice + existing lint), not fixed template strings. Clarifications reference the *actual*
   pending items ("Did you mean approve both the Slack note and the email, or just one?").

## SAFETY — the gate stays exactly as hard (this is the whole point)
- **The LLM never executes and never bypasses the gate.** Approval intent → it calls the existing
  `approve_proposed_action` (conditional UPDATE, one-shot) → `execute_proposed_action` (requires
  `status='approved'`). No new execution path. Re-assert in tests: **no action runs without an explicit
  approved transition.**
- **Conservative by construction.** Approve/send/destructive intents require **high-confidence, explicit
  reference to a specific pending item.** On ANY ambiguity → `intent:"clarify"` (ask naturally) — **never
  assume-yes.** Default bias is converse/clarify, not act.
- Preserve the existing "bare yes with 0 or >1 pending → don't act, ask" guarantee (now phrased naturally).
- Audit: log the router's interpretation + confidence + the handler invoked, for every actioning decision.

## Phasing
- **(a)** unified pending-context assembler. **(b)** intent-router for the agency gate first (approve / reject /
  clarify / converse — today's pain), mapping to the existing gate. **(c)** natural tone generation. **(d)**
  extend the router to OKR-apply / radar-dismiss / commitments so all of Artemis's flows share the one front door.

## Ship gate (Lead verifies LIVE)
- Natural approvals route correctly: "yes a2 and a3" → both approved+executed; "go ahead with the Slack one" →
  only the slack proposal; "approve both" → both; "skip the email" → that one rejected.
- **Ambiguous/partial replies → a natural clarifying question, NO action taken.** No cross-flow mis-mapping
  (proposal reply never lands on OKR context).
- Safety re-proven: no execution without an approved transition; one-shot holds; nothing sends on ambiguity.
- Tone reads human, not templated — spot-check a few replies with Jon.
