// How the wall's page tabs fit in the top bar - plain numbers in, a decision out (no DOM here, so
// `npm test` checks it under node). tabs.jsx measures the bar and hands the widths over.
//
// Who gets the width, in this order:
//   1. the clock and date (left)             - always their full width
//   2. the right-side controls               - music chip, clock note, dim button: always their full width
//   3. the page tabs                         - whatever is left (see below)
//   4. a decoration (.wt-decor, between the clock and the tabs) - only what the tabs leave over
//
// Where the tabs go, best first (the first that fits wins):
//   names (only with tabNames 'fit') centred on the bar · names right after the clock ·
//   icons with the current tab named, centred · the same right after the clock ·
//   the same right after the clock, scrolling sideways.
// Centred means the true centre of the whole bar, clear of the clock and the right side. With a
// decoration there is no centring: the tabs sit right after it.
//
// A dashboard's tabNames: 'current' = icons, the current tab named (the default) · 'fit' = every name
// when they fit, otherwise the same as 'current'.

export const TAB_NAMES = ['current', 'fit'];

const SLACK = 0.5; // sub-pixel rounding

/** A strip's width: its padding and border (`chrome`), each tab, and the gap between tabs. */
export function stripWidth(widths, chrome = 0, gap = 0) {
  return widths.length ? chrome + widths.reduce((s, w) => s + w, 0) + gap * (widths.length - 1) : 0;
}

/** The width the tabs may use beside the clock: the bar less the clock, the right side and the gaps. */
export function tabRoom({ bar, clock, right, gap = 0, decor = false }) {
  return Math.max(0, bar - clock - right - gap * (decor ? 3 : 2));
}

/** The widest strip that can sit at the true centre of the bar without touching either side. */
export function centreRoom({ bar, clock, right, gap = 0 }) {
  return Math.max(0, bar - 2 * Math.max(clock, right) - 2 * gap);
}

/**
 * bar / clock / right: widths of the bar and its two sides · gap: between them · decor: a decoration
 * is showing · chrome / tabGap: the strip's padding+border and the gap between tabs · named / icons:
 * each tab's width with its name / icon only · current: the current tab's index (-1: none) ·
 * pref: the dashboard's tabNames.
 * → { names: every tab named?, place: 'centre' | 'start' (right after the clock, or the decoration),
 *     scroll: wider than the room?, room: the width the strip may use }
 */
export function tabFit({ bar, clock, right, gap = 0, decor = false, chrome = 0, tabGap = 0, named, icons, current = -1, pref = 'current' }) {
  const room = tabRoom({ bar, clock, right, gap, decor });
  const centre = decor ? -1 : centreRoom({ bar, clock, right, gap });
  const mixed = icons.map((w, i) => (i === current ? named[i] : w));
  const tries = pref === 'fit' ? [[true, named], [false, mixed]] : [[false, mixed]];
  for (const [names, widths] of tries) {
    const w = stripWidth(widths, chrome, tabGap);
    if (w <= centre + SLACK) return { names, place: 'centre', scroll: false, room };
    if (w <= room + SLACK) return { names, place: 'start', scroll: false, room };
  }
  return { names: false, place: 'start', scroll: true, room };
}

/** Where to scroll a strip so tab `index` shows in full (past the edge fade), given the widths it ends up at. */
export function scrollFor({ widths, index, gap = 0, start = 0, view, now, fade = 0 }) {
  if (index < 0 || index >= widths.length) return now;
  const left = start + widths.slice(0, index).reduce((s, w) => s + w + gap, 0);
  const right = left + widths[index];
  if (left - fade < now) return Math.max(0, left - fade);
  if (right + fade > now + view) return right + fade - view;
  return now;
}
