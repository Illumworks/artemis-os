"""Granola meeting-notes integration.

Provides two auth paths:
  1. Local-state (friendly default for Mac users): reads WorkOS tokens from
     ~/Library/Application Support/Granola/supabase.json — no OAuth dance.
  2. OAuth (fallback): standard PKCE flow via mcp-auth.granola.ai.

Exposes a GranolaClient wrapping the MCP StreamableHTTP API at mcp.granola.ai.
"""
