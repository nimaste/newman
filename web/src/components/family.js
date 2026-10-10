// The family map's shared pieces (the wall's Family tile, Household -> Map, the phone layout preview):
// how a person looks (their colour and photo from the hub), grouping people who are at the same spot into
// one bubble, the "home area" view (everyone within ~50 km of Home; farther people get an edge label
// instead of squeezing the map), and drawing pins into a map's DOM (both map libraries use drawPin).

import { tabletToken } from '../api.js';
import { fmtDistance, initial } from '../format.js';

export const NEAR_M = 50000; // the home view keeps to this far from Home
export const MIN_SPAN_M = 3000; // ... and never shows less than this across
export const TOGETHER_M = 150; // people this close (or at the same named place) share one bubble
export const FLY_SPAN_M = 1500; // tapping someone shows this much ground around them

const FALLBACK = '#3F8EDB';

export const located = (p) => !!p && p.sharing && typeof p.lat === 'number' && typeof p.lon === 'number';
export const colorOf = (p) => (p && typeof p.color === 'string' && /^#[0-9a-f]{6}$/i.test(p.color) ? p.color : FALLBACK);
export const firstName = (p) => String((p && p.name) || '').trim().split(/\s+/)[0] || '?';

/** Their photo's address for an <img> (with the wall's sign-in when its cookie may be missing), or null. */
export function photoSrc(p) {
  if (!p || !p.photo) return null;
  const u = new URL(String(p.photo).replace(/^\/+/, ''), window.location.href);
  const tok = tabletToken();
  if (tok) u.searchParams.set('token', tok);
  return u.toString();
}

/** Black or white initials, whichever reads better on their colour. */
export function inkOn(hex) {
  const c = colorOf({ color: hex }).slice(1);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(c.slice(i, i + 2), 16) / 255);
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.6 ? '#0b1026' : '#ffffff';
}

