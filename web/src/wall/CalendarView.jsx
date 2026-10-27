// The wall's full-screen calendar (#/wall/calendar, approved TabletCalendar board): a month, Sunday to
// Saturday (‹ › or a swipe for the month before / after, Today back to this one; days of the months either
// side dimmed), the chosen day's events on the right, or everything coming up; + New ▾ (Event / Task) on
// calendars Newman may change. GCloud tasks (CalendarTasks.jsx) show on their days with a tick box, and in
// the day's list with Done. Data as the Calendar tile: calendar devices' attrs.upcoming / attrs.colour (only `cals`
// if given) while the month is inside what upcoming holds (attrs.upcomingUntil), else the hub's
// GET api/calendars/{id}/events?from=&to= for the weeks shown - asked again when the calendar was read
// again (attrs.fetchedAt); a calendar that can't answer it shows what upcoming has.

import { useEffect, useMemo, useRef, useState } from 'react';
import { api, enc } from '../api.js';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { useCalendarSheets, writableCalendars } from './CalendarEvents.jsx';
import { TaskBox, markTaskDone, taskState, tasksOn, useTasks } from './CalendarTasks.jsx';

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const SWIPE_PX = 60; // further than this sideways (and more sideways than down) moves a month

const pad = (n) => String(n).padStart(2, '0');
export const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const monthOf = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
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

/** A calendar's events (the hub's shape) → the screen's, with colour and calendar name, in [from, until). */
function shape(c, list, tag, from, until) {
  const out = [];
  list.forEach((ev, i) => {
    const s = new Date(ev.start);
    const e = new Date(ev.end || ev.start);
    if (Number.isNaN(s.getTime()) || e <= from || s >= until) return;
    out.push({ ...ev, key: c.id + tag + i, s, e, colour: c.attrs.colour || '#3CCFB4', cal: c.name, calId: c.id, writable: c.attrs.readOnly === false });
  });
  return out;
}

const order = (a, b) => (a.allDay === b.allDay ? a.s - b.s : a.allDay ? -1 : 1);

