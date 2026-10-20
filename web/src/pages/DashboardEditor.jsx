// The dashboard layout editor: the real wall tiles (round 18) on the tablet's 24 × 16 grid, at the
// tablet's shape. Drag to move, drag the corner to resize, keys for both; settings on the right are
// drawn from the hub's tile catalogue (GET api/dashboards/tiles). Nothing is ever sent to a device.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import '@fontsource/sora/latin-500.css';
import '@fontsource/sora/latin-600.css';
import '../wall/wall.css';
import { api, enc, toast } from '../api.js';
import { navigate, useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { ErrorBox, Loading, Segmented, Switch } from '../components/Controls.jsx';
import { Icon } from '../icons.jsx';
import { StatusChips, Tile } from '../wall/tiles.jsx';
import { NamedHub, cleanNames } from '../wall/names.jsx';
import { InertCommands } from '../wall/PinPad.jsx';
import { fieldDefaults, TileField } from '../components/TileFields.jsx';
import { MORNING_DEFAULTS, homeOf } from '../wall/home.js';
import { WallTime, wallDay } from '../wall/clock.jsx';
import WallTabs from '../wall/tabs.jsx';
import { TAB_NAMES } from '../wall/tabfit.js';
import { SEASON, THEMES, chosenTheme, christmasOf } from '../wall/theme.js';
import { pictureSrc } from '../wall/Theme.jsx';
import { MONTHS, MONTH_DAYS, mmdd, parseMMDD } from '../automation.js';

// The hub's wall grid (dashboards.GRID): saved with every dashboard as "grid", so the hub knows the
// tiles aren't on the old 12 × 8 one.
const COLS = 24;
const ROWS = 16;
const SIZES = { fire: [1280, 800], ipad: [1024, 768] };
const PAGE_ICONS = ['home', 'shield', 'bulb', 'thermometer', 'camera', 'star', 'bed', 'sofa'];

const rid = (p) => p + Math.random().toString(16).slice(2, 10);
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const bodyOf = (d) => ({ name: d.name, theme: chosenTheme(d), christmas: christmasOf(d), pages: d.pages, names: cleanNames(d.names), home: homeOf(d), tabNames: TAB_NAMES.includes(d.tabNames) ? d.tabNames : TAB_NAMES[0], grid: [COLS, ROWS] });
const THEME_NAMES = { midnight: 'Midnight', glass: 'Glass', christmas: 'Christmas' };

/** [month] [day]: one end of the Christmas season ("MM-DD"). */
function MonthDay({ value, onChange, label }) {
  const [m, dy] = parseMMDD(value) || [12, 1];
  const set = (mo, day) => onChange(mmdd(mo, Math.min(day, MONTH_DAYS[mo - 1])));
  return (
    <span className="de-home-row">
      <select className="select select--sm" aria-label={`${label} month`} value={m} onChange={(e) => set(Number(e.target.value), dy)}>
        {MONTHS.map((name, i) => (
          <option key={name} value={i + 1}>
            {name}
          </option>
        ))}
      </select>
      <select className="select select--sm" aria-label={`${label} day`} value={dy} onChange={(e) => set(m, Number(e.target.value))}>
        {Array.from({ length: MONTH_DAYS[m - 1] }, (_, i) => (
          <option key={i} value={i + 1}>
            {i + 1}
          </option>
        ))}
      </select>
    </span>
  );
}

/**
 * How the wall looks (dashboard.theme) and Christmas: its season (every year it replaces the theme by
 * itself), snow, the greeting, the characters (Newman's drawings or up to three pictures) and where they go.
 */
function LookSection({ dash, setDash, onPictures }) {
  const theme = chosenTheme(dash);
  const x = christmasOf(dash);
  const setX = (patch) => setDash((d) => ({ ...d, christmas: { ...christmasOf(d), ...patch } }));
  const season = x.season;
  const [busy, setBusy] = useState(false);
  const file = useRef(null);
  const upload = async (f) => {
    if (!f) return;
    setBusy(true);
    try {
      const res = await fetch(`api/dashboards/${enc(dash.id)}/pictures`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': f.type || 'application/octet-stream' }, body: f });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Upload failed (${res.status})`);
      onPictures(data.pictures);
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      setBusy(false);
      if (file.current) file.current.value = '';
    }
  };
  const remove = async (pid) => {
    try {
      onPictures((await api.del(`api/dashboards/${enc(dash.id)}/pictures/${pid}`)).pictures);
    } catch {
      /* toasted */
    }
  };
  const showXmas = theme === 'christmas' || !!season;
  return (
    <div className="de-look">
      <label className="field">
        <span className="field-label">Theme</span>
        <select className="select" value={theme} onChange={(e) => setDash((d) => ({ ...d, theme: e.target.value }))}>
          {THEMES.map((t) => (
            <option key={t} value={t}>
              {THEME_NAMES[t]}
            </option>
          ))}
        </select>
      </label>
      <Switch checked={!!season} onChange={(on) => setX({ season: on ? { ...SEASON } : null })} label="Christmas season" />
      {season ? (
        <div className="field de-home-times">
          <span className="help">Christmas every year from</span>
          <MonthDay value={season.from} label="From" onChange={(from) => setX({ season: { ...season, from } })} />
          <span className="help">to</span>
          <MonthDay value={season.to} label="To" onChange={(to) => setX({ season: { ...season, to } })} />
        </div>
      ) : null}
      {showXmas ? (
        <>
          <Switch checked={x.snow} onChange={(snow) => setX({ snow })} label="Falling snow" />
          <label className="field">
            <span className="field-label">Greeting</span>
            <input className="input" maxLength={40} value={x.greeting} placeholder="No greeting" onChange={(e) => setX({ greeting: e.target.value })} />
          </label>
          <label className="field">
            <span className="field-label">Characters</span>
            <select className="select" value={x.characters} onChange={(e) => setX({ characters: e.target.value })}>
              <option value="builtin">Built-in (Santa, reindeer, elf)</option>
              <option value="pictures">Your pictures</option>
              <option value="off">Off</option>
            </select>
          </label>
          {x.characters === 'pictures' ? (
            <div className="field">
              <span className="field-label">Your pictures (PNG or WebP with a see-through background, up to 3)</span>
              <div className="de-pics">
                {x.pictures.map((p) => (
                  <span key={p} className="de-pic">
                    <img src={pictureSrc(dash.id, p)} alt="" />
                    <button type="button" className="icon-btn" aria-label="Remove this picture" onClick={() => remove(p)}>
                      <Icon name="x" size={14} />
                    </button>
                  </span>
                ))}
                {x.pictures.length < 3 ? (
                  <label className={'btn btn-sm' + (busy ? ' is-busy' : '')}>
                    <Icon name="plus" size={14} /> {busy ? 'Adding…' : 'Add a picture'}
                    <input ref={file} type="file" accept="image/png,image/webp" hidden disabled={busy} onChange={(e) => upload(e.target.files && e.target.files[0])} />
                  </label>
                ) : null}
              </div>
            </div>
          ) : null}
          {x.characters !== 'off' ? (
            <label className="field">
              <span className="field-label">Characters go</span>
              <select className="select" value={x.place} onChange={(e) => setX({ place: e.target.value })}>
                <option value="clock">By the clock</option>
                <option value="background">Soft, behind the tiles</option>
                <option value="off">Nowhere</option>
              </select>
            </label>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

const hm = (t) => String(t || '').replace(/^0(\d)/, '$1').replace(/:00$/, '');

/** Whose a dashboard is, in words: "Marlina", or "Wall tablet account (Hallway)". */
export function ownerLabel(u) {
  if (!u) return 'Another account';
  return u.role === 'tablet' ? `Wall tablet account (${u.name})` : u.name;
}

/**
 * Where the wall tablet rests (dashboard.home): the usual home page, another one in the mornings,
 * and going back after a while untouched. A one-line summary; "Change" opens the settings.
 */
function HomeSection({ dash, onHome, onAddToday, hasToday }) {
  const [open, setOpen] = useState(false);
  const home = homeOf(dash);
  const m = home.morning;
  const name = (id) => (dash.pages.find((p) => p.id === id) || {}).name || '?';
  const set = (patch) => onHome({ ...home, ...patch });
  const setM = (patch) => set({ morning: { ...m, ...patch } });
  const win = (part, key, v) => setM({ [part]: { ...m[part], [key]: v } });
  const summary = [
    name(home.page),
    m ? `${name(m.page)} weekday mornings ${hm(m.weekdays.from)}–${hm(m.weekdays.to)}, weekends ${hm(m.weekends.from)}–${hm(m.weekends.to)}` : '',
    home.idleMinutes ? `back after ${home.idleMinutes} min untouched` : 'stays where it was left',
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <div className="de-home">
      <div className="de-home-head">
        <span className="de-side-title">Home page</span>
        <button type="button" className="btn btn-sm" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Done' : 'Change'}
        </button>
      </div>
      {!open ? <p className="help">{summary}</p> : null}
      {open ? (
        <div className="de-home-form">
          <label className="field">
            <span className="field-label">Usual home page</span>
            <select className="select" value={home.page} onChange={(e) => set({ page: e.target.value })}>
              {dash.pages.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <Switch checked={!!m} onChange={(on) => set({ morning: on ? { ...MORNING_DEFAULTS, page: (dash.pages.find((p) => p.id !== home.page) || dash.pages[0]).id } : null })} label="Mornings: another page" />
          {m ? (
            <>
              <label className="field">
                <span className="field-label">Morning page</span>
                <select className="select" value={m.page} onChange={(e) => setM({ page: e.target.value })}>
                  {dash.pages.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              {[
                ['weekdays', 'Mon–Fri'],
                ['weekends', 'Sat–Sun'],
              ].map(([part, label]) => (
                <div key={part} className="field de-home-times">
                  <span className="field-label">{label}</span>
                  <span className="de-home-row">
                    <input className="input input--sm" type="time" aria-label={`${label} from`} value={m[part].from} onChange={(e) => win(part, 'from', e.target.value)} />
                    <span className="help">to</span>
                    <input className="input input--sm" type="time" aria-label={`${label} to`} value={m[part].to} onChange={(e) => win(part, 'to', e.target.value)} />
                  </span>
                </div>
              ))}
            </>
          ) : null}
          <label className="field">
            <span className="field-label">Go back to the home page after</span>
            <span className="de-home-row de-home-idle">
              <input className="input input--sm" type="number" min={0} max={240} aria-label="Minutes untouched" value={home.idleMinutes} onChange={(e) => set({ idleMinutes: Math.max(0, Math.min(240, Math.round(Number(e.target.value) || 0))) })} />
              <span>minutes untouched (0 = never)</span>
            </span>
          </label>
          <p className="help">A morning starting or ending moves the tablet once nobody has touched it for 30 seconds.</p>
        </div>
      ) : null}
      {!hasToday ? (
        <button type="button" className="btn" onClick={onAddToday}>
          <Icon name="sun" size={15} /> Add a Today page
        </button>
      ) : null}
    </div>
  );
}

/** First free spot for a w × h tile, scanning rows then columns. */
function freeSpot(tiles, w, h) {
  for (let y = 0; y + h <= ROWS; y++) {
    for (let x = 0; x + w <= COLS; x++) {
      const box = { x, y, w, h };
      if (!tiles.some((t) => overlaps(t, box))) return box;
    }
  }
  return null;
}

/** The wall's top bar, drawn still (the canvas is a preview), with the wall's own clock and date. */
function CanvasTopBar({ pages, current, tabNames }) {
  const now = new Date();
  const { day, date } = wallDay(now);
  return (
    <header className="wt-top" aria-hidden="true">
      <div className="wt-clock">
        <WallTime date={now} />
        <div className="wt-date">
          <span>{day}</span>
          <span className="wt-dim-text">{date}</span>
        </div>
      </div>
      <WallTabs pages={pages} current={current} tabNames={tabNames} />

      <div className="wt-top-right">
        <span className="wt-round">
          <Icon name="moon" size={22} />
        </span>
      </div>
    </header>
  );
}

/**
 * The page tabs above a canvas: pick, double-click to rename, drag to reorder, ⋯ for the rest.
 * The phone layout editor adds its own menu items (`menuExtra`), a class per tab (`classFor`) and
 * how a new page starts (`newPage`).
 */
export function PageTabs({ pages, current, onPick, onChange, confirm, noun = 'dashboard', menuExtra, classFor, newPage }) {
  const [editing, setEditing] = useState(null);
  const [menu, setMenu] = useState(null);
  const [dragFrom, setDragFrom] = useState(null);
  const setPage = (id, patch) => onChange(pages.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  const move = (from, to) => {
    if (to < 0 || to >= pages.length || from === to) return;
    const next = pages.slice();
    const [p] = next.splice(from, 1);
    next.splice(to, 0, p);
    onChange(next);
  };
  const remove = async (p) => {
    if (pages.length <= 1) return toast(`A ${noun} needs at least one page.`, 'error');
    if (p.tiles.length && !(await confirm({ title: `Delete the “${p.name}” page?`, body: `Its ${p.tiles.length} tile${p.tiles.length === 1 ? '' : 's'} go with it (when you save).`, confirmLabel: 'Delete page', danger: true }))) return undefined;
    const next = pages.filter((x) => x.id !== p.id);
    onChange(next);
    if (current === p.id) onPick(next[0].id);
    return undefined;
  };
  useEffect(() => {
    if (!menu) return undefined;
    const close = (e) => !e.target.closest('.de-tabmenu') && setMenu(null);
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menu]);
  return (
    <div className="de-tabs" role="tablist" aria-label="Pages">
      {pages.map((p, i) => (
        <div
          key={p.id}
          className={'de-tab' + (p.id === current ? ' is-on' : '') + (dragFrom === i ? ' is-dragging' : '') + (classFor ? ' ' + classFor(p) : '')}
          draggable={editing !== p.id}
          onDragStart={() => setDragFrom(i)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={() => {
            if (dragFrom !== null) move(dragFrom, i);
            setDragFrom(null);
          }}
          onDragEnd={() => setDragFrom(null)}
        >
          {editing === p.id ? (
            <input
              className="input input--sm de-tab-input"
              defaultValue={p.name}
              autoFocus
              aria-label="Page name"
              maxLength={24}
              onBlur={(e) => {
                setPage(p.id, { name: e.target.value.trim() || p.name });
                setEditing(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur();
                if (e.key === 'Escape') setEditing(null);
              }}
            />
          ) : (
            <button type="button" role="tab" aria-selected={p.id === current} className="de-tab-btn" onClick={() => onPick(p.id)} onDoubleClick={() => setEditing(p.id)}>
              <Icon name={p.icon || 'grid'} size={16} /> {p.name}
            </button>
          )}
          <button type="button" className="de-tab-more" aria-label={`More for ${p.name}`} onClick={() => setMenu(menu === p.id ? null : p.id)}>
            ⋯
          </button>
          {menu === p.id ? (
            <div className="de-tabmenu more-menu" role="menu">
              <button type="button" role="menuitem" className="more-item" onClick={() => (setMenu(null), setEditing(p.id))}>
                Rename
              </button>
              <div className="de-icons" role="group" aria-label="Icon">
                {PAGE_ICONS.map((ic) => (
                  <button key={ic} type="button" className={'de-icon' + ((p.icon || 'home') === ic ? ' is-on' : '')} aria-label={`Icon: ${ic}`} aria-pressed={(p.icon || 'home') === ic} onClick={() => setPage(p.id, { icon: ic })}>
                    <Icon name={ic} size={18} />
                  </button>
                ))}
              </div>
              {menuExtra ? menuExtra(p, () => setMenu(null)) : null}
              <button type="button" role="menuitem" className="more-item" disabled={i === 0} onClick={() => (setMenu(null), move(i, i - 1))}>
                Move left
              </button>
              <button type="button" role="menuitem" className="more-item" disabled={i === pages.length - 1} onClick={() => (setMenu(null), move(i, i + 1))}>
                Move right
              </button>
              <button type="button" role="menuitem" className="more-item more-item--danger" onClick={() => (setMenu(null), remove(p))}>
                Delete page
              </button>
            </div>
          ) : null}
        </div>
      ))}
      <button
        type="button"
        className="de-addpage"
        onClick={() => {
          const p = { id: rid('p_'), name: 'New page', icon: 'star', tiles: [], ...(newPage ? newPage() : {}) };
          onChange([...pages, p]);
          onPick(p.id);
        }}
      >
        + Page
      </button>
    </div>
  );
}

/** Drag/resize/keyboard layer over the canvas grid. */
function EditLayer({ tiles, types, selected, onSelect, onMove, onRemove, scale, onBlocked }) {
  const layer = useRef(null);
  const [drag, setDrag] = useState(null); // { id, mode, px, py, orig, box, bad }
  const pitch = () => {
    const r = layer.current.getBoundingClientRect();
    const gap = parseFloat(getComputedStyle(layer.current).columnGap || '14') * scale;
    return { cx: (r.width + gap) / COLS, cy: (r.height + gap) / ROWS };
  };
  const minOf = (t) => (types[t.type] && types[t.type].min) || [1, 1];
  const candidate = (t, mode, dx, dy) => {
    const [mw, mh] = minOf(t);
    if (mode === 'move') return { x: Math.min(COLS - t.w, Math.max(0, t.x + dx)), y: Math.min(ROWS - t.h, Math.max(0, t.y + dy)), w: t.w, h: t.h };
    return { x: t.x, y: t.y, w: Math.min(COLS - t.x, Math.max(mw, t.w + dx)), h: Math.min(ROWS - t.y, Math.max(mh, t.h + dy)) };
  };
  const bad = (id, box) => tiles.some((o) => o.id !== id && overlaps(o, box));
  const down = (e, t, mode) => {
    e.stopPropagation();
    e.preventDefault();
    onSelect(t.id);
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ id: t.id, mode, px: e.clientX, py: e.clientY, orig: t, box: { x: t.x, y: t.y, w: t.w, h: t.h }, bad: false, p: pitch() });
  };
  const move = (e) => {
    if (!drag) return;
    const dx = Math.round((e.clientX - drag.px) / drag.p.cx);
    const dy = Math.round((e.clientY - drag.py) / drag.p.cy);
    const box = candidate(drag.orig, drag.mode, dx, dy);
    setDrag((d) => ({ ...d, box, bad: bad(d.id, box) }));
  };
  const up = () => {
    if (!drag) return;
    if (drag.bad) onBlocked('Tiles can’t overlap — it went back where it was.');
    else onMove(drag.id, drag.box);
    setDrag(null);
  };
  const key = (e, t) => {
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      onRemove(t.id);
      return;
    }
    if (e.key === 'Escape') return onSelect(null);
    const a = arrows[e.key];
    if (!a) return undefined;
    e.preventDefault();
    const box = candidate(t, e.shiftKey ? 'resize' : 'move', a[0], a[1]);
    if (box.x === t.x && box.y === t.y && box.w === t.w && box.h === t.h) return onBlocked('That’s the edge of the grid (or the smallest this tile can be).');
    if (bad(t.id, box)) return onBlocked('Another tile is in the way.');
    onMove(t.id, box);
    return undefined;
  };
  return (
    <div className="de-layer" ref={layer} onPointerDown={() => onSelect(null)}>
      {Array.from({ length: COLS * ROWS }, (_, i) => (
        <span key={i} className="de-cell" style={{ gridColumn: (i % COLS) + 1, gridRow: Math.floor(i / COLS) + 1 }} />
      ))}
      {tiles.map((t) => {
        const box = drag && drag.id === t.id ? drag.box : t;
        const spec = types[t.type];
        return (
          <div
            key={t.id}
            className={'de-box' + (selected === t.id ? ' is-selected' : '') + (drag && drag.id === t.id && drag.bad ? ' is-bad' : '') + (drag && drag.id === t.id ? ' is-dragging' : '')}
            style={{ gridColumn: `${box.x + 1} / span ${box.w}`, gridRow: `${box.y + 1} / span ${box.h}` }}
            tabIndex={0}
            role="button"
            aria-label={`${spec ? spec.name : t.type} tile, ${box.w} by ${box.h} at column ${box.x + 1}, row ${box.y + 1}. Arrows move it, shift and arrows resize it, Delete removes it.`}
            onPointerDown={(e) => down(e, t, 'move')}
            onPointerMove={move}
            onPointerUp={up}
            onPointerCancel={() => setDrag(null)}
            onFocus={() => onSelect(t.id)}
            onKeyDown={(e) => key(e, t)}
          >
            <span className="de-box-label">{(t.config && t.config.title) || (spec ? spec.name : t.type)}</span>
            <span className="de-handle" aria-hidden="true" onPointerDown={(e) => down(e, t, 'resize')} onPointerMove={move} onPointerUp={up} />
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardEditor({ id }) {
  const confirm = useConfirm();
  const catalog = useApi('api/dashboards/tiles');
  const [dash, setDash] = useState(null);
  // Another account's dashboard (a wall tablet's, arranged by an admin): say whose it is.
  const { user: me, isAdmin } = useAuth();
  const owners = useApi(isAdmin ? 'api/users' : null);
  const [saved, setSaved] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [pageId, setPageId] = useState(null);
  const [selected, setSelected] = useState(null);
  const [aspect, setAspect] = useState('fire');
  const [errors, setErrors] = useState([]);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState('');
  const stage = useRef(null);
  const [room, setRoom] = useState({ w: 800, h: 500 });

  const load = useCallback(async () => {
    try {
      const d = await api.get('api/dashboards/' + enc(id), { quiet: true });
      setDash(d);
      setSaved(JSON.stringify(bodyOf(d)));
      setPageId((cur) => cur || (d.pages[0] && d.pages[0].id));
      setLoadError(null);
    } catch (e) {
      setLoadError(e);
    }
  }, [id]);
  useEffect(() => {
    load();
  }, [load]);

  useLayoutEffect(() => {
    const el = stage.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => setRoom({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setRoom({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, [dash]);

  const dirty = !!dash && JSON.stringify(bodyOf(dash)) !== saved;
  useEffect(() => {
    if (!dirty) return undefined;
    const on = (e) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', on);
    return () => window.removeEventListener('beforeunload', on);
  }, [dirty]);
  useEffect(() => {
    if (!note) return undefined;
    const t = setTimeout(() => setNote(''), 3000);
    return () => clearTimeout(t);
  }, [note]);

  const types = useMemo(() => Object.fromEntries(((catalog.data && catalog.data.types) || []).map((t) => [t.type, t])), [catalog.data]);

  if (loadError) {
    return (
      <div className="app-page">
        <div className="app-head">
          <a className="crumb" href="#/dashboards">
            Dashboards /
          </a>
        </div>
        <ErrorBox error={loadError} onRetry={load}>
          {loadError.status === 404 ? "This dashboard doesn't exist any more." : `Couldn't load it: ${loadError.message}`}
        </ErrorBox>
      </div>
    );
  }
  if (!dash || !catalog.data) return <Loading>Loading the dashboard…</Loading>;

  const page = dash.pages.find((p) => p.id === pageId) || dash.pages[0];
  const setPages = (pages) => setDash((d) => ({ ...d, pages }));
  // "Show as": a name for a device on this whole dashboard (every page and tile).
  const setName = (id, text) => setDash((d) => ({ ...d, names: { ...(d.names || {}), [id]: text } }));
  // The hub keeps the pictures as they're added or removed: the saved copy has them too (not "unsaved").
  const setPictures = (pictures) => {
    const withPics = (d) => ({ ...d, christmas: { ...christmasOf(d), pictures } });
    setDash(withPics);
    setSaved((s) => (s ? JSON.stringify(withPics(JSON.parse(s))) : s));
  };
  const setTiles = (fn) => setDash((d) => ({ ...d, pages: d.pages.map((p) => (p.id === page.id ? { ...p, tiles: fn(p.tiles) } : p)) }));
  const tile = page.tiles.find((t) => t.id === selected) || null;
  const spec = tile && types[tile.type];
  const [W, H] = SIZES[aspect];
  const scale = Math.max(0.2, Math.min(room.w / W, room.h / H));

  const addTile = (t) => {
    const [dw, dh] = t.default || t.min || [3, 2];
    const spot = freeSpot(page.tiles, dw, dh) || freeSpot(page.tiles, (t.min || [dw])[0], (t.min || [0, dh])[1]);
    if (!spot) {
      setNote('No room on this page. Make space or add a page.');
      return;
    }
    const nt = { id: rid('t_'), type: t.type, ...spot, config: fieldDefaults(t) };
    setTiles((ts) => [...ts, nt]);
    setSelected(nt.id);
  };
  // A ready-made Today page (the hub lays it out), first in the tabs, and Mornings pointing at it.
  const addToday = async () => {
    try {
      const made = await api.get('api/dashboards/today-page', { quiet: true });
      const all = dash.pages.flatMap((p) => p.tiles);
      const wx = all.find((t) => t.type === 'weather' && t.config && t.config.entity);
      const cal = all.find((t) => t.type === 'calendar' && t.config);
      const tiles = made.tiles.map((t) => {
        if (t.type === 'weather' && wx) return { ...t, config: { ...t.config, entity: wx.config.entity } };
        if (t.type === 'calendar' && cal) return { ...t, config: { ...t.config, entities: cal.config.entities || [] } };
        return t;
      });
      const id = dash.pages.some((p) => p.id === made.id) ? rid('p_') : made.id;
      setDash((d) => {
        const h = homeOf(d); // before the new page goes first
        return { ...d, pages: [{ ...made, id, tiles }, ...d.pages], home: { ...h, morning: { ...MORNING_DEFAULTS, ...(h.morning || {}), page: id } } };
      });
      setPageId(id);
      setSelected(null);
    } catch (err) {
      toast((err && err.message) || "Couldn't make the Today page", 'error');
    }
  };
  const removeTile = (tid) => {
    setTiles((ts) => ts.filter((t) => t.id !== tid));
    setSelected(null);
  };
  const save = async () => {
    setSaving(true);
    setErrors([]);
    try {
      const res = await api.put('api/dashboards/' + enc(dash.id), bodyOf(dash), { quiet: true });
      const next = res && res.pages ? { ...dash, ...res } : dash;
      setDash(next);
      setSaved(JSON.stringify(bodyOf(next)));
      toast('Saved, tablets updated', 'success');
    } catch (err) {
      const d = err.data && typeof err.data === 'object' ? err.data : {};
      setErrors(Array.isArray(d.errors) && d.errors.length ? d.errors : [err.message]);
    } finally {
      setSaving(false);
    }
  };
  const back = async () => {
    if (dirty && !(await confirm({ title: 'Discard changes?', body: "You haven't saved this dashboard.", confirmLabel: 'Discard', danger: true }))) return;
    navigate('dashboards');
  };

  return (
    <div className="app-page de-page">
      <div className="app-head app-head--nowrap">
        <button type="button" className="crumb" onClick={back}>
          Dashboards /
        </button>
        <input className="name-input" aria-label="Dashboard name" value={dash.name} maxLength={40} onChange={(e) => setDash((d) => ({ ...d, name: e.target.value }))} />
        {dash.userId && me && dash.userId !== me.id ? (
          <span className="muted de-owner" title="Whose dashboard this is">
            {ownerLabel((owners.data || []).find((u) => u.id === dash.userId))}
          </span>
        ) : null}
        {dirty ? <span className="dirty-pill">Unsaved</span> : null}
        <a className="btn head-btn" href="#/wall" target="_blank" rel="noopener">
          <Icon name="open" size={15} /> Open on this computer
        </a>
        <button
          type="button"
          className="btn head-btn"
          disabled={!dirty || saving}
          onClick={() => {
            setDash(JSON.parse(JSON.stringify({ ...dash, ...JSON.parse(saved) })));
            setErrors([]);
            setSelected(null);
          }}
        >
          Discard
        </button>
        <button type="button" className="btn btn-primary head-btn" disabled={!dirty || saving} onClick={save}>
          {saving ? 'Saving…' : dirty ? 'Save' : 'Saved'}
        </button>
      </div>
      {errors.length ? (
        <div className="error-box de-errors" role="alert">
          <Icon name="alert" size={16} /> {errors.join(' · ')}
        </div>
      ) : null}

      <div className="de-main">
        <section className="pane de-canvas-pane" aria-label="Layout">
          <div className="de-canvas-bar">
            <PageTabs pages={dash.pages} current={page.id} onPick={(pid) => (setPageId(pid), setSelected(null))} onChange={setPages} confirm={confirm} />
            <Segmented
              label="Tablet shape"
              value={aspect}
              onChange={setAspect}
              options={[
                { id: 'fire', label: 'Tablet 16:10' },
                { id: 'ipad', label: 'iPad 4:3' },
              ]}
            />
          </div>
          <div className="de-stage" ref={stage} data-wt-theme={chosenTheme(dash) === 'midnight' ? undefined : chosenTheme(dash)}>
            <div className="de-scaler" style={{ width: W * scale, height: H * scale }}>
              <div className={'wall wall--canvas' + (W <= 1200 ? ' wall--narrow' : '')} style={{ width: W, height: H, transform: `scale(${scale})` }}>
                <CanvasTopBar pages={dash.pages} current={page.id} tabNames={dash.tabNames} />
                {page.id === homeOf(dash).page ? (
                  <NamedHub names={dash.names}>
                    <StatusChips />
                  </NamedHub>
                ) : null}
                <div className="wt-grid de-grid">
                  <NamedHub names={dash.names}>
                  <InertCommands>
                    {page.tiles.map((t) => (
                      <Tile key={t.id} tile={t} />
                    ))}
                  </InertCommands>
                  </NamedHub>
                  <EditLayer
                    tiles={page.tiles}
                    types={types}
                    selected={selected}
                    scale={scale}
                    onSelect={setSelected}
                    onMove={(tid, box) => setTiles((ts) => ts.map((t) => (t.id === tid ? { ...t, ...box } : t)))}
                    onRemove={removeTile}
                    onBlocked={setNote}
                  />
                </div>
              </div>
            </div>
            {note ? (
              <div className="de-note" role="status">
                {note}
              </div>
            ) : null}
          </div>
        </section>

        <aside className="pane de-side" aria-label={tile ? 'Tile settings' : 'Add a tile'}>
          <div className="pane-body stack">
            {tile ? (
              <>
                <div>
                  <div className="de-side-title">{spec ? spec.name : tile.type}</div>
                  {spec ? <p className="help">{spec.description}</p> : null}
                  <div className="help">
                    {tile.w} × {tile.h} of {COLS} × {ROWS} · column {tile.x + 1}, row {tile.y + 1}
                  </div>
                </div>
                {((spec && spec.fields) || []).map((f) => (
                  <TileField key={f.key} field={f} config={tile.config || {}} tileType={tile.type} names={dash.names} onName={setName} pages={dash.pages} screens={catalog.data.screens} onChange={(config) => setTiles((ts) => ts.map((t) => (t.id === tile.id ? { ...t, config } : t)))} />
                ))}
                {spec && !(spec.fields || []).length ? <div className="help">Nothing to set up for this tile.</div> : null}
                <div className="inline-form">
                  <button type="button" className="btn btn-danger" onClick={() => removeTile(tile.id)}>
                    <Icon name="trash" size={15} /> Remove tile
                  </button>
                  <button type="button" className="btn" onClick={() => setSelected(null)}>
                    Done
                  </button>
                </div>
              </>
            ) : (
              <>
                <HomeSection dash={dash} onHome={(home) => setDash((d) => ({ ...d, home }))} onAddToday={addToday} hasToday={dash.pages.some((p) => p.tiles.some((t) => t.type === 'todo' || t.type === 'greeting'))} />
                <label className="field">
                  <span className="field-label">Tab names</span>
                  <select className="select" value={bodyOf(dash).tabNames} onChange={(e) => setDash((d) => ({ ...d, tabNames: e.target.value }))}>
                    <option value="current">Icons, current tab named</option>
                    <option value="fit">Names when they fit</option>
                  </select>
                </label>
                <LookSection dash={dash} setDash={setDash} onPictures={setPictures} />
                <div className="de-side-title">+ Add tile</div>
                <p className="help">Pick a tile; it goes in the first free spot on “{page.name}”. Drag it where you want it, and its corner to resize.</p>
                <div className="de-catalog">
                  {((catalog.data && catalog.data.types) || []).map((t) => (
                    <button key={t.type} type="button" className="de-type" disabled={t.available === false} onClick={() => addTile(t)}>
                      <span className="de-type-name">
                        {t.name}
                        {t.available === false ? <span className="tf-first">coming soon</span> : null}
                      </span>
                      <span className="de-type-desc">{t.description}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
