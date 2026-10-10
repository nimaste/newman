// The wall's full-screen calendar (#/wall/calendar, approved TabletCalendar board): the next 31 days
// as a grid from this week's Monday, the chosen day's events on the right, or everything coming up.
// Data as the Calendar tile: calendar devices' attrs.upcoming / attrs.colour (only `cals` if given).

import { useEffect, useMemo, useState } from 'react';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';

const DAYS_AHEAD = 31;
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const pad = (n) => String(n).padStart(2, '0');
export const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const parseKey = (k) => {
  const [y, m, d] = String(k).split('-').map(Number);
  return y && m && d ? new Date(y, m - 1, d) : null;
};
const longDay = (d) => d.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
const clock = (d) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

function useNow(ms) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** "All day", "9:30 AM – 10:30 AM", "From 9:00 PM" (runs past midnight). */
function whenText(ev) {
  if (ev.allDay) return 'All day';
  if (dayKey(ev.e) !== dayKey(ev.s) && ev.e - ev.s > 60e3) return 'From ' + clock(ev.s);
  return ev.e - ev.s > 60e3 ? `${clock(ev.s)} – ${clock(ev.e)}` : clock(ev.s);
}

/** Calendar devices → their events in the window, each with colour and calendar name. */
function useEvents(cals, from, until) {
  const { entities } = useHub();
  return useMemo(() => {
    const ids = cals || [];
    const list = (entities || []).filter((e) => e.attrs && Array.isArray(e.attrs.upcoming)).filter((e) => !ids.length || ids.includes(e.id));
    const events = [];
    list.forEach((c) =>
      c.attrs.upcoming.forEach((ev, i) => {
        const s = new Date(ev.start);
        const e = new Date(ev.end || ev.start);
        if (Number.isNaN(s.getTime()) || e <= from || s >= until) return;
        events.push({ ...ev, key: c.id + ':' + i, s, e, colour: c.attrs.colour || '#3CCFB4', cal: c.name, calId: c.id });
      }),
    );
    events.sort((a, b) => (a.allDay === b.allDay ? a.s - b.s : a.allDay ? -1 : 1));
    return { calendars: list, events };
  }, [entities, cals, from, until]);
}

/** The events covering a day (multi-day ones show on each day they cover), all-day first. */
function eventsOn(events, day) {
  const start = day.getTime();
  const end = addDays(day, 1).getTime();
  return events.filter((ev) => ev.s.getTime() < end && (ev.e.getTime() > start || (ev.e.getTime() === ev.s.getTime() && ev.s.getTime() >= start)));
}

function EventRow({ ev, now }) {
  const on = !ev.allDay && ev.s.getTime() <= now && now < ev.e.getTime();
  return (
    <div className={'wcal-ev' + (on ? ' is-now' : '')}>
      <span className="wcal-ev-bar" style={{ background: ev.colour }} />
      <span className="wcal-ev-text">
        <span className="wcal-ev-title">
          {on ? <span className="cal-now">Now</span> : null}
          {ev.title}
        </span>
        <span className="wcal-ev-sub">
          {whenText(ev)} · {ev.cal}
        </span>
        {ev.location ? <span className="wcal-ev-sub">{ev.location}</span> : null}
      </span>
    </div>
  );
}

