// The wall's look, worked out from a dashboard (plain data in, an answer out - no DOM, so `npm test`
// checks it under node). Theme.jsx applies it; theme.css has each theme's tokens.
//
// dashboard.theme: 'midnight' (the original deep blue) | 'glass' (light, frosted) | 'christmas'.
// dashboard.christmas: { snow, greeting, characters: 'builtin' | 'pictures' | 'off',
//   place: 'clock' | 'background' | 'off', pictures: [id], season: null | { from: 'MM-DD', to: 'MM-DD' } }
// In its season (every year; Dec 1 - Jan 6 by default, over New Year) Christmas replaces the chosen theme.

export const THEMES = ['midnight', 'glass', 'christmas'];
export const SEASON = { from: '12-01', to: '01-06' };
export const CHRISTMAS = { snow: true, greeting: 'Merry Christmas', characters: 'builtin', place: 'clock', pictures: [], season: null };
const OLD = { 'night-glass': 'midnight' };

/** "12-01" -> [12, 1]; null when it isn't a month and day. */
export function md(v) {
  const m = /^\s*(\d{1,2})-(\d{1,2})\s*$/.exec(String(v || ''));
  if (!m) return null;
  const mo = Number(m[1]);
  const dy = Number(m[2]);
  return mo >= 1 && mo <= 12 && dy >= 1 && dy <= 31 ? [mo, dy] : null;
}

/** Is `day` (a local Date) inside the season? Inclusive; a season over New Year wraps. Off (null): never. */
export function seasonActive(season, day = new Date()) {
  if (!season || typeof season !== 'object') return false;
  const a = md(season.from || SEASON.from);
  const b = md(season.to || SEASON.to);
  if (!a || !b) return false;
  const n = (day.getMonth() + 1) * 100 + day.getDate();
  const s = a[0] * 100 + a[1];
  const e = b[0] * 100 + b[1];
  return s <= e ? n >= s && n <= e : n >= s || n <= e;
}

export function christmasOf(dash) {
  const c = (dash && dash.christmas) || {};
  return { ...CHRISTMAS, ...c, pictures: Array.isArray(c.pictures) ? c.pictures : [] };
}

export function chosenTheme(dash) {
  const t = dash && (OLD[dash.theme] || dash.theme);
  return THEMES.includes(t) ? t : 'midnight';
}

/** The look today: Christmas in its season, otherwise the dashboard's own theme. */
export function themeNow(dash, day = new Date()) {
  return seasonActive(christmasOf(dash).season, day) ? 'christmas' : chosenTheme(dash);
}

/**
 * Each theme's plain page colour (theme.css --wt-page; Midnight's is wall.css's fallback) and light or
 * dark: what the wall apps paint behind the page while it loads or can't be reached (NewmanWall.setTheme).
 */
export const SHELL = {
  midnight: { background: '#0b1026', scheme: 'dark' },
  glass: { background: '#e6def5', scheme: 'light' },
  christmas: { background: '#6f9dd4', scheme: 'dark' },
};

export function shellOf(theme) {
  return SHELL[theme] || SHELL.midnight;
}

/** Milliseconds until the next local midnight (when the season can start or end), plus a second. */
export function msToMidnight(now = new Date()) {
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 1);
  return Math.max(1000, next - now);
}

/**
 * What of the Christmas decoration fits in the top bar's decor slot, which only gets what the tabs leave
 * over (tabfit.js). room: that width · full: greeting + characters · cast: characters only (0: none) ·
 * shown: the last answer, for a little give so it doesn't flicker at the edge.
 * → 'all' | 'cast' | 'none'. Whatever doesn't fit goes to the dimmed screen.
 */
export function decorFit({ room, full, cast = 0, shown = null, give = 8 }) {
  const fits = (w, was) => w > 0 && w <= room + (shown === was ? give : 0) - (shown === was ? 0 : give / 2);
  if (fits(full, 'all')) return 'all';
  if (cast && fits(cast, 'cast')) return 'cast';
  return 'none';
}
