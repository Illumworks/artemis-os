# The Mac Studio, and what can safely run on it

Audited 2026-09-09. The short version: the box is real, reachable and usable, the
routing was already built, and it was pointed at a model that does not exist
there, so it has never been used.

## What is actually there

`ARTEMIS_LM_STUDIO_BASE_URL` = `http://100.89.191.2:1234` (Tailscale). Four models
loaded:

| Model | Verdict |
|---|---|
| `qwen3-coder-30b-a3b-instruct-mlx` | **The one to use.** Clean output, 4-12s for short work. |
| `qwen/qwen3.6-35b-a3b` | **Unusable as configured.** See below. |
| `qwen/qwen3-coder-next` | Untested |
| `text-embedding-nomic-embed-text-v1.5` | Embeddings. We already embed locally in-process with all-MiniLM-L6-v2, so there is nothing to save here. |

## Two reasons it was never being used

**The catalog named a model that is not loaded.** `_T3_LM_FIRST` routed to
`qwen/qwen3-14b`. Nothing on the Mac Studio answers to that name, so every Tier 3
call failed at lm-studio and cascaded to Gemini and then Claude. Nothing reported
it, because a cascade that falls through looks exactly like one that was never
configured. Fixed.

**The general model returns nothing.** `qwen/qwen3.6-35b-a3b` is a reasoning
model, and in this LM Studio setup it spends its whole budget on reasoning tokens
and returns an EMPTY string. Measured at 300, 400 and 1200 `max_tokens`, and
unchanged by a `/no_think` prefix or `chat_template_kwargs.enable_thinking =
false`:

| max_tokens | reasoning tokens | visible characters |
|---|---|---|
| 400 | 399 | 0 |
| 1200 | 1199 | 0 |

An empty reply is the worst failure shape available: HTTP 200, valid JSON, a
usable-looking response object, and no content. Anything downstream that checks
"did the call succeed" rather than "is there text" would sail straight past it.
The coder-instruct model returns 0 reasoning tokens and real text, which is why
Tier 3 points there.

## What runs there now

The seven Tier 3 features: `background`, `meeting_summary`,
`memory_consolidation`, `memory_graph_extraction`, `okr_suggest_kr`,
`skill_distiller`, `trajectory_summary`. All internal, none customer-facing, none
gating a decision.

## What must NOT move there, and why

**Scouts (`agent_run`, Tier 1).** This is where the tokens actually go: 380 runs
in 14 days. It is also the workload that was tried on non-Claude providers before
and reverted, because scout output is Claude-tuned and failed the validators. The
saving is real and so is the reason it was rejected. See
`docs/provider-output-hardening.md`; that work is the prerequisite, not this
routing change.

**Anything customer-facing.** Callie's chat, signal classification, anything whose
output reaches a person outside the company.

## Verifying it

```bash
uv run python -c "
import asyncio, httpx, artemis
from artemis.config import settings
async def m():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f'{settings.lm_studio_base_url}/v1/models')
    print([m['id'] for m in r.json()['data']])
asyncio.run(m())"
```

If `qwen3-coder-30b-a3b-instruct-mlx` is missing from that list, Tier 3 is
silently running on Gemini or Claude again. **Check the list, not the health
probe**: the probe only asks whether the endpoint answers, and it answers whether
or not the routed model is loaded.

## The honest caveat

Nothing here proves output QUALITY on real workloads, only that the pipe works
and returns text. The right next step is to run one Tier 3 feature on it against
real inputs and read the output, rather than assuming a working pipe means a
working feature. That is the same discipline as everything else in this repo: a
200 is not a result.
