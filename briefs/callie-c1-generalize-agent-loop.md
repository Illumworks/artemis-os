# Worker Brief — Callie C1: Generalize the Agent Loop by agent_id

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies + merges.
**Status:** READY. **Branch:** `worker/callie-c1-generalize-loop`. **No Slack token needed.**
**Design:** `docs/callie-build-plan.md` (phase C1). Persona committed: `callie-personality-profile.md` v1.1.3.

## Why
Callie is a second Named agent that will run the SAME Floating Artemis loop with her own persona and (later)
marketing scope. Today the loop hardcodes Artemis's identity. C1 makes the loop **persona-parameterized by
`agent_id`**, defaulting to `"artemis"` so **Artemis's behavior is byte-for-byte unchanged**. No Slack/multi-
bot work here (that is C2); this is the safe foundation.

## Scope

### 1. Agent profile factory
Generalize persona loading (currently `artemis/floating_artemis/personality.py`, which globally loads
`artemis-personality-profile.md` and parses the voice corpus). Add:

```python
@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    display_name: str          # "Artemis" | "Callie"
    persona_core: str          # the distilled lead-in rules block
    profile_text: str          # full <agent>-personality-profile.md
    voice_corpus: list[str]    # parsed "Characteristic phrases"

def load_agent_profile(agent_id: str) -> AgentProfile: ...
```
- `"artemis"` → `persona_core` = the existing `_PERSONA_CORE` text currently inlined in chat.py:57-93,
  `profile_text` = `artemis-personality-profile.md`, voice corpus as today.
- `"callie"` → `profile_text` = `callie-personality-profile.md`, `voice_corpus` parsed the same way (her
  "Characteristic phrases" section), `display_name` = "Callie". For `persona_core`, distill a short lead-in
  from her profile's Identity + key rules (mirroring how Artemis's `_PERSONA_CORE` distills hers — keep it
  factual, no new persona invention; it is a condensation of the committed v1.1.3 doc).
- Cache per agent_id (module-level, like today). Missing file → empty strings, never raise (current
  fallback behavior preserved).
- `select_voice_samples` gains an optional `voice_corpus` param (default = Artemis's global, so existing
  callers are unchanged).

### 2. Parameterize the system prompt
`_build_system_prompt` (chat.py): accept the agent's `persona_core`, `profile_text`, `display_name` instead
of relying on the module-global Artemis constants. Move the inlined `_PERSONA_CORE` out so it is supplied by
`load_agent_profile("artemis")`. The Slack personal-DM block ("You are Jon's orchestrator and personal
partner… Marketing is Callie's lane") is **Artemis-specific** — gate it so it only renders for
`agent_id == "artemis"` (Callie gets her own framing later in C2/C3; for C1 a neutral default is fine).

### 3. Thread agent_id through handle_turn
- `handle_turn` reads `agent_id` from session metadata via the already-loaded `_SessionContext`
  (default `"artemis"` when absent — which is every session today).
- Load `AgentProfile` for that agent_id and feed persona_core/profile/voice/display_name into the prompt
  and `select_voice_samples`.
- **Do NOT change surface resolution or routing here.** `route_inbound` still does not set a callie agent_id
  (that is C2), so in practice every live session resolves to "artemis" and behaves exactly as today.

## Constraints
- **Zero behavior change for Artemis.** The default (`agent_id="artemis"`) path must produce the identical
  system prompt + voice sampling as before. Prove it (snapshot/intent test).
- Lossless; no new dependencies (org policy). ruff + mypy strict clean; run `./scripts/check.sh`.
- Do not touch the P1 inbound guards or slice-1 scope logic.

## Tests
- `load_agent_profile("artemis")` returns the current persona_core + voice corpus (≥ the 12 phrases, 0 em
  dashes); `load_agent_profile("callie")` returns Callie's display_name/profile/voice (her phrases).
- `_build_system_prompt` with the artemis profile == today's output for the same inputs (regression/snapshot).
- `handle_turn` with no agent_id in metadata behaves as artemis; with `agent_id="callie"` in metadata, the
  prompt leads with Callie's persona (unit-level; no Slack needed).
- Full FA suite stays green (`artemis/floating_artemis/tests/`), plus the P1 + slice-1 Slack tests.

## Acceptance
The loop runs as either agent purely by `agent_id`; Artemis is unchanged (tests prove it); Callie's persona
loads and would drive the prompt. Nothing goes live yet (no token, no routing). Sets up C2.

## Out of scope (C2+)
Multi-bot Slack routing by `api_app_id`, per-app signing-secret HMAC, Callie's marketing surface scope +
agent-aware DM scope, her domain tools, escalation/delegation. See `docs/callie-build-plan.md`.
