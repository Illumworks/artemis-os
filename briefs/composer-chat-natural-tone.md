# Build brief — Composer chat: natural collaborator tone (B1)

**Agent:** Codex (backend-only, Python). **Branch:** `worker/composer-chat-natural-tone` off `main`. **Own git
worktree, cwd inside. Own test DB** (`artemis_test_chattone`). **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md`.

## Why
On a live draft, the composer chat reads like a compliance engine, not a writing partner. Jon (Creative
Director): make it converse naturally; keep the compliance INTELLIGENCE but stop dumping it into every reply.
This is independent of the apply-to-document work (separate brief) — ship it on its own.

## Two confirmed sources
1. **Output-formatting scaffold** — the compose system prompt seeds from `profile.system_prompt` (origin:
   `artemis/writing_rules/seed_corpus.py:113-119`, the "Common Operating Rules" → `## Output formatting`):
   *"1. Start with a short 'Recommended framing' … 3. End with 'Compliance check' listing any Tier 4 claims
   used and what proof packaging is required."* That mandated "Recommended framing / Compliance check" wall is
   the unnatural part.
2. **`Proposed learning:` line** — `compose_engine.py:435-443` instructs the model to append a labeled
   `Proposed learning: <rule>` line. It IS extracted into training candidates
   (`extract_proposed_learnings`, `routes/writing_studio.py:810`) — but it is **NOT stripped** from the
   returned/persisted `responseText`, so the raw line shows in chat.

## Fix 1 — conversational presentation (do NOT edit the authored brand modules)
**Do not delete or rewrite the seed-corpus brand modules** (01_MESSAGE_COMPASS etc. — Angela/Julie's content,
Jon's territory). The compliance/anti-fabrication rules still GOVERN the writing. Only change how the
interactive **chat** presents.

Lever: in `compose_engine.py` the system prompt is assembled in `system_parts` (~line 396) as
`profile.system_prompt → runtime_context → grounding_block`. Append a **final compose-chat presentation
directive** to `system_parts` (last = strongest) that overrides the output-formatting scaffold for the
interactive composer, e.g.:

> "You are replying inside a live document editor, conversationally, as a writing collaborator — not a report
> generator. Do NOT emit 'Recommended framing' or 'Compliance check' section headers, and do NOT enumerate
> Tier ratings, proof-pack IDs (E001…), or claim-evidence tables in your reply. Compliance is handled by the
> document's inline claim flags. If a sentence you write uses an unapproved or Tier-4 strong claim, add at
> most ONE short plain-English heads-up line at the end (e.g. 'Heads-up: the "9 weeks of growth" stat needs
> its proof pack before this ships.') — not a section, just a sentence. Keep replies tight and human."

Verify the EFFECT (a later explicit instruction must actually win over the scaffold) — see acceptance. If a
late directive does NOT reliably override, the fallback is to make the `## Output formatting` block
**compose-context-aware** (omit the Recommended-framing/Compliance-check items when assembling the
*interactive composer* system prompt) WITHOUT mutating the stored brand-module text — your call, but prefer
the additive directive first and only escalate if it doesn't hold.

## Fix 2 — strip the `Proposed learning:` line from the visible reply
Keep extracting it into training candidates (that behavior stays), but the user must not see the raw line.
In `routes/writing_studio.py` (~line 800-810): extract proposed learnings from the RAW response, then
**remove the matched `Proposed learning:` line(s) from `response_text` before it is persisted as the
assistant message AND before it is returned as `responseText`.** Reuse the same regex shape as
`extract_proposed_learnings` (case-insensitive, optional "reusable", leading `**`) so the strip matches the
extract exactly. Order matters: persist/return the CLEANED text; the candidate row still gets the rule.
(Optionally also soften the prompt instruction at `compose_engine.py:438-442` so the model emits the line
less ceremoniously — but the strip is the guarantee.)

## Acceptance (verify the EFFECT — paste real output)
- Run a real compose turn (live LLM, not a mock) on a draft: ask it to "tighten the opening." Paste the
  reply. It must read like a colleague — NO "Recommended framing" header, NO "Compliance check:" section, NO
  Tier/E00x dumps, NO visible "Proposed learning:" line.
- Confirm a training candidate WAS still created from that turn (query `training_candidates` /
  `proposedCandidates` in the response is non-empty when the model proposed a rule) — i.e. the learning
  pipeline is intact; only the chat text changed.
- Confirm an unapproved strong claim still gets at most a one-line heads-up (not a wall).
- `./scripts/check.sh` for touched Python (note PRE-EXISTING failures separately). Add/adjust a unit test:
  given a response containing a `Proposed learning:` line, `responseText` no longer contains it but a
  candidate is extracted.

## Constraints
Do NOT edit authored brand-module text (lossless to the rules corpus). Compliance/anti-fabrication still
governs the writing — only chat presentation + the proposed-learning line change. Don't fork the compose
engine. No migration expected. Isolated worktree + own test DB. **Do NOT merge** — report branch + SHA +
worktree + the pasted before/after compose output + the candidate-still-created proof. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
