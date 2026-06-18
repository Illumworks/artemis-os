/**
 * Unit tests for the pure helper functions in jira-card-drawer.js.
 * Tests cover:
 *   - _buildMentionRegex  (Fix 2: bounded @mention pill)
 *   - _buildCommentThread (Fix 1: client-side reply-threading)
 *   - _extractKnownNames  (helper used by threading)
 *
 * Run with:  node --test public/js/components/jira-card-drawer.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  _buildMentionRegex,
  _buildCommentThread,
  _extractKnownNames,
} from './jira-comment-helpers.js';

// ── _buildMentionRegex ──────────────────────────────────────────────────────

test('_buildMentionRegex: matches single-word @mention against known name', () => {
  const re = _buildMentionRegex(['Jon Fila']);
  const m = '@Jon Fila this is a message'.match(re);
  assert.ok(m, 'should match');
  assert.equal(m[0], '@Jon Fila');
});

test('_buildMentionRegex: does NOT swallow the word after a multi-word name', () => {
  const re = _buildMentionRegex(['Jon Fila']);
  const input = '@Jon Fila this';
  const matches = [...input.matchAll(re)];
  assert.equal(matches.length, 1);
  // The match must be exactly "@Jon Fila" — "this" must NOT be included.
  assert.equal(matches[0][0], '@Jon Fila');
});

test('_buildMentionRegex: matches longest name first (Angel Blandero, not Angel)', () => {
  const re = _buildMentionRegex(['Angel', 'Angel Blandero']);
  const matches = [...'@Angel Blandero can you check'.matchAll(re)];
  assert.equal(matches.length, 1);
  assert.equal(matches[0][0], '@Angel Blandero');
});

test('_buildMentionRegex: falls back to single-token for unknown @name', () => {
  const re = _buildMentionRegex(['Jon Fila']);
  // @Unknown is not a known name — should match single token @Unknown
  const matches = [...'@Unknown here'.matchAll(re)];
  assert.equal(matches.length, 1);
  assert.equal(matches[0][0], '@Unknown');
});

test('_buildMentionRegex: does not include the trailing word for @Kristen Spiker Yes pattern', () => {
  const re = _buildMentionRegex(['Kristen Spiker']);
  const matches = [...'@Kristen Spiker Yes, agreed'.matchAll(re)];
  assert.equal(matches.length, 1);
  assert.equal(matches[0][0], '@Kristen Spiker');
});

test('_buildMentionRegex: still matches URLs correctly alongside mentions', () => {
  const re = _buildMentionRegex(['Jon Fila']);
  const input = 'see https://example.com/foo and @Jon Fila';
  const matches = [...input.matchAll(re)];
  assert.equal(matches.length, 2);
  assert.equal(matches[0][0], 'https://example.com/foo');
  assert.equal(matches[1][0], '@Jon Fila');
});

test('_buildMentionRegex: no known names — falls back to single-token matching', () => {
  const re = _buildMentionRegex([]);
  const matches = [...'@hello world'.matchAll(re)];
  assert.equal(matches.length, 1);
  assert.equal(matches[0][0], '@hello');
});

// ── _extractKnownNames ──────────────────────────────────────────────────────

test('_extractKnownNames: returns unique, non-empty author names', () => {
  const comments = [
    { author: 'Jon Fila',  body: 'hello' },
    { author: 'Angel Blandero', body: 'world' },
    { author: 'Jon Fila',  body: 'again' },   // duplicate
    { author: '',          body: 'anon' },    // empty → skip
  ];
  const names = _extractKnownNames(comments);
  assert.deepEqual(names.sort(), ['Angel Blandero', 'Jon Fila']);
});

// ── _buildCommentThread ─────────────────────────────────────────────────────

test('_buildCommentThread: all top-level when no @prefix replies', () => {
  const comments = [
    { author: 'Jon Fila',  body: 'First comment', created: '2024-01-01T10:00:00Z' },
    { author: 'Angel Blandero', body: 'Second comment', created: '2024-01-01T10:01:00Z' },
  ];
  const threads = _buildCommentThread(comments, ['Jon Fila', 'Angel Blandero']);
  assert.equal(threads.length, 2);
  assert.equal(threads[0].replies.length, 0);
  assert.equal(threads[1].replies.length, 0);
});

test('_buildCommentThread: reply prefixed with @Author is nested under parent', () => {
  const comments = [
    { author: 'Jon Fila',  body: 'First comment', created: '2024-01-01T10:00:00Z' },
    { author: 'Angel Blandero', body: '@Jon Fila great idea!', created: '2024-01-01T10:01:00Z' },
  ];
  const threads = _buildCommentThread(comments, ['Jon Fila', 'Angel Blandero']);
  assert.equal(threads.length, 1);
  assert.equal(threads[0].comment.author, 'Jon Fila');
  assert.equal(threads[0].replies.length, 1);
  assert.equal(threads[0].replies[0].author, 'Angel Blandero');
});

test('_buildCommentThread: reply with @@mention (double-at) still nests', () => {
  const comments = [
    { author: 'Jon Fila', body: 'Hello', created: '2024-01-01T10:00:00Z' },
    { author: 'Kristen Spiker', body: '@@Jon Fila Yes, agreed', created: '2024-01-01T10:02:00Z' },
  ];
  const threads = _buildCommentThread(comments, ['Jon Fila', 'Kristen Spiker']);
  assert.equal(threads.length, 1);
  assert.equal(threads[0].replies.length, 1);
  assert.equal(threads[0].replies[0].author, 'Kristen Spiker');
});

test('_buildCommentThread: reply with no prior parent comment renders top-level', () => {
  const comments = [
    // Angel mentions Jon, but Jon has no prior comment
    { author: 'Angel Blandero', body: '@Jon Fila can you check?', created: '2024-01-01T10:00:00Z' },
    { author: 'Jon Fila', body: 'Sure', created: '2024-01-01T10:01:00Z' },
  ];
  const threads = _buildCommentThread(comments, ['Jon Fila', 'Angel Blandero']);
  assert.equal(threads.length, 2, 'both render top-level when no prior parent exists');
  assert.equal(threads[0].comment.author, 'Angel Blandero');
  assert.equal(threads[1].comment.author, 'Jon Fila');
});

test('_buildCommentThread: multiple replies nest under the correct parent', () => {
  const comments = [
    { author: 'Jon Fila',  body: 'First', created: '2024-01-01T10:00:00Z' },
    { author: 'Angel Blandero', body: 'Second', created: '2024-01-01T10:01:00Z' },
    { author: 'Kristen Spiker', body: '@Jon Fila yes!', created: '2024-01-01T10:02:00Z' },
    { author: 'Angel Blandero', body: '@Jon Fila agreed', created: '2024-01-01T10:03:00Z' },
  ];
  const threads = _buildCommentThread(comments, ['Jon Fila', 'Angel Blandero', 'Kristen Spiker']);
  // Jon + Angel are top-level; both Kristen and second Angel reply to Jon
  assert.equal(threads.length, 2);
  const jonThread = threads.find(t => t.comment.author === 'Jon Fila');
  assert.ok(jonThread);
  assert.equal(jonThread.replies.length, 2);
  assert.equal(jonThread.replies[0].author, 'Kristen Spiker');
  assert.equal(jonThread.replies[1].author, 'Angel Blandero');
});

test('_buildCommentThread: empty input returns empty array', () => {
  assert.deepEqual(_buildCommentThread([], []), []);
  assert.deepEqual(_buildCommentThread(null, []), []);
});

test('_buildCommentThread: preserves chronological order within replies', () => {
  const comments = [
    { author: 'Jon Fila',  body: 'Base', created: '2024-01-01T10:00:00Z' },
    { author: 'A', body: '@Jon Fila reply1', created: '2024-01-01T10:01:00Z' },
    { author: 'B', body: '@Jon Fila reply2', created: '2024-01-01T10:02:00Z' },
    { author: 'C', body: '@Jon Fila reply3', created: '2024-01-01T10:03:00Z' },
  ];
  const threads = _buildCommentThread(comments, ['Jon Fila', 'A', 'B', 'C']);
  assert.equal(threads.length, 1);
  const replies = threads[0].replies;
  assert.equal(replies[0].author, 'A');
  assert.equal(replies[1].author, 'B');
  assert.equal(replies[2].author, 'C');
});
