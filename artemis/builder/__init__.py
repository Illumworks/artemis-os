"""artemis.builder — Agent-Builder + Self-Improvement (O1).

Package layout:
  engine.py               — Builder-Engine primitives (read/propose/commit)
  agent_builder.py        — Agent-Builder system prompt + tool list + conversation handler
  trajectory_summarizer.py — per-run trajectory summary writer (async, background)
  routes.py               — HTTP routes for /api/builder/*
  schemas.py              — Pydantic schemas for builder API types
  repository.py           — DB helpers for builder_sessions, definition_proposals,
                            agent_run_trajectory_summaries
"""
