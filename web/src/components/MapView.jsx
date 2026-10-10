// The map: places as soft circles, people as pins in their colour (or photo). Apple Maps (MapKit JS, AppleMapView.jsx) when
// the hub has a MapKit key, else - and if Apple's map can't load - a MapLibre map of OpenFreeMap's vector
// map (below). Each library is loaded only when a map using it is shown.
//   places: [{id, name, lat, lon, radiusM}]  (lat null = not placed yet; skipped)
//   people: [{id, name, lat, lon, accuracyM, live, color, photo}] - or bubbles ({id, lat, lon, members, tag}:
//           several people in one pin; family.js groupPeople). A `tag` shows a name label under the pin.
//   highlight: a place id drawn stronger (the one being edited)
//   focus: {lat, lon, zoom?, pan?, key} or {bounds: {s, w, n, e}, key}: fly (or just pan) there / fit that
//          box whenever `key` changes (zoom in the usual web-map levels: 15 = streets, 16 = a few houses)
//   onMapClick(lat, lon): a click on the map (not on a pin)
//   onPersonClick(id): a click on someone's pin
//   className: extra classes; "map-view--dark" uses the night map (the wall tablet)
// The first time there's something to show, the map fits it all in view.
// OpenFreeMap: everything it draws comes through the hub (/api/map/ofm/..., which fetches from OpenFreeMap
// and keeps copies), so the browser never contacts anyone else - at home or away. Apple Maps: the browser
// loads MapKit JS and the map imagery from Apple directly (Apple sees which areas are viewed).

import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import 'maplibre-gl/dist/maplibre-gl.css';
import { tabletToken } from '../api.js';
import { drawPin } from './family.js';
import { INK, appleUnavailable, knownProvider, mapProvider, placed } from './mapProvider.js';

// Apple's map in its own file: fetched only when the hub says to use it. (If even that file can't
// load, OpenFreeMap it is.)
const loadApple = () => import('./AppleMapView.jsx');
const AppleMapView = lazy(() => loadApple().catch(() => ({
  default: function NoApple({ onUnavailable }) {
    useEffect(() => onUnavailable(), []); // eslint-disable-line react-hooks/exhaustive-deps
    return null;
  },
})));

const STYLE = { light: 'positron', dark: 'fiord' };
const ATTRIBUTION = '&copy; <a href="https://openfreemap.org" target="_blank" rel="noreferrer">OpenFreeMap</a> '
  + '&copy; <a href="https://www.openmaptiles.org/" target="_blank" rel="noreferrer">OpenMapTiles</a> '
  + '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors';
// The usual web-map zoom levels (256 px tiles) -> MapLibre's (512 px): one less shows the same ground.
const Z = (z) => z - 1;
const EMPTY = { type: 'FeatureCollection', features: [] };

const absolute = (u) => (typeof u === 'string' && u.startsWith('/') ? window.location.origin + u : u);
const onHub = (u) => u.startsWith(window.location.origin + '/api/');

// maplibre-gl is big: loaded the first time a map is wanted (its own file), then kept.
let lib = null;
const loadLib = () => lib || (lib = import('maplibre-gl').then((m) => m.default || m).catch((e) => { lib = null; throw e; }));
const styles = {};
function getJson(path) {
  const token = tabletToken();
  return fetch(path.replace(/^\/+/, ''), { credentials: 'same-origin', headers: token ? { Authorization: 'Bearer ' + token } : {} }).then((r) => {
    if (!r.ok) throw new Error('map data: HTTP ' + r.status);
    return r.json();
  });
}
// The style, with each vector source's TileJSON folded in (one fewer round trip before the first
// tiles, and no second attribution line from it).
function loadStyle(name) {
  if (!styles[name]) {
    styles[name] = getJson('api/map/ofm/styles/' + name)
      .then(async (s) => {
        await Promise.all(Object.values(s.sources).filter((src) => src.type === 'vector' && src.url).map(async (src) => {
          const tj = await getJson(src.url);
          delete src.url;
          for (const k of ['tiles', 'minzoom', 'maxzoom', 'bounds', 'scheme']) if (tj[k] !== undefined) src[k] = tj[k];
        }));
        return s;
      })
      .catch((e) => {
        delete styles[name];
        throw e;
      });
  }
  return styles[name];
}
/** Start fetching the map code (and OpenFreeMap's style) early (e.g. when a tile that opens a map is shown). */
export function preloadMap(dark = false) {
  mapProvider().then((p) => {
    if (p.provider === 'apple') {
      loadApple().then((m) => m.preloadMapKit()).catch(() => {});
      return;
    }
    loadLib().catch(() => {});
    loadStyle(dark ? STYLE.dark : STYLE.light).catch(() => {});
  });
}

