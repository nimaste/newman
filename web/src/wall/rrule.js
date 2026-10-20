// Repeats for the wall's event form: the Repeats choices for a start date, a custom repeat ↔ its RRULE,
// and plain words for one ("Every week on Tuesday and Thursday, until Fri, Jun 25, 2027").

export const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const CODES = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const UNITS = { days: 'DAILY', weeks: 'WEEKLY', months: 'MONTHLY', years: 'YEARLY' };
const UNIT_OF = { DAILY: 'days', WEEKLY: 'weeks', MONTHLY: 'months', YEARLY: 'years' };

const pad = (n) => String(n).padStart(2, '0');
export const dateKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function ordinal(n) {
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ['th', 'st', 'nd', 'rd'][n % 10] || 'th';
  return n + s;
}

/** Which week of the month the day falls in: 1…4, or -1 for a last one. */
function weekOfMonth(d) {
  const n = Math.ceil(d.getDate() / 7);
  const next = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 7);
  return next.getMonth() !== d.getMonth() && n >= 4 ? -1 : n;
}

export function shortDate(d) {
  return `${WEEKDAYS[d.getDay()].slice(0, 3)}, ${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

/** The Repeats dropdown's fixed choices for an event starting on `start`: [{id, label, rule}]. */
export function presets(start) {
  const wd = WEEKDAYS[start.getDay()];
  const code = CODES[start.getDay()];
  const n = weekOfMonth(start);
  return [
    { id: 'never', label: 'Never', rule: '' },
    { id: 'day', label: 'Every day', rule: 'FREQ=DAILY' },
    { id: 'week', label: `Every week on ${wd}`, rule: `FREQ=WEEKLY;BYDAY=${code}` },
    { id: '2weeks', label: `Every 2 weeks on ${wd}`, rule: `FREQ=WEEKLY;INTERVAL=2;BYDAY=${code}` },
    { id: 'month', label: n === -1 ? `Every month on the last ${wd}` : `Every month on the ${ordinal(n)} ${wd}`, rule: `FREQ=MONTHLY;BYDAY=${n}${code}` },
    { id: 'year', label: `Every year on ${MONTHS[start.getMonth()]} ${start.getDate()}`, rule: 'FREQ=YEARLY' },
  ];
}

export function parseRule(rule) {
  const out = {};
  String(rule || '')
    .replace(/^RRULE:/i, '')
    .split(';')
    .forEach((part) => {
      const [k, v] = part.split('=');
      if (k && v !== undefined) out[k.toUpperCase()] = v;
    });
  return out;
}

/** The preset a rule is (for this start), else 'custom'. */
export function presetOf(rule, start) {
  const norm = (r) => {
    const p = parseRule(r);
    if (p.INTERVAL === '1') delete p.INTERVAL;
    return Object.keys(p)
      .sort()
      .map((k) => `${k}=${p[k]}`)
      .join(';');
  };
  const want = norm(rule);
  const hit = presets(start).find((p) => norm(p.rule) === want);
  return hit ? hit.id : 'custom';
}

/** A custom repeat {every, unit, days: [0…6], ends: 'never'|'date'|'count', until: Date, count} as an RRULE. */
export function customRule(c, allDay = false) {
  const parts = [`FREQ=${UNITS[c.unit] || 'WEEKLY'}`];
  const every = Math.max(1, Number(c.every) || 1);
  if (every > 1) parts.push(`INTERVAL=${every}`);
  if (c.unit === 'weeks' && c.days && c.days.length) parts.push('BYDAY=' + [...c.days].sort((a, b) => a - b).map((d) => CODES[d]).join(','));
  if (c.ends === 'date' && c.until) {
    // A date for all-day events; otherwise the end of that day here, in UTC (as the standard wants).
    const u = c.until;
    parts.push(allDay ? `UNTIL=${dateKey(u).replace(/-/g, '')}` : `UNTIL=${new Date(u.getFullYear(), u.getMonth(), u.getDate(), 23, 59, 59).toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '')}`);
  }
  if (c.ends === 'count') parts.push(`COUNT=${Math.max(1, Number(c.count) || 1)}`);
  return parts.join(';');
}

/** An RRULE back into the custom form (unknown parts are left at their defaults). */
export function customOf(rule, start) {
  const p = parseRule(rule);
  const unit = UNIT_OF[p.FREQ] || 'weeks';
  const days = p.BYDAY && unit === 'weeks' ? p.BYDAY.split(',').map((x) => CODES.indexOf(x.slice(-2))).filter((i) => i >= 0) : [start.getDay()];
  let ends = 'never';
  let until = null;
  if (p.UNTIL) {
    ends = 'date';
    const u = p.UNTIL;
    const [y, m, d] = [Number(u.slice(0, 4)), Number(u.slice(4, 6)) - 1, Number(u.slice(6, 8))];
    if (/Z$/i.test(u) && u.length >= 15) {
      const at = new Date(Date.UTC(y, m, d, Number(u.slice(9, 11)), Number(u.slice(11, 13)), Number(u.slice(13, 15))));
      until = new Date(at.getFullYear(), at.getMonth(), at.getDate());
    } else until = new Date(y, m, d);
  } else if (p.COUNT) ends = 'count';
  return { every: Number(p.INTERVAL) || 1, unit, days, ends, until, count: Number(p.COUNT) || 10 };
}

function andList(xs) {
  return xs.length <= 1 ? xs.join('') : xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1];
}

/** "Every week on Tuesday and Thursday, until Fri, Jun 25, 2027" */
export function describeCustom(c) {
  const every = Math.max(1, Number(c.every) || 1);
  const unitWord = every === 1 ? c.unit.replace(/s$/, '') : `${every} ${c.unit}`;
  let t = `Every ${unitWord}`;
  if (c.unit === 'weeks' && c.days && c.days.length) t += ' on ' + andList([...c.days].sort((a, b) => a - b).map((d) => WEEKDAYS[d]));
  if (c.ends === 'date' && c.until) t += `, until ${shortDate(c.until)}, ${c.until.getFullYear()}`;
  if (c.ends === 'count') t += `, ${Math.max(1, Number(c.count) || 1)} times`;
  return t;
}

/** Short words for any rule, for the dropdown's Custom line: "every week on Tue, Thu". */
export function shortWords(rule, start) {
  const c = customOf(rule, start);
  const every = c.every === 1 ? c.unit.replace(/s$/, '') : `${c.every} ${c.unit}`;
  const on = c.unit === 'weeks' && c.days.length ? ' on ' + [...c.days].sort((a, b) => a - b).map((d) => WEEKDAYS[d].slice(0, 3)).join(', ') : '';
  return `every ${every}${on}`;
}
