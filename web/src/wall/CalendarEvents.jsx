// Calendar events on the wall (approved WallEvent / WallAdd / WallReminder boards): tapping an event
// opens its card (when, where, notes, a QR code for its link; Edit on calendars Newman may change);
// + on the calendar tile opens the new-event form - What (required), Calendar, All day, Starts / Ends as
// big date and time buttons (a month calendar; hour and minute wheels with AM / PM), Repeats (incl.
// Custom…) - which won't save without a name or with the end before the start, moves the end with the
// start, and closes on Save. Writes go through the hub (POST/PUT api/calendars/{id}/events) to the
// calendar server. Reminder cards: {"type": "calendarReminder"} on the live socket, OK on any tablet
// clears them all.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, enc, toast } from '../api.js';
import { useHub } from '../hooks.js';
import { useLive } from '../live.js';
import { QrCode } from '../components/ConnectDevice.jsx';
import { playSound, unlockAudioOnFirstTouch } from './sounds.js';
import { WEEKDAYS, customOf, customRule, dateKey, describeCustom, presetOf, presets, shortDate, shortWords } from './rrule.js';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const ITEM = 88; // a wheel row
const pad = (n) => String(n).padStart(2, '0');

/** Calendars the wall may add to and change (a calendar server that allows it). */
export const writableCalendars = (entities) =>
  (entities || []).filter((e) => e.attrs && Array.isArray(e.attrs.upcoming) && e.attrs.readOnly === false && e.available !== false);

function clock(d) {
  const h = d.getHours();
  return `${h % 12 || 12}:${pad(d.getMinutes())} ${h < 12 ? 'AM' : 'PM'}`;
}
const sameDay = (a, b) => dateKey(a) === dateKey(b);
const atMidnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n, d.getHours(), d.getMinutes());
const daysBetween = (a, b) => Math.round((atMidnight(b) - atMidnight(a)) / 864e5);

function parseLocal(s, allDay) {
  const [d, t] = String(s || '').split('T');
  const [y, m, day] = d.split('-').map(Number);
  if (allDay || !t) return new Date(y, m - 1, day);
  const [h, mi] = t.split(':').map(Number);
  return new Date(y, m - 1, day, h, mi);
}
const localText = (d, allDay) => (allDay ? dateKey(d) : `${dateKey(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`);

/** "Friday, October 23 · 6:00 – 7:00 PM" for the card. */
function whenLong(ev) {
  const s = new Date(ev.start);
  const e = new Date(ev.end || ev.start);
  const day = (d) => `${WEEKDAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}`;
  if (ev.allDay) {
    const last = new Date(e.getTime() - 864e5);
    return sameDay(s, last) ? `${day(s)} · All day` : `${day(s)} – ${day(last)} · All day`;
  }
  if (!sameDay(s, e)) return `${day(s)} ${clock(s)} – ${day(e)} ${clock(e)}`;
  return `${day(s)} · ${clock(s)} – ${clock(e)}`;
}

/** The overlays a calendar tile (or the full calendar) opens: {openEvent(ev), newEvent(), ui}. */
export function useCalendarSheets() {
  const [sheet, setSheet] = useState(null);
  // React passes clicks inside a portal up to the tile that opened it (whose own tap opens the calendar
  // screen): keep them in here.
  const stop = (e) => e.stopPropagation();
  const ui = sheet
    ? createPortal(
        <div className="ce-portal" onClick={stop} onPointerDown={stop} onPointerUp={stop} onKeyDown={stop}>
          <Sheets sheet={sheet} setSheet={setSheet} />
        </div>,
        document.body,
      )
    : null;
  return { openEvent: (ev) => setSheet({ kind: 'event', ev }), newEvent: () => setSheet({ kind: 'form' }), ui };
}

