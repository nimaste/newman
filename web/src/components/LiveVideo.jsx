// Real-time camera video: the hub relays the camera system's MSE stream over a websocket
// (entity.live = "api/entities/{id}/live", ?quality=sub|main).
//   1. open the socket, send {"type":"mse","value":"<codecs this browser plays>"}
//   2. the reply {"type":"mse","value":"video/mp4; codecs=…"} is the SourceBuffer mime
//   3. binary fMP4 segments follow; append them in order.
// Latency stays low: the buffer is trimmed to ~10 s and playback jumps to the live edge
// when it falls more than 2 s behind. Off-screen/hidden players stop; at most MAX_STREAMS
// tiles play at once. If the stream fails, the device's still picture is shown instead.

import { useEffect, useRef, useState } from 'react';
import { wsUrl } from '../live.js';
import { CameraStill, hasStill } from './Camera.jsx';

const CODECS = ['avc1.640029', 'avc1.64002A', 'avc1.640033', 'hvc1.1.6.L153.B0', 'mp4a.40.2', 'mp4a.40.5', 'flac', 'opus'];
const MAX_STREAMS = 8;
const KEEP_SECONDS = 10;
const MAX_LAG = 2;

function mediaSourceClass() {
  if (typeof window === 'undefined') return null;
  return window.ManagedMediaSource || window.MediaSource || null;
}

export function canPlayLive() {
  return !!mediaSourceClass();
}

// Some browsers say they take H.265 (hvc1) but then decode no pictures (e.g. without a hardware
// decoder). Once that is seen, stop offering it so cameras that can send H.264 do.
let hevcBroken = false;

function supportedCodecs(MS) {
  return CODECS.filter((c) => !(hevcBroken && /^hvc1|^hev1/.test(c))).filter((c) => {
    try {
      return MS.isTypeSupported(`video/mp4; codecs="${c}"`);
    } catch {
      return false;
    }
  }).join(',');
}

/* ---- a small pool so only MAX_STREAMS tiles stream at once (the full-screen view is exempt) ---- */
const active = new Set();
const waiting = new Set();
function wake() {
  waiting.forEach((fn) => fn());
}
function useStreamSlot(want, priority) {
  const [has, setHas] = useState(false);
  const token = useRef({});
  useEffect(() => {
    const me = token.current;
    if (!want) {
      setHas(false);
      return undefined;
    }
    if (priority) {
      setHas(true);
      return undefined;
    }
    const tryTake = () => {
      if (active.has(me)) return;
      if (active.size < MAX_STREAMS) {
        active.add(me);
        waiting.delete(me.retry);
        setHas(true);
      }
    };
    me.retry = tryTake;
    waiting.add(tryTake);
    tryTake();
    return () => {
      waiting.delete(tryTake);
      if (active.delete(me)) wake();
      setHas(false);
    };
  }, [want, priority]);
  return has;
}

/** True while `ref` is on screen and the page is visible. */
function useOnScreen(ref) {
  const [onScreen, setOnScreen] = useState(false);
  const [tabVisible, setTabVisible] = useState(typeof document === 'undefined' || document.visibilityState !== 'hidden');
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') {
      setOnScreen(true);
      return undefined;
    }
    const io = new IntersectionObserver((entries) => setOnScreen(entries.some((e) => e.isIntersecting)), { rootMargin: '50px' });
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

