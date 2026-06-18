/**
 * Pure helper functions for jira-card-drawer.js comment threading and @mention rendering.
 * Extracted into a standalone module so they can be unit-tested in Node without browser globals.
 */

// Build a regex that matches @mentions bounded to known display names (longest-first),
// falling back to a single-word token for unknown @names.
// knownNames: string[] of display names (e.g. ["Jon Fila", "Angel Blandero"]).
export function _buildMentionRegex(knownNames) {
  // Sort longest-first so multi-word names are tried before single-word prefixes.
  const sorted = [...knownNames].sort((a, b) => b.length - a.length);
  // Escape each name for use inside a regex.
  const escapedNames = sorted.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  // Build the known-name alternation; fall back to a single \S+ token for unknowns.
  const knownAlt = escapedNames.length > 0 ? escapedNames.join('|') : null;
  const mentionPart = knownAlt
    ? `@@?(?:${knownAlt}|[A-Za-z]\\S*)`
    : `@@?[A-Za-z]\\S*`;
  return new RegExp(`(https?:\\/\\/[^\\s<>"&]+)|(${mentionPart})`, 'g');
}

// Infer the set of "known author names" from a flat comment list.
// Returns a string[] of unique, non-empty author names.
export function _extractKnownNames(comments) {
  const seen = new Set();
  for (const c of (comments || [])) {
    const n = (c.author || '').trim();
    if (n) seen.add(n);
  }
  return [...seen];
}

// Given a flat array of comment objects (each with .author, .body/.text, .created),
// and the set of known author names, build a threaded tree:
//   [ { comment, replies: [ comment, ... ] }, ... ]
// A comment is treated as a reply to another author's most-recent PRIOR comment
// when its body starts with "@Name " (exactly that known name, case-insensitive,
// followed by a space or end of string).
// Comments with no resolvable parent render top-level with an empty replies array.
// Chronological order is preserved within each group.
export function _buildCommentThread(comments, knownNames) {
  if (!comments || comments.length === 0) return [];

  // Sort longest-name-first so "Jon Fila Smith" is tried before "Jon Fila".
  const namesSorted = [...knownNames].sort((a, b) => b.length - a.length);

  const threads = []; // array of { comment, replies: [] }
  // Map: authorName (lowercased) → index in `threads` of their most-recent top-level comment
  const lastTopLevelByAuthor = new Map();

  for (const c of comments) {
    const body = (c.body || c.text || '').trim();

    // Try to detect a leading @mention of a known author.
    let parentAuthor = null;
    for (const name of namesSorted) {
      // Match @Name followed by space/punctuation/end-of-string (case-insensitive).
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp(`^@@?${escaped}(?:\\s|$)`, 'i');
      if (re.test(body)) {
        parentAuthor = name;
        break;
      }
    }

    if (parentAuthor) {
      const key = parentAuthor.toLowerCase();
      const parentIdx = lastTopLevelByAuthor.get(key);
      if (parentIdx !== undefined) {
        // Attach as a reply to that parent thread slot.
        threads[parentIdx].replies.push(c);
        continue;
      }
      // No prior top-level comment by that author → fall through to top-level.
    }

    // Top-level comment.
    const authorKey = (c.author || '').toLowerCase();
    lastTopLevelByAuthor.set(authorKey, threads.length);
    threads.push({ comment: c, replies: [] });
  }

  return threads;
}
