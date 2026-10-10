import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { useLive } from './live.js';

/**
 * Fetch `path` (GET) and keep the result. With `interval` the request repeats
 * quietly (no toasts) every N ms while the component is mounted.
 * Pass path = null to skip.
 */
export function useApi(path, { interval = 0 } = {}) {
  const [state, setState] = useState({ data: undefined, error: null, loading: !!path });
  const alive = useRef(true);
  const seq = useRef(0);

  const load = useCallback(
    async (quiet) => {
      if (!path) return undefined;
      const mine = ++seq.current;
      try {
        const data = await api.get(path, { quiet });
        if (alive.current && mine === seq.current) setState({ data, error: null, loading: false });
        return data;
      } catch (error) {
        if (alive.current && mine === seq.current) setState((s) => ({ data: s.data, error, loading: false }));
        return undefined;
      }
    },
    [path],
  );

  useEffect(() => {
    alive.current = true;
    if (!path) {
      setState({ data: undefined, error: null, loading: false });
      return undefined;
    }
    setState((s) => ({ ...s, loading: true }));
    load(true);
    let timer;
    if (interval) timer = setInterval(() => load(true), interval);
    return () => {
      alive.current = false;
      if (timer) clearInterval(timer);
    };
  }, [path, interval, load]);

  const setData = useCallback((fnOrValue) => {
    setState((s) => ({ ...s, data: typeof fnOrValue === 'function' ? fnOrValue(s.data) : fnOrValue }));
  }, []);

  return { ...state, reload: () => load(true), setData };
}

/**
 * Recent activity (api/events?limit=N) kept current by the live socket: new entries are
 * prepended as they arrive, and the list is refetched once after a reconnect. No polling.
 */
export function useActivityFeed(limit, { enabled = true } = {}) {
  const res = useApi(enabled ? 'api/events?limit=' + limit : null);
  const { setData, reload } = res;
  useLive((m) => {
    if (!enabled) return;
    if (m.type === 'reconnected') reload();
    else if (m.type === 'activity' && m.entry) {
      setData((list) => {
        if (!Array.isArray(list)) return list;
        if (list.some((x) => x.id === m.entry.id)) return list;
        return [m.entry, ...list].slice(0, Math.max(limit, list.length));
      });
    }
  });
  return res;
}

/** Current hash route: { parts: ['automations', 'auto_1'], query: URLSearchParams } */
function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const [pathPart, query = ''] = raw.split('?');
  return {
    parts: pathPart.split('/').filter(Boolean).map((p) => decodeURIComponent(p)),
    query: new URLSearchParams(query),
  };
}

export function useRoute() {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  return route;
}

// Where the full-screen live view (#/cameras/<id>) should go back to: the last page before it.
let currentHash = typeof window !== 'undefined' ? window.location.hash : '';
let returnHash = null;
const isLiveHash = (h) => /^#\/cameras\/[^/?]+/.test(h || '');
// The Devices list as last shown, with its filters (they live in its URL): a device page's "Devices /"
// goes back to it.
const isDevicesList = (h) => /^#\/devices\/?(\?|$)/.test(h || '');
let devicesList = isDevicesList(currentHash) ? currentHash : '#/devices';
if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', () => {
    if (!isLiveHash(currentHash)) returnHash = currentHash;
    currentHash = window.location.hash;
    if (isDevicesList(currentHash)) devicesList = currentHash;
  });
}
export function devicesListHref() {
  return devicesList;
}
export function liveReturnPath() {
  return returnHash && !isLiveHash(returnHash) ? returnHash.replace(/^#\/?/, '') : null;
}

export function navigate(path, { replace = false } = {}) {
  const hash = '#/' + path.replace(/^\/+/, '');
  if (replace) {
    window.history.replaceState(null, '', hash);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else {
    window.location.hash = hash;
  }
}

/** Shared hub data: meta, status, entities (polled) and lookup maps. */
export const HubContext = createContext(null);
export function useHub() {
  return useContext(HubContext);
}

export function useDebounced(value, ms) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
