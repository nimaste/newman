// The wall's Weather screen (#/wall/weather, approved TabletWeather board): now, the next 12 hours
// with rain bars, the next 7 days (tap one for its detail), and warnings on the right.

import { useRef, useState } from 'react';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { stateLabelOf } from '../model.js';
import { WeatherIcon } from './WeatherIcon.jsx';
import { DaySheet } from './WeatherDay.jsx';

const num = (v) => typeof v === 'number' && Number.isFinite(v);
const deg = (v) => (num(v) ? Math.round(v) + '°' : '–');
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '');
const CONDITION = {
  clear: 'Clear', 'mostly-clear': 'Mostly clear', 'partly-cloudy': 'Partly cloudy', cloudy: 'Cloudy', fog: 'Fog', drizzle: 'Drizzle', rain: 'Rain',
  'freezing-rain': 'Freezing rain', snow: 'Snow', showers: 'Showers', 'snow-showers': 'Snow showers', thunderstorm: 'Thunderstorm',
};
const ALERT_COLOUR = { red: '#FF5C5C', orange: '#FF963C', yellow: '#F5D142' };

export default function WeatherView({ onBack }) {
  const { entities, kindsById } = useHub();
  const root = useRef(null);
  const [day, setDay] = useState(null);
  const e = (entities || []).find((x) => x.attrs && Array.isArray(x.attrs.forecast));
  if (!e) {
    return (
      <div className="wcal">
        <div className="wcal-main">
          <div className="wt-cams-head wcal-head">
            <button type="button" className="wt-back" onClick={onBack}>
              <Icon name="chevronLeft" size={26} strokeWidth={2.4} /> Back
            </button>
            <span className="wt-cams-title">Weather</span>
          </div>
          <div className="wcal-empty">Add a Weather connection in the Newman dashboard.</div>
        </div>
      </div>
    );
  }
  const a = e.attrs;
  const label = stateLabelOf(kindsById[e.kind], e.state) || CONDITION[e.state] || e.state || '';
  const now = Date.now();
  const hourly = (Array.isArray(a.hours) && a.hours.length ? a.hours : a.hourly) || [];
  const start = hourly.findIndex((h) => new Date(h.time).getTime() + 3600e3 > now);
  const next = (start < 0 ? [] : hourly.slice(start)).slice(0, 12);
  const days = (a.forecast || []).slice(0, 7);
  const lo = Math.min(...days.map((d) => d.low).filter(num));
  const hi = Math.max(...days.map((d) => d.high).filter(num));
  const span = hi - lo || 1;
  const today = new Date();
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  const alerts = (Array.isArray(a.alerts) ? a.alerts : []).filter((x) => !x.expires || new Date(x.expires).getTime() > now);

  return (
    <div className="wcal wwx" ref={root}>
      <div className="wcal-main">
        <div className="wt-cams-head wcal-head">
          <button type="button" className="wt-back" onClick={onBack}>
            <Icon name="chevronLeft" size={26} strokeWidth={2.4} /> Back
          </button>
          <span className="wt-cams-title">Weather · {e.name}</span>
          <span className="wt-dim-text">{a.source || 'Open-Meteo'}{a.alerts ? ' · warnings from Environment Canada' : ''}</span>
        </div>

        <section className="wwx-now">
          <WeatherIcon condition={e.state} isDay={a.isDay !== false} size={88} strokeWidth={1.4} />
          <span className="wwx-temp">{deg(a.temperature)}</span>
          <span className="wwx-now-text">
            <span className="wwx-cond">{label}</span>
            <span className="wwx-dim">
              {num(a.feelsLike) ? `Feels like ${deg(a.feelsLike)} · ` : ''}H {deg(a.high)} L {deg(a.low)}
            </span>
            <span className="wwx-dim">
              {[num(a.wind) ? `Wind ${Math.round(a.wind)} km/h` : '', num(a.gusts) ? `gusts ${Math.round(a.gusts)}` : '', num(a.humidity) ? `humidity ${Math.round(a.humidity)}%` : ''].filter(Boolean).join(' · ')}
            </span>
          </span>
          <span className="wcal-gap" />
          <span className="wwx-sun">
            {a.sunrise ? <span>Sunrise {clock(a.sunrise)}</span> : null}
            {a.sunset ? <span>Sunset {clock(a.sunset)}</span> : null}
            {num(a.uv) ? <span>UV {Math.round(a.uv)}</span> : null}
          </span>
        </section>

        <section className="wwx-hours" aria-label="Next hours">
          <span className="wwx-h">Next 12 hours</span>
          <div className="wwx-hgrid">
            {next.map((h, i) => {
              const p = num(h.precipChance) ? h.precipChance : 0;
              return (
                <div key={h.time} className={'wwx-hour' + (i === 0 ? ' is-now' : '')}>
                  <span className="wwx-ht">{i === 0 ? 'Now' : new Date(h.time).toLocaleTimeString([], { hour: 'numeric' }).replace(/:00|\s?[AP]M/g, '')}</span>
                  <span className="wwx-htemp">{deg(h.temperature)}</span>
                  <span className="wwx-rainbar">
                    <span style={{ height: Math.max(0, Math.min(100, p)) + '%' }} />
                  </span>
                  <span className={'wwx-rain' + (p >= 50 ? ' is-high' : '')}>{p >= 10 ? Math.round(p) + '%' : ''}</span>
                </div>
              );
            })}
          </div>
        </section>

        <section className="wwx-days" aria-label="Next 7 days">
          {days.map((d) => (
            <button key={d.date} type="button" className="wwx-day" onClick={() => setDay(d.date)} aria-label={`${d.day}: details`}>
              <span className="wwx-dname">{d.date === todayKey ? 'Today' : new Date(d.date + 'T12:00:00').toLocaleDateString([], { weekday: 'long' })}</span>
              <span className="wwx-dcond">
                <WeatherIcon condition={d.condition} size={22} strokeWidth={1.8} />
                {CONDITION[d.condition] || d.condition}
              </span>
              <span className="wwx-drain">{num(d.precipChance) && d.precipChance >= 10 ? Math.round(d.precipChance) + '%' : ''}</span>
              <span className="wx-bar">
                <span style={{ left: ((d.low - lo) / span) * 100 + '%', width: Math.max(4, ((d.high - d.low) / span) * 100) + '%' }} />
              </span>
              <span className="wwx-drange">
                {deg(d.low)} {deg(d.high)}
              </span>
            </button>
          ))}
        </section>
      </div>

      <aside className="wcal-side" aria-label="Warnings">
        <span className="wwx-side-title">Warnings</span>
        <div className="wcal-list">
          {!alerts.length ? <div className="wcal-empty">No warnings right now.</div> : null}
          {alerts.map((al) => (
            <div key={al.id} className="wwx-alert" style={{ '--al': ALERT_COLOUR[al.colour] || '#F5B942' }}>
              <span className="wwx-alert-name">{al.name || 'Weather alert'}</span>
              {al.text ? <span className="wwx-alert-text">{al.text}</span> : null}
              <span className="wwx-alert-meta">
                {[al.area, al.expires ? 'until ' + new Date(al.expires).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : ''].filter(Boolean).join(' · ')}
              </span>
            </div>
          ))}
        </div>
        <span className="wtodo-foot">A new warning also shows in Today's Heads-up, and can start an automation.</span>
      </aside>
      {day ? <DaySheet place={e.name} attrs={a} date={day} host={root.current && root.current.closest('.wall')} onClose={() => setDay(null)} /> : null}
    </div>
  );
}
