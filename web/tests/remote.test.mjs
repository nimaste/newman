// A remote's buttons (src/model.js) and what an automation can make a device do, from kind metadata alone.
import test from 'node:test';
import assert from 'node:assert/strict';
import { actionCommands, buttonNames, entityStateText, shortEventLabel } from '../src/model.js';

const press = (id, name, how = 'pressed') => ({ id, label: `“${name}” is ${how}` });
const REMOTE = {
  id: 'remote', states: [{ id: 'idle', label: 'Idle' }], commands: [],
  events: [press('on', 'On'), press('button_1', 'Button 1'), press('button_1_long', 'Button 1', 'long-pressed'),
    press('button_1_released', 'Button 1', 'let go after being long-pressed'), press('button_2', 'Button 2'),
    press('button_2_long', 'Button 2', 'long-pressed')],
};
const remote = (events) => ({ id: 'r', name: 'Dual button', kind: 'remote', state: 'idle', available: true, attrs: { events } });

test('a long press and letting go are the same button, not more buttons', () => {
  const e = remote(['button_1', 'button_1_long', 'button_1_released', 'button_2', 'button_2_long']);
  assert.deepEqual(buttonNames(e, REMOTE), ['Button 1', 'Button 2']);
  assert.equal(entityStateText(e, REMOTE), 'Buttons: Button 1, Button 2');
  assert.equal(shortEventLabel('“Button 1” is long-pressed'), 'Button 1');
  assert.equal(shortEventLabel('Person detected'), 'Person');
});

test('a Pico’s long presses and let gos are still its five buttons', () => {
  const buttons = [['on', 'On'], ['favorite', 'Favorite'], ['off', 'Off'], ['raise', 'Raise'], ['lower', 'Lower']];
  const kind = { ...REMOTE, events: buttons.flatMap(([b, n]) => [press(b, n), press(`${b}_long`, n, 'long-pressed'),
    press(`${b}_released`, n, 'let go after being long-pressed')]) };
  const e = remote(kind.events.map((x) => x.id));
  assert.deepEqual(buttonNames(e, kind), ['On', 'Favorite', 'Off', 'Raise', 'Lower']);
  assert.equal(entityStateText(e, kind), '5 buttons (On, Favorite, …)');
});

test('automation-only commands are offered to automations, after the device’s own', () => {
  const light = { id: 'light', commands: [{ id: 'turn_on' }], automationCommands: [{ id: 'brighten_while_held', label: 'Brighten while held' }] };
  assert.deepEqual(actionCommands(light).map((c) => [c.id, !!c.automationOnly]), [['turn_on', false], ['brighten_while_held', true]]);
  assert.deepEqual(actionCommands({ id: 'old', commands: [{ id: 'press' }] }).map((c) => c.id), ['press']);
  assert.deepEqual(actionCommands(undefined), []);
});
