// The Climate glance tile (look only): the thermostat as an arc (where it's set) with the room's
// temperature as the big number, its humidity and the outside; then a box per chosen room with a
// cool-to-warm bar. A tap opens the page picked for it, or the thermostat's own dial.
// Colours come from the wall theme (theme.css); values are shown exactly as the devices report them.

import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { enc } from '../api.js';
import { navigate, useHub } from '../hooks.js';
import Dial, { arc, pol, KNOB, TRACK } from './Dial.jsx';
import { deg, mix, pct, roomColumns, roomOf, tempCommand, thermostatOf, warmth } from './climate.js';

// The arc's colour by the thermostat's mode (its state), as on the Dial; anything else: the accent.
const MODE_TONE = { heat: 'var(--wt-heat, #ff8a5b)', cool: 'var(--wt-cool, #5aa9ff)' };
// A room's bar: the theme's cool and warm colours mixed by how warm it is (plain colours where the
// browser can't mix).
const CAN_MIX = typeof CSS !== 'undefined' && CSS.supports && CSS.supports('color', 'color-mix(in srgb, red 50%, blue)');
const tint = (f) => (CAN_MIX ? `color-mix(in srgb, var(--wt-heat, #ff8a4c) ${Math.round(f * 100)}%, var(--wt-cool, #4cb7ff))` : mix('#4cb7ff', '#ff8a4c', f));

function useBox(ref) {
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const on = () => setBox((b) => (b.w === el.clientWidth && b.h === el.clientHeight ? b : { w: el.clientWidth, h: el.clientHeight }));
    on();
    const ro = new ResizeObserver(on);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return box;
}

function ThermoArc({ t, tone }) {
  const span = t.max - t.min || 1;
  const ang = (v) => 135 + ((Math.min(t.max, Math.max(t.min, Number(v))) - t.min) / span) * 270;
  const set = Number.isFinite(Number(t.target)) && t.target !== null && !t.off;
  const now = Number.isFinite(Number(t.now)) && t.now !== null ? pol(ang(t.now)) : null;
  const w = 'var(--wt-dial-w, 26)';
  return (
    <svg viewBox="0 0 360 360" className="cg-arc-svg" aria-hidden="true">
      <path d={arc(135, 405)} fill="none" style={{ stroke: TRACK, strokeWidth: w }} strokeLinecap="round" />
      {set ? <path d={arc(135, Math.max(135.1, ang(t.target)))} className="wt-arc-fill" fill="none" style={{ stroke: tone, strokeWidth: w }} strokeLinecap="round" /> : null}
      {/* the candy cane's stripes (shown by the Christmas theme only, theme.css) */}
      {set ? <path d={arc(135, Math.max(135.1, ang(t.target)))} className="wt-arc-stripe" fill="none" style={{ strokeWidth: w }} /> : null}
      {now ? <circle cx={now[0]} cy={now[1]} r="7" style={{ fill: KNOB }} opacity="0.75" /> : null}
    </svg>
  );
}

export default function ClimateTile({ tile }) {
  const { entitiesById, kindsById } = useHub();
  const c = tile.config || {};
  const te = entitiesById[c.entity];
  const tk = te && kindsById[te.kind];
  const t = thermostatOf(te, tk);
  const out = entitiesById[c.outside];
  const outTemp = out && (out.properties || []).find((p) => p.id === 'temperature');
  const rooms = (c.rooms || []).map((id) => entitiesById[id]).filter(Boolean).map((e) => roomOf(e, kindsById[e.kind]));
  const [sheet, setSheet] = useState(false);
  const root = useRef(null);
  const grid = useRef(null);
  const box = useBox(root);
  const area = useBox(grid);
  const cols = roomColumns(box.w / (Number(c.textSize) > 0 ? Number(c.textSize) / 100 : 1), rooms.length);
  // As many rows of rooms as fit (measured from a drawn box); the rest as "+N more".
  const [rowH, setRowH] = useState(0);
  useLayoutEffect(() => {
    const g = grid.current;
    const first = g && g.firstElementChild;
    if (!first) return;
    const zoom = g.getBoundingClientRect().height / (g.clientHeight || 1) || 1;
    const h = first.getBoundingClientRect().height / zoom + 8;
    if (Math.abs(h - rowH) > 1) setRowH(h);
  });
  const rows = area.h && rowH ? Math.max(1, Math.floor((area.h + 8) / rowH)) : Math.ceil(rooms.length / cols);
  const room = rows * cols;
  const shown = rooms.length > room ? rooms.slice(0, room - 1) : rooms;
  const more = rooms.length - shown.length;
  const tone = (t && MODE_TONE[t.modeId]) || 'var(--wt-teal, #3ccfb4)';
  const open = () => {
    if (c.page) navigate('wall/' + enc(c.page));
    else if (t) setSheet(true);
  };
  const status = t ? [t.mode, t.action].filter(Boolean).join(' · ') : '';
  const outLine = out ? [outTemp ? deg(outTemp.value) : null, out.stateLabel || null].filter(Boolean).join(' · ') : '';
  const label = ['Climate', t && t.now !== null ? `${deg(t.now)} now` : null, t && t.target !== null ? `set to ${deg(t.target)}` : null].filter(Boolean).join(', ');
  return (
    <div className="cg" ref={root}>
      <button type="button" className="cg-btn" onClick={open} aria-label={label}>
        <span className="cg-top">
          <span className="cg-title">Climate</span>
          {status ? (
            <span className="cg-mode" style={{ color: tone }}>
              {status}
            </span>
          ) : null}
        </span>
        {t ? (
          <span className="cg-hero">
            <span className="cg-arc">
              <ThermoArc t={t} tone={tone} />
              <span className="cg-now">{deg(t.now)}</span>
            </span>
            <span className="cg-side">
              <span className="cg-set">
                {t.off ? (
                  'Off'
                ) : (
                  <>
                    Set to <em style={{ color: tone }}>{deg(t.target)}</em>
                  </>
                )}
              </span>
              {t.humidity !== null ? <span className="cg-sm">Humidity {pct(t.humidity)}</span> : null}
              {outLine ? <span className="cg-sm">Outside {outLine}</span> : null}
            </span>
          </span>
        ) : outLine ? (
          <span className="cg-side cg-side--alone">
            <span className="cg-sm">Outside {outLine}</span>
          </span>
        ) : null}
        <span className="cg-rooms" ref={grid} style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {!t && !rooms.length ? <span className="wt-muted">No thermostat or rooms chosen.</span> : null}
          {shown.map((r) => {
            const f = warmth(r.temp, r.unit);
            return (
              <span key={r.id} className={'cg-room' + (r.off ? ' is-off' : '')}>
                <span className="cg-room-name">{r.name}</span>
                <span className="cg-room-t">{r.off ? 'Offline' : deg(r.temp)}</span>
                <span className="cg-room-h">{r.humidity !== null && !r.off ? pct(r.humidity) : ''}</span>
                <span className="cg-room-bar">{f !== null && !r.off ? <i style={{ width: Math.max(4, f * 100) + '%', background: tint(f) }} /> : null}</span>
              </span>
            );
          })}
          {more > 0 ? <span className="cg-room cg-room--more">+{more} more</span> : null}
        </span>
      </button>
      {sheet && t
        ? createPortal(
            <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={te.name} onClick={(ev) => ev.target === ev.currentTarget && setSheet(false)}>
              <div className="wt-confirm cg-sheet">
                <Dial entity={te} kind={tk} config={{ command: tempCommand(tk).id, modeCommand: ((tk.commands || []).find((x) => x.valueType === 'state') || {}).id }} />
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
