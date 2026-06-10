# Worker Brief — Named-Agent Output Lint (no em dashes, no emojis)

**Owner:** Codex (backend slice). **Lead:** Artemis (Opus) verifies + merges.
**Status:** READY for pickup. **Branch:** `worker/lint-named-agent-output`.
**Depends on:** nothing (the persona docs are already committed: `artemis-personality-profile.md`
v1.2.2, `callie-personality-profile.md` v1.1.3).

## Why
Both Named-agent personas declare **hard writing lints**: *never use em dashes (or en dashes)* and
*no emojis*. Prompt-level instruction alone does NOT hold — the live Slack test on 2026-06-10 showed
Artemis's own replies full of `—` and ✅/❌/⏸️. We need a **deterministic post-generation lint** as the
guarantee, plus a small prompt change so the model mostly self-complies and the lint rarely has to fire.

This is a backstop, not the primary mechanism: the prompt does the phrasing nuance (comma vs. parentheses
vs. new sentence); the lint guarantees the result.

## Scope

### 1. Pure lint function
Add `artemis/writing_rules/agent_lint.py` (or the nearest existing writing-rules module) with:

```python
def lint_agent_text(text: str) -> str: ...
```

Behavior, deterministic, no LLM:
- **Em dash (U+2014) and en dash (U+2013):** replace with sensible ASCII. Heuristic:
  - ` — ` / ` – ` (spaced) -> `, ` (comma + space)
  - `—`/`–` with no surrounding spaces -> `, ` as well, then collapse any `, ,` / `,,` / doubled spaces
  - a dash immediately before end-of-string or newline -> drop it / trailing comma cleanup
  Keep it simple and readable; perfection isn't required, "no dash characters remain" is.
- **Emoji:** strip emoji codepoints (the standard Unicode emoji ranges, incl. variation selectors and
  ZWJ sequences) and tidy the resulting whitespace.
- **Preserve** content inside fenced code blocks (```...```), inline code (`` `...` ``), and URLs —
  do NOT rewrite dashes/emoji there.
- **Idempotent:** `lint_agent_text(lint_agent_text(x)) == lint_agent_text(x)`.

### 2. Apply at the outbound boundary
Apply `lint_agent_text` to the text that gets **posted to Slack** in
`artemis/routes/integrations_slack_events.py::route_inbound` — lint `response_text` right before
`client.post_message(...)`. Lint only the **outbound rendering**; do NOT mutate the stored
`floating_artemis_messages` content (keep the raw turn for provenance).
(Leave other surfaces, e.g. composer, for a follow-up; this slice targets the live Slack surface.)

### 3. Prompt nudge (so the lint rarely fires)
In `artemis/floating_artemis/chat.py::_build_system_prompt`:
- Strengthen the voice-samples framing to **calibration-only**: "These calibrate your register and rhythm.
  Never quote them verbatim or near-verbatim. Generate fresh lines in this spirit." (Today it says "use
  sparingly … never force them" — make the no-verbatim rule explicit; it's the main fix for the
  "canned/repetitive" feel.)
- Add the writing lint to the prompt explicitly: no em/en dashes, no emojis (commas/parentheses/new
  sentence instead).

### 4. (Optional, secondary) Tier the spiky voice lines
Let the 3 sharpest Artemis lines ("order of your fault", "Try to stay calm", "You asked. I answered.")
be **occasional** rather than in the default `select_voice_samples` rotation (low weight or a separate
"rare" pool). Keep it small; skip if it balloons scope.

## Constraints
- No new dependencies (org policy: nothing <7 days old; prefer stdlib `re`/`unicodedata`).
- Lossless: do not alter stored message rows; lint the outbound copy only.
- ruff + mypy strict clean; run `./scripts/check.sh`.

## Tests
`tests/test_agent_lint.py`:
- em dash spaced/unspaced, en dash, multiple per line -> none remain, readable result
- emoji (single, ZWJ sequence, with variation selector) -> stripped, whitespace tidy
- code block / inline code / URL with a dash -> preserved untouched
- idempotency
- a route-level test: a Slack reply containing `—` and an emoji is linted before `post_message`
  (assert the posted text has neither).

## Acceptance
Linted Slack replies contain zero em/en dashes and zero emojis; stored turn rows unchanged; prompt
reframed to calibration-only; checks green.
