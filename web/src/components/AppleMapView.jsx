// The map on Apple Maps (MapKit JS) - the same props and look as MapView's OpenFreeMap map:
// places as true-to-scale circles with their names, people as initial pins (ringed while live, a faint
// circle for a rough fix), the one being edited stronger, focus / first-time fit, clicks.
// MapKit JS comes from Apple's CDN the first time a map is shown; it asks the hub for its tokens
// (/api/map/token) and again as each runs out. If it can't load or Apple refuses the token,
// onUnavailable() switches this page to the OpenFreeMap map.

import { useEffect, useRef, useState } from 'react';
import { drawPin } from './family.js';
import { INK, mapkitToken, placed } from './mapProvider.js';

const CDN = 'https://cdn.apple-mapkit.com/mk/5.x.x/mapkit.core.js';
const READY = '__newmanMapKitReady';
const LOAD_TIMEOUT_MS = 20000;

let loading = null;
const refused = new Set(); // maps to switch over if Apple refuses the token
function loadMapKit() {
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('MapKit JS took too long')), LOAD_TIMEOUT_MS);
    window[READY] = () => {
      clearTimeout(timer);
      try {
        const mk = window.mapkit;
        mk.init({
          authorizationCallback: (done) => {
            mapkitToken().then(done, () => refused.forEach((f) => f()));
          },
        });
        mk.addEventListener('error', (e) => {
          if (e && /Unauthorized|Malformed/i.test(String(e.status))) refused.forEach((f) => f());
        });
        resolve(mk);
      } catch (e) {
        reject(e);
      }
    };
    const s = document.createElement('script');
    s.src = CDN;
    s.async = true;
    s.crossOrigin = 'anonymous';
    s.dataset.callback = READY;
    s.dataset.libraries = 'map,annotations,overlays';
    s.onerror = () => {
      clearTimeout(timer);
      reject(new Error("MapKit JS didn't load"));
    };
    document.head.appendChild(s);
  }).catch((e) => {
    loading = null;
    throw e;
  });
  return loading;
}
/** Start loading MapKit JS early (MapView's preloadMap). */
export function preloadMapKit() {
  loadMapKit().catch(() => {});
}

// Web-map zoom levels (256 px tiles: 15 = streets, 16 = a few houses) <-> the longitude span shown.
const spanFor = (zoom, widthPx) => (Math.max(widthPx, 1) * 360) / (256 * 2 ** zoom);
const zoomOf = (lonDelta, widthPx) => Math.log2((Math.max(widthPx, 1) * 360) / (256 * lonDelta));

function regionAt(mk, el, lat, lon, zoom) {
  const w = el.clientWidth || 600;
  const h = el.clientHeight || 400;
  const dLon = spanFor(zoom, w);
  const dLat = Math.min(170, (dLon * Math.cos((lat * Math.PI) / 180) * h) / w);
  return new mk.CoordinateRegion(new mk.Coordinate(lat, lon), new mk.CoordinateSpan(dLat, dLon));
}

// Where to look at first: the focus, or everything there is to show (with a margin, no closer than 16).
// A {s, w, n, e} box -> a region showing all of it (with a little margin).
function regionOfBox(mk, b) {
  return new mk.CoordinateRegion(new mk.Coordinate((b.s + b.n) / 2, (b.w + b.e) / 2),
    new mk.CoordinateSpan((b.n - b.s) * 1.08, (b.e - b.w) * 1.08));
}

function firstRegion(mk, el, focus, pts) {
  if (focus && focus.bounds) return regionOfBox(mk, focus.bounds);
  if (placed(focus)) return regionAt(mk, el, focus.lat, focus.lon, focus.zoom || 15);
  if (pts.length === 1) return regionAt(mk, el, pts[0].lat, pts[0].lon, 15);
  if (pts.length > 1) {
    const lats = pts.map((p) => p.lat);
    const lons = pts.map((p) => p.lon);
    const [s, n, w, e] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
    const tight = regionAt(mk, el, (s + n) / 2, (w + e) / 2, 16);
    const dLat = Math.max((n - s) * 1.5 || 0.004, tight.span.latitudeDelta);
    const dLon = Math.max((e - w) * 1.5 || 0.004, tight.span.longitudeDelta);
    return new mk.CoordinateRegion(new mk.Coordinate((s + n) / 2, (w + e) / 2), new mk.CoordinateSpan(dLat, dLon));
  }
  return null;
}

const style = (mk, stroke, width, fill, fillO) =>
  new mk.Style({ strokeColor: stroke, strokeOpacity: 1, lineWidth: width, fillColor: fill, fillOpacity: fillO });

