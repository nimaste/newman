// Weather and calendar tiles (TabletOverview board; the Charcoal board's header strip for the
// compact weather), plus the weather chips. Devices are picked by what they report — a forecast,
// upcoming events — never by kind id.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { deviceIcon, entityLabel } from '../model.js';
import { WeatherIcon } from './WeatherIcon.jsx';
import { DaySheet } from './WeatherDay.jsx';
import { useCompactWall } from './compactContext.js';
import { useWidth } from './look.js';
import { UNITS } from './compact.js';

export { WeatherIcon };

export const hasForecast = (e) => !!(e && e.attrs && Array.isArray(e.attrs.forecast));
export const hasAgenda = (e) => !!(e && e.attrs && Array.isArray(e.attrs.upcoming));

const deg = (v) => (typeof v === 'number' && Number.isFinite(v) ? Math.round(v) + '°' : '–');
/** Rain/snow chance, always shown (dimmed under 10%) so the column never looks broken. */
function Chance({ v }) {
  if (typeof v !== 'number') return <span className="wx-rain" />;
  return <span className={'wx-rain' + (v < 10 ? ' is-low' : '')}>{Math.round(v)}%</span>;
}
const amount = (v) => (Math.round(v * 10) / 10).toLocaleString([], { maximumFractionDigits: 1 });

/** The current time, re-read every `ms`. */
function useNow(ms = 60000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** How many rows with a `pitch` (row + gap) fit in `ref`'s height; null while it has no height
 *  (screen off, tab hidden), so a list measured then shows everything instead of only "+N more". */
function useRows(ref, pitch, gap) {
  const [n, setN] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => {
      const h = el.clientHeight;
      setN(h > 0 ? Math.max(0, Math.floor((h + gap) / pitch)) : null);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    // A sleeping WebView can miss the resize when the screen comes back, so measure again then too.
    const again = () => requestAnimationFrame(measure);
    document.addEventListener('visibilitychange', again);
    window.addEventListener('focus', again);
    window.addEventListener('resize', again);
    return () => {
      ro.disconnect();
      document.removeEventListener('visibilitychange', again);
      window.removeEventListener('focus', again);
      window.removeEventListener('resize', again);
    };
  }, [ref, pitch, gap]);
  return n;
}

/* ---------------- weather alerts ---------------- */

const RANK = { red: 3, orange: 2, yellow: 1 };
const TYPE_LABEL = { warning: 'Warning', watch: 'Watch', advisory: 'Advisory', statement: 'Statement' };
const worst = (alerts) => alerts.slice().sort((a, b) => (RANK[b.colour] || 0) - (RANK[a.colour] || 0))[0];

