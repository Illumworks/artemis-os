// Bootstraps the surface-availability flags. Called once on app boot.
let _status = null;

export async function loadStatus() {
  if (_status) return _status;
  const res = await fetch("/api/_status");
  _status = await res.json();
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
