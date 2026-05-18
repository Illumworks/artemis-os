// Design discipline: fluidity, simplicity, purposefulness, naturalness, spacious, open.
// Surfaces should feel persistent — never blank, never make the operator wait for content
// that was already known a moment ago.
//
// SWR (stale-while-revalidate) cache backed by sessionStorage.
// Scoped to one tab/session — stale data doesn't leak across browser restarts.
//
// API:
//   await swr(key, fetcher, { maxAgeMs }) → { data, isStale, source }
//     - returns the cached value immediately if present
//     - fires fetcher() in the background; updates cache when it resolves
//     - emits a 'swr:fresh' CustomEvent on document with detail={key, data}
//       so the caller can re-render with fresh data
//
//   swr.invalidate(key) → drops the cached entry (used on mutations)

const DEFAULT_MAX_AGE_MS = 5 * 60 * 1000; // 5 minutes

// In-memory fallback if sessionStorage is unavailable or quota-exceeded
const _memoryFallback = new Map();

function _read(key) {
  try {
    const raw = sessionStorage.getItem(`swr:${key}`);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return _memoryFallback.get(key) ?? null;
  }
}

function _write(key, entry) {
  try {
    sessionStorage.setItem(`swr:${key}`, JSON.stringify(entry));
    _memoryFallback.delete(key); // keep consistent: prefer sessionStorage
  } catch {
    // sessionStorage full or unavailable — fall back silently
    _memoryFallback.set(key, entry);
  }
}

function _delete(key) {
  try {
    sessionStorage.removeItem(`swr:${key}`);
  } catch {
    // ignore
  }
  _memoryFallback.delete(key);
}

// Track in-flight revalidation promises to avoid duplicate fetches
const _inflight = new Map();

/**
 * Stale-while-revalidate fetch.
 *
 * @param {string} key        Cache key (should be unique per resource)
 * @param {() => Promise<*>} fetcher  Async function that returns fresh data
 * @param {{ maxAgeMs?: number }} [opts]
 * @returns {Promise<{ data: *, isStale: boolean, source: 'cache'|'fresh' }>}
 */
export async function swr(key, fetcher, { maxAgeMs = DEFAULT_MAX_AGE_MS } = {}) {
  const cached = _read(key);
  const now = Date.now();

  if (cached) {
    const age = now - cached.fetchedAt;
    const isStale = age > cached.ttlMs;

    // Always kick off a background revalidation if stale (or if the TTL is up)
    if (isStale && !_inflight.has(key)) {
      _revalidate(key, fetcher, maxAgeMs);
    }

    return { data: cached.data, isStale, source: 'cache' };
  }

  // No cache — must fetch now (awaited)
  if (_inflight.has(key)) {
    // Another caller already waiting — join their promise
    const data = await _inflight.get(key);
    return { data, isStale: false, source: 'fresh' };
  }

  return _fetchAndStore(key, fetcher, maxAgeMs);
}

/**
 * Drop a cached entry so the next swr() call fetches fresh.
 * Call this immediately after a mutation (create/update/delete).
 *
 * @param {string} key
 */
swr.invalidate = function invalidate(key) {
  _delete(key);
  _inflight.delete(key);
};

/**
 * Peek at the cached value without triggering a fetch or revalidation.
 * Returns the data if present, otherwise null.
 *
 * @param {string} key
 * @returns {* | null}
 */
swr.peek = function peek(key) {
  const cached = _read(key);
  if (!cached) return null;
  return cached.data ?? null;
};

/**
 * Invalidate all cached entries whose key starts with the given prefix.
 * Useful when you don't know the exact key (e.g. range-scoped calendar events).
 *
 * @param {string} prefix
 */
swr.invalidatePrefix = function invalidatePrefix(prefix) {
  // Clear in-memory fallback
  for (const k of _memoryFallback.keys()) {
    if (k.startsWith(prefix)) _memoryFallback.delete(k);
  }
  // Clear sessionStorage
  try {
    const ssPrefix = `swr:${prefix}`;
    const toRemove = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k && k.startsWith(ssPrefix)) toRemove.push(k);
    }
    toRemove.forEach((k) => sessionStorage.removeItem(k));
  } catch {
    // ignore
  }
};

// ── Internal helpers ─────────────────────────────────────────────────────────

async function _fetchAndStore(key, fetcher, maxAgeMs) {
  const promise = fetcher();
  _inflight.set(key, promise);

  try {
    const data = await promise;
    _write(key, { data, fetchedAt: Date.now(), ttlMs: maxAgeMs });
    return { data, isStale: false, source: 'fresh' };
  } finally {
    _inflight.delete(key);
  }
}

function _revalidate(key, fetcher, maxAgeMs) {
  const promise = fetcher()
    .then((data) => {
      _write(key, { data, fetchedAt: Date.now(), ttlMs: maxAgeMs });
      if (typeof document !== 'undefined') {
        document.dispatchEvent(new CustomEvent('swr:fresh', { detail: { key, data } }));
      }
      return data;
    })
    .catch((err) => {
      console.warn(`[swr] Background revalidation failed for "${key}":`, err);
    })
    .finally(() => {
      _inflight.delete(key);
    });

  _inflight.set(key, promise);
}
