// The web-build reload rules (src/build.js), under plain node: `npm test` (node --test).
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildIdOf, retryAt, wallBlockers, webBlockers, RETRY_DELAYS_MS, TOUCH_QUIET_MS, DIALOG_QUIET_MS } from '../src/build.js';

test('build id: the hashed names, sorted, the same however the page was reached', () => {
  assert.equal(buildIdOf(['http://10.1.1.2:8093/assets/index-Cg0z-opQ.js', 'http://10.1.1.2:8093/wall/assets/index-D4f2a.css']), 'index-Cg0z-opQ.js index-D4f2a.css');
  assert.equal(buildIdOf(['./assets/b.css', './assets/a.js', './assets/a.js']), 'a.js b.css');
  assert.equal(buildIdOf(['/src/main.jsx', '/favicon.ico', null]), ''); // the dev server: nothing to compare
});

test('wall: idle only when nothing is going on', () => {
  const now = 10_000_000;
  const calm = { voice: false, playing: false, lastTouch: now - TOUCH_QUIET_MS - 1, dialog: false, dialogSince: 0 };
  assert.deepEqual(wallBlockers(calm, now), []);
  assert.deepEqual(wallBlockers({ ...calm, voice: true }, now), ['voice']);
  assert.deepEqual(wallBlockers({ ...calm, playing: true }, now), ['music']);
  assert.deepEqual(wallBlockers({ ...calm, lastTouch: now - 5000 }, now), ['touched']);
  // A sheet / dialog / cook mode: in use until it has sat untouched for a while (counted from when it appeared).
  assert.deepEqual(wallBlockers({ ...calm, dialog: true, dialogSince: now - 1000 }, now), ['dialog']);
  assert.deepEqual(wallBlockers({ ...calm, dialog: true, dialogSince: now - DIALOG_QUIET_MS - 1, lastTouch: now - DIALOG_QUIET_MS - 1 }, now), []);
  assert.deepEqual(wallBlockers({ ...calm, dialog: true, dialogSince: now - DIALOG_QUIET_MS - 1, lastTouch: now - 2 * TOUCH_QUIET_MS }, now), ['dialog']);
  assert.deepEqual(wallBlockers({ voice: true, playing: true, lastTouch: now, dialog: true, dialogSince: now }, now), ['voice', 'music', 'touched', 'dialog']);
});

test('other pages: never mid-edit', () => {
  assert.deepEqual(webBlockers({ dirty: false, typing: false }), []);
  assert.deepEqual(webBlockers({ dirty: true, typing: true }), ['unsaved', 'typing']);
});

test('a reload that still has the old build backs off 1 min, 5 min, then 30 min', () => {
  const at = 1_000_000;
  assert.equal(retryAt(null, at), 0);
  assert.equal(retryAt({ count: 1, at }, at + 10), at + RETRY_DELAYS_MS[0]);
  assert.equal(retryAt({ count: 2, at }, at + 10), at + 5 * 60 * 1000);
  assert.equal(retryAt({ count: 3, at }, at + 10), at + 30 * 60 * 1000);
  assert.equal(retryAt({ count: 9, at }, at + 10), at + 30 * 60 * 1000);
  assert.equal(retryAt({ count: 1, at: at + 99 * 60 * 1000 }, at), 0); // the clock jumped back: don't wait forever
});
