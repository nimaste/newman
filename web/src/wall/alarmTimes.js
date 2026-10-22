// Pure helpers for the wall's alarms and timers (AlarmsTimers.jsx, VoiceLayer.jsx): the words on the
// cards, the Alarms & timers sheet and the ringing screen, and which alarm rings or shows when. Plain
// data in, an answer out - `npm test` checks them under node. An alarm is the hub's json
// (server/newman/voice/alarms.py): {hour, minute, days: [0 = Sunday … 6], label, enabled, state, nextAt,
// snoozeUntil, ringingSince, skip: "YYYY-MM-DD" | null, time: "6:30 AM", repeat: "Weekdays" | ""}.

const DAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DAY_LONG = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const DAY_LETTERS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

/** The quick picks on + New timer (seconds and their words). */
export const QUICK_TIMERS = [
  [60, '1 min'],
  [180, '3 min'],
  [300, '5 min'],
  [600, '10 min'],
  [900, '15 min'],
  [1800, '30 min'],
  [3600, '1 hour'],
];

/** "6:30 AM". */
export function clockText(hour, minute) {
  return `${hour % 12 || 12}:${String(minute).padStart(2, '0')} ${hour < 12 ? 'AM' : 'PM'}`;
}

/** The ringing screen's big time: "6:30". */
export function ringClock(hour, minute) {
  return `${hour % 12 || 12}:${String(minute).padStart(2, '0')}`;
}

/** 24-hour hour from the Edit screen's 1-12 and AM / PM. */
export function to24(h12, ampm) {
  const h = Number(h12) % 12;
  return ampm === 'PM' ? h + 12 : h;
}

/** 24-hour hour -> {h12, ampm}. */
export function from24(hour) {
  return { h12: hour % 12 || 12, ampm: hour < 12 ? 'AM' : 'PM' };
}

/** The Edit screen's minutes: every 5, plus the alarm's own minute when it isn't one of them (6:32 stays 6:32). */
export function minuteOptions(own) {
  const out = [];
  for (let m = 0; m < 60; m += 5) out.push(m);
  if (own != null && !out.includes(own)) {
    out.push(own);
    out.sort((a, b) => a - b);
  }
  return out;
}

function midnight(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** Whole days from `now` (a Date) to `when` (a Date): 0 today, 1 tomorrow. */
function daysFrom(now, when) {
  return Math.round((midnight(when) - midnight(now)) / 86400000);
}

/** "Today", "Tomorrow", "Friday" (within the week), "Oct 30". */
export function dayWord(when, now = new Date()) {
  const n = daysFrom(now, when);
  if (n === 0) return 'Today';
  if (n === 1) return 'Tomorrow';
  if (n > 1 && n < 7) return DAY_LONG[when.getDay()];
  return `${MONTHS[when.getMonth()]} ${when.getDate()}`;
}

/** "2026-10-26" -> a local Date at midnight. */
function isoDay(s) {
  const [y, m, d] = String(s).split('-').map(Number);
  return new Date(y, m - 1, d);
}

/** The days an alarm repeats on, Monday first: "Mon Tue Wed Thu Fri". */
export function daysText(days) {
  return [...days].sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7)).map((d) => DAY_SHORT[d]).join(' ');
}