/** Play `url` (a live socket path) into `video`. Calls onState('loading'|'playing'|'failed'). Returns a stop function. */
function startMse(video, url, onState) {
  const MS = mediaSourceClass();
  if (!MS) {
    onState('failed');
    return () => {};
  }
  let closed = false;
  let ws = null;
  let sb = null;
  const queue = [];
  const ms = new MS();
  const managed = typeof window.ManagedMediaSource !== 'undefined' && MS === window.ManagedMediaSource;
  let objectUrl = null;
  if (managed) {
    video.disableRemotePlayback = true;
    video.srcObject = ms;
  } else {
    objectUrl = URL.createObjectURL(ms);
    video.src = objectUrl;
  }

  const fail = () => {
    if (closed) return;
    onState('failed');
    stop();
  };

  const pump = () => {
    if (!sb || sb.updating || closed) return;
    const buf = sb.buffered;
    // Trim what is well behind the play head so memory and latency stay small.
    if (buf.length && video.currentTime - buf.start(0) > KEEP_SECONDS + 2) {
      try {
        sb.remove(buf.start(0), video.currentTime - KEEP_SECONDS);
        return;
      } catch {
        /* try again after the next append */
      }
    }
    if (!queue.length) return;
    try {
      sb.appendBuffer(queue.shift());
    } catch {
      fail();
    }
  };

  const onUpdateEnd = () => {
    if (closed) return;
    const buf = sb.buffered;
    if (buf.length) {
      const end = buf.end(buf.length - 1);
      // Stay near the live edge.
      if (end - video.currentTime > MAX_LAG) video.currentTime = Math.max(buf.start(buf.length - 1), end - 0.3);
      if (video.paused) video.play().catch(() => {});
    }
    pump();
  };

  ms.addEventListener('sourceopen', () => {
    if (closed) return;
    try {
      ws = new WebSocket(wsUrl(url));
    } catch {
      fail();
      return;
    }
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => ws.send(JSON.stringify({ type: 'mse', value: supportedCodecs(MS) }));
    ws.onmessage = (ev) => {
      if (closed) return;
      if (typeof ev.data === 'string') {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === 'mse' && !sb) {
          // No video track we can use (audio only): show the still instead.
          if (!/avc1|hvc1|hev1/.test(String(msg.value))) {
            fail();
            return;
          }
          hevc = /hvc1|hev1/.test(String(msg.value));
          try {
            sb = ms.addSourceBuffer(msg.value);
            sb.mode = 'segments';
            sb.addEventListener('updateend', onUpdateEnd);
          } catch {
            fail();
          }
        } else if (msg.type === 'error') {
          fail();
        }
        return;
      }
      if (!sb) return;
      queue.push(ev.data);
      if (queue.length > 60) queue.splice(0, queue.length - 60); // never let a stalled tab hoard memory
      pump();
    };
    ws.onerror = fail;
    ws.onclose = () => {
      if (!closed) fail();
    };
  });

  let hevc = false;
  let watchdog = null;
  const onPlaying = () => {
    if (closed) return;
    onState('playing');
    // Time moves but no picture ever decodes: this browser can't really play H.265.
    clearTimeout(watchdog);
    watchdog = setTimeout(() => {
      if (closed || video.videoWidth > 0) return;
      if (hevc && !hevcBroken) {
        hevcBroken = true;
        onState('retry');
        stop();
      } else fail();
    }, 3500);
  };
  video.addEventListener('playing', onPlaying);
  onState('loading');

  function stop() {
    if (closed) return;
    closed = true;
    clearTimeout(watchdog);
    video.removeEventListener('playing', onPlaying);
    if (ws) {
      ws.onclose = null;
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    }
    try {
      if (ms.readyState === 'open') ms.endOfStream();
    } catch {
      /* ignore */
    }
    try {
      video.pause();
      video.removeAttribute('src');
      video.srcObject = null;
      video.load();
    } catch {
      /* ignore */
    }
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }
  return stop;
}

/**
 * Live video of a device (muted, autoplay, inline, no controls). `quality` "sub" for tiles,
 * "main" for the full-screen view (`priority` skips the tile limit). Falls back to the still.
 */
export default function LiveVideo({ entity, quality = 'sub', priority = false, className = '', stillHeight = 360, showNote = true }) {
  const box = useRef(null);
  const video = useRef(null);
  const onScreen = useOnScreen(box);
  const liveUrl = entity && entity.live;
  // Changes when what the hub plays for it changes (a stream picked, a "Video from" link): reconnect.
  const videoKey = (entity && entity.video && entity.video.key) || '';
  const canLive = !!liveUrl && canPlayLive();
  const slot = useStreamSlot(canLive && onScreen, priority);
  const [state, setState] = useState('idle');
  const [failedAt, setFailedAt] = useState(0);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!slot || !video.current || !liveUrl) return undefined;
    // After a failure, wait a little before trying again.
    const wait = failedAt ? Math.max(0, 15000 - (Date.now() - failedAt)) : 0;
    let stop = () => {};
    const t = setTimeout(() => {
      const sep = liveUrl.includes('?') ? '&' : '?';
      stop = startMse(video.current, `${liveUrl}${sep}quality=${quality}`, (s) => {
        if (s === 'retry') {
          // Ask again without H.265 straight away.
          setState('loading');
          setAttempt((n) => n + 1);
          return;
        }
        setState(s);
        if (s === 'failed') setFailedAt(Date.now());
      });
    }, wait);
    return () => {
      clearTimeout(t);
      stop();
    };
  }, [slot, liveUrl, quality, failedAt, attempt, videoKey]);

  const playing = state === 'playing';
  const failed = !canLive || state === 'failed';
  const offline = entity && (entity.available === false || entity.state === 'offline');
  return (
    <div ref={box} className={'live-video ' + className}>
      {canLive ? <video ref={video} className={'live-video-el' + (playing ? ' is-playing' : '')} muted autoPlay playsInline disablePictureInPicture aria-label={`Live video from ${entity.name}`} /> : null}
      {!playing ? (
        hasStill(entity) ? (
          <CameraStill entity={entity} h={stillHeight} interval={failed ? 5000 : 60000} className="live-video-still" />
        ) : (
          <div className="cam-still-empty">{offline ? 'Camera offline' : failed ? 'No picture right now' : 'Connecting…'}</div>
        )
      ) : null}
      {failed && canLive && showNote && !offline ? <span className="live-video-note">Live video unavailable</span> : null}
    </div>
  );
}
