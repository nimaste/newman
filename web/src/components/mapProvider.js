// Which map the web and wall maps draw: Apple Maps when the hub has a MapKit key (it hands out short-lived
// MapKit JS tokens at /api/map/token), else OpenFreeMap through the hub. Asked once per page load and kept;
// MapKit asks for a fresh token as one runs out (mapkitToken).

import { tabletToken } from '../api.js';

// Shared by both maps. Circles and outlines: teal, the one being edited amber; brighter on the night map.
export const INK = {
  light: { line: '#1d5c57', fill: '#6fc2b6', fillO: 0.12, onLine: '#9a4a0b', onFill: '#e0a15a', onFillO: 0.2, acc: '#1d5c57', accO: 0.08 },
  dark: { line: '#3ccfb4', fill: '#3ccfb4', fillO: 0.12, onLine: '#f2b26b', onFill: '#f2b26b', onFillO: 0.18, acc: '#3ccfb4', accO: 0.1 },
};
export const placed = (x) => x && typeof x.lat === 'number' && typeof x.lon === 'number';

const OFM = { provider: 'openfreemap' };
let first = null; // the first answer (a promise), kept for the page's life
let known = null; // ...and once it's in: 'apple' | 'openfreemap'
let appleOff = false; // MapKit couldn't load or was refused: OpenFreeMap from then on

function fetchToken() {
  const token = tabletToken();
  return fetch('api/map/token', { credentials: 'same-origin', headers: token ? { Authorization: 'Bearer ' + token } : {} }).then((r) => {
    if (!r.ok) throw new Error('map token: HTTP ' + r.status);
    return r.json();
  });
}

/** {provider: 'apple', token, expires} or {provider: 'openfreemap'}. */
export function mapProvider() {
  if (!first) {
    first = fetchToken()
      .then((j) => (j && j.provider === 'apple' && j.token ? j : OFM))
      .catch(() => {
        first = null; // ask again next time; this map uses OpenFreeMap
        return OFM;
      });
  }
  return first.then((p) => {
    known = appleOff ? 'openfreemap' : p.provider;
    return appleOff ? OFM : p;
  });
}

/** The provider if already known (so a second map draws straight away), else null. */
export const knownProvider = () => (appleOff ? 'openfreemap' : known);

/** Apple Maps failed (script blocked, key refused): use OpenFreeMap for the rest of this page load. */
export function appleUnavailable() {
  appleOff = true;
  known = 'openfreemap';
}

/** A MapKit JS token for MapKit's authorizationCallback: the kept one while it has 5+ minutes left. */
export async function mapkitToken() {
  const p = first ? await first : null;
  if (p && p.token && p.expires - Date.now() / 1000 > 300) return p.token;
  const j = await fetchToken();
  if (!j || j.provider !== 'apple' || !j.token) throw new Error('no MapKit token');
  first = Promise.resolve(j);
  return j.token;
}
