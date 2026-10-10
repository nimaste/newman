// A wall tablet's smart charging (Household → Accounts → the tablet): the hub switches the plug it
// charges from on below one level and off above another (server/newman/charging.py), so it isn't held
// at 100 % on the wall. The charger is learned by watching which plug makes it start charging; it can
// be picked here instead. The status line is live, from the hub's devices.

import { useEffect, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { useLiveRefresh } from '../live.js';
import { Check } from '../components/Controls.jsx';

function learnedOn(ts) {
  return ts ? new Date(ts * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' }) : '';
}

function statusText(tablet, plug, chargerId) {
  if (!tablet || typeof (tablet.attrs || {}).batteryLevel !== 'number') return 'Hasn’t reported its battery yet';
  const a = tablet.attrs;
  let out = `Battery ${a.batteryLevel}%, ${a.charging ? 'charging' : 'not charging'}`;
  if (!tablet.available) out += ' (not reporting)';
  if (plug) out += plug.available ? ` · charger ${(plug.stateLabel || plug.state || '').toLowerCase()}` : ' · charger not responding';
  else if (chargerId) out += ' · charger gone';
  return out;
}

function Level({ label, value, min, max, onChange, onDone }) {
  return (
    <label className="voice-row">
      <span className="voice-label">{label}</span>
      <span className="slider voice-slider">
        <input type="range" min={min} max={max} step={1} value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))} onPointerUp={onDone} onKeyUp={onDone} />
        <span className="muted small charge-pct">{value}%</span>
      </span>
    </label>
  );
}

/** The hub's question when the tablet didn't start charging from its plug: the likely plugs, or none. */
function WhichPlug({ q, onAnswer, busy }) {
  return (
    <div className="charge-question" role="alert">
      <span className="charge-question-text">{q.text}</span>
      <span className="charge-question-choices">
        {q.choices.map((c) => (
          <button key={c.id} type="button" className="btn btn-sm2" disabled={busy} onClick={() => onAnswer(c.id)}>
            {c.name}
          </button>
        ))}
        <button type="button" className="btn btn-sm2" disabled={busy} onClick={() => onAnswer(null)}>
          None of these
        </button>
      </span>
    </div>
  );
}

export default function SmartCharging({ user }) {
  const res = useApi('api/charging/' + enc(user.id));
  const hub = useHub();
  const [s, setS] = useState(null);
  const [start, setStart] = useState(20);
  const [stop, setStop] = useState(80);
  const [answering, setAnswering] = useState(false);
  useEffect(() => {
    if (res.data) {
      setS(res.data);
      setStart(res.data.startBelow);
      setStop(res.data.stopAbove);
    }
  }, [res.data]);
  // A charger learned (or a plug switched) while this is open shows straight away.
  useLiveRefresh((m) => m.type === 'activity' && !!m.entry && !!s && m.entry.entityId === s.device, () => res.reload(), 500);
  if (!s) return <span className="muted small">{res.error ? "Couldn't load smart charging" : 'Loading…'}</span>;

  const save = async (changes, done) => {
    const before = s;
    setS({ ...s, ...changes });
    try {
      const saved = await api.patch('api/charging/' + enc(user.id), changes);
      setS(saved);
      setStart(saved.startBelow);
      setStop(saved.stopAbove);
      if (done) toast(done, 'success');
    } catch {
      setS(before);
      setStart(before.startBelow);
      setStop(before.stopAbove);
    }
  };
  const answer = async (plugId) => {
    setAnswering(true);
    try {
      const saved = await api.post('api/charging/' + enc(user.id) + '/answer', { question: s.question.id, plug: plugId });
      setS(saved);
      const c = plugId && s.question.choices.find((x) => x.id === plugId);
      toast(c ? `${user.name} charges from ${c.name}` : 'Noted', 'success');
    } catch {
      res.reload();
    } finally {
      setAnswering(false);
    }
  };
  const byId = (hub && hub.entitiesById) || {};
  const tablet = s.device ? byId[s.device] : null;
  const plug = s.charger ? byId[s.charger] : null;
  const choices = s.choices || [];
  const groups = [...new Set(choices.map((c) => c.group))];
  const known = !s.charger || choices.some((c) => c.id === s.charger);
  return (
    <div className="voice-settings charge-settings">
      <Check
        checked={s.enabled}
        onChange={(on) => save({ enabled: on }, on ? `${user.name} charges between ${s.startBelow}% and ${s.stopAbove}%` : `Smart charging is off for ${user.name}`)}
      >
        Smart charging
      </Check>
      <div className="muted small charge-status" role="status">
        {statusText(tablet, plug, s.charger)}
      </div>
      {s.question ? <WhichPlug q={s.question} onAnswer={answer} busy={answering} /> : null}
      {s.enabled ? (
        <>
          <Level label="Start charging below" value={start} min={5} max={95} onChange={(v) => setStart(Math.min(v, stop - 1))} onDone={() => start !== s.startBelow && save({ startBelow: start })} />
          <Level label="Stop charging above" value={stop} min={5} max={95} onChange={(v) => setStop(Math.max(v, start + 1))} onDone={() => stop !== s.stopAbove && save({ stopAbove: stop })} />
          <label className="voice-row">
            <span className="voice-label">Charger plug</span>
            <span className="charge-plug">
              <select
                className="select select--sm hh-fact-select"
                value={s.charger || ''}
                aria-label="Charger plug"
                onChange={(e) => {
                  const c = choices.find((x) => x.id === e.target.value);
                  save({ charger: e.target.value || null }, c ? `${user.name} charges from ${c.name}` : null);
                }}
              >
                <option value="">{s.charger ? 'None' : 'Not known yet'}</option>
                {!known ? <option value={s.charger}>{s.chargerName || 'A device that’s gone'}</option> : null}
                {groups.map((g) => (
                  <optgroup key={g} label={g}>
                    {choices
                      .filter((c) => c.group === g)
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                          {c.available ? '' : ' (offline)'}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </select>
              {s.charger && s.learned ? <span className="muted small">(learned {learnedOn(s.learnedAt)})</span> : null}
            </span>
          </label>
        </>
      ) : null}
    </div>
  );
}
