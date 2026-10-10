// The wall's clock, always on one line: "4:36" with a small "PM" where the tablet's locale uses a
// 12-hour clock, "16:36" where it uses 24 hours. Built from the hours and minutes (not the locale's
// own text) so en-CA's "p.m." and the like can't wrap under the time and crowd the page tabs.

function twelveHour() {
  try {
    const o = new Intl.DateTimeFormat([], { hour: 'numeric' }).resolvedOptions();
    if (o.hourCycle) return o.hourCycle === 'h11' || o.hourCycle === 'h12';
    return o.hour12 !== false;
  } catch {
    return true;
  }
}

/** { time: "4:36", period: "PM" } (12-hour locales) or { time: "16:36", period: "" }. */
export function clockParts(d) {
  const h = d.getHours();
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (twelveHour()) return { time: `${h % 12 || 12}:${mm}`, period: h < 12 ? 'AM' : 'PM' };
  return { time: `${String(h).padStart(2, '0')}:${mm}`, period: '' };
}

/** The top bar's time (the wall's and the layout editor's preview of it). */
export function WallTime({ date }) {
  const { time, period } = clockParts(date);
  return (
    <div className="wt-time">
      {time}
      {period ? <span className="wt-ampm">{period}</span> : null}
    </div>
  );
}

/** "Saturday" and "October 3" for beside the time. */
export function wallDay(d) {
  return { day: d.toLocaleDateString([], { weekday: 'long' }), date: d.toLocaleDateString([], { month: 'long', day: 'numeric' }) };
}