// The style as MapLibre wants it: absolute links (the hub hands out /api/... ones), one attribution
// line of our own, and our circle layers tucked in under the map's labels.
function buildStyle(base, ink) {
  const s = JSON.parse(JSON.stringify(base));
  s.sprite = absolute(s.sprite);
  s.glyphs = absolute(s.glyphs);
  for (const src of Object.values(s.sources)) {
    if (src.url) src.url = absolute(src.url);
    if (src.tiles) src.tiles = src.tiles.map(absolute);
    delete src.attribution;
  }
  s.sources['nm-places'] = { type: 'geojson', data: EMPTY };
  s.sources['nm-acc'] = { type: 'geojson', data: EMPTY };
  const ours = [
    { id: 'nm-acc', type: 'fill', source: 'nm-acc', paint: { 'fill-color': ink.acc, 'fill-opacity': ink.accO } },
    { id: 'nm-places-fill', type: 'fill', source: 'nm-places',
      paint: { 'fill-color': ['case', ['get', 'on'], ink.onFill, ink.fill], 'fill-opacity': ['case', ['get', 'on'], ink.onFillO, ink.fillO] } },
    { id: 'nm-places-line', type: 'line', source: 'nm-places',
      paint: { 'line-color': ['case', ['get', 'on'], ink.onLine, ink.line], 'line-width': ['case', ['get', 'on'], 2.5, 1.5] } },
  ];
  const firstLabel = s.layers.findIndex((l) => l.type === 'symbol');
  s.layers.splice(firstLabel < 0 ? s.layers.length : firstLabel, 0, ...ours);
  return s;
}

// A true circle of `r` metres on the ground, as a polygon (so its size is right at every zoom and latitude).
function circle(lat, lon, r, props) {
  const d = r / 6371008.8;
  const la = (lat * Math.PI) / 180;
  const lo = (lon * Math.PI) / 180;
  const ring = [];
  for (let i = 0; i <= 96; i++) {
    const b = (2 * Math.PI * i) / 96;
    const la2 = Math.asin(Math.sin(la) * Math.cos(d) + Math.cos(la) * Math.sin(d) * Math.cos(b));
    const lo2 = lo + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la), Math.cos(d) - Math.sin(la) * Math.sin(la2));
    ring.push([(lo2 * 180) / Math.PI, (la2 * 180) / Math.PI]);
  }
  return { type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [ring] } };
}

// Where to look at first: the focus, or everything there is to show.
const boxOf = (b) => [[b.w, b.s], [b.e, b.n]];
function firstView(ml, focus, pts) {
  if (focus && focus.bounds) return { bounds: boxOf(focus.bounds), fitBoundsOptions: { padding: 30 } };
  if (placed(focus)) return { center: [focus.lon, focus.lat], zoom: Z(focus.zoom || 15) };
  if (pts.length === 1) return { center: [pts[0].lon, pts[0].lat], zoom: Z(15) };
  if (pts.length > 1) {
    const b = new ml.LngLatBounds();
    pts.forEach((p) => b.extend([p.lon, p.lat]));
    const dx = (b.getEast() - b.getWest()) * 0.25 || 0.002;
    const dy = (b.getNorth() - b.getSouth()) * 0.25 || 0.002;
    return { bounds: [[b.getWest() - dx, b.getSouth() - dy], [b.getEast() + dx, b.getNorth() + dy]], fitBoundsOptions: { maxZoom: Z(16) } };
  }
  return null;
}

