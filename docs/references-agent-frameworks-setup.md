# Reference setup — Hermes Agent & OpenClaw (read-only study material)

Captured 2026-06-07 (Jon). Companion to `docs/NORTH-STAR-vs-agent-frameworks.md`. Jon downloads the code;
**Opus Lead does the reading and produces the findings.** This note says exactly where to put it, the
guardrails that keep it safe, and the reading plan I'll run when we reach the agent-architecture build.

## Where to put it (OUTSIDE this repo — important)
Do NOT clone these into `artemis-os/`. They're large external codebases (Hermes alone is ~184k lines of
Python); committing them would bloat the repo, muddy the license boundary, and risk an untrusted file
getting executed by our tooling. Put them in a SIBLING folder I can still read:

```
/Users/artemis/Artemis/agent-references/      ← make this folder
├── openclaw/            ← github.com/openclaw/openclaw
├── hermes-agent/        ← github.com/NousResearch/hermes-agent
├── hermes-self-evolution/ ← github.com/NousResearch/hermes-agent-self-evolution  (their learning loop)
└── papers/              ← drop the arXiv PDFs here (see reading list)
```

I have read access to `/Users/artemis/Artemis/`, so anything under `agent-references/` is reachable
without it living in our repo.

> **Path corrected 2026-08-20.** This doc said `~/Desktop/Artemis/` until then; the material actually
> landed in `~/Artemis/agent-references/`. Nothing was ever at the Desktop path.

## Exact commands (paste these — clone only, do not run the apps)
```bash
mkdir -p /Users/artemis/Artemis/agent-references/papers
cd /Users/artemis/Artemis/agent-references
git clone --depth 1 https://github.com/openclaw/openclaw.git openclaw
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git hermes-agent
git clone --depth 1 https://github.com/NousResearch/hermes-agent-self-evolution.git hermes-self-evolution
```
`--depth 1` grabs just the current source (no full history) — smaller and all we need for reading.

**Licensing is two-of-three, not "both projects."** Verified against the downloaded trees on 2026-08-20:

| Repo | LICENSE file | Terms |
|---|---|---|
| `openclaw` | present | MIT — © 2026 OpenClaw Foundation |
| `hermes-agent` | present | MIT — © 2025 Nous Research |
| `hermes-agent-self-evolution` | **absent** | **none shipped — all rights reserved by default** |

The third repo ships no LICENSE and no license field, so it is **not** MIT and this doc was wrong to imply
it. Absent a license, default copyright applies: reading it is fine, adapting anything from it is not.
Treat it as **read-only inspiration** — if the self-evolution loop (reading-list item 5) turns out to be
worth building, it gets a clean-room design from the *paper and concepts*, never from that source. Re-check
upstream for an added LICENSE before assuming otherwise.

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
   **⚠ STILL NOT DONE (checked 2026-08-20).** `papers/` was never created and this PDF was never
   downloaded. The doc's own #1 priority sat unfetched for ten weeks while 333 MB of source we could
   re-download in minutes sat on disk instead. It is a ~2 MB paper, it is the one item here that is
   *not* trivially re-obtainable from a clone, and it is the only item whose value does not depend on
   reading 200k lines of someone else's code. Fetch it first, before any re-clone:

   ```bash
   mkdir -p ~/Artemis/agent-references/papers
   curl -Lo ~/Artemis/agent-references/papers/2603.27517-openclaw-vuln-taxonomy.pdf \
     https://arxiv.org/pdf/2603.27517
   ```
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
7. **Hermes Desktop UX** (`hermes-agent/apps/desktop` — comes with the clone; MIT, v0.15.2, released
   2026-06-02). A native GUI over the same agent: streaming tool output, a right-hand preview pane, file
   browser, voice, credential/model settings. **Strong front-end reference** for Artemis's own interface —
   read the source for layout/UX ideas. NOTE: do NOT install the downloadable desktop *binary* from their
   site — it runs the full autonomous agent, same never-run rule as the CLI. The source in `apps/desktop` is
   safe to read.

## What I deliver when we get there
A findings doc per the reading list: for each area — *what they do · what's good · what's risky · what
Artemis should adopt / avoid / do differently*, mapped onto our existing design docs. The security taxonomy
becomes a checklist we make sure Artemis passes. None of this is a build task now; it's the study phase that
front-loads the agent-architecture + governance build (`docs/artemis-agent-architecture-and-governance.md`),
which is queued behind the Writing Studio MVP core.

---

## Restoring this material (deleted from disk 2026-08-20)

**The 333 MB cache is gone; nothing authored was in it.** Deleted after verifying it was a
*cache, not an archive*: no `.git` directories anywhere, every folder carrying the `-main` branch
suffix of a GitHub ZIP download, and **every single file in each tree sharing one mtime to the
minute** — 4,775 files at `2026-06-07 15:54`, 19,829 at `2026-06-07 18:26`, 29 at
`2026-03-29 11:47`. Not one file was newer than its extraction. Zero local modifications, zero
notes, zero findings, no `.env` (only the repos' own `.env.example`). All three upstreams were
confirmed anonymously cloneable the same day.

Re-obtain it with the commands below. **Read the guardrails above first** — the never-run rule is
the entire reason this material is allowed on this machine at all.

```bash
mkdir -p ~/Artemis/agent-references/papers && cd ~/Artemis/agent-references
git clone --depth 1 https://github.com/openclaw/openclaw.git openclaw
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git hermes-agent
git clone --depth 1 https://github.com/NousResearch/hermes-agent-self-evolution.git hermes-self-evolution
curl -Lo papers/2603.27517-openclaw-vuln-taxonomy.pdf https://arxiv.org/pdf/2603.27517
```

`--depth 1` clones are substantially smaller than what was deleted — the 333 MB included
`node_modules`-class vendored trees that a shallow clone of source alone does not carry.

### ⚠ Clone to READ. Never install, never run.
Restoring this puts three autonomous agent frameworks — code that executes shell, file and browser
operations — onto a machine holding live Amira credentials, a production Postgres, and Slack/Google
tokens. `npm install` and `pip install` alone execute arbitrary post-install hooks; that is enough
to lose the machine. Read the source. Do not install dependencies, do not run setup or start
scripts, do not open their configs with anything that executes them. If one ever has to be watched
*behaving*, it happens in a throwaway VM with no real keys and no route to Artemis data.

### What a restore does NOT give you back
These were ZIP downloads, so **no commit SHA was recoverable** — there is no way to reproduce the
exact bytes that were studied. A fresh clone gets current upstream `HEAD`, which will have moved.
The versions that were on disk, for reference if a specific one ever matters:

| Repo | Version on disk | Downloaded |
|---|---|---|
| `openclaw` | `2026.6.2` | 2026-06-07 |
| `hermes-agent` | `0.16.0` (desktop app `v0.15.2`) | 2026-06-07 |
| `hermes-agent-self-evolution` | `0.1.0` | 2026-03-29 |

This is the cost of deleting a cache with no provenance, and it is cheap here **only because
nothing had been read yet** — no findings depended on those exact bytes. Had the reading-list
findings doc existed and cited line numbers, this table would not have been enough. If the study
phase ever starts in earnest, clone properly and record the SHA.
