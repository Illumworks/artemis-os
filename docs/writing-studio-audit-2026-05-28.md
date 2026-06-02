# Writing Studio Audit — 2026-05-28

**Trigger:** Jon's precautionary ask given the session's hollowness pattern. The Writing Studio is the centralized writing surface trained by Angela/Olivia/Julie and called by pipelines (currently marketing) — a hollow Studio would be a single point of failure for every pipeline that needs writing.

**Verdict (one line):** the Studio *surface* is healthy and substantial; the pipeline → Studio handoff has the same hollowness pattern as the qualifier had pre-CC4 (declared tools that were never implemented).

---

## W1 — Content-agent tools missing from the registry (CRITICAL, same pattern as pre-CC4 qualifier)

The three content agents declare tools that don't exist in the registry — silently dropped by the MCP server's per-agent scoping (so the agent runs with effectively no tools, just like the pre-CC4 qualifier):

| Agent | Declared tools | In registry? |
|---|---|---|
| `marketing.content.brief_assembler` | `campaign_brief.read`, `campaign_brief.write` | only `.write` is real; `.read` is **missing** |
| `marketing.content.asset_selector` | `content_registry.list_approved_assets`, `claude.complete` | **both missing** (and `claude.complete` looks misdeclared — that's the LLM itself, not a custom tool) |
| `marketing.content.writing_studio_adapter` | `writing_studio.enqueue` | **missing** — the boundary tool that's supposed to push drafts into the Studio |

`known_tool_names()` confirms: **zero writing-related or content-read tools** in the 37-tool registry. The MCP server, given an agent with all-unknown tools, exposes none — so the content agents have nothing to call. They run, "succeed" via LLM chat, and emit no work-product.

**Fix shape (CC12):** same as CC4 for the qualifier. Implement the missing tools against existing modules — `writing_studio.enqueue` wraps the already-built `artemis/marketing/writing_studio/invoke.py` (which does the real work: creates a deliverable, builds the metadata bundle, publishes events); `campaign_brief.read` wraps the existing campaign_briefs read path; `content_registry.list_approved_assets` wraps the asset link query. Plus drop the misdeclared `claude.complete` (the LLM is implicit, not a tool). All bounded — same pattern P3/CC4 established.

## W2 — Q5's "one central writing agent" diverged in implementation (architectural, bank)

Master plan Q5 (RESOLVED): *"The Writing Studio is ONE agent, trained by Angela/Olivia/Julie. It gets called from any workflow under context-specific guidelines."* But the agent in the system is `marketing.content.writing_studio_adapter` — namespaced **under marketing**, not as a domain-agnostic central agent. If a future sales/support pipeline needs writing, it'd need its own adapter rather than calling the same central agent.

The Studio *surface* (UI + rules + voice profile + examples) is general — domain-agnostic content. It's only the *adapter agent* that's marketing-scoped.

**Recommendation (bank):** when the platform expands beyond the marketing seed, lift the writing-adapter agent out of `marketing.content.*` into a domain-neutral location (e.g. `writing.studio.adapter`) — same agent, called by any pipeline. Or: keep per-pipeline adapter agents but ensure they all share the same Studio backend (current architecture supports this — the rules/profile/examples tables are not marketing-scoped). Either approach honors Q5; document the intent so the next pipeline that needs writing doesn't accidentally re-invent.

## W3 — The Studio surface itself is real (not hollow)

Substantial and verified:
- **UI:** `public/js/features/writing-studio.js` — 3,334 lines. Renders drafts, folder browser, organization rail, sync card, draft rows. This is a real surface, not a stub.
- **Backend:** `artemis/marketing/routes/writing_studio.py` — full CRUD endpoints (drafts list/detail/update/archive), Gate-2 review submission, webhook receiver for the external Studio. Plus the supporting modules: `artemis/marketing/writing_studio/{invoke,external,adapter,events,sync}.py` (five files — real machinery).
- **Data tables:** `writing_profiles` (1), `writing_rules` (2), `writing_examples` (7), `writing_folders` (1), `writing_sources` (9), plus `floating_artemis_voice_corpus`. The Studio has seeded data for Angela/Julie/Olivia to build on.
- **State machine:** the marketing state machine handles deliverable lifecycle (`DeliverableState` enum, transitions). Wired.

So when humans use the Studio UI — they have a real surface with real data. **The Studio's HUMAN side works.**

## W4 — External Studio handoff is a stub by default (v1 design — fine)

`invoke.py` notes: *"Uses ExternalWritingStudio (Stub by default) from .external."* The "external" Studio (a separate writing service) is intentionally stubbed for v1 — humans-in-the-loop using the in-app Studio UI is the design. **Not a bug.** Worth banking as a future integration point if/when an external Studio service is brought online.

## W5 — Self-improvement integration is automatic once CC10+CC11 land

The `marketing.content.writing_studio_adapter` (and the other content agents) are in the `agents` table → their runs land in `agent_runs` → CC10+CC11 will produce trajectory summaries for them → the Builder will be able to propose adapter-prompt or voice-rule improvements with run-id citations. **No additional wiring needed.** As soon as the self-improvement loop is live, the Writing Studio agents get the same treatment as scouts and qualifier.

## W6 — Drafts have reached the Studio before (1 deliverable, 3 brief_snapshots) — but the route is broken now

- `campaign_briefs = 0` (CC5 noted: this fills only after Gate-1 human approval).
- `campaign_deliverables = 1` (a draft made it through historically).
- `brief_snapshots = 3` (qualifier → brief_composer's output via CC4 — proven this session).

So the route from qualifier-brief → Studio HAS worked historically (1 deliverable) but is now broken on the content-team-tools side (W1). CC12 reopens it.

---

## Recommended sequence (no change to current plan)

1. **Merge CC8/CC9/CC10/CC11 together** (in flight). That delivers: serialized runs, dedup, self-improvement loop alive.
2. **Then CC12 — content-agent tools** (this audit's deliverable). Implements `campaign_brief.read`, `content_registry.list_approved_assets`, `writing_studio.enqueue`, drops `claude.complete`. Same scope as CC4 for the qualifier. Closes the content half of the marketing pipeline → Studio handoff.
3. **THEN SP1** — with the *entire* marketing chain working AND self-improvement active, the Signal Playbook is built on a platform that's both functionally complete and actively learning.

After SP1: the W2 architectural cleanup (lift the writing-adapter to a domain-neutral location) is worth doing before the platform expands beyond the marketing seed.

---

## Bottom line for Jon

The Writing Studio is healthier than the hollowness pattern would suggest. The *human* side (UI, rules, voice profile, examples) is real and substantive — Angela/Julie/Olivia have a working surface to train on. **The pipe FROM the pipeline INTO the Studio is broken**, and it's broken in exactly the way the qualifier was broken pre-CC4: missing tool implementations. The fix is bounded (CC12) and well-understood. Once it lands, drafts reach the Studio again, humans + AI can both contribute to the writing, and the self-improvement loop (via CC10+CC11) will tell us what the content agents are stalling on.