// Annotation elements are 0 x 0 at the coordinate (MapKit anchors an annotation's bottom centre there);
// what shows is positioned around that point in CSS (.amap-*), like MapView's markers.
function zeroBox(cls) {
  const box = document.createElement('div');
  box.className = 'amap-anchor ' + cls;
  return box;
}

export default function AppleMapView({ places = [], people = [], highlight, focus, onMapClick, onPersonClick, className = '', label = 'Map', onUnavailable }) {
  const el = useRef(null);
  const map = useRef(null);
  const mkRef = useRef(null);
  const [ready, setReady] = useState(false);
  const fitted = useRef(false);
  const circles = useRef(new Map()); // place id -> {overlay, label annotation, text element}
  const pins = useRef(new Map()); // person id -> {annotation, pin element, accuracy overlay}
  const latest = useRef({});
  latest.current = { places, people, focus };
  const clickRef = useRef(onMapClick);
  clickRef.current = onMapClick;
  const personRef = useRef(onPersonClick);
  personRef.current = onPersonClick;
  const failRef = useRef(onUnavailable);
  failRef.current = onUnavailable;
  const dark = className.split(/\s+/).includes('map-view--dark');
  const ink = dark ? INK.dark : INK.light;

  useEffect(() => {
    let gone = false;
    const fail = () => {
      if (!gone && failRef.current) failRef.current();
    };
    refused.add(fail);
    loadMapKit().then((mk) => {
      if (gone || !el.current) return;
      mkRef.current = mk;
      const hidden = mk.FeatureVisibility.Hidden;
      const m = new mk.Map(el.current, {
        colorScheme: dark ? mk.Map.ColorSchemes.Dark : mk.Map.ColorSchemes.Light,
        showsCompass: hidden,
        showsScale: hidden,
        showsMapTypeControl: false,
        showsUserLocationControl: false,
        showsZoomControl: true,
        isRotationEnabled: false,
      });
      const { places: pl, people: pe, focus: fo } = latest.current;
      const region = firstRegion(mk, el.current, fo, [...pl.filter(placed), ...pe.filter(placed)]);
      if (region) {
        fitted.current = true; // starting where the content is: no world map first
        m.region = region;
      }
      // A tap on the map (not on a pin) -> where that is. (MapKit's own tap: drags and pinches aren't taps.)
      m.addEventListener('single-tap', (e) => {
        if (!clickRef.current || !e || !e.pointOnPage) return;
        const p = e.pointOnPage;
        const hit = document.elementFromPoint(p.x - window.scrollX, p.y - window.scrollY);
        if (hit && hit.closest && hit.closest('.map-pin-wrap')) return;
        const c = m.convertPointOnPageToCoordinate(new DOMPoint(p.x, p.y));
        if (c) clickRef.current(c.latitude, c.longitude);
      });
      // A pin tapped: MapKit selects its annotation - tell the page, and let it go again.
      m.addEventListener('select', (e) => {
        const a = e && e.annotation;
        if (!a || !a.data || !a.data.person) return;
        if (personRef.current) personRef.current(a.data.person);
        setTimeout(() => {
          if (m.selectedAnnotation === a) m.selectedAnnotation = null;
        }, 0);
      });
      map.current = m;
      el.current.mapInstance = m; // (for tests and debugging)
      setReady(true);
      // (MapKit follows its element's size itself: the panes around it can resize freely.)
    }).catch(fail);
    return () => {
      gone = true;
      refused.delete(fail);
      if (map.current) map.current.destroy();
      map.current = null;
      circles.current.clear();
      pins.current.clear();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Places: a circle of the right size in metres (updated in place, so the radius slider moves it
  // smoothly) and the name just under its centre.
  useEffect(() => {
    const m = map.current;
    const mk = mkRef.current;
    if (!ready || !m) return;
    const keep = new Set();
    places.filter(placed).forEach((p) => {
      keep.add(p.id);
      const on = p.id === highlight;
      const at = new mk.Coordinate(p.lat, p.lon);
      const look = on ? style(mk, ink.onLine, 2.5, ink.onFill, ink.onFillO) : style(mk, ink.line, 1.5, ink.fill, ink.fillO);
      let c = circles.current.get(p.id);
      if (!c) {
        const text = document.createElement('div');
        text.className = 'map-place-label';
        const box = zeroBox('amap-label');
        box.appendChild(text);
        const overlay = new mk.CircleOverlay(at, p.radiusM, { style: look, enabled: false });
        const tag = new mk.Annotation(at, () => box, {
          enabled: false, calloutEnabled: false, animates: false,
          displayPriority: mk.Annotation.DisplayPriority.Required, data: { place: p.id },
        });
        m.addOverlay(overlay);
        m.addAnnotation(tag);
        c = { overlay, tag, text };
        circles.current.set(p.id, c);
      } else {
        const o = c.overlay;
        if (o.coordinate.latitude !== p.lat || o.coordinate.longitude !== p.lon) {
          o.coordinate = at;
          c.tag.coordinate = at;
        }
        if (o.radius !== p.radiusM) o.radius = p.radiusM;
        if (c.on !== on) o.style = look;
      }
      c.on = on;
      if (c.text.textContent !== p.name) c.text.textContent = p.name;
      c.text.classList.toggle('is-on', on);
    });
    circles.current.forEach((c, id) => {
      if (!keep.has(id)) {
        m.removeOverlay(c.overlay);
        m.removeAnnotation(c.tag);
        circles.current.delete(id);
      }
    });
  }, [ready, places, highlight]); // eslint-disable-line react-hooks/exhaustive-deps

  // People: a pin with their initial (ringed while live), a faint circle for a rough fix (over 30 m).
  useEffect(() => {
    const m = map.current;
    const mk = mkRef.current;
    if (!ready || !m) return;
    const keep = new Set();
    people.filter(placed).forEach((p) => {
      keep.add(p.id);
      const at = new mk.Coordinate(p.lat, p.lon);
      let x = pins.current.get(p.id);
      if (!x) {
        const box = zeroBox('amap-pin map-pin-wrap');
        const ann = new mk.Annotation(at, () => box, {
          calloutEnabled: false, animates: false,
          displayPriority: mk.Annotation.DisplayPriority.Required, data: { person: p.id },
        });
        m.addAnnotation(ann);
        x = { ann, box, acc: null };
        pins.current.set(p.id, x);
      } else if (x.ann.coordinate.latitude !== p.lat || x.ann.coordinate.longitude !== p.lon) {
        x.ann.coordinate = at;
      }
      drawPin(x.box, p);
      const accuracyM = p.members ? (p.members.length === 1 ? p.members[0].accuracyM : 0) : p.accuracyM;
      const rough = accuracyM > 30;
      if (rough && !x.acc) {
        x.acc = new mk.CircleOverlay(at, accuracyM, { style: style(mk, ink.acc, 0, ink.acc, ink.accO), enabled: false });
        m.addOverlay(x.acc);
      } else if (rough) {
        x.acc.coordinate = at;
        x.acc.radius = accuracyM;
      } else if (x.acc) {
        m.removeOverlay(x.acc);
        x.acc = null;
      }
    });
    pins.current.forEach((x, id) => {
      if (!keep.has(id)) {
        m.removeAnnotation(x.ann);
        if (x.acc) m.removeOverlay(x.acc);
        pins.current.delete(id);
      }
    });
  }, [ready, people]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fit everything into view once, the first time there's anything to show (if it wasn't there at the start).
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || fitted.current) return;
    const region = firstRegion(mkRef.current, el.current, null, [...places.filter(placed), ...people.filter(placed)]);
    if (!region) return;
    fitted.current = true;
    m.region = region;
  }, [ready, places, people]);

  const focusKey = focus && focus.key;
  useEffect(() => {
    const m = map.current;
    const mk = mkRef.current;
    if (!ready || !m || !focus) return;
    if (focus.bounds) {
      fitted.current = true;
      if (focus.instant) m.region = regionOfBox(mk, focus.bounds);
      else m.setRegionAnimated(regionOfBox(mk, focus.bounds), true);
      return;
    }
    if (!placed(focus)) return;
    fitted.current = true;
    const c = m.center;
    if (c && Math.abs(c.latitude - focus.lat) < 1e-7 && Math.abs(c.longitude - focus.lon) < 1e-7) return; // already there
    if (focus.pan) {
      m.setCenterAnimated(new mk.Coordinate(focus.lat, focus.lon), true); // following someone: keep their zoom
      return;
    }
    const w = el.current.clientWidth || 600;
    const now = m.region && m.region.span ? zoomOf(m.region.span.longitudeDelta, w) : 0;
    m.setRegionAnimated(regionAt(mk, el.current, focus.lat, focus.lon, focus.zoom || Math.max(now, 15)), true);
  }, [ready, focusKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return <div ref={el} className={'map-view map-view--apple ' + className} role="region" aria-label={label} />;
}
