// The wall apps' shell colour (src/wall/theme.js shellOf, reported with NewmanWall.setTheme) matches the
// page colours in the CSS; and device labels prefer the hub's own wording (src/model.js entityLabel).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { SHELL, THEMES, shellOf } from '../src/wall/theme.js';
import { entityLabel } from '../src/model.js';

const css = (f) => readFileSync(new URL('../src/wall/' + f, import.meta.url), 'utf8');

test('every theme has a shell colour, and it is the page colour in the CSS', () => {
  for (const t of THEMES) assert.match(SHELL[t].background, /^#[0-9a-f]{6}$/, t);
  assert.ok(css('wall.css').includes(`var(--wt-page, ${SHELL.midnight.background})`), 'midnight: wall.css fallback');
  const theme = css('theme.css');
  for (const t of ['glass', 'christmas']) {
    const block = theme.slice(theme.indexOf(`:root[data-wt-theme='${t}']`));
    const page = /--wt-page:\s*(#[0-9a-f]{6})/i.exec(block);
    assert.equal(page && page[1].toLowerCase(), SHELL[t].background, t);
    const scheme = /color-scheme:\s*(light|dark)/.exec(block);
    assert.equal(scheme && scheme[1], SHELL[t].scheme, t + ' scheme');
  }
  assert.deepEqual(shellOf('nope'), SHELL.midnight);
  assert.deepEqual(shellOf('glass'), { background: '#e6def5', scheme: 'light' });
});

test("device labels: the hub's stateLabel first (a tablet's note), then the kind's label, then the state", () => {
  const tablet = { states: [{ id: 'charging', label: 'Charging' }, { id: 'battery', label: 'On battery' }] };
  assert.equal(entityLabel({ state: 'charging', stateLabel: 'Screen off', attrs: { screen: 'off' } }, tablet), 'Screen off');
  assert.equal(entityLabel({ state: 'charging' }, tablet), 'Charging');
  assert.equal(entityLabel({ state: 'odd' }, tablet), 'odd');
  assert.equal(entityLabel({ state: 'x' }, null), 'x');
  assert.equal(entityLabel(null, tablet), '');
  assert.equal(entityLabel({}, tablet), '');
});
