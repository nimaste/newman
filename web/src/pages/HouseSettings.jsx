// Settings: how the whole house behaves (GET/PATCH api/settings/house), one tab per section. Every row is
// a name and a line of help on the left, its control on the right; a change saves as it's made (sliders
// once they settle). Only an admin can change them; everyone else sees them. The Today tab is one card
// (GET/PATCH api/today/settings): the wall's rain heads-up, when evening heads-ups start, and which
// calendars Today's tasks come from.

import { useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { useLive } from '../live.js';
import { useAuth } from '../auth.jsx';
import { ErrorBox, Loading, Segmented, Tabs } from '../components/Controls.jsx';
import { cameraTiles } from '../components/CameraGrid.jsx';
import { minutesLabel, openMinuteChoices } from '../components/DeviceWarnings.jsx';

const TABS = [
  { id: 'devices', label: 'Devices' },
  { id: 'lights', label: 'Lights & remotes' },
  { id: 'climate', label: 'Climate' },
  { id: 'cameras', label: 'Cameras' },
  { id: 'history', label: 'Data & history' },
  { id: 'today', label: 'Today' },
];

const COOLDOWNS = [
  { v: 0, label: 'No wait' },
  { v: 1, label: '1 minute' },
  { v: 5, label: '5 minutes' },
  { v: 15, label: '15 minutes' },
  { v: 30, label: '30 minutes' },
  { v: 60, label: '1 hour' },
];

function Row({ name, help, htmlFor, children }) {
  return (
    <div className="set-row">
      <div className="set-text">
        {htmlFor ? (
          <label className="set-name" htmlFor={htmlFor}>
            {name}
          </label>
        ) : (
          <span className="set-name">{name}</span>
        )}
        {help ? <span className="set-help">{help}</span> : null}
      </div>
      <div className="set-control">{children}</div>
    </div>
  );
}

/** A slider whose value shows beside it; it saves once dragging stops (and on keyboard changes, after a pause). */
function Slider({ id, value, min, max, step, format, onSave, disabled }) {
  const [v, setV] = useState(value);
  const timer = useRef(null);
  useEffect(() => setV(value), [value]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const change = (n) => {
    setV(n);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => n !== value && onSave(n), 450);
  };
  return (
    <span className="set-range">
      <input id={id} type="range" min={min} max={max} step={step} value={v} disabled={disabled} onChange={(e) => change(Number(e.target.value))} />
      <span className="set-value">{format(v)}</span>
    </span>
  );
}

/** A number box that saves when it loses focus (or Enter). */
function NumberBox({ value, onSave, label, disabled, unit = '°' }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  const commit = () => {
    const n = Number(String(v).replace(',', '.').replace(/[^0-9.-]/g, ''));
    if (!Number.isFinite(n)) return setV(String(value));
    if (n !== value) onSave(n);
  };
  return (
    <span className="set-num">
      <input
        className="input input--sm set-num-input"
        aria-label={label}
        inputMode="decimal"
        value={v}
        disabled={disabled}
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      />
      <span className="set-unit">{unit}</span>
    </span>
  );
}

function Swatch({ color, label, onSave, disabled }) {
  return (
    <label className="set-swatch" style={{ background: color }} title={label}>
      <span className="sr-only">{label}</span>
      <input type="color" value={color} disabled={disabled} onChange={(e) => onSave(e.target.value)} />
    </label>
  );
}

export default function HouseSettings() {
  const { isAdmin } = useAuth();
  const hub = useHub();
  const res = useApi('api/settings/house');
  const [tab, setTab] = useState('devices');
  const s = res.data;

  useLive((m) => {
    if (m.type === 'settings-house' && m.settings) res.setData(m.settings);
  });

  const save = async (section, values) => {
    try {
      const out = await api.patch('api/settings/house', { [section]: values });
      res.setData(out);
    } catch {
      res.reload(); // the toast said why; show what's saved
    }
  };

  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Settings</h1>
        <span className="device-meta2">How the whole house behaves. Changes save as you make them.</span>
      </div>
      <section className="pane set-pane" aria-label="Settings">
        <div className="conn-tabs">
          <Tabs label="Settings sections" value={tab} onChange={setTab} tabs={TABS} />
        </div>
        <div className="pane-body">
          {!s ? (
            res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />
          ) : (
            <div className="set-list">
              {!isAdmin ? <p className="help set-note">Only an admin can change these.</p> : null}
              {tab === 'devices' ? <DevicesTab s={s} save={save} disabled={!isAdmin} hub={hub} /> : null}
              {tab === 'lights' ? <LightsTab s={s} save={save} disabled={!isAdmin} /> : null}
              {tab === 'climate' ? <ClimateTab s={s} save={save} disabled={!isAdmin} /> : null}
              {tab === 'cameras' ? <CamerasTab s={s} save={save} disabled={!isAdmin} hub={hub} /> : null}
              {tab === 'history' ? <HistoryTab s={s} save={save} disabled={!isAdmin} /> : null}
              {tab === 'today' ? <TodayTab disabled={!isAdmin} /> : null}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function DevicesTab({ s, save, disabled, hub }) {
  // Devices that report a battery, and which of them use their own warning level.
  const [own, setOwn] = useState(null);
  const batteryIds = (hub.entities || []).filter((e) => e.attrs && typeof e.attrs.batteryLevel === 'number').map((e) => e.id).join(',');
  useEffect(() => {
    let alive = true;
    const ids = batteryIds ? batteryIds.split(',') : [];
    Promise.all(ids.map((id) => api.get('api/entities/' + enc(id) + '/warnings', { quiet: true }).then((w) => [id, w]).catch(() => [id, null]))).then((rows) => {
      if (alive) setOwn(rows.filter(([, w]) => w && typeof w.batteryLow === 'number').map(([id, w]) => ({ id, level: w.batteryLow })));
    });
    return () => {
      alive = false;
    };
  }, [batteryIds]);
  const name = (id) => ((hub.entities || []).find((e) => e.id === id) || {}).name || id;
  return (
    <>
      <Row name="Low battery warning" htmlFor="set-bat" help="For every device. A device's own Warnings tab can set its own level.">
        <Slider id="set-bat" value={s.devices.batteryLow} min={5} max={50} step={1} format={(v) => v + '%'} disabled={disabled} onSave={(v) => save('devices', { batteryLow: v })} />
      </Row>
      <Row name="Left open or unlocked" htmlFor="set-open" help="Doors, windows, locks and the garage: when to tell you one has been left open or unlocked.">
        <select id="set-open" className="input input--sm" value={s.devices.leftOpenMinutes} disabled={disabled} onChange={(e) => save('devices', { leftOpenMinutes: Number(e.target.value) })}>
          {openMinuteChoices(s.devices.leftOpenMinutes).map((n) => (
            <option key={n} value={n}>
              {minutesLabel(n)}
            </option>
          ))}
        </select>
      </Row>
      <Row name="Devices using their own level" help={own === null ? 'Checking…' : own.length ? null : 'None — every device uses the level above.'}>
        {own && own.length ? (
          <span className="set-links">
            {own.map((o) => (
              <a key={o.id} href={'#/devices/' + enc(o.id) + '?tab=warnings'}>
                {name(o.id)} {o.level}%
              </a>
            ))}
          </span>
        ) : null}
      </Row>
    </>
  );
}

function LightsTab({ s, save, disabled }) {
  return (
    <>
      <Row name="Hold-to-dim speed" htmlFor="set-ramp" help="How long holding a button takes from dark to full.">
        <Slider id="set-ramp" value={s.lights.rampSeconds} min={3} max={6} step={0.5} format={(v) => v + ' s'} disabled={disabled} onSave={(v) => save('lights', { rampSeconds: v })} />
      </Row>
      <Row name="Raise / lower step" help="One tap changes brightness by this much. A remote's Controls can override it.">
        <Segmented
          label="Raise / lower step"
          value={s.lights.step}
          options={[5, 10, 20].map((n) => ({ id: n, label: n + '%', disabled }))}
          onChange={(v) => save('lights', { step: v })}
        />
      </Row>
      <Row name="A press counts as a hold after" htmlFor="set-hold" help="Lutron Picos. Matter buttons like the BILRESA decide this themselves.">
        <Slider id="set-hold" value={s.lights.holdSeconds} min={0.3} max={1.5} step={0.1} format={(v) => Number(v).toFixed(1) + ' s'} disabled={disabled} onSave={(v) => save('lights', { holdSeconds: Math.round(v * 10) / 10 })} />
      </Row>
    </>
  );
}

function ClimateTab({ s, save, disabled }) {
  const [lo, hi] = s.climate.range;
  const bands = s.climate.bands;
  const cool = bands.find((b) => b.below !== undefined) || bands[0];
  const warm = bands.find((b) => b.above !== undefined) || bands[bands.length - 1];
  const mid = bands.find((b) => b !== cool && b !== warm) || bands[1];
  const setBand = (band, change) => save('climate', { bands: bands.map((b) => (b === band ? { ...b, ...change } : b)) });
  return (
    <>
      <Row name="Room bar range" help="An empty bar is the low end, a full bar the high end.">
        <span className="set-inline">
          <NumberBox label="Lowest" value={lo} disabled={disabled} onSave={(v) => save('climate', { range: [v, hi] })} />
          <span className="set-word">to</span>
          <NumberBox label="Highest" value={hi} disabled={disabled} onSave={(v) => save('climate', { range: [lo, v] })} />
        </span>
      </Row>
      <Row name={cool.name} help="Rooms below this. Tap the colour to change it.">
        <span className="set-inline">
          <Swatch color={cool.color} label={cool.name + ' colour'} disabled={disabled} onSave={(c) => setBand(cool, { color: c })} />
          <span className="set-word">below</span>
          <NumberBox label={cool.name + ' below'} value={cool.below} disabled={disabled} onSave={(v) => setBand(cool, { below: v })} />
        </span>
      </Row>
      <Row name={mid.name} help="Everything in between.">
        <span className="set-inline">
          <Swatch color={mid.color} label={mid.name + ' colour'} disabled={disabled} onSave={(c) => setBand(mid, { color: c })} />
          <span className="set-word">
            {cool.below}° – {warm.above}°
          </span>
        </span>
      </Row>
      <Row name={warm.name} help="Rooms above this.">
        <span className="set-inline">
          <Swatch color={warm.color} label={warm.name + ' colour'} disabled={disabled} onSave={(c) => setBand(warm, { color: c })} />
          <span className="set-word">above</span>
          <NumberBox label={warm.name + ' above'} value={warm.above} disabled={disabled} onSave={(v) => setBand(warm, { above: v })} />
        </span>
      </Row>
    </>
  );
}

function CamerasTab({ s, save, disabled, hub }) {
  const cams = cameraTiles(hub.entities || [], hub.kindsById || {});
  const c = s.cameras;
  const featured = c.mode === 'featured';
  return (
    <>
      <Row name="Big camera on the phone" help="The large picture at the top of the Cameras tab.">
        <span className="set-stack">
          <Segmented
            label="Big camera"
            value={c.mode}
            options={[
              { id: 'latest', label: 'Latest activity', disabled },
              { id: 'featured', label: 'Featured camera', disabled: disabled || !cams.length },
            ]}
            onChange={(v) => save('cameras', v === 'featured' ? { mode: 'featured', featured: c.featured || (cams[0] && cams[0].id) || null } : { mode: 'latest' })}
          />
          {featured ? (
            <select className="input" aria-label="Featured camera" value={c.featured || ''} disabled={disabled} onChange={(e) => save('cameras', { mode: 'featured', featured: e.target.value })}>
              {cams.map((cam) => (
                <option key={cam.id} value={cam.id}>
                  {cam.name}
                </option>
              ))}
            </select>
          ) : null}
        </span>
      </Row>
      <Row name="Wait before alerting again" htmlFor="set-cool" help="For each camera, unless an automation sets its own.">
        <select id="set-cool" className="input" value={c.cooldownMinutes} disabled={disabled} onChange={(e) => save('cameras', { cooldownMinutes: Number(e.target.value) })}>
          {COOLDOWNS.map((o) => (
            <option key={o.v} value={o.v}>
              {o.label}
            </option>
          ))}
        </select>
      </Row>
      <Row name="Keep copies of watched clips" htmlFor="set-clips" help="Newman keeps a copy of each clip you play so it plays and skips smoothly on every device. Frigate keeps the originals.">
        <select id="set-clips" className="input" value={c.clipDays} disabled={disabled} onChange={(e) => save('cameras', { clipDays: Number(e.target.value) })}>
          {[1, 2, 3, 4, 5, 6, 7].map((d) => (
            <option key={d} value={d}>
              {d === 1 ? '1 day' : d + ' days'}
            </option>
          ))}
        </select>
      </Row>
    </>
  );
}

function HistoryTab({ s, save, disabled }) {
  const days = (v) => (v === 1 ? '1 day' : v + ' days');
  return (
    <>
      <Row name="Keep activity for" htmlFor="set-act" help="Locks, doors, alerts. Up to 90 days.">
        <Slider id="set-act" value={s.history.activityDays} min={1} max={90} step={1} format={days} disabled={disabled} onSave={(v) => save('history', { activityDays: v })} />
      </Row>
      <Row name="Keep automation history for" htmlFor="set-runs" help="Each run and what it did. Up to 90 days.">
        <Slider id="set-runs" value={s.history.runDays} min={1} max={90} step={1} format={days} disabled={disabled} onSave={(v) => save('history', { runDays: v })} />
      </Row>
    </>
  );
}

/** Settings › Today: "Today on the wall" - when rain is worth a heads-up, when evening heads-ups start, and
 * where tasks come from (the calendar server connection's calendars that hold tasks). */
function TodayTab({ disabled }) {
  const res = useApi('api/today/settings');
  const [thr, setThr] = useState(null);
  const [from, setFrom] = useState('');
  useEffect(() => {
    if (res.data) {
      setThr(Number(res.data.todayRainThreshold ?? 50));
      setFrom(res.data.todayEveningFrom || '17:00');
    }
  }, [res.data]);
  const save = async (patch) => {
    try {
      res.setData(await api.patch('api/today/settings', patch));
      toast('Today settings saved', 'success');
    } catch {
      res.reload(); // the toast said why; show what's saved
    }
  };
  if (thr === null) return res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />;
  const cals = (res.data && res.data.taskCalendars) || [];
  return (
    <section className="card set-today" aria-labelledby="set-today-title">
      <h2 id="set-today-title" className="set-today-title">
        Today on the wall
      </h2>
      <label className="set-today-label" htmlFor="set-today-rain">
        Rain heads-up when the chance is at least {thr ? thr + '%' : 'Off'}
      </label>
      <input
        id="set-today-rain"
        type="range"
        min={0}
        max={100}
        step={5}
        value={thr}
        disabled={disabled}
        aria-valuetext={thr ? thr + '%' : 'Off'}
        onChange={(e) => setThr(Number(e.target.value))}
        onPointerUp={() => save({ todayRainThreshold: thr })}
        onKeyUp={() => save({ todayRainThreshold: thr })}
      />
      <span className="set-today-help">0 turns it off.</span>
      <label className="set-today-label" htmlFor="set-today-evening">
        Evening heads-ups from
      </label>
      <input
        id="set-today-evening"
        className="input set-today-time"
        type="time"
        value={from}
        disabled={disabled}
        onChange={(e) => (setFrom(e.target.value), e.target.value && save({ todayEveningFrom: e.target.value }))}
      />
      <span className="set-today-help">From then on, Today mentions tomorrow’s garbage day and tasks due tomorrow.</span>
      <span className="set-today-label set-today-split">Tasks come from</span>
      <span className="set-today-help set-today-para">
        {cals.length
          ? `The GCloud calendars Newman sees (Connections › Calendar server): ${cals.join(', ')}. A task that belongs to someone shows on their phone; everyone’s show on the wall.`
          : 'No calendar with tasks yet. Add GCloud under Connections › Calendar server, and its calendars’ tasks show here.'}
      </span>
    </section>
  );
}
