// A tile's Look (src/wall/look.js): tiles, list, or auto by the tile's width; older devices tiles by "style".
import test from 'node:test';
import assert from 'node:assert/strict';
import { LIST_BELOW, lookOf } from '../src/wall/look.js';

test('a chosen look stays whatever the width', () => {
  assert.equal(lookOf({ look: 'list' }, 2000), 'list');
  assert.equal(lookOf({ look: 'tiles' }, 100), 'tiles');
});

test('auto: a list when the tile is narrow, cards when there is room', () => {
  assert.equal(lookOf({ look: 'auto' }, LIST_BELOW - 1), 'list');
  assert.equal(lookOf({ look: 'auto' }, LIST_BELOW), 'tiles');
  assert.equal(lookOf({}, 300), 'list');
  assert.equal(lookOf({}, 0), 'tiles'); // not drawn yet
});

test('a devices tile saved before Look goes by its style', () => {
  assert.equal(lookOf({ style: 'list' }, 2000), 'list');
  assert.equal(lookOf({ style: 'tiles' }, 200), 'tiles');
  assert.equal(lookOf({ style: 'list', look: 'tiles' }, 200), 'tiles');
});
