"""Forge-scoped context variables for Ares dev-project turns.

Provides the ``forge_project_path_var`` context variable used to opt a
claude-code adapter turn into Forge mode, where the CLI runs inside a real
project directory with read-only native tools (Read, Glob, Grep) instead of
the Artemis MCP server.

Usage (in an Ares turn handler):

    from artemis.dev_projects.context import forge_project_path_var

    token = forge_project_path_var.set("/path/to/project")
    try:
        response = await adapter.complete(request)
    finally:
        forge_project_path_var.reset(token)

The adapter reads this variable inside ``_complete_with_tools``; when it is
set, the Forge argv is built (``--add-dir``, ``--permission-mode
bypassPermissions``, read-only allowed-tools) and the subprocess cwd is set
to the project path.  When it is None, the existing MCP argv path is taken
unchanged.
"""

from __future__ import annotations

from contextvars import ContextVar

#: Set to the absolute path of the project directory before calling
#: ClaudeCodeAdapter.complete() in a Forge turn.  The adapter runs the
#: claude CLI with cwd=path and read-only native tools scoped to that dir.
forge_project_path_var: ContextVar[str | None] = ContextVar(
    "forge_project_path_var",
    default=None,
)
