// The wall's compact layout, for short screens (under COMPACT_MAX_H CSS px tall - an Echo Show 5's
// 960 x 480, a phone held sideways). Big tablets never get it. compact.js decides which tiles share a
// page; compact.css sizes everything in units of the screen's height (its mockup is 960 x 480).
//   top bar: the clock · the tabs as icons · one alerts chip (the first problem and how many; tap: the
//            list, each with its own action) · the dim button
//   body:    the current tab's tiles as pages swiped sideways, two to a page, with dots under them

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { enc } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { deviceIcon, entityLabel, isModeKind, notSecure } from '../model.js';
import { COMPACT_MAX_H, compactPages, pageAt } from './compact.js';
import { CompactContext } from './compactContext.js';
import { useWallCommand } from './PinPad.jsx';
import { useDeviceOpener } from './TodayTile.jsx';
import { Tile, nextAction } from './tiles.jsx';
import { AlertButton, useWeatherWarning } from './WeatherCalendar.jsx';

const QUERY = `(max-height: ${COMPACT_MAX_H - 0.02}px)`;

/** Is this screen short enough for the compact layout? Follows rotation and resizing. */
export function useCompactScreen() {
  const get = () => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(QUERY).matches;
  const [on, setOn] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const mq = window.matchMedia(QUERY);
    const change = () => setOn(mq.matches);
    change();
    if (mq.addEventListener) mq.addEventListener('change', change);
    else mq.addListener(change);
    return () => (mq.removeEventListener ? mq.removeEventListener('change', change) : mq.removeListener(change));
  }, []);
  return on;
}

/* ---------------- top bar ---------------- */

/** The dashboard's tabs, every one as its icon. */
export function CompactTabs({ pages, current }) {
  return (
    <nav className="wc-tabs" aria-label="Pages">
      {pages.map((p) => (
        <a key={p.id} className={'wc-tab' + (p.id === current ? ' is-on' : '')} href={'#/wall/' + enc(p.id)} aria-label={p.name} aria-current={p.id === current ? 'page' : undefined}>
          <Icon name={p.icon || 'grid'} size={22} />
        </a>
      ))}
    </nav>
  );
}

const issuesText = (e) => ((e.health && e.health.issues) || []).join(' · ') || 'Needs attention';

/**
 * What the first page's status chips say, as rows: the devices that aren't secure, the house mode,
 * a weather warning, devices that need attention, and the tablet's clock being off. Every row but the
 * house mode is a problem (counted on the chip).
 */
function useProblems(skewMin) {
  const { entities, kindsById } = useHub();
  const list = entities || [];
  const warning = useWeatherWarning();
  const rows = [];
  list
    .filter((e) => kindsById[e.kind] && kindsById[e.kind].secureState && notSecure(e, kindsById[e.kind]))
    .forEach((e) => rows.push({ key: 'sec:' + e.id, problem: true, tone: 'warn', e, text: `${e.name} ${(e.stateLabel || e.state || '').toLowerCase()}`, action: nextAction(e, kindsById[e.kind]) }));
  const mode = list.find((e) => isModeKind(kindsById[e.kind]));
  if (mode) rows.push({ key: 'mode', problem: false, tone: 'calm', e: mode, text: `${mode.name}: ${entityLabel(mode, kindsById[mode.kind])}` });
  if (warning) rows.push({ key: 'wx', problem: true, tone: 'warn', warning, text: warning.top.name || 'Weather warning' });
  list
    .filter((e) => e.health && e.health.ok === false)
    .forEach((e) => rows.push({ key: 'bad:' + e.id, problem: true, tone: 'bad', e, text: `${e.name}: ${(e.health.issues || [])[0] || 'needs attention'}`, sick: true }));
  if (skewMin) rows.push({ key: 'skew', problem: true, tone: 'warn', text: `Tablet clock is ${skewMin} min off` });
  return rows;
}

