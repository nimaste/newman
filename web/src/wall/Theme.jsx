// The wall's theme (theme.js decides, theme.css draws): the html element's data-wt-theme, switched live
// when the dashboard changes and at local midnight (the Christmas season starting or ending); and the
// Christmas extras - falling snow, the greeting and characters in the top bar's decor slot (or on the
// dimmed screen when the bar has no room), or the characters big and soft behind the tiles.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import '@fontsource/great-vibes/latin-400.css';
import '@fontsource/nunito/latin-600.css';
import '@fontsource/nunito/latin-700.css';
import '@fontsource/nunito/latin-800.css';
import '@fontsource/nunito/latin-900.css';
import './theme.css';
import { tabletToken } from '../api.js';
import { christmasOf, decorFit, msToMidnight, shellOf, themeNow } from './theme.js';

/** The look today for `dash`, kept on <html data-wt-theme> (so sheets outside the wall get it too). */
export function useWallTheme(dash) {
  const [day, setDay] = useState(() => new Date());
  useEffect(() => {
    let t = 0;
    const plan = () => {
      clearTimeout(t);
      t = setTimeout(() => (setDay(new Date()), plan()), msToMidnight());
    };
    plan();
    // a tablet that slept through midnight (timers paused) checks again when it wakes
    const wake = () => document.visibilityState === 'visible' && (setDay(new Date()), plan());
    document.addEventListener('visibilitychange', wake);
    return () => (clearTimeout(t), document.removeEventListener('visibilitychange', wake));
  }, []);
  const theme = dash ? themeNow(dash, day) : 'midnight';
  useEffect(() => {
    const el = document.documentElement;
    if (theme === 'midnight') el.removeAttribute('data-wt-theme');
    else el.setAttribute('data-wt-theme', theme);
  }, [theme]);
  useEffect(() => () => document.documentElement.removeAttribute('data-wt-theme'), []);
  // The wall apps paint their loading and "can't reach" screens in the page's colour: tell them (once the
  // dashboard is known, and again when their bridge arrives after the page's scripts).
  const known = !!dash;
  useEffect(() => {
    if (!known) return undefined;
    const report = () => reportShell(theme);
    report();
    window.addEventListener('newmanwall', report);
    return () => window.removeEventListener('newmanwall', report);
  }, [theme, known]);
  return { theme, xmas: christmasOf(dash) };
}

/** NewmanWall.setTheme({background, scheme}) inside the wall apps; nothing in a browser. */
export function reportShell(theme) {
  const b = typeof window !== 'undefined' ? window.NewmanWall : null;
  if (!b || typeof b.setTheme !== 'function') return;
  try {
    b.setTheme({ ...shellOf(theme) });
  } catch {
    /* an older app */
  }
}

/* ---------------- the characters ---------------- */