export default function CalendarView({ day, cals, onBack }) {
  const now = useNow(60000);
  const today = useMemo(() => {
    const d = new Date(now);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }, [Math.floor(now / 3600e3)]); // eslint-disable-line react-hooks/exhaustive-deps
  const last = addDays(today, DAYS_AHEAD - 1);
  const until = addDays(today, DAYS_AHEAD);
  const start = addDays(today, -((today.getDay() + 6) % 7)); // this week's Monday
  const rows = Math.ceil((Math.round((until - start) / 86400e3)) / 7); // 5, or 6 when today is a Sunday
  const { calendars, events } = useEvents(cals, today, until);

  // Opening without ?day: today, or the first day with events if today is empty.
  const firstBusy = () => {
    for (let i = 0; i < DAYS_AHEAD; i += 1) if (eventsOn(events, addDays(today, i)).length) return dayKey(addDays(today, i));
    return dayKey(today);
  };
  const asked = parseKey(day);
  const initial = asked && asked >= today && asked <= last ? dayKey(asked) : eventsOn(events, today).length ? dayKey(today) : firstBusy();
  const [sel, setSel] = useState(initial);
  const [all, setAll] = useState(false);

  const cells = Array.from({ length: rows * 7 }, (_, i) => addDays(start, i));
  const selDate = parseKey(sel) || today;
  const dayEvents = eventsOn(events, selDate);
  // Everything: each event once, under the day it starts (or today, if it started before).
  const upcoming = events.map((ev) => ({ ev, on: ev.s < today ? today : new Date(ev.s.getFullYear(), ev.s.getMonth(), ev.s.getDate()) })).sort((a, b) => a.on - b.on || (a.ev.allDay === b.ev.allDay ? a.ev.s - b.ev.s : a.ev.allDay ? -1 : 1));
  const n = Math.round((selDate - today) / 86400e3);
  const rel = n === 0 ? 'Today' : n === 1 ? 'Tomorrow' : `In ${n} days`;
  let lastHeading = null;

  return (
    <div className="wcal">
      <div className="wcal-main">
        <div className="wt-cams-head wcal-head">
          <button type="button" className="wt-back" onClick={onBack}>
            <Icon name="chevronLeft" size={26} strokeWidth={2.4} /> Back
          </button>
          <span className="wt-cams-title">Next 31 days</span>
          <span className="wt-dim-text">Tap a day to see it</span>
          <span className="wcal-gap" />
          {calendars.length > 1 ? (
            <span className="wcal-key">
              {calendars.map((c) => (
                <span key={c.id} className="wcal-key-item">
                  <span className="wcal-key-sq" style={{ background: c.attrs.colour || '#3CCFB4' }} />
                  {c.name}
                </span>
              ))}
            </span>
          ) : null}
        </div>
        <div className="wcal-week">
          {WEEKDAYS.map((w) => (
            <span key={w}>{w}</span>
          ))}
        </div>
        <div className="wcal-grid" style={{ gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>
          {cells.map((d) => {
            const k = dayKey(d);
            const off = d < today || d > last;
            const evs = off ? [] : eventsOn(events, d);
            const isToday = k === dayKey(today);
            const selected = !all && k === sel;
            return (
              <button
                key={k}
                type="button"
                className={'wcal-day' + (off ? ' is-off' : '') + (isToday ? ' is-today' : '') + (selected ? ' is-sel' : '') + (evs.length > 2 ? ' has-more' : '')}
                disabled={off}
                aria-pressed={off ? undefined : selected}
                aria-label={`${longDay(d)}${off ? '' : evs.length ? `, ${evs.length} ${evs.length === 1 ? 'event' : 'events'}` : ', nothing on'}`}
                onClick={() => {
                  setSel(k);
                  setAll(false);
                }}
              >
                <span className="wcal-num">
                  <span className="wcal-n">{d.getDate()}</span>
                  {isToday ? <span className="wcal-sub is-today">Today</span> : d.getDate() === 1 ? <span className="wcal-sub">{d.toLocaleDateString([], { month: 'short' })}</span> : null}
                </span>
                {evs.slice(0, 2).map((ev) => (
                  <span key={ev.key} className="wcal-line">
                    <span className="wcal-line-bar" style={{ background: ev.colour }} />
                    <span className="wcal-line-title">{ev.title}</span>
                  </span>
                ))}
                {evs.length > 2 ? <span className="wcal-more">+{evs.length - 2}</span> : null}
              </button>
            );
          })}
        </div>
      </div>

      <aside className="wcal-side" aria-label="Events">
        <div className="wcal-side-head">
          <span className="wcal-side-title">{all ? 'Everything coming up' : longDay(selDate)}</span>
          <span className="wcal-side-sub">
            {all ? `${upcoming.length} ${upcoming.length === 1 ? 'event' : 'events'} in the next 31 days` : `${rel} · ${dayEvents.length === 1 ? '1 event' : dayEvents.length + ' events'}`}
          </span>
        </div>
        <div className="wcal-toggle" role="group" aria-label="Which events">
          <button type="button" aria-pressed={!all} onClick={() => setAll(false)}>
            This day
          </button>
          <button type="button" aria-pressed={all} onClick={() => setAll(true)}>
            Everything
          </button>
        </div>
        <div className="wcal-list">
          {all ? (
            !upcoming.length ? (
              <div className="wcal-empty">Nothing in the next 31 days.</div>
            ) : (
              upcoming.map(({ ev, on }) => {
                const k = dayKey(on);
                const heading = k !== lastHeading ? longDay(on) : null;
                lastHeading = k;
                return (
                  <div key={ev.key} className="wcal-item">
                    {heading ? <span className="wcal-heading">{heading}</span> : null}
                    <EventRow ev={ev} now={now} />
                  </div>
                );
              })
            )
          ) : !dayEvents.length ? (
            <div className="wcal-empty">Nothing on this day.</div>
          ) : (
            dayEvents.map((ev) => (
              <div key={ev.key} className="wcal-item">
                <EventRow ev={ev} now={now} />
              </div>
            ))
          )}
        </div>
      </aside>
    </div>
  );
}
