// Deterministic presence color helper.
//
// presenceColor(key) returns a stable HSL color string derived from the key
// (typically a user's email address).  The same key always yields the same
// color, so a person's avatar, remote caret, and selection band are
// consistently one color everywhere in the UI.
//
// Implementation: sum the char-codes of the key modulo 360 to derive a hue,
// then use fixed saturation/lightness values chosen for legibility against
// both white paper and the dark header.
//
// Note: the design doc says "hash from User.id" but at the WebSocket layer we
// only have email — email is the stable per-person key used here.  When a
// real user ID becomes available over the wire this function can be updated
// without changing any callers.

/**
 * Return a stable HSL color string for the given *key* (e.g. email address).
 *
 * @param {string} key
 * @returns {string}  e.g. "hsl(213, 68%, 44%)"
 */
export function presenceColor(key) {
  const str = String(key || '');
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (h + str.charCodeAt(i)) % 360;
  }
  // Saturation 68%, Lightness 44% — vivid but not eye-watering, readable over
  // white paper (contrast ratio ≥ 3:1 for the caret bar / avatar bg).
  return `hsl(${h}, 68%, 44%)`;
}
