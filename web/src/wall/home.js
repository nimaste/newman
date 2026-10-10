// The dashboard's home page (dashboard.home, see dashboards.py): the page the wall tablet shows when
// nobody is using it - a different one in the mornings (by the tablet's own clock) - and going back
// to it after a while untouched. Pages stay in the tab bar either way; this only decides where the
// wall rests.

import { useEffect, useRef } from 'react';

export const HOME_DEFAULTS = {
  page: '',
  morning: null,
  idleMinutes: 2,
};
export const MORNING_DEFAULTS = {
  weekdays: { from: '06:00', to: '10:00' },
  weekends: { from: '07:00', to: '12:00' },
};
// A change of home (a morning starting or ending) waits until the screen has been left alone this long.
export const SWITCH_AFTER_IDLE_MS = 30 * 1000;

/** The dashboard's home settings with defaults, pointing only at pages that exist. */
export function homeOf(dash) {
  const pages = (dash && dash.pages) || [];
  const ids = pages.map((p) => p.id);
  const h = { ...HOME_DEFAULTS, ...((dash && dash.home) || {}) };
  const page = ids.includes(h.page) ? h.page : ids[0] || '';
  const m = h.morning && ids.includes(h.morning.page) ? { ...MORNING_DEFAULTS, ...h.morning } : null;
  const idle = Number.isFinite(Number(h.idleMinutes)) ? Math.max(0, Number(h.idleMinutes)) : HOME_DEFAULTS.idleMinutes;
  return { page, morning: m, idleMinutes: idle };
}

const minutes = (hhmm) => {
  const [h, m] = String(hhmm || '').split(':').map(Number);
  return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : null;
};

/** Is `now` inside today's morning window (Mon-Fri or Sat-Sun)? */
export function inMorning(morning, now = new Date()) {
  if (!morning) return false;
  const day = now.getDay();
  const w = day === 0 || day === 6 ? morning.weekends : morning.weekdays;
  const from = minutes(w && w.from);
  const to = minutes(w && w.to);
  if (from === null || to === null || from >= to) return false;
  const t = now.getHours() * 60 + now.getMinutes();
  return t >= from && t < to;
}

/** The page that is "home" right now. */
export function currentHome(home, now = new Date()) {
  return home.morning && inMorning(home.morning, now) ? home.morning.page : home.page;
}

/** Something on screen is in use: a full-screen camera, a device sheet, a PIN pad, an alert… */
function inUse() {
  return !!document.querySelector('[role="dialog"], [role="alertdialog"], [data-wall-in-use]'); // (the Family map flown away)
}

/**
 * Keeps the wall on its home page: when the home changes (a morning window starts or ends) it moves
 * there once nobody has touched the screen for 30 s; after `idleMinutes` untouched on another page it
 * goes back. `showing` is the current page id (or a full-screen view's name); `go(pageId)` navigates.
 */
export function useHomeSchedule(home, showing, go) {
  const lastTouch = useRef(Date.now());
  const lastHome = useRef(null);
  const pending = useRef(false);
  const state = useRef({ home, showing, go });
  state.current = { home, showing, go };

  useEffect(() => {
    const touched = () => {
      lastTouch.current = Date.now();
    };
    const opts = { capture: true, passive: true };
    ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach((ev) => window.addEventListener(ev, touched, opts));
    return () => ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach((ev) => window.removeEventListener(ev, touched, opts));
  }, []);

  useEffect(() => {
    const tick = () => {
      const { home: h, showing: on, go: nav } = state.current;
      if (!h || !h.page) return;
      const now = Date.now();
      const target = currentHome(h, new Date(now));
      if (lastHome.current === null) lastHome.current = target;
      if (target !== lastHome.current) {
        lastHome.current = target;
        pending.current = true; // a morning started or ended
      }
      if (on === target) {
        pending.current = false;
        return;
      }
      if (inUse()) {
        lastTouch.current = now; // open counts as being used; the clock starts when it closes
        return;
      }
      const idle = now - lastTouch.current;
      if (pending.current && idle >= SWITCH_AFTER_IDLE_MS) {
        pending.current = false;
        nav(target);
        return;
      }
      if (h.idleMinutes > 0 && idle >= h.idleMinutes * 60000) nav(target);
    };
    const t = setInterval(tick, 5000);
    return () => clearInterval(t);
  }, []);
}
