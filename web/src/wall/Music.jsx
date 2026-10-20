// Music on the wall tablet (the Navidrome connection). The hub keeps this tablet's queue; this page plays
// it through the tablet's own speaker:
//   MusicEngine   an <audio> element fed by api/music/stream/<id> (the hub adds Navidrome's token). It
//                 follows the hub's {type: "music"} pushes (play / pause / next / volume, from the screen,
//                 voice or an automation) and reports back position, paused and ended - so the hub moves
//                 on to the next song and the music player device stays true. Quiet (ducked) while
//                 Newman listens or speaks, a timer rings or a wall alert sounds (music.js duck()).
//   NowPlayingChip in the status chips (the top bar on other pages) while music is on: cover, song,
//                 play/pause - not while a Music tile is on screen (it shows the same). Tap: the sheet.
//   PlayerSheet   the player over the page: cover, progress, controls, volume, up next, and the
//                 lyrics (api/music/lyrics - synced lines follow the song). Also "show the lyrics".
//   MusicTile     the dashboard's Music tile: what's playing, a search box, a few recent albums or
//                 playlists; More opens MusicPicker (full screen: recent, artists A-Z, albums A-Z,
//                 playlists, search - the keyboard's Search key shows all the results). An artist: Shuffle
//                 (all their songs) and their albums. An album or playlist: Play, Shuffle and its songs
//                 (a song plays it from there). The tile's covers and results open those pages too.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useDebounced } from '../hooks.js';
import { useLive } from '../live.js';
import { Icon } from '../icons.jsx';
import { closeSheet, duck, fmtTime, musicUrl, openSheet, setPlayer, setSheetLyrics, useDuck, useMusic, useSheet, useTileShown, useTilesShown } from './music.js';
import './music.css';

const DUCK_LEVEL = 0.12; // share of the volume left while ducked
let engineAudio = null; // the one <audio> (for the progress bars)

async function send(player, command, value) {
  if (!player) return;
  try {
    await api.post('api/entities/' + enc(player.entityId) + '/command', value === undefined ? { command } : { command, value }, { quiet: true });
  } catch (e) {
    toast(e.message || "The music didn't do that", 'error');
  }
}

async function playItem(body) {
  try {
    const r = await api.post('api/music/play', body, { quiet: true });
    if (r && r.player) setPlayer(r.player);
    return true;
  } catch (e) {
    toast(e.message || "Couldn't play that", 'error');
    return false;
  }
}

/* ---------------- the player itself ---------------- */

