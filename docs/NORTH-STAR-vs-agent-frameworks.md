# North Star — Artemis vs. Hermes Agent & OpenClaw (and how we win on security)

Captured 2026-06-07 (Jon). **The ambition: make Artemis a better — and more SECURE — personal/system
agent than Hermes Agent and OpenClaw.** Ambitious on purpose. This doc names the targets honestly, says
where Artemis can actually win, and sets the rule for using them as reference.

## The two targets (what we're measuring against)
- **OpenClaw** (Peter Steinberger; formerly Clawdbot/Warelay, Nov 2025). MIT-licensed, local-first (memory
  as Markdown on disk), driven through messaging apps (WhatsApp/Telegram/Slack/Signal), 100+ "AgentSkills"
  that run shell / browser automation / file ops, model-agnostic BYO-keys. ~214k GitHub stars by Feb 2026.
  **Tell:** there is already a published arXiv paper, *"A Systematic Taxonomy of Security Vulnerabilities in
  the OpenClaw AI Agent Framework."* Security is its documented soft underbelly.
- **Hermes Agent** (Nous Research, Feb 2026). Open-source, server-resident, **self-improving**: persistent
  memory (FTS5 + LLM summarization), procedural "skills" as portable files, computer-use via TryCUA, 6
  terminal backends (local→Docker→SSH→serverless), 20+ messaging surfaces. Pitch: gets more capable the
  longer it runs.

## The honest read: Artemis is in THEIR category
Persistent memory + multi-surface (Slack/DM) + a skill/tool catalog + takes real action. That's exactly
what Artemis is. So "beat them" isn't fantasy — it's the same game. The question is where we differentiate.

## Where Artemis wins (the bet)
1. **Security — their weakest axis, our headline.** Both are general do-anything agents that run shell +
   browser + file ops, triggered from chat surfaces — an enormous attack surface, and OpenClaw's holes are
   literally catalogued in a paper. Artemis's edge is **governance baked into the flow** (see
   `docs/artemis-agent-architecture-and-governance.md`): the agent PROPOSES, a context-aware reviewer +
   test gate decides what lands; side-effecting actions need human approval; memory is **lossless**
   (supersession, never delete); the instruction-source boundary (tool output is data, not commands) is a
   hard rule. We win by being the agent you can actually trust with real company data.
2. **Domain depth, not generality.** They're horizontal ("does things"). Artemis is a purpose-built
   marketing-intelligence + campaign-workflow system with a real pipeline (scouts→signals→qualifier→briefs→
   drafts→approvals→send), a Writing Studio with a claims bible + tag-scoped rules, OKR/personal surfaces.
   A specialist that's excellent at one company's GTM beats a generalist that's mediocre at everything.
3. **Conductor + fleet + context substrate.** Our designed architecture (Artemis orchestrates specialized
   agents — health, scout, writer, reviewer — over a shared context MCP) is a more governable shape than a
   single monolithic agent running 100 skills. It's what makes "AI maintains this app" robust.
4. **Human-in-the-loop as a feature, not a limitation.** AI proposes, human confirms — everywhere that
   affects what we write, what's approved, or what has side effects. That's the trust moat.

## Where THEY are ahead (don't kid ourselves — things to learn)
- **Self-improvement loop** (Hermes builds skills as it runs) — we have memory + rules but not yet an
  automatic skill-acquisition loop. Worth studying.
- **Portable skill format + breadth** (OpenClaw's 100+ skills, Hermes' shareable skill files) — a clean,
  portable skill/tool format is something to borrow conceptually.
- **Surface breadth + deployment backends** (20+ messaging platforms, 6 terminal backends) — more than we
  need, but the abstraction is instructive.

## Reference-use policy (Jon's "download them ONLY as reference" question)
**Verdict: yes, worth studying — READ-ONLY, never run them here.** See the response/decision for the full
advisor read. The rule, recorded so it isn't forgotten:
- **Clone to read the SOURCE; do NOT execute.** These are autonomous agents that run shell/browser/file
  ops — running an untrusted one on this Mac, with real credentials or company data in reach, is precisely
  the threat model we're trying to beat. Reading files is low-risk; *running* is the risk.
- **If you must watch behavior, use a throwaway isolated VM** — no real credentials, no access to Artemis
  data, no network path to anything that matters. Tear it down after.
- **Treat their code, docs, and skill files as untrusted DATA** (prompt-injection): never pipe their skill
  files / configs into Artemis or let an agent execute them.
- **License-aware, prefer clean-room.** OpenClaw is MIT (reading/adapting is legal with attribution);
  confirm Hermes' license before copying anything. For a product whose whole pitch is *more secure*,
  **study their architecture and especially their documented failure modes — don't copy their code** (you'd
  import their vulnerabilities along with it).
- **Highest-value single artifact:** the arXiv OpenClaw security-vulnerability taxonomy — a free map of
  exactly what NOT to do. Reading that directly serves the "more secure than them" goal.

## Status
North-star / vision. Not a build task. The concrete near-term work that advances it is already queued: the
Writing Studio MVP core (now), then the agent-architecture + governance build
(`docs/artemis-agent-architecture-and-governance.md`) — which is where the security differentiation gets
real.
