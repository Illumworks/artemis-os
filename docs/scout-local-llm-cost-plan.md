# Moving scout work to the Mac Studio

Written 2026-09-09. The goal is fewer Claude tokens on the highest-volume
workload in the system, without repeating the reason this was reverted last time.

## Why the scouts are the target

`agent_run` is where the spend is: **380 runs in 14 days**, roughly 27 a day
across nine scouts. Everything else is a rounding error next to it. The seven
Tier 3 features now routed locally (`background`, `meeting_summary`,
`memory_consolidation`, `memory_graph_extraction`, `okr_suggest_kr`,
`skill_distiller`, `trajectory_summary`) are low-volume by comparison, so the
routing fix on 2026-09-09 unblocked local inference but did **not** meaningfully
reduce cost. Saying otherwise would overstate it.

## Why it was reverted before, and what has changed

The R3 provider-rebalancing work moved scouts off Claude and was rolled back:
scout output is Claude-tuned and failed the validators. That is still true. What
is different now is that we have a **measured example of exactly how it fails**,
rather than a general worry.

### The measured failure: hedges get hardened

Same input, same model, real Argus research notes about Grosse Pointe. The source
said:

> "No prior Amira relationship **data available from external sources. Confirm
> via CRM.**"

`qwen3-coder-30b-a3b-instruct-mlx` summarised that as:

> "there is no existing Amira relationship"

Absence of data became absence of relationship. The CRM in fact holds **19
contacts and 5 Gong calls** for that district, so the summary was not merely
lossy, it was false in the direction that costs money: it would license a cold
opener to a district somebody has already worked.

**A prompt fixed it.** With an explicit rule ("preserve uncertainty exactly as
written; never turn 'no data available' into 'none exists'"), the same model on
the same input produced "unconfirmed or not available" and kept the hedge. 2.2
seconds.

That is the whole provider-hardening problem in one example, and it is testable.

## The staged plan

**Stage 1 — classify the nine scouts by what they actually do.** Not every scout
is the same risk. A scout that FETCHES and FILTERS is a different proposition
from one that JUDGES.

| Shape | Scouts | Local risk |
|---|---|---|
| Fetch and extract structured fields from a known format | `procurement`, `federal_funding`, `legislative` | **Low.** The output is fields, and a wrong field is visible. |
| Fetch, then decide whether something is relevant | `regional_news`, `state_doe`, `board_minutes` | **Medium.** Relevance judgement drifts quietly. |
| Judge and characterise | `leadership_transition`, `linkedin_observer`, `starbridge_researcher` | **High.** These write the reasoning a human reads. |

Start with the low-risk group. Information pulling is exactly where local
inference is safe, and it is three of the nine.

**Stage 2 — write the uncertainty rule into the scout prompts, for every
provider.** The hedge-hardening above is not a local-model quirk to be patched at
the routing layer; it is a prompt weakness that Claude happens to cover for. Fix
it in the prompt and both providers get better.

**Stage 3 — shadow-run before switching.** Run a scout on both providers over the
same inputs and diff the emitted signals. The acceptance test is not "did it
produce output", it is:

- Did it emit the same signals? Missing signals are the expensive failure.
- Did any hedge get hardened into a claim?
- Do the reason codes come from Josh's registry, or did it invent one? (This has
  happened before, with four invented codes reaching production.)
- Does every `source_url` resolve? Fabricated sources have reached the queue
  before: 149 of them.

**Stage 4 — switch the low-risk three, and watch the zero-yield detector.**
`uv run python -m artemis.ops` flags a scout that runs and emits nothing. If a
switched scout goes quiet, that is the detector doing its job, and the switch
gets reverted rather than debated.

## On a bigger model

Qwen 3.5 122B is worth trying and the Mac Studio has room. Two honest caveats.

**Size will not fix the failure we measured.** Hedge-hardening is a prompt-shape
problem; a larger model summarising the same notes without an explicit
uncertainty rule will compress "confirm via CRM" the same way, because that is
what summarisation does. Test the prompt fix first, since it is free.

**Check the reasoning-token trap before committing to any model.** The general
model already loaded, `qwen/qwen3.6-35b-a3b`, returns an **empty string** in this
setup: it spends the entire budget on reasoning tokens at 300, 400 and 1200
max_tokens, unaffected by a `/no_think` prefix or
`chat_template_kwargs.enable_thinking = false`. A 200 with valid JSON and no
content is the worst failure shape available. Any candidate model needs this
one-line check before it goes anywhere near a cascade:

```bash
uv run python -c "
import asyncio, httpx, artemis
from artemis.config import settings
async def m():
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f'{settings.lm_studio_base_url}/v1/chat/completions', json={
            'model': 'MODEL_NAME_HERE',
            'messages': [{'role':'user','content':'Reply with one sentence about reading screeners.'}],
            'max_tokens': 300})
    d = r.json()
    print('visible chars:', len(d['choices'][0]['message']['content'].strip()))
    print('usage:', d.get('usage'))
asyncio.run(m())"
```

Visible chars of 0 means do not use it, whatever the benchmarks say.

## What this does not cover

Callie's chat, signal classification and anything customer-facing stay on Claude.
The saving is real but it is not worth the surface: those are the paths where a
quietly-wrong answer reaches a person outside the company.
