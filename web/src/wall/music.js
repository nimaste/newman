// The wall tablet's music (the Navidrome connection): this tablet's player as the hub keeps it, and
// "ducking" - music goes quiet while Newman listens or speaks, a timer rings or a wall alert sounds.
// Music.jsx plays it (an <audio> element fed by the hub) and draws the now-playing chip, the player
// sheet (with the lyrics) and the Music tile.

import { useEffect, useState } from 'react';
import { tabletToken } from '../api.js';

let state = { available: false, player: null, loaded: false };
const subs = new Set();

function emit() {
  subs.forEach((fn) => fn(state));
}

/** A newer player from the hub (a push, a report's answer) replaces the one we have; an older one is ignored. */
export function setPlayer(player, extra = {}) {
  const cur = state.player;
  if (player && cur && player.entityId === cur.entityId && (player.seq || 0) < (cur.seq || 0)) return;
  state = { ...state, ...extra, player, loaded: true };
  emit();
}

export function useMusic() {
  const [s, setS] = useState(state);
  useEffect(() => {
    subs.add(setS);
    setS(state);
    return () => subs.delete(setS);
  }, []);
  return s;
}

/* ---------------- ducking ---------------- */

const reasons = new Set();
const duckSubs = new Set();

/** duck('voice', true) … duck('voice', false): quiet while any reason holds. */
export function duck(reason, on) {
  const had = reasons.has(reason);
  if (on === had) return;
  if (on) reasons.add(reason);
  else reasons.delete(reason);
  duckSubs.forEach((fn) => fn(new Set(reasons)));
}

export function useDuck() {
  const [r, setR] = useState(() => new Set(reasons));
  useEffect(() => {
    duckSubs.add(setR);
    setR(new Set(reasons));
    return () => duckSubs.delete(setR);
  }, []);
  return r;
}

/* ---------------- the voice card on screen (the bar steps aside) ---------------- */

let cardUp = false;
const cardSubs = new Set();
export function setVoiceCard(on) {
  if (cardUp === !!on) return;
  cardUp = !!on;
  cardSubs.forEach((fn) => fn(cardUp));
}
export function useVoiceCard() {
  const [v, setV] = useState(cardUp);
  useEffect(() => {
    cardSubs.add(setV);
    setV(cardUp);
    return () => cardSubs.delete(setV);
  }, []);
  return v;
}

/** For the wall's "is anyone using it" check (build.js): music playing; Newman listening / speaking, a timer or alert sounding. */
export function busyNow() {
  return { playing: !!(state.player && state.player.state === 'playing'), voice: reasons.size > 0 || cardUp };
}

/* ---------------- the Music tile on screen (the now-playing chip hides then) ---------------- */

let tiles = 0;
const tileSubs = new Set();
export function useTileShown() {
  useEffect(() => {
    tiles += 1;
    tileSubs.forEach((fn) => fn(tiles));
    return () => {
      tiles -= 1;
      tileSubs.forEach((fn) => fn(tiles));
    };
  }, []);
}
export function useTilesShown() {
  const [n, setN] = useState(tiles);
  useEffect(() => {
    tileSubs.add(setN);
    setN(tiles);
    return () => tileSubs.delete(setN);
  }, []);
  return n;
}

/** A hub address for <audio>/<img> - with the tablet's token when its cookie may be missing. */
export function musicUrl(path, params = {}) {
  if (!path) return null;
  const u = new URL(path, window.location.href);
  Object.entries(params).forEach(([k, v]) => u.searchParams.set(k, String(v)));
  const tok = tabletToken();
  if (tok) u.searchParams.set('token', tok);
  u.hash = '';
  return u.toString();
}

export function fmtTime(s) {
  const t = Math.max(0, Math.round(Number(s) || 0));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
}

/* ---------------- the player sheet (the now-playing chip opens it; so does "show the lyrics") ---------------- */

let sheet = { open: false, lyrics: false };
const sheetSubs = new Set();
function setSheet(next) {
  sheet = next;
  sheetSubs.forEach((fn) => fn(sheet));
}
export function openSheet({ lyrics = false } = {}) {
  setSheet({ open: true, lyrics: !!lyrics });
}
export function closeSheet() {
  if (sheet.open) setSheet({ open: false, lyrics: false });
}
export function setSheetLyrics(on) {
  setSheet({ ...sheet, lyrics: !!on });
}
export function useSheet() {
  const [s, setS] = useState(sheet);
  useEffect(() => {
    sheetSubs.add(setS);
    setS(sheet);
    return () => sheetSubs.delete(setS);
  }, []);
  return s;
}
