# Worker Brief — Morning Brief Cleanup (formatting + Jira names)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges. **Status:** READY.
**Branch:** `worker/p2a-brief-cleanup`. Builds on P2a (merged). Test DB at head — real tests.
Jon's feedback after seeing the first live brief: it's close, three cleanups.

## Scope
1. **Drop the "Confidence" line** from the Slack-rendered brief. It's the model's self-assessment — noise to
   Jon. Remove it from the Slack format (`artemis/proactivity/scheduler.py:_format_brief_for_slack`). (Keep
   the field in the stored snapshot if you like; just don't render it in the DM.)
2. **Strip the trailing metadata suffixes** in highlights + priorities. Currently each line renders as
   `- <text>: <detail>; <source>` (highlights) and `- <text>: <detail>; <level>` (priorities) — the dangling
   `; calendar` / `; jira` / `; okr` and `; high` / `; medium` read as junk. Drop the `source` and `level`
   from the rendered `extras` (scheduler.py ~lines 196-215). Keep the bullet + detail; lose the trailing tag.
3. **Use Jira ticket NAMES, not keys.** The brief references tickets as `MT-456`, `MT-551`, etc. — Jon doesn't
   know what those are. Show the ticket **title/summary** (e.g. `"Fix login redirect"` or `MT-456 — Fix login
   redirect`). Two parts:
   - Ensure the brief's Jira source (`artemis/brief/sources.py:_safe_jira`) includes each ticket's
     **summary/title** in what it returns (the Jira overview/board data has `items[].summary` — carry it).
   - Update the generator context/prompt (`artemis/brief/generator.py` `_build_context_string` + the prompt)
     so the LLM refers to tickets by **title** (or `KEY — Title`), not the bare key. Make the instruction
     explicit so it doesn't regress to keys.

## Constraints
- Don't regress the gather_sources per-session fix just landed. Lossless; no new deps; ruff + mypy strict.
- Re-run the existing P2a tests (`artemis/proactivity/tests/`) green; add/adjust a formatter test asserting no
  `; source`/`; level` suffix and no Confidence line; add a generator/source test that a ticket title flows
  through (mock Jira data with a summary → brief text contains the title, not just the key).

## Acceptance
The morning brief in Jon's DM: no "Confidence" line, no dangling `; calendar`/`; high` suffixes, and Jira
items show their titles (not bare keys). Lead verifies by re-firing a brief to Jon's DM and eyeballing it.