function fmtUntil(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return 'Until ' + d.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

/** The full text of each alert on a glass sheet over the whole wall. */
function AlertSheet({ place, alerts, onClose, host }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const sheet = (
    <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={`Weather alerts for ${place}`} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wx-sheet">
        <div className="wx-sheet-body">
          {alerts.map((a) => (
            <section key={a.id} className={'wx-sheet-alert wx-c--' + (a.colour || 'yellow')}>
              <div className="wx-sheet-head">
                <Icon name="alert" size={22} strokeWidth={2.2} />
                <span className="wx-sheet-name">{a.name || 'Weather alert'}</span>
              </div>
              <div className="wx-sheet-meta">
                {[a.colour ? a.colour[0].toUpperCase() + a.colour.slice(1) + ' ' + (TYPE_LABEL[a.type] || 'alert').toLowerCase() : TYPE_LABEL[a.type], a.area, fmtUntil(a.expires)].filter(Boolean).join(' · ')}
              </div>
              <div className="wx-sheet-text">{a.text || 'No details given.'}</div>
            </section>
          ))}
        </div>
        <button type="button" className="wx-sheet-close" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
  return host ? createPortal(sheet, host) : sheet;
}

/** A coloured bar (or chip) naming the worst alert; tap for the full text. */
export function AlertButton({ place, alerts, className, children }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const top = worst(alerts);
  return (
    <>
      <button ref={ref} type="button" className={className + ' wx-c--' + (top.colour || 'yellow')} onClick={() => setOpen(true)} aria-haspopup="dialog">
        {children || (
          <>
            <Icon name="alert" size={17} strokeWidth={2.2} />
            <span className="wx-alert-name">{top.name || 'Weather alert'}</span>
            {alerts.length > 1 ? <span className="wx-alert-more">+{alerts.length - 1}</span> : null}
          </>
        )}
      </button>
      {open ? <AlertSheet place={place} alerts={alerts} onClose={() => setOpen(false)} host={ref.current && ref.current.closest('.wall')} /> : null}
    </>
  );
}

/* ---------------- weather tile ---------------- */

const localDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

function DaysList({ forecast, now, onOpen }) {
  const ref = useRef(null);
  const today = localDay(new Date(now));
  const days = forecast.filter((d) => d.date > today);
  const fit = useRows(ref, 30, 6);
  const shown = fit == null ? days : days.slice(0, fit);
  const lo = Math.min(...days.map((d) => d.low).filter((v) => typeof v === 'number'));
  const hi = Math.max(...days.map((d) => d.high).filter((v) => typeof v === 'number'));
  const span = hi - lo || 1;
  return (
    <div className="wx-days" ref={ref}>
      {shown.map((d) => (
        <button key={d.date} type="button" className="wx-day" onClick={() => onOpen(d.date)} aria-label={`${d.day}: ${deg(d.high)} / ${deg(d.low)}, details`}>
          <span>{d.day}</span>
          <Chance v={d.precipChance} />
          <span className="wx-lo">{deg(d.low)}</span>
          <span className="wx-bar">
            <span style={{ left: ((d.low - lo) / span) * 100 + '%', width: Math.max(4, ((d.high - d.low) / span) * 100) + '%' }} />
          </span>
          <span>{deg(d.high)}</span>
        </button>
      ))}
    </div>
  );
}

function HoursRow({ hourly, now, count, win, onOpen }) {
  const start = hourly.findIndex((h) => new Date(h.time).getTime() + 3600e3 > now);
  const hours = (start < 0 ? [] : hourly.slice(start)).slice(0, count);
  return (
    <div className="wx-hours" style={{ gridTemplateColumns: `repeat(${Math.max(1, hours.length)}, minmax(0, 1fr))` }}>
      {hours.map((h, i) => (
        <button key={h.time} type="button" className="wx-hour" onClick={() => onOpen(String(h.time).slice(0, 10), h.time)}>
          <span className="wx-hour-time">{i === 0 ? 'Now' : new Date(h.time).toLocaleTimeString([], { hour: 'numeric' }).replace(':00', '')}</span>
          <WeatherIcon condition={h.condition} size={26} isDay={dayAt(h.time, win)} />
          <span className="wx-hour-temp">{deg(h.temperature)}</span>
          <Chance v={h.precipChance} />
        </button>
      ))}
    </div>
  );
}

/** Daytime at iso, by today's sunrise/sunset minutes (win); day when unknown. */
function dayAt(iso, win) {
  if (!win) return true;
  const d = new Date(iso);
  const mins = d.getHours() * 60 + d.getMinutes();
  return mins >= win[0] && mins < win[1];
}

function StripDays({ forecast, now, count, onOpen }) {
  const today = localDay(new Date(now));
  return forecast
    .filter((d) => d.date > today)
    .slice(0, count)
    .map((d) => (
      <button key={d.date} type="button" className="wx-strip-day" onClick={() => onOpen(d.date)}>
        <span className="wx-dim">{d.day}</span>
        <span>
          {deg(d.high)} <span className="wx-dim">{deg(d.low)}</span>
        </span>
      </button>
    ));
}

/** "Rain 60% this afternoon" for the rest of today (the likeliest hour at 30%+), or ''. */
function rainLine(a, now) {
  const hours = (Array.isArray(a.hours) && a.hours.length ? a.hours : a.hourly) || [];
  const today = localDay(new Date(now));
  let best = null;
  hours.forEach((h) => {
    const t = new Date(h.time);
    if (localDay(t) !== today || t.getTime() + 3600e3 < now) return;
    if (typeof h.precipChance === 'number' && h.precipChance >= 30 && (!best || h.precipChance > best.precipChance)) best = h;
  });
  if (!best) return '';
  const hr = new Date(best.time).getHours();
  const when = new Date(best.time).getTime() <= now ? 'now' : hr < 12 ? 'this morning' : hr < 17 ? 'this afternoon' : hr < 21 ? 'this evening' : 'tonight';
  const what = (best.snow || 0) > (best.rain || 0) ? 'Snow' : 'Rain';
  return `${what} ${Math.round(best.precipChance)}% ${when}`;
}

/** The compact wall's weather: the next days (or hours) side by side, as many as fit across. */
function WcAcross({ forecast, hourly, hours, now, win, onOpen }) {
  const ref = useRef(null);
  const width = useWidth(ref);
  const unit = typeof window !== 'undefined' && window.innerHeight ? window.innerHeight / UNITS : 1;
  const count = width ? Math.max(3, Math.min(7, Math.floor(width / (96 * unit)))) : 4;
  const today = localDay(new Date(now));
  return (
    <div className="wc-days" ref={ref}>
      {hours ? (
        <HoursRow hourly={hourly} now={now} count={count + 2} win={win} onOpen={onOpen} />
      ) : (
        forecast
          .filter((d) => d.date > today)
          .slice(0, count)
          .map((d) => (
            <button key={d.date} type="button" className="wc-day" onClick={() => onOpen(d.date)} aria-label={`${d.day}: ${deg(d.high)} / ${deg(d.low)}, details`}>
              <span className="wx-dim">{d.day}</span>
              <b>{deg(d.high)}</b>
              <span className="wx-dim">{deg(d.low)}</span>
            </button>
          ))
      )}
    </div>
  );
}

export function WeatherTile({ tile }) {
  const wc = useCompactWall();
  const { entities, entitiesById, kindsById } = useHub();
  const now = useNow();
  const [sheet, setSheet] = useState(null); // { date, hour } of the open day sheet
  const rootRef = useRef(null);
  const chosen = entitiesById[tile.config.entity];
  const e = hasForecast(chosen) ? chosen : (entities || []).find(hasForecast);
  if (!e) return <div className="wt-empty">Add a Weather connection in the Newman dashboard.</div>;
  const a = e.attrs || {};
  const kind = kindsById[e.kind];
  const label = entityLabel(e, kind);
  const alerts = Array.isArray(a.alerts) ? a.alerts : [];
  const forecast = Array.isArray(a.forecast) ? a.forecast : [];
  const hourly = Array.isArray(a.hourly) ? a.hourly : [];
  const isDay = a.isDay !== false;
  const toMins = (s) => (s ? new Date(s).getHours() * 60 + new Date(s).getMinutes() : null);
  const win = a.sunrise && a.sunset ? [toMins(a.sunrise), toMins(a.sunset)] : null;
  const compact = tile.h <= 4;
  const cols = Math.floor(tile.w / 2); // its width in the old grid's columns (12 across), which these counts were made for
  const hours = tile.config.show === 'hours';
  // Today's chance and amount after H/L, when there is any: "52% · 1.6 mm" (snow in cm).
  const todayF = forecast.find((d) => d.date === localDay(new Date(now))) || {};
  const chance = typeof todayF.precipChance === 'number' ? todayF.precipChance : a.precipChance;
  const rainMm = typeof todayF.rain === 'number' ? todayF.rain : typeof todayF.precip === 'number' ? todayF.precip : a.rainToday;
  const snowCm = typeof todayF.snow === 'number' ? todayF.snow : a.snowToday;
  const wet = [
    typeof chance === 'number' && chance > 0 ? `${Math.round(chance)}%` : '',
    typeof snowCm === 'number' && snowCm > 0 ? `${amount(snowCm)} cm` : typeof rainMm === 'number' && rainMm > 0 ? `${amount(rainMm)} mm` : '',
  ].filter(Boolean);
  const hl = `H ${deg(a.high)} · L ${deg(a.low)}${wet.length ? ' · ' + wet.join(' · ') : ''}`;
  const banner = alerts.length ? <AlertButton place={e.name} alerts={alerts} className="wx-alert" /> : null;
  const today = localDay(new Date(now));
  const open = (date, hour) => setSheet({ date: date || today, hour: hour || null });
  const daySheet = sheet ? (
    <DaySheet place={e.name} attrs={a} date={sheet.date} hour={sheet.hour} host={rootRef.current && rootRef.current.closest('.wall')} onClose={() => setSheet(null)} />
  ) : null;
  // The icon and temperature open the full Weather screen.
  const nowButton = (size, small) => (
    <a className="wx-now-btn" href="#/wall/weather" aria-label={`${deg(a.temperature)} ${label}: open the weather`}>
      <WeatherIcon condition={e.state} isDay={isDay} size={size} strokeWidth={small ? 1.7 : 1.6} />
      <span className={'wx-temp' + (small ? ' wx-temp--sm' : '')}>{deg(a.temperature)}</span>
    </a>
  );

  if (a.temperature === undefined)
    return <div className="wt-empty">{e.available === false ? 'Weather unavailable right now.' : 'Waiting for the weather…'}</div>;

  if (tile.config.show === 'line') {
    // One line (TabletToday board): now, high/low, and rain (or snow) later today. Opens the Weather screen.
    return (
      <a className={'wx-line' + (e.available === false ? ' is-stale' : '')} href="#/wall/weather" aria-label={`${e.name}: ${deg(a.temperature)} ${label}, open the forecast`}>
        <WeatherIcon condition={e.state} isDay={isDay} size={40} strokeWidth={1.6} />
        <span className="wx-temp wx-temp--line">{deg(a.temperature)}</span>
        <span className="wx-sub">
          <span>
            {label} · H {deg(a.high)} L {deg(a.low)}
          </span>
          <span className="wx-dim">{alerts.length ? (alerts[0].name || 'Weather warning') : rainLine(a, now) || `Feels like ${deg(a.feelsLike)}`}</span>
        </span>
      </a>
    );
  }

  if (wc) {
    // The wall's compact layout: the tile's name, now (big), then as many days (or hours) as fit across the bottom.
    const spec = wc.types.find((t) => t.type === tile.type);
    return (
      <div ref={rootRef} className={'wx wx--wc' + (e.available === false ? ' is-stale' : '')} aria-label={`${e.name}: ${deg(a.temperature)} ${label}`}>
        <div className="wt-tile-head">
          <span className="wc-head">
            <Icon name={deviceIcon(e, kind)} size={22} />
            {spec ? spec.name : e.name}
          </span>
        </div>
        {banner}
        <div className="wx-now">
          {nowButton(84, false)}
          <span className="wx-sub">
            <span>{label}</span>
            <span className="wx-dim">{hl}</span>
          </span>
        </div>
        <WcAcross forecast={forecast} hourly={hourly} hours={hours} now={now} win={win} onOpen={open} />
        {daySheet}
      </div>
    );
  }

  if (compact) {
    // The Charcoal board's header strip: icon, temperature, H/L, then as many days as fit.
    const extra = Math.max(0, hours ? Math.min(6, (cols - 3) * 2) : Math.min(5, cols - 3));
    return (
      <div ref={rootRef} className={'wx wx--compact' + (e.available === false ? ' is-stale' : '')} aria-label={`${e.name}: ${deg(a.temperature)} ${label}`}>
        {banner}
        <div className="wx-strip">
          {nowButton(40, true)}
          <span className="wx-sub">
            {tile.w >= 8 ? <span>{label}</span> : null}
            <span className="wx-dim">{hl}</span>
          </span>
          {extra ? <span className="wx-strip-gap" /> : null}
          {extra && !hours ? <StripDays forecast={forecast} now={now} count={extra} onOpen={open} /> : null}
          {extra && hours ? <HoursRow hourly={hourly} now={now} count={extra} win={win} onOpen={open} /> : null}
        </div>
        {daySheet}
      </div>
    );
  }
  return (
    <div ref={rootRef} className={'wx' + (e.available === false ? ' is-stale' : '')} aria-label={`${e.name}: ${deg(a.temperature)} ${label}`}>
      {banner}
      <div className="wx-now">
        {nowButton(52, false)}
        <span className="wx-sub">
          <span>{label}</span>
          <span className="wx-dim">{hl}</span>
        </span>
      </div>
      {hours ? <HoursRow hourly={hourly} now={now} count={Math.max(3, Math.min(6, cols + 2))} win={win} onOpen={open} /> : <DaysList forecast={forecast} now={now} onOpen={open} />}
      {daySheet}
    </div>
  );
}

/* ---------------- calendar tile ---------------- */

function timeParts(d) {
  const parts = new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).formatToParts(d);
  const period = (parts.find((p) => p.type === 'dayPeriod') || {}).value || '';
  const core = parts
    .filter((p) => p.type !== 'dayPeriod')
    .map((p) => p.value)
    .join('')
    .trim();
  return { core, period, full: parts.map((p) => p.value).join('') };
}

function timeText(ev) {
  if (ev.allDay) return 'All day';
  const s = timeParts(ev.s);
  if (localDay(ev.e) !== localDay(ev.s)) return 'From ' + s.full;
  if (ev.e - ev.s < 60e3) return s.full;
  const e = timeParts(ev.e);
  return s.period && s.period === e.period ? `${s.core} – ${e.full}` : `${s.full} – ${e.full}`;
}

function dayLabel(key, now) {
  const today = new Date(now);
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  if (key === localDay(today)) return ['Today'];
  if (key === localDay(tomorrow)) return ['Tomorrow'];
  const [y, m, d] = key.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  return [date.toLocaleDateString([], { weekday: 'short' }), date.toLocaleDateString([], { month: 'short', day: 'numeric' })];
}

/** How many of `ref`'s rows (children matching `rows`) fit whole in its height, as drawn; null while it
 *  has no height (screen off, tab hidden), so a list measured then hides nothing. */
function useWholeRows(ref, rows, count) {
  const [n, setN] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => {
      if (!el.clientHeight) {
        setN(null);
        return;
      }
      const bottom = el.getBoundingClientRect().bottom + 0.5;
      let fit = 0;
      for (const row of el.querySelectorAll(rows)) {
        if (row.getBoundingClientRect().bottom > bottom) break;
        fit += 1;
      }
      setN(fit);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    // A sleeping WebView can miss the resize when the screen comes back, so measure again then too.
    const again = () => requestAnimationFrame(measure);
    document.addEventListener('visibilitychange', again);
    window.addEventListener('focus', again);
    window.addEventListener('resize', again);
    return () => {
      ro.disconnect();
      document.removeEventListener('visibilitychange', again);
      window.removeEventListener('focus', again);
      window.removeEventListener('resize', again);
    };
  }, [ref, rows, count]);
  return n;
}

