// A tile's When tapped / When held on the wall (the hub's catalog: dashboards.py ACTIONS). A tap on the
// tile itself does what When tapped says instead of the tile's own thing; its own buttons, dials, links
// and fields keep working (a part marked data-tile-surface - the climate glance, a camera's picture - is
// the tile itself). Holding the tile HOLD_MS does what When held says. Outside the wall (the dashboard
// editor's preview, no dashboard id) nothing happens.

import { useRef } from 'react';
import { api, enc } from '../api.js';
import { navigate, useHub } from '../hooks.js';
import { useWallCommand } from './PinPad.jsx';

export const HOLD_MS = 700; // as long as holding the clock
const CONTROLS = 'button, a, input, select, textarea, [role="button"], [role="slider"], [data-tile-control]';
const MOVE_PX = 12; // further than this and it's a drag, not a hold

function onControl(target, tile) {
  const el = target && target.closest ? target.closest(CONTROLS) : null;
  return !!el && el !== tile && tile.contains(el) && !el.hasAttribute('data-tile-surface');
}

/** Props for the tile's <section>: {} when it keeps its own tap and has no hold. */
export function useTileActions(tile, dashId) {
  const { entitiesById, kindsById } = useHub();
  const { run } = useWallCommand();
  const press = useRef(null); // { timer, x, y }
  const heldAt = useRef(0);
  const c = (tile && tile.config) || {};
  const tap = dashId && c.tap && c.tap.do && c.tap.do !== 'own' ? c.tap : null;
  const hold = dashId && c.hold && c.hold.do && c.hold.do !== 'none' ? c.hold : null;
  if (!tap && !hold) return {};

  const perform = (act, which) => {
    if (act.do === 'page' || act.do === 'screen') {
      const to = act.do === 'page' ? act.page : act.screen;
      if (to) navigate('wall/' + enc(to));
    } else if (act.do === 'automation') {
      api.post(`api/dashboards/${enc(dashId)}/tiles/${enc(tile.id)}/run`, { which }).catch(() => {});
    } else if (act.do === 'device') {
      const e = entitiesById[act.entity];
      const cmd = e && ((kindsById[e.kind] || {}).commands || []).find((x) => x.id === act.command);
      if (cmd) run(e, cmd);
    }
  };
  const cancel = () => {
    if (press.current) clearTimeout(press.current.timer);
    press.current = null;
  };
  const acts = tap && tap.do !== 'none';
  return {
    className: acts ? 'has-tap' : '',
    onClickCapture: (ev) => {
      if (Date.now() - heldAt.current < 600) {
        // the click that ends a hold
        ev.preventDefault();
        ev.stopPropagation();
        return;
      }
      if (!tap || onControl(ev.target, ev.currentTarget)) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (acts) perform(tap, 'tap');
    },
    ...(acts
      ? {
          tabIndex: 0,
          onKeyDown: (ev) => {
            if ((ev.key === 'Enter' || ev.key === ' ') && ev.target === ev.currentTarget) {
              ev.preventDefault();
              perform(tap, 'tap');
            }
          },
        }
      : {}),
    ...(hold
      ? {
          onPointerDown: (ev) => {
            if (onControl(ev.target, ev.currentTarget)) return;
            cancel();
            press.current = {
              x: ev.clientX,
              y: ev.clientY,
              timer: setTimeout(() => {
                press.current = null;
                heldAt.current = Date.now();
                perform(hold, 'hold');
              }, HOLD_MS),
            };
          },
          onPointerMove: (ev) => {
            const p = press.current;
            if (p && Math.hypot(ev.clientX - p.x, ev.clientY - p.y) > MOVE_PX) cancel();
          },
          onPointerUp: cancel,
          onPointerLeave: cancel,
          onPointerCancel: cancel,
          onContextMenu: (ev) => ev.preventDefault(),
        }
      : {}),
  };
}
