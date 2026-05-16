# public/ — Verbatim Node Port

This directory is a verbatim copy of the Node app's `public/` directory.

**Source of truth (frozen reference):**
`/Users/artemis/Desktop/Artemis/claudeck-artemis/public/`

## Status

The JS in this directory calls Node API endpoints (e.g. `/api/sessions`, `/api/agents`, `/api/costs`) that do not yet exist in the Python app. The UI shell will load visually, but most interactions will fail with network errors until slice **E1b — API client rewire** is complete.

Do not edit any files in `js/` or `css/` in this directory; those changes belong in E1b or later slices, and must be coordinated with the Node reference to avoid divergence.

## What comes next

- **E1b**: Rewrite `public/js/core/api.js` to point at Python endpoints.
- **E2+**: Implement the Python equivalents of the Node route modules so the API calls return real data.
