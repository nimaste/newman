// The round drag dial (TabletClimate board) for any "number" command: a thermostat's target, …
// Range/step/unit come from the kind's command in /api/meta, the value from attrs[command.attr].
// Dragging sends one command ~600 ms after release - on the big dial and the compact Overview one alike.

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { deviceIcon, stateChoices, stateLabelOf } from '../model.js';
import { Icon } from '../icons.jsx';
import { formatPropValue } from '../automation.js';
import { useWallCommand } from './PinPad.jsx';

// Colours by mode (state id); anything else gets a calm neutral. (Shared with PhoneTiles.jsx.)
export const MODE_COLOR = { heat: '#FF8A5B', cool: '#5AA9FF', auto: '#3CCFB4', heat_cool: '#3CCFB4', off: '#6B7599', eco: '#3CCFB4', fan_only: '#A9B4D6' };
export const MODE_WORD = { heat: 'Heat to', cool: 'Cool to', auto: 'Keep at', heat_cool: 'Keep at' };
export const MODE_ICON = {
  off: 'M12 3v9M6.5 6.5a8 8 0 1 0 11 0',
  heat: 'M12 3c1 4 5 5 5 10a5 5 0 0 1-10 0c0-3 2-4 2-7 1.5 1 2.5 2.5 3 4 .5-3-.5-5 0-7z',
  cool: 'M12 3v18M4.5 7.5l15 9M19.5 7.5l-15 9',
  auto: 'M4 12a8 8 0 0 1 14-5l2-2v6h-6l2-2A5 5 0 0 0 7 12M20 12a8 8 0 0 1-14 5l-2 2v-6h6l-2 2a5 5 0 0 0 9-3',
};
export const NEUTRAL = '#C9D4F5';
export const TRACK = 'rgba(var(--wt-fg, 255, 255, 255), 0.1)';
export const KNOB = 'var(--wt-knob, #EEF2FF)';
const C = 180;
const R = 150;

export function pol(deg, r = R) {
  const a = (deg * Math.PI) / 180;
  return [C + r * Math.cos(a), C + r * Math.sin(a)];
}
export function arc(a0, a1) {
  const p0 = pol(a0);
  const p1 = pol(a1);
  return `M ${p0[0].toFixed(1)} ${p0[1].toFixed(1)} A ${R} ${R} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${p1[0].toFixed(1)} ${p1[1].toFixed(1)}`;
}

