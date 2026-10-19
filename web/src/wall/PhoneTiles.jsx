// Tiles the way the phone apps draw them (android ui/tiles, ios Views/Tiles), for the phone layout
// editor's preview: each tile gets its exact cell rectangle in px and lays itself out to fit it -
// a smaller ring, fewer rows and "+N more", buttons that wrap or give way to "More…" - so nothing
// is ever clipped or overlapping. The wall tablet keeps its own tiles (tiles.jsx); this is only used
// through <Tile phone={…}>. Devices come only from each tile's config; what they can do from /api/meta.

import { useApi, useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { fmtClock, fmtDistance } from '../format.js';
import { entityLabel, entityStateText, isActive, deviceIcon, notSecure, plainCommands, stateChoices, stateLabelOf } from '../model.js';
import { formatPropValue } from '../automation.js';
import LiveVideo from '../components/LiveVideo.jsx';
import { Avatar } from '../components/FamilyBits.jsx';
import { useWallCommand } from './PinPad.jsx';
import { MODE_COLOR, MODE_ICON, MODE_WORD, NEUTRAL } from './Dial.jsx';
import { deviceVitals } from './DeviceSheet.jsx';
import { WeatherIcon, hasAgenda, hasForecast } from './WeatherCalendar.jsx';
import { greeting, levelColour, useToday } from './today.js';
import './phone-tiles.css';

const PAD = 14; // a phone card's padding (GlassCard)
const LINE = 18; // one line of small text
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const deg = (v) => (typeof v === 'number' && Number.isFinite(v) ? Math.round(v) + '°' : '–');
/** How many rows of `row` px with `gap` between them fit in `avail` px. */
const rowsIn = (avail, row, gap = 0) => Math.max(0, Math.floor((avail + gap) / (row + gap)));

/**
 * Rows that fit, the rest counted: { shown, more, left } - room is kept for a `moreH` "+N more" line
 * unless that would leave no row at all (then the count shows only where `left` has room for it).
 */
function fitList(items, avail, row, gap, moreH) {
  const all = rowsIn(avail, row, gap);
  if (all >= items.length) return { shown: items, more: 0, left: avail - items.length * (row + gap) + gap };
  const n = Math.max(Math.min(all, 1), moreH ? rowsIn(avail - moreH, row, gap) : all);
  return { shown: items.slice(0, n), more: items.length - n, left: avail - n * (row + gap) + (n ? gap : 0) };
}

/** A muted note that wraps but never runs past `avail` px (whole lines only). */
function Note({ children, avail, center }) {
  const lines = Math.max(1, Math.floor(avail / LINE));
  return (
    <div className={'pt-note' + (center ? ' is-center' : '')} style={{ WebkitLineClamp: lines, maxHeight: lines * LINE }}>
      {children}
    </div>
  );
}

/** A tile's title row; the note on its right (a count) only when the tile is wide enough for both. */
function Head({ icon, children, note, noteColor, iw = 999, h = 22 }) {
  return (
    <div className="pt-head" style={{ height: h }}>
      {icon && iw >= 120 ? <Icon name={icon} size={17} strokeWidth={2.2} /> : null}
      <span className="pt-head-text">{children}</span>
      {note && iw >= 130 ? (
        <span className="pt-head-note" style={noteColor ? { color: noteColor } : undefined}>
          {note}
        </span>
      ) : null}
    </div>
  );
}

/* ---------------- house mode (an alarm) ---------------- */

const btnWidth = (label) => label.length * 7.8 + 30; // a 14px heavy label in a pill, roughly

function PhoneMode({ tile, iw, ih }) {
  const { entitiesById, kindsById } = useHub();
  const { run, busy, mode } = useWallCommand();
  const e = entitiesById[tile.config.entity];
  if (!e) return <Note avail={ih}>This device is missing.</Note>;
  const kind = kindsById[e.kind];
  const cmds = plainCommands(kind).filter((c) => c.confirmsTo !== e.state);
  const state = e.available === false ? 'Offline' : entityLabel(e, kind);
  const stateCls = 'pt-mode-state' + (iw < 100 ? ' is-small' : '');
  const stacked = iw < 170; // name over state; otherwise one row
  const headH = stacked ? 42 : 28;
  const gap = 8;
  const off = e.available === false;
  const button = (c) => (
    <button key={c.id} type="button" className="pt-btn" disabled={off || !!busy[e.id]} onClick={() => run(e, c)} title={c.label}>
      <span className="pt-ell">{c.label}</span>
      {c.sensitive && mode === 'pin' ? <span className="wt-pin-tag">PIN</span> : null}
    </button>
  );
  const more = (n) => (
    <button key="more" type="button" className="pt-btn pt-btn--more" title={`${n} more`}>
      More…
    </button>
  );
  // Buttons under the name: one row when every label fits, else two columns (one when narrow).
  const one = cmds.length && cmds.every((c) => btnWidth(c.label) <= (iw - gap * (cmds.length - 1)) / cmds.length);
  const cols = Math.max(1, Math.min(cmds.length || 1, one ? cmds.length : iw >= 200 ? 2 : 1));
  const below = ih - headH - 10;
  let btnH = 44;
  let rows = rowsIn(below, btnH, gap);
  if (rows < Math.ceil(cmds.length / cols)) {
    btnH = Math.max(34, Math.min(44, Math.floor((below - gap * (Math.ceil(cmds.length / cols) - 1)) / Math.ceil(cmds.length / cols))));
    rows = rowsIn(below, btnH, gap);
  }
  const head = (
    <div className={'pt-mode-head' + (stacked ? ' is-stacked' : '')} style={{ height: headH }}>
      <span className="pt-mode-name">{e.name}</span>
      <span className={stateCls}>{state}</span>
    </div>
  );
  if (!cmds.length) return head;
  if (rows >= 1) {
    const slots = rows * cols;
    const list = cmds.length <= slots ? cmds.map(button) : [...cmds.slice(0, slots - 1).map(button), more(cmds.length - slots + 1)];
    return (
      <div className="pt-col">
        {head}
        <div className="pt-mode-btns" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridAutoRows: btnH, marginTop: 10 }}>
          {list}
        </div>
      </div>
    );
  }
  // Short (one row of cells): the name and state on the left, as many buttons as fit beside them.
  const textW = Math.min(150, Math.max(80, e.name.length * 6.6, state.length * 11.5));
  const fit = Math.floor((iw - textW + gap) / (84 + gap));
  const h = Math.min(44, ih);
  if (fit < 1 || ih < 30) {
    return (
      <div className="pt-mode-head is-stacked" style={{ height: ih }}>
        <span className="pt-mode-name">{e.name}</span>
        <span className={stateCls}>{state}</span>
      </div>
    );
  }
  const list = cmds.length <= fit ? cmds.map(button) : [...cmds.slice(0, fit - 1).map(button), more(cmds.length - fit + 1)];
  return (
    <div className="pt-mode-inline">
      <div className="pt-mode-head is-stacked" style={{ width: textW, height: ih }}>
        <span className="pt-mode-name">{e.name}</span>
        <span className={stateCls}>{state}</span>
      </div>
      <div className="pt-mode-btns" style={{ gridTemplateColumns: `repeat(${list.length}, minmax(0, 1fr))`, gridAutoRows: h, flex: 1 }}>
        {list}
      </div>
    </div>
  );
}

