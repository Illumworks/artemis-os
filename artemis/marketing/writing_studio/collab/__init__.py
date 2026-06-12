"""Real-time collaborative editing plumbing for Writing Studio.

Phase 0: identity-aware per-draft WebSocket with presence broadcasting.
Phase 1 will add roster display, remote cursors, and selection highlights.
"""

from __future__ import annotations

from artemis.marketing.writing_studio.collab.routes import router

__all__ = ["router"]
