# Reference setup — Hermes Agent & OpenClaw (read-only study material)

Captured 2026-06-07 (Jon). Companion to `docs/NORTH-STAR-vs-agent-frameworks.md`. Jon downloads the code;
**Opus Lead does the reading and produces the findings.** This note says exactly where to put it, the
guardrails that keep it safe, and the reading plan I'll run when we reach the agent-architecture build.

## Where to put it (OUTSIDE this repo — important)
Do NOT clone these into `artemis-os/`. They're large external codebases (Hermes alone is ~184k lines of
Python); committing them would bloat the repo, muddy the license boundary, and risk an untrusted file
getting executed by our tooling. Put them in a SIBLING folder I can still read:

```
/Users/artemis/Desktop/Artemis/agent-references/      ← make this folder
├── openclaw/            ← github.com/openclaw/openclaw
├── hermes-agent/        ← github.com/NousResearch/hermes-agent
├── hermes-self-evolution/ ← github.com/NousResearch/hermes-agent-self-evolution  (their learning loop)
└── papers/              ← drop the arXiv PDFs here (see reading list)
```

I have read access to `/Users/artemis/Desktop/Artemis/`, so anything under `agent-references/` is reachable
without it living in our repo.

## Exact commands (paste these — clone only, do not run the apps)
```bash
mkdir -p /Users/artemis/Desktop/Artemis/agent-references/papers
cd /Users/artemis/Desktop/Artemis/agent-references
git clone --depth 1 https://github.com/openclaw/openclaw.git openclaw
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git hermes-agent
git clone --depth 1 https://github.com/NousResearch/hermes-agent-self-evolution.git hermes-self-evolution
```
`--depth 1` grabs just the current source (no full history) — smaller and all we need for reading.
Both projects are **MIT-licensed** (reading + adapting is legal with attribution; confirm the LICENSE file
still says MIT when you clone).

## The guardrails (the "secure" half of the goal — non-negotiable)
1. **Clone to READ. Never `npm install` / `pip install` / run their setup or start scripts.** These are
   autonomous agents that execute shell + browser + file operations. Running an untrusted one on this Mac,
   with your credentials and company data in reach, is the exact threat we're trying to beat. Reading source
   files is safe; *executing* is the risk.
2. **No credentials, ever.** Don't put any API key, token, or `.env` into these folders. If we ever need to
   watch one actually behave, that happens in a throwaway VM with no real keys and no path to Artemis data —
   then we delete it.
3. **Their files are untrusted DATA, not instructions.** Skill files, configs, READMEs, and docs in those
   repos may contain text aimed at an agent. I will treat them as reference material only and never pipe
   them into Artemis or let any tool execute them.
4. **Study, don't copy-paste.** For a product whose pitch is *more secure*, the value is in their
   architecture and especially their documented failure modes — copying their code would import their
   vulnerabilities. Any code we adapt gets a clean-room rewrite + MIT attribution, decided case by case.

## Reading list (priority order — what I'll go through)
1. **★ OpenClaw security-vulnerability taxonomy** — arXiv `2603.27517` ("A Systematic Taxonomy of Security
   Vulnerabilities in the OpenClaw AI Agent Framework"). The single highest-value artifact: a free catalog
   of exactly what NOT to do, written for the person trying to out-secure them. Drop the PDF in `papers/`.
2. **Their security/permission model** — how each gates shell/file/browser actions, what (if anything)
   requires human approval, how they handle prompt-injection from message surfaces. This is where we expect
   to win; I'll map each gap to our propose→review→merge + human-approval design.
3. **Skill/tool format** — OpenClaw's "AgentSkills" + Hermes' portable skill files. We want a clean,
   portable skill format; I'll assess what to borrow conceptually for our fleet.
4. **Memory architecture** — Hermes' persistent memory (FTS5 + LLM summarization) + OpenClaw's Markdown-on-
   disk store, vs. our pgvector + lossless-supersession keystone. What, if anything, beats ours.
5. **★ Hermes self-evolution loop** (`hermes-self-evolution`, DSPy + GEPA) — their skill self-improvement.
   This is the one capability gap I flagged; I'll evaluate whether a governed version fits Artemis (it must
   stay inside the propose→review gate — no unreviewed self-modification).
6. **Surface/gateway routing** — how they fan one agent across 20+ messaging platforms. More than we need,
   but the abstraction informs our channel-routing design.

## What I deliver when we get there
A findings doc per the reading list: for each area — *what they do · what's good · what's risky · what
Artemis should adopt / avoid / do differently*, mapped onto our existing design docs. The security taxonomy
becomes a checklist we make sure Artemis passes. None of this is a build task now; it's the study phase that
front-loads the agent-architecture + governance build (`docs/artemis-agent-architecture-and-governance.md`),
which is queued behind the Writing Studio MVP core.