/* ---------------- dial (a thermostat) ---------------- */

const C = 180;
const R = 150;
const pol = (d, r = R) => [C + r * Math.cos((d * Math.PI) / 180), C + r * Math.sin((d * Math.PI) / 180)];
function arc(a0, a1) {
  const p0 = pol(a0);
  const p1 = pol(a1);
  return `M ${p0[0].toFixed(1)} ${p0[1].toFixed(1)} A ${R} ${R} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${p1[0].toFixed(1)} ${p1[1].toFixed(1)}`;
}

function dialModel(tile, e, kind) {
  const cmd = ((kind && kind.commands) || []).find((c) => c.id === tile.config.command) || ((kind && kind.commands) || []).find((c) => c.valueType === 'number');
  if (!e || !cmd) return null;
  const modeCmd = tile.config.modeCommand ? ((kind && kind.commands) || []).find((c) => c.id === tile.config.modeCommand) : null;
  const min = typeof cmd.min === 'number' ? cmd.min : 0;
  const max = typeof cmd.max === 'number' ? cmd.max : 100;
  const step = cmd.step || 1;
  const unit = cmd.unit || '';
  const current = cmd.attr && e.attrs && typeof e.attrs[cmd.attr] === 'number' ? e.attrs[cmd.attr] : null;
  const modes = modeCmd ? stateChoices(kind) : [];
  const mode = modeCmd ? e.state : null;
  const off = mode === 'off';
  const color = (mode && MODE_COLOR[mode]) || NEUTRAL;
  const word = mode && MODE_WORD[mode] ? MODE_WORD[mode] : off ? 'Off' : cmd.label;
  const props = e.properties || [];
  const nowProp = props.find((p) => p.type === 'number' && p.unit === unit && p.attr !== cmd.attr);
  const pctProp = props.find((p) => p.type === 'number' && p.unit === '%' && p.attr !== cmd.attr);
  const activity = props.find((p) => p.type === 'text' && (p.options || []).length && p.attr);
  const status = [mode ? stateLabelOf(kind, mode) : null, activity && activity.value ? formatPropValue(activity, activity.value).toLowerCase() : null].filter(Boolean).join(' · ');
  const decimals = String(step).includes('.') ? String(step).split('.')[1].length : 0;
  const clamp = (v) => Math.min(max, Math.max(min, Math.round(v / step) * step));
  const angle = (v) => 135 + ((clamp(v) - min) / (max - min || 1)) * 270;
  const text = off || typeof current !== 'number' ? '—' : `${current.toFixed(decimals)}${unit.startsWith('°') ? '°' : ''}`;
  const nowShort = nowProp ? `Now ${formatPropValue(nowProp, nowProp.value).replace(/ °([CF])$/, '°')}` : '';
  const nowLong = [nowProp ? `Now ${formatPropValue(nowProp, nowProp.value)}` : null, pctProp ? `${pctProp.label} ${formatPropValue(pctProp, pctProp.value)}` : null].filter(Boolean).join(' · ');
  return { e, kind, cmd, modeCmd, modes, mode, off, color, word, status, current, angle, text, nowShort, nowLong, nowProp };
}

