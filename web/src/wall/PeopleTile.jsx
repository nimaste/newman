// The Family tile (tile type "people"): a row of name chips - photo or coloured initial, first name, where
// they are and how long ago - over a map of the home area. People at the same place share one bubble
// (tap it: who's there, since when, a refresh button each). Anyone farther than ~50 km from Home isn't
// squeezed onto the map: their chip says how far and points their way, and a label sits at the map's
// edge; "Everyone" fits all of them. Tapping a chip asks the hub for that person's fresh location (one
// push to their phone, at most every 2 minutes) and flies the map to them; "Back to home view" (or a
// minute untouched) returns. What the tablet may see is the hub's call (the tablet account's "Can see
// where people are" and each person's sharing): anyone it can't see reads "Not sharing".
// config.people picks who's shown (empty = everyone); config.mapLook "light" (default) or "dark".
// PersonMap (one person, full screen) is what "show Dante on the map" opens.

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useApi } from '../hooks.js';
import { useLive } from '../live.js';
import { Icon } from '../icons.jsx';
import MapView, { preloadMap } from '../components/MapView.jsx';
import { Avatar, useRefresher } from '../components/FamilyBits.jsx';
import {
  FLY_SPAN_M, arrowStyle, boundsAround, boundsOf, firstName, groupPeople, homeArea, located, spanOf, whereShort,
} from '../components/family.js';
import { fmtAgo, fmtClock, fmtDistance } from '../format.js';

const BACK_HOME_MS = 60000; // flown away / everyone: back to the home view after this long untouched

/** "4 min", "just now", "2 h" - how old their location is. */
const ago = (p) => (p && p.at ? fmtAgo(p.at).replace(/ ago$/, '') : '');

/** "Home", "School", "3.2 km away" - or "Not sharing" / "No location yet" (the full-screen map's bar). */
function whereWall(p) {
  if (!p.sharing) return 'Not sharing';
  if (typeof p.lat !== 'number') return 'No location yet';
  if (p.home) return 'Home';
  if (p.place) return p.place;
  return typeof p.distanceM === 'number' ? `${fmtDistance(p.distanceM)} away` : 'Away';
}

/** The household from the hub, kept current by the live socket's person messages. */
export function usePeople() {
  const people = useApi('api/people', { interval: 300000 });
  useLive((m) => {
    if (m.type === 'reconnected') people.reload();
    if (m.type !== 'person' || !m.person) return;
    people.setData((list) => (Array.isArray(list) ? list.map((p) => (p.id === m.person.id ? { ...p, ...m.person } : p)) : list));
  });
  // "4 min ago" stays true.
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, []);
  return people;
}

