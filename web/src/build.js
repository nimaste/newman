// Keeping every open page on the web build the hub serves. A page keeps running the script it loaded
// first - for days on a wall tablet - so after the hub is updated it compares builds and reloads itself.
//   The page's own build: the hashed script / style names it loaded (worked out at start, before any lazy
//   chunk adds more). The hub's: "webBuild" in the live socket's hello and in GET api/hello (api.py
//   web_build_id - the same names, read from its index.html).
//   Checked whenever the live socket (re)connects, when the page comes back into view and every 10 min.
//   The wall reloads once nobody is using it: not while Newman listens / speaks (or a timer or alert
//   sounds), not while music plays, not within a minute of a touch, not while a sheet or dialog that was
//   opened or touched in the last 10 minutes is up (cook mode's step reading is Newman speaking).
//   Other pages reload on the next page change, or on coming back into view after being hidden - never
//   while something is being typed or an editor has unsaved changes.
//   A reload that didn't bring the new build (the hub mid-update) waits 1 min, then 5, then 30 before
//   trying again (remembered across reloads in localStorage).
// No dependencies, so the rules (wallBlockers, webBlockers, retryAt) run under plain node (web/tests).

export const CHECK_EVERY_MS = 10 * 60 * 1000;
export const TOUCH_QUIET_MS = 60 * 1000;
export const DIALOG_QUIET_MS = 10 * 60 * 1000;
export const RETRY_DELAYS_MS = [60 * 1000, 5 * 60 * 1000, 30 * 60 * 1000];
const POLL_MS = 5000;
const RECORD_KEY = 'newman.webBuildReload';
const NOTES_KEY = 'newman.reloadNotes';