export function MusicEngine() {
  const { player } = useMusic();
  const ducked = useDuck();
  const audio = useRef(null);
  const loadedId = useRef('');
  const pendingSeek = useRef(null);
  const lastReport = useRef(0);
  const pRef = useRef(player);
  pRef.current = player;

  const report = useCallback(async (body) => {
    try {
      const p = await api.post('api/music/report', body, { quiet: true });
      if (p && p.entityId) setPlayer(p);
    } catch {
      /* offline for a moment: the next report catches up */
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const r = await api.get('api/music', { quiet: true });
      setPlayer(r.player || null, { available: !!r.available });
    } catch {
      setPlayer(null, { available: false });
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  useLive((m) => {
    if (m.type === 'music' && m.player) {
      if (typeof m.seek === 'number') pendingSeek.current = m.seek;
      setPlayer(m.player);
    }
    if (m.type === 'reconnected') load();
  });

  useEffect(() => {
    const a = new Audio();
    a.preload = 'auto';
    audio.current = a;
    engineAudio = a;
    const track = () => pRef.current && pRef.current.track;
    const onEnded = () => {
      const t = track();
      if (t) report({ state: 'ended', trackId: t.id, position: Math.round(a.currentTime) });
    };
    const onError = () => {
      const t = track();
      if (t && a.getAttribute('src')) report({ state: 'error', trackId: t.id });
    };
    const onTime = () => {
      const t = track();
      const now = Date.now();
      if (t && now - lastReport.current > 15000) {
        lastReport.current = now;
        report({ state: a.paused ? 'paused' : 'playing', trackId: t.id, position: Math.round(a.currentTime) });
      }
    };
    a.addEventListener('ended', onEnded);
    a.addEventListener('error', onError);
    a.addEventListener('timeupdate', onTime);
    // "Still here" once a minute, so the player shows as online.
    const beat = setInterval(() => {
      const t = track();
      report(t ? { trackId: t.id, position: Math.round(a.currentTime) } : {});
    }, 60000);
    return () => {
      clearInterval(beat);
      a.removeEventListener('ended', onEnded);
      a.removeEventListener('error', onError);
      a.removeEventListener('timeupdate', onTime);
      a.pause();
      a.removeAttribute('src');
      engineAudio = null;
    };
  }, [report]);

  // Follow the hub: the song, playing or paused, a jump.
  useEffect(() => {
    const a = audio.current;
    if (!a) return;
    const t = player && player.track;
    if (!t || player.state === 'idle') {
      a.pause();
      if (loadedId.current) {
        a.removeAttribute('src');
        a.load();
        loadedId.current = '';
      }
      return;
    }
    if (loadedId.current !== t.id) {
      loadedId.current = t.id;
      const start = pendingSeek.current ?? player.position ?? 0;
      pendingSeek.current = null;
      a.src = musicUrl(t.stream);
      if (start > 0) a.addEventListener('loadedmetadata', () => (a.currentTime = start), { once: true });
    } else if (pendingSeek.current !== null) {
      try {
        a.currentTime = pendingSeek.current;
      } catch {
        /* not seekable yet */
      }
      pendingSeek.current = null;
    }
    if (player.state === 'playing') {
      if (a.paused) a.play().catch(() => {});
    } else if (!a.paused) a.pause();
  }, [player]);

  // Volume, faded; much lower while ducked.
  const vol = player ? player.volume : 60;
  const isDucked = ducked.size > 0;
  useEffect(() => {
    const a = audio.current;
    if (!a) return undefined;
    const target = Math.max(0, Math.min(1, (vol ?? 60) / 100)) * (isDucked ? DUCK_LEVEL : 1);
    const from = a.volume;
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      a.volume = Math.max(0, Math.min(1, from + (target - from) * (i / 8)));
      if (i >= 8) clearInterval(id);
    }, 40);
    return () => clearInterval(id);
  }, [vol, isDucked]);

  return <PlayerSheet />;
}

/** The engine's playing position (s), once a second. */
function useProgress(player) {
  const now = () => (engineAudio && !Number.isNaN(engineAudio.currentTime) ? engineAudio.currentTime : (player && player.position) || 0);
  const [pos, setPos] = useState(now);
  useEffect(() => {
    setPos(now());
    const t = setInterval(() => setPos(now()), 1000);
    return () => clearInterval(t);
  }, [player]);
  return pos;
}

/* ---------------- pieces ---------------- */

function Cover({ src, size = 300, className = '' }) {
  const [bad, setBad] = useState(false);
  useEffect(() => setBad(false), [src]);
  return (
    <span className={'mu-cover ' + className}>
      {src && !bad ? <img src={musicUrl(src, { size })} alt="" loading="lazy" onError={() => setBad(true)} /> : <Icon name="music" size={28} />}
    </span>
  );
}

function Controls({ player, big }) {
  const playing = player.state === 'playing';
  const [v, setV] = useState(player.volume);
  const dragging = useRef(false);
  useEffect(() => {
    if (!dragging.current) setV(player.volume);
  }, [player.volume]);
  const commit = () => {
    dragging.current = false;
    send(player, 'set_volume', v);
  };
  return (
    <div className={'mu-controls' + (big ? ' is-big' : '')}>
      {big ? (
        <button type="button" className="mu-btn" onClick={() => send(player, 'previous')} aria-label="Previous song">
          <Icon name="skipBack" size={22} strokeWidth={2.2} />
        </button>
      ) : null}
      <button type="button" className="mu-btn mu-btn--main" onClick={() => send(player, playing ? 'pause' : 'play')} aria-label={playing ? 'Pause' : 'Play'}>
        <Icon name={playing ? 'pause' : 'play'} size={big ? 28 : 24} strokeWidth={2.6} />
      </button>
      <button type="button" className="mu-btn" onClick={() => send(player, 'next')} aria-label="Next song">
        <Icon name="skipNext" size={22} strokeWidth={2.2} />
      </button>
      <label className="mu-vol">
        <Icon name="volume" size={18} />
        <input
          type="range"
          min="0"
          max="100"
          value={v}
          aria-label="Volume"
          onChange={(e) => {
            dragging.current = true;
            setV(Number(e.target.value));
          }}
          onPointerUp={commit}
          onKeyUp={commit}
          onTouchEnd={commit}
        />
      </label>
    </div>
  );
}

function Progress({ player }) {
  const pos = useProgress(player);
  const dur = (player.track && player.track.duration) || 0;
  return (
    <span className="mu-progress" aria-hidden="true">
      <span style={{ transform: `scaleX(${dur ? Math.min(1, pos / dur).toFixed(4) : 0})` }} />
    </span>
  );
}

/* ---------------- the now-playing chip and the player sheet ---------------- */

const SHEET_IDLE_MS = 60000; // the sheet closes itself after this long untouched
const CHIP_PAUSED_MS = 60000; // a paused song keeps its chip this long (to tap play again), then it goes

/** True once the player has sat paused for CHIP_PAUSED_MS - timed from the hub's `pausedAt`, so a screen
 * opened (or redrawn) later still knows how long it has been. */
function usePausedLong(player) {
  const since = player && player.state === 'paused' ? (player.pausedAt ? player.pausedAt * 1000 : Date.now()) : null;
  const [, tick] = useState(0);
  const left = since === null ? null : since + CHIP_PAUSED_MS - Date.now();
  useEffect(() => {
    if (left === null || left <= 0) return undefined;
    const t = setTimeout(() => tick((n) => n + 1), left + 50);
    return () => clearTimeout(t);
  }, [since, left === null || left <= 0]);
  return left !== null && left <= 0;
}

/** In the status chips (or the top bar on other pages) while music plays, and for a minute after a
 * pause - not while a Music tile is on screen (it shows the same). Tap: the player sheet; its round
 * button plays / pauses in place. */
export function NowPlayingChip({ className = '' }) {
  const { player } = useMusic();
  const tiles = useTilesShown();
  const t = player && player.track;
  const pausedLong = usePausedLong(player);
  if (!t || player.state === 'idle' || pausedLong || tiles > 0) return null;
  const playing = player.state === 'playing';
  const open = () => openSheet();
  return (
    <div
      className={'wt-chip mu-chip ' + className}
      role="button"
      tabIndex={0}
      aria-label={`Now playing ${t.title}${t.artist ? ' by ' + t.artist : ''} - open the player`}
      onClick={open}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && open()}
    >
      <Cover src={t.cover} size={96} className="mu-cover--chip" />
      <Marquee className="mu-chip-text" text={t.title + (t.artist ? ' · ' + t.artist : '')} />
      <button
        type="button"
        className="mu-chip-pp"
        aria-label={playing ? 'Pause' : 'Play'}
        onClick={(e) => {
          e.stopPropagation();
          send(player, playing ? 'pause' : 'play');
        }}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <Icon name={playing ? 'pause' : 'play'} size={14} strokeWidth={2.8} />
      </button>
    </div>
  );
}

