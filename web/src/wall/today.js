// Today's briefing on the wall (GET /api/today) and the actions on its items: Done (a GCloud task, with
// an optional "who"), Undo, Remind tomorrow, and dismissing an appliance's need. Shared by the Today
// tile and the To do screen; refreshed on live device changes (~2 s later) and every minute.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useLive } from '../live.js';

const listeners = new Set();
let cache = null;
let loading = null;

async function load() {
  if (loading) return loading;
  loading = api
    .get('api/today', { quiet: true })
    .then((d) => {
      cache = d;
      listeners.forEach((fn) => fn(d));
      return d;
    })
    .catch(() => cache)
    .finally(() => {
      loading = null;
    });
  return loading;
}

/** { data, reload } — the briefing, kept fresh. */
export function useToday() {
  const [data, setData] = useState(cache);
  const timer = useRef(null);
  useEffect(() => {
    listeners.add(setData);
    load();
    const t = setInterval(load, 60000);
    return () => {
      listeners.delete(setData);
      clearInterval(t);
      clearTimeout(timer.current);
    };
  }, []);
  useLive((m) => {
    // "today": a task changed in GCloud (the calendar server connection heard it).
    if (m.type === 'entity' || m.type === 'entity-removed' || m.type === 'reconnected' || m.type === 'today') {
      clearTimeout(timer.current);
      timer.current = setTimeout(load, 2000);
    }
  });
  return { data, reload: load };
}

/** The household (names for "Who did it?"): GET /api/tasks/calendars → { people, calendars }. */
export function useHousehold() {
  const [data, setData] = useState(null);
  const reload = useCallback(() => {
    api
      .get('api/tasks/calendars', { quiet: true })
      .then(setData)
      .catch(() => {});
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, reload };
}

/** Done → the hub's answer: { who, nextDue } (nextDue: when a repeating task is due again). */
export async function markDone(item, who) {
  const r = await api.post('api/tasks/done', who ? { id: item.id, who } : { id: item.id }, { quiet: true });
  load();
  return r || {};
}
export async function undoDone(id) {
  await api.post('api/tasks/undo', { id }, { quiet: true });
  load();
}
export async function snooze(item) {
  await api.post('api/tasks/snooze', { id: item.id, days: 1 }, { quiet: true });
  load();
}
export async function dismissNeed(item) {
  await api.post('api/today/dismiss', { id: item.id }, { quiet: true });
  load();
}
export function failed(err) {
  toast((err && err.message) || "That didn't work", 'error');
}

/** Colours for people (initial circles), stable by position. */
export const PEOPLE_COLOURS = ['#7FB8FF', '#C58BFF', '#3CCFB4', '#F5B942', '#FF8A6B', '#8BD46B'];

/** Stripe / badge colour by level. */
export function levelColour(level) {
  if (level === 'overdue' || level === 'device') return 'var(--wt-coral)';
  if (level === 'due' || level === 'appliance') return 'var(--wt-amber)';
  return 'rgba(238, 242, 255, 0.35)';
}

export function greeting(now = new Date()) {
  const h = now.getHours();
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
}

// Just done on this tablet: the row stays (struck through, with Undo) for a while after Done.
const KEEP_DONE_MS = 10 * 60 * 1000;
const recent = new Map(); // To do item id -> { item, taskId, who, at }
const recentListeners = new Set();
function emitRecent() {
  const snap = new Map(recent);
  recentListeners.forEach((fn) => fn(snap));
}
export function rememberDone(item, id, who, nextDue) {
  recent.set(item.id, { item, taskId: id, who: who || item.owner || '', nextDue: nextDue || '', at: Date.now() });
  emitRecent();
}
export function forgetDone(itemId) {
  recent.delete(itemId);
  emitRecent();
}
/** Map of items done here lately (older than 10 minutes drop off). */
export function useRecentlyDone() {
  const [snap, setSnap] = useState(() => new Map(recent));
  useEffect(() => {
    recentListeners.add(setSnap);
    const t = setInterval(() => {
      let changed = false;
      recent.forEach((v, k) => {
        if (Date.now() - v.at > KEEP_DONE_MS) {
          recent.delete(k);
          changed = true;
        }
      });
      if (changed) emitRecent();
    }, 30000);
    return () => {
      recentListeners.delete(setSnap);
      clearInterval(t);
    };
  }, []);
  return snap;
}

/** Done (a task: optionally who; appliance: dismiss), remembered for Undo. */
export async function doItem(item, who) {
  try {
    if (item.action === 'dismiss') {
      await dismissNeed(item);
      rememberDone(item, null, '');
      return true;
    }
    const r = await markDone(item, who);
    rememberDone(item, item.id, who, r.nextDue);
    return true;
  } catch (err) {
    failed(err);
    return false;
  }
}

/** Undo a Done made here (appliance dismissals can't be undone; they come back when reported again). */
export async function undoItem(itemId) {
  const r = recent.get(itemId);
  if (!r) return;
  try {
    if (r.taskId) await undoDone(r.taskId);
    forgetDone(itemId);
  } catch (err) {
    failed(err);
  }
}

/** The briefing's to-do list with items just done here kept in place (in their old spot). */
export function withRecent(list, recentMap) {
  const out = (list || []).filter((x) => !recentMap.has(x.id));
  recentMap.forEach((r) => {
    if (!out.some((x) => x.id === r.item.id)) out.push({ ...r.item, justDone: true, doneBy: r.who, nextDue: r.nextDue });
  });
  return out;
}