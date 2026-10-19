// A device's Warnings tab: the low-battery level it warns below and, for doors, windows and locks, how long
// it may be left open or unlocked (each the house setting or its own), and a
// switch for every problem it can report (GET/PUT api/entities/{id}/warnings). A problem switched off
// never shows as needing attention; Mute (on the problem itself) is the short-term version.

import { useEffect, useRef, useState } from 'react';
import { api, enc } from '../api.js';
import { ErrorBox, Loading, Switch } from './Controls.jsx';

/** The device's warnings, or null while loading (shared with DeviceDetail to decide whether the tab shows). */
export function useDeviceWarnings(entityId) {
  const [state, setState] = useState({ data: undefined, error: null });
  const load = () =>
    api
      .get('api/entities/' + enc(entityId) + '/warnings', { quiet: true })
      .then((data) => setState({ data, error: null }))
      .catch((error) => setState({ data: null, error }));
  useEffect(() => {
    setState({ data: undefined, error: null });
    load();
  }, [entityId]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, reload: load, setData: (data) => setState({ data, error: null }) };
}

/** Whether a device has anything for the Warnings tab. */
export function hasWarnings(w) {
  return !!(w && (w.hasBattery || w.hasSecureState || (w.warnings || []).length));
}

/** The left-open wait choices, plus the current value when it isn't one of them (set through the API). */
export function openMinuteChoices(current) {
  const list = [5, 10, 15, 30, 60, 120];
  return typeof current === 'number' && !list.includes(current) ? [...list, current].sort((a, b) => a - b) : list;
}

export function minutesLabel(n) {
  if (n >= 60 && n % 60 === 0) return n === 60 ? '1 hour' : `${n / 60} hours`;
  return `${n} min`;
}

export default function DeviceWarnings({ entity, res, isAdmin }) {
  const w = res.data;
  const [level, setLevel] = useState(null);
  const timer = useRef(null);
  useEffect(() => setLevel(w && typeof w.batteryLow === 'number' ? w.batteryLow : null), [w]);
  useEffect(() => () => clearTimeout(timer.current), []);
  if (w === undefined) return <Loading />;
  if (!w) return <ErrorBox error={res.error} onRetry={res.reload} />;

  const put = async (body) => {
    try {
      res.setData(await api.put('api/entities/' + enc(entity.id) + '/warnings', body));
    } catch {
      res.reload();
    }
  };
  const own = typeof w.batteryLow === 'number';
  const ownOpen = typeof w.leftOpenMinutes === 'number';
  const slide = (n) => {
    setLevel(n);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => put({ batteryLow: n }), 450);
  };

  return (
    <div className="set-list">
      {!isAdmin ? <p className="help set-note">Only an admin can change these.</p> : null}
      {w.hasBattery ? (
        <div className="set-row">
          <div className="set-text">
            <span className="set-name">Low battery</span>
            <span className="set-help">Warn when its battery is below this.</span>
          </div>
          <div className="set-control">
            <span className="set-stack">
              <span className="radio-stack" role="radiogroup" aria-label="Low battery level">
                <label className="radio-line">
                  <input type="radio" name="bl" checked={!own} disabled={!isAdmin} onChange={() => put({ batteryLow: null })} />
                  Use the house setting ({w.houseBatteryLow}%)
                </label>
                <label className="radio-line">
                  <input type="radio" name="bl" checked={own} disabled={!isAdmin} onChange={() => put({ batteryLow: w.houseBatteryLow })} />
                  Its own level
                </label>
              </span>
              {own ? (
                <span className="set-range">
                  <input type="range" min={5} max={50} step={1} value={level ?? w.batteryLow} aria-label="Its own level" disabled={!isAdmin} onChange={(e) => slide(Number(e.target.value))} />
                  <span className="set-value">{level ?? w.batteryLow}%</span>
                </span>
              ) : null}
            </span>
          </div>
        </div>
      ) : null}
      {w.hasSecureState ? (
        <div className="set-row">
          <div className="set-text">
            <span className="set-name">Left open or unlocked</span>
            <span className="set-help">Tell you when it has been left open or unlocked this long.</span>
          </div>
          <div className="set-control">
            <span className="set-stack">
              <span className="radio-stack" role="radiogroup" aria-label="Left open or unlocked">
                <label className="radio-line">
                  <input type="radio" name="lo" checked={!ownOpen} disabled={!isAdmin} onChange={() => put({ leftOpenMinutes: null })} />
                  Use the house setting ({minutesLabel(w.houseLeftOpenMinutes)})
                </label>
                <label className="radio-line">
                  <input type="radio" name="lo" checked={ownOpen} disabled={!isAdmin} onChange={() => put({ leftOpenMinutes: w.houseLeftOpenMinutes })} />
                  Its own time
                </label>
              </span>
              {ownOpen ? (
                <select className="input input--sm" aria-label="Its own time" value={w.leftOpenMinutes} disabled={!isAdmin} onChange={(e) => put({ leftOpenMinutes: Number(e.target.value) })}>
                  {openMinuteChoices(w.leftOpenMinutes).map((n) => (
                    <option key={n} value={n}>
                      {minutesLabel(n)}
                    </option>
                  ))}
                </select>
              ) : null}
            </span>
          </div>
        </div>
      ) : null}
      {(w.warnings || []).length ? (
        <>
          <div className="set-row set-row--head">
            <div className="set-text">
              <span className="set-name">Warn me about</span>
              <span className="set-help">Every problem this device can report. Switch one off and it never shows as needing attention.</span>
            </div>
          </div>
          {w.warnings.map((it) => (
            <div key={it.id} className="set-row set-row--tight">
              <div className="set-text">
                <span className="set-name set-name--sm">{it.label}</span>
              </div>
              <div className="set-control set-control--end">
                <Switch checked={it.on} disabled={!isAdmin} ariaLabel={`Warn me about ${it.label}`} onChange={(v) => put({ warnings: { [it.id]: v } })} />
              </div>
            </div>
          ))}
        </>
      ) : null}
    </div>
  );
}
