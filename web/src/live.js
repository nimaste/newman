// Live updates from the hub over one shared websocket (GET api/ws):
//   {type:"hello", time, version, webBuild} · {type:"entity", entity} · {type:"event", entityId, event}
//   · {type:"activity", entry} · {type:"person", person} (someone you may see moved) · {type:"pong"}
// Reconnects with backoff (1 s → 30 s). After a reconnect it emits {type:"reconnected"}
// so stores can refetch once to catch up.

import { useEffect, useRef, useState } from 'react';
import { tabletToken } from './api.js';
import { noteHubBuild } from './build.js';

const listeners = new Set();
const statusListeners = new Set();
let ws = null;
let started = false;
let backoff = 1000;
let retryTimer = null;
let pingTimer = null;
let everOpen = false;
let status = { state: 'connecting', since: Date.now() };
// The last "hello" (it carries the hub's clock): replayed to listeners that subscribe later.
let lastHello = null;

/** ws:// or wss:// URL for a hub path relative to the page ("api/ws", "api/entities/x/live"). */
export function wsUrl(path) {
  const u = new URL(path, window.location.href);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  u.hash = '';
  // The wall tablet's fallback when its cookie is missing (the hub accepts ?token= on websockets).
  const tok = tabletToken();
  if (tok) u.searchParams.set('token', tok);
  return u.toString();
}

function setStatus(state) {
  if (status.state === state) return;
  status = { state, since: Date.now() };
  statusListeners.forEach((fn) => fn(status));
}

function emit(msg) {
  listeners.forEach((fn) => {
    try {
      fn(msg);
    } catch (e) {
      console.warn('live listener failed', e); // eslint-disable-line no-console
    }
  });
}

function schedule() {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connect, backoff);
  backoff = Math.min(30000, backoff * 2);
}

function connect() {
  clearTimeout(retryTimer);
  if (!started) return;
  let sock;
  try {
    sock = new WebSocket(wsUrl('api/ws'));
  } catch {
    setStatus('down');
    schedule();
    return;
  }
  ws = sock;
  sock.onopen = () => {
    backoff = 1000;
    setStatus('open');
    if (everOpen) emit({ type: 'reconnected' });
    everOpen = true;
    clearInterval(pingTimer);
    pingTimer = setInterval(() => sock.readyState === 1 && sock.send('ping'), 25000);
  };
  sock.onmessage = (ev) => {
    if (typeof ev.data !== 'string') return;
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (msg && msg.type === 'hello') {
      lastHello = msg;
      noteHubBuild(msg.webBuild); // a newer web build: the page reloads itself when it's a good moment (build.js)
    }
    if (msg && msg.type) emit(msg);
  };
  sock.onclose = () => {
    clearInterval(pingTimer);
    if (ws === sock) ws = null;
    setStatus('down');
    schedule();
  };
  sock.onerror = () => {
    try {
      sock.close();
    } catch {
      /* closing anyway */
    }
  };
}

function start() {
  if (started || typeof WebSocket === 'undefined') return;
  started = true;
  connect();
}

/** Signed out: close the socket and stop retrying (the hub would refuse it anyway). */
export function stopLive() {
  started = false;
  lastHello = null;
  everOpen = false;
  clearTimeout(retryTimer);
  clearInterval(pingTimer);
  const s = ws;
  ws = null;
  if (s) {
    s.onclose = null;
    try {
      s.close();
    } catch {
      /* already closed */
    }
  }
  backoff = 1000;
  setStatus('connecting');
}

/** Call `fn(msg)` for every live message while mounted. */
export function useLive(fn) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    start();
    const l = (m) => ref.current(m);
    listeners.add(l);
    if (lastHello) l(lastHello);
    return () => listeners.delete(l);
  }, []);
}

/** { state: 'connecting' | 'open' | 'down', since } of the live connection. */
export function useLiveStatus() {
  const [s, setS] = useState(status);
  useEffect(() => {
    start();
    statusListeners.add(setS);
    setS(status);
    return () => statusListeners.delete(setS);
  }, []);
  return s;
}

/** Call `fn()` (debounced) whenever a live message matches `test`; also after a reconnect. */
export function useLiveRefresh(test, fn, delay = 300) {
  const timer = useRef(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => () => clearTimeout(timer.current), []);
  useLive((m) => {
    if (m.type === 'reconnected' || test(m)) {
      clearTimeout(timer.current);
      timer.current = setTimeout(() => fnRef.current(), delay);
    }
  });
}