/** The ring, `size` px square, with the number centred inside; the word and "now" line only when there's room. */
function Ring({ m, size, big }) {
  const sw = big ? 26 : 32;
  const end = typeof m.current === 'number' ? m.angle(m.current) : 135;
  const k = pol(end);
  const now = big && m.nowProp && typeof m.nowProp.value === 'number' ? pol(m.angle(m.nowProp.value)) : null;
  const value = Math.round(size * (big ? 0.2 : 0.22));
  const small = Math.max(9, Math.min(13, Math.round(size * 0.045)));
  const showWord = big && size >= 150;
  const showNow = size >= 96;
  return (
    <div className="pt-ring" style={{ width: size, height: size }}>
      <svg viewBox="0 0 360 360" width={size} height={size} aria-hidden="true">
        <path d={arc(135, 405)} fill="none" stroke="rgba(255,255,255,0.10)" strokeWidth={sw} strokeLinecap="round" />
        {!m.off && typeof m.current === 'number' ? (
          <>
            <path d={arc(135, Math.max(135.1, end))} fill="none" stroke={m.color} strokeWidth={sw} strokeLinecap="round" />
            {now ? <circle cx={now[0]} cy={now[1]} r="5" fill="#EEF2FF" opacity="0.7" /> : null}
            <circle cx={k[0]} cy={k[1]} r={sw * 0.72} fill="#EEF2FF" />
            {big ? <circle cx={k[0]} cy={k[1]} r={sw * 0.3} fill={m.color} /> : null}
          </>
        ) : null}
      </svg>
      {/* Inside the ring's opening (about 60% of it), so nothing touches the arc. */}
      <div className="pt-ring-center" style={{ width: size * 0.6, paddingTop: big ? 0 : size * 0.04 }}>
        {showWord ? (
          <span className="pt-ring-word" style={{ color: m.color, fontSize: small - 1 }}>
            {m.word}
          </span>
        ) : null}
        <span className="pt-ring-value" style={{ fontSize: value, lineHeight: 1.05 }}>
          {m.text}
        </span>
        {showNow ? (
          <span className="pt-ring-now" style={{ fontSize: small }}>
            {big && size >= 200 ? m.nowLong : m.nowShort}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function ModeRow({ m, iw, avail }) {
  const { run } = useWallCommand();
  // A segmented row (two when four don't fit the width), only whole rows that fit.
  const cols = Math.max(1, Math.min(m.modes.length, Math.floor((iw - 8) / 56)));
  const rowsNeeded = Math.ceil(m.modes.length / cols);
  const rows = Math.min(rowsNeeded, rowsIn(avail - 8, 36, 4));
  if (!rows) return null;
  const shown = m.modes.slice(0, rows * cols);
  const icons = (iw - 8) / cols >= 84;
  return (
    <div className="pt-modes" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }} role="group" aria-label={m.modeCmd.label}>
      {shown.map((s) => {
        const on = s.id === m.mode;
        return (
          <button key={s.id} type="button" className="pt-mode" aria-pressed={on} style={on ? { background: MODE_COLOR[s.id] || NEUTRAL, color: '#0B1026' } : undefined} onClick={() => !on && run(m.e, m.modeCmd, s.id)}>
            {icons ? (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d={MODE_ICON[s.id] || 'M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0'} />
              </svg>
            ) : null}
            <span className="pt-ell">{s.label}</span>
          </button>
        );
      })}
    </div>
  );
}

const modeRowH = (m, iw) => (m.modes.length ? Math.ceil(m.modes.length / Math.max(1, Math.min(m.modes.length, Math.floor((iw - 8) / 56)))) * 40 + 8 - 4 : 0);

function PhoneDial({ tile, iw, ih }) {
  const { entitiesById, kindsById } = useHub();
  const e = entitiesById[tile.config.entity];
  const m = dialModel(tile, e, e && kindsById[e.kind]);
  if (!m) return <Note avail={ih}>This dial's device is missing.</Note>;
  const wide = tile.w >= 4 && tile.h >= 4; // the phone's full dial (PhoneGrid.isCompact)
  if (wide) {
    // Name and status, the ring with − / + beside it (below it when tall), the mode buttons.
    const headH = 30;
    const modesH = m.modes.length ? modeRowH(m, iw) : 0;
    const area = ih - headH - 10 - (modesH ? modesH + 10 : 0);
    const below = area >= Math.min(iw, 280) + 66;
    const ring = below ? Math.min(iw, 280, area - 66) : Math.max(60, Math.min(area, iw - 2 * (44 + 10), 300));
    const round = (sign) => (
      <button type="button" className="pt-round" disabled={m.off} aria-label={sign === '+' ? 'Higher' : 'Lower'}>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
          <path d={sign === '+' ? 'M5 12h14M12 5v14' : 'M5 12h14'} />
        </svg>
      </button>
    );
    return (
      <div className="pt-col pt-dial">
        <div className="pt-dial-head" style={{ height: headH }}>
          <span className="pt-dial-name">{m.e.name}</span>
          <span className="pt-dial-status" style={{ color: m.color }}>
            {m.status}
          </span>
        </div>
        {below ? (
          <div className="pt-dial-stack" style={{ height: area, marginTop: 10 }}>
            <Ring m={m} size={ring} big />
            <div className="pt-dial-pm">
              {round('-')}
              {round('+')}
            </div>
          </div>
        ) : (
          <div className="pt-dial-row" style={{ height: area, marginTop: 10 }}>
            {round('-')}
            <Ring m={m} size={ring} big />
            {round('+')}
          </div>
        )}
        {modesH ? (
          <div style={{ marginTop: 10 }}>
            <ModeRow m={m} iw={iw} avail={modesH + 8} />
          </div>
        ) : null}
      </div>
    );
  }
  // The compact dial (CompactDial): the name and mode over a ring sized to the room left.
  const label = m.mode ? stateLabelOf(m.kind, m.mode) : '';
  if (ih < 100 && iw >= ih * 1.6) {
    // One row of cells: the ring on the left, the words beside it.
    return (
      <div className="pt-dial-side">
        <Ring m={m} size={ih} />
        <div className="pt-dial-words">
          <span className="pt-dial-sname">{m.e.name}</span>
          {label ? (
            <span className="pt-dial-smode" style={{ color: m.color }}>
              {m.status || label}
            </span>
          ) : null}
          {m.nowShort ? <span className="pt-dial-snow">{m.nowShort}</span> : null}
        </div>
      </div>
    );
  }
  const headH = ih - 24 >= 60 ? 24 : 0;
  const ring = Math.max(24, Math.min(iw, ih - headH));
  const left = ih - headH - ring;
  return (
    <div className="pt-col pt-dial">
      {headH ? (
        <div className="pt-dial-chead" style={{ height: headH }}>
          <span className="pt-dial-sname">{m.e.name}</span>
          {label && iw >= 120 ? (
            <span className="pt-dial-smode" style={{ color: m.color }}>
              {label}
            </span>
          ) : null}
        </div>
      ) : null}
      <div className="pt-dial-ringbox" style={{ height: ring }}>
        <Ring m={m} size={ring} />
      </div>
      {m.modes.length && left >= 44 && iw >= 120 ? (
        <div style={{ marginTop: 8 }}>
          <ModeRow m={m} iw={iw} avail={left - 8} />
        </div>
      ) : null}
    </div>
  );
}

/* ---------------- readings ---------------- */

function PhoneValues({ tile, iw, ih }) {
  const { entitiesById } = useHub();
  const items = (tile.config.items || [])
    .map((it) => {
      const e = entitiesById[it.entity];
      const p = e && (e.properties || []).find((x) => x.id === it.property);
      return e ? { key: it.entity + ':' + it.property, e, p } : null;
    })
    .filter(Boolean);
  let title = iw >= 100 ? tile.config.title || '' : ''; // a narrow tile keeps its room for a reading
  const titleH = 28;
  if (!items.length) {
    return (
      <div className="pt-col">
        {title && ih >= titleH + LINE ? <Head>{title}</Head> : null}
        <Note avail={ih - (title && ih >= titleH + LINE ? titleH : 0)}>No readings chosen.</Note>
      </div>
    );
  }
  // Full rows (name, what it is, the value), stacked ones when narrow, one-line ones when short.
  const pick = (avail, withMore) => {
    const styles = iw < 150 ? [['stack', 44], ['line', 24]] : [['full', 52], ['line', 26]];
    for (const [look, h] of styles) {
      const f = fitList(items, avail, h, 0, withMore ? LINE : 0);
      if (f.shown.length) return { look, h, ...f };
    }
    return { look: 'line', h: 24, shown: [], more: items.length };
  };
  let fit = pick(ih - (title ? titleH : 0), !title);
  if (!fit.shown.length && title) {
    title = ''; // no room for both: the readings matter more
    fit = pick(ih, true);
  }
  const moreText = fit.more ? `+${fit.more} more` : '';
  return (
    <div className="pt-col">
      {title ? (
        <Head iw={iw} note={moreText}>
          {title}
        </Head>
      ) : null}
      {title ? <div style={{ height: titleH - 22 }} /> : null}
      {fit.shown.map(({ key, e, p }, i) => (
        <div key={key} className={'pt-val pt-val--' + fit.look + (i < fit.shown.length - 1 ? ' has-line' : '')} style={{ height: fit.h }}>
          {fit.look === 'stack' ? (
            <>
              <span className="pt-val-value">{p ? formatPropValue(p, p.value) : '—'}</span>
              <span className="pt-val-name">{e.name}</span>
            </>
          ) : (
            <>
              <span className="pt-val-text">
                <span className="pt-val-name">{e.name}</span>
                {fit.look === 'full' ? <span className="pt-val-label">{p ? p.label : 'Not reported'}</span> : null}
              </span>
              <span className="pt-val-value">{p ? formatPropValue(p, p.value) : '—'}</span>
            </>
          )}
        </div>
      ))}
      {(!title || iw < 130) && moreText && fit.left >= LINE ? <div className="pt-more">{moreText}</div> : null}
    </div>
  );
}

/* ---------------- devices ---------------- */

// PhoneGrid.fitDevices: sizes the phone uses for a devices tile's contents.
const DEV = { pad: 10, title: 28, row: 56, gap: 8, twoFrom: 240 };

function DeviceButton({ e, kind, w }) {
  const { run, busy } = useWallCommand();
  const plain = plainCommands(kind);
  const action =
    (kind && kind.activeState && plain.length === 2 && !plain.some((c) => c.sensitive) && (() => {
      const on = plain.find((c) => c.confirmsTo === kind.activeState);
      const offc = plain.find((c) => c !== on && c.confirmsTo);
      return on && offc ? (isActive(e, kind) ? offc : on) : null;
    })()) ||
    plain.find((c) => c.confirmsTo && c.confirmsTo !== e.state) ||
    (plain.length === 1 ? plain[0] : null);
  const { battery, problem } = deviceVitals(e);
  const off = e.available === false;
  const lit = isActive(e, kind) && !(kind && kind.secureState) && !off;
  const warn = !!problem || notSecure(e, kind);
  const calm = !warn && kind && kind.secureState;
  const state = [entityStateText(e, kind), battery !== null ? `${battery}%` : '', problem || ''].filter(Boolean).join(' · ');
  return (
    <button type="button" className={'pt-devbtn' + (lit ? ' is-lit' : '') + (warn ? ' is-warn' : '') + (off ? ' is-off' : '')} disabled={!action || off || !!busy[e.id]} onClick={() => action && run(e, action)} title={e.name}>
      {w >= 110 ? <Icon name={deviceIcon(e, kind)} size={22} /> : null}
      <span className="pt-devbtn-text">
        <span className="pt-devbtn-name">{e.name}</span>
        <span className={'pt-devbtn-state' + (warn && !lit ? ' is-warn' : calm ? ' is-calm' : '')}>{state}</span>
      </span>
    </button>
  );
}

function PhoneDevices({ tile, W, H }) {
  const { entitiesById, kindsById } = useHub();
  let list = (tile.config.entities || []).map((id) => entitiesById[id]).filter(Boolean);
  if (tile.config.groupBy === 'area') list = list.slice().sort((a, b) => ((a.attrs && a.attrs.area) || '~').localeCompare((b.attrs && b.attrs.area) || '~'));
  let title = tile.config.title || '';
  const columns = tile.config.style !== 'list' && W >= DEV.twoFrom ? 2 : 1;
  let avail = H - 2 * DEV.pad - (title ? DEV.title : 0);
  if (title && avail < 34) {
    title = '';
    avail = H - 2 * DEV.pad;
  }
  const rows = Math.max(1, rowsIn(avail, DEV.row, DEV.gap));
  const rowH = rows === 1 ? Math.max(24, Math.min(DEV.row, avail)) : DEV.row;
  const cap = rows * columns;
  const shown = list.length <= cap ? list : list.slice(0, cap - 1);
  const more = list.length - shown.length;
  const cellW = (W - 2 * DEV.pad - (columns - 1) * DEV.gap) / columns;
  return (
    <div className="pt-col">
      {title ? (
        <div className="pt-dev-title" style={{ height: DEV.title }}>
          {title}
        </div>
      ) : null}
      {!list.length ? <Note avail={avail}>No devices chosen.</Note> : null}
      <div className="pt-dev-grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gridAutoRows: rowH, gap: DEV.gap }}>
        {shown.map((e) => (
          <DeviceButton key={e.id} e={e} kind={kindsById[e.kind]} w={cellW} />
        ))}
        {more > 0 ? (
          <button type="button" className="pt-devbtn pt-devbtn--more">
            +{more} more
          </button>
        ) : null}
      </div>
    </div>
  );
}

/* ---------------- needs attention ---------------- */

function PhoneAttention({ iw, ih }) {
  const { entities, kindsById } = useHub();
  const alarms = (entities || []).filter((e) => {
    const k = kindsById[e.kind];
    return k && k.urgent && k.secureState && notSecure(e, k);
  });
  const sick = (entities || []).filter((e) => e.health && e.health.ok === false && !alarms.includes(e));
  const bad = [
    ...alarms.map((e) => ({ id: e.id, name: e.name, issues: [entityLabel(e, kindsById[e.kind]), ...((e.health && e.health.issues) || [])], alarm: true })),
    ...sick.map((e) => ({ id: e.id, name: e.name, issues: (e.health && e.health.issues) || [] })),
  ];
  if (iw < 140) {
    // Narrow: how many, large, then whichever names fit.
    const fitN = fitList(bad, ih - 48, 18, 0, 0);
    return (
      <div className={'pt-col pt-attn is-narrow' + (bad.length ? ' is-bad' : '')}>
        <span className="pt-attn-count">{bad.length ? bad.length : <Icon name="check" size={24} strokeWidth={2.4} />}</span>
        <span className="pt-attn-what">{bad.length ? 'need attention' : 'All OK'}</span>
        {bad.length
          ? fitN.shown.map((a) => (
              <span key={a.id} className="pt-attn-name is-small">
                {a.name}
              </span>
            ))
          : null}
      </div>
    );
  }
  const fit = fitList(bad, ih - 30, 36, 8, 0);
  return (
    <div className={'pt-col pt-attn' + (bad.length ? ' is-bad' : '')}>
      <Head icon={bad.length ? 'alert' : 'check'} iw={iw} note={fit.more ? `+${fit.more} more` : ''}>
        {bad.length ? 'Needs attention' : 'All devices OK'}
      </Head>
      {fit.shown.length ? <div style={{ height: 8 }} /> : null}
      <div className="pt-attn-list">
        {fit.shown.map((a) => (
          <div key={a.id} className={'pt-attn-item' + (a.alarm ? ' is-alarm' : '')}>
            <span className="pt-attn-name">{a.name}</span>
            <span className="pt-attn-issues">{a.issues.join(' · ')}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- camera, alerts ---------------- */

function PhoneCamera({ tile, iw, ih }) {
  const { entitiesById } = useHub();
  const cams = (tile.config.entities || []).map((id) => entitiesById[id]).filter(Boolean);
  const cam = cams[0];
  if (!cam) return <Note avail={ih} center>No cameras chosen for this tile.</Note>;
  const pills = cams.length > 1 && ih >= 150;
  return (
    <div className="pt-col">
      <div className="pt-cam" style={{ height: ih - (pills ? 38 : 0) }}>
        <LiveVideo key={cam.id} entity={cam} quality="sub" stillHeight={360} showNote={false} className="wt-cam-video" />
        {iw >= 110 ? (
          <span className="pt-cam-name">
            <span className="wt-live-dot" aria-hidden="true" />
            <span className="pt-ell">{cam.name}</span>
          </span>
        ) : null}
      </div>
      {pills ? (
        <div className="pt-pills">
          {cams.map((c) => (
            <span key={c.id} className={'pt-pill' + (c.id === cam.id ? ' is-on' : '')}>
              {c.name}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PhoneAlerts({ tile, iw, ih }) {
  const { entitiesById } = useHub();
  const ids = tile.config.entities || [];
  const limit = Math.max(1, Math.min(12, Number(tile.config.limit) || 4));
  const one = ids.length === 1 && entitiesById[ids[0]] ? entitiesById[ids[0]].alerts : null;
  const res = useApi((one || 'api/alerts') + '?limit=' + (ids.length > 1 ? 50 : limit));
  const items = (Array.isArray(res.data) ? res.data : []).filter((a) => ids.length <= 1 || ids.includes(a.entityId)).slice(0, limit);
  const thumbs = iw >= 220;
  const rowH = thumbs ? 64 : 40;
  const fit = fitList(items, ih - 30, rowH, 8, 0);
  return (
    <div className="pt-col">
      <Head iw={iw} note={fit.more ? `+${fit.more} more` : ''}>
        Recent alerts
      </Head>
      <div style={{ height: 8 }} />
      {res.data === undefined ? <Note avail={ih - 30}>Loading…</Note> : !items.length ? <Note avail={ih - 30}>Nothing lately.</Note> : null}
      <div className="pt-alerts">
        {fit.shown.map((a) => (
          <div key={a.id} className="pt-alert" style={{ height: rowH }}>
            {thumbs ? <span className="pt-alert-thumb">{a.thumbUrl || a.snapshotUrl ? <img src={a.thumbUrl || a.snapshotUrl} alt="" loading="lazy" /> : null}</span> : null}
            <span className="pt-alert-text">
              <span className="pt-alert-title">{a.title}</span>
              <span className="pt-alert-sub">{[(a.zones || []).join(', ') || a.cameraName, fmtClock(a.start)].filter(Boolean).join(' · ')}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- weather, calendar ---------------- */

const localDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

function PhoneWeather({ tile, iw, ih }) {
  const { entities, entitiesById, kindsById } = useHub();
  const chosen = entitiesById[tile.config.entity];
  const e = hasForecast(chosen) ? chosen : (entities || []).find(hasForecast);
  if (!e || !e.attrs || typeof e.attrs.temperature !== 'number') {
    return <Note avail={ih}>{e ? 'Waiting for the weather…' : 'Add a Weather connection in the Newman dashboard.'}</Note>;
  }
  const a = e.attrs;
  const label = entityLabel(e, kindsById[e.kind]);
  const forecast = Array.isArray(a.forecast) ? a.forecast : [];
  const today = localDay(new Date());
  const todayF = forecast.find((d) => d.date === today) || {};
  const chance = typeof todayF.precipChance === 'number' ? todayF.precipChance : a.precipChance;
  const snow = typeof todayF.snow === 'number' ? todayF.snow : a.snowToday;
  const rain = typeof todayF.rain === 'number' ? todayF.rain : a.rainToday;
  const wet = [typeof chance === 'number' && chance > 0 ? `${Math.round(chance)}% chance today` : '', typeof snow === 'number' && snow > 0 ? `${Math.round(snow * 10) / 10} cm snow` : typeof rain === 'number' && rain > 0 ? `${Math.round(rain * 10) / 10} mm rain` : ''].filter(Boolean).join(' · ');
  const alerts = Array.isArray(a.alerts) ? a.alerts : [];
  const RANK = { red: 3, orange: 2, yellow: 1 };
  const top = alerts.slice().sort((x, y) => (RANK[y.colour] || 0) - (RANK[x.colour] || 0))[0];
  // The main row scales down for one row of cells; then each line only when it fits.
  const mainH = Math.min(40, ih);
  const temp = Math.round(mainH * 0.75);
  let left = ih - mainH;
  const lines = [];
  const add = (key, h, node) => {
    if (left >= h) {
      lines.push(<div key={key}>{node}</div>);
      left -= h;
      return true;
    }
    return false;
  };
  const hl = `${label} · H ${deg(a.high)} L ${deg(a.low)}`;
  const hlBelow = add('hl', LINE + 2, <div className="pt-wx-line">{hl}</div>);
  if (wet) add('wet', LINE, <div className="pt-wx-sub">{wet}</div>);
  if (top) add('alert', LINE + 4, <div className="pt-wx-alert"><Icon name="alert" size={14} strokeWidth={2.2} /> <span className="pt-ell">{top.name || 'Weather alert'}</span></div>);
  const days = tile.w >= 4 ? forecast.filter((d) => (d.date || '') > today).slice(0, 6) : [];
  const nDays = days.length ? rowsIn(left - 8, 26, 6) : 0;
  const shownDays = days.slice(0, nDays);
  const lows = shownDays.map((d) => d.low).filter((v) => typeof v === 'number');
  const highs = shownDays.map((d) => d.high).filter((v) => typeof v === 'number');
  const lo = lows.length ? Math.min(...lows) : 0;
  const span = Math.max(1, (highs.length ? Math.max(...highs) : 0) - lo);
  return (
    <div className="pt-col">
      <div className="pt-wx-main" style={{ height: mainH }}>
        {iw >= 70 ? <WeatherIcon condition={e.state} isDay={a.isDay !== false} size={Math.round(mainH * 0.85)} /> : null}
        <span className="pt-wx-temp" style={{ fontSize: temp }}>
          {deg(a.temperature)}
        </span>
        {/* One row of cells: the condition and high/low beside the temperature instead of under it. */}
        {!hlBelow && iw >= 200 ? <span className="pt-wx-line pt-wx-inline">{hl}</span> : null}
      </div>
      {lines}
      {shownDays.length ? (
        <div className="pt-wx-days">
          {shownDays.map((d) => {
            const s = typeof d.low === 'number' ? (d.low - lo) / span : 0;
            const w = typeof d.low === 'number' && typeof d.high === 'number' ? Math.max(0.04, (d.high - d.low) / span) : 0;
            return (
              <div key={d.date} className="pt-wx-day">
                <span className="pt-wx-dname">{d.day || ''}</span>
                <WeatherIcon condition={d.condition} size={20} />
                <span className="pt-wx-chance">{typeof d.precipChance === 'number' ? `${Math.round(d.precipChance)}%` : ''}</span>
                <span className="pt-wx-lo">{deg(d.low)}</span>
                <span className="pt-wx-bar">
                  <i style={{ left: `${Math.min(96, s * 100)}%`, width: `${Math.min(100 - s * 100, w * 100)}%` }} />
                </span>
                <span className="pt-wx-hi">{deg(d.high)}</span>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function calTime(ev) {
  if (ev.allDay) return 'All day';
  const t = (d) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (localDay(ev.s) !== localDay(ev.e)) return `From ${t(ev.s)}`;
  if (ev.e - ev.s < 60000) return t(ev.s);
  return `${t(ev.s)} – ${t(ev.e)}`;
}
function calDay(key) {
  const now = new Date();
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  if (key === localDay(now)) return 'Today';
  if (key === localDay(tomorrow)) return 'Tomorrow';
  const [y, m, d] = key.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
}

function PhoneCalendar({ tile, iw, ih }) {
  const { entities } = useHub();
  const ids = tile.config.entities || [];
  const cals = (entities || []).filter(hasAgenda).filter((c) => !ids.length || ids.includes(c.id));
  const days = Math.max(1, Math.min(31, Number(tile.config.days) || 31));
  const now = Date.now();
  const todayStart = new Date(new Date(now).setHours(0, 0, 0, 0));
  const until = new Date(todayStart.getFullYear(), todayStart.getMonth(), todayStart.getDate() + days).getTime();
  const events = [];
  cals.forEach((c) =>
    (c.attrs.upcoming || []).forEach((ev, i) => {
      const s = new Date(ev.start);
      const e = new Date(ev.end || ev.start);
      if (Number.isNaN(s.getTime()) || e.getTime() <= now || s.getTime() >= until) return;
      events.push({ ...ev, key: c.id + ':' + i, s, e, day: localDay(s < todayStart ? todayStart : s), colour: c.attrs.colour || '#3CCFB4', on: !ev.allDay && s.getTime() <= now && now < e.getTime() });
    }),
  );
  events.sort((a, b) => (a.day === b.day ? (a.allDay === b.allDay ? a.s - b.s : a.allDay ? -1 : 1) : a.day < b.day ? -1 : 1));
  const all = events.slice(0, 8);
  const head = ih >= 22 + LINE + 8;
  const fit = fitList(all, ih - (head ? 30 : 0), 38, 8, 0);
  const avail = ih - (head ? 30 : 0);
  const inline = !fit.shown.length && all.length > 0 && avail >= LINE; // no room for a row: the next event on one line
  const more = fit.more - (inline ? 1 : 0);
  return (
    <div className="pt-col">
      {head ? (
        <Head icon="calendar" iw={iw} note={more > 0 ? `+${more} more` : ''}>
          Coming up
        </Head>
      ) : null}
      {head ? <div style={{ height: 8 }} /> : null}
      {!(entities || []).some(hasAgenda) ? <Note avail={avail}>{iw < 100 ? 'No calendars yet.' : 'Add a Calendars connection in the Newman dashboard.'}</Note> : !all.length ? <Note avail={avail}>Nothing coming up.</Note> : null}
      {inline ? <span className="pt-greet-line">{`${all[0].title} · ${calDay(all[0].day)} ${calTime(all[0])}`}</span> : null}
      <div className="pt-cal">
        {fit.shown.map((ev) => (
          <div key={ev.key} className="pt-cal-ev">
            <span className="pt-cal-bar" style={{ background: ev.colour }} />
            <span className="pt-cal-text">
              <span className="pt-cal-title">{(ev.on ? 'Now · ' : '') + ev.title}</span>
              <span className="pt-cal-time">{[calDay(ev.day), calTime(ev), ev.location].filter(Boolean).join(' · ')}</span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------- today, to do, greeting, people ---------------- */

function PhoneToday({ tile, iw, ih }) {
  const { data } = useToday();
  const todoOnly = tile.type === 'todo';
  if (!data) return <Note avail={ih}>Loading today…</Note>;
  const todo = [...(data.todo || []), ...(data.soon || [])];
  const events = todoOnly ? [] : data.events || [];
  const open = (data.todo || []).length;
  const summary = [open ? plural(open, 'thing to do', 'things to do') : 'Nothing to do', events.length ? plural(events.length, 'event', 'events') : ''].filter(Boolean).join(' · ');
  const rows = [
    ...todo.map((x) => ({ id: 'td:' + x.id, title: x.title, sub: x.status, colour: levelColour(x.level) })),
    ...events.map((ev, i) => ({ id: 'ev:' + i, title: ev.title, sub: ev.now ? 'Now' : ev.time, colour: ev.colour || 'var(--wt-teal)' })),
  ];
  const headH = iw >= 200 ? 24 : 42;
  const fit = fitList(rows, ih - headH - 8, 40, 6, LINE);
  return (
    <div className="pt-col">
      <div className={'pt-today-head' + (iw >= 200 ? '' : ' is-stacked')} style={{ height: Math.min(headH, ih) }}>
        <span className="pt-today-title">{todoOnly ? 'To do' : 'Today'}</span>
        <span className="pt-today-sum">{summary}</span>
      </div>
      {fit.shown.length ? <div style={{ height: 8 }} /> : null}
      {!rows.length && ih - headH - 8 >= LINE ? <Note avail={ih - headH - 8}>Nothing needs doing today.</Note> : null}
      {!fit.shown.length && rows.length > 0 && ih - headH - 8 >= LINE ? (
        <div style={{ marginTop: 8 }}>
          <span className="pt-greet-line" style={{ display: 'block' }}>{`${rows[0].title} · ${rows[0].sub}`}</span>
        </div>
      ) : null}
      <div className="pt-today-list">
        {fit.shown.map((r) => (
          <div key={r.id} className="pt-today-row">
            <span className="pt-today-stripe" style={{ background: r.colour }} />
            <span className="pt-today-text">
              <span className="pt-today-rtitle">{r.title}</span>
              <span className="pt-today-rsub" style={{ color: r.colour }}>
                {r.sub}
              </span>
            </span>
          </div>
        ))}
      </div>
      {fit.more && fit.shown.length && fit.left >= LINE ? <div className="pt-more">+{fit.more} more</div> : null}
    </div>
  );
}

function PhoneGreeting({ ih }) {
  const { data } = useToday();
  const size = Math.max(14, Math.min(26, Math.round(ih * 0.42)));
  const open = data ? (data.todo || []).length : 0;
  const events = data ? (data.events || []).length : 0;
  const line = data ? [open ? plural(open, 'thing to do', 'things to do') : 'Nothing to do', events ? plural(events, 'event today', 'events today') : ''].filter(Boolean).join(' · ') : '';
  return (
    <div className="pt-col pt-greet">
      <span className="pt-greet-hello" style={{ fontSize: size }}>
        {greeting()}
      </span>
      {line && ih >= size * 1.3 + LINE ? <span className="pt-greet-line">{line}</span> : null}
    </div>
  );
}

function wherePerson(p) {
  if (!p.sharing) return 'Not sharing';
  if (typeof p.lat !== 'number') return 'No location yet';
  if (p.home) return 'Home';
  if (p.place) return p.place;
  return typeof p.distanceM === 'number' ? `${fmtDistance(p.distanceM)} away` : 'Away';
}

function PhonePeople({ tile, iw, ih }) {
  const people = useApi('api/people', { interval: 300000 });
  const pick = Array.isArray(tile.config.people) ? tile.config.people : [];
  const all = Array.isArray(people.data) ? people.data : [];
  const list = pick.length ? all.filter((p) => pick.includes(p.id)) : all;
  const head = ih >= 22 + 40 + 8;
  const avail = ih - (head ? 30 : 0);
  const fit = fitList(list, avail, 44, 6, head ? 0 : LINE);
  if (!head && list.length > 1 && ih >= 22 + LINE) {
    // No room for rows: who's there, on one line.
    return (
      <div className="pt-col pt-greet">
        <Head>People</Head>
        <span className="pt-greet-line">{list.map((p) => `${p.name}${p.sharing && p.home ? ' (home)' : ''}`).join(' · ')}</span>
      </div>
    );
  }
  return (
    <div className="pt-col">
      {head ? (
        <Head iw={iw} note={fit.more ? `+${fit.more} more` : ''}>
          People
        </Head>
      ) : null}
      {head ? <div style={{ height: 8 }} /> : null}
      {people.data === undefined ? <Note avail={avail}>Loading…</Note> : !list.length ? <Note avail={avail}>Nobody to show.</Note> : null}
      <div className="pt-people">
        {fit.shown.map((p) => (
          <div key={p.id} className="pt-person">
            {iw >= 100 ? (
              <Avatar person={p} size={36} className={p.sharing ? '' : 'is-dim'} />
            ) : null}
            <span className="pt-person-text">
              <span className="pt-person-name">{p.name}</span>
              <span className={'pt-person-where' + (p.home && p.sharing ? ' is-calm' : '')}>{wherePerson(p)}</span>
            </span>
          </div>
        ))}
      </div>
      {(!head || iw < 130) && fit.more && fit.left >= LINE ? <div className="pt-more">+{fit.more} more</div> : null}
    </div>
  );
}

function PhoneSoon({ tile, ih }) {
  return (
    <div className="pt-col pt-soon">
      {ih >= 60 ? <Icon name="grid" size={22} /> : null}
      <span className="pt-ell">{tile.type}</span>
    </div>
  );
}

const BODIES = {
  mode: PhoneMode,
  dial: PhoneDial,
  values: PhoneValues,
  attention: PhoneAttention,
  camera: PhoneCamera,
  alerts: PhoneAlerts,
  weather: PhoneWeather,
  calendar: PhoneCalendar,
  today: PhoneToday,
  todo: PhoneToday,
  greeting: PhoneGreeting,
  people: PhonePeople,
};
// Cards with their own padding (PhoneGrid.DEVICE_PADDING, the camera's 8 and the weather's 12 dp).
const PADS = { devices: DEV.pad, camera: 8, weather: 12, dial: 12 };

/** A tile drawn the phone's way inside its `width` × `height` px cell rectangle. */
export default function PhoneTileBody({ tile, width, height }) {
  const pad = PADS[tile.type] ?? PAD;
  const iw = Math.max(0, width - 2 * pad);
  const ih = Math.max(0, height - 2 * pad);
  let body;
  if (tile.type === 'devices') body = <PhoneDevices tile={tile} W={width} H={height} />;
  else {
    const Body = BODIES[tile.type] || PhoneSoon;
    body = <Body tile={tile} iw={iw} ih={ih} />;
  }
  return (
    <div className={'pt pt--' + tile.type} style={{ padding: pad }}>
      <div className="pt-box" style={{ width: iw, height: ih }}>
        {body}
      </div>
    </div>
  );
}
