// Wall themes (src/wall/theme.js): the Christmas season (every year, over New Year), the look today,
// the top bar decoration's fit (what doesn't fit goes to the dimmed screen); and the Climate glance
// tile's reading of devices (src/wall/climate.js) - exact values, by what devices report.
import test from 'node:test';
import assert from 'node:assert/strict';
import { christmasOf, chosenTheme, decorFit, msToMidnight, seasonActive, themeNow, SEASON } from '../src/wall/theme.js';
import { deg, pct, reportsTemperature, roomColumns, roomOf, setsTemperature, thermostatOf, warmth } from '../src/wall/climate.js';
import { stripWidth, tabFit } from '../src/wall/tabfit.js';
import { setCategoryScreens } from '../src/model.js';

// Each category's screen role comes from the hub (/api/categories), as the app loads it.
setCategoryScreens([{ id: 'climate', screen: 'climate' }, { id: 'servers', screen: null }, { id: 'other', screen: null }]);

const day = (y, m, d) => new Date(y, m - 1, d, 12);

test('the Christmas season: inclusive, wraps over New Year, off means never', () => {
  const s = { ...SEASON };
  const cases = [[2026, 11, 30, false], [2026, 12, 1, true], [2026, 12, 25, true], [2026, 12, 31, true], [2027, 1, 1, true], [2027, 1, 6, true], [2027, 1, 7, false], [2027, 7, 4, false]];
  for (const [y, m, d, on] of cases) assert.equal(seasonActive(s, day(y, m, d)), on, `${y}-${m}-${d}`);
  assert.equal(seasonActive({ from: '12-20', to: '12-26' }, day(2026, 12, 26)), true);
  assert.equal(seasonActive({ from: '12-20', to: '12-26' }, day(2026, 12, 27)), false);
  assert.equal(seasonActive(null, day(2026, 12, 25)), false);
  assert.equal(seasonActive({}, day(2026, 12, 25)), true); // on, no dates: the default ones
  assert.equal(seasonActive({ from: 'nope', to: '01-06' }, day(2026, 12, 25)), false);
});

test('the look today: Christmas in its season replaces the chosen theme', () => {
  const dash = { theme: 'glass', christmas: { season: SEASON } };
  assert.equal(themeNow(dash, day(2026, 12, 24)), 'christmas');
  assert.equal(themeNow(dash, day(2026, 10, 7)), 'glass');
  assert.equal(themeNow({ theme: 'night-glass' }, day(2026, 12, 24)), 'midnight');
  assert.equal(themeNow({ theme: 'disco' }), 'midnight');
  assert.equal(chosenTheme(null), 'midnight');
  assert.deepEqual(christmasOf({}), { snow: true, greeting: 'Merry Christmas', characters: 'builtin', place: 'clock', pictures: [], season: null });
  assert.equal(christmasOf({ christmas: { greeting: '' } }).greeting, '');
});

test('the next check is at local midnight', () => {
  const ms = msToMidnight(new Date(2026, 11, 31, 23, 59, 0));
  assert.equal(ms, 61000);
  assert.ok(msToMidnight(new Date(2026, 11, 31, 23, 59, 59, 999)) >= 1000);
});

