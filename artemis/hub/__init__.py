"""Artemis hub — connective tissue for the multi-agent team.

Phase 1: escalation layer.
- Records pending asks when an agent @-mentions Jon or asks him a question.
- Marks asks resolved when Jon replies.
- Escalation sweep (hourly) fires after ~1 day unresolved: Artemis posts a
  terminal connective comment in-channel + notifies Jon via her DM.
- Sole-interrupt path: only Artemis uses notify_jon; other agents fold their
  FYIs into the morning brief.
"""