function cap(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/** The Alarms list's title: "Gym alarm", else "Weekdays" / "Saturdays", else "Alarm". */
export function alarmTitle(a) {
  if (a.label) return `${cap(a.label)} alarm`;
  return a.repeat || 'Alarm';
}

/** The small line under it: "Mon Tue Wed Thu Fri", "Tomorrow only", "Off", "… · skipping tomorrow". */
export function alarmSmall(a, now = new Date()) {
  if (!a.enabled && a.state === 'set') return 'Off';
  if (a.days && a.days.length) {
    const s = daysText(a.days);
    return a.skip ? `${s} · skipping ${dayWord(isoDay(a.skip), now).toLowerCase()}` : s;
  }
  return a.nextAt ? `${dayWord(new Date(a.nextAt * 1000), now)} only` : 'Once';
}

/** An alarm card's name: "Alarm · weekdays", "Alarm · gym", "Alarm", "Alarm · snoozed". */
export function alarmCardName(a) {
  if (a.state === 'snoozed') return 'Alarm · snoozed';
  const what = a.label || (a.repeat || '').toLowerCase();
  return what ? `Alarm · ${what}` : 'Alarm';
}

/** The ringing screen's label: "ALARM", "GYM ALARM". */
export function alarmRingLabel(a) {
  return (a.label ? `${a.label} alarm` : 'Alarm').toUpperCase();
}

/**
 * The alarm cards on the wall (hub clock seconds `now`): every snoozed alarm, and the next alarm in the
 * coming 24 hours.
 */
export function alarmCards(alarms, now) {
  const snoozed = alarms.filter((a) => a.state === 'snoozed');
  const next = alarms
    .filter((a) => a.state === 'set' && a.enabled && a.nextAt && a.nextAt - now <= 86400 && a.nextAt > now)
    .sort((a, b) => a.nextAt - b.nextAt)[0];
  return next ? [...snoozed, next] : snoozed;
}

/** "8 minutes", "1 hour", "1 hour 30 minutes", "90 seconds": a timer's full time on its ringing screen. */
export function durationWords(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const part = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const out = [];
  if (h) out.push(part(h, 'hour'));
  if (m) out.push(part(m, 'minute'));
  if (sec) out.push(part(sec, 'second'));
  return out.join(' ') || '0 seconds';
}

/** Seconds left on a timer (hub clock `now`): a paused one keeps what it had. */
export function timerLeft(t, now) {
  if (t.state === 'paused') return Math.max(0, t.left || 0);
  if (t.state === 'ringing') return 0;
  return Math.max(0, t.endsAt - now);
}

/**
 * What rings now, first to ring first: [{kind: 'alarm' | 'timer', id, since, item}]. `local`: alarms the
 * wall rings itself because the hub didn't say (localRings).
 */
export function ringingNow(timers, alarms, local = []) {
  const out = [];
  for (const t of timers) if (t.state === 'ringing') out.push({ kind: 'timer', id: t.id, since: t.endsAt, item: t });
  for (const a of alarms) if (a.state === 'ringing') out.push({ kind: 'alarm', id: a.id, since: a.ringingSince || 0, item: a });
  for (const a of local) if (!out.some((x) => x.id === a.id)) out.push({ kind: 'alarm', id: a.id, since: a.nextAt, item: a, local: true });
  return out.sort((a, b) => a.since - b.since);
}

/** How long after an alarm's time the wall rings it itself when the hub hasn't said (it's away). */
export const LOCAL_AFTER_S = 30;

/**
 * Alarms the wall rings by itself (the hub unreachable at their time): due more than LOCAL_AFTER_S
 * ago, less than `ringFor` seconds ago, not rung, stopped or snoozed here already (`done`: id@nextAt).
 */
export function localRings(alarms, now, ringFor, done = new Set()) {
  return alarms.filter((a) => {
    const at = a.state === 'snoozed' ? a.snoozeUntil : a.enabled && a.state === 'set' ? a.nextAt : null;
    if (!at) return false;
    return now >= at + LOCAL_AFTER_S && now < at + ringFor && !done.has(`${a.id}@${at}`);
  });
}

/** The times the wall app keeps as a backup (seconds, hub clock): [{id, at, title}] of the coming alarms. */
export function alarmSchedule(alarms) {
  return alarms
    .map((a) => ({ id: a.id, at: a.state === 'snoozed' ? a.snoozeUntil : a.enabled && a.state === 'set' ? a.nextAt : null, title: alarmRingLabel(a) }))
    .filter((x) => x.at)
    .sort((a, b) => a.at - b.at);
}
