# Brief — Screen-Time Watch #2: Callie reports to #policy-watch

**Owner:** app-seat Lead (me) → Sonnet worker. **Read first:**
`docs/screentime-watch-plan.md` + Brief 1. **Depends on:** Brief 1 (signals exist).
**Coordination:** marketing/Callie files are terminal's lane — **call Callie's
posting path, do NOT modify `callie_push.py` or the campaign push logic.**

**Goal:** Callie surfaces the "real moves" to a dedicated **#policy-watch** channel —
a short, voiced digest for Angela's team. Reuse Callie's existing Slack posting +
voice; this is a new *consumer* of the screentime signals, not a change to her
campaign behavior.

## Scope
1. **Channel config:** add `screentime_report_channel` (config; empty = feature off,
   same dormant-until-set pattern as `callie_proactive_channel`). Dedicated
   #policy-watch, separate from the campaign signals channel.
2. **Two report modes** (off the screentime_signals table):
   - **Weekly digest** (cron): the week's real moves, grouped by stance/state —
     "what changed, which state, favorable/unfavorable for us, the Amira angle, source
     link." Skip if nothing new.
   - **Immediate big-move alert:** when a high-impact signal lands (e.g. a passed
     blanket restriction in a large state, or a new evidence-based carve-out), post
     immediately. Define "big move" via config thresholds (tunable).
3. **Voice:** post AS Callie via her existing Slack send path (reuse, don't
   reimplement). Her voice rules apply (concise, so-what first, source-linked, no
   headline-padding). Each item links the actual bill/policy source, not a headline.
4. **No double-posting:** dedup so a signal is reported once (reuse a posted-marker
   like the campaign push dedup pattern, but in the screentime namespace).

## Constraints / coordination
- Do NOT modify `callie_push.py` / campaign push. If you need a shared helper,
  import + call it; if it's not importable cleanly, add a thin caller in
  `artemis/screentime/`. Flag in COORDINATION.md if you must touch any marketing file.
- Additive `config.py` settings only. Lazy provider imports. Own test DB.
- Cost: digest composition can use a cheaper provider; it's text work.

## Verification (observe the EFFECT)
- With `screentime_report_channel` unset → no posts (dormant).
- Set to a test channel → a real digest posts in Callie's voice, source-linked, once
  (no duplicate on re-run).
- A fixtured "big move" triggers an immediate alert; a minor signal does not.
- Unit tests for digest selection (real moves only, dedup) + the big-move threshold.

**Deliverable:** committed to a worktree branch; report the channel/threshold config,
how you reused Callie's posting path (no campaign changes), the dedup mechanism, and
the live test-channel result.
