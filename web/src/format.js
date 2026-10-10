// Small presentation helpers. Times from the API are Unix seconds.

export const DAY_LETTERS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
export const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

export function joinList(arr, word = 'and') {
  const a = arr.filter(Boolean);
  if (a.length <= 1) return a[0] || '';
  return a.slice(0, -1).join(', ') + ' ' + word + ' ' + a[a.length - 1];
}

/** "21:00" -> "9:00 PM" */
export function fmtHHMM(hhmm) {
  if (!hhmm || !/^\d{1,2}:\d{2}/.test(hhmm)) return hhmm || '';
  const [h, m] = hhmm.split(':').map(Number);
  const suffix = h >= 12 ? 'PM' : 'AM';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${String(m).padStart(2, '0')} ${suffix}`;
}

function toDate(ts) {
  if (ts === null || ts === undefined || ts === '') return null;
  if (typeof ts === 'string' && !/^\d+(\.\d+)?$/.test(ts)) {
    const d = new Date(ts);
    return isNaN(d) ? null : d;
  }
  return new Date(Number(ts) * 1000);
}

export function fmtClock(ts) {
  const d = toDate(ts);
  if (!d) return '';
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function dayDiff(d) {
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const now = new Date();
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((a - b) / 86400000);
}

/** Past timestamps: "8:42 PM", "Yesterday 8:42 PM", "Mon 8:42 PM", "Sep 3" */
export function fmtWhen(ts) {
  const d = toDate(ts);
  if (!d) return '';
  const diff = dayDiff(d);
  const clock = fmtClock(ts);
  if (diff === 0) return clock;
  if (diff === -1) return 'Yesterday ' + clock;
  if (diff > -7 && diff < 0) return d.toLocaleDateString([], { weekday: 'short' }) + ' ' + clock;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

/** Future timestamps: "Today 9:00 PM", "Tomorrow 9:00 PM", "Wed 9:00 PM" */
export function fmtNext(ts) {
  const d = toDate(ts);
  if (!d) return '';
  const diff = dayDiff(d);
  const clock = fmtClock(ts);
  if (diff === 0) return clock;
  if (diff === 1) return 'Tomorrow ' + clock;
  if (diff > 1 && diff < 7) return d.toLocaleDateString([], { weekday: 'short' }) + ' ' + clock;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + clock;
}

/** Heading for grouping activity: "Today", "Yesterday", "Monday, Sep 22" */
export function fmtDay(ts) {
  const d = toDate(ts);
  if (!d) return '';
  const diff = dayDiff(d);
  if (diff === 0) return 'Today';
  if (diff === -1) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
}

export function fmtUptime(seconds) {
  const s = Number(seconds) || 0;
  if (s < 90) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60);
  if (m < 90) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h`;
  return `${Math.floor(h / 24)} days`;
}

export function daysText(days) {
  const on = [...new Set(days || [])].sort();
  if (on.length === 7) return 'every day';
  if (on.join() === '0,1,2,3,4') return 'every weekday';
  if (on.join() === '5,6') return 'every weekend';
  if (on.length === 0) return 'on no days';
  return 'every ' + joinList(on.map((i) => DAY_NAMES[i]), 'and');
}

export function greeting() {
  const h = new Date().getHours();
  if (h < 5) return 'Good night';
  if (h < 12) return 'Good morning';
  if (h < 18) return 'Good afternoon';
  return 'Good evening';
}

export function initial(name) {
  return (name || '?').trim().charAt(0).toUpperCase() || '?';
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || one + 's'}`;
}

export function platformLabel(p) {
  return { ios: 'iPhone', android: 'Android', test: 'Test phone' }[p] || p || 'Phone';
}

/** 420 -> "420 m", 2140 -> "2.1 km", 18400 -> "18 km" */
export function fmtDistance(m) {
  if (m === null || m === undefined) return '';
  if (m < 950) return `${Math.max(10, Math.round(m / 10) * 10)} m`;
  return m < 9950 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m / 1000)} km`;
}

/** Where someone is, the way the phone app says it: "Home", "School", "2.1 km from home". */
export function whereText(p) {
  if (!p || !p.sharing) return 'Not sharing';
  if (p.lat === null || p.lat === undefined) return 'No location yet';
  if (p.home) return 'Home';
  if (p.place) return p.place;
  return p.distanceM !== null && p.distanceM !== undefined ? `${fmtDistance(p.distanceM)} from home` : 'Away';
}

/** "just now", "4 min ago", "2 h ago", else the time. */
export function fmtAgo(ts) {
  const s = Date.now() / 1000 - Number(ts);
  if (!ts || !isFinite(s)) return '';
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 6 * 3600) return `${Math.floor(s / 3600)} h ago`;
  return fmtWhen(ts);
}

// A webhook's incoming address carries its secret: /api/public/webhooks/<entity id>/<secret>.
const HOOK_RE = /(\/api\/public\/webhooks\/[^/\s·]+\/)([^\s·]+)/g;

/** A connection's status text without webhook addresses (their secret is a key), safe to show anyone. */
export function cleanDetail(text) {
  if (!text) return text;
  return String(text)
    .replace(/:\s*\/api\/public\/webhooks\/[^\s·]+/g, '')
    .replace(HOOK_RE, '$1••••');
}

