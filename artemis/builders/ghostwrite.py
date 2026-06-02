"""Ghostwrite system-prompt assembly for agents with persona.ghostwrite == True.

When an agent has persona.ghostwrite = true, this module prepends a ghostwrite
directive + voice samples from the floating-artemis personality profile to the
agent's system prompt at run-time.

Usage:
    from artemis.builders.ghostwrite import apply_ghostwrite_frame

    effective_prompt = apply_ghostwrite_frame(
        system_prompt=agent.system_prompt or "",
        persona=agent.persona or {},
        session_id=run_id,
    )
"""

from __future__ import annotations

from artemis.floating_artemis.personality import select_voice_samples

_GHOSTWRITE_DIRECTIVE = """\
GHOSTWRITE DIRECTIVE — read this before all other instructions:
Your output is framed as if Jon wrote it. Do not refer to yourself as the agent
or use the agent's name. Do not say "I am {name}" or "As {name}". Match Jon's
voice precisely. Do not add greetings, sign-offs, or attribution lines.
""".strip()

_VOICE_SAMPLES_HEADER = "\nJon's voice — characteristic phrases (match this register):"


def _build_ghostwrite_block(persona: dict[str, object], session_id: str) -> str:
    """Return the ghostwrite preamble including voice samples.

    Args:
        persona: the agent's persona JSONB dict.
        session_id: the current run_id; used to seed deterministic sample selection.
    """
    agent_name = str(persona.get("name", "the agent"))
    directive = _GHOSTWRITE_DIRECTIVE.replace("{name}", agent_name)

    voice_notes = persona.get("voice_notes")
    if voice_notes:
        directive += f"\n\nVoice notes from Jon: {voice_notes!s}"

    samples = select_voice_samples(session_id, k=4)
    if samples:
        sample_lines = "\n".join(f'  - "{s}"' for s in samples)
        directive += f"{_VOICE_SAMPLES_HEADER}\n{sample_lines}"

    return directive


def apply_ghostwrite_frame(
    system_prompt: str,
    persona: dict[str, object],
    session_id: str,
) -> str:
    """Return the effective system prompt with ghostwrite directive prepended.

    If persona.ghostwrite is not True, returns system_prompt unchanged.

    Args:
        system_prompt: the agent's stored system prompt (may be empty string).
        persona: the agent's persona JSONB dict (may be empty dict).
        session_id: current run_id; seeds deterministic voice-sample selection.

    Returns:
        The effective system prompt string.
    """
    if not persona.get("ghostwrite", False):
        return system_prompt

    ghostwrite_block = _build_ghostwrite_block(persona, session_id)

    if system_prompt:
        return f"{ghostwrite_block}\n\n---\n\n{system_prompt}"
    return ghostwrite_block
