// One day's weather in detail (like a weather site's day view): the hour-by-hour chart —
// temperature line, rain/snow bars, chance of precipitation — and the day's numbers.
// Opened from the weather tile; day chips switch days; ✕, a tap outside, Escape or Back close it.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../icons.jsx';
import { WeatherIcon } from './WeatherIcon.jsx';

const num = (v) => typeof v === 'number' && Number.isFinite(v);
const deg = (v) => (num(v) ? Math.round(v) + '°' : '–');
const one = (v) => (num(v) ? (Math.round(v * 10) / 10).toLocaleString([], { maximumFractionDigits: 1 }) : '–');
const localDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const parseDay = (key) => {
  const [y, m, d] = String(key).split('-').map(Number);
  return new Date(y, m - 1, d);
};
const hourLabel = (iso) => new Date(iso).toLocaleTimeString([], { hour: 'numeric' }).replace(':00', '');
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '');

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const compass = (deg) => (num(deg) ? COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16] : '');
function uvWord(uv) {
  if (!num(uv)) return '';
  if (uv < 3) return 'Low';
  if (uv < 6) return 'Moderate';
  if (uv < 8) return 'High';
  if (uv < 11) return 'Very high';
  return 'Extreme';
}
const CONDITION = {
  clear: 'Clear', 'mostly-clear': 'Mostly clear', 'partly-cloudy': 'Partly cloudy', cloudy: 'Cloudy', fog: 'Fog', drizzle: 'Drizzle', rain: 'Rain',
  'freezing-rain': 'Freezing rain', snow: 'Snow', showers: 'Showers', 'snow-showers': 'Snow showers', thunderstorm: 'Thunderstorm',
};

