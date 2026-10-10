import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { useHub } from '../hooks.js';
import { useLive } from '../live.js';
import { Empty, ErrorBox, Loading } from '../components/Controls.jsx';
import { ActivityItem } from '../components/Camera.jsx';
import { AlertCard, alertFromEvent } from '../components/AlertCard.jsx';
import { fmtClock, fmtDay } from '../format.js';
import { kindIcon } from '../model.js';

const PAGE = 50;

// Icons for activity that isn't about one device.
const EVENT_ICONS = { automation: 'bolt', followup: 'check', command: 'bolt', problem: 'alert', detection: 'camera', alert: 'camera', integration: 'plug', phone: 'phone', signin: 'key', account: 'user', weather: 'cloud' };
// Activity about no longer known devices still files under a category (weather alerts → Climate).
const EVENT_CATEGORY = { weather: 'climate' };

/**
 * "Show" options: everything, one per device category from /api/meta (only those with
 * devices), automations and problems. Camera alerts/detections go with their device's category,
 * or with any category that has camera devices when the device isn't known.
 */
const FIXED_VIEWS = [
  { id: 'automations', label: 'Automations', events: ['automation', 'followup'] },
  { id: 'problems', label: 'Problems', events: ['problem'], failedSignIns: true },
  { id: 'signins', label: 'Sign-ins', events: ['signin', 'account'] },
];
const CAMERA_ACTIVITY = ['alert', 'detection'];

export default function Activity() {
  const { kindsById, entitiesById, entities, categories } = useHub();
  const [view, setView] = useState('all');
  const [search, setSearch] = useState('');
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const itemsRef = useRef(null);
  itemsRef.current = items;

  // First page; newer entries then arrive over the live socket (no polling).
  useEffect(() => {
    let alive = true;
    setItems(null);
    setError(null);
    (async () => {
      try {
        const data = await api.get('api/events?limit=' + PAGE, { quiet: true });
        if (!alive) return;
        setItems(data || []);
        setMore((data || []).length >= PAGE);
      } catch (e) {
        if (alive) setError(e);
      }
    })();
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  // Each new activity entry slides in at the top; after a reconnect, catch up once.
  useLive((m) => {
    if (m.type === 'activity' && m.entry) {
      setItems((xs) => (!xs || xs.some((x) => x.id === m.entry.id) ? xs : [m.entry, ...xs]));
    } else if (m.type === 'reconnected' && itemsRef.current) {
      api
        .get('api/events?limit=' + PAGE, { quiet: true })
        .then((data) => {
          const cur = itemsRef.current || [];
          const ids = new Set(cur.map((e) => e.id));
          const fresh = (data || []).filter((e) => !ids.has(e.id));
          if (fresh.length) setItems((xs) => [...fresh, ...(xs || [])]);
        })
        .catch(() => {});
    }
  });

  const loadMore = async () => {
    if (!items || !items.length) return;
    setLoadingMore(true);
    try {
      const data = await api.get(`api/events?limit=${PAGE}&before=${items[items.length - 1].ts}`);
      const ids = new Set(items.map((e) => e.id));
      const older = (data || []).filter((e) => !ids.has(e.id));
      setItems((xs) => [...xs, ...older]);
      setMore((data || []).length >= PAGE && older.length > 0);
    } catch {
      /* toast shown */
    } finally {
      setLoadingMore(false);
    }
  };

  const deviceKind = (x) => (x.entityId && entitiesById[x.entityId] ? entitiesById[x.entityId].kind : null);
  const catOf = (kindId) => (kindId && kindsById[kindId] ? kindsById[kindId].category : null);
  const usedCats = (categories || []).filter((c) => (entities || []).some((e) => catOf(e.kind) === c.id));
  const views = [
    { id: 'all', label: 'Everything' },
    ...usedCats.map((c) => ({
      id: 'cat:' + c.id,
      label: c.name,
      category: c.id,
      cameras: (entities || []).some((e) => catOf(e.kind) === c.id && kindsById[e.kind] && kindsById[e.kind].hasCamera),
    })),
    ...FIXED_VIEWS,
  ];
  const current = views.find((v) => v.id === view) || views[0];
  const needle = search.trim().toLowerCase();
  const matches = (x) => {
    if (current.id !== 'all') {
      const byEvent = (current.events || []).includes(x.kind) || (!!current.failedSignIns && x.kind === 'signin' && !!(x.data && x.data.failed)) || (!!current.cameras && !deviceKind(x) && CAMERA_ACTIVITY.includes(x.kind));
      const byDevice = !!current.category && (catOf(deviceKind(x)) || EVENT_CATEGORY[x.kind]) === current.category;
      if (!byEvent && !byDevice) return false;
    }
    if (!needle) return true;
    return `${x.title || ''} ${x.body || ''}`.toLowerCase().includes(needle);
  };
  const shown = (items || []).filter(matches);
  const iconFor = (x) => {
    const k = deviceKind(x);
    if (EVENT_ICONS[x.kind] && x.kind !== 'command') return EVENT_ICONS[x.kind];
    return k && kindsById[k] ? kindIcon(kindsById[k]) : EVENT_ICONS[x.kind] || 'dot';
  };

  const groups = [];
  shown.forEach((e) => {
    const day = fmtDay(e.ts);
    const g = groups[groups.length - 1];
    if (g && g.day === day) g.items.push(e);
    else groups.push({ day, items: [e] });
  });

  return (
    <div className="app-page">
      <div className="app-head">
      <h1 className="page-title">Activity</h1>

      <div className="filter-bar filter-bar--inline" role="search">
        <label className="filter">
          <span className="filter-label">Show</span>
          <select className="select" value={current.id} onChange={(e) => setView(e.target.value)}>
            {views.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <label className="filter filter--grow">
          <span className="filter-label">Search</span>
          <input className="input" type="search" placeholder="e.g. Side Door, person, Chris" value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
      </div>
      </div>

      <div className="pane">
      <div className="pane-body" style={{ paddingTop: 4 }}>
        {items === null ? (
          error ? (
            <div style={{ padding: '16px 0' }}>
              <ErrorBox error={error} onRetry={() => setReloadKey((k) => k + 1)} />
            </div>
          ) : (
            <Loading>Loading activity…</Loading>
          )
        ) : !items.length ? (
          <Empty title="Nothing here yet">Lock changes, doorbell presses, camera detections and automation runs appear here as they happen.</Empty>
        ) : !shown.length ? (
          <Empty title="Nothing matches" action={more ? <button type="button" className="btn btn-sm" onClick={loadMore} disabled={loadingMore}>Look further back</button> : null}>
            Nothing recent{current.id !== 'all' ? ` for “${current.label}”` : ''}
            {needle ? ` matching “${search.trim()}”` : ''}.
          </Empty>
        ) : (
          groups.map((g) => (
            <div key={g.day}>
              <div className="feed-day">{g.day}</div>
              {g.items.map((e) => (
                e.kind === 'alert' ? (
                  <div key={e.id} className="feed-alert">
                    <AlertCard alert={alertFromEvent(e)} compact />
                  </div>
                ) : (
                  <ActivityItem key={e.id} event={e} icon={iconFor(e)} time={fmtClock(e.ts)} />
                )
              ))}
            </div>
          ))
        )}
        {shown.length > 0 && more ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '14px 0 4px' }}>
            <button type="button" className="btn" onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? 'Loading…' : 'Load older'}
            </button>
          </div>
        ) : null}
      </div>
      </div>
    </div>
  );
}
