# Plan — OS Multi-Team Expansion ("Marketing OS" → company intelligence layer)

**Status:** PLANNING / PARKED (captured 2026-06-13 from the Jun-12 meeting; Mark Angel + Amy Scholz steer).
**Not sequenced — not being built yet.** Distinct from the standalone credibility app
(`docs/product-data-credibility-app.md`).

## The pivot Mark + Amy are asking for
Make the OS **valuable to more than marketing.** Mark, after seeing the demo: "I see huge potential… what's
the intent for other parts of the org — sales, success, anybody else?" The signal/intelligence engine Jon
built (lossless memory, multi-source scouts, signal scoring, agents-in-Slack, claims/writing studio) is the
foundation; the ask is to serve **Sales (AEs)** and **Customer Success / Impact Directors** too.

## Mark's three hard requirements (verbatim intent)
1. **Different roles need different signals from the same data.** Lead-gen (Josh/sales) cares about different
   triggers than renewals/relationships (success). "You'll get a different take on signals from an impact
   director than from Josh… immense value in multiple directions."
2. **Role-based filtering is make-or-break — or it's spam.** A signal only helps an AE/CSM if it's *their*
   territory + *their* accounts/pipeline. "Otherwise you're just spamming them… they'll either exert energy
   where they shouldn't, or discount all the good energy because it's overwhelmed by too much stream." Needs
   **Salesforce** (territory + pipeline) to target.
3. **Fit the company MCP / data-interchange strategy — do NOT build a silo.** Mark's biggest concern: everyone
   "doing their own thing" → "non-maintainable and radically duplicative." There's "**Bernie**," who is
   "supposed to be doing exactly what you're doing." Jon's system should **plug into the shared connectors**
   (Salesforce, Gong, Churn Zero / "turn zero," onyx) so data **flows around the company**. "How is this going
   to fit into the MCP strategy we're using generally? How can we leverage the connectors and integration
   points?" Mark also sees Jon's working system as the **proof/pioneer** for whether the onyx/MCP investment
   actually works (e.g., the churn-zero MCP connector nobody's exercised).

## Current connectivity (Jon's demo answer)
Fully integrated: **Google Suite, Slack**. Backend built (not live): **Salesforce, HubSpot**. **Not yet:**
Gong, Churn Zero, onyx/Bernie. Hot-signals → Slack channels for sales is MVP'd (signal, not full noise).

## My recommendation (Lead read)
The center of gravity is **"get the right intelligence to the right person, credibly, through the company's
shared data fabric"** — not "add more agents." The expansion gates on **two enablers**, in this order:

1. **A targeting / identity layer** — every signal knows *who it's for* (territory → account → owning AE/CSM).
   This is what turns today's marketing signals into a multi-team product **without rebuilding** — same engine,
   role-aware routing. It's also Mark's #1 ("or it's spam"). **Requires Salesforce** (territory + pipeline);
   the backend already exists, needs wiring + the routing rules per role.
2. **A deliberate "how we plug into the MCP/connector fabric" design** — coordinate with **Bernie + the onyx
   team** on the shared connector strategy *before* building more connectors solo. This is partly an
   **org-coordination** task, not just a build (talk to Bernie; map Jon's connectors to the company's MCP
   strategy; decide what Jon owns vs. consumes from the fabric).

**Worst case if we skip these:** expanding to Sales/Success without filtering + MCP-fit (a) spams them into
ignoring it (Mark's explicit warning), and (b) builds the exact silo he warned against — sidelined as
"non-maintainable + duplicative" no matter how good. So filtering + MCP-fit are **the gate to adoption**, not
polish.

## Memory readiness (does the keystone fare?)
**Yes — the memory is architected for this (multi-scope, built for Salesforce/Gong/Churn-Zero); no rebuild.**
But company-wide volume makes three memory upgrades the gate to doing it *accurately + cheaply at scale*:
semantic conflict detection (M1), a retrieval eval/tuning harness (M2), and **scope/role-aware retrieval (M3)
— which IS the same per-role filter this expansion needs** (one build, two uses). Full analysis +
sequence: `docs/memory-readiness-and-upgrades.md`. These are sequenced before P6 in the build plan.

## Rough shape (when sequenced)
- **Phase A — Targeting layer:** Salesforce integration (territory + pipeline + account ownership) → every
  signal carries "who it's for"; role-scoped Slack routing (AE sees only their territory's hot signals; CSM
  sees renewal-relevant signals for their accounts). Kills the spam risk; unlocks multi-team with the existing
  engine.
- **Phase B — MCP/fabric alignment:** the Bernie/onyx conversation + design — what flows where, which
  connectors Jon owns vs. consumes, how the lossless memory/signal store exposes data company-wide vs.
  duplicates. (Likely a strategy doc + an exec/Bernie alignment, then a connector plan.)
- **Phase C — Success/renewal signal types:** add the impact-director / renewal lens (different scoring +
  playbook than lead-gen), once targeting + fabric are in place.

## Open questions for Jon
- **Sequence:** start with the targeting/Salesforce layer (the enabler), or the Bernie/MCP alignment first
  (since it may constrain the connector design)?
- **The Bernie piece:** org-coordination you'll navigate, vs. a design we produce for how Jon's system plugs
  in — or both?
- Which non-marketing team first — **Sales (lead-gen, Josh's lane)** or **Success (renewals)**?