/** The calendars, everything coming up (from `today`), and the events of the weeks shown [from, until). */
function useEvents(cals, today, from, until, now) {
  const { entities } = useHub();
  const calendars = useMemo(() => {
    const ids = cals || [];
    return (entities || []).filter((e) => e.attrs && Array.isArray(e.attrs.upcoming)).filter((e) => !ids.length || ids.includes(e.id));
  }, [entities, cals]);
  const [fetched, setFetched] = useState({}); // calendar id -> {sig, events} for the weeks shown
  const cannot = useRef(new Set()); // calendars whose connection can't show other days
  // Inside what upcoming holds: after now (it has nothing that's over), before upcomingUntil.
  const inside = (c) => from.getTime() > now && !!c.attrs.upcomingUntil && until <= new Date(c.attrs.upcomingUntil);
  const span = `${dayKey(from)}|${dayKey(until)}`;
  const sigOf = (c) => `${span}|${c.attrs.fetchedAt || ''}|${c.attrs.upcoming.length}`;
  const wanted = calendars.filter((c) => !inside(c) && !cannot.current.has(c.id));
  const want = wanted.map((c) => c.id + '=' + sigOf(c)).join(',');

  useEffect(() => {
    let gone = false;
    wanted.forEach((c) => {
      const sig = sigOf(c);
      if (fetched[c.id] && fetched[c.id].sig === sig) return;
      api
        .get(`api/calendars/${enc(c.id)}/events?from=${dayKey(from)}&to=${dayKey(until)}`, { quiet: true })
        .then((r) => !gone && setFetched((f) => ({ ...f, [c.id]: { sig, events: Array.isArray(r.events) ? r.events : [] } })))
        .catch((e) => {
          if (e && e.status === 404) cannot.current.add(c.id);
        });
    });
    return () => {
      gone = true;
    };
  }, [want]); // eslint-disable-line react-hooks/exhaustive-deps

  return useMemo(() => {
    const upcoming = [];
    const shown = [];
    calendars.forEach((c) => {
      upcoming.push(...shape(c, c.attrs.upcoming, ':', today, new Date(8.64e15)));
      const got = fetched[c.id];
      // Until the hub answers (or when it can't): what upcoming has for these weeks.
      const list = !inside(c) && got && got.sig === sigOf(c) ? got.events : c.attrs.upcoming;
      shown.push(...shape(c, list, list === c.attrs.upcoming ? ':' : ':m', from, until));
    });
    upcoming.sort(order);
    shown.sort(order);
    return { calendars, upcoming, events: shown };
  }, [calendars, fetched, today, from, until]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** The events covering a day (multi-day ones show on each day they cover), all-day first. */
function eventsOn(events, day) {
  const start = day.getTime();
  const end = addDays(day, 1).getTime();
  return events.filter((ev) => ev.s.getTime() < end && (ev.e.getTime() > start || (ev.e.getTime() === ev.s.getTime() && ev.s.getTime() >= start)));
}

/** A task in the day's list: its tick box, "Task · Home · Chris", and Done. */
function TaskRow({ item, today, onOpen, onDone }) {
  const t = item.task;
  const state = item.done ? 'done' : taskState(t, today);
  return (
    <div className={'wcal-ev wcal-task' + (state === 'done' ? ' is-done' : '')}>
      <button type="button" className="wcal-task-open" onClick={() => onOpen(t)}>
        <TaskBox task={t} state={state} size="row" />
        <span className="wcal-ev-text">
          <span className="wcal-ev-title">{t.title}</span>
          <span className="wcal-ev-sub">
            Task · {t.calendar} · {item.done && item.who ? item.who : t.owner || 'Everyone'}
          </span>
        </span>
      </button>
      {state !== 'done' && t.writable ? (
        <button type="button" className="wcal-task-done" onClick={() => onDone(t)}>
          Done
        </button>
      ) : null}
    </div>
  );
}

function EventRow({ ev, now, onOpen }) {
  const on = !ev.allDay && ev.s.getTime() <= now && now < ev.e.getTime();
  return (
    <button type="button" className={'wcal-ev' + (on ? ' is-now' : '')} onClick={() => onOpen(ev)}>
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
    </button>
  );
}

export default function CalendarView({ day, cals, onBack }) {
  const now = useNow(60000);
  const sheets = useCalendarSheets();
  const today = useMemo(() => {
    const d = new Date(now);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }, [Math.floor(now / 3600e3)]); // eslint-disable-line react-hooks/exhaustive-deps
  const asked = parseKey(day);
  const [sel, setSel] = useState(() => dayKey(asked || today));
  const [view, setView] = useState(() => monthOf(asked || today));
  const [all, setAll] = useState(false);

  const start = addDays(view, -view.getDay()); // the Sunday on or before the 1st
  const inMonth = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
  const rows = Math.ceil((view.getDay() + inMonth) / 7); // 5 or 6 (4 for a February from a Sunday)
  const until = addDays(start, rows * 7);
  const range = useMemo(() => [start, until], [dayKey(start), rows]); // eslint-disable-line react-hooks/exhaustive-deps
  const { calendars, upcoming: coming, events } = useEvents(cals, today, range[0], range[1], now);
  const tasks = useTasks();
  // A short screen's day cells have room for one line (Show5Calendar board), the big tablet's for two.
  const maxLines = typeof document !== 'undefined' && document.querySelector('.wall.wall--short') ? 1 : 2;
  const canEvent = writableCalendars(calendars).length > 0;
  const canTask = tasks.calendars.some((c) => c.writable);
  const [menu, setMenu] = useState(false);
  const taskCtx = { calendars: tasks.calendars, people: tasks.people, today, onChanged: tasks.reload };
  const openTask = (t) => sheets.openTask(t, taskCtx);
  const doneTask = async (t) => {
    if (t.askWho && tasks.people.length) sheets.openTask(t, taskCtx, true);
    else if (await markTaskDone(t, null)) tasks.reload();
  };

  // Opening without ?day on a month with nothing today: the first day this month with events coming up.
  const looked = useRef(!!asked);
  useEffect(() => {
    if (looked.current || !calendars.length) return;
    looked.current = true;
    if (eventsOn(coming, today).length) return;
    const next = coming.find((ev) => ev.s > today && ev.s.getMonth() === today.getMonth() && ev.s.getFullYear() === today.getFullYear());
    if (next) setSel(dayKey(new Date(next.s.getFullYear(), next.s.getMonth(), next.s.getDate())));
  }, [calendars.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // Another month: today when it's this month, else its 1st.
  const toMonth = (m) => {
    setView(m);
    setSel(dayKey(m.getFullYear() === today.getFullYear() && m.getMonth() === today.getMonth() ? today : m));
    setAll(false);
  };
  const step = (n) => toMonth(new Date(view.getFullYear(), view.getMonth() + n, 1));
  const pick = (d) => {
    setSel(dayKey(d));
    setAll(false);
    if (d.getMonth() !== view.getMonth()) setView(monthOf(d));
  };

  // Swiping the grid left / right: the next / previous month (the tap that ends a swipe picks no day).
  const swipe = useRef(null);
  const swiped = useRef(false);
  const onPointerDown = (e) => {
    swipe.current = { x: e.clientX, y: e.clientY };
    swiped.current = false;
  };
  const onPointerUp = (e) => {
    const s = swipe.current;
    swipe.current = null;
    if (!s) return;
    const dx = e.clientX - s.x;
    const dy = e.clientY - s.y;
    if (Math.abs(dx) > SWIPE_PX && Math.abs(dx) > Math.abs(dy) * 1.5) {
      swiped.current = true;
      step(dx < 0 ? 1 : -1);
    }
  };

  const cells = Array.from({ length: rows * 7 }, (_, i) => addDays(start, i));
  const selDate = parseKey(sel) || today;
  const dayEvents = eventsOn(events, selDate);
  const dayTasks = tasksOn(tasks.tasks, selDate);
  // Everything: open tasks in the next 31 days (overdue ones under today) with the events.
  const horizon = addDays(today, 31);
  const comingTasks = tasks.tasks
    .filter((t) => !t.done && t.due && parseKey(t.due) < horizon)
    .map((t) => ({ t, on: parseKey(t.due) < today ? today : parseKey(t.due) }));
  // Everything: each event once, under the day it starts (or today, if it started before).
  const upcoming = [
    ...comingTasks.map(({ t, on }) => ({ task: t, on })),
    ...coming.map((ev) => ({ ev, on: ev.s < today ? today : new Date(ev.s.getFullYear(), ev.s.getMonth(), ev.s.getDate()) })),
  ].sort((a, b) => a.on - b.on || (a.task ? -1 : b.task ? 1 : order(a.ev, b.ev)));
  const nEvents = (n) => `${n} ${n === 1 ? 'event' : 'events'}`;
  const nTasks = (n) => `${n} ${n === 1 ? 'task' : 'tasks'}`;
  const n = Math.round((selDate - today) / 86400e3);
  const rel = n === 0 ? 'Today' : n === 1 ? 'Tomorrow' : n === -1 ? 'Yesterday' : n < 0 ? `${-n} days ago` : `In ${n} days`;
  let lastHeading = null;

  return (
    <div className="wcal wcal--month">
      <div className="wcal-main">
        <div className="wt-cams-head wcal-head">
          <button type="button" className="wt-back" onClick={onBack}>
            <Icon name="chevronLeft" size={26} strokeWidth={2.4} /> Back
          </button>
          <span className="wcal-gap" />
          <button type="button" className="wcal-round" aria-label="Previous month" onClick={() => step(-1)}>
            <Icon name="chevronLeft" size={28} strokeWidth={2.6} />
          </button>
          <span className="wt-cams-title wcal-month">{view.toLocaleDateString([], { month: 'long', year: 'numeric' })}</span>
          <button type="button" className="wcal-round" aria-label="Next month" onClick={() => step(1)}>
            <Icon name="chevronRight" size={28} strokeWidth={2.6} />
          </button>
          <button type="button" className="wcal-today" onClick={() => toMonth(monthOf(today))}>
            Today
          </button>
          <span className="wcal-gap" />
          {canEvent || canTask ? (
            <span className="wcal-new">
              <button type="button" className="wcal-add" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>
                <Icon name="plus" size={22} strokeWidth={2.6} /> New <span className="wcal-caret">▾</span>
              </button>
              {menu ? (
                <>
                  <span className="wcal-menu-shade" onClick={() => setMenu(false)} />
                  <span className="wcal-menu" role="menu">
                    {canEvent ? (
                      <button type="button" role="menuitem" onClick={() => (setMenu(false), sheets.newEvent(selDate))}>
                        <svg viewBox="0 0 24 24" fill="none" strokeWidth="2.2" aria-hidden="true">
                          <rect x="3" y="5" width="18" height="16" rx="2" />
                          <path d="M3 10h18M8 3v4M16 3v4" />
                        </svg>
                        Event
                      </button>
                    ) : null}
                    {canTask ? (
                      <button type="button" role="menuitem" onClick={() => (setMenu(false), sheets.newTask(selDate, taskCtx))}>
                        <svg viewBox="0 0 24 24" fill="none" strokeWidth="2.2" aria-hidden="true">
                          <rect x="4" y="4" width="16" height="16" rx="3" />
                          <path d="m8.5 12 2.5 2.5 4.5-5" />
                        </svg>
                        Task
                      </button>
                    ) : null}
                  </span>
                </>
              ) : null}
            </span>
          ) : null}
        </div>
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
        <div className="wcal-week">
          {WEEKDAYS.map((w) => (
            <span key={w}>{w}</span>
          ))}
        </div>
        <div
          className="wcal-grid"
          style={{ gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          onPointerCancel={() => (swipe.current = null)}
          onClickCapture={(e) => {
            if (swiped.current) {
              swiped.current = false;
              e.stopPropagation();
            }
          }}
        >
          {cells.map((d) => {
            const k = dayKey(d);
            const out = d.getMonth() !== view.getMonth();
            const evs = eventsOn(events, d);
            const tks = tasksOn(tasks.tasks, d);
            const lines = [...tks.map((x) => ({ kind: 'task', x })), ...evs.map((ev) => ({ kind: 'event', ev }))];
            const isToday = k === dayKey(today);
            const selected = !all && k === sel;
            return (
              <button
                key={k}
                type="button"
                className={'wcal-day' + (out ? ' is-out' : '') + (isToday ? ' is-today' : '') + (selected ? ' is-sel' : '') + (lines.length > maxLines ? ' has-more' : '')}
                aria-pressed={selected}
                aria-label={`${longDay(d)}${lines.length ? `, ${[evs.length ? nEvents(evs.length) : '', tks.length ? nTasks(tks.length) : ''].filter(Boolean).join(', ')}` : ', nothing on'}`}
                onClick={() => pick(d)}
              >
                <span className="wcal-num">
                  <span className="wcal-n">{d.getDate()}</span>
                  {isToday ? <span className="wcal-sub is-today">Today</span> : d.getDate() === 1 ? <span className="wcal-sub">{d.toLocaleDateString([], { month: 'short' })}</span> : null}
                </span>
                {lines.slice(0, maxLines).map((l) =>
                  l.kind === 'event' ? (
                    <span key={l.ev.key} className="wcal-line">
                      <span className="wcal-line-bar" style={{ background: l.ev.colour }} />
                      <span className="wcal-line-title">{l.ev.title}</span>
                    </span>
                  ) : (
                    <span key={l.x.key} className={'wcal-line wcal-line--task is-' + (l.x.done ? 'done' : taskState(l.x.task, today))}>
                      <TaskBox task={l.x.task} state={l.x.done ? 'done' : taskState(l.x.task, today)} />
                      <span className="wcal-line-title">{l.x.task.title}</span>
                    </span>
                  ),
                )}
                {lines.length > maxLines ? <span className="wcal-more">+{lines.length - maxLines}</span> : null}
              </button>
            );
          })}
        </div>
      </div>

      <aside className="wcal-side" aria-label="Events">
        <div className="wcal-side-head">
          <span className="wcal-side-title">{all ? 'Everything coming up' : longDay(selDate)}</span>
          <span className="wcal-side-sub">
            {all
              ? `${nEvents(upcoming.filter((u) => u.ev).length)} · ${nTasks(comingTasks.length)} in the next 31 days`
              : `${rel} · ${nEvents(dayEvents.length)} · ${nTasks(dayTasks.length)}`}
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
              upcoming.map(({ ev, task, on }) => {
                const k = dayKey(on);
                const heading = k !== lastHeading ? longDay(on) : null;
                lastHeading = k;
                return (
                  <div key={ev ? ev.key : task.id} className="wcal-item">
                    {heading ? <span className="wcal-heading">{heading}</span> : null}
                    {ev ? <EventRow ev={ev} now={now} onOpen={sheets.openEvent} /> : <TaskRow item={{ task, done: false }} today={today} onOpen={openTask} onDone={doneTask} />}
                  </div>
                );
              })
            )
          ) : !dayEvents.length && !dayTasks.length ? (
            <div className="wcal-empty">Nothing on this day.</div>
          ) : (
            <>
              {dayTasks.map((x) => (
                <div key={x.key} className="wcal-item">
                  <TaskRow item={x} today={today} onOpen={openTask} onDone={doneTask} />
                </div>
              ))}
              {dayEvents.map((ev) => (
                <div key={ev.key} className="wcal-item">
                  <EventRow ev={ev} now={now} onOpen={sheets.openEvent} />
                </div>
              ))}
            </>
          )}
        </div>
      </aside>
      {sheets.ui}
    </div>
  );
}