const ASSET = /(?:^|\/)assets\/([^/?#]+\.(?:js|css))(?:[?#]|$)/i;

/** The hashed script / style names in these URLs, sorted and space-joined; '' when none (the dev server). */
export function buildIdOf(urls) {
  const names = new Set();
  (urls || []).forEach((u) => {
    const m = ASSET.exec(String(u || ''));
    if (m) names.add(m[1]);
  });
  return [...names].sort().join(' ');
}

/** What this page loaded (index.html's own <script> / <link> tags; call it before lazy chunks load). */
export function ownBuildFrom(doc) {
  if (!doc || !doc.querySelectorAll) return '';
  const urls = [];
  doc.querySelectorAll('script[src], link[href]').forEach((el) => {
    const rel = (el.getAttribute('rel') || '').toLowerCase();
    if (el.tagName === 'LINK' && !/\b(stylesheet|modulepreload)\b/.test(rel)) return;
    urls.push(el.getAttribute(el.tagName === 'LINK' ? 'href' : 'src'));
  });
  return buildIdOf(urls);
}

/**
 * When a reload may happen again. `record` is the last reload made for a new build ({ to, count, at }):
 * after the 1st it waits 1 min, after the 2nd 5 min, after that 30 min each. 0 = now.
 */
export function retryAt(record, now) {
  if (!record || !record.count) return 0;
  const wait = RETRY_DELAYS_MS[Math.min(record.count, RETRY_DELAYS_MS.length) - 1];
  const at = (Number(record.at) || 0) + wait;
  return at > now + wait ? 0 : at; // a clock that jumped back doesn't hold it forever
}

/**
 * Why the wall can't reload right now ([] = it can). s: { voice (Newman listening / thinking / speaking,
 * a timer or alert sounding), playing (music), lastTouch, dialog (a sheet / dialog / cook mode is up),
 * dialogSince (when it appeared) }.
 */
export function wallBlockers(s, now) {
  const why = [];
  if (s.voice) why.push('voice');
  if (s.playing) why.push('music');
  if (now - (s.lastTouch || 0) < TOUCH_QUIET_MS) why.push('touched');
  if (s.dialog && now - Math.max(s.lastTouch || 0, s.dialogSince || 0) < DIALOG_QUIET_MS) why.push('dialog');
  return why;
}

/** Why another page can't reload right now. s: { dirty (an editor's unsaved changes), typing (a field has focus) }. */
export function webBlockers(s) {
  const why = [];
  if (s.dirty) why.push('unsaved');
  if (s.typing) why.push('typing');
  return why;
}

/* ---------------- the page's side ---------------- */

const PROD = !!(import.meta.env && import.meta.env.PROD);
const own = typeof document !== 'undefined' ? ownBuildFrom(document) : '';
let started = false;
let wall = false;
let hub = null;
let lastTouch = 0;
let dialogSince = 0;
let probe = () => ({});
let poll = null;
let lastWhy = '';
let announced = '';
const beforeReload = new Set();

const log = (...a) => console.info('[newman] web build:', ...a); // eslint-disable-line no-console

function store() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}
function readRecord() {
  try {
    return JSON.parse(store().getItem(RECORD_KEY) || 'null');
  } catch {
    return null;
  }
}
function writeRecord(r) {
  try {
    if (r) store().setItem(RECORD_KEY, JSON.stringify(r));
    else store().removeItem(RECORD_KEY);
  } catch {
    /* no storage: the 10-min check still spaces reloads out */
  }
}

/** The wall's own state for the rules: () => ({ voice, playing }). */
export function setIdleProbe(fn) {
  probe = typeof fn === 'function' ? fn : () => ({});
}

/** fn() runs just before the page reloads itself; what it returns is handed back by reloadNote(key) afterwards. */
export function onBeforeReload(key, fn) {
  const entry = { key, fn };
  beforeReload.add(entry);
  return () => beforeReload.delete(entry);
}

/** A value saved by onBeforeReload(key) for the page that reloaded itself (read once). */
export function reloadNote(key) {
  try {
    const notes = JSON.parse(window.sessionStorage.getItem(NOTES_KEY) || '{}');
    if (!(key in notes)) return undefined;
    const v = notes[key];
    delete notes[key];
    window.sessionStorage.setItem(NOTES_KEY, JSON.stringify(notes));
    return v;
  } catch {
    return undefined;
  }
}

function dialogUp() {
  return !!document.querySelector('[role="dialog"], [role="alertdialog"], [aria-modal="true"], [data-wall-in-use]');
}

function unsaved() {
  // The editors ask before the page goes while they have unsaved changes (a beforeunload handler).
  const ev = new Event('beforeunload', { cancelable: true });
  try {
    window.dispatchEvent(ev);
  } catch {
    return false;
  }
  return ev.defaultPrevented;
}

function typing() {
  const el = document.activeElement;
  if (!el || el === document.body) return false;
  return el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
}

function reload(why) {
  const rec = readRecord();
  writeRecord({ from: own, to: hub, count: ((rec && rec.count) || 0) + 1, at: Date.now() });
  const notes = {};
  beforeReload.forEach(({ key, fn }) => {
    try {
      const v = fn();
      if (v !== undefined) notes[key] = v;
    } catch {
      /* a note is a nicety */
    }
  });
  try {
    window.sessionStorage.setItem(NOTES_KEY, JSON.stringify(notes));
  } catch {
    /* no storage */
  }
  log(`reloading (${why}): ${own} -> ${hub}`);
  window.location.reload();
}

/** trigger: 'hello' | 'check' | 'poll' | 'route' | 'visible'. */
function consider(trigger) {
  if (!hub || hub === own) return;
  const now = Date.now();
  const at = retryAt(readRecord(), now);
  if (now < at) {
    const why = 'waiting until ' + new Date(at).toLocaleTimeString() + ' (the last reload still had the old build)';
    if (why !== lastWhy) log(why);
    lastWhy = why;
    return;
  }
  let why;
  if (wall) {
    const p = probe() || {};
    const up = dialogUp();
    if (!up) dialogSince = 0;
    else if (!dialogSince) dialogSince = now;
    why = wallBlockers({ voice: !!p.voice, playing: !!p.playing, lastTouch, dialog: up, dialogSince }, now);
  } else {
    if (trigger !== 'route' && trigger !== 'visible') return; // other pages wait for a natural moment
    why = webBlockers({ dirty: unsaved(), typing: typing() });
  }
  if (why.length) {
    const w = 'new build waiting: ' + why.join(', ');
    if (w !== lastWhy) log(w);
    lastWhy = w;
    return;
  }
  reload(trigger);
}

/** The hub's build (from the live socket's hello, or api/hello). */
export function noteHubBuild(build, trigger = 'hello') {
  if (!started || !build || typeof build !== 'string') return;
  hub = build;
  if (build === own) {
    if (readRecord()) writeRecord(null);
    clearInterval(poll);
    poll = null;
    lastWhy = '';
    announced = '';
    return;
  }
  if (announced !== build) log(`the hub serves ${build}; this page runs ${own}`);
  announced = build;
  if (wall && !poll) poll = setInterval(() => consider('poll'), POLL_MS);
  consider(trigger);
}

async function check(trigger = 'check') {
  try {
    const r = await fetch(new URL('api/hello', window.location.href).toString(), { cache: 'no-store', credentials: 'same-origin' });
    if (r.ok) noteHubBuild((await r.json()).webBuild, trigger);
  } catch {
    /* offline: the live socket's hello will tell when it's back */
  }
}

/** Starts watching (once, from main.jsx). Only for built pages: the dev server has no build to compare. */
export function startBuildWatch({ isWall = false } = {}) {
  if (started || !PROD || !own || typeof window === 'undefined') return;
  started = true;
  wall = !!isWall;
  const rec = readRecord();
  if (rec && rec.to === own) {
    log(`now running ${own} (was ${rec.from})`);
    writeRecord(null);
  } else log(`running ${own}`);
  const touched = () => {
    lastTouch = Date.now();
  };
  const opts = { capture: true, passive: true };
  ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach((ev) => window.addEventListener(ev, touched, opts));
  let wasHidden = document.visibilityState === 'hidden';
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      wasHidden = true;
      return;
    }
    if (!wasHidden) return;
    wasHidden = false;
    check('visible');
  });
  window.addEventListener('hashchange', () => setTimeout(() => consider('route'), 0));
  window.addEventListener('online', () => check());
  setInterval(() => check(), CHECK_EVERY_MS);
  // For DevTools (and the test harness): newmanBuild() - what this page and the hub run.
  window.newmanBuild = () => ({ own, hub, wall, lastTouch, record: readRecord(), waiting: lastWhy });
}