// Original drawings (not anyone's film characters): Santa, a red-nosed reindeer and an elf.
const SANTA = `<svg viewBox="0 0 120 140" role="img" aria-label="Santa">
  <path d="M22 62 Q60 2 98 50 L104 58 Q60 40 22 62Z" fill="#d93a4c"/>
  <circle cx="104" cy="58" r="9" fill="#fff"/>
  <rect x="18" y="56" width="84" height="14" rx="7" fill="#fff"/>
  <circle cx="60" cy="84" r="24" fill="#f6c9a8"/>
  <circle cx="51" cy="80" r="3" fill="#2b2b38"/><circle cx="69" cy="80" r="3" fill="#2b2b38"/>
  <circle cx="45" cy="90" r="5" fill="#f3a7a7" opacity="0.7"/><circle cx="75" cy="90" r="5" fill="#f3a7a7" opacity="0.7"/>
  <circle cx="60" cy="88" r="4.5" fill="#ee8f86"/>
  <path d="M30 92 Q32 136 60 136 Q88 136 90 92 Q76 104 60 102 Q44 104 30 92Z" fill="#fff"/>
  <path d="M46 98 Q53 94 60 98 Q67 94 74 98 Q67 103 60 101 Q53 103 46 98Z" fill="#fff" stroke="#e3e8f2" stroke-width="1.5"/>
  <path d="M53 108 Q60 114 67 108" stroke="#c64a52" stroke-width="2.5" fill="none" stroke-linecap="round"/>
</svg>`;
const REINDEER = `<svg viewBox="0 0 120 140" role="img" aria-label="Red-nosed reindeer">
  <path d="M38 52 L26 30 M26 30 L18 34 M26 30 L24 18 M32 42 L20 44" stroke="#8a5a35" stroke-width="5" stroke-linecap="round" fill="none"/>
  <path d="M82 52 L94 30 M94 30 L102 34 M94 30 L96 18 M88 42 L100 44" stroke="#8a5a35" stroke-width="5" stroke-linecap="round" fill="none"/>
  <ellipse cx="34" cy="62" rx="10" ry="6" fill="#a86f45" transform="rotate(-25 34 62)"/>
  <ellipse cx="86" cy="62" rx="10" ry="6" fill="#a86f45" transform="rotate(25 86 62)"/>
  <ellipse cx="60" cy="80" rx="27" ry="30" fill="#b87b4b"/>
  <ellipse cx="60" cy="100" rx="18" ry="16" fill="#d9a77a"/>
  <circle cx="49" cy="76" r="4" fill="#2b2b38"/><circle cx="71" cy="76" r="4" fill="#2b2b38"/>
  <circle cx="50.5" cy="74.5" r="1.3" fill="#fff"/><circle cx="72.5" cy="74.5" r="1.3" fill="#fff"/>
  <circle cx="60" cy="96" r="13" fill="#ff3b4a" opacity="0.35"/>
  <circle cx="60" cy="96" r="8.5" fill="#ff3b4a"/>
  <circle cx="57" cy="93" r="2.4" fill="#fff" opacity="0.8"/>
  <path d="M53 108 Q60 113 67 108" stroke="#7a4a2c" stroke-width="2.4" fill="none" stroke-linecap="round"/>
</svg>`;
const ELF = `<svg viewBox="0 0 110 140" role="img" aria-label="Elf">
  <path d="M28 58 L60 6 Q70 2 74 12 L84 58Z" fill="#3fa36b"/>
  <circle cx="62" cy="8" r="6" fill="#ffd34d"/>
  <rect x="24" y="54" width="64" height="11" rx="5.5" fill="#d93a4c"/>
  <path d="M30 80 L14 70 L32 90Z" fill="#f6c9a8"/><path d="M82 80 L98 70 L80 90Z" fill="#f6c9a8"/>
  <circle cx="56" cy="88" r="25" fill="#f6c9a8"/>
  <path d="M36 70 Q42 62 50 66 Q56 60 62 66 Q70 62 76 70" fill="#f5d36b"/>
  <circle cx="47" cy="86" r="3" fill="#2b2b38"/><circle cx="65" cy="86" r="3" fill="#2b2b38"/>
  <circle cx="41" cy="96" r="4.5" fill="#f3a7a7" opacity="0.7"/><circle cx="71" cy="96" r="4.5" fill="#f3a7a7" opacity="0.7"/>
  <path d="M48 100 Q56 108 64 100" stroke="#c64a52" stroke-width="2.5" fill="none" stroke-linecap="round"/>
  <path d="M34 112 L56 124 L78 112 L80 140 L32 140Z" fill="#3fa36b"/>
  <path d="M44 116 L56 128 L68 116" stroke="#ffd34d" stroke-width="3" fill="none"/>
</svg>`;
const BUILTIN = [
  ['santa', SANTA, false],
  ['reindeer', REINDEER, false],
  ['elf', ELF, true],
];

/** A dashboard picture's address for an <img> (with the wall's sign-in when its cookie may be missing). */
export function pictureSrc(dashId, pid) {
  const u = new URL(`api/dashboards/${encodeURIComponent(dashId)}/pictures/${pid}`, window.location.href);
  const tok = tabletToken();
  if (tok) u.searchParams.set('token', tok);
  return u.toString();
}

/** Who shows: the built-in three, the household's pictures, or nobody. */
export function hasCast(xmas) {
  return xmas.characters === 'builtin' || (xmas.characters === 'pictures' && xmas.pictures.length > 0);
}

export function Cast({ xmas, dashId, className = '' }) {
  if (!hasCast(xmas)) return null;
  return (
    <span className={'xm-cast ' + className}>
      {xmas.characters === 'pictures'
        ? xmas.pictures.map((p) => <img key={p} className="xm-who xm-pic" src={pictureSrc(dashId, p)} alt="" draggable={false} />)
        : BUILTIN.map(([id, svg, small]) => <span key={id} className={'xm-who' + (small ? ' is-small' : '')} dangerouslySetInnerHTML={{ __html: svg }} />)}
    </span>
  );
}

/* ---------------- the top bar's decoration ---------------- */

/**
 * The greeting and (place "clock") the characters, for the bar's decor slot. `room` is what the tabs
 * leave over (tabs.jsx); the ruler measures both versions; onFit says what's shown ('all' | 'cast' |
 * 'none') so the rest goes to the dimmed screen.
 */