function TimeBar({ player }) {
  const pos = useProgress(player);
  const dur = (player.track && player.track.duration) || 0;
  return (
    <div className="mu-time">
      <span>{fmtTime(Math.min(pos, dur || pos))}</span>
      <span className="mu-time-bar" aria-hidden="true">
        <span style={{ transform: `scaleX(${dur ? Math.min(1, pos / dur).toFixed(4) : 0})` }} />
      </span>
      <span>{dur ? fmtTime(dur) : ''}</span>
    </div>
  );
}

/** The engine's position, every `ms` while `on` (the lyrics follow it). */
function useClock(on, ms = 250) {
  const [pos, setPos] = useState(0);
  useEffect(() => {
    if (!on) return undefined;
    const tick = () => setPos(engineAudio && !Number.isNaN(engineAudio.currentTime) ? engineAudio.currentTime : 0);
    tick();
    const id = setInterval(tick, ms);
    return () => clearInterval(id);
  }, [on, ms]);
  return pos;
}

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/** The song's words (api/music/lyrics): synced lines follow the song, the current one lit and kept in the
 * middle; plain lyrics scroll by hand. A touch or scroll holds the following for a few seconds. */
function Lyrics({ track }) {
  const [got, setGot] = useState(null);
  useEffect(() => {
    let live = true;
    setGot(null);
    api.get('api/music/lyrics/' + enc(track.id), { quiet: true }).then(
      (r) => live && setGot(r || { source: null }),
      () => live && setGot({ error: true }),
    );
    return () => {
      live = false;
    };
  }, [track.id]);
  const lines = got && got.synced && Array.isArray(got.lines) && got.lines.length ? got.lines : null;
  const pos = useClock(!!lines);
  const box = useRef(null);
  const held = useRef(0);
  let cur = -1;
  if (lines) {
    for (let i = 0; i < lines.length; i += 1) {
      if (lines[i].t <= pos + 0.2) cur = i;
      else break;
    }
  }
  useEffect(() => {
    const el = box.current;
    if (!lines || !el || Date.now() < held.current) return;
    const line = el.querySelector(`[data-i="${Math.max(0, cur)}"]`);
    if (!line) return;
    el.scrollTo({ top: line.offsetTop - el.clientHeight / 2 + line.offsetHeight / 2, behavior: reducedMotion() ? 'auto' : 'smooth' });
  }, [cur, lines]);
  if (!got) return <div className="mu-lyrics mu-lyrics--msg">Finding the lyrics…</div>;
  if (got.error) return <div className="mu-lyrics mu-lyrics--msg">Couldn't get the lyrics just now.</div>;
  if (!got.source || (!lines && !got.text)) return <div className="mu-lyrics mu-lyrics--msg">No lyrics found for this song</div>;
  const hold = () => (held.current = Date.now() + 6000);
  return (
    <div className="mu-lyrics-wrap">
      <div className={'mu-lyrics ' + (lines ? 'is-synced' : 'is-plain')} ref={box} onPointerDown={hold} onWheel={hold} onTouchMove={hold}>
        {lines
          ? lines.map((l, i) => (
              <p key={i} data-i={i} className={'mu-line' + (i === cur ? ' is-now' : i < cur ? ' is-past' : '')} aria-current={i === cur ? 'true' : undefined}>
                {l.text || '♪'}
              </p>
            ))
          : got.text}
      </div>
      {got.source === 'lrclib' ? <span className="mu-lyrics-src">Lyrics from LRCLIB</span> : null}
    </div>
  );
}

/** The player: a glass card over the page - cover, song, progress, controls, volume, what's next and
 * the lyrics. Tap outside (or ×) to close; it closes itself after a minute untouched (not while the
 * lyrics follow a playing song). A dialog, so the wall doesn't go back to its home page meanwhile. */
