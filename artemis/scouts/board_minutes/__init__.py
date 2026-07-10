"""Board Minutes Scouts — school district board meeting minutes and agendas.

Two scouts share this package:

- ``scout.BoardMinutesScout`` (v1) — literacy/pre-RFP-intent signals from
  Amira's priority districts (BoardDocs, Granicus, district sites).
  discovered_by="board_minutes_scout".
- ``peer_scout.BoardPeerValidationScout`` (v2) — NON-customer districts
  discussing Amira / screentime / AI-in-schools.  Fetches agenda-item BODY
  text, classifies mention sentiment with an LLM (``classifier``), applies a
  pluggable customer-exclusion filter (``customers``), and emits
  peer-validation signals.  discovered_by="board_peer_validation_scout".
"""
