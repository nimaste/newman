// The wall's page tabs in the top bar (the wall, and the layout editor's still preview). The fit
// rules and who gets the width first are in tabfit.js; this measures the bar live and applies them.
// It sits inside header.wt-top, between .wt-clock (and an optional .wt-decor) and .wt-top-right.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from '../icons.jsx';
import { scrollFor, stripWidth, tabFit } from './tabfit.js';

const FADE = 28; // px of soft edge on a scrolling strip (matches wall.css)

const px = (v) => parseFloat(v) || 0;

/** A side's own width: its in-flow children (they don't shrink) and the gaps between them. */
function natural(el) {
  if (!el) return 0;
  const kids = [...el.children].filter((k) => {
    const cs = getComputedStyle(k);
    return cs.display !== 'none' && cs.position !== 'absolute' && cs.position !== 'fixed';
  });
  return kids.reduce((s, k) => s + k.offsetWidth, 0) + px(getComputedStyle(el).columnGap) * Math.max(0, kids.length - 1);
}

const reduced = () => typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * pages [{id, name, icon}], current: a page id, tabNames: 'current' | 'fit', hrefFor(page): a link
 * per tab (none: still spans, for the editor's preview). onDecorRoom(px): the width the tabs leave over for a
 * decoration (.wt-decor); decor: what the decoration shows now (a change measures again).
 */
export default function WallTabs({ pages, current, tabNames = 'current', hrefFor, onDecorRoom, decor: decorNow = '' }) {
  const onRoom = useRef(onDecorRoom);
  onRoom.current = onDecorRoom;
  const nav = useRef(null);
  const strip = useRef(null);
  const ruler = useRef(null);
  const [fit, setFit] = useState({ names: false, scroll: false, place: 'centre', labels: [], widths: null });
  const [edges, setEdges] = useState({ start: false, end: false });
  const at = pages.findIndex((p) => p.id === current);
  const pref = tabNames === 'fit' ? 'fit' : 'current';
  const key = pages.map((p) => p.id + '\u0001' + p.name + '\u0001' + (p.icon || '')).join('\u0002');

  // Measure: the bar, both sides, and each tab named and icon-only (on the hidden ruler).
  const measure = useRef(() => {});
  measure.current = () => {
    const n = nav.current;
    const s = strip.current;
    const r = ruler.current;
    const bar = n && n.closest('.wt-top');
    if (!bar || !s || !r) return;
    const clock = natural(bar.querySelector(':scope > .wt-clock'));
    const right = natural(bar.querySelector(':scope > .wt-top-right'));
    bar.style.setProperty('--wt-clock-w', clock + 'px');
    bar.style.setProperty('--wt-right-w', right + 'px');
    const decor = !!bar.querySelector(':scope > .wt-decor');
    const [rowNamed, rowIcons] = r.children;
    const named = [...rowNamed.children].map((t) => t.offsetWidth);
    const icons = [...rowIcons.children].map((t) => t.offsetWidth);
    const labels = [...rowNamed.children].map((t) => (t.lastElementChild ? t.lastElementChild.offsetWidth : 0));
    const cs = getComputedStyle(s);
    const chrome = px(cs.paddingLeft) + px(cs.paddingRight) + px(cs.borderLeftWidth) + px(cs.borderRightWidth);
    const tabGap = px(cs.columnGap);
    const d = tabFit({ bar: bar.clientWidth, clock, right, gap: px(getComputedStyle(bar).columnGap), decor, chrome, tabGap, named, icons, current: at, pref });
    bar.style.setProperty('--wt-tab-room', d.room + 'px');
    const widths = d.names ? named : icons.map((w, i) => (i === at ? named[i] : w));
    // What's left for a decoration once the tabs have theirs (as if it were showing: one more gap).
    if (onRoom.current) onRoom.current(d.scroll ? 0 : Math.max(0, Math.floor(bar.clientWidth - clock - right - px(getComputedStyle(bar).columnGap) * 3 - stripWidth(widths, chrome, tabGap))));
    const next = { ...d, place: decor ? 'decor' : d.place, labels, widths, gap: tabGap, start: px(cs.paddingLeft) + px(cs.borderLeftWidth) };
    setFit((f) => (JSON.stringify(f) === JSON.stringify(next) ? f : next));
  };

  // Live: the bar resizing (rotation, window), either side changing (the time, the music chip coming
  // and going), the tabs' own widths (names, fonts loading).
  useLayoutEffect(() => {
    const n = nav.current;
    const bar = n && n.closest('.wt-top');
    if (!bar) return undefined;
    let frame = 0;
    const run = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => measure.current());
    };
    measure.current();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(run);
    const sides = [bar.querySelector(':scope > .wt-clock'), bar.querySelector(':scope > .wt-top-right')].filter(Boolean);
    const watch = () => {
      ro.disconnect();
      [bar, ruler.current, ...sides, ...sides.flatMap((el) => [...el.children])].forEach((el) => el && ro.observe(el));
    };
    watch();
    const mo = typeof MutationObserver !== 'undefined' ? new MutationObserver(() => (watch(), run())) : null;
    if (mo) sides.forEach((el) => mo.observe(el, { childList: true }));
    const fonts = typeof document !== 'undefined' && document.fonts;
    if (fonts) {
      fonts.ready.then(run);
      fonts.addEventListener && fonts.addEventListener('loadingdone', run);
    }
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      if (mo) mo.disconnect();
      if (fonts && fonts.removeEventListener) fonts.removeEventListener('loadingdone', run);
    };
  }, [key, at, pref, decorNow]);

  // Where the tabs sit (the bar's data-tabs: centre | start | decor; wall.css lays it out). A move
  // glides from where they were.
  const lastLeft = useRef(null);
  useLayoutEffect(() => {
    const n = nav.current;
    const bar = n && n.closest('.wt-top');
    if (!bar) return;
    const before = lastLeft.current;
    if (bar.dataset.tabs !== fit.place) bar.dataset.tabs = fit.place;
    n.style.transition = 'none';
    n.style.transform = '';
    const after = n.offsetLeft;
    lastLeft.current = after;
    if (before === null || Math.abs(before - after) < 1 || reduced()) return;
    n.style.transform = `translateX(${before - after}px)`;
    void n.offsetWidth; // start from there
    n.style.transition = 'transform 0.3s ease';
    n.style.transform = '';
  }, [fit.place]);
  useEffect(() => {
    // keep the starting point current as the bar changes without a move (rotation, a new page…)
    const n = nav.current;
    if (n && !n.style.transform) lastLeft.current = n.offsetLeft;
  });

  // A scrolling strip: soft fades on the edges that hide more, and the current tab in view.
  const onScroll = () => {
    const s = strip.current;
    if (!s) return;
    const max = s.scrollWidth - s.clientWidth;
    const next = { start: fit.scroll && s.scrollLeft > 1, end: fit.scroll && s.scrollLeft < max - 1 };
    setEdges((e) => (e.start === next.start && e.end === next.end ? e : next));
  };
  useEffect(() => {
    const s = strip.current;
    if (!s) return undefined;
    if (!fit.scroll || !fit.widths) {
      if (s.scrollLeft) s.scrollLeft = 0;
      onScroll();
      return undefined;
    }
    const left = scrollFor({ widths: fit.widths, index: at, gap: fit.gap, start: fit.start, view: s.clientWidth, now: s.scrollLeft, fade: FADE });
    if (Math.abs(left - s.scrollLeft) > 1) s.scrollTo({ left, behavior: reduced() ? 'auto' : 'smooth' });
    // the widths ease into place: look at the edges again once they have
    const t = setTimeout(onScroll, 320);
    onScroll();
    return () => clearTimeout(t);
  }, [at, fit]); // eslint-disable-line react-hooks/exhaustive-deps

  const tab = (p, i, show) => {
    const on = p.id === current;
    const link = hrefFor && i >= 0; // the ruler's copies are never links
    const Tag = link ? 'a' : 'span';
    const lw = fit.labels[i];
    return (
      <Tag
        key={p.id}
        className={'wt-tab' + (on ? ' is-on' : '') + (show ? ' is-named' : ' is-icon')}
        {...(link ? { href: hrefFor(p), 'aria-current': on ? 'page' : undefined, 'aria-label': p.name, title: p.name } : {})}
        style={lw ? { '--wt-label-w': lw + 'px' } : undefined}
      >
        <Icon name={p.icon || 'grid'} size={20} />
        <span className="wt-tab-label">{p.name}</span>
      </Tag>
    );
  };

  return (
    <nav className="wt-tabs-wrap" aria-label="Pages" ref={nav}>
      <div
        ref={strip}
        className={'wt-tabs' + (fit.scroll ? ' is-scrolling' : '') + (edges.start ? ' fade-start' : '') + (edges.end ? ' fade-end' : '')}
        onScroll={fit.scroll ? onScroll : undefined}
      >
        {pages.map((p, i) => tab(p, i, fit.names || p.id === current))}
      </div>
      <div className="wt-tab-ruler" ref={ruler} aria-hidden="true">
        <div>{pages.map((p) => tab({ ...p, id: 'n' + p.id }, -1, true))}</div>
        <div>{pages.map((p) => tab({ ...p, id: 'i' + p.id }, -1, false))}</div>
      </div>
    </nav>
  );
}