/** Full screen: one person on the map (the tile's "Map look"); opening it asks for their fresh location once. */
export function PersonMap({ person, dark, onClose }) {
  const places = useApi('api/places');
  const people = useMemo(() => [person], [person]);
  const { refresh, busy } = useRefresher(people);
  const on = located(person);

  useEffect(() => {
    refresh(person);
  }, [person.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const key = (ev) => ev.key === 'Escape' && onClose();
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [onClose]);

  const focus = on ? { bounds: boundsAround(person.lat, person.lon, FLY_SPAN_M), key: person.id + ':' + person.at } : null;
  const sheet = (
    <div className="wt-pmap" role="dialog" aria-modal="true" aria-label={`Where ${person.name} is`}>
      <div className="wt-pmap-bar">
        <Avatar person={person} size={44} />
        <span className="wt-pmap-text">
          <span className="wt-pmap-name">{person.name}</span>
          <span className="wt-pmap-where">
            {person.sharing ? whereWall(person) : 'Not sharing any more'}
            {busy(person) ? ' · Updating…' : on && person.at ? ` · updated ${fmtAgo(person.at)}` : ''}
          </span>
        </span>
        <button type="button" className="wt-pmap-close" onClick={onClose}>
          <Icon name="x" size={22} strokeWidth={2.4} /> Close
        </button>
      </div>
      <div className="wt-pmap-body">
        <MapView className={(dark ? 'map-view--dark ' : '') + 'wt-pmap-map'} places={places.data || []} people={on ? [{ ...person, tag: firstName(person) }] : []} focus={focus} label={`Map of where ${person.name} is`} />
      </div>
    </div>
  );
  // Inside the wall (so its alerts still come up on top), outside the tile (which clips).
  return createPortal(sheet, document.querySelector('.wall') || document.body);
}

/** Where an edge label for someone off the map sits: on the map's border, in their direction from home. */
function edgeSpot(deg) {
  const dx = Math.sin((deg * Math.PI) / 180);
  const dy = -Math.cos((deg * Math.PI) / 180);
  const k = 0.5 / Math.max(Math.abs(dx), Math.abs(dy));
  const x = Math.round((0.5 + dx * k) * 100) / 100;
  const y = Math.round((0.5 + dy * k) * 100) / 100;
  // Inside the map's edge (12 px in), pulled in by its own size so it never hangs off.
  return { left: `calc(12px + ${x} * (100% - 24px))`, top: `calc(12px + ${y} * (100% - 24px))`, transform: `translate(${-x * 100}%, ${-y * 100}%)` };
}

function Chip({ p, far, busy, selected, onTap }) {
  const on = located(p);
  let where;
  if (busy) where = 'Updating…';
  else if (!p.sharing) where = 'Not sharing';
  else if (!on) where = 'No location yet';
  else if (far) where = null;
  else where = `${whereShort(p)}${ago(p) ? ' · ' + ago(p) : ''}`;
  return (
    <button
      type="button"
      className={'fam-chip' + (p.sharing ? '' : ' is-off') + (busy ? ' is-busy' : '') + (selected ? ' is-on' : '')}
      disabled={!p.sharing}
      onClick={() => onTap(p)}
      aria-label={`${p.name}: ${far ? fmtDistance(far.distanceM) + ' away' : where}. Refresh and show on the map`}
    >
      <Avatar person={p} size={34} />
      <span className="fam-chip-text">
        <b>{firstName(p)}</b>
        <em>
          {far && !busy ? (
            <>
              {p.place ? p.place + ' · ' : ''}
              {fmtDistance(far.distanceM)} <span style={arrowStyle(far.bearing)} aria-hidden="true">↑</span>
            </>
          ) : (
            where
          )}
        </em>
      </span>
      {busy ? <span className="fam-spin" aria-hidden="true" /> : null}
    </button>
  );
}

/** The card for a bubble: who's there, since when, a refresh button each. */
function GroupCard({ group, busy, onRefresh, onPick, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const away = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    document.addEventListener('pointerdown', away, true);
    return () => document.removeEventListener('pointerdown', away, true);
  }, [onClose]);
  return (
    <div className="fam-card" ref={ref} role="group" aria-label={group.place ? `At ${group.place}` : 'Together'}>
      <div className="fam-card-head">{group.place === 'Home' ? 'At home' : group.place ? `At ${group.place}` : 'Together'}</div>
      {group.members.map((p) => (
        <div key={p.id} className="fam-card-row">
          <button type="button" className="fam-card-who" onClick={() => onPick(p)}>
            <Avatar person={p} size={34} />
            <b>{firstName(p)}</b>
            <em>{busy(p) ? 'Updating…' : p.at ? `since ${fmtClock(p.at)}` : ''}</em>
          </button>
          <button type="button" className={'fam-card-ref' + (busy(p) ? ' is-busy' : '')} onClick={() => onRefresh(p)} aria-label={`Refresh where ${firstName(p)} is`}>
            <Icon name="refresh" size={18} strokeWidth={2.4} />
          </button>
        </div>
      ))}
    </div>
  );
}

