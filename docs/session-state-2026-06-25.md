# Session State — Fable audit + build sprint (2026-06-25)

Durable record of what shipped, what's in flight, and what needs Jon's attention.
Companion to [docs/fable-audit-roadmap.md](fable-audit-roadmap.md) (full prioritized backlog).

## ✅ Shipped & live on main (verified, restarted)
| What | Commit | Notes |
|---|---|---|
| Kai product taxonomy (Lectura ILP → Enseñar) | 320d591 | query-expansion + persona glossary |
| Agents answer thread replies on their own posts | 3ba2ccf | Kai/Slack |
| **SECURITY: memory-scope agent_id spoof closed** | 6f3e884 | subprocess binds to live caller, not stored agent_id |
| **SECURITY: /api/integrations owner-gated + CORS pinned** | 78dcf0d | was fully open to non-owners |
| Agent Report Card (LLM-as-judge evals) | f477793 | `uv run python -m artemis.evals`; grades Artemis/Callie/Ares |
| Memory quality pass | a8df6ae | consolidation propagates confidence/evidence (stops self-penalty); near-dup deduper wired into daily maintenance; hit_count fix; bounded conflict pool |
| Scout contract fix + board peer-validation scout | 470d6bd | contract fix unblocks ALL scouts; board scout **DISABLED by default** |
| **SECURITY fast-follows** | ed4968f | prod fail-open guard, WS auth under CF, SSRF egress guard, defusedxml 0.7.1 |
| Tier-1: Artemis owner-gate + WS drag-drop | bea0da1 | no more proposing meetings for others' commitments; folders stick |
| **ARTEMIS_ENV=production** (repo .env) | (config) | activates the fail-open guard; verified guard passes with live CF config |

## 🔶 Needs Jon's attention (parked — no action required now, don't lose)
1. **WS auth live smoke.** The security fix now requires a Cloudflare-verified JWT on the *agent-monitor* and *workflow* streaming panels (Artemis chat WS untouched). Through CF it should stream fine; **if those panels stop live-updating, that's the cause** — revert `artemis/ws/routes.py` (one file) and it's back. Worth a glance when next in those views.
2. **Board peer-validation scout is built but DISABLED.** Blocked on: (a) the Amira customer list from Salesforce (Neil — API/MCP access pending) to power the non-customer exclusion, and (b) a prioritized national district seed list. Enable in `config/scouts.yaml` once both land.
3. **Screen-Time Watch autopilot still not wired.** cron/digest/seed are dormant (manual runs only), parked pending Angela's stance review. BEFORE wiring the cron: fix the LegiScan status mapping (vetoed/failed bills currently read as "passed" → would fire false alerts) and the seeded display-only pipeline flag. (Details in the roadmap.)
4. **Report card grades fixtures today.** To get real numbers on Artemis/Callie/Ares, feed real captured outputs — a clean follow-up behind the existing API.
5. **Tableau screen-time tracker (Whiteboard Advisors):** decided NOT to scrape (their product, downloads disabled). Broadening our own scouts instead (in flight). Revisit only if we ever want their curation specifically.

## ✅ Broaden Screen-Time scout coverage — MERGED (main 3bbb2db)
News outlets (Chalkbeat/EdSource/K-12 Dive/EdWeek/GovTech/The 74/Hechinger/Axios) + keywords; state-DOE 7→20 states; board scout wired into the fan-out with a 13-district live-verified seed. Board scout kept **DISABLED** (Salesforce exclusion pending — enabling would flag customers as "peer validation").
  - ✅ **Topic gate widened to include AI-in-schools policy — LIVE (main 855ae82).** topic_config.py v3 + LegiScan fanout now admit AI-policy findings (14 multi-word anchors, never bare "ai"). Verified no stored DB 'topic' row overrides the new DEFAULT, so it's active. Screen-Time Watch now tracks screen-time AND AI-in-schools policy as one story. **AI-policy STANCE tuning DEFERRED to the Angela stance review** (a ban on open chatbots is NOT unfavorable to Amira — standards-aligned carve-out); AI findings currently take best-effort stance + a TODO in stance_config.py.

## ⏭️ Deferred (in the roadmap)
Memory embedding refresh (MiniLM→bge) + eval A/B; marketing gate-timeout (DB-backed); frontend XSS-escaping unify; scout dedup cursor; Forge items (handed to the app-seat Opus); Callie self-serve COO report (Jon has a manual report for now).

## Fable trial
~2-day promo window used for the hard/greenfield builds above (report card, memory, scout, security). Method + full findings in memory `project-fable-system-audit` and the roadmap doc.