function Sheets({ sheet, setSheet }) {
  const close = () => setSheet(null);
  if (sheet.kind === 'event') {
    return (
      <EventCard
        ev={sheet.ev}
        onClose={close}
        onEdit={() => (sheet.ev.recurring ? setSheet({ kind: 'scope', ev: sheet.ev }) : setSheet({ kind: 'form', ev: sheet.ev, scope: 'all' }))}
      />
    );
  }
  if (sheet.kind === 'scope') {
    return (
      <div className="wt-overlay" role="dialog" aria-modal="true" aria-label="Change which?" onClick={(e) => e.target === e.currentTarget && close()}>
        <div className="wt-confirm">
          <div className="wt-sheet-title">Change “{sheet.ev.title}”</div>
          <div className="wt-sheet-sub">It repeats. Change just this time, or every time?</div>
          <div className="wt-confirm-btns">
            <button type="button" className="wt-confirm-btn" onClick={() => setSheet({ kind: 'form', ev: sheet.ev, scope: 'this' })}>
              This event
            </button>
            <button type="button" className="wt-confirm-btn is-ok" onClick={() => setSheet({ kind: 'form', ev: sheet.ev, scope: 'all' })}>
              All events
            </button>
          </div>
          <button type="button" className="wt-confirm-btn" onClick={close}>
            Cancel
          </button>
        </div>
      </div>
    );
  }
  return <EventForm ev={sheet.ev} scope={sheet.scope || 'all'} onClose={close} />;
}

/* ---------------- the card ---------------- */

