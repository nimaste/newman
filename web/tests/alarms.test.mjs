// The wall's alarms and timers: the words on the cards and sheets, what rings and shows when.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  alarmCardName,
  alarmCards,
  alarmRingLabel,
  alarmSchedule,
  alarmSmall,
  alarmTitle,
  clockText,
  dayWord,
  durationWords,
  from24,
  localRings,
  minuteOptions,
  ringClock,
  ringingNow,
  timerLeft,
  to24,
} from '../src/wall/alarmTimes.js';

const thu = new Date(2026, 9, 22, 21, 0); // Thursday evening
const s = (d) => d.getTime() / 1000;
const weekdays = { id: 'a', hour: 6, minute: 30, days: [1, 2, 3, 4, 5], label: '', enabled: true, state: 'set', repeat: 'Weekdays', time: '6:30 AM', skip: null, nextAt: s(new Date(2026, 9, 23, 6, 30)) };
const gym = { id: 'g', hour: 5, minute: 45, days: [], label: 'gym', enabled: true, state: 'set', repeat: '', time: '5:45 AM', nextAt: s(new Date(2026, 9, 23, 5, 45)) };
const saturdays = { id: 's', hour: 8, minute: 0, days: [6], label: '', enabled: false, state: 'set', repeat: 'Saturdays', time: '8:00 AM', nextAt: null };

test('times and the Edit screen', () => {
  assert.equal(clockText(6, 30), '6:30 AM');
  assert.equal(clockText(0, 5), '12:05 AM');
  assert.equal(clockText(12, 0), '12:00 PM');
  assert.equal(ringClock(18, 30), '6:30');
  assert.equal(to24(12, 'AM'), 0);
  assert.equal(to24(7, 'PM'), 19);
  assert.deepEqual(from24(19), { h12: 7, ampm: 'PM' });
  assert.equal(minuteOptions(null).length, 12);
  assert.deepEqual(minuteOptions(32).slice(6, 8), [30, 32]); // a spoken 6:32 stays 6:32
});

test('the Alarms list says what each one does', () => {
  assert.equal(alarmTitle(weekdays), 'Weekdays');
  assert.equal(alarmSmall(weekdays, thu), 'Mon Tue Wed Thu Fri');
  assert.equal(alarmTitle(gym), 'Gym alarm');
  assert.equal(alarmSmall(gym, thu), 'Tomorrow only');
  assert.equal(alarmTitle(saturdays), 'Saturdays');
  assert.equal(alarmSmall(saturdays, thu), 'Off');
  assert.equal(alarmSmall({ ...weekdays, skip: '2026-10-23' }, thu), 'Mon Tue Wed Thu Fri · skipping tomorrow');
  assert.equal(alarmTitle({ ...gym, label: '' }), 'Alarm');
  assert.equal(dayWord(new Date(2026, 9, 25, 7), thu), 'Sunday');
  assert.equal(dayWord(new Date(2026, 10, 2, 7), thu), 'Nov 2');
});

test('the cards: the next alarm in the coming 24 hours, and snoozed ones', () => {
  const now = s(thu);
  assert.deepEqual(alarmCards([weekdays, gym, saturdays], now).map((a) => a.id), ['g']);
  assert.deepEqual(alarmCards([{ ...weekdays, nextAt: now + 2 * 86400 }], now), []);
  const snoozed = { ...weekdays, state: 'snoozed', snoozeUntil: now + 300 };
  assert.deepEqual(alarmCards([snoozed, gym], now).map((a) => a.id), ['a', 'g']);
  assert.equal(alarmCardName(weekdays), 'Alarm · weekdays');
  assert.equal(alarmCardName(gym), 'Alarm · gym');
  assert.equal(alarmCardName(snoozed), 'Alarm · snoozed');
  assert.equal(alarmRingLabel(gym), 'GYM ALARM');
  assert.equal(alarmRingLabel(weekdays), 'ALARM');
});

test('what rings: first to ring first; the wall rings an alarm itself when the hub is away', () => {
  const t = { id: 't', state: 'ringing', endsAt: 100, seconds: 480, name: 'pasta timer' };
  const a = { ...gym, state: 'ringing', ringingSince: 50 };
  assert.deepEqual(ringingNow([t], [a]).map((x) => x.kind), ['alarm', 'timer']);
  const due = { ...gym, nextAt: 1000 };
  assert.deepEqual(localRings([due], 1020, 600), []); // the hub has a moment to say
  assert.deepEqual(localRings([due], 1031, 600).map((x) => x.id), ['g']);
  assert.deepEqual(localRings([due], 1031, 600, new Set(['g@1000'])), []); // stopped here already
  assert.deepEqual(localRings([due], 1601, 600), []); // past "Stop ringing after"
  assert.deepEqual(ringingNow([], [], [due])[0].local, true);
  assert.deepEqual(alarmSchedule([weekdays, gym, saturdays]).map((x) => [x.id, x.title]), [['g', 'GYM ALARM'], ['a', 'ALARM']]);
});

test('timers: what is left, and their full time in words', () => {
  assert.equal(timerLeft({ state: 'paused', left: 380 }, 0), 380);
  assert.equal(timerLeft({ state: 'running', endsAt: 160 }, 100), 60);
  assert.equal(timerLeft({ state: 'ringing', endsAt: 100 }, 200), 0);
  assert.equal(durationWords(480), '8 minutes');
  assert.equal(durationWords(5400), '1 hour 30 minutes');
  assert.equal(durationWords(90), '1 minute 30 seconds');
});
