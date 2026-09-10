# Running scouts on the Studio

**Written 2026-09-10.** Records what was measured rather than assumed, because
two of the conclusions along the way were wrong and both looked convincing.

## Current routing

| Provider | Scouts |
|---|---|
| `lm-studio` → falls back to `claude-code` | regional_news, board_minutes, procurement, linkedin_observer |
| `claude-code` → `anthropic` | federal_funding, leadership_transition, legislative, starbridge_researcher, state_doe |

Routing is the `provider` / `fallback_provider` columns on the `agents` row. It is
a column update, not a deploy:

```sql
UPDATE agents SET provider='lm-studio', fallback_provider='claude-code'
WHERE agent_id = 'marketing.scout.<name>';
```

Reverting is the same statement with `claude-code` / `anthropic`.

## The measured position

**Local drives the full agent loop.** `marketing.scout.regional_news` on
`qwen/qwen3.6-35b-a3b` called `territory_config.get_priority_states`,
`reason_codes.get_allowlist`, `news_api.search`, `pdf_extractor.extract` and then
`signal_queue.write`, emitting signal 4750. Completed, no error.

**It costs 426 seconds and 377,223 input tokens.** Both are fine: the run is a
scheduled background job, and local tokens are free. Compare the Claude path at
roughly one minute and quota that Jon and the build sessions are competing for.

**Single-shot extraction is strong.** Against the scout's real `ScoutEmittedSignal`
validator on production-shaped input: 5/5, with every fact traceable to the source
and `districtId` correctly left empty on a state-level item — the hardest
instruction in the prompt and the one Gemini historically failed.

## Three things that made it look impossible

All three were ours, and each produced a convincing wrong conclusion.

**A 120-second client timeout.** `OpenAIAdapter` hardcodes it and LM Studio
inherited it. A loop test died at 132.1s and was read as "the model cannot drive a
loop". A local turn prefills ~24,500 tokens then generates at ~100 tok/s, so two
minutes is a normal turn. Now a class attribute, 600s on the local subclass only.

**A max_tokens below the reasoning budget.** These models think before emitting a
character, so a small budget is consumed entirely by the reasoning block and
returns `finish_reason="length"` with empty content — a 200 with a usable shape.
Measured: 200 tokens → 0 characters; 8192 → correct answer having spent 759. The
floor is now in the adapter. An earlier note in `feature_catalog` concluded from
300/400/1200 that the model was unusable; it was not.

**A model name no local server has.** A cascade hands one model name to every
provider, and the scouts are configured `claude-haiku-4-5`, so the Studio was
asked for a Claude model. The adapter now drops a name it cannot serve.

## How to judge whether it is working

Signal yield per run, against the Claude baseline, not "did it error":

```sql
SELECT a.provider, t.agent_id, count(*) runs,
       sum(t.output_tokens) out_tokens
FROM agent_traces t JOIN agents a USING (agent_id)
WHERE t.created_at > now() - interval '7 days' AND t.agent_id LIKE 'marketing.scout%'
GROUP BY 1, 2 ORDER BY 1, 2;
```

and the signals those runs produced:

```sql
SELECT discovered_by, count(*), max(created_at)::date
FROM signal_queue WHERE created_at > now() - interval '7 days' GROUP BY 1;
```

**Watch for silent degradation, not errors.** The fallback catches a provider that
fails; nothing catches a provider that succeeds and produces worse signals. A
local scout that runs clean and emits half the signals is the failure mode to look
for, and only the yield numbers show it.

## What is not settled

- **One clean loop run.** That proves it can work, not that it does reliably.
  Expand past these four only after a week of yield that matches.
- **The three near-dead scouts are on local because there is nothing to lose** —
  board_minutes, procurement and linkedin_observer produced 2 signals between
  them across 194 runs last month. Moving them saves quota; it does not make them
  useful. Fixing or retiring them is worth more than either provider.
- **Tool support warns falsely.** The adapter logs "does not support tool
  execution. Tools will be ignored" and then the tools are used anyway. Harmless
  today, misleading in a log, and it should be checked rather than trusted in
  either direction.