function RowIcon({ row }) {
  const { kindsById } = useHub();
  // A device that isn't secure, or the house mode: its own icon; anything else is a warning.
  const name = row.e && !row.sick ? deviceIcon(row.e, kindsById[row.e.kind]) : 'alert';
  return (
    <span className={'wc-row-icon is-' + row.tone}>
      <Icon name={name} size={22} />
    </span>
  );
}

/** The alerts chip: the first problem and how many there are; a tap lists them all. None: no chip. */
export function CompactAlerts({ skewMin }) {
  const rows = useProblems(skewMin);
  const problems = rows.filter((r) => r.problem);
  const [open, setOpen] = useState(false);
  const { run, busy } = useWallCommand();
  const { openDevice, sheet } = useDeviceOpener();
  const close = useCallback(() => setOpen(false), []);
  useEffect(() => {
    if (!problems.length) setOpen(false);
  }, [problems.length]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (ev) => ev.key === 'Escape' && close();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, close]);
  if (!problems.length) return sheet;
  const first = problems[0];
  return (
    <>
      <button type="button" className="wc-chip" onClick={() => setOpen((v) => !v)} aria-haspopup="dialog" aria-expanded={open}>
        <Icon name="alert" size={20} strokeWidth={2.2} />
        <span className="wc-chip-text">{first.text}</span>
        <span className="wc-chip-n">{problems.length}</span>
      </button>
      {open ? (
        <>
          <div className="wc-dim" onClick={close} aria-hidden="true" />
          <div className="wc-sheet" role="dialog" aria-label="Alerts">
            {rows.map((r) => {
              const inner = (
                <>
                  <RowIcon row={r} />
                  <span className="wc-row-text">{r.text}</span>
                </>
              );
              if (r.warning)
                return (
                  <AlertButton key={r.key} place={r.warning.e.name} alerts={r.warning.warnings} className="wc-row wc-row--tap">
                    {inner}
                  </AlertButton>
                );
              if (r.sick)
                return (
                  <button key={r.key} type="button" className="wc-row wc-row--tap" onClick={() => (close(), openDevice({ entity: r.e.id, status: issuesText(r.e) }))}>
                    {inner}
                  </button>
                );
              return (
                <div key={r.key} className="wc-row">
                  {inner}
                  {r.action ? (
                    <button type="button" className="wc-row-btn" disabled={r.e.available === false || !!busy[r.e.id]} onClick={() => run(r.e, r.action)}>
                      {r.action.label}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </>
      ) : null}
      {sheet}
    </>
  );
}

/* ---------------- body ---------------- */

/** The current tab's tiles as pages swiped sideways (two to a page), dots under them. */
export function CompactPages({ page, dashId }) {
  const cat = useApi('api/dashboards/tiles');
  const types = cat.data && Array.isArray(cat.data.types) ? cat.data.types : null;
  const ready = !!types || !!cat.error;
  const pages = ready ? compactPages(page.tiles, types) : [];
  const strip = useRef(null);
  const [at, setAt] = useState(0);
  // Another tab: back to its first page.
  useLayoutEffect(() => {
    if (strip.current) strip.current.scrollLeft = 0;
    setAt(0);
  }, [page.id]);
  const onScroll = () => {
    const s = strip.current;
    if (s) setAt(pageAt(s.scrollLeft, s.clientWidth, pages.length));
  };
  return (
    <CompactContext.Provider value={{ types: types || [] }}>
      <div className="wc-body">
        <div className="wc-strip" ref={strip} onScroll={onScroll}>
          {pages.map((p) => (
            <div key={p.map((t) => t.id).join(' ')} className={'wc-page' + (p.length === 1 ? ' is-one' : '')}>
              {p.map((t) => (
                <Tile key={t.id} tile={t} dash={dashId} />
              ))}
            </div>
          ))}
        </div>
      </div>
      {pages.length > 1 ? (
        <div className="wc-dots" aria-hidden="true">
          {pages.map((p, i) => (
            <i key={p.map((t) => t.id).join(' ')} className={i === Math.min(at, pages.length - 1) ? 'is-on' : ''} />
          ))}
        </div>
      ) : null}
    </CompactContext.Provider>
  );
}
