// The wall's compact layout (short screens, like a 960 x 480 Echo Show 5) - plain data in, an answer
// out (no DOM here, so `npm test` checks it under node). Compact.jsx draws it; compact.css sizes it.
//
// On a screen shorter than COMPACT_MAX_H CSS px the wall swaps its grid for pages you swipe sideways:
// the current tab's tiles in reading order (row by row, then left to right), two to a page. A tile
// whose type says it needs the room (the hub's catalog, dashboards.TILE_TYPES `ownPage`) gets a page
// to itself, and a tile left without a partner gets the page's full width.

/** Shorter than this (CSS px): the compact layout. Big tablets never are. */
export const COMPACT_MAX_H = 600;
/** The compact layout is drawn in units of the screen's height / UNITS (its mockup: 960 x 480). */
export const UNITS = 480;

/** Tiles in reading order: top to bottom by row, then left to right. */
export function readingOrder(tiles) {
  return (tiles || []).slice().sort((a, b) => (a.y || 0) - (b.y || 0) || (a.x || 0) - (b.x || 0));
}

/**
 * tiles: a page's tiles ({ type, x, y, … }) · types: the hub's tile types ([{ type, ownPage }]).
 * → pages: [[tile] | [tile, tile]] - one tile alone fills the page's width.
 */
export function compactPages(tiles, types) {
  const own = new Set((types || []).filter((t) => t && t.ownPage).map((t) => t.type));
  const pages = [];
  let waiting = null;
  for (const t of readingOrder(tiles)) {
    if (own.has(t.type)) {
      if (waiting) pages.push([waiting]);
      waiting = null;
      pages.push([t]);
    } else if (waiting) {
      pages.push([waiting, t]);
      waiting = null;
    } else {
      waiting = t;
    }
  }
  if (waiting) pages.push([waiting]);
  return pages;
}

/** Which page a sideways strip shows: its scroll position over one page's width. */
export function pageAt(scrollLeft, width, count) {
  if (!(width > 0) || !(count > 0)) return 0;
  return Math.max(0, Math.min(count - 1, Math.round(scrollLeft / width)));
}