function PlayerSheet() {
  const { player } = useMusic();
  const sheet = useSheet();
  const last = useRef(Date.now());
  const t = player && player.track && player.state !== 'idle' ? player.track : null;
  const open = sheet.open && !!t;
  const following = open && sheet.lyrics && player.state === 'playing';
  useEffect(() => {
    last.current = Date.now();
  }, [sheet.open, sheet.lyrics, following]);
  useEffect(() => {
    if (!open) return undefined;
    const id = setInterval(() => {
      if (following) last.current = Date.now();
      else if (Date.now() - last.current > SHEET_IDLE_MS) closeSheet();
    }, 1000);
    return () => clearInterval(id);
  }, [open, following]);
  useEffect(() => {
    if (sheet.open && !t) closeSheet(); // the music stopped
  }, [sheet.open, t]);
  if (!open) return null;
  const touch = () => {
    last.current = Date.now();
  };
  const next = player.upNext || [];
  return (
    <div
      className="mu-scrim"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) closeSheet();
      }}
    >
      <div className={'mu-sheet' + (sheet.lyrics ? ' is-lyrics' : '')} role="dialog" aria-modal="true" aria-label="Now playing" onPointerDownCapture={touch} onKeyDownCapture={touch}>
        <div className="mu-sheet-head">
          <Cover src={t.cover} size={sheet.lyrics ? 160 : 400} className={sheet.lyrics ? '' : 'mu-cover--sheet'} />
          <span className="mu-text">
            <Marquee className="mu-title mu-title--sheet" text={t.title} />
            <Marquee className="mu-artist" text={[t.artist, t.album].filter(Boolean).join(' · ')} />
          </span>
          <button type="button" className="mu-btn mu-sheet-close" aria-label="Close" onClick={closeSheet}>
            <Icon name="x" size={20} strokeWidth={2.4} />
          </button>
        </div>
        {sheet.lyrics ? <Lyrics track={t} /> : null}
        <TimeBar player={player} />
        <div className="mu-sheet-row">
          <Controls player={player} big />
          <button type="button" className={'wt-pill mu-lyrics-btn' + (sheet.lyrics ? ' is-on' : '')} aria-pressed={sheet.lyrics} onClick={() => setSheetLyrics(!sheet.lyrics)}>
            <Icon name="list" size={18} /> Lyrics
          </button>
        </div>
        {next.length ? (
          <div className="mu-next">
            Up next: <b>{next[0].title}</b>
            {next[0].artist ? ' (' + next[0].artist + ')' : ''}
            {next.slice(1).map((n) => ' · ' + n.title).join('')}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* ---------------- the Music tile ---------------- */

/** Text that fits its box; when it's too long it scrolls slowly from end to end and back (car-stereo
 * style) instead of being cut off. Still for people who ask for less motion. */
export function Marquee({ text, className = '' }) {
  const box = useRef(null);
  const [shift, setShift] = useState(0);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const measure = () => {
      const inner = el.firstChild;
      const over = inner ? Math.ceil(inner.scrollWidth - el.clientWidth) : 0;
      setShift((s) => (Math.abs(s - Math.max(0, over)) > 1 ? Math.max(0, over) : s));
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text]);
  const style = shift ? { '--mq-shift': `-${shift}px`, '--mq-time': `${Math.max(6, shift / 25 + 4)}s` } : undefined;
  return (
    <span ref={box} className={'mu-marquee ' + (shift ? 'is-long ' : '') + className} title={text}>
      <span className="mu-marquee-in" style={style}>{text}</span>
    </span>
  );
}

function Card({ item, sub, onPick }) {
  return (
    <button type="button" className="mu-card" onClick={onPick}>
      <Cover src={item.cover} size={240} />
      <Marquee className="mu-card-name" text={item.name || item.title} />
      {sub ? <Marquee className="mu-card-sub" text={sub} /> : null}
    </button>
  );
}

/** Search results as rows: an artist, album or playlist opens its page (`onOpen({kind, item})`); a song
 * plays (`onPlayed` after). */
function Results({ data, limit, onOpen, onPlayed }) {
  const rows = [
    ...(data.artists || []).slice(0, 2).map((a) => ({ key: 'ar' + a.id, round: true, cover: a.cover, name: a.name, sub: 'Artist', open: { kind: 'artist', item: a } })),
    ...(data.playlists || []).slice(0, 2).map((p) => ({ key: 'pl' + p.id, cover: p.cover, name: p.name, sub: 'Playlist', open: { kind: 'playlist', item: p } })),
    ...(data.albums || []).slice(0, 3).map((a) => ({ key: 'al' + a.id, cover: a.cover, name: a.name, sub: 'Album · ' + a.artist, open: { kind: 'album', item: a } })),
    ...(data.songs || []).slice(0, 4).map((s) => ({ key: 's' + s.id, cover: s.cover, name: s.title, sub: s.artist, body: { songId: s.id } })),
  ].slice(0, limit);
  if (!rows.length) return <div className="wt-muted mu-none">Nothing matches.</div>;
  return (
    <div className="mu-results">
      {rows.map((r) => (
        <button key={r.key} type="button" className="mu-row" onClick={async () => (r.open ? onOpen(r.open) : (await playItem(r.body)) && onPlayed && onPlayed())}>
          <Cover src={r.cover} size={96} className={'mu-cover--sm' + (r.round ? ' mu-cover--round' : '')} />
          <span className="mu-text">
            <Marquee className="mu-title" text={r.name} />
            <Marquee className="mu-artist" text={r.sub} />
          </span>
        </button>
      ))}
    </div>
  );
}

/** Rows of square cards (plus two lines of text) that fit in `ref`, `cols` across - smaller cards (more
 * across) when not even one row would fit. -> [cols, rows] */
function useCardFit(ref, cols) {
  const [fit, setFit] = useState([cols, 1]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => {
      const gap = 12;
      let c = cols;
      let rows = 0;
      for (; c <= 6; c += 1) {
        rows = Math.floor((el.clientHeight + gap) / ((el.clientWidth - gap * (c - 1)) / c + 44 + gap));
        if (rows >= 1) break;
      }
      const next = [Math.min(c, 6), Math.max(1, rows)];
      setFit((f) => (f[0] === next[0] && f[1] === next[1] ? f : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  });
  return fit;
}

export function MusicTile({ tile }) {
  useTileShown();
  const { player, available, loaded } = useMusic();
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim(), 450);
  const browse = useApi(available ? 'api/music/browse' : null);
  const found = useApi(available && dq ? 'api/music/search?q=' + enc(dq) : null);
  const [picker, setPicker] = useState(null); // null, or what MusicPicker opens on: {} | {open} | {query}
  const body = useRef(null);
  const [fitCols, fitRows] = useCardFit(body, tile.w >= 12 ? 4 : 3);
  // Outside the wall (the dashboard editor's preview) there's no engine to ask the hub.
  useEffect(() => {
    if (loaded) return;
    api.get('api/music', { quiet: true }).then((r) => setPlayer(r.player || null, { available: !!r.available }), () => setPlayer(null, { available: false }));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!loaded) return <div className="wt-muted">Loading…</div>;
  if (!available) {
    return (
      <div className="mu-tile mu-tile--off">
        <Icon name="music" size={32} />
        <span className="wt-muted">Music isn't connected.</span>
      </div>
    );
  }
  const t = player && player.track && player.state !== 'idle' ? player.track : null;
  const show = tile.config.show === 'playlists' ? 'playlists' : 'albums';
  const cols = fitCols;
  const rows = Math.min(2, fitRows);
  const items = ((browse.data && browse.data[show]) || []).slice(0, cols * rows);
  return (
    <div className="mu-tile">
      {t ? (
        <div className="mu-now">
          <button type="button" className="mu-now-open" onClick={() => openSheet()} aria-label="Open the player">
            <Cover src={t.cover} size={240} className="mu-cover--now" />
          </button>
          <div className="mu-now-side">
            <span className="mu-text">
              <Marquee className="mu-title mu-title--big" text={t.title} />
              <Marquee className="mu-artist" text={(t.artist || '') + (t.album ? ' · ' + t.album : '')} />
            </span>
            <Controls player={player} big />
          </div>
          <Progress player={player} />
        </div>
      ) : null}
      <form
        className="mu-search"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          if (!q.trim()) return;
          e.currentTarget.querySelector('input').blur(); // the keyboard goes away
          setPicker({ query: q.trim() });
        }}
      >
        <Icon name="search" size={18} />
        <input type="search" inputMode="search" enterKeyHint="search" placeholder="Search songs, artists, albums" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search music" />
        {q ? (
          <button type="button" className="mu-clear" onClick={() => setQ('')} aria-label="Clear">
            <Icon name="x" size={16} strokeWidth={2.4} />
          </button>
        ) : null}
      </form>
      <div className="mu-body" ref={body}>
        {dq ? (
          found.data ? (
            <Results data={found.data} limit={t ? 3 : 5} onOpen={(open) => setPicker({ open })} onPlayed={() => setQ('')} />
          ) : (
            <div className="wt-muted mu-none">Searching…</div>
          )
        ) : (
          <div className="mu-grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
            {items.map((it) => (
              <Card key={it.id} item={it} sub={show === 'albums' ? it.artist : null} onPick={() => setPicker({ open: { kind: show === 'albums' ? 'album' : 'playlist', item: it } })} />
            ))}
          </div>
        )}
      </div>
      <div className="mu-foot">
        <span className="wt-dim-text">{dq ? '' : show === 'albums' ? 'Recent albums' : 'Playlists'}</span>
        <button type="button" className="wt-pill" onClick={() => setPicker({})}>
          More
        </button>
      </div>
      {picker ? <MusicPicker start={show} open={picker.open} query={picker.query} onClose={() => setPicker(null)} /> : null}
    </div>
  );
}

/* ---------------- the full-screen picker ---------------- */

function AllAlbums({ onOpen }) {
  const [list, setList] = useState([]);
  const [more, setMore] = useState(true);
  const [busy, setBusy] = useState(false);
  const next = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.get('api/music/albums?offset=' + list.length, { quiet: true });
      setList((l) => [...l, ...(r.albums || [])]);
      setMore(!!r.more);
    } catch {
      setMore(false);
    } finally {
      setBusy(false);
    }
  }, [list.length]);
  useEffect(() => {
    next();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <div className="mu-grid mu-grid--full">
        {list.map((a) => (
          <Card key={a.id} item={a} sub={a.artist} onPick={() => onOpen({ kind: 'album', item: a })} />
        ))}
      </div>
      {more ? (
        <button type="button" className="wt-pill mu-loadmore" disabled={busy} onClick={next}>
          {busy ? 'Loading…' : 'More albums'}
        </button>
      ) : null}
    </>
  );
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** "42 min", "1 hr 5 min" - an album's or playlist's length. */
function fmtLength(s) {
  const m = Math.round((Number(s) || 0) / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} hr${m % 60 ? ` ${m % 60} min` : ''}`;
}

/** Every artist A-Z in Navidrome's letter groups, with a letter strip to jump (and drag) along when
 * the list is long. Scrolls itself; `scroll` keeps the spot while an artist is open. */
function AllArtists({ index, scroll, onOpen }) {
  const list = useRef(null);
  const strip = useRef(null);
  const total = index.reduce((n, g) => n + g.artists.length, 0);
  useLayoutEffect(() => {
    if (list.current) list.current.scrollTop = scroll.current;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const jump = (letter) => {
    const el = list.current && list.current.querySelector(`[data-letter="${CSS.escape(letter)}"]`);
    if (el) list.current.scrollTop = el.offsetTop;
  };
  const drag = (e) => {
    const at = document.elementFromPoint(e.clientX, e.clientY);
    const letter = at && strip.current && strip.current.contains(at) ? at.getAttribute('data-jump') : null;
    if (letter) jump(letter);
  };
  if (!total) return <div className="wt-muted">No artists.</div>;
  return (
    <div className="mu-az">
      <div className="mu-az-list" ref={list} onScroll={(e) => (scroll.current = e.currentTarget.scrollTop)}>
        {index.map((g) => (
          <section key={g.letter} data-letter={g.letter} className="mu-az-group">
            <h3 className="mu-az-letter">{g.letter}</h3>
            <div className="mu-az-rows">
              {g.artists.map((a) => (
                <ArtistRow key={a.id} artist={a} onOpen={onOpen} />
              ))}
            </div>
          </section>
        ))}
      </div>
      {total > 24 && index.length > 1 ? (
        <div
          className="mu-az-strip"
          ref={strip}
          role="navigation"
          aria-label="Jump to a letter"
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            drag(e);
          }}
          onPointerMove={(e) => e.buttons && drag(e)}
        >
          {index.map((g) => (
            <button key={g.letter} type="button" data-jump={g.letter} aria-label={'Jump to ' + g.letter} onClick={() => jump(g.letter)}>
              {g.letter}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ArtistRow({ artist: a, onOpen }) {
  return (
    <button type="button" className="mu-row" onClick={() => onOpen({ kind: 'artist', item: a })}>
      <Cover src={a.cover} size={96} className="mu-cover--sm mu-cover--round" />
      <span className="mu-text">
        <Marquee className="mu-title" text={a.name} />
        {a.albums != null ? <Marquee className="mu-artist" text={plural(a.albums, 'album')} /> : null}
      </span>
    </button>
  );
}

/** One artist: Shuffle (all their songs) and their albums (each opens its page). */
function ArtistPage({ artist, onPlay, onOpen }) {
  const got = useApi('api/music/artists/' + enc(artist.id));
  const albums = got.data ? got.data.albums : null;
  const count = albums ? albums.length : artist.albums;
  return (
    <div className="mu-artist-page">
      <div className="mu-artist-head">
        <Cover src={artist.cover} size={240} className="mu-cover--artist mu-cover--round" />
        <span className="mu-text">
          <Marquee className="mu-title mu-title--artist" text={artist.name} />
          {count != null ? <Marquee className="mu-artist" text={plural(count, 'album')} /> : null}
        </span>
        <button type="button" className="wt-pill mu-shuffle" onClick={() => onPlay({ artistId: artist.id })}>
          <Icon name="shuffle" size={18} strokeWidth={2.4} /> Shuffle
        </button>
      </div>
      {albums ? (
        <div className="mu-grid mu-grid--full">
          {albums.map((a) => (
            <Card key={a.id} item={a} sub={a.year || null} onPick={() => onOpen({ kind: 'album', item: { ...a, artist: a.artist || artist.name } })} />
          ))}
        </div>
      ) : got.error ? (
        <div className="wt-muted">{got.error.message || "Navidrome didn't answer."}</div>
      ) : (
        <div className="wt-muted">Loading…</div>
      )}
    </div>
  );
}

/** An album's (or a playlist's) page: cover, name, artist, year, songs and length, Play and Shuffle, then
 * the numbered songs - a song plays the album from there (the rest of it after). */
function TrackListPage({ kind, item, onPlay }) {
  const album = kind === 'album';
  const got = useApi(`api/music/${album ? 'albums' : 'playlists'}/${enc(item.id)}`);
  const { player } = useMusic();
  const about = (got.data && got.data[kind]) || item;
  const songs = got.data ? got.data.songs : null;
  const idKey = album ? 'albumId' : 'playlistId';
  const nowId = player && player.state !== 'idle' && player.track ? player.track.id : null;
  const count = songs ? songs.length : about.songs;
  const meta = [album && about.year ? String(about.year) : null, count != null ? plural(count, 'song') : null, got.data && about.duration ? fmtLength(about.duration) : null].filter(Boolean);
  return (
    <div className="mu-tracks-page">
      <div className="mu-tracks-head">
        <Cover src={about.cover || item.cover} size={400} className="mu-cover--album" />
        <div className="mu-tracks-about">
          <span className="mu-tracks-kind">{album ? 'Album' : 'Playlist'}</span>
          <Marquee className="mu-title mu-title--album" text={about.name} />
          {album && about.artist ? <Marquee className="mu-tracks-artist" text={about.artist} /> : null}
          {meta.length ? <Marquee className="mu-artist" text={meta.join(' · ')} /> : null}
          <div className="mu-tracks-btns">
            <button type="button" className="wt-pill mu-play-all" disabled={songs !== null && !songs.length} onClick={() => onPlay({ [idKey]: item.id })}>
              <Icon name="play" size={18} strokeWidth={2.6} /> Play
            </button>
            <button type="button" className="wt-pill mu-shuffle-all" disabled={songs !== null && !songs.length} onClick={() => onPlay({ [idKey]: item.id, shuffle: true })}>
              <Icon name="shuffle" size={18} strokeWidth={2.4} /> Shuffle
            </button>
          </div>
        </div>
      </div>
      {songs ? (
        songs.length ? (
          <ol className="mu-tracks">
            {songs.map((s, i) => (
              <li key={s.id + ':' + i}>
                <button type="button" className={'mu-track' + (s.id === nowId ? ' is-now' : '')} aria-current={s.id === nowId ? 'true' : undefined} onClick={() => onPlay({ [idKey]: item.id, startSongId: s.id })}>
                  <span className="mu-track-n">{s.id === nowId ? <Icon name="volume" size={16} /> : album && s.track ? s.track : i + 1}</span>
                  <span className="mu-text">
                    <Marquee className="mu-title" text={s.title} />
                    {!album || (s.artist && s.artist !== about.artist) ? <Marquee className="mu-artist" text={s.artist} /> : null}
                  </span>
                  <span className="mu-track-time">{s.duration ? fmtTime(s.duration) : ''}</span>
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <div className="wt-muted">No songs.</div>
        )
      ) : got.error ? (
        <div className="wt-muted">{got.error.message || "Navidrome didn't answer."}</div>
      ) : (
        <div className="wt-muted">Loading…</div>
      )}
    </div>
  );
}

/** The full results for a search (the keyboard's Search key): every artist, album, song and playlist. */
function FullResults({ q, onOpen, onPlay }) {
  const got = useApi('api/music/search?full=1&q=' + enc(q));
  if (got.error) return <div className="wt-muted">{got.error.message || "Navidrome didn't answer."}</div>;
  if (!got.data) return <div className="wt-muted">Searching…</div>;
  const { artists = [], albums = [], songs = [], playlists = [] } = got.data;
  if (!artists.length && !albums.length && !songs.length && !playlists.length) return <div className="wt-muted mu-none">Nothing matches “{q}”.</div>;
  return (
    <div className="mu-full">
      {artists.length ? (
        <section className="mu-full-sec">
          <h3 className="mu-full-h">Artists</h3>
          <div className="mu-az-rows">
            {artists.map((a) => (
              <ArtistRow key={a.id} artist={a} onOpen={onOpen} />
            ))}
          </div>
        </section>
      ) : null}
      {albums.length ? (
        <section className="mu-full-sec">
          <h3 className="mu-full-h">Albums</h3>
          <div className="mu-grid mu-grid--full">
            {albums.map((a) => (
              <Card key={a.id} item={a} sub={a.artist} onPick={() => onOpen({ kind: 'album', item: a })} />
            ))}
          </div>
        </section>
      ) : null}
      {songs.length ? (
        <section className="mu-full-sec">
          <h3 className="mu-full-h">Songs</h3>
          <div className="mu-full-songs">
            {songs.map((s) => (
              <button key={s.id} type="button" className="mu-row" onClick={() => onPlay({ songId: s.id })}>
                <Cover src={s.cover} size={96} className="mu-cover--sm" />
                <span className="mu-text">
                  <Marquee className="mu-title" text={s.title} />
                  <Marquee className="mu-artist" text={[s.artist, s.album].filter(Boolean).join(' · ')} />
                </span>
                <span className="mu-track-time">{s.duration ? fmtTime(s.duration) : ''}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {playlists.length ? (
        <section className="mu-full-sec">
          <h3 className="mu-full-h">Playlists</h3>
          <div className="mu-grid mu-grid--full">
            {playlists.map((p) => (
              <Card key={p.id} item={p} sub={p.songs ? plural(p.songs, 'song') : null} onPick={() => onOpen({ kind: 'playlist', item: p })} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

/** Full screen: tabs (recent, artists A-Z, albums A-Z, playlists) and search. An artist, album or playlist
 * opens its page over what was there; Back returns to it as it was (same tab, same place in the list).
 * `open`: start on one page ({kind, item} - Back from it closes); `query`: start on a search's full results. */
export function MusicPicker({ start, open, query, onClose }) {
  const [tab, setTab] = useState(start === 'playlists' ? 'playlists' : 'recent');
  const [stack, setStack] = useState(() => (open ? [{ ...open, closes: true }] : []));
  const artistScroll = useRef(0);
  const [q, setQ] = useState(query || '');
  const [submitted, setSubmitted] = useState(query ? query.trim() : '');
  const dq = useDebounced(q.trim(), 450);
  const live = dq && dq !== submitted ? dq : '';
  const searching = !!(submitted || live);
  const browse = useApi('api/music/browse');
  const artists = useApi(tab === 'artists' ? 'api/music/artists' : null);
  const found = useApi(live ? 'api/music/search?q=' + enc(live) : null);
  const layers = useRef([]);
  const saved = useRef([]);
  const restore = useRef(null);
  const input = useRef(null);

  const play = async (body) => {
    if (await playItem(body)) onClose();
  };
  const openPage = (page) => {
    const d = stack.length;
    const el = layers.current[d];
    const az = el && el.querySelector('.mu-az-list');
    saved.current[d] = el ? { top: el.scrollTop, az: az ? az.scrollTop : 0 } : null;
    setStack((s) => [...s, page]);
  };
  const back = () => {
    if (stack.length) {
      if (stack[stack.length - 1].closes) return onClose();
      restore.current = stack.length - 1;
      setStack((s) => s.slice(0, -1));
    } else if (q || submitted) {
      setQ('');
      setSubmitted('');
    } else onClose();
  };
  useLayoutEffect(() => {
    const d = restore.current;
    if (d === null) return;
    restore.current = null;
    const el = layers.current[d];
    const s = saved.current[d];
    if (el && s) {
      el.scrollTop = s.top;
      const az = el.querySelector('.mu-az-list');
      if (az) az.scrollTop = s.az;
    }
  }, [stack]);
  const choose = (id) => {
    setStack([]);
    setTab(id);
  };
  const type = (v) => {
    setQ(v);
    if (stack.length) setStack([]);
    if (v.trim() !== submitted) setSubmitted('');
  };
  const submit = (e) => {
    e.preventDefault();
    if (input.current) input.current.blur(); // the keyboard goes away
    setStack([]);
    setSubmitted(q.trim());
  };
  const b = browse.data || {};
  const az = tab === 'artists' && !searching;
  const top = stack.length;
  const base = submitted ? (
    <FullResults key={submitted} q={submitted} onOpen={openPage} onPlay={play} />
  ) : live ? (
    found.data ? (
      <Results data={found.data} limit={12} onOpen={openPage} onPlayed={onClose} />
    ) : (
      <div className="wt-muted">Searching…</div>
    )
  ) : tab === 'artists' ? (
    artists.data ? (
      <AllArtists index={artists.data.index || []} scroll={artistScroll} onOpen={openPage} />
    ) : artists.error ? (
      <div className="wt-muted">{artists.error.message || "Navidrome didn't answer."}</div>
    ) : (
      <div className="wt-muted">Loading…</div>
    )
  ) : tab === 'albums' ? (
    <AllAlbums onOpen={openPage} />
  ) : (
    <div className="mu-grid mu-grid--full">
      {(tab === 'recent' ? b.albums || [] : b.playlists || []).map((it) => (
        <Card key={it.id} item={it} sub={tab === 'recent' ? it.artist : it.songs ? plural(it.songs, 'song') : null} onPick={() => openPage({ kind: tab === 'recent' ? 'album' : 'playlist', item: it })} />
      ))}
    </div>
  );
  return (
    <div className="mu-picker" role="dialog" aria-modal="true" aria-label="Music">
      <div className="mu-picker-head">
        <button type="button" className="wt-back" onClick={back}>
          <Icon name="chevronLeft" size={26} strokeWidth={2.4} /> Back
        </button>
        <form className="mu-search mu-search--big" role="search" onSubmit={submit}>
          <Icon name="search" size={20} />
          <input
            ref={input}
            type="search"
            inputMode="search"
            enterKeyHint="search"
            placeholder="Search songs, artists, albums, playlists"
            value={q}
            onChange={(e) => type(e.target.value)}
            aria-label="Search music"
            autoFocus={false}
          />
          {q ? (
            <button type="button" className="mu-clear" onClick={() => type('')} aria-label="Clear">
              <Icon name="x" size={18} strokeWidth={2.4} />
            </button>
          ) : null}
        </form>
      </div>
      {!searching && !(top && stack[0].closes) ? (
        <div className="mu-tabs" role="tablist">
          {[
            ['recent', 'Recent'],
            ['artists', 'Artists'],
            ['albums', 'Albums'],
            ['playlists', 'Playlists'],
          ].map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} className={'wt-pill' + (tab === id ? ' is-on' : '')} onClick={() => choose(id)}>
              {label}
            </button>
          ))}
        </div>
      ) : null}
      {/* What was there stays (hidden) under an open page, so Back finds it as it was. */}
      <div className={'mu-picker-body' + (az ? ' mu-picker-body--az' : '')} ref={(el) => (layers.current[0] = el)} style={top ? { display: 'none' } : undefined}>
        {base}
      </div>
      {stack.map((p, i) => (
        <div key={i + ':' + p.kind + ':' + p.item.id} className="mu-picker-body" ref={(el) => (layers.current[i + 1] = el)} style={i + 1 < top ? { display: 'none' } : undefined}>
          {p.kind === 'artist' ? <ArtistPage artist={p.item} onPlay={play} onOpen={openPage} /> : <TrackListPage kind={p.kind} item={p.item} onPlay={play} />}
        </div>
      ))}
    </div>
  );
}

export { duck, fmtTime };
