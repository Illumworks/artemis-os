# Service — Memory Layer

Tracks per-`(district_id, reason_code)` the last-seen signal and embedding hash. Used by scouts to dedupe and decide on material-change emissions.

**Backing table:** `memory_layer`
**Module path:** `artemis_os/services/memory_layer.py`

## Interface

```python
from typing import Optional
from datetime import datetime

@dataclass
class MemoryEntry:
    district_id: str
    reason_code: str
    last_seen_at: datetime
    last_signal_id: Optional[str]
    embedding_hash: Optional[str]
    last_material_change_at: Optional[datetime]


def get(district_id: str, reason_code: str) -> Optional[MemoryEntry]:
    """
    Return the memory entry for a (district, reason_code) pair.
    None if no prior signal.
    """

def upsert(
    district_id: str,
    reason_code: str,
    signal_id: str,
    embedding_hash: str,
    is_material_change: bool = False,
) -> None:
    """
    Update or insert. Called by scouts after they emit (or suppress) a signal.

    - On emit: updates last_seen_at, last_signal_id, embedding_hash. Optionally last_material_change_at.
    - On suppress: only updates last_seen_at (so dedupe window stays active).
    """

def compute_similarity(snippet_a: str, snippet_b: str) -> float:
    """
    Returns cosine similarity 0–1 between two text snippets.
    Uses OpenAI text-embedding-3-small. Cached by snippet hash.
    """

def material_change_check(prior_signal_id: str, new_snippet: str) -> tuple[bool, str]:
    """
    LLM-based fallback when embedding similarity is in the gray zone (0.70–0.92).
    Returns (is_material_change, reasoning).

    Used by scouts ONLY when similarity is in the gray zone — never for cheap suppress
    or cheap emit decisions.
    """
```

## Embedding strategy

- Model: `text-embedding-3-small` (OpenAI). 1536 dimensions. Cheap.
- Cache by SHA256 of input snippet. Cache TTL: 30 days.
- Snippet is truncated to first 500 characters before embedding (consistency + cost).

## Material-change check prompt

```
You are deciding whether a new signal is genuinely new information vs. a near-duplicate
of a prior signal for the same district + reason code.

PRIOR SIGNAL EVIDENCE:
{{prior_signal.source.verbatim_snippet}}

NEW SIGNAL EVIDENCE:
{{new_snippet}}

DECISION RULES:
- If both describe the same underlying event (same RFP, same board vote, same press release)
  with minor rewording, return is_material_change=false.
- If the new evidence reveals a NEW stage (e.g., bill moved from introduced to passed chamber),
  return is_material_change=true.
- If the new evidence is from a different source covering the same event, return is_material_change=false.
- When in doubt, prefer false (suppress) to reduce noise.

Return JSON: { "is_material_change": true|false, "reasoning": "1 sentence" }
```

## Failure modes

- Embedding API unreachable → scout falls back to material-change LLM check for all gray-zone cases (slower, more expensive, but functional)
- Material-change LLM call fails → scout suppresses the new signal (conservative default) and logs a warning
- Memory layer DB unreachable → scout cannot dedupe; emits the signal and logs critical error (over-emission is recoverable; under-emission is not)
