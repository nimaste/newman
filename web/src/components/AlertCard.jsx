// Camera alerts (from any connection that keeps them): one card per alert with the Snapshot and the Video side by
// side, clearly labelled. Reused by camera/zone pages, the Cameras page (the
// future wall-tablet "latest alerts" panel) and Activity.

import { useCallback, useEffect, useState } from 'react';
import { api, enc } from '../api.js';
import { Icon } from '../icons.jsx';
import { useLiveRefresh } from '../live.js';
import { fmtWhen } from '../format.js';
import { Empty, ErrorBox, Loading } from './Controls.jsx';
import { clipUrl, MediaModal } from './Camera.jsx';

function duration(a) {
  if (!a.start || !a.end) return null;
  const s = Math.max(1, Math.round(a.end - a.start));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60 ? (s % 60) + ' s' : ''}`.trim();
}

/** Activity entries of kind "alert" in the same shape as /api/alerts items. */
export function alertFromEvent(x) {
  const d = x.data || {};
  // Pictures as the hub gives them: ready-made URLs when present, else the stored snapshot.
  const snap = d.snapshotUrl || (d.snapshotId ? 'api/snapshots/' + enc(d.snapshotId) : null);
  return {
    id: 'ev-' + x.id,
    title: x.title,
    zones: x.body ? [x.body] : [],
    severity: d.severity,
    start: x.ts,
    end: d.end !== undefined ? d.end : x.ts,
    clip: d.clip || null,
    snapshotUrl: snap,
    thumbUrl: d.thumbUrl || snap,
    entityId: x.entityId,
  };
}

export function AlertCard({ alert: a, compact }) {
  const [open, setOpen] = useState(null); // 'snapshot' | 'video'
  const ongoing = a.start && !a.end;
  const hasVideo = !!(a.clip && clipUrl(a.clip));
  const len = duration(a);
  return (
    <article className={'alert-card' + (compact ? ' alert-card--compact' : '')} aria-label={a.title}>
      <div className="alert-head">
        <div className="alert-title">
          {a.title}
          {/* Alerts are the default; only a lesser "detection" gets a badge. */}
          {a.severity === 'detection' ? <span className="sev sev--detection">Detection</span> : null}
          {ongoing ? <span className="sev sev--live">Happening now</span> : null}
        </div>
        <div className="alert-meta">
          {fmtWhen(a.start)}
          {(a.zones || []).length ? <span> · {a.zones.join(', ')}</span> : null}
        </div>
      </div>
      <div className="alert-media">
        <button
          type="button"
          className="media-tile"
          onClick={() => a.snapshotUrl && setOpen('snapshot')}
          disabled={!a.snapshotUrl}
          aria-label={`Snapshot of ${a.title} — open full size`}
        >
          {a.snapshotUrl ? <img src={a.snapshotUrl} alt="" loading="lazy" /> : <span className="media-none">No snapshot</span>}
          <span className="media-label">
            <Icon name="camera" size={14} /> Snapshot
          </span>
        </button>
        <button
          type="button"
          className="media-tile media-tile--video"
          onClick={() => hasVideo && setOpen('video')}
          disabled={!hasVideo}
          aria-label={`Video of ${a.title} — play`}
        >
          {a.thumbUrl ? <img src={a.thumbUrl} alt="" loading="lazy" /> : <span className="media-none">No video</span>}
          {hasVideo ? (
            <span className="media-play" aria-hidden="true">
              <Icon name="play" size={22} />
            </span>
          ) : null}
          <span className="media-label">
            <Icon name="play" size={13} /> Video{len ? ` · ${len}` : ''}
            {ongoing ? ' · recording' : ''}
          </span>
        </button>
      </div>
      {open === 'snapshot' ? <MediaModal image={a.snapshotUrl} title={a.title} onClose={() => setOpen(null)} /> : null}
      {open === 'video' ? <MediaModal clip={a.clip} title={a.title} ongoing={ongoing} onClose={() => setOpen(null)} /> : null}
    </article>
  );
}

/**
 * Alerts from `path` (…?limit=N added), newest first, `pageSize` at a time with "Show older".
 * The newest page refreshes when a live alert arrives.
 */
export function AlertList({ path, pageSize = 10, emptyText, grid }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const sep = path.includes('?') ? '&' : '?';

  const loadFirst = useCallback(
    async (quiet) => {
      try {
        const data = await api.get(`${path}${sep}limit=${pageSize}`, { quiet: true });
        setItems((cur) => {
          const list = Array.isArray(data) ? data : [];
          if (!cur || !quiet) return list;
          // Keep older pages that were already loaded; replace the newest page.
          const ids = new Set(list.map((x) => x.id));
          const rest = cur.filter((x) => !ids.has(x.id) && x.start < (list[list.length - 1] || { start: Infinity }).start);
          return [...list, ...rest];
        });
        if (!quiet) setMore(Array.isArray(data) && data.length >= pageSize);
        setError(null);
      } catch (e) {
        if (!quiet) setError(e);
      }
    },
    [path, sep, pageSize],
  );

  useEffect(() => {
    setItems(null);
    loadFirst(false);
  }, [loadFirst]);
  // New alerts arrive as live activity entries: refresh the newest page then (and after a reconnect).
  useLiveRefresh((m) => m.type === 'activity' && !!m.entry && m.entry.kind === 'alert', () => loadFirst(true), 800);

  const older = async () => {
    if (!items || !items.length) return;
    setBusy(true);
    try {
      const last = items[items.length - 1];
      const data = await api.get(`${path}${sep}limit=${pageSize}&before=${last.start}`);
      const ids = new Set(items.map((x) => x.id));
      const add = (data || []).filter((x) => !ids.has(x.id));
      setItems((xs) => [...xs, ...add]);
      setMore((data || []).length >= pageSize && add.length > 0);
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };

  if (items === null) return error ? <ErrorBox error={error} onRetry={() => loadFirst(false)} /> : <Loading>Loading alerts…</Loading>;
  if (!items.length) return <Empty title="No alerts yet">{emptyText || 'When a camera raises an alert it shows up here with its snapshot and video.'}</Empty>;
  return (
    <div className="stack-sm">
      <div className={grid ? 'alert-grid' : 'alert-list'}>
        {items.map((a) => (
          <AlertCard key={a.id} alert={a} />
        ))}
      </div>
      {more ? (
        <button type="button" className="btn" onClick={older} disabled={busy} style={{ alignSelf: 'center', minHeight: 44 }}>
          {busy ? 'Loading…' : 'Show older'}
        </button>
      ) : null}
    </div>
  );
}
