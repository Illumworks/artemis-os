// Bootstraps the surface-availability flags. Called once on app boot.
let _status = null;

export async function loadStatus() {
  if (_status) return _status;
  try {
    const res = await fetch("/api/_status");
    if (!res.ok) {
      console.warn(`loadStatus: /api/_status returned ${res.status}`);
      return null;
    }
    _status = await res.json();
  } catch (err) {
    console.warn("loadStatus: failed to load surface availability flags", err);
    return null;
  }
  return _status;
}

export function isSurfaceAvailable(name) {
  if (!_status) {
    console.warn(`isSurfaceAvailable("${name}") called before loadStatus()`);
    return false;
  }
  return _status.available_surfaces.includes(name);
}

export function getStatus() {
  return _status;
}