export function useDecor(xmas, dashId, on, room) {
  const ruler = useRef(null);
  const [fit, setFit] = useState('none');
  const greeting = on ? xmas.greeting : '';
  const cast = on && xmas.place === 'clock' && hasCast(xmas);
  const want = !!(greeting || cast);
  // the script font (and pictures) arriving change the widths: look again
  const [, bump] = useState(0);
  useEffect(() => {
    const fonts = typeof document !== 'undefined' && document.fonts;
    const on = () => bump((n) => n + 1);
    if (fonts && fonts.addEventListener) fonts.addEventListener('loadingdone', on);
    const r = ruler.current;
    const imgs = r ? [...r.querySelectorAll('img')] : [];
    imgs.forEach((i) => i.addEventListener('load', on));
    return () => {
      if (fonts && fonts.removeEventListener) fonts.removeEventListener('loadingdone', on);
      imgs.forEach((i) => i.removeEventListener('load', on));
    };
  }, [want, xmas.characters, xmas.pictures.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    if (!want) {
      setFit('none');
      return;
    }
    const r = ruler.current;
    if (!r || room === null) return;
    const [full, only] = r.children;
    const next = decorFit({ room, full: full ? full.offsetWidth : 0, cast: cast && only ? only.offsetWidth : 0, shown: fit });
    if (next !== fit) setFit(next);
  });
  const pieces = (withGreeting) => (
    <>
      {cast ? <Cast xmas={xmas} dashId={dashId} /> : null}
      {withGreeting && greeting ? <span className="xm-greet">{greeting}</span> : null}
    </>
  );
  const rulerEl = want ? (
    <div className="xm-ruler" ref={ruler} aria-hidden="true">
      <div className="xm-decor">{pieces(true)}</div>
      {cast ? <div className="xm-decor">{pieces(false)}</div> : null}
    </div>
  ) : null;
  const shown = fit === 'all' ? <div className="xm-decor">{pieces(true)}</div> : fit === 'cast' ? <div className="xm-decor">{pieces(false)}</div> : null;
  return { ruler: rulerEl, decor: shown, fit, moved: want && fit !== 'all' };
}

/** Big and soft on the dimmed screen: the greeting and characters that didn't fit in the bar. */
export function DimGreeting({ xmas, dashId }) {
  return (
    <div className="xm-dim" aria-hidden="true">
      {xmas.greeting ? <div className="xm-dim-greet">{xmas.greeting}</div> : null}
      {xmas.place === 'clock' ? <Cast xmas={xmas} dashId={dashId} className="xm-cast--dim" /> : null}
    </div>
  );
}

/** Behind the tiles (place "background"): the characters, large and blurred. */
export function Backdrop({ xmas, dashId }) {
  return (
    <div className="xm-backdrop" aria-hidden="true">
      <Cast xmas={xmas} dashId={dashId} className="xm-cast--back" />
    </div>
  );
}

/* ---------------- snow ---------------- */

const FLAKES = 60;

/** Gentle falling snow on a canvas: ~30 frames a second, at most 60 flakes; stops while the page is
 *  hidden or the screen dimmed; never with "reduce motion". */
export function Snow({ paused }) {
  const ref = useRef(null);
  const [still] = useState(() => typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || still || paused) return undefined;
    const ctx = canvas.getContext('2d');
    if (!ctx) return undefined;
    let raf = 0;
    let last = 0;
    let flakes = [];
    const size = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.round(canvas.clientWidth * dpr);
      canvas.height = Math.round(canvas.clientHeight * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      flakes = Array.from({ length: FLAKES }, () => ({ x: Math.random() * w, y: Math.random() * h, r: 1 + Math.random() * 2.4, s: 0.4 + Math.random() * 1, d: Math.random() * Math.PI * 2 }));
    };
    const step = (t) => {
      raf = requestAnimationFrame(step);
      if (document.hidden || t - last < 32) return;
      last = t;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
      ctx.beginPath();
      for (const f of flakes) {
        f.y += f.s;
        f.d += 0.02;
        f.x += Math.sin(f.d) * 0.4;
        if (f.y > h + 4) {
          f.y = -4;
          f.x = Math.random() * w;
        }
        ctx.moveTo(f.x + f.r, f.y);
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
      }
      ctx.fill();
    };
    size();
    raf = requestAnimationFrame(step);
    window.addEventListener('resize', size);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', size);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    };
  }, [still, paused]);
  if (still) return null;
  return <canvas className="xm-snow" ref={ref} aria-hidden="true" />;
}