test('the decoration only gets what the tabs leave over; the rest moves to the dimmed screen', () => {
  // greeting + characters 400 px, characters alone 150 px
  assert.equal(decorFit({ room: 500, full: 400, cast: 150 }), 'all');
  assert.equal(decorFit({ room: 300, full: 400, cast: 150 }), 'cast');
  assert.equal(decorFit({ room: 100, full: 400, cast: 150 }), 'none');
  assert.equal(decorFit({ room: 300, full: 400, cast: 0 }), 'none'); // no characters: greeting or nothing
  // a little give at the edge, so it doesn't flicker
  assert.equal(decorFit({ room: 398, full: 400, cast: 150, shown: null }), 'cast');
  assert.equal(decorFit({ room: 396, full: 400, cast: 150, shown: 'all' }), 'all');
  // From the real bar: a 1280 px wall, 4 / 6 / 8 / 10 tabs - what's left beside them, and what fits.
  const BAR = { bar: 1232, clock: 300, right: 48, gap: 20, chrome: 14, tabGap: 6, decor: true };
  const left = (n) => {
    const icons = Array(n).fill(44);
    const named = Array(n).fill(120);
    const f = tabFit({ ...BAR, named, icons, current: 0 });
    const widths = icons.map((w, i) => (i === 0 ? named[i] : w));
    return f.scroll ? 0 : BAR.bar - BAR.clock - BAR.right - 3 * BAR.gap - stripWidth(widths, BAR.chrome, BAR.tabGap);
  };
  const want = { full: 420, cast: 170 };
  assert.equal(decorFit({ room: left(4), ...want }), 'all');
  assert.equal(decorFit({ room: left(8), ...want }), 'cast');
  assert.ok(left(4) > left(6) && left(6) > left(8));
});

const KIND_T = { id: 'thermostat', category: 'climate', states: [{ id: 'heat', label: 'Heat' }, { id: 'off', label: 'Off' }],
  commands: [{ id: 'set_mode', valueType: 'state' }, { id: 'set_temperature', valueType: 'number', unit: '°C', min: 5, max: 35, attr: 'targetTemp' }] };
const P = (id, unit, attr, value, type = 'number', options = []) => ({ id, label: id, unit, attr, type, value, options });
const THERMO = { id: 't', name: 'Main Floor', state: 'heat', attrs: { targetTemp: 22.5, currentTemp: 22.9, humidity: 50, action: 'idle' },
  properties: [P('temperature', '°C', 'currentTemp', 22.9), P('humidity', '%', 'humidity', 50), P('target', '°C', 'targetTemp', 22.5), P('action', '', 'action', 'idle', 'text', [{ value: 'idle', label: 'Idle' }])] };

test('the thermostat and rooms, by what they report, exactly as reported', () => {
  assert.equal(setsTemperature(KIND_T), true);
  assert.equal(setsTemperature({ commands: [{ valueType: 'number', unit: 'min' }] }), false);
  const t = thermostatOf(THERMO, KIND_T);
  assert.deepEqual({ target: t.target, now: t.now, humidity: t.humidity, mode: t.mode, action: t.action, min: t.min, max: t.max },
    { target: 22.5, now: 22.9, humidity: 50, mode: 'Heat', action: 'Idle', min: 5, max: 35 });
  const sensorKind = { category: 'climate', commands: [] };
  const room = roomOf({ id: 's', name: 'Office', state: '21.40', properties: [P('temperature', '°C', '', '21.40'), P('humidity', '%', 'humidity', 46)] }, sensorKind);
  assert.deepEqual([room.temp, room.humidity], ['21.40', 46]);
  assert.equal(deg(room.temp), '21.40°'); // never rounded or padded
  assert.equal(deg(19.25), '19.25°');
  assert.equal(pct(46), '46%');
  assert.equal(deg(null), '—');
  // CPU / disk temperatures aren't rooms; a generic sensor measuring °C is; degrees of angle aren't.
  assert.equal(reportsTemperature({ properties: [P('temperature', '°C', 'temperature', 55)] }, { category: 'servers' }), false);
  assert.equal(reportsTemperature({ properties: [P('value', '°C', '', '19.5')] }, { category: 'other' }), true);
  assert.equal(reportsTemperature({ properties: [P('elevation', '°', 'elevation', 30)] }, { category: 'climate' }), false);
  assert.equal(reportsTemperature(THERMO, KIND_T), true);
});

test('cool-to-warm bars and room columns', () => {
  assert.equal(warmth(16), 0);
  assert.equal(warmth(26), 1);
  assert.equal(warmth(21), 0.5);
  assert.ok(Math.abs(warmth(69.8, '°F') - 0.5) < 1e-9);
  assert.equal(warmth('n/a'), null);
  assert.equal(roomColumns(330, 5), 2); // a narrow tile: two columns
  assert.equal(roomColumns(700, 5), 4);
  assert.equal(roomColumns(700, 1), 1);
});
