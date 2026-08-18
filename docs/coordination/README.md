# Coordination and architecture decisions — recovered 2026-08-18

These documents governed the Artemis OS rebuild. Until 2026-08-18 they lived **only** at
`~/Desktop/Artemis/claudeck-artemis/` on the Mac Mini, and this repo's `CLAUDE.md` instructed every
agent to read them "before doing anything substantive" — pointing at an absolute path that did not
exist on any other machine, and had already moved on the Mini itself.

An agent following that instruction hit a missing directory and carried on **without the context it
had just been told was mandatory.**

They now live in this repo, so the reference resolves anywhere the code does. `claudeck-artemis`
itself was a fork of a third-party project (`github.com/hamedafarag/claudeck`) and was deleted from
the MacBook on 2026-08-17; its 275 text files are preserved at
`~/Artemis/_preserved/claudeck-artemis/` on that machine.

| File | What it is |
|---|---|
| `COORDINATION.md` | cross-repo coordination log |
| `PROJECT_LOG.md` | running project log |
| `CLAUDE_CODE_PLANNING_HANDOFF.md` | planning handoff |
| `decisions/artemis-python-rebuild.md` | **the gen-2 → gen-3 decision** — why the Python rebuild happened |
| `decisions/rebuild-phased-plan.md` | phased rebuild plan |
| `decisions/memory-v2-architecture.md` | memory architecture v2 |
| `decisions/j3-overview-contract.md` | J3 overview contract |
| `decisions/phase-g-floating-artemis-design.md` | Phase G floating UI design |