export default function Dial({ entity: e, kind, config, compact, tall, side }) {
  const { run } = useWallCommand();
  const cmd = ((kind && kind.commands) || []).find((c) => c.id === config.command) || null;
  const modeCmd = config.modeCommand ? ((kind && kind.commands) || []).find((c) => c.id === config.modeCommand) : null;
  const min = cmd && typeof cmd.min === 'number' ? cmd.min : 0;
  const max = cmd && typeof cmd.max === 'number' ? cmd.max : 100;
  const step = (cmd && cmd.step) || 1;
  const unit = (cmd && cmd.unit) || '';
  const current = cmd && cmd.attr && e && e.attrs && typeof e.attrs[cmd.attr] === 'number' ? e.attrs[cmd.attr] : null;
  const [target, setTarget] = useState(current);
  const dragging = useRef(false);
  const pending = useRef(false);
  const timer = useRef(null);
  const [sheet, setSheet] = useState(false);
  useEffect(() => {
    if (!dragging.current && !pending.current) setTarget(current);
  }, [current]);
  useEffect(() => () => clearTimeout(timer.current), []);

  if (!e || !cmd) return <div className="wt-empty">This dial's device is missing.</div>;

  // Mode: the device's state when the mode command is a "state" command.
  const modes = modeCmd ? stateChoices(kind) : [];
  const mode = modeCmd ? e.state : null;
  const off = mode === 'off';
  const color = mode ? MODE_COLOR[mode] || NEUTRAL : NEUTRAL;
  const word = mode && MODE_WORD[mode] ? MODE_WORD[mode] : off ? 'Off' : cmd.label;
  const props = e.properties || [];
  const nowProp = props.find((p) => p.type === 'number' && p.unit === unit && p.attr !== cmd.attr);
  const pctProp = props.find((p) => p.type === 'number' && p.unit === '%' && p.attr !== cmd.attr);
  const activity = props.find((p) => p.type === 'text' && (p.options || []).length && p.attr);
  const status = [mode ? stateLabelOf(kind, mode) : null, activity && activity.value ? formatPropValue(activity, activity.value).toLowerCase() : null].filter(Boolean).join(' · ');
  const nowLine = [nowProp ? `Now ${formatPropValue(nowProp, nowProp.value)}` : null, pctProp && !compact ? `${pctProp.label} ${formatPropValue(pctProp, pctProp.value)}` : pctProp ? formatPropValue(pctProp, pctProp.value) : null].filter(Boolean).join(' · ');

  const clamp = (v) => Math.min(max, Math.max(min, Math.round(v / step) * step));
  const angle = (v) => 135 + ((clamp(v) - min) / (max - min || 1)) * 270;
  const commit = (v) => {
    pending.current = true;
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      pending.current = false;
      if (v !== current) await run(e, cmd, v);
    }, 600);
  };
  const setFrom = (ev) => {
    const r = ev.currentTarget.getBoundingClientRect();
    const x = ((ev.clientX - r.left) * 360) / r.width;
    const y = ((ev.clientY - r.top) * 360) / r.height;
    let a = (Math.atan2(y - C, x - C) * 180) / Math.PI;
    if (a < 0) a += 360;
    if (a < 135) a += 360;
    if (a > 405) a = a > 450 ? 135 : 405;
    const v = clamp(min + ((a - 135) / 270) * (max - min));
    setTarget(v);
    return v;
  };
  const decimals = String(step).includes('.') ? String(step).split('.')[1].length : 0;
  const shown = typeof target === 'number' ? target : current;
  const end = typeof shown === 'number' ? angle(shown) : 135;
  const k = pol(end);
  const now = nowProp && typeof nowProp.value === 'number' ? pol(angle(nowProp.value)) : null;
  const bump = (d) => {
    if (off || typeof shown !== 'number') return;
    const v = clamp(shown + d);
    setTarget(v);
    commit(v);
  };

  // The ring's look comes from the wall theme (theme.css); Midnight leaves these at their defaults.
  const sw = compact ? 'var(--wt-dial-w-compact, 20)' : 'var(--wt-dial-w, 26)';
  const ring = (
    <div className="wt-dial-ring">
      <svg
        viewBox="0 0 360 360"
        className="wt-dial-svg"
        onPointerDown={(ev) => {
          if (off) return;
          ev.currentTarget.setPointerCapture(ev.pointerId);
          dragging.current = true;
          setFrom(ev);
        }}
        onPointerMove={(ev) => dragging.current && setFrom(ev)}
        onPointerUp={(ev) => {
          if (!dragging.current) return;
          dragging.current = false;
          commit(setFrom(ev));
        }}
        onPointerCancel={() => (dragging.current = false)}
        role="slider"
        aria-label={`${cmd.label} for ${e.name}`}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={typeof shown === 'number' ? shown : undefined}
      >
        <path d={arc(135, 405)} fill="none" style={{ stroke: TRACK, strokeWidth: sw }} strokeLinecap="round" />
        {!off && typeof shown === 'number' ? <path d={arc(135, Math.max(135.1, end))} className="wt-arc-fill" fill="none" stroke={color} style={{ strokeWidth: sw }} strokeLinecap="round" /> : null}
        {/* the candy cane's stripes (shown by the Christmas theme only, theme.css) */}
        {!off && typeof shown === 'number' ? <path d={arc(135, Math.max(135.1, end))} className="wt-arc-stripe" fill="none" style={{ strokeWidth: sw }} /> : null}
        {now && !compact ? <circle cx={now[0]} cy={now[1]} r="5" style={{ fill: KNOB }} opacity="0.7" /> : null}
        {!off && typeof shown === 'number' ? (
          <>
            <circle cx={k[0]} cy={k[1]} r={compact ? 15 : 19} style={{ fill: KNOB }} />
            {!compact ? <circle cx={k[0]} cy={k[1]} r="8" fill={color} /> : null}
          </>
        ) : null}
      </svg>
      <div className="wt-dial-center">
        <span className="wt-dial-word" style={{ color }}>
          {word}
        </span>
        <span className="wt-dial-value">{off || typeof shown !== 'number' ? '—' : `${shown.toFixed(decimals)}${unit.startsWith('°') ? '°' : ''}`}</span>
        {nowLine && !side ? <span className="wt-dial-now">{nowLine}</span> : null}
      </div>
    </div>
  );

  const modeButtons = (mini) =>
    modes.map((m) => {
      const on = m.id === mode;
      const c = MODE_COLOR[m.id] || NEUTRAL;
      return (
        <button key={m.id} type="button" aria-pressed={on} className={'wt-mode' + (mini ? ' wt-mode--mini' : '')} style={on ? { background: c, color: '#0B1026' } : undefined} onClick={() => !on && run(e, modeCmd, m.id)}>
          <svg width={mini ? 16 : 22} height={mini ? 16 : 22} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d={MODE_ICON[m.id] || 'M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0'} />
          </svg>
          {m.label}
        </button>
      );
    });

  if (side) {
    // The compact wall's page of its own: the name, now, what it's set to and the modes beside the dial.
    const value = off || typeof shown !== 'number' ? null : `${shown.toFixed(decimals)} ${unit}`.trim();
    const doing = activity && activity.value ? formatPropValue(activity, activity.value).toLowerCase() : '';
    return (
      <div className="wt-dial wt-dial--side">
        <div className="wt-dial-info">
          <div className="wt-tile-head">
            <span className="wc-head">
              <Icon name={deviceIcon(e, kind)} size={22} />
              {e.name}
            </span>
          </div>
          {nowLine ? <span className="wt-dial-line wt-dim-text">{nowLine}</span> : null}
          <span className="wt-dial-line">
            {word}
            {value ? (
              <>
                {' '}
                <b>{value}</b>
              </>
            ) : null}
            {doing ? ` · ${doing}` : ''}
          </span>
          {modes.length ? (
            <div className="wt-modes" role="group" aria-label={modeCmd.label}>
              {modeButtons(false)}
            </div>
          ) : null}
        </div>
        <div className="wt-dial-box">{ring}</div>
      </div>
    );
  }

  if (compact) {
    // The small Overview dial: the mode label opens a sheet with the modes and −/+; a tall tile
    // also shows small mode chips under the dial.
    return (
      <div className="wt-dial wt-dial--compact">
        <div className="wt-tile-head">
          <span>{e.name}</span>
          <button type="button" className="wt-tile-note wt-dial-modebtn" style={{ color }} onClick={() => setSheet(true)} aria-haspopup="dialog" aria-label={`${status || cmd.label}: change`}>
            {status || cmd.label} <span aria-hidden="true">▾</span>
          </button>
        </div>
        <div className="wt-dial-box">{ring}</div>
        {tall && modes.length ? (
          <div className="wt-modes wt-modes--mini" role="group" aria-label={modeCmd.label}>
            {modeButtons(true)}
          </div>
        ) : null}
        {sheet
          ? createPortal(
              <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={e.name} onClick={(ev) => ev.target === ev.currentTarget && setSheet(false)}>
                <div className="wt-confirm wt-devsheet wt-dialsheet">
                  <div className="wt-sheet-title">{e.name}</div>
                  <div className="wt-sheet-sub" style={{ color }}>
                    {[status, nowLine].filter(Boolean).join(' · ')}
                  </div>
                  <div className="wt-dialsheet-row">
                    <button type="button" className="wt-round-btn" disabled={off} onClick={() => bump(-step)} aria-label={`${step} ${unit} lower`}>
                      <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                        <path d="M5 12h14" />
                      </svg>
                    </button>
                    <span className="wt-dialsheet-value">
                      <span className="wt-dial-word" style={{ color }}>
                        {word}
                      </span>
                      <span className="wt-dial-value">{off || typeof shown !== 'number' ? '—' : `${shown.toFixed(decimals)}${unit.startsWith('°') ? '°' : ''}`}</span>
                    </span>
                    <button type="button" className="wt-round-btn" disabled={off} onClick={() => bump(step)} aria-label={`${step} ${unit} higher`}>
                      <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                        <path d="M5 12h14M12 5v14" />
                      </svg>
                    </button>
                  </div>
                  {modes.length ? (
                    <div className="wt-modes" role="group" aria-label={modeCmd.label}>
                      {modeButtons(false)}
                    </div>
                  ) : null}
                  <button type="button" className="wt-confirm-btn is-ok" onClick={() => setSheet(false)}>
                    Done
                  </button>
                </div>
              </div>,
              document.body,
            )
          : null}
      </div>
    );
  }
  return (
    <div className="wt-dial">
      <div className="wt-tile-head wt-tile-head--big">
        <span className="wt-big-name">{e.name}</span>
        <span className="wt-tile-note" style={{ color }}>
          {status}
        </span>
      </div>
      <div className="wt-dial-row">
        <button type="button" className="wt-round-btn" disabled={off} onClick={() => bump(-step)} aria-label={`${step} ${unit} lower`}>
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
            <path d="M5 12h14" />
          </svg>
        </button>
        <div className="wt-dial-box">{ring}</div>
        <button type="button" className="wt-round-btn" disabled={off} onClick={() => bump(step)} aria-label={`${step} ${unit} higher`}>
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
            <path d="M5 12h14M12 5v14" />
          </svg>
        </button>
      </div>
      {modes.length ? (
        <div className="wt-modes" role="group" aria-label={modeCmd.label}>
          {modeButtons(false)}
        </div>
      ) : null}
    </div>
  );
}
