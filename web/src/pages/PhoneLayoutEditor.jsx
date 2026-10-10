// The phone layout editor: a phone-shaped canvas (390 × 844) drawing what the phone app shows - the
// status bar, the page's 4-column grid of square cells with the real tiles, the Today card and the
// links on the first tab, and the bottom tabs (up to three pages, then People and Alerts). Drag a
// tile to move it, drag its corner to resize; keys for both. Tiles are drawn the phone's way, fitted
// to their cells (<Tile phone>), so they look as they will on the phone. Settings on the right come from the
// hub's tile catalogue, like the wall editor. Who it's for: the household default (#/dashboards/
// phone/default), your own phone (…/me) or, for an admin, anyone's (…/<account id>).

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import '@fontsource/sora/latin-500.css';
import '@fontsource/sora/latin-600.css';
import '../wall/wall.css';
import { api, enc, toast } from '../api.js';
import { navigate, useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { ErrorBox, Loading, Switch } from '../components/Controls.jsx';
import { Icon } from '../icons.jsx';
import { Tile } from '../wall/tiles.jsx';
import TodayTile from '../wall/TodayTile.jsx';
import { InertCommands } from '../wall/PinPad.jsx';
import { fieldDefaults, TileField } from '../components/TileFields.jsx';
import { plural } from '../format.js';
import { PageTabs } from './DashboardEditor.jsx';

const COLS = 4;
const MAX_H = 8;
const MAX_TABS = 3;
const MAX_PAGES = 8;
const PHONE = { w: 390, h: 844, pad: 14, gap: 10, status: 44, head: 62, tabbar: 82 };
const CELL = (PHONE.w - 2 * PHONE.pad - (COLS - 1) * PHONE.gap) / COLS; // a square cell, about 83 px
const TODAY_ROWS = 3; // the Today card's height in the preview, in cells
const SCROLL_H = PHONE.h - PHONE.status - PHONE.head - PHONE.tabbar;

const rid = (p) => p + Math.random().toString(16).slice(2, 10);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
const bodyOf = (l) => ({ today: !!l.today, pages: l.pages });
const bottomOf = (tiles) => tiles.reduce((m, t) => Math.max(m, t.y + t.h), 0);
const span = (n) => n * CELL + (n - 1) * PHONE.gap;

/** First free spot for a w × h tile, top to bottom (a phone page grows downwards). */
function freeSpot(tiles, w, h) {
  for (let y = 0; y < 400; y++) {
    for (let x = 0; x + w <= COLS; x++) {
      const box = { x, y, w, h };
      if (!tiles.some((t) => overlaps(t, box))) return box;
    }
  }
  return null;
}

function greeting(name) {
  const h = new Date().getHours();
  const g = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  return name ? `${g}, ${name}` : g;
}

/** Drag/resize/keyboard layer over the phone's grid: whole cells only, never overlapping. */
function PhoneEditLayer({ tiles, types, rows, selected, onSelect, onMove, onRemove, scale, onBlocked }) {
  const layer = useRef(null);
  const [drag, setDrag] = useState(null); // { id, mode, px, py, orig, box, bad, p }
  const pitch = () => {
    const r = layer.current.getBoundingClientRect();
    const c = (r.width + PHONE.gap * scale) / COLS;
    return { cx: c, cy: c }; // square cells
  };
  const candidate = (t, mode, dx, dy) =>
    mode === 'move'
      ? { x: clamp(t.x + dx, 0, COLS - t.w), y: clamp(t.y + dy, 0, rows - 1), w: t.w, h: t.h }
      : { x: t.x, y: t.y, w: clamp(t.w + dx, 1, COLS - t.x), h: clamp(t.h + dy, 1, MAX_H) };
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
    const box = candidate(drag.orig, drag.mode, Math.round((e.clientX - drag.px) / drag.p.cx), Math.round((e.clientY - drag.py) / drag.p.cy));
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
      return undefined;
    }
    if (e.key === 'Escape') return onSelect(null);
    const a = arrows[e.key];
    if (!a) return undefined;
    e.preventDefault();
    const box = candidate(t, e.shiftKey ? 'resize' : 'move', a[0], a[1]);
    if (box.x === t.x && box.y === t.y && box.w === t.w && box.h === t.h) return onBlocked('That’s the edge of the phone (or as small or tall as a tile goes).');
    if (bad(t.id, box)) return onBlocked('Another tile is in the way.');
    onMove(t.id, box);
    return undefined;
  };
  return (
    <div className="de-layer pl-layer" ref={layer} style={{ gridTemplateRows: `repeat(${rows}, ${CELL}px)`, gridAutoRows: `${CELL}px` }} onPointerDown={() => onSelect(null)}>
      {Array.from({ length: COLS * rows }, (_, i) => (
        <span key={i} className="de-cell" style={{ gridColumn: (i % COLS) + 1, gridRow: Math.floor(i / COLS) + 1 }} />
      ))}
      {tiles.map((t) => {
        const box = drag && drag.id === t.id ? drag.box : t;
        const spec = types[t.type];
        return (
          <div
            key={t.id}
            className={'de-box pl-box' + (selected === t.id ? ' is-selected' : '') + (drag && drag.id === t.id && drag.bad ? ' is-bad' : '') + (drag && drag.id === t.id ? ' is-dragging' : '')}
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

/** The phone itself: status bar, header, the page (Today card, grid, links), the tab bar. */
function PhoneCanvas({ layout, page, who, types, selected, onSelect, onMove, onRemove, onPick, onBlocked, scale }) {
  const tabs = layout.pages.filter((p) => p.tab).slice(0, MAX_TABS);
  const first = tabs[0] || layout.pages[0];
  const links = layout.pages.filter((p) => !p.tab);
  const isFirst = page.id === first.id;
  const isLink = !page.tab;
  const today = isFirst && layout.today;
  const fill = Math.floor((SCROLL_H - PHONE.pad - (today ? span(TODAY_ROWS) + PHONE.gap : 0) + PHONE.gap) / (CELL + PHONE.gap));
  const rows = Math.max(bottomOf(page.tiles) + 2, fill);
  const activeTab = isLink ? first.id : page.id;
  return (
    <div className="wall wall--canvas pl-phone" style={{ width: PHONE.w, height: PHONE.h, transform: `scale(${scale})` }}>
      <div className="pl-status" aria-hidden="true">
        <span>9:41</span>
        <span className="pl-status-r">
          <span className="pl-signal">
            <i />
            <i />
            <i />
            <i />
          </span>
          <span className="pl-batt" />
        </span>
      </div>
      <div className="pl-head">
        {isLink ? (
          <button type="button" className="pl-back" onClick={() => onPick(first.id)}>
            <Icon name="chevronLeft" size={18} /> {first.name}
          </button>
        ) : null}
        <div className="pl-title">{isFirst ? greeting(who) : page.name}</div>
      </div>
      <div className="pl-scroll">
        {today ? (
          <section className="wt-tile wt-tile--today pl-today" style={{ height: span(TODAY_ROWS) }} title="The Today card: switch it off on the right">
            <InertCommands>
              <TodayTile tile={{ id: 'today-card', type: 'today', x: 0, y: 0, w: COLS, h: TODAY_ROWS, config: { limit: 3 } }} />
            </InertCommands>
          </section>
        ) : null}
        <div className="pl-gridwrap" style={{ height: span(rows) }}>
          <div className="wt-grid de-grid pl-grid" style={{ gridTemplateRows: `repeat(${rows}, ${CELL}px)`, gridAutoRows: `${CELL}px` }}>
            <InertCommands>
              {page.tiles.map((t) => (
                <Tile key={t.id} tile={t} phone={{ w: span(Math.min(t.w, COLS)), h: span(t.h) }} />
              ))}
            </InertCommands>
            <PhoneEditLayer tiles={page.tiles} types={types} rows={rows} selected={selected} scale={scale} onSelect={onSelect} onMove={onMove} onRemove={onRemove} onBlocked={onBlocked} />
          </div>
        </div>
        {isFirst && links.length ? (
          <div className="pl-links">
            {links.map((p) => (
              <button key={p.id} type="button" className="pl-link" onClick={() => onPick(p.id)}>
                <Icon name={p.icon || 'grid'} size={18} />
                <span>{p.name}</span>
                <Icon name="chevronRight" size={16} />
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <nav className="pl-tabbar" aria-label="The phone's tabs">
        {tabs.map((p) => (
          <button key={p.id} type="button" className={'pl-tab' + (p.id === activeTab ? ' is-on' : '')} onClick={() => onPick(p.id)}>
            <Icon name={p.icon || 'grid'} size={22} />
            <span>{p.name}</span>
          </button>
        ))}
        <span className="pl-tab is-app" title="Drawn by the app">
          <Icon name="people" size={22} />
          <span>People</span>
        </span>
        <span className="pl-tab is-app" title="Drawn by the app">
          <Icon name="bell" size={22} />
          <span>Alerts</span>
        </span>
      </nav>
    </div>
  );
}

export default function PhoneLayoutEditor({ who = 'me' }) {
  const confirm = useConfirm();
  const { user: me } = useAuth();
  const catalog = useApi('api/dashboards/tiles');
  const url = 'api/phone-layouts/' + (who === 'default' ? 'default' : enc(who));
  const isDefault = who === 'default';
  const mine = who === 'me' || (me && who === me.id);
  const [info, setInfo] = useState(null); // { source, name, using }
  const [layout, setLayout] = useState(null);
  const [saved, setSaved] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [pageId, setPageId] = useState(null);
  const [selected, setSelected] = useState(null);
  const [errors, setErrors] = useState([]);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState('');
  const [tabNote, setTabNote] = useState('');
  const stage = useRef(null);
  const [room, setRoom] = useState({ w: 600, h: 700 });

  const take = (d) => {
    setInfo({ source: d.source || 'default', name: d.name, using: d.using });
    setLayout(d.layout);
    setSaved(JSON.stringify(bodyOf(d.layout)));
    setPageId((cur) => (cur && d.layout.pages.some((p) => p.id === cur) ? cur : ((d.layout.pages.find((p) => p.tab) || d.layout.pages[0]) || {}).id));
  };
  const load = useCallback(async () => {
    try {
      take(await api.get(url, { quiet: true }));
      setLoadError(null);
    } catch (e) {
      setLoadError(e);
    }
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps
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
  }, [layout === null]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = !!layout && JSON.stringify(bodyOf(layout)) !== saved;
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
  useEffect(() => setTabNote(''), [pageId]);

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
          {loadError.status === 404 ? 'There’s no phone layout for that account.' : `Couldn't load it: ${loadError.message}`}
        </ErrorBox>
      </div>
    );
  }
  if (!layout || !catalog.data || !info) return <Loading>Loading the phone layout…</Loading>;

  const page = layout.pages.find((p) => p.id === pageId) || layout.pages[0];
  const tabCount = layout.pages.filter((p) => p.tab).length;
  const firstTab = layout.pages.find((p) => p.tab) || layout.pages[0];
  const links = layout.pages.filter((p) => !p.tab);
  // Pages change together: always at least one tab (the first tab holds the Today card and links).
  const setPages = (pages) => {
    const next = pages.length && !pages.some((p) => p.tab) ? pages.map((p, i) => (i === 0 ? { ...p, tab: true } : p)) : pages;
    setLayout((l) => ({ ...l, pages: next }));
  };
  const setTiles = (fn) => setLayout((l) => ({ ...l, pages: l.pages.map((p) => (p.id === page.id ? { ...p, tiles: fn(p.tiles) } : p)) }));
  const tile = page.tiles.find((t) => t.id === selected) || null;
  const spec = tile && types[tile.type];
  const scale = Math.max(0.3, Math.min(1, (room.h - 24) / PHONE.h, (room.w - 24) / PHONE.w)); // room for the bezel

  const setTab = (p, on) => {
    if (on && !p.tab && tabCount >= MAX_TABS) {
      setTabNote(`A phone has room for ${MAX_TABS} page tabs (People and Alerts are the last two). Turn one off first.`);
      return false;
    }
    if (!on && p.tab && tabCount <= 1) {
      setTabNote('The first tab needs a page, so one page stays a tab.');
      return false;
    }
    setTabNote('');
    setPages(layout.pages.map((x) => (x.id === p.id ? { ...x, tab: on } : x)));
    return true;
  };
  const addTile = (t) => {
    const [dw, dh] = t.phone || [COLS, 2];
    const spot = freeSpot(page.tiles, Math.min(COLS, dw), Math.min(MAX_H, dh));
    if (!spot) {
      setNote('No room on this page.');
      return;
    }
    const nt = { id: rid('t_'), type: t.type, ...spot, config: fieldDefaults(t) };
    setTiles((ts) => [...ts, nt]);
    setSelected(nt.id);
  };
  const removeTile = (tid) => {
    setTiles((ts) => ts.filter((t) => t.id !== tid));
    setSelected(null);
  };
  const save = async () => {
    setSaving(true);
    setErrors([]);
    try {
      const res = await api.put(url, bodyOf(layout), { quiet: true });
      take({ ...res, name: res.name || info.name, using: res.using === undefined ? info.using : res.using });
      toast(isDefault ? `Saved · everyone using the default (${plural(res.using || 0, 'person', 'people')}) has it now` : 'Saved · the phone has it now', 'success');
    } catch (err) {
      const d = err.data && typeof err.data === 'object' ? err.data : {};
      setErrors(Array.isArray(d.errors) && d.errors.length ? d.errors : [err.message]);
    } finally {
      setSaving(false);
    }
  };
  const reset = async () => {
    try {
      take(await api.post(url + '/reset', {}));
      setSelected(null);
      toast('Back to the household default', 'success');
    } catch {
      /* toast shown */
    }
  };
  const back = async () => {
    if (dirty && !(await confirm({ title: 'Discard changes?', body: 'This phone layout has changes that aren’t saved.', confirmLabel: 'Discard', danger: true }))) return;
    navigate('dashboards');
  };

  const whoName = isDefault ? 'Household default' : mine ? 'Your phone' : `${info.name || 'Someone'}’s phone`;
  const whose = isDefault ? (
    <p className="help">
      Everyone using it gets changes right away ({plural(info.using || 0, 'person', 'people')}).
    </p>
  ) : info.source === 'own' ? (
    <p className="help">
      {mine ? 'Arranged just for you.' : `Arranged just for ${info.name}.`}{' '}
      <button type="button" className="btn-link" onClick={reset}>
        Reset to the household default
      </button>
    </p>
  ) : (
    <p className="help">{mine ? 'Using the household default. Change anything and save: this phone gets its own copy.' : `${info.name} uses the household default. Change anything and save: ${info.name}’s phone gets its own copy.`}</p>
  );

  return (
    <div className="app-page de-page">
      <div className="app-head app-head--nowrap">
        <button type="button" className="crumb" onClick={back}>
          Dashboards /
        </button>
        <span className="pl-head-title">Phone layout: {whoName}</span>
        {dirty ? <span className="muted pl-unsaved">Unsaved changes</span> : null}
        <button
          type="button"
          className="btn head-btn"
          style={{ marginLeft: 'auto' }}
          disabled={!dirty || saving}
          onClick={() => {
            setLayout(JSON.parse(saved));
            setErrors([]);
            setSelected(null);
            setTabNote('');
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
        <section className="pane de-canvas-pane" aria-label="Phone layout">
          <div className="de-canvas-bar">
            <PageTabs
              pages={layout.pages}
              current={page.id}
              onPick={(pid) => (setPageId(pid), setSelected(null))}
              onChange={setPages}
              confirm={confirm}
              noun="phone layout"
              classFor={(p) => (p.tab ? '' : 'de-tab--link')}
              newPage={() => ({ tab: tabCount < MAX_TABS })}
              menuExtra={(p, close) => (
                <button type="button" role="menuitem" className="more-item" disabled={!p.tab && tabCount >= MAX_TABS} onClick={() => (close(), setTab(p, !p.tab))}>
                  {p.tab ? 'Open from a link instead' : tabCount >= MAX_TABS ? `Show as a tab (${MAX_TABS} already)` : 'Show as a tab'}
                </button>
              )}
            />
          </div>
          <div className="de-stage pl-stage" ref={stage}>
            <div className="de-scaler pl-scaler" style={{ width: PHONE.w * scale, height: PHONE.h * scale }}>
              <PhoneCanvas
                layout={layout}
                page={page}
                who={isDefault ? '' : mine ? me && me.name : info.name}
                types={types}
                selected={selected}
                scale={scale}
                onSelect={setSelected}
                onMove={(tid, box) => setTiles((ts) => ts.map((t) => (t.id === tid ? { ...t, ...box } : t)))}
                onRemove={removeTile}
                onPick={(pid) => (setPageId(pid), setSelected(null))}
                onBlocked={setNote}
              />
            </div>
            {note ? (
              <div className="de-note" role="status">
                {note}
              </div>
            ) : null}
          </div>
        </section>

        <aside className="pane de-side" aria-label={tile ? 'Tile settings' : 'Phone layout settings'}>
          <div className="pane-body stack">
            {tile ? (
              <>
                <div>
                  <div className="de-side-title">{spec ? spec.name : tile.type}</div>
                  {spec ? <p className="help">{spec.description}</p> : null}
                  <div className="help">
                    {tile.w} × {tile.h} · column {tile.x + 1}, row {tile.y + 1}
                  </div>
                </div>
                {((spec && spec.fields) || []).map((f) => (
                  <TileField key={f.key} field={f} config={tile.config || {}} tileType={tile.type} onChange={(config) => setTiles((ts) => ts.map((t) => (t.id === tile.id ? { ...t, config } : t)))} />
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
                <div className="pl-whose">
                  <div className="de-side-title">{whoName}</div>
                  {whose}
                </div>
                <div className="pl-section">
                  <Switch checked={!!layout.today} onChange={(on) => setLayout((l) => ({ ...l, today: on }))} label={`Today card at the top of “${firstTab.name}”`} />
                  <Switch checked={!!page.tab} onChange={(on) => setTab(page, on)} label={`Show “${page.name}” as a tab`} />
                  {tabNote ? (
                    <p className="pl-tabnote" role="status">
                      {tabNote}
                    </p>
                  ) : null}
                  <p className="help">
                    Tabs: {[...layout.pages.filter((p) => p.tab).map((p) => p.name), 'People', 'Alerts'].join(' · ')}
                    <br />
                    {links.length ? `Opened from links on the first tab: ${links.map((p) => p.name).join(', ')}` : 'Every page is a tab.'}
                    {layout.pages.length >= MAX_PAGES ? ` · ${MAX_PAGES} pages is the most a phone has.` : ''}
                  </p>
                </div>
                <div className="de-side-title">+ Add tile</div>
                <p className="help">Pick a tile; it goes in the first free spot on “{page.name}”. Drag it where you want it, and its corner to resize.</p>
                <div className="pl-catalog">
                  {((catalog.data && catalog.data.types) || []).filter((t) => !t.wallOnly).map((t) => (
                    <button key={t.type} type="button" className="pl-type" title={t.description} disabled={t.available === false} onClick={() => addTile(t)}>
                      {t.name}
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
