// The Devices page's category chips (src/model.js summaryChips) are worded from kind metadata alone.
import test from 'node:test';
import assert from 'node:assert/strict';
import { needsLook, summaryChips } from '../src/model.js';

const st = (...pairs) => pairs.map(([id, label]) => ({ id, label }));
const KINDS = [
  { id: 'lock', name: 'Lock', plural: 'Locks', states: st(['LOCKED', 'Locked'], ['UNLOCKED', 'Unlocked'], ['LOCKING', 'Locking']), activeState: 'UNLOCKED', secureState: 'LOCKED', transitional: ['LOCKING'], commands: [{ id: 'lock', confirmsTo: 'LOCKED' }] },
  { id: 'alarm', name: 'Alarm', plural: 'Alarms', states: st(['disarmed', 'Disarmed'], ['armed_away', 'Armed (away)'], ['triggered', 'Triggered']), activeState: 'disarmed', isMode: true, commands: [{ id: 'disarm', confirmsTo: 'disarmed' }, { id: 'arm_away', confirmsTo: 'armed_away' }] },
  { id: 'contact', name: 'Door sensor', plural: 'Doors & windows', states: st(['open', 'Open'], ['closed', 'Closed']), activeState: 'open', secureState: 'closed' },
  { id: 'motion', name: 'Motion sensor', plural: 'Motion', states: st(['detected', 'Motion'], ['clear', 'Clear']), activeState: 'detected' },
  { id: 'leak', name: 'Leak sensor', plural: 'Leak sensors', states: st(['dry', 'Dry'], ['wet', 'Leak!']), activeState: 'wet', secureState: 'dry', urgent: true },
  { id: 'light', name: 'Light', plural: 'Lights', states: st(['on', 'On'], ['off', 'Off']), activeState: 'on', commands: [{ id: 'turn_on', confirmsTo: 'on' }] },
  { id: 'switch', name: 'Switch', plural: 'Switches', states: st(['on', 'On'], ['off', 'Off']), activeState: 'on', commands: [{ id: 'turn_on', confirmsTo: 'on' }] },
  { id: 'thermostat', name: 'Thermostat', plural: 'Thermostats', states: st(['off', 'Off'], ['heat', 'Heat']), commands: [{ id: 'set_mode', valueType: 'state' }] },
  { id: 'temperature', name: 'Temperature sensor', plural: 'Temperature', states: [], unit: '°C', commands: [] },
  { id: 'server', name: 'Server', plural: 'Servers', states: st(['online', 'Online'], ['offline', 'Offline']), commands: [] },
];
const byId = Object.fromEntries(KINDS.map((k) => [k.id, k]));
const dev = (name, kind, state, extra = {}) => ({ id: name, name, kind, state, available: true, attrs: {}, ...extra });
const texts = (list) => summaryChips(list, KINDS).map((c) => c.text);

test('a calm security category: all secure, the alarm, motion by name', () => {
  const list = [
    dev('Front Door', 'lock', 'LOCKED'),
    dev('Side Door', 'lock', 'LOCKED'),
    dev('Alarm', 'alarm', 'disarmed'),
    dev('Master Bedroom', 'motion', 'detected'),
    dev('Hall', 'motion', 'clear'),
  ];
  assert.deepEqual(texts(list), ['All locked', 'Alarm disarmed', 'Motion: Master Bedroom']);
  assert.equal(list.some((e) => needsLook(e, byId[e.kind])), false);
});

test('something wrong comes first, urgent before merely open, and needs a look', () => {
  const list = [
    dev('Front Door', 'lock', 'LOCKED'),
    dev('Side Door', 'lock', 'UNLOCKED'),
    dev('Basement', 'leak', 'wet'),
    dev('Garage', 'contact', 'closed', { available: false }),
  ];
  const chips = summaryChips(list, KINDS);
  assert.deepEqual(chips.map((c) => [c.text, c.tone]), [
    ['Basement leak!', 'bad'],
    ['Side Door unlocked', 'warn'],
    ['1 offline', 'warn'],
  ]);
  assert.equal(needsLook(list[1], byId.lock), true);
  assert.equal(needsLook(list[3], byId.contact), true);
  assert.equal(needsLook(list[0], byId.lock), false);
});

test('a moment-long state (locking) is not "unlocked"', () => {
  assert.deepEqual(texts([dev('Front Door', 'lock', 'LOCKING'), dev('Side Door', 'lock', 'LOCKED')]), ['All locked']);
});

test('things you switch are counted across kinds; readings and modes by name', () => {
  const list = [
    dev('Kitchen', 'light', 'on'),
    dev('Hall', 'light', 'off'),
    dev('Porch plug', 'switch', 'on'),
    dev('Main Floor', 'thermostat', 'heat', { stateLabel: 'Heat' }),
    dev('Basement', 'temperature', '21.13'),
  ];
  assert.deepEqual(texts(list), ['2 on', 'Main Floor · Heat', 'Basement 21.1 °C']);
});

test('health issues and reported problems; nothing else to say = how many are online', () => {
  const list = [
    dev('NAS', 'server', 'online'),
    dev('Pi', 'server', 'online', { attrs: { problem: 'Disk almost full' } }),
    dev('Router', 'server', 'online'),
    dev('Old box', 'server', 'offline'),
  ];
  assert.deepEqual(texts(list), ['1 offline', 'Pi: disk almost full', '3 online']);
  assert.equal(needsLook(list[1], byId.server), true);
  // One or two: what they're doing instead of a count.
  assert.deepEqual(texts([dev('NAS', 'server', 'online')]), ['NAS · Online']);
  const sensor = dev('Back Door', 'contact', 'closed', { health: { issues: ['Battery 12%'] } });
  assert.deepEqual(texts([sensor]), ['Back Door: battery 12%', 'Back Door closed']);
});

test('an alarm in a state no command sets (triggered) is flagged', () => {
  const chips = summaryChips([dev('Alarm', 'alarm', 'triggered')], KINDS);
  assert.deepEqual(chips.map((c) => [c.text, c.tone]), [['Alarm triggered', 'bad']]);
});
