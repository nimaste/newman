// The wall event form's repeats: the dropdown's choices for a start date, custom repeats ↔ RRULE, and their words.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { customOf, customRule, describeCustom, presetOf, presets, shortWords } from '../src/wall/rrule.js';

const fri = new Date(2026, 9, 23, 18, 0); // Fri Oct 23 2026 (the 4th Friday)
const lastFri = new Date(2026, 9, 30, 18, 0);

test('the Repeats choices name the start day', () => {
  const p = presets(fri);
  assert.deepEqual(p.map((x) => x.label), ['Never', 'Every day', 'Every week on Friday', 'Every 2 weeks on Friday', 'Every month on the 4th Friday', 'Every year on Oct 23']);
  assert.equal(p.find((x) => x.id === 'month').rule, 'FREQ=MONTHLY;BYDAY=4FR');
  assert.equal(presets(lastFri).find((x) => x.id === 'month').label, 'Every month on the last Friday');
});

test('a rule is matched back to its choice, or Custom', () => {
  assert.equal(presetOf('FREQ=WEEKLY;BYDAY=FR', fri), 'week');
  assert.equal(presetOf('FREQ=WEEKLY;BYDAY=FR;INTERVAL=2', fri), '2weeks');
  assert.equal(presetOf('FREQ=WEEKLY;BYDAY=TU,TH', fri), 'custom');
});

test('custom repeats round-trip', () => {
  const c = { every: 1, unit: 'weeks', days: [4, 2], ends: 'count', count: 10, until: null };
  const rule = customRule(c);
  assert.equal(rule, 'FREQ=WEEKLY;BYDAY=TU,TH;COUNT=10');
  const back = customOf(rule, fri);
  assert.deepEqual(back.days, [2, 4]);
  assert.equal(back.ends, 'count');
  assert.equal(describeCustom(c), 'Every week on Tuesday and Thursday, 10 times');
  assert.equal(shortWords(rule, fri), 'every week on Tue, Thu');
});

test('an end date: a date for all-day events, UTC for timed ones', () => {
  const until = new Date(2027, 5, 25);
  assert.equal(customRule({ every: 2, unit: 'months', days: [], ends: 'date', until }, true), 'FREQ=MONTHLY;INTERVAL=2;UNTIL=20270625');
  const timed = customRule({ every: 1, unit: 'days', days: [], ends: 'date', until });
  assert.match(timed, /^FREQ=DAILY;UNTIL=2027062\dT\d{6}Z$/);
  const back = customOf(timed, fri);
  assert.equal(back.until.getDate(), 25);
  assert.equal(describeCustom({ every: 1, unit: 'days', ends: 'date', until }), 'Every day, until Fri, Jun 25, 2027');
});