export default function PeopleTile({ tile }) {
  const people = usePeople();
  const places = useApi('api/places', { interval: 600000 });
  const pick = Array.isArray(tile.config.people) ? tile.config.people : [];
  const all = Array.isArray(people.data) ? people.data : [];
  const list = pick.length ? all.filter((p) => pick.includes(p.id)) : all;
  const dark = tile.config.mapLook === 'dark';
  const home = Array.isArray(places.data) ? places.data.find((x) => x.id === 'home') : null;
  const { refresh, busy } = useRefresher(list);
  const [view, setView] = useState({ mode: 'home', n: 0 }); // home | all | person (id)
  const [card, setCard] = useState(null); // a bubble's id
  const touched = useRef(Date.now());
  const rootRef = useRef(null);

  const area = useMemo(() => homeArea(list, home), [list, home]);
  const farOf = (p) => area.far.find((f) => f.person.id === p.id);
  const locatedN = list.filter(located).length;
  const shown = view.mode === 'home' ? area.near : list.filter(located);
  const target = view.mode === 'person' ? list.find((p) => p.id === view.id) : null;

  // The view: the home area / everyone / one person, re-fitted when what it covers changes.
  let focus = null;
  if (view.mode === 'person' && located(target)) {
    focus = { bounds: boundsAround(target.lat, target.lon, FLY_SPAN_M), key: `p:${target.id}:${target.lat},${target.lon}:${view.n}` };
  } else if (view.mode === 'all' && locatedN) {
    const b = boundsOf(list.filter(located));
    focus = { bounds: b, key: 'all:' + JSON.stringify(b) + view.n };
  } else if (area.bounds) {
    const b = area.bounds;
    focus = { bounds: b, key: 'home:' + [b.s, b.w, b.n, b.e].map((x) => x.toFixed(3)).join(',') + ':' + view.n };
  }
  // Bubbles: people at one place or close together; zoomed far out (everyone), "close" grows with the map.
  const groups = groupPeople(shown, locatedN, view.mode === 'all' && focus ? Math.max(150, spanOf(focus.bounds) * 0.04) : undefined);
  // A place with a bubble on it: the bubble says its name (no second label under it).
  const mapPlaces = (places.data || []).filter((pl) => !groups.some((g) => g.place && g.place === pl.name));

  // Away from the home view: back after a minute untouched (and the wall counts it as in use meanwhile).
  const away = view.mode !== 'home';
  useEffect(() => {
    if (!away) return undefined;
    const t = setInterval(() => {
      if (Date.now() - touched.current > BACK_HOME_MS) setView((v) => ({ mode: 'home', n: v.n + 1 }));
    }, 2000);
    return () => clearInterval(t);
  }, [away]);

  // Someone can be shown: get the map ready in the background.
  useEffect(() => {
    if (!locatedN) return undefined;
    const t = setTimeout(() => preloadMap(dark), 1500);
    return () => clearTimeout(t);
  }, [locatedN > 0, dark]); // eslint-disable-line react-hooks/exhaustive-deps

  const goTo = (p) => {
    touched.current = Date.now();
    setCard(null);
    refresh(p);
    if (located(p)) setView((v) => ({ mode: 'person', id: p.id, n: v.n + 1 }));
  };
  const onPin = (id) => {
    touched.current = Date.now();
    const g = groups.find((x) => x.id === id);
    if (!g) return;
    if (g.members.length > 1) setCard(g.id);
    else goTo(g.members[0]);
  };
  const openCard = groups.find((g) => g.id === card);

  return (
    <div
      className="wt-list fam"
      ref={rootRef}
      data-wall-in-use={away ? '' : undefined}
      onPointerDown={() => {
        touched.current = Date.now();
      }}
    >
      <div className="fam-chips" role="list" aria-label="Family">
        {people.data === undefined ? <div className="wt-muted">{people.error ? "Couldn't load the household." : 'Loading…'}</div> : null}
        {people.data !== undefined && !list.length ? <div className="wt-muted">Nobody to show.</div> : null}
        {list.map((p) => (
          <Chip key={p.id} p={p} far={farOf(p)} busy={busy(p)} selected={view.mode === 'person' && view.id === p.id} onTap={goTo} />
        ))}
      </div>
      <div className="fam-map">
        <MapView
          className={(dark ? 'map-view--dark ' : '') + 'fam-map-view'}
          places={mapPlaces}
          people={groups}
          focus={focus}
          onPersonClick={onPin}
          label="Map of where the family is"
        />
        {view.mode === 'home'
          ? area.far.map((f) => (
              <button key={f.person.id} type="button" className="fam-edge" style={edgeSpot(f.bearing)} onClick={() => goTo(f.person)}>
                {firstName(f.person)} · {f.person.place ? f.person.place + ' · ' : ''}
                {fmtDistance(f.distanceM)} <span style={arrowStyle(f.bearing)} aria-hidden="true">↑</span>
              </button>
            ))
          : null}
        {view.mode === 'home' && area.far.length ? (
          <button type="button" className="fam-mapbtn" onClick={() => setView((v) => ({ mode: 'all', n: v.n + 1 }))}>
            <Icon name="people" size={18} strokeWidth={2.4} /> Everyone
          </button>
        ) : null}
        {away ? (
          <button type="button" className="fam-mapbtn" onClick={() => setView((v) => ({ mode: 'home', n: v.n + 1 }))}>
            <Icon name="home" size={18} strokeWidth={2.4} /> Back to home view
          </button>
        ) : null}
        {openCard ? <GroupCard group={openCard} busy={busy} onRefresh={(p) => refresh(p)} onPick={goTo} onClose={() => setCard(null)} /> : null}
      </div>
    </div>
  );
}