/** Apple Maps or OpenFreeMap, as the hub says (asked once per page load). */
export default function MapView(props) {
  const [provider, setProvider] = useState(knownProvider);
  useEffect(() => {
    let gone = false;
    mapProvider().then((p) => !gone && setProvider(p.provider));
    return () => {
      gone = true;
    };
  }, []);
  const { className = '', label = 'Map' } = props;
  const blank = <div className={'map-view ' + className} role="region" aria-label={label} />;
  if (!provider) return blank;
  if (provider === 'apple') {
    const fallBack = () => {
      appleUnavailable();
      setProvider('openfreemap');
    };
    return (
      <Suspense fallback={blank}>
        <AppleMapView {...props} onUnavailable={fallBack} />
      </Suspense>
    );
  }
  return <LibreMap {...props} />;
}

function LibreMap({ places = [], people = [], highlight, focus, onMapClick, onPersonClick, className = '', label = 'Map' }) {
  const el = useRef(null);
  const map = useRef(null);
  const ml = useRef(null);
  const [ready, setReady] = useState(false);
  const fitted = useRef(false);
  const labels = useRef(new Map());
  const pins = useRef(new Map());
  const latest = useRef({});
  latest.current = { places, people, focus };
  const clickRef = useRef(onMapClick);
  clickRef.current = onMapClick;
  const personRef = useRef(onPersonClick);
  personRef.current = onPersonClick;
  const dark = className.split(/\s+/).includes('map-view--dark');

  useEffect(() => {
    let gone = false;
    let ro = null;
    Promise.all([loadLib(), loadStyle(dark ? STYLE.dark : STYLE.light)]).then(([maplibre, base]) => {
      if (gone || !el.current) return;
      ml.current = maplibre;
      const { places: pl, people: pe, focus: fo } = latest.current;
      const view = firstView(maplibre, fo, [...pl.filter(placed), ...pe.filter(placed)]);
      if (view) fitted.current = true; // starting where the content is: no world map first
      const m = new maplibre.Map({
        container: el.current,
        style: buildStyle(base, dark ? INK.dark : INK.light),
        center: [-40, 30],
        zoom: 1,
        ...(view || {}),
        maxZoom: 19,
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        fadeDuration: 0,
        // Links in the style are the hub's; the wall also sends its sign-in (its cookie may not stick).
        transformRequest: (url) => {
          const u = absolute(url);
          const token = onHub(u) ? tabletToken() : null;
          return token ? { url: u, headers: { Authorization: 'Bearer ' + token } } : { url: u };
        },
      });
      m.touchZoomRotate.disableRotation();
      m.keyboard.disableRotation();
      m.addControl(new maplibre.NavigationControl({ showCompass: false }), 'top-left');
      m.addControl(new maplibre.AttributionControl({ customAttribution: ATTRIBUTION }), 'bottom-right');
      m.on('click', (e) => {
        const p = e.lngLat.wrap();
        if (clickRef.current) clickRef.current(p.lat, p.lng);
      });
      const styled = () => {
        if (!m.getSource('nm-places')) return;
        m.off('styledata', styled);
        setReady(true);
      };
      m.on('styledata', styled);
      map.current = m;
      el.current.mapInstance = m; // (for tests and debugging)
      // The panes around it resize (window, sidebar): keep the map filling the box.
      ro = new ResizeObserver(() => m.resize());
      ro.observe(el.current);
    }).catch(() => {
      if (!gone && el.current) el.current.classList.add('is-unavailable');
    });
    return () => {
      gone = true;
      if (ro) ro.disconnect();
      labels.current.forEach((mk) => mk.remove());
      pins.current.forEach((mk) => mk.remove());
      labels.current.clear();
      pins.current.clear();
      if (map.current) map.current.remove();
      map.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Places: circles in the map (cheap to redraw, so the radius slider moves it smoothly), names as labels.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const shown = places.filter(placed);
    m.getSource('nm-places').setData({ type: 'FeatureCollection', features: shown.map((p) => circle(p.lat, p.lon, p.radiusM, { on: p.id === highlight })) });
    const keep = new Set();
    shown.forEach((p) => {
      keep.add(p.id);
      let mk = labels.current.get(p.id);
      if (!mk) {
        const div = document.createElement('div');
        div.className = 'map-place-label';
        mk = new ml.current.Marker({ element: div, anchor: 'top', offset: [0, 14] }).setLngLat([p.lon, p.lat]).addTo(m);
        labels.current.set(p.id, mk);
      }
      mk.setLngLat([p.lon, p.lat]);
      const div = mk.getElement();
      if (div.textContent !== p.name) div.textContent = p.name;
      div.classList.toggle('is-on', p.id === highlight);
    });
    labels.current.forEach((mk, id) => {
      if (!keep.has(id)) {
        mk.remove();
        labels.current.delete(id);
      }
    });
  }, [ready, places, highlight]);

  // People: a pin with their initial (ringed while live), a faint circle for a rough fix.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const shown = people.filter(placed);
    const acc = (p) => (p.members ? (p.members.length === 1 ? p.members[0].accuracyM : 0) : p.accuracyM);
    m.getSource('nm-acc').setData({ type: 'FeatureCollection', features: shown.filter((p) => acc(p) > 30).map((p) => circle(p.lat, p.lon, acc(p), {})) });
    const keep = new Set();
    shown.forEach((p) => {
      keep.add(p.id);
      let mk = pins.current.get(p.id);
      if (!mk) {
        const wrap = document.createElement('div');
        wrap.className = 'map-pin-wrap map-pin-zero';
        wrap.addEventListener('click', (e) => {
          e.stopPropagation(); // a pin, not the map underneath
          if (personRef.current) personRef.current(p.id);
        });
        mk = new ml.current.Marker({ element: wrap, anchor: 'center' }).setLngLat([p.lon, p.lat]).addTo(m);
        pins.current.set(p.id, mk);
      }
      mk.setLngLat([p.lon, p.lat]);
      drawPin(mk.getElement(), p);
    });
    pins.current.forEach((mk, id) => {
      if (!keep.has(id)) {
        mk.remove();
        pins.current.delete(id);
      }
    });
  }, [ready, people]);

  // Fit everything into view once, the first time there's anything to show (if it wasn't there at the start).
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || fitted.current) return;
    const view = firstView(ml.current, null, [...places.filter(placed), ...people.filter(placed)]);
    if (!view) return;
    fitted.current = true;
    if (view.bounds) m.fitBounds(view.bounds, { ...view.fitBoundsOptions, duration: 0 });
    else m.jumpTo(view);
  }, [ready, places, people]);

  const focusKey = focus && focus.key;
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !focus) return;
    if (focus.bounds) {
      fitted.current = true;
      m.fitBounds(boxOf(focus.bounds), { padding: 30, duration: focus.instant ? 0 : 700 });
      return;
    }
    if (!placed(focus)) return;
    fitted.current = true;
    const c = m.getCenter();
    if (Math.abs(c.lat - focus.lat) < 1e-7 && Math.abs(c.lng - focus.lon) < 1e-7) return; // already there
    if (focus.pan) m.panTo([focus.lon, focus.lat]); // following someone: keep the zoom they chose
    else m.flyTo({ center: [focus.lon, focus.lat], zoom: focus.zoom ? Z(focus.zoom) : Math.max(m.getZoom(), Z(15)), duration: 600 });
  }, [ready, focusKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return <div ref={el} className={'map-view ' + className} role="region" aria-label={label} />;
}
