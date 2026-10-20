// Full-screen live camera + today's alerts (the approved "Live camera + today's alerts" board,
// in the dashboard's own look). #/cameras/<id>
//   left: "‹ Back" pill, LIVE/REPLAY tag + name, grid button; the full-quality stream with
//         prev/next arrows and dots for where you are among the cameras.
//   right: "Today" — this camera's alerts (or all cameras'); tapping one replays its clip here.

import { useEffect, useMemo, useRef, useState } from 'react';
import { enc } from '../api.js';
import { liveReturnPath, navigate, useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { fmtClock } from '../format.js';
import LiveVideo from '../components/LiveVideo.jsx';
import { clipUrl } from '../components/Camera.jsx';
import { cameraForZone, cameraTiles, useTodayAlerts } from '../components/CameraGrid.jsx';

function whatOf(a) {
  const objs = (a.objects || []).map((o) => String(o).replace(/_/g, ' '));
  if (objs.length) return objs.map((o, i) => (i ? o : o.charAt(0).toUpperCase() + o.slice(1))).join(', ');
  return a.title || 'Alert';
}

const CLIP_TRIES = 3;
const CLIP_WAITS = [2000, 5000]; // before the 2nd and 3rd tries

/** An alert's clip. Until it can play, its snapshot under a veil ("Loading the clip…"); a player that fails
 *  tries again by itself (CLIP_TRIES in all, a fresh request each time), and only then says so, with Try again. */
function Clip({ alert }) {
  const src = clipUrl(alert.clip);
  const [go, setGo] = useState({ n: 1, tries: 1, state: 'loading' }); // n: requests so far; state: loading | waiting | ready | failed
  const begun = useRef(false);
  useEffect(() => {
    setGo({ n: 1, tries: 1, state: 'loading' });
    begun.current = false;
  }, [alert.id]);
  useEffect(() => {
    if (go.state !== 'waiting') return undefined;
    const t = setTimeout(() => setGo((g) => ({ n: g.n + 1, tries: g.tries + 1, state: 'loading' })), CLIP_WAITS[go.tries - 1] || 5000);
    return () => clearTimeout(t);
  }, [go]);
  // Start it ourselves once it can play: some tablets' web views ignore autoplay (React doesn't put
  // `muted` on the element itself), which left the clip waiting for a second tap on ▶.
  const start = (ev) => {
    if (begun.current) return; // once per clip: a pause by hand stays paused
    begun.current = true;
    const v = ev.currentTarget;
    v.muted = true;
    if (v.paused) v.play().catch(() => {});
  };
  const ready = () => setGo((g) => (g.state === 'loading' ? { ...g, state: 'ready' } : g));
  const failed = () => setGo((g) => ({ ...g, state: g.tries < CLIP_TRIES ? 'waiting' : 'failed' }));
  const snapshot = alert.snapshotUrl ? <img src={alert.snapshotUrl} alt={`Snapshot: ${alert.title || whatOf(alert)}`} /> : null;
  if (!src) {
    return (
      <div className="lv-clip lv-clip--none">
        {snapshot}
        <span className="lv-note">No video for this alert.</span>
      </div>
    );
  }
  const playing = go.state === 'loading' || go.state === 'ready';
  return (
    <>
      {playing ? (
        <video
          key={alert.id + ':' + go.n}
          className="lv-clip"
          src={go.n > 1 ? src + (src.includes('?') ? '&' : '?') + 'try=' + go.n : src}
          controls
          autoPlay
          playsInline
          muted
          onLoadedMetadata={start}
          onCanPlay={(ev) => {
            start(ev);
            ready();
          }}
          onLoadedData={ready}
          onError={failed}
          aria-label={`Recording: ${alert.title || whatOf(alert)}`}
        />
      ) : null}
      {go.state !== 'ready' ? (
        <div className="lv-clip lv-clip--none" aria-live="polite">
          {snapshot}
          {go.state === 'failed' ? (
            <span className="lv-clip-msg">
              <span className="lv-note">This clip couldn't be played.</span>
              <button type="button" className="lv-clip-again" onClick={() => setGo((g) => ({ n: g.n + 1, tries: 1, state: 'loading' }))}>
                Try again
              </button>
            </span>
          ) : (
            <span className="lv-clip-msg">
              <span className="lv-clip-spin" aria-hidden="true" />
              <span className="lv-note">Loading the clip…</span>
            </span>
          )}
        </div>
      ) : null}
    </>
  );
}

/** The alert to replay when the view opens (the wall's alerts tile sets it before navigating). */
export const pendingReplay = { alert: null };

/**
 * wall: the wall tablet's version (night glass): its own paths, and Back returns to the tablet page.
 */
export default function LiveView({ id, wall = null }) {
  const { entities, entitiesById, kindsById } = useHub();
  const all = entities || [];
  const cams = cameraTiles(all, kindsById);
  const e = entitiesById[id];
  // A zone plays its camera's stream; the dots show that camera's place.
  const anchor = e ? cameraForZone(e, all) || e : null;
  const idx = anchor ? cams.findIndex((c) => c.id === anchor.id) : -1;
  const [allCams, setAllCams] = useState(false);
  const [playing, setPlaying] = useState(() => {
    const a = pendingReplay.alert;
    pendingReplay.alert = null;
    return a;
  });
  const camPath = (cid) => (wall ? 'wall/camera/' : 'cameras/') + enc(cid);
  const today = useTodayAlerts(allCams ? 'api/alerts' : e ? e.alerts : null);
  const list = useMemo(() => today.list || [], [today.list]);

  // New alerts slide in at the top: remember what was already shown.
  const seen = useRef(null);
  const fresh = new Set();
  if (seen.current) list.forEach((a) => !seen.current.has(a.id) && fresh.add(a.id));
  useEffect(() => {
    if (today.list) seen.current = new Set(today.list.map((a) => a.id));
  }, [today.list]);

  const back = () => (wall ? wall.back() : navigate(liveReturnPath() || 'cameras'));
  const toLive = () => setPlaying(null);
  const step = (d) => {
    if (cams.length < 2) return;
    const i = idx < 0 ? 0 : idx;
    setPlaying(null);
    navigate(camPath(cams[(i + d + cams.length) % cams.length].id), { replace: true });
  };
  const play = (a) => {
    setPlaying(a);
    // From "All cameras": follow the alert to its camera (the list stays as it is).
    if (a.entityId && a.entityId !== id && entitiesById[a.entityId]) navigate(camPath(a.entityId), { replace: true });
  };

  useEffect(() => {
    const onKey = (ev) => {
      if (ev.key === 'Escape') {
        if (playing) setPlaying(null);
        else back();
      } else if (!playing && ev.key === 'ArrowLeft') step(-1);
      else if (!playing && ev.key === 'ArrowRight') step(1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (!entities) return <div className="lv" />;
  if (!e) {
    return (
      <div className="lv lv--missing">
        <div className="lv-bar">
          <button type="button" className="lv-pill" onClick={back}>
            <Icon name="chevronLeft" size={22} strokeWidth={2.4} /> Back
          </button>
          <span className="lv-heading">This camera is no longer here.</span>
        </div>
      </div>
    );
  }

  const heading = playing ? `${whatOf(playing)} at ${playing.cameraName || e.name} · ${fmtClock(playing.start)}` : e.name;
  const count = list.length === 1 ? '1 alert' : `${list.length} alerts`;

  return (
    <div className={'lv' + (wall ? ' lv--wall' : '')} role="dialog" aria-modal="true" aria-label={`Live: ${e.name}`}>
      <div className="lv-main">
        <div className="lv-bar">
          {playing ? (
            <button type="button" className="lv-pill" onClick={toLive}>
              <Icon name="chevronLeft" size={22} strokeWidth={2.4} /> Back to live
            </button>
          ) : (
            <button type="button" className="lv-pill" onClick={back}>
              <Icon name="chevronLeft" size={22} strokeWidth={2.4} /> Back
            </button>
          )}
          <div className="lv-title">
            {playing ? <span className="lv-tag lv-tag--replay">REPLAY</span> : <span className="lv-tag lv-tag--live">LIVE</span>}
            <span className="lv-heading">{heading}</span>
          </div>
          <a className="lv-round" href={wall ? '#/wall/cameras' : '#/cameras'} aria-label="All cameras" title="All cameras">
            <Icon name="grid" size={22} />
          </a>
        </div>

        <div className="lv-stage-wrap">
          <div className="lv-stage">
            {playing ? <Clip alert={playing} /> : <LiveVideo key={e.id} entity={e} quality="main" priority className="lv-video" stillHeight={1080} />}
            {!playing && cams.length > 1 ? (
              <>
                <button type="button" className="lv-arrow lv-arrow--prev" onClick={() => step(-1)} aria-label="Previous camera">
                  <Icon name="chevronLeft" size={26} strokeWidth={2.4} />
                </button>
                <button type="button" className="lv-arrow lv-arrow--next" onClick={() => step(1)} aria-label="Next camera">
                  <Icon name="chevronRight" size={26} strokeWidth={2.4} />
                </button>
              </>
            ) : null}
          </div>
        </div>

        <div className="lv-dots" aria-label={idx >= 0 ? `Camera ${idx + 1} of ${cams.length}` : undefined}>
          {cams.map((c) => (
            <span key={c.id} className={'lv-dot' + (anchor && c.id === anchor.id ? ' is-on' : '')} />
          ))}
        </div>
      </div>

      <aside className="lv-side" aria-label="Today's alerts">
        <div className="lv-side-head">
          <span className="lv-side-title">Today</span>
          <span className="lv-side-count">{today.list ? count : ''}</span>
        </div>
        <div className="lv-toggle" role="group" aria-label="Which alerts">
          <button type="button" aria-pressed={!allCams} onClick={() => setAllCams(false)}>
            This camera
          </button>
          <button type="button" aria-pressed={allCams} onClick={() => setAllCams(true)}>
            All cameras
          </button>
        </div>
        <div className="lv-list">
          {!today.list ? (
            <div className="lv-empty">{today.error ? "Couldn't load alerts." : 'Loading…'}</div>
          ) : !list.length ? (
            <div className="lv-empty">{allCams ? 'Nothing on any camera today.' : e.alerts ? 'Nothing on this camera today.' : 'This camera keeps no alerts.'}</div>
          ) : (
            list.map((a) => {
              const on = playing && playing.id === a.id;
              const where = [allCams ? a.cameraName : null, (a.zones || []).join(', ')].filter(Boolean).join(' · ');
              return (
                <button key={a.id} type="button" className={'lv-item' + (on ? ' is-on' : '') + (fresh.has(a.id) ? ' is-new' : '')} onClick={() => play(a)} aria-pressed={!!on}>
                  <span className="lv-thumb">
                    {a.thumbUrl || a.snapshotUrl ? <img src={a.thumbUrl || a.snapshotUrl} alt="" loading="lazy" /> : null}
                    <span className="lv-thumb-play" aria-hidden="true">
                      <Icon name="play" size={16} />
                    </span>
                  </span>
                  <span className="lv-item-text">
                    <span className="lv-item-what">{whatOf(a)}</span>
                    {where ? <span className="lv-item-where">{where}</span> : null}
                    <span className="lv-item-time">{fmtClock(a.start)}</span>
                  </span>
                </button>
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}