export function EventCard({ ev, onClose, onEdit }) {
  return (
    <div className="wt-overlay ce-shade" role="dialog" aria-modal="true" aria-label={ev.title} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="ce-card">
        <div className="ce-card-main">
          <div className="ce-card-head">
            <span className="ce-card-bar" style={{ background: ev.colour }} />
            <span className="ce-card-heading">
              <span className="ce-card-title">{ev.title}</span>
              <span className="ce-card-when">{whenLong(ev)}</span>
              <span className="ce-card-cal" style={{ color: ev.colour }}>
                {ev.cal}
              </span>
            </span>
          </div>
          {ev.location ? (
            <div className="ce-field">
              <span className="ce-label">WHERE</span>
              <span className="ce-value">{ev.location}</span>
            </div>
          ) : null}
          {ev.notes ? (
            <div className="ce-field">
              <span className="ce-label">NOTES</span>
              <span className="ce-value ce-notes">{ev.notes}</span>
            </div>
          ) : null}
          <div className="ce-card-btns">
            <button type="button" className="ce-btn is-primary" onClick={onClose}>
              Close
            </button>
            {ev.writable && ev.href ? (
              <button type="button" className="ce-btn" onClick={onEdit}>
                Edit
              </button>
            ) : null}
          </div>
        </div>
        {ev.url ? (
          <div className="ce-qr">
            <QrCode text={ev.url} size={200} label="QR code for the event's link" />
            <span className="ce-qr-text">Scan to open the link on your phone</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* ---------------- the form ---------------- */

function nextHour() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours() + 1, 0);
}

export function EventForm({ ev, scope, onClose }) {
  const { entities } = useHub();
  const cals = writableCalendars(entities);
  const editing = !!(ev && ev.href);
  const [loaded, setLoaded] = useState(!editing);
  const [title, setTitle] = useState('');
  const [calId, setCalId] = useState((ev && ev.calId) || (cals[0] && cals[0].id) || '');
  const [allDay, setAllDay] = useState(false);
  const [start, setStart] = useState(nextHour);
  const [end, setEnd] = useState(() => new Date(nextHour().getTime() + 3600e3));
  const [rule, setRule] = useState('');
  const [extra, setExtra] = useState({ location: '', url: '', notes: '' });
  const [tried, setTried] = useState(false);
  const [saving, setSaving] = useState(false);
  const [picker, setPicker] = useState(null); // 'startDate' | 'endDate' | 'startTime' | 'endTime' | 'custom' | 'untilDate'
  const [custom, setCustom] = useState(null);

  useEffect(() => {
    if (!editing) return;
    let gone = false;
    const q = `href=${enc(ev.href)}&scope=${scope}` + (ev.occurrence ? `&occurrence=${enc(ev.occurrence)}` : '');
    api
      .get(`api/calendars/${enc(ev.calId)}/event?${q}`)
      .then((f) => {
        if (gone) return;
        setTitle(f.title || '');
        setAllDay(!!f.allDay);
        setStart(parseLocal(f.start, f.allDay));
        setEnd(parseLocal(f.end, f.allDay));
        setRule(f.rrule || '');
        setExtra({ location: f.location || '', url: f.url || '', notes: f.notes || '' });
        setLoaded(true);
      })
      .catch((e) => {
        toast(e.message || 'Couldn’t open that event', 'error');
        onClose();
      });
    return () => {
      gone = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const backwards = allDay ? atMidnight(end) < atMidnight(start) : end <= start;
  const noName = !title.trim();
  const blocked = backwards || noName || !calId;
  const cal = cals.find((c) => c.id === calId);

  // Moving the start moves the end with it (same length); the end alone can go anywhere.
  const moveStart = (d) => {
    const length = end - start;
    setStart(d);
    setEnd(new Date(d.getTime() + length));
  };
  const setDate = (which, day) => {
    const base = which === 'start' ? start : end;
    const d = new Date(day.getFullYear(), day.getMonth(), day.getDate(), base.getHours(), base.getMinutes());
    if (which === 'start') moveStart(d);
    else setEnd(d);
  };
  const setTime = (which, minutes) => {
    const base = which === 'start' ? start : end;
    const d = new Date(base.getFullYear(), base.getMonth(), base.getDate(), Math.floor(minutes / 60), minutes % 60);
    if (which === 'start') moveStart(d);
    else setEnd(d);
  };

  const preset = rule ? presetOf(rule, start) : 'never';
  const choices = presets(start);
  const onRepeat = (id) => {
    if (id === 'custom') {
      setCustom(customOf(rule || `FREQ=WEEKLY`, start));
      setPicker('custom');
    } else setRule((choices.find((c) => c.id === id) || { rule: '' }).rule);
  };

  const save = async () => {
    if (blocked) {
      setTried(true);
      return;
    }
    setSaving(true);
    const event = { title: title.trim(), allDay, start: localText(start, allDay), end: localText(end, allDay), rrule: scope === 'this' ? '' : rule, ...extra };
    try {
      if (editing) {
        const out = await api.put(`api/calendars/${enc(ev.calId)}/events`, {
          href: ev.href,
          scope,
          occurrence: ev.occurrence,
          event,
          ...(calId !== ev.calId ? { calendar: calId } : {}),
        });
        toast(`Saved to ${out.calendar}`, 'success');
      } else {
        const out = await api.post(`api/calendars/${enc(calId)}/events`, { event });
        toast(`Added to ${out.calendar}`, 'success');
      }
      onClose();
    } catch (e) {
      setSaving(false);
      toast(e.message || 'Couldn’t save it', 'error');
    }
  };

  const days = daysBetween(start, end) + 1;
  const summary =
    (title.trim() || 'Untitled') +
    ' · ' +
    (allDay ? shortDate(start) + (days > 1 ? ` – ${shortDate(end)} (${days} days)` : ', all day') : `${shortDate(start)} ${clock(start)} – ${sameDay(start, end) ? '' : shortDate(end) + ' '}${clock(end)}`) +
    (cal ? ' · ' + cal.name : '') +
    (rule && scope !== 'this' ? ' · repeats ' + (preset === 'custom' ? shortWords(rule, start) : (choices.find((c) => c.id === preset) || {}).label.toLowerCase()) : '');

  return (
    <div className="wt-overlay ce-shade" role="dialog" aria-modal="true" aria-label={editing ? 'Edit event' : 'New event'}>
      <div className="ce-form">
        <div className="ce-form-head">
          <span className="ce-form-title">{editing ? 'Edit event' : 'New event'}</span>
          <span className="ce-gap" />
          <button type="button" className="ce-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={'ce-btn is-primary' + (blocked || saving || !loaded ? ' is-off' : '')} aria-disabled={blocked || saving || !loaded} onClick={() => loaded && !saving && save()}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>

        <div className="ce-top">
          <label className="ce-col">
            <span className="ce-label">WHAT</span>
            <input className="ce-input ce-what" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What’s happening?" disabled={!loaded} />
          </label>
          <label className="ce-col ce-calcol">
            <span className="ce-label">CALENDAR</span>
            <select className="ce-input ce-select" value={calId} onChange={(e) => setCalId(e.target.value)} disabled={!loaded || scope === 'this'}>
              {cals.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="ce-allday">
          <input type="checkbox" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} disabled={!loaded} />
          All day
        </label>

        <div className="ce-grid">
          <span className="ce-label">STARTS</span>
          <button type="button" className="ce-pick" onClick={() => setPicker('startDate')} disabled={!loaded}>
            {shortDate(start)}
          </button>
          {allDay ? <span /> : (
            <button type="button" className="ce-pick ce-time" onClick={() => setPicker('startTime')} disabled={!loaded}>
              {clock(start)}
            </button>
          )}
          <span className="ce-label">ENDS</span>
          <button type="button" className={'ce-pick' + (backwards ? ' is-bad' : '')} onClick={() => setPicker('endDate')} disabled={!loaded}>
            {shortDate(end)}
          </button>
          {allDay ? <span /> : (
            <button type="button" className={'ce-pick ce-time' + (backwards ? ' is-bad' : '')} onClick={() => setPicker('endTime')} disabled={!loaded}>
              {clock(end)}
            </button>
          )}
          <span className="ce-label">REPEATS</span>
          <select className="ce-input ce-select ce-repeat" value={preset} onChange={(e) => onRepeat(e.target.value)} disabled={!loaded || scope === 'this'} aria-label="Repeats">
            {choices.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
            <option value="custom">{preset === 'custom' && rule ? 'Custom: ' + shortWords(rule, start) : 'Custom…'}</option>
          </select>
        </div>

        {backwards ? <div className="ce-bad" role="alert">It ends before it starts — pick a later end.</div> : null}
        {tried && noName ? <div className="ce-bad" role="alert">Give it a name first.</div> : null}
        <div className="ce-summary">{summary}</div>
      </div>

      {picker === 'startDate' || picker === 'endDate' || picker === 'untilDate' ? (
        <DatePicker
          value={picker === 'startDate' ? start : picker === 'endDate' ? end : (custom && custom.until) || addDays(start, 30)}
          onPick={(d) => {
            if (picker === 'untilDate') {
              setCustom((c) => ({ ...c, until: d }));
              setPicker('custom');
            } else {
              setDate(picker === 'startDate' ? 'start' : 'end', d);
              setPicker(null);
            }
          }}
          onClose={() => setPicker(picker === 'untilDate' ? 'custom' : null)}
        />
      ) : null}
      {picker === 'startTime' || picker === 'endTime' ? (
        <TimePicker
          key={picker}
          value={picker === 'startTime' ? start : end}
          onChange={(m) => setTime(picker === 'startTime' ? 'start' : 'end', m)}
          onDone={() => setPicker(null)}
        />
      ) : null}
      {picker === 'custom' && custom ? (
        <CustomRepeat
          value={custom}
          onChange={setCustom}
          onPickDate={() => setPicker('untilDate')}
          onDone={() => {
            setRule(customRule(custom, allDay));
            setPicker(null);
          }}
        />
      ) : null}
    </div>
  );
}

/* ---------------- pickers ---------------- */

export function DatePicker({ value, onPick, onClose }) {
  const [view, setView] = useState(() => new Date(value.getFullYear(), value.getMonth(), 1));
  const first = view.getDay();
  const count = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < first; i += 1) cells.push(null);
  for (let d = 1; d <= count; d += 1) cells.push(new Date(view.getFullYear(), view.getMonth(), d));
  return (
    <div className="ce-layer" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="ce-datepick" role="dialog" aria-label="Pick a date">
        <div className="ce-dp-head">
          <button type="button" className="ce-round" aria-label="Previous month" onClick={() => setView(new Date(view.getFullYear(), view.getMonth() - 1, 1))}>
            ‹
          </button>
          <span className="ce-dp-month">
            {MONTHS[view.getMonth()]} {view.getFullYear()}
          </span>
          <button type="button" className="ce-round" aria-label="Next month" onClick={() => setView(new Date(view.getFullYear(), view.getMonth() + 1, 1))}>
            ›
          </button>
        </div>
        <div className="ce-dp-week">
          {WEEKDAYS.map((w) => (
            <span key={w}>{w[0]}</span>
          ))}
        </div>
        <div className="ce-dp-grid">
          {cells.map((d, i) =>
            d ? (
              <button key={i} type="button" className={'ce-dp-day' + (sameDay(d, value) ? ' is-on' : '')} aria-pressed={sameDay(d, value)} aria-label={`${WEEKDAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}`} onClick={() => onPick(d)}>
                {d.getDate()}
              </button>
            ) : (
              <span key={i} />
            ),
          )}
        </div>
      </div>
    </div>
  );
}

/** One wheel: values scroll through a highlighted band; the one in the band is chosen (or tap one). */
function Wheel({ label, items, index, onIndex }) {
  const ref = useRef(null);
  const timer = useRef(null);
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = index * ITEM;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const onScroll = (e) => {
    const el = e.currentTarget;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const i = Math.max(0, Math.min(items.length - 1, Math.round(el.scrollTop / ITEM)));
      if (i !== index) onIndex(i);
    }, 90);
  };
  return (
    <div className="ce-wheel" role="listbox" aria-label={label} ref={ref} onScroll={onScroll}>
      <div className="ce-wheel-pad" />
      {items.map((t, i) => (
        <button
          key={t}
          type="button"
          role="option"
          aria-selected={i === index}
          className={'ce-wheel-item' + (i === index ? ' is-on' : '')}
          onClick={() => {
            onIndex(i);
            if (ref.current) ref.current.scrollTo({ top: i * ITEM, behavior: 'smooth' });
          }}
        >
          {t}
        </button>
      ))}
      <div className="ce-wheel-pad" />
    </div>
  );
}

const HOURS = Array.from({ length: 12 }, (_, i) => String(i + 1));
const MINUTES = Array.from({ length: 60 }, (_, i) => pad(i));

export function TimePicker({ value, onChange, onDone }) {
  const h24 = value.getHours();
  const min = value.getMinutes();
  const pm = h24 >= 12;
  const h12 = h24 % 12 || 12;
  const set = (h, m, p) => onChange(((h % 12) + (p ? 12 : 0)) * 60 + m);
  return (
    <div className="ce-layer">
      <div className="ce-timepick" role="dialog" aria-label="Pick a time">
        <span className="ce-tp-now">{clock(value)}</span>
        <div className="ce-tp-row">
          <div className="ce-tp-wheels">
            <div className="ce-tp-band" aria-hidden="true" />
            <Wheel label="Hour" items={HOURS} index={h12 - 1} onIndex={(i) => set(i + 1, min, pm)} />
            <span className="ce-tp-colon" aria-hidden="true">
              :
            </span>
            <Wheel label="Minute" items={MINUTES} index={min} onIndex={(i) => set(h12, i, pm)} />
          </div>
          <div className="ce-tp-ampm" role="radiogroup" aria-label="AM or PM">
            <button type="button" role="radio" aria-checked={!pm} className={'ce-ampm' + (!pm ? ' is-on' : '')} onClick={() => set(h12, min, false)}>
              AM
            </button>
            <button type="button" role="radio" aria-checked={pm} className={'ce-ampm' + (pm ? ' is-on' : '')} onClick={() => set(h12, min, true)}>
              PM
            </button>
          </div>
        </div>
        <button type="button" className="ce-btn is-primary ce-done" onClick={onDone}>
          Done
        </button>
      </div>
    </div>
  );
}

export function CustomRepeat({ value: c, onChange, onPickDate, onDone }) {
  const set = (patch) => onChange({ ...c, ...patch });
  return (
    <div className="ce-layer">
      <div className="ce-custom" role="dialog" aria-label="Custom repeat">
        <span className="ce-form-title">Repeat</span>
        <div className="ce-cu-row">
          Every
          <button type="button" className="ce-round" aria-label="Fewer" onClick={() => set({ every: Math.max(1, c.every - 1) })}>
            −
          </button>
          <span className="ce-cu-n">{c.every}</span>
          <button type="button" className="ce-round" aria-label="More" onClick={() => set({ every: c.every + 1 })}>
            +
          </button>
          <select className="ce-input ce-select ce-cu-unit" value={c.unit} onChange={(e) => set({ unit: e.target.value })} aria-label="Unit">
            <option value="days">days</option>
            <option value="weeks">weeks</option>
            <option value="months">months</option>
            <option value="years">years</option>
          </select>
        </div>
        {c.unit === 'weeks' ? (
          <div className="ce-cu-days">
            <span className="ce-label">ON</span>
            <div className="ce-cu-dayrow">
              {WEEKDAYS.map((w, i) => {
                const on = c.days.includes(i);
                return (
                  <button key={w} type="button" aria-pressed={on} aria-label={w} className={'ce-cu-day' + (on ? ' is-on' : '')} onClick={() => set({ days: on ? c.days.filter((x) => x !== i) : [...c.days, i] })}>
                    {w[0]}
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}
        <div className="ce-cu-row">
          <span className="ce-label ce-cu-ends">ENDS</span>
          <select className="ce-input ce-select" value={c.ends} onChange={(e) => set({ ends: e.target.value, until: c.until || null })} aria-label="Ends">
            <option value="never">Never</option>
            <option value="date">On a date</option>
            <option value="count">After a number of times</option>
          </select>
          {c.ends === 'date' ? (
            <button type="button" className="ce-pick ce-cu-until" onClick={onPickDate}>
              {c.until ? `${shortDate(c.until)}, ${c.until.getFullYear()}` : 'Pick a date'}
            </button>
          ) : null}
          {c.ends === 'count' ? (
            <span className="ce-cu-count">
              <button type="button" className="ce-round" aria-label="Fewer times" onClick={() => set({ count: Math.max(1, c.count - 1) })}>
                −
              </button>
              {c.count} times
              <button type="button" className="ce-round" aria-label="More times" onClick={() => set({ count: c.count + 1 })}>
                +
              </button>
            </span>
          ) : null}
        </div>
        <div className="ce-cu-sentence">{describeCustom(c)}</div>
        <button type="button" className={'ce-btn is-primary ce-done' + (c.ends === 'date' && !c.until ? ' is-off' : '')} onClick={() => !(c.ends === 'date' && !c.until) && onDone()}>
          Done
        </button>
      </div>
    </div>
  );
}

/* ---------------- reminders ---------------- */

function inWords(r, now) {
  const left = Math.round((r.startsAt * 1000 - now) / 60000);
  if (left <= 0) return 'NOW';
  if (left < 60) return `IN ${left} MINUTE${left === 1 ? '' : 'S'}`;
  if (left < 1440) {
    const h = Math.round(left / 60);
    return `IN ${h} HOUR${h === 1 ? '' : 'S'}`;
  }
  const d = Math.round(left / 1440);
  return `IN ${d} DAY${d === 1 ? '' : 'S'}`;
}

/** Calendar reminders (calendars with "Remind on the wall tablets" ticked): a card at the top with a chime. */
export function CalendarReminders({ onWake }) {
  const [cards, setCards] = useState([]);
  const stops = useRef({});
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    unlockAudioOnFirstTouch();
    api
      .get('api/calendar-reminders', { quiet: true })
      .then((r) => setCards(r.reminders || []))
      .catch(() => {});
    const t = setInterval(() => setNow(Date.now()), 30000);
    return () => {
      clearInterval(t);
      Object.values(stops.current).forEach((s) => s && s());
    };
  }, []);
  useLive((m) => {
    if (m.type === 'calendarReminder' && m.reminder) {
      if (onWake) onWake();
      try {
        if (window.NewmanWall && typeof window.NewmanWall.wake === 'function') window.NewmanWall.wake();
      } catch {
        /* the native bridge is optional */
      }
      stops.current[m.reminder.id] = playSound('chime');
      setCards((list) => [...list.filter((x) => x.id !== m.reminder.id), m.reminder]);
    } else if (m.type === 'calendarReminderDone') {
      const stop = stops.current[m.id];
      if (stop) stop();
      delete stops.current[m.id];
      setCards((list) => list.filter((x) => x.id !== m.id));
    }
  });
  if (!cards.length) return null;
  const ok = (r) => {
    setCards((list) => list.filter((x) => x.id !== r.id));
    api.post(`api/calendar-reminders/${enc(r.id)}/done`).catch(() => {});
  };
  return (
    <div className="ce-reminders" aria-live="assertive">
      {cards.slice(-3).map((r) => {
        const s = new Date(r.start);
        return (
          <div key={r.id} className="ce-reminder" role="alert">
            <span className="ce-card-bar" style={{ background: r.colour }} />
            <span className="ce-reminder-text">
              <span className="ce-reminder-when" style={{ color: r.colour }}>
                {inWords(r, now)} · {String(r.calendar || '').toUpperCase()}
              </span>
              <span className="ce-reminder-title">{r.title}</span>
              <span className="ce-reminder-sub">{[r.allDay ? 'All day' : clock(s), r.location].filter(Boolean).join(' · ')}</span>
            </span>
            <button type="button" className="ce-btn is-primary" onClick={() => ok(r)}>
              OK
            </button>
          </div>
        );
      })}
    </div>
  );
}