export function CalendarTile({ tile }) {
  const { entities } = useHub();
  const now = useNow(30000);
  const listRef = useRef(null);
  const ids = tile.config.entities || [];
  const cals = (entities || []).filter(hasAgenda).filter((e) => !ids.length || ids.includes(e.id));
  const days = Math.max(1, Math.min(31, Number(tile.config.days) || 31));
  const todayStart = new Date(new Date(now).setHours(0, 0, 0, 0));
  const until = new Date(todayStart.getFullYear(), todayStart.getMonth(), todayStart.getDate() + days).getTime();
  const events = [];
  cals.forEach((c) =>
    (c.attrs.upcoming || []).forEach((ev, i) => {
      const s = new Date(ev.start);
      const e = new Date(ev.end || ev.start);
      if (Number.isNaN(s.getTime()) || e.getTime() <= now || s.getTime() >= until) return;
      const shownOn = s < todayStart ? todayStart : s; // running since before today: under Today
      events.push({ ...ev, key: c.id + ':' + i, s, e, day: localDay(shownOn), colour: c.attrs.colour || '#3CCFB4', cal: c.name, calId: c.id, writable: c.attrs.readOnly === false, on: !ev.allDay && s.getTime() <= now && now < e.getTime() });
    }),
  );
  events.sort((a, b) => (a.day === b.day ? (a.allDay === b.allDay ? a.s - b.s : a.allDay ? -1 : 1) : a.day < b.day ? -1 : 1));
  // Every event is drawn; those that don't fit whole are hidden (measured as drawn, so any text size works).
  const fit = useWholeRows(listRef, '.cal-ev', events.length);
  let lastDay = null;
  // A tap anywhere on the tile - an event, the title, the space between - opens the full-screen calendar,
  // with this tile's calendars (events are opened and added there).
  const href = '#/wall/calendar' + (ids.length ? '?cals=' + ids.map(encodeURIComponent).join(',') : '');
  return (
    <div className="cal">
      {cals.length ? <a className="cal-cover" href={href} aria-label="Open the calendar" data-tile-surface="" /> : null}
      <div className="wt-tile-head">
        <span className="cal-head">
          <Icon name="calendar" size={20} /> Coming up
        </span>
      </div>
      <div className="cal-list" ref={listRef}>
        {!(entities || []).some(hasAgenda) ? (
          <div className="wt-empty">Add a Calendars connection in the Newman dashboard.</div>
        ) : !cals.length ? (
          <div className="wt-empty">The calendars chosen for this tile are gone.</div>
        ) : !events.length ? <div className="wt-muted">Nothing coming up.</div> : null}
        {events.map((ev, i) => {
          const first = ev.day !== lastDay;
          lastDay = ev.day;
          const [l1, l2] = dayLabel(ev.day, now);
          const cut = fit != null && i >= fit;
          return (
            <div key={ev.key} className={'cal-ev' + (ev.on ? ' is-now' : '') + (cut ? ' is-cut' : '')} aria-hidden={cut || undefined}>
              <span className="cal-when">
                {first ? (
                  <>
                    {l1}
                    {l2 ? <span>{l2}</span> : null}
                  </>
                ) : null}
              </span>
              <span className="cal-bar" style={{ background: ev.colour }} />
              <span className="cal-text">
                <span className="cal-title">
                  {ev.on ? <span className="cal-now">Now</span> : null}
                  {ev.title}
                </span>
                <span className="cal-time">
                  {timeText(ev)}
                  {ev.location ? ' · ' + ev.location : ''}
                </span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---------------- chips ---------------- */

/** "14° Cloudy" for the first page's chips. */
export function WeatherChip() {
  const { entities, kindsById } = useHub();
  const e = (entities || []).find(hasForecast);
  if (!e || !e.attrs || e.attrs.temperature === undefined) return null;
  const label = entityLabel(e, kindsById[e.kind]);
  return (
    <div className="wt-chip wx-chip">
      <WeatherIcon condition={e.state} isDay={e.attrs.isDay !== false} size={20} strokeWidth={1.9} />
      {deg(e.attrs.temperature)} {label}
    </div>
  );
}

/** The active weather warning (tap for its text). */
/** The active weather warning: { e (the place), warnings, top (the worst) }, or null. */
export function useWeatherWarning() {
  const { entities } = useHub();
  const e = (entities || []).find((x) => hasForecast(x) && Array.isArray(x.attrs.alerts) && x.attrs.alerts.some((a) => a.type === 'warning'));
  if (!e) return null;
  const warnings = e.attrs.alerts.filter((a) => a.type === 'warning');
  return { e, warnings, top: worst(warnings) };
}

export function WeatherWarningChip() {
  const w = useWeatherWarning();
  if (!w) return null;
  const { e, warnings, top } = w;
  return (
    <AlertButton place={e.name} alerts={warnings} className="wt-chip wx-chip-alert">
      <Icon name="alert" size={18} strokeWidth={2.2} />
      {top.name || 'Weather warning'}
    </AlertButton>
  );
}
