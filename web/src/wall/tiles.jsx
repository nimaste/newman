// The wall tablet's tiles, drawn by `type` from the dashboard the hub keeps. Devices come only from
// each tile's `config` (never picked by kind); what a device can do comes from /api/meta.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ProblemList } from './MuteProblems.jsx';
import { createPortal } from 'react-dom';
import { enc } from '../api.js';
import { navigate, useApi, useHub } from '../hooks.js';
import { useLiveRefresh } from '../live.js';
import { Icon } from '../icons.jsx';
import { fmtClock } from '../format.js';
import { entityStateText, isActive, entityLabel, isModeKind, kindIcon, notSecure, plainCommands } from '../model.js';
import { formatPropValue } from '../automation.js';
import LiveVideo from '../components/LiveVideo.jsx';
import { pendingReplay } from '../pages/LiveView.jsx';
import Dial from './Dial.jsx';
import { useWallCommand } from './PinPad.jsx';
import { CalendarTile, WeatherChip, WeatherTile, WeatherWarningChip } from './WeatherCalendar.jsx';
import { DeviceSheet, deviceVitals, extraActions } from './DeviceSheet.jsx';
import TodayTile, { useDeviceOpener } from './TodayTile.jsx';
import { GreetingTile, TodoTile } from './TodayPage.jsx';
import PeopleTile from './PeopleTile.jsx';
import PhoneTileBody from './PhoneTiles.jsx';
import { MusicTile, NowPlayingChip } from './Music.jsx';
import { RecipesTile } from './CookMode.jsx';
import ClimateTile from './ClimateTile.jsx';


function useEnts(ids) {
  const { entitiesById } = useHub();
  return (ids || []).map((id) => entitiesById[id]).filter(Boolean);
}

/** How many rows of at least `min` px fit in `ref` (for lists that must never scroll). */
export function useFit(ref, min, total) {
  const [n, setN] = useState(total);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => setN(Math.max(1, Math.floor((el.clientHeight + 6) / (min + 6))));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, min]);
  return n;
}

function Title({ children, right }) {
  return (
    <div className="wt-tile-head">
      <span>{children}</span>
      {right || null}
    </div>
  );
}

/* ---------------- camera ---------------- */

/**
 * How many of a row's chips fit whole beside its last child (the "All cameras" link): the rest are
 * left out rather than squeezed. Widths are measured once while every chip shows.
 */
