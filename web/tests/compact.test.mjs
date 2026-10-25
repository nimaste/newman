// The wall's compact layout: which tiles share a swipe page (src/wall/compact.js).
import test from 'node:test';
import assert from 'node:assert/strict';
import { COMPACT_MAX_H, compactPages, pageAt, readingOrder } from '../src/wall/compact.js';

const t = (id, type, x, y) => ({ id, type, x, y, w: 6, h: 6, config: {} });
// The flag comes from the hub's tile catalog; these names are only test data.
const TYPES = [{ type: 'big', ownPage: true }, { type: 'small' }, { type: 'other', ownPage: false }];
const ids = (pages) => pages.map((p) => p.map((x) => x.id));

test('short screens only', () => {
  assert.equal(COMPACT_MAX_H, 600);
});

test('reading order: row by row, then left to right', () => {
  const tiles = [t('c', 'small', 8, 0), t('d', 'small', 0, 6), t('a', 'small', 0, 0), t('b', 'small', 18, 0)];
  assert.deepEqual(readingOrder(tiles).map((x) => x.id), ['a', 'c', 'b', 'd']);
  assert.deepEqual(tiles.map((x) => x.id), ['c', 'd', 'a', 'b'], 'the page itself is left alone');
});

test('two to a page; a tile that needs the room gets a page to itself', () => {
  const tiles = [t('a', 'small', 0, 0), t('b', 'big', 8, 0), t('c', 'small', 18, 0), t('d', 'other', 0, 6), t('e', 'small', 18, 8), t('f', 'small', 8, 10)];
  assert.deepEqual(ids(compactPages(tiles, TYPES)), [['a'], ['b'], ['c', 'd'], ['e', 'f']]);
});

test('a tile left without a partner is alone on its page', () => {
  assert.deepEqual(ids(compactPages([t('a', 'small', 0, 0), t('b', 'small', 1, 0), t('c', 'small', 2, 0)], TYPES)), [['a', 'b'], ['c']]);
  assert.deepEqual(ids(compactPages([t('a', 'small', 0, 0), t('b', 'big', 1, 0), t('c', 'small', 2, 0)], TYPES)), [['a'], ['b'], ['c']]);
});

test('no catalog yet, or no tiles: still pages', () => {
  assert.deepEqual(ids(compactPages([t('a', 'big', 0, 0), t('b', 'big', 1, 0)], null)), [['a', 'b']]);
  assert.deepEqual(compactPages([], TYPES), []);
  assert.deepEqual(compactPages(undefined, TYPES), []);
});

test('the page a strip shows', () => {
  assert.equal(pageAt(0, 900, 4), 0);
  assert.equal(pageAt(880, 900, 4), 1);
  assert.equal(pageAt(5000, 900, 4), 3);
  assert.equal(pageAt(100, 0, 4), 0);
});
