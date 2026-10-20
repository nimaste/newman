// A tile's Look, for tiles that show a list of things (the hub's catalog: dashboards.py LOOK): "tiles"
// (a small card each), "list" (a row each, full names) or "auto" - cards when the tile has room, rows
// when it's narrow. A devices tile saved before Look goes by its old "style".

import { useLayoutEffect, useState } from 'react';

/** Narrower than this (px, the tile's own width): "auto" shows a list. About a third of the tablet. */
export const LIST_BELOW = 440;

export function lookOf(config, width) {
  const c = config || {};
  const v = c.look || (c.style === 'list' ? 'list' : c.style === 'tiles' ? 'tiles' : 'auto');
  if (v === 'list' || v === 'tiles') return v;
  return width > 0 && width < LIST_BELOW ? 'list' : 'tiles';
}

/** An element's width in px, kept up to date (0 until it's drawn). */
export function useWidth(ref) {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    setW(el.clientWidth);
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}
