// Camera stills, detection thumbnails and the clip player (all served by the hub).

import { useEffect, useRef, useState } from 'react';
import { enc } from '../api.js';
import { Icon } from '../icons.jsx';
import { fmtWhen } from '../format.js';
import { countList } from '../model.js';

/** "<integration>:<clip id>" -> api/clips/<integration>/<clip id> */
export function clipUrl(clip) {
  if (!clip || typeof clip !== 'string') return null;
  // The hub's alert lists give the signed-in path already; activity entries and pushes give the clip id.
  if (clip.startsWith('api/clips/')) return clip;
  if (clip.startsWith('/api/clips/')) return clip.slice(1);
  const i = clip.indexOf(':');
  if (i <= 0) return null;
  return `api/clips/${enc(clip.slice(0, i))}/${enc(clip.slice(i + 1))}`;
}

export function snapshotUrl(id) {
  return id ? 'api/snapshots/' + enc(id) : null;
}

/** A device with a live picture: the hub gives it a `still` URL (any integration, any kind). */
export function hasStill(entity) {
  return !!(entity && entity.still);
}

export function stillUrl(entity, h) {
  if (!hasStill(entity)) return null;
  const sep = entity.still.includes('?') ? '&' : '?';
  return `${entity.still}${sep}h=${Math.min(1080, Math.round(h))}`;
}

/** True while the element is on screen and the tab is visible. */
function useVisible(ref) {
  const [onScreen, setOnScreen] = useState(true);
  const [tabVisible, setTabVisible] = useState(typeof document === 'undefined' || document.visibilityState !== 'hidden');
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const io = new IntersectionObserver((entries) => setOnScreen(entries.some((e) => e.isIntersecting)), { rootMargin: '100px' });
    io.observe(el);
    return () => io.disconnect();
  }, [ref]);
  useEffect(() => {
    const on = () => setTabVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);
  return onScreen && tabVisible;
}

/**
 * Latest camera frame, refreshed every `interval` ms while visible. The next
 * frame is preloaded and swapped in only once it has loaded, so tiles never flash.
 */
export function CameraStill({ entity, h = 360, interval = 5000, className = '', alt }) {
  const box = useRef(null);
  const visible = useVisible(box);
  const base = stillUrl(entity, h);
  const [src, setSrc] = useState(base ? `${base}&t=${Date.now()}` : null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!base || !visible) return undefined;
    let alive = true;
    const tick = () => {
      const next = `${base}&t=${Date.now()}`;
      const img = new Image();
      img.onload = () => {
        if (!alive) return;
        setSrc(next);
        setFailed(false);
      };
      img.onerror = () => alive && setFailed(true);
      img.src = next;
    };
    // The first frame comes from the initial src; refresh from here on.
    const t = setInterval(tick, interval);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [base, visible, interval]);

  const offline = entity && (entity.available === false || entity.state === 'offline');
  return (
    <div ref={box} className={'cam-still ' + className}>
      {src && !failed ? <img src={src} alt={alt || `Latest picture from ${entity.name}`} loading="lazy" onError={() => setFailed(true)} /> : null}
      {!src || failed || offline ? (
        <div className="cam-still-empty">
          <Icon name="camera" size={28} />
          <span>{offline ? 'Camera offline' : !base ? 'No live picture' : failed ? 'No picture right now' : 'Loading…'}</span>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Dark modal for a clip (video) or a snapshot (image). Videos show a spinner
 * while loading and a plain explanation if the camera system has no recording.
 */
export function MediaModal({ clip, image, title, ongoing, onClose }) {
  const closeBtn = useRef(null);
  const url = clip ? clipUrl(clip) : null;
  const [state, setState] = useState(clip ? (url ? 'loading' : 'error') : 'ready');
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const last = document.activeElement;
    closeBtn.current && closeBtn.current.focus();
    const onKey = (e) => e.key === 'Escape' && closeRef.current();
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      if (last && last.focus) last.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal clip-modal" role="dialog" aria-modal="true" aria-label={title || (clip ? 'Video' : 'Snapshot')}>
        <div className="clip-head">
          <div className="strong">
            <Icon name={clip ? 'play' : 'camera'} size={15} /> {clip ? 'Video' : 'Snapshot'}
            {title ? <span className="muted-dark"> · {title}</span> : null}
          </div>
          <button ref={closeBtn} type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            <Icon name="x" size={18} />
          </button>
        </div>
        {clip ? (
          <div className="clip-stage">
            {state === 'error' ? (
              <div className="clip-missing" role="alert">
                <Icon name="alert" size={22} />
                <span>This video isn't available — the camera system may not have kept a recording for it.</span>
              </div>
            ) : (
              <>
                {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                <video
                  className="clip-video"
                  src={url}
                  controls
                  autoPlay
                  playsInline
                  onLoadedData={() => setState('ready')}
                  onCanPlay={() => setState('ready')}
                  onError={() => setState('error')}
                />
                {state === 'loading' ? (
                  <div className="clip-loading" role="status">
                    <span className="spinner" aria-hidden="true" /> Loading video…
                  </div>
                ) : null}
              </>
            )}
            {ongoing && state !== 'error' ? <div className="clip-note">Still recording — the video will be complete when it ends.</div> : null}
          </div>
        ) : (
          <img className="clip-image" src={image} alt={title ? `Snapshot: ${title}` : 'Snapshot'} />
        )}
      </div>
    </div>
  );
}

/** Kept for older call sites: a clip in the media modal. */
export function ClipModal({ clip, title, onClose }) {
  return <MediaModal clip={clip} title={title} onClose={onClose} />;
}

export function CountChips({ counts, empty }) {
  const list = countList(counts);
  if (!list.length) return empty ? <span className="count-chip count-chip--empty">{empty}</span> : null;
  return (
    <>
      {list.map((t) => (
        <span key={t} className="count-chip">
          {t}
        </span>
      ))}
    </>
  );
}

/**
 * One activity entry; with a snapshot it shows a thumbnail, with a clip a ▶ Play
 * button that opens the player.
 */
export function ActivityItem({ event: x, time, icon }) {
  const [playing, setPlaying] = useState(false);
  const snap = x.data && x.data.snapshotId;
  const clip = x.data && x.data.clip;
  return (
    <div className="feed-item">
      <div className="feed-main">
        {snap ? (
          <a className="feed-thumb" href={snapshotUrl(snap)} target="_blank" rel="noreferrer" aria-label={`Snapshot: ${x.title}`}>
            <img src={snapshotUrl(snap)} alt="" loading="lazy" />
          </a>
        ) : icon ? (
          <span className="feed-icon" aria-hidden="true">
            <Icon name={icon} size={16} />
          </span>
        ) : null}
        <span style={{ paddingTop: snap ? 2 : 5, minWidth: 0 }}>
          <span className="strong">{x.title}</span>
          {x.body ? <span className="muted"> · {x.body}</span> : null}
          {clip && clipUrl(clip) ? (
            <div style={{ marginTop: 6 }}>
              <button type="button" className="btn btn-sm" onClick={() => setPlaying(true)} aria-label={`Play clip: ${x.title}`}>
                <Icon name="play" size={13} /> Play
              </button>
            </div>
          ) : null}
        </span>
      </div>
      <span className="row-time" style={{ paddingTop: 5 }}>
        {time || fmtWhen(x.ts)}
      </span>
      {playing ? <ClipModal clip={clip} title={`${x.title}${x.body ? ' · ' + x.body : ''}`} onClose={() => setPlaying(false)} /> : null}
    </div>
  );
}