function useChipFit(ref, key) {
  const [fit, setFit] = useState(null);
  const widths = useRef(null);
  useLayoutEffect(() => {
    widths.current = null;
    setFit(null);
  }, [key]);
  useLayoutEffect(() => {
    const row = ref.current;
    if (!row || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => {
      const kids = [...row.children];
      if (kids.length < 2) return;
      if (!widths.current) widths.current = kids.slice(0, -1).map((k) => k.offsetWidth);
      const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
      let room = row.clientWidth - kids[kids.length - 1].offsetWidth;
      let n = 0;
      for (const w of widths.current) {
        if (room < w + gap) break;
        room -= w + gap;
        n += 1;
      }
      setFit(n);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(row);
    return () => ro.disconnect();
  }, [ref, key, fit === null]);
  return fit;
}

function CameraTile({ tile }) {
  const cams = useEnts(tile.config.entities);
  const [pick, setPick] = useState(null);
  const cam = cams.find((c) => c.id === pick) || cams[0];
  const chips = useRef(null);
  const fit = useChipFit(chips, cams.map((c) => c.name).join('|'));
  if (!cam) return <div className="wt-empty">No cameras chosen for this tile.</div>;
  return (
    <div className="wt-cam">
      <a className="wt-cam-pic" href={'#/wall/camera/' + enc(cam.id)} aria-label={`Watch ${cam.name} full screen`}>
        <LiveVideo key={cam.id} entity={cam} quality="sub" stillHeight={480} showNote={false} className="wt-cam-video" />
        <span className="wt-cam-name">
          <span className="wt-live-dot" aria-hidden="true" />
          {cam.name}
        </span>
        <span className="wt-cam-full" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
          </svg>
        </span>
      </a>
      <div className="wt-pills" ref={chips}>
        {cams.map((c, i) => (
          <button key={c.id} type="button" className={'wt-pill' + (c.id === cam.id ? ' is-on' : '')} hidden={fit !== null && i >= fit} onClick={() => setPick(c.id)} aria-pressed={c.id === cam.id}>
            {c.name}
          </button>
        ))}
        <a className="wt-pill wt-pill--link" href="#/wall/cameras">
          <Icon name="grid" size={15} strokeWidth={2.2} /> All cameras
        </a>
      </div>
    </div>
  );
}

/* ---------------- alerts ---------------- */

function AlertsTile({ tile }) {
  const { entitiesById } = useHub();
  const ids = tile.config.entities || [];
  const limit = Math.max(1, Math.min(12, Number(tile.config.limit) || 4));
  // One camera: its own list; several or none: all alerts, filtered to those cameras.
  const one = ids.length === 1 && entitiesById[ids[0]] ? entitiesById[ids[0]].alerts : null;
  const res = useApi((one || 'api/alerts') + '?limit=' + (ids.length > 1 ? 50 : limit));
  useLiveRefresh((m) => m.type === 'activity' && !!m.entry && m.entry.kind === 'alert', () => res.reload(), 800);
  const listRef = useRef(null);
  const fit = useFit(listRef, 48, limit);
  const items = (Array.isArray(res.data) ? res.data : []).filter((a) => ids.length <= 1 || ids.includes(a.entityId)).slice(0, Math.min(limit, fit));
  const open = (a) => {
    pendingReplay.alert = a;
    navigate('wall/camera/' + enc(a.entityId || (ids[0] || '')));
  };
  return (
    <div className="wt-alerts">
      <Title right={<a className="wt-link" href="#/wall/cameras">See all</a>}>Recent alerts</Title>
      <div className="wt-alert-list" ref={listRef}>
        {res.data === undefined ? <div className="wt-muted">Loading…</div> : !items.length ? <div className="wt-muted">Nothing lately.</div> : null}
        {items.map((a) => (
          <button key={a.id} type="button" className="wt-alert" onClick={() => open(a)}>
            <span className="wt-alert-thumb">
              {a.thumbUrl || a.snapshotUrl ? <img src={a.thumbUrl || a.snapshotUrl} alt="" loading="lazy" /> : null}
              <Icon name="play" size={14} />
            </span>
            <span className="wt-alert-text">
              <span className="wt-alert-title">{a.title}</span>
              <span className="wt-alert-zone">{(a.zones || []).join(', ') || a.cameraName}</span>
            </span>
            <span className="wt-alert-time">{fmtClock(a.start)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ---------------- devices ---------------- */

/** The action a list row offers: a command that isn't the current state (Lock/Unlock, Open/Close). */
function nextAction(e, kind) {
  const plain = plainCommands(kind);
  // A device with a single action (a button's "Press") just offers it.
  return plain.find((c) => c.confirmsTo && c.confirmsTo !== e.state) || (plain.length === 1 ? plain[0] : null);
}
/** Tap on a device tile: its on/off pair, else the next action. */
function tapAction(e, kind) {
  const plain = plainCommands(kind);
  if (kind && kind.activeState && plain.length === 2 && !plain.some((c) => c.sensitive)) {
    const on = plain.find((c) => c.confirmsTo === kind.activeState);
    const off = plain.find((c) => c !== on && c.confirmsTo);
    if (on && off) return isActive(e, kind) ? off : on;
  }
  return nextAction(e, kind);
}

function BrightnessBar({ e, cmd, lit }) {
  const { run } = useWallCommand();
  const cur = cmd.attr && typeof (e.attrs || {})[cmd.attr] === 'number' ? e.attrs[cmd.attr] : null;
  const [v, setV] = useState(cur ?? cmd.default ?? 50);
  const drag = useRef(false);
  useEffect(() => {
    if (!drag.current && cur !== null) setV(cur);
  }, [cur]);
  const pct = lit ? Math.round(((v - (cmd.min ?? 0)) / ((cmd.max ?? 100) - (cmd.min ?? 0))) * 100) : 0;
  const commit = () => {
    drag.current = false;
    run(e, cmd, v);
  };
  return (
    <label className="wt-bar">
      <span className="wt-bar-fill" style={{ width: pct + '%' }} />
      <input
        type="range"
        min={cmd.min ?? 0}
        max={cmd.max ?? 100}
        step={cmd.step || 1}
        value={v}
        aria-label={`${e.name}: ${cmd.label}`}
        onChange={(ev) => {
          drag.current = true;
          setV(Number(ev.target.value));
        }}
        onPointerUp={commit}
        onKeyUp={commit}
        onTouchEnd={commit}
      />
    </label>
  );
}

/**
 * How a device's state reads, shared by the tile and list styles: "calm" (teal) in its kind's
 * secureState, "warn" (coral) away from it or with a problem, "" for kinds without a secureState.
 */
function stateTone(e, kind, problem) {
  if (problem || notSecure(e, kind)) return 'warn';
  return kind && kind.secureState ? 'calm' : '';
}

function DeviceCard({ e, kind, showArea }) {
  const { run, busy, mode } = useWallCommand();
  const action = tapAction(e, kind);
  const lit = isActive(e, kind) && !(kind && kind.secureState);
  const warn = notSecure(e, kind);
  const pct = ((kind && kind.commands) || []).find((c) => c.valueType === 'percent') || null;
  const dimmable = pct && !(e.attrs && e.attrs.dimmable === false);
  const off = e.available === false;
  const extras = extraActions(e, kind, action);
  const { battery, problem } = deviceVitals(e);
  const tone = stateTone(e, kind, problem);
  const [sheet, setSheet] = useState(null); // null | { start? }
  return (
    <div className={'wt-dev' + (lit ? ' is-lit' : '') + (warn || problem ? ' is-warn' : '') + (off ? ' is-off' : '')}>
      {showArea ? <div className="wt-dev-area">{(e.attrs && e.attrs.area) || ' '}</div> : null}
      <button type="button" className="wt-dev-main" disabled={!action || off || !!busy[e.id]} onClick={() => action && run(e, action)} aria-pressed={kind && kind.activeState ? isActive(e, kind) : undefined}>
        <span className="wt-dev-icon">
          <Icon name={kindIcon(kind)} size={28} />
        </span>
        <span className="wt-dev-text">
          <span className="wt-dev-name">{e.name}</span>
          <span className={'wt-dev-state' + (tone ? ' is-' + tone : '')}>
            {entityStateText(e, kind)}
            {battery !== null ? ` · ${battery}%` : ''}
            {action && extras.length ? ` · tap to ${action.label.toLowerCase()}` : ''}
          </span>
          {problem ? <span className="wt-dev-problem">{problem}</span> : null}
        </span>
      </button>
      {dimmable ? <BrightnessBar e={e} cmd={pct} lit={lit} /> : null}
      {extras.length ? (
        <span className="wt-dev-extras">
          {extras.map((c) => (
            <button
              key={c.id}
              type="button"
              className="wt-pill"
              disabled={off || !!busy[e.id] || (c.valueType !== 'choices' && !!c.confirmsTo && c.confirmsTo === e.state)}
              onClick={() => (c.valueType === 'choices' ? setSheet({ start: c }) : run(e, c))}
            >
              {c.label}
              {c.valueType === 'choices' ? '…' : ''}
            </button>
          ))}
          {/* Small cards show just this one (container query in wall.css). */}
          <button type="button" className="wt-pill wt-pill--more" disabled={off} onClick={() => setSheet({})}>
            More…
          </button>
        </span>
      ) : null}
      {sheet ? <DeviceSheet e={e} kind={kind} main={action} start={sheet.start} onClose={() => setSheet(null)} /> : null}
    </div>
  );
}

function DeviceRow({ e, kind, last, showArea }) {
  const { run, busy, mode } = useWallCommand();
  const action = nextAction(e, kind);
  const { battery, problem } = deviceVitals(e);
  const tone = stateTone(e, kind, problem);
  const warn = tone === 'warn';
  const calm = tone === 'calm';
  const extras = extraActions(e, kind, action);
  const [sheet, setSheet] = useState(false);
  return (
    <div className={'wt-row' + (last ? ' is-last' : '')}>
      <span className={'wt-row-icon' + (warn ? ' is-warn' : calm ? ' is-calm' : '')}>
        <Icon name={kindIcon(kind)} size={22} />
      </span>
      <span className="wt-row-text">
        <span className="wt-row-name">{e.name}</span>
        <span className={'wt-row-state' + (warn ? ' is-warn' : calm ? ' is-calm' : '')}>
          {entityStateText(e, kind)}
          {battery !== null ? ` · ${battery}%` : ''}
          {problem ? ` · ${problem}` : ''}
          {showArea && e.attrs && e.attrs.area ? ` · ${e.attrs.area}` : ''}
        </span>
      </span>
      {action ? (
        <button type="button" className="wt-action" disabled={e.available === false || !!busy[e.id]} onClick={() => run(e, action)}>
          {action.label}
          {action.sensitive && mode === 'pin' ? <span className="wt-pin-tag">PIN</span> : null}
        </button>
      ) : null}
      {extras.length ? (
        <button type="button" className="wt-action wt-action--more" onClick={() => setSheet(true)} aria-label={`More for ${e.name}`} title="More">
          ⋯
        </button>
      ) : null}
      {sheet ? <DeviceSheet e={e} kind={kind} main={action} onClose={() => setSheet(false)} /> : null}
    </div>
  );
}

function DevicesTile({ tile }) {
  const { kindsById } = useHub();
  const c = tile.config;
  let ents = useEnts(c.entities);
  const byArea = c.groupBy === 'area';
  if (byArea) ents = ents.slice().sort((a, b) => ((a.attrs && a.attrs.area) || '~').localeCompare((b.attrs && b.attrs.area) || '~'));
  const listRef = useRef(null);
  const fitRows = useFit(listRef, 52, ents.length);
  if (c.style === 'list') {
    const shown = ents.slice(0, fitRows);
    return (
      <div className="wt-list">
        {c.title ? <Title>{c.title}</Title> : null}
        <div className="wt-list-body" ref={listRef}>
          {!ents.length ? <div className="wt-muted">No devices chosen.</div> : null}
          {shown.map((e, i) => (
            <DeviceRow key={e.id} e={e} kind={kindsById[e.kind]} last={i === shown.length - 1} showArea={byArea} />
          ))}
          {ents.length > shown.length ? <div className="wt-more">+{ents.length - shown.length} more</div> : null}
        </div>
      </div>
    );
  }
  // Tiles: a grid shaped like the tile itself.
  const n = Math.max(1, ents.length);
  const cols = Math.max(1, Math.min(n, Math.round(Math.sqrt((n * tile.w * 1.6) / tile.h))));
  // At least a card per half-tile of height, like the Lights board (big, easy targets).
  const rows = Math.max(Math.ceil(n / cols), Math.floor(tile.h / 4));
  const summary = (() => {
    const lit = ents.filter((e) => isActive(e, kindsById[e.kind]) && !(kindsById[e.kind] || {}).secureState).length;
    return lit ? `${lit} on` : '';
  })();
  return (
    <div className="wt-devs">
      {c.title ? <Title right={summary ? <span className="wt-tile-note">{summary}</span> : null}>{c.title}</Title> : null}
      <div className="wt-dev-grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))` }}>
        {!ents.length ? <div className="wt-muted">No devices chosen.</div> : null}
        {ents.map((e) => (
          <DeviceCard key={e.id} e={e} kind={kindsById[e.kind]} showArea={byArea} />
        ))}
      </div>
    </div>
  );
}

/* ---------------- mode (alarm) ---------------- */

function ModeTile({ tile }) {
  const { entitiesById, kindsById } = useHub();
  const { run, busy, mode } = useWallCommand();
  const e = entitiesById[tile.config.entity];
  if (!e) return <div className="wt-empty">This device is missing.</div>;
  const kind = kindsById[e.kind];
  const cmds = plainCommands(kind).filter((c) => c.confirmsTo !== e.state);
  const calm = kind && kind.activeState ? !isActive(e, kind) : false;
  return (
    <div className="wt-mode-row">
      <span className={'wt-mode-icon' + (calm ? ' is-calm' : '')}>
        <Icon name={kindIcon(kind)} size={28} />
      </span>
      <span className="wt-mode-text">
        <span className="wt-mode-name">{e.name}</span>
        <span className="wt-mode-state">{e.available === false ? 'Offline' : entityLabel(e, kind)}</span>
      </span>
      <span className="wt-mode-btns">
        {cmds.map((c) => (
          <button key={c.id} type="button" className="wt-action" disabled={e.available === false || !!busy[e.id]} onClick={() => run(e, c)}>
            {c.label}
            {c.sensitive && mode === 'pin' ? <span className="wt-pin-tag">PIN</span> : null}
          </button>
        ))}
      </span>
    </div>
  );
}

/* ---------------- values ---------------- */

function ValuesTile({ tile }) {
  const { entitiesById } = useHub();
  const items = (tile.config.items || [])
    .map((it) => {
      const e = entitiesById[it.entity];
      const p = e && (e.properties || []).find((x) => x.id === it.property);
      return e ? { key: it.entity + ':' + it.property, e, p } : null;
    })
    .filter(Boolean);
  const listRef = useRef(null);
  const fit = useFit(listRef, 52, items.length);
  const shown = items.slice(0, fit);
  return (
    <div className="wt-list">
      {tile.config.title ? <Title>{tile.config.title}</Title> : null}
      <div className="wt-list-body" ref={listRef}>
        {!items.length ? <div className="wt-muted">No readings chosen.</div> : null}
        {shown.map(({ key, e, p }, i) => (
          <div key={key} className={'wt-row' + (i === shown.length - 1 ? ' is-last' : '')}>
            <span className="wt-row-text">
              <span className="wt-row-name">{e.name}</span>
              <span className="wt-row-state">{p ? p.label : 'Not reported'}</span>
            </span>
            <span className="wt-reading">{p ? formatPropValue(p, p.value) : '—'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- attention ---------------- */

function AttentionTile() {
  const { entities, kindsById } = useHub();
  // Urgent kinds (meta `urgent`: a leak, smoke) away from their secure state come first, in coral.
  // An open door is normal and stays in the security summary. Then offline / battery / tamper.
  const alarms = (entities || []).filter((e) => {
    const k = kindsById[e.kind];
    return k && k.urgent && k.secureState && notSecure(e, k);
  });
  const sick = (entities || []).filter((e) => e.health && e.health.ok === false && !alarms.includes(e));
  const bad = [
    ...alarms.map((e) => ({ ...e, health: { ok: false, issues: [entityLabel(e, kindsById[e.kind]), ...((e.health && e.health.issues) || [])] }, alarm: true })),
    ...sick,
  ];
  const listRef = useRef(null);
  const fit = useFit(listRef, 46, bad.length);
  const shown = bad.slice(0, fit);
  return (
    <div className={'wt-attn' + (bad.length ? ' is-bad' : '')}>
      <Title>
        <span className="wt-attn-head">
          {bad.length ? <Icon name="alert" size={18} strokeWidth={2.2} /> : <Icon name="check" size={18} strokeWidth={2.2} />}
          {bad.length ? 'Needs attention' : 'All devices OK'}
        </span>
      </Title>
      <div className="wt-list-body" ref={listRef}>
        {shown.map((e) => (
          <div key={e.id} className={'wt-attn-item' + (e.alarm ? ' is-alarm' : '')}>
            <span className="wt-attn-name">{e.name}</span>
            <span className="wt-attn-issues">{((e.health && e.health.issues) || []).join(' · ')}</span>
          </div>
        ))}
        {bad.length > shown.length ? <div className="wt-more">+{bad.length - shown.length} more</div> : null}
      </div>
    </div>
  );
}

/* ---------------- a tile type this tablet doesn't know yet ---------------- */

function SoonTile({ tile }) {
  return (
    <div className="wt-soon">
      <Icon name="grid" size={30} />
      <span className="wt-soon-title">{tile.type}</span>
      <span className="wt-muted">Coming soon</span>
    </div>
  );
}

/** The tile's text size (config.textSize, % of normal) as --ts for wall.css; icons and buttons keep theirs. */
function textScale(tile) {
  const v = Number(tile.config && tile.config.textSize);
  return Number.isFinite(v) && v > 0 && v !== 100 ? { '--ts': String(Math.max(0.5, Math.min(1.6, v / 100))) } : {};
}

/**
 * One tile in its grid cell. `phone` ({ w, h }: its cell rectangle in px) draws it the phone apps' way
 * instead, fitted to that rectangle (the phone layout editor's preview); without it, the wall's own.
 */
export function Tile({ tile, phone }) {
  const { entitiesById, kindsById } = useHub();
  if (phone) {
    return (
      <section
        className={'wt-tile wt-tile--phone wt-tile--' + tile.type}
        style={{ gridColumn: `${tile.x + 1} / span ${tile.w}`, gridRow: `${tile.y + 1} / span ${tile.h}`, ...textScale(tile) }}
        aria-label={tile.config.title || tile.type}
      >
        <PhoneTileBody tile={tile} width={phone.w} height={phone.h} />
      </section>
    );
  }
  let body;
  switch (tile.type) {
    case 'camera':
      body = <CameraTile tile={tile} />;
      break;
    case 'alerts':
      body = <AlertsTile tile={tile} />;
      break;
    case 'dial': {
      const e = entitiesById[tile.config.entity];
      body = <Dial entity={e} kind={e && kindsById[e.kind]} config={tile.config} compact={tile.w < 4 || tile.h < 4} tall={tile.h >= 4} />;
      break;
    }
    case 'devices':
      body = <DevicesTile tile={tile} />;
      break;
    case 'mode':
      body = <ModeTile tile={tile} />;
      break;
    case 'values':
      body = <ValuesTile tile={tile} />;
      break;
    case 'attention':
      body = <AttentionTile />;
      break;
    case 'weather':
      body = <WeatherTile tile={tile} />;
      break;
    case 'calendar':
      body = <CalendarTile tile={tile} />;
      break;
    case 'today':
      body = <TodayTile tile={tile} />;
      break;
    case 'greeting':
      body = <GreetingTile />;
      break;
    case 'todo':
      body = <TodoTile />;
      break;
    case 'people':
      body = <PeopleTile tile={tile} />;
      break;
    case 'music':
      body = <MusicTile tile={tile} />;
      break;
    case 'recipes':
      body = <RecipesTile />;
      break;
    case 'climate':
      body = <ClimateTile tile={tile} />;
      break;
    default:
      body = <SoonTile tile={tile} />;
  }
  const plain = tile.type === 'devices' && tile.config.style !== 'list' && !tile.config.title;
  return (
    <section
      className={'wt-tile wt-tile--' + tile.type + (plain ? ' wt-tile--bare' : '') + (tile.type === 'attention' ? '' : '')}
      style={{ gridColumn: `${tile.x + 1} / span ${tile.w}`, gridRow: `${tile.y + 1} / span ${tile.h}`, ...textScale(tile) }}
      aria-label={tile.config.title || tile.type}
    >
      {body}
    </section>
  );
}

/** "All secure · 2 locks locked", the house mode, and what needs attention: the first page's chips. */
export function StatusChips() {
  const { entities, kindsById } = useHub();
  const list = entities || [];
  const secure = list.filter((e) => kindsById[e.kind] && kindsById[e.kind].secureState);
  const open = secure.filter((e) => notSecure(e, kindsById[e.kind]));
  const mode = list.find((e) => isModeKind(kindsById[e.kind]));
  const bad = list.filter((e) => e.health && e.health.ok === false);
  const badRef = useRef(null);
  const [sickOpen, setSickOpen] = useState(false);
  const closeSick = () => setSickOpen(false);
  // everything fixed while the sheet was up: it closes (and stays closed when something new breaks)
  useEffect(() => {
    if (!bad.length) setSickOpen(false);
  }, [bad.length]);
  const chips = [];
  if (secure.length) {
    chips.push(
      open.length ? (
        <div key="sec" className="wt-chip wt-chip--warn">
          <Icon name="alert" size={18} strokeWidth={2.2} />
          {open.length === 1 ? `${open[0].name} ${(open[0].stateLabel || open[0].state || '').toLowerCase()}` : `${open.length} not secure`}
        </div>
      ) : (
        <div key="sec" className="wt-chip wt-chip--ok">
          <Icon name="shield" size={18} strokeWidth={2.2} />
          All secure
        </div>
      ),
    );
  }
  if (mode) {
    chips.push(
      <div key="mode" className="wt-chip">
        {mode.name}: {entityLabel(mode, kindsById[mode.kind])}
      </div>,
    );
  }
  chips.push(<WeatherChip key="wx" />);
  chips.push(<NowPlayingChip key="music" />);
  chips.push(<div key="gap" className="wt-chip-gap" />);
  chips.push(<WeatherWarningChip key="wxwarn" />);
  if (bad.length) {
    chips.push(
      <button key="bad" ref={badRef} type="button" className="wt-chip wt-chip--bad" onClick={() => setSickOpen(true)} aria-haspopup="dialog">
        <Icon name="alert" size={18} strokeWidth={2.2} />
        {bad.length === 1 ? `${bad[0].name}: ${(bad[0].health.issues || [])[0] || 'needs attention'}` : `${bad.length} devices need attention`}
      </button>,
    );
  }
  return (
    <div className="wt-chips">
      {chips}
      {sickOpen && bad.length ? <AttentionSheet list={bad} onClose={closeSick} host={badRef.current && badRef.current.closest('.wall')} /> : null}
    </div>
  );
}

/** What a device's health says is wrong, as one line. */
const issuesText = (e) => ((e.health && e.health.issues) || []).join(' · ') || 'Needs attention';

/** Every device that needs attention, on a sheet over the wall; a row opens that device's sheet. */
function AttentionSheet({ list, onClose, host }) {
  const { kindsById } = useHub();
  const { openDevice, sheet } = useDeviceOpener();
  const deviceOpen = !!sheet;
  useEffect(() => {
    if (deviceOpen) return undefined; // the device's own sheet takes Escape while it's up
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, deviceOpen]);
  const title = list.length === 1 ? '1 device needs attention' : `${list.length} devices need attention`;
  const view = (
    <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wx-sheet wt-sick-sheet">
        <div className="wt-sick-title">
          <Icon name="alert" size={22} strokeWidth={2.2} />
          <span>{title}</span>
        </div>
        <div className="wx-sheet-body">
          {list.map((e) => (
            <div key={e.id} className="wt-sick-entry">
            <button type="button" className="wt-sick-row" onClick={() => openDevice({ entity: e.id, status: issuesText(e) })}>
              <span className="wt-sick-icon">
                <Icon name={kindIcon(kindsById[e.kind])} size={24} />
              </span>
              <span className="wt-sick-text">
                <span className="wt-sick-name">{e.name}</span>
                <span className="wt-sick-issues">{issuesText(e)}</span>
              </span>
              <Icon name="chevronRight" size={20} />
            </button>
            <ProblemList entity={e} />
            </div>
          ))}
        </div>
        <button type="button" className="wx-sheet-close" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
  return (
    <>
      {host ? createPortal(view, host) : view}
      {sheet}
    </>
  );
}
