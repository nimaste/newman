// The wall top bar's page tabs: who gets the width, where they sit, when names show (src/wall/tabfit.js).
import test from 'node:test';
import assert from 'node:assert/strict';
import { centreRoom, scrollFor, stripWidth, tabFit, tabRoom, TAB_NAMES } from '../src/wall/tabfit.js';

// A 1280 px wall: 1216 px of bar, a 300 px clock and date, the dim button (48), 20 px between them.
const BAR = { bar: 1216, clock: 300, right: 48, gap: 20, chrome: 14, tabGap: 6 };
const CHIP = 48 + 10 + 300; // the music chip joins the dim button
const pages = (n) => ({ named: Array(n).fill(120), icons: Array(n).fill(44), current: 1 });
const fit = (n, more = {}) => tabFit({ ...BAR, ...pages(n), ...more });
const pick = ({ names, place, scroll }) => ({ names, place, scroll });

test('the clock and the right side come first; the tabs get the rest', () => {
  assert.deepEqual(TAB_NAMES, ['current', 'fit']);
  assert.equal(tabRoom(BAR), 1216 - 300 - 48 - 40);
  assert.equal(tabRoom({ ...BAR, decor: true }), 1216 - 300 - 48 - 60);
  assert.equal(centreRoom(BAR), 1216 - 600 - 40);
  assert.equal(centreRoom({ ...BAR, right: CHIP }), 1216 - 2 * CHIP - 40);
  assert.equal(tabRoom({ ...BAR, bar: 200 }), 0);
  assert.equal(stripWidth([44, 120, 44], 14, 6), 234);
  assert.equal(stripWidth([], 14, 6), 0);
});

test('icons, current tab named (the default): centred at 4 and 6 tabs on 1280, never all named', () => {
  assert.deepEqual(pick(fit(4)), { names: false, place: 'centre', scroll: false });
  assert.deepEqual(pick(fit(6)), { names: false, place: 'centre', scroll: false });
  assert.deepEqual(pick(fit(6, { pref: 'nonsense' })), { names: false, place: 'centre', scroll: false });
  // 8 tabs (494 px) clear the clock at the centre, until the music chip widens the right side.
  assert.deepEqual(pick(fit(8)), { names: false, place: 'centre', scroll: false });
  assert.deepEqual(pick(fit(8, { right: CHIP })), { names: false, place: 'start', scroll: false });
  assert.deepEqual(pick(fit(6, { right: CHIP })), { names: false, place: 'centre', scroll: false });
  // too many even as icons: beside the clock, scrolling
  assert.deepEqual(pick(fit(20)), { names: false, place: 'start', scroll: true });
});

test('names when they fit: centred, else beside the clock, else icons', () => {
  assert.deepEqual(pick(fit(4, { pref: 'fit' })), { names: true, place: 'centre', scroll: false }); // 512 px
  assert.deepEqual(pick(fit(6, { pref: 'fit' })), { names: true, place: 'start', scroll: false }); // 764 px: not at the centre
  assert.deepEqual(pick(fit(8, { pref: 'fit' })), { names: false, place: 'centre', scroll: false }); // 1016 px: icons
  assert.deepEqual(pick(fit(8, { pref: 'fit', right: CHIP })), { names: false, place: 'start', scroll: false });
  const exact = stripWidth(Array(4).fill(120), 14, 6);
  assert.equal(fit(4, { pref: 'fit', bar: exact + 600 + 40 - 0.4 }).place, 'centre'); // rounding
});

test('a decoration: the tabs sit right after it, never centred', () => {
  assert.deepEqual(pick(fit(4, { decor: true })), { names: false, place: 'start', scroll: false });
  assert.deepEqual(pick(fit(4, { decor: true, pref: 'fit' })), { names: true, place: 'start', scroll: false });
});

test('the current tab scrolls into view past the fade', () => {
  const widths = [44, 44, 44, 44, 44, 120, 44, 44, 44, 44];
  // start 7 (padding + border), gap 6: tab 5 sits at 7 + 5 * 50 = 257..377
  assert.equal(scrollFor({ widths, index: 5, gap: 6, start: 7, view: 300, now: 0, fade: 28 }), 377 + 28 - 300);
  assert.equal(scrollFor({ widths, index: 0, gap: 6, start: 7, view: 300, now: 200, fade: 28 }), 0);
  assert.equal(scrollFor({ widths, index: 2, gap: 6, start: 7, view: 300, now: 50, fade: 28 }), 50); // already in view
});