function useSize(ref) {
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

/* ---------------- the hour-by-hour chart ---------------- */

const RAIN = '#5AA9FF';
const SNOW = '#D9CCFF';

function DayChart({ hours, now, focus, win }) {
  const box = useRef(null);
  const { w: W, h: H } = useSize(box);
  const n = hours.length;
  const ok = W > 100 && H > 120 && n > 0;
  let svg = null;
  if (ok) {
    const pl = 8;
    const pr = 8;
    const colW = (W - pl - pr) / n;
    const x = (i) => pl + (i + 0.5) * colW;
    const iconsY = 4;
    const tTop = 62;
    const tBottom = Math.round(H * 0.52);
    const pTop = tBottom + 26;
    const pBottom = H - 26;
    const temps = hours.map((h) => h.temperature).filter(num);
    const tMin = Math.min(...temps) - 1;
    const tMax = Math.max(...temps) + 1;
    const ty = (t) => tBottom - ((t - tMin) / (tMax - tMin || 1)) * (tBottom - tTop);
    const amount = (h) => (num(h.rain) ? h.rain : num(h.precip) ? h.precip : 0) + (num(h.snow) ? h.snow : 0);
    const pMax = Math.max(2, ...hours.map(amount));
    const py = (v) => ((pBottom - pTop) * v) / pMax;
    const line = hours.map((h, i) => (num(h.temperature) ? `${i === 0 || !num(hours[i - 1].temperature) ? 'M' : 'L'}${x(i).toFixed(1)} ${ty(h.temperature).toFixed(1)}` : '')).join(' ');
    const chance = hours.map((h) => (num(h.precipChance) ? h.precipChance : 0));
    const area = `M${x(0)} ${pBottom} ` + chance.map((c, i) => `L${x(i).toFixed(1)} ${(pBottom - ((pBottom - pTop) * c) / 100).toFixed(1)}`).join(' ') + ` L${x(n - 1)} ${pBottom} Z`;
    const chanceLine = chance.map((c, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${(pBottom - ((pBottom - pTop) * c) / 100).toFixed(1)}`).join(' ');
    const t0 = new Date(hours[0].time).getTime();
    const nowI = (now - t0) / 3600e3; // hours since the first column's start
    const showNow = nowI >= 0 && nowI < n;
    const barW = Math.max(4, Math.min(26, colW * 0.6));
    const isDay = (iso) => {
      if (!win) return true;
      const d = new Date(iso);
      const m = d.getHours() * 60 + d.getMinutes();
      return m >= win[0] && m < win[1];
    };
    svg = (
      <svg width={W} height={H} className="wd-svg" role="img" aria-label="Hour-by-hour temperature and precipitation">
        {focus >= 0 && focus < n ? <rect x={pl + focus * colW} y={0} width={colW} height={H - 4} rx={10} className="wd-focus" /> : null}
        {/* chance of precipitation: a faint area behind the bars */}
        <path d={area} className="wd-chance" />
        <path d={chanceLine} className="wd-chance-line" />
        <line x1={pl} x2={W - pr} y1={pBottom} y2={pBottom} className="wd-base" />
        {hours.map((h, i) => {
          const r = num(h.rain) ? h.rain : num(h.precip) && !num(h.snow) ? h.precip : 0;
          const s = num(h.snow) ? h.snow : 0;
          const rh = py(r);
          const sh = py(s);
          const total = r + s;
          return (
            <g key={h.time}>
              {r > 0 ? <rect x={x(i) - barW / 2} y={pBottom - rh} width={barW} height={rh} rx={3} fill={RAIN} /> : null}
              {s > 0 ? <rect x={x(i) - barW / 2} y={pBottom - rh - sh} width={barW} height={sh} rx={3} fill={SNOW} /> : null}
              {total >= 0.2 && colW >= 30 ? (
                <text x={x(i)} y={pBottom - rh - sh - 5} className="wd-amt">
                  {one(total)}
                </text>
              ) : null}
            </g>
          );
        })}
        {/* temperature */}
        <path d={line} className="wd-temp" />
        {hours.map((h, i) =>
          i % 3 === 0 && num(h.temperature) ? (
            <g key={'t' + h.time}>
              <circle cx={x(i)} cy={ty(h.temperature)} r={4} className="wd-dot" />
              <text x={x(i)} y={ty(h.temperature) - 11} className="wd-tlabel">
                {deg(h.temperature)}
              </text>
            </g>
          ) : null,
        )}
        {/* condition icons along the top, hours along the bottom */}
        {hours.map((h, i) =>
          i % 3 === 0 ? (
            <g key={'i' + h.time} transform={`translate(${x(i) - 13} ${iconsY})`}>
              <WeatherIcon condition={h.condition} isDay={isDay(h.time)} size={26} strokeWidth={1.7} />
            </g>
          ) : null,
        )}
        {hours.map((h, i) =>
          i % 3 === 0 ? (
            <text key={'x' + h.time} x={x(i)} y={H - 6} className="wd-xlabel">
              {hourLabel(h.time)}
            </text>
          ) : null,
        )}
        {showNow ? (
          <g>
            <line x1={pl + nowI * colW} x2={pl + nowI * colW} y1={tTop - 22} y2={pBottom} className="wd-now" />
            <text x={pl + nowI * colW} y={pBottom + 16} className="wd-now-label">
              Now
            </text>
          </g>
        ) : null}
      </svg>
    );
  }
  return (
    <div className="wd-chart" ref={box}>
      {svg}
    </div>
  );
}

/* ---------------- the sheet ---------------- */

function Card({ label, value, unit, sub, children }) {
  return (
    <div className="wd-card">
      <span className="wd-card-label">{label}</span>
      <span className="wd-card-value">
        {children}
        {value}
        {unit ? <span className="wd-card-unit">{unit}</span> : null}
      </span>
      {sub ? <span className="wd-card-sub">{sub}</span> : null}
    </div>
  );
}

function fmtUntil(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : 'until ' + d.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

/**
 * The day sheet. `attrs` is the weather device's attrs; `date` the day to open ("YYYY-MM-DD");
 * `hour` (optional, local ISO) the hour that was tapped.
 */
export function DaySheet({ place, attrs, date, hour, host, onClose }) {
  const [day, setDay] = useState(date);
  const [focusTime, setFocusTime] = useState(hour || null);
  const [openAlert, setOpenAlert] = useState(null);
  const [now, setNow] = useState(() => Date.now());
  const closed = useRef(false);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(t);
  }, []);

  // Back closes the sheet: it takes one history step, given back when it closes another way.
  useEffect(() => {
    window.history.pushState({ newmanSheet: true }, '');
    const onPop = () => {
      if (closed.current) return;
      closed.current = true;
      onClose();
    };
    const onKey = (e) => e.key === 'Escape' && close();
    window.addEventListener('popstate', onPop);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('popstate', onPop);
      window.removeEventListener('keydown', onKey);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const close = () => {
    if (closed.current) return;
    closed.current = true;
    if (window.history.state && window.history.state.newmanSheet) window.history.back();
    onClose();
  };

  const forecast = Array.isArray(attrs.forecast) ? attrs.forecast : [];
  const all = Array.isArray(attrs.hours) && attrs.hours.length ? attrs.hours : Array.isArray(attrs.hourly) ? attrs.hourly : [];
  const f = forecast.find((d) => d.date === day) || forecast[0] || {};
  const hours = all.filter((h) => String(h.time).slice(0, 10) === day);
  const today = localDay(new Date(now));
  const isToday = day === today;
  const focus = focusTime ? hours.findIndex((h) => h.time.slice(0, 13) === focusTime.slice(0, 13)) : -1;
  const toMins = (s) => (s ? new Date(s).getHours() * 60 + new Date(s).getMinutes() : null);
  const win = f.sunrise && f.sunset ? [toMins(f.sunrise), toMins(f.sunset)] : null;
  const alerts = (Array.isArray(attrs.alerts) ? attrs.alerts : []).filter((a) => !a.expires || new Date(a.expires).getTime() > now);
  const rain = num(f.rain) ? f.rain : num(f.precip) && !num(f.snow) ? f.precip : null;
  const nearFreezing = num(f.low) && f.low <= 2;
  const title = parseDay(day).toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });

  const sheet = (
    <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={`Weather for ${title}`} onClick={(e) => e.target === e.currentTarget && close()}>
      <div className="wd-sheet">
        <div className="wd-head">
          <WeatherIcon condition={f.condition} size={56} strokeWidth={1.5} />
          <div className="wd-title">
            <span className="wd-day">{isToday ? 'Today' : parseDay(day).toLocaleDateString([], { weekday: 'long' })}</span>
            <span className="wd-date">
              {title}
              {place ? ' · ' + place : ''}
            </span>
          </div>
          <div className="wd-hl">
            <span className="wd-hl-main">
              {deg(f.high)} <span className="wd-dim">/ {deg(f.low)}</span>
            </span>
            <span className="wd-dim">
              {CONDITION[f.condition] || ''}
              {num(f.feelsHigh) || num(f.feelsLow) ? ` · feels ${deg(f.feelsHigh)} / ${deg(f.feelsLow)}` : ''}
            </span>
          </div>
          <button type="button" className="wd-close" onClick={close} aria-label="Close">
            <Icon name="x" size={26} strokeWidth={2.2} />
          </button>
        </div>

        <div className="wd-chips" role="tablist" aria-label="Days">
          {forecast.map((d) => (
            <button
              key={d.date}
              type="button"
              role="tab"
              aria-selected={d.date === day}
              className={'wd-chip' + (d.date === day ? ' is-on' : '')}
              onClick={() => {
                setDay(d.date);
                setFocusTime(null);
              }}
            >
              <span className="wd-chip-day">{d.date === today ? 'Today' : d.day}</span>
              <WeatherIcon condition={d.condition} size={24} strokeWidth={1.8} />
              <span className="wd-chip-t">
                {deg(d.high)} <span className="wd-dim">{deg(d.low)}</span>
              </span>            </button>
          ))}
        </div>

        {alerts.length ? (
          <div className="wd-alerts">
            {alerts.slice(0, 2).map((a) => (
              <button key={a.id} type="button" className={'wd-alert wx-c--' + (a.colour || 'yellow') + (openAlert === a.id ? ' is-open' : '')} onClick={() => setOpenAlert(openAlert === a.id ? null : a.id)} aria-expanded={openAlert === a.id}>
                <span className="wd-alert-line">
                  <Icon name="alert" size={18} strokeWidth={2.2} />
                  <strong>{a.name || 'Weather alert'}</strong>
                  <span className="wd-alert-meta">{[a.area, fmtUntil(a.expires)].filter(Boolean).join(' · ')}</span>
                  <span className="wd-alert-more">{openAlert === a.id ? 'Less' : 'Details'}</span>
                </span>
                {openAlert === a.id ? <span className="wd-alert-text">{a.text || 'No details given.'}</span> : null}
              </button>
            ))}
          </div>
        ) : null}

        <section className="wd-chart-wrap" aria-label="Hour by hour">
          <div className="wd-legend">
            <span>Hour by hour</span>
            <span className="wd-key">
              <i className="wd-key-temp" />
              Temperature
            </span>
            <span className="wd-key">
              <i style={{ background: RAIN }} />
              Rain mm
            </span>
            <span className="wd-key">
              <i style={{ background: SNOW }} />
              Snow cm
            </span>
            <span className="wd-key">
              <i className="wd-key-chance" />
              Chance %
            </span>
          </div>
          {hours.length ? <DayChart key={day} hours={hours} now={isToday ? now : -1} focus={focus} win={win} /> : <div className="wd-none">No hour-by-hour forecast for this day yet.</div>}
        </section>

        <div className="wd-cards">
          {rain !== null && !(rain === 0 && num(f.snow) && f.snow > 0) ? <Card label="Rain" value={one(rain)} unit=" mm" sub={num(f.precip) && f.precip > rain + 0.05 ? `${one(f.precip)} mm in all` : null} /> : null}
          {num(f.snow) && (f.snow > 0 || nearFreezing) ? <Card label="Snow" value={one(f.snow)} unit=" cm" /> : null}
          {num(f.precipChance) ? <Card label="Chance" value={Math.round(f.precipChance)} unit="%" sub="of rain or snow" /> : null}
          {num(f.precipHours) ? <Card label="Wet hours" value={Math.round(f.precipHours)} unit=" h" sub="with rain or snow" /> : null}
          {num(f.wind) ? (
            <Card label="Wind" value={Math.round(f.wind)} unit=" km/h" sub={[num(f.gusts) ? `Gusts ${Math.round(f.gusts)}` : '', num(f.windDirection) ? 'from ' + compass(f.windDirection) : ''].filter(Boolean).join(' · ')}>
              {num(f.windDirection) ? (
                <svg className="wd-arrow" width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" style={{ transform: `rotate(${f.windDirection + 180}deg)` }}>
                  <path d="M12 3v18M12 3l-5 6M12 3l5 6" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              ) : null}
            </Card>
          ) : null}
          {num(f.uv) ? <Card label="UV index" value={Math.round(f.uv)} sub={uvWord(f.uv)} /> : null}
          {f.sunrise || f.sunset ? (
            <div className="wd-card">
              <span className="wd-card-label">Sunrise · Sunset</span>
              <span className="wd-card-sun">
                <span>
                  <Icon name="sun" size={16} /> {clock(f.sunrise)}
                </span>
                <span>
                  <Icon name="moon" size={16} /> {clock(f.sunset)}
                </span>
              </span>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
  return host ? createPortal(sheet, host) : sheet;
}
