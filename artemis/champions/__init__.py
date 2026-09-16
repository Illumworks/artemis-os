"""Champions community digest — ingest, classification and storage.

Phase 1 deliberately delivers nothing. It pulls the Amira Champions community
from Vanilla, classifies each item, and stores rows. Email and Slack delivery
are built but not wired to this, so the whole thing can run alongside Hannah's
existing setup for a week and be diffed against it. That comparison is the
acceptance test and it costs nothing.
"""