const R = 6371008.8;
const rad = (d) => (d * Math.PI) / 180;
export function distanceM(a, b) {
  const dp = rad(b.lat - a.lat);
  const dl = rad(b.lon - a.lon);
  const h = Math.sin(dp / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}
/** Compass bearing from a to b, degrees clockwise from north. */
export function bearing(a, b) {
  const y = Math.sin(rad(b.lon - a.lon)) * Math.cos(rad(b.lat));
  const x = Math.cos(rad(a.lat)) * Math.sin(rad(b.lat)) - Math.sin(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.cos(rad(b.lon - a.lon));
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

/** {s, w, n, e} around a spot, `spanM` across (and a third of that up and down: wall maps are wide, so the
 * width is what's fitted; a narrow map shows more height). */
export function boundsAround(lat, lon, spanM, tall = 0.33) {
  const dLon = (spanM / 2 / R) * (180 / Math.PI) / Math.max(0.05, Math.cos(rad(lat)));
  const dLat = (spanM * tall / 2 / R) * (180 / Math.PI);
  return { s: lat - dLat, n: lat + dLat, w: lon - dLon, e: lon + dLon };
}
/** The box around these spots, at least `minSpanM` across, with a margin. */
export function boundsOf(pts, minSpanM = MIN_SPAN_M) {
  if (!pts.length) return null;
  const lats = pts.map((p) => p.lat);
  const lons = pts.map((p) => p.lon);
  const b = { s: Math.min(...lats), n: Math.max(...lats), w: Math.min(...lons), e: Math.max(...lons) };
  const mid = { lat: (b.s + b.n) / 2, lon: (b.w + b.e) / 2 };
  const min = boundsAround(mid.lat, mid.lon, minSpanM);
  const padLat = (b.n - b.s) * 0.18;
  const padLon = (b.e - b.w) * 0.18;
  return {
    s: Math.min(b.s - padLat, min.s), n: Math.max(b.n + padLat, min.n),
    w: Math.min(b.w - padLon, min.w), e: Math.max(b.e + padLon, min.e),
  };
}
/** About how wide a box is on the ground ("About 3 km across"). */
export const spanOf = (b) => (b ? distanceM({ lat: (b.s + b.n) / 2, lon: b.w }, { lat: (b.s + b.n) / 2, lon: b.e }) : 0);

/** "Home", "School", "6 km" - where someone is in a word or two (null when we don't know). */
export function whereShort(p) {
  if (!located(p)) return null;
  if (p.home) return 'Home';
  if (p.place) return p.place;
  return typeof p.distanceM === 'number' ? fmtDistance(p.distanceM) : 'Away';
}

/**
 * People with a location -> map bubbles. Same named place (Home included) or within `togetherM` (150 m;
 * more on a zoomed-out map, so pins don't pile up) of each other -> one bubble. Each: {id, lat, lon,
 * members: [person], place: "Home"|"School"|null, tag}. `everyone`: how many located people there are in
 * all (a bubble with all of them says "everyone").
 */
export function groupPeople(people, everyone = people.length, togetherM = TOGETHER_M) {
  const groups = [];
  const placeOf = (p) => (p.home ? 'Home' : p.place || null);
  people.filter(located).forEach((p) => {
    const place = placeOf(p);
    const g = groups.find((x) => (place && x.places.has(place)) || x.members.some((m) => distanceM(m, p) <= togetherM));
    if (g) {
      g.members.push(p);
      g.places.add(place);
    } else groups.push({ places: new Set([place]), members: [p] });
  });
  return groups.map((g) => {
    const n = g.members.length;
    const lat = g.members.reduce((s, m) => s + m.lat, 0) / n;
    const lon = g.members.reduce((s, m) => s + m.lon, 0) / n;
    g.place = g.places.size === 1 ? [...g.places][0] : null;
    const where = g.place || (n > 1 ? (g.places.has('Home') ? 'Near home' : 'Together') : whereShort(g.members[0]) || '');
    const tag = n === 1 ? `${firstName(g.members[0])}${where ? ' · ' + where : ''}` : `${where} · ${n === everyone && n > 2 ? 'everyone' : n}`;
    return { id: 'g:' + g.members.map((m) => m.id).sort().join(','), lat, lon, members: g.members, place: g.place, tag, name: tag };
  });
}

/**
 * The home area: who's near enough to Home to be on the map (`near`), who isn't (`far`, each with its
 * bearing from Home and distance), and the box to show (`bounds`, at least 3 km across). Without a Home
 * spot everyone is "near".
 */
export function homeArea(people, home) {
  const on = people.filter(located);
  const hasHome = home && typeof home.lat === 'number' && typeof home.lon === 'number';
  if (!hasHome) return { near: on, far: [], bounds: boundsOf(on) };
  const near = [];
  const far = [];
  on.forEach((p) => {
    const d = distanceM(home, p);
    if (d <= NEAR_M) near.push(p);
    else far.push({ person: p, distanceM: d, bearing: bearing(home, p) });
  });
  return { near, far, bounds: boundsOf([home, ...near]) };
}

/** An arrow pointing that way (for a chip / edge label). */
export const arrowStyle = (deg) => ({ display: 'inline-block', transform: `rotate(${Math.round(deg)}deg)` });

// -- pins on the map (DOM, shared by the OpenFreeMap and Apple maps) ------------------------------------

function avatarEl(p, cls) {
  const a = document.createElement('span');
  a.className = cls;
  const color = colorOf(p);
  a.style.background = color;
  a.style.color = inkOn(color);
  const src = photoSrc(p);
  if (src) {
    const img = document.createElement('img');
    img.src = src;
    img.alt = '';
    img.draggable = false;
    img.onerror = () => {
      img.remove();
      a.textContent = initial(p.name);
    };
    a.appendChild(img);
  } else a.textContent = initial(p.name);
  return a;
}

/**
 * Fill a pin's box: one avatar (or a stack of up to 4, with "+n") and, when `item.tag` is set, a name tag
 * under it. `item` is a person ({name, color, photo, live}) or a bubble ({members, tag}). Redrawn only
 * when what it shows changes.
 */
export function drawPin(box, item) {
  const members = item.members || [item];
  const sig = JSON.stringify([item.tag || '', !!item.live, members.map((m) => [m.name, m.color, m.photo])]);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.textContent = '';
  box.title = item.tag || item.name || '';
  if (members.length === 1) {
    const pin = avatarEl(members[0], 'map-pin');
    pin.classList.toggle('is-live', !!item.live);
    box.appendChild(pin);
  } else {
    const stack = document.createElement('span');
    stack.className = 'map-pin-stack';
    members.slice(0, 4).forEach((m) => stack.appendChild(avatarEl(m, 'map-pin map-pin--small')));
    if (members.length > 4) {
      const more = document.createElement('span');
      more.className = 'map-pin-more';
      more.textContent = '+' + (members.length - 4);
      stack.appendChild(more);
    }
    box.appendChild(stack);
  }
  if (item.tag) {
    const tag = document.createElement('span');
    tag.className = 'map-pin-tag' + (members.length > 1 ? ' is-group' : '');
    tag.textContent = item.tag;
    box.appendChild(tag);
  }
}
