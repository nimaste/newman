// Tiny fetch wrapper for the Newman hub API (see API.md).
//
// * JSON in, JSON out.
// * Failures throw ApiError and — unless `quiet` — raise a toast.
// * Network failures / 502-504 flag the hub as unreachable so the app can show
//   a banner; the next successful call clears it.
// * 401 {signIn: true} means the session is gone: it emits {type: 'auth'} (no toast) and
//   the app shows the sign-in page.

const listeners = new Set();

export function onApiEvent(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit(event) {
  listeners.forEach((fn) => {
    try {
      fn(event);
    } catch {
      /* a broken listener must not break requests */
    }
  });
}

/** Show a toast from anywhere. kind: 'info' | 'success' | 'error'. */
export function toast(message, kind = 'info') {
  emit({ type: 'toast', message, kind });
}

// The wall tablet signs in as a "tablet" session: the hub sets a cookie, and the token it also returns
// is kept as a fallback (Authorization: Bearer). Only the wall uses it — a bearer token wins over the
// cookie, so the desktop dashboard must never send it.
const TABLET_TOKEN_KEY = 'newman.tablet.token';
let useTabletToken = false;
export function enableTabletToken(on) {
  useTabletToken = !!on;
}
export function tabletToken() {
  if (!useTabletToken) return null;
  try {
    return window.localStorage.getItem(TABLET_TOKEN_KEY);
  } catch {
    return null;
  }
}
export function setTabletToken(token) {
  try {
    if (token) window.localStorage.setItem(TABLET_TOKEN_KEY, token);
    else window.localStorage.removeItem(TABLET_TOKEN_KEY);
  } catch {
    /* storage blocked: the cookie still works */
  }
}

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

// Relative URLs: the dashboard is served by the hub itself (and proxied by
// Vite in development), so "api/…" resolves against the page's own origin.
function url(path) {
  return path.replace(/^\/+/, '');
}

export async function request(path, { method = 'GET', body, quiet = false } = {}) {
  let res;
  try {
    res = await fetch(url(path), {
      method,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(tabletToken() ? { Authorization: 'Bearer ' + tabletToken() } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    emit({ type: 'hub', up: false });
    const err = new ApiError("Can't reach the Newman hub.", 0, null);
    if (!quiet) toast(err.message, 'error');
    throw err;
  }

  emit({ type: 'hub', up: ![502, 503, 504].includes(res.status) });

  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!res.ok) {
    // The session is gone (expired, signed out elsewhere): show the sign-in page, quietly.
    if (res.status === 401 && data && typeof data === 'object' && data.signIn) {
      emit({ type: 'auth', signedIn: false });
      throw new ApiError(data.error || 'Please sign in', 401, data);
    }
    const msg =
      (data && typeof data === 'object' && (data.error || data.message)) ||
      (res.status === 404 ? 'Not found.' : `${method} ${path} failed (${res.status}).`);
    const err = new ApiError(msg, res.status, data);
    if (!quiet) toast(msg, 'error');
    throw err;
  }
  return data;
}

export const api = {
  get: (path, opts) => request(path, { ...opts, method: 'GET' }),
  post: (path, body, opts) => request(path, { ...opts, method: 'POST', body: body === undefined ? {} : body }),
  put: (path, body, opts) => request(path, { ...opts, method: 'PUT', body }),
  patch: (path, body, opts) => request(path, { ...opts, method: 'PATCH', body }),
  del: (path, opts) => request(path, { ...opts, method: 'DELETE' }),
};

export const enc = encodeURIComponent;
